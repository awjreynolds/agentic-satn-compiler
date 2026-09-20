from __future__ import annotations

import geopandas as gpd
import pandas as pd
from bath_saltford_fixture import configured_bath_saltford
from shapely.geometry import LineString, Point, Polygon
from shapely.wkt import loads as load_wkt

from satn.agents import FakeAgentRuntime
from satn.compiler import compile_network
from satn.effective_strategic_network import (
    EffectiveStrategicNetworkRequest,
    compile_effective_strategic_network,
)
from satn.identifiers import coordinate_key
from satn.network_selection import CandidateSourceClass
from satn.routing import RoadGraph
from satn.sources import load_snapshot, snapshot
from satn.urban_journeys import prepare_urban_journeys


def _graph() -> RoadGraph:
    rows = []
    coordinates = {"a": 0.0, "b": 1.0, "c": 2.0, "d": 10.0, "e": 11.0}
    for source_id, left, right, highway, ref in (
        ("a4", "a", "b", "primary", "A4"),
        ("cycle", "b", "c", "cycleway", None),
        ("local", "d", "e", "unclassified", None),
    ):
        geometry = LineString([(coordinates[left], 0.0), (coordinates[right], 0.0)])
        for u, v, directed_geometry in (
            (left, right, geometry),
            (right, left, LineString(list(geometry.coords)[::-1])),
        ):
            rows.append(
                {
                    "osmid": f"{source_id}-{u}-{v}",
                    "u": u,
                    "v": v,
                    "oneway": False,
                    "highway": highway,
                    "ref": ref,
                    "geometry": directed_geometry,
                }
            )
    return RoadGraph(gpd.GeoDataFrame(rows, geometry="geometry", crs=27700))


def _places() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    places = gpd.GeoDataFrame(
        [
            {"element": "node", "id": 1, "place": "town", "name": "Alpha", "geometry": Point(0, 0)},
            {"element": "node", "id": 2, "place": "town", "name": "Beta", "geometry": Point(1, 0)},
            {
                "element": "relation",
                "id": 2,
                "place": "town",
                "name": "Beta relation",
                "geometry": Point(1, 0),
            },
            {"element": "node", "id": 3, "place": "town", "name": "Gamma", "geometry": Point(2, 0)},
            {
                "element": "node",
                "id": 4,
                "place": "village",
                "name": "Village",
                "geometry": Point(1, 1),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    area = gpd.GeoDataFrame(
        [{"geometry": Polygon([(-1, -1), (12, -1), (12, 1), (-1, 1), (-1, -1)])}],
        geometry="geometry",
        crs=27700,
    )
    return places, area


def test_public_urban_preparation_deduplicates_places_and_retains_full_adjacency() -> None:
    places, area = _places()

    result = prepare_urban_journeys(label_places=places, area_definition=area, road_graph=_graph())

    assert [place.name for place in result.places] == ["Alpha", "Beta", "Gamma"]
    assert [place.source_id for place in result.places] == ["node/1", "node/2", "node/3"]
    assert len(result.adjacencies) == 2
    by_names = {frozenset(item.place_names): item for item in result.adjacencies}
    assert by_names[frozenset(("Alpha", "Beta"))].preferred_classes == (
        "a-road-reference",
        "a-road-highway",
    )
    assert by_names[frozenset(("Beta", "Gamma"))].preferred_classes == ("cycleway",)


def test_public_urban_preparation_reports_unbound_place_without_cartesian_pair() -> None:
    places, area = _places()
    disconnected = places.copy()
    disconnected.loc[len(disconnected)] = {
        "element": "node",
        "id": 5,
        "place": "town",
        "name": "Delta",
        "geometry": Point(10, 0),
    }

    result = prepare_urban_journeys(
        label_places=disconnected,
        area_definition=area,
        road_graph=_graph(),
    )

    assert all("Delta" not in adjacency.place_names for adjacency in result.adjacencies)
    assert any(issue.reason == "urban-place-no-cross-region-adjacency" for issue in result.issues)


def test_public_urban_preparation_anchors_nearby_place_to_dominant_component() -> None:
    places, area = _places()
    isolated = places.iloc[[0]].copy()
    isolated.loc[len(isolated)] = {
        "element": "node",
        "id": 5,
        "place": "town",
        "name": "Delta",
        "geometry": Point(10, 0),
    }

    anchored = prepare_urban_journeys(
        label_places=isolated,
        area_definition=area,
        road_graph=_graph(),
        urban_scope_buffer_m=100,
    )
    delta = next(place for place in anchored.places if place.name == "Delta")
    assert delta.routing_node_id == "c"
    assert all(issue.source_id != "node/5" for issue in anchored.issues)

    beyond_extent = prepare_urban_journeys(
        label_places=isolated,
        area_definition=area,
        road_graph=_graph(),
        urban_scope_buffer_m=1,
    )
    delta = next(place for place in beyond_extent.places if place.name == "Delta")
    assert delta.routing_node_id == "d"
    assert any(issue.source_id == "node/5" for issue in beyond_extent.issues)


def test_public_urban_preparation_keeps_remote_place_on_local_component() -> None:
    rows = []
    for index in range(18):
        left = f"main-{index}"
        right = f"main-{index + 1}"
        geometry = LineString([(float(index), 0.0), (float(index + 1), 0.0)])
        for u, v, directed_geometry in (
            (left, right, geometry),
            (right, left, LineString(list(geometry.coords)[::-1])),
        ):
            rows.append(
                {
                    "osmid": f"main-{index}-{u}-{v}",
                    "u": u,
                    "v": v,
                    "oneway": False,
                    "highway": "primary",
                    "geometry": directed_geometry,
                }
            )
    for u, v, geometry in (
        (
            "remote-a",
            "remote-b",
            LineString([(1000.0, 0.0), (1001.0, 0.0)]),
        ),
        (
            "remote-b",
            "remote-a",
            LineString([(1001.0, 0.0), (1000.0, 0.0)]),
        ),
    ):
        rows.append(
            {
                "osmid": f"remote-{u}-{v}",
                "u": u,
                "v": v,
                "oneway": False,
                "highway": "unclassified",
                "geometry": geometry,
            }
        )
    road_graph = RoadGraph(gpd.GeoDataFrame(rows, geometry="geometry", crs=27700))
    labels = gpd.GeoDataFrame(
        [
            {
                "element": "node",
                "id": 6,
                "place": "town",
                "name": "Remote",
                "geometry": Point(1000, 0),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    area = gpd.GeoDataFrame(
        [{"geometry": Polygon([(-1, -1), (1100, -1), (1100, 1), (-1, 1), (-1, -1)])}],
        geometry="geometry",
        crs=27700,
    )

    result = prepare_urban_journeys(
        label_places=labels,
        area_definition=area,
        road_graph=road_graph,
        urban_scope_buffer_m=100,
    )

    remote = next(place for place in result.places if place.name == "Remote")
    assert remote.routing_node_id == "remote-a"
    assert any(issue.source_id == "node/6" for issue in result.issues)


def test_public_compile_passes_configured_urban_scope_to_city_anchors(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    config.source.urban_scope_buffer_km = 1.0
    snapshot(config)
    source = load_snapshot(config)

    labels = source["label_places"].copy()
    labels["kind"] = "town"
    labels["element"] = "node"
    labels["id"] = ["bath", "saltford"]
    labels["name"] = ["Bath", "Saltford"]
    labels.loc[len(labels)] = {
        "element": "node",
        "id": "yate",
        "kind": "town",
        "name": "Yate",
        "geometry": Point(-2.395, 51.395),
    }
    source["label_places"] = gpd.GeoDataFrame(labels, geometry="geometry", crs=labels.crs)

    network = source["network"].copy()
    isolated_rows = []
    for source_id, start, end, geometry in (
        (
            "isolated-yate-forward",
            "yate-a",
            "yate-b",
            LineString([(-2.395, 51.395), (-2.396, 51.395)]),
        ),
        (
            "isolated-yate-reverse",
            "yate-b",
            "yate-a",
            LineString([(-2.396, 51.395), (-2.395, 51.395)]),
        ),
    ):
        row = network.iloc[0].copy()
        row["source_id"] = source_id
        row["u"] = start
        row["v"] = end
        row["highway"] = "unclassified"
        row["ref"] = None
        row["name"] = "Synthetic isolated Yate edge"
        row["geometry"] = geometry
        isolated_rows.append(row)
    source["network"] = gpd.GeoDataFrame(
        pd.concat(
            [
                network,
                gpd.GeoDataFrame(isolated_rows, geometry="geometry", crs=network.crs),
            ],
            ignore_index=True,
        ),
        geometry="geometry",
        crs=network.crs,
    )

    compiled = compile_network(config, source, FakeAgentRuntime())
    preparation = compiled.strategic_corridor_preparation
    assert preparation is not None and preparation.urban_journeys is not None
    yate = next(
        place for place in preparation.urban_journeys.places if place.source_id == "node/yate"
    )
    assert yate.routing_node_id != "yate-a"
    assert yate.routing_node_id != "yate-b"


def test_public_compile_uses_city_town_labels_and_mesh_keeps_selected_journey(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    source = load_snapshot(config)
    labels = source["label_places"].copy()
    labels["kind"] = "town"
    labels["element"] = "node"
    labels["id"] = ["bath", "saltford"]
    labels["name"] = ["Bath", "Saltford"]
    source["label_places"] = labels
    a_geometry = (
        source["network"]
        .loc[source["network"]["source_id"].eq("a4-bath-saltford-forward"), "geometry"]
        .iloc[0]
    )
    source["official_road_classification"] = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "synthetic-a4-bath-saltford",
                "official_classification": "a-road",
                "official_road_number": "A4",
                "official_road_function": "primary",
                "source_id": "synthetic-official-roads",
                "geometry": a_geometry,
            }
        ],
        geometry="geometry",
        crs=source["network"].crs,
    )

    compiled = compile_network(config, source, FakeAgentRuntime())
    preparation = compiled.strategic_corridor_preparation
    assert preparation is not None
    assert preparation.urban_journeys is not None
    assert [place.name for place in preparation.urban_journeys.places] == ["Bath", "Saltford"]
    urban_units = [unit for unit in preparation.units if unit.urban_journey_id]
    assert len(urban_units) == 1
    urban_unit = urban_units[0]
    assert urban_unit.anchor_connection_ids == ()
    assert urban_unit.anchor_obligation_ids == ()
    assert any(
        unit.unit_role.value == "a-road-backbone" and unit.backbone_required
        for unit in preparation.units
    )
    assert {candidate.source_class.value for candidate in urban_unit.candidate_set.candidates} == {
        "a-road-corridor",
        "verified-existing-asset",
    }

    selected = [
        item
        for item in compiled.strategic_network_planning.selections
        if item.obligation_id == urban_unit.unit_id
    ]
    assert len(selected) == 1
    assert any(
        section.candidate_id == selected[0].effective_candidate_id
        and section.network_scope == "rural"
        for section in compiled.strategic_network_planning.effective_network.sections
    )


def test_urban_journey_identity_survives_candidate_profile_changes(tmp_path) -> None:
    first_config = configured_bath_saltford(tmp_path / "first")
    snapshot(first_config)
    first_source = load_snapshot(first_config)
    legacy = compile_network(first_config, first_source, FakeAgentRuntime())
    legacy_interurban = [
        unit
        for unit in legacy.strategic_corridor_preparation.units
        if unit.unit_role.value == "interurban-spine" and unit.urban_journey_id is None
    ]
    assert legacy_interurban
    assert all(unit.network_scope == "rural" for unit in legacy_interurban)

    def city_labels(source):
        labels = source["label_places"].copy()
        labels["kind"] = "town"
        labels["element"] = "node"
        labels["id"] = ["bath", "saltford"]
        labels["name"] = ["Bath", "Saltford"]
        source["label_places"] = labels

    city_labels(first_source)
    first = compile_network(first_config, first_source, FakeAgentRuntime())
    first_unit = next(
        unit for unit in first.strategic_corridor_preparation.units if unit.urban_journey_id
    )

    second_config = configured_bath_saltford(tmp_path / "second")
    second_config.compilation.network_selection = (
        second_config.compilation.network_selection.model_copy(
            update={
                "candidate_source_precedence": (
                    CandidateSourceClass.A_ROAD_CORRIDOR,
                    CandidateSourceClass.VERIFIED_EXISTING_ASSET,
                    CandidateSourceClass.OTHER_ROUTABLE,
                )
            }
        )
    )
    snapshot(second_config)
    second_source = load_snapshot(second_config)
    city_labels(second_source)
    second = compile_network(second_config, second_source, FakeAgentRuntime())
    second_unit = next(
        unit for unit in second.strategic_corridor_preparation.units if unit.urban_journey_id
    )

    assert first_unit.urban_journey_id == second_unit.urban_journey_id
    assert first_unit.unit_id == second_unit.unit_id
    assert first_unit.candidate_set.candidate_set_fingerprint != (
        second_unit.candidate_set.candidate_set_fingerprint
    )


def test_public_effective_compile_keeps_journey_under_mandatory_a_pressure(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    source = load_snapshot(config)
    labels = source["label_places"].copy()
    labels["kind"] = "town"
    labels["element"] = "node"
    labels["id"] = ["bath", "saltford"]
    labels["name"] = ["Bath", "Saltford"]
    source["label_places"] = labels
    network = source["network"].copy()
    network["u"] = [coordinate_key(tuple(row.geometry.coords[0])) for _, row in network.iterrows()]
    network["v"] = [coordinate_key(tuple(row.geometry.coords[-1])) for _, row in network.iterrows()]
    source["network"] = network
    compiled = compile_network(config, source, FakeAgentRuntime())
    preparation = compiled.strategic_corridor_preparation
    urban_unit = next(unit for unit in preparation.units if unit.urban_journey_id)
    prepared_candidate_ids = {item.candidate_id for item in urban_unit.candidate_set.candidates}

    mandatory_a = gpd.GeoDataFrame(
        [
            {
                "structure_id": "urban-long-a-pressure",
                "official_classification": "a-road",
                "geometry": LineString(
                    [
                        (-2.39, 51.39),
                        (-2.39, 51.391),
                        (-2.405, 51.405),
                        (-2.43, 51.401),
                        (-2.43, 51.4),
                    ]
                ),
            }
        ],
        geometry="geometry",
        crs=source["network"].crs,
    )
    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=source["network"],
            preparation=preparation,
            area_fingerprint="a" * 64,
            snapshot_fingerprint="b" * 64,
            urban_spines=mandatory_a,
        )
    )
    assert state.is_evaluated
    result = state.result
    assert result is not None
    selected = next(item for item in result.selections if item.obligation_id == urban_unit.unit_id)
    assert selected.effective_candidate_id not in prepared_candidate_ids
    journey_section = next(
        section
        for section in result.effective_network.sections
        if section.obligation_id == urban_unit.unit_id
    )
    a_section = next(
        section
        for section in result.effective_network.sections
        if section.obligation_id == "urban-structure:urban-long-a-pressure"
    )
    assert journey_section.network_scope == "rural"
    assert a_section.network_role == "urban-main-road-spine"
    assert load_wkt(a_section.geometry_wkt).intersects(load_wkt(journey_section.geometry_wkt))
