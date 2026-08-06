"""Bounded local execution for deterministic geographic compilation bundles.

This module is deliberately a small execution seam rather than a compiler stage.
Partition identity and output content determine semantic digests; worker counts,
DuckDB settings, timing, memory observations, and filesystem paths are runtime
metadata only.  The process profile always uses an explicit ``spawn`` context so
macOS workers do not inherit a live interpreter or database connection.
"""

from __future__ import annotations

import hashlib
import math
import os
import resource
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from multiprocessing import get_context
from pathlib import Path
from time import monotonic_ns
from typing import Literal

EXECUTION_PROFILE_CONTRACT = "satn-parallel-profile/v1"
EXECUTION_TASK_CONTRACT = "satn-partition-execution-task/v1"
EXECUTION_RESULT_CONTRACT = "satn-partition-execution-result/v1"
BENCHMARK_CONTRACT = "satn-parallel-benchmark/v1"

ExecutionMode = Literal["serial", "process", "thread"]
PartitionStatus = Literal["complete", "gap"]


class PartitionExecutionError(RuntimeError):
    """A required partition could not complete after its serial retry."""

    def __init__(self, partition_id: str, attempts: int, cause: BaseException) -> None:
        self.partition_id = partition_id
        self.attempts = attempts
        self.cause = cause
        super().__init__(
            f"required partition {partition_id!r} failed after {attempts} attempts: {cause}"
        )


class SemanticDigestMismatch(ValueError):
    """A candidate execution did not reproduce the deterministic reference digest."""


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{label} must be non-empty canonical text")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _nonnegative_real(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite non-negative number")
    converted = float(value)
    if not math.isfinite(converted) or converted < 0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return converted


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _tuple_container(value: object, label: str) -> tuple[object, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{label} must be a tuple or list")
    return tuple(value)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_text(value: object, label: str) -> str:
    text = _text(value, label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _copy_manifest(value: object) -> object:
    """Copy JSON-like task metadata into a small picklable value."""

    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("task manifest keys must be strings")
        return {key: _copy_manifest(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return tuple(_copy_manifest(item) for item in value)
    raise ValueError(f"task manifest contains unsupported {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    """Portable runtime profile; no field contributes to a semantic artifact ID."""

    profile_id: str
    mode: ExecutionMode = "serial"
    workers: int = 1
    duckdb_threads: int = 1
    cpu_budget: int | None = None
    memory_budget_mb: int | None = None
    measured: bool = False
    native_kernel: str | None = None
    start_method: str | None = None

    def __post_init__(self) -> None:
        _text(self.profile_id, "profile_id")
        if self.mode not in ("serial", "process", "thread"):
            raise ValueError("execution profile mode is invalid")
        workers = _positive_int(self.workers, "workers")
        duckdb_threads = _positive_int(self.duckdb_threads, "duckdb_threads")
        object.__setattr__(self, "workers", workers)
        object.__setattr__(self, "duckdb_threads", duckdb_threads)
        if self.cpu_budget is not None:
            _positive_int(self.cpu_budget, "cpu_budget")
        if self.memory_budget_mb is not None:
            _positive_int(self.memory_budget_mb, "memory_budget_mb")
        if type(self.measured) is not bool:
            raise ValueError("measured must be a boolean")
        if self.mode == "serial" and (workers != 1 or duckdb_threads != 1):
            raise ValueError("serial profile must use one worker and one DuckDB thread")
        if self.mode == "process" and self.start_method != "spawn":
            raise ValueError("process profiles require the explicit macOS spawn context")
        if self.mode != "process" and self.start_method is not None:
            raise ValueError("start_method is only valid for process profiles")
        if self.mode == "thread":
            if not self.measured or not self.native_kernel:
                raise ValueError(
                    "thread profiles require a measured GIL-releasing native kernel"
                )
            _text(self.native_kernel, "native_kernel")
        elif self.native_kernel is not None:
            raise ValueError("native_kernel is only valid for thread profiles")
        if not self.within_budget():
            raise ValueError("combined worker/DuckDB budget is exceeded")

    @classmethod
    def reference(cls, *, memory_budget_mb: int | None = None) -> ExecutionProfile:
        """The deterministic one-worker, one-DuckDB-thread reference profile."""

        return cls(
            profile_id="serial-reference",
            mode="serial",
            workers=1,
            duckdb_threads=1,
            cpu_budget=1,
            memory_budget_mb=memory_budget_mb,
        )

    @classmethod
    def process_spawn(
        cls,
        *,
        workers: int,
        duckdb_threads: int = 1,
        cpu_budget: int | None = None,
        memory_budget_mb: int | None = None,
        measured: bool = False,
        profile_id: str | None = None,
    ) -> ExecutionProfile:
        return cls(
            profile_id=profile_id or f"spawn-{workers}x{duckdb_threads}",
            mode="process",
            workers=workers,
            duckdb_threads=duckdb_threads,
            cpu_budget=cpu_budget,
            memory_budget_mb=memory_budget_mb,
            measured=measured,
            start_method="spawn",
        )

    @classmethod
    def native_threads(
        cls,
        *,
        profile_id: str,
        workers: int,
        kernel: str,
        measured: bool,
        duckdb_threads: int = 1,
        cpu_budget: int | None = None,
        memory_budget_mb: int | None = None,
    ) -> ExecutionProfile:
        return cls(
            profile_id=profile_id,
            mode="thread",
            workers=workers,
            duckdb_threads=duckdb_threads,
            cpu_budget=cpu_budget,
            memory_budget_mb=memory_budget_mb,
            measured=measured,
            native_kernel=kernel,
        )

    @property
    def total_threads(self) -> int:
        return self.workers * self.duckdb_threads

    def within_budget(self) -> bool:
        return self.cpu_budget is None or self.total_threads <= self.cpu_budget

    def semantic_payload(self) -> dict[str, str]:
        """Stable semantic marker intentionally independent of runtime settings."""

        return {"contract": EXECUTION_PROFILE_CONTRACT}

    def operational_payload(self) -> dict[str, object]:
        return {
            "contract": EXECUTION_PROFILE_CONTRACT,
            "profile_id": self.profile_id,
            "mode": self.mode,
            "workers": self.workers,
            "duckdb_threads": self.duckdb_threads,
            "cpu_budget": self.cpu_budget,
            "memory_budget_mb": self.memory_budget_mb,
            "measured": self.measured,
            "native_kernel": self.native_kernel,
            "start_method": self.start_method,
        }


@dataclass(frozen=True, slots=True)
class PartitionTask:
    """Compact worker input containing identifiers and file-backed paths only."""

    partition_id: str
    input_paths: tuple[str | Path, ...]
    output_path: str | Path
    required: bool = True
    manifest: Mapping[str, object] = field(default_factory=dict)
    contract: str = field(init=False, default=EXECUTION_TASK_CONTRACT)

    def __post_init__(self) -> None:
        _text(self.partition_id, "partition_id")
        raw_paths = _tuple_container(self.input_paths, "input_paths")
        if any(not isinstance(path, (str, Path)) for path in raw_paths):
            raise ValueError("input_paths must contain strings or paths")
        if any(isinstance(path, str) and not path.strip() for path in raw_paths):
            raise ValueError("input_paths must contain paths")
        paths = tuple(str(Path(path)) for path in raw_paths)
        object.__setattr__(self, "input_paths", paths)
        if not isinstance(self.output_path, (str, Path)):
            raise ValueError("output_path must be a string or path")
        if isinstance(self.output_path, str) and not self.output_path.strip():
            raise ValueError("output_path must be non-empty")
        output = str(Path(self.output_path))
        _text(output, "output_path")
        object.__setattr__(self, "output_path", output)
        if type(self.required) is not bool:
            raise ValueError("required must be a boolean")
        if not isinstance(self.manifest, Mapping):
            raise ValueError("manifest must be a mapping")
        copied = _copy_manifest(self.manifest)
        if not isinstance(copied, dict):
            raise ValueError("manifest must be a mapping")
        object.__setattr__(self, "manifest", copied)

    def compact_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "partition_id": self.partition_id,
            "input_paths": list(self.input_paths),
            "required": self.required,
            "manifest": self.manifest,
        }


@dataclass(frozen=True, slots=True)
class _WorkerOutcome:
    partition_id: str
    output_path: str
    semantic_digest: str
    output_bytes: int


@dataclass(frozen=True, slots=True)
class PartitionExecutionResult:
    partition_id: str
    status: PartitionStatus
    output_path: str | None
    semantic_digest: str | None
    attempts: int
    required: bool
    gap_reason: str | None = None
    retry_mode: str | None = None
    error: str | None = None
    contract: str = field(init=False, default=EXECUTION_RESULT_CONTRACT)

    def __post_init__(self) -> None:
        _text(self.partition_id, "partition_id")
        _optional_text(self.output_path, "output_path")
        if self.status not in ("complete", "gap"):
            raise ValueError("partition execution status is invalid")
        _positive_int(self.attempts, "attempts")
        if type(self.required) is not bool:
            raise ValueError("required must be a boolean")
        _optional_text(self.gap_reason, "gap_reason")
        _optional_text(self.retry_mode, "retry_mode")
        _optional_text(self.error, "error")
        if self.status == "complete":
            if self.output_path is None:
                raise ValueError("complete partition requires an output path")
            if self.semantic_digest is None:
                raise ValueError("complete partition requires a semantic digest")
            _digest_text(self.semantic_digest, "semantic_digest")
            if self.gap_reason is not None or self.error is not None:
                raise ValueError("complete partition cannot have gap diagnostics")
        else:
            if self.required:
                raise ValueError("gap partition must be optional")
            if self.output_path is not None:
                raise ValueError("gap partition cannot have an output path")
            if self.semantic_digest is not None:
                raise ValueError("gap partition cannot have a semantic digest")
            if not self.gap_reason or not self.error:
                raise ValueError("gap partition requires canonical reason and error")


@dataclass(frozen=True, slots=True)
class ExecutionRun:
    """A run whose semantic digest covers complete outputs only.

    Optional gaps are deterministic operational outcomes.  They remain in
    :attr:`artifact_identity` (with their reason and diagnostic), but do not
    make the semantic content digest appear different from the complete routes
    that were successfully produced.
    """

    profile: ExecutionProfile
    results: tuple[PartitionExecutionResult, ...]
    semantic_digest: str
    wall_time_ms: float
    peak_rss_mb: float

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ExecutionProfile):
            raise ValueError("execution run profile must be an ExecutionProfile")
        raw_results = _tuple_container(self.results, "execution run results")
        if any(not isinstance(result, PartitionExecutionResult) for result in raw_results):
            raise ValueError("execution run results must be PartitionExecutionResult values")
        if len({result.partition_id for result in raw_results}) != len(raw_results):
            raise ValueError("execution run partition IDs must be unique")
        _digest_text(self.semantic_digest, "semantic_digest")
        expected_digest = _semantic_digest(raw_results)
        if self.semantic_digest != expected_digest:
            raise ValueError("execution run semantic digest is not canonical")
        object.__setattr__(
            self, "wall_time_ms", _nonnegative_real(self.wall_time_ms, "wall_time_ms")
        )
        object.__setattr__(
            self, "peak_rss_mb", _nonnegative_real(self.peak_rss_mb, "peak_rss_mb")
        )
        object.__setattr__(
            self,
            "results",
            tuple(sorted(raw_results, key=lambda value: value.partition_id)),
        )

    @property
    def artifact_identity(self) -> str:
        """Digest of ordered result records; excludes profile/timing/RSS."""

        payload = "\n".join(
            f"{result.partition_id}\0{result.status}\0{result.semantic_digest or ''}\0"
            f"{result.gap_reason or ''}\0{result.error or ''}"
            for result in self.results
        ).encode("utf-8")
        return _sha256(payload)

    def operational_payload(self) -> dict[str, object]:
        return {
            "profile": self.profile.operational_payload(),
            "wall_time_ms": self.wall_time_ms,
            "peak_rss_mb": self.peak_rss_mb,
            "semantic_digest": self.semantic_digest,
            "results": [
                {
                    "contract": result.contract,
                    "partition_id": result.partition_id,
                    "status": result.status,
                    "output_path": result.output_path,
                    "semantic_digest": result.semantic_digest,
                    "attempts": result.attempts,
                    "required": result.required,
                    "gap_reason": result.gap_reason,
                    "retry_mode": result.retry_mode,
                    "error": result.error,
                }
                for result in self.results
            ],
        }


@dataclass(frozen=True, slots=True)
class BenchmarkRecord:
    profile: ExecutionProfile
    wall_time_ms: float
    peak_rss_mb: float
    semantic_digest: str | None
    reference_digest: str
    safe: bool
    rejection_reason: str | None = None
    artifact_identity: str | None = None
    reference_artifact_identity: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ExecutionProfile):
            raise ValueError("benchmark profile must be an ExecutionProfile")
        object.__setattr__(
            self, "wall_time_ms", _nonnegative_real(self.wall_time_ms, "wall_time_ms")
        )
        object.__setattr__(
            self, "peak_rss_mb", _nonnegative_real(self.peak_rss_mb, "peak_rss_mb")
        )
        _digest_text(self.reference_digest, "reference_digest")
        if self.semantic_digest is not None:
            _digest_text(self.semantic_digest, "semantic_digest")
        if type(self.safe) is not bool:
            raise ValueError("benchmark safety must be a boolean")
        if self.safe and self.semantic_digest != self.reference_digest:
            raise ValueError("safe benchmark must match the reference semantic digest")
        if self.safe and self.rejection_reason is not None:
            raise ValueError("safe benchmark cannot have a rejection reason")
        _optional_text(self.rejection_reason, "rejection_reason")
        if (self.artifact_identity is None) != (self.reference_artifact_identity is None):
            raise ValueError("benchmark artifact identities must be provided together")
        if self.artifact_identity is not None:
            _digest_text(self.artifact_identity, "artifact_identity")
            assert self.reference_artifact_identity is not None
            _digest_text(self.reference_artifact_identity, "reference_artifact_identity")
        if (
            self.safe
            and self.artifact_identity is not None
            and self.artifact_identity != self.reference_artifact_identity
        ):
            raise ValueError("safe benchmark must match the reference artifact identity")

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id

    def payload(self) -> dict[str, object]:
        return {
            "contract": BENCHMARK_CONTRACT,
            "profile": self.profile.operational_payload(),
            "wall_time_ms": self.wall_time_ms,
            "peak_rss_mb": self.peak_rss_mb,
            "semantic_digest": self.semantic_digest,
            "reference_digest": self.reference_digest,
            "safe": self.safe,
            "rejection_reason": self.rejection_reason,
            "artifact_identity": self.artifact_identity,
            "reference_artifact_identity": self.reference_artifact_identity,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkSweep:
    reference_digest: str
    records: tuple[BenchmarkRecord, ...]

    def __post_init__(self) -> None:
        _digest_text(self.reference_digest, "reference_digest")
        raw_records = _tuple_container(self.records, "benchmark records")
        if any(not isinstance(record, BenchmarkRecord) for record in raw_records):
            raise ValueError("benchmark records must be BenchmarkRecord values")
        object.__setattr__(self, "records", tuple(raw_records))

    @property
    def safe_records(self) -> tuple[BenchmarkRecord, ...]:
        return tuple(record for record in self.records if record.safe)


PartitionWorker = Callable[[PartitionTask], bytes | bytearray | memoryview | Path | str | None]


def _rss_mb() -> float:
    observed = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # macOS reports bytes; Linux reports KiB.  Supporting both keeps benchmark
    # records portable without making platform topology part of identity.
    if os.uname().sysname == "Darwin":
        return observed / (1024 * 1024)
    return observed / 1024


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _materialise_worker_output(task: PartitionTask, output: object) -> _WorkerOutcome:
    destination = Path(task.output_path)
    if isinstance(output, (bytes, bytearray, memoryview)):
        _write_bytes_atomic(destination, bytes(output))
    elif isinstance(output, (Path, str)):
        source = Path(output)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"worker output file does not exist: {source}")
        if source != destination:
            _write_bytes_atomic(destination, source.read_bytes())
    elif output is None:
        if not destination.exists() or not destination.is_file():
            raise FileNotFoundError(
                f"worker returned no bytes and did not create output: {destination}"
            )
    else:
        raise TypeError("partition worker must return bytes, a file path, or None")
    content = destination.read_bytes()
    return _WorkerOutcome(task.partition_id, str(destination), _sha256(content), len(content))


def _invoke_worker(task: PartitionTask, worker: PartitionWorker) -> _WorkerOutcome:
    """Top-level importable protocol used by both process and thread executors."""

    return _materialise_worker_output(task, worker(task))


def _bounded_map(
    executor: Executor,
    tasks: Sequence[PartitionTask],
    worker: PartitionWorker,
    workers: int,
) -> list[tuple[_WorkerOutcome | None, BaseException | None]]:
    """Submit at most ``workers`` tasks at once while preserving input order."""

    pending: dict[int, Future[_WorkerOutcome]] = {}
    outcomes: list[tuple[_WorkerOutcome | None, BaseException | None] | None] = [
        None
    ] * len(tasks)
    next_index = 0

    def fill() -> None:
        nonlocal next_index
        while next_index < len(tasks) and len(pending) < workers:
            index = next_index
            next_index += 1
            pending[index] = executor.submit(_invoke_worker, tasks[index], worker)

    fill()
    while pending:
        index = min(pending)
        future = pending.pop(index)
        try:
            outcomes[index] = (future.result(), None)
        except BaseException as error:  # process pool failures are per-task observations
            outcomes[index] = (None, error)
        fill()
    return [item for item in outcomes if item is not None]


def _validate_tasks(tasks: Iterable[PartitionTask]) -> tuple[PartitionTask, ...]:
    ordered = tuple(sorted(tasks, key=lambda task: task.partition_id))
    if any(not isinstance(task, PartitionTask) for task in ordered):
        raise TypeError("partition tasks must be PartitionTask values")
    if len({task.partition_id for task in ordered}) != len(ordered):
        raise ValueError("partition task IDs must be unique")
    return ordered


def _semantic_digest(results: Iterable[PartitionExecutionResult]) -> str:
    """Digest only complete outputs; gaps stay in ``artifact_identity``."""

    payload = "\n".join(
        f"{result.partition_id}\0{result.semantic_digest}"
        for result in sorted(results, key=lambda value: value.partition_id)
        if result.status == "complete"
    ).encode("utf-8")
    return _sha256(payload)


def _run_attempts(
    tasks: tuple[PartitionTask, ...],
    worker: PartitionWorker,
    profile: ExecutionProfile,
) -> list[tuple[_WorkerOutcome | None, BaseException | None]]:
    if profile.mode == "serial":
        return [_serial_attempt(task, worker) for task in tasks]

    executor_class: type[Executor]
    if profile.mode == "process":
        executor_class = ProcessPoolExecutor
        executor = executor_class(
            max_workers=profile.workers,
            mp_context=get_context("spawn"),
        )
    else:
        executor_class = ThreadPoolExecutor
        executor = executor_class(max_workers=profile.workers)
    try:
        return _bounded_map(executor, tasks, worker, profile.workers)
    finally:
        executor.shutdown(wait=True, cancel_futures=False)


def _serial_attempt(
    task: PartitionTask, worker: PartitionWorker
) -> tuple[_WorkerOutcome | None, BaseException | None]:
    try:
        return _invoke_worker(task, worker), None
    except BaseException as error:
        return None, error


def execute_partitions(
    tasks: Iterable[PartitionTask],
    worker: PartitionWorker,
    *,
    profile: ExecutionProfile | None = None,
    expected_digest: str | None = None,
) -> ExecutionRun:
    """Execute bounded partition work with one deterministic serial retry."""

    selected = profile or ExecutionProfile.reference()
    if not isinstance(selected, ExecutionProfile):
        raise TypeError("profile must be an ExecutionProfile")
    ordered = _validate_tasks(tasks)
    if not callable(worker):
        raise TypeError("partition worker must be callable")
    started = monotonic_ns()
    before_rss = _rss_mb()
    # A pool startup failure is an execution-environment error, not a worker
    # failure.  It must remain visible to the caller/benchmark rather than being
    # misreported as a safe serial fallback.
    attempts = _run_attempts(ordered, worker, selected)

    results: list[PartitionExecutionResult] = []
    for task, (outcome, error) in zip(ordered, attempts, strict=True):
        if outcome is not None:
            results.append(
                PartitionExecutionResult(
                    partition_id=task.partition_id,
                    status="complete",
                    output_path=outcome.output_path,
                    semantic_digest=outcome.semantic_digest,
                    attempts=1,
                    required=task.required,
                )
            )
            continue
        retry_outcome, retry_error = _serial_attempt(task, worker)
        if retry_outcome is not None:
            results.append(
                PartitionExecutionResult(
                    partition_id=task.partition_id,
                    status="complete",
                    output_path=retry_outcome.output_path,
                    semantic_digest=retry_outcome.semantic_digest,
                    attempts=2,
                    required=task.required,
                    retry_mode="serial",
                )
            )
            continue
        if task.required:
            assert retry_error is not None
            Path(task.output_path).unlink(missing_ok=True)
            raise PartitionExecutionError(task.partition_id, 2, retry_error) from retry_error
        Path(task.output_path).unlink(missing_ok=True)
        results.append(
            PartitionExecutionResult(
                partition_id=task.partition_id,
                status="gap",
                output_path=None,
                semantic_digest=None,
                attempts=2,
                required=False,
                gap_reason="worker-failure-after-serial-retry",
                retry_mode="serial",
                error=str(retry_error or error) or "worker failure",
            )
        )

    digest = _semantic_digest(results)
    if expected_digest is not None and digest != _digest_text(expected_digest, "expected_digest"):
        raise SemanticDigestMismatch(
            f"semantic digest mismatch: expected {expected_digest}, observed {digest}"
        )
    elapsed = max(0.0, (monotonic_ns() - started) / 1_000_000)
    return ExecutionRun(
        profile=selected,
        results=tuple(results),
        semantic_digest=digest,
        wall_time_ms=elapsed,
        peak_rss_mb=max(before_rss, _rss_mb()),
    )


def benchmark_profiles(
    tasks: Iterable[PartitionTask],
    worker: PartitionWorker,
    profiles: Iterable[ExecutionProfile],
    *,
    memory_budget_mb: int | None = None,
    expected_digest: str | None = None,
) -> BenchmarkSweep:
    """Run a profile sweep against the serial reference and record safe results."""

    ordered_tasks = _validate_tasks(tasks)
    ordered_profiles = tuple(profiles)
    if not ordered_profiles:
        raise ValueError("benchmark profiles cannot be empty")
    reference = ExecutionProfile.reference(memory_budget_mb=memory_budget_mb)
    reference_run = execute_partitions(ordered_tasks, worker, profile=reference)
    reference_digest = reference_run.semantic_digest
    if expected_digest is not None and reference_digest != _digest_text(
        expected_digest, "expected_digest"
    ):
        raise SemanticDigestMismatch(
            f"semantic digest mismatch: expected {expected_digest}, observed {reference_digest}"
        )

    records: list[BenchmarkRecord] = []
    for profile in ordered_profiles:
        try:
            run = (
                reference_run
                if profile == reference
                else execute_partitions(
                    ordered_tasks,
                    worker,
                    profile=profile,
                    expected_digest=reference_digest,
                )
            )
            observed_memory = run.peak_rss_mb
            memory_ok = memory_budget_mb is None or observed_memory <= memory_budget_mb
            artifact_ok = run.artifact_identity == reference_run.artifact_identity
            safe = memory_ok and artifact_ok
            rejection_reason = (
                None
                if safe
                else (
                    "peak RSS exceeds memory budget"
                    if not memory_ok
                    else "artifact identity mismatch (gap/diagnostic divergence)"
                )
            )
            records.append(
                BenchmarkRecord(
                    profile=profile,
                    wall_time_ms=run.wall_time_ms,
                    peak_rss_mb=observed_memory,
                    semantic_digest=run.semantic_digest,
                    reference_digest=reference_digest,
                    safe=safe,
                    rejection_reason=rejection_reason,
                    artifact_identity=run.artifact_identity,
                    reference_artifact_identity=reference_run.artifact_identity,
                )
            )
        except SemanticDigestMismatch as error:
            records.append(
                BenchmarkRecord(
                    profile=profile,
                    wall_time_ms=0.0,
                    peak_rss_mb=0.0,
                    semantic_digest=None,
                    reference_digest=reference_digest,
                    safe=False,
                    rejection_reason=str(error),
                    artifact_identity=None,
                    reference_artifact_identity=None,
                )
            )
        except PartitionExecutionError as error:
            records.append(
                BenchmarkRecord(
                    profile=profile,
                    wall_time_ms=0.0,
                    peak_rss_mb=0.0,
                    semantic_digest=None,
                    reference_digest=reference_digest,
                    safe=False,
                    rejection_reason=str(error),
                    artifact_identity=None,
                    reference_artifact_identity=None,
                )
            )
        except Exception as error:
            records.append(
                BenchmarkRecord(
                    profile=profile,
                    wall_time_ms=0.0,
                    peak_rss_mb=0.0,
                    semantic_digest=None,
                    reference_digest=reference_digest,
                    safe=False,
                    rejection_reason=f"execution profile unavailable: {error}",
                    artifact_identity=None,
                    reference_artifact_identity=None,
                )
            )
    return BenchmarkSweep(reference_digest=reference_digest, records=tuple(records))


def select_auto_profile(
    records: Iterable[BenchmarkRecord],
    *,
    memory_budget_mb: int | None = None,
) -> ExecutionProfile:
    """Select the fastest measured safe profile, falling back to the reference."""

    ordered = tuple(records)
    candidates = [
        record
        for record in ordered
        if record.safe
        and record.profile.measured
        and record.semantic_digest == record.reference_digest
        and (
            record.artifact_identity is None
            or record.artifact_identity == record.reference_artifact_identity
        )
        and (memory_budget_mb is None or record.peak_rss_mb <= memory_budget_mb)
    ]
    if candidates:
        return min(candidates, key=lambda record: (record.wall_time_ms, record.profile_id)).profile
    for record in ordered:
        if (
            record.safe
            and record.profile.mode == "serial"
            and record.profile.workers == 1
            and record.profile.duckdb_threads == 1
            and record.semantic_digest == record.reference_digest
            and (
                record.artifact_identity is None
                or record.artifact_identity == record.reference_artifact_identity
            )
            and (memory_budget_mb is None or record.peak_rss_mb <= memory_budget_mb)
        ):
            return record.profile
    return ExecutionProfile.reference(memory_budget_mb=memory_budget_mb)


__all__ = [
    "BENCHMARK_CONTRACT",
    "EXECUTION_PROFILE_CONTRACT",
    "EXECUTION_RESULT_CONTRACT",
    "EXECUTION_TASK_CONTRACT",
    "BenchmarkRecord",
    "BenchmarkSweep",
    "ExecutionProfile",
    "ExecutionRun",
    "PartitionExecutionError",
    "PartitionExecutionResult",
    "PartitionTask",
    "SemanticDigestMismatch",
    "benchmark_profiles",
    "execute_partitions",
    "select_auto_profile",
]
