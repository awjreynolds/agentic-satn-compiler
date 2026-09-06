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
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import StrEnum
from itertools import pairwise

import networkx as nx
from shapely.geometry import LineString, Point
from shapely.wkt import loads as load_wkt

from satn.alignment_selection import AlignmentCandidateSet, _reuse_first_sort_key
from satn.candidate_discovery import CandidateDiscoveryResult
from satn.planning_graph import PlanningGraphSnapshot
from satn.strategic_mesh import (
    CandidateRouteSection,
    MeshCoveragePoint,
    MeshGap,
    StrategicMainNetworkProfile,
    StrategicMainNetworkRequest,
    assemble_strategic_main_network,
    derive_mesh_coverage_points,
)


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
    required_sections: tuple[EffectiveStrategicSection, ...] = ()
    backbone_obligation_ids: tuple[str, ...] = ()
    mesh_profile: StrategicMainNetworkProfile = field(default_factory=StrategicMainNetworkProfile)
    mesh_profile_fingerprint: str | None = None

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
        required_sections = tuple(sorted(self.required_sections, key=lambda item: item.section_id))
        if any(not isinstance(item, EffectiveStrategicSection) for item in required_sections):
            raise ValueError("required strategic sections must be effective section records")
        if len({item.section_id for item in required_sections}) != len(required_sections):
            raise ValueError("required strategic section IDs must be unique")
        backbone_obligation_ids = tuple(
            sorted(_text(item, "backbone obligation id") for item in self.backbone_obligation_ids)
        )
        if self.mesh_profile_fingerprint is not None:
            try:
                int(self.mesh_profile_fingerprint, 16)
            except (TypeError, ValueError) as error:
                raise ValueError("strategic mesh profile fingerprint must be SHA-256") from error
            if (
                len(self.mesh_profile_fingerprint) != 64
                or self.mesh_profile_fingerprint != self.mesh_profile_fingerprint.lower()
            ):
                raise ValueError("strategic mesh profile fingerprint must be SHA-256")
            if self.mesh_profile_fingerprint != self.mesh_profile.fingerprint:
                raise ValueError("strategic mesh profile fingerprint does not match profile")
        elif self.mesh_profile is not None:
            object.__setattr__(self, "mesh_profile_fingerprint", self.mesh_profile.fingerprint)
        object.__setattr__(self, "reference_routes", routes)
        object.__setattr__(self, "compiler_preferred_candidate_ids", preferences)
        object.__setattr__(self, "routing_endpoint_bindings", endpoint_bindings)
        object.__setattr__(self, "officer_candidate_choices", officer_choices)
        object.__setattr__(self, "required_sections", required_sections)
        object.__setattr__(self, "backbone_obligation_ids", backbone_obligation_ids)

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
                "required_sections": self.required_sections,
                "backbone_obligation_ids": self.backbone_obligation_ids,
                "mesh_profile": self.mesh_profile,
                "mesh_profile_fingerprint": self.mesh_profile_fingerprint,
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
    network_scope: str | None = None

    def __post_init__(self) -> None:
        scope = self.network_scope
        if scope is None:
            scope = "rural" if self.network_role.casefold() == "interurban-spine" else "urban"
        if scope not in {"urban", "rural"}:
            raise ValueError("effective strategic section network_scope must be urban or rural")
        object.__setattr__(self, "network_scope", scope)


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
    selection_reason: str = "selection reason unavailable"
    decision_id: str | None = None
    decision_maker: str | None = None


@dataclass(frozen=True)
class CandidateDisposition:
    candidate_set_id: str
    obligation_id: str
    candidate_id: str
    disposition: str
    reason: str
    comparison_reason: str | None = None


@dataclass(frozen=True)
class ReviewableNetworkGap:
    obligation_id: str
    network_role: str
    endpoints: tuple[str, ...]
    reason: str
    candidate_set_id: str | None = None
    gap_id: str = ""
    mesh_proof_points: tuple[tuple[float, float], ...] = ()
    endpoint_coordinates: tuple[tuple[float, float], ...] = ()

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
        raw_proof_points = tuple(self.mesh_proof_points)
        if any(
            not isinstance(point, (tuple, list)) or len(point) != 2 for point in raw_proof_points
        ):
            raise ValueError("mesh proof points must contain coordinate pairs")
        proof_points = tuple((float(point[0]), float(point[1])) for point in raw_proof_points)
        object.__setattr__(self, "mesh_proof_points", proof_points)
        raw_endpoint_coordinates = tuple(self.endpoint_coordinates)
        if any(
            not isinstance(point, (tuple, list)) or len(point) != 2
            for point in raw_endpoint_coordinates
        ):
            raise ValueError("endpoint coordinates must contain coordinate pairs")
        endpoint_coordinates = tuple(
            (float(point[0]), float(point[1])) for point in raw_endpoint_coordinates
        )
        object.__setattr__(self, "endpoint_coordinates", endpoint_coordinates)


def _canonical_gaps(gaps: list[ReviewableNetworkGap]) -> tuple[ReviewableNetworkGap, ...]:
    by_obligation: dict[str, ReviewableNetworkGap] = {}
    for gap in sorted(
        gaps,
        key=lambda item: (
            item.obligation_id,
            item.gap_id,
            item.candidate_set_id or "",
            item.reason,
        ),
    ):
        existing = by_obligation.get(gap.obligation_id)
        if existing is not None:
            if existing.gap_id == gap.gap_id and existing != gap:
                raise ValueError("strategic network gap identity collision")
            continue
        by_obligation[gap.obligation_id] = gap
    return tuple(by_obligation[obligation_id] for obligation_id in sorted(by_obligation))


_ACCESS_SUPPORT_GAP_ROLES = frozenset(
    {
        "community-access",
        "community-access-obligation",
        "cross-spine-connector",
        "school-access",
        "school-access-obligation",
        "strategic-destination-access",
    }
)


def _canonical_gap_scope(
    request: StrategicNetworkPlanningRequest,
) -> tuple[set[str] | None, set[str], set[str]]:
    """Return the required and access gap identities for semantic publication.

    Direct planning requests have no preparation roster, so every unresolved
    obligation remains explicit.  A prepared regional run distinguishes the
    required A-road and urban-journey units from optional candidate attempts while
    retaining destination/access preparation issues for the support layer.
    """

    preparation = request.corridor_obligations
    if preparation is None:
        return None, set(), set()
    required_obligation_ids = {str(item) for item in request.backbone_obligation_ids}
    required_candidate_set_ids: set[str] = set()
    access_issue_ids: set[str] = set()
    for unit in tuple(getattr(preparation, "units", ())):
        if getattr(unit, "backbone_required", False) or getattr(unit, "urban_journey_id", None):
            required_obligation_ids.add(str(getattr(unit, "unit_id", "")))
            candidate_set = getattr(unit, "candidate_set", None)
            candidate_set_id = getattr(candidate_set, "candidate_set_id", None)
            if candidate_set_id:
                required_candidate_set_ids.add(str(candidate_set_id))
    for issue in tuple(getattr(preparation, "issues", ())):
        issue_id = getattr(issue, "obligation_id", None)
        role = getattr(getattr(issue, "unit_role", None), "value", None)
        if role == "a-road-backbone" and issue_id:
            required_obligation_ids.add(str(issue_id))
        elif role == "interurban-spine" and issue_id:
            if getattr(issue, "reason", None) == "urban-place-no-cross-region-adjacency":
                required_obligation_ids.add(str(issue_id))
        elif role == "strategic-destination-access" and issue_id:
            access_issue_ids.add(str(issue_id))
    return required_obligation_ids, required_candidate_set_ids, access_issue_ids


def _published_gaps(
    request: StrategicNetworkPlanningRequest,
    gaps: list[ReviewableNetworkGap],
    selected_obligation_ids: set[str],
) -> list[ReviewableNetworkGap]:
    required_ids, required_candidate_set_ids, access_issue_ids = _canonical_gap_scope(request)

    def selected_discovery_failure(gap: ReviewableNetworkGap) -> bool:
        if gap.obligation_id not in selected_obligation_ids:
            return False
        reason = gap.reason.casefold()
        return (
            reason == "no-path"
            or reason.startswith("all generated")
            or reason.startswith("all prepared")
            or reason.startswith("prepared candidate route")
            or reason.startswith("no admitted candidate set")
        )

    return [
        gap
        for gap in gaps
        if not selected_discovery_failure(gap)
        and (
            required_ids is None
            or gap.network_role.casefold() in _ACCESS_SUPPORT_GAP_ROLES
            or gap.network_role.casefold() == "strategic-main-network"
            or gap.obligation_id in required_ids
            or (gap.candidate_set_id or "") in required_candidate_set_ids
            or gap.obligation_id in access_issue_ids
        )
    ]


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
    mesh_profile_fingerprint: str | None


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
            # Governed node identity establishes graph continuity. Reprojection
            # can leave adjacent source-edge endpoint coordinates fractionally
            # different, so retain both exact source coordinates in the line.
            coordinates.extend(points)
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
    return min(candidates, key=lambda item: _compiler_candidate_sort_key(candidate_set, item))


def _compiler_candidate_sort_key(
    candidate_set: AlignmentCandidateSet,
    candidate: object,
) -> tuple[object, ...]:
    """Return the configured compiler ordering for one Candidate Set.

    Legacy profiles retain their existing source-precedence/directness/id
    comparator.  vNext keeps its governed reuse-first comparator unchanged.
    """

    profile = candidate_set.profile
    if profile.contract == "satn-network-selection-profile/vNext":
        return _reuse_first_sort_key(profile, candidate)
    precedence = {
        source: index for index, source in enumerate(candidate_set.candidate_source_precedence)
    }
    return (
        precedence.get(candidate.source_class, len(precedence)),
        candidate.directness_m,
        candidate.candidate_id,
    )


def _compiler_comparison_dimensions(candidate_set: AlignmentCandidateSet) -> tuple[object, ...]:
    profile = candidate_set.profile
    if profile.contract == "satn-network-selection-profile/vNext":
        return tuple(getattr(profile, "comparator_order", ()) or ())
    return ("candidate-source-precedence", "route-length", "stable-candidate-id")


def _candidate_comparison_label(candidate: object, dimension: object) -> str:
    """Render the governed fact used by one comparator dimension.

    This is deliberately a view of the existing lexicographic comparator.  It
    does not add a score or infer an admission rationale for the selected
    candidate.
    """

    name = str(getattr(dimension, "value", dimension))
    if name == "reuse-class":
        value = getattr(candidate, "reuse_class", None)
        return str(getattr(value, "value", value))
    if name == "candidate-source-precedence":
        value = getattr(candidate, "source_class", None)
        return str(getattr(value, "value", value))
    if name == "intervention-state":
        value = getattr(candidate, "intervention_state", None)
        return str(getattr(value, "value", value))
    if name in {"route-length", "route-detour"}:
        return f"{float(candidate.directness_m):g}m"
    if name == "route-effort":
        value = getattr(candidate, "total_absolute_elevation_change_m", None)
        return "unknown" if value is None else f"{float(value):g}m elevation"
    if name == "transition-fragmentation-burden":
        transitions = getattr(candidate, "transition_count", None)
        fragments = getattr(candidate, "fragmentation_count", None)
        if transitions is None or fragments is None:
            return "unknown"
        return f"{transitions + fragments} transitions/fragments"
    if name == "stable-candidate-id":
        return str(getattr(candidate, "candidate_id", "unknown candidate"))
    value = getattr(candidate, name.replace("-", "_"), None)
    return "unknown" if value is None else str(value)


def _compiler_pairwise_comparison_reason(
    candidate_set: AlignmentCandidateSet,
    selected: object,
    alternative: object,
) -> str:
    comparator_order = _compiler_comparison_dimensions(candidate_set)
    selected_key = _compiler_candidate_sort_key(candidate_set, selected)
    alternative_key = _compiler_candidate_sort_key(candidate_set, alternative)
    for dimension, selected_value, alternative_value in zip(
        comparator_order, selected_key, alternative_key, strict=True
    ):
        if selected_value == alternative_value:
            continue
        dimension_name = str(getattr(dimension, "value", dimension))
        if selected_value < alternative_value:
            return (
                f"{dimension_name} ranked candidate {selected.candidate_id} "
                f"({_candidate_comparison_label(selected, dimension)}) ahead of candidate "
                f"{alternative.candidate_id} "
                f"({_candidate_comparison_label(alternative, dimension)})"
            )
        return (
            f"{dimension_name} ranked candidate {alternative.candidate_id} "
            f"({_candidate_comparison_label(alternative, dimension)}) ahead of candidate "
            f"{selected.candidate_id} ({_candidate_comparison_label(selected, dimension)})"
        )
    return (
        f"governed comparator tied candidate {selected.candidate_id} and "
        f"candidate {alternative.candidate_id}"
    )


def _compiler_selection_reason(
    candidate_set: AlignmentCandidateSet,
    selected: object | None,
    supplied_preference: str | None,
) -> str:
    """Explain the actual compiler choice from the configured comparator."""

    if supplied_preference is not None:
        return "compiler selection: supplied preference; selection rationale unavailable"
    if selected is None:
        return "compiler selection: no admitted candidate"
    candidates = tuple(candidate_set.admitted_candidates)
    comparator_order = _compiler_comparison_dimensions(candidate_set)
    if not comparator_order:
        return "compiler selection: governed comparator unavailable"
    try:
        _compiler_candidate_sort_key(candidate_set, selected)
    except (AssertionError, KeyError, TypeError, ValueError):
        return "compiler selection: governed comparator unavailable"
    for alternative in sorted(
        (item for item in candidates if item.candidate_id != selected.candidate_id),
        key=lambda item: _compiler_candidate_sort_key(candidate_set, item),
    ):
        return "compiler selection: " + _compiler_pairwise_comparison_reason(
            candidate_set, selected, alternative
        )
    return "compiler selection: governed comparator selected the candidate"


def _compiler_alternative_comparison_reason(
    candidate_set: AlignmentCandidateSet,
    compiler: object | None,
    alternative: object,
    supplied_preference: str | None,
) -> str:
    alternative_id = str(alternative.candidate_id)
    if compiler is None:
        return (
            f"compiler comparison unavailable for candidate {alternative_id}: no compiler candidate"
        )
    compiler_id = str(compiler.candidate_id)
    if supplied_preference is not None:
        return (
            f"compiler comparison unavailable between candidate {compiler_id} and candidate "
            f"{alternative_id}: supplied preference rationale unavailable"
        )
    if compiler_id == alternative_id:
        return (
            f"compiler comparison: candidate {compiler_id} was compiler-preferred; "
            "another authority supplied the effective choice"
        )
    return "compiler comparison: " + _compiler_pairwise_comparison_reason(
        candidate_set, compiler, alternative
    )


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


def _active_officer_choice_metadata(
    ledger: object | None,
) -> dict[str, tuple[str, str | None, str | None]]:
    """Return rationale and attribution only for active ledger choices."""

    if ledger is None:
        return {}
    metadata: dict[str, tuple[str, str | None, str | None]] = {}
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
        if not isinstance(target_id, str) or not isinstance(decision_id, str):
            continue
        rationale = getattr(decision, "rationale", None)
        decision_maker = getattr(decision, "decision_maker", None)
        metadata[target_id] = (
            decision_id,
            rationale.strip() if isinstance(rationale, str) and rationale.strip() else None,
            decision_maker.strip()
            if isinstance(decision_maker, str) and decision_maker.strip()
            else None,
        )
    return metadata


def _officer_selection_details(
    candidate_id: str,
    decision_id: str,
    active_metadata: Mapping[str, tuple[str, str | None, str | None]],
) -> tuple[str, str, str | None]:
    ledger_choice = active_metadata.get(candidate_id)
    if ledger_choice is not None and ledger_choice[0] == decision_id:
        rationale = ledger_choice[1]
        return (
            f"officer decision: {rationale}"
            if rationale is not None
            else "officer decision: rationale unavailable",
            decision_id,
            ledger_choice[2],
        )
    return (
        "officer selection: supplied candidate choice; decision rationale unavailable",
        decision_id,
        None,
    )


def _display_state(intervention_state: object | None) -> str:
    value = getattr(intervention_state, "value", intervention_state)
    return {
        "existing-provision": "existing-provision",
        "upgrade-required": "upgrade-required",
        "proposed-new-link": "proposed-new-link",
        "undetermined": "unresolved-gap",
    }.get(str(value), "unresolved-gap")


def _mesh_corridor_class(section: EffectiveStrategicSection) -> str:
    bases = {str(item) for item in section.alignment_bases}
    if bases & {
        "current-ncn",
        "ncn-link",
        "greenway",
        "mapped-cycleway",
        "cycle-track",
        "shared-use-path",
    }:
        return "existing-cycleway"
    if section.primary_alignment_basis == "a-road" or "a-road" in bases:
        return "a-road"
    return "other"


def _planning_edge_corridor_class(edge: object) -> str:
    highway = str(getattr(edge, "highway", "") or "").casefold()
    bicycle = str(getattr(edge, "bicycle", "") or "").casefold()
    ref = str(getattr(edge, "ref", "") or "").casefold().replace(" ", "")
    if (
        getattr(edge, "asset_observation_ids", ())
        or highway == "cycleway"
        or bicycle in {"designated", "official"}
    ):
        return "existing-cycleway"
    if highway in {"trunk", "primary"} or ref.startswith("a"):
        return "a-road"
    return "other"


def _candidate_endpoint_components(
    candidates: tuple[CandidateRouteSection, ...],
) -> tuple[tuple[CandidateRouteSection, ...], ...]:
    by_id = {item.section_id: item for item in candidates}
    section_ids_by_node: dict[str, set[str]] = {}
    for candidate in candidates:
        for node_id in candidate.endpoint_ids:
            section_ids_by_node.setdefault(node_id, set()).add(candidate.section_id)
    remaining = set(by_id)
    components: list[tuple[CandidateRouteSection, ...]] = []
    while remaining:
        pending = [min(remaining)]
        component_ids: set[str] = set()
        while pending:
            section_id = pending.pop()
            if section_id in component_ids:
                continue
            component_ids.add(section_id)
            for node_id in by_id[section_id].endpoint_ids:
                pending.extend(sorted(section_ids_by_node[node_id], reverse=True))
        remaining.difference_update(component_ids)
        components.append(tuple(by_id[item] for item in sorted(component_ids)))
    return tuple(components)


def _main_continuity_sections(
    graph: PlanningGraphSnapshot,
    candidates: tuple[CandidateRouteSection, ...],
    coverage_points: tuple[MeshCoveragePoint, ...],
    profile: StrategicMainNetworkProfile,
) -> tuple[tuple[CandidateRouteSection, ...], tuple[EffectiveStrategicSection, ...]]:
    """Connect reachable Main components through the governed routable graph.

    The connector search is lexicographic: existing cycle provision first,
    A-road corridors second, and other routable edges only where continuity
    requires them. Unreachable components are left for the mesh kernel to
    expose as gaps.
    """

    main_candidates = tuple(item for item in candidates if not item.is_access_support)
    components = _candidate_endpoint_components(main_candidates)
    if len(components) <= 1:
        return (), ()
    root_selection = assemble_strategic_main_network(
        StrategicMainNetworkRequest(
            route_sections=main_candidates,
            coverage_points=coverage_points,
            profile=profile,
        )
    )
    if not root_selection.selected_section_ids:
        return (), ()
    root_id = root_selection.selected_section_ids[0]
    root_index = next(
        index
        for index, component in enumerate(components)
        if any(item.section_id == root_id for item in component)
    )
    component_index_by_section_id = {
        item.section_id: index for index, component in enumerate(components) for item in component
    }
    selected_component_order = {
        component_index_by_section_id[section_id]: order
        for order, section_id in enumerate(root_selection.selected_section_ids)
        if section_id in component_index_by_section_id
    }

    records_by_endpoints: dict[tuple[str, str], list[object]] = {}
    for edge in graph.edge_records:
        if edge.reciprocal_state != "reciprocal" or edge.access in {
            "no",
            "private",
            "customers",
        }:
            continue
        left, right = sorted((edge.from_node_id, edge.to_node_id))
        records_by_endpoints.setdefault((left, right), []).append(edge)

    physical_edges: list[tuple[str, str, str, int, str, dict[tuple[str, str], str]]] = []
    corridor_order = {"existing-cycleway": 0, "a-road": 1, "other": 2}
    for (left, right), records in sorted(records_by_endpoints.items()):
        records_by_direction = {
            direction: tuple(
                edge for edge in records if (edge.from_node_id, edge.to_node_id) == direction
            )
            for direction in ((left, right), (right, left))
        }
        if any(not items for items in records_by_direction.values()):
            continue
        chosen = {
            direction: min(
                items,
                key=lambda item: (
                    corridor_order[_planning_edge_corridor_class(item)],
                    item.length_mm,
                    item.source_edge_id,
                    item.directed_edge_id,
                ),
            )
            for direction, items in records_by_direction.items()
        }
        representative = min(
            chosen.values(),
            key=lambda item: (
                corridor_order[_planning_edge_corridor_class(item)],
                item.length_mm,
                item.source_edge_id,
                item.directed_edge_id,
            ),
        )
        source_id = "|".join(sorted({item.source_edge_id for item in chosen.values()}))
        physical_edges.append(
            (
                left,
                right,
                source_id,
                int(representative.length_mm),
                _planning_edge_corridor_class(representative),
                {direction: edge.directed_edge_id for direction, edge in chosen.items()},
            )
        )
    if not physical_edges:
        return (), ()

    # The base is derived from the complete eligible graph length, making the
    # scalar Dijkstra weight exactly preserve the governed lexicographic order.
    base = sum(item[3] for item in physical_edges) + 1
    route_graph = nx.Graph()
    for left, right, source_id, length_mm, corridor_class, directed in physical_edges:
        class_weight = {
            "existing-cycleway": length_mm,
            "a-road": length_mm * base + length_mm,
            "other": length_mm * base * base + length_mm,
        }[corridor_class]
        choice = (class_weight, source_id)
        existing = route_graph.get_edge_data(left, right)
        if existing is not None and existing["choice"] <= choice:
            continue
        route_graph.add_edge(
            left,
            right,
            choice=choice,
            weight=class_weight,
            corridor_class=corridor_class,
            directed=directed,
        )

    component_nodes = tuple(
        frozenset(node_id for item in component for node_id in item.endpoint_ids)
        for component in components
    )
    endpoint_coordinates = tuple(
        {
            node_id: tuple(
                sorted(
                    {
                        coordinates
                        for item in component
                        for candidate_node, coordinates in (
                            (item.start_node_id, item.coordinates[0]),
                            (item.end_node_id, item.coordinates[-1]),
                        )
                        if candidate_node == node_id
                    }
                )
            )
            for node_id in component_nodes[index]
        }
        for index, component in enumerate(components)
    )
    connected_nodes = set(component_nodes[root_index])
    connected_components = {root_index}
    remaining = set(range(len(components))) - {root_index}
    connector_candidates: list[CandidateRouteSection] = []
    connector_sections: list[EffectiveStrategicSection] = []
    while remaining:
        sources = sorted(connected_nodes.intersection(route_graph))
        if not sources:
            next_root = min(
                remaining,
                key=lambda index: (selected_component_order.get(index, len(components)), index),
            )
            remaining.remove(next_root)
            connected_nodes = set(component_nodes[next_root])
            connected_components = {next_root}
            continue
        distances, paths = nx.multi_source_dijkstra(
            route_graph,
            sources,
            weight="weight",
        )
        reachable = [
            (distances[node_id], node_id, component_index)
            for component_index in remaining
            for node_id in component_nodes[component_index]
            if node_id in distances
        ]
        if not reachable:
            next_root = min(
                remaining,
                key=lambda index: (selected_component_order.get(index, len(components)), index),
            )
            remaining.remove(next_root)
            connected_nodes = set(component_nodes[next_root])
            connected_components = {next_root}
            continue
        _distance, target_node, component_index = min(reachable)
        path_nodes = paths[target_node]
        if len(path_nodes) < 2:
            remaining.remove(component_index)
            connected_nodes.update(component_nodes[component_index])
            continue
        forward_ids: list[str] = []
        reverse_ids: list[str] = []
        classes: list[str] = []
        for start_node, end_node in pairwise(path_nodes):
            edge_data = route_graph[start_node][end_node]
            directed = edge_data["directed"]
            forward_ids.append(directed[(start_node, end_node)])
            reverse_ids.insert(0, directed[(end_node, start_node)])
            classes.append(edge_data["corridor_class"])
        forward = tuple(forward_ids)
        reverse = tuple(reverse_ids)
        endpoints = (path_nodes[0], path_nodes[-1])
        geometry_wkt = _edge_geometry(graph, forward, endpoints=endpoints)
        _reverse_edge_chain(graph, forward, reverse, endpoints)
        route_geometry = load_wkt(geometry_wkt)
        route_coordinates = list(route_geometry.coords)
        start_options = tuple(
            coordinate
            for index in sorted(connected_components)
            for coordinate in endpoint_coordinates[index].get(endpoints[0], ())
        )
        end_options = endpoint_coordinates[component_index].get(endpoints[1], ())
        start_anchor = min(
            start_options,
            key=lambda coordinate: (
                Point(coordinate).distance(Point(route_coordinates[0])),
                coordinate,
            ),
        )
        end_anchor = min(
            end_options,
            key=lambda coordinate: (
                Point(coordinate).distance(Point(route_coordinates[-1])),
                coordinate,
            ),
        )
        if route_coordinates[0] != start_anchor:
            route_coordinates.insert(0, start_anchor)
        if route_coordinates[-1] != end_anchor:
            route_coordinates.append(end_anchor)
        geometry_wkt = LineString(route_coordinates).wkt
        section_id = _stable_id("strategic-main-continuity", forward)
        corridor_class = (
            "other"
            if "other" in classes
            else "a-road"
            if "a-road" in classes
            else "existing-cycleway"
        )
        bases = tuple(
            basis
            for corridor, basis in (
                ("existing-cycleway", "mapped-cycleway"),
                ("a-road", "a-road"),
                ("other", "other-routable"),
            )
            if corridor in classes
        )
        geometry = load_wkt(geometry_wkt)
        connector_candidates.append(
            CandidateRouteSection(
                section_id=section_id,
                start_node_id=endpoints[0],
                end_node_id=endpoints[1],
                coordinates=tuple((float(x), float(y)) for x, y in geometry.coords),
                corridor_class=corridor_class,
                network_role="strategic-main-connector",
                network_scope="rural",
            )
        )
        connector_sections.append(
            EffectiveStrategicSection(
                section_id=section_id,
                obligation_id=f"strategic-main-continuity:{section_id}",
                candidate_id=None,
                network_role="strategic-main-connector",
                routing_edge_ids=forward,
                reverse_routing_edge_ids=reverse,
                geometry_wkt=geometry_wkt,
                authority=PlanningAuthority.COMPILER,
                alignment_bases=bases,
                primary_alignment_basis=bases[0],
                intervention_state=(
                    "existing-provision"
                    if corridor_class == "existing-cycleway"
                    else "upgrade-required"
                ),
                display_state=(
                    "existing-provision"
                    if corridor_class == "existing-cycleway"
                    else "upgrade-required"
                ),
                network_scope="rural",
            )
        )
        connected_nodes.update(component_nodes[component_index])
        connected_components.add(component_index)
        remaining.remove(component_index)
    return tuple(connector_candidates), tuple(connector_sections)


def _mesh_materialized_sections(
    request: StrategicNetworkPlanningRequest,
    sections: tuple[EffectiveStrategicSection, ...],
) -> tuple[
    tuple[EffectiveStrategicSection, ...],
    tuple[PlanningDiagnostic, ...],
    tuple[MeshGap, ...],
    tuple[MeshCoveragePoint, ...],
]:
    """Reduce all materialized main routes at the sole planning boundary.

    Candidate and governed-reference routes are only available after fallback
    selection. Access Support remains effective, but is excluded from Main
    coverage by the mesh kernel.
    """

    if not sections:
        return (), (), (), ()
    edge_by_id = {edge.directed_edge_id: edge for edge in request.graph.edge_records}
    candidates: list[CandidateRouteSection] = []
    normalized_sections: list[EffectiveStrategicSection] = []
    for section in sections:
        geometry = load_wkt(section.geometry_wkt)
        if not isinstance(geometry, LineString) or geometry.is_empty:
            raise ValueError(f"mesh section geometry is not a non-empty line: {section.section_id}")
        is_access_support = section.network_role.casefold() in {
            "community-access",
            "school-access",
            "strategic-destination-access",
            "cross-spine-connector",
        }
        if is_access_support:
            first_node_id = f"access-support:{section.section_id}:start"
            last_node_id = f"access-support:{section.section_id}:end"
        else:
            if not section.routing_edge_ids:
                raise ValueError(f"mesh section has no planning edges: {section.section_id}")
            first_edge = edge_by_id.get(section.routing_edge_ids[0])
            last_edge = edge_by_id.get(section.routing_edge_ids[-1])
            if first_edge is None or last_edge is None:
                missing = next(
                    edge_id for edge_id in section.routing_edge_ids if edge_id not in edge_by_id
                )
                raise ValueError(f"mesh section edge is absent: {missing}")
            first_node_id = first_edge.from_node_id
            last_node_id = last_edge.to_node_id
        # A-road backbone units use the interurban role for publication, while
        # their governed unit scope may still be urban. Preserve that explicit
        # scope; only the legacy default for ordinary interurban sections is
        # normalized to rural at construction.
        scope = section.network_scope
        normalized = replace(section, network_scope=scope)
        normalized_sections.append(normalized)
        candidates.append(
            CandidateRouteSection(
                section_id=normalized.section_id,
                start_node_id=first_node_id,
                end_node_id=last_node_id,
                coordinates=tuple((float(x), float(y)) for x, y in geometry.coords),
                corridor_class=_mesh_corridor_class(normalized),
                network_role=normalized.network_role,
                is_access_support=is_access_support,
                network_scope=scope,
            )
        )
    coverage_points = derive_mesh_coverage_points(
        tuple(candidate for candidate in candidates if not candidate.is_access_support),
        profile=request.mesh_profile,
    )
    continuity_candidates, continuity_sections = _main_continuity_sections(
        request.graph,
        tuple(candidates),
        coverage_points,
        request.mesh_profile,
    )
    candidates.extend(continuity_candidates)
    normalized_sections.extend(continuity_sections)

    selected_candidate_ids = {
        section.candidate_id for section in sections if section.candidate_id is not None
    }
    candidate_records = {
        record.candidate_id: record for record in request.discovery.candidate_records
    }
    candidate_set_by_id = {
        candidate.candidate_id: candidate_set
        for candidate_set in request.discovery.candidate_sets
        for candidate in candidate_set.candidates
    }
    admitted_candidate_ids = {
        candidate.candidate_id
        for candidate_set in request.discovery.candidate_sets
        for candidate in candidate_set.admitted_candidates
    }
    urban_journey_obligation_ids = {
        unit.unit_id
        for unit in getattr(request.corridor_obligations, "units", ())
        if getattr(unit, "urban_journey_id", None)
    }

    def _a_section_replaced_by_compared_candidate(
        section: EffectiveStrategicSection,
    ) -> bool:
        if (
            section.network_role.casefold() != "urban-main-road-spine"
            or "a-road" not in section.alignment_bases
            or section.candidate_id is not None
            or not section.routing_edge_ids
        ):
            return False
        first_edge = edge_by_id.get(section.routing_edge_ids[0])
        last_edge = edge_by_id.get(section.routing_edge_ids[-1])
        if first_edge is None or last_edge is None:
            return False
        section_endpoints = (first_edge.from_node_id, last_edge.to_node_id)
        for record in request.discovery.candidate_records:
            if record.candidate_id not in admitted_candidate_ids:
                continue
            if record.candidate_id in selected_candidate_ids:
                continue
            if record.edge_ids != section.routing_edge_ids:
                continue
            if record.endpoints != section_endpoints:
                continue
            if record.primary_alignment_basis != "a-road" and (
                "a-road" not in record.alignment_bases
            ):
                continue
            candidate_set = candidate_set_by_id.get(record.candidate_id)
            if candidate_set is None:
                continue
            selected_ids_in_set = {
                candidate.candidate_id
                for candidate in candidate_set.admitted_candidates
                if candidate.candidate_id in selected_candidate_ids
            }
            if not selected_ids_in_set:
                continue
            candidate_role = str(
                getattr(candidate_set.network_role, "value", candidate_set.network_role)
            ).casefold()
            if candidate_role != "interurban-spine":
                continue
            if any(
                selected_id != record.candidate_id
                and candidate_records.get(selected_id) is not None
                and candidate_records[selected_id].endpoints == record.endpoints
                and candidate_records[selected_id].network_role == record.network_role
                for selected_id in selected_ids_in_set
            ):
                return True
        return False

    protected_section_ids = {
        section.section_id
        for section in normalized_sections
        if section.obligation_id in request.backbone_obligation_ids
        or section.obligation_id in urban_journey_obligation_ids
        or (
            section.network_role.casefold() == "urban-main-road-spine"
            and "a-road" in section.alignment_bases
            and not _a_section_replaced_by_compared_candidate(section)
        )
    }
    assembly = assemble_strategic_main_network(
        StrategicMainNetworkRequest(
            route_sections=tuple(candidates),
            coverage_points=coverage_points,
            profile=request.mesh_profile,
            preserve_connected_components=bool(protected_section_ids),
            protected_section_ids=tuple(sorted(protected_section_ids)),
        )
    )
    selected_ids = set(assembly.selected_section_ids)
    selected = tuple(
        section for section in normalized_sections if section.section_id in selected_ids
    )
    diagnostics: list[PlanningDiagnostic] = []
    for gap in assembly.gaps:
        diagnostics.append(
            PlanningDiagnostic(
                "strategic-mesh-gap",
                gap.gap_id,
                f"{gap.scope} mesh coverage is not proved: {gap.reason}",
            )
        )
    for section in normalized_sections:
        if section.section_id in selected_ids:
            continue
        if section.section_id in assembly.access_support_section_ids:
            selected = (*selected, section)
            continue
        diagnostics.append(
            PlanningDiagnostic(
                "strategic-mesh-section-omitted",
                section.section_id,
                "section is outside the selected Strategic Main Network mesh",
            )
        )
    return (
        tuple(sorted(selected, key=lambda item: item.section_id)),
        tuple(diagnostics),
        assembly.gaps,
        coverage_points,
    )


def _resolved_backbone_component_gap_ids(
    request: StrategicNetworkPlanningRequest,
    sections: tuple[EffectiveStrategicSection, ...],
) -> set[str]:
    """Return preparation component gaps bridged by selected Main sections.

    Preparation records identify the official component groups, while the
    selected Planning Graph edge chains prove whether the effective result
    actually traverses those groups.  A source-only component diagnostic must
    not survive as a final gap once that proof exists.
    """

    preparation = request.corridor_obligations
    if preparation is None:
        return set()
    units = tuple(getattr(preparation, "units", ()))
    issues = tuple(getattr(preparation, "issues", ()))
    component_ids = sorted(
        {
            str(component_id)
            for unit in units
            for component_id in tuple(getattr(unit, "backbone_component_ids", ()))
        }
        | {
            str(component_id)
            for issue in issues
            if getattr(issue, "reason", None) == "a-road-backbone-component-unconnected"
            for component_id in tuple(getattr(issue, "component_ids", ()))
        }
    )
    if not component_ids:
        return set()
    parent = {component_id: component_id for component_id in component_ids}

    def find(component_id: str) -> str:
        root = component_id
        while parent[root] != root:
            root = parent[root]
        while parent[component_id] != component_id:
            next_id = parent[component_id]
            parent[component_id] = root
            component_id = next_id
        return root

    def union(component_group: tuple[str, ...]) -> None:
        roots = sorted({find(component_id) for component_id in component_group})
        if len(roots) < 2:
            return
        for root in roots[1:]:
            parent[root] = roots[0]

    edge_by_id = {edge.directed_edge_id: edge for edge in request.graph.edge_records}
    routing_components: dict[str, set[str]] = {}
    for unit in units:
        component_group = tuple(
            str(component_id) for component_id in tuple(getattr(unit, "backbone_component_ids", ()))
        )
        # A junction-context unit owns a two-component proof only when that
        # unit itself is selected.  Attaching both IDs to both endpoints would
        # let an unrelated route touching one endpoint resolve the other side.
        if not component_group or len(component_group) != 1:
            continue
        for node_id in (
            getattr(unit, "routing_start_node_id", ""),
            getattr(unit, "routing_end_node_id", ""),
        ):
            routing_components.setdefault(str(node_id), set()).update(component_group)

    for section in sections:
        if section.obligation_id in {getattr(unit, "unit_id", None) for unit in units}:
            unit = next(
                (item for item in units if getattr(item, "unit_id", None) == section.obligation_id),
                None,
            )
            if unit is not None:
                union(
                    tuple(
                        str(component_id)
                        for component_id in tuple(getattr(unit, "backbone_component_ids", ()))
                    )
                )
        traversed_components: set[str] = set()
        for edge_id in section.routing_edge_ids:
            edge = edge_by_id.get(edge_id)
            if edge is None:
                continue
            traversed_components.update(routing_components.get(edge.from_node_id, ()))
            traversed_components.update(routing_components.get(edge.to_node_id, ()))
        union(tuple(sorted(traversed_components)))

    resolved: set[str] = set()
    for issue in issues:
        if getattr(issue, "reason", None) != "a-road-backbone-component-unconnected":
            continue
        issue_components = tuple(
            str(component_id) for component_id in tuple(getattr(issue, "component_ids", ()))
        )
        if (
            len(issue_components) > 1
            and len({find(component_id) for component_id in issue_components}) == 1
        ):
            obligation_id = getattr(issue, "obligation_id", None)
            if obligation_id:
                resolved.add(str(obligation_id))
    return resolved


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
    supplied_diagnostics = request.network_diagnostics
    diagnostics.extend(
        item
        for item in getattr(supplied_diagnostics, "diagnostics", ())
        if isinstance(item, PlanningDiagnostic)
    )
    gaps.extend(
        item
        for item in getattr(supplied_diagnostics, "gaps", ())
        if isinstance(item, ReviewableNetworkGap)
    )
    required_sections = tuple(request.required_sections)
    preparation_units = tuple(getattr(request.corridor_obligations, "units", ()))
    preparation_issues = tuple(getattr(request.corridor_obligations, "issues", ()))
    endpoint_coordinates_by_obligation = {
        str(getattr(item, "unit_id", "")): tuple(getattr(item, "endpoint_coordinates", ()))
        for item in preparation_units
        if getattr(item, "endpoint_coordinates", ())
    }
    endpoint_coordinates_by_obligation.update(
        {
            str(getattr(item, "obligation_id", "")): tuple(
                getattr(item, "endpoint_coordinates", ())
            )
            for item in preparation_issues
            if getattr(item, "obligation_id", None) and getattr(item, "endpoint_coordinates", ())
        }
    )
    for gap in sorted(discovery.gaps, key=lambda item: item.obligation_id):
        issue = next(
            (
                item
                for item in preparation_issues
                if getattr(item, "obligation_id", None) == gap.obligation_id
            ),
            None,
        )
        role = str(
            getattr(gap, "network_role", None)
            or getattr(issue, "network_role", None)
            or "unresolved-strategic-alignment"
        )
        reviewable_gap = ReviewableNetworkGap(
            gap.obligation_id,
            role,
            tuple(gap.endpoints),
            gap.reason,
            endpoint_coordinates=endpoint_coordinates_by_obligation.get(gap.obligation_id, ()),
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
    active_officer_metadata = _active_officer_choice_metadata(request.officer_decisions)
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
    sections: list[EffectiveStrategicSection] = list(required_sections)
    selected_ids: set[str] = set()
    effective_roles: set[str] = {section.network_role for section in required_sections}

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
        compiler_reason = _compiler_selection_reason(
            candidate_set,
            compiler,
            governed_preference,
        )
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
        selection_reason = compiler_reason
        decision_id: str | None = None
        decision_maker: str | None = None
        if authority is PlanningAuthority.OFFICER and officer_id is not None:
            officer_decision_ids = officer_by_candidate.get(officer_id, ())
            if len(officer_decision_ids) == 1:
                selection_reason, decision_id, decision_maker = _officer_selection_details(
                    officer_id,
                    officer_decision_ids[0],
                    active_officer_metadata,
                )
            else:
                selection_reason = (
                    "officer selection: multiple decision attributions; rationale unavailable"
                )
        elif authority is PlanningAuthority.GOVERNED_REFERENCE:
            selection_reason = (
                "governed reference route: supplied fallback; compiler comparison unavailable"
            )
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
                    selection_reason=selection_reason,
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
                    network_scope=(
                        "rural" if str(role).casefold() == "interurban-spine" else "urban"
                    ),
                )
            )
            effective_roles.add(str(role))
        elif effective is None:
            reason = binding_reason_by_set.get(
                set_id, "no admitted Candidate Set candidate or governed reference route"
            )
            gaps.append(
                ReviewableNetworkGap(
                    obligation_id,
                    str(role),
                    endpoints,
                    reason,
                    candidate_set.candidate_set_id,
                    endpoint_coordinates=endpoint_coordinates_by_obligation.get(obligation_id, ()),
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
                        endpoint_coordinates=endpoint_coordinates_by_obligation.get(
                            obligation_id, ()
                        ),
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
                        selection_reason=selection_reason,
                        decision_id=decision_id,
                        decision_maker=decision_maker,
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
                        network_scope=(
                            getattr(record.sections[0], "network_scope", None)
                            or ("rural" if str(role).casefold() == "interurban-spine" else "urban")
                        ),
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
                disposition, reason, comparison_reason = (
                    "effective",
                    "candidate is effective in the immutable strategic network",
                    None,
                )
            elif getattr(admission.disposition, "value", admission.disposition) == "admitted":
                disposition, reason, comparison_reason = (
                    "unselected",
                    "admitted alternative retained for review",
                    _compiler_alternative_comparison_reason(
                        candidate_set,
                        compiler,
                        candidate,
                        governed_preference,
                    ),
                )
            else:
                disposition, reason, comparison_reason = (
                    "rejected",
                    getattr(admission.rationale, "value", str(admission.rationale)),
                    None,
                )
            dispositions.append(
                CandidateDisposition(
                    candidate_set.candidate_set_id,
                    obligation_id,
                    candidate.candidate_id,
                    disposition,
                    reason,
                    comparison_reason,
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

    mesh_sections, mesh_diagnostics, mesh_gaps, mesh_coverage_points = _mesh_materialized_sections(
        request, tuple(sections)
    )
    mesh_omitted_ids = {section.section_id for section in sections} - {
        section.section_id for section in mesh_sections
    }
    mesh_omitted_obligation_ids = {
        section.obligation_id for section in sections if section.section_id in mesh_omitted_ids
    }
    diagnostics.extend(mesh_diagnostics)
    coverage_points_by_id = {point.point_id: point for point in mesh_coverage_points}
    mesh_gap_groups: dict[tuple[str, str], list[MeshGap]] = {}
    for mesh_gap in mesh_gaps:
        mesh_gap_groups.setdefault((mesh_gap.scope, mesh_gap.reason), []).append(mesh_gap)
    for (scope, reason), grouped_gaps in sorted(mesh_gap_groups.items()):
        proof_points = tuple(
            coverage_points_by_id[item.coverage_point_id].coordinates
            for item in sorted(grouped_gaps, key=lambda value: value.coverage_point_id)
        )
        gaps.append(
            ReviewableNetworkGap(
                f"strategic-mesh:{scope}:{reason}",
                "strategic-main-network",
                ("", ""),
                f"{scope} mesh coverage is not proved at {len(grouped_gaps)} proof points: "
                f"{reason}",
                mesh_proof_points=proof_points,
            )
        )
    if mesh_omitted_ids:
        selected_ids.difference_update(mesh_omitted_ids)
        selections = [
            selection
            for selection in selections
            if selection.effective_candidate_id not in mesh_omitted_ids
            and selection.obligation_id not in mesh_omitted_obligation_ids
        ]
        dispositions = [
            replace(
                disposition,
                disposition="unselected",
                reason="omitted by Strategic Main Network mesh",
            )
            if disposition.candidate_id in mesh_omitted_ids
            else disposition
            for disposition in dispositions
        ]
    sections = list(mesh_sections)
    effective_roles = {section.network_role for section in sections}

    resolved_backbone_component_gaps = _resolved_backbone_component_gap_ids(
        request, tuple(sections)
    )
    if resolved_backbone_component_gaps:
        gaps = [gap for gap in gaps if gap.obligation_id not in resolved_backbone_component_gaps]

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

    selected_obligation_ids = {item.obligation_id for item in selections}
    canonical_gaps = _canonical_gaps(_published_gaps(request, gaps, selected_obligation_ids))
    status = (
        "complete-with-gaps"
        if canonical_gaps
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
        mesh_profile_fingerprint=request.mesh_profile_fingerprint,
    )
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
