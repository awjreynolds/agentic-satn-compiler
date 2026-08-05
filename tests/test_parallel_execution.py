from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest

from satn.parallel_execution import (
    BenchmarkRecord,
    BenchmarkSweep,
    ExecutionProfile,
    ExecutionRun,
    PartitionExecutionError,
    PartitionExecutionResult,
    PartitionTask,
    benchmark_profiles,
    execute_partitions,
    select_auto_profile,
)


def _write_payload(task: PartitionTask) -> bytes:
    payload = Path(task.input_paths[0]).read_bytes()
    return task.partition_id.encode() + b":" + payload


def _flaky_worker(task: PartitionTask) -> bytes:
    marker = Path(task.output_path).with_suffix(".fail-once")
    if task.partition_id == "optional" and not marker.exists():
        marker.write_text("failed", encoding="utf-8")
        raise RuntimeError("synthetic worker failure")
    if task.partition_id == "required" and not marker.exists():
        marker.write_text("failed", encoding="utf-8")
        raise RuntimeError("synthetic worker failure")
    return _write_payload(task)


def _always_fails(task: PartitionTask) -> bytes:
    raise RuntimeError(f"failed {task.partition_id}")


def _different_worker(task: PartitionTask) -> bytes:
    return b"different:" + task.partition_id.encode()


def _task(tmp_path: Path, partition_id: str, *, required: bool = True) -> PartitionTask:
    source = tmp_path / f"{partition_id}.input"
    source.write_bytes(partition_id.encode())
    return PartitionTask(
        partition_id=partition_id,
        input_paths=(source,),
        output_path=tmp_path / f"{partition_id}.output",
        required=required,
        manifest={"partition": partition_id},
    )


def test_reference_profile_is_one_worker_and_one_duckdb_thread() -> None:
    profile = ExecutionProfile.reference()

    assert profile.mode == "serial"
    assert profile.workers == 1
    assert profile.duckdb_threads == 1
    assert profile.semantic_payload() == {"contract": "satn-parallel-profile/v1"}


def test_process_profile_requires_spawn_and_respects_combined_budget() -> None:
    profile = ExecutionProfile.process_spawn(
        workers=2,
        duckdb_threads=2,
        cpu_budget=4,
        memory_budget_mb=512,
    )

    assert profile.start_method == "spawn"
    assert profile.total_threads == 4
    assert profile.within_budget()
    with pytest.raises(ValueError, match="combined worker/DuckDB budget"):
        ExecutionProfile.process_spawn(
            workers=2,
            duckdb_threads=2,
            cpu_budget=3,
            memory_budget_mb=512,
        )


def test_native_thread_profile_requires_measured_gil_releasing_kernel() -> None:
    with pytest.raises(ValueError, match="GIL-releasing"):
        ExecutionProfile(
            profile_id="threads",
            mode="thread",
            workers=2,
            duckdb_threads=1,
        )

    profile = ExecutionProfile.native_threads(
        profile_id="geos-threads",
        workers=2,
        kernel="shapely-buffer",
        measured=True,
        cpu_budget=2,
        memory_budget_mb=512,
    )
    assert profile.native_kernel == "shapely-buffer"
    assert profile.measured


def test_parallel_execution_is_order_stable_and_worker_profile_not_semantic(
    tmp_path: Path,
) -> None:
    tasks = (_task(tmp_path, "b"), _task(tmp_path, "a"))
    serial = execute_partitions(tasks, _write_payload, profile=ExecutionProfile.reference())
    thread = execute_partitions(
        tasks,
        _write_payload,
        profile=ExecutionProfile.native_threads(
            profile_id="measured-test",
            workers=2,
            kernel="test-native",
            measured=True,
            cpu_budget=2,
            memory_budget_mb=512,
        ),
    )

    assert [item.partition_id for item in serial.results] == ["a", "b"]
    assert serial.semantic_digest == thread.semantic_digest
    assert serial.artifact_identity == thread.artifact_identity


def test_failed_optional_partition_retries_once_then_becomes_explicit_gap(tmp_path: Path) -> None:
    task = _task(tmp_path, "optional", required=False)
    run = execute_partitions((task,), _always_fails, profile=ExecutionProfile.reference())

    assert run.results[0].status == "gap"
    assert run.results[0].attempts == 2
    assert run.results[0].gap_reason == "worker-failure-after-serial-retry"


def test_failed_required_partition_retries_once_then_fails_closed(tmp_path: Path) -> None:
    task = _task(tmp_path, "required")

    with pytest.raises(PartitionExecutionError, match="required") as error:
        execute_partitions((task,), _always_fails, profile=ExecutionProfile.reference())
    assert error.value.attempts == 2


def test_one_worker_retry_is_serial_even_when_parallel_profile_requested(tmp_path: Path) -> None:
    task = _task(tmp_path, "optional", required=False)
    profile = ExecutionProfile.native_threads(
        profile_id="measured-test",
        workers=2,
        kernel="test-native",
        measured=True,
        cpu_budget=2,
        memory_budget_mb=512,
    )
    run = execute_partitions((task,), _always_fails, profile=profile)
    assert run.results[0].attempts == 2
    assert run.results[0].retry_mode == "serial"


def test_benchmark_rejects_semantic_digest_mismatch_and_records_wall_time_rss(
    tmp_path: Path,
) -> None:
    tasks = (_task(tmp_path, "a"),)
    profiles = (
        ExecutionProfile.reference(),
        ExecutionProfile(
            profile_id="measured-different",
            mode="serial",
            workers=1,
            duckdb_threads=1,
            measured=True,
            memory_budget_mb=512,
        ),
    )
    sweep = benchmark_profiles(tasks, _write_payload, profiles)
    mismatch = benchmark_profiles(tasks, _different_worker, profiles)

    assert sweep.reference_digest == sweep.records[0].semantic_digest
    assert all(isinstance(item, BenchmarkRecord) for item in sweep.records)
    assert all(item.wall_time_ms >= 0 for item in sweep.records)
    assert all(item.peak_rss_mb >= 0 for item in sweep.records)
    assert mismatch.records[0].safe
    assert mismatch.records[1].safe
    assert mismatch.records[1].rejection_reason is None

    with pytest.raises(ValueError, match="digest mismatch"):
        benchmark_profiles(
            tasks,
            _different_worker,
            (ExecutionProfile.reference(),),
            expected_digest=hashlib.sha256(b"wrong").hexdigest(),
        )


def test_auto_selects_fastest_measured_safe_profile_with_memory_gate() -> None:
    reference = ExecutionProfile.reference()
    fast = ExecutionProfile(
        profile_id="fast",
        mode="serial",
        workers=1,
        duckdb_threads=1,
        measured=True,
        memory_budget_mb=512,
    )
    too_large = ExecutionProfile(
        profile_id="large",
        mode="serial",
        workers=1,
        duckdb_threads=1,
        measured=True,
        memory_budget_mb=512,
    )
    records = (
        BenchmarkRecord(reference, 20.0, 10.0, "a" * 64, "a" * 64, True),
        BenchmarkRecord(fast, 5.0, 12.0, "a" * 64, "a" * 64, True),
        BenchmarkRecord(too_large, 1.0, 100.0, "a" * 64, "a" * 64, True),
    )

    assert select_auto_profile(records, memory_budget_mb=20) == fast
    assert select_auto_profile(records, memory_budget_mb=11) == reference


@pytest.mark.parametrize(
    ("input_paths", "output_path"),
    [
        (None, "out"),
        ("input", "out"),
        ((None,), "out"),
        (("",), "out"),
        (("input",), None),
        (("input",), []),
    ],
)
def test_partition_task_rejects_malformed_containers_and_paths(
    input_paths: object, output_path: object
) -> None:
    with pytest.raises(ValueError):
        PartitionTask("p", input_paths, output_path)  # type: ignore[arg-type]


def test_execution_run_rejects_invalid_profile_results_and_metrics() -> None:
    digest = "a" * 64
    valid_result = PartitionExecutionResult(
        "p", "gap", None, None, 1, False, "missing", error="evidence unavailable"
    )

    with pytest.raises(ValueError):
        ExecutionRun(None, (valid_result,), digest, 1.0, 1.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ExecutionRun(ExecutionProfile.reference(), None, digest, 1.0, 1.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ExecutionRun(ExecutionProfile.reference(), (None,), digest, 1.0, 1.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ExecutionRun(ExecutionProfile.reference(), (valid_result,), digest, "1", 1.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ExecutionRun(ExecutionProfile.reference(), (valid_result,), digest, math.nan, 1.0)


def test_benchmark_record_rejects_invalid_profile_and_metrics() -> None:
    digest = "a" * 64
    cases = [
        (None, 1.0, 1.0),
        (ExecutionProfile.reference(), "1", 1.0),
        (ExecutionProfile.reference(), 1.0, True),
        (ExecutionProfile.reference(), math.inf, 1.0),
    ]
    for profile, wall_time, peak_rss in cases:
        with pytest.raises(ValueError):
            BenchmarkRecord(profile, wall_time, peak_rss, digest, digest, True)  # type: ignore[arg-type]


def test_benchmark_sweep_rejects_invalid_record_container_and_values() -> None:
    digest = "a" * 64
    with pytest.raises(ValueError):
        BenchmarkRecord(  # type: ignore[arg-type]
            ExecutionProfile.reference(), 1.0, 1.0, digest, digest, False, 42
        )
    with pytest.raises(ValueError):
        BenchmarkSweep(digest, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        BenchmarkSweep(digest, "records")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        BenchmarkSweep(digest, (None,))  # type: ignore[arg-type]


def test_partition_execution_result_rejects_contradictory_complete_and_gap_states() -> None:
    digest = "a" * 64
    complete_cases = [
        {"output_path": None},
        {"semantic_digest": None},
        {"error": "unexpected"},
        {"gap_reason": "unexpected"},
    ]
    for overrides in complete_cases:
        values = {
            "partition_id": "p",
            "status": "complete",
            "output_path": "out",
            "semantic_digest": digest,
            "attempts": 1,
            "required": True,
        }
        values.update(overrides)
        with pytest.raises(ValueError):
            PartitionExecutionResult(**values)  # type: ignore[arg-type]

    gap_cases = [
        {"output_path": "out"},
        {"semantic_digest": digest},
        {"required": True},
        {"gap_reason": None},
        {"error": None},
    ]
    for overrides in gap_cases:
        values = {
            "partition_id": "p",
            "status": "gap",
            "output_path": None,
            "semantic_digest": None,
            "attempts": 2,
            "required": False,
            "gap_reason": "missing evidence",
            "error": "worker failure",
        }
        values.update(overrides)
        with pytest.raises(ValueError):
            PartitionExecutionResult(**values)  # type: ignore[arg-type]


def test_execution_run_canonical_digest_excludes_gaps_but_identity_includes_them() -> None:
    digest_a = hashlib.sha256(b"a").hexdigest()
    digest_b = hashlib.sha256(b"b").hexdigest()
    complete_a = PartitionExecutionResult("a", "complete", "a.out", digest_a, 1, True)
    complete_b = PartitionExecutionResult("b", "complete", "b.out", digest_b, 1, True)
    gap = PartitionExecutionResult(
        "gap", "gap", None, None, 2, False, "missing boundary", error="worker failure"
    )
    expected = hashlib.sha256(f"a\0{digest_a}\nb\0{digest_b}".encode()).hexdigest()
    with_gap = ExecutionRun(
        ExecutionProfile.reference(), (gap, complete_b, complete_a), expected, 1, 1
    )
    without_gap = ExecutionRun(
        ExecutionProfile.reference(), (complete_b, complete_a), expected, 1, 1
    )

    assert with_gap.semantic_digest == expected
    assert with_gap.semantic_digest == without_gap.semantic_digest
    assert with_gap.artifact_identity != without_gap.artifact_identity

    with pytest.raises(ValueError, match="canonical"):
        ExecutionRun(ExecutionProfile.reference(), (complete_a, complete_b), "c" * 64, 1, 1)
    with pytest.raises(ValueError, match="unique"):
        ExecutionRun(ExecutionProfile.reference(), (complete_a, complete_a), expected, 1, 1)


def test_benchmark_rejects_gap_diagnostic_divergence_even_when_complete_digest_matches(
    tmp_path: Path,
) -> None:
    tasks = (_task(tmp_path, "complete"), _task(tmp_path, "optional", required=False))
    calls = 0

    def worker(task: PartitionTask) -> bytes:
        nonlocal calls
        if task.partition_id == "optional":
            calls += 1
            raise RuntimeError(f"diagnostic-{calls}")
        return b"stable"

    measured = ExecutionProfile(
        profile_id="measured-serial",
        mode="serial",
        workers=1,
        duckdb_threads=1,
        measured=True,
    )
    sweep = benchmark_profiles(
        tasks,
        worker,
        (ExecutionProfile.reference(), measured),
    )

    assert sweep.records[0].safe
    assert sweep.records[1].semantic_digest == sweep.records[0].semantic_digest
    assert sweep.records[1].artifact_identity != sweep.records[0].artifact_identity
    assert not sweep.records[1].safe
    assert sweep.records[1].rejection_reason == (
        "artifact identity mismatch (gap/diagnostic divergence)"
    )
