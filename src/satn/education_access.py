"""Deterministic education-access evidence for Preferred Strategic Alignments.

This module assesses declared, option-specific evidence.  It does not route to
schools or Strategic Education Destinations, nor infer demand, safety,
suitability, ability, or completeness.
All access and independent-travel evidence is deliberately typed so that absent
evidence remains visible rather than becoming an optimistic conclusion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satn.identifiers import stable_id
from satn.models import AccessPointStatus, AccessServiceStatus
from satn.network_selection import IndependentTravelPhase
from satn.runtime_governance_contract import canonical_sha256


class EducationPhase(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    ALL_THROUGH = "all-through"
    SPECIAL = "special"
    UNRESOLVED = "unresolved"


class ConnectorContinuity(StrEnum):
    CONTINUOUS = "continuous"
    DISCONTINUOUS = "discontinuous"
    UNKNOWN = "unknown"


class EvidenceAvailability(StrEnum):
    """Availability of evidence, never a safety, access, or suitability result."""

    AVAILABLE = "evidence-available"
    UNKNOWN = "evidence-unknown"


class IndependentTravelStatus(StrEnum):
    EVIDENCE_AVAILABLE = "evidence-available"
    EVIDENCE_REQUIRED = "evidence-required"


class RouteObservationKind(StrEnum):
    """Claim-safe, bounded route observations available to this assessment."""

    CROSSING_RECORDED = "crossing-recorded"
    LIGHTING_RECORDED = "lighting-recorded"
    SEVERANCE_RECORDED = "severance-recorded"
    CONNECTION_RECORDED = "connection-recorded"


class ExternalEvidenceUnknown(StrEnum):
    """A bounded uncertainty reported by external option evidence."""

    JUNCTION_DESIGN_OUTSIDE_SELECTION_PASS = "junction-design-outside-selection-pass"


class CompilerDerivedUnknown(StrEnum):
    """An output-only uncertainty derived from assessment state."""

    NO_OPTION_SPECIFIC_EVIDENCE = "no-option-specific-education-evidence"
    SCHOOL_PHASE_UNRESOLVED = "school-phase-unresolved-in-governed-register"
    NO_TYPED_SPECIAL_SCHOOL_EVIDENCE = "no-typed-special-school-evidence"
    NO_TYPED_INDEPENDENT_TRAVEL_EVIDENCE = "no-typed-independent-travel-evidence"
    INFERRED_ACCESS_POINT_ENTRANCE_VERIFICATION_UNKNOWN = (
        "inferred-access-point-entrance-verification-unknown"
    )


type EducationUnknown = ExternalEvidenceUnknown | CompilerDerivedUnknown


class SchoolAccessLabel(StrEnum):
    EVIDENCED = "school-access-obligation-evidenced"
    PROVISIONAL = "school-access-obligation-provisionally-evidenced"
    GAP = "school-access-obligation-network-gap"


class StrategicDestinationAccessLabel(StrEnum):
    EVIDENCED = "strategic-education-destination-access-evidenced"
    PROVISIONAL = "strategic-education-destination-access-provisionally-evidenced"
    GAP = "strategic-education-destination-network-gap"


class DistanceEvidenceStatus(StrEnum):
    MEASURED = "measured"
    NOT_OBSERVED = "not-observed"
    NOT_APPLICABLE = "not-applicable"


class IndependentTravelLabel(StrEnum):
    FACTORS_AVAILABLE = "independent-travel-factor-evidence-available"
    FACTORS_REQUIRED = "independent-travel-factor-evidence-required"


class StrategicAdmissionRationale(StrEnum):
    CONFIGURED_DESTINATION = "configured-strategic-education-destination"


class StrategicAdmissionReviewTrigger(StrEnum):
    GOVERNED_RECORD_CHANGES = "governed-destination-record-changes"


class PCTLimitation(StrEnum):
    CANNOT_ESTABLISH_CURRENT_DEMAND = "cannot-establish-current-demand"
    CANNOT_ESTABLISH_ACTUAL_ROUTE_CHOICE = "cannot-establish-actual-route-choice"
    CANNOT_ESTABLISH_SAFETY = "cannot-establish-safety"
    CANNOT_ESTABLISH_COMPLETENESS = "cannot-establish-completeness"


class PCTIncludedPopulation(StrEnum):
    HISTORICAL_SECONDARY_SCHOOL_TRAVEL_RECORDS = "historical-secondary-school-travel-records"


class PCTExcludedPopulation(StrEnum):
    OUTSIDE_SCENARIO_BOUNDARY = "outside-scenario-boundary"


class PCTCoverage(StrEnum):
    HISTORICAL_ORIGIN_DESTINATION = "historical-origin-destination"


_IMMUTABLE_PCT_LIMITATIONS = (
    PCTLimitation.CANNOT_ESTABLISH_CURRENT_DEMAND,
    PCTLimitation.CANNOT_ESTABLISH_ACTUAL_ROUTE_CHOICE,
    PCTLimitation.CANNOT_ESTABLISH_SAFETY,
    PCTLimitation.CANNOT_ESTABLISH_COMPLETENESS,
)
_ID_PATTERN = re.compile(r"^\S+$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_DistanceM = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _SelfRevalidatingModel(_FrozenModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )


class MeasuredDistance(_FrozenModel):
    """A finite, non-negative measured distance."""

    status: Literal[DistanceEvidenceStatus.MEASURED] = DistanceEvidenceStatus.MEASURED
    distance_m: _DistanceM


class DistanceNotObserved(_FrozenModel):
    """A distance for which no governed measurement is available."""

    status: Literal[DistanceEvidenceStatus.NOT_OBSERVED] = DistanceEvidenceStatus.NOT_OBSERVED


class DistanceNotApplicable(_FrozenModel):
    """A distance that does not apply to the assessed evidence relationship."""

    status: Literal[DistanceEvidenceStatus.NOT_APPLICABLE] = DistanceEvidenceStatus.NOT_APPLICABLE


DistanceEvidence = Annotated[
    MeasuredDistance | DistanceNotObserved | DistanceNotApplicable,
    Field(discriminator="status"),
]


def _strict_identifier(value: str) -> str:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError("identifiers must be non-blank and contain no whitespace")
    return value


def _strict_identifier_values(value: tuple[str, ...]) -> tuple[str, ...]:
    if len(value) != len(set(value)):
        raise ValueError("identifier values must not contain duplicates")
    for item in value:
        _strict_identifier(item)
    return tuple(sorted(value))


def _trimmed_nonblank_text(value: str) -> str:
    if not value or value != value.strip():
        raise ValueError("controlled text must be non-blank and trimmed")
    return value


def _canonical_enum_values[T: StrEnum](values: tuple[T, ...]) -> tuple[T, ...]:
    return tuple(sorted(set(values), key=lambda value: value.value))


def _strict_model_identifiers(
    values: tuple[RouteQualityEvidence, ...], label: str
) -> tuple[RouteQualityEvidence, ...]:
    identifiers = tuple(value.evidence_id for value in values)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{label} must not contain duplicate evidence IDs")
    return tuple(
        sorted(
            values,
            key=lambda value: (
                value.evidence_id,
                value.observation.value,
                value.as_of or date.min,
            ),
        )
    )


def _with_generated_unknowns(
    supplied: tuple[ExternalEvidenceUnknown, ...],
    *generated: CompilerDerivedUnknown,
) -> tuple[EducationUnknown, ...]:
    return tuple(sorted({*supplied, *generated}, key=lambda unknown: unknown.value))


def _require_literal_true(value: object) -> bool:
    if value is not True:
        raise ValueError("governed authority fields must be literal true")
    return True


def _school_obligation_id(school_id: str) -> str:
    return stable_id("school-access-obligation", school_id)


def _strategic_destination_access_id(
    option_id: str,
    strategic_destination_id: str,
) -> str:
    return stable_id(
        "strategic-education-destination-access",
        option_id,
        strategic_destination_id,
    )


def _school_evidence_request_id(school_id: str) -> str:
    return stable_id("school-evidence-request", school_id)


def _independent_travel_opportunity_id(option_id: str, school_id: str) -> str:
    return stable_id("independent-travel-opportunity", option_id, school_id)


def _special_school_view_id(option_id: str, school_id: str) -> str:
    return stable_id("special-school-evidence", option_id, school_id)


def _network_gap_id(
    gap_kind: Literal[
        "school-access-obligation",
        "strategic-education-destination",
    ],
    reason: Literal["no-candidate-options", "candidate-option-unserved"],
    target_id: str,
    option_id: str | None,
) -> str:
    context = (
        ("network-gap", gap_kind, reason, target_id)
        if option_id is None
        else ("network-gap", gap_kind, reason, target_id, option_id)
    )
    return stable_id(*context)


class SchoolRegisterEvidence(_FrozenModel):
    """A current register snapshot used to create School Access Obligations."""

    evidence_id: str
    source_name: str = Field(min_length=1)
    as_of: date
    governed: Literal[True] = True
    current: Literal[True] = True

    _identifier = field_validator("evidence_id")(_strict_identifier)
    _controlled_text = field_validator("source_name")(_trimmed_nonblank_text)
    _authority = field_validator("governed", "current", mode="before")(_require_literal_true)


class School(_FrozenModel):
    """A governed School register row."""

    school_id: str
    name: str = Field(min_length=1)
    phase: EducationPhase
    source_evidence_id: str

    _identifiers = field_validator("school_id", "source_evidence_id")(_strict_identifier)
    _controlled_text = field_validator("name")(_trimmed_nonblank_text)


class StrategicEducationDestination(_FrozenModel):
    """One frozen and governed admission of a non-school destination."""

    record_id: str
    record_version: str
    strategic_destination_id: str
    name: str = Field(min_length=1)
    source_evidence_id: str
    admitted_on: date
    rationale: StrategicAdmissionRationale
    admission_evidence_ids: tuple[str, ...] = Field(min_length=1)
    review_trigger: StrategicAdmissionReviewTrigger
    access_evidence_ids: tuple[str, ...] = Field(min_length=1)
    governed: Literal[True] = True

    _identifiers = field_validator(
        "record_id",
        "record_version",
        "strategic_destination_id",
        "source_evidence_id",
    )(_strict_identifier)
    _controlled_text = field_validator("name")(_trimmed_nonblank_text)
    _evidence_identifiers = field_validator("admission_evidence_ids", "access_evidence_ids")(
        _strict_identifier_values
    )
    _authority = field_validator("governed", mode="before")(_require_literal_true)


class RouteQualityEvidence(_FrozenModel):
    """A bounded route observation, never a safety conclusion."""

    evidence_id: str
    observation: RouteObservationKind
    as_of: date | None = None

    _identifier = field_validator("evidence_id")(_strict_identifier)


class EvidenceFactor(_FrozenModel):
    """The evidence state for one named factor and the records behind it."""

    availability: EvidenceAvailability
    evidence_ids: tuple[str, ...] = ()

    _identifiers = field_validator("evidence_ids")(_strict_identifier_values)

    @model_validator(mode="after")
    def validate_available_evidence(self) -> Self:
        if self.availability is EvidenceAvailability.AVAILABLE and not self.evidence_ids:
            raise ValueError("available evidence factors require evidence IDs")
        if self.availability is EvidenceAvailability.UNKNOWN and self.evidence_ids:
            raise ValueError("unknown evidence factors must not claim evidence IDs")
        return self


class IndependentTravelEvidence(_FrozenModel):
    """Separate, factor-level evidence for independent-travel consideration."""

    gradient: EvidenceFactor
    road_class: EvidenceFactor
    speed: EvidenceFactor
    crossing: EvidenceFactor
    separation: EvidenceFactor
    lighting: EvidenceFactor
    severance: EvidenceFactor
    audit: EvidenceFactor

    def is_complete(self) -> bool:
        return all(
            factor.availability is EvidenceAvailability.AVAILABLE
            for factor in (
                self.gradient,
                self.road_class,
                self.speed,
                self.crossing,
                self.separation,
                self.lighting,
                self.severance,
                self.audit,
            )
        )


class SpecialSchoolEvidence(_FrozenModel):
    """Special-school evidence is separate from ordinary school obligations."""

    accessibility: EvidenceFactor
    support: EvidenceFactor
    independent_travel: EvidenceFactor


class SupplementaryPCTEvidence(_FrozenModel):
    """Historical PCT context that cannot establish a selection conclusion."""

    evidence_id: str
    phase: Literal[EducationPhase.SECONDARY]
    scenario_id: str
    method_version: str
    routing_version: str
    included_population: PCTIncludedPopulation
    excluded_population: PCTExcludedPopulation
    coverage: PCTCoverage
    limitations: tuple[PCTLimitation, ...] = Field(default=(), validate_default=True)
    disposition: Literal["supplementary"] = "supplementary"
    as_of: Literal[2011] = 2011

    _identifiers = field_validator(
        "evidence_id", "scenario_id", "method_version", "routing_version"
    )(_strict_identifier)

    @field_validator("limitations")
    @classmethod
    def append_immutable_limitations(
        cls, values: tuple[PCTLimitation, ...]
    ) -> tuple[PCTLimitation, ...]:
        # PCT limitations are a non-configurable safety boundary.  Callers may
        # supply a legacy ordering or duplicates, but publication is always the
        # one canonical complete tuple.
        return _IMMUTABLE_PCT_LIMITATIONS


class _OptionAccessEvidence(_FrozenModel):
    """Shared bounded evidence for one candidate option."""

    option_id: str
    connector_distance: DistanceEvidence = Field(default_factory=DistanceNotObserved)
    connector_continuity: ConnectorContinuity = ConnectorContinuity.UNKNOWN
    access_point_status: AccessPointStatus
    destination_distance: DistanceEvidence = Field(default_factory=DistanceNotObserved)
    access_evidence_ids: tuple[str, ...] = ()
    support_evidence_ids: tuple[str, ...] = ()
    route_quality_evidence: tuple[RouteQualityEvidence, ...] = ()
    unknowns: tuple[ExternalEvidenceUnknown, ...] = ()

    _identifier = field_validator("option_id")(_strict_identifier)
    _evidence_identifiers = field_validator("access_evidence_ids", "support_evidence_ids")(
        _strict_identifier_values
    )
    _unknowns = field_validator("unknowns")(_canonical_enum_values)
    _unique_route_evidence = field_validator("route_quality_evidence")(
        lambda values: _strict_model_identifiers(values, "route-quality evidence")
    )

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.connector_continuity is ConnectorContinuity.CONTINUOUS and (
            not isinstance(self.connector_distance, MeasuredDistance)
        ):
            raise ValueError("continuous connector evidence requires a measured connector distance")
        if self.access_point_status is AccessPointStatus.MAPPED and not self.access_evidence_ids:
            raise ValueError("mapped access points require access evidence IDs")
        if self.access_point_status is AccessPointStatus.INFERRED and not self.access_evidence_ids:
            raise ValueError("inferred access points require access evidence IDs")
        if self.access_point_status is AccessPointStatus.UNRESOLVED and self.access_evidence_ids:
            raise ValueError("unresolved access points must not claim access evidence IDs")
        return self


class SchoolAccessEvidence(_OptionAccessEvidence):
    """Option-specific evidence for one School Access Obligation."""

    evidence_kind: Literal["school-access-obligation"] = "school-access-obligation"
    school_id: str
    independent_travel_evidence: IndependentTravelEvidence | None = None
    special_school_evidence: SpecialSchoolEvidence | None = None

    _school_identifier = field_validator("school_id")(_strict_identifier)


class StrategicEducationDestinationEvidence(_OptionAccessEvidence):
    """Option-specific evidence for one Strategic Education Destination."""

    evidence_kind: Literal["strategic-education-destination"] = "strategic-education-destination"
    strategic_destination_id: str

    _destination_identifier = field_validator("strategic_destination_id")(_strict_identifier)


OptionEducationEvidence = Annotated[
    SchoolAccessEvidence | StrategicEducationDestinationEvidence,
    Field(discriminator="evidence_kind"),
]


class EducationAccessSourceBinding(_SelfRevalidatingModel):
    """Exact governed target and option evidence behind one conclusion."""

    source_record_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    option_evidence_fingerprint: str = Field(pattern=_SHA256_PATTERN)


class SchoolEvidenceRequest(_SelfRevalidatingModel):
    request_id: str
    school_id: str
    reason: Literal["unresolved-school-phase"]
    required_evidence: Literal["current-governed-school-register-phase"]
    source_binding: EducationAccessSourceBinding

    _identifiers = field_validator("request_id", "school_id")(_strict_identifier)

    @model_validator(mode="after")
    def validate_canonical_request_id(self) -> Self:
        if self.request_id != _school_evidence_request_id(self.school_id):
            raise ValueError("School Evidence Request requires its canonical request_id")
        return self


class SchoolAccessObligation(_SelfRevalidatingModel):
    """One option-specific, evidence-bounded School Access Obligation."""

    obligation_id: str
    option_id: str
    school_id: str
    name: str
    phase: EducationPhase
    status: AccessServiceStatus
    public_label: SchoolAccessLabel
    access_point_status: AccessPointStatus
    access_evidence_ids: tuple[str, ...]
    support_evidence_ids: tuple[str, ...]
    connector_distance: DistanceEvidence
    connector_continuity: ConnectorContinuity
    destination_distance: DistanceEvidence
    route_quality_evidence: tuple[RouteQualityEvidence, ...]
    unknowns: tuple[EducationUnknown, ...]
    source_binding: EducationAccessSourceBinding

    _identifiers = field_validator("obligation_id", "option_id", "school_id")(_strict_identifier)
    _evidence_identifiers = field_validator("access_evidence_ids", "support_evidence_ids")(
        _strict_identifier_values
    )
    _controlled_text = field_validator("name")(_trimmed_nonblank_text)
    _unknowns = field_validator("unknowns")(_canonical_enum_values)
    _route_evidence = field_validator("route_quality_evidence")(
        lambda values: _strict_model_identifiers(values, "route-quality evidence")
    )

    @model_validator(mode="after")
    def validate_semantics(self) -> Self:
        if self.obligation_id != _school_obligation_id(self.school_id):
            raise ValueError("School Access Obligation requires its canonical obligation_id")
        expected_status = _derived_access_status(
            school_phase=self.phase,
            access_point_status=self.access_point_status,
            connector_continuity=self.connector_continuity,
            connector_distance=self.connector_distance,
            access_evidence_ids=self.access_evidence_ids,
            support_evidence_ids=self.support_evidence_ids,
        )
        if self.status is not expected_status:
            raise ValueError("School Access Obligation status contradicts its access evidence")
        if self.public_label is not _school_access_label(self.status):
            raise ValueError("School Access Obligation label must match its status")
        _validate_access_output_evidence(
            self.access_point_status,
            self.connector_continuity,
            self.connector_distance,
            self.access_evidence_ids,
        )
        return self


class StrategicEducationDestinationAccess(_SelfRevalidatingModel):
    """Option-specific access evidence for a Strategic Education Destination."""

    access_id: str
    option_id: str
    strategic_destination_id: str
    name: str
    status: AccessServiceStatus
    public_label: StrategicDestinationAccessLabel
    access_point_status: AccessPointStatus
    access_evidence_ids: tuple[str, ...]
    support_evidence_ids: tuple[str, ...]
    connector_distance: DistanceEvidence
    connector_continuity: ConnectorContinuity
    destination_distance: DistanceEvidence
    route_quality_evidence: tuple[RouteQualityEvidence, ...]
    unknowns: tuple[EducationUnknown, ...]
    source_binding: EducationAccessSourceBinding

    _identifiers = field_validator("access_id", "option_id", "strategic_destination_id")(
        _strict_identifier
    )
    _evidence_identifiers = field_validator("access_evidence_ids", "support_evidence_ids")(
        _strict_identifier_values
    )
    _controlled_text = field_validator("name")(_trimmed_nonblank_text)
    _unknowns = field_validator("unknowns")(_canonical_enum_values)
    _route_evidence = field_validator("route_quality_evidence")(
        lambda values: _strict_model_identifiers(values, "route-quality evidence")
    )

    @model_validator(mode="after")
    def validate_semantics(self) -> Self:
        expected = _strategic_destination_access_id(
            self.option_id,
            self.strategic_destination_id,
        )
        if self.access_id != expected:
            raise ValueError(
                "Strategic Education Destination Access requires its canonical access_id"
            )
        expected_status = _derived_access_status(
            school_phase=None,
            access_point_status=self.access_point_status,
            connector_continuity=self.connector_continuity,
            connector_distance=self.connector_distance,
            access_evidence_ids=self.access_evidence_ids,
            support_evidence_ids=self.support_evidence_ids,
        )
        if self.status is not expected_status:
            raise ValueError(
                "Strategic Education Destination Access status contradicts its access evidence"
            )
        if self.public_label is not _strategic_destination_access_label(self.status):
            raise ValueError("Strategic Education Destination Access label must match its status")
        _validate_access_output_evidence(
            self.access_point_status,
            self.connector_continuity,
            self.connector_distance,
            self.access_evidence_ids,
        )
        return self


class IndependentTravelOpportunity(_SelfRevalidatingModel):
    """A distinct factor-evidence view for secondary education phases only."""

    opportunity_id: str
    option_id: str
    school_id: str
    phase: IndependentTravelPhase
    status: IndependentTravelStatus
    public_label: IndependentTravelLabel
    evidence: IndependentTravelEvidence
    unknowns: tuple[EducationUnknown, ...]
    source_binding: EducationAccessSourceBinding

    _identifiers = field_validator("opportunity_id", "option_id", "school_id")(_strict_identifier)
    _unknowns = field_validator("unknowns")(_canonical_enum_values)

    @model_validator(mode="after")
    def validate_semantics(self) -> Self:
        expected_id = _independent_travel_opportunity_id(
            self.option_id,
            self.school_id,
        )
        if self.opportunity_id != expected_id:
            raise ValueError("Independent-Travel Opportunity requires its canonical opportunity_id")
        expected_status = (
            IndependentTravelStatus.EVIDENCE_AVAILABLE
            if self.evidence.is_complete()
            else IndependentTravelStatus.EVIDENCE_REQUIRED
        )
        if self.status is not expected_status:
            raise ValueError(
                "Independent-Travel Opportunity status contradicts its factor evidence"
            )
        expected_label = (
            IndependentTravelLabel.FACTORS_AVAILABLE
            if expected_status is IndependentTravelStatus.EVIDENCE_AVAILABLE
            else IndependentTravelLabel.FACTORS_REQUIRED
        )
        if self.public_label is not expected_label:
            raise ValueError("Independent-Travel Opportunity label must match its evidence status")
        return self


class SpecialSchoolAccessibilityView(_SelfRevalidatingModel):
    """A non-generic accessibility, support, and independent-travel evidence view."""

    view_id: str
    option_id: str
    school_id: str
    accessibility: EvidenceFactor
    support: EvidenceFactor
    independent_travel: EvidenceFactor
    unknowns: tuple[EducationUnknown, ...]
    source_binding: EducationAccessSourceBinding

    _identifiers = field_validator("view_id", "option_id", "school_id")(_strict_identifier)
    _unknowns = field_validator("unknowns")(_canonical_enum_values)

    @model_validator(mode="after")
    def validate_canonical_view_id(self) -> Self:
        if self.view_id != _special_school_view_id(self.option_id, self.school_id):
            raise ValueError("Special School Accessibility View requires its canonical view_id")
        return self


class NetworkGap(_SelfRevalidatingModel):
    """A deterministic fail-closed gap for an unserved governed requirement."""

    gap_id: str
    reason: Literal["no-candidate-options", "candidate-option-unserved"]
    source_binding: EducationAccessSourceBinding | None = None

    _identifier = field_validator("gap_id")(_strict_identifier)

    @model_validator(mode="after")
    def reject_untyped_gap(self) -> Self:
        if type(self) is NetworkGap:
            raise ValueError("NetworkGap must be a typed Network Gap model")
        return self


class SchoolAccessNetworkGap(NetworkGap):
    gap_kind: Literal["school-access-obligation"] = "school-access-obligation"
    school_id: str
    obligation_id: str
    option_id: str | None = None
    public_label: Literal[
        "no-candidate-options-for-school-access-obligation",
        "candidate-option-does-not-serve-school-access-obligation",
    ]
    source_binding: EducationAccessSourceBinding

    _school_identifiers = field_validator("school_id", "obligation_id")(_strict_identifier)
    _option_identifier = field_validator("option_id")(
        lambda value: _strict_identifier(value) if value is not None else value
    )

    @model_validator(mode="after")
    def validate_reason_and_option(self) -> Self:
        if self.reason == "no-candidate-options":
            if self.option_id is not None:
                raise ValueError("no-candidate gaps must not claim a candidate option")
            expected_label = "no-candidate-options-for-school-access-obligation"
        else:
            if self.option_id is None:
                raise ValueError("candidate-option gaps require the unserved candidate option")
            expected_label = "candidate-option-does-not-serve-school-access-obligation"
        if self.public_label != expected_label:
            raise ValueError("School Access Network Gap label must match its reason")
        if self.obligation_id != _school_obligation_id(self.school_id):
            raise ValueError("School Access Network Gap requires its canonical obligation_id")
        expected_gap_id = _network_gap_id(
            self.gap_kind,
            self.reason,
            self.school_id,
            self.option_id,
        )
        if self.gap_id != expected_gap_id:
            raise ValueError("School Access Network Gap requires its canonical gap_id")
        return self


class StrategicEducationDestinationNetworkGap(NetworkGap):
    gap_kind: Literal["strategic-education-destination"] = "strategic-education-destination"
    reason: Literal["no-candidate-options", "candidate-option-unserved"]
    strategic_destination_id: str
    option_id: str | None = None
    public_label: Literal[
        "no-candidate-options-for-strategic-education-destination",
        "candidate-option-does-not-serve-strategic-education-destination",
    ]
    source_binding: EducationAccessSourceBinding

    _destination_identifier = field_validator("strategic_destination_id")(_strict_identifier)
    _option_identifier = field_validator("option_id")(
        lambda value: _strict_identifier(value) if value is not None else value
    )

    @model_validator(mode="after")
    def validate_reason_and_option(self) -> Self:
        if self.reason == "no-candidate-options":
            if self.option_id is not None:
                raise ValueError("no-candidate gaps must not claim a candidate option")
            expected_label = "no-candidate-options-for-strategic-education-destination"
        else:
            if self.option_id is None:
                raise ValueError("candidate-option gaps require the unserved candidate option")
            expected_label = "candidate-option-does-not-serve-strategic-education-destination"
        if self.public_label != expected_label:
            raise ValueError(
                "Strategic Education Destination Network Gap label must match its reason"
            )
        expected_gap_id = _network_gap_id(
            self.gap_kind,
            self.reason,
            self.strategic_destination_id,
            self.option_id,
        )
        if self.gap_id != expected_gap_id:
            raise ValueError(
                "Strategic Education Destination Network Gap requires its canonical gap_id"
            )
        return self


NetworkGapEvidence = Annotated[
    SchoolAccessNetworkGap | StrategicEducationDestinationNetworkGap,
    Field(discriminator="gap_kind"),
]


def _network_gap_sort_key(
    gap: SchoolAccessNetworkGap | StrategicEducationDestinationNetworkGap,
) -> tuple[str, str, str]:
    if isinstance(gap, SchoolAccessNetworkGap):
        return (gap.gap_kind, gap.school_id, gap.option_id or "")
    return (gap.gap_kind, gap.strategic_destination_id, gap.option_id or "")


def _option_evidence_key(
    evidence: SchoolAccessEvidence | StrategicEducationDestinationEvidence,
) -> tuple[str, str, str]:
    if isinstance(evidence, SchoolAccessEvidence):
        return (evidence.option_id, evidence.evidence_kind, evidence.school_id)
    return (
        evidence.option_id,
        evidence.evidence_kind,
        evidence.strategic_destination_id,
    )


def _revalidate_exact_model[T: BaseModel](
    value: object,
    expected_type: type[T],
    label: str,
) -> T:
    if type(value) is not expected_type:
        raise ValueError(f"{label} must use the exact canonical model type")
    assert isinstance(value, BaseModel)
    return expected_type.model_validate(
        value.model_dump(mode="python", round_trip=True, warnings=False)
    )


def _revalidate_option_evidence(
    value: object,
) -> SchoolAccessEvidence | StrategicEducationDestinationEvidence:
    if type(value) is SchoolAccessEvidence:
        expected_type: type[SchoolAccessEvidence] = SchoolAccessEvidence
        expected_kind = "school-access-obligation"
    elif type(value) is StrategicEducationDestinationEvidence:
        expected_type = StrategicEducationDestinationEvidence
        expected_kind = "strategic-education-destination"
    else:
        raise ValueError("option evidence must use an exact canonical concrete evidence model")
    assert isinstance(value, BaseModel)
    payload = value.model_dump(mode="python", round_trip=True, warnings=False)
    if payload.get("evidence_kind") != expected_kind:
        raise ValueError("option evidence_kind must match its concrete evidence model")
    return expected_type.model_validate(payload)


def _raw_sequence(value: object, label: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list or tuple of canonical records")
    return tuple(value)


def _revalidate_raw_model[T: BaseModel](
    value: object,
    expected_type: type[T],
    label: str,
) -> T:
    if type(value) is expected_type:
        return _revalidate_exact_model(value, expected_type, label)
    if isinstance(value, dict):
        return expected_type.model_validate(value)
    raise ValueError(f"{label} must use the exact canonical model type")


def _revalidate_raw_model_sequence[T: BaseModel](
    value: object,
    expected_type: type[T],
    label: str,
) -> tuple[T, ...]:
    return tuple(
        _revalidate_raw_model(item, expected_type, label) for item in _raw_sequence(value, label)
    )


def _revalidate_raw_option_evidence(
    value: object,
) -> SchoolAccessEvidence | StrategicEducationDestinationEvidence:
    if isinstance(value, dict):
        evidence_kind = value.get("evidence_kind")
        if evidence_kind == "school-access-obligation":
            return SchoolAccessEvidence.model_validate(value)
        if evidence_kind == "strategic-education-destination":
            return StrategicEducationDestinationEvidence.model_validate(value)
        raise ValueError("option evidence_kind must identify a canonical concrete evidence model")
    return _revalidate_option_evidence(value)


def _revalidate_raw_option_evidence_sequence(
    value: object,
) -> tuple[SchoolAccessEvidence | StrategicEducationDestinationEvidence, ...]:
    return tuple(
        _revalidate_raw_option_evidence(item) for item in _raw_sequence(value, "option evidence")
    )


def _revalidate_raw_network_gap(
    value: object,
) -> SchoolAccessNetworkGap | StrategicEducationDestinationNetworkGap:
    if isinstance(value, dict):
        gap_kind = value.get("gap_kind")
        if gap_kind == "school-access-obligation":
            return SchoolAccessNetworkGap.model_validate(value)
        if gap_kind == "strategic-education-destination":
            return StrategicEducationDestinationNetworkGap.model_validate(value)
        raise ValueError("Network Gap gap_kind must identify a canonical concrete gap model")
    if type(value) is SchoolAccessNetworkGap:
        return _revalidate_exact_model(
            value,
            SchoolAccessNetworkGap,
            "School Access Network Gap",
        )
    if type(value) is StrategicEducationDestinationNetworkGap:
        return _revalidate_exact_model(
            value,
            StrategicEducationDestinationNetworkGap,
            "Strategic Education Destination Network Gap",
        )
    raise ValueError("Network Gap must use an exact canonical concrete model")


def _model_fingerprint(value: BaseModel) -> str:
    return canonical_sha256(value.model_dump(mode="json"))


def _validate_unique(values: tuple[object, ...], key: str | tuple[str, ...], label: str) -> None:
    keys = tuple(
        getattr(value, key) if isinstance(key, str) else tuple(getattr(value, name) for name in key)
        for value in values
    )
    if len(keys) != len(set(keys)):
        raise ValueError(f"{label} values must be unique")


def _validate_unique_keys(keys: tuple[object, ...], label: str) -> None:
    if len(keys) != len(set(keys)):
        raise ValueError(f"{label} values must be unique")


def _validate_external_unknowns(
    option_evidence: tuple[OptionEducationEvidence, ...],
) -> None:
    if any(
        not isinstance(unknown, ExternalEvidenceUnknown)
        for observation in option_evidence
        for unknown in observation.unknowns
    ):
        raise ValueError("option evidence unknowns must contain only external evidence unknowns")


def _resolve_option_ids(
    declared_option_ids: tuple[str, ...],
    option_evidence: tuple[OptionEducationEvidence, ...],
) -> tuple[str, ...]:
    evidence_option_ids = {item.option_id for item in option_evidence}
    undeclared_option_ids = evidence_option_ids.difference(declared_option_ids)
    if undeclared_option_ids:
        formatted = ", ".join(sorted(undeclared_option_ids))
        raise ValueError(
            f"option evidence must resolve to a declared candidate option: {formatted}"
        )
    return declared_option_ids


def _validate_school_evidence(
    register_evidence: SchoolRegisterEvidence, schools: tuple[School, ...]
) -> None:
    for school in schools:
        if school.source_evidence_id != register_evidence.evidence_id:
            raise ValueError(
                "schools must resolve to the current governed school-register evidence"
            )


def _validate_option_evidence_places(
    option_evidence: tuple[OptionEducationEvidence, ...],
    schools_by_id: dict[str, School],
    destinations_by_id: dict[str, StrategicEducationDestination],
) -> None:
    for observation in option_evidence:
        if isinstance(observation, StrategicEducationDestinationEvidence):
            if observation.strategic_destination_id not in destinations_by_id:
                raise ValueError(
                    "strategic destination evidence must resolve to a known "
                    "Strategic Education Destination"
                )
            continue
        school = schools_by_id.get(observation.school_id)
        if school is None:
            raise ValueError("School access evidence must resolve to a known School")
        if observation.independent_travel_evidence is not None and (
            school.phase not in {EducationPhase.SECONDARY, EducationPhase.ALL_THROUGH}
        ):
            raise ValueError(
                "independent-travel evidence is only permitted for secondary or all-through schools"
            )
        if observation.special_school_evidence is not None and (
            school.phase is not EducationPhase.SPECIAL
        ):
            raise ValueError("special-school evidence is only permitted for special schools")


def _phase_requests(
    schools: tuple[School, ...],
    option_evidence: tuple[OptionEducationEvidence, ...],
) -> list[SchoolEvidenceRequest]:
    return [
        SchoolEvidenceRequest(
            request_id=_school_evidence_request_id(school.school_id),
            school_id=school.school_id,
            reason="unresolved-school-phase",
            required_evidence="current-governed-school-register-phase",
            source_binding=_source_binding(
                school,
                None,
                None,
                "school-access-obligation",
                school.school_id,
                option_evidence,
            ),
        )
        for school in schools
        if school.phase is EducationPhase.UNRESOLVED
    ]


def _no_candidate_gaps(
    schools: tuple[School, ...],
    destinations: tuple[StrategicEducationDestination, ...],
    option_evidence: tuple[OptionEducationEvidence, ...],
) -> tuple[SchoolAccessNetworkGap | StrategicEducationDestinationNetworkGap, ...]:
    school_gaps = (
        SchoolAccessNetworkGap(
            gap_id=_network_gap_id(
                "school-access-obligation",
                "no-candidate-options",
                school.school_id,
                None,
            ),
            school_id=school.school_id,
            obligation_id=_school_obligation_id(school.school_id),
            reason="no-candidate-options",
            public_label="no-candidate-options-for-school-access-obligation",
            source_binding=_source_binding(
                school,
                None,
                None,
                "school-access-obligation",
                school.school_id,
                option_evidence,
            ),
        )
        for school in schools
    )
    destination_gaps = (
        StrategicEducationDestinationNetworkGap(
            gap_id=_network_gap_id(
                "strategic-education-destination",
                "no-candidate-options",
                destination.strategic_destination_id,
                None,
            ),
            strategic_destination_id=destination.strategic_destination_id,
            reason="no-candidate-options",
            public_label=("no-candidate-options-for-strategic-education-destination"),
            source_binding=_source_binding(
                destination,
                None,
                None,
                "strategic-education-destination",
                destination.strategic_destination_id,
                option_evidence,
            ),
        )
        for destination in destinations
    )
    return tuple(
        sorted(
            (*school_gaps, *destination_gaps),
            key=_network_gap_sort_key,
        )
    )


def _candidate_school_gap(
    obligation: SchoolAccessObligation,
) -> SchoolAccessNetworkGap:
    return SchoolAccessNetworkGap(
        gap_id=_network_gap_id(
            "school-access-obligation",
            "candidate-option-unserved",
            obligation.school_id,
            obligation.option_id,
        ),
        school_id=obligation.school_id,
        obligation_id=obligation.obligation_id,
        option_id=obligation.option_id,
        reason="candidate-option-unserved",
        public_label=("candidate-option-does-not-serve-school-access-obligation"),
        source_binding=obligation.source_binding,
    )


def _candidate_destination_gap(
    access: StrategicEducationDestinationAccess,
) -> StrategicEducationDestinationNetworkGap:
    return StrategicEducationDestinationNetworkGap(
        gap_id=_network_gap_id(
            "strategic-education-destination",
            "candidate-option-unserved",
            access.strategic_destination_id,
            access.option_id,
        ),
        strategic_destination_id=access.strategic_destination_id,
        option_id=access.option_id,
        reason="candidate-option-unserved",
        public_label=("candidate-option-does-not-serve-strategic-education-destination"),
        source_binding=access.source_binding,
    )


def _school_access_obligation(
    option_id: str,
    school: School,
    observation: SchoolAccessEvidence | None,
    source_binding: EducationAccessSourceBinding,
) -> SchoolAccessObligation:
    access_point_status = (
        observation.access_point_status if observation else AccessPointStatus.UNRESOLVED
    )
    access_evidence = observation.access_evidence_ids if observation else ()
    support_evidence = observation.support_evidence_ids if observation else ()
    external_unknowns = observation.unknowns if observation else ()
    generated_unknowns: list[CompilerDerivedUnknown] = []
    if observation is None:
        generated_unknowns.append(CompilerDerivedUnknown.NO_OPTION_SPECIFIC_EVIDENCE)
    if school.phase is EducationPhase.UNRESOLVED:
        generated_unknowns.append(CompilerDerivedUnknown.SCHOOL_PHASE_UNRESOLVED)
    if school.phase is EducationPhase.SPECIAL and (
        observation is None or observation.special_school_evidence is None
    ):
        generated_unknowns.append(CompilerDerivedUnknown.NO_TYPED_SPECIAL_SCHOOL_EVIDENCE)
    if access_point_status is AccessPointStatus.INFERRED:
        generated_unknowns.append(
            CompilerDerivedUnknown.INFERRED_ACCESS_POINT_ENTRANCE_VERIFICATION_UNKNOWN
        )
    status = _status(school.phase, observation)
    return SchoolAccessObligation(
        obligation_id=_school_obligation_id(school.school_id),
        option_id=option_id,
        school_id=school.school_id,
        name=school.name,
        phase=school.phase,
        status=status,
        public_label=_school_access_label(status),
        access_point_status=access_point_status,
        access_evidence_ids=access_evidence,
        support_evidence_ids=support_evidence,
        connector_distance=(
            observation.connector_distance if observation else DistanceNotObserved()
        ),
        connector_continuity=(
            observation.connector_continuity if observation else ConnectorContinuity.UNKNOWN
        ),
        destination_distance=(
            observation.destination_distance if observation else DistanceNotObserved()
        ),
        route_quality_evidence=observation.route_quality_evidence if observation else (),
        unknowns=_with_generated_unknowns(external_unknowns, *generated_unknowns),
        source_binding=source_binding,
    )


def _strategic_destination_access(
    option_id: str,
    destination: StrategicEducationDestination,
    observation: StrategicEducationDestinationEvidence | None,
    source_binding: EducationAccessSourceBinding,
) -> StrategicEducationDestinationAccess:
    access_point_status = (
        observation.access_point_status if observation else AccessPointStatus.UNRESOLVED
    )
    external_unknowns = observation.unknowns if observation else ()
    generated_unknowns: list[CompilerDerivedUnknown] = []
    if observation is None:
        generated_unknowns.append(CompilerDerivedUnknown.NO_OPTION_SPECIFIC_EVIDENCE)
    if access_point_status is AccessPointStatus.INFERRED:
        generated_unknowns.append(
            CompilerDerivedUnknown.INFERRED_ACCESS_POINT_ENTRANCE_VERIFICATION_UNKNOWN
        )
    status = _status(None, observation)
    return StrategicEducationDestinationAccess(
        access_id=_strategic_destination_access_id(
            option_id,
            destination.strategic_destination_id,
        ),
        option_id=option_id,
        strategic_destination_id=destination.strategic_destination_id,
        name=destination.name,
        status=status,
        public_label=_strategic_destination_access_label(status),
        access_point_status=access_point_status,
        access_evidence_ids=(observation.access_evidence_ids if observation else ()),
        support_evidence_ids=(observation.support_evidence_ids if observation else ()),
        connector_distance=(
            observation.connector_distance if observation else DistanceNotObserved()
        ),
        connector_continuity=(
            observation.connector_continuity if observation else ConnectorContinuity.UNKNOWN
        ),
        destination_distance=(
            observation.destination_distance if observation else DistanceNotObserved()
        ),
        route_quality_evidence=(observation.route_quality_evidence if observation else ()),
        unknowns=_with_generated_unknowns(external_unknowns, *generated_unknowns),
        source_binding=source_binding,
    )


def _status(
    school_phase: EducationPhase | None,
    observation: _OptionAccessEvidence | None,
) -> AccessServiceStatus:
    if observation is None:
        return AccessServiceStatus.NETWORK_GAP
    return _derived_access_status(
        school_phase=school_phase,
        access_point_status=observation.access_point_status,
        connector_continuity=observation.connector_continuity,
        connector_distance=observation.connector_distance,
        access_evidence_ids=observation.access_evidence_ids,
        support_evidence_ids=observation.support_evidence_ids,
    )


def _derived_access_status(
    *,
    school_phase: EducationPhase | None,
    access_point_status: AccessPointStatus,
    connector_continuity: ConnectorContinuity,
    connector_distance: DistanceEvidence,
    access_evidence_ids: tuple[str, ...],
    support_evidence_ids: tuple[str, ...],
) -> AccessServiceStatus:
    if (
        connector_continuity is not ConnectorContinuity.CONTINUOUS
        or access_point_status is AccessPointStatus.UNRESOLVED
    ):
        return AccessServiceStatus.NETWORK_GAP
    if not isinstance(connector_distance, MeasuredDistance):
        return AccessServiceStatus.NETWORK_GAP
    if (
        school_phase is EducationPhase.UNRESOLVED
        or access_point_status is AccessPointStatus.INFERRED
        or not access_evidence_ids
        or not support_evidence_ids
    ):
        return AccessServiceStatus.SERVED_PROVISIONAL
    return AccessServiceStatus.SERVED


def _validate_access_output_evidence(
    access_point_status: AccessPointStatus,
    connector_continuity: ConnectorContinuity,
    connector_distance: DistanceEvidence,
    access_evidence_ids: tuple[str, ...],
) -> None:
    if access_point_status in {AccessPointStatus.MAPPED, AccessPointStatus.INFERRED} and (
        not access_evidence_ids
    ):
        raise ValueError("resolved access points require access evidence IDs")
    if access_point_status is AccessPointStatus.UNRESOLVED and access_evidence_ids:
        raise ValueError("unresolved access points must not claim access evidence IDs")
    if connector_continuity is ConnectorContinuity.CONTINUOUS and not isinstance(
        connector_distance,
        MeasuredDistance,
    ):
        raise ValueError("continuous connector outputs require a measured connector distance")


def _school_access_label(status: AccessServiceStatus) -> SchoolAccessLabel:
    return {
        AccessServiceStatus.SERVED: SchoolAccessLabel.EVIDENCED,
        AccessServiceStatus.SERVED_PROVISIONAL: SchoolAccessLabel.PROVISIONAL,
        AccessServiceStatus.NETWORK_GAP: SchoolAccessLabel.GAP,
    }[status]


def _strategic_destination_access_label(
    status: AccessServiceStatus,
) -> StrategicDestinationAccessLabel:
    return {
        AccessServiceStatus.SERVED: StrategicDestinationAccessLabel.EVIDENCED,
        AccessServiceStatus.SERVED_PROVISIONAL: (StrategicDestinationAccessLabel.PROVISIONAL),
        AccessServiceStatus.NETWORK_GAP: StrategicDestinationAccessLabel.GAP,
    }[status]


def _unknown_independent_evidence() -> IndependentTravelEvidence:
    unknown = EvidenceFactor(availability=EvidenceAvailability.UNKNOWN)
    return IndependentTravelEvidence(
        gradient=unknown,
        road_class=unknown,
        speed=unknown,
        crossing=unknown,
        separation=unknown,
        lighting=unknown,
        severance=unknown,
        audit=unknown,
    )


def _independent_travel(
    option_id: str,
    school: School,
    observation: SchoolAccessEvidence | None,
    source_binding: EducationAccessSourceBinding,
) -> IndependentTravelOpportunity:
    if school.phase not in {
        EducationPhase.SECONDARY,
        EducationPhase.ALL_THROUGH,
    }:
        raise ValueError("independent-travel opportunities require a secondary education phase")
    phase = (
        IndependentTravelPhase.SECONDARY
        if school.phase is EducationPhase.SECONDARY
        else IndependentTravelPhase.ALL_THROUGH_SECONDARY
    )
    evidence = observation.independent_travel_evidence if observation else None
    if evidence is None:
        evidence = _unknown_independent_evidence()
    status = (
        IndependentTravelStatus.EVIDENCE_AVAILABLE
        if evidence.is_complete()
        else IndependentTravelStatus.EVIDENCE_REQUIRED
    )
    external_unknowns = observation.unknowns if observation else ()
    generated_unknowns: list[CompilerDerivedUnknown] = []
    if observation is None or observation.independent_travel_evidence is None:
        generated_unknowns.append(CompilerDerivedUnknown.NO_TYPED_INDEPENDENT_TRAVEL_EVIDENCE)
    return IndependentTravelOpportunity(
        opportunity_id=_independent_travel_opportunity_id(
            option_id,
            school.school_id,
        ),
        option_id=option_id,
        school_id=school.school_id,
        phase=phase,
        status=status,
        public_label=(
            IndependentTravelLabel.FACTORS_AVAILABLE
            if status is IndependentTravelStatus.EVIDENCE_AVAILABLE
            else IndependentTravelLabel.FACTORS_REQUIRED
        ),
        evidence=evidence,
        unknowns=_with_generated_unknowns(external_unknowns, *generated_unknowns),
        source_binding=source_binding,
    )


def _special_school_view(
    option_id: str,
    school: School,
    observation: SchoolAccessEvidence | None,
    source_binding: EducationAccessSourceBinding,
) -> SpecialSchoolAccessibilityView:
    evidence = observation.special_school_evidence if observation else None
    if evidence is None:
        unknown = EvidenceFactor(availability=EvidenceAvailability.UNKNOWN)
        evidence = SpecialSchoolEvidence(
            accessibility=unknown, support=unknown, independent_travel=unknown
        )
    external_unknowns = observation.unknowns if observation else ()
    generated_unknowns: list[CompilerDerivedUnknown] = []
    if observation is None or observation.special_school_evidence is None:
        generated_unknowns.append(CompilerDerivedUnknown.NO_TYPED_SPECIAL_SCHOOL_EVIDENCE)
    return SpecialSchoolAccessibilityView(
        view_id=_special_school_view_id(option_id, school.school_id),
        option_id=option_id,
        school_id=school.school_id,
        accessibility=evidence.accessibility,
        support=evidence.support,
        independent_travel=evidence.independent_travel,
        unknowns=_with_generated_unknowns(external_unknowns, *generated_unknowns),
        source_binding=source_binding,
    )


class GovernedInputRecordKind(StrEnum):
    SCHOOL_REGISTER = "school-register"
    SCHOOL = "school"
    STRATEGIC_EDUCATION_DESTINATION = "strategic-education-destination"
    OPTION_EDUCATION_EVIDENCE = "option-education-evidence"
    SUPPLEMENTARY_PCT_EVIDENCE = "supplementary-pct-evidence"
    SPECIAL_SCHOOL_EVIDENCE = "special-school-evidence"


class GovernedInputRecordFingerprint(_SelfRevalidatingModel):
    record_kind: GovernedInputRecordKind
    record_id: str
    content_fingerprint: str = Field(pattern=_SHA256_PATTERN)

    _identifier = field_validator("record_id")(_strict_identifier)


def _source_payload(
    register: SchoolRegisterEvidence,
    schools: tuple[School, ...],
    destinations: tuple[StrategicEducationDestination, ...],
    option_ids: tuple[str, ...],
    option_evidence: tuple[OptionEducationEvidence, ...],
    pct_evidence: tuple[SupplementaryPCTEvidence, ...],
) -> dict[str, object]:
    return {
        "method_version": "satn-education-access-assessment/v2",
        "register_evidence": register.model_dump(mode="json"),
        "schools": [item.model_dump(mode="json") for item in schools],
        "strategic_education_destinations": [item.model_dump(mode="json") for item in destinations],
        "option_ids": list(option_ids),
        "option_evidence": [item.model_dump(mode="json") for item in option_evidence],
        "supplementary_pct_evidence": [item.model_dump(mode="json") for item in pct_evidence],
    }


def _source_record_fingerprints(
    register: SchoolRegisterEvidence,
    schools: tuple[School, ...],
    destinations: tuple[StrategicEducationDestination, ...],
    option_evidence: tuple[OptionEducationEvidence, ...],
    pct_evidence: tuple[SupplementaryPCTEvidence, ...],
) -> tuple[GovernedInputRecordFingerprint, ...]:
    rows: list[GovernedInputRecordFingerprint] = [
        GovernedInputRecordFingerprint(
            record_kind=GovernedInputRecordKind.SCHOOL_REGISTER,
            record_id=register.evidence_id,
            content_fingerprint=_model_fingerprint(register),
        )
    ]
    rows.extend(
        GovernedInputRecordFingerprint(
            record_kind=GovernedInputRecordKind.SCHOOL,
            record_id=item.school_id,
            content_fingerprint=_model_fingerprint(item),
        )
        for item in schools
    )
    rows.extend(
        GovernedInputRecordFingerprint(
            record_kind=GovernedInputRecordKind.STRATEGIC_EDUCATION_DESTINATION,
            record_id=item.record_id,
            content_fingerprint=_model_fingerprint(item),
        )
        for item in destinations
    )
    for item in option_evidence:
        target_id = (
            item.school_id
            if isinstance(item, SchoolAccessEvidence)
            else item.strategic_destination_id
        )
        rows.append(
            GovernedInputRecordFingerprint(
                record_kind=GovernedInputRecordKind.OPTION_EDUCATION_EVIDENCE,
                record_id=stable_id(
                    "education-option-evidence",
                    item.evidence_kind,
                    item.option_id,
                    target_id,
                ),
                content_fingerprint=_model_fingerprint(item),
            )
        )
        if isinstance(item, SchoolAccessEvidence) and item.special_school_evidence is not None:
            rows.append(
                GovernedInputRecordFingerprint(
                    record_kind=GovernedInputRecordKind.SPECIAL_SCHOOL_EVIDENCE,
                    record_id=stable_id(
                        "special-school-evidence-input",
                        item.option_id,
                        item.school_id,
                    ),
                    content_fingerprint=_model_fingerprint(item.special_school_evidence),
                )
            )
    rows.extend(
        GovernedInputRecordFingerprint(
            record_kind=GovernedInputRecordKind.SUPPLEMENTARY_PCT_EVIDENCE,
            record_id=item.evidence_id,
            content_fingerprint=_model_fingerprint(item),
        )
        for item in pct_evidence
    )
    return tuple(sorted(rows, key=lambda item: (item.record_kind.value, item.record_id)))


def _validate_source_inputs(
    register: SchoolRegisterEvidence,
    schools: tuple[School, ...],
    destinations: tuple[StrategicEducationDestination, ...],
    option_ids: tuple[str, ...],
    option_evidence: tuple[OptionEducationEvidence, ...],
    pct_evidence: tuple[SupplementaryPCTEvidence, ...],
) -> None:
    _validate_unique(schools, "school_id", "school")
    _validate_unique_keys(
        tuple(_option_evidence_key(item) for item in option_evidence),
        "option evidence",
    )
    _validate_external_unknowns(option_evidence)
    _validate_unique(destinations, "record_id", "strategic destination record")
    _validate_unique(
        destinations,
        "strategic_destination_id",
        "strategic destination admission",
    )
    _validate_unique(pct_evidence, "evidence_id", "supplementary PCT evidence")
    _validate_school_evidence(register, schools)
    _validate_option_evidence_places(
        option_evidence,
        {item.school_id: item for item in schools},
        {item.strategic_destination_id: item for item in destinations},
    )
    _resolve_option_ids(option_ids, option_evidence)


class EducationAccessSourceSnapshot(_SelfRevalidatingModel):
    method_version: Literal["satn-education-access-assessment/v2"] = (
        "satn-education-access-assessment/v2"
    )
    register_evidence: SchoolRegisterEvidence
    schools: tuple[School, ...]
    strategic_education_destinations: tuple[StrategicEducationDestination, ...]
    option_ids: tuple[str, ...]
    option_evidence: tuple[OptionEducationEvidence, ...]
    supplementary_pct_evidence: tuple[SupplementaryPCTEvidence, ...]
    record_fingerprints: tuple[GovernedInputRecordFingerprint, ...]
    source_content_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    source_snapshot_fingerprint: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("register_evidence", mode="before")
    @classmethod
    def revalidate_register(cls, value: object) -> SchoolRegisterEvidence:
        return _revalidate_raw_model(
            value,
            SchoolRegisterEvidence,
            "embedded school-register evidence",
        )

    @field_validator("schools", mode="before")
    @classmethod
    def revalidate_schools(cls, value: object) -> tuple[School, ...]:
        schools = _revalidate_raw_model_sequence(value, School, "embedded School")
        return tuple(sorted(schools, key=lambda item: item.school_id))

    @field_validator("strategic_education_destinations", mode="before")
    @classmethod
    def revalidate_destinations(
        cls,
        value: object,
    ) -> tuple[StrategicEducationDestination, ...]:
        destinations = _revalidate_raw_model_sequence(
            value,
            StrategicEducationDestination,
            "embedded Strategic Education Destination",
        )
        return tuple(
            sorted(
                destinations,
                key=lambda item: item.strategic_destination_id,
            )
        )

    _options = field_validator("option_ids")(_strict_identifier_values)

    @field_validator("option_evidence", mode="before")
    @classmethod
    def revalidate_option_evidence(
        cls,
        value: object,
    ) -> tuple[SchoolAccessEvidence | StrategicEducationDestinationEvidence, ...]:
        evidence = _revalidate_raw_option_evidence_sequence(value)
        return tuple(sorted(evidence, key=_option_evidence_key))

    @field_validator("supplementary_pct_evidence", mode="before")
    @classmethod
    def revalidate_pct(
        cls,
        value: object,
    ) -> tuple[SupplementaryPCTEvidence, ...]:
        evidence = _revalidate_raw_model_sequence(
            value,
            SupplementaryPCTEvidence,
            "embedded supplementary PCT evidence",
        )
        return tuple(sorted(evidence, key=lambda item: item.evidence_id))

    @field_validator("record_fingerprints", mode="before")
    @classmethod
    def revalidate_record_fingerprints(
        cls,
        value: object,
    ) -> tuple[GovernedInputRecordFingerprint, ...]:
        records = _revalidate_raw_model_sequence(
            value,
            GovernedInputRecordFingerprint,
            "governed input record fingerprint",
        )
        return tuple(
            sorted(
                records,
                key=lambda item: (item.record_kind.value, item.record_id),
            )
        )

    @model_validator(mode="after")
    def verify_snapshot(self) -> Self:
        register = _revalidate_exact_model(
            self.register_evidence,
            SchoolRegisterEvidence,
            "embedded school-register evidence",
        )
        schools = tuple(
            _revalidate_exact_model(item, School, "embedded School") for item in self.schools
        )
        destinations = tuple(
            _revalidate_exact_model(
                item,
                StrategicEducationDestination,
                "embedded Strategic Education Destination",
            )
            for item in self.strategic_education_destinations
        )
        evidence = tuple(_revalidate_option_evidence(item) for item in self.option_evidence)
        pct = tuple(
            _revalidate_exact_model(
                item,
                SupplementaryPCTEvidence,
                "embedded supplementary PCT evidence",
            )
            for item in self.supplementary_pct_evidence
        )
        _validate_source_inputs(
            register,
            schools,
            destinations,
            self.option_ids,
            evidence,
            pct,
        )
        expected_records = _source_record_fingerprints(
            register, schools, destinations, evidence, pct
        )
        if self.record_fingerprints != expected_records:
            raise ValueError("source record fingerprints must match embedded governed inputs")
        payload = _source_payload(
            register,
            schools,
            destinations,
            self.option_ids,
            evidence,
            pct,
        )
        content_fingerprint = canonical_sha256(payload)
        if self.source_content_fingerprint != content_fingerprint:
            raise ValueError("source content fingerprint mismatch")
        snapshot_fingerprint = canonical_sha256(
            {
                "source_content": payload,
                "record_fingerprints": [item.model_dump(mode="json") for item in expected_records],
                "source_content_fingerprint": content_fingerprint,
            }
        )
        if self.source_snapshot_fingerprint != snapshot_fingerprint:
            raise ValueError("source snapshot fingerprint mismatch")
        return self


def _build_source_snapshot(
    register: SchoolRegisterEvidence,
    schools: tuple[School, ...],
    destinations: tuple[StrategicEducationDestination, ...],
    option_ids: tuple[str, ...],
    option_evidence: tuple[OptionEducationEvidence, ...],
    pct: tuple[SupplementaryPCTEvidence, ...],
) -> EducationAccessSourceSnapshot:
    payload = _source_payload(register, schools, destinations, option_ids, option_evidence, pct)
    records = _source_record_fingerprints(register, schools, destinations, option_evidence, pct)
    content_fingerprint = canonical_sha256(payload)
    return EducationAccessSourceSnapshot(
        register_evidence=register,
        schools=schools,
        strategic_education_destinations=destinations,
        option_ids=option_ids,
        option_evidence=option_evidence,
        supplementary_pct_evidence=pct,
        record_fingerprints=records,
        source_content_fingerprint=content_fingerprint,
        source_snapshot_fingerprint=canonical_sha256(
            {
                "source_content": payload,
                "record_fingerprints": [item.model_dump(mode="json") for item in records],
                "source_content_fingerprint": content_fingerprint,
            }
        ),
    )


def _source_binding(
    record: School | StrategicEducationDestination,
    observation: OptionEducationEvidence | None,
    option_id: str | None,
    target_kind: str,
    target_id: str,
    all_evidence: tuple[OptionEducationEvidence, ...],
) -> EducationAccessSourceBinding:
    if observation is not None:
        evidence_fingerprint = _model_fingerprint(observation)
    elif option_id is None:
        evidence_fingerprint = canonical_sha256(
            [item.model_dump(mode="json") for item in all_evidence]
        )
    else:
        evidence_fingerprint = canonical_sha256(
            {
                "option_id": option_id,
                "target_kind": target_kind,
                "target_id": target_id,
                "option_evidence": None,
            }
        )
    return EducationAccessSourceBinding(
        source_record_fingerprint=_model_fingerprint(record),
        option_evidence_fingerprint=evidence_fingerprint,
    )


@dataclass(frozen=True)
class _EducationAccessDerivation:
    school_access_obligations: tuple[SchoolAccessObligation, ...]
    strategic_education_destination_access: tuple[StrategicEducationDestinationAccess, ...]
    school_evidence_requests: tuple[SchoolEvidenceRequest, ...]
    independent_travel_opportunities: tuple[IndependentTravelOpportunity, ...]
    special_school_accessibility_views: tuple[SpecialSchoolAccessibilityView, ...]
    network_gaps: tuple[NetworkGapEvidence, ...]


def _derive_education_access(
    snapshot: EducationAccessSourceSnapshot,
) -> _EducationAccessDerivation:
    school_evidence = {
        (item.option_id, item.school_id): item
        for item in snapshot.option_evidence
        if isinstance(item, SchoolAccessEvidence)
    }
    destination_evidence = {
        (item.option_id, item.strategic_destination_id): item
        for item in snapshot.option_evidence
        if isinstance(item, StrategicEducationDestinationEvidence)
    }
    obligations: list[SchoolAccessObligation] = []
    accesses: list[StrategicEducationDestinationAccess] = []
    opportunities: list[IndependentTravelOpportunity] = []
    special_views: list[SpecialSchoolAccessibilityView] = []
    gaps = list(
        _no_candidate_gaps(
            snapshot.schools,
            snapshot.strategic_education_destinations,
            snapshot.option_evidence,
        )
        if not snapshot.option_ids
        else ()
    )
    for option_id in snapshot.option_ids:
        for school in snapshot.schools:
            observation = school_evidence.get((option_id, school.school_id))
            binding = _source_binding(
                school,
                observation,
                option_id,
                "school-access-obligation",
                school.school_id,
                snapshot.option_evidence,
            )
            obligation = _school_access_obligation(option_id, school, observation, binding)
            obligations.append(obligation)
            if obligation.status is AccessServiceStatus.NETWORK_GAP:
                gaps.append(_candidate_school_gap(obligation))
            if school.phase in {
                EducationPhase.SECONDARY,
                EducationPhase.ALL_THROUGH,
            }:
                opportunities.append(_independent_travel(option_id, school, observation, binding))
            if school.phase is EducationPhase.SPECIAL:
                special_views.append(_special_school_view(option_id, school, observation, binding))
        for destination in snapshot.strategic_education_destinations:
            observation = destination_evidence.get(
                (option_id, destination.strategic_destination_id)
            )
            binding = _source_binding(
                destination,
                observation,
                option_id,
                "strategic-education-destination",
                destination.strategic_destination_id,
                snapshot.option_evidence,
            )
            access = _strategic_destination_access(option_id, destination, observation, binding)
            accesses.append(access)
            if access.status is AccessServiceStatus.NETWORK_GAP:
                gaps.append(_candidate_destination_gap(access))
    return _EducationAccessDerivation(
        school_access_obligations=tuple(
            sorted(obligations, key=lambda item: (item.option_id, item.school_id))
        ),
        strategic_education_destination_access=tuple(
            sorted(
                accesses,
                key=lambda item: (
                    item.option_id,
                    item.strategic_destination_id,
                ),
            )
        ),
        school_evidence_requests=tuple(
            sorted(
                _phase_requests(snapshot.schools, snapshot.option_evidence),
                key=lambda item: item.request_id,
            )
        ),
        independent_travel_opportunities=tuple(
            sorted(
                opportunities,
                key=lambda item: (item.option_id, item.school_id),
            )
        ),
        special_school_accessibility_views=tuple(
            sorted(
                special_views,
                key=lambda item: (item.option_id, item.school_id),
            )
        ),
        network_gaps=tuple(sorted(gaps, key=_network_gap_sort_key)),
    )


def _derivation_payload(
    snapshot: EducationAccessSourceSnapshot,
    derivation: _EducationAccessDerivation,
) -> dict[str, object]:
    return {
        "source_snapshot": snapshot.model_dump(mode="json"),
        **{
            name: [item.model_dump(mode="json") for item in getattr(derivation, name)]
            for name in (
                "school_access_obligations",
                "strategic_education_destination_access",
                "school_evidence_requests",
                "independent_travel_opportunities",
                "special_school_accessibility_views",
                "network_gaps",
            )
        },
    }


class EducationAccessAssessment(_SelfRevalidatingModel):
    assessment_id: str = Field(pattern=_SHA256_PATTERN)
    source_snapshot: EducationAccessSourceSnapshot
    school_access_obligations: tuple[SchoolAccessObligation, ...]
    strategic_education_destination_access: tuple[StrategicEducationDestinationAccess, ...]
    school_evidence_requests: tuple[SchoolEvidenceRequest, ...]
    independent_travel_opportunities: tuple[IndependentTravelOpportunity, ...]
    special_school_accessibility_views: tuple[SpecialSchoolAccessibilityView, ...]
    network_gaps: tuple[NetworkGapEvidence, ...]

    _identifier = field_validator("assessment_id")(_strict_identifier)

    @field_validator("source_snapshot", mode="before")
    @classmethod
    def revalidate_source_snapshot(
        cls,
        value: object,
    ) -> EducationAccessSourceSnapshot:
        return _revalidate_raw_model(
            value,
            EducationAccessSourceSnapshot,
            "education-access source snapshot",
        )

    @field_validator("school_access_obligations", mode="before")
    @classmethod
    def revalidate_obligations(
        cls,
        value: object,
    ) -> tuple[SchoolAccessObligation, ...]:
        obligations = _revalidate_raw_model_sequence(
            value,
            SchoolAccessObligation,
            "School Access Obligation output",
        )
        return tuple(
            sorted(
                obligations,
                key=lambda item: (item.option_id, item.school_id),
            )
        )

    @field_validator("strategic_education_destination_access", mode="before")
    @classmethod
    def revalidate_destination_access(
        cls,
        value: object,
    ) -> tuple[StrategicEducationDestinationAccess, ...]:
        access = _revalidate_raw_model_sequence(
            value,
            StrategicEducationDestinationAccess,
            "Strategic Education Destination Access output",
        )
        return tuple(
            sorted(
                access,
                key=lambda item: (
                    item.option_id,
                    item.strategic_destination_id,
                ),
            )
        )

    @field_validator("school_evidence_requests", mode="before")
    @classmethod
    def revalidate_requests(
        cls,
        value: object,
    ) -> tuple[SchoolEvidenceRequest, ...]:
        requests = _revalidate_raw_model_sequence(
            value,
            SchoolEvidenceRequest,
            "School Evidence Request output",
        )
        return tuple(sorted(requests, key=lambda item: item.request_id))

    @field_validator("independent_travel_opportunities", mode="before")
    @classmethod
    def revalidate_opportunities(
        cls,
        value: object,
    ) -> tuple[IndependentTravelOpportunity, ...]:
        opportunities = _revalidate_raw_model_sequence(
            value,
            IndependentTravelOpportunity,
            "Independent-Travel Opportunity output",
        )
        return tuple(
            sorted(
                opportunities,
                key=lambda item: (item.option_id, item.school_id),
            )
        )

    @field_validator("special_school_accessibility_views", mode="before")
    @classmethod
    def revalidate_special_views(
        cls,
        value: object,
    ) -> tuple[SpecialSchoolAccessibilityView, ...]:
        views = _revalidate_raw_model_sequence(
            value,
            SpecialSchoolAccessibilityView,
            "Special School Accessibility View output",
        )
        return tuple(sorted(views, key=lambda item: (item.option_id, item.school_id)))

    @field_validator("network_gaps", mode="before")
    @classmethod
    def canonicalise_network_gaps(
        cls,
        value: object,
    ) -> tuple[NetworkGapEvidence, ...]:
        gaps = tuple(
            _revalidate_raw_network_gap(item) for item in _raw_sequence(value, "Network Gap output")
        )
        gap_ids = tuple(item.gap_id for item in gaps)
        if len(gap_ids) != len(set(gap_ids)):
            raise ValueError("Network Gap IDs must be unique")
        return tuple(sorted(gaps, key=_network_gap_sort_key))

    @model_validator(mode="after")
    def verify_derivation(self) -> Self:
        snapshot = _revalidate_exact_model(
            self.source_snapshot,
            EducationAccessSourceSnapshot,
            "education-access source snapshot",
        )
        typed_outputs: tuple[tuple[str, tuple[BaseModel, ...], type[BaseModel]], ...] = (
            (
                "School Access Obligation",
                self.school_access_obligations,
                SchoolAccessObligation,
            ),
            (
                "Strategic Education Destination Access",
                self.strategic_education_destination_access,
                StrategicEducationDestinationAccess,
            ),
            (
                "School Evidence Request",
                self.school_evidence_requests,
                SchoolEvidenceRequest,
            ),
            (
                "Independent-Travel Opportunity",
                self.independent_travel_opportunities,
                IndependentTravelOpportunity,
            ),
            (
                "Special School Accessibility View",
                self.special_school_accessibility_views,
                SpecialSchoolAccessibilityView,
            ),
        )
        for label, values, expected_type in typed_outputs:
            for value in values:
                _revalidate_exact_model(value, expected_type, f"{label} output")
        for gap in self.network_gaps:
            gap_type = (
                SchoolAccessNetworkGap
                if type(gap) is SchoolAccessNetworkGap
                else StrategicEducationDestinationNetworkGap
            )
            _revalidate_exact_model(gap, gap_type, "Network Gap output")
        expected = _derive_education_access(snapshot)
        for name in (
            "school_access_obligations",
            "strategic_education_destination_access",
            "school_evidence_requests",
            "independent_travel_opportunities",
            "special_school_accessibility_views",
            "network_gaps",
        ):
            if name == "independent_travel_opportunities" and any(
                actual.phase is not derived.phase
                for actual, derived in zip(
                    self.independent_travel_opportunities,
                    expected.independent_travel_opportunities,
                    strict=False,
                )
            ):
                raise ValueError(
                    "Independent-Travel Opportunity phase must match the embedded School record"
                )
            actual = [item.model_dump(mode="json") for item in getattr(self, name)]
            derived = [item.model_dump(mode="json") for item in getattr(expected, name)]
            if actual != derived:
                raise ValueError(
                    "published derived outputs do not exactly match "
                    f"deterministic derivation: {name}"
                )
        expected_id = canonical_sha256(_derivation_payload(snapshot, expected))
        if self.assessment_id != expected_id:
            raise ValueError("assessment_id does not match the full canonical derivation")
        return self

    @property
    def register_evidence(self) -> SchoolRegisterEvidence:
        return self.source_snapshot.register_evidence

    @property
    def strategic_education_destinations(
        self,
    ) -> tuple[StrategicEducationDestination, ...]:
        return self.source_snapshot.strategic_education_destinations

    @property
    def supplementary_pct_evidence(
        self,
    ) -> tuple[SupplementaryPCTEvidence, ...]:
        return self.source_snapshot.supplementary_pct_evidence


def assess_education_access(
    *,
    register_evidence: SchoolRegisterEvidence,
    schools: tuple[School, ...],
    option_evidence: tuple[OptionEducationEvidence, ...],
    option_ids: tuple[str, ...] = (),
    strategic_destinations: tuple[StrategicEducationDestination, ...] = (),
    supplementary_pct_evidence: tuple[SupplementaryPCTEvidence, ...] = (),
) -> EducationAccessAssessment:
    register = _revalidate_exact_model(
        register_evidence,
        SchoolRegisterEvidence,
        "school-register evidence",
    )
    canonical_schools = tuple(
        sorted(
            (_revalidate_exact_model(item, School, "School") for item in schools),
            key=lambda item: item.school_id,
        )
    )
    canonical_destinations = tuple(
        sorted(
            (
                _revalidate_exact_model(
                    item,
                    StrategicEducationDestination,
                    "Strategic Education Destination",
                )
                for item in strategic_destinations
            ),
            key=lambda item: item.strategic_destination_id,
        )
    )
    canonical_evidence = tuple(
        sorted(
            (_revalidate_option_evidence(item) for item in option_evidence),
            key=_option_evidence_key,
        )
    )
    canonical_pct = tuple(
        sorted(
            (
                _revalidate_exact_model(
                    item,
                    SupplementaryPCTEvidence,
                    "supplementary PCT evidence",
                )
                for item in supplementary_pct_evidence
            ),
            key=lambda item: item.evidence_id,
        )
    )
    canonical_options = _strict_identifier_values(option_ids)
    _validate_source_inputs(
        register,
        canonical_schools,
        canonical_destinations,
        canonical_options,
        canonical_evidence,
        canonical_pct,
    )
    snapshot = _build_source_snapshot(
        register,
        canonical_schools,
        canonical_destinations,
        canonical_options,
        canonical_evidence,
        canonical_pct,
    )
    derivation = _derive_education_access(snapshot)
    return EducationAccessAssessment(
        assessment_id=canonical_sha256(_derivation_payload(snapshot, derivation)),
        source_snapshot=snapshot,
        school_access_obligations=derivation.school_access_obligations,
        strategic_education_destination_access=(derivation.strategic_education_destination_access),
        school_evidence_requests=derivation.school_evidence_requests,
        independent_travel_opportunities=(derivation.independent_travel_opportunities),
        special_school_accessibility_views=(derivation.special_school_accessibility_views),
        network_gaps=derivation.network_gaps,
    )


def governed_education_assessment_fingerprint(
    *,
    governed_source_fingerprint: str,
    school_ids: tuple[str, ...],
    strategic_destination_ids: tuple[str, ...],
    assessment_content_sha256: str,
) -> str:
    """Bind one exact education scope and assessment to its full source."""

    if (
        re.fullmatch(_SHA256_PATTERN, governed_source_fingerprint) is None
        or re.fullmatch(_SHA256_PATTERN, assessment_content_sha256) is None
    ):
        raise ValueError(
            "governed education binding requires lowercase SHA-256 identities"
        )
    for label, values in (
        ("school_ids", school_ids),
        ("strategic_destination_ids", strategic_destination_ids),
    ):
        if (
            values != tuple(sorted(values))
            or len(set(values)) != len(values)
            or any(_ID_PATTERN.fullmatch(item) is None for item in values)
        ):
            raise ValueError(
                f"governed education binding {label} must be canonical"
            )
    return canonical_sha256(
        {
            "schema": "satn-governed-education-assessment-binding/v3",
            "governed_source_fingerprint": governed_source_fingerprint,
            "scope": {
                "school_ids": list(school_ids),
                "strategic_destination_ids": list(
                    strategic_destination_ids
                ),
            },
            "assessment_content_sha256": assessment_content_sha256,
        }
    )
