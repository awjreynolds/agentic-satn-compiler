"""Provider-neutral graph quality and reachability diagnostics.

Diagnostics are deliberately projections of a :class:`PlanningGraphSnapshot`.
They never mutate geometry, repair connectivity, select a route, or collapse an
unknown claim into a favourable value.  The small network/obligation views in
this module are temporary authority-neutral contracts until strategic selection
lands its own effective-network types.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import networkx as nx
from shapely import wkt
from shapely.geometry import Point

from satn.evidence_contracts import evidence_fingerprint
from satn.planning_graph import PlanningEdgeRecord, PlanningGraphSnapshot


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def _crs_identity(value: object) -> str:
    from pyproj import CRS

    try:
        crs = CRS.from_user_input(value)
    except Exception as error:
        raise ValueError("network diagnostics require an explicit valid CRS") from error
    authority = crs.to_authority()
    return f"{authority[0]}:{authority[1]}" if authority else crs.to_wkt(pretty=False)


def _number(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return float(value)


def _identity_value(value: object) -> object:
    """Convert diagnostic measurements to explicit text for evidence identity."""

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("diagnostic identity cannot contain non-finite measurements")
        normalized = format(value, ".9f").rstrip("0").rstrip(".")
        return normalized or "0"
    if isinstance(value, Mapping):
        return {str(key): _identity_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_identity_value(item) for item in value]
    return value


@dataclass(frozen=True)
class NetworkDiagnosticProfile:
    """Named diagnostic assumptions and no implicit quality score."""

    profile_id: str
    canonical_crs: str = "EPSG:27700"
    version: int = 1
    permitted_access_states: tuple[str, ...] = ("yes", "permitted", "designated")
    crossing_claims: tuple[str, ...] = ("crossing", "evidenced-crossing")
    unknown_policy: Literal["retain-as-unknown-and-report"] = "retain-as-unknown-and-report"
    maximum_path_length_m: float | None = None

    def __post_init__(self) -> None:
        _required_text(self.profile_id, "profile id")
        if self.version < 1:
            raise ValueError("profile version must be positive")
        object.__setattr__(self, "canonical_crs", _crs_identity(self.canonical_crs))
        if self.canonical_crs != "EPSG:27700":
            raise ValueError("network diagnostics canonical CRS must be EPSG:27700")
        if self.unknown_policy != "retain-as-unknown-and-report":
            raise ValueError("unknown policy must retain and report unknown claims")
        states = tuple(
            sorted(
                _required_text(item, "permitted access state")
                for item in self.permitted_access_states
            )
        )
        claims = tuple(
            sorted(_required_text(item, "crossing claim") for item in self.crossing_claims)
        )
        if len(states) != len(set(states)) or len(claims) != len(set(claims)):
            raise ValueError("diagnostic profile claims cannot contain duplicates")
        object.__setattr__(self, "permitted_access_states", states)
        object.__setattr__(self, "crossing_claims", claims)
        object.__setattr__(
            self,
            "maximum_path_length_m",
            _number(self.maximum_path_length_m, "maximum path length"),
        )

    @property
    def fingerprint(self) -> str:
        return evidence_fingerprint(
            _identity_value(
                {
                    "contract": "satn-network-diagnostic-profile/v1",
                    "profile_id": self.profile_id,
                    "canonical_crs": self.canonical_crs,
                    "version": self.version,
                    "permitted_access_states": self.permitted_access_states,
                    "crossing_claims": self.crossing_claims,
                    "unknown_policy": self.unknown_policy,
                    "maximum_path_length_m": self.maximum_path_length_m,
                }
            )
        )


@dataclass(frozen=True)
class DiagnosticNetworkView:
    """Authority-neutral selection and release comparison view."""

    edge_ids: tuple[str, ...] | None = None
    place_node_ids: tuple[str, ...] = ()
    before_edge_ids: tuple[str, ...] | None = None
    after_edge_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        for name in ("edge_ids", "before_edge_ids", "after_edge_ids"):
            values = getattr(self, name)
            if values is None:
                continue
            canonical = tuple(sorted(_required_text(item, f"{name} item") for item in values))
            if len(canonical) != len(set(canonical)):
                raise ValueError(f"{name} cannot contain duplicates")
            object.__setattr__(self, name, canonical)
        places = tuple(
            sorted(_required_text(item, "place node id") for item in self.place_node_ids)
        )
        if len(places) != len(set(places)):
            raise ValueError("place node ids cannot contain duplicates")
        object.__setattr__(self, "place_node_ids", places)


@dataclass(frozen=True)
class DiagnosticObligation:
    obligation_id: str
    origin_node_id: str
    destination_node_id: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required_text(self.obligation_id, "obligation id")
        _required_text(self.origin_node_id, "origin node id")
        _required_text(self.destination_node_id, "destination node id")
        evidence = tuple(
            sorted(_required_text(item, "obligation evidence id") for item in self.evidence_ids)
        )
        if len(evidence) != len(set(evidence)):
            raise ValueError("obligation evidence ids cannot contain duplicates")
        object.__setattr__(self, "evidence_ids", evidence)


@dataclass(frozen=True)
class ComponentDiagnostic:
    component_id: str
    kind: Literal["weak", "strong"]
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]


@dataclass(frozen=True)
class DegreeDangleDiagnostic:
    node_id: str
    kind: Literal["dangle", "isolated"]
    degree: int
    edge_ids: tuple[str, ...]


@dataclass(frozen=True)
class BridgeCutDiagnostic:
    source_edge_id: str
    directed_edge_ids: tuple[str, ...]
    from_node_id: str
    to_node_id: str


@dataclass(frozen=True)
class ReciprocalAccessDiagnostic:
    source_edge_id: str
    state: Literal["one-way", "unknown", "conflicting"]
    directed_edge_ids: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class DirectnessDiagnostic:
    obligation_id: str
    numerator_m: float | None
    denominator_m: float | None
    circuity: float | None
    state: Literal["available", "unknown", "no-path"]
    failed_reason: str | None


@dataclass(frozen=True)
class ReachabilityDiagnostic:
    source_node_id: str
    reachable_node_ids: tuple[str, ...]
    reachable_edge_ids: tuple[str, ...]
    unknown_edge_ids: tuple[str, ...]
    blocked_edge_ids: tuple[str, ...]
    assumptions: tuple[str, ...]


@dataclass(frozen=True)
class SeveranceDiagnostic:
    source_edge_id: str
    affected_obligation_ids: tuple[str, ...]
    crossing_assumption: Literal["evidenced", "conflicting", "unknown", "none"]
    state: Literal["severance", "barrier", "unknown"]


@dataclass(frozen=True)
class WitnessDiagnostic:
    obligation_id: str
    state: Literal["found", "no-path", "unknown"]
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    distance_m: float | None
    failed_reason: str | None


@dataclass(frozen=True)
class DeltaDiagnostic:
    edge_id: str
    change: Literal["added", "removed"]


@dataclass(frozen=True)
class NetworkDiagnosticIssue:
    code: str
    subject_id: str
    message: str


@dataclass(frozen=True)
class NetworkDiagnosticResult:
    profile: NetworkDiagnosticProfile
    graph_fingerprint: str
    fingerprint: str
    components: tuple[ComponentDiagnostic, ...]
    degree_dangles: tuple[DegreeDangleDiagnostic, ...]
    bridge_cuts: tuple[BridgeCutDiagnostic, ...]
    reciprocal_access: tuple[ReciprocalAccessDiagnostic, ...]
    directness: tuple[DirectnessDiagnostic, ...]
    reachability: tuple[ReachabilityDiagnostic, ...]
    severance: tuple[SeveranceDiagnostic, ...]
    witnesses: tuple[WitnessDiagnostic, ...]
    delta: tuple[DeltaDiagnostic, ...]
    diagnostics: tuple[NetworkDiagnosticIssue, ...]

    @property
    def weak_components(self) -> tuple[ComponentDiagnostic, ...]:
        return tuple(item for item in self.components if item.kind == "weak")

    @property
    def strong_components(self) -> tuple[ComponentDiagnostic, ...]:
        return tuple(item for item in self.components if item.kind == "strong")


@dataclass(frozen=True)
class _ParsedEdge:
    record: PlanningEdgeRecord
    geometry: object


def _network_edge_ids(network: object, all_ids: set[str]) -> tuple[str, ...]:
    if network is None:
        return tuple(sorted(all_ids))
    if isinstance(network, Mapping):
        value = network.get("edge_ids", network.get("selected_edge_ids"))
    else:
        value = getattr(network, "edge_ids", getattr(network, "selected_edge_ids", None))
    if value is None:
        return tuple(sorted(all_ids))
    selected = tuple(sorted(str(item) for item in value if str(item) in all_ids))
    return selected


def _requested_network_edge_ids(network: object) -> tuple[str, ...] | None:
    if network is None:
        return None
    if isinstance(network, Mapping):
        value = network.get("edge_ids", network.get("selected_edge_ids"))
    else:
        value = getattr(network, "edge_ids", getattr(network, "selected_edge_ids", None))
    if value is None:
        return None
    return tuple(sorted(str(item) for item in value))


def _network_place_ids(network: object) -> tuple[str, ...]:
    if network is None:
        return ()
    if isinstance(network, Mapping):
        value = network.get("place_node_ids", network.get("place_ids", ()))
    else:
        value = getattr(network, "place_node_ids", getattr(network, "place_ids", ()))
    if value is None:
        return ()
    return tuple(sorted(str(item) for item in value))


def _obligation(value: object) -> DiagnosticObligation:
    if isinstance(value, DiagnosticObligation):
        return value
    if isinstance(value, Mapping):
        return DiagnosticObligation(
            obligation_id=value.get("obligation_id", value.get("id")),
            origin_node_id=value.get("origin_node_id", value.get("origin")),
            destination_node_id=value.get("destination_node_id", value.get("destination")),
            evidence_ids=tuple(value.get("evidence_ids", ())),
        )
    return DiagnosticObligation(
        obligation_id=getattr(value, "obligation_id", getattr(value, "id", None)),
        origin_node_id=getattr(value, "origin_node_id", getattr(value, "origin", None)),
        destination_node_id=getattr(
            value,
            "destination_node_id",
            getattr(value, "destination", None),
        ),
        evidence_ids=tuple(getattr(value, "evidence_ids", ())),
    )


def _edge_access(edge: PlanningEdgeRecord, profile: NetworkDiagnosticProfile) -> str:
    if "access" in edge.unknown_claims or edge.access is None:
        return "unknown"
    if edge.access.lower() in profile.permitted_access_states:
        return "permitted"
    if edge.access.lower() in {"no", "private", "restricted", "prohibited"}:
        return "blocked"
    return "unknown"


def _edge_graph(edges: Sequence[_ParsedEdge]) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    for item in sorted(edges, key=lambda value: value.record.directed_edge_id):
        edge = item.record
        graph.add_edge(
            edge.from_node_id,
            edge.to_node_id,
            key=edge.directed_edge_id,
            edge_id=edge.directed_edge_id,
            source_edge_id=edge.source_edge_id,
            length_m=edge.length_m,
            access_state="unknown" if "access" in edge.unknown_claims else edge.access,
        )
    return graph


def _simple_graph(edges: Sequence[_ParsedEdge]) -> nx.Graph:
    graph = nx.Graph()
    for item in edges:
        edge = item.record
        graph.add_edge(edge.from_node_id, edge.to_node_id)
    return graph


def _path(
    graph: nx.MultiDiGraph,
    source: str,
    target: str,
    allowed_ids: set[str],
) -> tuple[float, tuple[str, ...], tuple[str, ...]] | None:
    if source not in graph or target not in graph:
        return None
    queue: list[tuple[float, tuple[str, ...], str, tuple[str, ...]]] = [
        (0.0, (), source, (source,))
    ]
    best: dict[str, tuple[float, tuple[str, ...]]] = {}
    while queue:
        distance, edge_ids, node, nodes = heapq.heappop(queue)
        current = (distance, edge_ids)
        if node in best and current > best[node]:
            continue
        best[node] = current
        if node == target:
            return distance, edge_ids, nodes
        for _, neighbour, _key, data in sorted(
            graph.out_edges(node, keys=True, data=True),
            key=lambda item: (str(item[3]["edge_id"]), str(item[1])),
        ):
            edge_id = str(data["edge_id"])
            if edge_id not in allowed_ids:
                continue
            candidate_ids = (*edge_ids, edge_id)
            candidate_distance = distance + float(data["length_m"])
            candidate = (candidate_distance, candidate_ids)
            if neighbour not in best or candidate < best[neighbour]:
                heapq.heappush(
                    queue,
                    (
                        candidate_distance,
                        candidate_ids,
                        str(neighbour),
                        (*nodes, str(neighbour)),
                    ),
                )
    return None


def _node_points(edges: Sequence[_ParsedEdge]) -> dict[str, Point]:
    points: dict[str, Point] = {}
    for item in edges:
        edge = item.record
        coordinates = tuple(item.geometry.coords)
        points.setdefault(edge.from_node_id, Point(coordinates[0]))
        points.setdefault(edge.to_node_id, Point(coordinates[-1]))
    return points


def _obligation_diagnostics(
    obligations: tuple[DiagnosticObligation, ...],
    graph: nx.MultiDiGraph,
    allowed_ids: set[str],
    unknown_ids: set[str],
    points: Mapping[str, Point],
    profile: NetworkDiagnosticProfile,
) -> tuple[tuple[DirectnessDiagnostic, ...], tuple[WitnessDiagnostic, ...]]:
    directness: list[DirectnessDiagnostic] = []
    witnesses: list[WitnessDiagnostic] = []
    for obligation in obligations:
        denominator = None
        if obligation.origin_node_id in points and obligation.destination_node_id in points:
            denominator = points[obligation.origin_node_id].distance(
                points[obligation.destination_node_id]
            )
        found = _path(graph, obligation.origin_node_id, obligation.destination_node_id, allowed_ids)
        if found is None:
            unknown_found = _path(
                graph,
                obligation.origin_node_id,
                obligation.destination_node_id,
                allowed_ids | unknown_ids,
            )
            state: Literal["available", "unknown", "no-path"] = (
                "unknown" if unknown_found is not None else "no-path"
            )
            reason = "unknown-edge-not-traversed" if unknown_found is not None else "no-path"
            directness.append(
                DirectnessDiagnostic(
                    obligation.obligation_id,
                    None,
                    denominator,
                    None,
                    state,
                    reason,
                )
            )
            witnesses.append(
                WitnessDiagnostic(
                    obligation.obligation_id,
                    state,
                    (),
                    (),
                    None,
                    reason,
                )
            )
            continue
        distance, edge_ids, nodes = found
        if profile.maximum_path_length_m is not None and distance > profile.maximum_path_length_m:
            state: Literal["available", "unknown", "no-path"] = "unknown"
            reason = "path-exceeds-profile-limit"
        else:
            state = "available"
            reason = None
        circuity = distance / denominator if denominator and denominator > 0 else None
        directness.append(
            DirectnessDiagnostic(
                obligation.obligation_id, distance, denominator, circuity, state, reason
            )
        )
        witnesses.append(
            WitnessDiagnostic(obligation.obligation_id, "found", nodes, edge_ids, distance, reason)
        )
    return (
        tuple(sorted(directness, key=lambda item: item.obligation_id)),
        tuple(sorted(witnesses, key=lambda item: item.obligation_id)),
    )


def analyse_network(
    graph: PlanningGraphSnapshot,
    network: DiagnosticNetworkView | object | None,
    obligations: tuple[DiagnosticObligation, ...] | Sequence[object],
    profile: NetworkDiagnosticProfile,
) -> NetworkDiagnosticResult:
    """Return deterministic diagnostics without changing the planning snapshot."""

    if not isinstance(graph, PlanningGraphSnapshot):
        raise ValueError("network diagnostics require a PlanningGraphSnapshot")
    all_records = tuple(graph.edge_records)
    all_ids = {edge.directed_edge_id for edge in all_records}
    selected_ids = set(_network_edge_ids(network, all_ids))
    issues: list[NetworkDiagnosticIssue] = []
    requested_ids = _requested_network_edge_ids(network)
    if requested_ids is not None:
        for edge_id in sorted(set(requested_ids) - all_ids):
            issues.append(
                NetworkDiagnosticIssue(
                    "missing-selected-edge",
                    edge_id,
                    "selected diagnostic edge is absent from the Planning Graph snapshot",
                )
            )
    parsed_edges: list[_ParsedEdge] = []
    for edge in all_records:
        if edge.directed_edge_id not in selected_ids:
            continue
        try:
            parsed_edges.append(_ParsedEdge(edge, wkt.loads(edge.geometry_wkt)))
        except (TypeError, ValueError) as error:
            issues.append(
                NetworkDiagnosticIssue(
                    "invalid-edge-geometry",
                    edge.directed_edge_id,
                    str(error),
                )
            )
    selected = tuple(parsed_edges)
    if selected_ids != all_ids:
        for edge_id in sorted(all_ids - selected_ids):
            issues.append(
                NetworkDiagnosticIssue(
                    "excluded-edge",
                    edge_id,
                    "edge is outside the supplied diagnostic network view",
                )
            )
    try:
        parsed_obligations = tuple(_obligation(item) for item in obligations)
    except (TypeError, ValueError) as error:
        parsed_obligations = ()
        issues.append(NetworkDiagnosticIssue("invalid-obligation", "obligations", str(error)))
    parsed_obligations = tuple(sorted(parsed_obligations, key=lambda item: item.obligation_id))
    full_graph = _edge_graph(selected)
    simple = _simple_graph(selected)

    components: list[ComponentDiagnostic] = []
    for kind, groups in (
        ("weak", nx.connected_components(simple)),
        ("strong", nx.strongly_connected_components(full_graph)),
    ):
        for nodes in sorted((tuple(sorted(group)) for group in groups), key=lambda item: item):
            edge_ids = tuple(
                sorted(
                    edge.record.directed_edge_id
                    for edge in selected
                    if edge.record.from_node_id in nodes and edge.record.to_node_id in nodes
                )
            )
            components.append(
                ComponentDiagnostic(
                    evidence_fingerprint({"kind": kind, "node_ids": nodes}),
                    kind,  # type: ignore[arg-type]
                    nodes,
                    edge_ids,
                )
            )

    degree_dangles: list[DegreeDangleDiagnostic] = []
    for node in sorted(simple.nodes):
        degree = simple.degree(node)
        component_size = len(nx.node_connected_component(simple, node)) if simple else 1
        if degree == 0:
            degree_dangles.append(DegreeDangleDiagnostic(node, "isolated", degree, ()))
        elif degree == 1 and component_size > 2:
            edge_ids = tuple(
                sorted(
                    edge.record.directed_edge_id
                    for edge in selected
                    if edge.record.from_node_id == node or edge.record.to_node_id == node
                )
            )
            degree_dangles.append(DegreeDangleDiagnostic(node, "dangle", degree, edge_ids))

    bridge_cuts: list[BridgeCutDiagnostic] = []
    for left, right in nx.bridges(simple):
        pair = tuple(
            edge
            for edge in selected
            if {edge.record.from_node_id, edge.record.to_node_id} == {left, right}
        )
        source_ids = sorted({edge.record.source_edge_id for edge in pair})
        for source_id in source_ids:
            directed_ids = tuple(
                sorted(
                    edge.record.directed_edge_id
                    for edge in pair
                    if edge.record.source_edge_id == source_id
                )
            )
            endpoint_left, endpoint_right = sorted((str(left), str(right)))
            bridge_cuts.append(
                BridgeCutDiagnostic(
                    source_id,
                    directed_ids,
                    endpoint_left,
                    endpoint_right,
                )
            )

    reciprocal_access_records: list[ReciprocalAccessDiagnostic] = []
    for source_id in sorted({edge.record.source_edge_id for edge in selected}):
        source_edges = tuple(edge for edge in selected if edge.record.source_edge_id == source_id)
        states = {str(edge.record.reciprocal_state) for edge in source_edges}
        if "conflicting" in states:
            state = "conflicting"
        elif "unknown" in states:
            state = "unknown"
        elif "one-way" in states:
            state = "one-way"
        else:
            state = "reciprocal"
        if state != "reciprocal":
            reciprocal_access_records.append(
                ReciprocalAccessDiagnostic(
                    source_id,
                    state,  # type: ignore[arg-type]
                    tuple(sorted(edge.record.directed_edge_id for edge in source_edges)),
                    "reciprocal access is not evidenced",
                )
            )
    reciprocal_access = tuple(reciprocal_access_records)

    permitted_ids = {
        edge.record.directed_edge_id
        for edge in selected
        if _edge_access(edge.record, profile) == "permitted"
    }
    unknown_ids = {
        edge.record.directed_edge_id
        for edge in selected
        if _edge_access(edge.record, profile) == "unknown"
    }
    blocked_ids = {
        edge.record.directed_edge_id
        for edge in selected
        if _edge_access(edge.record, profile) == "blocked"
    }
    points = _node_points(selected)
    directness, witnesses = _obligation_diagnostics(
        parsed_obligations,
        full_graph,
        permitted_ids,
        unknown_ids,
        points,
        profile,
    )

    sources = sorted(
        {obligation.origin_node_id for obligation in parsed_obligations}
        | set(_network_place_ids(network))
    )
    reachability: list[ReachabilityDiagnostic] = []
    permitted_graph = nx.MultiDiGraph()
    permitted_graph.add_edges_from(
        (
            edge.record.from_node_id,
            edge.record.to_node_id,
            {"edge_id": edge.record.directed_edge_id},
        )
        for edge in selected
        if edge.record.directed_edge_id in permitted_ids
    )
    for source in sources:
        if source not in permitted_graph:
            issues.append(
                NetworkDiagnosticIssue(
                    "missing-place-node",
                    source,
                    "place or obligation origin is absent from the diagnostic network",
                )
            )
            continue
        reachable_nodes = tuple(
            sorted(
                nx.descendants(permitted_graph, source)
                | ({source} if source in permitted_graph else set())
            )
        )
        reachable_edges = tuple(
            sorted(
                edge.record.directed_edge_id
                for edge in selected
                if edge.record.from_node_id in reachable_nodes
                and edge.record.to_node_id in reachable_nodes
                and edge.record.directed_edge_id in permitted_ids
            )
        )
        reachability.append(
            ReachabilityDiagnostic(
                source,
                reachable_nodes,
                reachable_edges,
                tuple(sorted(unknown_ids)),
                tuple(sorted(blocked_ids)),
                (f"unknown_policy={profile.unknown_policy}", "unknown edges are not traversed"),
            )
        )

    observation_states = {
        source_id: tuple(
            sorted(
                str(binding.state).lower()
                for binding in graph.observation_matches
                if binding.subject_id == source_id and binding.claim in profile.crossing_claims
            )
        )
        for source_id in {edge.record.source_edge_id for edge in selected}
    }
    severance_sources = {
        edge.record.source_edge_id
        for edge in selected
        if _edge_access(edge.record, profile) != "permitted"
    } | {item.source_edge_id for item in bridge_cuts}
    severance: list[SeveranceDiagnostic] = []
    for source_id in sorted(severance_sources):
        source_edges = tuple(edge for edge in selected if edge.record.source_edge_id == source_id)
        claims = {
            claim for edge in source_edges for claim, _ids in edge.record.claim_observation_ids
        }
        states = observation_states.get(source_id, ())
        if any(state in {"conflicting", "conflict"} for state in states):
            crossing = "conflicting"
        elif any(
            state in {"evidenced", "confirmed", "present", "permitted", "yes", "true"}
            for state in states
        ):
            crossing = "evidenced"
        elif states or claims & set(profile.crossing_claims):
            crossing = "unknown"
        else:
            crossing = "none"
        source_directed_ids = {edge.record.directed_edge_id for edge in source_edges}
        affected = tuple(
            obligation.obligation_id
            for obligation in parsed_obligations
            if _path(
                full_graph,
                obligation.origin_node_id,
                obligation.destination_node_id,
                permitted_ids | source_directed_ids,
            )
            is not None
            and _path(
                full_graph,
                obligation.origin_node_id,
                obligation.destination_node_id,
                permitted_ids - source_directed_ids,
            )
            is None
        )
        severance.append(
            SeveranceDiagnostic(
                source_id,
                affected,
                crossing,  # type: ignore[arg-type]
                "severance"
                if source_id in {item.source_edge_id for item in bridge_cuts}
                else "barrier",
            )
        )

    delta: list[DeltaDiagnostic] = []
    if (
        isinstance(network, DiagnosticNetworkView)
        and network.before_edge_ids is not None
        and network.after_edge_ids is not None
    ):
        before = set(network.before_edge_ids)
        after = set(network.after_edge_ids)
        delta.extend(DeltaDiagnostic(edge_id, "added") for edge_id in sorted(after - before))
        delta.extend(DeltaDiagnostic(edge_id, "removed") for edge_id in sorted(before - after))

    payload = {
        "contract": "satn-network-diagnostic-result/v1",
        "graph_fingerprint": graph.graph_fingerprint,
        "profile_fingerprint": profile.fingerprint,
        "obligations": tuple(
            {
                "obligation_id": item.obligation_id,
                "origin_node_id": item.origin_node_id,
                "destination_node_id": item.destination_node_id,
                "evidence_ids": item.evidence_ids,
            }
            for item in sorted(obligations, key=lambda item: item.obligation_id)
        ),
        "components": tuple(
            item.__dict__
            for item in sorted(components, key=lambda item: (item.kind, item.node_ids))
        ),
        "degree_dangles": tuple(item.__dict__ for item in degree_dangles),
        "bridge_cuts": tuple(
            item.__dict__ for item in sorted(bridge_cuts, key=lambda item: item.source_edge_id)
        ),
        "reciprocal_access": tuple(item.__dict__ for item in reciprocal_access),
        "directness": tuple(item.__dict__ for item in directness),
        "reachability": tuple(item.__dict__ for item in reachability),
        "severance": tuple(item.__dict__ for item in severance),
        "witnesses": tuple(item.__dict__ for item in witnesses),
        "delta": tuple(item.__dict__ for item in delta),
        "diagnostics": tuple(item.__dict__ for item in issues),
    }
    return NetworkDiagnosticResult(
        profile=profile,
        graph_fingerprint=graph.graph_fingerprint,
        fingerprint=evidence_fingerprint(_identity_value(payload)),
        components=tuple(sorted(components, key=lambda item: (item.kind, item.node_ids))),
        degree_dangles=tuple(degree_dangles),
        bridge_cuts=tuple(sorted(bridge_cuts, key=lambda item: item.source_edge_id)),
        reciprocal_access=reciprocal_access,
        directness=directness,
        reachability=tuple(reachability),
        severance=tuple(sorted(severance, key=lambda item: item.source_edge_id)),
        witnesses=witnesses,
        delta=tuple(delta),
        diagnostics=tuple(issues),
    )


NetworkDiagnosticObligation = DiagnosticObligation
EffectiveNetworkDiagnosticView = DiagnosticNetworkView


__all__ = [
    "BridgeCutDiagnostic",
    "ComponentDiagnostic",
    "DegreeDangleDiagnostic",
    "DiagnosticNetworkView",
    "DiagnosticObligation",
    "DirectnessDiagnostic",
    "EffectiveNetworkDiagnosticView",
    "NetworkDiagnosticIssue",
    "NetworkDiagnosticObligation",
    "NetworkDiagnosticProfile",
    "NetworkDiagnosticResult",
    "ReachabilityDiagnostic",
    "ReciprocalAccessDiagnostic",
    "SeveranceDiagnostic",
    "WitnessDiagnostic",
    "analyse_network",
]
