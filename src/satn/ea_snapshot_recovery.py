"""Fail-closed recovery evidence for superseding invalid EA route snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import CRS, Transformer
from shapely import wkt
from shapely.geometry import LineString, Point
from shapely.ops import transform

from satn.content_identity import canonical_network_geometry_fingerprint
from satn.ea_elevation import (
    ELIGIBLE_FEATURE_TYPES,
    sha256_file,
    validate_eligible_route_geometries,
)
from satn.identifiers import stable_id
from satn.models import AreaConfig, GovernedSpatialSourceConfig
from satn.sources import (
    ROAD_CLASSIFICATION_FILENAME,
    StagedSnapshot,
    _read_snapshot_frames,
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
_GEOJSON_FEATURE_ID = "__recovery_geojson_feature_id"


def load_legacy_ea_recovery_snapshot(
    config: AreaConfig,
) -> dict[str, gpd.GeoDataFrame]:
    """Load only the pinned invalid v10 parent for candidate-only recovery."""

    source = getattr(config, "source", None)
    snapshot_id = getattr(source, "snapshot_id", None)
    snapshot_dir = getattr(source, "snapshot_dir", None)
    if (
        snapshot_id != LEGACY_NAN_PARENT_SNAPSHOT_ID
        or not isinstance(snapshot_dir, Path)
    ):
        raise ValueError("EA recovery requires the exact pinned WECA v10 parent")
    parent = snapshot_dir / snapshot_id
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("EA recovery requires the exact pinned WECA v10 parent")
    try:
        parent.resolve(strict=True).relative_to(snapshot_dir.resolve(strict=True))
    except ValueError as error:
        raise ValueError("EA recovery requires the exact pinned WECA v10 parent") from error
    manifest_path = parent / "snapshot.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("EA recovery requires the exact pinned WECA v10 parent")
    manifest_sha256 = _sha256_file(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("EA recovery requires the exact pinned WECA v10 parent") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("snapshot_id") != LEGACY_NAN_PARENT_SNAPSHOT_ID
        or manifest_sha256 != LEGACY_NAN_PARENT_MANIFEST_SHA256
    ):
        raise ValueError("EA recovery requires the exact pinned WECA v10 parent")

    normalization_report: dict[str, int] = {}
    _validate_snapshot(
        parent,
        defer_ea_route_nondegeneracy=True,
        legacy_nan_property_key=LEGACY_NAN_PROPERTY_KEY,
        expected_legacy_nan_count=LEGACY_NAN_EXPECTED_COUNT,
        normalization_report=normalization_report,
    )
    return _read_snapshot_frames(parent)


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
                    "collapsed_point_x": str(float(row["geometry"].centroid.x)),
                    "collapsed_point_y": str(float(row["geometry"].centroid.y)),
                    **{
                        field: value
                        for field in (
                            "place_id",
                            "community_id",
                            "obligation_id",
                            "school_id",
                            "community_attachment_node",
                            "target_attachment_node",
                            "spine_attachment_node",
                            "spine_name",
                            "parent_target_name",
                            "parent_target_id",
                            "root_spine_id",
                            "spine_id",
                            "community_attachment_point",
                            "target_attachment_point",
                            "spine_attachment_point",
                        )
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
    candidate_rows: list[dict[str, object]] = []
    candidate_parents: dict[str, dict[str, object]] = {}
    for row in _stream_metric_geojson(corrected_candidate_network):
        if _governed_target_identity(row) in governed_identities:
            candidate_rows.append(row)
        if row.get("feature_type") == "strategic-spine":
            parent_id = _unambiguous_replacement_identity(
                pd.Series(row),
                ("feature_id", "spine_id"),
            )
            if parent_id is None or parent_id in candidate_parents:
                raise ValueError(
                    "EA snapshot recovery candidate strategic-spine identity is ambiguous"
                )
            candidate_parents[parent_id] = row
    collapsed_endpoint_nodes = {
        item[field]
        for item in primary
        for field in (
            "community_attachment_node",
            "target_attachment_node",
            "spine_attachment_node",
        )
        if field in item
    }
    collapsed_endpoint_nodes.update(
        routing_node_id
        for row in candidate_rows
        if (provenance := _json_object(row.get("provenance"))) is not None
        if (routing_node_id := _text(provenance.get("routing_node_id"))) is not None
    )
    pinned_edges = _retained_pinned_edges(
        invalid_sampled_routes.parent / "network.geojson",
        collapsed_endpoint_nodes,
    )
    cited_official_feature_ids = {
        evidence_id
        for parent in candidate_parents.values()
        if (provenance := _json_object(parent.get("provenance"))) is not None
        for evidence_id in _text_list(provenance.get("evidence_ids"))
    }
    official_features = _retained_official_features(
        invalid_sampled_routes.parent / ROAD_CLASSIFICATION_FILENAME,
        cited_official_feature_ids,
    )
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
            collapsed_endpoint_node_ids=tuple(
                item[field]
                for field in (
                    "community_attachment_node",
                    "target_attachment_node",
                    "spine_attachment_node",
                )
                if field in item
            ),
            collapsed_point=Point(
                float(item["collapsed_point_x"]),
                float(item["collapsed_point_y"]),
            ),
            collapsed_road_names=tuple(
                item[field]
                for field in ("spine_name", "parent_target_name")
                if field in item
            ),
            collapsed_attachment_points=tuple(
                item[field]
                for field in (
                    "community_attachment_point",
                    "target_attachment_point",
                    "spine_attachment_point",
                )
                if field in item
            ),
            collapsed_parent_spine_ids=tuple(
                item[field]
                for field in ("parent_target_id", "root_spine_id", "spine_id")
                if field in item
            ),
            candidate_parents=candidate_parents,
            pinned_edges=pinned_edges,
            official_features=official_features,
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
    collapsed_endpoint_node_ids: tuple[str, ...],
    collapsed_point: Point,
    collapsed_road_names: tuple[str, ...],
    collapsed_attachment_points: tuple[str, ...],
    collapsed_parent_spine_ids: tuple[str, ...],
    candidate_parents: Mapping[str, Mapping[str, object]],
    pinned_edges: tuple[Mapping[str, object], ...],
    official_features: Mapping[str, Mapping[str, object]],
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
    associations = [
        proof
        for _, row in matching.iterrows()
        if (
            proof := _governed_colocated_access_association(
                row,
                identity_field=identity_field,
                identity_value=identity_value,
                governed_identifiers=governed_identifiers,
                collapsed_endpoint_node_ids=collapsed_endpoint_node_ids,
                collapsed_point=collapsed_point,
                collapsed_road_names=collapsed_road_names,
                collapsed_attachment_points=collapsed_attachment_points,
                collapsed_parent_spine_ids=collapsed_parent_spine_ids,
                candidate_parents=candidate_parents,
                pinned_edges=pinned_edges,
                official_features=official_features,
            )
        )
        is not None
    ]
    if (len(routes), len(gaps), len(associations)) == (1, 0, 0):
        replacement = _unambiguous_replacement_identity(
            routes.iloc[0],
            ("feature_id", "access_connection_id"),
        )
        resolution = "superseded-by-distinct-node-route"
        association_proof: dict[str, str] = {}
    elif (len(routes), len(gaps), len(associations)) == (0, 1, 0):
        replacement = _unambiguous_replacement_identity(
            gaps.iloc[0],
            ("feature_id", "gap_id", "connection_id"),
        )
        resolution = "superseded-by-network-gap"
        association_proof = {}
    elif (len(routes), len(gaps), len(associations)) == (0, 0, 1):
        association_proof = associations[0]
        replacement = association_proof.pop("replacement_feature_id")
        resolution = association_proof.pop("association_resolution")
    else:
        raise ValueError(
            f"collapsed eligible route {feature_id} requires exactly one route or "
            "network gap, or one governed colocated access association"
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
        **association_proof,
    }


def _governed_colocated_access_association(
    row: pd.Series,
    *,
    identity_field: str,
    identity_value: str,
    governed_identifiers: dict[str, str],
    collapsed_endpoint_node_ids: tuple[str, ...],
    collapsed_point: Point,
    collapsed_road_names: tuple[str, ...],
    collapsed_attachment_points: tuple[str, ...],
    collapsed_parent_spine_ids: tuple[str, ...],
    candidate_parents: Mapping[str, Mapping[str, object]],
    pinned_edges: tuple[Mapping[str, object], ...],
    official_features: Mapping[str, Mapping[str, object]],
) -> dict[str, str] | None:
    """Return complete proof for one compiler-owned zero-edge association."""

    expected_obligation_id = governed_identifiers.get("obligation_id")
    expected_school_id = governed_identifiers.get("school_id")
    is_school = expected_school_id is not None
    geometry = row.get("geometry")
    if (
        identity_field not in {"place_id", "community_id", "school_id"}
        or expected_obligation_id is None
        or row.get("feature_type")
        != ("school-access-obligation" if is_school else "access-obligation")
        or _text(row.get("obligation_id")) != expected_obligation_id
        or row.get("obligation_kind") != ("school" if is_school else "community")
        or _text(row.get("place_id")) != identity_value
        or (
            _text(row.get("school_id")) != expected_school_id
            if is_school
            else _text(row.get("community_id")) != identity_value
        )
        or row.get("network_role")
        != ("school-access-obligation" if is_school else "community-access-obligation")
        or row.get("service_status") != ("served-provisional" if is_school else "served")
        or row.get("criterion_continuity") != "green"
        or (
            is_school
            and (
                row.get("criterion_access_point") != "amber"
                or row.get("access_point_status") != "inferred"
                or row.get("service_rationale")
                != (
                    "School Access Point is colocated with fixed governed Backbone "
                    "geometry; no route edge is required or published."
                )
                or _text(row.get("access_point_rationale")) is None
                or _text(row.get("school_kind")) is None
            )
        )
        or not pd.isna(row.get("topography_profile_id"))
        or _text(row.get("feature_id")) is not None
        or _text(row.get("access_connection_id")) is not None
        or _text(row.get("connection_id")) is not None
        or _text(row.get("gap_id")) is not None
        or (
            (geojson_feature_id := _text(row.get(_GEOJSON_FEATURE_ID)))
            is not None
            and geojson_feature_id != expected_obligation_id
        )
        or not isinstance(geometry, Point)
        or geometry.is_empty
        or not math.isfinite(float(geometry.x))
        or not math.isfinite(float(geometry.y))
        or len(collapsed_endpoint_node_ids) != 3
        or len(set(collapsed_endpoint_node_ids)) != 1
    ):
        return None
    provenance_value = _json_object(row.get("provenance"))
    supporting_evidence = _json_object(row.get("supporting_evidence"))
    if provenance_value is None or supporting_evidence is None:
        return None
    proof_fields = (
        "association_kind",
        "parent_role",
        "parent_target_id",
        "root_evidence_id",
        "root_source_id",
        "root_spine_id",
        "routing_node_id",
        "service_kind",
    )
    if any(
        _text(provenance_value.get(field))
        != _text(supporting_evidence.get(field))
        for field in proof_fields
    ):
        return None
    association_kind = _text(provenance_value.get("association_kind"))
    routing_node_id = _text(provenance_value.get("routing_node_id"))
    root_spine_id = _text(row.get("root_spine_id"))
    parent_target_id = _text(provenance_value.get("parent_target_id"))
    school_kind = _text(row.get("school_kind"))
    if (
        provenance_value.get("service_kind") != "backbone-access-association"
        or provenance_value.get("service_status")
        != ("served-provisional" if is_school else "served")
        or association_kind != "colocated-direct-strategic-spine"
        or provenance_value.get("parent_role") != "strategic-spine"
        or _text(provenance_value.get("root_spine_id")) != root_spine_id
        or parent_target_id != root_spine_id
        or routing_node_id is None
        or root_spine_id is None
        or _text(provenance_value.get("root_source_id")) is None
        or _text(provenance_value.get("root_evidence_id")) is None
        or _text(provenance_value.get("access_connection_id")) is not None
        or _text(provenance_value.get("gap_id")) is not None
        or (
            is_school
            and (
                _text(provenance_value.get("school_id")) != expected_school_id
                or _text(provenance_value.get("school_kind")) != school_kind
                or provenance_value.get("access_point_status") != "inferred"
                or not pd.isna(provenance_value.get("access_point_source_id"))
            )
        )
    ):
        return None
    base_proof = {
        "replacement_feature_id": expected_obligation_id,
        "association_kind": association_kind,
        "routing_node_id": routing_node_id,
        "root_spine_id": root_spine_id,
        "parent_target_id": parent_target_id,
    }
    if routing_node_id == collapsed_endpoint_node_ids[0]:
        exact_proof = {
            **base_proof,
            "association_resolution": (
                "superseded-by-colocated-backbone-access-association"
            ),
        }
        if is_school:
            exact_proof.update(
                {
                    "service_status": "served-provisional",
                    "criterion_access_point": "amber",
                    "access_point_status": "inferred",
                    "school_kind": school_kind,
                }
            )
        return exact_proof
    if is_school:
        synthetic_crosswalk = _pinned_synthetic_school_frontier_crosswalk(
            synthetic_attachment_id=collapsed_endpoint_node_ids[0],
            candidate_node_id=routing_node_id,
            candidate_association_point=geometry,
            collapsed_point=collapsed_point,
            collapsed_attachment_points=collapsed_attachment_points,
            collapsed_parent_spine_ids=collapsed_parent_spine_ids,
            collapsed_road_names=collapsed_road_names,
            root_spine_id=root_spine_id,
            root_source_id=_text(provenance_value.get("root_source_id")),
            root_evidence_id=_text(provenance_value.get("root_evidence_id")),
            candidate_parents=candidate_parents,
            pinned_edges=pinned_edges,
            official_features=official_features,
        )
        if synthetic_crosswalk is None:
            return None
        return {
            **base_proof,
            "association_resolution": (
                "superseded-by-pinned-synthetic-school-frontier-association"
            ),
            "service_status": "served-provisional",
            "criterion_access_point": "amber",
            "access_point_status": "inferred",
            "school_kind": school_kind,
            **synthetic_crosswalk,
        }
    crosswalk = _pinned_official_node_crosswalk(
        collapsed_node_id=collapsed_endpoint_node_ids[0],
        candidate_node_id=routing_node_id,
        collapsed_point=collapsed_point,
        collapsed_road_names=collapsed_road_names,
        root_spine_id=root_spine_id,
        root_source_id=_text(provenance_value.get("root_source_id")),
        root_evidence_id=_text(provenance_value.get("root_evidence_id")),
        candidate_parents=candidate_parents,
        pinned_edges=pinned_edges,
        official_features=official_features,
    )
    if crosswalk is None:
        return None
    return {
        **base_proof,
        "association_resolution": (
            "superseded-by-pinned-official-node-crosswalk-association"
        ),
        **crosswalk,
    }


def _retained_pinned_edges(
    path: Path,
    collapsed_endpoint_nodes: set[str],
) -> tuple[Mapping[str, object], ...]:
    """Retain only pinned-network edges incident to a collapsed endpoint."""

    if path.is_symlink() or not path.is_file():
        return ()
    retained: list[Mapping[str, object]] = []
    for row in _stream_metric_geojson(path):
        u = _node_text(row.get("u"))
        v = _node_text(row.get("v"))
        if u in collapsed_endpoint_nodes or v in collapsed_endpoint_nodes:
            retained.append(row)
    return tuple(retained)


def _retained_official_features(
    path: Path,
    cited_feature_ids: set[str],
) -> dict[str, Mapping[str, object]]:
    """Retain only official-road rows cited by candidate strategic spines."""

    if path.is_symlink() or not path.is_file():
        return {}
    retained: dict[str, Mapping[str, object]] = {}
    for row in _stream_metric_geojson(path):
        feature_id = _text(row.get("official_feature_id"))
        if feature_id not in cited_feature_ids:
            continue
        if feature_id in retained:
            raise ValueError(
                "EA snapshot recovery official-road feature identity is ambiguous"
            )
        retained[feature_id] = row
    return retained


def _pinned_synthetic_school_frontier_crosswalk(
    *,
    synthetic_attachment_id: str,
    candidate_node_id: str,
    candidate_association_point: Point,
    collapsed_point: Point,
    collapsed_attachment_points: tuple[str, ...],
    collapsed_parent_spine_ids: tuple[str, ...],
    collapsed_road_names: tuple[str, ...],
    root_spine_id: str,
    root_source_id: str | None,
    root_evidence_id: str | None,
    candidate_parents: Mapping[str, Mapping[str, object]],
    pinned_edges: tuple[Mapping[str, object], ...],
    official_features: Mapping[str, Mapping[str, object]],
) -> dict[str, str] | None:
    """Prove one pinned-v10 synthetic School frontier against current topology."""

    if (
        len(collapsed_attachment_points) != 3
        or len(collapsed_parent_spine_ids) != 3
        or len(set(collapsed_parent_spine_ids)) != 1
        or len(collapsed_road_names) != 2
        or len(set(collapsed_road_names)) != 1
        or root_source_id is None
        or root_evidence_id is None
    ):
        return None
    attachment_points: list[Point] = []
    try:
        for value in collapsed_attachment_points:
            parsed = wkt.loads(value)
            if (
                not isinstance(parsed, Point)
                or parsed.is_empty
                or not math.isfinite(float(parsed.x))
                or not math.isfinite(float(parsed.y))
            ):
                return None
            attachment_points.append(parsed)
    except (TypeError, ValueError):
        return None
    attachment_fingerprints = {
        canonical_network_geometry_fingerprint(point, 4326)
        for point in attachment_points
    }
    if len(attachment_fingerprints) != 1:
        return None
    attachment_fingerprint = next(iter(attachment_fingerprints))
    old_parent_spine_id = collapsed_parent_spine_ids[0]
    if (
        stable_id(
            "school-frontier-attachment",
            old_parent_spine_id,
            attachment_fingerprint,
        )
        != synthetic_attachment_id
    ):
        return None
    transformer = Transformer.from_crs(4326, 27700, always_xy=True)
    attachment_point = transform(transformer.transform, attachment_points[0])
    if attachment_point.distance(collapsed_point) > 0.001:
        return None

    incident_edges = [
        edge
        for edge in pinned_edges
        if candidate_node_id
        in {_node_text(edge.get("u")), _node_text(edge.get("v"))}
    ]
    node_points = [
        point
        for edge in incident_edges
        if (point := _edge_endpoint_for_node(edge, candidate_node_id)) is not None
    ]
    if not node_points:
        return None
    candidate_node_point = node_points[0]
    if any(point.distance(candidate_node_point) > 0.001 for point in node_points[1:]):
        return None
    point_node_distance = attachment_point.distance(candidate_node_point)
    candidate_association_node_distance = candidate_association_point.distance(
        candidate_node_point
    )
    if point_node_distance > 20 or candidate_association_node_distance > 20:
        return None

    road_ref = collapsed_road_names[0]
    reciprocal_edges: list[Mapping[str, object]] = []
    for edge in incident_edges:
        u = _node_text(edge.get("u"))
        v = _node_text(edge.get("v"))
        counterpart = v if u == candidate_node_id else u
        if counterpart is None or _single_text_value(edge.get("ref")) != road_ref:
            continue
        for reverse in incident_edges:
            if (
                _node_text(reverse.get("u")) == counterpart
                and _node_text(reverse.get("v")) == candidate_node_id
                and _node_text(edge.get("u")) == candidate_node_id
                and _reciprocal_edge_identity(edge, reverse)
            ):
                reciprocal_edges.extend((edge, reverse))
    reciprocal_by_id = {
        _pinned_edge_id(edge): edge
        for edge in reciprocal_edges
        if _pinned_edge_id(edge) is not None
    }
    edge_osm_ids = {
        value
        for edge in reciprocal_by_id.values()
        if (value := _node_text(edge.get("osmid"))) is not None
    }
    edge_names = {
        value
        for edge in reciprocal_by_id.values()
        if (value := _single_text_value(edge.get("name"))) is not None
    }
    edge_refs = {
        value
        for edge in reciprocal_by_id.values()
        if (value := _single_text_value(edge.get("ref"))) is not None
    }
    if (
        not reciprocal_by_id
        or len(edge_osm_ids) != 1
        or len(edge_names) != 1
        or edge_refs != {road_ref}
    ):
        return None

    parent = candidate_parents.get(root_spine_id)
    if parent is None:
        return None
    parent_geometry = parent.get("geometry")
    parent_provenance = _json_object(parent.get("provenance"))
    if (
        parent.get("feature_type") != "strategic-spine"
        or parent.get("network_role") != "strategic-spine"
        or parent.get("spine_kind") != "a-road"
        or parent.get("category") != "Governed official A-road strategic spine"
        or _text(parent.get("name")) != road_ref
        or _text(parent.get("source_id")) != root_source_id
        or _text(parent.get("evidence_id")) != root_evidence_id
        or not isinstance(parent_geometry, LineString)
        or parent_geometry.is_empty
        or parent_provenance is None
        or _text(parent_provenance.get("source_id")) != root_source_id
        or _text(parent_provenance.get("evidence_id")) != root_evidence_id
        or parent_provenance.get("source_feature_type") != "a-road-spine"
    ):
        return None
    official_source_ids = _text_list(parent_provenance.get("source_ids"))
    cited_evidence_ids = _text_list(parent_provenance.get("evidence_ids"))
    if (
        len(official_source_ids) != 1
        or not official_source_ids[0].startswith("os-open-roads-")
        or not cited_evidence_ids
    ):
        return None
    official_source_id = official_source_ids[0]
    cited_official = [
        official_features[evidence_id]
        for evidence_id in cited_evidence_ids
        if evidence_id in official_features
    ]
    if not cited_official or any(
        row.get("official_classification") != "a-road"
        or _text(row.get("official_road_number")) != road_ref
        or _text(row.get("source_id")) != official_source_id
        or not isinstance(row.get("geometry"), LineString)
        or row["geometry"].is_empty
        for row in cited_official
    ):
        return None
    parent_attachment_distance = parent_geometry.distance(attachment_point)
    parent_node_distance = parent_geometry.distance(candidate_node_point)
    official_attachment_distance = min(
        row["geometry"].distance(attachment_point) for row in cited_official
    )
    official_node_distance = min(
        row["geometry"].distance(candidate_node_point) for row in cited_official
    )
    if (
        parent_attachment_distance > 20
        or parent_node_distance > 20
        or official_attachment_distance > 20
        or official_node_distance > 20
    ):
        return None
    official_feature_ids = sorted(
        feature_id
        for row in cited_official
        if (feature_id := _text(row.get("official_feature_id"))) is not None
    )
    return {
        "legacy_synthetic_attachment_id": synthetic_attachment_id,
        "legacy_attachment_geometry_sha256": attachment_fingerprint,
        "legacy_attachment_point_to_candidate_node_m": f"{point_node_distance:.3f}",
        "candidate_association_point_to_node_m": (
            f"{candidate_association_node_distance:.3f}"
        ),
        "pinned_graph_edge_ids": ",".join(sorted(reciprocal_by_id)),
        "pinned_osm_id": next(iter(edge_osm_ids)),
        "pinned_road_ref": road_ref,
        "pinned_road_name": next(iter(edge_names)),
        "candidate_parent_spine_id": root_spine_id,
        "candidate_parent_attachment_distance_m": f"{parent_attachment_distance:.3f}",
        "candidate_parent_node_distance_m": f"{parent_node_distance:.3f}",
        "official_attachment_distance_m": f"{official_attachment_distance:.3f}",
        "official_node_distance_m": f"{official_node_distance:.3f}",
        "official_source_id": official_source_id,
        "official_feature_ids": ",".join(official_feature_ids),
    }


def _edge_endpoint_for_node(
    edge: Mapping[str, object],
    node_id: str,
) -> Point | None:
    geometry = edge.get("geometry")
    if not isinstance(geometry, LineString) or geometry.is_empty:
        return None
    if _node_text(edge.get("u")) == node_id:
        return Point(geometry.coords[0])
    if _node_text(edge.get("v")) == node_id:
        return Point(geometry.coords[-1])
    return None


def _pinned_edge_id(edge: Mapping[str, object]) -> str | None:
    u = _node_text(edge.get("u"))
    v = _node_text(edge.get("v"))
    key = _node_text(edge.get("key"))
    if u is None or v is None or key is None:
        return None
    return f"{u}:{v}:{key}"


def _reciprocal_edge_identity(
    forward: Mapping[str, object],
    reverse: Mapping[str, object],
) -> bool:
    forward_geometry = forward.get("geometry")
    reverse_geometry = reverse.get("geometry")
    return (
        isinstance(forward_geometry, LineString)
        and isinstance(reverse_geometry, LineString)
        and not forward_geometry.is_empty
        and not reverse_geometry.is_empty
        and _node_text(forward.get("key")) == _node_text(reverse.get("key"))
        and _node_text(forward.get("osmid")) == _node_text(reverse.get("osmid"))
        and _single_text_value(forward.get("ref"))
        == _single_text_value(reverse.get("ref"))
        and _single_text_value(forward.get("name"))
        == _single_text_value(reverse.get("name"))
        and _line_is_exact_reverse(forward_geometry, reverse_geometry)
    )


def _pinned_official_node_crosswalk(
    *,
    collapsed_node_id: str,
    candidate_node_id: str,
    collapsed_point: Point,
    collapsed_road_names: tuple[str, ...],
    root_spine_id: str,
    root_source_id: str | None,
    root_evidence_id: str | None,
    candidate_parents: Mapping[str, Mapping[str, object]],
    pinned_edges: tuple[Mapping[str, object], ...],
    official_features: Mapping[str, Mapping[str, object]],
) -> dict[str, str] | None:
    """Prove one legacy/current node split from pinned topology and official roads."""

    if (
        len(collapsed_road_names) != 2
        or len(set(collapsed_road_names)) != 1
        or root_source_id is None
        or root_evidence_id is None
        or collapsed_node_id == candidate_node_id
    ):
        return None
    road_ref = collapsed_road_names[0]
    parent = candidate_parents.get(root_spine_id)
    if parent is None:
        return None
    parent_geometry = parent.get("geometry")
    parent_provenance = _json_object(parent.get("provenance"))
    if (
        parent.get("feature_type") != "strategic-spine"
        or parent.get("network_role") != "strategic-spine"
        or parent.get("spine_kind") != "a-road"
        or parent.get("category") != "Governed official A-road strategic spine"
        or _text(parent.get("name")) != road_ref
        or _text(parent.get("source_id")) != root_source_id
        or _text(parent.get("evidence_id")) != root_evidence_id
        or not isinstance(parent_geometry, LineString)
        or parent_geometry.is_empty
        or parent_provenance is None
        or _text(parent_provenance.get("source_id")) != root_source_id
        or _text(parent_provenance.get("evidence_id")) != root_evidence_id
        or parent_provenance.get("source_feature_type") != "a-road-spine"
    ):
        return None
    official_source_ids = _text_list(parent_provenance.get("source_ids"))
    cited_evidence_ids = _text_list(parent_provenance.get("evidence_ids"))
    if (
        len(official_source_ids) != 1
        or not official_source_ids[0].startswith("os-open-roads-")
        or not cited_evidence_ids
    ):
        return None
    official_source_id = official_source_ids[0]
    cited_official = [
        official_features[evidence_id]
        for evidence_id in cited_evidence_ids
        if evidence_id in official_features
    ]
    if not cited_official:
        return None
    if any(
        row.get("official_classification") != "a-road"
        or _text(row.get("official_road_number")) != road_ref
        or _text(row.get("source_id")) != official_source_id
        or not isinstance(row.get("geometry"), LineString)
        or row["geometry"].is_empty
        for row in cited_official
    ):
        return None

    forward = [
        row
        for row in pinned_edges
        if _node_text(row.get("u")) == collapsed_node_id
        and _node_text(row.get("v")) == candidate_node_id
    ]
    reverse = [
        row
        for row in pinned_edges
        if _node_text(row.get("u")) == candidate_node_id
        and _node_text(row.get("v")) == collapsed_node_id
    ]
    if len(forward) != 1 or len(reverse) != 1:
        return None
    forward_edge, reverse_edge = forward[0], reverse[0]
    forward_geometry = forward_edge.get("geometry")
    reverse_geometry = reverse_edge.get("geometry")
    forward_ref = _single_text_value(forward_edge.get("ref"))
    reverse_ref = _single_text_value(reverse_edge.get("ref"))
    forward_name = _single_text_value(forward_edge.get("name"))
    reverse_name = _single_text_value(reverse_edge.get("name"))
    forward_osm_id = _node_text(forward_edge.get("osmid"))
    reverse_osm_id = _node_text(reverse_edge.get("osmid"))
    forward_key = _node_text(forward_edge.get("key"))
    reverse_key = _node_text(reverse_edge.get("key"))
    forward_length = _finite_float(forward_edge.get("length"))
    reverse_length = _finite_float(reverse_edge.get("length"))
    if (
        not isinstance(forward_geometry, LineString)
        or not isinstance(reverse_geometry, LineString)
        or forward_geometry.is_empty
        or reverse_geometry.is_empty
        or forward_ref != road_ref
        or reverse_ref != road_ref
        or forward_name is None
        or forward_name != reverse_name
        or forward_osm_id is None
        or forward_osm_id != reverse_osm_id
        or forward_key is None
        or forward_key != reverse_key
        or forward_length is None
        or reverse_length is None
        or not 0 < forward_length <= 20
        or abs(forward_length - reverse_length) > 0.001
        or abs(forward_geometry.length - forward_length) > 0.01
        or abs(reverse_geometry.length - reverse_length) > 0.01
        or not _line_is_exact_reverse(forward_geometry, reverse_geometry)
    ):
        return None
    old_endpoint = Point(forward_geometry.coords[0])
    new_endpoint = Point(forward_geometry.coords[-1])
    parent_old_distance = parent_geometry.distance(old_endpoint)
    parent_new_distance = parent_geometry.distance(new_endpoint)
    official_old_distance = min(
        row["geometry"].distance(old_endpoint) for row in cited_official
    )
    official_new_distance = min(
        row["geometry"].distance(new_endpoint) for row in cited_official
    )
    if (
        collapsed_point.distance(old_endpoint) > 0.001
        or parent_old_distance > 20
        or parent_new_distance > 20
        or official_old_distance > 20
        or official_new_distance > 20
    ):
        return None
    official_feature_ids = sorted(
        _text(row.get("official_feature_id"))
        for row in cited_official
        if _text(row.get("official_feature_id")) is not None
    )
    geometry_sha256 = hashlib.sha256(
        json.dumps(
            [[float(x), float(y)] for x, y in forward_geometry.coords],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "collapsed_routing_node_id": collapsed_node_id,
        "candidate_routing_node_id": candidate_node_id,
        "pinned_osm_id": forward_osm_id,
        "pinned_network_edge_key": forward_key,
        "pinned_road_ref": forward_ref,
        "pinned_road_name": forward_name,
        "pinned_edge_length_m": f"{forward_length:.3f}",
        "pinned_forward_geometry_sha256": geometry_sha256,
        "candidate_parent_spine_id": root_spine_id,
        "candidate_parent_old_node_distance_m": f"{parent_old_distance:.3f}",
        "candidate_parent_new_node_distance_m": f"{parent_new_distance:.3f}",
        "official_old_node_distance_m": f"{official_old_distance:.3f}",
        "official_new_node_distance_m": f"{official_new_distance:.3f}",
        "official_source_id": official_source_id,
        "official_feature_ids": ",".join(official_feature_ids),
    }


def _line_is_exact_reverse(forward: LineString, reverse: LineString) -> bool:
    forward_coordinates = tuple(
        (float(x), float(y)) for x, y in forward.coords
    )
    reverse_coordinates = tuple(
        (float(x), float(y)) for x, y in reverse.coords
    )
    return forward_coordinates == tuple(reversed(reverse_coordinates))


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _node_text(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    return _text(value)


def _single_text_value(value: object) -> str | None:
    if isinstance(value, list):
        values = {_text(item) for item in value}
        values.discard(None)
        return next(iter(values)) if len(values) == 1 else None
    return _text(value)


def _text_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    values = tuple(_text(item) for item in value)
    return tuple(value for value in values if value is not None)


def _unambiguous_replacement_identity(
    row: pd.Series,
    property_fields: tuple[str, ...],
) -> str | None:
    identities = {
        identity
        for field in (_GEOJSON_FEATURE_ID, *property_fields)
        if (identity := _text(row.get(field))) is not None
    }
    if len(identities) > 1:
        raise ValueError("EA snapshot recovery candidate has conflicting replacement identities")
    return next(iter(identities), None)


def _json_object(value: object) -> dict[str, object] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    return value


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
        feature_id_property_key=_GEOJSON_FEATURE_ID,
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
