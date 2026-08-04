"""Governed ports for offline external planning-analysis experiments.

External engines are evidence comparators, never geometry or selection
authorities.  This module deliberately keeps their observations in a separate
closed record type and only accepts frozen, retained exports.  The production
prototype reads a pinned JSON export; it does not invoke a process or make a
network request.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from pyproj import CRS

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_DISALLOWED_OUTPUT_KEYS = frozenset(
    {
        "canonical_geometry",
        "geometry",
        "route_geometry",
        "route_winner",
        "selected_route",
        "winner",
    }
)
_OBSERVATION_STATES = frozenset(
    {"available", "null", "unreachable", "unmatched", "unavailable", "invalid"}
)


class ExternalAnalysisStatus(StrEnum):
    """Terminal state of one governed external analysis run."""

    COMPLETE = "complete"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    INVALID = "invalid"


class ExternalAnalysisUnavailableError(RuntimeError):
    """An external engine was unavailable without invalidating the core result."""


class ExternalAnalysisAdapter(Protocol):
    """A provider port that returns only external observations."""

    def run(self, request: ExternalAnalysisRequest) -> ExternalAnalysisResponse: ...


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name)


def _identifier(value: object, name: str) -> str:
    text = _required_text(value, name)
    if _ID.fullmatch(text) is None:
        raise ValueError(f"{name} must be a canonical identifier")
    return text


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase full SHA-256")
    return value


def _crs_identity(value: object) -> str:
    try:
        crs = CRS.from_user_input(value)
    except Exception as error:
        raise ValueError("external analysis requires an explicit valid CRS") from error
    authority = crs.to_authority()
    if authority is not None:
        return f"{authority[0]}:{authority[1]}"
    return crs.to_wkt(version="WKT2_2019", pretty=False)


def _canonical_decimal(value: object, name: str) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name} must be a finite numeric value")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a finite numeric value") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite numeric value")
    # Decimal(str(value)) avoids binary artefacts while retaining explicit
    # precision from retained exports.
    from decimal import Decimal, InvalidOperation

    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be a finite numeric value") from error
    if not parsed.is_finite():
        raise ValueError(f"{name} must be a finite numeric value")
    normalized = format(parsed.normalize(), "f")
    if normalized in {"", "-0"}:
        return "0"
    return normalized


def _canonical_value(value: object, *, path: str = "value") -> object:
    """Return a JSON-safe, deterministic representation for governance inputs."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return _canonical_decimal(value, path)
    from decimal import Decimal

    if isinstance(value, Decimal):
        return _canonical_decimal(value, path)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{path} mapping keys must be text")
        return {key: _canonical_value(value[key], path=f"{path}.{key}") for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise ValueError(f"{path} contains unsupported value {type(value).__name__}")


def _freeze(value: object) -> object:
    canonical = _canonical_value(value)
    if isinstance(canonical, dict):
        return MappingProxyType({key: _freeze(item) for key, item in canonical.items()})
    if isinstance(canonical, list):
        return tuple(_freeze(item) for item in canonical)
    return canonical


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _canonical_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _hash_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _find_disallowed_output(value: object, path: str = "raw_output") -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and key.lower() in _DISALLOWED_OUTPUT_KEYS:
                return f"{path}.{key}"
            found = _find_disallowed_output(child, f"{path}.{key}")
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found = _find_disallowed_output(child, f"{path}[{index}]")
            if found is not None:
                return found
    return None


@dataclass(frozen=True)
class ExternalAnalysisObservation:
    """One external observation, intentionally separate from SATN geometry/facts."""

    observation_id: str
    subject_id: str | None
    metric: str
    state: str
    value: object = None
    unit: str | None = None
    source_row_id: str | None = None
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.observation_id, "observation id")
        if self.subject_id is not None:
            _identifier(self.subject_id, "subject id")
        _required_text(self.metric, "metric")
        if self.state not in _OBSERVATION_STATES:
            raise ValueError(f"unsupported external observation state: {self.state}")
        if self.unit is not None:
            _required_text(self.unit, "observation unit")
        if self.source_row_id is not None:
            _identifier(self.source_row_id, "source row id")
        normalized_ids = tuple(
            sorted(_identifier(item, "evidence id") for item in self.evidence_ids)
        )
        if len(normalized_ids) != len(set(normalized_ids)):
            raise ValueError("evidence ids cannot contain duplicates")
        object.__setattr__(self, "evidence_ids", normalized_ids)
        if self.state == "available":
            if self.value is None:
                raise ValueError("available external observations require a value")
            object.__setattr__(self, "value", _canonical_decimal(self.value, "observation value"))
        elif self.value is not None:
            raise ValueError("non-available external observations cannot carry a value")

    @property
    def value_decimal(self) -> str | None:
        return self.value if isinstance(self.value, str) else None

    @property
    def availability_state(self) -> str:
        """Compatibility name that does not imply a route-selection decision."""

        return self.state

    def canonical_payload(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "subject_id": self.subject_id,
            "metric": self.metric,
            "state": self.state,
            "value_decimal": self.value_decimal,
            "unit": self.unit,
            "source_row_id": self.source_row_id,
            "evidence_ids": self.evidence_ids,
        }


@dataclass(frozen=True)
class ExternalAnalysisRequest:
    """All governed inputs needed to reproduce an external analysis run."""

    analysis_id: str
    profile_id: str
    source_export_hashes: tuple[str, ...] = ()
    parameters: Mapping[str, object] = ()
    defaults: Mapping[str, object] = ()
    canonical_crs: str = "EPSG:27700"
    timezone: str = "UTC"
    analysis_date: date | str | None = None
    seed: int | None = None
    thread_policy: str = "single-threaded"
    expected_observation_ids: tuple[str, ...] = ()
    profile_version: int = 1
    # Aliases are accepted to keep source/export terminology explicit at call sites.
    source_export_fingerprints: tuple[str, ...] = ()
    source_export_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.analysis_id, "analysis id")
        _identifier(self.profile_id, "profile id")
        if self.profile_version < 1:
            raise ValueError("profile version must be positive")
        aliases = list(self.source_export_hashes) + list(self.source_export_fingerprints)
        if self.source_export_fingerprint is not None:
            aliases.append(self.source_export_fingerprint)
        if not aliases:
            raise ValueError("at least one source export hash is required")
        hashes = tuple(sorted(set(_sha256(item, "source export hash") for item in aliases)))
        object.__setattr__(self, "source_export_hashes", hashes)
        object.__setattr__(self, "source_export_fingerprints", hashes)
        object.__setattr__(
            self,
            "source_export_fingerprint",
            hashes[0] if len(hashes) == 1 else None,
        )
        object.__setattr__(self, "parameters", _freeze(self.parameters or {}))
        object.__setattr__(self, "defaults", _freeze(self.defaults or {}))
        object.__setattr__(self, "canonical_crs", _crs_identity(self.canonical_crs))
        _required_text(self.timezone, "timezone")
        if self.analysis_date is None:
            raise ValueError("analysis date is required")
        if isinstance(self.analysis_date, date):
            normalized_date = self.analysis_date
        elif isinstance(self.analysis_date, str):
            try:
                normalized_date = date.fromisoformat(self.analysis_date)
            except ValueError as error:
                raise ValueError("analysis date must be ISO-8601") from error
        else:
            raise ValueError("analysis date must be a date or ISO-8601 text")
        object.__setattr__(self, "analysis_date", normalized_date)
        if self.seed is not None and (
            not isinstance(self.seed, int) or isinstance(self.seed, bool) or self.seed < 0
        ):
            raise ValueError("seed must be a non-negative integer")
        _required_text(self.thread_policy, "thread policy")
        expected = tuple(
            sorted(
                _identifier(item, "expected observation id")
                for item in self.expected_observation_ids
            )
        )
        if len(expected) != len(set(expected)):
            raise ValueError("expected observation ids cannot contain duplicates")
        object.__setattr__(self, "expected_observation_ids", expected)

    @property
    def profile_fingerprint(self) -> str:
        return _hash_json(self.identity_payload())

    def identity_payload(self) -> dict[str, object]:
        return {
            "contract": "satn-external-analysis-request/v1",
            "analysis_id": self.analysis_id,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "source_export_hashes": self.source_export_hashes,
            "parameters": self.parameters,
            "defaults": self.defaults,
            "canonical_crs": self.canonical_crs,
            "timezone": self.timezone,
            "analysis_date": self.analysis_date.isoformat(),
            "seed": self.seed,
            "thread_policy": self.thread_policy,
            "expected_observation_ids": self.expected_observation_ids,
        }


@dataclass(frozen=True)
class ExternalAnalysisResponse:
    """Provider output before governance normalization."""

    status: ExternalAnalysisStatus | str = ExternalAnalysisStatus.COMPLETE
    observations: tuple[ExternalAnalysisObservation, ...] = ()
    engine_name: str = "unknown"
    engine_version: str = "unknown"
    engine_commit: str = "unknown"
    engine_licence: str = "unknown"
    environment: Mapping[str, object] = ()
    resource_limits: Mapping[str, object] = ()
    warnings: tuple[str, ...] = ()
    raw_output: object = None
    raw_output_bytes: bytes | None = None
    unavailable_reason: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        try:
            normalized_status = ExternalAnalysisStatus(self.status)
        except ValueError as error:
            raise ValueError(f"unsupported external analysis status: {self.status}") from error
        object.__setattr__(self, "status", normalized_status)
        observations = tuple(self.observations)
        if not all(isinstance(item, ExternalAnalysisObservation) for item in observations):
            raise ValueError(
                "external response observations must be ExternalAnalysisObservation records"
            )
        object.__setattr__(self, "observations", observations)
        for field_name in ("engine_name", "engine_version", "engine_commit", "engine_licence"):
            _required_text(getattr(self, field_name), field_name.replace("_", " "))
        object.__setattr__(self, "environment", _freeze(self.environment or {}))
        object.__setattr__(self, "resource_limits", _freeze(self.resource_limits or {}))
        if self.raw_output_bytes is not None and not isinstance(self.raw_output_bytes, bytes):
            raise ValueError("raw output bytes must be bytes")
        warnings = tuple(_required_text(item, "warning") for item in self.warnings)
        object.__setattr__(self, "warnings", warnings)
        if self.unavailable_reason is not None:
            _required_text(self.unavailable_reason, "unavailable reason")
        if self.error_code is not None:
            _identifier(self.error_code, "error code")


@dataclass(frozen=True)
class GovernedExternalAnalysisRun:
    """Immutable, reviewable result of one governed external adapter call."""

    status: ExternalAnalysisStatus
    analysis_id: str
    profile_id: str
    source_export_hashes: tuple[str, ...]
    engine_name: str
    engine_version: str
    engine_commit: str
    engine_licence: str
    environment: Mapping[str, object]
    parameters: Mapping[str, object]
    defaults: Mapping[str, object]
    canonical_crs: str
    timezone: str
    analysis_date: date
    seed: int | None
    thread_policy: str
    resource_limits: Mapping[str, object]
    warnings: tuple[str, ...]
    observations: tuple[ExternalAnalysisObservation, ...]
    raw_output_hash: str
    normalized_observation_hash: str
    run_fingerprint: str
    error_code: str | None = None
    unavailable_reason: str | None = None

    @property
    def raw_output_sha256(self) -> str:
        return self.raw_output_hash

    @property
    def normalized_observation_sha256(self) -> str:
        return self.normalized_observation_hash

    @property
    def source_export_fingerprints(self) -> tuple[str, ...]:
        return self.source_export_hashes

    def canonical_payload(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "analysis_id": self.analysis_id,
            "profile_id": self.profile_id,
            "source_export_hashes": self.source_export_hashes,
            "engine_name": self.engine_name,
            "engine_version": self.engine_version,
            "engine_commit": self.engine_commit,
            "engine_licence": self.engine_licence,
            "environment": self.environment,
            "parameters": self.parameters,
            "defaults": self.defaults,
            "canonical_crs": self.canonical_crs,
            "timezone": self.timezone,
            "analysis_date": self.analysis_date.isoformat(),
            "seed": self.seed,
            "thread_policy": self.thread_policy,
            "resource_limits": self.resource_limits,
            "warnings": self.warnings,
            "observations": tuple(item.canonical_payload() for item in self.observations),
            "raw_output_hash": self.raw_output_hash,
            "normalized_observation_hash": self.normalized_observation_hash,
            "error_code": self.error_code,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True)
class DeterministicFixtureExternalAnalysisAdapter:
    """Offline fixture adapter with deterministic metadata and observations."""

    fixture_id: str
    observations: tuple[ExternalAnalysisObservation, ...] = ()
    status: ExternalAnalysisStatus | str = ExternalAnalysisStatus.COMPLETE
    engine_name: str = "fixture-engine"
    engine_version: str = "fixture-v1"
    engine_commit: str = "fixture"
    engine_licence: str = "fixture-only"
    environment: Mapping[str, object] = ()
    warnings: tuple[str, ...] = ()
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.fixture_id, "fixture id")
        try:
            object.__setattr__(self, "status", ExternalAnalysisStatus(self.status))
        except ValueError as error:
            raise ValueError(f"unsupported fixture status: {self.status}") from error

    def run(self, request: ExternalAnalysisRequest) -> ExternalAnalysisResponse:
        del request
        fixture_payload = {
            "schema": "satn-external-analysis-fixture/v1",
            "fixture_id": self.fixture_id,
            "observations": tuple(item.canonical_payload() for item in self.observations),
        }
        return ExternalAnalysisResponse(
            status=self.status,
            observations=tuple(self.observations),
            engine_name=self.engine_name,
            engine_version=self.engine_version,
            engine_commit=self.engine_commit,
            engine_licence=self.engine_licence,
            environment=self.environment,
            warnings=self.warnings,
            raw_output=fixture_payload,
            raw_output_bytes=_canonical_json(fixture_payload),
            unavailable_reason=self.unavailable_reason,
            error_code=(
                "engine-unavailable" if self.status is ExternalAnalysisStatus.UNAVAILABLE else None
            ),
        )


def _validate_no_symlink_components(path: Path) -> None:
    """Reject lexical traversal and every existing symlink in a retained path."""

    if not path.is_absolute():
        raise ValueError("pinned export path must be absolute")
    if ".." in path.parts:
        raise ValueError("pinned export path cannot contain parent traversal")
    current = path
    while True:
        if current.is_symlink():
            raise ValueError("pinned export path cannot be a symlink or have a symlink parent")
        if current == current.parent:
            break
        current = current.parent


def _read_pinned_export(path: Path, *, max_bytes: int) -> bytes:
    """Open and read a retained export without following a path race."""

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    directory_fd: int | None = None
    file_fd: int | None = None
    try:
        directory_fd = os.open("/", directory_flags)
        for component in path.parent.parts[1:]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        # lstat through the opened parent prevents a directory/FIFO from being
        # mistaken for an ordinary retained file before the final open.
        status = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(status.st_mode):
            raise ValueError("pinned export must be a regular file")
        file_fd = os.open(path.name, file_flags, dir_fd=directory_fd)
        opened_status = os.fstat(file_fd)
        if not stat.S_ISREG(opened_status.st_mode):
            raise ValueError("pinned export must be a regular file")
        if opened_status.st_size > max_bytes:
            raise ValueError("pinned export exceeds max export bytes")
        chunks: list[bytes] = []
        remaining = max_bytes
        while True:
            chunk = os.read(file_fd, min(1_048_576, remaining + 1))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
            if remaining < 0:
                raise ValueError("pinned export exceeds max export bytes")
        return b"".join(chunks)
    except FileNotFoundError as error:
        raise ExternalAnalysisUnavailableError("pinned export is unavailable") from error
    except OSError as error:
        # ELOOP is the expected no-follow response when a path is swapped to a
        # symlink after adapter construction.  Keep it typed as invalid output.
        raise ValueError("pinned export path cannot be opened safely") from error
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


@dataclass(frozen=True)
class PinnedExternalAnalysisAdapter:
    """Production boundary for a retained frozen JSON export.

    It reads bytes only.  There is intentionally no subprocess, shell, HTTP
    client or geometry conversion in this adapter.
    """

    export_path: Path | str
    expected_export_sha256: str
    engine_name: str = "pinned-external-engine"
    engine_version: str = "unknown"
    engine_commit: str = "unknown"
    engine_licence: str = "unknown"
    environment: Mapping[str, object] = ()
    max_export_bytes: int = 50_000_000

    def __post_init__(self) -> None:
        _sha256(self.expected_export_sha256, "expected export hash")
        path = Path(self.export_path)
        if not path.is_absolute():
            raise ValueError("pinned export path must be absolute")
        if ".." in path.parts:
            raise ValueError("pinned export path cannot contain parent traversal")
        _validate_no_symlink_components(path)
        if (
            not isinstance(self.max_export_bytes, int)
            or isinstance(self.max_export_bytes, bool)
            or self.max_export_bytes <= 0
        ):
            raise ValueError("max export bytes must be a positive integer")
        object.__setattr__(self, "export_path", path)

    def run(self, request: ExternalAnalysisRequest) -> ExternalAnalysisResponse:
        if self.expected_export_sha256 not in request.source_export_hashes:
            raise ValueError("pinned export hash is absent from governed request")
        path = self.export_path
        _validate_no_symlink_components(path)
        raw = _read_pinned_export(path, max_bytes=self.max_export_bytes)
        actual = hashlib.sha256(raw).hexdigest()
        if actual != self.expected_export_sha256:
            raise ValueError("pinned export checksum does not match retained bytes")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("pinned export is not valid UTF-8 JSON") from error
        if not isinstance(payload, Mapping):
            raise ValueError("pinned export root must be an object")
        found = _find_disallowed_output(payload)
        if found is not None:
            raise ValueError(f"pinned export contains prohibited external claim at {found}")
        rows = payload.get("observations", payload.get("rows"))
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise ValueError("pinned export observations must be an array")
        observations = tuple(_observation_from_mapping(row) for row in rows)
        warnings = payload.get("warnings", ())
        if not isinstance(warnings, Sequence) or isinstance(warnings, (str, bytes)):
            raise ValueError("pinned export warnings must be an array")
        return ExternalAnalysisResponse(
            status=payload.get("status", ExternalAnalysisStatus.COMPLETE),
            observations=observations,
            engine_name=self.engine_name,
            engine_version=self.engine_version,
            engine_commit=self.engine_commit,
            engine_licence=self.engine_licence,
            environment=self.environment,
            resource_limits={"max_export_bytes": self.max_export_bytes},
            warnings=tuple(str(item) for item in warnings),
            raw_output=payload,
            raw_output_bytes=raw,
            unavailable_reason=payload.get("unavailable_reason"),
            error_code=payload.get("error_code"),
        )


# Clear aliases for callers that use the wording from the planning specification.
FixtureExternalAnalysisAdapter = DeterministicFixtureExternalAnalysisAdapter
PinnedExportAnalysisAdapter = PinnedExternalAnalysisAdapter


def _observation_from_mapping(value: object) -> ExternalAnalysisObservation:
    if not isinstance(value, Mapping):
        raise ValueError("external observation row must be an object")
    state = value.get("state", value.get("status"))
    if state is None:
        raise ValueError("external observation row requires state")
    raw_value = value.get("value", value.get("value_decimal"))
    return ExternalAnalysisObservation(
        observation_id=value.get("observation_id"),
        subject_id=value.get("subject_id"),
        metric=value.get("metric"),
        state=state,
        value=raw_value,
        unit=value.get("unit"),
        source_row_id=value.get("source_row_id"),
        evidence_ids=tuple(value.get("evidence_ids", ())),
    )


def _coerce_response(value: object) -> ExternalAnalysisResponse:
    if isinstance(value, ExternalAnalysisResponse):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("external adapter must return ExternalAnalysisResponse")
    rows = value.get("observations", ())
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("external response observations must be an array")
    observations = tuple(
        item if isinstance(item, ExternalAnalysisObservation) else _observation_from_mapping(item)
        for item in rows
    )
    return ExternalAnalysisResponse(
        status=value.get("status", ExternalAnalysisStatus.COMPLETE),
        observations=observations,
        engine_name=value.get("engine_name", "unknown"),
        engine_version=value.get("engine_version", "unknown"),
        engine_commit=value.get("engine_commit", "unknown"),
        engine_licence=value.get("engine_licence", "unknown"),
        environment=value.get("environment", {}),
        resource_limits=value.get("resource_limits", {}),
        warnings=tuple(value.get("warnings", ())),
        raw_output=value.get("raw_output", value),
        raw_output_bytes=value.get("raw_output_bytes"),
        unavailable_reason=value.get("unavailable_reason"),
        error_code=value.get("error_code"),
    )


def _fallback_observations(
    request: ExternalAnalysisRequest,
    existing: tuple[ExternalAnalysisObservation, ...],
) -> tuple[ExternalAnalysisObservation, ...]:
    ids = tuple(
        sorted({item.observation_id for item in existing} | set(request.expected_observation_ids))
    )
    unavailable = tuple(
        ExternalAnalysisObservation(
            observation_id=observation_id,
            subject_id=None,
            metric="external-analysis",
            state="unavailable",
        )
        for observation_id in ids
    )
    if not unavailable:
        return (
            ExternalAnalysisObservation(
                observation_id="analysis-unavailable",
                subject_id=None,
                metric="external-analysis",
                state="unavailable",
            ),
        )
    return unavailable


def _normalize_observations(
    observations: Sequence[ExternalAnalysisObservation],
) -> tuple[ExternalAnalysisObservation, ...]:
    normalized = tuple(observations)
    ids = [item.observation_id for item in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("external observations cannot contain duplicate observation ids")
    return tuple(sorted(normalized, key=lambda item: (item.observation_id, item.metric)))


def _build_run(
    request: ExternalAnalysisRequest,
    response: ExternalAnalysisResponse,
    *,
    status: ExternalAnalysisStatus | None = None,
    error_code: str | None = None,
    warning: str | None = None,
) -> GovernedExternalAnalysisRun:
    final_status = status or response.status
    observations = _normalize_observations(response.observations)
    if final_status is not ExternalAnalysisStatus.COMPLETE:
        observations = _normalize_observations(_fallback_observations(request, observations))
    raw_payload = response.raw_output
    if raw_payload is None:
        raw_payload = {"observations": tuple(item.canonical_payload() for item in observations)}
    found = _find_disallowed_output(raw_payload)
    if found is not None:
        raise ValueError(f"external output contains prohibited claim at {found}")
    raw_bytes = (
        response.raw_output_bytes
        if response.raw_output_bytes is not None
        else (raw_payload if isinstance(raw_payload, bytes) else _canonical_json(raw_payload))
    )
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    normalized_payload = tuple(item.canonical_payload() for item in observations)
    normalized_hash = _hash_json(normalized_payload)
    warnings = tuple(response.warnings) + ((warning,) if warning else ())
    final_error = error_code or response.error_code
    run_payload = {
        "contract": "satn-governed-external-analysis-run/v1",
        "request": request.identity_payload(),
        "status": final_status.value,
        "engine_name": response.engine_name,
        "engine_version": response.engine_version,
        "engine_commit": response.engine_commit,
        "engine_licence": response.engine_licence,
        "environment": response.environment,
        "resource_limits": response.resource_limits,
        "warnings": warnings,
        "raw_output_hash": raw_hash,
        "normalized_observation_hash": normalized_hash,
        "error_code": final_error,
        "unavailable_reason": response.unavailable_reason,
    }
    return GovernedExternalAnalysisRun(
        status=final_status,
        analysis_id=request.analysis_id,
        profile_id=request.profile_id,
        source_export_hashes=request.source_export_hashes,
        engine_name=response.engine_name,
        engine_version=response.engine_version,
        engine_commit=response.engine_commit,
        engine_licence=response.engine_licence,
        environment=response.environment,
        parameters=request.parameters,
        defaults=request.defaults,
        canonical_crs=request.canonical_crs,
        timezone=request.timezone,
        analysis_date=request.analysis_date,
        seed=request.seed,
        thread_policy=request.thread_policy,
        resource_limits=response.resource_limits,
        warnings=warnings,
        observations=observations,
        raw_output_hash=raw_hash,
        normalized_observation_hash=normalized_hash,
        run_fingerprint=_hash_json(run_payload),
        error_code=final_error,
        unavailable_reason=response.unavailable_reason,
    )


def run_governed_external_analysis(
    request: ExternalAnalysisRequest,
    adapter: ExternalAnalysisAdapter,
) -> GovernedExternalAnalysisRun:
    """Run one bounded adapter and always return a typed, complete result."""

    if not isinstance(request, ExternalAnalysisRequest):
        raise TypeError("request must be an ExternalAnalysisRequest")
    try:
        response = _coerce_response(adapter.run(request))
        if response.status is ExternalAnalysisStatus.COMPLETE:
            return _build_run(request, response)
        return _build_run(request, response)
    except TimeoutError as error:
        response = ExternalAnalysisResponse(
            status=ExternalAnalysisStatus.TIMEOUT,
            engine_name="unavailable",
            engine_version="unknown",
            engine_commit="unknown",
            engine_licence="unknown",
            warnings=(str(error) or "external adapter timed out",),
            unavailable_reason=str(error) or "external adapter timed out",
            error_code="adapter-timeout",
        )
        return _build_run(request, response, status=ExternalAnalysisStatus.TIMEOUT)
    except ExternalAnalysisUnavailableError as error:
        response = ExternalAnalysisResponse(
            status=ExternalAnalysisStatus.UNAVAILABLE,
            engine_name="unavailable",
            engine_version="unknown",
            engine_commit="unknown",
            engine_licence="unknown",
            warnings=(str(error) or "external adapter unavailable",),
            unavailable_reason=str(error) or "external adapter unavailable",
            error_code="engine-unavailable",
        )
        return _build_run(request, response, status=ExternalAnalysisStatus.UNAVAILABLE)
    except Exception as error:  # typed invalid output keeps the core compilation complete
        response = ExternalAnalysisResponse(
            status=ExternalAnalysisStatus.INVALID,
            engine_name="invalid",
            engine_version="unknown",
            engine_commit="unknown",
            engine_licence="unknown",
            warnings=(str(error) or "invalid external output",),
            unavailable_reason="external output was rejected",
            error_code="invalid-external-output",
        )
        return _build_run(request, response, status=ExternalAnalysisStatus.INVALID)


__all__ = [
    "DeterministicFixtureExternalAnalysisAdapter",
    "ExternalAnalysisAdapter",
    "ExternalAnalysisObservation",
    "ExternalAnalysisRequest",
    "ExternalAnalysisResponse",
    "ExternalAnalysisStatus",
    "ExternalAnalysisUnavailableError",
    "FixtureExternalAnalysisAdapter",
    "GovernedExternalAnalysisRun",
    "PinnedExportAnalysisAdapter",
    "PinnedExternalAnalysisAdapter",
    "run_governed_external_analysis",
]
