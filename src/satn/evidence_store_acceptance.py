"""Explicit local acceptance gate for regional Local Evidence equivalence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pyogrio
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from satn.evidence_contracts import (
    EvidencePartitionKey,
    IngestionContract,
    SourceExport,
)
from satn.evidence_store_equivalence import (
    OfficialRoadSourceLineage,
    assert_official_road_source_frame_equivalent,
    project_official_road_source_frame,
)
from satn.local_evidence_store import (
    LocalEvidenceStore,
    _bng_cells_intersecting,
    provision_spatial_runtime,
)
from satn.models import AreaDefinition
from satn.open_roads_adapter import (
    ATTRIBUTES as OPEN_ROADS_ATTRIBUTES,
)
from satn.open_roads_adapter import (
    SOURCE_LAYER as OPEN_ROADS_SOURCE_LAYER,
)
from satn.open_roads_adapter import (
    SOURCE_SCHEMA as OPEN_ROADS_SOURCE_SCHEMA,
)
from satn.open_roads_adapter import contract_payload as open_roads_contract_payload
from satn.sources import load_snapshot


class RegionalAcceptanceBlocked(RuntimeError):
    """A configured regional acceptance target cannot be evaluated."""


@dataclass(frozen=True)
class RegionalAcceptanceTarget:
    name: str
    area_definition: Path
    source_export: Path
    snapshot_manifest: Path
    official_road_frame: Path
    selector_frame: Path
    expected_availability_counts: Mapping[str, int]
    expected_semantic_fingerprint: str


@dataclass(frozen=True)
class EvidenceAcceptanceReport:
    """Observable result of one independent oracle/store comparison."""

    row_count: int
    availability_counts: Mapping[str, int]
    semantic_fingerprint: str
    query_result_fingerprint: str


def accept_store_query(
    *,
    store_path: Path,
    runtime_lock_path: Path,
    extension_cache: Path,
    source_export: SourceExport,
    ingestion_contract: IngestionContract,
    partition_keys: tuple[EvidencePartitionKey, ...],
    selector: BaseGeometry,
    projection: tuple[str, ...],
    oracle: gpd.GeoDataFrame,
    source_id: str,
    expected_availability_counts: Mapping[str, int],
) -> EvidenceAcceptanceReport:
    """Compare an actual DuckDB query with an independently loaded source frame."""

    store = LocalEvidenceStore(
        store_path=store_path,
        runtime_lock_path=runtime_lock_path,
        extension_cache=extension_cache,
    )
    store.initialise()
    refreshed = store.refresh(
        source_export=source_export,
        ingestion_contract=ingestion_contract,
        partition_keys=partition_keys,
    )
    result = store.query(
        state_fingerprint=refreshed.coverage.fingerprint,
        source_layer=ingestion_contract.source_layer,
        selector=selector,
        selector_crs="EPSG:27700",
        projection=projection,
    )
    projected = project_official_road_source_frame(
        result,
        OfficialRoadSourceLineage(source_export, source_id),
    )
    assert_official_road_source_frame_equivalent(
        oracle,
        projected,
        expected_availability_counts=expected_availability_counts,
    )
    return EvidenceAcceptanceReport(
        row_count=len(projected.rows),
        availability_counts=dict(projected.availability_counts),
        semantic_fingerprint=projected.semantic_fingerprint,
        query_result_fingerprint=projected.query_result_fingerprint,
    )


def resolve_regional_target(name: str, data_root: Path) -> RegionalAcceptanceTarget:
    """Resolve one configured corpus or fail before any acceptance work starts."""

    config_root = Path(__file__).resolve().parents[2] / "config"
    if name == "banes":
        snapshot = (
            data_root / "snapshots" / "banes-osm-open-roads-v1-2026-07-29"
        )
        target = RegionalAcceptanceTarget(
            name=name,
            area_definition=config_root / "banes.yaml",
            source_export=(
                data_root / "governed" / "weca-os-open-roads-2026-04-07.geojson"
            ),
            snapshot_manifest=snapshot / "snapshot.json",
            official_road_frame=snapshot / "official-road-classification.geojson",
            selector_frame=snapshot / "boundary.geojson",
            expected_availability_counts={
                "available": 10,
                "no-data": 0,
                "explicit-unknown": 0,
            },
            expected_semantic_fingerprint=(
                "92666e753db3e2a462f16cc4f3fbc2a1d353e59ac46745e7560f3d08df7d1de5"
            ),
        )
        labels = (
            "B&NES area definition",
            "B&NES source",
            "B&NES snapshot manifest",
            "B&NES snapshot source frame",
            "B&NES snapshot selector frame",
        )
    elif name == "weca-v10":
        snapshot = (
            data_root
            / "snapshots"
            / "weca-classification-elevation-2026-07-28-v10"
        )
        target = RegionalAcceptanceTarget(
            name=name,
            area_definition=config_root / "weca.yaml",
            source_export=(
                data_root / "governed" / "weca-os-open-roads-2026-04-07.geojson"
            ),
            snapshot_manifest=snapshot / "snapshot.json",
            official_road_frame=snapshot / "official-road-classification.geojson",
            selector_frame=(
                data_root / "governed" / "weca-os-open-roads-2026-04-07.geojson"
            ),
            expected_availability_counts={
                "available": 26,
                "no-data": 4,
                "explicit-unknown": 0,
            },
            expected_semantic_fingerprint=(
                "8d512258175bd4bad14d293ddc348a4b15335c291d1ae69cbc279014fd8e5678"
            ),
        )
        labels = (
            "WECA area definition",
            "WECA v10",
            "WECA v10 snapshot manifest",
            "WECA v10 snapshot source frame",
            "WECA v10 source selector frame",
        )
    else:
        raise ValueError(f"unsupported regional acceptance target: {name}")
    for label, path in zip(
        labels,
        (
            target.area_definition,
            target.source_export,
            target.snapshot_manifest,
            target.official_road_frame,
            target.selector_frame,
        ),
        strict=True,
    ):
        if not path.is_file():
            suffix = " governed Open Roads export" if path == target.source_export else ""
            raise RegionalAcceptanceBlocked(f"{label}{suffix} is absent: {path}")
    return target


def run_regional_acceptance(
    target: RegionalAcceptanceTarget,
    *,
    runtime_lock_path: Path,
    extension_archive: Path,
    work_dir: Path,
) -> EvidenceAcceptanceReport:
    """Execute one configured regional gate against retained source and oracle bytes."""

    source_info = pyogrio.read_info(target.source_export)
    fields = {str(field) for field in source_info.get("fields", ())}
    missing_fields = set(OPEN_ROADS_SOURCE_SCHEMA) - fields
    if missing_fields:
        issue = " (blocked by #224)" if target.name == "banes" else ""
        raise RegionalAcceptanceBlocked(
            f"{target.name} governed Open Roads export is missing v1 fields: "
            + ", ".join(sorted(missing_fields))
            + issue
        )
    manifest = _json_object(target.snapshot_manifest, "snapshot manifest")
    file_hashes = manifest.get("file_sha256")
    if not isinstance(file_hashes, dict):
        raise RegionalAcceptanceBlocked(
            f"{target.name} snapshot manifest has no file_sha256 mapping"
        )
    expected_oracle_sha256 = file_hashes.get(target.official_road_frame.name)
    actual_oracle_sha256 = _sha256_file(target.official_road_frame)
    if expected_oracle_sha256 != actual_oracle_sha256:
        raise RegionalAcceptanceBlocked(
            f"{target.name} snapshot source-frame bytes do not match its manifest"
        )
    if target.selector_frame != target.source_export:
        expected_selector_sha256 = file_hashes.get(target.selector_frame.name)
        actual_selector_sha256 = _sha256_file(target.selector_frame)
        if expected_selector_sha256 != actual_selector_sha256:
            raise RegionalAcceptanceBlocked(
                f"{target.name} snapshot selector-frame bytes do not match its manifest"
            )
    definition = AreaDefinition.from_yaml(target.area_definition)
    definition.source = definition.source.model_copy(
        update={
            "snapshot_dir": target.snapshot_manifest.parent.parent,
            "snapshot_id": target.snapshot_manifest.parent.name,
        }
    )
    oracle = load_snapshot(definition).get("official_road_classification")
    if not isinstance(oracle, gpd.GeoDataFrame):
        raise RegionalAcceptanceBlocked(
            f"{target.name} production snapshot loader returned no official-road frame"
        )
    evidence_sources = manifest.get("evidence_sources")
    official_source = (
        evidence_sources.get("official_road_classification")
        if isinstance(evidence_sources, dict)
        else None
    )
    if not isinstance(official_source, dict):
        raise RegionalAcceptanceBlocked(
            f"{target.name} snapshot has no official-road source lineage"
        )
    source_sha256 = _sha256_file(target.source_export)
    if official_source.get("content_fingerprint") != source_sha256:
        raise RegionalAcceptanceBlocked(
            f"{target.name} source export and snapshot lineage fingerprints differ"
        )
    source_id = official_source.get("source_id")
    effective_date = official_source.get("effective_date")
    licence = official_source.get("licence")
    if not all(isinstance(value, str) and value for value in (source_id, effective_date, licence)):
        raise RegionalAcceptanceBlocked(
            f"{target.name} snapshot official-road lineage is incomplete"
        )
    source_crs = source_info.get("crs")
    if not isinstance(source_crs, str) or not source_crs:
        raise RegionalAcceptanceBlocked(
            f"{target.name} governed Open Roads export has no CRS"
        )
    source_export = SourceExport(
        source_family="os-open-roads",
        dataset="open-roads",
        layer="RoadLink",
        publisher_release=effective_date,
        effective_date=effective_date,
        licence=licence,
        format="GeoJSON",
        declared_crs=source_crs,
        raw_bytes_sha256=source_sha256,
        provenance={"retained_path": str(target.source_export.resolve())},
    )
    contract_data = open_roads_contract_payload(source_crs)
    contract_data.pop("contract")
    ingestion_contract = IngestionContract(**contract_data)
    selector_frame = gpd.read_file(target.selector_frame).to_crs(27700)
    selector = (
        box(*selector_frame.total_bounds)
        if target.selector_frame == target.source_export
        else selector_frame.geometry.union_all()
    )
    partition_keys = tuple(
        EvidencePartitionKey(OPEN_ROADS_SOURCE_LAYER, "bng-10km/v1", cell)
        for cell in _bng_cells_intersecting(selector)
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    extension_cache = work_dir / "extensions"
    provision_spatial_runtime(
        runtime_lock_path=runtime_lock_path,
        extension_archive=extension_archive,
        extension_cache=extension_cache,
    )
    report = accept_store_query(
        store_path=work_dir / f"{target.name}.duckdb",
        runtime_lock_path=runtime_lock_path,
        extension_cache=extension_cache,
        source_export=source_export,
        ingestion_contract=ingestion_contract,
        partition_keys=partition_keys,
        selector=selector,
        projection=tuple(OPEN_ROADS_ATTRIBUTES),
        oracle=oracle,
        source_id=source_id,
        expected_availability_counts=target.expected_availability_counts,
    )
    if report.semantic_fingerprint != target.expected_semantic_fingerprint:
        raise RegionalAcceptanceBlocked(
            f"{target.name} canonical oracle hash changed: "
            f"expected {target.expected_semantic_fingerprint}, "
            f"found {report.semantic_fingerprint}"
        )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed regional Local Evidence equivalence acceptance."
    )
    parser.add_argument("--target", choices=("banes", "weca-v10"), required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--runtime-lock", type=Path)
    parser.add_argument("--extension-archive", type=Path)
    parser.add_argument("--work-dir", type=Path)
    arguments = parser.parse_args(argv)
    try:
        target = resolve_regional_target(arguments.target, arguments.data_root)
        for label, path in (
            ("DuckDB Spatial runtime lock", arguments.runtime_lock),
            ("DuckDB Spatial extension archive", arguments.extension_archive),
            ("acceptance work directory", arguments.work_dir),
        ):
            if path is None:
                raise RegionalAcceptanceBlocked(f"{label} is not configured")
            if label != "acceptance work directory" and not path.is_file():
                raise RegionalAcceptanceBlocked(f"{label} is absent: {path}")
        report = run_regional_acceptance(
            target,
            runtime_lock_path=arguments.runtime_lock,
            extension_archive=arguments.extension_archive,
            work_dir=arguments.work_dir,
        )
    except RegionalAcceptanceBlocked as error:
        print(f"regional equivalence acceptance blocked: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"regional equivalence acceptance failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "target": arguments.target,
                "row_count": report.row_count,
                "availability_counts": dict(report.availability_counts),
                "semantic_fingerprint": report.semantic_fingerprint,
                "query_result_fingerprint": report.query_result_fingerprint,
            },
            sort_keys=True,
        )
    )
    return 0


def _json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RegionalAcceptanceBlocked(f"{label} is not valid JSON: {path}") from error
    if not isinstance(value, dict):
        raise RegionalAcceptanceBlocked(f"{label} is not an object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
