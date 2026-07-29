from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pytest
import yaml
from shapely.geometry import LineString, box

from satn.evidence_contracts import EvidencePartitionKey, IngestionContract
from satn.local_evidence_store import (
    EvidenceWriterBusy,
    LocalEvidenceStore,
    SpatialRuntimeError,
    provision_spatial_runtime,
)
from satn.open_roads_adapter import contract_payload as open_roads_contract_payload

PROJECT = Path(__file__).parents[1]
SPATIAL_ARCHIVE = Path(
    os.environ.get(
        "SATN_TEST_DUCKDB_SPATIAL_EXTENSION",
        "__satn_test_duckdb_spatial_extension_not_configured__",
    )
)


def _write_descriptor(raw: Path, descriptor: Path) -> Path:
    contract_payload = open_roads_contract_payload("EPSG:27700")
    contract = IngestionContract(
        **{key: value for key, value in contract_payload.items() if key != "contract"}
    )
    descriptor.write_text(
        yaml.safe_dump(
            {
                "source_family": "os-open-roads",
                "dataset": "open-roads",
                "layer": "RoadLink",
                "publisher_release": "2026-04",
                "effective_date": "2026-04-07",
                "licence": "OGL-3.0",
                "format": "GeoJSON",
                "declared_crs": "EPSG:27700",
                "raw_bytes_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                "path": raw.name,
                "ingestion_contract": {
                    **json.loads(json.dumps(contract.canonical_payload())),
                    "fingerprint": contract.fingerprint,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return descriptor


def _descriptor(tmp_path: Path) -> Path:
    raw = tmp_path / "RoadLink.geojson"
    raw.write_text("governed bytes", encoding="utf-8")
    return _write_descriptor(raw, tmp_path / "roads.yaml")


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
    return _write_descriptor(raw, tmp_path / "roads.yaml")


def _coverage_for(
    binding,
    descriptor: Path,
    *,
    cell: str = "ST56",
    availability: str = "no-data",
) -> object:
    source = replace(
        binding.source_export,
        provenance={
            "retained_path": binding.source_export.provenance["retained_path"],
            "descriptor_path": str(descriptor),
        },
    )
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", cell)
    content = SimpleNamespace(
        partition_key=key,
        availability=availability,
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
    monkeypatch,
) -> None:
    runtime_lock = json.loads(
        (PROJECT / "config/duckdb-spatial-runtime-lock.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr(
        "satn.local_evidence_store._runtime_platform",
        lambda: runtime_lock["platform"],
    )
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


def test_failed_first_initialise_cleans_partial_store_parent_and_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store_path = tmp_path / "workspace/.satn/evidence/local-evidence.duckdb"
    workspace = LocalEvidenceStore.workspace(
        workspace=tmp_path / "workspace",
        store=store_path,
        extension_cache=tmp_path / "extensions",
        invocation_dir=tmp_path,
    )

    class Store:
        def verify_runtime(self) -> None:
            pass

        def initialise(self) -> None:
            store_path.write_text("partial", encoding="utf-8")

        def status(self, *, verify: bool = False):
            assert verify
            raise ValueError("final verify failed")

    monkeypatch.setattr(type(workspace), "_store", lambda _self: Store())

    with pytest.raises(ValueError, match="final verify failed"):
        workspace.initialise(extension_archive=None)

    assert not store_path.parent.exists()
    assert not store_path.with_suffix(store_path.suffix + ".lock").exists()


def test_busy_initialiser_cannot_unlink_lock_inode_or_admit_a_third_writer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import _evidence_operations as operations_module

    store_path = tmp_path / "workspace/.satn/evidence/local-evidence.duckdb"
    workspace = LocalEvidenceStore.workspace(
        workspace=tmp_path / "workspace",
        store=store_path,
        extension_cache=tmp_path / "extensions",
        invocation_dir=tmp_path,
    )

    class Store:
        def verify_runtime(self) -> None:
            pass

        def initialise(self) -> None:
            raise AssertionError("busy second writer must not initialise")

    monkeypatch.setattr(type(workspace), "_store", lambda _self: Store())

    with operations_module._writer_lock(workspace.paths) as lock_path:
        first_inode = lock_path.stat().st_ino

        with pytest.raises(EvidenceWriterBusy):
            workspace.initialise(extension_archive=None)

        assert lock_path.is_file()
        assert lock_path.stat().st_ino == first_inode
        with (
            pytest.raises(EvidenceWriterBusy),
            operations_module._writer_lock(workspace.paths),
        ):
            raise AssertionError("third writer must not enter")

        assert lock_path.stat().st_ino == first_inode


def test_status_verify_validates_and_reports_runtime_before_uninitialised_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[str] = []

    class Store:
        def runtime_status(self):
            calls.append("runtime")
            return {"fingerprint": "a" * 64, "duckdb_version": "1.4.4"}

        def status(self, *, verify: bool = False):
            calls.append(f"status:{verify}")
            return SimpleNamespace(state="uninitialised", current_coverage=None)

    workspace = LocalEvidenceStore.workspace(
        workspace=tmp_path,
        store=tmp_path / "evidence.duckdb",
        extension_cache=tmp_path / "extensions",
        invocation_dir=tmp_path,
    )
    monkeypatch.setattr(type(workspace), "_store", lambda _self: Store())

    result = workspace.status(
        area=None,
        state=None,
        verify=True,
        provenance=False,
    )

    assert calls == ["runtime", "status:True"]
    assert result["store_state"] == "uninitialised"
    assert result["runtime"] == {
        "fingerprint": "a" * 64,
        "duckdb_version": "1.4.4",
    }


@pytest.mark.skipif(
    not SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_real_status_verify_reports_pinned_runtime_without_initialising_store(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "extensions"
    provision_spatial_runtime(
        runtime_lock_path=Path(
            os.environ["SATN_TEST_DUCKDB_SPATIAL_RUNTIME_LOCK"]
        ),
        extension_archive=SPATIAL_ARCHIVE,
        extension_cache=cache,
    )
    workspace = LocalEvidenceStore.workspace(
        workspace=tmp_path,
        store=tmp_path / "evidence.duckdb",
        extension_cache=cache,
        invocation_dir=tmp_path,
    )

    result = workspace.status(
        area=None,
        state=None,
        verify=True,
        provenance=False,
    )

    assert result["store_state"] == "uninitialised"
    assert result["coverage"]["status"] == "missing"
    assert result["runtime"]["contract"] == "satn-duckdb-spatial-runtime/v1"
    assert result["runtime"]["fingerprint"]
    assert not workspace.paths.store.exists()


def test_descriptor_requires_an_explicit_matching_ingestion_contract(
    tmp_path: Path,
) -> None:
    from satn import _evidence_operations as operations_module

    descriptor = _descriptor(tmp_path)
    payload = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    payload.pop("ingestion_contract")
    descriptor.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="explicit ingestion_contract"):
        operations_module._load_source_descriptor(descriptor)

    descriptor = _descriptor(tmp_path)
    payload = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    payload["ingestion_contract"]["fingerprint"] = "f" * 64
    descriptor.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="fingerprint"):
        operations_module._load_source_descriptor(descriptor)


@pytest.mark.parametrize("fingerprint", ["", "A" * 64, "g" * 64])
def test_descriptor_rejects_noncanonical_ingestion_contract_fingerprint(
    tmp_path: Path,
    fingerprint: str,
) -> None:
    from satn import _evidence_operations as operations_module

    descriptor = _descriptor(tmp_path)
    payload = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    payload["ingestion_contract"]["fingerprint"] = fingerprint
    descriptor.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="full lowercase SHA-256"):
        operations_module._load_source_descriptor(descriptor)


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


def test_retained_refresh_maps_each_descriptor_only_to_its_attested_cells(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import _evidence_operations as operations_module

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old_descriptor = _descriptor(old_dir)
    new_descriptor = _descriptor(new_dir)
    (new_dir / "RoadLink.geojson").write_text("replacement bytes", encoding="utf-8")
    new_descriptor = _write_descriptor(
        new_dir / "RoadLink.geojson",
        new_descriptor,
    )
    old_binding = operations_module._load_source_descriptor(old_descriptor)
    new_binding = operations_module._load_source_descriptor(new_descriptor)
    old_coverage = _coverage_for(old_binding, old_descriptor, cell="ST56")
    new_coverage = _coverage_for(new_binding, new_descriptor, cell="ST66")
    coverage = SimpleNamespace(
        fingerprint="d" * 64,
        state="complete",
        attestations=(
            old_coverage.attestations[0],
            new_coverage.attestations[0],
        ),
        requested_partition_keys=(
            old_coverage.requested_partition_keys[0],
            new_coverage.requested_partition_keys[0],
        ),
    )

    class Store:
        captured = None

        def status(self, *, verify: bool = False):
            assert verify
            return SimpleNamespace(state="ready", current_coverage=coverage)

        def refresh_many(self, **values):
            self.captured = values
            return SimpleNamespace(
                coverage=coverage,
                reused_cells=(
                    "os-open-roads/RoadLink:ST56",
                    "os-open-roads/RoadLink:ST66",
                ),
                missing_cells=(),
                replaced_cells=(),
            )

    def partition_keys(source_layer, _selector):
        return tuple(
            EvidencePartitionKey(source_layer, "bng-10km/v1", cell)
            for cell in ("ST56", "ST66")
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
        lambda _path: box(350000, 160000, 370000, 170000),
    )
    monkeypatch.setattr(operations_module, "evidence_partition_keys", partition_keys)

    result = workspace.refresh(
        area=tmp_path / "area.yaml",
        source_exports=(),
        replace_source=None,
        expect_state=None,
        dry_run=True,
        rebuild=False,
    )

    requested = {
        request.source_export.fingerprint: tuple(
            key.cell for key in request.partition_keys
        )
        for request in store.captured["requests"]
    }
    assert requested == {
        old_binding.source_export.fingerprint: ("ST56",),
        new_binding.source_export.fingerprint: ("ST66",),
    }
    assert result["missing_cells"] == [
        "openstreetmap/lines:ST56",
        "openstreetmap/lines:ST66",
    ]


def test_retained_refresh_reports_precise_unbound_cells_before_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import _evidence_operations as operations_module

    descriptor = _descriptor(tmp_path)
    binding = operations_module._load_source_descriptor(descriptor)
    coverage = _coverage_for(binding, descriptor, cell="ST56")

    class Store:
        refresh_calls = 0

        def status(self, *, verify: bool = False):
            return SimpleNamespace(state="ready", current_coverage=coverage)

        def refresh_many(self, **_values):
            self.refresh_calls += 1
            raise AssertionError("refresh must not mutate with unresolved retained cells")

    def partition_keys(source_layer, _selector):
        return tuple(
            EvidencePartitionKey(source_layer, "bng-10km/v1", cell)
            for cell in ("ST56", "ST66")
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
        lambda _path: box(350000, 160000, 370000, 170000),
    )
    monkeypatch.setattr(operations_module, "evidence_partition_keys", partition_keys)

    with pytest.raises(ValueError, match="retained Source Export descriptors") as error:
        workspace.refresh(
            area=tmp_path / "area.yaml",
            source_exports=(),
            replace_source=None,
            expect_state=None,
            dry_run=False,
            rebuild=False,
        )

    message = str(error.value)
    assert "os-open-roads/RoadLink:ST66" in message
    assert "openstreetmap/lines:ST56" in message
    assert "openstreetmap/lines:ST66" in message
    assert store.refresh_calls == 0


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


def test_status_reports_stale_explicit_unknown_and_complete_provenance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from satn import _evidence_operations as operations_module

    descriptor = _descriptor(tmp_path)
    binding = operations_module._load_source_descriptor(descriptor)
    coverage = _coverage_for(
        binding,
        descriptor,
        availability="explicit-unknown",
    )
    (tmp_path / "RoadLink.geojson").write_text("mutated bytes", encoding="utf-8")

    class Store:
        def status(self, *, verify: bool = False):
            assert not verify
            return SimpleNamespace(state="ready", current_coverage=coverage)

    workspace = LocalEvidenceStore.workspace(
        workspace=tmp_path,
        store=tmp_path / "evidence.duckdb",
        extension_cache=tmp_path / "extensions",
        invocation_dir=tmp_path,
    )
    monkeypatch.setattr(type(workspace), "_store", lambda _self: Store())

    result = workspace.status(
        area=None,
        state=None,
        verify=False,
        provenance=True,
    )

    report = result["coverage"]
    assert report["explicit_unknown"] == 1
    assert report["explicit_unknown_cells"] == ["os-open-roads/RoadLink:ST56"]
    assert report["stale"] == ["os-open-roads/RoadLink:ST56"]
    partition = report["partitions"][0]
    assert partition["source_export"]["fingerprint"] == binding.source_export.fingerprint
    assert partition["source_export"]["provenance"] == {
        "descriptor_path": str(descriptor),
        "retained_path": str(tmp_path / "RoadLink.geojson"),
    }
    assert (
        partition["ingestion_contract"]["fingerprint"]
        == binding.ingestion_contract.fingerprint
    )
    assert partition["ingestion_contract"]["source_layer"] == "os-open-roads/RoadLink"


def test_delete_moves_store_and_lock_to_recoverable_workspace_trash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state = "a" * 64
    store_path = tmp_path / ".satn/evidence/local-evidence.duckdb"
    store_path.parent.mkdir(parents=True)
    store_path.write_text("recoverable store", encoding="utf-8")

    class Store:
        def status(self, *, verify: bool = False):
            assert verify
            return SimpleNamespace(
                state="ready",
                current_coverage=SimpleNamespace(fingerprint=state),
            )

    workspace = LocalEvidenceStore.workspace(
        workspace=tmp_path,
        store=store_path,
        extension_cache=tmp_path / "extensions",
        invocation_dir=tmp_path,
    )
    monkeypatch.setattr(type(workspace), "_store", lambda _self: Store())

    result = workspace.delete(yes=True, expect_state=state)

    restore_path = Path(result["restore_path"])
    restore_lock_path = Path(result["restore_lock_path"])
    assert not store_path.exists()
    assert restore_path.read_text(encoding="utf-8") == "recoverable store"
    assert restore_lock_path.is_file()
    assert restore_path.parent == store_path.parent / "trash"


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


def test_rebuild_rejects_source_selection_and_repairs_registered_states(
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
