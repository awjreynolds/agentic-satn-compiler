"""Regression coverage for the sibling strategic-corridor preparation seam."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from bath_saltford_fixture import configured_bath_saltford

from satn.agents import FakeAgentRuntime
from satn.compiler import compile_network
from satn.evidence import mark_ncn_edges
from satn.population_reach import compile_population_reach
from satn.psa_evidence_loaders import load_population_reach_evidence
from satn.routing import RoadGraph
from satn.sources import load_snapshot, snapshot
from satn.strategic_corridors import (
    StrategicCorridorUnitRole,
    prepare_strategic_corridors,
)


def _compiled(tmp_path: Path):
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    source = load_snapshot(config)
    return config, source, compile_network(config, source, FakeAgentRuntime())


def test_bath_prepares_separate_interurban_and_destination_units(tmp_path: Path) -> None:
    config, source, compiled = _compiled(tmp_path)

    # Existing Spine Access deliberately keeps direct-to-spine rows out of its
    # contract.  The sibling preparation is the only strategic promotion seam.
    legacy = compiled.spine_access_candidate_preparation
    assert legacy is not None
    assert not legacy.prepared_spine_access_connections
    assert {
        row.disposition for row in legacy.connection_roster
    } == {"out-of-scope-direct-strategic-spine"}

    prepared = compiled.strategic_corridor_preparation
    assert prepared is not None and prepared.prepared
    assert compiled.network_selection_preparation is not None
    assert len(compiled.network_selection_preparation.alignment_units) == 2
    units = {item.unit_role: item for item in prepared.units}
    assert set(units) == {
        StrategicCorridorUnitRole.INTERURBAN_SPINE,
        StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS,
    }

    interurban = units[StrategicCorridorUnitRole.INTERURBAN_SPINE]
    assert interurban.candidate_set.endpoints == ("bath-edge", "saltford")
    assert {
        candidate.source_class.value for candidate in interurban.candidate_set.admitted_candidates
    } == {"verified-existing-asset", "a-road-corridor"}
    assert {record.route_role for record in interurban.candidate_records} >= {
        "ncn-informed",
        "strategic-spine",
    }
    for record in interurban.candidate_records:
        assert record.routing_start_node_id == interurban.routing_start_node_id
        assert record.routing_end_node_id == interurban.routing_end_node_id
        assert record.routing_edge_ids and record.reverse_routing_edge_ids
        assert record.candidate.geometry_fingerprint
        assert record.source_ids and record.evidence_ids

    destination = units[StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS]
    assert destination.strategic_destination_id == "bath-spa-university"
    assert destination.site_id == "bath-spa-university"
    assert destination.access_point_evidence_ids == (
        "bath-spa-university-synthetic-entrance",
    )
    assert destination.candidate_set.mandatory_strategic_destination_ids == (
        "bath-spa-university",
    )
    assert not destination.candidate_set.mandatory_access_obligation_ids
    assert all(
        candidate.served_strategic_destination_ids == ("bath-spa-university",)
        for candidate in destination.candidate_set.candidates
    )
    assert {candidate.source_class.value for candidate in destination.candidate_set.candidates} == {
        "a-road-corridor"
    }

    physical_ids = [item.physical_alignment_id for item in prepared.physical_alignments]
    assert physical_ids == sorted(set(physical_ids))
    assert all(
        len(item.candidate_ids) == len(set(item.candidate_ids))
        for item in prepared.physical_alignments
    )

    # Population evidence remains a corridor measure.  Candidate geometry is
    # passed straight to the governed calculator; this asserts both declared
    # distances without inventing any access or demand claim.
    evidence = load_population_reach_evidence(
        config.source.population_reach_evidence,
        base_directory=config.config_path.parent,
        pwc_outside_tolerance_m=0,
    )
    assert evidence is not None
    routes = gpd.GeoDataFrame(
        {
            "option_id": [
                item.candidate_id
                for item in interurban.candidate_set.admitted_candidates
            ],
            "geometry": [
                item.geometry.as_shapely()
                for item in interurban.candidate_set.admitted_candidates
            ],
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    reach = compile_population_reach(
        routes,
        evidence.output_areas,
        source["boundary"],
        source=evidence.source,
        columns=evidence.columns,
    )
    assert {item.corridor_distance_m for item in reach.summaries} == {500.0, 1000.0}


def test_missing_governed_destination_geometry_is_an_explicit_incomplete_issue(
    tmp_path: Path,
) -> None:
    config, source, _compiled_network = _compiled(tmp_path)
    context = source["context"].drop(columns=["access_point_evidence_ids"])

    result = prepare_strategic_corridors(
        config.compilation.network_selection,
        road_graph=RoadGraph(mark_ncn_edges(source["network"], source["context"])),
        spine_access_connections=_compiled_network.spine_access_connections,
        context=context,
        source_config=config.source,
        config_directory=config.config_path.parent,
    )

    assert result.status == "incomplete"
    assert not [
        unit
        for unit in result.units
        if unit.unit_role is StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS
    ]
    assert [issue.reason for issue in result.issues] == [
        "destination-site-or-access-geometry-missing-or-mismatched"
    ]


def test_strategic_preparation_is_deterministic_and_makes_no_delivery_claims(
    tmp_path: Path,
) -> None:
    config, source, compiled = _compiled(tmp_path)
    first = compiled.strategic_corridor_preparation
    assert first is not None
    repeated = prepare_strategic_corridors(
        config.compilation.network_selection,
        road_graph=RoadGraph(mark_ncn_edges(source["network"], source["context"])),
        spine_access_connections=compiled.spine_access_connections,
        context=source["context"],
        source_config=config.source,
        config_directory=config.config_path.parent,
    )

    assert repeated.preparation_fingerprint == first.preparation_fingerprint
    metadata = repeated.metadata()
    assert metadata["selection_performed"] is False
    assert metadata["network_geometry_mutated"] is False
    assert metadata["publication_performed"] is False
