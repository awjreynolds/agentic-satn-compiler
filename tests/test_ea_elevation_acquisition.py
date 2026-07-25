from __future__ import annotations

import hashlib
import importlib.util
import json
import urllib.parse
from pathlib import Path

import geopandas as gpd
import pytest
from PIL import Image, TiffImagePlugin
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box

from satn.ea_elevation import (
    WECA_SURVEY_BBOX,
    WECA_SURVEY_INDEX_FEATURE_COUNT,
    WECA_SURVEY_INDEX_FEATURE_SHA256,
    WECA_SURVEY_REQUEST,
    WECA_SURVEY_REQUEST_BBOX,
    eligible_route_samples,
    read_sample_ledger,
)
from satn.models import NationalElevationConfig
from satn.sources import _ea_elevation_acquisition_provenance, _validate_ea_ledger_completeness

SCRIPT = Path(__file__).parents[1] / "scripts" / "acquire_ea_elevation.py"
SPEC = importlib.util.spec_from_file_location("acquire_ea_elevation", SCRIPT)
assert SPEC and SPEC.loader
acquisition = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acquisition)


def test_route_sampling_is_generic_bounded_and_deduplicated(tmp_path: Path) -> None:
    routes = tmp_path / "routes.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile-1",
                "geometry": LineString([(350000, 150000), (350025, 150000)]),
            },
            {
                "feature_type": "cross-spine-connector",
                "topography_profile_id": "aggregate",
                "geometry": LineString([(350000, 150000), (350100, 150000)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(routes, driver="GeoJSON")

    points, feature_ids = acquisition.route_sample_points(routes, 10)

    assert [(point.x, point.y) for point in points] == [
        (350000, 150000),
        (350010, 150000),
        (350020, 150000),
        (350025, 150000),
    ]
    assert len(feature_ids) == 1


def test_multipart_route_uses_one_canonical_sequence_for_tiles_evidence_and_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.geojson"
    boundaries = tmp_path / "authorities.geojson"
    index = tmp_path / "survey-index.geojson"
    output = tmp_path / "elevation-evidence.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "multipart",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile",
                "geometry": MultiLineString(
                    [
                        [(0, 0), (10, 0)],
                        [(10, 0), (20, 0)],
                        [(100, 0), (110, 0)],
                    ]
                ),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(routes, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {
                "authority": name,
                "authority_id": f"authority-{position}",
                "source_query": "synthetic-authority-boundaries/v1",
                "geometry": box(-10, -10, 120, 10)
                if position == 0
                else box(1_000 + position, 0, 1_001 + position, 1),
            }
            for position, name in enumerate(acquisition.WECA_AUTHORITIES)
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(boundaries, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {
                "id": "survey-1",
                "resolution": 1,
                "ed_flown": "2022-01-02",
                "geometry": box(-10, -10, 120, 10),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(index, driver="GeoJSON")
    monkeypatch.setattr(
        acquisition,
        "validate_official_weca_survey_index",
        lambda _path: {
            "official": "fixture",
            "raw_sha256": "a" * 64,
            "canonical_feature_sha256": "b" * 64,
        },
    )
    monkeypatch.setattr(
        acquisition,
        "acquire_tile",
        lambda key, *_args, **_kwargs: (
            key,
            tmp_path / f"{key[0]}-{key[1]}.tif",
            "url",
            "a" * 64,
            1,
            None,
        ),
    )
    monkeypatch.setattr(acquisition, "load_tile", lambda _path: object())
    monkeypatch.setattr(acquisition, "sample_grid", lambda _grid, _point: 42.0)
    original_survey_choice = acquisition._survey_choice
    survey_choice_calls = 0

    def counting_survey_choice(point: Point, index: object) -> dict[str, object] | None:
        nonlocal survey_choice_calls
        survey_choice_calls += 1
        return original_survey_choice(point, index)

    monkeypatch.setattr(acquisition, "_survey_choice", counting_survey_choice)

    canonical, feature_ids = eligible_route_samples(gpd.read_file(routes), spacing_m=10)
    assert [
        (sample["sample_index"], sample["geometry"].x, sample["geometry"].y)
        for sample in canonical
    ] == [(0, 0, 0), (1, 10, 0), (2, 20, 0), (3, 100, 0), (4, 110, 0)]
    points, sampled_feature_ids = acquisition.route_sample_points(routes, 10)
    assert [(point.x, point.y) for point in points] == [
        (0, 0),
        (10, 0),
        (20, 0),
        (100, 0),
        (110, 0),
    ]
    assert sampled_feature_ids == feature_ids == ["multipart"]

    manifest = acquisition.write_evidence(
        routes,
        output,
        tmp_path / "cache",
        spacing_m=10,
        authority_boundaries_path=boundaries,
        survey_index_path=index,
    )
    ledger = read_sample_ledger(output.with_name("elevation-evidence.sample-ledger.jsonl"))
    assert [
        (row["route_id"], row["sample_index"], row["east_mm"], row["north_mm"])
        for row in ledger
    ] == [
        (
            sample["route_id"],
            sample["sample_index"],
            round(sample["geometry"].x * 1000),
            round(sample["geometry"].y * 1000),
        )
        for sample in canonical
    ]
    assert manifest["requested_point_count"] == len(canonical)
    assert manifest["evidence_sample_count"] == len(canonical)
    assert manifest["effective_survey_date"] == "2022-01-02"
    # Preflight and final attribution each inspect every requested sample.  The
    # effective date must reuse final choices rather than starting a third pass.
    assert survey_choice_calls == len(canonical) * 2
    _validate_ea_ledger_completeness(
        rows=ledger,
        route_path=output.with_name("elevation-evidence.sampled-routes.geojson"),
    )


def test_getcoverage_url_uses_verified_axes_coverage_and_scaling() -> None:
    url = acquisition.build_getcoverage_url(
        70,
        30,
        tile_size_m=5000,
        spacing_m=10,
    )
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    assert query["coverageId"] == [acquisition.COVERAGE_ID]
    assert query["subset"] == ["E(350000,355000)", "N(150000,155000)"]
    assert query["scaleFactor"] == ["0.10000000"]
    assert query["format"] == ["image/tiff"]


def test_weca_wfs_pin_uses_governed_request_bbox_not_returned_feature_envelope() -> None:
    request_bbox = [float(value) for value in WECA_SURVEY_REQUEST["bbox"].split(",")[:4]]

    assert tuple(request_bbox) == WECA_SURVEY_REQUEST_BBOX
    assert tuple(request_bbox) != WECA_SURVEY_BBOX
    assert WECA_SURVEY_INDEX_FEATURE_COUNT == 1931
    assert WECA_SURVEY_INDEX_FEATURE_SHA256 == (
        "fa4b7b78d7adfb865166d7da161261b0134b98a9a909b5cc6fa5203b5d8ccd72"
    )


def test_exhausted_wcs_tile_is_bounded_and_retained_as_nodata_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0

    def unavailable(*_args: object, **_kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        raise OSError("persistent 500")

    monkeypatch.setattr(acquisition.urllib.request, "urlopen", unavailable)
    monkeypatch.setattr(acquisition.time, "sleep", lambda _delay: None)

    key, path, _url, digest, used, failure = acquisition.acquire_tile(
        (64, 37), tmp_path, tile_size_m=5000, spacing_m=10, max_attempts=2
    )

    assert key == (64, 37)
    assert path is None and digest is None
    assert used == attempts == 2
    assert failure == "OSError: persistent 500"


def test_eligible_route_fingerprint_is_semantic_not_wkb_or_direction_sensitive() -> None:
    from satn.ea_elevation import eligible_route_fingerprint

    def routes(line: LineString) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            [
                {
                    "feature_id": "route-1",
                    "feature_type": "strategic-spine",
                    "topography_profile_id": "p",
                    "geometry": line,
                }
            ],
            geometry="geometry",
            crs=27700,
        )

    canonical = eligible_route_fingerprint(routes(LineString([(0.0, 0.0), (10.0, 0.0)])))
    assert canonical == eligible_route_fingerprint(
        routes(LineString([(10.0, -0.0), (0.0004, 0.0)]))
    )
    assert canonical != eligible_route_fingerprint(routes(LineString([(0.0, 0.0), (10.01, 0.0)])))


def test_canonical_survey_polygon_ignores_ring_start_direction_and_multipart_order() -> None:
    from satn.ea_elevation import canonical_polygon_geometry

    first = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    same_reversed = Polygon([(10, 10), (10, 0), (0, 0), (0, 10)])
    second = Polygon([(20, 0), (30, 0), (30, 10), (20, 10)])
    assert canonical_polygon_geometry(first) == canonical_polygon_geometry(same_reversed)
    assert canonical_polygon_geometry(MultiPolygon([first, second])) == canonical_polygon_geometry(
        MultiPolygon([second, same_reversed])
    )
    assert canonical_polygon_geometry(first) != canonical_polygon_geometry(
        Polygon([(0, 0), (11, 0), (11, 10), (0, 10)])
    )


def _unindexed_survey_choice(
    point: Point, index: gpd.GeoDataFrame
) -> dict[str, object] | None:
    """The previous full-scan selection rule, retained here as an equivalence oracle."""
    matches: list[dict[str, object]] = []
    for position, row in index.to_crs(27700).iterrows():
        if not row.geometry.covers(point):
            continue
        feature_id = str(row.get("id") or row.get("polygon_id") or position)
        date = str(row.get("ed_flown") or "")[:10]
        try:
            resolution = float(row.get("resolution"))
        except (TypeError, ValueError):
            resolution = float("inf")
        matches.append(
            {"feature_id": feature_id, "ed_flown": date or None, "resolution_m": resolution}
        )
    if not matches:
        return None
    return sorted(
        matches,
        key=lambda row: (
            -(int(str(row["ed_flown"] or "0000-00-00").replace("-", ""))),
            float(row["resolution_m"]),
            str(row["feature_id"]),
        ),
    )[0]


def test_spatial_survey_attribution_is_equivalent_for_overlaps_and_missing_coverage() -> None:
    index = gpd.GeoDataFrame(
        [
            {
                "id": "old-fine",
                "ed_flown": "2020-01-01",
                "resolution": 0.5,
                "geometry": box(-10, -10, 10, 10),
            },
            {
                "id": "new-coarse",
                "ed_flown": "2024-01-01",
                "resolution": 2,
                "geometry": box(-10, -10, 10, 10),
            },
            {
                "id": "z-new-fine",
                "ed_flown": "2024-01-01",
                "resolution": 1,
                "geometry": box(-10, -10, 10, 10),
            },
            {
                "id": "a-new-fine",
                "ed_flown": "2024-01-01",
                "resolution": 1,
                "geometry": box(-10, -10, 10, 10),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    spatial = acquisition._SurveyAttributionIndex(index)

    for point in (Point(0, 0), Point(10, 0), Point(20, 0)):
        expected = _unindexed_survey_choice(point, index)
        actual = acquisition._survey_choice(point, spatial)
        assert actual == expected
        assert json.dumps(actual, sort_keys=True, separators=(",", ":")) == json.dumps(
            expected, sort_keys=True, separators=(",", ":")
        )

    assert acquisition._survey_choice(Point(0, 0), spatial) == {
        "feature_id": "a-new-fine",
        "ed_flown": "2024-01-01",
        "resolution_m": 1.0,
    }


def test_spatial_survey_attribution_scales_without_scanning_every_polygon(
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_count = 100
    index = gpd.GeoDataFrame(
        [
            {
                "id": f"survey-{feature_index}",
                "ed_flown": "2024-01-01",
                "resolution": 1,
                "geometry": box(feature_index * 20, 0, feature_index * 20 + 10, 10),
            }
            for feature_index in range(feature_count)
        ],
        geometry="geometry",
        crs=27700,
    )
    samples = [
        {
            "route_id": "synthetic-route",
            "sample_index": sample_index,
            "geometry": Point((sample_index % feature_count) * 20 + 5, 5),
        }
        for sample_index in range(90_000)
    ]
    spatial = acquisition._SurveyAttributionIndex(index)

    choices = acquisition._survey_choices(samples, spatial, phase="synthetic attribution")

    assert len(choices) == len(samples)
    assert spatial.candidate_checks == len(samples)
    assert spatial.candidate_checks < len(samples) * feature_count // 50
    heartbeats = capsys.readouterr().out.splitlines()
    assert 1 <= len(heartbeats) <= acquisition.MAX_PROGRESS_HEARTBEATS
    assert heartbeats[-1].endswith("90000/90000")


def test_float_geotiff_sampling_uses_embedded_model_transform(tmp_path: Path) -> None:
    path = tmp_path / "tile.tif"
    image = Image.new("F", (2, 2))
    image.putdata([10.0, 20.0, 30.0, 40.0])
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[34264] = (
        10.0,
        0.0,
        0.0,
        350000.0,
        0.0,
        -10.0,
        0.0,
        150020.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    )
    image.save(path, tiffinfo=tags)

    assert acquisition.sample_tile(path, Point(350005, 150015)) == pytest.approx(10)
    assert acquisition.sample_tile(path, Point(350015, 150005)) == pytest.approx(40)


def test_invalid_or_partial_tiff_never_becomes_a_sample(tmp_path: Path) -> None:
    path = tmp_path / "truncated.tif"
    path.write_bytes(b"II\x2a\x00partial")

    with pytest.raises((OSError, ValueError)):
        acquisition.load_tile(path)


def test_weca_acquisition_rejects_any_buffer_other_than_the_pinned_15km(
    tmp_path: Path,
) -> None:
    routes = tmp_path / "routes.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile",
                "geometry": LineString([(350000, 150000), (350010, 150000)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(routes, driver="GeoJSON")

    with pytest.raises(ValueError, match="exactly 15000m"):
        acquisition.validate_weca_route_extent(routes, routing_buffer_m=14_999)


def test_banes_cross_boundary_samples_beyond_authority_buffer_are_retained_reported_and_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 10 m ledger reports authority/outside transitions exactly as it is verified."""
    routes = tmp_path / "routes.geojson"
    boundaries = tmp_path / "authorities.geojson"
    survey_index = tmp_path / "survey-index.geojson"
    output = tmp_path / "elevation-evidence.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "banes-cross-boundary-spine",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile",
                "geometry": LineString([(350050, 150000), (380050, 150000)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(routes, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {
                "authority": name,
                "authority_id": f"authority-{position}",
                "source_query": "synthetic-banes-boundaries/v1",
                "geometry": box(
                    350000 + position * 110,
                    149900,
                    350100 + position * 110,
                    150100,
                ),
            }
            for position, name in enumerate(acquisition.WECA_AUTHORITIES)
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(boundaries, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {
                "id": "survey-1",
                "resolution": 1,
                "ed_flown": "2022-01-02",
                "geometry": box(340000, 140000, 390000, 160000),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(survey_index, driver="GeoJSON")
    official_index = {
        "raw_sha256": hashlib.sha256(survey_index.read_bytes()).hexdigest(),
        "canonical_feature_sha256": "b" * 64,
    }
    monkeypatch.setattr(
        acquisition,
        "validate_official_weca_survey_index",
        lambda _path: official_index,
    )
    monkeypatch.setattr(
        "satn.sources.validate_official_weca_survey_index", lambda _path: official_index
    )
    monkeypatch.setattr(
        acquisition,
        "acquire_tile",
        lambda key, *_args, **_kwargs: (key, tmp_path / "tile.tif", "url", "a" * 64, 1, None),
    )
    monkeypatch.setattr(acquisition, "load_tile", lambda _path: object())
    monkeypatch.setattr(acquisition, "sample_grid", lambda _grid, _point: 42.0)

    manifest = acquisition.write_evidence(
        routes,
        output,
        tmp_path / "cache",
        spacing_m=10,
        authority_boundaries_path=boundaries,
        survey_index_path=survey_index,
        governed_input_fingerprint="c" * 64,
    )
    ledger = read_sample_ledger(output.with_name("elevation-evidence.sample-ledger.jsonl"))
    outside = next(
        row
        for row in manifest["sample_validation"]["authorities"]
        if row["authority"] == "routing-buffer/outside-authority"
    )

    assert manifest["requested_point_count"] == 3001
    assert manifest["evidence_sample_count"] == 3001
    assert manifest["nodata_sample_count"] == 0
    assert outside == {
        "authority": "routing-buffer/outside-authority",
        "status": "available",
        "route_sample_count": 2962,
        "requested_sample_count": 2962,
        "available_sample_count": 2962,
        "nodata_sample_count": 0,
    }
    assert manifest["survey_coverage_preflight"]["official_survey_index"] == official_index
    assert manifest["sample_validation"]["cross_boundary_transitions"][-1] == {
        "route_id": "banes-cross-boundary-spine",
        "before_sample_index": 38,
        "after_sample_index": 39,
        "from_authority": acquisition._normalise_authority(acquisition.WECA_AUTHORITIES[3]),
        "to_authority": "routing-buffer",
        "status": "available",
    }

    provenance = _ea_elevation_acquisition_provenance(
        NationalElevationConfig(
            provider="local-geojson",
            path=output,
            source_id="ea-lidar-composite-dtm-1m",
            acquisition_contract="ea-lidar-weca-v1",
            licence=acquisition.LICENCE,
            attribution=acquisition.ATTRIBUTION,
        )
    )

    assert provenance["cross_boundary_transitions"][-1] == {
        "route_id": "banes-cross-boundary-spine",
        "before_sample_index": 38,
        "after_sample_index": 39,
        "from_authority_id": "authority-3",
        "to_authority_id": "routing-buffer",
        "status": "available",
    }
    assert len(ledger) == manifest["requested_point_count"]


def test_weca_pinned_route_extent_fails_closed_when_eligible_route_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.geojson"

    def write_routes(endpoint: int) -> None:
        gpd.GeoDataFrame(
            [
                {
                    "feature_type": "strategic-spine",
                    "topography_profile_id": "profile",
                    "geometry": LineString([(100, 100), (endpoint, 100)]),
                }
            ],
            geometry="geometry",
            crs=27700,
        ).to_file(routes, driver="GeoJSON")

    monkeypatch.setattr(acquisition, "WECA_PINNED_ELIGIBLE_ROUTE_BBOX", (100, 100, 200, 100))
    monkeypatch.setattr(acquisition, "WECA_SURVEY_REQUEST_BBOX", (-14900, -14900, 15200, 15100))
    write_routes(200)

    acquisition.validate_weca_route_extent(routes, routing_buffer_m=15_000)

    write_routes(201)
    with pytest.raises(ValueError, match="retained eligible-route extent differs"):
        acquisition.validate_weca_route_extent(routes, routing_buffer_m=15_000)


def _weca_preflight_inputs(tmp_path: Path, *, complete: bool) -> tuple[Path, Path, Path]:
    routes = tmp_path / "routes.geojson"
    boundaries = tmp_path / "authorities.geojson"
    index = tmp_path / "survey-index.geojson"
    names = list(acquisition.WECA_AUTHORITIES)
    geometries = [box(number * 100, 0, (number + 1) * 100, 100) for number in range(4)]
    gpd.GeoDataFrame(
        [
            {
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile",
                "geometry": LineString([(number * 100 + 10, 50), (number * 100 + 90, 50)]),
            }
            for number in range(4)
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(routes, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {
                "authority": name,
                "authority_id": f"test-authority-{number}",
                "source_query": "synthetic-test-boundaries/v1",
                "geometry": geometry,
            }
            for number, (name, geometry) in enumerate(zip(names, geometries, strict=True))
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(boundaries, driver="GeoJSON")
    covered = geometries if complete else geometries[:-1]
    metadata = {
        "schema_version": acquisition.CONTRACT_SCHEMA_VERSION,
        "dataset_id": acquisition.DATASET_ID,
        "dataset_title": acquisition.DATASET_TITLE,
        "coverage_id": acquisition.COVERAGE_ID,
        "endpoint": acquisition.ENDPOINT,
        "licence": acquisition.LICENCE,
        "attribution": acquisition.ATTRIBUTION,
        "effective_date": "2023-02-08",
    }
    gpd.GeoDataFrame(
        [{**metadata, "geometry": geometry} for geometry in covered],
        geometry="geometry",
        crs=27700,
    ).to_file(index, driver="GeoJSON")
    return routes, boundaries, index


def test_weca_preflight_requires_pinned_contract_and_reports_each_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes, boundaries, index = _weca_preflight_inputs(tmp_path, complete=True)
    monkeypatch.setattr(
        acquisition,
        "validate_official_weca_survey_index",
        lambda _path: {"official": "test-fixture"},
    )

    first = acquisition.preflight_weca_coverage(routes, boundaries, index)
    second = acquisition.preflight_weca_coverage(routes, boundaries, index)

    assert first == second
    assert first["status"] == "available"
    assert first["official_survey_index"] == {"official": "test-fixture"}
    assert [row["authority"] for row in first["authorities"]] == list(acquisition.WECA_AUTHORITIES)
    assert all(row["route_sample_count"] > 0 for row in first["authorities"])
    assert all(row["missing_sample_count"] == 0 for row in first["authorities"])


def test_weca_preflight_makes_partial_cross_authority_coverage_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes, boundaries, index = _weca_preflight_inputs(tmp_path, complete=False)
    monkeypatch.setattr(
        acquisition,
        "validate_official_weca_survey_index",
        lambda _path: {"official": "test-fixture"},
    )

    report = acquisition.preflight_weca_coverage(routes, boundaries, index)

    assert report["status"] == "partial"
    assert report["authorities"][-1]["authority"] == "South Gloucestershire"
    assert report["authorities"][-1]["status"] == "unavailable"
    assert report["authorities"][-1]["route_sample_count"] == 9
    assert report["authorities"][-1]["missing_sample_count"] == 9
    assert report["authorities"][-1]["available_sample_count"] == 0


def test_weca_preflight_refuses_an_unpinned_external_contract(tmp_path: Path) -> None:
    routes, boundaries, index = _weca_preflight_inputs(tmp_path, complete=True)
    with pytest.raises(ValueError, match=r"missing official fields|pinned canonical"):
        acquisition.preflight_weca_coverage(routes, boundaries, index)
