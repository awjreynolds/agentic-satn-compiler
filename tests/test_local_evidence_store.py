"""Behavioural tests for the additive Local Evidence Store seam."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

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
    EvidenceFeature,
    EvidencePartitionInput,
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


def _open_roads_export() -> SourceExport:
    return SourceExport(
        source_family="os-open-roads",
        dataset="open-roads",
        layer="RoadLink",
        publisher_release="2026-04",
        effective_date="2026-04-07",
        licence="OGL-3.0",
        format="GeoJSON",
        declared_crs="EPSG:27700",
        raw_bytes_sha256="a" * 64,
        provenance={"retrieved_by": "test-suite", "source_uri": "local://open-roads"},
    )


def _open_roads_contract() -> IngestionContract:
    return IngestionContract(
        source_layer="os-open-roads/RoadLink",
        contract_version="satn-open-roads-ingestion/v1",
        accepted_schema={"road_name": "string", "road_number": "string"},
        stable_feature_key_policy="publisher-roadlink-id/v1",
        selected_attributes=("road_name", "road_number"),
        normalisation={"trim_road_name": True},
        crs_transform={
            "source_crs": "EPSG:27700",
            "target_crs": "EPSG:27700",
            "axis_order": "always_xy",
        },
        partition_scheme="bng-10km/v1",
        spatial_predicate="intersects",
        implementation_dependency_fingerprint="b" * 64,
    )


def _available_partition(
    *,
    cell: str = "ST56",
    logical_key: str = "roadlink:100",
) -> tuple[EvidencePartitionKey, EvidencePartitionInput]:
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", cell)
    return key, EvidencePartitionInput(
        source_export=_open_roads_export(),
        ingestion_contract=_open_roads_contract(),
        partition_key=key,
        availability="available",
        features=(
            EvidenceFeature(
                logical_key=logical_key,
                geometry=LineString([(350000, 165000), (351000, 165000)]),
                attributes={"road_name": "A4", "road_number": "A4"},
            ),
        ),
    )


def _seeded_real_store(tmp_path: Path) -> tuple[LocalEvidenceStore, EvidenceCoverage]:
    store = _real_store(tmp_path)
    store.initialise()
    key, partition = _available_partition()
    refreshed = store.refresh(
        requested_partition_keys=(key,),
        partitions=(partition,),
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
        "satn-local-evidence-store-physical-schema/v1",
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

    for operation in (
        lambda: store.status(),
        lambda: store.refresh(requested_partition_keys=(), partitions=()),
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
    export = _open_roads_export()
    contract = _open_roads_contract()

    refreshed = store.refresh(
        requested_partition_keys=(available, no_data),
        partitions=iter(
            (
                EvidencePartitionInput(
                    source_export=export,
                    ingestion_contract=contract,
                    partition_key=available,
                    availability="available",
                    features=(
                        EvidenceFeature(
                            logical_key="roadlink:100",
                            geometry=LineString([(350000, 165000), (351000, 165000)]),
                            attributes={"road_name": "A4", "road_number": "A4"},
                        ),
                    ),
                ),
                EvidencePartitionInput(
                    source_export=export,
                    ingestion_contract=contract,
                    partition_key=no_data,
                    availability="no-data",
                    features=(),
                ),
            )
        ),
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
            "UPDATE open_roads_roadlink SET road_name = ?",
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
    key, partition = _available_partition(logical_key="roadlink:late-failure")
    original_insert_coverage_state = store._insert_coverage_state

    def fail_before_pointer(connection: Any, coverage: EvidenceCoverage) -> None:
        original_insert_coverage_state(connection, coverage)
        raise RuntimeError("forced failure before current pointer advancement")

    monkeypatch.setattr(store, "_insert_coverage_state", fail_before_pointer)

    with pytest.raises(RuntimeError, match="forced failure"):
        store.refresh(
            requested_partition_keys=(key,),
            partitions=(partition,),
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
    export = _open_roads_export()
    contract = _open_roads_contract()
    prior = store.refresh(
        requested_partition_keys=(available,),
        partitions=(
            EvidencePartitionInput(
                source_export=export,
                ingestion_contract=contract,
                partition_key=available,
                availability="available",
                features=(
                    EvidenceFeature(
                        logical_key="roadlink:100",
                        geometry=LineString([(350000, 165000), (351000, 165000)]),
                        attributes={"road_name": "A4", "road_number": "A4"},
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="no-data and explicit-unknown"):
        store.refresh(
            requested_partition_keys=(available, no_data),
            partitions=(
                EvidencePartitionInput(
                    source_export=export,
                    ingestion_contract=contract,
                    partition_key=available,
                    availability="available",
                    features=(
                        EvidenceFeature(
                            logical_key="roadlink:101",
                            geometry=LineString([(351000, 165000), (352000, 165000)]),
                            attributes={"road_name": "A4", "road_number": "A4"},
                        ),
                    ),
                ),
                EvidencePartitionInput(
                    source_export=export,
                    ingestion_contract=contract,
                    partition_key=no_data,
                    availability="no-data",
                    features=(
                        EvidenceFeature(
                            logical_key="roadlink:should-rollback",
                            geometry=LineString([(352000, 165000), (353000, 165000)]),
                            attributes={"road_name": "A4", "road_number": "A4"},
                        ),
                    ),
                ),
            ),
        )

    assert store.status(verify=True).current_coverage == prior.coverage
    connection = store._connect(read_only=True)
    try:
        leaked = connection.execute(
            "SELECT logical_key FROM open_roads_roadlink WHERE logical_key = ?",
            ["roadlink:101"],
        ).fetchone()
    finally:
        connection.close()
    assert leaked is None
