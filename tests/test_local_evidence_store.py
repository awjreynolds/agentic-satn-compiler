"""Behavioural tests for the additive Local Evidence Store seam."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest
from shapely.geometry import LineString

import satn.local_evidence_store as local_evidence_store
from satn.evidence_contracts import (
    EvidenceCoverage,
    EvidencePartitionKey,
    IngestionContract,
    SourceExport,
)
from satn.local_evidence_store import (
    EvidenceStoreSchemaError,
    LocalEvidenceStore,
    SpatialRuntimeError,
    SpatialRuntimeLock,
    provision_spatial_runtime,
)

PROJECT = Path(__file__).parents[1]
LOCAL_SPATIAL_ARCHIVE = Path(
    "/private/tmp/banes-satn-embedded-store-benchmark/duckdb_extensions/"
    "v1.4.4/osx_arm64/spatial.duckdb_extension"
)


def _runtime_lock(
    path: Path,
    *,
    platform: str = "osx_arm64",
    extension_sha256: str,
) -> Path:
    runtime_lock_path = path / "runtime-lock.json"
    runtime_lock_path.write_text(
        json.dumps(
            {
                "contract": "satn-duckdb-spatial-runtime/v1",
                "duckdb_version": "1.4.4",
                "spatial_version": "f129b24",
                "platform": platform,
                "extension_relative_path": "v1.4.4/osx_arm64/spatial.duckdb_extension",
                "extension_sha256": extension_sha256,
            }
        ),
        encoding="utf-8",
    )
    return runtime_lock_path


def _store(tmp_path: Path, *, runtime_lock_path: Path) -> LocalEvidenceStore:
    return LocalEvidenceStore(
        store_path=tmp_path / "evidence.duckdb",
        runtime_lock_path=runtime_lock_path,
        extension_cache=tmp_path / "extensions",
    )


def _real_store(tmp_path: Path) -> LocalEvidenceStore:
    runtime_lock_path = PROJECT / "config" / "duckdb-spatial-runtime-lock.json"
    extension_cache = tmp_path / "extensions"
    provision_spatial_runtime(
        runtime_lock_path=runtime_lock_path,
        extension_archive=LOCAL_SPATIAL_ARCHIVE,
        extension_cache=extension_cache,
    )
    return LocalEvidenceStore(
        store_path=tmp_path / "evidence.duckdb",
        runtime_lock_path=runtime_lock_path,
        extension_cache=extension_cache,
    )


def _open_roads_contract(*, source_crs: str = "EPSG:27700") -> IngestionContract:
    return IngestionContract(
        source_layer="os-open-roads/RoadLink",
        contract_version="satn-open-roads-ingestion/v1",
        accepted_schema={
            "id": "string",
            "name_1": "string|null",
            "road_classification": "string",
            "road_classification_number": "string|null",
            "road_function": "string",
        },
        stable_feature_key_policy="source-export-roadlink-id/v1",
        selected_attributes=(
            "road_classification",
            "road_function",
            "road_classification_number",
            "name_1",
        ),
        normalisation={"trim_strings": True},
        crs_transform={
            "source_crs": source_crs,
            "target_crs": "EPSG:27700",
            "axis_order": "always_xy",
        },
        partition_scheme="bng-10km/v1",
        spatial_predicate="intersects",
        implementation_dependency_fingerprint=(
            local_evidence_store._open_roads_adapter_fingerprint()
        ),
    )


def _write_open_roads_fixture(
    path: Path,
    *,
    driver: str = "GeoJSON",
    crs: str = "EPSG:27700",
    inside_id: str = "100",
    drop_column: str | None = None,
) -> Path:
    frame = gpd.GeoDataFrame(
        {
            "id": [inside_id, "outside"],
            "road_classification": ["  A Road  ", "B Road"],
            "road_function": ["A Road", "B Road"],
            "road_classification_number": [" A4 ", "B1"],
            "name_1": [" London Road ", "Outside Road"],
        },
        geometry=[
            LineString([(349000, 165000), (361000, 165000)]),
            LineString([(370000, 165000), (371000, 165000)]),
        ],
        crs="EPSG:27700",
    )
    if crs != "EPSG:27700":
        frame = frame.to_crs(crs)
    if drop_column is not None:
        frame = frame.drop(columns=[drop_column])
    frame.to_file(path, layer="RoadLink", driver=driver, index=False)
    return path


def _source_export_for(path: Path, *, format: str, crs: str = "EPSG:27700") -> SourceExport:
    return SourceExport(
        source_family="os-open-roads",
        dataset="open-roads",
        layer="RoadLink",
        publisher_release="2026-04",
        effective_date="2026-04-07",
        licence="OGL-3.0",
        format=format,
        declared_crs=crs,
        raw_bytes_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        provenance={"retained_path": str(path.resolve())},
    )


def _seeded_real_store(tmp_path: Path) -> tuple[LocalEvidenceStore, EvidenceCoverage]:
    store = _real_store(tmp_path)
    store.initialise()
    source_path = _write_open_roads_fixture(tmp_path / "RoadLink.geojson")
    source_export = _source_export_for(source_path, format="GeoJSON")
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    refreshed = store.refresh(
        source_export=source_export,
        ingestion_contract=_open_roads_contract(),
        partition_keys=(key,),
    )
    return store, refreshed.coverage


def _mutate_store(
    store: LocalEvidenceStore,
    statement: str,
    parameters: list[object] | None = None,
) -> None:
    connection = store._connect(read_only=False)
    try:
        connection.execute(statement, parameters or [])
    finally:
        connection.close()


def test_repository_runtime_lock_pins_the_benchmarked_duckdb_and_spatial_runtime() -> None:
    lock = SpatialRuntimeLock.from_json(PROJECT / "config" / "duckdb-spatial-runtime-lock.json")

    assert lock.duckdb_version == "1.4.4"
    assert lock.spatial_version == "f129b24"
    assert lock.extension_sha256 == (
        "21a2d9de1bc82fde782b8b55822b5c5e94c487be8f239dff2f8241ffcc869f55"
    )


def test_initialise_fails_before_database_creation_without_a_provisioned_extension(
    tmp_path: Path,
) -> None:
    store = _store(
        tmp_path,
        runtime_lock_path=_runtime_lock(tmp_path, extension_sha256="a" * 64),
    )

    with pytest.raises(SpatialRuntimeError, match=r"provision.*pinned Spatial extension"):
        store.initialise()

    assert not (tmp_path / "evidence.duckdb").exists()


def test_initialise_rejects_a_cached_extension_with_the_wrong_checksum(tmp_path: Path) -> None:
    runtime_lock_path = _runtime_lock(tmp_path, extension_sha256="a" * 64)
    cached = tmp_path / "extensions" / "v1.4.4/osx_arm64/spatial.duckdb_extension"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"not the locked extension")

    with pytest.raises(SpatialRuntimeError, match="cached checksum"):
        _store(tmp_path, runtime_lock_path=runtime_lock_path).initialise()

    assert not (tmp_path / "evidence.duckdb").exists()


def test_initialise_rejects_a_runtime_lock_for_another_platform(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        runtime_lock_path=_runtime_lock(
            tmp_path,
            platform="linux_x86_64",
            extension_sha256="a" * 64,
        ),
    )

    with pytest.raises(SpatialRuntimeError, match="targets linux_x86_64"):
        store.initialise()

    assert not (tmp_path / "evidence.duckdb").exists()


def test_initialise_rejects_a_different_duckdb_version_before_opening_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension = b"synthetic locked extension"
    runtime_lock_path = _runtime_lock(
        tmp_path,
        extension_sha256=hashlib.sha256(extension).hexdigest(),
    )
    cached = tmp_path / "extensions" / "v1.4.4/osx_arm64/spatial.duckdb_extension"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(extension)

    class WrongVersionDuckDB:
        __version__ = "1.4.3"

    monkeypatch.setattr(local_evidence_store, "_duckdb_module", lambda: WrongVersionDuckDB)

    with pytest.raises(SpatialRuntimeError, match=r"DuckDB 1\.4\.4 is required; found 1\.4\.3"):
        _store(tmp_path, runtime_lock_path=runtime_lock_path).initialise()

    assert not (tmp_path / "evidence.duckdb").exists()


def test_status_rejects_a_different_duckdb_version_before_opening_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension = b"synthetic locked extension"
    runtime_lock_path = _runtime_lock(
        tmp_path,
        extension_sha256=hashlib.sha256(extension).hexdigest(),
    )
    cached = tmp_path / "extensions" / "v1.4.4/osx_arm64/spatial.duckdb_extension"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(extension)
    store_path = tmp_path / "evidence.duckdb"
    store_path.write_bytes(b"existing store bytes")

    class WrongVersionDuckDB:
        __version__ = "1.4.3"

    monkeypatch.setattr(local_evidence_store, "_duckdb_module", lambda: WrongVersionDuckDB)

    with pytest.raises(SpatialRuntimeError, match=r"DuckDB 1\.4\.4 is required; found 1\.4\.3"):
        _store(tmp_path, runtime_lock_path=runtime_lock_path).status()

    assert store_path.read_bytes() == b"existing store bytes"


def test_status_reports_uninitialised_without_creating_a_missing_store(tmp_path: Path) -> None:
    store_path = tmp_path / "missing-parent" / "evidence.duckdb"
    store = LocalEvidenceStore(
        store_path=store_path,
        runtime_lock_path=PROJECT / "config" / "duckdb-spatial-runtime-lock.json",
        extension_cache=tmp_path / "missing-extension-cache",
    )

    status = store.status()

    assert status.state == "uninitialised"
    assert status.current_coverage is None
    assert not store_path.parent.exists()


def test_initialise_disables_automatic_extensions_and_loads_only_the_locked_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension = b"synthetic locked extension"
    runtime_lock_path = _runtime_lock(
        tmp_path,
        extension_sha256=hashlib.sha256(extension).hexdigest(),
    )
    cached = tmp_path / "extensions" / "v1.4.4/osx_arm64/spatial.duckdb_extension"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(extension)
    statements: list[str] = []

    class Result:
        def fetchone(self) -> tuple[str]:
            return ("f129b24",)

    class Connection:
        def execute(
            self, statement: str, _parameters: list[object] | None = None
        ) -> Result | Connection:
            statements.append(statement)
            return Result() if "duckdb_extensions" in statement else self

        def close(self) -> None:
            return None

    class DuckDB:
        __version__ = "1.4.4"

        @staticmethod
        def connect(_database: str, *, read_only: bool = False) -> Connection:
            assert not read_only
            return Connection()

    monkeypatch.setattr(local_evidence_store, "_duckdb_module", lambda: DuckDB)

    _store(tmp_path, runtime_lock_path=runtime_lock_path).initialise()

    assert "SET autoinstall_known_extensions = false" in statements
    assert "SET autoload_known_extensions = false" in statements
    assert f"LOAD '{cached}'" in statements
    assert not any(statement.upper().startswith("INSTALL ") for statement in statements)


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_initialise_loads_only_the_explicit_provisioned_spatial_binary(tmp_path: Path) -> None:
    store = _real_store(tmp_path)

    store.initialise()

    assert (tmp_path / "evidence.duckdb").is_file()


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_refresh_reads_governed_source_bytes_and_preserves_full_crossing_geometry(
    tmp_path: Path,
) -> None:
    source_path = _write_open_roads_fixture(tmp_path / "RoadLink.geojson")
    source_export = _source_export_for(source_path, format="GeoJSON")
    store = _real_store(tmp_path)
    store.initialise()

    refreshed = store.refresh(
        source_export=source_export,
        ingestion_contract=_open_roads_contract(),
        partition_keys=(EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56"),),
    )

    attestation = refreshed.coverage.attestations[0]
    assert attestation.partition_content.availability == "available"
    assert len(attestation.partition_content.features) == 1
    assert attestation.partition_content.features[0] == {
        "logical_key": "roadlink:100",
        "geometry_fingerprint": (
            "3a1fd2bfd847426a50cdde12737278580c6e09e072fa85748628be567999453f"
        ),
        "attributes": {
            "name_1": "London Road",
            "road_classification": "A Road",
            "road_classification_number": "A4",
            "road_function": "A Road",
        },
    }
    assert attestation.source_export.provenance["retained_path"] == str(source_path.resolve())


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_refresh_rejects_forged_bytes_and_untrusted_adapter_identity(
    tmp_path: Path,
) -> None:
    source_path = _write_open_roads_fixture(tmp_path / "RoadLink.geojson")
    source_export = _source_export_for(source_path, format="GeoJSON")
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    store = _real_store(tmp_path)
    store.initialise()

    forged_export = replace(
        source_export,
        raw_bytes_sha256="0" * 64,
        fingerprint="",
    )
    with pytest.raises(ValueError, match="checksum"):
        store.refresh(
            source_export=forged_export,
            ingestion_contract=_open_roads_contract(),
            partition_keys=(key,),
        )

    untrusted_contract = replace(
        _open_roads_contract(),
        implementation_dependency_fingerprint="0" * 64,
        fingerprint="",
    )
    with pytest.raises(ValueError, match="untrusted"):
        store.refresh(
            source_export=source_export,
            ingestion_contract=untrusted_contract,
            partition_keys=(key,),
        )

    assert store.status(verify=True).current_coverage is None


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_refresh_fails_closed_on_schema_crs_format_and_normalisation_mismatch(
    tmp_path: Path,
) -> None:
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    store = _real_store(tmp_path)
    store.initialise()

    missing_field_path = _write_open_roads_fixture(
        tmp_path / "missing-field.geojson",
        drop_column="road_function",
    )
    with pytest.raises(ValueError, match="missing required schema fields"):
        store.refresh(
            source_export=_source_export_for(missing_field_path, format="GeoJSON"),
            ingestion_contract=_open_roads_contract(),
            partition_keys=(key,),
        )

    wrong_crs_path = _write_open_roads_fixture(
        tmp_path / "wrong-crs.geojson",
        crs="EPSG:4326",
    )
    wrong_crs_export = _source_export_for(wrong_crs_path, format="GeoJSON")
    with pytest.raises(ValueError, match="CRS"):
        store.refresh(
            source_export=wrong_crs_export,
            ingestion_contract=_open_roads_contract(),
            partition_keys=(key,),
        )

    source_path = _write_open_roads_fixture(tmp_path / "wrong-format.geojson")
    wrong_format_export = replace(
        _source_export_for(source_path, format="GeoJSON"),
        format="GeoPackage",
        fingerprint="",
    )
    with pytest.raises(ValueError, match="format"):
        store.refresh(
            source_export=wrong_format_export,
            ingestion_contract=_open_roads_contract(),
            partition_keys=(key,),
        )

    untrusted_normalisation = replace(
        _open_roads_contract(),
        normalisation={"trim_strings": False},
        fingerprint="",
    )
    with pytest.raises(ValueError, match="untrusted"):
        store.refresh(
            source_export=_source_export_for(source_path, format="GeoJSON"),
            ingestion_contract=untrusted_normalisation,
            partition_keys=(key,),
        )

    assert store.status(verify=True).current_coverage is None


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_refresh_transforms_source_crs_but_preserves_full_geometry(
    tmp_path: Path,
) -> None:
    source_path = _write_open_roads_fixture(
        tmp_path / "RoadLink.geojson",
        crs="EPSG:4326",
    )
    source_export = _source_export_for(
        source_path,
        format="GeoJSON",
        crs="EPSG:4326",
    )
    store = _real_store(tmp_path)
    store.initialise()

    refreshed = store.refresh(
        source_export=source_export,
        ingestion_contract=_open_roads_contract(source_crs="EPSG:4326"),
        partition_keys=(EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56"),),
    )

    feature = refreshed.coverage.attestations[0].partition_content.features[0]
    assert feature["geometry_fingerprint"] == (
        "61d54acb1773506030bfbc6355fe42cf15bcefad462a9983f378c188ecfbb53b"
    )


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_relocated_retained_export_replays_identically_and_verifies_new_path(
    tmp_path: Path,
) -> None:
    source_path = _write_open_roads_fixture(tmp_path / "RoadLink.geojson")
    source_export = _source_export_for(source_path, format="GeoJSON")
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    store = _real_store(tmp_path)
    store.initialise()
    first = store.refresh(
        source_export=source_export,
        ingestion_contract=_open_roads_contract(),
        partition_keys=(key,),
    )
    moved_path = tmp_path / "retained" / "RoadLink.geojson"
    moved_path.parent.mkdir()
    source_path.replace(moved_path)
    relocated_export = replace(
        source_export,
        provenance={"retained_path": str(moved_path.resolve())},
    )

    replay = store.refresh(
        source_export=relocated_export,
        ingestion_contract=_open_roads_contract(),
        partition_keys=(key,),
    )

    assert replay.coverage.fingerprint == first.coverage.fingerprint
    verified = store.status(verify=True)
    assert verified.current_coverage == replay.coverage
    assert verified.current_coverage.attestations[0].source_export.provenance[
        "retained_path"
    ] == str(moved_path.resolve())


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_export_identity_is_separate_from_equal_normalised_partition_content(
    tmp_path: Path,
) -> None:
    first_path = _write_open_roads_fixture(tmp_path / "RoadLink-first.geojson")
    second_path = tmp_path / "RoadLink-second.geojson"
    second_path.write_bytes(first_path.read_bytes() + b" ")
    first_export = _source_export_for(first_path, format="GeoJSON")
    second_export = _source_export_for(second_path, format="GeoJSON")
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    store = _real_store(tmp_path)
    store.initialise()

    first = store.refresh(
        source_export=first_export,
        ingestion_contract=_open_roads_contract(),
        partition_keys=(key,),
    ).coverage.attestations[0]
    second = store.refresh(
        source_export=second_export,
        ingestion_contract=_open_roads_contract(),
        partition_keys=(key,),
    ).coverage.attestations[0]

    assert first.partition_content.fingerprint == second.partition_content.fingerprint
    assert first.source_export.fingerprint != second.source_export.fingerprint
    assert first.fingerprint != second.fingerprint


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_verify_rejects_mutated_retained_source_bytes(tmp_path: Path) -> None:
    store, _ = _seeded_real_store(tmp_path)
    source_path = tmp_path / "RoadLink.geojson"
    source_path.write_bytes(source_path.read_bytes() + b" ")

    with pytest.raises(EvidenceStoreSchemaError, match="retained Source Export bytes"):
        store.status(verify=True)


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_exact_repeat_initialise_validates_metadata_without_mutating_store_bytes(
    tmp_path: Path,
) -> None:
    store = _real_store(tmp_path)
    store.initialise()
    store_path = tmp_path / "evidence.duckdb"
    before = store_path.read_bytes()
    runtime_lock = SpatialRuntimeLock.from_json(
        PROJECT / "config" / "duckdb-spatial-runtime-lock.json"
    )

    store.initialise()

    assert store_path.read_bytes() == before
    connection = store._connect(read_only=True)
    try:
        metadata = connection.execute(
            """
            SELECT schema_contract, runtime_lock_fingerprint
            FROM local_evidence_store_metadata
            WHERE singleton = true
            """
        ).fetchone()
    finally:
        connection.close()
    assert metadata == (
        "satn-local-evidence-store-physical-schema/v2",
        runtime_lock.fingerprint,
    )


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_existing_partial_store_fails_closed_without_schema_or_byte_mutation(
    tmp_path: Path,
) -> None:
    import duckdb

    store = _real_store(tmp_path)
    store_path = tmp_path / "evidence.duckdb"
    connection = duckdb.connect(str(store_path))
    connection.execute("CREATE TABLE accidental_partial_table (value INTEGER)")
    connection.close()
    before = store_path.read_bytes()
    source_path = _write_open_roads_fixture(tmp_path / "RoadLink.geojson")
    source_export = _source_export_for(source_path, format="GeoJSON")
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")

    for operation in (
        lambda: store.status(),
        lambda: store.refresh(
            source_export=source_export,
            ingestion_contract=_open_roads_contract(),
            partition_keys=(key,),
        ),
        store.initialise,
    ):
        with pytest.raises(EvidenceStoreSchemaError, match="rebuild"):
            operation()
        assert store_path.read_bytes() == before

    connection = duckdb.connect(str(store_path), read_only=True)
    try:
        tables = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    finally:
        connection.close()
    assert tables == [("accidental_partial_table",)]


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("schema_contract", "satn-local-evidence-store-physical-schema/v999"),
        ("runtime_lock_fingerprint", "0" * 64),
    ),
)
def test_incompatible_store_metadata_fails_closed_without_byte_mutation(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    store = _real_store(tmp_path)
    store.initialise()
    _mutate_store(
        store,
        f"UPDATE local_evidence_store_metadata SET {column} = ? WHERE singleton = true",
        [value],
    )
    store_path = tmp_path / "evidence.duckdb"
    before = store_path.read_bytes()

    with pytest.raises(EvidenceStoreSchemaError, match="rebuild"):
        store.status()
    assert store_path.read_bytes() == before

    with pytest.raises(EvidenceStoreSchemaError, match="rebuild"):
        store.initialise()
    assert store_path.read_bytes() == before


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_refresh_persists_availability_as_an_immutable_attested_coverage_state(
    tmp_path: Path,
) -> None:
    runtime_lock_path = PROJECT / "config" / "duckdb-spatial-runtime-lock.json"
    extension_cache = tmp_path / "extensions"
    provision_spatial_runtime(
        runtime_lock_path=runtime_lock_path,
        extension_archive=LOCAL_SPATIAL_ARCHIVE,
        extension_cache=extension_cache,
    )
    store = LocalEvidenceStore(
        store_path=tmp_path / "evidence.duckdb",
        runtime_lock_path=runtime_lock_path,
        extension_cache=extension_cache,
    )
    store.initialise()
    available = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    no_data = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST57")
    source_path = _write_open_roads_fixture(tmp_path / "RoadLink.geojson")
    export = _source_export_for(source_path, format="GeoJSON")
    contract = _open_roads_contract()

    refreshed = store.refresh(
        source_export=export,
        ingestion_contract=contract,
        partition_keys=(available, no_data),
    )

    status = store.status(verify=True)

    assert refreshed.coverage.state == "complete"
    assert status.current_coverage == refreshed.coverage
    assert sorted(
        item.partition_content.availability for item in status.current_coverage.attestations
    ) == ["available", "no-data"]


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
@pytest.mark.parametrize(
    ("statement", "parameters"),
    (
        (
            "UPDATE open_roads_roadlink SET logical_key = ?",
            ["roadlink:tampered"],
        ),
        (
            "UPDATE open_roads_roadlink SET name_1 = ?",
            ["tampered road name"],
        ),
        (
            "UPDATE open_roads_roadlink SET geometry_fingerprint = ?",
            ["0" * 64],
        ),
        (
            "UPDATE open_roads_roadlink SET canonical_geometry_json = ?",
            ["{}"],
        ),
        (
            """
            UPDATE open_roads_roadlink
            SET geometry = ST_GeomFromText(
                'LINESTRING (350000 165000, 351000 166000)'
            )
            """,
            [],
        ),
        (
            "UPDATE partition_content_registry SET canonical_payload_json = ?",
            ["{}"],
        ),
        (
            "UPDATE source_export_registry SET provenance_json = ?",
            ['{"retrieved_by":"tampered"}'],
        ),
        (
            "DELETE FROM source_export_registry",
            [],
        ),
    ),
    ids=(
        "logical-key",
        "typed-attribute",
        "geometry-fingerprint",
        "canonical-geometry",
        "physical-geometry",
        "canonical-registry-payload",
        "source-provenance",
        "dependency-closure",
    ),
)
def test_verify_rejects_tampered_registry_typed_and_geometry_state_without_mutation(
    tmp_path: Path,
    statement: str,
    parameters: list[object],
) -> None:
    store, _ = _seeded_real_store(tmp_path)
    _mutate_store(store, statement, parameters)
    store_path = tmp_path / "evidence.duckdb"
    before = store_path.read_bytes()

    with pytest.raises(EvidenceStoreSchemaError, match="rebuild"):
        store.status(verify=True)

    assert store_path.read_bytes() == before


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_same_named_btree_cannot_satisfy_the_required_open_roads_rtree(
    tmp_path: Path,
) -> None:
    store, _ = _seeded_real_store(tmp_path)
    _mutate_store(store, "DROP INDEX open_roads_roadlink_geometry_rtree")
    _mutate_store(
        store,
        """
        CREATE INDEX open_roads_roadlink_geometry_rtree
        ON open_roads_roadlink (logical_key)
        """,
    )
    store_path = tmp_path / "evidence.duckdb"
    before = store_path.read_bytes()

    with pytest.raises(EvidenceStoreSchemaError, match=r"RTree binding.*rebuild"):
        store.status()

    assert store_path.read_bytes() == before


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_late_refresh_failure_rolls_back_before_advancing_the_current_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, prior_coverage = _seeded_real_store(tmp_path)
    source_path = _write_open_roads_fixture(
        tmp_path / "RoadLink-late-failure.geojson",
        inside_id="late-failure",
    )
    source_export = _source_export_for(source_path, format="GeoJSON")
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    original_insert_coverage_state = store._insert_coverage_state

    def fail_before_pointer(connection: Any, coverage: EvidenceCoverage) -> None:
        original_insert_coverage_state(connection, coverage)
        raise RuntimeError("forced failure before current pointer advancement")

    monkeypatch.setattr(store, "_insert_coverage_state", fail_before_pointer)

    with pytest.raises(RuntimeError, match="forced failure"):
        store.refresh(
            source_export=source_export,
            ingestion_contract=_open_roads_contract(),
            partition_keys=(key,),
        )

    assert store.status(verify=True).current_coverage == prior_coverage
    connection = store._connect(read_only=True)
    try:
        leaked = connection.execute(
            "SELECT logical_key FROM open_roads_roadlink WHERE logical_key = ?",
            ["roadlink:late-failure"],
        ).fetchone()
    finally:
        connection.close()
    assert leaked is None


@pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
def test_refresh_rolls_back_staged_rows_and_preserves_the_prior_current_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, prior_coverage = _seeded_real_store(tmp_path)
    available = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    no_data = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST57")
    source_path = _write_open_roads_fixture(
        tmp_path / "RoadLink-rollback.geojson",
        inside_id="101",
    )
    source_export = _source_export_for(source_path, format="GeoJSON")
    original_reader = store._read_open_roads_partition

    def fail_second_partition(
        source_path: Path,
        source_export: SourceExport,
        ingestion_contract: IngestionContract,
        partition_key: EvidencePartitionKey,
    ) -> object:
        if partition_key == no_data:
            raise ValueError("forced governed reader failure")
        return original_reader(
            source_path,
            source_export,
            ingestion_contract,
            partition_key,
        )

    monkeypatch.setattr(store, "_read_open_roads_partition", fail_second_partition)

    with pytest.raises(ValueError, match="forced governed reader failure"):
        store.refresh(
            source_export=source_export,
            ingestion_contract=_open_roads_contract(),
            partition_keys=(available, no_data),
        )

    assert store.status(verify=True).current_coverage == prior_coverage
    connection = store._connect(read_only=True)
    try:
        leaked = connection.execute(
            "SELECT logical_key FROM open_roads_roadlink WHERE logical_key = ?",
            ["roadlink:101"],
        ).fetchone()
    finally:
        connection.close()
    assert leaked is None
