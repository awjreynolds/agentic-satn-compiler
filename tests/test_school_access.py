from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon
from shapely.wkt import loads as load_wkt

import satn.backbone as backbone_module
from satn.agents import AgentRole, CompilationGate, FakeAgentRuntime
from satn.backbone import assemble_backbone_outward
from satn.compiler import compile_network
from satn.evidence import derive_context_layers, govern_network_scope
from satn.models import AgentConfig, CouncilConfig, TrafficLight
from satn.publisher import publish
from satn.routing import RoadGraph
from satn.sources import derive_strategic_destinations

PROJECT = Path(__file__).parents[1]


def config() -> CouncilConfig:
    council = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    council.compilation.agent.response_mode = "direct-runtime"
    return council


def frame(rows: list[dict[str, object]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)


def test_school_evidence_prefers_mapped_entrances_and_records_fallback_state() -> None:
    network = frame(
        [
            {
                "osmid": "primary-access-road",
                "highway": "unclassified",
                "geometry": LineString([(-0.004, 0), (-0.002, 0)]),
            },
            {
                "osmid": "secondary-road",
                "highway": "unclassified",
                "geometry": LineString([(0.019, 0), (0.023, 0)]),
            },
        ]
    )
    facilities = frame(
        [
            {
                "osmid": "primary",
                "amenity": "school",
                "school": "primary",
                "name": "Primary School",
                "geometry": Polygon(
                    [(-0.002, -0.002), (0.002, -0.002), (0.002, 0.002), (-0.002, 0.002)]
                ),
            },
            {
                "osmid": "primary-main",
                "entrance": "main",
                "access": "yes",
                "geometry": Point(-0.002, 0),
            },
            {
                "osmid": "primary-emergency",
                "entrance": "emergency",
                "geometry": Point(0.002, 0),
            },
            {
                "osmid": "unrelated-internal-entrance",
                "entrance": "main",
                "geometry": Point(0, 0),
            },
            {
                "osmid": "secondary",
                "amenity": "school",
                "isced:level": "2;3",
                "name": "Secondary School",
                "geometry": Polygon(
                    [(0.02, -0.002), (0.022, -0.002), (0.022, 0.002), (0.02, 0.002)]
                ),
            },
            {
                "osmid": "all-through",
                "amenity": "school",
                "school": "all_through",
                "name": "All-through School",
                "geometry": Point(0.04, 0),
            },
            {
                "osmid": "special",
                "amenity": "school",
                "special_needs": "yes",
                "name": "Special School",
                "geometry": Point(0.06, 0),
            },
            {
                "osmid": "not-special",
                "amenity": "school",
                "special_needs": "no",
                "isced:level": "2;3",
                "name": "Ordinary Secondary",
                "geometry": Point(0.07, 0),
            },
            {
                "osmid": "college",
                "amenity": "college",
                "name": "Context College",
                "geometry": Point(0.08, 0),
            },
            {
                "osmid": "university",
                "amenity": "university",
                "name": "Context University",
                "geometry": Point(0.10, 0),
            },
        ]
    )

    context = derive_context_layers(network, facilities=facilities)
    governed = govern_network_scope(
        context,
        gpd.GeoDataFrame({"place": []}, geometry=[], crs=4326),
        urban_place_types=["city", "town"],
        urban_scope_buffer_km=0.5,
    )
    schools = governed[governed["feature_type"] == "school"].set_index("source_id")

    ordinary_schools = schools[schools["category"] == "school"]
    assert set(
        ordinary_schools.loc[ordinary_schools["school_obligation_eligible"], "school_kind"]
    ) == {
        "all-through",
        "primary",
        "secondary",
        "special",
    }
    assert not bool(schools.loc["college", "school_obligation_eligible"])
    assert not bool(schools.loc["university", "school_obligation_eligible"])
    assert schools.loc["not-special", "school_kind"] == "secondary"
    assert schools.loc["primary", "access_point_status"] == "mapped"
    assert schools.loc["primary", "access_point_source_id"] == "primary-main"
    assert schools.loc["primary"].geometry.distance(Point(-0.002, 0)) < 1e-9
    assert schools.loc["secondary", "access_point_status"] == "inferred"
    assert "school boundary" in schools.loc["secondary", "access_point_rationale"]
    assert schools.loc["special", "access_point_status"] == "unresolved"
    assert set(schools["network_scope"]) == {"rural"}

    destinations = derive_strategic_destinations(facilities, ["college"], 4326)
    assert list(destinations["kind"]) == ["strategic_destination"]
    assert list(destinations["place_class"]) == ["college"]
    assert list(destinations["source_id"]) == ["college"]


def test_school_entrance_proximity_without_boundary_and_path_evidence_is_not_mapped() -> None:
    network = frame(
        [
            {
                "osmid": "road",
                "highway": "unclassified",
                "geometry": LineString([(0, 0.01), (0.02, 0.01)]),
            }
        ]
    )
    facilities = frame(
        [
            {
                "osmid": "school",
                "amenity": "school",
                "geometry": Polygon([(0, 0), (0.02, 0), (0.02, 0.02), (0, 0.02)]),
            },
            {"osmid": "internal", "entrance": "main", "geometry": Point(0.01, 0.01)},
        ]
    )

    school = (
        derive_context_layers(network, facilities=facilities)
        .query("feature_type == 'school'")
        .iloc[0]
    )

    assert school["access_point_status"] == "inferred"
    assert school["access_point_source_id"] is None


def school_source() -> dict[str, gpd.GeoDataFrame]:
    return {
        "places": frame(
            [
                {
                    "place_id": "near",
                    "name": "Near",
                    "kind": "community",
                    "place_class": "village",
                    "geometry": Point(0.04, 0),
                },
                {
                    "place_id": "far",
                    "name": "Far",
                    "kind": "community",
                    "place_class": "village",
                    "geometry": Point(0.06, 0),
                },
            ]
        ),
        "network": frame(
            [
                {
                    "osmid": "spine",
                    "highway": "primary",
                    "ref": "A1",
                    "geometry": LineString([(0, 0), (0, 0.01)]),
                },
                {
                    "osmid": "feed-1",
                    "highway": "unclassified",
                    "geometry": LineString([(0, 0), (0.02, 0)]),
                },
                {
                    "osmid": "feed-2",
                    "highway": "unclassified",
                    "geometry": LineString([(0.02, 0), (0.04, 0)]),
                },
                {
                    "osmid": "feed-3",
                    "highway": "unclassified",
                    "geometry": LineString([(0.04, 0), (0.06, 0)]),
                },
            ]
        ),
        "context": frame(
            [
                {
                    "evidence_id": "a1",
                    "feature_type": "a-road-spine",
                    "name": "A1",
                    "category": "A-road strategic spine",
                    "source_id": "spine",
                    "feature_count": 1,
                    "network_scope": "rural",
                    "geometry": LineString([(0, 0), (0, 0.01)]),
                },
                {
                    "evidence_id": "mapped-school",
                    "feature_type": "school",
                    "name": "Mapped Primary",
                    "category": "school",
                    "source_id": "school-mapped",
                    "feature_count": 1,
                    "network_scope": "rural",
                    "school_kind": "primary",
                    "school_obligation_eligible": True,
                    "access_point_status": "mapped",
                    "access_point_source_id": "entrance-main",
                    "access_point_rationale": "Mapped main entrance with usable access.",
                    "geometry": Point(0.02, 0),
                },
                {
                    "evidence_id": "unresolved-school",
                    "feature_type": "school",
                    "name": "Unresolved Special",
                    "category": "school",
                    "source_id": "school-unresolved",
                    "feature_count": 1,
                    "network_scope": "rural",
                    "school_kind": "special",
                    "school_obligation_eligible": True,
                    "access_point_status": "unresolved",
                    "access_point_source_id": None,
                    "access_point_rationale": (
                        "No usable entrance or defensible inference is mapped."
                    ),
                    "geometry": Point(0.05, 0.005),
                },
                {
                    "evidence_id": "college",
                    "feature_type": "school",
                    "name": "Context College",
                    "category": "college",
                    "source_id": "college",
                    "feature_count": 1,
                    "network_scope": "rural",
                    "school_kind": "college",
                    "school_obligation_eligible": False,
                    "access_point_status": "unresolved",
                    "access_point_source_id": None,
                    "access_point_rationale": "Context only.",
                    "geometry": Point(0.03, 0.005),
                },
                {
                    "evidence_id": "university",
                    "feature_type": "school",
                    "name": "Context University",
                    "category": "university",
                    "source_id": "university",
                    "feature_count": 1,
                    "network_scope": "rural",
                    "school_kind": "university",
                    "school_obligation_eligible": False,
                    "access_point_status": "unresolved",
                    "access_point_source_id": None,
                    "access_point_rationale": "Context only.",
                    "geometry": Point(0.035, 0.005),
                },
            ]
        ),
        "boundary": frame(
            [{"geometry": Polygon([(-0.01, -0.01), (0.07, -0.01), (0.07, 0.02), (-0.01, 0.02)])}]
        ),
    }


def test_rural_school_reuses_branch_without_creating_peer_journey_pairs() -> None:
    compiled = compile_network(config(), school_source(), FakeAgentRuntime())

    school_obligations = compiled.access_obligations[
        compiled.access_obligations["obligation_kind"] == "school"
    ].set_index("school_id")
    assert set(school_obligations.index) == {"mapped-school", "unresolved-school"}
    assert school_obligations.loc["mapped-school", "service_status"] == "served"
    assert school_obligations.loc["mapped-school", "access_point_status"] == "mapped"
    assert school_obligations.loc["unresolved-school", "service_status"] == "network-gap"

    school_connections = compiled.spine_access_connections[
        compiled.spine_access_connections["obligation_kind"] == "school"
    ]
    assert school_connections.empty
    mapped = school_obligations.loc["mapped-school"]
    provenance = json.loads(mapped["provenance"])
    assert pd.isna(mapped["access_connection_id"])
    assert provenance["service_kind"] == "backbone-access-association"
    assert provenance["association_kind"] == "colocated-existing-backbone"
    assert provenance["parent_role"] == "spine-access-connection"
    assert mapped["branch_id"] in set(
        compiled.spine_access_connections.loc[
            compiled.spine_access_connections["obligation_kind"] == "community", "branch_id"
        ]
    )
    assert "mapped-school" not in set(compiled.branch_meeting_connections["from_place_id"])
    assert "mapped-school" not in set(compiled.branch_meeting_connections["to_place_id"])
    assert not any(
        connection.parent_role == "school-access-connection"
        for connection in compiled.spine_access_connections.itertuples()
    )

    school_gaps = compiled.gaps[compiled.gaps["network_role"] == "school-access-gap"]
    assert len(school_gaps) == 1
    assert school_gaps.iloc[0]["from_place"] == "unresolved-school"
    assert school_gaps.iloc[0]["access_point_status"] == "unresolved"
    assert "No usable entrance" in school_gaps.iloc[0]["access_point_rationale"]
    assert set(compiled.schools["category"]) >= {"college", "university"}


def test_school_attachment_uses_at_most_one_aggregate_graph_search(
    monkeypatch: object,
) -> None:
    calls = 0
    original = RoadGraph.best_attachment

    def tracked(self: RoadGraph, *args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(RoadGraph, "best_attachment", tracked)  # type: ignore[attr-defined]
    without_obligations = school_source()
    without_obligations["context"].loc[
        without_obligations["context"]["feature_type"] == "school",
        "school_obligation_eligible",
    ] = False
    compile_network(config(), without_obligations, FakeAgentRuntime())
    baseline_calls = calls

    calls = 0
    compile_network(config(), school_source(), FakeAgentRuntime())

    assert 0 <= calls - baseline_calls <= 1


def test_school_access_point_beyond_tight_graph_bound_is_a_gap() -> None:
    source = school_source()
    school_index = source["context"].index[source["context"]["evidence_id"] == "mapped-school"][0]
    source["context"].loc[school_index, "geometry"] = Point(0.02, 0.0003)
    source["context"] = source["context"][
        source["context"]["evidence_id"] != "unresolved-school"
    ].copy()

    compiled = compile_network(config(), source, FakeAgentRuntime())

    assert compiled.spine_access_connections[
        compiled.spine_access_connections["obligation_kind"] == "school"
    ].empty
    gap = compiled.gaps.set_index("school_id").loc["mapped-school"]
    assert "20 metre bound" in gap["selection_reason"]


def test_school_access_point_on_long_edge_routes_from_the_edge_interior() -> None:
    source = school_source()
    school_index = source["context"].index[source["context"]["evidence_id"] == "mapped-school"][0]
    source["context"].loc[school_index, "geometry"] = Point(0.03, 0)
    source["context"] = source["context"][
        source["context"]["evidence_id"] != "unresolved-school"
    ].copy()

    compiled = compile_network(config(), source, FakeAgentRuntime())

    connection = compiled.spine_access_connections[
        compiled.spine_access_connections["obligation_kind"] == "school"
    ].iloc[0]
    assert connection["distance_km"] > 0
    assert len(set(connection.geometry.coords)) > 1
    assert connection["community_attachment_distance_m"] < 1
    assert load_wkt(connection["community_attachment_point"]).x == pytest.approx(0.03, abs=1e-6)
    assert connection["parent_role"] == "spine-access-connection"


def test_school_fallback_forbids_stationary_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = school_source()
    school_index = source["context"].index[source["context"]["evidence_id"] == "mapped-school"][0]
    source["context"].loc[school_index, "geometry"] = Point(0.03, 0)
    source["context"] = source["context"][
        source["context"]["evidence_id"] != "unresolved-school"
    ].copy()
    stationary_controls: list[bool] = []
    original = RoadGraph.best_point_attachment

    def tracked(
        self: RoadGraph,
        *args: object,
        **kwargs: object,
    ) -> object:
        if "allow_stationary" in kwargs:
            stationary_controls.append(bool(kwargs["allow_stationary"]))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(backbone_module, "_direct_school_candidate", lambda *_args: None)
    monkeypatch.setattr(RoadGraph, "best_point_attachment", tracked)

    compiled = compile_network(config(), source, FakeAgentRuntime())

    connection = compiled.spine_access_connections[
        compiled.spine_access_connections["obligation_kind"] == "school"
    ].iloc[0]
    assert stationary_controls == [True]
    assert connection["distance_km"] > 0
    assert len(set(connection.geometry.coords)) > 1


def test_rejected_direct_frontier_falls_through_to_next_direct_frontier() -> None:
    network = frame(
        [
            {
                "osmid": "spine-a-edge",
                "highway": "primary",
                "ref": "A1",
                "geometry": LineString([(0, 0), (0, 0.01)]),
            },
            {
                "osmid": "spine-b-edge",
                "highway": "primary",
                "ref": "A2",
                "geometry": LineString([(0.0001, 0), (0.0001, 0.01)]),
            },
        ]
    )
    graph = RoadGraph(network)
    spines = frame(
        [
            {
                "spine_id": "spine-a",
                "name": "A1",
                "spine_kind": "a-road",
                "evidence_id": "evidence-a",
                "source_id": "spine-a-edge",
                "geometry": LineString([(0, 0), (0, 0.01)]),
            },
            {
                "spine_id": "spine-b",
                "name": "A2",
                "spine_kind": "a-road",
                "evidence_id": "evidence-b",
                "source_id": "spine-b-edge",
                "geometry": LineString([(0.0001, 0), (0.0001, 0.01)]),
            },
        ]
    )
    schools = frame(
        [
            {
                "place_id": "school-between",
                "name": "School Between",
                "kind": "school",
                "place_class": "school",
                "school_kind": "primary",
                "evidence_id": "school-evidence",
                "source_id": "school-source",
                "access_point_status": "mapped",
                "access_point_source_id": "school-entrance",
                "access_point_rationale": "Mapped entrance.",
                "geometry": Point(0.00005, 0.005),
            }
        ]
    )
    empty = gpd.GeoDataFrame(columns=["place_id", "geometry"], geometry="geometry", crs=4326)
    runtime = FakeAgentRuntime(
        {
            AgentRole.DECISION: [
                {"request_id": "$request", "choice_id": "2"}
            ]
        }
    )

    assembly = assemble_backbone_outward(
        empty,
        schools,
        empty,
        spines,
        graph,
        CompilationGate(
            runtime,
            AgentConfig(
                response_mode="direct-runtime",
                review_statuses=(TrafficLight.GREEN,),
            ),
        ),
        15,
    )

    assert len(assembly.connections) == 1
    assert assembly.connections.iloc[0]["parent_target_id"] in {"spine-a", "spine-b"}
    assert assembly.connections.iloc[0]["distance_km"] > 0
    assert len(set(assembly.connections.iloc[0].geometry.coords)) > 1
    assert assembly.connections.iloc[0].geometry.length > 0
    school_records = [
        record
        for record in assembly.agent_records
        if record.connection_id.startswith("spine-access-")
    ]
    assert [record.decision for record in school_records] == ["superseded", "accept"]
    assert len({record.connection_id for record in school_records}) == 2


def test_configured_strategic_destination_participates_in_the_network() -> None:
    source = school_source()
    source["places"] = frame(
        [
            *source["places"].to_dict("records"),
            {
                "place_id": "strategic-college",
                "name": "Strategic College",
                "kind": "strategic_destination",
                "place_class": "college",
                "parent_place_id": None,
                "source_id": "college",
                "geometry": Point(0.03, 0),
            },
        ]
    )

    compiled = compile_network(config(), source, FakeAgentRuntime())

    assert "strategic-college" in set(compiled.places["place_id"])
    assert "strategic-college" not in set(compiled.access_obligations["place_id"])


def test_inferred_school_access_is_served_provisionally_and_never_green_or_red() -> None:
    source = school_source()
    school_index = source["context"].index[source["context"]["evidence_id"] == "mapped-school"][0]
    source["context"].loc[school_index, "access_point_status"] = "inferred"
    source["context"].loc[school_index, "access_point_source_id"] = None
    source["context"].loc[school_index, "access_point_rationale"] = (
        "Inferred from a mapped school boundary/path intersection; requires verification."
    )
    source["context"] = source["context"][
        source["context"]["evidence_id"] != "unresolved-school"
    ].copy()

    compiled = compile_network(config(), source, FakeAgentRuntime())

    obligation = compiled.access_obligations.set_index("school_id").loc["mapped-school"]
    assert obligation["service_status"] == "served-provisional"
    assert obligation["criterion_access_point"] == "amber"
    assert compiled.criteria["spine_network"]["all_access_obligations_resolved"] == "amber"


def test_mapped_but_unreachable_school_is_a_red_network_gap() -> None:
    source = school_source()
    school_index = source["context"].index[source["context"]["evidence_id"] == "mapped-school"][0]
    source["context"].loc[school_index, "geometry"] = Point(1, 1)

    compiled = compile_network(config(), source, FakeAgentRuntime())

    gap = compiled.gaps.set_index("school_id").loc["mapped-school"]
    assert gap["network_role"] == "school-access-gap"
    assert gap["access_point_status"] == "mapped"
    assert gap["criterion_endpoints"] == "red"
    obligation = compiled.access_obligations.set_index("school_id").loc["mapped-school"]
    assert obligation["service_status"] == "network-gap"


def test_legacy_school_point_without_access_evidence_is_not_silently_snapped() -> None:
    source = school_source()
    source["context"] = source["context"].drop(
        columns=[
            "access_point_status",
            "access_point_source_id",
            "access_point_rationale",
        ]
    )

    compiled = compile_network(config(), source, FakeAgentRuntime())

    school_obligations = compiled.access_obligations[
        compiled.access_obligations["obligation_kind"] == "school"
    ]
    assert set(school_obligations["access_point_status"]) == {"unresolved"}
    assert set(school_obligations["service_status"]) == {"network-gap"}
    assert compiled.spine_access_connections[
        compiled.spine_access_connections["obligation_kind"] == "school"
    ].empty


def test_school_state_and_rationale_publish_to_spatial_and_accessible_map_artifacts(
    tmp_path: Path,
) -> None:
    council = config()
    council.publication.output_dir = tmp_path / "output"
    compiled = compile_network(council, school_source(), FakeAgentRuntime())

    artifacts = publish(council, compiled, "run-school-access")

    network = json.loads(artifacts["geojson"].read_text())
    run = json.loads(artifacts["run"].read_text())
    assert run["layer_counts"]["gaps"] == 1
    school_features = [
        feature
        for feature in network["features"]
        if feature["properties"]["feature_type"] == "school-access-obligation"
    ]
    assert len(school_features) == 2
    by_id = {feature["properties"]["school_id"]: feature for feature in school_features}
    assert by_id["mapped-school"]["properties"]["access_point_status"] == "mapped"
    assert "Mapped main entrance" in by_id["mapped-school"]["properties"]["access_point_rationale"]
    assert by_id["unresolved-school"]["properties"]["service_status"] == "network-gap"

    access_layer = gpd.read_file(artifacts["geopackage"], layer="access_obligations")
    assert set(access_layer.loc[access_layer["obligation_kind"] == "school", "school_id"]) == {
        "mapped-school",
        "unresolved-school",
    }
    html = artifacts["review_map"].read_text()
    assert 'id="legend-schools"' in html
    assert "Mapped and served" in html
    assert "Inferred access point" in html
    assert "Unresolved access point" in html
