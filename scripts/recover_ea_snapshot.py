#!/usr/bin/env python3
"""Seal a clean EA recovery snapshot from an already governed corrected candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from satn.compilation_dependencies import compilation_dependency_manifest
from satn.ea_elevation import sha256_file
from satn.ea_fixed_point_convergence import EAFixedPointSnapshot
from satn.ea_fixed_point_operations import _validated_candidate_status
from satn.ea_snapshot_recovery import (
    preflight_recovery_output_family,
    promote_recovery_transaction,
    reconcile_stationary_route_recovery,
    recovery_transaction_artifact,
    recovery_transaction_plan,
    validate_recovery_output_family,
    validate_recovery_sampled_route_output,
    verified_official_road_identity,
)
from satn.models import AreaDefinition, RetainedCoreSourceConfig, safe_snapshot_id
from satn.pipeline import compilation_governed_input_fingerprint
from satn.sources import _validated_ea_snapshot_replay_inputs, stage_retained_core_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("candidate_network", type=Path)
    parser.add_argument("elevation_output", type=Path)
    parser.add_argument("target_snapshot_id")
    parser.add_argument("record", type=Path)
    parser.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    config_path = _contained(args.config, project_root, "configuration")
    candidate_network = _contained(
        args.candidate_network, project_root, "candidate network"
    )
    elevation_output = _contained(
        args.elevation_output, project_root, "elevation output"
    )
    record_path = _contained(args.record, project_root, "recovery record")
    cache_dir = _contained(args.cache_dir, project_root, "EA cache")
    target_snapshot_id = safe_snapshot_id(
        args.target_snapshot_id,
        field_name="EA recovery target snapshot identifier",
    )
    config = AreaDefinition.from_yaml(config_path)
    original_config_bytes = config_path.read_bytes()
    original_config_sha256 = hashlib.sha256(original_config_bytes).hexdigest()
    target = config.source.snapshot_dir / target_snapshot_id
    existing_plan = recovery_transaction_plan(record_path)
    if config.source.snapshot_id == target_snapshot_id and existing_plan is not None:
        _resume_completed_recovery(
            config=config,
            config_path=config_path,
            config_bytes=original_config_bytes,
            target=target,
            record_path=record_path,
            plan=existing_plan,
        )
        print(target)
        print(record_path)
        return
    parent_id = config.source.snapshot_id
    parent_dir = config.source.snapshot_dir / parent_id
    parent_manifest_path = parent_dir / "snapshot.json"
    parent_manifest_sha256 = sha256_file(parent_manifest_path)
    retained_routes = parent_dir / "ea-elevation-sampled-routes.geojson"
    record = reconcile_stationary_route_recovery(
        retained_routes,
        candidate_network,
        parent_snapshot_id=parent_id,
        parent_manifest_sha256=parent_manifest_sha256,
        target_snapshot_id=target_snapshot_id,
    )
    governed_official = config.source.official_road_classification
    if governed_official is None:
        raise ValueError(
            "EA recovery requires configured official-road classification"
        )
    official_identity = verified_official_road_identity(
        parent_dir,
        parent_manifest_sha256=parent_manifest_sha256,
        governed=governed_official,
    )
    parent_manifest = json.loads(parent_manifest_path.read_text(encoding="utf-8"))
    snapshot = EAFixedPointSnapshot(
        snapshot_id=parent_id,
        manifest_sha256=parent_manifest_sha256,
        primary_fingerprint=parent_manifest["evidence_sources"]["elevation"][
            "pre_elevation_network_sha256"
        ],
        retained_sample_routes=retained_routes,
        route_inventory=("invalid-v10-supplement-explicitly-excluded",),
        governed_source_identities=tuple(
            parent_manifest["provenance_file_sha256"].items()
        ),
    )
    status = _validated_candidate_status(
        config,
        snapshot,
        candidate_network.parent,
    )
    _validate_recovery_transaction_candidate_fingerprint(config, status)

    recovered = config.model_copy(deep=True)
    recovered.source.snapshot_id = target_snapshot_id
    recovered.source.retained_core_source = RetainedCoreSourceConfig(
        snapshot_id=parent_id,
        manifest_sha256=parent_manifest_sha256,
    )
    if recovered.source.national_elevation is None:
        raise ValueError("EA recovery requires configured national elevation")
    recovered.source.national_elevation.path = elevation_output

    staged = None
    target_manifest_sha256: str | None = None
    resumed = recovery_transaction_artifact(record_path, target=target)
    if resumed is not None:
        staged, target_manifest_sha256 = resumed
    elif target.exists():
        raise ValueError(
            "EA snapshot recovery target exists without its transaction journal; "
            "refusing ambiguous ownership"
        )
    else:
        preflight_recovery_output_family(elevation_output)
        replay = _validated_ea_snapshot_replay_inputs(parent_dir)
        command = (
            sys.executable,
            str(project_root / "scripts" / "acquire_ea_elevation.py"),
            str(candidate_network),
            str(elevation_output),
            "--cache-dir",
            str(cache_dir),
            "--spacing-m",
            "10",
            "--authority-boundaries",
            str(replay["authority_boundaries"]),
            "--survey-index",
            str(replay["survey_index"]),
            "--weca-preflight",
            "--routing-buffer-m",
            "15000",
            "--governed-input-fingerprint",
            status["governed_input_fingerprint"],
        )
        completed = subprocess.run(command, cwd=project_root, check=False)
        if completed.returncode != 0:
            raise ValueError(
                f"EA clean-baseline recovery acquisition failed: {completed.returncode}"
            )
        validate_recovery_output_family(elevation_output)
        sampled = elevation_output.with_name(
            f"{elevation_output.stem}.sampled-routes.geojson"
        )
        validate_recovery_sampled_route_output(sampled)
        staged = stage_retained_core_snapshot(recovered)
        target_manifest_sha256 = sha256_file(staged.path / "snapshot.json")

    if target_manifest_sha256 is None:  # pragma: no cover - branches establish it.
        raise ValueError("EA recovery target manifest identity is unavailable")
    record["status"] = "sealed"
    record["target_manifest_sha256"] = target_manifest_sha256
    record["official_road_classification"] = official_identity
    promoted_config_bytes = _promoted_configuration_bytes(
        original_config_bytes,
        target_snapshot_id=target_snapshot_id,
        parent_snapshot_id=parent_id,
        parent_manifest_sha256=parent_manifest_sha256,
        elevation_output=elevation_output,
        config_path=config_path,
    )
    promote_recovery_transaction(
        staged_snapshot=staged,
        target=target,
        record_path=record_path,
        config_path=config_path,
        expected_config_sha256=original_config_sha256,
        promoted_config_bytes=promoted_config_bytes,
        record=record,
        parent_snapshot_id=parent_id,
        parent_manifest_sha256=parent_manifest_sha256,
        official_source_id=official_identity["source_id"],
        official_content_fingerprint=official_identity["content_fingerprint"],
    )
    print(target)
    print(record_path)


def _validate_recovery_transaction_candidate_fingerprint(
    config: AreaDefinition,
    status: dict[str, object],
) -> str:
    """Bind recovery transaction input to the recovery compiler path."""

    dependency_manifest = compilation_dependency_manifest(
        config,
        compiler_path="ea-recovery",
    )
    expected = compilation_governed_input_fingerprint(
        config,
        dependency_manifest=dependency_manifest,
    )
    if status.get("governed_input_fingerprint") != expected:
        raise ValueError("EA recovery candidate governed-input fingerprint is stale")
    return expected


def _contained(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"EA recovery {label} escapes the project root")
    return resolved


def _promoted_configuration_bytes(
    original: bytes,
    *,
    target_snapshot_id: str,
    parent_snapshot_id: str,
    parent_manifest_sha256: str,
    elevation_output: Path,
    config_path: Path,
) -> bytes:
    """Update only recovery source scalars while preserving the Area YAML layout."""

    text = original.decode("utf-8")
    relative_elevation = Path(
        os.path.relpath(elevation_output, config_path.parent)
    ).as_posix()
    replacements = {
        ("source", "snapshot_id"): target_snapshot_id,
        ("source", "retained_core_source", "snapshot_id"): parent_snapshot_id,
        (
            "source",
            "retained_core_source",
            "manifest_sha256",
        ): parent_manifest_sha256,
        ("source", "national_elevation", "path"): relative_elevation,
    }
    lines = text.splitlines(keepends=True)
    stack: list[tuple[int, str]] = []
    found: set[tuple[str, ...]] = set()
    for index, line in enumerate(lines):
        stripped = line.lstrip(" ")
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        indentation = len(line) - len(stripped)
        key, remainder = stripped.split(":", 1)
        while stack and stack[-1][0] >= indentation:
            stack.pop()
        path = (*[item[1] for item in stack], key)
        if path in replacements:
            if path in found:
                raise ValueError(
                    f"EA recovery configuration repeats {'.'.join(path)}"
                )
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = (
                f"{' ' * indentation}{key}: "
                f"{json.dumps(replacements[path])}{newline}"
            )
            found.add(path)
        if not remainder.strip():
            stack.append((indentation, key))
    if found != set(replacements):
        missing = ", ".join(".".join(path) for path in set(replacements) - found)
        raise ValueError(f"EA recovery configuration lacks required fields: {missing}")
    promoted = "".join(lines).encode()
    parsed = yaml.safe_load(promoted)
    if (
        not isinstance(parsed, dict)
        or parsed.get("source", {}).get("snapshot_id") != target_snapshot_id
        or parsed.get("source", {}).get("retained_core_source")
        != {
            "snapshot_id": parent_snapshot_id,
            "manifest_sha256": parent_manifest_sha256,
        }
        or parsed.get("source", {}).get("national_elevation", {}).get("path")
        != relative_elevation
    ):
        raise ValueError("EA recovery promoted configuration is invalid")
    return promoted


def _resume_completed_recovery(
    *,
    config: AreaDefinition,
    config_path: Path,
    config_bytes: bytes,
    target: Path,
    record_path: Path,
    plan: dict[str, object],
) -> None:
    """Validate and replay a transaction whose configuration already names v11."""

    parent_snapshot_id = plan.get("parent_snapshot_id")
    parent_manifest_sha256 = plan.get("parent_manifest_sha256")
    expected_config_sha256 = plan.get("expected_configuration_sha256")
    promoted_config_sha256 = plan.get("promoted_configuration_sha256")
    if (
        not isinstance(parent_snapshot_id, str)
        or not isinstance(parent_manifest_sha256, str)
        or not isinstance(expected_config_sha256, str)
        or not isinstance(promoted_config_sha256, str)
        or hashlib.sha256(config_bytes).hexdigest() != promoted_config_sha256
    ):
        raise ValueError("EA recovery completed transaction identity differs")
    lineage = config.source.retained_core_source
    if (
        lineage is None
        or lineage.snapshot_id != parent_snapshot_id
        or lineage.manifest_sha256 != parent_manifest_sha256
    ):
        raise ValueError("EA recovery completed configuration lineage differs")
    governed = config.source.official_road_classification
    if governed is None:
        raise ValueError(
            "EA recovery requires configured official-road classification"
        )
    parent = config.source.snapshot_dir / parent_snapshot_id
    official_identity = verified_official_road_identity(
        parent,
        parent_manifest_sha256=parent_manifest_sha256,
        governed=governed,
    )
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("EA recovery completed record is unreadable") from error
    if not isinstance(record, dict):
        raise ValueError("EA recovery completed record is invalid")
    artifact = recovery_transaction_artifact(record_path, target=target)
    if artifact is None:
        raise ValueError("EA recovery completed transaction journal is missing")
    staged, _target_manifest_sha256 = artifact
    promote_recovery_transaction(
        staged_snapshot=staged,
        target=target,
        record_path=record_path,
        config_path=config_path,
        expected_config_sha256=expected_config_sha256,
        promoted_config_bytes=config_bytes,
        record=record,
        parent_snapshot_id=parent_snapshot_id,
        parent_manifest_sha256=parent_manifest_sha256,
        official_source_id=official_identity["source_id"],
        official_content_fingerprint=official_identity["content_fingerprint"],
    )


if __name__ == "__main__":
    main()
