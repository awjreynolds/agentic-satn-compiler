"""Behavioural tests for the additive Local Evidence Store seam."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from shapely.geometry import LineString

import satn.local_evidence_store as local_evidence_store
from satn.evidence_contracts import EvidencePartitionKey, IngestionContract, SourceExport
from satn.local_evidence_store import (
    EvidenceFeature,
    EvidencePartitionInput,
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

    class WrongVersionDuckDB:
        __version__ = "1.4.3"

    monkeypatch.setattr(local_evidence_store, "_duckdb_module", lambda: WrongVersionDuckDB)

    with pytest.raises(SpatialRuntimeError, match=r"DuckDB 1\.4\.4 is required; found 1\.4\.3"):
        _store(tmp_path, runtime_lock_path=runtime_lock_path).status()

    assert not (tmp_path / "evidence.duckdb").exists()


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
        def execute(self, statement: str) -> Result | Connection:
            statements.append(statement)
            return Result() if "duckdb_extensions" in statement else self

        def close(self) -> None:
            return None

    class DuckDB:
        __version__ = "1.4.4"

        @staticmethod
        def connect(_database: str) -> Connection:
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

    assert (tmp_path / "evidence.duckdb").is_file()


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
    connection = store._connect()
    try:
        leaked = connection.execute(
            "SELECT logical_key FROM open_roads_roadlink WHERE logical_key = ?",
            ["roadlink:101"],
        ).fetchone()
    finally:
        connection.close()
    assert leaked is None
