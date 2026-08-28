from __future__ import annotations

import sys

import geopandas as gpd
import pytest
from shapely.geometry import LineString
from test_strategic_network_planning import discovery, fixture_graph, request

from satn.candidate_discovery import CorridorObligation
from satn.effective_strategic_network import (
    EffectiveStrategicNetworkRequest,
    EffectiveStrategicNetworkState,
    EffectiveStrategicNetworkStatus,
    _planning_graph_with_urban_spines,
    _route_geometry,
    compile_effective_strategic_network,
    planning_graph_from_compiler_edges,
)
from satn.routing import RoadGraph
from satn.strategic_mesh import StrategicMainNetworkProfile
from satn.strategic_network_publication import project_strategic_network


def _fixture_preparation(graph):
    discovered = discovery(graph, CorridorObligation("corridor-a-d", "A", "D"))
    records = tuple(
        type(
            "Prepared",
            (),
            {
                "candidate": record.candidate_input,
                "routing_edge_ids": record.edge_ids,
                "reverse_routing_edge_ids": record.reverse_edge_ids,
                "evidence_ids": (),
                "source_ids": (),
                "generation_strategies": ("fixture",),
            },
        )()
        for record in discovered.candidate_records
    )
    unit = type(
        "Unit",
        (),
        {
            "unit_id": "corridor-a-d",
            "unit_role": type("Role", (), {"value": "interurban-spine"})(),
            "candidate_set": discovered.candidate_sets[0],
            "candidate_records": records,
            "routing_start_node_id": "A",
            "routing_end_node_id": "D",
        },
    )()
    return type(
        "Preparation",
        (),
        {
            "units": (unit,),
            "issues": (),
            "preparation_fingerprint": "a" * 64,
            "profile_fingerprint": discovered.candidate_sets[0].profile_fingerprint,
        },
    )()


def _fixture_routable_network() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "source_id": "a-road",
                "u": "A",
                "v": "D",
                "highway": "primary",
                "ref": "A1",
                "geometry": LineString([(0, 0), (100, 0)]),
            },
            {
                "source_id": "cycle-ab",
                "u": "A",
                "v": "B",
                "highway": "cycleway",
                "bicycle": "designated",
                "geometry": LineString([(0, 0), (0, 60)]),
            },
            {
                "source_id": "cycle-bd",
                "u": "B",
                "v": "D",
                "highway": "cycleway",
                "bicycle": "designated",
                "geometry": LineString([(0, 60), (100, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )


def test_repeated_osmid_rows_keep_directed_identity_and_contiguous_route_geometry() -> None:
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "shared-way",
                "u": "A",
                "v": "B",
                "oneway": True,
                "geometry": LineString([(0, 0), (1, 0)]),
            },
            {
                "osmid": "shared-way",
                "u": "B",
                "v": "C",
                "oneway": True,
                "geometry": LineString([(1, 0), (2, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )

    snapshot = planning_graph_from_compiler_edges(
        network,
        source_export_fingerprint="a" * 64,
    )
    graph = RoadGraph(network)
    option = graph.option("A", "C", "strategic-spine", strategic_use=True)

    assert option is not None
    assert len(snapshot.edge_records) == 2
    assert len({record.directed_edge_id for record in snapshot.edge_records}) == 2
    records_by_direction = {
        (record.from_node_id, record.to_node_id): record for record in snapshot.edge_records
    }
    assert tuple(option.directed_edge_ids) == (
        records_by_direction[("A", "B")].directed_edge_id,
        records_by_direction[("B", "C")].directed_edge_id,
    )
    assert list(_route_geometry(snapshot, tuple(option.directed_edge_ids)).coords) == [
        (0.0, 0.0),
        (1.0, 0.0),
        (2.0, 0.0),
    ]


def test_evaluation_is_the_canonical_state_and_preserves_planning_parity() -> None:
    graph = fixture_graph()
    planning_request = request(
        graph,
        discovery(graph, CorridorObligation("corridor-a-d", "A", "D")),
    )

    from satn.strategic_network_planning import compile_strategic_network

    state = EffectiveStrategicNetworkState.evaluated(compile_strategic_network(planning_request))

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    assert state.result is not None
    assert state.effective_network == state.result.effective_network
    assert state.selections == state.result.selections
    assert state.gaps == state.result.gaps
    assert state.divergences == state.result.divergences
    assert state.evidence_requests == state.result.evidence_requests
    assert state.lineage == state.result.lineage
    assert state.fingerprint == state.result.fingerprint


def test_missing_governed_identity_is_an_explicit_unavailable_state() -> None:
    state = compile_effective_strategic_network(EffectiveStrategicNetworkRequest())

    assert state.status is EffectiveStrategicNetworkStatus.UNAVAILABLE
    assert state.result is None
    assert state.reason == "governed-identity-unavailable"
    assert state.selections == ()
    assert state.gaps == ()


def test_canonical_module_has_no_runtime_adapter_dependency() -> None:
    import satn.effective_strategic_network as canonical

    sys.modules.pop("satn.strategic_network_adapter", None)
    assert (
        canonical.compile_effective_strategic_network(EffectiveStrategicNetworkRequest()).status
        is EffectiveStrategicNetworkStatus.UNAVAILABLE
    )

    assert "satn.strategic_network_adapter" not in sys.modules


def test_complete_routable_snapshot_and_preparation_use_one_canonical_selector() -> None:
    graph = fixture_graph()
    preparation = _fixture_preparation(graph)

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=graph,
            preparation=preparation,
            area_fingerprint="b" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    assert len(state.selections) == 1
    assert not state.gaps


def test_governed_access_connections_are_retained_as_access_support() -> None:
    network = _fixture_routable_network()
    graph = planning_graph_from_compiler_edges(
        network,
        source_export_fingerprint="3" * 64,
    )
    access_support = gpd.GeoDataFrame(
        [
            {
                "access_connection_id": "access-bathford",
                "obligation_id": "access-obligation-bathford",
                "obligation_kind": "community",
                "geometry": LineString([(20, 20), (40, 20)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=network,
            preparation=_fixture_preparation(graph),
            area_fingerprint="b" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
            access_support=(access_support,),
        )
    )

    support = next(
        section
        for section in state.effective_network.sections
        if section.section_id == "access-bathford"
    )
    assert support.network_role == "community-access"
    assert support.obligation_id == "access-obligation-bathford"
    assert support.routing_edge_ids == ()
    assert support.geometry_wkt == "LINESTRING (20 20, 40 20)"
    projection = project_strategic_network(state.result)
    assert [
        feature["properties"]["section_id"]
        for feature in projection.layers["Access Support"]["features"]
    ] == ["access-bathford"]


def test_redundant_urban_main_roads_are_omitted_by_authoritative_mesh_selection() -> None:
    routable_network = _fixture_routable_network()
    graph = planning_graph_from_compiler_edges(
        routable_network,
        source_export_fingerprint="3" * 64,
    )
    urban_spines = gpd.GeoDataFrame(
        [
            {
                "structure_id": "urban-spine-bristol-a4",
                "official_classification": "a-road",
                "geometry": LineString([(0, 0), (2, 0)]),
            },
            {
                "structure_id": "urban-spine-bristol-b4051",
                "official_classification": "b-road",
                "geometry": LineString([(2, 0), (2, 2)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=routable_network,
            preparation=_fixture_preparation(graph),
            area_fingerprint="b" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
            urban_spines=urban_spines,
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    urban_sections = tuple(
        section
        for section in state.effective_network.sections
        if section.network_role == "urban-main-road-spine"
    )
    assert urban_sections == ()
    selected_route = next(
        section
        for section in state.effective_network.sections
        if section.network_role == "interurban-spine"
    )
    assert selected_route.primary_alignment_basis == "mapped-cycleway"
    assert {
        diagnostic.subject_id
        for diagnostic in state.diagnostics
        if diagnostic.code == "strategic-mesh-section-omitted"
    } >= {"urban-spine-bristol-a4", "urban-spine-bristol-b4051"}


def test_effective_network_reduces_each_component_without_losing_mesh_coverage() -> None:
    routable_network = gpd.GeoDataFrame(
        [
            {
                "source_id": "a-road",
                "u": "A",
                "v": "D",
                "highway": "primary",
                "ref": "A1",
                "geometry": LineString([(0, 0), (1000, 0)]),
            },
            {
                "source_id": "b-road",
                "u": "C",
                "v": "E",
                "highway": "secondary",
                "ref": "B1",
                "geometry": LineString([(0, 150), (1000, 150)]),
            },
            {
                "source_id": "b-left",
                "u": "A",
                "v": "C",
                "highway": "residential",
                "geometry": LineString([(0, 0), (0, 150)]),
            },
            {
                "source_id": "b-right",
                "u": "D",
                "v": "E",
                "highway": "residential",
                "geometry": LineString([(1000, 0), (1000, 150)]),
            },
            {
                "source_id": "far-a-road",
                "u": "F",
                "v": "G",
                "highway": "primary",
                "ref": "A2",
                "geometry": LineString([(2000, 0), (3000, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    graph = planning_graph_from_compiler_edges(
        routable_network,
        source_export_fingerprint="6" * 64,
    )
    urban_spines = gpd.GeoDataFrame(
        [
            {
                "structure_id": "urban-main-a1",
                "official_classification": "a-road",
                "geometry": LineString([(0, 0), (1000, 0)]),
            },
            {
                "structure_id": "urban-main-b1",
                "official_classification": "b-road",
                "geometry": LineString([(0, 150), (1000, 150)]),
            },
            {
                "structure_id": "urban-main-a2",
                "official_classification": "a-road",
                "geometry": LineString([(2000, 0), (3000, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )

    mesh_profile = StrategicMainNetworkProfile()
    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=routable_network,
            preparation=_fixture_preparation(graph),
            area_fingerprint="c" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
            urban_spines=urban_spines,
            mesh_profile=mesh_profile,
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    assert state.lineage.mesh_profile_fingerprint == mesh_profile.fingerprint
    assert tuple(
        section.section_id
        for section in state.effective_network.sections
        if section.network_role == "urban-main-road-spine"
    ) == ("urban-main-a1", "urban-main-a2")
    assert not any(diagnostic.code == "strategic-mesh-gap" for diagnostic in state.diagnostics)
    assert not any(gap.network_role == "strategic-main-network" for gap in state.gaps)


def test_urban_spine_interior_endpoints_are_attached_to_routable_topology() -> None:
    routable_network = gpd.GeoDataFrame(
        [
            {
                "source_id": "main-a-b",
                "u": "A",
                "v": "B",
                "highway": "primary",
                "geometry": LineString([(0, 0), (1000, 0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    urban_spines = gpd.GeoDataFrame(
        [
            {
                "structure_id": "urban-a500-510",
                "official_classification": "a-road",
                "geometry": LineString([(500, 0), (510, 0)]),
            },
            {
                "structure_id": "urban-a600-610",
                "official_classification": "a-road",
                "geometry": LineString([(600, 0), (610, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )

    graph, required_sections = _planning_graph_with_urban_spines(
        routable_network,
        urban_spines,
        source_export_fingerprint="4" * 64,
    )

    assert [section.section_id for section in required_sections] == [
        "urban-a500-510",
        "urban-a600-610",
    ]
    urban_edges = {
        edge.directed_edge_id
        for section in required_sections
        for edge in graph.edge_records
        if edge.directed_edge_id in section.routing_edge_ids
    }
    weak_component = next(
        component
        for component in graph.component_records
        if component.kind == "weak" and urban_edges <= set(component.directed_edge_ids)
    )
    expected_nodes = {
        "A",
        "B",
        "xy:500.0000000:0.0000000",
        "xy:510.0000000:0.0000000",
        "xy:600.0000000:0.0000000",
        "xy:610.0000000:0.0000000",
    }
    assert expected_nodes <= set(weak_component.node_ids)
    assert {
        ("A", "xy:500.0000000:0.0000000"),
        ("xy:500.0000000:0.0000000", "xy:510.0000000:0.0000000"),
        ("xy:510.0000000:0.0000000", "xy:600.0000000:0.0000000"),
        ("xy:600.0000000:0.0000000", "xy:610.0000000:0.0000000"),
        ("xy:610.0000000:0.0000000", "B"),
    } <= {
        (edge.from_node_id, edge.to_node_id)
        for edge in graph.edge_records
        if edge.directed_edge_id in weak_component.directed_edge_ids
    }


def test_isolated_urban_spine_without_routable_attachment_is_rejected() -> None:
    routable_network = gpd.GeoDataFrame(
        [
            {
                "source_id": "main-a-b",
                "u": "A",
                "v": "B",
                "highway": "primary",
                "geometry": LineString([(0, 0), (1000, 0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    urban_spines = gpd.GeoDataFrame(
        [
            {
                "structure_id": "urban-isolated-2000-2100",
                "official_classification": "a-road",
                "geometry": LineString([(2000, 0), (2100, 0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )

    with pytest.raises(
        ValueError,
        match="unattachable urban strategic section: urban-isolated-2000-2100",
    ):
        _planning_graph_with_urban_spines(
            routable_network,
            urban_spines,
            source_export_fingerprint="5" * 64,
        )
