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
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from .traffic_evidence import TrafficFreshnessState

_PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class CandidateSourceClass(StrEnum):
    """A finite candidate-generation class, in declared precedence order."""

    VERIFIED_EXISTING_ASSET = "verified-existing-asset"
    A_ROAD_CORRIDOR = "a-road-corridor"
    B_ROAD_CORRIDOR = "b-road-corridor"
    OTHER_ROUTABLE = "other-routable"


class ReuseFirstCandidateClass(StrEnum):
    """Evidence-derived candidate classes for a reuse-first profile."""

    EXISTING_CYCLE_PROVISION = "existing-cycle-provision"
    UPGRADEABLE_OFF_CARRIAGEWAY = "upgradeable-off-carriageway"
    LOW_TRAFFIC_NON_A_ROAD = "low-traffic-non-a-road"
    A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE = "a-road-major-protected-infrastructure"


class TrafficBandConfig(BaseModel):
    """One ordered upper-bound band in a deployment traffic profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    upper_vehicles_per_day: int | None = Field(default=None, ge=0, strict=True)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if _PROFILE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("traffic band id must be a lowercase kebab-case identifier")
        return value


class TrafficProfileConfig(BaseModel):
    """Frozen, data-only DfT AADF classification and freshness policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    metric: Literal["all_motor_vehicles"] = "all_motor_vehicles"
    thresholds: tuple[TrafficBandConfig, ...] = Field(min_length=1)
    high_traffic_challenge_band: str = Field(min_length=1)
    max_observation_age_years: int | None = Field(default=None, ge=0, strict=True)
    as_at_year: int | None = Field(default=None, ge=1900, strict=True)
    stale_value_policy: Literal["retain-and-diagnose"] = "retain-and-diagnose"
    missing_policy: Literal["explicit-unknown"] = "explicit-unknown"

    @field_validator("profile_id")
    @classmethod
    def validate_profile_id(cls, value: str) -> str:
        if _PROFILE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "traffic profile_id must be a lowercase kebab-case identifier"
            )
        return value

    @field_validator("thresholds")
    @classmethod
    def validate_thresholds(
        cls, value: tuple[TrafficBandConfig, ...]
    ) -> tuple[TrafficBandConfig, ...]:
        ids = tuple(item.id for item in value)
        if len(set(ids)) != len(ids):
            raise ValueError("traffic thresholds cannot contain duplicate ids")
        if value[-1].upper_vehicles_per_day is not None:
            raise ValueError("traffic thresholds must end with an open upper bound")
        if any(item.upper_vehicles_per_day is None for item in value[:-1]):
            raise ValueError("traffic thresholds may only use an open upper bound last")
        numeric = tuple(
            item.upper_vehicles_per_day for item in value if item.upper_vehicles_per_day is not None
        )
        if tuple(sorted(numeric)) != numeric or len(set(numeric)) != len(numeric):
            raise ValueError("traffic thresholds must be strictly increasing")
        return value

    @model_validator(mode="after")
    def validate_challenge_band(self) -> Self:
        if self.max_observation_age_years is not None and self.as_at_year is None:
            raise ValueError("max_observation_age_years requires as_at_year")
        if self.high_traffic_challenge_band not in {
            item.id for item in self.thresholds
        }:
            raise ValueError("high traffic challenge band must name a configured threshold")
        return self

    def canonical_payload(self) -> dict[str, object]:
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

    def freshness_for(
        self,
        observation_year: int,
        reported_state: TrafficFreshnessState,
    ) -> TrafficFreshnessState:
        """Derive freshness from the declared as-at year when configured."""

        # Keep the method fail-closed even for model_construct() values that
        # bypass the normal model validator: an age policy without an as-at
        # year has no reference point and must never silently use a reported
        # freshness state.
        if self.max_observation_age_years is not None and self.as_at_year is None:
            raise ValueError("max_observation_age_years requires as_at_year")
        if self.as_at_year is None or self.max_observation_age_years is None:
            return reported_state
        age = self.as_at_year - observation_year
        if age < 0:
            return TrafficFreshnessState.UNKNOWN
        return (
            TrafficFreshnessState.STALE
            if age > self.max_observation_age_years
            else TrafficFreshnessState.FRESH
        )


class TrafficMatchPolicyConfig(BaseModel):
    """Versioned DfT count-point-to-candidate matching policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    # A matching tolerance is part of the governed policy and must be
    # explicitly supplied; the compiler does not invent a geometric default.
    route_buffer_m: float = Field(ge=0, strict=True)
    source_layers: tuple[str, ...] = ("aadf", "aadf-by-direction")
    contract: Literal["satn-dft-traffic-matching/v1"] = (
        "satn-dft-traffic-matching/v1"
    )

    @field_validator("route_buffer_m")
    @classmethod
    def validate_route_buffer(cls, value: float) -> float:
        import math

        if not math.isfinite(value):
            raise ValueError("route_buffer_m must be finite")
        return 0.0 if value == 0 else value

    @field_validator("source_layers")
    @classmethod
    def validate_source_layers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        allowed = {"aadf", "aadf-by-direction"}
        if not value or any(item not in allowed for item in value):
            raise ValueError("source_layers must use supported DfT traffic layers")
        if len(set(value)) != len(value):
            raise ValueError("source_layers cannot contain duplicates")
        return tuple(sorted(value))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )


class InterventionState(StrEnum):
    """Delivery state of a routable selected or complementary section."""

    EXISTING_PROVISION = "existing-provision"
    UPGRADE_REQUIRED = "upgrade-required"
    PROPOSED_NEW_LINK = "proposed-new-link"


class ComparatorDimension(StrEnum):
    """Finite dimensions permitted in a profile's lexicographic comparator."""

    MANDATORY_OBLIGATION_SERVICE = "mandatory-obligation-service"
    REUSE_CLASS = "reuse-class"
    INTERVENTION_STATE = "intervention-state"
    ROUTE_LENGTH = "route-length"
    ROUTE_DETOUR = "route-detour"
    ROUTE_EFFORT = "route-effort"
    TRANSITION_FRAGMENTATION_BURDEN = "transition-fragmentation-burden"
    GOVERNED_CONSTRAINTS = "governed-constraints"
    TRAFFIC_CHALLENGE = "traffic-challenge"
    STABLE_CANDIDATE_ID = "stable-candidate-id"


class DisplacementReasonCode(StrEnum):
    """Closed reason vocabulary for a lower-ranked candidate displacement."""

    FAILED_MANDATORY_OBLIGATION = "failed-mandatory-obligation"
    KNOWN_TOPOLOGY_DISCONTINUITY = "known-topology-discontinuity"
    KNOWN_ACCESS_PROHIBITION = "known-access-prohibition"
    DETOUR_LIMIT_EXCEEDED = "detour-limit-exceeded"
    ROUTE_EFFORT_LIMIT_EXCEEDED = "route-effort-limit-exceeded"
    TRANSITION_OR_FRAGMENTATION_LIMIT_EXCEEDED = (
        "transition-or-fragmentation-limit-exceeded"
    )
    KNOWN_MATERIAL_CONSTRAINT = "known-material-constraint"
    OFFICER_DECISION_APPLIED = "officer-decision-applied"
    HIGHER_RANKED_CANDIDATE_INELIGIBLE = "higher-ranked-candidate-ineligible"


class MaterialDifferenceRule(BaseModel):
    """Typed threshold used when comparing two otherwise eligible candidates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: ComparatorDimension
    threshold: float | int | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, min_length=1)
    comparison: Literal["absolute", "relative", "ratio"] = "absolute"

    @field_validator("threshold", mode="before")
    @classmethod
    def require_numeric_threshold(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("material difference threshold must be numeric")
        return value

    @model_validator(mode="after")
    def require_threshold_and_unit(self) -> Self:
        if self.threshold is None:
            raise ValueError("material difference rule requires a threshold")
        if self.unit is None:
            raise ValueError("material difference rule requires a unit")
        return self


class DisplacementRule(BaseModel):
    """Typed displacement predicate bound to the closed reason-code vocabulary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason_code: DisplacementReasonCode
    predicate: str = Field(min_length=1)
    threshold: float | int | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, min_length=1)
    evidence_requirements: tuple[str, ...] = ()

    @field_validator("threshold", mode="before")
    @classmethod
    def require_numeric_threshold(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("displacement threshold must be numeric")
        return value

    @field_validator("evidence_requirements")
    @classmethod
    def reject_duplicate_evidence_requirements(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("displacement evidence requirements cannot contain duplicates")
        return value


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


class SectionPopulationCaptureProfileConfig(BaseModel):
    """Declared local population evidence for short strategic-route sections."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["satn-section-population-capture/v1"] = (
        "satn-section-population-capture/v1"
    )
    display_section_length_m: int = Field(default=100, gt=0, le=1_000, strict=True)
    maximum_display_section_length_m: Literal[1000] = 1_000
    urban_capture_radius_m: int = Field(default=250, gt=0, strict=True)
    rural_capture_radius_m: int = Field(default=750, gt=0, strict=True)
    material_absolute_difference_residents: int = Field(
        default=500,
        ge=0,
        strict=True,
    )
    material_relative_difference_pct: float = Field(
        default=50.0,
        ge=0,
        strict=True,
        allow_inf_nan=False,
    )
    material_persistence_m: int = Field(default=500, gt=0, strict=True)


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
    contract: Literal["satn-network-selection-profile/vNext"] | None = None
    version: str | None = Field(default=None, min_length=1)
    method_version: Literal["satn-alignment-selection/v1"] = "satn-alignment-selection/v1"
    candidate_source_precedence: tuple[CandidateSourceClass, ...] | None = None
    candidate_class_order: tuple[ReuseFirstCandidateClass, ...] | None = None
    intervention_state_order: tuple[InterventionState, ...] | None = None
    comparator_order: tuple[ComparatorDimension, ...] | None = None
    material_difference_rules: tuple[MaterialDifferenceRule, ...] | None = None
    displacement_rules: tuple[DisplacementRule, ...] | None = None
    unknown_value_policy: Literal["retain-and-request-evidence"] | None = None
    traffic_profile: TrafficProfileConfig | None = None
    traffic_profile_fingerprint: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    traffic_match_policy: TrafficMatchPolicyConfig | None = None
    traffic_match_policy_fingerprint: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    deterministic_tie_break: Literal["stable-candidate-id"] | None = None
    agent_call_bound: int | None = Field(default=None, ge=0, strict=True)
    maximum_options_per_candidate_set: int | None = Field(default=None, ge=1, strict=True)
    maximum_hybrid_candidates_per_set: int | None = Field(default=None, ge=0, strict=True)
    maximum_transitions_per_candidate: int | None = Field(default=None, ge=0, strict=True)
    primary_objective: AlignmentSelectionObjective = AlignmentSelectionObjective.POPULATION_REACH
    population: PopulationReachProfileConfig = Field(default_factory=PopulationReachProfileConfig)
    section_population: SectionPopulationCaptureProfileConfig = Field(
        default_factory=SectionPopulationCaptureProfileConfig
    )
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
        cls, value: tuple[CandidateSourceClass, ...] | None
    ) -> tuple[CandidateSourceClass, ...] | None:
        if value is None:
            return None
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

    @field_validator("candidate_class_order")
    @classmethod
    def validate_candidate_class_order(
        cls, value: tuple[ReuseFirstCandidateClass, ...] | None
    ) -> tuple[ReuseFirstCandidateClass, ...] | None:
        if value is None:
            return None
        if len(set(value)) != len(value):
            raise ValueError("candidate_class_order cannot contain duplicates")
        required = frozenset(ReuseFirstCandidateClass)
        if frozenset(value) != required:
            raise ValueError(
                "candidate_class_order must contain every supported reuse-first class exactly once"
            )
        return value

    @field_validator("intervention_state_order")
    @classmethod
    def validate_intervention_state_order(
        cls, value: tuple[InterventionState, ...] | None
    ) -> tuple[InterventionState, ...] | None:
        if value is None:
            return None
        if len(set(value)) != len(value) or frozenset(value) != frozenset(InterventionState):
            raise ValueError(
                "intervention_state_order must contain every supported intervention state "
                "exactly once"
            )
        return value

    @field_validator("comparator_order")
    @classmethod
    def validate_comparator_order(
        cls, value: tuple[ComparatorDimension, ...] | None
    ) -> tuple[ComparatorDimension, ...] | None:
        if value is None:
            return None
        if len(set(value)) != len(value):
            raise ValueError("comparator_order cannot contain duplicates")
        if value.count(ComparatorDimension.STABLE_CANDIDATE_ID) != 1:
            raise ValueError("comparator_order must contain stable-candidate-id exactly once")
        if value[-1] != ComparatorDimension.STABLE_CANDIDATE_ID:
            raise ValueError("stable-candidate-id must be the final comparator dimension")
        return value

    @field_validator("material_difference_rules")
    @classmethod
    def reject_duplicate_material_difference_dimensions(
        cls, value: tuple[MaterialDifferenceRule, ...] | None
    ) -> tuple[MaterialDifferenceRule, ...] | None:
        if value is not None and len({rule.dimension for rule in value}) != len(value):
            raise ValueError("material_difference_rules cannot contain duplicate dimensions")
        return value

    @field_validator("displacement_rules")
    @classmethod
    def reject_duplicate_displacement_reason_codes(
        cls, value: tuple[DisplacementRule, ...] | None
    ) -> tuple[DisplacementRule, ...] | None:
        if value is not None and len({rule.reason_code for rule in value}) != len(value):
            raise ValueError("displacement_rules cannot contain duplicate reason_code values")
        return value

    @field_validator("traffic_profile_fingerprint")
    @classmethod
    def validate_traffic_profile_fingerprint(cls, value: str | None) -> str | None:
        if value is not None and not isinstance(value, str):
            raise ValueError("traffic_profile_fingerprint must be a SHA-256 string")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.traffic_profile is not None:
            expected_traffic_fingerprint = self.traffic_profile.fingerprint
            if (
                self.traffic_profile_fingerprint is not None
                and self.traffic_profile_fingerprint != expected_traffic_fingerprint
            ):
                raise ValueError("traffic_profile_fingerprint is stale for traffic_profile")
            object.__setattr__(
                self,
                "traffic_profile_fingerprint",
                expected_traffic_fingerprint,
            )
        if self.traffic_match_policy is not None:
            expected_match_fingerprint = self.traffic_match_policy.fingerprint
            if (
                self.traffic_match_policy_fingerprint is not None
                and self.traffic_match_policy_fingerprint != expected_match_fingerprint
            ):
                raise ValueError(
                    "traffic_match_policy_fingerprint is stale for traffic_match_policy"
                )
            object.__setattr__(
                self,
                "traffic_match_policy_fingerprint",
                expected_match_fingerprint,
            )
        is_vnext = self.contract == "satn-network-selection-profile/vNext"
        if is_vnext:
            legacy_fields = frozenset(
                {
                    "method_version",
                    "primary_objective",
                    "population",
                    "section_population",
                    "education",
                    "existing_alignment",
                    "ambiguity",
                    "publication",
                }
            )
            supplied_legacy_fields = legacy_fields & self.__pydantic_fields_set__
            if supplied_legacy_fields:
                names = ", ".join(sorted(supplied_legacy_fields))
                raise ValueError(f"vNext profiles cannot supply legacy policy fields: {names}")
            required = {
                "version": self.version,
                "candidate_class_order": self.candidate_class_order,
                "intervention_state_order": self.intervention_state_order,
                "comparator_order": self.comparator_order,
                "material_difference_rules": self.material_difference_rules,
                "displacement_rules": self.displacement_rules,
                "unknown_value_policy": self.unknown_value_policy,
                "deterministic_tie_break": self.deterministic_tie_break,
                "agent_call_bound": self.agent_call_bound,
                "maximum_options_per_candidate_set": self.maximum_options_per_candidate_set,
                "maximum_hybrid_candidates_per_set": self.maximum_hybrid_candidates_per_set,
                "maximum_transitions_per_candidate": self.maximum_transitions_per_candidate,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                raise ValueError(
                    "satn-network-selection-profile/vNext requires " + ", ".join(missing)
                )
            if self.candidate_source_precedence is not None:
                raise ValueError(
                    "vNext profiles must declare candidate_class_order, not legacy "
                    "candidate_source_precedence"
                )
            return self

        if any(
            value is not None
            for value in (
                self.version,
                self.candidate_class_order,
                self.intervention_state_order,
                self.comparator_order,
                self.material_difference_rules,
                self.displacement_rules,
                self.unknown_value_policy,
                self.traffic_profile,
                self.traffic_profile_fingerprint,
                self.deterministic_tie_break,
                self.agent_call_bound,
                self.maximum_options_per_candidate_set,
                self.maximum_hybrid_candidates_per_set,
                self.maximum_transitions_per_candidate,
            )
        ):
            raise ValueError("vNext fields require contract satn-network-selection-profile/vNext")
        if self.candidate_source_precedence is None:
            raise ValueError("legacy v1 profiles require candidate_source_precedence")
        return self

    def canonical_payload(self) -> dict[str, object]:
        """Return the exact JSON-safe payload used for immutable profile identity."""
        return self.model_dump(mode="json")

    @model_serializer(mode="wrap")
    def serialize_profile(self, handler: Any) -> dict[str, object]:
        """Serialize only the policy surface belonging to the active contract."""
        if self.contract == "satn-network-selection-profile/vNext":
            payload: dict[str, object] = {
                "contract": self.contract,
                "profile_id": self.profile_id,
                "version": self.version,
                "candidate_class_order": list(self.candidate_class_order or ()),
                "intervention_state_order": list(self.intervention_state_order or ()),
                "comparator_order": list(self.comparator_order or ()),
                "material_difference_rules": [
                    rule.model_dump(mode="json")
                    for rule in (self.material_difference_rules or ())
                ],
                "displacement_rules": [
                    rule.model_dump(mode="json") for rule in (self.displacement_rules or ())
                ],
                "unknown_value_policy": self.unknown_value_policy,
                "traffic_profile_fingerprint": self.traffic_profile_fingerprint,
                "deterministic_tie_break": self.deterministic_tie_break,
                "agent_call_bound": self.agent_call_bound,
                "maximum_options_per_candidate_set": self.maximum_options_per_candidate_set,
                "maximum_hybrid_candidates_per_set": self.maximum_hybrid_candidates_per_set,
                "maximum_transitions_per_candidate": self.maximum_transitions_per_candidate,
            }
            if self.traffic_profile is not None:
                payload["traffic_profile"] = self.traffic_profile.model_dump(mode="json")
            if self.traffic_match_policy is not None:
                payload["traffic_match_policy"] = self.traffic_match_policy.model_dump(
                    mode="json"
                )
                payload["traffic_match_policy_fingerprint"] = (
                    self.traffic_match_policy_fingerprint
                )
            return payload
        payload = handler(self)
        for field in (
            "contract",
            "version",
            "candidate_class_order",
            "intervention_state_order",
            "comparator_order",
            "material_difference_rules",
            "displacement_rules",
            "unknown_value_policy",
            "traffic_profile",
            "traffic_profile_fingerprint",
            "deterministic_tie_break",
            "agent_call_bound",
            "maximum_options_per_candidate_set",
            "maximum_hybrid_candidates_per_set",
            "maximum_transitions_per_candidate",
        ):
            payload.pop(field, None)
        if self.traffic_match_policy is not None:
            payload["traffic_match_policy"] = self.traffic_match_policy.model_dump(mode="json")
            payload["traffic_match_policy_fingerprint"] = (
                self.traffic_match_policy_fingerprint
            )
        return payload

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
