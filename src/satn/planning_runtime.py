"""Durable orchestration for the experimental planning pass.

The engine remains the authority for admitting and applying operations.  This
module only binds that engine to the local history store and to the typed
provider boundary.  A replay never creates a provider or reads a source.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from satn.models import AreaConfig, AreaDefinition
from satn.planning_engine import (
    apply_operation,
    build_planning_problem,
    expand_connection,
    initial_proposal,
    replay_expansion,
    semantic_fingerprint,
    validate_proposal,
)
from satn.planning_history import (
    Branch,
    HistoryMissingError,
    HistoryReplayError,
    HistoryStore,
)
from satn.planning_publication import PublicationValidationError, publish_planning_output
from satn.planning_routing import (
    CapabilityKind,
    CapabilityRequirements,
    DecisionTask,
    RoutingStatus,
    StaticCapabilityRouter,
)
from satn.typesafe_planning import ChoiceQuestion, TypeSafeClient

RunMode = Literal["deterministic", "live"]
DecisionClass = Literal["mechanical", "classifier", "agent"]
ProviderFunction = Callable[[Mapping[str, object], Mapping[str, object]], object]

_UNKNOWN = "__unknown__"
_EVIDENCE = "__needs_evidence__"
_NONE = "__none__"


def _copy_json(value: object) -> object:
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False))


def _wire_json(value: object) -> object:
    if hasattr(value, "as_payload"):
        return _wire_json(value.as_payload())  # type: ignore[union-attr]
    if isinstance(value, Mapping):
        return {str(key): _wire_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire_json(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _safe_json(value: object) -> object:
    try:
        return _copy_json(_wire_json(value))
    except (TypeError, ValueError):
        return {"type": type(value).__name__, "repr": repr(value)}


def _status(value: object) -> str:
    if value is None:
        return ""
    return str(value.value if hasattr(value, "value") else value)


def _decision_class(value: Mapping[str, object] | None) -> DecisionClass | None:
    """Return the runtime-assigned attribution for a dispatched result."""

    if value is None:
        return None
    decision_class = value.get("decision_class")
    if decision_class in {"mechanical", "classifier", "agent"}:
        return cast(DecisionClass, decision_class)
    return None


@dataclass(frozen=True, slots=True)
class PlanningRunResult:
    """Reviewable result returned by a run or explicit state advance."""

    status: str
    mode: str
    branch_id: str
    history_root: str
    history_event_id: str | None
    problem: dict[str, object]
    state: dict[str, object] | None
    output: dict[str, object]
    diagnostics: tuple[object, ...] = ()
    decision_trace: tuple[dict[str, object], ...] = ()
    termination_reason: str | None = None
    provider_result: dict[str, object] | None = None
    publication: dict[str, object] | None = None
    run_path: Path | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "mode": self.mode,
            "branch_id": self.branch_id,
            "history_root": self.history_root,
            "history_event_id": self.history_event_id,
            "problem": self.problem,
            "state": self.state,
            "output": self.output,
            "diagnostics": list(self.diagnostics),
            "decision_trace": [dict(item) for item in self.decision_trace],
            "termination_reason": self.termination_reason,
            "provider": self.provider_result.get("provider") if self.provider_result else None,
            "model": self.provider_result.get("model") if self.provider_result else None,
            "usage": self.provider_result.get("usage") if self.provider_result else None,
            "provider_status": self.provider_result.get("status") if self.provider_result else None,
            "publication": self.publication,
            "run_path": str(self.run_path) if self.run_path is not None else None,
        }


class PlanningRuntime:
    """Run planning against a content-addressed local history root."""

    def __init__(
        self,
        history_root: Path | str,
        *,
        provider: object | None = None,
        router: StaticCapabilityRouter | None = None,
        brief: Mapping[str, object] | None = None,
        policy: Mapping[str, object] | None = None,
        connection_options: Sequence[Mapping[str, object]] = (),
    ) -> None:
        self.history_root = Path(history_root)
        self.store = HistoryStore(self.history_root)
        self.provider = provider
        self.router = router
        self.brief = dict(brief or {})
        self.policy = dict(policy or {})
        self.connection_options = [dict(item) for item in connection_options]
        self.brief_ref: str | None = None
        self.policy_ref: str | None = None

    def run(
        self,
        config: AreaConfig | str | Path,
        *,
        output_root: Path | str,
        branch: str = "main",
        mode: RunMode = "deterministic",
        operations: Sequence[Mapping[str, object]] = (),
        requested_connections: Sequence[Mapping[str, object]] = (),
        connection_options: Sequence[Mapping[str, object]] = (),
    ) -> PlanningRunResult:
        """Admit an area, then execute explicit or typed decisions until stable."""

        if mode not in {"deterministic", "live"}:
            raise ValueError("mode must be deterministic or live")
        config = self._load_config(config)
        self.connection_options = [dict(item) for item in connection_options]
        problem, state, event_id, envelope_ref = self._start_or_resume(
            config, branch, mode, connection_options
        )
        explicit = [dict(item) for item in operations]
        explicit.extend(self._connection_operations(requested_connections))
        for template in explicit:
            operation = self._bind_operation(template, state)
            advanced = self._apply_and_record(
                branch,
                problem,
                state,
                operation,
                config=config,
                envelope_ref=envelope_ref,
                actor_kind="code",
                decision_class="mechanical",
                mode=mode,
            )
            event_id = advanced[0]
            if advanced[2] is not None:
                return self._result(
                    branch,
                    mode,
                    problem,
                    advanced[1],
                    event_id,
                    advanced[2],
                    output_root,
                    termination_reason="invalid-operation",
                )
            problem, state = advanced[3], advanced[1]

        termination_reason: str | None = None
        provider_result: dict[str, object] | None = None
        while True:
            output = validate_proposal(problem, state)
            if output.get("status") == "validated":
                break
            questions, choice_context = self._questions(problem, state)
            if not choice_context.get("dispatchable", True):
                request = self._request(problem, state, questions, mode, choice_context, branch)
                self._commit(
                    branch,
                    {
                        **self._event_context(problem, envelope_ref, state),
                        "event_kind": "request",
                        "actor_kind": "code",
                        "outcome": "unresolved",
                        "state_transition": False,
                        "input_state": state,
                        "request": request,
                    },
                )
                operation = self._bind_operation(self._coverage_operation(choice_context), state)
                old_fingerprint = semantic_fingerprint(state)
                advanced = self._apply_and_record(
                    branch,
                    problem,
                    state,
                    operation,
                    config=config,
                    envelope_ref=envelope_ref,
                    actor_kind="code",
                    decision_class="mechanical",
                    mode=mode,
                    request=request,
                    receipt={"status": "unresolved", "provider": "none"},
                )
                event_id = advanced[0]
                if advanced[2] is not None:
                    output = self._invalid_output(output, advanced[2])
                    return self._result(
                        branch,
                        mode,
                        problem,
                        state,
                        event_id,
                        output,
                        output_root,
                        termination_reason="invalid-operation",
                    )
                problem, state = advanced[3], advanced[1]
                if semantic_fingerprint(state) == old_fingerprint:
                    termination_reason = "semantic-no-progress"
                    break
                continue
            if mode == "deterministic":
                operation = self._bind_operation(
                    self._deterministic_operation(problem, state, choice_context), state
                )
                request = self._request(problem, state, questions, mode, choice_context, branch)
                event_id = self._commit(
                    branch,
                    {
                        **self._event_context(problem, envelope_ref, state),
                        "event_kind": "request",
                        "actor_kind": "code",
                        "outcome": "deterministic",
                        "state_transition": False,
                        "input_state": state,
                        "request": request,
                    },
                )
                provider_result = {
                    "status": "offline",
                    "provider": "none",
                    "model": "deterministic-code",
                    "decision_class": "mechanical",
                    "request": request,
                }
                advanced = self._apply_and_record(
                    branch,
                    problem,
                    state,
                    operation,
                    config=config,
                    envelope_ref=envelope_ref,
                    actor_kind="code",
                    decision_class="mechanical",
                    mode=mode,
                    request=request,
                    receipt=provider_result,
                )
                event_id = advanced[0]
                if advanced[2] is not None:
                    termination_reason = "invalid-operation"
                    output = self._invalid_output(output, advanced[2])
                    return self._result(
                        branch,
                        mode,
                        problem,
                        state,
                        event_id,
                        output,
                        output_root,
                        termination_reason=termination_reason,
                    )
                old_fingerprint = semantic_fingerprint(state)
                problem, state = advanced[3], advanced[1]
                if semantic_fingerprint(state) == old_fingerprint:
                    termination_reason = "semantic-no-progress"
                    break
                continue

            request = self._request(problem, state, questions, mode, choice_context, branch)
            attempt = self._commit(
                branch,
                {
                    **self._event_context(problem, envelope_ref, state),
                    "event_kind": "attempt",
                    "actor_kind": "provider",
                    "outcome": "started",
                    "state_transition": False,
                    "input_state": state,
                    "request": request,
                },
            )
            del attempt
            try:
                provider_result = self._dispatch(questions, branch, request)
            except KeyboardInterrupt:
                self._commit(
                    branch,
                    {
                        **self._event_context(problem, envelope_ref, state),
                        "event_kind": "attempt",
                        "actor_kind": "provider",
                        "outcome": "interrupted",
                        "state_transition": False,
                        "input_state": state,
                        "request": request,
                    },
                )
                raise
            status = _status(provider_result.get("status"))
            if status != "answered":
                event_id = self._commit(
                    branch,
                    {
                        **self._event_context(problem, envelope_ref, state),
                        "event_kind": "receipt",
                        "actor_kind": "provider",
                        "outcome": status or "failed",
                        "state_transition": False,
                        "input_state": state,
                        "request": request,
                        "receipt": provider_result,
                        "decision_class": _decision_class(provider_result),
                    },
                )
                output = self._provider_failure_output(output, provider_result)
                return self._result(
                    branch,
                    mode,
                    problem,
                    state,
                    event_id,
                    output,
                    output_root,
                    termination_reason=f"provider-{status or 'failed'}",
                    provider_result=provider_result,
                )
            try:
                operation = self._bind_operation(
                    self._operation_from_answer(problem, state, choice_context, provider_result),
                    state,
                )
            except ValueError as error:
                event_id = self._commit(
                    branch,
                    {
                        **self._event_context(problem, envelope_ref, state),
                        "event_kind": "receipt",
                        "actor_kind": "provider",
                        "outcome": "invalid",
                        "state_transition": False,
                        "input_state": state,
                        "request": request,
                        "receipt": provider_result,
                        "decision_class": _decision_class(provider_result),
                        "diagnostics": [str(error)],
                    },
                )
                output = self._invalid_output(output, {"status": "invalid", "error": str(error)})
                return self._result(
                    branch,
                    mode,
                    problem,
                    state,
                    event_id,
                    output,
                    output_root,
                    termination_reason="invalid-provider-choice",
                    provider_result=provider_result,
                )
            old_fingerprint = semantic_fingerprint(state)
            advanced = self._apply_and_record(
                branch,
                problem,
                state,
                operation,
                config=config,
                envelope_ref=envelope_ref,
                actor_kind="model",
                decision_class=_decision_class(provider_result),
                mode=mode,
                request=request,
                receipt=provider_result,
            )
            event_id = advanced[0]
            if advanced[2] is not None:
                output = self._invalid_output(output, advanced[2])
                return self._result(
                    branch,
                    mode,
                    problem,
                    state,
                    event_id,
                    output,
                    output_root,
                    termination_reason="invalid-operation",
                    provider_result=provider_result,
                )
            problem, state = advanced[3], advanced[1]
            if semantic_fingerprint(state) == old_fingerprint:
                termination_reason = "semantic-no-progress"
                break

        output = validate_proposal(problem, state)
        if output.get("status") == "invalid":
            termination_reason = termination_reason or "invalid-proposal"
        return self._result(
            branch,
            mode,
            problem,
            state,
            event_id,
            output,
            output_root,
            termination_reason=termination_reason,
            provider_result=provider_result,
        )

    def verify(self, branch: str = "main") -> dict[str, object]:
        """Verify an entire branch without writing anything."""

        return self.store.verify(branch)

    def replay(self, branch: str = "main") -> dict[str, object]:
        """Replay a branch with recorded operations and expansion receipts only."""

        with self.store._read_cache_scope():
            return self._replay_uncached(branch)

    def _replay_uncached(self, branch: str) -> dict[str, object]:

        _current_problem, _envelope = self._context(branch)
        problem = self._root_problem(branch)
        current_problem = problem

        def reducer(state: object, operation: object) -> object:
            nonlocal current_problem
            if not isinstance(operation, Mapping):
                raise HistoryReplayError("recorded operation is not an object")
            if operation.get("kind") == "initialize":
                initial_ref = operation.get("state_ref")
                initial = (
                    self.store.get(initial_ref)
                    if isinstance(initial_ref, str)
                    else operation.get("state")
                )
                if not isinstance(initial, Mapping):
                    raise HistoryReplayError("initial state is missing")
                return _safe_json(initial)
            if operation.get("kind") == "expand-connection":
                receipt = operation.get("receipt")
                problem_ref = operation.get("problem_ref")
                child_problem = (
                    self.store.get(problem_ref)
                    if isinstance(problem_ref, str)
                    else operation.get("problem")
                )
                if not isinstance(state, Mapping) or not isinstance(receipt, Mapping):
                    raise HistoryReplayError("expansion replay input is incomplete")
                expanded = replay_expansion(current_problem, state, receipt)
                if expanded.get("status") != "expanded":
                    raise HistoryReplayError("recorded expansion was rejected")
                if isinstance(child_problem, Mapping) and expanded.get("problem") != child_problem:
                    raise HistoryReplayError("recorded expansion problem differs")
                current_problem = dict(expanded["problem"])
                return _safe_json(expanded["state"])
            if not isinstance(state, Mapping):
                raise HistoryReplayError("operation has no input state")
            child = apply_operation(current_problem, state, operation)
            if child.get("status") == "invalid":
                raise HistoryReplayError("recorded operation was rejected")
            return _safe_json(child)

        result = self.store.replay(branch, reducer)
        state = result.get("state")
        if isinstance(state, Mapping):
            result["problem"] = current_problem
            result["output"] = validate_proposal(current_problem, state)
            result["binding"] = current_problem.get("binding")
        result["decision_trace"] = list(self._decision_trace(branch, result.get("target_event_id")))
        return result

    def fork(self, checkpoint: str, branch: str | None = None) -> Branch:
        """Create a metadata-only child at the checkpoint's pre-decision state."""

        return self.store.fork(checkpoint, branch)

    def advance(
        self,
        branch: str,
        operation: Mapping[str, object],
        *,
        expected_head: str | None = None,
        output_root: Path | str | None = None,
    ) -> PlanningRunResult:
        """Replay the existing prefix, apply one explicit replacement, and append it."""

        problem, envelope = self._context(branch)
        replay = self.replay(branch)
        state = replay.get("state")
        if not isinstance(state, Mapping):
            raise HistoryReplayError("branch has no replayable state")
        bound = self._bind_operation(operation, state)
        head = self.store.head(branch).head_event_id
        if expected_head is None:
            expected_head = head
        advanced = self._apply_and_record(
            branch,
            problem,
            state,
            bound,
            config=None,
            envelope_ref=envelope,
            actor_kind="code",
            mode="deterministic",
            decision_class="mechanical",
            expected_head=expected_head,
        )
        event_id = advanced[0]
        if advanced[2] is not None:
            output = self._invalid_output(validate_proposal(problem, state), advanced[2])
            return self._result(
                branch,
                "deterministic",
                problem,
                dict(state),
                event_id,
                output,
                output_root,
                termination_reason="invalid-operation",
            )
        child_problem, child_state = advanced[3], advanced[1]
        return self._result(
            branch,
            "deterministic",
            child_problem,
            child_state,
            event_id,
            validate_proposal(child_problem, child_state),
            output_root,
        )

    def compare(self, base_branch: str, branch: str) -> dict[str, object]:
        return self.store.compare(base_branch, branch)

    # ---- orchestration helpers --------------------------------------

    @staticmethod
    def _load_config(config: AreaConfig | str | Path) -> AreaConfig:
        if isinstance(config, (str, Path)):
            return AreaDefinition.from_yaml(config)
        return config

    def _start_or_resume(
        self,
        config: AreaConfig,
        branch: str,
        mode: RunMode,
        connection_options: Sequence[Mapping[str, object]],
    ) -> tuple[dict[str, object], dict[str, object], str, str]:
        try:
            head = self.store.head(branch)
        except HistoryMissingError:
            self.store.create_branch(branch)
            head = self.store.head(branch)
        if head.head_event_id is not None:
            problem, envelope_ref = self._context(branch)
            replay = self.replay(branch)
            state = replay.get("state")
            if not isinstance(state, Mapping):
                raise HistoryReplayError("existing branch has no replayable state")
            return dict(problem), dict(state), head.head_event_id, envelope_ref

        problem = dict(build_planning_problem(config, brief=self.brief or None))
        if not self.brief and isinstance(problem.get("brief"), Mapping):
            self.brief = dict(problem["brief"])
        problem = self._bind_problem_context(problem)
        state = dict(initial_proposal(problem))
        problem_ref = self.store.put(problem, kind="planning-problem")
        state_ref = self.store.put(state, kind="state")
        self.brief_ref = self.store.put(self.brief, kind="planning-brief") if self.brief else None
        self.policy_ref = (
            self.store.put(self.policy, kind="planning-policy") if self.policy else None
        )
        envelope = {
            "schema_version": "planning-run/v1",
            "mode": mode,
            "problem_ref": problem_ref,
            "problem_fingerprint": problem.get("input_fingerprint"),
            "binding": problem.get("binding"),
            "brief": self.brief,
            "policy": self.policy,
            "brief_ref": self.brief_ref,
            "policy_ref": self.policy_ref,
            "connection_options": _safe_json(connection_options),
        }
        envelope_ref = self.store.put(envelope, kind="planning-run")
        event_id = self._commit(
            branch,
            {
                "event_kind": "initialization",
                "actor_kind": "code",
                "outcome": "accepted",
                "state_transition": True,
                "output_state": state,
                "operation": {"kind": "initialize", "state_ref": state_ref},
                "problem_ref": problem_ref,
                "run_envelope_ref": envelope_ref,
                "dependency_refs": [problem_ref, envelope_ref],
            },
        )
        return problem, state, event_id, envelope_ref

    def _bind_problem_context(self, problem: dict[str, object]) -> dict[str, object]:
        if self.brief and "brief" not in problem:
            problem["brief"] = _copy_json(self.brief)
        if self.brief and "brief_fingerprint" not in problem:
            problem["brief_fingerprint"] = _digest(self.brief)
        if self.policy:
            problem["policy"] = _copy_json(self.policy)
            problem.pop("input_fingerprint", None)
            problem.pop("problem_id", None)
            problem["input_fingerprint"] = _digest(problem)
            problem["problem_id"] = f"planning-problem-{_digest(problem['input_fingerprint'])}"
        return problem

    def _context(self, branch: str) -> tuple[dict[str, object], str]:
        branch_record = self.store.branch(branch)
        head = self.store.head(branch)
        event_id = head.head_event_id or branch_record.base_history_event_id
        seen: set[str] = set()
        problem_ref: str | None = None
        envelope_ref: str | None = None
        while event_id is not None:
            if event_id in seen:
                raise HistoryReplayError("history event cycle detected")
            seen.add(event_id)
            event = self.store.get(event_id)
            if not isinstance(event, Mapping):
                raise HistoryReplayError("history event is not an object")
            if problem_ref is None and isinstance(event.get("problem_ref"), str):
                problem_ref = event["problem_ref"]
            if envelope_ref is None and isinstance(event.get("run_envelope_ref"), str):
                envelope_ref = event["run_envelope_ref"]
            parent = event.get("timeline_parent_id")
            event_id = parent if isinstance(parent, str) else None
        if problem_ref is None or envelope_ref is None:
            raise HistoryMissingError(f"branch {branch!r} has no planning run envelope")
        problem = self.store.get(problem_ref)
        envelope = self.store.get(envelope_ref)
        if not isinstance(problem, Mapping) or not isinstance(envelope, Mapping):
            raise HistoryReplayError("planning run envelope is malformed")
        if isinstance(envelope.get("brief"), Mapping) and not self.brief:
            self.brief = dict(envelope["brief"])
        if isinstance(envelope.get("policy"), Mapping) and not self.policy:
            self.policy = dict(envelope["policy"])
        if isinstance(envelope.get("brief_ref"), str):
            self.brief_ref = envelope["brief_ref"]
        if isinstance(envelope.get("policy_ref"), str):
            self.policy_ref = envelope["policy_ref"]
        if isinstance(envelope.get("connection_options"), list) and not self.connection_options:
            self.connection_options = [
                dict(item) for item in envelope["connection_options"] if isinstance(item, Mapping)
            ]
        return dict(problem), envelope_ref

    def _root_problem(self, branch: str) -> dict[str, object]:
        branch_record = self.store.branch(branch)
        if branch_record.fork_checkpoint_id and branch_record.base_history_event_id:
            base_event = self.store.get(branch_record.base_history_event_id)
            if isinstance(base_event, Mapping) and isinstance(base_event.get("problem_ref"), str):
                problem = self.store.get(base_event["problem_ref"])
                if isinstance(problem, Mapping):
                    return dict(problem)
        event_id = self.store.head(branch).head_event_id or branch_record.base_history_event_id
        problem_refs: list[str] = []
        seen: set[str] = set()
        while event_id is not None:
            if event_id in seen:
                raise HistoryReplayError("history event cycle detected")
            seen.add(event_id)
            event = self.store.get(event_id)
            if isinstance(event, Mapping) and isinstance(event.get("problem_ref"), str):
                problem_refs.append(event["problem_ref"])
            parent = event.get("timeline_parent_id") if isinstance(event, Mapping) else None
            event_id = parent if isinstance(parent, str) else None
        if not problem_refs:
            raise HistoryMissingError(f"branch {branch!r} has no planning problem")
        problem = self.store.get(problem_refs[-1])
        if not isinstance(problem, Mapping):
            raise HistoryReplayError("planning problem is malformed")
        return dict(problem)

    def _event_context(
        self, problem: Mapping[str, object], envelope_ref: str, state: Mapping[str, object]
    ) -> dict[str, object]:
        problem_ref = self.store.put(problem, kind="planning-problem")
        state_ref = self.store.put(state, kind="state")
        return {
            "problem_ref": problem_ref,
            "run_envelope_ref": envelope_ref,
            "state_ref": state_ref,
            "dependency_refs": [problem_ref, envelope_ref, state_ref],
        }

    def _commit(
        self,
        branch: str,
        event: Mapping[str, object],
        expected_head: str | None = None,
    ) -> str:
        if expected_head is None:
            expected_head = self.store.head(branch).head_event_id
        return self.store.commit(branch, expected_head, event)

    def _apply_and_record(
        self,
        branch: str,
        problem: Mapping[str, object],
        state: Mapping[str, object],
        operation: Mapping[str, object],
        *,
        config: AreaConfig | None,
        envelope_ref: str,
        actor_kind: str,
        mode: RunMode,
        decision_class: DecisionClass | None = None,
        request: Mapping[str, object] | None = None,
        receipt: Mapping[str, object] | None = None,
        expected_head: str | None = None,
    ) -> tuple[str, dict[str, object], dict[str, object] | None, dict[str, object]]:
        current_problem = dict(problem)
        expanded = None
        payload = operation.get("payload")
        payload_mapping = payload if isinstance(payload, Mapping) else {}
        if operation.get("kind") == "expand-connection":
            receipt_value = operation.get("receipt")
            if not isinstance(receipt_value, Mapping):
                child = {"status": "invalid", "diagnostics": ["expansion receipt is missing"]}
            else:
                expanded = replay_expansion(current_problem, state, receipt_value)
                child_problem = expanded.get("problem")
                child_state = expanded.get("state")
                if (
                    expanded.get("status") == "expanded"
                    and isinstance(child_problem, Mapping)
                    and isinstance(child_state, Mapping)
                ):
                    current_problem = dict(child_problem)
                    child = dict(child_state)
                else:
                    child = dict(expanded)
        elif (
            config is not None
            and operation.get("kind") in {"propose-connection", "revise-connection"}
            and not any(
                isinstance(item, Mapping)
                and item.get("connection_id") == payload_mapping.get("connection_id")
                for item in current_problem.get("candidates", [])
            )
        ):
            expanded = expand_connection(current_problem, state, operation, config)
            if expanded.get("status") == "expanded":
                child_problem = expanded.get("problem")
                child_state = expanded.get("state")
                if isinstance(child_problem, Mapping) and isinstance(child_state, Mapping):
                    child_problem_ref = self.store.put(child_problem, kind="planning-problem")
                    operation = {
                        "kind": "expand-connection",
                        "operation": _safe_json(operation),
                        "receipt": _safe_json(expanded.get("receipt", {})),
                        "problem_ref": child_problem_ref,
                    }
                    current_problem = dict(child_problem)
                    child = dict(child_state)
                else:
                    child = {"status": "invalid"}
            else:
                child = dict(expanded)
        else:
            child = apply_operation(current_problem, state, operation)
        if child.get("status") == "invalid":
            diagnostic = child.get("diagnostics", [{"code": "operation", "message": "rejected"}])
            event = {
                **self._event_context(problem, envelope_ref, state),
                "event_kind": "diagnostic",
                "actor_kind": actor_kind,
                "outcome": "invalid",
                "state_transition": False,
                "input_state": state,
                "operation": operation,
                "request": request,
                "receipt": receipt,
                "decision_class": decision_class,
                "diagnostics": _safe_json(diagnostic),
            }
            event_id = self._commit(branch, event, expected_head)
            return event_id, dict(state), dict(child), current_problem
        event = {
            **self._event_context(problem, envelope_ref, state),
            "event_kind": "decision",
            "actor_kind": actor_kind,
            "outcome": "accepted",
            "state_transition": True,
            "input_state": state,
            "output_state": child,
            "operation": operation,
            "request": request,
            "receipt": receipt,
            "decision_class": decision_class,
            "mode": mode,
        }
        child_problem_ref = self.store.put(current_problem, kind="planning-problem")
        event["problem_ref"] = child_problem_ref
        event["dependency_refs"] = sorted(set(event["dependency_refs"]) | {child_problem_ref})
        event_id = self._commit(branch, event, expected_head)
        return event_id, dict(child), None, current_problem

    def _result(
        self,
        branch: str,
        mode: str,
        problem: Mapping[str, object],
        state: Mapping[str, object] | None,
        event_id: str | None,
        output: Mapping[str, object],
        output_root: Path | str | None,
        *,
        termination_reason: str | None = None,
        provider_result: Mapping[str, object] | None = None,
    ) -> PlanningRunResult:
        result = PlanningRunResult(
            status=str(output.get("status", "incomplete")),
            mode=mode,
            branch_id=branch,
            history_root=str(self.history_root),
            history_event_id=event_id,
            problem=dict(problem),
            state=dict(state) if isinstance(state, Mapping) else None,
            output=dict(output),
            diagnostics=tuple(output.get("validation", {}).get("diagnostics", []))
            if isinstance(output.get("validation"), Mapping)
            else (),
            decision_trace=self._decision_trace(branch, event_id),
            termination_reason=termination_reason,
            provider_result=dict(provider_result) if provider_result is not None else None,
        )
        if output_root is None:
            return result
        destination = Path(output_root)
        destination.mkdir(parents=True, exist_ok=True)
        publication: dict[str, object] | None = None
        if result.status in {"validated", "reviewable-incomplete"}:
            history_metadata = {
                "history_ref": str(self.history_root),
                "history_root": str(self.history_root),
                "history_event_id": event_id,
                "history_id": event_id,
                "branch_id": branch,
                "branch_ref": branch,
                "state_fingerprint": state.get("state_fingerprint")
                if isinstance(state, Mapping)
                else None,
                "decision_trace": self._compact_decision_trace(result.decision_trace),
            }
            try:
                publication = publish_planning_output(
                    result.output,
                    destination,
                    history_metadata,
                )
            except PublicationValidationError as error:
                publication = {"status": "failed", "error": str(error)}
            self._atomic_json(destination / "proposal.json", result.output)
        result = replace(
            result,
            publication=publication,
            run_path=destination / "run.json",
        )
        # Keep the public result materialized for callers while making the
        # durable report a small index into immutable history records.  Large
        # planning problems and proposal states are already content-addressed
        # by the initialization/decision events; copying them into run.json
        # multiplies storage for every report.
        report = result.as_dict()
        report.pop("problem", None)
        report.pop("state", None)
        problem_ref = self.store.put(problem, kind="planning-problem")
        state_ref = self.store.put(state, kind="state") if state is not None else None
        report["problem_ref"] = problem_ref
        report["state_ref"] = state_ref
        report["problem_fingerprint"] = problem.get("input_fingerprint")
        report["state_fingerprint"] = (
            state.get("state_fingerprint") if isinstance(state, Mapping) else None
        )
        report["decision_trace"] = self._compact_decision_trace(result.decision_trace)
        self._atomic_json(destination / "run.json", report)
        return result

    def _decision_trace(self, branch: str, event_id: object) -> tuple[dict[str, object], ...]:
        """Project stable attribution and safe provider identity from history."""

        current = event_id if isinstance(event_id, str) else self.store.head(branch).head_event_id
        trace: list[dict[str, object]] = []
        seen: set[str] = set()
        while isinstance(current, str):
            if current in seen:
                raise HistoryReplayError("history event cycle detected")
            seen.add(current)
            event = self.store.get(current)
            if not isinstance(event, Mapping):
                raise HistoryReplayError("history decision event is not an object")
            decision_class = event.get("decision_class")
            if decision_class in {"mechanical", "classifier", "agent"}:
                item: dict[str, object] = {
                    "event_id": current,
                    "event_kind": event.get("event_kind"),
                    "actor_kind": event.get("actor_kind"),
                    "decision_class": decision_class,
                    "outcome": event.get("outcome"),
                }
                operation = event.get("operation")
                if not isinstance(operation, Mapping) and isinstance(
                    event.get("operation_ref"), str
                ):
                    operation_value = self.store.get(event["operation_ref"])
                    operation = operation_value if isinstance(operation_value, Mapping) else None
                if isinstance(operation, Mapping):
                    item["operation_kind"] = operation.get("kind")
                receipt = event.get("receipt")
                if not isinstance(receipt, Mapping) and isinstance(event.get("receipt_ref"), str):
                    receipt_value = self.store.get(event["receipt_ref"])
                    receipt = receipt_value if isinstance(receipt_value, Mapping) else None
                if isinstance(receipt, Mapping):
                    for key in (
                        "provider",
                        "model",
                        "usage",
                        "capability_id",
                        "request_receipt",
                        "response_receipt",
                        "receipt",
                    ):
                        if key in receipt:
                            item[key] = _safe_json(receipt[key])
                trace.append(item)
            parent = event.get("timeline_parent_id")
            current = parent if isinstance(parent, str) else None
        trace.reverse()
        return tuple(trace)

    def _compact_decision_trace(
        self,
        trace: Sequence[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        """Keep report attribution while referring to the exact receipt once."""

        compact: list[dict[str, object]] = []
        for item in trace:
            projected = dict(item)
            event_id = projected.get("event_id")
            if isinstance(event_id, str):
                event = self.store.get(event_id)
                if isinstance(event, Mapping) and isinstance(event.get("receipt_ref"), str):
                    projected["receipt_ref"] = event["receipt_ref"]
            for key in ("request_receipt", "response_receipt", "receipt"):
                projected.pop(key, None)
            compact.append(projected)
        return compact

    @staticmethod
    def _atomic_json(path: Path, value: object) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps(_safe_json(value), sort_keys=True, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _connection_operations(
        requested: Sequence[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        operations: list[dict[str, object]] = []
        for item in requested:
            if "kind" in item:
                operations.append(dict(item))
            else:
                operations.append({"kind": "propose-connection", "payload": dict(item)})
        return operations

    @staticmethod
    def _bind_operation(
        template: Mapping[str, object], state: Mapping[str, object]
    ) -> dict[str, object]:
        operation = dict(template)
        if operation.get("kind") == "expand-connection":
            nested = operation.get("operation")
            if isinstance(nested, Mapping):
                operation["operation"] = PlanningRuntime._bind_operation(nested, state)
            return operation
        operation.setdefault("operation_id", f"planning-operation-{_digest(operation)[:24]}")
        operation["parent_state_fingerprint"] = state.get("state_fingerprint")
        return operation

    def _questions(
        self, problem: Mapping[str, object], state: Mapping[str, object]
    ) -> tuple[dict[str, ChoiceQuestion], dict[str, object]]:
        reserved = {
            _UNKNOWN: "record an unknown and continue review",
            _EVIDENCE: "request evidence before selecting an alignment",
            _NONE: "record that no supplied option is adopted",
        }
        configured_connections = {
            str(item.get("connection_id") or item.get("operation_id")): dict(item)
            for item in self.connection_options
            if item.get("connection_id") or item.get("operation_id")
        }
        intents = [
            item
            for item in state.get("connection_intents", [])
            if isinstance(item, Mapping) and item.get("connection_id")
        ]
        intent_ids = {str(item["connection_id"]) for item in intents}
        selected_connection_ids = {
            str(item.get("obligation_id") or item.get("connection_id"))
            for item in state.get("selected_alignments", [])
            if isinstance(item, Mapping)
            and (item.get("obligation_id") or item.get("connection_id"))
        }
        active_connection_ids = intent_ids - selected_connection_ids
        pending_connections = {
            connection_id: connection
            for connection_id, connection in configured_connections.items()
            if connection_id not in intent_ids
        }
        if pending_connections and not active_connection_ids:
            connections = pending_connections
            criteria = {
                key: self._connection_label(value) for key, value in sorted(connections.items())
            }
            dispatchable = len(criteria) + len(reserved) <= 255
            if not dispatchable:
                criteria = {}
            criteria.update(reserved)
            return (
                {
                    "decision": ChoiceQuestion(
                        instructions=(
                            "Choose one admitted named-place connection intent or an explicit "
                            "unresolved outcome."
                        ),
                        criteria=criteria,
                    )
                },
                {
                    "question_kind": "connection",
                    "connections": connections,
                    "candidate_refs": [],
                    "scope_refs": sorted(connections),
                    "source_corridor_refs": sorted(
                        {
                            str(reference)
                            for connection in connections.values()
                            for reference in connection.get("corridor_refs", [])
                        }
                    ),
                    "place_refs": sorted(
                        {
                            str(reference)
                            for connection in connections.values()
                            for reference in (
                                connection.get("origin_place_id"),
                                connection.get("destination_place_id"),
                            )
                            if reference
                        }
                    ),
                    "evidence_refs": sorted(
                        {
                            str(reference)
                            for connection in connections.values()
                            for reference in connection.get("evidence_refs", [])
                        }
                    ),
                    "dispatchable": bool(connections) and dispatchable,
                    "permitted_action_kinds": [
                        "propose-connection",
                        "revise-connection",
                        "request-evidence",
                        "request-candidates",
                        "record-gap",
                    ],
                },
            )

        all_candidates = [
            item
            for item in problem.get("candidates", [])
            if isinstance(item, Mapping) and isinstance(item.get("candidate_id"), str)
        ]
        if active_connection_ids:
            candidates = [
                item
                for item in all_candidates
                if str(item.get("connection_id")) in active_connection_ids
            ]
        else:
            candidates = self._task_candidates(problem, state, all_candidates)
        scope_refs = sorted(
            {str(item.get("obligation_id")) for item in candidates if item.get("obligation_id")}
        )
        dispatchable = len(candidates) + len(reserved) <= 255
        offered_candidates = candidates if dispatchable else []
        criteria = {
            str(item["candidate_id"]): self._candidate_label(item)
            for item in sorted(offered_candidates, key=lambda value: str(value["candidate_id"]))
        }
        criteria.update(reserved)
        evidence_refs = sorted(
            {
                str(reference)
                for candidate in offered_candidates
                for reference in (
                    candidate.get("evidence_refs", [])
                    if isinstance(candidate.get("evidence_refs"), list)
                    else candidate.get("provenance", {}).get("evidence_refs", [])
                    if isinstance(candidate.get("provenance"), Mapping)
                    else []
                )
            }
        )
        source_corridor_refs = sorted(
            {
                str(reference)
                for candidate in offered_candidates
                for reference in candidate.get("source_corridor_refs", [])
            }
        )
        place_refs = sorted(
            {
                str(reference)
                for candidate in offered_candidates
                for reference in candidate.get("place_refs", [])
            }
        )
        unknown_refs = sorted(
            str(item.get("unknown_id"))
            for item in state.get("unknown_facts", [])
            if isinstance(item, Mapping) and item.get("unknown_id")
        )
        gap_refs = sorted(
            str(item.get("gap_id"))
            for source in (problem, state)
            for item in source.get("planning_gaps", [])
            if isinstance(item, Mapping) and item.get("gap_id")
        )
        return (
            {
                "decision": ChoiceQuestion(
                    instructions=(
                        "Choose one admitted planning option or an explicit unresolved outcome."
                    ),
                    criteria=criteria,
                )
            },
            {
                "question_kind": "alignment",
                "kind": "select-alignment",
                "candidates": {
                    str(item["candidate_id"]): dict(item) for item in offered_candidates
                },
                "candidate_refs": [str(item["candidate_id"]) for item in offered_candidates],
                "evidence_refs": evidence_refs,
                "scope_refs": scope_refs,
                "source_corridor_refs": source_corridor_refs,
                "place_refs": place_refs,
                "unknown_refs": unknown_refs,
                "gap_refs": gap_refs,
                "dispatchable": dispatchable,
                "permitted_action_kinds": [
                    "select-alignment",
                    "record-departure",
                    "propose-intervention",
                    "propose-connection",
                    "revise-connection",
                    "request-evidence",
                    "request-candidates",
                    "record-gap",
                ],
            },
        )

    @staticmethod
    def _task_candidates(
        problem: Mapping[str, object],
        state: Mapping[str, object],
        candidates: Sequence[Mapping[str, object]],
    ) -> list[Mapping[str, object]]:
        pending = state.get("pending_task") or problem.get("pending_task")
        if isinstance(pending, Mapping) and isinstance(pending.get("candidate_refs"), list):
            refs = {str(item) for item in pending["candidate_refs"]}
            return [item for item in candidates if str(item.get("candidate_id")) in refs]
        if not candidates:
            return []
        obligation_id = str(candidates[0].get("obligation_id"))
        return [item for item in candidates if str(item.get("obligation_id")) == obligation_id]

    @staticmethod
    def _candidate_label(candidate: Mapping[str, object]) -> str:
        provenance = candidate.get("provenance")
        evidence_refs = (
            provenance.get("evidence_refs", [])
            if isinstance(provenance, Mapping)
            else candidate.get("evidence_refs", [])
        )
        source_refs = candidate.get("source_corridor_refs", [])
        return json.dumps(
            {
                "role": candidate.get("role"),
                "connection_id": candidate.get("connection_id"),
                "current_or_future": candidate.get("current_or_future"),
                "source_corridor_refs": source_refs,
                "evidence_refs": evidence_refs,
                "place_refs": candidate.get("place_refs", []),
            },
            sort_keys=True,
            ensure_ascii=True,
        )

    @staticmethod
    def _connection_label(connection: Mapping[str, object]) -> str:
        return json.dumps(
            {
                "origin_place_id": connection.get("origin_place_id"),
                "destination_place_id": connection.get("destination_place_id"),
                "corridor_refs": connection.get("corridor_refs", []),
                "current_or_future": connection.get("current_or_future"),
                "evidence_refs": connection.get("evidence_refs", []),
            },
            sort_keys=True,
            ensure_ascii=True,
        )

    @staticmethod
    def _deterministic_operation(
        problem: Mapping[str, object],
        state: Mapping[str, object],
        context: Mapping[str, object],
    ) -> dict[str, object]:
        del problem, context
        target_refs = [
            str(item.get("obligation_id"))
            for item in state.get("obligations", [])
            if isinstance(item, Mapping) and item.get("obligation_id")
        ]
        return {
            "kind": "request-evidence",
            "payload": {
                "target_refs": target_refs,
                "claim": "planning-decision",
                "reason": "deterministic mode cannot choose a planning judgment",
            },
        }

    @staticmethod
    def _coverage_operation(context: Mapping[str, object]) -> dict[str, object]:
        return {
            "kind": "request-candidates",
            "payload": {
                "target_refs": [str(item) for item in context.get("scope_refs", [])],
                "claim": "candidate-coverage",
                "reason": "candidate coverage needs an explicit connection or expansion task",
            },
        }

    @staticmethod
    def _operation_from_answer(
        problem: Mapping[str, object],
        state: Mapping[str, object],
        context: Mapping[str, object],
        result: Mapping[str, object],
    ) -> dict[str, object]:
        proposal = result.get("proposal")
        if isinstance(proposal, Mapping) or isinstance(result.get("operation"), Mapping):
            return PlanningRuntime._operation_from_proposal(context, result)
        answers = result.get("answers")
        if not isinstance(answers, Mapping):
            raise ValueError("provider response has no answers")
        answer = answers.get("decision")
        if isinstance(answer, str):
            choice = answer
        elif isinstance(answer, Mapping) and isinstance(answer.get("choice"), str):
            choice = str(answer["choice"])
        else:
            raise ValueError("provider response has no decision choice")
        if choice in (_UNKNOWN, _EVIDENCE):
            target_refs = (
                [str(item) for item in context.get("scope_refs", [])]
                if context.get("question_kind") == "alignment"
                else []
            )
            return {
                "kind": "request-evidence",
                "payload": {
                    "target_refs": target_refs,
                    "claim": "planning-decision",
                    "reason": "provider marked the planning choice unresolved",
                },
            }
        if choice == _NONE:
            target_refs = (
                [str(item) for item in context.get("scope_refs", [])]
                if context.get("question_kind") == "alignment"
                else []
            )
            return {
                "kind": "record-gap",
                "payload": {
                    "target_refs": target_refs,
                    "reason": "provider selected none of the admitted options",
                },
            }
        if context.get("question_kind") == "connection":
            connections = context.get("connections", {})
            if not isinstance(connections, Mapping) or choice not in connections:
                raise ValueError("provider selected a connection outside the offered menu")
            connection = connections[choice]
            if not isinstance(connection, Mapping):
                raise ValueError("offered connection is malformed")
            payload = {
                key: value
                for key, value in connection.items()
                if key
                in {
                    "connection_id",
                    "origin_place_id",
                    "destination_place_id",
                    "corridor_refs",
                    "current_or_future",
                    "status",
                    "reason",
                }
            }
            kind = str(connection.get("kind", "propose-connection"))
            if kind not in {"propose-connection", "revise-connection"}:
                raise ValueError("connection option has an unsupported operation kind")
            return {"kind": kind, "payload": payload}
        candidates = context.get("candidates", {})
        if not isinstance(candidates, Mapping) or choice not in candidates:
            raise ValueError("provider selected an option outside the offered menu")
        candidate = candidates[choice]
        if not isinstance(candidate, Mapping):
            raise ValueError("offered candidate is malformed")
        payload: dict[str, object] = {"candidate_id": choice}
        if candidate.get("obligation_id") is not None:
            payload["obligation_id"] = candidate["obligation_id"]
        del problem
        return {
            "kind": "select-alignment",
            "payload": payload,
            "parent_state_fingerprint": state.get("state_fingerprint"),
        }

    @staticmethod
    def _operation_from_proposal(
        context: Mapping[str, object], result: Mapping[str, object]
    ) -> dict[str, object]:
        proposal = result.get("proposal")
        if not isinstance(proposal, Mapping):
            proposal = result
        candidate = proposal.get("operation")
        if isinstance(candidate, Mapping):
            operation = dict(candidate)
        elif isinstance(candidate, str):
            operation = {
                "kind": candidate,
                "payload": proposal.get("payload", {}),
            }
        elif isinstance(proposal.get("kind"), str):
            operation = dict(proposal)
        else:
            raise ValueError("specialist proposal has no typed operation")
        kind = operation.get("kind")
        permitted = {str(item) for item in context.get("permitted_action_kinds", [])}
        if not isinstance(kind, str) or kind not in permitted:
            raise ValueError("specialist proposed an operation outside the task scope")
        if not isinstance(operation.get("payload", {}), Mapping):
            raise ValueError("specialist operation payload is not an object")
        payload = operation["payload"]
        allowed_scope = {
            str(item)
            for key in (
                "scope_refs",
                "candidate_refs",
                "source_corridor_refs",
                "place_refs",
                "evidence_refs",
                "unknown_refs",
                "gap_refs",
            )
            for item in context.get(key, [])
        }

        def require_scope(values: object, message: str) -> None:
            references = values if isinstance(values, (list, tuple, set)) else []
            if any(str(item) not in allowed_scope for item in references):
                raise ValueError(message)

        if kind == "select-alignment":
            offered = context.get("candidates", {})
            candidate_id = payload.get("candidate_id")
            if not isinstance(offered, Mapping) or candidate_id not in offered:
                raise ValueError("specialist selected a candidate outside the offered task scope")
            candidate = offered[candidate_id]
            if not isinstance(candidate, Mapping):
                raise ValueError("offered candidate is malformed")
            obligation_id = payload.get("obligation_id")
            if obligation_id is not None and obligation_id != candidate.get("obligation_id"):
                raise ValueError("specialist selected an obligation outside the offered task scope")
        elif kind in {"propose-connection", "revise-connection"}:
            for key in ("origin_place_id", "destination_place_id"):
                if payload.get(key) not in {str(item) for item in context.get("place_refs", [])}:
                    raise ValueError("specialist referenced a place outside the offered task scope")
            require_scope(
                payload.get("corridor_refs", []),
                "specialist referenced a corridor outside the offered task scope",
            )
        elif kind == "record-departure":
            allowed_corridors = {str(item) for item in context.get("source_corridor_refs", [])}
            affected = payload.get("source_corridor_refs", payload.get("target_refs", []))
            if any(str(item) not in allowed_corridors for item in affected):
                raise ValueError("specialist referenced a corridor outside the offered task scope")
            allowed_evidence = {str(item) for item in context.get("evidence_refs", [])}
            if any(str(item) not in allowed_evidence for item in payload.get("evidence_refs", [])):
                raise ValueError("specialist referenced evidence outside the offered task scope")
            outcome = payload.get("outcome")
            if isinstance(outcome, Mapping) and outcome.get("candidate_id") is not None:
                offered = context.get("candidates", {})
                if not isinstance(offered, Mapping) or outcome.get("candidate_id") not in offered:
                    raise ValueError(
                        "specialist referenced an alternate outside the offered task scope"
                    )
            if isinstance(outcome, Mapping) and outcome.get("gap_id") is not None:
                require_scope(
                    [outcome["gap_id"]],
                    "specialist referenced a departure gap outside the offered task scope",
                )
        elif kind == "propose-intervention":
            require_scope(
                payload.get("target_refs", []),
                "specialist referenced an intervention target outside the offered task scope",
            )
            require_scope(
                payload.get("evidence_refs", []),
                "specialist referenced evidence outside the offered task scope",
            )
        elif kind in {"request-evidence", "request-candidates", "record-gap"}:
            require_scope(
                payload.get("target_refs", []),
                "specialist referenced a target outside the offered task scope",
            )
        return operation

    def _request(
        self,
        problem: Mapping[str, object],
        state: Mapping[str, object],
        questions: Mapping[str, object],
        mode: RunMode,
        context: Mapping[str, object],
        branch: str,
    ) -> dict[str, object]:
        task_packet = self._task_packet(problem, state, questions, mode, context, branch)
        request_key = (
            problem.get("problem_id"),
            state.get("state_id"),
            context.get("question_kind"),
            context.get("scope_refs", []),
            context.get("candidate_refs", []),
        )
        packet_ref = self.store.put(task_packet, kind="planning-task-packet")
        return {
            "schema_version": "planning-request/v1",
            "request_id": f"planning-request-{_digest(request_key)[:24]}",
            "mode": mode,
            "problem_id": problem.get("problem_id"),
            "problem_fingerprint": problem.get("input_fingerprint"),
            "binding": _safe_json(problem.get("binding")),
            "brief": _safe_json(self.brief),
            "policy": _safe_json(self.policy),
            "state_id": state.get("state_id"),
            "state_fingerprint": state.get("state_fingerprint"),
            "state_ref": self.store.put(state, kind="state"),
            "task_packet": task_packet,
            "task_packet_ref": packet_ref,
            "task_packet_fingerprint": _digest(task_packet),
            "scope_refs": _safe_json(context.get("scope_refs", [])),
            "question_kind": context.get("question_kind", "obligation-disposition"),
            "candidate_refs": _safe_json(context.get("candidate_refs", [])),
            "evidence_refs": _safe_json(context.get("evidence_refs", [])),
            "policy_ref": self.policy_ref or problem.get("policy_fingerprint"),
            "offered_consideration_refs": _safe_json(
                [*context.get("candidate_refs", []), _UNKNOWN, _EVIDENCE, _NONE]
            ),
            "permitted_action_kinds": _safe_json(context.get("permitted_action_kinds", [])),
            "output_contract": "typed-choice-operation/v1",
            "history_ref": str(self.history_root),
            "branch_id": branch,
            "questions": _safe_json(questions),
        }

    def _task_packet(
        self,
        problem: Mapping[str, object],
        state: Mapping[str, object],
        questions: Mapping[str, object],
        mode: RunMode,
        context: Mapping[str, object],
        branch: str,
    ) -> dict[str, object]:
        """Project one decision into the facts a provider is allowed to see.

        The proposal state remains an immutable history artifact.  A provider
        receives only this task's admitted endpoints, candidates, source facts,
        evidence, and unresolved items, together with the binding needed to
        identify the exact input it is judging.
        """

        raw_candidates = context.get("candidates", {})
        candidates: list[dict[str, object]] = []
        if isinstance(raw_candidates, Mapping):
            for candidate_id, value in raw_candidates.items():
                if isinstance(value, Mapping):
                    candidate = dict(value)
                    candidate.setdefault("candidate_id", str(candidate_id))
                    candidates.append(candidate)
        candidates.sort(key=lambda item: str(item.get("candidate_id", "")))

        raw_connections = context.get("connections", {})
        connections: list[dict[str, object]] = []
        if isinstance(raw_connections, Mapping):
            for value in raw_connections.values():
                if isinstance(value, Mapping):
                    connections.append(dict(value))
        intents = state.get("connection_intents")
        if isinstance(intents, list):
            connections.extend(item for item in intents if isinstance(item, Mapping))

        place_by_id = {
            str(item.get("place_id")): item
            for item in problem.get("places", [])
            if isinstance(item, Mapping) and item.get("place_id")
        }
        endpoint_ids: set[str] = set()
        corridor_ids: set[str] = set()
        obligation_ids: set[str] = set(str(item) for item in context.get("scope_refs", []))
        evidence_ids: set[str] = set(str(item) for item in context.get("evidence_refs", []))
        candidate_ids: set[str] = set()
        for candidate in candidates:
            candidate_id = candidate.get("candidate_id")
            if candidate_id:
                candidate_ids.add(str(candidate_id))
            if candidate.get("obligation_id"):
                obligation_ids.add(str(candidate["obligation_id"]))
            if candidate.get("connection_id"):
                obligation_ids.add(str(candidate["connection_id"]))
            endpoint_ids.update(str(item) for item in candidate.get("place_refs", []))
            corridor_ids.update(str(item) for item in candidate.get("source_corridor_refs", []))
            provenance = candidate.get("provenance")
            if isinstance(provenance, Mapping):
                evidence_ids.update(str(item) for item in provenance.get("evidence_refs", []))
            evidence_ids.update(str(item) for item in candidate.get("evidence_refs", []))
        for connection in connections:
            if connection.get("connection_id"):
                obligation_ids.add(str(connection["connection_id"]))
            for key in ("origin_place_id", "destination_place_id"):
                if connection.get(key):
                    endpoint_ids.add(str(connection[key]))
            corridor_ids.update(str(item) for item in connection.get("corridor_refs", []))
            evidence_ids.update(str(item) for item in connection.get("evidence_refs", []))

        named_endpoints = [
            {
                "admitted_id": place_id,
                "place_id": place_id,
                "name": place.get("name"),
                "kind": place.get("kind"),
                "place_class": place.get("place_class"),
                "source_refs": _safe_json(place.get("source_refs", [])),
            }
            for place_id, place in sorted(place_by_id.items())
            if place_id in endpoint_ids
        ]
        named_connections = [
            self._connection_packet(item, place_by_id)
            for item in connections
            if item.get("connection_id")
        ]

        source_corridors = [
            dict(item)
            for item in problem.get("source_corridors", [])
            if isinstance(item, Mapping) and str(item.get("corridor_id")) in corridor_ids
        ]
        obligations = [
            dict(item)
            for item in problem.get("obligations", [])
            if isinstance(item, Mapping) and str(item.get("obligation_id")) in obligation_ids
        ]
        source_edges: list[dict[str, object]] = []
        graph_evidence = problem.get("graph_evidence")
        directed_edge_ids: set[str] = set()
        for candidate in candidates:
            graph_path = candidate.get("graph_path")
            endpoint = candidate.get("endpoint_provenance")
            for path in (graph_path, endpoint):
                if isinstance(path, Mapping):
                    directed_edge_ids.update(
                        str(item) for item in path.get("directed_edge_ids", [])
                    )
        if isinstance(graph_evidence, Mapping):
            source_edges = [
                dict(item)
                for item in graph_evidence.get("directed_edges", [])
                if isinstance(item, Mapping)
                and str(item.get("directed_edge_id")) in directed_edge_ids
            ]
        evidence_ids.update(
            str(item) for corridor in source_corridors for item in corridor.get("evidence_refs", [])
        )

        unresolved: list[dict[str, object]] = []
        feedback_unknowns: list[dict[str, object]] = []
        for source_name, source in (("problem", problem), ("state", state)):
            for collection_name in ("planning_gaps", "unknown_facts"):
                collection = source.get(collection_name, [])
                if not isinstance(collection, list):
                    continue
                for item in collection:
                    if not isinstance(item, Mapping):
                        continue
                    subject_refs = {
                        str(item.get(key))
                        for key in ("subject_id", "obligation_id", "gap_id")
                        if item.get(key)
                    }
                    subject_refs.update(
                        str(reference) for reference in item.get("subject_refs", [])
                    )
                    if subject_refs:
                        if not subject_refs.intersection(
                            endpoint_ids | corridor_ids | obligation_ids | candidate_ids
                        ):
                            continue
                    elif not (
                        source_name == "state"
                        and collection_name == "unknown_facts"
                        and item.get("request_kind") in {"request-evidence", "request-candidates"}
                    ):
                        continue
                    entry = dict(item)
                    entry["origin"] = source_name
                    unresolved.append(entry)
                    if source_name == "state" and collection_name == "unknown_facts":
                        feedback_unknowns.append(entry)

        prior_decisions = {
            "connection_intents": [
                _safe_json(item)
                for item in state.get("connection_intents", [])
                if isinstance(item, Mapping)
            ],
            "selected_alignments": [
                _safe_json(item)
                for item in state.get("selected_alignments", [])
                if isinstance(item, Mapping)
            ],
            "departures": [
                _safe_json(item)
                for item in state.get("departures", [])
                if isinstance(item, Mapping)
            ],
            "future_interventions": [
                _safe_json(item)
                for item in state.get("future_interventions", [])
                if isinstance(item, Mapping)
            ],
        }

        packet_basis = {
            "problem_id": problem.get("problem_id"),
            "state_id": state.get("state_id"),
            "question_kind": context.get("question_kind"),
            "candidate_refs": sorted(candidate_ids),
            "scope_refs": sorted(str(item) for item in context.get("scope_refs", [])),
        }
        packet: dict[str, object] = {
            "schema_version": "planning-task-packet/v1",
            "task_id": f"planning-task-{_digest(packet_basis)[:24]}",
            "mode": mode,
            "question_kind": context.get("question_kind", "obligation-disposition"),
            "brief": _safe_json(problem.get("brief", self.brief)),
            "policy": _safe_json(self.policy),
            "input_binding": _safe_json(problem.get("binding", {})),
            "history": {
                "root": str(self.history_root),
                "branch_id": branch,
                "head_event_id": self.store.head(branch).head_event_id,
                "problem_id": problem.get("problem_id"),
                "problem_fingerprint": problem.get("input_fingerprint"),
                "state_id": state.get("state_id"),
                "state_fingerprint": state.get("state_fingerprint"),
            },
            "scope": {
                "scope_refs": _safe_json(context.get("scope_refs", [])),
                "candidate_refs": sorted(candidate_ids),
                "evidence_refs": sorted(evidence_ids),
                "permitted_action_kinds": _safe_json(context.get("permitted_action_kinds", [])),
            },
            "named_endpoints": named_endpoints,
            "connection_options": named_connections,
            "candidates": [_safe_json(item) for item in candidates],
            "facts": {
                "obligations": [_safe_json(item) for item in obligations],
                "source_corridors": [_safe_json(item) for item in source_corridors],
            },
            "source_evidence": {
                "evidence_refs": sorted(evidence_ids),
                "directed_edges": [_safe_json(item) for item in source_edges],
            },
            "prior_decisions": prior_decisions,
            "feedback_unknowns": feedback_unknowns,
            "unknowns": [_safe_json(item) for item in unresolved],
            "questions": _safe_json(questions),
        }
        return packet

    @staticmethod
    def _connection_packet(
        connection: Mapping[str, object],
        places: Mapping[str, Mapping[str, object]],
    ) -> dict[str, object]:
        def endpoint(place_id: object) -> dict[str, object]:
            identifier = str(place_id) if place_id is not None else ""
            place = places.get(identifier, {})
            return {
                "admitted_id": identifier,
                "place_id": identifier,
                "name": place.get("name"),
                "kind": place.get("kind"),
                "place_class": place.get("place_class"),
            }

        return {
            "admitted_id": connection.get("connection_id"),
            "connection_id": connection.get("connection_id"),
            "origin": endpoint(connection.get("origin_place_id")),
            "destination": endpoint(connection.get("destination_place_id")),
            "corridor_refs": _safe_json(connection.get("corridor_refs", [])),
            "current_or_future": connection.get("current_or_future"),
            "evidence_refs": _safe_json(connection.get("evidence_refs", [])),
        }

    def _dispatch(
        self,
        questions: Mapping[str, object],
        branch: str,
        request: Mapping[str, object],
    ) -> dict[str, object]:
        try:
            task_packet = request.get("task_packet")
            if not isinstance(task_packet, Mapping):
                raise TypeError("planning request has no task packet")
            if self.router is not None:
                task = DecisionTask(
                    task_id=f"planning-judgment-{_digest(request)[:24]}",
                    required_capabilities=self._router_requirements(task_packet),
                    branch_id=branch,
                    input_state=task_packet,
                    state=task_packet,
                    questions=questions,
                    evidence=tuple(
                        item
                        for item in task_packet.get("source_evidence", {}).get("directed_edges", [])
                        if isinstance(item, Mapping)
                    ),
                    candidates=tuple(
                        item
                        for item in task_packet.get("candidates", [])
                        if isinstance(item, Mapping)
                    ),
                    policy=task_packet.get("policy"),
                    allowed_operations=tuple(
                        str(item)
                        for item in task_packet.get("scope", {}).get("permitted_action_kinds", [])
                    ),
                )
                routed = self.router.route(task)
                if routed.status is RoutingStatus.JEV_JUDGMENT and isinstance(
                    routed.result, Mapping
                ):
                    result = dict(_safe_json(routed.result))
                    result["decision_class"] = "classifier"
                    result.setdefault("provider", routed.capability_id or "jev")
                    result["capability_id"] = routed.capability_id
                    result["routing"] = routed.as_dict()
                    return result
                if routed.status is RoutingStatus.SPECIALIST_PROPOSAL:
                    result = dict(routed.result) if isinstance(routed.result, Mapping) else {}
                    result["status"] = "answered"
                    result.setdefault("provider", routed.capability_id or "router")
                    result["proposal"] = _safe_json(routed.proposal)
                    result["capability_id"] = routed.capability_id
                    result["routing"] = routed.as_dict()
                    result["decision_class"] = "agent"
                    return result
                if routed.status in {
                    RoutingStatus.NO_CAPABLE_PROVIDER,
                    RoutingStatus.UNAVAILABLE,
                }:
                    return {
                        "status": "unavailable",
                        "provider": routed.capability_id or "router",
                        "failure_class": routed.reason or routed.status.value,
                        "routing": routed.as_dict(),
                    }
                result = {"status": routed.status.value, **routed.as_dict()}
                if isinstance(routed.result, Mapping):
                    for key in (
                        "provider",
                        "model",
                        "usage",
                        "request",
                        "response",
                        "request_receipt",
                        "response_receipt",
                        "receipt",
                    ):
                        if key in routed.result:
                            result[key] = _safe_json(routed.result[key])
                capability_class = self._router_decision_class(routed.capability_id)
                if capability_class is not None:
                    result["decision_class"] = capability_class
                return result
            provider = self.provider or TypeSafeClient()
            if hasattr(provider, "judge"):
                result = provider.judge(task_packet, questions)  # type: ignore[union-attr]
            elif callable(provider):
                result = cast(ProviderFunction, provider)(task_packet, questions)
            else:
                raise TypeError("provider must expose judge(state, questions)")
            if not isinstance(result, Mapping):
                return {"status": "invalid", "violations": ["provider result must be an object"]}
            dispatched = dict(_safe_json(result))
            dispatched["decision_class"] = "classifier"
            return dispatched
        except Exception:
            return {
                "status": "servicefailed",
                "provider": (
                    type(self.provider).__name__ if self.provider is not None else "typesafe"
                ),
                "failure_class": "adapter-exception",
            }

    def _router_decision_class(self, capability_id: str | None) -> DecisionClass | None:
        if self.router is None or capability_id is None:
            return None
        for capability in self.router.capabilities:
            if capability.capability_id != capability_id:
                continue
            if capability.kind is CapabilityKind.CODE:
                return "mechanical"
            if capability.kind is CapabilityKind.JEV:
                return "classifier"
            if capability.kind in {
                CapabilityKind.SPECIALIST,
                CapabilityKind.DOMAIN_SPECIALIST,
            }:
                return "agent"
            return None
        return None

    def _router_requirements(
        self, task_packet: Mapping[str, object] | None = None
    ) -> CapabilityRequirements:
        """Select a configured proposal contract without inventing authority.

        A sole configured specialist can own this task.  When multiple
        capabilities are available, the router's explicit preference is
        required before using a specialist; otherwise the ordinary Choice
        contract remains the only eligible judgment.
        """

        if self.router is None:
            return CapabilityRequirements(judgment_forms=("choice",))
        feedback_unknowns = (
            task_packet.get("feedback_unknowns") if isinstance(task_packet, Mapping) else None
        )
        unresolved_focused_task = isinstance(feedback_unknowns, list) and bool(feedback_unknowns)
        configured = [
            capability
            for capability in self.router.capabilities
            if capability.enabled and capability.configured
        ]
        specialists = [
            capability
            for capability in configured
            if capability.kind in {CapabilityKind.SPECIALIST, CapabilityKind.DOMAIN_SPECIALIST}
            and "structured-proposal" in capability.judgment_forms
        ]
        declared_specialists = [
            capability
            for capability in self.router.capabilities
            if capability.enabled
            and capability.kind in {CapabilityKind.SPECIALIST, CapabilityKind.DOMAIN_SPECIALIST}
            and "structured-proposal" in capability.judgment_forms
        ]
        preferred_ids = {
            preference.capability_id
            for preference in self.router.configuration.explicit_preferences
        }
        selected = [
            capability for capability in specialists if capability.capability_id in preferred_ids
        ]
        if unresolved_focused_task and declared_specialists:
            return CapabilityRequirements(
                judgment_forms=("structured-proposal",),
                exactness="proposal-then-validate",
            )
        if selected or (len(configured) == 1 and len(specialists) == 1):
            return CapabilityRequirements(
                judgment_forms=("structured-proposal",),
                exactness="proposal-then-validate",
            )
        return CapabilityRequirements(judgment_forms=("choice",))

    @staticmethod
    def _invalid_output(output: Mapping[str, object], reason: object) -> dict[str, object]:
        invalid = dict(output)
        validation = (
            dict(invalid.get("validation", {}))
            if isinstance(invalid.get("validation"), Mapping)
            else {}
        )
        diagnostics = list(validation.get("diagnostics", []))
        diagnostics.append(reason)
        validation["diagnostics"] = diagnostics
        invalid["validation"] = validation
        invalid["status"] = "invalid"
        return invalid

    @staticmethod
    def _provider_failure_output(
        output: Mapping[str, object], result: Mapping[str, object]
    ) -> dict[str, object]:
        failed = dict(output)
        validation = (
            dict(failed.get("validation", {}))
            if isinstance(failed.get("validation"), Mapping)
            else {}
        )
        diagnostics = list(validation.get("diagnostics", []))
        diagnostics.append(
            {
                "code": f"provider-{_status(result.get('status')) or 'failed'}",
                "message": str(result.get("failure_class", "provider did not answer")),
            }
        )
        validation["diagnostics"] = diagnostics
        failed["validation"] = validation
        failed["provider_status"] = result.get("status")
        return failed


__all__ = ["PlanningRunResult", "PlanningRuntime", "RunMode"]
