"""Small durable, content-addressed history for local planning runs.

The store deliberately knows nothing about the planning engine.  States,
operations, requests, receipts, and events are JSON records addressed by
SHA-256.  Branch heads are the only mutable records; all semantic records are
immutable once published.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

type JSONValue = bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"] | None

HISTORY_RECORD_SCHEMA = "satn-planning-history-record/v1"
HISTORY_HEAD_SCHEMA = "satn-planning-history-head/v1"
HISTORY_BRANCH_SCHEMA = "satn-planning-history-branch/v1"
HISTORY_EVENT_KIND = "event"
HISTORY_CHECKPOINT_KIND = "checkpoint"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_KIND = re.compile(r"^[a-z][a-z0-9._/-]*$")
_UNSET = object()
_STATE_CONTEXT_REF = "_planning_state_context_ref"
_STATE_DELTA = "_planning_state_delta"
_STATE_SHARED_FIELDS = ("source_corridors", "places", "obligations", "candidates")


class HistoryError(Exception):
    """Base class for durable-history failures."""

    code = "history_error"


class HistoryMissingError(HistoryError):
    """A requested immutable record or branch is unavailable."""

    code = "missing"


class HistoryCorruptError(HistoryError):
    """A record exists but fails its schema, hash, or dependency contract."""

    code = "corrupt"


class HistoryStaleHeadError(HistoryError):
    """The caller's expected branch head was replaced by another writer."""

    code = "concurrent_head_conflict"

    def __init__(
        self,
        branch_id: str,
        expected: str | None,
        actual: str | None,
        revision: int,
    ) -> None:
        self.branch_id = branch_id
        self.expected_head = expected
        self.actual_head = actual
        self.revision = revision
        super().__init__(
            f"branch {branch_id!r} head is stale: expected {expected!r}, "
            f"actual {actual!r} at revision {revision}"
        )


class HistoryReplayError(HistoryError):
    """The supplied deterministic reducer did not reproduce a recorded state."""

    code = "replay_failed"


@dataclass(frozen=True)
class Branch:
    branch_id: str
    parent_branch_id: str | None = None
    fork_checkpoint_id: str | None = None
    base_state_ref: str | None = None
    base_history_event_id: str | None = None
    status: str = "active"

    @property
    def name(self) -> str:
        return self.branch_id

    def as_dict(self) -> dict[str, JSONValue]:
        return {
            "branch_id": self.branch_id,
            "parent_branch_id": self.parent_branch_id,
            "fork_checkpoint_id": self.fork_checkpoint_id,
            "base_state_ref": self.base_state_ref,
            "base_history_event_id": self.base_history_event_id,
            "status": self.status,
        }


@dataclass(frozen=True)
class BranchHead:
    branch_id: str
    head_event_id: str | None
    revision: int

    @property
    def event_id(self) -> str | None:
        return self.head_event_id

    def as_dict(self) -> dict[str, JSONValue]:
        return {
            "branch_id": self.branch_id,
            "head_event_id": self.head_event_id,
            "revision": self.revision,
        }


def _validate_json(value: object, *, label: str = "value") -> JSONValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{label} object keys must be strings")
            result[key] = _validate_json(item, label=label)
        return result
    if isinstance(value, (list, tuple)):
        return [_validate_json(item, label=label) for item in value]
    raise ValueError(f"{label} contains a non-JSON value")


def _canonical_bytes(value: object) -> bytes:
    normalized = _validate_json(value)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _record_digest(kind: str, payload: object) -> str:
    return hashlib.sha256(_canonical_bytes({"kind": kind, "payload": payload})).hexdigest()


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_name(value: object, label: str) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _require_kind(value: object) -> str:
    if not isinstance(value, str) or _KIND.fullmatch(value) is None:
        raise ValueError("record kind is invalid")
    return value


def _unique_refs(values: object, label: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{label} must be a list of record digests")
    refs = [_require_digest(item, label) for item in values]
    if len(set(refs)) != len(refs):
        raise ValueError(f"{label} must not contain duplicates")
    return sorted(refs)


def _copy_json(value: object) -> JSONValue:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover - platform-specific directory support.
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class HistoryStore:
    """Filesystem-local content-addressed planning history."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.records_root = self.root / "records"
        self.artifacts_root = self.root / "artifacts"
        self.branches_root = self.root / "branches"
        self.locks_root = self.root / "locks"
        self._thread_lock = threading.RLock()
        # A read cache is scoped to one verification/replay operation.  It is
        # intentionally discarded afterwards so a later call always observes
        # tampering or replacement of an immutable object.
        self._record_read_cache: ContextVar[dict[tuple[str, str], object] | None] = ContextVar(
            "planning_history_record_read_cache", default=None
        )

    @contextmanager
    def _read_cache_scope(self):
        existing = self._record_read_cache.get()
        if existing is not None:
            # Nested store operations (for example a runtime replay calling
            # the store replay primitive) share the same per-operation cache.
            yield
            return
        token = self._record_read_cache.set({})
        try:
            yield
        finally:
            self._record_read_cache.reset(token)

    # Public paths make corruption and crash tests inspectable without coupling
    # callers to the directory layout.
    def record_path(self, record_id: str) -> Path:
        _require_digest(record_id, "record ID")
        return self.records_root / record_id[:2] / f"{record_id}.json"

    def artifact_path(self, artifact_id: str) -> Path:
        _require_digest(artifact_id, "artifact ID")
        return self.artifacts_root / artifact_id[:2] / artifact_id

    def branch_path(self, branch_id: str) -> Path:
        _require_name(branch_id, "branch ID")
        return self.branches_root / f"{branch_id}.json"

    def head_path(self, branch_id: str) -> Path:
        _require_name(branch_id, "branch ID")
        return self.branches_root / f"{branch_id}.head.json"

    def put(self, value: object, *, kind: str = "record") -> str:
        """Materialise one immutable JSON value and return its content ID."""

        kind = _require_kind(kind)
        payload = _validate_json(value, label=f"{kind} payload")
        record_id = _record_digest(kind, payload)
        destination = self.record_path(record_id)
        if destination.exists() or destination.is_symlink():
            existing_kind, existing = self._load_record(record_id)
            if existing_kind != kind or existing != payload:
                raise HistoryCorruptError(f"immutable record {record_id} changed")
            return record_id
        envelope = {
            "schema": HISTORY_RECORD_SCHEMA,
            "record_digest": record_id,
            "kind": kind,
            "payload": payload,
        }
        self._atomic_write(destination, _canonical_bytes(envelope) + b"\n")
        # Validate after publication.  A failure here leaves an immutable
        # orphan that remains diagnosable and can never be referenced as valid.
        self._load_record(record_id)
        return record_id

    def put_state(self, state: Mapping[str, object], *, problem_ref: str) -> str:
        """Store a planning state with its admitted problem facts shared by reference."""

        if not isinstance(state, Mapping):
            raise ValueError("planning state must be an object")
        problem_ref = _require_digest(problem_ref, "planning problem ref")
        problem_kind, problem = self._load_record(problem_ref)
        if problem_kind != "planning-problem" or not isinstance(problem, Mapping):
            raise HistoryCorruptError("planning problem ref does not identify a planning problem")

        payload = _validate_json(state, label="state payload")
        if not isinstance(payload, dict):
            raise ValueError("planning state must be an object")
        for field in _STATE_SHARED_FIELDS:
            if field not in payload or field not in problem:
                raise ValueError(f"planning state context is missing {field}")
            if _canonical_bytes(payload[field]) != _canonical_bytes(problem[field]):
                raise ValueError(f"planning state {field} does not match its problem")
        if payload.get("parent_problem_id") != problem.get("problem_id"):
            raise ValueError("planning state parent problem does not match its problem")
        if payload.get("problem_fingerprint") != problem.get("input_fingerprint"):
            raise ValueError("planning state problem fingerprint does not match its problem")
        if payload.get("brief_fingerprint") != problem.get("brief_fingerprint"):
            raise ValueError("planning state brief fingerprint does not match its problem")
        if _canonical_bytes(payload.get("brief")) != _canonical_bytes(problem.get("brief")):
            raise ValueError("planning state brief does not match its problem")

        record_id = _record_digest("state", payload)
        destination = self.record_path(record_id)
        if destination.exists() or destination.is_symlink():
            existing_kind, existing = self._load_record(record_id)
            if existing_kind != "state" or existing != payload:
                raise HistoryCorruptError(f"immutable record {record_id} changed")
            return record_id
        compact_payload = {
            _STATE_CONTEXT_REF: problem_ref,
            _STATE_DELTA: {
                key: value for key, value in payload.items() if key not in _STATE_SHARED_FIELDS
            },
        }
        envelope = {
            "schema": HISTORY_RECORD_SCHEMA,
            "record_digest": record_id,
            "kind": "state",
            "payload": compact_payload,
        }
        self._atomic_write(destination, _canonical_bytes(envelope) + b"\n")
        self._load_record(record_id)
        return record_id

    def get(self, record_id: str) -> JSONValue:
        """Read and verify an immutable JSON record."""

        # Cached reads share the validated in-memory value internally, but a
        # public caller receives its own JSON tree so a reducer cannot mutate
        # a later replay step through an alias.
        return _copy_json(self._load_record(_require_digest(record_id, "record ID"))[1])

    def get_record(self, record_id: str) -> tuple[str, JSONValue]:
        """Return a record's kind and payload after integrity validation."""

        kind, payload = self._load_record(_require_digest(record_id, "record ID"))
        return kind, _copy_json(payload)

    def put_artifact(self, content: bytes, *, metadata: object | None = None) -> str:
        """Materialise immutable bytes addressed by their SHA-256 digest."""

        if not isinstance(content, bytes):
            raise ValueError("artifact content must be bytes")
        if metadata is not None:
            _validate_json(metadata, label="artifact metadata")
        artifact_id = hashlib.sha256(content).hexdigest()
        destination = self.artifact_path(artifact_id)
        if destination.exists() or destination.is_symlink():
            existing = self._read_artifact(artifact_id)
            if existing != content:
                raise HistoryCorruptError(f"immutable artifact {artifact_id} changed")
            return artifact_id
        self._atomic_write(destination, content)
        if metadata is not None:
            self._atomic_write(
                destination.with_name(destination.name + ".metadata.json"),
                _canonical_bytes({"artifact_id": artifact_id, "metadata": metadata}) + b"\n",
            )
        self._read_artifact(artifact_id)
        return artifact_id

    def get_artifact(self, artifact_id: str) -> bytes:
        return self._read_artifact(_require_digest(artifact_id, "artifact ID"))

    def create_branch(
        self,
        name: str = "main",
        *,
        parent_branch_id: str | None = None,
        fork_checkpoint_id: str | None = None,
        base_state_ref: str | None = None,
        base_history_event_id: str | None = None,
    ) -> Branch:
        """Create a stable branch locator without overwriting an existing one."""

        name = _require_name(name, "branch ID")
        with self._branch_lock(name):
            return self._create_branch_unlocked(
                name,
                parent_branch_id=parent_branch_id,
                fork_checkpoint_id=fork_checkpoint_id,
                base_state_ref=base_state_ref,
                base_history_event_id=base_history_event_id,
            )

    def branch(self, branch_id: str) -> Branch:
        return self._load_branch(_require_name(branch_id, "branch ID"))

    def head(self, branch_id: str = "main") -> BranchHead:
        branch_id = _require_name(branch_id, "branch ID")
        self._load_branch(branch_id)
        return self._load_head(branch_id)

    def commit(
        self,
        branch_id: str,
        expected_head: str | BranchHead | None,
        event: Mapping[str, object],
    ) -> str:
        """Append one verified event using an optimistic expected-head check."""

        branch_id = _require_name(branch_id, "branch ID")
        if isinstance(expected_head, BranchHead):
            expected = expected_head.head_event_id
        elif expected_head is None or isinstance(expected_head, str):
            expected = expected_head
        else:
            raise ValueError("expected head is invalid")
        if expected is not None:
            _require_digest(expected, "expected head")
        if not isinstance(event, Mapping):
            raise ValueError("history event must be an object")

        with self._branch_lock(branch_id):
            branch = self._load_branch(branch_id)
            current = self._load_head(branch_id)
            if expected != current.head_event_id:
                raise HistoryStaleHeadError(
                    branch_id,
                    expected,
                    current.head_event_id,
                    current.revision,
                )
            prepared = self._prepare_event(branch, current, event)
            event_id = self.put(prepared, kind=HISTORY_EVENT_KIND)
            self._write_checkpoint_for_event(event_id, prepared)
            self._write_head(
                BranchHead(branch_id, event_id, current.revision + 1),
            )
            return event_id

    def checkpoint(self, event_id: str) -> str:
        """Return the stable pre-decision checkpoint for an event."""

        event_id = _require_digest(event_id, "event ID")
        kind, event = self._get_event(event_id)
        del kind
        return self._write_checkpoint_for_event(event_id, event)

    def fork(self, checkpoint_id: str, branch_id: str | None = None) -> Branch:
        """Create a child branch at a checkpoint's pre-decision state."""

        checkpoint_id = _require_digest(checkpoint_id, "checkpoint ID")
        checkpoint = self._get_checkpoint_or_event(checkpoint_id)
        if branch_id is None:
            branch_id = f"fork-{checkpoint_id[:12]}"
        branch_id = _require_name(branch_id, "branch ID")
        parent = checkpoint["branch_id"]
        if not isinstance(parent, str):
            raise HistoryCorruptError("checkpoint branch ID is invalid")
        with self._branch_lock(parent):
            self._verify_checkpoint_payload(checkpoint)
            with self._branch_lock(branch_id):
                return self._create_branch_unlocked(
                    branch_id,
                    parent_branch_id=parent,
                    fork_checkpoint_id=checkpoint_id,
                    base_state_ref=checkpoint.get("pre_state_ref"),
                    base_history_event_id=checkpoint.get("pre_history_root"),
                )

    def advance(
        self,
        branch_id: str,
        replacement: Mapping[str, object],
        *,
        expected_head: str | object | None = _UNSET,
    ) -> str:
        """Append a replacement decision after deterministic prefix replay."""

        branch_id = _require_name(branch_id, "branch ID")
        if not isinstance(replacement, Mapping):
            raise ValueError("replacement event must be an object")
        if expected_head is _UNSET:
            expected: str | None = self.head(branch_id).head_event_id
        elif expected_head is None or isinstance(expected_head, str):
            expected = expected_head
        else:
            raise ValueError("expected head is invalid")
        event = dict(replacement)
        event.setdefault("replacement", True)
        return self.commit(branch_id, expected, event)

    def restore(self, checkpoint_id: str) -> dict[str, JSONValue]:
        """Restore a verified checkpoint without writing or advancing a branch."""

        checkpoint_id = _require_digest(checkpoint_id, "checkpoint ID")
        checkpoint = self._get_checkpoint(checkpoint_id)
        self._verify_checkpoint_payload(checkpoint)
        state_ref = checkpoint.get("pre_state_ref")
        state = self.get(state_ref) if isinstance(state_ref, str) else None
        return {
            "checkpoint_id": checkpoint_id,
            "branch_id": checkpoint["branch_id"],
            "event_id": checkpoint["event_id"],
            "state_ref": state_ref,
            "state": state,
            "history_root": checkpoint.get("pre_history_root"),
            "dependency_manifest_ref": checkpoint.get("dependency_manifest_ref"),
        }

    def verify(self, target_id: str) -> dict[str, JSONValue]:
        """Verify a branch, checkpoint, event, record, or materialised artifact."""

        with self._read_cache_scope():
            return self._verify_uncached(target_id)

    def _verify_uncached(self, target_id: str) -> dict[str, JSONValue]:

        if not isinstance(target_id, str) or not target_id:
            raise ValueError("history target ID is invalid")
        if _NAME.fullmatch(target_id) and self.branch_path(target_id).exists():
            branch = self._load_branch(target_id)
            head = self._load_head(target_id)
            events = self._verify_branch(branch, head)
            return {
                "target_id": target_id,
                "target_kind": "branch",
                "branch_id": target_id,
                "head_event_id": head.head_event_id,
                "revision": head.revision,
                "verified_records": events,
                "valid": True,
            }
        if _SHA256.fullmatch(target_id) is None:
            raise HistoryMissingError(f"history target is unavailable: {target_id}")
        if self.record_path(target_id).exists() or self.record_path(target_id).is_symlink():
            kind, payload = self._load_record(target_id)
            if kind == HISTORY_EVENT_KIND:
                records = self._verify_event_chain(target_id)
            elif kind == HISTORY_CHECKPOINT_KIND:
                self._verify_checkpoint_payload(payload)
                records = [target_id]
            else:
                records = self._verify_record_closure(target_id)
            return {
                "target_id": target_id,
                "target_kind": kind,
                "verified_records": records,
                "valid": True,
            }
        if self.artifact_path(target_id).exists() or self.artifact_path(target_id).is_symlink():
            self.get_artifact(target_id)
            return {
                "target_id": target_id,
                "target_kind": "artifact",
                "verified_records": [target_id],
                "valid": True,
            }
        raise HistoryMissingError(f"history target is unavailable: {target_id}")

    def replay(
        self,
        branch_id: str,
        reducer: Callable[..., object],
        target_event_id: str | None = None,
    ) -> dict[str, JSONValue]:
        """Replay recorded operations through a caller-supplied pure reducer."""

        with self._read_cache_scope():
            return self._replay_uncached(branch_id, reducer, target_event_id)

    def _replay_uncached(
        self,
        branch_id: str,
        reducer: Callable[..., object],
        target_event_id: str | None = None,
    ) -> dict[str, JSONValue]:

        branch_id = _require_name(branch_id, "branch ID")
        if not callable(reducer):
            raise TypeError("replay reducer must be callable")
        branch = self._load_branch(branch_id)
        head = self._load_head(branch_id)
        target = target_event_id or head.head_event_id
        if target is None:
            return {
                "branch_id": branch_id,
                "target_event_id": None,
                "state": None,
                "state_ref": None,
                "events": [],
                "diagnostics": [],
                "head_event_id": head.head_event_id,
                "advanced": False,
            }
        target = _require_digest(target, "target event ID")
        events = self._verify_event_chain(target)
        event_ids = [record_id for record_id in events]
        if head.head_event_id is not None and target not in self._lineage_ids(head.head_event_id):
            raise HistoryReplayError("target event is not on the selected branch")

        state: object = None
        start_after = branch.base_history_event_id
        if branch.base_state_ref is not None:
            state = self.get(branch.base_state_ref)
        elif start_after is not None:
            parent_event = self._get_event(start_after)[1]
            ref = parent_event.get("output_state_ref")
            state = self.get(ref) if isinstance(ref, str) else None

        diagnostics: list[JSONValue] = []
        started = start_after is None
        replayed: list[str] = []
        for event_id in event_ids:
            event = self._get_event(event_id)[1]
            if not started:
                if event_id == start_after:
                    started = True
                continue
            replayed.append(event_id)
            if not bool(event.get("state_transition", True)):
                if event.get("outcome") in {"started", "interrupted"}:
                    diagnostics.append(
                        {
                            "event_id": event_id,
                            "outcome": event.get("outcome"),
                            "message": "non-transition attempt was recorded and skipped",
                        }
                    )
                continue
            input_ref = event.get("input_state_ref")
            if isinstance(input_ref, str):
                if state is None:
                    state = self.get(input_ref)
                elif _record_digest("state", state) != input_ref:
                    raise HistoryReplayError(
                        f"event {event_id} input state does not match replay state"
                    )
            operation_ref = event.get("operation_ref")
            if isinstance(operation_ref, str):
                operation = self.get(operation_ref)
            else:
                operation = event.get("operation")
            try:
                state = self._call_reducer(reducer, state, operation, event)
            except HistoryError:
                raise
            except Exception as error:
                raise HistoryReplayError(f"reducer failed at event {event_id}") from error
            output_ref = event.get("output_state_ref")
            if isinstance(output_ref, str) and _record_digest("state", state) != output_ref:
                raise HistoryReplayError(
                    f"event {event_id} output state differs from recorded state"
                )
        return {
            "branch_id": branch_id,
            "target_event_id": target,
            "state": _copy_json(state),
            "state_ref": _record_digest("state", state) if state is not None else None,
            "events": replayed,
            "diagnostics": diagnostics,
            "head_event_id": head.head_event_id,
            "advanced": False,
        }

    def compare(self, base_branch_id: str, branch_id: str) -> dict[str, JSONValue]:
        """Describe branch divergence and dependency-driven stale descendants."""

        base_branch_id = _require_name(base_branch_id, "base branch ID")
        branch_id = _require_name(branch_id, "branch ID")
        base = self._load_branch(base_branch_id)
        branch = self._load_branch(branch_id)
        base_head = self._load_head(base_branch_id)
        head = self._load_head(branch_id)
        base_ids = self._lineage_ids(base_head.head_event_id)
        branch_ids = self._lineage_ids(head.head_event_id)
        common_length = 0
        for left, right in zip(base_ids, branch_ids, strict=False):
            if left != right:
                break
            common_length += 1
        common = base_ids[:common_length]
        base_tail = base_ids[common_length:]
        branch_tail = branch_ids[common_length:]
        replaced: list[dict[str, JSONValue]] = []
        dependency_differences: list[dict[str, JSONValue]] = []
        changed_refs: set[str] = set()
        for index in range(min(len(base_tail), len(branch_tail))):
            old_id = base_tail[index]
            new_id = branch_tail[index]
            if old_id == new_id:
                continue
            old = self._get_event(old_id)[1]
            new = self._get_event(new_id)[1]
            replaced.append({"old_event_id": old_id, "new_event_id": new_id})
            changed_refs.update(self._event_semantic_refs(old))
            changed_refs.update(self._event_semantic_refs(new))
            old_dependencies = old.get("dependency_refs", [])
            new_dependencies = new.get("dependency_refs", [])
            if old_dependencies != new_dependencies:
                dependency_differences.append(
                    {
                        "old_event_id": old_id,
                        "new_event_id": new_id,
                        "old_dependency_refs": old_dependencies,
                        "new_dependency_refs": new_dependencies,
                    }
                )
        for event_id in base_tail[len(replaced) :]:
            changed_refs.add(event_id)
        invalidated: list[str] = []
        for event_id in base_tail:
            event = self._get_event(event_id)[1]
            refs = self._event_semantic_refs(event)
            if refs.intersection(changed_refs) or event_id in changed_refs:
                invalidated.append(event_id)
        changed_artifacts: set[str] = set()
        for event_id in base_tail + branch_tail:
            event = self._get_event(event_id)[1]
            changed_artifacts.update(event.get("output_artifact_refs", []))
        unresolved = [
            event_id
            for event_id in base_tail + branch_tail
            if self._get_event(event_id)[1].get("outcome")
            in {
                "unresolved",
                "failed",
                "interrupted",
            }
        ]
        reused = sorted(set(base_ids).intersection(branch_ids).difference(common))
        return {
            "base_branch_id": base_branch_id,
            "branch_id": branch_id,
            "common_history_prefix": common,
            "replaced_events": replaced,
            "new_events": branch_tail,
            "reused_nodes": reused,
            "invalidated_descendants": invalidated,
            "unresolved_outcomes": sorted(set(unresolved)),
            "changed_artifacts": sorted(changed_artifacts),
            "dependency_differences": dependency_differences,
            "changed_dependencies": sorted(changed_refs),
            "base_head_event_id": base_head.head_event_id,
            "head_event_id": head.head_event_id,
            "base_status": base.status,
            "branch_status": branch.status,
        }

    # ---- branch and record internals ---------------------------------

    def _create_branch_unlocked(
        self,
        name: str,
        *,
        parent_branch_id: str | None,
        fork_checkpoint_id: str | None,
        base_state_ref: str | None,
        base_history_event_id: str | None,
    ) -> Branch:
        destination = self.branch_path(name)
        if destination.exists() or destination.is_symlink():
            existing = self._load_branch(name)
            requested = Branch(
                name,
                parent_branch_id,
                fork_checkpoint_id,
                base_state_ref,
                base_history_event_id,
            )
            if existing != requested:
                raise ValueError(f"branch {name!r} already exists with different lineage")
            return existing
        if parent_branch_id is not None:
            parent_branch_id = _require_name(parent_branch_id, "parent branch ID")
            self._load_branch(parent_branch_id)
        if fork_checkpoint_id is not None:
            _require_digest(fork_checkpoint_id, "fork checkpoint ID")
            checkpoint = self._get_checkpoint(fork_checkpoint_id)
            self._verify_checkpoint_payload(checkpoint)
            expected = Branch(
                name,
                str(checkpoint["branch_id"]),
                fork_checkpoint_id,
                checkpoint.get("pre_state_ref"),
                checkpoint.get("pre_history_root"),
            )
            if expected.parent_branch_id != parent_branch_id and parent_branch_id is not None:
                raise ValueError("fork parent branch does not match checkpoint")
            if base_state_ref is not None and base_state_ref != expected.base_state_ref:
                raise ValueError("fork state does not match checkpoint")
            if (
                base_history_event_id is not None
                and base_history_event_id != expected.base_history_event_id
            ):
                raise ValueError("fork history root does not match checkpoint")
            parent_branch_id = expected.parent_branch_id
            base_state_ref = expected.base_state_ref
            base_history_event_id = expected.base_history_event_id
        if base_state_ref is not None:
            _require_digest(base_state_ref, "base state ref")
            self._verify_reference(base_state_ref)
        if base_history_event_id is not None:
            _require_digest(base_history_event_id, "base history event ID")
            self._get_event(base_history_event_id)
        branch = Branch(
            name,
            parent_branch_id,
            fork_checkpoint_id,
            base_state_ref,
            base_history_event_id,
        )
        head_revision = self._event_position(base_history_event_id)
        envelope = {
            "schema": HISTORY_BRANCH_SCHEMA,
            **branch.as_dict(),
        }
        self._atomic_write(destination, _canonical_bytes(envelope) + b"\n")
        self._write_head(BranchHead(name, base_history_event_id, head_revision))
        return branch

    def _load_branch(self, branch_id: str) -> Branch:
        path = self.branch_path(branch_id)
        if not path.exists() and not path.is_symlink():
            raise HistoryMissingError(f"branch is unavailable: {branch_id}")
        if path.is_symlink() or not path.is_file():
            raise HistoryCorruptError(f"branch record is invalid: {branch_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise HistoryCorruptError(f"branch record is invalid: {branch_id}") from error
        expected = {
            "schema",
            "branch_id",
            "parent_branch_id",
            "fork_checkpoint_id",
            "base_state_ref",
            "base_history_event_id",
            "status",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise HistoryCorruptError(f"branch record is invalid: {branch_id}")
        if payload.get("schema") != HISTORY_BRANCH_SCHEMA or payload.get("branch_id") != branch_id:
            raise HistoryCorruptError(f"branch record is invalid: {branch_id}")
        for key in ("fork_checkpoint_id", "base_state_ref", "base_history_event_id"):
            if payload[key] is not None:
                _require_digest(payload[key], key)
        parent = payload["parent_branch_id"]
        if parent is not None:
            _require_name(parent, "parent branch ID")
        status = payload["status"]
        if status not in {"active", "stale"}:
            raise HistoryCorruptError(f"branch status is invalid: {branch_id}")
        return Branch(
            branch_id,
            parent,
            payload["fork_checkpoint_id"],
            payload["base_state_ref"],
            payload["base_history_event_id"],
            status,
        )

    def _load_head(self, branch_id: str) -> BranchHead:
        path = self.head_path(branch_id)
        if not path.exists() and not path.is_symlink():
            raise HistoryMissingError(f"branch head is unavailable: {branch_id}")
        if path.is_symlink() or not path.is_file():
            raise HistoryCorruptError(f"branch head is invalid: {branch_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise HistoryCorruptError(f"branch head is invalid: {branch_id}") from error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema", "branch_id", "head_event_id", "revision"}
            or payload.get("schema") != HISTORY_HEAD_SCHEMA
            or payload.get("branch_id") != branch_id
            or (
                payload.get("head_event_id") is not None
                and _SHA256.fullmatch(str(payload.get("head_event_id"))) is None
            )
            or not isinstance(payload.get("revision"), int)
            or isinstance(payload.get("revision"), bool)
            or payload.get("revision") < 0
        ):
            raise HistoryCorruptError(f"branch head is invalid: {branch_id}")
        head_event_id = payload["head_event_id"]
        if head_event_id is not None and not isinstance(head_event_id, str):
            raise HistoryCorruptError(f"branch head is invalid: {branch_id}")
        return BranchHead(branch_id, head_event_id, payload["revision"])

    def _write_head(self, head: BranchHead) -> None:
        self._atomic_write(
            self.head_path(head.branch_id),
            _canonical_bytes(
                {
                    "schema": HISTORY_HEAD_SCHEMA,
                    **head.as_dict(),
                }
            )
            + b"\n",
        )

    def _load_record(self, record_id: str) -> tuple[str, JSONValue]:
        cache = self._record_read_cache.get()
        cache_key = ("record", record_id)
        if cache is not None and cache_key in cache:
            cached = cache[cache_key]
            if isinstance(cached, tuple) and len(cached) == 2:
                return cached
        kind, stored_value = self._read_record_envelope(record_id)
        value = stored_value
        if kind == "state" and self._is_compact_state(stored_value):
            value = self._materialize_compact_state(stored_value)
        if _record_digest(kind, value) != record_id:
            raise HistoryCorruptError(f"record digest does not match payload: {record_id}")
        result = (kind, value)
        if cache is not None:
            cache[cache_key] = result
        return result

    def _read_record_envelope(self, record_id: str) -> tuple[str, JSONValue]:
        cache = self._record_read_cache.get()
        cache_key = ("record-envelope", record_id)
        if cache is not None and cache_key in cache:
            cached = cache[cache_key]
            if isinstance(cached, tuple) and len(cached) == 2:
                return cached
        path = self.record_path(record_id)
        if not path.exists() and not path.is_symlink():
            raise HistoryMissingError(f"record is unavailable: {record_id}")
        if path.is_symlink() or not path.is_file():
            raise HistoryCorruptError(f"record is invalid: {record_id}")
        try:
            raw = path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise HistoryCorruptError(f"record is invalid: {record_id}") from error
        expected = {"schema", "record_digest", "kind", "payload"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise HistoryCorruptError(f"record is invalid: {record_id}")
        if payload.get("schema") != HISTORY_RECORD_SCHEMA:
            raise HistoryCorruptError(f"record schema is invalid: {record_id}")
        if payload.get("record_digest") != record_id:
            raise HistoryCorruptError(f"record digest is invalid: {record_id}")
        try:
            kind = _require_kind(payload.get("kind"))
            value = _validate_json(payload.get("payload"), label="record payload")
        except ValueError as error:
            raise HistoryCorruptError(f"record payload is invalid: {record_id}") from error
        result = (kind, value)
        if cache is not None:
            cache[cache_key] = result
        return result

    @staticmethod
    def _is_compact_state(value: object) -> bool:
        return isinstance(value, Mapping) and set(value) == {_STATE_CONTEXT_REF, _STATE_DELTA}

    def _materialize_compact_state(self, value: Mapping[str, object]) -> dict[str, JSONValue]:
        context_ref = value.get(_STATE_CONTEXT_REF)
        if not isinstance(context_ref, str):
            raise HistoryCorruptError("compact planning state context ref is invalid")
        context_kind, context = self._load_record(_require_digest(context_ref, "state context ref"))
        if context_kind != "planning-problem" or not isinstance(context, Mapping):
            raise HistoryCorruptError("compact planning state context is not a planning problem")
        delta = value.get(_STATE_DELTA)
        if not isinstance(delta, Mapping):
            raise HistoryCorruptError("compact planning state delta is invalid")
        materialized = dict(_validate_json(delta, label="planning state delta"))
        if materialized.get("parent_problem_id") != context.get("problem_id"):
            raise HistoryCorruptError("compact planning state parent problem is stale")
        if materialized.get("problem_fingerprint") != context.get("input_fingerprint"):
            raise HistoryCorruptError("compact planning state problem fingerprint is stale")
        if materialized.get("brief_fingerprint") != context.get("brief_fingerprint"):
            raise HistoryCorruptError("compact planning state brief fingerprint is stale")
        if _canonical_bytes(materialized.get("brief")) != _canonical_bytes(context.get("brief")):
            raise HistoryCorruptError("compact planning state brief is stale")
        for field in _STATE_SHARED_FIELDS:
            if field not in context:
                raise HistoryCorruptError(f"compact planning state context is missing {field}")
            materialized[field] = _copy_json(context[field])
        return materialized

    def _read_artifact(self, artifact_id: str) -> bytes:
        cache = self._record_read_cache.get()
        cache_key = ("artifact", artifact_id)
        if cache is not None and cache_key in cache:
            cached = cache[cache_key]
            if isinstance(cached, bytes):
                return cached
        path = self.artifact_path(artifact_id)
        if not path.exists() and not path.is_symlink():
            raise HistoryMissingError(f"artifact is unavailable: {artifact_id}")
        if path.is_symlink() or not path.is_file():
            raise HistoryCorruptError(f"artifact is invalid: {artifact_id}")
        try:
            content = path.read_bytes()
        except OSError as error:
            raise HistoryCorruptError(f"artifact is invalid: {artifact_id}") from error
        if hashlib.sha256(content).hexdigest() != artifact_id:
            raise HistoryCorruptError(f"artifact digest does not match content: {artifact_id}")
        if cache is not None:
            cache[cache_key] = content
        return content

    def _get_event(self, event_id: str) -> tuple[str, dict[str, JSONValue]]:
        kind, payload = self._load_record(_require_digest(event_id, "event ID"))
        if kind != HISTORY_EVENT_KIND or not isinstance(payload, dict):
            raise HistoryCorruptError(f"record is not a history event: {event_id}")
        return kind, payload

    def _get_checkpoint(self, checkpoint_id: str) -> dict[str, JSONValue]:
        kind, payload = self._load_record(_require_digest(checkpoint_id, "checkpoint ID"))
        if kind != HISTORY_CHECKPOINT_KIND or not isinstance(payload, dict):
            raise HistoryCorruptError(f"record is not a checkpoint: {checkpoint_id}")
        return payload

    def _get_checkpoint_or_event(self, record_id: str) -> dict[str, JSONValue]:
        try:
            return self._get_checkpoint(record_id)
        except HistoryCorruptError:
            _kind, event = self._get_event(record_id)
            return self._checkpoint_payload(record_id, event)

    # ---- event/replay internals --------------------------------------

    def _prepare_event(
        self,
        branch: Branch,
        current: BranchHead,
        event: Mapping[str, object],
    ) -> dict[str, JSONValue]:
        prepared = _validate_json(dict(event), label="history event")
        if not isinstance(prepared, dict):  # pragma: no cover - mapping above.
            raise ValueError("history event must be an object")
        supplied_branch = prepared.get("branch_id")
        if supplied_branch is not None and supplied_branch != branch.branch_id:
            raise ValueError("event branch ID does not match commit branch")
        supplied_parent = prepared.get("timeline_parent_id")
        if supplied_parent is not None and supplied_parent != current.head_event_id:
            raise HistoryStaleHeadError(
                branch.branch_id,
                supplied_parent if isinstance(supplied_parent, str) else None,
                current.head_event_id,
                current.revision,
            )
        event_kind = prepared.get("event_kind", prepared.get("kind", "operation"))
        if not isinstance(event_kind, str) or not event_kind:
            raise ValueError("event kind is invalid")
        prepared["event_kind"] = event_kind
        prepared["branch_id"] = branch.branch_id
        prepared["timeline_parent_id"] = current.head_event_id
        prepared["timeline_position"] = current.revision + 1
        if "input_history_root" not in prepared:
            prepared["input_history_root"] = current.head_event_id
        if prepared["input_history_root"] is not None:
            _require_digest(prepared["input_history_root"], "input history root")

        inline_kinds = {
            "input_state": ("input_state_ref", "state"),
            "output_state": ("output_state_ref", "state"),
            "operation": ("operation_ref", "operation"),
            "request": ("request_ref", "request"),
            "receipt": ("receipt_ref", "receipt"),
        }
        for field, (ref_field, kind) in inline_kinds.items():
            if field in prepared:
                digest = self.put(prepared[field], kind=kind)
                supplied = prepared.get(ref_field)
                if supplied is not None and supplied != digest:
                    raise ValueError(f"{field} and {ref_field} disagree")
                prepared[ref_field] = digest
                # States and operations are often the largest materialized
                # values in a run.  New events carry their immutable refs;
                # older events with inline values remain readable and are
                # still checked against their refs during verification.
                if field in {
                    "input_state",
                    "output_state",
                    "operation",
                    "request",
                    "receipt",
                }:
                    prepared.pop(field, None)
            elif prepared.get(ref_field) is not None:
                _require_digest(prepared[ref_field], ref_field)

        prepared["dependency_refs"] = _unique_refs(
            prepared.get("dependency_refs", prepared.get("dependencies")),
            "dependency refs",
        )
        for field in (
            "side_effect_receipt_refs",
            "output_artifact_refs",
            "diagnostic_refs",
            "capability_refs",
        ):
            if field in prepared and field != "capability_refs":
                prepared[field] = _unique_refs(prepared[field], field)
        for field in (
            "input_state_ref",
            "output_state_ref",
            "operation_ref",
            "request_ref",
            "receipt_ref",
            "transcript_ref",
        ):
            if prepared.get(field) is not None:
                _require_digest(prepared[field], field)
        refs_to_verify = list(prepared["dependency_refs"])
        for field in (
            "input_state_ref",
            "output_state_ref",
            "operation_ref",
            "request_ref",
            "receipt_ref",
            "transcript_ref",
            "input_history_root",
        ):
            ref = prepared.get(field)
            if isinstance(ref, str):
                refs_to_verify.append(ref)
        for field in ("side_effect_receipt_refs", "output_artifact_refs", "diagnostic_refs"):
            refs_to_verify.extend(prepared.get(field, []))
        for ref in refs_to_verify:
            self._verify_record_closure(ref)

        if "state_transition" not in prepared:
            prepared["state_transition"] = event_kind not in {
                "attempt",
                "receipt",
                "diagnostic",
            }
        if not isinstance(prepared["state_transition"], bool):
            raise ValueError("state_transition must be a boolean")
        if prepared["input_history_root"] != current.head_event_id:
            raise ValueError("input history root must match the current branch head")
        current_state_ref = self._current_state_ref(branch, current)
        input_state_ref = prepared.get("input_state_ref")
        if (
            current_state_ref is not None
            and (prepared["state_transition"] or input_state_ref is not None)
            and input_state_ref != current_state_ref
        ):
            raise ValueError("input state must match the current branch state")
        outcome = prepared.get("outcome", "accepted")
        if not isinstance(outcome, str) or not outcome:
            raise ValueError("event outcome is invalid")
        prepared["outcome"] = outcome
        actor_kind = str(prepared.get("actor_kind", "")).lower()
        if (
            prepared["state_transition"]
            and outcome in {"accepted", "reused"}
            and (
                actor_kind in {"provider", "model", "specialist-model", "jev"}
                or event_kind in {"provider", "model", "judgment"}
            )
            and not prepared.get("receipt_ref")
            and not prepared.get("side_effect_receipt_refs")
        ):
            raise ValueError("accepted model operation requires a response receipt")
        return prepared

    def _write_checkpoint_for_event(
        self,
        event_id: str,
        event: Mapping[str, JSONValue],
    ) -> str:
        checkpoint = self._checkpoint_payload(event_id, event)
        return self.put(checkpoint, kind=HISTORY_CHECKPOINT_KIND)

    @staticmethod
    def _checkpoint_payload(event_id: str, event: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
        return {
            "checkpoint_id": event_id,
            "branch_id": event.get("branch_id"),
            "event_id": event_id,
            "pre_state_ref": event.get("input_state_ref"),
            "post_state_ref": event.get("output_state_ref"),
            "pre_history_root": event.get("timeline_parent_id"),
            "post_history_root": event_id,
            "dependency_manifest_ref": event.get("dependency_manifest_ref"),
            "dependency_refs": event.get("dependency_refs", []),
        }

    def _verify_checkpoint_payload(self, checkpoint: Mapping[str, JSONValue]) -> None:
        required = {
            "checkpoint_id",
            "branch_id",
            "event_id",
            "pre_state_ref",
            "post_state_ref",
            "pre_history_root",
            "post_history_root",
            "dependency_manifest_ref",
            "dependency_refs",
        }
        if set(checkpoint) != required:
            raise HistoryCorruptError("checkpoint contract is invalid")
        event_id = checkpoint.get("event_id")
        if not isinstance(event_id, str):
            raise HistoryCorruptError("checkpoint event ID is invalid")
        event = self._get_event(event_id)[1]
        expected = self._checkpoint_payload(event_id, event)
        if dict(checkpoint) != expected:
            raise HistoryCorruptError("checkpoint does not match event")
        self._verify_event_chain(event_id)

    def _verify_branch(self, branch: Branch, head: BranchHead) -> list[str]:
        if head.head_event_id is None:
            if head.revision != self._event_position(branch.base_history_event_id):
                raise HistoryCorruptError("branch head revision is invalid")
            return []
        events = self._verify_event_chain(head.head_event_id)
        if head.revision != self._event_position(head.head_event_id):
            raise HistoryCorruptError("branch head revision is invalid")
        return events

    def _verify_event_chain(self, event_id: str) -> list[str]:
        ids = self._lineage_ids(event_id)
        for current_id in ids:
            event = self._get_event(current_id)[1]
            self._verify_event_payload(current_id, event)
        return ids

    def _verify_event_payload(self, event_id: str, event: Mapping[str, JSONValue]) -> None:
        if event.get("branch_id") is None or not isinstance(event.get("branch_id"), str):
            raise HistoryCorruptError(f"event branch is invalid: {event_id}")
        if not isinstance(event.get("timeline_position"), int) or event["timeline_position"] < 1:
            raise HistoryCorruptError(f"event position is invalid: {event_id}")
        parent = event.get("timeline_parent_id")
        if parent is not None:
            _require_digest(parent, "timeline parent ID")
            parent_event = self._get_event(parent)[1]
            position = parent_event.get("timeline_position")
            if not isinstance(position, int) or position >= event["timeline_position"]:
                raise HistoryCorruptError(f"event timeline order is invalid: {event_id}")
        for field, kind in (
            ("input_state", "state"),
            ("output_state", "state"),
            ("operation", "operation"),
            ("request", "request"),
            ("receipt", "receipt"),
        ):
            ref_field = f"{field}_ref"
            if (
                field in event
                and ref_field in event
                and _record_digest(kind, event[field]) != event[ref_field]
            ):
                raise HistoryCorruptError(f"event {field} does not match its ref: {event_id}")
        refs = self._event_semantic_refs(event)
        for ref in refs:
            self._verify_record_closure(ref)

    def _verify_record_closure(self, record_id: str) -> list[str]:
        seen: set[str] = set()
        active: set[str] = set()

        def visit(ref: str) -> None:
            if ref in active:
                raise HistoryCorruptError(f"record dependency cycle: {ref}")
            if ref in seen:
                return
            active.add(ref)
            record_path = self.record_path(ref)
            artifact_path = self.artifact_path(ref)
            if record_path.exists() or record_path.is_symlink():
                kind, payload = self._load_record(ref)
                if kind == "state":
                    _, payload = self._read_record_envelope(ref)
                for child in self._payload_refs(payload):
                    visit(child)
            elif artifact_path.exists() or artifact_path.is_symlink():
                self.get_artifact(ref)
            else:
                raise HistoryMissingError(f"dependency is unavailable: {ref}")
            active.remove(ref)
            seen.add(ref)

        visit(record_id)
        return sorted(seen)

    def _verify_reference(self, ref: str) -> None:
        _require_digest(ref, "dependency ref")
        if self.record_path(ref).exists() or self.record_path(ref).is_symlink():
            self._load_record(ref)
        elif self.artifact_path(ref).exists() or self.artifact_path(ref).is_symlink():
            self.get_artifact(ref)
        else:
            raise HistoryMissingError(f"dependency is unavailable: {ref}")

    def _lineage_ids(self, event_id: str | None) -> list[str]:
        if event_id is None:
            return []
        current = _require_digest(event_id, "event ID")
        result: list[str] = []
        seen: set[str] = set()
        while current is not None:
            if current in seen:
                raise HistoryCorruptError("history event cycle detected")
            seen.add(current)
            event = self._get_event(current)[1]
            result.append(current)
            parent = event.get("timeline_parent_id")
            if parent is not None:
                _require_digest(parent, "timeline parent ID")
            current = parent if isinstance(parent, str) else None
        result.reverse()
        return result

    def _event_position(self, event_id: str | None) -> int:
        if event_id is None:
            return 0
        event = self._get_event(event_id)[1]
        position = event.get("timeline_position")
        if not isinstance(position, int):
            raise HistoryCorruptError("event position is invalid")
        return position

    def _current_state_ref(self, branch: Branch, current: BranchHead) -> str | None:
        if current.head_event_id is None:
            return branch.base_state_ref
        for event_id in reversed(self._lineage_ids(current.head_event_id)):
            event = self._get_event(event_id)[1]
            state_ref = event.get("output_state_ref")
            if isinstance(state_ref, str):
                return state_ref
        return branch.base_state_ref

    @staticmethod
    def _payload_refs(payload: object) -> list[str]:
        if not isinstance(payload, Mapping):
            return []
        refs: list[str] = []
        for key, value in payload.items():
            if key == "dependency_refs" or key.endswith("_refs"):
                if isinstance(value, list):
                    refs.extend(
                        item for item in value if isinstance(item, str) and _SHA256.fullmatch(item)
                    )
            elif key.endswith("_ref") and isinstance(value, str) and _SHA256.fullmatch(value):
                refs.append(value)
        return sorted(set(refs))

    def _event_semantic_refs(self, event: Mapping[str, JSONValue]) -> set[str]:
        refs = set(self._payload_refs(event))
        refs.update(
            item
            for item in (
                event.get("input_state_ref"),
                event.get("output_state_ref"),
                event.get("operation_ref"),
                event.get("request_ref"),
                event.get("receipt_ref"),
            )
            if isinstance(item, str) and _SHA256.fullmatch(item)
        )
        return refs

    def _call_reducer(
        self,
        reducer: Callable[..., object],
        state: object,
        operation: object,
        event: Mapping[str, JSONValue],
    ) -> object:
        del event
        return reducer(state, operation)

    @contextmanager
    def _branch_lock(self, branch_id: str):
        self.locks_root.mkdir(parents=True, exist_ok=True)
        path = self.locks_root / f"{branch_id}.lock"
        with self._thread_lock:
            stream = path.open("a+")
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                stream.close()

    @staticmethod
    def _atomic_write(destination: Path, content: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
        try:
            with temporary.open("wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                temporary.rename(destination)
            except FileExistsError:
                # Immutable records are validated by the caller after this point.
                if not destination.exists():
                    raise
            _fsync_directory(destination.parent)
        finally:
            temporary.unlink(missing_ok=True)


__all__ = [
    "HISTORY_BRANCH_SCHEMA",
    "HISTORY_CHECKPOINT_KIND",
    "HISTORY_EVENT_KIND",
    "HISTORY_HEAD_SCHEMA",
    "HISTORY_RECORD_SCHEMA",
    "Branch",
    "BranchHead",
    "HistoryCorruptError",
    "HistoryError",
    "HistoryMissingError",
    "HistoryReplayError",
    "HistoryStaleHeadError",
    "HistoryStore",
    "JSONValue",
]
