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
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import geopandas as gpd
import networkx as nx
from shapely.errors import ShapelyError
from shapely.geometry import LineString

from satn.alignment_selection import admit_candidate_set
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
    _coordinate_id,
    _directed_edge_identity,
    _present,
    _truthy,
)
from satn.strategic_corridors import StrategicCorridorPreparationResult

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

    def __post_init__(self) -> None:
        if self.routable_network is not None and not isinstance(
            self.routable_network, (gpd.GeoDataFrame, PlanningGraphSnapshot)
        ):
            raise ValueError("effective strategic request requires a routable snapshot")
        if self.area_fingerprint is not None and not _is_sha256(self.area_fingerprint):
            raise ValueError("effective strategic request area fingerprint must be SHA-256")
        if self.snapshot_fingerprint is not None and not _is_sha256(self.snapshot_fingerprint):
            raise ValueError("effective strategic request snapshot fingerprint must be SHA-256")
        object.__setattr__(self, "officer_decisions", tuple(self.officer_decisions))

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


def _source_edge_id(row: object, index: object) -> str:
    if "osmid" in row:
        return str(row.get("osmid"))
    if "source_id" in row:
        return str(row.get("source_id"))
    if "edge_id" in row:
        return str(row.get("edge_id"))
    return str(index)


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
    directed = nx.DiGraph()
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
    if explicit_bases:
        bases = explicit_bases
    elif ncn or source_class == "verified-existing-asset":
        bases = ("current-ncn",)
    elif "cycleway" in highways:
        bases = ("mapped-cycleway",)
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
    elif source_class == "verified-existing-asset" or "cycleway" in highways:
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
                    network_role=unit.unit_role.value,
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
        obligation_id = issue.strategic_destination_id or issue.site_id or f"issue-{index + 1}"
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
                endpoints=("unresolved", obligation_id),
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
            matches = [
                record.candidate.candidate_id
                for record in unit.candidate_records
                if record.candidate.candidate_id
                in {item.candidate_id for item in candidate_set.candidates}
                and route_id in {record.candidate.candidate_id, record.physical_alignment_id}
            ]
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
        graph = request.routable_network
        if graph.source_export_fingerprint != request.snapshot_fingerprint:
            return EffectiveStrategicNetworkState.unavailable("governed-identity-mismatch")
    else:
        graph = planning_graph_from_compiler_edges(
            request.routable_network,
            source_export_fingerprint=request.snapshot_fingerprint,
        )
    discovery = discovery_from_preparation(request.preparation, graph)
    prepared_candidate_sets = discovery.candidate_sets
    from satn.strategic_network_planning import StrategicNetworkPlanningRequest

    planning_request = StrategicNetworkPlanningRequest(
        graph=graph,
        discovery=discovery,
        area_fingerprint=request.area_fingerprint,
        corridor_obligations=request.preparation,
        selection_profile=(
            request.preparation.units[0].candidate_set.profile
            if getattr(request.preparation, "units", ())
            else None
        ),
        compiler_preferred_candidate_ids=_compiler_preferences(
            request.preparation, prepared_candidate_sets
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
    )
    return _evaluate_planning_request(planning_request)


__all__ = [
    "EffectiveStrategicNetworkRequest",
    "EffectiveStrategicNetworkState",
    "EffectiveStrategicNetworkStatus",
    "compile_effective_strategic_network",
]
