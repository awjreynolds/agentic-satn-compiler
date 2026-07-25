"""Deny-by-default evidence for whether an agent runtime can be promoted.

This module intentionally does *not* decide which provider or model is approved.
That is a human governance decision (tracked separately).  It provides the
technical mechanism: every run receives a canonical, tamper-detectable account
of the configured and observed runtime, and promotion is refused unless an
immutable runtime class and decision-ledger provenance have both been approved.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from satn.models import (
    AgentConfig,
    AgentRecord,
    DivergenceRecord,
    canonical_decision_ledger_payload,
)
from satn.runtime_governance_contract import (
    assert_declared_runtime_governance_digests,
    canonical_sha256,
)

SCHEMA_VERSION = "satn-runtime-governance/v1"
GovernanceStatus = Literal["production-approved", "non-production", "reviewable"]


@dataclass(frozen=True)
class ApprovedRuntimeClass:
    """One immutable, human-approved direct-runtime class and its audit digest.

    A future human policy may construct these values from a reviewed run.  The
    repository deliberately supplies no instances: a release is therefore
    denied by default rather than accidentally treating configuration as an
    approval.
    """

    runtime_class_sha256: str
    ledger_provenance_sha256: str


APPROVED_RUNTIME_CLASSES: frozenset[ApprovedRuntimeClass] = frozenset()


def _approved_manifest_class(manifest: Mapping[str, object]) -> bool:
    """Return whether a manifest names a reviewed immutable production class.

    ``status`` and ``promotion`` are evidence emitted by this module, not an
    authority to promote.  A copied or hand-authored manifest can make those
    fields self-consistent.  The release gate therefore also requires the two
    immutable digests to be present in the repository's reviewed allow-list.
    That list is deliberately empty until a human adds a reviewed entry.
    """

    runtime_class_sha256 = manifest.get("runtime_class_sha256")
    ledger_provenance_sha256 = manifest.get("decision_ledger_provenance_sha256")
    if not isinstance(runtime_class_sha256, str) or not isinstance(
        ledger_provenance_sha256, str
    ):
        return False
    return (
        ApprovedRuntimeClass(runtime_class_sha256, ledger_provenance_sha256)
        in APPROVED_RUNTIME_CLASSES
    )


def _review_records(
    records: Iterable[AgentRecord | DivergenceRecord],
) -> list[AgentRecord | DivergenceRecord]:
    return [record for record in records if record.review_required]


def _record_responder_mode(record: AgentRecord | DivergenceRecord) -> str:
    return record.responder_mode or "not-invoked"


def _runtime_class(
    config: AgentConfig,
    records: list[AgentRecord | DivergenceRecord],
) -> tuple[dict[str, object], list[str], list[str], list[str]]:
    review_records = _review_records(records)
    responder_modes = sorted({_record_responder_mode(record) for record in review_records})
    direct_records = [
        record for record in review_records if record.responder_mode == "direct-runtime"
    ]
    providers = sorted({str(record.runtime) for record in direct_records if record.runtime})
    models = sorted({str(record.model) for record in direct_records if record.model})
    runtime_class = {
        "configured": {
            "provider": config.provider,
            "model": config.model,
            "response_mode": config.response_mode,
        },
        "observed": {
            "providers": providers,
            "models": models,
            "responder_modes": responder_modes,
        },
    }
    return runtime_class, responder_modes, providers, models


def _usage_metadata(records: list[AgentRecord | DivergenceRecord]) -> dict[str, object]:
    by_mode: dict[str, dict[str, int]] = {}
    for mode, grouped in _group_by_mode(records).items():
        by_mode[mode] = {
            "records": len(grouped),
            "requests": sum(int(record.usage.get("requests", 0)) for record in grouped),
            "tokens": sum(int(record.usage.get("tokens", 0)) for record in grouped),
        }
    return {
        "available": bool(records),
        "records": len(records),
        "requests": sum(int(record.usage.get("requests", 0)) for record in records),
        "tokens": sum(int(record.usage.get("tokens", 0)) for record in records),
        "by_responder_mode": by_mode,
    }


def _group_by_mode(
    records: list[AgentRecord | DivergenceRecord],
) -> dict[str, list[AgentRecord | DivergenceRecord]]:
    grouped: dict[str, list[AgentRecord | DivergenceRecord]] = {}
    for record in records:
        grouped.setdefault(_record_responder_mode(record), []).append(record)
    return grouped


def _ledger_provenance_sha256(
    decision_contract: str,
    decision_ledger_input: Mapping[str, object],
    accepted_decisions: list[dict[str, object]],
) -> str:
    return canonical_sha256(
        {
            "decision_contract": decision_contract,
            "decision_ledger_input": decision_ledger_input,
            "accepted_decisions": accepted_decisions,
        }
    )


def classify_runtime_governance(
    config: AgentConfig,
    records: Iterable[AgentRecord | DivergenceRecord],
    *,
    decision_ledger_input: Mapping[str, object],
    accepted_decisions: list[dict[str, object]],
    validation: str | None = None,
    approved_runtime_classes: Iterable[ApprovedRuntimeClass] = APPROVED_RUNTIME_CLASSES,
) -> dict[str, object]:
    """Return public, canonical runtime evidence and a deny-by-default status.

    Caller ledgers do not carry immutable origin runtime evidence in the current
    decision-ledger contract.  They are consequently non-production even if
    their choices are valid.  That conservative result is intentional: replay
    cannot upgrade its origin until a future approved provenance contract can
    prove both the original class and ledger digest.
    """

    canonical_input = canonical_decision_ledger_payload(dict(decision_ledger_input))
    canonical_accepted = canonical_decision_ledger_payload(
        {"decision_contract": canonical_input.decision_contract, "responses": accepted_decisions}
    )
    if canonical_input.decision_contract != canonical_accepted.decision_contract:
        raise ValueError("runtime governance decision contracts do not match")
    canonical_input_payload = canonical_input.model_dump(mode="json")
    canonical_accepted_payload = canonical_accepted.model_dump(mode="json")["responses"]
    if dict(decision_ledger_input) != canonical_input_payload:
        raise ValueError("runtime governance input ledger is not canonical")
    if accepted_decisions != canonical_accepted_payload:
        raise ValueError("runtime governance accepted decisions are not canonical")

    record_list = list(records)
    runtime_class, responder_modes, providers, models = _runtime_class(config, record_list)
    runtime_class_sha256 = canonical_sha256(runtime_class)
    ledger_provenance_sha256 = _ledger_provenance_sha256(
        canonical_input.decision_contract,
        canonical_input_payload,
        canonical_accepted_payload,
    )
    replay_status = "replay" if canonical_input.responses else "fresh"
    review_records = _review_records(record_list)
    direct_records = [
        record for record in review_records if record.responder_mode == "direct-runtime"
    ]
    caller_records = [record for record in review_records if record.responder_mode == "caller"]
    policy = frozenset(approved_runtime_classes)
    approved = ApprovedRuntimeClass(runtime_class_sha256, ledger_provenance_sha256) in policy

    status: GovernanceStatus
    reason: str
    if config.provider == "fake" or "fake" in providers:
        status = "non-production"
        reason = "fake-runtime"
    elif validation is not None:
        status = "reviewable"
        reason = f"incomplete-decisions:{validation}"
    elif caller_records or canonical_input.responses:
        status = "non-production"
        reason = "replay-origin-provenance-unavailable"
    elif not direct_records:
        status = "reviewable"
        reason = "no-direct-runtime-decision-provenance"
    elif len(providers) != 1 or len(models) != 1 or responder_modes != ["direct-runtime"]:
        status = "reviewable"
        reason = "runtime-class-is-not-immutable"
    elif not approved:
        status = "non-production"
        reason = "runtime-class-not-approved"
    else:
        status = "production-approved"
        reason = "approved-immutable-runtime-class-and-ledger-provenance"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "promotion": {"allowed": status == "production-approved", "reason": reason},
        "configured_runtime": runtime_class["configured"],
        "observed_runtime": runtime_class["observed"],
        "responder_modes": responder_modes,
        "replay_status": replay_status,
        "usage": _usage_metadata(review_records),
        "decision_ledger_provenance_sha256": ledger_provenance_sha256,
        "runtime_class_sha256": runtime_class_sha256,
        "reviewed_decision_count": len(review_records),
        "accepted_decision_count": len(canonical_accepted.responses),
    }


def incomplete_runtime_governance(
    config: AgentConfig,
    records: Iterable[AgentRecord | DivergenceRecord],
    *,
    decision_ledger_input: Mapping[str, object],
    validation: str | None,
) -> dict[str, object]:
    """Classify an incomplete invocation without pretending it fell back safely."""

    return classify_runtime_governance(
        config,
        records,
        decision_ledger_input=decision_ledger_input,
        accepted_decisions=[],
        validation=validation or "response-required",
    )


def assert_promotable_runtime_governance(
    manifest: Mapping[str, object],
    *,
    decision_contract: object,
    decision_ledger_input: Mapping[str, object],
    accepted_decisions: list[dict[str, object]],
) -> None:
    """Refuse an explicit production promotion unless the run is approved."""

    try:
        runtime_class_sha256, ledger_provenance_sha256 = (
            assert_declared_runtime_governance_digests(
                manifest,
                decision_contract=decision_contract,
                decision_ledger_input=decision_ledger_input,
                accepted_decisions=accepted_decisions,
            )
        )
    except ValueError as error:
        raise ValueError(f"production promotion denied: {error}") from error
    promotion = manifest.get("promotion")
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("status") != "production-approved"
        or not isinstance(promotion, Mapping)
        or promotion.get("allowed") is not True
        or ApprovedRuntimeClass(runtime_class_sha256, ledger_provenance_sha256)
        not in APPROVED_RUNTIME_CLASSES
    ):
        raise ValueError(
            "production promotion denied: no approved immutable runtime class and "
            "decision-ledger provenance match this publication"
        )


def validate_runtime_governance(
    manifest: Mapping[str, object],
    config: AgentConfig,
    records: Iterable[AgentRecord | DivergenceRecord],
    *,
    decision_ledger_input: Mapping[str, object],
    accepted_decisions: list[dict[str, object]],
) -> None:
    """Reject a present manifest that does not exactly recompute from its run."""

    expected = classify_runtime_governance(
        config,
        records,
        decision_ledger_input=decision_ledger_input,
        accepted_decisions=accepted_decisions,
    )
    if dict(manifest) != expected:
        raise ValueError("runtime governance manifest differs from decision provenance")
