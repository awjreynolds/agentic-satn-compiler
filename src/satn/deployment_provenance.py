"""Immutable provenance locks for independently published Area Deployments.

The lock deliberately binds two different things: the validated compiler
inputs/run and the *derived, public deployment* that is actually shipped.  It
does not pretend that the unfiltered compiler GeoJSON is the public network.
``provenance-lock.json`` and ``review-map.zip`` are the two documented cyclic
artefacts and are therefore excluded from its own file digest table.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from satn.models import AreaConfig, canonical_decision_ledger_payload
from satn.pipeline import (
    area_definition_sha256,
    compilation_governed_input_fingerprint,
    decision_ledger_input_fingerprint,
    snapshot_manifest_sha256,
)
from satn.publisher import validate_publication

LOCK_NAME = "provenance-lock.json"
SCHEMA_VERSION = "satn-deployment-provenance-lock/v2"
PUBLISHABLE_STATUSES = {"complete", "reviewable"}
CYCLIC_RUNTIME_FILES = frozenset({LOCK_NAME, "review-map.zip"})


def lock_path(definition: AreaConfig) -> Path:
    return definition.config_path.parent / LOCK_NAME


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _exact_digest(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid {label}: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object: {path}")
    return value


def _run_contract(definition: AreaConfig, run: dict[str, Any]) -> dict[str, str]:
    try:
        ledger = canonical_decision_ledger_payload(run["decision_ledger_input"])
        accepted = canonical_decision_ledger_payload(
            {"decision_contract": run["decision_contract"], "responses": run["accepted_decisions"]}
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("compiler run has an invalid decision provenance contract") from error
    if ledger.decision_contract != run["decision_contract"] or accepted.model_dump(mode="json")[
        "responses"
    ] != run["accepted_decisions"]:
        raise ValueError("compiler run has a non-canonical accepted-decision contract")
    governed = compilation_governed_input_fingerprint(definition)
    expected = decision_ledger_input_fingerprint(governed, ledger)
    contract = {
        "area_definition_sha256": area_definition_sha256(definition),
        "snapshot_manifest_sha256": snapshot_manifest_sha256(definition),
        "governed_input_fingerprint": governed,
        "decision_ledger_input_sha256": hashlib.sha256(
            json.dumps(run["decision_ledger_input"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "decision_contract_sha256": hashlib.sha256(
            json.dumps(run["decision_contract"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "accepted_decisions_sha256": hashlib.sha256(
            json.dumps(run["accepted_decisions"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "compilation_input_fingerprint": expected,
    }
    for key, expected_value in contract.items():
        if key.endswith("sha256") or key.endswith("fingerprint"):
            actual = _exact_digest(run.get(key), key) if key in run else expected_value
            if key in {"decision_contract_sha256", "accepted_decisions_sha256"}:
                continue
            if actual != expected_value:
                if key == "snapshot_manifest_sha256":
                    raise ValueError("compiled output was produced from a stale snapshot manifest")
                raise ValueError(f"compiler run {key} is stale or inconsistent")
    return contract


def runtime_artifact_digests(deployment: Path) -> dict[str, dict[str, object]]:
    """Return the complete public runtime file set, excluding documented cycles."""
    if deployment.is_symlink() or not deployment.is_dir():
        raise ValueError(f"deployment directory is missing or unsafe: {deployment}")
    artifacts: dict[str, dict[str, object]] = {}
    for item in sorted(deployment.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"deployment must not contain symlinks: {item}")
        if not item.is_file():
            continue
        relative = item.relative_to(deployment).as_posix()
        if relative in CYCLIC_RUNTIME_FILES:
            continue
        artifacts[relative] = {"sha256": _sha256(item), "size_bytes": item.stat().st_size}
    if not artifacts:
        raise ValueError("deployment contains no runtime artefacts")
    return artifacts


def _source_contract(definition: AreaConfig) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate the complete compiler source before trusting a deployment."""
    output = definition.publication.output_dir
    validate_publication(output, definition)
    run = _json(output / "run.json", "compiler run")
    if run.get("council_id") != definition.area_id:
        raise ValueError("compiler run area_id does not match Area Definition")
    if run.get("status") not in PUBLISHABLE_STATUSES:
        raise ValueError("compiler run is not publishable")
    return run, _run_contract(definition, run)


def _default_deployment(definition: AreaConfig) -> Path:
    return Path(__file__).parents[2] / "build" / "deployments" / definition.deployment_slug


def generate_lock(
    definition: AreaConfig,
    path: Path | None = None,
    *,
    deployment: Path | None = None,
) -> Path:
    """Lock a previously bootstrapped, deterministic public Area Deployment.

    Bootstrap builds are intentionally lock-free.  Once their source compiler
    run validates, this function records every runtime file.  A normal build
    then recreates the directory and verifies it against this tracked lock.
    """
    run, contract = _source_contract(definition)
    deployment = deployment or _default_deployment(definition)
    artifacts = runtime_artifact_digests(deployment)
    target = path or lock_path(definition)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "deployment_id": definition.deployment_slug,
        "area_id": definition.area_id,
        "area_name": definition.area_name,
        "title": definition.publication.title,
        "scope": {
            "area_id": definition.area_id,
            "area_name": definition.area_name,
            "audience": definition.publication.audience,
        },
        "snapshot_id": definition.source.snapshot_id,
        "run_id": run["run_id"],
        "status": run["status"],
        **contract,
        "connection_count": run["connection_count"],
        "gap_count": run["gap_count"],
        "layer_counts": run["layer_counts"],
        "criteria": run["criteria"],
        "cyclic_runtime_files": sorted(CYCLIC_RUNTIME_FILES),
        "artifacts": artifacts,
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def verify_lock(
    definition: AreaConfig,
    path: Path | None = None,
    *,
    deployment: Path | None = None,
) -> dict[str, Any]:
    """Verify compiler provenance and, when supplied, every public runtime file."""
    target = path or lock_path(definition)
    lock = _json(target, "deployment provenance lock")
    if lock.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("deployment provenance lock schema_version is invalid")
    expected_identity = {
        "deployment_id": definition.deployment_slug,
        "area_id": definition.area_id,
        "area_name": definition.area_name,
        "title": definition.publication.title,
        "scope": {
            "area_id": definition.area_id,
            "area_name": definition.area_name,
            "audience": definition.publication.audience,
        },
        "snapshot_id": definition.source.snapshot_id,
    }
    for key, value in expected_identity.items():
        if lock.get(key) != value:
            raise ValueError(f"deployment provenance lock {key} does not match Area Definition")
    run, contract = _source_contract(definition)
    for key, value in contract.items():
        if lock.get(key) != value:
            raise ValueError(f"deployment provenance lock {key} is stale")
    for key in ("run_id", "status", "connection_count", "gap_count", "layer_counts", "criteria"):
        if lock.get(key) != run.get(key):
            raise ValueError(f"deployment provenance lock {key} does not match compiler run")
    if lock.get("cyclic_runtime_files") != sorted(CYCLIC_RUNTIME_FILES):
        raise ValueError("deployment provenance lock cyclic runtime files are invalid")
    artifacts = lock.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("deployment provenance lock runtime artifacts are invalid")
    if deployment is not None and artifacts != runtime_artifact_digests(deployment):
        raise ValueError("deployment provenance lock runtime artifacts do not match deployment")
    return lock
