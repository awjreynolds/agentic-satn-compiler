from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import geopandas as gpd
import yaml
from shapely.geometry import LineString, Polygon

PROJECT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_source_baseline_maps", PROJECT / "scripts" / "build_source_baseline_maps.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
build_source_baseline_maps = MODULE.build_source_baseline_maps


def _write_test_area(root: Path) -> Path:
    area = root / "area.yaml"
    snapshot = root / "snapshots" / "fixture-source-baseline"
    snapshot.mkdir(parents=True)

    boundary = gpd.GeoDataFrame(
        [
            {
                "boundary_id": "test-boundary",
                "geometry": Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
            }
        ],
        geometry="geometry",
        crs=4326,
    )
    boundary.to_file(snapshot / "boundary.geojson", driver="GeoJSON")

    network = gpd.GeoDataFrame(
        [
            {
                "source_id": "cycle-forward",
                "highway": "cycleway",
                "geometry": LineString([(0, 0), (10, 0)]),
            },
            {
                "source_id": "cycle-reverse",
                "highway": "cycleway",
                "geometry": LineString([(10, 0), (0, 0)]),
            },
            {
                "source_id": "cycle-parallel",
                "highway": "cycleway",
                "geometry": LineString([(0, 0.001), (10, 0.001)]),
            },
            {
                "source_id": "ncn-way",
                "highway": "secondary",
                "ncn": "yes",
                "geometry": LineString([(0, 1), (10, 1)]),
            },
            {
                "source_id": "proposed-cycle",
                "highway": "cycleway",
                "cycleway": "proposed",
                "geometry": LineString([(0, 2), (10, 2)]),
            },
            {
                "source_id": "bridleway-ncn",
                "highway": "bridleway",
                "designation": "permissive",
                "ncn": "yes",
                "geometry": LineString([(0, 8), (10, 8)]),
            },
            {
                "source_id": "bridleway-designation",
                "highway": "path",
                "designation": "public_bridleway",
                "geometry": LineString([(0, 8.5), (10, 8.5)]),
            },
            {
                "source_id": "active-railway",
                "railway": "rail",
                "geometry": LineString([(0, 9), (10, 9)]),
            },
            {
                "source_id": "abandoned-railway",
                "railway": "abandoned",
                "highway": "cycleway",
                "geometry": LineString([(0, 9.2), (10, 9.2)]),
            },
            {
                "source_id": "disused-railway-reverse",
                "railway": "disused",
                "geometry": LineString([(10, 9.2), (0, 9.2)]),
            },
            {
                "source_id": "dismantled-railway",
                "railway": "dismantled",
                "geometry": LineString([(0, 9.5), (10, 9.5)]),
            },
            {
                "source_id": "razed-railway",
                "railway": "razed",
                "geometry": LineString([(0, 9.7), (10, 9.7)]),
            },
        ],
        geometry="geometry",
        crs=4326,
    )
    network.to_file(snapshot / "network.geojson", driver="GeoJSON")

    context = gpd.GeoDataFrame(
        [
            {
                "evidence_id": "current-evidence",
                "feature_type": "ncn-route",
                "source_id": "current-source",
                "name": "Current route",
                "geometry": LineString([(0, 3), (10, 3)]),
            },
            {
                "evidence_id": "current-evidence-reverse",
                "feature_type": "ncn-route",
                "source_id": "current-source-reverse",
                "name": "Current route",
                "geometry": LineString([(10, 3), (0, 3)]),
            },
            {
                "evidence_id": "former-evidence",
                "feature_type": "declassified-ncn-route",
                "source_id": "former-source",
                "name": "Former route",
                "geometry": LineString([(0, 4), (10, 4)]),
            },
            {
                "evidence_id": "greenway-evidence",
                "feature_type": "greenway-cycleway",
                "source_id": "greenway-source",
                "name": "Greenway",
                "geometry": LineString([(0, 5), (10, 5)]),
            },
            {
                "evidence_id": "bridleway-evidence",
                "feature_type": "bridleway",
                "source_id": "context-bridleway",
                "name": "Context bridleway",
                "geometry": LineString([(0, 6.5), (10, 6.5)]),
            },
            {
                "evidence_id": "public-bridleway-evidence",
                "feature_type": "public-bridleway",
                "source_id": "context-public-bridleway",
                "name": "Context public bridleway",
                "geometry": LineString([(0, 6.8), (10, 6.8)]),
            },
            {
                "evidence_id": "former-railway-evidence",
                "feature_type": "former-railway",
                "source_id": "context-former-railway",
                "name": "Context former railway",
                "geometry": LineString([(0, 7.1), (10, 7.1)]),
            },
            {
                "evidence_id": "outside-evidence",
                "feature_type": "ncn-route",
                "source_id": "outside-source",
                "name": "Outside",
                "geometry": LineString([(20, 20), (21, 21)]),
            },
        ],
        geometry="geometry",
        crs=4326,
    )
    context.to_file(snapshot / "context.geojson", driver="GeoJSON")

    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "official-a1",
                "official_classification": "a-road",
                "official_road_number": "A1",
                "official_road_name": "Test A road",
                "source_id": "official-source",
                "geometry": LineString([(-1, 6), (5, 6)]),
            },
            {
                "official_feature_id": "official-b1",
                "official_classification": "b-road",
                "official_road_number": "B1",
                "official_road_name": "Excluded B road",
                "source_id": "official-source",
                "geometry": LineString([(0, 7), (10, 7)]),
            },
        ],
        geometry="geometry",
        crs=4326,
    )
    official.to_file(snapshot / "official-road-classification.geojson", driver="GeoJSON")
    (snapshot / "snapshot.json").write_text(
        json.dumps(
            {
                "snapshot_id": "fixture-source-baseline",
                "retrieved_at": "2026-09-07T00:00:00+00:00",
                "source_kind": "fixture",
                "source_identifier": "source-baseline-test",
                "attribution": "Test source attribution",
                "evidence_sources": {
                    "official_road_classification": {
                        "licence": "Open Government Licence v3.0",
                        "effective_date": "2026-04-07",
                        "attribution": "Official road source",
                    },
                    "ncn": "NCN source",
                    "reclassified_ncn": "Reclassified NCN source",
                },
            }
        ),
        encoding="utf-8",
    )
    area.write_text(
        yaml.safe_dump(
            {
                "area_id": "source-baseline-test",
                "area_name": "Source Baseline Test",
                "deployment_id": "source-baseline-test",
                "source": {
                    "kind": "fixture",
                    "snapshot_dir": "snapshots",
                    "snapshot_id": "fixture-source-baseline",
                    "source_identifier": "source-baseline-test",
                },
                "publication": {
                    "output_dir": "build/compiled/source-baseline-test",
                    "title": "Source Baseline Test",
                    "audience": "public",
                },
            }
        ),
        encoding="utf-8",
    )
    return area


def test_source_baseline_builder_clips_and_retains_source_classes(tmp_path: Path) -> None:
    area = _write_test_area(tmp_path)
    destination = tmp_path / "source-baseline-pages"

    build_source_baseline_maps([area], destination)

    deployment = destination / "deployments" / "source-baseline-test"
    network = json.loads((deployment / "network.geojson").read_text(encoding="utf-8"))
    publication = json.loads((deployment / "publication.json").read_text(encoding="utf-8"))
    catalogue = json.loads((destination / "catalogue.json").read_text(encoding="utf-8"))

    features = network["features"]
    categories = [feature["properties"]["category"] for feature in features]
    assert set(categories) == {
        "a-road",
        "cycleway",
        "current-ncn",
        "former-ncn",
        "bridleway",
        "abandoned-railway",
    }
    assert categories.count("a-road") == 1
    assert categories.count("current-ncn") == 3
    assert categories.count("former-ncn") == 1
    assert categories.count("cycleway") == 4
    assert categories.count("bridleway") == 4
    assert categories.count("abandoned-railway") == 4
    bridleway_ncn = next(
        feature for feature in features if "bridleway-ncn" in feature["properties"]["source_ids"]
    )
    assert bridleway_ncn["properties"]["classification"] == "bridleway"
    railway_sources = {
        source_id
        for feature in features
        if feature["properties"]["category"] == "abandoned-railway"
        for source_id in feature["properties"]["source_ids"]
    }
    assert {
        "abandoned-railway",
        "disused-railway-reverse",
        "razed-railway",
    } <= railway_sources
    assert all(
        all(-1e-9 <= coordinate <= 10.000000001 for coordinate in point)
        for feature in features
        for point in feature["geometry"]["coordinates"]
    )
    assert all(
        feature["properties"].get("source_feature_id") not in {"official-b1", "outside-source"}
        for feature in features
    )
    assert publication["publication_kind"] == "source-baseline"
    assert publication["counts"] == {
        "a-road": 1,
        "cycleway": 4,
        "current-ncn": 3,
        "former-ncn": 1,
        "bridleway": 4,
        "abandoned-railway": 4,
    }
    assert catalogue["deployments"][0]["publication_kind"] == "source-baseline"
    assert catalogue["deployments"][0]["artifacts"]["review_map"] == (
        "deployments/source-baseline-test/index.html"
    )
    assert (destination / "index.html").exists()
    assert (destination / ".nojekyll").exists()
    assert (deployment / "service-worker.js").exists()
    html = (deployment / "index.html").read_text(encoding="utf-8")
    assert 'data-layer="baseline-bridleway"' in html
    assert 'data-layer="baseline-abandoned-railway"' in html
    assert "Bridleway evidence" in html
    assert "razed railway evidence" in html
    assert "does not assert cycling access" in html


def test_source_baseline_reads_adjacent_source_corridors(tmp_path: Path) -> None:
    area = _write_test_area(tmp_path)
    gpd.GeoDataFrame(
        [
            {
                "source_id": "adjacent-former-railway",
                "railway": "disused",
                "dataset": "explicit-railway-extract",
                "publisher": "Test railway publisher",
                "effective_date": "2026-09-07",
                "licence": "Test licence",
                "geometry": LineString([(0, 7.4), (10, 7.4)]),
            },
            {
                "source_id": "adjacent-razed-railway",
                "railway": "razed",
                "highway": "cycleway",
                "dataset": "explicit-railway-extract",
                "geometry": LineString([(0, 7.6), (10, 7.6)]),
            },
        ],
        geometry="geometry",
        crs=4326,
    ).to_file(area.parent / "source-corridors.geojson", driver="GeoJSON")

    destination = tmp_path / "source-baseline-pages"
    build_source_baseline_maps([area], destination)

    network = json.loads(
        (destination / "deployments" / "source-baseline-test" / "network.geojson").read_text(
            encoding="utf-8"
        )
    )
    adjacent = [
        feature
        for feature in network["features"]
        if "adjacent-former-railway" in feature["properties"]["source_ids"]
    ]
    assert len(adjacent) == 1
    assert adjacent[0]["properties"]["category"] == "abandoned-railway"
    assert adjacent[0]["properties"]["source_kind"] == "source-corridors"
    assert adjacent[0]["properties"]["dataset"] == "explicit-railway-extract"
    razed = [
        feature
        for feature in network["features"]
        if "adjacent-razed-railway" in feature["properties"]["source_ids"]
    ]
    assert len(razed) == 1
    assert razed[0]["properties"]["category"] == "abandoned-railway"
    assert (
        sum(feature["properties"]["category"] == "cycleway" for feature in network["features"]) == 4
    )
