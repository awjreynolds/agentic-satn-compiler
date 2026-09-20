"""Small experimental planning seam for admitted source corridors."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

import geopandas as gpd
from pyproj import CRS
from shapely.geometry import LineString, MultiLineString, mapping

from satn.content_identity import canonical_network_geometry_fingerprint
from satn.models import AreaConfig
from satn.routing import RoadGraph
from satn.sources import load_snapshot

_LINE_GEOMETRIES = (LineString, MultiLineString)


def _fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_value(value: object) -> object:
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True))


def _crs_identity(crs: object) -> str:
    if crs is None:
        raise ValueError("planning corridor geometry requires a CRS")
    resolved = CRS.from_user_input(crs)
    authority = resolved.to_authority()
    return f"{authority[0]}:{authority[1]}" if authority is not None else resolved.to_wkt()


def _text(value: object, fallback: str) -> str:
    return str(value) if value is not None and str(value).strip() else fallback


def _source_ref(evidence_id: object, source_id: object) -> dict[str, str]:
    if evidence_id is None or not str(evidence_id).strip():
        raise ValueError("A-road source evidence requires an evidence identity")
    if source_id is None or not str(source_id).strip():
        raise ValueError("A-road source evidence requires a source identity")
    return {
        "evidence_id": str(evidence_id),
        "source_id": str(source_id),
    }


def _stable_id(prefix: str, value: object) -> str:
    return f"{prefix}-{_fingerprint(value)}"


def _source_observations(source: Mapping[str, object]) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    context = source.get("context")
    if isinstance(context, gpd.GeoDataFrame):
        context_rows = (
            context.loc[context["feature_type"].astype(str).eq("a-road-spine")]
            if "feature_type" in context
            else context.iloc[0:0]
        )
        for _, row in context_rows.iterrows():
            geometry = row.geometry
            if not isinstance(geometry, _LINE_GEOMETRIES) or geometry.is_empty:
                raise ValueError("A-road source evidence requires non-empty line geometry")
            observations.append(
                {
                    "evidence_id": row.get("evidence_id"),
                    "source_id": row.get("source_id"),
                    "name": row.get("name"),
                    "scope_status": row.get("network_scope"),
                    "geometry": geometry,
                    "crs": context.crs,
                }
            )

    official = source.get("official_road_classification")
    if isinstance(official, gpd.GeoDataFrame) and not official.empty:
        classification = official.get("official_classification")
        for _, row in official.loc[classification.astype(str).eq("a-road")].iterrows():
            geometry = row.geometry
            if not isinstance(geometry, _LINE_GEOMETRIES) or geometry.is_empty:
                raise ValueError("A-road source evidence requires non-empty line geometry")
            observations.append(
                {
                    "evidence_id": row.get("official_feature_id"),
                    "source_id": row.get("source_id"),
                    "name": row.get("official_road_number") or row.get("official_road_name"),
                    "scope_status": "unresolved",
                    "geometry": geometry,
                    "crs": official.crs,
                }
            )
    return observations


def _topology_fact(
    geometry: object,
    geometry_crs: object,
    graph: RoadGraph | None,
) -> dict[str, object]:
    graph_geometry = geometry
    if graph is not None and graph.crs is not None and geometry_crs != graph.crs:
        graph_geometry = gpd.GeoSeries([geometry], crs=geometry_crs).to_crs(graph.crs).iloc[0]
    nodes = tuple(graph.nodes_on_geometry(graph_geometry) if graph is not None else ())
    node_ids = sorted({str(node_id) for node_id, _distance in nodes})
    attached = bool(node_ids)
    return {
        "graph_attachment": "attached" if attached else "unattached",
        "node_ids": node_ids,
        "directed_edge_ids": [],
        "source_edge_ids": [],
        "status": "resolved" if attached else "unresolved",
    }


@dataclass(frozen=True)
class ExperimentalPlanningResult:
    """Machine-readable result of the first experimental planning slice."""

    status: str
    source_corridors: tuple[dict[str, object], ...]
    planning_gaps: tuple[dict[str, object], ...]
    unknown_facts: tuple[dict[str, object], ...]
    exclusions: tuple[dict[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "experimental-planning/v1",
            "status": self.status,
            "source_corridors": _json_value(self.source_corridors),
            "planning_gaps": _json_value(self.planning_gaps),
            "unknown_facts": _json_value(self.unknown_facts),
            "exclusions": _json_value(self.exclusions),
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


def run_experimental_planning(config: AreaConfig) -> ExperimentalPlanningResult:
    """Admit source corridors from one pinned snapshot for experimental planning."""

    source = load_snapshot(config)
    observations = _source_observations(source)
    network = source.get("network")
    graph = (
        RoadGraph(network) if isinstance(network, gpd.GeoDataFrame) and not network.empty else None
    )

    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for observation in observations:
        geometry = observation["geometry"]
        crs = observation["crs"]
        if not isinstance(geometry, _LINE_GEOMETRIES):  # pragma: no cover - validated above.
            raise ValueError("A-road source evidence requires line geometry")
        crs_identity = _crs_identity(crs)
        geometry_fingerprint = canonical_network_geometry_fingerprint(geometry, crs)
        key = (crs_identity, geometry_fingerprint)
        source_ref = _source_ref(observation.get("evidence_id"), observation.get("source_id"))
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = {
                "geometry": geometry,
                "crs": crs,
                "source_refs": [source_ref],
                "name": _text(observation.get("name"), "Unnamed A-road"),
                "scope_status": _text(observation.get("scope_status"), "unresolved"),
            }
        elif source_ref not in existing["source_refs"]:
            existing["source_refs"].append(source_ref)

    corridors: list[dict[str, object]] = []
    gaps: list[dict[str, object]] = []
    unknowns: list[dict[str, object]] = []
    for item in sorted(
        grouped.values(),
        key=lambda value: (
            tuple((ref["source_id"], ref["evidence_id"]) for ref in value["source_refs"]),
            value["name"],
        ),
    ):
        geometry = item["geometry"]
        crs = item["crs"]
        source_refs = sorted(
            item["source_refs"],
            key=lambda value: (value["source_id"], value["evidence_id"]),
        )
        geometry_fingerprint = canonical_network_geometry_fingerprint(geometry, crs)
        corridor_id = _stable_id(
            "planning-corridor",
            {"geometry": geometry_fingerprint, "source_refs": source_refs},
        )
        topology = _topology_fact(geometry, crs, graph)
        geometry_ref = {
            "geometry_id": _stable_id("geometry", geometry_fingerprint),
            "crs": _crs_identity(crs),
            "geometry_kind": geometry.geom_type,
            "content_fingerprint": geometry_fingerprint,
            "source_ref": source_refs[0]["source_id"],
            "geometry": _json_value(mapping(geometry)),
        }
        corridor = {
            "corridor_id": corridor_id,
            "section_id": f"{corridor_id}-section",
            "source_refs": source_refs,
            "name": item["name"],
            "geometry_ref": geometry_ref,
            "classification": "a-road",
            "scope_status": item["scope_status"],
            "mandatory_planning_corridor": True,
            "topology_fact": topology,
            "evidence_refs": [ref["evidence_id"] for ref in source_refs],
            "departure_disposition": "not-assessed",
            "decision_ref": None,
            "status": "admitted-with-unknowns"
            if topology["status"] == "unresolved"
            else "admitted",
        }
        corridors.append(corridor)
        if topology["status"] == "unresolved":
            gap_id = _stable_id("planning-gap", corridor_id)
            gaps.append(
                {
                    "gap_id": gap_id,
                    "subject_id": corridor_id,
                    "obligation_id": corridor_id,
                    "reason": "source corridor has no routing-graph attachment",
                    "status": "unresolved",
                }
            )
            unknowns.append(
                {
                    "unknown_id": _stable_id("unknown", corridor_id),
                    "subject_id": corridor_id,
                    "claim": "routing-graph attachment",
                    "reason": "source corridor has no matching routing-graph geometry",
                    "status": "unresolved",
                }
            )

    status = "admitted-with-unknowns" if gaps or unknowns else "admitted"
    return ExperimentalPlanningResult(
        status=status,
        source_corridors=tuple(corridors),
        planning_gaps=tuple(gaps),
        unknown_facts=tuple(unknowns),
    )


__all__ = ["ExperimentalPlanningResult", "run_experimental_planning"]
