"""Immutable application of discovered strategic candidates.

Candidate discovery deliberately stops before choosing a network.  This module is
the small authority boundary that applies the configured candidate order, an
initial officer ledger, or an explicitly governed reference route.  It never
invents geometry: every effective line is reconstructed from the exact directed
edge IDs in the Planning Graph snapshot.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum

from shapely.geometry import LineString
from shapely.wkt import loads as load_wkt

from satn.alignment_selection import AlignmentCandidateSet, _reuse_first_sort_key
from satn.candidate_discovery import CandidateDiscoveryResult
from satn.planning_graph import PlanningGraphSnapshot


def _canonical(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _canonical(model_dump(mode="json"))
    if is_dataclass(value):
        return {item.name: _canonical(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: object) -> str:
    return f"{prefix}-{_fingerprint(value)[:20]}"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be non-empty canonical text")
    return value


class PlanningAuthority(StrEnum):
    OFFICER = "officer"
    COMPILER = "compiler"
    GOVERNED_REFERENCE = "governed-reference-provisional"
    GAP = "gap"


@dataclass(frozen=True)
class ReferenceRoute:
    """A governed route retained as a provisional, non-generated fallback."""

    route_id: str
    obligation_id: str
    routing_edge_ids: tuple[str, ...]
    source_fingerprint: str
    role: str = "interurban-spine"
    graph_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _text(self.route_id, "reference route id")
        _text(self.obligation_id, "reference obligation id")
        _text(self.source_fingerprint, "reference source fingerprint")
        if not self.routing_edge_ids:
            raise ValueError("reference route requires directed routing edge IDs")
        if len(set(self.routing_edge_ids)) != len(self.routing_edge_ids):
            raise ValueError("reference route edge IDs must be unique")
        for edge_id in self.routing_edge_ids:
            _text(edge_id, "reference edge id")
        _text(self.role, "reference route role")


@dataclass(frozen=True)
class StrategicPlanningFallbackProfile:
    """Explicit authority order and mandatory role roster."""

    profile_id: str = "strategic-network-planning-trial-v1"
    fallback_order: tuple[str, ...] = ("officer", "compiler", "reference", "gap")
    required_roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.profile_id, "strategic planning profile id")
        allowed = {"officer", "compiler", "reference", "gap"}
        if set(self.fallback_order) - allowed or len(set(self.fallback_order)) != len(
            self.fallback_order
        ):
            raise ValueError("fallback order must contain each configured authority at most once")
        if "gap" not in self.fallback_order:
            raise ValueError("fallback order must include gap")
        roles = tuple(sorted(_text(item, "required network role") for item in self.required_roles))
        if len(set(roles)) != len(roles):
            raise ValueError("required network roles must be unique")
        object.__setattr__(self, "required_roles", roles)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self)


@dataclass(frozen=True)
class StrategicNetworkPlanningRequest:
    graph: PlanningGraphSnapshot
    discovery: CandidateDiscoveryResult
    area_fingerprint: str
    corridor_obligations: object | None = None
    network_diagnostics: object | None = None
    reference_routes: tuple[ReferenceRoute, ...] = ()
    officer_decisions: object | None = None
    fallback_profile: StrategicPlanningFallbackProfile = StrategicPlanningFallbackProfile()
    selection_profile: object | None = None
    compiler_preferred_candidate_ids: tuple[tuple[str, str], ...] = ()
    routing_endpoint_bindings: tuple[tuple[str, tuple[str, str]], ...] = ()
    officer_candidate_choices: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.graph, PlanningGraphSnapshot):
            raise ValueError("strategic planning requires a Planning Graph snapshot")
        if not isinstance(self.discovery, CandidateDiscoveryResult):
            raise ValueError("strategic planning requires a Candidate Discovery result")
        _text(self.area_fingerprint, "area fingerprint")
        routes = tuple(sorted(self.reference_routes, key=lambda item: item.route_id))
        if any(not isinstance(item, ReferenceRoute) for item in routes):
            raise ValueError("reference routes require ReferenceRoute records")
        if len({item.route_id for item in routes}) != len(routes):
            raise ValueError("reference route IDs must be unique")
        if len({item.obligation_id for item in routes}) != len(routes):
            raise ValueError("reference route obligation IDs must be unique")
        if self.selection_profile is not None:
            selection_fingerprint = getattr(self.selection_profile, "fingerprint", None)
            candidate_fingerprints = {
                item.profile_fingerprint for item in self.discovery.candidate_sets
            }
            if not isinstance(selection_fingerprint, str) or (
                candidate_fingerprints and candidate_fingerprints != {selection_fingerprint}
            ):
                raise ValueError(
                    "strategic planning selection profile does not match Candidate Discovery"
                )
        preferences = tuple(
            sorted(
                (
                    (
                        _text(candidate_set_id, "preferred Candidate Set id"),
                        _text(candidate_id, "preferred candidate id"),
                    )
                    for candidate_set_id, candidate_id in self.compiler_preferred_candidate_ids
                ),
                key=lambda item: item[0],
            )
        )
        if len({item[0] for item in preferences}) != len(preferences):
            raise ValueError("compiler preferences must name each Candidate Set at most once")
        endpoint_bindings = tuple(
            sorted(
                (
                    (
                        _text(candidate_set_id, "routing endpoint Candidate Set id"),
                        (
                            _text(endpoints[0], "routing start node id"),
                            _text(endpoints[1], "routing end node id"),
                        ),
                    )
                    for candidate_set_id, endpoints in self.routing_endpoint_bindings
                ),
                key=lambda item: item[0],
            )
        )
        if len({item[0] for item in endpoint_bindings}) != len(endpoint_bindings):
            raise ValueError("routing endpoint bindings must name each Candidate Set once")
        officer_choices = tuple(
            sorted(
                (
                    (
                        _text(candidate_id, "officer candidate id"),
                        _text(decision_id, "officer decision id"),
                    )
                    for candidate_id, decision_id in self.officer_candidate_choices
                ),
                key=lambda item: (item[0], item[1]),
            )
        )
        object.__setattr__(self, "reference_routes", routes)
        object.__setattr__(self, "compiler_preferred_candidate_ids", preferences)
        object.__setattr__(self, "routing_endpoint_bindings", endpoint_bindings)
        object.__setattr__(self, "officer_candidate_choices", officer_choices)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "area_fingerprint": self.area_fingerprint,
                "graph": {
                    "graph": self.graph.graph_fingerprint,
                    "profile": self.graph.profile_fingerprint,
                    "source": self.graph.source_export_fingerprint,
                    "route_controls": self.graph.route_control_fingerprint,
                },
                "discovery": self.discovery.fingerprint,
                "obligations": getattr(self.corridor_obligations, "fingerprint", None),
                "diagnostics": getattr(self.network_diagnostics, "fingerprint", None),
                "reference_routes": self.reference_routes,
                "officer": getattr(
                    self.officer_decisions,
                    "ledger_fingerprint",
                    getattr(self.officer_decisions, "fingerprint", None),
                ),
                "fallback_profile": self.fallback_profile.fingerprint,
                "selection_profile": getattr(self.selection_profile, "fingerprint", None),
                "compiler_preferred_candidate_ids": self.compiler_preferred_candidate_ids,
                "routing_endpoint_bindings": self.routing_endpoint_bindings,
                "officer_candidate_choices": self.officer_candidate_choices,
            }
        )


@dataclass(frozen=True)
class EffectiveStrategicSection:
    section_id: str
    obligation_id: str
    candidate_id: str | None
    network_role: str
    routing_edge_ids: tuple[str, ...]
    reverse_routing_edge_ids: tuple[str, ...]
    geometry_wkt: str
    authority: PlanningAuthority
    alignment_bases: tuple[str, ...] = ()
    primary_alignment_basis: str | None = None
    intervention_state: str | None = None
    display_state: str | None = None


@dataclass(frozen=True)
class EffectiveStrategicNetwork:
    sections: tuple[EffectiveStrategicSection, ...]
    fingerprint: str

    @property
    def routing_edge_ids(self) -> tuple[str, ...]:
        return tuple(edge_id for section in self.sections for edge_id in section.routing_edge_ids)


@dataclass(frozen=True)
class EffectiveReviewableSelection:
    obligation_id: str
    candidate_set_id: str | None
    network_role: str
    endpoints: tuple[str, str]
    compiler_candidate_id: str | None
    effective_candidate_id: str | None
    authority: PlanningAuthority
    routing_edge_ids: tuple[str, ...]
    reverse_routing_edge_ids: tuple[str, ...]
    geometry_wkt: str


@dataclass(frozen=True)
class CandidateDisposition:
    candidate_set_id: str
    obligation_id: str
    candidate_id: str
    disposition: str
    reason: str


@dataclass(frozen=True)
class ReviewableNetworkGap:
    obligation_id: str
    network_role: str
    endpoints: tuple[str, str]
    reason: str
    candidate_set_id: str | None = None
    gap_id: str = ""

    def __post_init__(self) -> None:
        expected = _stable_id(
            "network-gap",
            {
                "obligation_id": self.obligation_id,
                "network_role": self.network_role,
                "endpoints": self.endpoints,
                "reason": self.reason,
                "candidate_set_id": self.candidate_set_id,
            },
        )
        if self.gap_id and self.gap_id != expected:
            raise ValueError("strategic network gap_id is stale")
        object.__setattr__(
            self,
            "gap_id",
            expected,
        )


def _canonical_gaps(gaps: list[ReviewableNetworkGap]) -> tuple[ReviewableNetworkGap, ...]:
    by_id: dict[str, ReviewableNetworkGap] = {}
    for gap in gaps:
        existing = by_id.get(gap.gap_id)
        if existing is not None and existing != gap:
            raise ValueError("strategic network gap identity collision")
        by_id[gap.gap_id] = gap
    return tuple(by_id[gap_id] for gap_id in sorted(by_id))


@dataclass(frozen=True)
class OfficerCompilerDivergence:
    obligation_id: str
    network_role: str
    officer_candidate_id: str
    compiler_candidate_id: str
    reason: str


@dataclass(frozen=True)
class EvidenceRequest:
    request_id: str
    obligation_id: str
    claim: str
    reason: str


@dataclass(frozen=True)
class PlanningDiagnostic:
    code: str
    subject_id: str
    message: str


@dataclass(frozen=True)
class StrategicPlanningLineage:
    area_fingerprint: str
    graph_fingerprint: str
    graph_profile_fingerprint: str
    source_export_fingerprint: str
    route_control_fingerprint: str | None
    discovery_fingerprint: str
    obligations_fingerprint: str | None
    diagnostics_fingerprint: str | None
    selection_profile_fingerprint: str
    officer_decision_fingerprint: str | None
    reference_routes_fingerprint: str
    fallback_profile_fingerprint: str


@dataclass(frozen=True)
class StrategicNetworkPlanningResult:
    status: str
    effective_network: EffectiveStrategicNetwork
    selections: tuple[EffectiveReviewableSelection, ...]
    candidate_sets: tuple[AlignmentCandidateSet, ...]
    reference_routes: tuple[ReferenceRoute, ...]
    unselected_candidates: tuple[CandidateDisposition, ...]
    gaps: tuple[ReviewableNetworkGap, ...]
    divergences: tuple[OfficerCompilerDivergence, ...]
    evidence_requests: tuple[EvidenceRequest, ...]
    diagnostics: tuple[PlanningDiagnostic, ...]
    lineage: StrategicPlanningLineage
    fingerprint: str

    @property
    def strategic_result_fingerprint(self) -> str:
        return self.fingerprint

    @property
    def effective_selections(self) -> tuple[EffectiveReviewableSelection, ...]:
        return self.selections


def _edge_geometry(
    graph: PlanningGraphSnapshot,
    edge_ids: tuple[str, ...],
    *,
    endpoints: tuple[str, str] | None = None,
) -> str:
    if not edge_ids:
        raise ValueError("materialisation requires at least one directed edge")
    by_id = {item.directed_edge_id: item for item in graph.edge_records}
    if len(by_id) != len(graph.edge_records):
        raise ValueError("Planning Graph contains duplicate directed edge identities")
    edges = []
    for edge_id in edge_ids:
        if edge_id not in by_id:
            raise ValueError(f"selected edge {edge_id} is absent from the Planning Graph")
        edges.append(by_id[edge_id])
    prohibited = [
        edge.directed_edge_id for edge in edges if edge.access in {"no", "private", "customers"}
    ]
    if prohibited:
        raise ValueError("selected route uses known prohibited edge(s): " + ", ".join(prohibited))
    if endpoints:
        graph_nodes = {item.node_id.lower(): item.node_id for item in graph.node_records}
        graph_nodes.update(
            {
                node_id.lower(): node_id
                for edge in graph.edge_records
                for node_id in (edge.from_node_id, edge.to_node_id)
            }
        )
        resolved = tuple(graph_nodes.get(item.lower(), item) for item in endpoints)
    else:
        resolved = None
    if resolved and (edges[0].from_node_id != resolved[0] or edges[-1].to_node_id != resolved[1]):
        raise ValueError("selected edge chain does not bind Candidate Set endpoints")
    coordinates: list[tuple[float, float]] = []
    for index, edge in enumerate(edges):
        try:
            geometry = load_wkt(edge.geometry_wkt)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"selected edge {edge.directed_edge_id} has invalid geometry"
            ) from error
        if not isinstance(geometry, LineString) or geometry.is_empty:
            raise ValueError(f"selected edge {edge.directed_edge_id} is not a line geometry")
        if index and edges[index - 1].to_node_id != edge.from_node_id:
            raise ValueError("selected routing edge IDs are not a contiguous directed chain")
        points = list(geometry.coords)
        if not coordinates:
            coordinates.extend(points)
        elif coordinates[-1] == points[0]:
            coordinates.extend(points[1:])
        else:
            raise ValueError("selected edge geometry is not contiguous")
    return LineString(coordinates).wkt


def _reverse_edge_chain(
    graph: PlanningGraphSnapshot,
    forward_ids: tuple[str, ...],
    reverse_ids: tuple[str, ...],
    endpoints: tuple[str, str],
) -> None:
    """Validate a true reverse chain while retaining legacy reverse-order IDs."""

    if not reverse_ids:
        return
    by_id = {item.directed_edge_id: item for item in graph.edge_records}
    if len(set(reverse_ids)) != len(reverse_ids) or any(item not in by_id for item in reverse_ids):
        raise ValueError("reverse routing edge IDs are not members of the Planning Graph")
    graph_nodes = {item.node_id.lower(): item.node_id for item in graph.node_records}
    start = graph_nodes.get(endpoints[1].lower(), endpoints[1])
    end = graph_nodes.get(endpoints[0].lower(), endpoints[0])
    reverse_edges = tuple(by_id[item] for item in reverse_ids)
    if (
        reverse_edges[0].from_node_id == start
        and reverse_edges[-1].to_node_id == end
        and all(
            reverse_edges[index - 1].to_node_id == edge.from_node_id
            for index, edge in enumerate(reverse_edges)
            if index
        )
    ):
        return
    # Candidate Discovery v1 records a reverse traversal as the forward IDs in
    # reverse order when the snapshot has no reverse directed edges. Preserve
    # that explicit representation, but reject any other malformed chain.
    if reverse_ids == tuple(reversed(forward_ids)) and not any(
        edge.from_node_id == by_id[forward_ids[-1]].to_node_id
        and edge.to_node_id == by_id[forward_ids[-1]].from_node_id
        for edge in graph.edge_records
    ):
        return
    raise ValueError("reverse routing edge IDs are not a contiguous reverse chain")


def _geometry_matches(expected_wkt: str, actual_wkt: str) -> bool:
    try:
        expected = load_wkt(expected_wkt)
        actual = load_wkt(actual_wkt)
    except (TypeError, ValueError):
        return False
    return (
        isinstance(expected, LineString)
        and isinstance(actual, LineString)
        and expected.equals_exact(actual, 1e-6)
    )


def _obligation_ids(result: CandidateDiscoveryResult) -> dict[str, str]:
    return {
        item.candidate_set_id: item.obligation_id
        for item in result.obligation_dispositions
        if item.candidate_set_id is not None
    }


def _candidate_record_map(result: CandidateDiscoveryResult) -> dict[str, object]:
    return {item.candidate_id: item for item in result.candidate_records}


def _preferred_candidate(
    candidate_set: object, governed_candidate_id: str | None = None
) -> object | None:
    candidates = tuple(candidate_set.admitted_candidates)
    if not candidates:
        return None
    if governed_candidate_id is not None:
        return next(
            (item for item in candidates if item.candidate_id == governed_candidate_id),
            None,
        )
    return min(candidates, key=lambda item: _reuse_first_sort_key(candidate_set.profile, item))


def _active_officer_choices(ledger: object | None) -> tuple[tuple[str, str], ...]:
    if ledger is None:
        return ()
    choices: list[tuple[str, str]] = []
    for decision in getattr(ledger, "decisions", ()):
        status = getattr(decision, "status", "active")
        if getattr(status, "value", status) != "active":
            continue
        action = getattr(decision, "action", None)
        if getattr(action, "kind", None) != "select-alignment":
            continue
        target = getattr(decision, "target", None)
        target_id = getattr(target, "target_id", None)
        decision_id = getattr(decision, "decision_id", "officer-decision")
        if isinstance(target_id, str):
            choices.append((target_id, decision_id))
    return tuple(sorted(choices))


def _display_state(intervention_state: object | None) -> str:
    value = getattr(intervention_state, "value", intervention_state)
    return {
        "existing-provision": "existing-provision",
        "upgrade-required": "upgrade-required",
        "proposed-new-link": "proposed-new-link",
        "undetermined": "unresolved-gap",
    }.get(str(value), "unresolved-gap")


def compile_strategic_network(
    request: StrategicNetworkPlanningRequest,
) -> StrategicNetworkPlanningResult:
    """Compile every finite Candidate Set into an immutable reviewable network."""

    if not isinstance(request, StrategicNetworkPlanningRequest):
        raise ValueError("strategic planning requires a StrategicNetworkPlanningRequest")
    graph = request.graph
    discovery = request.discovery
    diagnostics: list[PlanningDiagnostic] = []
    gaps: list[ReviewableNetworkGap] = []
    requests: list[EvidenceRequest] = []
    for gap in sorted(discovery.gaps, key=lambda item: item.obligation_id):
        role = str(getattr(gap, "network_role", "unresolved-strategic-alignment"))
        reviewable_gap = ReviewableNetworkGap(
            gap.obligation_id,
            role,
            tuple(gap.endpoints),
            gap.reason,
        )
        gaps.append(reviewable_gap)
        requests.append(
            EvidenceRequest(
                _stable_id(
                    "evidence-request",
                    (gap.obligation_id, gap.reason, tuple(gap.search_diagnostic_ids)),
                ),
                gap.obligation_id,
                "candidate-set",
                gap.reason,
            )
        )
    raw_candidate_sets = tuple(
        sorted(discovery.candidate_sets, key=lambda item: getattr(item, "candidate_set_id", ""))
    )
    set_ids = tuple(getattr(item, "candidate_set_id", "") for item in raw_candidate_sets)
    duplicate_set_ids = sorted({item for item in set_ids if set_ids.count(item) > 1})
    if duplicate_set_ids:
        diagnostics.extend(
            PlanningDiagnostic(
                "duplicate-candidate-set",
                item,
                "Candidate Discovery contains duplicate Candidate Set identities",
            )
            for item in duplicate_set_ids
        )
        raw_candidate_sets = tuple(
            item
            for index, item in enumerate(raw_candidate_sets)
            if getattr(item, "candidate_set_id", "") not in set_ids[:index]
        )
    invalid_candidate_sets: list[tuple[object, str]] = []
    candidate_sets: list[AlignmentCandidateSet] = []
    for candidate_set in raw_candidate_sets:
        subject_id = str(getattr(candidate_set, "candidate_set_id", "unknown-candidate-set"))
        try:
            if not isinstance(candidate_set, AlignmentCandidateSet):
                raise ValueError("Candidate Discovery contains a non-canonical Candidate Set")
            validated = AlignmentCandidateSet.model_validate(
                candidate_set.model_dump(mode="python")
            )
        except (AttributeError, TypeError, ValueError) as error:
            error_text = str(error)
            diagnostic_code = (
                "invalid-candidate-set"
                if "candidate_set_id" in error_text or "candidate_set_fingerprint" in error_text
                else "malformed-candidate-set"
            )
            diagnostics.append(PlanningDiagnostic(diagnostic_code, subject_id, error_text))
            invalid_candidate_sets.append((candidate_set, error_text))
            continue
        candidate_sets.append(validated)
    candidate_sets = tuple(candidate_sets)
    disposition_rows = tuple(
        sorted(
            discovery.obligation_dispositions,
            key=lambda item: (
                str(getattr(item, "candidate_set_id", "")),
                str(getattr(item, "obligation_id", "")),
                str(getattr(item, "disposition", "")),
            ),
        )
    )
    disposition_set_ids = tuple(item.candidate_set_id for item in disposition_rows)
    duplicate_disposition_ids = sorted(
        {item for item in disposition_set_ids if disposition_set_ids.count(item) > 1}
    )
    if duplicate_disposition_ids:
        diagnostics.extend(
            PlanningDiagnostic(
                "duplicate-obligation-disposition",
                item,
                "Candidate Discovery contains duplicate obligation disposition rows",
            )
            for item in duplicate_disposition_ids
        )
    set_to_obligation: dict[str, str] = {}
    for item in disposition_rows:
        if item.candidate_set_id in set_to_obligation:
            continue
        set_to_obligation[item.candidate_set_id] = item.obligation_id
    candidate_set_ids = {item.candidate_set_id for item in candidate_sets}
    for item in disposition_rows:
        if item.candidate_set_id not in candidate_set_ids:
            diagnostics.append(
                PlanningDiagnostic(
                    "foreign-obligation-disposition",
                    str(item.candidate_set_id),
                    "obligation disposition names an absent Candidate Set",
                )
            )
    binding_invalid_sets: set[str] = set()
    binding_reason_by_set: dict[str, str] = {}
    disposition_by_set: dict[str, tuple[object, ...]] = {}
    for item in disposition_rows:
        if item.candidate_set_id is None:
            continue
        disposition_by_set[item.candidate_set_id] = (
            *disposition_by_set.get(item.candidate_set_id, ()),
            item,
        )
    obligation_by_set: dict[str, str] = {}
    for candidate_set in candidate_sets:
        set_id = candidate_set.candidate_set_id
        rows = disposition_by_set.get(set_id, ())
        if not rows:
            binding_invalid_sets.add(set_id)
            binding_reason_by_set[set_id] = "Candidate Set has no obligation disposition"
            diagnostics.append(
                PlanningDiagnostic(
                    "missing-obligation-disposition",
                    set_id,
                    "Candidate Set has no obligation disposition",
                )
            )
            obligation_by_set[set_id] = set_id
            continue
        row = rows[0]
        obligation_id = getattr(row, "obligation_id", None)
        obligation_by_set[set_id] = str(obligation_id)
        if not isinstance(obligation_id, str) or not obligation_id.strip():
            binding_invalid_sets.add(set_id)
            binding_reason_by_set[set_id] = "obligation disposition has no canonical obligation ID"
            diagnostics.append(
                PlanningDiagnostic(
                    "obligation-binding-mismatch",
                    set_id,
                    "obligation disposition has no canonical obligation ID",
                )
            )
            continue
        if len(rows) > 1:
            binding_invalid_sets.add(set_id)
            binding_reason_by_set[set_id] = "Candidate Set has duplicate obligation dispositions"
        expected_disposition = "candidates" if candidate_set.admitted_candidates else "gap"
        actual_disposition = str(getattr(row, "disposition", ""))
        if actual_disposition != expected_disposition:
            binding_invalid_sets.add(set_id)
            binding_reason_by_set[set_id] = (
                "obligation disposition is inconsistent with Candidate Set admissions"
            )
            diagnostics.append(
                PlanningDiagnostic(
                    "obligation-disposition-mismatch",
                    set_id,
                    "obligation disposition is inconsistent with Candidate Set admissions",
                )
            )
        for candidate in candidate_set.candidates:
            bound_records = tuple(
                item
                for item in discovery.candidate_records
                if item.candidate_id == candidate.candidate_id
            )
            if any(item.obligation_id != obligation_id for item in bound_records):
                binding_invalid_sets.add(set_id)
                binding_reason_by_set[set_id] = (
                    "Candidate Discovery record obligation does not match its disposition"
                )
                diagnostics.append(
                    PlanningDiagnostic(
                        "obligation-binding-mismatch",
                        set_id,
                        "Candidate Discovery record obligation does not match its disposition",
                    )
                )
                break
    for candidate_set, reason in invalid_candidate_sets:
        set_id = str(getattr(candidate_set, "candidate_set_id", "invalid-candidate-set"))
        binding_invalid_sets.add(set_id)
        binding_reason_by_set[set_id] = reason
        gaps.append(
            ReviewableNetworkGap(
                set_id,
                str(getattr(candidate_set, "network_role", "unresolved-strategic-alignment")),
                tuple(getattr(candidate_set, "endpoints", ("", ""))),
                reason,
                set_id,
            )
        )
    records = _candidate_record_map(discovery)
    record_ids = tuple(item.candidate_id for item in discovery.candidate_records)
    duplicate_record_ids = sorted({item for item in record_ids if record_ids.count(item) > 1})
    if duplicate_record_ids:
        diagnostics.extend(
            PlanningDiagnostic(
                "duplicate-candidate-record",
                item,
                "Candidate Discovery contains duplicate candidate identities",
            )
            for item in duplicate_record_ids
        )
        records = {
            item.candidate_id: item
            for item in discovery.candidate_records
            if item.candidate_id not in duplicate_record_ids
        }
    expected_candidate_ids = {
        candidate.candidate_id
        for candidate_set in candidate_sets
        for candidate in candidate_set.candidates
    }
    foreign_record_ids = sorted(set(records) - expected_candidate_ids)
    if foreign_record_ids:
        diagnostics.extend(
            PlanningDiagnostic(
                "foreign-candidate-record",
                item,
                "Candidate Discovery record is not bound to a supplied Candidate Set",
            )
            for item in foreign_record_ids
        )
    missing_record_ids = sorted(expected_candidate_ids - set(records))
    if missing_record_ids:
        diagnostics.extend(
            PlanningDiagnostic(
                "missing-candidate-record",
                item,
                "Candidate Set candidate has no bound discovery record",
            )
            for item in missing_record_ids
        )
    reference_by_obligation = {item.obligation_id: item for item in request.reference_routes}
    known_obligations = {
        obligation_id
        for set_id, obligation_id in obligation_by_set.items()
        if set_id not in binding_invalid_sets
    }
    for route in request.reference_routes:
        if route.obligation_id not in known_obligations:
            diagnostics.append(
                PlanningDiagnostic(
                    "foreign-reference-route",
                    route.route_id,
                    "reference route names an obligation absent from Candidate Discovery",
                )
            )
    officer_choices = tuple(
        sorted(
            (
                *_active_officer_choices(request.officer_decisions),
                *request.officer_candidate_choices,
            )
        )
    )
    preferred_by_set = dict(request.compiler_preferred_candidate_ids)
    routing_endpoints_by_set = dict(request.routing_endpoint_bindings)
    officer_by_candidate: dict[str, tuple[str, ...]] = {}
    for candidate_id, decision_id in officer_choices:
        officer_by_candidate[candidate_id] = (
            *officer_by_candidate.get(candidate_id, ()),
            decision_id,
        )
    selections: list[EffectiveReviewableSelection] = []
    divergences: list[OfficerCompilerDivergence] = []
    dispositions: list[CandidateDisposition] = []
    sections: list[EffectiveStrategicSection] = []
    selected_ids: set[str] = set()
    effective_roles: set[str] = set()

    profile_fingerprints = {item.profile_fingerprint for item in candidate_sets}
    if len(profile_fingerprints) > 1:
        diagnostics.append(
            PlanningDiagnostic(
                "inconsistent-selection-profile",
                "candidate-sets",
                "Candidate Discovery Candidate Sets do not share one governed profile",
            )
        )
    if not candidate_sets:
        reason = "Candidate Discovery returned no Candidate Sets"
        gaps.append(
            ReviewableNetworkGap("discovery", "unresolved-strategic-alignment", ("", ""), reason)
        )
        requests.append(
            EvidenceRequest(
                _stable_id("evidence-request", reason), "discovery", "candidate-set", reason
            )
        )

    for candidate_set in candidate_sets:
        set_id = candidate_set.candidate_set_id
        obligation_id = obligation_by_set.get(set_id, set_to_obligation.get(set_id, set_id))
        binding_valid = set_id not in binding_invalid_sets
        role = getattr(candidate_set.network_role, "value", candidate_set.network_role)
        endpoints = tuple(candidate_set.endpoints)
        routing_endpoints = routing_endpoints_by_set.get(set_id, endpoints)
        governed_preference = preferred_by_set.get(set_id)
        compiler = (
            _preferred_candidate(candidate_set, governed_preference) if binding_valid else None
        )
        if governed_preference is not None and compiler is None:
            diagnostics.append(
                PlanningDiagnostic(
                    "invalid-compiler-preference",
                    set_id,
                    "governed compiler preference is not an admitted candidate",
                )
            )
        compiler_id = None if compiler is None else compiler.candidate_id
        set_candidate_ids = {item.candidate_id for item in candidate_set.candidates}
        officer_ids = tuple(
            candidate_id
            for candidate_id in officer_by_candidate
            if candidate_id in set_candidate_ids
        )
        officer_id = officer_ids[0] if len(officer_ids) == 1 else None
        if len(officer_ids) > 1 or any(len(officer_by_candidate[item]) > 1 for item in officer_ids):
            diagnostics.append(
                PlanningDiagnostic(
                    "conflicting-officer-choice",
                    candidate_set.candidate_set_id,
                    "multiple active officer alignment choices target one obligation",
                )
            )
            gaps.append(
                ReviewableNetworkGap(
                    obligation_id,
                    str(role),
                    endpoints,
                    "active officer choices are ambiguous",
                    candidate_set.candidate_set_id,
                )
            )
        effective = None
        authority = PlanningAuthority.GAP
        candidate_by_id = {item.candidate_id: item for item in candidate_set.admitted_candidates}
        officer_candidate = candidate_by_id.get(officer_id) if officer_id is not None else None
        if officer_id is not None and officer_candidate is None:
            diagnostics.append(
                PlanningDiagnostic(
                    "officer-target-rejected",
                    officer_id,
                    "officer selected a candidate rejected by the governed Candidate Set",
                )
            )
            gaps.append(
                ReviewableNetworkGap(
                    obligation_id,
                    str(role),
                    endpoints,
                    "officer target is not admitted",
                    candidate_set.candidate_set_id,
                )
            )
        reference = reference_by_obligation.get(obligation_id) if binding_valid else None
        reference_geometry: str | None = None
        if reference is not None:
            try:
                if (
                    reference.graph_fingerprint
                    and reference.graph_fingerprint != graph.graph_fingerprint
                ):
                    raise ValueError("reference route graph fingerprint is stale")
                reference_geometry = _edge_geometry(
                    graph, reference.routing_edge_ids, endpoints=routing_endpoints
                )
            except ValueError as error:
                diagnostics.append(
                    PlanningDiagnostic("invalid-reference-route", reference.route_id, str(error))
                )

        fallback_order = request.fallback_profile.fallback_order if binding_valid else ("gap",)
        for authority_name in fallback_order:
            if authority_name == "officer" and officer_candidate is not None:
                effective = officer_candidate
                authority = PlanningAuthority.OFFICER
            elif authority_name == "compiler" and compiler is not None:
                effective = compiler
                authority = PlanningAuthority.COMPILER
            elif (
                authority_name == "reference"
                and reference is not None
                and reference_geometry is not None
            ):
                authority = PlanningAuthority.GOVERNED_REFERENCE
            elif authority_name == "gap":
                authority = PlanningAuthority.GAP
                break
            if effective is not None or authority is PlanningAuthority.GOVERNED_REFERENCE:
                break
        if (
            officer_id is not None
            and compiler_id is not None
            and officer_id != compiler_id
            and effective is not None
        ):
            divergences.append(
                OfficerCompilerDivergence(
                    obligation_id,
                    str(role),
                    officer_id,
                    compiler_id,
                    "immutable officer choice differs from compiler-preferred candidate",
                )
            )
        if (
            effective is None
            and authority is PlanningAuthority.GOVERNED_REFERENCE
            and reference is not None
            and reference_geometry is not None
        ):
            selections.append(
                EffectiveReviewableSelection(
                    obligation_id,
                    candidate_set.candidate_set_id,
                    str(role),
                    endpoints,
                    compiler_id,
                    None,
                    authority,
                    reference.routing_edge_ids,
                    (),
                    reference_geometry,
                )
            )
            sections.append(
                EffectiveStrategicSection(
                    reference.route_id,
                    obligation_id,
                    None,
                    str(role),
                    reference.routing_edge_ids,
                    (),
                    reference_geometry,
                    authority,
                    display_state="reference-route",
                )
            )
            effective_roles.add(str(role))
        elif effective is None:
            reason = binding_reason_by_set.get(
                set_id, "no admitted Candidate Set candidate or governed reference route"
            )
            gaps.append(
                ReviewableNetworkGap(
                    obligation_id, str(role), endpoints, reason, candidate_set.candidate_set_id
                )
            )
            requests.append(
                EvidenceRequest(
                    _stable_id("evidence-request", (obligation_id, reason)),
                    obligation_id,
                    "strategic-alignment",
                    reason,
                )
            )
        else:
            record = records.get(effective.candidate_id)
            try:
                if record is None:
                    raise ValueError("Candidate Discovery record is missing for selected candidate")
                edge_ids = tuple(record.edge_ids)
                geometry = _edge_geometry(graph, edge_ids, endpoints=routing_endpoints)
                reverse_ids = tuple(record.reverse_edge_ids)
                _reverse_edge_chain(graph, edge_ids, reverse_ids, routing_endpoints)
                if not _geometry_matches(record.geometry_wkt, geometry):
                    raise ValueError(
                        "candidate geometry does not match materialized Planning Graph geometry"
                    )
            except ValueError as error:
                diagnostics.append(
                    PlanningDiagnostic(
                        "candidate-geometry-mismatch"
                        if "candidate geometry" in str(error)
                        else "invalid-selected-candidate",
                        effective.candidate_id,
                        str(error),
                    )
                )
                gaps.append(
                    ReviewableNetworkGap(
                        obligation_id,
                        str(role),
                        endpoints,
                        str(error),
                        candidate_set.candidate_set_id,
                    )
                )
                requests.append(
                    EvidenceRequest(
                        _stable_id(
                            "evidence-request", (obligation_id, effective.candidate_id, str(error))
                        ),
                        obligation_id,
                        "route-continuity",
                        str(error),
                    )
                )
            else:
                selected_ids.add(effective.candidate_id)
                effective_roles.add(str(role))
                selections.append(
                    EffectiveReviewableSelection(
                        obligation_id,
                        candidate_set.candidate_set_id,
                        str(role),
                        endpoints,
                        compiler_id,
                        effective.candidate_id,
                        authority,
                        edge_ids,
                        reverse_ids,
                        geometry,
                    )
                )
                section_id = effective.candidate_id
                sections.append(
                    EffectiveStrategicSection(
                        section_id,
                        obligation_id,
                        effective.candidate_id,
                        str(role),
                        edge_ids,
                        reverse_ids,
                        geometry,
                        authority,
                        tuple(effective.alignment_bases),
                        effective.primary_alignment_basis,
                        getattr(
                            effective.intervention_state, "value", effective.intervention_state
                        ),
                        _display_state(effective.intervention_state),
                    )
                )

        admission_by_id = {item.candidate_id: item for item in candidate_set.admissions}
        for candidate in candidate_set.candidates:
            admission = admission_by_id.get(candidate.candidate_id)
            if admission is None:
                diagnostics.append(
                    PlanningDiagnostic(
                        "malformed-candidate-set",
                        candidate_set.candidate_set_id,
                        "Candidate Set candidate is missing an admission disposition",
                    )
                )
                gaps.append(
                    ReviewableNetworkGap(
                        obligation_id,
                        str(role),
                        endpoints,
                        "Candidate Set admission roster is incomplete",
                        candidate_set.candidate_set_id,
                    )
                )
                continue
            if candidate.candidate_id in selected_ids:
                disposition, reason = (
                    "effective",
                    "candidate is effective in the immutable strategic network",
                )
            elif getattr(admission.disposition, "value", admission.disposition) == "admitted":
                disposition, reason = "unselected", "admitted alternative retained for review"
            else:
                disposition, reason = (
                    "rejected",
                    getattr(admission.rationale, "value", str(admission.rationale)),
                )
            dispositions.append(
                CandidateDisposition(
                    candidate_set.candidate_set_id,
                    obligation_id,
                    candidate.candidate_id,
                    disposition,
                    reason,
                )
            )

    for target_id, decision_id in officer_choices:
        if not any(
            target_id == candidate.candidate_id
            for item in candidate_sets
            for candidate in item.candidates
        ):
            diagnostics.append(
                PlanningDiagnostic(
                    "unknown-officer-target",
                    decision_id,
                    f"officer target {target_id} is not present in the Candidate Discovery result",
                )
            )
            if candidate_sets:
                candidate_set = candidate_sets[0]
                obligation_id = obligation_by_set.get(
                    candidate_set.candidate_set_id, candidate_set.candidate_set_id
                )
                gaps.append(
                    ReviewableNetworkGap(
                        obligation_id,
                        str(candidate_set.network_role.value),
                        tuple(candidate_set.endpoints),
                        "unknown officer target",
                        candidate_set.candidate_set_id,
                    )
                )

    missing_roles = set(request.fallback_profile.required_roles) - effective_roles
    for role in sorted(missing_roles):
        diagnostics.append(
            PlanningDiagnostic(
                "required-role-gap",
                role,
                "required strategic network role has no effective selection",
            )
        )
        gaps.append(
            ReviewableNetworkGap(
                f"role:{role}",
                role,
                ("", ""),
                "required strategic network role has no effective selection",
            )
        )

    status = (
        "complete-with-gaps"
        if gaps
        else "reference-fallback"
        if any(item.authority is PlanningAuthority.GOVERNED_REFERENCE for item in selections)
        else "complete"
    )
    effective_network = EffectiveStrategicNetwork(
        sections=tuple(sorted(sections, key=lambda item: item.section_id)),
        fingerprint=_fingerprint(tuple(sorted(sections, key=lambda item: item.section_id))),
    )
    selected_profile = (
        candidate_sets[0].profile_fingerprint
        if candidate_sets
        else _fingerprint("empty-selection-profile")
    )
    officer_fingerprint = getattr(request.officer_decisions, "ledger_fingerprint", None)
    lineage = StrategicPlanningLineage(
        area_fingerprint=request.area_fingerprint,
        graph_fingerprint=graph.graph_fingerprint,
        graph_profile_fingerprint=graph.profile_fingerprint,
        source_export_fingerprint=graph.source_export_fingerprint,
        route_control_fingerprint=graph.route_control_fingerprint,
        discovery_fingerprint=discovery.fingerprint,
        obligations_fingerprint=getattr(request.corridor_obligations, "fingerprint", None),
        diagnostics_fingerprint=getattr(request.network_diagnostics, "fingerprint", None),
        selection_profile_fingerprint=selected_profile,
        officer_decision_fingerprint=officer_fingerprint,
        reference_routes_fingerprint=_fingerprint(request.reference_routes),
        fallback_profile_fingerprint=request.fallback_profile.fingerprint,
    )
    canonical_gaps = _canonical_gaps(gaps)
    payload = {
        "status": status,
        "effective_network": effective_network,
        "selections": selections,
        "candidate_sets": candidate_sets,
        "reference_routes": request.reference_routes,
        "unselected_candidates": dispositions,
        "gaps": canonical_gaps,
        "divergences": divergences,
        "evidence_requests": requests,
        "diagnostics": diagnostics,
        "lineage": lineage,
    }
    return StrategicNetworkPlanningResult(
        status=status,
        effective_network=effective_network,
        selections=tuple(sorted(selections, key=lambda item: item.obligation_id)),
        candidate_sets=candidate_sets,
        reference_routes=request.reference_routes,
        unselected_candidates=tuple(
            sorted(dispositions, key=lambda item: (item.obligation_id, item.candidate_id))
        ),
        gaps=canonical_gaps,
        divergences=tuple(sorted(divergences, key=lambda item: item.obligation_id)),
        evidence_requests=tuple(sorted(requests, key=lambda item: item.request_id)),
        diagnostics=tuple(sorted(diagnostics, key=lambda item: (item.code, item.subject_id))),
        lineage=lineage,
        fingerprint=_fingerprint(payload),
    )


__all__ = [
    "CandidateDisposition",
    "EffectiveReviewableSelection",
    "EffectiveStrategicNetwork",
    "EffectiveStrategicSection",
    "EvidenceRequest",
    "OfficerCompilerDivergence",
    "PlanningAuthority",
    "PlanningDiagnostic",
    "ReferenceRoute",
    "ReviewableNetworkGap",
    "StrategicNetworkPlanningRequest",
    "StrategicNetworkPlanningResult",
    "StrategicPlanningFallbackProfile",
    "StrategicPlanningLineage",
    "compile_strategic_network",
]
