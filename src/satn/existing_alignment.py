"""Governed Existing-Alignment Advantage evidence and deterministic comparison.

The module owns geometry matching, freshness checks and proof binding. Callers
provide governed geometries and evidence, never precomputed offsets, lengths or
match hashes. Results are selection inputs only: they do not establish safety,
quality, feasibility, cost, benefit or delivery.

Declassified NCN status itself contributes no recognised-corridor advantage.
Separately governed evidence may still establish that the physical asset is
reusable; that conclusion is independent of route designation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Literal, Self

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pyproj import CRS
from shapely import line_merge, normalize, wkb, wkt
from shapely import transform as shapely_transform
from shapely.geometry import GeometryCollection, LineString, MultiLineString
from shapely.geometry.base import BaseGeometry

from satn.network_selection import AlignmentSelectionObjective, NetworkSelectionProfile

_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:[a-z0-9._-]*[a-z0-9])?$")
_SHA256 = r"^[0-9a-f]{64}$"
_STRICT = ConfigDict(frozen=True, extra="forbid", strict=True)


def _identifier(value: str, *, field: str) -> str:
    if value != value.strip() or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} must be a nonblank stable lowercase identifier")
    return value


def _nonblank(value: str, *, field: str) -> str:
    if not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be nonblank with no surrounding whitespace")
    return value


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _fingerprint(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_number(value: float) -> float:
    return 0.0 if value == 0 else value


class EvidenceState(StrEnum):
    CONFIRMED = "confirmed"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class CurrentRouteKind(StrEnum):
    CURRENT_NCN = "current-ncn"
    GREENWAY = "greenway"
    OTHER_RECOGNISED = "other-recognised"
    DECLASSIFIED_NCN = "declassified-ncn"
    OTHER = "other"
    UNKNOWN = "unknown"


class RouteAvailability(StrEnum):
    OPEN = "open"
    TEMPORARILY_CLOSED = "temporarily-closed"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class SurfaceType(StrEnum):
    ASPHALT = "asphalt"
    CONCRETE = "concrete"
    COMPACTED = "compacted"
    UNSEALED = "unsealed"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class FacilityQuality(StrEnum):
    AUDITED_GOOD = "audited-good"
    AUDITED_DEFICIENT = "audited-deficient"
    MIXED = "mixed"
    NOT_ASSESSED = "not-assessed"
    UNKNOWN = "unknown"


class LightingState(StrEnum):
    LIT = "lit"
    UNLIT = "unlit"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class RoadClass(StrEnum):
    TRAFFIC_FREE = "traffic-free"
    A_ROAD = "a-road"
    B_ROAD = "b-road"
    CLASSIFIED_UNNUMBERED = "classified-unnumbered"
    UNCLASSIFIED = "unclassified"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class BarrierType(StrEnum):
    NONE_IDENTIFIED = "none-identified"
    RIVER = "river"
    RAILWAY = "railway"
    MAJOR_ROAD = "major-road"
    GATE = "gate"
    STEPS = "steps"
    OTHER = "other"
    UNKNOWN = "unknown"


class AccessibilityState(StrEnum):
    ACCESSIBLE = "accessible"
    RESTRICTED = "restricted"
    INACCESSIBLE = "inaccessible"
    NOT_ASSESSED = "not-assessed"
    UNKNOWN = "unknown"


class ReusableEvidenceDimension(StrEnum):
    LAWFUL_ACCESS = "lawful-access"
    USABLE_CONDITION = "usable-condition"
    CONTINUITY = "continuity"
    RESPONSIBLE_OWNERSHIP_OR_MAINTENANCE = (
        "responsible-ownership-or-maintenance"
    )


class DeliveryEvidenceDimension(StrEnum):
    CONCEPT = "concept"
    CONSTRAINTS = "constraints"
    CONSENTS = "consents"
    COST = "cost"
    ACCOUNTABLE_FEASIBILITY = "accountable-feasibility"


class ExistingAlignmentUnknownReason(StrEnum):
    NO_EVIDENCE = "no-evidence"
    FUTURE_EVIDENCE = "future-evidence"
    STALE_EVIDENCE = "stale-evidence"
    UNBOUNDED_STATUS_FRESHNESS = "unbounded-status-freshness"
    UNBOUNDED_EVIDENCE_FRESHNESS = "unbounded-evidence-freshness"
    CONFLICTING_EVIDENCE = "conflicting-evidence"
    CURRENT_STATUS_NOT_QUALIFYING = "current-status-not-qualifying"
    GREENWAY_QUALIFICATION_INCOMPLETE = "greenway-qualification-incomplete"
    LAWFUL_ACCESS_UNKNOWN = "lawful-access-unknown"
    LAWFUL_ACCESS_CONFLICT = "lawful-access-conflict"
    USABLE_CONDITION_UNKNOWN = "usable-condition-unknown"
    USABLE_CONDITION_CONFLICT = "usable-condition-conflict"
    CONTINUITY_UNKNOWN = "continuity-unknown"
    CONTINUITY_CONFLICT = "continuity-conflict"
    RESPONSIBILITY_UNKNOWN = "responsibility-unknown"
    RESPONSIBILITY_CONFLICT = "responsibility-conflict"
    DELIVERY_CONCEPT_UNKNOWN = "delivery-concept-unknown"
    DELIVERY_CONCEPT_CONFLICT = "delivery-concept-conflict"
    DELIVERY_CONSTRAINTS_UNKNOWN = "delivery-constraints-unknown"
    DELIVERY_CONSTRAINTS_CONFLICT = "delivery-constraints-conflict"
    DELIVERY_CONSENTS_UNKNOWN = "delivery-consents-unknown"
    DELIVERY_CONSENTS_CONFLICT = "delivery-consents-conflict"
    DELIVERY_COST_UNKNOWN = "delivery-cost-unknown"
    DELIVERY_COST_CONFLICT = "delivery-cost-conflict"
    DELIVERY_ACCOUNTABLE_FEASIBILITY_UNKNOWN = (
        "delivery-accountable-feasibility-unknown"
    )
    DELIVERY_ACCOUNTABLE_FEASIBILITY_CONFLICT = (
        "delivery-accountable-feasibility-conflict"
    )
    DELIVERY_EVIDENCE_INCOMPLETE = "delivery-evidence-incomplete"
    CLOSURE_BLOCKER = "closure-blocker"
    OPEN_DIVERSION_UNKNOWN = "open-diversion-unknown"
    ROUTE_AVAILABILITY_UNKNOWN = "route-availability-unknown"


class GovernedFreshnessPolicy(BaseModel):
    model_config = _STRICT

    policy_id: str
    max_age_days: int = Field(gt=0, le=3660)

    @field_validator("policy_id")
    @classmethod
    def validate_policy_id(cls, value: str) -> str:
        return _identifier(value, field="policy_id")


class EvidenceProvenance(BaseModel):
    """Content-bound evidence identity with explicit scenario-date semantics."""

    model_config = _STRICT

    source_id: str
    content_sha256: str = Field(pattern=_SHA256)
    release: str
    effective_date: date
    licence: str
    observed_on: date
    valid_until: date | None = None
    freshness_policy: GovernedFreshnessPolicy | None = None

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        return _identifier(value, field="source_id")

    @field_validator("release", "licence")
    @classmethod
    def validate_text(cls, value: str, info: object) -> str:
        return _nonblank(value, field=getattr(info, "field_name", "text"))

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if self.effective_date > self.observed_on:
            raise ValueError("effective_date cannot follow observed_on")
        if self.valid_until is not None and self.valid_until < self.observed_on:
            raise ValueError("valid_until cannot precede observed_on")
        return self

    def currency_at(self, as_of: date, *, require_bounded: bool = False) -> Literal[
        "current", "future", "stale", "unbounded"
    ]:
        if self.effective_date > as_of or self.observed_on > as_of:
            return "future"
        bounded_until = self.valid_until
        if self.freshness_policy is not None:
            policy_until = self.observed_on + timedelta(
                days=self.freshness_policy.max_age_days
            )
            bounded_until = (
                policy_until if bounded_until is None else min(bounded_until, policy_until)
            )
        if require_bounded and bounded_until is None:
            return "unbounded"
        if bounded_until is not None and as_of > bounded_until:
            return "stale"
        return "current"


def _provenance_sort_key(record: EvidenceProvenance) -> str:
    """One total ordering over every governed provenance field."""
    return _canonical_json(record.model_dump(mode="json"))


def _canonical_provenance(
    records: tuple[EvidenceProvenance, ...] | list[EvidenceProvenance],
) -> tuple[EvidenceProvenance, ...]:
    unique = {_provenance_sort_key(record): record for record in records}
    return tuple(unique[key] for key in sorted(unique))


class GovernedAssertion(BaseModel):
    model_config = _STRICT

    state: EvidenceState
    provenance: EvidenceProvenance
    note: str

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        return _nonblank(value, field="note")


class ReusableAssetEvidence(BaseModel):
    """Independent evidence required before physical reuse may contribute."""

    model_config = _STRICT

    lawful_access: GovernedAssertion
    usable_condition: GovernedAssertion
    continuity: GovernedAssertion
    responsible_ownership_or_maintenance: GovernedAssertion

    def assertions(self) -> tuple[GovernedAssertion, ...]:
        return (
            self.lawful_access,
            self.usable_condition,
            self.continuity,
            self.responsible_ownership_or_maintenance,
        )

    def evidence_state_at(self, as_of: date) -> EvidenceState:
        if any(
            assertion.provenance.currency_at(as_of, require_bounded=True) != "current"
            for assertion in self.assertions()
        ):
            return EvidenceState.UNKNOWN
        if any(assertion.state is EvidenceState.UNKNOWN for assertion in self.assertions()):
            return EvidenceState.UNKNOWN
        if any(assertion.state is EvidenceState.ABSENT for assertion in self.assertions()):
            return EvidenceState.ABSENT
        return EvidenceState.CONFIRMED


class DeliveryEvidence(BaseModel):
    """Five evidence dimensions; completeness is not deliverability."""

    model_config = _STRICT

    concept: GovernedAssertion
    constraints: GovernedAssertion
    consents: GovernedAssertion
    cost: GovernedAssertion
    accountable_feasibility: GovernedAssertion

    def assertions(self) -> tuple[GovernedAssertion, ...]:
        return (
            self.concept,
            self.constraints,
            self.consents,
            self.cost,
            self.accountable_feasibility,
        )

    def evidence_state_at(self, as_of: date) -> EvidenceState:
        if any(
            assertion.provenance.currency_at(as_of, require_bounded=True) != "current"
            for assertion in self.assertions()
        ):
            return EvidenceState.UNKNOWN
        if any(assertion.state is EvidenceState.UNKNOWN for assertion in self.assertions()):
            return EvidenceState.UNKNOWN
        if any(assertion.state is EvidenceState.ABSENT for assertion in self.assertions()):
            return EvidenceState.ABSENT
        return EvidenceState.CONFIRMED


class GreenwayQualificationEvidence(BaseModel):
    model_config = _STRICT

    traffic_free: GovernedAssertion
    lawful_cycling_access: GovernedAssertion
    continuity: GovernedAssertion

    def qualifies_at(self, as_of: date) -> bool:
        return all(
            assertion.state is EvidenceState.CONFIRMED
            and assertion.provenance.currency_at(as_of, require_bounded=True) == "current"
            for assertion in (
                self.traffic_free,
                self.lawful_cycling_access,
                self.continuity,
            )
        )


class SurfaceObservation(BaseModel):
    model_config = _STRICT
    value: SurfaceType = SurfaceType.UNKNOWN
    provenance: EvidenceProvenance | None = None

    @model_validator(mode="after")
    def require_provenance_for_observation(self) -> Self:
        if self.value is not SurfaceType.UNKNOWN and self.provenance is None:
            raise ValueError("known surface evidence requires provenance")
        return self


class FacilityQualityObservation(BaseModel):
    model_config = _STRICT
    value: FacilityQuality = FacilityQuality.UNKNOWN
    provenance: EvidenceProvenance | None = None

    @model_validator(mode="after")
    def require_provenance_for_observation(self) -> Self:
        if self.value is not FacilityQuality.UNKNOWN and self.provenance is None:
            raise ValueError("known facility-quality evidence requires provenance")
        return self


class LightingObservation(BaseModel):
    model_config = _STRICT
    value: LightingState = LightingState.UNKNOWN
    provenance: EvidenceProvenance | None = None

    @model_validator(mode="after")
    def require_provenance_for_observation(self) -> Self:
        if self.value is not LightingState.UNKNOWN and self.provenance is None:
            raise ValueError("known lighting evidence requires provenance")
        return self


class RoadClassObservation(BaseModel):
    model_config = _STRICT
    value: RoadClass = RoadClass.UNKNOWN
    provenance: EvidenceProvenance | None = None

    @model_validator(mode="after")
    def require_provenance_for_observation(self) -> Self:
        if self.value is not RoadClass.UNKNOWN and self.provenance is None:
            raise ValueError("known road-class evidence requires provenance")
        return self


class BarrierObservation(BaseModel):
    model_config = _STRICT
    value: BarrierType = BarrierType.UNKNOWN
    provenance: EvidenceProvenance | None = None

    @model_validator(mode="after")
    def require_provenance_for_observation(self) -> Self:
        if self.value is not BarrierType.UNKNOWN and self.provenance is None:
            raise ValueError("known barrier evidence requires provenance")
        return self


class AccessibilityObservation(BaseModel):
    model_config = _STRICT
    value: AccessibilityState = AccessibilityState.UNKNOWN
    provenance: EvidenceProvenance | None = None

    @model_validator(mode="after")
    def require_provenance_for_observation(self) -> Self:
        if self.value is not AccessibilityState.UNKNOWN and self.provenance is None:
            raise ValueError("known accessibility evidence requires provenance")
        return self


class AlignmentContextEvidence(BaseModel):
    """Typed context retained without interpreting any value as adequate."""

    model_config = _STRICT
    surface: SurfaceObservation = Field(default_factory=SurfaceObservation)
    facility_quality: FacilityQualityObservation = Field(
        default_factory=FacilityQualityObservation
    )
    lighting: LightingObservation = Field(default_factory=LightingObservation)
    road_class: RoadClassObservation = Field(default_factory=RoadClassObservation)
    barrier: BarrierObservation = Field(default_factory=BarrierObservation)
    accessibility: AccessibilityObservation = Field(
        default_factory=AccessibilityObservation
    )


class GeometryMatchProfile(BaseModel):
    model_config = _STRICT

    method_version: Literal["satn-directional-line-overlap/v1"] = (
        "satn-directional-line-overlap/v1"
    )
    crs: str
    tolerance_m: float = Field(gt=0, le=50, allow_inf_nan=False)
    minimum_match_length_m: float = Field(gt=0, le=1000, allow_inf_nan=False)
    maximum_direction_difference_degrees: float = Field(
        gt=0, le=45, allow_inf_nan=False
    )

    @field_validator("crs")
    @classmethod
    def validate_crs(cls, value: str) -> str:
        _nonblank(value, field="crs")
        crs = CRS.from_user_input(value)
        if not crs.is_projected:
            raise ValueError("geometry matching requires a projected CRS")
        axis_units = {axis.unit_name.lower() for axis in crs.axis_info}
        if not axis_units or not axis_units <= {"metre", "meter"}:
            raise ValueError("geometry matching CRS axes must use metres")
        return crs.to_string()

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.model_dump(mode="json"))


class ExistingAlignmentCandidate(BaseModel):
    """One candidate geometry; length and geometry identity are derived internally."""

    model_config = _STRICT

    candidate_id: str
    geometry_wkt: str
    geometry_crs: str
    directness: float = Field(ge=1, allow_inf_nan=False)

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        return _identifier(value, field="candidate_id")

    @field_validator("geometry_wkt", "geometry_crs")
    @classmethod
    def validate_nonblank(cls, value: str, info: object) -> str:
        return _nonblank(value, field=getattr(info, "field_name", "geometry"))


class ExistingAlignmentEvidence(BaseModel):
    """Governed route evidence geometry, independent of any candidate offsets."""

    model_config = _STRICT

    evidence_id: str
    geometry_wkt: str
    geometry_crs: str
    current_route_kind: CurrentRouteKind
    availability: RouteAvailability
    current_status_provenance: EvidenceProvenance
    greenway_qualification: GreenwayQualificationEvidence | None = None
    open_diversion: GovernedAssertion | None = None
    reusable_asset: ReusableAssetEvidence | None = None
    delivery_evidence: DeliveryEvidence | None = None
    context: AlignmentContextEvidence = Field(default_factory=AlignmentContextEvidence)

    @field_validator("evidence_id")
    @classmethod
    def validate_evidence_id(cls, value: str) -> str:
        return _identifier(value, field="evidence_id")

    @field_validator("geometry_wkt", "geometry_crs")
    @classmethod
    def validate_nonblank(cls, value: str, info: object) -> str:
        return _nonblank(value, field=getattr(info, "field_name", "geometry"))

    @model_validator(mode="after")
    def validate_greenway_fields(self) -> Self:
        if (
            self.current_route_kind is not CurrentRouteKind.GREENWAY
            and self.greenway_qualification is not None
        ):
            raise ValueError("greenway qualification is only valid for Greenway evidence")
        if (
            self.availability not in {
                RouteAvailability.CLOSED,
                RouteAvailability.TEMPORARILY_CLOSED,
            }
            and self.open_diversion is not None
        ):
            raise ValueError(
                "open diversion evidence is only valid for a closed alignment"
            )
        return self


class EvidenceFingerprintBinding(BaseModel):
    model_config = _STRICT

    evidence_id: str
    fingerprint: str = Field(pattern=_SHA256)

    @field_validator("evidence_id")
    @classmethod
    def validate_evidence_id(cls, value: str) -> str:
        return _identifier(value, field="evidence_id")


class EvidenceGeometryLength(BaseModel):
    model_config = _STRICT

    evidence_id: str
    length_m: float = Field(ge=0, allow_inf_nan=False)

    @field_validator("evidence_id")
    @classmethod
    def validate_evidence_id(cls, value: str) -> str:
        return _identifier(value, field="evidence_id")

    @field_validator("length_m")
    @classmethod
    def canonicalise_zero(cls, value: float) -> float:
        return _canonical_number(value)


class ReusableDimensionAssessment(BaseModel):
    model_config = _STRICT

    dimension: ReusableEvidenceDimension
    state: EvidenceState
    conflicting: bool

    @model_validator(mode="after")
    def validate_conflict_state(self) -> Self:
        if self.conflicting and self.state is not EvidenceState.UNKNOWN:
            raise ValueError("a conflicting reuse dimension must be unknown")
        return self


class DeliveryDimensionAssessment(BaseModel):
    model_config = _STRICT

    dimension: DeliveryEvidenceDimension
    state: EvidenceState
    conflicting: bool

    @model_validator(mode="after")
    def validate_conflict_state(self) -> Self:
        if self.conflicting and self.state is not EvidenceState.UNKNOWN:
            raise ValueError("a conflicting delivery dimension must be unknown")
        return self


class EvidenceProvenanceLineage(BaseModel):
    model_config = _STRICT

    evidence_id: str
    provenance: tuple[EvidenceProvenance, ...]
    fingerprint: str = Field(pattern=_SHA256)

    @field_validator("evidence_id")
    @classmethod
    def validate_evidence_id(cls, value: str) -> str:
        return _identifier(value, field="evidence_id")

    @model_validator(mode="after")
    def validate_lineage_fingerprint(self) -> Self:
        if not self.provenance:
            raise ValueError("matched evidence requires provenance")
        canonical_records = _canonical_provenance(self.provenance)
        if canonical_records != self.provenance:
            raise ValueError("evidence provenance must be canonically ordered")
        expected = _fingerprint(
            [item.model_dump(mode="json") for item in self.provenance]
        )
        if self.fingerprint != expected:
            raise ValueError("provenance fingerprint does not match evidence lineage")
        return self


class EvidenceContextLineage(BaseModel):
    """ID- and geometry-bound context with its complete provenance."""

    model_config = _STRICT

    evidence_id: str
    geometry_fingerprint: str = Field(pattern=_SHA256)
    context: AlignmentContextEvidence
    provenance: tuple[EvidenceProvenance, ...]
    context_fingerprint: str = Field(pattern=_SHA256)

    @field_validator("evidence_id")
    @classmethod
    def validate_evidence_id(cls, value: str) -> str:
        return _identifier(value, field="evidence_id")

    @model_validator(mode="after")
    def validate_context_lineage(self) -> Self:
        expected_provenance = _canonical_provenance(
            list(_context_provenance(self.context))
        )
        if self.provenance != expected_provenance:
            raise ValueError("context provenance must exactly match context observations")
        expected_fingerprint = _fingerprint(
            {
                "evidence_id": self.evidence_id,
                "geometry_fingerprint": self.geometry_fingerprint,
                "context": self.context.model_dump(mode="json"),
                "provenance": [
                    item.model_dump(mode="json") for item in self.provenance
                ],
            }
        )
        if self.context_fingerprint != expected_fingerprint:
            raise ValueError("context fingerprint does not match ID-bound context")
        return self


_REUSABLE_DIMENSION_REASONS = {
    ReusableEvidenceDimension.LAWFUL_ACCESS: (
        ExistingAlignmentUnknownReason.LAWFUL_ACCESS_UNKNOWN,
        ExistingAlignmentUnknownReason.LAWFUL_ACCESS_CONFLICT,
    ),
    ReusableEvidenceDimension.USABLE_CONDITION: (
        ExistingAlignmentUnknownReason.USABLE_CONDITION_UNKNOWN,
        ExistingAlignmentUnknownReason.USABLE_CONDITION_CONFLICT,
    ),
    ReusableEvidenceDimension.CONTINUITY: (
        ExistingAlignmentUnknownReason.CONTINUITY_UNKNOWN,
        ExistingAlignmentUnknownReason.CONTINUITY_CONFLICT,
    ),
    ReusableEvidenceDimension.RESPONSIBLE_OWNERSHIP_OR_MAINTENANCE: (
        ExistingAlignmentUnknownReason.RESPONSIBILITY_UNKNOWN,
        ExistingAlignmentUnknownReason.RESPONSIBILITY_CONFLICT,
    ),
}

_DELIVERY_DIMENSION_REASONS = {
    DeliveryEvidenceDimension.CONCEPT: (
        ExistingAlignmentUnknownReason.DELIVERY_CONCEPT_UNKNOWN,
        ExistingAlignmentUnknownReason.DELIVERY_CONCEPT_CONFLICT,
    ),
    DeliveryEvidenceDimension.CONSTRAINTS: (
        ExistingAlignmentUnknownReason.DELIVERY_CONSTRAINTS_UNKNOWN,
        ExistingAlignmentUnknownReason.DELIVERY_CONSTRAINTS_CONFLICT,
    ),
    DeliveryEvidenceDimension.CONSENTS: (
        ExistingAlignmentUnknownReason.DELIVERY_CONSENTS_UNKNOWN,
        ExistingAlignmentUnknownReason.DELIVERY_CONSENTS_CONFLICT,
    ),
    DeliveryEvidenceDimension.COST: (
        ExistingAlignmentUnknownReason.DELIVERY_COST_UNKNOWN,
        ExistingAlignmentUnknownReason.DELIVERY_COST_CONFLICT,
    ),
    DeliveryEvidenceDimension.ACCOUNTABLE_FEASIBILITY: (
        ExistingAlignmentUnknownReason.DELIVERY_ACCOUNTABLE_FEASIBILITY_UNKNOWN,
        ExistingAlignmentUnknownReason.DELIVERY_ACCOUNTABLE_FEASIBILITY_CONFLICT,
    ),
}


class ExistingAlignmentTransition(BaseModel):
    model_config = _STRICT

    start_m: float = Field(ge=0, allow_inf_nan=False)
    end_m: float = Field(ge=0, allow_inf_nan=False)
    recognised_current_corridor: bool
    reusable_asset: bool
    reusable_asset_evidence_state: EvidenceState
    reuse_availability_evidence_state: EvidenceState
    reusable_dimension_assessments: tuple[ReusableDimensionAssessment, ...]
    delivery_evidence_complete: bool
    delivery_evidence_state: EvidenceState
    delivery_dimension_assessments: tuple[DeliveryDimensionAssessment, ...]
    route_kinds: tuple[CurrentRouteKind, ...]
    availability_states: tuple[RouteAvailability, ...]
    unknown_reasons: tuple[ExistingAlignmentUnknownReason, ...]
    evidence_ids: tuple[str, ...]
    evidence_geometry_fingerprints: tuple[EvidenceFingerprintBinding, ...]
    evidence_provenance_lineage: tuple[EvidenceProvenanceLineage, ...]
    provenance: tuple[EvidenceProvenance, ...]
    context_lineage: tuple[EvidenceContextLineage, ...]

    @field_validator("start_m", "end_m")
    @classmethod
    def canonicalise_zero(cls, value: float) -> float:
        return _canonical_number(value)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end_m < self.start_m:
            raise ValueError("transition start_m cannot exceed end_m")
        expected_reuse_dimensions = (
            tuple(ReusableEvidenceDimension) if self.evidence_ids else ()
        )
        actual_reuse_dimensions = tuple(
            item.dimension for item in self.reusable_dimension_assessments
        )
        if actual_reuse_dimensions != expected_reuse_dimensions:
            raise ValueError(
                "reuse dimension assessments must be complete and canonically ordered"
            )
        expected_delivery_dimensions = (
            tuple(DeliveryEvidenceDimension) if self.evidence_ids else ()
        )
        actual_delivery_dimensions = tuple(
            item.dimension for item in self.delivery_dimension_assessments
        )
        if actual_delivery_dimensions != expected_delivery_dimensions:
            raise ValueError(
                "delivery dimension assessments must be complete and canonically ordered"
            )
        reuse_states = (
            self.reuse_availability_evidence_state,
            *(item.state for item in self.reusable_dimension_assessments),
        )
        expected_reuse_state = (
            EvidenceState.UNKNOWN
            if EvidenceState.UNKNOWN in reuse_states
            else (
                EvidenceState.ABSENT
                if EvidenceState.ABSENT in reuse_states
                else EvidenceState.CONFIRMED
            )
        )
        if self.reusable_asset_evidence_state is not expected_reuse_state:
            raise ValueError(
                "reusable evidence state must agree with dimensions and availability"
            )
        if self.reusable_asset != (
            self.reusable_asset_evidence_state is EvidenceState.CONFIRMED
        ):
            raise ValueError("reusable_asset must agree with reusable evidence state")
        delivery_states = tuple(
            item.state for item in self.delivery_dimension_assessments
        )
        expected_delivery_state = (
            EvidenceState.UNKNOWN
            if not delivery_states or EvidenceState.UNKNOWN in delivery_states
            else (
                EvidenceState.ABSENT
                if EvidenceState.ABSENT in delivery_states
                else EvidenceState.CONFIRMED
            )
        )
        if self.delivery_evidence_state is not expected_delivery_state:
            raise ValueError(
                "delivery evidence state must agree with dimension assessments"
            )
        if self.delivery_evidence_complete != (
            self.delivery_evidence_state is not EvidenceState.UNKNOWN
        ):
            raise ValueError(
                "delivery_evidence_complete must agree with delivery evidence state"
            )
        for name in (
            "route_kinds",
            "availability_states",
            "unknown_reasons",
            "evidence_ids",
        ):
            values = getattr(self, name)
            if tuple(sorted(set(values))) != values:
                raise ValueError(f"{name} must be unique and canonically ordered")
        if not self.evidence_ids:
            if any(
                (
                    self.route_kinds,
                    self.availability_states,
                    self.evidence_geometry_fingerprints,
                    self.evidence_provenance_lineage,
                    self.provenance,
                    self.context_lineage,
                )
            ):
                raise ValueError("an unmatched transition cannot retain evidence claims")
            if ExistingAlignmentUnknownReason.NO_EVIDENCE not in self.unknown_reasons:
                raise ValueError("an unmatched transition must state no-evidence")
            if (
                self.recognised_current_corridor
                or self.reusable_asset
                or self.delivery_evidence_complete
            ):
                raise ValueError("an unmatched transition cannot claim an advantage")
        elif self.reuse_availability_evidence_state is EvidenceState.ABSENT:
            raise ValueError("matched alignment availability cannot be absent")
        if (
            RouteAvailability.OPEN in self.availability_states
            and self.availability_states == (RouteAvailability.OPEN,)
            and self.reuse_availability_evidence_state
            is not EvidenceState.CONFIRMED
        ):
            raise ValueError("open matched evidence must have confirmed availability")
        if (
            RouteAvailability.UNKNOWN in self.availability_states
            and self.reuse_availability_evidence_state is not EvidenceState.UNKNOWN
        ):
            raise ValueError("unknown route availability must block reuse")
        for bindings, label in (
            (self.evidence_geometry_fingerprints, "geometry"),
        ):
            binding_ids = tuple(item.evidence_id for item in bindings)
            if binding_ids != self.evidence_ids:
                raise ValueError(
                    f"matched evidence IDs require corresponding {label} fingerprints"
                )
        lineage_ids = tuple(item.evidence_id for item in self.evidence_provenance_lineage)
        if lineage_ids != self.evidence_ids:
            raise ValueError(
                "matched evidence IDs require corresponding provenance lineage"
            )
        context_lineage_ids = tuple(
            item.evidence_id for item in self.context_lineage
        )
        if context_lineage_ids != self.evidence_ids:
            raise ValueError("matched evidence IDs require corresponding context lineage")
        geometry_fingerprint_by_evidence_id = {
            item.evidence_id: item.fingerprint
            for item in self.evidence_geometry_fingerprints
        }
        if any(
            item.geometry_fingerprint
            != geometry_fingerprint_by_evidence_id[item.evidence_id]
            for item in self.context_lineage
        ):
            raise ValueError("context lineage must bind the evidence geometry fingerprint")
        provenance_by_evidence_id = {
            item.evidence_id: {
                _provenance_sort_key(record) for record in item.provenance
            }
            for item in self.evidence_provenance_lineage
        }
        for item in self.context_lineage:
            if not {
                _provenance_sort_key(record) for record in item.provenance
            } <= provenance_by_evidence_id[item.evidence_id]:
                raise ValueError(
                    "context provenance must be included in evidence provenance lineage"
                )
        lineage_provenance = _canonical_provenance(
            [
                item
                for lineage in self.evidence_provenance_lineage
                for item in lineage.provenance
            ]
        )
        if self.provenance != lineage_provenance:
            raise ValueError("transition provenance must equal evidence lineage")
        expected_dimension_reasons = {
            (
                _REUSABLE_DIMENSION_REASONS[item.dimension][1]
                if item.conflicting
                else _REUSABLE_DIMENSION_REASONS[item.dimension][0]
            )
            for item in self.reusable_dimension_assessments
            if item.conflicting or item.state is EvidenceState.UNKNOWN
        }
        expected_dimension_reasons.update(
            {
                (
                    _DELIVERY_DIMENSION_REASONS[item.dimension][1]
                    if item.conflicting
                    else _DELIVERY_DIMENSION_REASONS[item.dimension][0]
                )
                for item in self.delivery_dimension_assessments
                if item.conflicting or item.state is EvidenceState.UNKNOWN
            }
        )
        all_dimension_reasons = {
            reason
            for reasons in (
                *_REUSABLE_DIMENSION_REASONS.values(),
                *_DELIVERY_DIMENSION_REASONS.values(),
            )
            for reason in reasons
        }
        actual_dimension_reasons = set(self.unknown_reasons).intersection(
            all_dimension_reasons
        )
        if actual_dimension_reasons != expected_dimension_reasons:
            raise ValueError(
                "dimension unknown and conflict reasons must exactly match assessments"
            )
        if any(
            item.conflicting
            for item in (
                *self.reusable_dimension_assessments,
                *self.delivery_dimension_assessments,
            )
        ) and ExistingAlignmentUnknownReason.CONFLICTING_EVIDENCE not in (
            self.unknown_reasons
        ):
            raise ValueError("dimension conflict requires conflicting-evidence reason")
        if (
            self.delivery_evidence_state is EvidenceState.UNKNOWN
            and self.evidence_ids
            and ExistingAlignmentUnknownReason.DELIVERY_EVIDENCE_INCOMPLETE
            not in self.unknown_reasons
        ):
            raise ValueError("unknown delivery evidence must be reported incomplete")
        if self.recognised_current_corridor:
            if self.availability_states != (RouteAvailability.OPEN,):
                raise ValueError("recognised current corridor evidence must be open")
            if not set(self.route_kinds) <= {
                CurrentRouteKind.CURRENT_NCN,
                CurrentRouteKind.GREENWAY,
            }:
                raise ValueError("route kind cannot establish recognised current share")
            blocked_reasons = {
                ExistingAlignmentUnknownReason.CURRENT_STATUS_NOT_QUALIFYING,
                ExistingAlignmentUnknownReason.GREENWAY_QUALIFICATION_INCOMPLETE,
                ExistingAlignmentUnknownReason.UNBOUNDED_STATUS_FRESHNESS,
            }
            if blocked_reasons.intersection(self.unknown_reasons):
                raise ValueError(
                    "recognised current corridor conflicts with retained uncertainty"
                )
        return self


class ExistingAlignmentAdvantage(BaseModel):
    model_config = _STRICT

    method_version: Literal["satn-existing-alignment-advantage/v1"] = (
        "satn-existing-alignment-advantage/v1"
    )
    candidate_id: str
    as_of: date
    directness: float = Field(ge=1, allow_inf_nan=False)
    geometry_match_profile_fingerprint: str = Field(pattern=_SHA256)
    candidate_geometry_fingerprint: str = Field(pattern=_SHA256)
    evidence_fingerprint: str = Field(pattern=_SHA256)
    match_fingerprint: str = Field(pattern=_SHA256)
    evidence_geometry_lengths_m: tuple[EvidenceGeometryLength, ...]
    alignment_length_m: float = Field(gt=0, allow_inf_nan=False)
    matched_length_m: float = Field(ge=0, allow_inf_nan=False)
    recognised_current_length_m: float = Field(ge=0, allow_inf_nan=False)
    reusable_asset_length_m: float = Field(ge=0, allow_inf_nan=False)
    declassified_length_m: float = Field(ge=0, allow_inf_nan=False)
    unknown_length_m: float = Field(ge=0, allow_inf_nan=False)
    longest_continuous_match_m: float = Field(ge=0, allow_inf_nan=False)
    longest_continuous_recognised_m: float = Field(ge=0, allow_inf_nan=False)
    longest_continuous_reusable_m: float = Field(ge=0, allow_inf_nan=False)
    matched_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    recognised_current_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    reusable_asset_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    delivery_evidence_complete_length_m: float = Field(ge=0, allow_inf_nan=False)
    transitions: tuple[ExistingAlignmentTransition, ...]
    gaps: tuple[ExistingAlignmentTransition, ...]
    unknown_reasons: tuple[ExistingAlignmentUnknownReason, ...]
    does_not_establish_route_quality_or_adequacy: Literal[True] = True
    does_not_establish_feasibility: Literal[True] = True
    does_not_establish_cost_or_benefit: Literal[True] = True
    does_not_override_mandatory_safeguards_or_education_obligations: Literal[True] = True

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        return _identifier(value, field="candidate_id")

    @field_validator(
        "alignment_length_m",
        "matched_length_m",
        "recognised_current_length_m",
        "reusable_asset_length_m",
        "declassified_length_m",
        "unknown_length_m",
        "longest_continuous_match_m",
        "longest_continuous_recognised_m",
        "longest_continuous_reusable_m",
        "matched_share",
        "recognised_current_share",
        "reusable_asset_share",
        "delivery_evidence_complete_length_m",
    )
    @classmethod
    def canonicalise_zero(cls, value: float) -> float:
        return _canonical_number(value)

    @model_validator(mode="after")
    def validate_totals(self) -> Self:
        if not self.transitions:
            raise ValueError("a non-empty candidate alignment requires transitions")
        bounded = (
            self.matched_length_m,
            self.recognised_current_length_m,
            self.reusable_asset_length_m,
            self.declassified_length_m,
            self.unknown_length_m,
            self.longest_continuous_match_m,
            self.longest_continuous_recognised_m,
            self.longest_continuous_reusable_m,
            self.delivery_evidence_complete_length_m,
        )
        if any(value > self.alignment_length_m for value in bounded):
            raise ValueError("summary lengths cannot exceed alignment_length_m")
        if not math.isclose(
            self.matched_length_m + self.unknown_length_m,
            self.alignment_length_m,
            rel_tol=0,
            abs_tol=1e-7,
        ):
            raise ValueError("matched and unknown lengths must cover the alignment exactly")
        if not math.isclose(
            self.matched_share,
            self.matched_length_m / self.alignment_length_m,
            rel_tol=0,
            abs_tol=1e-12,
        ) or not math.isclose(
            self.recognised_current_share,
            self.recognised_current_length_m / self.alignment_length_m,
            rel_tol=0,
            abs_tol=1e-12,
        ) or not math.isclose(
            self.reusable_asset_share,
            self.reusable_asset_length_m / self.alignment_length_m,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError("shares must agree with their governed lengths")
        geometry_length_ids = tuple(
            item.evidence_id for item in self.evidence_geometry_lengths_m
        )
        if tuple(sorted(set(geometry_length_ids))) != geometry_length_ids:
            raise ValueError(
                "evidence_geometry_lengths_m must be unique and canonically ordered"
            )
        if any(
            length < 0 or not math.isfinite(length)
            for item in self.evidence_geometry_lengths_m
            for length in (item.length_m,)
        ):
            raise ValueError("evidence geometry lengths must be finite and nonnegative")
        if self.transitions and (
            self.transitions[0].start_m != 0
            or self.transitions[-1].end_m != self.alignment_length_m
        ):
            raise ValueError("transitions must cover the complete candidate geometry")
        if any(item.end_m <= item.start_m for item in self.transitions):
            raise ValueError("derived transitions must have positive length")
        if any(
            left.end_m != right.start_m for left, right in pairwise(self.transitions)
        ):
            raise ValueError("transitions must be contiguous and canonically ordered")
        def transition_length(item: ExistingAlignmentTransition) -> float:
            return item.end_m - item.start_m

        derived_matched = sum(
            transition_length(item) for item in self.transitions if item.evidence_ids
        )
        derived_recognised = sum(
            transition_length(item)
            for item in self.transitions
            if item.recognised_current_corridor
        )
        derived_reusable = sum(
            transition_length(item) for item in self.transitions if item.reusable_asset
        )
        derived_declassified = sum(
            transition_length(item)
            for item in self.transitions
            if CurrentRouteKind.DECLASSIFIED_NCN in item.route_kinds
            and not item.recognised_current_corridor
        )
        derived_unknown = sum(
            transition_length(item) for item in self.transitions if not item.evidence_ids
        )
        derived_delivery = sum(
            transition_length(item)
            for item in self.transitions
            if item.delivery_evidence_complete
        )
        derived_longest = matched_continuous = 0.0
        derived_recognised_longest = recognised_continuous = 0.0
        derived_reusable_longest = reusable_continuous = 0.0
        for item in self.transitions:
            if item.evidence_ids:
                matched_continuous += transition_length(item)
                derived_longest = max(derived_longest, matched_continuous)
            else:
                matched_continuous = 0.0
            if item.recognised_current_corridor:
                recognised_continuous += transition_length(item)
                derived_recognised_longest = max(
                    derived_recognised_longest, recognised_continuous
                )
            else:
                recognised_continuous = 0.0
            if item.reusable_asset:
                reusable_continuous += transition_length(item)
                derived_reusable_longest = max(
                    derived_reusable_longest, reusable_continuous
                )
            else:
                reusable_continuous = 0.0
        for name, actual, expected in (
            ("matched_length_m", self.matched_length_m, derived_matched),
            (
                "recognised_current_length_m",
                self.recognised_current_length_m,
                derived_recognised,
            ),
            ("reusable_asset_length_m", self.reusable_asset_length_m, derived_reusable),
            ("declassified_length_m", self.declassified_length_m, derived_declassified),
            ("unknown_length_m", self.unknown_length_m, derived_unknown),
            (
                "delivery_evidence_complete_length_m",
                self.delivery_evidence_complete_length_m,
                derived_delivery,
            ),
            (
                "longest_continuous_match_m",
                self.longest_continuous_match_m,
                derived_longest,
            ),
            (
                "longest_continuous_recognised_m",
                self.longest_continuous_recognised_m,
                derived_recognised_longest,
            ),
            (
                "longest_continuous_reusable_m",
                self.longest_continuous_reusable_m,
                derived_reusable_longest,
            ),
        ):
            if not math.isclose(actual, expected, rel_tol=0, abs_tol=1e-7):
                raise ValueError(f"{name} does not agree with transitions")
        derived_gaps = tuple(item for item in self.transitions if not item.evidence_ids)
        if self.gaps != derived_gaps:
            raise ValueError("gaps must be exactly the unmatched transitions")
        derived_reasons = tuple(
            sorted(
                {
                    reason
                    for item in self.transitions
                    for reason in item.unknown_reasons
                }
            )
        )
        if self.unknown_reasons != derived_reasons:
            raise ValueError("unknown_reasons must be derived from transitions")
        matched_evidence_ids = tuple(
            sorted(
                {
                    evidence_id
                    for item in self.transitions
                    for evidence_id in item.evidence_ids
                }
            )
        )
        if geometry_length_ids != matched_evidence_ids:
            raise ValueError(
                "evidence geometry-length IDs must equal transition-derived evidence IDs"
            )
        if tuple(sorted(set(self.unknown_reasons))) != self.unknown_reasons:
            raise ValueError("unknown_reasons must be unique and canonically ordered")
        return self

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.canonical_payload())


class CandidateEligibilityProof(BaseModel):
    model_config = _STRICT

    candidate_id: str
    advantage_fingerprint: str = Field(pattern=_SHA256)
    candidate_geometry_fingerprint: str = Field(pattern=_SHA256)
    evidence_fingerprint: str = Field(pattern=_SHA256)
    mandatory_validity_topology_fingerprint: str = Field(pattern=_SHA256)
    education_completeness_fingerprint: str = Field(pattern=_SHA256)
    active_objective_evidence_fingerprint: str = Field(pattern=_SHA256)
    near_equivalence_calculation_fingerprint: str = Field(pattern=_SHA256)
    near_equivalence_profile_fingerprint: str = Field(pattern=_SHA256)

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        return _identifier(value, field="candidate_id")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.model_dump(mode="json"))


class NearEquivalenceProof(BaseModel):
    model_config = _STRICT

    method_version: Literal["satn-near-equivalence-proof/v1"] = (
        "satn-near-equivalence-proof/v1"
    )
    proof_id: str
    as_of: date
    profile_fingerprint: str = Field(pattern=_SHA256)
    active_objective: AlignmentSelectionObjective
    near_equivalence_calculation_fingerprint: str = Field(pattern=_SHA256)
    near_equivalence_profile_fingerprint: str = Field(pattern=_SHA256)
    candidate_ids: tuple[str, ...]
    eligibility: tuple[CandidateEligibilityProof, ...]
    near_equivalent_after_mandatory_gates: Literal[True]

    @field_validator("proof_id")
    @classmethod
    def validate_proof_id(cls, value: str) -> str:
        return _identifier(value, field="proof_id")

    @model_validator(mode="after")
    def validate_canonical_coverage(self) -> Self:
        if len(self.candidate_ids) < 2:
            raise ValueError("near-equivalence proof requires at least two candidates")
        if tuple(sorted(set(self.candidate_ids))) != self.candidate_ids:
            raise ValueError("candidate_ids must be unique and canonically ordered")
        eligibility_ids = tuple(item.candidate_id for item in self.eligibility)
        if eligibility_ids != self.candidate_ids:
            raise ValueError("eligibility must canonically cover candidate_ids exactly")
        if any(
            item.near_equivalence_calculation_fingerprint
            != self.near_equivalence_calculation_fingerprint
            or item.near_equivalence_profile_fingerprint
            != self.near_equivalence_profile_fingerprint
            for item in self.eligibility
        ):
            raise ValueError(
                "eligibility must bind the exact near-equivalence calculation and profile"
            )
        return self

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.model_dump(mode="json"))


class CandidateFingerprintBinding(BaseModel):
    model_config = _STRICT

    candidate_id: str
    fingerprint: str = Field(pattern=_SHA256)

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        return _identifier(value, field="candidate_id")


class ExistingAlignmentComparisonValue(BaseModel):
    model_config = _STRICT

    candidate_id: str
    reusable_asset_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    recognised_current_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    directness: float = Field(ge=1, allow_inf_nan=False)

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        return _identifier(value, field="candidate_id")

    @field_validator(
        "reusable_asset_share",
        "recognised_current_share",
        "directness",
    )
    @classmethod
    def canonicalise_zero(cls, value: float) -> float:
        return _canonical_number(value)


class ExistingAlignmentLexicographicComparison(BaseModel):
    model_config = _STRICT

    method_version: Literal["satn-existing-alignment-advantage/v1"] = (
        "satn-existing-alignment-advantage/v1"
    )
    as_of: date
    profile_id: str
    profile_fingerprint: str = Field(pattern=_SHA256)
    active_objective: AlignmentSelectionObjective
    near_equivalence_proof_id: str
    near_equivalence_proof_fingerprint: str = Field(pattern=_SHA256)
    advantage_fingerprints: tuple[CandidateFingerprintBinding, ...]
    candidate_geometry_fingerprints: tuple[CandidateFingerprintBinding, ...]
    evidence_fingerprints: tuple[CandidateFingerprintBinding, ...]
    advantages: tuple[ExistingAlignmentAdvantage, ...]
    comparison_values: tuple[ExistingAlignmentComparisonValue, ...]
    ranked_candidate_ids: tuple[str, ...]
    comparison_order: Literal[
        "reusable-asset-share,recognised-current-share,directness,stable-candidate-id"
    ] = "reusable-asset-share,recognised-current-share,directness,stable-candidate-id"
    weighted_aggregate_used: Literal[False] = False
    tie_break_only: Literal[True] = True
    does_not_override_mandatory_safeguards_or_education_obligations: Literal[True] = True

    @field_validator("profile_id", "near_equivalence_proof_id")
    @classmethod
    def validate_ids(cls, value: str, info: object) -> str:
        return _identifier(value, field=getattr(info, "field_name", "identifier"))

    @model_validator(mode="after")
    def validate_derived_comparison(self) -> Self:
        bindings = (
            self.advantage_fingerprints,
            self.candidate_geometry_fingerprints,
            self.evidence_fingerprints,
        )
        candidate_ids = tuple(item.candidate_id for item in self.advantages)
        if len(candidate_ids) < 2 or tuple(sorted(set(candidate_ids))) != candidate_ids:
            raise ValueError("advantages must be unique and canonically ordered")
        expected_values = tuple(
            ExistingAlignmentComparisonValue(
                candidate_id=item.candidate_id,
                reusable_asset_share=item.reusable_asset_share,
                recognised_current_share=item.recognised_current_share,
                directness=item.directness,
            )
            for item in self.advantages
        )
        if self.comparison_values != expected_values:
            raise ValueError("comparison_values must be derived from bound advantages")
        if any(
            tuple(item.candidate_id for item in binding) != candidate_ids
            for binding in bindings
        ):
            raise ValueError("fingerprint bindings must cover comparison candidates")
        expected_bindings = (
            tuple(
                CandidateFingerprintBinding(
                    candidate_id=item.candidate_id,
                    fingerprint=item.fingerprint,
                )
                for item in self.advantages
            ),
            tuple(
                CandidateFingerprintBinding(
                    candidate_id=item.candidate_id,
                    fingerprint=item.candidate_geometry_fingerprint,
                )
                for item in self.advantages
            ),
            tuple(
                CandidateFingerprintBinding(
                    candidate_id=item.candidate_id,
                    fingerprint=item.evidence_fingerprint,
                )
                for item in self.advantages
            ),
        )
        if bindings != expected_bindings:
            raise ValueError("fingerprint bindings must match bound advantages")
        expected_ranking = tuple(
            item.candidate_id
            for item in sorted(
                self.comparison_values,
                key=lambda item: (
                    -item.reusable_asset_share,
                    -item.recognised_current_share,
                    item.directness,
                    item.candidate_id,
                ),
            )
        )
        if self.ranked_candidate_ids != expected_ranking:
            raise ValueError("ranked_candidate_ids contradict comparison values")
        return self

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.model_dump(mode="json"))


def _parse_line_geometry(value: str, crs_value: str, profile: GeometryMatchProfile) -> BaseGeometry:
    if CRS.from_user_input(crs_value) != CRS.from_user_input(profile.crs):
        raise ValueError("all geometries must use the governed geometry-match CRS")
    try:
        geometry = wkt.loads(value)
    except Exception as error:
        raise ValueError("geometry_wkt is invalid") from error
    if (
        geometry.is_empty
        or not geometry.is_valid
        or geometry.geom_type not in {"LineString", "MultiLineString"}
    ):
        raise ValueError("geometry must be a finite valid LineString or MultiLineString")
    bounds = geometry.bounds
    if len(bounds) != 4 or not all(float("-inf") < value < float("inf") for value in bounds):
        raise ValueError("geometry coordinates must be finite")
    zero_normalised = shapely_transform(
        geometry,
        lambda coordinates: np.where(coordinates == 0.0, 0.0, coordinates),
    )
    return normalize(zero_normalised)


def _canonical_geometry_payload(
    geometry: BaseGeometry, crs_value: str
) -> dict[str, str]:
    canonical_geometry = _canonical_linework(geometry)
    canonical_wkb = wkb.dumps(
        canonical_geometry,
        hex=True,
        output_dimension=2,
        big_endian=True,
    )
    return {
        "crs": CRS.from_user_input(crs_value).to_string(),
        "wkb": canonical_wkb,
    }


def _geometry_fingerprint(geometry: BaseGeometry, crs_value: str) -> str:
    return _fingerprint(_canonical_geometry_payload(geometry, crs_value))


def _line_parts(geometry: BaseGeometry) -> tuple[LineString, ...]:
    if isinstance(geometry, LineString):
        return (geometry,) if not geometry.is_empty and geometry.length > 0 else ()
    if isinstance(geometry, MultiLineString | GeometryCollection):
        return tuple(
            part
            for child in geometry.geoms
            for part in _line_parts(child)
            if part.length > 0
        )
    return ()


def _canonical_linework(geometry: BaseGeometry) -> BaseGeometry:
    simplified = geometry.simplify(0, preserve_topology=True)
    merged = line_merge(simplified)
    return normalize(merged.simplify(0, preserve_topology=True))


def _merge_intervals(
    intervals: list[tuple[float, float]] | tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    merged: list[tuple[float, float]] = []
    for lower, upper in sorted(intervals):
        if merged and lower <= merged[-1][1] + 1e-9:
            merged[-1] = (merged[-1][0], max(merged[-1][1], upper))
        else:
            merged.append((lower, upper))
    return tuple(
        (
            _canonical_number(lower),
            _canonical_number(upper),
        )
        for lower, upper in merged
    )


def _match_intervals(
    candidate: LineString,
    evidence: BaseGeometry,
    profile: GeometryMatchProfile,
) -> tuple[tuple[float, float], ...]:
    candidate = _canonical_linework(candidate)
    evidence = _canonical_linework(evidence)
    intervals: list[tuple[float, float]] = []
    candidate_offset = 0.0
    candidate_coordinates = list(candidate.coords)
    for candidate_start, candidate_end in pairwise(candidate_coordinates):
        candidate_dx = candidate_end[0] - candidate_start[0]
        candidate_dy = candidate_end[1] - candidate_start[1]
        candidate_length = math.hypot(candidate_dx, candidate_dy)
        if candidate_length == 0:
            continue
        candidate_unit = (
            candidate_dx / candidate_length,
            candidate_dy / candidate_length,
        )
        for evidence_part in _line_parts(evidence):
            for evidence_start, evidence_end in pairwise(evidence_part.coords):
                evidence_dx = evidence_end[0] - evidence_start[0]
                evidence_dy = evidence_end[1] - evidence_start[1]
                evidence_length = math.hypot(evidence_dx, evidence_dy)
                if evidence_length == 0:
                    continue
                direction_dot = abs(
                    candidate_dx * evidence_dx
                    + candidate_dy * evidence_dy
                )
                direction_cross = abs(
                    candidate_dx * evidence_dy
                    - candidate_dy * evidence_dx
                )
                angle = math.degrees(
                    math.atan2(direction_cross, direction_dot)
                )
                if angle > profile.maximum_direction_difference_degrees:
                    continue
                relative_start = (
                    evidence_start[0] - candidate_start[0],
                    evidence_start[1] - candidate_start[1],
                )
                start_lateral = (
                    relative_start[0] * candidate_unit[1]
                    - relative_start[1] * candidate_unit[0]
                )
                lateral_delta = (
                    evidence_dx * candidate_unit[1]
                    - evidence_dy * candidate_unit[0]
                )
                if lateral_delta == 0:
                    if abs(start_lateral) > profile.tolerance_m:
                        continue
                    clipped_parameter_start = 0.0
                    clipped_parameter_end = 1.0
                else:
                    lateral_roots = (
                        (-profile.tolerance_m - start_lateral)
                        / lateral_delta,
                        (profile.tolerance_m - start_lateral)
                        / lateral_delta,
                    )
                    clipped_parameter_start = max(
                        0.0,
                        min(lateral_roots),
                    )
                    clipped_parameter_end = min(
                        1.0,
                        max(lateral_roots),
                    )
                    if clipped_parameter_end <= clipped_parameter_start:
                        continue
                start_projection = (
                    relative_start[0] * candidate_unit[0]
                    + relative_start[1] * candidate_unit[1]
                )
                projection_delta = (
                    evidence_dx * candidate_unit[0]
                    + evidence_dy * candidate_unit[1]
                )
                projections = (
                    start_projection
                    + clipped_parameter_start * projection_delta,
                    start_projection
                    + clipped_parameter_end * projection_delta,
                )
                lower = max(0.0, min(projections))
                upper = min(candidate_length, max(projections))
                if upper > lower:
                    intervals.append(
                        (
                            _canonical_number(candidate_offset + lower),
                            _canonical_number(candidate_offset + upper),
                        )
                    )
        candidate_offset += candidate_length
    return _merge_intervals(intervals)


def _all_assertions(evidence: ExistingAlignmentEvidence) -> tuple[GovernedAssertion, ...]:
    values: list[GovernedAssertion] = []
    if evidence.greenway_qualification is not None:
        values.extend(
            (
                evidence.greenway_qualification.traffic_free,
                evidence.greenway_qualification.lawful_cycling_access,
                evidence.greenway_qualification.continuity,
            )
        )
    if evidence.reusable_asset is not None:
        values.extend(evidence.reusable_asset.assertions())
    if evidence.delivery_evidence is not None:
        values.extend(evidence.delivery_evidence.assertions())
    if evidence.open_diversion is not None:
        values.append(evidence.open_diversion)
    return tuple(values)


def _context_provenance(
    context: AlignmentContextEvidence,
) -> tuple[EvidenceProvenance, ...]:
    return _canonical_provenance(
        [
            observation.provenance
            for observation in (
                context.surface,
                context.facility_quality,
                context.lighting,
                context.road_class,
                context.barrier,
                context.accessibility,
            )
            if observation.provenance is not None
        ]
    )


def _ordered_provenance(
    evidence: tuple[ExistingAlignmentEvidence, ...],
) -> tuple[EvidenceProvenance, ...]:
    return _canonical_provenance(
        [
            record
            for item in evidence
            for record in (
                item.current_status_provenance,
                *tuple(assertion.provenance for assertion in _all_assertions(item)),
                *_context_provenance(item.context),
            )
        ]
    )


def _recognised_at(evidence: ExistingAlignmentEvidence, as_of: date) -> bool:
    if evidence.availability is not RouteAvailability.OPEN:
        return False
    if evidence.current_status_provenance.currency_at(as_of, require_bounded=True) != "current":
        return False
    if evidence.current_route_kind is CurrentRouteKind.CURRENT_NCN:
        return True
    return (
        evidence.current_route_kind is CurrentRouteKind.GREENWAY
        and evidence.greenway_qualification is not None
        and evidence.greenway_qualification.qualifies_at(as_of)
    )


def _reusable_state(
    evidence: tuple[ExistingAlignmentEvidence, ...], as_of: date
) -> tuple[
    EvidenceState,
    tuple[ReusableDimensionAssessment, ...],
    tuple[ExistingAlignmentUnknownReason, ...],
]:
    if not evidence:
        return EvidenceState.UNKNOWN, (), ()
    dimensions = (
        (
            "lawful_access",
            ReusableEvidenceDimension.LAWFUL_ACCESS,
        ),
        (
            "usable_condition",
            ReusableEvidenceDimension.USABLE_CONDITION,
        ),
        ("continuity", ReusableEvidenceDimension.CONTINUITY),
        (
            "responsible_ownership_or_maintenance",
            ReusableEvidenceDimension.RESPONSIBLE_OWNERSHIP_OR_MAINTENANCE,
        ),
    )
    assessments: list[ReusableDimensionAssessment] = []
    reasons: list[ExistingAlignmentUnknownReason] = []
    for field_name, dimension in dimensions:
        assertions = [
            getattr(item.reusable_asset, field_name)
            for item in evidence
            if item.reusable_asset is not None
        ]
        if (
            len(assertions) != len(evidence)
            or not assertions
            or any(
                assertion.provenance.currency_at(as_of, require_bounded=True)
                != "current"
                for assertion in assertions
            )
        ):
            state = EvidenceState.UNKNOWN
            conflicting = False
        else:
            states = {assertion.state for assertion in assertions}
            conflicting = (
                EvidenceState.CONFIRMED in states
                and EvidenceState.ABSENT in states
            )
            if conflicting or EvidenceState.UNKNOWN in states:
                state = EvidenceState.UNKNOWN
            elif states == {EvidenceState.ABSENT}:
                state = EvidenceState.ABSENT
            else:
                state = EvidenceState.CONFIRMED
        assessment = ReusableDimensionAssessment(
            dimension=dimension,
            state=state,
            conflicting=conflicting,
        )
        assessments.append(assessment)
        if conflicting:
            reasons.append(_REUSABLE_DIMENSION_REASONS[dimension][1])
        elif state is EvidenceState.UNKNOWN:
            reasons.append(_REUSABLE_DIMENSION_REASONS[dimension][0])
    dimension_states = tuple(item.state for item in assessments)
    if EvidenceState.UNKNOWN in dimension_states:
        aggregate = EvidenceState.UNKNOWN
    elif EvidenceState.ABSENT in dimension_states:
        aggregate = EvidenceState.ABSENT
    else:
        aggregate = EvidenceState.CONFIRMED
    return aggregate, tuple(assessments), tuple(reasons)


def _delivery_state(
    evidence: tuple[ExistingAlignmentEvidence, ...], as_of: date
) -> tuple[
    EvidenceState,
    tuple[DeliveryDimensionAssessment, ...],
    tuple[ExistingAlignmentUnknownReason, ...],
]:
    if not evidence:
        return EvidenceState.UNKNOWN, (), ()
    dimensions = (
        ("concept", DeliveryEvidenceDimension.CONCEPT),
        ("constraints", DeliveryEvidenceDimension.CONSTRAINTS),
        ("consents", DeliveryEvidenceDimension.CONSENTS),
        ("cost", DeliveryEvidenceDimension.COST),
        (
            "accountable_feasibility",
            DeliveryEvidenceDimension.ACCOUNTABLE_FEASIBILITY,
        ),
    )
    assessments: list[DeliveryDimensionAssessment] = []
    reasons: list[ExistingAlignmentUnknownReason] = []
    for field_name, dimension in dimensions:
        assertions = [
            getattr(item.delivery_evidence, field_name)
            for item in evidence
            if item.delivery_evidence is not None
        ]
        if (
            len(assertions) != len(evidence)
            or not assertions
            or any(
                assertion.provenance.currency_at(as_of, require_bounded=True)
                != "current"
                for assertion in assertions
            )
        ):
            state = EvidenceState.UNKNOWN
            conflicting = False
        else:
            states = {assertion.state for assertion in assertions}
            conflicting = (
                EvidenceState.CONFIRMED in states
                and EvidenceState.ABSENT in states
            )
            if conflicting or EvidenceState.UNKNOWN in states:
                state = EvidenceState.UNKNOWN
            elif states == {EvidenceState.ABSENT}:
                state = EvidenceState.ABSENT
            else:
                state = EvidenceState.CONFIRMED
        assessment = DeliveryDimensionAssessment(
            dimension=dimension,
            state=state,
            conflicting=conflicting,
        )
        assessments.append(assessment)
        if conflicting:
            reasons.append(_DELIVERY_DIMENSION_REASONS[dimension][1])
        elif state is EvidenceState.UNKNOWN:
            reasons.append(_DELIVERY_DIMENSION_REASONS[dimension][0])
    dimension_states = tuple(item.state for item in assessments)
    if EvidenceState.UNKNOWN in dimension_states:
        return EvidenceState.UNKNOWN, tuple(assessments), tuple(reasons)
    if EvidenceState.ABSENT in dimension_states:
        return EvidenceState.ABSENT, tuple(assessments), tuple(reasons)
    return EvidenceState.CONFIRMED, tuple(assessments), tuple(reasons)


def _reuse_availability_state(
    evidence: tuple[ExistingAlignmentEvidence, ...], as_of: date
) -> tuple[EvidenceState, tuple[ExistingAlignmentUnknownReason, ...]]:
    if not evidence:
        return EvidenceState.UNKNOWN, ()
    reasons: set[ExistingAlignmentUnknownReason] = set()
    for item in evidence:
        if item.availability is RouteAvailability.OPEN:
            continue
        if item.availability is RouteAvailability.UNKNOWN:
            reasons.add(ExistingAlignmentUnknownReason.ROUTE_AVAILABILITY_UNKNOWN)
            continue
        diversion = item.open_diversion
        if (
            diversion is None
            or diversion.state is not EvidenceState.CONFIRMED
            or diversion.provenance.currency_at(as_of, require_bounded=True)
            != "current"
        ):
            reasons.add(ExistingAlignmentUnknownReason.CLOSURE_BLOCKER)
            reasons.add(ExistingAlignmentUnknownReason.OPEN_DIVERSION_UNKNOWN)
    return (
        EvidenceState.UNKNOWN if reasons else EvidenceState.CONFIRMED,
        tuple(sorted(reasons)),
    )


def _status_unknown_reasons(
    evidence: tuple[ExistingAlignmentEvidence, ...], as_of: date
) -> set[ExistingAlignmentUnknownReason]:
    reasons: set[ExistingAlignmentUnknownReason] = set()
    for item in evidence:
        currency = item.current_status_provenance.currency_at(as_of, require_bounded=True)
        if currency == "future":
            reasons.add(ExistingAlignmentUnknownReason.FUTURE_EVIDENCE)
        elif currency == "stale":
            reasons.add(ExistingAlignmentUnknownReason.STALE_EVIDENCE)
        elif currency == "unbounded":
            reasons.add(ExistingAlignmentUnknownReason.UNBOUNDED_STATUS_FRESHNESS)
        if not _recognised_at(item, as_of):
            reasons.add(ExistingAlignmentUnknownReason.CURRENT_STATUS_NOT_QUALIFYING)
        if (
            item.current_route_kind is CurrentRouteKind.GREENWAY
            and (
                item.greenway_qualification is None
                or not item.greenway_qualification.qualifies_at(as_of)
            )
        ):
            reasons.add(ExistingAlignmentUnknownReason.GREENWAY_QUALIFICATION_INCOMPLETE)
        for assertion in _all_assertions(item):
            assertion_currency = assertion.provenance.currency_at(
                as_of, require_bounded=True
            )
            if assertion_currency == "future":
                reasons.add(ExistingAlignmentUnknownReason.FUTURE_EVIDENCE)
            elif assertion_currency == "stale":
                reasons.add(ExistingAlignmentUnknownReason.STALE_EVIDENCE)
            elif assertion_currency == "unbounded":
                reasons.add(
                    ExistingAlignmentUnknownReason.UNBOUNDED_EVIDENCE_FRESHNESS
                )
    return reasons


def _context_lineage(
    evidence: ExistingAlignmentEvidence,
    geometry_fingerprint: str,
) -> EvidenceContextLineage:
    provenance = _context_provenance(evidence.context)
    return EvidenceContextLineage(
        evidence_id=evidence.evidence_id,
        geometry_fingerprint=geometry_fingerprint,
        context=evidence.context,
        provenance=provenance,
        context_fingerprint=_fingerprint(
            {
                "evidence_id": evidence.evidence_id,
                "geometry_fingerprint": geometry_fingerprint,
                "context": evidence.context.model_dump(mode="json"),
                "provenance": [
                    item.model_dump(mode="json") for item in provenance
                ],
            }
        ),
    )


def _transition(
    start_m: float,
    end_m: float,
    evidence: tuple[ExistingAlignmentEvidence, ...],
    geometry_fingerprints: dict[str, str],
    as_of: date,
) -> ExistingAlignmentTransition:
    reasons = _status_unknown_reasons(evidence, as_of)
    if not evidence:
        reasons.add(ExistingAlignmentUnknownReason.NO_EVIDENCE)
    recognised_states = {_recognised_at(item, as_of) for item in evidence}
    recognised = bool(evidence) and recognised_states == {True}
    if len(recognised_states) > 1:
        reasons.add(ExistingAlignmentUnknownReason.CONFLICTING_EVIDENCE)
    physical_reuse_state, reusable_assessments, reusable_reasons = _reusable_state(
        evidence, as_of
    )
    reuse_availability_state, availability_reasons = _reuse_availability_state(
        evidence, as_of
    )
    reuse_states = (physical_reuse_state, reuse_availability_state)
    if EvidenceState.UNKNOWN in reuse_states:
        reusable_state = EvidenceState.UNKNOWN
    elif EvidenceState.ABSENT in reuse_states:
        reusable_state = EvidenceState.ABSENT
    else:
        reusable_state = EvidenceState.CONFIRMED
    delivery_state, delivery_assessments, delivery_reasons = _delivery_state(
        evidence, as_of
    )
    if any(
        item.conflicting
        for item in (*reusable_assessments, *delivery_assessments)
    ):
        reasons.add(ExistingAlignmentUnknownReason.CONFLICTING_EVIDENCE)
    reasons.update(reusable_reasons)
    reasons.update(delivery_reasons)
    reasons.update(availability_reasons)
    if evidence and delivery_state is EvidenceState.UNKNOWN:
        reasons.add(ExistingAlignmentUnknownReason.DELIVERY_EVIDENCE_INCOMPLETE)
    if len({item.availability for item in evidence}) > 1:
        reasons.add(ExistingAlignmentUnknownReason.CONFLICTING_EVIDENCE)
    return ExistingAlignmentTransition(
        start_m=start_m,
        end_m=end_m,
        recognised_current_corridor=recognised,
        reusable_asset=reusable_state is EvidenceState.CONFIRMED,
        reusable_asset_evidence_state=reusable_state,
        reuse_availability_evidence_state=reuse_availability_state,
        reusable_dimension_assessments=reusable_assessments,
        delivery_evidence_complete=delivery_state is not EvidenceState.UNKNOWN,
        delivery_evidence_state=delivery_state,
        delivery_dimension_assessments=delivery_assessments,
        route_kinds=tuple(sorted({item.current_route_kind for item in evidence})),
        availability_states=tuple(sorted({item.availability for item in evidence})),
        unknown_reasons=tuple(sorted(reasons)),
        evidence_ids=tuple(sorted(item.evidence_id for item in evidence)),
        evidence_geometry_fingerprints=tuple(
            EvidenceFingerprintBinding(
                evidence_id=item.evidence_id,
                fingerprint=geometry_fingerprints[item.evidence_id],
            )
            for item in sorted(evidence, key=lambda item: item.evidence_id)
        ),
        evidence_provenance_lineage=tuple(
            EvidenceProvenanceLineage(
                evidence_id=item.evidence_id,
                provenance=_ordered_provenance((item,)),
                fingerprint=_fingerprint(
                    [
                        record.model_dump(mode="json")
                        for record in _ordered_provenance((item,))
                    ]
                ),
            )
            for item in sorted(evidence, key=lambda item: item.evidence_id)
        ),
        provenance=_ordered_provenance(evidence),
        context_lineage=tuple(
            _context_lineage(item, geometry_fingerprints[item.evidence_id])
            for item in sorted(evidence, key=lambda item: item.evidence_id)
        ),
    )


def evaluate_existing_alignment_advantage(
    candidate: ExistingAlignmentCandidate,
    evidence: tuple[ExistingAlignmentEvidence, ...],
    *,
    as_of: date,
    match_profile: GeometryMatchProfile,
) -> ExistingAlignmentAdvantage:
    """Match governed geometries and derive evidence coverage without selecting."""
    if type(as_of) is not date:
        raise TypeError("as_of must be a date without coercion")
    if type(evidence) is not tuple:
        raise TypeError("evidence must be an immutable tuple")
    if len({item.evidence_id for item in evidence}) != len(evidence):
        raise ValueError("evidence_id values must be unique")
    candidate_geometry = _parse_line_geometry(
        candidate.geometry_wkt, candidate.geometry_crs, match_profile
    )
    if not isinstance(candidate_geometry, LineString) or not candidate_geometry.is_simple:
        raise ValueError("candidate geometry must be one simple LineString")
    ordered_evidence = tuple(sorted(evidence, key=lambda item: item.evidence_id))
    evidence_geometries = {
        item.evidence_id: _parse_line_geometry(
            item.geometry_wkt, item.geometry_crs, match_profile
        )
        for item in ordered_evidence
    }
    evidence_geometry_fingerprints = {
        item.evidence_id: _geometry_fingerprint(
            evidence_geometries[item.evidence_id], match_profile.crs
        )
        for item in ordered_evidence
    }
    raw_intervals = {
        item.evidence_id: _match_intervals(
            candidate_geometry, evidence_geometries[item.evidence_id], match_profile
        )
        for item in ordered_evidence
    }
    eligible_union_intervals = tuple(
        interval
        for interval in _merge_intervals(
            [
                interval
                for evidence_intervals in raw_intervals.values()
                for interval in evidence_intervals
            ]
        )
        if interval[1] - interval[0] >= match_profile.minimum_match_length_m
    )
    intervals = {
        evidence_id: _merge_intervals(
            [
                (
                    max(evidence_start, eligible_start),
                    min(evidence_end, eligible_end),
                )
                for evidence_start, evidence_end in evidence_intervals
                for eligible_start, eligible_end in eligible_union_intervals
                if min(evidence_end, eligible_end)
                > max(evidence_start, eligible_start)
            ]
        )
        for evidence_id, evidence_intervals in raw_intervals.items()
    }
    boundaries = {0.0, float(candidate_geometry.length)}
    for matched in intervals.values():
        for start_m, end_m in matched:
            boundaries.update((start_m, end_m))
    ordered_boundaries = sorted(boundaries)
    transitions = tuple(
        _transition(
            start_m,
            end_m,
            tuple(
                item
                for item in ordered_evidence
                if any(
                    interval_start <= start_m and interval_end >= end_m
                    for interval_start, interval_end in intervals[item.evidence_id]
                )
            ),
            evidence_geometry_fingerprints,
            as_of,
        )
        for start_m, end_m in pairwise(ordered_boundaries)
        if end_m > start_m
    )
    alignment_length = float(candidate_geometry.length)

    def length(transition: ExistingAlignmentTransition) -> float:
        return transition.end_m - transition.start_m

    matched = sum(length(item) for item in transitions if item.evidence_ids)
    recognised = sum(
        length(item) for item in transitions if item.recognised_current_corridor
    )
    reusable = sum(length(item) for item in transitions if item.reusable_asset)
    declassified = sum(
        length(item)
        for item in transitions
        if CurrentRouteKind.DECLASSIFIED_NCN in item.route_kinds
        and not item.recognised_current_corridor
    )
    unknown = sum(length(item) for item in transitions if not item.evidence_ids)
    delivery = sum(
        length(item) for item in transitions if item.delivery_evidence_complete
    )
    longest = matched_continuous = 0.0
    recognised_longest = recognised_continuous = 0.0
    reusable_longest = reusable_continuous = 0.0
    for item in transitions:
        if item.evidence_ids:
            matched_continuous += length(item)
            longest = max(longest, matched_continuous)
        else:
            matched_continuous = 0.0
        if item.recognised_current_corridor:
            recognised_continuous += length(item)
            recognised_longest = max(recognised_longest, recognised_continuous)
        else:
            recognised_continuous = 0.0
        if item.reusable_asset:
            reusable_continuous += length(item)
            reusable_longest = max(reusable_longest, reusable_continuous)
        else:
            reusable_continuous = 0.0
    candidate_geometry_fingerprint = _geometry_fingerprint(
        candidate_geometry, match_profile.crs
    )
    evidence_payload = [
        {
            "evidence": item.model_dump(
                mode="json",
                exclude={"geometry_wkt", "geometry_crs"},
            ),
            "geometry": _canonical_geometry_payload(
                evidence_geometries[item.evidence_id],
                match_profile.crs,
            ),
        }
        for item in ordered_evidence
    ]
    evidence_fingerprint = _fingerprint(evidence_payload)
    match_fingerprint = _fingerprint(
        {
            "as_of": as_of.isoformat(),
            "candidate_geometry_fingerprint": candidate_geometry_fingerprint,
            "evidence_fingerprint": evidence_fingerprint,
            "geometry_match_profile_fingerprint": match_profile.fingerprint,
            "intervals": intervals,
        }
    )
    matched_evidence_ids = {
        evidence_id
        for transition in transitions
        for evidence_id in transition.evidence_ids
    }
    return ExistingAlignmentAdvantage(
        candidate_id=candidate.candidate_id,
        as_of=as_of,
        directness=candidate.directness,
        geometry_match_profile_fingerprint=match_profile.fingerprint,
        candidate_geometry_fingerprint=candidate_geometry_fingerprint,
        evidence_fingerprint=evidence_fingerprint,
        match_fingerprint=match_fingerprint,
        evidence_geometry_lengths_m=tuple(
            EvidenceGeometryLength(
                evidence_id=item.evidence_id,
                length_m=float(evidence_geometries[item.evidence_id].length),
            )
            for item in ordered_evidence
            if item.evidence_id in matched_evidence_ids
        ),
        alignment_length_m=alignment_length,
        matched_length_m=matched,
        recognised_current_length_m=recognised,
        reusable_asset_length_m=reusable,
        declassified_length_m=declassified,
        unknown_length_m=unknown,
        longest_continuous_match_m=longest,
        longest_continuous_recognised_m=recognised_longest,
        longest_continuous_reusable_m=reusable_longest,
        matched_share=matched / alignment_length,
        recognised_current_share=recognised / alignment_length,
        reusable_asset_share=reusable / alignment_length,
        delivery_evidence_complete_length_m=delivery,
        transitions=transitions,
        gaps=tuple(item for item in transitions if not item.evidence_ids),
        unknown_reasons=tuple(
            sorted({reason for item in transitions for reason in item.unknown_reasons})
        ),
    )


def compare_near_equivalent_existing_alignments(
    profile: NetworkSelectionProfile,
    advantages: tuple[ExistingAlignmentAdvantage, ...],
    *,
    proof: NearEquivalenceProof,
) -> ExistingAlignmentLexicographicComparison:
    """Rank only candidates whose exact evidence has passed mandatory gates."""
    if type(advantages) is not tuple:
        raise TypeError("advantages must be an immutable tuple")
    advantage_by_id = {advantage.candidate_id: advantage for advantage in advantages}
    candidate_ids = tuple(sorted(advantage_by_id))
    if len(candidate_ids) != len(advantages) or len(candidate_ids) < 2:
        raise ValueError("comparison requires at least two uniquely identified candidates")
    if proof.candidate_ids != candidate_ids:
        raise ValueError("near-equivalence proof does not cover the exact candidates")
    if proof.profile_fingerprint != profile.fingerprint:
        raise ValueError("near-equivalence proof profile fingerprint does not match")
    if proof.active_objective is not profile.primary_objective:
        raise ValueError("near-equivalence proof does not use the active objective")
    as_of_values = {advantage.as_of for advantage in advantages}
    if as_of_values != {proof.as_of}:
        raise ValueError("advantages and near-equivalence proof must share one as_of date")
    eligibility_by_id = {item.candidate_id: item for item in proof.eligibility}
    for candidate_id in candidate_ids:
        advantage = advantage_by_id[candidate_id]
        eligibility = eligibility_by_id[candidate_id]
        if (
            eligibility.advantage_fingerprint != advantage.fingerprint
            or eligibility.candidate_geometry_fingerprint
            != advantage.candidate_geometry_fingerprint
            or eligibility.evidence_fingerprint != advantage.evidence_fingerprint
        ):
            raise ValueError(
                f"eligibility proof is not bound to exact advantage evidence: {candidate_id}"
            )
    ranked = tuple(
        sorted(
            candidate_ids,
            key=lambda candidate_id: (
                -advantage_by_id[candidate_id].reusable_asset_share,
                -advantage_by_id[candidate_id].recognised_current_share,
                advantage_by_id[candidate_id].directness,
                candidate_id,
            ),
        )
    )
    return ExistingAlignmentLexicographicComparison(
        as_of=proof.as_of,
        profile_id=profile.profile_id,
        profile_fingerprint=profile.fingerprint,
        active_objective=profile.primary_objective,
        near_equivalence_proof_id=proof.proof_id,
        near_equivalence_proof_fingerprint=proof.fingerprint,
        advantage_fingerprints=tuple(
            CandidateFingerprintBinding(
                candidate_id=candidate_id,
                fingerprint=advantage_by_id[candidate_id].fingerprint,
            )
            for candidate_id in candidate_ids
        ),
        candidate_geometry_fingerprints=tuple(
            CandidateFingerprintBinding(
                candidate_id=candidate_id,
                fingerprint=advantage_by_id[
                    candidate_id
                ].candidate_geometry_fingerprint,
            )
            for candidate_id in candidate_ids
        ),
        evidence_fingerprints=tuple(
            CandidateFingerprintBinding(
                candidate_id=candidate_id,
                fingerprint=advantage_by_id[candidate_id].evidence_fingerprint,
            )
            for candidate_id in candidate_ids
        ),
        advantages=tuple(advantage_by_id[candidate_id] for candidate_id in candidate_ids),
        comparison_values=tuple(
            ExistingAlignmentComparisonValue(
                candidate_id=candidate_id,
                reusable_asset_share=advantage_by_id[
                    candidate_id
                ].reusable_asset_share,
                recognised_current_share=advantage_by_id[
                    candidate_id
                ].recognised_current_share,
                directness=advantage_by_id[candidate_id].directness,
            )
            for candidate_id in candidate_ids
        ),
        ranked_candidate_ids=ranked,
    )
