"""Evidence facts for one legacy routed candidate.

This module only classifies the exact directed source edges carried by a
``RouteOption``.  It does not infer provenance from a candidate's source unit
or from a route role.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from satn.network_selection import CandidateSourceClass
from satn.routing import RoadGraph, RouteOption
from satn.tags import tag_values

_A_REF = re.compile(r"^a\s*\d+[a-z]?$", re.IGNORECASE)
_B_REF = re.compile(r"^b\s*\d+[a-z]?$", re.IGNORECASE)
_CYCLE_HIGHWAYS = {
    "cycleway",
    "cycle_track",
    "cycle-track",
    "greenway",
    "shared_use_path",
    "path-cycleway",
}
_CYCLE_BICYCLE_HIGHWAYS = {"path", "track"}
_CYCLE_BICYCLE_VALUES = {"yes", "designated", "official"}
_PROW_HIGHWAYS = {"footway", "path", "track", "byway", "restricted_byway"}
_QUIET_HIGHWAYS = {"residential", "unclassified", "service", "living_street"}
_CANONICAL_BASIS_ORDER = (
    "current-ncn",
    "ncn-link",
    "reclassified-ncn",
    "greenway",
    "mapped-cycleway",
    "cycle-track",
    "shared-use-path",
    "public-bridleway",
    "restricted-byway",
    "public-footpath",
    "byway-open-to-all-traffic",
    "prow-class-unknown",
    "local-connector",
    "a-road",
    "b-road",
    "classified-unnumbered-road",
    "unclassified-road",
    "proposed-new-corridor",
)
_BASIS_ORDER = {value: index for index, value in enumerate(_CANONICAL_BASIS_ORDER)}
_FALLBACK_SOURCE_ORDER = (
    CandidateSourceClass.VERIFIED_EXISTING_ASSET,
    CandidateSourceClass.A_ROAD_CORRIDOR,
    CandidateSourceClass.B_ROAD_CORRIDOR,
    CandidateSourceClass.OTHER_ROUTABLE,
)
_CYCLE_EVIDENCE_BASES = frozenset(
    {
        "current-ncn",
        "ncn-link",
        "reclassified-ncn",
        "greenway",
    }
)


@dataclass(frozen=True)
class RouteSourceFacts:
    """Canonical source facts derived from one exact routed edge sequence."""

    generation_source_class: CandidateSourceClass | None
    alignment_bases: tuple[str, ...]
    primary_alignment_basis: str | None
    complete: bool = True
    unresolved_edge_ids: tuple[str, ...] = ()


def derive_route_source_facts(
    route: RouteOption | Iterable[str],
    graph: RoadGraph,
    source_precedence: Sequence[CandidateSourceClass | str],
) -> RouteSourceFacts:
    """Classify exact route edges by physical extent and governed precedence.

    A ``RouteOption`` contributes ``directed_edge_ids``; a sequence is accepted
    for the legacy planning-graph adapter.  Each resolved directed edge is
    counted once using its existing ``length_m``.  If any requested edge is
    missing or ambiguous, the returned classification is deliberately
    unresolved while retaining the bases that were actually observed.
    """

    requested = _route_edge_ids(route)
    if not requested:
        return RouteSourceFacts(None, (), None, complete=False)
    edges_by_directed, edges_by_source = _edge_indexes(graph)
    resolved: list[Mapping[str, object]] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for edge_id in requested:
        if edge_id in seen:
            continue
        seen.add(edge_id)
        matches = edges_by_directed.get(edge_id)
        if matches is None:
            matches = edges_by_source.get(edge_id, ())
        if len(matches) != 1:
            unresolved.append(edge_id)
            continue
        resolved.append(matches[0])

    extents: defaultdict[CandidateSourceClass, float] = defaultdict(float)
    bases: set[str] = set()
    winning_group_bases: defaultdict[CandidateSourceClass, defaultdict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    precedence = _source_precedence(source_precedence)
    for edge in resolved:
        length = _edge_length(edge)
        if length is None:
            unresolved.append(_edge_identity(edge))
            continue
        groups = _edge_groups(edge)
        if not groups:
            unresolved.append(_edge_identity(edge))
            continue
        edge_bases = set().union(*groups.values())
        bases.update(edge_bases)
        winning_group = min(groups, key=lambda item: precedence[item])
        extents[winning_group] += length
        for basis in groups[winning_group]:
            winning_group_bases[winning_group][basis] += length

    ordered_bases = _sort_bases(bases)
    if unresolved:
        return RouteSourceFacts(
            generation_source_class=None,
            alignment_bases=ordered_bases,
            primary_alignment_basis=None,
            complete=False,
            unresolved_edge_ids=tuple(sorted(set(unresolved))),
        )
    if not extents:
        return RouteSourceFacts(None, ordered_bases, None, complete=False)

    generation_source_class = min(extents, key=lambda item: (-extents[item], precedence[item]))
    primary = min(
        winning_group_bases[generation_source_class],
        key=lambda item: (-winning_group_bases[generation_source_class][item], _basis_key(item)),
    )
    return RouteSourceFacts(
        generation_source_class=generation_source_class,
        alignment_bases=ordered_bases,
        primary_alignment_basis=primary,
    )


def _route_edge_ids(route: RouteOption | Iterable[str]) -> tuple[str, ...]:
    if isinstance(route, RouteOption):
        values = route.directed_edge_ids or route.edge_ids
    elif isinstance(route, str):
        values = (route,)
    else:
        values = route
    return tuple(str(value) for value in values if str(value))


def _edge_indexes(
    graph: RoadGraph | object,
) -> tuple[
    dict[str, tuple[Mapping[str, object], ...]],
    dict[str, tuple[Mapping[str, object], ...]],
]:
    by_directed: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    by_source: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    graph_network = getattr(graph, "graph", None)
    if graph_network is not None and hasattr(graph_network, "edges"):
        edge_values = (dict(attrs) for _left, _right, attrs in graph_network.edges(data=True))
    else:
        edge_values = (
            {
                "directed_edge_id": getattr(record, "directed_edge_id", None),
                "edge_id": getattr(record, "directed_edge_id", None),
                "source_edge_id": getattr(record, "source_edge_id", None),
                "length_m": getattr(record, "length_m", None),
                "highway": getattr(record, "highway", None),
                "ref": getattr(record, "ref", None),
                "bicycle": getattr(record, "bicycle", None),
                "cycle_alignment_bases": getattr(record, "cycle_alignment_bases", ()),
            }
            for record in getattr(graph, "edge_records", ())
        )
    for edge in edge_values:
        directed_id = str(edge.get("directed_edge_id") or "")
        source_id = str(edge.get("source_edge_id") or edge.get("edge_id") or "")
        if directed_id:
            by_directed[directed_id].append(edge)
        if source_id:
            by_source[source_id].append(edge)
    return (
        {key: tuple(value) for key, value in by_directed.items()},
        {key: tuple(value) for key, value in by_source.items()},
    )


def _source_precedence(
    source_precedence: Sequence[CandidateSourceClass | str],
) -> dict[CandidateSourceClass, int]:
    configured: list[CandidateSourceClass] = []
    for value in source_precedence:
        try:
            source = CandidateSourceClass(value)
        except (TypeError, ValueError):
            continue
        if source not in configured:
            configured.append(source)
    result = {source: index for index, source in enumerate(configured)}
    fallback_start = len(configured)
    for offset, source in enumerate(_FALLBACK_SOURCE_ORDER):
        result.setdefault(source, fallback_start + offset)
    return result


def _edge_identity(edge: Mapping[str, object]) -> str:
    return str(edge.get("directed_edge_id") or edge.get("edge_id") or "unknown-edge")


def _edge_length(edge: Mapping[str, object]) -> float | None:
    value = edge.get("length_m")
    try:
        length = float(value)
    except (TypeError, ValueError):
        return None
    return length if length >= 0 else None


def _edge_groups(edge: Mapping[str, object]) -> dict[CandidateSourceClass, set[str]]:
    highways = {value.casefold() for value in tag_values(edge.get("highway"))}
    refs = tuple(tag_values(edge.get("ref")))
    bicycles = {value.casefold() for value in tag_values(edge.get("bicycle"))}
    typed_bases = set(tag_values(edge.get("cycle_alignment_bases")))
    if not typed_bases.issubset(_CANONICAL_BASIS_ORDER):
        return {}

    cycle_bases = typed_bases & _CYCLE_EVIDENCE_BASES
    if highways & _CYCLE_HIGHWAYS:
        cycle_bases.add("mapped-cycleway")
    if "bridleway" in highways:
        cycle_bases.add("public-bridleway")
    if highways & _CYCLE_BICYCLE_HIGHWAYS and bicycles & _CYCLE_BICYCLE_VALUES:
        cycle_bases.add("mapped-cycleway")

    groups: dict[CandidateSourceClass, set[str]] = {}
    if cycle_bases:
        groups[CandidateSourceClass.VERIFIED_EXISTING_ASSET] = cycle_bases
    if any(_A_REF.fullmatch(value.strip()) for value in refs):
        groups[CandidateSourceClass.A_ROAD_CORRIDOR] = {"a-road"}
    if any(_B_REF.fullmatch(value.strip()) for value in refs):
        groups[CandidateSourceClass.B_ROAD_CORRIDOR] = {"b-road"}
    if groups:
        return groups
    if highways & _PROW_HIGHWAYS:
        return {CandidateSourceClass.OTHER_ROUTABLE: {"prow-class-unknown"}}
    if highways & _QUIET_HIGHWAYS:
        return {CandidateSourceClass.OTHER_ROUTABLE: {"local-connector"}}
    return {CandidateSourceClass.OTHER_ROUTABLE: {"proposed-new-corridor"}}


def _basis_key(value: str) -> tuple[int, str]:
    return (_BASIS_ORDER.get(value, len(_BASIS_ORDER)), value)


def _sort_bases(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values), key=_basis_key))


__all__ = ["RouteSourceFacts", "derive_route_source_facts"]
