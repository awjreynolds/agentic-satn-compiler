"""Adversarial and replay tests for Existing-Alignment Advantage v1."""

from __future__ import annotations

import json
import math
from datetime import date

import pytest
from pydantic import ValidationError

from satn.existing_alignment import (
    AccessibilityObservation,
    AccessibilityState,
    AlignmentContextEvidence,
    BarrierObservation,
    BarrierType,
    CandidateEligibilityProof,
    CurrentRouteKind,
    DeliveryEvidence,
    DeliveryEvidenceDimension,
    EvidenceProvenance,
    EvidenceState,
    ExistingAlignmentAdvantage,
    ExistingAlignmentCandidate,
    ExistingAlignmentEvidence,
    ExistingAlignmentUnknownReason,
    FacilityQuality,
    FacilityQualityObservation,
    GeometryMatchProfile,
    GovernedAssertion,
    GovernedFreshnessPolicy,
    GreenwayQualificationEvidence,
    LightingObservation,
    LightingState,
    NearEquivalenceProof,
    ReusableAssetEvidence,
    ReusableEvidenceDimension,
    RoadClass,
    RoadClassObservation,
    RouteAvailability,
    SurfaceObservation,
    SurfaceType,
    compare_near_equivalent_existing_alignments,
    evaluate_existing_alignment_advantage,
)
from satn.network_selection import CandidateSourceClass, NetworkSelectionProfile

AS_OF = date(2026, 7, 26)
HASH = "a" * 64
PROFILE = GeometryMatchProfile(
    crs="EPSG:27700",
    tolerance_m=0.001,
    minimum_match_length_m=0.1,
    maximum_direction_difference_degrees=5.0,
)


def provenance(
    *,
    source_id: str = "official-register",
    observed_on: date = date(2026, 1, 2),
    valid_until: date | None = date(2026, 12, 31),
    freshness_policy: GovernedFreshnessPolicy | None = None,
) -> EvidenceProvenance:
    return EvidenceProvenance(
        source_id=source_id,
        content_sha256=HASH,
        release="2026.1",
        effective_date=date(2026, 1, 1),
        licence="Open Government Licence v3.0",
        observed_on=observed_on,
        valid_until=valid_until,
        freshness_policy=freshness_policy,
    )


def assertion(
    state: EvidenceState = EvidenceState.CONFIRMED,
    *,
    source_id: str = "official-register",
    valid_until: date | None = date(2026, 12, 31),
) -> GovernedAssertion:
    return GovernedAssertion(
        state=state,
        provenance=provenance(source_id=source_id, valid_until=valid_until),
        note="Recorded fact",
    )


def reusable(
    state: EvidenceState = EvidenceState.CONFIRMED,
    *,
    valid_until: date | None = date(2026, 12, 31),
) -> ReusableAssetEvidence:
    return ReusableAssetEvidence(
        lawful_access=assertion(
            state, source_id="access-register", valid_until=valid_until
        ),
        usable_condition=assertion(
            state, source_id="condition-survey", valid_until=valid_until
        ),
        continuity=assertion(
            state, source_id="network-survey", valid_until=valid_until
        ),
        responsible_ownership_or_maintenance=assertion(
            state, source_id="maintenance-register", valid_until=valid_until
        ),
    )


def delivery(
    state: EvidenceState = EvidenceState.CONFIRMED,
    *,
    valid_until: date | None = date(2026, 12, 31),
) -> DeliveryEvidence:
    return DeliveryEvidence(
        concept=assertion(
            state, source_id="concept-record", valid_until=valid_until
        ),
        constraints=assertion(
            state, source_id="constraints-record", valid_until=valid_until
        ),
        consents=assertion(
            state, source_id="consents-record", valid_until=valid_until
        ),
        cost=assertion(state, source_id="cost-record", valid_until=valid_until),
        accountable_feasibility=assertion(
            state, source_id="accountable-record", valid_until=valid_until
        ),
    )


def greenway_qualification(
    state: EvidenceState = EvidenceState.CONFIRMED,
    *,
    valid_until: date | None = date(2026, 12, 31),
) -> GreenwayQualificationEvidence:
    return GreenwayQualificationEvidence(
        traffic_free=assertion(
            state, source_id="traffic-free-survey", valid_until=valid_until
        ),
        lawful_cycling_access=assertion(
            state, source_id="access-register", valid_until=valid_until
        ),
        continuity=assertion(
            state, source_id="continuity-survey", valid_until=valid_until
        ),
    )


def context() -> AlignmentContextEvidence:
    return AlignmentContextEvidence(
        surface=SurfaceObservation(
            value=SurfaceType.UNKNOWN,
            provenance=None,
        ),
        facility_quality=FacilityQualityObservation(
            value=FacilityQuality.AUDITED_DEFICIENT,
            provenance=provenance(source_id="quality-audit"),
        ),
        lighting=LightingObservation(
            value=LightingState.UNLIT,
            provenance=provenance(source_id="lighting-survey"),
        ),
        road_class=RoadClassObservation(
            value=RoadClass.TRAFFIC_FREE,
            provenance=provenance(source_id="road-register"),
        ),
        barrier=BarrierObservation(
            value=BarrierType.GATE,
            provenance=provenance(source_id="barrier-audit"),
        ),
        accessibility=AccessibilityObservation(
            value=AccessibilityState.RESTRICTED,
            provenance=provenance(source_id="accessibility-audit"),
        ),
    )


def evidence(
    evidence_id: str,
    start: float,
    end: float,
    *,
    kind: CurrentRouteKind = CurrentRouteKind.CURRENT_NCN,
    availability: RouteAvailability = RouteAvailability.OPEN,
    status_provenance: EvidenceProvenance | None = None,
    greenway: GreenwayQualificationEvidence | None = None,
    diversion: GovernedAssertion | None = None,
    asset: ReusableAssetEvidence | None = None,
    delivery_record: DeliveryEvidence | None = None,
    context_record: AlignmentContextEvidence | None = None,
) -> ExistingAlignmentEvidence:
    return ExistingAlignmentEvidence(
        evidence_id=evidence_id,
        geometry_wkt=f"LINESTRING ({start} 0, {end} 0)",
        geometry_crs="EPSG:27700",
        current_route_kind=kind,
        availability=availability,
        current_status_provenance=status_provenance or provenance(),
        greenway_qualification=greenway,
        open_diversion=diversion,
        reusable_asset=asset,
        delivery_evidence=delivery_record,
        context=context_record or context(),
    )


def candidate(
    candidate_id: str = "candidate-a",
    *,
    directness: float = 1.2,
    y: float = 0.0,
) -> ExistingAlignmentCandidate:
    return ExistingAlignmentCandidate(
        candidate_id=candidate_id,
        geometry_wkt=f"LINESTRING (0 {y}, 100 {y})",
        geometry_crs="EPSG:27700",
        directness=directness,
    )


def selection_profile() -> NetworkSelectionProfile:
    return NetworkSelectionProfile(
        profile_id="test-selection-profile",
        candidate_source_precedence=(
            CandidateSourceClass.VERIFIED_EXISTING_ASSET,
            CandidateSourceClass.A_ROAD_CORRIDOR,
            CandidateSourceClass.OTHER_ROUTABLE,
        ),
    )


def evaluate(
    candidate_record: ExistingAlignmentCandidate,
    records: tuple[ExistingAlignmentEvidence, ...],
    *,
    as_of: date = AS_OF,
) -> ExistingAlignmentAdvantage:
    return evaluate_existing_alignment_advantage(
        candidate_record,
        records,
        as_of=as_of,
        match_profile=PROFILE,
    )


def eligibility(
    advantage: ExistingAlignmentAdvantage,
) -> CandidateEligibilityProof:
    return CandidateEligibilityProof(
        candidate_id=advantage.candidate_id,
        advantage_fingerprint=advantage.fingerprint,
        candidate_geometry_fingerprint=advantage.candidate_geometry_fingerprint,
        evidence_fingerprint=advantage.evidence_fingerprint,
        mandatory_validity_topology_fingerprint="b" * 64,
        education_completeness_fingerprint="c" * 64,
        active_objective_evidence_fingerprint="d" * 64,
        near_equivalence_calculation_fingerprint="e" * 64,
        near_equivalence_profile_fingerprint="f" * 64,
    )


def near_equivalence_proof(
    profile: NetworkSelectionProfile,
    advantages: tuple[ExistingAlignmentAdvantage, ...],
) -> NearEquivalenceProof:
    ordered = tuple(sorted(advantages, key=lambda item: item.candidate_id))
    return NearEquivalenceProof(
        proof_id="proof-one",
        as_of=AS_OF,
        profile_fingerprint=profile.fingerprint,
        active_objective=profile.primary_objective,
        near_equivalence_calculation_fingerprint="e" * 64,
        near_equivalence_profile_fingerprint="f" * 64,
        candidate_ids=tuple(item.candidate_id for item in ordered),
        eligibility=tuple(eligibility(item) for item in ordered),
        near_equivalent_after_mandatory_gates=True,
    )


def test_current_open_ncn_and_qualified_greenway_contribute_recognised_share() -> None:
    result = evaluate(
        candidate(),
        (
            evidence(
                "ncn",
                0,
                40,
                asset=reusable(),
                delivery_record=delivery(),
            ),
            evidence(
                "greenway",
                60,
                100,
                kind=CurrentRouteKind.GREENWAY,
                greenway=greenway_qualification(),
            ),
        ),
    )

    assert result.recognised_current_length_m == pytest.approx(80, abs=0.01)
    assert result.reusable_asset_length_m == pytest.approx(40, abs=0.01)
    assert result.unknown_length_m == pytest.approx(20, abs=0.01)
    assert result.does_not_establish_route_quality_or_adequacy is True
    retained_context = result.transitions[0].context_lineage[0].context
    assert retained_context.surface.value is SurfaceType.UNKNOWN
    assert retained_context.facility_quality.value is (
        FacilityQuality.AUDITED_DEFICIENT
    )
    assert retained_context.barrier.value is BarrierType.GATE
    assert retained_context.accessibility.value is AccessibilityState.RESTRICTED
    assert {
        item.source_id
        for item in result.transitions[0].evidence_provenance_lineage[0].provenance
    } >= {
        "barrier-audit",
        "accessibility-audit",
        "quality-audit",
    }
    json.loads(json.dumps(result.canonical_payload(), allow_nan=False))


@pytest.mark.parametrize(
    "availability",
    (RouteAvailability.CLOSED, RouteAvailability.TEMPORARILY_CLOSED),
)
def test_closed_reusable_asset_gets_no_advantage_without_open_diversion(
    availability: RouteAvailability,
) -> None:
    blocked = evaluate(
        candidate(),
        (
            evidence(
                "blocked",
                0,
                100,
                kind=CurrentRouteKind.DECLASSIFIED_NCN,
                availability=availability,
                asset=reusable(),
            ),
        ),
    )
    transition = blocked.transitions[0]

    assert blocked.reusable_asset_length_m == 0
    assert blocked.reusable_asset_share == 0
    assert blocked.longest_continuous_reusable_m == 0
    assert transition.reuse_availability_evidence_state is EvidenceState.UNKNOWN
    assert transition.reusable_asset_evidence_state is EvidenceState.UNKNOWN
    assert ExistingAlignmentUnknownReason.CLOSURE_BLOCKER in transition.unknown_reasons
    assert (
        ExistingAlignmentUnknownReason.OPEN_DIVERSION_UNKNOWN
        in transition.unknown_reasons
    )


def test_current_governed_open_diversion_allows_physical_reuse() -> None:
    result = evaluate(
        candidate(),
        (
            evidence(
                "diverted",
                0,
                100,
                kind=CurrentRouteKind.DECLASSIFIED_NCN,
                availability=RouteAvailability.CLOSED,
                diversion=assertion(
                    EvidenceState.CONFIRMED,
                    source_id="diversion-order",
                ),
                asset=reusable(),
            ),
        ),
    )

    assert result.reusable_asset_length_m == 100
    assert (
        result.transitions[0].reuse_availability_evidence_state
        is EvidenceState.CONFIRMED
    )
    assert ExistingAlignmentUnknownReason.CLOSURE_BLOCKER not in (
        result.transitions[0].unknown_reasons
    )
    assert "diversion-order" in {
        item.source_id
        for item in result.transitions[0].evidence_provenance_lineage[0].provenance
    }


def test_unbounded_or_unconfirmed_diversion_cannot_clear_closure() -> None:
    for diversion in (
        assertion(
            EvidenceState.CONFIRMED,
            source_id="unbounded-diversion",
            valid_until=None,
        ),
        assertion(EvidenceState.ABSENT, source_id="absent-diversion"),
    ):
        result = evaluate(
            candidate(),
            (
                evidence(
                    "blocked",
                    0,
                    100,
                    availability=RouteAvailability.CLOSED,
                    diversion=diversion,
                    asset=reusable(),
                ),
            ),
        )
        assert result.reusable_asset_length_m == 0
        assert ExistingAlignmentUnknownReason.CLOSURE_BLOCKER in (
            result.transitions[0].unknown_reasons
        )


def test_closed_alignment_cannot_win_the_reuse_tie_break() -> None:
    profile = selection_profile()
    blocked = evaluate(
        candidate("candidate-a"),
        (
            evidence(
                "blocked",
                0,
                100,
                kind=CurrentRouteKind.DECLASSIFIED_NCN,
                availability=RouteAvailability.CLOSED,
                asset=reusable(),
            ),
        ),
    )
    open_asset = evaluate(
        candidate("candidate-b"),
        (
            evidence(
                "open",
                0,
                100,
                kind=CurrentRouteKind.DECLASSIFIED_NCN,
                asset=reusable(),
            ),
        ),
    )
    advantages = (blocked, open_asset)
    comparison = compare_near_equivalent_existing_alignments(
        profile,
        advantages,
        proof=near_equivalence_proof(profile, advantages),
    )

    assert blocked.reusable_asset_share == 0
    assert open_asset.reusable_asset_share == 1
    assert comparison.ranked_candidate_ids == ("candidate-b", "candidate-a")


def test_barrier_and_accessibility_are_governed_context_not_adequacy_claims() -> None:
    result = evaluate(candidate(), (evidence("route", 0, 100),))
    retained = result.transitions[0].context_lineage[0]

    assert retained.context.barrier.value is BarrierType.GATE
    assert retained.context.accessibility.value is AccessibilityState.RESTRICTED
    assert result.does_not_establish_route_quality_or_adequacy is True
    with pytest.raises(ValidationError, match="known barrier evidence"):
        BarrierObservation(value=BarrierType.STEPS)
    with pytest.raises(ValidationError, match="known accessibility evidence"):
        AccessibilityObservation(value=AccessibilityState.ACCESSIBLE)


@pytest.mark.parametrize(
    ("kind", "availability", "greenway"),
    (
        (CurrentRouteKind.CURRENT_NCN, RouteAvailability.TEMPORARILY_CLOSED, None),
        (CurrentRouteKind.CURRENT_NCN, RouteAvailability.CLOSED, None),
        (CurrentRouteKind.OTHER_RECOGNISED, RouteAvailability.OPEN, None),
        (CurrentRouteKind.GREENWAY, RouteAvailability.OPEN, None),
        (
            CurrentRouteKind.GREENWAY,
            RouteAvailability.OPEN,
            greenway_qualification(EvidenceState.UNKNOWN),
        ),
    ),
)
def test_nonqualifying_current_status_is_preserved_but_not_recognised(
    kind: CurrentRouteKind,
    availability: RouteAvailability,
    greenway: GreenwayQualificationEvidence | None,
) -> None:
    result = evaluate(
        candidate(),
        (
            evidence(
                "status",
                0,
                100,
                kind=kind,
                availability=availability,
                greenway=greenway,
            ),
        ),
    )

    assert result.recognised_current_length_m == 0
    assert (
        ExistingAlignmentUnknownReason.CURRENT_STATUS_NOT_QUALIFYING
        in result.unknown_reasons
    )
    assert result.transitions[0].route_kinds == (kind,)
    assert result.transitions[0].availability_states == (availability,)


def test_future_unbounded_and_expired_current_status_never_contribute() -> None:
    future = provenance(
        source_id="future",
        observed_on=date(2026, 8, 1),
        valid_until=date(2027, 1, 1),
    ).model_copy(update={"effective_date": date(2026, 8, 1)})
    result = evaluate(
        candidate(),
        (
            evidence("future", 0, 30, status_provenance=future),
            evidence(
                "unbounded",
                35,
                65,
                status_provenance=provenance(
                    source_id="unbounded", valid_until=None
                ),
            ),
            evidence(
                "expired",
                70,
                100,
                status_provenance=provenance(
                    source_id="expired", valid_until=date(2026, 2, 1)
                ),
            ),
        ),
    )

    assert result.recognised_current_length_m == 0
    assert set(result.unknown_reasons) >= {
        ExistingAlignmentUnknownReason.FUTURE_EVIDENCE,
        ExistingAlignmentUnknownReason.UNBOUNDED_STATUS_FRESHNESS,
        ExistingAlignmentUnknownReason.STALE_EVIDENCE,
    }


def test_governed_freshness_policy_can_bound_current_status() -> None:
    policy = GovernedFreshnessPolicy(policy_id="annual-review", max_age_days=365)
    result = evaluate(
        candidate(),
        (
            evidence(
                "policy",
                0,
                100,
                status_provenance=provenance(
                    valid_until=None,
                    freshness_policy=policy,
                ),
            ),
        ),
    )
    assert result.recognised_current_length_m == pytest.approx(100, abs=0.01)


def test_unbounded_greenway_reuse_and_delivery_evidence_yield_no_advantage() -> None:
    result = evaluate(
        candidate(),
        (
            evidence(
                "unbounded-evidence",
                0,
                100,
                kind=CurrentRouteKind.GREENWAY,
                greenway=greenway_qualification(valid_until=None),
                asset=reusable(valid_until=None),
                delivery_record=delivery(valid_until=None),
            ),
        ),
    )
    assert result.recognised_current_length_m == 0
    assert result.reusable_asset_length_m == 0
    assert result.delivery_evidence_complete_length_m == 0
    assert (
        ExistingAlignmentUnknownReason.UNBOUNDED_EVIDENCE_FRESHNESS
        in result.unknown_reasons
    )
    assert (
        ExistingAlignmentUnknownReason.GREENWAY_QUALIFICATION_INCOMPLETE
        in result.unknown_reasons
    )


def test_declassified_status_has_no_status_advantage_but_verified_reuse_counts() -> None:
    result = evaluate(
        candidate(),
        (
            evidence(
                "former-ncn",
                0,
                100,
                kind=CurrentRouteKind.DECLASSIFIED_NCN,
                asset=reusable(),
                delivery_record=delivery(),
            ),
        ),
    )

    assert result.recognised_current_length_m == 0
    assert result.declassified_length_m == pytest.approx(100, abs=0.01)
    assert result.reusable_asset_length_m == pytest.approx(100, abs=0.01)
    assert result.delivery_evidence_complete_length_m == pytest.approx(100, abs=0.01)
    assert result.matched_share == 1.0
    assert result.longest_continuous_match_m == 100.0
    assert result.longest_continuous_recognised_m == 0.0
    assert result.longest_continuous_reusable_m == 100.0


def test_stale_status_does_not_discard_independently_current_reuse_or_delivery() -> None:
    result = evaluate(
        candidate(),
        (
            evidence(
                "stale-status",
                0,
                100,
                status_provenance=provenance(
                    source_id="old-status",
                    valid_until=date(2026, 2, 1),
                ),
                asset=reusable(),
                delivery_record=delivery(),
            ),
        ),
    )
    assert result.recognised_current_length_m == 0
    assert result.reusable_asset_length_m == pytest.approx(100, abs=0.01)
    assert result.delivery_evidence_complete_length_m == pytest.approx(100, abs=0.01)


def test_conflicting_status_on_the_same_geometry_is_visible_and_claim_safe() -> None:
    result = evaluate(
        candidate(),
        (
            evidence(
                "current",
                0,
                100,
                kind=CurrentRouteKind.CURRENT_NCN,
                asset=reusable(),
            ),
            evidence(
                "declassified",
                0,
                100,
                kind=CurrentRouteKind.DECLASSIFIED_NCN,
                asset=reusable(),
            ),
        ),
    )
    assert result.recognised_current_length_m == 0
    assert result.reusable_asset_length_m == pytest.approx(100, abs=0.01)
    assert (
        ExistingAlignmentUnknownReason.CONFLICTING_EVIDENCE
        in result.unknown_reasons
    )
    assert result.transitions[0].evidence_ids == ("current", "declassified")
    assert len(result.transitions[0].evidence_geometry_fingerprints) == 2
    assert len(
        {
            item.fingerprint
            for item in result.transitions[0].evidence_geometry_fingerprints
        }
    ) == 1


def test_conflicting_confirmed_and_absent_reuse_and_delivery_are_explicit() -> None:
    result = evaluate(
        candidate(),
        (
            evidence(
                "absent",
                0,
                100,
                asset=reusable(EvidenceState.ABSENT),
                delivery_record=delivery(EvidenceState.ABSENT),
            ),
            evidence(
                "confirmed",
                0,
                100,
                asset=reusable(EvidenceState.CONFIRMED),
                delivery_record=delivery(EvidenceState.CONFIRMED),
            ),
        ),
    )
    transition = result.transitions[0]
    assert transition.reusable_asset_evidence_state is EvidenceState.UNKNOWN
    assert transition.delivery_evidence_state is EvidenceState.UNKNOWN
    assert transition.reusable_asset is False
    assert transition.delivery_evidence_complete is False
    assert (
        ExistingAlignmentUnknownReason.CONFLICTING_EVIDENCE
        in transition.unknown_reasons
    )
    assert tuple(
        item.dimension for item in transition.reusable_dimension_assessments
    ) == tuple(ReusableEvidenceDimension)
    assert all(
        item.conflicting and item.state is EvidenceState.UNKNOWN
        for item in transition.reusable_dimension_assessments
    )
    assert tuple(
        item.dimension for item in transition.delivery_dimension_assessments
    ) == tuple(DeliveryEvidenceDimension)
    assert all(
        item.conflicting and item.state is EvidenceState.UNKNOWN
        for item in transition.delivery_dimension_assessments
    )
    assert set(transition.unknown_reasons) >= {
        ExistingAlignmentUnknownReason.LAWFUL_ACCESS_CONFLICT,
        ExistingAlignmentUnknownReason.USABLE_CONDITION_CONFLICT,
        ExistingAlignmentUnknownReason.CONTINUITY_CONFLICT,
        ExistingAlignmentUnknownReason.RESPONSIBILITY_CONFLICT,
        ExistingAlignmentUnknownReason.DELIVERY_CONCEPT_CONFLICT,
        ExistingAlignmentUnknownReason.DELIVERY_CONSTRAINTS_CONFLICT,
        ExistingAlignmentUnknownReason.DELIVERY_CONSENTS_CONFLICT,
        ExistingAlignmentUnknownReason.DELIVERY_COST_CONFLICT,
        ExistingAlignmentUnknownReason.DELIVERY_ACCOUNTABLE_FEASIBILITY_CONFLICT,
    }
    assert {item.source_id for item in transition.provenance} >= {
        "access-register",
        "concept-record",
    }


def test_reuse_conflict_is_derived_per_dimension_and_lists_only_unknowns() -> None:
    first_asset = ReusableAssetEvidence(
        lawful_access=assertion(EvidenceState.CONFIRMED, source_id="first-access"),
        usable_condition=assertion(EvidenceState.ABSENT, source_id="first-condition"),
        continuity=assertion(EvidenceState.CONFIRMED, source_id="first-continuity"),
        responsible_ownership_or_maintenance=assertion(
            EvidenceState.CONFIRMED, source_id="first-responsibility"
        ),
    )
    second_asset = ReusableAssetEvidence(
        lawful_access=assertion(EvidenceState.ABSENT, source_id="second-access"),
        usable_condition=assertion(
            EvidenceState.CONFIRMED, source_id="second-condition"
        ),
        continuity=assertion(EvidenceState.CONFIRMED, source_id="second-continuity"),
        responsible_ownership_or_maintenance=assertion(
            EvidenceState.CONFIRMED, source_id="second-responsibility"
        ),
    )
    result = evaluate(
        candidate(),
        (
            evidence("first", 0, 100, asset=first_asset),
            evidence("second", 0, 100, asset=second_asset),
        ),
    )
    reasons = set(result.transitions[0].unknown_reasons)
    assert ExistingAlignmentUnknownReason.CONFLICTING_EVIDENCE in reasons
    assert ExistingAlignmentUnknownReason.LAWFUL_ACCESS_CONFLICT in reasons
    assert ExistingAlignmentUnknownReason.USABLE_CONDITION_CONFLICT in reasons
    assert ExistingAlignmentUnknownReason.LAWFUL_ACCESS_UNKNOWN not in reasons
    assert ExistingAlignmentUnknownReason.USABLE_CONDITION_UNKNOWN not in reasons
    assert ExistingAlignmentUnknownReason.CONTINUITY_UNKNOWN not in reasons
    assert ExistingAlignmentUnknownReason.RESPONSIBILITY_UNKNOWN not in reasons


def test_per_dimension_unknowns_are_published_without_false_conflicts() -> None:
    asset = reusable().model_copy(
        update={
            "lawful_access": assertion(
                EvidenceState.UNKNOWN,
                source_id="access-unknown",
            )
        }
    )
    delivery_record = delivery().model_copy(
        update={
            "concept": assertion(
                EvidenceState.UNKNOWN,
                source_id="concept-unknown",
            ),
            "cost": assertion(EvidenceState.ABSENT, source_id="cost-absent"),
        }
    )
    result = evaluate(
        candidate(),
        (
            evidence(
                "dimensions",
                0,
                100,
                asset=asset,
                delivery_record=delivery_record,
            ),
        ),
    )
    transition = result.transitions[0]
    reusable_states = {
        item.dimension: (item.state, item.conflicting)
        for item in transition.reusable_dimension_assessments
    }
    delivery_states = {
        item.dimension: (item.state, item.conflicting)
        for item in transition.delivery_dimension_assessments
    }

    assert reusable_states[ReusableEvidenceDimension.LAWFUL_ACCESS] == (
        EvidenceState.UNKNOWN,
        False,
    )
    assert delivery_states[DeliveryEvidenceDimension.CONCEPT] == (
        EvidenceState.UNKNOWN,
        False,
    )
    assert delivery_states[DeliveryEvidenceDimension.COST] == (
        EvidenceState.ABSENT,
        False,
    )
    assert set(transition.unknown_reasons) >= {
        ExistingAlignmentUnknownReason.LAWFUL_ACCESS_UNKNOWN,
        ExistingAlignmentUnknownReason.DELIVERY_CONCEPT_UNKNOWN,
        ExistingAlignmentUnknownReason.DELIVERY_EVIDENCE_INCOMPLETE,
    }
    assert ExistingAlignmentUnknownReason.LAWFUL_ACCESS_CONFLICT not in (
        transition.unknown_reasons
    )
    assert ExistingAlignmentUnknownReason.DELIVERY_CONCEPT_CONFLICT not in (
        transition.unknown_reasons
    )
    assert ExistingAlignmentUnknownReason.DELIVERY_COST_UNKNOWN not in (
        transition.unknown_reasons
    )


def test_closed_and_temporarily_closed_statuses_are_conflicting() -> None:
    result = evaluate(
        candidate(),
        (
            evidence(
                "closed",
                0,
                100,
                availability=RouteAvailability.CLOSED,
            ),
            evidence(
                "temporary",
                0,
                100,
                availability=RouteAvailability.TEMPORARILY_CLOSED,
            ),
        ),
    )
    assert (
        ExistingAlignmentUnknownReason.CONFLICTING_EVIDENCE
        in result.transitions[0].unknown_reasons
    )


def test_provenance_with_licence_and_freshness_differences_is_canonical() -> None:
    zulu = provenance(source_id="same-record").model_copy(
        update={"licence": "Zulu licence"}
    )
    alpha = provenance(
        source_id="same-record",
        freshness_policy=GovernedFreshnessPolicy(
            policy_id="context-review",
            max_age_days=365,
        ),
    ).model_copy(update={"licence": "Alpha licence"})
    context_record = AlignmentContextEvidence(
        surface=SurfaceObservation(
            value=SurfaceType.ASPHALT,
            provenance=alpha,
        )
    )

    result = evaluate(
        candidate(),
        (
            evidence(
                "route",
                0,
                100,
                status_provenance=zulu,
                context_record=context_record,
            ),
        ),
    )
    lineage = result.transitions[0].evidence_provenance_lineage[0].provenance

    assert {item.licence for item in lineage} == {"Alpha licence", "Zulu licence"}
    assert lineage == tuple(
        sorted(
            lineage,
            key=lambda item: json.dumps(
                item.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    assert result == evaluate(
        candidate(),
        (
            evidence(
                "route",
                0,
                100,
                status_provenance=zulu,
                context_record=context_record,
            ),
        ),
    )


def test_context_is_id_bound_and_swapping_it_changes_or_invalidates_proof() -> None:
    first_context = context()
    second_context = context().model_copy(
        update={
            "barrier": BarrierObservation(
                value=BarrierType.RIVER,
                provenance=provenance(source_id="river-survey"),
            )
        }
    )
    first_record = evidence(
        "first",
        0,
        100,
        context_record=first_context,
    )
    second_record = evidence(
        "second",
        0,
        100,
        context_record=second_context,
    )
    result = evaluate(candidate(), (first_record, second_record))
    transition = result.transitions[0]
    lineage_payloads = [
        item.model_dump(mode="python") for item in transition.context_lineage
    ]
    lineage_payloads[0]["context"] = second_context.model_dump(mode="python")
    lineage_payloads[1]["context"] = first_context.model_dump(mode="python")

    with pytest.raises(
        ValidationError,
        match=r"context provenance|context fingerprint",
    ):
        transition.__class__.model_validate(
            {
                **transition.model_dump(mode="python"),
                "context_lineage": tuple(lineage_payloads),
            }
        )

    swapped = evaluate(
        candidate(),
        (
            first_record.model_copy(update={"context": second_context}),
            second_record.model_copy(update={"context": first_context}),
        ),
    )
    assert swapped.fingerprint != result.fingerprint
    assert (
        swapped.transitions[0].context_lineage[0].context_fingerprint
        != transition.context_lineage[0].context_fingerprint
    )


def test_geometry_matching_is_internal_crs_aware_and_replay_stable() -> None:
    records = (
        evidence("second", 50, 100, asset=reusable()),
        evidence("first", 0, 50, asset=reusable()),
    )
    first = evaluate(candidate(), records)
    replay = evaluate(candidate(), tuple(reversed(records)))

    assert first == replay
    assert first.fingerprint == replay.fingerprint
    assert first.match_fingerprint == replay.match_fingerprint
    assert first.matched_length_m == pytest.approx(100, abs=0.01)
    with pytest.raises(ValueError, match="governed geometry-match CRS"):
        evaluate_existing_alignment_advantage(
            candidate(),
            (
                records[0].model_copy(update={"geometry_crs": "EPSG:4326"}),
            ),
            as_of=AS_OF,
            match_profile=PROFILE,
        )


def test_directional_overlap_counts_exact_collinear_length_not_buffer_caps() -> None:
    collinear = evaluate(
        candidate(),
        (
            evidence("twenty-metres", 20, 40),
        ),
    )
    perpendicular_record = evidence("perpendicular", 0, 20).model_copy(
        update={"geometry_wkt": "LINESTRING (30 -10, 30 10)"}
    )
    perpendicular = evaluate(candidate(), (perpendicular_record,))

    assert collinear.matched_length_m == 20.0
    assert collinear.matched_share == 0.2
    assert collinear.longest_continuous_match_m == 20.0
    assert perpendicular.matched_length_m == 0.0
    assert perpendicular.longest_continuous_match_m == 0.0


def test_shallow_angle_clips_remote_endpoints_and_is_vertex_invariant() -> None:
    profile = GeometryMatchProfile(
        crs="EPSG:27700",
        tolerance_m=5.0,
        minimum_match_length_m=0.1,
        maximum_direction_difference_degrees=5.0,
    )
    sparse_candidate = ExistingAlignmentCandidate(
        candidate_id="candidate-a",
        geometry_wkt="LINESTRING (0 0, 1000 0)",
        geometry_crs="EPSG:27700",
        directness=1.2,
    )
    sparse_evidence = evidence("shallow", 0, 100).model_copy(
        update={"geometry_wkt": "LINESTRING (-100 -10, 1100 10)"}
    )
    dense_candidate = sparse_candidate.model_copy(
        update={"geometry_wkt": "LINESTRING (0 0, 500 0, 1000 0)"}
    )
    dense_evidence = sparse_evidence.model_copy(
        update={
            "geometry_wkt": (
                "LINESTRING (-100 -10, 200 -5, 500 0, 800 5, 1100 10)"
            )
        }
    )

    sparse = evaluate_existing_alignment_advantage(
        sparse_candidate,
        (sparse_evidence,),
        as_of=AS_OF,
        match_profile=profile,
    )
    dense_evidence_only = evaluate_existing_alignment_advantage(
        sparse_candidate,
        (dense_evidence,),
        as_of=AS_OF,
        match_profile=profile,
    )
    dense_candidate_only = evaluate_existing_alignment_advantage(
        dense_candidate,
        (sparse_evidence,),
        as_of=AS_OF,
        match_profile=profile,
    )
    dense_both = evaluate_existing_alignment_advantage(
        dense_candidate,
        (dense_evidence,),
        as_of=AS_OF,
        match_profile=profile,
    )

    assert sparse.matched_length_m == pytest.approx(600.0, abs=1e-9)
    assert sparse.matched_share == pytest.approx(0.6, abs=1e-12)
    assert [
        (item.start_m, item.end_m)
        for item in sparse.transitions
        if item.evidence_ids
    ] == [(200.0, 800.0)]
    assert dense_evidence_only == sparse
    assert dense_candidate_only == sparse
    assert dense_both == sparse
    assert dense_both.candidate_geometry_fingerprint == (
        sparse.candidate_geometry_fingerprint
    )
    assert dense_both.evidence_fingerprint == sparse.evidence_fingerprint
    assert dense_both.match_fingerprint == sparse.match_fingerprint
    assert dense_both.fingerprint == sparse.fingerprint


def test_direction_lateral_and_minimum_length_boundaries_are_inclusive() -> None:
    boundary_candidate = ExistingAlignmentCandidate(
        candidate_id="candidate-a",
        geometry_wkt="LINESTRING (0 0, 4 0)",
        geometry_crs="EPSG:27700",
        directness=1.2,
    )
    boundary_profile = GeometryMatchProfile(
        crs="EPSG:27700",
        tolerance_m=1.0,
        minimum_match_length_m=2.0,
        maximum_direction_difference_degrees=45.0,
    )
    crossing = evidence("crossing", 0, 4).model_copy(
        update={"geometry_wkt": "LINESTRING (0 -2, 4 2)"}
    )
    on_lateral_boundary = evidence("parallel", 0, 4).model_copy(
        update={"geometry_wkt": "LINESTRING (0 1, 4 1)"}
    )
    outside_lateral_boundary = on_lateral_boundary.model_copy(
        update={"geometry_wkt": "LINESTRING (0 1.000001, 4 1.000001)"}
    )

    exact = evaluate_existing_alignment_advantage(
        boundary_candidate,
        (crossing,),
        as_of=AS_OF,
        match_profile=boundary_profile,
    )
    direction_outside = evaluate_existing_alignment_advantage(
        boundary_candidate,
        (crossing,),
        as_of=AS_OF,
        match_profile=boundary_profile.model_copy(
            update={"maximum_direction_difference_degrees": 44.999}
        ),
    )
    minimum_outside = evaluate_existing_alignment_advantage(
        boundary_candidate,
        (crossing,),
        as_of=AS_OF,
        match_profile=boundary_profile.model_copy(
            update={"minimum_match_length_m": 2.000001}
        ),
    )
    lateral_exact = evaluate_existing_alignment_advantage(
        boundary_candidate,
        (on_lateral_boundary,),
        as_of=AS_OF,
        match_profile=boundary_profile,
    )
    lateral_outside = evaluate_existing_alignment_advantage(
        boundary_candidate,
        (outside_lateral_boundary,),
        as_of=AS_OF,
        match_profile=boundary_profile,
    )

    assert exact.matched_length_m == pytest.approx(2.0, abs=1e-12)
    assert direction_outside.matched_length_m == 0
    assert minimum_outside.matched_length_m == 0
    assert lateral_exact.matched_length_m == 4.0
    assert lateral_outside.matched_length_m == 0


def test_minimum_match_length_applies_after_candidate_wide_union() -> None:
    union_profile = GeometryMatchProfile(
        crs="EPSG:27700",
        tolerance_m=0.001,
        minimum_match_length_m=2.0,
        maximum_direction_difference_degrees=5.0,
    )
    union_candidate = ExistingAlignmentCandidate(
        candidate_id="candidate-a",
        geometry_wkt="LINESTRING (0 0, 4 0)",
        geometry_crs="EPSG:27700",
        directness=1.2,
    )
    first = evidence(
        "first",
        0,
        1,
        status_provenance=provenance(source_id="first-status"),
    )
    second = evidence(
        "second",
        1,
        2,
        status_provenance=provenance(source_id="second-status"),
    )
    single = evidence(
        "single",
        0,
        2,
        status_provenance=provenance(source_id="single-status"),
    )

    fragmented = evaluate_existing_alignment_advantage(
        union_candidate,
        (first, second),
        as_of=AS_OF,
        match_profile=union_profile,
    )
    one_feature = evaluate_existing_alignment_advantage(
        union_candidate,
        (single,),
        as_of=AS_OF,
        match_profile=union_profile,
    )
    matched_transitions = tuple(
        item for item in fragmented.transitions if item.evidence_ids
    )

    assert fragmented.matched_length_m == one_feature.matched_length_m == 2.0
    assert fragmented.matched_share == one_feature.matched_share == 0.5
    assert fragmented.longest_continuous_match_m == (
        one_feature.longest_continuous_match_m
    ) == 2.0
    assert tuple(item.evidence_ids for item in matched_transitions) == (
        ("first",),
        ("second",),
    )
    assert {
        lineage.evidence_id
        for transition in matched_transitions
        for lineage in transition.evidence_provenance_lineage
    } == {"first", "second"}
    assert {
        record.source_id
        for transition in matched_transitions
        for record in transition.provenance
    } >= {"first-status", "second-status"}


def test_noncontiguous_subminimum_fragments_remain_unmatched() -> None:
    union_profile = GeometryMatchProfile(
        crs="EPSG:27700",
        tolerance_m=0.001,
        minimum_match_length_m=2.0,
        maximum_direction_difference_degrees=5.0,
    )
    union_candidate = ExistingAlignmentCandidate(
        candidate_id="candidate-a",
        geometry_wkt="LINESTRING (0 0, 4 0)",
        geometry_crs="EPSG:27700",
        directness=1.2,
    )
    result = evaluate_existing_alignment_advantage(
        union_candidate,
        (
            evidence("first", 0, 1),
            evidence("second", 2, 3),
        ),
        as_of=AS_OF,
        match_profile=union_profile,
    )

    assert result.matched_length_m == 0
    assert result.matched_share == 0
    assert result.longest_continuous_match_m == 0
    assert all(not item.evidence_ids for item in result.transitions)


def test_connected_multipart_boundary_linework_has_single_line_identity() -> None:
    boundary_candidate = ExistingAlignmentCandidate(
        candidate_id="candidate-a",
        geometry_wkt="LINESTRING (0 0, 4 0)",
        geometry_crs="EPSG:27700",
        directness=1.2,
    )
    boundary_profile = GeometryMatchProfile(
        crs="EPSG:27700",
        tolerance_m=1.0,
        minimum_match_length_m=2.0,
        maximum_direction_difference_degrees=45.0,
    )
    single = evidence("boundary", 0, 4).model_copy(
        update={"geometry_wkt": "LINESTRING (0 -2, 4 2)"}
    )
    connected_multipart = single.model_copy(
        update={
            "geometry_wkt": (
                "MULTILINESTRING ((4 2, 2 0), (0 -2, 2 0))"
            )
        }
    )

    single_result = evaluate_existing_alignment_advantage(
        boundary_candidate,
        (single,),
        as_of=AS_OF,
        match_profile=boundary_profile,
    )
    multipart_result = evaluate_existing_alignment_advantage(
        boundary_candidate,
        (connected_multipart,),
        as_of=AS_OF,
        match_profile=boundary_profile,
    )

    assert single_result.matched_length_m == 2.0
    assert multipart_result == single_result
    assert multipart_result.evidence_fingerprint == (
        single_result.evidence_fingerprint
    )
    assert multipart_result.match_fingerprint == single_result.match_fingerprint
    assert multipart_result.fingerprint == single_result.fingerprint


def test_geometry_evidence_identity_is_invariant_to_line_orientation() -> None:
    candidate_forward = candidate()
    evidence_forward = evidence("route", 0, 100, asset=reusable())
    forward = evaluate(candidate_forward, (evidence_forward,))
    reversed_result = evaluate(
        candidate_forward.model_copy(
            update={"geometry_wkt": "LINESTRING (100 0, 0 0)"}
        ),
        (
            evidence_forward.model_copy(
                update={"geometry_wkt": "LINESTRING (100 0, 0 0)"}
            ),
        ),
    )
    assert forward.candidate_geometry_fingerprint == (
        reversed_result.candidate_geometry_fingerprint
    )
    assert forward.evidence_fingerprint == reversed_result.evidence_fingerprint
    assert forward.match_fingerprint == reversed_result.match_fingerprint
    assert forward.fingerprint == reversed_result.fingerprint


def test_signed_zero_is_canonical_in_geometry_and_output_identity() -> None:
    positive = evaluate(candidate(), (evidence("route", 0, 100),))
    signed = evaluate(
        candidate().model_copy(
            update={"geometry_wkt": "LINESTRING (-0 -0, 100 -0)"}
        ),
        (
            evidence("route", 0, 100).model_copy(
                update={"geometry_wkt": "LINESTRING (-0 -0, 100 -0)"}
            ),
        ),
    )
    assert positive.fingerprint == signed.fingerprint
    assert math.copysign(1.0, signed.transitions[0].start_m) == 1.0


def test_geometry_changes_change_derived_identity_and_no_offsets_are_accepted() -> None:
    first = evaluate(candidate(), (evidence("route", 0, 40),))
    second = evaluate(candidate(y=0.5), (evidence("route", 0, 40),))
    assert first.candidate_geometry_fingerprint != second.candidate_geometry_fingerprint
    assert first.match_fingerprint != second.match_fingerprint
    with pytest.raises(ValidationError, match="Extra inputs"):
        ExistingAlignmentEvidence.model_validate(
            {
                **evidence("route", 0, 40).model_dump(mode="python"),
                "start_m": 0.0,
            }
        )


def test_comparison_requires_exact_gate_objective_date_and_evidence_proof() -> None:
    profile = selection_profile()
    candidates = (
        candidate("candidate-a", directness=1.1),
        candidate("candidate-b", directness=1.1),
    )
    advantages = tuple(
        evaluate(item, (evidence(f"route-{item.candidate_id}", 0, 50, asset=reusable()),))
        for item in candidates
    )
    proof = near_equivalence_proof(profile, advantages)
    comparison = compare_near_equivalent_existing_alignments(
        profile,
        tuple(reversed(advantages)),
        proof=proof,
    )
    assert comparison.ranked_candidate_ids == ("candidate-a", "candidate-b")
    assert comparison.weighted_aggregate_used is False
    assert comparison.near_equivalence_proof_fingerprint == proof.fingerprint
    changed = ExistingAlignmentAdvantage.model_validate(
        {
            **advantages[0].model_dump(mode="python"),
            "evidence_fingerprint": "b" * 64,
        }
    )
    with pytest.raises(ValueError, match="exact advantage evidence"):
        compare_near_equivalent_existing_alignments(
            profile,
            (changed, advantages[1]),
            proof=proof,
        )
    wrong_date = proof.model_copy(update={"as_of": date(2026, 7, 25)})
    with pytest.raises(ValueError, match="share one as_of"):
        compare_near_equivalent_existing_alignments(
            profile,
            advantages,
            proof=wrong_date,
        )
    with pytest.raises(ValidationError, match="contradict comparison values"):
        comparison.__class__.model_validate(
            {
                **comparison.model_dump(mode="python"),
                "ranked_candidate_ids": tuple(
                    reversed(comparison.ranked_candidate_ids)
                ),
            }
        )
    altered_values = list(comparison.comparison_values)
    altered_values[0] = altered_values[0].model_copy(
        update={"reusable_asset_share": 0.99}
    )
    with pytest.raises(ValidationError, match="derived from bound advantages"):
        comparison.__class__.model_validate(
            {
                **comparison.model_dump(mode="python"),
                "comparison_values": tuple(altered_values),
            }
        )


def test_derived_models_reject_contradictory_lengths_states_and_gaps() -> None:
    result = evaluate(
        candidate(),
        (
            evidence("route", 0, 50, asset=reusable()),
        ),
    )
    transition = result.transitions[0]
    with pytest.raises(ValidationError, match="reusable_asset must agree"):
        transition.__class__.model_validate(
            {
                **transition.model_dump(mode="python"),
                "reusable_asset": False,
            }
        )
    with pytest.raises(
        ValidationError,
        match=r"shares must agree|does not agree with transitions",
    ):
        ExistingAlignmentAdvantage.model_validate(
            {
                **result.model_dump(mode="python"),
                "reusable_asset_length_m": 0.0,
            }
        )
    with pytest.raises(ValidationError, match="gaps must be exactly"):
        ExistingAlignmentAdvantage.model_validate(
            {
                **result.model_dump(mode="python"),
                "gaps": (),
            }
        )
    with pytest.raises(ValidationError, match="corresponding geometry"):
        transition.__class__.model_validate(
            {
                **transition.model_dump(mode="python"),
                "evidence_geometry_fingerprints": (),
            }
        )
    with pytest.raises(ValidationError, match="corresponding provenance"):
        transition.__class__.model_validate(
            {
                **transition.model_dump(mode="python"),
                "evidence_provenance_lineage": (),
            }
        )
    with pytest.raises(ValidationError, match="transition-derived evidence IDs"):
        ExistingAlignmentAdvantage.model_validate(
            {
                **result.model_dump(mode="python"),
                "evidence_geometry_lengths_m": (),
            }
        )


@pytest.mark.parametrize(
    ("candidate_id", "directness"),
    (
        (" candidate", 1.0),
        ("candidate", 0.9),
        ("candidate", "1.1"),
        ("candidate", True),
        ("candidate", float("nan")),
        ("candidate", float("inf")),
    ),
)
def test_candidate_contract_rejects_noncanonical_or_nonfinite_input(
    candidate_id: str,
    directness: object,
) -> None:
    with pytest.raises(ValidationError):
        ExistingAlignmentCandidate.model_validate(
            {
                "candidate_id": candidate_id,
                "geometry_wkt": "LINESTRING (0 0, 1 0)",
                "geometry_crs": "EPSG:27700",
                "directness": directness,
            }
        )


def test_geometry_and_proof_contracts_reject_ambiguous_or_noncanonical_input() -> None:
    with pytest.raises(ValueError, match="simple LineString"):
        evaluate(
            ExistingAlignmentCandidate(
                candidate_id="loop",
                geometry_wkt="LINESTRING (0 0, 10 0, 0 0)",
                geometry_crs="EPSG:27700",
                directness=1.0,
            ),
            (),
        )
    profile = selection_profile()
    advantage = evaluate(candidate(), ())
    with pytest.raises(ValidationError, match="Extra inputs"):
        CandidateEligibilityProof.model_validate(
            {
                **eligibility(advantage).model_dump(mode="python"),
                "mandatory_safeguards_passed": True,
            }
        )
    with pytest.raises(ValidationError, match="canonically ordered"):
        NearEquivalenceProof(
            proof_id="bad-proof",
            as_of=AS_OF,
            profile_fingerprint=profile.fingerprint,
            active_objective=profile.primary_objective,
            near_equivalence_calculation_fingerprint="e" * 64,
            near_equivalence_profile_fingerprint="f" * 64,
            candidate_ids=("candidate-b", "candidate-a"),
            eligibility=(eligibility(advantage),),
            near_equivalent_after_mandatory_gates=True,
        )
