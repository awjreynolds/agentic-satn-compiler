"""Prepare finite urban-place journey relationships from current graph evidence.

Urban journeys are a preparation input, not a route-selection policy.  The
module binds canonical city/town points to the current RoadGraph, partitions
that graph from those bindings, and records only physical cross-partition
edges.  It therefore cannot create a Cartesian place-pair roster or infer a
connection where the current graph has no transition between place regions.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import networkx as nx
from shapely.geometry import Point
from shapely.ops import unary_union

from satn.routing import RoadGraph

_A_REFERENCE = re.compile(r"^a[0-9]+[a-z]?$", re.IGNORECASE)
_B_REFERENCE = re.compile(r"^b[0-9]+[a-z]?$", re.IGNORECASE)
_PREFERRED_CLASSES = frozenset({"a-road-reference", "a-road-highway", "cycleway", "ncn"})
_CLASS_ORDER = (
    "a-road-reference",
    "a-road-highway",
    "cycleway",
    "ncn",
    "b-road-reference",
    "local",
)
_CYCLEWAY_HIGHWAYS = frozenset(
    {
        "cycleway",
        "cycle_track",
        "cycle-track",
        "greenway",
        "path-cycleway",
        "shared_use_path",
    }
)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _stable_value(value: object) -> str:
    if value is None:
        return ""
    try:
        if value != value:  # NaN
            return ""
    except Exception:
        pass
    return str(value).strip()


def _values(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    values = value if isinstance(value, (list, tuple, set, frozenset)) else (value,)
    return tuple(sorted({_stable_value(item) for item in values if _stable_value(item)}))


def _source_id(row: Any, index: object) -> str:
    element = _stable_value(row.get("element")) or "place"
    identifier = _stable_value(row.get("id"))
    if identifier:
        return f"{element}/{identifier}"
    for column in ("source_id", "osmid", "osm_id", "place_id"):
        value = _stable_value(row.get(column))
        if value:
            return value
    return f"place/{index}"


def _place_id(source_id: str) -> str:
    return f"urban-place-{_fingerprint(source_id)[:20]}"


@dataclass(frozen=True)
class UrbanJourneyPlace:
    """One canonical in-area city/town point and its graph binding."""

    place_id: str
    source_id: str
    name: str
    place_class: str
    routing_node_id: str
    binding_distance_m: float
    coordinates: tuple[float, float]

    def canonical(self) -> dict[str, object]:
        return {
            "place_id": self.place_id,
            "source_id": self.source_id,
            "name": self.name,
            "place_class": self.place_class,
            "routing_node_id": self.routing_node_id,
            "binding_distance_m": self.binding_distance_m,
            "coordinates": list(self.coordinates),
        }


@dataclass(frozen=True)
class UrbanJourneyAdjacency:
    """One observed transition between two graph regions owned by places."""

    journey_id: str
    place_ids: tuple[str, str]
    place_names: tuple[str, str]
    routing_node_ids: tuple[str, str]
    cross_region_edge_ids: tuple[str, ...]
    road_classes: tuple[str, ...]
    preferred_classes: tuple[str, ...]

    @property
    def preferred(self) -> bool:
        return bool(self.preferred_classes)

    def canonical(self) -> dict[str, object]:
        return {
            "journey_id": self.journey_id,
            "place_ids": list(self.place_ids),
            "place_names": list(self.place_names),
            "routing_node_ids": list(self.routing_node_ids),
            "cross_region_edge_ids": list(self.cross_region_edge_ids),
            "road_classes": list(self.road_classes),
            "preferred_classes": list(self.preferred_classes),
            "preferred": self.preferred,
        }


@dataclass(frozen=True)
class UrbanJourneyIssue:
    """A bounded, inspectable urban-place evidence gap."""

    reason: str
    detail: str
    source_id: str | None = None

    def canonical(self) -> dict[str, object]:
        return {
            "reason": self.reason,
            "detail": self.detail,
            "source_id": self.source_id,
        }


@dataclass(frozen=True)
class UrbanJourneyPreparation:
    """Canonical urban places and graph-observed adjacencies."""

    places: tuple[UrbanJourneyPlace, ...]
    adjacencies: tuple[UrbanJourneyAdjacency, ...]
    issues: tuple[UrbanJourneyIssue, ...]
    preparation_fingerprint: str

    def canonical(self) -> dict[str, object]:
        return {
            "places": [item.canonical() for item in self.places],
            "adjacencies": [item.canonical() for item in self.adjacencies],
            "issues": [item.canonical() for item in self.issues],
        }

    def metadata(self) -> dict[str, object]:
        return {
            **self.canonical(),
            "preparation_fingerprint": self.preparation_fingerprint,
            "place_count": len(self.places),
            "adjacency_count": len(self.adjacencies),
        }


def prepare_urban_journeys(
    *,
    label_places: gpd.GeoDataFrame,
    area_definition: gpd.GeoDataFrame | None,
    road_graph: RoadGraph,
) -> UrbanJourneyPreparation:
    """Bind in-area city/town points and derive observed region adjacencies.

    Every adjacency comes from an edge joining two multi-source graph regions.
    All such edges are retained, including B-road and local transitions; the
    preferred class tuple is evidence for the caller's existing candidate
    policy and does not itself admit a route.
    """

    places, issues = _bind_places(label_places, area_definition, road_graph)
    physical_graph, edge_records = _physical_graph(road_graph)
    owners = _region_owners(physical_graph, places)
    adjacencies = _adjacencies(places, owners, edge_records)
    adjacency_place_ids = {place_id for item in adjacencies for place_id in item.place_ids}
    for place in places:
        if place.place_id not in adjacency_place_ids:
            issues.append(
                UrbanJourneyIssue(
                    reason="urban-place-no-cross-region-adjacency",
                    detail=(
                        f"{place.name} is bound to {place.routing_node_id}, but no current "
                        "graph edge crosses from its region to another urban-place region"
                    ),
                    source_id=place.source_id,
                )
            )
    canonical = {
        "places": [item.canonical() for item in places],
        "adjacencies": [item.canonical() for item in adjacencies],
        "issues": [
            item.canonical()
            for item in sorted(issues, key=lambda item: (item.reason, item.source_id or ""))
        ],
    }
    return UrbanJourneyPreparation(
        places=tuple(places),
        adjacencies=tuple(adjacencies),
        issues=tuple(sorted(issues, key=lambda item: (item.reason, item.source_id or ""))),
        preparation_fingerprint=_fingerprint(canonical),
    )


def _bind_places(
    label_places: gpd.GeoDataFrame,
    area_definition: gpd.GeoDataFrame | None,
    road_graph: RoadGraph,
) -> tuple[list[UrbanJourneyPlace], list[UrbanJourneyIssue]]:
    if label_places is None or label_places.empty:
        return [], []
    frame = label_places
    if frame.crs is None and road_graph.crs is not None:
        frame = frame.set_crs(road_graph.crs, allow_override=True)
    area = None
    if area_definition is not None and not area_definition.empty:
        area_frame = area_definition
        if area_frame.crs is not None and frame.crs is not None:
            area_frame = area_frame.to_crs(frame.crs)
        area = unary_union(area_frame.geometry)
    rows: dict[str, Any] = {}
    for index, row in frame.sort_index().iterrows():
        place_class = _stable_value(row.get("place"))
        if not place_class:
            place_class = _stable_value(row.get("kind"))
        place_class = place_class.casefold()
        if place_class not in {"city", "town"}:
            continue
        if str(_stable_value(row.get("element"))).casefold() == "relation":
            continue
        geometry = row.geometry
        if not isinstance(geometry, Point) or geometry.is_empty:
            continue
        if area is not None and not area.covers(geometry):
            continue
        source_id = _source_id(row, index)
        if source_id in rows:
            continue
        name = _stable_value(row.get("name")) or source_id
        point = geometry
        if frame.crs is not None and road_graph.crs is not None:
            point = gpd.GeoSeries([point], crs=frame.crs).to_crs(road_graph.crs).iloc[0]
        try:
            routing_node_id, distance_m = road_graph.nearest_node(point)
        except (ValueError, KeyError) as exc:
            issues = UrbanJourneyIssue(
                reason="urban-place-not-bound",
                detail=f"{source_id} cannot bind to current RoadGraph: {exc}",
                source_id=source_id,
            )
            rows[source_id] = issues
            continue
        rows[source_id] = UrbanJourneyPlace(
            place_id=_place_id(source_id),
            source_id=source_id,
            name=name,
            place_class=place_class,
            routing_node_id=str(routing_node_id),
            binding_distance_m=float(distance_m),
            coordinates=(float(point.x), float(point.y)),
        )
    places = [item for item in rows.values() if isinstance(item, UrbanJourneyPlace)]
    issues = [item for item in rows.values() if isinstance(item, UrbanJourneyIssue)]
    return sorted(places, key=lambda item: item.source_id), issues


def _physical_graph(
    road_graph: RoadGraph,
) -> tuple[nx.Graph, dict[tuple[str, str], list[dict[str, object]]]]:
    graph = nx.Graph()
    edge_records: dict[tuple[str, str], list[dict[str, object]]] = {}
    for left, right, attrs in road_graph.graph.edges(data=True):
        left_id, right_id = str(left), str(right)
        length_m = attrs.get("length_m")
        try:
            length = float(length_m)
        except (TypeError, ValueError):
            continue
        if length < 0:
            continue
        graph.add_edge(left_id, right_id, length_m=length)
        key = tuple(sorted((left_id, right_id)))
        edge_records.setdefault(key, []).append(
            {
                "edge_id": _stable_value(attrs.get("edge_id")),
                "highway": _values(attrs.get("highway")),
                "ref": _values(attrs.get("ref")),
                "ncn": bool(attrs.get("ncn")),
            }
        )
    return graph, edge_records


def _region_owners(
    graph: nx.Graph,
    places: list[UrbanJourneyPlace],
) -> dict[str, str]:
    source_nodes = {place.routing_node_id: place.place_id for place in places}
    if not source_nodes:
        return {}
    reachable = nx.multi_source_dijkstra_path(graph, sorted(source_nodes), weight="length_m")
    return {
        str(node): source_nodes[str(path[0])]
        for node, path in reachable.items()
        if path and str(path[0]) in source_nodes
    }


def _adjacencies(
    places: list[UrbanJourneyPlace],
    owners: dict[str, str],
    edge_records: dict[tuple[str, str], list[dict[str, object]]],
) -> list[UrbanJourneyAdjacency]:
    places_by_id = {place.place_id: place for place in places}
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for (left, right), records in sorted(edge_records.items()):
        left_owner, right_owner = owners.get(left), owners.get(right)
        if not left_owner or not right_owner or left_owner == right_owner:
            continue
        place_ids = tuple(sorted((left_owner, right_owner)))
        item = grouped.setdefault(place_ids, {"edge_ids": set(), "classes": set()})
        for record in records:
            edge_id = str(record["edge_id"])
            if edge_id:
                item["edge_ids"].add(edge_id)
            item["classes"].update(_edge_classes(record))
    result: list[UrbanJourneyAdjacency] = []
    for place_ids, item in sorted(grouped.items()):
        left_place, right_place = (places_by_id[place_ids[0]], places_by_id[place_ids[1]])
        classes = tuple(item["classes"])
        preferred_classes = tuple(
            item for item in _CLASS_ORDER if item in classes and item in _PREFERRED_CLASSES
        )
        journey_id = f"urban-journey-{_fingerprint([*place_ids, sorted(item['edge_ids'])])[:20]}"
        result.append(
            UrbanJourneyAdjacency(
                journey_id=journey_id,
                place_ids=place_ids,
                place_names=(left_place.name, right_place.name),
                routing_node_ids=(left_place.routing_node_id, right_place.routing_node_id),
                cross_region_edge_ids=tuple(sorted(item["edge_ids"])),
                road_classes=tuple(
                    class_name for class_name in _CLASS_ORDER if class_name in classes
                ),
                preferred_classes=preferred_classes,
            )
        )
    return result


def _edge_classes(record: dict[str, object]) -> set[str]:
    refs = tuple(record.get("ref", ()))
    highways = {value.casefold() for value in tuple(record.get("highway", ()))}
    classes: set[str] = set()
    if any(_A_REFERENCE.fullmatch(ref) for ref in refs):
        classes.add("a-road-reference")
    if highways.intersection({"primary", "trunk", "primary_link", "trunk_link"}):
        classes.add("a-road-highway")
    if highways.intersection(_CYCLEWAY_HIGHWAYS):
        classes.add("cycleway")
    if bool(record.get("ncn")):
        classes.add("ncn")
    if any(_B_REFERENCE.fullmatch(ref) for ref in refs):
        classes.add("b-road-reference")
    if not classes:
        classes.add("local")
    return classes
