"""Finite, evidence-bound corridor obligation derivation.

This module turns governed origin/destination observations into a bounded
roster of *investigation obligations*.  It does not create a network, infer
demand from population, or select an alignment.  Missing, unmatched and
failed observations remain typed records while valid obligations continue.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import StrEnum
from itertools import pairwise

from satn.planning_graph import PlanningEdgeRecord, PlanningGraphSnapshot


def _canonical(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("corridor identity cannot contain non-finite values")
        return round(value, 9)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _canonical(model_dump(mode="json"))
    return value


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: object) -> str:
    return f"{prefix}-{_fingerprint(value)[:20]}"


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip() != value:
        raise ValueError(f"{name} must be non-empty canonical text")
    return value


def _identifiers(values: Iterable[str], name: str) -> tuple[str, ...]:
    result = tuple(sorted(set(_identifier(item, name) for item in values)))
    return result


def _strict_identifiers(values: Iterable[str], name: str) -> tuple[str, ...]:
    identifiers = tuple(_identifier(item, name) for item in values)
    duplicates = sorted({item for item in identifiers if identifiers.count(item) > 1})
    if duplicates:
        raise ValueError(f"duplicate {name}: {', '.join(duplicates)}")
    return tuple(sorted(identifiers))


def _require_unique_records(records: Iterable[object], attribute: str, label: str) -> None:
    identifiers = [getattr(record, attribute) for record in records]
    duplicates = sorted({value for value in identifiers if identifiers.count(value) > 1})
    if duplicates:
        raise ValueError(f"duplicate {label} IDs: {', '.join(duplicates)}")


def _unusable_seed_state(flow: GovernedDemandFlow) -> tuple[SeedDisposition, str]:
    """Classify unusable governed observations without collapsing their causes."""

    if flow.match_state is DemandMatchState.UNMATCHED:
        return SeedDisposition.UNMATCHED, "demand observation did not match a governed source"
    if flow.match_state is DemandMatchState.CONFLICTING:
        return SeedDisposition.CONFLICTING, "governed demand evidence contains conflicting matches"
    if flow.match_state is DemandMatchState.UNKNOWN:
        return SeedDisposition.UNKNOWN_DEMAND, "governed demand match state is unknown"
    if flow.origin_node_id is None or flow.destination_node_id is None:
        return SeedDisposition.MISSING_ENDPOINT, "demand observation is missing an endpoint"
    if flow.demand_value is None:
        return SeedDisposition.MISSING_VALUE, "demand observation is missing a demand value"
    return SeedDisposition.UNKNOWN_DEMAND, "demand observation is not usable for path seeding"


def _graph_identity(graph: PlanningGraphSnapshot) -> dict[str, object]:
    return {
        "graph_fingerprint": graph.graph_fingerprint,
        "graph_profile_fingerprint": graph.profile_fingerprint,
        "source_export_fingerprint": graph.source_export_fingerprint,
        "route_control_fingerprint": graph.route_control_fingerprint,
    }


class FallbackOrigin(StrEnum):
    DEMAND_LED = "demand-led"
    PLACE_HIERARCHY = "place-hierarchy"
    COVERAGE_ONLY = "coverage-only"


class DemandMatchState(StrEnum):
    EVIDENCED = "evidenced"
    UNMATCHED = "unmatched"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class SeedDisposition(StrEnum):
    ELIGIBLE = "eligible"
    BELOW_THRESHOLD = "below-threshold"
    UNMATCHED = "unmatched"
    UNKNOWN_DEMAND = "unknown-demand"
    CONFLICTING = "conflicting"
    MISSING_ENDPOINT = "missing-endpoint"
    MISSING_VALUE = "missing-value"
    FAILED_PATH = "failed-path"
    OVER_DISTANCE = "over-distance"
    DISCONNECTED_ISLAND = "disconnected-island"
    BUDGET_EXCEEDED = "budget-exceeded"


class PairDisposition(StrEnum):
    ELIGIBLE = "eligible"
    BELOW_THRESHOLD = "below-threshold"
    FAILED_PATH = "failed-path"
    OVER_DISTANCE = "over-distance"
    DISCONNECTED_ISLAND = "disconnected-island"
    DUPLICATE = "duplicate"
    RETAINED = "retained"


@dataclass(frozen=True)
class ObligationPlace:
    place_id: str
    node_id: str
    category: str = "place"
    hierarchy_rank: int = 0
    evidence_ids: tuple[str, ...] = ()
    context_population: float | None = None

    def __post_init__(self) -> None:
        _identifier(self.place_id, "place_id")
        _identifier(self.node_id, "node_id")
        _identifier(self.category, "category")
        if self.hierarchy_rank < 0:
            raise ValueError("hierarchy_rank must be non-negative")
        object.__setattr__(
            self, "evidence_ids", _strict_identifiers(self.evidence_ids, "evidence_id")
        )
        if self.context_population is not None and (
            not math.isfinite(self.context_population) or self.context_population < 0
        ):
            raise ValueError("context_population must be finite and non-negative")


@dataclass(frozen=True)
class StrategicDestination:
    destination_id: str
    node_id: str
    category: str = "strategic-destination"
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.destination_id, "destination_id")
        _identifier(self.node_id, "node_id")
        _identifier(self.category, "category")
        object.__setattr__(
            self, "evidence_ids", _strict_identifiers(self.evidence_ids, "evidence_id")
        )


@dataclass(frozen=True)
class CrossBoundaryGateway:
    gateway_id: str
    node_id: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.gateway_id, "gateway_id")
        _identifier(self.node_id, "node_id")
        object.__setattr__(
            self, "evidence_ids", _strict_identifiers(self.evidence_ids, "evidence_id")
        )


@dataclass(frozen=True)
class GovernedDemandFlow:
    flow_id: str
    origin_node_id: str | None
    destination_node_id: str | None
    demand_value: float | None
    evidence_ids: tuple[str, ...] = ()
    match_state: DemandMatchState = DemandMatchState.EVIDENCED

    def __post_init__(self) -> None:
        _identifier(self.flow_id, "flow_id")
        if self.origin_node_id is not None:
            _identifier(self.origin_node_id, "origin_node_id")
        if self.destination_node_id is not None:
            _identifier(self.destination_node_id, "destination_node_id")
        if (
            self.origin_node_id is not None
            and self.destination_node_id is not None
            and self.origin_node_id == self.destination_node_id
        ):
            raise ValueError("demand flow endpoints must be distinct")
        if self.demand_value is not None and (
            not math.isfinite(self.demand_value) or self.demand_value < 0
        ):
            raise ValueError("demand_value must be finite and non-negative")
        if isinstance(self.match_state, str):
            object.__setattr__(self, "match_state", DemandMatchState(self.match_state))
        object.__setattr__(
            self, "evidence_ids", _strict_identifiers(self.evidence_ids, "evidence_id")
        )

    @property
    def usable(self) -> bool:
        return (
            self.match_state is DemandMatchState.EVIDENCED
            and self.origin_node_id is not None
            and self.destination_node_id is not None
            and self.demand_value is not None
        )


@dataclass(frozen=True)
class CorridorObligationProfile:
    """All demand-led policy knobs, with no hidden ranking constants."""

    profile_id: str = "corridor-obligations-trial-v1"
    version: int = 1
    hierarchy_pairing_rules: tuple[tuple[str, str], ...] = ()
    gateway_pairing_rules: tuple[str, ...] = ()
    network_distance_limit_m: float = 50_000.0
    maximum_path_length_m: float | None = None
    maximum_path_seeds_per_pair: int = 3
    segment_length_m: float = 500.0
    threshold_method: str = "absolute"
    threshold_value: float = 1.0
    clustering_epsilon_m: float = 1_000.0
    clustering_min_samples: int = 2
    tie_break: str = "minimum-total-network-distance-then-stable-id"
    fallback_order: tuple[FallbackOrigin, ...] = (
        FallbackOrigin.DEMAND_LED,
        FallbackOrigin.PLACE_HIERARCHY,
        FallbackOrigin.COVERAGE_ONLY,
    )
    sensitivity_profile_ids: tuple[str, ...] = ("default",)
    maximum_obligations: int = 256
    maximum_seed_evaluations: int = 2_048

    def __post_init__(self) -> None:
        _identifier(self.profile_id, "profile_id")
        if self.version < 1:
            raise ValueError("profile version must be positive")
        if self.network_distance_limit_m <= 0 or not math.isfinite(self.network_distance_limit_m):
            raise ValueError("network distance limit must be finite and positive")
        if self.maximum_path_length_m is not None and (
            self.maximum_path_length_m <= 0 or not math.isfinite(self.maximum_path_length_m)
        ):
            raise ValueError("maximum path length must be finite and positive")
        if self.maximum_path_seeds_per_pair < 1 or self.maximum_obligations < 1:
            raise ValueError("corridor roster budgets must be positive")
        if self.maximum_seed_evaluations < 1:
            raise ValueError("maximum seed evaluations must be positive")
        if self.segment_length_m <= 0 or not math.isfinite(self.segment_length_m):
            raise ValueError("segment length must be finite and positive")
        if self.threshold_method not in {"absolute", "percentile"}:
            raise ValueError("threshold method must be absolute or percentile")
        if not math.isfinite(self.threshold_value) or self.threshold_value < 0:
            raise ValueError("threshold value must be finite and non-negative")
        if self.threshold_method == "percentile" and self.threshold_value > 100:
            raise ValueError("percentile threshold must be between 0 and 100")
        if self.clustering_epsilon_m < 0 or not math.isfinite(self.clustering_epsilon_m):
            raise ValueError("clustering epsilon must be finite and non-negative")
        if self.clustering_min_samples < 1:
            raise ValueError("clustering minimum samples must be positive")
        _identifier(self.tie_break, "tie_break")
        fallback_order = tuple(
            item if isinstance(item, FallbackOrigin) else FallbackOrigin(item)
            for item in self.fallback_order
        )
        object.__setattr__(self, "fallback_order", fallback_order)
        if not self.fallback_order or len(set(self.fallback_order)) != len(self.fallback_order):
            raise ValueError("fallback order must be finite and unique")
        if FallbackOrigin.COVERAGE_ONLY not in self.fallback_order:
            raise ValueError("fallback order must include coverage-only")
        for left, right in self.hierarchy_pairing_rules:
            _identifier(left, "hierarchy pairing category")
            _identifier(right, "hierarchy pairing category")
        if len(set(self.hierarchy_pairing_rules)) != len(self.hierarchy_pairing_rules):
            raise ValueError("hierarchy pairing rules must be unique")
        for rule in self.gateway_pairing_rules:
            _identifier(rule, "gateway pairing rule")
        if len(set(self.gateway_pairing_rules)) != len(self.gateway_pairing_rules):
            raise ValueError("gateway pairing rules must be unique")
        if not self.sensitivity_profile_ids or len(set(self.sensitivity_profile_ids)) != len(
            self.sensitivity_profile_ids
        ):
            raise ValueError("sensitivity profile IDs must be non-empty and unique")
        object.__setattr__(
            self,
            "sensitivity_profile_ids",
            _strict_identifiers(self.sensitivity_profile_ids, "sensitivity profile ID"),
        )

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "contract": "satn-corridor-obligation-profile/v1",
                **self.__dict__,
            }
        )


@dataclass(frozen=True)
class CorridorObligation:
    obligation_id: str
    origin_endpoint_id: str
    destination_endpoint_id: str
    origin_node_id: str
    destination_node_id: str
    source_flow_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    role: str = "interurban-spine"
    mandatory: bool = True
    fallback_origin: FallbackOrigin = FallbackOrigin.DEMAND_LED

    def __post_init__(self) -> None:
        _identifier(self.obligation_id, "obligation_id")
        for name in (
            "origin_endpoint_id",
            "destination_endpoint_id",
            "origin_node_id",
            "destination_node_id",
        ):
            _identifier(getattr(self, name), name)
        if self.origin_endpoint_id == self.destination_endpoint_id:
            raise ValueError("obligation endpoints must be distinct")
        _identifiers(self.source_flow_ids, "source_flow_id")
        _identifiers(self.evidence_ids, "evidence_id")
        _identifier(self.role, "role")

    @property
    def endpoints(self) -> tuple[str, str]:
        return self.origin_node_id, self.destination_node_id


@dataclass(frozen=True)
class CorridorSeedRecord:
    seed_id: str
    source_flow_id: str
    origin_node_id: str | None
    destination_node_id: str | None
    flow_value: float | None
    segment_index: int
    segment_start_m: float | None
    segment_end_m: float | None
    disposition: SeedDisposition
    reason: str
    network_distance_m: float | None = None
    path_edge_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    cluster_id: str | None = None
    medoid: bool = False


@dataclass(frozen=True)
class CorridorPairRecord:
    pair_id: str
    origin_endpoint_id: str
    destination_endpoint_id: str
    origin_node_id: str
    destination_node_id: str
    disposition: PairDisposition
    reason: str
    network_distance_m: float | None = None
    path_edge_ids: tuple[str, ...] = ()
    source_seed_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceRequest:
    request_id: str
    claim: str
    reason: str
    source_flow_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CorridorObligationResult:
    obligations: tuple[CorridorObligation, ...]
    seed_records: tuple[CorridorSeedRecord, ...]
    pair_records: tuple[CorridorPairRecord, ...]
    unmatched_demand: tuple[GovernedDemandFlow, ...]
    sensitivity_profile_ids: tuple[str, ...]
    fallback_origin: FallbackOrigin
    evidence_requests: tuple[EvidenceRequest, ...]
    status: str
    profile_fingerprint: str
    fingerprint: str
    graph_fingerprint: str = ""
    graph_profile_fingerprint: str = ""
    source_export_fingerprint: str = ""
    route_control_fingerprint: str | None = None
    evaluated_seed_count: int = 0

    @property
    def discarded_pairs(self) -> tuple[CorridorPairRecord, ...]:
        return tuple(
            item for item in self.pair_records if item.disposition is not PairDisposition.ELIGIBLE
        )

    @property
    def unmatched_flows(self) -> tuple[GovernedDemandFlow, ...]:
        return self.unmatched_demand


@dataclass(frozen=True)
class _Route:
    distance_m: float
    edge_ids: tuple[str, ...]


def _build_adjacency(
    graph: PlanningGraphSnapshot,
) -> dict[str, tuple[PlanningEdgeRecord, ...]]:
    by_node: dict[str, list[PlanningEdgeRecord]] = defaultdict(list)
    for edge in graph.edge_records:
        if edge.access in {"no", "private", "customers"}:
            continue
        by_node[edge.from_node_id].append(edge)
    return {
        node: tuple(sorted(edges, key=lambda item: item.directed_edge_id))
        for node, edges in by_node.items()
    }


def _route(
    adjacency: Mapping[str, tuple[PlanningEdgeRecord, ...]],
    origin: str,
    destination: str,
    maximum_distance_m: float,
) -> _Route | None:
    if origin == destination:
        return _Route(0.0, ())
    queue: list[tuple[float, tuple[str, ...], str]] = [(0.0, (), origin)]
    best: dict[str, tuple[float, tuple[str, ...]]] = {origin: (0.0, ())}
    while queue:
        distance, path, node = heapq.heappop(queue)
        if distance > maximum_distance_m + 1e-9:
            continue
        if node == destination:
            return _Route(distance, path)
        if (distance, path) != best.get(node):
            continue
        for edge in adjacency.get(node, ()):
            next_distance = distance + edge.length_m
            if next_distance > maximum_distance_m + 1e-9:
                continue
            next_path = (*path, edge.directed_edge_id)
            prior = best.get(edge.to_node_id)
            candidate = (next_distance, next_path)
            if prior is None or candidate < prior:
                best[edge.to_node_id] = candidate
                heapq.heappush(queue, (next_distance, next_path, edge.to_node_id))
    return None


def _component_by_node(graph: PlanningGraphSnapshot) -> dict[str, str]:
    return {
        node_id: component.component_id
        for component in graph.component_records
        if component.kind == "weak"
        for node_id in component.node_ids
    }


def _percentile(values: tuple[float, ...], percentile: float) -> float:
    if not values:
        return math.inf
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def _seed_distance(
    left: CorridorSeedRecord,
    right: CorridorSeedRecord,
    adjacency: Mapping[str, tuple[PlanningEdgeRecord, ...]],
    maximum_distance_m: float,
) -> float:
    if left.origin_node_id is None or right.origin_node_id is None:
        return math.inf
    if left.destination_node_id is None or right.destination_node_id is None:
        return math.inf
    origin = _route(adjacency, left.origin_node_id, right.origin_node_id, maximum_distance_m)
    destination = _route(
        adjacency, left.destination_node_id, right.destination_node_id, maximum_distance_m
    )
    if origin is None or destination is None:
        return math.inf
    return (origin.distance_m + destination.distance_m) / 2.0


def _cluster_seeds(
    seeds: tuple[CorridorSeedRecord, ...],
    adjacency: Mapping[str, tuple[PlanningEdgeRecord, ...]],
    profile: CorridorObligationProfile,
) -> tuple[CorridorSeedRecord, ...]:
    if not seeds:
        return ()
    distances = {}
    for left in seeds:
        for right in seeds:
            forward = _seed_distance(left, right, adjacency, profile.network_distance_limit_m)
            reverse = _seed_distance(right, left, adjacency, profile.network_distance_limit_m)
            distances[(left.seed_id, right.seed_id)] = min(forward, reverse)
    neighbours = {
        seed.seed_id: tuple(
            other.seed_id
            for other in seeds
            if distances[(seed.seed_id, other.seed_id)] <= profile.clustering_epsilon_m
        )
        for seed in seeds
    }
    core = {
        seed_id
        for seed_id, nearby in neighbours.items()
        if len(nearby) >= profile.clustering_min_samples
    }
    by_id = {seed.seed_id: seed for seed in seeds}
    core_components: list[set[str]] = []
    assigned_core: set[str] = set()
    for seed_id in sorted(core):
        if seed_id in assigned_core:
            continue
        members: set[str] = set()
        queue = deque([seed_id])
        while queue:
            current = queue.popleft()
            if current in members:
                continue
            members.add(current)
            assigned_core.add(current)
            queue.extend(
                item for item in neighbours[current] if item in core and item not in members
            )
        core_components.append(members)

    # Border seeds may touch more than one core component. Assign each border
    # exactly once using a stable nearest-component tie-break, rather than
    # allowing iteration order to create premature singleton clusters.
    clusters: list[set[str]] = [set(component) for component in core_components]
    for seed in sorted(seeds, key=lambda item: item.seed_id):
        if seed.seed_id in core:
            continue
        candidates: list[tuple[float, tuple[str, ...], int]] = []
        for index, component in enumerate(clusters):
            touching = [
                member
                for member in component
                if distances[(seed.seed_id, member)] <= profile.clustering_epsilon_m
            ]
            if touching:
                candidates.append(
                    (
                        min(distances[(seed.seed_id, member)] for member in touching),
                        tuple(sorted(component)),
                        index,
                    )
                )
        if candidates:
            clusters[min(candidates)[2]].add(seed.seed_id)
        else:
            clusters.append({seed.seed_id})

    medoids: list[CorridorSeedRecord] = []
    for member_ids in clusters:
        cluster = tuple(sorted((by_id[item] for item in member_ids), key=lambda item: item.seed_id))
        cluster_id = _stable_id("seed-cluster", tuple(item.seed_id for item in cluster))
        if len(cluster) == 1:
            medoid = cluster[0]
        else:
            medoid = min(
                cluster,
                key=lambda candidate: (
                    sum(distances[(candidate.seed_id, other.seed_id)] for other in cluster),
                    candidate.seed_id,
                ),
            )
        for item in cluster:
            # Preserve every seed; only the medoid seeds the finite obligation roster.
            medoids.append(
                replace(item, cluster_id=cluster_id, medoid=item.seed_id == medoid.seed_id)
            )
    return tuple(sorted(medoids, key=lambda item: item.seed_id))


def _endpoint_id(node_id: str, by_node: Mapping[str, tuple[str, ...]]) -> str:
    values = by_node.get(node_id, ())
    return values[0] if values else f"node:{node_id}"


def _make_obligation(
    origin_endpoint_id: str,
    destination_endpoint_id: str,
    origin_node_id: str,
    destination_node_id: str,
    source_flow_ids: tuple[str, ...],
    evidence_ids: tuple[str, ...],
    profile: CorridorObligationProfile,
    fallback_origin: FallbackOrigin,
    graph_identity: Mapping[str, object],
) -> CorridorObligation:
    payload = {
        "origin_endpoint_id": origin_endpoint_id,
        "destination_endpoint_id": destination_endpoint_id,
        "origin_node_id": origin_node_id,
        "destination_node_id": destination_node_id,
        "source_flow_ids": source_flow_ids,
        "evidence_ids": evidence_ids,
        "profile": profile.fingerprint,
        "graph": graph_identity,
    }
    return CorridorObligation(
        obligation_id=_stable_id("corridor-obligation", payload),
        origin_endpoint_id=origin_endpoint_id,
        destination_endpoint_id=destination_endpoint_id,
        origin_node_id=origin_node_id,
        destination_node_id=destination_node_id,
        source_flow_ids=source_flow_ids,
        evidence_ids=evidence_ids,
        fallback_origin=fallback_origin,
    )


def derive_corridor_obligations(
    places: tuple[ObligationPlace, ...],
    destinations: tuple[StrategicDestination, ...],
    gateways: tuple[CrossBoundaryGateway, ...],
    demand: tuple[GovernedDemandFlow, ...],
    graph: PlanningGraphSnapshot,
    profile: CorridorObligationProfile,
) -> CorridorObligationResult:
    """Derive a finite, deterministic, demand-led obligation roster."""

    if not isinstance(profile, CorridorObligationProfile):
        raise ValueError("corridor obligation derivation requires a governed profile")
    if not isinstance(graph, PlanningGraphSnapshot):
        raise ValueError("corridor obligation derivation requires a Planning Graph snapshot")
    graph_identity = _graph_identity(graph)

    def seed_id(flow: GovernedDemandFlow, segment_index: int = 0) -> str:
        return _stable_id(
            "corridor-seed",
            {
                "flow_id": flow.flow_id,
                "origin_node_id": flow.origin_node_id,
                "destination_node_id": flow.destination_node_id,
                "demand_value": flow.demand_value,
                "evidence_ids": flow.evidence_ids,
                "segment_index": segment_index,
                "profile": profile.fingerprint,
                "graph": graph_identity,
            },
        )

    places = tuple(places or ())
    destinations = tuple(destinations or ())
    gateways = tuple(gateways or ())
    demand = tuple(demand or ())
    _require_unique_records(places, "place_id", "place")
    _require_unique_records(destinations, "destination_id", "destination")
    _require_unique_records(gateways, "gateway_id", "gateway")
    _require_unique_records(demand, "flow_id", "flow")
    endpoint_ids = [
        *(item.place_id for item in places),
        *(item.destination_id for item in destinations),
        *(item.gateway_id for item in gateways),
    ]
    if len(endpoint_ids) != len(set(endpoint_ids)):
        raise ValueError("endpoint identities must be globally unique")
    places = tuple(sorted(places, key=lambda item: item.place_id))
    destinations = tuple(sorted(destinations, key=lambda item: item.destination_id))
    gateways = tuple(sorted(gateways, key=lambda item: item.gateway_id))
    demand = tuple(sorted(demand, key=lambda item: item.flow_id))
    adjacency = _build_adjacency(graph)
    node_endpoints: dict[str, list[str]] = defaultdict(list)
    for item in places:
        node_endpoints[item.node_id].append(item.place_id)
    for item in destinations:
        node_endpoints[item.node_id].append(item.destination_id)
    for item in gateways:
        node_endpoints[item.node_id].append(item.gateway_id)
    endpoint_by_node = {node: tuple(sorted(set(values))) for node, values in node_endpoints.items()}
    ambiguous_nodes = sorted(node for node, values in endpoint_by_node.items() if len(values) > 1)
    if ambiguous_nodes:
        raise ValueError(
            "endpoint identities are ambiguous at graph nodes: " + ", ".join(ambiguous_nodes)
        )

    flow_values = tuple(
        item.demand_value for item in demand if item.usable and item.demand_value is not None
    )
    threshold = (
        profile.threshold_value
        if profile.threshold_method == "absolute"
        else _percentile(flow_values, profile.threshold_value)
    )
    seeds: list[CorridorSeedRecord] = []
    evaluated_seed_count = 0
    unmatched: list[GovernedDemandFlow] = []
    pair_records: list[CorridorPairRecord] = []
    component_by_node = _component_by_node(graph)
    anchor_nodes = set(endpoint_by_node)
    component_anchor_nodes = {
        component.component_id: bool(set(component.node_ids) & anchor_nodes)
        for component in graph.component_records
        if component.kind == "weak"
    }

    def retain_pair_for_seed(flow: GovernedDemandFlow, seed: CorridorSeedRecord) -> None:
        if seed.origin_node_id is None or seed.destination_node_id is None:
            return
        disposition = {
            SeedDisposition.BELOW_THRESHOLD: PairDisposition.BELOW_THRESHOLD,
            SeedDisposition.FAILED_PATH: PairDisposition.FAILED_PATH,
            SeedDisposition.OVER_DISTANCE: PairDisposition.OVER_DISTANCE,
            SeedDisposition.DISCONNECTED_ISLAND: PairDisposition.DISCONNECTED_ISLAND,
        }.get(seed.disposition)
        if disposition is None:
            return
        origin_endpoint = _endpoint_id(seed.origin_node_id, endpoint_by_node)
        destination_endpoint = _endpoint_id(seed.destination_node_id, endpoint_by_node)
        if origin_endpoint == destination_endpoint:
            return
        pair_records.append(
            CorridorPairRecord(
                pair_id=_stable_id(
                    "corridor-pair",
                    {
                        "flow_id": flow.flow_id,
                        "origin_endpoint_id": origin_endpoint,
                        "destination_endpoint_id": destination_endpoint,
                        "evidence_ids": seed.evidence_ids,
                        "profile": profile.fingerprint,
                        "graph": graph_identity,
                    },
                ),
                origin_endpoint_id=origin_endpoint,
                destination_endpoint_id=destination_endpoint,
                origin_node_id=seed.origin_node_id,
                destination_node_id=seed.destination_node_id,
                disposition=disposition,
                reason=seed.reason,
                network_distance_m=seed.network_distance_m,
                path_edge_ids=seed.path_edge_ids,
                source_seed_ids=(seed.seed_id,),
                evidence_ids=seed.evidence_ids,
            )
        )

    def budget_seed(flow: GovernedDemandFlow, segment_index: int = 0) -> CorridorSeedRecord:
        return CorridorSeedRecord(
            seed_id=seed_id(flow, segment_index),
            source_flow_id=flow.flow_id,
            origin_node_id=flow.origin_node_id,
            destination_node_id=flow.destination_node_id,
            flow_value=flow.demand_value,
            segment_index=segment_index,
            segment_start_m=None,
            segment_end_m=None,
            disposition=SeedDisposition.BUDGET_EXCEEDED,
            reason="maximum seed evaluation budget exhausted; flow retained without route search",
            evidence_ids=flow.evidence_ids,
        )

    for flow in demand:
        if not flow.usable:
            unmatched.append(flow)
            disposition, reason = _unusable_seed_state(flow)
            seed = CorridorSeedRecord(
                seed_id=seed_id(flow),
                source_flow_id=flow.flow_id,
                origin_node_id=flow.origin_node_id,
                destination_node_id=flow.destination_node_id,
                flow_value=flow.demand_value,
                segment_index=0,
                segment_start_m=None,
                segment_end_m=None,
                disposition=disposition,
                reason=reason,
                evidence_ids=flow.evidence_ids,
            )
            seeds.append(seed)
            continue
        assert flow.origin_node_id is not None and flow.destination_node_id is not None
        assert flow.demand_value is not None
        if flow.demand_value < threshold:
            seed = CorridorSeedRecord(
                seed_id=seed_id(flow),
                source_flow_id=flow.flow_id,
                origin_node_id=flow.origin_node_id,
                destination_node_id=flow.destination_node_id,
                flow_value=flow.demand_value,
                segment_index=0,
                segment_start_m=0.0,
                segment_end_m=None,
                disposition=SeedDisposition.BELOW_THRESHOLD,
                reason="demand retained below configured threshold",
                evidence_ids=flow.evidence_ids,
            )
            seeds.append(seed)
            retain_pair_for_seed(flow, seed)
            continue
        component_origin = component_by_node.get(flow.origin_node_id)
        component_destination = component_by_node.get(flow.destination_node_id)
        disconnected_island = (
            component_origin is not None
            and component_destination is not None
            and (
                (
                    component_origin == component_destination
                    and not component_anchor_nodes.get(component_origin, False)
                    and bool(anchor_nodes)
                )
                or (
                    component_origin != component_destination
                    and (
                        not component_anchor_nodes.get(component_origin, False)
                        or not component_anchor_nodes.get(component_destination, False)
                    )
                )
            )
        )
        if disconnected_island:
            seed = CorridorSeedRecord(
                seed_id=seed_id(flow),
                source_flow_id=flow.flow_id,
                origin_node_id=flow.origin_node_id,
                destination_node_id=flow.destination_node_id,
                flow_value=flow.demand_value,
                segment_index=0,
                segment_start_m=0.0,
                segment_end_m=None,
                disposition=SeedDisposition.DISCONNECTED_ISLAND,
                reason="demand flow lies in a graph component without a governed endpoint",
                evidence_ids=flow.evidence_ids,
            )
            seeds.append(seed)
            retain_pair_for_seed(flow, seed)
            continue
        if evaluated_seed_count >= profile.maximum_seed_evaluations:
            seeds.append(budget_seed(flow))
            continue
        evaluated_seed_count += 1
        path_budget = profile.maximum_path_length_m or max(
            profile.network_distance_limit_m * 4,
            sum(edge.length_m for edge in graph.edge_records),
        )
        path = _route(
            adjacency,
            flow.origin_node_id,
            flow.destination_node_id,
            path_budget,
        )
        if path is None:
            disposition = (
                SeedDisposition.OVER_DISTANCE
                if profile.maximum_path_length_m is not None
                else SeedDisposition.FAILED_PATH
            )
            seed = CorridorSeedRecord(
                seed_id=seed_id(flow),
                source_flow_id=flow.flow_id,
                origin_node_id=flow.origin_node_id,
                destination_node_id=flow.destination_node_id,
                flow_value=flow.demand_value,
                segment_index=0,
                segment_start_m=0.0,
                segment_end_m=None,
                disposition=disposition,
                reason="governed graph could not provide a permitted bounded path",
                network_distance_m=None,
                evidence_ids=flow.evidence_ids,
            )
            seeds.append(seed)
            retain_pair_for_seed(flow, seed)
            continue
        if path.distance_m > profile.network_distance_limit_m + 1e-9:
            seed = CorridorSeedRecord(
                seed_id=seed_id(flow),
                source_flow_id=flow.flow_id,
                origin_node_id=flow.origin_node_id,
                destination_node_id=flow.destination_node_id,
                flow_value=flow.demand_value,
                segment_index=0,
                segment_start_m=0.0,
                segment_end_m=path.distance_m,
                disposition=SeedDisposition.OVER_DISTANCE,
                reason="bounded network distance limit exceeded",
                network_distance_m=path.distance_m,
                path_edge_ids=path.edge_ids,
                evidence_ids=flow.evidence_ids,
            )
            seeds.append(seed)
            retain_pair_for_seed(flow, seed)
            continue
        segment_count = min(
            profile.maximum_path_seeds_per_pair,
            max(1, math.ceil(path.distance_m / profile.segment_length_m)),
        )
        for segment_index in range(segment_count):
            start = min(path.distance_m, segment_index * profile.segment_length_m)
            end = min(path.distance_m, (segment_index + 1) * profile.segment_length_m)
            seeds.append(
                CorridorSeedRecord(
                    seed_id=_stable_id(
                        "corridor-seed",
                        {
                            "flow_id": flow.flow_id,
                            "origin_node_id": flow.origin_node_id,
                            "destination_node_id": flow.destination_node_id,
                            "demand_value": flow.demand_value,
                            "evidence_ids": flow.evidence_ids,
                            "segment_index": segment_index,
                            "profile": profile.fingerprint,
                            "graph": graph_identity,
                        },
                    ),
                    source_flow_id=flow.flow_id,
                    origin_node_id=flow.origin_node_id,
                    destination_node_id=flow.destination_node_id,
                    flow_value=flow.demand_value,
                    segment_index=segment_index,
                    segment_start_m=start,
                    segment_end_m=end,
                    disposition=SeedDisposition.ELIGIBLE,
                    reason="matched demand flow seeded within configured bounds",
                    network_distance_m=path.distance_m,
                    path_edge_ids=path.edge_ids,
                    evidence_ids=flow.evidence_ids,
                )
            )

    eligible = tuple(item for item in seeds if item.disposition is SeedDisposition.ELIGIBLE)
    clustered = _cluster_seeds(eligible, adjacency, profile)
    seeds_by_id = {item.seed_id: item for item in seeds}
    seeds_by_id.update({item.seed_id: item for item in clustered})
    seeds = [seeds_by_id[item.seed_id] for item in sorted(seeds, key=lambda item: item.seed_id)]
    medoids = tuple(item for item in clustered if item.medoid)

    obligations: list[CorridorObligation] = []
    seen_obligation_keys: set[tuple[str, str]] = set()
    endpoint_records = (*places, *destinations, *gateways)

    def add_configured_pair(
        origin_id: str,
        destination_id: str,
        origin: object,
        destination: object,
        fallback: FallbackOrigin,
    ) -> None:
        if origin_id == destination_id:
            return
        key = (origin_id, destination_id)
        origin_node = origin.node_id
        destination_node = destination.node_id
        evidence = _identifiers(
            (*getattr(origin, "evidence_ids", ()), *getattr(destination, "evidence_ids", ())),
            "evidence_id",
        )
        if key in seen_obligation_keys:
            pair_records.append(
                CorridorPairRecord(
                    pair_id=_stable_id(
                        "corridor-pair",
                        {
                            "key": key,
                            "fallback": fallback,
                            "origin_node_id": origin_node,
                            "destination_node_id": destination_node,
                            "evidence_ids": evidence,
                            "disposition": PairDisposition.DUPLICATE,
                            "profile": profile.fingerprint,
                            "graph": graph_identity,
                        },
                    ),
                    origin_endpoint_id=origin_id,
                    destination_endpoint_id=destination_id,
                    origin_node_id=origin_node,
                    destination_node_id=destination_node,
                    disposition=PairDisposition.DUPLICATE,
                    reason="configured directed pair duplicates an existing retained obligation",
                    source_seed_ids=(),
                    evidence_ids=evidence,
                )
            )
            return
        seen_obligation_keys.add(key)
        path_budget = profile.maximum_path_length_m or max(
            profile.network_distance_limit_m * 4,
            sum(edge.length_m for edge in graph.edge_records),
        )
        path = _route(adjacency, origin_node, destination_node, path_budget)
        if path is None:
            disposition = PairDisposition.FAILED_PATH
            reason = "configured endpoint pair has no bounded permitted path"
        elif path.distance_m > profile.network_distance_limit_m + 1e-9:
            disposition = PairDisposition.OVER_DISTANCE
            reason = "configured endpoint pair exceeds network distance limit"
        else:
            disposition = PairDisposition.ELIGIBLE
            reason = "configured endpoint pair retained by declared pairing rule"
        pair_records.append(
            CorridorPairRecord(
                pair_id=_stable_id(
                    "corridor-pair",
                    {
                        "key": key,
                        "fallback": fallback,
                        "origin_node_id": origin_node,
                        "destination_node_id": destination_node,
                        "evidence_ids": evidence,
                        "profile": profile.fingerprint,
                        "graph": graph_identity,
                    },
                ),
                origin_endpoint_id=origin_id,
                destination_endpoint_id=destination_id,
                origin_node_id=origin_node,
                destination_node_id=destination_node,
                disposition=disposition,
                reason=reason,
                network_distance_m=None if path is None else path.distance_m,
                path_edge_ids=() if path is None else path.edge_ids,
                evidence_ids=evidence,
            )
        )
        obligations.append(
            _make_obligation(
                origin_id,
                destination_id,
                origin_node,
                destination_node,
                (),
                evidence,
                profile,
                fallback,
                graph_identity,
            )
        )

    def add_hierarchy_fallback() -> None:
        for left, right in profile.hierarchy_pairing_rules:
            matching_left = [item for item in places if item.category == left]
            matching_right = [item for item in places if item.category == right]
            for origin in matching_left:
                for destination in matching_right:
                    add_configured_pair(
                        origin.place_id,
                        destination.place_id,
                        origin,
                        destination,
                        FallbackOrigin.PLACE_HIERARCHY,
                    )

    def add_coverage_fallback() -> None:
        for left, right in pairwise(endpoint_records):
            left_id = (
                getattr(left, "place_id", None)
                or getattr(left, "destination_id", None)
                or left.gateway_id
            )
            right_id = (
                getattr(right, "place_id", None)
                or getattr(right, "destination_id", None)
                or right.gateway_id
            )
            add_configured_pair(left_id, right_id, left, right, FallbackOrigin.COVERAGE_ONLY)

    usable_input_demand = any(item.usable for item in demand)
    usable_demand_exists = bool(medoids)
    for seed in medoids:
        if seed.origin_node_id is None or seed.destination_node_id is None:
            continue
        origin_endpoint = _endpoint_id(seed.origin_node_id, endpoint_by_node)
        destination_endpoint = _endpoint_id(seed.destination_node_id, endpoint_by_node)
        if origin_endpoint == destination_endpoint:
            continue
        key = (origin_endpoint, destination_endpoint)
        if key in seen_obligation_keys:
            pair_records.append(
                CorridorPairRecord(
                    pair_id=_stable_id(
                        "corridor-pair",
                        {
                            "key": key,
                            "seed_id": seed.seed_id,
                            "origin_node_id": seed.origin_node_id,
                            "destination_node_id": seed.destination_node_id,
                            "evidence_ids": seed.evidence_ids,
                            "disposition": PairDisposition.DUPLICATE,
                            "profile": profile.fingerprint,
                            "graph": graph_identity,
                        },
                    ),
                    origin_endpoint_id=origin_endpoint,
                    destination_endpoint_id=destination_endpoint,
                    origin_node_id=seed.origin_node_id,
                    destination_node_id=seed.destination_node_id,
                    disposition=PairDisposition.DUPLICATE,
                    reason="demand medoid duplicates a retained directed endpoint pair",
                    network_distance_m=seed.network_distance_m,
                    path_edge_ids=seed.path_edge_ids,
                    source_seed_ids=(seed.seed_id,),
                    evidence_ids=seed.evidence_ids,
                )
            )
            continue
        seen_obligation_keys.add(key)
        pair_id = _stable_id(
            "corridor-pair",
            {
                "key": key,
                "seed_id": seed.seed_id,
                "origin_node_id": seed.origin_node_id,
                "destination_node_id": seed.destination_node_id,
                "evidence_ids": seed.evidence_ids,
                "profile": profile.fingerprint,
                "graph": graph_identity,
            },
        )
        pair_records.append(
            CorridorPairRecord(
                pair_id=pair_id,
                origin_endpoint_id=origin_endpoint,
                destination_endpoint_id=destination_endpoint,
                origin_node_id=seed.origin_node_id,
                destination_node_id=seed.destination_node_id,
                disposition=PairDisposition.ELIGIBLE,
                reason="demand medoid retained as a bounded obligation seed",
                network_distance_m=seed.network_distance_m,
                path_edge_ids=seed.path_edge_ids,
                source_seed_ids=(seed.seed_id,),
                evidence_ids=seed.evidence_ids,
            )
        )
        obligations.append(
            _make_obligation(
                origin_endpoint,
                destination_endpoint,
                seed.origin_node_id,
                seed.destination_node_id,
                (seed.source_flow_id,),
                seed.evidence_ids,
                profile,
                FallbackOrigin.DEMAND_LED,
                graph_identity,
            )
        )

    fallback_origin = (
        FallbackOrigin.DEMAND_LED if usable_input_demand else FallbackOrigin.COVERAGE_ONLY
    )
    evidence_requests: list[EvidenceRequest] = []
    if not usable_input_demand:
        evidence_requests.append(
            EvidenceRequest(
                request_id=_stable_id("evidence-request", ("matched-demand", profile.fingerprint)),
                claim="matched-demand",
                reason="no usable governed demand observation was available",
                source_flow_ids=tuple(item.flow_id for item in unmatched),
            )
        )
        for fallback in profile.fallback_order:
            if fallback is FallbackOrigin.PLACE_HIERARCHY:
                add_hierarchy_fallback()
            elif fallback is FallbackOrigin.COVERAGE_ONLY:
                add_coverage_fallback()
            if obligations:
                fallback_origin = fallback
                break

    if usable_input_demand and usable_demand_exists:
        add_hierarchy_fallback()
    for rule in profile.gateway_pairing_rules:
        if rule in {"place-gateway", "gateway-place"}:
            for place in places:
                for gateway in gateways:
                    if rule == "place-gateway":
                        origin_id, destination_id = place.place_id, gateway.gateway_id
                        origin, destination = place, gateway
                    else:
                        origin_id, destination_id = gateway.gateway_id, place.place_id
                        origin, destination = gateway, place
                    add_configured_pair(
                        origin_id,
                        destination_id,
                        origin,
                        destination,
                        FallbackOrigin.COVERAGE_ONLY,
                    )
        elif rule == "gateway-destination":
            for gateway in gateways:
                for destination in destinations:
                    add_configured_pair(
                        gateway.gateway_id,
                        destination.destination_id,
                        gateway,
                        destination,
                        FallbackOrigin.COVERAGE_ONLY,
                    )

    obligations = sorted(obligations, key=lambda item: item.obligation_id)[
        : profile.maximum_obligations
    ]
    has_gaps = (
        bool(evidence_requests)
        or any(item.disposition is not SeedDisposition.ELIGIBLE for item in seeds)
        or any(item.disposition is not PairDisposition.ELIGIBLE for item in pair_records)
    )
    status = "complete-with-gaps" if has_gaps else "complete"
    payload = {
        "obligations": obligations,
        "seed_records": tuple(seeds),
        "pair_records": tuple(sorted(pair_records, key=lambda item: item.pair_id)),
        "unmatched_demand": tuple(unmatched),
        "sensitivity_profile_ids": profile.sensitivity_profile_ids,
        "fallback_origin": fallback_origin,
        "evidence_requests": tuple(evidence_requests),
        "status": status,
        "profile_fingerprint": profile.fingerprint,
        "graph_identity": graph_identity,
        "evaluated_seed_count": evaluated_seed_count,
    }
    return CorridorObligationResult(
        obligations=tuple(obligations),
        seed_records=tuple(seeds),
        pair_records=tuple(sorted(pair_records, key=lambda item: item.pair_id)),
        unmatched_demand=tuple(unmatched),
        sensitivity_profile_ids=profile.sensitivity_profile_ids,
        fallback_origin=fallback_origin,
        evidence_requests=tuple(evidence_requests),
        status=status,
        profile_fingerprint=profile.fingerprint,
        fingerprint=_fingerprint(payload),
        graph_fingerprint=graph.graph_fingerprint,
        graph_profile_fingerprint=graph.profile_fingerprint,
        source_export_fingerprint=graph.source_export_fingerprint,
        route_control_fingerprint=graph.route_control_fingerprint,
        evaluated_seed_count=evaluated_seed_count,
    )


# Spec-facing aliases retain the authority-neutral record names used by the
# planning-routine transfer document without duplicating contracts.
NetworkPlace = ObligationPlace
MatchedDemandObservation = GovernedDemandFlow


__all__ = [
    "CorridorObligation",
    "CorridorObligationProfile",
    "CorridorObligationResult",
    "CorridorPairRecord",
    "CorridorSeedRecord",
    "CrossBoundaryGateway",
    "DemandMatchState",
    "EvidenceRequest",
    "FallbackOrigin",
    "GovernedDemandFlow",
    "MatchedDemandObservation",
    "NetworkPlace",
    "ObligationPlace",
    "PairDisposition",
    "SeedDisposition",
    "StrategicDestination",
    "derive_corridor_obligations",
]
