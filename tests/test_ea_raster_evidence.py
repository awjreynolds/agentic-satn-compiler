"""Focused acceptance tests for the metadata-only EA raster catalogue."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, TiffImagePlugin
from shapely.geometry import LineString, Point

import satn.ea_raster_evidence as ea_raster
import satn.local_evidence_store as local_evidence_store
from satn.ea_elevation import (
    SAMPLE_LEDGER_SCHEMA_VERSION,
    evidence_row_sha256,
    read_sample_ledger,
    write_sample_ledger,
)
from satn.local_evidence_store import (
    EvidenceStoreSchemaError,
    LocalEvidenceStore,
    SpatialRuntimeLock,
    provision_spatial_runtime,
)

LOCAL_SPATIAL_RUNTIME_LOCK_SETTING = os.environ.get(
    "SATN_TEST_DUCKDB_SPATIAL_RUNTIME_LOCK"
)
LOCAL_SPATIAL_ARCHIVE_SETTING = os.environ.get("SATN_TEST_DUCKDB_SPATIAL_EXTENSION")
LOCAL_SPATIAL_RUNTIME_LOCK = Path(
    LOCAL_SPATIAL_RUNTIME_LOCK_SETTING
    or "__satn_test_duckdb_spatial_runtime_lock_not_configured__"
)
LOCAL_SPATIAL_ARCHIVE = Path(
    LOCAL_SPATIAL_ARCHIVE_SETTING
    or "__satn_test_duckdb_spatial_extension_not_configured__"
)
RUN_REAL_SPATIAL = pytest.mark.skipif(
    LOCAL_SPATIAL_RUNTIME_LOCK_SETTING is None
    or LOCAL_SPATIAL_ARCHIVE_SETTING is None
    or importlib.util.find_spec("duckdb") is None,
    reason=(
        "real DuckDB Spatial tests require explicit pinned "
        "SATN_TEST_DUCKDB_SPATIAL_RUNTIME_LOCK and "
        "SATN_TEST_DUCKDB_SPATIAL_EXTENSION artifacts"
    ),
)


def _real_store(tmp_path: Path) -> LocalEvidenceStore:
    if not LOCAL_SPATIAL_RUNTIME_LOCK.is_file():
        pytest.fail(
            "configured SATN_TEST_DUCKDB_SPATIAL_RUNTIME_LOCK is not a file: "
            f"{LOCAL_SPATIAL_RUNTIME_LOCK}"
        )
    if not LOCAL_SPATIAL_ARCHIVE.is_file():
        pytest.fail(
            "configured SATN_TEST_DUCKDB_SPATIAL_EXTENSION is not a file: "
            f"{LOCAL_SPATIAL_ARCHIVE}"
        )
    runtime_lock = SpatialRuntimeLock.from_json(LOCAL_SPATIAL_RUNTIME_LOCK)
    runtime_platform = local_evidence_store._runtime_platform()
    if runtime_lock.platform != runtime_platform:
        pytest.fail(
            "configured pinned DuckDB Spatial test runtime targets "
            f"{runtime_lock.platform}, not {runtime_platform}"
        )
    extension_cache = tmp_path / "extensions"
    provision_spatial_runtime(
        runtime_lock_path=LOCAL_SPATIAL_RUNTIME_LOCK,
        extension_archive=LOCAL_SPATIAL_ARCHIVE,
        extension_cache=extension_cache,
    )
    store = LocalEvidenceStore(
        store_path=tmp_path / "evidence.duckdb",
        runtime_lock_path=LOCAL_SPATIAL_RUNTIME_LOCK,
        extension_cache=extension_cache,
    )
    store.initialise()
    return store


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _write_tile(
    cache_dir: Path,
    key: tuple[int, int],
    *,
    value: float = 42.125,
    epsg: int = 27700,
    origin_offset_m: int = 0,
    pixel_size_m: float = 1.0,
    nodata_tag: str = ea_raster.EA_NODATA,
) -> tuple[Path, Path]:
    minimum_east = key[0] * ea_raster.EA_TILE_SIZE_M
    minimum_north = key[1] * ea_raster.EA_TILE_SIZE_M
    bounds = [
        minimum_east,
        minimum_north,
        minimum_east + ea_raster.EA_TILE_SIZE_M,
        minimum_north + ea_raster.EA_TILE_SIZE_M,
    ]
    transform = (
        pixel_size_m,
        0.0,
        0.0,
        float(minimum_east + origin_offset_m),
        0.0,
        -pixel_size_m,
        0.0,
        float(minimum_north + ea_raster.EA_TILE_SIZE_M),
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    )
    image = Image.new(
        "F",
        (ea_raster.EA_TILE_SIZE_M, ea_raster.EA_TILE_SIZE_M),
        value,
    )
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[34264] = transform
    tags[34735] = (1, 1, 0, 1, 3072, 0, 1, epsg)
    tags[42113] = nodata_tag
    temporary = cache_dir / "temporary.tif"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    image.save(temporary, format="TIFF", compression="tiff_lzw", tiffinfo=tags)
    raw = temporary.read_bytes()
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    object_path = cache_dir / "objects" / "sha256" / f"{raw_sha256}.tif"
    object_path.parent.mkdir(parents=True, exist_ok=True)
    temporary.replace(object_path)

    request_payload: dict[str, object] = {
        "contract": ea_raster.EA_TILE_RECEIPT_CONTRACT,
        "source_id": ea_raster.EA_SOURCE_ID,
        "dataset_id": ea_raster.DTM_DATASET_ID,
        "dataset_title": ea_raster.DTM_TITLE,
        "coverage_id": ea_raster.DTM_COVERAGE_ID,
        "endpoint": ea_raster.DTM_ENDPOINT,
        "licence": ea_raster.DTM_LICENCE,
        "attribution": ea_raster.DTM_ATTRIBUTION,
        "acquisition_contract_version": ea_raster.CONTRACT_SCHEMA_VERSION,
        "publisher_release": None,
        "effective_date": None,
        "dataset_declared_survey_period": {
            "start": ea_raster.DATASET_DECLARED_SURVEY_START,
            "end": ea_raster.DATASET_DECLARED_SURVEY_END,
        },
        "request": {
            "service": "WCS",
            "version": ea_raster.EA_WCS_VERSION,
            "operation": "GetCoverage",
            "format": "image/tiff",
            "crs": "EPSG:27700",
            "tile_key": list(key),
            "bounds_m": bounds,
            "tile_size_m": ea_raster.EA_TILE_SIZE_M,
            "output_spacing_mm": ea_raster.EA_RESOLUTION_MM,
            "scale_factor": "1.00000000",
        },
        "vertical_reference": ea_raster.EA_VERTICAL_REFERENCE,
        "transformation": ea_raster.EA_TRANSFORMATION,
        "source_resolution_m": ea_raster.EA_RESOLUTION_M,
        "vertical_accuracy": ea_raster.DTM_VERTICAL_ACCURACY,
        "nodata_policy": ea_raster.EA_NODATA_POLICY,
    }
    request_fingerprint = hashlib.sha256(_canonical_bytes(request_payload)).hexdigest()
    receipt = {
        **request_payload,
        "request_fingerprint": request_fingerprint,
        "raw_sha256": raw_sha256,
        "byte_count": len(raw),
        "observed_raster_metadata": {
            "crs": "EPSG:27700",
            "dimensions": [ea_raster.EA_TILE_SIZE_M, ea_raster.EA_TILE_SIZE_M],
            "model_transformation": list(transform),
            "nodata": ea_raster.EA_NODATA,
            "nodata_observed": nodata_tag,
        },
    }
    receipt_path = cache_dir / "receipts" / f"{request_fingerprint}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_bytes(_canonical_bytes(receipt))
    return receipt_path, object_path


@RUN_REAL_SPATIAL
def test_offline_sampling_matches_retained_ledger_at_northing_boundaries(
    tmp_path: Path,
) -> None:
    store = _real_store(tmp_path)
    cache = tmp_path / "cache"
    _write_tile(cache, (70, 31), value=10.0)
    north_receipt_path, _north_object = _write_tile(cache, (70, 32), value=20.0)
    _write_tile(cache, (0, 0), value=7.0)
    north_receipt = ea_raster._read_receipt(north_receipt_path)
    store.refresh_ea_elevation_cache(
        cache_dir=cache,
        requested_bng_10km_cells=("ST55", "ST56", "SV00"),
    )

    retained_ledger_path = tmp_path / "retained-sample-ledger.jsonl"
    write_sample_ledger(
        retained_ledger_path,
        [
            {
                "schema_version": SAMPLE_LEDGER_SCHEMA_VERSION,
                "route_id": "retained-boundary-route",
                "sample_index": index,
                "east_mm": 350_100_000 + index * 1_000,
                "north_mm": 160_000_000,
                "authority_id": "fixture-authority",
                "bucket": "authority",
                "availability": "available",
                "elevation_m": 20.0,
                "survey_feature_id": "fixture-survey",
                "ed_flown": "2022-04-02",
                "resolution_m": 1,
                "evidence_row_sha256": evidence_row_sha256(
                    route_id="retained-boundary-route",
                    sample_index=index,
                    east_mm=350_100_000 + index * 1_000,
                    north_mm=160_000_000,
                    elevation_m=20.0,
                ),
                "route_position": index,
                "previous_sample_index": None if index == 0 else index - 1,
                "next_sample_index": None if index == 2 else index + 1,
                "tile_request_fingerprint": north_receipt.request_fingerprint,
                "tile_raw_sha256": north_receipt.raw_sha256,
                "tile_pixel_status": "validated-value",
            }
            for index in range(3)
        ],
    )
    retained_ledger = read_sample_ledger(retained_ledger_path)

    retained = store.sample_elevation(
        cache_dir=cache,
        geometry=LineString([(350_100, 160_000), (350_102, 160_000)]),
        spacing_mm=1_000,
    )
    retained_payload = retained.canonical_payload()
    assert retained_payload["vertical_reference"] == {
        "value": "ODN",
        "provenance_kind": "governed-dataset-contract-declaration",
        "observed_in_geotiff": False,
    }
    assert retained_payload["transformation"] == {
        "value": "OSTN15",
        "provenance_kind": "governed-dataset-contract-declaration",
        "observed_in_geotiff": False,
    }
    assert [
        {
            "sample_index": item.sample_index,
            "east_mm": item.east_mm,
            "north_mm": item.north_mm,
            "availability": item.availability,
            "elevation_m": (
                None if item.elevation_mm is None else item.elevation_mm / 1_000
            ),
        }
        for item in retained.observations
    ] == [
        {
            key: row[key]
            for key in (
                "sample_index",
                "east_mm",
                "north_mm",
                "availability",
                "elevation_m",
            )
        }
        for row in retained_ledger
    ]

    unseen = store.sample_elevation(
        cache_dir=cache,
        geometry=LineString([(350_100, 159_999), (350_102, 159_999)]),
        spacing_mm=1_000,
    )
    assert [item.elevation_mm for item in unseen.observations] == [10_000, 10_000, 10_000]
    origin = store.sample_elevation(
        cache_dir=cache,
        geometry=Point(100, 0),
    )
    assert origin.observations[0].elevation_mm == 7_000


@RUN_REAL_SPATIAL
def test_northing_boundary_nodata_comes_from_the_governed_north_tile(
    tmp_path: Path,
) -> None:
    store = _real_store(tmp_path)
    cache = tmp_path / "cache"
    _write_tile(cache, (70, 31), value=10.0)
    _write_tile(cache, (70, 32), value=float(np.finfo(np.float32).min))
    store.refresh_ea_elevation_cache(
        cache_dir=cache,
        requested_bng_10km_cells=("ST55", "ST56"),
    )

    result = store.sample_elevation(
        cache_dir=cache,
        geometry=Point(350_100, 160_000),
    )

    assert result.observations[0].availability == "no-data"
    assert result.observations[0].elevation_mm is None
    assert result.observations[0].tile_receipt_fingerprint is not None


@RUN_REAL_SPATIAL
def test_exact_repeat_reuses_attested_tile_without_decode_or_unrelated_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _real_store(tmp_path)
    cache = tmp_path / "cache"
    _write_tile(cache, (70, 32), value=42.0)
    metadata_checks: list[bool] = []
    original = ea_raster._observed_raster_metadata

    def tracked_metadata(
        path: Path,
        receipt: ea_raster.EATileReceipt,
        *,
        decode: bool,
    ) -> dict[str, object]:
        metadata_checks.append(decode)
        return original(path, receipt, decode=decode)

    monkeypatch.setattr(ea_raster, "_observed_raster_metadata", tracked_metadata)
    first = store.refresh_ea_elevation_cache(
        cache_dir=cache,
        requested_bng_10km_cells=("ST56",),
    )
    assert metadata_checks == [True]
    metadata_checks.clear()
    (cache / "receipts" / "unrelated-corrupt.json").write_text(
        "{not-json",
        encoding="utf-8",
    )

    repeated = store.refresh_ea_elevation_cache(
        cache_dir=cache,
        requested_bng_10km_cells=("ST56",),
    )

    assert repeated.fingerprint == first.fingerprint
    assert metadata_checks == [False]


@RUN_REAL_SPATIAL
def test_offline_sampling_is_incremental_direction_equivalent_and_metadata_only(
    tmp_path: Path,
) -> None:
    store = _real_store(tmp_path)
    cache = tmp_path / "cache"
    receipt_path, _object_path = _write_tile(cache, (70, 32))
    _write_tile(cache, (70, 33), value=44.0)
    receipt = ea_raster._read_receipt(receipt_path)
    with pytest.raises(ValueError, match="outside its attested BNG"):
        ea_raster.EABng10kmRasterAttestation(
            bng_10km_cell="SP50",
            completeness="partial",
            tile_receipts=(receipt,),
        )

    first = store.refresh_ea_elevation_cache(
        cache_dir=cache,
        requested_bng_10km_cells=("ST56", "SP50"),
    )
    assert [(item.bng_10km_cell, item.completeness) for item in first.attestations] == [
        ("SP50", "partial"),
        ("ST56", "partial"),
    ]

    forward = store.sample_elevation(
        cache_dir=cache,
        geometry=LineString([(350_100, 160_100), (350_103, 160_100)]),
        spacing_mm=1_000,
    )
    reverse = store.sample_elevation(
        cache_dir=cache,
        geometry=LineString([(350_103, 160_100), (350_100, 160_100)]),
        spacing_mm=1_000,
    )
    assert forward.fingerprint == reverse.fingerprint
    assert {item.availability for item in forward.observations} == {"available"}
    assert {item.elevation_mm for item in forward.observations} == {42_125}
    north_boundary = store.sample_elevation(
        cache_dir=cache,
        geometry=Point(350_100, 165_000),
    )
    assert north_boundary.observations[0].availability == "available"
    assert north_boundary.observations[0].elevation_mm == 44_000

    disconnected = store.sample_elevation(
        cache_dir=cache,
        geometry=Point(450_100, 200_100),
    )
    assert [item.availability for item in disconnected.observations] == ["explicit-unknown"]
    with pytest.raises(ValueError, match="does not cover sampling BNG cells"):
        store.sample_elevation(
            cache_dir=cache,
            geometry=Point(550_100, 250_100),
        )

    _write_tile(cache, (71, 32), value=43.5)
    second = store.refresh_ea_elevation_cache(
        cache_dir=cache,
        requested_bng_10km_cells=("ST56",),
    )
    assert second.fingerprint != first.fingerprint
    new_tile = store.sample_elevation(
        cache_dir=cache,
        geometry=Point(355_000, 160_100),
    )
    assert new_tile.observations[0].elevation_mm == 43_500
    historical = store.sample_elevation(
        cache_dir=cache,
        geometry=Point(355_100, 160_100),
        state_fingerprint=first.fingerprint,
    )
    assert historical.observations[0].availability == "explicit-unknown"

    connection = store._connect(read_only=True)
    try:
        receipt_columns = connection.execute(
            """
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_name = 'ea_raster_receipt_registry'
            ORDER BY ordinal_position
            """
        ).fetchall()
        payloads = connection.execute(
            "SELECT canonical_payload_json FROM ea_raster_receipt_registry"
        ).fetchall()
    finally:
        connection.close()
    assert not any("BLOB" in str(data_type).upper() for _, data_type in receipt_columns)
    assert all("retained_path" not in str(payload) for payload in payloads)
    assert all(len(str(payload)) < 4_000 for payload in payloads)


@RUN_REAL_SPATIAL
def test_nodata_is_distinct_from_missing_and_tamper_fails_closed(tmp_path: Path) -> None:
    store = _real_store(tmp_path)
    cache = tmp_path / "cache"
    _receipt, object_path = _write_tile(
        cache,
        (70, 32),
        value=float(np.finfo(np.float32).min),
    )
    store.refresh_ea_elevation_cache(
        cache_dir=cache,
        requested_bng_10km_cells=("ST56",),
    )

    nodata = store.sample_elevation(
        cache_dir=cache,
        geometry=Point(350_100, 160_100),
    )
    missing = store.sample_elevation(
        cache_dir=cache,
        geometry=Point(355_100, 160_100),
    )
    assert nodata.observations[0].availability == "no-data"
    assert nodata.observations[0].tile_receipt_fingerprint is not None
    assert missing.observations[0].availability == "explicit-unknown"
    assert missing.observations[0].tile_receipt_fingerprint is None

    object_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match=r"byte count|digest"):
        store.verify_ea_elevation_cache(cache_dir=cache)
    with pytest.raises(ValueError, match=r"byte count|digest"):
        store.sample_elevation(
            cache_dir=cache,
            geometry=Point(350_100, 160_100),
        )


@RUN_REAL_SPATIAL
def test_elevation_schema_rejects_removed_singleton_constraints(tmp_path: Path) -> None:
    store = _real_store(tmp_path)
    connection = store._connect(read_only=False)
    try:
        connection.execute("DROP TABLE current_ea_raster_coverage_state")
        connection.execute(
            """
            CREATE TABLE current_ea_raster_coverage_state (
                singleton BOOLEAN NOT NULL,
                coverage_fingerprint VARCHAR NOT NULL
            )
            """
        )
    finally:
        connection.close()

    with pytest.raises(EvidenceStoreSchemaError, match="constraint"):
        store.status(verify=True)


@RUN_REAL_SPATIAL
def test_wrong_raster_metadata_and_noncanonical_receipts_are_rejected(
    tmp_path: Path,
) -> None:
    store = _real_store(tmp_path)
    wrong_cache = tmp_path / "wrong"
    _write_tile(wrong_cache, (70, 32), epsg=4326)
    with pytest.raises(ValueError, match="CRS"):
        store.refresh_ea_elevation_cache(
            cache_dir=wrong_cache,
            requested_bng_10km_cells=("ST56",),
        )

    transform_cache = tmp_path / "transform"
    _write_tile(transform_cache, (70, 32), pixel_size_m=2.0)
    with pytest.raises(ValueError, match="transform"):
        store.refresh_ea_elevation_cache(
            cache_dir=transform_cache,
            requested_bng_10km_cells=("ST56",),
        )

    nodata_cache = tmp_path / "nodata"
    _write_tile(nodata_cache, (70, 32), nodata_tag="-9999")
    with pytest.raises(ValueError, match="NoData"):
        store.refresh_ea_elevation_cache(
            cache_dir=nodata_cache,
            requested_bng_10km_cells=("ST56",),
        )

    datum_cache = tmp_path / "datum"
    datum_receipt, _object = _write_tile(datum_cache, (70, 32))
    datum_payload = json.loads(datum_receipt.read_bytes())
    datum_payload["vertical_reference"] = "unknown"
    datum_receipt.write_bytes(_canonical_bytes(datum_payload))
    with pytest.raises(ValueError, match="provenance"):
        store.refresh_ea_elevation_cache(
            cache_dir=datum_cache,
            requested_bng_10km_cells=("ST56",),
        )

    extra_cache = tmp_path / "extra"
    receipt_path, _object = _write_tile(extra_cache, (70, 32))
    payload = json.loads(receipt_path.read_bytes())
    payload["unrecognised"] = True
    receipt_path.write_bytes(_canonical_bytes(payload))
    with pytest.raises(ValueError, match="v1 schema"):
        store.refresh_ea_elevation_cache(
            cache_dir=extra_cache,
            requested_bng_10km_cells=("ST56",),
        )


@RUN_REAL_SPATIAL
def test_sampling_releases_each_decoded_tile_before_opening_the_next(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _real_store(tmp_path)
    cache = tmp_path / "cache"
    _write_tile(cache, (70, 32), value=10)
    _write_tile(cache, (71, 32), value=20)
    store.refresh_ea_elevation_cache(
        cache_dir=cache,
        requested_bng_10km_cells=("ST56",),
    )

    active = 0
    maximum = 0

    class TrackedPixels:
        shape = (ea_raster.EA_TILE_SIZE_M, ea_raster.EA_TILE_SIZE_M)

        def __init__(self, value: float) -> None:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            self.value = value

        def __getitem__(self, _position: object) -> float:
            return self.value

        def __del__(self) -> None:
            nonlocal active
            active -= 1

    original = ea_raster._load_verified_tile

    def tracked(path: Path, receipt: ea_raster.EATileReceipt) -> tuple[object, tuple[float, ...]]:
        original(path, receipt)
        return TrackedPixels(float(receipt.tile_key[0])), ea_raster._expected_transform(
            receipt.bounds_m
        )

    monkeypatch.setattr(ea_raster, "_load_verified_tile", tracked)
    result = store.sample_elevation(
        cache_dir=cache,
        geometry=LineString([(354_999, 160_100), (355_001, 160_100)]),
        spacing_mm=1_000,
    )
    assert len(result.tile_receipt_fingerprints) == 2
    assert maximum == 1
    assert active == 0
