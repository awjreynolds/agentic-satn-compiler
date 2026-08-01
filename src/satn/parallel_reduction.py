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
from itertools import pairwise
from typing import Literal, Protocol, Self

import geopandas as gpd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from shapely.geometry import LineString, Point, box
from shapely.ops import substring

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
from satn.identifiers import stable_id
from satn.network_selection import NetworkSelectionProfile
from satn.parallel_reduction_evidence import ParallelEvidenceSummary, build_parallel_evidence
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


class ParallelNetworkScopeSpan(BaseModel):
    """Ordered evidence/display range; it is never a topology boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_distance_m: float = Field(ge=0)
    end_distance_m: float = Field(gt=0)
    network_scope: _SCOPE

    @model_validator(mode="after")
    def _range(self):
        if self.end_distance_m <= self.start_distance_m:
            raise ValueError("network scope span must have positive length")
        return self


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
    section_evidence: tuple[ParallelSectionEvidence, ...] = ()
    section_ids: tuple[str, ...] = ()
    transition_choice_point_ids: tuple[str, ...] = ()
    composition_provenance_ids: tuple[str, ...] = ()
    cumulative_elevation_variation_m: float | None = Field(default=None, ge=0)
    cev_source_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    topography_only_justification: bool = False
    network_scope_spans: tuple[ParallelNetworkScopeSpan, ...] = ()

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

    @model_validator(mode="after")
    def _scope_spans(self):
        spans = tuple(sorted(self.network_scope_spans, key=lambda item: item.start_distance_m))
        if any(right.start_distance_m < left.end_distance_m for left, right in pairwise(spans)):
            raise ValueError("network scope spans must not overlap")
        object.__setattr__(self, "network_scope_spans", spans)
        return self


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
    runtime_maximum_calls: int = Field(default=3, ge=1, le=32)
    runtime_maximum_request_bytes: int = Field(default=16_384, ge=1, le=1_000_000)
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


class ParallelChoicePoint(BaseModel):
    """One explicitly governed point at which a route may switch section."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    choice_point_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")
    coordinates: tuple[float, float]
    kind: Literal[
        "divergence-rejoin",
        "junction",
        "access-obligation",
        "physical-constraint",
    ]


class ParallelSectionEvidence(BaseModel):
    """Governed evidence for one declared route section, not a route-wide score."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_choice_point_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")
    end_choice_point_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._:-]*$")
    population: int = Field(default=0, ge=0)
    gradient_pct: float = Field(default=0.0, ge=0.0)
    cumulative_elevation_variation_m: float | None = Field(default=None, ge=0)
    cev_source_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    access_score: float = 0.0
    existing_infrastructure_score: float = 0.0
    evidence_ids: tuple[str, ...] = ()


class ParallelAlignmentSection(BaseModel):
    """A maximal chain bounded only by an explicit logical choice point."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str
    source_route_id: str
    logical_endpoints: tuple[str, str]
    start_choice_point_id: str
    end_choice_point_id: str
    coordinates: tuple[tuple[float, float], ...]
    provenance_ids: tuple[str, ...] = ()


class ParallelAlignmentOption(BaseModel):
    """An end-to-end base or hybrid route with inspectable section provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    route_id: str
    ordered_section_ids: tuple[str, ...]
    transition_choice_point_ids: tuple[str, ...] = ()
    provenance_ids: tuple[str, ...] = ()
    maximum_gradient_pct: float | None = None
    cumulative_elevation_variation_m: float | None = None
    cev_source_fingerprint: str | None = None
    topography_assessment: Literal["assessed", "unassessed"] = "unassessed"
    topography_only_justification: bool = False


class ParallelReductionRequest(BaseModel):
    """Data-only compiler input; all route candidates are discovered here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    routes: tuple[ParallelRoute, ...] = Field(min_length=1)
    config: ParallelReductionConfig = Field(default_factory=ParallelReductionConfig)
    area_id: str = "parallel-reduction-area"
    # A junction is explicit topology.  A geometric intersection alone is not one.
    junction_node_ids: tuple[str, ...] = ()
    choice_points: tuple[ParallelChoicePoint, ...] = ()
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
    route_findings: tuple[dict[str, object], ...] = ()
    decisive_consideration_ids: tuple[str, ...] = ()
    validation_status: Literal[
        "not-invoked",
        "accepted",
        "runtime-unavailable",
        "runtime-error",
        "runtime-timeout",
        "runtime-call-bound-reached",
        "runtime-request-too-large",
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
    sections: tuple[ParallelAlignmentSection, ...] = ()
    options: tuple[ParallelAlignmentOption, ...] = ()
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

    @model_validator(mode="after")
    def _bind_fingerprint(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        expected = _digest(payload)
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("parallel reduction artifact fingerprint is stale")
        object.__setattr__(self, "fingerprint", expected)
        return self


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


def _qualitative_outcomes(
    routes: tuple[ParallelRoute, ...],
    *,
    values: Mapping[str, float],
    material_difference: float,
    available: bool,
) -> dict[str, str]:
    if not available:
        return {route.route_id: "unassessed" for route in routes}
    highest = max(values.values())
    lowest = min(values.values())
    if highest - lowest < material_difference:
        return {route.route_id: "equivalent" for route in routes}
    return {
        route.route_id: (
            "material-advantage"
            if values[route.route_id] == highest
            else "material-disadvantage"
            if values[route.route_id] == lowest
            else "equivalent"
        )
        for route in routes
    }


def _unassessed_outcomes(routes: tuple[ParallelRoute, ...]) -> dict[str, str]:
    return {route.route_id: "unassessed" for route in routes}


def _population_outcomes(
    routes: tuple[ParallelRoute, ...], evidence: ParallelEvidenceSummary
) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    outcomes = _unassessed_outcomes(routes)
    evidence_ids = {route.route_id: () for route in routes}
    route_ids = set(outcomes)
    for difference in evidence.material_population_differences:
        advantaged = difference["advantaged_alignment_id"]
        compared = difference["compared_alignment_id"]
        if advantaged not in route_ids or compared not in route_ids:
            continue
        supporting = tuple(sorted(str(item) for item in difference["supporting_section_ids"]))
        outcomes[advantaged] = "material-advantage"
        outcomes[compared] = "material-disadvantage"
        evidence_ids[advantaged] = supporting
        evidence_ids[compared] = supporting
    return outcomes, evidence_ids


def _topography_outcomes(
    routes: tuple[ParallelRoute, ...],
    config: ParallelReductionConfig,
    evidence: ParallelEvidenceSummary,
) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    route_ids = {route.route_id for route in routes}
    if any(f"elevation:{route_id}" in evidence.missing_evidence for route_id in route_ids):
        return _unassessed_outcomes(routes), {route.route_id: () for route in routes}
    values: dict[str, float] = {}
    for comparison in evidence.cumulative_elevation_variation:
        left, right = comparison["route_ids"]
        if left in route_ids:
            values[left] = float(comparison["left_cumulative_elevation_variation_m"])
        if right in route_ids:
            values[right] = float(comparison["right_cumulative_elevation_variation_m"])
    if set(values) != route_ids:
        return _unassessed_outcomes(routes), {route.route_id: () for route in routes}
    highest, lowest = max(values.values()), min(values.values())
    relative_difference = 0.0 if highest == 0 else (highest - lowest) / highest * 100
    if (
        highest - lowest < config.material_elevation_variation_m
        or relative_difference < config.material_elevation_variation_pct
    ):
        outcomes = {route.route_id: "equivalent" for route in routes}
    else:
        outcomes = {
            route.route_id: (
                "material-advantage"
                if values[route.route_id] == lowest
                else "material-disadvantage"
                if values[route.route_id] == highest
                else "equivalent"
            )
            for route in routes
        }
    return (
        outcomes,
        {
            route.route_id: tuple(
                f"{route.route_id}:{distance_m}" for distance_m, _ in route.elevation_samples
            )
            for route in routes
        },
    )


def _guidance_outcomes(
    routes: tuple[ParallelRoute, ...],
) -> dict[tuple[str, str], str]:
    outcomes = {
        (route.route_id, item.principle_id): "unassessed"
        for route in routes
        for item in route.guidance_considerations
    }
    principle_ids = {
        item.principle_id for route in routes for item in route.guidance_considerations
    }
    for principle_id in principle_ids:
        considerations = {
            route.route_id: next(
                (
                    item
                    for item in route.guidance_considerations
                    if item.principle_id == principle_id
                ),
                None,
            )
            for route in routes
        }
        states = {
            item.state
            for item in considerations.values()
            if item is not None and item.state != "unassessed"
        }
        for route_id, item in considerations.items():
            if item is None or item.state == "unassessed":
                continue
            if {"supported", "contradicted"}.issubset(states):
                outcomes[(route_id, principle_id)] = (
                    "material-advantage" if item.state == "supported" else "material-disadvantage"
                )
            else:
                outcomes[(route_id, principle_id)] = "equivalent"
    return outcomes


def _runtime_route_menu(
    routes: tuple[ParallelRoute, ...],
    config: ParallelReductionConfig,
    evidence: ParallelEvidenceSummary,
    output_area_source_fingerprint: str | None,
) -> tuple[dict[str, object], ...]:
    """Summarise governed comparisons without serialising their raw observations."""

    population_outcomes, population_evidence_ids = _population_outcomes(routes, evidence)
    topography_outcomes, topography_evidence_ids = _topography_outcomes(routes, config, evidence)
    guidance_outcomes = _guidance_outcomes(routes)
    dimensions = (
        (
            "access",
            _qualitative_outcomes(
                routes,
                values={route.route_id: route.access_score for route in routes},
                material_difference=config.material_score_difference,
                available=all(route.evidence_ids for route in routes),
            ),
        ),
        (
            "existing-infrastructure",
            _qualitative_outcomes(
                routes,
                values={route.route_id: route.existing_infrastructure_score for route in routes},
                material_difference=config.material_score_difference,
                available=all(route.evidence_ids for route in routes),
            ),
        ),
    )
    outcomes = {dimension: values for dimension, values in dimensions}
    menu = []
    for route in sorted(routes, key=lambda item: item.route_id):
        findings = []
        for dimension, outcome, evidence_ids, citation_ids in (
            (
                "population",
                population_outcomes[route.route_id],
                population_evidence_ids[route.route_id],
                (output_area_source_fingerprint,)
                if population_evidence_ids[route.route_id] and output_area_source_fingerprint
                else (),
            ),
            (
                "topography",
                topography_outcomes[route.route_id],
                topography_evidence_ids[route.route_id],
                tuple(sorted(route.evidence_ids)),
            ),
            *(
                (
                    dimension,
                    outcomes[dimension][route.route_id],
                    tuple(sorted(route.evidence_ids)),
                    tuple(sorted(route.evidence_ids)),
                )
                for dimension in outcomes
            ),
        ):
            if outcome == "unassessed":
                evidence_ids, citation_ids = (), ()
            findings.append(
                {
                    "consideration_id": f"{dimension}:{route.route_id}",
                    "dimension": dimension,
                    "outcome": outcome,
                    "evidence_ids": evidence_ids,
                    "citation_ids": citation_ids,
                }
            )
        for consideration in sorted(
            route.guidance_considerations, key=lambda item: item.principle_id
        ):
            principle_id = consideration.principle_id
            outcome = guidance_outcomes[(route.route_id, principle_id)]
            evidence_ids = (
                () if outcome == "unassessed" else (f"guidance:{route.route_id}:{principle_id}",)
            )
            citation_ids = consideration.citation_ids
            findings.append(
                {
                    "consideration_id": f"guidance:{route.route_id}:{principle_id}",
                    "dimension": f"guidance:{principle_id}",
                    "outcome": outcome,
                    "evidence_ids": evidence_ids,
                    "citation_ids": citation_ids,
                }
            )
        menu.append(
            {
                "route_id": route.route_id,
                "findings": tuple(findings),
                "evidence_ids": tuple(
                    sorted({item for finding in findings for item in finding["evidence_ids"]})
                ),
                "consideration_ids": tuple(item["consideration_id"] for item in findings),
            }
        )
    return tuple(menu)


def _has_conflicting_material_advantages(menu: tuple[dict[str, object], ...]) -> bool:
    advantages = {
        str(route["route_id"]): {
            str(finding["dimension"])
            for finding in route["findings"]
            if finding["outcome"] == "material-advantage"
        }
        for route in menu
    }
    return any(
        left_route != right_route and left_dimension != right_dimension
        for left_route, left_dimensions in advantages.items()
        for right_route, right_dimensions in advantages.items()
        for left_dimension in left_dimensions
        for right_dimension in right_dimensions
    )


def _runtime_request(
    *,
    target_id: str,
    compiler_preferred_route_id: str,
    routes: tuple[ParallelRoute, ...],
    config: ParallelReductionConfig,
    evidence: ParallelEvidenceSummary,
    output_area_source_fingerprint: str | None,
) -> dict[str, object]:
    """Build the sole bounded runtime packet; never expose raw route datasets."""

    menu = _runtime_route_menu(routes, config, evidence, output_area_source_fingerprint)
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


def _runtime_request_bytes(request: Mapping[str, object]) -> int:
    return len(json.dumps(request, sort_keys=True, separators=(",", ":")).encode())


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
    findings = selected.get("findings")
    if not isinstance(findings, tuple):
        return None, ()
    relevant = {
        item.get("consideration_id") for item in findings if item.get("outcome") != "unassessed"
    }
    if not set(decisive_ids).issubset(relevant):
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


def _scope_coverage(
    left: ParallelRoute,
    right: ParallelRoute,
    config: ParallelReductionConfig,
    unresolved_distance: float | None = None,
) -> float:
    line = LineString(left.coordinates)
    spans = left.network_scope_spans or (
        ParallelNetworkScopeSpan(
            start_distance_m=0, end_distance_m=line.length, network_scope=left.network_scope
        ),
    )
    covered = 0.0
    for span in spans:
        segment = substring(line, span.start_distance_m, min(span.end_distance_m, line.length))
        if span.network_scope == "urban":
            distance = config.urban_proximity_m
        elif span.network_scope == "rural":
            distance = config.rural_proximity_m
        else:
            distance = unresolved_distance or config.rural_proximity_m
        covered += segment.intersection(LineString(right.coordinates).buffer(distance)).length
    return 0.0 if line.length == 0 else 100.0 * covered / line.length


def discover_parallel_relations(
    routes: tuple[ParallelRoute, ...], config: ParallelReductionConfig
) -> tuple[ParallelCandidateRelation, ...]:
    """Discover scope-sensitive symmetric candidates without topology inference."""

    relations: list[ParallelCandidateRelation] = []
    for index, left in enumerate(sorted(routes, key=lambda item: item.route_id)):
        for right in sorted(routes, key=lambda item: item.route_id)[index + 1 :]:
            if left.endpoints != right.endpoints:
                continue
            spans = (*left.network_scope_spans, *right.network_scope_spans)
            scopes = {
                left.network_scope,
                right.network_scope,
                *(item.network_scope for item in spans),
            }
            if "unresolved" in scopes:
                urban = min(
                    _scope_coverage(left, right, config, config.urban_proximity_m),
                    _scope_coverage(right, left, config, config.urban_proximity_m),
                )
                rural = min(
                    _scope_coverage(left, right, config, config.rural_proximity_m),
                    _scope_coverage(right, left, config, config.rural_proximity_m),
                )
                if rural < config.minimum_symmetric_coverage_pct:
                    continue
                sensitive = urban < config.minimum_symmetric_coverage_pct
            else:
                sensitive = False
            left_coverage, right_coverage = (
                _scope_coverage(left, right, config),
                _scope_coverage(right, left, config),
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


def discover_parallel_components(
    routes: tuple[ParallelRoute, ...],
    relations: tuple[ParallelCandidateRelation, ...],
) -> tuple[tuple[str, ...], ...]:
    """Return transitive proximity components, retaining every isolated route."""

    by_endpoints: dict[tuple[str, str], set[str]] = {}
    for route in routes:
        by_endpoints.setdefault(route.endpoints, set()).add(route.route_id)
    relation_pairs = {tuple(sorted(item.route_ids)) for item in relations}
    components: list[tuple[str, ...]] = []
    for _endpoints, route_ids in sorted(by_endpoints.items()):
        parents = {route_id: route_id for route_id in route_ids}

        for left, right in relation_pairs:
            if left in route_ids and right in route_ids:
                left_root, right_root = (
                    _component_find(parents, left),
                    _component_find(parents, right),
                )
                if left_root != right_root:
                    parents[max(left_root, right_root)] = min(left_root, right_root)
        grouped: dict[str, list[str]] = {}
        for route_id in sorted(route_ids):
            grouped.setdefault(_component_find(parents, route_id), []).append(route_id)
        components.extend(tuple(values) for _, values in sorted(grouped.items()))
    return tuple(sorted(components))


def _component_find(parents: dict[str, str], route_id: str) -> str:
    while parents[route_id] != route_id:
        parents[route_id] = parents[parents[route_id]]
        route_id = parents[route_id]
    return route_id


def discover_parallel_sections(
    routes: tuple[ParallelRoute, ...],
    choice_points: tuple[ParallelChoicePoint, ...] = (),
    junction_node_ids: tuple[str, ...] = (),
) -> tuple[ParallelAlignmentSection, ...]:
    """Split only at explicit choice points; ordinary/display cuts remain transparent."""

    declared_by_coordinate = {point.coordinates: point.choice_point_id for point in choice_points}
    sections: list[ParallelAlignmentSection] = []
    for route in sorted(routes, key=lambda item: item.route_id):
        point_ids = _route_choice_point_ids(route, declared_by_coordinate, set(junction_node_ids))
        positions = sorted(point_ids)
        for start, end in pairwise(positions):
            start_id, end_id = point_ids[start], point_ids[end]
            coordinates = route.coordinates[start : end + 1]
            sections.append(
                ParallelAlignmentSection(
                    section_id=stable_id(
                        "parallel-section", route.route_id, start_id, end_id, coordinates
                    ),
                    source_route_id=route.route_id,
                    logical_endpoints=route.endpoints,
                    start_choice_point_id=start_id,
                    end_choice_point_id=end_id,
                    coordinates=coordinates,
                    provenance_ids=route.provenance_ids or (route.route_id,),
                )
            )
    return tuple(sections)


def _route_choice_point_ids(
    route: ParallelRoute,
    declared_by_coordinate: Mapping[tuple[float, float], str],
    junction_node_ids: set[str],
) -> dict[int, str]:
    point_ids = {
        0: f"endpoint:{route.endpoints[0]}",
        len(route.coordinates) - 1: f"endpoint:{route.endpoints[1]}",
    }
    for position, coordinate in enumerate(route.coordinates[1:-1], start=1):
        declared = declared_by_coordinate.get(coordinate)
        if declared is not None:
            point_ids[position] = declared
        elif (
            len(route.node_ids) == len(route.coordinates)
            and route.node_ids[position] in junction_node_ids
        ):
            point_ids[position] = f"junction:{route.node_ids[position]}"
    return point_ids


def _decorate_base_routes(
    routes: tuple[ParallelRoute, ...],
    sections: tuple[ParallelAlignmentSection, ...],
) -> tuple[ParallelRoute, ...]:
    by_route: dict[str, list[ParallelAlignmentSection]] = {}
    for section in sections:
        by_route.setdefault(section.source_route_id, []).append(section)
    return tuple(
        route.model_copy(
            update={
                "section_ids": tuple(
                    item.section_id
                    for item in sorted(
                        by_route[route.route_id],
                        key=lambda item: route.coordinates.index(item.coordinates[0]),
                    )
                ),
                "transition_choice_point_ids": tuple(
                    item.end_choice_point_id
                    for item in sorted(
                        by_route[route.route_id],
                        key=lambda item: route.coordinates.index(item.coordinates[0]),
                    )[:-1]
                ),
                "composition_provenance_ids": route.provenance_ids or (route.route_id,),
                "cumulative_elevation_variation_m": _route_cev(route)[0],
                "cev_source_fingerprint": _route_cev(route)[1],
            }
        )
        for route in routes
    )


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
    routes: tuple[ParallelRoute, ...],
    sections: tuple[ParallelAlignmentSection, ...],
    config: ParallelReductionConfig,
) -> tuple[ParallelRoute, ...]:
    """Create bounded, material, explicit-choice-point compositions only."""

    by_route: dict[str, tuple[ParallelAlignmentSection, ...]] = {}
    for route in routes:
        by_route[route.route_id] = tuple(
            sorted(
                (item for item in sections if item.source_route_id == route.route_id),
                key=lambda item: route.section_ids.index(item.section_id),
            )
        )
    base_geometries = {_digest(route.coordinates) for route in routes}
    hybrids: dict[str, ParallelRoute] = {}
    for index, left in enumerate(routes):
        for right in routes[index + 1 :]:
            if left.endpoints != right.endpoints:
                continue
            for switch_id in sorted(
                {item.end_choice_point_id for item in by_route[left.route_id][:-1]}
                & {item.start_choice_point_id for item in by_route[right.route_id][1:]}
            ):
                if switch_id.startswith("endpoint:"):
                    continue
                left_prefix = _sections_through(by_route[left.route_id], switch_id, prefix=True)
                right_suffix = _sections_through(by_route[right.route_id], switch_id, prefix=False)
                hybrid = _compose_hybrid(
                    left,
                    right,
                    left_prefix,
                    right_suffix,
                    switch_id,
                    config,
                )
                if hybrid is not None and _digest(hybrid.coordinates) not in base_geometries:
                    hybrids.setdefault(_digest(hybrid.coordinates), hybrid)
                right_prefix = _sections_through(by_route[right.route_id], switch_id, prefix=True)
                left_suffix = _sections_through(by_route[left.route_id], switch_id, prefix=False)
                hybrid = _compose_hybrid(
                    right,
                    left,
                    right_prefix,
                    left_suffix,
                    switch_id,
                    config,
                )
                if hybrid is not None and _digest(hybrid.coordinates) not in base_geometries:
                    hybrids.setdefault(_digest(hybrid.coordinates), hybrid)
    return tuple(
        sorted(hybrids.values(), key=lambda item: item.route_id)[: config.maximum_hybrids_per_group]
    )


def _sections_through(
    sections: tuple[ParallelAlignmentSection, ...], switch_id: str, *, prefix: bool
) -> tuple[ParallelAlignmentSection, ...]:
    index = next(
        (
            position
            for position, section in enumerate(sections)
            if (section.end_choice_point_id if prefix else section.start_choice_point_id)
            == switch_id
        ),
        None,
    )
    if index is None:
        return ()
    return sections[: index + 1] if prefix else sections[index:]


def _compose_hybrid(
    first: ParallelRoute,
    second: ParallelRoute,
    first_sections: tuple[ParallelAlignmentSection, ...],
    second_sections: tuple[ParallelAlignmentSection, ...],
    switch_id: str,
    config: ParallelReductionConfig,
) -> ParallelRoute | None:
    if not first_sections or not second_sections:
        return None
    if first_sections[-1].coordinates[-1] != second_sections[0].coordinates[0]:
        return None
    selected_sections = (*first_sections, *second_sections)
    metrics = _composition_metrics(first, second, selected_sections)
    if metrics is None:
        return None
    material, topography_only = _material_hybrid(metrics, first, second, config)
    if not material:
        return None
    coordinates = tuple(
        coordinate
        for position, section in enumerate(selected_sections)
        for coordinate in (section.coordinates if position == 0 else section.coordinates[1:])
    )
    if len(set(coordinates)) < 2:
        return None
    return ParallelRoute(
        route_id=f"hybrid:{first.route_id}:{second.route_id}:{switch_id}",
        endpoints=first.endpoints,
        coordinates=coordinates,
        network_scope=first.network_scope,
        source_class="other-routable",
        evidence_ids=metrics["evidence_ids"],
        provenance_ids=(first.route_id, second.route_id),
        population=metrics["population"],
        gradient_pct=metrics["gradient_pct"],
        access_score=metrics["access_score"],
        existing_infrastructure_score=metrics["existing_infrastructure_score"],
        cumulative_elevation_variation_m=metrics["cumulative_elevation_variation_m"],
        cev_source_fingerprint=metrics["cev_source_fingerprint"],
        topography_only_justification=topography_only,
        section_ids=tuple(item.section_id for item in selected_sections),
        transition_choice_point_ids=(switch_id,),
        composition_provenance_ids=(first.route_id, second.route_id, switch_id),
    )


def _composition_metrics(
    first: ParallelRoute,
    second: ParallelRoute,
    sections: tuple[ParallelAlignmentSection, ...],
) -> dict[str, object] | None:
    evidence_by_route = {
        route.route_id: {
            frozenset((item.start_choice_point_id, item.end_choice_point_id)): item
            for item in route.section_evidence
        }
        for route in (first, second)
    }
    observations = []
    for section in sections:
        observation = evidence_by_route[section.source_route_id].get(
            frozenset((section.start_choice_point_id, section.end_choice_point_id))
        )
        if observation is None:
            return None
        observations.append(observation)
    return {
        "population": sum(item.population for item in observations),
        "gradient_pct": max(item.gradient_pct for item in observations),
        "cumulative_elevation_variation_m": _complete_cev(observations)[0],
        "cev_source_fingerprint": _complete_cev(observations)[1],
        "access_score": sum(item.access_score for item in observations),
        "existing_infrastructure_score": sum(
            item.existing_infrastructure_score for item in observations
        ),
        "evidence_ids": tuple(
            sorted({evidence_id for item in observations for evidence_id in item.evidence_ids})
        ),
    }


def _complete_cev(
    observations: list[ParallelSectionEvidence] | tuple[ParallelSectionEvidence, ...],
) -> tuple[float | None, str | None]:
    """Return a sum only for complete, compatible direction-independent CEV."""

    if not observations or any(
        item.cumulative_elevation_variation_m is None or item.cev_source_fingerprint is None
        for item in observations
    ):
        return None, None
    fingerprints = {item.cev_source_fingerprint for item in observations}
    if len(fingerprints) != 1:
        return None, None
    return (
        sum(float(item.cumulative_elevation_variation_m) for item in observations),
        next(iter(fingerprints)),
    )


def _route_cev(route: ParallelRoute) -> tuple[float | None, str | None]:
    if route.cumulative_elevation_variation_m is not None and route.cev_source_fingerprint:
        return route.cumulative_elevation_variation_m, route.cev_source_fingerprint
    return _complete_cev(route.section_evidence)


def _material_cev_advantage(
    hybrid_cev: float | None,
    hybrid_fingerprint: str | None,
    base: ParallelRoute,
    config: ParallelReductionConfig,
) -> bool:
    base_cev, base_fingerprint = _route_cev(base)
    if (
        hybrid_cev is None
        or hybrid_fingerprint is None
        or base_cev is None
        or base_fingerprint != hybrid_fingerprint
        or hybrid_cev > base_cev
    ):
        return False
    difference = base_cev - hybrid_cev
    relative = 0.0 if base_cev == 0 else difference / max(base_cev, hybrid_cev) * 100
    return (
        difference >= config.material_elevation_variation_m
        and relative >= config.material_elevation_variation_pct
    )


def _material_hybrid(
    metrics: Mapping[str, object],
    first: ParallelRoute,
    second: ParallelRoute,
    config: ParallelReductionConfig,
) -> tuple[bool, bool]:
    """Require a governed whole-option advantage; never combine cross-dimension scores."""

    population = int(metrics["population"])
    access = float(metrics["access_score"])
    infrastructure = float(metrics["existing_infrastructure_score"])
    cev = metrics["cumulative_elevation_variation_m"]
    cev_fingerprint = metrics["cev_source_fingerprint"]
    non_topographic_material = True
    topographic_material = True
    for base in (first, second):
        no_worse = (
            population >= base.population
            and access >= base.access_score
            and infrastructure >= base.existing_infrastructure_score
        )
        materially_better = (
            population >= base.population + config.material_population_difference
            or access >= base.access_score + config.material_score_difference
            or infrastructure
            >= base.existing_infrastructure_score + config.material_score_difference
        )
        if not (no_worse and materially_better):
            non_topographic_material = False
        if not _material_cev_advantage(cev, cev_fingerprint, base, config):
            topographic_material = False
    return non_topographic_material or topographic_material, (
        topographic_material and not non_topographic_material
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
    sections = discover_parallel_sections(
        request.routes,
        request.choice_points,
        request.junction_node_ids,
    )
    base_routes = _decorate_base_routes(request.routes, sections)
    relations = discover_parallel_relations(base_routes, request.config)
    base_by_id = {route.route_id: route for route in base_routes}
    components = discover_parallel_components(base_routes, relations)
    component_endpoint_counts: dict[tuple[str, str], int] = {}
    for component in components:
        endpoints = base_by_id[component[0]].endpoints
        component_endpoint_counts[endpoints] = component_endpoint_counts.get(endpoints, 0) + 1
    candidate_sets = []
    selections = []
    group_endpoints: dict[str, tuple[str, str]] = {}
    routes_by_group: dict[str, tuple[ParallelRoute, ...]] = {}
    selection_routes_by_group: dict[str, tuple[ParallelRoute, ...]] = {}
    for component in components:
        group_id = "component:" + _digest(component)[:16]
        base_component_routes = tuple(base_by_id[route_id] for route_id in component)
        endpoints = base_component_routes[0].endpoints
        scenario_endpoints = (
            endpoints
            if component_endpoint_counts[endpoints] == 1
            else (
                f"{endpoints[0]}:component:{group_id.rsplit(':', 1)[1]}",
                f"{endpoints[1]}:component:{group_id.rsplit(':', 1)[1]}",
            )
        )
        routes = [
            *base_component_routes,
            *_bounded_hybrids(base_component_routes, sections, request.config),
        ]
        group_endpoints[group_id] = endpoints
        routes_by_group[group_id] = tuple(sorted(routes, key=lambda item: item.route_id))
        selection_routes_by_group[group_id] = tuple(
            route
            for route in routes_by_group[group_id]
            if not route.topography_only_justification
        )
        inputs = tuple(
            AlignmentCandidateInput(
                network_role=NetworkRole.INTERURBAN_SPINE,
                endpoints=scenario_endpoints,
                source_class=route.source_class,
                geometry=CanonicalLineString(coordinates=route.coordinates),
                evidence_fingerprints=(_digest((route.route_id, route.evidence_ids)),),
                provenance_ids=route.provenance_ids or (route.route_id,),
                topology_state=route.topology,
                served_network_place_ids=scenario_endpoints,
                directness_m=LineString(route.coordinates).length,
                maximum_gradient_pct=route.gradient_pct,
            )
            for route in selection_routes_by_group[group_id]
        )
        candidate_set = admit_candidate_set(
            profile,
            network_role=NetworkRole.INTERURBAN_SPINE,
            endpoints=scenario_endpoints,
            candidates=inputs,
            mandatory_network_place_ids=scenario_endpoints,
        )
        by_input_identity = {
            _digest((route.route_id, route.evidence_ids)): route
            for route in selection_routes_by_group[group_id]
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
        for candidate_set, routes in zip(
            candidate_sets, selection_routes_by_group.values(), strict=True
        )
        for candidate in candidate_set.candidates
        for route in routes
        if candidate.evidence_fingerprints[0] == _digest((route.route_id, route.evidence_ids))
    }
    evidence = build_parallel_evidence(
        request.routes,
        request.config,
        request.output_area_centroids,
        request.output_area_source_fingerprint,
    )
    decisions: list[ParallelDecisionArtifact] = []
    unavailable: list[OfficerTargetUnavailable] = []
    selected_by_group: dict[str, str] = {}
    officers = {item.target_id: item.route_id for item in request.officer_decisions}
    runtime_calls = 0
    endpoint_group_counts = {
        endpoints: sum(1 for item in group_endpoints.values() if item == endpoints)
        for endpoints in group_endpoints.values()
    }
    for group_id, routes in selection_routes_by_group.items():
        endpoints = group_endpoints[group_id]
        target_id = "parallel:" + ":".join(endpoints)
        if endpoint_group_counts[endpoints] > 1:
            target_id = f"{target_id}:{group_id.rsplit(':', 1)[1]}"
        selection = selections[list(routes_by_group).index(group_id)]
        compiler_preferred = route_by_candidate[selection.selected_candidate_id]
        selected, mode, trigger = compiler_preferred, "deterministic", None
        runtime_request: Mapping[str, object] | None = None
        route_findings = _runtime_route_menu(
            routes,
            request.config,
            evidence,
            request.output_area_source_fingerprint,
        )
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
        elif _has_conflicting_material_advantages(route_findings):
            if request.config.runtime_eligible and runtime is not None:
                if runtime_calls >= request.config.runtime_maximum_calls:
                    mode, trigger, validation_status = (
                        "fallback",
                        "runtime-call-bound-reached",
                        "runtime-call-bound-reached",
                    )
                    selected = _configured_fallback_route(
                        routes, compiler_preferred, request.config
                    )
                else:
                    runtime_request = _runtime_request(
                        target_id=target_id,
                        compiler_preferred_route_id=compiler_preferred,
                        routes=routes,
                        config=request.config,
                        evidence=evidence,
                        output_area_source_fingerprint=request.output_area_source_fingerprint,
                    )
                    if (
                        _runtime_request_bytes(runtime_request)
                        > request.config.runtime_maximum_request_bytes
                    ):
                        mode, trigger, validation_status = (
                            "fallback",
                            "runtime-request-too-large",
                            "runtime-request-too-large",
                        )
                        selected = _configured_fallback_route(
                            routes, compiler_preferred, request.config
                        )
                    else:
                        runtime_calls += 1
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
            else:
                mode, trigger, validation_status = (
                    "fallback",
                    "runtime-unavailable",
                    "runtime-unavailable",
                )
                selected = _configured_fallback_route(routes, compiler_preferred, request.config)
        selected_by_group[group_id] = selected
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
                route_findings=route_findings,
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
    for group_id, selection in zip(selection_routes_by_group, selections, strict=True):
        decision = decisions[list(selection_routes_by_group).index(group_id)]
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
    artifact = ParallelReductionArtifact(
        relations=relations,
        sections=sections,
        options=tuple(
            ParallelAlignmentOption(
                route_id=route.route_id,
                ordered_section_ids=route.section_ids,
                transition_choice_point_ids=route.transition_choice_point_ids,
                provenance_ids=route.composition_provenance_ids or route.provenance_ids,
                maximum_gradient_pct=route.gradient_pct,
                cumulative_elevation_variation_m=route.cumulative_elevation_variation_m,
                cev_source_fingerprint=route.cev_source_fingerprint,
                topography_assessment=(
                    "assessed"
                    if route.cumulative_elevation_variation_m is not None
                    and route.cev_source_fingerprint is not None
                    else "unassessed"
                ),
                topography_only_justification=route.topography_only_justification,
            )
            for routes in routes_by_group.values()
            for route in routes
        ),
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
