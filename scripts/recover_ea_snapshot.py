#!/usr/bin/env python3
"""Seal a clean EA recovery snapshot from an already governed corrected candidate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from satn.compilation_dependencies import compilation_dependency_manifest
from satn.ea_elevation import sha256_file
from satn.ea_fixed_point_convergence import EAFixedPointSnapshot
from satn.ea_fixed_point_operations import (
    _validated_candidate_status,
    run_ea_fixed_point_convergence,
)
from satn.ea_snapshot_recovery import (
    EARecoveryClosureProof,
    atomic_replace_recovery_configuration,
    create_recovery_prepared_configuration,
    preflight_recovery_output_family,
    promoted_recovery_configuration_bytes,
    reconcile_stationary_route_recovery,
    recovery_output_family,
    validate_recovery_output_family,
    validate_recovery_sampled_route_output,
    validate_recovery_target,
    verified_official_road_identity,
    write_recovery_record,
)
from satn.models import AreaDefinition, RetainedCoreSourceConfig, safe_snapshot_id
from satn.pipeline import compilation_governed_input_fingerprint
from satn.sources import (
    _validated_ea_snapshot_replay_inputs,
    promote_staged_snapshot,
    stage_ea_recovery_snapshot,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("candidate_network", type=Path)
    parser.add_argument("elevation_output", type=Path)
    parser.add_argument("target_snapshot_id")
    parser.add_argument("record", type=Path)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--max-iterations", type=int, default=3)
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
    target = config.source.snapshot_dir / target_snapshot_id
    prepared_config_path, convergence_record_path = _recovery_bridge_paths(
        config_path,
        record_path,
    )
    if record_path.exists():
        final_target = _complete_bounded_recovery(
            config_path=config_path,
            prepared_config_path=prepared_config_path,
            convergence_record_path=convergence_record_path,
            recovery_record_path=record_path,
            base_record={},
            max_iterations=args.max_iterations,
            expected_prepared_config_bytes=None,
        )
        print(config.source.snapshot_dir / final_target.name)
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

    prepared_config_bytes = promoted_recovery_configuration_bytes(
        original_config_bytes,
        target_snapshot_id=target_snapshot_id,
        parent_snapshot_id=parent_id,
        parent_manifest_sha256=parent_manifest_sha256,
        elevation_output=elevation_output,
        config_path=prepared_config_path,
    )
    if prepared_config_path.is_symlink():
        raise ValueError("EA recovery prepared configuration is unsafe")
    if not prepared_config_path.exists():
        create_recovery_prepared_configuration(
            prepared_config_path,
            prepared_config_bytes,
        )
    elif not prepared_config_path.is_file():
        raise ValueError("EA recovery prepared configuration is unsafe")

    output_family = recovery_output_family(elevation_output)
    existing_outputs = tuple(path.exists() or path.is_symlink() for path in output_family)
    if any(existing_outputs) and not all(existing_outputs):
        raise ValueError("EA snapshot recovery output is incomplete")
    if not all(existing_outputs):
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

    actual_eligible_route_fingerprint = status.get(
        "actual_eligible_route_fingerprint"
    )
    if not isinstance(actual_eligible_route_fingerprint, str):
        raise ValueError("EA recovery candidate actual fingerprint is invalid")
    elevation_output_sha256 = sha256_file(elevation_output)
    if target.exists():
        validate_recovery_target(
            target,
            target_snapshot_id=target_snapshot_id,
            parent_snapshot_id=parent_id,
            parent_manifest_sha256=parent_manifest_sha256,
            official_source_id=official_identity["source_id"],
            official_content_fingerprint=official_identity["content_fingerprint"],
            elevation_output_sha256=elevation_output_sha256,
            actual_eligible_route_fingerprint=actual_eligible_route_fingerprint,
        )
        target_manifest_sha256 = sha256_file(target / "snapshot.json")
    else:
        staged = stage_ea_recovery_snapshot(recovered)
        target_manifest_sha256 = sha256_file(staged.path / "snapshot.json")
        promote_staged_snapshot(staged)
        validate_recovery_target(
            target,
            target_snapshot_id=target_snapshot_id,
            parent_snapshot_id=parent_id,
            parent_manifest_sha256=parent_manifest_sha256,
            official_source_id=official_identity["source_id"],
            official_content_fingerprint=official_identity["content_fingerprint"],
            elevation_output_sha256=elevation_output_sha256,
            actual_eligible_route_fingerprint=actual_eligible_route_fingerprint,
        )

    record["status"] = "clean-baseline-staged"
    record["clean_baseline_target_manifest_sha256"] = target_manifest_sha256
    record["official_road_classification"] = official_identity
    final_target = _complete_bounded_recovery(
        config_path=config_path,
        prepared_config_path=prepared_config_path,
        convergence_record_path=convergence_record_path,
        recovery_record_path=record_path,
        base_record=record,
        max_iterations=args.max_iterations,
        expected_prepared_config_bytes=prepared_config_bytes,
    )
    print(config.source.snapshot_dir / final_target.name)
    print(record_path)


def _recovery_bridge_paths(
    config_path: Path,
    record_path: Path,
) -> tuple[Path, Path]:
    prepared = config_path.with_name(
        f".{config_path.name}.{record_path.stem}.ea-recovery-prepared.yaml"
    )
    convergence = record_path.with_name(
        f".{record_path.stem}.ea-fixed-point-convergence.json"
    )
    return prepared, convergence


def _regular_sha256(path: Path, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"EA recovery {label} is missing or unsafe")
    return sha256_file(path)


def _read_document(path: Path, label: str) -> dict[str, object]:
    _regular_sha256(path, label)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"EA recovery {label} is invalid JSON") from error
    if not isinstance(document, dict):
        raise ValueError(f"EA recovery {label} is invalid")
    return document


def _terminal_prepared_configuration_is_bound(
    prepared_config_path: Path,
    convergence_record_path: Path,
) -> bool:
    closure_path = convergence_record_path.with_name(
        f"{convergence_record_path.stem}.closure.json"
    )
    if not convergence_record_path.exists() or not closure_path.exists():
        return False
    closure = _read_document(closure_path, "convergence closure")
    return (
        closure.get("schema_version") == "ea-fixed-point-finalization/v1"
        and closure.get("convergence_record_sha256")
        == _regular_sha256(convergence_record_path, "convergence record")
        and closure.get("promoted_configuration_sha256")
        == _regular_sha256(prepared_config_path, "prepared configuration")
    )


def _completed_recovery_target(
    *,
    config_path: Path,
    prepared_config_path: Path,
    convergence_record_path: Path,
    recovery_record_path: Path,
    max_iterations: int,
) -> Path:
    record = _read_document(recovery_record_path, "governed record")
    bridge = record.get("bounded_convergence")
    if (
        record.get("schema_version") != "ea-snapshot-recovery/v1"
        or record.get("status") != "sealed"
        or not isinstance(bridge, dict)
        or bridge.get("schema_version")
        != "ea-recovery-bounded-convergence/v1"
    ):
        raise ValueError("EA recovery governed record is not a completed convergence")
    proof = EARecoveryClosureProof.from_record(record.get("fixed_point_closure"))
    if (
        record.get("target_snapshot_id") != proof.target_snapshot_id
        or record.get("target_manifest_sha256") != proof.target_manifest_sha256
    ):
        raise ValueError("EA recovery governed record target differs from its closure")
    terminal = run_ea_fixed_point_convergence(
        prepared_config_path,
        max_iterations=max_iterations,
        record_path=convergence_record_path,
        resume=True,
    )
    if (
        terminal.status != "converged"
        or terminal.final_snapshot.snapshot_id != proof.target_snapshot_id
        or terminal.final_snapshot.manifest_sha256 != proof.target_manifest_sha256
    ):
        raise ValueError("EA recovery terminal convergence result differs")
    closure_path = convergence_record_path.with_name(
        f"{convergence_record_path.stem}.closure.json"
    )
    if (
        _regular_sha256(convergence_record_path, "convergence record")
        != bridge.get("convergence_record_sha256")
        or _regular_sha256(closure_path, "convergence closure")
        != bridge.get("closure_record_sha256")
        or _regular_sha256(prepared_config_path, "prepared configuration")
        != bridge.get("final_configuration_sha256")
    ):
        raise ValueError("EA recovery bounded convergence identity differs")
    closure = _read_document(closure_path, "convergence closure")
    if (
        closure.get("schema_version") != "ea-fixed-point-finalization/v1"
        or closure.get("convergence_record_sha256")
        != bridge.get("convergence_record_sha256")
        or closure.get("promoted_configuration_sha256")
        != bridge.get("final_configuration_sha256")
        or closure.get("fixed_point_closure") != proof.record()
    ):
        raise ValueError("EA recovery convergence closure differs")
    original_sha256 = bridge.get("original_configuration_sha256")
    final_sha256 = bridge.get("final_configuration_sha256")
    current_sha256 = _regular_sha256(config_path, "main configuration")
    if current_sha256 == final_sha256:
        return Path(proof.target_snapshot_id)
    if current_sha256 != original_sha256:
        raise ValueError("EA recovery main configuration changed outside the bridge")
    atomic_replace_recovery_configuration(
        config_path,
        prepared_config_path.read_bytes(),
    )
    return Path(proof.target_snapshot_id)


def _complete_bounded_recovery(
    *,
    config_path: Path,
    prepared_config_path: Path,
    convergence_record_path: Path,
    recovery_record_path: Path,
    base_record: dict[str, object],
    max_iterations: int,
    expected_prepared_config_bytes: bytes | None,
) -> Path:
    if recovery_record_path.exists() or recovery_record_path.is_symlink():
        return _completed_recovery_target(
            config_path=config_path,
            prepared_config_path=prepared_config_path,
            convergence_record_path=convergence_record_path,
            recovery_record_path=recovery_record_path,
            max_iterations=max_iterations,
        )
    if expected_prepared_config_bytes is None:
        raise ValueError("EA recovery expected prepared configuration is missing")
    _regular_sha256(prepared_config_path, "prepared configuration")
    if prepared_config_path.read_bytes() != expected_prepared_config_bytes and not (
        _terminal_prepared_configuration_is_bound(
            prepared_config_path,
            convergence_record_path,
        )
    ):
        raise ValueError("EA recovery prepared configuration identity differs")
    original_configuration_sha256 = _regular_sha256(
        config_path,
        "main configuration",
    )
    result = run_ea_fixed_point_convergence(
        prepared_config_path,
        max_iterations=max_iterations,
        record_path=convergence_record_path,
        resume=convergence_record_path.exists(),
    )
    if result.status != "converged":
        raise ValueError("EA recovery bounded fixed-point run did not converge")
    closure_path = convergence_record_path.with_name(
        f"{convergence_record_path.stem}.closure.json"
    )
    closure = _read_document(closure_path, "convergence closure")
    proof = EARecoveryClosureProof.from_record(closure.get("fixed_point_closure"))
    final_snapshot = result.final_snapshot
    if (
        proof.target_snapshot_id != final_snapshot.snapshot_id
        or proof.target_manifest_sha256 != final_snapshot.manifest_sha256
    ):
        raise ValueError("EA recovery convergence result differs from its closure")
    convergence_sha256 = _regular_sha256(
        convergence_record_path,
        "convergence record",
    )
    final_configuration_sha256 = _regular_sha256(
        prepared_config_path,
        "prepared configuration",
    )
    if (
        closure.get("schema_version") != "ea-fixed-point-finalization/v1"
        or closure.get("convergence_record_sha256") != convergence_sha256
        or closure.get("promoted_configuration_sha256")
        != final_configuration_sha256
    ):
        raise ValueError("EA recovery convergence closure is invalid")
    final_record = {
        **base_record,
        "status": "sealed",
        "target_snapshot_id": proof.target_snapshot_id,
        "target_manifest_sha256": proof.target_manifest_sha256,
        "fixed_point_closure": proof.record(),
        "bounded_convergence": {
            "schema_version": "ea-recovery-bounded-convergence/v1",
            "convergence_record_sha256": convergence_sha256,
            "closure_record_sha256": _regular_sha256(
                closure_path,
                "convergence closure",
            ),
            "original_configuration_sha256": original_configuration_sha256,
            "final_configuration_sha256": final_configuration_sha256,
        },
    }
    write_recovery_record(recovery_record_path, final_record)
    return _completed_recovery_target(
        config_path=config_path,
        prepared_config_path=prepared_config_path,
        convergence_record_path=convergence_record_path,
        recovery_record_path=recovery_record_path,
        max_iterations=max_iterations,
    )


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


if __name__ == "__main__":
    main()
