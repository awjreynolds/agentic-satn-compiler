from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pytest
from shapely.geometry import LineString, Point, Polygon

import satn.backbone as backbone_module
from satn.agents import AgentRole, FakeAgentRuntime
from satn.compiler import compile_network
from satn.models import CouncilConfig, TrafficLight
from satn.publisher import publish
from satn.routing import RouteOption

PROJECT = Path(__file__).parents[1]


def config() -> CouncilConfig:
    council = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    council.compilation.agent.response_mode = "direct-runtime"
    return council


def frame(rows: list[dict[str, object]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)


def parallel_spine_source(*, reverse: bool = False) -> dict[str, gpd.GeoDataFrame]:
    places = [
        {
            "place_id": "left-near",
            "name": "Left Near",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(0.02, 0),
        },
        {
            "place_id": "hinterland",
            "name": "Hinterland",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(0.04, 0),
        },
        {
            "place_id": "right-near",
            "name": "Right Near",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(0.08, 0),
        },
    ]
    network = [
        {
            "osmid": "left-spine-edge",
            "highway": "primary",
            "ref": "A1",
            "geometry": LineString([(0, 0), (0, 0.01)]),
        },
        {
            "osmid": "left-feed",
            "highway": "unclassified",
            "geometry": LineString([(0, 0), (0.02, 0)]),
        },
        {
            "osmid": "hinterland-feed",
            "highway": "unclassified",
            "geometry": LineString([(0.02, 0), (0.04, 0)]),
        },
        {
            "osmid": "middle-feed",
            "highway": "unclassified",
            "geometry": LineString([(0.04, 0), (0.08, 0)]),
        },
        {
            "osmid": "right-feed",
            "highway": "unclassified",
            "geometry": LineString([(0.08, 0), (0.1, 0)]),
        },
        {
            "osmid": "right-spine-edge",
            "highway": "primary",
            "ref": "A2",
            "geometry": LineString([(0.1, 0), (0.1, 0.01)]),
        },
    ]
    context = [
        {
            "evidence_id": "left-a1",
            "feature_type": "a-road-spine",
            "name": "A1",
            "category": "A-road strategic spine",
            "source_id": "left-spine-edge",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": LineString([(0, 0), (0, 0.01)]),
        },
        {
            "evidence_id": "right-a2",
            "feature_type": "a-road-spine",
            "name": "A2",
            "category": "A-road strategic spine",
            "source_id": "right-spine-edge",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": LineString([(0.1, 0), (0.1, 0.01)]),
        },
    ]
    if reverse:
        places.reverse()
        network.reverse()
        context.reverse()
    return {
        "places": frame(places),
        "network": frame(network),
        "context": frame(context),
        "boundary": gpd.GeoDataFrame(geometry=[], crs=4326),
    }


def three_spine_source() -> dict[str, gpd.GeoDataFrame]:
    source = parallel_spine_source()
    source["places"] = frame(
        [
            *source["places"].to_dict("records"),
            {
                "place_id": "third-near",
                "name": "Third Near",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(0.18, 0),
            },
        ]
    )
    source["network"] = frame(
        [
            *source["network"].to_dict("records"),
            {
                "osmid": "right-to-third",
                "highway": "unclassified",
                "geometry": LineString([(0.1, 0), (0.18, 0)]),
            },
            {
                "osmid": "third-feed",
                "highway": "unclassified",
                "geometry": LineString([(0.18, 0), (0.2, 0)]),
            },
            {
                "osmid": "third-spine-edge",
                "highway": "primary",
                "ref": "A3",
                "geometry": LineString([(0.2, 0), (0.2, 0.01)]),
            },
        ]
    )
    source["context"] = frame(
        [
            *source["context"].to_dict("records"),
            {
                "evidence_id": "third-a3",
                "feature_type": "a-road-spine",
                "name": "A3",
                "category": "A-road strategic spine",
                "source_id": "third-spine-edge",
                "feature_count": 1,
                "network_scope": "rural",
                "geometry": LineString([(0.2, 0), (0.2, 0.01)]),
            },
        ]
    )
    return source


def topology(compiled: object) -> list[tuple[object, ...]]:
    return sorted(
        (
            row.access_connection_id,
            row.place_id,
            row.root_spine_id,
            row.branch_id,
            row.parent_role,
            row.parent_place_id,
            row.parent_access_connection_id,
            row.geometry.wkb_hex,
        )
        for row in compiled.spine_access_connections.itertuples()
    )


def cross_spine_topology(compiled: object) -> list[tuple[object, ...]]:
    return sorted(
        (
            row.meeting_connection_id,
            row.from_place_id,
            row.to_place_id,
            row.from_root_spine_id,
            row.to_root_spine_id,
            row.geometry.wkb_hex,
        )
        for row in compiled.branch_meeting_connections.itertuples()
    )


def backbone_snapshot(compiled: object) -> dict[str, object]:
    """Capture authoritative, externally visible assembly results without timestamps."""
    return {
        "access": topology(compiled),
        "meetings": cross_spine_topology(compiled),
        "connectors": sorted(
            (
                row.cross_spine_connector_id,
                row.meeting_connection_id,
                row.geometry.wkb_hex,
            )
            for row in compiled.cross_spine_connectors.itertuples()
        ),
        "agent_records": [
            record.model_dump(mode="json", exclude={"created_at"})
            for record in compiled.agent_records
        ],
    }


def with_source_costs_below_geometry(
    source: dict[str, gpd.GeoDataFrame],
) -> dict[str, gpd.GeoDataFrame]:
    """Keep an admissible but non-unit cost/geometry ratio on every road edge."""
    network = source["network"].copy()
    network["length"] = 100.0
    return {**source, "network": network}


def test_all_spines_seed_order_independent_growth_and_hinterland_chaining() -> None:
    first = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    reordered = compile_network(config(), parallel_spine_source(reverse=True), FakeAgentRuntime())

    assert topology(first) == topology(reordered)
    assert cross_spine_topology(first) == cross_spine_topology(reordered)
    assert len(first.spine_access_connections) == 3
    assert len(first.access_obligations) == 3
    assert set(first.access_obligations["service_status"]) == {"served"}
    assert set(first.spine_access_connections["root_spine_id"]) == set(
        first.strategic_spines["spine_id"]
    )

    by_place = first.spine_access_connections.set_index("place_id")
    chained = by_place.loc["hinterland"]
    assert chained["parent_role"] == "spine-access-connection"
    assert chained["parent_place_id"] == "left-near"
    assert (
        chained["parent_access_connection_id"] == by_place.loc["left-near", "access_connection_id"]
    )
    assert chained["branch_id"] == by_place.loc["left-near", "branch_id"]
    assert chained["attachment_depth"] == 2

    for row in first.spine_access_connections.itertuples():
        provenance = json.loads(row.provenance)
        assert provenance["root_spine_id"] == row.root_spine_id
        assert provenance["branch_id"] == row.branch_id
        assert provenance["source_ids"]
        assert "cycling-network cost" in row.selection_reason

    assert len(first.spine_access_branches) == 2
    access_ids = set(first.spine_access_connections["access_connection_id"])
    assert access_ids <= {record.connection_id for record in first.agent_records}
    assert first.criteria["spine_network"]["all_access_obligations_resolved"] == "green"
    assert first.criteria["spine_network"]["degree_one_access_valid"] == "green"

    assert len(first.branch_meeting_connections) == 1
    meeting = first.branch_meeting_connections.iloc[0]
    assert {meeting["from_place_id"], meeting["to_place_id"]} == {
        "hinterland",
        "right-near",
    }
    assert {meeting["from_root_spine_id"], meeting["to_root_spine_id"]} == set(
        first.strategic_spines["spine_id"]
    )
    assert meeting["network_role"] == "branch-meeting-connection"
    assert meeting["status"] == "validated"
    assert meeting["intervention_archetype"] == "transverse link between Strategic Spine branches"
    assert "first justified" in meeting["selection_reason"]

    assert len(first.cross_spine_connectors) == 1
    connector = first.cross_spine_connectors.iloc[0]
    assert connector["network_role"] == "cross-spine-connector"
    assert connector["meeting_connection_id"] == meeting["meeting_connection_id"]
    assert {connector["from_root_spine_id"], connector["to_root_spine_id"]} == set(
        first.strategic_spines["spine_id"]
    )
    connector_ids = set(json.loads(connector["connection_ids"]))
    assert meeting["meeting_connection_id"] in connector_ids
    assert set(first.spine_access_connections["access_connection_id"]) <= connector_ids
    assert connector.geometry.covers(meeting.geometry)
    assert first.criteria["spine_network"]["cross_spine_traversal"] == "green"
    assert first.criteria["spine_network"]["parallel_meetings_suppressed"] == "green"


def test_reachable_attachment_can_bypass_a_nearer_disconnected_fragment() -> None:
    source = parallel_spine_source()
    source["places"] = frame(
        [
            *source["places"].to_dict("records"),
            {
                "place_id": "near-island",
                "name": "Near Island",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(0.05, 0.001),
            },
        ]
    )
    source["network"] = frame(
        [
            *source["network"].to_dict("records"),
            {
                "osmid": "near-island-fragment",
                "highway": "unclassified",
                "geometry": LineString([(0.05, 0.001), (0.051, 0.001)]),
            },
        ]
    )

    compiled = compile_network(config(), source, FakeAgentRuntime())

    obligation = compiled.access_obligations.set_index("place_id").loc["near-island"]
    assert obligation["service_status"] == "served"
    access = compiled.spine_access_connections.set_index("place_id").loc["near-island"]
    assert access["community_attachment_node"] != "xy:0.0500000:0.0010000"


def test_cross_spine_roles_publish_consistently_to_spatial_and_review_artifacts(
    tmp_path: Path,
) -> None:
    council = config()
    council.publication.output_dir = tmp_path / "output"
    source = parallel_spine_source()
    source["boundary"] = gpd.GeoDataFrame(
        [{"geometry": Polygon([(-0.01, -0.01), (0.11, -0.01), (0.11, 0.02), (-0.01, 0.02)])}],
        geometry="geometry",
        crs=4326,
    )
    compiled = compile_network(council, source, FakeAgentRuntime())

    artifacts = publish(council, compiled, "run-cross-spine")

    layer_names = set(gpd.list_layers(artifacts["geopackage"])["name"])
    assert {"branch_meeting_connections", "cross_spine_connectors"} <= layer_names
    meeting = gpd.read_file(artifacts["geopackage"], layer="branch_meeting_connections")
    connector = gpd.read_file(artifacts["geopackage"], layer="cross_spine_connectors")
    network = json.loads(artifacts["geojson"].read_text())
    feature_by_id = {feature["id"]: feature for feature in network["features"]}
    assert (
        feature_by_id[meeting.iloc[0]["meeting_connection_id"]]["properties"]["network_role"]
        == "branch-meeting-connection"
    )
    assert (
        feature_by_id[connector.iloc[0]["cross_spine_connector_id"]]["properties"][
            "selection_reason"
        ]
        == connector.iloc[0]["selection_reason"]
    )
    connector_id = connector.iloc[0]["cross_spine_connector_id"]
    run = json.loads(artifacts["run"].read_text())
    assert {
        "feature_id": connector_id,
        "network_role": "cross-spine-connector",
    } in run["authoritative_features"]
    agents = json.loads(artifacts["agents"].read_text())
    assert any(
        reference
        == {
            "feature_id": connector_id,
            "network_role": "cross-spine-connector",
        }
        for record in agents["records"]
        for reference in record["derived_features"]
    )
    html = artifacts["review_map"].read_text()
    assert 'id="layer-cross-spine-connectors"' in html
    assert 'id="legend-cross-spine-connectors"' in html


def test_direct_compile_publish_persists_the_canonical_empty_decision_contract(
    tmp_path: Path,
) -> None:
    council = config()
    council.compilation.agent.review_statuses = ()
    council.publication.output_dir = tmp_path / "output"
    source = parallel_spine_source()
    source["boundary"] = gpd.GeoDataFrame(
        [{"geometry": Polygon([(-0.01, -0.01), (0.11, -0.01), (0.11, 0.02), (-0.01, 0.02)])}],
        geometry="geometry",
        crs=4326,
    )
    compiled = compile_network(council, source, FakeAgentRuntime())

    assert compiled.decision_ledger_input == {
        "decision_contract": "agent-decision-menu/v1",
        "responses": [],
    }
    assert compiled.accepted_decisions == []
    artifacts = publish(council, compiled, "run-direct-empty-ledger")
    run = json.loads(artifacts["run"].read_text())
    assert run["decision_ledger_input"] == compiled.decision_ledger_input
    assert run["accepted_decisions"] == []


def test_direct_compile_publish_persists_its_bounded_runtime_audit(tmp_path: Path) -> None:
    council = config()
    council.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    council.compilation.agent.max_requests = 100
    council.compilation.agent.max_tokens = 10_000
    council.compilation.agent.deadline_seconds = 5
    council.publication.output_dir = tmp_path / "output"
    source = parallel_spine_source()
    source["boundary"] = gpd.GeoDataFrame(
        [{"geometry": Polygon([(-0.01, -0.01), (0.11, -0.01), (0.11, 0.02), (-0.01, 0.02)])}],
        geometry="geometry",
        crs=4326,
    )
    compiled = compile_network(council, source, FakeAgentRuntime())

    assert len(compiled.accepted_decisions) > 1
    assert [response["request_id"] for response in compiled.accepted_decisions] == sorted(
        response["request_id"] for response in compiled.accepted_decisions
    )
    artifacts = publish(council, compiled, "run-direct-runtime-audit")
    run = json.loads(artifacts["run"].read_text())
    assert run["decision_ledger_input"] == {
        "decision_contract": "agent-decision-menu/v1",
        "responses": [],
    }
    assert run["accepted_decisions"] == compiled.accepted_decisions


def test_first_meetings_connect_three_roots_without_forming_a_mesh() -> None:
    source = three_spine_source()
    reordered = {name: value.iloc[::-1].reset_index(drop=True) for name, value in source.items()}

    compiled = compile_network(config(), source, FakeAgentRuntime())
    repeated = compile_network(config(), reordered, FakeAgentRuntime())

    roots = set(compiled.spine_access_connections["root_spine_id"])
    assert len(roots) == 3
    assert len(compiled.branch_meeting_connections) == 2
    assert len(compiled.cross_spine_connectors) == 2
    assert cross_spine_topology(compiled) == cross_spine_topology(repeated)
    root_graph = nx.Graph()
    root_graph.add_nodes_from(roots)
    root_graph.add_edges_from(
        compiled.branch_meeting_connections[["from_root_spine_id", "to_root_spine_id"]].itertuples(
            index=False, name=None
        )
    )
    assert nx.is_tree(root_graph)


def test_rejected_first_meeting_falls_through_to_next_adjacency() -> None:
    runtime = FakeAgentRuntime(
        {
            AgentRole.DECISION: [
                *({"request_id": "$request", "choice_id": "1"} for _ in range(3)),
                {"request_id": "$request", "choice_id": "2"},
                {"request_id": "$request", "choice_id": "1"},
            ]
        }
    )

    council = config()
    council.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    compiled = compile_network(council, parallel_spine_source(), runtime)

    assert len(compiled.branch_meeting_connections) == 1
    meeting_records = [
        record
        for record in compiled.agent_records
        if record.connection_id.startswith("branch-meeting-")
    ]
    assert [record.decision for record in meeting_records] == ["superseded", "accept"]
    assert (
        compiled.branch_meeting_connections.iloc[0]["meeting_connection_id"]
        == meeting_records[-1].connection_id
    )


def test_rejected_meetings_superseded_when_other_tree_edges_connect_the_roots() -> None:
    runtime = FakeAgentRuntime(
        {
            AgentRole.DECISION: [
                *({"request_id": "$request", "choice_id": "1"} for _ in range(4)),
                *({"request_id": "$request", "choice_id": "2"} for _ in range(2)),
                *({"request_id": "$request", "choice_id": "1"} for _ in range(2)),
            ]
        }
    )

    council = config()
    council.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    compiled = compile_network(council, three_spine_source(), runtime)

    meeting_records = [
        record
        for record in compiled.agent_records
        if record.connection_id.startswith("branch-meeting-")
    ]
    assert [record.decision for record in meeting_records].count("accept") == 2
    assert [record.decision for record in meeting_records].count("superseded") == 2
    assert all(record.decision != "gap" for record in meeting_records)


@pytest.mark.parametrize("row_factory", ["_connection_row", "_meeting_row"])
def test_intervention_coverage_includes_every_backbone_connection_role(
    monkeypatch: pytest.MonkeyPatch,
    row_factory: str,
) -> None:
    original = getattr(backbone_module, row_factory)

    def without_intervention(*args: object, **kwargs: object) -> dict[str, object]:
        row = original(*args, **kwargs)
        row["intervention_archetype"] = None
        return row

    monkeypatch.setattr(backbone_module, row_factory, without_intervention)

    compiled = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())

    assert compiled.criteria["network"]["intervention_coverage"] == "red"


def test_meeting_distance_challenge_uses_unrounded_route_length() -> None:
    compiled = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    grouped = list(compiled.spine_access_connections.groupby("root_spine_id", sort=True))
    left = grouped[0][1].iloc[0]
    right = grouped[1][1].iloc[0]
    candidate = backbone_module._MeetingCandidate(
        rank=(),
        left=left,
        right=right,
        option=RouteOption(
            role="direct",
            geometry=LineString([(0, 0), (0.1, 0)]),
            length_km=15.0004,
            edge_ids=["forward"],
            a_road_share=0.0,
            ncn_share=0.0,
            bidirectional=True,
            reverse_length_km=15.0004,
            reverse_edge_ids=["reverse"],
            reverse_corridor_share=1.0,
            impracticable_alongside=False,
        ),
        start_node="left",
        end_node="right",
    )

    row = backbone_module._meeting_row(candidate, max_connection_km=15.0)

    assert row["distance_km"] == 15.0
    assert row["criterion_distance"] == "amber"


def test_equidistant_attachment_tie_is_stable_when_source_rows_reverse() -> None:
    places = [
        {
            "place_id": "tie",
            "name": "Tie",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(0, 0),
        },
        {
            "place_id": "left-anchor",
            "name": "Left Anchor",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(-0.01, 0),
        },
    ]
    network = [
        {
            "osmid": "left",
            "highway": "primary",
            "ref": "A1",
            "geometry": LineString([(-0.01, 0), (-0.001, 0)]),
        },
        {
            "osmid": "right",
            "highway": "primary",
            "ref": "A2",
            "geometry": LineString([(0.001, 0), (0.01, 0)]),
        },
    ]
    context = [
        {
            "evidence_id": "left-spine",
            "feature_type": "a-road-spine",
            "name": "A1",
            "category": "A-road strategic spine",
            "source_id": "left",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": network[0]["geometry"],
        },
        {
            "evidence_id": "right-spine",
            "feature_type": "a-road-spine",
            "name": "A2",
            "category": "A-road strategic spine",
            "source_id": "right",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": network[1]["geometry"],
        },
    ]

    def compile_rows(reverse: bool) -> object:
        return compile_network(
            config(),
            {
                "places": frame(list(reversed(places)) if reverse else places),
                "network": frame(list(reversed(network)) if reverse else network),
                "context": frame(list(reversed(context)) if reverse else context),
                "boundary": gpd.GeoDataFrame(geometry=[], crs=4326),
            },
            FakeAgentRuntime(),
        )

    assert topology(compile_rows(False)) == topology(compile_rows(True))


def test_unreachable_community_becomes_a_gap_without_fabricated_linework() -> None:
    source = parallel_spine_source()
    source["places"] = frame(
        [
            *source["places"].to_dict("records"),
            {
                "place_id": "island",
                "name": "Island",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(1, 1),
            },
        ]
    )
    source["network"] = frame(
        [
            *source["network"].to_dict("records"),
            {
                "osmid": "island-edge",
                "highway": "unclassified",
                "geometry": LineString([(1, 1), (1.01, 1)]),
            },
        ]
    )

    compiled = compile_network(config(), source, FakeAgentRuntime())

    obligation = compiled.access_obligations.set_index("place_id").loc["island"]
    assert obligation["service_status"] == "network-gap"
    gaps = compiled.gaps[compiled.gaps["network_role"] == "spine-access-gap"]
    assert len(gaps) == 1
    gap = gaps.iloc[0]
    assert gap["from_place"] == "island"
    assert gap.geometry.geom_type == "MultiPoint"
    assert len(gap.geometry.geoms) == 1
    assert gap["criterion_continuity"] == "red"
    assert compiled.criteria["spine_network"]["all_access_obligations_resolved"] == "red"


def test_agent_gate_rejection_cannot_enter_validated_backbone_state() -> None:
    runtime = FakeAgentRuntime(
        {
            AgentRole.DECISION: [
                {"request_id": "$request", "choice_id": "3"} for _ in range(6)
            ]
        }
    )

    council = config()
    council.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    compiled = compile_network(council, parallel_spine_source(), runtime)

    assert compiled.spine_access_connections.empty
    assert set(compiled.access_obligations["service_status"]) == {"network-gap"}
    access_records = [
        record
        for record in compiled.agent_records
        if record.connection_id.startswith("spine-access-")
    ]
    assert len(access_records) == 3
    assert {record.decision for record in access_records} == {"gap"}
    assert {record.selected_choice_id for record in access_records} == {"3"}


def test_meaningful_cross_boundary_gateway_attaches_to_the_assembled_frontier() -> None:
    source = parallel_spine_source()
    source["places"] = frame(
        [
            *source["places"].to_dict("records"),
            {
                "place_id": "gateway-east",
                "name": "Towards East Town",
                "kind": "cross_boundary_gateway",
                "place_class": "road",
                "geometry": Point(0.09, 0),
            },
        ]
    )
    source["network"] = frame(
        [
            *source["network"].to_dict("records"),
            {
                "osmid": "gateway-link",
                "highway": "unclassified",
                "geometry": LineString([(0.08, 0), (0.09, 0)]),
            },
            {
                "osmid": "gateway-to-spine",
                "highway": "unclassified",
                "geometry": LineString([(0.09, 0), (0.1, 0)]),
            },
        ]
    )

    compiled = compile_network(config(), source, FakeAgentRuntime())

    gateway = compiled.spine_access_connections[
        compiled.spine_access_connections["place_id"] == "gateway-east"
    ].iloc[0]
    assert gateway["place_kind"] == "cross_boundary_gateway"
    assert gateway["network_role"] == "gateway-access-connection"
    assert gateway["root_spine_id"] in set(compiled.strategic_spines["spine_id"])
    assert gateway["parent_role"] in {"strategic-spine", "spine-access-connection"}
    assert "gateway-east" not in set(compiled.access_obligations["place_id"])
    branch_place_ids = {
        place_id
        for value in compiled.spine_access_branches["place_ids"]
        for place_id in json.loads(value)
    }
    assert "gateway-east" not in branch_place_ids
    assert compiled.criteria["spine_network"]["gateway_coverage"] == "green"


def test_colocated_gateway_is_already_connected_without_zero_length_linework() -> None:
    source = parallel_spine_source()
    source["places"] = frame(
        [
            *source["places"].to_dict("records"),
            {
                "place_id": "gateway-colocated",
                "name": "Towards Nearby Town",
                "kind": "cross_boundary_gateway",
                "place_class": "road",
                "geometry": Point(0.08, 0),
            },
        ]
    )

    compiled = compile_network(config(), source, FakeAgentRuntime())

    gateway_rows = compiled.spine_access_connections[
        compiled.spine_access_connections["place_id"] == "gateway-colocated"
    ]
    assert gateway_rows.empty
    assert compiled.criteria["spine_network"]["gateway_coverage"] == "green"


def test_growth_uses_lazy_bounds_to_avoid_redundant_searches_at_representative_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    community_count = 24
    places = [
        {
            "place_id": f"community-{index:02d}",
            "name": f"Community {index:02d}",
            "kind": "community",
            "place_class": "village",
            "geometry": Point((index + 1) * 3000, 0),
        }
        for index in range(community_count)
    ]
    network = [
        {
            "osmid": f"edge-{index:02d}",
            "highway": "primary" if index == 0 else "unclassified",
            "ref": "A1" if index == 0 else None,
            "geometry": LineString([(index * 3000, 0), ((index + 1) * 3000, 0)]),
        }
        for index in range(community_count)
    ]
    context = [
        {
            "evidence_id": "scale-spine",
            "feature_type": "a-road-spine",
            "name": "A1",
            "category": "A-road strategic spine",
            "source_id": "edge-00",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": network[0]["geometry"],
        }
    ]
    calls = 0
    original = backbone_module._candidate

    def counted_candidate(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(backbone_module, "_candidate", counted_candidate)
    compiled = compile_network(
        config(),
        {
            "places": gpd.GeoDataFrame(places, geometry="geometry", crs=27700),
            "network": gpd.GeoDataFrame(network, geometry="geometry", crs=27700),
            "context": gpd.GeoDataFrame(context, geometry="geometry", crs=27700),
            "boundary": gpd.GeoDataFrame(geometry=[], crs=27700),
        },
        FakeAgentRuntime(),
    )

    assert len(compiled.spine_access_connections) == community_count
    diagnostics = compiled.compilation_diagnostics
    assert calls == community_count
    assert diagnostics["candidate_evaluations"] == calls
    assert diagnostics["candidate_pairs_enqueued"] == (
        community_count + community_count * (community_count - 1) // 2
    )
    assert diagnostics["candidate_searches_avoided"] == (
        diagnostics["candidate_pairs_enqueued"] - calls
    )
    assert diagnostics["candidate_lower_bound_factor"] == 1.0
    assert diagnostics["candidate_lower_bound_disabled_reason"] is None


def test_lazy_bounds_match_eager_equivalent_assembly_with_discounted_source_costs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = with_source_costs_below_geometry(parallel_spine_source())
    lazy = compile_network(config(), source, FakeAgentRuntime())

    def disable_lower_bound(self: object) -> None:
        self._lower_bound_cost_factor = 0.0
        self._lower_bound_disabled_reason = "forced-by-regression-test"

    monkeypatch.setattr(
        backbone_module.RoadGraph,
        "_set_lower_bound_cost_factor",
        disable_lower_bound,
    )
    eager_equivalent = compile_network(config(), source, FakeAgentRuntime())

    assert 0.0 < lazy.compilation_diagnostics["candidate_lower_bound_factor"] < 1.0
    assert eager_equivalent.compilation_diagnostics["candidate_lower_bound_factor"] == 0.0
    assert backbone_snapshot(lazy) == backbone_snapshot(eager_equivalent)


def test_lazy_bounds_skip_stale_community_work_after_the_community_is_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = parallel_spine_source()
    source["places"] = source["places"].query("place_id != 'hinterland'").copy()
    calls = 0
    original = backbone_module._candidate

    def counted_candidate(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(backbone_module, "_candidate", counted_candidate)
    compiled = compile_network(config(), source, FakeAgentRuntime())

    assert calls == 2
    assert compiled.compilation_diagnostics["candidate_evaluations"] == 2
    assert compiled.compilation_diagnostics["candidate_pairs_enqueued"] == 5
    assert compiled.compilation_diagnostics["candidate_searches_avoided"] == 3


def test_rejected_community_candidate_is_superseded_when_a_later_frontier_is_accepted() -> None:
    runtime = FakeAgentRuntime(
        {
            AgentRole.DECISION: [
                {"request_id": "$request", "choice_id": "2"},
                *({"request_id": "$request", "choice_id": "1"} for _ in range(8)),
            ]
        }
    )
    council = config()
    council.compilation.agent.review_statuses = (TrafficLight.GREEN,)

    compiled = compile_network(council, parallel_spine_source(), runtime)

    access_records = [
        record
        for record in compiled.agent_records
        if record.connection_id.startswith("spine-access-")
    ]
    rejected = access_records[0]
    assert rejected.decision == "superseded"
    assert "different governed frontier attachment" in rejected.outcome_reason
    assert any(
        record.decision == "accept"
        and record.affected_feature_identifiers[1] == rejected.affected_feature_identifiers[1]
        for record in access_records
    )
    assert [record.decision for record in access_records].count("accept") == 3
    assert len(compiled.spine_access_connections) == 3


def test_tied_roots_remain_eager_equivalent_when_inputs_are_reversed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    places = [
        {
            "place_id": "tie",
            "name": "Tie",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(0, 0),
        },
        {
            "place_id": "left-anchor",
            "name": "Left Anchor",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(-0.01, 0),
        },
    ]
    network = [
        {
            "osmid": "left",
            "highway": "primary",
            "ref": "A1",
            "length": 100,
            "geometry": LineString([(-0.01, 0), (-0.001, 0)]),
        },
        {
            "osmid": "right",
            "highway": "primary",
            "ref": "A2",
            "length": 100,
            "geometry": LineString([(0.001, 0), (0.01, 0)]),
        },
    ]
    context = [
        {
            "evidence_id": "left-spine",
            "feature_type": "a-road-spine",
            "name": "A1",
            "category": "A-road strategic spine",
            "source_id": "left",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": network[0]["geometry"],
        },
        {
            "evidence_id": "right-spine",
            "feature_type": "a-road-spine",
            "name": "A2",
            "category": "A-road strategic spine",
            "source_id": "right",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": network[1]["geometry"],
        },
    ]

    def compile_rows(*, reverse: bool, eager_equivalent: bool) -> object:
        if eager_equivalent:
            monkeypatch.setattr(
                backbone_module.RoadGraph,
                "_set_lower_bound_cost_factor",
                lambda graph: setattr(graph, "_lower_bound_cost_factor", 0.0),
            )
        return compile_network(
            config(),
            {
                "places": frame(list(reversed(places)) if reverse else places),
                "network": frame(list(reversed(network)) if reverse else network),
                "context": frame(list(reversed(context)) if reverse else context),
                "boundary": gpd.GeoDataFrame(geometry=[], crs=4326),
            },
            FakeAgentRuntime(),
        )

    lazy = compile_rows(reverse=False, eager_equivalent=False)
    reversed_lazy = compile_rows(reverse=True, eager_equivalent=False)
    eager_equivalent = compile_rows(reverse=True, eager_equivalent=True)

    assert backbone_snapshot(lazy) == backbone_snapshot(reversed_lazy)
    assert backbone_snapshot(lazy) == backbone_snapshot(eager_equivalent)
