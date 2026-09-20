"""Small experimental planning seam for admitted source corridors."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString

from satn.models import AreaConfig
from satn.routing import RoadGraph

_LINE_GEOMETRIES = (LineString, MultiLineString)


def _fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_value(value: object) -> object:
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True))


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
    """Project the new planning problem into the first-slice result shape."""

    # Import lazily: planning_engine reuses the identity/topology helpers above.
    from satn.planning_engine import build_planning_problem

    problem = build_planning_problem(config)
    corridors = tuple(
        _json_value(corridor)
        for corridor in problem["source_corridors"]
        if corridor.get("classification") == "a-road"
    )
    corridor_ids = {str(corridor["corridor_id"]) for corridor in corridors}
    gaps = tuple(
        _json_value(gap)
        for gap in problem.get("planning_gaps", [])
        if str(gap.get("subject_id")) in corridor_ids
    )
    unknowns = tuple(
        _json_value(unknown)
        for unknown in problem.get("unknown_facts", [])
        if str(unknown.get("subject_id")) in corridor_ids
    )

    return ExperimentalPlanningResult(
        status="admitted-with-unknowns" if gaps or unknowns else "admitted",
        source_corridors=corridors,
        planning_gaps=gaps,
        unknown_facts=unknowns,
    )


__all__ = ["ExperimentalPlanningResult", "run_experimental_planning"]
