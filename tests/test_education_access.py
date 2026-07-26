from __future__ import annotations

import inspect
import json
from datetime import date
from enum import StrEnum

import pytest
from pydantic import ValidationError

import satn.education_access as education_access
from satn.education_access import (
    AccessPointStatus,
    CompilerDerivedUnknown,
    ConnectorContinuity,
    DistanceNotObserved,
    EducationPhase,
    EvidenceAvailability,
    EvidenceFactor,
    ExternalEvidenceUnknown,
    IndependentTravelEvidence,
    IndependentTravelStatus,
    MeasuredDistance,
    PCTCoverage,
    PCTExcludedPopulation,
    PCTIncludedPopulation,
    PCTLimitation,
    RouteObservationKind,
    RouteQualityEvidence,
    School,
    SchoolAccessEvidence,
    SchoolAccessLabel,
    SchoolRegisterEvidence,
    SpecialSchoolEvidence,
    StrategicAdmissionRationale,
    StrategicAdmissionReviewTrigger,
    StrategicEducationDestination,
    StrategicEducationDestinationEvidence,
    SupplementaryPCTEvidence,
    assess_education_access,
)
from satn.models import (
    AccessPointStatus as CanonicalAccessPointStatus,
)
from satn.models import (
    AccessServiceStatus,
)
from satn.network_selection import IndependentTravelPhase

REGISTER = SchoolRegisterEvidence(
    evidence_id="banes-school-register-2026-07",
    source_name="B&NES governed school register",
    as_of=date(2026, 7, 1),
)


def school(school_id: str, phase: EducationPhase, *, name: str | None = None) -> School:
    return School(
        school_id=school_id,
        name=name or school_id.replace("-", " ").title(),
        phase=phase,
        source_evidence_id=REGISTER.evidence_id,
    )


def available_factor(identifier: str) -> EvidenceFactor:
    return EvidenceFactor(availability=EvidenceAvailability.AVAILABLE, evidence_ids=(identifier,))


def independent_evidence() -> IndependentTravelEvidence:
    return IndependentTravelEvidence(
        gradient=available_factor("gradient-audit"),
        road_class=available_factor("road-class-audit"),
        speed=available_factor("speed-audit"),
        crossing=available_factor("crossing-audit"),
        separation=available_factor("separation-audit"),
        lighting=available_factor("lighting-audit"),
        severance=available_factor("severance-audit"),
        audit=available_factor("independent-travel-audit"),
    )


def special_evidence() -> SpecialSchoolEvidence:
    return SpecialSchoolEvidence(
        accessibility=available_factor("special-access-evidence"),
        support=available_factor("special-support-evidence"),
        independent_travel=available_factor("special-independent-evidence"),
    )


def option(
    option_id: str,
    school_id: str,
    *,
    continuity: ConnectorContinuity = ConnectorContinuity.CONTINUOUS,
    access_point_status: AccessPointStatus = AccessPointStatus.MAPPED,
    access: tuple[str, ...] = ("school-entrance-survey",),
    support: tuple[str, ...] = ("connector-continuity-evidence",),
    independent: IndependentTravelEvidence | None = None,
    special: SpecialSchoolEvidence | None = None,
    unknowns: tuple[ExternalEvidenceUnknown, ...] = (
        ExternalEvidenceUnknown.JUNCTION_DESIGN_OUTSIDE_SELECTION_PASS,
    ),
) -> SchoolAccessEvidence:
    return SchoolAccessEvidence(
        option_id=option_id,
        school_id=school_id,
        connector_distance=MeasuredDistance(distance_m=120),
        connector_continuity=continuity,
        access_point_status=access_point_status,
        destination_distance=MeasuredDistance(distance_m=900),
        access_evidence_ids=access,
        support_evidence_ids=support,
        route_quality_evidence=(
            RouteQualityEvidence(
                evidence_id="route-observation",
                observation=RouteObservationKind.CROSSING_RECORDED,
            ),
        ),
        independent_travel_evidence=independent,
        special_school_evidence=special,
        unknowns=unknowns,
    )


def strategic_option(
    option_id: str,
    strategic_destination_id: str,
    *,
    support: tuple[str, ...] = ("connector-continuity-evidence",),
) -> StrategicEducationDestinationEvidence:
    return StrategicEducationDestinationEvidence(
        option_id=option_id,
        strategic_destination_id=strategic_destination_id,
        connector_distance=MeasuredDistance(distance_m=120),
        connector_continuity=ConnectorContinuity.CONTINUOUS,
        access_point_status=AccessPointStatus.MAPPED,
        destination_distance=MeasuredDistance(distance_m=900),
        access_evidence_ids=("destination-entrance-survey",),
        support_evidence_ids=support,
    )


def admission(
    strategic_destination_id: str = "bath-spa-university",
    record_id: str = "bath-spa-admission",
    admission_evidence_ids: tuple[str, ...] = ("strategic-admission-record",),
    access_evidence_ids: tuple[str, ...] = ("university-entrance-record",),
) -> StrategicEducationDestination:
    return StrategicEducationDestination(
        record_id=record_id,
        record_version="1.0",
        strategic_destination_id=strategic_destination_id,
        name="Bath Spa University",
        source_evidence_id="higher-education-register-2026",
        admitted_on=date(2026, 7, 2),
        rationale=StrategicAdmissionRationale.CONFIGURED_DESTINATION,
        admission_evidence_ids=admission_evidence_ids,
        review_trigger=StrategicAdmissionReviewTrigger.GOVERNED_RECORD_CHANGES,
        access_evidence_ids=access_evidence_ids,
    )


def pct() -> SupplementaryPCTEvidence:
    return SupplementaryPCTEvidence(
        evidence_id="pct-school-flow",
        phase=EducationPhase.SECONDARY,
        scenario_id="school-access-scenario",
        method_version="pct-method-2011",
        routing_version="routing-2011",
        included_population=PCTIncludedPopulation.HISTORICAL_SECONDARY_SCHOOL_TRAVEL_RECORDS,
        excluded_population=PCTExcludedPopulation.OUTSIDE_SCENARIO_BOUNDARY,
        coverage=PCTCoverage.HISTORICAL_ORIGIN_DESTINATION,
        limitations=(),
    )


def test_mandatory_schools_are_assessed_with_distinct_access_and_independent_evidence() -> None:
    schools = (
        school("primary", EducationPhase.PRIMARY),
        school("secondary", EducationPhase.SECONDARY),
        school("all-through", EducationPhase.ALL_THROUGH),
        school("special", EducationPhase.SPECIAL),
        school("unresolved", EducationPhase.UNRESOLVED),
    )
    observations = (
        option("spine-a", "secondary", independent=independent_evidence()),
        option("spine-a", "primary"),
        option("spine-a", "all-through", independent=independent_evidence()),
        option(
            "spine-a",
            "special",
            support=("arbitrary-id-does-not-decide",),
            special=special_evidence(),
        ),
        option("spine-a", "unresolved"),
    )

    result = assess_education_access(
        register_evidence=REGISTER,
        schools=schools,
        option_evidence=tuple(reversed(observations)),
        option_ids=("spine-a",),
    )

    assert [item.school_id for item in result.school_access_obligations] == [
        "all-through",
        "primary",
        "secondary",
        "special",
        "unresolved",
    ]
    assert {item.school_id: item.status for item in result.school_access_obligations} == {
        "all-through": AccessServiceStatus.SERVED,
        "primary": AccessServiceStatus.SERVED,
        "secondary": AccessServiceStatus.SERVED,
        "special": AccessServiceStatus.SERVED,
        "unresolved": AccessServiceStatus.SERVED_PROVISIONAL,
    }
    assert {item.status for item in result.independent_travel_opportunities} == {
        IndependentTravelStatus.EVIDENCE_AVAILABLE
    }
    assert {item.school_id: item.phase for item in result.independent_travel_opportunities} == {
        "all-through": IndependentTravelPhase.ALL_THROUGH_SECONDARY,
        "secondary": IndependentTravelPhase.SECONDARY,
    }
    assert result.school_evidence_requests[0].school_id == "unresolved"
    assert (
        result.special_school_accessibility_views[0].support.availability
        is EvidenceAvailability.AVAILABLE
    )


def test_access_point_status_is_typed_and_controls_obligation_service_status() -> None:
    assert AccessPointStatus is CanonicalAccessPointStatus
    site = school("secondary", EducationPhase.SECONDARY)
    statuses = {
        state: assess_education_access(
            register_evidence=REGISTER,
            schools=(site,),
            option_evidence=(
                option(
                    f"route-{state}",
                    "secondary",
                    access_point_status=state,
                    access=() if state is AccessPointStatus.UNRESOLVED else ("entrance-record",),
                ),
            ),
            option_ids=(f"route-{state}",),
        )
        .school_access_obligations[0]
        .status
        for state in AccessPointStatus
    }
    assert statuses == {
        AccessPointStatus.MAPPED: AccessServiceStatus.SERVED,
        AccessPointStatus.INFERRED: AccessServiceStatus.SERVED_PROVISIONAL,
        AccessPointStatus.UNRESOLVED: AccessServiceStatus.NETWORK_GAP,
    }


@pytest.mark.parametrize("invalid_authority", [False, 1, 1.0, "true", "1"])
@pytest.mark.parametrize("field_name", ["governed", "current"])
def test_school_register_authority_requires_literal_true(
    field_name: str,
    invalid_authority: object,
) -> None:
    payload = REGISTER.model_dump()
    payload[field_name] = invalid_authority

    with pytest.raises(ValidationError, match="must be literal true"):
        SchoolRegisterEvidence.model_validate(payload)


@pytest.mark.parametrize("invalid_authority", [False, 1, 1.0, "true", "1"])
def test_strategic_admission_authority_requires_literal_true(
    invalid_authority: object,
) -> None:
    payload = admission().model_dump()
    payload["governed"] = invalid_authority

    with pytest.raises(ValidationError, match="must be literal true"):
        StrategicEducationDestination.model_validate(payload)


@pytest.mark.parametrize("field_name", ["governed", "current"])
def test_assessment_revalidates_preconstructed_school_register(
    field_name: str,
) -> None:
    tampered = REGISTER.model_copy(update={field_name: 1})

    with pytest.raises(ValidationError, match="literal true"):
        assess_education_access(
            register_evidence=tampered,
            schools=(),
            option_evidence=(),
        )


def test_assessment_revalidates_school_phase_admission_date_and_pct_contract() -> None:
    invalid_school = school(
        "secondary",
        EducationPhase.SECONDARY,
    ).model_copy(update={"phase": "not-a-school-phase"})
    with pytest.raises(ValidationError):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(invalid_school,),
            option_evidence=(),
        )

    invalid_admission = admission().model_copy(update={"admitted_on": "not-a-date"})
    with pytest.raises(ValidationError):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(),
            option_evidence=(),
            strategic_destinations=(invalid_admission,),
        )

    invalid_pct = pct().model_copy(update={"as_of": 2026, "limitations": ()})
    with pytest.raises(ValidationError):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(),
            option_evidence=(),
            supplementary_pct_evidence=(invalid_pct,),
        )

    bypassed_limitations = pct().model_copy(update={"limitations": ()})
    normalized = assess_education_access(
        register_evidence=REGISTER,
        schools=(),
        option_evidence=(),
        supplementary_pct_evidence=(bypassed_limitations,),
    )
    assert normalized.supplementary_pct_evidence[0].limitations == tuple(PCTLimitation)


def test_assessment_revalidates_nested_distance_models_and_discriminators() -> None:
    invalid_distance = MeasuredDistance.model_construct(distance_m=-1)
    tampered_distance = option(
        "spine-a",
        "secondary",
    ).model_copy(update={"connector_distance": invalid_distance})
    with pytest.raises(ValidationError):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(school("secondary", EducationPhase.SECONDARY),),
            option_evidence=(tampered_distance,),
        )

    spoofed_kind = option(
        "spine-a",
        "secondary",
    ).model_copy(update={"evidence_kind": "strategic-education-destination"})
    with pytest.raises(ValueError, match="evidence_kind"):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(school("secondary", EducationPhase.SECONDARY),),
            option_evidence=(spoofed_kind,),
        )


def test_source_snapshot_rejects_constructed_missing_nested_fields_as_validation_errors() -> None:
    base = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(option("spine-a", "secondary"),),
        option_ids=("spine-a",),
        strategic_destinations=(admission(),),
        supplementary_pct_evidence=(pct(),),
    ).source_snapshot
    invalid_school = School.model_construct(
        name="Missing identifier",
        phase=EducationPhase.SECONDARY,
        source_evidence_id=REGISTER.evidence_id,
    )
    destination_payload = admission().model_dump()
    destination_payload.pop("strategic_destination_id")
    invalid_destination = StrategicEducationDestination.model_construct(**destination_payload)
    option_payload = option("spine-a", "secondary").model_dump()
    option_payload.pop("school_id")
    invalid_option = SchoolAccessEvidence.model_construct(**option_payload)
    pct_payload = pct().model_dump()
    pct_payload.pop("evidence_id")
    invalid_pct = SupplementaryPCTEvidence.model_construct(**pct_payload)
    register_payload = REGISTER.model_dump()
    register_payload.pop("evidence_id")
    invalid_register = SchoolRegisterEvidence.model_construct(**register_payload)

    mutations = (
        {"schools": (invalid_school,)},
        {"strategic_education_destinations": (invalid_destination,)},
        {"option_evidence": (invalid_option,)},
        {"supplementary_pct_evidence": (invalid_pct,)},
        {"register_evidence": invalid_register},
    )
    for update in mutations:
        constructed_snapshot = base.model_copy(update=update)
        with pytest.raises(ValidationError):
            education_access.EducationAccessSourceSnapshot.model_validate(constructed_snapshot)

    snapshot_payload = base.model_dump(mode="python", round_trip=True)
    snapshot_payload.pop("schools")
    missing_snapshot_field = education_access.EducationAccessSourceSnapshot.model_construct(
        **snapshot_payload
    )
    valid_assessment = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(option("spine-a", "secondary"),),
        option_ids=("spine-a",),
        strategic_destinations=(admission(),),
        supplementary_pct_evidence=(pct(),),
    )
    with pytest.raises(ValidationError):
        education_access.EducationAccessAssessment.model_validate(
            valid_assessment.model_copy(update={"source_snapshot": missing_snapshot_field})
        )


def test_source_snapshot_rejects_constructed_special_and_discriminator_tampering() -> None:
    base_option = option(
        "spine-a",
        "special",
        special=special_evidence(),
    )
    base = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("special", EducationPhase.SPECIAL),),
        option_evidence=(base_option,),
        option_ids=("spine-a",),
    ).source_snapshot

    invalid_special = SpecialSchoolEvidence.model_construct(
        support=available_factor("support"),
        independent_travel=available_factor("independent"),
    )
    nested_special = base_option.model_copy(update={"special_school_evidence": invalid_special})
    invalid_kind_payload = base_option.model_dump()
    invalid_kind_payload["evidence_kind"] = "strategic-education-destination"
    invalid_kind = SchoolAccessEvidence.model_construct(**invalid_kind_payload)

    for observation in (nested_special, invalid_kind):
        constructed_snapshot = base.model_copy(update={"option_evidence": (observation,)})
        with pytest.raises(ValidationError):
            education_access.EducationAccessSourceSnapshot.model_validate(constructed_snapshot)


def test_assessment_rejects_constructed_missing_output_keys_before_sorting() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(
            school("secondary", EducationPhase.SECONDARY),
            school("special", EducationPhase.SPECIAL),
            school("unresolved", EducationPhase.UNRESOLVED),
        ),
        option_evidence=(),
        option_ids=("spine-a",),
        strategic_destinations=(admission(),),
    )
    cases = (
        ("school_access_obligations", 0, "school_id"),
        ("strategic_education_destination_access", 0, "strategic_destination_id"),
        ("school_evidence_requests", 0, "school_id"),
        ("independent_travel_opportunities", 0, "school_id"),
        ("special_school_accessibility_views", 0, "school_id"),
        ("network_gaps", 0, "school_id"),
    )
    for field_name, index, missing_key in cases:
        outputs = getattr(result, field_name)
        payload = outputs[index].model_dump(mode="python", round_trip=True)
        payload.pop(missing_key)
        constructed = type(outputs[index]).model_construct(**payload)
        tampered = result.model_copy(update={field_name: (constructed,)})
        with pytest.raises(ValidationError):
            education_access.EducationAccessAssessment.model_validate(tampered)

    gap = result.network_gaps[0]
    wrong_gap_payload = gap.model_dump(mode="python", round_trip=True)
    wrong_gap_payload["gap_kind"] = "strategic-education-destination"
    wrong_gap = education_access.SchoolAccessNetworkGap.model_construct(**wrong_gap_payload)
    with pytest.raises(ValidationError):
        education_access.EducationAccessAssessment.model_validate(
            result.model_copy(update={"network_gaps": (wrong_gap,)})
        )


def test_duplicate_option_evidence_is_rejected_in_both_input_orders() -> None:
    first = option(
        "spine-a",
        "secondary",
        access=("first-entrance",),
    )
    second = option(
        "spine-a",
        "secondary",
        access=("second-entrance",),
    )

    for observations in ((first, second), (second, first)):
        with pytest.raises(ValueError, match="option evidence values must be unique"):
            assess_education_access(
                register_evidence=REGISTER,
                schools=(school("secondary", EducationPhase.SECONDARY),),
                option_evidence=observations,
            )


@pytest.mark.parametrize(
    "invalid_distance",
    [None, True, "120", float("nan"), float("inf"), float("-inf"), -1],
)
def test_measured_distances_reject_null_coercion_non_finite_and_negative_values(
    invalid_distance: object,
) -> None:
    with pytest.raises(ValidationError):
        MeasuredDistance.model_validate({"distance_m": invalid_distance})


@pytest.mark.parametrize(
    ("evidence_model", "target_field", "target_id"),
    [
        (SchoolAccessEvidence, "school_id", "secondary"),
        (
            StrategicEducationDestinationEvidence,
            "strategic_destination_id",
            "university",
        ),
    ],
)
@pytest.mark.parametrize("field_name", ["connector_distance", "destination_distance"])
def test_distance_observations_reject_explicit_json_null(
    evidence_model: type[SchoolAccessEvidence | StrategicEducationDestinationEvidence],
    target_field: str,
    target_id: str,
    field_name: str,
) -> None:
    values: dict[str, object] = {
        "option_id": "spine-a",
        target_field: target_id,
        "connector_distance": {"status": "measured", "distance_m": 120},
        "connector_continuity": ConnectorContinuity.CONTINUOUS,
        "access_point_status": AccessPointStatus.MAPPED,
        "destination_distance": {"status": "measured", "distance_m": 900},
        "access_evidence_ids": ("entrance-record",),
    }
    values[field_name] = None

    with pytest.raises(ValidationError):
        evidence_model.model_validate_json(json.dumps(values))


def test_missing_distance_is_a_typed_observation_not_a_null_number() -> None:
    evidence = SchoolAccessEvidence(
        option_id="spine-a",
        school_id="secondary",
        connector_distance=DistanceNotObserved(),
        connector_continuity=ConnectorContinuity.UNKNOWN,
        access_point_status=AccessPointStatus.UNRESOLVED,
        destination_distance=DistanceNotObserved(),
    )

    assert evidence.connector_distance.status == "not-observed"
    assert evidence.destination_distance.status == "not-observed"
    payload = json.loads(evidence.model_dump_json())
    assert payload["connector_distance"] == {"status": "not-observed"}
    assert payload["destination_distance"] == {"status": "not-observed"}


def test_inferred_entrances_require_evidence_and_publish_verification_unknown() -> None:
    with pytest.raises(ValidationError, match="inferred access points require access evidence IDs"):
        SchoolAccessEvidence(
            option_id="spine-a",
            school_id="secondary",
            connector_distance=DistanceNotObserved(),
            destination_distance=DistanceNotObserved(),
            access_point_status=AccessPointStatus.INFERRED,
        )

    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(
            option(
                "spine-a",
                "secondary",
                access_point_status=AccessPointStatus.INFERRED,
            ),
        ),
        option_ids=("spine-a",),
    )

    obligation = result.school_access_obligations[0]
    assert obligation.status is AccessServiceStatus.SERVED_PROVISIONAL
    assert (
        CompilerDerivedUnknown.INFERRED_ACCESS_POINT_ENTRANCE_VERIFICATION_UNKNOWN
        in obligation.unknowns
    )


def test_independent_travel_is_not_derived_from_obligation_status() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(
            option(
                "no-obligation-evidence",
                "secondary",
                continuity=ConnectorContinuity.DISCONTINUOUS,
                independent=independent_evidence(),
            ),
        ),
        option_ids=("no-obligation-evidence",),
    )
    assert result.school_access_obligations[0].status is AccessServiceStatus.NETWORK_GAP
    assert (
        result.independent_travel_opportunities[0].status
        is IndependentTravelStatus.EVIDENCE_AVAILABLE
    )

    required = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(option("missing-factors", "secondary"),),
        option_ids=("missing-factors",),
    )
    opportunity = required.independent_travel_opportunities[0]
    assert opportunity.status is IndependentTravelStatus.EVIDENCE_REQUIRED
    assert opportunity.evidence.gradient.availability is EvidenceAvailability.UNKNOWN
    assert opportunity.evidence.audit.availability is EvidenceAvailability.UNKNOWN


def test_unserved_school_with_candidate_emits_canonical_network_gap() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(
            option(
                "candidate-route",
                "secondary",
                continuity=ConnectorContinuity.DISCONTINUOUS,
            ),
        ),
        option_ids=("candidate-route",),
    )

    obligation = result.school_access_obligations[0]
    gap = result.network_gaps[0]
    assert obligation.status is AccessServiceStatus.NETWORK_GAP
    assert isinstance(gap, education_access.SchoolAccessNetworkGap)
    assert gap.school_id == "secondary"
    assert gap.obligation_id == obligation.obligation_id
    assert gap.option_id == "candidate-route"
    assert gap.reason == "candidate-option-unserved"
    assert obligation.public_label is SchoolAccessLabel.GAP
    assert obligation.public_label.value == "school-access-obligation-network-gap"
    assert ExternalEvidenceUnknown.JUNCTION_DESIGN_OUTSIDE_SELECTION_PASS in obligation.unknowns


def test_unserved_strategic_destination_with_candidate_emits_typed_network_gap() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(),
        option_evidence=(),
        option_ids=("a4-spine",),
        strategic_destinations=(admission(),),
    )

    access = result.strategic_education_destination_access[0]
    gap = result.network_gaps[0]
    assert access.status is AccessServiceStatus.NETWORK_GAP
    assert isinstance(gap, education_access.StrategicEducationDestinationNetworkGap)
    assert gap.strategic_destination_id == "bath-spa-university"
    assert gap.option_id == "a4-spine"
    assert gap.reason == "candidate-option-unserved"
    assert gap.public_label == ("candidate-option-does-not-serve-strategic-education-destination")
    assert CompilerDerivedUnknown.NO_OPTION_SPECIFIC_EVIDENCE in access.unknowns


def test_special_school_never_uses_arbitrary_support_identifier_as_a_conclusion() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("special", EducationPhase.SPECIAL),),
        option_evidence=(option("spine-a", "special", support=("special-school-support-plan",)),),
        option_ids=("spine-a",),
    )

    assert result.school_access_obligations[0].status is AccessServiceStatus.SERVED
    assert (
        CompilerDerivedUnknown.NO_TYPED_SPECIAL_SCHOOL_EVIDENCE
        in result.school_access_obligations[0].unknowns
    )
    view = result.special_school_accessibility_views[0]
    assert view.accessibility.availability is EvidenceAvailability.UNKNOWN
    assert view.support.availability is EvidenceAvailability.UNKNOWN
    assert view.independent_travel.availability is EvidenceAvailability.UNKNOWN
    assert result.independent_travel_opportunities == ()


def test_special_school_evidence_is_rejected_for_non_special_school_sites() -> None:
    with pytest.raises(ValueError, match="only permitted for special schools"):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(school("secondary", EducationPhase.SECONDARY),),
            option_evidence=(option("spine-a", "secondary", special=special_evidence()),),
        )


def test_declared_option_without_education_evidence_keeps_independent_travel_unknown() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(),
        option_ids=("no-evidence-option",),
    )

    assert result.school_access_obligations[0].status is AccessServiceStatus.NETWORK_GAP
    assert (
        result.independent_travel_opportunities[0].status
        is IndependentTravelStatus.EVIDENCE_REQUIRED
    )


def test_empty_option_ids_rejects_all_option_evidence_as_rogue() -> None:
    with pytest.raises(ValueError, match="declared candidate option"):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(school("secondary", EducationPhase.SECONDARY),),
            option_evidence=(option("rogue-option", "secondary"),),
            option_ids=(),
        )


@pytest.mark.parametrize(
    "rogue_evidence",
    [
        option("rogue-option", "secondary"),
        strategic_option("rogue-option", "bath-spa-university"),
    ],
)
def test_declared_option_ids_reject_undeclared_option_evidence(
    rogue_evidence: education_access.OptionEducationEvidence,
) -> None:
    with pytest.raises(ValueError, match="declared candidate option"):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(school("secondary", EducationPhase.SECONDARY),),
            option_evidence=(rogue_evidence,),
            option_ids=("declared-option",),
            strategic_destinations=(admission(),),
        )


def test_frozen_strategic_admission_is_output_with_exactly_one_version_per_site() -> None:
    bath_spa = admission()
    admitted = assess_education_access(
        register_evidence=REGISTER,
        schools=(),
        option_evidence=(strategic_option("a4-spine", "bath-spa-university"),),
        option_ids=("a4-spine",),
        strategic_destinations=(bath_spa,),
    )

    assert admitted.school_access_obligations == ()
    assert admitted.strategic_education_destination_access[0].strategic_destination_id == (
        "bath-spa-university"
    )
    assert admitted.strategic_education_destinations[0] == bath_spa
    with pytest.raises(ValueError, match="admission values must be unique"):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(),
            option_evidence=(),
            strategic_destinations=(admission(), admission(record_id="bath-spa-admission-v2")),
        )


def test_unknown_place_evidence_duplicate_options_and_whitespace_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="known School"):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(school("secondary", EducationPhase.SECONDARY),),
            option_evidence=(option("spine-a", "unknown-site"),),
        )
    with pytest.raises(ValueError, match="must not contain duplicates"):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(school("secondary", EducationPhase.SECONDARY),),
            option_evidence=(),
            option_ids=("spine-a", "spine-a"),
        )
    with pytest.raises(ValidationError, match="contain no whitespace"):
        SchoolAccessEvidence(
            option_id="spine a",
            school_id="secondary",
            connector_distance=DistanceNotObserved(),
            destination_distance=DistanceNotObserved(),
            access_point_status=AccessPointStatus.UNRESOLVED,
        )
    with pytest.raises(ValidationError, match="duplicates"):
        SchoolAccessEvidence(
            option_id="spine-a",
            school_id="secondary",
            connector_distance=DistanceNotObserved(),
            destination_distance=DistanceNotObserved(),
            access_point_status=AccessPointStatus.MAPPED,
            access_evidence_ids=("entrance", "entrance"),
        )
    with pytest.raises(ValidationError, match="duplicate evidence IDs"):
        SchoolAccessEvidence(
            option_id="spine-a",
            school_id="secondary",
            connector_distance=DistanceNotObserved(),
            destination_distance=DistanceNotObserved(),
            access_point_status=AccessPointStatus.UNRESOLVED,
            route_quality_evidence=(
                RouteQualityEvidence(
                    evidence_id="route", observation=RouteObservationKind.CROSSING_RECORDED
                ),
                RouteQualityEvidence(
                    evidence_id="route", observation=RouteObservationKind.LIGHTING_RECORDED
                ),
            ),
        )


def test_nested_set_like_evidence_is_canonical_and_reversal_equivalent() -> None:
    route_evidence = (
        RouteQualityEvidence(
            evidence_id="z-lighting",
            observation=RouteObservationKind.LIGHTING_RECORDED,
        ),
        RouteQualityEvidence(
            evidence_id="a-crossing",
            observation=RouteObservationKind.CROSSING_RECORDED,
        ),
    )
    unknowns = (ExternalEvidenceUnknown.JUNCTION_DESIGN_OUTSIDE_SELECTION_PASS,)
    first = SchoolAccessEvidence(
        option_id="spine-a",
        school_id="secondary",
        connector_distance=MeasuredDistance(distance_m=120),
        connector_continuity=ConnectorContinuity.CONTINUOUS,
        access_point_status=AccessPointStatus.MAPPED,
        destination_distance=MeasuredDistance(distance_m=900),
        access_evidence_ids=("z-entrance", "a-entrance"),
        support_evidence_ids=("z-support", "a-support"),
        route_quality_evidence=route_evidence,
        unknowns=unknowns,
    )
    second = SchoolAccessEvidence(
        option_id="spine-a",
        school_id="secondary",
        connector_distance=MeasuredDistance(distance_m=120),
        connector_continuity=ConnectorContinuity.CONTINUOUS,
        access_point_status=AccessPointStatus.MAPPED,
        destination_distance=MeasuredDistance(distance_m=900),
        access_evidence_ids=tuple(reversed(first.access_evidence_ids)),
        support_evidence_ids=tuple(reversed(first.support_evidence_ids)),
        route_quality_evidence=tuple(reversed(route_evidence)),
        unknowns=tuple(reversed(unknowns)),
    )
    first_admission = admission(
        admission_evidence_ids=("z-admission", "a-admission"),
        access_evidence_ids=("z-access", "a-access"),
    )
    second_admission = admission(
        admission_evidence_ids=tuple(reversed(first_admission.admission_evidence_ids)),
        access_evidence_ids=tuple(reversed(first_admission.access_evidence_ids)),
    )

    assert first == second
    assert first_admission == second_admission


@pytest.mark.parametrize("generated_unknown", tuple(CompilerDerivedUnknown))
def test_option_evidence_rejects_compiler_derived_unknowns(
    generated_unknown: CompilerDerivedUnknown,
) -> None:
    with pytest.raises(ValidationError):
        option(
            "spine-a",
            "secondary",
            unknowns=(generated_unknown,),  # type: ignore[arg-type]
        )

    bypassed_validation = option(
        "spine-a",
        "secondary",
        unknowns=(),
    ).model_copy(update={"unknowns": (generated_unknown,)})
    with pytest.raises(ValueError, match="unknowns"):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(school("secondary", EducationPhase.SECONDARY),),
            option_evidence=(bypassed_validation,),
        )


def test_complete_served_and_independent_travel_evidence_has_no_derived_unknowns() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(
            option(
                "spine-a",
                "secondary",
                independent=independent_evidence(),
                unknowns=(),
            ),
        ),
        option_ids=("spine-a",),
    )

    assert result.school_access_obligations[0].status is AccessServiceStatus.SERVED
    assert (
        result.independent_travel_opportunities[0].status
        is IndependentTravelStatus.EVIDENCE_AVAILABLE
    )
    assert result.school_access_obligations[0].unknowns == ()
    assert result.independent_travel_opportunities[0].unknowns == ()


def test_continuous_connector_without_support_evidence_is_never_served() -> None:
    school_result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(option("spine-a", "secondary", support=()),),
        option_ids=("spine-a",),
    )
    destination_result = assess_education_access(
        register_evidence=REGISTER,
        schools=(),
        option_evidence=(
            strategic_option(
                "spine-a",
                "bath-spa-university",
                support=(),
            ),
        ),
        option_ids=("spine-a",),
        strategic_destinations=(admission(),),
    )

    assert (
        school_result.school_access_obligations[0].status is AccessServiceStatus.SERVED_PROVISIONAL
    )
    assert (
        destination_result.strategic_education_destination_access[0].status
        is AccessServiceStatus.SERVED_PROVISIONAL
    )
    assert school_result.network_gaps == ()
    assert destination_result.network_gaps == ()


def test_assessment_is_reversal_equivalent_for_set_like_inputs() -> None:
    schools = (
        school("secondary", EducationPhase.SECONDARY),
        school("primary", EducationPhase.PRIMARY),
    )
    destinations = (
        admission("z-university", "z-admission"),
        admission("a-college", "a-admission"),
    )
    first = assess_education_access(
        register_evidence=REGISTER,
        schools=schools,
        option_evidence=(),
        option_ids=("z-route", "a-route"),
        strategic_destinations=destinations,
    )
    second = assess_education_access(
        register_evidence=REGISTER,
        schools=tuple(reversed(schools)),
        option_evidence=(),
        option_ids=("a-route", "z-route"),
        strategic_destinations=tuple(reversed(destinations)),
    )

    assert first == second


def test_source_snapshot_embeds_exact_inputs_and_round_trips_canonically() -> None:
    schools = (
        school("secondary", EducationPhase.SECONDARY),
        school("primary", EducationPhase.PRIMARY),
    )
    destinations = (
        admission("z-university", "z-admission"),
        admission("a-college", "a-admission"),
    )
    evidence = (
        option("z-route", "secondary"),
        strategic_option("a-route", "a-college"),
    )
    first = assess_education_access(
        register_evidence=REGISTER,
        schools=schools,
        option_evidence=evidence,
        option_ids=("z-route", "a-route"),
        strategic_destinations=destinations,
        supplementary_pct_evidence=(pct(),),
    )
    reversed_inputs = assess_education_access(
        register_evidence=REGISTER,
        schools=tuple(reversed(schools)),
        option_evidence=tuple(reversed(evidence)),
        option_ids=("a-route", "z-route"),
        strategic_destinations=tuple(reversed(destinations)),
        supplementary_pct_evidence=(pct(),),
    )

    snapshot = first.source_snapshot
    assert snapshot.option_ids == ("a-route", "z-route")
    assert snapshot.register_evidence == REGISTER
    assert snapshot.schools == tuple(sorted(schools, key=lambda item: item.school_id))
    assert [
        item.strategic_destination_id for item in snapshot.strategic_education_destinations
    ] == [
        "a-college",
        "z-university",
    ]
    assert snapshot.option_evidence == tuple(
        sorted(evidence, key=education_access._option_evidence_key)
    )
    assert snapshot.supplementary_pct_evidence == (pct(),)
    assert len(snapshot.source_content_fingerprint) == 64
    assert len(snapshot.source_snapshot_fingerprint) == 64
    assert len(first.assessment_id) == 64
    assert snapshot.source_snapshot_fingerprint == (
        reversed_inputs.source_snapshot.source_snapshot_fingerprint
    )
    assert first.assessment_id == reversed_inputs.assessment_id

    encoded = first.model_dump_json()
    round_trip = education_access.EducationAccessAssessment.model_validate_json(encoded)
    assert round_trip == first
    assert round_trip.model_dump_json() == encoded

    changed_evidence = assess_education_access(
        register_evidence=REGISTER,
        schools=schools,
        option_evidence=(
            option("z-route", "secondary", support=("different-continuity-record",)),
            strategic_option("a-route", "a-college"),
        ),
        option_ids=("z-route", "a-route"),
        strategic_destinations=destinations,
        supplementary_pct_evidence=(pct(),),
    )
    assert snapshot.source_snapshot_fingerprint != (
        changed_evidence.source_snapshot.source_snapshot_fingerprint
    )
    assert first.assessment_id != changed_evidence.assessment_id


def test_source_snapshot_prevents_mutually_deleted_targets_requests_and_gaps() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("unresolved", EducationPhase.UNRESOLVED),),
        option_evidence=(),
        strategic_destinations=(admission(),),
    )
    base_payload = {
        name: getattr(result, name)
        for name in education_access.EducationAccessAssessment.model_fields
    }

    without_school = dict(base_payload)
    without_school["school_evidence_requests"] = ()
    without_school["network_gaps"] = tuple(
        gap
        for gap in result.network_gaps
        if not isinstance(gap, education_access.SchoolAccessNetworkGap)
    )
    with pytest.raises(ValidationError, match="derived outputs"):
        education_access.EducationAccessAssessment(**without_school)

    without_destination = dict(base_payload)
    without_destination["network_gaps"] = tuple(
        gap
        for gap in result.network_gaps
        if not isinstance(
            gap,
            education_access.StrategicEducationDestinationNetworkGap,
        )
    )
    with pytest.raises(ValidationError, match="derived outputs"):
        education_access.EducationAccessAssessment(**without_destination)

    without_request = dict(base_payload)
    without_request["school_evidence_requests"] = ()
    with pytest.raises(ValidationError, match="derived outputs"):
        education_access.EducationAccessAssessment(**without_request)

    without_gap = dict(base_payload)
    without_gap["network_gaps"] = result.network_gaps[1:]
    with pytest.raises(ValidationError, match="derived outputs"):
        education_access.EducationAccessAssessment(**without_gap)

    without_school_source = result.model_dump(mode="python", round_trip=True)
    without_school_source["source_snapshot"]["schools"] = ()
    without_school_source["school_evidence_requests"] = ()
    without_school_source["network_gaps"] = tuple(
        gap
        for gap in result.network_gaps
        if not isinstance(gap, education_access.SchoolAccessNetworkGap)
    )
    with pytest.raises(ValidationError, match=r"source.*fingerprint"):
        education_access.EducationAccessAssessment.model_validate(without_school_source)

    without_destination_source = result.model_dump(mode="python", round_trip=True)
    without_destination_source["source_snapshot"]["strategic_education_destinations"] = ()
    without_destination_source["network_gaps"] = tuple(
        gap
        for gap in result.network_gaps
        if not isinstance(
            gap,
            education_access.StrategicEducationDestinationNetworkGap,
        )
    )
    with pytest.raises(ValidationError, match=r"source.*fingerprint"):
        education_access.EducationAccessAssessment.model_validate(without_destination_source)


def test_assessment_rejects_construct_copy_name_binding_and_conclusion_tampering() -> None:
    served = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(option("spine-a", "secondary"),),
        option_ids=("spine-a",),
    )
    copied = served.model_copy(update={"assessment_id": "0" * 64})
    with pytest.raises(ValidationError, match="assessment_id"):
        education_access.EducationAccessAssessment.model_validate(copied)

    constructed = education_access.EducationAccessAssessment.model_construct(
        **{
            **{
                name: getattr(served, name)
                for name in education_access.EducationAccessAssessment.model_fields
            },
            "assessment_id": "f" * 64,
        }
    )
    with pytest.raises(ValidationError, match="assessment_id"):
        education_access.EducationAccessAssessment.model_validate(constructed)

    changed_school = served.source_snapshot.schools[0].model_copy(
        update={"name": "Changed School Name"}
    )
    changed_snapshot = served.source_snapshot.model_copy(update={"schools": (changed_school,)})
    changed_name = served.model_copy(update={"source_snapshot": changed_snapshot})
    with pytest.raises(ValidationError, match=r"source.*fingerprint"):
        education_access.EducationAccessAssessment.model_validate(changed_name)

    obligation = served.school_access_obligations[0]
    changed_binding = obligation.source_binding.model_copy(
        update={"source_record_fingerprint": "0" * 64}
    )
    changed_obligation = obligation.model_copy(update={"source_binding": changed_binding})
    changed_binding_assessment = served.model_copy(
        update={"school_access_obligations": (changed_obligation,)}
    )
    with pytest.raises(ValidationError, match="derived outputs"):
        education_access.EducationAccessAssessment.model_validate(changed_binding_assessment)

    gap = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(
            option(
                "spine-a",
                "secondary",
                continuity=ConnectorContinuity.DISCONTINUOUS,
            ),
        ),
        option_ids=("spine-a",),
    )
    flipped_conclusion = served.model_copy(
        update={
            "school_access_obligations": gap.school_access_obligations,
            "network_gaps": gap.network_gaps,
        }
    )
    with pytest.raises(ValidationError, match="derived outputs"):
        education_access.EducationAccessAssessment.model_validate(flipped_conclusion)


def test_outputs_bind_exact_source_records_and_option_evidence() -> None:
    special_evidence = SpecialSchoolEvidence(
        accessibility=available_factor("accessible"),
        support=available_factor("support"),
        independent_travel=available_factor("independent"),
    )
    school_record = school("special", EducationPhase.SPECIAL)
    school_observation = option(
        "spine-a",
        "special",
        special=special_evidence,
    )
    destination_record = admission()
    destination_observation = strategic_option(
        "spine-a",
        destination_record.strategic_destination_id,
    )
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school_record,),
        option_evidence=(school_observation, destination_observation),
        option_ids=("spine-a",),
        strategic_destinations=(destination_record,),
    )

    school_binding = result.school_access_obligations[0].source_binding
    special_binding = result.special_school_accessibility_views[0].source_binding
    destination_binding = result.strategic_education_destination_access[0].source_binding
    assert school_binding.source_record_fingerprint == (
        education_access.canonical_sha256(school_record.model_dump(mode="json"))
    )
    assert school_binding.option_evidence_fingerprint == (
        education_access.canonical_sha256(school_observation.model_dump(mode="json"))
    )
    assert special_binding == school_binding
    assert destination_binding.source_record_fingerprint == (
        education_access.canonical_sha256(destination_record.model_dump(mode="json"))
    )
    assert destination_binding.option_evidence_fingerprint == (
        education_access.canonical_sha256(destination_observation.model_dump(mode="json"))
    )
    assert result.source_snapshot.option_evidence == (
        school_observation,
        destination_observation,
    )
    assert any(
        item.record_kind == "special-school-evidence"
        for item in result.source_snapshot.record_fingerprints
    )

    gap_result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(),
        option_ids=("spine-a",),
    )
    assert gap_result.independent_travel_opportunities[0].source_binding == (
        gap_result.school_access_obligations[0].source_binding
    )
    assert gap_result.network_gaps[0].source_binding == (
        gap_result.school_access_obligations[0].source_binding
    )

    no_candidate = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("unresolved", EducationPhase.UNRESOLVED),),
        option_evidence=(),
    )
    assert no_candidate.school_evidence_requests[0].source_binding == (
        no_candidate.network_gaps[0].source_binding
    )

    changed_input = assess_education_access(
        register_evidence=REGISTER,
        schools=(
            school(
                "special",
                EducationPhase.SPECIAL,
                name="Changed School Name",
            ),
        ),
        option_evidence=(school_observation, destination_observation),
        option_ids=("spine-a",),
        strategic_destinations=(destination_record,),
    )
    assert changed_input.source_snapshot.source_snapshot_fingerprint != (
        result.source_snapshot.source_snapshot_fingerprint
    )
    assert changed_input.assessment_id != result.assessment_id


def test_public_domain_models_use_the_canonical_glossary_names() -> None:
    assert education_access.AccessServiceStatus is AccessServiceStatus
    assert education_access.IndependentTravelPhase is IndependentTravelPhase
    assert education_access.School is School
    assert education_access.SchoolAccessObligation
    assert education_access.StrategicEducationDestination
    assert education_access.NetworkGap
    assert not hasattr(education_access, "EducationSite")
    assert not hasattr(education_access, "EducationObligation")
    assert not hasattr(education_access, "EducationNetworkGap")
    assert not hasattr(education_access, "AccessStatus")


def test_school_access_evidence_contains_no_school_network_place_concept() -> None:
    module_source = inspect.getsource(education_access)
    public_strings = {
        value
        for member in vars(education_access).values()
        if isinstance(member, type) and issubclass(member, StrEnum)
        for value in member
    }
    model_fields = {
        field_name
        for model in (
            education_access.School,
            education_access.SchoolAccessEvidence,
            education_access.SchoolAccessObligation,
            education_access.NetworkGap,
            education_access.SchoolAccessNetworkGap,
            education_access.StrategicEducationDestinationNetworkGap,
        )
        for field_name in model.model_fields
    }

    assert "network_place_id" not in model_fields
    assert all("network-place" not in value for value in public_strings)
    assert all("school-network-place" not in value for value in public_strings)
    assert "network_place" not in module_source
    assert "network-place" not in module_source
    assert "School Network Place" not in module_source


def test_pct_requires_full_historical_scenario_contract_and_cannot_be_newer() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(option("spine-a", "secondary"),),
        option_ids=("spine-a",),
        supplementary_pct_evidence=(pct(),),
    )
    assert result.supplementary_pct_evidence[0].as_of == 2011
    assert result.supplementary_pct_evidence[0].disposition == "supplementary"
    assert result.supplementary_pct_evidence[0].limitations == tuple(PCTLimitation)
    assert SupplementaryPCTEvidence(
        evidence_id="pct-reordered",
        phase=EducationPhase.SECONDARY,
        scenario_id="school-access-scenario",
        method_version="pct-method-2011",
        routing_version="routing-2011",
        included_population=PCTIncludedPopulation.HISTORICAL_SECONDARY_SCHOOL_TRAVEL_RECORDS,
        excluded_population=PCTExcludedPopulation.OUTSIDE_SCENARIO_BOUNDARY,
        coverage=PCTCoverage.HISTORICAL_ORIGIN_DESTINATION,
        limitations=(
            PCTLimitation.CANNOT_ESTABLISH_SAFETY,
            PCTLimitation.CANNOT_ESTABLISH_SAFETY,
            PCTLimitation.CANNOT_ESTABLISH_CURRENT_DEMAND,
        ),
    ).limitations == tuple(PCTLimitation)
    with pytest.raises(ValidationError):
        SupplementaryPCTEvidence(
            evidence_id="pct-primary",
            phase=EducationPhase.PRIMARY,
            scenario_id="school-access-scenario",
            method_version="pct-method-2011",
            routing_version="routing-2011",
            included_population=PCTIncludedPopulation.HISTORICAL_SECONDARY_SCHOOL_TRAVEL_RECORDS,
            excluded_population=PCTExcludedPopulation.OUTSIDE_SCENARIO_BOUNDARY,
            coverage=PCTCoverage.HISTORICAL_ORIGIN_DESTINATION,
        )
    with pytest.raises(ValidationError):
        SupplementaryPCTEvidence(evidence_id="newer", phase=EducationPhase.SECONDARY, as_of=2021)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        SupplementaryPCTEvidence(evidence_id="incomplete", phase=EducationPhase.SECONDARY)  # type: ignore[call-arg]


def test_no_candidate_options_emit_deterministic_governed_network_gaps() -> None:
    schools = (
        school("primary", EducationPhase.PRIMARY),
        school("secondary", EducationPhase.SECONDARY),
    )

    first = assess_education_access(
        register_evidence=REGISTER,
        schools=schools,
        option_evidence=(),
    )
    second = assess_education_access(
        register_evidence=REGISTER,
        schools=tuple(reversed(schools)),
        option_evidence=(),
    )

    assert first.school_access_obligations == ()
    assert [(gap.school_id, gap.reason) for gap in first.network_gaps] == [
        ("primary", "no-candidate-options"),
        ("secondary", "no-candidate-options"),
    ]
    assert all(
        gap.obligation_id == education_access.stable_id("school-access-obligation", gap.school_id)
        for gap in first.network_gaps
    )
    assert first.network_gaps == second.network_gaps


def test_no_candidate_strategic_destination_gap_has_no_fabricated_distance() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(),
        option_evidence=(),
        strategic_destinations=(admission(),),
    )

    gap = result.network_gaps[0]
    assert isinstance(gap, education_access.StrategicEducationDestinationNetworkGap)
    assert gap.strategic_destination_id == "bath-spa-university"
    payload = gap.model_dump()
    assert "connector_distance" not in payload
    assert "destination_distance" not in payload


def test_same_local_school_and_destination_ids_have_unique_no_candidate_gap_ids() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("shared-local-id", EducationPhase.SECONDARY),),
        option_evidence=(),
        strategic_destinations=(
            admission(
                strategic_destination_id="shared-local-id",
                record_id="shared-destination-admission",
            ),
        ),
    )

    gaps_by_kind = {gap.gap_kind: gap for gap in result.network_gaps}
    gap_ids = [gap.gap_id for gap in result.network_gaps]
    assert len(gap_ids) == len(set(gap_ids)) == 2
    assert gaps_by_kind["school-access-obligation"].gap_id == education_access.stable_id(
        "network-gap",
        "school-access-obligation",
        "no-candidate-options",
        "shared-local-id",
    )
    assert gaps_by_kind["strategic-education-destination"].gap_id == education_access.stable_id(
        "network-gap",
        "strategic-education-destination",
        "no-candidate-options",
        "shared-local-id",
    )


def test_same_local_school_and_destination_ids_have_unique_candidate_gap_ids() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("shared-local-id", EducationPhase.SECONDARY),),
        option_evidence=(),
        option_ids=("shared-option",),
        strategic_destinations=(
            admission(
                strategic_destination_id="shared-local-id",
                record_id="shared-destination-admission",
            ),
        ),
    )

    gaps_by_kind = {gap.gap_kind: gap for gap in result.network_gaps}
    gap_ids = [gap.gap_id for gap in result.network_gaps]
    assert len(gap_ids) == len(set(gap_ids)) == 2
    assert gaps_by_kind["school-access-obligation"].gap_id == education_access.stable_id(
        "network-gap",
        "school-access-obligation",
        "candidate-option-unserved",
        "shared-local-id",
        "shared-option",
    )
    assert gaps_by_kind["strategic-education-destination"].gap_id == education_access.stable_id(
        "network-gap",
        "strategic-education-destination",
        "candidate-option-unserved",
        "shared-local-id",
        "shared-option",
    )


def test_assessment_rejects_duplicate_network_gap_ids() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(),
    )
    payload = result.model_dump()
    payload["network_gaps"] = [payload["network_gaps"][0], payload["network_gaps"][0]]

    with pytest.raises(ValidationError, match="Network Gap IDs must be unique"):
        education_access.EducationAccessAssessment.model_validate(payload)


def test_network_gap_models_and_assessment_reject_swapped_gap_ids() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("shared-local-id", EducationPhase.SECONDARY),),
        option_evidence=(),
        strategic_destinations=(
            admission(
                strategic_destination_id="shared-local-id",
                record_id="shared-destination-admission",
            ),
        ),
    )
    school_gap = next(
        gap
        for gap in result.network_gaps
        if isinstance(gap, education_access.SchoolAccessNetworkGap)
    )
    destination_gap = next(
        gap
        for gap in result.network_gaps
        if isinstance(gap, education_access.StrategicEducationDestinationNetworkGap)
    )

    for gap, swapped_gap_id in (
        (school_gap, destination_gap.gap_id),
        (destination_gap, school_gap.gap_id),
    ):
        payload = gap.model_dump()
        payload["gap_id"] = swapped_gap_id
        with pytest.raises(ValidationError, match="canonical gap_id"):
            type(gap).model_validate(payload)

    tampered_school_gap = school_gap.model_copy(update={"gap_id": destination_gap.gap_id})
    assessment_payload = {
        name: getattr(result, name)
        for name in education_access.EducationAccessAssessment.model_fields
    }
    assessment_payload["network_gaps"] = (tampered_school_gap, destination_gap)
    with pytest.raises(ValidationError, match="canonical gap_id"):
        education_access.EducationAccessAssessment(**assessment_payload)


def test_untyped_network_gap_and_arbitrary_obligation_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="typed Network Gap"):
        education_access.NetworkGap(
            gap_id="arbitrary-gap",
            reason="no-candidate-options",
        )

    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(),
        option_ids=("spine-a",),
    )
    obligation = result.school_access_obligations[0]
    gap = result.network_gaps[0]

    obligation_payload = obligation.model_dump()
    obligation_payload["obligation_id"] = "arbitrary-obligation"
    with pytest.raises(ValidationError, match="canonical obligation_id"):
        education_access.SchoolAccessObligation.model_validate(obligation_payload)

    gap_payload = gap.model_dump()
    gap_payload["obligation_id"] = "arbitrary-obligation"
    with pytest.raises(ValidationError, match="canonical obligation_id"):
        education_access.SchoolAccessNetworkGap.model_validate(gap_payload)


def test_strategic_access_id_is_bound_to_destination_and_option() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(),
        option_evidence=(strategic_option("a4-spine", "bath-spa-university"),),
        option_ids=("a4-spine",),
        strategic_destinations=(admission(),),
    )
    access = result.strategic_education_destination_access[0]

    for updates in (
        {"access_id": "arbitrary-access"},
        {"strategic_destination_id": "swapped-destination"},
        {"option_id": "swapped-option"},
    ):
        payload = access.model_dump()
        payload.update(updates)
        with pytest.raises(ValidationError, match="canonical access_id"):
            education_access.StrategicEducationDestinationAccess.model_validate(payload)


def test_public_access_outputs_reject_contradictory_status_labels_and_evidence() -> None:
    obligation = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(option("spine-a", "secondary"),),
        option_ids=("spine-a",),
    ).school_access_obligations[0]
    obligation_mutations = (
        {"status": AccessServiceStatus.NETWORK_GAP},
        {"public_label": SchoolAccessLabel.GAP},
        {"access_point_status": AccessPointStatus.UNRESOLVED},
        {"connector_distance": DistanceNotObserved()},
        {"support_evidence_ids": ()},
    )
    for updates in obligation_mutations:
        payload = obligation.model_dump()
        payload.update(updates)
        with pytest.raises(ValidationError):
            education_access.SchoolAccessObligation.model_validate(payload)

    destination = assess_education_access(
        register_evidence=REGISTER,
        schools=(),
        option_evidence=(strategic_option("spine-a", "bath-spa-university"),),
        option_ids=("spine-a",),
        strategic_destinations=(admission(),),
    ).strategic_education_destination_access[0]
    destination_payload = destination.model_dump()
    destination_payload["status"] = AccessServiceStatus.NETWORK_GAP
    with pytest.raises(ValidationError, match="status contradicts"):
        education_access.StrategicEducationDestinationAccess.model_validate(destination_payload)


def test_independent_travel_output_identity_evidence_and_phase_are_enforced() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(
            option(
                "spine-a",
                "secondary",
                independent=independent_evidence(),
            ),
        ),
        option_ids=("spine-a",),
    )
    opportunity = result.independent_travel_opportunities[0]
    for updates in (
        {"opportunity_id": "arbitrary-opportunity"},
        {"status": IndependentTravelStatus.EVIDENCE_REQUIRED},
        {"public_label": education_access.IndependentTravelLabel.FACTORS_REQUIRED},
        {"evidence": education_access._unknown_independent_evidence()},
    ):
        payload = opportunity.model_dump()
        payload.update(updates)
        with pytest.raises(ValidationError):
            education_access.IndependentTravelOpportunity.model_validate(payload)

    wrong_phase = opportunity.model_copy(
        update={"phase": IndependentTravelPhase.ALL_THROUGH_SECONDARY}
    )
    assessment_payload = {
        name: getattr(result, name)
        for name in education_access.EducationAccessAssessment.model_fields
    }
    assessment_payload["independent_travel_opportunities"] = (wrong_phase,)
    with pytest.raises(ValidationError, match="phase must match"):
        education_access.EducationAccessAssessment(**assessment_payload)


def test_special_view_and_school_request_ids_are_canonical() -> None:
    result = assess_education_access(
        register_evidence=REGISTER,
        schools=(
            school("special", EducationPhase.SPECIAL),
            school("unresolved", EducationPhase.UNRESOLVED),
        ),
        option_evidence=(),
        option_ids=("spine-a",),
    )
    special_view = result.special_school_accessibility_views[0]
    view_payload = special_view.model_dump()
    view_payload["view_id"] = "arbitrary-view"
    with pytest.raises(ValidationError, match="canonical view_id"):
        education_access.SpecialSchoolAccessibilityView.model_validate(view_payload)

    request = result.school_evidence_requests[0]
    request_payload = request.model_dump()
    request_payload["request_id"] = "arbitrary-request"
    with pytest.raises(ValidationError, match="canonical request_id"):
        education_access.SchoolEvidenceRequest.model_validate(request_payload)


def test_assessment_gaps_correspond_exactly_to_network_gap_access_outputs() -> None:
    gap_result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(),
        option_ids=("spine-a",),
    )
    missing_gap_payload = {
        name: getattr(gap_result, name)
        for name in education_access.EducationAccessAssessment.model_fields
    }
    missing_gap_payload["network_gaps"] = ()
    with pytest.raises(ValidationError, match="exactly match"):
        education_access.EducationAccessAssessment(**missing_gap_payload)

    served_result = assess_education_access(
        register_evidence=REGISTER,
        schools=(school("secondary", EducationPhase.SECONDARY),),
        option_evidence=(option("spine-a", "secondary"),),
        option_ids=("spine-a",),
    )
    extra_gap_payload = {
        name: getattr(served_result, name)
        for name in education_access.EducationAccessAssessment.model_fields
    }
    extra_gap_payload["network_gaps"] = gap_result.network_gaps
    with pytest.raises(ValidationError, match="exactly match"):
        education_access.EducationAccessAssessment(**extra_gap_payload)


def test_independent_travel_evidence_is_rejected_for_non_secondary_school_phases() -> None:
    with pytest.raises(ValueError, match="only permitted"):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(school("primary", EducationPhase.PRIMARY),),
            option_evidence=(option("spine-a", "primary", independent=independent_evidence()),),
        )


def test_destination_evidence_is_rejected_without_strategic_admission() -> None:
    with pytest.raises(ValueError, match="known Strategic Education Destination"):
        assess_education_access(
            register_evidence=REGISTER,
            schools=(),
            option_evidence=(strategic_option("a4-spine", "contextual-university"),),
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RouteQualityEvidence(evidence_id="route", observation="used safely"),
        lambda: SchoolAccessEvidence(
            option_id="spine-a",
            school_id="secondary",
            connector_distance=DistanceNotObserved(),
            destination_distance=DistanceNotObserved(),
            access_point_status=AccessPointStatus.UNRESOLVED,
            unknowns=("actual route choice",),
        ),
        lambda: StrategicEducationDestination(
            record_id="record",
            record_version="1",
            strategic_destination_id="destination",
            name="Destination",
            source_evidence_id="source",
            admitted_on=date(2026, 7, 2),
            rationale="used safely",
            admission_evidence_ids=("admission",),
            review_trigger="actual route choice",
            access_evidence_ids=("entrance",),
        ),
        lambda: SupplementaryPCTEvidence(
            evidence_id="pct",
            phase=EducationPhase.SECONDARY,
            scenario_id="scenario",
            method_version="method",
            routing_version="routing",
            included_population="used safely",
            excluded_population="actual route choice",
            coverage="complete evidence",
        ),
    ],
)
def test_public_claim_text_cannot_enter_structured_education_outputs(factory: object) -> None:
    with pytest.raises(ValidationError):
        factory()  # type: ignore[operator]
