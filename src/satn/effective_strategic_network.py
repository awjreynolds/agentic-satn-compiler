"""Canonical state boundary for the effective strategic network.

The strategic planner remains the only selector.  This module gives callers one
stable state to consume: an evaluated planning result when all governed inputs
are present, or an explicit unavailable state when the governed identity cannot
be established.  Compatibility properties delegate to the stored result and
never invoke planning a second time.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from itertools import pairwise
from typing import TYPE_CHECKING, Any

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.errors import ShapelyError
from shapely.geometry import LineString, Point
from shapely.ops import substring

from satn.alignment_selection import AlignmentCandidateInput, admit_candidate_set
from satn.candidate_discovery import (
    AssessedCandidateRecord,
    CandidateDiscoveryResult,
    CandidateReviewSection,
    CandidateSearchDiagnostic,
    CandidateSetGapEvidence,
    CorridorObligationDisposition,
    EvidenceRequest,
)
from satn.content_identity import canonical_network_geometry_fingerprint
from satn.network_selection import InterventionState, ReuseFirstCandidateClass
from satn.planning_graph import (
    GraphComponentRecord,
    GraphDiagnostic,
    PlanningEdgeRecord,
    PlanningGraphProfile,
    PlanningGraphSnapshot,
    PlanningNodeRecord,
)
from satn.routing import (
    RoadGraph,
    _coordinate_id,
    _directed_edge_identity,
    _present,
    _source_edge_id,
    _truthy,
)
from satn.strategic_corridors import StrategicCorridorPreparationResult
from satn.strategic_mesh import (
    StrategicMainNetworkProfile,
)
from satn.urban import URBAN_SPINE_TERMINUS_TOLERANCE_M

if TYPE_CHECKING:
    from satn.strategic_network_planning import (
        EffectiveReviewableSelection,
        EffectiveStrategicNetwork,
        StrategicNetworkPlanningRequest,
        StrategicNetworkPlanningResult,
        StrategicPlanningLineage,
    )


class EffectiveStrategicNetworkStatus(StrEnum):
    """Lifecycle of the canonical effective-network state."""

    EVALUATED = "evaluated"
    UNAVAILABLE = "unavailable"


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


@dataclass(frozen=True)
class EffectiveStrategicNetworkRequest:
    """Complete governed input packet for one effective-network evaluation."""

    routable_network: gpd.GeoDataFrame | PlanningGraphSnapshot | None = None
    preparation: object | None = None
    area_fingerprint: str | None = None
    snapshot_fingerprint: str | None = None
    officer_decisions: tuple[object, ...] = ()
    urban_spines: gpd.GeoDataFrame | None = None
    access_support: tuple[gpd.GeoDataFrame, ...] = ()
    mesh_profile: StrategicMainNetworkProfile = field(default_factory=StrategicMainNetworkProfile)

    def __post_init__(self) -> None:
        if self.routable_network is not None and not isinstance(
            self.routable_network, (gpd.GeoDataFrame, PlanningGraphSnapshot)
        ):
            raise ValueError("effective strategic request requires a routable snapshot")
        if self.area_fingerprint is not None and not _is_sha256(self.area_fingerprint):
            raise ValueError("effective strategic request area fingerprint must be SHA-256")
        if self.snapshot_fingerprint is not None and not _is_sha256(self.snapshot_fingerprint):
            raise ValueError("effective strategic request snapshot fingerprint must be SHA-256")
        if self.urban_spines is not None:
            if not isinstance(self.urban_spines, gpd.GeoDataFrame):
                raise ValueError("effective strategic request urban spines must be a GeoDataFrame")
            if self.urban_spines.crs is None:
                raise ValueError("effective strategic request urban spines require an explicit CRS")
        access_support = tuple(self.access_support)
        if any(not isinstance(frame, gpd.GeoDataFrame) for frame in access_support):
            raise ValueError("effective strategic request access support must be GeoDataFrames")
        if any(frame.crs is None for frame in access_support):
            raise ValueError("effective strategic request access support requires explicit CRS")
        if not isinstance(self.mesh_profile, StrategicMainNetworkProfile):
            raise ValueError(
                "effective strategic request mesh profile must be a StrategicMainNetworkProfile"
            )
        object.__setattr__(self, "officer_decisions", tuple(self.officer_decisions))
        object.__setattr__(self, "access_support", access_support)

    @property
    def governed_identity_complete(self) -> bool:
        return (
            self.routable_network is not None
            and self.preparation is not None
            and _is_sha256(self.area_fingerprint)
            and _is_sha256(self.snapshot_fingerprint)
            and _is_sha256(getattr(self.preparation, "preparation_fingerprint", None))
            and _is_sha256(getattr(self.preparation, "profile_fingerprint", None))
        )


def _canonical(value: object) -> object:
    if hasattr(value, "model_dump"):
        return _canonical(value.model_dump(mode="json"))
    if hasattr(value, "canonical"):
        return _canonical(value.canonical())
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _canonical(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _component_id(kind: str, nodes: Iterable[str]) -> str:
    return f"{kind}-{_fingerprint((kind, tuple(sorted(nodes))))[:20]}"


def planning_graph_from_compiler_edges(
    routable_network: gpd.GeoDataFrame,
    *,
    source_export_fingerprint: str,
) -> PlanningGraphSnapshot:
    """Build a lossless planning graph while retaining compiler edge IDs."""

    if not source_export_fingerprint or len(source_export_fingerprint) != 64:
        raise ValueError("strategic planning requires the snapshot manifest SHA-256")
    if routable_network.crs is None:
        raise ValueError("strategic planning requires routable network CRS")
    projected = routable_network.to_crs(27700)
    drafts: list[dict[str, object]] = []
    diagnostics: list[GraphDiagnostic] = []
    seen: set[str] = set()
    directed = nx.MultiDiGraph()
    source_rows: list[tuple[object, object, object, str, str, str]] = []
    for index, row in projected.iterrows():
        geometry = row.geometry
        source_edge_id = _source_edge_id(row, index)
        if not isinstance(geometry, LineString) or len(geometry.coords) < 2:
            diagnostics.append(
                GraphDiagnostic(
                    "invalid-compiler-edge", source_edge_id, "source edge is not a line"
                )
            )
            continue
        source_row = routable_network.loc[index]
        source_geometry = source_row.geometry
        start = (
            str(source_row.get("u"))
            if _present(source_row.get("u"))
            else _coordinate_id(tuple(source_geometry.coords[0]))
        )
        end = (
            str(source_row.get("v"))
            if _present(source_row.get("v"))
            else _coordinate_id(tuple(source_geometry.coords[-1]))
        )
        source_rows.append((index, source_row, geometry, source_edge_id, start, end))
    source_id_counts: dict[str, int] = {}
    for _index, _source_row, _geometry, source_edge_id, _start, _end in source_rows:
        source_id_counts[source_edge_id] = source_id_counts.get(source_edge_id, 0) + 1
    for _index, source_row, geometry, source_edge_id, start, end in source_rows:
        edge_id = _directed_edge_identity(
            source_edge_id,
            start,
            end,
            geometry,
            duplicate_source_id=source_id_counts[source_edge_id] > 1,
            crs="EPSG:27700",
        )
        if edge_id in seen:
            diagnostics.append(
                GraphDiagnostic(
                    "duplicate-compiler-edge-id",
                    source_edge_id,
                    "duplicate directed source identity was retained once",
                )
            )
            continue
        seen.add(edge_id)
        directed.add_edge(start, end, edge_id=edge_id)
        drafts.append(
            {
                "edge_id": edge_id,
                "source_edge_id": source_edge_id,
                "start": start,
                "end": end,
                "geometry": geometry,
                "highway": _scalar(source_row.get("highway")),
                "ref": _scalar(source_row.get("ref")),
                "access": _scalar(source_row.get("access")),
                "bicycle": _scalar(source_row.get("bicycle")),
                "foot": _scalar(source_row.get("foot")),
                "oneway": _truthy(source_row.get("oneway"))
                if _present(source_row.get("oneway"))
                else None,
            }
        )
    weak_by_node: dict[str, str] = {}
    weak_components: list[GraphComponentRecord] = []
    for nodes in sorted(
        nx.weakly_connected_components(directed), key=lambda item: tuple(sorted(item))
    ):
        component_id = _component_id("weak", nodes)
        weak_by_node.update({str(node): component_id for node in nodes})
        edge_ids = tuple(
            sorted(
                str(data["edge_id"])
                for left, right, data in directed.edges(data=True)
                if left in nodes and right in nodes
            )
        )
        weak_components.append(
            GraphComponentRecord(
                component_id, "weak", tuple(sorted(nodes)), edge_ids, len(nodes), len(edge_ids)
            )
        )
    strong_by_node: dict[str, str] = {}
    strong_components: list[GraphComponentRecord] = []
    for nodes in sorted(
        nx.strongly_connected_components(directed), key=lambda item: tuple(sorted(item))
    ):
        component_id = _component_id("strong", nodes)
        strong_by_node.update({str(node): component_id for node in nodes})
        edge_ids = tuple(
            sorted(
                str(data["edge_id"])
                for left, right, data in directed.edges(data=True)
                if left in nodes and right in nodes
            )
        )
        strong_components.append(
            GraphComponentRecord(
                component_id, "strong", tuple(sorted(nodes)), edge_ids, len(nodes), len(edge_ids)
            )
        )
    records = tuple(
        PlanningEdgeRecord(
            source_edge_id=str(item["source_edge_id"]),
            directed_edge_id=str(item["edge_id"]),
            from_node_id=str(item["start"]),
            to_node_id=str(item["end"]),
            geometry_wkt=item["geometry"].wkt,
            geometry_fingerprint=canonical_network_geometry_fingerprint(
                item["geometry"], "EPSG:27700"
            ),
            length_mm=round(float(item["geometry"].length) * 1_000),
            highway=item["highway"],
            ref=item["ref"],
            access=item["access"],
            bicycle=item["bicycle"],
            foot=item["foot"],
            oneway=item["oneway"],
            reciprocal_state=(
                "reciprocal"
                if directed.has_edge(str(item["end"]), str(item["start"]))
                else "one-way"
                if item["oneway"] is True
                else "unknown"
            ),
            weak_component_id=weak_by_node[str(item["start"])],
            strong_component_id=strong_by_node[str(item["start"])],
        )
        for item in sorted(drafts, key=lambda row: str(row["edge_id"]))
    )
    profile = PlanningGraphProfile(canonical_crs="EPSG:27700")
    return PlanningGraphSnapshot(
        graph_fingerprint=_fingerprint(
            tuple(
                (
                    item.directed_edge_id,
                    item.from_node_id,
                    item.to_node_id,
                    item.geometry_fingerprint,
                )
                for item in records
            )
        ),
        edge_records=records,
        node_records=tuple(
            PlanningNodeRecord(node, weak_by_node[node], strong_by_node[node])
            for node in sorted(directed.nodes)
        ),
        component_records=tuple((*weak_components, *strong_components)),
        observation_matches=(),
        diagnostics=tuple(diagnostics),
        profile_fingerprint=profile.fingerprint,
        source_export_fingerprint=source_export_fingerprint,
        route_control_fingerprint=None,
    )


def _route_geometry(graph: PlanningGraphSnapshot, edge_ids: tuple[str, ...]) -> LineString:
    by_id = {item.directed_edge_id: item for item in graph.edge_records}
    coordinates: list[tuple[float, float]] = []
    for edge_id in edge_ids:
        record = by_id[edge_id]
        geometry = LineString(
            tuple(
                tuple(float(value) for value in coordinate)
                for coordinate in _wkt_coords(record.geometry_wkt)
            )
        )
        points = list(geometry.coords)
        if not coordinates:
            coordinates.extend(points)
        elif coordinates[-1] == points[0]:
            coordinates.extend(points[1:])
        else:
            raise ValueError("prepared routing edge geometry is not contiguous")
    return LineString(coordinates)


def _wkt_coords(value: str) -> tuple[tuple[float, float], ...]:
    from shapely.wkt import loads

    geometry = loads(value)
    if not isinstance(geometry, LineString):
        raise ValueError("prepared routing edge is not a line")
    return tuple((float(x), float(y)) for x, y in geometry.coords)


def _facts(candidate: object, graph: PlanningGraphSnapshot, edge_ids: tuple[str, ...]):
    by_id = {item.directed_edge_id: item for item in graph.edge_records}
    edges = tuple(by_id[item] for item in edge_ids)
    highways = {str(item.highway or "").lower() for item in edges}
    refs = {str(item.ref or "").upper() for item in edges}
    ncn = any("ncn" in basis for basis in getattr(candidate, "alignment_bases", ()))
    explicit_reuse = getattr(candidate, "reuse_class", None)
    explicit_intervention = getattr(candidate, "intervention_state", None)
    explicit_bases = tuple(getattr(candidate, "alignment_bases", ()))
    source_class = str(getattr(getattr(candidate, "source_class", None), "value", ""))
    cycleway_highways = {"cycleway", "path", "footway", "track", "pedestrian", "bridleway"}
    cycleway_only = bool(highways) and highways.issubset(cycleway_highways)
    if explicit_bases:
        bases = explicit_bases
    elif ncn or (source_class == "verified-existing-asset" and cycleway_only):
        bases = ("current-ncn",)
    elif cycleway_only:
        bases = ("mapped-cycleway",)
    elif source_class == "verified-existing-asset":
        bases = ("proposed-new-corridor",)
    elif highways & {"path", "footway", "track"}:
        bases = ("prow-class-unknown",)
    elif any(value.startswith("A") for value in refs) or source_class == "a-road-corridor":
        bases = ("a-road",)
    elif any(value.startswith("B") for value in refs) or source_class == "b-road-corridor":
        bases = ("b-road",)
    elif highways & {"residential", "unclassified", "service", "living_street"}:
        bases = ("local-connector",)
    else:
        bases = ("proposed-new-corridor",)
    if explicit_reuse is not None:
        reuse = explicit_reuse
    elif cycleway_only or (source_class == "verified-existing-asset" and not highways):
        reuse = ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION
    elif highways & {"path", "footway", "track"}:
        reuse = ReuseFirstCandidateClass.UPGRADEABLE_OFF_CARRIAGEWAY
    elif "a-road" in bases or "b-road" in bases:
        reuse = ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE
    elif "local-connector" in bases:
        reuse = ReuseFirstCandidateClass.LOW_TRAFFIC_NON_A_ROAD
    else:
        reuse = ReuseFirstCandidateClass.UNKNOWN_OR_CONFLICTING
    if explicit_intervention is not None:
        intervention = explicit_intervention
    elif reuse is ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION:
        intervention = InterventionState.EXISTING_PROVISION
    elif reuse in {
        ReuseFirstCandidateClass.UPGRADEABLE_OFF_CARRIAGEWAY,
        ReuseFirstCandidateClass.LOW_TRAFFIC_NON_A_ROAD,
    }:
        intervention = InterventionState.UPGRADE_REQUIRED
    elif reuse is ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE:
        intervention = InterventionState.PROPOSED_NEW_LINK
    else:
        intervention = InterventionState.UNDETERMINED
    return reuse, intervention, tuple(sorted(bases)), bases[0]


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _canonical(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class _UrbanAttachmentDiagnostics:
    """Planning diagnostics produced while materialising urban road spines."""

    gaps: tuple[object, ...] = ()
    diagnostics: tuple[object, ...] = ()
    fingerprint: str = ""


def _scalar(value: object) -> str | None:
    if not _present(value):
        return None
    if isinstance(value, (tuple, list, set)):
        values = sorted(str(item) for item in value if _present(item))
        return values[0] if values else None
    return str(value)


def _rebuild_candidate_set(candidate_set, candidates):
    """Re-admit only candidates whose prepared route materialised cleanly."""

    return admit_candidate_set(
        candidate_set.profile,
        network_role=candidate_set.network_role,
        endpoints=tuple(candidate_set.endpoints),
        candidates=tuple(candidates),
        mandatory_network_place_ids=tuple(candidate_set.mandatory_network_place_ids),
        mandatory_access_obligation_ids=tuple(candidate_set.mandatory_access_obligation_ids),
        mandatory_strategic_destination_ids=tuple(
            candidate_set.mandatory_strategic_destination_ids
        ),
    )


def discovery_from_preparation(
    preparation: StrategicCorridorPreparationResult,
    graph: PlanningGraphSnapshot,
) -> CandidateDiscoveryResult:
    records: list[AssessedCandidateRecord] = []
    dispositions: list[CorridorObligationDisposition] = []
    diagnostics: list[CandidateSearchDiagnostic] = []
    gaps: list[CandidateSetGapEvidence] = []
    requests: list[EvidenceRequest] = []
    candidate_sets = []
    for unit in preparation.units:
        valid_candidates = []
        for prepared in unit.candidate_records:
            candidate = prepared.candidate
            try:
                geometry = _route_geometry(graph, prepared.routing_edge_ids)
            except (KeyError, ShapelyError, TypeError, ValueError) as error:
                reason = f"prepared candidate route is unusable: {type(error).__name__}: {error}"
                diagnostic_payload = (
                    unit.unit_id,
                    candidate.candidate_id,
                    prepared.routing_edge_ids,
                    reason,
                )
                diagnostic_id = f"strategic-prepared-route-{_fingerprint(diagnostic_payload)[:20]}"
                diagnostics.append(
                    CandidateSearchDiagnostic(
                        code="malformed-prepared-route",
                        obligation_id=unit.unit_id,
                        message=reason,
                        candidate_id=candidate.candidate_id,
                        edge_ids=tuple(prepared.routing_edge_ids),
                    )
                )
                gaps.append(
                    CandidateSetGapEvidence(
                        obligation_id=unit.unit_id,
                        endpoints=tuple(unit.candidate_set.endpoints),
                        reason=reason,
                        search_diagnostic_ids=(diagnostic_id,),
                    )
                )
                requests.append(
                    EvidenceRequest(
                        request_id=(
                            "evidence-request-"
                            f"{_fingerprint((unit.unit_id, candidate.candidate_id, reason))[:20]}"
                        ),
                        obligation_id=unit.unit_id,
                        claim="route-continuity",
                        reason=reason,
                        candidate_id=candidate.candidate_id,
                    )
                )
                continue
            reuse, intervention, bases, primary = _facts(
                candidate, graph, prepared.routing_edge_ids
            )
            candidate = AlignmentCandidateInput.model_validate(
                {
                    **candidate.model_dump(mode="python", exclude={"candidate_id"}),
                    "reuse_class": reuse,
                    "intervention_state": intervention,
                    "alignment_bases": bases,
                    "primary_alignment_basis": primary,
                }
            )
            evidence_ids = tuple(sorted(set((*prepared.evidence_ids, *prepared.source_ids))))
            section = CandidateReviewSection(
                section_id=f"section-{candidate.candidate_id}",
                candidate_id=candidate.candidate_id,
                edge_ids=prepared.routing_edge_ids,
                geometry_wkt=geometry.wkt,
                length_m=float(geometry.length),
                reuse_class=reuse,
                intervention_state=intervention,
                alignment_bases=bases,
                primary_alignment_basis=primary,
                evidence_ids=evidence_ids,
                evidence_snapshot_fingerprint=graph.source_export_fingerprint,
                network_scope=getattr(unit, "network_scope", None),
                total_absolute_elevation_change_m=getattr(
                    candidate, "total_absolute_elevation_change_m", None
                ),
            )
            records.append(
                AssessedCandidateRecord(
                    candidate_id=candidate.candidate_id,
                    obligation_id=unit.unit_id,
                    endpoints=unit.candidate_set.endpoints,
                    edge_ids=prepared.routing_edge_ids,
                    reverse_edge_ids=prepared.reverse_routing_edge_ids,
                    geometry_wkt=geometry.wkt,
                    length_m=float(geometry.length),
                    directness_m=float(candidate.directness_m),
                    reuse_class=reuse,
                    intervention_state=intervention,
                    alignment_bases=bases,
                    primary_alignment_basis=primary,
                    sections=(section,),
                    generating_strategy_ids=prepared.generation_strategies,
                    total_absolute_elevation_change_m=getattr(
                        candidate, "total_absolute_elevation_change_m", None
                    ),
                    transition_count=getattr(candidate, "transition_count", None) or 0,
                    fragmentation_count=getattr(candidate, "fragmentation_count", None) or 0,
                    evidence_ids=evidence_ids,
                    network_role=unit.unit_role.network_role.value
                    if hasattr(unit.unit_role, "network_role")
                    else unit.unit_role.value,
                    evidence_snapshot_fingerprint=graph.source_export_fingerprint,
                    edge_evidence_fingerprint=preparation.preparation_fingerprint,
                    candidate_input=candidate,
                )
            )
            valid_candidates.append(candidate)
        rebuilt_set = _rebuild_candidate_set(unit.candidate_set, tuple(valid_candidates))
        candidate_sets.append(rebuilt_set)
        dispositions.append(
            CorridorObligationDisposition(
                unit.unit_id,
                "candidates" if rebuilt_set.admitted_candidates else "gap",
                rebuilt_set.candidate_set_id,
                "prepared strategic corridor candidates retained"
                if rebuilt_set.admitted_candidates
                else "all prepared strategic corridor candidates were unusable",
            )
        )
    for index, issue in enumerate(preparation.issues):
        obligation_id = (
            getattr(issue, "obligation_id", None)
            or issue.strategic_destination_id
            or issue.site_id
            or f"issue-{index + 1}"
        )
        issue_endpoints = tuple(getattr(issue, "endpoints", ("", "")))
        endpoints = (
            issue_endpoints
            if len(issue_endpoints) == 2 and all(issue_endpoints)
            else ("unresolved", obligation_id)
        )
        diagnostic_id = f"strategic-preparation-{_fingerprint(issue.canonical())[:20]}"
        diagnostics.append(
            CandidateSearchDiagnostic(
                code=issue.reason,
                obligation_id=obligation_id,
                message=issue.detail,
            )
        )
        gaps.append(
            CandidateSetGapEvidence(
                obligation_id=obligation_id,
                endpoints=endpoints,
                reason=issue.detail,
                search_diagnostic_ids=(diagnostic_id,),
            )
        )
        requests.append(
            EvidenceRequest(
                request_id=f"evidence-request-{_fingerprint((obligation_id, issue.reason))[:20]}",
                obligation_id=obligation_id,
                claim=issue.reason,
                reason=issue.detail,
            )
        )
    payload = {
        "preparation": preparation.preparation_fingerprint,
        "graph": graph.graph_fingerprint,
        "candidate_sets": tuple(item.candidate_set_id for item in candidate_sets),
        "records": tuple(item.candidate_id for item in records),
        "gaps": gaps,
    }
    return CandidateDiscoveryResult(
        candidate_sets=tuple(candidate_sets),
        candidate_records=tuple(sorted(records, key=lambda item: item.candidate_id)),
        obligation_dispositions=tuple(sorted(dispositions, key=lambda item: item.obligation_id)),
        search_diagnostics=tuple(diagnostics),
        evidence_requests=tuple(requests),
        fingerprint=_fingerprint(payload),
        status="complete" if not gaps else "complete-with-gaps",
        gaps=tuple(gaps),
        evidence_snapshot_fingerprint=graph.source_export_fingerprint,
        edge_evidence_fingerprint=preparation.preparation_fingerprint,
        selection_profile_fingerprint=preparation.profile_fingerprint,
    )


def _compiler_preferences(preparation: StrategicCorridorPreparationResult, candidate_sets=()):
    preferences: list[tuple[str, str]] = []
    sets = tuple(candidate_sets) or tuple(unit.candidate_set for unit in preparation.units)
    for candidate_set in sets:
        admitted = tuple(candidate_set.admitted_candidates)
        if not admitted or candidate_set.profile.contract == "satn-network-selection-profile/vNext":
            continue
        precedence = {
            source: index for index, source in enumerate(candidate_set.candidate_source_precedence)
        }
        preferred = min(
            admitted,
            key=lambda item: (
                precedence.get(item.source_class, len(precedence)),
                item.directness_m,
                item.candidate_id,
            ),
        )
        preferences.append((candidate_set.candidate_set_id, preferred.candidate_id))
    return tuple(sorted(preferences))


def _officer_choices(
    preparation: StrategicCorridorPreparationResult,
    decisions: tuple[object, ...],
    candidate_sets=(),
):
    choices: list[tuple[str, str]] = []
    sets = tuple(candidate_sets) or tuple(unit.candidate_set for unit in preparation.units)
    for decision in decisions:
        target_id = getattr(decision, "target_id", None)
        route_id = getattr(decision, "route_id", None)
        for unit, candidate_set in zip(preparation.units, sets, strict=True):
            aliases = {
                unit.unit_id,
                candidate_set.candidate_set_id,
                candidate_set.connection_id,
                *unit.anchor_connection_ids,
                *unit.anchor_obligation_ids,
            }
            if target_id not in aliases:
                continue
            candidates_by_geometry = {
                item.geometry_fingerprint: item.candidate_id for item in candidate_set.candidates
            }
            matches = [
                candidates_by_geometry.get(record.candidate.geometry_fingerprint)
                for record in unit.candidate_records
                if route_id in {record.candidate.candidate_id, record.physical_alignment_id}
                and record.candidate.geometry_fingerprint in candidates_by_geometry
            ]
            matches = [item for item in matches if item is not None]
            if len(matches) == 1:
                choices.append((matches[0], f"preloaded-officer:{target_id}:{route_id}"))
    return tuple(sorted(choices))


@dataclass(frozen=True)
class EffectiveStrategicNetworkState:
    """One immutable canonical result or an explicit governed-input gap."""

    status: EffectiveStrategicNetworkStatus
    result: StrategicNetworkPlanningResult | None = None
    reason: str | None = None
    fingerprint: str = ""

    def __post_init__(self) -> None:
        status = EffectiveStrategicNetworkStatus(self.status)
        object.__setattr__(self, "status", status)
        if status is EffectiveStrategicNetworkStatus.EVALUATED:
            if self.result is None:
                raise ValueError("evaluated effective strategic state requires a planning result")
            if self.reason is not None:
                raise ValueError(
                    "evaluated effective strategic state cannot have an unavailable reason"
                )
            fingerprint = self.result.fingerprint
        else:
            if self.result is not None:
                raise ValueError(
                    "unavailable effective strategic state cannot have a planning result"
                )
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("unavailable effective strategic state requires a reason")
            fingerprint = _fingerprint({"status": status.value, "reason": self.reason})
        if self.fingerprint and self.fingerprint != fingerprint:
            raise ValueError("effective strategic state fingerprint is stale")
        object.__setattr__(self, "fingerprint", fingerprint)

    @classmethod
    def evaluated(cls, result: StrategicNetworkPlanningResult) -> EffectiveStrategicNetworkState:
        return cls(EffectiveStrategicNetworkStatus.EVALUATED, result=result)

    @classmethod
    def unavailable(
        cls, reason: str = "governed-identity-unavailable"
    ) -> EffectiveStrategicNetworkState:
        return cls(EffectiveStrategicNetworkStatus.UNAVAILABLE, reason=reason)

    @property
    def is_evaluated(self) -> bool:
        return self.status is EffectiveStrategicNetworkStatus.EVALUATED

    def _result_value(self, name: str, default: Any = ()) -> Any:
        return getattr(self.result, name, default) if self.result is not None else default

    @property
    def effective_network(self) -> EffectiveStrategicNetwork | None:
        return self._result_value("effective_network", None)

    @property
    def selections(self) -> tuple[EffectiveReviewableSelection, ...]:
        return self._result_value("selections")

    @property
    def candidate_sets(self) -> tuple[object, ...]:
        return self._result_value("candidate_sets")

    @property
    def reference_routes(self) -> tuple[object, ...]:
        return self._result_value("reference_routes")

    @property
    def unselected_candidates(self) -> tuple[object, ...]:
        return self._result_value("unselected_candidates")

    @property
    def gaps(self) -> tuple[object, ...]:
        return self._result_value("gaps")

    @property
    def divergences(self) -> tuple[object, ...]:
        return self._result_value("divergences")

    @property
    def evidence_requests(self) -> tuple[object, ...]:
        return self._result_value("evidence_requests")

    @property
    def diagnostics(self) -> tuple[object, ...]:
        return self._result_value("diagnostics")

    @property
    def lineage(self) -> StrategicPlanningLineage | None:
        return self._result_value("lineage", None)

    def canonical(self) -> dict[str, object]:
        """Return a compact state identity without duplicating selection data."""

        return {
            "status": self.status.value,
            "reason": self.reason,
            "planning_result_fingerprint": self.result.fingerprint if self.result else None,
            "fingerprint": self.fingerprint,
        }


def _evaluate_planning_request(
    request: StrategicNetworkPlanningRequest | None,
    *,
    result: StrategicNetworkPlanningResult | None = None,
) -> EffectiveStrategicNetworkState:
    """Evaluate one request, or make governed identity absence explicit.

    ``result`` is an internal compatibility escape hatch for callers that have
    already run the authority planner.  Supplying it avoids a second selector;
    when omitted, this function invokes :func:`compile_strategic_network` once.
    """

    if result is not None:
        return EffectiveStrategicNetworkState.evaluated(result)
    if request is None:
        return EffectiveStrategicNetworkState.unavailable()
    from satn.strategic_network_planning import compile_strategic_network

    return EffectiveStrategicNetworkState.evaluated(compile_strategic_network(request))


def _planning_graph_with_urban_spines(
    routable_network: gpd.GeoDataFrame,
    urban_spines: gpd.GeoDataFrame | None,
    *,
    source_export_fingerprint: str,
):
    from satn.strategic_network_planning import (
        EffectiveStrategicSection,
        PlanningAuthority,
    )

    if urban_spines is None or urban_spines.empty:
        return (
            planning_graph_from_compiler_edges(
                routable_network,
                source_export_fingerprint=source_export_fingerprint,
            ),
            (),
        )

    urban_for_graph = urban_spines.to_crs(routable_network.crs)
    urban_projected = urban_spines.to_crs(27700)
    routable_projected = routable_network.to_crs(27700)
    road_graph = RoadGraph(routable_network)

    # ``RoadGraph.nodes_near`` intentionally only knows about source vertices.
    # An urban spine can, however, terminate on the interior of a source edge.
    # Materialise deterministic split overlays for those exact on-edge points so
    # the urban node belongs to the same weak component as the source topology.
    # Keep the original source edge too: prepared candidate routes retain their
    # source edge identities while the overlay supplies the attachment topology.
    urban_endpoint_points: dict[str, Point] = {}
    for row in urban_projected.sort_values("structure_id").itertuples():
        geometry = row.geometry
        if isinstance(geometry, LineString) and not geometry.is_empty:
            for coordinate in (geometry.coords[0], geometry.coords[-1]):
                point = Point(coordinate)
                urban_endpoint_points.setdefault(_coordinate_id(coordinate), point)

    source_endpoint_nodes: dict[str, str] = {}
    interior_nodes: set[str] = set()
    attachment_rows: list[dict[str, object]] = []
    source_interiors: dict[int, list[tuple[float, str, Point]]] = {}
    source_index = routable_projected.geometry.sindex
    for point_id, point in urban_endpoint_points.items():
        for position in source_index.query(
            point.buffer(URBAN_SPINE_TERMINUS_TOLERANCE_M), predicate="intersects"
        ):
            source_position = int(position)
            geometry = routable_projected.geometry.iloc[source_position]
            if not isinstance(geometry, LineString):
                continue
            if geometry.distance(point) > URBAN_SPINE_TERMINUS_TOLERANCE_M:
                continue
            distance_along = float(geometry.project(point))
            if distance_along <= 0.0 or distance_along >= float(geometry.length):
                continue
            source_interiors.setdefault(source_position, []).append(
                (distance_along, point_id, point)
            )
            interior_nodes.add(point_id)

    for position, (index, row) in enumerate(routable_projected.iterrows()):
        geometry = row.geometry
        if not isinstance(geometry, LineString) or len(geometry.coords) < 2:
            continue
        source_edge_id = _source_edge_id(row, index)
        start_node = (
            str(row.get("u"))
            if _present(row.get("u"))
            else _coordinate_id(tuple(geometry.coords[0]))
        )
        end_node = (
            str(row.get("v"))
            if _present(row.get("v"))
            else _coordinate_id(tuple(geometry.coords[-1]))
        )
        source_endpoint_nodes.setdefault(_coordinate_id(geometry.coords[0]), start_node)
        source_endpoint_nodes.setdefault(_coordinate_id(geometry.coords[-1]), end_node)
        interior = source_interiors.get(position, [])
        if not interior:
            continue
        ordered_points = sorted(interior, key=lambda item: (item[0], item[1]))
        boundaries = (
            [(0.0, start_node)]
            + [(distance_along, point_id) for distance_along, point_id, _point in ordered_points]
            + [(float(geometry.length), end_node)]
        )
        for segment_index, ((start, from_node), (end, to_node)) in enumerate(pairwise(boundaries)):
            segment = substring(geometry, start, end)
            if not isinstance(segment, LineString) or segment.is_empty:
                continue
            segment_row = row.to_dict()
            segment_row["u"] = from_node
            segment_row["v"] = to_node
            segment_row["geometry"] = (
                gpd.GeoSeries([segment], crs=27700).to_crs(routable_network.crs).iloc[0]
            )
            segment_row["source_id"] = (
                f"urban-attachment:{source_edge_id}:{index!s}:{segment_index}"
            )
            segment_row["osmid"] = None
            attachment_rows.append(segment_row)

    attachment_node_by_coordinate = {point_id: point_id for point_id in interior_nodes}

    def urban_node(point: Point) -> str:
        projected_point = gpd.GeoSeries([point], crs=routable_network.crs).to_crs(27700).iloc[0]
        point_id = _coordinate_id(tuple(projected_point.coords[0]))
        if point_id in attachment_node_by_coordinate:
            return attachment_node_by_coordinate[point_id]
        if point_id in source_endpoint_nodes:
            return source_endpoint_nodes[point_id]
        matches = road_graph.nodes_near(point, URBAN_SPINE_TERMINUS_TOLERANCE_M)
        return matches[0][0] if matches else point_id

    combined = routable_network.copy()
    if "osmid" in combined.columns:
        source_key = "osmid"
    elif "source_id" in combined.columns:
        source_key = "source_id"
    elif "edge_id" in combined.columns:
        source_key = "edge_id"
    else:
        source_key = "source_id"
        combined[source_key] = combined.index.map(str)

    highway_by_classification = {
        "a-road": "primary",
        "b-road": "secondary",
        "classified-unnumbered": "tertiary",
    }
    urban_rows: list[dict[str, object]] = []
    classification_by_id: dict[str, str] = {}
    for _, row in urban_for_graph.sort_values("structure_id").iterrows():
        geometry = row.geometry
        if not isinstance(geometry, LineString) or geometry.is_empty:
            raise ValueError("urban strategic spine geometry must be a non-empty line")
        section_id = str(row["structure_id"])
        classification = str(row["official_classification"])
        if classification not in highway_by_classification:
            raise ValueError(f"unsupported urban strategic classification: {classification}")
        if section_id in classification_by_id:
            raise ValueError(f"duplicate urban strategic section ID: {section_id}")
        classification_by_id[section_id] = classification
        urban_rows.append(
            {
                source_key: section_id,
                "u": urban_node(Point(geometry.coords[0])),
                "v": urban_node(Point(geometry.coords[-1])),
                "highway": highway_by_classification[classification],
                "oneway": False,
                "geometry": geometry,
            }
        )
    urban_edges = gpd.GeoDataFrame(
        [*attachment_rows, *urban_rows],
        geometry="geometry",
        crs=routable_network.crs,
    )
    combined = gpd.GeoDataFrame(
        pd.concat([combined, urban_edges], ignore_index=True, sort=False),
        geometry="geometry",
        crs=routable_network.crs,
    )
    graph = planning_graph_from_compiler_edges(
        combined,
        source_export_fingerprint=source_export_fingerprint,
    )
    edge_by_source = {
        edge.source_edge_id: edge
        for edge in graph.edge_records
        if edge.source_edge_id in classification_by_id
    }
    if set(edge_by_source) != set(classification_by_id):
        missing = sorted(set(classification_by_id) - set(edge_by_source))
        raise ValueError(f"urban strategic section has no exact planning edge: {missing[0]}")
    basis_by_classification = {
        "a-road": "a-road",
        "b-road": "b-road",
        "classified-unnumbered": "classified-unnumbered-road",
    }
    # B-road and classified-unnumbered rows remain in ``combined`` above so
    # they are available as routable graph context.  They are not authoritative
    # Main sections just because the source inventory supplied them as urban
    # spines; a later continuity choice must establish an interurban connection
    # first.
    required_sections = tuple(
        EffectiveStrategicSection(
            section_id=section_id,
            obligation_id=f"urban-structure:{section_id}",
            candidate_id=None,
            network_role="urban-main-road-spine",
            routing_edge_ids=(edge_by_source[section_id].directed_edge_id,),
            reverse_routing_edge_ids=(),
            geometry_wkt=edge_by_source[section_id].geometry_wkt,
            authority=PlanningAuthority.COMPILER,
            alignment_bases=(basis_by_classification[classification_by_id[section_id]],),
            primary_alignment_basis=basis_by_classification[classification_by_id[section_id]],
            intervention_state="upgrade-required",
            display_state="upgrade-required",
            network_scope="urban",
        )
        for section_id in sorted(classification_by_id)
        if classification_by_id[section_id] == "a-road"
    )
    return graph, required_sections


def _urban_attachment_diagnostics(
    routable_network: gpd.GeoDataFrame | None,
    urban_spines: gpd.GeoDataFrame | None,
    graph: PlanningGraphSnapshot,
    required_sections: tuple[object, ...],
) -> _UrbanAttachmentDiagnostics:
    """Describe detached governed urban sections without changing their geometry."""

    if routable_network is None or urban_spines is None or urban_spines.empty:
        return _UrbanAttachmentDiagnostics(fingerprint=_fingerprint(()))
    from satn.strategic_network_planning import PlanningDiagnostic, ReviewableNetworkGap

    urban_projected = urban_spines.to_crs(27700)
    source_ids = {
        _source_edge_id(row, index)
        for index, row in routable_network.iterrows()
        if isinstance(row.geometry, LineString) and len(row.geometry.coords) >= 2
    }
    rows_by_id = {
        str(row.structure_id): row
        for row in urban_projected.itertuples()
        if isinstance(row.geometry, LineString) and not row.geometry.is_empty
    }
    urban_edge_by_source = {
        edge.source_edge_id: edge
        for edge in graph.edge_records
        if edge.source_edge_id in rows_by_id
    }
    original_edge_ids = {
        edge.directed_edge_id for edge in graph.edge_records if edge.source_edge_id in source_ids
    }
    component_by_edge = {
        edge_id: component
        for component in graph.component_records
        if component.kind == "weak"
        for edge_id in component.directed_edge_ids
    }
    diagnostics: list[PlanningDiagnostic] = []
    gaps: list[ReviewableNetworkGap] = []
    required_by_id = {section.section_id: section for section in required_sections}
    for section_id, row in sorted(rows_by_id.items()):
        urban_edge = urban_edge_by_source.get(section_id)
        component = (
            component_by_edge.get(urban_edge.directed_edge_id) if urban_edge is not None else None
        )
        if component is None or original_edge_ids.intersection(component.directed_edge_ids):
            continue
        classification = str(getattr(row, "official_classification", ""))
        if classification == "classified-unnumbered":
            if section_id in required_by_id:
                continue
            reason = (
                "Detached classified-unnumbered urban section is excluded from required "
                "Strategic Main coverage because it has no current routable-network attachment."
            )
            diagnostics.append(
                PlanningDiagnostic("strategic-main-section-excluded", section_id, reason)
            )
            continue
        if classification not in {"a-road", "b-road"}:
            continue
        section = required_by_id.get(section_id)
        if section is None:
            continue
        geometry = row.geometry
        coordinates = tuple(
            (float(coordinate[0]), float(coordinate[1]))
            for coordinate in (geometry.coords[0], geometry.coords[-1])
        )
        reason = (
            "Required urban A-road or B-road geometry has no current routable-network "
            "attachment; the exact proposed line is retained and its attachment remains unresolved."
        )
        diagnostics.append(
            PlanningDiagnostic("strategic-main-attachment-gap", section.section_id, reason)
        )
        gaps.append(
            ReviewableNetworkGap(
                obligation_id=section.obligation_id,
                network_role="strategic-main-network",
                endpoints=("", ""),
                reason=reason,
                endpoint_coordinates=coordinates,
            )
        )
    fingerprint = _fingerprint({"diagnostics": tuple(diagnostics), "gaps": tuple(gaps)})
    return _UrbanAttachmentDiagnostics(
        gaps=tuple(gaps),
        diagnostics=tuple(diagnostics),
        fingerprint=fingerprint,
    )


def _access_support_sections(
    frames: tuple[gpd.GeoDataFrame, ...],
):
    """Materialise exact governed access lines without treating them as mesh coverage."""

    from satn.strategic_network_planning import (
        EffectiveStrategicSection,
        PlanningAuthority,
    )

    sections: list[EffectiveStrategicSection] = []
    identifier_fields = (
        "access_connection_id",
        "meeting_connection_id",
        "connection_id",
        "section_id",
    )
    for frame in frames:
        if frame.empty:
            continue
        projected = frame.to_crs(27700)
        for index, row in projected.iterrows():
            geometry = row.geometry
            if not isinstance(geometry, LineString) or geometry.is_empty:
                raise ValueError("access support geometry must be a non-empty line")
            section_id = next(
                (
                    str(row[field_name])
                    for field_name in identifier_fields
                    if field_name in row and _present(row[field_name])
                ),
                None,
            )
            if section_id is None:
                raise ValueError(f"access support row has no governed identifier: {index!s}")
            obligation_id = (
                str(row["obligation_id"])
                if "obligation_id" in row and _present(row["obligation_id"])
                else section_id
            )
            role = (
                "school-access"
                if str(row.get("obligation_kind", "")).casefold() == "school"
                else "community-access"
            )
            sections.append(
                EffectiveStrategicSection(
                    section_id=section_id,
                    obligation_id=obligation_id,
                    candidate_id=None,
                    network_role=role,
                    routing_edge_ids=(),
                    reverse_routing_edge_ids=(),
                    geometry_wkt=geometry.wkt,
                    authority=PlanningAuthority.COMPILER,
                    alignment_bases=("access-support",),
                    primary_alignment_basis="access-support",
                    intervention_state="upgrade-required",
                    display_state="upgrade-required",
                    network_scope="rural",
                )
            )
    return tuple(sorted(sections, key=lambda section: section.section_id))


def compile_effective_strategic_network(
    request: EffectiveStrategicNetworkRequest | None,
) -> EffectiveStrategicNetworkState:
    """Compile the canonical state from complete governed strategic inputs.

    The translation from the routable frame and prepared corridor units to a
    :class:`StrategicNetworkPlanningRequest` lives behind this boundary.  The
    existing planning compiler is then called exactly once.
    """

    if request is None:
        return EffectiveStrategicNetworkState.unavailable()
    if not isinstance(request, EffectiveStrategicNetworkRequest):
        raise TypeError(
            "effective strategic compilation requires an EffectiveStrategicNetworkRequest"
        )
    if not request.governed_identity_complete:
        return EffectiveStrategicNetworkState.unavailable()

    if isinstance(request.routable_network, PlanningGraphSnapshot):
        if request.urban_spines is not None and not request.urban_spines.empty:
            raise ValueError(
                "urban strategic sections require the routable GeoDataFrame, "
                "not a prebuilt snapshot"
            )
        graph = request.routable_network
        required_sections = ()
        if graph.source_export_fingerprint != request.snapshot_fingerprint:
            return EffectiveStrategicNetworkState.unavailable("governed-identity-mismatch")
    else:
        graph, required_sections = _planning_graph_with_urban_spines(
            request.routable_network,
            request.urban_spines,
            source_export_fingerprint=request.snapshot_fingerprint,
        )
    required_sections = (
        *required_sections,
        *_access_support_sections(request.access_support),
    )
    attachment_diagnostics = _urban_attachment_diagnostics(
        request.routable_network
        if isinstance(request.routable_network, gpd.GeoDataFrame)
        else None,
        request.urban_spines,
        graph,
        tuple(required_sections),
    )
    discovery = discovery_from_preparation(request.preparation, graph)
    prepared_candidate_sets = discovery.candidate_sets
    from satn.strategic_network_planning import StrategicNetworkPlanningRequest

    planning_request = StrategicNetworkPlanningRequest(
        graph=graph,
        discovery=discovery,
        area_fingerprint=request.area_fingerprint,
        corridor_obligations=request.preparation,
        network_diagnostics=(
            attachment_diagnostics
            if attachment_diagnostics.diagnostics or attachment_diagnostics.gaps
            else None
        ),
        selection_profile=(
            request.preparation.units[0].candidate_set.profile
            if getattr(request.preparation, "units", ())
            else None
        ),
        routing_endpoint_bindings=tuple(
            (
                candidate_set.candidate_set_id,
                (unit.routing_start_node_id, unit.routing_end_node_id),
            )
            for unit, candidate_set in zip(
                request.preparation.units, prepared_candidate_sets, strict=True
            )
        ),
        officer_candidate_choices=_officer_choices(
            request.preparation,
            request.officer_decisions,
            prepared_candidate_sets,
        ),
        officer_decisions=None,
        required_sections=required_sections,
        backbone_obligation_ids=tuple(
            unit.unit_id
            for unit in request.preparation.units
            if getattr(unit, "backbone_required", False)
        ),
        mesh_profile=request.mesh_profile,
        mesh_profile_fingerprint=request.mesh_profile.fingerprint,
    )
    return _evaluate_planning_request(planning_request)


__all__ = [
    "EffectiveStrategicNetworkRequest",
    "EffectiveStrategicNetworkState",
    "EffectiveStrategicNetworkStatus",
    "compile_effective_strategic_network",
]
