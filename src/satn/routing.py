"""OSM network graph construction and alignment option routing."""

from __future__ import annotations

import heapq
import json
import logging
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import LineString, Point
from shapely.ops import linemerge, substring, unary_union

from satn.content_identity import (
    canonical_network_geometry_fingerprint,
    content_fingerprint,
)
from satn.route_controls import (
    DirectedEdgeBinding,
    EdgeBindingMode,
    RouteControlNetworkGap,
    RouteControlSet,
    RouteEdgeBinding,
)
from satn.tags import source_identity
from satn.tags import tag_values as _tag_values

LOW_TRAFFIC = {
    "living_street",
    "residential",
    "unclassified",
    "service",
    "track",
    "path",
    "cycleway",
}
MAIN_ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary"}

LOGGER = logging.getLogger(__name__)
ATTACHMENT_TIE_BREAK_EPSILON = 1e-9
_REVERSE_PATH_UNSET = object()


def _directed_edge_identity(
    source_edge_id: str,
    from_node_id: str,
    to_node_id: str,
    geometry: LineString,
    *,
    duplicate_source_id: bool,
    crs: object,
) -> str:
    """Return a stable identity for one directed source segment.

    Source OSM way IDs are not edge identities: one way can cover many directed
    segments. Preserve the established source ID when it is unique, and add a
    deterministic segment suffix only when disambiguation is required.
    """

    if not duplicate_source_id:
        return source_edge_id
    return f"{source_edge_id}#{
        content_fingerprint(
            {
                'contract': 'satn-routing-directed-edge/v1',
                'source_edge_id': source_edge_id,
                'from_node_id': from_node_id,
                'to_node_id': to_node_id,
                'geometry_fingerprint': canonical_network_geometry_fingerprint(geometry, crs),
            }
        )[:20]
    }"


@dataclass
class RouteOption:
    role: str
    geometry: LineString
    length_km: float
    edge_ids: list[str]
    a_road_share: float
    ncn_share: float
    bidirectional: bool
    reverse_length_km: float | None
    reverse_edge_ids: list[str]
    reverse_corridor_share: float
    impracticable_alongside: bool
    directed_edge_ids: list[str] = field(default_factory=list)
    reverse_directed_edge_ids: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        return {
            "role": self.role,
            "length_km": round(self.length_km, 3),
            "a_road_share": round(self.a_road_share, 3),
            "ncn_share": round(self.ncn_share, 3),
            "bidirectional": self.bidirectional,
            "reverse_length_km": (
                round(self.reverse_length_km, 3) if self.reverse_length_km is not None else None
            ),
            "reverse_edge_ids": self.reverse_edge_ids,
            "reverse_corridor_share": round(self.reverse_corridor_share, 3),
            "impracticable_alongside": self.impracticable_alongside,
        }


@dataclass(frozen=True)
class RoutedAttachment:
    option: RouteOption
    start_node: str
    start_snap_m: float
    end_node: str
    end_snap_m: float
    total_distance_km: float
    start_point: Point
    end_point: Point
    start_attachment_id: str
    end_attachment_id: str


@dataclass(frozen=True)
class PointAttachment:
    node_id: str
    routing_cost_m: float
    association_m: float
    prefix_geometry: LineString | None
    prefix_length_m: float
    edge_id: str | None
    reverse_edge_id: str | None
    a_road: bool
    ncn: bool
    impracticable_alongside: bool
    attachment_point: Point
    directed_edge_id: str | None = None
    reverse_directed_edge_id: str | None = None


class RoadGraph:
    """A deterministic routed view of governed road-edge evidence."""

    edge_id_attribute = "edge_id"

    def __init__(
        self,
        edges: gpd.GeoDataFrame,
        *,
        route_controls: RouteControlSet | None = None,
    ):
        self.crs = edges.crs
        self.graph = nx.DiGraph()
        self.node_points: dict[str, Point] = {}
        self._shortest_lengths: dict[str, dict[str, float]] = {}
        self._nearby_node_cache: dict[tuple[str, float], tuple[tuple[str, float], ...]] = {}
        self._nearby_node_cache_hits = 0
        self._unmaterializable_attachment_paths = 0
        self._lower_bound_cost_factor = 0.0
        self._lower_bound_disabled_reason: str | None = "no-routable-edges"
        self._attachment_lower_bound_cost_factor = 0.0
        self._attachment_lower_bound_disabled_reason: str | None = "no-reciprocal-routable-edges"
        # Project all source edge geometries in one GeoPandas operation before
        # constructing the graph.  Projection is evidence normalisation, not
        # a routing decision, so it must not depend on iteration order.
        projected_source = gpd.GeoSeries(edges.geometry, crs=edges.crs).to_crs(27700)
        projected_lengths = projected_source.length
        edge_rows = [
            (index, row, float(projected_lengths.iloc[position]), projected_source.iloc[position])
            for position, (index, row) in enumerate(edges.iterrows())
        ]
        source_id_counts: dict[str, int] = {}
        for index, row, _projected_length_m, _projected_geometry in edge_rows:
            geometry = row.geometry
            if not isinstance(geometry, LineString) or len(geometry.coords) < 2:
                continue
            source_edge_id = _source_edge_id(row, index)
            source_id_counts[source_edge_id] = source_id_counts.get(source_edge_id, 0) + 1
        for index, row, projected_length_m, projected_geometry in sorted(
            edge_rows,
            key=lambda item: _edge_row_sort_key((item[0], item[1])),
        ):
            geometry = row.geometry
            if not isinstance(geometry, LineString) or len(geometry.coords) < 2:
                continue
            u = str(row.get("u")) if _present(row.get("u")) else _coordinate_id(geometry.coords[0])
            v = str(row.get("v")) if _present(row.get("v")) else _coordinate_id(geometry.coords[-1])
            self.node_points.setdefault(u, Point(geometry.coords[0]))
            self.node_points.setdefault(v, Point(geometry.coords[-1]))
            source_length = row.get("length")
            length_m = float(source_length) if _present(source_length) else projected_length_m
            source_edge_id = _source_edge_id(row, index)
            directed_edge_id = _directed_edge_identity(
                source_edge_id,
                u,
                v,
                projected_geometry,
                duplicate_source_id=source_id_counts[source_edge_id] > 1,
                crs="EPSG:27700",
            )
            attrs = {
                self.edge_id_attribute: source_edge_id,
                "directed_edge_id": directed_edge_id,
                "geometry": geometry,
                "length_m": length_m,
                "projected_length_m": projected_length_m,
                "highway": _tag_values(row.get("highway")),
                "bicycle": _tag_values(row.get("bicycle")),
                "ref": _tag_values(row.get("ref")),
                "oneway": _truthy(row.get("oneway")),
                "alongside": str(row.get("satn_alongside", "possible")),
                "ncn": _truthy(row.get("satn_ncn")),
                "cycle_alignment_bases": tuple(_tag_values(row.get("cycle_alignment_bases"))),
            }
            self._add_best_edge(u, v, attrs)
            if not _present(row.get("u")) and not attrs["oneway"]:
                reverse_projected_geometry = LineString(list(projected_geometry.coords)[::-1])
                reverse = attrs | {
                    "geometry": LineString(list(geometry.coords)[::-1]),
                    "directed_edge_id": _directed_edge_identity(
                        source_edge_id,
                        v,
                        u,
                        reverse_projected_geometry,
                        duplicate_source_id=True,
                        crs="EPSG:27700",
                    ),
                }
                self._add_best_edge(v, u, reverse)
        self._edge_ids_by_node: dict[str, tuple[str, ...]] = {}
        references_by_edge_id: dict[str, set[str]] = {}
        edge_ids_by_node: dict[str, set[str]] = {}
        for left, right, attrs in self.graph.edges(data=True):
            edge_id = str(attrs[self.edge_id_attribute])
            edge_ids_by_node.setdefault(str(left), set()).add(edge_id)
            edge_ids_by_node.setdefault(str(right), set()).add(edge_id)
            references_by_edge_id.setdefault(edge_id, set()).update(
                str(ref) for ref in attrs["ref"]
            )
        self._edge_ids_by_node = {
            node_id: tuple(sorted(edge_ids)) for node_id, edge_ids in edge_ids_by_node.items()
        }
        self._references_by_edge_id = {
            edge_id: tuple(sorted(references))
            for edge_id, references in references_by_edge_id.items()
        }
        self.route_controls = (
            RouteControlSet.model_validate(route_controls.model_dump(mode="python"))
            if route_controls is not None
            else None
        )
        (
            self._routing_excluded_pairs,
            self._strategic_excluded_pairs,
        ) = self._validated_control_pairs()
        self._routing_graph = nx.subgraph_view(
            self.graph,
            filter_edge=lambda left, right: (
                not self._bicycle_prohibited(left, right)
                and (str(left), str(right)) not in self._routing_excluded_pairs
            ),
        )
        self._strategic_graph = nx.subgraph_view(
            self._routing_graph,
            filter_edge=lambda left, right: (
                (
                    str(left),
                    str(right),
                )
                not in self._strategic_excluded_pairs
            ),
        )
        self._set_lower_bound_cost_factor()
        # Access connections must work in both directions. Searching only edges
        # with an explicit reciprocal prevents a one-way result from triggering
        # a combinatorial retry across every possible start/end pairing.
        self._attachment_graph = nx.DiGraph()
        for u, v, attrs in self._routing_graph.edges(data=True):
            if self._routing_graph.has_edge(v, u):
                self._attachment_graph.add_edge(u, v, **attrs)
        self._set_attachment_lower_bound_cost_factor()
        self._node_ids = list(self._attachment_graph.nodes)
        strong_components = sorted(
            nx.strongly_connected_components(self._attachment_graph),
            key=lambda component: (-len(component), tuple(sorted(component))),
        )
        self._strong_component_by_node = {
            node: component_index
            for component_index, component in enumerate(strong_components)
            for node in component
        }
        dominant = strong_components[0] if strong_components else set()
        routable_share = (
            len(dominant) / len(self._attachment_graph) if self._attachment_graph else 0
        )
        if routable_share >= 0.9:
            self._node_ids = [node for node in self._node_ids if node in dominant]
        all_node_ids = tuple(sorted(self.node_points))
        all_projected_nodes = gpd.GeoSeries(
            [self.node_points[node] for node in all_node_ids], crs=self.crs
        ).to_crs(27700)
        self._projected_node_by_id = dict(zip(all_node_ids, all_projected_nodes, strict=True))
        self._projected_nodes = gpd.GeoSeries(
            [self._projected_node_by_id[node] for node in self._node_ids], crs=27700
        )
        self._projected_node_index = self._projected_nodes.sindex
        edge_rows = [
            {"u": u, "v": v, "geometry": attrs["geometry"]}
            for u, v, attrs in self._routing_graph.edges(data=True)
        ]
        self._projected_edges = gpd.GeoDataFrame(
            edge_rows,
            columns=["u", "v", "geometry"],
            geometry="geometry",
            crs=self.crs,
        ).to_crs(27700)
        self._projected_edge_index = self._projected_edges.sindex

    def _bicycle_prohibited(self, left: object, right: object) -> bool:
        bicycle_values = self.graph.edges[left, right].get("bicycle", ())
        return "no" in {str(value).lower() for value in bicycle_values}

    def edge_ids_for_node(self, node_id: str) -> tuple[str, ...]:
        """Return the canonical source edge identities incident to a graph node."""

        return self._edge_ids_by_node.get(node_id, ())

    def references_for_edge_ids(self, edge_ids: Iterable[str]) -> tuple[str, ...]:
        """Return canonical road references for known source-edge identities."""

        return tuple(
            sorted(
                {
                    reference
                    for edge_id in edge_ids
                    for reference in self._references_by_edge_id.get(str(edge_id), ())
                }
            )
        )

    def projected_node(self, node_id: str) -> Point | None:
        """Return the preprojected EPSG:27700 coordinate for a current graph node."""

        return self._projected_node_by_id.get(node_id)

    def _add_best_edge(self, u: str, v: str, attrs: dict[str, object]) -> None:
        existing = self.graph.get_edge_data(u, v)
        if existing is None or float(attrs["length_m"]) < float(existing["length_m"]):
            self.graph.add_edge(u, v, **attrs)

    def bind_route_edge(
        self,
        from_node_id: str,
        to_node_id: str,
        *,
        evidence_snapshot_fingerprint: str,
        mode: EdgeBindingMode = EdgeBindingMode.DIRECTIONAL,
    ) -> RouteEdgeBinding:
        """Bind a current directed edge or explicit reciprocal pair for a decision."""

        directions = [
            self._directed_edge_binding(
                from_node_id,
                to_node_id,
                evidence_snapshot_fingerprint=evidence_snapshot_fingerprint,
            )
        ]
        if mode == EdgeBindingMode.BIDIRECTIONAL:
            directions.append(
                self._directed_edge_binding(
                    to_node_id,
                    from_node_id,
                    evidence_snapshot_fingerprint=evidence_snapshot_fingerprint,
                )
            )
        return RouteEdgeBinding(mode=mode, directions=tuple(directions))

    def _directed_edge_binding(
        self,
        from_node_id: str,
        to_node_id: str,
        *,
        evidence_snapshot_fingerprint: str,
    ) -> DirectedEdgeBinding:
        attrs = self.graph.get_edge_data(from_node_id, to_node_id)
        if attrs is None:
            raise ValueError(
                f"current RoadGraph has no directed edge {from_node_id!r}->{to_node_id!r}"
            )
        return DirectedEdgeBinding(
            evidence_snapshot_fingerprint=evidence_snapshot_fingerprint,
            source_edge_id=str(attrs["edge_id"]),
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            geometry_fingerprint=canonical_network_geometry_fingerprint(
                attrs["geometry"],
                self.crs,
            ),
        )

    def _validated_control_pairs(
        self,
    ) -> tuple[frozenset[tuple[str, str]], frozenset[tuple[str, str]]]:
        if self.route_controls is None:
            return frozenset(), frozenset()
        routing = self._validate_bindings(self.route_controls.routing_exclusions)
        strategic = self._validate_bindings(self.route_controls.strategic_spine_exclusions)
        return frozenset(routing), frozenset(strategic)

    def _validate_bindings(
        self,
        bindings: tuple[RouteEdgeBinding, ...],
    ) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for binding in bindings:
            for direction in binding.directions:
                attrs = self.graph.get_edge_data(
                    direction.from_node_id,
                    direction.to_node_id,
                )
                if attrs is None:
                    raise ValueError(
                        "route control edge binding is missing from the current RoadGraph"
                    )
                geometry_fingerprint = canonical_network_geometry_fingerprint(
                    attrs["geometry"],
                    self.crs,
                )
                if (
                    str(attrs["edge_id"]) != direction.source_edge_id
                    or geometry_fingerprint != direction.geometry_fingerprint
                ):
                    raise ValueError("route control edge binding is stale or geometry-mismatched")
                pairs.add(direction.directed_key)
        return pairs

    @property
    def route_control_fingerprint(self) -> str | None:
        """Return the governed routing dependency, absent for a clean baseline."""

        return self.route_controls.control_fingerprint if self.route_controls is not None else None

    def edge_restrictions(
        self,
        from_node_id: str,
        to_node_id: str,
    ) -> tuple[dict[str, str], ...]:
        """Expose restrictions while retaining the underlying edge as evidence."""

        if self.route_controls is None:
            return ()
        records: list[dict[str, str]] = []
        for restriction, bindings in (
            ("exclude-from-strategic-spine", self.route_controls.strategic_spine_exclusions),
            ("exclude-from-routing", self.route_controls.routing_exclusions),
        ):
            for binding in bindings:
                if any(
                    item.directed_key == (from_node_id, to_node_id) for item in binding.directions
                ):
                    records.append(
                        {
                            "restriction": restriction,
                            "binding_id": binding.binding_id,
                            "route_control_fingerprint": (self.route_controls.control_fingerprint),
                        }
                    )
        return tuple(sorted(records, key=lambda item: (item["restriction"], item["binding_id"])))

    def edge_is_allowed(
        self,
        from_node_id: str,
        to_node_id: str,
        *,
        strategic_use: bool = False,
    ) -> bool:
        """Return whether the exact directed edge may be traversed."""

        pair = (from_node_id, to_node_id)
        return pair not in self._routing_excluded_pairs and (
            not strategic_use or pair not in self._strategic_excluded_pairs
        )

    def _graph_for_role(
        self,
        role: str,
        *,
        strategic_use: bool = False,
    ) -> nx.DiGraph:
        if strategic_use or role == "strategic-spine":
            return self._strategic_graph
        return self._routing_graph

    @property
    def lower_bound_cost_factor(self) -> float:
        """A safe multiplier from projected straight-line distance to route cost."""
        return self._lower_bound_cost_factor

    @property
    def lower_bound_disabled_reason(self) -> str | None:
        """Explain why metric lower bounds fall back to zero."""
        return self._lower_bound_disabled_reason

    @property
    def attachment_lower_bound_cost_factor(self) -> float:
        """Return the safe metric factor for reciprocal attachment routes.

        Cross-Spine meetings route only through ``_attachment_graph``.  A
        one-way or otherwise non-reciprocal edge in the wider road graph can
        make the broader factor unnecessarily weak even though it can never
        participate in a meeting route.  Keeping this factor scoped to the
        actual attachment graph gives the scheduler a tighter *safe* bound;
        it never estimates a route or changes its selected geometry.
        """
        return self._attachment_lower_bound_cost_factor

    @property
    def attachment_lower_bound_disabled_reason(self) -> str | None:
        """Explain why reciprocal attachment bounds fall back to zero."""
        return self._attachment_lower_bound_disabled_reason

    def lower_bound_to_geometry_m(self, point: Point, projected_geometry: object) -> float:
        """Return a conservative route-cost bound, or zero when graph geometry is unsafe."""
        if self._lower_bound_cost_factor <= 0 or projected_geometry is None:
            return 0.0
        projected_point = gpd.GeoSeries([point], crs=self.crs).to_crs(27700).iloc[0]
        return self._lower_bound_cost_factor * float(projected_point.distance(projected_geometry))

    def _set_lower_bound_cost_factor(self) -> None:
        """Derive a metric lower bound only from canonically connected graph edges.

        A graph edge is allowed to be cheaper than its rendered geometry, but then the
        smallest cost/geometry ratio is the only safe global multiplier.  If an edge's
        endpoint does not agree with the canonical node coordinate, the graph can make a
        geometric jump and no Euclidean-derived bound is sound.
        """
        if self._routing_graph.number_of_edges() == 0:
            return
        ratios: list[float] = []
        for u, v, attrs in self._routing_graph.edges(data=True):
            geometry = attrs["geometry"]
            if (
                not isinstance(geometry, LineString)
                or len(geometry.coords) < 2
                or self.node_points.get(u) != Point(geometry.coords[0])
                or self.node_points.get(v) != Point(geometry.coords[-1])
            ):
                self._lower_bound_disabled_reason = "non-canonical-edge-endpoints"
                return
            cost_m = float(attrs["length_m"])
            geometry_m = float(attrs["projected_length_m"])
            if not math.isfinite(cost_m) or cost_m <= 0:
                self._lower_bound_disabled_reason = "non-positive-or-non-finite-edge-cost"
                return
            if not math.isfinite(geometry_m) or geometry_m <= 0:
                self._lower_bound_disabled_reason = "non-positive-or-non-finite-projected-geometry"
                return
            ratios.append(cost_m / geometry_m)
        # Point-to-edge association is charged in physical metres, so a factor above one
        # would not remain valid for every attachment route.
        self._lower_bound_cost_factor = min(1.0, min(ratios))
        self._lower_bound_disabled_reason = None

    def _set_attachment_lower_bound_cost_factor(self) -> None:
        """Derive the metric lower bound from exactly the meeting route graph.

        The same endpoint and finite-cost checks as the whole-graph factor
        apply.  A failure is conservative: the caller receives zero rather
        than an unsound geometric shortcut.
        """
        if self._attachment_graph.number_of_edges() == 0:
            return
        ratios: list[float] = []
        for u, v, attrs in self._attachment_graph.edges(data=True):
            geometry = attrs["geometry"]
            if (
                not isinstance(geometry, LineString)
                or len(geometry.coords) < 2
                or self.node_points.get(u) != Point(geometry.coords[0])
                or self.node_points.get(v) != Point(geometry.coords[-1])
            ):
                self._attachment_lower_bound_disabled_reason = "non-canonical-edge-endpoints"
                return
            cost_m = float(attrs["length_m"])
            geometry_m = float(attrs["projected_length_m"])
            if not math.isfinite(cost_m) or cost_m <= 0:
                self._attachment_lower_bound_disabled_reason = (
                    "non-positive-or-non-finite-edge-cost"
                )
                return
            if not math.isfinite(geometry_m) or geometry_m <= 0:
                self._attachment_lower_bound_disabled_reason = (
                    "non-positive-or-non-finite-projected-geometry"
                )
                return
            ratios.append(cost_m / geometry_m)
        self._attachment_lower_bound_cost_factor = min(1.0, min(ratios))
        self._attachment_lower_bound_disabled_reason = None

    def compilation_diagnostics(self) -> dict[str, object]:
        """Return deterministic graph-search dimensions for run diagnostics."""
        return {
            "road_graph_nodes": self.graph.number_of_nodes(),
            "road_graph_edges": self.graph.number_of_edges(),
            "reciprocal_routing_nodes": self._attachment_graph.number_of_nodes(),
            "reciprocal_routing_edges": self._attachment_graph.number_of_edges(),
            "nearby_node_candidate_sets": len(self._nearby_node_cache),
            "nearby_node_candidate_set_reuses": self._nearby_node_cache_hits,
            "unmaterializable_attachment_paths": self._unmaterializable_attachment_paths,
            "lower_bound_cost_factor": self._lower_bound_cost_factor,
            "lower_bound_disabled_reason": self._lower_bound_disabled_reason,
            "attachment_lower_bound_cost_factor": self._attachment_lower_bound_cost_factor,
            "attachment_lower_bound_disabled_reason": self._attachment_lower_bound_disabled_reason,
        }

    def nearest_node(self, point: Point) -> tuple[str, float]:
        if not self.node_points:
            raise ValueError("source network has no routable LineString edges")
        target = gpd.GeoSeries([point], crs=self.crs).to_crs(27700).iloc[0]
        distances = self._projected_nodes.distance(target)
        return min(
            (
                (self._node_ids[position], float(distance))
                for position, distance in enumerate(distances)
            ),
            key=lambda match: (match[1], match[0]),
        )

    def nearest_node_on_largest_reciprocal_component(
        self,
        point: Point,
        max_distance_m: float,
    ) -> tuple[str, float]:
        """Bind a canonical urban point to the largest reciprocal component nearby.

        This opt-in seam only prefers a node in the largest strongly connected
        reciprocal component when that node is within the caller's governed
        urban attachment extent.  Otherwise it retains the nearest reciprocal
        node, so an out-of-scope fragment remains local instead of snapping to
        the globally filtered dominant component.
        """

        if max_distance_m < 0:
            raise ValueError("urban attachment extent must be non-negative")
        dominant_nodes = {
            node_id
            for node_id, component_index in self._strong_component_by_node.items()
            if component_index == 0
        }
        if dominant_nodes:
            candidates = [
                (node_id, distance_m)
                for node_id, distance_m in self.nodes_near(point, max_distance_m)
                if node_id in dominant_nodes
            ]
            if candidates:
                return min(candidates, key=lambda match: (match[1], match[0]))
        if self._strong_component_by_node:
            target = gpd.GeoSeries([point], crs=self.crs).to_crs(27700).iloc[0]
            reciprocal_candidates = [
                (
                    node_id,
                    float(self._projected_node_by_id[node_id].distance(target)),
                )
                for node_id in self._strong_component_by_node
                if node_id in self._projected_node_by_id
            ]
            if reciprocal_candidates:
                return min(reciprocal_candidates, key=lambda match: (match[1], match[0]))
        return self.nearest_node(point)

    def nodes_near(self, point: Point, max_distance_m: float) -> list[tuple[str, float]]:
        """Return every bounded attachment candidate with deterministic tie-breaking."""
        if not self.node_points:
            raise ValueError("source network has no routable LineString edges")
        cache_key = (point.wkb_hex, float(max_distance_m))
        cached = self._nearby_node_cache.get(cache_key)
        if cached is not None:
            self._nearby_node_cache_hits += 1
            return list(cached)
        target = gpd.GeoSeries([point], crs=self.crs).to_crs(27700).iloc[0]
        positions = self._projected_node_index.query(
            target.buffer(max_distance_m), predicate="intersects"
        )
        ordered_positions = sorted(int(position) for position in positions)
        selected_nodes = self._projected_nodes.iloc[ordered_positions]
        distances = selected_nodes.distance(target)
        matches = [
            (
                self._node_ids[position],
                float(distance),
            )
            for position, distance in zip(ordered_positions, distances, strict=True)
        ]
        result = tuple(
            sorted(
                (match for match in matches if match[1] <= max_distance_m),
                key=lambda match: (match[1], match[0]),
            )
        )
        self._nearby_node_cache[cache_key] = result
        return list(result)

    def nodes_on_geometry(
        self,
        geometry: object,
        *,
        tolerance_m: float = 20,
    ) -> list[tuple[str, float]]:
        """Return routable graph nodes evidenced on a corridor, never an unbounded snap."""
        if geometry is None or geometry.is_empty or not self._node_ids:
            return []
        target = gpd.GeoSeries([geometry], crs=self.crs).to_crs(27700).iloc[0]
        positions = self._projected_node_index.query(
            target.buffer(tolerance_m), predicate="intersects"
        )
        matches = [
            (
                self._node_ids[int(position)],
                float(self._projected_nodes.iloc[int(position)].distance(target)),
            )
            for position in positions
        ]
        return sorted(
            (match for match in matches if match[1] <= tolerance_m),
            key=lambda match: (match[1], match[0]),
        )

    def best_point_attachment(
        self,
        point: Point,
        max_association_m: float,
        ends: list[tuple[str, float]],
        *,
        allow_stationary: bool = True,
        excluded_pairs: set[tuple[str, str]] | None = None,
    ) -> RoutedAttachment | None:
        """Attach a point to nearby nodes or edge interiors without hiding edge travel."""
        attachments = self._point_attachments(point, max_association_m)
        choice = self.best_attachment(
            [(item.node_id, item.routing_cost_m) for item in attachments],
            ends,
            allow_stationary=allow_stationary,
            excluded_pairs=excluded_pairs,
        )
        if choice is None:
            return None
        selected = min(
            (
                item
                for item in attachments
                if item.node_id == choice.start_node
                and abs(item.routing_cost_m - choice.start_snap_m) < 1e-6
            ),
            key=lambda item: (
                item.routing_cost_m,
                item.edge_id or "",
                item.reverse_edge_id or "",
            ),
        )
        option = self._prepend_point_attachment(selected, choice.option)
        return RoutedAttachment(
            option=option,
            start_node=choice.start_node,
            start_snap_m=selected.association_m,
            end_node=choice.end_node,
            end_snap_m=choice.end_snap_m,
            total_distance_km=choice.total_distance_km,
            start_point=selected.attachment_point,
            end_point=choice.end_point,
            start_attachment_id=(
                choice.start_attachment_id
                if selected.edge_id is None
                else (
                    f"edge:{selected.edge_id}:{_coordinate_id(selected.attachment_point.coords[0])}"
                )
            ),
            end_attachment_id=choice.end_attachment_id,
        )

    def has_point_attachment(self, point: Point, max_association_m: float) -> bool:
        """Return whether a point has a governed bidirectional node/edge attachment."""
        return bool(self._point_attachments(point, max_association_m))

    def _point_attachments(
        self,
        point: Point,
        max_association_m: float,
    ) -> list[PointAttachment]:
        attachments = [
            PointAttachment(
                node_id=node_id,
                routing_cost_m=distance_m,
                association_m=distance_m,
                prefix_geometry=None,
                prefix_length_m=0.0,
                edge_id=None,
                reverse_edge_id=None,
                a_road=False,
                ncn=False,
                impracticable_alongside=False,
                attachment_point=self.node_points[node_id],
            )
            for node_id, distance_m in self.nodes_near(point, max_association_m)
        ]
        target = gpd.GeoSeries([point], crs=self.crs).to_crs(27700).iloc[0]
        positions = self._projected_edge_index.query(
            target.buffer(max_association_m), predicate="intersects"
        )
        for position in positions:
            projected = self._projected_edges.iloc[int(position)]
            projected_geometry = projected.geometry
            distance_along = projected_geometry.project(target)
            projected_point = projected_geometry.interpolate(distance_along)
            association_m = float(target.distance(projected_point))
            if association_m > max_association_m or projected_geometry.length == 0:
                continue
            u = str(projected["u"])
            v = str(projected["v"])
            if not self._routing_graph.has_edge(v, u):
                continue
            fraction = float(distance_along / projected_geometry.length)
            if fraction <= 1e-9 or fraction >= 1 - 1e-9:
                continue
            attrs = self._routing_graph[u][v]
            reverse = self._routing_graph[v][u]
            reverse_geometry = reverse["geometry"]
            if (
                not isinstance(reverse_geometry, LineString)
                or min(
                    _corridor_share(attrs["geometry"], reverse_geometry, self.crs),
                    _corridor_share(reverse_geometry, attrs["geometry"], self.crs),
                )
                < 0.5
            ):
                continue
            prefix_length_m = float(attrs["length_m"]) * (1 - fraction)
            prefix = substring(attrs["geometry"], fraction, 1, normalized=True)
            if not isinstance(prefix, LineString) or prefix.is_empty:
                continue
            attachments.append(
                PointAttachment(
                    node_id=v,
                    routing_cost_m=association_m + prefix_length_m,
                    association_m=association_m,
                    prefix_geometry=prefix,
                    prefix_length_m=prefix_length_m,
                    edge_id=str(attrs["edge_id"]),
                    reverse_edge_id=str(reverse["edge_id"]),
                    a_road=_is_a_road(attrs["ref"]),
                    ncn=bool(attrs["ncn"]),
                    impracticable_alongside=str(attrs["alongside"]) == "impracticable",
                    attachment_point=gpd.GeoSeries([projected_point], crs=27700)
                    .to_crs(self.crs)
                    .iloc[0],
                    directed_edge_id=str(attrs["directed_edge_id"]),
                    reverse_directed_edge_id=str(reverse["directed_edge_id"]),
                )
            )
        return sorted(
            attachments,
            key=lambda item: (item.routing_cost_m, item.node_id, item.edge_id or ""),
        )

    @staticmethod
    def _prepend_point_attachment(
        attachment: PointAttachment,
        option: RouteOption,
    ) -> RouteOption:
        if attachment.prefix_geometry is None or attachment.edge_id is None:
            return option
        geometry = _merge_route([attachment.prefix_geometry, option.geometry])
        if geometry is None:
            geometry = option.geometry
        total_m = attachment.prefix_length_m + option.length_km * 1000
        a_road_m = attachment.prefix_length_m if attachment.a_road else 0.0
        a_road_m += option.a_road_share * option.length_km * 1000
        ncn_m = attachment.prefix_length_m if attachment.ncn else 0.0
        ncn_m += option.ncn_share * option.length_km * 1000
        reverse_length_km = (
            option.reverse_length_km + attachment.prefix_length_m / 1000
            if option.reverse_length_km is not None
            else None
        )
        directed_edge_ids = option.directed_edge_ids or option.edge_ids
        reverse_directed_edge_ids = option.reverse_directed_edge_ids or option.reverse_edge_ids
        return RouteOption(
            role=option.role,
            geometry=geometry,
            length_km=total_m / 1000,
            edge_ids=[attachment.edge_id, *option.edge_ids],
            a_road_share=a_road_m / total_m if total_m else 0.0,
            ncn_share=ncn_m / total_m if total_m else 0.0,
            bidirectional=option.bidirectional and attachment.reverse_edge_id is not None,
            reverse_length_km=reverse_length_km,
            reverse_edge_ids=[
                *option.reverse_edge_ids,
                *([attachment.reverse_edge_id] if attachment.reverse_edge_id else []),
            ],
            reverse_corridor_share=option.reverse_corridor_share,
            impracticable_alongside=(
                option.impracticable_alongside or attachment.impracticable_alongside
            ),
            directed_edge_ids=[
                attachment.directed_edge_id or attachment.edge_id,
                *directed_edge_ids,
            ],
            reverse_directed_edge_ids=[
                *reverse_directed_edge_ids,
                *(
                    [attachment.reverse_directed_edge_id or attachment.reverse_edge_id]
                    if attachment.reverse_edge_id
                    else []
                ),
            ],
        )

    def network_distance(
        self,
        starts: list[tuple[str, float]],
        ends: list[tuple[str, float]],
    ) -> float:
        best = float("inf")
        for start, start_snap in starts:
            self._cache_direct_lengths(start)
            lengths = self._shortest_lengths[start]
            for end, end_snap in ends:
                if end in lengths:
                    best = min(best, float(lengths[end]) + start_snap + end_snap)
        return best

    def attachment_group_distance_bounds(
        self,
        groups: Mapping[str, Sequence[str]],
    ) -> tuple[dict[tuple[str, str], float], set[tuple[str, str]], dict[str, int]]:
        """Return route-cost bounds and component-proven no-route group pairs.

        Cross-Spine assembly already has fixed attachment *nodes* for every
        served root group.  For each origin group, a multi-source Dijkstra over
        exactly the reciprocal attachment graph yields a lower bound for every
        later group: the minimum cost from any origin attachment node to any
        destination attachment node.  With zero snap costs this is an exact
        numeric lower bound on the cost that ``best_attachment`` may rank
        before it materialises route geometry.

        The second result contains pairs whose attachment nodes share no
        reciprocal strong component.  This is the same eligibility condition
        checked at the start of ``best_attachment``, so such a pair cannot
        produce a route and need not be materialised merely to rediscover that
        fact.  The method deliberately returns no chosen path, endpoint or
        ``RouteOption``.  Callers must still invoke
        ``best_attachment`` before an edge can reach a gate or publication.
        That separation makes this a scheduling optimisation rather than a
        second routing implementation.  Missing paths are omitted instead of
        being asserted as an absence; callers retain their conservative
        fallback schedule for those pairs.
        """
        root_ids = sorted(str(root_id) for root_id in groups)
        nodes_by_root = {
            root_id: tuple(
                sorted(
                    {
                        str(node_id)
                        for node_id in groups[root_id]
                        if str(node_id) in self._attachment_graph
                    }
                )
            )
            for root_id in root_ids
        }
        components_by_root = {
            root_id: frozenset(
                self._strong_component_by_node[node_id]
                for node_id in nodes_by_root[root_id]
                if node_id in self._strong_component_by_node
            )
            for root_id in root_ids
        }
        bounds: dict[tuple[str, str], float] = {}
        unroutable_pairs: set[tuple[str, str]] = set()
        searches = 0
        nodes_settled = 0
        # The final root has no later partner in this deterministic ordering,
        # so planning from it cannot produce a bound used by the caller.
        for left_index, left_root in enumerate(root_ids[:-1]):
            starts = nodes_by_root[left_root]
            routable_right_roots = []
            for right_root in root_ids[left_index + 1 :]:
                if components_by_root[left_root].intersection(components_by_root[right_root]):
                    routable_right_roots.append(right_root)
                else:
                    unroutable_pairs.add((left_root, right_root))
            if not starts or not routable_right_roots:
                continue
            lengths = nx.multi_source_dijkstra_path_length(
                self._attachment_graph,
                starts,
                weight="length_m",
            )
            searches += 1
            nodes_settled += len(lengths)
            for right_root in routable_right_roots:
                distances = [
                    float(lengths[node_id])
                    for node_id in nodes_by_root[right_root]
                    if node_id in lengths
                ]
                if distances:
                    bounds[(left_root, right_root)] = min(distances)
        return (
            bounds,
            unroutable_pairs,
            {
                "root_group_distance_planning_searches": searches,
                "root_group_distance_planning_nodes_settled": nodes_settled,
            },
        )

    def best_attachment(
        self,
        starts: list[tuple[str, float]],
        ends: list[tuple[str, float]],
        *,
        allow_stationary: bool = True,
        excluded_pairs: set[tuple[str, str]] | None = None,
    ) -> RoutedAttachment | None:
        """Select one attachment with a bounded multi-source/multi-target search."""
        end_components = {
            self._strong_component_by_node[end]
            for end, _ in ends
            if end in self._strong_component_by_node
        }
        eligible_starts = [
            (start, start_snap)
            for start, start_snap in starts
            if self._strong_component_by_node.get(start) in end_components
        ]
        if not eligible_starts or not ends:
            return None
        search_heap: list[
            tuple[
                float,
                float,
                float,
                str,
                str,
                int,
                list[tuple[str, float]],
                list[tuple[str, float]],
                tuple[float, list[str], str, float, str, float],
            ]
        ] = []
        sequence = 0

        def add_search(
            search_starts: list[tuple[str, float]],
            search_ends: list[tuple[str, float]],
        ) -> None:
            nonlocal sequence
            routed = self._attachment_path(search_starts, search_ends)
            if routed is None:
                return
            total_m, _, start, start_snap, end, end_snap = routed
            heapq.heappush(
                search_heap,
                (
                    total_m,
                    start_snap,
                    end_snap,
                    start,
                    end,
                    sequence,
                    search_starts,
                    search_ends,
                    routed,
                ),
            )
            sequence += 1

        add_search(eligible_starts, ends)
        while search_heap:
            _, _, _, _, _, _, search_starts, search_ends, routed = heapq.heappop(search_heap)
            total_m, nodes, start, start_snap, end, end_snap = routed
            if start == end and not allow_stationary:
                add_search(
                    [attachment for attachment in search_starts if attachment[0] != start],
                    search_ends,
                )
                add_search(
                    [attachment for attachment in search_starts if attachment[0] == start],
                    [attachment for attachment in search_ends if attachment[0] != end],
                )
                continue
            if start == end:
                option = stationary_route_option(self.node_points[start])
            else:
                option = self._option_from_nodes(nodes, "direct")
            if option is None:
                self._unmaterializable_attachment_paths += 1
                LOGGER.debug(
                    "Attachment path could not be materialized start=%s end=%s",
                    start,
                    end,
                )
                return None
            if excluded_pairs is None or (start, end) not in excluded_pairs:
                if not option.bidirectional:
                    LOGGER.debug(
                        "Reciprocal attachment has insufficient reverse-corridor overlap "
                        "start=%s end=%s share=%.3f",
                        start,
                        end,
                        option.reverse_corridor_share,
                    )
                return RoutedAttachment(
                    option=option,
                    start_node=start,
                    start_snap_m=start_snap,
                    end_node=end,
                    end_snap_m=end_snap,
                    total_distance_km=total_m / 1000,
                    start_point=self.node_points[start],
                    end_point=self.node_points[end],
                    start_attachment_id=start,
                    end_attachment_id=end,
                )
            add_search(
                [attachment for attachment in search_starts if attachment[0] != start],
                search_ends,
            )
            add_search(
                [attachment for attachment in search_starts if attachment[0] == start],
                [attachment for attachment in search_ends if attachment[0] != end],
            )
        return None

    def _attachment_path(
        self,
        starts: list[tuple[str, float]],
        ends: list[tuple[str, float]],
    ) -> tuple[float, list[str], str, float, str, float] | None:
        if not starts or not ends:
            return None
        source = object()
        sink = object()
        start_connectors: dict[object, tuple[str, float]] = {}
        end_connectors: dict[object, tuple[str, float]] = {}
        temporary_nodes: list[object] = [source, sink]
        try:
            for start, snap_m in starts:
                connector = object()
                start_connectors[connector] = (start, snap_m)
                temporary_nodes.append(connector)
                self._attachment_graph.add_edge(source, connector, length_m=0.0)
                self._attachment_graph.add_edge(
                    connector,
                    start,
                    length_m=snap_m * (1.0 + ATTACHMENT_TIE_BREAK_EPSILON),
                )
            for end, snap_m in ends:
                connector = object()
                end_connectors[connector] = (end, snap_m)
                temporary_nodes.append(connector)
                self._attachment_graph.add_edge(
                    end,
                    connector,
                    length_m=snap_m * (1.0 + ATTACHMENT_TIE_BREAK_EPSILON),
                )
                self._attachment_graph.add_edge(connector, sink, length_m=0.0)
            total_m, path = nx.single_source_dijkstra(
                self._attachment_graph, source, target=sink, weight="length_m"
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
        finally:
            self._attachment_graph.remove_nodes_from(temporary_nodes)
        start, start_snap = start_connectors[path[1]]
        end, end_snap = end_connectors[path[-2]]
        return float(total_m), path[2:-2], start, start_snap, end, end_snap

    def _cache_direct_lengths(self, source: str) -> None:
        if source in self._shortest_lengths:
            return
        self._shortest_lengths[source] = nx.single_source_dijkstra_path_length(
            self._routing_graph, source, weight="length_m"
        )

    def option(
        self,
        start: str,
        end: str,
        role: str,
        *,
        strategic_use: bool = False,
    ) -> RouteOption | None:
        route_graph = self._graph_for_role(role, strategic_use=strategic_use)
        try:
            nodes = nx.shortest_path(route_graph, start, end, weight=_weight_for(role))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
        return self._option_from_nodes(nodes, role, strategic_use=strategic_use)

    def route_options_for_pairs(
        self,
        pairs: Iterable[tuple[str, str]],
        *,
        roles: Iterable[str],
        strategic_use: bool = False,
    ) -> tuple[dict[tuple[str, str], dict[str, RouteOption | None]], int]:
        """Route each finite pair with the targeted option implementation.

        The returned mapping retains one entry per supplied, distinct directed
        pair and the original role order.  The count records one targeted
        route search for each distinct pair and role; reverse-route material-
        isation remains part of that option's result.
        """

        unique_pairs = tuple(sorted(set(pairs)))
        ordered_roles = tuple(dict.fromkeys(roles))
        options = {pair: {role: None for role in ordered_roles} for pair in unique_pairs}
        for pair in unique_pairs:
            start, end = pair
            for role in ordered_roles:
                options[pair][role] = self.option(
                    start,
                    end,
                    role,
                    strategic_use=strategic_use,
                )
        return options, len(unique_pairs) * len(ordered_roles)

    def _option_from_nodes(
        self,
        nodes: list[str],
        role: str,
        *,
        strategic_use: bool = False,
        reverse_nodes: list[str] | object | None = _REVERSE_PATH_UNSET,
    ) -> RouteOption | None:
        weight = _weight_for(role)
        route_graph = self._graph_for_role(role, strategic_use=strategic_use)
        edge_data = [route_graph[a][b] for a, b in pairwise(nodes)]
        if not edge_data:
            return None
        geometry = _merge_route([edge["geometry"] for edge in edge_data])
        if geometry is None:
            return None
        length_m = sum(float(edge["length_m"]) for edge in edge_data)
        a_length = sum(float(edge["length_m"]) for edge in edge_data if _is_a_road(edge["ref"]))
        ncn_length = sum(float(edge["length_m"]) for edge in edge_data if edge["ncn"])
        try:
            if reverse_nodes is _REVERSE_PATH_UNSET:
                resolved_reverse_nodes = list(reversed(nodes))
                if not all(
                    route_graph.has_edge(left, right)
                    for left, right in pairwise(resolved_reverse_nodes)
                ):
                    resolved_reverse_nodes = nx.shortest_path(
                        route_graph,
                        nodes[-1],
                        nodes[0],
                        weight=weight,
                    )
            elif reverse_nodes is None:
                raise nx.NetworkXNoPath
            else:
                resolved_reverse_nodes = reverse_nodes
            reverse_edges = [
                route_graph[left][right] for left, right in pairwise(resolved_reverse_nodes)
            ]
            reverse_geometry = _merge_route([edge["geometry"] for edge in reverse_edges])
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            reverse_edges = []
            reverse_geometry = None
        reverse_corridor_share = (
            min(
                _corridor_share(reverse_geometry, geometry, self.crs),
                _corridor_share(geometry, reverse_geometry, self.crs),
            )
            if reverse_geometry is not None
            else 0.0
        )
        reverse_exists = reverse_corridor_share >= 0.5
        reverse_length_m = (
            sum(float(edge["length_m"]) for edge in reverse_edges)
            if reverse_geometry is not None
            else None
        )
        return RouteOption(
            role=role,
            geometry=geometry,
            length_km=length_m / 1000,
            edge_ids=[str(edge["edge_id"]) for edge in edge_data],
            a_road_share=a_length / length_m if length_m else 0,
            ncn_share=ncn_length / length_m if length_m else 0,
            bidirectional=reverse_exists,
            reverse_length_km=(reverse_length_m / 1000 if reverse_length_m is not None else None),
            reverse_edge_ids=[str(edge["edge_id"]) for edge in reverse_edges],
            reverse_corridor_share=reverse_corridor_share,
            impracticable_alongside=any(
                edge["alongside"] == "impracticable" and _is_a_road(edge["ref"])
                for edge in edge_data
            ),
            directed_edge_ids=[str(edge["directed_edge_id"]) for edge in edge_data],
            reverse_directed_edge_ids=[str(edge["directed_edge_id"]) for edge in reverse_edges],
        )

    def governed_option_or_gap(
        self,
        start: str,
        end: str,
        role: str,
        *,
        strategic_use: bool = False,
        unsatisfied_network_place_ids: tuple[str, ...] = (),
        unsatisfied_access_obligation_ids: tuple[str, ...] = (),
        unsatisfied_strategic_destination_ids: tuple[str, ...] = (),
    ) -> RouteOption | RouteControlNetworkGap:
        """Return a route or an explicit no-geometry gap for a governed scenario."""

        if self.route_controls is None:
            raise ValueError("a governed route outcome requires a RouteControlSet")
        option = self.option(
            start,
            end,
            role,
            strategic_use=strategic_use,
        )
        if option is not None:
            return option
        return RouteControlNetworkGap(
            from_node_id=start,
            to_node_id=end,
            route_role=role,
            unsatisfied_network_place_ids=unsatisfied_network_place_ids,
            unsatisfied_access_obligation_ids=unsatisfied_access_obligation_ids,
            unsatisfied_strategic_destination_ids=(unsatisfied_strategic_destination_ids),
            evidence_snapshot_fingerprint=(self.route_controls.evidence_snapshot_fingerprint),
            route_control_fingerprint=self.route_controls.control_fingerprint,
            excluded_edge_binding_ids=self.route_controls.excluded_binding_ids,
        )


def choose_alignment(
    graph: RoadGraph,
    start: str,
    end: str,
    *,
    strategic_use: bool = False,
    precomputed_options: Mapping[str, RouteOption | None] | None = None,
) -> tuple[RouteOption | None, list[RouteOption], str]:
    direct = (
        precomputed_options.get("direct")
        if precomputed_options is not None
        else graph.option(start, end, "direct", strategic_use=strategic_use)
    )
    if direct is None:
        return None, [], "No continuous OSM cycling-network path exists."
    strategic = (
        precomputed_options.get("strategic-spine")
        if precomputed_options is not None
        else graph.option(start, end, "strategic-spine", strategic_use=strategic_use)
    )
    ncn = (
        precomputed_options.get("ncn-informed")
        if precomputed_options is not None
        else graph.option(start, end, "ncn-informed", strategic_use=strategic_use)
    )
    quiet = (
        precomputed_options.get("low-traffic")
        if precomputed_options is not None
        else graph.option(start, end, "low-traffic", strategic_use=strategic_use)
    )
    options = _unique_options([direct, strategic, ncn, quiet])

    if (
        strategic
        and strategic.a_road_share >= 0.8
        and strategic.length_km <= direct.length_km * 1.5
    ):
        if strategic.impracticable_alongside:
            fallback = quiet if quiet and quiet.length_km <= direct.length_km * 1.5 else direct
            return (
                fallback,
                options,
                "Parallel fallback selected because alongside A-road provision is marked "
                "physically impracticable.",
            )
        return (
            strategic,
            options,
            "A-road Strategic Spine selected for directness and social oversight.",
        )
    if ncn and ncn.ncn_share > 0 and ncn.length_km <= direct.length_km * 1.35:
        return (
            ncn,
            options,
            "National Cycle Network evidence informed the selected continuous alignment.",
        )
    if quiet and quiet.length_km <= direct.length_km * 1.35:
        return (
            quiet,
            options,
            "Low-traffic OSM alignment selected within the directness challenge margin.",
        )
    return direct, options, "Most direct continuous OSM alignment selected."


def serialise_options(options: list[RouteOption]) -> str:
    return json.dumps([option.summary() for option in options], sort_keys=True)


def _weight_for(role: str) -> Callable[[str, str, dict[str, object]], float]:
    def weight(_u: str, _v: str, edge: dict[str, object]) -> float:
        length = float(edge["length_m"])
        is_a = _is_a_road(edge["ref"])
        is_b = _is_b_road(edge["ref"])
        highway = set(edge["highway"])
        if role == "strategic-spine":
            return length * (0.35 if is_a else 1.6)
        if role == "b-road-corridor":
            return length * (0.35 if is_b else 1.6)
        if role == "low-traffic":
            return length * (0.75 if highway & LOW_TRAFFIC else 4.0)
        if role == "ncn-informed":
            return length * (0.4 if edge["ncn"] else 1.3)
        return length

    return weight


def _is_b_road(refs: object) -> bool:
    return any(ref.upper().startswith("B") for ref in _tag_values(refs))


def stationary_route_option(point: Point) -> RouteOption:
    return RouteOption(
        role="direct",
        geometry=LineString([point, point]),
        length_km=0.0,
        edge_ids=[],
        a_road_share=0.0,
        ncn_share=0.0,
        bidirectional=True,
        reverse_length_km=0.0,
        reverse_edge_ids=[],
        reverse_corridor_share=1.0,
        impracticable_alongside=False,
    )


def _merge_route(lines: list[LineString]) -> LineString | None:
    unioned = unary_union(lines)
    if isinstance(unioned, LineString):
        return unioned
    merged = linemerge(unioned)
    if isinstance(merged, LineString):
        return merged
    return None


def _corridor_share(route: LineString, corridor: LineString, crs: object) -> float:
    projected = gpd.GeoSeries([route, corridor], crs=crs).to_crs(3857)
    route_geometry, corridor_geometry = projected.iloc[0], projected.iloc[1]
    if not route_geometry.length:
        return 0.0
    return route_geometry.intersection(corridor_geometry.buffer(250)).length / route_geometry.length


def _unique_options(options: list[RouteOption | None]) -> list[RouteOption]:
    result: list[RouteOption] = []
    signatures: set[tuple[str, ...]] = set()
    for option in options:
        if option is None:
            continue
        signature = (option.role, *option.edge_ids)
        if signature not in signatures:
            result.append(option)
            signatures.add(signature)
    return result


def _is_a_road(refs: object) -> bool:
    return any(str(ref).upper().startswith("A") for ref in refs)


def _truthy(value: object) -> bool:
    return str(value).lower() in {"yes", "true", "1", "-1"}


def _present(value: object) -> bool:
    return value is not None and str(value).lower() not in {"nan", "none", ""}


def _source_edge_id(row: pd.Series, fallback: object) -> str:
    """Return one stable identity for scalar or collection-valued OSM IDs."""
    return str(
        source_identity(
            row,
            ("osmid", "source_id", "osm_id", "id", "edge_id"),
            fallback,
        )
    )


def _edge_row_sort_key(item: tuple[object, pd.Series]) -> tuple[str, ...]:
    index, row = item
    geometry = row.geometry
    if isinstance(geometry, LineString) and len(geometry.coords) >= 2:
        u = str(row.get("u")) if _present(row.get("u")) else _coordinate_id(geometry.coords[0])
        v = str(row.get("v")) if _present(row.get("v")) else _coordinate_id(geometry.coords[-1])
        geometry_key = geometry.wkb_hex
    else:
        u = v = geometry_key = ""
    return (
        u,
        v,
        _source_edge_id(row, index),
        geometry_key,
        str(index),
    )


def _coordinate_id(coordinate: tuple[float, ...]) -> str:
    x = round(float(coordinate[0]), 7)
    y = round(float(coordinate[1]), 7)
    return f"xy:{0.0 if x == 0 else x:.7f}:{0.0 if y == 0 else y:.7f}"
