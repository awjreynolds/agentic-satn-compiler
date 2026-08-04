"""Bounded AI assistance for already-governed review decisions.

The model-facing response contains identifiers only.  Human-readable statements,
investigations and actions are compiler-authored packet data, so a runtime cannot add
geometry, facts, measurements, thresholds, scores or candidate options.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

ResponseType = Literal[
    "comparison-explanation",
    "evidence-investigation-request",
    "cited-inconsistency",
    "offered-action",
]
RuntimeState = Literal["accepted", "timed-out", "unavailable"]
ValidationOutcome = Literal[
    "valid-cited-response",
    "invalid-response-schema",
    "invalid-citation",
    "insufficient-comparison-evidence",
    "unoffered-investigation",
    "unoffered-action",
    "action-not-delegated",
    "runtime-timed-out",
    "runtime-unavailable",
    "runtime-error",
    "invalid-runtime-invocation",
]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def _identifier(value: object, name: str) -> str:
    value = _required_text(value, name)
    if _ID.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical identifier")
    return value


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase full SHA-256")
    return value


def _unique_ids(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    canonical = tuple(sorted(_identifier(value, name) for value in values))
    if len(set(canonical)) != len(canonical):
        raise ValueError(f"{name} must be unique")
    return canonical


@dataclass(frozen=True)
class ReviewEvidence:
    evidence_id: str
    dimension: str
    statement: str
    evidence_fingerprint: str

    def __post_init__(self) -> None:
        _identifier(self.evidence_id, "evidence id")
        _identifier(self.dimension, "evidence dimension")
        _required_text(self.statement, "evidence statement")
        _sha256(self.evidence_fingerprint, "evidence fingerprint")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "dimension": self.dimension,
            "statement": self.statement,
            "evidence_fingerprint": self.evidence_fingerprint,
        }


@dataclass(frozen=True)
class ReviewInvestigation:
    investigation_id: str
    label: str

    def __post_init__(self) -> None:
        _identifier(self.investigation_id, "investigation id")
        _required_text(self.label, "investigation label")

    def canonical_payload(self) -> dict[str, object]:
        return {"investigation_id": self.investigation_id, "label": self.label}


@dataclass(frozen=True)
class ReviewAction:
    action_id: str
    label: str

    def __post_init__(self) -> None:
        _identifier(self.action_id, "action id")
        _required_text(self.label, "action label")

    def canonical_payload(self) -> dict[str, object]:
        return {"action_id": self.action_id, "label": self.label}


@dataclass(frozen=True)
class ReviewAssistantResponse:
    response_type: ResponseType
    citation_ids: tuple[str, ...]
    investigation_id: str | None = None
    action_id: str | None = None

    def __post_init__(self) -> None:
        if self.response_type not in {
            "comparison-explanation",
            "evidence-investigation-request",
            "cited-inconsistency",
            "offered-action",
        }:
            raise ValueError("response type is outside the closed review vocabulary")
        citations = _unique_ids(self.citation_ids, "citation id")
        if not citations:
            raise ValueError("review responses require at least one exact citation")
        object.__setattr__(self, "citation_ids", citations)
        if self.investigation_id is not None:
            _identifier(self.investigation_id, "investigation id")
        if self.action_id is not None:
            _identifier(self.action_id, "action id")
        if self.response_type == "evidence-investigation-request":
            if self.investigation_id is None or self.action_id is not None:
                raise ValueError("investigation response requires only one investigation id")
        elif self.response_type == "offered-action":
            if self.action_id is None or self.investigation_id is not None:
                raise ValueError("action response requires only one action id")
        elif self.investigation_id is not None or self.action_id is not None:
            raise ValueError("cited explanations cannot carry an action or investigation")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "response_type": self.response_type,
            "citation_ids": self.citation_ids,
            "investigation_id": self.investigation_id,
            "action_id": self.action_id,
        }

    @classmethod
    def from_runtime_payload(cls, payload: object) -> ReviewAssistantResponse:
        if not isinstance(payload, Mapping):
            raise ValueError("runtime response must be one strict object")
        required = {
            "response_type",
            "citation_ids",
            "investigation_id",
            "action_id",
        }
        if set(payload) != required:
            raise ValueError("runtime response fields do not match the closed schema")
        citations = payload["citation_ids"]
        if not isinstance(citations, (list, tuple)) or any(
            not isinstance(item, str) for item in citations
        ):
            raise ValueError("runtime citations must be a finite identifier list")
        return cls(
            response_type=payload["response_type"],  # type: ignore[arg-type]
            citation_ids=tuple(citations),
            investigation_id=payload["investigation_id"],  # type: ignore[arg-type]
            action_id=payload["action_id"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class DeterministicReviewFallback:
    response: ReviewAssistantResponse
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.response, ReviewAssistantResponse):
            raise ValueError("fallback response must use the closed response contract")
        _identifier(self.reason, "fallback reason")

    def canonical_payload(self) -> dict[str, object]:
        return {"response": self.response.canonical_payload(), "reason": self.reason}


@dataclass(frozen=True)
class ReviewDecisionPacket:
    packet_id: str
    candidate_ids: tuple[str, ...]
    evidence: tuple[ReviewEvidence, ...]
    investigations: tuple[ReviewInvestigation, ...]
    offered_actions: tuple[ReviewAction, ...]
    actions_delegated: bool
    fallback: DeterministicReviewFallback

    def __post_init__(self) -> None:
        _identifier(self.packet_id, "packet id")
        candidates = _unique_ids(self.candidate_ids, "candidate id")
        if not candidates:
            raise ValueError("review packet requires a finite candidate set")
        object.__setattr__(self, "candidate_ids", candidates)
        evidence = tuple(sorted(self.evidence, key=lambda item: item.evidence_id))
        investigations = tuple(sorted(self.investigations, key=lambda item: item.investigation_id))
        actions = tuple(sorted(self.offered_actions, key=lambda item: item.action_id))
        if len({item.evidence_id for item in evidence}) != len(evidence):
            raise ValueError("evidence ids must be unique")
        if len({item.investigation_id for item in investigations}) != len(investigations):
            raise ValueError("investigation ids must be unique")
        if len({item.action_id for item in actions}) != len(actions):
            raise ValueError("action ids must be unique")
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "investigations", investigations)
        object.__setattr__(self, "offered_actions", actions)
        if not isinstance(self.actions_delegated, bool):
            raise ValueError("actions_delegated must be boolean")
        outcome = _validate_response(self, self.fallback.response)
        if outcome != "valid-cited-response":
            raise ValueError(f"fallback response is not bound to packet: {outcome}")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": "satn-review-decision-packet/v1",
            "packet_id": self.packet_id,
            "candidate_ids": self.candidate_ids,
            "evidence": [item.canonical_payload() for item in self.evidence],
            "investigations": [item.canonical_payload() for item in self.investigations],
            "offered_actions": [item.canonical_payload() for item in self.offered_actions],
            "actions_delegated": self.actions_delegated,
            "fallback": self.fallback.canonical_payload(),
        }

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.canonical_payload())


@dataclass(frozen=True)
class RuntimeDescriptor:
    provider_id: str
    model_id: str
    model_fingerprint: str
    runtime_fingerprint: str

    def __post_init__(self) -> None:
        _identifier(self.provider_id, "runtime provider id")
        _required_text(self.model_id, "model id")
        _sha256(self.model_fingerprint, "model fingerprint")
        _sha256(self.runtime_fingerprint, "runtime fingerprint")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "model_fingerprint": self.model_fingerprint,
            "runtime_fingerprint": self.runtime_fingerprint,
        }


@dataclass(frozen=True)
class RuntimeInvocation:
    state: RuntimeState
    response_payload: object | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.state not in {"accepted", "timed-out", "unavailable"}:
            raise ValueError("runtime state is outside the closed vocabulary")
        if self.state == "accepted":
            if self.response_payload is None or self.detail is not None:
                raise ValueError("accepted invocation requires only a response payload")
        else:
            if self.response_payload is not None or self.detail is None:
                raise ValueError("failed invocation requires only a typed detail")
            _required_text(self.detail, "runtime detail")


@runtime_checkable
class ReviewAssistantRuntime(Protocol):
    descriptor: RuntimeDescriptor

    def invoke(self, prompt: dict[str, object]) -> RuntimeInvocation: ...


@dataclass(frozen=True)
class ReviewAssistanceRecord:
    disposition: Literal["accepted", "fallback"]
    validation_outcome: ValidationOutcome
    response: ReviewAssistantResponse
    used_fallback: bool
    candidate_ids: tuple[str, ...]
    fallback_reason: str | None
    packet_fingerprint: str
    prompt_fingerprint: str
    schema_fingerprint: str
    provider_id: str
    model_id: str
    model_fingerprint: str
    runtime_fingerprint: str
    runtime_state: str
    result_fingerprint: str


_ALLOWED_RESPONSE_SCHEMA = {
    "contract": "satn-review-assistant-response/v1",
    "additional_properties": False,
    "fields": {
        "response_type": (
            "comparison-explanation",
            "evidence-investigation-request",
            "cited-inconsistency",
            "offered-action",
        ),
        "citation_ids": "packet-evidence-id[]",
        "investigation_id": "packet-investigation-id|null",
        "action_id": "packet-action-id|null",
    },
}
_SCHEMA_FINGERPRINT = _fingerprint(_ALLOWED_RESPONSE_SCHEMA)


def assist_review(
    packet: ReviewDecisionPacket,
    runtime: ReviewAssistantRuntime,
) -> ReviewAssistanceRecord:
    """Invoke bounded assistance and deterministically fall back on every failure."""

    if not isinstance(packet, ReviewDecisionPacket):
        raise ValueError("review assistance requires a ReviewDecisionPacket")
    descriptor = getattr(runtime, "descriptor", None)
    if not isinstance(descriptor, RuntimeDescriptor):
        raise ValueError("review runtime requires a governed RuntimeDescriptor")
    prompt = {
        "contract": "satn-review-assistant-prompt/v1",
        "packet_fingerprint": packet.fingerprint,
        "packet": packet.canonical_payload(),
        "allowed_response_schema": _ALLOWED_RESPONSE_SCHEMA,
    }
    prompt_fingerprint = _fingerprint(prompt)
    try:
        invocation = runtime.invoke(prompt)
    except Exception:
        return _fallback_record(
            packet,
            descriptor,
            prompt_fingerprint,
            "runtime-error",
            "runtime-error",
        )
    if not isinstance(invocation, RuntimeInvocation):
        return _fallback_record(
            packet,
            descriptor,
            prompt_fingerprint,
            "invalid-runtime-invocation",
            "invalid-runtime-invocation",
        )
    if invocation.state == "timed-out":
        return _fallback_record(
            packet,
            descriptor,
            prompt_fingerprint,
            "runtime-timed-out",
            invocation.state,
        )
    if invocation.state == "unavailable":
        return _fallback_record(
            packet,
            descriptor,
            prompt_fingerprint,
            "runtime-unavailable",
            invocation.state,
        )
    try:
        response = ReviewAssistantResponse.from_runtime_payload(invocation.response_payload)
    except (TypeError, ValueError):
        return _fallback_record(
            packet,
            descriptor,
            prompt_fingerprint,
            "invalid-response-schema",
            invocation.state,
        )
    outcome = _validate_response(packet, response)
    if outcome != "valid-cited-response":
        return _fallback_record(
            packet,
            descriptor,
            prompt_fingerprint,
            outcome,
            invocation.state,
        )
    return _record(
        packet=packet,
        descriptor=descriptor,
        prompt_fingerprint=prompt_fingerprint,
        response=response,
        disposition="accepted",
        validation_outcome="valid-cited-response",
        runtime_state=invocation.state,
        fallback_reason=None,
    )


def _validate_response(
    packet: ReviewDecisionPacket,
    response: ReviewAssistantResponse,
) -> ValidationOutcome:
    evidence_ids = {item.evidence_id for item in packet.evidence}
    if not set(response.citation_ids).issubset(evidence_ids):
        return "invalid-citation"
    if response.response_type in {"comparison-explanation", "cited-inconsistency"}:
        dimensions = {
            item.dimension for item in packet.evidence if item.evidence_id in response.citation_ids
        }
        if len(response.citation_ids) < 2 or len(dimensions) < 2:
            return "insufficient-comparison-evidence"
    if response.response_type == "evidence-investigation-request":
        investigations = {item.investigation_id for item in packet.investigations}
        if response.investigation_id not in investigations:
            return "unoffered-investigation"
    if response.response_type == "offered-action":
        actions = {item.action_id for item in packet.offered_actions}
        if response.action_id not in actions:
            return "unoffered-action"
        if not packet.actions_delegated:
            return "action-not-delegated"
    return "valid-cited-response"


def _fallback_record(
    packet: ReviewDecisionPacket,
    descriptor: RuntimeDescriptor,
    prompt_fingerprint: str,
    outcome: ValidationOutcome,
    runtime_state: str,
) -> ReviewAssistanceRecord:
    return _record(
        packet=packet,
        descriptor=descriptor,
        prompt_fingerprint=prompt_fingerprint,
        response=packet.fallback.response,
        disposition="fallback",
        validation_outcome=outcome,
        runtime_state=runtime_state,
        fallback_reason=packet.fallback.reason,
    )


def _record(
    *,
    packet: ReviewDecisionPacket,
    descriptor: RuntimeDescriptor,
    prompt_fingerprint: str,
    response: ReviewAssistantResponse,
    disposition: Literal["accepted", "fallback"],
    validation_outcome: ValidationOutcome,
    runtime_state: str,
    fallback_reason: str | None,
) -> ReviewAssistanceRecord:
    payload = {
        "contract": "satn-review-assistance-record/v1",
        "disposition": disposition,
        "validation_outcome": validation_outcome,
        "response": response.canonical_payload(),
        "used_fallback": disposition == "fallback",
        "candidate_ids": packet.candidate_ids,
        "fallback_reason": fallback_reason,
        "packet_fingerprint": packet.fingerprint,
        "prompt_fingerprint": prompt_fingerprint,
        "schema_fingerprint": _SCHEMA_FINGERPRINT,
        "runtime": descriptor.canonical_payload(),
        "runtime_state": runtime_state,
    }
    return ReviewAssistanceRecord(
        disposition=disposition,
        validation_outcome=validation_outcome,
        response=response,
        used_fallback=disposition == "fallback",
        candidate_ids=packet.candidate_ids,
        fallback_reason=fallback_reason,
        packet_fingerprint=packet.fingerprint,
        prompt_fingerprint=prompt_fingerprint,
        schema_fingerprint=_SCHEMA_FINGERPRINT,
        provider_id=descriptor.provider_id,
        model_id=descriptor.model_id,
        model_fingerprint=descriptor.model_fingerprint,
        runtime_fingerprint=descriptor.runtime_fingerprint,
        runtime_state=runtime_state,
        result_fingerprint=_fingerprint(payload),
    )


__all__ = [
    "DeterministicReviewFallback",
    "ReviewAction",
    "ReviewAssistanceRecord",
    "ReviewAssistantResponse",
    "ReviewAssistantRuntime",
    "ReviewDecisionPacket",
    "ReviewEvidence",
    "ReviewInvestigation",
    "RuntimeDescriptor",
    "RuntimeInvocation",
    "assist_review",
]
