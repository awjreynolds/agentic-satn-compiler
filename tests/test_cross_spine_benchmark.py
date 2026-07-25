"""Operational Cross-Spine benchmark command contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT = Path(__file__).parents[1]


def _benchmark_module() -> object:
    specification = importlib.util.spec_from_file_location(
        "cross_spine_benchmark", PROJECT / "scripts" / "benchmark_cross_spine.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_benchmark_records_structured_phase_times_and_deterministic_peak_work(
    monkeypatch: object,
) -> None:
    module = _benchmark_module()
    area = SimpleNamespace(
        area_id="west-of-england",
        source=SimpleNamespace(snapshot_id="weca-osm-current"),
        compilation=SimpleNamespace(
            agent=SimpleNamespace(
                provider="fake",
                response_mode="direct-runtime",
                review_statuses=("amber",),
            )
        ),
    )
    diagnostics = {
        "schema_version": "cross-spine-diagnostics/v2",
        "peak_noded_graph_edges": 42,
        "peak_shortest_path_frontier": 5,
        "candidate_connectors": 7,
    }
    compiled = SimpleNamespace(compilation_diagnostics={"cross_spine": diagnostics})
    monkeypatch.setattr(module.AreaDefinition, "from_yaml", lambda _path: area)
    monkeypatch.setattr(module, "load_snapshot", lambda _area: {"network": "snapshot"})
    monkeypatch.setattr(module, "runtime_for", lambda _agent: "fake-runtime")
    monkeypatch.setattr(module, "area_definition_sha256", lambda _area: "a" * 64)
    monkeypatch.setattr(module, "snapshot_manifest_sha256", lambda _area: "b" * 64)
    monkeypatch.setattr(module, "compilation_governed_input_fingerprint", lambda _area: "c" * 64)

    def compile_stub(*_args: object, cross_spine_progress: object, **_kwargs: object) -> object:
        cross_spine_progress(0, 7, diagnostics)
        cross_spine_progress(7, 7, diagnostics)
        return compiled

    monkeypatch.setattr(module, "compile_network", compile_stub)

    result = module.benchmark(Path("deployments/weca/area.yaml"))

    assert result["schema_version"] == "cross-spine-benchmark/v2"
    assert result["area_id"] == "west-of-england"
    assert result["execution"] == "local-fake-runtime-no-publication"
    assert result["area_definition_sha256"] == "a" * 64
    assert result["snapshot_manifest_sha256"] == "b" * 64
    assert result["governed_input_fingerprint"] == "c" * 64
    assert result["cross_spine_diagnostics"] == diagnostics
    assert result["peak_work_counts"] == {
        "peak_noded_graph_edges": 42,
        "peak_shortest_path_frontier": 5,
    }
    assert set(result["phase_durations_seconds"]) == {
        "snapshot_load",
        "network_compile",
        "cross_spine_assembly",
    }


@pytest.mark.parametrize(
    "output",
    [
        "README.md",
        "deployments/weca/benchmark.json",
        "../outside.json",
    ],
)
def test_benchmark_cli_rejects_tracked_source_and_arbitrary_output_paths(
    output: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _benchmark_module()

    with pytest.raises(SystemExit) as raised:
        module.main(["--output", output])

    assert raised.value.code == 2
    assert "must be below ignored" in capsys.readouterr().err


def test_benchmark_output_refuses_an_existing_non_benchmark_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _benchmark_module()
    benchmark_root = tmp_path / "build" / "benchmarks"
    benchmark_root.mkdir(parents=True)
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "BENCHMARK_ROOT", benchmark_root)
    unsafe = benchmark_root / "important.json"
    unsafe.write_text('{"schema_version":"unrelated/v1"}', encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe existing non-benchmark"):
        module.benchmark_output_path(unsafe)


def test_benchmark_output_allows_replacing_its_own_versioned_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _benchmark_module()
    benchmark_root = tmp_path / "build" / "benchmarks"
    benchmark_root.mkdir(parents=True)
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "BENCHMARK_ROOT", benchmark_root)
    baseline = benchmark_root / "weca.json"
    baseline.write_text(
        '{"schema_version":"cross-spine-benchmark/v2"}', encoding="utf-8"
    )

    assert module.benchmark_output_path(baseline) == baseline
