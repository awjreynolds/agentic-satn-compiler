# ruff: noqa: E501 -- benchmark evidence labels are deliberately explicit.
"""Fail-closed paired operational benchmark for Cross-Spine assembly.

The command runs the retained eager reference and the lazy implementation in
separate worker processes.  Separate workers are essential: ``ru_maxrss`` is a
process-lifetime high-water mark, so two schedules in one interpreter cannot
produce honest per-mode RSS evidence.  The orchestrator accepts the pair only
when both workers report the same pinned governed inputs and governed output.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import enum
import hashlib
import json
import math
import os
import platform
import resource
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

import satn.backbone as backbone_module
from satn.agents import runtime_for
from satn.compiler import CompiledNetwork, compile_network
from satn.models import AreaDefinition
from satn.pipeline import (
    _compiler_digest,
    area_definition_sha256,
    compilation_governed_input_fingerprint,
    snapshot_manifest_sha256,
)
from satn.sources import load_snapshot

BENCHMARK_SCHEMA_VERSION = "cross-spine-benchmark/v4"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = (PROJECT_ROOT / "build" / "benchmarks").resolve()
# This file is intentionally byte-for-byte the #125 Area Definition.  The
# deployable WECA definition can evolve (for example to add elevation), while
# the operational optimisation gate must retain the historical input it claims
# to measure.
DEFAULT_WECA_AREA = Path("deployments/weca/area-125-benchmark.yaml")
DEFAULT_OUTPUT = Path("build/benchmarks/weca-cross-spine-paired.json")
WECA_LOGICAL_BASELINE_COUNTERS = {
    "root_pairs_considered": 2211,
    "root_pair_candidate_searches": 2211,
}
WECA_BASELINE_INPUT_DIGESTS = {
    "area_definition_sha256": "24a03e50ccfe541ff637b9c75f15caa41ac452cc20667f31df5ad274ffbeae6a",
    "snapshot_manifest_sha256": "d4d8cbe37c13a6b9ae5d027693d64e89eab2edccf7b69afcdbec519883b1a988",
}
WECA_BASELINE_GOVERNED_INPUT_FINGERPRINT = (
    "90264ba7be42de07eae4dc441a9aba89c23f5447b6dc96b23ded946069de3d37"
)
PERFORMANCE_DIAGNOSTIC_KEYS = frozenset(
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
INPUT_BINDING_KEYS = (
    "area_definition_sha256",
    "snapshot_manifest_sha256",
    "governed_input_fingerprint",
    "compiler_sha256",
)
WORKER_MODES = frozenset({"eager-reference", "lazy"})
GOVERNED_OUTPUT_CONTRACT_FIELDS = tuple(
    field.name for field in dataclasses.fields(CompiledNetwork)
)
HEX_SHA256 = frozenset("0123456789abcdef")
REQUIRED_DIAGNOSTIC_COUNTERS = frozenset(
    {
        "root_pairs_considered",
        "root_pair_candidate_searches",
        "root_pair_route_searches",
        "root_pair_route_searches_avoided",
        "root_pair_candidate_bounds_enqueued",
        "root_pair_candidate_bounds_skipped_as_connected",
        "root_pair_candidate_bounds_skipped_as_unroutable",
        "root_group_distance_planning_searches",
        "root_group_distance_planning_nodes_settled",
        "root_pair_exact_distance_bounds",
        "meeting_agent_evaluations",
        "candidate_connectors",
        "authoritative_connectors",
        "route_refinement_findings",
        "noded_graphs_built",
        "noded_graph_nodes_total",
        "noded_graph_edges_total",
        "peak_noded_graph_nodes",
        "peak_noded_graph_edges",
        "root_candidate_nodes_examined",
        "eligible_root_endpoint_candidates",
        "endpoint_pairs_considered",
        "weighted_shortest_path_searches",
        "weighted_shortest_path_nodes_settled",
        "weighted_shortest_path_edge_relaxations",
        "peak_shortest_path_frontier",
        "deterministic_path_nodes_selected",
        "connector_traversal_attempts",
    }
)


def _normalise(value: Any) -> dict[str, object]:
    """Encode supported governed values without collapsing their Python types.

    This is deliberately a tagged *value* encoding rather than a JSON-friendly
    normalisation.  In particular, ``True``, ``1``, ``np.int64(1)`` and
    ``"1"`` are distinct, mapping keys retain their types, and no unknown
    object is stringified into a potentially colliding representation.
    """
    if value is None:
        return {"type": "none"}
    # ``StrEnum`` is also a ``str`` (and ``IntEnum`` an ``int``), so enum
    # identity must win before scalar dispatch.  Otherwise a governed enum
    # could silently collide with its underlying scalar value.
    if isinstance(value, enum.Enum):
        return {
            "type": "enum",
            "class": f"{type(value).__module__}.{type(value).__qualname__}",
            "member": value.name,
            "value": _normalise(value.value),
        }
    # Missing values are ordinary in pandas/NumPy-backed compiler frames.  They
    # are semantic values, not invalid measurements: retain their origin and
    # dtype so they cannot collide with ``None`` or with one another.  The
    # separate worker-evidence validator remains deliberately stricter and
    # rejects non-finite timing/counter values.
    if value is pd.NA:
        return {"type": "pandas-missing"}
    if value is pd.NaT:
        return {"type": "pandas-nat"}
    # GeoPandas frames can contain NumPy arrays in object columns (for example
    # an evidence-derived list of identifiers).  They are not generic Python
    # sequences: their dtype, dimensionality and C-order element sequence are
    # governed values.  Encode those facts explicitly before scalar/sequence
    # dispatch so an ndarray never silently collides with a list or tuple.
    #
    # ``ravel(order="C")`` intentionally records semantic index order rather
    # than incidental C/F memory contiguity.  The explicit shape makes arrays
    # with the same flattened values but different dimensions distinct.  Each
    # element is sent back through this strict encoder, so object arrays retain
    # their typed contents and infinities/unknown values still fail closed.
    if isinstance(value, np.ndarray):
        return {
            "type": "numpy-ndarray",
            "dtype": value.dtype.str,
            "shape": list(value.shape),
            "order": "C",
            "items": [_normalise(item) for item in value.ravel(order="C")],
        }
    if isinstance(value, np.generic):
        dtype = value.dtype.str
        if isinstance(value, np.floating) and math.isnan(float(value)):
            return {"type": "numpy-missing", "dtype": dtype}
        if isinstance(value, (np.datetime64, np.timedelta64)) and np.isnat(value):
            return {"type": "numpy-missing", "dtype": dtype}
        # ``item`` retains the scalar's semantic value while dtype records the
        # NumPy representation (including signedness and precision).
        return {"type": "numpy-scalar", "dtype": dtype, "value": _normalise(value.item())}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int):
        return {"type": "int", "value": str(value)}
    if isinstance(value, float):
        if math.isnan(value):
            return {"type": "float-missing"}
        if not math.isfinite(value):
            raise ValueError("governed values must not contain non-finite floats")
        # hex is exact, preserves negative zero, and avoids JSON parser quirks.
        return {"type": "float", "value": value.hex()}
    if isinstance(value, str):
        return {"type": "str", "value": value}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            raise ValueError("governed values must not contain NaT")
        return {"type": "pandas-timestamp", "value": value.isoformat()}
    if isinstance(value, pd.Timedelta):
        # ``Timedelta`` subclasses ``datetime.timedelta``, but its exact
        # nanosecond value cannot be represented by the latter's microsecond
        # fields.  Keep its concrete pandas identity and integer nanoseconds
        # before the Python timedelta branch, otherwise 1ns and 999ns both
        # silently collapse to zero-duration Python timedeltas.  Constructing
        # ``pd.Timedelta("NaT")`` materialises as ``pd.NaT``, so it reaches
        # the explicit pandas-missing branch above rather than this one.
        return {
            "type": "pandas-timedelta",
            "class": f"{type(value).__module__}.{type(value).__qualname__}",
            "nanoseconds": str(value.value),
        }
    if isinstance(value, dt.timedelta):
        # Avoid a lossy float total-seconds representation.  This also covers
        # NumPy timedelta scalars whose ``item()`` materialises as Python's
        # ``datetime.timedelta`` for coarse units.
        return {
            "type": "timedelta",
            "days": str(value.days),
            "seconds": value.seconds,
            "microseconds": value.microseconds,
        }
    if isinstance(value, dt.datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, dt.date):
        return {"type": "date", "value": value.isoformat()}
    if hasattr(value, "model_dump"):
        return {
            "type": "model",
            "class": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": _normalise(value.model_dump(mode="json", exclude={"created_at"})),
        }
    if dataclasses.is_dataclass(value):
        return {
            "type": "dataclass",
            "class": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": _normalise({field.name: getattr(value, field.name) for field in dataclasses.fields(value)}),
        }
    if isinstance(value, Mapping):
        entries = [[_normalise(key), _normalise(item)] for key, item in value.items()]
        entries.sort(key=lambda item: json.dumps(item[0], sort_keys=True, separators=(",", ":"), allow_nan=False))
        return {"type": "mapping", "entries": entries}
    if isinstance(value, list):
        return {"type": "list", "items": [_normalise(item) for item in value]}
    if isinstance(value, tuple):
        return {"type": "tuple", "items": [_normalise(item) for item in value]}
    if isinstance(value, (set, frozenset)):
        items = [_normalise(item) for item in value]
        items.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return {"type": "frozenset" if isinstance(value, frozenset) else "set", "items": items}
    raise TypeError(f"unsupported governed value type: {type(value).__module__}.{type(value).__qualname__}")


def _governed_frame(frame: gpd.GeoDataFrame) -> dict[str, object]:
    """Capture all values *and* pandas schema that can govern publication."""
    return {
        "columns": list(frame.columns),
        "column_dtypes": [
            {
                "column": _normalise(column),
                "dtype": _pandas_dtype(frame.dtypes.iloc[position]),
            }
            for position, column in enumerate(frame.columns)
        ],
        "crs": frame.crs.to_string() if frame.crs is not None else None,
        "index": _governed_index(frame.index),
        "rows": [
            {
                column: (
                    {"type": "geometry-wkb-hex", "value": row[column].wkb_hex}
                    if column == frame.geometry.name and row[column] is not None
                    else _normalise(row[column])
                )
                for column in frame.columns
            }
            for _, row in frame.iterrows()
        ],
    }


def _pandas_dtype(dtype: object) -> dict[str, str]:
    """Record both pandas' textual and concrete dtype identity."""
    return {
        "class": f"{type(dtype).__module__}.{type(dtype).__qualname__}",
        "value": str(dtype),
    }


def _governed_index(index: pd.Index) -> dict[str, object]:
    """Encode ordered labels plus index class, names and level dtypes."""
    return {
        "class": f"{type(index).__module__}.{type(index).__qualname__}",
        "names": [_normalise(name) for name in index.names],
        "level_dtypes": [_pandas_dtype(level.dtype) for level in index.levels]
        if isinstance(index, pd.MultiIndex)
        else [_pandas_dtype(index.dtype)],
        "values": [_normalise(value) for value in index.tolist()],
    }


def governed_output_contract(compiled: CompiledNetwork) -> dict[str, object]:
    """Return the complete governed compiler contract, excluding only transient data.

    ``created_at`` on direct-runtime records is an operational timestamp, and
    the explicit Cross-Spine counters below are the optimisation under test.
    Everything else from ``CompiledNetwork`` is included, including records,
    criteria, findings, provenance and every GeoDataFrame.
    """
    contract: dict[str, object] = {}
    for field in dataclasses.fields(compiled):
        value = getattr(compiled, field.name)
        if isinstance(value, gpd.GeoDataFrame):
            contract[field.name] = _governed_frame(value)
        elif field.name == "compilation_diagnostics":
            diagnostics = dict(value)
            cross_spine = diagnostics.get("cross_spine")
            if isinstance(cross_spine, Mapping):
                diagnostics["cross_spine"] = {
                    key: item for key, item in cross_spine.items() if key not in PERFORMANCE_DIAGNOSTIC_KEYS
                }
            contract[field.name] = _normalise(diagnostics)
        else:
            contract[field.name] = _normalise(value)
    return contract


def governed_output_signature(compiled: CompiledNetwork) -> str:
    return _governed_contract_digest(governed_output_contract(compiled))


def _governed_contract_digest(contract: Mapping[str, object]) -> str:
    payload = json.dumps(contract, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _governed_contract_field_digests(contract: Mapping[str, object]) -> dict[str, str]:
    """Hash each governed field so paired evidence can locate a mismatch safely."""
    return {
        field_name: _governed_contract_digest({field_name: contract[field_name]})
        for field_name in GOVERNED_OUTPUT_CONTRACT_FIELDS
    }


def compare_governed_outputs(left: CompiledNetwork, right: CompiledNetwork) -> list[str]:
    """Return explicit parity failures; an empty list proves governed equality."""
    return [] if governed_output_contract(left) == governed_output_contract(right) else ["governed-output-signature-mismatch"]


def benchmark(config_path: Path, *, eager_reference: bool = False) -> dict[str, object]:
    """Run one isolated benchmark worker.  The paired orchestrator gates release."""
    area = AreaDefinition.from_yaml(config_path)
    if area.compilation.agent.provider != "fake":
        raise ValueError("Cross-Spine benchmark requires the deterministic fake agent provider")
    phase_durations: dict[str, float] = {}
    phase_cpu_times: dict[str, float] = {}
    cross_spine_started: float | None = None
    cross_spine_finished: float | None = None

    def observe_cross_spine(assessed: int, total: int, _diagnostics: Mapping[str, object]) -> None:
        nonlocal cross_spine_finished, cross_spine_started
        now = time.perf_counter()
        cross_spine_started = cross_spine_started or now
        if assessed == total:
            cross_spine_finished = now

    snapshot_started, snapshot_cpu_started = time.perf_counter(), time.process_time()
    source = load_snapshot(area)
    phase_durations["snapshot_load"] = round(time.perf_counter() - snapshot_started, 6)
    phase_cpu_times["snapshot_load"] = round(time.process_time() - snapshot_cpu_started, 6)
    compile_started, compile_cpu_started = time.perf_counter(), time.process_time()
    runtime = (
        runtime_for(area.compilation.agent)
        if area.compilation.agent.response_mode == "direct-runtime" and area.compilation.agent.review_statuses
        else None
    )
    original_meetings = backbone_module._cross_spine_meetings

    def eager_meetings(*args: object, **kwargs: object) -> object:
        return original_meetings(*args, lazy_bounds=False, **kwargs)

    override = _temporary_attribute(backbone_module, "_cross_spine_meetings", eager_meetings) if eager_reference else contextlib.nullcontext()
    with override:
        compiled = compile_network(area, source, runtime, cross_spine_progress=observe_cross_spine)
    phase_durations["network_compile"] = round(time.perf_counter() - compile_started, 6)
    phase_cpu_times["network_compile"] = round(time.process_time() - compile_cpu_started, 6)
    phase_durations["cross_spine_assembly"] = round((cross_spine_finished or time.perf_counter()) - (cross_spine_started or compile_started), 6)
    diagnostics = compiled.compilation_diagnostics.get("cross_spine")
    if not isinstance(diagnostics, dict):
        raise ValueError("compiler did not emit Cross-Spine diagnostics")
    worker_diagnostics = _plain_json_mapping(diagnostics, label="Cross-Spine diagnostics")
    rss = _peak_rss_measurement()
    governed_contract = governed_output_contract(compiled)
    result = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "area_id": area.area_id,
        "snapshot_id": area.source.snapshot_id,
        "area_definition_sha256": area_definition_sha256(area),
        "snapshot_manifest_sha256": snapshot_manifest_sha256(area),
        "governed_input_fingerprint": compilation_governed_input_fingerprint(area),
        # This is intentionally recorded, not pinned to #125.  The current
        # compiler digest is source-inclusive, so requiring the historical
        # value after changing the Cross-Spine implementation would forge
        # provenance.  #123 will replace this broad identity with its explicit
        # dependency manifest; until then the paired workers must agree on it.
        "compiler_sha256": _compiler_digest(),
        "baseline": {
            "contract": "#125-weca-source-inclusive-baseline/v1",
            "governed_input_fingerprint": WECA_BASELINE_GOVERNED_INPUT_FINGERPRINT,
        },
        "execution": "isolated-local-fake-runtime-no-publication",
        "benchmark_mode": "eager-reference" if eager_reference else "lazy",
        "phase_durations_seconds": phase_durations,
        "phase_process_cpu_seconds": phase_cpu_times,
        "peak_rss": rss,
        "host": _host_metadata(),
        "protocol": {
            "input_binding": "area-definition-snapshot-governed-fingerprint/v1",
            "workers": "separate subprocesses; each independently loads the same pinned input identity",
            "worker_pythonhashseed": "0",
            "runtime": "deterministic-fake",
            "publication": "disabled",
            "candidate_schedule": "eager-reference" if eager_reference else "lazy-bounds",
            "wall_clock": "time.perf_counter",
            "process_cpu": "time.process_time",
            "peak_rss": rss["scope"],
        },
        # Worker evidence is deliberately an ordinary JSON object: the paired
        # validator consumes the diagnostics schema and integer counters
        # directly.  Tagged governed-output encoding belongs only in the
        # signature contract above.
        "cross_spine_diagnostics": worker_diagnostics,
        "peak_work_counts": {
            key: value
            for key, value in worker_diagnostics.items()
            if key.startswith("peak_") and isinstance(value, int) and not isinstance(value, bool)
        },
        "governed_output_signature": _governed_contract_digest(governed_contract),
        "governed_output_field_digests": _governed_contract_field_digests(governed_contract),
    }
    expected_mode = "eager-reference" if eager_reference else "lazy"
    failures = _validate_worker_result(result, expected_mode=expected_mode)
    if failures:
        raise ValueError(f"benchmark worker emitted invalid evidence: {', '.join(failures)}")
    return result


@contextlib.contextmanager
def _temporary_attribute(target: object, name: str, value: object) -> object:
    original = getattr(target, name)
    setattr(target, name, value)
    try:
        yield
    finally:
        setattr(target, name, original)


def _peak_rss_measurement() -> dict[str, object]:
    """Normalise platform-supported ``ru_maxrss`` or explicitly mark unsupported."""
    system = platform.system()
    if system == "Darwin":
        return {"bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss), "supported": True, "unit": "bytes", "scope": "process-lifetime-high-water"}
    if system == "Linux":
        return {"bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024, "supported": True, "unit": "KiB-normalised-to-bytes", "scope": "process-lifetime-high-water"}
    return {"bytes": None, "supported": False, "unit": "unsupported", "scope": "unsupported-platform"}


def _host_metadata() -> dict[str, object]:
    return {"platform": platform.platform(), "python": platform.python_version(), "cpu_count": os.cpu_count(), "machine": platform.machine()}


def _run_worker(config_path: Path, mode: str) -> dict[str, object]:
    command = [sys.executable, str(Path(__file__).resolve()), str(config_path), "--worker-mode", mode]
    # Several legacy generated identifiers include Python hash-derived tokens.
    # The compiler's public cross-platform-ID limitation remains visible in
    # #139, but paired same-runtime evidence needs one deterministic interpreter
    # seed or it would compare two unrelated runtimes rather than eager/lazy
    # schedules.  Do not omit governed fields from the signature to hide this.
    worker_environment = {**os.environ, "PYTHONHASHSEED": "0"}
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=worker_environment,
    )
    if completed.returncode:
        raise RuntimeError(f"{mode} worker failed ({completed.returncode}): {completed.stderr.strip()}")
    try:
        result = _strict_json_loads(completed.stdout)
    except (json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"{mode} worker emitted invalid JSON") from error
    if not isinstance(result, dict):
        raise RuntimeError(f"{mode} worker emitted a non-object result")
    return result


def _reject_non_json_number(value: str) -> object:
    """Reject JSON's non-standard NaN/Infinity extensions in worker evidence."""
    raise ValueError(f"non-finite JSON number: {value}")


def _strict_json_loads(value: str) -> object:
    """Decode JSON evidence while rejecting non-standard NaN/Infinity tokens."""
    return json.loads(value, parse_constant=_reject_non_json_number)


def _plain_json_mapping(value: Mapping[str, object], *, label: str) -> dict[str, object]:
    """Return a strict, detached JSON object for worker evidence.

    Compiler data can contain NumPy/Pandas values, but worker evidence is a
    public machine-to-machine contract.  A JSON round trip keeps that contract
    deliberately boring: string keys, JSON scalar values and no NaN/Infinity.
    The governed signature has a separate typed encoding precisely so it can
    represent compiler-domain missing values without weakening this boundary.
    """
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        decoded = _strict_json_loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} must be a strict plain JSON mapping") from error
    if not isinstance(decoded, dict):
        raise ValueError(f"{label} must be a strict plain JSON mapping")
    return decoded


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX_SHA256


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_nonnegative_metric(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value >= 0
    )


def _validate_worker_result(result: Mapping[str, object], *, expected_mode: str) -> list[str]:
    """Validate one worker's evidence before any eager/lazy comparison.

    A malformed value is not treated as absent-but-equal: this validator is a
    release gate and therefore records every missing or untrusted field as an
    independently failing reason.
    """
    failures: list[str] = []
    if result.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        failures.append("schema-version-invalid")
    if result.get("benchmark_mode") != expected_mode:
        failures.append("benchmark-mode-invalid")
    for key in (*INPUT_BINDING_KEYS, "governed_output_signature"):
        if not _is_sha256(result.get(key)):
            failures.append(f"{key}-invalid-or-missing")
    field_digests = result.get("governed_output_field_digests")
    if (
        not isinstance(field_digests, Mapping)
        or set(field_digests) != set(GOVERNED_OUTPUT_CONTRACT_FIELDS)
        or any(
            not isinstance(field_name, str) or not _is_sha256(digest)
            for field_name, digest in field_digests.items()
        )
    ):
        failures.append("governed-output-field-digests-invalid-or-missing")
    baseline = result.get("baseline")
    if not isinstance(baseline, Mapping) or (
        baseline.get("contract") != "#125-weca-source-inclusive-baseline/v1"
        or not _is_sha256(baseline.get("governed_input_fingerprint"))
    ):
        failures.append("baseline-missing-or-invalid")
    for group in ("phase_durations_seconds", "phase_process_cpu_seconds"):
        metrics = result.get(group)
        if not isinstance(metrics, Mapping) or "network_compile" not in metrics:
            failures.append(f"{group}-network-compile-missing")
            continue
        for key, value in metrics.items():
            if not isinstance(key, str) or not _is_nonnegative_metric(value):
                failures.append(f"{group}-metric-invalid:{key!r}")
    diagnostics = result.get("cross_spine_diagnostics")
    if not isinstance(diagnostics, Mapping):
        failures.append("cross-spine-diagnostics-missing")
    else:
        if diagnostics.get("schema_version") != "cross-spine-diagnostics/v2":
            failures.append("cross-spine-diagnostics-schema-invalid")
        for key in REQUIRED_DIAGNOSTIC_COUNTERS:
            if not _is_nonnegative_int(diagnostics.get(key)):
                failures.append(f"cross-spine-counter-invalid-or-missing:{key}")
        failures.extend(_cross_spine_counter_invariant_failures(diagnostics, expected_mode))
    peak_rss = result.get("peak_rss")
    if not isinstance(peak_rss, Mapping) or peak_rss.get("supported") is not True:
        failures.append("peak-rss-unsupported-or-invalid")
    elif not _is_nonnegative_int(peak_rss.get("bytes")):
        failures.append("peak-rss-bytes-invalid-or-missing")
    return failures


def _cross_spine_counter_invariant_failures(
    diagnostics: Mapping[str, object], expected_mode: str
) -> list[str]:
    """Reject evidence whose Cross-Spine counters cannot describe one schedule.

    Candidate searches include both first-pass root pairs and retried exact
    candidates after a governed rejection.  In lazy mode, every initial unique
    pair is a bound: a bound is either expanded to one exact route, skipped
    because its roots are connected, or skipped after the same reciprocal
    strong-component eligibility proof that makes ``best_attachment`` return
    no route.  Thus skipped bounds are exactly the avoided route searches.
    Eager mode has no bounds and materialises every candidate search as a
    route.
    """
    keys = {
        "distinct": "root_pairs_considered",
        "candidates": "root_pair_candidate_searches",
        "routes": "root_pair_route_searches",
        "avoided": "root_pair_route_searches_avoided",
        "bounds": "root_pair_candidate_bounds_enqueued",
        "skipped": "root_pair_candidate_bounds_skipped_as_connected",
        "unroutable": "root_pair_candidate_bounds_skipped_as_unroutable",
        "distance_plans": "root_group_distance_planning_searches",
        "distance_plan_nodes": "root_group_distance_planning_nodes_settled",
        "exact_distance_bounds": "root_pair_exact_distance_bounds",
    }
    values = {name: diagnostics.get(key) for name, key in keys.items()}
    if not all(_is_nonnegative_int(value) for value in values.values()):
        return []
    distinct = int(values["distinct"])
    candidates = int(values["candidates"])
    routes = int(values["routes"])
    avoided = int(values["avoided"])
    bounds = int(values["bounds"])
    skipped = int(values["skipped"])
    unroutable = int(values["unroutable"])
    distance_plans = int(values["distance_plans"])
    distance_plan_nodes = int(values["distance_plan_nodes"])
    exact_distance_bounds = int(values["exact_distance_bounds"])
    failures: list[str] = []
    if distinct > candidates:
        failures.append("cross-spine-counter-invariant-distinct-pairs-exceed-candidates")
    if routes > candidates:
        failures.append("cross-spine-counter-invariant-routes-exceed-candidates")
    if avoided != candidates - routes:
        failures.append("cross-spine-counter-invariant-avoided-not-candidates-minus-routes")
    if expected_mode == "eager-reference":
        if bounds != 0 or skipped != 0 or unroutable != 0:
            failures.append("cross-spine-counter-invariant-eager-has-bounds")
        if distance_plans != 0 or distance_plan_nodes != 0 or exact_distance_bounds != 0:
            failures.append("cross-spine-counter-invariant-eager-has-distance-planning")
        if routes != candidates:
            failures.append("cross-spine-counter-invariant-eager-routes-not-candidates")
    elif expected_mode == "lazy":
        if bounds != distinct:
            failures.append("cross-spine-counter-invariant-lazy-bounds-not-distinct-pairs")
        if skipped + unroutable > bounds:
            failures.append("cross-spine-counter-invariant-lazy-skipped-bounds-exceed-enqueued")
        if avoided != skipped + unroutable:
            failures.append("cross-spine-counter-invariant-lazy-avoided-not-skipped-bounds")
        if distance_plans > distinct:
            failures.append("cross-spine-counter-invariant-lazy-distance-plans-exceed-pairs")
        if distance_plans and not distance_plan_nodes:
            failures.append("cross-spine-counter-invariant-lazy-distance-plans-without-nodes")
        if exact_distance_bounds > distinct:
            failures.append("cross-spine-counter-invariant-lazy-exact-distance-bounds-exceed-pairs")
    return failures


def _weca_release_baseline_applies(config_path: Path) -> bool:
    """Return whether this is the governed WECA release command, not a generic worker run.

    Single-worker evidence and synthetic benchmark tests must only prove their
    own integer contract.  The fixed #125 logical baseline is a release gate
    for the canonical WECA paired command, whose input is held at this exact
    repository path.
    """
    candidate = config_path if config_path.is_absolute() else PROJECT_ROOT / config_path
    return candidate.resolve(strict=False) == (PROJECT_ROOT / DEFAULT_WECA_AREA).resolve(strict=False)


def _weca_release_fixture_failures(config_path: Path) -> list[str]:
    """Prove that the canonical command has its retained #125 inputs locally."""
    if not _weca_release_baseline_applies(config_path):
        return []
    try:
        area = AreaDefinition.from_yaml(config_path)
    except (OSError, ValueError) as error:
        return [f"weca-historical-fixture-unreadable:{error}"]
    if area_definition_sha256(area) != WECA_BASELINE_INPUT_DIGESTS["area_definition_sha256"]:
        return ["weca-historical-fixture-area-definition-digest-mismatch"]
    snapshot = area.source.snapshot_dir / area.source.snapshot_id / "snapshot.json"
    if not snapshot.is_file():
        return [f"weca-historical-snapshot-missing:{snapshot}"]
    if snapshot_manifest_sha256(area) != WECA_BASELINE_INPUT_DIGESTS["snapshot_manifest_sha256"]:
        return ["weca-historical-snapshot-manifest-digest-mismatch"]
    return []


def _logical_baseline_budget(
    lazy: Mapping[str, object], eager: Mapping[str, object], *, applies: bool
) -> dict[str, object]:
    """Fail closed when the canonical WECA pair no longer represents #125's input shape."""
    observed = {
        mode: {
            key: diagnostics.get(key) if isinstance(diagnostics := result.get("cross_spine_diagnostics"), Mapping) else None
            for key in WECA_LOGICAL_BASELINE_COUNTERS
        }
        for mode, result in (("eager", eager), ("lazy", lazy))
    }
    reasons: list[str] = []
    if applies:
        for mode, counters in observed.items():
            for key, threshold in WECA_LOGICAL_BASELINE_COUNTERS.items():
                value = counters[key]
                if not _is_nonnegative_int(value):
                    reasons.append(f"weca-logical-baseline-{mode}-{key}-missing-or-invalid")
                elif value != threshold:
                    reasons.append(f"weca-logical-baseline-{mode}-{key}-expected-{threshold}-got-{value}")
    return {
        "applies": applies,
        "contract": "#125-weca-logical-baseline/v1" if applies else "not-applicable-non-weca-paired-benchmark",
        "thresholds": dict(WECA_LOGICAL_BASELINE_COUNTERS) if applies else {},
        "observed": observed,
        "passed": not reasons,
        "reasons": reasons,
    }


def _weca_release_input_contract(
    lazy: Mapping[str, object], eager: Mapping[str, object], *, applies: bool
) -> dict[str, object]:
    """Bind the canonical WECA release pair to the #125 governed inputs.

    Pair equality proves only that the two workers agree with one another.  It
    cannot establish that they measured the retained #125 WECA baseline, so
    the canonical release command must separately require the exact recorded
    area and snapshot-manifest digests.  The source-inclusive #125 governed
    input fingerprint is recorded as historical baseline evidence instead of
    being falsely required after this implementation changes compiler source.
    Synthetic and other non-WECA paired commands retain their own contract and
    are explicitly N/A.
    """
    observed = {
        mode: {key: result.get(key) for key in INPUT_BINDING_KEYS}
        for mode, result in (("eager", eager), ("lazy", lazy))
    }
    reasons: list[str] = []
    if applies:
        for mode, values in observed.items():
            for key, expected in WECA_BASELINE_INPUT_DIGESTS.items():
                value = values[key]
                if value != expected:
                    reasons.append(
                        f"weca-release-input-{mode}-{key}-expected-{expected}-got-{value}"
                    )
    return {
        "applies": applies,
        "contract": "#125-weca-area-snapshot-and-current-compiler-identity/v2"
        if applies
        else "not-applicable-non-weca-paired-benchmark",
        "expected": dict(WECA_BASELINE_INPUT_DIGESTS) if applies else {},
        "observed": observed,
        "passed": not reasons,
        "reasons": reasons,
    }


def _paired_budget(
    lazy: Mapping[str, object], eager: Mapping[str, object], *, weca_release_baseline: bool
) -> dict[str, object]:
    reasons: list[str] = []
    lazy_diagnostics = lazy.get("cross_spine_diagnostics")
    route_searches = lazy_diagnostics.get("root_pair_route_searches") if isinstance(lazy_diagnostics, Mapping) else None
    if not _is_nonnegative_int(route_searches):
        reasons.append("lazy-root-pair-route-searches-missing")
    elif route_searches > 100:
        reasons.append("lazy-root-pair-route-searches-exceeds-100")
    lazy_wall = _metric(lazy, "phase_durations_seconds", "network_compile")
    lazy_cpu = _metric(lazy, "phase_process_cpu_seconds", "network_compile")
    eager_cpu = _metric(eager, "phase_process_cpu_seconds", "network_compile")
    if lazy_wall is None or lazy_wall > 1200:
        reasons.append("lazy-wall-seconds-missing-or-exceeds-1200")
    if lazy_cpu is None or lazy_cpu > 1200:
        reasons.append("lazy-cpu-seconds-missing-or-exceeds-1200")
    if eager_cpu is None or lazy_cpu is None or lazy_cpu >= eager_cpu:
        reasons.append("lazy-cpu-not-less-than-eager")
    lazy_rss = lazy.get("peak_rss")
    eager_rss = eager.get("peak_rss")
    lazy_bytes = lazy_rss.get("bytes") if isinstance(lazy_rss, Mapping) else None
    eager_bytes = eager_rss.get("bytes") if isinstance(eager_rss, Mapping) else None
    if not (isinstance(lazy_rss, Mapping) and isinstance(eager_rss, Mapping) and lazy_rss.get("supported") and eager_rss.get("supported")):
        reasons.append("peak-rss-unsupported")
    elif not _is_nonnegative_int(lazy_bytes) or not _is_nonnegative_int(eager_bytes) or lazy_bytes >= 4 * 1024**3 or lazy_bytes > eager_bytes * 1.10:
        reasons.append("lazy-rss-missing-or-exceeds-budget")
    if lazy.get("governed_output_signature") != eager.get("governed_output_signature"):
        reasons.append("governed-output-signature-mismatch")
    logical_baseline = _logical_baseline_budget(lazy, eager, applies=weca_release_baseline)
    input_contract = _weca_release_input_contract(lazy, eager, applies=weca_release_baseline)
    baseline_fingerprint = {
        mode: (
            result.get("baseline", {}).get("governed_input_fingerprint")
            if isinstance(result.get("baseline"), Mapping)
            else None
        )
        for mode, result in (("eager", eager), ("lazy", lazy))
    }
    baseline_reasons = []
    if weca_release_baseline:
        for mode, value in baseline_fingerprint.items():
            if value != WECA_BASELINE_GOVERNED_INPUT_FINGERPRINT:
                baseline_reasons.append(
                    "weca-baseline-governed-input-fingerprint-"
                    f"{mode}-expected-{WECA_BASELINE_GOVERNED_INPUT_FINGERPRINT}-got-{value}"
                )
    reasons.extend(logical_baseline["reasons"])
    reasons.extend(input_contract["reasons"])
    reasons.extend(baseline_reasons)
    return {
        "passed": not reasons,
        "reasons": reasons,
        "thresholds": {"lazy_wall_seconds_maximum": 1200, "lazy_cpu_seconds_maximum": 1200, "lazy_cpu_must_be_less_than_eager": True, "lazy_route_searches_maximum": 100, "lazy_rss_bytes_maximum": 4 * 1024**3, "lazy_rss_eager_ratio_maximum": 1.10},
        "observed": {"lazy_wall_seconds": lazy_wall, "lazy_cpu_seconds": lazy_cpu, "eager_cpu_seconds": eager_cpu, "lazy_root_pair_route_searches": route_searches, "lazy_peak_rss_bytes": lazy_bytes, "eager_peak_rss_bytes": eager_bytes},
        "logical_baseline": logical_baseline,
        "weca_release_input_contract": input_contract,
        "baseline_governed_input_fingerprint": {
            "contract": "#125-weca-source-inclusive-baseline/v1"
            if weca_release_baseline
            else "not-applicable-non-weca-paired-benchmark",
            "expected": WECA_BASELINE_GOVERNED_INPUT_FINGERPRINT
            if weca_release_baseline
            else None,
            "observed": baseline_fingerprint,
            "passed": not baseline_reasons,
            "reasons": baseline_reasons,
        },
    }


def _governed_output_parity(
    lazy: Mapping[str, object], eager: Mapping[str, object]
) -> dict[str, object]:
    """Return an evidence-safe first differing governed field without weakening parity."""
    eager_digests = eager.get("governed_output_field_digests")
    lazy_digests = lazy.get("governed_output_field_digests")
    expected = set(GOVERNED_OUTPUT_CONTRACT_FIELDS)
    if not (
        isinstance(eager_digests, Mapping)
        and isinstance(lazy_digests, Mapping)
        and set(eager_digests) == expected
        and set(lazy_digests) == expected
    ):
        return {
            "passed": False,
            "reason": "governed-output-field-digests-invalid-or-missing",
            "first_difference": None,
        }
    differences = [
        field_name
        for field_name in GOVERNED_OUTPUT_CONTRACT_FIELDS
        if eager_digests[field_name] != lazy_digests[field_name]
    ]
    first = differences[0] if differences else None
    return {
        "passed": not differences,
        "reason": None if not differences else "governed-output-field-digest-mismatch",
        "first_difference": (
            {
                "field": first,
                "eager_sha256": eager_digests[first],
                "lazy_sha256": lazy_digests[first],
            }
            if first is not None
            else None
        ),
    }


def _metric(result: Mapping[str, object], group: str, key: str) -> float | None:
    values = result.get(group)
    value = values.get(key) if isinstance(values, Mapping) else None
    return float(value) if _is_nonnegative_metric(value) else None


def paired_benchmark(config_path: Path) -> dict[str, object]:
    """Collect an eagerly and lazily scheduled pair and fail closed on any gap."""
    failures: list[str] = []
    fixture_failures = _weca_release_fixture_failures(config_path)
    if fixture_failures:
        return {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "benchmark_mode": "paired-eager-lazy",
            "passed": False,
            "reasons": fixture_failures,
            "modes": {},
        }
    try:
        eager = _run_worker(config_path, "eager")
        lazy = _run_worker(config_path, "lazy")
    except RuntimeError as error:
        return {"schema_version": BENCHMARK_SCHEMA_VERSION, "benchmark_mode": "paired", "passed": False, "reasons": [str(error)], "modes": {}}
    for mode, result in (("eager-reference", eager), ("lazy", lazy)):
        failures.extend(f"{mode}-worker-evidence-invalid:{failure}" for failure in _validate_worker_result(result, expected_mode=mode))
    for key in (*INPUT_BINDING_KEYS, "governed_output_signature"):
        eager_value = eager.get(key)
        lazy_value = lazy.get(key)
        if not (_is_sha256(eager_value) and _is_sha256(lazy_value) and eager_value == lazy_value):
            failures.append(f"pinned-input-mismatch-or-missing:{key}")
    governed_parity = _governed_output_parity(lazy, eager)
    if not governed_parity["passed"]:
        failures.append(str(governed_parity["reason"]))
    budget = _paired_budget(lazy, eager, weca_release_baseline=_weca_release_baseline_applies(config_path))
    failures.extend(budget["reasons"])
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "benchmark_mode": "paired-eager-lazy",
        "protocol": {"input_pairing": "same pinned area/snapshot plus current governed-input/compiler identity; subprocesses use PYTHONHASHSEED=0 for same-runtime schedule parity", "measurement": "isolated subprocess per mode for independent process-lifetime RSS and CPU", "logical_baseline": "canonical WECA paired command only: eager and lazy root_pairs_considered/root_pair_candidate_searches must both equal #125 baseline 2211", "weca_release_input_contract": "canonical WECA paired command only: both workers must exactly match #125 area-definition/snapshot digests and each other current governed-input/compiler identities; #125 source-inclusive fingerprint is separately recorded"},
        "input_binding": {key: lazy.get(key) for key in INPUT_BINDING_KEYS},
        "modes": {"eager": eager, "lazy": lazy},
        "governed_output_parity": governed_parity,
        "release_budget": budget,
        "passed": not failures,
        "reasons": failures,
    }


def benchmark_output_path(path: Path) -> Path:
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
            existing = _strict_json_loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("refusing to replace an unsafe existing non-benchmark file") from error
        if not isinstance(existing, dict) or existing.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
            raise ValueError("refusing to replace an unsafe existing non-benchmark file")
    return resolved


def _reject_symlink_components(candidate: Path, resolved: Path) -> None:
    current = candidate
    while current != current.parent:
        if current.exists() and current.is_symlink():
            raise ValueError("benchmark --output must not traverse a symlink")
        if current in (PROJECT_ROOT, BENCHMARK_ROOT):
            return
        current = current.parent
    if not resolved.is_relative_to(BENCHMARK_ROOT):
        raise ValueError(f"benchmark --output must be below ignored {BENCHMARK_ROOT}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "config",
        type=Path,
        help=(
            "Explicit Area Definition for this manually invoked governed benchmark "
            "(the WECA release gate is never a routine test)"
        ),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Ignored paired benchmark JSON path below build/.")
    parser.add_argument("--worker-mode", choices=("eager", "lazy"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker_mode:
        print(
            json.dumps(
                benchmark(args.config, eager_reference=args.worker_mode == "eager"),
                sort_keys=True,
                allow_nan=False,
            )
        )
        return 0
    try:
        output = benchmark_output_path(args.output)
    except ValueError as error:
        parser.error(str(error))
    result = paired_benchmark(args.config)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(output)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
