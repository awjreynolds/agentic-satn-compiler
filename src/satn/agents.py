"""Typed, provider-neutral and bounded agent compilation gate."""

from __future__ import annotations

import hashlib
import json
import signal
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock, current_thread, main_thread
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from satn.models import (
    AgentConfig,
    AgentDecisionAction,
    AgentDecisionChoice,
    AgentDecisionLedger,
    AgentDecisionRequest,
    AgentDecisionResponse,
    AgentRecord,
    AgentReviewDecision,
    AgentRuntimeDecisionResponse,
    DivergenceRecord,
    TrafficLight,
)
from satn.models import (
    AgentFinding as ChallengeFinding,
)


class AgentRole(StrEnum):
    DECISION = "decision"


@dataclass
class RuntimeReply:
    output: BaseModel
    tokens: int = 0


@dataclass(frozen=True)
class ResolvedAgentDecision:
    response: AgentDecisionResponse
    choice: AgentDecisionChoice
    responder_mode: Literal["caller", "direct-runtime"]
    runtime: str
    model: str
    usage: dict[str, int]


class _RuntimeDeadlineExceeded(BaseException):
    pass


class AgentRuntime(ABC):
    """Provider-neutral adapter interface. Implementations never mutate compiled state."""

    name = "runtime"
    model = "unknown"

    @abstractmethod
    def run(
        self,
        role: AgentRole,
        payload: BaseModel,
        output_type: type[BaseModel],
    ) -> RuntimeReply:
        """Return a response validated against ``output_type``."""


class AgentRuntimeProvider:
    """Materialise one shared Agent Runtime only when a selected status needs it."""

    def __init__(self, factory: Callable[[], AgentRuntime]):
        self._factory = factory
        self._runtime: AgentRuntime | None = None
        self._lock = Lock()

    def get(self) -> AgentRuntime:
        if self._runtime is None:
            with self._lock:
                if self._runtime is None:
                    self._runtime = self._factory()
        return self._runtime


AgentRuntimeSource = AgentRuntime | AgentRuntimeProvider | None


def materialize_agent_runtime(source: AgentRuntimeSource) -> AgentRuntime:
    if isinstance(source, AgentRuntime):
        return source
    if isinstance(source, AgentRuntimeProvider):
        return source.get()
    raise RuntimeError("agent review is required, but no runtime exists")


class FakeAgentRuntime(AgentRuntime):
    """Deterministic adapter with optional scripted responses for contract tests."""

    name = "fake"
    model = "deterministic-choices-v1"

    def __init__(
        self,
        scripts: dict[AgentRole, list[object]] | None = None,
        *,
        tokens_per_reply: int = 1,
    ):
        self.scripts = defaultdict(list, scripts or {})
        self.tokens_per_reply = tokens_per_reply

    def run(
        self,
        role: AgentRole,
        payload: BaseModel,
        output_type: type[BaseModel],
    ) -> RuntimeReply:
        if self.scripts[role]:
            scripted = self.scripts[role].pop(0)
            if isinstance(scripted, Exception):
                raise scripted
            if isinstance(scripted, dict) and scripted.get("request_id") == "$request":
                request = AgentDecisionRequest.model_validate(payload)
                scripted = {**scripted, "request_id": request.request_id}
            return RuntimeReply(
                output_type.model_validate(scripted),
                tokens=self.tokens_per_reply,
            )
        output = self._default(role, payload)
        return RuntimeReply(
            output_type.model_validate(output),
            tokens=self.tokens_per_reply,
        )

    def _default(self, role: AgentRole, payload: BaseModel) -> dict[str, object]:
        if role != AgentRole.DECISION:
            raise ValueError(f"unsupported bounded runtime role: {role}")
        request = AgentDecisionRequest.model_validate(payload)
        selected = next(
            choice for choice in request.choices if choice.choice_id != "terminate"
        )
        return {"request_id": request.request_id, "choice_id": selected.choice_id}


class PydanticAIRuntime(AgentRuntime):
    """Pydantic AI adapter configured solely by model name and environment credentials."""

    name = "pydantic-ai"

    def __init__(self, config: AgentConfig):
        if not config.model:
            raise ValueError("a non-fake agent provider requires agent.model")
        self.model = config.model
        self.max_tokens = config.max_tokens

    def run(
        self,
        role: AgentRole,
        payload: BaseModel,
        output_type: type[BaseModel],
    ) -> RuntimeReply:
        from pydantic_ai import Agent
        from pydantic_ai.usage import UsageLimits

        agent = Agent(
            self.model,
            output_type=output_type,
            instructions=_ROLE_INSTRUCTIONS[role],
            model_settings={"max_tokens": self.max_tokens},
            retries=0,
        )
        result = agent.run_sync(
            payload.model_dump_json(),
            usage_limits=UsageLimits(total_tokens_limit=self.max_tokens, request_limit=1),
        )
        usage = result.usage()
        return RuntimeReply(result.output, tokens=int(usage.total_tokens or 0))


@dataclass
class GateOutcome:
    record: AgentRecord
    selected_role: str | None


class AgentDecisionRequired(Exception):
    """End this invocation with one compiler-authored bounded decision menu."""

    def __init__(
        self,
        request: AgentDecisionRequest,
        resolver: AgentDecisionResolver | None = None,
    ):
        super().__init__(request.request_id)
        self.request = request
        self.applied_records = list(resolver.applied_records) if resolver else []
        self.applied_divergence_records = (
            list(resolver.applied_divergence_records) if resolver else []
        )
        self.validation = resolver.validation if resolver else None


class AgentCompilationTerminated(Exception):
    """End this invocation after accepting the reserved terminate choice."""

    def __init__(self, resolver: AgentDecisionResolver):
        super().__init__("agent decision terminated compilation")
        self.applied_records = list(resolver.applied_records)
        self.applied_divergence_records = list(resolver.applied_divergence_records)


class AgentDecisionResolver:
    """Validate data-only ledger responses against freshly regenerated requests."""

    def __init__(
        self,
        ledger: AgentDecisionLedger | None,
        governed_input_fingerprint: str,
    ):
        self.ledger = ledger or AgentDecisionLedger()
        self.governed_input_fingerprint = governed_input_fingerprint
        self.applied_records: list[AgentRecord] = []
        self.applied_divergence_records: list[DivergenceRecord] = []
        self.accepted_responses: list[AgentDecisionResponse] = []
        self.validation: str | None = None

    @property
    def consumed_request_ids(self) -> set[str]:
        return {response.request_id for response in self.accepted_responses}

    def request_context_fingerprint(self) -> str:
        payload = {
            "governed_input_fingerprint": self.governed_input_fingerprint,
            "accepted_responses": [
                response.model_dump(mode="json") for response in self.accepted_responses
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def offered_choice(
        self,
        request: AgentDecisionRequest,
    ) -> tuple[AgentDecisionResponse, AgentDecisionChoice]:
        resolved = self.ledger_choice(request)
        if resolved is None:
            self.validation = "response-required"
            raise AgentDecisionRequired(request, self)
        return resolved

    def ledger_choice(
        self,
        request: AgentDecisionRequest,
    ) -> tuple[AgentDecisionResponse, AgentDecisionChoice] | None:
        response = next(
            (
                candidate
                for candidate in self.ledger.responses
                if candidate.request_id == request.request_id
            ),
            None,
        )
        if response is None:
            unconsumed = {
                candidate.request_id for candidate in self.ledger.responses
            } - self.consumed_request_ids
            if unconsumed:
                self.validation = "unknown-request"
                raise AgentDecisionRequired(request, self)
            return None
        return response, self.validate_response(request, response)

    def validate_response(
        self,
        request: AgentDecisionRequest,
        response: AgentDecisionResponse,
    ) -> AgentDecisionChoice:
        if response.request_id != request.request_id:
            self.validation = "response-for-another-request"
            raise AgentDecisionRequired(request, self)
        if response.dependency_fingerprint != request.dependency_fingerprint:
            self.validation = "stale-fingerprint"
            raise AgentDecisionRequired(request, self)
        choice = next(
            (choice for choice in request.choices if choice.choice_id == response.choice_id),
            None,
        )
        if choice is None:
            self.validation = "unknown-choice"
            raise AgentDecisionRequired(request, self)
        return choice

    def accept(
        self,
        response: AgentDecisionResponse,
        record: AgentRecord | DivergenceRecord,
    ) -> None:
        self.validation = "accepted"
        self.accepted_responses.append(response)
        if isinstance(record, AgentRecord):
            self.applied_records.append(record)
        else:
            self.applied_divergence_records.append(record)


def resolve_agent_decision(
    request: AgentDecisionRequest,
    resolver: AgentDecisionResolver,
    runtime_source: AgentRuntimeSource,
    config: AgentConfig,
) -> ResolvedAgentDecision:
    """Resolve one menu through a caller ledger or one deadline-bound runtime call."""
    ledger_choice = resolver.ledger_choice(request)
    if ledger_choice is not None:
        response, choice = ledger_choice
        return ResolvedAgentDecision(
            response=response,
            choice=choice,
            responder_mode="caller",
            runtime="caller",
            model="decision-ledger",
            usage={"requests": 0, "tokens": 0},
        )
    if runtime_source is None or config.response_mode != "direct-runtime":
        resolver.validation = "response-required"
        raise AgentDecisionRequired(request, resolver)
    try:
        runtime = materialize_agent_runtime(runtime_source)
        reply = _run_with_deadline(
            lambda: runtime.run(
                AgentRole.DECISION,
                request,
                AgentRuntimeDecisionResponse,
            ),
            config.deadline_seconds,
        )
        direct_response = AgentRuntimeDecisionResponse.model_validate(reply.output)
        if isinstance(reply.tokens, bool) or not isinstance(reply.tokens, int):
            raise TypeError("runtime token usage must be an integer")
    except _RuntimeDeadlineExceeded:
        resolver.validation = "runtime-timeout"
        raise AgentDecisionRequired(request, resolver) from None
    except (AttributeError, TypeError, ValueError, ValidationError):
        resolver.validation = "runtime-schema-failure"
        raise AgentDecisionRequired(request, resolver) from None
    except Exception:
        resolver.validation = "runtime-unavailable"
        raise AgentDecisionRequired(request, resolver) from None
    if reply.tokens < 0 or reply.tokens > config.max_tokens:
        resolver.validation = "runtime-token-limit"
        raise AgentDecisionRequired(request, resolver)
    if direct_response.request_id != request.request_id:
        resolver.validation = "response-for-another-request"
        raise AgentDecisionRequired(request, resolver)
    response = AgentDecisionResponse(
        request_id=direct_response.request_id,
        dependency_fingerprint=request.dependency_fingerprint,
        choice_id=direct_response.choice_id,
    )
    choice = resolver.validate_response(request, response)
    return ResolvedAgentDecision(
        response=response,
        choice=choice,
        responder_mode="direct-runtime",
        runtime=runtime.name,
        model=runtime.model,
        usage={"requests": 1, "tokens": reply.tokens},
    )


def _run_with_deadline(call: Callable[[], RuntimeReply], seconds: float) -> RuntimeReply:
    """Interrupt a synchronous runtime call in-process without leaving an orphan worker."""
    if current_thread() is not main_thread() or not hasattr(signal, "setitimer"):
        raise _RuntimeDeadlineExceeded("hard deadlines require the compiler main thread")

    def expire(_signum: int, _frame: object) -> None:
        raise _RuntimeDeadlineExceeded("direct Agent Runtime deadline exceeded")

    started = time.monotonic()
    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, expire)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    if 0 < previous_timer[0] < seconds:
        signal.setitimer(signal.ITIMER_REAL, previous_timer[0])
    try:
        return call()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer != (0.0, 0.0):
            elapsed = time.monotonic() - started
            remaining = max(previous_timer[0] - elapsed, 1e-6)
            signal.setitimer(signal.ITIMER_REAL, remaining, previous_timer[1])


def build_agent_decision_request(
    *,
    compilation_scope: str,
    affected_identifiers: list[str],
    criterion: str,
    status: TrafficLight,
    evidence_references: list[str],
    findings: list[ChallengeFinding],
    choices: list[AgentDecisionChoice],
    review_policy: tuple[TrafficLight, ...],
    governed_input_fingerprint: str,
) -> AgentDecisionRequest:
    """Build a stable request only from governed dependencies and predefined actions."""
    contract = "agent-decision-menu/v1"
    affected = tuple(dict.fromkeys(value for value in affected_identifiers if value))
    evidence = tuple(sorted(set(value for value in evidence_references if value)))
    identity_payload = {
        "decision_contract": contract,
        "compilation_scope": compilation_scope,
        "affected_identifiers": affected,
        "criterion": criterion,
    }
    request_id = "agent-decision-" + hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True).encode()
    ).hexdigest()[:16]
    dependency_payload = {
        **identity_payload,
        "governed_input_fingerprint": governed_input_fingerprint,
        "status": status.value,
        "evidence_references": evidence,
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "choices": [choice.model_dump(mode="json") for choice in choices],
        "review_policy": [value.value for value in review_policy],
    }
    dependency_fingerprint = hashlib.sha256(
        json.dumps(dependency_payload, sort_keys=True).encode()
    ).hexdigest()
    return AgentDecisionRequest(
        request_id=request_id,
        dependency_fingerprint=dependency_fingerprint,
        compilation_scope=compilation_scope,
        affected_identifiers=affected,
        criterion=criterion,
        question=(
            f"Which predefined compiler action should be applied for the {criterion} criterion?"
        ),
        status=status,
        governed_evidence_references=evidence,
        deterministic_findings=tuple(findings),
        choices=tuple(choices),
    )


def termination_choice() -> AgentDecisionChoice:
    """Return the one reserved stop-and-preserve action shared by every menu."""
    return AgentDecisionChoice(
        choice_id="terminate",
        label="Terminate this compilation",
        compiler_action=AgentDecisionAction(kind="terminate"),
        expected_consequence=(
            "Stop this run, preserve the previous valid publication, and require a fresh "
            "compilation."
        ),
        mandatory_constraints=(
            "No partial result from this invocation may be published.",
            "A later attempt must start a fresh compilation.",
        ),
    )


class CompilationGate:
    def __init__(
        self,
        runtime: AgentRuntimeSource,
        config: AgentConfig,
        governed_input_fingerprint: str = "",
        decision_resolver: AgentDecisionResolver | None = None,
    ):
        self.runtime_source = runtime
        self.config = config
        fallback_fingerprint = governed_input_fingerprint or hashlib.sha256(
            config.model_dump_json().encode()
        ).hexdigest()
        self.decision_resolver = decision_resolver or AgentDecisionResolver(
            None,
            fallback_fingerprint,
        )

    def evaluate(
        self,
        connection_id: str,
        facts: dict[str, Any],
        initial_role: str | None,
        available_roles: list[str],
        *,
        governing_criterion: str,
        governing_status: TrafficLight,
        deterministic_decision: Literal["accept", "gap"] = "accept",
    ) -> GateOutcome:
        review = self.config.review_decision(governing_status)
        deterministic_outcome = deterministic_decision
        if not review.review_required:
            if governing_status == TrafficLight.RED:
                deterministic_outcome = "gap"
            reason = (
                "Deterministic Red prevents this connection entering the network; "
                "agent review was skipped by policy."
                if governing_status == TrafficLight.RED
                else f"Deterministic {governing_status.value.title()} decision applied; "
                "agent review was skipped by policy."
            )
            return GateOutcome(
                AgentRecord(
                    connection_id=connection_id,
                    governing_criterion=governing_criterion,
                    **review.model_dump(),
                    runtime="not-invoked",
                    model="not-invoked",
                    decision=deterministic_outcome,
                    selected_role=initial_role,
                    outcome_reason=reason,
                    usage={"requests": 0, "tokens": 0},
                    created_at=None,
                ),
                initial_role,
            )
        scope = {
            "direct": "spine-access",
            "cross-spine-connector": "branch-meeting",
            "gap": "network-gap",
            "school-access-gap": "urban-school-access-gap",
        }.get(initial_role or "", "network-decision")
        findings = _deterministic_findings(
            facts,
            initial_role,
            governing_criterion=governing_criterion,
            governing_status=governing_status,
        )
        request = build_agent_decision_request(
            compilation_scope=scope,
            affected_identifiers=[
                connection_id,
                str(facts.get("from_place") or ""),
                str(facts.get("to_place") or ""),
            ],
            criterion=governing_criterion,
            status=governing_status,
            evidence_references=[str(value) for value in facts.get("evidence_ids", ())],
            findings=findings,
            choices=_route_decision_choices(
                available_roles,
                include_candidate_controls=scope in {"spine-access", "branch-meeting"},
            ),
            review_policy=review.review_policy,
            governed_input_fingerprint=(
                self.decision_resolver.request_context_fingerprint()
            ),
        )
        resolved = resolve_agent_decision(
            request,
            self.decision_resolver,
            self.runtime_source,
            self.config,
        )
        action = resolved.choice.compiler_action
        selected_role = action.network_role
        if action.kind == "terminate":
            record = _choice_record(
                connection_id,
                governing_criterion,
                review,
                request,
                resolved,
                initial_role,
                "gap",
            )
            self.decision_resolver.accept(resolved.response, record)
            raise AgentCompilationTerminated(self.decision_resolver)
        if action.kind in {"reject-candidate", "retain-network-gap"}:
            decision = "reject" if action.kind == "reject-candidate" else "gap"
            record = _choice_record(
                connection_id,
                governing_criterion,
                review,
                request,
                resolved,
                initial_role,
                decision,
            )
            self.decision_resolver.accept(resolved.response, record)
            return GateOutcome(record, initial_role)
        if action.kind != "select-network-role" or selected_role not in available_roles:
            self.decision_resolver.validation = "invalid-action"
            raise AgentDecisionRequired(request, self.decision_resolver)
        selected_checks = facts.get("checks_by_role", {}).get(selected_role, {})
        if selected_role not in {"gap", "school-access-gap"} and any(
            status == "red" for status in selected_checks.values()
        ):
            self.decision_resolver.validation = "mandatory-red"
            raise AgentDecisionRequired(request, self.decision_resolver)
        decision = (
            "gap"
            if selected_role in {"gap", "school-access-gap"}
            else deterministic_decision
        )
        record = _choice_record(
            connection_id,
            governing_criterion,
            review,
            request,
            resolved,
            selected_role,
            decision,
        )
        self.decision_resolver.accept(resolved.response, record)
        return GateOutcome(record, selected_role)


def runtime_for(config: AgentConfig) -> AgentRuntime:
    if config.provider == "fake":
        return FakeAgentRuntime()
    return PydanticAIRuntime(config)


def _deterministic_findings(
    facts: dict[str, Any],
    role: str | None,
    *,
    governing_criterion: str | None = None,
    governing_status: TrafficLight | None = None,
) -> list[ChallengeFinding]:
    checks = facts.get("checks_by_role", {}).get(role, {}) if role else {}
    evidence_ids = [str(value) for value in facts.get("evidence_ids", ()) if value]
    findings = [
        ChallengeFinding(
            code=f"deterministic-{name}",
            severity="blocking" if status == "red" else "advisory",
            message=f"Deterministic criterion {name} is {status}.",
            evidence_ids=evidence_ids,
        )
        for name, status in checks.items()
        if status in {"red", "amber"}
    ]
    if governing_criterion is not None and governing_status is not None:
        governing_code = f"deterministic-{governing_criterion}"
        findings = [finding for finding in findings if finding.code != governing_code]
        findings.insert(
            0,
            ChallengeFinding(
                code=governing_code,
                severity=(
                    "blocking" if governing_status == TrafficLight.RED else "advisory"
                ),
                message=(
                    f"Deterministic criterion {governing_criterion} is "
                    f"{governing_status.value}."
                ),
                evidence_ids=evidence_ids,
            ),
        )
    return findings


def _route_decision_choices(
    available_roles: list[str],
    *,
    include_candidate_controls: bool = False,
) -> list[AgentDecisionChoice]:
    constraints = (
        "The choice must remain one of the compiler-authored actions in this request.",
        "The compiler will revalidate all deterministic invariants before changing state.",
        "A mandatory Red invariant cannot be waived by this choice.",
    )
    choices = [
        AgentDecisionChoice(
            choice_id=str(index),
            label=f"Select {role.replace('-', ' ')}",
            compiler_action=AgentDecisionAction(
                kind="select-network-role",
                network_role=role,
            ),
            expected_consequence=(
                "Retain the visible Network Gap and do not add an invalid connection."
                if role in {"gap", "school-access-gap"}
                else f"Validate and apply the {role} network role if its invariants pass."
            ),
            mandatory_constraints=constraints,
        )
        for index, role in enumerate(available_roles, start=1)
    ]
    if include_candidate_controls and len(available_roles) == 1 and available_roles[0] not in {
        "gap",
        "school-access-gap",
    }:
        choices.extend(
            [
                AgentDecisionChoice(
                    choice_id="2",
                    label="Reject this candidate",
                    compiler_action=AgentDecisionAction(kind="reject-candidate"),
                    expected_consequence=(
                        "Reject this candidate and evaluate the next compiler-ranked "
                        "alternative, if one exists."
                    ),
                    mandatory_constraints=constraints,
                ),
                AgentDecisionChoice(
                    choice_id="3",
                    label="Retain a visible Network Gap",
                    compiler_action=AgentDecisionAction(kind="retain-network-gap"),
                    expected_consequence=(
                        "Stop evaluating candidates for this obligation and retain a "
                        "visible Network Gap."
                    ),
                    mandatory_constraints=constraints,
                ),
            ]
        )
    choices.append(termination_choice())
    return choices


def _choice_record(
    connection_id: str,
    governing_criterion: str,
    review: AgentReviewDecision,
    request: AgentDecisionRequest,
    resolved: ResolvedAgentDecision,
    selected_role: str | None,
    decision: Literal["accept", "reject", "gap"],
) -> AgentRecord:
    choice = resolved.choice
    return AgentRecord(
        connection_id=connection_id,
        governing_criterion=governing_criterion,
        network_role=selected_role,
        **review.model_dump(),
        runtime=resolved.runtime,
        model=resolved.model,
        decision=decision,
        selected_role=selected_role,
        outcome_reason=(
            f"{resolved.responder_mode.title()} selected choice "
            f"{resolved.response.choice_id}: {choice.label}. "
            "The compiler validated and applied its predefined action."
        ),
        usage=resolved.usage,
        decision_request=request,
        selected_choice_id=resolved.response.choice_id,
        mapped_action=choice.compiler_action,
        responder_mode=resolved.responder_mode,
        choice_validation="accepted",
        affected_feature_identifiers=request.affected_identifiers,
        created_at=None,
    )


_ROLE_INSTRUCTIONS = {
    AgentRole.DECISION: (
        "Select exactly one offered choice from the fingerprinted request. Return only "
        "the request identifier and choice identifier; never invent or parameterise an action."
    )
}
