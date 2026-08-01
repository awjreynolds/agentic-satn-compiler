"""Record deterministic strategic-corridor route-batching dimensions.

This is a compact synthetic benchmark, not a semantic routing fixture or a
release gate.  It deliberately measures all strategic roles over the same
finite anchor pairs and emits elapsed time only as an observation.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import subprocess
import sys
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from time import perf_counter

import geopandas as gpd
from shapely.geometry import LineString

from satn.routing import RoadGraph

ANCHOR_COUNTS = (10, 25, 50)
ROLES = ("direct", "strategic-spine", "ncn-informed", "low-traffic")
COMMAND = "uv run python scripts/benchmark_strategic_corridor_routing.py"
NOT_APPLICABLE = "not-applicable-synthetic-roadgraph"


def benchmark(
    anchor_counts: tuple[int, ...] = ANCHOR_COUNTS,
    *,
    commit: str | None = None,
) -> dict[str, object]:
    """Return one measured batch record for each requested anchor count."""

    started_benchmark = perf_counter()
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
        semantic_payload = [
            {
                "start": start,
                "end": end,
                "role": role,
                "edge_ids": option.edge_ids,
                "reverse_edge_ids": option.reverse_edge_ids,
                "geometry_wkb": option.geometry.wkb_hex,
                "bidirectional": option.bidirectional,
            }
            for (start, end), role_options in sorted(options.items())
            for role, option in role_options.items()
            if option is not None
        ]
        runs.append(
            {
                "anchors": anchor_count,
                "pairs": len(pairs),
                "route_searches": route_searches,
                "route_options": len(semantic_payload),
                "elapsed_seconds": elapsed_seconds,
                "semantic_fingerprint": _sha256(semantic_payload),
            }
        )
    input_binding = {
        "contract": "strategic-corridor-routing-synthetic-input/v1",
        "anchor_counts": list(anchor_counts),
        "roles": list(ROLES),
        "strategic_use": True,
        "network": "reciprocal 100 metre line with one anchor per node",
        "crs": "EPSG:27700",
    }
    governed_inputs = {
        "area_definition": NOT_APPLICABLE,
        "decision_fingerprint": NOT_APPLICABLE,
        "snapshot": NOT_APPLICABLE,
        "source_export": NOT_APPLICABLE,
        "store_state": NOT_APPLICABLE,
    }
    return {
        "schema_version": "strategic-corridor-routing-benchmark/v2",
        "commit": commit or _current_commit(),
        "recorded_at": datetime.now(UTC).isoformat(),
        "command": COMMAND,
        "machine": {
            "node": platform.node(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "power_mode": "not-recorded",
            "material_workloads": "not-recorded",
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "networkx": importlib.metadata.version("networkx"),
            "geopandas": importlib.metadata.version("geopandas"),
            "shapely": importlib.metadata.version("shapely"),
            "duckdb": NOT_APPLICABLE,
            "duckdb_spatial": NOT_APPLICABLE,
        },
        "input_binding": {
            **input_binding,
            "synthetic_input_sha256": _sha256(input_binding),
        },
        "governed_inputs": governed_inputs,
        "cache_state": (
            "fresh RoadGraph per anchor count; same process; OS cache uncontrolled"
        ),
        "exit_status": 0,
        "wall_seconds": perf_counter() - started_benchmark,
        "peak_rss_bytes": _peak_rss_bytes(),
        "roles": list(ROLES),
        "runs": runs,
        "result_counts": {
            "runs": len(runs),
            "route_options": sum(int(run["route_options"]) for run in runs),
        },
        "semantic_fingerprint": _sha256(
            [
                {
                    key: value
                    for key, value in run.items()
                    if key != "elapsed_seconds"
                }
                for run in runs
            ]
        ),
    }


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _current_commit() -> str:
    return subprocess.check_output(
        ("git", "rev-parse", "HEAD"),
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    ).strip()


def _peak_rss_bytes() -> int:
    maximum = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return maximum if sys.platform == "darwin" else maximum * 1024


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
