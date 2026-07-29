"""Small operational CLI for the workspace-local Local Evidence Store."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import geopandas as gpd
import typer
import yaml
from shapely import from_geojson
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from satn.evidence_contracts import EvidencePartitionKey, IngestionContract, SourceExport
from satn.local_evidence_store import (
    LocalEvidenceStore,
    evidence_partition_keys,
    provision_spatial_runtime,
)
from satn.models import AreaDefinition
from satn.open_roads_adapter import contract_payload as open_roads_contract_payload
from satn.osm_network_adapter import contract_payload as osm_network_contract_payload

OutputFormat = Literal["text", "json"]


@dataclass(frozen=True)
class EvidenceCliContext:
    """Resolved, invocation-local paths shared by all evidence commands."""

    workspace: Path
    store: Path
    extension_cache: Path
    output_format: OutputFormat


@dataclass(frozen=True)
class SourceBinding:
    """One local Source Export and its closed Ingestion Contract."""

    source_export: SourceExport
    ingestion_contract: IngestionContract


class EvidenceWriterBusy(RuntimeError):
    """Another mutating evidence command owns the workspace writer lock."""


evidence_app = typer.Typer(
    no_args_is_help=True,
    help="Manage one offline workspace-local Local Evidence Store.",
)


@evidence_app.callback()
def evidence_options(
    ctx: typer.Context,
    workspace: Annotated[
        Path | None,
        typer.Option("--workspace", help="Workspace used by the default store path."),
    ] = None,
    store: Annotated[
        Path | None,
        typer.Option("--store", help="Explicit Local Evidence Store path."),
    ] = None,
    extension_cache: Annotated[
        Path | None,
        typer.Option("--extension-cache", help="Explicit pinned Spatial extension cache."),
    ] = None,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
) -> None:
    """Resolve paths once without environment-variable or global-cache lookup."""

    invocation_dir = Path.cwd()
    resolved_workspace = _absolute(workspace or invocation_dir, invocation_dir)
    resolved_store = _absolute(
        store or resolved_workspace / ".satn/evidence/local-evidence.duckdb",
        invocation_dir,
    )
    resolved_extension_cache = _absolute(
        extension_cache or resolved_store.parent / "extensions",
        invocation_dir,
    )
    ctx.obj = EvidenceCliContext(
        workspace=resolved_workspace,
        store=resolved_store,
        extension_cache=resolved_extension_cache,
        output_format=output_format,
    )


@evidence_app.command("init")
def initialise_command(
    ctx: typer.Context,
    extension_archive: Annotated[
        Path | None,
        typer.Option(
            "--extension-archive",
            help="Caller-supplied pinned Spatial binary; never a URL.",
        ),
    ] = None,
) -> None:
    """Initialise or verify the exact offline store runtime."""

    paths = _paths(ctx)

    def operation() -> dict[str, object]:
        existed = paths.store.is_file()
        lock = nullcontext() if existed else _writer_lock(paths)
        with lock:
            if extension_archive is not None:
                provision_spatial_runtime(
                    runtime_lock_path=_runtime_lock_path(),
                    extension_archive=_absolute(extension_archive, Path.cwd()),
                    extension_cache=paths.extension_cache,
                )
            store = _store(paths)
            store.initialise()
            status = store.status(verify=True)
        coverage = status.current_coverage
        return _result(
            paths,
            command="init",
            state=None if coverage is None else coverage.fingerprint,
            created=not existed and paths.store.is_file(),
        )

    _execute(paths, "init", operation)


@evidence_app.command("refresh")
def refresh_command(
    ctx: typer.Context,
    area: Path,
    source_export: Annotated[
        list[Path] | None,
        typer.Option(
            "--source-export",
            help="Local governed Source Export descriptor; repeat for multiple layers.",
        ),
    ] = None,
    replace_source: Annotated[
        str | None,
        typer.Option("--replace-source", help="Source layer explicitly allowed to change."),
    ] = None,
    expect_state: Annotated[
        str | None,
        typer.Option("--expect-state", help="Exact current state required for replacement."),
    ] = None,
    dry_run: bool = typer.Option(False, "--dry-run"),
    rebuild: bool = typer.Option(False, "--rebuild"),
) -> None:
    """Plan or refresh exact disconnected Evidence Coverage."""

    paths = _paths(ctx)

    def operation() -> dict[str, object]:
        if (replace_source is None) != (expect_state is None):
            raise ValueError("--replace-source and --expect-state must be supplied together")
        descriptors = tuple(source_export or ())
        if not descriptors:
            raise ValueError("refresh requires at least one local --source-export descriptor")
        if dry_run and rebuild:
            raise ValueError("--dry-run and --rebuild cannot be combined")
        if dry_run and len(descriptors) != 1:
            raise ValueError(
                "one dry-run plans one Source Export exactly; run each descriptor separately"
            )
        lock = nullcontext() if dry_run else _writer_lock(paths)
        with lock:
            selector = _load_area_geometry(_absolute(area, Path.cwd()))
            store = _store(paths)
            plans: list[object] = []
            coverage = None
            for descriptor in descriptors:
                binding = _load_source_descriptor(_absolute(descriptor, Path.cwd()))
                layer = binding.ingestion_contract.source_layer
                if replace_source is not None and layer != replace_source:
                    raise ValueError(
                        f"--replace-source {replace_source} does not match descriptor layer {layer}"
                    )
                partition_keys = _partition_keys(layer, selector)
                plan = store.plan_refresh(
                    source_export=binding.source_export,
                    ingestion_contract=binding.ingestion_contract,
                    partition_keys=partition_keys,
                    replace_source=replace_source is not None,
                    expect_state=expect_state,
                )
                plans.append(plan)
                if dry_run:
                    coverage = plan.coverage
                    continue
                if replace_source is None:
                    coverage = store.refresh(
                        source_export=binding.source_export,
                        ingestion_contract=binding.ingestion_contract,
                        partition_keys=partition_keys,
                    ).coverage
                else:
                    coverage = store.replace_source(
                        source_export=binding.source_export,
                        ingestion_contract=binding.ingestion_contract,
                        partition_keys=partition_keys,
                        expect_state=expect_state,
                    ).coverage
            assert coverage is not None
            if rebuild:
                coverage = store.rebuild().coverage
        reused = sorted({cell for plan in plans for cell in plan.reused_cells})
        missing = sorted({cell for plan in plans for cell in plan.missing_cells})
        replaced = sorted({cell for plan in plans for cell in plan.replaced_cells})
        return _result(
            paths,
            command="refresh",
            state=coverage.fingerprint,
            dry_run=dry_run,
            rebuilt=rebuild,
            reused_cells=reused,
            missing_cells=missing,
            replaced_cells=replaced,
            coverage=_coverage_payload(coverage, provenance=False),
        )

    _execute(paths, "refresh", operation)


@evidence_app.command("status")
def status_command(
    ctx: typer.Context,
    area: Annotated[
        Path | None,
        typer.Option("--area", help="Compare coverage with this Area Definition."),
    ] = None,
    state: Annotated[
        str | None,
        typer.Option("--state", help="Inspect this exact historical coverage state."),
    ] = None,
    verify: bool = typer.Option(False, "--verify"),
    provenance: bool = typer.Option(False, "--provenance"),
) -> None:
    """Report current or historical Evidence Coverage and diagnostics."""

    paths = _paths(ctx)

    def operation() -> dict[str, object]:
        store = _store(paths)
        area_selector = None if area is None else _load_area_geometry(_absolute(area, Path.cwd()))
        if state is None:
            status = store.status(verify=verify)
            coverage = status.current_coverage
            store_state = status.state
        else:
            coverage = store.resolve_coverage(state_fingerprint=state, verify=verify)
            store_state = "ready"
        coverage_payload = _coverage_payload(
            coverage,
            provenance=provenance,
            area_selector=area_selector,
        )
        return _result(
            paths,
            command="status",
            state=None if coverage is None else coverage.fingerprint,
            store_state=store_state,
            coverage=coverage_payload,
        )

    _execute(paths, "status", operation)


@evidence_app.command("query")
def query_command(
    ctx: typer.Context,
    layer: str,
    area: Annotated[
        Path | None,
        typer.Option("--area", help="Use this Area Definition boundary."),
    ] = None,
    bbox: Annotated[
        str | None,
        typer.Option("--bbox", help="BNG xmin,ymin,xmax,ymax."),
    ] = None,
    geometry: Annotated[
        str | None,
        typer.Option("--geometry", help="Inline GeoJSON geometry or local GeoJSON path."),
    ] = None,
    predicate: Literal["intersects", "within", "contains"] = typer.Option(
        "intersects", "--predicate"
    ),
    where: Annotated[
        list[str] | None,
        typer.Option("--where", help="Declared equality filter FIELD=JSON."),
    ] = None,
    field: Annotated[
        list[str] | None,
        typer.Option("--field", help="Declared attribute to include."),
    ] = None,
    state: Annotated[
        str | None,
        typer.Option("--state", help="Exact historical coverage state."),
    ] = None,
    export_gpkg: Annotated[
        Path | None,
        typer.Option("--export-gpkg", help="Generated inspection export."),
    ] = None,
    replace_export: bool = typer.Option(False, "--replace-export"),
) -> None:
    """Inspect one exact spatial and equality-filtered source subset."""

    paths = _paths(ctx)

    def operation() -> dict[str, object]:
        selectors = sum(value is not None for value in (area, bbox, geometry))
        if selectors != 1:
            raise ValueError("query requires exactly one of --area, --geometry or --bbox")
        selector: BaseGeometry | None = None
        parsed_bbox: tuple[float, float, float, float] | None = None
        if area is not None:
            selector = _load_area_geometry(_absolute(area, Path.cwd()))
        elif geometry is not None:
            selector = _load_geometry(geometry)
        else:
            assert bbox is not None
            parsed_bbox = _parse_bbox(bbox)
        filters = _parse_filters(tuple(where or ()))
        projection = _unique_fields(tuple(field or ()))
        store = _store(paths)
        result = store.query(
            state_fingerprint=state,
            source_layer=layer,
            selector=selector,
            bbox=parsed_bbox,
            predicate=predicate,
            filters=filters,
            projection=projection,
        )
        rows = [_query_row_payload(row) for row in result.rows]
        export_path = None
        if export_gpkg is not None:
            resolved_export = _absolute(export_gpkg, Path.cwd())
            coverage = (
                store.status().current_coverage
                if state is None
                else store.resolve_coverage(
                    state_fingerprint=state,
                    verify=False,
                )
            )
            _export_query_result(
                result,
                resolved_export,
                source_layer=layer,
                store_path=paths.store,
                retained_source_paths=_retained_source_paths(coverage),
                replace=replace_export,
            )
            export_path = str(resolved_export)
        resolved_state = str(result.manifest["coverage_state_fingerprint"])
        return _result(
            paths,
            command="query",
            state=resolved_state,
            row_count=len(rows),
            result_fingerprint=result.fingerprint,
            manifest=dict(result.manifest),
            rows=rows,
            export_gpkg=export_path,
        )

    _execute(paths, "query", operation)


@evidence_app.command("delete")
def delete_command(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", help="Confirm recoverable store removal."),
    expect_state: str = typer.Option(
        ...,
        "--expect-state",
        help="Exact current coverage state required before moving the store.",
    ),
) -> None:
    """Move the store and lock metadata to recoverable workspace trash."""

    paths = _paths(ctx)

    def operation() -> dict[str, object]:
        if not yes:
            raise ValueError("recoverable delete requires --yes")
        with _writer_lock(paths) as lock_path:
            status = _store(paths).status(verify=True)
            coverage = status.current_coverage
            current_state = None if coverage is None else coverage.fingerprint
            if current_state != expect_state:
                raise ValueError("Local Evidence Store current state does not match --expect-state")
            if not paths.store.is_file():
                raise ValueError("Local Evidence Store file is missing")
            trash = paths.store.parent / "trash"
            trash.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            restore_path = trash / f"{paths.store.name}.{stamp}"
            os.replace(paths.store, restore_path)
            restored_lock = trash / f"{lock_path.name}.{stamp}"
            os.replace(lock_path, restored_lock)
        return _result(
            paths,
            command="delete",
            state=current_state,
            restore_path=str(restore_path),
            restore_lock_path=None if restored_lock is None else str(restored_lock),
        )

    _execute(paths, "delete", operation)


def _paths(ctx: typer.Context) -> EvidenceCliContext:
    if not isinstance(ctx.obj, EvidenceCliContext):
        raise RuntimeError("evidence CLI paths were not initialised")
    return ctx.obj


def _absolute(path: Path, invocation_dir: Path) -> Path:
    return (path if path.is_absolute() else invocation_dir / path).resolve()


def _runtime_lock_path() -> Path:
    project_lock = Path(__file__).parents[2] / "config/duckdb-spatial-runtime-lock.json"
    if project_lock.is_file():
        return project_lock
    return Path(__file__).with_name("assets") / "duckdb-spatial-runtime-lock.json"


def _store(paths: EvidenceCliContext) -> LocalEvidenceStore:
    return LocalEvidenceStore(
        store_path=paths.store,
        runtime_lock_path=_runtime_lock_path(),
        extension_cache=paths.extension_cache,
    )


def _result(
    paths: EvidenceCliContext,
    *,
    command: str,
    state: str | None,
    **values: object,
) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": True,
        "command": command,
        "store": str(paths.store),
        "extension_cache": str(paths.extension_cache),
    }
    if state is not None:
        result["state"] = state
    result.update(values)
    result["exit_code"] = 0
    return result


def _coverage_payload(
    coverage: object | None,
    *,
    provenance: bool,
    area_selector: BaseGeometry | None = None,
) -> dict[str, object]:
    if coverage is None:
        return {
            "status": "missing",
            "area_status": "unknown" if area_selector is not None else None,
            "available": 0,
            "no_data": 0,
            "explicit_unknown": 0,
            "explicit_unknown_cells": [],
            "missing": [],
            "stale": [],
            "partitions": [],
        }
    attestations = tuple(coverage.attestations)
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
                **attestation.source_export.canonical_payload(),
                "fingerprint": attestation.source_export.fingerprint,
                "provenance": dict(attestation.source_export.provenance),
            }
            item["ingestion_contract"] = {
                **content.ingestion_contract.canonical_payload(),
                "fingerprint": content.ingestion_contract.fingerprint,
            }
        partitions.append(item)
    counts = {
        name: sum(partition["availability"] == availability for partition in partitions)
        for name, availability in (
            ("available", "available"),
            ("no_data", "no-data"),
            ("explicit_unknown", "explicit-unknown"),
        )
    }
    existing = {
        (str(partition["source_layer"]), str(partition["cell"])) for partition in partitions
    }
    missing: list[str] = []
    if area_selector is not None:
        for source_layer in sorted({layer for layer, _cell in existing}):
            for key in _partition_keys(source_layer, area_selector):
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
    return {
        "status": str(coverage.state),
        "area_status": (
            None if area_selector is None else ("incomplete" if missing else "complete")
        ),
        **counts,
        "explicit_unknown_cells": explicit_unknown_cells,
        "missing": sorted(missing),
        "stale": stale,
        "partitions": sorted(
            partitions,
            key=lambda item: (str(item["source_layer"]), str(item["cell"])),
        ),
    }


def _retained_source_exists(attestation: object) -> bool:
    retained_path = attestation.source_export.provenance.get("retained_path")
    return isinstance(retained_path, str) and Path(retained_path).is_file()


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


def _load_geometry(value: str) -> BaseGeometry:
    candidate = Path(value)
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


def _partition_keys(source_layer: str, selector: BaseGeometry) -> tuple[EvidencePartitionKey, ...]:
    return evidence_partition_keys(source_layer, selector)


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
        "attributes": dict(row.attributes),
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
    if path.resolve() == store_path.resolve():
        raise ValueError("GeoPackage inspection export cannot replace the Local Evidence Store")
    if path.resolve() in {candidate.resolve() for candidate in retained_source_paths}:
        raise ValueError("GeoPackage inspection export cannot replace a retained Source Export")
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    if path.exists() and not replace:
        raise ValueError("GeoPackage inspection export already exists; use --replace-export")
    path.parent.mkdir(parents=True, exist_ok=True)
    if replace:
        path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
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
    frame.to_file(path, layer=source_layer.replace("/", "_"), driver="GPKG")
    manifest = {
        **dict(result.manifest),
        "result_fingerprint": result.fingerprint,
        "export": str(path),
    }
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _retained_source_paths(coverage: object | None) -> tuple[Path, ...]:
    if coverage is None:
        return ()
    paths: set[Path] = set()
    for attestation in coverage.attestations:
        retained_path = attestation.source_export.provenance.get("retained_path")
        if isinstance(retained_path, str) and retained_path:
            paths.add(Path(retained_path))
    return tuple(sorted(paths))


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"retained Source Export is missing: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _writer_lock(paths: EvidenceCliContext):
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


def _execute(
    paths: EvidenceCliContext,
    command: str,
    operation: object,
) -> None:
    try:
        result = operation()  # type: ignore[operator]
    except Exception as error:
        exit_code = 75 if _lock_busy(error) else 1
        payload = {
            "ok": False,
            "command": command,
            "store": str(paths.store),
            "extension_cache": str(paths.extension_cache),
            "error": str(error),
            "exit_code": exit_code,
        }
        _emit(paths.output_format, payload)
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(exit_code) from None
    _emit(paths.output_format, result)


def _lock_busy(error: Exception) -> bool:
    message = str(error).casefold()
    return "lock" in message and any(
        fragment in message for fragment in ("busy", "conflict", "could not set")
    )


def _emit(output_format: OutputFormat, payload: dict[str, object]) -> None:
    if output_format == "json":
        typer.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return
    for key, value in payload.items():
        _emit_text(key.replace("_", "-"), value)


def _emit_text(key: str, value: object) -> None:
    if isinstance(value, dict):
        for child, item in value.items():
            _emit_text(f"{key}.{child.replace('_', '-')}", item)
    elif isinstance(value, list):
        typer.echo(f"{key}: {json.dumps(value, sort_keys=True, separators=(',', ':'))}")
    elif isinstance(value, bool):
        typer.echo(f"{key}: {str(value).lower()}")
    elif value is None:
        typer.echo(f"{key}: null")
    else:
        typer.echo(f"{key}: {value}")
