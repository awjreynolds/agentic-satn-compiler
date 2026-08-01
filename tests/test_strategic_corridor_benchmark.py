"""Acceptance evidence contract for strategic-corridor route batching."""

from __future__ import annotations

import importlib.util
import json
import subprocess
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

    power_mode = {
        "observed": True,
        "power_source": "AC Power",
        "setting": "automatic",
    }
    material_workloads = {
        "observed": True,
        "other_material_workloads": False,
        "basis": "operator observation",
    }
    result = module.benchmark(
        (3,),
        commit="a" * 40,
        power_mode=power_mode,
        material_workloads=material_workloads,
    )

    assert result["schema_version"] == "strategic-corridor-routing-benchmark/v2"
    assert result["commit"] == "a" * 40
    assert result["command"] == (
        "uv run python scripts/benchmark_strategic_corridor_routing.py "
        "--material-workloads none"
    )
    assert result["machine"]["machine"]
    assert result["machine"]["power_mode"] == power_mode
    assert result["machine"]["material_workloads"] == material_workloads
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


def test_committed_manifest_replays_the_bound_semantic_oracle() -> None:
    module = _benchmark_module()
    manifest = json.loads(
        (
            PROJECT
            / "docs"
            / "benchmarks"
            / "strategic-corridor-routing-2026-08-01.json"
        ).read_text(encoding="utf-8")
    )

    replayed = module.benchmark(
        commit=manifest["commit"],
        power_mode=manifest["machine"]["power_mode"],
        material_workloads=manifest["machine"]["material_workloads"],
    )

    assert subprocess.run(
        ("git", "merge-base", "--is-ancestor", manifest["commit"], "HEAD"),
        cwd=PROJECT,
        check=False,
    ).returncode == 0
    assert manifest["machine"]["power_mode"]["observed"] is True
    assert manifest["machine"]["material_workloads"]["observed"] is True
    assert replayed["input_binding"] == manifest["input_binding"]
    assert replayed["semantic_fingerprint"] == manifest["semantic_fingerprint"]
    assert [
        {
            key: value
            for key, value in run.items()
            if key != "elapsed_seconds"
        }
        for run in replayed["runs"]
    ] == [
        {
            key: value
            for key, value in run.items()
            if key != "elapsed_seconds"
        }
        for run in manifest["runs"]
    ]
