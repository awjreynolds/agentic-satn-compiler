"""Acceptance evidence contract for strategic-corridor route batching."""

from __future__ import annotations

import importlib.util
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def _benchmark_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "strategic_corridor_routing_benchmark",
        PROJECT / "scripts" / "benchmark_strategic_corridor_routing.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_benchmark_emits_an_adr0016_bound_semantic_manifest() -> None:
    module = _benchmark_module()

    result = module.benchmark((3,), commit="a" * 40)

    assert result["schema_version"] == "strategic-corridor-routing-benchmark/v2"
    assert result["commit"] == "a" * 40
    assert result["command"] == (
        "uv run python scripts/benchmark_strategic_corridor_routing.py"
    )
    assert result["machine"]["machine"]
    assert result["runtime"]["python"]
    assert result["runtime"]["networkx"]
    assert result["input_binding"]["synthetic_input_sha256"]
    assert result["governed_inputs"] == {
        "area_definition": "not-applicable-synthetic-roadgraph",
        "decision_fingerprint": "not-applicable-synthetic-roadgraph",
        "snapshot": "not-applicable-synthetic-roadgraph",
        "source_export": "not-applicable-synthetic-roadgraph",
        "store_state": "not-applicable-synthetic-roadgraph",
    }
    assert result["cache_state"] == (
        "fresh RoadGraph per anchor count; same process; OS cache uncontrolled"
    )
    assert result["exit_status"] == 0
    assert result["peak_rss_bytes"] > 0
    assert result["wall_seconds"] >= result["runs"][0]["elapsed_seconds"]
    assert result["runs"] == [
        {
            **result["runs"][0],
            "anchors": 3,
            "pairs": 3,
            "route_searches": 8,
            "route_options": 12,
        }
    ]
    assert len(result["runs"][0]["semantic_fingerprint"]) == 64
    assert len(result["semantic_fingerprint"]) == 64
    assert "passed" not in result
