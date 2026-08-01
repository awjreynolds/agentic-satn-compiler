from __future__ import annotations

from itertools import combinations
from time import perf_counter

import geopandas as gpd
import networkx as nx
import pytest
from shapely.geometry import LineString, Point

import satn.routing as routing
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


def test_road_graph_exposes_canonical_edge_ids_and_projected_nodes() -> None:
    graph = RoadGraph(
        gpd.GeoDataFrame(
            [
                {
                    "osmid": "a-road",
                    "u": "a",
                    "v": "b",
                    "length": 100,
                    "ref": "A4",
                    "geometry": LineString([(0, 0), (100, 0)]),
                },
            ],
            geometry="geometry",
            crs=27700,
        )
    )

    assert graph.edge_id_attribute == "edge_id"
    assert graph.edge_ids_for_node("a") == ("a-road",)
    assert graph.references_for_edge_ids(("a-road", "missing")) == ("A4",)
    assert graph.projected_node("a") == Point(0, 0)
    assert graph.projected_node("missing") is None


def test_batched_routes_preserve_asymmetric_one_way_and_equal_cost_options() -> None:
    graph = RoadGraph(
        gpd.GeoDataFrame(
            [
                {
                    "osmid": "c-t",
                    "u": "c",
                    "v": "t",
                    "length": 1,
                    "geometry": LineString([(2, 0), (3, 0)]),
                },
                {
                    "osmid": "d-t",
                    "u": "d",
                    "v": "t",
                    "length": 2,
                    "geometry": LineString([(1, 1), (3, 0)]),
                },
                {
                    "osmid": "s-c",
                    "u": "s",
                    "v": "c",
                    "length": 2,
                    "geometry": LineString([(0, 0), (2, 0)]),
                },
                {
                    "osmid": "s-d",
                    "u": "s",
                    "v": "d",
                    "length": 1,
                    "geometry": LineString([(0, 0), (1, 1)]),
                },
                {
                    "osmid": "t-s-one-way",
                    "u": "t",
                    "v": "s",
                    "length": 7,
                    "geometry": LineString([(3, 0), (0, 0)]),
                },
            ],
            geometry="geometry",
            crs=27700,
        )
    )
    roles = ("direct", "strategic-spine")
    expected = {
        role: graph.option("s", "t", role, strategic_use=True)
        for role in roles
    }

    assert expected["direct"] is not None
    assert expected["direct"].edge_ids == ["s-c", "c-t"]
    assert expected["direct"].reverse_edge_ids == ["t-s-one-way"]

    routed, search_count = graph.route_options_for_pairs(
        (("s", "t"),),
        roles=roles,
        strategic_use=True,
    )

    # Two role/start traversals, two target-rooted tie traces, and two
    # one-way reverse-route traversals are all reported.
    assert search_count == 6
    for role, option in expected.items():
        actual = routed[("s", "t")][role]
        assert actual is not None and option is not None
        assert actual.edge_ids == option.edge_ids
        assert actual.reverse_edge_ids == option.reverse_edge_ids
        assert actual.geometry.wkb_hex == option.geometry.wkb_hex
        assert actual.bidirectional is option.bidirectional


def test_equal_cost_ties_count_each_unique_legacy_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows: list[dict[str, object]] = []
    expected_geometries: dict[str, LineString] = {}
    pairs: list[tuple[str, str]] = []
    for index in range(8):
        target = f"t-{index:02d}"
        c_node = f"c-{index:02d}"
        d_node = f"d-{index:02d}"
        c_point = (2, index * 10)
        d_point = (1, index * 10 + 1)
        target_point = (3, index * 10)
        expected_geometries[target] = LineString([(0, 0), c_point, target_point])
        pairs.append(("s", target))
        rows.extend(
            (
                {
                    "osmid": f"{c_node}-{target}",
                    "u": c_node,
                    "v": target,
                    "length": 1,
                    "geometry": LineString([c_point, target_point]),
                },
                {
                    "osmid": f"{target}-{c_node}",
                    "u": target,
                    "v": c_node,
                    "length": 1,
                    "geometry": LineString([target_point, c_point]),
                },
                {
                    "osmid": f"{d_node}-{target}",
                    "u": d_node,
                    "v": target,
                    "length": 2,
                    "geometry": LineString([d_point, target_point]),
                },
                {
                    "osmid": f"{target}-{d_node}",
                    "u": target,
                    "v": d_node,
                    "length": 2,
                    "geometry": LineString([target_point, d_point]),
                },
                {
                    "osmid": f"s-{c_node}",
                    "u": "s",
                    "v": c_node,
                    "length": 2,
                    "geometry": LineString([(0, 0), c_point]),
                },
                {
                    "osmid": f"{c_node}-s",
                    "u": c_node,
                    "v": "s",
                    "length": 2,
                    "geometry": LineString([c_point, (0, 0)]),
                },
                {
                    "osmid": f"s-{d_node}",
                    "u": "s",
                    "v": d_node,
                    "length": 1,
                    "geometry": LineString([(0, 0), d_point]),
                },
                {
                    "osmid": f"{d_node}-s",
                    "u": d_node,
                    "v": "s",
                    "length": 1,
                    "geometry": LineString([d_point, (0, 0)]),
                },
            )
        )
    graph = RoadGraph(gpd.GeoDataFrame(rows, geometry="geometry", crs=27700))
    trace_roots: list[tuple[str, bool]] = []
    original_trace = routing._dijkstra_trace

    def counting_trace(
        route_graph: nx.DiGraph,
        root: str,
        weight: object,
        *,
        reverse: bool,
    ) -> tuple[routing._DijkstraTraceEvent, ...]:
        trace_roots.append((root, reverse))
        return original_trace(route_graph, root, weight, reverse=reverse)  # type: ignore[arg-type]

    monkeypatch.setattr(routing, "_dijkstra_trace", counting_trace)

    def unexpected_single_source(*_args: object, **_kwargs: object) -> None:
        pytest.fail("grouped routing must not hide an uncounted NetworkX traversal")

    monkeypatch.setattr(nx, "single_source_dijkstra", unexpected_single_source)

    routed, search_count = graph.route_options_for_pairs(
        pairs,
        roles=("direct",),
        strategic_use=True,
    )

    assert search_count == len(trace_roots) == 9
    assert trace_roots == [
        ("s", False),
        *((f"t-{index:02d}", True) for index in range(8)),
    ]
    for _start, target in pairs:
        option = routed[("s", target)]["direct"]
        assert option is not None
        assert option.edge_ids == [f"s-c-{target[-2:]}", f"c-{target[-2:]}-{target}"]
        assert option.reverse_edge_ids == [
            f"{target}-c-{target[-2:]}",
            f"c-{target[-2:]}-s",
        ]
        assert option.geometry.equals_exact(expected_geometries[target], tolerance=0)


def test_dense_tied_pairs_cache_traversals_by_unique_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor_count = 8
    rows: list[dict[str, object]] = []
    for index in range(anchor_count):
        anchor = f"a-{index:02d}"
        anchor_point = (index * 10, 0)
        for hub, hub_point in (("c", (0, 10)), ("d", (0, -10))):
            rows.extend(
                (
                    {
                        "osmid": f"{anchor}-{hub}",
                        "u": anchor,
                        "v": hub,
                        "length": 1,
                        "geometry": LineString([anchor_point, hub_point]),
                    },
                    {
                        "osmid": f"{hub}-{anchor}",
                        "u": hub,
                        "v": anchor,
                        "length": 1,
                        "geometry": LineString([hub_point, anchor_point]),
                    },
                )
            )
    graph = RoadGraph(gpd.GeoDataFrame(rows, geometry="geometry", crs=27700))
    anchors = tuple(f"a-{index:02d}" for index in range(anchor_count))
    pairs = tuple(combinations(anchors, 2))
    expected = {
        pair: graph.option(*pair, "direct", strategic_use=True)
        for pair in pairs
    }
    trace_roots: list[tuple[str, bool]] = []
    original_trace = routing._dijkstra_trace

    def counting_trace(
        route_graph: nx.DiGraph,
        root: str,
        weight: object,
        *,
        reverse: bool,
    ) -> tuple[routing._DijkstraTraceEvent, ...]:
        trace_roots.append((root, reverse))
        return original_trace(route_graph, root, weight, reverse=reverse)  # type: ignore[arg-type]

    monkeypatch.setattr(routing, "_dijkstra_trace", counting_trace)

    routed, search_count = graph.route_options_for_pairs(
        pairs,
        roles=("direct",),
        strategic_use=True,
    )

    assert len(pairs) == anchor_count * (anchor_count - 1) // 2
    assert search_count == len(trace_roots) == 2 * (anchor_count - 1)
    assert len(set(trace_roots)) == len(trace_roots)
    for pair, legacy in expected.items():
        actual = routed[pair]["direct"]
        assert actual is not None and legacy is not None
        assert actual.edge_ids == legacy.edge_ids
        assert actual.reverse_edge_ids == legacy.reverse_edge_ids
        assert actual.geometry.wkb_hex == legacy.geometry.wkb_hex


@pytest.mark.parametrize("anchor_count", (10, 25, 50))
def test_batched_anchor_benchmark_records_search_count_and_elapsed_time(
    anchor_count: int,
) -> None:
    rows: list[dict[str, object]] = []
    for index in range(anchor_count - 1):
        rows.extend(
            (
                {
                    "osmid": f"forward-{index}",
                    "u": f"node-{index:02d}",
                    "v": f"node-{index + 1:02d}",
                    "length": 100,
                    "geometry": LineString([(index * 100, 0), ((index + 1) * 100, 0)]),
                },
                {
                    "osmid": f"reverse-{index}",
                    "u": f"node-{index + 1:02d}",
                    "v": f"node-{index:02d}",
                    "length": 100,
                    "geometry": LineString([((index + 1) * 100, 0), (index * 100, 0)]),
                },
            )
        )
    graph = RoadGraph(gpd.GeoDataFrame(rows, geometry="geometry", crs=27700))
    anchors = tuple(f"node-{index:02d}" for index in range(anchor_count))
    pairs = tuple(combinations(anchors, 2))

    started_at = perf_counter()
    options, search_count = graph.route_options_for_pairs(
        pairs,
        roles=("direct",),
        strategic_use=True,
    )
    elapsed_seconds = perf_counter() - started_at

    assert len(pairs) == anchor_count * (anchor_count - 1) // 2
    assert search_count == anchor_count - 1
    assert options[pairs[-1]]["direct"] is not None
    assert elapsed_seconds >= 0


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
