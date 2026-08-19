from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import tracemalloc
import urllib.parse
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

import satn.ea_snapshot_recovery as recovery_module
import satn.sources as sources_module
import satn.streaming_geojson as streaming_geojson_module
from satn import compile
from satn.agents import FakeAgentRuntime
from satn.compilation_dependencies import compilation_dependency_manifest
from satn.compiler import compile_network
from satn.ea_elevation import (
    DTM_ATTRIBUTION,
    FIXED_POINT_PRIMARY_FIELD,
    LEGACY_CONTRACT_SCHEMA_VERSION,
    LEGACY_SAMPLE_LEDGER_SCHEMA_VERSION,
    SAMPLE_LEDGER_FILENAME,
    WECA_PINNED_ELIGIBLE_ROUTE_BBOX,
    canonical_ea_elevation_evidence_bytes,
    eligible_route_fingerprint,
    evidence_row_sha256,
    fixed_point_route_fingerprint,
    read_sample_ledger,
    sha256_file,
    write_sample_ledger,
)
from satn.filesystem_safety import publication_destination_authority
from satn.models import (
    CouncilConfig,
    NationalElevationConfig,
    ObservedThroughTrafficConfig,
    OfficialRoadClassificationConfig,
    RetainedCoreSourceConfig,
    canonical_decision_ledger_payload,
)
from satn.pipeline import (
    _reuse_validated_publication,
    compilation_governed_input_fingerprint,
    decision_ledger_input_fingerprint,
)
from satn.publisher import (
    EA_FIXED_POINT_CANDIDATE_DIRECTORY,
    EA_FIXED_POINT_CANDIDATE_NETWORK,
    EA_FIXED_POINT_CANDIDATE_STATUS,
    _ea_fixed_point_candidate_path,
    _retain_ea_fixed_point_candidate,
    _validate_ea_elevation_fixed_point,
    _write_geojson,
    publish,
    validate_publication,
)
from satn.sources import (
    EA_LIDAR_COVERAGE_ID,
    EA_LIDAR_DATASET_ID,
    EA_LIDAR_ENDPOINT,
    EA_RETAINED_ROUTE_FILENAME,
    ELEVATION_EVIDENCE_FILENAME,
    _osm_elevation_corroboration,
    _replace_snapshot_directory,
    _validate_snapshot,
    load_snapshot,
    promote_staged_snapshot,
    snapshot,
    stage_retained_core_snapshot,
)


def _official_index(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    path = tmp_path / "survey-index.geojson"
    gpd.GeoDataFrame(
        [
            {
                "id": "synthetic-survey-1",
                "filename": "synthetic",
                "tilename": "synthetic",
                "polygon_id": "synthetic",
                "resolution": 1,
                "year": 2022,
                "od_dtm_fn": "synthetic",
                "sd_flown": "2022-01-01",
                "ed_flown": "2022-01-02",
                "geometry": Point(-2.5, 51.4).buffer(1),
            }
        ],
        geometry="geometry",
        crs=4326,
    ).to_file(path, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {
                "authority": "synthetic",
                "authority_id": "synthetic",
                "source_query": "synthetic",
                "geometry": Point(-2.5, 51.4).buffer(1),
            }
        ],
        geometry="geometry",
        crs=4326,
    ).to_file(tmp_path / "authorities.geojson", driver="GeoJSON")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, {
        "official": "synthetic-contract",
        "raw_sha256": digest,
        "canonical_feature_sha256": "synthetic-feature-digest",
    }


PROJECT = Path(__file__).parents[1]


def _publication_compiled(config: CouncilConfig):
    """Build a compact deterministic candidate without any live EA acquisition."""
    from test_backbone_assembly import parallel_spine_source

    return compile_network(config, parallel_spine_source(), FakeAgentRuntime())


@pytest.mark.parametrize("unsafe_name", ["../outside.geojson", "/tmp/outside.geojson"])
def test_snapshot_rejects_traversal_and_absolute_retained_filenames(
    tmp_path: Path, unsafe_name: str
) -> None:
    config = copied_config(tmp_path)
    snapshot_path = snapshot(config)
    manifest_path = snapshot_path / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(unsafe_name)
    manifest["file_sha256"][unsafe_name] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="safe sibling basename"):
        _validate_snapshot(snapshot_path)


def test_snapshot_rejects_duplicate_directory_special_and_symlink_retained_filenames(
    tmp_path: Path,
) -> None:
    config = copied_config(tmp_path)
    snapshot_path = snapshot(config)
    manifest_path = snapshot_path / "snapshot.json"

    def invalid(name: str, *, setup: object | None = None) -> None:
        if callable(setup):
            setup()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"].append(name)
        manifest["file_sha256"][name] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(ValueError, match=r"duplicates|regular non-symlink"):
            _validate_snapshot(snapshot_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"].pop()
        manifest["file_sha256"].pop(name)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    duplicate = json.loads(manifest_path.read_text(encoding="utf-8"))["files"][0]
    invalid(duplicate)
    invalid("retained-directory", setup=lambda: (snapshot_path / "retained-directory").mkdir())
    invalid("retained-special", setup=lambda: os.mkfifo(snapshot_path / "retained-special"))
    invalid(
        "retained-link.geojson",
        setup=lambda: (snapshot_path / "retained-link.geojson").symlink_to("network.geojson"),
    )


def test_snapshot_rejects_unsafe_provenance_filename_before_reading_retained_files(
    tmp_path: Path,
) -> None:
    config = copied_config(tmp_path)
    snapshot_path = snapshot(config)
    manifest_path = snapshot_path / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provenance_file_sha256"]["../outside.json"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="safe sibling basename"):
        _validate_snapshot(snapshot_path)


def _streaming_snapshot(
    tmp_path: Path,
    *,
    feature_lines: list[bytes],
    crs_name: str = "urn:ogc:def:crs:EPSG::27700",
) -> tuple[Path, Path]:
    snapshot_path = tmp_path / "streaming-snapshot"
    snapshot_path.mkdir()
    routes = snapshot_path / EA_RETAINED_ROUTE_FILENAME
    with routes.open("wb") as stream:
        stream.write(b'{\n"type": "FeatureCollection",\n')
        stream.write(
            b'"crs":'
            + json.dumps(
                {
                    "type": "name",
                    "properties": {"name": crs_name},
                }
            ).encode()
            + b",\n"
        )
        stream.write(b'"features": [\n')
        for index, line in enumerate(feature_lines):
            stream.write(line)
            stream.write(b",\n" if index + 1 < len(feature_lines) else b"\n")
        stream.write(b"]\n}\n")
    manifest = {
        "schema_version": sources_module.SCHEMA_VERSION,
        "snapshot_id": snapshot_path.name,
        "files": [routes.name],
        "file_sha256": {routes.name: sha256_file(routes)},
        "provenance_file_sha256": {},
        "evidence_sources": {},
    }
    (snapshot_path / "snapshot.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return snapshot_path, routes


def _route_feature_line(feature_id: str = "route-a") -> bytes:
    return json.dumps(
        {
            "type": "Feature",
            "properties": {
                "feature_id": feature_id,
                "feature_type": "spine-access-connection",
                "topography_profile_id": f"profile-{feature_id}",
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[350000, 150000], [350010, 150000]],
            },
        },
        separators=(",", ":"),
    ).encode()


def _single_line_feature_collection(features: list[bytes]) -> bytes:
    return (
        b'{"type":"FeatureCollection","crs":{"type":"name","properties":'
        b'{"name":"urn:ogc:def:crs:EPSG::27700"}},"features":[' + b",".join(features) + b"]}"
    )


def _replace_streaming_routes(
    snapshot_path: Path,
    routes: Path,
    payload: bytes,
) -> None:
    routes.write_bytes(payload)
    manifest_path = snapshot_path / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["file_sha256"][routes.name] = sha256_file(routes)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_snapshot_streaming_parser_accepts_single_line_collection_across_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_path, routes = _streaming_snapshot(
        tmp_path,
        feature_lines=[_route_feature_line()],
    )
    payload = _single_line_feature_collection(
        [_route_feature_line(f"route-{index}") for index in range(2_000)]
    )
    _replace_streaming_routes(snapshot_path, routes, payload)
    monkeypatch.setattr(sources_module, "STREAMING_GEOJSON_THRESHOLD_BYTES", 1)
    monkeypatch.setattr(streaming_geojson_module, "READ_CHUNK_BYTES", 64)

    _validate_snapshot(snapshot_path)


@pytest.mark.parametrize(
    "payload",
    [
        b"garbage" + _single_line_feature_collection([_route_feature_line()]),
        (b'{"type":"FeatureCollection","features":[' + _route_feature_line() + b"] garbage}"),
        _single_line_feature_collection([_route_feature_line()]) + b"}",
        _single_line_feature_collection([_route_feature_line()]) + b" true",
    ],
    ids=["prefix", "middle", "suffix", "trailing"],
)
def test_snapshot_streaming_parser_rejects_wrapper_garbage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    snapshot_path, routes = _streaming_snapshot(
        tmp_path,
        feature_lines=[_route_feature_line()],
    )
    _replace_streaming_routes(snapshot_path, routes, payload)
    monkeypatch.setattr(sources_module, "STREAMING_GEOJSON_THRESHOLD_BYTES", 1)
    monkeypatch.setattr(streaming_geojson_module, "READ_CHUNK_BYTES", 17)

    with pytest.raises(ValueError):
        _validate_snapshot(snapshot_path)


def test_large_snapshot_geojson_validation_has_bounded_python_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_path, routes = _streaming_snapshot(
        tmp_path,
        feature_lines=[_route_feature_line(f"route-{index}") for index in range(20_000)],
    )
    monkeypatch.setattr(sources_module, "STREAMING_GEOJSON_THRESHOLD_BYTES", 1)
    original_read_bytes = Path.read_bytes
    original_read_file = gpd.read_file

    def bounded_read_bytes(path: Path) -> bytes:
        if path == routes:
            raise AssertionError("large GeoJSON must not use Path.read_bytes")
        return original_read_bytes(path)

    def bounded_read_file(path: object, *args: object, **kwargs: object):
        if Path(path) == routes:
            raise AssertionError("large GeoJSON must not use geopandas.read_file")
        return original_read_file(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", bounded_read_bytes)
    monkeypatch.setattr(gpd, "read_file", bounded_read_file)
    tracemalloc.start()
    _validate_snapshot(snapshot_path)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert routes.stat().st_size > 4_000_000
    assert peak < 4_000_000


def test_large_ea_route_reconciliation_streams_without_geopandas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature = json.loads(_route_feature_line())
    feature["properties"][FIXED_POINT_PRIMARY_FIELD] = True
    _snapshot_path, routes = _streaming_snapshot(
        tmp_path,
        feature_lines=[json.dumps(feature, separators=(",", ":")).encode()],
    )
    rows = [
        {"route_id": "route-a", "sample_index": 0, "east_mm": 350000000, "north_mm": 150000000},
        {"route_id": "route-a", "sample_index": 1, "east_mm": 350010000, "north_mm": 150000000},
    ]
    expected_routes = gpd.GeoDataFrame(
        [
            {
                "feature_id": "route-a",
                "feature_type": "spine-access-connection",
                "topography_profile_id": "profile-route-a",
                FIXED_POINT_PRIMARY_FIELD: True,
                "geometry": LineString([(350000, 150000), (350010, 150000)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    monkeypatch.setattr(sources_module, "STREAMING_GEOJSON_THRESHOLD_BYTES", 1)
    original_read_file = gpd.read_file

    def bounded_read_file(path: object, *args: object, **kwargs: object):
        if Path(path) == routes:
            raise AssertionError("large EA route reconciliation must stream")
        return original_read_file(path, *args, **kwargs)

    monkeypatch.setattr(gpd, "read_file", bounded_read_file)

    has_primary, fingerprint = sources_module._validate_streaming_ea_ledger_completeness(
        rows=rows,
        route_path=routes,
    )

    assert has_primary
    assert fingerprint == fixed_point_route_fingerprint(expected_routes)


@pytest.mark.parametrize(
    ("feature_lines", "crs_name", "message"),
    [
        ([], "urn:ogc:def:crs:EPSG::27700", "contains no features"),
        ([_route_feature_line()], "not-a-real-crs", "CRS"),
        ([b'{"type":"Feature","properties":'], "urn:ogc:def:crs:EPSG::27700", "invalid JSON"),
        (
            [
                json.dumps(
                    {
                        "type": "Feature",
                        "properties": {
                            "feature_id": "collapsed",
                            "feature_type": "spine-access-connection",
                            "topography_profile_id": "profile-collapsed",
                        },
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[350000, 150000], [350000, 150000]],
                        },
                    },
                    separators=(",", ":"),
                ).encode()
            ],
            "urn:ogc:def:crs:EPSG::27700",
            "collapses at identity precision",
        ),
    ],
)
def test_streaming_snapshot_validation_still_refuses_invalid_geojson(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    feature_lines: list[bytes],
    crs_name: str,
    message: str,
) -> None:
    snapshot_path, _routes = _streaming_snapshot(
        tmp_path,
        feature_lines=feature_lines,
        crs_name=crs_name,
    )
    monkeypatch.setattr(sources_module, "STREAMING_GEOJSON_THRESHOLD_BYTES", 1)

    with pytest.raises(ValueError, match=message):
        _validate_snapshot(snapshot_path)


def test_streaming_snapshot_validation_rejects_tampered_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_path, routes = _streaming_snapshot(
        tmp_path,
        feature_lines=[_route_feature_line()],
    )
    with routes.open("ab") as stream:
        stream.write(b"tampered")
    monkeypatch.setattr(sources_module, "STREAMING_GEOJSON_THRESHOLD_BYTES", 1)

    with pytest.raises(ValueError, match="content hash mismatch"):
        _validate_snapshot(snapshot_path)


def test_snapshot_replacement_restores_a_stale_backup_after_failed_replacement(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "snapshot"
    backup = tmp_path / ".snapshot.previous"
    backup.mkdir()
    (backup / "sentinel").write_text("retained", encoding="utf-8")
    temporary = tmp_path / "temporary"
    temporary.mkdir()

    with pytest.raises(ValueError, match=r"missing.*snapshot.json"):
        _replace_snapshot_directory(temporary, destination)

    assert (destination / "sentinel").read_text(encoding="utf-8") == "retained"


def test_weca_bootstrap_and_final_definitions_are_separate_parseable_workflow_stages() -> None:
    bootstrap = CouncilConfig.from_yaml(PROJECT / "deployments/weca/area-bootstrap.yaml")
    final = CouncilConfig.from_yaml(PROJECT / "deployments/weca/area.yaml")

    assert bootstrap.source.national_elevation is None
    assert bootstrap.source.snapshot_id != final.source.snapshot_id
    retained = final.source.retained_core_source
    assert retained is not None
    assert retained.snapshot_id == (
        "weca-classification-elevation-2026-07-31-v14-fp-20260731T092920522968Z-02-"
        "fp-20260809T123804416841Z-01"
    )
    retained_snapshot = final.source.snapshot_dir / retained.snapshot_id / "snapshot.json"
    assert retained_snapshot.is_file()
    assert retained.manifest_sha256 == hashlib.sha256(retained_snapshot.read_bytes()).hexdigest()
    assert final.source.snapshot_id == (
        "weca-classification-elevation-2026-07-31-v14-fp-20260731T092920522968Z-02-"
        "fp-20260809T123804416841Z-01-fp-20260819T011108973323Z-01"
    )
    assert bootstrap.publication.output_dir != final.publication.output_dir
    assert final.source.national_elevation is not None
    assert final.source.national_elevation.source_id == "ea-lidar-composite-dtm-1m"


def copied_config(tmp_path: Path) -> CouncilConfig:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    return CouncilConfig.from_yaml(fixture / "council.yaml")


def _final_ea_config(config: CouncilConfig, tmp_path: Path) -> CouncilConfig:
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=tmp_path / "not-read-by-fixed-point.geojson",
        source_id="ea-lidar-composite-dtm-1m",
        acquisition_contract="ea-lidar-weca-v1",
        licence="Open Government Licence v3.0",
        attribution=DTM_ATTRIBUTION,
    )
    return config


def _write_final_ea_snapshot(config: CouncilConfig, *, pre_elevation_network_sha256: str) -> Path:
    """Create the minimal immutable retained evidence set accepted at publication."""
    snapshot_path = config.source.snapshot_dir / config.source.snapshot_id
    snapshot_path.mkdir(parents=True, exist_ok=True)
    evidence = gpd.GeoDataFrame(
        [
            {
                "evidence_id": "retained-elevation-1",
                "source_id": "ea-lidar-composite-dtm-1m",
                "effective_date": "2022-01-02",
                "licence": "Open Government Licence v3.0",
                "elevation_m": 10.0,
                "route_id": "retained-route",
                "sample_index": 0,
                "evidence_row_sha256": "a" * 64,
                "source_resolution_m": 1.0,
                "output_sample_spacing_m": 10.0,
                "geometry": Point(-2.5, 51.4),
            }
        ],
        geometry="geometry",
        crs=4326,
    )
    west, south, east, north = WECA_PINNED_ELIGIBLE_ROUTE_BBOX
    retained_routes = gpd.GeoDataFrame(
        [
            {
                "feature_id": "retained-route",
                "feature_type": "strategic-spine",
                "topography_profile_id": "retained-profile",
                "geometry": LineString([(west, south), (east, north)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    retained = {
        ELEVATION_EVIDENCE_FILENAME: canonical_ea_elevation_evidence_bytes(
            evidence,
            source_id="ea-lidar-composite-dtm-1m",
            licence="Open Government Licence v3.0",
            effective_date="2022-01-02",
            source_resolution_m=1,
            output_sample_spacing_m=10,
        ),
        SAMPLE_LEDGER_FILENAME: b"retained-elevation-sample-ledger",
        EA_RETAINED_ROUTE_FILENAME: retained_routes.to_json().encode(),
        "ea-authority-boundaries.geojson": b'{"authority":"retained"}',
        "ea-survey-index.geojson": b'{"survey":"retained"}',
    }
    for filename, contents in retained.items():
        (snapshot_path / filename).write_bytes(contents)
    digests = {
        filename: hashlib.sha256(contents).hexdigest() for filename, contents in retained.items()
    }
    acquisition_output_digest = hashlib.sha256(b"raw-elevation-acquisition-output").hexdigest()
    acquisition = {
        "source_id": "ea-lidar-composite-dtm-1m",
        "licence": "Open Government Licence v3.0",
        "effective_survey_date": "2022-01-02",
        "source_resolution_m": 1,
        "output_sample_spacing_m": 10,
        "acquisition_protocol": "two-pass-fixed-point/v1",
        "pre_elevation_network_sha256": pre_elevation_network_sha256,
        "output_sha256": acquisition_output_digest,
        "sample_ledger_path": SAMPLE_LEDGER_FILENAME,
        "sample_ledger_sha256": digests[SAMPLE_LEDGER_FILENAME],
        "sample_route_path": EA_RETAINED_ROUTE_FILENAME,
        "sample_route_sha256": digests[EA_RETAINED_ROUTE_FILENAME],
        "authority_boundaries_path": "ea-authority-boundaries.geojson",
        "survey_index_path": "ea-survey-index.geojson",
        "survey_index_sha256": digests["ea-survey-index.geojson"],
        "survey_coverage_preflight": {
            "authority_boundaries": {
                "raw_sha256": digests["ea-authority-boundaries.geojson"],
            }
        },
        "governed_input_fingerprint": "b" * 64,
    }
    acquisition_path = snapshot_path / "elevation-evidence.manifest.json"
    acquisition_path.write_text(json.dumps(acquisition, sort_keys=True) + "\n", encoding="utf-8")
    digests[acquisition_path.name] = hashlib.sha256(acquisition_path.read_bytes()).hexdigest()
    manifest = {
        "evidence_sources": {
            "elevation": {
                "acquisition_protocol": "two-pass-fixed-point/v1",
                "pre_elevation_network_sha256": pre_elevation_network_sha256,
                "content_fingerprint": digests[ELEVATION_EVIDENCE_FILENAME],
                "acquisition_output_sha256": acquisition_output_digest,
                "sample_ledger_sha256": digests[SAMPLE_LEDGER_FILENAME],
                "ea_acquisition_manifest_sha256": digests["elevation-evidence.manifest.json"],
                "replay_inputs": {
                    "schema_version": "ea-fixed-point-replay-inputs/v1",
                    "licence": "Open Government Licence v3.0",
                    "files": {
                        "authority_boundaries": {
                            "path": "ea-authority-boundaries.geojson",
                            "sha256": digests["ea-authority-boundaries.geojson"],
                        },
                        "sample_ledger": {
                            "path": SAMPLE_LEDGER_FILENAME,
                            "sha256": digests[SAMPLE_LEDGER_FILENAME],
                        },
                        "sample_routes": {
                            "path": EA_RETAINED_ROUTE_FILENAME,
                            "sha256": digests[EA_RETAINED_ROUTE_FILENAME],
                        },
                        "survey_index": {
                            "path": "ea-survey-index.geojson",
                            "sha256": digests["ea-survey-index.geojson"],
                        },
                    },
                },
            }
        },
        "file_sha256": {
            filename: digest
            for filename, digest in digests.items()
            if filename != "elevation-evidence.manifest.json"
        },
        "provenance_file_sha256": digests,
    }
    (snapshot_path / "snapshot.json").write_text(json.dumps(manifest), encoding="utf-8")
    return snapshot_path


def _rewrite_retained_ea_acquisition(snapshot_path: Path, mutate: object) -> None:
    """Change the retained statement while keeping its snapshot file proof current."""
    acquisition_path = snapshot_path / "elevation-evidence.manifest.json"
    acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
    assert callable(mutate)
    mutate(acquisition)
    acquisition_path.write_text(json.dumps(acquisition, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(acquisition_path.read_bytes()).hexdigest()
    snapshot_manifest_path = snapshot_path / "snapshot.json"
    snapshot_manifest = json.loads(snapshot_manifest_path.read_text(encoding="utf-8"))
    snapshot_manifest["evidence_sources"]["elevation"]["ea_acquisition_manifest_sha256"] = digest
    snapshot_manifest["provenance_file_sha256"][acquisition_path.name] = digest
    snapshot_manifest_path.write_text(json.dumps(snapshot_manifest), encoding="utf-8")


def _reseal_retained_evidence_hashes(snapshot_path: Path) -> None:
    """Model an attacker updating every mutable hash for retained evidence."""
    evidence_path = snapshot_path / ELEVATION_EVIDENCE_FILENAME
    digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    snapshot_manifest_path = snapshot_path / "snapshot.json"
    snapshot_manifest = json.loads(snapshot_manifest_path.read_text(encoding="utf-8"))
    snapshot_manifest["file_sha256"][ELEVATION_EVIDENCE_FILENAME] = digest
    snapshot_manifest["provenance_file_sha256"][ELEVATION_EVIDENCE_FILENAME] = digest
    snapshot_manifest["evidence_sources"]["elevation"]["content_fingerprint"] = digest
    snapshot_manifest_path.write_text(
        json.dumps(snapshot_manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def _final_eligible_network(path: Path) -> str:
    routes = gpd.GeoDataFrame(
        [
            {
                "feature_id": "final-route",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile",
                "geometry": LineString([(350000, 150000), (350010, 150000)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    routes.to_file(path, driver="GeoJSON")
    return eligible_route_fingerprint(routes)


def _pinned_weca_candidate(path: Path) -> Path:
    from satn.ea_elevation import WECA_PINNED_ELIGIBLE_ROUTE_BBOX

    west, south, east, north = WECA_PINNED_ELIGIBLE_ROUTE_BBOX
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "pinned-route",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile",
                "geometry": LineString([(west, south), (east, north)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(path, driver="GeoJSON")
    return path


def _retain_candidate_status(
    config: CouncilConfig,
    candidate_network: Path,
    *,
    run_id: str,
) -> dict[str, object]:
    _retain_ea_fixed_point_candidate(
        config,
        run_id=run_id,
        network_path=candidate_network,
        expected="0" * 64,
        actual=eligible_route_fingerprint(gpd.read_file(candidate_network)),
        governed_input_fingerprint="c" * 64,
    )
    return json.loads(
        (_ea_fixed_point_candidate_path(config) / EA_FIXED_POINT_CANDIDATE_STATUS).read_text()
    )


def test_local_national_elevation_is_clipped_and_snapshotted_with_provenance(
    tmp_path: Path,
) -> None:
    config = copied_config(tmp_path)
    terrain = tmp_path / "national-terrain.geojson"
    gpd.GeoDataFrame(
        [
            {
                "sample": "inside-1",
                "height": 10,
                "source_resolution_m": 1,
                "output_sample_spacing_m": 10,
                "geometry": Point(-2.50, 51.40),
            },
            {
                "sample": "inside-2",
                "height": 20,
                "source_resolution_m": 1,
                "output_sample_spacing_m": 10,
                "geometry": Point(-2.48, 51.41),
            },
            {"sample": "outside", "height": 30, "geometry": Point(-1.0, 52.0)},
        ],
        geometry="geometry",
        crs=4326,
    ).to_file(terrain, driver="GeoJSON")
    config.source.external_buffer_km = 0.1
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=terrain,
        source_id="national-dtm-2026",
        effective_date="2026-01-15",
        licence="Open Government Licence v3.0",
        attribution="National terrain test source",
        elevation_field="height",
        identifier_field="sample",
    )

    path = snapshot(config)
    loaded = load_snapshot(config)["elevation_evidence"]
    manifest = json.loads((path / "snapshot.json").read_text())

    assert set(loaded["evidence_id"]) == {"inside-1", "inside-2"}
    assert set(loaded["source_id"]) == {"national-dtm-2026"}
    assert list(loaded["elevation_m"]) == [10.0, 20.0]
    assert list(loaded["source_resolution_m"]) == [1, 1]
    assert list(loaded["output_sample_spacing_m"]) == [10, 10]
    elevation_digest = hashlib.sha256((path / ELEVATION_EVIDENCE_FILENAME).read_bytes()).hexdigest()
    assert manifest["file_sha256"][ELEVATION_EVIDENCE_FILENAME] == elevation_digest
    assert manifest["provenance_file_sha256"][ELEVATION_EVIDENCE_FILENAME] == elevation_digest
    assert manifest["evidence_sources"]["elevation"]["content_fingerprint"] == elevation_digest
    assert manifest["evidence_sources"]["elevation"] | {
        "content_fingerprint": "ignored",
        "retrieved_at": "ignored",
    } == {
        "provider": "local-geojson",
        "source_id": "national-dtm-2026",
        "effective_date": "2026-01-15",
        "date_kind": "effective",
        "licence": "Open Government Licence v3.0",
        "attribution": "National terrain test source",
        "bounded_to_compilation_area": True,
        "coverage_status": "available",
        "sample_count": 2,
        "content_fingerprint": "ignored",
        "retrieved_at": "ignored",
    }


def test_generic_banes_ea_source_is_not_reinterpreted_as_the_weca_ledger_contract(
    tmp_path: Path,
) -> None:
    config = copied_config(tmp_path)
    terrain = tmp_path / "banes-ea-samples.geojson"
    gpd.GeoDataFrame(
        [{"sample": "banes-1", "height": 12, "geometry": Point(-2.50, 51.40)}],
        geometry="geometry",
        crs=4326,
    ).to_file(terrain, driver="GeoJSON")
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=terrain,
        source_id="ea-lidar-composite-dtm-1m",
        licence="Open Government Licence v3.0",
        attribution=DTM_ATTRIBUTION,
        elevation_field="height",
        identifier_field="sample",
    )

    path = snapshot(config)

    assert (
        "ea_acquisition_manifest_sha256"
        not in json.loads((path / "snapshot.json").read_text(encoding="utf-8"))["evidence_sources"][
            "elevation"
        ]
    )


def _write_synthetic_ea_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pre_elevation_network_sha256: str = "a" * 64,
) -> tuple[CouncilConfig, Path, str]:
    """Build a writer-validated EA snapshot without any live WECA acquisition."""
    config = copied_config(tmp_path)
    terrain = tmp_path / "ea-samples.geojson"
    gpd.GeoDataFrame(
        [
            {
                "sample": "ea-1",
                "route_id": "route-1",
                "sample_index": 0,
                "height": 10,
                "evidence_row_sha256": "placeholder",
                "geometry": Point(-2.50, 51.40),
            }
        ],
        geometry="geometry",
        crs=4326,
    ).to_file(terrain, driver="GeoJSON")
    point_27700 = gpd.read_file(terrain).to_crs(27700).geometry.iloc[0]
    evidence_hash = evidence_row_sha256(
        route_id="route-1",
        sample_index=0,
        east_mm=round(point_27700.x * 1000),
        north_mm=round(point_27700.y * 1000),
        elevation_m=10,
    )
    source = gpd.read_file(terrain)
    source["evidence_row_sha256"] = evidence_hash
    source.to_file(terrain, driver="GeoJSON")
    terrain_digest = hashlib.sha256(terrain.read_bytes()).hexdigest()
    ledger_digest = write_sample_ledger(
        tmp_path / "ea-elevation-sample-ledger.jsonl",
        [
            {
                "schema_version": "ea-lidar-sample-ledger/v1",
                "route_id": "route-1",
                "sample_index": 0,
                "route_position": 0,
                "previous_sample_index": None,
                "next_sample_index": None,
                "east_mm": round(point_27700.x * 1000),
                "north_mm": round(point_27700.y * 1000),
                "authority_id": "synthetic",
                "bucket": "authority",
                "availability": "available",
                "elevation_m": 10.0,
                "survey_feature_id": "synthetic-survey-1",
                "ed_flown": "2022-01-02",
                "resolution_m": 1.0,
                "evidence_row_sha256": evidence_hash,
            }
        ],
    )
    network_digest = pre_elevation_network_sha256
    index, official_index = _official_index(tmp_path)
    monkeypatch.setattr(
        "satn.sources.validate_official_weca_survey_index", lambda _path: official_index
    )
    # This fixture isolates snapshot sibling relinking; dedicated acquisition
    # tests exercise route-linework regeneration.
    monkeypatch.setattr("satn.sources._validate_ea_ledger_completeness", lambda **_kwargs: None)
    preflight = {
        "status": "available",
        "official_survey_index": official_index,
        "authority_boundaries": {
            "raw_sha256": hashlib.sha256(
                (tmp_path / "authorities.geojson").read_bytes()
            ).hexdigest()
        },
        "sample_validation": {
            "status": "available",
            "authorities": [
                {
                    "requested_sample_count": 1,
                    "available_sample_count": 1,
                    "nodata_sample_count": 0,
                    "explicit_unknown_sample_count": 0,
                },
                {
                    "requested_sample_count": 0,
                    "available_sample_count": 0,
                    "nodata_sample_count": 0,
                    "explicit_unknown_sample_count": 0,
                },
                {
                    "requested_sample_count": 0,
                    "available_sample_count": 0,
                    "nodata_sample_count": 0,
                    "explicit_unknown_sample_count": 0,
                },
                {
                    "requested_sample_count": 0,
                    "available_sample_count": 0,
                    "nodata_sample_count": 0,
                    "explicit_unknown_sample_count": 0,
                },
                {
                    "requested_sample_count": 0,
                    "available_sample_count": 0,
                    "nodata_sample_count": 0,
                    "explicit_unknown_sample_count": 0,
                },
            ],
            "cross_boundary_transitions": [],
        },
    }
    terrain.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "source_id": "ea-lidar-composite-dtm-1m",
                "acquisition_protocol": "two-pass-fixed-point/v1",
                "contract_schema_version": "ea-lidar-composite-dtm-contract/v2",
                "dataset_id": EA_LIDAR_DATASET_ID,
                "coverage_id": EA_LIDAR_COVERAGE_ID,
                "endpoint": EA_LIDAR_ENDPOINT,
                "licence": "Open Government Licence v3.0",
                "attribution": DTM_ATTRIBUTION,
                "dataset_title": "LIDAR Composite Digital Terrain Model (DTM) - 1m",
                "source_resolution_m": 1,
                "output_sample_spacing_m": 10,
                "vertical_accuracy": "+/-15cm RMSE",
                "effective_survey_date": "2022-01-02",
                "output_sha256": terrain_digest,
                "sample_ledger_path": "ea-elevation-sample-ledger.jsonl",
                "sample_ledger_sha256": ledger_digest,
                "sample_ledger_schema_version": "ea-lidar-sample-ledger/v1",
                "sample_route_path": terrain.name,
                "sample_route_sha256": terrain_digest,
                "pre_elevation_network_sha256": network_digest,
                "requested_point_count": 1,
                "governed_input_fingerprint": "a" * 64,
                "authority_boundaries_path": "authorities.geojson",
                "evidence_sample_count": 1,
                "nodata_sample_count": 0,
                "explicit_unknown_sample_count": 0,
                "survey_coverage_preflight": preflight,
                "sample_validation": {"status": "available"},
                "survey_index_path": index.name,
                "survey_index_sha256": official_index["raw_sha256"],
                "survey_index_feature_sha256": official_index["canonical_feature_sha256"],
            }
        ),
        encoding="utf-8",
    )
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=terrain,
        source_id="ea-lidar-composite-dtm-1m",
        acquisition_contract="ea-lidar-weca-v1",
        effective_date="2023-02-08",
        licence="Open Government Licence v3.0",
        attribution=DTM_ATTRIBUTION,
        elevation_field="height",
        identifier_field="sample",
    )

    return config, snapshot(config), terrain_digest


def test_ea_acquisition_sidecar_binds_pre_elevation_network_to_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config, path, terrain_digest = _write_synthetic_ea_snapshot(tmp_path, monkeypatch)
    manifest = json.loads((path / "snapshot.json").read_text())

    elevation = manifest["evidence_sources"]["elevation"]
    assert elevation["pre_elevation_network_sha256"] == "a" * 64
    assert elevation["acquisition_output_sha256"] == terrain_digest
    assert elevation["acquisition_output_sha256"] != elevation["content_fingerprint"]
    assert elevation["acquisition_protocol"] == "two-pass-fixed-point/v1"
    assert len(elevation["ea_acquisition_manifest_sha256"]) == 64
    assert (path / "ea-authority-boundaries.geojson").exists()
    assert (path / "ea-elevation-sample-ledger.jsonl").exists()
    assert (
        manifest["provenance_file_sha256"][ELEVATION_EVIDENCE_FILENAME]
        == elevation["content_fingerprint"]
    )
    assert "ea-elevation-sample-ledger.jsonl" in manifest["provenance_file_sha256"]
    replay = elevation["replay_inputs"]
    assert replay["schema_version"] == "ea-fixed-point-replay-inputs/v1"
    assert replay["licence"] == "Open Government Licence v3.0"
    assert {role: record["path"] for role, record in replay["files"].items()} == {
        "authority_boundaries": "ea-authority-boundaries.geojson",
        "sample_ledger": SAMPLE_LEDGER_FILENAME,
        "sample_routes": EA_RETAINED_ROUTE_FILENAME,
        "survey_index": "ea-survey-index.geojson",
    }
    for record in replay["files"].values():
        assert manifest["file_sha256"][record["path"]] == record["sha256"]
        assert manifest["provenance_file_sha256"][record["path"]] == record["sha256"]
    copied_sidecar = json.loads((path / "elevation-evidence.manifest.json").read_text())
    assert copied_sidecar["contract_schema_version"] == LEGACY_CONTRACT_SCHEMA_VERSION
    assert copied_sidecar["sample_ledger_schema_version"] == LEGACY_SAMPLE_LEDGER_SCHEMA_VERSION
    assert "explicit_unknown_sample_count" not in elevation
    assert all(
        row["schema_version"] == LEGACY_SAMPLE_LEDGER_SCHEMA_VERSION
        for row in read_sample_ledger(path / SAMPLE_LEDGER_FILENAME)
    )
    assert copied_sidecar["authority_boundaries_path"] == "ea-authority-boundaries.geojson"
    assert copied_sidecar["survey_index_path"] == "ea-survey-index.geojson"


def test_snapshot_refuses_degenerate_eligible_routes_before_sealing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _initial, _terrain_digest = _write_synthetic_ea_snapshot(
        tmp_path,
        monkeypatch,
    )
    feature_id = "spine-access-07d8d07fe59d"
    retained_routes = tmp_path / "degenerate-sampled-routes.geojson"
    routes = gpd.GeoDataFrame(
        [
            {
                "feature_id": feature_id,
                "feature_type": "spine-access-connection",
                "topography_profile_id": "topography-profile-dc8152b42c505885",
                "geometry": LineString(
                    [
                        (369092.3832793793, 169040.53825675382),
                        (369092.3832793793, 169040.53825675382),
                    ]
                ),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    routes.to_file(retained_routes, driver="GeoJSON")
    sidecar_path = config.source.national_elevation.path.with_suffix(".manifest.json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["sample_route_path"] = retained_routes.name
    sidecar["sample_route_sha256"] = hashlib.sha256(retained_routes.read_bytes()).hexdigest()
    sidecar["pre_elevation_network_sha256"] = fixed_point_route_fingerprint(routes)
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    config.source.snapshot_id = "degenerate-routes-must-not-seal"

    with pytest.raises(
        ValueError,
        match=rf"{feature_id}.*collapses at identity precision",
    ):
        snapshot(config)

    assert not (config.source.snapshot_dir / config.source.snapshot_id).exists()


def test_lineaged_ea_snapshot_writer_output_passes_fixed_point_and_rejects_self_reseal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final_network = tmp_path / "final-network.geojson"
    fingerprint = _final_eligible_network(final_network)
    config, _initial, _terrain_digest = _write_synthetic_ea_snapshot(
        tmp_path, monkeypatch, pre_elevation_network_sha256=fingerprint
    )
    national_elevation = config.source.national_elevation

    config.source.national_elevation = None
    config.source.snapshot_id = "historical-core"
    historical = snapshot(config)
    historical_manifest_sha256 = hashlib.sha256(
        (historical / "snapshot.json").read_bytes()
    ).hexdigest()

    config.source.snapshot_id = "final-elevation"
    config.source.retained_core_source = RetainedCoreSourceConfig(
        snapshot_id="historical-core", manifest_sha256=historical_manifest_sha256
    )
    config.source.national_elevation = national_elevation
    target = snapshot(config, retain_core=True)

    _validate_ea_elevation_fixed_point(config, final_network)

    evidence_path = target / ELEVATION_EVIDENCE_FILENAME
    tampered_evidence = gpd.read_file(evidence_path)
    tampered_evidence.loc[0, "elevation_m"] = 999.0
    tampered_evidence.to_file(evidence_path, driver="GeoJSON")
    resealed_digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    manifest_path = target / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["file_sha256"][ELEVATION_EVIDENCE_FILENAME] = resealed_digest
    manifest["provenance_file_sha256"][ELEVATION_EVIDENCE_FILENAME] = resealed_digest
    manifest["evidence_sources"]["elevation"]["content_fingerprint"] = resealed_digest
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(
        ValueError, match="EA sample ledger observation differs from retained evidence"
    ):
        snapshot(config, retain_core=True)


def test_lineaged_ea_fixed_point_rejects_whitespace_after_full_hash_reseal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final_network = tmp_path / "final-network.geojson"
    fingerprint = _final_eligible_network(final_network)
    config, _initial, _terrain_digest = _write_synthetic_ea_snapshot(
        tmp_path, monkeypatch, pre_elevation_network_sha256=fingerprint
    )
    national_elevation = config.source.national_elevation
    config.source.national_elevation = None
    config.source.snapshot_id = "historical-core"
    historical = snapshot(config)
    config.source.snapshot_id = "final-elevation"
    config.source.retained_core_source = RetainedCoreSourceConfig(
        snapshot_id="historical-core",
        manifest_sha256=hashlib.sha256((historical / "snapshot.json").read_bytes()).hexdigest(),
    )
    config.source.national_elevation = national_elevation
    target = snapshot(config, retain_core=True)

    evidence_path = target / ELEVATION_EVIDENCE_FILENAME
    evidence_path.write_bytes(evidence_path.read_bytes() + b"\n")
    _reseal_retained_evidence_hashes(target)

    with pytest.raises(ValueError, match="retained evidence is not canonical GeoJSON"):
        snapshot(config, retain_core=True)


def test_lineaged_ea_fixed_point_rejects_forged_metadata_after_full_hash_reseal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final_network = tmp_path / "final-network.geojson"
    fingerprint = _final_eligible_network(final_network)
    config, _initial, _terrain_digest = _write_synthetic_ea_snapshot(
        tmp_path, monkeypatch, pre_elevation_network_sha256=fingerprint
    )
    national_elevation = config.source.national_elevation
    config.source.national_elevation = None
    config.source.snapshot_id = "historical-core"
    historical = snapshot(config)
    config.source.snapshot_id = "final-elevation"
    config.source.retained_core_source = RetainedCoreSourceConfig(
        snapshot_id="historical-core",
        manifest_sha256=hashlib.sha256((historical / "snapshot.json").read_bytes()).hexdigest(),
    )
    config.source.national_elevation = national_elevation
    target = snapshot(config, retain_core=True)

    evidence_path = target / ELEVATION_EVIDENCE_FILENAME
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    for feature in payload["features"]:
        feature["properties"]["source_id"] = "forged-source"
        feature["properties"]["licence"] = "Forged licence"
    evidence_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    _reseal_retained_evidence_hashes(target)

    with pytest.raises(ValueError, match="mismatches governed source_id"):
        snapshot(config, retain_core=True)


def test_retained_core_snapshot_augmentation_keeps_core_bytes_unchanged(tmp_path: Path) -> None:
    config = copied_config(tmp_path)
    first = snapshot(config)
    first_manifest = json.loads((first / "snapshot.json").read_text(encoding="utf-8"))
    core_hashes = {
        name: hashlib.sha256((first / name).read_bytes()).hexdigest()
        for name in ("boundary.geojson", "places.geojson", "network.geojson", "context.geojson")
    }
    config.source.snapshot_id = "retained-core-augmented"
    config.source.retained_core_source = RetainedCoreSourceConfig(
        snapshot_id=first.name,
        manifest_sha256=hashlib.sha256((first / "snapshot.json").read_bytes()).hexdigest(),
    )
    terrain = tmp_path / "retained-core-elevation.geojson"
    gpd.GeoDataFrame(
        [{"sample": "elevation", "height": 10, "geometry": Point(-2.5, 51.4)}],
        geometry="geometry",
        crs=4326,
    ).to_file(terrain, driver="GeoJSON")
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=terrain,
        source_id="retained-core-test-elevation",
        licence="Synthetic fixture",
        attribution="Synthetic fixture",
        elevation_field="height",
        identifier_field="sample",
    )
    classification = tmp_path / "retained-core-road-classification.geojson"
    gpd.GeoDataFrame(
        [
            {
                "osmid": "official-a",
                "official_classification": "A road",
                "geometry": LineString([(-2.5, 51.39), (-2.5, 51.45)]),
            }
        ],
        geometry="geometry",
        crs=4326,
    ).to_file(classification, driver="GeoJSON")
    config.source.official_road_classification = OfficialRoadClassificationConfig(
        path=classification,
        source_id="retained-core-test-road-classification",
        effective_date="2026-04-07",
        licence="Synthetic fixture",
    )
    through_traffic = tmp_path / "retained-core-observed-through-traffic.geojson"
    gpd.GeoDataFrame(
        [
            {
                "id": "traffic-study-1",
                "geometry": LineString([(-2.5, 51.4), (-2.48, 51.42)]),
            }
        ],
        geometry="geometry",
        crs=4326,
    ).to_file(through_traffic, driver="GeoJSON")
    config.source.observed_through_traffic = ObservedThroughTrafficConfig(
        path=through_traffic,
        source_id="retained-core-test-observed-through-traffic",
        effective_date="2026-04-07",
        licence="Synthetic fixture",
    )

    augmented = snapshot(config, retain_core=True)
    manifest = json.loads((augmented / "snapshot.json").read_text(encoding="utf-8"))

    assert augmented != first
    assert {
        name: hashlib.sha256((augmented / name).read_bytes()).hexdigest() for name in core_hashes
    } == core_hashes
    assert (augmented / "elevation-evidence.geojson").exists()
    assert (augmented / "official-road-classification.geojson").exists()
    assert (augmented / "observed-through-traffic.geojson").exists()
    assert manifest["attribution"] == first_manifest["attribution"]
    assert manifest["evidence_sources"]["official_road_classification"]["attribution"] == (
        "retained-core-test-road-classification; Synthetic fixture"
    )
    assert manifest["evidence_sources"]["observed_through_traffic"]["attribution"] == (
        "retained-core-test-observed-through-traffic; Synthetic fixture"
    )
    assert snapshot(config, retain_core=True) == augmented


def test_retained_core_snapshot_can_be_validated_before_absent_only_promotion(
    tmp_path: Path,
) -> None:
    config = copied_config(tmp_path)
    first = snapshot(config)
    config.source.snapshot_id = "staged-retained-core"
    config.source.retained_core_source = RetainedCoreSourceConfig(
        snapshot_id=first.name,
        manifest_sha256=hashlib.sha256((first / "snapshot.json").read_bytes()).hexdigest(),
    )
    terrain = tmp_path / "staged-elevation.geojson"
    gpd.GeoDataFrame(
        [{"sample": "elevation", "height": 10, "geometry": Point(-2.5, 51.4)}],
        geometry="geometry",
        crs=4326,
    ).to_file(terrain, driver="GeoJSON")
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=terrain,
        source_id="staged-test-elevation",
        licence="Synthetic fixture",
        attribution="Synthetic fixture",
        elevation_field="height",
        identifier_field="sample",
    )

    staged = stage_retained_core_snapshot(config)
    target = config.source.snapshot_dir / config.source.snapshot_id

    assert staged.path.is_dir()
    assert staged.destination == target
    assert not target.exists()
    _validate_snapshot(staged.path)

    promoted = promote_staged_snapshot(staged)
    assert promoted == target
    assert target.is_dir()
    assert not staged.path.exists()


def test_ea_recovery_staging_accepts_only_the_exact_legacy_parent_and_seals_strict_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = copied_config(tmp_path)
    parent = snapshot(config)
    retained_routes = parent / EA_RETAINED_ROUTE_FILENAME
    retained_routes.write_bytes(
        b'{"type":"FeatureCollection","crs":{"type":"name","properties":'
        b'{"name":"urn:ogc:def:crs:EPSG::27700"}},"features":['
        b'{"type":"Feature","properties":{"feature_id":"legacy-route",'
        b'"access_point_source_id":NaN},"geometry":{"type":"LineString",'
        b'"coordinates":[[350000,150000],[350001,150001]]}}]}'
    )
    parent_manifest_path = parent / "snapshot.json"
    parent_manifest = json.loads(parent_manifest_path.read_text(encoding="utf-8"))
    parent_manifest["files"].append(EA_RETAINED_ROUTE_FILENAME)
    parent_manifest["file_sha256"][EA_RETAINED_ROUTE_FILENAME] = hashlib.sha256(
        retained_routes.read_bytes()
    ).hexdigest()
    parent_manifest_path.write_text(
        json.dumps(parent_manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    parent_manifest_sha256 = hashlib.sha256(parent_manifest_path.read_bytes()).hexdigest()
    monkeypatch.setattr(sources_module, "STREAMING_GEOJSON_THRESHOLD_BYTES", 1)
    monkeypatch.setattr(
        recovery_module,
        "LEGACY_NAN_PARENT_SNAPSHOT_ID",
        parent.name,
    )
    monkeypatch.setattr(
        recovery_module,
        "LEGACY_NAN_PARENT_MANIFEST_SHA256",
        parent_manifest_sha256,
    )
    monkeypatch.setattr(recovery_module, "LEGACY_NAN_EXPECTED_COUNT", 1)

    config.source.snapshot_id = "strict-recovered-v11"
    config.source.retained_core_source = RetainedCoreSourceConfig(
        snapshot_id=parent.name,
        manifest_sha256=parent_manifest_sha256,
    )
    terrain = tmp_path / "strict-recovered-elevation.geojson"
    gpd.GeoDataFrame(
        [{"sample": "elevation", "height": 10, "geometry": Point(-2.5, 51.4)}],
        geometry="geometry",
        crs=4326,
    ).to_file(terrain, driver="GeoJSON")
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=terrain,
        source_id="strict-recovered-test-elevation",
        licence="Synthetic fixture",
        attribution="Synthetic fixture",
        elevation_field="height",
        identifier_field="sample",
    )

    with pytest.raises(ValueError, match="invalid JSON value"):
        stage_retained_core_snapshot(config)

    staged = sources_module.stage_ea_recovery_snapshot(config)

    assert staged.destination == config.source.snapshot_dir / config.source.snapshot_id
    assert not staged.destination.exists()
    assert not (staged.path / EA_RETAINED_ROUTE_FILENAME).exists()
    _validate_snapshot(staged.path)
    staged_manifest = json.loads((staged.path / "snapshot.json").read_text(encoding="utf-8"))
    assert staged_manifest["retained_core_lineage"] == {
        "source_snapshot_id": parent.name,
        "source_manifest_sha256": parent_manifest_sha256,
    }


def test_staged_snapshot_promotion_never_replaces_an_existing_target(
    tmp_path: Path,
) -> None:
    config = copied_config(tmp_path)
    first = snapshot(config)
    config.source.snapshot_id = "staged-collision"
    config.source.retained_core_source = RetainedCoreSourceConfig(
        snapshot_id=first.name,
        manifest_sha256=hashlib.sha256((first / "snapshot.json").read_bytes()).hexdigest(),
    )
    terrain = tmp_path / "collision-elevation.geojson"
    gpd.GeoDataFrame(
        [{"sample": "elevation", "height": 10, "geometry": Point(-2.5, 51.4)}],
        geometry="geometry",
        crs=4326,
    ).to_file(terrain, driver="GeoJSON")
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=terrain,
        source_id="collision-test-elevation",
        licence="Synthetic fixture",
        attribution="Synthetic fixture",
        elevation_field="height",
        identifier_field="sample",
    )
    staged = stage_retained_core_snapshot(config)
    staged_bytes = (staged.path / "snapshot.json").read_bytes()
    target = staged.destination
    target.mkdir()
    (target / "attacker").write_text("do not replace", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        promote_staged_snapshot(staged)

    assert (target / "attacker").read_text(encoding="utf-8") == "do not replace"
    assert (staged.path / "snapshot.json").read_bytes() == staged_bytes


def _banes_style_generic_elevation_snapshot(tmp_path: Path) -> tuple[CouncilConfig, Path]:
    """Build a local-elevation snapshot matching B&NES's generic EA source contract."""
    config = copied_config(tmp_path)
    terrain = tmp_path / "banes-elevation.geojson"
    gpd.GeoDataFrame(
        [{"sample": "banes-elevation", "height": 10, "geometry": Point(-2.5, 51.4)}],
        geometry="geometry",
        crs=4326,
    ).to_file(terrain, driver="GeoJSON")
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=terrain,
        source_id="ea-lidar-composite-dtm-1m",
        effective_date="2023-02-08",
        licence="Open Government Licence v3.0",
        attribution="© Environment Agency copyright and/or database right 2022.",
        elevation_field="height",
        identifier_field="sample",
    )
    return config, snapshot(config)


def test_legacy_banes_generic_elevation_snapshot_loads_without_provenance_map(
    tmp_path: Path,
) -> None:
    config, snapshot_path = _banes_style_generic_elevation_snapshot(tmp_path)
    manifest_path = snapshot_path / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    elevation_bytes = (snapshot_path / ELEVATION_EVIDENCE_FILENAME).read_bytes()
    manifest.pop("provenance_file_sha256")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _validate_snapshot(snapshot_path)

    assert (snapshot_path / ELEVATION_EVIDENCE_FILENAME).read_bytes() == elevation_bytes
    assert list(load_snapshot(config)["elevation_evidence"]["source_id"]) == [
        "ea-lidar-composite-dtm-1m"
    ]


def test_legacy_generic_elevation_snapshot_rejects_resealed_tampering(tmp_path: Path) -> None:
    _config, snapshot_path = _banes_style_generic_elevation_snapshot(tmp_path)
    manifest_path = snapshot_path / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("provenance_file_sha256")
    evidence_path = snapshot_path / ELEVATION_EVIDENCE_FILENAME
    evidence_path.write_bytes(evidence_path.read_bytes() + b"\n")
    manifest["file_sha256"][ELEVATION_EVIDENCE_FILENAME] = hashlib.sha256(
        evidence_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="elevation evidence provenance mismatch"):
        _validate_snapshot(snapshot_path)


def test_legacy_generic_elevation_snapshot_rejects_present_partial_provenance(
    tmp_path: Path,
) -> None:
    _config, snapshot_path = _banes_style_generic_elevation_snapshot(tmp_path)
    manifest_path = snapshot_path / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provenance_file_sha256"] = {}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="elevation evidence provenance mismatch"):
        _validate_snapshot(snapshot_path)


def test_legacy_retained_core_elevation_snapshot_rejects_missing_provenance(tmp_path: Path) -> None:
    _config, snapshot_path = _banes_style_generic_elevation_snapshot(tmp_path)
    manifest_path = snapshot_path / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("provenance_file_sha256")
    manifest["retained_core_lineage"] = {
        "source_snapshot_id": "historical-core",
        "source_manifest_sha256": "a" * 64,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="elevation evidence provenance mismatch"):
        _validate_snapshot(snapshot_path)


def test_legacy_ea_fixed_point_snapshot_rejects_missing_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config, snapshot_path, _terrain_digest = _write_synthetic_ea_snapshot(tmp_path, monkeypatch)
    manifest_path = snapshot_path / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("provenance_file_sha256")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="elevation evidence provenance mismatch"):
        _validate_snapshot(snapshot_path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest, _path: manifest["provenance_file_sha256"].pop(
                ELEVATION_EVIDENCE_FILENAME
            ),
            "elevation evidence provenance mismatch",
        ),
        (
            lambda manifest, _path: manifest["provenance_file_sha256"].__setitem__(
                ELEVATION_EVIDENCE_FILENAME, "0" * 64
            ),
            "elevation evidence provenance mismatch",
        ),
        (
            lambda manifest, path: _tamper_elevation_evidence_provenance(manifest, path),
            "elevation evidence provenance mismatch",
        ),
    ],
)
def test_snapshot_rejects_missing_mismatched_or_tampered_elevation_evidence_provenance(
    tmp_path: Path, mutate: object, message: str
) -> None:
    config = copied_config(tmp_path)
    snapshot_path = snapshot(config)
    manifest_path = snapshot_path / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert callable(mutate)
    mutate(manifest, snapshot_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        _validate_snapshot(snapshot_path)


def _tamper_elevation_evidence_provenance(manifest: dict[str, object], snapshot_path: Path) -> None:
    """Self-reseal ordinary hashes while retaining the immutable content claim."""
    evidence_path = snapshot_path / ELEVATION_EVIDENCE_FILENAME
    evidence_path.write_bytes(evidence_path.read_bytes() + b"\n")
    digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    file_hashes = manifest["file_sha256"]
    provenance_hashes = manifest["provenance_file_sha256"]
    assert isinstance(file_hashes, dict)
    assert isinstance(provenance_hashes, dict)
    file_hashes[ELEVATION_EVIDENCE_FILENAME] = digest
    provenance_hashes[ELEVATION_EVIDENCE_FILENAME] = digest


def test_fixed_point_uses_retained_snapshot_identity_even_when_config_changes(
    tmp_path: Path,
) -> None:
    config = copied_config(tmp_path)
    _final_ea_config(config, tmp_path)
    network = tmp_path / "network.geojson"
    _final_eligible_network(network)
    _write_final_ea_snapshot(config, pre_elevation_network_sha256="0" * 64)

    with pytest.raises(ValueError, match="two-pass fixed point failed"):
        _validate_ea_elevation_fixed_point(config, network)


def test_final_ea_fixed_point_allows_complete_immutable_snapshot(tmp_path: Path) -> None:
    config = _final_ea_config(copied_config(tmp_path), tmp_path)
    network = tmp_path / "network.geojson"
    fingerprint = _final_eligible_network(network)
    _write_final_ea_snapshot(config, pre_elevation_network_sha256=fingerprint)

    _validate_ea_elevation_fixed_point(config, network)


def test_ea_fixed_point_mismatch_retains_candidate_with_exact_status_and_keeps_output(
    tmp_path: Path,
) -> None:
    config = copied_config(tmp_path)
    config.publication.output_dir = tmp_path / "published"
    snapshot(config)
    compile(
        config,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )
    published_bytes = {
        path.relative_to(config.publication.output_dir): path.read_bytes()
        for path in config.publication.output_dir.rglob("*")
        if path.is_file()
    }
    compiled = _publication_compiled(config)
    compiled.governed_input_fingerprint = "c" * 64
    _final_ea_config(config, tmp_path)
    expected = "0" * 64
    _write_final_ea_snapshot(config, pre_elevation_network_sha256=expected)

    with pytest.raises(ValueError, match=rf"expected={expected}.*retained candidate="):
        publish(
            config,
            compiled,
            "run-ea-candidate",
            publication_authority=publication_destination_authority(workspace_root=tmp_path),
        )

    candidate = _ea_fixed_point_candidate_path(config)
    retained_network = candidate / EA_FIXED_POINT_CANDIDATE_NETWORK
    status = json.loads((candidate / EA_FIXED_POINT_CANDIDATE_STATUS).read_text())
    actual = eligible_route_fingerprint(gpd.read_file(retained_network))
    assert {
        path.relative_to(config.publication.output_dir): path.read_bytes()
        for path in config.publication.output_dir.rglob("*")
        if path.is_file()
    } == published_bytes
    assert status == {
        "actual_eligible_route_fingerprint": actual,
        "area_id": config.area_id,
        "candidate_network_path": EA_FIXED_POINT_CANDIDATE_NETWORK,
        "candidate_network_sha256": hashlib.sha256(retained_network.read_bytes()).hexdigest(),
        "expected_eligible_route_fingerprint": expected,
        "governed_input_fingerprint": "c" * 64,
        "next_step_reason": status["next_step_reason"],
        "next_step_status": "survey-index-repin-required",
        "run_id": "run-ea-candidate",
        "schema_version": "ea-fixed-point-candidate/v1",
        "snapshot_id": config.source.snapshot_id,
        "status": "eligible-route-mismatch",
        "timestamp": status["timestamp"],
    }
    assert (
        status["next_step_reason"] == "candidate-extent-or-request-differs-from-pinned-survey-index"
    )
    assert "next_step_command" not in status


def test_ea_fixed_point_candidate_advertises_only_pinned_weca_request_with_current_fingerprint(
    tmp_path: Path,
) -> None:
    config = _final_ea_config(copied_config(tmp_path), tmp_path)
    config.publication.output_dir = tmp_path / "published"
    compiled = _publication_compiled(config)
    compiled.governed_input_fingerprint = "c" * 64
    snapshot_path = _write_final_ea_snapshot(config, pre_elevation_network_sha256="0" * 64)
    isolated_snapshot = tmp_path / "isolated-checkout" / "snapshots" / config.source.snapshot_id
    isolated_snapshot.parent.mkdir(parents=True)
    snapshot_path.replace(isolated_snapshot)
    config.source.snapshot_dir = isolated_snapshot.parent
    status = _retain_candidate_status(
        config,
        _pinned_weca_candidate(tmp_path / "pinned-candidate.geojson"),
        run_id="run-pinned-candidate",
    )

    assert status["governed_input_fingerprint"] == compiled.governed_input_fingerprint
    assert status["next_step_status"] == "ea-acquisition-ready"
    command = status["next_step_command"]
    assert "--weca-preflight" in command
    assert f"--governed-input-fingerprint {'c' * 64}" in command
    assert "b" * 64 not in command
    command_parts = shlex.split(command)
    for option, filename in (
        ("--authority-boundaries", "ea-authority-boundaries.geojson"),
        ("--survey-index", "ea-survey-index.geojson"),
    ):
        command_path = Path(command_parts[command_parts.index(option) + 1])
        assert command_path == isolated_snapshot / filename
        assert command_path.is_file()


@pytest.mark.parametrize("mutation", ["unhashed", "escaping"])
def test_ea_fixed_point_candidate_refuses_invalid_snapshot_replay_inputs(
    tmp_path: Path,
    mutation: str,
) -> None:
    config = _final_ea_config(copied_config(tmp_path), tmp_path)
    config.publication.output_dir = tmp_path / "published"
    snapshot_path = _write_final_ea_snapshot(config, pre_elevation_network_sha256="0" * 64)
    manifest_path = snapshot_path / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "unhashed":
        manifest["file_sha256"].pop("ea-survey-index.geojson")
    else:
        acquisition_path = snapshot_path / "elevation-evidence.manifest.json"
        acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
        acquisition["authority_boundaries_path"] = "../authorities.geojson"
        acquisition_path.write_text(
            json.dumps(acquisition, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256(acquisition_path.read_bytes()).hexdigest()
        manifest["provenance_file_sha256"][acquisition_path.name] = digest
        manifest["evidence_sources"]["elevation"]["ea_acquisition_manifest_sha256"] = digest
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    status = _retain_candidate_status(
        config,
        _pinned_weca_candidate(tmp_path / "pinned-candidate.geojson"),
        run_id="run-invalid-replay-inputs",
    )

    assert status["next_step_status"] == "survey-index-repin-required"
    assert status["next_step_reason"] == "candidate-snapshot-replay-inputs-invalid"
    assert "next_step_command" not in status


def test_legacy_ea_snapshot_remains_usable_with_explicit_replay_diagnostic(
    tmp_path: Path,
) -> None:
    config = _final_ea_config(copied_config(tmp_path), tmp_path)
    config.publication.output_dir = tmp_path / "published"
    final_network = tmp_path / "final-network.geojson"
    fingerprint = _final_eligible_network(final_network)
    snapshot_path = _write_final_ea_snapshot(
        config,
        pre_elevation_network_sha256=fingerprint,
    )
    manifest_path = snapshot_path / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evidence_sources"]["elevation"].pop("replay_inputs")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _validate_ea_elevation_fixed_point(config, final_network)

    status = _retain_candidate_status(
        config,
        _pinned_weca_candidate(tmp_path / "pinned-candidate.geojson"),
        run_id="run-legacy-replay",
    )

    assert status["next_step_status"] == "survey-index-repin-required"
    assert status["next_step_reason"] == "legacy-snapshot-not-self-contained"


def test_bootstrap_publication_sharing_output_parent_preserves_ea_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = _final_ea_config(copied_config(tmp_path), tmp_path)
    final.publication.output_dir = tmp_path / "published"
    final_candidate_network = tmp_path / "candidate.geojson"
    _final_eligible_network(final_candidate_network)
    _write_final_ea_snapshot(final, pre_elevation_network_sha256="0" * 64)
    _retain_ea_fixed_point_candidate(
        final,
        run_id="run-final-candidate",
        network_path=final_candidate_network,
        expected="0" * 64,
        actual=eligible_route_fingerprint(gpd.read_file(final_candidate_network)),
        governed_input_fingerprint="c" * 64,
    )
    candidate = _ea_fixed_point_candidate_path(final)
    retained_status = (candidate / EA_FIXED_POINT_CANDIDATE_STATUS).read_bytes()

    bootstrap = CouncilConfig.from_yaml(final.config_path)
    bootstrap.publication.output_dir = final.publication.output_dir
    monkeypatch.setattr("satn.publisher._validate_artifacts", lambda *_args: None)

    publish(
        bootstrap,
        _publication_compiled(bootstrap),
        "run-bootstrap-success",
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )

    assert (candidate / EA_FIXED_POINT_CANDIDATE_STATUS).read_bytes() == retained_status


def test_ea_fixed_point_candidate_replaces_stale_contents_and_stays_contained(
    tmp_path: Path,
) -> None:
    config = _final_ea_config(copied_config(tmp_path), tmp_path)
    config.council_id = "../outside-publication"
    config.publication.output_dir = tmp_path / "published"
    network = tmp_path / "candidate.geojson"
    _final_eligible_network(network)
    _write_final_ea_snapshot(config, pre_elevation_network_sha256="0" * 64)

    candidate = _retain_ea_fixed_point_candidate(
        config,
        run_id="run-first",
        network_path=network,
        expected="1" * 64,
        actual="2" * 64,
        governed_input_fingerprint="c" * 64,
    )
    (candidate / "stale.txt").write_text("remove me", encoding="utf-8")
    network.write_bytes(network.read_bytes() + b"\n")
    _retain_ea_fixed_point_candidate(
        config,
        run_id="run-second",
        network_path=network,
        expected="3" * 64,
        actual="4" * 64,
        governed_input_fingerprint="c" * 64,
    )

    status = json.loads((candidate / EA_FIXED_POINT_CANDIDATE_STATUS).read_text())
    assert {path.name for path in candidate.iterdir()} == {
        EA_FIXED_POINT_CANDIDATE_NETWORK,
        EA_FIXED_POINT_CANDIDATE_STATUS,
    }
    assert status["run_id"] == "run-second"
    assert status["expected_eligible_route_fingerprint"] == "3" * 64
    assert status["actual_eligible_route_fingerprint"] == "4" * 64
    assert status["candidate_network_sha256"] == hashlib.sha256(network.read_bytes()).hexdigest()
    assert candidate.parent.name == EA_FIXED_POINT_CANDIDATE_DIRECTORY
    assert candidate.parent.parent == config.publication.output_dir.parent.resolve()
    with pytest.raises(ValueError):
        candidate.relative_to(config.publication.output_dir)


def test_successful_publication_clears_stale_ea_fixed_point_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _final_ea_config(copied_config(tmp_path), tmp_path)
    config.publication.output_dir = tmp_path / "published"
    compiled = _publication_compiled(config)
    compiled.governed_input_fingerprint = "c" * 64
    rendered_network = tmp_path / "rendered-network.geojson"
    _write_geojson(rendered_network, compiled)
    expected = eligible_route_fingerprint(gpd.read_file(rendered_network))
    _write_final_ea_snapshot(config, pre_elevation_network_sha256=expected)
    _retain_ea_fixed_point_candidate(
        config,
        run_id="run-stale",
        network_path=rendered_network,
        expected="0" * 64,
        actual=expected,
        governed_input_fingerprint=compiled.governed_input_fingerprint,
    )
    monkeypatch.setattr("satn.publisher._validate_artifacts", lambda *_args: None)

    publish(
        config,
        compiled,
        "run-fixed-point-success",
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )

    assert not _ea_fixed_point_candidate_path(config).exists()


def test_ea_fixed_point_does_not_retain_candidates_for_unrelated_failure(tmp_path: Path) -> None:
    config = _final_ea_config(copied_config(tmp_path), tmp_path)
    config.publication.output_dir = tmp_path / "published"
    compiled = _publication_compiled(config)

    with pytest.raises(ValueError, match=r"missing immutable snapshot\.json"):
        publish(
            config,
            compiled,
            "run-unrelated-failure",
            publication_authority=publication_destination_authority(workspace_root=tmp_path),
        )

    candidate_root = config.publication.output_dir.parent / EA_FIXED_POINT_CANDIDATE_DIRECTORY
    assert not candidate_root.exists()


def test_banes_ea_source_does_not_use_the_weca_candidate_protocol(tmp_path: Path) -> None:
    config = CouncilConfig.from_yaml(PROJECT / "config" / "banes.yaml")
    config.publication.output_dir = tmp_path / "published"
    network = tmp_path / "network.geojson"
    _final_eligible_network(network)

    _validate_ea_elevation_fixed_point(config, network)

    assert not (config.publication.output_dir.parent / EA_FIXED_POINT_CANDIDATE_DIRECTORY).exists()


def test_final_ea_fixed_point_rejects_bootstrap_snapshot(tmp_path: Path) -> None:
    config = copied_config(tmp_path)
    snapshot(config)
    _final_ea_config(config, tmp_path)
    network = tmp_path / "network.geojson"
    _final_eligible_network(network)

    with pytest.raises(ValueError, match="cannot read immutable snapshot provenance"):
        _validate_ea_elevation_fixed_point(config, network)


def test_final_ea_fixed_point_rejects_missing_snapshot(tmp_path: Path) -> None:
    config = _final_ea_config(copied_config(tmp_path), tmp_path)
    network = tmp_path / "network.geojson"
    _final_eligible_network(network)

    with pytest.raises(ValueError, match=r"missing immutable snapshot\.json"):
        _validate_ea_elevation_fixed_point(config, network)


def test_stale_ea_fixed_point_disables_whole_publication_reuse(tmp_path: Path) -> None:
    """The current validation contract must reject a stale final EA publication."""
    config = copied_config(tmp_path)
    snapshot(config)
    first = compile(config)
    config = _final_ea_config(config, tmp_path)
    _write_final_ea_snapshot(config, pre_elevation_network_sha256="0" * 64)
    manifest = compilation_dependency_manifest()
    governed_input = compilation_governed_input_fingerprint(config, dependency_manifest=manifest)
    run_path = first.artifacts["run"]
    run = json.loads(run_path.read_text(encoding="utf-8"))
    input_ledger = canonical_decision_ledger_payload(run["decision_ledger_input"])
    input_fingerprint = decision_ledger_input_fingerprint(governed_input, input_ledger)
    run["governed_input_fingerprint"] = governed_input
    run["compilation_input_fingerprint"] = input_fingerprint
    run["compilation_dependency_manifest"] = manifest
    run_path.write_text(json.dumps(run), encoding="utf-8")

    with pytest.raises(ValueError, match="two-pass fixed point failed"):
        validate_publication(config.publication.output_dir, config)
    assert _reuse_validated_publication(config, governed_input, input_fingerprint, manifest) is None


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest: manifest.pop("evidence_sources"),
            "cannot read immutable snapshot provenance",
        ),
        (
            lambda manifest: manifest["evidence_sources"].pop("elevation"),
            "cannot read immutable snapshot provenance",
        ),
        (
            lambda manifest: manifest["evidence_sources"].__setitem__("elevation", []),
            "cannot read immutable snapshot provenance",
        ),
        (
            lambda manifest: manifest["evidence_sources"]["elevation"].__setitem__(
                "acquisition_protocol", "one-pass/v1"
            ),
            "lacks the two-pass fixed-point protocol",
        ),
        (
            lambda manifest: manifest["evidence_sources"]["elevation"].__setitem__(
                "pre_elevation_network_sha256", "0" * 64
            ),
            "retained acquisition manifest mismatches pre-elevation route fingerprint",
        ),
        (
            lambda manifest: manifest["evidence_sources"]["elevation"].pop("sample_ledger_sha256"),
            "cannot read immutable snapshot provenance",
        ),
        (
            lambda manifest: manifest["provenance_file_sha256"].pop(SAMPLE_LEDGER_FILENAME),
            "cannot read immutable snapshot provenance",
        ),
        (
            lambda manifest: manifest["provenance_file_sha256"].pop(EA_RETAINED_ROUTE_FILENAME),
            "cannot read immutable snapshot provenance",
        ),
        (
            lambda manifest: manifest["provenance_file_sha256"].pop(ELEVATION_EVIDENCE_FILENAME),
            "cannot read immutable snapshot provenance",
        ),
        (
            lambda manifest: manifest["provenance_file_sha256"].__setitem__(
                ELEVATION_EVIDENCE_FILENAME, []
            ),
            "invalid elevation-evidence.geojson provenance",
        ),
    ],
)
def test_final_ea_fixed_point_rejects_missing_or_malformed_provenance(
    tmp_path: Path, mutate: object, message: str
) -> None:
    config = _final_ea_config(copied_config(tmp_path), tmp_path)
    network = tmp_path / "network.geojson"
    fingerprint = _final_eligible_network(network)
    snapshot_path = _write_final_ea_snapshot(config, pre_elevation_network_sha256=fingerprint)
    manifest_path = snapshot_path / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert callable(mutate)
    mutate(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        _validate_ea_elevation_fixed_point(config, network)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda acquisition: acquisition.__setitem__("acquisition_protocol", "one-pass/v1"),
            "mismatches acquisition protocol",
        ),
        (
            lambda acquisition: acquisition.__setitem__("pre_elevation_network_sha256", "0" * 64),
            "mismatches pre-elevation route fingerprint",
        ),
        (
            lambda acquisition: acquisition.__setitem__("output_sha256", "0" * 64),
            "mismatches acquisition elevation output digest",
        ),
        (
            lambda acquisition: acquisition.__setitem__("sample_ledger_sha256", "0" * 64),
            "mismatches sample-ledger digest",
        ),
        (
            lambda acquisition: acquisition.__setitem__("sample_route_sha256", "0" * 64),
            "mismatches sampled-route digest",
        ),
        (
            lambda acquisition: acquisition.__setitem__("sample_ledger_path", "other-ledger.jsonl"),
            "mismatches sample-ledger path",
        ),
        (
            lambda acquisition: acquisition.__setitem__(
                "sample_route_path", "other-routes.geojson"
            ),
            "mismatches sampled-route path",
        ),
    ],
)
def test_final_ea_fixed_point_rejects_retained_manifest_mismatch_with_valid_hashes(
    tmp_path: Path, mutate: object, message: str
) -> None:
    config = _final_ea_config(copied_config(tmp_path), tmp_path)
    network = tmp_path / "network.geojson"
    fingerprint = _final_eligible_network(network)
    snapshot_path = _write_final_ea_snapshot(config, pre_elevation_network_sha256=fingerprint)

    _rewrite_retained_ea_acquisition(snapshot_path, mutate)

    with pytest.raises(ValueError, match=message):
        _validate_ea_elevation_fixed_point(config, network)


def test_ea_acquisition_sidecar_tampering_fails_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = copied_config(tmp_path)
    terrain = tmp_path / "ea-samples.geojson"
    gpd.GeoDataFrame(
        [{"sample": "ea-1", "height": 10, "geometry": Point(-2.50, 51.40)}],
        geometry="geometry",
        crs=4326,
    ).to_file(terrain, driver="GeoJSON")
    index, official_index = _official_index(tmp_path)
    monkeypatch.setattr(
        "satn.sources.validate_official_weca_survey_index", lambda _path: official_index
    )
    terrain.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "source_id": "ea-lidar-composite-dtm-1m",
                "acquisition_protocol": "two-pass-fixed-point/v1",
                "contract_schema_version": "ea-lidar-composite-dtm-contract/v2",
                "dataset_id": EA_LIDAR_DATASET_ID,
                "coverage_id": EA_LIDAR_COVERAGE_ID,
                "endpoint": EA_LIDAR_ENDPOINT,
                "licence": "Open Government Licence v3.0",
                "attribution": DTM_ATTRIBUTION,
                "dataset_title": "LIDAR Composite Digital Terrain Model (DTM) - 1m",
                "source_resolution_m": 1,
                "output_sample_spacing_m": 10,
                "vertical_accuracy": "+/-15cm RMSE",
                "effective_survey_date": "2022-01-02",
                "output_sha256": "0" * 64,
                "pre_elevation_network_sha256": "a" * 64,
                "requested_point_count": 1,
                "governed_input_fingerprint": "a" * 64,
                "authority_boundaries_path": "authorities.geojson",
                "evidence_sample_count": 1,
                "survey_coverage_preflight": {
                    "status": "available",
                    "official_survey_index": official_index,
                    "authority_boundaries": {
                        "raw_sha256": hashlib.sha256(
                            (tmp_path / "authorities.geojson").read_bytes()
                        ).hexdigest()
                    },
                },
                "sample_validation": {
                    "status": "available",
                    "authorities": [
                        {"available_sample_count": 1, "nodata_sample_count": 0},
                        {"available_sample_count": 0, "nodata_sample_count": 0},
                        {"available_sample_count": 0, "nodata_sample_count": 0},
                        {"available_sample_count": 0, "nodata_sample_count": 0},
                        {"available_sample_count": 0, "nodata_sample_count": 0},
                    ],
                },
                "survey_index_path": index.name,
                "survey_index_sha256": official_index["raw_sha256"],
                "survey_index_feature_sha256": official_index["canonical_feature_sha256"],
            }
        ),
        encoding="utf-8",
    )
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=terrain,
        source_id="ea-lidar-composite-dtm-1m",
        acquisition_contract="ea-lidar-weca-v1",
        effective_date="2023-02-08",
        licence="Open Government Licence v3.0",
        attribution=DTM_ATTRIBUTION,
        elevation_field="height",
        identifier_field="sample",
    )

    with pytest.raises(ValueError, match="does not bind its output"):
        snapshot(config)


def test_remote_national_elevation_request_uses_governed_bbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = copied_config(tmp_path)
    config.source.external_buffer_km = 0.1
    config.source.national_elevation = NationalElevationConfig(
        provider="remote-geojson",
        url="https://terrain.example.test/samples",
        source_id="national-remote-dtm",
        licence="Open Government Licence v3.0",
        attribution="Remote national terrain",
        elevation_field="height",
        identifier_field="sample",
    )
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"sample": "remote-1", "height": 15},
                "geometry": {"type": "Point", "coordinates": [-2.49, 51.40]},
            }
        ],
    }
    seen: dict[str, str] = {}

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(payload).encode()

    def fake_urlopen(request: object, timeout: int) -> Response:
        seen["url"] = request.full_url  # type: ignore[attr-defined]
        assert timeout == 90
        return Response()

    monkeypatch.setattr(sources_module, "open_configured_https", fake_urlopen)

    path = snapshot(config)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(seen["url"]).query)
    assert "bbox" in query
    assert len(query["bbox"][0].split(",")) == 4
    manifest = json.loads((path / "snapshot.json").read_text())
    assert manifest["evidence_sources"]["elevation"]["date_kind"] == "retrieved"
    assert manifest["evidence_sources"]["elevation"]["sample_count"] == 1


def test_configured_source_without_local_coverage_is_explicit_unknown(
    tmp_path: Path,
) -> None:
    config = copied_config(tmp_path)
    terrain = tmp_path / "outside-terrain.geojson"
    gpd.GeoDataFrame(
        [{"height": 30, "geometry": Point(-1.0, 52.0)}],
        geometry="geometry",
        crs=4326,
    ).to_file(terrain, driver="GeoJSON")
    config.source.external_buffer_km = 0.1
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=terrain,
        source_id="national-dtm-no-local-coverage",
        effective_date="2026-01-15",
        licence="Open Government Licence v3.0",
        attribution="National terrain test source",
        elevation_field="height",
    )

    path = snapshot(config)
    result = compile(config)
    manifest = json.loads((path / "snapshot.json").read_text())
    profiles = gpd.read_file(result.artifacts["geopackage"], layer="topography_profiles")

    assert manifest["evidence_sources"]["elevation"]["coverage_status"] == ("explicit-unknown")
    assert manifest["evidence_sources"]["elevation"]["sample_count"] == 0
    assert result.metadata["elevation_evidence_status"] == "explicit-unknown"
    assert set(profiles["evidence_status"]) == {"evidence-unavailable"}


def test_empty_remote_coverage_is_snapshotted_as_explicit_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = copied_config(tmp_path)
    config.source.national_elevation = NationalElevationConfig(
        provider="remote-geojson",
        url="https://terrain.example.test/empty",
        source_id="national-empty-remote",
        licence="Open Government Licence v3.0",
        attribution="Empty remote terrain fixture",
    )

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"type":"FeatureCollection","features":[]}'

    def fake_urlopen(_request: object, timeout: int) -> Response:
        assert timeout == 90
        return Response()

    monkeypatch.setattr(sources_module, "open_configured_https", fake_urlopen)

    path = snapshot(config)
    result = compile(config)
    manifest = json.loads((path / "snapshot.json").read_text())

    assert manifest["evidence_sources"]["elevation"]["coverage_status"] == ("explicit-unknown")
    assert result.metadata["elevation_evidence_status"] == "explicit-unknown"


def test_duplicate_governed_terrain_identifiers_are_rejected(tmp_path: Path) -> None:
    config = copied_config(tmp_path)
    terrain = tmp_path / "duplicate-terrain.geojson"
    gpd.GeoDataFrame(
        [
            {"sample": "duplicate", "height": 10, "geometry": Point(-2.50, 51.40)},
            {"sample": "duplicate", "height": 20, "geometry": Point(-2.48, 51.41)},
        ],
        geometry="geometry",
        crs=4326,
    ).to_file(terrain, driver="GeoJSON")
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=terrain,
        source_id="duplicate-terrain",
        effective_date="2026-01-15",
        licence="Synthetic fixture",
        attribution="Duplicate identifier fixture",
        elevation_field="height",
        identifier_field="sample",
    )

    with pytest.raises(ValueError, match="duplicate sample identifiers"):
        snapshot(config)


@pytest.mark.parametrize("height", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_governed_terrain_heights_are_rejected(
    tmp_path: Path, height: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = copied_config(tmp_path)
    terrain = tmp_path / "non-finite-terrain.geojson"
    source = gpd.GeoDataFrame(
        [{"sample": "invalid-height", "height": height, "geometry": Point(-2.50, 51.40)}],
        geometry="geometry",
        crs=4326,
    )
    terrain.touch()
    original_read_file = gpd.read_file

    def read_file_with_non_finite_source(
        path: object, *args: object, **kwargs: object
    ) -> gpd.GeoDataFrame:
        if Path(path) == terrain:
            return source.copy()
        return original_read_file(path, *args, **kwargs)

    monkeypatch.setattr("satn.sources.gpd.read_file", read_file_with_non_finite_source)
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=terrain,
        source_id="non-finite-terrain",
        effective_date="2026-01-15",
        licence="Synthetic fixture",
        attribution="Non-finite height fixture",
        elevation_field="height",
        identifier_field="sample",
    )

    with pytest.raises(ValueError, match="unusable heights"):
        snapshot(config)


def test_null_governed_terrain_identifier_uses_geometry_fallback(tmp_path: Path) -> None:
    config = copied_config(tmp_path)
    terrain = tmp_path / "null-identifier-terrain.geojson"
    gpd.GeoDataFrame(
        [
            {"sample": 101, "height": 10, "geometry": Point(-2.50, 51.40)},
            {"sample": None, "height": 20, "geometry": Point(-2.48, 51.41)},
        ],
        geometry="geometry",
        crs=4326,
    ).to_file(terrain, driver="GeoJSON")
    config.source.national_elevation = NationalElevationConfig(
        provider="local-geojson",
        path=terrain,
        source_id="null-identifier-terrain",
        effective_date="2026-01-15",
        licence="Synthetic fixture",
        attribution="Null identifier fixture",
        elevation_field="height",
        identifier_field="sample",
    )

    snapshot(config)
    loaded = load_snapshot(config)["elevation_evidence"]

    assert "nan" not in set(loaded["evidence_id"])
    assert "<NA>" not in set(loaded["evidence_id"])
    assert len(set(loaded["evidence_id"])) == 2


def test_segmented_osm_way_corroboration_ids_are_unique_and_stable() -> None:
    rows = [
        {
            "osmid": "shared-way",
            "ele": "100",
            "incline": "5%",
            "geometry": LineString([(0, 0), (1, 0)]),
        },
        {
            "osmid": "shared-way",
            "ele": "110",
            "incline": "6%",
            "geometry": LineString([(1, 0), (2, 0)]),
        },
    ]
    network = gpd.GeoDataFrame(rows, geometry="geometry", crs=27700)

    first = _osm_elevation_corroboration(network)
    reversed_result = _osm_elevation_corroboration(network.iloc[::-1])

    assert first["corroboration_id"].is_unique
    assert set(first["corroboration_id"]) == set(reversed_result["corroboration_id"])
    assert set(first["source_id"]) == {"shared-way"}


def test_sparse_osm_height_tags_never_replace_missing_national_elevation(
    tmp_path: Path,
) -> None:
    config = copied_config(tmp_path)
    config.source.national_elevation = None
    evidence_path = config.source.fixture_dir / "elevation-evidence.geojson"
    evidence_path.unlink()
    network_path = config.source.fixture_dir / "network.geojson"
    network = gpd.read_file(network_path)
    network["ele"] = "150"
    network["incline"] = "12%"
    network.to_file(network_path, driver="GeoJSON")
    snapshot(config)

    result = compile(config)
    profiles = gpd.read_file(result.artifacts["geopackage"], layer="topography_profiles")
    corroboration = gpd.read_file(result.artifacts["geopackage"], layer="elevation_corroboration")

    assert set(profiles["evidence_status"]) == {"evidence-unavailable"}
    assert result.metadata["elevation_evidence_status"] == "explicit-unknown"
    assert result.metadata["elevation_corroboration_count"] == len(network)
    assert set(corroboration["evidence_role"]) == {"corroborating-only"}


@pytest.mark.live_terrain
def test_live_national_elevation_acquisition_is_explicitly_opt_in(tmp_path: Path) -> None:
    url = os.environ.get("SATN_TEST_TERRAIN_GEOJSON_URL")
    if not url:
        pytest.skip("set SATN_TEST_TERRAIN_GEOJSON_URL for the opt-in live terrain test")
    config = copied_config(tmp_path)
    config.source.national_elevation = NationalElevationConfig(
        provider="remote-geojson",
        url=url,
        source_id="live-national-terrain",
        licence="Configured by SATN_TEST_TERRAIN_GEOJSON_URL",
        attribution="Configured live terrain test source",
    )

    path = snapshot(config)

    assert (path / "elevation-evidence.geojson").exists()
