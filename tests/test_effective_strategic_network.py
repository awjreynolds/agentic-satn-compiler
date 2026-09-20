from __future__ import annotations

import sys
from dataclasses import replace

import geopandas as gpd
import pytest
from shapely.geometry import LineString
from test_strategic_network_planning import discovery, fixture_graph, request

from satn.alignment_selection import admit_candidate_set
from satn.candidate_discovery import CorridorObligation
from satn.effective_strategic_network import (
    EffectiveStrategicNetworkRequest,
    EffectiveStrategicNetworkState,
    EffectiveStrategicNetworkStatus,
    _planning_graph_with_urban_spines,
    _route_geometry,
    _urban_attachment_diagnostics,
    compile_effective_strategic_network,
    planning_graph_from_compiler_edges,
)
from satn.network_selection import NetworkSelectionProfile
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


def _legacy_fixture_preparation(graph):
    preparation = _fixture_preparation(graph)
    source_set = preparation.units[0].candidate_set
    profile = NetworkSelectionProfile.model_validate(
        {
            "profile_id": "effective-legacy-preference-fixture",
            "candidate_source_precedence": [
                "verified-existing-asset",
                "a-road-corridor",
                "other-routable",
            ],
        }
    )
    legacy_set = admit_candidate_set(
        profile,
        network_role=source_set.network_role,
        endpoints=source_set.endpoints,
        candidates=source_set.candidates,
        mandatory_network_place_ids=source_set.mandatory_network_place_ids,
        mandatory_access_obligation_ids=source_set.mandatory_access_obligation_ids,
        mandatory_strategic_destination_ids=source_set.mandatory_strategic_destination_ids,
    )
    unit = type(
        "LegacyUnit",
        (),
        {
            "unit_id": preparation.units[0].unit_id,
            "unit_role": preparation.units[0].unit_role,
            "candidate_set": legacy_set,
            "candidate_records": preparation.units[0].candidate_records,
            "routing_start_node_id": preparation.units[0].routing_start_node_id,
            "routing_end_node_id": preparation.units[0].routing_end_node_id,
        },
    )()
    return type(
        "LegacyPreparation",
        (),
        {
            "units": (unit,),
            "issues": (),
            "preparation_fingerprint": preparation.preparation_fingerprint,
            "profile_fingerprint": legacy_set.profile_fingerprint,
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


def test_collection_osmid_survives_public_preparation_effective_compile() -> None:
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": [56752458, 25286883],
                "u": "A",
                "v": "B",
                "oneway": True,
                "highway": "cycleway",
                "bicycle": "designated",
                "geometry": LineString([(0, 0), (100, 0)]),
            },
            {
                "osmid": [56752458, 25286883],
                "u": "B",
                "v": "D",
                "oneway": True,
                "highway": "cycleway",
                "bicycle": "designated",
                "geometry": LineString([(100, 0), (200, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    effective_graph = planning_graph_from_compiler_edges(
        network,
        source_export_fingerprint="3" * 64,
    )
    routing_graph = RoadGraph(network)
    canonical_ids = {
        (str(start), str(end)): str(attrs["directed_edge_id"])
        for start, end, attrs in routing_graph.graph.edges(data=True)
    }
    replacement_ids = {
        record.directed_edge_id: canonical_ids[(record.from_node_id, record.to_node_id)]
        for record in effective_graph.edge_records
    }
    preparation_graph = replace(
        effective_graph,
        edge_records=tuple(
            replace(
                record,
                directed_edge_id=replacement_ids[record.directed_edge_id],
            )
            for record in effective_graph.edge_records
        ),
        component_records=tuple(
            replace(
                component,
                directed_edge_ids=tuple(
                    replacement_ids.get(edge_id, edge_id) for edge_id in component.directed_edge_ids
                ),
            )
            for component in effective_graph.component_records
        ),
    )
    preparation = _fixture_preparation(preparation_graph)

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=network,
            preparation=preparation,
            area_fingerprint="b" * 64,
            snapshot_fingerprint=effective_graph.source_export_fingerprint,
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    assert len(state.selections) == 1
    assert not state.gaps


def test_unique_edge_id_is_preserved_through_public_compile() -> None:
    network = gpd.GeoDataFrame(
        [
            {
                "edge_id": "fallback-edge",
                "u": "A",
                "v": "D",
                "oneway": True,
                "highway": "cycleway",
                "bicycle": "designated",
                "geometry": LineString([(0, 0), (200, 0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    graph = planning_graph_from_compiler_edges(
        network,
        source_export_fingerprint="3" * 64,
    )
    routing_graph = RoadGraph(network)

    assert graph.edge_records[0].source_edge_id == "fallback-edge"
    assert graph.edge_records[0].directed_edge_id == "fallback-edge"
    road_edge = next(iter(routing_graph.graph.edges(data=True)))[2]
    assert road_edge["edge_id"] == "fallback-edge"
    assert road_edge["directed_edge_id"] == "fallback-edge"

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=network,
            preparation=_fixture_preparation(graph),
            area_fingerprint="b" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    selected_sections = tuple(
        section
        for section in state.effective_network.sections
        if section.network_role == "interurban-spine"
    )
    assert len(selected_sections) == 1
    assert selected_sections[0].routing_edge_ids == ("fallback-edge",)


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


def test_effective_compile_reports_legacy_source_precedence_reason() -> None:
    routable_network = _fixture_routable_network()
    graph = planning_graph_from_compiler_edges(
        routable_network,
        source_export_fingerprint="3" * 64,
    )
    preparation = _legacy_fixture_preparation(graph)

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=routable_network,
            preparation=preparation,
            area_fingerprint="b" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    selection = state.selections[0]
    expected = next(
        candidate
        for candidate in preparation.units[0].candidate_set.admitted_candidates
        if candidate.source_class.value == "verified-existing-asset"
    )
    alternative = next(
        candidate
        for candidate in preparation.units[0].candidate_set.admitted_candidates
        if candidate.source_class.value == "a-road-corridor"
    )
    assert selection.compiler_candidate_id == expected.candidate_id
    assert selection.effective_candidate_id == expected.candidate_id
    assert selection.selection_reason.startswith(
        "compiler selection: candidate-source-precedence ranked candidate "
    )
    assert expected.candidate_id in selection.selection_reason
    assert alternative.candidate_id in selection.selection_reason
    assert "verified-existing-asset" in selection.selection_reason
    assert "a-road-corridor" in selection.selection_reason
    assert alternative.candidate_id != expected.candidate_id


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
                "target_attachment_node": "D",
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
    assert support.attachment_node_ids == ("D",)
    assert support.geometry_wkt == "LINESTRING (20 20, 40 20)"
    projection = project_strategic_network(state.result)
    assert [
        feature["properties"]["section_id"]
        for feature in projection.layers["Access Support"]["features"]
    ] == ["access-bathford"]


def test_legacy_backbone_support_retains_prepared_parent_when_substitute_is_selected() -> None:
    network = gpd.GeoDataFrame(
        [
            {
                "source_id": "a-road-am",
                "u": "A",
                "v": "M",
                "highway": "primary",
                "ref": "A1",
                "geometry": LineString([(0, 0), (50, 0)]),
            },
            {
                "source_id": "a-road-md",
                "u": "M",
                "v": "D",
                "highway": "primary",
                "ref": "A1",
                "geometry": LineString([(50, 0), (100, 0)]),
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
            {
                "source_id": "feeder",
                "u": "F",
                "v": "M",
                "highway": "residential",
                "geometry": LineString([(50, -20), (50, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    graph = planning_graph_from_compiler_edges(
        network,
        source_export_fingerprint="3" * 64,
    )
    base_preparation = _legacy_fixture_preparation(graph)
    base_unit = base_preparation.units[0]
    backbone_unit = type(
        "LegacyBackboneUnit",
        (),
        {
            "unit_id": base_unit.unit_id,
            "unit_role": base_unit.unit_role,
            "candidate_set": base_unit.candidate_set,
            "candidate_records": base_unit.candidate_records,
            "routing_start_node_id": base_unit.routing_start_node_id,
            "routing_end_node_id": base_unit.routing_end_node_id,
            "backbone_required": True,
        },
    )()
    preparation = type(
        "LegacyBackbonePreparation",
        (),
        {
            "units": (backbone_unit,),
            "issues": (),
            "preparation_fingerprint": base_preparation.preparation_fingerprint,
            "profile_fingerprint": base_preparation.profile_fingerprint,
        },
    )()
    feeder = gpd.GeoDataFrame(
        [
            {
                "access_connection_id": "feeder-access",
                "obligation_id": "feeder-obligation",
                "obligation_kind": "community",
                "root_spine_id": "corridor-a-d",
                "target_attachment_node": "M",
                "geometry": LineString([(50, -20), (50, 0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=network,
            preparation=preparation,
            area_fingerprint="b" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
            access_support=(feeder,),
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    assert state.selections[0].effective_candidate_id is not None
    selected_main_edges = {
        edge_id
        for section in state.effective_network.sections
        if section.network_role != "community-access"
        for edge_id in section.routing_edge_ids
    }
    assert {"a-road-am", "a-road-md", "cycle-ab", "cycle-bd"} <= selected_main_edges


def test_offset_access_support_is_extended_over_exact_graph_edges_to_selected_main() -> None:
    base = _fixture_routable_network()
    network = gpd.GeoDataFrame(
        [
            *base.to_dict("records"),
            {
                "source_id": "support-mx",
                "u": "M",
                "v": "X",
                "oneway": False,
                "highway": "residential",
                "geometry": LineString([(40, 0), (20, 0)]),
            },
            {
                "source_id": "support-xm",
                "u": "X",
                "v": "M",
                "oneway": False,
                "highway": "residential",
                "geometry": LineString([(20, 0), (40, 0)]),
            },
            {
                "source_id": "support-xa",
                "u": "X",
                "v": "A",
                "oneway": False,
                "highway": "residential",
                "geometry": LineString([(20, 0), (0, 0)]),
            },
            {
                "source_id": "support-ax",
                "u": "A",
                "v": "X",
                "oneway": False,
                "highway": "residential",
                "geometry": LineString([(0, 0), (20, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    graph = planning_graph_from_compiler_edges(
        network,
        source_export_fingerprint="3" * 64,
    )
    preparation = _legacy_fixture_preparation(graph)
    feeder = gpd.GeoDataFrame(
        [
            {
                "access_connection_id": "offset-feeder-access",
                "obligation_id": "offset-feeder-obligation",
                "obligation_kind": "community",
                "root_spine_id": "strategic-spine-a1",
                "parent_target_id": "strategic-spine-a1",
                "parent_target_name": "A1",
                "target_attachment_node": "M",
                "geometry": LineString([(40, -20), (40, 0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=network,
            preparation=preparation,
            area_fingerprint="b" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
            access_support=(feeder,),
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    support = next(
        section
        for section in state.effective_network.sections
        if section.section_id == "offset-feeder-access"
    )
    assert support.routing_edge_ids == ("support-mx", "support-xa")
    assert support.geometry_wkt == "LINESTRING (40 -20, 40 0, 20 0, 0 0)"
    main_edges = {
        edge_id
        for section in state.effective_network.sections
        if section.network_role != "community-access"
        for edge_id in section.routing_edge_ids
    }
    assert "a-road" not in main_edges
    assert {"cycle-ab", "cycle-bd"} <= main_edges


def test_offset_access_support_without_exact_graph_route_is_an_explicit_gap() -> None:
    base = _fixture_routable_network()
    network = gpd.GeoDataFrame(
        [
            *base.to_dict("records"),
            {
                "source_id": "isolated-mn",
                "u": "M",
                "v": "N",
                "highway": "residential",
                "geometry": LineString([(40, 0), (60, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    graph = planning_graph_from_compiler_edges(
        network,
        source_export_fingerprint="3" * 64,
    )
    feeder = gpd.GeoDataFrame(
        [
            {
                "access_connection_id": "unreachable-feeder-access",
                "obligation_id": "unreachable-feeder-obligation",
                "obligation_kind": "community",
                "target_attachment_node": "M",
                "geometry": LineString([(40, -20), (40, 0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=network,
            preparation=_legacy_fixture_preparation(graph),
            area_fingerprint="b" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
            access_support=(feeder,),
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    assert any(
        gap.obligation_id == "unreachable-feeder-obligation"
        and gap.network_role == "community-access"
        and gap.endpoints == ("M",)
        and "no exact allowed Planning Graph path" in gap.reason
        for gap in state.gaps
    )


def test_access_extension_uses_reciprocal_length_and_reverse_provenance() -> None:
    base = _fixture_routable_network()
    network = gpd.GeoDataFrame(
        [
            *base.to_dict("records"),
            {
                "source_id": "support-mx",
                "u": "M",
                "v": "X",
                "oneway": False,
                "highway": "residential",
                "geometry": LineString([(40, 0), (39, 0)]),
            },
            {
                "source_id": "support-xm",
                "u": "X",
                "v": "M",
                "oneway": False,
                "highway": "residential",
                "geometry": LineString([(39, 0), (40, 0)]),
            },
            {
                "source_id": "support-xa",
                "u": "X",
                "v": "A",
                "oneway": False,
                "highway": "residential",
                "geometry": LineString([(39, 0), (0, 0)]),
            },
            {
                "source_id": "support-ax",
                "u": "A",
                "v": "X",
                "oneway": False,
                "highway": "residential",
                "geometry": LineString([(0, 0), (39, 0)]),
            },
            {
                "source_id": "support-md",
                "u": "M",
                "v": "D",
                "oneway": False,
                "highway": "residential",
                "geometry": LineString([(40, 0), (40, 1000), (100, 0)]),
            },
            {
                "source_id": "support-dm",
                "u": "D",
                "v": "M",
                "oneway": False,
                "highway": "residential",
                "geometry": LineString([(100, 0), (40, 1000), (40, 0)]),
            },
            {
                "source_id": "support-mb-oneway",
                "u": "M",
                "v": "B",
                "oneway": True,
                "highway": "residential",
                "geometry": LineString([(40, 0), (0, 60)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    graph = planning_graph_from_compiler_edges(
        network,
        source_export_fingerprint="3" * 64,
    )
    feeder = gpd.GeoDataFrame(
        [
            {
                "access_connection_id": "prepend-feeder-access",
                "obligation_id": "prepend-feeder-obligation",
                "obligation_kind": "community",
                "target_attachment_node": "M",
                "geometry": LineString([(40, 0), (40, -20)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=network,
            preparation=_legacy_fixture_preparation(graph),
            area_fingerprint="b" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
            access_support=(feeder,),
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    support = next(
        section
        for section in state.effective_network.sections
        if section.section_id == "prepend-feeder-access"
    )
    assert support.routing_edge_ids == ("support-ax", "support-xm")
    assert support.reverse_routing_edge_ids == ("support-mx", "support-xa")
    assert support.geometry_wkt == "LINESTRING (0 0, 39 0, 40 0, 40 -20)"
    assert "support-md" not in support.routing_edge_ids
    assert "support-mb-oneway" not in support.routing_edge_ids


def test_geometry_only_non_oneway_edges_keep_roadgraph_reciprocity_for_access_extension() -> None:
    network = gpd.GeoDataFrame(
        [
            {
                "source_id": "geometry-only-main",
                "highway": "primary",
                "geometry": LineString([(0, 0), (100, 0)]),
            },
            {
                "source_id": "geometry-only-support",
                "highway": "residential",
                "geometry": LineString([(50, 0), (0, 0)]),
            },
            {
                "source_id": "oneway-support",
                "oneway": True,
                "highway": "residential",
                "geometry": LineString([(50, 0), (100, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    graph = planning_graph_from_compiler_edges(
        network,
        source_export_fingerprint="4" * 64,
    )
    start_node = "xy:0.0000000:0.0000000"
    end_node = "xy:100.0000000:0.0000000"
    attachment_node = "xy:50.0000000:0.0000000"
    road_graph = RoadGraph(network)
    main_option = road_graph.option(start_node, end_node, "strategic-spine")
    assert main_option is not None
    discovered = discovery(
        graph,
        CorridorObligation("geometry-only-corridor", start_node, end_node),
    )
    source_set = discovered.candidate_sets[0]
    records = tuple(
        type(
            "PreparedGeometryOnlyCandidate",
            (),
            {
                "candidate": record.candidate_input,
                "routing_edge_ids": tuple(main_option.directed_edge_ids),
                "reverse_routing_edge_ids": tuple(main_option.reverse_directed_edge_ids),
                "evidence_ids": (),
                "source_ids": (),
                "generation_strategies": ("fixture",),
            },
        )()
        for record in discovered.candidate_records
    )
    unit = type(
        "GeometryOnlyUnit",
        (),
        {
            "unit_id": "geometry-only-corridor",
            "unit_role": type("Role", (), {"value": "interurban-spine"})(),
            "candidate_set": source_set,
            "candidate_records": records,
            "routing_start_node_id": start_node,
            "routing_end_node_id": end_node,
        },
    )()
    preparation = type(
        "GeometryOnlyPreparation",
        (),
        {
            "units": (unit,),
            "issues": (),
            "preparation_fingerprint": "5" * 64,
            "profile_fingerprint": source_set.profile_fingerprint,
        },
    )()
    access_support = gpd.GeoDataFrame(
        [
            {
                "access_connection_id": "geometry-only-access",
                "obligation_id": "geometry-only-access-obligation",
                "obligation_kind": "community",
                "target_attachment_node": attachment_node,
                "geometry": LineString([(50, 20), (50, 0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=network,
            preparation=preparation,
            area_fingerprint="6" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
            access_support=(access_support,),
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    support = next(
        section
        for section in state.effective_network.sections
        if section.section_id == "geometry-only-access"
    )
    assert support.routing_edge_ids == ("geometry-only-support",)
    assert "oneway-support" not in support.routing_edge_ids
    assert support.geometry_wkt == "LINESTRING (50 20, 50 0, 0 0)"
    assert not any(gap.obligation_id == "geometry-only-access-obligation" for gap in state.gaps)


def test_geometry_only_cross_source_opposites_keep_roadgraph_ids_in_planning_graph() -> None:
    network = gpd.GeoDataFrame(
        [
            {
                "source_id": "long-forward",
                "highway": "primary",
                "length": 100.0,
                "geometry": LineString([(0, 0), (100, 0)]),
            },
            {
                "source_id": "short-opposite",
                "highway": "primary",
                "length": 1.0,
                "geometry": LineString([(100, 0), (0, 0)]),
            },
            {
                "source_id": "explicit-oneway",
                "oneway": True,
                "highway": "primary",
                "geometry": LineString([(0, 0), (100, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    planning = planning_graph_from_compiler_edges(
        network,
        source_export_fingerprint="a" * 64,
    )
    road = RoadGraph(network)
    planning_by_id = {edge.directed_edge_id: edge for edge in planning.edge_records}
    road_edges = {
        str(attrs["directed_edge_id"]): (str(start), str(end))
        for start, end, attrs in road.graph.edges(data=True)
    }

    assert road_edges
    assert set(road_edges) <= set(planning_by_id)
    assert all(
        (
            planning_by_id[directed_edge_id].from_node_id,
            planning_by_id[directed_edge_id].to_node_id,
        )
        == endpoints
        for directed_edge_id, endpoints in road_edges.items()
    )
    assert not any(
        edge.source_edge_id == "explicit-oneway"
        and edge.from_node_id == "xy:100.0000000:0.0000000"
        and edge.to_node_id == "xy:0.0000000:0.0000000"
        for edge in planning.edge_records
    )


def test_geometry_only_reverse_roadgraph_edge_keeps_forward_prepared_route_identity() -> None:
    network = gpd.GeoDataFrame(
        [
            {
                "source_id": "geometry-only-reversed-main",
                "highway": "primary",
                "geometry": LineString([(100, 0), (0, 0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    graph = planning_graph_from_compiler_edges(
        network,
        source_export_fingerprint="7" * 64,
    )
    start_node = "xy:0.0000000:0.0000000"
    end_node = "xy:100.0000000:0.0000000"
    reverse_record = next(
        record
        for record in graph.edge_records
        if record.from_node_id == start_node and record.to_node_id == end_node
    )
    road_option = RoadGraph(network).option(start_node, end_node, "strategic-spine")
    assert road_option is not None
    assert tuple(road_option.directed_edge_ids) == (reverse_record.directed_edge_id,)
    discovered = discovery(graph, CorridorObligation("reversed-corridor", start_node, end_node))
    source_set = discovered.candidate_sets[0]
    prepared_candidate = next(record for record in discovered.candidate_records if record.edge_ids)
    prepared_record = type(
        "PreparedReversedGeometryOnlyCandidate",
        (),
        {
            "candidate": prepared_candidate.candidate_input,
            "routing_edge_ids": tuple(road_option.directed_edge_ids),
            "reverse_routing_edge_ids": tuple(road_option.reverse_directed_edge_ids),
            "evidence_ids": (),
            "source_ids": (),
            "generation_strategies": ("roadgraph-reverse-fixture",),
        },
    )()
    unit = type(
        "ReversedGeometryOnlyUnit",
        (),
        {
            "unit_id": "reversed-corridor",
            "unit_role": type("Role", (), {"value": "interurban-spine"})(),
            "candidate_set": source_set,
            "candidate_records": (prepared_record,),
            "routing_start_node_id": start_node,
            "routing_end_node_id": end_node,
        },
    )()
    preparation = type(
        "ReversedGeometryOnlyPreparation",
        (),
        {
            "units": (unit,),
            "issues": (),
            "preparation_fingerprint": "8" * 64,
            "profile_fingerprint": source_set.profile_fingerprint,
        },
    )()

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=network,
            preparation=preparation,
            area_fingerprint="9" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    selected = next(
        section
        for section in state.effective_network.sections
        if section.network_role == "interurban-spine"
    )
    assert selected.routing_edge_ids == (reverse_record.directed_edge_id,)
    assert selected.geometry_wkt == "LINESTRING (0 0, 100 0)"


def test_urban_a_road_defaults_are_protected_by_authoritative_mesh_selection() -> None:
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
    assert tuple(section.section_id for section in urban_sections) == ("urban-spine-bristol-a4",)
    assert not any(
        section.network_role == "interurban-spine" for section in state.effective_network.sections
    )


def test_supplied_b_road_village_branch_is_not_a_main_section() -> None:
    routable_network = _fixture_routable_network()
    graph = planning_graph_from_compiler_edges(
        routable_network,
        source_export_fingerprint="3" * 64,
    )
    urban_spines = gpd.GeoDataFrame(
        [
            {
                "structure_id": "urban-spine-village-b-branch",
                "official_classification": "b-road",
                "geometry": LineString([(0, 0), (0, 500)]),
            }
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
    assert "urban-spine-village-b-branch" not in {
        section.section_id for section in state.effective_network.sections
    }
    assert "urban-spine-village-b-branch" not in {
        diagnostic.subject_id
        for diagnostic in state.diagnostics
        if diagnostic.code == "strategic-main-attachment-gap"
    }


@pytest.mark.parametrize(
    ("gap_highway", "urban_classification"),
    (("secondary", "b-road"), ("tertiary", "classified-unnumbered")),
)
def test_non_main_urban_spine_can_connect_selected_main_components(
    gap_highway: str,
    urban_classification: str,
) -> None:
    routable_network = gpd.GeoDataFrame(
        [
            {
                "source_id": "a-west",
                "u": "A",
                "v": "B",
                "highway": "primary",
                "oneway": False,
                "geometry": LineString([(0, 0), (100, 0)]),
            },
            {
                "source_id": "a-west-reverse",
                "u": "B",
                "v": "A",
                "highway": "primary",
                "oneway": False,
                "geometry": LineString([(100, 0), (0, 0)]),
            },
            {
                "source_id": "b-gap",
                "u": "B",
                "v": "C",
                "highway": gap_highway,
                "oneway": False,
                "geometry": LineString([(100, 0), (200, 0)]),
            },
            {
                "source_id": "b-gap-reverse",
                "u": "C",
                "v": "B",
                "highway": gap_highway,
                "oneway": False,
                "geometry": LineString([(200, 0), (100, 0)]),
            },
            {
                "source_id": "a-east",
                "u": "C",
                "v": "D",
                "highway": "primary",
                "oneway": False,
                "geometry": LineString([(200, 0), (300, 0)]),
            },
            {
                "source_id": "a-east-reverse",
                "u": "D",
                "v": "C",
                "highway": "primary",
                "oneway": False,
                "geometry": LineString([(300, 0), (200, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    graph = planning_graph_from_compiler_edges(
        routable_network,
        source_export_fingerprint="7" * 64,
    )
    urban_spines = gpd.GeoDataFrame(
        [
            {
                "structure_id": "urban-main-west",
                "official_classification": "a-road",
                "geometry": LineString([(0, 0), (100, 0)]),
            },
            {
                "structure_id": "urban-b-gap",
                "official_classification": urban_classification,
                "geometry": LineString([(100, 0), (200, 0)]),
            },
            {
                "structure_id": "urban-main-east",
                "official_classification": "a-road",
                "geometry": LineString([(200, 0), (300, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    preparation = type(
        "EmptyPreparation",
        (),
        {
            "units": (),
            "issues": (),
            "preparation_fingerprint": "d" * 64,
            "profile_fingerprint": "e" * 64,
        },
    )()

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=routable_network,
            preparation=preparation,
            area_fingerprint="f" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
            urban_spines=urban_spines,
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    sections = {section.section_id: section for section in state.effective_network.sections}
    assert "urban-b-gap" not in sections
    connectors = tuple(
        section
        for section in state.effective_network.sections
        if section.network_role == "strategic-main-connector"
    )
    assert len(connectors) == 1
    assert set((*connectors[0].routing_edge_ids, *connectors[0].reverse_routing_edge_ids)) == {
        "b-gap",
        "b-gap-reverse",
    }


def test_effective_network_and_publication_connect_main_components_through_routable_graph() -> None:
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
                "source_id": "a-road-reverse",
                "u": "D",
                "v": "A",
                "highway": "primary",
                "ref": "A1",
                "geometry": LineString([(1000, 0), (0, 0)]),
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
            {
                "source_id": "far-a-road-reverse",
                "u": "G",
                "v": "F",
                "highway": "primary",
                "ref": "A2",
                "geometry": LineString([(3000, 0), (2000, 0)]),
            },
            {
                "source_id": "short-other-link",
                "u": "D",
                "v": "F",
                "highway": "residential",
                "geometry": LineString([(1000, 0), (2000, 0)]),
            },
            {
                "source_id": "short-other-link",
                "u": "F",
                "v": "D",
                "highway": "residential",
                "geometry": LineString([(2000, 0), (1000, 0)]),
            },
            {
                "source_id": "cycle-continuity-west",
                "u": "D",
                "v": "J",
                "highway": "cycleway",
                "bicycle": "designated",
                "geometry": LineString([(1000, 0), (1500, 500)]),
            },
            {
                "source_id": "cycle-continuity-west",
                "u": "J",
                "v": "D",
                "highway": "cycleway",
                "bicycle": "designated",
                "geometry": LineString([(1500, 500), (1000, 0)]),
            },
            {
                "source_id": "cycle-continuity-east",
                "u": "J",
                "v": "F",
                "highway": "cycleway",
                "bicycle": "designated",
                "geometry": LineString([(1500.001, 500), (2000, 0)]),
            },
            {
                "source_id": "cycle-continuity-east",
                "u": "F",
                "v": "J",
                "highway": "cycleway",
                "bicycle": "designated",
                "geometry": LineString([(2000, 0), (1500.001, 500)]),
            },
            {
                "source_id": "unreachable-a-road",
                "u": "H",
                "v": "I",
                "highway": "primary",
                "ref": "A3",
                "geometry": LineString([(4000, 0), (5000, 0)]),
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
                "geometry": LineString([(100, 10), (900, 10)]),
            },
            {
                "structure_id": "urban-main-b1",
                "official_classification": "b-road",
                "geometry": LineString([(0, 150), (1000, 150)]),
            },
            {
                "structure_id": "urban-main-a2",
                "official_classification": "a-road",
                "geometry": LineString([(2100, 10), (2900, 10)]),
            },
            {
                "structure_id": "urban-main-a3-unreachable",
                "official_classification": "a-road",
                "geometry": LineString([(4000, 0), (5000, 0)]),
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
    ) == ("urban-main-a1", "urban-main-a2", "urban-main-a3-unreachable")
    assert not any(diagnostic.code == "strategic-mesh-gap" for diagnostic in state.diagnostics)
    assert not any(gap.network_role == "strategic-main-network" for gap in state.gaps)

    projection = project_strategic_network(state.result)
    main_features = projection.layers["Strategic Main Network"]["features"]
    selected_features = [
        feature
        for feature in main_features
        if feature["properties"]["feature_type"] == "reviewable-selected-route"
    ]
    assert len(selected_features) == 4
    assert {feature["properties"]["section_id"] for feature in selected_features} >= {
        "urban-main-a1",
        "urban-main-a2",
        "urban-main-a3-unreachable",
    }
    assert any(
        feature["properties"]["section_id"].startswith("strategic-main-continuity-")
        for feature in selected_features
    )


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


def test_unattached_a_road_spine_is_retained_with_located_main_attachment_gap() -> None:
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

    graph = planning_graph_from_compiler_edges(
        routable_network,
        source_export_fingerprint="5" * 64,
    )
    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network,
            _fixture_preparation(graph),
            "d" * 64,
            graph.source_export_fingerprint,
            urban_spines=urban_spines,
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    assert "urban-isolated-2000-2100" in {
        section.section_id for section in state.effective_network.sections
    }
    gap = next(
        gap for gap in state.gaps if gap.obligation_id == "urban-structure:urban-isolated-2000-2100"
    )
    assert gap.network_role == "strategic-main-network"
    assert gap.endpoint_coordinates == ((2000.0, 0.0), (2100.0, 0.0))
    assert any(
        diagnostic.code == "strategic-main-attachment-gap"
        and diagnostic.subject_id == "urban-isolated-2000-2100"
        for diagnostic in state.diagnostics
    )


def test_detached_classified_unnumbered_spine_is_excluded_but_context_remains() -> None:
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
                "structure_id": "urban-isolated-unnumbered",
                "official_classification": "classified-unnumbered",
                "geometry": LineString([(2000, 0), (2100, 0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )

    graph, required_sections = _planning_graph_with_urban_spines(
        routable_network,
        urban_spines,
        source_export_fingerprint="6" * 64,
    )
    diagnostics = _urban_attachment_diagnostics(
        routable_network,
        urban_spines,
        graph,
        required_sections,
    )

    assert required_sections == ()
    urban_edge = next(
        edge for edge in graph.edge_records if edge.source_edge_id == "urban-isolated-unnumbered"
    )
    assert urban_edge.geometry_wkt == "LINESTRING (2000 0, 2100 0)"
    assert any(
        diagnostic.code == "strategic-main-section-excluded"
        and diagnostic.subject_id == "urban-isolated-unnumbered"
        and "classified-unnumbered" in diagnostic.message
        and "no current routable-network attachment" in diagnostic.message
        for diagnostic in diagnostics.diagnostics
    )


def test_detached_mixed_a_and_classified_unnumbered_component_keeps_a_only() -> None:
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
                "structure_id": "urban-detached-a",
                "official_classification": "a-road",
                "geometry": LineString([(2000, 0), (2050, 0)]),
            },
            {
                "structure_id": "urban-detached-unnumbered",
                "official_classification": "classified-unnumbered",
                "geometry": LineString([(2050, 0), (2100, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )

    graph, required_sections = _planning_graph_with_urban_spines(
        routable_network,
        urban_spines,
        source_export_fingerprint="8" * 64,
    )
    diagnostics = _urban_attachment_diagnostics(
        routable_network,
        urban_spines,
        graph,
        required_sections,
    )

    assert {section.section_id for section in required_sections} == {"urban-detached-a"}
    assert [
        diagnostic.subject_id
        for diagnostic in diagnostics.diagnostics
        if diagnostic.code == "strategic-main-attachment-gap"
    ] == ["urban-detached-a"]
    assert any(
        diagnostic.subject_id == "urban-detached-unnumbered"
        and diagnostic.code == "strategic-main-section-excluded"
        for diagnostic in diagnostics.diagnostics
    )


def test_attached_classified_unnumbered_spine_is_excluded_but_context_remains() -> None:
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
                "structure_id": "urban-attached-unnumbered",
                "official_classification": "classified-unnumbered",
                "geometry": LineString([(500, 0), (600, 0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )

    graph, required_sections = _planning_graph_with_urban_spines(
        routable_network,
        urban_spines,
        source_export_fingerprint="7" * 64,
    )
    diagnostics = _urban_attachment_diagnostics(
        routable_network,
        urban_spines,
        graph,
        required_sections,
    )

    assert required_sections == ()
    urban_edge = next(
        edge for edge in graph.edge_records if edge.source_edge_id == "urban-attached-unnumbered"
    )
    assert urban_edge.geometry_wkt == "LINESTRING (500 0, 600 0)"
    assert diagnostics.diagnostics == ()
