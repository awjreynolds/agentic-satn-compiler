from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import geopandas as gpd
import pytest
from PIL import Image, TiffImagePlugin
from shapely.geometry import LineString, Point

import satn.ea_raster_evidence as ea_raster
import satn.evidence_replay as replay_module
from satn import open_roads_adapter
from satn.ea_raster_evidence import ElevationObservation, ElevationSamplingResult
from satn.evidence_contracts import (
    EvidencePartitionKey,
    IngestionContract,
    ScenarioConfiguration,
    SourceExport,
    evidence_fingerprint,
    evidence_geometry_fingerprint,
)
from satn.evidence_replay import (
    EvidenceReplayProbe,
    EvidenceReplayRequest,
    VectorEvidenceBinding,
    run_source_query_replay,
)
from satn.local_evidence_store import (
    EvidenceQueryResult,
    LocalEvidenceStore,
    provision_spatial_runtime,
)

PROJECT = Path(__file__).parents[1]
LOCAL_SPATIAL_ARCHIVE = Path(
    "/private/tmp/banes-satn-embedded-store-benchmark/duckdb_extensions/"
    "v1.4.4/osx_arm64/spatial.duckdb_extension"
)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
RUN_REPLAY = pytest.mark.skipif(
    not LOCAL_SPATIAL_ARCHIVE.is_file() or importlib.util.find_spec("duckdb") is None,
    reason="pinned local Spatial archive or DuckDB package absent",
)
EXECUTION_SENSITIVE_DISTRIBUTIONS = (
    "duckdb",
    "geopandas",
    "numpy",
    "pandas",
    "Pillow",
    "pyogrio",
    "pyproj",
    "shapely",
)


def _scenario(seed: str = SHA_A) -> ScenarioConfiguration:
    return ScenarioConfiguration(
        area_definition_fingerprint=seed,
        criteria_set_fingerprint=SHA_B,
        network_selection_profile_fingerprint=SHA_C,
    )


def _clean_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        replay_module,
        "_git_identity",
        lambda: {"sha": SHA_A, "available": True, "dirty": False},
    )


def _runtime_paths(tmp_path: Path) -> tuple[Path, Path]:
    runtime_lock_path = PROJECT / "config" / "duckdb-spatial-runtime-lock.json"
    extension_cache = tmp_path / "extensions"
    provision_spatial_runtime(
        runtime_lock_path=runtime_lock_path,
        extension_archive=LOCAL_SPATIAL_ARCHIVE,
        extension_cache=extension_cache,
    )
    return runtime_lock_path, extension_cache


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()


def _register_ea_tile(
    tmp_path: Path,
    *,
    point: Point,
    elevation_m: float = 42.125,
) -> tuple[str, str, str]:
    cache_dir = tmp_path / "ea-cache"
    tile_key = (
        int(point.x) // ea_raster.EA_TILE_SIZE_M,
        int(point.y) // ea_raster.EA_TILE_SIZE_M,
    )
    minimum_east = tile_key[0] * ea_raster.EA_TILE_SIZE_M
    minimum_north = tile_key[1] * ea_raster.EA_TILE_SIZE_M
    bounds = [
        minimum_east,
        minimum_north,
        minimum_east + ea_raster.EA_TILE_SIZE_M,
        minimum_north + ea_raster.EA_TILE_SIZE_M,
    ]
    transform = (
        1.0,
        0.0,
        0.0,
        float(minimum_east),
        0.0,
        -1.0,
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
        elevation_m,
    )
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[34264] = transform
    tags[34735] = (1, 1, 0, 1, 3072, 0, 1, ea_raster.EA_GEOTIFF_EPSG)
    tags[42113] = ea_raster.EA_NODATA
    temporary = cache_dir / "temporary.tif"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    image.save(temporary, format="TIFF", compression="tiff_lzw", tiffinfo=tags)
    raw = temporary.read_bytes()
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    object_path = cache_dir / "objects" / "sha256" / f"{raw_sha256}.tif"
    object_path.parent.mkdir(parents=True, exist_ok=True)
    temporary.replace(object_path)

    receipt_payload: dict[str, object] = {
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
            "tile_key": list(tile_key),
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
    request_fingerprint = hashlib.sha256(_canonical_bytes(receipt_payload)).hexdigest()
    receipt = {
        **receipt_payload,
        "request_fingerprint": request_fingerprint,
        "raw_sha256": raw_sha256,
        "byte_count": len(raw),
        "observed_raster_metadata": {
            "crs": "EPSG:27700",
            "dimensions": [
                ea_raster.EA_TILE_SIZE_M,
                ea_raster.EA_TILE_SIZE_M,
            ],
            "model_transformation": list(transform),
            "nodata": ea_raster.EA_NODATA,
            "nodata_observed": ea_raster.EA_NODATA,
        },
    }
    receipt_path = cache_dir / "receipts" / f"{request_fingerprint}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_bytes(_canonical_bytes(receipt))

    runtime_lock_path, extension_cache = _runtime_paths(tmp_path)
    store = LocalEvidenceStore(
        store_path=tmp_path / "evidence.duckdb",
        runtime_lock_path=runtime_lock_path,
        extension_cache=extension_cache,
    )
    store.initialise()
    coverage = store.refresh_ea_elevation_cache(
        cache_dir=cache_dir,
        requested_bng_10km_cells=("ST56",),
    )
    attestation = coverage.attestations[0]
    return (
        coverage.fingerprint,
        attestation.fingerprint,
        attestation.tile_receipts[0].fingerprint,
    )


def _write_open_roads(
    path: Path,
    *,
    road_id: str,
    geometry: LineString,
    classification: str,
) -> Path:
    frame = gpd.GeoDataFrame(
        {
            "id": [road_id],
            "road_classification": [classification],
            "road_function": ["A Road"],
            "road_classification_number": [classification],
            "name_1": [f"{classification} road"],
        },
        geometry=[geometry],
        crs="EPSG:27700",
    )
    frame.to_file(path, layer="RoadLink", driver="GPKG", index=False)
    return path


def _binding(
    path: Path,
    *,
    cell: str,
    release: str,
) -> VectorEvidenceBinding:
    source = SourceExport(
        source_family="os-open-roads",
        dataset="open-roads",
        layer="RoadLink",
        publisher_release=release,
        effective_date=f"{release}-01",
        licence="OGL-3.0",
        format="GeoPackage",
        declared_crs="EPSG:27700",
        raw_bytes_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        provenance={"retained_path": str(path.resolve())},
    )
    payload = open_roads_adapter.contract_payload("EPSG:27700")
    payload.pop("contract")
    contract = IngestionContract(**payload)
    return VectorEvidenceBinding(
        source_export=source,
        ingestion_contract=contract,
        requested_partition_keys=(
            EvidencePartitionKey(
                source_layer=contract.source_layer,
                partition_scheme=contract.partition_scheme,
                cell=cell,
            ),
        ),
    )


def _vector_probe(
    probe_id: str,
    point: Point,
) -> EvidenceReplayProbe:
    return EvidenceReplayProbe(
        probe_id=probe_id,
        kind="vector",
        source_layer="os-open-roads/RoadLink",
        selector=point,
        projection=("road_classification",),
    )


def _request(
    tmp_path: Path,
    *,
    bindings: tuple[VectorEvidenceBinding, ...],
    probes: tuple[EvidenceReplayProbe, ...],
    scenario: ScenarioConfiguration | None = None,
    elevation_state_fingerprint: str | None = None,
) -> EvidenceReplayRequest:
    runtime_lock_path, extension_cache = _runtime_paths(tmp_path)
    return EvidenceReplayRequest(
        scenario_configuration=scenario or _scenario(),
        vector_bindings=bindings,
        probes=probes,
        store_path=tmp_path / "evidence.duckdb",
        runtime_lock_path=runtime_lock_path,
        extension_cache=extension_cache,
        cache_path=tmp_path / "source-query-cache",
        run_manifest_path=tmp_path / "source-query-run.json",
        ea_cache_dir=(
            tmp_path / "ea-cache" if any(item.kind == "elevation" for item in probes) else None
        ),
        elevation_state_fingerprint=elevation_state_fingerprint,
    )


def _banes_binding(tmp_path: Path) -> VectorEvidenceBinding:
    return _binding(
        _write_open_roads(
            tmp_path / "banes.gpkg",
            road_id="banes-a4",
            geometry=LineString([(355_000, 165_000), (355_900, 165_000)]),
            classification="A4",
        ),
        cell="ST56",
        release="2026-04",
    )


def _oxfordshire_binding(
    tmp_path: Path,
    *,
    release: str = "2026-05",
    name: str = "oxfordshire",
) -> VectorEvidenceBinding:
    return _binding(
        _write_open_roads(
            tmp_path / f"{name}.gpkg",
            road_id=f"{name}-a40",
            geometry=LineString([(455_000, 205_000), (455_900, 205_000)]),
            classification="A40",
        ),
        cell="SP50",
        release=release,
    )


def test_probe_cannot_accept_a_caller_fabricated_oracle() -> None:
    with pytest.raises(TypeError, match="direct_source_oracle"):
        EvidenceReplayProbe(
            probe_id="fabricated",
            kind="vector",
            source_layer="os-open-roads/RoadLink",
            selector=Point(355_100, 165_000),
            projection=("road_classification",),
            direct_source_oracle={  # type: ignore[call-arg]
                "result_fingerprint": SHA_A
            },
        )


@RUN_REPLAY
def test_real_store_query_matches_independent_governed_byte_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_commit(monkeypatch)
    binding = _banes_binding(tmp_path)
    request = _request(
        tmp_path,
        bindings=(binding,),
        probes=(_vector_probe("banes", Point(355_100, 165_000)),),
    )

    first = run_source_query_replay(request)
    second = run_source_query_replay(request)

    assert first.accepted
    assert first.manifest["scope"] == "source-query-replay-gate"
    assert first.manifest["work"] == {
        "vector_refresh_calls": 1,
        "vector_refreshed_partitions": 1,
        "vector_validation_source_reads": 1,
        "vector_validation_source_bytes": Path(
            binding.source_export.provenance["retained_path"]
        ).stat().st_size,
        "vector_store_queries": 1,
        "source_adapter_partition_reads": 1,
        "ea_oracle_samples": 0,
        "ea_replay_store_samples": 0,
        "ea_oracle_receipt_reads": 0,
        "ea_oracle_object_reads": 0,
        "ea_replay_receipt_reads": 0,
        "ea_replay_object_reads": 0,
        "ea_cache_validation_receipt_reads": 0,
        "ea_cache_validation_object_reads": 0,
        "derived_hits": 0,
        "derived_misses": 1,
    }
    assert second.manifest["work"]["derived_hits"] == 1  # type: ignore[index]
    assert second.manifest["work"]["vector_validation_source_reads"] == 1  # type: ignore[index]
    assert second.manifest["work"]["vector_validation_source_bytes"] == (  # type: ignore[index]
        Path(binding.source_export.provenance["retained_path"]).stat().st_size
    )
    assert second.manifest["work"]["vector_store_queries"] == 0  # type: ignore[index]
    assert second.manifest["work"]["source_adapter_partition_reads"] == 0  # type: ignore[index]
    assert second.manifest["cache"]["reused_generation"] is True  # type: ignore[index]
    assert len(tuple(replay_module._generation_directory(request.cache_path).glob("*.json"))) == 1
    dependency = second.manifest["probes"][0]["dependency"]  # type: ignore[index]
    assert dependency["required_partition_key_fingerprints"] == [
        binding.requested_partition_keys[0].fingerprint
    ]
    assert dependency["source_export_fingerprints"] == [binding.source_export.fingerprint]
    assert "compiler-equivalence" in second.manifest["claims_excluded"]  # type: ignore[operator]
    assert "publication-equivalence" in second.manifest["claims_excluded"]  # type: ignore[operator]


@RUN_REPLAY
def test_exact_vector_hit_validates_each_live_source_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_commit(monkeypatch)
    banes = _banes_binding(tmp_path)
    oxfordshire = _oxfordshire_binding(tmp_path)
    request = _request(
        tmp_path,
        bindings=(banes, oxfordshire),
        probes=(
            _vector_probe("banes", Point(355_100, 165_000)),
            _vector_probe("oxfordshire", Point(455_100, 205_000)),
        ),
    )
    run_source_query_replay(request)

    import satn.local_evidence_store as store_module

    retained_paths = {
        Path(binding.source_export.provenance["retained_path"]).resolve()
        for binding in (banes, oxfordshire)
    }
    expected_bytes = sum(path.stat().st_size for path in retained_paths)
    actual_reads: list[Path] = []
    original_sha256_file = store_module._sha256_file

    def observed_sha256_file(path: Path) -> str:
        resolved = path.resolve()
        if resolved in retained_paths:
            actual_reads.append(resolved)
        return original_sha256_file(path)

    monkeypatch.setattr(store_module, "_sha256_file", observed_sha256_file)

    second = run_source_query_replay(request)

    assert sorted(actual_reads) == sorted(retained_paths)
    assert second.manifest["work"]["vector_validation_source_reads"] == 2  # type: ignore[index]
    assert second.manifest["work"]["vector_validation_source_bytes"] == expected_bytes  # type: ignore[index]
    assert second.manifest["work"]["derived_hits"] == 2  # type: ignore[index]
    assert second.manifest["work"]["vector_store_queries"] == 0  # type: ignore[index]
    assert second.manifest["work"]["source_adapter_partition_reads"] == 0  # type: ignore[index]


@RUN_REPLAY
def test_exact_vector_hit_fails_closed_when_retained_source_bytes_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_commit(monkeypatch)
    binding = _banes_binding(tmp_path)
    request = _request(
        tmp_path,
        bindings=(binding,),
        probes=(_vector_probe("banes", Point(355_100, 165_000)),),
    )
    run_source_query_replay(request)
    committed = request.run_manifest_path.read_bytes()
    retained_path = Path(binding.source_export.provenance["retained_path"])
    retained_path.write_bytes(retained_path.read_bytes() + b"tampered")

    with pytest.raises(RuntimeError, match="retained Source Export bytes"):
        run_source_query_replay(request)

    assert request.run_manifest_path.read_bytes() == committed


@RUN_REPLAY
@pytest.mark.parametrize("distribution", EXECUTION_SENSITIVE_DISTRIBUTIONS)
def test_any_execution_sensitive_runtime_version_change_invalidates_a_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    distribution: str,
) -> None:
    _clean_commit(monkeypatch)
    binding = _banes_binding(tmp_path)
    request = _request(
        tmp_path,
        bindings=(binding,),
        probes=(_vector_probe("banes", Point(355_100, 165_000)),),
    )
    first = run_source_query_replay(request)
    original_version = replay_module.importlib.metadata.version

    def changed_version(name: str) -> str:
        version = original_version(name)
        return f"{version}+replay-drift" if name == distribution else version

    monkeypatch.setattr(replay_module.importlib.metadata, "version", changed_version)

    second = run_source_query_replay(request)

    assert set(first.manifest["runtime"]["distributions"]) >= set(  # type: ignore[index]
        EXECUTION_SENSITIVE_DISTRIBUTIONS
    )
    assert second.manifest["work"]["derived_hits"] == 0  # type: ignore[index]
    assert second.manifest["work"]["derived_misses"] == 1  # type: ignore[index]
    assert second.manifest["work"]["source_adapter_partition_reads"] == 1  # type: ignore[index]
    assert second.manifest["work"]["vector_store_queries"] == 1  # type: ignore[index]


@RUN_REPLAY
def test_fabricated_store_answer_cannot_match_an_arbitrary_caller_oracle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_commit(monkeypatch)
    binding = _banes_binding(tmp_path)
    request = _request(
        tmp_path,
        bindings=(binding,),
        probes=(_vector_probe("banes", Point(355_100, 165_000)),),
    )
    original_query = LocalEvidenceStore.query

    def fabricated_query(
        self: LocalEvidenceStore,
        **kwargs: object,
    ) -> EvidenceQueryResult:
        actual = original_query(self, **kwargs)  # type: ignore[arg-type]
        manifest = dict(actual.manifest)
        manifest["row_count"] = 0
        manifest["row_fingerprints"] = []
        return EvidenceQueryResult(rows=(), manifest=manifest)

    monkeypatch.setattr(LocalEvidenceStore, "query", fabricated_query)

    with pytest.raises(ValueError, match="governed byte-adapter observation"):
        run_source_query_replay(request)

    assert not request.run_manifest_path.exists()


@RUN_REPLAY
def test_unrelated_disconnected_binding_does_not_invalidate_a_query_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_commit(monkeypatch)
    banes = _banes_binding(tmp_path)
    probe = _vector_probe("banes", Point(355_100, 165_000))
    first_request = _request(tmp_path, bindings=(banes,), probes=(probe,))
    run_source_query_replay(first_request)
    oxfordshire = _oxfordshire_binding(tmp_path)

    second = run_source_query_replay(
        _request(
            tmp_path,
            bindings=(banes, oxfordshire),
            probes=(probe,),
        )
    )

    assert second.manifest["work"]["derived_hits"] == 1  # type: ignore[index]
    assert second.manifest["work"]["vector_refresh_calls"] == 0  # type: ignore[index]
    dependency = second.manifest["probes"][0]["dependency"]  # type: ignore[index]
    assert oxfordshire.source_export.fingerprint not in dependency["source_export_fingerprints"]


@RUN_REPLAY
def test_disconnected_cells_can_use_different_valid_source_releases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_commit(monkeypatch)
    banes = _banes_binding(tmp_path)
    oxfordshire = _oxfordshire_binding(tmp_path)

    result = run_source_query_replay(
        _request(
            tmp_path,
            bindings=(banes, oxfordshire),
            probes=(
                _vector_probe("banes", Point(355_100, 165_000)),
                _vector_probe("oxfordshire", Point(455_100, 205_000)),
            ),
        )
    )

    by_id = {item["probe_id"]: item for item in result.manifest["probes"]}  # type: ignore[union-attr]
    assert by_id["banes"]["dependency"]["source_export_fingerprints"] == [
        banes.source_export.fingerprint
    ]
    assert by_id["oxfordshire"]["dependency"]["source_export_fingerprints"] == [
        oxfordshire.source_export.fingerprint
    ]


@RUN_REPLAY
def test_unrelated_release_mismatch_is_ignored_until_its_cell_is_queried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_commit(monkeypatch)
    banes = _banes_binding(tmp_path)
    oxfordshire = _oxfordshire_binding(tmp_path)
    run_source_query_replay(
        _request(
            tmp_path,
            bindings=(banes, oxfordshire),
            probes=(
                _vector_probe("banes", Point(355_100, 165_000)),
                _vector_probe("oxfordshire", Point(455_100, 205_000)),
            ),
        )
    )
    replacement = _oxfordshire_binding(
        tmp_path,
        release="2026-06",
        name="oxfordshire-replacement",
    )

    retained = run_source_query_replay(
        _request(
            tmp_path,
            bindings=(banes, replacement),
            probes=(_vector_probe("banes", Point(355_100, 165_000)),),
        )
    )

    assert retained.manifest["work"]["derived_hits"] == 1  # type: ignore[index]
    with pytest.raises(ValueError, match="different governed inputs"):
        run_source_query_replay(
            _request(
                tmp_path,
                bindings=(banes, replacement),
                probes=(
                    _vector_probe(
                        "oxfordshire",
                        Point(455_100, 205_000),
                    ),
                ),
            )
        )


def _elevation_result(
    geometry: Point,
    *,
    state_fingerprint: str,
    attestation: str,
    receipt: str,
    elevation_mm: int,
) -> ElevationSamplingResult:
    geometry_fingerprint = evidence_geometry_fingerprint(geometry, "EPSG:27700")
    return ElevationSamplingResult(
        coverage_state_fingerprint=state_fingerprint,
        geometry_fingerprint=geometry_fingerprint,
        spacing_mm=10_000,
        consulted_attestation_fingerprints=(attestation,),
        tile_receipt_fingerprints=(receipt,),
        observations=(
            ElevationObservation(
                sample_index=0,
                distance_mm=0,
                east_mm=round(geometry.x * 1000),
                north_mm=round(geometry.y * 1000),
                availability="available",
                elevation_mm=elevation_mm,
                tile_receipt_fingerprint=receipt,
            ),
        ),
    )


@RUN_REPLAY
def test_elevation_cache_key_uses_only_each_probe_receipt_read_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_commit(monkeypatch)
    point = Point(355_100, 165_100)
    state, attestation, receipt = _register_ea_tile(tmp_path, point=point)
    probe = EvidenceReplayProbe(
        probe_id="elevation",
        kind="elevation",
        source_layer="environment-agency/lidar-composite-dtm-1m",
        selector=point,
    )
    first_request = _request(
        tmp_path,
        bindings=(),
        probes=(probe,),
        elevation_state_fingerprint=state,
    )
    first = run_source_query_replay(first_request)

    runtime_lock_path, extension_cache = _runtime_paths(tmp_path)
    store = LocalEvidenceStore(
        store_path=tmp_path / "evidence.duckdb",
        runtime_lock_path=runtime_lock_path,
        extension_cache=extension_cache,
    )
    expanded = store.refresh_ea_elevation_cache(
        cache_dir=tmp_path / "ea-cache",
        requested_bng_10km_cells=("SP50",),
    )

    second = run_source_query_replay(
        _request(
            tmp_path,
            bindings=(),
            probes=(probe,),
            elevation_state_fingerprint=expanded.fingerprint,
        )
    )

    assert first.manifest["work"]["ea_oracle_samples"] == 1  # type: ignore[index]
    assert first.manifest["work"]["ea_replay_store_samples"] == 1  # type: ignore[index]
    assert first.manifest["work"]["ea_oracle_receipt_reads"] == 2  # type: ignore[index]
    assert first.manifest["work"]["ea_oracle_object_reads"] == 5  # type: ignore[index]
    assert first.manifest["work"]["ea_replay_receipt_reads"] == 2  # type: ignore[index]
    assert first.manifest["work"]["ea_replay_object_reads"] == 5  # type: ignore[index]
    assert second.manifest["work"]["derived_hits"] == 1  # type: ignore[index]
    assert second.manifest["work"]["ea_oracle_samples"] == 0  # type: ignore[index]
    assert second.manifest["work"]["ea_replay_store_samples"] == 0  # type: ignore[index]
    assert second.manifest["work"]["ea_cache_validation_receipt_reads"] == 1  # type: ignore[index]
    assert second.manifest["work"]["ea_cache_validation_object_reads"] == 2  # type: ignore[index]
    dependency = second.manifest["probes"][0]["dependency"]  # type: ignore[index]
    assert "coverage_state_fingerprint" not in dependency
    assert dependency["consulted_attestation_fingerprints"] == [attestation]
    assert dependency["tile_receipt_fingerprints"] == [receipt]
    assert second.manifest["cache"]["reused_generation"] is True  # type: ignore[index]


@RUN_REPLAY
def test_fabricated_ea_store_sample_cannot_match_closed_receipt_object_oracle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_commit(monkeypatch)
    point = Point(355_100, 165_100)
    state, attestation, receipt = _register_ea_tile(tmp_path, point=point)
    probe = EvidenceReplayProbe(
        probe_id="elevation",
        kind="elevation",
        source_layer="environment-agency/lidar-composite-dtm-1m",
        selector=point,
    )
    direct_calls = 0
    direct_result: ElevationSamplingResult | None = None
    fabrication = "dependency"
    direct_sample = replay_module.sample_elevation_from_receipts

    def observed_direct_sample(*args: object, **kwargs: object) -> ElevationSamplingResult:
        nonlocal direct_calls, direct_result
        direct_calls += 1
        direct_result = direct_sample(*args, **kwargs)  # type: ignore[arg-type]
        return direct_result

    def fabricated_store_sample(
        _self: LocalEvidenceStore,
        *,
        state_fingerprint: str | None,
        **_kwargs: object,
    ) -> ElevationSamplingResult:
        assert state_fingerprint is not None
        assert direct_result is not None
        fabricated_receipt = SHA_B if fabrication == "dependency" else receipt
        return ElevationSamplingResult(
            coverage_state_fingerprint=state_fingerprint,
            geometry_fingerprint=direct_result.geometry_fingerprint,
            spacing_mm=direct_result.spacing_mm,
            consulted_attestation_fingerprints=(
                SHA_A if fabrication == "dependency" else attestation,
            ),
            tile_receipt_fingerprints=(fabricated_receipt,),
            observations=(
                replace(
                    direct_result.observations[0],
                    elevation_mm=987_654_321,
                    tile_receipt_fingerprint=fabricated_receipt,
                ),
            ),
        )

    monkeypatch.setattr(
        replay_module,
        "sample_elevation_from_receipts",
        observed_direct_sample,
    )
    monkeypatch.setattr(
        LocalEvidenceStore,
        "sample_elevation",
        fabricated_store_sample,
    )
    request = _request(
        tmp_path,
        bindings=(),
        probes=(probe,),
        elevation_state_fingerprint=state,
    )

    with pytest.raises(
        ValueError,
        match="EA Local Evidence sample dependency differs",
    ):
        run_source_query_replay(request)

    assert direct_calls == 1
    assert not request.run_manifest_path.exists()
    fabrication = "value"

    with pytest.raises(
        ValueError,
        match="differs from the governed EA receipt/object observation",
    ):
        run_source_query_replay(request)

    assert direct_calls == 2
    assert not request.run_manifest_path.exists()


@RUN_REPLAY
def test_second_publication_write_failure_preserves_committed_cache_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clean_commit(monkeypatch)
    binding = _banes_binding(tmp_path)
    first_request = _request(
        tmp_path,
        bindings=(binding,),
        probes=(_vector_probe("first", Point(355_100, 165_000)),),
    )
    run_source_query_replay(first_request)
    committed = first_request.run_manifest_path.read_bytes()

    def fail_commit(_path: Path, _value: object) -> None:
        raise OSError("injected manifest publication failure")

    monkeypatch.setattr(replay_module, "_atomic_write_json", fail_commit)
    changed_request = _request(
        tmp_path,
        bindings=(binding,),
        probes=(_vector_probe("second", Point(355_200, 165_000)),),
    )

    with pytest.raises(OSError, match="injected manifest"):
        run_source_query_replay(changed_request)

    assert first_request.run_manifest_path.read_bytes() == committed


@RUN_REPLAY
@pytest.mark.parametrize("target", ["manifest", "dependency"])
def test_committed_manifest_or_dependency_corruption_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    _clean_commit(monkeypatch)
    binding = _banes_binding(tmp_path)
    request = _request(
        tmp_path,
        bindings=(binding,),
        probes=(_vector_probe("banes", Point(355_100, 165_000)),),
    )
    run_source_query_replay(request)
    if target == "manifest":
        commit = json.loads(request.run_manifest_path.read_text())
        commit["manifest"]["scope"] = "fabricated"
        request.run_manifest_path.write_text(json.dumps(commit), encoding="utf-8")
    else:
        commit = json.loads(request.run_manifest_path.read_text())
        generation = (
            replay_module._generation_directory(request.cache_path)
            / f"{commit['generation_fingerprint']}.json"
        )
        document = json.loads(generation.read_text())
        entry = next(iter(document["entries"].values()))
        entry["dependency"]["probe_fingerprint"] = SHA_C
        generation.write_text(json.dumps(document), encoding="utf-8")
    prior_store = request.store_path.read_bytes()

    with pytest.raises(ValueError, match="committed state is corrupt"):
        run_source_query_replay(request)

    assert request.store_path.read_bytes() == prior_store


@RUN_REPLAY
def test_dirty_or_unavailable_commit_cannot_pass_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        replay_module,
        "_git_identity",
        lambda: {"sha": None, "available": False, "dirty": True},
    )
    binding = _banes_binding(tmp_path)

    result = run_source_query_replay(
        _request(
            tmp_path,
            bindings=(binding,),
            probes=(_vector_probe("banes", Point(355_100, 165_000)),),
        )
    )

    assert not result.accepted
    assert result.manifest["exit"] == {"code": 2, "status": "not-accepted"}
    assert result.manifest["acceptance"]["reasons"] == [  # type: ignore[index]
        "git-commit-unavailable",
        "git-worktree-dirty",
    ]


@RUN_REPLAY
def test_gate_never_claims_or_invokes_compiler_or_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import satn.compiler
    import satn.pipeline
    import satn.publisher

    _clean_commit(monkeypatch)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("compiler/publication path was invoked")

    monkeypatch.setattr(satn.pipeline, "compile", forbidden)
    monkeypatch.setattr(satn.compiler, "compile_network", forbidden)
    monkeypatch.setattr(satn.publisher, "publish", forbidden)
    binding = _banes_binding(tmp_path)

    result = run_source_query_replay(
        _request(
            tmp_path,
            bindings=(binding,),
            probes=(_vector_probe("banes", Point(355_100, 165_000)),),
        )
    )

    assert result.manifest["scope"] == "source-query-replay-gate"
    assert set(result.manifest["claims_excluded"]) >= {
        "compiler-equivalence",
        "network-artifact-equivalence",
        "publication-equivalence",
    }


def test_generation_dependency_fingerprint_is_not_backend_declared() -> None:
    assert "VectorReplayBackend" not in replay_module.__dict__
    assert "ElevationReplayBackend" not in replay_module.__dict__
    assert "VectorPreparation" not in replay_module.__dict__
    assert "ElevationPreparation" not in replay_module.__dict__
    assert (
        evidence_fingerprint(
            {
                "contract": "test/v1",
                "measured": True,
            }
        )
        != SHA_A
    )
