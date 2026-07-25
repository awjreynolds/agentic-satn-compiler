"""Record a repeatable operational benchmark for Cross-Spine assembly.

This deliberately writes a separate operational benchmark, not a governed
compiler/publication artifact.  Durations vary by machine and cache state;
the embedded Cross-Spine diagnostics remain deterministic logical counts.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from satn.agents import runtime_for
from satn.compiler import compile_network
from satn.models import AreaDefinition
from satn.pipeline import (
    area_definition_sha256,
    compilation_governed_input_fingerprint,
    snapshot_manifest_sha256,
)
from satn.sources import load_snapshot

BENCHMARK_SCHEMA_VERSION = "cross-spine-benchmark/v2"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = (PROJECT_ROOT / "build" / "benchmarks").resolve()
DEFAULT_WECA_AREA = Path("deployments/weca/area.yaml")
DEFAULT_OUTPUT = Path("build/benchmarks/weca-cross-spine-baseline.json")


def benchmark(config_path: Path) -> dict[str, object]:
    """Compile the configured local snapshot and return an operational baseline.

    The target must use the deterministic fake provider, so this command does
    not invoke a live agent while measuring compiler work.  It does not publish
    any artifacts or alter the configured deployment output directory.
    """
    area = AreaDefinition.from_yaml(config_path)
    if area.compilation.agent.provider != "fake":
        raise ValueError("Cross-Spine benchmark requires the deterministic fake agent provider")

    phase_durations: dict[str, float] = {}
    cross_spine_started: float | None = None
    cross_spine_finished: float | None = None

    def observe_cross_spine(
        assessed: int,
        total: int,
        _diagnostics: Mapping[str, object],
    ) -> None:
        nonlocal cross_spine_finished, cross_spine_started
        now = time.perf_counter()
        if cross_spine_started is None:
            cross_spine_started = now
        if assessed == total:
            cross_spine_finished = now

    snapshot_started = time.perf_counter()
    source = load_snapshot(area)
    phase_durations["snapshot_load"] = round(time.perf_counter() - snapshot_started, 6)

    compile_started = time.perf_counter()
    runtime = (
        runtime_for(area.compilation.agent)
        if area.compilation.agent.response_mode == "direct-runtime"
        and area.compilation.agent.review_statuses
        else None
    )
    compiled = compile_network(
        area,
        source,
        runtime,
        cross_spine_progress=observe_cross_spine,
    )
    phase_durations["network_compile"] = round(time.perf_counter() - compile_started, 6)
    phase_durations["cross_spine_assembly"] = round(
        (cross_spine_finished or time.perf_counter()) - (cross_spine_started or compile_started),
        6,
    )
    diagnostics = compiled.compilation_diagnostics["cross_spine"]
    if not isinstance(diagnostics, dict):
        raise ValueError("compiler did not emit Cross-Spine diagnostics")
    peak_work_counts = {
        key: value
        for key, value in diagnostics.items()
        if key.startswith("peak_") and isinstance(value, int)
    }
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "area_id": area.area_id,
        "snapshot_id": area.source.snapshot_id,
        # These are the same canonical identities used to bind a published
        # compiler run.  A timing result without them is not a useful baseline:
        # it could silently measure a different Area Definition or snapshot.
        "area_definition_sha256": area_definition_sha256(area),
        "snapshot_manifest_sha256": snapshot_manifest_sha256(area),
        "governed_input_fingerprint": compilation_governed_input_fingerprint(area),
        "execution": "local-fake-runtime-no-publication",
        "phase_durations_seconds": phase_durations,
        "cross_spine_diagnostics": diagnostics,
        "peak_work_counts": peak_work_counts,
    }


def benchmark_output_path(path: Path) -> Path:
    """Return a safe, ignored operational-output path or reject it.

    Benchmark JSON is deliberately disposable.  Keeping it beneath this one
    ignored root prevents a command intended for local timing from writing into
    a source, tracked artifact, deployment, or arbitrary user path.
    """
    source = Path(path)
    candidate = source if source.is_absolute() else PROJECT_ROOT / source
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(BENCHMARK_ROOT):
        raise ValueError(f"benchmark --output must be below ignored {BENCHMARK_ROOT}")
    if resolved == BENCHMARK_ROOT or resolved.suffix.lower() != ".json":
        raise ValueError("benchmark --output must name a JSON file below build/benchmarks")
    _reject_symlink_components(candidate, resolved)
    if resolved.exists():
        if not resolved.is_file() or resolved.is_symlink():
            raise ValueError("refusing to replace a non-file benchmark output")
        try:
            existing = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("refusing to replace an unsafe existing non-benchmark file") from error
        if (
            not isinstance(existing, dict)
            or existing.get("schema_version") != BENCHMARK_SCHEMA_VERSION
        ):
            raise ValueError("refusing to replace an unsafe existing non-benchmark file")
    return resolved


def _reject_symlink_components(candidate: Path, resolved: Path) -> None:
    """Do not let a syntactically safe output path traverse a symlink."""
    current = candidate
    while current != current.parent:
        if current.exists() and current.is_symlink():
            raise ValueError("benchmark --output must not traverse a symlink")
        if current in (PROJECT_ROOT, BENCHMARK_ROOT):
            return
        current = current.parent
    # Absolute paths outside the project are already rejected by the resolved
    # containment check; this guard covers a malformed path chain defensively.
    if not resolved.is_relative_to(BENCHMARK_ROOT):
        raise ValueError(f"benchmark --output must be below ignored {BENCHMARK_ROOT}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=DEFAULT_WECA_AREA,
        help="Area Definition to benchmark (defaults to WECA).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Operational baseline JSON path (defaults below ignored build/).",
    )
    args = parser.parse_args(argv)
    try:
        output = benchmark_output_path(args.output)
    except ValueError as error:
        parser.error(str(error))
    result = benchmark(args.config)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
