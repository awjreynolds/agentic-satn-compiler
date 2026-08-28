"""Finite, evidence-bound candidate discovery over a Planning Graph.

This module deliberately stops at discovery.  It enumerates a bounded set of
materially different edge chains for each finite corridor obligation, derives
claim-specific candidate facts from the graph, and leaves preferred selection
to the Network Selection stage.  No shortest-path result is treated as an
authority and no external routing provider is called here.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import StrEnum
from itertools import pairwise

from shapely.geometry import LineString
from shapely.ops import linemerge
from shapely.wkt import loads as load_wkt

from .alignment_selection import (
    AlignmentCandidateInput as CanonicalAlignmentCandidateInput,
)
from .alignment_selection import (
    AlignmentCandidateSet as CanonicalAlignmentCandidateSet,
)
from .alignment_selection import (
    CandidateSourceClass,
    CanonicalLineString,
    CriterionState,
    NetworkRole,
    admit_candidate_set,
)
from .network_selection import (
    ComparatorDimension,
    InterventionState,
    NetworkSelectionProfile,
    ReuseFirstCandidateClass,
)
from .planning_graph import PlanningEdgeRecord, PlanningGraphSnapshot


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
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("candidate discovery identity cannot contain non-finite values")
        return round(value, 6)
    return value


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: object) -> str:
    return f"{prefix}-{_fingerprint(value)[:20]}"


class CandidateSearchStrategy(StrEnum):
    MINIMUM_DISTANCE = "minimum-distance"
    REUSE_FIRST = "reuse-first"
    OFF_CARRIAGEWAY_OPPORTUNITY = "off-carriageway-opportunity"
    LOW_TRAFFIC_NON_A_ROAD = "low-traffic-non-a-road"
    MAJOR_ROAD_REFERENCE = "major-road-reference"


_STRATEGY_DIMENSIONS: dict[CandidateSearchStrategy, tuple[str, ...]] = {
    CandidateSearchStrategy.MINIMUM_DISTANCE: ("length_m",),
    CandidateSearchStrategy.REUSE_FIRST: (
        "proposed_new_link_m",
        "major_road_new_provision_m",
        "upgrade_required_m",
        "mixed_traffic_m",
        "length_m",
    ),
    CandidateSearchStrategy.OFF_CARRIAGEWAY_OPPORTUNITY: (
        "off_carriageway_deficit_m",
        "proposed_new_link_m",
        "length_m",
    ),
    CandidateSearchStrategy.LOW_TRAFFIC_NON_A_ROAD: (
        "a_road_m",
        "high_traffic_unprotected_m",
        "mixed_traffic_m",
        "length_m",
    ),
    CandidateSearchStrategy.MAJOR_ROAD_REFERENCE: ("non_major_road_m", "length_m"),
}


def _default_selection_profile(maximum_options: int) -> NetworkSelectionProfile:
    """Build the explicit governed vNext selection profile for discovery."""

    return NetworkSelectionProfile(
        profile_id="satn-candidate-discovery-default",
        contract="satn-network-selection-profile/vNext",
        version="1",
        candidate_class_order=(
            ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION,
            ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE,
            ReuseFirstCandidateClass.UPGRADEABLE_OFF_CARRIAGEWAY,
            ReuseFirstCandidateClass.LOW_TRAFFIC_NON_A_ROAD,
            ReuseFirstCandidateClass.UNKNOWN_OR_CONFLICTING,
        ),
        intervention_state_order=tuple(InterventionState),
        comparator_order=(
            ComparatorDimension.MANDATORY_OBLIGATION_SERVICE,
            ComparatorDimension.REUSE_CLASS,
            ComparatorDimension.INTERVENTION_STATE,
            ComparatorDimension.ROUTE_LENGTH,
            ComparatorDimension.STABLE_CANDIDATE_ID,
        ),
        material_difference_rules=(),
        displacement_rules=(),
        unknown_value_policy="retain-and-request-evidence",
        deterministic_tie_break="stable-candidate-id",
        agent_call_bound=0,
        maximum_options_per_candidate_set=maximum_options,
        maximum_hybrid_candidates_per_set=2,
        maximum_transitions_per_candidate=2,
    )


def _effective_selection_profile(profile: NetworkSelectionProfile) -> NetworkSelectionProfile:
    """Make legacy vNext vocabularies total for every discovery fact.

    vNext profiles may intentionally omit newly introduced conservative classes
    for backwards compatibility. Discovery still emits those classes, so the
    effective governed profile appends them last and exposes the resulting
    fingerprint to downstream consumers.
    """

    class_order = tuple(profile.candidate_class_order or ())
    if ReuseFirstCandidateClass.UNKNOWN_OR_CONFLICTING not in class_order:
        class_order += (ReuseFirstCandidateClass.UNKNOWN_OR_CONFLICTING,)
    intervention_order = tuple(profile.intervention_state_order or ())
    if InterventionState.UNDETERMINED not in intervention_order:
        intervention_order += (InterventionState.UNDETERMINED,)
    if (
        class_order == profile.candidate_class_order
        and intervention_order == profile.intervention_state_order
    ):
        return profile
    return profile.model_copy(
        update={
            "candidate_class_order": class_order,
            "intervention_state_order": intervention_order,
        }
    )


@dataclass(frozen=True)
class CandidateSearchStrategyConfig:
    """One named lexicographic strategy, with no hidden composite weight."""

    strategy_id: str
    dimensions: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise ValueError("candidate strategy id must be non-empty")
        if not self.dimensions or any(not item.strip() for item in self.dimensions):
            raise ValueError("candidate strategy dimensions must be non-empty")


@dataclass(frozen=True)
class CandidateDiscoveryProfile:
    """Frozen search and admission budget for one candidate discovery run."""

    profile_id: str = "satn-candidate-discovery-trial-v1"
    version: int = 1
    strategies: tuple[CandidateSearchStrategyConfig, ...] = ()
    maximum_paths_per_strategy: int = 3
    maximum_generated_candidates: int = 12
    maximum_admitted_options: int = 5
    detour_ceiling: float = 2.0
    maximum_node_settlements: int = 10_000
    maximum_deviations: int = 64
    # Friendly aliases used by config loaders; canonical fields above remain
    # the identity-bearing surface.
    max_paths_per_strategy: int | None = None
    max_generated_candidates: int | None = None
    max_admitted_options: int | None = None
    detour_ratio_ceiling: float | None = None
    node_settlement_budget: int | None = None
    selection_profile: NetworkSelectionProfile | None = None

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or self.version < 1:
            raise ValueError("candidate discovery profile identity is invalid")
        aliases = (
            ("max_paths_per_strategy", "maximum_paths_per_strategy"),
            ("max_generated_candidates", "maximum_generated_candidates"),
            ("max_admitted_options", "maximum_admitted_options"),
            ("detour_ratio_ceiling", "detour_ceiling"),
            ("node_settlement_budget", "maximum_node_settlements"),
        )
        for alias, canonical in aliases:
            value = getattr(self, alias)
            if value is not None:
                current = getattr(self, canonical)
                if (
                    current
                    != (
                        3
                        if alias == "max_paths_per_strategy"
                        else 12
                        if alias == "max_generated_candidates"
                        else 5
                        if alias == "max_admitted_options"
                        else 2.0
                        if alias == "detour_ratio_ceiling"
                        else 10_000
                    )
                    and current != value
                ):
                    raise ValueError(f"{alias} conflicts with {canonical}")
                object.__setattr__(self, canonical, value)
        if self.maximum_paths_per_strategy < 1 or self.maximum_generated_candidates < 1:
            raise ValueError("candidate discovery path and generation budgets must be positive")
        if self.maximum_admitted_options < 1 or self.maximum_node_settlements < 1:
            raise ValueError(
                "candidate discovery admission and settlement budgets must be positive"
            )
        if (
            self.maximum_deviations < 0
            or not math.isfinite(self.detour_ceiling)
            or self.detour_ceiling < 1
        ):
            raise ValueError("candidate discovery detour and deviation budgets are invalid")
        configured_values = self.strategies or tuple(CandidateSearchStrategy)
        configured = tuple(
            item
            if isinstance(item, CandidateSearchStrategyConfig)
            else CandidateSearchStrategyConfig(
                str(item),
                _STRATEGY_DIMENSIONS.get(CandidateSearchStrategy(str(item)), ("length_m",)),
            )
            for item in configured_values
        )
        ids = tuple(item.strategy_id for item in configured)
        if len(set(ids)) != len(ids):
            raise ValueError("candidate discovery strategies must be unique")
        object.__setattr__(self, "strategies", tuple(configured))
        selection_profile = _effective_selection_profile(
            self.selection_profile or _default_selection_profile(self.maximum_admitted_options)
        )
        if selection_profile.contract != "satn-network-selection-profile/vNext":
            raise ValueError("candidate discovery requires a vNext Network Selection Profile")
        if selection_profile.maximum_options_per_candidate_set != self.maximum_admitted_options:
            raise ValueError(
                "candidate discovery admitted-option budget must match selection profile"
            )
        object.__setattr__(self, "selection_profile", selection_profile)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "contract": "satn-candidate-discovery-profile/v1",
                "profile_id": self.profile_id,
                "version": self.version,
                "strategies": tuple(
                    {"id": item.strategy_id, "dimensions": item.dimensions}
                    for item in self.strategies
                ),
                "maximum_paths_per_strategy": self.maximum_paths_per_strategy,
                "maximum_generated_candidates": self.maximum_generated_candidates,
                "maximum_admitted_options": self.maximum_admitted_options,
                "detour_ceiling": self.detour_ceiling,
                "maximum_node_settlements": self.maximum_node_settlements,
                "maximum_deviations": self.maximum_deviations,
                "selection_profile": self.selection_profile.fingerprint,
            }
        )


@dataclass(frozen=True)
class CorridorObligation:
    """A finite endpoint obligation consumed by candidate discovery.

    Package 5 may provide richer demand-led obligations.  Discovery only needs
    the stable ID and endpoint node identities, so richer objects are accepted
    through the same duck-typed adapter.
    """

    obligation_id: str
    origin_node_id: str
    destination_node_id: str
    role: str = "interurban-spine"
    mandatory: bool = True

    @property
    def endpoints(self) -> tuple[str, str]:
        return self.origin_node_id, self.destination_node_id


@dataclass(frozen=True)
class CandidateEdgeEvidence:
    """Governed, claim-specific enrichment bound to one graph edge."""

    edge_id: str
    absolute_elevation_change_m: float | None = None
    elevation_change_m: float | None = None
    traffic_observation_ids: tuple[str, ...] = ()
    traffic_state: str | None = None
    constraint_observation_ids: tuple[str, ...] = ()
    constraint_state: str | None = None
    gradient_band: str | None = None
    network_scope: str | None = None
    boundary_id: str | None = None
    section_boundary: bool = False
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.edge_id, str) or not self.edge_id.strip():
            raise ValueError("candidate edge evidence requires a non-empty edge id")
        if (
            self.absolute_elevation_change_m is not None
            and self.elevation_change_m is not None
            and self.absolute_elevation_change_m != abs(self.elevation_change_m)
        ):
            raise ValueError("elevation enrichment aliases must agree")
        elevation = (
            self.absolute_elevation_change_m
            if self.absolute_elevation_change_m is not None
            else None
            if self.elevation_change_m is None
            else abs(self.elevation_change_m)
        )
        if elevation is not None and (not math.isfinite(elevation) or elevation < 0):
            raise ValueError("absolute elevation change must be finite and non-negative")
        if elevation is not None:
            object.__setattr__(self, "absolute_elevation_change_m", float(elevation))
            object.__setattr__(self, "elevation_change_m", float(elevation))
        for field_name in (
            "traffic_observation_ids",
            "constraint_observation_ids",
            "evidence_ids",
        ):
            values = tuple(getattr(self, field_name))
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"{field_name} must contain non-empty identifiers")
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} cannot contain duplicates")
            object.__setattr__(self, field_name, tuple(sorted(values)))
        for field_name in (
            "traffic_state",
            "constraint_state",
            "gradient_band",
            "network_scope",
            "boundary_id",
        ):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be non-empty text when supplied")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self)


@dataclass(frozen=True)
class CandidateReviewSection:
    section_id: str
    candidate_id: str
    edge_ids: tuple[str, ...]
    geometry_wkt: str
    length_m: float
    reuse_class: ReuseFirstCandidateClass
    intervention_state: InterventionState
    alignment_bases: tuple[str, ...]
    primary_alignment_basis: str
    evidence_ids: tuple[str, ...] = ()
    traffic_observation_ids: tuple[str, ...] = ()
    constraint_observation_ids: tuple[str, ...] = ()
    access_observation_ids: tuple[str, ...] = ()
    evidence_snapshot_fingerprint: str | None = None
    traffic_state: str | None = None
    constraint_state: str | None = None
    gradient_band: str | None = None
    network_scope: str | None = None
    boundary_id: str | None = None
    total_absolute_elevation_change_m: float | None = None


@dataclass(frozen=True)
class AssessedCandidateRecord:
    candidate_id: str
    obligation_id: str
    endpoints: tuple[str, str]
    edge_ids: tuple[str, ...]
    reverse_edge_ids: tuple[str, ...]
    geometry_wkt: str
    length_m: float
    directness_m: float
    reuse_class: ReuseFirstCandidateClass
    intervention_state: InterventionState
    alignment_bases: tuple[str, ...]
    primary_alignment_basis: str
    sections: tuple[CandidateReviewSection, ...]
    generating_strategy_ids: tuple[str, ...]
    existing_provision_m: float | None = None
    upgrade_required_m: float | None = None
    proposed_new_link_m: float | None = None
    low_traffic_m: float | None = None
    major_road_m: float | None = None
    total_absolute_elevation_change_m: float | None = None
    transition_count: int = 0
    fragmentation_count: int = 0
    traffic_observation_ids: tuple[str, ...] = ()
    constraint_observation_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    unknown_facts: tuple[str, ...] = ()
    known_access_prohibition: bool = False
    network_role: str = "interurban-spine"
    mandatory: bool = True
    evidence_snapshot_fingerprint: str | None = None
    edge_evidence_fingerprint: str | None = None
    admission_disposition: str = "eligible"
    candidate_input: CanonicalAlignmentCandidateInput | None = None

    @property
    def candidate(self) -> CanonicalAlignmentCandidateInput | AssessedCandidateRecord:
        """Return the governed canonical candidate when one is available."""

        return self.candidate_input if self.candidate_input is not None else self

    @property
    def canonical_candidate(self) -> CanonicalAlignmentCandidateInput:
        if self.candidate_input is None:
            raise ValueError("candidate has not been materialised as a canonical input")
        return self.candidate_input

    @property
    def disposition(self) -> str:
        """Explicit hard-gate disposition for review and publication adapters."""

        return self.admission_disposition

    @property
    def routing_edge_ids(self) -> tuple[str, ...]:
        return self.edge_ids

    @property
    def reverse_routing_edge_ids(self) -> tuple[str, ...]:
        return self.reverse_edge_ids


AlignmentCandidateSet = CanonicalAlignmentCandidateSet


@dataclass(frozen=True)
class CandidateSearchDiagnostic:
    code: str
    obligation_id: str
    message: str
    strategy_id: str | None = None
    candidate_id: str | None = None
    edge_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceRequest:
    request_id: str
    obligation_id: str
    claim: str
    reason: str
    candidate_id: str | None = None


@dataclass(frozen=True)
class CorridorObligationDisposition:
    obligation_id: str
    disposition: str
    candidate_set_id: str | None
    reason: str


@dataclass(frozen=True)
class CandidateSetGapEvidence:
    obligation_id: str
    endpoints: tuple[str, str]
    reason: str
    search_diagnostic_ids: tuple[str, ...]


@dataclass(frozen=True)
class CandidateDiscoveryRequest:
    graph: PlanningGraphSnapshot
    obligations: tuple[object, ...]
    evidence_snapshot: object
    profile: CandidateDiscoveryProfile
    edge_evidence: tuple[CandidateEdgeEvidence, ...] = ()
    selection_profile: NetworkSelectionProfile | None = None

    def __post_init__(self) -> None:
        if _snapshot_fingerprint(self.evidence_snapshot) is None:
            raise ValueError("candidate discovery requires governed evidence snapshot fingerprint")
        if self.selection_profile is not None:
            if self.selection_profile.contract != "satn-network-selection-profile/vNext":
                raise ValueError("candidate discovery requires a vNext Network Selection Profile")
            if (
                self.selection_profile.maximum_options_per_candidate_set
                != self.profile.maximum_admitted_options
            ):
                raise ValueError(
                    "candidate discovery admitted-option budget must match selection profile"
                )
            if self.profile.selection_profile.fingerprint != self.selection_profile.fingerprint:
                object.__setattr__(
                    self,
                    "profile",
                    replace(self.profile, selection_profile=self.selection_profile),
                )
        bindings = tuple(self.edge_evidence)
        if any(not isinstance(item, CandidateEdgeEvidence) for item in bindings):
            raise ValueError("edge_evidence must contain CandidateEdgeEvidence records")
        ids = tuple(item.edge_id for item in bindings)
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate candidate edge evidence binding")
        graph_ids = {item.directed_edge_id for item in self.graph.edge_records}
        foreign = sorted(set(ids) - graph_ids)
        if foreign:
            raise ValueError("foreign candidate edge evidence binding: " + ", ".join(foreign))
        object.__setattr__(
            self,
            "edge_evidence",
            tuple(sorted(bindings, key=lambda item: item.edge_id)),
        )


@dataclass(frozen=True)
class CandidateDiscoveryResult:
    candidate_sets: tuple[CanonicalAlignmentCandidateSet, ...]
    candidate_records: tuple[AssessedCandidateRecord, ...]
    obligation_dispositions: tuple[CorridorObligationDisposition, ...]
    search_diagnostics: tuple[CandidateSearchDiagnostic, ...]
    evidence_requests: tuple[EvidenceRequest, ...]
    fingerprint: str
    status: str = "complete"
    gaps: tuple[CandidateSetGapEvidence, ...] = ()
    evidence_snapshot_fingerprint: str | None = None
    edge_evidence_fingerprint: str | None = None
    selection_profile_fingerprint: str | None = None


@dataclass(frozen=True)
class _Path:
    nodes: tuple[str, ...]
    edge_ids: tuple[str, ...]
    cost: tuple[int, ...]
    length_m: float


def _obligation_value(obligation: object, *names: str) -> object | None:
    for name in names:
        if hasattr(obligation, name):
            value = getattr(obligation, name)
            if value is not None:
                return value
    return None


def _obligation_details(obligation: object) -> tuple[str, str, str, str, bool]:
    identifier = _obligation_value(obligation, "obligation_id", "id", "connection_id")
    endpoints = _obligation_value(obligation, "endpoints")
    origin = _obligation_value(
        obligation, "origin_node_id", "origin", "from_node_id", "source_node_id"
    )
    destination = _obligation_value(
        obligation, "destination_node_id", "destination", "to_node_id", "target_node_id"
    )
    if endpoints is not None and (origin is None or destination is None):
        try:
            origin, destination = tuple(endpoints)[:2]
        except (TypeError, ValueError):
            origin = destination = None
    if not all(isinstance(value, str) and value for value in (identifier, origin, destination)):
        raise ValueError("corridor obligations require stable ID and two endpoint node IDs")
    role = _obligation_value(obligation, "role", "network_role") or "interurban-spine"
    if not isinstance(role, str) or not role.strip():
        raise ValueError("corridor obligation role must be non-empty text")
    mandatory = bool(
        _obligation_value(obligation, "mandatory")
        if _obligation_value(obligation, "mandatory") is not None
        else True
    )
    return str(identifier), str(origin), str(destination), role, mandatory


def _snapshot_fingerprint(snapshot: object) -> str | None:
    """Read only a governed snapshot identity; never hash provider object IDs."""

    for name in ("snapshot_fingerprint", "fingerprint", "content_fingerprint"):
        value = getattr(snapshot, name, None)
        if isinstance(value, str) and value:
            return value
    if isinstance(snapshot, Mapping):
        value = snapshot.get("snapshot_fingerprint") or snapshot.get("fingerprint")
        if isinstance(value, str) and value:
            return value
    return None


def _edge_evidence_ids(edge: PlanningEdgeRecord) -> tuple[str, ...]:
    claim_ids = tuple(
        identifier
        for _claim, identifiers in edge.claim_observation_ids
        for identifier in identifiers
    )
    return tuple(
        sorted(
            set(
                edge.access_observation_ids
                + edge.asset_observation_ids
                + edge.road_observation_ids
                + claim_ids
            )
        )
    )


def _edge_claims(edge: PlanningEdgeRecord) -> set[str]:
    claims = {claim.lower() for claim, _ids in edge.claim_observation_ids}
    claims.update(identifier.lower() for identifier in edge.asset_observation_ids)
    claims.update(identifier.lower() for identifier in edge.road_observation_ids)
    return claims


def _edge_class(
    edge: PlanningEdgeRecord,
) -> tuple[ReuseFirstCandidateClass, InterventionState, str, bool]:
    highway = (edge.highway or "").lower()
    ref = (edge.ref or "").lower()
    claims = _edge_claims(edge)
    is_a_road = highway in {"trunk", "primary", "trunk_link", "primary_link"} or ref.startswith("a")
    is_cycle = (
        highway
        in {
            "cycleway",
            "cycle_track",
            "cycle-track",
            "greenway",
            "shared_use_path",
            "path-cycleway",
        }
        or any(
            token in claims
            for token in {
                "cycleway",
                "cycle-track",
                "existing-provision",
                "current-ncn",
                "mapped-cycleway",
            }
        )
        or (edge.bicycle in {"designated", "yes", "official"} and highway in {"path", "track"})
    )
    is_prow = (
        highway in {"footway", "path", "bridleway", "byway", "restricted_byway"}
        or any(
            token in claims
            for token in {
                "prow",
                "public-footpath",
                "public-bridleway",
                "reclassified-ncn",
                "declassified-ncn",
            }
        )
        or "prow" in ref
        or ("ncn" in ref and "current" not in claims)
    )
    is_quiet = highway in {"residential", "living_street", "unclassified", "tertiary", "service"}
    prohibited = edge.access in {"no", "private", "customers"} or (
        edge.bicycle in {"no", "dismount"} and edge.foot in {"no", "private"}
    )
    if is_cycle:
        return (
            ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION,
            InterventionState.EXISTING_PROVISION,
            "cycleway",
            prohibited,
        )
    if is_prow:
        return (
            ReuseFirstCandidateClass.UPGRADEABLE_OFF_CARRIAGEWAY,
            InterventionState.UPGRADE_REQUIRED,
            "prow",
            prohibited,
        )
    if is_a_road:
        return (
            ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE,
            InterventionState.PROPOSED_NEW_LINK,
            "a-road",
            prohibited,
        )
    if is_quiet:
        return (
            ReuseFirstCandidateClass.LOW_TRAFFIC_NON_A_ROAD,
            InterventionState.UPGRADE_REQUIRED,
            "quiet-road",
            prohibited,
        )
    return (
        ReuseFirstCandidateClass.UNKNOWN_OR_CONFLICTING,
        InterventionState.UNDETERMINED,
        "unknown",
        prohibited,
    )


def _edge_cost(edge: PlanningEdgeRecord) -> dict[str, int]:
    klass, intervention, _basis, _prohibited = _edge_class(edge)
    length = int(edge.length_mm)
    return {
        "length_m": length,
        "proposed_new_link_m": length if intervention == InterventionState.PROPOSED_NEW_LINK else 0,
        "major_road_new_provision_m": length
        if klass == ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE
        else 0,
        "upgrade_required_m": length if intervention == InterventionState.UPGRADE_REQUIRED else 0,
        "mixed_traffic_m": length
        if klass
        in {
            ReuseFirstCandidateClass.LOW_TRAFFIC_NON_A_ROAD,
            ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE,
        }
        else 0,
        "off_carriageway_deficit_m": length
        if klass
        not in {
            ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION,
            ReuseFirstCandidateClass.UPGRADEABLE_OFF_CARRIAGEWAY,
        }
        else 0,
        "a_road_m": length
        if klass == ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE
        else 0,
        "high_traffic_unprotected_m": 0,
        "non_major_road_m": length
        if klass != ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE
        else 0,
    }


def _combine_geometry(edges: tuple[PlanningEdgeRecord, ...]) -> str:
    lines: list[LineString] = []
    for edge in edges:
        geometry = load_wkt(edge.geometry_wkt)
        if not isinstance(geometry, LineString) or geometry.is_empty:
            continue
        lines.append(geometry)
    if not lines:
        return "LINESTRING EMPTY"
    try:
        merged = linemerge(lines)
        if isinstance(merged, LineString):
            return merged.wkt
    except (TypeError, ValueError):
        pass
    coords: list[tuple[float, float]] = []
    for line in lines:
        points = list(line.coords)
        if coords and coords[-1] != points[0] and coords[-1] == points[-1]:
            points.reverse()
        if not coords:
            coords.extend(points)
        elif coords[-1] == points[0]:
            coords.extend(points[1:])
        else:
            coords.extend(points)
    return LineString(coords).wkt


def _enumerate_paths(
    edges: tuple[PlanningEdgeRecord, ...],
    origin: str,
    destination: str,
    strategy: CandidateSearchStrategyConfig,
    profile: CandidateDiscoveryProfile,
    diagnostics: list[CandidateSearchDiagnostic],
    obligation_id: str,
    *,
    include_prohibited: bool = False,
) -> tuple[_Path, ...]:
    by_node: dict[str, list[PlanningEdgeRecord]] = defaultdict(list)
    for edge in sorted(edges, key=lambda item: item.directed_edge_id):
        by_node[edge.from_node_id].append(edge)
    edge_by_id = {edge.directed_edge_id: edge for edge in edges}
    initial = tuple(0 for _ in strategy.dimensions)
    heap: list[tuple[tuple[int, ...], tuple[str, ...], tuple[str, ...], str]] = [
        (initial, (origin,), (), origin)
    ]
    found: list[_Path] = []
    settlements = 0
    while heap and len(found) < profile.maximum_paths_per_strategy:
        if found and len(found) >= 1 + profile.maximum_deviations:
            diagnostics.append(
                CandidateSearchDiagnostic(
                    "search-truncated",
                    obligation_id,
                    "maximum Yen-style deviations reached",
                    strategy.strategy_id,
                )
            )
            diagnostics.append(
                CandidateSearchDiagnostic(
                    "deviation-suppressed",
                    obligation_id,
                    "materially distinct path suppressed by deviation budget",
                    strategy.strategy_id,
                )
            )
            break
        cost, nodes, path_edges, node = heapq.heappop(heap)
        settlements += 1
        if settlements > profile.maximum_node_settlements:
            diagnostics.append(
                CandidateSearchDiagnostic(
                    "search-truncated",
                    obligation_id,
                    "node settlement budget exhausted",
                    strategy.strategy_id,
                )
            )
            break
        if node == destination and path_edges:
            length_m = sum(edge_by_id[item].length_m for item in path_edges)
            found.append(_Path(nodes, path_edges, cost, length_m))
            continue
        for edge in by_node.get(node, ()):
            if edge.directed_edge_id in path_edges or edge.to_node_id in nodes:
                continue
            _klass, _intervention, _basis, prohibited = _edge_class(edge)
            if prohibited and not include_prohibited:
                diagnostics.append(
                    CandidateSearchDiagnostic(
                        "known-access-prohibition",
                        obligation_id,
                        "edge retained as provenance but excluded by access hard gate",
                        strategy.strategy_id,
                        edge_ids=(edge.directed_edge_id,),
                    )
                )
                continue
            if prohibited:
                diagnostics.append(
                    CandidateSearchDiagnostic(
                        "known-access-prohibition",
                        obligation_id,
                        "edge retained as candidate provenance but fails the access hard gate",
                        strategy.strategy_id,
                        edge_ids=(edge.directed_edge_id,),
                    )
                )
            dimensions = _edge_cost(edge)
            increment = tuple(dimensions.get(item, 0) for item in strategy.dimensions)
            next_cost = tuple(left + right for left, right in zip(cost, increment, strict=True))
            heapq.heappush(
                heap,
                (
                    next_cost,
                    (*nodes, edge.to_node_id),
                    (*path_edges, edge.directed_edge_id),
                    edge.to_node_id,
                ),
            )
    return tuple(found)


def _direct_distance(
    edges: tuple[PlanningEdgeRecord, ...],
    origin: str,
    destination: str,
    profile: CandidateDiscoveryProfile,
    obligation_id: str,
    *,
    include_prohibited: bool = False,
) -> float | None:
    strategy = CandidateSearchStrategyConfig("direct-distance", ("length_m",))
    diagnostics: list[CandidateSearchDiagnostic] = []
    # The direct path is a comparison baseline, not a candidate search result;
    # discover it with a deterministic graph-sized budget even when the caller
    # deliberately constrains material candidate discovery.
    baseline_budget = max(profile.maximum_node_settlements, len(edges) * max(2, len(edges)))
    paths = _enumerate_paths(
        edges,
        origin,
        destination,
        strategy,
        replace(profile, maximum_paths_per_strategy=1, maximum_node_settlements=baseline_budget),
        diagnostics,
        obligation_id,
        include_prohibited=include_prohibited,
    )
    return paths[0].length_m if paths else None


def _claim_state(values: tuple[CandidateEdgeEvidence | None, ...], claim: str) -> str | None:
    states = {
        getattr(item, f"{claim}_state")
        for item in values
        if item is not None and getattr(item, f"{claim}_state") is not None
    }
    if len(states) == 1:
        return next(iter(states))
    if len(states) > 1:
        return "conflicting"
    return None


def _claim_complete(values: tuple[CandidateEdgeEvidence | None, ...], claim: str) -> bool:
    if not values or any(item is None for item in values):
        return False
    if claim == "elevation":
        return all(item.absolute_elevation_change_m is not None for item in values if item)
    if claim == "traffic":
        return all(
            item.traffic_state is not None or item.traffic_observation_ids
            for item in values
            if item
        )
    if claim == "constraint":
        return all(
            item.constraint_state is not None or item.constraint_observation_ids
            for item in values
            if item
        )
    return False


def _enrichment_evidence_ids(
    item: CandidateEdgeEvidence | None,
) -> tuple[str, ...]:
    if item is None:
        return ()
    return tuple(
        sorted(
            set(item.evidence_ids + item.traffic_observation_ids + item.constraint_observation_ids)
        )
    )


def _canonical_identifier(value: str, *, prefix: str = "evidence") -> str:
    """Convert governed source identifiers to the canonical lowercase ID shape."""

    token = "".join(char.lower() if char.isalnum() or char in "._:-" else "-" for char in value)
    token = token.strip("-")
    return token or f"{prefix}-{_fingerprint(value)[:20]}"


def _canonical_endpoint(value: str) -> str:
    return _canonical_identifier(value, prefix="node")


def _canonical_role(value: str) -> NetworkRole:
    try:
        return NetworkRole(value)
    except ValueError:
        return NetworkRole.UNRESOLVED_STRATEGIC_ALIGNMENT


def _canonical_source_class(reuse: ReuseFirstCandidateClass) -> CandidateSourceClass:
    if reuse == ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION:
        return CandidateSourceClass.VERIFIED_EXISTING_ASSET
    if reuse == ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE:
        return CandidateSourceClass.A_ROAD_CORRIDOR
    return CandidateSourceClass.OTHER_ROUTABLE


def _canonical_alignment_basis(value: str) -> str:
    return {
        "cycleway": "mapped-cycleway",
        "prow": "public-footpath",
        "a-road": "a-road",
        "quiet-road": "unclassified-road",
    }.get(value, "prow-class-unknown")


def _canonical_geometry(geometry_wkt: str) -> CanonicalLineString:
    geometry = load_wkt(geometry_wkt)
    if not isinstance(geometry, LineString) or geometry.is_empty:
        raise ValueError("candidate geometry must contain a non-empty LineString")
    coordinates = tuple((float(x), float(y)) for x, y in geometry.coords)
    return CanonicalLineString(coordinates=coordinates)


def _materialise_candidate(
    obligation_id: str,
    endpoints: tuple[str, str],
    path: _Path,
    edge_by_id: Mapping[str, PlanningEdgeRecord],
    strategies: tuple[str, ...],
    direct_distance: float,
    network_role: str,
    mandatory: bool,
    evidence_snapshot_fingerprint: str | None,
    enrichment_by_edge: Mapping[str, CandidateEdgeEvidence],
    edge_evidence_fingerprint: str | None,
) -> AssessedCandidateRecord:
    path_edges = tuple(edge_by_id[item] for item in path.edge_ids)
    facts = tuple(_edge_class(edge) for edge in path_edges)
    enrichments = tuple(enrichment_by_edge.get(edge.directed_edge_id) for edge in path_edges)
    classes = tuple(item[0] for item in facts)
    interventions = tuple(item[1] for item in facts)
    bases = tuple(sorted({item[2] for item in facts}))
    if ReuseFirstCandidateClass.UNKNOWN_OR_CONFLICTING in classes:
        reuse = ReuseFirstCandidateClass.UNKNOWN_OR_CONFLICTING
    elif ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION in classes:
        # A reusable section gives a mixed route reuse-first priority.  The
        # section facts and the route-level intervention state below retain
        # any A-road/local continuity delivery burden.
        reuse = ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION
    elif ReuseFirstCandidateClass.UPGRADEABLE_OFF_CARRIAGEWAY in classes:
        reuse = ReuseFirstCandidateClass.UPGRADEABLE_OFF_CARRIAGEWAY
    elif ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE in classes:
        reuse = ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE
    elif ReuseFirstCandidateClass.LOW_TRAFFIC_NON_A_ROAD in classes:
        reuse = ReuseFirstCandidateClass.LOW_TRAFFIC_NON_A_ROAD
    else:
        reuse = ReuseFirstCandidateClass.UNKNOWN_OR_CONFLICTING
    if InterventionState.PROPOSED_NEW_LINK in interventions:
        intervention = InterventionState.PROPOSED_NEW_LINK
    elif InterventionState.UPGRADE_REQUIRED in interventions:
        intervention = InterventionState.UPGRADE_REQUIRED
    elif (
        InterventionState.EXISTING_PROVISION in interventions
        and InterventionState.UNDETERMINED not in interventions
    ):
        intervention = InterventionState.EXISTING_PROVISION
    else:
        intervention = InterventionState.UNDETERMINED
    primary = (
        "unknown"
        if reuse == ReuseFirstCandidateClass.UNKNOWN_OR_CONFLICTING
        else (
            "cycleway"
            if reuse == ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION
            else "prow"
            if reuse == ReuseFirstCandidateClass.UPGRADEABLE_OFF_CARRIAGEWAY
            else "a-road"
            if reuse == ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE
            else "quiet-road"
        )
    )
    geometry_wkt = _combine_geometry(path_edges)
    evidence_ids = tuple(
        sorted(
            {
                identifier
                for edge, enrichment in zip(path_edges, enrichments, strict=True)
                for identifier in _edge_evidence_ids(edge) + _enrichment_evidence_ids(enrichment)
            }
        )
    )
    snapshot_evidence = (
        (_fingerprint(evidence_snapshot_fingerprint),)
        if evidence_snapshot_fingerprint is not None
        else ()
    )
    canonical_evidence_fingerprints = snapshot_evidence + tuple(
        _fingerprint(identifier) for identifier in (evidence_ids or path.edge_ids)
    )
    governed_evidence_ids = tuple(
        _canonical_identifier(identifier)
        for identifier in (
            (f"snapshot-{evidence_snapshot_fingerprint}",)
            + (evidence_ids or (f"candidate-discovery-{_fingerprint(path.edge_ids)}",))
        )
    )
    canonical_endpoints = tuple(_canonical_endpoint(item) for item in endpoints)
    prohibited = any(item[3] for item in facts)
    canonical_basis = tuple(sorted({_canonical_alignment_basis(item) for item in bases}))
    canonical_candidate = CanonicalAlignmentCandidateInput(
        network_role=_canonical_role(network_role),
        endpoints=canonical_endpoints,
        source_class=_canonical_source_class(reuse),
        geometry=_canonical_geometry(geometry_wkt),
        evidence_fingerprints=canonical_evidence_fingerprints,
        provenance_ids=tuple(_canonical_identifier(item, prefix="edge") for item in path.edge_ids),
        topology_state=CriterionState.UNSATISFIED if prohibited else CriterionState.SATISFIED,
        served_network_place_ids=canonical_endpoints,
        served_access_obligation_ids=()
        if prohibited
        else (_canonical_identifier(obligation_id, prefix="obligation"),),
        directness_m=float(path.length_m),
        reuse_class=reuse,
        intervention_state=intervention,
        alignment_bases=canonical_basis,
        primary_alignment_basis=_canonical_alignment_basis(primary),
        total_absolute_elevation_change_m=(
            sum(item.absolute_elevation_change_m or 0 for item in enrichments if item is not None)
            if _claim_complete(enrichments, "elevation")
            else None
        ),
        transition_count=max(0, len({(fact[0], fact[1], fact[2]) for fact in facts}) - 1),
        fragmentation_count=sum(1 for left, right in pairwise(facts) if left[0] != right[0]),
        governed_evidence_ids=governed_evidence_ids,
    )
    candidate_id = canonical_candidate.candidate_id
    sections: list[CandidateReviewSection] = []
    groups: list[list[int]] = []
    for index, (edge, fact, enrichment) in enumerate(
        zip(path_edges, facts, enrichments, strict=True)
    ):
        signature = (
            fact[0],
            fact[1],
            fact[2],
            None if enrichment is None else enrichment.traffic_state,
            None if enrichment is None else enrichment.constraint_state,
            None if enrichment is None else enrichment.gradient_band,
            None if enrichment is None else enrichment.network_scope,
            None if enrichment is None else enrichment.boundary_id,
            _edge_evidence_ids(edge),
            _enrichment_evidence_ids(enrichment),
        )
        boundary = enrichment is not None and enrichment.section_boundary
        if not groups or boundary or signature != groups[-1][1]:
            groups.append([index, signature])
            groups[-1].append(index)
        else:
            groups[-1].append(index)
    # The second item stores the signature; remaining items are edge indexes.
    for _index, group in enumerate(groups):
        signature = group[1]
        indexes = tuple(group[2:])
        grouped_edges = tuple(path_edges[item] for item in indexes)
        grouped_facts = tuple(facts[item] for item in indexes)
        grouped_enrichment = tuple(enrichments[item] for item in indexes)
        edge_ids = tuple(item.directed_edge_id for item in grouped_edges)
        section_evidence_ids = tuple(
            sorted(
                {
                    identifier
                    for edge, enrichment in zip(grouped_edges, grouped_enrichment, strict=True)
                    for identifier in _edge_evidence_ids(edge)
                    + _enrichment_evidence_ids(enrichment)
                }
            )
        )
        section_traffic_ids = tuple(
            sorted(
                {
                    identifier
                    for enrichment in grouped_enrichment
                    if enrichment is not None
                    for identifier in enrichment.traffic_observation_ids
                }
            )
        )
        section_constraint_ids = tuple(
            sorted(
                {
                    identifier
                    for enrichment in grouped_enrichment
                    if enrichment is not None
                    for identifier in enrichment.constraint_observation_ids
                }
            )
        )
        section_access_ids = tuple(
            sorted(
                {identifier for edge in grouped_edges for identifier in edge.access_observation_ids}
            )
        )
        section_elevation = (
            sum(
                item.absolute_elevation_change_m or 0
                for item in grouped_enrichment
                if item is not None
            )
            if _claim_complete(grouped_enrichment, "elevation")
            else None
        )
        first_fact = grouped_facts[0]
        section_id = _stable_id(
            "section",
            {
                "candidate_id": candidate_id,
                "edge_ids": edge_ids,
                "signature": signature,
                "edge_evidence_fingerprint": edge_evidence_fingerprint,
            },
        )
        sections.append(
            CandidateReviewSection(
                section_id=section_id,
                candidate_id=candidate_id,
                edge_ids=edge_ids,
                geometry_wkt=_combine_geometry(grouped_edges),
                length_m=sum(edge.length_m for edge in grouped_edges),
                reuse_class=first_fact[0],
                intervention_state=first_fact[1],
                alignment_bases=tuple(sorted({item[2] for item in grouped_facts})),
                primary_alignment_basis=first_fact[2],
                evidence_ids=section_evidence_ids,
                traffic_observation_ids=section_traffic_ids,
                constraint_observation_ids=section_constraint_ids,
                access_observation_ids=section_access_ids,
                evidence_snapshot_fingerprint=evidence_snapshot_fingerprint,
                traffic_state=_claim_state(grouped_enrichment, "traffic"),
                constraint_state=_claim_state(grouped_enrichment, "constraint"),
                gradient_band=(
                    None if grouped_enrichment[0] is None else grouped_enrichment[0].gradient_band
                ),
                network_scope=(
                    None if grouped_enrichment[0] is None else grouped_enrichment[0].network_scope
                ),
                boundary_id=(
                    None if grouped_enrichment[0] is None else grouped_enrichment[0].boundary_id
                ),
                total_absolute_elevation_change_m=section_elevation,
            )
        )
    lengths = [edge.length_m for edge in path_edges]
    existing = sum(
        length
        for length, item in zip(lengths, facts, strict=True)
        if item[1] == InterventionState.EXISTING_PROVISION
    )
    upgrade = sum(
        length
        for length, item in zip(lengths, facts, strict=True)
        if item[1] == InterventionState.UPGRADE_REQUIRED
    )
    proposed = sum(
        length
        for length, item in zip(lengths, facts, strict=True)
        if item[1] == InterventionState.PROPOSED_NEW_LINK
    )
    prohibited = any(item[3] for item in facts)
    elevation_complete = _claim_complete(enrichments, "elevation")
    traffic_complete = _claim_complete(enrichments, "traffic")
    constraint_complete = _claim_complete(enrichments, "constraint")
    elevation = (
        sum(item.absolute_elevation_change_m or 0 for item in enrichments if item is not None)
        if elevation_complete
        else None
    )
    traffic_ids = tuple(
        sorted(
            {
                identifier
                for item in enrichments
                if item is not None
                for identifier in item.traffic_observation_ids
            }
        )
    )
    constraint_ids = tuple(
        sorted(
            {
                identifier
                for item in enrichments
                if item is not None
                for identifier in item.constraint_observation_ids
            }
        )
    )
    unknown = tuple(
        claim
        for claim, complete in (
            ("traffic", traffic_complete),
            ("elevation", elevation_complete),
            ("constraints", constraint_complete),
        )
        if not complete
    )
    payload = {
        "candidate_id": candidate_id,
        "obligation_id": obligation_id,
        "edge_ids": path.edge_ids,
        "reuse_class": reuse,
        "intervention_state": intervention,
        "geometry_wkt": _combine_geometry(path_edges),
    }
    return AssessedCandidateRecord(
        candidate_id=candidate_id,
        obligation_id=obligation_id,
        endpoints=endpoints,
        edge_ids=path.edge_ids,
        reverse_edge_ids=tuple(reversed(path.edge_ids)),
        geometry_wkt=str(payload["geometry_wkt"]),
        length_m=path.length_m,
        directness_m=path.length_m,
        reuse_class=reuse,
        intervention_state=intervention,
        alignment_bases=bases,
        primary_alignment_basis=primary,
        sections=tuple(sections),
        generating_strategy_ids=tuple(sorted(strategies)),
        existing_provision_m=existing,
        upgrade_required_m=upgrade,
        proposed_new_link_m=proposed,
        total_absolute_elevation_change_m=elevation,
        low_traffic_m=sum(
            length
            for length, item in zip(lengths, facts, strict=True)
            if item[0] == ReuseFirstCandidateClass.LOW_TRAFFIC_NON_A_ROAD
        ),
        major_road_m=sum(
            length
            for length, item in zip(lengths, facts, strict=True)
            if item[0] == ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE
        ),
        transition_count=max(0, len(sections) - 1),
        fragmentation_count=sum(1 for left, right in pairwise(facts) if left[0] != right[0]),
        traffic_observation_ids=traffic_ids,
        constraint_observation_ids=constraint_ids,
        evidence_ids=tuple(
            sorted(
                {
                    identifier
                    for edge, enrichment in zip(path_edges, enrichments, strict=True)
                    for identifier in _edge_evidence_ids(edge)
                    + _enrichment_evidence_ids(enrichment)
                }
            )
        ),
        unknown_facts=unknown,
        known_access_prohibition=prohibited,
        network_role=network_role,
        mandatory=mandatory,
        evidence_snapshot_fingerprint=evidence_snapshot_fingerprint,
        edge_evidence_fingerprint=edge_evidence_fingerprint,
        admission_disposition=("ineligible-known-access-prohibition" if prohibited else "eligible"),
        candidate_input=canonical_candidate,
    )


def discover_candidate_sets(request: CandidateDiscoveryRequest) -> CandidateDiscoveryResult:
    """Enumerate finite candidate alternatives and always return a typed result."""

    if not isinstance(request, CandidateDiscoveryRequest):
        raise ValueError("candidate discovery requires a CandidateDiscoveryRequest")
    graph = request.graph
    profile = request.profile
    edge_records = tuple(sorted(graph.edge_records, key=lambda item: item.directed_edge_id))
    edge_by_id = {item.directed_edge_id: item for item in edge_records}
    diagnostics: list[CandidateSearchDiagnostic] = []
    records_by_path: dict[tuple[str, tuple[str, ...]], AssessedCandidateRecord] = {}
    strategy_by_path: dict[tuple[str, tuple[str, ...]], set[str]] = defaultdict(set)
    sets: list[AlignmentCandidateSet] = []
    dispositions: list[CorridorObligationDisposition] = []
    requests: list[EvidenceRequest] = []
    gaps: list[CandidateSetGapEvidence] = []
    generated_count_by_obligation: dict[str, int] = defaultdict(int)
    evidence_snapshot_fingerprint = _snapshot_fingerprint(request.evidence_snapshot)
    enrichment_by_edge = {item.edge_id: item for item in request.edge_evidence}
    edge_evidence_fingerprint = (
        _fingerprint(tuple(request.edge_evidence)) if request.edge_evidence else None
    )
    for obligation in sorted(
        request.obligations,
        key=lambda item: str(_obligation_value(item, "obligation_id", "id", "connection_id")),
    ):
        obligation_id, origin, destination, network_role, mandatory = _obligation_details(
            obligation
        )
        endpoints = (origin, destination)
        direct = _direct_distance(edge_records, origin, destination, profile, obligation_id)
        if direct is None:
            provenance_direct = _direct_distance(
                edge_records,
                origin,
                destination,
                profile,
                obligation_id,
                include_prohibited=True,
            )
            if provenance_direct is not None:
                direct = provenance_direct
                diagnostics.append(
                    CandidateSearchDiagnostic(
                        "known-access-prohibition",
                        obligation_id,
                        "only prohibited routes connect the obligation endpoints; "
                        "retained for review",
                    )
                )
        # Preserve disconnected assets as reviewable diagnostics.  We use an
        # undirected reachability walk only for this diagnostic; direction and
        # access remain authoritative for candidate search.
        undirected: dict[str, set[str]] = defaultdict(set)
        for edge in edge_records:
            undirected[edge.from_node_id].add(edge.to_node_id)
            undirected[edge.to_node_id].add(edge.from_node_id)
        reachable = {origin}
        pending = [origin]
        while pending:
            node = pending.pop()
            for neighbour in sorted(undirected.get(node, ())):
                if neighbour not in reachable:
                    reachable.add(neighbour)
                    pending.append(neighbour)
        for edge in edge_records:
            if not ({edge.from_node_id, edge.to_node_id} & reachable):
                klass, _state, _basis, _prohibited = _edge_class(edge)
                if klass in {
                    ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION,
                    ReuseFirstCandidateClass.UPGRADEABLE_OFF_CARRIAGEWAY,
                }:
                    diagnostics.append(
                        CandidateSearchDiagnostic(
                            "disconnected-asset",
                            obligation_id,
                            "asset is retained but disconnected from obligation endpoint component",
                            edge_ids=(edge.directed_edge_id,),
                        )
                    )
        if direct is None:
            diagnostics.append(
                CandidateSearchDiagnostic(
                    "no-path", obligation_id, "no permitted path connects obligation endpoints"
                )
            )
            diagnostics.extend(
                CandidateSearchDiagnostic(
                    "known-access-prohibition",
                    obligation_id,
                    "a supplied edge is retained as provenance but blocked by access evidence",
                    edge_ids=(edge.directed_edge_id,),
                )
                for edge in edge_records
                if _edge_class(edge)[3]
            )
            diagnostics.extend(
                CandidateSearchDiagnostic(
                    "disconnected-asset",
                    obligation_id,
                    "obligation endpoint is absent or disconnected",
                )
                for node in (origin, destination)
                if node
                not in {edge.from_node_id for edge in edge_records}
                | {edge.to_node_id for edge in edge_records}
            )
            candidate_set = admit_candidate_set(
                profile.selection_profile,
                network_role=_canonical_role(network_role),
                endpoints=tuple(_canonical_endpoint(item) for item in endpoints),
                candidates=(),
            )
            sets.append(candidate_set)
            dispositions.append(
                CorridorObligationDisposition(
                    obligation_id, "gap", candidate_set.candidate_set_id, "no-path"
                )
            )
            gap = CandidateSetGapEvidence(
                obligation_id,
                endpoints,
                "no-path",
                tuple(
                    _stable_id("diagnostic", item.__dict__)
                    for item in diagnostics
                    if item.obligation_id == obligation_id
                ),
            )
            gaps.append(gap)
            requests.append(
                EvidenceRequest(
                    _stable_id("evidence-request", (obligation_id, "network-access")),
                    obligation_id,
                    "network-access",
                    "no permitted path exists",
                )
            )
            continue
        found_for_obligation: set[tuple[str, ...]] = set()
        for strategy in profile.strategies:
            paths = _enumerate_paths(
                edge_records,
                origin,
                destination,
                strategy,
                profile,
                diagnostics,
                obligation_id,
                include_prohibited=True,
            )
            for path in paths:
                path_is_prohibited = any(
                    _edge_class(edge_by_id[edge_id])[3] for edge_id in path.edge_ids
                )
                if (
                    path.length_m > direct * profile.detour_ceiling + 1e-9
                    and not path_is_prohibited
                ):
                    diagnostics.append(
                        CandidateSearchDiagnostic(
                            "detour-suppressed",
                            obligation_id,
                            "candidate exceeds discovery detour ceiling",
                            strategy.strategy_id,
                            edge_ids=path.edge_ids,
                        )
                    )
                    continue
                if path.edge_ids in found_for_obligation:
                    strategy_by_path[(obligation_id, path.edge_ids)].add(strategy.strategy_id)
                    diagnostics.append(
                        CandidateSearchDiagnostic(
                            "duplicate-suppressed",
                            obligation_id,
                            "candidate generated by more than one strategy; provenance merged",
                            strategy.strategy_id,
                            edge_ids=path.edge_ids,
                        )
                    )
                    continue
                if (
                    generated_count_by_obligation[obligation_id]
                    >= profile.maximum_generated_candidates
                ):
                    diagnostics.append(
                        CandidateSearchDiagnostic(
                            "candidate-budget",
                            obligation_id,
                            "maximum generated candidate budget exhausted",
                            strategy.strategy_id,
                            edge_ids=path.edge_ids,
                        )
                    )
                    continue
                found_for_obligation.add(path.edge_ids)
                generated_count_by_obligation[obligation_id] += 1
                strategy_by_path[(obligation_id, path.edge_ids)].add(strategy.strategy_id)
                records_by_path[(obligation_id, path.edge_ids)] = _materialise_candidate(
                    obligation_id,
                    endpoints,
                    path,
                    edge_by_id,
                    (strategy.strategy_id,),
                    direct,
                    network_role,
                    mandatory,
                    evidence_snapshot_fingerprint,
                    enrichment_by_edge,
                    edge_evidence_fingerprint,
                )
        candidates = [
            records_by_path[key] for key in sorted(records_by_path) if key[0] == obligation_id
        ]
        candidates.sort(key=lambda item: item.candidate_id)
        if not candidates:
            diagnostics.append(
                CandidateSearchDiagnostic(
                    "no-path",
                    obligation_id,
                    "strategies produced no candidate within configured bounds",
                )
            )
        for candidate in candidates:
            strategies = tuple(sorted(strategy_by_path[(obligation_id, candidate.edge_ids)]))
            if strategies != candidate.generating_strategy_ids:
                records_by_path[(obligation_id, candidate.edge_ids)] = AssessedCandidateRecord(
                    **{**candidate.__dict__, "generating_strategy_ids": strategies}
                )
            for fact in candidate.unknown_facts:
                requests.append(
                    EvidenceRequest(
                        _stable_id("evidence-request", (candidate.candidate_id, fact)),
                        obligation_id,
                        fact,
                        "optional candidate fact is missing",
                        candidate.candidate_id,
                    )
                )
        candidates = [
            records_by_path[key] for key in sorted(records_by_path) if key[0] == obligation_id
        ]
        canonical_candidates = tuple(item.canonical_candidate for item in candidates)
        candidate_set = admit_candidate_set(
            profile.selection_profile,
            network_role=_canonical_role(network_role),
            endpoints=tuple(_canonical_endpoint(item) for item in endpoints),
            candidates=canonical_candidates,
            mandatory_access_obligation_ids=(
                _canonical_identifier(obligation_id, prefix="obligation"),
            ),
        )
        admitted_ids = {
            item.candidate_id
            for item in candidate_set.admissions
            if item.disposition.value == "admitted"
        }
        for admission in candidate_set.admissions:
            if admission.rationale.value in {"profile-candidate-limit", "profile-limit"}:
                candidate = next(
                    item for item in candidates if item.candidate_id == admission.candidate_id
                )
                diagnostics.append(
                    CandidateSearchDiagnostic(
                        "admission-limit-suppressed",
                        obligation_id,
                        "generated candidate retained but rejected by the governed "
                        "selection profile",
                        candidate_id=candidate.candidate_id,
                        edge_ids=candidate.edge_ids,
                    )
                )
        sets.append(candidate_set)
        reason = candidate_set.generation_gap_reason.value
        dispositions.append(
            CorridorObligationDisposition(
                obligation_id,
                "candidates" if admitted_ids else "gap",
                candidate_set.candidate_set_id,
                reason,
            )
        )
        if not admitted_ids:
            gaps.append(
                CandidateSetGapEvidence(
                    obligation_id,
                    endpoints,
                    reason,
                    tuple(
                        _stable_id("diagnostic", item.__dict__)
                        for item in diagnostics
                        if item.obligation_id == obligation_id
                    ),
                )
            )
    records = tuple(sorted(records_by_path.values(), key=lambda item: item.candidate_id))
    diagnostics = sorted(
        diagnostics,
        key=lambda item: (
            item.obligation_id,
            item.code,
            item.strategy_id or "",
            item.candidate_id or "",
            item.edge_ids,
        ),
    )
    requests = sorted(
        {item.request_id: item for item in requests}.values(), key=lambda item: item.request_id
    )
    sets = sorted(sets, key=lambda item: item.candidate_set_id)
    dispositions = sorted(dispositions, key=lambda item: item.obligation_id)
    status = "complete" if not gaps else "complete-with-gaps"
    fingerprint = _fingerprint(
        {
            "profile": profile.fingerprint,
            "graph": getattr(graph, "graph_fingerprint", None),
            "evidence_snapshot_fingerprint": evidence_snapshot_fingerprint,
            "edge_evidence_fingerprint": edge_evidence_fingerprint,
            "candidate_sets": tuple(item.__dict__ for item in sets),
            "candidate_records": tuple(item.__dict__ for item in records),
            "diagnostics": tuple(item.__dict__ for item in diagnostics),
            "evidence_requests": tuple(item.__dict__ for item in requests),
        }
    )
    return CandidateDiscoveryResult(
        tuple(sets),
        records,
        tuple(dispositions),
        tuple(diagnostics),
        tuple(requests),
        fingerprint,
        status,
        tuple(gaps),
        evidence_snapshot_fingerprint,
        edge_evidence_fingerprint,
        profile.selection_profile.fingerprint,
    )


__all__ = [
    "AlignmentCandidateSet",
    "AssessedCandidateRecord",
    "CandidateDiscoveryProfile",
    "CandidateDiscoveryRequest",
    "CandidateDiscoveryResult",
    "CandidateEdgeEvidence",
    "CandidateReviewSection",
    "CandidateSearchDiagnostic",
    "CandidateSearchStrategy",
    "CandidateSearchStrategyConfig",
    "CandidateSetGapEvidence",
    "CorridorObligation",
    "CorridorObligationDisposition",
    "EvidenceRequest",
    "discover_candidate_sets",
]
