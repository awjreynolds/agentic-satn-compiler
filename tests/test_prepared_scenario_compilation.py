"""Scenario compilation bridge regressions for PRD #137."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import MappingProxyType
from typing import Literal

import pytest
from pydantic import ValidationError
from test_alignment_selection import (
    accepted_envelope,
    candidate,
    candidate_set,
    compile_education,
    criteria,
    profile,
)

from satn.alignment_selection import (
    AlignmentCandidateInput,
    AssessmentKind,
    CandidateCriteria,
    CandidateGenerationGapReason,
    CandidateSetGapEvidence,
    DecisionProcessMode,
    GovernedAssessmentBinding,
    GovernedEducationCriterionBinding,
    GovernedEvidenceSnapshot,
    RuntimeDecisionAttempt,
    RuntimeInvocationRecord,
    ScenarioDecisionRecord,
    admit_candidate_set,
    review_frontier_fingerprint,
    traffic_diagnostics_for_candidate,
)
from satn.education_access import (
    assess_education_access,
    governed_education_assessment_fingerprint,
)
from satn.network_selection import NetworkSelectionProfile
from satn.scenario_compilation import (
    PreparedCandidateCriteria,
    PreparedCriteriaLineage,
    PreparedScenarioCompilationInput,
    compile_prepared_scenario,
    prepared_network_geometry_source_fingerprint,
    prepared_topography_source_fingerprint,
)
from satn.spine_access_candidate_preparation import (
    CandidatePreparationIssue,
    PreparedConnectionRosterRecord,
    PreparedSpineAccessConnection,
    SpineAccessCandidatePreparationResult,
)
from satn.traffic_evidence import (
    ProtectedSpaceEvidence,
    TrafficFreshnessState,
    TrafficObservation,
)

AREA = hashlib.sha256(b"prepared-scenario-area").hexdigest()
POPULATION_SOURCE = hashlib.sha256(b"population-source").hexdigest()
POPULATION_FRAME = hashlib.sha256(b"prepared-population-frame").hexdigest()
POPULATION_ARTIFACT = hashlib.sha256(b"prepared-population-artifact").hexdigest()
EDUCATION_GOVERNED = hashlib.sha256(b"prepared-education-governed").hexdigest()
EDUCATION_REGISTER = hashlib.sha256(b"prepared-education-register").hexdigest()


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def education_base_source(
    prepared: PreparedSpineAccessConnection,
) -> dict[str, object]:
    assessed = compile_education(prepared.candidate_set)
    source = assessed.source_snapshot
    base = assess_education_access(
        register_evidence=source.register_evidence,
        schools=source.schools,
        strategic_destinations=source.strategic_education_destinations,
        option_evidence=(),
    )
    return base.source_snapshot.model_dump(mode="json")


def governed_lineage(
    prepared: PreparedSpineAccessConnection,
) -> dict[str, object]:
    return {
        "population": {
            "source_content_sha256": POPULATION_SOURCE,
            "frame_content_sha256": POPULATION_FRAME,
            "artifact_lineage": [{"content_sha256": POPULATION_ARTIFACT}],
        },
        "education": {
            "governed_source_fingerprint": EDUCATION_GOVERNED,
            "source_snapshot": education_base_source(prepared),
            "school_register_lineage": {"content_sha256": EDUCATION_REGISTER},
            "admissions_lineage": None,
        },
    }


def evidence_fingerprints() -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                POPULATION_SOURCE,
                POPULATION_FRAME,
                POPULATION_ARTIFACT,
                EDUCATION_GOVERNED,
                EDUCATION_REGISTER,
            }
        )
    )


def connection(
    label: str = "one",
    *,
    ambiguous: bool = False,
    selection_profile=None,
    gap: Literal["no-options", "all-rejected"] | None = None,
) -> PreparedSpineAccessConnection:
    endpoints = (f"community-{label}", f"parent-community-{label}")
    candidates = []
    if gap != "no-options":
        candidates.append(
            candidate(
                f"prepared-{label}-one",
                role="community-access",
                endpoints=endpoints,
                places=endpoints,
            )
        )
    if ambiguous:
        candidates.append(
            candidate(
                f"prepared-{label}-two",
                role="community-access",
                endpoints=endpoints,
                places=endpoints,
            )
        )
    if gap == "no-options":
        candidate_set_value = admit_candidate_set(
            selection_profile or profile(),
            network_role="community-access",
            endpoints=endpoints,
            candidates=(),
            mandatory_network_place_ids=endpoints,
            mandatory_access_obligation_ids=("secondary-school",),
        )
    elif gap == "all-rejected":
        candidate_set_value = admit_candidate_set(
            selection_profile or profile(),
            network_role="community-access",
            endpoints=endpoints,
            candidates=tuple(candidates),
            mandatory_network_place_ids=endpoints,
            mandatory_access_obligation_ids=("secondary-school",),
            mandatory_strategic_destination_ids=("university-campus",),
        )
    else:
        candidate_set_value = candidate_set(
            *candidates,
            selection_profile=selection_profile or profile(),
            places=endpoints,
        )
    return PreparedSpineAccessConnection(
        access_connection_id=f"prepared-community-access-{label}",
        candidate_set=candidate_set_value,
        root_spine_id="root-spine",
        strategic_source_id="source",
        strategic_evidence_id="evidence",
        strategic_provenance={},
        obligation_kind="community",
        parent_role="spine-access-connection",
        community_id=endpoints[0],
        place_id=endpoints[0],
        parent_place_id=endpoints[1],
        candidate_generation_rationales=(),
        candidate_records=(),
    )


def reuse_first_profile(
    *, detour_limit: float = 1.5, traffic_profile: dict[str, object] | None = None
) -> NetworkSelectionProfile:
    payload: dict[str, object] = {
            "contract": "satn-network-selection-profile/vNext",
            "profile_id": "prepared-reuse-first",
            "version": "2026-08-02",
            "candidate_class_order": [
                "existing-cycle-provision",
                "upgradeable-off-carriageway",
                "low-traffic-non-a-road",
                "a-road-major-protected-infrastructure",
            ],
            "intervention_state_order": [
                "existing-provision",
                "upgrade-required",
                "proposed-new-link",
            ],
            "comparator_order": [
                "mandatory-obligation-service",
                "reuse-class",
                "intervention-state",
                "route-detour",
                "route-effort",
                "transition-fragmentation-burden",
                "governed-constraints",
                "traffic-challenge",
                "stable-candidate-id",
            ],
            "material_difference_rules": [
                {"dimension": "route-effort", "threshold": 100, "unit": "m"}
            ],
            "displacement_rules": [
                {
                    "reason_code": "detour-limit-exceeded",
                    "predicate": "detour-ratio-exceeds-threshold",
                    "threshold": detour_limit,
                    "unit": "ratio",
                    "evidence_requirements": ["route-length-evidence"],
                }
            ],
            "unknown_value_policy": "retain-and-request-evidence",
            "deterministic_tie_break": "stable-candidate-id",
            "agent_call_bound": 0,
            "maximum_options_per_candidate_set": 12,
            "maximum_hybrid_candidates_per_set": 2,
            "maximum_transitions_per_candidate": 2,
    }
    if traffic_profile is not None:
        payload["traffic_profile"] = traffic_profile
    return NetworkSelectionProfile.model_validate(payload)


def reuse_candidate(
    label: str,
    *,
    reuse_class: str,
    intervention_state: str,
    alignment_basis: str,
    route_length_m: float,
    total_absolute_elevation_change_m: float | None = 20.0,
) -> AlignmentCandidateInput:
    base = candidate(
        label,
        role="community-access",
        endpoints=("community-reuse", "parent-community-reuse"),
        places=("community-reuse", "parent-community-reuse"),
        source="other-routable",
        directness=route_length_m,
    )
    return AlignmentCandidateInput.model_validate(
        base.model_dump(mode="python", exclude={"candidate_id"})
        | {
            "reuse_class": reuse_class,
            "intervention_state": intervention_state,
            "alignment_bases": [alignment_basis],
            "primary_alignment_basis": alignment_basis,
            "total_absolute_elevation_change_m": total_absolute_elevation_change_m,
            "transition_count": 0,
            "fragmentation_count": 0,
            "governed_evidence_ids": [f"evidence-{label}"],
        }
    )


def high_traffic_on_carriageway_candidate(
    *,
    protected_state: str = "absent",
    include_observation: bool = True,
    observation_year: int = 2025,
    reported_freshness: TrafficFreshnessState = TrafficFreshnessState.FRESH,
    estimation_method: str = "Counted",
) -> AlignmentCandidateInput:
    base = reuse_candidate(
        "traffic-high",
        reuse_class="a-road-major-protected-infrastructure",
        intervention_state="upgrade-required",
        alignment_basis="a-road",
        route_length_m=1_000.0,
    )
    annotations: dict[str, object] = {
        "traffic_exposure": "on-carriageway",
        "protected_space_evidence": ProtectedSpaceEvidence(
            state=protected_state,
            evidence_ids=("protected-evidence-123",),
            provenance_ids=("protected-provenance-123",),
        ),
    }
    if include_observation:
        annotations["traffic_observation"] = TrafficObservation(
                observation_id="traffic-observation-traffic-high",
                source_export_fingerprint=hashlib.sha256(b"dft-export").hexdigest(),
                source_layer="aadf",
                count_point_id="cp-123",
                observation_year=observation_year,
                all_motor_vehicles=12_000,
                estimation_method=estimation_method,
                freshness_state=reported_freshness,
                match_state="matched",
                coverage_status="sampled",
                row_fingerprint=hashlib.sha256(b"dft-row").hexdigest(),
                evidence_ids=("traffic-evidence-123",),
                provenance_ids=("traffic-provenance-123",),
            )
    return AlignmentCandidateInput.model_validate(
        base.model_dump(mode="python", exclude={"candidate_id"}) | annotations
    )


def test_legacy_candidate_without_traffic_annotations_keeps_exact_identity_and_dump_shape() -> None:
    legacy = candidate("legacy-fingerprint")

    assert legacy.candidate_id == "candidate-6d43843933834b51bea0"
    payload = legacy.model_dump(mode="json")
    assert not {
        "reuse_class",
        "intervention_state",
        "alignment_bases",
        "primary_alignment_basis",
        "total_absolute_elevation_change_m",
        "transition_count",
        "fragmentation_count",
        "governed_evidence_ids",
        "traffic_observation",
        "protected_space_evidence",
        "traffic_exposure",
    } & payload.keys()


def test_on_carriageway_is_not_an_alignment_basis() -> None:
    with pytest.raises(ValidationError, match="Alignment Basis"):
        candidate_value = candidate("invalid-basis")
        AlignmentCandidateInput.model_validate(
            candidate_value.model_dump(mode="python", exclude={"candidate_id"})
            | {
                "alignment_bases": ("on-carriageway",),
                "primary_alignment_basis": "on-carriageway",
                "traffic_exposure": "on-carriageway",
            }
        )


def test_alignment_basis_uses_the_closed_governed_vocabulary() -> None:
    vocabulary = (
        "current-ncn",
        "ncn-link",
        "greenway",
        "cycle-track",
        "shared-use-path",
        "reclassified-ncn",
        "public-bridleway",
        "restricted-byway",
        "public-footpath",
        "byway-open-to-all-traffic",
        "prow-class-unknown",
        "former-railway",
        "local-connector",
        "a-road",
        "b-road",
        "classified-unnumbered-road",
        "unclassified-road",
        "proposed-new-corridor",
    )
    for basis in vocabulary:
        value = candidate(f"basis-{basis}")
        AlignmentCandidateInput.model_validate(
            value.model_dump(mode="python", exclude={"candidate_id"})
            | {"alignment_bases": (basis,), "primary_alignment_basis": basis}
        )
    with pytest.raises(ValidationError, match="Alignment Basis"):
        value = candidate("basis-invented")
        AlignmentCandidateInput.model_validate(
            value.model_dump(mode="python", exclude={"candidate_id"})
            | {
                "alignment_bases": ("invented-corridor",),
                "primary_alignment_basis": "invented-corridor",
            }
        )


def reuse_connection(
    *candidates: AlignmentCandidateInput,
    selection_profile: NetworkSelectionProfile,
) -> PreparedSpineAccessConnection:
    candidate_set_value = candidate_set(
        *candidates,
        selection_profile=selection_profile,
        places=("community-reuse", "parent-community-reuse"),
    )
    return PreparedSpineAccessConnection(
        access_connection_id="prepared-community-access-reuse",
        candidate_set=candidate_set_value,
        root_spine_id="root-spine",
        strategic_source_id="source",
        strategic_evidence_id="evidence",
        strategic_provenance={},
        obligation_kind="community",
        parent_role="spine-access-connection",
        community_id="community-reuse",
        place_id="community-reuse",
        parent_place_id="parent-community-reuse",
        candidate_generation_rationales=(),
        candidate_records=(),
    )


def roster_for(
    item: PreparedSpineAccessConnection,
) -> PreparedConnectionRosterRecord:
    return PreparedConnectionRosterRecord(
        access_connection_id=item.access_connection_id,
        obligation_kind=item.obligation_kind,
        parent_role=item.parent_role,
        community_id=item.community_id,
        place_id=item.place_id,
        parent_place_id=item.parent_place_id,
        disposition=(
            "prepared-candidate-set"
            if item.candidate_set.admitted_candidates
            else "prepared-candidate-set-gap"
        ),
    )


def preparation(
    *items: PreparedSpineAccessConnection,
    roster: tuple[PreparedConnectionRosterRecord, ...] | None = None,
    issues: tuple[CandidatePreparationIssue, ...] = (),
    status: str = "prepared",
    fingerprints: tuple[str, ...] | None = None,
) -> SpineAccessCandidatePreparationResult:
    lineage_item = items[0] if items else connection("lineage-source")
    roster = roster if roster is not None else tuple(roster_for(item) for item in items)
    diagnostics = {
        "expected_connection_roster_count": len(roster),
        "prepared_connection_count": sum(
            item.disposition.startswith("prepared-") for item in roster
        ),
        "out_of_scope_connection_count": sum(
            item.disposition == "out-of-scope-direct-strategic-spine"
            for item in roster
        ),
        "unresolved_connection_count": sum(
            item.disposition == "unresolved-gap" for item in roster
        ),
    }
    unbound = SpineAccessCandidatePreparationResult(
        contract="satn-spine-access-candidate-preparation/v1",
        profile_fingerprint=(items[0].candidate_set.profile_fingerprint if items else "a" * 64),
        status=status,
        prepared_spine_access_connections=tuple(items),
        connection_roster=roster,
        generation_issues=issues,
        missing_inputs=(),
        evidence_fingerprints=(
            evidence_fingerprints() if fingerprints is None else fingerprints
        ),
        evidence_lineage=governed_lineage(lineage_item),
        preparation_fingerprint="0" * 64,
        diagnostics=diagnostics,
    )
    return replace(
        unbound,
        preparation_fingerprint=canonical_hash(unbound.canonical_payload()),
    )


def bound_snapshot(
    prepared: PreparedSpineAccessConnection,
    snapshot: GovernedEvidenceSnapshot,
    *,
    use_base_education_source: bool = False,
) -> GovernedEvidenceSnapshot:
    education_source = (
        education_base_source(prepared)["source_content_fingerprint"]
        if use_base_education_source
        else EDUCATION_GOVERNED
    )
    expected = {
        AssessmentKind.POPULATION_REACH: POPULATION_SOURCE,
        AssessmentKind.EDUCATION_ACCESS: education_source,
        AssessmentKind.NETWORK_GEOMETRY: (
            prepared_network_geometry_source_fingerprint(prepared)
        ),
        AssessmentKind.TOPOGRAPHY: prepared_topography_source_fingerprint(prepared),
    }
    assessment_ids = (
        {
            AssessmentKind.NETWORK_GEOMETRY: (
                f"network-{prepared.access_connection_id}"
            ),
            AssessmentKind.TOPOGRAPHY: f"topography-{prepared.access_connection_id}",
        }
        if prepared.access_connection_id != "prepared-community-access-one"
        else {}
    )
    return GovernedEvidenceSnapshot(
        snapshot_id=f"bound-{prepared.access_connection_id}",
        assessments=tuple(
            GovernedAssessmentBinding(
                kind=item.kind,
                assessment_id=assessment_ids.get(item.kind, item.assessment_id),
                assessment_content_sha256=item.assessment_content_sha256,
                source_content_sha256=expected[item.kind],
                method_version=item.method_version,
            )
            for item in snapshot.assessments
        ),
    )


def bound_criteria(
    prepared: PreparedSpineAccessConnection,
    base: CandidateCriteria | None = None,
    **changes,
) -> CandidateCriteria:
    base = base or criteria(prepared.candidate_set, **changes)
    snapshot = bound_snapshot(prepared, base.evidence_snapshot)
    network_assessment = snapshot.assessment(AssessmentKind.NETWORK_GEOMETRY)
    topography_assessment = snapshot.assessment(AssessmentKind.TOPOGRAPHY)
    assert network_assessment is not None and topography_assessment is not None
    governed = base.education.governed_binding
    governed_binding = GovernedEducationCriterionBinding(
        school_ids=governed.school_ids,
        strategic_destination_ids=governed.strategic_destination_ids,
        full_source_governed_fingerprint=EDUCATION_GOVERNED,
        governed_input_fingerprint=(
            governed_education_assessment_fingerprint(
                governed_source_fingerprint=EDUCATION_GOVERNED,
                school_ids=governed.school_ids,
                strategic_destination_ids=(
                    governed.strategic_destination_ids
                ),
                assessment_content_sha256=(
                    governed.assessment_content_sha256
                ),
            )
        ),
        assessment_content_sha256=governed.assessment_content_sha256,
    )
    return CandidateCriteria(
        evidence_snapshot=snapshot,
        population=base.population.model_copy(
            update={
                "scenario_evidence_snapshot_fingerprint": snapshot.snapshot_fingerprint
            }
        ),
        education=base.education.model_copy(
            update={
                "scenario_evidence_snapshot_fingerprint": (
                    snapshot.snapshot_fingerprint
                ),
                "governed_binding": governed_binding,
            }
        ),
        existing_alignment=base.existing_alignment,
        directness=tuple(
            item.model_copy(
                update={"assessment_id": network_assessment.assessment_id}
            )
            for item in base.directness
        ),
        gradient=tuple(
            item.model_copy(
                update={"assessment_id": topography_assessment.assessment_id}
            )
            for item in base.gradient
        ),
        uncertainty=tuple(
            item.model_copy(
                update={"assessment_id": network_assessment.assessment_id}
            )
            for item in base.uncertainty
        ),
    )


def gap_evidence(prepared: PreparedSpineAccessConnection) -> CandidateSetGapEvidence:
    seed = connection("seed")
    snapshot = bound_snapshot(
        prepared,
        criteria(seed.candidate_set).evidence_snapshot,
        use_base_education_source=True,
    )
    candidate_set_value = prepared.candidate_set
    return CandidateSetGapEvidence(
        candidate_set=candidate_set_value,
        evidence_snapshot=snapshot,
        rejected_candidate_ids=tuple(
            item.candidate_id
            for item in candidate_set_value.admissions
            if item.disposition.value == "rejected"
        ),
        unsatisfied_network_place_ids=candidate_set_value.mandatory_network_place_ids,
        unsatisfied_access_obligation_ids=(
            candidate_set_value.mandatory_access_obligation_ids
        ),
        unsatisfied_strategic_destination_ids=(
            candidate_set_value.mandatory_strategic_destination_ids
        ),
        generation_gap_reason=candidate_set_value.generation_gap_reason,
    )


def request(
    packets: tuple[PreparedCandidateCriteria, ...] = (),
    *,
    decision_record=None,
    run_id: str = "prepared-scenario-review",
    prior=None,
) -> PreparedScenarioCompilationInput:
    return PreparedScenarioCompilationInput(
        area_fingerprint=AREA,
        criteria=packets,
        decision_record=decision_record,
        review_run_instance_id=run_id,
        prior_orchestration=prior,
    )


def packet(
    prepared: PreparedSpineAccessConnection,
    criterion: CandidateCriteria | CandidateSetGapEvidence,
    *,
    source_preparation: SpineAccessCandidatePreparationResult | None = None,
) -> PreparedCandidateCriteria:
    source_preparation = source_preparation or preparation(prepared)
    return PreparedCandidateCriteria(
        access_connection_id=prepared.access_connection_id,
        criteria=criterion,
        preparation_lineage=PreparedCriteriaLineage.from_preparation(
            source_preparation
        ),
    )


def test_profile_disabled_is_a_no_op_artifact() -> None:
    result = compile_prepared_scenario(None, request())

    assert result.status == "disabled"
    assert result.scenario is None
    assert result.reference_satn_created is False
    assert result.can_mutate_authoritative_network is False


def test_reuse_first_scenario_prefers_existing_cycle_provision_to_shorter_a_road() -> None:
    profile_value = reuse_first_profile()
    cycleway = reuse_candidate(
        "reuse-cycleway",
        reuse_class="existing-cycle-provision",
        intervention_state="existing-provision",
        alignment_basis="cycle-track",
        route_length_m=1_200.0,
    )
    a_road = reuse_candidate(
        "reuse-a-road",
        reuse_class="a-road-major-protected-infrastructure",
        intervention_state="upgrade-required",
        alignment_basis="a-road",
        route_length_m=1_000.0,
    )
    prepared = reuse_connection(
        cycleway,
        a_road,
        selection_profile=profile_value,
    )
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    assert result.status == "compiled"
    assert result.scenario is not None
    assert result.scenario.selections[0].selected_candidate_id == cycleway.candidate_id


def test_reuse_first_scenario_records_configured_detour_displacement() -> None:
    profile_value = reuse_first_profile(detour_limit=1.5)
    cycleway = reuse_candidate(
        "detour-cycleway",
        reuse_class="existing-cycle-provision",
        intervention_state="existing-provision",
        alignment_basis="cycle-track",
        route_length_m=1_600.0,
    )
    a_road = reuse_candidate(
        "detour-a-road",
        reuse_class="a-road-major-protected-infrastructure",
        intervention_state="upgrade-required",
        alignment_basis="a-road",
        route_length_m=1_000.0,
    )
    prepared = reuse_connection(
        cycleway,
        a_road,
        selection_profile=profile_value,
    )
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    assert result.scenario is not None
    selection = result.scenario.selections[0]
    assert selection.selected_candidate_id == a_road.candidate_id
    assert len(selection.material_displacements) == 1
    displacement = selection.material_displacements[0]
    assert displacement.reason_code == "detour-limit-exceeded"
    assert displacement.selected_candidate_id == a_road.candidate_id
    assert displacement.displaced_candidate_id == cycleway.candidate_id
    assert displacement.observed_values == {
        "displaced_route_length_m": 1_600.0,
        "selected_route_length_m": 1_000.0,
        "detour_ratio": 1.6,
    }
    assert displacement.threshold == 1.5
    assert displacement.unit == "ratio"
    assert displacement.evidence_ids == (
        "evidence-detour-a-road",
        "evidence-detour-cycleway",
    )
    assert displacement.profile_fingerprint == profile_value.fingerprint
    assert displacement.decision_provenance == "deterministic-profile"


def test_high_traffic_on_carriageway_without_protected_space_is_a_non_veto_diagnostic() -> None:
    profile_value = reuse_first_profile(
        traffic_profile={
            "profile_id": "prepared-dft-traffic",
            "version": "2026-08-02",
            "metric": "all_motor_vehicles",
            "thresholds": [
                {"id": "low", "upper_vehicles_per_day": 1_000},
                {"id": "high", "upper_vehicles_per_day": None},
            ],
            "high_traffic_challenge_band": "high",
            "max_observation_age_years": 3,
            "as_at_year": 2026,
            "stale_value_policy": "retain-and-diagnose",
            "missing_policy": "explicit-unknown",
        }
    )
    candidate_value = high_traffic_on_carriageway_candidate()
    competing = reuse_candidate(
        "traffic-competing-existing",
        reuse_class="existing-cycle-provision",
        intervention_state="existing-provision",
        alignment_basis="cycle-track",
        route_length_m=1_200.0,
    )
    prepared = reuse_connection(
        competing,
        candidate_value,
        selection_profile=profile_value,
    )
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    assert result.scenario is not None
    selected_with_traffic = result.scenario.selections[0].selected_candidate_id
    assert selected_with_traffic == competing.candidate_id
    assert candidate_value.candidate_id in {
        item.candidate_id for item in result.scenario.candidate_sets[0].admitted_candidates
    }
    diagnostics = result.diagnostics["traffic_diagnostics"]
    assert isinstance(diagnostics, tuple)
    challenge = next(
        item
        for item in diagnostics
        if item["diagnostic_id"] == "traffic-high-on-carriageway-without-protected-space"
    )
    assert challenge["traffic_observation_id"] == "traffic-observation-traffic-high"
    assert challenge["traffic_profile_fingerprint"] == profile_value.traffic_profile.fingerprint
    assert challenge["evidence_ids"] == (
        "protected-evidence-123",
        "traffic-evidence-123",
    )
    assert challenge["provenance_ids"] == (
        "protected-provenance-123",
        "traffic-provenance-123",
    )

    baseline_candidate = reuse_candidate(
        "traffic-high",
        reuse_class="a-road-major-protected-infrastructure",
        intervention_state="upgrade-required",
        alignment_basis="a-road",
        route_length_m=1_000.0,
    )
    baseline_prepared = reuse_connection(
        competing,
        baseline_candidate,
        selection_profile=profile_value,
    )
    baseline_source_preparation = preparation(baseline_prepared)
    baseline = compile_prepared_scenario(
        baseline_source_preparation,
        request(
            (
                packet(
                    baseline_prepared,
                    bound_criteria(baseline_prepared),
                    source_preparation=baseline_source_preparation,
                ),
            )
        ),
    )
    assert baseline.scenario is not None
    assert baseline.scenario.selections[0].selected_candidate_id == selected_with_traffic


def test_configured_on_carriageway_without_observation_emits_explicit_unknown_traffic() -> None:
    profile_value = reuse_first_profile(
        traffic_profile={
            "profile_id": "prepared-dft-traffic-missing-observation",
            "version": "2026-08-02",
            "metric": "all_motor_vehicles",
            "thresholds": [
                {"id": "high", "upper_vehicles_per_day": None},
            ],
            "high_traffic_challenge_band": "high",
            "max_observation_age_years": 3,
            "as_at_year": 2026,
            "stale_value_policy": "retain-and-diagnose",
            "missing_policy": "explicit-unknown",
        }
    )
    candidate_value = high_traffic_on_carriageway_candidate(include_observation=False)
    prepared = reuse_connection(candidate_value, selection_profile=profile_value)
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    assert result.scenario is not None
    assert result.scenario.selections[0].selected_candidate_id == candidate_value.candidate_id
    diagnostics = result.diagnostics["traffic_diagnostics"]
    assert diagnostics[0]["diagnostic_id"] == "traffic-unknown"
    assert diagnostics[0]["traffic_status"] == "unknown"
    assert "all_motor_vehicles" not in diagnostics[0]
    assert 0 not in diagnostics[0].values()


def test_traffic_freshness_is_derived_from_profile_as_at_and_observation_year() -> None:
    profile_value = reuse_first_profile(
        traffic_profile={
            "profile_id": "prepared-dft-traffic-stale",
            "version": "2026-08-02",
            "metric": "all_motor_vehicles",
            "thresholds": [{"id": "high", "upper_vehicles_per_day": None}],
            "high_traffic_challenge_band": "high",
            "max_observation_age_years": 3,
            "as_at_year": 2026,
            "stale_value_policy": "retain-and-diagnose",
            "missing_policy": "explicit-unknown",
        }
    )
    candidate_value = high_traffic_on_carriageway_candidate(
        observation_year=2020,
        reported_freshness=TrafficFreshnessState.FRESH,
    )
    prepared = reuse_connection(candidate_value, selection_profile=profile_value)
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    diagnostics = result.diagnostics["traffic_diagnostics"]
    diagnostic_ids = tuple(item["diagnostic_id"] for item in diagnostics)
    assert "traffic-stale" in diagnostic_ids
    assert "traffic-high-on-carriageway-without-protected-space" in diagnostic_ids
    stale = next(item for item in diagnostics if item["diagnostic_id"] == "traffic-stale")
    assert stale["freshness_state"] == "stale"
    assert stale["traffic_observation_id"] == "traffic-observation-traffic-high"
    assert stale["traffic_profile_fingerprint"] == profile_value.traffic_profile.fingerprint
    assert stale["evidence_ids"] == (
        "protected-evidence-123",
        "traffic-evidence-123",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("match_state", "ambiguous"),
        ("match_state", "unmatched"),
        ("match_state", "unknown"),
        ("coverage_status", "not_sampled"),
        ("coverage_status", "unknown"),
    ],
)
def test_unmatched_or_unresolved_traffic_observation_is_explicit_unknown(
    field: str,
    value: str,
) -> None:
    profile_value = reuse_first_profile(
        traffic_profile={
            "profile_id": "prepared-dft-traffic-unresolved",
            "version": "2026-08-02",
            "metric": "all_motor_vehicles",
            "thresholds": [{"id": "high", "upper_vehicles_per_day": None}],
            "high_traffic_challenge_band": "high",
            "max_observation_age_years": 3,
            "as_at_year": 2026,
            "stale_value_policy": "retain-and-diagnose",
            "missing_policy": "explicit-unknown",
        }
    )
    base = high_traffic_on_carriageway_candidate()
    observation = base.traffic_observations[0]
    unresolved = observation.model_dump(mode="python")
    unresolved[field] = value
    candidate_value = AlignmentCandidateInput.model_validate(
        base.model_dump(mode="python", exclude={"candidate_id", "traffic_observations"})
        | {"traffic_observations": (TrafficObservation(**unresolved),)}
    )
    prepared = reuse_connection(candidate_value, selection_profile=profile_value)
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    assert result.scenario is not None
    diagnostic = result.diagnostics["traffic_diagnostics"][0]
    assert diagnostic["diagnostic_id"] == "traffic-unknown"
    assert diagnostic["traffic_status"] == "unknown"


def test_conflicting_traffic_observation_roster_is_retained_and_never_averaged() -> None:
    profile_value = reuse_first_profile(
        traffic_profile={
            "profile_id": "prepared-dft-traffic-conflict",
            "version": "2026-08-02",
            "metric": "all_motor_vehicles",
            "thresholds": [{"id": "high", "upper_vehicles_per_day": None}],
            "high_traffic_challenge_band": "high",
            "max_observation_age_years": 3,
            "as_at_year": 2026,
            "stale_value_policy": "retain-and-diagnose",
            "missing_policy": "explicit-unknown",
        }
    )
    base = high_traffic_on_carriageway_candidate()
    first = base.traffic_observation
    assert first is not None
    first_payload = first.model_dump(mode="python")
    first_payload["direction_of_travel"] = "combined"
    first = TrafficObservation(**first_payload)
    second_payload = first.model_dump(mode="python")
    second_payload.update(
        {
            "observation_id": "traffic-observation-traffic-conflict",
            "source_export_fingerprint": hashlib.sha256(b"dft-export-2").hexdigest(),
            "all_motor_vehicles": 13_000,
            "row_fingerprint": hashlib.sha256(b"dft-row-2").hexdigest(),
        }
    )
    candidate_value = AlignmentCandidateInput.model_validate(
        base.model_dump(mode="python", exclude={"candidate_id", "traffic_observation"})
        | {"traffic_observations": (first, TrafficObservation(**second_payload))}
    )
    prepared = reuse_connection(candidate_value, selection_profile=profile_value)
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    assert result.scenario is not None
    assert candidate_value.candidate_id in {
        item.candidate_id for item in result.scenario.candidate_sets[0].admitted_candidates
    }
    conflict = next(
        item
        for item in result.diagnostics["traffic_diagnostics"]
        if item["diagnostic_id"] == "traffic-conflict"
    )
    assert conflict["traffic_observation_ids"] == (
        "traffic-observation-traffic-conflict",
        "traffic-observation-traffic-high",
    )
    assert conflict["source_export_fingerprints"] == tuple(
        sorted(
            (
                hashlib.sha256(b"dft-export").hexdigest(),
                hashlib.sha256(b"dft-export-2").hexdigest(),
            )
        )
    )
    assert conflict["all_motor_vehicles"] is None
    assert conflict["field_differences"] == ("all_motor_vehicles",)
    assert "traffic-unknown" not in {
        item["diagnostic_id"] for item in result.diagnostics["traffic_diagnostics"]
    }


def test_distinct_direction_observations_are_retained_without_conflict_or_aggregation() -> None:
    profile_value = reuse_first_profile(
        traffic_profile={
            "profile_id": "prepared-dft-traffic-distinct-directions",
            "version": "2026-08-02",
            "metric": "all_motor_vehicles",
            "thresholds": [{"id": "high", "upper_vehicles_per_day": None}],
            "high_traffic_challenge_band": "high",
            "max_observation_age_years": 3,
            "as_at_year": 2026,
            "stale_value_policy": "retain-and-diagnose",
            "missing_policy": "explicit-unknown",
        }
    )
    base = high_traffic_on_carriageway_candidate()
    first = base.traffic_observation
    assert first is not None
    first_payload = first.model_dump(mode="python")
    first_payload["direction_of_travel"] = "N"
    second_payload = first.model_dump(mode="python")
    second_payload.update(
        {
            "observation_id": "traffic-observation-traffic-south",
            "direction_of_travel": "S",
            "all_motor_vehicles": 13_000,
            "row_fingerprint": hashlib.sha256(b"dft-row-south").hexdigest(),
        }
    )
    candidate_value = AlignmentCandidateInput.model_validate(
        base.model_dump(mode="python", exclude={"candidate_id", "traffic_observation"})
        | {
            "traffic_observations": (
                TrafficObservation(**first_payload),
                TrafficObservation(**second_payload),
            )
        }
    )
    prepared = reuse_connection(candidate_value, selection_profile=profile_value)
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    diagnostics = result.diagnostics["traffic_diagnostics"]
    assert "traffic-conflict" not in {
        item["diagnostic_id"] for item in diagnostics
    }
    unknown = next(item for item in diagnostics if item["diagnostic_id"] == "traffic-unknown")
    assert unknown["traffic_status"] == "multiple-observations-no-combined"
    assert unknown["traffic_observation_ids"] == (
        "traffic-observation-traffic-high",
        "traffic-observation-traffic-south",
    )


def test_identical_same_claim_observations_are_deduped_without_conflict() -> None:
    profile_value = reuse_first_profile(
        traffic_profile={
            "profile_id": "prepared-dft-traffic-duplicate",
            "version": "2026-08-02",
            "metric": "all_motor_vehicles",
            "thresholds": [{"id": "high", "upper_vehicles_per_day": None}],
            "high_traffic_challenge_band": "high",
            "max_observation_age_years": 3,
            "as_at_year": 2026,
            "stale_value_policy": "retain-and-diagnose",
            "missing_policy": "explicit-unknown",
        }
    )
    base = high_traffic_on_carriageway_candidate()
    first = base.traffic_observation
    assert first is not None
    first = TrafficObservation(
        **(first.model_dump(mode="python") | {"direction_of_travel": "combined"})
    )
    duplicate_payload = first.model_dump(mode="python")
    duplicate_payload.update(
        {
            "observation_id": "traffic-observation-duplicate",
            "source_export_fingerprint": hashlib.sha256(b"dft-export-duplicate").hexdigest(),
            "row_fingerprint": hashlib.sha256(b"dft-row-duplicate").hexdigest(),
            "evidence_ids": ("traffic-evidence-duplicate",),
            "provenance_ids": ("traffic-provenance-duplicate",),
        }
    )
    candidate_value = AlignmentCandidateInput.model_validate(
        base.model_dump(mode="python", exclude={"candidate_id", "traffic_observation"})
        | {
            "traffic_observations": (first, TrafficObservation(**duplicate_payload))
        }
    )
    prepared = reuse_connection(candidate_value, selection_profile=profile_value)
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    diagnostics = result.diagnostics["traffic_diagnostics"]
    assert "traffic-conflict" not in {
        item["diagnostic_id"] for item in diagnostics
    }
    challenge = next(
        item
        for item in diagnostics
        if item["diagnostic_id"] == "traffic-high-on-carriageway-without-protected-space"
    )
    assert challenge["traffic_observation_ids"] == (
        "traffic-observation-duplicate",
        "traffic-observation-traffic-high",
    )
    assert challenge["provenance_ids"] == (
        "protected-provenance-123",
        "traffic-provenance-123",
        "traffic-provenance-duplicate",
    )


def test_conflicting_directional_claim_does_not_contaminate_distinct_direction() -> None:
    profile_value = reuse_first_profile(
        traffic_profile={
            "profile_id": "prepared-dft-traffic-local-conflict",
            "version": "2026-08-02",
            "metric": "all_motor_vehicles",
            "thresholds": [{"id": "high", "upper_vehicles_per_day": None}],
            "high_traffic_challenge_band": "high",
            "max_observation_age_years": 3,
            "as_at_year": 2026,
            "stale_value_policy": "retain-and-diagnose",
            "missing_policy": "explicit-unknown",
        }
    )
    base = high_traffic_on_carriageway_candidate()
    first = base.traffic_observation
    assert first is not None
    conflicting = TrafficObservation(
        **(
            first.model_dump(mode="python")
            | {"direction_of_travel": "N", "match_state": "conflicting"}
        )
    )
    distinct = TrafficObservation(
        **(
            first.model_dump(mode="python")
            | {
                "observation_id": "traffic-observation-south-usable",
                "direction_of_travel": "S",
            }
        )
    )
    candidate_value = AlignmentCandidateInput.model_validate(
        base.model_dump(mode="python", exclude={"candidate_id", "traffic_observation"})
        | {"traffic_observations": (conflicting, distinct)}
    )
    prepared = reuse_connection(candidate_value, selection_profile=profile_value)
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    diagnostics = result.diagnostics["traffic_diagnostics"]
    diagnostic_ids = {item["diagnostic_id"] for item in diagnostics}
    assert "traffic-conflict" in diagnostic_ids
    assert "traffic-high-on-carriageway-without-protected-space" in diagnostic_ids


def test_normalized_traffic_observation_retains_direction_dates_link_identity_and_crs() -> None:
    with pytest.raises(ValidationError, match="direction"):
        TrafficObservation(
            observation_id="traffic-direction-missing",
            source_export_fingerprint=hashlib.sha256(b"dft-export").hexdigest(),
            source_layer="aadf-by-direction",
            count_point_id="cp-123",
            observation_year=2025,
            all_motor_vehicles=12_000,
            row_fingerprint=hashlib.sha256(b"dft-row").hexdigest(),
        )
    observation = TrafficObservation(
        observation_id="traffic-direction-combined",
        source_export_fingerprint=hashlib.sha256(b"dft-export").hexdigest(),
        source_layer="aadf-by-direction",
        count_point_id="cp-123",
        observation_year=2025,
        count_date="2025-06-01",
        direction_of_travel="combined",
        road_name="A36",
        road_category="PA",
        road_type="Major",
        start_junction_road_name="Bathwick Hill",
        end_junction_road_name="Cleveland Bridge",
        latitude=51.38,
        longitude=-2.34,
        declared_crs="EPSG:4326",
        geometry_fingerprint=hashlib.sha256(b"cp-geometry").hexdigest(),
        link_length_km=1.2,
        all_motor_vehicles=12_000,
        row_fingerprint=hashlib.sha256(b"dft-row").hexdigest(),
    )
    assert observation.direction_of_travel == "combined"
    assert observation.count_date.isoformat() == "2025-06-01"
    assert observation.road_name == "A36"
    assert observation.declared_crs == "EPSG:4326"
    assert observation.geometry_fingerprint == hashlib.sha256(b"cp-geometry").hexdigest()
    assert observation.link_length_km == 1.2


def test_on_carriageway_traffic_evidence_without_profile_is_explicit_unknown() -> None:
    profile_value = reuse_first_profile()
    candidate_value = high_traffic_on_carriageway_candidate()
    prepared = reuse_connection(candidate_value, selection_profile=profile_value)
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    assert result.scenario is not None
    diagnostics = result.diagnostics["traffic_diagnostics"]
    assert diagnostics[0]["diagnostic_id"] == "traffic-unknown"
    assert diagnostics[0]["traffic_status"] == "profile-unavailable"


def test_singular_conflicting_traffic_observation_has_typed_roster_without_profile() -> None:
    profile_value = reuse_first_profile()
    base = high_traffic_on_carriageway_candidate()
    observation = base.traffic_observation
    assert observation is not None
    conflicting = TrafficObservation(
        **(observation.model_dump(mode="python") | {"match_state": "conflicting"})
    )
    candidate_value = AlignmentCandidateInput.model_validate(
        base.model_dump(mode="python", exclude={"candidate_id", "traffic_observation"})
        | {"traffic_observations": (conflicting,)}
    )

    diagnostics = traffic_diagnostics_for_candidate(
        candidate_value,
        profile_value,
    )

    assert diagnostics[0]["diagnostic_id"] == "traffic-conflict"
    assert diagnostics[0]["traffic_status"] == "conflicting"
    assert diagnostics[0]["traffic_conflict_evidence"]["observation_ids"] == [
        "traffic-observation-traffic-high",
    ]
    assert diagnostics[0]["traffic_conflict_evidence"]["conflicting_fields"] == [
        "match_state",
    ]


def test_unapplied_traffic_freshness_is_unknown_with_configuration_diagnostic() -> None:
    profile_value = reuse_first_profile(
        traffic_profile={
            "profile_id": "prepared-dft-traffic-unapplied-freshness",
            "version": "2026-08-02",
            "thresholds": [{"id": "high", "upper_vehicles_per_day": None}],
            "high_traffic_challenge_band": "high",
            "max_observation_age_years": 3,
            "stale_value_policy": "retain-and-diagnose",
            "missing_policy": "explicit-unknown",
        }
    )
    candidate_value = high_traffic_on_carriageway_candidate()

    diagnostics = traffic_diagnostics_for_candidate(candidate_value, profile_value)

    assert diagnostics[0]["diagnostic_id"] == "traffic-freshness-configuration"
    assert diagnostics[0]["traffic_status"] == "unknown"
    assert diagnostics[0]["freshness_state"] == "unknown"
    assert diagnostics[0]["freshness_configuration_diagnostic"] == (
        "max-observation-age-without-as-at-year"
    )


def test_estimated_aadf_retains_band_and_emits_traffic_estimated() -> None:
    profile_value = reuse_first_profile(
        traffic_profile={
            "profile_id": "prepared-dft-traffic-estimated",
            "version": "2026-08-02",
            "metric": "all_motor_vehicles",
            "thresholds": [{"id": "high", "upper_vehicles_per_day": None}],
            "high_traffic_challenge_band": "high",
            "max_observation_age_years": 3,
            "as_at_year": 2026,
            "stale_value_policy": "retain-and-diagnose",
            "missing_policy": "explicit-unknown",
        }
    )
    candidate_value = high_traffic_on_carriageway_candidate(
        estimation_method="Estimated"
    )
    prepared = reuse_connection(candidate_value, selection_profile=profile_value)
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    diagnostics = result.diagnostics["traffic_diagnostics"]
    estimated = next(
        item for item in diagnostics if item["diagnostic_id"] == "traffic-estimated"
    )
    assert estimated["traffic_band"] == "high"
    assert estimated["traffic_observation_id"] == "traffic-observation-traffic-high"
    assert estimated["estimation_method"] == "Estimated"


@pytest.mark.parametrize(
    ("protected_state", "expected_diagnostic"),
    [
        ("missing", "protected-space-evidence-unknown"),
        ("stale", "protected-space-evidence-unknown"),
        ("unknown", "protected-space-evidence-unknown"),
        ("conflicting", "protected-space-conflict"),
    ],
)
def test_unknown_protected_space_never_becomes_absence_or_candidate_ineligibility(
    protected_state: str,
    expected_diagnostic: str,
) -> None:
    profile_value = reuse_first_profile(
        traffic_profile={
            "profile_id": "prepared-dft-traffic-unknown-space",
            "version": "2026-08-02",
            "metric": "all_motor_vehicles",
            "thresholds": [
                {"id": "high", "upper_vehicles_per_day": None},
            ],
            "high_traffic_challenge_band": "high",
            "max_observation_age_years": 3,
            "as_at_year": 2026,
            "stale_value_policy": "retain-and-diagnose",
            "missing_policy": "explicit-unknown",
        }
    )
    candidate_value = high_traffic_on_carriageway_candidate(
        protected_state=protected_state
    )
    prepared = reuse_connection(candidate_value, selection_profile=profile_value)
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    assert result.scenario is not None
    assert result.scenario.selections[0].selected_candidate_id == candidate_value.candidate_id
    diagnostic_ids = tuple(
        item["diagnostic_id"] for item in result.diagnostics["traffic_diagnostics"]
    )
    assert expected_diagnostic in diagnostic_ids
    assert "traffic-high-on-carriageway-without-protected-space" not in diagnostic_ids
    assert result.scenario.candidate_sets[0].admitted_candidates


def test_reuse_first_scenario_retains_unknown_optional_effort_without_treating_it_as_zero() -> None:
    profile_value = reuse_first_profile()
    cycleway = reuse_candidate(
        "unknown-effort-cycleway",
        reuse_class="existing-cycle-provision",
        intervention_state="existing-provision",
        alignment_basis="cycle-track",
        route_length_m=1_200.0,
        total_absolute_elevation_change_m=None,
    )
    a_road = reuse_candidate(
        "known-effort-a-road",
        reuse_class="a-road-major-protected-infrastructure",
        intervention_state="upgrade-required",
        alignment_basis="a-road",
        route_length_m=1_000.0,
        total_absolute_elevation_change_m=0.0,
    )
    prepared = reuse_connection(cycleway, a_road, selection_profile=profile_value)
    source_preparation = preparation(prepared)
    result = compile_prepared_scenario(
        source_preparation,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source_preparation,
                ),
            )
        ),
    )

    assert result.scenario is not None
    assert result.scenario.selections[0].selected_candidate_id == cycleway.candidate_id
    selected = next(
        item
        for item in result.scenario.candidate_sets[0].candidates
        if item.candidate_id == cycleway.candidate_id
    )
    assert selected.total_absolute_elevation_change_m is None


def test_direct_spine_attachment_is_preserved_but_never_promoted() -> None:
    direct = PreparedConnectionRosterRecord(
        access_connection_id="direct-to-spine",
        obligation_kind="community",
        parent_role="strategic-spine",
        community_id="community-direct",
        place_id="community-direct",
        parent_place_id=None,
        disposition="out-of-scope-direct-strategic-spine",
        reason="out-of-scope-direct-strategic-spine-attachment",
    )
    issue = CandidatePreparationIssue(
        access_connection_id="direct-to-spine",
        reason="out-of-scope-direct-strategic-spine-attachment",
        detail="Direct-to-spine is not a strategic Community Connection.",
    )
    result = compile_prepared_scenario(
        preparation(roster=(direct,), issues=(issue,)),
        request(),
    )

    assert result.status == "incomplete"
    assert result.scenario is None
    assert result.missing_inputs == ("eligible-chained-community-connection",)
    assert result.diagnostics["out_of_scope_connections"]


def test_missing_roster_and_empty_evidence_fingerprints_fail_closed() -> None:
    item = connection()
    with pytest.raises(ValueError, match="exhaustive unique connection roster"):
        compile_prepared_scenario(
            preparation(item, roster=()),
            request((packet(item, bound_criteria(item)),)),
        )
    with pytest.raises(ValueError, match="empty, foreign or stale"):
        compile_prepared_scenario(
            preparation(item, fingerprints=()),
            request((packet(item, bound_criteria(item)),)),
        )


def test_missing_parent_roster_gap_is_explicitly_incomplete() -> None:
    missing = PreparedConnectionRosterRecord(
        access_connection_id="missing-parent",
        obligation_kind="community",
        parent_role="spine-access-connection",
        community_id="community-one",
        place_id="community-one",
        parent_place_id=None,
        disposition="unresolved-gap",
        reason="missing-parent-network-place-endpoint",
    )
    issue = CandidatePreparationIssue(
        access_connection_id="missing-parent",
        reason="missing-parent-network-place-endpoint",
        detail="Parent Community Network Place is missing.",
    )
    result = compile_prepared_scenario(
        preparation(roster=(missing,), issues=(issue,)),
        request(),
    )

    assert result.status == "incomplete"
    assert result.missing_inputs == ("unresolved-preparation:missing-parent",)
    assert result.diagnostics["unresolved_connections"]


@pytest.mark.parametrize(
    ("gap", "expected"),
    [
        ("no-options", CandidateGenerationGapReason.NO_GENERATED_CANDIDATES),
        ("all-rejected", CandidateGenerationGapReason.ALL_GENERATED_CANDIDATES_REJECTED),
    ],
)
def test_gap_only_candidate_sets_remain_review_required(
    gap: Literal["no-options", "all-rejected"],
    expected: CandidateGenerationGapReason,
) -> None:
    item = connection(gap=gap)
    assert item.candidate_set.generation_gap_reason == expected
    result = compile_prepared_scenario(
        preparation(item),
        request((packet(item, gap_evidence(item)),)),
    )

    assert result.status == "review-required"
    assert result.scenario is not None and result.scenario.publishable is False
    assert result.review_orchestration is not None
    actions = {
        option.action.value
        for option in result.review_orchestration.actionable_requests[0].request.options
    }
    assert actions == {"expose-network-gap", "terminate"}


def test_clear_and_multi_set_compilations_use_exact_source_bindings() -> None:
    first = connection("one", selection_profile=profile(review_when=[]))
    second = connection("two", selection_profile=first.candidate_set.profile)
    prepared = preparation(first, second)
    result = compile_prepared_scenario(
        prepared,
        request(
            (
                packet(
                    first,
                    bound_criteria(first),
                    source_preparation=prepared,
                ),
                packet(
                    second,
                    bound_criteria(second),
                    source_preparation=prepared,
                ),
            )
        ),
    )

    assert result.status == "compiled"
    assert result.scenario is not None
    assert len(result.scenario.selections) == 2
    assert result.review_orchestration is None
    assert result.diagnostics["agent_runtime_constructed"] is False


@pytest.mark.parametrize("extra_assessment_id", ["000-extra", "zzz-extra"])
def test_extra_population_binding_is_rejected_regardless_of_sort_order(
    extra_assessment_id: str,
) -> None:
    item = connection(gap="no-options")
    prepared = preparation(item)
    exact = gap_evidence(item)
    referenced = exact.evidence_snapshot.assessment(
        AssessmentKind.POPULATION_REACH
    )
    assert referenced is not None
    extra = referenced.model_copy(
        update={"assessment_id": extra_assessment_id}
    )
    snapshot = GovernedEvidenceSnapshot(
        snapshot_id=f"extra-population-{extra_assessment_id}",
        assessments=(*exact.evidence_snapshot.assessments, extra),
    )
    population_ids = tuple(
        binding.assessment_id
        for binding in snapshot.assessments
        if binding.kind == AssessmentKind.POPULATION_REACH
    )
    if extra_assessment_id.startswith("000"):
        assert population_ids == (extra_assessment_id, referenced.assessment_id)
    else:
        assert population_ids == (referenced.assessment_id, extra_assessment_id)
    forged_payload = exact.model_dump(
        mode="python",
        exclude={"criteria_fingerprint"},
    )
    forged_payload["evidence_snapshot"] = snapshot
    forged = CandidateSetGapEvidence.model_validate(forged_payload)

    with pytest.raises(ValueError, match="exactly one binding"):
        compile_prepared_scenario(
            prepared,
            request(
                (
                    packet(
                        item,
                        forged,
                        source_preparation=prepared,
                    ),
                )
            ),
        )


def test_existing_alignment_binding_is_rejected_without_matching_section() -> None:
    item = connection()
    prepared = preparation(item)
    exact = bound_criteria(item)
    extra = GovernedAssessmentBinding(
        kind=AssessmentKind.EXISTING_ALIGNMENT,
        assessment_id="foreign-existing-alignment",
        assessment_content_sha256=hashlib.sha256(
            b"foreign-existing-assessment"
        ).hexdigest(),
        source_content_sha256=hashlib.sha256(
            b"foreign-existing-source"
        ).hexdigest(),
        method_version="existing-alignment/v1",
    )
    snapshot = GovernedEvidenceSnapshot(
        snapshot_id="extra-existing-alignment",
        assessments=(*exact.evidence_snapshot.assessments, extra),
    )
    forged = CandidateCriteria(
        evidence_snapshot=snapshot,
        population=exact.population.model_copy(
            update={
                "scenario_evidence_snapshot_fingerprint": (
                    snapshot.snapshot_fingerprint
                )
            }
        ),
        education=exact.education.model_copy(
            update={
                "scenario_evidence_snapshot_fingerprint": (
                    snapshot.snapshot_fingerprint
                )
            }
        ),
        existing_alignment=None,
        directness=exact.directness,
        gradient=exact.gradient,
        uncertainty=exact.uncertainty,
    )

    with pytest.raises(ValueError, match="no foreign bindings"):
        compile_prepared_scenario(
            prepared,
            request(
                (
                    packet(
                        item,
                        forged,
                        source_preparation=prepared,
                    ),
                )
            ),
        )


@pytest.mark.parametrize("mutation", ["education-raw", "population-raw"])
def test_original_criteria_packet_rejects_refingerprinted_raw_lineage_changes(
    mutation: str,
) -> None:
    item = connection()
    original = preparation(item)
    exact_packet = packet(
        item,
        bound_criteria(item),
        source_preparation=original,
    )
    lineage = json.loads(json.dumps(original.evidence_lineage))
    replacements: dict[str, str] = {}
    if mutation == "education-raw":
        education = lineage["education"]
        old_governed = education["governed_source_fingerprint"]
        old_register = education["school_register_lineage"]["content_sha256"]
        new_governed = hashlib.sha256(b"changed-education-governed").hexdigest()
        new_register = hashlib.sha256(b"changed-education-register").hexdigest()
        education["governed_source_fingerprint"] = new_governed
        education["school_register_lineage"]["content_sha256"] = new_register
        replacements = {
            old_governed: new_governed,
            old_register: new_register,
        }
    else:
        population = lineage["population"]
        old_frame = population["frame_content_sha256"]
        old_artifact = population["artifact_lineage"][0]["content_sha256"]
        new_frame = hashlib.sha256(b"changed-population-frame").hexdigest()
        new_artifact = hashlib.sha256(b"changed-population-artifact").hexdigest()
        population["frame_content_sha256"] = new_frame
        population["artifact_lineage"][0]["content_sha256"] = new_artifact
        replacements = {
            old_frame: new_frame,
            old_artifact: new_artifact,
        }
    changed_fingerprints = tuple(
        sorted(
            replacements.get(value, value)
            for value in original.evidence_fingerprints
        )
    )
    unbound = replace(
        original,
        evidence_lineage=lineage,
        evidence_fingerprints=changed_fingerprints,
        preparation_fingerprint="0" * 64,
    )
    changed = replace(
        unbound,
        preparation_fingerprint=canonical_hash(unbound.canonical_payload()),
    )

    original_lineage = exact_packet.preparation_lineage
    current_lineage = PreparedCriteriaLineage.from_preparation(changed)
    stale_variants = (
        original_lineage,
        replace(
            original_lineage,
            preparation_fingerprint=changed.preparation_fingerprint,
        ),
        PreparedCriteriaLineage(
            preparation_fingerprint=changed.preparation_fingerprint,
            evidence_fingerprints=current_lineage.evidence_fingerprints,
            evidence_lineage=original_lineage.evidence_lineage,
            evidence_lineage_fingerprint=(
                original_lineage.evidence_lineage_fingerprint
            ),
        ),
        PreparedCriteriaLineage(
            preparation_fingerprint=changed.preparation_fingerprint,
            evidence_fingerprints=original_lineage.evidence_fingerprints,
            evidence_lineage=current_lineage.evidence_lineage,
            evidence_lineage_fingerprint=(
                current_lineage.evidence_lineage_fingerprint
            ),
        ),
    )
    for stale_lineage in stale_variants:
        stale_packet = PreparedCandidateCriteria(
            access_connection_id=item.access_connection_id,
            criteria=exact_packet.criteria,
            preparation_lineage=stale_lineage,
        )
        with pytest.raises(
            ValueError,
            match="exact preparation and raw evidence lineage",
        ):
            compile_prepared_scenario(
                changed,
                request((stale_packet,)),
            )


def test_criteria_preparation_lineage_is_self_validating_and_deeply_immutable() -> None:
    item = connection()
    prepared = preparation(item)
    lineage = PreparedCriteriaLineage.from_preparation(prepared)

    assert isinstance(lineage.evidence_lineage, MappingProxyType)
    population = lineage.evidence_lineage["population"]
    assert isinstance(population, MappingProxyType)
    with pytest.raises(TypeError):
        population["frame_content_sha256"] = "0" * 64  # type: ignore[index]
    detached = lineage.canonical()
    detached_lineage = detached["evidence_lineage"]
    assert isinstance(detached_lineage, dict)
    detached_lineage["population"]["frame_content_sha256"] = "0" * 64
    assert (
        lineage.evidence_lineage["population"]["frame_content_sha256"]
        == POPULATION_FRAME
    )
    with pytest.raises(ValueError, match="lineage fingerprint is stale"):
        replace(lineage, evidence_lineage_fingerprint="0" * 64)


def test_forged_criterion_source_hash_fails_closed() -> None:
    item = connection()
    exact = bound_criteria(item)
    bindings = tuple(
        entry.model_copy(update={"source_content_sha256": "0" * 64})
        if entry.kind == AssessmentKind.NETWORK_GEOMETRY
        else entry
        for entry in exact.evidence_snapshot.assessments
    )
    forged_snapshot = GovernedEvidenceSnapshot(
        snapshot_id="forged-network-source",
        assessments=bindings,
    )
    forged = CandidateCriteria(
        evidence_snapshot=forged_snapshot,
        population=exact.population.model_copy(
            update={
                "scenario_evidence_snapshot_fingerprint": (
                    forged_snapshot.snapshot_fingerprint
                )
            }
        ),
        education=exact.education.model_copy(
            update={
                "scenario_evidence_snapshot_fingerprint": (
                    forged_snapshot.snapshot_fingerprint
                )
            }
        ),
        directness=exact.directness,
        gradient=exact.gradient,
        uncertainty=exact.uncertainty,
    )

    with pytest.raises(ValueError, match="network-geometry criterion source"):
        compile_prepared_scenario(
            preparation(item),
            request((packet(item, forged),)),
        )


def test_resealed_ordinary_criterion_rejects_foreign_full_education_source() -> None:
    item = connection()
    prepared = preparation(item)
    exact = bound_criteria(item)
    governed = exact.education.governed_binding
    foreign_source = "f" * 64
    foreign_binding = GovernedEducationCriterionBinding(
        school_ids=governed.school_ids,
        strategic_destination_ids=governed.strategic_destination_ids,
        full_source_governed_fingerprint=foreign_source,
        governed_input_fingerprint=(
            governed_education_assessment_fingerprint(
                governed_source_fingerprint=foreign_source,
                school_ids=governed.school_ids,
                strategic_destination_ids=(
                    governed.strategic_destination_ids
                ),
                assessment_content_sha256=(
                    governed.assessment_content_sha256
                ),
            )
        ),
        assessment_content_sha256=governed.assessment_content_sha256,
    )
    foreign_education_snapshot = next(
        binding
        for binding in exact.evidence_snapshot.assessments
        if binding.kind is AssessmentKind.EDUCATION_ACCESS
    ).model_copy(update={"source_content_sha256": foreign_source})
    foreign_snapshot = GovernedEvidenceSnapshot(
        snapshot_id=exact.evidence_snapshot.snapshot_id,
        assessments=tuple(
            foreign_education_snapshot
            if binding.kind is AssessmentKind.EDUCATION_ACCESS
            else binding
            for binding in exact.evidence_snapshot.assessments
        ),
    )
    foreign = CandidateCriteria(
        evidence_snapshot=foreign_snapshot,
        population=exact.population.model_copy(
            update={
                "scenario_evidence_snapshot_fingerprint": (
                    foreign_snapshot.snapshot_fingerprint
                )
            }
        ),
        education=exact.education.model_copy(
            update={
                "governed_binding": foreign_binding,
                "scenario_evidence_snapshot_fingerprint": (
                    foreign_snapshot.snapshot_fingerprint
                ),
            }
        ),
        existing_alignment=exact.existing_alignment,
        directness=exact.directness,
        gradient=exact.gradient,
        uncertainty=exact.uncertainty,
    )

    with pytest.raises(
        ValueError,
        match="education-access criterion source is foreign or stale",
    ):
        compile_prepared_scenario(
            prepared,
            request(
                (
                    packet(
                        item,
                        foreign,
                        source_preparation=prepared,
                    ),
                )
            ),
        )


def test_ambiguous_ledger_replays_exactly_and_stale_ledger_fails_closed() -> None:
    item = connection(ambiguous=True)
    exact = bound_criteria(item)
    prepared = preparation(item)
    provisional = compile_prepared_scenario(
        prepared,
        request((packet(item, exact),)),
    )
    assert provisional.scenario is not None
    assert provisional.review_orchestration is not None
    selection = provisional.scenario.selections[0]
    decision_request = provisional.review_orchestration.actionable_requests[0].request
    chosen = next(
        option.option_id
        for option in decision_request.options
        if option.candidate_id is not None
    )
    envelope = accepted_envelope(
        selection,
        decision_request,
        chosen,
        scenario_context_fingerprint=provisional.scenario.scenario_context_fingerprint,
    )
    ledger = ScenarioDecisionRecord(
        mode=DecisionProcessMode.ACCEPTED_LEDGER,
        accepted_envelopes=(envelope,),
    )

    first = compile_prepared_scenario(
        prepared,
        request((packet(item, exact),), decision_record=ledger),
    )
    second = compile_prepared_scenario(
        prepared,
        request((packet(item, exact),), decision_record=ledger),
    )
    assert first.status == second.status == "compiled"
    assert first.scenario is not None and second.scenario is not None
    assert first.scenario.scenario_fingerprint == second.scenario.scenario_fingerprint
    assert first.result_fingerprint == second.result_fingerprint

    changed = bound_criteria(
        item,
        counts_500={
            candidate.candidate_id: index + 100
            for index, candidate in enumerate(item.candidate_set.admitted_candidates)
        },
    )
    with pytest.raises(
        ValueError,
        match=r"stale|exact compiler-generated menu|clear no-agent selection",
    ):
        compile_prepared_scenario(
            prepared,
            request((packet(item, changed),), decision_record=ledger),
        )


def test_prior_orchestration_advances_and_preserves_round_history() -> None:
    item = connection(
        ambiguous=True,
        selection_profile=profile(maximum_review_rounds=2),
    )
    exact = bound_criteria(item)
    prepared = preparation(item)
    first = compile_prepared_scenario(
        prepared,
        request((packet(item, exact),), run_id="scenario-round-one"),
    )
    assert first.scenario is not None and first.review_orchestration is not None
    selection = first.scenario.selections[0]
    decision_request = first.review_orchestration.actionable_requests[0].request
    fallback = next(
        option
        for option in decision_request.options
        if option.action.value == "accept-profile-fallback"
    )
    envelope = accepted_envelope(
        selection,
        decision_request,
        fallback.option_id,
        scenario_context_fingerprint=first.scenario.scenario_context_fingerprint,
    )
    ledger = ScenarioDecisionRecord(
        mode=DecisionProcessMode.ACCEPTED_LEDGER,
        accepted_envelopes=(envelope,),
    )
    second = compile_prepared_scenario(
        prepared,
        request(
            (packet(item, exact),),
            decision_record=ledger,
            run_id="scenario-round-two",
            prior=first.review_orchestration,
        ),
    )

    assert second.status == "compiled"
    assert second.review_orchestration is not None
    assert second.review_orchestration.round_number == 2
    assert len(second.review_orchestration.round_history) == 1
    assert second.review_orchestration.converged is True


def test_prior_timeout_advances_to_maximum_round_intervention() -> None:
    item = connection(
        ambiguous=True,
        selection_profile=profile(maximum_review_rounds=1),
    )
    exact = bound_criteria(item)
    prepared = preparation(item)
    first = compile_prepared_scenario(
        prepared,
        request((packet(item, exact),), run_id="scenario-timeout-one"),
    )
    assert first.scenario is not None and first.review_orchestration is not None
    decision_request = first.review_orchestration.actionable_requests[0].request
    attempt = RuntimeDecisionAttempt(
        request=decision_request,
        outcome="provider-timeout",
        provider_failure_code="adapter-timeout",
        invocation_record=RuntimeInvocationRecord(
            invocation_id=f"timeout-{first.review_orchestration.review_run.run_id[-12:]}",
            review_run_id=first.review_orchestration.review_run.run_id,
            run_instance_id=first.review_orchestration.review_run.run_instance_id,
            run_scope_fingerprint=(
                first.review_orchestration.review_run.run_scope_fingerprint
            ),
            run_config_fingerprint=(
                first.review_orchestration.review_run.run_config_fingerprint
            ),
            attempt_number=1,
            maximum_attempts=first.review_orchestration.review_run.maximum_attempts,
            deadline_seconds=first.review_orchestration.review_run.deadline_seconds,
            frontier_fingerprint=review_frontier_fingerprint(
                first.review_orchestration
            ),
            request_fingerprint=decision_request.request_fingerprint,
            outcome="provider-timeout",
            failure_code="adapter-timeout",
            started_at_ms=1000,
            completed_at_ms=2000,
        ),
    )
    ledger = ScenarioDecisionRecord(
        mode=DecisionProcessMode.ACCEPTED_LEDGER,
        runtime_attempts=(attempt,),
    )
    second = compile_prepared_scenario(
        prepared,
        request(
            (packet(item, exact),),
            decision_record=ledger,
            run_id="scenario-timeout-two",
            prior=first.review_orchestration,
        ),
    )

    assert second.status == "review-required"
    assert second.review_orchestration is not None
    assert second.review_orchestration.round_number == 2
    assert len(second.review_orchestration.round_history) == 1
    assert second.review_orchestration.human_intervention_request is not None
    assert (
        second.review_orchestration.human_intervention_request.reason
        == "maximum-review-rounds-exhausted"
    )
    assert second.review_orchestration.scenario.decision_record.runtime_attempts == (
        attempt,
    )


def test_result_diagnostics_are_deeply_immutable_and_metadata_is_defensive() -> None:
    item = connection(selection_profile=profile(review_when=[]))
    result = compile_prepared_scenario(
        preparation(item),
        request((packet(item, bound_criteria(item)),)),
    )
    original = result.result_fingerprint

    assert isinstance(result.diagnostics, MappingProxyType)
    with pytest.raises(TypeError):
        result.diagnostics["reason"] = "mutated"  # type: ignore[index]
    metadata = result.metadata()
    diagnostics = metadata["diagnostics"]
    assert isinstance(diagnostics, dict)
    diagnostics["reason"] = "changed"
    assert result.result_fingerprint == original
    assert result.metadata()["diagnostics"]["reason"] is None
