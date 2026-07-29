"""Fail-closed recovery evidence for superseding invalid EA route snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import CRS, Transformer
from shapely.ops import transform

from satn.content_identity import canonical_network_geometry_fingerprint
from satn.ea_elevation import (
    ELIGIBLE_FEATURE_TYPES,
    sha256_file,
    validate_eligible_route_geometries,
)
from satn.models import GovernedSpatialSourceConfig
from satn.sources import (
    ROAD_CLASSIFICATION_FILENAME,
    StagedSnapshot,
    _validate_snapshot,
    promote_staged_snapshot,
)
from satn.streaming_geojson import iter_geojson_features

RECOVERY_SCHEMA_VERSION = "ea-snapshot-recovery/v1"
RECOVERY_TRANSACTION_SCHEMA_VERSION = "ea-snapshot-recovery-transaction/v1"
LEGACY_NAN_PARENT_SNAPSHOT_ID = "weca-classification-elevation-2026-07-28-v10"
LEGACY_NAN_PARENT_MANIFEST_SHA256 = (
    "1993f2f66aaf9fabf95bb5621502a0b8d17430e1d12b49abfc0f03d61d830729"
)
LEGACY_NAN_PROPERTY_KEY = "access_point_source_id"
LEGACY_NAN_EXPECTED_COUNT = 588


def recovery_output_family(elevation_output: Path) -> tuple[Path, ...]:
    """Return every acquisition artifact that one recovery run can create."""

    return (
        elevation_output,
        elevation_output.with_suffix(".manifest.json"),
        elevation_output.with_name(f"{elevation_output.stem}.sampled-routes.geojson"),
        elevation_output.with_name(f"{elevation_output.stem}.sample-ledger.jsonl"),
    )


def preflight_recovery_output_family(elevation_output: Path) -> tuple[Path, ...]:
    """Refuse acquisition when any member of its output family already exists."""

    outputs = recovery_output_family(elevation_output)
    for output in outputs:
        if output.is_symlink() or output.exists():
            raise ValueError(f"EA snapshot recovery output already exists: {output.name}")
    return outputs


def validate_recovery_output_family(elevation_output: Path) -> tuple[Path, ...]:
    """Require every acquisition output as a regular non-symlink file."""

    outputs = recovery_output_family(elevation_output)
    for output in outputs:
        if output.is_symlink() or not output.is_file():
            raise ValueError(f"EA snapshot recovery output is incomplete: {output.name}")
    return outputs


def validate_recovery_sampled_route_output(path: Path) -> int:
    """Validate and count a recovery route output with bounded memory."""

    count = sum(1 for _row in _stream_metric_geojson(path))
    if count == 0:  # pragma: no cover - iterator already refuses this.
        raise ValueError("EA recovery target has no clean sampled route inventory")
    return count


def reconcile_stationary_route_recovery(
    invalid_sampled_routes: Path,
    corrected_candidate_network: Path,
    *,
    parent_snapshot_id: str,
    parent_manifest_sha256: str,
    target_snapshot_id: str,
) -> dict[str, object]:
    """Account for every collapsed eligible route without admitting it as input."""

    collapsed: list[dict[str, str]] = []
    legacy_nan_parent = (
        parent_snapshot_id == LEGACY_NAN_PARENT_SNAPSHOT_ID
        and parent_manifest_sha256 == LEGACY_NAN_PARENT_MANIFEST_SHA256
    )
    normalization_report: dict[str, int] = {}
    for position, row in enumerate(
        _stream_metric_geojson(
            invalid_sampled_routes,
            legacy_nan_property_key=(
                LEGACY_NAN_PROPERTY_KEY if legacy_nan_parent else None
            ),
            expected_legacy_nan_count=(
                LEGACY_NAN_EXPECTED_COUNT if legacy_nan_parent else None
            ),
            normalization_report=normalization_report,
        )
    ):
        if (
            row.get("feature_type") not in ELIGIBLE_FEATURE_TYPES
            or pd.isna(row.get("topography_profile_id"))
        ):
            continue
        feature_id = _text(row.get("feature_id")) or str(position)
        try:
            canonical_network_geometry_fingerprint(row["geometry"], 27700)
        except ValueError as error:
            identity = _governed_target_identity(row)
            if identity is None:
                raise ValueError(
                    f"collapsed eligible route {feature_id} has no governed place identity"
                ) from error
            identity_field, identity_value = identity
            collapsed.append(
                {
                    "feature_id": feature_id,
                    "identity_field": identity_field,
                    "identity_value": identity_value,
                    "collapsed_geometry_sha256": hashlib.sha256(
                        row["geometry"].wkb
                    ).hexdigest(),
                    **{
                        field: value
                        for field in ("place_id", "community_id", "obligation_id", "school_id")
                        if (value := _text(row.get(field))) is not None
                    },
                }
            )
    if not collapsed:
        raise ValueError("EA snapshot recovery found no collapsed eligible routes")

    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for item in collapsed:
        key = (
            item["identity_field"],
            item["identity_value"],
            item["collapsed_geometry_sha256"],
        )
        grouped.setdefault(key, []).append(item)
    primary: list[dict[str, str]] = []
    duplicates: list[dict[str, object]] = []
    for items in grouped.values():
        ordinary = [
            item
            for item in items
            if not item["feature_id"].startswith("supplemental-")
        ]
        if len(ordinary) != 1:
            raise ValueError(
                "collapsed route duplicates require exactly one non-supplemental identity"
            )
        retained_item = ordinary[0]
        primary.append(retained_item)
        for duplicate in sorted(
            (
                item
                for item in items
                if item["feature_id"].startswith("supplemental-")
            ),
            key=lambda item: item["feature_id"],
        ):
            duplicate_record: dict[str, object] = {
                "retained_feature_id": duplicate["feature_id"],
                "deduplicated_against": retained_item["feature_id"],
                retained_item["identity_field"]: retained_item["identity_value"],
                "admitted_as_recovery_input": False,
            }
            duplicate_record.update(
                {
                    field: duplicate[field]
                    for field in ("obligation_id", "school_id")
                    if field in duplicate
                }
            )
            duplicates.append(duplicate_record)

    governed_identities = {
        (item["identity_field"], item["identity_value"]) for item in primary
    }
    candidate_rows = [
        row
        for row in _stream_metric_geojson(corrected_candidate_network)
        if _governed_target_identity(row) in governed_identities
    ]
    candidate = (
        gpd.GeoDataFrame(candidate_rows, geometry="geometry", crs=27700)
        if candidate_rows
        else gpd.GeoDataFrame(
            columns=["feature_type", "topography_profile_id", "geometry"],
            geometry="geometry",
            crs=27700,
        )
    )
    validate_eligible_route_geometries(
        candidate,
        source=corrected_candidate_network,
    )
    resolutions = [
        _resolution(
            candidate,
            feature_id=item["feature_id"],
            identity_field=item["identity_field"],
            identity_value=item["identity_value"],
            governed_identifiers={
                field: item[field]
                for field in ("obligation_id", "school_id")
                if field in item
            },
        )
        for item in sorted(primary, key=lambda value: value["feature_id"])
    ]
    replacements = [
        str(resolution["replacement_feature_id"]) for resolution in resolutions
    ]
    if len(replacements) != len(set(replacements)):
        raise ValueError("replacement feature identities must be globally unique")
    accounted = {
        str(resolution["collapsed_feature_id"]) for resolution in resolutions
    } | {str(duplicate["retained_feature_id"]) for duplicate in duplicates}
    collapsed_ids = {item["feature_id"] for item in collapsed}
    if accounted != collapsed_ids:
        raise ValueError("EA snapshot recovery did not account for every collapsed route")
    record: dict[str, object] = {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "status": "candidate-reconciled",
        "parent_snapshot_id": parent_snapshot_id,
        "parent_manifest_sha256": parent_manifest_sha256,
        "target_snapshot_id": target_snapshot_id,
        "invalid_sampled_routes_sha256": _sha256_file(invalid_sampled_routes),
        "corrected_candidate_network_sha256": _sha256_file(
            corrected_candidate_network
        ),
        "invalid_supplemental_routes_used": False,
        "collapsed_route_count": len(collapsed),
        "unique_collapsed_route_count": len(resolutions),
        "supplemental_duplicate_deduplication": duplicates,
        "resolutions": resolutions,
    }
    if legacy_nan_parent:
        record["legacy_nonfinite_property_normalization"] = {
            "parent_snapshot_id": parent_snapshot_id,
            "parent_manifest_sha256": parent_manifest_sha256,
            "property_key": LEGACY_NAN_PROPERTY_KEY,
            "token": "NaN",
            "replacement": None,
            "count": normalization_report[LEGACY_NAN_PROPERTY_KEY],
        }
    return record


def write_recovery_record(path: Path, record: dict[str, object]) -> None:
    """Create one canonical recovery record, allowing only byte-identical replay."""

    content = (
        json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode()
    if path.is_symlink():
        raise ValueError("EA snapshot recovery record cannot be a symlink")
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise ValueError(
                "EA snapshot recovery record already exists with different content"
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if not path.is_file() or path.read_bytes() != content:
                raise ValueError(
                    "EA snapshot recovery record already exists with different content"
                ) from None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def promote_recovery_transaction(
    *,
    staged_snapshot: StagedSnapshot | None,
    target: Path,
    record_path: Path,
    config_path: Path,
    expected_config_sha256: str,
    promoted_config_bytes: bytes,
    record: dict[str, object],
    parent_snapshot_id: str,
    parent_manifest_sha256: str,
    official_source_id: str,
    official_content_fingerprint: str,
) -> Path:
    """Journal an idempotent target/record/config promotion without rollback deletion."""

    _canonical_sha256(expected_config_sha256, "expected configuration")
    _canonical_sha256(parent_manifest_sha256, "parent manifest")
    _canonical_sha256(
        official_content_fingerprint, "official-road content fingerprint"
    )
    if record.get("schema_version") != RECOVERY_SCHEMA_VERSION:
        raise ValueError("EA snapshot recovery record schema differs")
    if record.get("status") != "sealed":
        raise ValueError("EA snapshot recovery record must be sealed before promotion")
    target_manifest_sha256 = record.get("target_manifest_sha256")
    _canonical_sha256(target_manifest_sha256, "target manifest")
    if record.get("target_snapshot_id") != target.name:
        raise ValueError("EA snapshot recovery record target identity differs")
    desired_config_sha256 = hashlib.sha256(promoted_config_bytes).hexdigest()
    record_content = (
        json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode()
    journal_path = recovery_transaction_journal_path(record_path)
    recorded_plan = (
        _untrusted_recovery_journal_plan(journal_path)
        if journal_path.exists()
        else None
    )
    if recorded_plan is not None:
        staged_identity = recorded_plan.get("staged")
    elif staged_snapshot is not None:
        staged_identity = str(staged_snapshot.path.resolve(strict=True))
    else:
        staged_identity = None
    plan = {
        "target": str(target.resolve(strict=False)),
        "staged": staged_identity,
        "target_manifest_sha256": target_manifest_sha256,
        "record": str(record_path.resolve(strict=False)),
        "record_sha256": hashlib.sha256(record_content).hexdigest(),
        "configuration": str(config_path.resolve(strict=False)),
        "expected_configuration_sha256": expected_config_sha256,
        "promoted_configuration_sha256": desired_config_sha256,
        "parent_snapshot_id": parent_snapshot_id,
        "parent_manifest_sha256": parent_manifest_sha256,
        "official_source_id": official_source_id,
        "official_content_fingerprint": official_content_fingerprint,
    }
    plan_bytes = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    transaction_id = hashlib.sha256(plan_bytes).hexdigest()

    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError("EA snapshot recovery configuration is missing or unsafe")
    current_config_sha256 = _sha256_file(config_path)
    if current_config_sha256 not in {
        expected_config_sha256,
        desired_config_sha256,
    }:
        raise ValueError("EA snapshot recovery configuration changed outside transaction")
    if target.exists() and not journal_path.exists():
        raise ValueError(
            "EA snapshot recovery target exists without its transaction journal; "
            "refusing ambiguous ownership"
        )
    if record_path.exists() and not journal_path.exists():
        raise ValueError(
            "EA snapshot recovery record exists without its transaction journal; "
            "refusing ambiguous ownership"
        )

    journal = {
        "schema_version": RECOVERY_TRANSACTION_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "phase": "prepared",
        "plan": plan,
    }
    if journal_path.exists():
        journal = _read_recovery_journal(
            journal_path,
            transaction_id=transaction_id,
            plan=plan,
        )
    else:
        _write_recovery_journal(journal_path, journal, create=True)

    if target.exists():
        _validate_promoted_recovery_target(
            target,
            target_snapshot_id=target.name,
            expected_manifest_sha256=str(target_manifest_sha256),
            parent_snapshot_id=parent_snapshot_id,
            parent_manifest_sha256=parent_manifest_sha256,
            official_source_id=official_source_id,
            official_content_fingerprint=official_content_fingerprint,
        )
    else:
        if staged_snapshot is None and isinstance(staged_identity, str):
            staged_snapshot = StagedSnapshot(Path(staged_identity), target)
        if staged_snapshot is None or staged_snapshot.destination != target:
            raise ValueError("EA snapshot recovery staged target is unavailable")
        staged_manifest = staged_snapshot.path / "snapshot.json"
        if _sha256_file(staged_manifest) != target_manifest_sha256:
            raise ValueError("EA snapshot recovery staged manifest identity differs")
        _validate_promoted_recovery_target(
            staged_snapshot.path,
            target_snapshot_id=target.name,
            expected_manifest_sha256=str(target_manifest_sha256),
            parent_snapshot_id=parent_snapshot_id,
            parent_manifest_sha256=parent_manifest_sha256,
            official_source_id=official_source_id,
            official_content_fingerprint=official_content_fingerprint,
        )
        promote_staged_snapshot(staged_snapshot)
        _validate_promoted_recovery_target(
            target,
            target_snapshot_id=target.name,
            expected_manifest_sha256=str(target_manifest_sha256),
            parent_snapshot_id=parent_snapshot_id,
            parent_manifest_sha256=parent_manifest_sha256,
            official_source_id=official_source_id,
            official_content_fingerprint=official_content_fingerprint,
        )
    journal["phase"] = "target-promoted"
    _write_recovery_journal(journal_path, journal, create=False)

    write_recovery_record(record_path, record)
    journal["phase"] = "record-promoted"
    _write_recovery_journal(journal_path, journal, create=False)

    current_config_sha256 = _sha256_file(config_path)
    if current_config_sha256 == expected_config_sha256:
        _atomic_replace_bytes(config_path, promoted_config_bytes)
    elif current_config_sha256 != desired_config_sha256:
        raise ValueError("EA snapshot recovery configuration changed outside transaction")
    journal["phase"] = "complete"
    _write_recovery_journal(journal_path, journal, create=False)
    return target


def recovery_transaction_journal_path(record_path: Path) -> Path:
    """Return the deterministic sibling journal for one immutable recovery record."""

    return record_path.with_name(f".{record_path.name}.transaction.json")


def recovery_transaction_artifact(
    record_path: Path,
    *,
    target: Path,
) -> tuple[StagedSnapshot | None, str] | None:
    """Recover the staged/target artifact identity from an existing journal."""

    journal_path = recovery_transaction_journal_path(record_path)
    if not journal_path.exists():
        return None
    plan = _untrusted_recovery_journal_plan(journal_path)
    if plan.get("target") != str(target.resolve(strict=False)):
        raise ValueError("EA snapshot recovery transaction target differs")
    target_manifest_sha256 = plan.get("target_manifest_sha256")
    _canonical_sha256(target_manifest_sha256, "target manifest")
    if target.exists():
        return None, str(target_manifest_sha256)
    staged_value = plan.get("staged")
    if not isinstance(staged_value, str):
        raise ValueError("EA snapshot recovery transaction has no staged artifact")
    staged_path = Path(staged_value)
    if (
        staged_path.is_symlink()
        or not staged_path.is_dir()
        or staged_path.resolve(strict=True).parent
        != target.parent.resolve(strict=True)
    ):
        raise ValueError("EA snapshot recovery transaction staged artifact is unsafe")
    return StagedSnapshot(staged_path, target), str(target_manifest_sha256)


def recovery_transaction_plan(record_path: Path) -> dict[str, object] | None:
    """Read the immutable identity fields needed to resume a completed CLI run."""

    journal_path = recovery_transaction_journal_path(record_path)
    if not journal_path.exists():
        return None
    return dict(_untrusted_recovery_journal_plan(journal_path))


def validate_recovery_target(
    target: Path,
    *,
    target_snapshot_id: str,
    parent_snapshot_id: str,
    parent_manifest_sha256: str,
    official_source_id: str,
    official_content_fingerprint: str,
) -> None:
    """Verify exact recovery lineage and configured official-road provenance."""

    _validate_snapshot(target)
    try:
        manifest = json.loads((target / "snapshot.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("EA snapshot recovery target manifest is unreadable") from error
    if not isinstance(manifest, dict) or manifest.get("snapshot_id") != target_snapshot_id:
        raise ValueError("EA snapshot recovery target identity differs")
    if manifest.get("retained_core_lineage") != {
        "source_snapshot_id": parent_snapshot_id,
        "source_manifest_sha256": parent_manifest_sha256,
    }:
        raise ValueError("EA snapshot recovery target parent lineage differs")
    sources = manifest.get("evidence_sources")
    official = (
        sources.get("official_road_classification")
        if isinstance(sources, dict)
        else None
    )
    if (
        not isinstance(official, dict)
        or official.get("source_id") != official_source_id
        or official.get("content_fingerprint") != official_content_fingerprint
    ):
        raise ValueError(
            "EA snapshot recovery official-road classification identity differs"
        )


def _validate_promoted_recovery_target(
    target: Path,
    *,
    target_snapshot_id: str,
    expected_manifest_sha256: str,
    parent_snapshot_id: str,
    parent_manifest_sha256: str,
    official_source_id: str,
    official_content_fingerprint: str,
) -> None:
    validate_recovery_target(
        target,
        target_snapshot_id=target_snapshot_id,
        parent_snapshot_id=parent_snapshot_id,
        parent_manifest_sha256=parent_manifest_sha256,
        official_source_id=official_source_id,
        official_content_fingerprint=official_content_fingerprint,
    )
    if _sha256_file(target / "snapshot.json") != expected_manifest_sha256:
        raise ValueError("EA snapshot recovery target manifest SHA-256 differs")


def _read_recovery_journal(
    path: Path,
    *,
    transaction_id: str,
    plan: dict[str, object],
) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("EA snapshot recovery transaction journal is unsafe")
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("EA snapshot recovery transaction journal is invalid JSON") from error
    if (
        not isinstance(journal, dict)
        or journal.get("schema_version") != RECOVERY_TRANSACTION_SCHEMA_VERSION
        or journal.get("transaction_id") != transaction_id
        or journal.get("plan") != plan
        or journal.get("phase")
        not in {"prepared", "target-promoted", "record-promoted", "complete"}
    ):
        raise ValueError("EA snapshot recovery transaction journal differs")
    return journal


def _untrusted_recovery_journal_plan(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("EA snapshot recovery transaction journal is unsafe")
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("EA snapshot recovery transaction journal is invalid JSON") from error
    plan = journal.get("plan") if isinstance(journal, dict) else None
    if (
        journal.get("schema_version") != RECOVERY_TRANSACTION_SCHEMA_VERSION
        or not isinstance(plan, dict)
    ):
        raise ValueError("EA snapshot recovery transaction journal differs")
    return plan


def _write_recovery_journal(
    path: Path,
    journal: dict[str, object],
    *,
    create: bool,
) -> None:
    content = (
        json.dumps(journal, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode()
    if path.is_symlink():
        raise ValueError("EA snapshot recovery transaction journal cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if create:
            try:
                os.link(temporary, path)
            except FileExistsError as error:
                raise ValueError(
                    "EA snapshot recovery transaction journal already exists"
                ) from error
        else:
            if not path.is_file():
                raise ValueError("EA snapshot recovery transaction journal is missing")
            temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_replace_bytes(path: Path, content: bytes) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("EA snapshot recovery configuration is missing or unsafe")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _canonical_sha256(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"EA snapshot recovery {label} SHA-256 is invalid")


def verified_official_road_identity(
    parent: Path,
    *,
    parent_manifest_sha256: str,
    governed: GovernedSpatialSourceConfig,
) -> dict[str, object]:
    """Derive official-road identity from a verified parent and configured bytes."""

    manifest_path = parent / "snapshot.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("EA snapshot recovery parent manifest is missing or unsafe")
    actual_manifest_sha256 = _sha256_file(manifest_path)
    if actual_manifest_sha256 != parent_manifest_sha256:
        raise ValueError("EA snapshot recovery parent manifest SHA-256 differs")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("EA snapshot recovery parent manifest is unreadable") from error
    legacy_nan_parent = (
        manifest.get("snapshot_id") == LEGACY_NAN_PARENT_SNAPSHOT_ID
        and parent_manifest_sha256 == LEGACY_NAN_PARENT_MANIFEST_SHA256
    )
    normalization_report: dict[str, int] = {}
    _validate_snapshot(
        parent,
        defer_ea_route_nondegeneracy=True,
        legacy_nan_property_key=(
            LEGACY_NAN_PROPERTY_KEY if legacy_nan_parent else None
        ),
        expected_legacy_nan_count=(
            LEGACY_NAN_EXPECTED_COUNT if legacy_nan_parent else None
        ),
        normalization_report=normalization_report,
    )
    sources = manifest.get("evidence_sources")
    official = (
        sources.get("official_road_classification")
        if isinstance(sources, dict)
        else None
    )
    configured_fingerprint = _sha256_file(governed.path)
    expected = {
        "source_id": governed.source_id,
        "effective_date": governed.effective_date.isoformat(),
        "licence": governed.licence,
        "content_fingerprint": configured_fingerprint,
    }
    if (
        not isinstance(official, dict)
        or official.get("snapshot_file") != ROAD_CLASSIFICATION_FILENAME
        or any(official.get(field) != value for field, value in expected.items())
    ):
        raise ValueError(
            "EA snapshot recovery parent official-road identity differs from configured source"
        )
    snapshotted = gpd.read_file(parent / ROAD_CLASSIFICATION_FILENAME)
    if (
        snapshotted.empty
        or not set(expected).issubset(snapshotted.columns)
        or any(
            set(snapshotted[field].dropna().astype(str)) != {value}
            for field, value in expected.items()
        )
    ):
        raise ValueError(
            "EA snapshot recovery parent official-road rows differ from configured source"
        )
    return {
        **expected,
        "recovery_parent_validation": {
            "eligible_route_nondegeneracy": "deferred-to-exhaustive-reconciliation",
            "legacy_nonfinite_property_normalization": (
                {
                    "parent_snapshot_id": LEGACY_NAN_PARENT_SNAPSHOT_ID,
                    "parent_manifest_sha256": LEGACY_NAN_PARENT_MANIFEST_SHA256,
                    "property_key": LEGACY_NAN_PROPERTY_KEY,
                    "token": "NaN",
                    "replacement": None,
                    "count": normalization_report[LEGACY_NAN_PROPERTY_KEY],
                }
                if legacy_nan_parent
                else None
            ),
        },
    }


def _resolution(
    candidate: gpd.GeoDataFrame,
    *,
    feature_id: str,
    identity_field: str,
    identity_value: str,
    governed_identifiers: dict[str, str],
) -> dict[str, str]:
    matching = (
        candidate
        if candidate.empty
        else candidate[
            [
                _governed_target_identity(row)
                == (identity_field, identity_value)
                for _, row in candidate.iterrows()
            ]
        ]
    )
    routes = matching[
        matching["feature_type"].isin(ELIGIBLE_FEATURE_TYPES)
        & matching["topography_profile_id"].notna()
    ]
    gaps = matching[matching["feature_type"].isin({"network-gap", "gap"})]
    if (len(routes), len(gaps)) == (1, 0):
        replacement = _text(routes.iloc[0].get("feature_id"))
        resolution = "superseded-by-distinct-node-route"
    elif (len(routes), len(gaps)) == (0, 1):
        replacement = _text(gaps.iloc[0].get("feature_id")) or _text(
            gaps.iloc[0].get("connection_id")
        )
        resolution = "superseded-by-network-gap"
    else:
        raise ValueError(
            f"collapsed eligible route {feature_id} requires exactly one route or network gap"
        )
    if replacement is None:
        raise ValueError(
            f"collapsed eligible route {feature_id} has no replacement identity"
        )
    return {
        "collapsed_feature_id": feature_id,
        identity_field: identity_value,
        **governed_identifiers,
        "resolution": resolution,
        "replacement_feature_id": replacement,
    }


def _governed_target_identity(
    row: Mapping[str, object] | pd.Series,
) -> tuple[str, str] | None:
    for field in ("place_id", "community_id", "obligation_id"):
        value = _text(row.get(field))
        if value is not None:
            return field, value
    return None


def _stream_metric_geojson(
    path: Path,
    *,
    legacy_nan_property_key: str | None = None,
    expected_legacy_nan_count: int | None = None,
    normalization_report: dict[str, int] | None = None,
) -> Iterator[dict[str, object]]:
    """Read strict GeoJSON feature-by-feature without loading the collection."""

    transformer: Transformer | None = None
    for _position, properties, geometry, source_crs in iter_geojson_features(
        path,
        legacy_nan_property_key=legacy_nan_property_key,
        expected_legacy_nan_count=expected_legacy_nan_count,
        normalization_report=normalization_report,
    ):
        if transformer is None:
            transformer = Transformer.from_crs(
                source_crs,
                CRS.from_epsg(27700),
                always_xy=True,
            )
        row = dict(properties)
        row["geometry"] = transform(transformer.transform, geometry)
        yield row


def _text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _sha256_file(path: Path) -> str:
    return sha256_file(path)
