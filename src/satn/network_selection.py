"""Frozen, governed policy contracts for strategic alignment selection.

This module intentionally contains policy declarations only.  It does not load
evidence, generate geometry, or choose an alignment; later compiler stages use
the immutable profile and its fingerprint as an input.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class CandidateSourceClass(StrEnum):
    """A finite candidate-generation class, in declared precedence order."""

    VERIFIED_EXISTING_ASSET = "verified-existing-asset"
    A_ROAD_CORRIDOR = "a-road-corridor"
    B_ROAD_CORRIDOR = "b-road-corridor"
    OTHER_ROUTABLE = "other-routable"


class AlignmentSelectionObjective(StrEnum):
    POPULATION_REACH = "population-reach"
    EDUCATION_ACCESS = "education-access"


class IndependentTravelPhase(StrEnum):
    SECONDARY = "secondary"
    ALL_THROUGH_SECONDARY = "all-through-secondary"


class AmbiguityTrigger(StrEnum):
    HEADLINE_AND_SENSITIVITY_ORDER_DIFFER = "headline-and-sensitivity-order-differ"
    OBJECTIVE_SECTIONS_CONFLICT_MATERIALLY = "objective-sections-conflict-materially"
    MATERIAL_GREY_EVIDENCE = "material-grey-evidence"
    NEAR_EQUIVALENT_OPTIONS = "near-equivalent-options"
    SUBSTITUTE_COMPLEMENTARY_UNCERTAIN = "substitute-complementary-uncertain"


class ToleranceStatus(StrEnum):
    NATIONAL_DEFAULT = "national-default"
    TRIAL = "trial"
    ADOPTED = "adopted"


class PopulationReachProfileConfig(BaseModel):
    """Declared interpretation of whole-OA population corridor measures."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["satn-population-reach/v1"] = "satn-population-reach/v1"
    headline_radius_m: Literal[500] = 500
    sensitivity_radius_m: Literal[1000] = 1000
    near_equivalent_tolerance_pct: float = Field(
        default=0.0, ge=0, le=100, strict=True, allow_inf_nan=False
    )
    tolerance_status: ToleranceStatus = ToleranceStatus.NATIONAL_DEFAULT

    @model_validator(mode="after")
    def validate_radii_and_tolerance(self) -> Self:
        if (
            self.near_equivalent_tolerance_pct
            and self.tolerance_status == ToleranceStatus.NATIONAL_DEFAULT
        ):
            raise ValueError("non-zero population tolerance requires a trial or adopted status")
        return self

    @field_validator("near_equivalent_tolerance_pct")
    @classmethod
    def canonicalise_zero_tolerance(cls, value: float) -> float:
        """Give mathematically equal zero tolerances one profile identity."""
        return 0.0 if value == 0 else value


class EducationAccessProfileConfig(BaseModel):
    """Declared education evidence boundary; not a safety or demand model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["satn-education-access/v1"] = "satn-education-access/v1"
    independent_travel_phases: tuple[IndependentTravelPhase, ...] = (
        IndependentTravelPhase.SECONDARY,
        IndependentTravelPhase.ALL_THROUGH_SECONDARY,
    )
    pct_schools: Literal["excluded", "supplementary"] = "supplementary"

    @field_validator("independent_travel_phases")
    @classmethod
    def require_v1_independent_travel_phases(
        cls, value: tuple[IndependentTravelPhase, ...]
    ) -> tuple[IndependentTravelPhase, ...]:
        if len(set(value)) != len(value):
            raise ValueError("independent_travel_phases cannot contain duplicates")
        required = frozenset(IndependentTravelPhase)
        if frozenset(value) != required:
            raise ValueError(
                "satn-education-access/v1 requires secondary and all-through-secondary phases"
            )
        return tuple(IndependentTravelPhase)


class ExistingAlignmentProfileConfig(BaseModel):
    """Declared limits on existing-alignment advantage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["satn-existing-alignment/v1"] = "satn-existing-alignment/v1"
    status_only_tolerance_pct: float = Field(
        default=0.0, ge=0, le=100, strict=True, allow_inf_nan=False
    )
    require_reusable_asset_evidence_for_strong_advantage: Literal[True] = True

    @field_validator("status_only_tolerance_pct")
    @classmethod
    def canonicalise_zero_tolerance(cls, value: float) -> float:
        """Give mathematically equal zero tolerances one profile identity."""
        return 0.0 if value == 0 else value

    @field_validator("require_reusable_asset_evidence_for_strong_advantage", mode="before")
    @classmethod
    def require_literal_true(cls, value: object) -> bool:
        if value is not True:
            raise ValueError("reusable-asset evidence for strong advantage must be literal true")
        return True

    @model_validator(mode="after")
    def reject_status_only_advantage(self) -> Self:
        if self.status_only_tolerance_pct != 0:
            raise ValueError("satn-existing-alignment/v1 permits no status-only tolerance")
        return self


class AlignmentAmbiguityPolicy(BaseModel):
    """Bounded conditions and limits for compiler-authored review requests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    review_when: tuple[AmbiguityTrigger, ...] = tuple(AmbiguityTrigger)
    provisional_fallback: Literal["apply-profile-hierarchy"] = "apply-profile-hierarchy"
    maximum_options_per_candidate_set: int = Field(default=5, ge=1, le=5, strict=True)
    maximum_actionable_requests_per_round: int = Field(default=12, ge=1, le=12, strict=True)
    maximum_review_rounds: int = Field(default=3, ge=1, le=3, strict=True)
    maximum_additional_analyses_per_candidate_set: int = Field(default=2, ge=0, le=2, strict=True)

    @field_validator("review_when")
    @classmethod
    def reject_duplicate_triggers(
        cls, value: tuple[AmbiguityTrigger, ...]
    ) -> tuple[AmbiguityTrigger, ...]:
        if len(set(value)) != len(value):
            raise ValueError("ambiguity review_when cannot contain duplicates")
        return tuple(trigger for trigger in AmbiguityTrigger if trigger in value)


class AlignmentPublicationPolicy(BaseModel):
    """Publication constraints for provisional and rejected alternatives."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    require_blocking_reviews_resolved: Literal[True] = True
    default_show_rejected_options: bool = Field(default=False, strict=True)

    @field_validator("require_blocking_reviews_resolved", mode="before")
    @classmethod
    def require_literal_true(cls, value: object) -> bool:
        if value is not True:
            raise ValueError("blocking reviews resolved must be literal true")
        return True


class NetworkSelectionProfile(BaseModel):
    """Frozen, data-only policy input for one alignment-selection scenario."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str = Field(min_length=1)
    method_version: Literal["satn-alignment-selection/v1"] = "satn-alignment-selection/v1"
    candidate_source_precedence: tuple[CandidateSourceClass, ...]
    primary_objective: AlignmentSelectionObjective = AlignmentSelectionObjective.POPULATION_REACH
    population: PopulationReachProfileConfig = Field(default_factory=PopulationReachProfileConfig)
    education: EducationAccessProfileConfig = Field(default_factory=EducationAccessProfileConfig)
    existing_alignment: ExistingAlignmentProfileConfig = Field(
        default_factory=ExistingAlignmentProfileConfig
    )
    ambiguity: AlignmentAmbiguityPolicy = Field(default_factory=AlignmentAmbiguityPolicy)
    publication: AlignmentPublicationPolicy = Field(default_factory=AlignmentPublicationPolicy)

    @field_validator("profile_id")
    @classmethod
    def validate_profile_id(cls, value: str) -> str:
        if _PROFILE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "network selection profile_id must be a lowercase kebab-case identifier"
            )
        return value

    @field_validator("candidate_source_precedence")
    @classmethod
    def validate_candidate_precedence(
        cls, value: tuple[CandidateSourceClass, ...]
    ) -> tuple[CandidateSourceClass, ...]:
        if len(set(value)) != len(value):
            raise ValueError("candidate_source_precedence cannot contain duplicates")
        required = {
            CandidateSourceClass.VERIFIED_EXISTING_ASSET,
            CandidateSourceClass.A_ROAD_CORRIDOR,
            CandidateSourceClass.OTHER_ROUTABLE,
        }
        classes = set(value)
        if not required.issubset(classes):
            raise ValueError(
                "candidate_source_precedence must contain verified-existing-asset, "
                "a-road-corridor and other-routable exactly once"
            )
        permitted = required | {CandidateSourceClass.B_ROAD_CORRIDOR}
        if classes != permitted and classes != required:
            raise ValueError("candidate_source_precedence contains an unsupported candidate class")
        return value

    def canonical_payload(self) -> dict[str, object]:
        """Return the exact JSON-safe payload used for immutable profile identity."""
        return self.model_dump(mode="json")

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class GovernedEvidenceArtifactConfig(BaseModel):
    """A content-bound input artifact declared by an Area Definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1)
    path: Path
    release: str = Field(min_length=1)
    effective_date: date
    licence: str = Field(min_length=1)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    redistribution: Literal["public", "controlled", "aggregate-only"]


class PopulationReachEvidenceConfig(BaseModel):
    """Governed ONS 2021 artifacts required by Population Reach v1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["satn-population-reach/v1"] = "satn-population-reach/v1"
    output_area_geometry: GovernedEvidenceArtifactConfig
    population_weighted_centroids: GovernedEvidenceArtifactConfig
    usual_resident_counts: GovernedEvidenceArtifactConfig

    @model_validator(mode="after")
    def require_distinct_artifacts(self) -> Self:
        paths = (
            self.output_area_geometry.path,
            self.population_weighted_centroids.path,
            self.usual_resident_counts.path,
        )
        if len(set(paths)) != len(paths):
            raise ValueError("population reach evidence must declare three distinct artifacts")
        hashes = (
            self.output_area_geometry.content_sha256,
            self.population_weighted_centroids.content_sha256,
            self.usual_resident_counts.content_sha256,
        )
        if len(set(hashes)) != len(hashes):
            raise ValueError(
                "population reach evidence must declare three distinct content identities"
            )
        return self


class SchoolRegisterEvidenceConfig(BaseModel):
    """Current governed school-register evidence; OSM remains geometry evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["satn-school-register/v1"] = "satn-school-register/v1"
    school_register: GovernedEvidenceArtifactConfig


class StrategicEducationDestinationAdmissionConfig(BaseModel):
    """Versioned local admission records for non-school education destinations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["satn-strategic-education-destination-admission/v1"] = (
        "satn-strategic-education-destination-admission/v1"
    )
    admissions: GovernedEvidenceArtifactConfig
