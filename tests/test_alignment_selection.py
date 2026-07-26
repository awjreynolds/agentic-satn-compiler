"""Authority and decision-hierarchy regressions for PRD #137 packages 5-6."""

from __future__ import annotations

import hashlib
import json
from datetime import date

import geopandas as gpd
import pytest
from pydantic import ValidationError
from shapely.geometry import LineString, Point, box

from satn.alignment_selection import (
    AcceptedDecisionEnvelope,
    AdmissionDisposition,
    AgentAuthorityRole,
    AgentInvocation,
    AlignmentCandidateInput,
    AlignmentChallengeFinding,
    AlignmentCritiqueRecord,
    AlignmentDecisionRequest,
    AlignmentDecisionResponse,
    AssessmentKind,
    CandidateCriteria,
    CandidateGenerationGapReason,
    CandidatePopulationOptionBinding,
    CandidateSetGapEvidence,
    CanonicalLineString,
    CriterionDetail,
    CriterionFinding,
    DecisionRevisionRecord,
    EducationCriterionSummary,
    ExistingAlignmentCriterionSummary,
    GovernedAssessmentBinding,
    GovernedEvidenceSnapshot,
    GovernedReferenceSelectionDecision,
    MaterialGeometryEquivalenceProfile,
    PopulationCriterionSummary,
    PreferredStrategicAlignment,
    ReferenceSATNSelection,
    ReviewRun,
    ReviewRunLedgerProvenance,
    RuntimeDecisionAttempt,
    RuntimeInvocationRecord,
    ScenarioCompilation,
    ScenarioCriteriaBinding,
    ScenarioDecisionRecord,
    ScenarioReviewDependency,
    ScenarioReviewOrchestration,
    SelectionAction,
    SelectionDisposition,
    admit_candidate_set,
    adopt_reference_satn,
    build_alignment_decision_request,
    build_existing_alignment_near_equivalence_proof,
    build_reference_adoption_request,
    education_option_id_for_candidate,
    orchestrate_scenario_review,
    review_frontier_fingerprint,
    select_preferred_alignment,
    validate_alignment_decision_envelope,
)
from satn.education_access import (
    ConnectorContinuity,
    EducationPhase,
    EvidenceAvailability,
    EvidenceFactor,
    IndependentTravelEvidence,
    MeasuredDistance,
    RouteObservationKind,
    RouteQualityEvidence,
    School,
    SchoolAccessEvidence,
    SchoolRegisterEvidence,
    assess_education_access,
)
from satn.existing_alignment import (
    CurrentRouteKind,
    EvidenceProvenance,
    EvidenceState,
    ExistingAlignmentCandidate,
    ExistingAlignmentEvidence,
    GeometryMatchProfile,
    GovernedAssertion,
    ReusableAssetEvidence,
    RouteAvailability,
    compare_near_equivalent_existing_alignments,
    evaluate_existing_alignment_advantage,
)
from satn.models import AccessPointStatus, AgentConfig
from satn.network_selection import NetworkSelectionProfile
from satn.population_reach import (
    CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
    CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
    CurrentDevelopmentEvidence,
    PopulationReachProfile,
    PopulationReachSource,
    compile_population_reach,
)

AS_OF = date(2026, 7, 26)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def principal_assertion(
    request: AlignmentDecisionRequest,
    *,
    option_id: str | None = None,
    response_fingerprint: str | None = None,
    critic: bool = False,
    finding: str = "accepted",
    resolved: bool = True,
    evidence_ids: tuple[str, ...] = ("network-assessment",),
) -> AgentInvocation:
    contracts = request.agent_review_contracts
    role_contract = contracts.critic_role_contract if critic else contracts.primary_role_contract
    prompt_contract = (
        contracts.critic_prompt_contract if critic else contracts.primary_prompt_contract
    )
    return AgentInvocation(
        invocation_id=(
            f"{'critic' if critic else 'primary'}-invocation-{request.request_fingerprint[:16]}"
        ),
        role=(
            AgentAuthorityRole.INDEPENDENT_ALIGNMENT_CRITIC
            if critic
            else AgentAuthorityRole.PRIMARY_ALIGNMENT_DECISION
        ),
        role_contract_fingerprint=role_contract.contract_fingerprint,
        prompt_contract_fingerprint=prompt_contract.contract_fingerprint,
        request_fingerprint=request.request_fingerprint,
        recorded_on=AS_OF,
    )


def reference_decision(
    compiled: ScenarioCompilation,
) -> GovernedReferenceSelectionDecision:
    request = build_reference_adoption_request(compiled)
    payload = {
        "decision_id": "reference-board-decision-2026-07-26",
        "decided_on": AS_OF.isoformat(),
        "decision_maker_name": "Reference SATN Adoption Board",
        "decision_maker_label": "B&NES local SATN review board",
        "rationale": "The exact publishable Scenario is adopted as the Reference SATN.",
        "evidence_ids": list(request.governed_evidence_ids[:1]),
        "source_url": "committee:/reference-board-minutes-2026-07-26",
        "adoption_request": request.model_dump(mode="json"),
        "selected_scenario_fingerprint": compiled.scenario_fingerprint,
        "selected_profile_fingerprint": compiled.profile_fingerprint,
        "selected_evidence_snapshot_fingerprint": compiled.evidence_snapshot.snapshot_fingerprint,
        "selection_run_fingerprint": compiled.decision_record.record_fingerprint,
    }
    return GovernedReferenceSelectionDecision(**payload)


def profile(
    *,
    objective: str = "population-reach",
    tolerance: float = 0.0,
    maximum: int = 5,
    maximum_additional_analyses: int = 2,
    maximum_actionable_requests: int = 12,
    maximum_review_rounds: int = 3,
    review_when: list[str] | None = None,
) -> NetworkSelectionProfile:
    return NetworkSelectionProfile.model_validate(
        {
            "profile_id": "selection-contract-v1",
            "candidate_source_precedence": [
                "verified-existing-asset",
                "a-road-corridor",
                "b-road-corridor",
                "other-routable",
            ],
            "primary_objective": objective,
            "population": (
                {}
                if tolerance == 0
                else {
                    "near_equivalent_tolerance_pct": tolerance,
                    "tolerance_status": "trial",
                }
            ),
            "ambiguity": {
                "maximum_options_per_candidate_set": maximum,
                "maximum_additional_analyses_per_candidate_set": (maximum_additional_analyses),
                "maximum_actionable_requests_per_round": maximum_actionable_requests,
                "maximum_review_rounds": maximum_review_rounds,
                **({"review_when": review_when} if review_when is not None else {}),
            },
        }
    )


def candidate(
    label: str,
    *,
    role: str = "interurban-spine",
    endpoints: tuple[str, str] = ("bath", "saltford"),
    source: str = "other-routable",
    places: tuple[str, ...] = ("bath", "saltford"),
    obligations: tuple[str, ...] = ("secondary-school",),
    destinations: tuple[str, ...] = (),
    topology: str = "satisfied",
    directness: float = 100.0,
    gradient: float | None = 2.0,
    geometry: CanonicalLineString | None = None,
) -> AlignmentCandidateInput:
    offset = float(int(digest(label)[:6], 16) % 1000) * 5000.0
    geometry = geometry or CanonicalLineString(coordinates=((offset, 0.0), (offset + 100.0, 0.0)))
    return AlignmentCandidateInput(
        network_role=role,
        endpoints=endpoints,
        source_class=source,
        geometry=geometry,
        evidence_fingerprints=(digest(f"route-evidence-{label}"),),
        provenance_ids=(f"source-{label}",),
        topology_state=topology,
        served_network_place_ids=places,
        served_access_obligation_ids=obligations,
        served_strategic_destination_ids=destinations,
        directness_m=directness,
        maximum_gradient_pct=gradient,
    )


def candidate_set(
    *items: AlignmentCandidateInput,
    selection_profile: NetworkSelectionProfile | None = None,
    places: tuple[str, ...] = ("bath", "saltford"),
    obligations: tuple[str, ...] = ("secondary-school",),
    destinations: tuple[str, ...] = (),
):
    first = items[0]
    return admit_candidate_set(
        selection_profile or profile(),
        network_role=first.network_role,
        endpoints=first.endpoints,
        candidates=tuple(items),
        mandatory_network_place_ids=places,
        mandatory_access_obligation_ids=obligations,
        mandatory_strategic_destination_ids=destinations,
    )


def population_source() -> PopulationReachSource:
    development = CurrentDevelopmentEvidence(
        source_id="adopted-development-register",
        release="2026.1",
        effective_date=AS_OF,
        licence="Open Government Licence v3.0",
        content_sha256=digest("development"),
        availability=CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
        conclusion=CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
    )
    return PopulationReachSource(
        source_id="ons-census-2021-oa-usual-residents",
        release="Census 2021 OA population-weighted centroids",
        effective_date="2021-03-21",
        licence="Open Government Licence v3.0",
        permitted_uses=("strategic-corridor-analysis",),
        known_limitations=("Whole OA population is assigned to its PWC.",),
        transformation_lineage=("Joined ONS OA data by OA21CD.",),
        source_uri="https://www.ons.gov.uk/census",
        version="census-2021-v1",
        content_sha256=digest("population-source"),
        current_development_evidence=development,
        current_development_evidence_id="development-register-2026",
    )


def compile_population(
    admitted,
    *,
    counts_500: dict[str, int] | None = None,
    counts_1000: dict[str, int] | None = None,
    comparison_tolerance_residents: int = 0,
    headline_distance_m: float = 100.0,
):
    items = admitted.admitted_candidates
    counts_500 = counts_500 or {item.candidate_id: 100 for item in items}
    counts_1000 = counts_1000 or {
        item.candidate_id: counts_500[item.candidate_id] for item in items
    }
    option_by_candidate = {
        item.candidate_id: f"option-{index}" for index, item in enumerate(items, start=1)
    }
    routes = gpd.GeoDataFrame(
        {
            "option_id": [option_by_candidate[item.candidate_id] for item in items],
            "geometry": [item.geometry.as_shapely() for item in items],
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    oa_ids: list[str] = []
    residents: list[int] = []
    centroids: list[Point] = []
    polygons = []
    oa_sequence = 0
    for item in items:
        midpoint = item.geometry.as_shapely().interpolate(0.5, normalized=True)
        headline = counts_500[item.candidate_id]
        sensitivity_only = counts_1000[item.candidate_id] - headline
        if sensitivity_only < 0:
            raise ValueError("1000m count cannot be lower than 500m fixture count")
        for count, distance_m in (
            (headline, headline_distance_m),
            (sensitivity_only, 750.0),
        ):
            if count == 0:
                continue
            oa_sequence += 1
            point = Point(midpoint.x, midpoint.y + distance_m)
            oa_ids.append(f"E{oa_sequence:08d}")
            residents.append(count)
            centroids.append(point)
            polygons.append(point.buffer(10.0))
    output_areas = gpd.GeoDataFrame(
        {
            "OA21CD": oa_ids,
            "usual_residents": residents,
            "population_weighted_centroid": centroids,
            "geometry": polygons,
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    minx, miny, maxx, maxy = routes.total_bounds
    area = gpd.GeoDataFrame(
        {"geometry": [box(minx - 2000, miny - 2000, maxx + 2000, maxy + 2000)]},
        geometry="geometry",
        crs="EPSG:27700",
    )
    assessment = compile_population_reach(
        routes,
        output_areas,
        area,
        source=population_source(),
        profile=PopulationReachProfile(
            comparison_tolerance_residents=comparison_tolerance_residents,
            comparison_tolerance_percent=(
                admitted.profile.population.near_equivalent_tolerance_pct
            ),
        ),
    )
    bindings = tuple(
        CandidatePopulationOptionBinding(
            candidate_id=item.candidate_id,
            option_id=option_by_candidate[item.candidate_id],
            assessment_geometry_sha256=item.geometry.population_geometry_sha256,
        )
        for item in items
    )
    return assessment, bindings


def compile_education(
    admitted,
    *,
    completeness: dict[str, str] | None = None,
    ito: dict[str, int | None] | None = None,
):
    items = admitted.admitted_candidates
    option_by_candidate = {
        item.candidate_id: education_option_id_for_candidate(item, admitted) for item in items
    }
    completeness = completeness or {}
    ito = ito or {item.candidate_id: 1 for item in items}
    evidence = []
    for item in items:
        state = completeness.get(item.candidate_id, "satisfied")
        factors = tuple(
            EvidenceFactor(availability=EvidenceAvailability.AVAILABLE, evidence_ids=(label,))
            for label in (
                "gradient",
                "road-class",
                "speed",
                "crossing",
                "separation",
                "lighting",
                "severance",
                "audit",
            )
        )
        independent = (
            None
            if ito.get(item.candidate_id) is None
            else IndependentTravelEvidence(
                gradient=factors[0],
                road_class=factors[1],
                speed=factors[2],
                crossing=factors[3],
                separation=factors[4],
                lighting=factors[5],
                severance=factors[6],
                audit=factors[7],
            )
        )
        evidence.append(
            SchoolAccessEvidence(
                option_id=option_by_candidate[item.candidate_id],
                school_id="secondary-school",
                connector_distance=MeasuredDistance(distance_m=120),
                connector_continuity=(
                    ConnectorContinuity.DISCONTINUOUS
                    if state == "unsatisfied"
                    else ConnectorContinuity.CONTINUOUS
                ),
                access_point_status=(
                    AccessPointStatus.UNRESOLVED if state == "unknown" else AccessPointStatus.MAPPED
                ),
                destination_distance=MeasuredDistance(distance_m=900),
                access_evidence_ids=(() if state == "unknown" else ("school-access",)),
                support_evidence_ids=("school-support",),
                route_quality_evidence=(
                    RouteQualityEvidence(
                        evidence_id="route-observation",
                        observation=RouteObservationKind.CROSSING_RECORDED,
                    ),
                ),
                independent_travel_evidence=independent,
            )
        )
    assessment = assess_education_access(
        register_evidence=SchoolRegisterEvidence(
            evidence_id="school-register", source_name="test register", as_of=AS_OF
        ),
        schools=(
            School(
                school_id="secondary-school",
                name="Secondary School",
                phase=EducationPhase.SECONDARY,
                source_evidence_id="school-register",
            ),
        ),
        option_evidence=tuple(evidence),
        option_ids=tuple(option_by_candidate.values()),
    )
    return assessment


def state_findings(
    items: tuple[AlignmentCandidateInput, ...],
    detail: CriterionDetail,
    states: dict[str, str] | None = None,
    *,
    assessment_id: str = "network-assessment",
) -> tuple[CriterionFinding, ...]:
    return tuple(
        CriterionFinding(
            candidate_id=item.candidate_id,
            state=(states or {}).get(item.candidate_id, "satisfied"),
            detail=detail,
            assessment_id=assessment_id,
            evidence_record_id=f"{detail}-{item.candidate_id}",
        )
        for item in sorted(items, key=lambda item: item.candidate_id)
    )


def criteria(
    admitted,
    *,
    counts_500: dict[str, int] | None = None,
    counts_1000: dict[str, int] | None = None,
    ito: dict[str, int | None] | None = None,
    completeness: dict[str, str] | None = None,
    directness: dict[str, str] | None = None,
    gradient: dict[str, str] | None = None,
    uncertainty: dict[str, str] | None = None,
    population_comparison_tolerance_residents: int = 0,
    population_headline_distance_m: float = 100.0,
    existing=None,
    snapshot_id: str = "evidence-snapshot",
) -> CandidateCriteria:
    items = admitted.admitted_candidates
    assessment, option_bindings = compile_population(
        admitted,
        counts_500=counts_500,
        counts_1000=counts_1000,
        comparison_tolerance_residents=(population_comparison_tolerance_residents),
        headline_distance_m=population_headline_distance_m,
    )
    ito = ito or {item.candidate_id: 1 for item in items}
    education_assessment = compile_education(
        admitted,
        completeness=completeness,
        ito=ito,
    )
    bindings = [
        GovernedAssessmentBinding(
            kind=AssessmentKind.POPULATION_REACH,
            assessment_id=assessment.assessment_id,
            assessment_content_sha256=digest_assessment(assessment),
            source_content_sha256=assessment.source.content_sha256,
            method_version="population-reach/v1",
        ),
        GovernedAssessmentBinding(
            kind=AssessmentKind.EDUCATION_ACCESS,
            assessment_id=education_assessment.assessment_id,
            assessment_content_sha256=education_assessment.assessment_id,
            source_content_sha256=(education_assessment.source_snapshot.source_content_fingerprint),
            method_version="education-access/v1",
        ),
        GovernedAssessmentBinding(
            kind=AssessmentKind.NETWORK_GEOMETRY,
            assessment_id="network-assessment",
            assessment_content_sha256=digest("network"),
            source_content_sha256=digest("network-source"),
            method_version="network/v1",
        ),
        GovernedAssessmentBinding(
            kind=AssessmentKind.TOPOGRAPHY,
            assessment_id="topography-assessment",
            assessment_content_sha256=digest("topography"),
            source_content_sha256=digest("topography-source"),
            method_version="topography/v1",
        ),
    ]
    if existing is not None:
        bindings.append(
            GovernedAssessmentBinding(
                kind=AssessmentKind.EXISTING_ALIGNMENT,
                assessment_id=existing.proof.proof_id,
                assessment_content_sha256=existing.summary_fingerprint,
                source_content_sha256=digest("existing-source"),
                method_version=existing.comparison.method_version,
            )
        )
    snapshot = GovernedEvidenceSnapshot(
        snapshot_id=snapshot_id,
        assessments=tuple(bindings),
    )
    population = PopulationCriterionSummary.from_assessment(
        assessment,
        option_bindings=option_bindings,
        scenario_evidence_snapshot_fingerprint=snapshot.snapshot_fingerprint,
    )
    return CandidateCriteria(
        evidence_snapshot=snapshot,
        population=population,
        education=EducationCriterionSummary.from_assessment(
            education_assessment,
            candidate_set=admitted,
            scenario_evidence_snapshot_fingerprint=snapshot.snapshot_fingerprint,
        ),
        existing_alignment=existing,
        directness=state_findings(
            items,
            CriterionDetail.DIRECTNESS_EVIDENCE,
            directness,
        ),
        gradient=state_findings(
            items,
            CriterionDetail.GRADIENT_EVIDENCE,
            gradient,
            assessment_id="topography-assessment",
        ),
        uncertainty=state_findings(
            items,
            CriterionDetail.UNCERTAINTY_EVIDENCE,
            uncertainty,
        ),
    )


def digest_assessment(assessment) -> str:
    import json

    return hashlib.sha256(
        json.dumps(
            assessment.canonical(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def test_education_option_bindings_reject_swapped_candidate_options() -> None:
    admitted = candidate_set(candidate("education-left"), candidate("education-right"))
    compiled_criteria = criteria(admitted)
    payload = compiled_criteria.education.model_dump(mode="json")
    bindings = payload["option_bindings"]
    assert len(bindings) == 2
    bindings[0]["option_id"], bindings[1]["option_id"] = (
        bindings[1]["option_id"],
        bindings[0]["option_id"],
    )
    with pytest.raises(ValidationError, match="exact candidate lineage"):
        EducationCriterionSummary.model_validate(payload)


def test_education_source_content_lineage_rejects_rebuilt_forged_snapshot() -> None:
    admitted = candidate_set(candidate("education-source-lineage"))
    compiled_criteria = criteria(admitted)
    forged_bindings = tuple(
        binding.model_copy(
            update={"source_content_sha256": "f" * 64}
            if binding.kind == AssessmentKind.EDUCATION_ACCESS
            else {}
        )
        for binding in compiled_criteria.evidence_snapshot.assessments
    )
    forged_snapshot = GovernedEvidenceSnapshot(
        snapshot_id=compiled_criteria.evidence_snapshot.snapshot_id,
        assessments=forged_bindings,
    )
    forged_education = compiled_criteria.education.model_copy(
        update={"scenario_evidence_snapshot_fingerprint": forged_snapshot.snapshot_fingerprint}
    )
    with pytest.raises(ValidationError, match="education section is stale"):
        CandidateCriteria(
            evidence_snapshot=forged_snapshot,
            population=compiled_criteria.population.model_copy(
                update={
                    "scenario_evidence_snapshot_fingerprint": forged_snapshot.snapshot_fingerprint
                }
            ),
            education=forged_education,
            directness=compiled_criteria.directness,
            gradient=compiled_criteria.gradient,
            uncertainty=compiled_criteria.uncertainty,
        )


def reusable() -> ReusableAssetEvidence:
    provenance = EvidenceProvenance(
        source_id="official-register",
        content_sha256=digest("asset-register"),
        release="2026.1",
        effective_date=date(2026, 1, 1),
        licence="Open Government Licence v3.0",
        observed_on=date(2026, 1, 2),
        valid_until=date(2026, 12, 31),
    )
    assertion = GovernedAssertion(
        state=EvidenceState.CONFIRMED,
        provenance=provenance,
        note="Confirmed",
    )
    return ReusableAssetEvidence(
        lawful_access=assertion,
        usable_condition=assertion,
        continuity=assertion,
        responsible_ownership_or_maintenance=assertion,
    )


def existing_comparison(
    admitted,
    shares: dict[str, float],
    evidence: CandidateCriteria,
):
    match_profile = GeometryMatchProfile(
        crs="EPSG:27700",
        tolerance_m=0.05,
        minimum_match_length_m=0.1,
        maximum_direction_difference_degrees=5.0,
    )
    advantages = []
    for item in admitted.admitted_candidates:
        line = item.geometry.as_shapely()
        end = max(0.01, shares[item.candidate_id])
        evidence_line = LineString(
            (
                line.interpolate(0.0, normalized=True),
                line.interpolate(end, normalized=True),
            )
        )
        provenance = EvidenceProvenance(
            source_id=f"asset-{item.candidate_id}",
            content_sha256=digest(f"asset-{item.candidate_id}"),
            release="2026.1",
            effective_date=date(2026, 1, 1),
            licence="Open Government Licence v3.0",
            observed_on=date(2026, 1, 2),
            valid_until=date(2026, 12, 31),
        )
        advantage = evaluate_existing_alignment_advantage(
            ExistingAlignmentCandidate(
                candidate_id=item.candidate_id,
                geometry_wkt=line.wkt,
                geometry_crs="EPSG:27700",
                directness=item.directness_m,
            ),
            (
                ExistingAlignmentEvidence(
                    evidence_id=f"evidence-{item.candidate_id}",
                    geometry_wkt=evidence_line.wkt,
                    geometry_crs="EPSG:27700",
                    current_route_kind=CurrentRouteKind.CURRENT_NCN,
                    availability=RouteAvailability.OPEN,
                    current_status_provenance=provenance,
                    reusable_asset=reusable(),
                ),
            ),
            as_of=AS_OF,
            match_profile=match_profile,
        )
        advantages.append(advantage)
    ordered = tuple(sorted(advantages, key=lambda item: item.candidate_id))
    proof = build_existing_alignment_near_equivalence_proof(
        admitted.profile,
        admitted,
        evidence,
        ordered,
        proof_id="proof-one",
        as_of=AS_OF,
    )
    return ExistingAlignmentCriterionSummary(
        proof=proof,
        comparison=compare_near_equivalent_existing_alignments(
            admitted.profile,
            ordered,
            proof=proof,
        ),
    )


def scenario(
    selections: tuple[PreferredStrategicAlignment, ...],
    *,
    mode: str = "no-agent-not-invoked",
    mandatory_places: tuple[str, ...] = ("bath", "saltford"),
    mandatory_obligations: tuple[str, ...] = ("secondary-school",),
    mandatory_destinations: tuple[str, ...] = (),
    decision_record: ScenarioDecisionRecord | None = None,
    evidence_snapshot: GovernedEvidenceSnapshot | None = None,
) -> ScenarioCompilation:
    assessment_by_key = {
        (binding.kind, binding.assessment_id): binding
        for selection in selections
        for binding in selection.criteria.evidence_snapshot.assessments
    }
    scenario_snapshot = evidence_snapshot or GovernedEvidenceSnapshot(
        snapshot_id="scenario-evidence-snapshot",
        assessments=tuple(assessment_by_key.values()),
    )
    return ScenarioCompilation(
        area_fingerprint=digest("area"),
        evidence_snapshot=scenario_snapshot,
        profile_fingerprint=selections[0].profile_fingerprint,
        decision_record=decision_record or ScenarioDecisionRecord(mode=mode),
        candidate_sets=tuple(item.candidate_set for item in selections),
        selections=selections,
        criteria_bindings=tuple(
            ScenarioCriteriaBinding(
                candidate_set_id=item.candidate_set_id,
                criteria_fingerprint=item.criteria_fingerprint,
            )
            for item in selections
        ),
        required_network_role_ids=tuple(
            sorted({item.candidate_set.network_role for item in selections})
        ),
        mandatory_network_place_ids=mandatory_places,
        mandatory_access_obligation_ids=mandatory_obligations,
        mandatory_strategic_destination_ids=mandatory_destinations,
        lineage_fingerprints=(digest("lineage"),),
    )


def accepted_envelope(
    selection: PreferredStrategicAlignment,
    request: AlignmentDecisionRequest,
    option_id: str,
    *,
    scenario_context_fingerprint: str,
    resolved_challenge_fingerprints: tuple[str, ...] = (),
    challenge_resolution_evidence_ids: tuple[str, ...] = (),
) -> AcceptedDecisionEnvelope:
    response = AlignmentDecisionResponse(
        request_id=request.request_id,
        request_fingerprint=request.request_fingerprint,
        option_id=option_id,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(request, option_id=option_id),
    )
    critique = AlignmentCritiqueRecord(
        request_fingerprint=request.request_fingerprint,
        response_fingerprint=response.response_fingerprint,
        selection_fingerprint=selection.selection_fingerprint,
        scenario_context_fingerprint=scenario_context_fingerprint,
        evidence_snapshot_fingerprint=(request.evidence_snapshot_fingerprint),
        profile_fingerprint=request.profile_fingerprint,
        finding="accepted",
        resolved=True,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            critic=True,
            response_fingerprint=response.response_fingerprint,
        ),
    )
    return AcceptedDecisionEnvelope(
        request=request,
        response=response,
        critique=critique,
        resolved_challenge_fingerprints=resolved_challenge_fingerprints,
        challenge_resolution_evidence_ids=challenge_resolution_evidence_ids,
    )


def test_geometry_dedupe_handles_sparse_dense_and_connected_multipart() -> None:
    sparse = candidate(
        "sparse",
        source="verified-existing-asset",
        geometry=CanonicalLineString(coordinates=((0.0, 0.0), (100.0, 0.0))),
    )
    dense = candidate(
        "dense",
        source="a-road-corridor",
        geometry=CanonicalLineString(
            coordinates=(
                (0.0, 0.0),
                (25.0, 0.0),
                (50.0, 0.0),
                (75.0, 0.0),
                (100.0, 0.0),
            )
        ),
    )
    multipart = candidate(
        "multipart",
        source="b-road-corridor",
        geometry=CanonicalLineString(
            parts=(
                ((50.0, 0.0), (100.0, 0.0)),
                ((0.0, 0.0), (50.0, 0.0)),
            )
        ),
    )

    admitted = candidate_set(multipart, dense, sparse)

    assert admitted.admitted_candidates == (sparse,)
    rejected = {
        item.candidate_id: item
        for item in admitted.admissions
        if item.disposition == AdmissionDisposition.REJECTED
    }
    assert rejected[dense.candidate_id].retained_candidate_id == sparse.candidate_id
    assert rejected[multipart.candidate_id].retained_candidate_id == sparse.candidate_id
    assert {item.provenance_ids[0] for item in admitted.candidates} == {
        "source-sparse",
        "source-dense",
        "source-multipart",
    }


def test_candidate_identity_is_invariant_to_equivalent_line_representation() -> None:
    geometries = (
        CanonicalLineString(
            coordinates=((0.0, 0.0), (100.0, 0.0)),
        ),
        CanonicalLineString(
            coordinates=(
                (0.0, 0.0),
                (25.0, 0.0),
                (50.0, 0.0),
                (100.0, 0.0),
            ),
        ),
        CanonicalLineString(
            coordinates=((100.0, 0.0), (0.0, 0.0)),
        ),
        CanonicalLineString(
            parts=(
                ((50.0, 0.0), (0.0, 0.0)),
                ((100.0, 0.0), (50.0, 0.0)),
            ),
        ),
    )
    candidates = tuple(
        AlignmentCandidateInput(
            network_role="interurban-spine",
            endpoints=("bath", "saltford"),
            source_class="other-routable",
            geometry=geometry,
            evidence_fingerprints=(digest("same-evidence"),),
            provenance_ids=("same-source",),
            topology_state="satisfied",
            served_network_place_ids=("bath", "saltford"),
            served_access_obligation_ids=("secondary-school",),
            directness_m=100.0,
            maximum_gradient_pct=2.0,
        )
        for geometry in geometries
    )

    assert len({item.fingerprint for item in geometries}) == 1
    assert len({item.candidate_id for item in candidates}) == 1
    assert all(item.as_shapely().length == 100.0 for item in geometries)


def test_candidate_identity_never_collapses_non_equivalent_tolerance_shapes() -> None:
    profile = MaterialGeometryEquivalenceProfile(tolerance_m=0.05)
    straight = CanonicalLineString(
        coordinates=((0.0, 0.0), (50.0, 0.0), (100.0, 0.0)),
        equivalence_profile=profile,
    )
    upper = CanonicalLineString(
        coordinates=((0.0, 0.0), (50.0, 0.04), (100.0, 0.0)),
        equivalence_profile=profile,
    )
    lower = CanonicalLineString(
        coordinates=((0.0, 0.0), (50.0, -0.04), (100.0, 0.0)),
        equivalence_profile=profile,
    )
    upper_boundary = CanonicalLineString(
        coordinates=((0.0, 0.0), (50.0, 0.049999), (100.0, 0.0)),
        equivalence_profile=profile,
    )
    outside_boundary = CanonicalLineString(
        coordinates=((0.0, 0.0), (50.0, 0.050001), (100.0, 0.0)),
        equivalence_profile=profile,
    )

    assert straight.materially_equivalent(upper)
    assert straight.materially_equivalent(lower)
    assert not upper.materially_equivalent(lower)
    assert straight.materially_equivalent(upper_boundary)
    assert not straight.materially_equivalent(outside_boundary)
    assert (
        len(
            {
                straight.fingerprint,
                upper.fingerprint,
                lower.fingerprint,
                upper_boundary.fingerprint,
                outside_boundary.fingerprint,
            }
        )
        == 5
    )

    candidates = tuple(
        AlignmentCandidateInput(
            network_role="interurban-spine",
            endpoints=("bath", "saltford"),
            source_class="other-routable",
            geometry=geometry,
            evidence_fingerprints=(digest("same-evidence"),),
            provenance_ids=("same-source",),
            topology_state="satisfied",
            served_network_place_ids=("bath", "saltford"),
            served_access_obligation_ids=("secondary-school",),
            directness_m=100.0,
            maximum_gradient_pct=2.0,
        )
        for geometry in (upper, lower)
    )
    assert candidates[0].candidate_id != candidates[1].candidate_id


def test_canonical_geometry_is_byte_stable_through_authority_chain() -> None:
    geometries = (
        CanonicalLineString(
            coordinates=((0.0, 0.0), (100.0, 0.0)),
        ),
        CanonicalLineString(
            coordinates=(
                (0.0, 0.0),
                (25.0, 0.0),
                (50.0, 0.0),
                (100.0, 0.0),
            ),
        ),
        CanonicalLineString(
            coordinates=((100.0, 0.0), (0.0, 0.0)),
        ),
        CanonicalLineString(
            parts=(
                ((50.0, 0.0), (0.0, 0.0)),
                ((100.0, 0.0), (50.0, 0.0)),
            ),
        ),
    )
    chains = []
    for geometry in geometries:
        represented = candidate(
            "canonical-chain",
            geometry=geometry,
        )
        alternative = candidate(
            "canonical-chain-alternative",
            geometry=CanonicalLineString(coordinates=((0.0, 100.0), (100.0, 100.0))),
        )
        admitted = candidate_set(represented, alternative)
        evidence = criteria(admitted)
        selection = select_preferred_alignment(
            profile(),
            admitted,
            evidence,
        )
        compiled = scenario(
            (selection,),
            mode="profile-fallback-awaiting-review",
        )
        request = build_alignment_decision_request(
            selection,
            scenario_context_fingerprint=(compiled.scenario_context_fingerprint),
        )
        chains.append(
            (
                represented.model_dump_json(),
                represented.candidate_id,
                represented.geometry.population_geometry_sha256,
                admitted.model_dump_json(),
                admitted.candidate_set_id,
                admitted.candidate_set_fingerprint,
                evidence.model_dump_json(),
                evidence.criteria_fingerprint,
                selection.model_dump_json(),
                selection.selection_fingerprint,
                request.model_dump_json(),
                request.request_fingerprint,
                compiled.model_dump_json(),
                compiled.scenario_fingerprint,
            )
        )

    assert len(set(chains)) == 1


def test_geometry_outside_governed_tolerance_remains_an_option() -> None:
    left = candidate(
        "left",
        geometry=CanonicalLineString(coordinates=((0.0, 0.0), (100.0, 0.0))),
    )
    shifted = candidate(
        "shifted",
        geometry=CanonicalLineString(coordinates=((0.0, 1.0), (100.0, 1.0))),
    )
    assert len(candidate_set(left, shifted).admitted_candidates) == 2


def test_candidate_set_rejects_mixed_geometry_equivalence_policies() -> None:
    left = candidate(
        "left",
        geometry=CanonicalLineString(coordinates=((0.0, 0.0), (100.0, 0.0))),
    )
    right = candidate(
        "right",
        geometry=CanonicalLineString(
            coordinates=((0.0, 0.0), (100.0, 0.0)),
            equivalence_profile=MaterialGeometryEquivalenceProfile(tolerance_m=1.0),
        ),
    )
    with pytest.raises(ValueError, match="share one governed"):
        candidate_set(left, right)


def test_admission_preserves_source_diversity_and_role_obligations() -> None:
    selection_profile = profile(maximum=2)
    existing = candidate("existing", source="verified-existing-asset")
    a_road = candidate("a-road", source="a-road-corridor")
    other = candidate("other", source="other-routable")
    admitted = candidate_set(
        other,
        a_road,
        existing,
        selection_profile=selection_profile,
    )
    assert {item.source_class for item in admitted.admitted_candidates} == {
        "verified-existing-asset",
        "a-road-corridor",
    }

    missing = candidate("missing", obligations=())
    covered = candidate("covered")
    scoped = candidate_set(missing, covered)
    rejection = next(
        item for item in scoped.admissions if item.candidate_id == missing.candidate_id
    )
    assert rejection.rationale == "missing-candidate-set-role-obligation"


def test_population_selection_uses_real_canonical_assessment() -> None:
    left, right = candidate("left"), candidate("right")
    admitted = candidate_set(left, right)
    evidence = criteria(
        admitted,
        counts_500={left.candidate_id: 300, right.candidate_id: 100},
        counts_1000={left.candidate_id: 400, right.candidate_id: 200},
    )

    result = select_preferred_alignment(profile(), admitted, evidence)

    assert result.selected_candidate_id == left.candidate_id
    assert result.criteria.population.assessment.assessment_id.startswith("population-reach-v1-")


def test_source_precedence_is_bounded_to_valid_objective_contenders() -> None:
    existing = candidate(
        "source-precedence-existing",
        source="verified-existing-asset",
        directness=150.0,
    )
    a_road = candidate(
        "source-precedence-a-road",
        source="a-road-corridor",
        directness=50.0,
    )
    admitted = candidate_set(existing, a_road)
    equal_objective = criteria(
        admitted,
        counts_500={
            existing.candidate_id: 100,
            a_road.candidate_id: 100,
        },
    )

    preferred_existing = select_preferred_alignment(
        profile(),
        admitted,
        equal_objective,
    )

    assert preferred_existing.selected_candidate_id == existing.candidate_id
    comparison = existing_comparison(
        admitted,
        {
            existing.candidate_id: 0.25,
            a_road.candidate_id: 0.80,
        },
        equal_objective,
    )
    equal_with_existing_evidence = criteria(
        admitted,
        counts_500={
            existing.candidate_id: 100,
            a_road.candidate_id: 100,
        },
        existing=comparison,
    )
    still_preferred_existing = select_preferred_alignment(
        profile(),
        admitted,
        equal_with_existing_evidence,
    )
    assert still_preferred_existing.selected_candidate_id == existing.candidate_id

    materially_better_a_road = criteria(
        admitted,
        counts_500={
            existing.candidate_id: 100,
            a_road.candidate_id: 200,
        },
    )
    preferred_a_road = select_preferred_alignment(
        profile(),
        admitted,
        materially_better_a_road,
    )
    assert preferred_a_road.selected_candidate_id == a_road.candidate_id

    invalid_existing = candidate(
        "invalid-source-precedence-existing",
        source="verified-existing-asset",
        topology="unsatisfied",
        directness=25.0,
    )
    valid_a_road = candidate(
        "valid-source-precedence-a-road",
        source="a-road-corridor",
        directness=100.0,
    )
    gated_set = candidate_set(invalid_existing, valid_a_road)
    gated_result = select_preferred_alignment(
        profile(),
        gated_set,
        criteria(gated_set),
    )
    assert gated_result.selected_candidate_id == valid_a_road.candidate_id


def test_existing_comparison_can_cover_lower_precedence_objective_contenders() -> None:
    first_existing = candidate(
        "first-existing-frontier",
        source="verified-existing-asset",
        geometry=CanonicalLineString(coordinates=((0.0, 0.0), (100.0, 0.0))),
    )
    second_existing = candidate(
        "second-existing-frontier",
        source="verified-existing-asset",
        geometry=CanonicalLineString(coordinates=((0.0, 100.0), (100.0, 100.0))),
    )
    a_road = candidate(
        "lower-precedence-a-road",
        source="a-road-corridor",
        geometry=CanonicalLineString(coordinates=((0.0, 200.0), (100.0, 200.0))),
    )
    selection_profile = profile(tolerance=5.0)
    admitted = candidate_set(
        first_existing,
        second_existing,
        a_road,
        selection_profile=selection_profile,
    )
    base_evidence = criteria(admitted)
    comparison = existing_comparison(
        admitted,
        {
            first_existing.candidate_id: 0.25,
            second_existing.candidate_id: 0.80,
            a_road.candidate_id: 1.0,
        },
        base_evidence,
    )
    evidence = criteria(admitted, existing=comparison)

    result = select_preferred_alignment(
        selection_profile,
        admitted,
        evidence,
    )

    assert result.selected_candidate_id == second_existing.candidate_id


def test_network_profile_is_sole_population_near_equivalence_authority() -> None:
    leader = candidate("leader", directness=100.0)
    shorter = candidate("shorter", directness=50.0)
    selection_profile = profile(tolerance=0.0)
    admitted = candidate_set(
        leader,
        shorter,
        selection_profile=selection_profile,
    )
    evidence = criteria(
        admitted,
        counts_500={leader.candidate_id: 100, shorter.candidate_id: 50},
        counts_1000={leader.candidate_id: 100, shorter.candidate_id: 50},
        population_comparison_tolerance_residents=100,
    )

    with pytest.raises(
        ValueError,
        match="resident tolerance is not authorised by selection profile",
    ):
        select_preferred_alignment(selection_profile, admitted, evidence)


def test_uppercase_ons_oa_ids_and_candidate_scoped_decisive_lineage() -> None:
    leader, runner = candidate("leader"), candidate("runner")
    selection_profile = profile(tolerance=5.0)
    admitted = candidate_set(
        leader,
        runner,
        selection_profile=selection_profile,
    )
    evidence = criteria(
        admitted,
        counts_500={leader.candidate_id: 100, runner.candidate_id: 96},
        counts_1000={leader.candidate_id: 100, runner.candidate_id: 96},
        population_headline_distance_m=500.0,
    )

    findings = evidence.population.headline_500m
    assert any(item.decisive_borderline_oa_ids for item in findings)
    assert all(
        oa_id.startswith("E") and oa_id[1:].isdigit()
        for item in findings
        for oa_id in (
            *item.borderline_oa_ids,
            *item.decisive_borderline_oa_ids,
        )
    )
    assert all(
        set(item.decisive_borderline_oa_ids).issubset(item.borderline_oa_ids) for item in findings
    )


def test_population_count_and_option_tampering_fail_revalidation() -> None:
    left, right = candidate("left"), candidate("right")
    admitted = candidate_set(left, right)
    evidence = criteria(admitted)
    finding = evidence.population.headline_500m[0]
    forged_population = evidence.population.model_copy(
        update={
            "headline_500m": (
                finding.model_copy(update={"resident_count": (finding.resident_count or 0) + 1}),
                *evidence.population.headline_500m[1:],
            )
        }
    )
    forged = evidence.model_copy(update={"population": forged_population})
    with pytest.raises(ValidationError, match="canonical assessment outputs"):
        select_preferred_alignment(profile(), admitted, forged)

    binding = evidence.population.option_bindings[0]
    forged_population = evidence.population.model_copy(
        update={
            "option_bindings": (
                binding.model_copy(update={"option_id": "foreign-option"}),
                *evidence.population.option_bindings[1:],
            )
        }
    )
    forged = evidence.model_copy(update={"population": forged_population})
    with pytest.raises(ValidationError, match="stale for assessed geometry"):
        select_preferred_alignment(profile(), admitted, forged)

    forged_population = evidence.population.model_copy(
        update={
            "headline_500m": (
                finding.model_copy(
                    update={
                        "current_development_omission": True,
                        "decisive_borderline_oa_ids": ("invented-oa",),
                        "borderline_oa_ids": ("invented-oa",),
                    }
                ),
                *evidence.population.headline_500m[1:],
            )
        }
    )
    forged = evidence.model_copy(update={"population": forged_population})
    with pytest.raises(ValidationError, match="canonical assessment outputs"):
        select_preferred_alignment(profile(), admitted, forged)


def test_500_1000_order_reversal_is_derived_from_assessment() -> None:
    left, right = candidate("left"), candidate("right")
    admitted = candidate_set(left, right)
    evidence = criteria(
        admitted,
        counts_500={left.candidate_id: 300, right.candidate_id: 100},
        counts_1000={left.candidate_id: 350, right.candidate_id: 500},
    )
    result = select_preferred_alignment(profile(), admitted, evidence)
    assert "headline-and-sensitivity-order-differ" in result.ambiguity_triggers


def test_empty_review_policy_records_nonblocking_sensitivity() -> None:
    left, right = candidate("left"), candidate("right", directness=110.0)
    selection_profile = profile(review_when=[])
    admitted = candidate_set(left, right, selection_profile=selection_profile)
    evidence = criteria(
        admitted,
        uncertainty={right.candidate_id: "unknown"},
    )
    result = select_preferred_alignment(selection_profile, admitted, evidence)
    assert result.disposition == SelectionDisposition.SELECTED
    assert result.publishable
    assert "material-grey-evidence" in result.detected_ambiguity_triggers
    assert result.ambiguity_triggers == ()


def test_unknown_hard_gate_is_grey_but_unsatisfied_is_gap() -> None:
    unknown = candidate("unknown", topology="unknown")
    selection_profile = profile(review_when=[])
    admitted = candidate_set(unknown, selection_profile=selection_profile)
    grey = select_preferred_alignment(
        selection_profile,
        admitted,
        criteria(admitted),
    )
    assert grey.disposition == SelectionDisposition.PROVISIONAL_REVIEW
    assert not grey.publishable

    red = candidate("red", topology="unsatisfied")
    red_set = candidate_set(red)
    gap = select_preferred_alignment(profile(), red_set, criteria(red_set))
    assert gap.disposition == SelectionDisposition.NETWORK_GAP
    assert gap.selected_candidate_id is None


def test_existing_alignment_uses_real_advantage_within_source_precedence() -> None:
    source_first = candidate(
        "source-first",
        source="verified-existing-asset",
        directness=90.0,
    )
    higher_reuse = candidate(
        "higher-reuse",
        source="verified-existing-asset",
        directness=120.0,
    )
    selection_profile = profile(tolerance=5.0)
    admitted = candidate_set(
        source_first,
        higher_reuse,
        selection_profile=selection_profile,
    )
    base_evidence = criteria(
        admitted,
        counts_500={
            source_first.candidate_id: 100,
            higher_reuse.candidate_id: 100,
        },
    )
    comparison = existing_comparison(
        admitted,
        {
            source_first.candidate_id: 0.25,
            higher_reuse.candidate_id: 0.80,
        },
        base_evidence,
    )
    evidence = criteria(
        admitted,
        counts_500={
            source_first.candidate_id: 100,
            higher_reuse.candidate_id: 100,
        },
        existing=comparison,
    )

    result = select_preferred_alignment(selection_profile, admitted, evidence)

    assert result.selected_candidate_id == higher_reuse.candidate_id
    assert result.criteria.existing_alignment == comparison
    compiled = scenario((result,))
    governed = reference_decision(compiled)
    assert (
        adopt_reference_satn(
            compiled,
            governed_decision=governed,
        ).scenario_fingerprint
        == compiled.scenario_fingerprint
    )


def test_forged_existing_shares_fail_canonical_comparison_validation() -> None:
    left, right = candidate("left"), candidate("right")
    selection_profile = profile(tolerance=5.0)
    admitted = candidate_set(left, right, selection_profile=selection_profile)
    base_evidence = criteria(admitted)
    comparison = existing_comparison(
        admitted,
        {left.candidate_id: 0.25, right.candidate_id: 0.80},
        base_evidence,
    )
    payload = comparison.comparison.model_dump(mode="python")
    payload["comparison_values"] = tuple(
        item.model_copy(update=({"reusable_asset_share": 1.0} if index == 0 else {}))
        for index, item in enumerate(comparison.comparison.comparison_values)
    )
    with pytest.raises(ValidationError, match="derived from bound advantages"):
        type(comparison.comparison).model_validate(payload)

    first = comparison.proof.eligibility[0]
    forged_proof = comparison.proof.model_copy(
        update={
            "eligibility": (
                first.model_copy(update={"advantage_fingerprint": digest("forged-advantage")}),
                *comparison.proof.eligibility[1:],
            )
        }
    )
    with pytest.raises(ValidationError, match="stale"):
        ExistingAlignmentCriterionSummary(
            proof=forged_proof,
            comparison=comparison.comparison,
        )


def test_existing_comparison_must_match_candidate_geometry() -> None:
    left, right = candidate("left"), candidate("right")
    selection_profile = profile(tolerance=5.0)
    admitted = candidate_set(left, right, selection_profile=selection_profile)
    base_evidence = criteria(admitted)
    comparison = existing_comparison(
        admitted,
        {left.candidate_id: 0.25, right.candidate_id: 0.80},
        base_evidence,
    )
    changed_right = AlignmentCandidateInput.model_validate(
        right.model_dump(mode="json")
        | {
            "geometry": CanonicalLineString(coordinates=((0.0, 10.0), (100.0, 10.0))).model_dump(
                mode="json"
            ),
            "candidate_id": "",
        }
    )
    changed_set = candidate_set(
        left,
        changed_right,
        selection_profile=selection_profile,
    )
    with pytest.raises(ValueError, match="another Candidate Set"):
        select_preferred_alignment(
            selection_profile,
            changed_set,
            criteria(changed_set, existing=comparison),
        )


def test_existing_proof_is_bound_to_current_active_objective_evidence() -> None:
    left, right = candidate("left"), candidate("right")
    selection_profile = profile(tolerance=5.0)
    admitted = candidate_set(
        left,
        right,
        selection_profile=selection_profile,
    )
    original = criteria(
        admitted,
        counts_500={left.candidate_id: 100, right.candidate_id: 100},
    )
    comparison = existing_comparison(
        admitted,
        {left.candidate_id: 0.25, right.candidate_id: 0.80},
        original,
    )
    changed = criteria(
        admitted,
        counts_500={left.candidate_id: 200, right.candidate_id: 100},
        existing=comparison,
    )

    with pytest.raises(
        ValueError,
        match="near-equivalence proof is stale for current selection criteria",
    ):
        select_preferred_alignment(selection_profile, admitted, changed)


def test_unknown_directness_cannot_decide_existing_alignment_comparison() -> None:
    lower_raw = candidate(
        "existing-unknown-directness-lower",
        source="verified-existing-asset",
        directness=50.0,
    )
    higher = candidate(
        "existing-unknown-directness-higher",
        source="verified-existing-asset",
        directness=100.0,
    )
    selection_profile = profile(tolerance=5.0, review_when=[])
    admitted = candidate_set(
        lower_raw,
        higher,
        selection_profile=selection_profile,
    )
    base_evidence = criteria(admitted)
    comparison = existing_comparison(
        admitted,
        {
            lower_raw.candidate_id: 0.5,
            higher.candidate_id: 0.5,
        },
        base_evidence,
    )
    evidence = criteria(
        admitted,
        existing=comparison,
        directness={lower_raw.candidate_id: "unknown"},
    )

    result = select_preferred_alignment(
        selection_profile,
        admitted,
        evidence,
    )

    assert result.disposition == SelectionDisposition.PROVISIONAL_REVIEW
    assert not result.publishable
    assert result.active_frontier_candidate_ids == tuple(
        sorted((lower_raw.candidate_id, higher.candidate_id))
    )
    assert result.selected_candidate_id == min(
        lower_raw.candidate_id,
        higher.candidate_id,
    )
    assert result.ambiguity_triggers == ("material-grey-evidence",)


def test_bath_railway_and_a4_campus_roles_are_compiler_classified() -> None:
    railway = candidate("railway", source="verified-existing-asset")
    railway_set = candidate_set(railway)
    railway_result = select_preferred_alignment(
        profile(),
        railway_set,
        criteria(railway_set),
    )
    a4 = candidate(
        "a4-campus",
        role="strategic-destination-access",
        endpoints=("bath", "bath-spa-campus"),
        source="a-road-corridor",
        places=("bath",),
        obligations=(),
        destinations=("bath-spa-campus",),
    )
    campus_set = candidate_set(
        a4,
        places=("bath",),
        obligations=(),
        destinations=("bath-spa-campus",),
    )
    campus_result = select_preferred_alignment(
        profile(),
        campus_set,
        criteria(campus_set),
    )
    compiled = scenario(
        (railway_result, campus_result),
        mandatory_destinations=("bath-spa-campus",),
    )
    assert compiled.selected_candidate_ids == (railway.candidate_id,)
    assert compiled.complementary_candidate_ids == (a4.candidate_id,)


def test_required_community_endpoints_remain_complementary() -> None:
    village_a = candidate(
        "village-a",
        role="community-access",
        endpoints=("hub", "village-a"),
        places=("hub", "village-a"),
        obligations=(),
    )
    village_b = candidate(
        "village-b",
        role="community-access",
        endpoints=("hub", "village-b"),
        places=("hub", "village-b"),
        obligations=(),
    )
    set_a = candidate_set(
        village_a,
        places=("hub", "village-a"),
        obligations=(),
    )
    set_b = candidate_set(
        village_b,
        places=("hub", "village-b"),
        obligations=(),
    )
    result_a = select_preferred_alignment(profile(), set_a, criteria(set_a))
    result_b = select_preferred_alignment(profile(), set_b, criteria(set_b))

    compiled = scenario(
        (result_a, result_b),
        mandatory_places=("hub", "village-a", "village-b"),
        mandatory_obligations=(),
    )

    assert compiled.selected_candidate_ids == ()
    assert compiled.complementary_candidate_ids == tuple(
        sorted((village_a.candidate_id, village_b.candidate_id))
    )


def test_scenario_global_school_obligations_make_connections_complementary() -> None:
    school_a = candidate(
        "school-a",
        role="school-access",
        endpoints=("hub", "school-a"),
        places=("hub",),
        obligations=("school-a",),
    )
    school_b = candidate(
        "school-b",
        role="school-access",
        endpoints=("hub", "school-b"),
        places=("hub",),
        obligations=("school-b",),
    )
    set_a = candidate_set(
        school_a,
        places=("hub",),
        obligations=(),
    )
    set_b = candidate_set(
        school_b,
        places=("hub",),
        obligations=(),
    )
    result_a = select_preferred_alignment(profile(), set_a, criteria(set_a))
    result_b = select_preferred_alignment(profile(), set_b, criteria(set_b))

    compiled = scenario(
        (result_a, result_b),
        mandatory_places=("hub",),
        mandatory_obligations=("school-a", "school-b"),
    )

    assert compiled.selected_candidate_ids == ()
    assert compiled.complementary_candidate_ids == tuple(
        sorted((school_a.candidate_id, school_b.candidate_id))
    )


def test_scenario_rejects_duplicate_connection_and_mixed_snapshot() -> None:
    first, second = candidate("first"), candidate("second")
    first_set, second_set = candidate_set(first), candidate_set(second)
    first_result = select_preferred_alignment(profile(), first_set, criteria(first_set))
    second_result = select_preferred_alignment(profile(), second_set, criteria(second_set))
    with pytest.raises(ValidationError, match="one Candidate Set"):
        scenario((first_result, second_result))

    access = candidate(
        "access",
        role="school-access",
        endpoints=("bath", "school"),
    )
    access_set = candidate_set(access)
    access_result = select_preferred_alignment(
        profile(),
        access_set,
        criteria(access_set, snapshot_id="other-snapshot"),
    )
    with pytest.raises(ValidationError, match="exact criterion assessments"):
        scenario(
            (first_result, access_result),
            evidence_snapshot=first_result.criteria.evidence_snapshot,
        )


def test_request_factory_rejects_clear_no_agent_selection() -> None:
    only = candidate("only")
    admitted = candidate_set(only)
    selected = select_preferred_alignment(profile(), admitted, criteria(admitted))
    assert selected.decision_action == SelectionAction.NO_AGENT_CLEAR
    with pytest.raises(ValueError, match="clear no-agent"):
        build_alignment_decision_request(
            selected,
            scenario_context_fingerprint=digest("scenario-context"),
        )


def test_request_menu_is_exactly_compiler_generated_and_scenario_bound() -> None:
    unknown = candidate("unknown", topology="unknown")
    admitted = candidate_set(unknown)
    provisional = select_preferred_alignment(
        profile(),
        admitted,
        criteria(admitted),
    )
    base = scenario(
        (provisional,),
        mode="profile-fallback-awaiting-review",
    )
    request = build_alignment_decision_request(
        provisional,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    assert request.reason == "grey-hard-gate"
    assert {item.option_id for item in request.options} == {
        "analyse-topology-continuity",
        "terminate",
    }
    payload = request.model_dump(mode="json")
    payload["options"].append(
        {
            "option_id": "invented",
            "action": "terminate",
            "candidate_id": None,
            "analysis_kind": None,
        }
    )
    payload["request_fingerprint"] = ""
    with pytest.raises(ValidationError, match="not compiler-generated"):
        AlignmentDecisionRequest.model_validate(payload)

    response = AlignmentDecisionResponse(
        request_id=request.request_id,
        request_fingerprint=request.request_fingerprint,
        option_id="analyse-topology-continuity",
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            option_id="analyse-topology-continuity",
        ),
    )
    critique = AlignmentCritiqueRecord(
        request_fingerprint=request.request_fingerprint,
        response_fingerprint=response.response_fingerprint,
        selection_fingerprint=provisional.selection_fingerprint,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
        evidence_snapshot_fingerprint=request.evidence_snapshot_fingerprint,
        profile_fingerprint=request.profile_fingerprint,
        finding="accepted",
        resolved=True,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            critic=True,
            response_fingerprint=response.response_fingerprint,
        ),
    )
    unoffered = AlignmentDecisionResponse(
        request_id=request.request_id,
        request_fingerprint=request.request_fingerprint,
        option_id="accept-profile-fallback",
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            option_id="accept-profile-fallback",
        ),
    )
    unoffered_critique = AlignmentCritiqueRecord(
        request_fingerprint=request.request_fingerprint,
        response_fingerprint=unoffered.response_fingerprint,
        selection_fingerprint=provisional.selection_fingerprint,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
        evidence_snapshot_fingerprint=request.evidence_snapshot_fingerprint,
        profile_fingerprint=request.profile_fingerprint,
        finding="accepted",
        resolved=True,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            critic=True,
            response_fingerprint=unoffered.response_fingerprint,
        ),
    )
    rejected = validate_alignment_decision_envelope(
        request,
        unoffered,
        unoffered_critique,
    )
    assert rejected.reason == "unoffered-option"
    assert rejected.offered_option_ids == (
        "analyse-topology-continuity",
        "terminate",
    )
    envelope = AcceptedDecisionEnvelope(
        request=request,
        response=response,
        critique=critique,
    )
    compiled = scenario(
        (provisional,),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            accepted_envelopes=(envelope,),
        ),
    )
    assert compiled.decision_record.accepted_envelopes == (envelope,)
    assert not compiled.publishable
    assert compiled.resolved_selections == ()


def test_decision_envelope_enforces_independent_governed_actor_contract() -> None:
    unknown = candidate("actor-contract", topology="unknown")
    admitted = candidate_set(unknown)
    provisional = select_preferred_alignment(
        profile(),
        admitted,
        criteria(admitted),
    )
    base = scenario(
        (provisional,),
        mode="profile-fallback-awaiting-review",
    )
    request = build_alignment_decision_request(
        provisional,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    response = AlignmentDecisionResponse(
        request_id=request.request_id,
        request_fingerprint=request.request_fingerprint,
        option_id="analyse-topology-continuity",
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            option_id="analyse-topology-continuity",
        ),
    )
    critique = AlignmentCritiqueRecord(
        request_fingerprint=request.request_fingerprint,
        response_fingerprint=response.response_fingerprint,
        selection_fingerprint=provisional.selection_fingerprint,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
        evidence_snapshot_fingerprint=request.evidence_snapshot_fingerprint,
        profile_fingerprint=request.profile_fingerprint,
        finding="accepted",
        resolved=True,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            critic=True,
            response_fingerprint=response.response_fingerprint,
        ),
    )
    envelope = AcceptedDecisionEnvelope(
        request=request,
        response=response,
        critique=critique,
    )
    foreign_evidence_response = response.model_dump(mode="json")
    foreign_evidence_response["evidence_ids"] = ["foreign-evidence"]
    foreign_evidence_response["response_fingerprint"] = ""
    assert (
        validate_alignment_decision_envelope(
            request,
            foreign_evidence_response,
            critique,
        ).reason
        == "invalid-authority"
    )
    assert envelope.response.invocation.invocation_fingerprint
    assert envelope.critique.invocation.invocation_fingerprint
    assert envelope.response.prompt_fingerprint
    assert envelope.critique.prompt_fingerprint
    assert envelope.response.invocation.invocation_id != envelope.critique.invocation.invocation_id
    contracts = request.agent_review_contracts
    assert contracts.primary_role_contract != contracts.critic_role_contract
    assert contracts.primary_prompt_contract != contracts.critic_prompt_contract
    assert any(
        "challenge" in instruction.lower()
        for instruction in contracts.critic_role_contract.canonical_instructions
    )
    assert "finding" in contracts.critic_role_contract.output_schema_fields
    assert "resolved" in contracts.critic_role_contract.output_schema_fields
    assert (
        "decision_revision_record.challenge_findings"
        in contracts.critic_role_contract.output_schema_fields
    )
    foreign_invocation = response.invocation.model_dump(mode="json")
    foreign_invocation["role"] = "independent-alignment-critic"
    foreign_invocation["invocation_fingerprint"] = ""
    with pytest.raises(ValidationError, match="primary agent invocation"):
        AlignmentDecisionResponse.model_validate(
            {
                **response.model_dump(mode="json"),
                "invocation": foreign_invocation,
                "response_fingerprint": "",
            }
        )

    changed_contract_request = request.model_dump(mode="json")
    role_contract = changed_contract_request["agent_review_contracts"]["primary_role_contract"]
    role_contract["canonical_instructions"][0] = "Choose any route you like."
    role_contract["content_sha256"] = ""
    role_contract["contract_fingerprint"] = ""
    changed_contract_request["agent_review_contracts"]["contracts_fingerprint"] = ""
    changed_contract_request["request_fingerprint"] = ""
    with pytest.raises(ValidationError, match="exact compiler-configured substantive content"):
        AlignmentDecisionRequest.model_validate(changed_contract_request)

    forged_response = response.model_copy(
        update={"response_fingerprint": digest("forged-response")}
    )
    with pytest.raises(
        ValidationError,
        match="response fingerprint is stale",
    ):
        AcceptedDecisionEnvelope(
            request=request,
            response=forged_response,
            critique=critique,
        )

    wrong_response_critique = critique.model_copy(
        update={
            "response_fingerprint": digest("another-response"),
            "prompt_fingerprint": "",
            "critique_fingerprint": "",
        }
    )
    with pytest.raises(
        ValidationError,
        match="exact resolved critique",
    ):
        AcceptedDecisionEnvelope(
            request=request,
            response=response,
            critique=wrong_response_critique,
        )

    duplicate_invocation = critique.model_copy(
        update={
            "invocation": response.invocation,
            "prompt_fingerprint": "",
            "critique_fingerprint": "",
        }
    )
    with pytest.raises(ValidationError, match="independent critic invocation"):
        AcceptedDecisionEnvelope(
            request=request,
            response=response,
            critique=duplicate_invocation,
        )


def test_decision_envelope_boundary_never_throws_for_invalid_nested_input() -> None:
    unknown = candidate("total-boundary", topology="unknown")
    admitted = candidate_set(unknown)
    provisional = select_preferred_alignment(profile(), admitted, criteria(admitted))
    compiled = scenario(
        (provisional,),
        mode="profile-fallback-awaiting-review",
    )
    request = build_alignment_decision_request(
        provisional,
        scenario_context_fingerprint=compiled.scenario_context_fingerprint,
    )
    option = request.options[0]
    response = AlignmentDecisionResponse(
        request_id=request.request_id,
        request_fingerprint=request.request_fingerprint,
        option_id=option.option_id,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(request, option_id=option.option_id),
    )
    critique = AlignmentCritiqueRecord(
        request_fingerprint=request.request_fingerprint,
        response_fingerprint=response.response_fingerprint,
        selection_fingerprint=provisional.selection_fingerprint,
        scenario_context_fingerprint=compiled.scenario_context_fingerprint,
        evidence_snapshot_fingerprint=request.evidence_snapshot_fingerprint,
        profile_fingerprint=request.profile_fingerprint,
        finding="accepted",
        resolved=True,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            critic=True,
            response_fingerprint=response.response_fingerprint,
        ),
    )
    malformed_response = response.model_dump(mode="json")
    malformed_response["invocation"]["request_fingerprint"] = digest("foreign-request")
    malformed_response["invocation"]["invocation_fingerprint"] = ""

    rejection = validate_alignment_decision_envelope(
        request.model_dump(mode="json"),
        malformed_response,
        critique.model_dump(mode="json"),
    )

    assert rejection.reason == "nested-validation-failure"
    assert rejection.request_id == "invalid-request"
    assert rejection.lineage_fingerprints

    class HostileBoundaryObject:
        def __getattribute__(self, name: str) -> object:
            raise BaseException(f"hostile attribute access: {name}")

        def __repr__(self) -> str:
            raise BaseException("hostile repr")

    hostile_rejection = validate_alignment_decision_envelope(
        HostileBoundaryObject(),
        HostileBoundaryObject(),
        HostileBoundaryObject(),
    )
    assert hostile_rejection.reason == "nested-validation-failure"
    assert hostile_rejection.request_id == "invalid-request"


def test_mixed_valid_and_grey_menu_targets_the_blocking_grey_candidate() -> None:
    valid = candidate("valid")
    grey = candidate("grey", topology="unknown")
    admitted = candidate_set(valid, grey)
    provisional = select_preferred_alignment(
        profile(),
        admitted,
        criteria(
            admitted,
            counts_500={valid.candidate_id: 100, grey.candidate_id: 200},
            counts_1000={valid.candidate_id: 100, grey.candidate_id: 200},
        ),
    )
    request = build_alignment_decision_request(
        provisional,
        scenario_context_fingerprint=digest("mixed-grey-context"),
    )

    topology = next(item for item in request.options if item.analysis_kind == "topology-continuity")
    assert topology.analysis_candidate_ids == (grey.candidate_id,)


def test_additional_analysis_cap_is_global_deterministic_and_explicit() -> None:
    grey = candidate("grey", topology="unknown")
    selection_profile = profile(maximum_additional_analyses=2)
    admitted = candidate_set(
        grey,
        selection_profile=selection_profile,
    )
    provisional = select_preferred_alignment(
        selection_profile,
        admitted,
        criteria(
            admitted,
            directness={grey.candidate_id: "unknown"},
        ),
    )
    request = build_alignment_decision_request(
        provisional,
        scenario_context_fingerprint=digest("capped-grey-context"),
    )

    analyses = tuple(item for item in request.options if item.action == "run-additional-analysis")
    assert tuple(item.analysis_kind for item in analyses) == (
        "directness-evidence",
        "topology-continuity",
    )
    assert not any(item.action == "request-human-intervention" for item in request.options)


def test_retain_complementary_set_is_exact_and_applied_by_scenario_ledger() -> None:
    left = candidate(
        "left",
        role="unresolved-strategic-alignment",
    )
    right = candidate(
        "right",
        role="unresolved-strategic-alignment",
    )
    admitted = candidate_set(left, right)
    provisional = select_preferred_alignment(
        profile(),
        admitted,
        criteria(admitted),
    )
    base = scenario(
        (provisional,),
        mode="profile-fallback-awaiting-review",
    )
    request = build_alignment_decision_request(
        provisional,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    retain = next(item for item in request.options if item.action == "retain-complementary-set")
    assert retain.complementary_candidate_ids == tuple(
        sorted((left.candidate_id, right.candidate_id))
    )
    assert retain.complementary_set_fingerprint

    forged = retain.model_dump(mode="json")
    forged["complementary_candidate_ids"] = [
        left.candidate_id,
        candidate("foreign").candidate_id,
    ]
    with pytest.raises(ValidationError, match="fingerprint is stale"):
        type(retain).model_validate(forged)

    response = AlignmentDecisionResponse(
        request_id=request.request_id,
        request_fingerprint=request.request_fingerprint,
        option_id=retain.option_id,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            option_id=retain.option_id,
        ),
    )
    critique = AlignmentCritiqueRecord(
        request_fingerprint=request.request_fingerprint,
        response_fingerprint=response.response_fingerprint,
        selection_fingerprint=provisional.selection_fingerprint,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
        evidence_snapshot_fingerprint=request.evidence_snapshot_fingerprint,
        profile_fingerprint=request.profile_fingerprint,
        finding="accepted",
        resolved=True,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            critic=True,
            response_fingerprint=response.response_fingerprint,
        ),
    )
    envelope = AcceptedDecisionEnvelope(
        request=request,
        response=response,
        critique=critique,
    )
    compiled = scenario(
        (provisional,),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            accepted_envelopes=(envelope,),
        ),
    )
    assert compiled.selected_candidate_ids == ()
    assert compiled.complementary_candidate_ids == (retain.complementary_candidate_ids)
    assert compiled.publishable
    assert compiled.resolved_selections[0].selected_candidate_id is None
    assert (
        compiled.resolved_selections[0].complementary_candidate_ids
        == retain.complementary_candidate_ids
    )

    governed = reference_decision(compiled)
    reference = adopt_reference_satn(
        compiled,
        governed_decision=governed,
    )
    assert reference.selected_candidate_ids == ()
    assert reference.complementary_candidate_ids == retain.complementary_candidate_ids


def test_accepted_select_derives_publishable_resolved_selection() -> None:
    left = candidate(
        "accepted-select-left",
        role="unresolved-strategic-alignment",
    )
    right = candidate(
        "accepted-select-right",
        role="unresolved-strategic-alignment",
    )
    admitted = candidate_set(left, right)
    provisional = select_preferred_alignment(
        profile(),
        admitted,
        criteria(admitted),
    )
    base = scenario(
        (provisional,),
        mode="profile-fallback-awaiting-review",
    )
    request = build_alignment_decision_request(
        provisional,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    chosen_id = next(
        item.candidate_id
        for item in request.options
        if (
            item.action == "select-eligible-option"
            and item.candidate_id != provisional.selected_candidate_id
        )
    )
    selected_option = next(item for item in request.options if item.candidate_id == chosen_id)
    response = AlignmentDecisionResponse(
        request_id=request.request_id,
        request_fingerprint=request.request_fingerprint,
        option_id=selected_option.option_id,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            option_id=selected_option.option_id,
        ),
    )
    critique = AlignmentCritiqueRecord(
        request_fingerprint=request.request_fingerprint,
        response_fingerprint=response.response_fingerprint,
        selection_fingerprint=provisional.selection_fingerprint,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
        evidence_snapshot_fingerprint=request.evidence_snapshot_fingerprint,
        profile_fingerprint=request.profile_fingerprint,
        finding="accepted",
        resolved=True,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            critic=True,
            response_fingerprint=response.response_fingerprint,
        ),
    )
    envelope = AcceptedDecisionEnvelope(
        request=request,
        response=response,
        critique=critique,
    )
    compiled = scenario(
        (provisional,),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            accepted_envelopes=(envelope,),
        ),
    )

    assert compiled.publishable
    assert len(compiled.resolved_selections) == 1
    resolved = compiled.resolved_selections[0]
    assert resolved.disposition == SelectionDisposition.SELECTED
    assert resolved.publishable
    assert resolved.selected_candidate_id == chosen_id
    assert resolved.complementary_candidate_ids == ()
    assert resolved.accepted_decision_envelope_fingerprint == envelope.envelope_fingerprint
    assert compiled.selected_candidate_ids == (chosen_id,)

    tampered = compiled.model_dump(mode="json")
    tampered["resolved_selections"][0]["selected_candidate_id"] = provisional.selected_candidate_id
    tampered["resolved_selections"][0]["resolution_fingerprint"] = ""
    tampered["scenario_id"] = ""
    tampered["scenario_fingerprint"] = ""
    with pytest.raises(
        ValidationError,
        match="contradicts its exact decision",
    ):
        ScenarioCompilation.model_validate(tampered)

    governed = reference_decision(compiled)
    reference = adopt_reference_satn(
        compiled,
        governed_decision=governed,
    )
    assert reference.selected_candidate_ids == (chosen_id,)


def test_accepted_profile_fallback_derives_exact_publishable_resolution() -> None:
    left = candidate(
        "accepted-fallback-left",
        geometry=CanonicalLineString(coordinates=((0.0, 0.0), (100.0, 0.0))),
    )
    right = candidate(
        "accepted-fallback-right",
        geometry=CanonicalLineString(coordinates=((0.0, 100.0), (100.0, 100.0))),
    )
    admitted = candidate_set(left, right)
    provisional = select_preferred_alignment(
        profile(),
        admitted,
        criteria(admitted),
    )
    base = scenario(
        (provisional,),
        mode="profile-fallback-awaiting-review",
    )
    request = build_alignment_decision_request(
        provisional,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    fallback = next(item for item in request.options if item.action == "accept-profile-fallback")
    response = AlignmentDecisionResponse(
        request_id=request.request_id,
        request_fingerprint=request.request_fingerprint,
        option_id=fallback.option_id,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            option_id=fallback.option_id,
        ),
    )
    critique = AlignmentCritiqueRecord(
        request_fingerprint=request.request_fingerprint,
        response_fingerprint=response.response_fingerprint,
        selection_fingerprint=provisional.selection_fingerprint,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
        evidence_snapshot_fingerprint=request.evidence_snapshot_fingerprint,
        profile_fingerprint=request.profile_fingerprint,
        finding="accepted",
        resolved=True,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            critic=True,
            response_fingerprint=response.response_fingerprint,
        ),
    )
    envelope = AcceptedDecisionEnvelope(
        request=request,
        response=response,
        critique=critique,
    )

    compiled = scenario(
        (provisional,),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            accepted_envelopes=(envelope,),
        ),
    )

    assert compiled.publishable
    resolved = compiled.resolved_selections[0]
    assert resolved.selected_candidate_id == provisional.selected_candidate_id
    assert resolved.resolution_action == "accept-profile-fallback"
    assert resolved.accepted_decision_envelope_fingerprint == envelope.envelope_fingerprint


def test_exposed_network_gap_is_typed_publishable_and_reference_adoptable() -> None:
    invalid = candidate(
        "explicit-network-gap",
        topology="unsatisfied",
    )
    admitted = candidate_set(invalid)
    gap_selection = select_preferred_alignment(
        profile(),
        admitted,
        criteria(admitted),
    )
    assert gap_selection.disposition == SelectionDisposition.NETWORK_GAP
    base = scenario(
        (gap_selection,),
        mode="provisional-review-awaiting-decision",
    )
    assert not base.publishable
    request = build_alignment_decision_request(
        gap_selection,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    expose = next(item for item in request.options if item.action == "expose-network-gap")
    envelope = accepted_envelope(
        gap_selection,
        request,
        expose.option_id,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )

    compiled = scenario(
        (gap_selection,),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            accepted_envelopes=(envelope,),
        ),
    )

    assert compiled.publishable
    assert compiled.selected_candidate_ids == ()
    assert compiled.complementary_candidate_ids == ()
    assert compiled.resolved_selections == ()
    assert len(compiled.network_gaps) == 1
    gap = compiled.network_gaps[0]
    assert gap.network_role == "interurban-spine"
    assert gap.unsatisfied_network_place_ids == ("bath", "saltford")
    assert gap.unsatisfied_access_obligation_ids == ("secondary-school",)
    assert gap.unsatisfied_strategic_destination_ids == ()
    assert "accepted-decision-ledger-changes" in gap.change_conditions
    assert gap.lineage_fingerprints

    governed = reference_decision(compiled)
    reference = adopt_reference_satn(
        compiled,
        governed_decision=governed,
    )
    assert reference.selected_candidate_ids == ()
    assert reference.complementary_candidate_ids == ()
    assert reference.network_gap_ids == (gap.gap_id,)

    tampered = compiled.model_dump(mode="json")
    tampered["network_gaps"][0]["unsatisfied_access_obligation_ids"] = []
    tampered["network_gaps"][0]["gap_id"] = ""
    tampered["network_gaps"][0]["gap_fingerprint"] = ""
    tampered["scenario_id"] = ""
    tampered["scenario_fingerprint"] = ""
    with pytest.raises(ValidationError, match="exact unsatisfied Candidate Set"):
        ScenarioCompilation.model_validate(tampered)


def test_mixed_alignment_and_explicit_gap_form_one_publishable_scenario() -> None:
    clear_set = candidate_set(candidate("mixed-clear"))
    gap_set = candidate_set(
        candidate(
            "mixed-gap",
            endpoints=("radstock", "midsomer-norton"),
            places=("radstock", "midsomer-norton"),
            obligations=("college-access",),
            topology="unsatisfied",
        ),
        places=("radstock", "midsomer-norton"),
        obligations=("college-access",),
    )
    clear = select_preferred_alignment(
        profile(),
        clear_set,
        criteria(clear_set),
    )
    gap = select_preferred_alignment(
        profile(),
        gap_set,
        criteria(gap_set),
    )
    requirements = ("bath", "midsomer-norton", "radstock", "saltford")
    base = scenario(
        (clear, gap),
        mode="provisional-review-awaiting-decision",
        mandatory_places=requirements,
        mandatory_obligations=("college-access", "secondary-school"),
    )
    request = build_alignment_decision_request(
        gap,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    expose = next(item for item in request.options if item.action == "expose-network-gap")
    envelope = accepted_envelope(
        gap,
        request,
        expose.option_id,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    compiled = scenario(
        (clear, gap),
        mandatory_places=requirements,
        mandatory_obligations=("college-access", "secondary-school"),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            accepted_envelopes=(envelope,),
        ),
    )
    assert compiled.publishable
    assert tuple(item.candidate_set_id for item in compiled.resolved_selections) == (
        clear.candidate_set_id,
    )
    assert tuple(item.candidate_set_id for item in compiled.network_gaps) == (gap.candidate_set_id,)
    assert compiled.selected_candidate_ids == (clear.selected_candidate_id,)


@pytest.mark.parametrize("generated", [False, True])
def test_zero_admitted_candidates_produce_an_honest_typed_network_gap(
    generated: bool,
) -> None:
    seed_set = candidate_set(candidate(f"gap-evidence-seed-{str(generated).lower()}"))
    snapshot = criteria(seed_set).evidence_snapshot
    generated_candidates = (
        (
            candidate(
                "missing-mandatory-coverage",
                places=("bath",),
                obligations=(),
            ),
        )
        if generated
        else ()
    )
    admitted = admit_candidate_set(
        profile(),
        network_role="interurban-spine",
        endpoints=("bath", "saltford"),
        candidates=generated_candidates,
        mandatory_network_place_ids=("bath", "saltford"),
        mandatory_access_obligation_ids=("secondary-school",),
        mandatory_strategic_destination_ids=("university-campus",),
    )
    assert admitted.admitted_candidates == ()
    assert admitted.generation_gap_reason == (
        CandidateGenerationGapReason.ALL_GENERATED_CANDIDATES_REJECTED
        if generated
        else CandidateGenerationGapReason.NO_GENERATED_CANDIDATES
    )
    if generated:
        assert admitted.admissions[0].rationale == "missing-candidate-set-role-obligation"

    gap_evidence = CandidateSetGapEvidence(
        candidate_set=admitted,
        evidence_snapshot=snapshot,
        rejected_candidate_ids=tuple(item.candidate_id for item in admitted.candidates),
        unsatisfied_network_place_ids=("bath", "saltford"),
        unsatisfied_access_obligation_ids=("secondary-school",),
        unsatisfied_strategic_destination_ids=("university-campus",),
        generation_gap_reason=admitted.generation_gap_reason,
    )
    selection = select_preferred_alignment(
        profile(),
        admitted,
        gap_evidence,
    )
    assert selection.disposition == SelectionDisposition.NETWORK_GAP
    assert selection.precomparison_rejections == admitted.admissions
    base = scenario(
        (selection,),
        mode="provisional-review-awaiting-decision",
        mandatory_destinations=("university-campus",),
    )
    request = build_alignment_decision_request(
        selection,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    assert {item.action for item in request.options} == {
        "expose-network-gap",
        "terminate",
    }


def test_review_orchestration_uses_bounded_dependency_frontier_and_fresh_replay() -> None:
    bounded_profile = profile(
        maximum_actionable_requests=1,
        maximum_review_rounds=2,
    )
    upstream_set = candidate_set(
        candidate("review-upstream"),
        selection_profile=bounded_profile,
    )
    downstream_set = candidate_set(
        candidate(
            "review-downstream",
            endpoints=("radstock", "midsomer-norton"),
            places=("radstock", "midsomer-norton"),
        ),
        selection_profile=bounded_profile,
        places=("radstock", "midsomer-norton"),
    )
    upstream = select_preferred_alignment(
        bounded_profile,
        upstream_set,
        criteria(
            upstream_set,
            uncertainty={upstream_set.admitted_candidates[0].candidate_id: "unknown"},
        ),
    )
    downstream = select_preferred_alignment(
        bounded_profile,
        downstream_set,
        criteria(
            downstream_set,
            uncertainty={downstream_set.admitted_candidates[0].candidate_id: "unknown"},
        ),
    )
    base = scenario(
        (upstream, downstream),
        mode="provisional-review-awaiting-decision",
    )
    dependencies = (
        ScenarioReviewDependency(candidate_set_id=upstream.candidate_set_id),
        ScenarioReviewDependency(
            candidate_set_id=downstream.candidate_set_id,
            depends_on_candidate_set_ids=(upstream.candidate_set_id,),
        ),
    )
    first = orchestrate_scenario_review(
        base,
        dependencies=dependencies,
        run_instance_id="frontier-run-1",
    )
    assert first.round_number == 1
    assert tuple(item.request.candidate_set_id for item in first.actionable_requests) == (
        upstream.candidate_set_id,
    )
    assert tuple(item.request.candidate_set_id for item in first.nonactionable_requests) == (
        downstream.candidate_set_id,
    )
    assert first.nonactionable_requests[0].blocked_by_candidate_set_ids == (
        upstream.candidate_set_id,
    )

    request = first.actionable_requests[0].request
    fallback = next(item for item in request.options if item.action == "accept-profile-fallback")
    envelope = accepted_envelope(
        upstream,
        request,
        fallback.option_id,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    replayed = scenario(
        (upstream, downstream),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            accepted_envelopes=(envelope,),
        ),
    )
    second = orchestrate_scenario_review(
        replayed,
        dependencies=dependencies,
        run_instance_id="frontier-run-2",
        prior_orchestration=first,
    )
    assert second.round_number == 2
    assert tuple(item.request.candidate_set_id for item in second.actionable_requests) == (
        downstream.candidate_set_id,
    )
    assert second.nonactionable_requests == ()
    assert second.replay_directive == "recompile-whole-network-on-ledger-change"
    assert second.round_history[0].compiler_attestation
    assert second.review_run.run_id != first.review_run.run_id
    assert second.review_run.prior_orchestration_fingerprint == first.orchestration_fingerprint

    tampered = second.model_dump(mode="json")
    tampered["actionable_requests"] = []
    tampered["orchestration_fingerprint"] = ""
    with pytest.raises(ValidationError, match="not compiler-derived"):
        ScenarioReviewOrchestration.model_validate(tampered)


def test_review_orchestration_preserves_cumulative_three_round_ledger() -> None:
    bounded_profile = profile(
        maximum_actionable_requests=1,
        maximum_review_rounds=3,
    )
    configurations = (
        ("chain-one", ("bath", "saltford")),
        ("chain-two", ("radstock", "midsomer-norton")),
        ("chain-three", ("keynsham", "bristol")),
    )
    selections: list[PreferredStrategicAlignment] = []
    for label, endpoints in configurations:
        admitted = candidate_set(
            candidate(label, endpoints=endpoints, places=endpoints),
            selection_profile=bounded_profile,
            places=endpoints,
        )
        selections.append(
            select_preferred_alignment(
                bounded_profile,
                admitted,
                criteria(
                    admitted,
                    uncertainty={admitted.admitted_candidates[0].candidate_id: "unknown"},
                ),
            )
        )
    dependencies = tuple(
        ScenarioReviewDependency(
            candidate_set_id=selection.candidate_set_id,
            depends_on_candidate_set_ids=(
                () if index == 0 else (selections[index - 1].candidate_set_id,)
            ),
        )
        for index, selection in enumerate(selections)
    )
    compiled = scenario(
        tuple(selections),
        mode="provisional-review-awaiting-decision",
    )
    first = orchestrate_scenario_review(
        compiled,
        dependencies=dependencies,
        run_instance_id="ledger-run-1",
    )
    cumulative: list[AcceptedDecisionEnvelope] = []
    prior = first
    for expected_round, selection in enumerate(selections[:2], start=2):
        request = prior.actionable_requests[0].request
        fallback = next(
            item for item in request.options if item.action == "accept-profile-fallback"
        )
        cumulative.append(
            accepted_envelope(
                selection,
                request,
                fallback.option_id,
                scenario_context_fingerprint=compiled.scenario_context_fingerprint,
            )
        )
        compiled = scenario(
            tuple(selections),
            decision_record=ScenarioDecisionRecord(
                mode="accepted-agent-decision-ledger",
                accepted_envelopes=tuple(cumulative),
            ),
        )
        prior = orchestrate_scenario_review(
            compiled,
            dependencies=dependencies,
            run_instance_id=f"ledger-run-{expected_round}",
            prior_orchestration=prior,
        )
        assert prior.round_number == expected_round
        assert {
            item.envelope_fingerprint for item in prior.scenario.decision_record.accepted_envelopes
        } == {item.envelope_fingerprint for item in cumulative}

    assert prior.actionable_requests[0].request.candidate_set_id == (selections[2].candidate_set_id)
    dropped_first = scenario(
        tuple(selections),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            accepted_envelopes=(cumulative[-1],),
        ),
    )
    with pytest.raises(ValueError, match="preserve every prior entry byte-for-byte"):
        orchestrate_scenario_review(
            dropped_first,
            dependencies=dependencies,
            run_instance_id="ledger-run-invalid",
            prior_orchestration=prior,
        )


def test_timeout_is_a_counted_frontier_attempt_and_empty_replay_cannot_advance() -> None:
    bounded_profile = profile(maximum_review_rounds=1)
    admitted = candidate_set(
        candidate("timeout-attempt"),
        selection_profile=bounded_profile,
    )
    selection = select_preferred_alignment(
        bounded_profile,
        admitted,
        criteria(
            admitted,
            uncertainty={admitted.admitted_candidates[0].candidate_id: "unknown"},
        ),
    )
    base = scenario(
        (selection,),
        mode="provisional-review-awaiting-decision",
    )
    dependencies = (ScenarioReviewDependency(candidate_set_id=selection.candidate_set_id),)
    first = orchestrate_scenario_review(
        base,
        dependencies=dependencies,
        run_instance_id="timeout-run-1",
    )
    with pytest.raises(ValidationError, match="exact typed result"):
        RuntimeDecisionAttempt(
            request=first.actionable_requests[0].request,
            outcome="provider-timeout",
            provider_failure_code="adapter-timeout",
        )
    attempt = RuntimeDecisionAttempt(
        request=first.actionable_requests[0].request,
        outcome="provider-timeout",
        provider_failure_code="adapter-timeout",
        invocation_record=RuntimeInvocationRecord(
            invocation_id=f"timeout-{first.review_run.run_id[-12:]}",
            review_run_id=first.review_run.run_id,
            run_instance_id=first.review_run.run_instance_id,
            run_scope_fingerprint=first.review_run.run_scope_fingerprint,
            run_config_fingerprint=first.review_run.run_config_fingerprint,
            attempt_number=1,
            maximum_attempts=first.review_run.maximum_attempts,
            deadline_seconds=first.review_run.deadline_seconds,
            frontier_fingerprint=review_frontier_fingerprint(first),
            request_fingerprint=first.actionable_requests[0].request.request_fingerprint,
            outcome="provider-timeout",
            failure_code="adapter-timeout",
            started_at_ms=1000,
            completed_at_ms=2000,
        ),
    )
    replayed = scenario(
        (selection,),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            runtime_attempts=(attempt,),
        ),
    )
    exhausted = orchestrate_scenario_review(
        replayed,
        dependencies=dependencies,
        run_instance_id="timeout-run-2",
        prior_orchestration=first,
    )
    assert exhausted.human_intervention_request is not None
    assert exhausted.human_intervention_request.reason == "maximum-review-rounds-exhausted"
    assert exhausted.review_run.run_id != first.review_run.run_id
    with pytest.raises(ValueError, match="nonempty counted decision-attempt delta"):
        orchestrate_scenario_review(
            replayed,
            dependencies=dependencies,
            run_instance_id="timeout-run-3",
            prior_orchestration=exhausted,
        )


def test_review_run_instances_reset_attempt_numbers_without_duplicate_pairs() -> None:
    selection_profile = profile(maximum_review_rounds=3)
    admitted = candidate_set(
        candidate("run-instance"),
        selection_profile=selection_profile,
    )
    selection = select_preferred_alignment(
        selection_profile,
        admitted,
        criteria(
            admitted,
            uncertainty={admitted.admitted_candidates[0].candidate_id: "unknown"},
        ),
    )
    base = scenario((selection,), mode="provisional-review-awaiting-decision")
    dependencies = (ScenarioReviewDependency(candidate_set_id=selection.candidate_set_id),)
    first = orchestrate_scenario_review(
        base,
        dependencies=dependencies,
        run_instance_id="run-instance-a",
    )

    def timeout_attempt(orchestration: ScenarioReviewOrchestration) -> RuntimeDecisionAttempt:
        request = orchestration.actionable_requests[0].request
        return RuntimeDecisionAttempt(
            request=request,
            outcome="provider-timeout",
            provider_failure_code="adapter-timeout",
            invocation_record=RuntimeInvocationRecord(
                invocation_id=f"timeout-{orchestration.review_run.run_id[-12:]}",
                review_run_id=orchestration.review_run.run_id,
                run_instance_id=orchestration.review_run.run_instance_id,
                run_scope_fingerprint=orchestration.review_run.run_scope_fingerprint,
                run_config_fingerprint=orchestration.review_run.run_config_fingerprint,
                attempt_number=1,
                maximum_attempts=orchestration.review_run.maximum_attempts,
                deadline_seconds=orchestration.review_run.deadline_seconds,
                frontier_fingerprint=review_frontier_fingerprint(orchestration),
                request_fingerprint=request.request_fingerprint,
                outcome="provider-timeout",
                failure_code="adapter-timeout",
                started_at_ms=1000,
                completed_at_ms=2000,
            ),
        )

    first_attempt = timeout_attempt(first)
    first_ledger = scenario(
        (selection,),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            runtime_attempts=(first_attempt,),
        ),
    )
    assert first_ledger.decision_record.review_run_provenance == (
        ReviewRunLedgerProvenance.from_invocation(first_attempt.invocation_record),
    )
    with pytest.raises(ValueError, match="already owns run_instance_id"):
        orchestrate_scenario_review(
            first_ledger,
            dependencies=dependencies,
            run_instance_id="run-instance-a",
        )
    with pytest.raises(ValueError, match="different immutable scope or configuration"):
        orchestrate_scenario_review(
            first_ledger,
            dependencies=dependencies,
            run_instance_id="run-instance-a",
            agent_config=AgentConfig(deadline_seconds=31),
        )
    second = orchestrate_scenario_review(
        first_ledger,
        dependencies=dependencies,
        run_instance_id="run-instance-b",
        prior_orchestration=first,
    )
    second_attempt = timeout_attempt(second)
    assert first_attempt.invocation_record is not None
    assert second_attempt.invocation_record is not None
    assert first_attempt.invocation_record.attempt_number == 1
    assert second_attempt.invocation_record.attempt_number == 1
    assert (
        first_attempt.invocation_record.review_run_id
        != second_attempt.invocation_record.review_run_id
    )
    assert second.review_run.run_instance_id == "run-instance-b"

    retained_ledger = scenario(
        (selection,),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            runtime_attempts=(first_attempt, second_attempt),
        ),
    )
    third = orchestrate_scenario_review(
        retained_ledger,
        dependencies=dependencies,
        run_instance_id="run-instance-c",
        prior_orchestration=second,
    )
    assert third.review_run.run_instance_id == "run-instance-c"

    duplicate_record = second_attempt.invocation_record.model_copy(
        update={"invocation_id": "duplicate-run-instance", "receipt_fingerprint": ""}
    )
    duplicate_attempt = RuntimeDecisionAttempt(
        request=second_attempt.request,
        outcome="provider-timeout",
        provider_failure_code="adapter-timeout",
        invocation_record=duplicate_record,
    )
    with pytest.raises(ValidationError, match=r"unique \(review_run_id, attempt_number\)"):
        ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            runtime_attempts=(first_attempt, second_attempt, duplicate_attempt),
        )


@pytest.mark.parametrize(
    "changed_run",
    (
        {
            "deadline_seconds": 31.0,
            "maximum_attempts": 3,
            "run_scope_fingerprint": digest("scope-a"),
        },
        {
            "deadline_seconds": 30.0,
            "maximum_attempts": 4,
            "run_scope_fingerprint": digest("scope-a"),
        },
        {
            "deadline_seconds": 30.0,
            "maximum_attempts": 3,
            "run_scope_fingerprint": digest("scope-b"),
        },
    ),
)
def test_runtime_ledger_rejects_parallel_mutable_provenance_for_one_instance(
    changed_run: dict[str, object],
) -> None:
    admitted = candidate_set(candidate("parallel-run-provenance"))
    selection = select_preferred_alignment(
        profile(),
        admitted,
        criteria(admitted, uncertainty={admitted.admitted_candidates[0].candidate_id: "unknown"}),
    )
    compiled = scenario((selection,), mode="provisional-review-awaiting-decision")
    request = build_alignment_decision_request(
        selection,
        scenario_context_fingerprint=compiled.scenario_context_fingerprint,
    )
    base_run = ReviewRun(
        run_instance_id="parallel-instance",
        run_scope_fingerprint=digest("scope-a"),
        deadline_seconds=30.0,
        maximum_attempts=3,
    )
    alternate_run = ReviewRun(run_instance_id="parallel-instance", **changed_run)

    def attempt(run: ReviewRun, invocation_id: str) -> RuntimeDecisionAttempt:
        return RuntimeDecisionAttempt(
            request=request,
            outcome="provider-timeout",
            provider_failure_code="adapter-timeout",
            invocation_record=RuntimeInvocationRecord(
                invocation_id=invocation_id,
                review_run_id=run.run_id,
                run_instance_id=run.run_instance_id,
                run_scope_fingerprint=run.run_scope_fingerprint,
                run_config_fingerprint=run.run_config_fingerprint,
                attempt_number=1,
                maximum_attempts=run.maximum_attempts,
                deadline_seconds=run.deadline_seconds,
                frontier_fingerprint=digest("parallel-frontier"),
                request_fingerprint=request.request_fingerprint,
                outcome="provider-timeout",
                failure_code="adapter-timeout",
                started_at_ms=1000,
                completed_at_ms=2000,
            ),
        )

    with pytest.raises(ValidationError, match="run_instance_id requires one immutable"):
        ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            runtime_attempts=(
                attempt(base_run, "parallel-a"),
                attempt(alternate_run, "parallel-b"),
            ),
        )


def test_runtime_ledger_rejects_one_run_id_mapped_to_foreign_instance() -> None:
    admitted = candidate_set(candidate("foreign-run-provenance"))
    selection = select_preferred_alignment(
        profile(),
        admitted,
        criteria(admitted, uncertainty={admitted.admitted_candidates[0].candidate_id: "unknown"}),
    )
    compiled = scenario((selection,), mode="provisional-review-awaiting-decision")
    request = build_alignment_decision_request(
        selection,
        scenario_context_fingerprint=compiled.scenario_context_fingerprint,
    )
    run = ReviewRun(
        run_instance_id="foreign-instance-a",
        run_scope_fingerprint=digest("foreign-scope"),
    )

    def attempt(invocation_id: str, instance_id: str) -> RuntimeDecisionAttempt:
        return RuntimeDecisionAttempt(
            request=request,
            outcome="provider-timeout",
            provider_failure_code="adapter-timeout",
            invocation_record=RuntimeInvocationRecord(
                invocation_id=invocation_id,
                review_run_id=run.run_id,
                run_instance_id=instance_id,
                run_scope_fingerprint=run.run_scope_fingerprint,
                run_config_fingerprint=run.run_config_fingerprint,
                attempt_number=1 if instance_id == "foreign-instance-a" else 2,
                maximum_attempts=run.maximum_attempts,
                deadline_seconds=run.deadline_seconds,
                frontier_fingerprint=digest("foreign-frontier"),
                request_fingerprint=request.request_fingerprint,
                outcome="provider-timeout",
                failure_code="adapter-timeout",
                started_at_ms=1000,
                completed_at_ms=2000,
            ),
        )

    with pytest.raises(ValidationError, match="review_run_id requires one immutable"):
        ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            runtime_attempts=(
                attempt("foreign-a", "foreign-instance-a"),
                attempt("foreign-b", "foreign-instance-b"),
            ),
        )


@pytest.mark.parametrize(
    ("dependency_kind", "expected_reason"),
    [
        ("cycle", "cyclic-review-dependency"),
        ("missing", "missing-upstream-decision"),
        ("round-cap", "maximum-review-rounds-exhausted"),
        ("convergence", "unresolved-review-convergence"),
    ],
)
def test_review_orchestration_terminates_with_typed_human_intervention(
    dependency_kind: str,
    expected_reason: str,
) -> None:
    bounded_profile = profile(maximum_review_rounds=(2 if dependency_kind == "convergence" else 1))
    admitted = candidate_set(
        candidate(f"intervention-{dependency_kind}"),
        selection_profile=bounded_profile,
    )
    selection = select_preferred_alignment(
        bounded_profile,
        admitted,
        criteria(
            admitted,
            uncertainty={admitted.admitted_candidates[0].candidate_id: "unknown"},
        ),
    )
    compiled = scenario(
        (selection,),
        mode="provisional-review-awaiting-decision",
    )
    if dependency_kind == "cycle":
        dependencies = (
            ScenarioReviewDependency(
                candidate_set_id=selection.candidate_set_id,
                depends_on_candidate_set_ids=(selection.candidate_set_id,),
            ),
        )
        prior = None
    elif dependency_kind == "missing":
        dependencies = ()
        prior = None
    else:
        dependencies = (
            ScenarioReviewDependency(
                candidate_set_id=selection.candidate_set_id,
            ),
        )
        prior = orchestrate_scenario_review(
            compiled,
            dependencies=dependencies,
            run_instance_id=f"intervention-{dependency_kind}-run-1",
        )
        request = prior.actionable_requests[0].request
        terminate = next(item for item in request.options if item.action == "terminate")
        if dependency_kind == "convergence":
            response = AlignmentDecisionResponse(
                request_id=request.request_id,
                request_fingerprint=request.request_fingerprint,
                option_id=terminate.option_id,
                evidence_ids=("network-assessment",),
                invocation=principal_assertion(
                    request,
                    option_id=terminate.option_id,
                ),
            )
            critique = AlignmentCritiqueRecord(
                request_fingerprint=request.request_fingerprint,
                response_fingerprint=response.response_fingerprint,
                selection_fingerprint=selection.selection_fingerprint,
                scenario_context_fingerprint=(compiled.scenario_context_fingerprint),
                evidence_snapshot_fingerprint=(request.evidence_snapshot_fingerprint),
                profile_fingerprint=request.profile_fingerprint,
                finding="rejected",
                resolved=False,
                evidence_ids=("network-assessment",),
                invocation=principal_assertion(
                    request,
                    critic=True,
                    response_fingerprint=response.response_fingerprint,
                    finding="rejected",
                    resolved=False,
                ),
            )
            revision = DecisionRevisionRecord(
                request=request,
                response=response,
                critique=critique,
                challenge_findings=(
                    AlignmentChallengeFinding(
                        challenge_id="mandatory-topology-challenge",
                        severity="mandatory-red",
                        evidence_ids=("network-assessment",),
                        missing_evidence_ids=("topology-continuity-proof",),
                    ),
                ),
                attempted_revision_fingerprints=(digest("attempt-1"),),
            )
            decision_record = ScenarioDecisionRecord(
                mode="accepted-agent-decision-ledger",
                revision_records=(revision,),
            )
        else:
            envelope = accepted_envelope(
                selection,
                request,
                terminate.option_id,
                scenario_context_fingerprint=(compiled.scenario_context_fingerprint),
            )
            decision_record = ScenarioDecisionRecord(
                mode="accepted-agent-decision-ledger",
                accepted_envelopes=(envelope,),
            )
        compiled = scenario(
            (selection,),
            decision_record=decision_record,
        )
    result = orchestrate_scenario_review(
        compiled,
        dependencies=dependencies,
        run_instance_id=f"intervention-{dependency_kind}-run-2",
        prior_orchestration=prior,
    )
    assert result.actionable_requests == ()
    assert result.human_intervention_request is not None
    assert result.human_intervention_request.reason == expected_reason
    assert result.human_intervention_request.lineage_fingerprints
    if dependency_kind == "convergence":
        assert result.human_intervention_request.missing_evidence_ids == (
            "topology-continuity-proof",
        )
        assert result.human_intervention_request.blocking_challenge_ids == (
            "mandatory-topology-challenge",
        )
        assert result.human_intervention_request.attempted_revision_fingerprints
        assert result.human_intervention_request.smallest_required_human_input


def test_challenges_require_revision_or_remain_unresolved() -> None:
    finding = AlignmentChallengeFinding(
        challenge_id="red-hard-gate",
        severity="mandatory-red",
        evidence_ids=("governed-red-evidence",),
        resolution="unresolved",
    )
    assert finding.resolution == "unresolved"


def test_challenge_lineage_blocks_publish_until_exact_later_acceptance() -> None:
    admitted = candidate_set(candidate("challenge-lineage"))
    selection = select_preferred_alignment(
        profile(),
        admitted,
        criteria(
            admitted,
            uncertainty={admitted.admitted_candidates[0].candidate_id: "unknown"},
        ),
    )
    base = scenario(
        (selection,),
        mode="provisional-review-awaiting-decision",
    )
    request = build_alignment_decision_request(
        selection,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    terminate = next(item for item in request.options if item.action == "terminate")
    response = AlignmentDecisionResponse(
        request_id=request.request_id,
        request_fingerprint=request.request_fingerprint,
        option_id=terminate.option_id,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            option_id=terminate.option_id,
        ),
    )
    critique = AlignmentCritiqueRecord(
        request_fingerprint=request.request_fingerprint,
        response_fingerprint=response.response_fingerprint,
        selection_fingerprint=selection.selection_fingerprint,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
        evidence_snapshot_fingerprint=request.evidence_snapshot_fingerprint,
        profile_fingerprint=request.profile_fingerprint,
        finding="rejected",
        resolved=False,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            request,
            critic=True,
            response_fingerprint=response.response_fingerprint,
            finding="rejected",
            resolved=False,
        ),
    )
    challenges = (
        AlignmentChallengeFinding(
            challenge_id="material-one",
            severity="material",
            evidence_ids=("network-assessment",),
        ),
        AlignmentChallengeFinding(
            challenge_id="material-two",
            severity="material",
            evidence_ids=("network-assessment",),
        ),
    )
    revision = DecisionRevisionRecord(
        request=request,
        response=response,
        critique=critique,
        challenge_findings=challenges,
    )
    fallback = next(item for item in request.options if item.action == "accept-profile-fallback")
    accepted_same_request = accepted_envelope(
        selection,
        request,
        fallback.option_id,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    with pytest.raises(ValueError, match="both accepted and revision"):
        ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            accepted_envelopes=(accepted_same_request,),
            revision_records=(revision,),
        )

    first_challenge = (challenges[0].challenge_fingerprint,)
    partial_request = build_alignment_decision_request(
        selection,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
        prior_challenge_fingerprints=first_challenge,
    )
    partial_fallback = next(
        item for item in partial_request.options if item.action == "accept-profile-fallback"
    )
    partial_acceptance = accepted_envelope(
        selection,
        partial_request,
        partial_fallback.option_id,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
        resolved_challenge_fingerprints=first_challenge,
        challenge_resolution_evidence_ids=("network-assessment",),
    )
    partly_resolved = scenario(
        (selection,),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            accepted_envelopes=(partial_acceptance,),
            revision_records=(revision,),
        ),
    )
    assert partly_resolved.resolved_selections
    assert not partly_resolved.publishable
    assert partly_resolved.decision_record.blocking_challenge_fingerprints == (
        challenges[1].challenge_fingerprint,
    )

    all_challenges = tuple(sorted(item.challenge_fingerprint for item in challenges))
    final_request = build_alignment_decision_request(
        selection,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
        prior_challenge_fingerprints=all_challenges,
    )
    final_fallback = next(
        item for item in final_request.options if item.action == "accept-profile-fallback"
    )
    final_acceptance = accepted_envelope(
        selection,
        final_request,
        final_fallback.option_id,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
        resolved_challenge_fingerprints=all_challenges,
        challenge_resolution_evidence_ids=("network-assessment",),
    )
    fully_resolved = scenario(
        (selection,),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            accepted_envelopes=(final_acceptance,),
            revision_records=(revision,),
        ),
    )
    assert fully_resolved.publishable
    assert not fully_resolved.decision_record.blocking_challenge_fingerprints


@pytest.mark.parametrize("unknown_section", ["directness", "gradient"])
def test_unknown_tie_break_evidence_forces_nonpublishable_stable_fallback(
    unknown_section: str,
) -> None:
    lower_raw = candidate(
        f"{unknown_section}-lower",
        directness=50.0 if unknown_section == "directness" else 100.0,
        gradient=1.0,
    )
    higher_known = candidate(
        f"{unknown_section}-higher",
        directness=100.0,
        gradient=2.0,
    )
    selection_profile = profile(review_when=[])
    admitted = candidate_set(
        lower_raw,
        higher_known,
        selection_profile=selection_profile,
    )
    states = {lower_raw.candidate_id: "unknown"}
    evidence = criteria(
        admitted,
        directness=(states if unknown_section == "directness" else None),
        gradient=(states if unknown_section == "gradient" else None),
    )

    result = select_preferred_alignment(
        selection_profile,
        admitted,
        evidence,
    )

    assert result.disposition == SelectionDisposition.PROVISIONAL_REVIEW
    assert not result.publishable
    assert result.active_frontier_candidate_ids == tuple(
        sorted((lower_raw.candidate_id, higher_known.candidate_id))
    )
    assert result.selected_candidate_id == min(
        lower_raw.candidate_id,
        higher_known.candidate_id,
    )
    assert result.ambiguity_triggers == ("material-grey-evidence",)


def test_topology_only_grey_is_present_in_detected_and_effective_trails() -> None:
    grey = candidate("topology-grey", topology="unknown")
    admitted = candidate_set(grey)

    result = select_preferred_alignment(profile(), admitted, criteria(admitted))

    assert result.detected_ambiguity_triggers == ("material-grey-evidence",)
    assert result.ambiguity_triggers == ("material-grey-evidence",)


def test_reference_requires_typed_decision_for_exact_scenario() -> None:
    only = candidate("only")
    admitted = candidate_set(only)
    selected = select_preferred_alignment(profile(), admitted, criteria(admitted))
    compiled = scenario((selected,))
    governed = reference_decision(compiled)
    reference = adopt_reference_satn(compiled, governed_decision=governed)
    assert reference.selected_candidate_ids == (only.candidate_id,)
    changed_adoption = governed.adoption_request.model_dump(mode="json")
    adoption_contract = changed_adoption["adoption_packet"]["adoption_contract"]
    adoption_contract["canonical_instructions"][0] = "Adopt an arbitrary scenario."
    adoption_contract["content_sha256"] = ""
    adoption_contract["contract_fingerprint"] = ""
    changed_adoption["adoption_packet"]["packet_fingerprint"] = ""
    changed_adoption["request_fingerprint"] = ""
    changed_decision = governed.model_dump(mode="json")
    changed_decision["adoption_request"] = changed_adoption
    changed_decision["decision_fingerprint"] = ""
    with pytest.raises(ValidationError, match="exact compiler contract"):
        GovernedReferenceSelectionDecision.model_validate(changed_decision)
    payload = reference.model_dump(mode="json")
    payload["selected_candidate_ids"] = [candidate("foreign").candidate_id]
    payload["reference_selection_fingerprint"] = ""
    with pytest.raises(ValidationError, match="do not match"):
        ReferenceSATNSelection.model_validate(payload)


@pytest.mark.parametrize(
    "source_url",
    (
        "http:foo",
        "https://",
        "https://user@example.com/minutes",
        "committee://minutes",
        "reference:/unsafe/path",
    ),
)
def test_reference_source_url_requires_strict_scheme_syntax(source_url: str) -> None:
    admitted = candidate_set(candidate("reference-url"))
    selected = select_preferred_alignment(profile(), admitted, criteria(admitted))
    decision = reference_decision(scenario((selected,)))
    payload = decision.model_dump(mode="json")
    payload["source_url"] = source_url
    payload["decision_fingerprint"] = ""
    with pytest.raises(ValidationError, match=r"source URL|source_url"):
        GovernedReferenceSelectionDecision.model_validate(payload)


def test_selection_rejects_forged_change_conditions_and_winner() -> None:
    left = candidate("left", directness=90.0)
    right = candidate("right", directness=110.0)
    admitted = candidate_set(left, right)
    selected = select_preferred_alignment(profile(), admitted, criteria(admitted))
    payload = selected.model_dump(mode="json")
    payload["change_conditions"] = ["network-selection-profile-changes"]
    payload["selection_fingerprint"] = ""
    with pytest.raises(ValidationError):
        PreferredStrategicAlignment.model_validate(payload)

    payload = selected.model_dump(mode="json")
    payload["selected_candidate_id"] = right.candidate_id
    payload["admitted_loser_ids"] = [left.candidate_id]
    payload["selection_fingerprint"] = ""
    with pytest.raises(ValidationError):
        PreferredStrategicAlignment.model_validate(payload)


def test_provisional_selection_cannot_be_adopted_as_reference() -> None:
    unknown = candidate("unknown", topology="unknown")
    admitted = candidate_set(unknown)
    gap = select_preferred_alignment(profile(), admitted, criteria(admitted))
    compiled = scenario(
        (gap,),
        mode="profile-fallback-awaiting-review",
    )
    governed = reference_decision(compiled)
    with pytest.raises(ValueError, match="resolved"):
        adopt_reference_satn(compiled, governed_decision=governed)
