from __future__ import annotations

import sys

import geopandas as gpd
from shapely.geometry import LineString
from test_strategic_network_planning import discovery, fixture_graph, request

from satn.candidate_discovery import CorridorObligation
from satn.effective_strategic_network import (
    EffectiveStrategicNetworkRequest,
    EffectiveStrategicNetworkState,
    EffectiveStrategicNetworkStatus,
    _route_geometry,
    compile_effective_strategic_network,
    planning_graph_from_compiler_edges,
)
from satn.routing import RoadGraph


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
    preparation = type(
        "Preparation",
        (),
        {
            "units": (unit,),
            "issues": (),
            "preparation_fingerprint": "a" * 64,
            "profile_fingerprint": discovered.candidate_sets[0].profile_fingerprint,
        },
    )()

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
