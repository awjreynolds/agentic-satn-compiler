from __future__ import annotations

import json
import shutil
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon

from satn import compile
from satn.agents import FakeAgentRuntime
from satn.compiler import compile_network
from satn.models import CouncilConfig, TopographyConfig
from satn.publisher import publish
from satn.sources import snapshot
from satn.topography import GradientThresholds, build_topography_profiles

PROJECT = Path(__file__).parents[1]


def edge(geometry: LineString, **properties: object) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [{"connection_id": "edge-1", **properties, "geometry": geometry}],
        geometry="geometry",
        crs=27700,
    )


def elevations(values: list[tuple[float, float]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "evidence_id": f"terrain-{index}",
                "source_id": "fixture-terrain",
                "elevation_m": elevation,
                "geometry": Point(distance, 0),
            }
            for index, (distance, elevation) in enumerate(values)
        ],
        geometry="geometry",
        crs=27700,
    )


def profiles_for(
    geometry: LineString,
    samples: list[tuple[float, float]],
    *,
    thresholds: GradientThresholds | None = None,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    frame = edge(geometry)
    profiles, sections = build_topography_profiles(
        [("connection", "connection_id", frame)],
        elevations(samples),
        thresholds=thresholds,
    )
    return frame, profiles, sections


def test_net_zero_hill_records_descent_and_climb_in_both_directions() -> None:
    frame, profiles, _ = profiles_for(
        LineString([(0, 0), (100, 0), (200, 0)]),
        [(0, 100), (100, 0), (200, 100)],
    )

    profile = profiles.iloc[0]
    assert profile["evidence_status"] == "available"
    assert profile["distance_m"] == pytest.approx(200)
    assert profile["forward_ascent_m"] == pytest.approx(100)
    assert profile["forward_descent_m"] == pytest.approx(100)
    assert profile["reverse_ascent_m"] == pytest.approx(100)
    assert profile["reverse_descent_m"] == pytest.approx(100)
    assert frame.iloc[0]["topography_profile_id"] == profile["profile_id"]
    assert frame.iloc[0]["topography_evidence_status"] == "available"


def test_downhill_in_one_direction_is_the_reverse_direction_climb() -> None:
    _, profiles, sections = profiles_for(
        LineString([(0, 0), (100, 0)]),
        [(0, 100), (100, 0)],
    )

    profile = profiles.iloc[0]
    assert profile["forward_ascent_m"] == pytest.approx(0)
    assert profile["forward_descent_m"] == pytest.approx(100)
    assert profile["reverse_ascent_m"] == pytest.approx(100)
    assert profile["reverse_descent_m"] == pytest.approx(0)
    assert sections.iloc[0]["uphill_direction"] == "reverse"


def test_adjustable_gradient_bands_keep_every_section_visible() -> None:
    _, profiles, sections = profiles_for(
        LineString([(0, 0), (100, 0), (200, 0), (300, 0), (400, 0), (500, 0)]),
        [(0, 0), (100, 3), (200, 8), (300, 16), (400, 28.5), (500, 48)],
        thresholds=GradientThresholds(gentle=3, noticeable=5, steep=8, very_steep=12.5),
    )

    assert list(sections["gradient_band"]) == [
        "gentle",
        "noticeable",
        "steep",
        "very-steep",
        "severe",
    ]
    assert list(sections["length_m"]) == pytest.approx([100, 100, 100, 100, 100])
    assert profiles.iloc[0]["steepest_sustained_gradient_pct"] == pytest.approx(19.5)
    assert json.loads(profiles.iloc[0]["gradient_section_ids"]) == list(sections["section_id"])


def test_elevation_evidence_is_reprojected_once_for_all_edges(monkeypatch) -> None:
    frame = gpd.GeoDataFrame(
        [
            {
                "connection_id": "edge-1",
                "geometry": LineString([(-2.36, 51.38), (-2.359, 51.38)]),
            },
            {
                "connection_id": "edge-2",
                "geometry": LineString([(-2.36, 51.381), (-2.359, 51.381)]),
            },
        ],
        geometry="geometry",
        crs=4326,
    )
    evidence = gpd.GeoDataFrame(
        [
            {
                "evidence_id": f"terrain-{index}",
                "source_id": "fixture-terrain",
                "elevation_m": elevation,
                "geometry": Point(x, y),
            }
            for index, (x, y, elevation) in enumerate(
                [
                    (-2.36, 51.38, 10),
                    (-2.359, 51.38, 11),
                    (-2.36, 51.381, 12),
                    (-2.359, 51.381, 13),
                ]
            )
        ],
        geometry="geometry",
        crs=4326,
    )
    original_to_crs = gpd.GeoDataFrame.to_crs
    elevation_reprojections = 0

    def counted_to_crs(dataframe, *args, **kwargs):
        nonlocal elevation_reprojections
        if "elevation_m" in dataframe.columns:
            elevation_reprojections += 1
        return original_to_crs(dataframe, *args, **kwargs)

    monkeypatch.setattr(gpd.GeoDataFrame, "to_crs", counted_to_crs)

    profiles, _ = build_topography_profiles(
        [("connection", "connection_id", frame)],
        evidence,
    )

    assert len(profiles) == 2
    assert elevation_reprojections == 1


def test_dense_evidence_publishes_20m_and_50m_micro_gradient_intervals() -> None:
    frame, profiles, _ = profiles_for(
        LineString([(0, 0), (100, 0)]),
        [(distance, distance / 10) for distance in range(0, 101, 10)],
    )

    profile = profiles.iloc[0]
    capability = json.loads(profile["micro_gradient_capability"])
    intervals = json.loads(profile["micro_gradient_intervals"])

    assert capability["status"] == "available"
    assert capability["windows_m"] == [20.0, 50.0]
    assert capability["maximum_observed_sample_spacing_m"] == pytest.approx(10)
    assert capability["evidence_quality_status"] == "uncertain"
    assert capability["uncertainty"]["vertical_accuracy_m"] is None
    assert {(item["window_m"], item["start_distance_m"]) for item in intervals} == {
        (20.0, 0.0),
        (20.0, 10.0),
        (20.0, 20.0),
        (20.0, 30.0),
        (20.0, 40.0),
        (20.0, 50.0),
        (20.0, 60.0),
        (20.0, 70.0),
        (20.0, 80.0),
        (50.0, 0.0),
        (50.0, 10.0),
        (50.0, 20.0),
        (50.0, 30.0),
        (50.0, 40.0),
        (50.0, 50.0),
    }
    assert {item["forward_gradient_pct"] for item in intervals} == {10.0}
    assert all(item["status"] == "available" for item in intervals)
    assert all(item["uncertainty"]["gradient_is_derived"] for item in intervals)
    assert all(item["calculation_method"] == "rolling-endpoint-difference" for item in intervals)
    assert "micro_gradient_intervals" not in frame


def test_sparse_legacy_profile_marks_micro_gradient_as_unavailable() -> None:
    _, profiles, _ = profiles_for(
        LineString([(0, 0), (100, 0)]),
        [(0, 0), (100, 10)],
    )

    profile = profiles.iloc[0]
    assert profile["evidence_status"] == "available"
    capability = json.loads(profile["micro_gradient_capability"])
    assert capability["status"] == "unavailable"
    assert "12.5 m" in capability["rationale"]
    assert json.loads(profile["micro_gradient_intervals"]) == []


def test_micro_gradient_source_quality_is_scoped_conservatively_to_edge_samples() -> None:
    frame = edge(LineString([(0, 0), (100, 0)]))
    evidence = elevations([(distance, distance / 10) for distance in range(0, 101, 10)])
    evidence["vertical_accuracy_m"] = [0.15] * (len(evidence) - 1) + [None]
    evidence.loc[len(evidence)] = {
        "evidence_id": "unrelated-complete-metadata",
        "source_id": "other-source",
        "elevation_m": 0,
        "vertical_accuracy_m": 0.05,
        "geometry": Point(500, 500),
    }

    profiles, _ = build_topography_profiles(
        [("connection", "connection_id", frame)],
        evidence,
    )

    capability = json.loads(profiles.iloc[0]["micro_gradient_capability"])
    assert capability["status"] == "available"
    assert capability["evidence_quality_status"] == "uncertain"
    assert capability["uncertainty"]["vertical_accuracy_m"] is None


def test_micro_gradient_clamps_endpoints_within_governed_edge_tolerance() -> None:
    _, profiles, _ = profiles_for(
        LineString([(0, 0), (100, 0)]),
        [(2, 0.2), (12, 1.2), (22, 2.2), (32, 3.2), (42, 4.2), (52, 5.2),
         (62, 6.2), (72, 7.2), (82, 8.2), (92, 9.2), (98, 9.8)],
    )

    intervals = json.loads(profiles.iloc[0]["micro_gradient_intervals"])
    assert intervals
    assert intervals[0]["start_distance_m"] == 0
    assert intervals[-1]["end_distance_m"] == 100


def test_micro_gradient_rejects_dense_samples_that_do_not_cover_edge_ends() -> None:
    _, profiles, _ = profiles_for(
        LineString([(0, 0), (100, 0)]),
        [(distance, distance / 10) for distance in range(20, 81, 10)],
    )

    capability = json.loads(profiles.iloc[0]["micro_gradient_capability"])
    assert capability["status"] == "unavailable"
    assert json.loads(profiles.iloc[0]["micro_gradient_intervals"]) == []


@pytest.mark.parametrize("limitation", ["bridge", "tunnel"])
def test_ground_model_micro_gradient_is_unavailable_on_carried_routes(
    limitation: str,
) -> None:
    frame = edge(
        LineString([(0, 0), (100, 0)]),
        **{limitation: "yes"},
    )
    profiles, sections = build_topography_profiles(
        [("connection", "connection_id", frame)],
        elevations([(distance, distance / 10) for distance in range(0, 101, 10)]),
    )

    capability = json.loads(profiles.iloc[0]["micro_gradient_capability"])
    assert profiles.iloc[0]["evidence_status"] == "available"
    assert not sections.empty
    assert capability["status"] == "unavailable"
    assert limitation in capability["rationale"]
    assert json.loads(profiles.iloc[0]["micro_gradient_intervals"]) == []


def test_unusable_elevation_evidence_is_explicitly_grey() -> None:
    frame, profiles, sections = profiles_for(
        LineString([(0, 0), (100, 0)]),
        [(0, 10)],
    )

    profile = profiles.iloc[0]
    assert profile["evidence_status"] == "evidence-unavailable"
    assert profile["criterion_elevation_evidence"] == "grey"
    assert "at least two" in profile["evidence_rationale"]
    assert profile["forward_ascent_m"] is None
    assert frame.iloc[0]["topography_evidence_status"] == "evidence-unavailable"
    assert sections.empty


def test_endpoint_only_evidence_on_a_long_edge_is_grey() -> None:
    _, profiles, sections = profiles_for(
        LineString([(0, 0), (1000, 0)]),
        [(0, 0), (1000, 0)],
    )

    profile = profiles.iloc[0]
    assert profile["evidence_status"] == "evidence-unavailable"
    assert "interior gap" in profile["evidence_rationale"]
    assert sections.empty


def test_isolated_sub_window_anomaly_is_not_a_sustained_severe_section() -> None:
    _, profiles, sections = profiles_for(
        LineString([(0, 0), (20, 0)]),
        [(0, 0), (9.99, 0), (10, 10), (10.01, 0), (20, 0)],
    )

    assert profiles.iloc[0]["steepest_sustained_gradient_pct"] == pytest.approx(0)
    assert profiles.iloc[0]["forward_ascent_m"] == pytest.approx(0)
    assert profiles.iloc[0]["forward_descent_m"] == pytest.approx(0)
    assert set(sections["gradient_band"]) == {"gentle"}


def test_clustered_sub_window_anomaly_is_visible_but_not_sustained() -> None:
    _, profiles, sections = profiles_for(
        LineString([(0, 0), (20, 0)]),
        [(0, 0), (10, 100), (10.01, 100), (20, 0)],
    )

    assert profiles.iloc[0]["forward_ascent_m"] == pytest.approx(0)
    assert profiles.iloc[0]["forward_descent_m"] == pytest.approx(0)
    assert profiles.iloc[0]["steepest_sustained_gradient_pct"] == pytest.approx(0)
    assert set(sections["gradient_band"]) == {"gentle"}


def test_short_governed_steeper_pinch_remains_visible_and_counts_climbing() -> None:
    _, profiles, sections = profiles_for(
        LineString([(0, 0), (4, 0), (8, 0)]),
        [(0, 0), (4, 1), (8, 0)],
    )

    assert profiles.iloc[0]["forward_ascent_m"] == pytest.approx(1)
    assert profiles.iloc[0]["forward_descent_m"] == pytest.approx(1)
    assert pd.isna(profiles.iloc[0]["steepest_sustained_gradient_pct"])
    assert set(sections["gradient_band"]) == {"severe"}
    assert not sections["sustained"].any()


def test_short_monotonic_edge_has_no_sustained_gradient_statistic() -> None:
    _, profiles, sections = profiles_for(
        LineString([(0, 0), (8, 0)]),
        [(0, 0), (8, 1)],
    )

    profile = profiles.iloc[0]
    assert profile["forward_ascent_m"] == pytest.approx(1)
    assert pd.isna(profile["steepest_sustained_gradient_pct"])
    assert "No interval meets" in profile["steepest_sustained_gradient_rationale"]
    assert list(sections["sustained"]) == [False]


def test_compiler_consumes_governed_fixture_elevation_evidence_without_rerouting(
    tmp_path: Path,
) -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "west",
                "name": "West",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(0, 0),
            },
            {
                "place_id": "east",
                "name": "East",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(200, 0),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "unchanged-route",
                "highway": "unclassified",
                "geometry": LineString([(0, 0), (100, 0), (200, 0)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    source = {
        "boundary": gpd.GeoDataFrame(
            geometry=[Polygon([(-50, -50), (250, -50), (250, 50), (-50, 50)])],
            crs=27700,
        ),
        "places": places,
        "network": network,
        "context": gpd.GeoDataFrame(
            [
                {
                    "evidence_id": "west-spine",
                    "feature_type": "a-road-spine",
                    "name": "A1",
                    "source_id": "west-spine-source",
                    "network_scope": "rural",
                    "category": "A-road strategic spine",
                    "geometry": LineString([(0, 0), (0, 1)]),
                }
            ],
            geometry="geometry",
            crs=27700,
        ),
        "elevation_evidence": elevations([(0, 100), (100, 0), (200, 100)]),
    }

    council = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    council.publication.output_dir = tmp_path / "published"
    compiled = compile_network(
        council,
        source,
        FakeAgentRuntime(),
    )

    east = compiled.spine_access_connections.set_index("place_id").loc["east"]
    assert east.geometry.equals(network.iloc[0].geometry)
    east_profile = compiled.topography_profiles[
        compiled.topography_profiles["edge_id"] == east["access_connection_id"]
    ].iloc[0]
    assert east_profile["evidence_status"] == "available"
    assert set(compiled.gradient_sections["uphill_direction"]) == {"forward", "reverse"}
    assert compiled.criteria["topography"]["elevation_evidence_coverage"] == "grey"

    artifacts = publish(council, compiled, "run-topography-fixture")
    published_profiles = gpd.read_file(artifacts["geopackage"], layer="topography_profiles")
    published_sections = gpd.read_file(artifacts["geopackage"], layer="gradient_sections")
    published_geojson = json.loads(artifacts["geojson"].read_text())
    assert set(published_profiles["profile_id"]) == {
        feature["id"]
        for feature in published_geojson["features"]
        if feature["properties"]["feature_type"] == "topography-profile"
    }
    assert set(published_sections["section_id"]) == {
        feature["id"]
        for feature in published_geojson["features"]
        if feature["properties"]["feature_type"] == "gradient-section"
    }


def test_public_compile_uses_snapshot_elevation_and_configured_bands(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    council = CouncilConfig.from_yaml(fixture / "council.yaml")
    council.compilation.topography = TopographyConfig(
        gentle_max_pct=100,
        noticeable_max_pct=101,
        steep_max_pct=102,
        very_steep_max_pct=103,
        maximum_sample_spacing_m=250,
        minimum_sustained_spacing_m=10,
    )
    snapshot_path = snapshot(council)

    manifest = json.loads((snapshot_path / "snapshot.json").read_text())
    elevation_manifest = manifest["evidence_sources"]["elevation"]
    assert elevation_manifest | {
        "retrieved_at": "ignored",
        "content_fingerprint": "ignored",
    } == {
        "provider": "local-geojson",
        "source_id": "fixture-terrain-2026",
        "effective_date": "2026-01-01",
        "date_kind": "effective",
        "licence": "Synthetic fixture",
        "attribution": "Synthetic governed terrain fixture",
        "bounded_to_compilation_area": True,
        "coverage_status": "available",
        "sample_count": 30,
        "content_fingerprint": "ignored",
        "retrieved_at": "ignored",
    }
    assert (
        elevation_manifest["content_fingerprint"]
        == manifest["file_sha256"]["elevation-evidence.geojson"]
    )
    result = compile(council)
    profiles = gpd.read_file(result.artifacts["geopackage"], layer="topography_profiles")
    sections = gpd.read_file(result.artifacts["geopackage"], layer="gradient_sections")
    assert "available" in set(profiles["evidence_status"])
    assert not sections.empty
    assert set(sections["gradient_band"]) == {"gentle"}
    html = result.artifacts["review_map"].read_text()
    assert "Gentle ≤ 100%;" in html
    assert "Noticeable ≤ 101%;" in html
