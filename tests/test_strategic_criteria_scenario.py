"""Bath-Saltford proof for the strategic criteria-and-Scenario safety seam."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from bath_saltford_fixture import configured_bath_saltford
from pydantic import ValidationError

from satn.agents import FakeAgentRuntime
from satn.alignment_selection import (
    AssessmentKind,
    CandidateCriteria,
    EducationCriterionSummary,
    GovernedAssessmentBinding,
    GovernedEducationCriterionBinding,
    GovernedEvidenceSnapshot,
)
from satn.compiler import compile_network
from satn.education_access import (
    StrategicEducationDestination,
    assess_education_access,
    governed_education_assessment_fingerprint,
)
from satn.evidence import mark_ncn_edges
from satn.psa_evidence_loaders import (
    load_education_access_evidence,
    load_population_reach_evidence,
)
from satn.routing import RoadGraph
from satn.sources import load_snapshot, snapshot
from satn.strategic_corridors import (
    StrategicCorridorUnitRole,
    prepare_strategic_corridors,
)
from satn.strategic_criteria_scenario import (
    PreparedStrategicUnitCriteria,
    StrategicCriteriaScenarioInput,
    compile_strategic_criteria_scenario,
)


def _compiled_inputs(tmp_path: Path):
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    source = load_snapshot(config)
    compiled = compile_network(config, source, FakeAgentRuntime())
    population = load_population_reach_evidence(
        config.source.population_reach_evidence,
        base_directory=config.config_path.parent,
        pwc_outside_tolerance_m=0,
    )
    education = load_education_access_evidence(
        config.source.school_register_evidence,
        config.source.strategic_education_destination_admissions,
        base_directory=config.config_path.parent,
        as_at=config.source.network_selection_as_at,
        school_register_max_age_days=(
            config.source.network_selection_school_register_max_age_days
        ),
        strategic_admissions_max_age_days=(
            config.source.network_selection_strategic_admissions_max_age_days
        ),
    )
    assert population is not None and education is not None
    return config, source, compiled, population, education


def _area_fingerprint(source) -> str:
    return hashlib.sha256(
        b"".join(bytes(item.wkb) for item in source["boundary"].geometry)
    ).hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def test_bath_compiles_both_strategic_roles_without_reference_authority(
    tmp_path: Path,
) -> None:
    _, source, compiled, population, education = _compiled_inputs(tmp_path)
    preparation = compiled.strategic_corridor_preparation
    assert preparation is not None
    request = StrategicCriteriaScenarioInput(
        preparation=preparation,
        population_evidence=population,
        education_evidence=education,
        area_definition=source["boundary"],
        area_fingerprint=_area_fingerprint(source),
    )

    # The request owns detached geometry. A caller mutation after construction
    # cannot alter criterion or Scenario identity.
    source["boundary"].loc[
        source["boundary"].index[0],
        "geometry",
    ] = source["boundary"].geometry.iloc[0].buffer(-1)
    first = compile_strategic_criteria_scenario(request)
    repeated = compile_strategic_criteria_scenario(request)

    assert first.result_fingerprint == repeated.result_fingerprint
    assert first.status == "review-required"
    assert first.human_adoption_required
    assert not first.reference_satn_created
    assert not first.can_mutate_authoritative_network
    assert {item.unit_role.value for item in first.criteria} == {
        "interurban-spine",
        "strategic-destination-access",
    }
    scenario = first.scenario
    assert scenario is not None
    assert {item.value for item in scenario.required_network_role_ids} == {
        "interurban-spine",
        "strategic-destination-access",
    }
    assert scenario.mandatory_network_place_ids == ("bath-edge", "saltford")
    assert scenario.mandatory_strategic_destination_ids == (
        "bath-spa-university",
    )
    assert len(scenario.candidate_sets) == 2
    assert len(scenario.selections) == 2
    assert first.review_orchestration is not None
    assert first.diagnostics["agent_runtime_constructed"] is False
    assert first.diagnostics["authoritative_network_geometry_mutated"] is False
    assert first.diagnostics["publication_performed"] is False

    criteria_by_role = {item.unit_role.value: item.criteria for item in first.criteria}
    interurban_source = criteria_by_role[
        "interurban-spine"
    ].education.assessment.source_snapshot
    destination_source = criteria_by_role[
        "strategic-destination-access"
    ].education.assessment.source_snapshot
    assert not interurban_source.option_evidence
    assert {
        item.strategic_destination_id for item in destination_source.option_evidence
    } == {"bath-spa-university"}
    assert all(
        item.connector_distance.distance_m == 0
        for item in destination_source.option_evidence
    )
    with pytest.raises(ValueError, match="fingerprint is stale"):
        replace(first, result_fingerprint="0" * 64)


def test_exact_governed_role_scope_offers_finite_candidate_actions(
    tmp_path: Path,
) -> None:
    _, source, compiled, population, education = _compiled_inputs(tmp_path)
    preparation = compiled.strategic_corridor_preparation
    assert preparation is not None

    result = compile_strategic_criteria_scenario(
        StrategicCriteriaScenarioInput(
            preparation=preparation,
            population_evidence=population,
            education_evidence=education,
            area_definition=source["boundary"],
            area_fingerprint=_area_fingerprint(source),
        )
    )

    assert result.status == "review-required"
    assert result.scenario is not None
    assert {
        item.disposition.value for item in result.scenario.selections
    } == {"provisional-review"}
    assert result.review_orchestration is not None
    assert all(
        any(
            option.action.value == "select-eligible-option"
            for option in state.request.options
        )
        for state in result.review_orchestration.actionable_requests
    )
    criteria_by_role = {
        item.unit_role: item.criteria for item in result.criteria
    }
    interurban = criteria_by_role[StrategicCorridorUnitRole.INTERURBAN_SPINE]
    destination = criteria_by_role[
        StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS
    ]
    assert not interurban.education.assessment.source_snapshot.schools
    assert not (
        interurban.education.assessment.source_snapshot
        .strategic_education_destinations
    )
    assert not destination.education.assessment.source_snapshot.schools
    assert tuple(
        item.strategic_destination_id
        for item in (
            destination.education.assessment.source_snapshot
            .strategic_education_destinations
        )
    ) == ("bath-spa-university",)
    assert all(
        item.state.value == "satisfied"
        for packet in result.criteria
        for item in packet.criteria.education.completeness
    )


def test_resealed_destination_criteria_cannot_erase_its_education_scope(
    tmp_path: Path,
) -> None:
    _, source, compiled, population, education = _compiled_inputs(tmp_path)
    preparation = compiled.strategic_corridor_preparation
    assert preparation is not None
    result = compile_strategic_criteria_scenario(
        StrategicCriteriaScenarioInput(
            preparation=preparation,
            population_evidence=population,
            education_evidence=education,
            area_definition=source["boundary"],
            area_fingerprint=_area_fingerprint(source),
        )
    )
    destination_packet = next(
        item
        for item in result.criteria
        if item.unit_role
        is StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS
    )
    destination = destination_packet.criteria
    assert isinstance(destination, CandidateCriteria)
    candidate_set = destination.education.candidate_set
    assert candidate_set.mandatory_strategic_destination_ids == (
        "bath-spa-university",
    )
    source_snapshot = destination.education.assessment.source_snapshot
    erased_assessment = assess_education_access(
        register_evidence=source_snapshot.register_evidence,
        schools=(),
        strategic_destinations=(),
        option_evidence=(),
        option_ids=source_snapshot.option_ids,
    )
    erased_content_sha256 = _canonical_sha256(
        erased_assessment.model_dump(mode="json")
    )
    full_source_sha256 = (
        destination.education.governed_binding
        .full_source_governed_fingerprint
    )
    erased_governed_input = governed_education_assessment_fingerprint(
        governed_source_fingerprint=full_source_sha256,
        school_ids=(),
        strategic_destination_ids=(),
        assessment_content_sha256=erased_content_sha256,
    )
    erased_governed_binding = GovernedEducationCriterionBinding(
        school_ids=(),
        strategic_destination_ids=(),
        full_source_governed_fingerprint=full_source_sha256,
        governed_input_fingerprint=erased_governed_input,
        assessment_content_sha256=erased_content_sha256,
    )
    erased_education_binding = GovernedAssessmentBinding(
        kind=AssessmentKind.EDUCATION_ACCESS,
        assessment_id=erased_assessment.assessment_id,
        assessment_content_sha256=erased_content_sha256,
        source_content_sha256=full_source_sha256,
        method_version="satn-governed-education-assessment-binding/v3",
    )
    erased_snapshot = GovernedEvidenceSnapshot(
        snapshot_id=destination.evidence_snapshot.snapshot_id,
        assessments=tuple(
            erased_education_binding
            if item.kind is AssessmentKind.EDUCATION_ACCESS
            else item
            for item in destination.evidence_snapshot.assessments
        ),
    )
    erased_population = destination.population.model_copy(
        update={
            "scenario_evidence_snapshot_fingerprint": (
                erased_snapshot.snapshot_fingerprint
            )
        }
    )
    erased_education = EducationCriterionSummary.from_assessment(
        erased_assessment,
        candidate_set=candidate_set,
        scenario_evidence_snapshot_fingerprint=(
            erased_snapshot.snapshot_fingerprint
        ),
        governed_binding=erased_governed_binding,
    )

    with pytest.raises(ValidationError, match="education scope"):
        CandidateCriteria(
            evidence_snapshot=erased_snapshot,
            population=erased_population,
            education=erased_education,
            existing_alignment=destination.existing_alignment,
            directness=destination.directness,
            gradient=destination.gradient,
            uncertainty=destination.uncertainty,
        )


def test_resealed_destination_criteria_cannot_substitute_foreign_source_with_same_ids(
    tmp_path: Path,
) -> None:
    _, source, compiled, population, education = _compiled_inputs(tmp_path)
    preparation = compiled.strategic_corridor_preparation
    assert preparation is not None
    result = compile_strategic_criteria_scenario(
        StrategicCriteriaScenarioInput(
            preparation=preparation,
            population_evidence=population,
            education_evidence=education,
            area_definition=source["boundary"],
            area_fingerprint=_area_fingerprint(source),
        )
    )
    destination_packet = next(
        item
        for item in result.criteria
        if item.unit_role
        is StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS
    )
    destination = destination_packet.criteria
    assert isinstance(destination, CandidateCriteria)
    governed = destination.education.governed_binding
    foreign_source_sha256 = "f" * 64
    foreign_input = governed_education_assessment_fingerprint(
        governed_source_fingerprint=foreign_source_sha256,
        school_ids=governed.school_ids,
        strategic_destination_ids=governed.strategic_destination_ids,
        assessment_content_sha256=governed.assessment_content_sha256,
    )
    foreign_binding = GovernedEducationCriterionBinding(
        school_ids=governed.school_ids,
        strategic_destination_ids=(
            governed.strategic_destination_ids
        ),
        full_source_governed_fingerprint=foreign_source_sha256,
        governed_input_fingerprint=foreign_input,
        assessment_content_sha256=governed.assessment_content_sha256,
    )
    foreign_summary = destination.education.model_copy(
        update={
            "governed_binding": foreign_binding,
        }
    )
    foreign_education_snapshot_binding = next(
        item
        for item in destination.evidence_snapshot.assessments
        if item.kind is AssessmentKind.EDUCATION_ACCESS
    ).model_copy(update={"source_content_sha256": foreign_source_sha256})
    foreign_snapshot = GovernedEvidenceSnapshot(
        snapshot_id=destination.evidence_snapshot.snapshot_id,
        assessments=tuple(
            foreign_education_snapshot_binding
            if item.kind is AssessmentKind.EDUCATION_ACCESS
            else item
            for item in destination.evidence_snapshot.assessments
        ),
    )
    foreign_summary = foreign_summary.model_copy(
        update={
            "scenario_evidence_snapshot_fingerprint": (
                foreign_snapshot.snapshot_fingerprint
            )
        }
    )
    foreign_criteria = CandidateCriteria(
        evidence_snapshot=foreign_snapshot,
        population=destination.population.model_copy(
            update={
                "scenario_evidence_snapshot_fingerprint": (
                    foreign_snapshot.snapshot_fingerprint
                )
            }
        ),
        education=foreign_summary,
        existing_alignment=destination.existing_alignment,
        directness=destination.directness,
        gradient=destination.gradient,
        uncertainty=destination.uncertainty,
    )

    with pytest.raises(ValueError, match="foreign to preparation lineage"):
        PreparedStrategicUnitCriteria(
            unit_id=destination_packet.unit_id,
            unit_role=destination_packet.unit_role,
            criteria=foreign_criteria,
            preparation_lineage=destination_packet.preparation_lineage,
        )


def test_resealed_destination_record_must_equal_preparation_source(
    tmp_path: Path,
) -> None:
    _, source, compiled, population, education = _compiled_inputs(tmp_path)
    preparation = compiled.strategic_corridor_preparation
    assert preparation is not None
    result = compile_strategic_criteria_scenario(
        StrategicCriteriaScenarioInput(
            preparation=preparation,
            population_evidence=population,
            education_evidence=education,
            area_definition=source["boundary"],
            area_fingerprint=_area_fingerprint(source),
        )
    )
    destination_packet = next(
        item
        for item in result.criteria
        if item.unit_role
        is StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS
    )
    destination = destination_packet.criteria
    assert isinstance(destination, CandidateCriteria)
    assessment_source = destination.education.assessment.source_snapshot
    original_destination = (
        assessment_source.strategic_education_destinations[0]
    )
    assert isinstance(
        original_destination,
        StrategicEducationDestination,
    )
    changed_destination = original_destination.model_copy(
        update={"name": "Foreign Bath Spa identity"}
    )
    changed_assessment = assess_education_access(
        register_evidence=assessment_source.register_evidence,
        schools=assessment_source.schools,
        strategic_destinations=(changed_destination,),
        option_evidence=assessment_source.option_evidence,
        option_ids=assessment_source.option_ids,
        supplementary_pct_evidence=(
            assessment_source.supplementary_pct_evidence
        ),
    )
    changed_content = _canonical_sha256(
        changed_assessment.model_dump(mode="json")
    )
    governed = destination.education.governed_binding
    changed_governed = GovernedEducationCriterionBinding(
        school_ids=governed.school_ids,
        strategic_destination_ids=governed.strategic_destination_ids,
        full_source_governed_fingerprint=(
            governed.full_source_governed_fingerprint
        ),
        governed_input_fingerprint=(
            governed_education_assessment_fingerprint(
                governed_source_fingerprint=(
                    governed.full_source_governed_fingerprint
                ),
                school_ids=governed.school_ids,
                strategic_destination_ids=(
                    governed.strategic_destination_ids
                ),
                assessment_content_sha256=changed_content,
            )
        ),
        assessment_content_sha256=changed_content,
    )
    changed_education_snapshot = next(
        binding
        for binding in destination.evidence_snapshot.assessments
        if binding.kind is AssessmentKind.EDUCATION_ACCESS
    ).model_copy(
        update={
            "assessment_id": changed_assessment.assessment_id,
            "assessment_content_sha256": changed_content,
        }
    )
    changed_snapshot = GovernedEvidenceSnapshot(
        snapshot_id=destination.evidence_snapshot.snapshot_id,
        assessments=tuple(
            changed_education_snapshot
            if binding.kind is AssessmentKind.EDUCATION_ACCESS
            else binding
            for binding in destination.evidence_snapshot.assessments
        ),
    )
    changed_summary = EducationCriterionSummary.from_assessment(
        changed_assessment,
        candidate_set=destination.education.candidate_set,
        scenario_evidence_snapshot_fingerprint=(
            changed_snapshot.snapshot_fingerprint
        ),
        governed_binding=changed_governed,
    )
    changed_criteria = CandidateCriteria(
        evidence_snapshot=changed_snapshot,
        population=destination.population.model_copy(
            update={
                "scenario_evidence_snapshot_fingerprint": (
                    changed_snapshot.snapshot_fingerprint
                )
            }
        ),
        education=changed_summary,
        existing_alignment=destination.existing_alignment,
        directness=destination.directness,
        gradient=destination.gradient,
        uncertainty=destination.uncertainty,
    )

    with pytest.raises(ValueError, match="prepared source lineage"):
        PreparedStrategicUnitCriteria(
            unit_id=destination_packet.unit_id,
            unit_role=destination_packet.unit_role,
            criteria=changed_criteria,
            preparation_lineage=destination_packet.preparation_lineage,
        )


def test_strategic_packet_deeply_revalidates_nested_criteria(
    tmp_path: Path,
) -> None:
    _, source, compiled, population, education = _compiled_inputs(tmp_path)
    preparation = compiled.strategic_corridor_preparation
    assert preparation is not None
    result = compile_strategic_criteria_scenario(
        StrategicCriteriaScenarioInput(
            preparation=preparation,
            population_evidence=population,
            education_evidence=education,
            area_definition=source["boundary"],
            area_fingerprint=_area_fingerprint(source),
        )
    )
    packet = next(
        item
        for item in result.criteria
        if isinstance(item.criteria, CandidateCriteria)
    )
    stale = packet.criteria.model_copy(
        update={"criteria_fingerprint": "0" * 64}
    )

    with pytest.raises(ValueError, match="CandidateCriteria is stale"):
        PreparedStrategicUnitCriteria(
            unit_id=packet.unit_id,
            unit_role=packet.unit_role,
            criteria=stale,
            preparation_lineage=packet.preparation_lineage,
        )


def test_missing_campus_binding_cannot_compile_or_be_adopted(tmp_path: Path) -> None:
    config, source, compiled, population, education = _compiled_inputs(tmp_path)
    context = source["context"].drop(columns=["access_point_evidence_ids"])
    preparation = prepare_strategic_corridors(
        config.compilation.network_selection,
        road_graph=RoadGraph(mark_ncn_edges(source["network"], source["context"])),
        spine_access_connections=compiled.spine_access_connections,
        context=context,
        source_config=config.source,
        config_directory=config.config_path.parent,
    )

    result = compile_strategic_criteria_scenario(
        StrategicCriteriaScenarioInput(
            preparation=preparation,
            population_evidence=population,
            education_evidence=education,
            area_definition=source["boundary"],
            area_fingerprint=_area_fingerprint(source),
        )
    )

    assert result.status == "incomplete"
    assert result.scenario is None
    assert result.review_orchestration is None
    assert result.human_adoption_required
    assert not result.reference_satn_created
    assert "strategic-corridor-preparation-not-ready" in result.missing_inputs
