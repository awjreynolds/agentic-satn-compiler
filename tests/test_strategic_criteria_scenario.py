"""Bath-Saltford proof for the strategic criteria-and-Scenario safety seam."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from bath_saltford_fixture import configured_bath_saltford

from satn.agents import FakeAgentRuntime
from satn.compiler import compile_network
from satn.evidence import mark_ncn_edges
from satn.psa_evidence_loaders import (
    load_education_access_evidence,
    load_population_reach_evidence,
)
from satn.routing import RoadGraph
from satn.sources import load_snapshot, snapshot
from satn.strategic_corridors import prepare_strategic_corridors
from satn.strategic_criteria_scenario import (
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
