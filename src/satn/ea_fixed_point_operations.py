"""Production operations for bounded EA elevation fixed-point convergence."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path

import geopandas as gpd
import pandas as pd

import satn.compiler as compiler
from satn.content_identity import canonical_network_geometry_fingerprint
from satn.ea_elevation import (
    ELIGIBLE_FEATURE_TYPES,
    eligible_route_fingerprint,
    fixed_point_route_fingerprint,
)
from satn.ea_fixed_point_convergence import (
    EAFixedPointAcquisition,
    EAFixedPointCompilation,
    EAFixedPointConvergenceResult,
    EAFixedPointSnapshot,
    EAFixedPointSnapshotCreation,
    converge_ea_fixed_point,
)
from satn.models import AreaConfig, AreaDefinition, RetainedCoreSourceConfig
from satn.pipeline import compile as compile_satn
from satn.publisher import (
    EA_FIXED_POINT_CANDIDATE_NETWORK,
    EA_FIXED_POINT_CANDIDATE_SCHEMA_VERSION,
    EA_FIXED_POINT_CANDIDATE_STATUS,
    _ea_fixed_point_candidate_path,
    _ea_fixed_point_next_step,
)
from satn.sources import _validated_ea_snapshot_replay_inputs
from satn.sources import snapshot as create_snapshot

_COMPILER_TIMING_LOCK = threading.Lock()
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sample_route_inventory(path: Path) -> tuple[str, ...]:
    routes = gpd.read_file(path)
    if routes.crs is None:
        raise ValueError("EA retained sampled routes require a CRS")
    metric = routes.to_crs(27700)
    identities: set[str] = set()
    for position, row in metric.iterrows():
        if (
            row.get("feature_type") not in ELIGIBLE_FEATURE_TYPES
            or pd.isna(row.get("topography_profile_id"))
            or row.geometry is None
            or row.geometry.is_empty
        ):
            continue
        feature_id = str(row.get("feature_id") or row.get("id") or position)
        try:
            identities.add(
                canonical_network_geometry_fingerprint(row.geometry, metric.crs)
            )
        except ValueError as error:
            raise ValueError(
                "EA retained sampled route "
                f"{feature_id!r} in {path} {error}; "
                "regenerate the candidate network and elevation snapshot"
            ) from error
    if not identities:
        raise ValueError("EA retained sampled routes contain no eligible routes")
    return tuple(sorted(identities))


def _snapshot_state(
    config: AreaConfig,
    *,
    expected_parent: EAFixedPointSnapshot | None = None,
) -> EAFixedPointSnapshot:
    snapshot_dir = config.source.snapshot_dir / config.source.snapshot_id
    replay_inputs = _validated_ea_snapshot_replay_inputs(snapshot_dir)
    manifest_path = snapshot_dir / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        primary = manifest["evidence_sources"]["elevation"][
            "pre_elevation_network_sha256"
        ]
    except (KeyError, TypeError) as error:
        raise ValueError(
            "EA convergence snapshot lacks its primary route fingerprint"
        ) from error
    governed_files = manifest.get("provenance_file_sha256")
    if not isinstance(governed_files, dict):
        raise ValueError("EA convergence snapshot lacks governed source identities")
    parent_id: str | None = None
    parent_digest: str | None = None
    lineage = manifest.get("retained_core_lineage")
    if lineage is not None:
        if not isinstance(lineage, dict):
            raise ValueError("EA convergence snapshot lineage is invalid")
        parent_id = lineage.get("source_snapshot_id")
        parent_digest = lineage.get("source_manifest_sha256")
    state = EAFixedPointSnapshot(
        snapshot_id=config.source.snapshot_id,
        manifest_sha256=_sha256_file(manifest_path),
        primary_fingerprint=primary,
        retained_sample_routes=replay_inputs["sample_routes"],
        route_inventory=_sample_route_inventory(replay_inputs["sample_routes"]),
        governed_source_identities=tuple(governed_files.items()),
        parent_snapshot_id=parent_id,
        parent_manifest_sha256=parent_digest,
    )
    if expected_parent is not None and (
        state.parent_snapshot_id != expected_parent.snapshot_id
        or state.parent_manifest_sha256 != expected_parent.manifest_sha256
    ):
        raise ValueError("EA convergence snapshot does not preserve exact parent lineage")
    return state


@contextmanager
def _compiler_stage_timings() -> Iterator[dict[str, int]]:
    timings = {"urban_access_ms": 0, "topography_ms": 0}
    originals = {
        "assess_urban_community_access": compiler.assess_urban_community_access,
        "assess_urban_school_access": compiler.assess_urban_school_access,
        "build_topography_profiles": compiler.build_topography_profiles,
    }

    def measured(name: str, function: Callable[..., object]) -> Callable[..., object]:
        @wraps(function)
        def wrapper(*args: object, **kwargs: object) -> object:
            started = time.perf_counter_ns()
            try:
                return function(*args, **kwargs)
            finally:
                key = (
                    "topography_ms"
                    if name == "build_topography_profiles"
                    else "urban_access_ms"
                )
                timings[key] += (time.perf_counter_ns() - started) // 1_000_000

        return wrapper

    with _COMPILER_TIMING_LOCK:
        try:
            for name, function in originals.items():
                setattr(compiler, name, measured(name, function))
            yield timings
        finally:
            for name, function in originals.items():
                setattr(compiler, name, function)


class EAFixedPointProductionOperations:
    """Execute compile, acquisition and immutable snapshot operations locally."""

    def __init__(self, config: AreaConfig, *, run_token: str) -> None:
        self._configs = {config.source.snapshot_id: config}
        self._base_config = config.model_copy(deep=True)
        self._base_snapshot_id = config.source.snapshot_id
        self._run_token = run_token

    def initial_snapshot(self) -> EAFixedPointSnapshot:
        return _snapshot_state(next(iter(self._configs.values())))

    def restore(self, snapshot: EAFixedPointSnapshot) -> None:
        config = self._base_config.model_copy(deep=True)
        config.source.snapshot_id = snapshot.snapshot_id
        if snapshot.parent_snapshot_id is not None:
            config.source.retained_core_source = RetainedCoreSourceConfig(
                snapshot_id=snapshot.parent_snapshot_id,
                manifest_sha256=snapshot.parent_manifest_sha256,
            )
        restored = _snapshot_state(config)
        if restored != snapshot:
            raise ValueError(
                "EA convergence checkpoint differs from its immutable snapshot"
            )
        self._configs[snapshot.snapshot_id] = config

    def compile(self, snapshot: EAFixedPointSnapshot) -> EAFixedPointCompilation:
        config = self._config(snapshot)
        config.compilation.full = True
        candidate = _ea_fixed_point_candidate_path(config)
        status_path = candidate / EA_FIXED_POINT_CANDIDATE_STATUS
        before = status_path.read_bytes() if status_path.is_file() else None
        with _compiler_stage_timings() as timings:
            try:
                result = compile_satn(config)
            except ValueError:
                after = status_path.read_bytes() if status_path.is_file() else None
                if after is None or after == before:
                    raise
                status = _validated_candidate_status(config, snapshot, candidate)
                return EAFixedPointCompilation(
                    expected_fingerprint=status[
                        "expected_eligible_route_fingerprint"
                    ],
                    actual_fingerprint=status["actual_eligible_route_fingerprint"],
                    candidate_network=candidate / EA_FIXED_POINT_CANDIDATE_NETWORK,
                    urban_access_ms=timings["urban_access_ms"],
                    topography_ms=timings["topography_ms"],
                    acquisition_command=tuple(
                        shlex.split(status["next_step_command"])
                    ),
                )
        if result.status not in {"reviewable", "complete"}:
            raise ValueError(
                "EA convergence compile requires a publishable result, "
                f"not {result.status}"
            )
        network = result.output_dir / "network.geojson"
        actual = fixed_point_route_fingerprint(gpd.read_file(network))
        return EAFixedPointCompilation(
            expected_fingerprint=snapshot.primary_fingerprint,
            actual_fingerprint=actual,
            candidate_network=network,
            urban_access_ms=timings["urban_access_ms"],
            topography_ms=timings["topography_ms"],
        )

    def acquire(
        self,
        snapshot: EAFixedPointSnapshot,
        compilation: EAFixedPointCompilation,
    ) -> EAFixedPointAcquisition:
        if not compilation.acquisition_command:
            raise ValueError("EA mismatch candidate has no governed acquisition command")
        evidence_path = _validated_acquisition_command(
            self._config(snapshot),
            snapshot,
            compilation,
        )
        completed = subprocess.run(
            compilation.acquisition_command,
            cwd=_PROJECT_ROOT,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError(
                "EA governed acquisition failed "
                f"with exit status {completed.returncode}"
            )
        sampled_routes = evidence_path.with_name(
            f"{evidence_path.stem}.sampled-routes.geojson"
        )
        primary = fixed_point_route_fingerprint(gpd.read_file(sampled_routes))
        return EAFixedPointAcquisition(
            primary_fingerprint=primary,
            route_inventory=_sample_route_inventory(sampled_routes),
            evidence_path=evidence_path,
        )

    def snapshot(
        self,
        previous: EAFixedPointSnapshot,
        acquisition: EAFixedPointAcquisition,
        iteration: int,
    ) -> EAFixedPointSnapshotCreation:
        previous_config = self._config(previous)
        config = previous_config.model_copy(deep=True)
        config.source.snapshot_id = (
            f"{self._base_snapshot_id}-fp-{self._run_token}-{iteration:02d}"
        )
        config.source.retained_core_source = RetainedCoreSourceConfig(
            snapshot_id=previous.snapshot_id,
            manifest_sha256=previous.manifest_sha256,
        )
        if (
            config.source.national_elevation is None
            or config.source.national_elevation.path is None
        ):
            raise ValueError("EA convergence requires local national elevation evidence")
        config.source.national_elevation.path = acquisition.evidence_path
        target = config.source.snapshot_dir / config.source.snapshot_id
        snapshot_seal_ms = 0
        if not target.exists():
            seal_started = time.perf_counter_ns()
            create_snapshot(config, retain_core=True)
            snapshot_seal_ms = (
                time.perf_counter_ns() - seal_started
            ) // 1_000_000
        validation_started = time.perf_counter_ns()
        state = _snapshot_state(config, expected_parent=previous)
        snapshot_validation_ms = (
            time.perf_counter_ns() - validation_started
        ) // 1_000_000
        if (
            state.primary_fingerprint != acquisition.primary_fingerprint
            or state.route_inventory != acquisition.route_inventory
        ):
            raise ValueError(
                "EA convergence target snapshot exists with different acquisition evidence"
            )
        self._configs[state.snapshot_id] = config
        return EAFixedPointSnapshotCreation(
            snapshot=state,
            snapshot_seal_ms=snapshot_seal_ms,
            snapshot_validation_ms=snapshot_validation_ms,
        )

    def _config(self, snapshot: EAFixedPointSnapshot) -> AreaConfig:
        try:
            return self._configs[snapshot.snapshot_id]
        except KeyError as error:
            raise ValueError(
                f"unknown EA convergence snapshot: {snapshot.snapshot_id}"
            ) from error


def _validated_acquisition_command(
    config: AreaConfig,
    snapshot: EAFixedPointSnapshot,
    compilation: EAFixedPointCompilation,
) -> Path:
    command = compilation.acquisition_command
    if len(command) != 21 or command[:4] != (
        "uv",
        "run",
        "python",
        "scripts/acquire_ea_elevation.py",
    ):
        raise ValueError("EA governed acquisition command is malformed")
    if (
        command[6] != "--cache-dir"
        or command[8:11] != ("--spacing-m", "10", "--authority-boundaries")
        or command[12] != "--survey-index"
        or command[14:18]
        != (
            "--weca-preflight",
            "--routing-buffer-m",
            "15000",
            "--governed-input-fingerprint",
        )
        or command[19] != "--supplemental-routes"
    ):
        raise ValueError("EA governed acquisition command is malformed")
    candidate_path = _path_within_project(command[4], "candidate network")
    evidence_path = _path_within_project(command[5], "elevation output")
    cache_path = _path_within_project(command[7], "elevation cache")
    authority_path = _path_within_project(command[11], "authority boundaries")
    survey_path = _path_within_project(command[13], "survey index")
    sample_routes = _path_within_project(command[20], "supplemental routes")
    if candidate_path != compilation.candidate_network.resolve():
        raise ValueError("EA governed acquisition candidate path is not the compiled candidate")
    expected_candidate_root = _ea_fixed_point_candidate_path(config)
    if candidate_path.parent != expected_candidate_root:
        raise ValueError("EA governed acquisition candidate path escapes its governed root")
    elevation = config.source.national_elevation
    if elevation is None or elevation.path is None:
        raise ValueError("EA convergence requires local national elevation evidence")
    if evidence_path != elevation.path.resolve():
        raise ValueError(
            "EA governed acquisition output differs from configured elevation evidence"
        )
    if cache_path != evidence_path.parent / "ea-dtm-cache":
        raise ValueError("EA governed acquisition cache path is not reproducible")
    replay_inputs = _validated_ea_snapshot_replay_inputs(
        config.source.snapshot_dir / snapshot.snapshot_id
    )
    if (
        authority_path != replay_inputs["authority_boundaries"].resolve()
        or survey_path != replay_inputs["survey_index"].resolve()
        or sample_routes != replay_inputs["sample_routes"].resolve()
    ):
        raise ValueError("EA governed acquisition replay paths are not reproducible")
    if (
        len(command[18]) != 64
        or any(character not in "0123456789abcdef" for character in command[18])
    ):
        raise ValueError("EA governed acquisition fingerprint is malformed")
    if not candidate_path.is_file():
        raise ValueError("EA governed acquisition candidate network is missing")
    return evidence_path


def _path_within_project(value: str, name: str) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to(_PROJECT_ROOT):
        raise ValueError(f"EA governed acquisition {name} path escapes the project root")
    return path


def _validated_candidate_status(
    config: AreaConfig,
    snapshot: EAFixedPointSnapshot,
    candidate: Path,
) -> dict[str, str]:
    status_path = candidate / EA_FIXED_POINT_CANDIDATE_STATUS
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("EA mismatch candidate status is unreadable") from error
    required = {
        "schema_version": EA_FIXED_POINT_CANDIDATE_SCHEMA_VERSION,
        "status": "eligible-route-mismatch",
        "area_id": config.area_id,
        "snapshot_id": snapshot.snapshot_id,
        "expected_eligible_route_fingerprint": snapshot.primary_fingerprint,
        "next_step_status": "ea-acquisition-ready",
        "candidate_network_path": EA_FIXED_POINT_CANDIDATE_NETWORK,
    }
    if not isinstance(status, dict) or any(
        status.get(key) != value for key, value in required.items()
    ):
        raise ValueError("EA mismatch candidate status is not governed for this snapshot")
    network = candidate / EA_FIXED_POINT_CANDIDATE_NETWORK
    if (
        not network.is_file()
        or status.get("candidate_network_sha256") != _sha256_file(network)
        or not isinstance(status.get("actual_eligible_route_fingerprint"), str)
        or not isinstance(status.get("next_step_command"), str)
    ):
        raise ValueError("EA mismatch candidate content identity is invalid")
    if (
        eligible_route_fingerprint(gpd.read_file(network))
        != status["actual_eligible_route_fingerprint"]
    ):
        raise ValueError("EA mismatch candidate route fingerprint is invalid")
    governed_input_fingerprint = status.get("governed_input_fingerprint")
    if not isinstance(governed_input_fingerprint, str):
        raise ValueError("EA mismatch candidate governed input identity is invalid")
    governed_next_step = _ea_fixed_point_next_step(
        config,
        network,
        validation_network=network,
        governed_input_fingerprint=governed_input_fingerprint,
    )
    if any(status.get(key) != value for key, value in governed_next_step.items()):
        raise ValueError("EA mismatch candidate acquisition command is not reproducible")
    return status


def run_ea_fixed_point_convergence(
    config_path: Path,
    *,
    max_iterations: int,
    record_path: Path | None = None,
    resume: bool = False,
) -> EAFixedPointConvergenceResult:
    config = AreaDefinition.from_yaml(config_path)
    configuration_identity = _sha256_file(config_path.resolve())
    if resume:
        if record_path is None:
            raise ValueError("EA fixed-point resume requires an explicit record path")
        try:
            checkpoint = json.loads(record_path.read_text(encoding="utf-8"))
            run_token = checkpoint["run_token"]
        except (KeyError, TypeError, json.JSONDecodeError, OSError) as error:
            raise ValueError("EA fixed-point resume record is unreadable") from error
        if not isinstance(run_token, str):
            raise ValueError("EA fixed-point resume run token is invalid")
    else:
        run_token = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    operations = EAFixedPointProductionOperations(config, run_token=run_token)
    initial = None if resume else operations.initial_snapshot()
    record = record_path or (
        config.publication.output_dir.parent
        / ".satn-ea-fixed-point-convergence"
        / f"{config.area_id}-{run_token}.json"
    )
    return converge_ea_fixed_point(
        initial,
        operations=operations,
        max_iterations=max_iterations,
        record_path=record,
        run_token=run_token,
        resume=resume,
        configuration_identity=configuration_identity,
    )
