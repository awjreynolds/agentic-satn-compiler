"""Record deterministic strategic-corridor route-batching dimensions.

This is a compact synthetic benchmark, not a semantic routing fixture or a
release gate.  It deliberately measures all strategic roles over the same
finite anchor pairs and emits elapsed time only as an observation.
"""

from __future__ import annotations

import json
from itertools import combinations
from time import perf_counter

import geopandas as gpd
from shapely.geometry import LineString

from satn.routing import RoadGraph

ANCHOR_COUNTS = (10, 25, 50)
ROLES = ("direct", "strategic-spine", "ncn-informed", "low-traffic")


def benchmark(anchor_counts: tuple[int, ...] = ANCHOR_COUNTS) -> dict[str, object]:
    """Return one measured batch record for each requested anchor count."""

    runs: list[dict[str, object]] = []
    for anchor_count in anchor_counts:
        graph = RoadGraph(_line_network(anchor_count))
        anchors = tuple(f"node-{index:02d}" for index in range(anchor_count))
        pairs = tuple(combinations(anchors, 2))
        started_at = perf_counter()
        options, route_searches = graph.route_options_for_pairs(
            pairs,
            roles=ROLES,
            strategic_use=True,
        )
        elapsed_seconds = perf_counter() - started_at
        if options[pairs[-1]]["direct"] is None:
            raise RuntimeError("synthetic benchmark route is unexpectedly absent")
        runs.append(
            {
                "anchors": anchor_count,
                "pairs": len(pairs),
                "route_searches": route_searches,
                "elapsed_seconds": elapsed_seconds,
            }
        )
    return {
        "schema_version": "strategic-corridor-routing-benchmark/v1",
        "roles": list(ROLES),
        "runs": runs,
    }


def _line_network(anchor_count: int) -> gpd.GeoDataFrame:
    rows: list[dict[str, object]] = []
    for index in range(anchor_count - 1):
        rows.extend(
            (
                {
                    "osmid": f"forward-{index}",
                    "u": f"node-{index:02d}",
                    "v": f"node-{index + 1:02d}",
                    "length": 100,
                    "geometry": LineString([(index * 100, 0), ((index + 1) * 100, 0)]),
                },
                {
                    "osmid": f"reverse-{index}",
                    "u": f"node-{index + 1:02d}",
                    "v": f"node-{index:02d}",
                    "length": 100,
                    "geometry": LineString([((index + 1) * 100, 0), (index * 100, 0)]),
                },
            )
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=27700)


if __name__ == "__main__":
    print(json.dumps(benchmark(), sort_keys=True))
