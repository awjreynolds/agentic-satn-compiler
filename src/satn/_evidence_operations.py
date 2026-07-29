"""Private five-operation implementation owned by :mod:`local_evidence_store`."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import geopandas as gpd
import yaml
from shapely import from_geojson
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from satn.evidence_contracts import (
    IngestionContract,
    SourceExport,
    canonical_evidence_json,
)
from satn.local_evidence_store import (
    EvidenceRefreshRequest,
    EvidenceWriterBusy,
    LocalEvidenceStore,
    _sha256_file,
    evidence_partition_keys,
    provision_spatial_runtime,
    supported_evidence_layers,
)
from satn.models import AreaDefinition
from satn.open_roads_adapter import contract_payload as open_roads_contract_payload
from satn.osm_network_adapter import contract_payload as osm_network_contract_payload


@dataclass(frozen=True)
class EvidencePaths:
    """Resolved paths for one Local Evidence workspace."""

    workspace: Path
    store: Path
    extension_cache: Path


@dataclass(frozen=True)
class SourceBinding:
    """One local Source Export and its closed Ingestion Contract."""

    source_export: SourceExport
    ingestion_contract: IngestionContract


class _LocalEvidenceWorkspace:
    """One deep operational interface over a workspace-local evidence store."""

    def __init__(
        self,
        *,
        workspace: Path | None,
        store: Path | None,
        extension_cache: Path | None,
        invocation_dir: Path,
    ) -> None:
        resolved_workspace = _absolute(workspace or invocation_dir, invocation_dir)
        resolved_store = _absolute(
            store or resolved_workspace / ".satn/evidence/local-evidence.duckdb",
            invocation_dir,
        )
        self.paths = EvidencePaths(
            workspace=resolved_workspace,
            store=resolved_store,
            extension_cache=_absolute(
                extension_cache or resolved_store.parent / "extensions",
                invocation_dir,
            ),
        )
        self._invocation_dir = invocation_dir.resolve()

    def initialise(self, *, extension_archive: Path | None) -> dict[str, object]:
        """Initialise or verify the exact offline store runtime."""

        existed = self.paths.store.is_file()
        store = self._store()
        if extension_archive is not None:
            provision_spatial_runtime(
                runtime_lock_path=_runtime_lock_path(),
                extension_archive=_absolute(extension_archive, self._invocation_dir),
                extension_cache=self.paths.extension_cache,
            )
        store.verify_runtime()
        lock = nullcontext() if existed else _writer_lock(self.paths)
        with lock:
            store.initialise()
            status = store.status(verify=True)
        coverage = status.current_coverage
        return self._result(
            command="init",
            state=None if coverage is None else coverage.fingerprint,
            created=not existed and self.paths.store.is_file(),
        )

    def refresh(
        self,
        *,
        area: Path,
        source_exports: tuple[Path, ...],
        replace_source: str | None,
        expect_state: str | None,
        dry_run: bool,
        rebuild: bool,
    ) -> dict[str, object]:
        """Plan, atomically refresh, or physically rebuild exact coverage."""

        if (replace_source is None) != (expect_state is None):
            raise ValueError("--replace-source and --expect-state must be supplied together")
        if rebuild:
            if dry_run or source_exports or replace_source is not None:
                raise ValueError(
                    "--rebuild repairs the current logical state and cannot refresh sources"
                )
            _load_area_geometry(_absolute(area, self._invocation_dir))
            with _writer_lock(self.paths):
                coverage = self._store().rebuild().coverage
            return self._result(
                command="refresh",
                state=coverage.fingerprint,
                dry_run=False,
                rebuilt=True,
                coverage=_coverage_payload(coverage, provenance=False),
            )

        selector = _load_area_geometry(_absolute(area, self._invocation_dir))
        store = self._store()
        descriptors = source_exports or _retained_descriptors(store)
        if not descriptors:
            raise ValueError(
                "refresh has no supplied or retained local Source Export descriptors"
            )
        bindings = tuple(
            _load_source_descriptor(_absolute(path, self._invocation_dir))
            for path in descriptors
        )
        if replace_source is not None and replace_source not in {
            binding.ingestion_contract.source_layer for binding in bindings
        }:
            raise ValueError(f"--replace-source {replace_source} has no matching descriptor")
        requests = tuple(
            EvidenceRefreshRequest(
                source_export=binding.source_export,
                ingestion_contract=binding.ingestion_contract,
                partition_keys=evidence_partition_keys(
                    binding.ingestion_contract.source_layer,
                    selector,
                ),
            )
            for binding in bindings
        )
        lock = nullcontext() if dry_run else _writer_lock(self.paths)
        with lock:
            refreshed = store.refresh_many(
                requests=requests,
                dry_run=dry_run,
                replace_source=replace_source,
                expect_state=expect_state,
            )
        coverage = refreshed.coverage
        return self._result(
            command="refresh",
            state=coverage.fingerprint,
            dry_run=dry_run,
            rebuilt=False,
            reused_cells=list(refreshed.reused_cells),
            missing_cells=list(refreshed.missing_cells),
            replaced_cells=list(refreshed.replaced_cells),
            sources=_refresh_sources_payload(bindings, coverage),
            coverage=_coverage_payload(coverage, provenance=dry_run),
        )

    def status(
        self,
        *,
        area: Path | None,
        state: str | None,
        verify: bool,
        provenance: bool,
    ) -> dict[str, object]:
        """Report current or historical coverage with complete diagnostics."""

        store = self._store()
        selector = (
            None
            if area is None
            else _load_area_geometry(_absolute(area, self._invocation_dir))
        )
        if state is None:
            status = store.status(verify=verify)
            coverage = status.current_coverage
            store_state = status.state
        else:
            coverage = store.resolve_coverage(state_fingerprint=state, verify=verify)
            store_state = "ready"
        return self._result(
            command="status",
            state=None if coverage is None else coverage.fingerprint,
            store_state=store_state,
            coverage=_coverage_payload(
                coverage,
                provenance=provenance,
                area_selector=selector,
            ),
        )

    def query(
        self,
        *,
        layer: str,
        area: Path | None,
        bbox: str | None,
        geometry: str | None,
        predicate: Literal["intersects", "within", "contains"],
        where: tuple[str, ...],
        fields: tuple[str, ...],
        state: str | None,
        export_gpkg: Path | None,
        replace_export: bool,
    ) -> dict[str, object]:
        """Inspect and optionally export one exact evidence subset."""

        if sum(value is not None for value in (area, bbox, geometry)) != 1:
            raise ValueError("query requires exactly one of --area, --geometry or --bbox")
        selector: BaseGeometry | None = None
        parsed_bbox: tuple[float, float, float, float] | None = None
        if area is not None:
            selector = _load_area_geometry(_absolute(area, self._invocation_dir))
        elif geometry is not None:
            selector = _load_geometry(geometry, self._invocation_dir)
        else:
            assert bbox is not None
            parsed_bbox = _parse_bbox(bbox)
        store = self._store()
        result = store.query(
            state_fingerprint=state,
            source_layer=layer,
            selector=selector,
            bbox=parsed_bbox,
            predicate=predicate,
            filters=_parse_filters(where),
            projection=_unique_fields(fields),
        )
        rows = [_query_row_payload(row) for row in result.rows]
        export_path = None
        if export_gpkg is not None:
            resolved_export = _absolute(export_gpkg, self._invocation_dir)
            _export_query_result(
                result,
                resolved_export,
                source_layer=layer,
                store_path=self.paths.store,
                retained_source_paths=store.retained_source_paths(),
                replace=replace_export,
            )
            export_path = str(resolved_export)
        return self._result(
            command="query",
            state=str(result.manifest["coverage_state_fingerprint"]),
            row_count=len(rows),
            result_fingerprint=result.fingerprint,
            manifest=_json_plain(result.manifest),
            rows=rows,
            export_gpkg=export_path,
        )

    def delete(self, *, yes: bool, expect_state: str) -> dict[str, object]:
        """Move the store and lock metadata to recoverable workspace trash."""

        if not yes:
            raise ValueError("recoverable delete requires --yes")
        with _writer_lock(self.paths) as lock_path:
            status = self._store().status(verify=True)
            coverage = status.current_coverage
            current_state = None if coverage is None else coverage.fingerprint
            if current_state != expect_state:
                raise ValueError("Local Evidence Store current state does not match --expect-state")
            if not self.paths.store.is_file():
                raise ValueError("Local Evidence Store file is missing")
            trash = self.paths.store.parent / "trash"
            trash.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            restore_path = trash / f"{self.paths.store.name}.{stamp}"
            os.replace(self.paths.store, restore_path)
            restored_lock = trash / f"{lock_path.name}.{stamp}"
            os.replace(lock_path, restored_lock)
        return self._result(
            command="delete",
            state=current_state,
            restore_path=str(restore_path),
            restore_lock_path=str(restored_lock),
        )

    def _store(self) -> LocalEvidenceStore:
        return LocalEvidenceStore(
            store_path=self.paths.store,
            runtime_lock_path=_runtime_lock_path(),
            extension_cache=self.paths.extension_cache,
        )

    def _result(
        self,
        *,
        command: str,
        state: str | None,
        **values: object,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "ok": True,
            "command": command,
            "store": str(self.paths.store),
            "extension_cache": str(self.paths.extension_cache),
        }
        if state is not None:
            result["state"] = state
        result.update(values)
        result["exit_code"] = 0
        return result


def _absolute(path: Path, invocation_dir: Path) -> Path:
    return (path if path.is_absolute() else invocation_dir / path).resolve()


def _runtime_lock_path() -> Path:
    project_lock = Path(__file__).parents[2] / "config/duckdb-spatial-runtime-lock.json"
    if project_lock.is_file():
        return project_lock
    return Path(__file__).with_name("assets") / "duckdb-spatial-runtime-lock.json"


def _coverage_payload(
    coverage: object | None,
    *,
    provenance: bool,
    area_selector: BaseGeometry | None = None,
) -> dict[str, object]:
    attestations = () if coverage is None else tuple(coverage.attestations)
    partitions: list[dict[str, object]] = []
    for attestation in attestations:
        content = attestation.partition_content
        item: dict[str, object] = {
            "source_layer": content.partition_key.source_layer,
            "cell": content.partition_key.cell,
            "availability": content.availability,
            "attestation": attestation.fingerprint,
            "partition_content": content.fingerprint,
        }
        if provenance:
            item["source_export"] = {
                **_json_plain(attestation.source_export.canonical_payload()),
                "fingerprint": attestation.source_export.fingerprint,
                "provenance": _json_plain(attestation.source_export.provenance),
            }
            item["ingestion_contract"] = {
                **_json_plain(content.ingestion_contract.canonical_payload()),
                "fingerprint": content.ingestion_contract.fingerprint,
            }
        partitions.append(item)
    availability_names = (
        ("available", "available"),
        ("no_data", "no-data"),
        ("explicit_unknown", "explicit-unknown"),
    )
    counts = {
        name: sum(partition["availability"] == availability for partition in partitions)
        for name, availability in availability_names
    }
    existing = {
        (str(partition["source_layer"]), str(partition["cell"])) for partition in partitions
    }
    missing: list[str] = []
    if area_selector is not None:
        for source_layer in supported_evidence_layers():
            for key in evidence_partition_keys(source_layer, area_selector):
                if (source_layer, key.cell) not in existing:
                    missing.append(f"{source_layer}:{key.cell}")
    explicit_unknown_cells = sorted(
        f"{partition['source_layer']}:{partition['cell']}"
        for partition in partitions
        if partition["availability"] == "explicit-unknown"
    )
    stale = sorted(
        f"{attestation.partition_content.partition_key.source_layer}:"
        f"{attestation.partition_content.partition_key.cell}"
        for attestation in attestations
        if not _retained_source_exists(attestation)
    )
    layers: dict[str, dict[str, object]] = {}
    for source_layer in supported_evidence_layers():
        layer_partitions = [
            partition
            for partition in partitions
            if partition["source_layer"] == source_layer
        ]
        layers[source_layer] = {
            name: sum(
                partition["availability"] == availability
                for partition in layer_partitions
            )
            for name, availability in availability_names
        }
        layers[source_layer]["missing"] = [
            item for item in sorted(missing) if item.startswith(f"{source_layer}:")
        ]
        layers[source_layer]["stale"] = [
            item for item in stale if item.startswith(f"{source_layer}:")
        ]
    return {
        "status": "missing" if coverage is None else str(coverage.state),
        "area_status": (
            None
            if area_selector is None
            else ("incomplete" if missing else "complete")
        ),
        **counts,
        "explicit_unknown_cells": explicit_unknown_cells,
        "missing": sorted(missing),
        "stale": stale,
        "layers": layers,
        "partitions": sorted(
            partitions,
            key=lambda item: (str(item["source_layer"]), str(item["cell"])),
        ),
    }


def _retained_source_exists(attestation: object) -> bool:
    retained_path = attestation.source_export.provenance.get("retained_path")
    return (
        isinstance(retained_path, str)
        and Path(retained_path).is_file()
        and _sha256_file(Path(retained_path))
        == attestation.source_export.raw_bytes_sha256
    )


def _load_area_geometry(path: Path) -> BaseGeometry:
    area = AreaDefinition.from_yaml(path)
    candidates: list[Path] = []
    if area.source.fixture_dir is not None:
        candidates.append(area.source.fixture_dir / "boundary.geojson")
    candidates.append(area.source.snapshot_dir / area.source.snapshot_id / "boundary.geojson")
    boundary_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if boundary_path is None:
        raise ValueError(
            "Area Definition has no retained local boundary; create or retain its snapshot first"
        )
    try:
        frame = gpd.read_file(boundary_path)
    except Exception as error:
        raise ValueError(f"cannot read Area Definition boundary: {boundary_path}") from error
    if frame.empty or frame.crs is None:
        raise ValueError("Area Definition boundary must be non-empty with an explicit CRS")
    geometry = frame.to_crs(27700).geometry.union_all()
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError("Area Definition boundary geometry is empty or invalid")
    return geometry


def _load_geometry(value: str, invocation_dir: Path) -> BaseGeometry:
    candidate = _absolute(Path(value), invocation_dir)
    if candidate.is_file():
        try:
            frame = gpd.read_file(candidate)
        except Exception as error:
            raise ValueError(f"cannot read selector geometry: {candidate}") from error
        if frame.empty or frame.crs is None:
            raise ValueError("selector geometry file must be non-empty with an explicit CRS")
        geometry = frame.to_crs(27700).geometry.union_all()
    else:
        try:
            geometry = from_geojson(value)
        except Exception as error:
            raise ValueError(
                "--geometry must be inline EPSG:27700 GeoJSON or a local spatial file"
            ) from error
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError("selector geometry is empty or invalid")
    return geometry


def _load_source_descriptor(path: Path) -> SourceBinding:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"cannot read Source Export descriptor: {path}") from error
    if not isinstance(payload, dict):
        raise ValueError("Source Export descriptor must contain a mapping")
    source_payload = payload.get("source_export", payload)
    if not isinstance(source_payload, dict):
        raise ValueError("Source Export descriptor source_export must be a mapping")
    raw_path_value = source_payload.get("path")
    if not isinstance(raw_path_value, str) or not raw_path_value:
        raise ValueError("Source Export descriptor requires path")
    raw_path = _absolute(Path(raw_path_value), path.parent)
    declared_checksum = source_payload.get("raw_bytes_sha256")
    actual_checksum = _sha256_file(raw_path)
    if declared_checksum != actual_checksum:
        raise ValueError("Source Export descriptor checksum does not match retained bytes")
    source_fields = {
        name: source_payload.get(name)
        for name in (
            "source_family",
            "dataset",
            "layer",
            "publisher_release",
            "effective_date",
            "licence",
            "format",
            "declared_crs",
            "raw_bytes_sha256",
        )
    }
    if not all(isinstance(value, str) and value for value in source_fields.values()):
        raise ValueError("Source Export descriptor identity fields are incomplete")
    source = SourceExport(
        **source_fields,  # type: ignore[arg-type]
        provenance={
            "retained_path": str(raw_path),
            "descriptor_path": str(path),
        },
    )
    contract_payload = payload.get("ingestion_contract")
    if contract_payload is None:
        contract_payload = _default_contract_payload(source)
    if not isinstance(contract_payload, dict):
        raise ValueError("Source Export descriptor ingestion_contract must be a mapping")
    contract_payload = {key: value for key, value in contract_payload.items() if key != "contract"}
    contract = IngestionContract(**contract_payload)
    source_layer = f"{source.source_family}/{source.layer}"
    if contract.source_layer != source_layer:
        raise ValueError("Source Export and Ingestion Contract source layers differ")
    return SourceBinding(source_export=source, ingestion_contract=contract)


def _default_contract_payload(source: SourceExport) -> dict[str, object]:
    source_layer = f"{source.source_family}/{source.layer}"
    if source_layer == "os-open-roads/RoadLink":
        return open_roads_contract_payload(source.declared_crs)
    if source_layer == "openstreetmap/lines":
        return osm_network_contract_payload()
    raise ValueError(
        f"descriptor requires an explicit supported Ingestion Contract for {source_layer}"
    )


def _retained_descriptors(store: LocalEvidenceStore) -> tuple[Path, ...]:
    status = store.status(verify=True)
    coverage = status.current_coverage
    if coverage is None:
        return ()
    descriptors: set[Path] = set()
    for attestation in coverage.attestations:
        descriptor = attestation.source_export.provenance.get("descriptor_path")
        if isinstance(descriptor, str) and descriptor:
            descriptors.add(Path(descriptor))
    return tuple(sorted(descriptors))


def _refresh_sources_payload(
    bindings: tuple[SourceBinding, ...],
    coverage: object,
) -> list[dict[str, object]]:
    sources: list[dict[str, object]] = []
    for binding in bindings:
        source = binding.source_export
        contract = binding.ingestion_contract
        attestations = [
            attestation
            for attestation in coverage.attestations
            if attestation.source_export.fingerprint == source.fingerprint
            and attestation.partition_content.ingestion_contract.fingerprint
            == contract.fingerprint
        ]
        availability = {
            state: sum(
                attestation.partition_content.availability == state
                for attestation in attestations
            )
            for state in ("available", "no-data", "explicit-unknown")
        }
        sources.append(
            {
                "source_layer": contract.source_layer,
                "source_export": source.fingerprint,
                "ingestion_contract": contract.fingerprint,
                "retained_bytes": Path(
                    str(source.provenance["retained_path"])
                ).stat().st_size,
                "partition_count": len(attestations),
                "feature_count": sum(
                    len(attestation.partition_content.features)
                    for attestation in attestations
                ),
                "availability": availability,
            }
        )
    return sorted(sources, key=lambda item: str(item["source_layer"]))


def _parse_bbox(value: str) -> tuple[float, float, float, float]:
    parts = value.split(",")
    if len(parts) != 4:
        raise ValueError("--bbox requires xmin,ymin,xmax,ymax")
    try:
        parsed = tuple(float(part) for part in parts)
    except ValueError as error:
        raise ValueError("--bbox values must be numbers") from error
    min_x, min_y, max_x, max_y = parsed
    if min_x >= max_x or min_y >= max_y:
        raise ValueError("--bbox must have increasing bounds")
    return parsed


def _parse_filters(values: tuple[str, ...]) -> dict[str, object]:
    filters: dict[str, object] = {}
    for expression in values:
        field, separator, encoded = expression.partition("=")
        if not separator or not field or field in filters:
            raise ValueError("--where requires unique FIELD=JSON expressions")
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError as error:
            raise ValueError(f"--where {field} value is not valid JSON") from error
        if isinstance(value, (dict, list, float, bool)):
            raise ValueError("--where values must be JSON string, integer or null scalars")
        filters[field] = value
    return filters


def _unique_fields(values: tuple[str, ...]) -> tuple[str, ...]:
    if any(not field or field.strip() != field for field in values):
        raise ValueError("--field values must be non-empty canonical names")
    if len(set(values)) != len(values):
        raise ValueError("--field cannot be repeated")
    return values


def _query_row_payload(row: object) -> dict[str, object]:
    return {
        "source_export_fingerprint": row.source_export_fingerprint,
        "logical_key": row.logical_key,
        "feature_content_fingerprint": row.feature_content_fingerprint,
        "geometry_fingerprint": row.geometry_fingerprint,
        "geometry": mapping(row.geometry),
        "crs": row.crs,
        "attributes": _json_plain(row.attributes),
        "attestation_fingerprints": list(row.attestation_fingerprints),
        "fingerprint": row.fingerprint,
    }


def _export_query_result(
    result: object,
    path: Path,
    *,
    source_layer: str,
    store_path: Path,
    retained_source_paths: tuple[Path, ...],
    replace: bool,
) -> None:
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    targets = {path.resolve(), manifest_path.resolve()}
    if store_path.resolve() in targets:
        raise ValueError("GeoPackage inspection export cannot replace the Local Evidence Store")
    if targets & {candidate.resolve() for candidate in retained_source_paths}:
        raise ValueError("GeoPackage inspection export cannot replace a retained Source Export")
    if (path.exists() or manifest_path.exists()) and not replace:
        raise ValueError("GeoPackage inspection export already exists; use --replace-export")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.stem}.tmp{path.suffix}")
    temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.tmp")
    temporary_path.unlink(missing_ok=True)
    temporary_manifest.unlink(missing_ok=True)
    records = []
    for row in result.rows:
        records.append(
            {
                "source_export_fingerprint": row.source_export_fingerprint,
                "logical_key": row.logical_key,
                "feature_content_fingerprint": row.feature_content_fingerprint,
                "geometry_fingerprint": row.geometry_fingerprint,
                "attestation_fingerprints": json.dumps(
                    row.attestation_fingerprints, separators=(",", ":")
                ),
                **dict(row.attributes),
                "geometry": row.geometry,
            }
        )
    if records:
        frame = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:27700")
    else:
        frame = gpd.GeoDataFrame(
            columns=[
                "source_export_fingerprint",
                "logical_key",
                "feature_content_fingerprint",
                "geometry_fingerprint",
                "attestation_fingerprints",
                "geometry",
            ],
            geometry="geometry",
            crs="EPSG:27700",
        )
    try:
        frame.to_file(
            temporary_path,
            layer=source_layer.replace("/", "_"),
            driver="GPKG",
        )
        manifest = {
            **_json_plain(result.manifest),
            "result_fingerprint": result.fingerprint,
            "export": str(path),
            "gpkg_sha256": _sha256_file(temporary_path),
        }
        temporary_manifest.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        _sha256_file(temporary_manifest)
        os.replace(temporary_path, path)
        os.replace(temporary_manifest, manifest_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)
        raise


def _json_plain(value: object) -> object:
    return json.loads(canonical_evidence_json(value))


@contextmanager
def _writer_lock(paths: EvidencePaths):
    lock_path = paths.store.with_suffix(paths.store.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise EvidenceWriterBusy(
                f"Local Evidence Store writer lock is busy: {lock_path}"
            ) from error
        yield lock_path
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
