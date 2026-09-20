"""Immutable workspace-local compiler artifacts and dependency validation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from satn.compilation_dependencies import is_compiler_cache_revision

ARTIFACT_MANIFEST_SCHEMA = "satn-artifact-manifest/v1"
COMPILATION_RUN_REPORT_SCHEMA = "satn-compilation-run/v1"
ARTIFACT_PIN_SCHEMA = "satn-artifact-pin/v1"
GC_TRANSACTION_SCHEMA = "satn-artifact-gc-transaction/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ROLE = re.compile(r"^[a-z][a-z0-9-]*$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class _FrozenObject:
    items: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class _FrozenArray:
    items: tuple[object, ...]


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require_sha256(value: str, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be one lowercase SHA-256 digest")
    return value


def _require_identity(value: str, label: str) -> str:
    """Validate a cache identity or the explicit compiler revision token."""

    if not isinstance(value, str) or (
        _SHA256.fullmatch(value) is None and not is_compiler_cache_revision(value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest or compiler revision")
    return value


def _require_name(value: str, label: str) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None or ".." in value:
        raise ValueError(f"{label} is invalid")
    return value


def _freeze_json(value: object, *, label: str) -> object:
    """Return an immutable canonical JSON value, rejecting operational objects."""

    if isinstance(value, (_FrozenObject, _FrozenArray)):
        return value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{label} object keys must be strings")
        return _FrozenObject(
            tuple((key, _freeze_json(item, label=label)) for key, item in sorted(value.items()))
        )
    if isinstance(value, (list, tuple)):
        return _FrozenArray(tuple(_freeze_json(item, label=label) for item in value))
    raise ValueError(f"{label} contains a non-JSON value")


def _thaw_json(value: object) -> object:
    if isinstance(value, _FrozenObject):
        return {key: _thaw_json(item) for key, item in value.items}
    if isinstance(value, _FrozenArray):
        return [_thaw_json(item) for item in value.items]
    return value


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sorted_unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must not contain duplicates")
    return tuple(sorted(values))


@dataclass(frozen=True)
class ArtifactSpecification:
    """Semantic inputs for one Retained Artifact before outputs are known."""

    kind: str
    contract_version: str
    implementation_fingerprint: str
    dependency_manifest_fingerprint: str
    parameters: object
    upstream_artifact_ids: tuple[str, ...]
    partition_identities: tuple[str, ...]
    coverage_identities: tuple[str, ...]
    validation_contract: str
    diagnostics: object
    status: str = "complete"

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _require_name(self.kind, "artifact kind"))
        object.__setattr__(
            self,
            "contract_version",
            _require_name(self.contract_version, "artifact contract version"),
        )
        object.__setattr__(
            self,
            "implementation_fingerprint",
            _require_identity(self.implementation_fingerprint, "implementation fingerprint"),
        )
        object.__setattr__(
            self,
            "dependency_manifest_fingerprint",
            _require_identity(
                self.dependency_manifest_fingerprint,
                "dependency manifest fingerprint",
            ),
        )
        upstream = _sorted_unique(self.upstream_artifact_ids, "upstream artifact IDs")
        for artifact_id in upstream:
            _require_sha256(artifact_id, "upstream artifact ID")
        object.__setattr__(self, "upstream_artifact_ids", upstream)
        object.__setattr__(
            self,
            "partition_identities",
            _sorted_unique(self.partition_identities, "partition identities"),
        )
        coverage = _sorted_unique(self.coverage_identities, "coverage identities")
        for identity in coverage:
            _require_sha256(identity, "coverage identity")
        object.__setattr__(self, "coverage_identities", coverage)
        object.__setattr__(
            self,
            "validation_contract",
            _require_name(self.validation_contract, "validation contract"),
        )
        if self.status not in {"complete", "complete-with-gaps"}:
            raise ValueError("artifact status is invalid")
        object.__setattr__(
            self,
            "parameters",
            _freeze_json(self.parameters, label="artifact parameters"),
        )
        object.__setattr__(
            self,
            "diagnostics",
            _freeze_json(self.diagnostics, label="artifact diagnostics"),
        )


@dataclass(frozen=True)
class ArtifactOutput:
    role: str
    sha256: str
    bytes: int

    def __post_init__(self) -> None:
        if _ROLE.fullmatch(self.role) is None:
            raise ValueError("artifact output role is invalid")
        _require_sha256(self.sha256, "artifact output fingerprint")
        if not isinstance(self.bytes, int) or isinstance(self.bytes, bool) or self.bytes < 0:
            raise ValueError("artifact output byte count is invalid")

    def payload(self) -> dict[str, object]:
        return {"role": self.role, "sha256": self.sha256, "bytes": self.bytes}


@dataclass(frozen=True)
class ArtifactManifest:
    artifact_id: str
    kind: str
    contract_version: str
    status: str
    implementation_fingerprint: str
    dependency_manifest_fingerprint: str
    parameters: object
    upstream_artifact_ids: tuple[str, ...]
    partition_identities: tuple[str, ...]
    coverage_identities: tuple[str, ...]
    outputs: tuple[ArtifactOutput, ...]
    validation_contract: str
    diagnostics: object

    @classmethod
    def create(
        cls,
        specification: ArtifactSpecification,
        outputs: tuple[ArtifactOutput, ...],
    ) -> ArtifactManifest:
        ordered_outputs = tuple(sorted(outputs, key=lambda output: output.role))
        if not ordered_outputs or len({output.role for output in ordered_outputs}) != len(
            ordered_outputs
        ):
            raise ValueError("artifact outputs must have unique roles")
        manifest = cls(
            artifact_id="0" * 64,
            kind=specification.kind,
            contract_version=specification.contract_version,
            status=specification.status,
            implementation_fingerprint=specification.implementation_fingerprint,
            dependency_manifest_fingerprint=(specification.dependency_manifest_fingerprint),
            parameters=specification.parameters,
            upstream_artifact_ids=specification.upstream_artifact_ids,
            partition_identities=specification.partition_identities,
            coverage_identities=specification.coverage_identities,
            outputs=ordered_outputs,
            validation_contract=specification.validation_contract,
            diagnostics=specification.diagnostics,
        )
        return cls(**{**manifest.__dict__, "artifact_id": _sha256_bytes(manifest.identity_bytes())})

    @classmethod
    def from_payload(cls, payload: object) -> ArtifactManifest:
        if not isinstance(payload, dict):
            raise ValueError("artifact manifest must be an object")
        expected = {
            "schema",
            "artifact_id",
            "kind",
            "contract_version",
            "status",
            "implementation_fingerprint",
            "dependency_manifest_fingerprint",
            "parameters",
            "upstream_artifact_ids",
            "partition_identities",
            "coverage_identities",
            "outputs",
            "validation_contract",
            "diagnostics",
        }
        if set(payload) != expected or payload.get("schema") != ARTIFACT_MANIFEST_SCHEMA:
            raise ValueError("artifact manifest contract is invalid")
        raw_outputs = payload.get("outputs")
        if not isinstance(raw_outputs, list):
            raise ValueError("artifact manifest outputs are invalid")
        outputs: list[ArtifactOutput] = []
        for item in raw_outputs:
            if not isinstance(item, dict) or set(item) != {"role", "sha256", "bytes"}:
                raise ValueError("artifact manifest output is invalid")
            outputs.append(ArtifactOutput(**item))
        specification = ArtifactSpecification(
            kind=payload["kind"],
            contract_version=payload["contract_version"],
            implementation_fingerprint=payload["implementation_fingerprint"],
            dependency_manifest_fingerprint=payload["dependency_manifest_fingerprint"],
            parameters=payload["parameters"],
            upstream_artifact_ids=tuple(payload["upstream_artifact_ids"]),
            partition_identities=tuple(payload["partition_identities"]),
            coverage_identities=tuple(payload["coverage_identities"]),
            validation_contract=payload["validation_contract"],
            diagnostics=payload["diagnostics"],
            status=payload["status"],
        )
        expected_manifest = cls.create(specification, tuple(outputs))
        if payload.get("artifact_id") != expected_manifest.artifact_id:
            raise ValueError("artifact manifest identity is invalid")
        return expected_manifest

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema": ARTIFACT_MANIFEST_SCHEMA,
            "kind": self.kind,
            "contract_version": self.contract_version,
            "status": self.status,
            "implementation_fingerprint": self.implementation_fingerprint,
            "dependency_manifest_fingerprint": self.dependency_manifest_fingerprint,
            "parameters": _thaw_json(self.parameters),
            "upstream_artifact_ids": list(self.upstream_artifact_ids),
            "partition_identities": list(self.partition_identities),
            "coverage_identities": list(self.coverage_identities),
            "outputs": [output.payload() for output in self.outputs],
            "validation_contract": self.validation_contract,
            "diagnostics": _thaw_json(self.diagnostics),
        }

    def identity_bytes(self) -> bytes:
        return _canonical_bytes(self.identity_payload())

    def payload(self) -> dict[str, object]:
        return {**self.identity_payload(), "artifact_id": self.artifact_id}

    def bytes(self) -> bytes:
        return _canonical_bytes(self.payload()) + b"\n"


@dataclass(frozen=True)
class RetainedArtifact:
    artifact_id: str
    manifest: ArtifactManifest
    path: Path

    def read_output(self, role: str) -> bytes:
        output = next((item for item in self.manifest.outputs if item.role == role), None)
        if output is None:
            raise KeyError(role)
        path = self.path / "outputs" / role
        content = path.read_bytes()
        if len(content) != output.bytes:
            raise ValueError(f"retained artifact output size differs: {role}")
        return content


@dataclass(frozen=True)
class ArtifactResolution:
    disposition: str
    reason: str
    artifact: RetainedArtifact | None = None
    quarantined_path: Path | None = None


class _ArtifactValidationError(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _manifest_matches_specification(
    manifest: ArtifactManifest,
    specification: ArtifactSpecification,
) -> bool:
    return (
        manifest.kind == specification.kind
        and manifest.contract_version == specification.contract_version
        and manifest.status == specification.status
        and manifest.implementation_fingerprint == specification.implementation_fingerprint
        and manifest.dependency_manifest_fingerprint
        == specification.dependency_manifest_fingerprint
        and manifest.parameters == specification.parameters
        and manifest.upstream_artifact_ids == specification.upstream_artifact_ids
        and manifest.partition_identities == specification.partition_identities
        and manifest.coverage_identities == specification.coverage_identities
        and manifest.validation_contract == specification.validation_contract
        and manifest.diagnostics == specification.diagnostics
    )


@dataclass(frozen=True)
class RunArtifactEvent:
    """One non-semantic observation of an artifact decision in a compile attempt."""

    kind: str
    scope: str
    disposition: str
    reason: str
    artifact_id: str | None
    elapsed_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _require_name(self.kind, "run artifact kind"))
        if not isinstance(self.scope, str) or not self.scope:
            raise ValueError("run artifact scope is invalid")
        if self.disposition not in {
            "hit",
            "build",
            "run",
            "done",
            "gap",
            "failed",
            "skipped",
        }:
            raise ValueError("run artifact disposition is invalid")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("run artifact reason is invalid")
        if self.artifact_id is not None:
            _require_sha256(self.artifact_id, "run artifact ID")
        if (
            not isinstance(self.elapsed_ms, int)
            or isinstance(self.elapsed_ms, bool)
            or self.elapsed_ms < 0
        ):
            raise ValueError("run artifact elapsed time is invalid")

    def payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "scope": self.scope,
            "disposition": self.disposition,
            "reason": self.reason,
            "artifact_id": self.artifact_id,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True)
class CompilationRunReport:
    """Immutable observation of one attempt; never part of artifact identity."""

    run_id: str
    area_definition: str
    mode: str
    result: str
    started_at: str
    finished_at: str
    workers: object
    artifact_events: tuple[RunArtifactEvent, ...]
    stitch: object
    publication: object
    peak_rss_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or _RUN_ID.fullmatch(self.run_id) is None:
            raise ValueError("compilation run ID is invalid")
        if not isinstance(self.area_definition, str) or not self.area_definition:
            raise ValueError("compilation run area definition is invalid")
        if self.mode not in {"incremental", "full", "targeted"}:
            raise ValueError("compilation run mode is invalid")
        if self.result not in {"complete", "complete-with-gaps", "failed"}:
            raise ValueError("compilation run result is invalid")
        started = _parse_timestamp(self.started_at, "run start")
        finished = _parse_timestamp(self.finished_at, "run finish")
        if finished < started:
            raise ValueError("compilation run finishes before it starts")
        if (
            not isinstance(self.peak_rss_bytes, int)
            or isinstance(self.peak_rss_bytes, bool)
            or self.peak_rss_bytes < 0
        ):
            raise ValueError("compilation run peak RSS is invalid")
        object.__setattr__(
            self,
            "workers",
            _freeze_json(self.workers, label="compilation run workers"),
        )
        object.__setattr__(
            self,
            "stitch",
            _freeze_json(self.stitch, label="compilation run stitch"),
        )
        object.__setattr__(
            self,
            "publication",
            _freeze_json(self.publication, label="compilation run publication"),
        )
        object.__setattr__(self, "artifact_events", tuple(self.artifact_events))
        if any(not isinstance(event, RunArtifactEvent) for event in self.artifact_events):
            raise ValueError("compilation run artifact events are invalid")

    def payload(self) -> dict[str, object]:
        return {
            "schema": COMPILATION_RUN_REPORT_SCHEMA,
            "run_id": self.run_id,
            "area_definition": self.area_definition,
            "mode": self.mode,
            "result": self.result,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "workers": _thaw_json(self.workers),
            "artifacts": [event.payload() for event in self.artifact_events],
            "stitch": _thaw_json(self.stitch),
            "publication": _thaw_json(self.publication),
            "peak_rss_bytes": self.peak_rss_bytes,
        }

    def bytes(self) -> bytes:
        return _canonical_bytes(self.payload()) + b"\n"

    @classmethod
    def from_payload(cls, payload: object) -> CompilationRunReport:
        if not isinstance(payload, dict):
            raise ValueError("compilation run report must be an object")
        expected = {
            "schema",
            "run_id",
            "area_definition",
            "mode",
            "result",
            "started_at",
            "finished_at",
            "workers",
            "artifacts",
            "stitch",
            "publication",
            "peak_rss_bytes",
        }
        if set(payload) != expected or payload.get("schema") != COMPILATION_RUN_REPORT_SCHEMA:
            raise ValueError("compilation run report contract is invalid")
        raw_events = payload.get("artifacts")
        if not isinstance(raw_events, list):
            raise ValueError("compilation run artifact events are invalid")
        events: list[RunArtifactEvent] = []
        event_fields = {
            "kind",
            "scope",
            "disposition",
            "reason",
            "artifact_id",
            "elapsed_ms",
        }
        for item in raw_events:
            if not isinstance(item, dict) or set(item) != event_fields:
                raise ValueError("compilation run artifact event is invalid")
            events.append(RunArtifactEvent(**item))
        return cls(
            run_id=payload["run_id"],
            area_definition=payload["area_definition"],
            mode=payload["mode"],
            result=payload["result"],
            started_at=payload["started_at"],
            finished_at=payload["finished_at"],
            workers=payload["workers"],
            artifact_events=tuple(events),
            stitch=payload["stitch"],
            publication=payload["publication"],
            peak_rss_bytes=payload["peak_rss_bytes"],
        )


@dataclass(frozen=True)
class GarbageCollectionCandidate:
    artifact_id: str
    bytes: int


@dataclass(frozen=True)
class GarbageCollectionPlan:
    reachable_artifact_ids: tuple[str, ...]
    candidates: tuple[GarbageCollectionCandidate, ...]
    grace_period_seconds: int
    planned_at: str


@dataclass(frozen=True)
class GarbageCollectionResult:
    removed_artifact_ids: tuple[str, ...]
    removed_bytes: int


class RetainedArtifactStore:
    """Content-addressed stage artifacts beneath one compiler workspace."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._recover_gc_transactions()

    @classmethod
    def in_workspace(cls, workspace: Path) -> RetainedArtifactStore:
        return cls(Path(workspace) / ".satn")

    @property
    def artifacts_root(self) -> Path:
        return self.root / "artifacts" / "sha256"

    @property
    def runs_root(self) -> Path:
        return self.root / "runs"

    @property
    def lineages_root(self) -> Path:
        return self.root / "lineages"

    def _artifact_path(self, artifact_id: str) -> Path:
        return self.artifacts_root / artifact_id[:2] / artifact_id

    def put(
        self,
        specification: ArtifactSpecification,
        *,
        outputs: Mapping[str, bytes],
    ) -> RetainedArtifact:
        output_content: dict[str, bytes] = {}
        output_records: list[ArtifactOutput] = []
        for role, value in sorted(outputs.items()):
            if _ROLE.fullmatch(role) is None:
                raise ValueError("artifact output role is invalid")
            if not isinstance(value, bytes):
                raise ValueError("artifact output must be bytes")
            output_content[role] = value
            output_records.append(
                ArtifactOutput(role=role, sha256=_sha256_bytes(value), bytes=len(value))
            )
        manifest = ArtifactManifest.create(specification, tuple(output_records))
        target = self._artifact_path(manifest.artifact_id)
        if target.exists():
            resolution = self.resolve(manifest.artifact_id)
            if resolution.artifact is not None:
                return resolution.artifact
            if target.exists():
                raise ValueError("existing retained artifact could not be quarantined")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{manifest.artifact_id}.tmp-{uuid.uuid4().hex}"
        temporary.mkdir()
        try:
            output_directory = temporary / "outputs"
            output_directory.mkdir()
            for role, content in output_content.items():
                _write_fsynced(output_directory / role, content)
            _fsync_directory(output_directory)
            _write_fsynced(temporary / "manifest.json", manifest.bytes())
            _fsync_directory(temporary)
            validated = self._validate_directory(temporary, manifest.artifact_id)
            try:
                temporary.rename(target)
            except FileExistsError:
                existing = self.resolve(manifest.artifact_id)
                if existing.artifact is None:
                    raise ValueError("concurrent retained artifact is invalid") from None
                return existing.artifact
            _fsync_directory(target.parent)
            return RetainedArtifact(manifest.artifact_id, validated, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def resolve(self, artifact_id: str) -> ArtifactResolution:
        if not isinstance(artifact_id, str) or _SHA256.fullmatch(artifact_id) is None:
            return ArtifactResolution("miss", "invalid-artifact-id")
        return self._resolve(artifact_id, visiting=set())

    def reject_semantic_artifact(self, artifact_id: str, *, reason: str) -> ArtifactResolution:
        """Quarantine bytes that passed storage checks but failed their public decoder."""

        if not isinstance(artifact_id, str) or _SHA256.fullmatch(artifact_id) is None:
            raise ValueError("semantic rejection artifact ID is invalid")
        if not isinstance(reason, str) or _ROLE.fullmatch(reason) is None:
            raise ValueError("semantic rejection reason is invalid")
        quarantined = self._quarantine(self._artifact_path(artifact_id), artifact_id, reason)
        return ArtifactResolution(
            "miss",
            reason,
            quarantined_path=quarantined,
        )

    def resolve_specification(
        self,
        specification: ArtifactSpecification,
    ) -> ArtifactResolution:
        """Resolve the unique validated output for semantic stage inputs."""

        if not isinstance(specification, ArtifactSpecification):
            raise ValueError("artifact specification is invalid")
        if not self.artifacts_root.exists():
            return ArtifactResolution("miss", "not-found")
        if self.artifacts_root.is_symlink() or not self.artifacts_root.is_dir():
            raise ValueError("retained artifact root is invalid")
        candidates: list[RetainedArtifact] = []
        failed_reason: str | None = None
        for prefix in sorted(self.artifacts_root.iterdir()):
            if prefix.is_symlink() or not prefix.is_dir():
                raise ValueError("retained artifact prefix is invalid")
            for path in sorted(prefix.iterdir()):
                artifact_id = path.name
                if _SHA256.fullmatch(artifact_id) is None or prefix.name != artifact_id[:2]:
                    continue
                if path.is_symlink() or not path.is_dir():
                    self.resolve(artifact_id)
                    continue
                try:
                    manifest = ArtifactManifest.from_payload(
                        json.loads((path / "manifest.json").read_text(encoding="utf-8"))
                    )
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    self.resolve(artifact_id)
                    continue
                if not _manifest_matches_specification(manifest, specification):
                    continue
                resolution = self.resolve(artifact_id)
                if resolution.artifact is None:
                    failed_reason = resolution.reason
                else:
                    candidates.append(resolution.artifact)
        if len(candidates) > 1:
            return ArtifactResolution("miss", "nondeterministic-output-candidates")
        if candidates:
            return ArtifactResolution(
                "validated-hit",
                "validated-dependency-closure",
                candidates[0],
            )
        return ArtifactResolution("miss", failed_reason or "not-found")

    def write_run_report(self, report: CompilationRunReport) -> Path:
        self.runs_root.mkdir(parents=True, exist_ok=True)
        destination = self.runs_root / f"{report.run_id}.json"
        if destination.exists():
            if self.read_run_report(report.run_id) != report:
                raise ValueError("compilation run report is immutable")
            return destination
        temporary = self.runs_root / f".{report.run_id}.tmp-{uuid.uuid4().hex}"
        try:
            _write_fsynced(temporary, report.bytes())
            restored = CompilationRunReport.from_payload(
                json.loads(temporary.read_text(encoding="utf-8"))
            )
            if restored != report:
                raise ValueError("compilation run report changed during serialization")
            try:
                temporary.rename(destination)
            except FileExistsError:
                if self.read_run_report(report.run_id) != report:
                    raise ValueError("concurrent compilation run report differs") from None
            _fsync_directory(self.runs_root)
            return destination
        finally:
            temporary.unlink(missing_ok=True)

    def read_run_report(self, run_id: str) -> CompilationRunReport:
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise ValueError("compilation run ID is invalid")
        path = self.runs_root / f"{run_id}.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("compilation run report is unavailable")
        try:
            return CompilationRunReport.from_payload(json.loads(path.read_text(encoding="utf-8")))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("compilation run report is invalid") from error

    def pin(self, reference_kind: str, reference_id: str, artifact_id: str) -> Path:
        if reference_kind not in {
            "publication",
            "scenario-compilation",
            "active-lineage",
        }:
            raise ValueError("artifact pin kind is invalid")
        if not isinstance(reference_id, str) or _RUN_ID.fullmatch(reference_id) is None:
            raise ValueError("artifact pin reference ID is invalid")
        _require_sha256(artifact_id, "artifact pin ID")
        resolution = self.resolve(artifact_id)
        if resolution.artifact is None:
            raise ValueError(f"artifact pin target is unavailable: {resolution.reason}")
        directory = self.lineages_root / reference_kind
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{reference_id}.json"
        payload = {
            "schema": ARTIFACT_PIN_SCHEMA,
            "reference_kind": reference_kind,
            "reference_id": reference_id,
            "artifact_id": artifact_id,
        }
        _atomic_write(destination, _canonical_bytes(payload) + b"\n")
        return destination

    def resolve_active_lineage(self, reference_id: str) -> ArtifactResolution:
        """Resolve one exact active-lineage pin and its complete artifact closure.

        Active-lineage references are invocation-independent semantic lookup
        keys (normally a full input fingerprint).  A malformed or ambiguous
        pin is a miss; callers must fall back to governed inputs rather than
        guessing from another lineage or from the newest artifact on disk.
        """

        if not isinstance(reference_id, str) or _RUN_ID.fullmatch(reference_id) is None:
            return ArtifactResolution("miss", "invalid-active-lineage-id")
        if self.lineages_root.exists() and (
            self.lineages_root.is_symlink() or not self.lineages_root.is_dir()
        ):
            return ArtifactResolution("miss", "active-lineage-pin-invalid")
        active_root = self.lineages_root / "active-lineage"
        if active_root.exists() and (active_root.is_symlink() or not active_root.is_dir()):
            return ArtifactResolution("miss", "active-lineage-pin-invalid")
        path = active_root / f"{reference_id}.json"
        if not path.exists() and not path.is_symlink():
            return ArtifactResolution("miss", "active-lineage-not-found")
        if path.is_symlink() or not path.is_file():
            return ArtifactResolution("miss", "active-lineage-pin-invalid")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return ArtifactResolution("miss", "active-lineage-pin-invalid")
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema", "reference_kind", "reference_id", "artifact_id"}
            or payload.get("schema") != ARTIFACT_PIN_SCHEMA
            or payload.get("reference_kind") != "active-lineage"
            or payload.get("reference_id") != reference_id
            or path.parent.name != "active-lineage"
            or path.stem != reference_id
        ):
            return ArtifactResolution("miss", "active-lineage-pin-invalid")
        artifact_id = payload.get("artifact_id")
        if not isinstance(artifact_id, str) or _SHA256.fullmatch(artifact_id) is None:
            return ArtifactResolution("miss", "active-lineage-pin-invalid")
        resolution = self.resolve(artifact_id)
        if resolution.artifact is None:
            return ArtifactResolution(
                "miss",
                f"active-lineage-{resolution.reason}",
                quarantined_path=resolution.quarantined_path,
            )
        return ArtifactResolution("validated-hit", "validated-active-lineage", resolution.artifact)

    def plan_garbage_collection(
        self,
        *,
        grace_period: timedelta,
        now: datetime | None = None,
    ) -> GarbageCollectionPlan:
        if grace_period <= timedelta(0):
            raise ValueError("garbage collection grace period must be positive")
        planned_at = now or datetime.now(UTC)
        if planned_at.tzinfo is None or planned_at.utcoffset() is None:
            raise ValueError("garbage collection time must be timezone-aware")
        roots = self._pinned_artifact_ids()
        reachable: set[str] = set()

        def visit(artifact_id: str) -> None:
            if artifact_id in reachable:
                return
            path = self._artifact_path(artifact_id)
            try:
                manifest = self._validate_directory(path, artifact_id)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(f"pinned artifact dependency is invalid: {artifact_id}") from error
            reachable.add(artifact_id)
            for upstream_id in manifest.upstream_artifact_ids:
                visit(upstream_id)

        for root in roots:
            visit(root)
        cutoff = planned_at.timestamp() - grace_period.total_seconds()
        candidates: list[GarbageCollectionCandidate] = []
        if self.artifacts_root.is_dir():
            for prefix in sorted(self.artifacts_root.iterdir()):
                if prefix.is_symlink() or not prefix.is_dir():
                    continue
                for path in sorted(prefix.iterdir()):
                    artifact_id = path.name
                    if (
                        artifact_id in reachable
                        or _SHA256.fullmatch(artifact_id) is None
                        or prefix.name != artifact_id[:2]
                        or path.is_symlink()
                        or not path.is_dir()
                        or path.stat().st_mtime > cutoff
                    ):
                        continue
                    try:
                        self._validate_directory(path, artifact_id)
                    except (OSError, TypeError, ValueError, json.JSONDecodeError):
                        # GC is conservative. Corruption is handled by resolve/quarantine,
                        # never silently classified as disposable here.
                        continue
                    candidates.append(
                        GarbageCollectionCandidate(
                            artifact_id=artifact_id,
                            bytes=sum(
                                item.stat().st_size
                                for item in path.rglob("*")
                                if item.is_file() and not item.is_symlink()
                            ),
                        )
                    )
        return GarbageCollectionPlan(
            reachable_artifact_ids=tuple(sorted(reachable)),
            candidates=tuple(sorted(candidates, key=lambda item: item.artifact_id)),
            grace_period_seconds=int(grace_period.total_seconds()),
            planned_at=planned_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        )

    def collect_garbage(
        self,
        plan: GarbageCollectionPlan,
        *,
        confirm: bool,
    ) -> GarbageCollectionResult:
        if confirm is not True:
            raise ValueError("garbage collection requires explicit confirmation")
        if not isinstance(plan, GarbageCollectionPlan):
            raise ValueError("garbage collection plan is invalid")
        current = self.plan_garbage_collection(
            grace_period=timedelta(seconds=plan.grace_period_seconds),
            now=_parse_timestamp(plan.planned_at, "garbage collection plan"),
        )
        if current != plan:
            raise ValueError("garbage collection plan is stale")
        if not plan.candidates:
            return GarbageCollectionResult((), 0)
        staging_root = self.root / "gc-staging" / uuid.uuid4().hex
        staging_root.mkdir(parents=True)
        moved: list[tuple[Path, Path]] = []
        transaction_path = staging_root / "transaction.json"
        transaction = {
            "schema": GC_TRANSACTION_SCHEMA,
            "state": "moving",
            "artifact_ids": [candidate.artifact_id for candidate in plan.candidates],
        }
        _write_fsynced(transaction_path, _canonical_bytes(transaction) + b"\n")
        _fsync_directory(staging_root)
        try:
            for candidate in plan.candidates:
                source = self._artifact_path(candidate.artifact_id)
                destination = staging_root / candidate.artifact_id
                if source.is_symlink() or not source.is_dir():
                    raise ValueError("garbage collection candidate changed")
                source.rename(destination)
                moved.append((source, destination))
                _fsync_directory(source.parent)
            removed = tuple(candidate.artifact_id for candidate in plan.candidates)
            removed_bytes = sum(candidate.bytes for candidate in plan.candidates)
            transaction["state"] = "committed"
            _atomic_write(transaction_path, _canonical_bytes(transaction) + b"\n")
            shutil.rmtree(staging_root)
            _remove_empty_directory(staging_root.parent)
            return GarbageCollectionResult(removed, removed_bytes)
        except Exception:
            for source, destination in reversed(moved):
                if destination.exists() and not source.exists():
                    destination.rename(source)
            shutil.rmtree(staging_root)
            _remove_empty_directory(staging_root.parent)
            raise

    def _recover_gc_transactions(self) -> None:
        root = self.root / "gc-staging"
        if not root.exists():
            return
        if root.is_symlink() or not root.is_dir():
            raise ValueError("garbage collection staging root is invalid")
        for transaction_root in sorted(root.iterdir()):
            if transaction_root.is_symlink() or not transaction_root.is_dir():
                raise ValueError("garbage collection transaction is invalid")
            transaction_path = transaction_root / "transaction.json"
            try:
                payload = json.loads(transaction_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError("garbage collection transaction is invalid") from error
            if (
                not isinstance(payload, dict)
                or set(payload) != {"schema", "state", "artifact_ids"}
                or payload.get("schema") != GC_TRANSACTION_SCHEMA
                or payload.get("state") not in {"moving", "committed"}
                or not isinstance(payload.get("artifact_ids"), list)
            ):
                raise ValueError("garbage collection transaction contract is invalid")
            artifact_ids = payload["artifact_ids"]
            if len(set(artifact_ids)) != len(artifact_ids):
                raise ValueError("garbage collection transaction IDs are invalid")
            for artifact_id in artifact_ids:
                if not isinstance(artifact_id, str):
                    raise ValueError("garbage collection transaction ID is invalid")
                _require_sha256(artifact_id, "garbage collection transaction ID")
            expected_entries = {"transaction.json", *artifact_ids}
            if any(item.name not in expected_entries for item in transaction_root.iterdir()):
                raise ValueError("garbage collection transaction has unexpected entries")
            for artifact_id in artifact_ids:
                staged = transaction_root / artifact_id
                if (staged.exists() or staged.is_symlink()) and (
                    staged.is_symlink() or not staged.is_dir()
                ):
                    raise ValueError("garbage collection staged artifact is invalid")
            if payload["state"] == "moving":
                for artifact_id in artifact_ids:
                    staged = transaction_root / artifact_id
                    source = self._artifact_path(artifact_id)
                    if staged.exists() or staged.is_symlink():
                        try:
                            self._validate_directory(staged, artifact_id)
                        except (
                            OSError,
                            TypeError,
                            ValueError,
                            json.JSONDecodeError,
                        ) as error:
                            raise ValueError(
                                "garbage collection staged artifact is invalid"
                            ) from error
                        if source.exists() or source.is_symlink():
                            raise ValueError("garbage collection rollback target already exists")
                        source.parent.mkdir(parents=True, exist_ok=True)
                        staged.rename(source)
                        _fsync_directory(source.parent)
                    else:
                        try:
                            self._validate_directory(source, artifact_id)
                        except (
                            OSError,
                            TypeError,
                            ValueError,
                            json.JSONDecodeError,
                        ) as error:
                            raise ValueError(
                                "garbage collection rollback artifact is missing or invalid"
                            ) from error
            shutil.rmtree(transaction_root)
        _remove_empty_directory(root)

    def _pinned_artifact_ids(self) -> tuple[str, ...]:
        if not self.lineages_root.exists():
            return ()
        artifact_ids: set[str] = set()
        for path in sorted(self.lineages_root.glob("*/*.json")):
            if path.is_symlink() or not path.is_file():
                raise ValueError("artifact pin is invalid")
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError("artifact pin is invalid") from error
            if (
                not isinstance(payload, dict)
                or set(payload) != {"schema", "reference_kind", "reference_id", "artifact_id"}
                or payload.get("schema") != ARTIFACT_PIN_SCHEMA
                or path.parent.name != payload.get("reference_kind")
                or path.stem != payload.get("reference_id")
            ):
                raise ValueError("artifact pin contract is invalid")
            artifact_id = payload.get("artifact_id")
            if not isinstance(artifact_id, str):
                raise ValueError("artifact pin target is invalid")
            _require_sha256(artifact_id, "artifact pin target")
            artifact_ids.add(artifact_id)
        return tuple(sorted(artifact_ids))

    def _resolve(self, artifact_id: str, *, visiting: set[str]) -> ArtifactResolution:
        if artifact_id in visiting:
            return ArtifactResolution("miss", "dependency-cycle")
        path = self._artifact_path(artifact_id)
        if not path.exists() and not path.is_symlink():
            return ArtifactResolution("miss", "not-found")
        if path.is_symlink() or not path.is_dir():
            quarantined = self._quarantine(path, artifact_id, "structure-invalid")
            return ArtifactResolution(
                "miss",
                "structure-invalid",
                quarantined_path=quarantined,
            )
        try:
            manifest = self._validate_directory(path, artifact_id)
        except _ArtifactValidationError as error:
            quarantined = self._quarantine(path, artifact_id, error.reason)
            return ArtifactResolution(
                "miss",
                error.reason,
                quarantined_path=quarantined,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            quarantined = self._quarantine(path, artifact_id, "manifest-invalid")
            return ArtifactResolution(
                "miss",
                "manifest-invalid",
                quarantined_path=quarantined,
            )
        visiting.add(artifact_id)
        try:
            for upstream_id in manifest.upstream_artifact_ids:
                upstream = self._resolve(upstream_id, visiting=visiting)
                if upstream.artifact is None:
                    return ArtifactResolution(
                        "miss",
                        f"upstream-{upstream.reason}",
                    )
        finally:
            visiting.remove(artifact_id)
        return ArtifactResolution(
            "validated-hit",
            "validated-dependency-closure",
            RetainedArtifact(artifact_id, manifest, path),
        )

    def _quarantine(self, path: Path, artifact_id: str, reason: str) -> Path | None:
        if not path.exists() and not path.is_symlink():
            return None
        quarantine_root = self.root / "quarantine"
        quarantine_root.mkdir(parents=True, exist_ok=True)
        destination = quarantine_root / f"{artifact_id}-{reason}-{uuid.uuid4().hex}"
        try:
            path.rename(destination)
        except OSError:
            return None
        _fsync_directory(quarantine_root)
        return destination

    def _validate_directory(self, path: Path, artifact_id: str) -> ArtifactManifest:
        if path.is_symlink() or not path.is_dir():
            raise _ArtifactValidationError(
                "structure-invalid", "retained artifact directory is invalid"
            )
        manifest_path = path / "manifest.json"
        output_directory = path / "outputs"
        if (
            manifest_path.is_symlink()
            or not manifest_path.is_file()
            or output_directory.is_symlink()
            or not output_directory.is_dir()
        ):
            raise _ArtifactValidationError(
                "structure-invalid", "retained artifact structure is invalid"
            )
        if {item.name for item in path.iterdir()} != {"manifest.json", "outputs"}:
            raise _ArtifactValidationError(
                "unexpected-entries", "retained artifact has unexpected entries"
            )
        try:
            manifest = ArtifactManifest.from_payload(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise _ArtifactValidationError(
                "manifest-invalid", "retained artifact manifest is invalid"
            ) from error
        if manifest.artifact_id != artifact_id:
            raise _ArtifactValidationError(
                "artifact-id-mismatch", "retained artifact path identity differs"
            )
        expected_roles = {output.role for output in manifest.outputs}
        actual_roles = {item.name for item in output_directory.iterdir()}
        if actual_roles != expected_roles:
            raise _ArtifactValidationError(
                "output-roster-mismatch", "retained artifact output roster differs"
            )
        for output in manifest.outputs:
            output_path = output_directory / output.role
            if output_path.is_symlink() or not output_path.is_file():
                raise _ArtifactValidationError(
                    "output-invalid", "retained artifact output is invalid"
                )
            if output_path.stat().st_size != output.bytes:
                raise _ArtifactValidationError(
                    "output-size-mismatch",
                    "retained artifact output size differs",
                )
        return manifest


def _write_fsynced(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
    try:
        _write_fsynced(temporary, content)
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_empty_directory(path: Path) -> None:
    with suppress(OSError):
        path.rmdir()


def _parse_timestamp(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} timestamp must use UTC Z notation")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} timestamp is invalid") from error
    if parsed.utcoffset() is None:
        raise ValueError(f"{label} timestamp is not timezone-aware")
    return parsed
