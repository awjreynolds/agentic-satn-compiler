# ruff: noqa: E501 -- names mirror public benchmark evidence labels.
"""Operational Cross-Spine paired benchmark command contract."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from dataclasses import fields, replace
from enum import IntEnum, StrEnum
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from satn.compiler import CompiledNetwork

PROJECT = Path(__file__).parents[1]
WECA_BASELINE_INPUT_DIGESTS = {
    "area_definition_sha256": "24a03e50ccfe541ff637b9c75f15caa41ac452cc20667f31df5ad274ffbeae6a",
    "snapshot_manifest_sha256": "d4d8cbe37c13a6b9ae5d027693d64e89eab2edccf7b69afcdbec519883b1a988",
}
WECA_BASELINE_GOVERNED_INPUT_FINGERPRINT = (
    "90264ba7be42de07eae4dc441a9aba89c23f5447b6dc96b23ded946069de3d37"
)
CANONICAL_WECA_BENCHMARK = Path("deployments/weca/area-125-benchmark.yaml")
GOVERNED_OUTPUT_CONTRACT_FIELDS = tuple(field.name for field in fields(CompiledNetwork))


def _benchmark_module() -> object:
    specification = importlib.util.spec_from_file_location(
        "cross_spine_benchmark", PROJECT / "scripts" / "benchmark_cross_spine.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _bypass_historical_snapshot_preflight(
    monkeypatch: pytest.MonkeyPatch, module: object
) -> None:
    """Keep downstream paired-budget tests independent of ignored live inputs.

    The production canonical command deliberately verifies the retained
    historical snapshot before it starts workers.  Unit tests below exercise
    only the evidence and budget logic with synthetic worker results; they
    explicitly opt out of that separate filesystem preflight.  The dedicated
    tests retain coverage of missing and mismatched historical snapshots.
    """
    monkeypatch.setattr(module, "_weca_release_fixture_failures", lambda _path: [])


def _worker_result(
    *,
    mode: str,
    signature: str = "d" * 64,
    routes: object | None = None,
    root_pairs: object = 2211,
    candidate_searches: object = 2211,
    cpu: float = 4.0,
    rss: object = 100,
) -> dict[str, object]:
    if routes is None:
        routes = (
            candidate_searches
            if mode == "eager-reference"
            else min(99, candidate_searches)
            if isinstance(candidate_searches, int)
            else 0
        )
    avoided = (
        candidate_searches - routes
        if isinstance(candidate_searches, int) and isinstance(routes, int)
        else 0
    )
    bounds = 0 if mode == "eager-reference" else root_pairs
    skipped = 0 if mode == "eager-reference" else avoided
    unroutable = 0
    planning_searches = 0 if mode == "eager-reference" else 1
    planning_nodes = 0 if mode == "eager-reference" else 1
    exact_bounds = 0 if mode == "eager-reference" else root_pairs
    return {
        "schema_version": "cross-spine-benchmark/v4",
        "benchmark_mode": mode,
        **WECA_BASELINE_INPUT_DIGESTS,
        "governed_input_fingerprint": "c" * 64,
        "compiler_sha256": "e" * 64,
        "baseline": {
            "contract": "#125-weca-source-inclusive-baseline/v1",
            "governed_input_fingerprint": WECA_BASELINE_GOVERNED_INPUT_FINGERPRINT,
        },
        "governed_output_signature": signature,
        "governed_output_field_digests": {
            field_name: signature for field_name in GOVERNED_OUTPUT_CONTRACT_FIELDS
        },
        "phase_durations_seconds": {"network_compile": 3.0},
        "phase_process_cpu_seconds": {"network_compile": cpu},
        "peak_rss": {
            "bytes": rss,
            "supported": True,
            "unit": "test",
            "scope": "process-lifetime-high-water",
        },
        "cross_spine_diagnostics": {
            "schema_version": "cross-spine-diagnostics/v2",
            "root_pairs_considered": root_pairs,
            "root_pair_candidate_searches": candidate_searches,
            "root_pair_route_searches": routes,
            "root_pair_route_searches_avoided": avoided,
            "root_pair_candidate_bounds_enqueued": bounds,
            "root_pair_candidate_bounds_skipped_as_connected": skipped,
            "root_pair_candidate_bounds_skipped_as_unroutable": unroutable,
            "root_group_distance_planning_searches": planning_searches,
            "root_group_distance_planning_nodes_settled": planning_nodes,
            "root_pair_exact_distance_bounds": exact_bounds,
            "meeting_agent_evaluations": 1,
            "candidate_connectors": 1,
            "authoritative_connectors": 1,
            "route_refinement_findings": 0,
            "noded_graphs_built": 1,
            "noded_graph_nodes_total": 1,
            "noded_graph_edges_total": 1,
            "peak_noded_graph_nodes": 1,
            "peak_noded_graph_edges": 1,
            "root_candidate_nodes_examined": 1,
            "eligible_root_endpoint_candidates": 1,
            "endpoint_pairs_considered": 1,
            "weighted_shortest_path_searches": 1,
            "weighted_shortest_path_nodes_settled": 1,
            "weighted_shortest_path_edge_relaxations": 1,
            "peak_shortest_path_frontier": 1,
            "deterministic_path_nodes_selected": 1,
            "connector_traversal_attempts": 1,
        },
    }


def test_worker_records_pinned_identity_and_exact_governed_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _benchmark_module()
    area = SimpleNamespace(
        area_id="fixture",
        source=SimpleNamespace(snapshot_id="fixture-osm-current"),
        compilation=SimpleNamespace(
            agent=SimpleNamespace(provider="fake", response_mode="direct-runtime", review_statuses=("amber",))
        ),
    )
    diagnostics = _worker_result(mode="lazy")["cross_spine_diagnostics"]
    compiled = SimpleNamespace(compilation_diagnostics={"cross_spine": diagnostics})
    monkeypatch.setattr(module.AreaDefinition, "from_yaml", lambda _path: area)
    monkeypatch.setattr(module, "load_snapshot", lambda _area: {"network": "snapshot"})
    monkeypatch.setattr(module, "runtime_for", lambda _agent: "fake-runtime")
    monkeypatch.setattr(module, "area_definition_sha256", lambda _area: "a" * 64)
    monkeypatch.setattr(module, "snapshot_manifest_sha256", lambda _area: "b" * 64)
    monkeypatch.setattr(module, "compilation_governed_input_fingerprint", lambda _area: "c" * 64)
    monkeypatch.setattr(
        module,
        "governed_output_contract",
        lambda _compiled: {
            field_name: {"fixture": field_name}
            for field_name in module.GOVERNED_OUTPUT_CONTRACT_FIELDS
        },
    )
    monkeypatch.setattr(module, "_governed_contract_digest", lambda _contract: "d" * 64)
    monkeypatch.setattr(module, "_peak_rss_measurement", lambda: {"bytes": 123, "supported": True, "unit": "test", "scope": "test"})

    def compile_stub(*_args: object, cross_spine_progress: object, **_kwargs: object) -> object:
        cross_spine_progress(0, 1, diagnostics)
        cross_spine_progress(1, 1, diagnostics)
        return compiled

    monkeypatch.setattr(module, "compile_network", compile_stub)
    result = module.benchmark(Path("examples/fixture/council.yaml"))

    assert result["schema_version"] == "cross-spine-benchmark/v4"
    assert result["governed_input_fingerprint"] == "c" * 64
    assert result["governed_output_signature"] == "d" * 64
    assert result["peak_rss"]["bytes"] == 123
    assert result["protocol"]["workers"].startswith("separate subprocesses")
    assert result["cross_spine_diagnostics"] == diagnostics
    # This is the real worker result, carried across the same strict JSON
    # boundary used by the paired subprocess orchestrator before validation.
    worker_wire = json.dumps(result, sort_keys=True, allow_nan=False)
    worker_result = json.loads(worker_wire, parse_constant=module._reject_non_json_number)
    assert module._validate_worker_result(worker_result, expected_mode="lazy") == []


def test_worker_diagnostics_refuse_non_json_values_before_evidence_validation() -> None:
    module = _benchmark_module()
    with pytest.raises(ValueError, match="strict plain JSON mapping"):
        module._plain_json_mapping(
            {"root_pair_route_searches": np.float64("nan")},
            label="Cross-Spine diagnostics",
        )


def test_worker_eager_reference_is_scoped_to_the_private_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _benchmark_module()
    area = SimpleNamespace(
        area_id="fixture",
        source=SimpleNamespace(snapshot_id="fixture-osm-current"),
        compilation=SimpleNamespace(agent=SimpleNamespace(provider="fake", response_mode="direct-runtime", review_statuses=("amber",))),
    )
    diagnostics = _worker_result(
        mode="eager-reference", routes=24, root_pairs=24, candidate_searches=24
    )["cross_spine_diagnostics"]
    compiled = SimpleNamespace(compilation_diagnostics={"cross_spine": diagnostics})
    monkeypatch.setattr(module.AreaDefinition, "from_yaml", lambda _path: area)
    monkeypatch.setattr(module, "load_snapshot", lambda _area: {"network": "snapshot"})
    monkeypatch.setattr(module, "runtime_for", lambda _agent: "fake-runtime")
    monkeypatch.setattr(module, "area_definition_sha256", lambda _area: "a" * 64)
    monkeypatch.setattr(module, "snapshot_manifest_sha256", lambda _area: "b" * 64)
    monkeypatch.setattr(module, "compilation_governed_input_fingerprint", lambda _area: "c" * 64)
    monkeypatch.setattr(
        module,
        "governed_output_contract",
        lambda _compiled: {
            field_name: {"fixture": field_name}
            for field_name in module.GOVERNED_OUTPUT_CONTRACT_FIELDS
        },
    )
    monkeypatch.setattr(module, "_governed_contract_digest", lambda _contract: "d" * 64)
    captured: dict[str, object] = {}

    def compile_stub(*_args: object, **kwargs: object) -> object:
        captured["meetings"] = module.backbone_module._cross_spine_meetings
        kwargs["cross_spine_progress"](1, 1, compiled.compilation_diagnostics["cross_spine"])
        return compiled

    monkeypatch.setattr(module, "compile_network", compile_stub)
    original = module.backbone_module._cross_spine_meetings
    result = module.benchmark(Path("examples/fixture/council.yaml"), eager_reference=True)
    assert result["benchmark_mode"] == "eager-reference"
    assert captured["meetings"] is not original
    assert module.backbone_module._cross_spine_meetings is original


def test_paired_benchmark_passes_only_a_bound_identical_eager_lazy_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _benchmark_module()
    _bypass_historical_snapshot_preflight(monkeypatch, module)
    eager = _worker_result(mode="eager-reference", cpu=5.0, rss=100)
    lazy = _worker_result(mode="lazy", cpu=4.0, rss=110)
    monkeypatch.setattr(module, "_run_worker", lambda _path, mode: eager if mode == "eager" else lazy)
    result = module.paired_benchmark(CANONICAL_WECA_BENCHMARK)
    assert result["passed"] is True
    assert result["reasons"] == []
    assert result["release_budget"]["observed"]["lazy_root_pair_route_searches"] == 99
    logical_baseline = result["release_budget"]["logical_baseline"]
    assert logical_baseline["passed"] is True
    assert logical_baseline["thresholds"] == {
        "root_pairs_considered": 2211,
        "root_pair_candidate_searches": 2211,
    }
    assert logical_baseline["observed"]["eager"] == logical_baseline["thresholds"]
    assert logical_baseline["observed"]["lazy"] == logical_baseline["thresholds"]
    assert result["release_budget"]["weca_release_input_contract"] == {
        "applies": True,
        "contract": "#125-weca-area-snapshot-and-current-compiler-identity/v2",
        "expected": WECA_BASELINE_INPUT_DIGESTS,
        "observed": {
            "eager": {
                **WECA_BASELINE_INPUT_DIGESTS,
                "governed_input_fingerprint": "c" * 64,
                "compiler_sha256": "e" * 64,
            },
            "lazy": {
                **WECA_BASELINE_INPUT_DIGESTS,
                "governed_input_fingerprint": "c" * 64,
                "compiler_sha256": "e" * 64,
            },
        },
        "passed": True,
        "reasons": [],
    }
    assert result["release_budget"]["baseline_governed_input_fingerprint"] == {
        "contract": "#125-weca-source-inclusive-baseline/v1",
        "expected": WECA_BASELINE_GOVERNED_INPUT_FINGERPRINT,
        "observed": {
            "eager": WECA_BASELINE_GOVERNED_INPUT_FINGERPRINT,
            "lazy": WECA_BASELINE_GOVERNED_INPUT_FINGERPRINT,
        },
        "passed": True,
        "reasons": [],
    }


def test_paired_benchmark_persists_the_first_governed_field_difference_without_weakening_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _benchmark_module()
    _bypass_historical_snapshot_preflight(monkeypatch, module)
    eager = _worker_result(mode="eager-reference", cpu=5.0, signature="d" * 64)
    lazy = _worker_result(mode="lazy", cpu=4.0, signature="d" * 64)
    lazy["governed_output_signature"] = "e" * 64
    lazy["governed_output_field_digests"]["places"] = "e" * 64
    monkeypatch.setattr(module, "_run_worker", lambda _path, mode: eager if mode == "eager" else lazy)

    result = module.paired_benchmark(CANONICAL_WECA_BENCHMARK)

    assert result["passed"] is False
    assert "governed-output-signature-mismatch" in result["reasons"]
    assert "governed-output-field-digest-mismatch" in result["reasons"]
    assert result["governed_output_parity"] == {
        "passed": False,
        "reason": "governed-output-field-digest-mismatch",
        "first_difference": {
            "field": "places",
            "eager_sha256": "d" * 64,
            "lazy_sha256": "e" * 64,
        },
    }


def test_paired_benchmark_rejects_missing_governed_field_digest_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _benchmark_module()
    _bypass_historical_snapshot_preflight(monkeypatch, module)
    eager = _worker_result(mode="eager-reference", cpu=5.0)
    lazy = _worker_result(mode="lazy", cpu=4.0)
    lazy["governed_output_field_digests"].pop("places")
    monkeypatch.setattr(module, "_run_worker", lambda _path, mode: eager if mode == "eager" else lazy)

    result = module.paired_benchmark(CANONICAL_WECA_BENCHMARK)

    assert result["passed"] is False
    assert (
        "lazy-worker-evidence-invalid:governed-output-field-digests-invalid-or-missing"
        in result["reasons"]
    )
    assert result["governed_output_parity"] == {
        "passed": False,
        "reason": "governed-output-field-digests-invalid-or-missing",
        "first_difference": None,
    }


def test_weca_paired_benchmark_records_not_pins_the_historical_source_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _benchmark_module()
    _bypass_historical_snapshot_preflight(monkeypatch, module)
    eager = _worker_result(mode="eager-reference", cpu=5.0)
    lazy = _worker_result(mode="lazy", cpu=4.0)
    # A changed implementation has a new current governed-input fingerprint
    # and compiler digest, but the two isolated schedules must agree exactly.
    eager["governed_input_fingerprint"] = lazy["governed_input_fingerprint"] = "a" * 64
    eager["compiler_sha256"] = lazy["compiler_sha256"] = "b" * 64
    monkeypatch.setattr(module, "_run_worker", lambda _path, mode: eager if mode == "eager" else lazy)

    result = module.paired_benchmark(CANONICAL_WECA_BENCHMARK)

    assert result["passed"] is True
    assert result["release_budget"]["weca_release_input_contract"]["passed"] is True
    assert result["release_budget"]["baseline_governed_input_fingerprint"]["passed"] is True


def test_weca_paired_benchmark_rejects_a_tampered_historical_baseline_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _benchmark_module()
    _bypass_historical_snapshot_preflight(monkeypatch, module)
    eager = _worker_result(mode="eager-reference", cpu=5.0)
    lazy = _worker_result(mode="lazy", cpu=4.0)
    lazy["baseline"]["governed_input_fingerprint"] = "0" * 64
    monkeypatch.setattr(module, "_run_worker", lambda _path, mode: eager if mode == "eager" else lazy)

    result = module.paired_benchmark(CANONICAL_WECA_BENCHMARK)

    assert result["passed"] is False
    assert result["release_budget"]["baseline_governed_input_fingerprint"]["passed"] is False


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda result: result["cross_spine_diagnostics"].pop("root_pair_route_searches"), "lazy-root-pair-route-searches-missing"),
        (lambda result: result["cross_spine_diagnostics"].pop("root_pairs_considered"), "weca-logical-baseline-lazy-root_pairs_considered-missing-or-invalid"),
        (lambda result: result["cross_spine_diagnostics"].update({"root_pair_candidate_searches": 2210}), "weca-logical-baseline-lazy-root_pair_candidate_searches-expected-2211-got-2210"),
        (lambda result: result.update({"governed_output_signature": "different"}), "governed-output-signature-mismatch"),
        (lambda result: result["peak_rss"].update({"supported": False, "bytes": None}), "peak-rss-unsupported"),
        (lambda result: result["phase_process_cpu_seconds"].update({"network_compile": 5.0}), "lazy-cpu-not-less-than-eager"),
    ],
)
def test_paired_benchmark_fails_closed_for_missing_or_failing_evidence(
    monkeypatch: pytest.MonkeyPatch,
    mutate: object,
    reason: str,
) -> None:
    module = _benchmark_module()
    _bypass_historical_snapshot_preflight(monkeypatch, module)
    eager = _worker_result(mode="eager-reference", cpu=5.0)
    lazy = _worker_result(mode="lazy", cpu=4.0)
    assert callable(mutate)
    mutate(lazy)
    monkeypatch.setattr(module, "_run_worker", lambda _path, mode: eager if mode == "eager" else lazy)
    result = module.paired_benchmark(CANONICAL_WECA_BENCHMARK)
    assert result["passed"] is False
    assert reason in result["reasons"]


@pytest.mark.parametrize(
    ("mode", "counter", "value", "reason"),
    [
        ("eager", "root_pairs_considered", None, "weca-logical-baseline-eager-root_pairs_considered-missing-or-invalid"),
        ("eager", "root_pair_candidate_searches", 2210, "weca-logical-baseline-eager-root_pair_candidate_searches-expected-2211-got-2210"),
        ("lazy", "root_pairs_considered", 2210, "weca-logical-baseline-lazy-root_pairs_considered-expected-2211-got-2210"),
        ("lazy", "root_pair_candidate_searches", None, "weca-logical-baseline-lazy-root_pair_candidate_searches-missing-or-invalid"),
    ],
)
def test_weca_paired_release_binds_both_workers_to_the_pinned_logical_baseline(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    counter: str,
    value: object,
    reason: str,
) -> None:
    module = _benchmark_module()
    _bypass_historical_snapshot_preflight(monkeypatch, module)
    eager = _worker_result(mode="eager-reference", cpu=5.0)
    lazy = _worker_result(mode="lazy", cpu=4.0)
    target = eager if mode == "eager" else lazy
    if value is None:
        target["cross_spine_diagnostics"].pop(counter)
    else:
        target["cross_spine_diagnostics"][counter] = value
    monkeypatch.setattr(module, "_run_worker", lambda _path, worker_mode: eager if worker_mode == "eager" else lazy)
    result = module.paired_benchmark(CANONICAL_WECA_BENCHMARK)
    assert result["passed"] is False
    assert reason in result["release_budget"]["logical_baseline"]["reasons"]


@pytest.mark.parametrize("digest", sorted(WECA_BASELINE_INPUT_DIGESTS))
@pytest.mark.parametrize("mode", ["eager", "lazy", "both"])
def test_weca_paired_release_binds_each_worker_to_each_pinned_input_digest(
    monkeypatch: pytest.MonkeyPatch,
    digest: str,
    mode: str,
) -> None:
    module = _benchmark_module()
    _bypass_historical_snapshot_preflight(monkeypatch, module)
    eager = _worker_result(mode="eager-reference", cpu=5.0)
    lazy = _worker_result(mode="lazy", cpu=4.0)
    tampered_value = "0" * 64
    if mode in {"eager", "both"}:
        eager[digest] = tampered_value
    if mode in {"lazy", "both"}:
        lazy[digest] = tampered_value
    monkeypatch.setattr(module, "_run_worker", lambda _path, worker_mode: eager if worker_mode == "eager" else lazy)

    result = module.paired_benchmark(CANONICAL_WECA_BENCHMARK)

    contract = result["release_budget"]["weca_release_input_contract"]
    assert result["passed"] is False
    assert contract["passed"] is False
    assert contract["expected"] == WECA_BASELINE_INPUT_DIGESTS
    expected_modes = ("eager", "lazy") if mode == "both" else (mode,)
    for worker_mode in expected_modes:
        assert (
            f"weca-release-input-{worker_mode}-{digest}-expected-"
            f"{WECA_BASELINE_INPUT_DIGESTS[digest]}-got-{tampered_value}"
        ) in contract["reasons"]


def test_paired_benchmark_rejects_tampered_input_and_cli_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _benchmark_module()
    _bypass_historical_snapshot_preflight(monkeypatch, module)
    eager = _worker_result(mode="eager-reference", cpu=5.0)
    lazy = _worker_result(mode="lazy", cpu=4.0)
    lazy["snapshot_manifest_sha256"] = "x" * 64
    monkeypatch.setattr(module, "_run_worker", lambda _path, mode: eager if mode == "eager" else lazy)
    result = module.paired_benchmark(CANONICAL_WECA_BENCHMARK)
    assert result["passed"] is False
    assert "pinned-input-mismatch-or-missing:snapshot_manifest_sha256" in result["reasons"]
    benchmark_root = tmp_path / "build" / "benchmarks"
    benchmark_root.mkdir(parents=True)
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "BENCHMARK_ROOT", benchmark_root)
    monkeypatch.setattr(module, "paired_benchmark", lambda _path: {"passed": False})
    assert module.main(["area.yaml", "--output", "build/benchmarks/fail.json"]) == 1


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda result: result.pop("governed_output_signature"), "governed_output_signature-invalid-or-missing"),
        (lambda result: result["phase_durations_seconds"].update({"network_compile": float("nan")}), "phase_durations_seconds-metric-invalid:'network_compile'"),
        (lambda result: result["phase_process_cpu_seconds"].update({"network_compile": float("inf")}), "phase_process_cpu_seconds-metric-invalid:'network_compile'"),
        (lambda result: result["cross_spine_diagnostics"].update({"root_pair_route_searches": -1}), "cross-spine-counter-invalid-or-missing:root_pair_route_searches"),
        (lambda result: result["cross_spine_diagnostics"].update({"root_pair_route_searches": True}), "cross-spine-counter-invalid-or-missing:root_pair_route_searches"),
        (lambda result: result["cross_spine_diagnostics"].update({"root_pairs_considered": -1}), "cross-spine-counter-invalid-or-missing:root_pairs_considered"),
        (lambda result: result["cross_spine_diagnostics"].update({"root_pair_candidate_searches": True}), "cross-spine-counter-invalid-or-missing:root_pair_candidate_searches"),
        (lambda result: result.update({"schema_version": "wrong/v1"}), "schema-version-invalid"),
        (lambda result: result.update({"benchmark_mode": "wrong"}), "benchmark-mode-invalid"),
        (lambda result: result.update({"area_definition_sha256": "A" * 64}), "area_definition_sha256-invalid-or-missing"),
    ],
)
def test_paired_benchmark_rejects_malformed_worker_evidence(
    monkeypatch: pytest.MonkeyPatch,
    mutate: object,
    reason: str,
) -> None:
    module = _benchmark_module()
    _bypass_historical_snapshot_preflight(monkeypatch, module)
    eager = _worker_result(mode="eager-reference", cpu=5.0)
    lazy = _worker_result(mode="lazy", cpu=4.0)
    assert callable(mutate)
    mutate(lazy)
    monkeypatch.setattr(module, "_run_worker", lambda _path, mode: eager if mode == "eager" else lazy)
    result = module.paired_benchmark(CANONICAL_WECA_BENCHMARK)
    assert result["passed"] is False
    assert any(reason in item for item in result["reasons"])


def test_worker_parser_rejects_non_standard_json_nan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _benchmark_module()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout='{"x":NaN}', stderr=""),
    )
    with pytest.raises(RuntimeError, match="invalid JSON"):
        module._run_worker(Path("area.yaml"), "lazy")


def test_paired_workers_fix_hash_seed_for_same_runtime_signature_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _benchmark_module()
    observed: dict[str, object] = {}

    def run_stub(*_args: object, **kwargs: object) -> object:
        observed.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(module.subprocess, "run", run_stub)
    assert module._run_worker(Path("area.yaml"), "lazy") == {}
    assert observed["env"]["PYTHONHASHSEED"] == "0"


@pytest.mark.parametrize("invalid_number", ["NaN", "Infinity", "-Infinity"])
def test_benchmark_output_refuses_non_standard_json_numbers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid_number: str
) -> None:
    module = _benchmark_module()
    root = tmp_path / "build" / "benchmarks"
    root.mkdir(parents=True)
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "BENCHMARK_ROOT", root)
    existing = root / "weca.json"
    existing.write_text('{"schema_version":' + invalid_number + '}', encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe existing non-benchmark"):
        module.benchmark_output_path(existing)


def test_non_weca_paired_benchmark_does_not_apply_weca_release_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _benchmark_module()
    eager = _worker_result(mode="eager-reference", cpu=5.0, root_pairs=1, candidate_searches=1)
    lazy = _worker_result(mode="lazy", cpu=4.0, root_pairs=1, candidate_searches=1)
    monkeypatch.setattr(module, "_run_worker", lambda _path, mode: eager if mode == "eager" else lazy)
    result = module.paired_benchmark(Path("tests/fixtures/synthetic-area.yaml"))
    assert result["passed"] is True
    assert result["release_budget"]["logical_baseline"] == {
        "applies": False,
        "contract": "not-applicable-non-weca-paired-benchmark",
        "thresholds": {},
        "observed": {
            "eager": {"root_pairs_considered": 1, "root_pair_candidate_searches": 1},
            "lazy": {"root_pairs_considered": 1, "root_pair_candidate_searches": 1},
        },
        "passed": True,
        "reasons": [],
    }
    assert result["release_budget"]["weca_release_input_contract"] == {
        "applies": False,
        "contract": "not-applicable-non-weca-paired-benchmark",
        "expected": {},
        "observed": {
            "eager": {
                **WECA_BASELINE_INPUT_DIGESTS,
                "governed_input_fingerprint": "c" * 64,
                "compiler_sha256": "e" * 64,
            },
            "lazy": {
                **WECA_BASELINE_INPUT_DIGESTS,
                "governed_input_fingerprint": "c" * 64,
                "compiler_sha256": "e" * 64,
            },
        },
        "passed": True,
        "reasons": [],
    }


def test_canonical_weca_fixture_is_byte_identical_to_the_recorded_125_area_definition() -> None:
    module = _benchmark_module()
    fixture = PROJECT / CANONICAL_WECA_BENCHMARK
    assert fixture.is_file()
    assert module.area_definition_sha256(module.AreaDefinition.from_yaml(fixture)) == (
        WECA_BASELINE_INPUT_DIGESTS["area_definition_sha256"]
    )


def test_canonical_weca_pair_fails_closed_before_workers_when_historical_snapshot_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _benchmark_module()
    missing_snapshot = tmp_path / "missing" / "snapshot.json"
    area = SimpleNamespace(
        source=SimpleNamespace(snapshot_dir=missing_snapshot.parent.parent, snapshot_id="missing")
    )
    monkeypatch.setattr(module.AreaDefinition, "from_yaml", lambda _path: area)
    monkeypatch.setattr(
        module,
        "area_definition_sha256",
        lambda _area: WECA_BASELINE_INPUT_DIGESTS["area_definition_sha256"],
    )
    monkeypatch.setattr(
        module,
        "_run_worker",
        lambda *_args: pytest.fail("workers must not run without the historical snapshot"),
    )

    result = module.paired_benchmark(CANONICAL_WECA_BENCHMARK)

    assert result["passed"] is False
    assert result["reasons"] == [f"weca-historical-snapshot-missing:{missing_snapshot}"]


def test_canonical_weca_pair_fails_closed_before_workers_when_historical_snapshot_mismatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _benchmark_module()
    snapshot = tmp_path / "snapshots" / "historical" / "snapshot.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("{}", encoding="utf-8")
    area = SimpleNamespace(
        source=SimpleNamespace(snapshot_dir=snapshot.parent.parent, snapshot_id="historical")
    )
    monkeypatch.setattr(module.AreaDefinition, "from_yaml", lambda _path: area)
    monkeypatch.setattr(
        module,
        "area_definition_sha256",
        lambda _area: WECA_BASELINE_INPUT_DIGESTS["area_definition_sha256"],
    )
    monkeypatch.setattr(module, "snapshot_manifest_sha256", lambda _area: "0" * 64)
    monkeypatch.setattr(
        module,
        "_run_worker",
        lambda *_args: pytest.fail("workers must not run with a mismatched historical snapshot"),
    )

    result = module.paired_benchmark(CANONICAL_WECA_BENCHMARK)

    assert result["passed"] is False
    assert result["reasons"] == ["weca-historical-snapshot-manifest-digest-mismatch"]


@pytest.mark.parametrize(
    ("mode", "mutate", "reason"),
    [
        (
            "eager-reference",
            lambda diagnostics: diagnostics.update({"root_pair_route_searches": 1}),
            "cross-spine-counter-invariant-eager-routes-not-candidates",
        ),
        (
            "lazy",
            lambda diagnostics: diagnostics.update({"root_pair_route_searches_avoided": 1}),
            "cross-spine-counter-invariant-avoided-not-candidates-minus-routes",
        ),
        (
            "lazy",
            lambda diagnostics: diagnostics.update({"root_pair_candidate_bounds_enqueued": 2210}),
            "cross-spine-counter-invariant-lazy-bounds-not-distinct-pairs",
        ),
        (
            "lazy",
            lambda diagnostics: diagnostics.update({"root_pair_candidate_bounds_skipped_as_connected": 2212}),
            "cross-spine-counter-invariant-lazy-skipped-bounds-exceed-enqueued",
        ),
    ],
)
def test_worker_counter_contract_rejects_internally_impossible_schedule_evidence(
    mode: str, mutate: object, reason: str
) -> None:
    module = _benchmark_module()
    result = _worker_result(mode=mode)
    diagnostics = result["cross_spine_diagnostics"]
    assert isinstance(diagnostics, dict)
    assert callable(mutate)
    mutate(diagnostics)

    failures = module._validate_worker_result(result, expected_mode=mode)

    assert reason in failures


def test_governed_signature_encoding_preserves_types_and_mapping_keys() -> None:
    module = _benchmark_module()
    values = [None, True, 1, 1.0, "1"]
    encodings = {
        json.dumps(module._normalise(value), sort_keys=True, separators=(",", ":"))
        for value in values
    }
    assert len(encodings) == len(values)
    numpy_encoding = json.dumps(module._normalise(module.np.int64(1)), sort_keys=True, separators=(",", ":"))
    assert numpy_encoding not in encodings
    mapping = {1: "int", "1": "str"}
    # Python itself aliases True and 1 when constructing one dict, but separate
    # maps must nevertheless encode their key types distinctly.
    entries = module._normalise(mapping)["entries"]
    assert len(entries) == 2
    assert {entry[0]["type"] for entry in entries} == {"int", "str"}
    assert module._normalise({1: "int"}) != module._normalise({"1": "int"})
    assert module._normalise({True: "bool"}) != module._normalise({1: "bool"})


class _StringBackedDecision(StrEnum):
    KEEP = "keep"


class _IntegerBackedDecision(IntEnum):
    KEEP = 1


def test_governed_signature_encoding_keeps_strenum_distinct_from_plain_string() -> None:
    module = _benchmark_module()
    encoded_enum = module._normalise(_StringBackedDecision.KEEP)
    assert encoded_enum["type"] == "enum"
    assert encoded_enum["member"] == "KEEP"
    assert encoded_enum != module._normalise("keep")
    assert module._normalise(_IntegerBackedDecision.KEEP) != module._normalise(1)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (pd.NA, {"type": "pandas-missing"}),
        (pd.NaT, {"type": "pandas-nat"}),
        (float("nan"), {"type": "float-missing"}),
        (np.float64("nan"), {"type": "numpy-missing", "dtype": "<f8"}),
        (np.datetime64("NaT", "ns"), {"type": "numpy-missing", "dtype": "<M8[ns]"}),
    ],
)
def test_governed_signature_encoding_types_ordinary_pandas_and_numpy_missing_values(
    value: object, expected: dict[str, str]
) -> None:
    module = _benchmark_module()
    assert module._normalise(value) == expected


def test_governed_signature_encoding_keeps_pandas_nat_distinct_from_timedelta_and_other_missing_values() -> None:
    module = _benchmark_module()
    pandas_timedelta_nat = pd.Timedelta("NaT")
    assert pandas_timedelta_nat is pd.NaT
    encoded_nat = module._normalise(pandas_timedelta_nat)

    assert encoded_nat == {"type": "pandas-nat"}
    assert encoded_nat != module._normalise(pd.Timedelta(1, unit="ns"))
    assert encoded_nat != module._normalise(pd.NA)
    assert encoded_nat != module._normalise(float("nan"))
    assert encoded_nat != module._normalise(np.float64("nan"))


def test_governed_signature_encoding_preserves_ndarray_dtype_shape_and_element_order() -> None:
    module = _benchmark_module()
    encoded = module._normalise(np.array([[1, 2], [3, 4]], dtype="<i4"))
    assert encoded == {
        "type": "numpy-ndarray",
        "dtype": "<i4",
        "shape": [2, 2],
        "order": "C",
        "items": [
            {"type": "numpy-scalar", "dtype": "<i4", "value": {"type": "int", "value": "1"}},
            {"type": "numpy-scalar", "dtype": "<i4", "value": {"type": "int", "value": "2"}},
            {"type": "numpy-scalar", "dtype": "<i4", "value": {"type": "int", "value": "3"}},
            {"type": "numpy-scalar", "dtype": "<i4", "value": {"type": "int", "value": "4"}},
        ],
    }
    assert encoded != module._normalise(np.array([[1, 2, 3, 4]], dtype="<i4"))
    assert encoded != module._normalise(np.array([[1, 2], [4, 3]], dtype="<i4"))
    assert encoded != module._normalise(np.array([[1, 2], [3, 4]], dtype=">i4"))


def test_governed_signature_encoding_handles_object_datetime_and_timedelta_ndarrays() -> None:
    module = _benchmark_module()
    object_array = np.array([np.int64(1), "one", np.float64("nan")], dtype=object)
    encoded_object = module._normalise(object_array)
    assert encoded_object["dtype"] == "|O"
    assert encoded_object["items"] == [
        {"type": "numpy-scalar", "dtype": "<i8", "value": {"type": "int", "value": "1"}},
        {"type": "str", "value": "one"},
        {"type": "numpy-missing", "dtype": "<f8"},
    ]
    assert module._normalise(np.array([np.datetime64("2026-07-25", "D")]))["dtype"] == "<M8[D]"
    assert module._normalise(np.array([np.timedelta64(2, "D")]))["dtype"] == "<m8[D]"
    with pytest.raises(ValueError, match="non-finite"):
        module._normalise(np.array([1.0, np.inf]))
    with pytest.raises(TypeError, match="unsupported"):
        module._normalise(np.array([object()], dtype=object))


def test_governed_signature_keeps_nanosecond_pandas_timedeltas_distinct_in_object_arrays() -> None:
    module = _benchmark_module()
    values = np.array(
        [
            pd.Timedelta(1, unit="ns"),
            pd.Timedelta(999, unit="ns"),
            pd.Timedelta(1, unit="us"),
            dt.timedelta(0),
        ],
        dtype=object,
    )
    encoded = module._normalise(values)
    pandas_timedelta_class = f"{type(values[0]).__module__}.{type(values[0]).__qualname__}"
    assert encoded["items"] == [
        {
            "type": "pandas-timedelta",
            "class": pandas_timedelta_class,
            "nanoseconds": "1",
        },
        {
            "type": "pandas-timedelta",
            "class": pandas_timedelta_class,
            "nanoseconds": "999",
        },
        {
            "type": "pandas-timedelta",
            "class": pandas_timedelta_class,
            "nanoseconds": "1000",
        },
        {"type": "timedelta", "days": "0", "seconds": 0, "microseconds": 0},
    ]
    assert len({json.dumps(item, sort_keys=True) for item in encoded["items"]}) == len(values)

    compiled = _four_root_compiled()
    places = compiled.places.copy(deep=True)
    durations = pd.Series([None] * len(places), index=places.index, dtype="object")
    durations.at[places.index[0]] = values
    places["signature_duration"] = durations
    governed = replace(compiled, places=places)
    signature = module.governed_output_signature(governed)

    tampered_places = places.copy(deep=True)
    tampered_durations = tampered_places["signature_duration"].copy(deep=True)
    tampered_durations.at[places.index[0]] = np.array(
        [pd.Timedelta(2, unit="ns"), *values[1:]], dtype=object
    )
    tampered_places["signature_duration"] = tampered_durations
    assert module.governed_output_signature(replace(compiled, places=tampered_places)) != signature


@pytest.mark.parametrize("value", [float("inf"), -float("inf"), object()])
def test_governed_signature_encoding_rejects_nonfinite_and_unknown_values(value: object) -> None:
    module = _benchmark_module()
    with pytest.raises((TypeError, ValueError)):
        module._normalise(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_worker_metric_validation_rejects_nonfinite_values(value: float) -> None:
    module = _benchmark_module()
    assert module._is_nonnegative_metric(value) is False


def _four_root_compiled() -> object:
    specification = importlib.util.spec_from_file_location(
        "backbone_benchmark_fixture", PROJECT / "tests" / "test_backbone_assembly.py"
    )
    assert specification is not None and specification.loader is not None
    fixture = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(fixture)
    return fixture.compile_network(fixture.config(), fixture.four_spine_source(), fixture.FakeAgentRuntime())


def test_four_root_compiled_signature_handles_missing_values_and_detects_tampering() -> None:
    module = _benchmark_module()
    compiled = _four_root_compiled()
    signature = module.governed_output_signature(compiled)
    assert signature == module.governed_output_signature(compiled)

    tampered_places = compiled.places.copy(deep=True)
    tampered_places.loc[tampered_places.index[0], "name"] = "Tampered"
    tampered = replace(compiled, places=tampered_places)
    assert module.governed_output_signature(tampered) != signature

    # These are genuine compiler GeoDataFrame values rather than artificial
    # worker evidence.  Typed governed encoding must preserve all missing
    # forms while the separate metric/counter validator rejects them.
    missing_places = compiled.places.copy(deep=True)
    missing_places["signature_missing"] = pd.Series(
        [pd.NA, pd.NaT, np.float64("nan"), None, pd.NA],
        index=missing_places.index,
        dtype="object",
    )
    with_missing = replace(compiled, places=missing_places)
    missing_contract = module.governed_output_contract(with_missing)
    encoded_missing = [row["signature_missing"] for row in missing_contract["places"]["rows"]]
    assert encoded_missing == [
        {"type": "pandas-missing"},
        {"type": "pandas-nat"},
        {"type": "numpy-missing", "dtype": "<f8"},
        {"type": "none"},
        {"type": "pandas-missing"},
    ]
    assert module.governed_output_signature(with_missing) == module.governed_output_signature(with_missing)


@pytest.mark.parametrize(
    "counter",
    [
        "root_pair_route_searches",
        "root_pair_route_searches_avoided",
        "root_pair_candidate_bounds_enqueued",
        "root_pair_candidate_bounds_skipped_as_connected",
        "root_group_distance_planning_nodes_settled",
        "root_group_distance_planning_searches",
        "root_pair_candidate_bounds_skipped_as_unroutable",
        "root_pair_exact_distance_bounds",
    ],
)
def test_governed_signature_excludes_only_new_cross_spine_schedule_work_counters(
    counter: str,
) -> None:
    """Schedule work may vary; every other Cross-Spine diagnostic remains governed."""
    module = _benchmark_module()
    expected_schedule_work_counters = frozenset(
        {
            "root_pair_route_searches",
            "root_pair_route_searches_avoided",
            "root_pair_candidate_bounds_enqueued",
            "root_pair_candidate_bounds_skipped_as_connected",
            "root_pair_candidate_bounds_skipped_as_unroutable",
            "root_group_distance_planning_searches",
            "root_group_distance_planning_nodes_settled",
            "root_pair_exact_distance_bounds",
        }
    )
    assert expected_schedule_work_counters == module.PERFORMANCE_DIAGNOSTIC_KEYS
    compiled = _four_root_compiled()
    signature = module.governed_output_signature(compiled)
    diagnostics = dict(compiled.compilation_diagnostics)
    cross_spine = dict(diagnostics["cross_spine"])
    cross_spine[counter] = int(cross_spine[counter]) + 1
    diagnostics["cross_spine"] = cross_spine

    assert module.governed_output_signature(
        replace(compiled, compilation_diagnostics=diagnostics)
    ) == signature


@pytest.mark.parametrize(
    "diagnostic",
    [
        "peak_noded_graph_nodes",
        "peak_noded_graph_edges",
        "peak_shortest_path_frontier",
        "shortest_path_nodes_settled",
        "shortest_path_relaxations",
    ],
)
def test_governed_signature_keeps_legacy_graph_and_search_diagnostics(
    diagnostic: str,
) -> None:
    """Only the exact scheduling-work allowlist may differ between schedules."""
    module = _benchmark_module()
    compiled = _four_root_compiled()
    signature = module.governed_output_signature(compiled)

    diagnostics = dict(compiled.compilation_diagnostics)
    cross_spine = dict(diagnostics["cross_spine"])
    cross_spine[diagnostic] = int(cross_spine.get(diagnostic, 0)) + 1
    diagnostics["cross_spine"] = cross_spine

    assert module.governed_output_signature(
        replace(compiled, compilation_diagnostics=diagnostics)
    ) != signature


def test_governed_signature_keeps_unmaterializable_attachment_paths() -> None:
    """A meaningful source-linework failure remains signature-sensitive.

    Cross-Spine scheduling may suppress a redundant candidate's operational
    failure before publication, but a failure which remains in the final
    legacy graph diagnostic is semantic evidence and must not join the eight
    schedule-work exclusions.
    """
    module = _benchmark_module()
    compiled = _four_root_compiled()
    signature = module.governed_output_signature(compiled)

    diagnostics = dict(compiled.compilation_diagnostics)
    diagnostics["unmaterializable_attachment_paths"] = (
        int(diagnostics["unmaterializable_attachment_paths"]) + 1
    )
    assert module.governed_output_signature(
        replace(compiled, compilation_diagnostics=diagnostics)
    ) != signature


def test_governed_signature_keeps_semantic_and_unknown_cross_spine_diagnostics_fail_closed() -> None:
    module = _benchmark_module()
    compiled = _four_root_compiled()
    signature = module.governed_output_signature(compiled)

    semantic_diagnostics = dict(compiled.compilation_diagnostics)
    semantic_cross_spine = dict(semantic_diagnostics["cross_spine"])
    semantic_cross_spine["authoritative_connectors"] = (
        int(semantic_cross_spine["authoritative_connectors"]) + 1
    )
    semantic_diagnostics["cross_spine"] = semantic_cross_spine
    assert module.governed_output_signature(
        replace(compiled, compilation_diagnostics=semantic_diagnostics)
    ) != signature

    unknown_diagnostics = dict(compiled.compilation_diagnostics)
    unknown_cross_spine = dict(unknown_diagnostics["cross_spine"])
    unknown_cross_spine["unrecognised_cross_spine_diagnostic"] = 1
    unknown_diagnostics["cross_spine"] = unknown_cross_spine
    assert module.governed_output_signature(
        replace(compiled, compilation_diagnostics=unknown_diagnostics)
    ) != signature


def test_governed_signature_covers_geodataframe_index_metadata_values_and_column_dtypes() -> None:
    module = _benchmark_module()
    compiled = _four_root_compiled()
    signature = module.governed_output_signature(compiled)

    reindexed_places = compiled.places.copy(deep=True)
    reindexed_places.index = pd.Index(
        range(100, 100 + len(reindexed_places)), dtype="int64", name="governed-place-index"
    )
    reindexed = replace(compiled, places=reindexed_places)
    reindexed_contract = module.governed_output_contract(reindexed)
    assert reindexed_contract["places"]["index"]["names"] == [
        {"type": "str", "value": "governed-place-index"}
    ]
    assert module.governed_output_signature(reindexed) != signature

    dtype_places = compiled.places.copy(deep=True)
    dtype_places["name"] = dtype_places["name"].astype("string")
    dtyped = replace(compiled, places=dtype_places)
    dtyped_contract = module.governed_output_contract(dtyped)
    name_dtype = next(
        item["dtype"]
        for item in dtyped_contract["places"]["column_dtypes"]
        if item["column"] == {"type": "str", "value": "name"}
    )
    assert name_dtype["value"] == "string"
    assert module.governed_output_signature(dtyped) != signature


def test_governed_signature_handles_ndarray_in_compiled_geodataframe_cell() -> None:
    module = _benchmark_module()
    compiled = _four_root_compiled()
    places = compiled.places.copy(deep=True)
    arrays = pd.Series([None] * len(places), index=places.index, dtype="object")
    arrays.at[places.index[0]] = np.array(["A4", np.int64(46)], dtype=object)
    places["signature_array"] = arrays
    with_array = replace(compiled, places=places)

    contract = module.governed_output_contract(with_array)
    encoded = contract["places"]["rows"][0]["signature_array"]
    assert encoded == {
        "type": "numpy-ndarray",
        "dtype": "|O",
        "shape": [2],
        "order": "C",
        "items": [
            {"type": "str", "value": "A4"},
            {"type": "numpy-scalar", "dtype": "<i8", "value": {"type": "int", "value": "46"}},
        ],
    }
    signature = module.governed_output_signature(with_array)
    assert signature == module.governed_output_signature(with_array)

    tampered_places = places.copy(deep=True)
    tampered_arrays = tampered_places["signature_array"].copy(deep=True)
    tampered_arrays.at[places.index[0]] = np.array(["A4", np.int64(47)], dtype=object)
    tampered_places["signature_array"] = tampered_arrays
    assert module.governed_output_signature(replace(compiled, places=tampered_places)) != signature


def test_benchmark_cli_requires_an_explicit_area_definition(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _benchmark_module()
    with pytest.raises(SystemExit) as raised:
        module.main([])
    assert raised.value.code == 2
    assert "the following arguments are required: config" in capsys.readouterr().err


@pytest.mark.parametrize("output", ["README.md", "deployments/weca/benchmark.json", "../outside.json"])
def test_benchmark_cli_rejects_tracked_source_and_arbitrary_output_paths(
    output: str, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _benchmark_module()
    with pytest.raises(SystemExit) as raised:
        module.main(["area.yaml", "--output", output])
    assert raised.value.code == 2
    assert "must be below ignored" in capsys.readouterr().err


def test_benchmark_output_refuses_non_benchmark_and_allows_its_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _benchmark_module()
    root = tmp_path / "build" / "benchmarks"
    root.mkdir(parents=True)
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "BENCHMARK_ROOT", root)
    unsafe = root / "important.json"
    unsafe.write_text('{"schema_version":"unrelated/v1"}', encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe existing non-benchmark"):
        module.benchmark_output_path(unsafe)
    allowed = root / "weca.json"
    allowed.write_text('{"schema_version":"cross-spine-benchmark/v4"}', encoding="utf-8")
    assert module.benchmark_output_path(allowed) == allowed
