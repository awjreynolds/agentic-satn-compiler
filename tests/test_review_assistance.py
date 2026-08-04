from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from satn.review_assistance import (
    DeterministicReviewFallback,
    ReviewAction,
    ReviewAssistantResponse,
    ReviewDecisionPacket,
    ReviewEvidence,
    ReviewInvestigation,
    RuntimeDescriptor,
    RuntimeInvocation,
    assist_review,
)


class ScriptedRuntime:
    def __init__(self, invocation: RuntimeInvocation, *, model: str = "fixture-model-v1"):
        self.descriptor = RuntimeDescriptor(
            provider_id="fixture-provider",
            model_id=model,
            model_fingerprint="a" * 64,
            runtime_fingerprint="b" * 64,
        )
        self._invocation = invocation

    def invoke(self, prompt: dict[str, object]) -> RuntimeInvocation:
        assert "packet_fingerprint" in prompt
        assert "allowed_response_schema" in prompt
        return self._invocation


def _packet(*, delegate_actions: bool = True) -> ReviewDecisionPacket:
    return ReviewDecisionPacket(
        packet_id="review-corridor-a-b",
        candidate_ids=("candidate-a-road", "candidate-cycleway"),
        evidence=(
            ReviewEvidence(
                evidence_id="evidence-cycleway-existing",
                dimension="existing-provision",
                statement="The cycleway is recorded as existing provision.",
                evidence_fingerprint="c" * 64,
            ),
            ReviewEvidence(
                evidence_id="evidence-a-road-traffic",
                dimension="traffic",
                statement="Traffic evidence for the A-road is unavailable.",
                evidence_fingerprint="d" * 64,
            ),
        ),
        investigations=(
            ReviewInvestigation(
                investigation_id="investigate-a-road-traffic",
                label="Obtain governed A-road traffic evidence",
            ),
        ),
        offered_actions=(
            ReviewAction(
                action_id="retain-compiler-preference",
                label="Retain the compiler preference",
            ),
        ),
        actions_delegated=delegate_actions,
        fallback=DeterministicReviewFallback(
            response=ReviewAssistantResponse(
                response_type="evidence-investigation-request",
                citation_ids=("evidence-a-road-traffic",),
                investigation_id="investigate-a-road-traffic",
            ),
            reason="deterministic-missing-evidence-fallback",
        ),
    )


def _accepted(payload: dict[str, object]) -> RuntimeInvocation:
    return RuntimeInvocation(state="accepted", response_payload=payload)


def test_valid_cited_explanation_is_accepted_without_authoring_facts() -> None:
    packet = _packet()
    runtime = ScriptedRuntime(
        _accepted(
            {
                "response_type": "comparison-explanation",
                "citation_ids": [
                    "evidence-cycleway-existing",
                    "evidence-a-road-traffic",
                ],
                "investigation_id": None,
                "action_id": None,
            }
        )
    )

    record = assist_review(packet, runtime)

    assert record.disposition == "accepted"
    assert record.validation_outcome == "valid-cited-response"
    assert record.response is not None
    assert record.response.citation_ids == (
        "evidence-a-road-traffic",
        "evidence-cycleway-existing",
    )
    assert record.used_fallback is False
    assert record.candidate_ids == packet.candidate_ids
    assert not hasattr(record, "geometry")
    assert not hasattr(record, "selected_candidate_id")
    assert len(record.prompt_fingerprint) == 64
    assert len(record.schema_fingerprint) == 64
    assert record.model_fingerprint == "a" * 64
    assert record.runtime_fingerprint == "b" * 64
    assert len(record.result_fingerprint) == 64

    with pytest.raises(FrozenInstanceError):
        packet.packet_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("invocation", "expected_outcome"),
    (
        (
            _accepted(
                {
                    "response_type": "comparison-explanation",
                    "citation_ids": ["invented-evidence"],
                    "investigation_id": None,
                    "action_id": None,
                }
            ),
            "invalid-citation",
        ),
        (
            _accepted(
                {
                    "response_type": "offered-action",
                    "citation_ids": ["evidence-cycleway-existing"],
                    "investigation_id": None,
                    "action_id": "invented-action",
                }
            ),
            "unoffered-action",
        ),
        (
            _accepted(
                {
                    "response_type": "comparison-explanation",
                    "citation_ids": ["evidence-cycleway-existing"],
                    "investigation_id": None,
                    "action_id": None,
                    "route_fact": "The A-road has space for a protected cycle track.",
                }
            ),
            "invalid-response-schema",
        ),
        (
            RuntimeInvocation(state="timed-out", detail="deadline exceeded"),
            "runtime-timed-out",
        ),
        (
            RuntimeInvocation(state="unavailable", detail="provider unavailable"),
            "runtime-unavailable",
        ),
    ),
)
def test_invalid_or_unavailable_ai_always_uses_the_same_typed_fallback(
    invocation: RuntimeInvocation,
    expected_outcome: str,
) -> None:
    packet = _packet()

    record = assist_review(packet, ScriptedRuntime(invocation))

    assert record.disposition == "fallback"
    assert record.validation_outcome == expected_outcome
    assert record.used_fallback is True
    assert record.response == packet.fallback.response
    assert record.candidate_ids == packet.candidate_ids
    assert record.fallback_reason == packet.fallback.reason
    assert len(record.result_fingerprint) == 64


def test_offered_action_requires_explicit_workflow_delegation() -> None:
    packet = _packet(delegate_actions=False)
    invocation = _accepted(
        {
            "response_type": "offered-action",
            "citation_ids": ["evidence-cycleway-existing"],
            "investigation_id": None,
            "action_id": "retain-compiler-preference",
        }
    )

    record = assist_review(packet, ScriptedRuntime(invocation))

    assert record.disposition == "fallback"
    assert record.validation_outcome == "action-not-delegated"


@pytest.mark.parametrize(
    "payload",
    (
        {
            "response_type": "evidence-investigation-request",
            "citation_ids": ["evidence-a-road-traffic"],
            "investigation_id": "investigate-a-road-traffic",
            "action_id": None,
        },
        {
            "response_type": "cited-inconsistency",
            "citation_ids": [
                "evidence-cycleway-existing",
                "evidence-a-road-traffic",
            ],
            "investigation_id": None,
            "action_id": None,
        },
        {
            "response_type": "offered-action",
            "citation_ids": ["evidence-cycleway-existing"],
            "investigation_id": None,
            "action_id": "retain-compiler-preference",
        },
    ),
)
def test_each_closed_response_type_can_be_accepted(payload: dict[str, object]) -> None:
    record = assist_review(_packet(), ScriptedRuntime(_accepted(payload)))

    assert record.disposition == "accepted"
    assert record.validation_outcome == "valid-cited-response"


def test_comparison_requires_citations_from_distinct_evidence_dimensions() -> None:
    invocation = _accepted(
        {
            "response_type": "comparison-explanation",
            "citation_ids": ["evidence-cycleway-existing"],
            "investigation_id": None,
            "action_id": None,
        }
    )

    record = assist_review(_packet(), ScriptedRuntime(invocation))

    assert record.disposition == "fallback"
    assert record.validation_outcome == "insufficient-comparison-evidence"


def test_runtime_and_packet_changes_change_record_identity() -> None:
    invocation = _accepted(
        {
            "response_type": "cited-inconsistency",
            "citation_ids": [
                "evidence-cycleway-existing",
                "evidence-a-road-traffic",
            ],
            "investigation_id": None,
            "action_id": None,
        }
    )

    first = assist_review(_packet(), ScriptedRuntime(invocation, model="fixture-model-v1"))
    second = assist_review(_packet(), ScriptedRuntime(invocation, model="fixture-model-v2"))

    assert first.result_fingerprint != second.result_fingerprint
    assert first.packet_fingerprint == second.packet_fingerprint


def test_packet_rejects_non_finite_or_unbound_fallback_choices() -> None:
    with pytest.raises(ValueError, match="unique"):
        ReviewDecisionPacket(
            packet_id="duplicate-candidates",
            candidate_ids=("candidate-a", "candidate-a"),
            evidence=(),
            investigations=(),
            offered_actions=(),
            actions_delegated=False,
            fallback=DeterministicReviewFallback(
                response=ReviewAssistantResponse(
                    response_type="comparison-explanation",
                    citation_ids=("missing",),
                ),
                reason="fallback",
            ),
        )
