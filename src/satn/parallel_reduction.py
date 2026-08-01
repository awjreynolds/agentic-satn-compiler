"""Raw, deterministic parallel-alignment reduction.

This is deliberately a small compiler seam.  Callers supply governed metric
route chains and observations, never pre-built candidate sets or criteria.  It
discovers symmetric parallel chains, assembles the existing selection evidence
models, and returns a complete Scenario Compilation wrapped with the discovery
artifact needed for inspection.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

import geopandas as gpd
from pydantic import BaseModel, ConfigDict, Field, field_validator
from shapely.geometry import LineString, Point, box

from satn.alignment_selection import (
    AlignmentCandidateInput,
    AssessmentKind,
    CandidateCriteria,
    CandidatePopulationOptionBinding,
    CanonicalLineString,
    CriterionDetail,
    CriterionFinding,
    EducationCriterionSummary,
    GovernedAssessmentBinding,
    GovernedEducationCriterionBinding,
    GovernedEvidenceSnapshot,
    NetworkRole,
    PopulationCriterionSummary,
    ScenarioCompilation,
    ScenarioCriteriaBinding,
    ScenarioDecisionRecord,
    admit_candidate_set,
    education_option_id_for_candidate,
    select_preferred_alignment,
)
from satn.education_access import (
    SchoolRegisterEvidence,
    assess_education_access,
    governed_education_assessment_fingerprint,
)
from satn.network_selection import NetworkSelectionProfile
from satn.population_reach import (
    CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
    CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
    CurrentDevelopmentEvidence,
    PopulationReachProfile,
    PopulationReachSource,
    compile_population_reach,
)

_SCOPE = Literal["urban", "rural", "unresolved"]


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class ParallelRoute(BaseModel):
    """One governed continuous chain in EPSG:27700 metric coordinates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    route_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")
    endpoints: tuple[str, str]
    coordinates: tuple[tuple[float, float], ...]
    network_scope: _SCOPE
    source_class: Literal[
        "verified-existing-asset", "a-road-corridor", "b-road-corridor", "other-routable"
    ] = "other-routable"
    evidence_ids: tuple[str, ...] = ()
    provenance_ids: tuple[str, ...] = ()
    topology: Literal["satisfied", "unknown", "unsatisfied"] = "satisfied"
    population: int = Field(default=0, ge=0)
    gradient_pct: float = Field(default=0.0, ge=0.0)
    access_score: float = 0.0
    existing_infrastructure_score: float = 0.0
    access_only_quiet_lane: bool = False
    node_ids: tuple[str, ...] = ()

    @field_validator("endpoints")
    @classmethod
    def _endpoints(cls, value: tuple[str, str]) -> tuple[str, str]:
        if len(value) != 2 or value[0] == value[1]:
            raise ValueError("route endpoints must be distinct")
        return tuple(sorted(value))

    @field_validator("coordinates")
    @classmethod
    def _coordinates(
        cls, value: tuple[tuple[float, float], ...]
    ) -> tuple[tuple[float, float], ...]:
        if len(value) < 2 or len(set(value)) < 2:
            raise ValueError("route requires a continuous metric line")
        return value


class ParallelReductionConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    urban_proximity_m: float = Field(default=500.0, gt=0)
    rural_proximity_m: float = Field(default=1_500.0, gt=0)
    minimum_symmetric_coverage_pct: float = Field(default=80.0, ge=0, le=100)
    material_population_difference: int = Field(default=1, ge=0)
    material_score_difference: float = Field(default=1.0, ge=0)


class ParallelReductionRequest(BaseModel):
    """Data-only compiler input; all route candidates are discovered here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    routes: tuple[ParallelRoute, ...] = Field(min_length=1)
    config: ParallelReductionConfig = Field(default_factory=ParallelReductionConfig)
    area_id: str = "parallel-reduction-area"
    # A junction is explicit topology.  A geometric intersection alone is not one.
    junction_node_ids: tuple[str, ...] = ()
    required_transitions: tuple[tuple[str, str], ...] = ()
    officer_decisions: tuple[PreloadedOfficerDecision, ...] = ()


class ParallelCandidateRelation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    route_ids: tuple[str, str]
    left_coverage_pct: float
    right_coverage_pct: float
    scope_sensitive: bool = False


class PreloadedOfficerDecision(BaseModel):
    """Persistent initial decision on one stable logical route group."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_id: str
    route_id: str


class ParallelDecisionArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_id: str
    compiler_preferred_route_id: str
    selected_route_id: str
    mode: Literal["deterministic", "agent", "fallback", "officer"]
    fallback_trigger: str | None = None


class CrossingWarning(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    route_ids: tuple[str, str]
    reason: Literal["visual-crossing-without-junction"] = "visual-crossing-without-junction"


class NetworkGapArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    route_ids: tuple[str, str]
    intervention_archetype: Literal["bridge"] = "bridge"


class ParallelReductionArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relations: tuple[ParallelCandidateRelation, ...]
    selected_route_ids: tuple[str, ...]
    retained_route_ids: tuple[str, ...]
    decisions: tuple[ParallelDecisionArtifact, ...] = ()
    crossing_warnings: tuple[CrossingWarning, ...] = ()
    network_gaps: tuple[NetworkGapArtifact, ...] = ()
    officer_compiler_divergences: tuple[ParallelDecisionArtifact, ...] = ()
    fingerprint: str = ""


@dataclass(frozen=True)
class ParallelReductionCompilation:
    """Complete existing Scenario plus inspectable raw-discovery provenance."""

    scenario: ScenarioCompilation
    artifact: ParallelReductionArtifact

    @property
    def selected_route_ids(self) -> tuple[str, ...]:
        return self.artifact.selected_route_ids


class ParallelReductionRuntime(Protocol):
    def choose(self, request: Mapping[str, object]) -> str | Mapping[str, object]: ...


def _coverage(left: LineString, right: LineString, distance_m: float) -> float:
    # Segment length within the other route's local configurable buffer.
    if left.length == 0:
        return 0.0
    return 100.0 * left.intersection(right.buffer(distance_m)).length / left.length


def discover_parallel_relations(
    routes: tuple[ParallelRoute, ...], config: ParallelReductionConfig
) -> tuple[ParallelCandidateRelation, ...]:
    """Discover scope-sensitive symmetric candidates without topology inference."""

    relations: list[ParallelCandidateRelation] = []
    for index, left in enumerate(sorted(routes, key=lambda item: item.route_id)):
        for right in sorted(routes, key=lambda item: item.route_id)[index + 1 :]:
            if left.endpoints != right.endpoints:
                continue
            left_line, right_line = LineString(left.coordinates), LineString(right.coordinates)
            scopes = {left.network_scope, right.network_scope}
            if "unresolved" in scopes:
                urban = min(
                    _coverage(left_line, right_line, config.urban_proximity_m),
                    _coverage(right_line, left_line, config.urban_proximity_m),
                )
                rural = min(
                    _coverage(left_line, right_line, config.rural_proximity_m),
                    _coverage(right_line, left_line, config.rural_proximity_m),
                )
                if rural < config.minimum_symmetric_coverage_pct:
                    continue
                distance = config.rural_proximity_m
                sensitive = urban < config.minimum_symmetric_coverage_pct
            else:
                distance = (
                    config.urban_proximity_m if scopes == {"urban"} else config.rural_proximity_m
                )
                sensitive = False
            left_coverage, right_coverage = (
                _coverage(left_line, right_line, distance),
                _coverage(right_line, left_line, distance),
            )
            if min(left_coverage, right_coverage) >= config.minimum_symmetric_coverage_pct:
                relations.append(
                    ParallelCandidateRelation(
                        route_ids=(left.route_id, right.route_id),
                        left_coverage_pct=left_coverage,
                        right_coverage_pct=right_coverage,
                        scope_sensitive=sensitive,
                    )
                )
    return tuple(relations)


def _profile(request: ParallelReductionRequest) -> NetworkSelectionProfile:
    return NetworkSelectionProfile.model_validate(
        {
            "profile_id": request.profile_id,
            "candidate_source_precedence": [
                "verified-existing-asset",
                "a-road-corridor",
                "b-road-corridor",
                "other-routable",
            ],
            # The raw compiler resolves near-equivalence with stable route IDs.
            "ambiguity": {"review_when": []},
        }
    )


def _population_source() -> PopulationReachSource:
    return PopulationReachSource(
        source_id="parallel-reduction-governed-population",
        release="parallel-reduction/v1",
        effective_date="2021-03-21",
        licence="Open Government Licence v3.0",
        permitted_uses=("strategic-corridor-analysis",),
        known_limitations=("Synthetic governed metric evidence.",),
        transformation_lineage=("Parallel reduction input.",),
        source_uri="https://example.invalid/parallel-reduction",
        version="v1",
        content_sha256=_digest("parallel-population-source"),
        current_development_evidence=CurrentDevelopmentEvidence(
            source_id="parallel-development",
            release="v1",
            effective_date=date(2026, 1, 1),
            licence="Open Government Licence v3.0",
            content_sha256=_digest("parallel-development"),
            availability=CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
            conclusion=CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
        ),
        current_development_evidence_id="parallel-development",
    )


def _criteria(candidate_set, route_by_candidate: Mapping[str, ParallelRoute]) -> CandidateCriteria:
    candidates = candidate_set.admitted_candidates
    option_ids = {
        item.candidate_id: f"parallel-option-{index}" for index, item in enumerate(candidates)
    }
    lines = [item.geometry.as_shapely() for item in candidates]
    routes = gpd.GeoDataFrame(
        {"option_id": [option_ids[item.candidate_id] for item in candidates], "geometry": lines},
        geometry="geometry",
        crs="EPSG:27700",
    )
    records = []
    for index, item in enumerate(candidates):
        route = route_by_candidate[item.candidate_id]
        point = lines[index].interpolate(0.5, normalized=True)
        records.append(
            {
                "OA21CD": f"E{index:08d}",
                "usual_residents": route.population,
                "population_weighted_centroid": Point(point.x, point.y + 100.0),
                "geometry": Point(point.x, point.y + 100.0).buffer(1.0),
            }
        )
    areas = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:27700")
    minx, miny, maxx, maxy = routes.total_bounds
    area = gpd.GeoDataFrame(
        {"geometry": [box(minx - 2_000, miny - 2_000, maxx + 2_000, maxy + 2_000)]},
        geometry="geometry",
        crs="EPSG:27700",
    )
    population_assessment = compile_population_reach(
        routes, areas, area, source=_population_source(), profile=PopulationReachProfile()
    )
    bindings = tuple(
        CandidatePopulationOptionBinding(
            candidate_id=item.candidate_id,
            option_id=option_ids[item.candidate_id],
            assessment_geometry_sha256=item.geometry.population_geometry_sha256,
        )
        for item in candidates
    )
    education = assess_education_access(
        register_evidence=SchoolRegisterEvidence(
            evidence_id="parallel-school-register",
            source_name="parallel reduction",
            as_of=date(2026, 1, 1),
        ),
        schools=(),
        option_evidence=(),
        option_ids=tuple(
            education_option_id_for_candidate(item, candidate_set) for item in candidates
        ),
    )
    education_content = _digest(education.model_dump(mode="json"))
    education_source = _digest("parallel-education-source")
    governed = GovernedEducationCriterionBinding(
        school_ids=(),
        strategic_destination_ids=(),
        full_source_governed_fingerprint=education_source,
        governed_input_fingerprint=governed_education_assessment_fingerprint(
            governed_source_fingerprint=education_source,
            school_ids=(),
            strategic_destination_ids=(),
            assessment_content_sha256=education_content,
        ),
        assessment_content_sha256=education_content,
    )
    snapshot = GovernedEvidenceSnapshot(
        snapshot_id="parallel-reduction-evidence",
        assessments=(
            GovernedAssessmentBinding(
                kind=AssessmentKind.POPULATION_REACH,
                assessment_id=population_assessment.assessment_id,
                assessment_content_sha256=_digest(population_assessment.canonical()),
                source_content_sha256=population_assessment.source.content_sha256,
                method_version="population-reach/v1",
            ),
            GovernedAssessmentBinding(
                kind=AssessmentKind.EDUCATION_ACCESS,
                assessment_id=education.assessment_id,
                assessment_content_sha256=education_content,
                source_content_sha256=education_source,
                method_version="satn-governed-full-education-assessment-binding/v3",
            ),
            GovernedAssessmentBinding(
                kind=AssessmentKind.NETWORK_GEOMETRY,
                assessment_id="parallel-network",
                assessment_content_sha256=_digest("parallel-network"),
                source_content_sha256=_digest("parallel-network-source"),
                method_version="parallel-reduction/v1",
            ),
            GovernedAssessmentBinding(
                kind=AssessmentKind.TOPOGRAPHY,
                assessment_id="parallel-topography",
                assessment_content_sha256=_digest("parallel-topography"),
                source_content_sha256=_digest("parallel-topography-source"),
                method_version="parallel-reduction/v1",
            ),
        ),
    )
    return CandidateCriteria(
        evidence_snapshot=snapshot,
        population=PopulationCriterionSummary.from_assessment(
            population_assessment,
            option_bindings=bindings,
            scenario_evidence_snapshot_fingerprint=snapshot.snapshot_fingerprint,
        ),
        education=EducationCriterionSummary.from_assessment(
            education,
            candidate_set=candidate_set,
            scenario_evidence_snapshot_fingerprint=snapshot.snapshot_fingerprint,
            governed_binding=governed,
        ),
        directness=tuple(
            CriterionFinding(
                candidate_id=item.candidate_id,
                state="satisfied",
                detail=CriterionDetail.DIRECTNESS_EVIDENCE,
                assessment_id="parallel-network",
                evidence_record_id=f"directness-{item.candidate_id}",
            )
            for item in candidates
        ),
        gradient=tuple(
            CriterionFinding(
                candidate_id=item.candidate_id,
                state="satisfied",
                detail=CriterionDetail.GRADIENT_EVIDENCE,
                assessment_id="parallel-topography",
                evidence_record_id=f"gradient-{item.candidate_id}",
            )
            for item in candidates
        ),
        uncertainty=tuple(
            CriterionFinding(
                candidate_id=item.candidate_id,
                state="satisfied",
                detail=CriterionDetail.UNCERTAINTY_EVIDENCE,
                assessment_id="parallel-network",
                evidence_record_id=f"uncertainty-{item.candidate_id}",
            )
            for item in candidates
        ),
    )


def compile_parallel_reduction_scenario(
    request: ParallelReductionRequest, runtime: ParallelReductionRuntime | None = None
) -> ParallelReductionCompilation:
    """Compile raw routes through discovery and the existing Scenario seam.

    ``runtime`` is reserved for a material conflict; this first vertical slice
    deliberately completes dominance and deterministic near-equivalence without
    constructing it.
    """
    request = ParallelReductionRequest.model_validate(request)
    profile = _profile(request)
    relations = discover_parallel_relations(request.routes, request.config)
    relation_ids = {route_id for relation in relations for route_id in relation.route_ids}
    groups: dict[tuple[str, str], list[ParallelRoute]] = {}
    for route in request.routes:
        if route.route_id in relation_ids:
            groups.setdefault(route.endpoints, []).append(route)
    if not groups:
        raise ValueError("raw routes contain no qualifying symmetric parallel candidate")
    candidate_sets = []
    selections = []
    routes_by_group: dict[tuple[str, str], tuple[ParallelRoute, ...]] = {}
    for endpoints, routes in sorted(groups.items()):
        routes_by_group[endpoints] = tuple(sorted(routes, key=lambda item: item.route_id))
        inputs = tuple(
            AlignmentCandidateInput(
                network_role=NetworkRole.INTERURBAN_SPINE,
                endpoints=endpoints,
                source_class=route.source_class,
                geometry=CanonicalLineString(coordinates=route.coordinates),
                evidence_fingerprints=(_digest((route.route_id, route.evidence_ids)),),
                provenance_ids=route.provenance_ids or (route.route_id,),
                topology_state=route.topology,
                served_network_place_ids=endpoints,
                directness_m=LineString(route.coordinates).length,
                maximum_gradient_pct=route.gradient_pct,
            )
            for route in sorted(routes, key=lambda item: item.route_id)
        )
        candidate_set = admit_candidate_set(
            profile,
            network_role=NetworkRole.INTERURBAN_SPINE,
            endpoints=endpoints,
            candidates=inputs,
            mandatory_network_place_ids=endpoints,
        )
        by_candidate = {
            candidate.candidate_id: route
            for candidate, route in zip(
                sorted(candidate_set.candidates, key=lambda item: item.provenance_ids[0]),
                sorted(routes, key=lambda item: item.route_id),
                strict=True,
            )
        }
        criteria = _criteria(candidate_set, by_candidate)
        candidate_sets.append(candidate_set)
        selections.append(select_preferred_alignment(profile, candidate_set, criteria))
    snapshot = GovernedEvidenceSnapshot(
        snapshot_id="parallel-reduction-scenario",
        assessments=tuple(
            {
                (
                    item.criteria.evidence_snapshot.snapshot_fingerprint,
                    binding.kind,
                    binding.assessment_id,
                ): binding
                for item in selections
                for binding in item.criteria.evidence_snapshot.assessments
            }.values()
        ),
    )
    scenario = ScenarioCompilation(
        area_fingerprint=_digest(request.area_id),
        evidence_snapshot=snapshot,
        profile_fingerprint=profile.fingerprint,
        decision_record=ScenarioDecisionRecord(mode="no-agent-not-invoked"),
        candidate_sets=tuple(candidate_sets),
        selections=tuple(selections),
        criteria_bindings=tuple(
            ScenarioCriteriaBinding(
                candidate_set_id=item.candidate_set_id,
                criteria_fingerprint=item.criteria_fingerprint,
            )
            for item in selections
        ),
        required_network_role_ids=(NetworkRole.INTERURBAN_SPINE,),
        mandatory_network_place_ids=tuple(
            sorted(
                {
                    endpoint
                    for candidate_set in candidate_sets
                    for endpoint in candidate_set.endpoints
                }
            )
        ),
        lineage_fingerprints=(_digest(request.model_dump(mode="json")),),
    )
    route_by_candidate = {
        candidate.candidate_id: route.route_id
        for candidate_set, routes in zip(candidate_sets, routes_by_group.values(), strict=True)
        for candidate, route in zip(
            sorted(candidate_set.candidates, key=lambda item: item.provenance_ids[0]),
            routes,
            strict=True,
        )
    }
    decisions: list[ParallelDecisionArtifact] = []
    selected_by_group: dict[tuple[str, str], str] = {}
    officers = {item.target_id: item.route_id for item in request.officer_decisions}
    for endpoints, routes in routes_by_group.items():
        target_id = "parallel:" + ":".join(endpoints)
        compiler_preferred = next(
            route_by_candidate[item]
            for selection in selections
            if selection.candidate_set.endpoints == endpoints
            for item in (selection.selected_candidate_id,)
            if item is not None
        )
        by_population = max(routes, key=lambda item: (item.population, item.route_id)).route_id
        by_quality = max(
            routes,
            key=lambda item: (
                item.access_score
                + item.existing_infrastructure_score
                + (request.config.material_score_difference if item.access_only_quiet_lane else 0),
                item.route_id,
            ),
        ).route_id
        selected, mode, trigger = compiler_preferred, "deterministic", None
        # Access-only quiet lanes are not filtered out.  Where the governed
        # population result is otherwise comparable, the declared quiet-lane
        # access advantage is the deterministic tie-break before route ID.
        quiet = tuple(item for item in routes if item.access_only_quiet_lane)
        if (
            quiet
            and max(item.population for item in routes) - min(item.population for item in routes)
            < request.config.material_population_difference
        ):
            selected = min(quiet, key=lambda item: item.route_id).route_id
        if by_population != by_quality:
            route_population = {item.route_id: item.population for item in routes}
            route_quality = {
                item.route_id: item.access_score
                + item.existing_infrastructure_score
                + (request.config.material_score_difference if item.access_only_quiet_lane else 0)
                for item in routes
            }
            material = (
                abs(route_population[by_population] - route_population[by_quality])
                >= request.config.material_population_difference
                and abs(route_quality[by_population] - route_quality[by_quality])
                >= request.config.material_score_difference
            )
            if material and runtime is not None:
                raw = runtime.choose(
                    {
                        "target_id": target_id,
                        "route_ids": tuple(item.route_id for item in routes),
                        "compiler_preferred_route_id": compiler_preferred,
                    }
                )
                chosen = raw if isinstance(raw, str) else raw.get("route_id")
                if chosen in {item.route_id for item in routes}:
                    selected, mode = chosen, "agent"
                else:
                    mode, trigger = "fallback", "invalid-runtime-response"
            elif material:
                mode, trigger = "fallback", "runtime-unavailable"
        officer = officers.get(target_id)
        if officer is not None:
            if officer not in {item.route_id for item in routes}:
                raise ValueError("officer decision target is unavailable")
            selected, mode = officer, "officer"
        selected_by_group[endpoints] = selected
        decisions.append(
            ParallelDecisionArtifact(
                target_id=target_id,
                compiler_preferred_route_id=compiler_preferred,
                selected_route_id=selected,
                mode=mode,
                fallback_trigger=trigger,
            )
        )
    selected = tuple(sorted(selected_by_group.values()))
    retained = tuple(
        sorted(route.route_id for route in request.routes if route.route_id in relation_ids)
    )
    warnings = []
    for index, left in enumerate(request.routes):
        for right in request.routes[index + 1 :]:
            if left.endpoints == right.endpoints:
                continue
            if LineString(left.coordinates).intersects(LineString(right.coordinates)) and not (
                set(left.node_ids) & set(right.node_ids) & set(request.junction_node_ids)
            ):
                warnings.append(
                    CrossingWarning(route_ids=tuple(sorted((left.route_id, right.route_id))))
                )
    gaps = tuple(
        NetworkGapArtifact(route_ids=tuple(sorted(pair)))
        for pair in request.required_transitions
        if not (set(pair[0:1]) & set(pair[1:2]))
    )
    divergences = tuple(
        item
        for item in decisions
        if item.mode == "officer" and item.selected_route_id != item.compiler_preferred_route_id
    )
    artifact = ParallelReductionArtifact(
        relations=relations,
        selected_route_ids=selected,
        retained_route_ids=retained,
        decisions=tuple(decisions),
        crossing_warnings=tuple(warnings),
        network_gaps=gaps,
        officer_compiler_divergences=divergences,
    )
    return ParallelReductionCompilation(scenario=scenario, artifact=artifact)


ParallelReductionRequest.model_rebuild()
