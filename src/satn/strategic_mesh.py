"""Deterministic assembly of a small Strategic Main Network mesh.

The module deliberately owns a narrow, authority-neutral seam.  It receives
candidate route sections that already have governed endpoint identifiers and
metric coordinates, then returns selected identifiers and explicit gaps.  It
does not join lines by visual intersection, create connector geometry, or
select Access Support routes as main-network coverage.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

MeshScope = Literal["urban", "rural"]
"""Scope labels for which the profile supplies a maximum coverage width."""

_CORRIDOR_ORDER = {
    "existing-cycleway": 0,
    "a-road": 1,
    "other": 2,
}
_CORRIDOR_ALIASES = {
    "cycleway": "existing-cycleway",
    "existing-cycleway-route": "existing-cycleway",
    "mapped-cycleway": "existing-cycleway",
    "verified-existing-asset": "existing-cycleway",
    "a-road-corridor": "a-road",
    "other-routable": "other",
    "b-road-corridor": "other",
}
_ACCESS_SUPPORT_ROLES = frozenset(
    {
        "access-support",
        "community-access",
        "community-access-obligation",
        "school-access",
        "school-access-obligation",
        "strategic-destination-access",
        "spine-access-branch",
        "spine-access-connection",
        "branch-meeting-connection",
        "backbone-access-association",
        "gateway-access-connection",
        "urban-community-access-gap",
        "urban-school-access-gap",
    }
)


def _text(value: object, field_name: str) -> str:
    value = getattr(value, "value", value)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{field_name} must be non-empty canonical text")
    return value


def _token(value: object, field_name: str) -> str:
    return _text(value, field_name).casefold().replace("_", "-").replace(" ", "-")


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be a finite number")
    return result


def _positive(value: object, field_name: str) -> float:
    result = _finite(value, field_name)
    if result <= 0:
        raise ValueError(f"{field_name} must be positive")
    return result


def _coordinates(value: object, field_name: str) -> tuple[tuple[float, float], ...]:
    """Canonicalise a metric line without changing or repairing its geometry."""

    if hasattr(value, "coords"):
        value = tuple(value.coords)  # type: ignore[union-attr]
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field_name} must contain coordinate pairs")
    try:
        raw_coordinates = tuple(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise ValueError(f"{field_name} must contain coordinate pairs") from error
    if len(raw_coordinates) < 2:
        raise ValueError(f"{field_name} must contain at least two coordinate pairs")

    normalised: list[tuple[float, float]] = []
    for index, raw_coordinate in enumerate(raw_coordinates):
        if isinstance(raw_coordinate, (str, bytes)):
            raise ValueError(f"{field_name}[{index}] must be a coordinate pair")
        try:
            pair = tuple(raw_coordinate)  # type: ignore[arg-type]
        except TypeError as error:
            raise ValueError(f"{field_name}[{index}] must be a coordinate pair") from error
        if len(pair) != 2:
            raise ValueError(f"{field_name}[{index}] must contain exactly two values")
        normalised.append(
            (
                _finite(pair[0], f"{field_name}[{index}].x"),
                _finite(pair[1], f"{field_name}[{index}].y"),
            )
        )

    line = LineString(normalised)
    if line.is_empty or line.length <= 0:
        raise ValueError(f"{field_name} must have positive length")
    return tuple(normalised)


def _point_coordinates(value: object, field_name: str) -> tuple[float, float]:
    if hasattr(value, "coords"):
        value = next(iter(value.coords))  # type: ignore[union-attr]
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field_name} must be a coordinate pair")
    try:
        pair = tuple(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise ValueError(f"{field_name} must be a coordinate pair") from error
    if len(pair) != 2:
        raise ValueError(f"{field_name} must contain exactly two values")
    return (_finite(pair[0], f"{field_name}.x"), _finite(pair[1], f"{field_name}.y"))


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_corridor_class(value: object) -> str:
    token = _token(value, "corridor class")
    token = _CORRIDOR_ALIASES.get(token, token)
    if token not in _CORRIDOR_ORDER:
        raise ValueError("corridor class must be existing-cycleway, a-road, or other")
    return token


@dataclass(frozen=True, slots=True)
class CandidateRouteSection:
    """One candidate metric line and its governed topological endpoints."""

    section_id: str
    start_node_id: str
    end_node_id: str
    coordinates: tuple[tuple[float, float], ...]
    corridor_class: str = "other"
    network_role: str = "main"
    is_access_support: bool = False
    network_scope: MeshScope = "urban"

    def __post_init__(self) -> None:
        object.__setattr__(self, "section_id", _text(self.section_id, "section id"))
        object.__setattr__(self, "start_node_id", _text(self.start_node_id, "start node id"))
        object.__setattr__(self, "end_node_id", _text(self.end_node_id, "end node id"))
        if self.start_node_id == self.end_node_id:
            raise ValueError("route section endpoints must be distinct")
        object.__setattr__(self, "coordinates", _coordinates(self.coordinates, "coordinates"))
        object.__setattr__(self, "corridor_class", _canonical_corridor_class(self.corridor_class))
        object.__setattr__(self, "network_role", _token(self.network_role, "network role"))
        if not isinstance(self.is_access_support, bool):
            raise ValueError("is_access_support must be boolean")
        scope = _token(self.network_scope, "network scope")
        if scope not in {"urban", "rural"}:
            raise ValueError("network scope must be urban or rural")
        object.__setattr__(self, "network_scope", scope)

    @property
    def geometry(self) -> LineString:
        """Return the supplied line as a metric geometry for distance checks."""

        return LineString(self.coordinates)

    @property
    def length_m(self) -> float:
        return float(self.geometry.length)

    @property
    def endpoint_ids(self) -> tuple[str, str]:
        return (self.start_node_id, self.end_node_id)

    @property
    def source_class(self) -> str:
        """Compatibility vocabulary for callers that call corridor class source class."""

        return self.corridor_class

    @property
    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "section_id": self.section_id,
            "start_node_id": self.start_node_id,
            "end_node_id": self.end_node_id,
            "coordinates": self.coordinates,
            "corridor_class": self.corridor_class,
            "network_role": self.network_role,
            "is_access_support": self.is_access_support,
            "network_scope": self.network_scope,
        }


@dataclass(frozen=True, slots=True)
class MeshCoveragePoint:
    """A governed point whose scope selects the coverage radius."""

    point_id: str
    coordinates: tuple[float, float]
    scope: MeshScope
    proof_radius_m: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "point_id", _text(self.point_id, "coverage point id"))
        coordinates = _point_coordinates(self.coordinates, "coverage point coordinates")
        object.__setattr__(self, "coordinates", coordinates)
        scope = _token(self.scope, "coverage point scope")
        if scope not in {"urban", "rural"}:
            raise ValueError("coverage point scope must be urban or rural")
        object.__setattr__(self, "scope", scope)
        if self.proof_radius_m is not None:
            object.__setattr__(
                self,
                "proof_radius_m",
                _positive(self.proof_radius_m, "coverage point proof radius"),
            )

    @property
    def geometry(self) -> Point:
        return Point(self.coordinates)

    @property
    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "point_id": self.point_id,
            "coordinates": self.coordinates,
            "scope": self.scope,
            "proof_radius_m": self.proof_radius_m,
        }


@dataclass(frozen=True, slots=True)
class StrategicMainNetworkProfile:
    """Frozen mesh assumptions, including the governed maximum widths."""

    urban_max_width_m: float = 500.0
    rural_max_width_m: float = 1500.0
    profile_id: str = "strategic-main-network-mesh/v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _text(self.profile_id, "profile id"))
        object.__setattr__(
            self,
            "urban_max_width_m",
            _positive(self.urban_max_width_m, "urban maximum mesh width"),
        )
        object.__setattr__(
            self,
            "rural_max_width_m",
            _positive(self.rural_max_width_m, "rural maximum mesh width"),
        )

    def maximum_width_m(self, scope: MeshScope) -> float:
        if scope == "urban":
            return self.urban_max_width_m
        if scope == "rural":
            return self.rural_max_width_m
        raise ValueError("scope must be urban or rural")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "contract": "satn-strategic-main-network-mesh/v1",
                "profile_id": self.profile_id,
                "urban_max_width_m": self.urban_max_width_m,
                "rural_max_width_m": self.rural_max_width_m,
            }
        )


@dataclass(frozen=True, slots=True)
class StrategicMainNetworkRequest:
    """Inputs accepted by :func:`assemble_strategic_main_network`."""

    route_sections: tuple[CandidateRouteSection, ...]
    coverage_points: tuple[MeshCoveragePoint, ...]
    profile: StrategicMainNetworkProfile = field(default_factory=StrategicMainNetworkProfile)
    preserve_connected_components: bool = False

    def __post_init__(self) -> None:
        sections = tuple(self.route_sections)
        if any(not isinstance(item, CandidateRouteSection) for item in sections):
            raise TypeError("route_sections must contain CandidateRouteSection values")
        if len({item.section_id for item in sections}) != len(sections):
            raise ValueError("route section ids must be unique")
        object.__setattr__(
            self,
            "route_sections",
            tuple(sorted(sections, key=lambda x: x.section_id)),
        )

        points = tuple(self.coverage_points)
        if any(not isinstance(item, MeshCoveragePoint) for item in points):
            raise TypeError("coverage_points must contain MeshCoveragePoint values")
        if len({item.point_id for item in points}) != len(points):
            raise ValueError("coverage point ids must be unique")
        object.__setattr__(self, "coverage_points", tuple(sorted(points, key=lambda x: x.point_id)))
        if not isinstance(self.profile, StrategicMainNetworkProfile):
            raise TypeError("profile must be a StrategicMainNetworkProfile")
        if not isinstance(self.preserve_connected_components, bool):
            raise TypeError("preserve_connected_components must be boolean")

    @property
    def candidate_sections(self) -> tuple[CandidateRouteSection, ...]:
        return self.route_sections

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "contract": "satn-strategic-main-network-request/v1",
                "route_sections": tuple(item.fingerprint_payload for item in self.route_sections),
                "coverage_points": tuple(item.fingerprint_payload for item in self.coverage_points),
                "profile": self.profile.fingerprint,
                "preserve_connected_components": self.preserve_connected_components,
            }
        )


@dataclass(frozen=True, slots=True)
class MeshGap:
    """A coverage point not proved by the returned connected main subset."""

    gap_id: str
    coverage_point_id: str
    scope: MeshScope
    reason: str
    candidate_section_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "gap_id", _text(self.gap_id, "gap id"))
        object.__setattr__(
            self,
            "coverage_point_id",
            _text(self.coverage_point_id, "coverage point id"),
        )
        scope = _token(self.scope, "gap scope")
        if scope not in {"urban", "rural"}:
            raise ValueError("gap scope must be urban or rural")
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "reason", _text(self.reason, "gap reason"))
        candidates = tuple(
            sorted(_text(item, "candidate section id") for item in self.candidate_section_ids)
        )
        if len(candidates) != len(set(candidates)):
            raise ValueError("gap candidate section ids must be unique")
        object.__setattr__(self, "candidate_section_ids", candidates)

    @property
    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "gap_id": self.gap_id,
            "coverage_point_id": self.coverage_point_id,
            "scope": self.scope,
            "reason": self.reason,
            "candidate_section_ids": self.candidate_section_ids,
        }


@dataclass(frozen=True, slots=True)
class StrategicMainNetworkResult:
    """Selected identifiers and explicit exclusions produced by the seam."""

    selected_section_ids: tuple[str, ...]
    access_support_section_ids: tuple[str, ...]
    nonselected_section_ids: tuple[str, ...]
    served_coverage_point_ids: tuple[str, ...]
    gaps: tuple[MeshGap, ...]
    fingerprint: str

    @property
    def gap_records(self) -> tuple[MeshGap, ...]:
        return self.gaps

    @property
    def mesh_gaps(self) -> tuple[MeshGap, ...]:
        return self.gaps

    @property
    def excluded_section_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.access_support_section_ids + self.nonselected_section_ids))

    @property
    def satisfied(self) -> bool:
        return not self.gaps


def _is_access_support(section: CandidateRouteSection) -> bool:
    return section.is_access_support or section.network_role in _ACCESS_SUPPORT_ROLES


def _is_connected(sections: Sequence[CandidateRouteSection]) -> bool:
    """Check only governed endpoint IDs; never infer a junction from geometry."""

    if not sections:
        return True
    adjacency: dict[str, set[str]] = {}
    for section in sections:
        start, end = section.endpoint_ids
        adjacency.setdefault(start, set()).add(end)
        adjacency.setdefault(end, set()).add(start)
    pending = [sections[0].start_node_id]
    visited: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        pending.extend(sorted(adjacency.get(node_id, ()), reverse=True))
    return all(node_id in visited for section in sections for node_id in section.endpoint_ids)


def _covered_point_ids(
    selected: Sequence[CandidateRouteSection],
    point_candidates: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    selected_ids = {section.section_id for section in selected}
    return tuple(
        point_id
        for point_id, candidate_ids in point_candidates.items()
        if selected_ids.intersection(candidate_ids)
    )


def derive_mesh_coverage_points(
    sections: Sequence[CandidateRouteSection],
    *,
    profile: StrategicMainNetworkProfile | None = None,
    maximum_width_m: float | None = None,
) -> tuple[MeshCoveragePoint, ...]:
    """Derive deterministic proof points from every supplied route line.

    Each scope's interval is one quarter of its profile maximum mesh width and each generated
    point is admitted at three eighths of that width.  Consequently every
    source-line position is within one eighth of that width (by line arclength)
    of a point, so the triangle inequality proves a selected union within half
    the maximum width: ``3W/8 + W/8 = W/2``.  The source line remains the only
    geometry used by the seam.
    """

    profile = profile or StrategicMainNetworkProfile()
    points: list[MeshCoveragePoint] = []
    for section in sorted(sections, key=lambda item: item.section_id):
        width = _positive(
            maximum_width_m
            if maximum_width_m is not None
            else profile.maximum_width_m(section.network_scope),
            "maximum mesh width",
        )
        spacing = width / 4.0
        proof_radius = 3.0 * width / 8.0
        line = section.geometry
        interval_count = max(1, math.ceil(line.length / spacing))
        for index in range(interval_count + 1):
            distance = line.length * index / interval_count
            point = line.interpolate(distance)
            points.append(
                MeshCoveragePoint(
                    point_id=f"{section.section_id}@{index}",
                    coordinates=(float(point.x), float(point.y)),
                    scope=section.network_scope,
                    proof_radius_m=proof_radius,
                )
            )
    return tuple(points)


def derive_urban_mesh_coverage_points(
    sections: Sequence[CandidateRouteSection],
    *,
    maximum_width_m: float = 500.0,
) -> tuple[MeshCoveragePoint, ...]:
    """Backward-compatible urban-specialized proof-point helper."""

    return derive_mesh_coverage_points(sections, maximum_width_m=maximum_width_m)


def _selection_score(
    selected: Sequence[CandidateRouteSection],
    point_candidates: dict[str, tuple[str, ...]],
) -> tuple[object, ...]:
    """Prefer coverage, then governed corridor order, then the smallest set."""

    covered = len(_covered_point_ids(selected, point_candidates))
    existing_cycleway_count = sum(item.corridor_class == "existing-cycleway" for item in selected)
    other_count = sum(item.corridor_class == "other" for item in selected)
    a_road_count = sum(item.corridor_class == "a-road" for item in selected)
    total_length = sum(item.length_m for item in selected)
    selected_ids = tuple(item.section_id for item in selected)
    return (
        -covered,
        -existing_cycleway_count,
        -a_road_count,
        other_count,
        len(selected),
        total_length,
        selected_ids,
    )


def _coverage_radius_m(
    point: MeshCoveragePoint,
    profile: StrategicMainNetworkProfile,
) -> float:
    profile_radius = profile.maximum_width_m(point.scope) / 2.0
    if point.proof_radius_m is None:
        return profile_radius
    return min(profile_radius, point.proof_radius_m)


def _point_candidates(
    sections: Sequence[CandidateRouteSection],
    points: Sequence[MeshCoveragePoint],
    profile: StrategicMainNetworkProfile,
) -> dict[str, tuple[str, ...]]:
    """Use one spatial index for all point-to-section eligibility queries."""

    ordered_sections = tuple(sorted(sections, key=lambda item: item.section_id))
    if not ordered_sections:
        return {point.point_id: () for point in points}
    tree = STRtree([section.geometry for section in ordered_sections])
    candidates: dict[str, tuple[str, ...]] = {}
    for point in points:
        indexes = tree.query(
            point.geometry,
            predicate="dwithin",
            distance=_coverage_radius_m(point, profile),
        )
        candidates[point.point_id] = tuple(
            sorted(ordered_sections[int(index)].section_id for index in indexes)
        )
    return candidates


def _endpoint_components(
    sections: Sequence[CandidateRouteSection],
) -> tuple[tuple[CandidateRouteSection, ...], ...]:
    ordered_sections = tuple(sorted(sections, key=lambda item: item.section_id))
    by_id = {item.section_id: item for item in ordered_sections}
    section_ids_by_node: dict[str, set[str]] = {}
    for section in ordered_sections:
        for node_id in section.endpoint_ids:
            section_ids_by_node.setdefault(node_id, set()).add(section.section_id)

    components: list[tuple[CandidateRouteSection, ...]] = []
    remaining_ids = set(by_id)
    while remaining_ids:
        seed_id = min(remaining_ids)
        pending = [seed_id]
        component_ids: set[str] = set()
        while pending:
            section_id = pending.pop()
            if section_id in component_ids:
                continue
            component_ids.add(section_id)
            section = by_id[section_id]
            for node_id in section.endpoint_ids:
                pending.extend(sorted(section_ids_by_node.get(node_id, ()), reverse=True))
        remaining_ids.difference_update(component_ids)
        components.append(tuple(by_id[item] for item in sorted(component_ids)))
    return tuple(components)


def _coverage_index(
    sections: Sequence[CandidateRouteSection],
    point_candidates: dict[str, tuple[str, ...]],
) -> tuple[dict[str, int], dict[str, tuple[str, ...]]]:
    selected_ids = {section.section_id for section in sections}
    counts = {
        point_id: sum(candidate_id in selected_ids for candidate_id in candidate_ids)
        for point_id, candidate_ids in point_candidates.items()
    }
    point_ids_by_section: dict[str, list[str]] = {section_id: [] for section_id in selected_ids}
    for point_id, candidate_ids in point_candidates.items():
        for section_id in candidate_ids:
            if section_id in selected_ids:
                point_ids_by_section.setdefault(section_id, []).append(point_id)
    return counts, {
        section_id: tuple(sorted(point_ids))
        for section_id, point_ids in point_ids_by_section.items()
    }


def _reduced_component(
    component: tuple[CandidateRouteSection, ...],
    point_candidates: dict[str, tuple[str, ...]],
) -> tuple[CandidateRouteSection, ...]:
    """Remove lower-priority sections while preserving this component's proof."""

    selected = list(component)
    counts, point_ids_by_section = _coverage_index(selected, point_candidates)
    initial_coverage = {point_id for point_id, count in counts.items() if count > 0}
    deletion_order = sorted(
        component,
        key=lambda item: (-_CORRIDOR_ORDER[item.corridor_class], item.section_id),
    )
    by_id = {item.section_id: item for item in component}
    for section in deletion_order:
        affected_points = point_ids_by_section.get(section.section_id, ())
        if any(
            counts[point_id] <= 1 for point_id in affected_points if point_id in initial_coverage
        ):
            continue
        trial = tuple(item for item in selected if item.section_id != section.section_id)
        if not _is_connected(trial):
            continue
        selected = list(trial)
        for point_id in affected_points:
            counts[point_id] -= 1
    return tuple(by_id[item.section_id] for item in sorted(selected, key=lambda x: x.section_id))


def _best_connected_subset(
    sections: Sequence[CandidateRouteSection],
    point_candidates: dict[str, tuple[str, ...]],
) -> tuple[CandidateRouteSection, ...]:
    """Reverse-delete each endpoint-connected component deterministically.

    A complete endpoint component is an initially proved connected candidate.
    Removing a section cannot improve either coverage or connectivity, so one
    reverse-deletion pass is inclusion-minimal for that component and avoids
    the exponential fixture-only subset search.
    """

    ordered_sections = tuple(sorted(sections, key=lambda item: item.section_id))
    if not ordered_sections:
        return ()

    reduced_components = [
        _reduced_component(component, point_candidates)
        for component in _endpoint_components(ordered_sections)
    ]

    return min(reduced_components, key=lambda item: _selection_score(item, point_candidates))


def _reduced_component_union(
    sections: Sequence[CandidateRouteSection],
    point_candidates: dict[str, tuple[str, ...]],
) -> tuple[CandidateRouteSection, ...]:
    """Reduce every endpoint component while allowing proven components to coexist."""

    ordered_sections = tuple(sorted(sections, key=lambda item: item.section_id))
    if not ordered_sections:
        return ()
    components = _endpoint_components(ordered_sections)
    component_ids_by_section_id = {
        section.section_id: frozenset(item.section_id for item in component)
        for component in components
        for section in component
    }
    selected = list(ordered_sections)
    counts, point_ids_by_section = _coverage_index(selected, point_candidates)
    initial_coverage = {point_id for point_id, count in counts.items() if count > 0}
    deletion_order = sorted(
        ordered_sections,
        key=lambda item: (-_CORRIDOR_ORDER[item.corridor_class], item.section_id),
    )
    for section in deletion_order:
        affected_points = point_ids_by_section.get(section.section_id, ())
        if any(
            counts[point_id] <= 1 for point_id in affected_points if point_id in initial_coverage
        ):
            continue
        component_ids = component_ids_by_section_id[section.section_id]
        trial = tuple(
            item
            for item in selected
            if item.section_id != section.section_id and item.section_id in component_ids
        )
        if trial and not _is_connected(trial):
            continue
        selected = [item for item in selected if item.section_id != section.section_id]
        for point_id in affected_points:
            counts[point_id] -= 1
    return tuple(sorted(selected, key=lambda item: item.section_id))


def assemble_strategic_main_network(
    request: StrategicMainNetworkRequest,
) -> StrategicMainNetworkResult:
    """Return the smallest connected main-route subset proven by ``request``.

    A point is covered only by a non-Access-Support section whose supplied
    geometry is no farther than half the profile maximum for that point's
    scope.  Endpoint IDs, rather than visual line intersections, establish
    connectivity.  If a complete proof is impossible, the best connected
    subset is returned and each unserved point is represented by a gap record.
    """

    if not isinstance(request, StrategicMainNetworkRequest):
        raise TypeError("request must be a StrategicMainNetworkRequest")

    support_sections = tuple(item for item in request.route_sections if _is_access_support(item))
    main_sections = tuple(item for item in request.route_sections if not _is_access_support(item))

    main_candidates = _point_candidates(main_sections, request.coverage_points, request.profile)
    support_candidates = _point_candidates(
        support_sections,
        request.coverage_points,
        request.profile,
    )

    selected_sections = (
        _reduced_component_union(main_sections, main_candidates)
        if request.preserve_connected_components
        else _best_connected_subset(main_sections, main_candidates)
    )
    selected_ids = tuple(sorted(item.section_id for item in selected_sections))
    selected_id_set = set(selected_ids)
    served_ids = tuple(
        sorted(
            point_id
            for point_id, candidate_ids in main_candidates.items()
            if selected_id_set.intersection(candidate_ids)
        )
    )
    served_set = set(served_ids)

    gaps: list[MeshGap] = []
    points_by_id = {item.point_id: item for item in request.coverage_points}
    for point_id in sorted(points_by_id):
        if point_id in served_set:
            continue
        point = points_by_id[point_id]
        main_candidate_ids = main_candidates[point_id]
        support_candidate_ids = support_candidates[point_id]
        if main_candidate_ids:
            reason = "disconnected-main-route"
            candidate_ids = main_candidate_ids
        elif support_candidate_ids:
            reason = "access-support-only"
            candidate_ids = support_candidate_ids
        else:
            reason = "no-main-route-within-scope-radius"
            candidate_ids = ()
        gaps.append(
            MeshGap(
                gap_id=f"coverage:{point_id}",
                coverage_point_id=point_id,
                scope=point.scope,
                reason=reason,
                candidate_section_ids=candidate_ids,
            )
        )

    nonselected_ids = tuple(
        sorted(item.section_id for item in main_sections if item.section_id not in selected_id_set)
    )
    support_ids = tuple(sorted(item.section_id for item in support_sections))
    gap_records = tuple(gaps)
    fingerprint = _digest(
        {
            "contract": "satn-strategic-main-network-result/v1",
            "request": request.fingerprint,
            "selected_section_ids": selected_ids,
            "access_support_section_ids": support_ids,
            "nonselected_section_ids": nonselected_ids,
            "served_coverage_point_ids": served_ids,
            "gaps": tuple(item.fingerprint_payload for item in gap_records),
        }
    )
    return StrategicMainNetworkResult(
        selected_section_ids=selected_ids,
        access_support_section_ids=support_ids,
        nonselected_section_ids=nonselected_ids,
        served_coverage_point_ids=served_ids,
        gaps=gap_records,
        fingerprint=fingerprint,
    )


# Small vocabulary aliases keep the seam easy to discover without introducing
# alternate implementations or constructor behaviour.
CoveragePoint = MeshCoveragePoint
MeshProfile = StrategicMainNetworkProfile
MeshRequest = StrategicMainNetworkRequest
MeshResult = StrategicMainNetworkResult


__all__ = [
    "CandidateRouteSection",
    "CoveragePoint",
    "MeshCoveragePoint",
    "MeshGap",
    "MeshProfile",
    "MeshRequest",
    "MeshResult",
    "StrategicMainNetworkProfile",
    "StrategicMainNetworkRequest",
    "StrategicMainNetworkResult",
    "assemble_strategic_main_network",
    "derive_mesh_coverage_points",
    "derive_urban_mesh_coverage_points",
]
