"""Regression tests for deterministic Preferred Strategic Alignment primitives."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from satn.alignment_selection import (
    AgentAuthorityRole,
    AgentInvocation,
    ReferenceAdoptionPacket,
    ReviewRun,
    RuntimeAttemptOutcome,
    RuntimeInvocationRecord,
    _configured_human_adoption_contract,
)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def test_agent_invocation_is_local_structured_and_tamper_evident() -> None:
    invocation = AgentInvocation(
        invocation_id="primary-run-001",
        role=AgentAuthorityRole.PRIMARY_ALIGNMENT_DECISION,
        role_contract_fingerprint=digest("primary-role"),
        prompt_contract_fingerprint=digest("primary-prompt"),
        request_fingerprint=digest("request"),
        recorded_on=date(2026, 7, 26),
    )
    assert invocation.invocation_fingerprint
    with pytest.raises(ValidationError, match="invocation fingerprint is stale"):
        AgentInvocation(
            **{
                **invocation.model_dump(mode="python"),
                "request_fingerprint": digest("altered-request"),
            }
        )


def test_review_run_is_deterministic_and_allows_a_fresh_rerun() -> None:
    scope = digest("scenario-and-dependencies")
    first = ReviewRun(run_scope_fingerprint=scope)
    rerun = ReviewRun(run_scope_fingerprint=scope)
    assert first == rerun
    next_run = ReviewRun(
        run_scope_fingerprint=scope,
        prior_orchestration_fingerprint=digest("previous-run"),
    )
    assert next_run.run_id != first.run_id
    with pytest.raises(ValidationError, match="review run ID is stale"):
        ReviewRun(run_scope_fingerprint=scope, run_id="review-run-00000000000000000000")


def test_runtime_failure_record_is_typed_and_bound_to_the_exact_frontier() -> None:
    record = RuntimeInvocationRecord(
        invocation_id="provider-attempt-001",
        review_run_id="review-run-1234567890abcdef1234",
        frontier_fingerprint=digest("frontier"),
        request_fingerprint=digest("request"),
        outcome=RuntimeAttemptOutcome.PROVIDER_TIMEOUT,
        failure_code="adapter-timeout",
        started_at_ms=10,
        completed_at_ms=11,
    )
    assert record.receipt_fingerprint
    with pytest.raises(ValidationError, match="not timed"):
        RuntimeInvocationRecord(
            **{
                **record.model_dump(mode="python"),
                "started_at_ms": 12,
            }
        )


def test_adoption_packet_is_attributable_but_makes_no_identity_claim() -> None:
    contract = _configured_human_adoption_contract()
    packet = ReferenceAdoptionPacket(
        scenario_fingerprint=digest("scenario"),
        profile_fingerprint=digest("profile"),
        evidence_snapshot_fingerprint=digest("evidence"),
        adoption_contract=contract,
    )
    assert packet.packet_fingerprint
    with pytest.raises(ValidationError, match="packet fingerprint is stale"):
        ReferenceAdoptionPacket(
            **{
                **packet.model_dump(mode="python"),
                "scenario_fingerprint": digest("another-scenario"),
            }
        )


def test_selection_core_contains_no_secret_or_signature_subsystem() -> None:
    source = Path(__file__).parents[1] / "src/satn/alignment_selection.py"
    content = source.read_text().lower()
    forbidden = (
        "ed25519",
        "private_key",
        "public_key",
        "external_signature",
        "verification_key",
    )
    assert not any(item in content for item in forbidden)
