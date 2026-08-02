"""Deterministic, evidence-bound Preferred Strategic Alignment selection.

The module's interface is deliberately small: admit a finite Candidate Set,
apply a frozen Network Selection Profile, bind the results into a Scenario
Compilation, and (only with governed human authority) adopt a Reference SATN.
Routing, evidence loading, agent execution and publication remain adapters at
other seams.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal, Protocol, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, unary_union

from .education_access import (
    EducationAccessAssessment,
    IndependentTravelStatus,
    governed_education_assessment_fingerprint,
)
from .existing_alignment import (
    CandidateEligibilityProof,
    ExistingAlignmentAdvantage,
    ExistingAlignmentLexicographicComparison,
    NearEquivalenceProof,
)
from .existing_alignment import (
    _geometry_fingerprint as _existing_geometry_fingerprint,
)
from .models import AccessServiceStatus, AgentConfig
from .network_selection import (
    AlignmentSelectionObjective,
    AmbiguityTrigger,
    CandidateSourceClass,
    DisplacementReasonCode,
    InterventionState,
    NetworkSelectionProfile,
    ReuseFirstCandidateClass,
)
from .population_reach import (
    PROHIBITED_CLAIMS,
    PopulationReachAssessment,
    _assert_unique_record_keys,
    _canonical_oa_id,
    _canonicalize_geometry,
    _coordinate_transformation_lineage,
    _current_development_warnings,
    _derive_sensitivities,
    _missing_current_development_evidence,
    _summarise_records,
)
from .population_reach import (
    _geometry_sha256 as _population_geometry_sha256,
)
from .traffic_evidence import (
    ProtectedSpaceEvidence,
    ProtectedSpaceState,
    TrafficChallengeDiagnostic,
    TrafficCoverageStatus,
    TrafficExposure,
    TrafficFreshnessState,
    TrafficMatchState,
    TrafficObservation,
)

_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_ID = re.compile(r"^candidate-[0-9a-f]{20}$")
_CANDIDATE_SET_ID = re.compile(r"^candidate-set-[0-9a-f]{20}$")
_ALIGNMENT_BASIS_VOCABULARY = frozenset(
    {
        "current-ncn",
        "ncn-link",
        "greenway",
        "mapped-cycleway",
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
    }
)
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\."
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$",
    re.IGNORECASE,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: object) -> str:
    return f"{prefix}-{_fingerprint(value)[:20]}"


"""The local compiler deliberately has no signing keys or remote trust roots.

Fingerprints detect stale or structurally altered records. They are not claims
of identity: the local operator controls the workspace, inputs and harness.
"""


def _finite(value: float, field: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return 0.0 if value == 0 else value


def _canonical_ids(
    value: tuple[str, ...],
    field: str,
    *,
    pattern: re.Pattern[str] = _ID,
) -> tuple[str, ...]:
    if any(pattern.fullmatch(item) is None for item in value):
        raise ValueError(f"{field} must contain canonical identifiers")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} cannot contain duplicates")
    return tuple(sorted(value))


def _canonical_population_oa_ids(
    value: tuple[str, ...],
    field: str,
) -> tuple[str, ...]:
    canonical = tuple(_canonical_oa_id(item) for item in value)
    if any(not item for item in canonical):
        raise ValueError(f"{field} must contain non-blank governed OA identifiers")
    if canonical != value:
        raise ValueError(f"{field} must contain canonical governed OA identifiers")
    if len(set(canonical)) != len(canonical):
        raise ValueError(f"{field} cannot contain duplicates")
    return tuple(sorted(canonical))


def _revalidate(model: BaseModel) -> BaseModel:
    """Revalidate even model instances crossing a trust boundary."""
    return type(model).model_validate(model.model_dump(mode="python"))


class _HasCandidateId(Protocol):
    candidate_id: str


class CandidateValidity(StrEnum):
    VALID = "valid"
    INVALID_TOPOLOGY = "invalid-topology"
    EDUCATION_INCOMPLETE = "education-incomplete"
    UNKNOWN_HARD_GATE = "unknown-hard-gate"


class NetworkRole(StrEnum):
    INTERURBAN_SPINE = "interurban-spine"
    CROSS_SPINE_CONNECTOR = "cross-spine-connector"
    COMMUNITY_ACCESS = "community-access"
    SCHOOL_ACCESS = "school-access"
    STRATEGIC_DESTINATION_ACCESS = "strategic-destination-access"
    UNRESOLVED_STRATEGIC_ALIGNMENT = "unresolved-strategic-alignment"


class CandidateSetDisposition(StrEnum):
    SUBSTITUTE_ALTERNATIVES = "substitute-alternatives"
    COMPLEMENTARY_REQUIRED = "complementary-required"
    UNCERTAIN = "uncertain"


def _role_disposition(role: NetworkRole) -> CandidateSetDisposition:
    if role in {
        NetworkRole.INTERURBAN_SPINE,
        NetworkRole.CROSS_SPINE_CONNECTOR,
    }:
        return CandidateSetDisposition.SUBSTITUTE_ALTERNATIVES
    if role in {
        NetworkRole.COMMUNITY_ACCESS,
        NetworkRole.SCHOOL_ACCESS,
        NetworkRole.STRATEGIC_DESTINATION_ACCESS,
    }:
        return CandidateSetDisposition.COMPLEMENTARY_REQUIRED
    return CandidateSetDisposition.UNCERTAIN


class SelectionDisposition(StrEnum):
    SELECTED = "selected"
    PROVISIONAL_REVIEW = "provisional-review"
    NETWORK_GAP = "network-gap"


class SelectionAction(StrEnum):
    NO_AGENT_CLEAR = "no-agent-not-invoked-clear"
    PROFILE_FALLBACK = "profile-fallback-review-required"
    NETWORK_GAP_REVIEW = "network-gap-review-required"


class CriterionState(StrEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNKNOWN = "unknown"


class CriterionDetail(StrEnum):
    EDUCATION_COMPLETENESS = "education-completeness"
    DIRECTNESS_EVIDENCE = "directness-evidence"
    GRADIENT_EVIDENCE = "gradient-evidence"
    UNCERTAINTY_EVIDENCE = "uncertainty-evidence"


class AssessmentKind(StrEnum):
    POPULATION_REACH = "population-reach"
    EDUCATION_ACCESS = "education-access"
    EXISTING_ALIGNMENT = "existing-alignment"
    NETWORK_GEOMETRY = "network-geometry"
    TOPOGRAPHY = "topography"


class GovernedAssessmentBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AssessmentKind
    assessment_id: str = Field(min_length=1)
    assessment_content_sha256: str = Field(pattern=_SHA256.pattern)
    source_content_sha256: str = Field(pattern=_SHA256.pattern)
    method_version: str = Field(min_length=1)


class GovernedEvidenceSnapshot(BaseModel):
    """Exact common evidence snapshot shared by every criterion section."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str = Field(min_length=1)
    assessments: tuple[GovernedAssessmentBinding, ...] = Field(min_length=2)
    snapshot_fingerprint: str = ""

    @field_validator("assessments")
    @classmethod
    def canonical_assessments(
        cls,
        value: tuple[GovernedAssessmentBinding, ...],
    ) -> tuple[GovernedAssessmentBinding, ...]:
        ordered = tuple(sorted(value, key=lambda item: (item.kind, item.assessment_id)))
        keys = tuple((item.kind, item.assessment_id) for item in ordered)
        if len(set(keys)) != len(keys):
            raise ValueError("governed assessment bindings must be unique")
        required = {
            AssessmentKind.POPULATION_REACH,
            AssessmentKind.EDUCATION_ACCESS,
            AssessmentKind.NETWORK_GEOMETRY,
            AssessmentKind.TOPOGRAPHY,
        }
        if not required.issubset({item.kind for item in ordered}):
            raise ValueError("snapshot requires population and education assessments")
        return ordered

    @model_validator(mode="after")
    def bind_snapshot(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"snapshot_fingerprint"})
        expected = _fingerprint(payload)
        if self.snapshot_fingerprint and self.snapshot_fingerprint != expected:
            raise ValueError("governed evidence snapshot fingerprint is stale")
        object.__setattr__(self, "snapshot_fingerprint", expected)
        return self

    def assessment(self, kind: AssessmentKind) -> GovernedAssessmentBinding | None:
        return next((item for item in self.assessments if item.kind == kind), None)


class AdmissionDisposition(StrEnum):
    ADMITTED = "admitted"
    REJECTED = "rejected"


class AdmissionRationale(StrEnum):
    ADMITTED = "admitted-for-comparison"
    DUPLICATE_GEOMETRY = "materially-equivalent-geometry-lower-precedence"
    PROFILE_LIMIT = "profile-candidate-limit"
    MISSING_ROLE_COVERAGE = "missing-candidate-set-role-obligation"


class CandidateGenerationGapReason(StrEnum):
    NONE = "not-a-generation-gap"
    NO_GENERATED_CANDIDATES = "no-generated-candidates"
    ALL_GENERATED_CANDIDATES_REJECTED = "all-generated-candidates-rejected"


class ComparisonRationale(StrEnum):
    NOT_PREFERRED = "not-preferred-after-criteria-hierarchy"
    INVALID_TOPOLOGY = "invalid-topology-hard-gate"
    EDUCATION_INCOMPLETE = "education-completeness-hard-gate"
    UNKNOWN_HARD_GATE = "hard-gate-evidence-required"
    NETWORK_GAP = "no-valid-preferred-alignment"


class ChangeCondition(StrEnum):
    EVIDENCE_CHANGES = "governed-evidence-changes"
    PROFILE_CHANGES = "network-selection-profile-changes"
    TOPOLOGY_REPAIRED = "topology-is-repaired"
    EDUCATION_COMPLETENESS_CHANGES = "education-completeness-evidence-changes"
    AMBIGUITY_RESOLVED = "bounded-ambiguity-is-resolved"
    ROLE_COVERAGE_CHANGES = "candidate-set-role-coverage-changes"
    LEDGER_CHANGES = "accepted-decision-ledger-changes"
    POPULATION_RANKING_CHANGES = "population-ranking-changes"
    BORDERLINE_OA_RESOLUTION_CHANGES = "borderline-oa-resolution-changes"
    CURRENT_DEVELOPMENT_EVIDENCE_CHANGES = "current-development-evidence-changes"
    WINNER_TOPOLOGY_INVALIDATED = "winner-topology-is-invalidated"
    WINNER_EDUCATION_INVALIDATED = "winner-education-completeness-is-invalidated"
    DECISION_CRITIQUE_RESOLVED = "decision-critique-is-resolved"


class MaterialGeometryEquivalenceProfile(BaseModel):
    """Governed route-equivalence policy used only for Candidate Set admission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method_version: Literal["connected-linework-hausdorff-and-length/v1"] = (
        "connected-linework-hausdorff-and-length/v1"
    )
    crs: Literal["EPSG:27700"] = "EPSG:27700"
    tolerance_m: float = Field(default=0.05, gt=0, le=5, strict=True)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.model_dump(mode="json"))


class CanonicalLineString(BaseModel):
    """Connected linework, accepting a line or order-invariant multipart form."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    coordinates: tuple[tuple[float, float], ...] | None = None
    parts: tuple[tuple[tuple[float, float], ...], ...] = ()
    equivalence_profile: MaterialGeometryEquivalenceProfile = Field(
        default_factory=MaterialGeometryEquivalenceProfile
    )

    @field_validator("coordinates", mode="before")
    @classmethod
    def validate_coordinates(
        cls,
        value: object,
    ) -> tuple[tuple[float, float], ...] | None:
        if value is None:
            return None
        return cls._canonical_part(value)

    @field_validator("parts", mode="before")
    @classmethod
    def validate_parts(
        cls,
        value: object,
    ) -> tuple[tuple[tuple[float, float], ...], ...]:
        if value in (None, (), []):
            return ()
        if not isinstance(value, (tuple, list)):
            raise ValueError("geometry parts must be an ordered sequence")
        return tuple(cls._canonical_part(item) for item in value)

    @staticmethod
    def _canonical_part(value: object) -> tuple[tuple[float, float], ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("geometry coordinates must be an ordered sequence")
        canonical: list[tuple[float, float]] = []
        for coordinate in value:
            if (
                not isinstance(coordinate, (tuple, list))
                or len(coordinate) != 2
                or any(type(item) is not float for item in coordinate)
            ):
                raise ValueError("geometry coordinates must be strict float pairs")
            x, y = (_finite(item, "geometry coordinate") for item in coordinate)
            canonical.append((x, y))
        if len(set(canonical)) < 2:
            raise ValueError("geometry must contain two distinct coordinates")
        return tuple(canonical)

    @model_validator(mode="after")
    def validate_representation(self) -> Self:
        if (self.coordinates is None) == (not self.parts):
            raise ValueError("geometry requires exactly one line or multipart representation")
        merged = self.as_shapely()
        if not isinstance(merged, LineString) or merged.is_empty or not merged.is_simple:
            raise ValueError("multipart geometry must form one connected simple LineString")
        exact = merged.simplify(0.0, preserve_topology=True)
        forward = tuple((float(x), float(y)) for x, y in exact.coords)
        canonical = min(forward, tuple(reversed(forward)))
        object.__setattr__(self, "coordinates", canonical)
        object.__setattr__(self, "parts", ())
        return self

    def as_shapely(self) -> LineString:
        source_parts = (self.coordinates,) if self.coordinates is not None else self.parts
        lines = tuple(LineString(item) for item in source_parts)
        merged: BaseGeometry = lines[0] if len(lines) == 1 else linemerge(unary_union(lines))
        if not isinstance(merged, LineString):
            raise ValueError("geometry parts do not form one connected LineString")
        return merged

    def materially_equivalent(self, other: CanonicalLineString) -> bool:
        if self.equivalence_profile != other.equivalence_profile:
            return False
        left = self.as_shapely()
        right = other.as_shapely()
        tolerance = self.equivalence_profile.tolerance_m
        return (
            left.hausdorff_distance(right) <= tolerance
            and abs(left.length - right.length) <= tolerance
        )

    @property
    def equivalence_fingerprint(self) -> str:
        simplified = self.as_shapely().simplify(
            self.equivalence_profile.tolerance_m,
            preserve_topology=True,
        )
        forward = tuple((float(x), float(y)) for x, y in simplified.coords)
        coordinates = min(forward, tuple(reversed(forward)))
        return _fingerprint(
            {
                "equivalence_profile": self.equivalence_profile.model_dump(mode="json"),
                "coordinates": coordinates,
            }
        )

    @property
    def fingerprint(self) -> str:
        exact = self.as_shapely().simplify(
            0.0,
            preserve_topology=True,
        )
        forward = tuple((float(x), float(y)) for x, y in exact.coords)
        coordinates = min(forward, tuple(reversed(forward)))
        return _fingerprint(
            {
                "equivalence_profile": self.equivalence_profile.model_dump(mode="json"),
                "coordinates": coordinates,
            }
        )

    @property
    def population_geometry_sha256(self) -> str:
        return _population_geometry_sha256(_canonicalize_geometry(self.as_shapely()))

    @property
    def existing_alignment_geometry_fingerprint(self) -> str:
        return _existing_geometry_fingerprint(
            self.as_shapely(),
            self.equivalence_profile.crs,
        )


class TrafficConflictEvidence(BaseModel):
    """Typed retention of contradictory traffic observations.

    Traffic conflict is optional enrichment.  It must never become a route
    veto, but the observations and their provenance remain inspectable even
    when the ordinary traffic status conservatively collapses to unknown.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_ids: tuple[str, ...] = Field(min_length=1)
    source_export_fingerprints: tuple[str, ...] = ()
    row_fingerprints: tuple[str, ...] = ()
    conflicting_fields: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    provenance_ids: tuple[str, ...] = ()

    @field_validator("observation_ids")
    @classmethod
    def validate_ids(cls, value: tuple[str, ...], _info: object) -> tuple[str, ...]:
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("observation_ids must contain non-blank identifiers")
        if len(set(value)) != len(value):
            raise ValueError("observation_ids cannot contain duplicates")
        return tuple(sorted(value))

    @field_validator("evidence_ids", "provenance_ids")
    @classmethod
    def validate_governed_ids(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        return _canonical_ids(value, getattr(info, "field_name", "identifiers"))

    @field_validator("source_export_fingerprints", "row_fingerprints")
    @classmethod
    def validate_fingerprints(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        field = getattr(info, "field_name", "fingerprints")
        if any(_SHA256.fullmatch(item) is None for item in value):
            raise ValueError(f"{field} must contain SHA-256 fingerprints")
        return tuple(sorted(value))

    @field_validator("conflicting_fields")
    @classmethod
    def validate_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or not item.replace("_", "").isalnum() for item in value):
            raise ValueError("traffic conflict fields must be canonical names")
        return tuple(sorted(set(value)))


class AlignmentCandidateInput(BaseModel):
    """One compiler-generated option, including its own role obligations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    network_role: NetworkRole
    endpoints: tuple[str, str]
    source_class: CandidateSourceClass
    geometry: CanonicalLineString
    evidence_fingerprints: tuple[str, ...] = Field(min_length=1)
    provenance_ids: tuple[str, ...] = Field(min_length=1)
    topology_state: CriterionState
    served_network_place_ids: tuple[str, ...] = ()
    served_access_obligation_ids: tuple[str, ...] = ()
    served_strategic_destination_ids: tuple[str, ...] = ()
    directness_m: float = Field(ge=0, strict=True, allow_inf_nan=False)
    # vNext facts are optional at the model boundary so existing v1 producers
    # remain valid. vNext Candidate Sets validate the required facts before use.
    reuse_class: ReuseFirstCandidateClass | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    intervention_state: InterventionState | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    alignment_bases: tuple[str, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    primary_alignment_basis: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    total_absolute_elevation_change_m: float | None = Field(
        default=None,
        ge=0,
        strict=True,
        allow_inf_nan=False,
        exclude_if=lambda value: value is None,
    )
    transition_count: int | None = Field(
        default=None, ge=0, strict=True, exclude_if=lambda value: value is None
    )
    fragmentation_count: int | None = Field(
        default=None, ge=0, strict=True, exclude_if=lambda value: value is None
    )
    governed_evidence_ids: tuple[str, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    maximum_gradient_pct: float | None = Field(
        default=None,
        ge=0,
        strict=True,
        allow_inf_nan=False,
    )
    traffic_observation: TrafficObservation | None = Field(
        default=None, exclude=True
    )
    traffic_observations: tuple[TrafficObservation, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    traffic_conflict_evidence: TrafficConflictEvidence | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    protected_space_evidence: ProtectedSpaceEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    traffic_exposure: TrafficExposure | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    candidate_id: str = ""

    @field_validator("endpoints")
    @classmethod
    def validate_endpoints(cls, value: tuple[str, str]) -> tuple[str, str]:
        if (
            len(value) != 2
            or value[0] == value[1]
            or any(_ID.fullmatch(item) is None for item in value)
        ):
            raise ValueError("endpoints must be distinct canonical Network Place identifiers")
        return tuple(sorted(value))

    @field_validator("evidence_fingerprints")
    @classmethod
    def validate_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(value, "evidence_fingerprints", pattern=_SHA256)

    @field_validator(
        "provenance_ids",
        "served_network_place_ids",
        "served_access_obligation_ids",
        "served_strategic_destination_ids",
    )
    @classmethod
    def validate_ids(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        return _canonical_ids(value, getattr(info, "field_name", "identifiers"))

    @field_validator("alignment_bases")
    @classmethod
    def validate_alignment_bases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unsupported = set(value) - _ALIGNMENT_BASIS_VOCABULARY
        if unsupported:
            raise ValueError(
                "alignment_bases contain unsupported Alignment Basis values: "
                + ", ".join(sorted(unsupported))
            )
        return _canonical_ids(value, "alignment_bases")

    @field_validator("governed_evidence_ids")
    @classmethod
    def validate_governed_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(value, "governed_evidence_ids")

    @field_validator("primary_alignment_basis")
    @classmethod
    def validate_primary_alignment_basis(cls, value: str | None) -> str | None:
        if value is not None and _ID.fullmatch(value) is None:
            raise ValueError("primary_alignment_basis must be a canonical identifier")
        if value is not None and value not in _ALIGNMENT_BASIS_VOCABULARY:
            raise ValueError(
                "primary_alignment_basis contains unsupported Alignment Basis value: "
                + value
            )
        return value

    @field_validator(
        "directness_m",
        "maximum_gradient_pct",
        "total_absolute_elevation_change_m",
    )
    @classmethod
    def validate_numbers(cls, value: float | None, info: object) -> float | None:
        return None if value is None else _finite(value, getattr(info, "field_name", "number"))

    @model_validator(mode="after")
    def bind_candidate(self) -> Self:
        observations = list(self.traffic_observations)
        if (
            self.traffic_observation is not None
            and self.traffic_observation not in observations
        ):
            observations.append(self.traffic_observation)
        observations = sorted(
            observations,
            key=lambda item: (
                item.observation_id,
                item.source_export_fingerprint,
                item.row_fingerprint,
            ),
        )
        object.__setattr__(self, "traffic_observations", tuple(observations))
        conflict_evidence = _derive_traffic_conflict_evidence(tuple(observations))
        if (
            self.traffic_conflict_evidence is not None
            and self.traffic_conflict_evidence != conflict_evidence
        ):
            raise ValueError("traffic_conflict_evidence is stale for its observations")
        object.__setattr__(self, "traffic_conflict_evidence", conflict_evidence)
        if (
            self.primary_alignment_basis is not None
            and self.primary_alignment_basis not in self.alignment_bases
        ):
            raise ValueError("primary_alignment_basis must be one of alignment_bases")
        payload = self.model_dump(
            mode="json",
            exclude={
                "candidate_id",
                "geometry",
                # Optional evidence annotations are identity-bearing only when
                # present.  This preserves every legacy/default candidate ID.
                *(
                    ("traffic_observation", "protected_space_evidence", "traffic_exposure")
                    if not self.traffic_observations
                    and self.protected_space_evidence is None
                    and self.traffic_exposure is None
                    else ()
                ),
            },
        )
        payload["geometry_fingerprint"] = self.geometry.fingerprint
        expected = _stable_id("candidate", payload)
        if self.candidate_id and self.candidate_id != expected:
            raise ValueError("candidate_id is stale for its canonical candidate identity")
        object.__setattr__(self, "candidate_id", expected)
        return self

    @property
    def geometry_fingerprint(self) -> str:
        return self.geometry.fingerprint

    @property
    def geometry_equivalence_fingerprint(self) -> str:
        return self.geometry.equivalence_fingerprint


def _derive_traffic_conflict_evidence(
    observations: tuple[TrafficObservation, ...],
) -> TrafficConflictEvidence | None:
    """Return a canonical conflict roster without selecting an observation."""

    if not observations:
        return None
    normalized_exclusions = {
        "observation_id",
        "source_export_fingerprint",
        "row_fingerprint",
        "evidence_ids",
        "provenance_ids",
    }
    by_claim: dict[tuple[object, ...], list[TrafficObservation]] = {}
    for item in observations:
        by_claim.setdefault(
            (item.count_point_id, item.observation_year, item.direction_of_travel),
            [],
        ).append(item)

    def field_differences(group: list[TrafficObservation]) -> tuple[str, ...]:
        if len(group) < 2:
            return ()
        baseline = group[0].model_dump(mode="json")
        return tuple(
            sorted(
                {
                    field
                    for item in group[1:]
                    for field, value in item.model_dump(mode="json").items()
                    if field not in normalized_exclusions and value != baseline[field]
                }
            )
        )

    conflicting_claims = [
        group for group in by_claim.values() if field_differences(group)
    ]
    explicitly_conflicting = tuple(
        item
        for item in observations
        if item.match_state == TrafficMatchState.CONFLICTING
        or item.coverage_status == TrafficCoverageStatus.CONFLICTING
    )
    if not explicitly_conflicting and not conflicting_claims:
        return None

    fields = {
        field
        for group in conflicting_claims
        for field in field_differences(group)
    }
    fields.update(
        field
        for field in ("match_state", "coverage_status")
        if any(
            getattr(item, field)
            in {TrafficMatchState.CONFLICTING, TrafficCoverageStatus.CONFLICTING}
            for item in explicitly_conflicting
        )
    )
    # Retain all observations when any conflict exists.  This is intentionally
    # conservative for distinct directional claims: no observation is silently
    # discarded while the caller still receives an unknown aggregate status.
    return TrafficConflictEvidence(
        observation_ids=tuple(item.observation_id for item in observations),
        source_export_fingerprints=tuple(
            item.source_export_fingerprint for item in observations
        ),
        row_fingerprints=tuple(item.row_fingerprint for item in observations),
        conflicting_fields=tuple(sorted(fields)),
        evidence_ids=tuple(
            sorted({identifier for item in observations for identifier in item.evidence_ids})
        ),
        provenance_ids=tuple(
            sorted({identifier for item in observations for identifier in item.provenance_ids})
        ),
    )


def traffic_diagnostics_for_candidate(
    candidate: AlignmentCandidateInput,
    profile: NetworkSelectionProfile,
) -> tuple[dict[str, object], ...]:
    """Derive non-veto traffic/protected-space diagnostics for one candidate."""

    traffic_profile = profile.traffic_profile
    if candidate.traffic_exposure != TrafficExposure.ON_CARRIAGEWAY:
        return ()
    observations = candidate.traffic_observations
    if not observations and candidate.traffic_observation is not None:
        observations = (candidate.traffic_observation,)
    if traffic_profile is None:
        if not observations:
            return ()
        conflict = candidate.traffic_conflict_evidence
        return (
            {
                "candidate_id": candidate.candidate_id,
                "diagnostic_id": "traffic-conflict" if conflict is not None else "traffic-unknown",
                "traffic_status": "conflicting" if conflict is not None else "profile-unavailable",
                "traffic_profile_fingerprint": None,
                "traffic_observation_ids": tuple(
                    item.observation_id for item in observations
                ),
                "source_export_fingerprints": tuple(
                    sorted(item.source_export_fingerprint for item in observations)
                ),
                "row_fingerprints": tuple(
                    sorted(item.row_fingerprint for item in observations)
                ),
                "evidence_ids": tuple(
                    sorted(
                        {
                            identifier
                            for item in observations
                            for identifier in item.evidence_ids
                        }
                    )
                ),
                "provenance_ids": tuple(
                    sorted(
                        {
                            identifier
                            for item in observations
                            for identifier in item.provenance_ids
                        }
                    )
                ),
                **(
                    {
                        "traffic_conflict_evidence": candidate.traffic_conflict_evidence.model_dump(
                            mode="json"
                        )
                    }
                    if candidate.traffic_conflict_evidence is not None
                    else {}
                ),
            },
        )
    if not observations:
        return (
            {
                "candidate_id": candidate.candidate_id,
                "diagnostic_id": "traffic-unknown",
                "traffic_status": "unknown",
                "traffic_profile_fingerprint": traffic_profile.fingerprint,
                "evidence_ids": (),
                "provenance_ids": (),
            },
        )

    observation_roster = tuple(observations)
    by_claim: dict[tuple[object, ...], list[TrafficObservation]] = {}
    for item in observations:
        by_claim.setdefault(
            (item.count_point_id, item.observation_year, item.direction_of_travel),
            [],
        ).append(item)
    normalized_exclusions = {
        "observation_id",
        "source_export_fingerprint",
        "row_fingerprint",
        "evidence_ids",
        "provenance_ids",
    }

    def field_differences(group: list[TrafficObservation]) -> tuple[str, ...]:
        if len(group) < 2:
            return ()
        baseline = group[0].model_dump(mode="json")
        return tuple(
            sorted(
                {
                    field
                    for item in group[1:]
                    for field, value in item.model_dump(mode="json").items()
                    if field not in normalized_exclusions and value != baseline[field]
                }
            )
        )

    conflicting_claims = [
        group
        for group in by_claim.values()
        if field_differences(group)
    ]
    explicitly_conflicting = tuple(
        item
        for item in observation_roster
        if item.match_state == TrafficMatchState.CONFLICTING
        or item.coverage_status == TrafficCoverageStatus.CONFLICTING
    )
    explicitly_conflicting_claims = {
        (item.count_point_id, item.observation_year, item.direction_of_travel)
        for item in explicitly_conflicting
    }
    local_explicit_conflict = bool(explicitly_conflicting) and (
        len(by_claim) == 1
        and len(explicitly_conflicting_claims) == 1
    )
    if (
        candidate.traffic_conflict_evidence is not None
        or local_explicit_conflict
        or conflicting_claims
    ):
        difference_fields: set[str] = set()
        for group in conflicting_claims:
            difference_fields.update(field_differences(group))
        difference_fields.update(
            field
            for field in ("match_state", "coverage_status")
            if any(
                getattr(item, field)
                in {
                    TrafficMatchState.CONFLICTING,
                    TrafficCoverageStatus.CONFLICTING,
                }
                for item in explicitly_conflicting
            )
        )
        if candidate.traffic_conflict_evidence is not None:
            difference_fields.update(candidate.traffic_conflict_evidence.conflicting_fields)
        explicit_ids = {item.observation_id for item in explicitly_conflicting}
        claim_observations = tuple(
            item
            for group in by_claim.values()
            if field_differences(group)
            or any(item.observation_id in explicit_ids for item in group)
            for item in group
        )
        if not claim_observations and candidate.traffic_conflict_evidence is not None:
            typed_ids = set(candidate.traffic_conflict_evidence.observation_ids)
            claim_observations = tuple(
                item for item in observations if item.observation_id in typed_ids
            )
        claim_observations = tuple(
            sorted(
                claim_observations,
                key=lambda item: (
                    item.observation_id,
                    item.source_export_fingerprint,
                    item.row_fingerprint,
                ),
            )
        )
        conflict_diagnostic = {
                "candidate_id": candidate.candidate_id,
                "diagnostic_id": "traffic-conflict",
                "traffic_status": "conflicting",
                "traffic_profile_fingerprint": traffic_profile.fingerprint,
                "traffic_observation_ids": tuple(
                    item.observation_id for item in claim_observations
                ),
                "source_export_fingerprints": tuple(
                    sorted(item.source_export_fingerprint for item in claim_observations)
                ),
                "row_fingerprints": tuple(
                    sorted(item.row_fingerprint for item in claim_observations)
                ),
                "all_motor_vehicles": None,
                "field_differences": tuple(sorted(difference_fields)),
                "evidence_ids": tuple(
                    sorted(
                        {
                            identifier
                            for item in claim_observations
                            for identifier in item.evidence_ids
                        }
                    )
                ),
                "provenance_ids": tuple(
                    sorted(
                        {
                            identifier
                            for item in claim_observations
                            for identifier in item.provenance_ids
                        }
                    )
                ),
                **(
                    {
                        "traffic_conflict_evidence": candidate.traffic_conflict_evidence.model_dump(
                            mode="json"
                        )
                    }
                    if candidate.traffic_conflict_evidence is not None
                    else {}
                ),
            }
        claim_ids = {item.observation_id for item in claim_observations}
        remaining = tuple(
            item for item in observations if item.observation_id not in claim_ids
        )
        if not remaining:
            return (conflict_diagnostic,)
        # A typed conflict is an additional diagnostic, not permission to hide
        # a separate observation that independently drives a high-traffic
        # challenge.  Re-run the ordinary evaluation over the non-conflicting
        # remainder and retain deterministic conflict-first ordering.
        reduced_candidate = candidate.model_copy(
            update={
                "traffic_observations": remaining,
                "traffic_observation": None,
                "traffic_conflict_evidence": None,
            }
        )
        return (conflict_diagnostic, *traffic_diagnostics_for_candidate(reduced_candidate, profile))
    deduped: list[TrafficObservation] = []
    substantive_by_key: set[str] = set()
    for item in observations:
        substantive = item.model_dump(mode="json", exclude=normalized_exclusions)
        key = json.dumps(substantive, sort_keys=True, separators=(",", ":"))
        if key not in substantive_by_key:
            substantive_by_key.add(key)
            deduped.append(item)
    observations = tuple(deduped)
    if len(observations) > 1:
        combined = tuple(
            item
            for item in observations
            if item.direction_of_travel == "combined"
        )
        if len(combined) != 1:
            return (
                {
                    "candidate_id": candidate.candidate_id,
                    "diagnostic_id": "traffic-unknown",
                    "traffic_status": "multiple-observations-no-combined",
                    "traffic_profile_fingerprint": traffic_profile.fingerprint,
                    "traffic_observation_ids": tuple(
                        item.observation_id for item in observations
                    ),
                    "source_export_fingerprints": tuple(
                        sorted(item.source_export_fingerprint for item in observations)
                    ),
                    "row_fingerprints": tuple(
                        sorted(item.row_fingerprint for item in observations)
                    ),
                    "evidence_ids": tuple(
                        sorted(
                            {
                                identifier
                                for item in observations
                                for identifier in item.evidence_ids
                            }
                        )
                    ),
                    "provenance_ids": tuple(
                        sorted(
                            {
                                identifier
                                for item in observations
                                for identifier in item.provenance_ids
                            }
                        )
                    ),
                    **(
                        {
                            "traffic_conflict_evidence": (
                                candidate.traffic_conflict_evidence.model_dump(
                                    mode="json"
                                )
                            )
                        }
                        if candidate.traffic_conflict_evidence is not None
                        else {}
                    ),
                },
            )
        observation = combined[0]
    else:
        observation = observations[0]

    freshness_state = traffic_profile.freshness_for(
        observation.observation_year,
        observation.freshness_state,
    )
    common = {
        "candidate_id": candidate.candidate_id,
        "traffic_observation_id": observation.observation_id,
        "traffic_observation_ids": tuple(
            item.observation_id for item in observation_roster
        ),
        "observation_year": observation.observation_year,
        "traffic_profile_fingerprint": traffic_profile.fingerprint,
        "source_export_fingerprint": observation.source_export_fingerprint,
        "source_export_fingerprints": tuple(
            sorted(item.source_export_fingerprint for item in observation_roster)
        ),
        "row_fingerprint": observation.row_fingerprint,
        "row_fingerprints": tuple(
            sorted(item.row_fingerprint for item in observation_roster)
        ),
        "all_motor_vehicles": observation.all_motor_vehicles,
        "freshness_state": freshness_state.value,
        "estimation_method": observation.estimation_method,
        "evidence_ids": tuple(
            sorted(
                {
                    identifier
                    for item in observation_roster
                    for identifier in item.evidence_ids
                }
            )
        ),
        "provenance_ids": tuple(
            sorted(
                {
                    identifier
                    for item in observation_roster
                    for identifier in item.provenance_ids
                }
            )
        ),
        **(
            {
                "traffic_conflict_evidence": candidate.traffic_conflict_evidence.model_dump(
                    mode="json"
                )
            }
            if candidate.traffic_conflict_evidence is not None
            else {}
        ),
        **(
            {
                "freshness_configuration_diagnostic": (
                    traffic_profile.freshness_configuration_diagnostic
                )
            }
            if traffic_profile.freshness_configuration_diagnostic is not None
            else {}
        ),
    }
    if (
        observation.source_layer != "aadf"
        or observation.all_motor_vehicles is None
        or observation.match_state != TrafficMatchState.MATCHED
        or observation.coverage_status != TrafficCoverageStatus.SAMPLED
        or freshness_state == TrafficFreshnessState.UNKNOWN
    ):
        return (
            {
                **common,
                "diagnostic_id": (
                    "traffic-freshness-configuration"
                    if traffic_profile.freshness_configuration_diagnostic is not None
                    and freshness_state == TrafficFreshnessState.UNKNOWN
                    else "traffic-unknown"
                ),
                "traffic_status": "unknown",
            },
        )

    value = observation.all_motor_vehicles
    band = next(
        (
            threshold.id
            for threshold in traffic_profile.thresholds
            if threshold.upper_vehicles_per_day is None
            or value <= threshold.upper_vehicles_per_day
        ),
        None,
    )
    if band is None:
        return (
            {
                **common,
                "diagnostic_id": "traffic-unknown",
                "traffic_status": "unknown",
            },
        )
    protected = candidate.protected_space_evidence
    protected_state = (
        protected.state if protected is not None else ProtectedSpaceState.UNKNOWN
    )
    protected_evidence_ids = protected.evidence_ids if protected is not None else ()
    protected_provenance_ids = protected.provenance_ids if protected is not None else ()
    traffic_evidence_ids = tuple(
        sorted(
            {
                identifier
                for item in observation_roster
                for identifier in item.evidence_ids
            }
        )
    )
    traffic_provenance_ids = tuple(
        sorted(
            {
                identifier
                for item in observation_roster
                for identifier in item.provenance_ids
            }
        )
    )
    stale_diagnostic = (
        {
            **common,
            "diagnostic_id": "traffic-stale",
            "traffic_status": "stale",
            "traffic_band": band,
            "protected_space_state": protected_state.value,
            "protected_space_evidence_ids": protected_evidence_ids,
            "protected_space_provenance_ids": protected_provenance_ids,
            "evidence_ids": tuple(sorted((*traffic_evidence_ids, *protected_evidence_ids))),
            "provenance_ids": tuple(
                sorted((*traffic_provenance_ids, *protected_provenance_ids))
            ),
        }
        if freshness_state == TrafficFreshnessState.STALE
        else None
    )
    estimated_diagnostic = (
        {
            **common,
            "diagnostic_id": "traffic-estimated",
            "traffic_status": "estimated",
            "traffic_band": band,
            "protected_space_state": protected_state.value,
            "protected_space_evidence_ids": protected_evidence_ids,
            "protected_space_provenance_ids": protected_provenance_ids,
        }
        if observation.estimation_method is not None
        and observation.estimation_method.lower() == "estimated"
        else None
    )

    def with_auxiliary(*records: dict[str, object]) -> tuple[dict[str, object], ...]:
        return tuple(
            (
                *([stale_diagnostic] if stale_diagnostic is not None else []),
                *([estimated_diagnostic] if estimated_diagnostic is not None else []),
                *records,
            )
        )

    if band != traffic_profile.high_traffic_challenge_band:
        if protected_state in {
            ProtectedSpaceState.MISSING,
            ProtectedSpaceState.STALE,
            ProtectedSpaceState.UNKNOWN,
            ProtectedSpaceState.CONFLICTING,
        }:
            return with_auxiliary(
                {
                    **common,
                    "diagnostic_id": (
                        "protected-space-conflict"
                        if protected_state == ProtectedSpaceState.CONFLICTING
                        else "protected-space-evidence-unknown"
                    ),
                    "traffic_band": band,
                    "protected_space_state": protected_state.value,
                    "protected_space_evidence_ids": (
                        protected.evidence_ids if protected is not None else ()
                    ),
                    "protected_space_provenance_ids": (
                        protected.provenance_ids if protected is not None else ()
                    ),
                },
            )
        return with_auxiliary()

    if protected_state == ProtectedSpaceState.PRESENT:
        return with_auxiliary()
    if protected_state == ProtectedSpaceState.ABSENT:
        diagnostic_id = "traffic-high-on-carriageway-without-protected-space"
    elif protected_state == ProtectedSpaceState.CONFLICTING:
        diagnostic_id = "protected-space-conflict"
    else:
        diagnostic_id = "protected-space-evidence-unknown"
    challenge = {
        **TrafficChallengeDiagnostic(
            diagnostic_id=diagnostic_id,
            candidate_id=candidate.candidate_id,
            traffic_observation_id=observation.observation_id,
            observation_year=observation.observation_year,
            traffic_band=band,
            traffic_profile_fingerprint=traffic_profile.fingerprint,
            source_export_fingerprint=observation.source_export_fingerprint,
            row_fingerprint=observation.row_fingerprint,
            freshness_state=freshness_state,
            estimation_method=observation.estimation_method,
            protected_space_state=protected_state,
            evidence_ids=tuple(sorted((*traffic_evidence_ids, *protected_evidence_ids))),
            provenance_ids=tuple(
                sorted((*traffic_provenance_ids, *protected_provenance_ids))
            ),
        ).model_dump(mode="json"),
        "traffic_observation_ids": tuple(
            item.observation_id for item in observation_roster
        ),
        "source_export_fingerprints": tuple(
            sorted(item.source_export_fingerprint for item in observation_roster)
        ),
        "row_fingerprints": tuple(
            sorted(item.row_fingerprint for item in observation_roster)
        ),
    }
    return with_auxiliary(challenge)


class CandidateAdmission(BaseModel):
    """One exact pre-comparison admission disposition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(pattern=_CANDIDATE_ID.pattern)
    disposition: AdmissionDisposition
    rationale: AdmissionRationale
    retained_candidate_id: str | None = Field(
        default=None,
        pattern=_CANDIDATE_ID.pattern,
    )
    change_conditions: tuple[ChangeCondition, ...] = ()

    @model_validator(mode="after")
    def validate_disposition(self) -> Self:
        if self.disposition == AdmissionDisposition.ADMITTED:
            if self.rationale != AdmissionRationale.ADMITTED or self.retained_candidate_id:
                raise ValueError("admitted candidates require only the admitted rationale")
        elif self.rationale == AdmissionRationale.ADMITTED:
            raise ValueError("rejected candidates cannot use the admitted rationale")
        if (self.rationale == AdmissionRationale.DUPLICATE_GEOMETRY) != (
            self.retained_candidate_id is not None
        ):
            raise ValueError("only a duplicate rejection binds a retained candidate")
        conditions = tuple(sorted(set(self.change_conditions), key=str))
        object.__setattr__(self, "change_conditions", conditions)
        return self


def _derive_admissions(
    candidates: tuple[AlignmentCandidateInput, ...],
    *,
    precedence_order: tuple[CandidateSourceClass, ...] = (),
    class_order: tuple[ReuseFirstCandidateClass, ...] | None = None,
    intervention_order: tuple[InterventionState, ...] | None = None,
    maximum_options: int,
    mandatory_network_place_ids: tuple[str, ...],
    mandatory_access_obligation_ids: tuple[str, ...],
    mandatory_strategic_destination_ids: tuple[str, ...],
) -> tuple[CandidateAdmission, ...]:
    precedence = {
        source: index for index, source in enumerate(precedence_order or ())
    }
    classes = {candidate_class: index for index, candidate_class in enumerate(class_order or ())}
    interventions = {
        state: index for index, state in enumerate(intervention_order or ())
    }
    if class_order is not None:
        if any(item.reuse_class not in classes for item in candidates):
            raise ValueError("candidate reuse class is absent from the frozen profile")
        if any(item.intervention_state not in interventions for item in candidates):
            raise ValueError("candidate intervention state is absent from the frozen profile")
    elif any(item.source_class not in precedence for item in candidates):
        raise ValueError("candidate source class is absent from the frozen profile")
    ordered = sorted(
        candidates,
        key=(
            lambda item: (
                classes[item.reuse_class],
                interventions[item.intervention_state],
                item.candidate_id,
            )
            if class_order is not None
            else (precedence[item.source_class], item.candidate_id)
        ),
    )
    eligible: list[AlignmentCandidateInput] = []
    records: dict[str, CandidateAdmission] = {}
    retained_by_geometry: list[AlignmentCandidateInput] = []
    for candidate in ordered:
        if not _candidate_covers_set(
            candidate,
            places=mandatory_network_place_ids,
            obligations=mandatory_access_obligation_ids,
            destinations=mandatory_strategic_destination_ids,
        ):
            records[candidate.candidate_id] = CandidateAdmission(
                candidate_id=candidate.candidate_id,
                disposition=AdmissionDisposition.REJECTED,
                rationale=AdmissionRationale.MISSING_ROLE_COVERAGE,
                change_conditions=(ChangeCondition.ROLE_COVERAGE_CHANGES,),
            )
            continue
        retained = next(
            (
                item
                for item in retained_by_geometry
                if item.geometry.materially_equivalent(candidate.geometry)
            ),
            None,
        )
        if retained is not None:
            records[candidate.candidate_id] = CandidateAdmission(
                candidate_id=candidate.candidate_id,
                disposition=AdmissionDisposition.REJECTED,
                rationale=AdmissionRationale.DUPLICATE_GEOMETRY,
                retained_candidate_id=retained.candidate_id,
                change_conditions=(ChangeCondition.EVIDENCE_CHANGES,),
            )
            continue
        retained_by_geometry.append(candidate)
        eligible.append(candidate)

    diverse: list[AlignmentCandidateInput] = []
    if class_order is not None:
        for candidate_class in class_order:
            first = next(
                (
                    item
                    for item in eligible
                    if item.reuse_class == candidate_class and item not in diverse
                ),
                None,
            )
            if first is not None and len(diverse) < maximum_options:
                diverse.append(first)
    else:
        for source in precedence_order:
            first = next(
                (
                    item
                    for item in eligible
                    if item.source_class == source and item not in diverse
                ),
                None,
            )
            if first is not None and len(diverse) < maximum_options:
                diverse.append(first)
    for candidate in eligible:
        if candidate not in diverse and len(diverse) < maximum_options:
            diverse.append(candidate)
    admitted_ids = {item.candidate_id for item in diverse}
    for candidate in eligible:
        if candidate.candidate_id in admitted_ids:
            records[candidate.candidate_id] = CandidateAdmission(
                candidate_id=candidate.candidate_id,
                disposition=AdmissionDisposition.ADMITTED,
                rationale=AdmissionRationale.ADMITTED,
            )
        else:
            records[candidate.candidate_id] = CandidateAdmission(
                candidate_id=candidate.candidate_id,
                disposition=AdmissionDisposition.REJECTED,
                rationale=AdmissionRationale.PROFILE_LIMIT,
                change_conditions=(ChangeCondition.PROFILE_CHANGES,),
            )
    return tuple(sorted(records.values(), key=lambda item: item.candidate_id))


class AlignmentCandidateSet(BaseModel):
    """All generated candidates and their exact, finite admission record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    network_role: NetworkRole
    endpoints: tuple[str, str]
    mandatory_network_place_ids: tuple[str, ...] = ()
    mandatory_access_obligation_ids: tuple[str, ...] = ()
    mandatory_strategic_destination_ids: tuple[str, ...] = ()
    profile: NetworkSelectionProfile
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    candidate_source_precedence: tuple[CandidateSourceClass, ...] = ()
    candidate_class_order: tuple[ReuseFirstCandidateClass, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    maximum_options: int = Field(ge=1, strict=True)
    geometry_equivalence_profile: MaterialGeometryEquivalenceProfile
    candidates: tuple[AlignmentCandidateInput, ...] = ()
    admissions: tuple[CandidateAdmission, ...] = ()
    generation_gap_reason: CandidateGenerationGapReason = CandidateGenerationGapReason.NONE
    candidate_set_id: str = ""
    candidate_set_fingerprint: str = ""
    connection_id: str = ""

    @field_validator("endpoints")
    @classmethod
    def validate_endpoints(cls, value: tuple[str, str]) -> tuple[str, str]:
        return AlignmentCandidateInput.validate_endpoints(value)

    @field_validator(
        "mandatory_network_place_ids",
        "mandatory_access_obligation_ids",
        "mandatory_strategic_destination_ids",
    )
    @classmethod
    def validate_mandatory_ids(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        return _canonical_ids(value, getattr(info, "field_name", "mandatory identifiers"))

    @field_validator("candidate_source_precedence")
    @classmethod
    def validate_precedence(
        cls,
        value: tuple[CandidateSourceClass, ...],
    ) -> tuple[CandidateSourceClass, ...]:
        if len(set(value)) != len(value):
            raise ValueError("candidate source precedence must be finite and unique")
        return value

    @model_validator(mode="after")
    def bind_set(self) -> Self:
        profile = NetworkSelectionProfile.model_validate(self.profile.model_dump(mode="json"))
        if self.profile_fingerprint != profile.fingerprint:
            raise ValueError("Candidate Set profile fingerprint is stale")
        if profile.contract == "satn-network-selection-profile/vNext":
            if self.candidate_source_precedence:
                raise ValueError("vNext Candidate Set cannot carry legacy source precedence")
            if self.candidate_class_order != profile.candidate_class_order:
                raise ValueError("Candidate Set reuse class order is stale")
            if self.maximum_options != profile.maximum_options_per_candidate_set:
                raise ValueError("Candidate Set option limit is stale")
            if any(
                candidate.reuse_class is None
                or candidate.intervention_state is None
                or not candidate.alignment_bases
                or candidate.primary_alignment_basis is None
                or candidate.transition_count is None
                or candidate.fragmentation_count is None
                or not candidate.governed_evidence_ids
                for candidate in self.candidates
            ):
                raise ValueError("vNext candidates require complete immutable selection facts")
        else:
            if self.candidate_source_precedence != profile.candidate_source_precedence:
                raise ValueError("Candidate Set source precedence is stale")
            if self.candidate_class_order is not None:
                raise ValueError("legacy Candidate Sets cannot carry reuse class order")
            if self.maximum_options != profile.ambiguity.maximum_options_per_candidate_set:
                raise ValueError("Candidate Set option limit is stale")
        object.__setattr__(self, "profile", profile)
        candidates = tuple(
            sorted(
                (
                    AlignmentCandidateInput.model_validate(item.model_dump(mode="json"))
                    for item in self.candidates
                ),
                key=lambda item: item.candidate_id,
            )
        )
        ids = tuple(item.candidate_id for item in candidates)
        if len(set(ids)) != len(ids):
            raise ValueError("Candidate Set candidate identities must be unique")
        if any(
            item.network_role != self.network_role or item.endpoints != self.endpoints
            for item in candidates
        ):
            raise ValueError("all candidates must assert the Candidate Set role and endpoints")
        if any(
            item.geometry.equivalence_profile != self.geometry_equivalence_profile
            for item in candidates
        ):
            raise ValueError("Candidate Set geometries must share one governed equivalence profile")
        if (
            not candidates
            and self.geometry_equivalence_profile != MaterialGeometryEquivalenceProfile()
        ):
            raise ValueError("empty Candidate Set geometry policy must use the compiler default")
        admissions = tuple(sorted(self.admissions, key=lambda item: item.candidate_id))
        expected_admissions = _derive_admissions(
            candidates,
            precedence_order=self.candidate_source_precedence,
            class_order=self.candidate_class_order,
            intervention_order=profile.intervention_state_order,
            maximum_options=self.maximum_options,
            mandatory_network_place_ids=self.mandatory_network_place_ids,
            mandatory_access_obligation_ids=self.mandatory_access_obligation_ids,
            mandatory_strategic_destination_ids=self.mandatory_strategic_destination_ids,
        )
        if admissions != expected_admissions:
            raise ValueError("candidate admissions are not the deterministic profile result")
        admission_ids = tuple(item.candidate_id for item in admissions)
        if admission_ids != ids:
            raise ValueError("every generated candidate needs exactly one admission disposition")
        admitted_ids = {
            item.candidate_id
            for item in admissions
            if item.disposition == AdmissionDisposition.ADMITTED
        }
        expected_gap_reason = (
            CandidateGenerationGapReason.NONE
            if admitted_ids
            else (
                CandidateGenerationGapReason.NO_GENERATED_CANDIDATES
                if not candidates
                else CandidateGenerationGapReason.ALL_GENERATED_CANDIDATES_REJECTED
            )
        )
        if self.generation_gap_reason != expected_gap_reason:
            raise ValueError("Candidate Set generation gap reason is not compiler-derived")
        by_id = {item.candidate_id: item for item in candidates}
        for admission in admissions:
            if admission.retained_candidate_id not in admitted_ids | {None}:
                raise ValueError("duplicate rejection must bind an admitted retained candidate")
            if admission.disposition != AdmissionDisposition.ADMITTED:
                continue
            candidate = by_id[admission.candidate_id]
            if (
                not set(self.mandatory_network_place_ids).issubset(
                    candidate.served_network_place_ids
                )
                or not set(self.mandatory_access_obligation_ids).issubset(
                    candidate.served_access_obligation_ids
                )
                or not set(self.mandatory_strategic_destination_ids).issubset(
                    candidate.served_strategic_destination_ids
                )
            ):
                raise ValueError("admitted candidate does not serve its Candidate Set obligations")
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "admissions", admissions)
        connection_id = _stable_id(
            "connection",
            {
                "network_role": self.network_role,
                "endpoints": self.endpoints,
            },
        )
        if self.connection_id and self.connection_id != connection_id:
            raise ValueError("connection_id is stale for canonical role and endpoints")
        object.__setattr__(self, "connection_id", connection_id)
        payload = self.model_dump(
            mode="json",
            exclude={"candidate_set_id", "candidate_set_fingerprint"},
        )
        fingerprint = _fingerprint(payload)
        identifier = _stable_id("candidate-set", payload)
        if self.candidate_set_id and self.candidate_set_id != identifier:
            raise ValueError("candidate_set_id is stale for its complete Candidate Set")
        if self.candidate_set_fingerprint and self.candidate_set_fingerprint != fingerprint:
            raise ValueError("candidate_set_fingerprint is stale")
        object.__setattr__(self, "candidate_set_id", identifier)
        object.__setattr__(self, "candidate_set_fingerprint", fingerprint)
        return self

    @property
    def admitted_candidates(self) -> tuple[AlignmentCandidateInput, ...]:
        admitted = {
            item.candidate_id
            for item in self.admissions
            if item.disposition == AdmissionDisposition.ADMITTED
        }
        return tuple(item for item in self.candidates if item.candidate_id in admitted)


class CriterionFinding(BaseModel):
    """Claim-safe evidence availability or hard-gate finding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(pattern=_CANDIDATE_ID.pattern)
    state: CriterionState
    detail: CriterionDetail
    assessment_id: str = Field(min_length=1)
    evidence_record_id: str = Field(min_length=1)


class PopulationReachFinding(BaseModel):
    """One whole-population result; no preferred flag or material judgement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(pattern=_CANDIDATE_ID.pattern)
    radius_m: Literal[500, 1000]
    resident_count: int | None = Field(default=None, ge=0, strict=True)
    state: CriterionState
    assessment_id: str = Field(min_length=1)
    assessment_option_id: str = Field(min_length=1)
    candidate_geometry_fingerprint: str = Field(pattern=_SHA256.pattern)
    rank: int | None = Field(default=None, ge=1, strict=True)
    near_equivalent: bool = Field(strict=True)
    borderline_oa_ids: tuple[str, ...] = ()
    decisive_borderline_oa_ids: tuple[str, ...] = ()
    current_development_omission: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.state == CriterionState.UNSATISFIED:
            raise ValueError("population evidence state is available or unknown")
        if (self.state == CriterionState.UNKNOWN) != (self.resident_count is None):
            raise ValueError("unknown population evidence has no resident count and vice versa")
        if (self.state == CriterionState.UNKNOWN) != (self.rank is None):
            raise ValueError("unknown population evidence has no ranking and vice versa")
        object.__setattr__(
            self,
            "borderline_oa_ids",
            _canonical_population_oa_ids(
                self.borderline_oa_ids,
                "borderline_oa_ids",
            ),
        )
        object.__setattr__(
            self,
            "decisive_borderline_oa_ids",
            _canonical_population_oa_ids(
                self.decisive_borderline_oa_ids,
                "decisive_borderline_oa_ids",
            ),
        )
        if not set(self.decisive_borderline_oa_ids).issubset(self.borderline_oa_ids):
            raise ValueError("decisive borderline OAs must be borderline OAs")
        return self


class CandidatePopulationOptionBinding(BaseModel):
    """Exact bridge from one compiler candidate to one assessed route option."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(pattern=_CANDIDATE_ID.pattern)
    option_id: str = Field(min_length=1)
    assessment_geometry_sha256: str = Field(pattern=_SHA256.pattern)


def _validate_population_assessment(
    assessment: PopulationReachAssessment,
) -> str:
    """Recompute every conclusion available without the governed source frames."""
    _assert_unique_record_keys(list(assessment.records))
    expected_summaries = tuple(_summarise_records(list(assessment.records)))
    if assessment.summaries != expected_summaries:
        raise ValueError("population summaries are not derived from assessment records")
    missing_development = _missing_current_development_evidence(assessment.source)
    expected_sensitivities = tuple(
        _derive_sensitivities(
            list(assessment.records),
            list(expected_summaries),
            assessment.profile,
            missing_current_development_evidence=missing_development,
        )
    )
    if assessment.sensitivities != expected_sensitivities:
        raise ValueError("population sensitivities are not derived from assessment records")
    if assessment.warnings != _current_development_warnings(assessment.source):
        raise ValueError("population warnings are not derived from governed development evidence")
    if assessment.prohibited_claims != PROHIBITED_CLAIMS:
        raise ValueError("population assessment prohibited claims are incomplete")
    if assessment.coordinate_transformation_lineage != _coordinate_transformation_lineage():
        raise ValueError("population coordinate lineage is not canonical")
    option_ids = tuple(item.option_id for item in assessment.option_geometries)
    if option_ids != tuple(sorted(set(option_ids))):
        raise ValueError("population option geometries must be unique and ordered")
    if {item.option_id for item in assessment.summaries} != set(option_ids):
        raise ValueError("population summaries do not cover exact option geometries")
    payload = assessment.canonical()
    payload.pop("assessment_id")
    expected_id = f"population-reach-v1-{_fingerprint(payload)[:16]}"
    if assessment.assessment_id != expected_id:
        raise ValueError("population assessment ID is stale")
    return _fingerprint(assessment.canonical())


def _derive_population_findings(
    assessment: PopulationReachAssessment,
    bindings: tuple[CandidatePopulationOptionBinding, ...],
) -> tuple[
    tuple[PopulationReachFinding, ...],
    tuple[PopulationReachFinding, ...],
]:
    by_option = {item.option_id: item for item in bindings}
    summaries = {
        (item.option_id, int(item.corridor_distance_m)): item for item in assessment.summaries
    }
    sensitivities = {int(item.corridor_distance_m): item for item in assessment.sensitivities}
    records = tuple(assessment.records)
    sections: list[tuple[PopulationReachFinding, ...]] = []
    for radius_m in (500, 1000):
        sensitivity = sensitivities[radius_m]
        ranking = {
            option_id: index
            for index, option_id in enumerate(
                sensitivity.option_ranking,
                start=1,
            )
        }
        highest = max(summaries[(option_id, radius_m)].total_residents for option_id in by_option)
        tolerance = max(
            assessment.profile.comparison_tolerance_residents,
            highest * assessment.profile.comparison_tolerance_percent / 100,
        )
        findings: list[PopulationReachFinding] = []
        decisive_ids = set(sensitivity.individually_decisive_borderline_oa_ids)
        for binding in sorted(bindings, key=lambda item: item.candidate_id):
            borderline_ids = tuple(
                sorted(
                    {
                        item.oa_id
                        for item in records
                        if item.option_id == binding.option_id
                        and int(item.corridor_distance_m) == radius_m
                        and item.borderline
                    }
                )
            )
            findings.append(
                PopulationReachFinding(
                    candidate_id=binding.candidate_id,
                    radius_m=radius_m,
                    resident_count=summaries[(binding.option_id, radius_m)].total_residents,
                    state=CriterionState.SATISFIED,
                    assessment_id=assessment.assessment_id,
                    assessment_option_id=binding.option_id,
                    candidate_geometry_fingerprint=(binding.assessment_geometry_sha256),
                    rank=ranking[binding.option_id],
                    near_equivalent=(
                        ranking[binding.option_id] != 1
                        and highest - summaries[(binding.option_id, radius_m)].total_residents
                        <= tolerance
                    ),
                    borderline_oa_ids=borderline_ids,
                    decisive_borderline_oa_ids=tuple(
                        item for item in borderline_ids if item in decisive_ids
                    ),
                    current_development_omission=(sensitivity.missing_current_development_evidence),
                )
            )
        sections.append(tuple(findings))
    return sections[0], sections[1]


class PopulationCriterionSummary(BaseModel):
    """Canonical PopulationReachAssessment plus its exact candidate adapter."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    assessment: PopulationReachAssessment
    option_bindings: tuple[CandidatePopulationOptionBinding, ...] = Field(min_length=1)
    headline_500m: tuple[PopulationReachFinding, ...] = Field(min_length=1)
    sensitivity_1000m: tuple[PopulationReachFinding, ...] = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    assessment_content_sha256: str = Field(pattern=_SHA256.pattern)
    scenario_evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)

    @classmethod
    def from_assessment(
        cls,
        assessment: PopulationReachAssessment,
        *,
        option_bindings: tuple[CandidatePopulationOptionBinding, ...],
        scenario_evidence_snapshot_fingerprint: str,
    ) -> PopulationCriterionSummary:
        fingerprint = _validate_population_assessment(assessment)
        headline, sensitivity = _derive_population_findings(
            assessment,
            option_bindings,
        )
        return cls(
            assessment=assessment,
            option_bindings=option_bindings,
            headline_500m=headline,
            sensitivity_1000m=sensitivity,
            assessment_id=assessment.assessment_id,
            assessment_content_sha256=fingerprint,
            scenario_evidence_snapshot_fingerprint=(scenario_evidence_snapshot_fingerprint),
        )

    @model_validator(mode="after")
    def validate_sections(self) -> Self:
        assessment_fingerprint = _validate_population_assessment(self.assessment)
        bindings = tuple(sorted(self.option_bindings, key=lambda item: item.candidate_id))
        if len({item.candidate_id for item in bindings}) != len(bindings):
            raise ValueError("population option bindings require unique candidates")
        if len({item.option_id for item in bindings}) != len(bindings):
            raise ValueError("population option bindings require unique assessed options")
        geometry_by_option = {
            item.option_id: item.geometry_sha256 for item in self.assessment.option_geometries
        }
        if any(
            geometry_by_option.get(item.option_id) != item.assessment_geometry_sha256
            for item in bindings
        ):
            raise ValueError("population option binding is stale for assessed geometry")
        expected_headline, expected_sensitivity = _derive_population_findings(
            self.assessment,
            bindings,
        )
        if (
            self.headline_500m != expected_headline
            or self.sensitivity_1000m != expected_sensitivity
        ):
            raise ValueError("population findings are not canonical assessment outputs")
        if (
            self.assessment_id != self.assessment.assessment_id
            or self.assessment_content_sha256 != assessment_fingerprint
        ):
            raise ValueError("population assessment binding is stale")
        object.__setattr__(self, "option_bindings", bindings)
        for expected_radius, findings in (
            (500, self.headline_500m),
            (1000, self.sensitivity_1000m),
        ):
            ids = tuple(item.candidate_id for item in findings)
            if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
                raise ValueError("population findings must be canonically ordered and unique")
            if any(item.radius_m != expected_radius for item in findings):
                raise ValueError("population finding is in the wrong radius section")
            ranks = tuple(item.rank for item in findings if item.rank is not None)
            if ranks and tuple(sorted(ranks)) != tuple(range(1, len(ranks) + 1)):
                raise ValueError("population ranking must be exact, unique and contiguous")
            if any(item.assessment_id != self.assessment_id for item in findings):
                raise ValueError("population finding names another assessment")
        return self


class IndependentTravelOpportunityFinding(BaseModel):
    """Separate ITO evidence; it never asserts safety or demand."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(pattern=_CANDIDATE_ID.pattern)
    opportunity_count: int | None = Field(default=None, ge=0, strict=True)
    state: CriterionState
    assessment_id: str = Field(min_length=1)
    evidence_record_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.state == CriterionState.UNSATISFIED:
            raise ValueError("ITO evidence state is available or unknown")
        if (self.state == CriterionState.UNKNOWN) != (self.opportunity_count is None):
            raise ValueError("unknown ITO evidence has no opportunity count and vice versa")
        return self


class CandidateEducationOptionBinding(BaseModel):
    """Compiler-derived bridge from a route identity to its education option evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(pattern=_CANDIDATE_ID.pattern)
    candidate_geometry_fingerprint: str = Field(pattern=_SHA256.pattern)
    candidate_connection_id: str = Field(pattern=_ID.pattern)
    candidate_lineage_fingerprint: str = Field(pattern=_SHA256.pattern)
    option_id: str = Field(min_length=1)
    option_evidence_fingerprint: str = Field(pattern=_SHA256.pattern)

    @model_validator(mode="after")
    def bind_option_identity(self) -> Self:
        expected = _stable_id(
            "education-option",
            {
                "candidate_id": self.candidate_id,
                "candidate_geometry_fingerprint": self.candidate_geometry_fingerprint,
                "candidate_connection_id": self.candidate_connection_id,
                "candidate_lineage_fingerprint": self.candidate_lineage_fingerprint,
            },
        )
        if self.option_id != expected:
            raise ValueError("education option ID is not derived from the exact candidate lineage")
        return self


def _candidate_education_lineage(candidate: AlignmentCandidateInput) -> str:
    return _fingerprint(candidate.model_dump(mode="json"))


def education_option_id_for_candidate(
    candidate: AlignmentCandidateInput,
    candidate_set: AlignmentCandidateSet,
) -> str:
    """Derive the only education option identity allowed for an admitted route."""
    return _stable_id(
        "education-option",
        {
            "candidate_id": candidate.candidate_id,
            "candidate_geometry_fingerprint": candidate.geometry_fingerprint,
            "candidate_connection_id": candidate_set.connection_id,
            "candidate_lineage_fingerprint": _candidate_education_lineage(candidate),
        },
    )


def _education_option_evidence_fingerprint(
    assessment: EducationAccessAssessment,
    option_id: str,
) -> str:
    return _fingerprint(
        {
            "source_content_fingerprint": assessment.source_snapshot.source_content_fingerprint,
            "option_id": option_id,
            "option_evidence": [
                item.model_dump(mode="json")
                for item in assessment.source_snapshot.option_evidence
                if item.option_id == option_id
            ],
        }
    )


def _derive_education_option_bindings(
    assessment: EducationAccessAssessment,
    candidate_set: AlignmentCandidateSet,
) -> tuple[CandidateEducationOptionBinding, ...]:
    bindings = tuple(
        CandidateEducationOptionBinding(
            candidate_id=candidate.candidate_id,
            candidate_geometry_fingerprint=candidate.geometry_fingerprint,
            candidate_connection_id=candidate_set.connection_id,
            candidate_lineage_fingerprint=_candidate_education_lineage(candidate),
            option_id=education_option_id_for_candidate(candidate, candidate_set),
            option_evidence_fingerprint="0" * 64,
        )
        for candidate in candidate_set.admitted_candidates
    )
    if {item.option_id for item in bindings} != set(assessment.source_snapshot.option_ids):
        raise ValueError("education assessment option IDs are not derived from admitted candidates")
    return tuple(
        item.model_copy(
            update={
                "option_evidence_fingerprint": _education_option_evidence_fingerprint(
                    assessment, item.option_id
                )
            }
        )
        for item in bindings
    )


def _education_assessment_fingerprint(assessment: EducationAccessAssessment) -> str:
    validated = EducationAccessAssessment.model_validate(assessment.model_dump(mode="python"))
    if validated != assessment:
        raise ValueError("education assessment is not self-revalidating")
    return validated.assessment_id


def _derive_education_findings(
    assessment: EducationAccessAssessment,
    bindings: tuple[CandidateEducationOptionBinding, ...],
) -> tuple[tuple[CriterionFinding, ...], tuple[IndependentTravelOpportunityFinding, ...]]:
    by_option = {binding.option_id: binding.candidate_id for binding in bindings}
    if set(by_option) != set(assessment.source_snapshot.option_ids):
        raise ValueError("education option bindings do not cover the exact assessment options")
    completeness: list[CriterionFinding] = []
    opportunities: list[IndependentTravelOpportunityFinding] = []
    for option_id, candidate_id in sorted(by_option.items(), key=lambda item: item[1]):
        obligations = tuple(
            item for item in assessment.school_access_obligations if item.option_id == option_id
        )
        destinations = tuple(
            item
            for item in assessment.strategic_education_destination_access
            if item.option_id == option_id
        )
        gaps = tuple(item for item in assessment.network_gaps if item.option_id == option_id)
        unknown = any(
            item.status == AccessServiceStatus.SERVED_PROVISIONAL or item.unknowns
            for item in (*obligations, *destinations)
        ) or any(item.option_id == option_id for item in assessment.school_evidence_requests)
        state = (
            CriterionState.UNSATISFIED
            if gaps
            or any(
                item.status == AccessServiceStatus.NETWORK_GAP
                for item in (*obligations, *destinations)
            )
            else CriterionState.UNKNOWN
            if unknown
            else CriterionState.SATISFIED
        )
        evidence_ids = tuple(
            sorted(
                {
                    *(item.obligation_id for item in obligations),
                    *(item.access_id for item in destinations),
                    *(item.gap_id for item in gaps),
                    *(item.request_id for item in assessment.school_evidence_requests),
                }
            )
        )
        completeness.append(
            CriterionFinding(
                candidate_id=candidate_id,
                state=state,
                detail=CriterionDetail.EDUCATION_COMPLETENESS,
                assessment_id=assessment.assessment_id,
                evidence_record_id=_fingerprint(
                    {"option_id": option_id, "evidence_ids": evidence_ids}
                ),
            )
        )
        option_opportunities = tuple(
            item
            for item in assessment.independent_travel_opportunities
            if item.option_id == option_id
        )
        opportunity_unknown = any(
            item.status != IndependentTravelStatus.EVIDENCE_AVAILABLE or item.unknowns
            for item in option_opportunities
        )
        opportunities.append(
            IndependentTravelOpportunityFinding(
                candidate_id=candidate_id,
                opportunity_count=sum(
                    item.status == IndependentTravelStatus.EVIDENCE_AVAILABLE
                    for item in option_opportunities
                )
                if not opportunity_unknown
                else None,
                state=CriterionState.UNKNOWN if opportunity_unknown else CriterionState.SATISFIED,
                assessment_id=assessment.assessment_id,
                evidence_record_id=_fingerprint(
                    {
                        "option_id": option_id,
                        "opportunity_ids": [item.opportunity_id for item in option_opportunities],
                    }
                ),
            )
        )
    return tuple(completeness), tuple(opportunities)


class GovernedEducationCriterionBinding(BaseModel):
    """Serializable provenance for one exactly scoped education assessment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    school_ids: tuple[str, ...] = ()
    strategic_destination_ids: tuple[str, ...] = ()
    full_source_governed_fingerprint: str = Field(pattern=_SHA256.pattern)
    governed_input_fingerprint: str = Field(pattern=_SHA256.pattern)
    assessment_content_sha256: str = Field(pattern=_SHA256.pattern)
    binding_fingerprint: str = ""

    @field_validator("school_ids", "strategic_destination_ids")
    @classmethod
    def canonical_scope_ids(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        return _canonical_ids(
            value,
            getattr(info, "field_name", "education scope identifiers"),
        )

    @model_validator(mode="after")
    def bind_governed_input(self) -> Self:
        expected_input = governed_education_assessment_fingerprint(
            governed_source_fingerprint=(
                self.full_source_governed_fingerprint
            ),
            school_ids=self.school_ids,
            strategic_destination_ids=self.strategic_destination_ids,
            assessment_content_sha256=self.assessment_content_sha256,
        )
        if self.governed_input_fingerprint != expected_input:
            raise ValueError(
                "education criterion governed input fingerprint is stale"
            )
        payload = self.model_dump(
            mode="json",
            exclude={"binding_fingerprint"},
        )
        expected_binding = _fingerprint(payload)
        if (
            self.binding_fingerprint
            and self.binding_fingerprint != expected_binding
        ):
            raise ValueError(
                "education criterion governed binding fingerprint is stale"
            )
        object.__setattr__(
            self,
            "binding_fingerprint",
            expected_binding,
        )
        return self


class EducationCriterionSummary(BaseModel):
    """Canonical EducationAccessAssessment plus its exact candidate adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assessment: EducationAccessAssessment
    candidate_set: AlignmentCandidateSet
    governed_binding: GovernedEducationCriterionBinding
    option_bindings: tuple[CandidateEducationOptionBinding, ...] = Field(min_length=1)
    completeness: tuple[CriterionFinding, ...] = Field(min_length=1)
    independent_travel_opportunity: tuple[
        IndependentTravelOpportunityFinding,
        ...,
    ] = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    assessment_content_sha256: str = Field(pattern=_SHA256.pattern)
    scenario_evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)

    @classmethod
    def from_assessment(
        cls,
        assessment: EducationAccessAssessment,
        *,
        candidate_set: AlignmentCandidateSet,
        scenario_evidence_snapshot_fingerprint: str,
        governed_binding: GovernedEducationCriterionBinding,
    ) -> EducationCriterionSummary:
        candidate_set = AlignmentCandidateSet.model_validate(
            candidate_set.model_dump(mode="python")
        )
        governed_binding = GovernedEducationCriterionBinding.model_validate(
            governed_binding.model_dump(mode="python")
        )
        option_bindings = _derive_education_option_bindings(assessment, candidate_set)
        _education_assessment_fingerprint(assessment)
        content_fingerprint = _fingerprint(
            assessment.model_dump(mode="json")
        )
        completeness, opportunities = _derive_education_findings(assessment, option_bindings)
        return cls(
            assessment=assessment,
            candidate_set=candidate_set,
            governed_binding=governed_binding,
            option_bindings=option_bindings,
            completeness=completeness,
            independent_travel_opportunity=opportunities,
            assessment_id=assessment.assessment_id,
            assessment_content_sha256=content_fingerprint,
            scenario_evidence_snapshot_fingerprint=scenario_evidence_snapshot_fingerprint,
        )

    @model_validator(mode="after")
    def validate_sections(self) -> Self:
        _education_assessment_fingerprint(self.assessment)
        assessment_content_fingerprint = _fingerprint(
            self.assessment.model_dump(mode="json")
        )
        candidate_set = AlignmentCandidateSet.model_validate(
            self.candidate_set.model_dump(mode="python")
        )
        governed_binding = GovernedEducationCriterionBinding.model_validate(
            self.governed_binding.model_dump(mode="python")
        )
        assessment_scope = (
            tuple(
                item.school_id
                for item in self.assessment.source_snapshot.schools
            ),
            tuple(
                item.strategic_destination_id
                for item in (
                    self.assessment.source_snapshot
                    .strategic_education_destinations
                )
            ),
        )
        if assessment_scope != (
            governed_binding.school_ids,
            governed_binding.strategic_destination_ids,
        ):
            raise ValueError(
                "education criterion governed scope differs from assessment"
            )
        bindings = tuple(sorted(self.option_bindings, key=lambda item: item.candidate_id))
        if len({item.candidate_id for item in bindings}) != len(bindings) or len(
            {item.option_id for item in bindings}
        ) != len(bindings):
            raise ValueError("education option bindings require unique candidates and options")
        expected_bindings = _derive_education_option_bindings(self.assessment, candidate_set)
        if bindings != expected_bindings:
            raise ValueError(
                "education option bindings are not exact candidate geometry/lineage outputs"
            )
        expected_completeness, expected_opportunities = _derive_education_findings(
            self.assessment,
            bindings,
        )
        if (
            self.completeness != expected_completeness
            or self.independent_travel_opportunity != expected_opportunities
        ):
            raise ValueError("education findings are not canonical assessment outputs")
        if (
            self.assessment_id != self.assessment.assessment_id
            or self.assessment_content_sha256
            != assessment_content_fingerprint
            or governed_binding.assessment_content_sha256
            != assessment_content_fingerprint
        ):
            raise ValueError("education assessment binding is stale")
        object.__setattr__(self, "option_bindings", bindings)
        object.__setattr__(self, "candidate_set", candidate_set)
        object.__setattr__(self, "governed_binding", governed_binding)
        for findings in (self.completeness, self.independent_travel_opportunity):
            ids = tuple(item.candidate_id for item in findings)
            if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
                raise ValueError("education findings must be canonically ordered and unique")
        if any(item.detail != CriterionDetail.EDUCATION_COMPLETENESS for item in self.completeness):
            raise ValueError("education completeness requires the typed completeness detail")
        return self


class ExistingAlignmentCriterionSummary(BaseModel):
    """Exact near-equivalence proof and its canonical lexicographic output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proof: NearEquivalenceProof
    comparison: ExistingAlignmentLexicographicComparison
    summary_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_comparison(self) -> Self:
        proof = NearEquivalenceProof.model_validate(self.proof.model_dump(mode="python"))
        comparison = ExistingAlignmentLexicographicComparison.model_validate(
            self.comparison.model_dump(mode="python")
        )
        if (
            comparison.near_equivalence_proof_id != proof.proof_id
            or comparison.near_equivalence_proof_fingerprint != proof.fingerprint
            or comparison.profile_fingerprint != proof.profile_fingerprint
            or comparison.active_objective != proof.active_objective
            or comparison.as_of != proof.as_of
            or (
                comparison.ranked_candidate_ids
                and set(comparison.ranked_candidate_ids) != set(proof.candidate_ids)
            )
        ):
            raise ValueError("existing-alignment comparison is stale for near-equivalence proof")
        advantages = {item.candidate_id: item for item in comparison.advantages}
        for eligibility in proof.eligibility:
            advantage = advantages[eligibility.candidate_id]
            if (
                eligibility.advantage_fingerprint != advantage.fingerprint
                or eligibility.candidate_geometry_fingerprint
                != advantage.candidate_geometry_fingerprint
                or eligibility.evidence_fingerprint != advantage.evidence_fingerprint
            ):
                raise ValueError("near-equivalence eligibility is stale for advantage evidence")
        object.__setattr__(self, "proof", proof)
        object.__setattr__(self, "comparison", comparison)
        payload = self.model_dump(mode="json", exclude={"summary_fingerprint"})
        expected = _fingerprint(payload)
        if self.summary_fingerprint and self.summary_fingerprint != expected:
            raise ValueError("existing-alignment criterion fingerprint is stale")
        object.__setattr__(self, "summary_fingerprint", expected)
        return self


class CandidateCriteria(BaseModel):
    """Separate criterion sections; no aggregate score or overall traffic light."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_snapshot: GovernedEvidenceSnapshot
    population: PopulationCriterionSummary
    education: EducationCriterionSummary
    existing_alignment: ExistingAlignmentCriterionSummary | None = None
    directness: tuple[CriterionFinding, ...] = Field(min_length=1)
    gradient: tuple[CriterionFinding, ...] = Field(min_length=1)
    uncertainty: tuple[CriterionFinding, ...] = Field(min_length=1)
    criteria_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_criteria(self) -> Self:
        population_binding = self.evidence_snapshot.assessment(AssessmentKind.POPULATION_REACH)
        education_binding = self.evidence_snapshot.assessment(AssessmentKind.EDUCATION_ACCESS)
        network_binding = self.evidence_snapshot.assessment(AssessmentKind.NETWORK_GEOMETRY)
        topography_binding = self.evidence_snapshot.assessment(AssessmentKind.TOPOGRAPHY)
        if (
            population_binding is None
            or education_binding is None
            or network_binding is None
            or topography_binding is None
        ):
            raise ValueError("criteria snapshot is missing a required governed assessment")
        if (
            self.population.assessment_id != population_binding.assessment_id
            or self.population.assessment_content_sha256
            != population_binding.assessment_content_sha256
            or self.population.assessment.source.content_sha256
            != population_binding.source_content_sha256
            or self.population.scenario_evidence_snapshot_fingerprint
            != self.evidence_snapshot.snapshot_fingerprint
        ):
            raise ValueError("population section is stale for the governed snapshot")
        if (
            self.education.assessment_id != education_binding.assessment_id
            or self.education.assessment_content_sha256
            != education_binding.assessment_content_sha256
            or self.education.governed_binding.assessment_content_sha256
            != education_binding.assessment_content_sha256
            or self.education.governed_binding.full_source_governed_fingerprint
            != education_binding.source_content_sha256
            or self.education.scenario_evidence_snapshot_fingerprint
            != self.evidence_snapshot.snapshot_fingerprint
        ):
            raise ValueError("education section is stale for the governed snapshot")
        scoped_method = "satn-governed-education-assessment-binding/v3"
        full_source_method = (
            "satn-governed-full-education-assessment-binding/v3"
        )
        if education_binding.method_version not in {
            scoped_method,
            full_source_method,
        }:
            raise ValueError(
                "education section uses an unsupported governed binding method"
            )
        education_scope = self.education.governed_binding
        candidate_set = self.education.candidate_set
        if (
            candidate_set.network_role is NetworkRole.INTERURBAN_SPINE
            and (
                education_scope.school_ids
                or education_scope.strategic_destination_ids
            )
        ):
            raise ValueError(
                "interurban education scope must be exactly empty"
            )
        if (
            candidate_set.network_role
            is NetworkRole.STRATEGIC_DESTINATION_ACCESS
            and (
                education_scope.school_ids
                or education_scope.strategic_destination_ids
                != candidate_set.mandatory_strategic_destination_ids
                or len(
                    candidate_set.mandatory_strategic_destination_ids
                )
                != 1
            )
        ):
            raise ValueError(
                "strategic destination education scope must exactly match "
                "mandatory destinations"
            )
        if any(
            item.assessment_id != education_binding.assessment_id
            for item in (
                *self.education.completeness,
                *self.education.independent_travel_opportunity,
            )
        ):
            raise ValueError("education finding is stale for the governed assessment")
        expected_assessments = (
            (network_binding.assessment_id, self.directness),
            (topography_binding.assessment_id, self.gradient),
            (network_binding.assessment_id, self.uncertainty),
        )
        if any(
            item.assessment_id != assessment_id
            for assessment_id, findings in expected_assessments
            for item in findings
        ):
            raise ValueError("criterion finding is stale for its governed assessment")
        if self.existing_alignment is not None:
            existing = ExistingAlignmentCriterionSummary.model_validate(
                self.existing_alignment.model_dump(mode="python")
            )
            object.__setattr__(self, "existing_alignment", existing)
            existing_binding = self.evidence_snapshot.assessment(AssessmentKind.EXISTING_ALIGNMENT)
            if (
                existing_binding is None
                or existing.proof.proof_id != existing_binding.assessment_id
                or existing.summary_fingerprint != existing_binding.assessment_content_sha256
            ):
                raise ValueError("existing-alignment section is stale for the snapshot")
        for expected_detail, findings in (
            (CriterionDetail.DIRECTNESS_EVIDENCE, self.directness),
            (CriterionDetail.GRADIENT_EVIDENCE, self.gradient),
            (CriterionDetail.UNCERTAINTY_EVIDENCE, self.uncertainty),
        ):
            ids = tuple(item.candidate_id for item in findings)
            if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
                raise ValueError("criterion findings must be canonically ordered and unique")
            if any(item.detail != expected_detail for item in findings):
                raise ValueError("criterion finding uses the wrong typed detail")
            if any(item.state == CriterionState.UNSATISFIED for item in findings):
                raise ValueError(
                    "directness, gradient and uncertainty evidence is available or unknown"
                )
        payload = self.model_dump(mode="json", exclude={"criteria_fingerprint"})
        expected = _fingerprint(payload)
        if self.criteria_fingerprint and self.criteria_fingerprint != expected:
            raise ValueError("criteria_fingerprint is stale")
        object.__setattr__(self, "criteria_fingerprint", expected)
        return self


class CandidateSetGapEvidence(BaseModel):
    """Evidence for an honest gap where no generated candidate was admissible."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_set: AlignmentCandidateSet
    evidence_snapshot: GovernedEvidenceSnapshot
    rejected_candidate_ids: tuple[str, ...] = ()
    unsatisfied_network_place_ids: tuple[str, ...] = ()
    unsatisfied_access_obligation_ids: tuple[str, ...] = ()
    unsatisfied_strategic_destination_ids: tuple[str, ...] = ()
    generation_gap_reason: CandidateGenerationGapReason
    criteria_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_gap_evidence(self) -> Self:
        candidate_set = AlignmentCandidateSet.model_validate(
            self.candidate_set.model_dump(mode="python")
        )
        if candidate_set.admitted_candidates:
            raise ValueError("Candidate Set gap evidence requires zero admitted candidates")
        rejected = tuple(
            item.candidate_id
            for item in candidate_set.admissions
            if item.disposition == AdmissionDisposition.REJECTED
        )
        expected = {
            "rejected_candidate_ids": rejected,
            "unsatisfied_network_place_ids": (candidate_set.mandatory_network_place_ids),
            "unsatisfied_access_obligation_ids": (candidate_set.mandatory_access_obligation_ids),
            "unsatisfied_strategic_destination_ids": (
                candidate_set.mandatory_strategic_destination_ids
            ),
            "generation_gap_reason": candidate_set.generation_gap_reason,
        }
        for field, value in expected.items():
            if getattr(self, field) != value:
                raise ValueError(
                    "Candidate Set gap evidence must preserve exact rejected "
                    "candidates and unsatisfied requirements"
                )
        object.__setattr__(self, "candidate_set", candidate_set)
        payload = self.model_dump(mode="json", exclude={"criteria_fingerprint"})
        fingerprint = _fingerprint(payload)
        if self.criteria_fingerprint and self.criteria_fingerprint != fingerprint:
            raise ValueError("Candidate Set gap evidence fingerprint is stale")
        object.__setattr__(self, "criteria_fingerprint", fingerprint)
        return self


class CandidateComparisonDisposition(BaseModel):
    """Post-admission loser/rejection record with bounded claims."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(pattern=_CANDIDATE_ID.pattern)
    validity: CandidateValidity
    rationale: ComparisonRationale
    change_conditions: tuple[ChangeCondition, ...] = ()

    @field_validator("change_conditions")
    @classmethod
    def canonical_conditions(
        cls,
        value: tuple[ChangeCondition, ...],
    ) -> tuple[ChangeCondition, ...]:
        return tuple(sorted(set(value), key=str))


class MaterialDisplacementRecord(BaseModel):
    """Cited profile rule permitting a lower reuse class to be selected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected_candidate_id: str = Field(pattern=_CANDIDATE_ID.pattern)
    displaced_candidate_id: str = Field(pattern=_CANDIDATE_ID.pattern)
    reason_code: DisplacementReasonCode
    rule_predicate: str = Field(min_length=1)
    observed_values: dict[str, float]
    threshold: float = Field(ge=0, strict=True, allow_inf_nan=False)
    unit: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    decision_provenance: Literal["deterministic-profile"] = "deterministic-profile"
    record_fingerprint: str = ""

    @field_validator("evidence_ids")
    @classmethod
    def canonical_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(value, "material displacement evidence")

    @field_validator("observed_values")
    @classmethod
    def canonical_observed_values(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("material displacement requires observed values")
        return {
            key: _finite(number, f"material displacement {key}")
            for key, number in sorted(value.items())
        }

    @model_validator(mode="after")
    def bind_record(self) -> Self:
        if self.selected_candidate_id == self.displaced_candidate_id:
            raise ValueError("material displacement requires two candidates")
        payload = self.model_dump(mode="json", exclude={"record_fingerprint"})
        expected = _fingerprint(payload)
        if self.record_fingerprint and self.record_fingerprint != expected:
            raise ValueError("material displacement fingerprint is stale")
        object.__setattr__(self, "record_fingerprint", expected)
        return self


class PreferredStrategicAlignment(BaseModel):
    """A candidate-set-bound result; not Reference SATN authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_set: AlignmentCandidateSet
    criteria: CandidateCriteria | CandidateSetGapEvidence
    disposition: SelectionDisposition
    selected_candidate_id: str | None = Field(
        default=None,
        pattern=_CANDIDATE_ID.pattern,
    )
    complementary_candidate_ids: tuple[str, ...] = ()
    admitted_loser_ids: tuple[str, ...] = ()
    precomparison_rejections: tuple[CandidateAdmission, ...] = ()
    comparison_dispositions: tuple[CandidateComparisonDisposition, ...] = ()
    material_displacements: tuple[MaterialDisplacementRecord, ...] = ()
    active_frontier_candidate_ids: tuple[str, ...] = ()
    detected_ambiguity_triggers: tuple[AmbiguityTrigger, ...] = ()
    ambiguity_triggers: tuple[AmbiguityTrigger, ...] = ()
    change_conditions: tuple[ChangeCondition, ...] = ()
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    decision_action: SelectionAction
    publishable: bool = Field(strict=True)
    selection_fingerprint: str = ""

    @property
    def candidate_set_id(self) -> str:
        return self.candidate_set.candidate_set_id

    @property
    def criteria_fingerprint(self) -> str:
        return self.criteria.criteria_fingerprint

    @model_validator(mode="after")
    def bind_result(self) -> Self:
        candidate_set = AlignmentCandidateSet.model_validate(
            self.candidate_set.model_dump(mode="python")
        )
        if isinstance(self.criteria, CandidateSetGapEvidence):
            criteria: CandidateCriteria | CandidateSetGapEvidence = (
                CandidateSetGapEvidence.model_validate(self.criteria.model_dump(mode="python"))
            )
        else:
            criteria = CandidateCriteria.model_validate(self.criteria.model_dump(mode="python"))
        object.__setattr__(self, "candidate_set", candidate_set)
        object.__setattr__(self, "criteria", criteria)
        if (
            isinstance(criteria, CandidateCriteria)
            and criteria.education.candidate_set != candidate_set
        ):
            raise ValueError("education assessment is not bound to this exact Candidate Set")
        if self.profile_fingerprint != candidate_set.profile_fingerprint:
            raise ValueError("selection profile does not match the exact Candidate Set")
        all_ids = {item.candidate_id for item in candidate_set.candidates}
        admitted_ids = {item.candidate_id for item in candidate_set.admitted_candidates}
        if isinstance(criteria, CandidateSetGapEvidence):
            if criteria.candidate_set != candidate_set or admitted_ids:
                raise ValueError(
                    "Candidate Set gap evidence must bind the exact empty admitted set"
                )
        else:
            criterion_sections = (
                criteria.population.headline_500m,
                criteria.population.sensitivity_1000m,
                criteria.education.completeness,
                criteria.education.independent_travel_opportunity,
                criteria.directness,
                criteria.gradient,
                criteria.uncertainty,
            )
            if any(
                {item.candidate_id for item in section} != admitted_ids
                for section in criterion_sections
            ):
                raise ValueError("selection criteria do not cover the exact admitted Candidate Set")
        rejected = tuple(
            item
            for item in candidate_set.admissions
            if item.disposition == AdmissionDisposition.REJECTED
        )
        if self.precomparison_rejections != rejected:
            raise ValueError("precomparison rejections must exactly match Candidate Set admissions")
        winners = {
            item
            for item in (self.selected_candidate_id, *self.complementary_candidate_ids)
            if item is not None
        }
        if not winners.issubset(admitted_ids):
            raise ValueError(
                "selected/complementary candidate must be admitted by this Candidate Set"
            )
        candidate_by_id = {item.candidate_id: item for item in candidate_set.admitted_candidates}
        completeness = (
            {}
            if isinstance(criteria, CandidateSetGapEvidence)
            else _finding_map(criteria.education.completeness)
        )
        if any(
            candidate_by_id[identifier].topology_state == CriterionState.UNSATISFIED
            or completeness[identifier].state == CriterionState.UNSATISFIED
            for identifier in winners
        ):
            raise ValueError("selected/complementary candidate violates an unwaivable hard gate")
        losers = _canonical_ids(
            self.admitted_loser_ids,
            "admitted_loser_ids",
            pattern=_CANDIDATE_ID,
        )
        if set(losers) != admitted_ids - winners:
            raise ValueError("admitted_loser_ids must retain every admitted non-winner")
        comparisons = tuple(
            sorted(self.comparison_dispositions, key=lambda item: item.candidate_id)
        )
        if {item.candidate_id for item in comparisons} != set(losers):
            raise ValueError("every admitted loser requires one comparison disposition")
        if not {item.candidate_id for item in comparisons}.issubset(all_ids):
            raise ValueError("comparison disposition references another Candidate Set")
        complements = _canonical_ids(
            self.complementary_candidate_ids,
            "complementary_candidate_ids",
            pattern=_CANDIDATE_ID,
        )
        derivation = (
            _empty_gap_derivation(candidate_set)
            if isinstance(criteria, CandidateSetGapEvidence)
            else _derive_selection(candidate_set.profile, candidate_set, criteria)
        )
        expected_winner_id = (
            derivation.winner.candidate_id if derivation.winner is not None else None
        )
        if derivation.winner is None:
            expected_selected = None
            expected_complements: tuple[str, ...] = ()
            expected_disposition = SelectionDisposition.NETWORK_GAP
            expected_action = SelectionAction.NETWORK_GAP_REVIEW
            expected_publishable = False
        else:
            expected_selected = expected_winner_id
            expected_complements = ()
            expected_disposition = (
                SelectionDisposition.PROVISIONAL_REVIEW
                if derivation.blocking
                else SelectionDisposition.SELECTED
            )
            expected_action = (
                SelectionAction.PROFILE_FALLBACK
                if derivation.blocking
                else SelectionAction.NO_AGENT_CLEAR
            )
            expected_publishable = not derivation.blocking
        expected_losers = tuple(
            item.candidate_id
            for item in candidate_set.admitted_candidates
            if item.candidate_id != expected_winner_id
        )
        expected_comparisons = _comparison_dispositions(
            candidate_set,
            expected_winner_id,
            derivation.validity,
        )
        frontier = _canonical_ids(
            self.active_frontier_candidate_ids,
            "active_frontier_candidate_ids",
            pattern=_CANDIDATE_ID,
        )
        displacements = tuple(
            sorted(
                self.material_displacements,
                key=lambda item: (item.displaced_candidate_id, item.selected_candidate_id),
            )
        )
        if (
            self.selected_candidate_id != expected_selected
            or complements != expected_complements
            or losers != expected_losers
            or comparisons != expected_comparisons
            or self.disposition != expected_disposition
            or self.decision_action != expected_action
            or self.publishable != expected_publishable
            or self.detected_ambiguity_triggers != derivation.triggers
            or self.ambiguity_triggers != derivation.blocking
            or self.change_conditions != derivation.change_conditions
            or frontier != derivation.active_frontier_candidate_ids
            or displacements != derivation.material_displacements
        ):
            raise ValueError(
                "Preferred Strategic Alignment is not the deterministic profile result"
            )
        if self.disposition == SelectionDisposition.SELECTED:
            if not self.publishable or self.ambiguity_triggers:
                raise ValueError("a selected outcome is publishable and has no ambiguity")
            if self.decision_action != SelectionAction.NO_AGENT_CLEAR:
                raise ValueError("clear deterministic selection records that no agent was invoked")
            if self.selected_candidate_id is None or complements:
                raise ValueError("a resolved Candidate Set selects exactly one candidate")
        elif self.disposition == SelectionDisposition.PROVISIONAL_REVIEW:
            if self.publishable or not self.ambiguity_triggers:
                raise ValueError("a provisional outcome is non-publishable and ambiguous")
            if self.decision_action != SelectionAction.PROFILE_FALLBACK or len(winners) != 1:
                raise ValueError("provisional outcome records exactly one profile fallback")
        else:
            if self.publishable or winners:
                raise ValueError("a Network Gap cannot contain a winner")
            if self.decision_action != SelectionAction.NETWORK_GAP_REVIEW:
                raise ValueError("a Network Gap requires a review action")
        triggers = tuple(
            trigger for trigger in AmbiguityTrigger if trigger in self.ambiguity_triggers
        )
        detected = tuple(
            trigger for trigger in AmbiguityTrigger if trigger in self.detected_ambiguity_triggers
        )
        if len(triggers) != len(self.ambiguity_triggers):
            raise ValueError("ambiguity triggers cannot contain duplicates")
        if len(detected) != len(self.detected_ambiguity_triggers):
            raise ValueError("detected ambiguity triggers cannot contain duplicates")
        object.__setattr__(self, "admitted_loser_ids", losers)
        object.__setattr__(self, "comparison_dispositions", comparisons)
        object.__setattr__(self, "active_frontier_candidate_ids", frontier)
        object.__setattr__(self, "complementary_candidate_ids", complements)
        object.__setattr__(self, "ambiguity_triggers", triggers)
        object.__setattr__(self, "detected_ambiguity_triggers", detected)
        object.__setattr__(self, "change_conditions", derivation.change_conditions)
        payload = self.model_dump(mode="json", exclude={"selection_fingerprint"})
        expected = _fingerprint(payload)
        if self.selection_fingerprint and self.selection_fingerprint != expected:
            raise ValueError("selection fingerprint is stale")
        object.__setattr__(self, "selection_fingerprint", expected)
        return self


def _candidate_covers_set(
    candidate: AlignmentCandidateInput,
    *,
    places: tuple[str, ...],
    obligations: tuple[str, ...],
    destinations: tuple[str, ...],
) -> bool:
    return (
        set(places).issubset(candidate.served_network_place_ids)
        and set(obligations).issubset(candidate.served_access_obligation_ids)
        and set(destinations).issubset(candidate.served_strategic_destination_ids)
    )


def admit_candidate_set(
    profile: NetworkSelectionProfile,
    *,
    network_role: NetworkRole,
    endpoints: tuple[str, str],
    candidates: tuple[AlignmentCandidateInput, ...],
    mandatory_network_place_ids: tuple[str, ...] = (),
    mandatory_access_obligation_ids: tuple[str, ...] = (),
    mandatory_strategic_destination_ids: tuple[str, ...] = (),
) -> AlignmentCandidateSet:
    """Admit a bounded, source-diverse set while preserving every generated input."""
    profile = NetworkSelectionProfile.model_validate(profile.model_dump(mode="json"))
    generated = tuple(
        sorted(
            (
                AlignmentCandidateInput.model_validate(item.model_dump(mode="json"))
                for item in candidates
            ),
            key=lambda item: item.candidate_id,
        )
    )
    geometry_profiles = {item.geometry.equivalence_profile.fingerprint for item in generated}
    if len(geometry_profiles) > 1:
        raise ValueError(
            "generated candidates must share one governed geometry-equivalence profile"
        )
    admissions = _derive_admissions(
        generated,
        precedence_order=profile.candidate_source_precedence or (),
        class_order=profile.candidate_class_order,
        intervention_order=profile.intervention_state_order,
        maximum_options=(
            profile.maximum_options_per_candidate_set
            if profile.contract == "satn-network-selection-profile/vNext"
            else profile.ambiguity.maximum_options_per_candidate_set
        ),
        mandatory_network_place_ids=mandatory_network_place_ids,
        mandatory_access_obligation_ids=mandatory_access_obligation_ids,
        mandatory_strategic_destination_ids=mandatory_strategic_destination_ids,
    )
    return AlignmentCandidateSet(
        network_role=network_role,
        endpoints=endpoints,
        mandatory_network_place_ids=mandatory_network_place_ids,
        mandatory_access_obligation_ids=mandatory_access_obligation_ids,
        mandatory_strategic_destination_ids=mandatory_strategic_destination_ids,
        profile=profile,
        profile_fingerprint=profile.fingerprint,
        candidate_source_precedence=profile.candidate_source_precedence or (),
        candidate_class_order=profile.candidate_class_order,
        maximum_options=(
            profile.maximum_options_per_candidate_set
            if profile.contract == "satn-network-selection-profile/vNext"
            else profile.ambiguity.maximum_options_per_candidate_set
        ),
        geometry_equivalence_profile=(
            generated[0].geometry.equivalence_profile
            if generated
            else MaterialGeometryEquivalenceProfile()
        ),
        candidates=generated,
        admissions=admissions,
        generation_gap_reason=(
            CandidateGenerationGapReason.NONE
            if any(item.disposition == AdmissionDisposition.ADMITTED for item in admissions)
            else (
                CandidateGenerationGapReason.NO_GENERATED_CANDIDATES
                if not generated
                else CandidateGenerationGapReason.ALL_GENERATED_CANDIDATES_REJECTED
            )
        ),
    )


def _finding_map[T: _HasCandidateId](
    findings: tuple[T, ...],
) -> dict[str, T]:
    return {item.candidate_id: item for item in findings}


def _validate_criteria(
    profile: NetworkSelectionProfile,
    candidate_set: AlignmentCandidateSet,
    criteria: CandidateCriteria,
) -> None:
    if candidate_set.profile_fingerprint != profile.fingerprint:
        raise ValueError("Candidate Set is stale for the active Network Selection Profile")
    admitted_ids = {item.candidate_id for item in candidate_set.admitted_candidates}
    sections = (
        criteria.population.headline_500m,
        criteria.population.sensitivity_1000m,
        criteria.education.completeness,
        criteria.education.independent_travel_opportunity,
        criteria.directness,
        criteria.gradient,
        criteria.uncertainty,
    )
    for section in sections:
        if {item.candidate_id for item in section} != admitted_ids:
            raise ValueError("every criterion section must cover exactly the admitted candidates")
    candidate_by_id = {item.candidate_id: item for item in candidate_set.admitted_candidates}
    population_profile = criteria.population.assessment.profile
    expected_distances = (
        float(profile.population.headline_radius_m),
        float(profile.population.sensitivity_radius_m),
    )
    if population_profile.corridor_distances_m != expected_distances:
        raise ValueError("population assessment radii are stale for selection profile")
    if population_profile.comparison_tolerance_residents != 0:
        raise ValueError("population resident tolerance is not authorised by selection profile")
    if (
        population_profile.comparison_tolerance_percent
        != profile.population.near_equivalent_tolerance_pct
    ):
        raise ValueError("population assessment tolerance is stale for selection profile")
    bindings_by_candidate = {
        item.candidate_id: item for item in criteria.population.option_bindings
    }
    if set(bindings_by_candidate) != admitted_ids:
        raise ValueError("population option bindings must cover admitted candidates")
    for candidate_id, candidate in candidate_by_id.items():
        binding = bindings_by_candidate[candidate_id]
        if binding.assessment_geometry_sha256 != candidate.geometry.population_geometry_sha256:
            raise ValueError("population option is stale for candidate geometry")
    gradient = _finding_map(criteria.gradient)
    for candidate in candidate_set.admitted_candidates:
        gradient_state = gradient[candidate.candidate_id].state
        if (
            candidate.maximum_gradient_pct is None and gradient_state != CriterionState.UNKNOWN
        ) or (
            candidate.maximum_gradient_pct is not None
            and gradient_state == CriterionState.UNSATISFIED
        ):
            raise ValueError("gradient evidence state is inconsistent with candidate evidence")
    if criteria.existing_alignment is None:
        return
    comparison = criteria.existing_alignment.comparison
    if (
        comparison.profile_fingerprint != profile.fingerprint
        or comparison.active_objective != profile.primary_objective
    ):
        raise ValueError("existing-alignment comparison is stale for profile/objective")
    completeness = _finding_map(criteria.education.completeness)
    population = _finding_map(criteria.population.headline_500m)
    education = _finding_map(criteria.education.independent_travel_opportunity)
    advantage_by_candidate = {item.candidate_id: item for item in comparison.advantages}
    comparison_ids = set(advantage_by_candidate)
    if not comparison_ids.issubset(admitted_ids):
        raise ValueError("existing-alignment comparison references another Candidate Set")
    proof = criteria.existing_alignment.proof
    try:
        expected_proof = _derive_expected_near_equivalence_proof(
            profile,
            candidate_set,
            criteria,
            comparison.advantages,
            proof_id=proof.proof_id,
            as_of=proof.as_of,
        )
    except ValueError as error:
        raise ValueError(
            "near-equivalence proof is stale for current selection criteria"
        ) from error
    if proof != expected_proof:
        raise ValueError("near-equivalence proof is stale for current selection criteria")
    for advantage in comparison.advantages:
        candidate = candidate_by_id.get(advantage.candidate_id)
        if candidate is None:
            raise ValueError("existing-alignment advantage references another Candidate Set")
        if (
            advantage.candidate_geometry_fingerprint
            != candidate.geometry.existing_alignment_geometry_fingerprint
            or advantage.directness != candidate.directness_m
        ):
            raise ValueError("existing-alignment advantage is stale for candidate geometry")
    if not comparison_ids.issubset(
        set(population)
        if profile.primary_objective == AlignmentSelectionObjective.POPULATION_REACH
        else set(education)
    ) or not comparison_ids.issubset(completeness):
        raise ValueError("existing-alignment comparison lacks active criterion evidence")


def _population_contenders(
    candidates: tuple[AlignmentCandidateInput, ...],
    findings: tuple[PopulationReachFinding, ...],
) -> tuple[tuple[AlignmentCandidateInput, ...], bool]:
    by_id = _finding_map(findings)
    if any(by_id[item.candidate_id].state == CriterionState.UNKNOWN for item in candidates):
        return candidates, True
    contenders = tuple(
        item
        for item in candidates
        if by_id[item.candidate_id].rank == 1 or by_id[item.candidate_id].near_equivalent
    )
    sensitivity = any(
        by_id[item.candidate_id].decisive_borderline_oa_ids
        or by_id[item.candidate_id].current_development_omission
        for item in candidates
    )
    return contenders, sensitivity


def _education_contenders(
    candidates: tuple[AlignmentCandidateInput, ...],
    findings: tuple[IndependentTravelOpportunityFinding, ...],
) -> tuple[tuple[AlignmentCandidateInput, ...], bool]:
    by_id = _finding_map(findings)
    if any(by_id[item.candidate_id].state == CriterionState.UNKNOWN for item in candidates):
        return candidates, True
    counts = {
        item.candidate_id: by_id[item.candidate_id].opportunity_count or 0 for item in candidates
    }
    highest = max(counts.values())
    return (
        tuple(item for item in candidates if counts[item.candidate_id] == highest),
        False,
    )


def _derive_expected_near_equivalence_proof(
    profile: NetworkSelectionProfile,
    candidate_set: AlignmentCandidateSet,
    criteria: CandidateCriteria,
    advantages: tuple[ExistingAlignmentAdvantage, ...],
    *,
    proof_id: str,
    as_of: date,
) -> NearEquivalenceProof:
    candidate_by_id = {item.candidate_id: item for item in candidate_set.admitted_candidates}
    ordered_advantages = tuple(sorted(advantages, key=lambda item: item.candidate_id))
    candidate_ids = tuple(item.candidate_id for item in ordered_advantages)
    if (
        len(candidate_ids) < 2
        or len(set(candidate_ids)) != len(candidate_ids)
        or not set(candidate_ids).issubset(candidate_by_id)
    ):
        raise ValueError("near-equivalence proof requires unique admitted candidates")
    completeness = _finding_map(criteria.education.completeness)
    population_500 = _finding_map(criteria.population.headline_500m)
    population_1000 = _finding_map(criteria.population.sensitivity_1000m)
    education = _finding_map(criteria.education.independent_travel_opportunity)
    proof_candidates = tuple(candidate_by_id[candidate_id] for candidate_id in candidate_ids)
    if any(
        candidate.topology_state != CriterionState.SATISFIED
        or completeness[candidate.candidate_id].state != CriterionState.SATISFIED
        for candidate in proof_candidates
    ):
        raise ValueError("near-equivalence proof candidates must pass exact mandatory gates")
    if profile.primary_objective == AlignmentSelectionObjective.POPULATION_REACH:
        objective_contenders, missing_objective = _population_contenders(
            proof_candidates,
            criteria.population.headline_500m,
        )
    else:
        objective_contenders, missing_objective = _education_contenders(
            proof_candidates,
            criteria.education.independent_travel_opportunity,
        )
    if missing_objective or {item.candidate_id for item in objective_contenders} != set(
        candidate_ids
    ):
        raise ValueError(
            "near-equivalence proof candidates are not exact active-objective contenders"
        )
    topology_fingerprints = {
        candidate_id: _fingerprint(
            {
                "method_version": "mandatory-topology-binding/v1",
                "candidate_id": candidate_id,
                "topology_state": candidate_by_id[candidate_id].topology_state,
                "geometry_fingerprint": candidate_by_id[candidate_id].geometry_fingerprint,
            }
        )
        for candidate_id in candidate_ids
    }
    education_fingerprints = {
        candidate_id: _fingerprint(completeness[candidate_id].model_dump(mode="json"))
        for candidate_id in candidate_ids
    }
    if profile.primary_objective == AlignmentSelectionObjective.POPULATION_REACH:
        objective_payloads = {
            candidate_id: {
                "headline_500m": population_500[candidate_id].model_dump(mode="json"),
                "sensitivity_1000m": population_1000[candidate_id].model_dump(mode="json"),
            }
            for candidate_id in candidate_ids
        }
        objective_profile: object = profile.population.model_dump(mode="json")
    else:
        objective_payloads = {
            candidate_id: education[candidate_id].model_dump(mode="json")
            for candidate_id in candidate_ids
        }
        objective_profile = profile.education.model_dump(mode="json")
    objective_fingerprints = {
        candidate_id: _fingerprint(objective_payloads[candidate_id])
        for candidate_id in candidate_ids
    }
    near_profile_fingerprint = _fingerprint(
        {
            "method_version": "selection-near-equivalence-profile/v1",
            "network_selection_profile_fingerprint": profile.fingerprint,
            "active_objective": profile.primary_objective,
            "objective_profile": objective_profile,
        }
    )
    calculation_fingerprint = _fingerprint(
        {
            "method_version": "selection-near-equivalence-calculation/v1",
            "candidate_ids": candidate_ids,
            "topology_fingerprints": topology_fingerprints,
            "education_completeness_fingerprints": (education_fingerprints),
            "active_objective_evidence": objective_payloads,
            "near_equivalence_profile_fingerprint": (near_profile_fingerprint),
        }
    )
    advantage_by_id = {item.candidate_id: item for item in ordered_advantages}
    eligibility = tuple(
        CandidateEligibilityProof(
            candidate_id=candidate_id,
            advantage_fingerprint=advantage_by_id[candidate_id].fingerprint,
            candidate_geometry_fingerprint=advantage_by_id[
                candidate_id
            ].candidate_geometry_fingerprint,
            evidence_fingerprint=advantage_by_id[candidate_id].evidence_fingerprint,
            mandatory_validity_topology_fingerprint=(topology_fingerprints[candidate_id]),
            education_completeness_fingerprint=(education_fingerprints[candidate_id]),
            active_objective_evidence_fingerprint=(objective_fingerprints[candidate_id]),
            near_equivalence_calculation_fingerprint=(calculation_fingerprint),
            near_equivalence_profile_fingerprint=(near_profile_fingerprint),
        )
        for candidate_id in candidate_ids
    )
    return NearEquivalenceProof(
        proof_id=proof_id,
        as_of=as_of,
        profile_fingerprint=profile.fingerprint,
        active_objective=profile.primary_objective,
        near_equivalence_calculation_fingerprint=(calculation_fingerprint),
        near_equivalence_profile_fingerprint=near_profile_fingerprint,
        candidate_ids=candidate_ids,
        eligibility=eligibility,
        near_equivalent_after_mandatory_gates=True,
    )


def build_existing_alignment_near_equivalence_proof(
    profile: NetworkSelectionProfile,
    candidate_set: AlignmentCandidateSet,
    criteria: CandidateCriteria,
    advantages: tuple[ExistingAlignmentAdvantage, ...],
    *,
    proof_id: str,
    as_of: date,
) -> NearEquivalenceProof:
    """Bind a comparison proof to the exact current selection evidence."""
    profile = NetworkSelectionProfile.model_validate(profile.model_dump(mode="json"))
    candidate_set = AlignmentCandidateSet.model_validate(candidate_set.model_dump(mode="python"))
    criteria = CandidateCriteria.model_validate(criteria.model_dump(mode="python"))
    if criteria.existing_alignment is not None:
        raise ValueError("build the near-equivalence proof before attaching existing alignment")
    _validate_criteria(profile, candidate_set, criteria)
    return _derive_expected_near_equivalence_proof(
        profile,
        candidate_set,
        criteria,
        advantages,
        proof_id=proof_id,
        as_of=as_of,
    )


def _existing_alignment_contenders(
    candidates: tuple[AlignmentCandidateInput, ...],
    criteria: CandidateCriteria,
) -> tuple[tuple[AlignmentCandidateInput, ...], bool]:
    if len(candidates) <= 1 or criteria.existing_alignment is None:
        return candidates, False
    candidate_by_id = {item.candidate_id: item for item in candidates}
    comparison = criteria.existing_alignment.comparison
    if not set(candidate_by_id).issubset(comparison.ranked_candidate_ids):
        raise ValueError("existing-alignment comparison must cover the active frontier")
    values = {item.candidate_id: item for item in comparison.comparison_values}
    highest_reusable_share = max(
        values[item.candidate_id].reusable_asset_share for item in candidates
    )
    contenders = tuple(
        item
        for item in candidates
        if values[item.candidate_id].reusable_asset_share == highest_reusable_share
    )
    if len(contenders) <= 1:
        return contenders, False
    highest_recognised_share = max(
        values[item.candidate_id].recognised_current_share for item in contenders
    )
    contenders = tuple(
        item
        for item in contenders
        if values[item.candidate_id].recognised_current_share == highest_recognised_share
    )
    if len(contenders) <= 1:
        return contenders, False
    directness = _finding_map(criteria.directness)
    if any(directness[item.candidate_id].state != CriterionState.SATISFIED for item in contenders):
        return contenders, True
    minimum_directness = min(values[item.candidate_id].directness for item in contenders)
    return (
        tuple(
            item
            for item in contenders
            if values[item.candidate_id].directness == minimum_directness
        ),
        False,
    )


def _source_precedence_contenders(
    profile: NetworkSelectionProfile,
    candidates: tuple[AlignmentCandidateInput, ...],
) -> tuple[AlignmentCandidateInput, ...]:
    if len(candidates) <= 1:
        return candidates
    precedence = {source: index for index, source in enumerate(profile.candidate_source_precedence)}
    best_precedence = min(precedence[item.source_class] for item in candidates)
    return tuple(item for item in candidates if precedence[item.source_class] == best_precedence)


def _comparison_dispositions(
    candidate_set: AlignmentCandidateSet,
    winner_id: str | None,
    validity: dict[str, CandidateValidity],
) -> tuple[CandidateComparisonDisposition, ...]:
    records: list[CandidateComparisonDisposition] = []
    for candidate in candidate_set.admitted_candidates:
        if candidate.candidate_id == winner_id:
            continue
        state = validity[candidate.candidate_id]
        if state == CandidateValidity.INVALID_TOPOLOGY:
            rationale = ComparisonRationale.INVALID_TOPOLOGY
            conditions = (ChangeCondition.TOPOLOGY_REPAIRED,)
        elif state == CandidateValidity.EDUCATION_INCOMPLETE:
            rationale = ComparisonRationale.EDUCATION_INCOMPLETE
            conditions = (ChangeCondition.EDUCATION_COMPLETENESS_CHANGES,)
        elif state == CandidateValidity.UNKNOWN_HARD_GATE:
            rationale = ComparisonRationale.UNKNOWN_HARD_GATE
            conditions = (
                ChangeCondition.TOPOLOGY_REPAIRED,
                ChangeCondition.EDUCATION_COMPLETENESS_CHANGES,
            )
        elif winner_id is None:
            rationale = ComparisonRationale.NETWORK_GAP
            conditions = (ChangeCondition.EVIDENCE_CHANGES,)
        else:
            rationale = ComparisonRationale.NOT_PREFERRED
            conditions = (
                ChangeCondition.EVIDENCE_CHANGES,
                ChangeCondition.PROFILE_CHANGES,
            )
        records.append(
            CandidateComparisonDisposition(
                candidate_id=candidate.candidate_id,
                validity=state,
                rationale=rationale,
                change_conditions=conditions,
            )
        )
    return tuple(sorted(records, key=lambda item: item.candidate_id))


@dataclass(frozen=True)
class _SelectionDerivation:
    winner: AlignmentCandidateInput | None
    validity: dict[str, CandidateValidity]
    triggers: tuple[AmbiguityTrigger, ...]
    blocking: tuple[AmbiguityTrigger, ...]
    hard_gate_unknown: bool
    change_conditions: tuple[ChangeCondition, ...]
    active_frontier_candidate_ids: tuple[str, ...]
    material_displacements: tuple[MaterialDisplacementRecord, ...] = ()


def _empty_gap_derivation(
    candidate_set: AlignmentCandidateSet,
) -> _SelectionDerivation:
    if candidate_set.admitted_candidates:
        raise ValueError("empty gap derivation requires zero admitted candidates")
    return _SelectionDerivation(
        winner=None,
        validity={},
        triggers=(),
        blocking=(),
        hard_gate_unknown=False,
        change_conditions=(
            ChangeCondition.EVIDENCE_CHANGES,
            ChangeCondition.ROLE_COVERAGE_CHANGES,
        ),
        active_frontier_candidate_ids=(),
    )


def _reuse_first_sort_key(
    profile: NetworkSelectionProfile,
    candidate: AlignmentCandidateInput,
) -> tuple[tuple[int, object], ...]:
    """Build the configured vNext lexicographic key without inventing unknowns."""
    assert profile.candidate_class_order is not None
    assert profile.intervention_state_order is not None
    assert profile.comparator_order is not None
    assert candidate.reuse_class is not None
    assert candidate.intervention_state is not None
    class_rank = {
        value: index for index, value in enumerate(profile.candidate_class_order)
    }
    intervention_rank = {
        value: index for index, value in enumerate(profile.intervention_state_order)
    }
    values: dict[object, tuple[int, object]] = {
        "mandatory-obligation-service": (0, 0),
        "reuse-class": (0, class_rank[candidate.reuse_class]),
        "intervention-state": (0, intervention_rank[candidate.intervention_state]),
        "route-length": (0, candidate.directness_m),
        "route-detour": (0, candidate.directness_m),
        "route-effort": (
            (1, 0.0)
            if candidate.total_absolute_elevation_change_m is None
            else (0, candidate.total_absolute_elevation_change_m)
        ),
        "transition-fragmentation-burden": (
            (1, 0)
            if candidate.transition_count is None or candidate.fragmentation_count is None
            else (0, candidate.transition_count + candidate.fragmentation_count)
        ),
        # Constraint and traffic facts are added by their governed evidence slices.
        # Until present, all candidates remain equally unknown rather than favourable.
        "governed-constraints": (1, 0),
        "traffic-challenge": (1, 0),
        "stable-candidate-id": (0, candidate.candidate_id),
    }
    return tuple(values[str(dimension)] for dimension in profile.comparator_order)


def _derive_reuse_first_selection(
    profile: NetworkSelectionProfile,
    considered: tuple[AlignmentCandidateInput, ...],
    validity: dict[str, CandidateValidity],
    *,
    hard_gate_unknown: bool,
) -> _SelectionDerivation:
    winner = min(considered, key=lambda item: _reuse_first_sort_key(profile, item))
    material_displacements: tuple[MaterialDisplacementRecord, ...] = ()
    detour_rule = next(
        (
            rule
            for rule in (profile.displacement_rules or ())
            if rule.reason_code is DisplacementReasonCode.DETOUR_LIMIT_EXCEEDED
            and rule.predicate == "detour-ratio-exceeds-threshold"
            and rule.threshold is not None
            and rule.unit == "ratio"
        ),
        None,
    )
    if detour_rule is not None:
        assert profile.candidate_class_order is not None
        class_rank = {
            value: index for index, value in enumerate(profile.candidate_class_order)
        }
        highest_rank = min(class_rank[item.reuse_class] for item in considered)
        highest = tuple(
            item for item in considered if class_rank[item.reuse_class] == highest_rank
        )
        displaced = min(highest, key=lambda item: _reuse_first_sort_key(profile, item))
        lower = tuple(
            item for item in considered if class_rank[item.reuse_class] > highest_rank
        )
        selected_lower = min(
            lower,
            key=lambda item: (item.directness_m, _reuse_first_sort_key(profile, item)),
            default=None,
        )
        if selected_lower is not None and selected_lower.directness_m > 0:
            ratio = displaced.directness_m / selected_lower.directness_m
            if ratio > float(detour_rule.threshold):
                winner = selected_lower
                material_displacements = (
                    MaterialDisplacementRecord(
                        selected_candidate_id=selected_lower.candidate_id,
                        displaced_candidate_id=displaced.candidate_id,
                        reason_code=detour_rule.reason_code,
                        rule_predicate=detour_rule.predicate,
                        observed_values={
                            "displaced_route_length_m": displaced.directness_m,
                            "selected_route_length_m": selected_lower.directness_m,
                            "detour_ratio": ratio,
                        },
                        threshold=float(detour_rule.threshold),
                        unit=detour_rule.unit,
                        evidence_ids=tuple(
                            sorted(
                                {
                                    *displaced.governed_evidence_ids,
                                    *selected_lower.governed_evidence_ids,
                                }
                            )
                        ),
                        profile_fingerprint=profile.fingerprint,
                    ),
                )
    triggers = (
        (AmbiguityTrigger.MATERIAL_GREY_EVIDENCE,) if hard_gate_unknown else ()
    )
    conditions = {
        ChangeCondition.EVIDENCE_CHANGES,
        ChangeCondition.PROFILE_CHANGES,
        ChangeCondition.WINNER_TOPOLOGY_INVALIDATED,
        ChangeCondition.WINNER_EDUCATION_INVALIDATED,
    }
    if hard_gate_unknown:
        conditions.update(
            {
                ChangeCondition.TOPOLOGY_REPAIRED,
                ChangeCondition.EDUCATION_COMPLETENESS_CHANGES,
            }
        )
    return _SelectionDerivation(
        winner=winner,
        validity=validity,
        triggers=triggers,
        blocking=(),
        hard_gate_unknown=hard_gate_unknown,
        change_conditions=tuple(sorted(conditions, key=str)),
        active_frontier_candidate_ids=tuple(
            sorted(item.candidate_id for item in considered)
        ),
        material_displacements=material_displacements,
    )


def _derive_selection(
    profile: NetworkSelectionProfile,
    candidate_set: AlignmentCandidateSet,
    criteria: CandidateCriteria,
) -> _SelectionDerivation:
    _validate_criteria(profile, candidate_set, criteria)
    admitted = candidate_set.admitted_candidates
    completeness = _finding_map(criteria.education.completeness)
    validity: dict[str, CandidateValidity] = {}
    for candidate in admitted:
        completeness_state = completeness[candidate.candidate_id].state
        if (
            candidate.topology_state == CriterionState.UNKNOWN
            or completeness_state == CriterionState.UNKNOWN
        ):
            validity[candidate.candidate_id] = CandidateValidity.UNKNOWN_HARD_GATE
        elif candidate.topology_state == CriterionState.UNSATISFIED:
            validity[candidate.candidate_id] = CandidateValidity.INVALID_TOPOLOGY
        elif completeness_state == CriterionState.UNSATISFIED:
            validity[candidate.candidate_id] = CandidateValidity.EDUCATION_INCOMPLETE
        else:
            validity[candidate.candidate_id] = CandidateValidity.VALID
    viable = tuple(
        item for item in admitted if validity[item.candidate_id] == CandidateValidity.VALID
    )
    grey = tuple(
        item
        for item in admitted
        if validity[item.candidate_id] == CandidateValidity.UNKNOWN_HARD_GATE
    )
    if not viable and not grey:
        return _SelectionDerivation(
            winner=None,
            validity=validity,
            triggers=(),
            blocking=(),
            hard_gate_unknown=False,
            change_conditions=(
                ChangeCondition.EVIDENCE_CHANGES,
                ChangeCondition.TOPOLOGY_REPAIRED,
                ChangeCondition.EDUCATION_COMPLETENESS_CHANGES,
            ),
            active_frontier_candidate_ids=(),
        )
    hard_gate_unknown = bool(grey)
    considered = (
        tuple((*viable, *grey))
        if profile.contract == "satn-network-selection-profile/vNext"
        else (viable or grey)
    )
    if profile.contract == "satn-network-selection-profile/vNext":
        return _derive_reuse_first_selection(
            profile,
            considered,
            validity,
            hard_gate_unknown=hard_gate_unknown,
        )

    triggers: set[AmbiguityTrigger] = set()
    selection_grey = hard_gate_unknown
    population_500, missing_population_500 = _population_contenders(
        considered,
        criteria.population.headline_500m,
    )
    _, missing_population_1000 = _population_contenders(
        considered,
        criteria.population.sensitivity_1000m,
    )
    education, missing_education = _education_contenders(
        considered,
        criteria.education.independent_travel_opportunity,
    )
    rank_500 = tuple(
        item.candidate_id
        for item in sorted(
            criteria.population.headline_500m,
            key=lambda finding: (
                finding.rank is None,
                finding.rank or 0,
                finding.candidate_id,
            ),
        )
        if item.candidate_id in {candidate.candidate_id for candidate in considered}
    )
    rank_1000 = tuple(
        item.candidate_id
        for item in sorted(
            criteria.population.sensitivity_1000m,
            key=lambda finding: (
                finding.rank is None,
                finding.rank or 0,
                finding.candidate_id,
            ),
        )
        if item.candidate_id in {candidate.candidate_id for candidate in considered}
    )
    if rank_500 != rank_1000:
        triggers.add(AmbiguityTrigger.HEADLINE_AND_SENSITIVITY_ORDER_DIFFER)
    if (
        len(population_500) == 1
        and len(education) == 1
        and population_500[0].candidate_id != education[0].candidate_id
    ):
        triggers.add(AmbiguityTrigger.OBJECTIVE_SECTIONS_CONFLICT_MATERIALLY)
    if _role_disposition(candidate_set.network_role) == CandidateSetDisposition.UNCERTAIN:
        triggers.add(AmbiguityTrigger.SUBSTITUTE_COMPLEMENTARY_UNCERTAIN)

    evidence_sections = (
        criteria.directness,
        criteria.gradient,
        criteria.uncertainty,
        criteria.education.completeness,
    )
    missing_general = any(
        item.state == CriterionState.UNKNOWN for section in evidence_sections for item in section
    )
    if (
        missing_population_500
        or missing_population_1000
        or missing_education
        or missing_general
        or hard_gate_unknown
    ):
        triggers.add(AmbiguityTrigger.MATERIAL_GREY_EVIDENCE)

    contenders = (
        population_500
        if profile.primary_objective == AlignmentSelectionObjective.POPULATION_REACH
        else education
    )
    contenders = _source_precedence_contenders(profile, contenders)
    contenders, existing_directness_unknown = _existing_alignment_contenders(
        contenders,
        criteria,
    )
    if existing_directness_unknown:
        selection_grey = True
        triggers.add(AmbiguityTrigger.MATERIAL_GREY_EVIDENCE)
    if len(contenders) > 1:
        directness = _finding_map(criteria.directness)
        if any(
            directness[item.candidate_id].state != CriterionState.SATISFIED for item in contenders
        ):
            selection_grey = True
            triggers.add(AmbiguityTrigger.MATERIAL_GREY_EVIDENCE)
        else:
            minimum_directness = min(item.directness_m for item in contenders)
            contenders = tuple(
                item for item in contenders if item.directness_m == minimum_directness
            )
    if len(contenders) > 1 and not selection_grey:
        gradient = _finding_map(criteria.gradient)
        if any(
            gradient[item.candidate_id].state != CriterionState.SATISFIED
            or item.maximum_gradient_pct is None
            for item in contenders
        ):
            selection_grey = True
            triggers.add(AmbiguityTrigger.MATERIAL_GREY_EVIDENCE)
        else:
            minimum_gradient = min(
                item.maximum_gradient_pct
                for item in contenders
                if item.maximum_gradient_pct is not None
            )
            contenders = tuple(
                item for item in contenders if item.maximum_gradient_pct == minimum_gradient
            )
    if not contenders:
        contenders = considered
        selection_grey = True
        triggers.add(AmbiguityTrigger.MATERIAL_GREY_EVIDENCE)
    if len(contenders) > 1:
        triggers.add(AmbiguityTrigger.NEAR_EQUIVALENT_OPTIONS)
    winner = min(contenders, key=lambda item: item.candidate_id)
    ordered_triggers = tuple(trigger for trigger in AmbiguityTrigger if trigger in triggers)
    blocking = tuple(trigger for trigger in profile.ambiguity.review_when if trigger in triggers)
    if selection_grey and AmbiguityTrigger.MATERIAL_GREY_EVIDENCE not in blocking:
        blocking = tuple(
            trigger
            for trigger in AmbiguityTrigger
            if trigger
            in {
                *blocking,
                AmbiguityTrigger.MATERIAL_GREY_EVIDENCE,
            }
        )
    conditions: set[ChangeCondition] = {
        ChangeCondition.EVIDENCE_CHANGES,
        ChangeCondition.PROFILE_CHANGES,
        ChangeCondition.WINNER_TOPOLOGY_INVALIDATED,
        ChangeCondition.WINNER_EDUCATION_INVALIDATED,
    }
    if AmbiguityTrigger.HEADLINE_AND_SENSITIVITY_ORDER_DIFFER in triggers:
        conditions.add(ChangeCondition.POPULATION_RANKING_CHANGES)
    population_findings = (
        *criteria.population.headline_500m,
        *criteria.population.sensitivity_1000m,
    )
    if any(item.decisive_borderline_oa_ids for item in population_findings):
        conditions.add(ChangeCondition.BORDERLINE_OA_RESOLUTION_CHANGES)
    if any(item.current_development_omission for item in population_findings):
        conditions.add(ChangeCondition.CURRENT_DEVELOPMENT_EVIDENCE_CHANGES)
    if blocking:
        conditions.add(ChangeCondition.AMBIGUITY_RESOLVED)
    if hard_gate_unknown:
        conditions.update(
            {
                ChangeCondition.TOPOLOGY_REPAIRED,
                ChangeCondition.EDUCATION_COMPLETENESS_CHANGES,
            }
        )
    return _SelectionDerivation(
        winner=winner,
        validity=validity,
        triggers=ordered_triggers,
        blocking=blocking,
        hard_gate_unknown=hard_gate_unknown,
        change_conditions=tuple(sorted(conditions, key=str)),
        active_frontier_candidate_ids=tuple(sorted(item.candidate_id for item in contenders)),
    )


def select_preferred_alignment(
    profile: NetworkSelectionProfile,
    candidate_set: AlignmentCandidateSet,
    criteria: CandidateCriteria | CandidateSetGapEvidence,
) -> PreferredStrategicAlignment:
    """Apply hard gates and the configured deterministic decision hierarchy."""
    profile = NetworkSelectionProfile.model_validate(profile.model_dump(mode="json"))
    candidate_set = AlignmentCandidateSet.model_validate(candidate_set.model_dump(mode="python"))
    if isinstance(criteria, CandidateSetGapEvidence):
        criteria = CandidateSetGapEvidence.model_validate(criteria.model_dump(mode="python"))
        derivation = _empty_gap_derivation(candidate_set)
    else:
        criteria = CandidateCriteria.model_validate(criteria.model_dump(mode="python"))
        derivation = _derive_selection(profile, candidate_set, criteria)
    admitted = candidate_set.admitted_candidates
    rejections = tuple(
        item
        for item in candidate_set.admissions
        if item.disposition == AdmissionDisposition.REJECTED
    )
    if derivation.winner is None:
        comparisons = _comparison_dispositions(
            candidate_set,
            None,
            derivation.validity,
        )
        return PreferredStrategicAlignment(
            candidate_set=candidate_set,
            criteria=criteria,
            disposition=SelectionDisposition.NETWORK_GAP,
            admitted_loser_ids=tuple(item.candidate_id for item in admitted),
            precomparison_rejections=rejections,
            comparison_dispositions=comparisons,
            active_frontier_candidate_ids=derivation.active_frontier_candidate_ids,
            detected_ambiguity_triggers=derivation.triggers,
            change_conditions=derivation.change_conditions,
            profile_fingerprint=profile.fingerprint,
            decision_action=SelectionAction.NETWORK_GAP_REVIEW,
            publishable=False,
        )
    winner = derivation.winner
    comparisons = _comparison_dispositions(
        candidate_set,
        winner.candidate_id,
        derivation.validity,
    )
    losers = tuple(
        item.candidate_id for item in admitted if item.candidate_id != winner.candidate_id
    )
    winner_fields: dict[str, object] = {
        "selected_candidate_id": winner.candidate_id,
        "complementary_candidate_ids": (),
        "material_displacements": derivation.material_displacements,
    }
    if derivation.blocking:
        return PreferredStrategicAlignment(
            candidate_set=candidate_set,
            criteria=criteria,
            disposition=SelectionDisposition.PROVISIONAL_REVIEW,
            admitted_loser_ids=losers,
            precomparison_rejections=rejections,
            comparison_dispositions=comparisons,
            active_frontier_candidate_ids=derivation.active_frontier_candidate_ids,
            detected_ambiguity_triggers=derivation.triggers,
            ambiguity_triggers=derivation.blocking,
            change_conditions=derivation.change_conditions,
            profile_fingerprint=profile.fingerprint,
            decision_action=SelectionAction.PROFILE_FALLBACK,
            publishable=False,
            **winner_fields,
        )
    return PreferredStrategicAlignment(
        candidate_set=candidate_set,
        criteria=criteria,
        disposition=SelectionDisposition.SELECTED,
        admitted_loser_ids=losers,
        precomparison_rejections=rejections,
        comparison_dispositions=comparisons,
        active_frontier_candidate_ids=derivation.active_frontier_candidate_ids,
        detected_ambiguity_triggers=derivation.triggers,
        change_conditions=derivation.change_conditions,
        profile_fingerprint=profile.fingerprint,
        decision_action=SelectionAction.NO_AGENT_CLEAR,
        publishable=True,
        **winner_fields,
    )


def _alignment_decision_request_payload(
    selection: PreferredStrategicAlignment,
    scenario_context_fingerprint: str,
    *,
    prior_challenge_fingerprints: tuple[str, ...] = (),
) -> dict[str, object]:
    prior_challenges = _canonical_ids(
        prior_challenge_fingerprints,
        "prior_challenge_fingerprints",
        pattern=_SHA256,
    )
    if selection.disposition == SelectionDisposition.SELECTED:
        raise ValueError("a clear no-agent selection cannot produce a decision request")
    gap_evidence = isinstance(selection.criteria, CandidateSetGapEvidence)
    derivation = (
        _empty_gap_derivation(selection.candidate_set)
        if gap_evidence
        else _derive_selection(
            selection.candidate_set.profile,
            selection.candidate_set,
            selection.criteria,
        )
    )
    if any(state == CandidateValidity.UNKNOWN_HARD_GATE for state in derivation.validity.values()):
        reason = AlignmentReviewReason.GREY_HARD_GATE
    elif AmbiguityTrigger.SUBSTITUTE_COMPLEMENTARY_UNCERTAIN in selection.ambiguity_triggers:
        reason = AlignmentReviewReason.SET_CLASSIFICATION
    elif selection.disposition == SelectionDisposition.NETWORK_GAP:
        reason = AlignmentReviewReason.NETWORK_GAP
    else:
        reason = AlignmentReviewReason.MATERIAL_AMBIGUITY

    options: list[AlignmentDecisionOption] = []
    if reason != AlignmentReviewReason.GREY_HARD_GATE:
        options.extend(
            AlignmentDecisionOption(
                option_id=f"select-{candidate_id}",
                action=AlignmentDecisionAction.SELECT_ELIGIBLE_OPTION,
                candidate_id=candidate_id,
            )
            for candidate_id in selection.active_frontier_candidate_ids
            if derivation.validity[candidate_id] == CandidateValidity.VALID
        )
    if reason == AlignmentReviewReason.SET_CLASSIFICATION:
        complementary_ids = tuple(
            sorted(
                item.candidate_id
                for item in selection.candidate_set.admitted_candidates
                if derivation.validity[item.candidate_id] == CandidateValidity.VALID
            )
        )
        if len(complementary_ids) >= 2:
            options.append(
                AlignmentDecisionOption(
                    option_id="retain-complementary-set",
                    action=AlignmentDecisionAction.RETAIN_COMPLEMENTARY_SET,
                    complementary_candidate_ids=complementary_ids,
                    complementary_set_fingerprint=_fingerprint(
                        {
                            "action": AlignmentDecisionAction.RETAIN_COMPLEMENTARY_SET,
                            "candidate_ids": complementary_ids,
                        }
                    ),
                )
            )
    population_findings = (
        ()
        if gap_evidence
        else (
            *selection.criteria.population.headline_500m,
            *selection.criteria.population.sensitivity_1000m,
        )
    )
    analysis_requirements = _additional_analysis_requirements(selection)
    analysis_cap = (
        selection.candidate_set.profile.ambiguity.maximum_additional_analyses_per_candidate_set
    )
    for analysis_kind, candidate_ids in analysis_requirements[:analysis_cap]:
        options.append(
            AlignmentDecisionOption(
                option_id=f"analyse-{analysis_kind.value}",
                action=AlignmentDecisionAction.RUN_ADDITIONAL_ANALYSIS,
                analysis_kind=analysis_kind,
                analysis_candidate_ids=candidate_ids,
            )
        )
    omitted_requirements = analysis_requirements[analysis_cap:]
    if omitted_requirements:
        options.append(
            AlignmentDecisionOption(
                option_id="request-human-intervention-analysis-cap",
                action=AlignmentDecisionAction.REQUEST_HUMAN_INTERVENTION,
                analysis_candidate_ids=tuple(
                    sorted(
                        {
                            candidate_id
                            for _, candidate_ids in omitted_requirements
                            for candidate_id in candidate_ids
                        }
                    )
                ),
                unresolved_analysis_kinds=tuple(
                    analysis_kind for analysis_kind, _ in omitted_requirements
                ),
            )
        )
    if selection.disposition == SelectionDisposition.NETWORK_GAP:
        options.append(
            AlignmentDecisionOption(
                option_id="expose-network-gap",
                action=AlignmentDecisionAction.EXPOSE_NETWORK_GAP,
            )
        )
    elif reason != AlignmentReviewReason.GREY_HARD_GATE:
        options.append(
            AlignmentDecisionOption(
                option_id="accept-profile-fallback",
                action=AlignmentDecisionAction.ACCEPT_PROFILE_FALLBACK,
            )
        )
    options.append(
        AlignmentDecisionOption(
            option_id="terminate",
            action=AlignmentDecisionAction.TERMINATE,
        )
    )
    ordered_options = tuple(sorted(options, key=lambda item: item.option_id))
    evidence_ids = tuple(
        sorted(
            {item.assessment_id for item in selection.criteria.evidence_snapshot.assessments}
            | (
                set()
                if gap_evidence
                else {
                    item.evidence_record_id
                    for item in (
                        *selection.criteria.education.completeness,
                        *selection.criteria.education.independent_travel_opportunity,
                        *selection.criteria.directness,
                        *selection.criteria.gradient,
                        *selection.criteria.uncertainty,
                    )
                }
            )
            | {item.assessment_option_id for item in population_findings}
        )
    )
    return {
        "selection": selection.model_dump(mode="json"),
        "request_id": _stable_id(
            "alignment-decision",
            (
                {
                    "selection_fingerprint": selection.selection_fingerprint,
                    "prior_challenge_fingerprints": prior_challenges,
                }
                if prior_challenges
                else selection.selection_fingerprint
            ),
        ),
        "reason": reason.value,
        "candidate_set_id": selection.candidate_set_id,
        "connection_id": selection.candidate_set.connection_id,
        "candidate_set_fingerprint": (selection.candidate_set.candidate_set_fingerprint),
        "selection_fingerprint": selection.selection_fingerprint,
        "evidence_snapshot_fingerprint": (
            selection.criteria.evidence_snapshot.snapshot_fingerprint
        ),
        "profile_fingerprint": selection.profile_fingerprint,
        "scenario_context_fingerprint": scenario_context_fingerprint,
        "agent_review_contracts": _compile_agent_review_contracts(
            profile_fingerprint=selection.profile_fingerprint,
            evidence_snapshot_fingerprint=(
                selection.criteria.evidence_snapshot.snapshot_fingerprint
            ),
            scenario_context_fingerprint=scenario_context_fingerprint,
        ).model_dump(mode="json"),
        "prior_challenge_fingerprints": list(prior_challenges),
        "immutable_evidence_ids": list(evidence_ids),
        "options": [item.model_dump(mode="json") for item in ordered_options],
    }


class AlignmentDecisionAction(StrEnum):
    SELECT_ELIGIBLE_OPTION = "select-eligible-option"
    RETAIN_COMPLEMENTARY_SET = "retain-complementary-set"
    RUN_ADDITIONAL_ANALYSIS = "run-additional-analysis"
    REQUEST_HUMAN_INTERVENTION = "request-human-intervention"
    ACCEPT_PROFILE_FALLBACK = "accept-profile-fallback"
    EXPOSE_NETWORK_GAP = "expose-network-gap"
    TERMINATE = "terminate"


class AdditionalAnalysisKind(StrEnum):
    TOPOLOGY_CONTINUITY = "topology-continuity"
    EDUCATION_COMPLETENESS = "education-access-completeness"
    EDUCATION_OPPORTUNITY = "education-access-opportunity"
    DIRECTNESS = "directness-evidence"
    GRADIENT = "gradient-evidence"
    UNCERTAINTY = "uncertainty-evidence"
    POPULATION_BORDERLINE_OA = "population-borderline-oa-review"
    CURRENT_DEVELOPMENT = "current-development-evidence"
    EXISTING_ALIGNMENT = "existing-alignment-eligibility"


_ADDITIONAL_ANALYSIS_ORDER = tuple(AdditionalAnalysisKind)


class AlignmentReviewReason(StrEnum):
    PROFILE_DEVIATION = "profile-deviation"
    SET_CLASSIFICATION = "candidate-set-classification"
    GREY_HARD_GATE = "grey-hard-gate"
    MATERIAL_AMBIGUITY = "material-ambiguity"
    NETWORK_GAP = "network-gap"


class AlignmentDecisionOption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    option_id: str = Field(pattern=_ID.pattern)
    action: AlignmentDecisionAction
    candidate_id: str | None = Field(default=None, pattern=_CANDIDATE_ID.pattern)
    complementary_candidate_ids: tuple[str, ...] = ()
    complementary_set_fingerprint: str = ""
    analysis_kind: AdditionalAnalysisKind | None = None
    analysis_candidate_ids: tuple[str, ...] = ()
    unresolved_analysis_kinds: tuple[AdditionalAnalysisKind, ...] = ()

    @model_validator(mode="after")
    def validate_action_payload(self) -> Self:
        if (self.action == AlignmentDecisionAction.SELECT_ELIGIBLE_OPTION) != (
            self.candidate_id is not None
        ):
            raise ValueError("only candidate-selection options name a candidate")
        complementary_ids = _canonical_ids(
            self.complementary_candidate_ids,
            "complementary_candidate_ids",
            pattern=_CANDIDATE_ID,
        )
        is_complementary = self.action == AlignmentDecisionAction.RETAIN_COMPLEMENTARY_SET
        if is_complementary != (len(complementary_ids) >= 2):
            raise ValueError("retain-complementary-set requires at least two exact candidates")
        expected_complementary_fingerprint = (
            _fingerprint(
                {
                    "action": self.action,
                    "candidate_ids": complementary_ids,
                }
            )
            if is_complementary
            else ""
        )
        if self.complementary_set_fingerprint != expected_complementary_fingerprint:
            raise ValueError("complementary set fingerprint is stale")
        if (self.action == AlignmentDecisionAction.RUN_ADDITIONAL_ANALYSIS) != (
            self.analysis_kind is not None
        ):
            raise ValueError("only analysis options name an analysis kind")
        analysis_candidate_ids = _canonical_ids(
            self.analysis_candidate_ids,
            "analysis_candidate_ids",
            pattern=_CANDIDATE_ID,
        )
        is_analysis = self.action == AlignmentDecisionAction.RUN_ADDITIONAL_ANALYSIS
        is_intervention = self.action == AlignmentDecisionAction.REQUEST_HUMAN_INTERVENTION
        if bool(analysis_candidate_ids) != (is_analysis or is_intervention):
            raise ValueError("analysis and intervention options must name affected candidates")
        unresolved = tuple(
            item for item in _ADDITIONAL_ANALYSIS_ORDER if item in self.unresolved_analysis_kinds
        )
        if len(unresolved) != len(self.unresolved_analysis_kinds):
            raise ValueError("unresolved analysis kinds must be unique and governed")
        if bool(unresolved) != is_intervention:
            raise ValueError("only human-intervention options name unresolved analyses")
        object.__setattr__(
            self,
            "analysis_candidate_ids",
            analysis_candidate_ids,
        )
        object.__setattr__(
            self,
            "complementary_candidate_ids",
            complementary_ids,
        )
        object.__setattr__(self, "unresolved_analysis_kinds", unresolved)
        return self


def _additional_analysis_requirements(
    selection: PreferredStrategicAlignment,
) -> tuple[tuple[AdditionalAnalysisKind, tuple[str, ...]], ...]:
    """Derive every material evidence action before applying the profile cap."""
    if isinstance(selection.criteria, CandidateSetGapEvidence):
        return ()
    candidate_ids_by_kind: dict[AdditionalAnalysisKind, set[str]] = {
        item: set() for item in AdditionalAnalysisKind
    }
    completeness = _finding_map(selection.criteria.education.completeness)
    opportunity = _finding_map(selection.criteria.education.independent_travel_opportunity)
    directness = _finding_map(selection.criteria.directness)
    gradient = _finding_map(selection.criteria.gradient)
    uncertainty = _finding_map(selection.criteria.uncertainty)
    for candidate in selection.candidate_set.admitted_candidates:
        candidate_id = candidate.candidate_id
        if candidate.topology_state == CriterionState.UNKNOWN:
            candidate_ids_by_kind[AdditionalAnalysisKind.TOPOLOGY_CONTINUITY].add(candidate_id)
        if completeness[candidate_id].state == CriterionState.UNKNOWN:
            candidate_ids_by_kind[AdditionalAnalysisKind.EDUCATION_COMPLETENESS].add(candidate_id)
        if opportunity[candidate_id].state == CriterionState.UNKNOWN:
            candidate_ids_by_kind[AdditionalAnalysisKind.EDUCATION_OPPORTUNITY].add(candidate_id)
        if directness[candidate_id].state == CriterionState.UNKNOWN:
            candidate_ids_by_kind[AdditionalAnalysisKind.DIRECTNESS].add(candidate_id)
        if gradient[candidate_id].state == CriterionState.UNKNOWN:
            candidate_ids_by_kind[AdditionalAnalysisKind.GRADIENT].add(candidate_id)
        if uncertainty[candidate_id].state == CriterionState.UNKNOWN:
            candidate_ids_by_kind[AdditionalAnalysisKind.UNCERTAINTY].add(candidate_id)
    for finding in (
        *selection.criteria.population.headline_500m,
        *selection.criteria.population.sensitivity_1000m,
    ):
        if finding.decisive_borderline_oa_ids:
            candidate_ids_by_kind[AdditionalAnalysisKind.POPULATION_BORDERLINE_OA].add(
                finding.candidate_id
            )
        if finding.current_development_omission:
            candidate_ids_by_kind[AdditionalAnalysisKind.CURRENT_DEVELOPMENT].add(
                finding.candidate_id
            )
    return tuple(
        (kind, tuple(sorted(candidate_ids_by_kind[kind])))
        for kind in _ADDITIONAL_ANALYSIS_ORDER
        if candidate_ids_by_kind[kind]
    )


class AlignmentDecisionRequest(BaseModel):
    """Finite, scenario-bound request at the compiler's alignment seam."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    selection: PreferredStrategicAlignment
    request_id: str = Field(pattern=_ID.pattern)
    reason: AlignmentReviewReason
    candidate_set_id: str = Field(pattern=_CANDIDATE_SET_ID.pattern)
    connection_id: str = Field(pattern=r"^connection-[0-9a-f]{20}$")
    candidate_set_fingerprint: str = Field(pattern=_SHA256.pattern)
    selection_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    scenario_context_fingerprint: str = Field(pattern=_SHA256.pattern)
    agent_review_contracts: AgentReviewContracts
    prior_challenge_fingerprints: tuple[str, ...] = ()
    immutable_evidence_ids: tuple[str, ...] = Field(min_length=1)
    options: tuple[AlignmentDecisionOption, ...] = Field(min_length=1, max_length=12)
    request_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_request(self) -> Self:
        selection = PreferredStrategicAlignment.model_validate(
            self.selection.model_dump(mode="python")
        )
        object.__setattr__(self, "selection", selection)
        evidence_ids = _canonical_ids(
            self.immutable_evidence_ids,
            "immutable_evidence_ids",
        )
        prior_challenges = _canonical_ids(
            self.prior_challenge_fingerprints,
            "prior_challenge_fingerprints",
            pattern=_SHA256,
        )
        object.__setattr__(
            self,
            "prior_challenge_fingerprints",
            prior_challenges,
        )
        options = tuple(sorted(self.options, key=lambda item: item.option_id))
        if len({item.option_id for item in options}) != len(options):
            raise ValueError("decision options must have unique IDs")
        expected = _alignment_decision_request_payload(
            selection,
            self.scenario_context_fingerprint,
            prior_challenge_fingerprints=prior_challenges,
        )
        actual = self.model_dump(
            mode="json",
            exclude={"request_fingerprint"},
        )
        if actual != expected:
            raise ValueError("alignment decision request is not compiler-generated")
        object.__setattr__(self, "immutable_evidence_ids", evidence_ids)
        object.__setattr__(self, "options", options)
        fingerprint = _fingerprint(expected)
        if self.request_fingerprint and self.request_fingerprint != fingerprint:
            raise ValueError("alignment decision request fingerprint is stale")
        object.__setattr__(self, "request_fingerprint", fingerprint)
        return self


def build_alignment_decision_request(
    selection: PreferredStrategicAlignment,
    *,
    scenario_context_fingerprint: str,
    prior_challenge_fingerprints: tuple[str, ...] = (),
) -> AlignmentDecisionRequest:
    """Generate the only accepted request menu for an unresolved selection."""
    selection = PreferredStrategicAlignment.model_validate(selection.model_dump(mode="python"))
    return AlignmentDecisionRequest.model_validate(
        _alignment_decision_request_payload(
            selection,
            scenario_context_fingerprint,
            prior_challenge_fingerprints=prior_challenge_fingerprints,
        )
    )


class _SubstantiveContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_id: str = Field(pattern=_ID.pattern)
    canonical_instructions: tuple[str, ...] = Field(min_length=2)
    evidence_packet_rules: tuple[str, ...] = Field(min_length=1)
    allowed_tools: tuple[str, ...] = Field(min_length=1)
    output_schema_fields: tuple[str, ...] = Field(min_length=1)
    stopping_policy: tuple[str, ...] = Field(min_length=1)
    authority_limits: tuple[str, ...] = Field(min_length=1)
    content_sha256: str = ""
    contract_fingerprint: str = ""

    @field_validator(
        "canonical_instructions",
        "evidence_packet_rules",
        "allowed_tools",
        "output_schema_fields",
        "stopping_policy",
        "authority_limits",
    )
    @classmethod
    def canonical_contract_content(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        if any(not item.strip() for item in value) or len(set(value)) != len(value):
            raise ValueError(
                f"{getattr(info, 'field_name', 'contract content')} must be substantive and unique"
            )
        return value

    @model_validator(mode="after")
    def bind_contract(self) -> Self:
        payload = self.model_dump(
            mode="json",
            exclude={"content_sha256", "contract_fingerprint"},
        )
        content_sha256 = _fingerprint(payload)
        if self.content_sha256 and self.content_sha256 != content_sha256:
            raise ValueError("substantive contract content SHA-256 is stale")
        object.__setattr__(self, "content_sha256", content_sha256)
        fingerprint = _fingerprint({**payload, "content_sha256": content_sha256})
        if self.contract_fingerprint and self.contract_fingerprint != fingerprint:
            raise ValueError("substantive contract fingerprint is stale")
        object.__setattr__(self, "contract_fingerprint", fingerprint)
        return self


class AgentRoleContract(_SubstantiveContract):
    """Canonical decision/critique role behavior, tools and authority limits."""


class PromptContract(_SubstantiveContract):
    """Canonical prompt packet, output schema and stopping policy."""


class HumanAdoptionContract(_SubstantiveContract):
    """Canonical human Reference SATN adoption authority contract."""


class AgentAuthorityRole(StrEnum):
    PRIMARY_ALIGNMENT_DECISION = "primary-alignment-decision-agent"
    INDEPENDENT_ALIGNMENT_CRITIC = "independent-alignment-critic"


class AgentInvocation(BaseModel):
    """A locally recorded agent invocation, not a proof of external identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    invocation_id: str = Field(pattern=_ID.pattern)
    role: AgentAuthorityRole
    role_contract_fingerprint: str = Field(pattern=_SHA256.pattern)
    prompt_contract_fingerprint: str = Field(pattern=_SHA256.pattern)
    request_fingerprint: str = Field(pattern=_SHA256.pattern)
    recorded_on: date
    invocation_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_invocation(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"invocation_fingerprint"})
        fingerprint = _fingerprint(payload)
        if self.invocation_fingerprint and self.invocation_fingerprint != fingerprint:
            raise ValueError("agent invocation fingerprint is stale")
        object.__setattr__(self, "invocation_fingerprint", fingerprint)
        return self


def _configured_agent_contracts(
    role: AgentAuthorityRole,
) -> tuple[AgentRoleContract, PromptContract]:
    if role == AgentAuthorityRole.INDEPENDENT_ALIGNMENT_CRITIC:
        return (
            AgentRoleContract(
                contract_id="satn-independent-alignment-critic-role.v3",
                canonical_instructions=(
                    "Independently challenge the primary agent's exact finite action.",
                    "Test evidence lineage, authority, hard gates and option admissibility.",
                    "Record material and mandatory challenges without selecting a route.",
                ),
                evidence_packet_rules=(
                    "Use only request evidence and the exact recorded primary response.",
                    "Reject foreign, stale or incomplete evidence.",
                ),
                allowed_tools=(
                    "read-governed-evidence",
                    "verify-primary-response-fingerprint",
                    "submit-alignment-critique-record",
                ),
                output_schema_fields=(
                    "request_fingerprint",
                    "response_fingerprint",
                    "selection_fingerprint",
                    "scenario_context_fingerprint",
                    "evidence_snapshot_fingerprint",
                    "profile_fingerprint",
                    "finding",
                    "resolved",
                    "evidence_ids",
                    "invocation",
                    "decision_revision_record.challenge_findings",
                ),
                stopping_policy=(
                    "Accept only when every material challenge is resolved.",
                    "Otherwise return rejected or needs-additional-analysis with challenges.",
                ),
                authority_limits=(
                    "The critic cannot choose an option, waive a challenge, or publish a network.",
                ),
            ),
            PromptContract(
                contract_id="satn-independent-alignment-critic-prompt.v3",
                canonical_instructions=(
                    "Audit the exact request and recorded primary response independently.",
                    "Return one schema-valid AlignmentCritiqueRecord and typed challenges.",
                ),
                evidence_packet_rules=(
                    "Cite immutable evidence IDs for every finding and missing-evidence claim.",
                ),
                allowed_tools=(
                    "read-only-evidence-inspection",
                    "verify-local-invocation-record",
                    "schema-bound-critique",
                ),
                output_schema_fields=(
                    "request_fingerprint",
                    "response_fingerprint",
                    "selection_fingerprint",
                    "scenario_context_fingerprint",
                    "evidence_snapshot_fingerprint",
                    "profile_fingerprint",
                    "finding",
                    "resolved",
                    "evidence_ids",
                    "invocation",
                    "decision_revision_record.challenge_findings",
                ),
                stopping_policy=(
                    "Stop with accepted only when no mandatory or material challenge remains.",
                ),
                authority_limits=(
                    "Prompt authority is critique-only and distinct from "
                    "primary decision authority.",
                ),
            ),
        )
    role_contract = AgentRoleContract(
        contract_id=f"satn-{role.value}-role.v2",
        canonical_instructions=(
            "Use only the finite compiler-authored option menu.",
            "Cite immutable evidence identifiers for every material conclusion.",
            "Never waive topology or education completeness mandatory Red gates.",
        ),
        evidence_packet_rules=(
            "Treat the request evidence snapshot and scenario context as immutable.",
        ),
        allowed_tools=(
            "read-governed-evidence",
            "submit-finite-option-response",
        ),
        output_schema_fields=(
            "request_id",
            "request_fingerprint",
            "option_id",
            "evidence_ids",
        ),
        stopping_policy=(
            "Stop at an offered option, typed analysis request, or human intervention.",
        ),
        authority_limits=(
            "No route invention, evidence mutation, profile mutation, or publication authority.",
        ),
    )
    prompt_contract = PromptContract(
        contract_id=f"satn-{role.value}-prompt.v2",
        canonical_instructions=(
            "Evaluate the exact Candidate Set and Scenario context in the request.",
            "Return one finite option and citations; return no free-form action.",
        ),
        evidence_packet_rules=("Ignore evidence not identified by immutable_evidence_ids.",),
        allowed_tools=(
            "read-only-evidence-inspection",
            "schema-bound-response",
        ),
        output_schema_fields=(
            "option_id",
            "evidence_ids",
            "agent_invocation",
        ),
        stopping_policy=(
            "Stop when evidence is insufficient and choose the compiler-authored escalation.",
        ),
        authority_limits=(
            "The prompt cannot grant authority beyond the configured role contract.",
        ),
    )
    return role_contract, prompt_contract


class AgentReviewContracts(BaseModel):
    """Compiler-authored role and prompt contracts for one review request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_set_id: Literal["satn-agent-review-contracts/v1"] = "satn-agent-review-contracts/v1"
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    scenario_context_fingerprint: str = Field(pattern=_SHA256.pattern)
    primary_role_contract: AgentRoleContract
    primary_prompt_contract: PromptContract
    critic_role_contract: AgentRoleContract
    critic_prompt_contract: PromptContract
    contracts_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_contracts(self) -> Self:
        expected_primary_role, expected_primary_prompt = _configured_agent_contracts(
            AgentAuthorityRole.PRIMARY_ALIGNMENT_DECISION
        )
        expected_critic_role, expected_critic_prompt = _configured_agent_contracts(
            AgentAuthorityRole.INDEPENDENT_ALIGNMENT_CRITIC
        )
        if (
            self.primary_role_contract != expected_primary_role
            or self.primary_prompt_contract != expected_primary_prompt
            or self.critic_role_contract != expected_critic_role
            or self.critic_prompt_contract != expected_critic_prompt
        ):
            raise ValueError(
                "agent contracts must contain exact compiler-configured substantive content"
            )
        payload = self.model_dump(mode="json", exclude={"contracts_fingerprint"})
        fingerprint = _fingerprint(payload)
        if self.contracts_fingerprint and self.contracts_fingerprint != fingerprint:
            raise ValueError("agent review contracts fingerprint is stale")
        object.__setattr__(self, "contracts_fingerprint", fingerprint)
        return self


def _compile_agent_review_contracts(
    *,
    profile_fingerprint: str,
    evidence_snapshot_fingerprint: str,
    scenario_context_fingerprint: str,
) -> AgentReviewContracts:
    primary_role, primary_prompt = _configured_agent_contracts(
        AgentAuthorityRole.PRIMARY_ALIGNMENT_DECISION
    )
    critic_role, critic_prompt = _configured_agent_contracts(
        AgentAuthorityRole.INDEPENDENT_ALIGNMENT_CRITIC
    )
    return AgentReviewContracts(
        profile_fingerprint=profile_fingerprint,
        evidence_snapshot_fingerprint=evidence_snapshot_fingerprint,
        scenario_context_fingerprint=scenario_context_fingerprint,
        primary_role_contract=primary_role,
        primary_prompt_contract=primary_prompt,
        critic_role_contract=critic_role,
        critic_prompt_contract=critic_prompt,
    )


AlignmentDecisionRequest.model_rebuild()


class AlignmentDecisionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(pattern=_ID.pattern)
    request_fingerprint: str = Field(pattern=_SHA256.pattern)
    option_id: str = Field(pattern=_ID.pattern)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    invocation: AgentInvocation
    prompt_fingerprint: str = ""
    response_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_response(self) -> Self:
        evidence_ids = _canonical_ids(self.evidence_ids, "evidence_ids")
        object.__setattr__(self, "evidence_ids", evidence_ids)
        invocation = AgentInvocation.model_validate(self.invocation.model_dump(mode="python"))
        if invocation.role != AgentAuthorityRole.PRIMARY_ALIGNMENT_DECISION:
            raise ValueError("alignment response must record a primary agent invocation")
        if invocation.request_fingerprint != self.request_fingerprint:
            raise ValueError("primary invocation is not bound to the exact response request")
        object.__setattr__(self, "invocation", invocation)
        prompt_fingerprint = _fingerprint(
            {
                "request_fingerprint": self.request_fingerprint,
                "option_id": self.option_id,
                "invocation_fingerprint": invocation.invocation_fingerprint,
            }
        )
        if self.prompt_fingerprint and self.prompt_fingerprint != prompt_fingerprint:
            raise ValueError("alignment decision prompt fingerprint is stale")
        object.__setattr__(self, "prompt_fingerprint", prompt_fingerprint)
        payload = self.model_dump(mode="json", exclude={"response_fingerprint"})
        expected = _fingerprint(payload)
        if self.response_fingerprint and self.response_fingerprint != expected:
            raise ValueError("alignment decision response fingerprint is stale")
        object.__setattr__(self, "response_fingerprint", expected)
        return self


class CritiqueFinding(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_ADDITIONAL_ANALYSIS = "needs-additional-analysis"


class AlignmentCritiqueRecord(BaseModel):
    """Independent critique bound to the exact request, result and evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_fingerprint: str = Field(pattern=_SHA256.pattern)
    response_fingerprint: str = Field(pattern=_SHA256.pattern)
    selection_fingerprint: str = Field(pattern=_SHA256.pattern)
    scenario_context_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    finding: CritiqueFinding
    resolved: bool = Field(strict=True)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    invocation: AgentInvocation
    prompt_fingerprint: str = ""
    critique_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_critique(self) -> Self:
        if self.resolved != (self.finding == CritiqueFinding.ACCEPTED):
            raise ValueError("only an accepted critique is resolved")
        evidence_ids = _canonical_ids(self.evidence_ids, "evidence_ids")
        object.__setattr__(self, "evidence_ids", evidence_ids)
        invocation = AgentInvocation.model_validate(self.invocation.model_dump(mode="python"))
        if invocation.role != AgentAuthorityRole.INDEPENDENT_ALIGNMENT_CRITIC:
            raise ValueError("critique must record an independent critic invocation")
        if invocation.request_fingerprint != self.request_fingerprint:
            raise ValueError("critic invocation is not bound to the exact critique request")
        object.__setattr__(self, "invocation", invocation)
        prompt_fingerprint = _fingerprint(
            {
                "request_fingerprint": self.request_fingerprint,
                "response_fingerprint": self.response_fingerprint,
                "invocation_fingerprint": invocation.invocation_fingerprint,
            }
        )
        if self.prompt_fingerprint and self.prompt_fingerprint != prompt_fingerprint:
            raise ValueError("alignment critique prompt fingerprint is stale")
        object.__setattr__(self, "prompt_fingerprint", prompt_fingerprint)
        payload = self.model_dump(mode="json", exclude={"critique_fingerprint"})
        expected = _fingerprint(payload)
        if self.critique_fingerprint and self.critique_fingerprint != expected:
            raise ValueError("alignment critique fingerprint is stale")
        object.__setattr__(self, "critique_fingerprint", expected)
        return self


class AcceptedDecisionEnvelope(BaseModel):
    """Alignment-specific accepted request, response and required critique."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: AlignmentDecisionRequest
    response: AlignmentDecisionResponse
    critique: AlignmentCritiqueRecord
    resolved_challenge_fingerprints: tuple[str, ...] = ()
    challenge_resolution_evidence_ids: tuple[str, ...] = ()
    envelope_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_envelope(self) -> Self:
        request = AlignmentDecisionRequest.model_validate(self.request.model_dump(mode="python"))
        response = AlignmentDecisionResponse.model_validate(self.response.model_dump(mode="python"))
        critique = AlignmentCritiqueRecord.model_validate(self.critique.model_dump(mode="python"))
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "response", response)
        object.__setattr__(self, "critique", critique)
        resolved_challenges = _canonical_ids(
            self.resolved_challenge_fingerprints,
            "resolved_challenge_fingerprints",
            pattern=_SHA256,
        )
        resolution_evidence = _canonical_ids(
            self.challenge_resolution_evidence_ids,
            "challenge_resolution_evidence_ids",
        )
        if bool(resolved_challenges) != bool(resolution_evidence):
            raise ValueError(
                "accepted challenge resolution requires fingerprints and exact evidence"
            )
        if not set(resolution_evidence).issubset(request.immutable_evidence_ids):
            raise ValueError("accepted challenge resolution cites foreign evidence")
        if resolved_challenges != request.prior_challenge_fingerprints:
            raise ValueError("accepted challenge resolution is not bound into its exact request")
        object.__setattr__(
            self,
            "resolved_challenge_fingerprints",
            resolved_challenges,
        )
        object.__setattr__(
            self,
            "challenge_resolution_evidence_ids",
            resolution_evidence,
        )
        if (
            response.request_id != request.request_id
            or response.request_fingerprint != request.request_fingerprint
            or response.option_id not in {item.option_id for item in request.options}
            or not set(response.evidence_ids).issubset(request.immutable_evidence_ids)
        ):
            raise ValueError("accepted response is stale or chooses an unoffered action")
        if (
            critique.request_fingerprint != request.request_fingerprint
            or critique.response_fingerprint != response.response_fingerprint
            or critique.selection_fingerprint != request.selection_fingerprint
            or critique.scenario_context_fingerprint != request.scenario_context_fingerprint
            or critique.evidence_snapshot_fingerprint != request.evidence_snapshot_fingerprint
            or critique.profile_fingerprint != request.profile_fingerprint
            or not set(critique.evidence_ids).issubset(request.immutable_evidence_ids)
            or not critique.resolved
        ):
            raise ValueError("accepted alignment decision lacks an exact resolved critique")
        if (
            response.invocation.role_contract_fingerprint
            != request.agent_review_contracts.primary_role_contract.contract_fingerprint
            or response.invocation.prompt_contract_fingerprint
            != request.agent_review_contracts.primary_prompt_contract.contract_fingerprint
            or critique.invocation.role_contract_fingerprint
            != request.agent_review_contracts.critic_role_contract.contract_fingerprint
            or critique.invocation.prompt_contract_fingerprint
            != request.agent_review_contracts.critic_prompt_contract.contract_fingerprint
        ):
            raise ValueError("agent invocation is not bound to the exact compiler contracts")
        if critique.invocation.invocation_id == response.invocation.invocation_id:
            raise ValueError("alignment decision critic must be a separately recorded invocation")
        payload = self.model_dump(mode="json", exclude={"envelope_fingerprint"})
        expected = _fingerprint(payload)
        if self.envelope_fingerprint and self.envelope_fingerprint != expected:
            raise ValueError("accepted decision envelope fingerprint is stale")
        object.__setattr__(self, "envelope_fingerprint", expected)
        return self


class DecisionEnvelopeRejectionReason(StrEnum):
    STALE_REQUEST = "stale-request"
    UNOFFERED_OPTION = "unoffered-option"
    INVALID_AUTHORITY = "invalid-authority"
    UNRESOLVED_CRITIQUE = "unresolved-critique"
    NESTED_VALIDATION_FAILURE = "nested-validation-failure"


class DecisionEnvelopeRejection(BaseModel):
    """Typed, non-throwing boundary result for an invalid runtime envelope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(pattern=_ID.pattern)
    reason: DecisionEnvelopeRejectionReason
    offered_option_ids: tuple[str, ...] = Field(min_length=1)
    lineage_fingerprints: tuple[str, ...] = Field(min_length=1)
    rejection_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_rejection(self) -> Self:
        options = _canonical_ids(self.offered_option_ids, "offered_option_ids")
        lineage = _canonical_ids(
            self.lineage_fingerprints,
            "lineage_fingerprints",
            pattern=_SHA256,
        )
        object.__setattr__(self, "offered_option_ids", options)
        object.__setattr__(self, "lineage_fingerprints", lineage)
        payload = self.model_dump(mode="json", exclude={"rejection_fingerprint"})
        fingerprint = _fingerprint(payload)
        if self.rejection_fingerprint and self.rejection_fingerprint != fingerprint:
            raise ValueError("decision envelope rejection fingerprint is stale")
        object.__setattr__(self, "rejection_fingerprint", fingerprint)
        return self


def validate_alignment_decision_envelope(
    request: object,
    response: object,
    critique: object,
) -> AcceptedDecisionEnvelope | DecisionEnvelopeRejection:
    """Total runtime boundary: malformed nested input becomes a typed rejection."""

    raw_values: tuple[object, ...] = (request, response, critique)

    def nested_rejection() -> DecisionEnvelopeRejection:
        lineages: list[str] = []
        for position, value in enumerate(raw_values):
            type_name = "uninspectable"
            try:
                value_type = type(value)
                type_name = f"{value_type.__module__}.{value_type.__qualname__}"
            except BaseException:
                pass
            lineages.append(
                _fingerprint(
                    {
                        "boundary_position": position,
                        "input_type": type_name,
                    }
                )
            )
        return DecisionEnvelopeRejection(
            request_id="invalid-request",
            reason=DecisionEnvelopeRejectionReason.NESTED_VALIDATION_FAILURE,
            offered_option_ids=("no-valid-options",),
            lineage_fingerprints=tuple(lineages),
        )

    try:
        validated_request = AlignmentDecisionRequest.model_validate(
            request.model_dump(mode="python") if isinstance(request, BaseModel) else request
        )
        validated_response = AlignmentDecisionResponse.model_validate(
            response.model_dump(mode="python") if isinstance(response, BaseModel) else response
        )
        validated_critique = AlignmentCritiqueRecord.model_validate(
            critique.model_dump(mode="python") if isinstance(critique, BaseModel) else critique
        )
    except BaseException:
        return nested_rejection()

    try:
        offered = tuple(item.option_id for item in validated_request.options)
        if (
            validated_response.request_id != validated_request.request_id
            or validated_response.request_fingerprint != validated_request.request_fingerprint
        ):
            reason = DecisionEnvelopeRejectionReason.STALE_REQUEST
        elif validated_response.option_id not in offered:
            reason = DecisionEnvelopeRejectionReason.UNOFFERED_OPTION
        elif not validated_critique.resolved:
            reason = DecisionEnvelopeRejectionReason.UNRESOLVED_CRITIQUE
        else:
            try:
                return AcceptedDecisionEnvelope(
                    request=validated_request,
                    response=validated_response,
                    critique=validated_critique,
                )
            except BaseException:
                reason = DecisionEnvelopeRejectionReason.INVALID_AUTHORITY
        return DecisionEnvelopeRejection(
            request_id=validated_request.request_id,
            reason=reason,
            offered_option_ids=offered,
            lineage_fingerprints=(
                _fingerprint(
                    {
                        "kind": "request",
                        "fingerprint": validated_request.request_fingerprint,
                    }
                ),
                _fingerprint(
                    {
                        "kind": "response",
                        "fingerprint": validated_response.response_fingerprint,
                    }
                ),
                _fingerprint(
                    {
                        "kind": "critique",
                        "fingerprint": validated_critique.critique_fingerprint,
                    }
                ),
            ),
        )
    except BaseException:
        return nested_rejection()


class ChallengeSeverity(StrEnum):
    MANDATORY_RED = "mandatory-red"
    MATERIAL = "material"
    ADVISORY = "advisory"


class ChallengeResolution(StrEnum):
    UNRESOLVED = "unresolved"
    REVISED = "resolved-by-revision"


class AlignmentChallengeFinding(BaseModel):
    """One critic challenge with explicit, bounded resolution semantics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    challenge_id: str = Field(pattern=_ID.pattern)
    severity: ChallengeSeverity
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    missing_evidence_ids: tuple[str, ...] = ()
    resolution: ChallengeResolution = ChallengeResolution.UNRESOLVED
    resolution_evidence_ids: tuple[str, ...] = ()
    challenge_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_challenge(self) -> Self:
        evidence = _canonical_ids(self.evidence_ids, "evidence_ids")
        missing = _canonical_ids(self.missing_evidence_ids, "missing_evidence_ids")
        resolution_evidence = _canonical_ids(
            self.resolution_evidence_ids,
            "resolution_evidence_ids",
        )
        if self.resolution == ChallengeResolution.UNRESOLVED and resolution_evidence:
            raise ValueError("unresolved challenge cannot claim resolution evidence")
        if self.resolution == ChallengeResolution.REVISED and not resolution_evidence:
            raise ValueError("revised challenge requires exact resolution evidence")
        object.__setattr__(self, "evidence_ids", evidence)
        object.__setattr__(self, "missing_evidence_ids", missing)
        object.__setattr__(self, "resolution_evidence_ids", resolution_evidence)
        payload = self.model_dump(mode="json", exclude={"challenge_fingerprint"})
        fingerprint = _fingerprint(payload)
        if self.challenge_fingerprint and self.challenge_fingerprint != fingerprint:
            raise ValueError("alignment challenge fingerprint is stale")
        object.__setattr__(self, "challenge_fingerprint", fingerprint)
        return self


class DecisionRevisionRecord(BaseModel):
    """Non-accepted critique and bounded attempted revisions retained for replay."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: AlignmentDecisionRequest
    response: AlignmentDecisionResponse
    critique: AlignmentCritiqueRecord
    challenge_findings: tuple[AlignmentChallengeFinding, ...] = Field(min_length=1)
    attempted_revision_fingerprints: tuple[str, ...] = ()
    revision_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_revision(self) -> Self:
        request = AlignmentDecisionRequest.model_validate(self.request.model_dump(mode="python"))
        response = AlignmentDecisionResponse.model_validate(self.response.model_dump(mode="python"))
        critique = AlignmentCritiqueRecord.model_validate(self.critique.model_dump(mode="python"))
        challenges = tuple(sorted(self.challenge_findings, key=lambda item: item.challenge_id))
        if len({item.challenge_id for item in challenges}) != len(challenges):
            raise ValueError("revision challenge IDs must be unique")
        attempts = _canonical_ids(
            self.attempted_revision_fingerprints,
            "attempted_revision_fingerprints",
            pattern=_SHA256,
        )
        if (
            response.request_id != request.request_id
            or response.request_fingerprint != request.request_fingerprint
            or response.option_id not in {item.option_id for item in request.options}
            or critique.request_fingerprint != request.request_fingerprint
            or critique.response_fingerprint != response.response_fingerprint
            or critique.selection_fingerprint != request.selection_fingerprint
            or critique.scenario_context_fingerprint != request.scenario_context_fingerprint
            or critique.evidence_snapshot_fingerprint != request.evidence_snapshot_fingerprint
            or critique.profile_fingerprint != request.profile_fingerprint
            or not set(critique.evidence_ids).issubset(request.immutable_evidence_ids)
            or critique.resolved
            or critique.finding == CritiqueFinding.ACCEPTED
        ):
            raise ValueError("revision record requires an exact unresolved critique replay")
        if any(
            not set((*item.evidence_ids, *item.resolution_evidence_ids)).issubset(
                request.immutable_evidence_ids
            )
            for item in challenges
        ):
            raise ValueError("revision challenge cites evidence outside the compiler request")
        if (
            response.invocation.role_contract_fingerprint
            != request.agent_review_contracts.primary_role_contract.contract_fingerprint
            or response.invocation.prompt_contract_fingerprint
            != request.agent_review_contracts.primary_prompt_contract.contract_fingerprint
            or critique.invocation.role_contract_fingerprint
            != request.agent_review_contracts.critic_role_contract.contract_fingerprint
            or critique.invocation.prompt_contract_fingerprint
            != request.agent_review_contracts.critic_prompt_contract.contract_fingerprint
        ):
            raise ValueError("revision invocations must use the exact compiler contracts")
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "response", response)
        object.__setattr__(self, "critique", critique)
        object.__setattr__(self, "challenge_findings", challenges)
        object.__setattr__(self, "attempted_revision_fingerprints", attempts)
        payload = self.model_dump(mode="json", exclude={"revision_fingerprint"})
        fingerprint = _fingerprint(payload)
        if self.revision_fingerprint and self.revision_fingerprint != fingerprint:
            raise ValueError("decision revision fingerprint is stale")
        object.__setattr__(self, "revision_fingerprint", fingerprint)
        return self


class RuntimeAttemptOutcome(StrEnum):
    ACCEPTED = "accepted-envelope"
    REVISION = "revision-record"
    ENVELOPE_REJECTED = "typed-envelope-rejection"
    PROVIDER_TIMEOUT = "provider-timeout"
    PROVIDER_REJECTION = "provider-rejection"


def _review_run_config_fingerprint(
    deadline_seconds: float,
    maximum_attempts: int,
) -> str:
    return _fingerprint(
        {
            "deadline_seconds": deadline_seconds,
            "maximum_attempts": maximum_attempts,
        }
    )


class RuntimeInvocationRecord(BaseModel):
    """Typed local record of a provider failure in one bounded review run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    invocation_id: str = Field(pattern=_ID.pattern)
    review_run_id: str = Field(pattern=r"^review-run-[0-9a-f]{20}$")
    run_instance_id: str = Field(pattern=_ID.pattern)
    run_scope_fingerprint: str = Field(pattern=_SHA256.pattern)
    run_config_fingerprint: str = Field(pattern=_SHA256.pattern)
    attempt_number: int = Field(ge=1, strict=True)
    maximum_attempts: int = Field(default=3, ge=1, le=10, strict=True)
    deadline_seconds: float = Field(default=30.0, gt=0, le=300)
    frontier_fingerprint: str = Field(pattern=_SHA256.pattern)
    request_fingerprint: str = Field(pattern=_SHA256.pattern)
    outcome: Literal[
        RuntimeAttemptOutcome.PROVIDER_TIMEOUT,
        RuntimeAttemptOutcome.PROVIDER_REJECTION,
    ]
    failure_code: str = Field(pattern=_ID.pattern)
    started_at_ms: int = Field(ge=0, strict=True)
    completed_at_ms: int = Field(ge=0, strict=True)
    receipt_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_invocation(self) -> Self:
        if self.attempt_number > self.maximum_attempts:
            raise ValueError("runtime invocation exceeds the review run attempt limit")
        if self.completed_at_ms < self.started_at_ms:
            raise ValueError("runtime invocation record is not timed")
        if self.run_config_fingerprint != _review_run_config_fingerprint(
            self.deadline_seconds,
            self.maximum_attempts,
        ):
            raise ValueError("runtime invocation record run config fingerprint is stale")
        fingerprint = _fingerprint(self.model_dump(mode="json", exclude={"receipt_fingerprint"}))
        if self.receipt_fingerprint and self.receipt_fingerprint != fingerprint:
            raise ValueError("runtime invocation record fingerprint is stale")
        object.__setattr__(self, "receipt_fingerprint", fingerprint)
        return self


class ReviewRunLedgerProvenance(BaseModel):
    """Immutable outer compile-run provenance retained with runtime attempts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    review_run_id: str = Field(pattern=r"^review-run-[0-9a-f]{20}$")
    run_instance_id: str = Field(pattern=_ID.pattern)
    run_scope_fingerprint: str = Field(pattern=_SHA256.pattern)
    run_config_fingerprint: str = Field(pattern=_SHA256.pattern)
    deadline_seconds: float = Field(gt=0, le=300)
    maximum_attempts: int = Field(ge=1, le=10, strict=True)

    @model_validator(mode="after")
    def bind_provenance(self) -> Self:
        if self.run_config_fingerprint != _review_run_config_fingerprint(
            self.deadline_seconds,
            self.maximum_attempts,
        ):
            raise ValueError("review run ledger provenance config fingerprint is stale")
        return self

    @classmethod
    def from_invocation(cls, record: RuntimeInvocationRecord) -> Self:
        return cls(
            review_run_id=record.review_run_id,
            run_instance_id=record.run_instance_id,
            run_scope_fingerprint=record.run_scope_fingerprint,
            run_config_fingerprint=record.run_config_fingerprint,
            deadline_seconds=record.deadline_seconds,
            maximum_attempts=record.maximum_attempts,
        )


class RuntimeDecisionAttempt(BaseModel):
    """One locally recorded attempt charged to the bounded review run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: AlignmentDecisionRequest
    outcome: RuntimeAttemptOutcome
    accepted_envelope_fingerprint: str = ""
    revision_fingerprint: str = ""
    envelope_rejection: DecisionEnvelopeRejection | None = None
    provider_failure_code: str = ""
    invocation_record: RuntimeInvocationRecord | None = None
    counting_policy: Literal["count-every-recorded-attempt"] = "count-every-recorded-attempt"
    attempt_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_attempt(self) -> Self:
        request = AlignmentDecisionRequest.model_validate(self.request.model_dump(mode="python"))
        accepted = self.accepted_envelope_fingerprint
        revision = self.revision_fingerprint
        rejection = (
            DecisionEnvelopeRejection.model_validate(
                self.envelope_rejection.model_dump(mode="python")
            )
            if self.envelope_rejection is not None
            else None
        )
        invocation = (
            RuntimeInvocationRecord.model_validate(self.invocation_record.model_dump(mode="python"))
            if self.invocation_record is not None
            else None
        )
        if accepted and _SHA256.fullmatch(accepted) is None:
            raise ValueError("runtime accepted envelope fingerprint is malformed")
        if revision and _SHA256.fullmatch(revision) is None:
            raise ValueError("runtime revision fingerprint is malformed")
        if self.outcome == RuntimeAttemptOutcome.ACCEPTED:
            valid = bool(accepted) and not revision and rejection is None
        elif self.outcome == RuntimeAttemptOutcome.REVISION:
            valid = bool(revision) and not accepted and rejection is None
        elif self.outcome == RuntimeAttemptOutcome.ENVELOPE_REJECTED:
            valid = (
                rejection is not None
                and rejection.request_id == request.request_id
                and not accepted
                and not revision
            )
        else:
            valid = (
                bool(self.provider_failure_code)
                and _ID.fullmatch(self.provider_failure_code) is not None
                and not accepted
                and not revision
                and rejection is None
                and invocation is not None
                and invocation.request_fingerprint == request.request_fingerprint
                and invocation.outcome == self.outcome
                and invocation.failure_code == self.provider_failure_code
            )
        if not valid:
            raise ValueError("runtime attempt outcome lacks its exact typed result")
        if (
            self.outcome
            not in {
                RuntimeAttemptOutcome.PROVIDER_TIMEOUT,
                RuntimeAttemptOutcome.PROVIDER_REJECTION,
            }
            and self.provider_failure_code
        ):
            raise ValueError("only provider failures name a provider failure code")
        if (
            self.outcome
            not in {
                RuntimeAttemptOutcome.PROVIDER_TIMEOUT,
                RuntimeAttemptOutcome.PROVIDER_REJECTION,
            }
            and invocation is not None
        ):
            raise ValueError("only provider failures contain invocation records")
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "envelope_rejection", rejection)
        object.__setattr__(self, "invocation_record", invocation)
        payload = self.model_dump(mode="json", exclude={"attempt_fingerprint"})
        fingerprint = _fingerprint(payload)
        if self.attempt_fingerprint and self.attempt_fingerprint != fingerprint:
            raise ValueError("runtime decision attempt fingerprint is stale")
        object.__setattr__(self, "attempt_fingerprint", fingerprint)
        return self


class ResolvedPreferredStrategicAlignment(BaseModel):
    """A publishable result resolved from a compiler result and exact ledger action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    compiler_selection: PreferredStrategicAlignment
    accepted_decision_envelope: AcceptedDecisionEnvelope | None = None
    disposition: Literal[SelectionDisposition.SELECTED] = SelectionDisposition.SELECTED
    selected_candidate_id: str | None = Field(
        default=None,
        pattern=_CANDIDATE_ID.pattern,
    )
    complementary_candidate_ids: tuple[str, ...] = ()
    resolution_action: AlignmentDecisionAction | None = None
    publishable: Literal[True] = True
    accepted_decision_envelope_fingerprint: str = ""
    resolution_fingerprint: str = ""

    @property
    def candidate_set_id(self) -> str:
        return self.compiler_selection.candidate_set_id

    @model_validator(mode="after")
    def bind_resolution(self) -> Self:
        selection = PreferredStrategicAlignment.model_validate(
            self.compiler_selection.model_dump(mode="python")
        )
        envelope = (
            AcceptedDecisionEnvelope.model_validate(
                self.accepted_decision_envelope.model_dump(mode="python")
            )
            if self.accepted_decision_envelope is not None
            else None
        )
        object.__setattr__(self, "compiler_selection", selection)
        object.__setattr__(
            self,
            "accepted_decision_envelope",
            envelope,
        )
        if envelope is None:
            if (
                selection.disposition != SelectionDisposition.SELECTED
                or not selection.publishable
                or selection.selected_candidate_id is None
            ):
                raise ValueError(
                    "a decision-free resolution requires an exact publishable compiler result"
                )
            expected_selected = selection.selected_candidate_id
            expected_complementary: tuple[str, ...] = ()
            expected_envelope_fingerprint = ""
            expected_action = None
        else:
            if (
                envelope.request.selection_fingerprint != selection.selection_fingerprint
                or envelope.request.candidate_set_id != selection.candidate_set_id
            ):
                raise ValueError("accepted decision resolution is stale for the compiler result")
            option = next(
                item
                for item in envelope.request.options
                if item.option_id == envelope.response.option_id
            )
            if (
                option.action == AlignmentDecisionAction.SELECT_ELIGIBLE_OPTION
                and option.candidate_id is not None
            ):
                expected_selected = option.candidate_id
                expected_complementary = ()
            elif option.action == AlignmentDecisionAction.RETAIN_COMPLEMENTARY_SET:
                expected_selected = None
                expected_complementary = option.complementary_candidate_ids
            elif option.action == AlignmentDecisionAction.ACCEPT_PROFILE_FALLBACK:
                derivation = _derive_selection(
                    selection.candidate_set.profile,
                    selection.candidate_set,
                    selection.criteria,
                )
                if derivation.hard_gate_unknown or selection.selected_candidate_id is None:
                    raise ValueError(
                        "profile fallback cannot resolve a mandatory hard-gate blocker"
                    )
                expected_selected = selection.selected_candidate_id
                expected_complementary = ()
            else:
                raise ValueError(
                    "accepted decision action does not resolve a Preferred Strategic Alignment"
                )
            expected_envelope_fingerprint = envelope.envelope_fingerprint
            expected_action = option.action
        complementary = _canonical_ids(
            self.complementary_candidate_ids,
            "complementary_candidate_ids",
            pattern=_CANDIDATE_ID,
        )
        if (
            self.selected_candidate_id != expected_selected
            or complementary != expected_complementary
            or self.resolution_action != expected_action
            or (
                "accepted_decision_envelope_fingerprint" in self.model_fields_set
                and self.accepted_decision_envelope_fingerprint != expected_envelope_fingerprint
            )
        ):
            raise ValueError(
                "resolved Preferred Strategic Alignment contradicts its exact decision"
            )
        if (expected_selected is not None) == bool(expected_complementary):
            raise ValueError(
                "resolved Preferred Strategic Alignment requires one selected option "
                "or one complementary set"
            )
        admitted_ids = {item.candidate_id for item in selection.candidate_set.admitted_candidates}
        if not {
            item
            for item in (
                expected_selected,
                *expected_complementary,
            )
            if item is not None
        }.issubset(admitted_ids):
            raise ValueError("resolved Preferred Strategic Alignment contains a foreign candidate")
        object.__setattr__(
            self,
            "complementary_candidate_ids",
            expected_complementary,
        )
        object.__setattr__(
            self,
            "accepted_decision_envelope_fingerprint",
            expected_envelope_fingerprint,
        )
        payload = self.model_dump(
            mode="json",
            exclude={"resolution_fingerprint"},
        )
        expected = _fingerprint(payload)
        if self.resolution_fingerprint and self.resolution_fingerprint != expected:
            raise ValueError("resolved Preferred Strategic Alignment fingerprint is stale")
        object.__setattr__(self, "resolution_fingerprint", expected)
        return self


class ResolvedNetworkGap(BaseModel):
    """An exact, accepted gap outcome that covers requirements without serving them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    compiler_selection: PreferredStrategicAlignment
    accepted_decision_envelope: AcceptedDecisionEnvelope
    network_role: NetworkRole
    unsatisfied_network_place_ids: tuple[str, ...] = ()
    unsatisfied_access_obligation_ids: tuple[str, ...] = ()
    unsatisfied_strategic_destination_ids: tuple[str, ...] = ()
    change_conditions: tuple[ChangeCondition, ...] = ()
    lineage_fingerprints: tuple[str, ...] = Field(min_length=1)
    gap_id: str = ""
    gap_fingerprint: str = ""

    @property
    def candidate_set_id(self) -> str:
        return self.compiler_selection.candidate_set_id

    @model_validator(mode="after")
    def bind_gap(self) -> Self:
        selection = PreferredStrategicAlignment.model_validate(
            self.compiler_selection.model_dump(mode="python")
        )
        envelope = AcceptedDecisionEnvelope.model_validate(
            self.accepted_decision_envelope.model_dump(mode="python")
        )
        object.__setattr__(self, "compiler_selection", selection)
        object.__setattr__(
            self,
            "accepted_decision_envelope",
            envelope,
        )
        if selection.disposition != SelectionDisposition.NETWORK_GAP:
            raise ValueError("an exposed Network Gap requires an exact compiler Network Gap")
        if (
            envelope.request.selection_fingerprint != selection.selection_fingerprint
            or envelope.request.candidate_set_id != selection.candidate_set_id
        ):
            raise ValueError("exposed Network Gap decision is stale for the compiler result")
        option = next(
            item
            for item in envelope.request.options
            if item.option_id == envelope.response.option_id
        )
        if option.action != AlignmentDecisionAction.EXPOSE_NETWORK_GAP:
            raise ValueError("resolved Network Gap requires the exact expose-network-gap action")
        candidate_set = selection.candidate_set
        expected_places = candidate_set.mandatory_network_place_ids
        expected_obligations = candidate_set.mandatory_access_obligation_ids
        expected_destinations = candidate_set.mandatory_strategic_destination_ids
        if (
            self.network_role != candidate_set.network_role
            or self.unsatisfied_network_place_ids != expected_places
            or self.unsatisfied_access_obligation_ids != expected_obligations
            or self.unsatisfied_strategic_destination_ids != expected_destinations
        ):
            raise ValueError(
                "resolved Network Gap must preserve the exact unsatisfied "
                "Candidate Set requirements"
            )
        expected_conditions = tuple(
            sorted(
                {
                    *selection.change_conditions,
                    ChangeCondition.LEDGER_CHANGES,
                },
                key=str,
            )
        )
        if self.change_conditions != expected_conditions:
            raise ValueError("resolved Network Gap change conditions are stale")
        expected_lineage = _canonical_ids(
            (
                selection.candidate_set.candidate_set_fingerprint,
                selection.criteria_fingerprint,
                selection.selection_fingerprint,
                envelope.envelope_fingerprint,
            ),
            "lineage_fingerprints",
            pattern=_SHA256,
        )
        if self.lineage_fingerprints != expected_lineage:
            raise ValueError("resolved Network Gap lineage is stale")
        payload = self.model_dump(
            mode="json",
            exclude={"gap_id", "gap_fingerprint"},
        )
        expected_fingerprint = _fingerprint(payload)
        expected_id = _stable_id("network-gap", payload)
        if self.gap_id and self.gap_id != expected_id:
            raise ValueError("resolved Network Gap ID is stale")
        if self.gap_fingerprint and self.gap_fingerprint != expected_fingerprint:
            raise ValueError("resolved Network Gap fingerprint is stale")
        object.__setattr__(self, "gap_id", expected_id)
        object.__setattr__(
            self,
            "gap_fingerprint",
            expected_fingerprint,
        )
        return self


class DecisionProcessMode(StrEnum):
    NO_AGENT = "no-agent-not-invoked"
    PROFILE_FALLBACK = "profile-fallback-awaiting-review"
    PROVISIONAL_REVIEW = "provisional-review-awaiting-decision"
    ACCEPTED_LEDGER = "accepted-agent-decision-ledger"


class ScenarioDecisionRecord(BaseModel):
    """Typed decision lineage; never an unexplained accepted-decision hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: DecisionProcessMode
    accepted_envelopes: tuple[AcceptedDecisionEnvelope, ...] = ()
    revision_records: tuple[DecisionRevisionRecord, ...] = ()
    runtime_attempts: tuple[RuntimeDecisionAttempt, ...] = ()
    review_run_provenance: tuple[ReviewRunLedgerProvenance, ...] = ()
    blocking_challenge_fingerprints: tuple[str, ...] = ()
    record_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_record(self) -> Self:
        envelopes = tuple(
            sorted(
                (
                    AcceptedDecisionEnvelope.model_validate(item.model_dump(mode="python"))
                    for item in self.accepted_envelopes
                ),
                key=lambda item: item.request.request_id,
            )
        )
        request_ids = tuple(item.request.request_id for item in envelopes)
        revisions = tuple(
            sorted(
                (
                    DecisionRevisionRecord.model_validate(item.model_dump(mode="python"))
                    for item in self.revision_records
                ),
                key=lambda item: (
                    item.request.request_id,
                    item.revision_fingerprint,
                ),
            )
        )
        revision_ids = tuple(
            (item.request.request_id, item.revision_fingerprint) for item in revisions
        )
        attempts = tuple(
            sorted(
                (
                    RuntimeDecisionAttempt.model_validate(item.model_dump(mode="python"))
                    for item in self.runtime_attempts
                ),
                key=lambda item: item.attempt_fingerprint,
            )
        )
        if len(set(request_ids)) != len(request_ids):
            raise ValueError("accepted decision envelopes must have unique request IDs")
        if len(set(revision_ids)) != len(revision_ids):
            raise ValueError("decision revision records must be unique")
        if len({item.attempt_fingerprint for item in attempts}) != len(attempts):
            raise ValueError("runtime decision attempts must be unique")
        invocation_pairs = tuple(
            (item.invocation_record.review_run_id, item.invocation_record.attempt_number)
            for item in attempts
            if item.invocation_record is not None
        )
        if len(set(invocation_pairs)) != len(invocation_pairs):
            raise ValueError(
                "runtime invocation records must have unique (review_run_id, attempt_number)"
            )
        expected_run_provenance = tuple(
            sorted(
                {
                    ReviewRunLedgerProvenance.from_invocation(item.invocation_record)
                    for item in attempts
                    if item.invocation_record is not None
                },
                key=lambda item: item.review_run_id,
            )
        )
        instance_provenance = {
            item.run_instance_id: {
                (
                    item.review_run_id,
                    item.run_scope_fingerprint,
                    item.run_config_fingerprint,
                    item.deadline_seconds,
                    item.maximum_attempts,
                )
            }
            for item in expected_run_provenance
        }
        for item in expected_run_provenance:
            instance_provenance.setdefault(item.run_instance_id, set()).add(
                (
                    item.review_run_id,
                    item.run_scope_fingerprint,
                    item.run_config_fingerprint,
                    item.deadline_seconds,
                    item.maximum_attempts,
                )
            )
        if any(len(values) != 1 for values in instance_provenance.values()):
            raise ValueError("each run_instance_id requires one immutable review run provenance")
        run_provenance = {
            item.review_run_id: {
                (
                    item.run_instance_id,
                    item.run_scope_fingerprint,
                    item.run_config_fingerprint,
                    item.deadline_seconds,
                    item.maximum_attempts,
                )
            }
            for item in expected_run_provenance
        }
        for item in expected_run_provenance:
            run_provenance.setdefault(item.review_run_id, set()).add(
                (
                    item.run_instance_id,
                    item.run_scope_fingerprint,
                    item.run_config_fingerprint,
                    item.deadline_seconds,
                    item.maximum_attempts,
                )
            )
        if any(len(values) != 1 for values in run_provenance.values()):
            raise ValueError("each review_run_id requires one immutable review run provenance")
        if (
            "review_run_provenance" in self.model_fields_set
            and self.review_run_provenance != expected_run_provenance
        ):
            raise ValueError("review run provenance is not runtime-ledger-derived")
        if set(request_ids) & {item.request.request_id for item in revisions}:
            raise ValueError("one request cannot appear in both accepted and revision records")
        known_challenges = {
            challenge.challenge_fingerprint
            for revision in revisions
            for challenge in revision.challenge_findings
            if challenge.severity
            in {
                ChallengeSeverity.MANDATORY_RED,
                ChallengeSeverity.MATERIAL,
            }
        }
        unresolved_challenges = {
            challenge.challenge_fingerprint
            for revision in revisions
            for challenge in revision.challenge_findings
            if challenge.severity
            in {
                ChallengeSeverity.MANDATORY_RED,
                ChallengeSeverity.MATERIAL,
            }
            and challenge.resolution == ChallengeResolution.UNRESOLVED
        }
        resolved_challenges = {
            fingerprint
            for envelope in envelopes
            for fingerprint in envelope.resolved_challenge_fingerprints
        }
        if not resolved_challenges.issubset(known_challenges):
            raise ValueError("accepted challenge resolution references no prior challenge lineage")
        requested_prior_challenges = {
            fingerprint
            for item in (*envelopes, *revisions, *attempts)
            for fingerprint in item.request.prior_challenge_fingerprints
        }
        if not requested_prior_challenges.issubset(known_challenges):
            raise ValueError("decision request references no retained prior challenge lineage")
        blocking_challenges = tuple(sorted(unresolved_challenges - resolved_challenges))
        if (
            "blocking_challenge_fingerprints" in self.model_fields_set
            and self.blocking_challenge_fingerprints != blocking_challenges
        ):
            raise ValueError("blocking challenge lineage is not ledger-derived")
        envelope_by_fingerprint = {item.envelope_fingerprint: item for item in envelopes}
        revision_by_fingerprint = {item.revision_fingerprint: item for item in revisions}
        for attempt in attempts:
            if attempt.outcome == RuntimeAttemptOutcome.ACCEPTED:
                result = envelope_by_fingerprint.get(attempt.accepted_envelope_fingerprint)
            elif attempt.outcome == RuntimeAttemptOutcome.REVISION:
                result = revision_by_fingerprint.get(attempt.revision_fingerprint)
            else:
                result = None
            if result is not None and (
                result.request.request_fingerprint != attempt.request.request_fingerprint
            ):
                raise ValueError("runtime attempt references a result for another request")
            if (attempt.outcome == RuntimeAttemptOutcome.ACCEPTED and result is None) or (
                attempt.outcome == RuntimeAttemptOutcome.REVISION and result is None
            ):
                raise ValueError("runtime attempt references no retained ledger result")
        if (self.mode == DecisionProcessMode.ACCEPTED_LEDGER) != bool(
            envelopes or revisions or attempts
        ):
            raise ValueError("only accepted-ledger mode contains decisions, revisions or attempts")
        object.__setattr__(self, "accepted_envelopes", envelopes)
        object.__setattr__(self, "revision_records", revisions)
        object.__setattr__(self, "runtime_attempts", attempts)
        object.__setattr__(self, "review_run_provenance", expected_run_provenance)
        object.__setattr__(
            self,
            "blocking_challenge_fingerprints",
            blocking_challenges,
        )
        payload = self.model_dump(mode="json", exclude={"record_fingerprint"})
        expected = _fingerprint(payload)
        if self.record_fingerprint and self.record_fingerprint != expected:
            raise ValueError("scenario decision record fingerprint is stale")
        object.__setattr__(self, "record_fingerprint", expected)
        return self


class ScenarioCriteriaBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_set_id: str = Field(pattern=_CANDIDATE_SET_ID.pattern)
    criteria_fingerprint: str = Field(pattern=_SHA256.pattern)


class CandidateSetClassification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_set_id: str = Field(pattern=_CANDIDATE_SET_ID.pattern)
    connection_id: str = Field(pattern=r"^connection-[0-9a-f]{20}$")
    disposition: CandidateSetDisposition


class ScenarioCompilation(BaseModel):
    """Whole-network, replayable scenario; still not Reference SATN authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    area_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot: GovernedEvidenceSnapshot
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    decision_record: ScenarioDecisionRecord
    candidate_sets: tuple[AlignmentCandidateSet, ...] = Field(min_length=1)
    selections: tuple[PreferredStrategicAlignment, ...] = Field(min_length=1)
    criteria_bindings: tuple[ScenarioCriteriaBinding, ...] = Field(min_length=1)
    required_network_role_ids: tuple[NetworkRole, ...] = Field(min_length=1)
    mandatory_network_place_ids: tuple[str, ...] = ()
    mandatory_access_obligation_ids: tuple[str, ...] = ()
    mandatory_strategic_destination_ids: tuple[str, ...] = ()
    lineage_fingerprints: tuple[str, ...] = ()
    replay_directive: Literal["recompile-whole-network-on-ledger-change"] = (
        "recompile-whole-network-on-ledger-change"
    )
    whole_network_criteria_fingerprint: str = ""
    scenario_context_fingerprint: str = ""
    candidate_set_classifications: tuple[CandidateSetClassification, ...] = ()
    selected_candidate_ids: tuple[str, ...] = ()
    complementary_candidate_ids: tuple[str, ...] = ()
    resolved_selections: tuple[ResolvedPreferredStrategicAlignment, ...] = ()
    network_gaps: tuple[ResolvedNetworkGap, ...] = ()
    pending_network_gap_candidate_set_ids: tuple[str, ...] = ()
    publishable: bool = Field(default=False, strict=True)
    scenario_id: str = ""
    scenario_fingerprint: str = ""

    @field_validator(
        "mandatory_network_place_ids",
        "mandatory_access_obligation_ids",
        "mandatory_strategic_destination_ids",
    )
    @classmethod
    def validate_network_ids(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        return _canonical_ids(value, getattr(info, "field_name", "network identifiers"))

    @field_validator("required_network_role_ids")
    @classmethod
    def validate_required_roles(
        cls,
        value: tuple[NetworkRole, ...],
    ) -> tuple[NetworkRole, ...]:
        if len(set(value)) != len(value):
            raise ValueError("required Network Roles must be unique")
        return tuple(sorted(value, key=str))

    @field_validator("lineage_fingerprints")
    @classmethod
    def validate_lineage(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(value, "lineage_fingerprints", pattern=_SHA256)

    @model_validator(mode="after")
    def bind_scenario(self) -> Self:
        decision_record = ScenarioDecisionRecord.model_validate(
            self.decision_record.model_dump(mode="python")
        )
        object.__setattr__(
            self,
            "decision_record",
            decision_record,
        )
        sets = tuple(
            sorted(
                (
                    AlignmentCandidateSet.model_validate(item.model_dump(mode="python"))
                    for item in self.candidate_sets
                ),
                key=lambda item: item.candidate_set_id,
            )
        )
        selections = tuple(
            sorted(
                (
                    PreferredStrategicAlignment.model_validate(item.model_dump(mode="python"))
                    for item in self.selections
                ),
                key=lambda item: item.candidate_set_id,
            )
        )
        set_ids = tuple(item.candidate_set_id for item in sets)
        if len(set(set_ids)) != len(set_ids):
            raise ValueError("Scenario Candidate Sets must be unique")
        connection_ids = tuple(item.connection_id for item in sets)
        if len(set(connection_ids)) != len(connection_ids):
            raise ValueError("Scenario allows one Candidate Set per Community Connection")
        if tuple(item.candidate_set_id for item in selections) != set_ids:
            raise ValueError("Scenario must bind every Candidate Set to one exact result")
        by_id = {item.candidate_set_id: item for item in sets}
        if any(
            selection.candidate_set != by_id[selection.candidate_set_id] for selection in selections
        ):
            raise ValueError("selection embeds a stale or foreign Candidate Set")
        if any(item.profile_fingerprint != self.profile_fingerprint for item in selections):
            raise ValueError("scenario selection is stale for the profile")
        scenario_assessments = {
            (item.kind, item.assessment_id): item for item in self.evidence_snapshot.assessments
        }
        if any(
            scenario_assessments.get((binding.kind, binding.assessment_id)) != binding
            for selection in selections
            for binding in selection.criteria.evidence_snapshot.assessments
        ):
            raise ValueError(
                "scenario evidence snapshot does not contain exact criterion assessments"
            )
        bindings = tuple(sorted(self.criteria_bindings, key=lambda item: item.candidate_set_id))
        expected_bindings = tuple(
            ScenarioCriteriaBinding(
                candidate_set_id=item.candidate_set_id,
                criteria_fingerprint=item.criteria_fingerprint,
            )
            for item in selections
        )
        if bindings != expected_bindings:
            raise ValueError("criteria bindings must exactly match per-set selection evidence")
        aggregate = _fingerprint([item.model_dump(mode="json") for item in expected_bindings])
        if (
            self.whole_network_criteria_fingerprint
            and self.whole_network_criteria_fingerprint != aggregate
        ):
            raise ValueError("whole-network criteria fingerprint is stale")

        accepted_option_by_set: dict[str, AlignmentDecisionOption] = {}
        for envelope in decision_record.accepted_envelopes:
            option = next(
                item
                for item in envelope.request.options
                if item.option_id == envelope.response.option_id
            )
            candidate_set_id = envelope.request.candidate_set_id
            if candidate_set_id in accepted_option_by_set:
                raise ValueError("a Candidate Set cannot accept more than one decision")
            accepted_option_by_set[candidate_set_id] = option

        network_gaps: list[ResolvedNetworkGap] = []
        pending_gap_candidate_set_ids: list[str] = []
        for selection in selections:
            if selection.disposition != SelectionDisposition.NETWORK_GAP:
                continue
            option = accepted_option_by_set.get(selection.candidate_set_id)
            envelope = next(
                (
                    item
                    for item in decision_record.accepted_envelopes
                    if item.request.candidate_set_id == selection.candidate_set_id
                ),
                None,
            )
            if (
                option is None
                or option.action != AlignmentDecisionAction.EXPOSE_NETWORK_GAP
                or envelope is None
            ):
                pending_gap_candidate_set_ids.append(selection.candidate_set_id)
                continue
            conditions = tuple(
                sorted(
                    {
                        *selection.change_conditions,
                        ChangeCondition.LEDGER_CHANGES,
                    },
                    key=str,
                )
            )
            lineage = _canonical_ids(
                (
                    selection.candidate_set.candidate_set_fingerprint,
                    selection.criteria_fingerprint,
                    selection.selection_fingerprint,
                    envelope.envelope_fingerprint,
                ),
                "lineage_fingerprints",
                pattern=_SHA256,
            )
            network_gaps.append(
                ResolvedNetworkGap(
                    compiler_selection=selection,
                    accepted_decision_envelope=envelope,
                    network_role=selection.candidate_set.network_role,
                    unsatisfied_network_place_ids=(
                        selection.candidate_set.mandatory_network_place_ids
                    ),
                    unsatisfied_access_obligation_ids=(
                        selection.candidate_set.mandatory_access_obligation_ids
                    ),
                    unsatisfied_strategic_destination_ids=(
                        selection.candidate_set.mandatory_strategic_destination_ids
                    ),
                    change_conditions=conditions,
                    lineage_fingerprints=lineage,
                )
            )
        expected_network_gaps = tuple(
            sorted(
                network_gaps,
                key=lambda item: item.candidate_set_id,
            )
        )
        expected_pending_gap_ids = tuple(sorted(pending_gap_candidate_set_ids))

        winners: list[AlignmentCandidateInput] = []
        winner_ids_by_set: dict[str, tuple[str, ...]] = {}
        for selection in selections:
            default_winner_ids = (
                (selection.selected_candidate_id,) if selection.selected_candidate_id else ()
            )
            accepted_option = accepted_option_by_set.get(selection.candidate_set_id)
            if (
                accepted_option is not None
                and accepted_option.action == AlignmentDecisionAction.RETAIN_COMPLEMENTARY_SET
            ):
                winner_ids = accepted_option.complementary_candidate_ids
            elif (
                accepted_option is not None
                and accepted_option.action == AlignmentDecisionAction.SELECT_ELIGIBLE_OPTION
                and accepted_option.candidate_id is not None
            ):
                winner_ids = (accepted_option.candidate_id,)
            else:
                winner_ids = default_winner_ids
            candidates = {
                item.candidate_id: item for item in selection.candidate_set.admitted_candidates
            }
            if not set(winner_ids).issubset(candidates):
                raise ValueError("accepted decision contains a foreign alignment candidate")
            winner_ids_by_set[selection.candidate_set_id] = winner_ids
            winners.extend(candidates[item] for item in winner_ids)
        winner_ids = tuple(item.candidate_id for item in winners)
        if len(set(winner_ids)) != len(winner_ids):
            raise ValueError("a candidate cannot win more than one Community Connection")
        gap_selections = tuple(
            item for item in selections if item.disposition == SelectionDisposition.NETWORK_GAP
        )
        gap_roles = {item.candidate_set.network_role for item in gap_selections}
        gap_places = {
            identifier
            for item in gap_selections
            for identifier in (item.candidate_set.mandatory_network_place_ids)
        }
        gap_obligations = {
            identifier
            for item in gap_selections
            for identifier in (item.candidate_set.mandatory_access_obligation_ids)
        }
        gap_destinations = {
            identifier
            for item in gap_selections
            for identifier in (item.candidate_set.mandatory_strategic_destination_ids)
        }
        if not set(self.required_network_role_ids).issubset(
            {item.network_role for item in winners} | gap_roles
        ):
            raise ValueError("required network roles are neither selected nor gap-covered")
        if not set(self.mandatory_network_place_ids).issubset(
            {identifier for item in winners for identifier in item.served_network_place_ids}
            | gap_places
        ):
            raise ValueError("mandatory Network Places are neither selected nor gap-covered")
        if not set(self.mandatory_access_obligation_ids).issubset(
            {identifier for item in winners for identifier in item.served_access_obligation_ids}
            | gap_obligations
        ):
            raise ValueError("mandatory Access Obligations are neither selected nor gap-covered")
        if not set(self.mandatory_strategic_destination_ids).issubset(
            {identifier for item in winners for identifier in item.served_strategic_destination_ids}
            | gap_destinations
        ):
            raise ValueError(
                "mandatory Strategic Destinations are neither selected nor gap-covered"
            )
        classifications: list[CandidateSetClassification] = []
        selected_ids: list[str] = []
        complementary_ids: list[str] = []
        for selection in selections:
            candidate_set = selection.candidate_set
            disposition = _role_disposition(candidate_set.network_role)
            selection_winner_ids = winner_ids_by_set[selection.candidate_set_id]
            accepted_option = accepted_option_by_set.get(selection.candidate_set_id)
            retained_as_set = (
                accepted_option is not None
                and accepted_option.action == AlignmentDecisionAction.RETAIN_COMPLEMENTARY_SET
            )
            without = [item for item in winners if item.candidate_id not in selection_winner_ids]
            loses_role = selection.candidate_set.network_role not in {
                item.network_role for item in without
            }
            required_places = set(selection.candidate_set.mandatory_network_place_ids)
            required_places.update(self.mandatory_network_place_ids)
            if selection.candidate_set.network_role == NetworkRole.COMMUNITY_ACCESS:
                required_places.update(selection.candidate_set.endpoints)
            loses_place = bool(
                required_places
                - {identifier for item in without for identifier in item.served_network_place_ids}
            )
            loses_obligation = bool(
                (
                    set(selection.candidate_set.mandatory_access_obligation_ids)
                    | set(self.mandatory_access_obligation_ids)
                )
                - {
                    identifier
                    for item in without
                    for identifier in item.served_access_obligation_ids
                }
            )
            loses_destination = bool(
                (
                    set(selection.candidate_set.mandatory_strategic_destination_ids)
                    | set(self.mandatory_strategic_destination_ids)
                )
                - {
                    identifier
                    for item in without
                    for identifier in item.served_strategic_destination_ids
                }
            )
            distinct_required_function = (
                (
                    selection.candidate_set.network_role in self.required_network_role_ids
                    and loses_role
                )
                or loses_place
                or loses_obligation
                or loses_destination
            )
            if (
                disposition == CandidateSetDisposition.COMPLEMENTARY_REQUIRED
                and not distinct_required_function
            ):
                disposition = CandidateSetDisposition.SUBSTITUTE_ALTERNATIVES
            if retained_as_set:
                disposition = CandidateSetDisposition.COMPLEMENTARY_REQUIRED
            classifications.append(
                CandidateSetClassification(
                    candidate_set_id=candidate_set.candidate_set_id,
                    connection_id=candidate_set.connection_id,
                    disposition=disposition,
                )
            )
            for winner_id in selection_winner_ids:
                if disposition == CandidateSetDisposition.COMPLEMENTARY_REQUIRED:
                    complementary_ids.append(winner_id)
                else:
                    selected_ids.append(winner_id)

        expected_classifications = tuple(
            sorted(classifications, key=lambda item: item.candidate_set_id)
        )
        expected_selected_ids = tuple(sorted(selected_ids))
        expected_complementary_ids = tuple(sorted(complementary_ids))
        if (
            "candidate_set_classifications" in self.model_fields_set
            and self.candidate_set_classifications != expected_classifications
        ):
            raise ValueError("Candidate Set classifications are not compiler-derived")
        if (
            "selected_candidate_ids" in self.model_fields_set
            and self.selected_candidate_ids != expected_selected_ids
        ):
            raise ValueError("scenario selected candidate IDs are not compiler-derived")
        if (
            "complementary_candidate_ids" in self.model_fields_set
            and self.complementary_candidate_ids != expected_complementary_ids
        ):
            raise ValueError("scenario complementary candidate IDs are not compiler-derived")

        context_payload = {
            "area_fingerprint": self.area_fingerprint,
            "evidence_snapshot_fingerprint": self.evidence_snapshot.snapshot_fingerprint,
            "profile_fingerprint": self.profile_fingerprint,
            "candidate_set_fingerprints": tuple(item.candidate_set_fingerprint for item in sets),
            "selection_fingerprints": tuple(item.selection_fingerprint for item in selections),
            "required_network_role_ids": self.required_network_role_ids,
            "mandatory_network_place_ids": self.mandatory_network_place_ids,
            "mandatory_access_obligation_ids": self.mandatory_access_obligation_ids,
            "mandatory_strategic_destination_ids": (self.mandatory_strategic_destination_ids),
        }
        scenario_context = _fingerprint(context_payload)
        if (
            self.scenario_context_fingerprint
            and self.scenario_context_fingerprint != scenario_context
        ):
            raise ValueError("scenario context fingerprint is stale")
        by_set_id = {item.candidate_set_id: item for item in selections}
        for envelope in (
            *self.decision_record.accepted_envelopes,
            *self.decision_record.revision_records,
            *self.decision_record.runtime_attempts,
        ):
            selection = by_set_id.get(envelope.request.candidate_set_id)
            if selection is None:
                raise ValueError("accepted decision envelope is foreign or stale")
            try:
                expected_request = build_alignment_decision_request(
                    selection,
                    scenario_context_fingerprint=scenario_context,
                    prior_challenge_fingerprints=(envelope.request.prior_challenge_fingerprints),
                )
            except ValueError as error:
                raise ValueError(
                    "a clear no-agent selection cannot contain a decision envelope"
                ) from error
            if envelope.request != expected_request:
                raise ValueError(
                    "accepted decision request is not the exact compiler-generated menu"
                )
            admitted_ids = {
                item.candidate_id for item in selection.candidate_set.admitted_candidates
            }
            option_candidate_ids = {
                candidate_id
                for item in envelope.request.options
                for candidate_id in (
                    *((item.candidate_id,) if item.candidate_id else ()),
                    *item.complementary_candidate_ids,
                    *item.analysis_candidate_ids,
                )
            }
            known_evidence_ids = {item.assessment_id for item in self.evidence_snapshot.assessments}
            if not isinstance(selection.criteria, CandidateSetGapEvidence):
                known_evidence_ids |= {
                    item.evidence_record_id
                    for item in (
                        *selection.criteria.education.completeness,
                        *selection.criteria.education.independent_travel_opportunity,
                        *selection.criteria.directness,
                        *selection.criteria.gradient,
                        *selection.criteria.uncertainty,
                    )
                }
                known_evidence_ids |= {
                    item.assessment_option_id
                    for item in (
                        *selection.criteria.population.headline_500m,
                        *selection.criteria.population.sensitivity_1000m,
                    )
                }
            if not option_candidate_ids.issubset(admitted_ids):
                raise ValueError("decision option names a foreign alignment candidate")
            if not set(envelope.request.immutable_evidence_ids).issubset(known_evidence_ids):
                raise ValueError("decision request names foreign governed evidence")

        envelope_by_set = {
            item.request.candidate_set_id: item for item in decision_record.accepted_envelopes
        }
        resolved_selections: list[ResolvedPreferredStrategicAlignment] = []
        for selection in selections:
            envelope = envelope_by_set.get(selection.candidate_set_id)
            if envelope is None:
                if not selection.publishable:
                    continue
                selected_candidate_id = selection.selected_candidate_id
                complementary_candidate_ids: tuple[str, ...] = ()
                resolution_action = None
            else:
                option = next(
                    item
                    for item in envelope.request.options
                    if item.option_id == envelope.response.option_id
                )
                if option.action == AlignmentDecisionAction.SELECT_ELIGIBLE_OPTION:
                    selected_candidate_id = option.candidate_id
                    complementary_candidate_ids = ()
                elif option.action == AlignmentDecisionAction.RETAIN_COMPLEMENTARY_SET:
                    selected_candidate_id = None
                    complementary_candidate_ids = option.complementary_candidate_ids
                elif option.action == AlignmentDecisionAction.ACCEPT_PROFILE_FALLBACK:
                    selected_candidate_id = selection.selected_candidate_id
                    complementary_candidate_ids = ()
                else:
                    continue
                resolution_action = option.action
            resolved_selections.append(
                ResolvedPreferredStrategicAlignment(
                    compiler_selection=selection,
                    accepted_decision_envelope=envelope,
                    selected_candidate_id=selected_candidate_id,
                    complementary_candidate_ids=(complementary_candidate_ids),
                    resolution_action=resolution_action,
                    accepted_decision_envelope_fingerprint=(
                        envelope.envelope_fingerprint if envelope is not None else ""
                    ),
                )
            )
        expected_resolved = tuple(
            sorted(
                resolved_selections,
                key=lambda item: item.candidate_set_id,
            )
        )
        resolved_set_ids = {item.candidate_set_id for item in expected_resolved}
        gap_set_ids = {item.candidate_set_id for item in expected_network_gaps}
        if resolved_set_ids & gap_set_ids:
            raise ValueError("a Candidate Set cannot be both alignment-resolved and gap-resolved")
        scenario_publishable = (
            resolved_set_ids | gap_set_ids == set(set_ids)
            and not expected_pending_gap_ids
            and not decision_record.blocking_challenge_fingerprints
        )
        if (
            "resolved_selections" in self.model_fields_set
            and self.resolved_selections != expected_resolved
        ):
            raise ValueError("scenario resolved selections are not compiler-and-ledger-derived")
        if "network_gaps" in self.model_fields_set and self.network_gaps != expected_network_gaps:
            raise ValueError("scenario Network Gaps are not compiler-and-ledger-derived")
        if (
            "pending_network_gap_candidate_set_ids" in self.model_fields_set
            and self.pending_network_gap_candidate_set_ids != expected_pending_gap_ids
        ):
            raise ValueError("pending Network Gap IDs are not compiler-derived")
        if "publishable" in self.model_fields_set and self.publishable != scenario_publishable:
            raise ValueError("scenario publishability is stale")

        if decision_record.mode == DecisionProcessMode.NO_AGENT and any(
            item.decision_action != SelectionAction.NO_AGENT_CLEAR for item in selections
        ):
            raise ValueError("no-agent scenario contains a fallback or unresolved selection")
        if decision_record.mode == DecisionProcessMode.PROFILE_FALLBACK and not any(
            item.decision_action == SelectionAction.PROFILE_FALLBACK for item in selections
        ):
            raise ValueError("fallback scenario must contain a provisional profile action")
        if decision_record.mode == DecisionProcessMode.PROVISIONAL_REVIEW and not any(
            not item.publishable for item in selections
        ):
            raise ValueError("provisional review requires an unresolved selection")
        object.__setattr__(self, "candidate_sets", sets)
        object.__setattr__(self, "selections", selections)
        object.__setattr__(self, "criteria_bindings", bindings)
        object.__setattr__(self, "whole_network_criteria_fingerprint", aggregate)
        object.__setattr__(self, "scenario_context_fingerprint", scenario_context)
        object.__setattr__(
            self,
            "candidate_set_classifications",
            expected_classifications,
        )
        object.__setattr__(self, "selected_candidate_ids", expected_selected_ids)
        object.__setattr__(
            self,
            "complementary_candidate_ids",
            expected_complementary_ids,
        )
        object.__setattr__(
            self,
            "resolved_selections",
            expected_resolved,
        )
        object.__setattr__(self, "network_gaps", expected_network_gaps)
        object.__setattr__(
            self,
            "pending_network_gap_candidate_set_ids",
            expected_pending_gap_ids,
        )
        object.__setattr__(
            self,
            "publishable",
            scenario_publishable,
        )
        payload = self.model_dump(
            mode="json",
            exclude={"scenario_id", "scenario_fingerprint"},
        )
        fingerprint = _fingerprint(payload)
        identifier = _stable_id("scenario", payload)
        if self.scenario_id and self.scenario_id != identifier:
            raise ValueError("scenario_id is stale")
        if self.scenario_fingerprint and self.scenario_fingerprint != fingerprint:
            raise ValueError("scenario_fingerprint is stale")
        object.__setattr__(self, "scenario_id", identifier)
        object.__setattr__(self, "scenario_fingerprint", fingerprint)
        return self


class ScenarioReviewDependency(BaseModel):
    """Data-only dependency edge used to derive a bounded review frontier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_set_id: str = Field(pattern=_CANDIDATE_SET_ID.pattern)
    depends_on_candidate_set_ids: tuple[str, ...] = ()

    @field_validator("depends_on_candidate_set_ids")
    @classmethod
    def canonical_dependencies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(
            value,
            "depends_on_candidate_set_ids",
            pattern=_CANDIDATE_SET_ID,
        )


class ScenarioReviewRoundHistory(BaseModel):
    """Immutable trace of a prior whole-scenario review round."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    round_number: int = Field(ge=1, strict=True)
    scenario_fingerprint: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    decision_record_fingerprint: str = Field(pattern=_SHA256.pattern)
    request_fingerprints: tuple[str, ...] = ()
    frontier_fingerprint: str = Field(pattern=_SHA256.pattern)
    prior_orchestration_fingerprint: str = ""
    compiler_attestation: str = ""
    history_fingerprint: str = ""

    @field_validator("request_fingerprints")
    @classmethod
    def canonical_requests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(
            value,
            "request_fingerprints",
            pattern=_SHA256,
        )

    @model_validator(mode="after")
    def bind_history(self) -> Self:
        if self.prior_orchestration_fingerprint and not _SHA256.fullmatch(
            self.prior_orchestration_fingerprint
        ):
            raise ValueError("prior orchestration fingerprint is malformed")
        attestation_payload = self.model_dump(
            mode="json",
            exclude={"compiler_attestation", "history_fingerprint"},
        )
        attestation = _fingerprint(
            {
                "compiler_contract": "satn-scenario-review-chain/v1",
                "round": attestation_payload,
            }
        )
        if self.compiler_attestation and self.compiler_attestation != attestation:
            raise ValueError("review round compiler attestation is stale")
        object.__setattr__(self, "compiler_attestation", attestation)
        payload = self.model_dump(mode="json", exclude={"history_fingerprint"})
        expected = _fingerprint(payload)
        if self.history_fingerprint and self.history_fingerprint != expected:
            raise ValueError("review round history fingerprint is stale")
        object.__setattr__(self, "history_fingerprint", expected)
        return self


class HumanInterventionReason(StrEnum):
    MAXIMUM_REVIEW_ROUNDS = "maximum-review-rounds-exhausted"
    CYCLIC_DEPENDENCY = "cyclic-review-dependency"
    MISSING_UPSTREAM_DECISION = "missing-upstream-decision"
    UNRESOLVED_CONVERGENCE = "unresolved-review-convergence"


class HumanInterventionRequest(BaseModel):
    """Typed terminal outcome when deterministic bounded review cannot proceed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: HumanInterventionReason
    round_number: int = Field(ge=1, strict=True)
    affected_candidate_set_ids: tuple[str, ...] = Field(min_length=1)
    scenario_fingerprint: str = Field(pattern=_SHA256.pattern)
    missing_evidence_ids: tuple[str, ...] = ()
    attempted_revision_fingerprints: tuple[str, ...] = ()
    available_compiler_choice_option_ids: tuple[str, ...] = ()
    smallest_required_human_input: str = Field(min_length=1)
    blocking_challenge_ids: tuple[str, ...] = ()
    lineage_fingerprints: tuple[str, ...] = Field(min_length=1)
    intervention_id: str = ""
    intervention_fingerprint: str = ""

    @field_validator("affected_candidate_set_ids")
    @classmethod
    def canonical_affected_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(
            value,
            "affected_candidate_set_ids",
            pattern=_CANDIDATE_SET_ID,
        )

    @field_validator("lineage_fingerprints")
    @classmethod
    def canonical_intervention_lineage(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(value, "lineage_fingerprints", pattern=_SHA256)

    @field_validator(
        "missing_evidence_ids",
        "available_compiler_choice_option_ids",
        "blocking_challenge_ids",
    )
    @classmethod
    def canonical_intervention_ids(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        return _canonical_ids(value, getattr(info, "field_name", "intervention IDs"))

    @field_validator("attempted_revision_fingerprints")
    @classmethod
    def canonical_revision_fingerprints(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _canonical_ids(
            value,
            "attempted_revision_fingerprints",
            pattern=_SHA256,
        )

    @model_validator(mode="after")
    def bind_intervention(self) -> Self:
        payload = self.model_dump(
            mode="json",
            exclude={"intervention_id", "intervention_fingerprint"},
        )
        identifier = _stable_id("human-intervention", payload)
        fingerprint = _fingerprint(payload)
        if self.intervention_id and self.intervention_id != identifier:
            raise ValueError("human intervention request ID is stale")
        if self.intervention_fingerprint and self.intervention_fingerprint != fingerprint:
            raise ValueError("human intervention request fingerprint is stale")
        object.__setattr__(self, "intervention_id", identifier)
        object.__setattr__(self, "intervention_fingerprint", fingerprint)
        return self


class ScenarioReviewRequestState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: AlignmentDecisionRequest
    blocked_by_candidate_set_ids: tuple[str, ...] = ()

    @field_validator("blocked_by_candidate_set_ids")
    @classmethod
    def canonical_blockers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(
            value,
            "blocked_by_candidate_set_ids",
            pattern=_CANDIDATE_SET_ID,
        )


def review_session_scope_fingerprint(
    scenario: ScenarioCompilation,
    dependencies: tuple[ScenarioReviewDependency, ...],
) -> str:
    """Canonical local scope for a bounded compile or replay run."""
    dependencies = tuple(sorted(dependencies, key=lambda item: item.candidate_set_id))
    return _fingerprint(
        {
            "area_fingerprint": scenario.area_fingerprint,
            "profile_fingerprint": scenario.profile_fingerprint,
            "evidence_snapshot_fingerprint": (scenario.evidence_snapshot.snapshot_fingerprint),
            "candidate_set_fingerprints": tuple(
                item.candidate_set_fingerprint for item in scenario.candidate_sets
            ),
            "selection_fingerprints": tuple(
                item.selection_fingerprint for item in scenario.selections
            ),
            "dependencies": [item.model_dump(mode="json") for item in dependencies],
        }
    )


def _review_frontier_fingerprint(
    actionable: tuple[ScenarioReviewRequestState, ...],
    nonactionable: tuple[ScenarioReviewRequestState, ...],
) -> str:
    return _fingerprint([item.model_dump(mode="json") for item in (*actionable, *nonactionable)])


class ReviewRun(BaseModel):
    """Local, deterministic scope for one compile or replay run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = ""
    run_instance_id: str = Field(pattern=_ID.pattern)
    run_scope_fingerprint: str = Field(pattern=_SHA256.pattern)
    prior_orchestration_fingerprint: str = ""
    deadline_seconds: float = Field(default=30.0, gt=0, le=300)
    maximum_attempts: int = Field(default=3, ge=1, le=10, strict=True)
    run_config_fingerprint: str = ""
    run_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_run(self) -> Self:
        if (
            self.prior_orchestration_fingerprint
            and _SHA256.fullmatch(self.prior_orchestration_fingerprint) is None
        ):
            raise ValueError("review run prior orchestration fingerprint is malformed")
        config_fingerprint = _review_run_config_fingerprint(
            self.deadline_seconds,
            self.maximum_attempts,
        )
        if self.run_config_fingerprint and self.run_config_fingerprint != config_fingerprint:
            raise ValueError("review run config fingerprint is stale")
        object.__setattr__(self, "run_config_fingerprint", config_fingerprint)
        payload = self.model_dump(mode="json", exclude={"run_id", "run_fingerprint"})
        run_id = _stable_id("review-run", payload)
        if self.run_id and self.run_id != run_id:
            raise ValueError("review run ID is stale")
        fingerprint = _fingerprint({**payload, "run_id": run_id})
        if self.run_fingerprint and self.run_fingerprint != fingerprint:
            raise ValueError("review run fingerprint is stale")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "run_fingerprint", fingerprint)
        return self


class ScenarioReviewOrchestration(BaseModel):
    """Deterministic dependency frontier with profile-bounded agent review."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: ScenarioCompilation
    dependencies: tuple[ScenarioReviewDependency, ...]
    review_run: ReviewRun
    prior_orchestration: ScenarioReviewOrchestration | None = None
    round_history: tuple[ScenarioReviewRoundHistory, ...] = ()
    round_number: int = 1
    actionable_requests: tuple[ScenarioReviewRequestState, ...] = ()
    nonactionable_requests: tuple[ScenarioReviewRequestState, ...] = ()
    human_intervention_request: HumanInterventionRequest | None = None
    converged: bool = Field(default=False, strict=True)
    replay_directive: Literal["recompile-whole-network-on-ledger-change"] = (
        "recompile-whole-network-on-ledger-change"
    )
    orchestration_fingerprint: str = ""

    @property
    def review_run_id(self) -> str:
        return self.review_run.run_id

    @model_validator(mode="after")
    def bind_orchestration(self) -> Self:
        scenario = ScenarioCompilation.model_validate(self.scenario.model_dump(mode="python"))
        dependencies = tuple(sorted(self.dependencies, key=lambda item: item.candidate_set_id))
        if len({item.candidate_set_id for item in dependencies}) != len(dependencies):
            raise ValueError("review dependencies must have unique Candidate Set IDs")
        run = ReviewRun.model_validate(self.review_run.model_dump(mode="python"))
        expected_scope = review_session_scope_fingerprint(scenario, dependencies)
        if run.run_scope_fingerprint != expected_scope:
            raise ValueError("review run is scoped to another scenario")
        retained_instances = tuple(
            item
            for item in scenario.decision_record.review_run_provenance
            if item.run_instance_id == run.run_instance_id
        )
        if retained_instances:
            if any(
                item.run_scope_fingerprint != run.run_scope_fingerprint
                or item.run_config_fingerprint != run.run_config_fingerprint
                for item in retained_instances
            ):
                raise ValueError(
                    "retained run_instance_id has different immutable scope or configuration"
                )
            raise ValueError(
                "retained runtime ledger already owns run_instance_id; "
                "use an unseen instance for replay"
            )
        prior = (
            ScenarioReviewOrchestration.model_validate(
                self.prior_orchestration.model_dump(mode="python")
            )
            if self.prior_orchestration is not None
            else None
        )
        if prior is None:
            if run.prior_orchestration_fingerprint:
                raise ValueError("review genesis cannot name a prior orchestration")
            history: tuple[ScenarioReviewRoundHistory, ...] = ()
            delta_accepted: tuple[AcceptedDecisionEnvelope, ...] = ()
            delta_revisions: tuple[DecisionRevisionRecord, ...] = ()
            delta_attempts: tuple[RuntimeDecisionAttempt, ...] = ()
        else:
            if run.run_instance_id == prior.review_run.run_instance_id:
                raise ValueError("fresh review replay requires a distinct run_instance_id")
            if (
                run.prior_orchestration_fingerprint != prior.orchestration_fingerprint
                or scenario.profile_fingerprint != prior.scenario.profile_fingerprint
                or scenario.evidence_snapshot.snapshot_fingerprint
                != prior.scenario.evidence_snapshot.snapshot_fingerprint
                or dependencies != prior.dependencies
            ):
                raise ValueError(
                    "next review round is stale for its anchored session/profile/evidence"
                )
            previous_accepted = {
                item.envelope_fingerprint: item
                for item in prior.scenario.decision_record.accepted_envelopes
            }
            current_accepted = {
                item.envelope_fingerprint: item
                for item in scenario.decision_record.accepted_envelopes
            }
            previous_revisions = {
                item.revision_fingerprint: item
                for item in prior.scenario.decision_record.revision_records
            }
            current_revisions = {
                item.revision_fingerprint: item
                for item in scenario.decision_record.revision_records
            }
            previous_attempts = {
                item.attempt_fingerprint: item
                for item in prior.scenario.decision_record.runtime_attempts
            }
            current_attempts = {
                item.attempt_fingerprint: item for item in scenario.decision_record.runtime_attempts
            }
            if (
                any(current_accepted.get(key) != value for key, value in previous_accepted.items())
                or any(
                    current_revisions.get(key) != value for key, value in previous_revisions.items()
                )
                or any(
                    current_attempts.get(key) != value for key, value in previous_attempts.items()
                )
            ):
                raise ValueError("next review ledger must preserve every prior entry byte-for-byte")
            delta_accepted = tuple(
                current_accepted[key]
                for key in sorted(current_accepted.keys() - previous_accepted.keys())
            )
            delta_revisions = tuple(
                current_revisions[key]
                for key in sorted(current_revisions.keys() - previous_revisions.keys())
            )
            delta_attempts = tuple(
                current_attempts[key]
                for key in sorted(current_attempts.keys() - previous_attempts.keys())
            )
            if not (delta_accepted or delta_revisions or delta_attempts):
                raise ValueError(
                    "next review round requires a nonempty counted decision-attempt delta"
                )
            offered_request_fingerprints = {
                item.request.request_fingerprint for item in prior.actionable_requests
            }
            delta_request_fingerprints = {
                item.request.request_fingerprint
                for item in (
                    *delta_accepted,
                    *delta_revisions,
                    *delta_attempts,
                )
            }
            if not delta_request_fingerprints or not delta_request_fingerprints.issubset(
                offered_request_fingerprints
            ):
                raise ValueError("next review delta contains an attempt outside the prior frontier")
            frontier_fingerprint = _review_frontier_fingerprint(
                prior.actionable_requests,
                prior.nonactionable_requests,
            )
            if any(
                attempt.invocation_record is not None
                and (
                    attempt.invocation_record.review_run_id != prior.review_run.run_id
                    or attempt.invocation_record.run_instance_id != prior.review_run.run_instance_id
                    or attempt.invocation_record.run_scope_fingerprint
                    != prior.review_run.run_scope_fingerprint
                    or attempt.invocation_record.run_config_fingerprint
                    != prior.review_run.run_config_fingerprint
                    or attempt.invocation_record.maximum_attempts
                    != prior.review_run.maximum_attempts
                    or attempt.invocation_record.deadline_seconds
                    != prior.review_run.deadline_seconds
                    or attempt.invocation_record.frontier_fingerprint != frontier_fingerprint
                )
                for attempt in delta_attempts
            ):
                raise ValueError(
                    "provider invocation record is stale for the prior review frontier"
                )
            if len(delta_attempts) > prior.review_run.maximum_attempts:
                raise ValueError("review run exceeds its maximum recorded runtime attempts")
            invocation_records = tuple(
                attempt.invocation_record
                for attempt in delta_attempts
                if attempt.invocation_record is not None
            )
            if invocation_records:
                attempt_numbers = tuple(
                    sorted(record.attempt_number for record in invocation_records)
                )
                if attempt_numbers != tuple(range(1, len(invocation_records) + 1)):
                    raise ValueError(
                        "runtime invocation records must use one contiguous local attempt sequence"
                    )
            prior_record = ScenarioReviewRoundHistory(
                round_number=prior.round_number,
                scenario_fingerprint=prior.scenario.scenario_fingerprint,
                profile_fingerprint=prior.scenario.profile_fingerprint,
                evidence_snapshot_fingerprint=(
                    prior.scenario.evidence_snapshot.snapshot_fingerprint
                ),
                decision_record_fingerprint=(prior.scenario.decision_record.record_fingerprint),
                request_fingerprints=tuple(sorted(offered_request_fingerprints)),
                frontier_fingerprint=frontier_fingerprint,
                prior_orchestration_fingerprint=(
                    prior.prior_orchestration.orchestration_fingerprint
                    if prior.prior_orchestration is not None
                    else ""
                ),
            )
            history = (*prior.round_history, prior_record)
        round_number = len(history) + 1
        all_set_ids = {item.candidate_set_id for item in scenario.selections}
        dependency_by_set = {
            item.candidate_set_id: set(item.depends_on_candidate_set_ids) for item in dependencies
        }
        missing_records = all_set_ids - set(dependency_by_set)
        foreign_ids = (set(dependency_by_set) - all_set_ids) | {
            item
            for values in dependency_by_set.values()
            for item in values
            if item not in all_set_ids
        }
        resolved_ids = {item.candidate_set_id for item in scenario.resolved_selections} | {
            item.candidate_set_id for item in scenario.network_gaps
        }
        unresolved_ids = all_set_ids - resolved_ids
        requests = {
            item.candidate_set_id: build_alignment_decision_request(
                item,
                scenario_context_fingerprint=scenario.scenario_context_fingerprint,
            )
            for item in scenario.selections
            if item.candidate_set_id in unresolved_ids
        }

        def has_cycle() -> bool:
            remaining = {
                identifier: set(values) & unresolved_ids
                for identifier, values in dependency_by_set.items()
                if identifier in unresolved_ids
            }
            while remaining:
                roots = {identifier for identifier, values in remaining.items() if not values}
                if not roots:
                    return True
                remaining = {
                    identifier: values - roots
                    for identifier, values in remaining.items()
                    if identifier not in roots
                }
            return False

        intervention_reason: HumanInterventionReason | None = None
        affected = unresolved_ids
        if unresolved_ids and (missing_records or foreign_ids):
            intervention_reason = HumanInterventionReason.MISSING_UPSTREAM_DECISION
            affected = (missing_records | foreign_ids | unresolved_ids) & all_set_ids
        elif unresolved_ids and has_cycle():
            intervention_reason = HumanInterventionReason.CYCLIC_DEPENDENCY
        ambiguity = scenario.candidate_sets[0].profile.ambiguity
        if (
            unresolved_ids
            and intervention_reason is None
            and len(history) >= ambiguity.maximum_review_rounds
        ):
            intervention_reason = HumanInterventionReason.MAXIMUM_REVIEW_ROUNDS
        if (
            unresolved_ids
            and intervention_reason is None
            and prior is not None
            and (delta_accepted or delta_revisions)
            and all(
                next(
                    option
                    for option in envelope.request.options
                    if option.option_id == envelope.response.option_id
                ).action
                in {
                    AlignmentDecisionAction.RUN_ADDITIONAL_ANALYSIS,
                    AlignmentDecisionAction.REQUEST_HUMAN_INTERVENTION,
                    AlignmentDecisionAction.TERMINATE,
                }
                for envelope in (
                    *delta_accepted,
                    *delta_revisions,
                )
            )
        ):
            intervention_reason = HumanInterventionReason.UNRESOLVED_CONVERGENCE

        actionable: tuple[ScenarioReviewRequestState, ...] = ()
        nonactionable: tuple[ScenarioReviewRequestState, ...] = ()
        intervention: HumanInterventionRequest | None = None
        revision_records = scenario.decision_record.revision_records
        missing_evidence_ids = tuple(
            sorted(
                {
                    identifier
                    for record in revision_records
                    for challenge in record.challenge_findings
                    for identifier in challenge.missing_evidence_ids
                }
            )
        )
        attempted_revision_fingerprints = tuple(
            sorted(
                {record.revision_fingerprint for record in revision_records}
                | {
                    fingerprint
                    for record in revision_records
                    for fingerprint in record.attempted_revision_fingerprints
                }
            )
        )
        blocking_challenge_ids = tuple(
            sorted(
                {
                    challenge.challenge_id
                    for record in revision_records
                    for challenge in record.challenge_findings
                    if challenge.resolution == ChallengeResolution.UNRESOLVED
                }
            )
        )
        available_choice_ids = tuple(
            sorted(
                {option.option_id for request in requests.values() for option in request.options}
            )
        )
        smallest_human_input = (
            "choose one compiler-authored option or provide the named missing evidence"
        )
        if intervention_reason is not None:
            intervention = HumanInterventionRequest(
                reason=intervention_reason,
                round_number=round_number,
                affected_candidate_set_ids=tuple(sorted(affected)),
                scenario_fingerprint=scenario.scenario_fingerprint,
                missing_evidence_ids=missing_evidence_ids,
                attempted_revision_fingerprints=(attempted_revision_fingerprints),
                available_compiler_choice_option_ids=available_choice_ids,
                smallest_required_human_input=smallest_human_input,
                blocking_challenge_ids=blocking_challenge_ids,
                lineage_fingerprints=tuple(
                    sorted(
                        {
                            scenario.scenario_fingerprint,
                            scenario.decision_record.record_fingerprint,
                            *attempted_revision_fingerprints,
                            *(item.history_fingerprint for item in history),
                        }
                    )
                ),
            )
        elif unresolved_ids:
            frontier = tuple(
                sorted(
                    identifier
                    for identifier in unresolved_ids
                    if dependency_by_set[identifier].issubset(resolved_ids)
                )
            )
            cap = ambiguity.maximum_actionable_requests_per_round
            actionable_ids = set(frontier[:cap])
            actionable = tuple(
                ScenarioReviewRequestState(request=requests[identifier])
                for identifier in sorted(actionable_ids)
            )
            nonactionable = tuple(
                ScenarioReviewRequestState(
                    request=requests[identifier],
                    blocked_by_candidate_set_ids=tuple(
                        sorted(dependency_by_set[identifier] - resolved_ids)
                    ),
                )
                for identifier in sorted(unresolved_ids - actionable_ids)
            )
            if not actionable:
                intervention = HumanInterventionRequest(
                    reason=HumanInterventionReason.MISSING_UPSTREAM_DECISION,
                    round_number=round_number,
                    affected_candidate_set_ids=tuple(sorted(unresolved_ids)),
                    scenario_fingerprint=scenario.scenario_fingerprint,
                    missing_evidence_ids=missing_evidence_ids,
                    attempted_revision_fingerprints=(attempted_revision_fingerprints),
                    available_compiler_choice_option_ids=available_choice_ids,
                    smallest_required_human_input=smallest_human_input,
                    blocking_challenge_ids=blocking_challenge_ids,
                    lineage_fingerprints=(
                        scenario.decision_record.record_fingerprint,
                        scenario.scenario_fingerprint,
                    ),
                )
        expected_converged = not unresolved_ids
        expected = {
            "round_number": round_number,
            "actionable_requests": actionable,
            "nonactionable_requests": nonactionable,
            "human_intervention_request": intervention,
            "converged": expected_converged,
        }
        for field, value in expected.items():
            if field in self.model_fields_set and getattr(self, field) != value:
                raise ValueError(f"scenario review {field} is not compiler-derived")
            object.__setattr__(self, field, value)
        if "round_history" in self.model_fields_set and self.round_history != history:
            raise ValueError("scenario review history is not compiler-chain-derived")
        object.__setattr__(self, "scenario", scenario)
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "review_run", run)
        object.__setattr__(self, "prior_orchestration", prior)
        object.__setattr__(self, "round_history", history)
        payload = self.model_dump(mode="json", exclude={"orchestration_fingerprint"})
        fingerprint = _fingerprint(payload)
        if self.orchestration_fingerprint and self.orchestration_fingerprint != fingerprint:
            raise ValueError("scenario review orchestration fingerprint is stale")
        object.__setattr__(self, "orchestration_fingerprint", fingerprint)
        return self


def orchestrate_scenario_review(
    scenario: ScenarioCompilation,
    *,
    dependencies: tuple[ScenarioReviewDependency, ...],
    run_instance_id: str,
    agent_config: AgentConfig | None = None,
    prior_orchestration: ScenarioReviewOrchestration | None = None,
) -> ScenarioReviewOrchestration:
    """Build one deterministic, bounded local review run."""
    config = agent_config or AgentConfig()
    scope = review_session_scope_fingerprint(scenario, dependencies)
    return ScenarioReviewOrchestration(
        scenario=scenario,
        dependencies=dependencies,
        review_run=ReviewRun(
            run_instance_id=run_instance_id,
            run_scope_fingerprint=scope,
            prior_orchestration_fingerprint=(
                prior_orchestration.orchestration_fingerprint if prior_orchestration else ""
            ),
            deadline_seconds=config.deadline_seconds,
            maximum_attempts=config.max_attempts,
        ),
        prior_orchestration=prior_orchestration,
    )


def review_frontier_fingerprint(
    orchestration: ScenarioReviewOrchestration,
) -> str:
    """Expose the exact local review frontier for a typed runtime record."""
    return _review_frontier_fingerprint(
        orchestration.actionable_requests,
        orchestration.nonactionable_requests,
    )


def _configured_human_adoption_contract() -> HumanAdoptionContract:
    return HumanAdoptionContract(
        contract_id="satn-reference-adoption-contract.v2",
        canonical_instructions=(
            "Adopt only the exact publishable Scenario Compilation in the request.",
            "Confirm selected alignments and explicit Network Gaps without altering them.",
            "Record no planning, funding, delivery, or statutory approval claim.",
        ),
        evidence_packet_rules=(
            "Use only the scenario, profile and evidence snapshot fingerprints in the request.",
        ),
        allowed_tools=(
            "read-reference-adoption-packet",
            "submit-scenario-adoption-decision",
        ),
        output_schema_fields=(
            "decision_id",
            "decided_on",
            "decision_maker_name",
            "decision_maker_label",
            "rationale",
            "evidence_ids",
            "adoption_request_fingerprint",
            "selected_scenario_fingerprint",
            "selected_profile_fingerprint",
            "selected_evidence_snapshot_fingerprint",
            "selection_run_fingerprint",
            "source_url",
        ),
        stopping_policy=(
            "Reject adoption when the scenario is provisional, stale or unpublishable.",
        ),
        authority_limits=(
            "Adoption grants Reference SATN selection authority only, not delivery approval.",
        ),
    )


class ReferenceAdoptionPacket(BaseModel):
    """Compiler-authored, fingerprinted packet for a local human decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    packet_config_id: Literal["satn-reference-adoption-packet/v1"] = (
        "satn-reference-adoption-packet/v1"
    )
    scope: Literal["reference-satn-adoption"] = "reference-satn-adoption"
    scenario_fingerprint: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    adoption_contract: HumanAdoptionContract
    packet_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_packet(self) -> Self:
        expected_contract = _configured_human_adoption_contract()
        if self.adoption_contract != expected_contract:
            raise ValueError("adoption packet must contain the exact compiler contract")
        payload = self.model_dump(mode="json", exclude={"packet_fingerprint"})
        fingerprint = _fingerprint(payload)
        if self.packet_fingerprint and self.packet_fingerprint != fingerprint:
            raise ValueError("reference adoption packet fingerprint is stale")
        object.__setattr__(self, "packet_fingerprint", fingerprint)
        return self


AlignmentChallengeFinding.model_rebuild()
DecisionRevisionRecord.model_rebuild()
ScenarioDecisionRecord.model_rebuild()
ScenarioCompilation.model_rebuild()
ScenarioReviewOrchestration.model_rebuild()


class ReferenceAdoptionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_fingerprint: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    selection_run_fingerprint: str = Field(pattern=_SHA256.pattern)
    governed_evidence_ids: tuple[str, ...] = Field(min_length=1)
    adoption_packet: ReferenceAdoptionPacket
    request_id: str = ""
    request_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_adoption_request(self) -> Self:
        packet = ReferenceAdoptionPacket.model_validate(
            self.adoption_packet.model_dump(mode="python")
        )
        if (
            packet.scenario_fingerprint != self.scenario_fingerprint
            or packet.profile_fingerprint != self.profile_fingerprint
            or packet.evidence_snapshot_fingerprint != self.evidence_snapshot_fingerprint
        ):
            raise ValueError("adoption packet is stale for adoption request")
        object.__setattr__(self, "adoption_packet", packet)
        object.__setattr__(
            self,
            "governed_evidence_ids",
            _canonical_ids(self.governed_evidence_ids, "governed_evidence_ids"),
        )
        payload = self.model_dump(
            mode="json",
            exclude={"request_id", "request_fingerprint"},
        )
        identifier = _stable_id("reference-adoption", payload)
        fingerprint = _fingerprint(payload)
        if self.request_id and self.request_id != identifier:
            raise ValueError("reference adoption request ID is stale")
        if self.request_fingerprint and self.request_fingerprint != fingerprint:
            raise ValueError("reference adoption request fingerprint is stale")
        object.__setattr__(self, "request_id", identifier)
        object.__setattr__(self, "request_fingerprint", fingerprint)
        return self


def build_reference_adoption_request(
    scenario: ScenarioCompilation,
) -> ReferenceAdoptionRequest:
    scenario = ScenarioCompilation.model_validate(scenario.model_dump(mode="python"))
    adoption_contract = _configured_human_adoption_contract()
    packet = ReferenceAdoptionPacket(
        scenario_fingerprint=scenario.scenario_fingerprint,
        profile_fingerprint=scenario.profile_fingerprint,
        evidence_snapshot_fingerprint=(scenario.evidence_snapshot.snapshot_fingerprint),
        adoption_contract=adoption_contract,
    )
    return ReferenceAdoptionRequest(
        scenario_fingerprint=scenario.scenario_fingerprint,
        profile_fingerprint=scenario.profile_fingerprint,
        evidence_snapshot_fingerprint=(scenario.evidence_snapshot.snapshot_fingerprint),
        selection_run_fingerprint=scenario.decision_record.record_fingerprint,
        governed_evidence_ids=tuple(
            sorted(
                {
                    *(item.assessment_id for item in scenario.evidence_snapshot.assessments),
                    *(item.candidate_set_id for item in scenario.candidate_sets),
                    *scenario.selected_candidate_ids,
                    *scenario.complementary_candidate_ids,
                    *(item.gap_id for item in scenario.network_gaps),
                    scenario.decision_record.record_fingerprint,
                }
            )
        ),
        adoption_packet=packet,
    )


def _validate_reference_source_url(source_url: str) -> None:
    parsed = urlparse(source_url)
    if parsed.scheme in {"http", "https"}:
        try:
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as error:
            raise ValueError(
                "Reference decision source_url has an invalid HTTP authority"
            ) from error
        if (
            not parsed.netloc
            or hostname is None
            or not _HOSTNAME.fullmatch(hostname)
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError(
                "Reference decision source_url requires a valid credential-free HTTP hostname"
            )
        return
    if parsed.scheme in {"committee", "reference"}:
        path = parsed.path
        if (
            parsed.netloc
            or parsed.params
            or parsed.query
            or parsed.fragment
            or not path.startswith("/")
            or not _ID.fullmatch(path[1:])
        ):
            raise ValueError(
                "committee/reference source URLs must use scheme:/safe-identifier syntax"
            )
        return
    raise ValueError(
        "Reference decision source_url must be an http(s), committee, or reference URI"
    )


class GovernedReferenceSelectionDecision(BaseModel):
    """Attributable local human decision for one exact scenario packet."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(pattern=_ID.pattern)
    decided_on: date
    decision_maker_name: str = Field(min_length=1)
    decision_maker_label: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    source_url: str = Field(min_length=1)
    adoption_request: ReferenceAdoptionRequest
    selected_scenario_fingerprint: str = Field(pattern=_SHA256.pattern)
    selected_profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    selected_evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    selection_run_fingerprint: str = Field(pattern=_SHA256.pattern)
    decision_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_decision(self) -> Self:
        request = ReferenceAdoptionRequest.model_validate(
            self.adoption_request.model_dump(mode="python")
        )
        evidence = _canonical_ids(self.evidence_ids, "evidence_ids")
        if (
            self.selected_scenario_fingerprint != request.scenario_fingerprint
            or self.selected_profile_fingerprint != request.profile_fingerprint
            or self.selected_evidence_snapshot_fingerprint != request.evidence_snapshot_fingerprint
            or self.selection_run_fingerprint != request.selection_run_fingerprint
            or not set(evidence).issubset(request.governed_evidence_ids)
        ):
            raise ValueError("Reference decision must select the exact local adoption packet")
        _validate_reference_source_url(self.source_url)
        object.__setattr__(self, "adoption_request", request)
        object.__setattr__(self, "evidence_ids", evidence)
        expected = _fingerprint(self.model_dump(mode="json", exclude={"decision_fingerprint"}))
        if self.decision_fingerprint and self.decision_fingerprint != expected:
            raise ValueError("governed reference decision fingerprint is stale")
        object.__setattr__(self, "decision_fingerprint", expected)
        return self


class ReferenceSATNSelection(BaseModel):
    """Human-governed adoption of one exact resolved Scenario Compilation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: ScenarioCompilation
    governed_decision: GovernedReferenceSelectionDecision
    authority: Literal["governed-human"]
    selected_candidate_ids: tuple[str, ...] = ()
    complementary_candidate_ids: tuple[str, ...] = ()
    network_gap_ids: tuple[str, ...] = ()
    reference_selection_fingerprint: str = ""

    @property
    def scenario_fingerprint(self) -> str:
        return self.scenario.scenario_fingerprint

    @model_validator(mode="after")
    def bind_reference(self) -> Self:
        scenario = ScenarioCompilation.model_validate(self.scenario.model_dump(mode="python"))
        outcome_set_ids = {item.candidate_set_id for item in scenario.resolved_selections} | {
            item.candidate_set_id for item in scenario.network_gaps
        }
        if not scenario.publishable or outcome_set_ids != {
            item.candidate_set_id for item in scenario.selections
        }:
            raise ValueError("Reference SATN requires a resolved, publishable Scenario Compilation")
        resolved_candidate_ids = {
            identifier
            for item in scenario.resolved_selections
            for identifier in (
                *((item.selected_candidate_id,) if item.selected_candidate_id else ()),
                *item.complementary_candidate_ids,
            )
        }
        scenario_candidate_ids = {
            *scenario.selected_candidate_ids,
            *scenario.complementary_candidate_ids,
        }
        if resolved_candidate_ids != scenario_candidate_ids:
            raise ValueError(
                "Reference SATN requires exact compiler-and-ledger-resolved candidates"
            )
        if self.governed_decision.selected_scenario_fingerprint != scenario.scenario_fingerprint:
            raise ValueError("governed reference decision selects another scenario")
        if (
            self.governed_decision.selection_run_fingerprint
            != scenario.decision_record.record_fingerprint
        ):
            raise ValueError("governed reference decision names another selection run")
        expected_adoption_request = build_reference_adoption_request(scenario)
        if self.governed_decision.adoption_request != expected_adoption_request:
            raise ValueError(
                "governed reference decision is stale for the compiler adoption request"
            )
        selected = scenario.selected_candidate_ids
        complementary = scenario.complementary_candidate_ids
        if self.selected_candidate_ids != selected:
            raise ValueError("Reference SATN selected IDs do not match the exact scenario")
        if self.complementary_candidate_ids != complementary:
            raise ValueError("Reference SATN complementary IDs do not match the exact scenario")
        network_gap_ids = tuple(sorted(item.gap_id for item in scenario.network_gaps))
        if self.network_gap_ids != network_gap_ids:
            raise ValueError("Reference SATN Network Gap IDs do not match the exact scenario")
        if not {*selected, *complementary, *network_gap_ids}:
            raise ValueError(
                "Reference SATN requires at least one alignment candidate or Network Gap"
            )
        object.__setattr__(self, "scenario", scenario)
        payload = self.model_dump(
            mode="json",
            exclude={"reference_selection_fingerprint"},
        )
        expected = _fingerprint(payload)
        if (
            self.reference_selection_fingerprint
            and self.reference_selection_fingerprint != expected
        ):
            raise ValueError("Reference SATN fingerprint is stale")
        object.__setattr__(self, "reference_selection_fingerprint", expected)
        return self


def adopt_reference_satn(
    scenario: ScenarioCompilation,
    *,
    governed_decision: GovernedReferenceSelectionDecision,
) -> ReferenceSATNSelection:
    """Adopt only an exact, resolved Scenario Compilation with human authority."""
    scenario = ScenarioCompilation.model_validate(scenario.model_dump(mode="python"))
    return ReferenceSATNSelection(
        scenario=scenario,
        governed_decision=governed_decision,
        authority="governed-human",
        selected_candidate_ids=scenario.selected_candidate_ids,
        complementary_candidate_ids=scenario.complementary_candidate_ids,
        network_gap_ids=tuple(sorted(item.gap_id for item in scenario.network_gaps)),
    )
