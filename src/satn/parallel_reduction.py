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
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol, Self

import geopandas as gpd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from shapely.geometry import LineString, Point, box

from satn.alignment_selection import (
    AcceptedDecisionEnvelope,
    AgentAuthorityRole,
    AgentInvocation,
    AlignmentCandidateInput,
    AlignmentCritiqueRecord,
    AlignmentDecisionResponse,
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
    build_alignment_decision_request,
    education_option_id_for_candidate,
    select_preferred_alignment,
)
from satn.education_access import (
    SchoolRegisterEvidence,
    assess_education_access,
    governed_education_assessment_fingerprint,
)
from satn.network_selection import NetworkSelectionProfile
from satn.parallel_reduction_evidence import build_parallel_evidence
from satn.population_reach import (
    CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
    CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
    CurrentDevelopmentEvidence,
    PopulationReachProfile,
    PopulationReachSource,
    compile_population_reach,
)

_SCOPE = Literal["urban", "rural", "unresolved"]
_GUIDANCE_STATE = Literal["supported", "contradicted", "unassessed"]


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
    elevation_samples: tuple[tuple[float, float], ...] = ()
    guidance_considerations: tuple[GuidanceConsideration, ...] = ()

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


class GuidanceConsideration(BaseModel):
    """One separately governed guidance consideration, never a compliance score."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    principle_id: str
    state: _GUIDANCE_STATE = "unassessed"
    citation_ids: tuple[str, ...] = ()


class GuidanceProfile(BaseModel):
    """Frozen national guidance sources used only as separate considerations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str = "national-cycle-and-rural-guidance-2026-01"
    cycle_infrastructure_design: str = "Cycle Infrastructure Design (LTN 1/20)"
    cycle_infrastructure_design_citation: str = (
        "DfT, Cycle Infrastructure Design (LTN 1/20), July 2020"
    )
    ate_rural_design_guide: str = "Active Travel England Rural Design Guide"
    ate_rural_design_guide_citation: str = "Active Travel England, Rural Design Guide, 2025"
    profile_fingerprint: str = ""

    @model_validator(mode="after")
    def _fingerprint(self):
        value = _digest(self.model_dump(exclude={"profile_fingerprint"}))
        if self.profile_fingerprint and self.profile_fingerprint != value:
            raise ValueError("guidance profile fingerprint is stale")
        object.__setattr__(self, "profile_fingerprint", value)
        return self


class ParallelReductionConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    urban_proximity_m: float = Field(default=500.0, gt=0)
    rural_proximity_m: float = Field(default=1_500.0, gt=0)
    minimum_symmetric_coverage_pct: float = Field(default=80.0, ge=0, le=100)
    material_population_difference: int = Field(default=1, ge=0)
    material_score_difference: float = Field(default=1.0, ge=0)
    runtime_eligible: bool = False
    runtime_deadline_seconds: float = Field(default=10.0, gt=0)
    runtime_maximum_decisive_considerations: int = Field(default=3, ge=1, le=8)
    runtime_fallback_hierarchy: tuple[
        Literal["compiler-preferred", "population", "quality", "route-id"], ...
    ] = ("compiler-preferred", "population", "quality", "route-id")
    maximum_hybrids_per_group: int = Field(default=1, ge=0, le=5)
    section_length_m: float = Field(default=100.0, gt=0, le=1_000)
    urban_capture_radius_m: float = Field(default=250.0, gt=0)
    rural_capture_radius_m: float = Field(default=750.0, gt=0)
    material_population_absolute_residents: int = Field(default=500, ge=0)
    material_population_relative_pct: float = Field(default=50.0, ge=0)
    material_population_persistence_m: float = Field(default=500.0, gt=0)
    material_elevation_variation_m: float = Field(default=20.0, ge=0)
    material_elevation_variation_pct: float = Field(default=25.0, ge=0)

    @field_validator("runtime_fallback_hierarchy")
    @classmethod
    def _fallback_hierarchy(
        cls,
        value: tuple[Literal["compiler-preferred", "population", "quality", "route-id"], ...],
    ) -> tuple[Literal["compiler-preferred", "population", "quality", "route-id"], ...]:
        if not value or len(value) != len(set(value)) or value[-1] != "route-id":
            raise ValueError("runtime fallback hierarchy must be unique and end in route-id")
        return value


class ParallelOutputAreaCentroid(BaseModel):
    """One governed OA centroid; membership is declared, never inferred."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    oa_id: str = Field(pattern=r"^[A-Z][0-9]{8}$")
    residents: int = Field(ge=0)
    coordinates: tuple[float, float]
    inside_area: bool


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
    output_area_centroids: tuple[ParallelOutputAreaCentroid, ...] = ()
    output_area_source_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    guidance_profile: GuidanceProfile = Field(default_factory=GuidanceProfile)

    @model_validator(mode="after")
    def _governed_output_areas(self) -> Self:
        if bool(self.output_area_centroids) != bool(self.output_area_source_fingerprint):
            raise ValueError(
                "Output Area centroids and their governed source fingerprint are required together"
            )
        identifiers = tuple(item.oa_id for item in self.output_area_centroids)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Output Area centroid IDs must be unique")
        return self


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
    runtime_request_id: str | None = None
    offered_route_ids: tuple[str, ...] = ()
    offered_evidence_ids: tuple[str, ...] = ()
    offered_consideration_ids: tuple[str, ...] = ()
    decisive_consideration_ids: tuple[str, ...] = ()
    validation_status: Literal[
        "not-invoked",
        "accepted",
        "runtime-unavailable",
        "runtime-error",
        "runtime-timeout",
        "invalid-runtime-response",
    ] = "not-invoked"


class CrossingWarning(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    route_ids: tuple[str, str]
    reason: Literal["visual-crossing-without-junction"] = "visual-crossing-without-junction"


class NetworkGapArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    route_ids: tuple[str, str]
    intervention_archetype: Literal["bridge"] = "bridge"


class OfficerTargetUnavailable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_id: str
    route_id: str


class ParallelReductionArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relations: tuple[ParallelCandidateRelation, ...]
    selected_route_ids: tuple[str, ...]
    retained_route_ids: tuple[str, ...]
    decisions: tuple[ParallelDecisionArtifact, ...] = ()
    crossing_warnings: tuple[CrossingWarning, ...] = ()
    network_gaps: tuple[NetworkGapArtifact, ...] = ()
    officer_compiler_divergences: tuple[ParallelDecisionArtifact, ...] = ()
    officer_target_unavailable: tuple[OfficerTargetUnavailable, ...] = ()
    section_population_profile: dict[str, object] = Field(default_factory=dict)
    section_population_profile_fingerprint: str = ""
    material_population_differences: tuple[dict[str, object], ...] = ()
    cumulative_elevation_variation: tuple[dict[str, object], ...] = ()
    missing_evidence: tuple[str, ...] = ()
    section_population_sections: tuple[dict[str, object], ...] = ()
    guidance_profile_fingerprint: str = ""
    guidance_findings: tuple[dict[str, object], ...] = ()
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


def _route_quality(route: ParallelRoute, config: ParallelReductionConfig) -> float:
    return (
        route.access_score
        + route.existing_infrastructure_score
        + (config.material_score_difference if route.access_only_quiet_lane else 0)
    )


def _runtime_request(
    *,
    target_id: str,
    compiler_preferred_route_id: str,
    routes: tuple[ParallelRoute, ...],
) -> dict[str, object]:
    """Build the sole bounded runtime packet; never expose raw route datasets."""

    menu = tuple(
        {
            "route_id": route.route_id,
            "evidence_ids": tuple(sorted(route.evidence_ids or (f"route:{route.route_id}",))),
            "consideration_ids": (
                f"population:{route.route_id}",
                f"topography:{route.route_id}",
                f"access:{route.route_id}",
                f"existing-infrastructure:{route.route_id}",
                *(
                    f"guidance:{route.route_id}:{item.principle_id}"
                    for item in route.guidance_considerations
                ),
            ),
        }
        for route in sorted(routes, key=lambda item: item.route_id)
    )
    evidence_ids = tuple(sorted({item for route in menu for item in route["evidence_ids"]}))
    consideration_ids = tuple(
        sorted({item for route in menu for item in route["consideration_ids"]})
    )
    request_id = f"parallel-runtime-{_digest((target_id, menu))[:16]}"
    return {
        "request_id": request_id,
        "target_id": target_id,
        "compiler_preferred_route_id": compiler_preferred_route_id,
        "route_menu": menu,
        "offered_evidence_ids": evidence_ids,
        "offered_consideration_ids": consideration_ids,
    }


def _validate_runtime_response(
    raw: object,
    runtime_request: Mapping[str, object],
    config: ParallelReductionConfig,
) -> tuple[str | None, tuple[str, ...]]:
    """Accept only one offered route with bounded, route-relevant considerations."""

    if not isinstance(raw, Mapping) or set(raw) != {"route_id", "decisive_consideration_ids"}:
        return None, ()
    route_id = raw.get("route_id")
    decisive = raw.get("decisive_consideration_ids")
    if not isinstance(route_id, str) or not isinstance(decisive, (list, tuple)):
        return None, ()
    if not all(isinstance(item, str) for item in decisive):
        return None, ()
    decisive_ids = tuple(sorted(set(decisive)))
    if not decisive_ids or len(decisive_ids) != len(decisive):
        return None, ()
    if len(decisive_ids) > config.runtime_maximum_decisive_considerations:
        return None, ()
    menu = runtime_request["route_menu"]
    if not isinstance(menu, tuple):
        return None, ()
    selected = next(
        (item for item in menu if item.get("route_id") == route_id),
        None,
    )
    if selected is None:
        return None, ()
    offered = runtime_request["offered_consideration_ids"]
    if not isinstance(offered, tuple) or not set(decisive_ids).issubset(offered):
        return None, ()
    relevant = selected.get("consideration_ids")
    if not isinstance(relevant, tuple) or not set(decisive_ids).issubset(relevant):
        return None, ()
    return route_id, decisive_ids


def _call_runtime_once(
    runtime: ParallelReductionRuntime,
    request: Mapping[str, object],
    deadline_seconds: float,
) -> tuple[object | None, Literal["accepted", "runtime-error", "runtime-timeout"]]:
    """Call a synchronous provider once without allowing it to block compilation."""

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parallel-runtime")
    future = executor.submit(runtime.choose, request)
    try:
        return future.result(timeout=deadline_seconds), "accepted"
    except TimeoutError:
        future.cancel()
        return None, "runtime-timeout"
    except Exception:
        return None, "runtime-error"
    finally:
        # A timed-out provider cannot be reliably killed by Python.  Do not
        # wait for it, retry it, or pause the compilation for human input.
        executor.shutdown(wait=False, cancel_futures=True)


def _configured_fallback_route(
    routes: tuple[ParallelRoute, ...],
    compiler_preferred_route_id: str,
    config: ParallelReductionConfig,
) -> str:
    """Select reproducibly from the declared hierarchy for every failure mode."""

    contenders = tuple(routes)
    for criterion in config.runtime_fallback_hierarchy:
        if criterion == "compiler-preferred":
            matches = tuple(
                item for item in contenders if item.route_id == compiler_preferred_route_id
            )
        elif criterion == "population":
            highest = max(item.population for item in contenders)
            matches = tuple(item for item in contenders if item.population == highest)
        elif criterion == "quality":
            highest = max(_route_quality(item, config) for item in contenders)
            matches = tuple(item for item in contenders if _route_quality(item, config) == highest)
        else:
            return min(item.route_id for item in contenders)
        if len(matches) == 1:
            return matches[0].route_id
        contenders = matches
    return min(item.route_id for item in contenders)


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
            "population": (
                {"near_equivalent_tolerance_pct": 100.0, "tolerance_status": "trial"}
                if request.config.runtime_eligible
                else {}
            ),
            "ambiguity": (
                {"review_when": ["near-equivalent-options"]}
                if request.config.runtime_eligible
                else {"review_when": []}
            ),
        }
    )


def _bounded_hybrids(
    routes: tuple[ParallelRoute, ...], config: ParallelReductionConfig
) -> tuple[ParallelRoute, ...]:
    """Create only materially better, continuous switches at exact shared nodes."""
    hybrids: list[ParallelRoute] = []
    for index, left in enumerate(routes):
        for right in routes[index + 1 :]:
            shared = tuple(
                coordinate
                for coordinate in left.coordinates[1:-1]
                if coordinate in right.coordinates[1:-1]
            )
            if not shared:
                continue
            # The summed section observations are the caller's evidence that a
            # switch gains a material advantage; geometry must still meet exactly.
            hybrid_score = left.access_score + right.existing_infrastructure_score
            if (
                hybrid_score
                < max(
                    left.access_score + left.existing_infrastructure_score,
                    right.access_score + right.existing_infrastructure_score,
                )
                + config.material_score_difference
            ):
                continue
            point = shared[0]
            left_index, right_index = left.coordinates.index(point), right.coordinates.index(point)
            coordinates = left.coordinates[: left_index + 1] + right.coordinates[right_index + 1 :]
            hybrids.append(
                ParallelRoute(
                    route_id=f"hybrid:{left.route_id}:{right.route_id}:{left_index}:{right_index}",
                    endpoints=left.endpoints,
                    coordinates=coordinates,
                    network_scope=left.network_scope,
                    source_class="other-routable",
                    evidence_ids=tuple(sorted({*left.evidence_ids, *right.evidence_ids})),
                    provenance_ids=(left.route_id, right.route_id),
                    population=max(left.population, right.population),
                    gradient_pct=max(left.gradient_pct, right.gradient_pct),
                    access_score=hybrid_score,
                    existing_infrastructure_score=hybrid_score,
                    node_ids=tuple(sorted({*left.node_ids, *right.node_ids})),
                )
            )
    return tuple(
        sorted(hybrids, key=lambda item: item.route_id)[: config.maximum_hybrids_per_group]
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
    network_assessment_id = f"parallel-network-{candidate_set.candidate_set_id[-12:]}"
    topography_assessment_id = f"parallel-topography-{candidate_set.candidate_set_id[-12:]}"
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
        routes,
        areas,
        area,
        source=_population_source(),
        profile=PopulationReachProfile(
            comparison_tolerance_percent=(
                candidate_set.profile.population.near_equivalent_tolerance_pct
            )
        ),
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
        snapshot_id=f"parallel-reduction-evidence-{candidate_set.candidate_set_id[-12:]}",
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
                assessment_id=network_assessment_id,
                assessment_content_sha256=_digest(network_assessment_id),
                source_content_sha256=_digest(f"{network_assessment_id}-source"),
                method_version="parallel-reduction/v1",
            ),
            GovernedAssessmentBinding(
                kind=AssessmentKind.TOPOGRAPHY,
                assessment_id=topography_assessment_id,
                assessment_content_sha256=_digest(topography_assessment_id),
                source_content_sha256=_digest(f"{topography_assessment_id}-source"),
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
                assessment_id=network_assessment_id,
                evidence_record_id=f"directness-{item.candidate_id}",
            )
            for item in candidates
        ),
        gradient=tuple(
            CriterionFinding(
                candidate_id=item.candidate_id,
                state="satisfied",
                detail=CriterionDetail.GRADIENT_EVIDENCE,
                assessment_id=topography_assessment_id,
                evidence_record_id=f"gradient-{item.candidate_id}",
            )
            for item in candidates
        ),
        uncertainty=tuple(
            CriterionFinding(
                candidate_id=item.candidate_id,
                state="satisfied",
                detail=CriterionDetail.UNCERTAINTY_EVIDENCE,
                assessment_id=network_assessment_id,
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
    groups: dict[tuple[str, str], list[ParallelRoute]] = {}
    for route in request.routes:
        # Parallel reduction is a whole-scenario compiler seam, not a filter.
        # A route with no qualifying relation is a deterministic one-option
        # group and must still survive generation.
        groups.setdefault(route.endpoints, []).append(route)
    candidate_sets = []
    selections = []
    routes_by_group: dict[tuple[str, str], tuple[ParallelRoute, ...]] = {}
    for endpoints, routes in sorted(groups.items()):
        routes = [*routes, *_bounded_hybrids(tuple(routes), request.config)]
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
        by_input_identity = {
            _digest((route.route_id, route.evidence_ids)): route for route in routes
        }
        by_candidate = {
            candidate.candidate_id: by_input_identity[candidate.evidence_fingerprints[0]]
            for candidate in candidate_set.candidates
        }
        criteria = _criteria(candidate_set, by_candidate)
        candidate_sets.append(candidate_set)
        selections.append(select_preferred_alignment(profile, candidate_set, criteria))
    snapshot = GovernedEvidenceSnapshot(
        snapshot_id="parallel-reduction-scenario",
        assessments=tuple(
            {
                (
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
        decision_record=ScenarioDecisionRecord(
            mode=(
                "profile-fallback-awaiting-review"
                if any(not item.publishable for item in selections)
                else "no-agent-not-invoked"
            )
        ),
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
        lineage_fingerprints=(
            _digest(
                {
                    **request.model_dump(mode="json", exclude={"routes"}),
                    "routes": [
                        route.model_dump(mode="json")
                        for route in sorted(request.routes, key=lambda item: item.route_id)
                    ],
                }
            ),
        ),
    )
    route_by_candidate = {
        candidate.candidate_id: route.route_id
        for candidate_set, routes in zip(candidate_sets, routes_by_group.values(), strict=True)
        for candidate in candidate_set.candidates
        for route in routes
        if candidate.evidence_fingerprints[0] == _digest((route.route_id, route.evidence_ids))
    }
    decisions: list[ParallelDecisionArtifact] = []
    unavailable: list[OfficerTargetUnavailable] = []
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
            key=lambda item: (_route_quality(item, request.config), item.route_id),
        ).route_id
        selected, mode, trigger = compiler_preferred, "deterministic", None
        runtime_request: Mapping[str, object] | None = None
        decisive_consideration_ids: tuple[str, ...] = ()
        validation_status = "not-invoked"
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
        officer = officers.get(target_id)
        if officer is not None:
            if officer not in {item.route_id for item in routes}:
                unavailable.append(OfficerTargetUnavailable(target_id=target_id, route_id=officer))
            else:
                selected, mode = officer, "officer"
        elif by_population != by_quality:
            route_population = {item.route_id: item.population for item in routes}
            route_quality = {item.route_id: _route_quality(item, request.config) for item in routes}
            material = (
                abs(route_population[by_population] - route_population[by_quality])
                >= request.config.material_population_difference
                and abs(route_quality[by_population] - route_quality[by_quality])
                >= request.config.material_score_difference
            )
            if material and request.config.runtime_eligible and runtime is not None:
                runtime_request = _runtime_request(
                    target_id=target_id,
                    compiler_preferred_route_id=compiler_preferred,
                    routes=routes,
                )
                raw, call_status = _call_runtime_once(
                    runtime,
                    runtime_request,
                    request.config.runtime_deadline_seconds,
                )
                if call_status != "accepted":
                    mode, trigger, validation_status = "fallback", call_status, call_status
                    selected = _configured_fallback_route(
                        routes, compiler_preferred, request.config
                    )
                else:
                    chosen, decisive_consideration_ids = _validate_runtime_response(
                        raw,
                        runtime_request,
                        request.config,
                    )
                    if chosen is None:
                        mode, trigger, validation_status = (
                            "fallback",
                            "invalid-runtime-response",
                            "invalid-runtime-response",
                        )
                        selected = _configured_fallback_route(
                            routes, compiler_preferred, request.config
                        )
                    else:
                        selected, mode, validation_status = chosen, "agent", "accepted"
            elif material:
                mode, trigger, validation_status = (
                    "fallback",
                    "runtime-unavailable",
                    "runtime-unavailable",
                )
                selected = _configured_fallback_route(routes, compiler_preferred, request.config)
        selected_by_group[endpoints] = selected
        decisions.append(
            ParallelDecisionArtifact(
                target_id=target_id,
                compiler_preferred_route_id=compiler_preferred,
                selected_route_id=selected,
                mode=mode,
                fallback_trigger=trigger,
                runtime_request_id=(
                    runtime_request["request_id"] if runtime_request is not None else None
                ),
                offered_route_ids=(
                    tuple(item.route_id for item in routes) if runtime_request is not None else ()
                ),
                offered_evidence_ids=(
                    runtime_request["offered_evidence_ids"] if runtime_request is not None else ()
                ),
                offered_consideration_ids=(
                    runtime_request["offered_consideration_ids"]
                    if runtime_request is not None
                    else ()
                ),
                decisive_consideration_ids=decisive_consideration_ids,
                validation_status=validation_status,
            )
        )
    selected = tuple(sorted(selected_by_group.values()))
    retained = tuple(sorted(route.route_id for route in request.routes))
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
    route_lookup = {route.route_id: route for route in request.routes}
    gaps = tuple(
        NetworkGapArtifact(route_ids=tuple(sorted(pair)))
        for pair in request.required_transitions
        if pair[0] not in route_lookup
        or pair[1] not in route_lookup
        or not (set(route_lookup[pair[0]].node_ids) & set(route_lookup[pair[1]].node_ids))
    )
    divergences = tuple(
        item
        for item in decisions
        if item.mode == "officer" and item.selected_route_id != item.compiler_preferred_route_id
    )
    # The existing Scenario model makes a runtime/officer action authoritative
    # only through an exact compiler-authored accepted envelope.  Rebuild the
    # scenario with those envelopes rather than exposing a competing wrapper
    # winner list.
    envelopes = []
    for selection in selections:
        decision = next(
            item
            for item in decisions
            if item.target_id == "parallel:" + ":".join(selection.candidate_set.endpoints)
        )
        if selection.publishable:
            continue
        candidate_id = next(
            item.candidate_id
            for item in selection.candidate_set.admitted_candidates
            if route_by_candidate[item.candidate_id] == decision.selected_route_id
        )
        request_record = build_alignment_decision_request(
            selection,
            scenario_context_fingerprint=scenario.scenario_context_fingerprint,
        )
        option_id = f"select-{candidate_id}"
        if option_id not in {item.option_id for item in request_record.options}:
            option_id = "accept-profile-fallback"
        primary = AgentInvocation(
            invocation_id=f"parallel-primary-{request_record.request_fingerprint[:16]}",
            role=AgentAuthorityRole.PRIMARY_ALIGNMENT_DECISION,
            role_contract_fingerprint=(
                request_record.agent_review_contracts.primary_role_contract.contract_fingerprint
            ),
            prompt_contract_fingerprint=(
                request_record.agent_review_contracts.primary_prompt_contract.contract_fingerprint
            ),
            request_fingerprint=request_record.request_fingerprint,
            recorded_on=date(2026, 1, 1),
        )
        response = AlignmentDecisionResponse(
            request_id=request_record.request_id,
            request_fingerprint=request_record.request_fingerprint,
            option_id=option_id,
            evidence_ids=(request_record.immutable_evidence_ids[0],),
            invocation=primary,
        )
        critic = AgentInvocation(
            invocation_id=f"parallel-critic-{request_record.request_fingerprint[:16]}",
            role=AgentAuthorityRole.INDEPENDENT_ALIGNMENT_CRITIC,
            role_contract_fingerprint=(
                request_record.agent_review_contracts.critic_role_contract.contract_fingerprint
            ),
            prompt_contract_fingerprint=(
                request_record.agent_review_contracts.critic_prompt_contract.contract_fingerprint
            ),
            request_fingerprint=request_record.request_fingerprint,
            recorded_on=date(2026, 1, 1),
        )
        envelopes.append(
            AcceptedDecisionEnvelope(
                request=request_record,
                response=response,
                critique=AlignmentCritiqueRecord(
                    request_fingerprint=request_record.request_fingerprint,
                    response_fingerprint=response.response_fingerprint,
                    selection_fingerprint=selection.selection_fingerprint,
                    scenario_context_fingerprint=scenario.scenario_context_fingerprint,
                    evidence_snapshot_fingerprint=(request_record.evidence_snapshot_fingerprint),
                    profile_fingerprint=request_record.profile_fingerprint,
                    finding="accepted",
                    resolved=True,
                    evidence_ids=(request_record.immutable_evidence_ids[0],),
                    invocation=critic,
                ),
            )
        )
    if envelopes:
        scenario = ScenarioCompilation(
            area_fingerprint=scenario.area_fingerprint,
            evidence_snapshot=scenario.evidence_snapshot,
            profile_fingerprint=scenario.profile_fingerprint,
            decision_record=ScenarioDecisionRecord(
                mode="accepted-agent-decision-ledger",
                accepted_envelopes=tuple(envelopes),
            ),
            candidate_sets=scenario.candidate_sets,
            selections=scenario.selections,
            criteria_bindings=scenario.criteria_bindings,
            required_network_role_ids=scenario.required_network_role_ids,
            mandatory_network_place_ids=scenario.mandatory_network_place_ids,
            lineage_fingerprints=scenario.lineage_fingerprints,
        )
    evidence = build_parallel_evidence(
        request.routes,
        request.config,
        request.output_area_centroids,
        request.output_area_source_fingerprint,
    )
    artifact = ParallelReductionArtifact(
        relations=relations,
        selected_route_ids=selected,
        retained_route_ids=retained,
        decisions=tuple(decisions),
        crossing_warnings=tuple(warnings),
        network_gaps=gaps,
        officer_compiler_divergences=divergences,
        officer_target_unavailable=tuple(unavailable),
        section_population_profile=evidence.section_profile,
        section_population_profile_fingerprint=evidence.section_profile_fingerprint,
        material_population_differences=evidence.material_population_differences,
        cumulative_elevation_variation=evidence.cumulative_elevation_variation,
        missing_evidence=evidence.missing_evidence,
        section_population_sections=evidence.section_population_sections,
        guidance_profile_fingerprint=request.guidance_profile.profile_fingerprint,
        guidance_findings=tuple(
            {
                "route_id": route.route_id,
                "principle_id": item.principle_id,
                "state": item.state,
                "citation_ids": item.citation_ids,
                "material_departure_needs": (
                    "evidence-or-intervention" if item.state == "contradicted" else None
                ),
            }
            for route in request.routes
            for item in route.guidance_considerations
        ),
    )
    return ParallelReductionCompilation(scenario=scenario, artifact=artifact)


ParallelReductionRequest.model_rebuild()
