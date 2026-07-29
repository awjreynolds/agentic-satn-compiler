from __future__ import annotations

import geopandas as gpd
import networkx as nx
import pytest
from shapely.geometry import LineString, Point

from satn.routing import RoadGraph


def test_metric_lower_bound_uses_the_smallest_source_cost_to_geometry_ratio() -> None:
    graph = RoadGraph(
        gpd.GeoDataFrame(
            [
                {
                    "osmid": "forward",
                    "u": "a",
                    "v": "b",
                    "length": 500,
                    "highway": "unclassified",
                    "geometry": LineString([(0, 0), (1000, 0)]),
                },
                {
                    "osmid": "reverse",
                    "u": "b",
                    "v": "a",
                    "length": 500,
                    "highway": "unclassified",
                    "geometry": LineString([(1000, 0), (0, 0)]),
                },
            ],
            geometry="geometry",
            crs=27700,
        )
    )

    assert graph.lower_bound_cost_factor == pytest.approx(0.5)
    assert graph.lower_bound_disabled_reason is None
    assert graph.attachment_lower_bound_cost_factor == pytest.approx(0.5)
    assert graph.attachment_lower_bound_disabled_reason is None
    assert graph.lower_bound_to_geometry_m(Point(0, 0), LineString([(1000, 0), (1000, 1)])) == (
        pytest.approx(500)
    )


def test_attachment_group_distance_bounds_are_exact_zero_snap_costs() -> None:
    graph = RoadGraph(
        gpd.GeoDataFrame(
            [
                {
                    "osmid": "forward",
                    "u": "a",
                    "v": "b",
                    "length": 500,
                    "highway": "unclassified",
                    "geometry": LineString([(0, 0), (1000, 0)]),
                },
                {
                    "osmid": "reverse",
                    "u": "b",
                    "v": "a",
                    "length": 500,
                    "highway": "unclassified",
                    "geometry": LineString([(1000, 0), (0, 0)]),
                },
                {
                    "osmid": "tail-forward",
                    "u": "b",
                    "v": "c",
                    "length": 700,
                    "highway": "unclassified",
                    "geometry": LineString([(1000, 0), (2000, 0)]),
                },
                {
                    "osmid": "tail-reverse",
                    "u": "c",
                    "v": "b",
                    "length": 700,
                    "highway": "unclassified",
                    "geometry": LineString([(2000, 0), (1000, 0)]),
                },
            ],
            geometry="geometry",
            crs=27700,
        )
    )

    bounds, unroutable_pairs, diagnostics = graph.attachment_group_distance_bounds(
        {"left": ("a",), "middle": ("b",), "right": ("c",), "missing": ("x",)}
    )

    assert bounds == {
        ("left", "middle"): 500.0,
        ("left", "right"): 1200.0,
        ("middle", "right"): 700.0,
    }
    assert unroutable_pairs == {
        ("left", "missing"),
        ("middle", "missing"),
        ("missing", "right"),
    }
    assert graph.best_attachment(
        [("a", 0.0)], [("x", 0.0)], allow_stationary=False
    ) is None
    assert diagnostics == {
        "root_group_distance_planning_searches": 2,
        "root_group_distance_planning_nodes_settled": 6,
    }


def test_attachment_search_continues_to_distinct_nodes_when_stationary_is_forbidden() -> None:
    graph = RoadGraph(
        gpd.GeoDataFrame(
            [
                {
                    "osmid": "forward",
                    "u": "a",
                    "v": "b",
                    "length": 100,
                    "highway": "unclassified",
                    "geometry": LineString([(0, 0), (100, 0)]),
                },
                {
                    "osmid": "reverse",
                    "u": "b",
                    "v": "a",
                    "length": 100,
                    "highway": "unclassified",
                    "geometry": LineString([(100, 0), (0, 0)]),
                },
            ],
            geometry="geometry",
            crs=27700,
        )
    )

    attachment = graph.best_attachment(
        [("a", 0.0), ("b", 10.0)],
        [("a", 0.0)],
        allow_stationary=False,
    )

    assert attachment is not None
    assert (attachment.start_node, attachment.end_node) == ("b", "a")
    assert attachment.option.length_km == pytest.approx(0.1)
    assert attachment.option.geometry.length == pytest.approx(100)


def test_point_attachment_continues_to_distinct_nodes_when_stationary_is_forbidden() -> None:
    graph = RoadGraph(
        gpd.GeoDataFrame(
            [
                {
                    "osmid": "forward",
                    "u": "a",
                    "v": "b",
                    "length": 100,
                    "highway": "unclassified",
                    "geometry": LineString([(0, 0), (100, 0)]),
                },
                {
                    "osmid": "reverse",
                    "u": "b",
                    "v": "a",
                    "length": 100,
                    "highway": "unclassified",
                    "geometry": LineString([(100, 0), (0, 0)]),
                },
            ],
            geometry="geometry",
            crs=27700,
        )
    )

    attachment = graph.best_point_attachment(
        Point(0, 0),
        120,
        [("a", 0.0)],
        allow_stationary=False,
    )

    assert attachment is not None
    assert attachment.start_node != attachment.end_node
    assert attachment.total_distance_km > 0
    assert len(set(attachment.option.geometry.coords)) > 1


def test_metric_lower_bound_falls_back_to_zero_for_noncanonical_endpoints() -> None:
    graph = RoadGraph(
        gpd.GeoDataFrame(
            [
                {
                    "osmid": "canonical",
                    "u": "a",
                    "v": "b",
                    "length": 1000,
                    "highway": "unclassified",
                    "geometry": LineString([(0, 0), (1000, 0)]),
                },
                {
                    "osmid": "mismatched",
                    "u": "a",
                    "v": "c",
                    "length": 1000,
                    "highway": "unclassified",
                    "geometry": LineString([(10, 0), (0, 1000)]),
                },
                {
                    "osmid": "canonical-reverse",
                    "u": "b",
                    "v": "a",
                    "length": 1000,
                    "highway": "unclassified",
                    "geometry": LineString([(1000, 0), (0, 0)]),
                },
            ],
            geometry="geometry",
            crs=27700,
        )
    )

    assert graph.lower_bound_cost_factor == 0.0
    assert graph.lower_bound_disabled_reason == "non-canonical-edge-endpoints"
    # The mismatched edge is not reciprocal and therefore cannot participate
    # in an attachment/meeting route.  The scoped bound remains sound and
    # stronger for that route graph.
    assert graph.attachment_lower_bound_cost_factor == pytest.approx(1.0)
    assert graph.attachment_lower_bound_disabled_reason is None
    assert graph.lower_bound_to_geometry_m(Point(0, 0), Point(1000, 0)) == 0.0


def test_dominant_routable_component_avoids_nearby_isolated_fragment() -> None:
    rows = []
    for index in range(20):
        rows.append(
            {
                "osmid": f"main-{index}",
                "highway": "unclassified",
                "geometry": LineString([(index * 0.01, 0), ((index + 1) * 0.01, 0)]),
            }
        )
    rows.append(
        {
            "osmid": "isolated",
            "highway": "path",
            "geometry": LineString([(0.05, 0.001), (0.051, 0.001)]),
        }
    )
    graph = RoadGraph(gpd.GeoDataFrame(rows, geometry="geometry", crs=4326))

    node, _ = graph.nearest_node(Point(0.05, 0.001))

    assert node not in {
        "xy:0.0500000:0.0010000",
        "xy:0.0510000:0.0010000",
    }


def test_nearest_node_breaks_exact_distance_ties_by_stable_node_id() -> None:
    rows = [
        {
            "osmid": "left",
            "highway": "unclassified",
            "geometry": LineString([(-2, 0), (-1, 0)]),
        },
        {
            "osmid": "right",
            "highway": "unclassified",
            "geometry": LineString([(1, 0), (2, 0)]),
        },
    ]
    forward = RoadGraph(gpd.GeoDataFrame(rows, geometry="geometry", crs=27700))
    reverse = RoadGraph(gpd.GeoDataFrame(list(reversed(rows)), geometry="geometry", crs=27700))

    assert forward.nearest_node(Point(0, 0)) == reverse.nearest_node(Point(0, 0))


def test_dense_attachment_uses_one_path_search_without_growing_route_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "osmid": f"edge-{coordinate}",
            "highway": "unclassified",
            "geometry": LineString([(coordinate, 0), (coordinate + 1, 0)]),
        }
        for coordinate in range(-100, 200)
    ]
    graph = RoadGraph(gpd.GeoDataFrame(rows, geometry="geometry", crs=27700))
    starts = graph.nodes_near(Point(0, 0), 100)
    searches = 0
    original = nx.single_source_dijkstra

    def counted_search(*args: object, **kwargs: object) -> object:
        nonlocal searches
        searches += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(nx, "single_source_dijkstra", counted_search)
    attachment = graph.best_attachment(
        starts,
        [("xy:200.0000000:0.0000000", 0.0)],
    )

    assert len(starts) == 201
    assert attachment is not None
    assert searches == 1
    assert graph._shortest_lengths == {}


def test_attachment_search_continues_after_shorter_asymmetric_corridor() -> None:
    rows = [
        {
            "osmid": "short-forward",
            "u": "short-start",
            "v": "end",
            "length": 2000,
            "highway": "unclassified",
            "geometry": LineString([(0, 0), (2000, 0)]),
        },
        {
            "osmid": "return-up",
            "u": "end",
            "v": "return-one",
            "length": 2000,
            "highway": "unclassified",
            "geometry": LineString([(2000, 0), (2000, 2000)]),
        },
        {
            "osmid": "return-across",
            "u": "return-one",
            "v": "return-two",
            "length": 2000,
            "highway": "unclassified",
            "geometry": LineString([(2000, 2000), (0, 2000)]),
        },
        {
            "osmid": "return-down",
            "u": "return-two",
            "v": "short-start",
            "length": 2000,
            "highway": "unclassified",
            "geometry": LineString([(0, 2000), (0, 0)]),
        },
        {
            "osmid": "long-forward",
            "u": "valid-start",
            "v": "end",
            "length": 4500,
            "highway": "unclassified",
            "geometry": LineString([(-2000, -2000), (2000, 0)]),
        },
        {
            "osmid": "long-reverse",
            "u": "end",
            "v": "valid-start",
            "length": 4500,
            "highway": "unclassified",
            "geometry": LineString([(2000, 0), (-2000, -2000)]),
        },
    ]
    graph = RoadGraph(gpd.GeoDataFrame(rows, geometry="geometry", crs=27700))

    attachment = graph.best_attachment(
        [("short-start", 0.0), ("valid-start", 0.0)],
        [("end", 0.0)],
    )

    assert attachment is not None
    assert attachment.start_node == "valid-start"
    assert attachment.option.bidirectional
