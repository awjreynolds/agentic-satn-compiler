"""Static capability selection around deterministic planning boundaries.

The router only chooses an explicitly registered capability and dispatches an
injected adapter.  It does not discover providers, invent policy, validate
geometry, or turn model prose into an executable operation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, cast


class CapabilityKind(StrEnum):
    CODE = "code"
    JEV = "jev"
    SPECIALIST = "specialist"
    DOMAIN_SPECIALIST = "domain-specialist"
    RETRIEVAL = "retrieval"
    DOMAIN = "domain-specialist"


class RoutingStatus(StrEnum):
    CODE_COMPLETED = "code-completed"
    JEV_JUDGMENT = "jev-judgment"
    SPECIALIST_PROPOSAL = "specialist-proposal"
    RETRIEVAL_COMPLETED = "retrieval-completed"
    EVIDENCE_EXPANSION_REQUESTED = "evidence-expansion-requested"
    CANDIDATE_EXPANSION_REQUESTED = "candidate-expansion-requested"
    UNKNOWN = "unknown"
    OWNER_POLICY_UNRESOLVED = "owner-policy-unresolved"
    INVALID_TASK = "invalid-task"
    ROUTING_CONFIGURATION_UNRESOLVED = "routing-configuration-unresolved"
    INVALID_PROVIDER_RESPONSE = "invalid-provider-response"
    SERVICEFAILED = "servicefailed"
    UNAVAILABLE = "unavailable"
    NO_CAPABLE_PROVIDER = "no-capable-provider"


def _tuple(values: Sequence[str] | None, field_name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    result = tuple(values)
    if any(not isinstance(value, str) or not value.strip() for value in result):
        raise ValueError(f"{field_name} must contain non-blank strings")
    if len(set(result)) != len(result):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class CapabilityRequirements:
    """Task-owned capability contract and scope ceiling."""

    operations: tuple[str, ...] = ()
    judgment_forms: tuple[str, ...] = ()
    exactness: Literal[
        "exact-code-required", "typed-judgment-sufficient", "proposal-then-validate"
    ] = "typed-judgment-sufficient"
    needs_retrieval: bool = False
    needs_candidate_generation: bool = False
    needs_decomposition: bool = False
    domain: str | None = None
    permitted_operation_scopes: tuple[str, ...] = ()
    permitted_authority_scopes: tuple[str, ...] = ()
    required_provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "operations", _tuple(self.operations, "operations"))
        object.__setattr__(self, "judgment_forms", _tuple(self.judgment_forms, "judgment_forms"))
        object.__setattr__(
            self,
            "permitted_operation_scopes",
            _tuple(self.permitted_operation_scopes, "permitted_operation_scopes"),
        )
        object.__setattr__(
            self,
            "permitted_authority_scopes",
            _tuple(self.permitted_authority_scopes, "permitted_authority_scopes"),
        )
        object.__setattr__(
            self,
            "required_provenance",
            _tuple(self.required_provenance, "required_provenance"),
        )
        if self.domain is not None and (
            not isinstance(self.domain, str) or not self.domain.strip()
        ):
            raise ValueError("domain must be blank or a non-blank identifier")


@dataclass(frozen=True, slots=True)
class DecisionTask:
    """Minimal routing envelope; operation validation remains downstream."""

    task_id: str
    required_capabilities: CapabilityRequirements
    parent_event_id: str | None = None
    stage_id: str | None = None
    pass_id: str | None = None
    branch_id: str | None = None
    input_state: object | None = None
    state: object | None = None
    questions: Mapping[str, object] | None = None
    evidence: tuple[object, ...] = ()
    candidates: tuple[object, ...] = ()
    policy: object | None = None
    policy_requirement: Literal["none", "required-for-this-task"] = "none"
    allowed_operations: tuple[str, ...] = ()
    allowed_authority_scopes: tuple[str, ...] = ()
    retrieval_scope: object | None = None
    domain_context: object | None = None
    state_admitted: bool = True
    evidence_admitted: bool = True
    operation_schema_admitted: bool = True
    domain_context_admitted: bool = True
    admission_violations: tuple[str, ...] = ()
    replay_mode: Literal["recorded", "fresh"] = "fresh"
    recorded_receipt: object | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-blank identifier")
        if not isinstance(self.required_capabilities, CapabilityRequirements):
            raise TypeError("required_capabilities must be CapabilityRequirements")
        object.__setattr__(
            self,
            "allowed_operations",
            _tuple(self.allowed_operations, "allowed_operations"),
        )
        object.__setattr__(
            self,
            "allowed_authority_scopes",
            _tuple(self.allowed_authority_scopes, "allowed_authority_scopes"),
        )
        object.__setattr__(
            self,
            "admission_violations",
            _tuple(self.admission_violations, "admission_violations"),
        )


Adapter = Callable[..., object]


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    """One statically configured capability and its optional injected adapter."""

    capability_id: str
    kind: CapabilityKind | str
    accepts: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    judgment_forms: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    operation_scopes: tuple[str, ...] = ()
    authority_scopes: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    provider: str | None = None
    implementation_version: str = "1"
    enabled: bool = True
    adapter: object | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.capability_id, str) or not self.capability_id.strip():
            raise ValueError("capability_id must be a non-blank identifier")
        object.__setattr__(self, "kind", CapabilityKind(self.kind))
        for name in (
            "accepts",
            "produces",
            "operations",
            "judgment_forms",
            "domains",
            "operation_scopes",
            "authority_scopes",
            "provenance",
        ):
            object.__setattr__(self, name, _tuple(getattr(self, name), name))
        if (
            not isinstance(self.implementation_version, str)
            or not self.implementation_version.strip()
        ):
            raise ValueError("implementation_version must be non-blank")

    @property
    def configured(self) -> bool:
        if self.kind is CapabilityKind.CODE:
            return True
        return bool(self.provider and self.provider.strip() and self.adapter is not None)


@dataclass(frozen=True, slots=True)
class RoutingPreference:
    """Explicit code-owned preference for one otherwise eligible capability."""

    capability_id: str
    requirement_profile: CapabilityRequirements | None = None
    operation: str | None = None
    domain: str | None = None

    def matches(self, task: DecisionTask) -> bool:
        if (
            self.requirement_profile is not None
            and self.requirement_profile != task.required_capabilities
        ):
            return False
        if (
            self.operation is not None
            and self.operation not in task.required_capabilities.operations
        ):
            return False
        return self.domain is None or self.domain == task.required_capabilities.domain


@dataclass(frozen=True, slots=True)
class RoutingConfiguration:
    """Static, code-owned preferences for ambiguous eligible sets."""

    configuration_version: str = "1"
    explicit_preferences: tuple[RoutingPreference, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.configuration_version, str)
            or not self.configuration_version.strip()
        ):
            raise ValueError("configuration_version must be non-blank")
        object.__setattr__(self, "explicit_preferences", tuple(self.explicit_preferences))


@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    """Typed JSON-compatible route result shared by the concrete outcome names."""

    status: RoutingStatus
    capability_id: str | None = None
    operation: str | None = None
    result: object | None = None
    judgment: object | None = None
    raw_distribution: object | None = None
    proposal: object | None = None
    evidence_refs: tuple[str, ...] = ()
    items: tuple[object, ...] = ()
    admission: str | None = None
    request: object | None = None
    cause: str | None = None
    missing_refs: tuple[str, ...] = ()
    next_task_id: str | None = None
    eligible_capabilities: tuple[str, ...] = ()
    rejected_capabilities: tuple[tuple[str, tuple[str, ...]], ...] = ()
    requirement_profile: object | None = None
    reason: str | None = None
    violations: tuple[str, ...] = ()
    provider_request: object | None = None
    failure_class: str | None = None
    retry_or_review: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            key: _json_value(value)
            for key, value in {
                "status": self.status.value,
                "capability_id": self.capability_id,
                "operation": self.operation,
                "result": self.result,
                "judgment": self.judgment,
                "raw_distribution": self.raw_distribution,
                "proposal": self.proposal,
                "evidence_refs": self.evidence_refs,
                "items": self.items,
                "admission": self.admission,
                "request": self.request,
                "cause": self.cause,
                "missing_refs": self.missing_refs,
                "next_task_id": self.next_task_id,
                "eligible_capabilities": self.eligible_capabilities,
                "rejected_capabilities": self.rejected_capabilities,
                "requirement_profile": self.requirement_profile,
                "reason": self.reason,
                "violations": self.violations,
                "provider_request": self.provider_request,
                "failure_class": self.failure_class,
                "retry_or_review": self.retry_or_review,
            }.items()
            if value is not None and value != ()
        }


def _json_value(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, CapabilityRequirements):
        return {
            "operations": value.operations,
            "judgment_forms": value.judgment_forms,
            "exactness": value.exactness,
            "needs_retrieval": value.needs_retrieval,
            "needs_candidate_generation": value.needs_candidate_generation,
            "needs_decomposition": value.needs_decomposition,
            "domain": value.domain,
            "permitted_operation_scopes": value.permitted_operation_scopes,
            "permitted_authority_scopes": value.permitted_authority_scopes,
            "required_provenance": value.required_provenance,
        }
    if hasattr(value, "as_dict"):
        return _json_value(value.as_dict())  # type: ignore[union-attr]
    return value


def _strip_model_explanation(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_model_explanation(item)
            for key, item in value.items()
            if str(key).lower() not in {"rationale", "reasoning", "explanation"}
        }
    if isinstance(value, (tuple, list)):
        return tuple(_strip_model_explanation(item) for item in value)
    return value


class StaticCapabilityRouter:
    """Select and dispatch one capability from a static tuple."""

    def __init__(
        self,
        capabilities: Sequence[CapabilityRecord],
        configuration: RoutingConfiguration | None = None,
    ) -> None:
        self.capabilities = tuple(capabilities)
        if len({capability.capability_id for capability in self.capabilities}) != len(
            self.capabilities
        ):
            raise ValueError("capability IDs must be unique")
        self.configuration = configuration or RoutingConfiguration()
        self._by_id = {capability.capability_id: capability for capability in self.capabilities}

    def route(self, task: DecisionTask) -> RoutingOutcome:
        invalid = self._validate_task(task)
        if invalid:
            return RoutingOutcome(status=RoutingStatus.INVALID_TASK, violations=tuple(invalid))
        if task.policy_requirement == "required-for-this-task" and task.policy is None:
            return RoutingOutcome(
                status=RoutingStatus.OWNER_POLICY_UNRESOLVED,
                reason="required policy is absent",
                missing_refs=("policy",),
            )
        eligible, rejected = self._eligible(task)
        eligible_ids = tuple(sorted(capability.capability_id for capability in eligible))
        if not eligible:
            return RoutingOutcome(
                status=RoutingStatus.NO_CAPABLE_PROVIDER,
                eligible_capabilities=(),
                rejected_capabilities=tuple(sorted(rejected)),
                requirement_profile=task.required_capabilities,
            )
        selection = self._select(task, eligible, eligible_ids)
        if isinstance(selection, RoutingOutcome):
            return selection
        capability = selection
        recheck = self._capability_rejection(task, capability)
        if recheck:
            return RoutingOutcome(
                status=RoutingStatus.INVALID_PROVIDER_RESPONSE,
                capability_id=capability.capability_id,
                violations=tuple(recheck),
            )
        if task.replay_mode == "recorded":
            return self._outcome(capability, task, task.recorded_receipt)
        try:
            response = self._dispatch(capability, task)
        except Exception:
            return RoutingOutcome(
                status=RoutingStatus.SERVICEFAILED,
                capability_id=capability.capability_id,
                failure_class="adapter-exception",
                retry_or_review="review-or-retry-under-owner-policy",
            )
        return self._outcome(capability, task, response)

    @staticmethod
    def _validate_task(task: DecisionTask) -> list[str]:
        if not isinstance(task, DecisionTask):
            return ["task:invalid-type"]
        requirements = task.required_capabilities
        violations = list(task.admission_violations)
        if task.policy_requirement not in {"none", "required-for-this-task"}:
            violations.append("policy_requirement")
        if task.replay_mode not in {"recorded", "fresh"}:
            violations.append("replay_mode")
        elif task.replay_mode == "recorded" and task.recorded_receipt is None:
            violations.append("recorded-receipt-required")
        if not task.state_admitted:
            violations.append("input_state:not-admitted")
        if not task.evidence_admitted:
            violations.append("evidence:not-admitted")
        if not task.operation_schema_admitted:
            violations.append("allowed_operations:not-admitted")
        if requirements.domain and not task.domain_context_admitted:
            violations.append("domain_context:not-admitted")
        if (
            requirements.operations
            and task.allowed_operations
            and not set(requirements.operations).issubset(task.allowed_operations)
        ):
            violations.append("required operations exceed allowed_operations")
        if (
            requirements.permitted_authority_scopes
            and task.allowed_authority_scopes
            and not set(requirements.permitted_authority_scopes).issubset(
                task.allowed_authority_scopes
            )
        ):
            violations.append("required authority scopes exceed allowed_authority_scopes")
        return sorted(set(violations))

    def _capability_rejection(self, task: DecisionTask, capability: CapabilityRecord) -> list[str]:
        requirements = task.required_capabilities
        if not capability.enabled:
            return ["disabled"]
        if (
            requirements.exactness == "exact-code-required"
            and capability.kind is not CapabilityKind.CODE
        ):
            return ["exact-code-required"]
        if requirements.needs_retrieval and capability.kind is not CapabilityKind.RETRIEVAL:
            return ["retrieval-capability-required"]
        if not requirements.needs_retrieval and capability.kind is CapabilityKind.RETRIEVAL:
            return ["retrieval-capability-not-requested"]
        if requirements.needs_candidate_generation and capability.kind not in {
            CapabilityKind.SPECIALIST,
            CapabilityKind.DOMAIN_SPECIALIST,
        }:
            return ["candidate-generation-capability-required"]
        if requirements.needs_decomposition and capability.kind not in {
            CapabilityKind.SPECIALIST,
            CapabilityKind.DOMAIN_SPECIALIST,
        }:
            return ["decomposition-capability-required"]
        if requirements.exactness == "proposal-then-validate" and capability.kind not in {
            CapabilityKind.SPECIALIST,
            CapabilityKind.DOMAIN_SPECIALIST,
        }:
            return ["proposal-capability-required"]
        if requirements.operations and not set(requirements.operations).issubset(
            capability.operations
        ):
            return ["operation-coverage"]
        if requirements.judgment_forms and not set(requirements.judgment_forms).issubset(
            capability.judgment_forms
        ):
            return ["judgment-form-coverage"]
        if requirements.domain and requirements.domain not in capability.domains:
            return ["domain-coverage"]
        if requirements.permitted_operation_scopes and not set(
            requirements.permitted_operation_scopes
        ).issubset(capability.operation_scopes):
            return ["operation-scope-coverage"]
        if requirements.permitted_authority_scopes and not set(
            requirements.permitted_authority_scopes
        ).issubset(capability.authority_scopes):
            return ["authority-scope-coverage"]
        if requirements.required_provenance and not set(requirements.required_provenance).issubset(
            capability.provenance
        ):
            return ["provenance-coverage"]
        if not capability.configured:
            return ["provider-not-configured"]
        return []

    def _eligible(
        self,
        task: DecisionTask,
    ) -> tuple[list[CapabilityRecord], list[tuple[str, tuple[str, ...]]]]:
        eligible: list[CapabilityRecord] = []
        rejected: list[tuple[str, tuple[str, ...]]] = []
        for capability in self.capabilities:
            reasons = self._capability_rejection(task, capability)
            if reasons:
                rejected.append((capability.capability_id, tuple(reasons)))
            else:
                eligible.append(capability)
        return eligible, rejected

    def _select(
        self,
        task: DecisionTask,
        eligible: Sequence[CapabilityRecord],
        eligible_ids: tuple[str, ...],
    ) -> CapabilityRecord | RoutingOutcome:
        if len(eligible) == 1:
            return eligible[0]
        matching = [
            preference
            for preference in self.configuration.explicit_preferences
            if preference.matches(task)
        ]
        if matching:
            matching_ids = [preference.capability_id for preference in matching]
            if len(matching) != 1 or matching_ids[0] not in eligible_ids:
                return RoutingOutcome(
                    status=RoutingStatus.ROUTING_CONFIGURATION_UNRESOLVED,
                    eligible_capabilities=eligible_ids,
                    requirement_profile=task.required_capabilities,
                    reason="explicit preference is missing, invalid, or ambiguous",
                )
            return self._by_id[matching_ids[0]]
        return RoutingOutcome(
            status=RoutingStatus.ROUTING_CONFIGURATION_UNRESOLVED,
            eligible_capabilities=eligible_ids,
            requirement_profile=task.required_capabilities,
            reason="multiple eligible capabilities have no explicit preference",
        )

    @staticmethod
    def _dispatch(capability: CapabilityRecord, task: DecisionTask) -> object:
        adapter = capability.adapter
        if adapter is None:
            return None
        method_name = {
            CapabilityKind.SPECIALIST: "propose",
            CapabilityKind.DOMAIN_SPECIALIST: "propose",
            CapabilityKind.RETRIEVAL: "retrieve",
        }.get(capability.kind)
        if method_name and hasattr(adapter, method_name):
            return getattr(adapter, method_name)(task)
        if capability.kind is CapabilityKind.JEV and hasattr(adapter, "judge"):
            if task.questions is not None:
                return adapter.judge(  # type: ignore[union-attr]
                    task.state if task.state is not None else task.input_state,
                    task.questions,
                )
            return adapter.judge(task)
        if callable(adapter):
            return cast(Callable[[DecisionTask], object], adapter)(task)
        raise TypeError("capability adapter is not callable")

    def _outcome(
        self,
        capability: CapabilityRecord,
        task: DecisionTask,
        response: object,
    ) -> RoutingOutcome:
        scope_violation = self._response_scope_violation(task, response)
        if scope_violation:
            return RoutingOutcome(
                status=RoutingStatus.INVALID_PROVIDER_RESPONSE,
                capability_id=capability.capability_id,
                violations=tuple(scope_violation),
            )
        if capability.kind is CapabilityKind.CODE:
            return RoutingOutcome(
                status=RoutingStatus.CODE_COMPLETED,
                capability_id=capability.capability_id,
                result=response,
            )
        if not isinstance(response, Mapping):
            return RoutingOutcome(
                status=RoutingStatus.INVALID_PROVIDER_RESPONSE,
                capability_id=capability.capability_id,
                violations=("response:object",),
            )
        provider_status = response.get("status")
        if provider_status == "unavailable":
            return RoutingOutcome(
                status=RoutingStatus.UNAVAILABLE,
                capability_id=capability.capability_id,
                failure_class=str(response.get("failure_class", "provider-unavailable")),
            )
        if provider_status == "servicefailed":
            return RoutingOutcome(
                status=RoutingStatus.SERVICEFAILED,
                capability_id=capability.capability_id,
                provider_request=response.get("request"),
                failure_class=str(response.get("failure_class", "provider-service-failed")),
                retry_or_review="review-or-retry-under-owner-policy",
            )
        if provider_status == "invalid":
            return RoutingOutcome(
                status=RoutingStatus.INVALID_PROVIDER_RESPONSE,
                capability_id=capability.capability_id,
                provider_request=response.get("request"),
                violations=tuple(str(item) for item in response.get("violations", ())),
            )
        if provider_status == "unknown":
            return RoutingOutcome(
                status=RoutingStatus.UNKNOWN,
                capability_id=capability.capability_id,
                result=response,
                cause=str(response.get("cause", "provider-unknown")),
                missing_refs=tuple(str(item) for item in response.get("missing_refs", ())),
            )
        if capability.kind is CapabilityKind.JEV:
            if provider_status not in {None, "answered"}:
                return RoutingOutcome(
                    status=RoutingStatus.INVALID_PROVIDER_RESPONSE,
                    capability_id=capability.capability_id,
                    violations=("response.status",),
                )
            answers = response.get("answers")
            if not isinstance(answers, Mapping):
                return RoutingOutcome(
                    status=RoutingStatus.INVALID_PROVIDER_RESPONSE,
                    capability_id=capability.capability_id,
                    violations=("response.answers",),
                )
            distributions = {
                str(question_id): _strip_model_explanation(
                    answer.get("probabilities", answer.get("noul"))
                )
                for question_id, answer in answers.items()
                if isinstance(answer, Mapping)
            }
            return RoutingOutcome(
                status=RoutingStatus.JEV_JUDGMENT,
                capability_id=capability.capability_id,
                result=response,
                judgment=_strip_model_explanation(answers),
                raw_distribution=distributions,
            )
        if capability.kind is CapabilityKind.RETRIEVAL:
            items = response.get("items")
            if not isinstance(items, (list, tuple)):
                return RoutingOutcome(
                    status=RoutingStatus.INVALID_PROVIDER_RESPONSE,
                    capability_id=capability.capability_id,
                    violations=("response.items",),
                )
            return RoutingOutcome(
                status=RoutingStatus.RETRIEVAL_COMPLETED,
                capability_id=capability.capability_id,
                result=response,
                items=tuple(_strip_model_explanation(item) for item in items),
                admission="unadmitted",
            )
        proposal = response.get("proposal", response)
        return RoutingOutcome(
            status=RoutingStatus.SPECIALIST_PROPOSAL,
            capability_id=capability.capability_id,
            result=response,
            proposal=_strip_model_explanation(proposal),
            evidence_refs=tuple(str(item) for item in response.get("evidence_refs", ())),
            operation=(
                response.get("operation") if isinstance(response.get("operation"), str) else None
            ),
        )

    @staticmethod
    def _response_scope_violation(task: DecisionTask, response: object) -> list[str]:
        violations: list[str] = []
        allowed_operation_scopes = set(task.required_capabilities.permitted_operation_scopes)
        allowed_authority_scopes = set(task.required_capabilities.permitted_authority_scopes)

        def inspect(envelope: Mapping[object, object], prefix: str) -> None:
            operation = envelope.get("operation")
            if (
                isinstance(operation, str)
                and task.allowed_operations
                and operation not in task.allowed_operations
            ):
                violations.append(f"{prefix}operation")
            for key, allowed in (
                ("operation_scope", allowed_operation_scopes),
                ("authority_scope", allowed_authority_scopes),
            ):
                value = envelope.get(key)
                if isinstance(value, str) and allowed and value not in allowed:
                    violations.append(f"{prefix}{key}")
            for key, allowed in (
                ("operation_scopes", allowed_operation_scopes),
                ("authority_scopes", allowed_authority_scopes),
            ):
                value = envelope.get(key)
                if (
                    isinstance(value, (list, tuple, set))
                    and allowed
                    and not set(value).issubset(allowed)
                ):
                    violations.append(f"{prefix}{key}")
            for nested_key in ("proposal", "operation", "result", "envelope"):
                nested = envelope.get(nested_key)
                if isinstance(nested, Mapping):
                    inspect(nested, f"{prefix}{nested_key}.")

        if isinstance(response, Mapping):
            inspect(response, "")
        return violations


def route_task(
    task: DecisionTask,
    capabilities: Sequence[CapabilityRecord],
    configuration: RoutingConfiguration | None = None,
) -> RoutingOutcome:
    """Convenience function for callers that do not need a retained router."""

    return StaticCapabilityRouter(capabilities, configuration).route(task)


__all__ = [
    "CapabilityKind",
    "CapabilityRecord",
    "CapabilityRequirements",
    "DecisionTask",
    "RoutingConfiguration",
    "RoutingOutcome",
    "RoutingPreference",
    "RoutingStatus",
    "StaticCapabilityRouter",
    "route_task",
]
