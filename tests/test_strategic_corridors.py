"""Regression coverage for the sibling strategic-corridor preparation seam."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
from bath_saltford_fixture import configured_bath_saltford
from shapely.affinity import translate
from shapely.geometry import LineString, Point

from satn.agents import FakeAgentRuntime
from satn.compiler import compile_network, governed_input_binding
from satn.evidence import mark_ncn_edges
from satn.network_selection import NetworkSelectionProfile
from satn.parallel_reduction import PreloadedOfficerDecision
from satn.population_reach import compile_population_reach
from satn.psa_evidence_loaders import load_population_reach_evidence
from satn.routing import RoadGraph, RouteOption
from satn.sources import load_snapshot, snapshot
from satn.strategic_corridors import (
    StrategicCorridorUnitRole,
    _a_road_backbone_units,
    _bound_backbone_node,
    _candidate_set,
    _fingerprint,
    _official_a_road_chains,
    _provenance_id,
    prepare_strategic_corridors,
    strategic_routable_network_with_a_road_backbone,
)


def _compiled(tmp_path: Path):
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    source = load_snapshot(config)
    return config, source, compile_network(config, source, FakeAgentRuntime())


def test_compound_external_edge_ids_have_stable_canonical_provenance_ids() -> None:
    assert _provenance_id("source-edge-1") == "source-edge-1"
    assert _provenance_id("[1001848710, 33175860]") == ("source-reference-4cea4d0a166e52d52240")
    assert _provenance_id("[1001848710, 33175860]") == _provenance_id("[1001848710, 33175860]")


def test_a_road_backbone_overlay_splits_interior_junctions_and_retains_exact_chain() -> None:
    source = gpd.GeoDataFrame(
        [
            {
                "source_id": "source-edge",
                "u": "source-start",
                "v": "source-end",
                "oneway": False,
                "geometry": LineString([(0.0, 0.0), (100.0, 0.0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "official-a1",
                "official_classification": "a-road",
                "official_road_number": "A1",
                "geometry": LineString([(25.0, 0.0), (75.0, 0.0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )

    overlay = strategic_routable_network_with_a_road_backbone(source, official)
    overlay_ids = tuple(str(item) for item in overlay["source_id"])

    assert any(item.startswith("a-road-backbone:") for item in overlay_ids)
    assert any(item.startswith("a-road-attachment:") for item in overlay_ids)
    assert len(overlay) > len(source)

    graph = RoadGraph(overlay)
    start = "xy:25.0000000:0.0000000"
    end = "xy:75.0000000:0.0000000"
    option = graph.option(start, end, "strategic-spine", strategic_use=True)
    assert option is not None
    assert list(option.geometry.coords) == [(25.0, 0.0), (50.0, 0.0), (75.0, 0.0)]
    backbone_rows = overlay[overlay["source_id"].astype(str).str.startswith("a-road-backbone:")]
    for _, row in backbone_rows.iterrows():
        assert Point(row.geometry.coords[0]).equals_exact(graph.node_points[str(row["u"])], 1e-9)
        assert Point(row.geometry.coords[-1]).equals_exact(graph.node_points[str(row["v"])], 1e-9)


def test_short_backbone_chain_binds_distinct_exact_overlay_endpoints() -> None:
    source = gpd.GeoDataFrame(
        [
            {
                "source_id": "source-edge",
                "u": "source-start",
                "v": "source-end",
                "oneway": False,
                "geometry": LineString([(0.0, 0.0), (100.0, 0.0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "official-short-a1",
                "official_classification": "a-road",
                "official_road_number": "A1",
                "geometry": LineString([(48.1, 0.0), (51.9, 0.0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )

    graph = RoadGraph(strategic_routable_network_with_a_road_backbone(source, official))

    start = _bound_backbone_node(graph, Point(48.1, 0.0))
    end = _bound_backbone_node(graph, Point(51.9, 0.0))

    assert start == "xy:48.1000000:0.0000000"
    assert end == "xy:51.9000000:0.0000000"
    assert start != end


def test_backbone_candidate_set_keeps_cycle_substitute_on_the_same_endpoints() -> None:
    source = gpd.GeoDataFrame(
        [
            {
                "source_id": "a-road",
                "u": "start",
                "v": "end",
                "oneway": False,
                "highway": "primary",
                "ref": "A1",
                "geometry": LineString([(0.0, 0.0), (100.0, 0.0)]),
            },
            {
                "source_id": "ncn-west",
                "u": "start",
                "v": "mid",
                "oneway": False,
                "highway": "cycleway",
                "satn_ncn": True,
                "geometry": LineString([(0.0, 0.0), (50.0, 60.0)]),
            },
            {
                "source_id": "ncn-east",
                "u": "mid",
                "v": "end",
                "oneway": False,
                "highway": "cycleway",
                "satn_ncn": True,
                "geometry": LineString([(50.0, 60.0), (100.0, 0.0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "official-a1",
                "official_classification": "a-road",
                "official_road_number": "A1",
                "geometry": LineString([(0.0, 0.0), (100.0, 0.0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    overlay = strategic_routable_network_with_a_road_backbone(source, official)
    profile = NetworkSelectionProfile.model_validate(
        {
            "profile_id": "backbone-fixture",
            "candidate_source_precedence": [
                "verified-existing-asset",
                "a-road-corridor",
                "other-routable",
            ],
        }
    )
    units, issues = _a_road_backbone_units(
        profile,
        RoadGraph(overlay),
        official,
        None,
        gpd.GeoDataFrame([], geometry=[], crs=27700),
        {},
    )

    assert not issues
    assert len(units) == 1
    unit = units[0]
    assert unit.endpoint_binding.routing_node_ids == ("start", "end")
    assert {candidate.source_class.value for candidate in unit.candidate_set.candidates} == {
        "a-road-corridor",
        "verified-existing-asset",
    }


def test_backbone_candidates_must_physically_meet_exact_fallback_endpoints() -> None:
    source = gpd.GeoDataFrame(
        [
            {
                "source_id": "official-a1",
                "u": "start",
                "v": "end",
                "oneway": False,
                "highway": "primary",
                "ref": "A1",
                "geometry": LineString([(0.0, 0.0), (100.0, 0.0)]),
            },
            {
                "source_id": "official-a1-reverse",
                "u": "end",
                "v": "start",
                "oneway": False,
                "highway": "primary",
                "ref": "A1",
                "geometry": LineString([(100.0, 0.0), (0.0, 0.0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    graph = RoadGraph(source)
    exact = graph.option("start", "end", "strategic-spine", strategic_use=True)
    assert exact is not None

    def option(name: str, geometry: LineString, *, ncn_share: float) -> RouteOption:
        return RouteOption(
            role=name,
            geometry=geometry,
            length_km=geometry.length / 1000,
            edge_ids=[name],
            a_road_share=0.0,
            ncn_share=ncn_share,
            bidirectional=True,
            reverse_length_km=geometry.length / 1000,
            reverse_edge_ids=[f"{name}-reverse"],
            reverse_corridor_share=ncn_share,
            impracticable_alongside=False,
        )

    bad = option(
        "bad-offset-cycleway",
        LineString([(2.0, 0.0), (50.0, 20.0), (102.0, 0.0)]),
        ncn_share=1.0,
    )
    valid = option(
        "valid-cycleway",
        LineString([(0.0, 0.0), (50.0, 20.0), (100.0, 0.0)]),
        ncn_share=1.0,
    )
    profile = NetworkSelectionProfile.model_validate(
        {
            "profile_id": "backbone-endpoint-fixture",
            "candidate_source_precedence": [
                "verified-existing-asset",
                "a-road-corridor",
                "other-routable",
            ],
        }
    )

    candidate_set, records = _candidate_set(
        profile,
        graph,
        unit_role=StrategicCorridorUnitRole.A_ROAD_BACKBONE,
        endpoints=("official-start", "official-end"),
        mandatory_network_place_ids=(),
        start_node="start",
        end_node="end",
        source_ids=("official-a1",),
        evidence_ids=("official-a1",),
        context=gpd.GeoDataFrame([], geometry=[], crs=27700),
        strategic_destination_id=None,
        precomputed_options={
            "direct": bad,
            "strategic-spine": valid,
            "ncn-informed": valid,
            "low-traffic": valid,
        },
        exact_backbone_option=exact,
    )

    assert candidate_set.candidates
    assert len(records) == len(candidate_set.candidates)
    assert all(
        candidate.geometry.as_shapely().coords[0]
        in {
            (0.0, 0.0),
            (100.0, 0.0),
        }
        for candidate in candidate_set.candidates
    )
    assert all("bad-offset-cycleway" not in record.routing_edge_ids for record in records)
    assert any("valid-cycleway" in record.routing_edge_ids for record in records)
    assert any("official-a1" in record.routing_edge_ids for record in records)


def test_backbone_exact_overlay_fallback_survives_structural_junction_filter() -> None:
    source = gpd.GeoDataFrame(
        [
            {
                "source_id": "cycle-ab",
                "u": "a",
                "v": "b",
                "oneway": False,
                "highway": "cycleway",
                "satn_ncn": True,
                "geometry": LineString([(0.0, 0.0), (50.0, 0.0)]),
            },
            {
                "source_id": "cycle-bc",
                "u": "b",
                "v": "c",
                "oneway": False,
                "highway": "cycleway",
                "satn_ncn": True,
                "geometry": LineString([(50.0, 0.0), (100.0, 0.0)]),
            },
            {
                "source_id": "road-bd",
                "u": "b",
                "v": "d",
                "oneway": False,
                "highway": "primary",
                "ref": "A2",
                "geometry": LineString([(50.0, 0.0), (50.0, -10.0)]),
            },
            {
                "source_id": "shortcut-ac",
                "u": "a",
                "v": "c",
                "oneway": False,
                "highway": "cycleway",
                "satn_ncn": True,
                "geometry": LineString([(0.0, 0.0), (100.0, 0.0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "official-long-a1",
                "official_classification": "a-road",
                "official_road_number": "A1",
                "geometry": LineString([(0.0, 0.0), (0.0, 500.0), (100.0, 500.0), (100.0, 0.0)]),
            },
            {
                "official_feature_id": "official-a2",
                "official_classification": "a-road",
                "official_road_number": "A2",
                "geometry": LineString([(50.0, 0.0), (50.0, -10.0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    overlay = strategic_routable_network_with_a_road_backbone(source, official)
    graph = RoadGraph(overlay)
    profile = NetworkSelectionProfile.model_validate(
        {
            "profile_id": "backbone-fallback-fixture",
            "candidate_source_precedence": [
                "verified-existing-asset",
                "a-road-corridor",
                "other-routable",
            ],
        }
    )

    units, issues = _a_road_backbone_units(
        profile,
        graph,
        official,
        None,
        gpd.GeoDataFrame([], geometry=[], crs=27700),
        {},
    )

    a1_chain = next(
        chain for chain in _official_a_road_chains(official) if chain["road_number"] == "A1"
    )
    a1_unit = next(unit for unit in units if unit.unit_id == a1_chain["chain_id"])
    assert any(issue.reason == "a-road-backbone-component-unconnected" for issue in issues)
    assert a1_unit.candidate_set.candidates
    assert any(
        any(edge_id.startswith("a-road-backbone:") for edge_id in record.routing_edge_ids)
        for record in a1_unit.candidate_records
    )
    exact_record = next(
        record
        for record in a1_unit.candidate_records
        if any(edge_id.startswith("a-road-backbone:") for edge_id in record.routing_edge_ids)
    )
    assert exact_record.candidate.geometry.as_shapely().wkt == official.loc[0, "geometry"].wkt
    assert all(edge_id.startswith("a-road-backbone:") for edge_id in exact_record.routing_edge_ids)


def test_unbound_a_road_backbone_remains_an_exact_proposal_without_attachment() -> None:
    source = gpd.GeoDataFrame(
        [
            {
                "source_id": "source-edge",
                "u": "source-start",
                "v": "source-end",
                "oneway": False,
                "geometry": LineString([(0.0, 0.0), (100.0, 0.0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "official-a1",
                "official_classification": "a-road",
                "official_road_number": "A1",
                "geometry": LineString([(25.0, 100.0), (75.0, 100.0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )

    overlay = strategic_routable_network_with_a_road_backbone(source, official)
    overlay_ids = tuple(str(item) for item in overlay["source_id"])

    assert len(overlay) == len(source) + 4
    assert sum(item.startswith("a-road-backbone:") for item in overlay_ids) == 4
    assert not any(item.startswith("a-road-attachment:") for item in overlay_ids)


def test_disconnected_degree_two_a_road_loop_is_retained_per_official_link() -> None:
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "loop-1",
                "official_classification": "a-road",
                "official_road_number": "A1",
                "geometry": LineString([(0.0, 0.0), (10.0, 0.0)]),
            },
            {
                "official_feature_id": "loop-2",
                "official_classification": "a-road",
                "official_road_number": "A1",
                "geometry": LineString([(10.0, 0.0), (5.0, 10.0)]),
            },
            {
                "official_feature_id": "loop-3",
                "official_classification": "a-road",
                "official_road_number": "A1",
                "geometry": LineString([(5.0, 10.0), (0.0, 0.0)]),
            },
            {
                "official_feature_id": "separate-link",
                "official_classification": "a-road",
                "official_road_number": "A2",
                "geometry": LineString([(100.0, 0.0), (110.0, 0.0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )

    chains = _official_a_road_chains(official)

    assert len(chains) == 4
    assert sum(len(chain["source_ids"]) for chain in chains) == 4


def test_non_motorway_junction_context_joins_a_road_components_without_a_classification() -> None:
    source = gpd.GeoDataFrame(
        [
            {
                "source_id": "a1-source",
                "u": "a1-start",
                "v": "a1-end",
                "oneway": False,
                "geometry": LineString([(0.0, 0.0), (10.0, 0.0)]),
            },
            {
                "source_id": "a2-source",
                "u": "a2-start",
                "v": "a2-end",
                "oneway": False,
                "geometry": LineString([(20.0, 0.0), (30.0, 0.0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "a1",
                "official_classification": "a-road",
                "official_road_number": "A1",
                "official_road_function": "A Road",
                "geometry": LineString([(0.0, 0.0), (10.0, 0.0)]),
            },
            {
                "official_feature_id": "a2",
                "official_classification": "a-road",
                "official_road_number": "A2",
                "official_road_function": "A Road",
                "geometry": LineString([(20.0, 0.0), (30.0, 0.0)]),
            },
            {
                "official_feature_id": "junction-local",
                "official_classification": "unclassified",
                "official_road_function": "Local Access Road",
                "geometry": LineString([(10.0, 0.0), (20.0, 0.0)]),
            },
            {
                "official_feature_id": "junction-restricted",
                "official_classification": "unknown",
                "official_road_function": "Restricted Local Access Road",
                "geometry": LineString([(20.0, 0.0), (10.0, 0.0)]),
            },
            {
                "official_feature_id": "same-component",
                "official_classification": "unclassified",
                "official_road_function": "Local Road",
                "geometry": LineString([(0.0, 0.0), (10.0, 0.0)]),
            },
            {
                "official_feature_id": "motorway-junction",
                "official_classification": "unknown",
                "official_road_function": "Motorway",
                "geometry": LineString([(10.0, 0.0), (20.0, 0.0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )

    chains = _official_a_road_chains(official)
    context = [chain for chain in chains if chain["official_classification"] != "a-road"]

    assert {chain["source_ids"] for chain in context} == {
        ("junction-local",),
        ("junction-restricted",),
    }
    assert all(chain["component_ids"] == tuple(sorted(chain["component_ids"])) for chain in context)
    assert all(chain["road_number"] is None for chain in context)

    overlay = strategic_routable_network_with_a_road_backbone(source, official)
    context_rows = overlay[
        overlay.get("official_classification", pd.Series(index=overlay.index)).ne("a-road")
        & overlay["source_id"].astype(str).str.startswith("a-road-backbone:")
    ]
    assert len(context_rows) == 8
    assert set(context_rows["official_classification"]) == {"unclassified", "unknown"}
    assert set(context_rows["highway"]) == {"unclassified"}
    assert context_rows["ref"].isna().all()


def test_disconnected_backbone_component_issue_is_located_at_official_endpoints() -> None:
    source = gpd.GeoDataFrame(
        [
            {
                "source_id": "a1-source",
                "u": "a1-start",
                "v": "a1-end",
                "oneway": False,
                "geometry": LineString([(0.0, 0.0), (10.0, 0.0)]),
            },
            {
                "source_id": "a2-source",
                "u": "a2-start",
                "v": "a2-end",
                "oneway": False,
                "geometry": LineString([(100.0, 0.0), (110.0, 0.0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "a1",
                "official_classification": "a-road",
                "official_road_number": "A1",
                "geometry": LineString([(0.0, 0.0), (10.0, 0.0)]),
            },
            {
                "official_feature_id": "a2",
                "official_classification": "a-road",
                "official_road_number": "A2",
                "geometry": LineString([(100.0, 0.0), (110.0, 0.0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    profile = NetworkSelectionProfile.model_validate(
        {
            "profile_id": "backbone-component-gap-fixture",
            "candidate_source_precedence": [
                "verified-existing-asset",
                "a-road-corridor",
                "other-routable",
            ],
        }
    )
    units, issues = _a_road_backbone_units(
        profile,
        RoadGraph(strategic_routable_network_with_a_road_backbone(source, official)),
        official,
        None,
        gpd.GeoDataFrame([], geometry=[], crs=27700),
        {},
    )

    component_issues = [
        issue for issue in issues if issue.reason == "a-road-backbone-component-unconnected"
    ]
    assert len(units) == 2
    assert len(component_issues) == 1
    issue = component_issues[0]
    assert issue.unit_role is StrategicCorridorUnitRole.A_ROAD_BACKBONE
    assert len(issue.endpoint_coordinates) == 2
    official_endpoints = {
        coordinate
        for geometry in official.geometry
        for coordinate in (geometry.coords[0], geometry.coords[-1])
    }
    assert all(coordinate in official_endpoints for coordinate in issue.endpoint_coordinates)


def test_bath_prepares_separate_interurban_and_destination_units(tmp_path: Path) -> None:
    config, source, compiled = _compiled(tmp_path)

    # Existing Spine Access deliberately keeps direct-to-spine rows out of its
    # contract.  The sibling preparation is the only strategic promotion seam.
    legacy = compiled.spine_access_candidate_preparation
    assert legacy is not None
    assert not legacy.prepared_spine_access_connections
    assert not legacy.connection_roster
    saltford_obligation = compiled.access_obligations.set_index("place_id").loc["saltford"]
    assert saltford_obligation["service_status"] == "served"
    assert saltford_obligation["access_connection_id"] is None
    assert not (compiled.spine_access_connections["place_id"] == "saltford").any()

    prepared = compiled.strategic_corridor_preparation
    assert prepared is not None and prepared.prepared
    strategic = compiled.strategic_network_planning
    assert strategic is not None
    assert strategic.status == "complete"
    assert {item.network_role for item in strategic.selections} == {
        "interurban-spine",
        "strategic-destination-access",
    }
    interurban_selection = next(
        item for item in strategic.selections if item.network_role == "interurban-spine"
    )
    assert interurban_selection.authority.value == "compiler"
    selected = next(
        candidate
        for candidate_set in strategic.candidate_sets
        for candidate in candidate_set.candidates
        if candidate.candidate_id == interurban_selection.effective_candidate_id
    )
    assert selected.source_class.value == "verified-existing-asset"
    assert compiled.network_selection_preparation is not None
    assert len(compiled.network_selection_preparation.alignment_units) == 2
    units = {item.unit_role: item for item in prepared.units}
    assert set(units) == {
        StrategicCorridorUnitRole.INTERURBAN_SPINE,
        StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS,
    }

    interurban = units[StrategicCorridorUnitRole.INTERURBAN_SPINE]
    assert not interurban.anchor_connection_ids
    assert set(interurban.anchor_obligation_ids) == set(
        compiled.access_obligations.loc[
            compiled.access_obligations["place_id"].isin({"bath-edge", "saltford"}),
            "obligation_id",
        ]
    )
    assert interurban.candidate_set.endpoints == ("bath-edge", "saltford")
    assert {
        candidate.source_class.value for candidate in interurban.candidate_set.admitted_candidates
    } == {"verified-existing-asset", "a-road-corridor"}
    assert len(interurban.candidate_set.candidates) == 2
    assert interurban.candidate_set.mandatory_network_place_ids == (
        "bath-edge",
        "saltford",
    )
    assert not interurban.candidate_set.mandatory_strategic_destination_ids
    assert interurban.endpoint_binding.network_place_ids == ("bath-edge", "saltford")
    assert not interurban.endpoint_binding.strategic_destination_ids
    assert {
        strategy
        for record in interurban.candidate_records
        for strategy in record.generation_strategies
    } >= {"ncn-informed", "strategic-spine"}
    for record in interurban.candidate_records:
        assert record.routing_start_node_id == interurban.routing_start_node_id
        assert record.routing_end_node_id == interurban.routing_end_node_id
        assert record.routing_edge_ids and record.reverse_routing_edge_ids
        assert record.candidate.geometry_fingerprint
        assert record.source_ids and record.evidence_ids

    destination = units[StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS]
    assert destination.strategic_destination_id == "bath-spa-university"
    assert destination.site_id == "bath-spa-university"
    assert destination.access_point_evidence_ids == ("bath-spa-university-synthetic-entrance",)
    assert destination.candidate_set.mandatory_strategic_destination_ids == ("bath-spa-university",)
    assert not destination.candidate_set.mandatory_network_place_ids
    assert not destination.candidate_set.mandatory_access_obligation_ids
    assert destination.endpoint_binding.network_place_ids == ("bath-edge",)
    assert destination.endpoint_binding.strategic_destination_ids == ("bath-spa-university",)
    assert destination.endpoint_binding.candidate_endpoints == (
        "bath-edge",
        "destination-endpoint-7d2d19a5e22cba8479fe",
    )
    assert all(
        candidate.served_strategic_destination_ids == ("bath-spa-university",)
        for candidate in destination.candidate_set.candidates
    )
    assert all(
        not candidate.served_network_place_ids for candidate in destination.candidate_set.candidates
    )
    assert len(destination.candidate_set.candidates) == 1
    assert {candidate.source_class.value for candidate in destination.candidate_set.candidates} == {
        "a-road-corridor"
    }

    destination_record = destination.candidate_records[0]
    assert destination_record.routing_edge_ids == ("a4-campus-forward",)
    assert destination_record.reverse_routing_edge_ids == ("a4-campus-reverse",)
    assert destination_record.generation_strategies == (
        "direct",
        "low-traffic",
        "ncn-informed",
        "strategic-spine",
    )

    physical_ids = [item.physical_alignment_id for item in prepared.physical_alignments]
    assert physical_ids == sorted(set(physical_ids))
    assert len(prepared.physical_alignments) == 3
    assert sum(len(item.candidate_set.candidates) for item in prepared.units) == 3
    assert all(
        len(item.candidate_ids) == len(set(item.candidate_ids))
        for item in prepared.physical_alignments
    )
    assert all(
        item.geometry.fingerprint == item.geometry_fingerprint
        for item in prepared.physical_alignments
    )
    section_population = prepared.section_population
    assert section_population is not None
    assert section_population.profile.display_section_length_m == 100
    assert section_population.profile.urban_capture_radius_m == 250
    assert section_population.profile.rural_capture_radius_m == 750
    assert section_population.sections
    assert len(compiled.population_display_sections) == len(section_population.sections)
    assert set(compiled.population_display_sections["network_scope"]) <= {
        "urban",
        "rural",
    }
    assert (
        compiled.population_display_sections["total_residents"]
        == compiled.population_display_sections["inside_area_residents"]
        + compiled.population_display_sections["outside_area_residents"]
    ).all()
    payload = prepared.canonical_payload()
    assert payload["material_population_differences"] == [
        item.canonical() for item in prepared.material_population_differences
    ]
    assert prepared.preparation_fingerprint == _fingerprint(payload)

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
                item.candidate_id for item in interurban.candidate_set.admitted_candidates
            ],
            "geometry": [
                item.geometry.as_shapely() for item in interurban.candidate_set.admitted_candidates
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


def test_compiler_carries_governed_urban_spine_into_effective_network(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    source = load_snapshot(config)
    urban_geometry = LineString([(-2.39, 51.37), (-2.365, 51.425)])
    urban_place = gpd.GeoDataFrame(
        [
            {
                "place_id": "bristol-urban-fixture",
                "name": "Bristol urban fixture",
                "kind": "community",
                "population": 100_000,
                "place_class": "town",
                "source_id": "bristol-urban-fixture",
                "geometry": Point(-2.365, 51.425),
            }
        ],
        geometry="geometry",
        crs=source["places"].crs,
    )
    source["places"] = gpd.GeoDataFrame(
        pd.concat([source["places"], urban_place], ignore_index=True, sort=False),
        geometry="geometry",
        crs=source["places"].crs,
    )
    urban_edges = gpd.GeoDataFrame(
        [
            {
                "source_id": "urban-a4-forward",
                "u": "a4-root",
                "v": "bristol-urban-fixture",
                "highway": "primary",
                "ref": "A4",
                "geometry": urban_geometry,
            },
            {
                "source_id": "urban-a4-reverse",
                "u": "bristol-urban-fixture",
                "v": "a4-root",
                "highway": "primary",
                "ref": "A4",
                "geometry": urban_geometry.reverse(),
            },
        ],
        geometry="geometry",
        crs=source["network"].crs,
    )
    source["network"] = gpd.GeoDataFrame(
        pd.concat([source["network"], urban_edges], ignore_index=True, sort=False),
        geometry="geometry",
        crs=source["network"].crs,
    )
    source["official_road_classification"] = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "official-urban-a4",
                "official_classification": "a-road",
                "official_road_number": "A4",
                "source_id": "synthetic-official-roads",
                "effective_date": "2026-08-19",
                "licence": "Synthetic fixture",
                "content_fingerprint": "a" * 64,
                "geometry": urban_geometry,
            }
        ],
        geometry="geometry",
        crs=source["network"].crs,
    )

    compiled = compile_network(config, source, FakeAgentRuntime())

    assert not compiled.urban_spines.empty
    strategic = compiled.strategic_network_planning
    assert strategic is not None
    urban_sections = [
        section
        for section in strategic.effective_network.sections
        if section.network_role == "urban-main-road-spine"
    ]
    assert urban_sections
    assert all(section.routing_edge_ids for section in urban_sections)
    assert all(section.primary_alignment_basis == "a-road" for section in urban_sections)


def test_preloaded_officer_route_is_applied_and_divergence_remains_visible(
    tmp_path: Path,
) -> None:
    config, source, baseline = _compiled(tmp_path)
    prepared = baseline.strategic_corridor_preparation
    assert prepared is not None
    interurban = next(
        item
        for item in prepared.units
        if item.unit_role is StrategicCorridorUnitRole.INTERURBAN_SPINE
    )
    road = next(
        item
        for item in interurban.candidate_records
        if item.candidate.source_class.value == "a-road-corridor"
    )
    decision = PreloadedOfficerDecision(
        target_id=interurban.unit_id,
        route_id=road.physical_alignment_id,
    )

    with governed_input_binding(officer_decisions=(decision,)):
        compiled = compile_network(config, source, FakeAgentRuntime())

    strategic = compiled.strategic_network_planning
    assert strategic is not None
    selection = next(
        item for item in strategic.selections if item.obligation_id == interurban.unit_id
    )
    assert selection.authority.value == "officer"
    selected_candidate = next(
        candidate
        for candidate_set in strategic.candidate_sets
        for candidate in candidate_set.candidates
        if candidate.candidate_id == selection.effective_candidate_id
    )
    assert selected_candidate.geometry_fingerprint == road.candidate.geometry_fingerprint
    assert strategic.divergences[0].officer_candidate_id == selected_candidate.candidate_id


def test_missing_governed_destination_geometry_is_an_explicit_incomplete_issue(
    tmp_path: Path,
) -> None:
    config, source, _compiled_network = _compiled(tmp_path)
    context = source["context"].drop(columns=["access_point_evidence_ids"])

    result = prepare_strategic_corridors(
        config.compilation.network_selection,
        road_graph=RoadGraph(mark_ncn_edges(source["network"], source["context"])),
        spine_access_connections=_compiled_network.spine_access_connections,
        access_obligations=_compiled_network.access_obligations,
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


def test_destination_access_point_seven_metre_offset_is_not_attached(
    tmp_path: Path,
) -> None:
    config, source, compiled = _compiled(tmp_path)
    context = source["context"].copy()
    destination_mask = context["site_id"].eq("bath-spa-university")
    point = context.loc[destination_mask].to_crs(27700).geometry.iloc[0]
    shifted = (
        gpd.GeoSeries(
            [translate(point, xoff=7.0)],
            crs="EPSG:27700",
        )
        .to_crs(context.crs)
        .iloc[0]
    )
    context.loc[destination_mask, "geometry"] = shifted

    result = prepare_strategic_corridors(
        config.compilation.network_selection,
        road_graph=RoadGraph(mark_ncn_edges(source["network"], source["context"])),
        spine_access_connections=compiled.spine_access_connections,
        access_obligations=compiled.access_obligations,
        context=context,
        source_config=config.source,
        config_directory=config.config_path.parent,
    )

    assert result.status == "incomplete"
    assert [issue.reason for issue in result.issues] == [
        "destination-access-geometry-not-exactly-bound-to-current-road-graph"
    ]
    assert all(
        unit.unit_role is StrategicCorridorUnitRole.INTERURBAN_SPINE for unit in result.units
    )


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
        access_obligations=compiled.access_obligations,
        context=source["context"],
        source_config=config.source,
        config_directory=config.config_path.parent,
        area_definition=source["boundary"],
        urban_extent=gpd.GeoDataFrame(
            {"geometry": []},
            geometry="geometry",
            crs=27700,
        ),
    )

    assert repeated.preparation_fingerprint == first.preparation_fingerprint
    metadata = repeated.metadata()
    assert metadata["selection_performed"] is False
    assert metadata["network_geometry_mutated"] is False
    assert metadata["publication_performed"] is False


def test_strategic_preparation_reports_batched_route_phase_diagnostics(
    tmp_path: Path,
) -> None:
    _config, _source, compiled = _compiled(tmp_path)
    preparation = compiled.strategic_corridor_preparation

    assert preparation is not None
    diagnostics = preparation.phase_diagnostics
    assert diagnostics["anchors"] == 2
    assert diagnostics["pairs"] == 2
    assert diagnostics["route_searches"] == 8
    assert diagnostics["unique_alignments"] == 3
    assert diagnostics["sections"] == len(preparation.section_population.sections)
    assert diagnostics["elapsed_seconds"] >= 0


def test_legacy_urban_mode_retains_governed_a_road_backbone_obligation(
    tmp_path: Path,
) -> None:
    """Urban pair selection must not discard a separately governed A-road chain."""

    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    source = load_snapshot(config)
    labels = source["label_places"].copy()
    labels["kind"] = "town"
    labels["element"] = "node"
    labels["id"] = ["bath", "saltford"]
    labels["name"] = ["Bath", "Saltford"]
    source["label_places"] = labels

    # This is the exact supplied A4 source segment.  The official frame is
    # governed input for the public compiler seam, rather than a test-only
    # route or endpoint invented for the assertion.
    a4_spine = (
        source["network"]
        .loc[source["network"]["source_id"].eq("a4-spine-forward"), "geometry"]
        .iloc[0]
    )
    source["official_road_classification"] = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "bath-saltford-a4-spine",
                "official_classification": "a-road",
                "official_road_number": "A4",
                "source_id": "a4-spine-forward",
                "content_fingerprint": "a" * 64,
                "geometry": a4_spine,
            }
        ],
        geometry="geometry",
        crs=source["network"].crs,
    )

    compiled = compile_network(config, source, FakeAgentRuntime())
    preparation = compiled.strategic_corridor_preparation
    assert preparation is not None

    backbone_units = [
        unit
        for unit in preparation.units
        if unit.unit_role is StrategicCorridorUnitRole.A_ROAD_BACKBONE
    ]
    assert backbone_units
    assert all(unit.backbone_required for unit in backbone_units)
    assert any(
        candidate.source_class.value == "a-road-corridor"
        for unit in backbone_units
        for candidate in unit.candidate_set.candidates
    )

    planning = compiled.strategic_network_planning
    assert planning is not None
    backbone = backbone_units[0]
    selection = next(item for item in planning.selections if item.obligation_id == backbone.unit_id)
    assert selection.authority.value == "compiler"
    assert any(
        section.obligation_id == backbone.unit_id
        and section.candidate_id == selection.effective_candidate_id
        and section.primary_alignment_basis == "a-road"
        for section in planning.effective_network.sections
    )


def test_legacy_urban_mode_retains_typed_cycle_corridor_outside_town_pair(
    tmp_path: Path,
) -> None:
    """A typed cycle corridor remains a required comparison beside town journeys."""

    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    source = load_snapshot(config)
    labels = source["label_places"].copy()
    labels["kind"] = "town"
    labels["element"] = "node"
    labels["id"] = ["bath", "saltford"]
    labels["name"] = ["Bath", "Saltford"]
    source["label_places"] = labels

    # This reciprocal graph branch is governed by the added context row below
    # and shares the existing source graph at A4-root.  It is deliberately
    # outside the Bath-Saltford town-pair endpoints.
    branch = gpd.GeoDataFrame(
        [
            {
                "source_id": "ncn-out-forward",
                "u": "a4-root",
                "v": "ncn-out-end",
                "highway": "cycleway",
                "geometry": LineString([(-2.39, 51.37), (-2.38, 51.36)]),
            },
            {
                "source_id": "ncn-out-reverse",
                "u": "ncn-out-end",
                "v": "a4-root",
                "highway": "cycleway",
                "geometry": LineString([(-2.38, 51.36), (-2.39, 51.37)]),
            },
            {
                "source_id": "former-out-forward",
                "u": "a4-attach",
                "v": "former-out-end",
                "highway": "cycleway",
                "geometry": LineString([(-2.39, 51.385), (-2.38, 51.375)]),
            },
            {
                "source_id": "former-out-reverse",
                "u": "former-out-end",
                "v": "a4-attach",
                "highway": "cycleway",
                "geometry": LineString([(-2.38, 51.375), (-2.39, 51.385)]),
            },
            {
                "source_id": "plain-out-forward",
                "u": "ncn-out-end",
                "v": "plain-out-end",
                "highway": "cycleway",
                "geometry": LineString([(-2.38, 51.36), (-2.37, 51.35)]),
            },
            {
                "source_id": "plain-out-reverse",
                "u": "plain-out-end",
                "v": "ncn-out-end",
                "highway": "cycleway",
                "geometry": LineString([(-2.37, 51.35), (-2.38, 51.36)]),
            },
        ],
        geometry="geometry",
        crs=source["network"].crs,
    )
    source["network"] = gpd.GeoDataFrame(
        pd.concat([source["network"], branch], ignore_index=True, sort=False),
        geometry="geometry",
        crs=source["network"].crs,
    )
    cycle_context = gpd.GeoDataFrame(
        [
            {
                "evidence_id": "ncn-out-evidence",
                "feature_type": "ncn-route",
                "name": "Governed outer NCN branch",
                "source_id": "ncn-out",
                "geometry": LineString([(-2.39, 51.37), (-2.38, 51.36)]),
            },
            {
                "evidence_id": "former-out-evidence",
                "feature_type": "declassified-ncn-route",
                "name": "Governed former NCN branch",
                "source_id": "former-out",
                "geometry": LineString([(-2.39, 51.385), (-2.38, 51.375)]),
            },
        ],
        geometry="geometry",
        crs=source["context"].crs,
    )
    source["context"] = gpd.GeoDataFrame(
        pd.concat([source["context"], cycle_context], ignore_index=True, sort=False),
        geometry="geometry",
        crs=source["context"].crs,
    )

    compiled = compile_network(config, source, FakeAgentRuntime())
    preparation = compiled.strategic_corridor_preparation
    assert preparation is not None
    cycle_units = [
        unit
        for unit in preparation.units
        if unit.backbone_required
        and any(
            "ncn-out" in source_id
            for record in unit.candidate_records
            for source_id in record.source_ids
        )
    ]
    assert cycle_units
    cycle = cycle_units[0]
    assert cycle.unit_role is StrategicCorridorUnitRole.INTERURBAN_SPINE
    assert any(
        "current-ncn" in candidate.alignment_bases for candidate in cycle.candidate_set.candidates
    )

    planning = compiled.strategic_network_planning
    assert planning is not None
    selection = next(item for item in planning.selections if item.obligation_id == cycle.unit_id)
    assert any(
        section.obligation_id == cycle.unit_id
        and section.candidate_id == selection.effective_candidate_id
        and "current-ncn" in section.alignment_bases
        for section in planning.effective_network.sections
    )

    former_units = [
        unit
        for unit in preparation.units
        if unit.backbone_required
        and any(
            "former-out" in source_id
            for record in unit.candidate_records
            for source_id in record.source_ids
        )
    ]
    assert former_units
    assert any(
        "reclassified-ncn" in candidate.alignment_bases
        for unit in former_units
        for candidate in unit.candidate_set.candidates
    )
    assert not any(
        "plain-out" in source_id
        for unit in preparation.units
        for record in unit.candidate_records
        for source_id in record.source_ids
    )
