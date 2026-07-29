"""Bounded orchestration for governed EA elevation fixed-point convergence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_FIXED_POINT_ITERATIONS = 10


def _sha256(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase full SHA-256")
    return value


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _route_inventory(values: tuple[str, ...]) -> tuple[str, ...]:
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("EA fixed-point route inventory values must be nonempty text")
    if len(set(values)) != len(values):
        raise ValueError("EA fixed-point route inventory cannot contain duplicates")
    return tuple(sorted(values))


def _governed_source_identities(
    values: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    if not values:
        raise ValueError("EA fixed-point snapshot must identify governed source files")
    if any(
        not isinstance(name, str)
        or not name
        or not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
        for name, digest in values
    ):
        raise ValueError("EA fixed-point governed source identity is invalid")
    if len({name for name, _digest in values}) != len(values):
        raise ValueError("EA fixed-point governed source identities cannot repeat a file")
    return tuple(sorted(values))


@dataclass(frozen=True)
class EAFixedPointSnapshot:
    """One immutable snapshot state admitted to the convergence loop."""

    snapshot_id: str
    manifest_sha256: str
    primary_fingerprint: str
    retained_sample_routes: Path
    route_inventory: tuple[str, ...]
    governed_source_identities: tuple[tuple[str, str], ...]
    parent_snapshot_id: str | None = None
    parent_manifest_sha256: str | None = None
    elevation_evidence_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.snapshot_id or self.snapshot_id.strip() != self.snapshot_id:
            raise ValueError("EA fixed-point snapshot_id must be nonempty canonical text")
        _sha256(self.manifest_sha256, "EA fixed-point snapshot manifest")
        _sha256(self.primary_fingerprint, "EA fixed-point primary fingerprint")
        object.__setattr__(self, "route_inventory", _route_inventory(self.route_inventory))
        object.__setattr__(
            self,
            "governed_source_identities",
            _governed_source_identities(self.governed_source_identities),
        )
        has_parent_id = self.parent_snapshot_id is not None
        has_parent_digest = self.parent_manifest_sha256 is not None
        if has_parent_id != has_parent_digest:
            raise ValueError("EA fixed-point snapshot lineage must be complete")
        if self.parent_manifest_sha256 is not None:
            _sha256(self.parent_manifest_sha256, "EA fixed-point parent manifest")


@dataclass(frozen=True)
class EAFixedPointCompilation:
    """One fully validated compile comparison at a named snapshot."""

    expected_fingerprint: str
    actual_fingerprint: str
    candidate_network: Path
    urban_access_ms: int
    topography_ms: int
    acquisition_command: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _sha256(self.expected_fingerprint, "EA fixed-point expected fingerprint")
        _sha256(self.actual_fingerprint, "EA fixed-point actual fingerprint")
        for name in ("urban_access_ms", "topography_ms"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True)
class EAFixedPointAcquisition:
    """New elevation output and its monotonically retained route inventory."""

    primary_fingerprint: str
    route_inventory: tuple[str, ...]
    evidence_path: Path

    def __post_init__(self) -> None:
        _sha256(self.primary_fingerprint, "EA acquisition primary fingerprint")
        object.__setattr__(self, "route_inventory", _route_inventory(self.route_inventory))


@dataclass(frozen=True)
class EAFixedPointSnapshotCreation:
    """A sealed snapshot plus separately measured validation work."""

    snapshot: EAFixedPointSnapshot
    snapshot_seal_ms: int
    snapshot_validation_ms: int

    def __post_init__(self) -> None:
        for name in ("snapshot_seal_ms", "snapshot_validation_ms"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True)
class EAFixedPointTimings:
    acquisition_ms: int
    snapshot_seal_ms: int
    snapshot_validation_ms: int
    urban_access_ms: int
    topography_ms: int

    def __post_init__(self) -> None:
        for name in (
            "acquisition_ms",
            "snapshot_seal_ms",
            "snapshot_validation_ms",
            "urban_access_ms",
            "topography_ms",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True)
class EAFixedPointIteration:
    iteration: int
    snapshot_id: str
    snapshot_manifest_sha256: str
    expected_fingerprint: str
    actual_fingerprint: str
    route_inventory: tuple[str, ...]
    governed_source_identities: tuple[tuple[str, str], ...]
    timings: EAFixedPointTimings

    def __post_init__(self) -> None:
        if type(self.iteration) is not int or self.iteration < 1:
            raise ValueError("EA fixed-point iteration must be a positive integer")
        if not self.snapshot_id:
            raise ValueError("EA fixed-point iteration snapshot_id must be nonempty")
        _sha256(self.snapshot_manifest_sha256, "EA fixed-point iteration manifest")
        _sha256(self.expected_fingerprint, "EA fixed-point expected fingerprint")
        _sha256(self.actual_fingerprint, "EA fixed-point actual fingerprint")
        object.__setattr__(self, "route_inventory", _route_inventory(self.route_inventory))
        object.__setattr__(
            self,
            "governed_source_identities",
            _governed_source_identities(self.governed_source_identities),
        )


class EAFixedPointOperations(Protocol):
    """Validated side effects required by the pure bounded loop."""

    def restore(self, snapshot: EAFixedPointSnapshot) -> None: ...

    def compile(self, snapshot: EAFixedPointSnapshot) -> EAFixedPointCompilation: ...

    def acquire(
        self,
        snapshot: EAFixedPointSnapshot,
        compilation: EAFixedPointCompilation,
    ) -> EAFixedPointAcquisition: ...

    def snapshot(
        self,
        previous: EAFixedPointSnapshot,
        acquisition: EAFixedPointAcquisition,
        iteration: int,
    ) -> EAFixedPointSnapshotCreation: ...


ConvergenceStatus = Literal["converged", "non-converged"]


@dataclass(frozen=True)
class EAFixedPointConvergenceResult:
    """Terminal convergence or bounded-refusal record."""

    status: ConvergenceStatus
    final_snapshot: EAFixedPointSnapshot
    iterations: tuple[EAFixedPointIteration, ...]
    max_iterations: int
    record_path: Path
    run_token: str
    configuration_identity: str | None = None
    terminal_candidate_network: Path | None = None


def converge_ea_fixed_point(
    initial_snapshot: EAFixedPointSnapshot | None,
    *,
    operations: EAFixedPointOperations,
    max_iterations: int,
    record_path: Path,
    run_token: str = "direct",
    resume: bool = False,
    configuration_identity: str | None = None,
) -> EAFixedPointConvergenceResult:
    """Advance immutable snapshots until equality or an explicit bounded refusal."""

    if type(max_iterations) is not int or not 1 <= max_iterations <= MAX_FIXED_POINT_ITERATIONS:
        raise ValueError(
            f"max_iterations must be between 1 and {MAX_FIXED_POINT_ITERATIONS}"
        )
    if not run_token or run_token.strip() != run_token:
        raise ValueError("EA fixed-point run token must be nonempty canonical text")
    if configuration_identity is not None:
        _sha256(configuration_identity, "EA fixed-point configuration identity")
    if resume:
        if initial_snapshot is not None:
            raise ValueError("EA fixed-point resume must restore its recorded snapshot")
        (
            current,
            history,
            iteration,
            compilation,
            acquisition,
            acquisition_ms,
        ) = _read_checkpoint(
            record_path,
            run_token=run_token,
            max_iterations=max_iterations,
            configuration_identity=configuration_identity,
        )
        operations.restore(current)
    else:
        if initial_snapshot is None:
            raise ValueError("EA fixed-point initial snapshot is required")
        current = initial_snapshot
        history = []
        iteration = 1
        compilation = None
        acquisition = None
        acquisition_ms = 0
        _write_checkpoint(
            record_path,
            create=True,
            phase="initial",
            run_token=run_token,
            max_iterations=max_iterations,
            configuration_identity=configuration_identity,
            current=current,
            history=history,
            iteration=iteration,
        )

    while iteration <= max_iterations:
        compiled_now = compilation is None
        if compiled_now:
            compilation = operations.compile(current)
        if compilation.expected_fingerprint != current.primary_fingerprint:
            raise ValueError(
                "EA compile expectation does not match immutable snapshot primary fingerprint"
            )
        if compiled_now:
            _write_checkpoint(
                record_path,
                create=False,
                phase="compile-complete",
                run_token=run_token,
                max_iterations=max_iterations,
                configuration_identity=configuration_identity,
                current=current,
                history=history,
                iteration=iteration,
                compilation=compilation,
            )
        snapshot_seal_ms = 0
        snapshot_validation_ms = 0
        converged = compilation.expected_fingerprint == compilation.actual_fingerprint
        next_snapshot = current
        if not converged and iteration < max_iterations:
            if acquisition is None:
                acquisition_started = time.perf_counter_ns()
                acquisition = operations.acquire(current, compilation)
                acquisition_ms = (
                    time.perf_counter_ns() - acquisition_started
                ) // 1_000_000
            if acquisition.primary_fingerprint != compilation.actual_fingerprint:
                raise ValueError(
                    "EA acquisition primary does not match the compiled candidate"
                )
            if not set(current.route_inventory) <= set(acquisition.route_inventory):
                raise ValueError(
                    "EA acquisition discarded previously retained sampled alternatives"
                )
            _write_checkpoint(
                record_path,
                create=False,
                phase="acquisition-complete",
                run_token=run_token,
                max_iterations=max_iterations,
                configuration_identity=configuration_identity,
                current=current,
                history=history,
                iteration=iteration,
                compilation=compilation,
                acquisition=acquisition,
                acquisition_ms=acquisition_ms,
            )
            creation = operations.snapshot(current, acquisition, iteration)
            next_snapshot = creation.snapshot
            snapshot_seal_ms = creation.snapshot_seal_ms
            snapshot_validation_ms = creation.snapshot_validation_ms
            if (
                next_snapshot.parent_snapshot_id != current.snapshot_id
                or next_snapshot.parent_manifest_sha256 != current.manifest_sha256
            ):
                raise ValueError("EA convergence snapshot lineage is not immutable")
            if next_snapshot.primary_fingerprint != acquisition.primary_fingerprint:
                raise ValueError(
                    "EA convergence snapshot primary differs from its acquisition"
                )
            if next_snapshot.route_inventory != acquisition.route_inventory:
                raise ValueError(
                    "EA convergence snapshot changed the acquired sampled alternatives"
                )
        history.append(
            EAFixedPointIteration(
                iteration=iteration,
                snapshot_id=current.snapshot_id,
                snapshot_manifest_sha256=current.manifest_sha256,
                expected_fingerprint=compilation.expected_fingerprint,
                actual_fingerprint=compilation.actual_fingerprint,
                route_inventory=current.route_inventory,
                governed_source_identities=current.governed_source_identities,
                timings=EAFixedPointTimings(
                    acquisition_ms=acquisition_ms,
                    snapshot_seal_ms=snapshot_seal_ms,
                    snapshot_validation_ms=snapshot_validation_ms,
                    urban_access_ms=compilation.urban_access_ms,
                    topography_ms=compilation.topography_ms,
                ),
            )
        )
        if converged:
            result = EAFixedPointConvergenceResult(
                status="converged",
                final_snapshot=current,
                iterations=tuple(history),
                max_iterations=max_iterations,
                record_path=record_path,
                run_token=run_token,
                configuration_identity=configuration_identity,
                terminal_candidate_network=compilation.candidate_network,
            )
            _write_convergence_record(result)
            return result
        current = next_snapshot
        iteration += 1
        compilation = None
        acquisition = None
        acquisition_ms = 0
        if iteration <= max_iterations:
            _write_checkpoint(
                record_path,
                create=False,
                phase="snapshot-complete",
                run_token=run_token,
                max_iterations=max_iterations,
                configuration_identity=configuration_identity,
                current=current,
                history=history,
                iteration=iteration,
            )
    result = EAFixedPointConvergenceResult(
        status="non-converged",
        final_snapshot=current,
        iterations=tuple(history),
        max_iterations=max_iterations,
        record_path=record_path,
        run_token=run_token,
        configuration_identity=configuration_identity,
    )
    _write_convergence_record(result)
    return result


def _write_convergence_record(result: EAFixedPointConvergenceResult) -> None:
    document = {
        "schema_version": "ea-fixed-point-convergence/v2",
        "status": result.status,
        "run_token": result.run_token,
        "configuration_identity": result.configuration_identity,
        "max_iterations": result.max_iterations,
        "final_snapshot": _snapshot_record(result.final_snapshot),
        "iterations": [_iteration_record(item) for item in result.iterations],
    }
    if result.status == "converged":
        candidate = result.terminal_candidate_network
        if candidate is None or not candidate.is_file() or candidate.is_symlink():
            raise ValueError("EA terminal candidate network is missing or unsafe")
        run_manifest = candidate.parent / "run.json"
        if not run_manifest.is_file() or run_manifest.is_symlink():
            raise ValueError("EA terminal run manifest is missing or unsafe")
        document["terminal_artifacts"] = {
            "candidate_network": str(candidate),
            "candidate_network_sha256": _sha256_path(candidate),
            "run_manifest": str(run_manifest),
            "run_manifest_sha256": _sha256_path(run_manifest),
        }
    _atomic_write_document(result.record_path, document, create=False)


def _write_checkpoint(
    record_path: Path,
    *,
    create: bool,
    phase: str,
    run_token: str,
    max_iterations: int,
    configuration_identity: str | None,
    current: EAFixedPointSnapshot,
    history: list[EAFixedPointIteration],
    iteration: int,
    compilation: EAFixedPointCompilation | None = None,
    acquisition: EAFixedPointAcquisition | None = None,
    acquisition_ms: int = 0,
) -> None:
    _atomic_write_document(
        record_path,
        {
            "schema_version": "ea-fixed-point-convergence/v2",
            "status": "in-progress",
            "phase": phase,
            "run_token": run_token,
            "configuration_identity": configuration_identity,
            "max_iterations": max_iterations,
            "active_iteration": iteration,
            "current_snapshot": _snapshot_record(current),
            "iterations": [_iteration_record(item) for item in history],
            "compilation": (
                _compilation_record(compilation) if compilation is not None else None
            ),
            "acquisition": (
                _acquisition_record(acquisition) if acquisition is not None else None
            ),
            "acquisition_ms": acquisition_ms,
        },
        create=create,
    )


def _read_checkpoint(
    record_path: Path,
    *,
    run_token: str,
    max_iterations: int,
    configuration_identity: str | None,
) -> tuple[
    EAFixedPointSnapshot,
    list[EAFixedPointIteration],
    int,
    EAFixedPointCompilation | None,
    EAFixedPointAcquisition | None,
    int,
]:
    if record_path.is_symlink() or not record_path.is_file():
        raise ValueError("EA fixed-point resume record is missing or unsafe")
    try:
        document = json.loads(record_path.read_text(encoding="utf-8"))
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != "ea-fixed-point-convergence/v2"
            or document.get("status") != "in-progress"
            or document.get("run_token") != run_token
            or document.get("max_iterations") != max_iterations
            or document.get("configuration_identity") != configuration_identity
        ):
            raise ValueError("record identity or status differs from this run")
        phase = document["phase"]
        if phase not in {
            "initial",
            "compile-complete",
            "acquisition-complete",
            "snapshot-complete",
        }:
            raise ValueError("record phase is invalid")
        iteration = document["active_iteration"]
        if type(iteration) is not int or not 1 <= iteration <= max_iterations:
            raise ValueError("record iteration is invalid")
        current = _snapshot_from_record(document["current_snapshot"])
        raw_history = document["iterations"]
        if not isinstance(raw_history, list):
            raise ValueError("record history is invalid")
        history = [_iteration_from_record(item) for item in raw_history]
        if [item.iteration for item in history] != list(range(1, iteration)):
            raise ValueError("record history is not contiguous")
        compilation = (
            _compilation_from_record(document["compilation"])
            if phase in {"compile-complete", "acquisition-complete"}
            else None
        )
        acquisition = (
            _acquisition_from_record(document["acquisition"])
            if phase == "acquisition-complete"
            else None
        )
        acquisition_ms = document["acquisition_ms"]
        if type(acquisition_ms) is not int or acquisition_ms < 0:
            raise ValueError("record acquisition timing is invalid")
    except (KeyError, TypeError, json.JSONDecodeError, OSError, ValueError) as error:
        raise ValueError(
            f"EA fixed-point resume record is invalid: {record_path}: {error}"
        ) from error
    return (
        current,
        history,
        iteration,
        compilation,
        acquisition,
        acquisition_ms,
    )


def _atomic_write_document(
    path: Path,
    document: dict[str, object],
    *,
    create: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("EA fixed-point convergence record cannot be a symlink")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(
                (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
            )
            stream.flush()
            os.fsync(stream.fileno())
        if create:
            try:
                os.link(temporary, path)
            except FileExistsError as error:
                raise ValueError(
                    f"EA fixed-point convergence record already exists: {path}"
                ) from error
            temporary.unlink()
        else:
            if not path.is_file():
                raise ValueError(
                    f"EA fixed-point convergence record is missing: {path}"
                )
            temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _snapshot_record(snapshot: EAFixedPointSnapshot) -> dict[str, object]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "manifest_sha256": snapshot.manifest_sha256,
        "primary_fingerprint": snapshot.primary_fingerprint,
        "retained_sample_routes": str(snapshot.retained_sample_routes),
        "elevation_evidence_path": (
            str(snapshot.elevation_evidence_path)
            if snapshot.elevation_evidence_path is not None
            else None
        ),
        "route_inventory": list(snapshot.route_inventory),
        "governed_source_identities": {
            name: digest for name, digest in snapshot.governed_source_identities
        },
        "parent_snapshot_id": snapshot.parent_snapshot_id,
        "parent_manifest_sha256": snapshot.parent_manifest_sha256,
    }


def _snapshot_from_record(value: object) -> EAFixedPointSnapshot:
    if not isinstance(value, dict):
        raise ValueError("snapshot record is invalid")
    sources = value["governed_source_identities"]
    if not isinstance(sources, dict):
        raise ValueError("snapshot governed sources are invalid")
    return EAFixedPointSnapshot(
        snapshot_id=value["snapshot_id"],
        manifest_sha256=value["manifest_sha256"],
        primary_fingerprint=value["primary_fingerprint"],
        retained_sample_routes=Path(value["retained_sample_routes"]),
        route_inventory=tuple(value["route_inventory"]),
        governed_source_identities=tuple(sources.items()),
        parent_snapshot_id=value["parent_snapshot_id"],
        parent_manifest_sha256=value["parent_manifest_sha256"],
        elevation_evidence_path=(
            Path(value["elevation_evidence_path"])
            if value.get("elevation_evidence_path") is not None
            else None
        ),
    )


def terminal_convergence_result(
    record_path: Path,
    document: object,
) -> EAFixedPointConvergenceResult:
    """Parse a terminal record as a strict, internally closed replay capability."""

    fields = {
        "schema_version",
        "status",
        "run_token",
        "configuration_identity",
        "max_iterations",
        "final_snapshot",
        "terminal_artifacts",
        "iterations",
    }
    snapshot_fields = {
        "snapshot_id",
        "manifest_sha256",
        "primary_fingerprint",
        "retained_sample_routes",
        "elevation_evidence_path",
        "route_inventory",
        "governed_source_identities",
        "parent_snapshot_id",
        "parent_manifest_sha256",
    }
    artifact_fields = {
        "candidate_network",
        "candidate_network_sha256",
        "run_manifest",
        "run_manifest_sha256",
    }
    iteration_fields = {
        "iteration",
        "snapshot_id",
        "snapshot_manifest_sha256",
        "expected_fingerprint",
        "actual_fingerprint",
        "route_inventory",
        "governed_source_identities",
        "timings_ms",
    }
    timing_fields = {
        "acquisition",
        "snapshot_seal",
        "snapshot_validation",
        "urban_access",
        "topography",
    }
    try:
        if (
            not isinstance(document, dict)
            or set(document) != fields
            or document["schema_version"] != "ea-fixed-point-convergence/v2"
            or document["status"] != "converged"
            or not isinstance(document["run_token"], str)
            or not document["run_token"]
            or document["run_token"].strip() != document["run_token"]
        ):
            raise TypeError
        _sha256(document["configuration_identity"], "terminal configuration identity")
        maximum = document["max_iterations"]
        if type(maximum) is not int or not 1 <= maximum <= MAX_FIXED_POINT_ITERATIONS:
            raise TypeError
        snapshot_record = document["final_snapshot"]
        artifacts = document["terminal_artifacts"]
        iteration_records = document["iterations"]
        if (
            not isinstance(snapshot_record, dict)
            or set(snapshot_record) != snapshot_fields
            or not isinstance(snapshot_record["retained_sample_routes"], str)
            or not isinstance(snapshot_record["elevation_evidence_path"], str)
            or not isinstance(snapshot_record["parent_snapshot_id"], str)
            or not isinstance(artifacts, dict)
            or set(artifacts) != artifact_fields
            or any(not isinstance(artifacts[field], str) for field in artifact_fields)
            or not isinstance(iteration_records, list)
            or not 1 <= len(iteration_records) <= maximum
        ):
            raise TypeError
        _sha256(artifacts["candidate_network_sha256"], "terminal candidate")
        _sha256(artifacts["run_manifest_sha256"], "terminal run manifest")
        for position, item in enumerate(iteration_records, start=1):
            if (
                not isinstance(item, dict)
                or set(item) != iteration_fields
                or item["iteration"] != position
                or not isinstance(item["timings_ms"], dict)
                or set(item["timings_ms"]) != timing_fields
                or any(type(item["timings_ms"][key]) is not int for key in timing_fields)
            ):
                raise TypeError
        snapshot = _snapshot_from_record(snapshot_record)
        iterations = tuple(_iteration_from_record(item) for item in iteration_records)
        last = iterations[-1]
        if (
            last.expected_fingerprint != last.actual_fingerprint
            or last.expected_fingerprint != snapshot.primary_fingerprint
            or last.snapshot_id != snapshot.snapshot_id
            or last.snapshot_manifest_sha256 != snapshot.manifest_sha256
        ):
            raise TypeError
        return EAFixedPointConvergenceResult(
            status="converged",
            final_snapshot=snapshot,
            iterations=iterations,
            max_iterations=maximum,
            record_path=record_path,
            run_token=document["run_token"],
            configuration_identity=document["configuration_identity"],
            terminal_candidate_network=Path(artifacts["candidate_network"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("EA fixed-point terminal record is invalid") from error


def _compilation_record(
    compilation: EAFixedPointCompilation,
) -> dict[str, object]:
    return {
        "expected_fingerprint": compilation.expected_fingerprint,
        "actual_fingerprint": compilation.actual_fingerprint,
        "candidate_network": str(compilation.candidate_network),
        "urban_access_ms": compilation.urban_access_ms,
        "topography_ms": compilation.topography_ms,
        "acquisition_command": list(compilation.acquisition_command),
    }


def _compilation_from_record(value: object) -> EAFixedPointCompilation:
    if not isinstance(value, dict):
        raise ValueError("compilation record is invalid")
    return EAFixedPointCompilation(
        expected_fingerprint=value["expected_fingerprint"],
        actual_fingerprint=value["actual_fingerprint"],
        candidate_network=Path(value["candidate_network"]),
        urban_access_ms=value["urban_access_ms"],
        topography_ms=value["topography_ms"],
        acquisition_command=tuple(value["acquisition_command"]),
    )


def _acquisition_record(
    acquisition: EAFixedPointAcquisition,
) -> dict[str, object]:
    return {
        "primary_fingerprint": acquisition.primary_fingerprint,
        "route_inventory": list(acquisition.route_inventory),
        "evidence_path": str(acquisition.evidence_path),
    }


def _acquisition_from_record(value: object) -> EAFixedPointAcquisition:
    if not isinstance(value, dict):
        raise ValueError("acquisition record is invalid")
    return EAFixedPointAcquisition(
        primary_fingerprint=value["primary_fingerprint"],
        route_inventory=tuple(value["route_inventory"]),
        evidence_path=Path(value["evidence_path"]),
    )


def _iteration_record(iteration: EAFixedPointIteration) -> dict[str, object]:
    return {
        "iteration": iteration.iteration,
        "snapshot_id": iteration.snapshot_id,
        "snapshot_manifest_sha256": iteration.snapshot_manifest_sha256,
        "expected_fingerprint": iteration.expected_fingerprint,
        "actual_fingerprint": iteration.actual_fingerprint,
        "route_inventory": list(iteration.route_inventory),
        "governed_source_identities": {
            name: digest for name, digest in iteration.governed_source_identities
        },
        "timings_ms": {
            "acquisition": iteration.timings.acquisition_ms,
            "snapshot_seal": iteration.timings.snapshot_seal_ms,
            "snapshot_validation": iteration.timings.snapshot_validation_ms,
            "urban_access": iteration.timings.urban_access_ms,
            "topography": iteration.timings.topography_ms,
        },
    }


def _iteration_from_record(value: object) -> EAFixedPointIteration:
    if not isinstance(value, dict):
        raise ValueError("iteration record is invalid")
    sources = value["governed_source_identities"]
    timings = value["timings_ms"]
    if not isinstance(sources, dict) or not isinstance(timings, dict):
        raise ValueError("iteration evidence is invalid")
    iteration = value["iteration"]
    if type(iteration) is not int or iteration < 1:
        raise ValueError("iteration number is invalid")
    return EAFixedPointIteration(
        iteration=iteration,
        snapshot_id=value["snapshot_id"],
        snapshot_manifest_sha256=value["snapshot_manifest_sha256"],
        expected_fingerprint=value["expected_fingerprint"],
        actual_fingerprint=value["actual_fingerprint"],
        route_inventory=_route_inventory(tuple(value["route_inventory"])),
        governed_source_identities=_governed_source_identities(
            tuple(sources.items())
        ),
        timings=EAFixedPointTimings(
            acquisition_ms=timings["acquisition"],
            snapshot_seal_ms=timings["snapshot_seal"],
            snapshot_validation_ms=timings["snapshot_validation"],
            urban_access_ms=timings["urban_access"],
            topography_ms=timings["topography"],
        ),
    )
