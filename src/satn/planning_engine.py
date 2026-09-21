"""A small JSON planning boundary over one validated source snapshot.

The engine keeps admission and proposal state separate.  It deliberately does
not call a model or a provider: an intelligent planner can submit operations at
the public boundary, while this module retains every governed source fact and
checks the resulting references before projecting an output.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise

import geopandas as gpd
from pyproj import CRS
from shapely.geometry import LineString, MultiLineString, Point, mapping, shape
from shapely.ops import linemerge, unary_union

from satn.content_identity import (
    canonical_network_geometry,
    canonical_network_geometry_fingerprint,
)
from satn.evidence import mark_ncn_edges
from satn.models import AreaConfig
from satn.planning_contracts import _source_ref, _stable_id, _topology_fact
from satn.routing import RoadGraph, RouteOption, choose_alignment
from satn.sources import load_snapshot
from satn.urban_journeys import UrbanJourneyPreparation, prepare_urban_journeys

_LINE_GEOMETRIES = (LineString, MultiLineString)
_GEOMETRIES = (Point, LineString, MultiLineString)

_CONTEXT_CLASSIFICATIONS = {
    "a-road-spine": "a-road",
    "ncn-route": "ncn-route",
    "ncn-link": "ncn-link",
    "declassified-ncn-route": "declassified-ncn-route",
    "greenway-cycleway": "greenway-cycleway",
    "cycleway": "cycleway",
    "road-cycleway": "cycleway",
    "bicycle-priority-road": "cycleway",
    "bicycle-route": "cycleway",
    "cycle-access-path": "cycleway",
    "shared-use-path": "cycleway",
    "proposed-cycleway": "proposed-cycleway",
    "proposed-new-corridor": "proposed-cycleway",
    "bridleway": "bridleway",
    "former-railway": "former-railway",
}

_DEPARTURE_OUTCOMES = {"alternate", "unresolved", "no-loss"}
_EVIDENCE_RELATIONS = {"supports", "contradicts", "does_not_establish"}
_PLANNING_CODE_CONTRACT = "planning-engine/v1"
_CONTEXT_SHARED_FIELDS = ("brief", "source_corridors", "places", "obligations", "candidates")
_CONTEXT_STATE_FINGERPRINT_FIELDS = frozenset(
    {
        "brief",
        "brief_fingerprint",
        "candidates",
        "obligations",
        "parent_problem_id",
        "places",
        "problem_fingerprint",
        "schema_version",
        "source_corridors",
    }
)


@dataclass(frozen=True)
class _PlanningContext:
    """Verified, run-owned bindings for one immutable planning problem."""

    problem_id: str
    input_fingerprint: str
    brief_fingerprint: str
    problem_fields: Mapping[str, object]
    state_fields: Mapping[str, object]
    indexes: Mapping[str, frozenset[str]]
    semantic_field_bytes: Mapping[str, bytes]
    state_field_bytes: Mapping[str, bytes]
    problem_ref: str | None = None


_DEFAULT_BRIEF = {
    "brief_ref": "satn-planning-brief/resolved-corridor-policy-v1",
    "corridor_policy": {
        "a_roads": "mandatory-in-scope-retain-source-geometry",
        "departure_outcomes": ["alternate", "unresolved", "no-loss"],
        "source_designation_is_current_usability": False,
        "current_and_future_are_distinct": True,
    },
    "source_families": [
        "a-road",
        "ncn-route",
        "ncn-link",
        "declassified-ncn-route",
        "cycleway",
        "proposed-cycleway",
        "greenway-cycleway",
        "bridleway",
        "former-railway",
    ],
    "completion": {
        "retain_unresolved_source_only_corridors": True,
        "retain_explicit_departure_geometry": True,
    },
}


def _canonical(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("planning identity cannot contain non-finite values")
        return value
    return value


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_copy(value: object) -> object:
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True))


def _crs_identity(value: object) -> str:
    if value is None:
        raise ValueError("planning geometry requires an explicit CRS")
    crs = CRS.from_user_input(value)
    authority = crs.to_authority()
    return f"{authority[0]}:{authority[1]}" if authority is not None else crs.to_wkt()


def _present_identifier(value: object) -> bool:
    if value is None:
        return False
    try:
        if bool(math.isnan(value)):  # type: ignore[arg-type]
            return False
    except (TypeError, ValueError):
        pass
    return bool(str(value).strip()) and str(value).strip().lower() != "nan"


def _required_identifier(value: object, label: str) -> str:
    if not _present_identifier(value):
        raise ValueError(f"planning source requires {label}")
    return str(value)


def _geometry_ref(geometry: object, crs: object, source_ref: str | None) -> dict[str, object]:
    if not isinstance(geometry, _GEOMETRIES) or geometry.is_empty or not geometry.is_valid:
        raise ValueError("planning source geometry must be valid and non-empty")
    crs_name = _crs_identity(crs)
    content_fingerprint = canonical_network_geometry_fingerprint(geometry, crs_name)
    return {
        "geometry_id": _stable_id("geometry", content_fingerprint),
        "crs": crs_name,
        "geometry_kind": geometry.geom_type,
        "content_fingerprint": content_fingerprint,
        "source_ref": source_ref,
        "geometry": _json_copy(mapping(geometry)),
    }


def _source_hash(
    *,
    source_kind: str,
    source_refs: Sequence[Mapping[str, str]],
    feature_type: str,
    name: object,
    geometry_ref: Mapping[str, object],
) -> str:
    return _fingerprint(
        {
            "source_kind": source_kind,
            "source_refs": list(source_refs),
            "feature_type": feature_type,
            "name": name,
            "geometry_fingerprint": geometry_ref["content_fingerprint"],
            "crs": geometry_ref["crs"],
        }
    )


def _graph_topology(
    geometry: object,
    crs: object,
    graph: RoadGraph | None,
) -> dict[str, object]:
    topology = _topology_fact(geometry, crs, graph)
    if graph is None or not topology["node_ids"]:
        return topology
    node_ids = [str(item) for item in topology["node_ids"]]
    directed_edge_ids: set[str] = set()
    source_edge_ids: set[str] = set()
    for node_id in node_ids:
        source_edge_ids.update(graph.edge_ids_for_node(node_id))
        for _left, _right, data in graph.graph.in_edges(node_id, data=True):
            directed_edge_ids.add(str(data["directed_edge_id"]))
        for _left, _right, data in graph.graph.out_edges(node_id, data=True):
            directed_edge_ids.add(str(data["directed_edge_id"]))
    return {
        **topology,
        "directed_edge_ids": sorted(directed_edge_ids),
        "source_edge_ids": sorted(source_edge_ids),
    }


def _context_classification(value: object) -> str | None:
    if not _present_identifier(value):
        return None
    normalized = str(value).strip().lower().replace("_", "-")
    return _CONTEXT_CLASSIFICATIONS.get(normalized)


def _corridor_from_row(
    row: Mapping[str, object],
    *,
    crs: object,
    source_kind: str,
    classification: str,
    graph: RoadGraph | None,
    evidence_key: str = "evidence_id",
    source_key: str = "source_id",
    name_keys: Sequence[str] = ("name",),
    scope_key: str = "network_scope",
    in_scope: bool = True,
) -> dict[str, object]:
    source_ref = _source_ref(
        row.get(evidence_key),
        row.get(source_key),
    )
    geometry = row.get("geometry")
    if not isinstance(geometry, _LINE_GEOMETRIES) or geometry.is_empty or not geometry.is_valid:
        raise ValueError("planning source corridor requires valid non-empty line geometry")
    geometry_ref = _geometry_ref(geometry, crs, source_ref["source_id"])
    name = next((row.get(key) for key in name_keys if _present_identifier(row.get(key))), None)
    source_refs = [source_ref]
    provenance = {
        "source_kind": source_kind,
        "source_refs": source_refs,
        "evidence_refs": [source_ref["evidence_id"]],
        "source_hash": _source_hash(
            source_kind=source_kind,
            source_refs=source_refs,
            feature_type=classification,
            name=name,
            geometry_ref=geometry_ref,
        ),
    }
    topology = _graph_topology(geometry, crs, graph)
    corridor_id = _stable_id(
        "planning-corridor",
        {
            "source_kind": source_kind,
            "source_refs": source_refs,
            "classification": classification,
            "geometry": geometry_ref["content_fingerprint"],
        },
    )
    scope = row.get(scope_key)
    scope_status = str(scope) if _present_identifier(scope) else "unresolved"
    current_cycle_asset = row.get("current_cycle_asset")
    if current_cycle_asset is True or str(current_cycle_asset).strip().lower() in {
        "true",
        "yes",
        "1",
    }:
        provision_status = "current"
    elif current_cycle_asset is False or str(current_cycle_asset).strip().lower() in {
        "false",
        "no",
        "0",
    }:
        provision_status = "future"
    else:
        provision_status = "unknown"
    mandatory = classification == "a-road" and in_scope
    return {
        "corridor_id": corridor_id,
        "section_id": f"{corridor_id}-section",
        "source_refs": source_refs,
        "name": str(name) if name is not None else None,
        "geometry_ref": geometry_ref,
        "classification": classification,
        "scope_status": scope_status,
        "in_scope": in_scope,
        "current_cycle_asset": (
            True
            if provision_status == "current"
            else False
            if provision_status == "future"
            else None
        ),
        "provision_status": provision_status,
        "mandatory_planning_corridor": mandatory,
        "topology_fact": topology,
        "evidence_refs": [source_ref["evidence_id"]],
        "provenance": provenance,
        "departure_disposition": "not-assessed",
        "decision_ref": None,
        "status": "admitted-with-unknowns" if topology["status"] == "unresolved" else "admitted",
    }


def _corridor_identity_key(corridor: Mapping[str, object]) -> tuple[object, ...]:
    """Identify one semantic source corridor independent of edge direction."""

    provenance = corridor.get("provenance")
    provenance_map = provenance if isinstance(provenance, Mapping) else {}
    source_refs = corridor.get("source_refs", provenance_map.get("source_refs", []))
    reference_keys = tuple(
        sorted(
            (
                str(reference.get("evidence_id")),
                str(reference.get("source_id")),
            )
            for reference in source_refs
            if isinstance(reference, Mapping)
        )
    )
    geometry_ref = corridor.get("geometry_ref")
    geometry_map = geometry_ref if isinstance(geometry_ref, Mapping) else {}
    return (
        str(provenance_map.get("source_kind")),
        reference_keys,
        str(corridor.get("classification")),
        str(geometry_map.get("geometry_kind")),
        str(geometry_map.get("crs")),
        str(geometry_map.get("content_fingerprint")),
    )


def _merge_equivalent_corridors(
    corridors: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Collapse reverse directed records while retaining their admitted facts."""

    merged: dict[tuple[object, ...], dict[str, object]] = {}
    for corridor in corridors:
        key = _corridor_identity_key(corridor)
        candidate = _json_copy(corridor)
        current = merged.get(key)
        if current is None:
            merged[key] = candidate
            continue

        current_provenance = dict(current.get("provenance", {}))
        candidate_provenance = candidate.get("provenance", {})
        if not isinstance(candidate_provenance, Mapping):
            candidate_provenance = {}
        source_refs = sorted(
            {
                json.dumps(_canonical(reference), sort_keys=True, separators=(",", ":"))
                for reference in list(current.get("source_refs", []))
                + list(candidate.get("source_refs", []))
                if isinstance(reference, Mapping)
            }
        )
        current["source_refs"] = [_json_copy(json.loads(reference)) for reference in source_refs]
        evidence_refs = sorted(
            {
                str(reference)
                for reference in list(current.get("evidence_refs", []))
                + list(candidate.get("evidence_refs", []))
                if _present_identifier(reference)
            }
        )
        current["evidence_refs"] = evidence_refs
        geometry_ref = current["geometry_ref"]
        if isinstance(geometry_ref, Mapping):
            normalized_geometry = canonical_network_geometry(
                shape(geometry_ref["geometry"]), geometry_ref["crs"]
            )
            geometry_ref = dict(geometry_ref)
            geometry_ref["geometry"] = normalized_geometry["geometry"]
            current["geometry_ref"] = geometry_ref
        current_provenance["source_refs"] = _json_copy(current["source_refs"])
        current_provenance["evidence_refs"] = evidence_refs

        names = {
            str(name)
            for corridor_map, provenance_map in (
                (current, current_provenance),
                (candidate, candidate_provenance),
            )
            for name in [
                corridor_map.get("name"),
                *provenance_map.get("name_variants", []),
            ]
            if _present_identifier(name)
        }
        if names:
            ordered_names = sorted(names)
            current["name"] = ordered_names[0]
            if len(ordered_names) > 1:
                current_provenance["name_variants"] = ordered_names

        source_hashes = {
            str(source_hash)
            for provenance_map in (current_provenance, candidate_provenance)
            for source_hash in [
                *provenance_map.get("source_hashes", []),
                provenance_map.get("source_hash"),
            ]
            if _present_identifier(source_hash)
        }
        current_provenance["source_hash"] = _source_hash(
            source_kind=str(current_provenance["source_kind"]),
            source_refs=current["source_refs"],
            feature_type=str(current["classification"]),
            name=current.get("name"),
            geometry_ref=current["geometry_ref"],
        )
        if len(source_hashes) > 1:
            current_provenance["source_hashes"] = sorted(source_hashes)
        current["provenance"] = current_provenance

        current["in_scope"] = bool(current.get("in_scope")) or bool(candidate.get("in_scope"))
        current["mandatory_planning_corridor"] = bool(
            current.get("mandatory_planning_corridor")
        ) or bool(candidate.get("mandatory_planning_corridor"))
        topology = current.get("topology_fact")
        candidate_topology = candidate.get("topology_fact")
        if isinstance(topology, Mapping) and isinstance(candidate_topology, Mapping):
            topology = dict(topology)
            for field in ("node_ids", "directed_edge_ids", "source_edge_ids"):
                topology[field] = sorted(
                    {
                        str(value)
                        for value in list(topology.get(field, []))
                        + list(candidate_topology.get(field, []))
                    }
                )
            topology["graph_attachment"] = (
                "attached"
                if "attached"
                in {
                    topology.get("graph_attachment"),
                    candidate_topology.get("graph_attachment"),
                }
                else topology.get("graph_attachment")
            )
            topology["status"] = (
                "resolved"
                if "resolved" in {topology.get("status"), candidate_topology.get("status")}
                else topology.get("status")
            )
            current["topology_fact"] = topology
        if current.get("status") != "admitted" and candidate.get("status") == "admitted":
            current["status"] = "admitted"

    return list(merged.values())


def _source_corridors(
    source: Mapping[str, object], graph: RoadGraph | None
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    corridors: list[dict[str, object]] = []
    gaps: list[dict[str, object]] = []
    unknowns: list[dict[str, object]] = []
    boundary = source.get("boundary")

    def is_in_scope(geometry: object, crs: object) -> bool:
        if not isinstance(boundary, gpd.GeoDataFrame) or boundary.empty:
            return True
        governed_area = boundary.to_crs(crs).geometry.union_all()
        return bool(geometry.intersects(governed_area))

    context = source.get("context")
    if isinstance(context, gpd.GeoDataFrame):
        for _, row in context.iterrows():
            classification = _context_classification(row.get("feature_type"))
            if classification is None:
                continue
            if not isinstance(row.geometry, _LINE_GEOMETRIES):
                raise ValueError(
                    f"configured source family {classification} requires line geometry"
                )
            corridor = _corridor_from_row(
                row,
                crs=context.crs,
                source_kind="context",
                classification=classification,
                graph=graph,
                in_scope=is_in_scope(row.geometry, context.crs),
            )
            corridors.append(corridor)
    official = source.get("official_road_classification")
    if isinstance(official, gpd.GeoDataFrame):
        for _, row in official.iterrows():
            if str(row.get("official_classification", "")).strip().lower() != "a-road":
                continue
            corridor = _corridor_from_row(
                row,
                crs=official.crs,
                source_kind="official-road-classification",
                classification="a-road",
                graph=graph,
                evidence_key="official_feature_id",
                name_keys=("official_road_number", "official_road_name"),
                in_scope=is_in_scope(row.geometry, official.crs),
            )
            corridors.append(corridor)
    corridors = _merge_equivalent_corridors(corridors)
    for corridor in sorted(corridors, key=lambda item: str(item["corridor_id"])):
        if not corridor["in_scope"] or corridor["topology_fact"]["status"] != "unresolved":
            continue
        gap_id = _stable_id("planning-gap", corridor["corridor_id"])
        gaps.append(
            {
                "gap_id": gap_id,
                "subject_id": corridor["corridor_id"],
                "obligation_id": corridor["corridor_id"],
                "reason": "source corridor has no routing-graph attachment",
                "status": "unresolved",
            }
        )
        unknowns.append(
            {
                "unknown_id": _stable_id("unknown", corridor["corridor_id"]),
                "subject_id": corridor["corridor_id"],
                "claim": "routing-graph attachment",
                "reason": "source corridor has no matching routing-graph geometry",
                "status": "unresolved",
            }
        )
    return corridors, gaps, unknowns


def _admitted_network(source: Mapping[str, object]) -> gpd.GeoDataFrame | None:
    """Return an NCN-enriched copy of the admitted routing network."""

    network = source.get("network")
    if not isinstance(network, gpd.GeoDataFrame):
        return None
    context = source.get("context")
    if not isinstance(context, gpd.GeoDataFrame):
        return network.copy(deep=True)
    return mark_ncn_edges(network, context)


def _planning_place_record(
    *,
    place_id: str,
    name: str | None,
    kind: str | None,
    place_class: str | None,
    source_id: str | None,
    geometry: Point,
    crs: object,
) -> dict[str, object]:
    source_refs = [{"source_id": source_id}] if source_id is not None else []
    geometry_ref = _geometry_ref(geometry, crs, source_id)
    return {
        "place_id": place_id,
        "name": name,
        "kind": kind,
        "place_class": place_class,
        "source_refs": source_refs,
        "geometry_ref": geometry_ref,
        "provenance": {
            "source_refs": source_refs,
            "evidence_refs": [],
            "source_hash": _fingerprint(
                {
                    "place_id": place_id,
                    "source_refs": source_refs,
                    "geometry": geometry_ref["content_fingerprint"],
                }
            ),
        },
    }


def _urban_source_aliases(source_id: str) -> tuple[str, ...]:
    aliases = [source_id]
    if "/" in source_id:
        aliases.append(source_id.rsplit("/", 1)[-1])
    return tuple(dict.fromkeys(aliases))


def _source_matches(
    source_id: str,
    existing_by_source: Mapping[str, Sequence[object]],
) -> list[object]:
    exact = existing_by_source.get(source_id)
    if exact:
        return list(exact)
    return [
        candidate
        for alias in _urban_source_aliases(source_id)[1:]
        for candidate in existing_by_source.get(alias, [])
    ]


def _place_records(
    source: Mapping[str, object],
    urban_preparation: UrbanJourneyPreparation | None = None,
    *,
    urban_crs: object | None = None,
) -> list[dict[str, object]]:
    places: list[dict[str, object]] = []
    frame = source.get("places")
    if isinstance(frame, gpd.GeoDataFrame):
        for _, row in frame.iterrows():
            place_id = _required_identifier(row.get("place_id"), "place identity")
            geometry = row.geometry
            if not isinstance(geometry, Point) or geometry.is_empty or not geometry.is_valid:
                raise ValueError("planning place requires valid non-empty point geometry")
            source_id = (
                _required_identifier(row.get("source_id"), "place source identity")
                if _present_identifier(row.get("source_id"))
                else None
            )
            places.append(
                _planning_place_record(
                    place_id=place_id,
                    name=(str(row.get("name")) if _present_identifier(row.get("name")) else None),
                    kind=(str(row.get("kind")) if _present_identifier(row.get("kind")) else None),
                    place_class=(
                        str(row.get("place_class"))
                        if _present_identifier(row.get("place_class"))
                        else None
                    ),
                    source_id=source_id,
                    geometry=geometry,
                    crs=frame.crs,
                )
            )
    if urban_preparation is None or urban_crs is None:
        return sorted(places, key=lambda item: str(item["place_id"]))

    existing_by_source: dict[str, list[dict[str, object]]] = {}
    for place in places:
        for source_ref in place.get("source_refs", []):
            if isinstance(source_ref, Mapping) and isinstance(source_ref.get("source_id"), str):
                existing_by_source.setdefault(source_ref["source_id"], []).append(place)
    for urban_place in urban_preparation.places:
        matches = {
            id(candidate)
            for candidate in _source_matches(urban_place.source_id, existing_by_source)
        }
        if matches:
            continue
        places.append(
            _planning_place_record(
                place_id=urban_place.place_id,
                name=urban_place.name,
                kind="community",
                place_class=urban_place.place_class,
                source_id=urban_place.source_id,
                geometry=Point(urban_place.coordinates),
                crs=urban_crs,
            )
        )
    return sorted(places, key=lambda item: str(item["place_id"]))


def _urban_preparation(
    source: Mapping[str, object], graph: RoadGraph | None
) -> UrbanJourneyPreparation | None:
    if graph is None:
        return None
    label_places = source.get("label_places")
    if not isinstance(label_places, gpd.GeoDataFrame):
        return None
    boundary = source.get("boundary")
    if not isinstance(boundary, gpd.GeoDataFrame):
        boundary = None
    return prepare_urban_journeys(
        label_places=label_places,
        area_definition=boundary,
        road_graph=graph,
    )


def _prepared_connections(
    preparation: UrbanJourneyPreparation | None,
    places: Sequence[Mapping[str, object]],
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    if preparation is None:
        return {}, []
    existing_by_source: dict[str, list[str]] = {}
    for place in places:
        place_id = place.get("place_id")
        if not isinstance(place_id, str):
            continue
        for source_ref in place.get("source_refs", []):
            if isinstance(source_ref, Mapping) and isinstance(source_ref.get("source_id"), str):
                existing_by_source.setdefault(source_ref["source_id"], []).append(place_id)
    urban_to_planning: dict[str, str] = {}
    issues = [item.canonical() for item in preparation.issues]
    for urban_place in preparation.places:
        matches = set(_source_matches(urban_place.source_id, existing_by_source))
        if len(matches) == 1:
            urban_to_planning[urban_place.place_id] = next(iter(matches))
        elif len(matches) > 1:
            issues.append(
                {
                    "reason": "urban-place-source-binding-ambiguous",
                    "detail": urban_place.source_id,
                    "source_id": urban_place.source_id,
                }
            )
        else:
            urban_to_planning[urban_place.place_id] = urban_place.place_id
    prepared: dict[str, dict[str, object]] = {}
    for adjacency in preparation.adjacencies:
        record = adjacency.canonical()
        record.update(
            {
                "connection_id": adjacency.journey_id,
                "origin_place_id": urban_to_planning.get(adjacency.place_ids[0]),
                "destination_place_id": urban_to_planning.get(adjacency.place_ids[1]),
                "corridor_refs": [],
                "current_or_future": "unknown",
            }
        )
        prepared[adjacency.journey_id] = record
    return prepared, sorted(issues, key=lambda item: json.dumps(item, sort_keys=True))


def _obligation_records(
    places: Sequence[Mapping[str, object]], source: Mapping[str, object]
) -> list[dict[str, object]]:
    obligations: list[dict[str, object]] = []
    for place in places:
        place_id = str(place["place_id"])
        obligations.append(
            {
                "obligation_id": _stable_id("network-place-obligation", place_id),
                "obligation_kind": "network-place-access",
                "place_refs": [place_id],
                "endpoint_refs": [],
                "mandatory": True,
                "status": "governed-unresolved",
                "source_refs": _json_copy(place["source_refs"]),
                "evidence_refs": [],
            }
        )
    context = source.get("context")
    if isinstance(context, gpd.GeoDataFrame) and "school_obligation_eligible" in context:
        for _, row in context.iterrows():
            eligible = row.get("school_obligation_eligible")
            if eligible is not True and str(eligible).lower() not in {"true", "1", "yes"}:
                continue
            source_ref = _source_ref(row.get("evidence_id"), row.get("source_id"))
            obligation_id = _stable_id("school-access-obligation", source_ref["source_id"])
            obligations.append(
                {
                    "obligation_id": obligation_id,
                    "obligation_kind": "school-access",
                    "place_refs": [],
                    "endpoint_refs": [],
                    "mandatory": True,
                    "status": "governed-unresolved",
                    "source_refs": [source_ref],
                    "evidence_refs": [source_ref["evidence_id"]],
                }
            )
    return sorted(
        {str(item["obligation_id"]): item for item in obligations}.values(),
        key=lambda item: str(item["obligation_id"]),
    )


def _route_candidate(
    corridor: Mapping[str, object],
    option: RouteOption,
    start_node: str,
    end_node: str,
    graph: RoadGraph,
) -> dict[str, object]:
    geometry = option.geometry
    geometry_ref = _geometry_ref(geometry, graph.crs, str(corridor["corridor_id"]))
    candidate_id = _stable_id(
        "planning-candidate",
        {
            "corridor_id": corridor["corridor_id"],
            "role": option.role,
            "directed_edge_ids": option.directed_edge_ids,
        },
    )
    return {
        "candidate_id": candidate_id,
        "obligation_id": corridor["corridor_id"],
        "source_corridor_refs": [corridor["corridor_id"]],
        "role": option.role,
        "status": "admitted",
        "current_or_future": corridor["provision_status"],
        "endpoint_provenance": {
            "start_node_id": start_node,
            "end_node_id": end_node,
            "source_geometry_ref": _json_copy(corridor["geometry_ref"]),
            "source_edge_ids": [str(item) for item in option.edge_ids],
            "directed_edge_ids": [str(item) for item in option.directed_edge_ids],
        },
        "graph_path": {
            "source_edge_ids": [str(item) for item in option.edge_ids],
            "directed_edge_ids": [str(item) for item in option.directed_edge_ids],
            "length_km": option.length_km,
            "a_road_share": option.a_road_share,
            "ncn_share": option.ncn_share,
        },
        "geometry_ref": geometry_ref,
        "provenance": {
            "source_refs": _json_copy(corridor["source_refs"]),
            "evidence_refs": _json_copy(corridor["evidence_refs"]),
            "source_corridor_ref": corridor["corridor_id"],
            "source_geometry_ref": _json_copy(corridor["geometry_ref"]),
        },
    }


def _graph_candidates(
    corridors: Sequence[Mapping[str, object]], graph: RoadGraph | None
) -> list[dict[str, object]]:
    if graph is None:
        return []
    candidates: list[dict[str, object]] = []
    for corridor in corridors:
        node_ids = [str(item) for item in corridor["topology_fact"]["node_ids"]]
        if len(node_ids) < 2:
            continue
        geometry_ref = corridor.get("geometry_ref")
        if not isinstance(geometry_ref, Mapping):
            continue
        try:
            geometry = shape(geometry_ref["geometry"])
            source_geometry = (
                gpd.GeoSeries([geometry], crs=geometry_ref["crs"]).to_crs(graph.crs).iloc[0]
            )
            if isinstance(source_geometry, LineString):
                endpoint_points = (
                    Point(source_geometry.coords[0]),
                    Point(source_geometry.coords[-1]),
                )
            elif isinstance(source_geometry, MultiLineString):
                endpoint_points = (
                    Point(source_geometry.geoms[0].coords[0]),
                    Point(source_geometry.geoms[-1].coords[-1]),
                )
            else:
                continue
            attachments = {node_id: graph.projected_node(node_id) for node_id in node_ids}
            start_node = min(
                node_ids,
                key=lambda node_id: (
                    attachments[node_id].distance(endpoint_points[0])
                    if attachments[node_id] is not None
                    else float("inf"),
                    node_id,
                ),
            )
            end_candidates = [node_id for node_id in node_ids if node_id != start_node]
            end_node = min(
                end_candidates,
                key=lambda node_id: (
                    attachments[node_id].distance(endpoint_points[1])
                    if attachments[node_id] is not None
                    else float("inf"),
                    node_id,
                ),
            )
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        try:
            _selected, options, _reason = choose_alignment(
                graph,
                start_node,
                end_node,
                strategic_use=True,
            )
        except (KeyError, RuntimeError, ValueError):
            continue
        for option in options:
            if not option.directed_edge_ids:
                continue
            candidates.append(_route_candidate(corridor, option, start_node, end_node, graph))
    return sorted(candidates, key=lambda item: str(item["candidate_id"]))


def _graph_evidence(graph: RoadGraph | None) -> dict[str, object] | None:
    """Retain the admitted directed edge facts needed for offline replay."""

    if graph is None:
        return None
    directed_edges: list[dict[str, object]] = []
    for from_node, to_node, data in sorted(
        graph.graph.edges(data=True),
        key=lambda item: (
            str(item[2].get("directed_edge_id")),
            str(item[0]),
            str(item[1]),
        ),
    ):
        directed_edge_id = data.get("directed_edge_id")
        source_edge_id = data.get("edge_id")
        geometry = data.get("geometry")
        if (
            not _present_identifier(directed_edge_id)
            or not _present_identifier(source_edge_id)
            or not isinstance(geometry, LineString)
        ):
            continue
        source_facts = data.get("source_facts", {})
        directed_edges.append(
            {
                "directed_edge_id": str(directed_edge_id),
                "source_edge_id": str(source_edge_id),
                "from_node_id": str(from_node),
                "to_node_id": str(to_node),
                "road_facts": dict(source_facts) if isinstance(source_facts, Mapping) else {},
                "geometry_ref": _geometry_ref(geometry, graph.crs, str(source_edge_id)),
            }
        )
    return {
        "crs": _crs_identity(graph.crs),
        "directed_edges": directed_edges,
    }


def _place_endpoint(
    place: Mapping[str, object], graph: RoadGraph
) -> tuple[str, float, Point] | None:
    geometry_ref = place.get("geometry_ref")
    if not isinstance(geometry_ref, Mapping):
        return None
    try:
        geometry = shape(geometry_ref["geometry"])
        point = gpd.GeoSeries([geometry], crs=geometry_ref["crs"]).to_crs(graph.crs).iloc[0]
        if not isinstance(point, Point) or point.is_empty:
            return None
        node_id, distance_m = graph.nearest_node(point)
    except (KeyError, TypeError, ValueError):
        return None
    return node_id, distance_m, point


def _connection_candidate(
    connection: Mapping[str, object],
    option: RouteOption,
    *,
    origin: Mapping[str, object],
    destination: Mapping[str, object],
    origin_node: str,
    destination_node: str,
    origin_distance_m: float,
    destination_distance_m: float,
    graph: RoadGraph,
) -> dict[str, object]:
    connection_id = str(connection["connection_id"])
    geometry_ref = _geometry_ref(option.geometry, graph.crs, connection_id)
    ordered_source_edges = [str(item) for item in option.edge_ids]
    ordered_directed_edges = [str(item) for item in option.directed_edge_ids]
    candidate_id = _stable_id(
        "planning-candidate",
        {
            "connection_id": connection_id,
            "role": option.role,
            "directed_edge_ids": ordered_directed_edges,
        },
    )
    return {
        "candidate_id": candidate_id,
        "obligation_id": connection_id,
        "connection_id": connection_id,
        "source_corridor_refs": [str(item) for item in connection.get("corridor_refs", [])],
        "place_refs": [str(origin["place_id"]), str(destination["place_id"])],
        "role": option.role,
        "status": "admitted",
        "current_or_future": connection["current_or_future"],
        "endpoint_provenance": {
            "origin_place_id": origin["place_id"],
            "destination_place_id": destination["place_id"],
            "origin_node_id": origin_node,
            "destination_node_id": destination_node,
            "origin_attachment_distance_m": origin_distance_m,
            "destination_attachment_distance_m": destination_distance_m,
            "origin_geometry_ref": _json_copy(origin["geometry_ref"]),
            "destination_geometry_ref": _json_copy(destination["geometry_ref"]),
            "source_edge_ids": ordered_source_edges,
            "directed_edge_ids": ordered_directed_edges,
        },
        "graph_path": {
            "source_edge_ids": ordered_source_edges,
            "directed_edge_ids": ordered_directed_edges,
            "length_km": option.length_km,
            "a_road_share": option.a_road_share,
            "ncn_share": option.ncn_share,
        },
        "geometry_ref": geometry_ref,
        "provenance": {
            "source_refs": _json_copy(origin.get("source_refs", []))
            + _json_copy(destination.get("source_refs", [])),
            "evidence_refs": [],
            "origin_place_ref": origin["place_id"],
            "destination_place_ref": destination["place_id"],
        },
    }


def _reidentify_problem(problem: Mapping[str, object]) -> dict[str, object]:
    child = copy.deepcopy(dict(problem))
    child.pop("input_fingerprint", None)
    child.pop("problem_id", None)
    child["input_fingerprint"] = _fingerprint(child)
    child["problem_id"] = _stable_id("planning-problem", child["input_fingerprint"])
    return child


def _rebind_state(state: Mapping[str, object], problem: Mapping[str, object]) -> dict[str, object]:
    child = copy.deepcopy(dict(state))
    child["parent_problem_id"] = problem["problem_id"]
    child["problem_fingerprint"] = problem["input_fingerprint"]
    child["brief"] = _json_copy(problem.get("brief", {}))
    child["brief_fingerprint"] = problem.get("brief_fingerprint")
    child["obligations"] = _json_copy(problem.get("obligations", []))
    child["candidates"] = _json_copy(problem.get("candidates", []))
    dispositions = child.get("obligation_dispositions", {})
    if not isinstance(dispositions, dict):
        dispositions = {}
    for obligation in problem.get("obligations", []):
        obligation_id = obligation.get("obligation_id") if isinstance(obligation, Mapping) else None
        if obligation_id and obligation_id not in dispositions:
            dispositions[str(obligation_id)] = "unresolved"
    child["obligation_dispositions"] = dispositions
    planning_gaps = child.get("planning_gaps", [])
    if not isinstance(planning_gaps, list):
        planning_gaps = []
    for gap in problem.get("planning_gaps", []):
        if isinstance(gap, Mapping):
            _append_unique(planning_gaps, gap, "gap_id")
    child["planning_gaps"] = planning_gaps
    child["state_fingerprint"] = semantic_fingerprint(child)
    child["state_id"] = _stable_id("proposal-state", child["state_fingerprint"])
    return child


def admit_expansion(
    problem: Mapping[str, object],
    state: Mapping[str, object],
    request: Mapping[str, object],
) -> dict[str, object]:
    """Admit a materialised expansion receipt without reading or routing inputs."""

    if request.get("base_problem_fingerprint") != problem.get("input_fingerprint"):
        return _operation_error("stale-expansion", "expansion receipt belongs to another problem")
    if request.get("base_state_fingerprint") != state.get("state_fingerprint"):
        return _operation_error("stale-expansion", "expansion receipt belongs to another state")
    if request.get("brief_fingerprint") != problem.get("brief_fingerprint"):
        return _operation_error("brief-binding", "expansion receipt has a stale planning brief")
    receipt_binding = request.get("snapshot_binding")
    problem_binding = problem.get("binding")
    if not isinstance(receipt_binding, Mapping) or not isinstance(problem_binding, Mapping):
        return _operation_error("snapshot-binding", "expansion snapshot binding is missing")
    for binding_key in (
        "area_id",
        "area_name",
        "deployment_id",
        "snapshot_id",
        "snapshot_manifest_sha256",
        "code_contract",
    ):
        if receipt_binding.get(binding_key) != problem_binding.get(binding_key):
            return _operation_error(
                "snapshot-binding", f"expansion snapshot binding is stale: {binding_key}"
            )
    operation = request.get("operation")
    if not isinstance(operation, Mapping):
        return _operation_error(
            "expansion-operation", "expansion receipt has no connection operation"
        )
    admitted = apply_operation(problem, state, operation)
    if admitted.get("status") == "invalid":
        return admitted
    connection = admitted["connection_intents"][-1]
    candidates = request.get("candidates", [])
    obligation = request.get("obligation")
    if not isinstance(candidates, list) or not isinstance(obligation, Mapping):
        return _operation_error("expansion-shape", "expansion receipt additions are malformed")
    indexes = _ref_index(problem)
    problem_candidates = problem.get("candidates", [])
    problem_candidates = problem_candidates if isinstance(problem_candidates, list) else []
    candidate_by_id = {
        str(item["candidate_id"]): item
        for item in problem_candidates
        if isinstance(item, Mapping) and item.get("candidate_id")
    }
    additions: list[Mapping[str, object]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            return _operation_error("expansion-candidate", "expansion candidate is not an object")
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            return _operation_error("expansion-candidate", "expansion candidate has no identity")
        existing = candidate_by_id.get(candidate_id)
        if existing is not None:
            if _canonical(existing) != _canonical(candidate):
                return _operation_error(
                    "expansion-candidate", "candidate identity is rebound to different facts"
                )
            continue
        if candidate.get("connection_id") != connection.get("connection_id"):
            return _operation_error(
                "expansion-candidate", "candidate belongs to another connection"
            )
        if set(_as_ref_list(candidate.get("place_refs", []))) != {
            str(connection["origin_place_id"]),
            str(connection["destination_place_id"]),
        }:
            return _operation_error(
                "expansion-candidate", "candidate endpoints are not the connection places"
            )
        if any(
            ref not in indexes["corridor"]
            for ref in _as_ref_list(candidate.get("source_corridor_refs", []))
        ):
            return _operation_error(
                "expansion-candidate", "candidate has a foreign source corridor"
            )
        if not _valid_geometry_reference(candidate.get("geometry_ref", {})):
            return _operation_error("expansion-candidate", "candidate geometry is invalid")
        if candidate.get("obligation_id") != connection.get("connection_id"):
            return _operation_error("expansion-candidate", "candidate obligation is stale")
        if candidate.get("current_or_future") != connection.get("current_or_future"):
            return _operation_error("expansion-candidate", "candidate provision status is stale")
        graph_path = candidate.get("graph_path")
        endpoint = candidate.get("endpoint_provenance")
        if not isinstance(graph_path, Mapping) or not isinstance(endpoint, Mapping):
            return _operation_error("expansion-candidate", "candidate graph provenance is missing")
        directed = _as_ref_list(graph_path.get("directed_edge_ids", []))
        endpoint_directed = _as_ref_list(endpoint.get("directed_edge_ids", []))
        if not directed or directed != endpoint_directed:
            return _operation_error(
                "expansion-candidate", "candidate path provenance is not ordered"
            )
        for field in ("origin_node_id", "destination_node_id"):
            if not isinstance(endpoint.get(field), str) or not endpoint[field].strip():
                return _operation_error("expansion-candidate", "candidate endpoint node is missing")
        for field in ("origin_attachment_distance_m", "destination_attachment_distance_m"):
            if not isinstance(endpoint.get(field), (int, float)) or endpoint[field] < 0:
                return _operation_error(
                    "expansion-candidate", "candidate endpoint attachment is invalid"
                )
        candidate_error = _expansion_candidate_error(problem, candidate, connection)
        if candidate_error:
            return _operation_error("expansion-candidate", candidate_error)
        additions.append(candidate)
    expected_obligation_id = str(connection["connection_id"])
    if obligation.get("obligation_id") != expected_obligation_id:
        return _operation_error(
            "expansion-obligation", "expansion obligation is not the connection"
        )
    place_refs = set(_as_ref_list(obligation.get("place_refs", [])))
    if place_refs != {
        str(connection["origin_place_id"]),
        str(connection["destination_place_id"]),
    }:
        return _operation_error("expansion-obligation", "expansion obligation endpoints are stale")
    existing_obligation = next(
        (
            item
            for item in problem.get("obligations", [])
            if isinstance(item, Mapping) and item.get("obligation_id") == expected_obligation_id
        ),
        None,
    )
    if existing_obligation is not None and _canonical(existing_obligation) != _canonical(
        obligation
    ):
        return _operation_error(
            "expansion-obligation", "obligation identity is rebound to different facts"
        )
    child_problem = copy.deepcopy(dict(problem))
    if existing_obligation is None:
        child_problem.setdefault("obligations", []).append(_json_copy(obligation))
    child_problem.setdefault("candidates", []).extend(_json_copy(additions))
    gap = request.get("gap")
    if gap is not None:
        if not isinstance(gap, Mapping) or not gap.get("gap_id"):
            return _operation_error("expansion-gap", "expansion gap is malformed")
        child_problem.setdefault("planning_gaps", []).append(_json_copy(gap))
    receipt = _json_copy(request)
    child_problem.setdefault("expansion_receipts", []).append(receipt)
    child_problem["candidates"] = sorted(
        {str(item["candidate_id"]): item for item in child_problem["candidates"]}.values(),
        key=lambda item: str(item["candidate_id"]),
    )
    child_problem["obligations"] = sorted(
        {str(item["obligation_id"]): item for item in child_problem["obligations"]}.values(),
        key=lambda item: str(item["obligation_id"]),
    )
    child_problem["planning_gaps"] = sorted(
        {str(item["gap_id"]): item for item in child_problem.get("planning_gaps", [])}.values(),
        key=lambda item: str(item["gap_id"]),
    )
    child_problem = _reidentify_problem(child_problem)
    child_state = _rebind_state(admitted, child_problem)
    return {
        "status": "expanded",
        "problem": child_problem,
        "state": child_state,
        "receipt": receipt,
    }


def expand_connection(
    problem: Mapping[str, object],
    state: Mapping[str, object],
    operation: Mapping[str, object],
    config: AreaConfig,
) -> dict[str, object]:
    """Admit graph-backed alternatives for one named-place connection intent.

    Expansion is on demand.  It uses the same pinned snapshot and routing
    helpers as source admission, and returns a re-bound problem/state pair.
    """

    if operation.get("kind") not in {"propose-connection", "revise-connection"}:
        return _operation_error(
            "operation-kind", "connection expansion requires a connection operation"
        )
    admitted = apply_operation(problem, state, operation)
    if admitted.get("status") == "invalid":
        return admitted
    connection = admitted["connection_intents"][-1]
    source = load_snapshot(config)
    network = _admitted_network(source)
    if network is None or network.empty:
        return _operation_error("network-empty", "connection expansion requires a routing graph")
    graph = RoadGraph(network)
    places_by_id = {str(item["place_id"]): item for item in problem.get("places", [])}
    origin = places_by_id.get(str(connection["origin_place_id"]))
    destination = places_by_id.get(str(connection["destination_place_id"]))
    if origin is None or destination is None:
        return _operation_error("unknown-place", "connection places are absent from the problem")
    origin_attachment = _place_endpoint(origin, graph)
    destination_attachment = _place_endpoint(destination, graph)
    if origin_attachment is None or destination_attachment is None:
        return _operation_error("place-attachment", "connection place has no graph endpoint")
    origin_node, origin_distance_m, _origin_point = origin_attachment
    destination_node, destination_distance_m, _destination_point = destination_attachment
    try:
        _selected, options, reason = choose_alignment(
            graph,
            origin_node,
            destination_node,
            strategic_use=True,
        )
    except (KeyError, RuntimeError, ValueError) as error:
        return _operation_error("candidate-search", str(error))
    candidates = [
        _connection_candidate(
            connection,
            option,
            origin=origin,
            destination=destination,
            origin_node=origin_node,
            destination_node=destination_node,
            origin_distance_m=origin_distance_m,
            destination_distance_m=destination_distance_m,
            graph=graph,
        )
        for option in options
        if option.directed_edge_ids
    ]
    receipt: dict[str, object] = {
        "base_problem_fingerprint": problem["input_fingerprint"],
        "base_state_fingerprint": state["state_fingerprint"],
        "brief_fingerprint": problem["brief_fingerprint"],
        "operation": _json_copy(operation),
        "snapshot_binding": _snapshot_binding(config),
        "candidates": candidates,
        "obligation": {
            "obligation_id": connection["connection_id"],
            "obligation_kind": "named-place-connection",
            "place_refs": [connection["origin_place_id"], connection["destination_place_id"]],
            "endpoint_refs": [origin_node, destination_node],
            "mandatory": bool(operation.get("mandatory", False)),
            "status": "candidate-search-complete" if options else "candidate-gap",
            "source_refs": _json_copy(origin.get("source_refs", []))
            + _json_copy(destination.get("source_refs", [])),
            "evidence_refs": [],
        },
        "gap": (
            {
                "gap_id": _stable_id("planning-gap", connection["connection_id"]),
                "subject_id": connection["connection_id"],
                "obligation_id": connection["connection_id"],
                "reason": reason,
                "status": "unresolved",
            }
            if not candidates
            else None
        ),
    }
    return admit_expansion(problem, state, receipt)


def replay_expansion(
    problem: Mapping[str, object],
    state: Mapping[str, object],
    receipt: Mapping[str, object],
) -> dict[str, object]:
    """Replay a materialised expansion without loading or routing any input."""

    return admit_expansion(problem, state, receipt)


def _snapshot_binding(config: AreaConfig) -> dict[str, object]:
    path = config.source.snapshot_dir / config.source.snapshot_id
    manifest_path = path / "snapshot.json"
    manifest_hash = (
        hashlib.sha256(manifest_path.read_bytes()).hexdigest() if manifest_path.is_file() else None
    )
    return {
        "area_id": config.area_id,
        "area_name": config.area_name,
        "deployment_id": config.deployment_id,
        "snapshot_id": config.source.snapshot_id,
        "snapshot_manifest_sha256": manifest_hash,
        "code_contract": _PLANNING_CODE_CONTRACT,
    }


def _resolve_brief(brief: Mapping[str, object] | None) -> tuple[dict[str, object], str]:
    resolved = _json_copy(brief if brief is not None else _DEFAULT_BRIEF)
    if not isinstance(resolved, dict):  # pragma: no cover - _json_copy preserves mappings.
        raise ValueError("planning brief must be a JSON object")
    if not _present_identifier(resolved.get("brief_ref")):
        raise ValueError("planning brief requires a brief_ref")
    return resolved, _fingerprint(resolved)


def build_planning_problem(
    config: AreaConfig, brief: Mapping[str, object] | None = None
) -> dict[str, object]:
    """Admit one validated snapshot as the planner's JSON input boundary."""

    source = load_snapshot(config)
    network = _admitted_network(source)
    graph = (
        RoadGraph(network) if isinstance(network, gpd.GeoDataFrame) and not network.empty else None
    )
    corridors, gaps, unknowns = _source_corridors(source, graph)
    urban_preparation = _urban_preparation(source, graph)
    places = _place_records(
        source,
        urban_preparation,
        urban_crs=graph.crs if graph is not None else None,
    )
    prepared_connections, prepared_connection_issues = _prepared_connections(
        urban_preparation,
        places,
    )
    obligations = _obligation_records(places, source)
    candidates = _graph_candidates(corridors, graph)
    resolved_brief, brief_fingerprint = _resolve_brief(brief)
    binding = _snapshot_binding(config)
    binding["brief_ref"] = resolved_brief["brief_ref"]
    binding["brief_fingerprint"] = brief_fingerprint
    problem: dict[str, object] = {
        "schema_version": "planning-problem/v1",
        "status": "admitted-with-unknowns" if gaps or unknowns else "admitted",
        "binding": binding,
        "brief": resolved_brief,
        "brief_fingerprint": brief_fingerprint,
        "places": places,
        "obligations": obligations,
        "prepared_connections": prepared_connections,
        "prepared_connection_issues": prepared_connection_issues,
        "source_corridors": sorted(corridors, key=lambda item: str(item["corridor_id"])),
        "candidates": candidates,
        "graph_evidence": _graph_evidence(graph),
        "planning_gaps": gaps,
        "unknown_facts": unknowns,
        "exclusions": [],
        "source_hashes": sorted(
            str(item["provenance"]["source_hash"])
            for item in corridors
            if item.get("provenance", {}).get("source_hash")
        ),
    }
    problem["input_fingerprint"] = _fingerprint(problem)
    problem["problem_id"] = _stable_id("planning-problem", problem["input_fingerprint"])
    return problem


def _ref_index(problem: Mapping[str, object]) -> dict[str, set[str]]:
    corridor_items = problem.get("source_corridors", [])
    place_items = problem.get("places", [])
    candidate_items = problem.get("candidates", [])
    obligation_items = problem.get("obligations", [])
    gap_items = problem.get("planning_gaps", [])
    corridor_items = corridor_items if isinstance(corridor_items, list) else []
    place_items = place_items if isinstance(place_items, list) else []
    candidate_items = candidate_items if isinstance(candidate_items, list) else []
    obligation_items = obligation_items if isinstance(obligation_items, list) else []
    gap_items = gap_items if isinstance(gap_items, list) else []
    return {
        "place": {str(item["place_id"]) for item in place_items if isinstance(item, Mapping)},
        "corridor": {
            str(item["corridor_id"]) for item in corridor_items if isinstance(item, Mapping)
        },
        "candidate": {
            str(item["candidate_id"]) for item in candidate_items if isinstance(item, Mapping)
        },
        "obligation": {
            str(item["obligation_id"]) for item in obligation_items if isinstance(item, Mapping)
        },
        "evidence": {
            str(evidence)
            for item in corridor_items
            if isinstance(item, Mapping)
            for evidence in _as_ref_list(item.get("evidence_refs", []))
        }
        | {
            str(evidence)
            for item in obligation_items
            if isinstance(item, Mapping)
            for evidence in _as_ref_list(item.get("evidence_refs", []))
        },
        "gap": {str(item["gap_id"]) for item in gap_items if isinstance(item, Mapping)},
    }


def _validate_problem_shape(problem: Mapping[str, object]) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    if problem.get("schema_version") != "planning-problem/v1":
        diagnostics.append({"code": "problem-schema", "message": "unsupported problem schema"})
    if not isinstance(problem.get("input_fingerprint"), str):
        diagnostics.append(
            {"code": "problem-fingerprint", "message": "problem fingerprint missing"}
        )
    if not isinstance(problem.get("brief"), Mapping) or not isinstance(
        problem.get("brief_fingerprint"), str
    ):
        diagnostics.append(
            {"code": "brief-binding", "message": "resolved planning brief is missing"}
        )
    for collection in ("places", "obligations", "source_corridors", "candidates"):
        if not isinstance(problem.get(collection), list):
            diagnostics.append(
                {"code": "problem-collection", "message": f"{collection} must be a list"}
            )
    return diagnostics


def initial_proposal(problem: Mapping[str, object]) -> dict[str, object]:
    """Create the initial proposal state without selecting or dropping facts."""

    diagnostics = _validate_problem_shape(problem)
    if diagnostics:
        raise ValueError(json.dumps(diagnostics, sort_keys=True))
    obligation_dispositions = {
        str(item["obligation_id"]): "unresolved"
        for item in problem.get("obligations", [])
        if isinstance(item, Mapping) and item.get("obligation_id")
    }
    state: dict[str, object] = {
        "schema_version": "proposal-state/v1",
        "parent_problem_id": problem.get("problem_id"),
        "problem_fingerprint": problem.get("input_fingerprint"),
        "brief": _json_copy(problem.get("brief", {})),
        "brief_fingerprint": problem.get("brief_fingerprint"),
        "status": "unresolved"
        if problem.get("planning_gaps") or obligation_dispositions
        else "provisional",
        "source_corridors": _json_copy(problem.get("source_corridors", [])),
        "places": _json_copy(problem.get("places", [])),
        "obligations": _json_copy(problem.get("obligations", [])),
        "candidates": _json_copy(problem.get("candidates", [])),
        "obligation_dispositions": obligation_dispositions,
        "connection_intents": [],
        "selected_alignments": [],
        "departures": [],
        "planning_gaps": _json_copy(problem.get("planning_gaps", [])),
        "unknown_facts": _json_copy(problem.get("unknown_facts", [])),
        "future_interventions": [],
        "operations": [],
    }
    state["state_fingerprint"] = semantic_fingerprint(state)
    state["state_id"] = _stable_id("proposal-state", state["state_fingerprint"])
    return state


def _operation_error(code: str, message: str) -> dict[str, object]:
    return {"status": "invalid", "diagnostics": [{"code": code, "message": message}]}


def _problem_binding_error(problem: Mapping[str, object]) -> str | None:
    brief = problem.get("brief")
    if not isinstance(brief, Mapping) or _fingerprint(brief) != problem.get("brief_fingerprint"):
        return "planning brief content does not match its fingerprint"
    input_fingerprint = problem.get("input_fingerprint")
    problem_id = problem.get("problem_id")
    if not isinstance(input_fingerprint, str) or not isinstance(problem_id, str):
        return "planning problem identity is missing"
    content = copy.deepcopy(dict(problem))
    content.pop("input_fingerprint", None)
    content.pop("problem_id", None)
    if _fingerprint(content) != input_fingerprint:
        return "planning problem content does not match its fingerprint"
    if problem_id != _stable_id("planning-problem", input_fingerprint):
        return "planning problem identity does not match its fingerprint"
    return None


def _state_binding_error(
    problem: Mapping[str, object],
    state: Mapping[str, object],
    *,
    context: _PlanningContext | None = None,
) -> str | None:
    if state.get("brief_fingerprint") != problem.get("brief_fingerprint") or _canonical(
        state.get("brief")
    ) != _canonical(problem.get("brief")):
        return "proposal brief content does not match the planning problem"
    state_fingerprint = state.get("state_fingerprint")
    if not isinstance(state_fingerprint, str):
        return "proposal state fingerprint is missing"
    try:
        actual_fingerprint = (
            _semantic_fingerprint_with_context(state, context)
            if context is not None
            else semantic_fingerprint(state)
        )
    except (TypeError, ValueError):
        return "proposal state content cannot be fingerprinted"
    if actual_fingerprint != state_fingerprint:
        return "proposal state content does not match its fingerprint"
    return None


def _context_error(
    problem: Mapping[str, object],
    state: Mapping[str, object],
    context: object,
) -> str | None:
    if not isinstance(context, _PlanningContext):
        return "planning execution context is invalid"
    if (
        problem.get("problem_id") != context.problem_id
        or problem.get("input_fingerprint") != context.input_fingerprint
        or problem.get("brief_fingerprint") != context.brief_fingerprint
    ):
        return "planning execution context is stale"
    for field in _CONTEXT_SHARED_FIELDS:
        if problem.get(field) is not context.problem_fields.get(field):
            return f"planning problem {field} changed after context verification"
        if state.get(field) is not context.state_fields.get(field):
            return f"proposal state {field} changed after context verification"
    if state.get("brief") is not context.state_fields.get("brief"):
        return "proposal state brief changed after context verification"
    return None


def _operation_refs(operation: Mapping[str, object]) -> Mapping[str, object]:
    refs = operation.get("refs")
    if isinstance(refs, Mapping):
        return refs
    target_refs = operation.get("target_refs")
    return target_refs if isinstance(target_refs, Mapping) else {}


def _as_ref_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item) for item in value]
    return []


def _provisional_choice_error(metadata: Mapping[str, object]) -> str | None:
    provisional = metadata.get("provisional")
    if "provisional" in metadata and not isinstance(provisional, bool):
        return "provisional selection flag must be boolean"
    reason = metadata.get("reason")
    if "reason" in metadata and (not isinstance(reason, str) or not reason.strip()):
        return "provisional selection reason must be non-blank text"
    uncertainties = metadata.get("uncertainties")
    if "uncertainties" in metadata and (
        not isinstance(uncertainties, list)
        or any(not isinstance(item, str) or not item.strip() for item in uncertainties)
    ):
        return "provisional selection uncertainties must be a list of non-blank text"
    if provisional is True:
        if not isinstance(reason, str) or not reason.strip():
            return "provisional selection requires a non-blank reason"
        if not isinstance(uncertainties, list) or not uncertainties:
            return "provisional selection requires at least one stated uncertainty"
    return None


def _geometry_identity_matches(value: Mapping[str, object]) -> bool:
    try:
        geometry = shape(value["geometry"])
        crs_name = _crs_identity(value["crs"])
        content_fingerprint = canonical_network_geometry_fingerprint(geometry, crs_name)
    except (KeyError, TypeError, ValueError):
        return False
    return value.get("content_fingerprint") == content_fingerprint and value.get(
        "geometry_id"
    ) == _stable_id("geometry", content_fingerprint)


def _valid_geometry_reference(
    value: Mapping[str, object], *, require_identity: bool = False
) -> bool:
    try:
        geometry = shape(value["geometry"])
        _crs_identity(value["crs"])
    except (KeyError, TypeError, ValueError):
        return False
    if require_identity and not _geometry_identity_matches(value):
        return False
    return (
        isinstance(geometry, _LINE_GEOMETRIES)
        and not geometry.is_empty
        and geometry.is_valid
        and _present_identifier(value.get("content_fingerprint"))
    )


def _expansion_candidate_error(
    problem: Mapping[str, object],
    candidate: Mapping[str, object],
    connection: Mapping[str, object],
) -> str | None:
    graph_evidence = problem.get("graph_evidence")
    if not isinstance(graph_evidence, Mapping):
        return "expansion graph evidence is missing"
    directed_edges = graph_evidence.get("directed_edges")
    if not isinstance(directed_edges, list):
        return "expansion graph evidence is malformed"
    by_id = {
        str(edge["directed_edge_id"]): edge
        for edge in directed_edges
        if isinstance(edge, Mapping) and edge.get("directed_edge_id")
    }
    graph_path = candidate.get("graph_path")
    endpoint = candidate.get("endpoint_provenance")
    if not isinstance(graph_path, Mapping) or not isinstance(endpoint, Mapping):
        return "candidate graph provenance is missing"
    directed = _as_ref_list(graph_path.get("directed_edge_ids", []))
    source = _as_ref_list(graph_path.get("source_edge_ids", []))
    endpoint_directed = _as_ref_list(endpoint.get("directed_edge_ids", []))
    endpoint_source = _as_ref_list(endpoint.get("source_edge_ids", []))
    if not directed or directed != endpoint_directed or not source or source != endpoint_source:
        return "candidate path provenance is not ordered"
    edges: list[Mapping[str, object]] = []
    for edge_id in directed:
        edge = by_id.get(edge_id)
        if edge is None:
            return f"candidate directed edge is not admitted: {edge_id}"
        edges.append(edge)
    expected_source = [str(edge.get("source_edge_id")) for edge in edges]
    if source != expected_source:
        return "candidate source-edge provenance does not match directed edges"
    if endpoint.get("origin_place_id") != connection.get("origin_place_id") or endpoint.get(
        "destination_place_id"
    ) != connection.get("destination_place_id"):
        return "candidate endpoint places do not match the connection"
    origin_node = endpoint.get("origin_node_id")
    destination_node = endpoint.get("destination_node_id")
    if not isinstance(origin_node, str) or not isinstance(destination_node, str):
        return "candidate endpoint node is missing"
    if edges[0].get("from_node_id") != origin_node:
        return "candidate origin node does not match the graph path"
    if edges[-1].get("to_node_id") != destination_node:
        return "candidate destination node does not match the graph path"
    if any(left.get("to_node_id") != right.get("from_node_id") for left, right in pairwise(edges)):
        return "candidate directed edges are not contiguous"
    geometry_ref = candidate.get("geometry_ref")
    if not isinstance(geometry_ref, Mapping) or not _valid_geometry_reference(
        geometry_ref, require_identity=True
    ):
        return "candidate geometry identity does not match its coordinates"
    graph_crs = graph_evidence.get("crs")
    if geometry_ref.get("crs") != graph_crs:
        return "candidate geometry CRS does not match graph evidence"
    try:
        lines = [shape(edge["geometry_ref"]["geometry"]) for edge in edges]
        unioned = unary_union(lines)
        route_geometry = unioned if isinstance(unioned, LineString) else linemerge(unioned)
    except (KeyError, TypeError, ValueError):
        return "candidate graph edge geometry is malformed"
    if not isinstance(route_geometry, LineString):
        return "candidate graph path geometry cannot be merged"
    route_fingerprint = canonical_network_geometry_fingerprint(route_geometry, graph_crs)
    if geometry_ref.get("content_fingerprint") != route_fingerprint:
        return "candidate geometry does not match its ordered graph path"
    return None


def _validate_departure_geometry(
    problem: Mapping[str, object],
    affected_corridor_ids: Sequence[str],
    extent: object,
    geometry_refs: object,
) -> str | None:
    if not isinstance(geometry_refs, Sequence) or isinstance(
        geometry_refs, (str, bytes, bytearray)
    ):
        return "departure geometry references must be a list"
    corridor_by_id = {
        str(item["corridor_id"]): item
        for item in problem.get("source_corridors", [])
        if isinstance(item, Mapping)
    }
    affected = [corridor_by_id.get(str(item)) for item in affected_corridor_ids]
    if any(item is None for item in affected):
        return "departure references an unknown source corridor"
    if not geometry_refs:
        return "departure needs at least one affected source geometry"
    for geometry_ref in geometry_refs:
        if not isinstance(geometry_ref, Mapping) or not _valid_geometry_reference(
            geometry_ref, require_identity=True
        ):
            return "departure geometry identity does not match its coordinates"
        source_ref = geometry_ref.get("source_ref")
        source_corridor = next(
            (
                corridor
                for corridor in affected
                if source_ref
                in {
                    str(item["source_id"])
                    for item in corridor.get("source_refs", [])
                    if isinstance(item, Mapping) and item.get("source_id")
                }
            ),
            None,
        )
        if source_corridor is None:
            return "departure geometry is foreign to its affected source corridor"
        source_geometry_ref = source_corridor["geometry_ref"]
        try:
            source_geometry = shape(source_geometry_ref["geometry"])
            departure_geometry = shape(geometry_ref["geometry"])
            transformed = (
                gpd.GeoSeries([departure_geometry], crs=geometry_ref["crs"])
                .to_crs(source_geometry_ref["crs"])
                .iloc[0]
            )
        except (KeyError, TypeError, ValueError):
            return "departure geometry could not be transformed to its source CRS"
        if not source_geometry.covers(transformed):
            return "departure geometry is outside its source corridor"
        if extent == "partial" and source_geometry.equals(transformed):
            return "partial departure cannot cover the complete source corridor"
        if extent == "full" and not source_geometry.equals(transformed):
            return "full departure geometry must retain the complete source corridor"
    if extent == "full" and len(geometry_refs) != len(affected_corridor_ids):
        return "full departure needs one complete geometry per affected corridor"
    return None


def _has_selected_alignment(
    state: Mapping[str, object],
    candidate: Mapping[str, object],
    affected_corridor_ids: Sequence[str],
) -> bool:
    selections = state.get("selected_alignments")
    if not isinstance(selections, list):
        return False
    candidate_id = candidate.get("candidate_id")
    candidate_refs = _as_ref_list(candidate.get("source_corridor_refs", []))
    for selection in selections:
        if not isinstance(selection, Mapping) or selection.get("candidate_id") != candidate_id:
            continue
        if not set(affected_corridor_ids).intersection(
            _as_ref_list(selection.get("source_corridor_refs", []))
        ):
            continue
        if any(
            _canonical(selection.get(field)) != _canonical(candidate.get(field))
            for field in (
                "source_corridor_refs",
                "geometry_ref",
                "graph_path",
                "endpoint_provenance",
                "current_or_future",
            )
        ):
            continue
        if not set(affected_corridor_ids).intersection(candidate_refs):
            continue
        return True
    return False


def _append_unique(items: list[dict[str, object]], item: Mapping[str, object], key: str) -> None:
    identifier = item.get(key)
    if any(existing.get(key) == identifier for existing in items):
        for position, existing in enumerate(items):
            if existing.get(key) == identifier:
                items[position] = dict(item)
                return
    items.append(dict(item))


def apply_operation(
    problem: Mapping[str, object],
    state: Mapping[str, object],
    operation: Mapping[str, object],
) -> dict[str, object]:
    """Apply one operation through the strict public engine boundary."""

    return _apply_operation_with_context(problem, state, operation, context=None)


def _apply_operation_with_context(
    problem: Mapping[str, object],
    state: Mapping[str, object],
    operation: Mapping[str, object],
    *,
    context: _PlanningContext | None = None,
) -> dict[str, object]:
    """Apply one explicit planner operation, returning a child or invalid result."""

    context_error = _context_error(problem, state, context) if context is not None else None
    try:
        problem_error = (
            None
            if context is not None and context_error is None
            else _problem_binding_error(problem)
        )
    except (TypeError, ValueError):
        problem_error = "planning problem identity cannot be validated"
    if context_error:
        return _operation_error("context-binding", context_error)
    if problem_error:
        return _operation_error("problem-binding", problem_error)
    if state.get("schema_version") != "proposal-state/v1":
        return _operation_error("state-schema", "operation state schema is unsupported")
    if state.get("parent_problem_id") != problem.get("problem_id"):
        return _operation_error("stale-problem", "proposal state belongs to another problem")
    try:
        state_error = _state_binding_error(problem, state, context=context)
    except (TypeError, ValueError):
        state_error = "proposal state binding cannot be validated"
    if state_error:
        code = "brief-binding" if "brief" in state_error else "state-fingerprint"
        return _operation_error(code, state_error)
    parent_fingerprint = operation.get("parent_state_fingerprint")
    if parent_fingerprint != state.get("state_fingerprint"):
        return _operation_error("stale-state", "operation parent state is stale")
    refs = _operation_refs(operation)
    kind = str(operation.get("kind", ""))
    payload = operation.get("payload")
    payload = payload if isinstance(payload, Mapping) else operation
    indexes = context.indexes if context is not None else _ref_index(problem)
    state_gaps = state.get("planning_gaps", [])
    if not isinstance(state_gaps, list):
        return _operation_error("state-shape", "state field planning_gaps is not a list")
    state_gap_ids = {
        str(item["gap_id"])
        for item in state_gaps
        if isinstance(item, Mapping) and item.get("gap_id")
    }
    record: Mapping[str, object] | None = None

    def require_refs(kind_name: str, values: object) -> str | None:
        for ref in _as_ref_list(values):
            if ref not in indexes[kind_name]:
                return f"{kind_name} reference is not admitted: {ref}"
        return None

    if kind in {"propose-connection", "revise-connection"}:
        origin = payload.get("origin_place_id")
        destination = payload.get("destination_place_id")
        for label, value in (("origin", origin), ("destination", destination)):
            if not isinstance(value, str) or value not in indexes["place"]:
                return _operation_error("unknown-place", f"{label} place is not admitted")
        corridor_refs = payload.get("corridor_refs", refs.get("corridor_refs", []))
        error = require_refs("corridor", corridor_refs)
        if error:
            return _operation_error("unknown-corridor", error)
        current_or_future = payload.get("current_or_future")
        if not isinstance(current_or_future, str) or current_or_future not in {
            "current",
            "future",
            "unknown",
        }:
            return _operation_error(
                "provision-status",
                "connection provision status must be explicitly current, future, or unknown",
            )
        connection_id = str(
            payload.get("connection_id")
            or _stable_id("connection", (origin, destination, _as_ref_list(corridor_refs)))
        )
        connection = {
            "connection_id": connection_id,
            "origin_place_id": origin,
            "destination_place_id": destination,
            "corridor_refs": _as_ref_list(corridor_refs),
            "status": str(payload.get("status", "proposed")),
            "reason": payload.get("reason"),
            "current_or_future": current_or_future,
        }
        record = connection
        field = "connection_intents"
        item_key = "connection_id"
    elif kind == "select-alignment":
        candidate_id = payload.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id not in indexes["candidate"]:
            return _operation_error("unknown-candidate", "selected candidate is not admitted")
        obligation_id = payload.get("obligation_id")
        if (
            obligation_id is not None
            and obligation_id not in indexes["obligation"] | indexes["corridor"]
        ):
            return _operation_error("unknown-obligation", "selected obligation is not admitted")
        candidate = next(
            item for item in problem["candidates"] if item["candidate_id"] == candidate_id
        )
        if obligation_id is not None and obligation_id not in {
            candidate.get("obligation_id"),
            *candidate.get("source_corridor_refs", []),
        }:
            return _operation_error(
                "candidate-binding", "selected candidate is not bound to the requested obligation"
            )
        provisional_metadata = {
            key: payload[key]
            for key in ("provisional", "reason", "uncertainties")
            if key in payload
        }
        provisional_error = _provisional_choice_error(provisional_metadata)
        if provisional_error:
            return _operation_error("provisional-choice", provisional_error)
        connection = {
            "selection_id": _stable_id("selection", (candidate_id, obligation_id)),
            "candidate_id": candidate_id,
            "obligation_id": obligation_id or candidate.get("obligation_id"),
            "source_corridor_refs": _json_copy(candidate.get("source_corridor_refs", [])),
            "geometry_ref": _json_copy(candidate.get("geometry_ref")),
            "graph_path": _json_copy(candidate.get("graph_path")),
            "endpoint_provenance": _json_copy(candidate.get("endpoint_provenance")),
            "current_or_future": candidate.get("current_or_future"),
            **_json_copy(provisional_metadata),
        }
        record = connection
        field = "selected_alignments"
        item_key = "selection_id"
    elif kind == "record-departure":
        affected = _as_ref_list(
            payload.get("source_corridor_refs", refs.get("source_corridor_refs", []))
        )
        error = require_refs("corridor", affected)
        if error or not affected:
            return _operation_error(
                "unknown-corridor", error or "departure needs affected corridors"
            )
        outcome = payload.get("outcome")
        if not isinstance(outcome, Mapping) or outcome.get("kind") not in _DEPARTURE_OUTCOMES:
            return _operation_error(
                "departure-outcome", "departure requires alternate, unresolved, or no-loss outcome"
            )
        outcome_kind = str(outcome["kind"])
        if outcome_kind == "alternate":
            candidate_id = outcome.get("candidate_id")
            if not isinstance(candidate_id, str) or candidate_id not in indexes["candidate"]:
                return _operation_error(
                    "unknown-candidate", "alternate departure requires an admitted candidate"
                )
            candidate = next(
                item for item in problem["candidates"] if item["candidate_id"] == candidate_id
            )
            if not set(affected).intersection(candidate.get("source_corridor_refs", [])):
                return _operation_error(
                    "candidate-binding", "alternate departure candidate is foreign to the corridor"
                )
            if not _has_selected_alignment(state, candidate, affected):
                return _operation_error(
                    "alternate-selection",
                    "alternate departure requires a selected admitted alignment",
                )
        if outcome_kind == "unresolved":
            gap_id = outcome.get("gap_id")
            if not isinstance(gap_id, str) or not gap_id.strip():
                return _operation_error(
                    "departure-gap", "unresolved departure requires a gap identity"
                )
            if gap_id not in indexes["gap"] | state_gap_ids:
                return _operation_error(
                    "departure-gap", "unresolved departure gap is not an admitted gap"
                )
        evidence_refs = _as_ref_list(payload.get("evidence_refs", []))
        error = require_refs("evidence", evidence_refs)
        if error:
            return _operation_error("unknown-evidence", error)
        if not evidence_refs:
            return _operation_error(
                "departure-evidence", "every departure outcome requires supporting evidence"
            )
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return _operation_error("departure-reason", "departure requires a reason")
        extent = payload.get("extent")
        if extent not in {"full", "partial"}:
            return _operation_error("departure-extent", "departure extent must be full or partial")
        explicit_geometry_refs = payload.get("affected_geometry_refs")
        if extent == "partial" and not isinstance(explicit_geometry_refs, Sequence):
            return _operation_error(
                "departure-geometry",
                "partial departure requires explicit affected geometry references",
            )
        if isinstance(explicit_geometry_refs, (str, bytes, bytearray)):
            return _operation_error(
                "departure-geometry",
                "partial departure geometry references must be a list",
            )
        if explicit_geometry_refs is not None and any(
            not isinstance(item, Mapping) or not _valid_geometry_reference(item)
            for item in explicit_geometry_refs
        ):
            return _operation_error(
                "departure-geometry",
                "departure geometry references must be valid JSON geometry refs",
            )
        departure_geometry_refs = (
            explicit_geometry_refs
            if explicit_geometry_refs is not None
            else [
                corridor["geometry_ref"]
                for corridor in problem["source_corridors"]
                if corridor["corridor_id"] in affected
            ]
        )
        geometry_error = _validate_departure_geometry(
            problem, affected, extent, departure_geometry_refs
        )
        if geometry_error:
            return _operation_error("departure-geometry", geometry_error)
        item = {
            "departure_id": _stable_id(
                "departure", (affected, extent, payload.get("reason"), outcome)
            ),
            "source_corridor_refs": affected,
            "extent": extent,
            "affected_geometry_refs": _json_copy(departure_geometry_refs),
            "reason": payload.get("reason"),
            "evidence_refs": evidence_refs,
            "outcome": _json_copy(outcome),
        }
        record = item
        field = "departures"
        item_key = "departure_id"
    elif kind in {"request-evidence", "request-candidates"}:
        target_refs = payload.get("target_refs", refs.get("target_refs", []))
        target_refs = _as_ref_list(target_refs)
        known = (
            indexes["corridor"]
            | indexes["place"]
            | indexes["obligation"]
            | indexes["candidate"]
            | indexes["evidence"]
        )
        if any(ref not in known for ref in target_refs):
            return _operation_error("unknown-target", "request target is not admitted")
        request_id = str(
            payload.get("request_id")
            or _stable_id("request", (kind, target_refs, payload.get("claim")))
        )
        existing_request = next(
            (
                item
                for item in state.get("unknown_facts", [])
                if isinstance(item, Mapping) and item.get("unknown_id") == request_id
            ),
            None,
        )
        request = {
            "unknown_id": request_id,
            "subject_refs": target_refs,
            "claim": payload.get("claim", kind),
            "reason": payload.get("reason"),
            "request_kind": kind,
            "status": "requested",
        }
        prior_judgments = (
            existing_request.get("evidence_judgments", [])
            if isinstance(existing_request, Mapping)
            else []
        )
        if isinstance(prior_judgments, list) and prior_judgments:
            request["evidence_judgments"] = _json_copy(prior_judgments)
        judgment = payload.get("evidence_judgment")
        if judgment is not None:
            if kind != "request-evidence" or not isinstance(judgment, Mapping):
                return _operation_error(
                    "evidence-judgment",
                    "evidence judgment must belong to a request-evidence operation",
                )
            relation = judgment.get("relation")
            if relation not in _EVIDENCE_RELATIONS:
                return _operation_error(
                    "evidence-judgment", "evidence judgment relation is not supported"
                )
            evidence_id = judgment.get("evidence_id")
            source = judgment.get("source")
            scope = judgment.get("scope")
            probabilities = judgment.get("probabilities")
            confidence = judgment.get("confidence")
            if not isinstance(evidence_id, str) or not evidence_id.strip():
                return _operation_error(
                    "evidence-judgment", "evidence judgment needs an evidence identity"
                )
            if not isinstance(source, Mapping) or not isinstance(source.get("excerpt"), str):
                return _operation_error(
                    "evidence-judgment", "evidence judgment needs retained source prose"
                )
            if not isinstance(scope, Mapping):
                return _operation_error("evidence-judgment", "evidence judgment scope is missing")
            candidate_id = scope.get("candidate_id")
            if not isinstance(candidate_id, str) or candidate_id not in indexes["candidate"]:
                return _operation_error(
                    "evidence-judgment", "evidence judgment candidate is not admitted"
                )
            candidate = next(
                item for item in problem["candidates"] if item.get("candidate_id") == candidate_id
            )
            corridor_refs = _as_ref_list(scope.get("source_corridor_refs", []))
            graph_path = candidate.get("graph_path")
            directed_edge_ids = _as_ref_list(scope.get("directed_edge_ids", []))
            path_edges = (
                _as_ref_list(graph_path.get("directed_edge_ids", []))
                if isinstance(graph_path, Mapping)
                else []
            )
            if not directed_edge_ids or any(edge not in path_edges for edge in directed_edge_ids):
                return _operation_error(
                    "evidence-judgment",
                    "evidence judgment directed edge is foreign to the candidate",
                )
            candidate_corridors = _as_ref_list(candidate.get("source_corridor_refs", []))
            corridors = {
                str(item.get("corridor_id")): item
                for item in problem.get("source_corridors", [])
                if isinstance(item, Mapping) and item.get("corridor_id")
            }
            for corridor_id in corridor_refs:
                corridor = corridors.get(corridor_id)
                topology = corridor.get("topology_fact") if isinstance(corridor, Mapping) else None
                topology_edges = (
                    _as_ref_list(topology.get("directed_edge_ids", []))
                    if isinstance(topology, Mapping)
                    else []
                )
                if corridor is None or (
                    corridor_id not in candidate_corridors
                    and not set(directed_edge_ids).issubset(topology_edges)
                ):
                    return _operation_error(
                        "evidence-judgment",
                        "evidence judgment corridor scope is foreign to the candidate",
                    )
            section_refs = _as_ref_list(scope.get("section_refs", []))
            admitted_sections = {
                str(corridors[corridor_id].get("section_id"))
                for corridor_id in corridor_refs
                if corridor_id in corridors and corridors[corridor_id].get("section_id")
            }
            if any(section_id not in admitted_sections for section_id in section_refs):
                return _operation_error(
                    "evidence-judgment",
                    "evidence judgment section scope is foreign to the candidate",
                )
            if not isinstance(probabilities, Mapping) or any(
                label not in probabilities for label in _EVIDENCE_RELATIONS
            ):
                return _operation_error(
                    "evidence-judgment",
                    "evidence judgment must retain the full relation distribution",
                )
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                return _operation_error(
                    "evidence-judgment", "evidence judgment confidence is missing"
                )
            claim = judgment.get("claim", request["claim"])
            if claim != request["claim"]:
                return _operation_error(
                    "evidence-judgment", "evidence judgment claim does not match the request"
                )
            normalized_judgment = _json_copy(dict(judgment))
            if not isinstance(normalized_judgment, dict):  # pragma: no cover - mapping copy
                return _operation_error("evidence-judgment", "evidence judgment is malformed")
            normalized_judgment["claim"] = request["claim"]
            normalized_judgment.setdefault(
                "judgment_id",
                _stable_id("evidence-judgment", (request_id, normalized_judgment)),
            )
            judgments = request.setdefault("evidence_judgments", [])
            if not isinstance(judgments, list):  # pragma: no cover - set above or prior list
                judgments = []
                request["evidence_judgments"] = judgments
            _append_unique(judgments, normalized_judgment, "judgment_id")
        record = request
        field = "unknown_facts"
        item_key = "unknown_id"
    elif kind == "record-gap":
        target_refs = _as_ref_list(payload.get("target_refs", refs.get("target_refs", [])))
        known = (
            indexes["corridor"] | indexes["place"] | indexes["obligation"] | indexes["candidate"]
        )
        if any(ref not in known for ref in target_refs):
            return _operation_error("unknown-target", "gap target is not admitted")
        gap = {
            "gap_id": str(
                payload.get("gap_id")
                or _stable_id("planning-gap", (target_refs, payload.get("reason")))
            ),
            "subject_refs": target_refs,
            "reason": payload.get("reason"),
            "status": "unresolved",
        }
        field = "planning_gaps"
        item_key = "gap_id"
        item = gap
        record = item
    elif kind == "propose-intervention":
        target_refs = _as_ref_list(payload.get("target_refs", refs.get("target_refs", [])))
        error = next(
            (
                f"corridor reference is not admitted: {ref}"
                for ref in target_refs
                if ref not in indexes["corridor"]
            ),
            None,
        )
        if error:
            return _operation_error("unknown-target", error)
        item = {
            "intervention_id": str(
                payload.get("intervention_id")
                or _stable_id("intervention", (target_refs, payload.get("proposal")))
            ),
            "target_refs": target_refs,
            "proposal": payload.get("proposal"),
            "evidence_refs": _as_ref_list(payload.get("evidence_refs", [])),
            "current_or_future": "future",
            "status": "proposed",
        }
        evidence_error = require_refs("evidence", item["evidence_refs"])
        if evidence_error:
            return _operation_error("unknown-evidence", evidence_error)
        record = item
        field = "future_interventions"
        item_key = "intervention_id"
    else:
        return _operation_error("operation-kind", f"unsupported operation kind: {kind}")

    if context is None:
        child = copy.deepcopy(dict(state))
    else:
        child = dict(state)
        for key, value in state.items():
            if key not in _CONTEXT_SHARED_FIELDS:
                child[key] = copy.deepcopy(value)
    values = child[field]
    if not isinstance(values, list):  # pragma: no cover - initial_proposal controls this.
        return _operation_error("state-shape", f"state field {field} is not a list")
    if record is None:  # pragma: no cover - every supported branch sets a record.
        return _operation_error("operation-record", "operation produced no proposal record")
    if kind in {"request-evidence", "request-candidates"}:
        for existing in state.get(field, []):
            if isinstance(existing, Mapping) and _semantic_record(existing) == _semantic_record(
                record
            ):
                record = existing
                break
    _append_unique(values, record, item_key)
    dispositions = child.get("obligation_dispositions", {})
    if not isinstance(dispositions, dict):
        dispositions = {}
    if kind == "select-alignment":
        selected_obligation = record.get("obligation_id")
        if selected_obligation:
            dispositions[str(selected_obligation)] = (
                "unknown" if record.get("current_or_future") == "unknown" else "selected"
            )
    elif kind == "record-departure":
        outcome_kind = record.get("outcome", {}).get("kind")
        for corridor_id in record.get("source_corridor_refs", []):
            dispositions[str(corridor_id)] = f"departure:{outcome_kind}"
    elif kind in {"propose-connection", "revise-connection"}:
        dispositions[str(record["connection_id"])] = "connection-proposed"
    child["obligation_dispositions"] = dispositions
    operations = child.get("operations")
    if not isinstance(operations, list):
        return _operation_error("state-shape", "state field operations is not a list")
    operations.append(_json_copy(operation))
    child["state_fingerprint"] = (
        _semantic_fingerprint_with_context(child, context)
        if context is not None
        else semantic_fingerprint(child)
    )
    child["state_id"] = _stable_id("proposal-state", child["state_fingerprint"])
    if child["state_fingerprint"] == state.get("state_fingerprint"):
        child["status"] = "no-progress"
        child["diagnostics"] = [
            {
                "code": "no-progress",
                "message": "operation changed event history without changing planning meaning",
            }
        ]
        return child
    unresolved_obligations = any(
        str(value) in {"unresolved", "not-assessed", "unknown"}
        for value in child.get("obligation_dispositions", {}).values()
    )
    child["status"] = (
        "unresolved"
        if child.get("planning_gaps") or child.get("unknown_facts") or unresolved_obligations
        else "provisional"
    )
    return child


_SEMANTIC_BOOKKEEPING_FIELDS = {
    "state_id",
    "state_fingerprint",
    "status",
    "diagnostics",
    "operations",
    "history",
    "history_event_id",
    "timestamp",
    "created_at",
    "updated_at",
    "provider_request_id",
    "probability_receipts",
    "unknown_id",
}


def _semantic_value(value: object, field: str | None = None) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _semantic_value(item, str(key))
            for key, item in value.items()
            if str(key) not in _SEMANTIC_BOOKKEEPING_FIELDS
        }
    if isinstance(value, list):
        values = [_semantic_value(item, field) for item in value]
        if field == "unknown_facts":
            unique: list[object] = []
            seen: set[str] = set()
            for item in values:
                marker = json.dumps(_canonical(item), sort_keys=True, separators=(",", ":"))
                if marker not in seen:
                    seen.add(marker)
                    unique.append(item)
            return unique
        return values
    return value


def _semantic_record(value: Mapping[str, object]) -> dict[str, object]:
    normalized = _semantic_value(value)
    return normalized if isinstance(normalized, dict) else {}


def _semantic_state(value: Mapping[str, object]) -> dict[str, object]:
    normalized = _semantic_value(value)
    return normalized if isinstance(normalized, dict) else {}


def _semantic_value_bytes(value: object, field: str | None = None) -> bytes:
    return json.dumps(
        _canonical(_semantic_value(value, field)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        _canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _prepare_planning_context(
    problem: Mapping[str, object], state: Mapping[str, object] | None = None
) -> _PlanningContext:
    """Verify one problem and cache its immutable lookup and identity work."""

    diagnostics = _validate_problem_shape(problem)
    if diagnostics:
        raise ValueError(json.dumps(diagnostics, sort_keys=True))
    problem_error = _problem_binding_error(problem)
    if problem_error:
        raise ValueError(problem_error)
    state_values = state if state is not None else problem
    if state is not None:
        validation = _validate_proposal_with_context(problem, state, context=None, project=False)
        validation_details = validation.get("validation", {})
        validation_diagnostics = (
            validation_details.get("diagnostics", [])
            if isinstance(validation_details, Mapping)
            else []
        )
        hard_diagnostics = [
            item
            for item in validation_diagnostics
            if isinstance(item, Mapping) and item.get("code") != "mandatory-source-unaccounted"
        ]
        if hard_diagnostics:
            raise ValueError(json.dumps(hard_diagnostics, sort_keys=True))
    semantic_field_bytes = {
        field: _semantic_value_bytes(state_values.get(field), field)
        for field in _CONTEXT_STATE_FINGERPRINT_FIELDS
    }
    state_field_bytes = {
        field: _canonical_json_bytes(state_values.get(field)) for field in _CONTEXT_SHARED_FIELDS
    }
    return _PlanningContext(
        problem_id=str(problem["problem_id"]),
        input_fingerprint=str(problem["input_fingerprint"]),
        brief_fingerprint=str(problem["brief_fingerprint"]),
        problem_fields={field: problem.get(field) for field in _CONTEXT_SHARED_FIELDS},
        state_fields={field: state_values.get(field) for field in _CONTEXT_SHARED_FIELDS},
        indexes={key: frozenset(values) for key, values in _ref_index(problem).items()},
        semantic_field_bytes=semantic_field_bytes,
        state_field_bytes=state_field_bytes,
    )


def _bind_planning_context_problem_ref(
    context: _PlanningContext, problem_ref: str
) -> _PlanningContext:
    """Bind a verified run context to its immutable history problem record."""

    return replace(context, problem_ref=problem_ref)


def _context_semantic_fingerprint(state: Mapping[str, object], context: _PlanningContext) -> str:
    fields = {
        str(key): value
        for key, value in state.items()
        if str(key) not in _SEMANTIC_BOOKKEEPING_FIELDS
    }
    encoded: list[bytes] = []
    for key in sorted(fields):
        key_bytes = json.dumps(key, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        value_bytes = context.semantic_field_bytes.get(key)
        if value_bytes is None:
            value_bytes = _semantic_value_bytes(fields[key], key)
        encoded.append(key_bytes + b":" + value_bytes)
    return hashlib.sha256(b"{" + b",".join(encoded) + b"}").hexdigest()


def _semantic_fingerprint_with_context(
    state: Mapping[str, object], context: _PlanningContext
) -> str:
    """Hash decision meaning while ignoring event and provider identity noise."""

    return _context_semantic_fingerprint(state, context)


def semantic_fingerprint(state: Mapping[str, object]) -> str:
    """Hash decision meaning through the strict public engine boundary."""

    return _fingerprint(_semantic_state(state))


def validate_proposal(
    problem: Mapping[str, object], state: Mapping[str, object]
) -> dict[str, object]:
    """Validate and project a proposal through the strict public boundary."""

    return _validate_proposal_with_context(problem, state, context=None, project=True)


def _validate_proposal_with_context(
    problem: Mapping[str, object],
    state: Mapping[str, object],
    *,
    context: _PlanningContext | None = None,
    project: bool = True,
) -> dict[str, object]:
    """Validate bindings and project the proposal into machine-readable output."""

    diagnostics = _validate_problem_shape(problem)
    effective_context = context
    context_error = _context_error(problem, state, context) if context is not None else None
    if context_error:
        diagnostics.append({"code": "context-binding", "message": context_error})
        effective_context = None
    if context is None or context_error:
        try:
            problem_error = _problem_binding_error(problem)
        except (TypeError, ValueError):
            problem_error = "planning problem identity cannot be validated"
        if problem_error:
            diagnostics.append({"code": "problem-binding", "message": problem_error})
    if state.get("schema_version") != "proposal-state/v1":
        diagnostics.append({"code": "state-schema", "message": "unsupported proposal state schema"})
    if state.get("parent_problem_id") != problem.get("problem_id"):
        diagnostics.append(
            {"code": "stale-problem", "message": "proposal belongs to another problem"}
        )
    if state.get("problem_fingerprint") != problem.get("input_fingerprint"):
        diagnostics.append(
            {"code": "problem-fingerprint", "message": "proposal input fingerprint is stale"}
        )
    try:
        state_error = _state_binding_error(problem, state, context=effective_context)
    except (TypeError, ValueError):
        state_error = "proposal state binding cannot be validated"
    if state_error:
        diagnostics.append({"code": "state-binding", "message": state_error})
    if effective_context is None and (
        state.get("brief_fingerprint") != problem.get("brief_fingerprint")
        or _canonical(state.get("brief")) != _canonical(problem.get("brief"))
    ):
        diagnostics.append({"code": "brief-binding", "message": "proposal brief binding is stale"})
    indexes = effective_context.indexes if effective_context is not None else _ref_index(problem)
    state_corridors = state.get("source_corridors", [])
    problem_corridors = problem.get("source_corridors", [])
    if not isinstance(state_corridors, list):
        diagnostics.append(
            {"code": "state-source-inventory", "message": "source inventory is not a list"}
        )
        state_corridors = []
    if not isinstance(problem_corridors, list):
        problem_corridors = []
    if effective_context is None:
        problem_corridor_by_id = {
            str(item["corridor_id"]): item
            for item in problem_corridors
            if isinstance(item, Mapping) and item.get("corridor_id")
        }
        state_corridor_by_id = {
            str(item["corridor_id"]): item
            for item in state_corridors
            if isinstance(item, Mapping) and item.get("corridor_id")
        }
        missing_inventory = sorted(set(problem_corridor_by_id) - set(state_corridor_by_id))
        extra_inventory = sorted(set(state_corridor_by_id) - set(problem_corridor_by_id))
        diagnostics.extend(
            {"code": "source-inventory-missing", "message": corridor_id}
            for corridor_id in missing_inventory
        )
        diagnostics.extend(
            {"code": "source-inventory-foreign", "message": corridor_id}
            for corridor_id in extra_inventory
        )
        diagnostics.extend(
            {
                "code": "source-inventory-mutated",
                "message": corridor_id,
            }
            for corridor_id in sorted(set(problem_corridor_by_id) & set(state_corridor_by_id))
            if _canonical(problem_corridor_by_id[corridor_id])
            != _canonical(state_corridor_by_id[corridor_id])
        )
        for collection, identifier_key in (
            ("places", "place_id"),
            ("obligations", "obligation_id"),
            ("candidates", "candidate_id"),
        ):
            expected_items = problem.get(collection, [])
            expected_items = expected_items if isinstance(expected_items, list) else []
            actual_items = state.get(collection, [])
            if not isinstance(actual_items, list):
                diagnostics.append(
                    {"code": "state-binding-shape", "message": f"{collection} is not a list"}
                )
                continue
            expected_by_id = {
                str(item[identifier_key]): item
                for item in expected_items
                if isinstance(item, Mapping) and item.get(identifier_key)
            }
            actual_by_id = {
                str(item[identifier_key]): item
                for item in actual_items
                if isinstance(item, Mapping) and item.get(identifier_key)
            }
            diagnostics.extend(
                {"code": "state-binding-missing", "message": f"{collection}:{item_id}"}
                for item_id in sorted(set(expected_by_id) - set(actual_by_id))
            )
            diagnostics.extend(
                {"code": "state-binding-foreign", "message": f"{collection}:{item_id}"}
                for item_id in sorted(set(actual_by_id) - set(expected_by_id))
            )
            diagnostics.extend(
                {"code": "state-binding-mutated", "message": f"{collection}:{item_id}"}
                for item_id in sorted(set(expected_by_id) & set(actual_by_id))
                if _canonical(expected_by_id[item_id]) != _canonical(actual_by_id[item_id])
            )
    candidate_by_id = {
        str(item["candidate_id"]): item
        for item in problem.get("candidates", [])
        if isinstance(item, Mapping) and item.get("candidate_id")
    }
    selections = state.get("selected_alignments", [])
    if not isinstance(selections, list):
        diagnostics.append(
            {"code": "state-selection-shape", "message": "selected alignments are not a list"}
        )
        selections = []
    for selection in selections:
        if not isinstance(selection, Mapping):
            diagnostics.append(
                {"code": "state-selection-shape", "message": "selection is not an object"}
            )
            continue
        candidate_id = selection.get("candidate_id")
        candidate = candidate_by_id.get(str(candidate_id))
        if candidate is None:
            diagnostics.append({"code": "unknown-candidate", "message": str(candidate_id)})
            continue
        provisional_error = _provisional_choice_error(selection)
        if provisional_error:
            diagnostics.append({"code": "provisional-choice", "message": provisional_error})
        for field in (
            "source_corridor_refs",
            "geometry_ref",
            "graph_path",
            "endpoint_provenance",
            "current_or_future",
        ):
            if _canonical(selection.get(field)) != _canonical(candidate.get(field)):
                diagnostics.append(
                    {"code": "candidate-binding", "message": f"{candidate_id}:{field}"}
                )
        obligation_id = selection.get("obligation_id")
        if (
            obligation_id is not None
            and obligation_id not in indexes["obligation"] | indexes["corridor"]
        ):
            diagnostics.append({"code": "unknown-obligation", "message": str(obligation_id)})
    departures = state.get("departures", [])
    if not isinstance(departures, list):
        diagnostics.append(
            {"code": "state-departure-shape", "message": "departures are not a list"}
        )
        departures = []
    state_gaps = state.get("planning_gaps", [])
    if not isinstance(state_gaps, list):
        diagnostics.append({"code": "state-gap-shape", "message": "planning gaps are not a list"})
        state_gaps = []
    state_gap_ids = {
        str(item["gap_id"])
        for item in state_gaps
        if isinstance(item, Mapping) and item.get("gap_id")
    }
    for departure in departures:
        if not isinstance(departure, Mapping):
            diagnostics.append(
                {"code": "state-departure-shape", "message": "departure is not an object"}
            )
            continue
        affected = _as_ref_list(departure.get("source_corridor_refs", []))
        if any(corridor_id not in indexes["corridor"] for corridor_id in affected):
            diagnostics.append({"code": "unknown-corridor", "message": str(affected)})
        geometry_error = _validate_departure_geometry(
            problem,
            affected,
            departure.get("extent"),
            departure.get("affected_geometry_refs"),
        )
        if geometry_error:
            diagnostics.append({"code": "departure-geometry", "message": geometry_error})
        evidence_refs = _as_ref_list(departure.get("evidence_refs", []))
        if not evidence_refs or any(ref not in indexes["evidence"] for ref in evidence_refs):
            diagnostics.append({"code": "departure-evidence", "message": str(evidence_refs)})
        outcome = departure.get("outcome")
        if not isinstance(outcome, Mapping) or outcome.get("kind") not in _DEPARTURE_OUTCOMES:
            diagnostics.append({"code": "departure-outcome", "message": str(outcome)})
            continue
        if outcome["kind"] == "alternate":
            candidate = candidate_by_id.get(str(outcome.get("candidate_id")))
            if candidate is None:
                diagnostics.append(
                    {"code": "unknown-candidate", "message": str(outcome.get("candidate_id"))}
                )
            elif not set(affected).intersection(candidate.get("source_corridor_refs", [])):
                diagnostics.append(
                    {"code": "candidate-binding", "message": "alternate is foreign to departure"}
                )
            elif not _has_selected_alignment(state, candidate, affected):
                diagnostics.append(
                    {
                        "code": "alternate-selection",
                        "message": "alternate departure requires a selected admitted alignment",
                    }
                )
        elif outcome["kind"] == "unresolved" and str(outcome.get("gap_id")) not in (
            indexes["gap"] | state_gap_ids
        ):
            diagnostics.append({"code": "departure-gap", "message": str(outcome.get("gap_id"))})
    unknown_facts = state.get("unknown_facts", [])
    if not isinstance(unknown_facts, list):
        diagnostics.append(
            {"code": "state-unknown-shape", "message": "unknown facts are not a list"}
        )
        unknown_facts = []
    if isinstance(unknown_facts, list):
        known_targets = (
            indexes["corridor"]
            | indexes["place"]
            | indexes["obligation"]
            | indexes["candidate"]
            | indexes["evidence"]
        )
        for unknown in unknown_facts:
            if not isinstance(unknown, Mapping):
                continue
            refs = _as_ref_list(unknown.get("subject_refs", []))
            if any(ref not in known_targets for ref in refs):
                diagnostics.append({"code": "unknown-target", "message": str(refs)})
    connections = state.get("connection_intents", [])
    if not isinstance(connections, list):
        diagnostics.append(
            {"code": "state-connection-shape", "message": "connection intents are not a list"}
        )
        connections = []
    if isinstance(connections, list):
        for connection in connections:
            if not isinstance(connection, Mapping):
                continue
            if connection.get("origin_place_id") not in indexes["place"]:
                diagnostics.append(
                    {"code": "unknown-place", "message": str(connection.get("origin_place_id"))}
                )
            if connection.get("destination_place_id") not in indexes["place"]:
                diagnostics.append(
                    {
                        "code": "unknown-place",
                        "message": str(connection.get("destination_place_id")),
                    }
                )
            if any(
                ref not in indexes["corridor"]
                for ref in _as_ref_list(connection.get("corridor_refs", []))
            ):
                diagnostics.append(
                    {"code": "unknown-corridor", "message": str(connection.get("corridor_refs"))}
                )
    interventions = state.get("future_interventions", [])
    if isinstance(interventions, list):
        for intervention in interventions:
            if not isinstance(intervention, Mapping):
                continue
            target_refs = _as_ref_list(intervention.get("target_refs", []))
            if any(ref not in indexes["corridor"] for ref in target_refs):
                diagnostics.append({"code": "unknown-target", "message": str(target_refs)})
            evidence_refs = _as_ref_list(intervention.get("evidence_refs", []))
            if any(ref not in indexes["evidence"] for ref in evidence_refs):
                diagnostics.append({"code": "unknown-evidence", "message": str(evidence_refs)})
    problem_obligations = problem.get("obligations", [])
    problem_obligations = problem_obligations if isinstance(problem_obligations, list) else []
    expected_obligations = {
        str(item["obligation_id"])
        for item in problem_obligations
        if isinstance(item, Mapping) and item.get("obligation_id")
    }
    connection_ids = {
        str(item["connection_id"])
        for item in connections
        if isinstance(item, Mapping) and item.get("connection_id")
    }
    expected_disposition_refs = expected_obligations | indexes["corridor"] | connection_ids
    dispositions = state.get("obligation_dispositions", {})
    if not isinstance(dispositions, Mapping):
        diagnostics.append(
            {
                "code": "obligation-disposition-shape",
                "message": "obligation dispositions are not an object",
            }
        )
        dispositions = {}
    missing_dispositions = sorted(expected_obligations - set(str(key) for key in dispositions))
    diagnostics.extend(
        {"code": "obligation-disposition-missing", "message": obligation_id}
        for obligation_id in missing_dispositions
    )
    unknown_dispositions = sorted(set(str(key) for key in dispositions) - expected_disposition_refs)
    diagnostics.extend(
        {"code": "obligation-disposition-foreign", "message": obligation_id}
        for obligation_id in unknown_dispositions
    )
    valid_dispositions = {
        "unresolved",
        "not-assessed",
        "unknown",
        "selected",
        "connection-proposed",
        "departure:alternate",
        "departure:unresolved",
        "departure:no-loss",
    }
    diagnostics.extend(
        {"code": "obligation-disposition-value", "message": str(key)}
        for key, value in dispositions.items()
        if str(value) not in valid_dispositions
    )
    for obligation_id, value in dispositions.items():
        key = str(obligation_id)
        value = str(value)
        if value == "selected":
            has_selection = any(
                isinstance(selection, Mapping)
                and (
                    str(selection.get("obligation_id")) == key
                    or key in _as_ref_list(selection.get("source_corridor_refs", []))
                )
                for selection in selections
            )
            if not has_selection:
                diagnostics.append(
                    {
                        "code": "obligation-disposition-proof",
                        "message": f"selected disposition has no admitted alignment: {key}",
                    }
                )
        elif value == "connection-proposed":
            if not any(
                isinstance(connection, Mapping) and str(connection.get("connection_id")) == key
                for connection in connections
            ):
                diagnostics.append(
                    {
                        "code": "obligation-disposition-proof",
                        "message": f"connection disposition has no admitted intent: {key}",
                    }
                )
        elif value.startswith("departure:"):
            outcome_kind = value.split(":", 1)[1]
            has_departure = any(
                isinstance(departure, Mapping)
                and outcome_kind
                == (
                    departure.get("outcome", {}).get("kind")
                    if isinstance(departure.get("outcome"), Mapping)
                    else None
                )
                and key in _as_ref_list(departure.get("source_corridor_refs", []))
                for departure in departures
            )
            if not has_departure:
                diagnostics.append(
                    {
                        "code": "obligation-disposition-proof",
                        "message": f"departure disposition has no decision record: {key}",
                    }
                )
    unresolved_obligation_set = {
        obligation_id
        for obligation_id, value in dispositions.items()
        if str(value) in {"unresolved", "not-assessed", "unknown", "connection-proposed"}
    }
    for selection in selections:
        if isinstance(selection, Mapping) and selection.get("current_or_future") == "unknown":
            unresolved_obligation_set.add(str(selection.get("obligation_id")))
    for departure in departures:
        outcome = departure.get("outcome") if isinstance(departure, Mapping) else None
        if not isinstance(outcome, Mapping) or outcome.get("kind") != "alternate":
            continue
        candidate = candidate_by_id.get(str(outcome.get("candidate_id")))
        if candidate is not None and candidate.get("current_or_future") == "unknown":
            unresolved_obligation_set.update(
                str(ref) for ref in _as_ref_list(departure.get("source_corridor_refs", []))
            )
    unresolved_obligations = sorted(obligation_id for obligation_id in unresolved_obligation_set)
    mandatory = {
        str(item["corridor_id"])
        for item in problem_corridors
        if isinstance(item, Mapping)
        and item.get("corridor_id")
        and item.get("mandatory_planning_corridor")
    }
    accounted = {
        str(item)
        for departure in departures
        if isinstance(departure, Mapping)
        for item in departure.get("source_corridor_refs", [])
    } | {
        str(item)
        for selection in selections
        if isinstance(selection, Mapping)
        for item in selection.get("source_corridor_refs", [])
    }
    missing = sorted(mandatory - accounted)
    diagnostics.extend(
        {"code": "mandatory-source-unaccounted", "message": corridor_id} for corridor_id in missing
    )
    incomplete = bool(missing or unresolved_obligations or state_gaps or unknown_facts)
    hard_diagnostics = [
        item for item in diagnostics if item["code"] != "mandatory-source-unaccounted"
    ]
    output_status = (
        "invalid" if hard_diagnostics else "reviewable-incomplete" if incomplete else "validated"
    )
    if not project:
        return {
            "schema_version": "validated-output/v1",
            "status": output_status,
            "proposal_state_ref": state.get("state_id"),
            "problem_ref": problem.get("problem_id"),
            "validation": {"diagnostics": diagnostics},
        }
    corridor_dispositions: list[dict[str, object]] = []
    for corridor in problem_corridors:
        if not isinstance(corridor, Mapping) or not corridor.get("corridor_id"):
            continue
        corridor_id = str(corridor["corridor_id"])
        selected = any(
            corridor_id in _as_ref_list(selection.get("source_corridor_refs", []))
            for selection in selections
            if isinstance(selection, Mapping)
        )
        departure = next(
            (
                item
                for item in departures
                if isinstance(item, Mapping)
                and corridor_id in _as_ref_list(item.get("source_corridor_refs", []))
            ),
            None,
        )
        disposition = (
            "selected"
            if selected
            else f"departure:{departure['outcome']['kind']}"
            if isinstance(departure, Mapping) and isinstance(departure.get("outcome"), Mapping)
            else "unresolved"
            if corridor.get("mandatory_planning_corridor")
            else str(corridor.get("departure_disposition", "not-assessed"))
        )
        corridor_dispositions.append(
            {
                "corridor_id": corridor_id,
                "mandatory": corridor.get("mandatory_planning_corridor", False),
                "disposition": disposition,
            }
        )
    output = {
        "schema_version": "validated-output/v1",
        "status": output_status,
        "proposal_state_ref": state.get("state_id"),
        "problem_ref": problem.get("problem_id"),
        "source_inventory": _json_copy(state.get("source_corridors", [])),
        "places": _json_copy(state.get("places", [])),
        "obligations": _json_copy(state.get("obligations", [])),
        "obligation_dispositions": _json_copy(dict(dispositions)),
        "unresolved_obligation_refs": unresolved_obligations,
        "source_corridor_dispositions": corridor_dispositions,
        "selected_alignments": _json_copy(state.get("selected_alignments", [])),
        "departures": _json_copy(state.get("departures", [])),
        "planning_gaps": _json_copy(state_gaps),
        "unknown_facts": _json_copy(unknown_facts),
        "future_interventions": _json_copy(state.get("future_interventions", [])),
        "validation": {"diagnostics": diagnostics, "mandatory_source_refs": sorted(mandatory)},
    }
    output["output_fingerprint"] = _fingerprint(output)
    return output


__all__ = [
    "admit_expansion",
    "apply_operation",
    "build_planning_problem",
    "expand_connection",
    "initial_proposal",
    "replay_expansion",
    "semantic_fingerprint",
    "validate_proposal",
]
