"""Canonical local scope ranges for parallel-route evidence and discovery."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

NetworkScope = Literal["urban", "rural", "unresolved"]


class _ScopeSpan(Protocol):
    start_distance_m: float
    end_distance_m: float
    network_scope: NetworkScope


@dataclass(frozen=True)
class RouteScopeRange:
    """One effective local scope interval on a continuous route chain."""

    start_distance_m: float
    end_distance_m: float
    network_scope: NetworkScope


def effective_route_scope_ranges(
    route_length_m: float,
    default_scope: NetworkScope,
    explicit_spans: Iterable[_ScopeSpan],
) -> tuple[RouteScopeRange, ...]:
    """Fill every uncovered route distance with its declared default scope.

    Explicit spans are local evidence only.  This function deliberately returns
    ranges rather than route fragments, so neither discovery nor population
    capture can turn a scope boundary into a topological section boundary.
    """

    if route_length_m <= 0:
        return ()
    ranges: list[RouteScopeRange] = []
    cursor = 0.0
    for span in sorted(explicit_spans, key=lambda item: item.start_distance_m):
        start = min(max(float(span.start_distance_m), 0.0), route_length_m)
        end = min(max(float(span.end_distance_m), 0.0), route_length_m)
        if end <= start or end <= cursor:
            continue
        if start > cursor:
            ranges.append(RouteScopeRange(cursor, start, default_scope))
        ranges.append(RouteScopeRange(max(start, cursor), end, span.network_scope))
        cursor = end
    if cursor < route_length_m:
        ranges.append(RouteScopeRange(cursor, route_length_m, default_scope))
    return tuple(ranges)
