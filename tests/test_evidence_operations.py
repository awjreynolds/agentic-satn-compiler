from __future__ import annotations

import hashlib
import importlib.util
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pytest
from shapely.geometry import LineString, box

from satn.evidence_contracts import EvidencePartitionKey
from satn.local_evidence_store import LocalEvidenceStore, SpatialRuntimeError

PROJECT = Path(__file__).parents[1]
SPATIAL_ARCHIVE = Path(
    os.environ.get(
        "SATN_TEST_DUCKDB_SPATIAL_EXTENSION",
        "__satn_test_duckdb_spatial_extension_not_configured__",
    )
)


def _descriptor(tmp_path: Path) -> Path:
    raw = tmp_path / "RoadLink.geojson"
    raw.write_text("governed bytes", encoding="utf-8")
    descriptor = tmp_path / "roads.yaml"
    descriptor.write_text(
        "\n".join(
            (
                "source_family: os-open-roads",
                "dataset: open-roads",
                "layer: RoadLink",
                "publisher_release: '2026-04'",
                "effective_date: '2026-04-07'",
                "licence: OGL-3.0",
                "format: GeoJSON",
                "declared_crs: EPSG:27700",
                f"raw_bytes_sha256: {hashlib.sha256(raw.read_bytes()).hexdigest()}",
                f"path: {raw.name}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return descriptor


def _open_roads_descriptor(tmp_path: Path) -> Path:
    raw = tmp_path / "RoadLink.geojson"
    gpd.GeoDataFrame(
        {
            "id": ["100"],
            "road_classification": ["A Road"],
            "road_function": ["A Road"],
            "road_classification_number": ["A4"],
            "name_1": ["London Road"],
        },
        geometry=[LineString([(368000, 165000), (376000, 165000)])],
        crs="EPSG:27700",
    ).to_file(raw, layer="RoadLink", driver="GeoJSON", index=False)
    descriptor = tmp_path / "roads.yaml"
    descriptor.write_text(
        "\n".join(
            (
                "source_family: os-open-roads",
                "dataset: open-roads",
                "layer: RoadLink",
                "publisher_release: '2026-04'",
                "effective_date: '2026-04-07'",
                "licence: OGL-3.0",
                "format: GeoJSON",
                "declared_crs: EPSG:27700",
                f"raw_bytes_sha256: {hashlib.sha256(raw.read_bytes()).hexdigest()}",
                f"path: {raw.name}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return descriptor


def _coverage_for(binding, descriptor: Path) -> object:
    source = replace(
        binding.source_export,
        provenance={
            "retained_path": binding.source_export.provenance["retained_path"],
            "descriptor_path": str(descriptor),
        },
    )
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    content = SimpleNamespace(
        partition_key=key,
        availability="no-data",
        ingestion_contract=binding.ingestion_contract,
        features=(),
        fingerprint="b" * 64,
    )
    attestation = SimpleNamespace(
        source_export=source,
        partition_content=content,
        fingerprint="c" * 64,
    )
    return SimpleNamespace(
        fingerprint="d" * 64,
        state="complete",
        attestations=(attestation,),
        requested_partition_keys=(key,),
    )


def test_workspace_resolves_all_operational_paths_from_one_invocation_directory(
    tmp_path: Path,
) -> None:
    invocation_dir = tmp_path / "invocation"
    invocation_dir.mkdir()

    workspace = LocalEvidenceStore.workspace(
        workspace=Path("workspace"),
        store=None,
        extension_cache=None,
        invocation_dir=invocation_dir,
    )

    assert workspace.paths.workspace == (invocation_dir / "workspace").resolve()
    assert workspace.paths.store == (
        invocation_dir / "workspace/.satn/evidence/local-evidence.duckdb"
    ).resolve()
    assert workspace.paths.extension_cache == (
        invocation_dir / "workspace/.satn/evidence/extensions"
    ).resolve()


def test_missing_init_runtime_leaves_no_store_parent_or_lock_inventory(
    tmp_path: Path,
) -> None:
    store = tmp_path / "workspace/.satn/evidence/local-evidence.duckdb"
    workspace = LocalEvidenceStore.workspace(
        workspace=tmp_path / "workspace",
        store=store,
        extension_cache=tmp_path / "missing-extensions",
        invocation_dir=tmp_path,
    )

    with pytest.raises(SpatialRuntimeError, match="provision"):
        workspace.initialise(extension_archive=None)

    assert not store.parent.exists()
    assert not store.with_suffix(store.suffix + ".lock").exists()


def test_cli_is_only_a_typer_and_rendering_adapter() -> None:
    source = (Path(__file__).parents[1] / "src/satn/evidence_cli.py").read_text(
        encoding="utf-8"
    )

    for implementation_detail in (
        "fcntl",
        "geopandas",
        "SourceExport",
        "IngestionContract",
        "GeoDataFrame",
        "sha256",
        "open_roads_contract_payload",
    ):
        assert implementation_detail not in source


def test_refresh_reuses_retained_descriptor_and_reports_exact_dry_run_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import _evidence_operations as operations_module

    descriptor = _descriptor(tmp_path)
    binding = operations_module._load_source_descriptor(descriptor)
    coverage = _coverage_for(binding, descriptor)

    class Store:
        captured = None

        def status(self, *, verify: bool = False):
            assert verify
            return SimpleNamespace(state="ready", current_coverage=coverage)

        def refresh_many(self, **values):
            self.captured = values
            return SimpleNamespace(
                coverage=coverage,
                reused_cells=("os-open-roads/RoadLink:ST56",),
                missing_cells=(),
                replaced_cells=(),
            )

    store = Store()
    workspace = LocalEvidenceStore.workspace(
        workspace=tmp_path,
        store=tmp_path / "evidence.duckdb",
        extension_cache=tmp_path / "extensions",
        invocation_dir=tmp_path,
    )
    monkeypatch.setattr(type(workspace), "_store", lambda _self: store)
    monkeypatch.setattr(
        operations_module,
        "_load_area_geometry",
        lambda _path: box(350000, 160000, 360000, 170000),
    )

    result = workspace.refresh(
        area=tmp_path / "area.yaml",
        source_exports=(),
        replace_source=None,
        expect_state=None,
        dry_run=True,
        rebuild=False,
    )

    assert store.captured["dry_run"] is True
    assert len(store.captured["requests"]) == 1
    assert result["sources"] == [
        {
            "source_layer": "os-open-roads/RoadLink",
            "source_export": binding.source_export.fingerprint,
            "ingestion_contract": binding.ingestion_contract.fingerprint,
            "retained_bytes": len("governed bytes"),
            "partition_count": 1,
            "feature_count": 0,
            "availability": {
                "available": 0,
                "no-data": 1,
                "explicit-unknown": 0,
            },
        }
    ]


def test_status_reports_wholly_absent_supported_layers_for_an_area(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import _evidence_operations as operations_module

    descriptor = _descriptor(tmp_path)
    binding = operations_module._load_source_descriptor(descriptor)
    coverage = _coverage_for(binding, descriptor)

    class Store:
        def status(self, *, verify: bool = False):
            return SimpleNamespace(state="ready", current_coverage=coverage)

    workspace = LocalEvidenceStore.workspace(
        workspace=tmp_path,
        store=tmp_path / "evidence.duckdb",
        extension_cache=tmp_path / "extensions",
        invocation_dir=tmp_path,
    )
    monkeypatch.setattr(type(workspace), "_store", lambda _self: Store())
    monkeypatch.setattr(
        operations_module,
        "_load_area_geometry",
        lambda _path: box(350000, 160000, 360000, 170000),
    )

    result = workspace.status(
        area=tmp_path / "area.yaml",
        state=None,
        verify=False,
        provenance=False,
    )

    missing_osm = result["coverage"]["layers"]["openstreetmap/lines"]["missing"]
    assert missing_osm
    assert all(item.startswith("openstreetmap/lines:") for item in missing_osm)


def test_query_export_cannot_replace_any_historically_retained_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    protected = tmp_path / "historical-source.gpkg"
    protected.write_text("historical", encoding="utf-8")

    class Store:
        def query(self, **_values):
            return SimpleNamespace(
                rows=(),
                manifest={"coverage_state_fingerprint": "e" * 64},
                fingerprint="f" * 64,
            )

        def retained_source_paths(self):
            return (protected,)

    workspace = LocalEvidenceStore.workspace(
        workspace=tmp_path,
        store=tmp_path / "evidence.duckdb",
        extension_cache=tmp_path / "extensions",
        invocation_dir=tmp_path,
    )
    monkeypatch.setattr(type(workspace), "_store", lambda _self: Store())

    with pytest.raises(ValueError, match="retained Source Export"):
        workspace.query(
            layer="os-open-roads/RoadLink",
            area=None,
            bbox="350000,160000,360000,170000",
            geometry=None,
            predicate="intersects",
            where=(),
            fields=(),
            state=None,
            export_gpkg=protected,
            replace_export=True,
        )

    assert protected.read_text(encoding="utf-8") == "historical"


def test_rebuild_rejects_source_selection_and_only_repairs_current_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import _evidence_operations as operations_module

    rebuilt = SimpleNamespace(
        coverage=SimpleNamespace(
            fingerprint="1" * 64,
            state="complete",
            attestations=(),
        )
    )

    class Store:
        rebuild_calls = 0

        def rebuild(self):
            self.rebuild_calls += 1
            return rebuilt

    store = Store()
    workspace = LocalEvidenceStore.workspace(
        workspace=tmp_path,
        store=tmp_path / "evidence.duckdb",
        extension_cache=tmp_path / "extensions",
        invocation_dir=tmp_path,
    )
    monkeypatch.setattr(type(workspace), "_store", lambda _self: store)
    monkeypatch.setattr(
        operations_module,
        "_load_area_geometry",
        lambda _path: box(350000, 160000, 360000, 170000),
    )

    with pytest.raises(ValueError, match="cannot refresh sources"):
        workspace.refresh(
            area=tmp_path / "area.yaml",
            source_exports=(tmp_path / "new-source.yaml",),
            replace_source=None,
            expect_state=None,
            dry_run=False,
            rebuild=True,
        )

    result = workspace.refresh(
        area=tmp_path / "area.yaml",
        source_exports=(),
        replace_source=None,
        expect_state=None,
        dry_run=False,
        rebuild=True,
    )

    assert store.rebuild_calls == 1
    assert result["state"] == "1" * 64


@pytest.mark.skipif(
    not SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_real_workspace_plan_apply_reuse_query_and_export_are_one_exact_flow(
    tmp_path: Path,
) -> None:
    workspace = LocalEvidenceStore.workspace(
        workspace=tmp_path,
        store=None,
        extension_cache=None,
        invocation_dir=tmp_path,
    )
    workspace.initialise(extension_archive=SPATIAL_ARCHIVE)
    descriptor = _open_roads_descriptor(tmp_path)
    area = PROJECT / "tests/fixtures/bath-saltford/bath-saltford.yaml"

    plan = workspace.refresh(
        area=area,
        source_exports=(descriptor,),
        replace_source=None,
        expect_state=None,
        dry_run=True,
        rebuild=False,
    )
    assert workspace.status(
        area=None,
        state=None,
        verify=True,
        provenance=False,
    )["coverage"]["status"] == "missing"

    applied = workspace.refresh(
        area=area,
        source_exports=(descriptor,),
        replace_source=None,
        expect_state=None,
        dry_run=False,
        rebuild=False,
    )
    reused = workspace.refresh(
        area=area,
        source_exports=(),
        replace_source=None,
        expect_state=None,
        dry_run=True,
        rebuild=False,
    )

    assert applied["state"] == plan["state"] == reused["state"]
    assert reused["reused_cells"]
    export = tmp_path / "inspection.gpkg"
    queried = workspace.query(
        layer="os-open-roads/RoadLink",
        area=area,
        bbox=None,
        geometry=None,
        predicate="intersects",
        where=(),
        fields=("road_classification_number",),
        state=applied["state"],
        export_gpkg=export,
        replace_export=False,
    )

    assert queried["row_count"] == 1
    assert export.is_file()
    assert export.with_suffix(".gpkg.manifest.json").is_file()
