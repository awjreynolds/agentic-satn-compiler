"""Lossless, deterministic planning graph records.

The planning graph is deliberately separate from the legacy :class:`RoadGraph`.
It preserves the source edge universe for later candidate discovery without making
route or selection decisions.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import geopandas as gpd
import networkx as nx
import pandas as pd
from pyproj import CRS
from shapely.geometry import LineString

from satn.evidence_contracts import (
    evidence_fingerprint,
    evidence_geometry_fingerprint,
)
from satn.route_controls import RouteControlSet


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def _sha256(value: str, name: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase full SHA-256")
    return value


def _crs_identity(value: object) -> str:
    try:
        crs = CRS.from_user_input(value)
    except Exception as error:
        raise ValueError("planning graph requires an explicit valid CRS") from error
    authority = crs.to_authority()
    if authority is not None:
        return f"{authority[0]}:{authority[1]}"
    return crs.to_wkt(version="WKT2_2019", pretty=False)


@dataclass(frozen=True)
class SourceExportFrame:
    """One governed routable-edge frame at the planning seam."""

    frame: gpd.GeoDataFrame
    source_export_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.frame, gpd.GeoDataFrame):
            raise ValueError("routable edges must be a GeoDataFrame")
        if self.frame.crs is None:
            raise ValueError("routable edges require an explicit CRS")
        _sha256(self.source_export_fingerprint, "source export fingerprint")


@dataclass(frozen=True)
class PlanningGraphProfile:
    """Fingerprintable graph-construction policy, never route preference policy."""

    canonical_crs: str
    profile_id: str = "planning-graph-trial-v1"
    version: int = 1
    legal_access_profile_id: str = "retain-claims-v1"

    def __post_init__(self) -> None:
        _required_text(self.profile_id, "profile id")
        _required_text(self.legal_access_profile_id, "legal access profile id")
        if self.version < 1:
            raise ValueError("profile version must be positive")
        object.__setattr__(self, "canonical_crs", _crs_identity(self.canonical_crs))
        if self.canonical_crs != "EPSG:27700":
            raise ValueError("planning graph canonical CRS must be EPSG:27700")

    @property
    def fingerprint(self) -> str:
        return evidence_fingerprint(
            {
                "contract": "satn-planning-graph-profile/v1",
                "profile_id": self.profile_id,
                "version": self.version,
                "canonical_crs": self.canonical_crs,
                "legal_access_profile_id": self.legal_access_profile_id,
            }
        )


@dataclass(frozen=True)
class PlanningGraphRequest:
    routable_edges: SourceExportFrame
    asset_observations: tuple[object, ...]
    road_observations: tuple[object, ...]
    route_controls: RouteControlSet | None
    profile: PlanningGraphProfile

    def __post_init__(self) -> None:
        if self.route_controls is not None and not isinstance(
            self.route_controls, RouteControlSet
        ):
            raise ValueError("route controls must be a RouteControlSet")


@dataclass(frozen=True)
class PlanningEdgeRecord:
    source_edge_id: str
    directed_edge_id: str
    from_node_id: str
    to_node_id: str
    geometry_wkt: str
    geometry_fingerprint: str
    length_mm: int
    highway: str | None
    ref: str | None
    access: str | None
    bicycle: str | None
    foot: str | None
    oneway: bool | None
    reciprocal_state: Literal["reciprocal", "one-way", "unknown"]
    weak_component_id: str
    strong_component_id: str
    access_observation_ids: tuple[str, ...] = ()
    asset_observation_ids: tuple[str, ...] = ()
    road_observation_ids: tuple[str, ...] = ()
    claim_observation_ids: tuple[tuple[str, tuple[str, ...]], ...] = ()
    unknown_claims: tuple[str, ...] = ()

    @property
    def length_m(self) -> float:
        return self.length_mm / 1_000


@dataclass(frozen=True)
class PlanningNodeRecord:
    node_id: str
    weak_component_id: str
    strong_component_id: str


@dataclass(frozen=True)
class GraphComponentRecord:
    component_id: str
    kind: Literal["weak", "strong"]
    node_ids: tuple[str, ...]
    directed_edge_ids: tuple[str, ...]
    node_count: int
    edge_count: int


@dataclass(frozen=True)
class EdgeObservationBinding:
    observation_id: str
    subject_id: str
    claim: str
    state: str
    directed_edge_ids: tuple[str, ...]
    source_kind: Literal["asset", "road"]


@dataclass(frozen=True)
class GraphDiagnostic:
    code: str
    subject_id: str
    message: str


@dataclass(frozen=True)
class PlanningGraphSnapshot:
    graph_fingerprint: str
    edge_records: tuple[PlanningEdgeRecord, ...]
    node_records: tuple[PlanningNodeRecord, ...]
    component_records: tuple[GraphComponentRecord, ...]
    observation_matches: tuple[EdgeObservationBinding, ...]
    diagnostics: tuple[GraphDiagnostic, ...]
    profile_fingerprint: str
    source_export_fingerprint: str
    route_control_fingerprint: str | None


@dataclass(frozen=True)
class _EdgeDraft:
    source_edge_id: str
    from_node_id: str
    to_node_id: str
    geometry: LineString
    geometry_fingerprint: str
    length_mm: int
    highway: str | None
    ref: str | None
    access: str | None
    bicycle: str | None
    foot: str | None
    oneway: bool | None
    unknown_claims: tuple[str, ...]

    @property
    def directed_edge_id(self) -> str:
        return evidence_fingerprint(
            {
                "contract": "satn-planning-directed-edge/v1",
                "source_edge_id": self.source_edge_id,
                "from_node_id": self.from_node_id,
                "to_node_id": self.to_node_id,
                "geometry_fingerprint": self.geometry_fingerprint,
            }
        )


def build_planning_graph(request: PlanningGraphRequest) -> PlanningGraphSnapshot:
    """Return a deterministic snapshot retaining every valid directed source edge."""

    if not isinstance(request, PlanningGraphRequest):
        raise ValueError("planning graph requires a PlanningGraphRequest")
    source = request.routable_edges
    profile = request.profile
    source_crs = _crs_identity(source.frame.crs)
    if source_crs != profile.canonical_crs:
        return _empty_snapshot(
            source,
            profile,
            request.route_controls,
            GraphDiagnostic(
                code="source-crs-mismatch",
                subject_id=source.source_export_fingerprint,
                message=f"expected {profile.canonical_crs}; received {source_crs}",
            ),
        )

    diagnostics: list[GraphDiagnostic] = []
    drafts_by_direction: dict[tuple[str, str, str], list[_EdgeDraft]] = {}
    for row_number, row in source.frame.iterrows():
        try:
            draft = _draft_from_row(row, profile.canonical_crs)
        except (TypeError, ValueError) as error:
            diagnostics.append(
                GraphDiagnostic(
                    code="invalid-source-edge",
                    subject_id=str(row.get("source_edge_id", row_number)),
                    message=str(error),
                )
            )
            continue
        key = (draft.source_edge_id, draft.from_node_id, draft.to_node_id)
        drafts_by_direction.setdefault(key, []).append(draft)

    drafts: dict[str, _EdgeDraft] = {}
    for key in sorted(drafts_by_direction):
        candidates = sorted(
            drafts_by_direction[key],
            key=_draft_signature,
        )
        draft = candidates[0]
        if len({_draft_signature(candidate) for candidate in candidates}) > 1:
            diagnostics.append(
                GraphDiagnostic(
                    code="conflicting-directed-edge",
                    subject_id=draft.source_edge_id,
                    message="duplicate directed edge identity has conflicting geometry",
                )
            )
        drafts[draft.directed_edge_id] = draft
        diagnostics.extend(
            GraphDiagnostic(
                code="unknown-edge-claim",
                subject_id=draft.source_edge_id,
                message=f"{claim} is missing or invalid",
            )
            for claim in draft.unknown_claims
        )

    graph = nx.MultiDiGraph()
    for draft in sorted(drafts.values(), key=lambda item: item.directed_edge_id):
        graph.add_edge(
            draft.from_node_id,
            draft.to_node_id,
            key=draft.directed_edge_id,
            directed_edge_id=draft.directed_edge_id,
        )

    weak_by_node, strong_by_node, components = _component_records(graph)
    bindings, observation_ids, observation_diagnostics = _bind_observations(
        drafts.values(), request.asset_observations, request.road_observations
    )
    diagnostics.extend(observation_diagnostics)
    records = tuple(
        PlanningEdgeRecord(
            source_edge_id=draft.source_edge_id,
            directed_edge_id=draft.directed_edge_id,
            from_node_id=draft.from_node_id,
            to_node_id=draft.to_node_id,
            geometry_wkt=draft.geometry.wkt,
            geometry_fingerprint=draft.geometry_fingerprint,
            length_mm=draft.length_mm,
            highway=draft.highway,
            ref=draft.ref,
            access=draft.access,
            bicycle=draft.bicycle,
            foot=draft.foot,
            oneway=draft.oneway,
            reciprocal_state=(
                "unknown"
                if draft.oneway is None
                else "one-way"
                if draft.oneway
                else "reciprocal"
            ),
            weak_component_id=weak_by_node[draft.from_node_id],
            strong_component_id=strong_by_node[draft.from_node_id],
            access_observation_ids=observation_ids.get((draft.source_edge_id, "access"), ()),
            asset_observation_ids=observation_ids.get((draft.source_edge_id, "asset"), ()),
            road_observation_ids=observation_ids.get((draft.source_edge_id, "road"), ()),
            claim_observation_ids=_claim_observation_ids(
                bindings, draft.source_edge_id
            ),
            unknown_claims=draft.unknown_claims,
        )
        for draft in sorted(
            drafts.values(),
            key=lambda item: (
                item.source_edge_id,
                item.from_node_id,
                item.to_node_id,
                item.directed_edge_id,
            ),
        )
    )
    nodes = tuple(
        PlanningNodeRecord(
            node_id=str(node),
            weak_component_id=weak_by_node[str(node)],
            strong_component_id=strong_by_node[str(node)],
        )
        for node in sorted(graph.nodes, key=str)
    )
    diagnostics_tuple = tuple(
        sorted(diagnostics, key=lambda item: (item.code, item.subject_id, item.message))
    )
    payload = _snapshot_payload(
        records,
        nodes,
        components,
        bindings,
        diagnostics_tuple,
        profile.fingerprint,
        source.source_export_fingerprint,
        _route_control_fingerprint(request.route_controls),
    )
    return PlanningGraphSnapshot(
        graph_fingerprint=evidence_fingerprint(payload),
        edge_records=records,
        node_records=nodes,
        component_records=components,
        observation_matches=bindings,
        diagnostics=diagnostics_tuple,
        profile_fingerprint=profile.fingerprint,
        source_export_fingerprint=source.source_export_fingerprint,
        route_control_fingerprint=_route_control_fingerprint(request.route_controls),
    )


def _draft_from_row(row: Mapping[str, object], crs: str) -> _EdgeDraft:
    source_edge_id = _required_text(row.get("source_edge_id"), "source edge id")
    from_node_id = _node_id(row.get("u"), "from node id")
    to_node_id = _node_id(row.get("v"), "to node id")
    geometry = row.get("geometry")
    if not isinstance(geometry, LineString) or geometry.is_empty or not geometry.is_valid:
        raise ValueError("source edge requires a nonempty valid LineString")
    if len(geometry.coords) < 2 or geometry.length <= 0:
        raise ValueError("source edge geometry must have positive length")
    geometry_fingerprint = evidence_geometry_fingerprint(geometry, crs)
    oneway = _boolean_tag(row.get("oneway"))
    return _EdgeDraft(
        source_edge_id=source_edge_id,
        from_node_id=from_node_id,
        to_node_id=to_node_id,
        geometry=geometry,
        geometry_fingerprint=geometry_fingerprint,
        length_mm=round(geometry.length * 1_000),
        highway=_optional_tag_text(row.get("highway")),
        ref=_optional_tag_text(row.get("ref")),
        access=_optional_tag_text(row.get("access")),
        bicycle=_optional_tag_text(row.get("bicycle")),
        foot=_optional_tag_text(row.get("foot")),
        oneway=oneway,
        unknown_claims=("oneway",) if oneway is None else (),
    )


def _node_id(value: object, name: str) -> str:
    if value is None or bool(pd.isna(value)):
        raise ValueError(f"{name} is required")
    return _required_text(str(value), name)


def _boolean_tag(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"yes", "true", "1"}:
            return True
        if normalized in {"no", "false", "0", ""}:
            return False
    return None


def _optional_tag_text(value: object) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (list, tuple, set)):
        values = tuple(sorted(str(item).strip() for item in value if str(item).strip()))
        return "|".join(values) or None
    text = str(value).strip()
    return text or None


def _draft_signature(draft: _EdgeDraft) -> tuple[object, ...]:
    return (
        draft.geometry_fingerprint,
        draft.length_mm,
        draft.highway or "",
        draft.ref or "",
        draft.access or "",
        draft.bicycle or "",
        draft.foot or "",
        "unknown" if draft.oneway is None else str(draft.oneway).lower(),
        draft.directed_edge_id,
    )


def _claim_observation_ids(
    bindings: tuple[EdgeObservationBinding, ...], source_edge_id: str
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    claims = sorted(
        {
            binding.claim
            for binding in bindings
            if binding.subject_id == source_edge_id and binding.directed_edge_ids
        }
    )
    return tuple(
        (
            claim,
            tuple(
                binding.observation_id
                for binding in bindings
                if binding.subject_id == source_edge_id
                and binding.claim == claim
                and binding.directed_edge_ids
            ),
        )
        for claim in claims
    )


def _component_records(
    graph: nx.MultiDiGraph,
) -> tuple[dict[str, str], dict[str, str], tuple[GraphComponentRecord, ...]]:
    weak_by_node: dict[str, str] = {}
    strong_by_node: dict[str, str] = {}
    records: list[GraphComponentRecord] = []
    for kind, groups in (
        ("weak", nx.weakly_connected_components(graph)),
        ("strong", nx.strongly_connected_components(graph)),
    ):
        ordered_groups = sorted(tuple(sorted(map(str, group))) for group in groups)
        for node_ids in ordered_groups:
            component_id = evidence_fingerprint(
                {"contract": "satn-planning-graph-component/v1", "kind": kind, "node_ids": node_ids}
            )
            directed_ids = tuple(
                sorted(
                    str(data["directed_edge_id"])
                    for u, v, _key, data in graph.edges(keys=True, data=True)
                    if str(u) in node_ids and str(v) in node_ids
                )
            )
            records.append(
                GraphComponentRecord(
                    component_id=component_id,
                    kind=kind,  # type: ignore[arg-type]
                    node_ids=node_ids,
                    directed_edge_ids=directed_ids,
                    node_count=len(node_ids),
                    edge_count=len(directed_ids),
                )
            )
            target = weak_by_node if kind == "weak" else strong_by_node
            target.update({node_id: component_id for node_id in node_ids})
    return weak_by_node, strong_by_node, tuple(
        sorted(records, key=lambda item: (item.kind, item.node_ids))
    )


def _observation_fields(observation: object) -> tuple[str, str, str, str]:
    draft = getattr(observation, "draft", observation)
    return (
        _required_text(getattr(draft, "observation_id", None), "observation id"),
        _required_text(getattr(draft, "subject_id", None), "observation subject id"),
        _required_text(getattr(draft, "claim", None), "observation claim"),
        _required_text(getattr(draft, "state", None), "observation state"),
    )


def _bind_observations(
    drafts: Sequence[_EdgeDraft] | object,
    asset_observations: tuple[object, ...],
    road_observations: tuple[object, ...],
) -> tuple[
    tuple[EdgeObservationBinding, ...],
    dict[tuple[str, str], tuple[str, ...]],
    tuple[GraphDiagnostic, ...],
]:
    draft_tuple = tuple(drafts)  # type: ignore[arg-type]
    edges_by_source: dict[str, tuple[str, ...]] = {}
    for source_id in sorted({draft.source_edge_id for draft in draft_tuple}):
        edges_by_source[source_id] = tuple(
            sorted(
                draft.directed_edge_id
                for draft in draft_tuple
                if draft.source_edge_id == source_id
            )
        )
    bindings: list[EdgeObservationBinding] = []
    grouped: dict[tuple[str, str], list[str]] = {}
    diagnostics: list[GraphDiagnostic] = []
    for source_kind, observations in (
        ("asset", asset_observations),
        ("road", road_observations),
    ):
        for observation in observations:
            try:
                observation_id, subject_id, claim, state = _observation_fields(observation)
            except ValueError as error:
                diagnostics.append(
                    GraphDiagnostic(
                        code="invalid-observation",
                        subject_id=type(observation).__name__,
                        message=str(error),
                    )
                )
                continue
            directed_ids = edges_by_source.get(subject_id, ())
            bindings.append(
                EdgeObservationBinding(
                    observation_id=observation_id,
                    subject_id=subject_id,
                    claim=claim,
                    state=state,
                    directed_edge_ids=directed_ids,
                    source_kind=source_kind,  # type: ignore[arg-type]
                )
            )
            if not directed_ids:
                diagnostics.append(
                    GraphDiagnostic(
                        code="unmatched-observation",
                        subject_id=observation_id,
                        message=f"no planning edge has source identity {subject_id}",
                    )
                )
            if directed_ids:
                grouped.setdefault((subject_id, claim), []).append(observation_id)
                grouped.setdefault((subject_id, source_kind), []).append(observation_id)
    return (
        tuple(
            sorted(
                bindings,
                key=lambda item: (item.subject_id, item.claim, item.observation_id),
            )
        ),
        {key: tuple(sorted(set(values))) for key, values in grouped.items()},
        tuple(
            sorted(
                diagnostics,
                key=lambda item: (item.code, item.subject_id, item.message),
            )
        ),
    )


def _snapshot_payload(
    edges: tuple[PlanningEdgeRecord, ...],
    nodes: tuple[PlanningNodeRecord, ...],
    components: tuple[GraphComponentRecord, ...],
    bindings: tuple[EdgeObservationBinding, ...],
    diagnostics: tuple[GraphDiagnostic, ...],
    profile_fingerprint: str,
    source_export_fingerprint: str,
    route_control_fingerprint: str | None,
) -> Mapping[str, object]:
    return {
        "contract": "satn-planning-graph-snapshot/v1",
        "profile_fingerprint": profile_fingerprint,
        "source_export_fingerprint": source_export_fingerprint,
        "route_control_fingerprint": route_control_fingerprint,
        "edges": [
            {
                "source_edge_id": item.source_edge_id,
                "directed_edge_id": item.directed_edge_id,
                "from": item.from_node_id,
                "to": item.to_node_id,
                "geometry_fingerprint": item.geometry_fingerprint,
                "length_mm": item.length_mm,
                "highway": item.highway,
                "ref": item.ref,
                "access": item.access,
                "bicycle": item.bicycle,
                "foot": item.foot,
                "oneway": item.oneway,
                "reciprocal_state": item.reciprocal_state,
                "weak_component_id": item.weak_component_id,
                "strong_component_id": item.strong_component_id,
                "access_observation_ids": item.access_observation_ids,
                "asset_observation_ids": item.asset_observation_ids,
                "road_observation_ids": item.road_observation_ids,
                "claim_observation_ids": item.claim_observation_ids,
                "unknown_claims": item.unknown_claims,
            }
            for item in edges
        ],
        "nodes": [item.__dict__ for item in nodes],
        "components": [item.__dict__ for item in components],
        "bindings": [item.__dict__ for item in bindings],
        "diagnostics": [item.__dict__ for item in diagnostics],
    }


def _empty_snapshot(
    source: SourceExportFrame,
    profile: PlanningGraphProfile,
    route_controls: RouteControlSet | None,
    diagnostic: GraphDiagnostic,
) -> PlanningGraphSnapshot:
    diagnostics = (diagnostic,)
    payload = _snapshot_payload(
        (),
        (),
        (),
        (),
        diagnostics,
        profile.fingerprint,
        source.source_export_fingerprint,
        _route_control_fingerprint(route_controls),
    )
    return PlanningGraphSnapshot(
        graph_fingerprint=evidence_fingerprint(payload),
        edge_records=(),
        node_records=(),
        component_records=(),
        observation_matches=(),
        diagnostics=diagnostics,
        profile_fingerprint=profile.fingerprint,
        source_export_fingerprint=source.source_export_fingerprint,
        route_control_fingerprint=_route_control_fingerprint(route_controls),
    )


def _route_control_fingerprint(route_controls: RouteControlSet | None) -> str | None:
    return route_controls.control_fingerprint if route_controls is not None else None
