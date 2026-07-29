from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from test_backbone_assembly import config as backbone_config

from satn.evidence_contracts import EvidencePartitionKey, IngestionContract, SourceExport
from satn.evidence_store_acceptance import accept_store_query
from satn.local_evidence_store import provision_spatial_runtime
from satn.open_roads_adapter import ATTRIBUTES, SOURCE_LAYER, contract_payload
from satn.sources import load_snapshot


def _write_frame(path: Path, rows: list[dict[str, object]]) -> None:
    gpd.GeoDataFrame(rows, geometry="geometry", crs=27700).to_file(
        path,
        driver="GeoJSON",
        index=False,
    )


def _compact_snapshot(
    tmp_path: Path,
    *,
    content_fingerprint: str,
) -> tuple[object, gpd.GeoDataFrame]:
    snapshot = tmp_path / "snapshots" / "equivalence"
    snapshot.mkdir(parents=True)
    _write_frame(
        snapshot / "boundary.geojson",
        [
            {
                "geometry": Polygon(
                    [
                        (349_000, 169_000),
                        (372_000, 169_000),
                        (372_000, 182_000),
                        (349_000, 182_000),
                    ]
                )
            }
        ],
    )
    _write_frame(
        snapshot / "places.geojson",
        [
            {
                "place_id": "west",
                "name": "West",
                "kind": "community",
                "place_class": "town",
                "geometry": Point(355_000, 175_000),
            }
        ],
    )
    _write_frame(
        snapshot / "network.geojson",
        [
            {
                "osmid": "street",
                "highway": "residential",
                "geometry": LineString(
                    [(355_000, 175_000), (356_000, 175_000)]
                ),
            }
        ],
    )
    _write_frame(
        snapshot / "official-road-classification.geojson",
        [
            {
                "official_feature_id": "official-overndale",
                "official_classification": "classified-unnumbered",
                "official_road_number": None,
                "official_road_name": "Overndale Road",
                "official_road_function": "Local Road",
                "source_id": "os-open-roads-2026-04-07",
                "effective_date": "2026-04-07",
                "licence": "Open Government Licence v3.0",
                "content_fingerprint": content_fingerprint,
                "geometry": LineString(
                    [(355_000, 175_000), (356_000, 175_000)]
                ),
            }
        ],
    )
    files = (
        "boundary.geojson",
        "places.geojson",
        "network.geojson",
        "official-road-classification.geojson",
    )
    (snapshot / "snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "snapshot_id": "equivalence",
                "source_kind": "fixture",
                "files": list(files),
                "file_sha256": {
                    name: hashlib.sha256((snapshot / name).read_bytes()).hexdigest()
                    for name in files
                },
            }
        ),
        encoding="utf-8",
    )
    definition = backbone_config()
    definition.source = definition.source.model_copy(
        update={
            "snapshot_dir": snapshot.parent,
            "snapshot_id": snapshot.name,
        }
    )
    oracle = load_snapshot(definition)["official_road_classification"]
    return definition, oracle


def test_real_store_query_matches_independent_load_snapshot_oracle(
    tmp_path: Path,
) -> None:
    runtime_lock_setting = os.environ.get("SATN_TEST_DUCKDB_SPATIAL_RUNTIME_LOCK")
    extension_setting = os.environ.get("SATN_TEST_DUCKDB_SPATIAL_EXTENSION")
    if runtime_lock_setting is None or extension_setting is None:
        pytest.skip("requires the explicitly configured pinned DuckDB Spatial runtime")
    runtime_lock = Path(runtime_lock_setting)
    extension_archive = Path(extension_setting)
    extension_cache = tmp_path / "extensions"
    provision_spatial_runtime(
        runtime_lock_path=runtime_lock,
        extension_archive=extension_archive,
        extension_cache=extension_cache,
    )
    source_path = tmp_path / "fixture-open-roads.geojson"
    _write_frame(
        source_path,
        [
            {
                "id": "official-overndale",
                "road_classification": "Classified Unnumbered",
                "road_function": "Local Road",
                "road_classification_number": None,
                "name_1": "Overndale Road",
                "geometry": LineString(
                    [(355_000, 175_000), (356_000, 175_000)]
                ),
            }
        ],
    )
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    _, oracle = _compact_snapshot(
        tmp_path,
        content_fingerprint=source_sha256,
    )
    source_export = SourceExport(
        source_family="os-open-roads",
        dataset="open-roads",
        layer="RoadLink",
        publisher_release="2026-04-07",
        effective_date="2026-04-07",
        licence="Open Government Licence v3.0",
        format="GeoJSON",
        declared_crs="EPSG:27700",
        raw_bytes_sha256=source_sha256,
        provenance={"retained_path": str(source_path.resolve())},
    )
    payload = contract_payload("EPSG:27700")
    payload.pop("contract")
    selector = MultiPolygon(
        [
            box(354_900, 174_900, 356_100, 175_100),
            box(365_100, 175_100, 365_200, 175_200),
        ]
    )

    report = accept_store_query(
        store_path=tmp_path / "evidence.duckdb",
        runtime_lock_path=runtime_lock,
        extension_cache=extension_cache,
        source_export=source_export,
        ingestion_contract=IngestionContract(**payload),
        partition_keys=(
            EvidencePartitionKey(SOURCE_LAYER, "bng-10km/v1", "ST57"),
            EvidencePartitionKey(SOURCE_LAYER, "bng-10km/v1", "ST67"),
        ),
        selector=selector,
        projection=tuple(ATTRIBUTES),
        oracle=oracle,
        source_id="os-open-roads-2026-04-07",
        expected_availability_counts={
            "available": 1,
            "no-data": 1,
            "explicit-unknown": 0,
        },
    )

    assert report.row_count == 1
    assert report.availability_counts == {
        "available": 1,
        "explicit-unknown": 0,
        "no-data": 1,
    }


def test_regional_acceptance_command_fails_when_configured_corpus_is_absent(
    tmp_path: Path,
) -> None:
    project = Path(__file__).parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "satn.evidence_store_acceptance",
            "--target",
            "weca-v10",
            "--data-root",
            str(tmp_path / "absent"),
        ],
        cwd=project,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert (
        "regional equivalence acceptance blocked: "
        "WECA v10 governed Open Roads export is absent"
    ) in result.stderr


def test_banes_regional_acceptance_fails_closed_for_pre_v1_source_schema(
    tmp_path: Path,
) -> None:
    project = Path(__file__).parents[1]
    data_root = tmp_path / "data"
    governed = data_root / "governed"
    snapshot = (
        data_root / "snapshots" / "banes-osm-open-roads-v1-2026-07-29"
    )
    governed.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    _write_frame(
        governed / "weca-os-open-roads-2026-04-07.geojson",
        [
            {
                "id": "road-with-legacy-schema",
                "road_classification": "A Road",
                "geometry": LineString(
                    [(355_000, 175_000), (356_000, 175_000)]
                ),
            }
        ],
    )
    (snapshot / "snapshot.json").write_text("{}", encoding="utf-8")
    (snapshot / "official-road-classification.geojson").write_text(
        "{}",
        encoding="utf-8",
    )
    (snapshot / "boundary.geojson").write_text("{}", encoding="utf-8")
    runtime_lock = tmp_path / "runtime-lock.json"
    runtime_lock.write_text("{}", encoding="utf-8")
    extension_archive = tmp_path / "spatial.duckdb_extension"
    extension_archive.write_bytes(b"not-needed-before-source-schema-validation")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "satn.evidence_store_acceptance",
            "--target",
            "banes",
            "--data-root",
            str(data_root),
            "--runtime-lock",
            str(runtime_lock),
            "--extension-archive",
            str(extension_archive),
            "--work-dir",
            str(tmp_path / "work"),
        ],
        cwd=project,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert (
        "regional equivalence acceptance blocked: "
        "banes governed Open Roads export is missing v1 fields: "
        "name_1, road_classification_number, road_function"
    ) in result.stderr


def test_regional_acceptance_fails_on_production_snapshot_loader_mismatch(
    tmp_path: Path,
) -> None:
    project = Path(__file__).parents[1]
    data_root = tmp_path / "data"
    governed = data_root / "governed"
    snapshot = (
        data_root
        / "snapshots"
        / "weca-classification-elevation-2026-07-28-v10"
    )
    governed.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    source_path = governed / "weca-os-open-roads-2026-04-07.geojson"
    _write_frame(
        source_path,
        [
            {
                "id": "official-overndale",
                "road_classification": "Classified Unnumbered",
                "road_function": "Local Road",
                "road_classification_number": None,
                "name_1": "Overndale Road",
                "geometry": LineString(
                    [(355_000, 175_000), (356_000, 175_000)]
                ),
            }
        ],
    )
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    _write_frame(
        snapshot / "boundary.geojson",
        [
            {
                "geometry": Polygon(
                    [
                        (354_000, 174_000),
                        (357_000, 174_000),
                        (357_000, 176_000),
                        (354_000, 176_000),
                    ]
                )
            }
        ],
    )
    _write_frame(
        snapshot / "places.geojson",
        [
            {
                "place_id": "west",
                "name": "West",
                "kind": "community",
                "place_class": "town",
                "geometry": Point(355_000, 175_000),
            }
        ],
    )
    _write_frame(
        snapshot / "network.geojson",
        [
            {
                "osmid": "street",
                "highway": "residential",
                "geometry": LineString(
                    [(355_000, 175_000), (356_000, 175_000)]
                ),
            }
        ],
    )
    _write_frame(
        snapshot / "official-road-classification.geojson",
        [
            {
                "official_feature_id": "official-overndale",
                "official_classification": "classified-unnumbered",
                "official_road_number": None,
                "official_road_name": "Overndale Road",
                "official_road_function": "Local Road",
                "source_id": "os-open-roads-2026-04-07",
                "effective_date": "2026-04-07",
                "licence": "Open Government Licence v3.0",
                "content_fingerprint": source_sha256,
                "geometry": LineString(
                    [(355_000, 175_000), (356_000, 175_000)]
                ),
            }
        ],
    )
    files = (
        "boundary.geojson",
        "places.geojson",
        "network.geojson",
        "official-road-classification.geojson",
    )
    (snapshot / "snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "snapshot_id": snapshot.name,
                "source_kind": "fixture",
                "files": list(files),
                "file_sha256": {
                    name: hashlib.sha256((snapshot / name).read_bytes()).hexdigest()
                    for name in files
                },
                "evidence_sources": {
                    "official_road_classification": {
                        "source_id": "os-open-roads-2026-04-07",
                        "effective_date": "2026-04-07",
                        "licence": "Open Government Licence v3.0",
                        "content_fingerprint": source_sha256,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (snapshot / "network.geojson").write_bytes(
        (snapshot / "network.geojson").read_bytes() + b"\n"
    )
    runtime_lock = tmp_path / "runtime-lock.json"
    runtime_lock.write_text("{}", encoding="utf-8")
    extension_archive = tmp_path / "spatial.duckdb_extension"
    extension_archive.write_bytes(b"not-reached-before-snapshot-loader-validation")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "satn.evidence_store_acceptance",
            "--target",
            "weca-v10",
            "--data-root",
            str(data_root),
            "--runtime-lock",
            str(runtime_lock),
            "--extension-archive",
            str(extension_archive),
            "--work-dir",
            str(tmp_path / "work"),
        ],
        cwd=project,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert (
        "regional equivalence acceptance failed: "
        "invalid snapshot: network.geojson content hash mismatch"
    ) in result.stderr
