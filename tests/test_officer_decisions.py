"""Focused governed officer-decision overlay contract tests for issue #235."""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from satn.models import AgentDecisionResponse
from satn.officer_decisions import (
    ActionableHumanInterventionRequest,
    ClassifyCommunityAction,
    CleanSATNBaseline,
    GovernedBaselineTarget,
    HumanInterventionRecord,
    HumanInterventionResponse,
    HumanInterventionResponseOutcome,
    InterventionRequestState,
    NetworkPublicationKind,
    OfferedOfficerAction,
    OfficerDecision,
    OfficerDecisionApplicationError,
    OfficerDecisionLedger,
    OfficerDecisionStatus,
    OfficerDecisionTarget,
    OfficerDecisionType,
    OfficerTargetKind,
    RetainNetworkGapAction,
    SetTargetEligibilityAction,
    apply_officer_decision_ledger,
    import_human_intervention_response,
    parse_canonical_officer_decision_ledger,
    publication_label,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
TODAY = date(2026, 7, 30)


def baseline() -> CleanSATNBaseline:
    return CleanSATNBaseline(
        baseline_id="baseline-clean-1",
        network_json='{"features":[{"id":"community-1"}],"type":"FeatureCollection"}',
        evidence_snapshot_fingerprint=SHA_B,
        profile_fingerprint=SHA_C,
        governed_evidence_ids=("evidence-community-source", "evidence-officer-note"),
        targets=(
            GovernedBaselineTarget(
                target=OfficerDecisionTarget(
                    kind=OfficerTargetKind.COMMUNITY,
                    target_id="community-1",
                )
            ),
            GovernedBaselineTarget(
                target=OfficerDecisionTarget(
                    kind=OfficerTargetKind.NETWORK_GAP,
                    target_id="network-gap-1",
                )
            ),
        ),
    )


def ledger_for(
    clean: CleanSATNBaseline,
    *decisions: OfficerDecision,
) -> OfficerDecisionLedger:
    return OfficerDecisionLedger(
        baseline_fingerprint=clean.baseline_fingerprint,
        evidence_snapshot_fingerprint=clean.evidence_snapshot_fingerprint,
        profile_fingerprint=clean.profile_fingerprint,
        decisions=decisions,
    )


def community_decision(
    clean: CleanSATNBaseline,
    *,
    decision_id: str = "officer-decision-1",
    status: OfficerDecisionStatus = OfficerDecisionStatus.ACTIVE,
) -> OfficerDecision:
    return OfficerDecision(
        decision_id=decision_id,
        decision_type=OfficerDecisionType.CLASSIFY_COMMUNITY,
        target=OfficerDecisionTarget(
            kind=OfficerTargetKind.COMMUNITY,
            target_id="community-1",
        ),
        action=ClassifyCommunityAction(classification="rural"),
        decision_maker="Alex Officer",
        decision_maker_role="Principal Transport Planner",
        organisation="Example Council",
        decision_date=TODAY,
        rationale="The governed settlement evidence supports rural treatment.",
        evidence_ids=("evidence-community-source",),
        source_url="https://example.test/decisions/1",
        effective_from=TODAY,
        status=status,
        baseline_fingerprint=clean.baseline_fingerprint,
        evidence_snapshot_fingerprint=clean.evidence_snapshot_fingerprint,
        profile_fingerprint=clean.profile_fingerprint,
    )


def intervention_request(clean: CleanSATNBaseline) -> ActionableHumanInterventionRequest:
    return ActionableHumanInterventionRequest(
        request_id="human-intervention-1",
        request_fingerprint=SHA_D,
        baseline_fingerprint=clean.baseline_fingerprint,
        evidence_snapshot_fingerprint=clean.evidence_snapshot_fingerprint,
        profile_fingerprint=clean.profile_fingerprint,
        governed_evidence_ids=clean.governed_evidence_ids,
        offered_actions=(
            OfferedOfficerAction(
                option_id="retain-gap",
                decision_type=OfficerDecisionType.RETAIN_NETWORK_GAP,
                target=OfficerDecisionTarget(
                    kind=OfficerTargetKind.NETWORK_GAP,
                    target_id="network-gap-1",
                ),
                action=RetainNetworkGapAction(),
            ),
        ),
    )


def intervention_response(
    clean: CleanSATNBaseline,
    *,
    outcome: HumanInterventionResponseOutcome = HumanInterventionResponseOutcome.ACCEPT,
    selected_option_id: str = "retain-gap",
) -> HumanInterventionResponse:
    return HumanInterventionResponse(
        response_id="human-response-1",
        request_id="human-intervention-1",
        request_fingerprint=SHA_D,
        selected_option_id=selected_option_id,
        outcome=outcome,
        baseline_fingerprint=clean.baseline_fingerprint,
        evidence_snapshot_fingerprint=clean.evidence_snapshot_fingerprint,
        profile_fingerprint=clean.profile_fingerprint,
        decision_maker="Alex Officer",
        decision_maker_role="Principal Transport Planner",
        organisation="Example Council",
        response_date=TODAY,
        rationale="Retain the unresolved gap for visible future investigation.",
        evidence_ids=("evidence-officer-note",),
        source_url="https://example.test/responses/1",
        effective_from=TODAY,
    )


def test_canonical_ledger_and_empty_overlay_preserve_clean_baseline_bytes() -> None:
    clean = baseline()
    ledger = ledger_for(clean)

    assert parse_canonical_officer_decision_ledger(ledger.canonical_json()) == ledger
    with pytest.raises(ValueError, match="not canonical"):
        parse_canonical_officer_decision_ledger(ledger.canonical_json() + b"\n")

    original = clean.model_dump_json()
    scenario = apply_officer_decision_ledger(clean, ledger, effective_on=TODAY)

    assert clean.model_dump_json() == original
    assert scenario.network_json.encode("ascii") == clean.network_bytes
    assert scenario.network_sha256 == clean.network_sha256
    assert scenario.scenario_id == clean.baseline_id
    assert scenario.scenario_fingerprint == clean.baseline_fingerprint
    assert scenario.publication_kind == NetworkPublicationKind.GENERATED_BASELINE
    assert scenario.applied_decision_ids == ()


def test_valid_attributable_decision_creates_separate_immutable_scenario() -> None:
    clean = baseline()
    decision = community_decision(clean)
    ledger = ledger_for(clean, decision)
    canonical = ledger.canonical_json()

    assert OfficerDecisionLedger.model_validate_json(canonical) == ledger
    assert decision.actor_kind == "human-officer"
    assert decision.evidence_ids == ("evidence-community-source",)

    scenario = apply_officer_decision_ledger(clean, ledger, effective_on=TODAY)

    assert scenario.publication_kind == NetworkPublicationKind.OFFICER_INFORMED_SCENARIO
    assert scenario.scenario_id.startswith("officer-scenario-")
    assert scenario.scenario_id != clean.baseline_id
    assert scenario.baseline_id == clean.baseline_id
    assert scenario.network_json == clean.network_json
    assert scenario.applied_decision_ids == ("officer-decision-1",)
    assert "community community-1: classified as rural" in (
        scenario.baseline_to_scenario_change_summary[0]
    )
    with pytest.raises(ValidationError):
        OfficerDecision.model_validate(
            {
                **decision.model_dump(mode="json"),
                "actor_kind": "agent",
            }
        )
    with pytest.raises(ValidationError):
        OfficerDecision.model_validate(
            AgentDecisionResponse(
                request_id="agent-request",
                dependency_fingerprint=SHA_A,
                choice_id="1",
            ).model_dump(mode="json")
        )


def test_unknown_stale_conflicting_and_ungoverned_decisions_fail_closed() -> None:
    clean = baseline()
    decision = community_decision(clean)

    with pytest.raises(ValueError, match="stale for its ledger lineage"):
        OfficerDecisionLedger(
            baseline_fingerprint=SHA_A,
            evidence_snapshot_fingerprint=clean.evidence_snapshot_fingerprint,
            profile_fingerprint=clean.profile_fingerprint,
            decisions=(decision,),
        )
    with pytest.raises(ValueError, match="duplicate active"):
        ledger_for(
            clean,
            decision,
            community_decision(clean, decision_id="officer-decision-2"),
        )

    conflicting = OfficerDecision(
        **{
            **decision.model_dump(
                mode="python",
                exclude={"decision_fingerprint", "decision_id", "decision_type", "action"},
            ),
            "decision_id": "officer-decision-3",
            "decision_type": OfficerDecisionType.SET_TARGET_ELIGIBILITY,
            "action": SetTargetEligibilityAction(eligibility="exclude"),
        }
    )
    with pytest.raises(ValueError, match="conflicting active"):
        ledger_for(clean, decision, conflicting)

    unknown_target = decision.model_copy(
        update={
            "decision_id": "officer-decision-unknown-target",
            "target": OfficerDecisionTarget(
                kind=OfficerTargetKind.COMMUNITY,
                target_id="community-missing",
            ),
            "decision_fingerprint": "",
        }
    )
    unknown_target = OfficerDecision.model_validate(
        unknown_target.model_dump(mode="python", exclude={"decision_fingerprint"})
    )
    with pytest.raises(OfficerDecisionApplicationError, match="unknown baseline target"):
        apply_officer_decision_ledger(
            clean,
            ledger_for(clean, unknown_target),
            effective_on=TODAY,
        )

    ungoverned = OfficerDecision.model_validate(
        {
            **decision.model_dump(mode="python", exclude={"decision_fingerprint"}),
            "decision_id": "officer-decision-ungoverned",
            "evidence_ids": ("evidence-not-in-snapshot",),
        }
    )
    with pytest.raises(OfficerDecisionApplicationError, match="governed snapshot"):
        apply_officer_decision_ledger(
            clean,
            ledger_for(clean, ungoverned),
            effective_on=TODAY,
        )


def test_intervention_response_selects_exact_offer_and_enters_officer_ledger() -> None:
    clean = baseline()
    request = intervention_request(clean)
    pending = HumanInterventionRecord(
        request=request,
        state=InterventionRequestState.PENDING,
    )
    response = intervention_response(clean)
    request_export = request.model_dump_json()

    assert ActionableHumanInterventionRequest.model_validate_json(request_export) == request
    answered, ledger = import_human_intervention_response(
        pending,
        HumanInterventionResponse.model_validate_json(response.model_dump_json()),
        ledger_for(clean),
    )

    assert answered.state == InterventionRequestState.ANSWERED
    assert answered.officer_decision_id == ledger.decisions[0].decision_id
    assert ledger.decisions[0].decision_type == OfficerDecisionType.RETAIN_NETWORK_GAP
    assert ledger.decisions[0].actor_kind == "human-officer"

    scenario = apply_officer_decision_ledger(clean, ledger, effective_on=TODAY)
    assert scenario.applied_decision_ids == (answered.officer_decision_id,)
    assert "retained as a visible Network Gap" in (
        scenario.baseline_to_scenario_change_summary[0]
    )

    with pytest.raises(ValueError, match="unoffered"):
        import_human_intervention_response(
            pending,
            intervention_response(clean, selected_option_id="invented-action"),
            ledger_for(clean),
        )
    with pytest.raises(ValueError, match="current pending"):
        import_human_intervention_response(answered, response, ledger)


def test_request_states_and_public_authority_labels_are_unambiguous() -> None:
    clean = baseline()
    request = intervention_request(clean)
    pending = HumanInterventionRecord(
        request=request,
        state=InterventionRequestState.PENDING,
    )
    rejected, unchanged = import_human_intervention_response(
        pending,
        intervention_response(
            clean,
            outcome=HumanInterventionResponseOutcome.REJECT,
        ),
        ledger_for(clean),
    )
    superseded = HumanInterventionRecord(
        request=request,
        state=InterventionRequestState.SUPERSEDED,
        superseded_by_request_id="human-intervention-2",
    )

    assert rejected.state == InterventionRequestState.REJECTED
    assert unchanged.decisions == ()
    assert superseded.state == InterventionRequestState.SUPERSEDED
    assert {
        publication_label(kind)
        for kind in (
            NetworkPublicationKind.GENERATED_BASELINE,
            NetworkPublicationKind.OFFICER_INFORMED_SCENARIO,
            NetworkPublicationKind.REFERENCE_SATN,
        )
    } == {
        "Generated clean baseline",
        "Officer-informed scenario",
        "Formally adopted Reference SATN",
    }
    assert len(json.loads(unchanged.canonical_json())["decisions"]) == 0
