from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import urllib.parse
from pathlib import Path

import geopandas as gpd
import pytest
from PIL import Image, TiffImagePlugin
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box

from satn.ea_elevation import (
    FIXED_POINT_PRIMARY_FIELD,
    WECA_SURVEY_BBOX,
    WECA_SURVEY_INDEX_FEATURE_COUNT,
    WECA_SURVEY_INDEX_FEATURE_SHA256,
    WECA_SURVEY_REQUEST,
    WECA_SURVEY_REQUEST_BBOX,
    eligible_route_fingerprint,
    eligible_route_samples,
    fixed_point_route_fingerprint,
    read_sample_ledger,
    write_sample_ledger,
)
from satn.models import NationalElevationConfig
from satn.sources import _ea_elevation_acquisition_provenance, _validate_ea_ledger_completeness

SCRIPT = Path(__file__).parents[1] / "scripts" / "acquire_ea_elevation.py"
SPEC = importlib.util.spec_from_file_location("acquire_ea_elevation", SCRIPT)
assert SPEC and SPEC.loader
acquisition = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acquisition)


class _TileResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _TileResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _valid_tile_bytes(
    key: tuple[int, int],
    *,
    tile_size_m: int = 20,
    spacing_m: int = 10,
    value: float = 42,
    nodata_tag: str = "-3.402823466e+38",
) -> bytes:
    image = Image.new("F", (tile_size_m // spacing_m, tile_size_m // spacing_m))
    image.putdata([value] * (image.width * image.height))
    minimum_east, minimum_north = key[0] * tile_size_m, key[1] * tile_size_m
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[34264] = (
        float(spacing_m),
        0.0,
        0.0,
        float(minimum_east),
        0.0,
        -float(spacing_m),
        0.0,
        float(minimum_north + tile_size_m),
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    )
    tags[34735] = (1, 1, 0, 1, 3072, 0, 1, 27700)
    tags[42113] = nodata_tag
    output = io.BytesIO()
    image.save(output, format="TIFF", tiffinfo=tags)
    return output.getvalue()


def test_supplemental_routes_break_a_two_cycle_without_changing_the_fixed_point(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary.geojson"
    supplemental = tmp_path / "supplemental.geojson"

    def write_routes(path: Path, second_line: LineString) -> None:
        gpd.GeoDataFrame(
            [
                {
                    "id": "shared",
                    "feature_type": "strategic-spine",
                    "topography_profile_id": "profile-shared",
                    "geometry": LineString([(350000, 150000), (350010, 150000)]),
                },
                {
                    "id": "choice",
                    "feature_type": "spine-access-connection",
                    "topography_profile_id": "profile-choice",
                    "geometry": second_line,
                },
            ],
            geometry="geometry",
            crs=27700,
        ).to_file(path, driver="GeoJSON")

    write_routes(primary, LineString([(350010, 150000), (350020, 150000)]))
    write_routes(supplemental, LineString([(350010, 150000), (350010, 150010)]))

    combined = acquisition._combined_sample_routes(primary, [supplemental])

    assert combined[FIXED_POINT_PRIMARY_FIELD].tolist() == [True, True, False]
    assert combined.iloc[-1]["feature_id"].startswith("supplemental-")
    assert fixed_point_route_fingerprint(combined) == eligible_route_fingerprint(
        gpd.read_file(primary)
    )
    samples, feature_ids = eligible_route_samples(combined, spacing_m=10)
    assert len(feature_ids) == 3
    assert {(sample["geometry"].x, sample["geometry"].y) for sample in samples} == {
        (350000, 150000),
        (350010, 150000),
        (350020, 150000),
        (350010, 150010),
    }


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
        (sample["sample_index"], sample["geometry"].x, sample["geometry"].y) for sample in canonical
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
        (row["route_id"], row["sample_index"], row["east_mm"], row["north_mm"]) for row in ledger
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


def test_arbitrary_endpoint_cannot_mint_a_governed_tile_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        acquisition.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail(
            "an unpinned endpoint must fail before network access"
        ),
    )

    with pytest.raises(ValueError, match="pinned official WCS endpoint"):
        acquisition.acquire_tile(
            (70, 30),
            tmp_path / "cache",
            tile_size_m=20,
            spacing_m=10,
            endpoint="https://example.test/untrusted-wcs",
            max_attempts=1,
        )
    assert not (tmp_path / "cache" / "receipts").exists()


def test_tile_receipt_publishes_a_content_addressed_object_and_reuses_it_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    key = (70, 30)
    payload = _valid_tile_bytes(key)
    monkeypatch.setattr(
        acquisition.urllib.request, "urlopen", lambda *_args, **_kwargs: _TileResponse(payload)
    )

    _key, path, _url, digest, attempts, failure = acquisition.acquire_tile(
        key, cache, tile_size_m=20, spacing_m=10, max_attempts=1
    )

    assert failure is None and attempts == 1 and digest is not None and path is not None
    assert path == cache / "objects" / "sha256" / f"{digest}.tif"
    request = acquisition._tile_request_payload(
        key, tile_size_m=20, spacing_m=10, endpoint=acquisition.ENDPOINT
    )
    request_fingerprint = acquisition._request_fingerprint(request)
    receipt = cache / "receipts" / f"{request_fingerprint}.json"
    observed = {
        "crs": "EPSG:27700",
        "dimensions": [2, 2],
        "model_transformation": [
            10.0,
            0.0,
            0.0,
            1400.0,
            0.0,
            -10.0,
            0.0,
            620.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ],
        "nodata": "-3.402823466e+38",
        "nodata_observed": "-3.402823466e+38",
    }
    assert receipt.read_bytes() == acquisition._canonical_receipt_bytes(
        {
            **request,
            "request_fingerprint": request_fingerprint,
            "raw_sha256": digest,
            "byte_count": len(payload),
            "observed_raster_metadata": observed,
        }
    )

    def offline(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("an intact governed receipt must be reused without network access")

    monkeypatch.setattr(acquisition.urllib.request, "urlopen", offline)
    assert acquisition.acquire_tile(key, cache, tile_size_m=20, spacing_m=10, max_attempts=1) == (
        key,
        path,
        acquisition.build_getcoverage_url(70, 30, tile_size_m=20, spacing_m=10),
        digest,
        0,
        None,
    )


def test_disconnected_route_tile_requests_reuse_real_cache_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two disconnected route tiles are independently reusable from one receipt cache."""

    cache = tmp_path / "cache"
    disconnected_route_tiles = [(70, 30), (73, 31)]
    calls = 0

    def online(*_args: object, **_kwargs: object) -> _TileResponse:
        nonlocal calls
        key = disconnected_route_tiles[calls]
        calls += 1
        return _TileResponse(_valid_tile_bytes(key))

    monkeypatch.setattr(acquisition.urllib.request, "urlopen", online)
    warmed = [
        acquisition.acquire_tile(key, cache, tile_size_m=20, spacing_m=10, max_attempts=1)
        for key in disconnected_route_tiles
    ]
    assert calls == 2 and all(result[1] is not None for result in warmed)

    monkeypatch.setattr(
        acquisition.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("both disconnected route tiles must reuse cache"),
    )
    reused = [
        acquisition.acquire_tile(key, cache, tile_size_m=20, spacing_m=10, max_attempts=1)
        for key in disconnected_route_tiles
    ]
    assert [result[4] for result in reused] == [0, 0]
    assert [result[5] for result in reused] == [None, None]


def test_tile_receipts_are_distinct_per_request_and_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    first_key, second_key = (70, 30), (71, 30)
    monkeypatch.setattr(
        acquisition.urllib.request,
        "urlopen",
        lambda request, **_kwargs: _TileResponse(
            _valid_tile_bytes(
                (70, 30)
                if urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)["subset"][0]
                == "E(1400,1420)"
                else (71, 30),
                value=43,
            )
        ),
    )

    acquisition.acquire_tile(first_key, cache, tile_size_m=20, spacing_m=10, max_attempts=1)
    acquisition.acquire_tile(second_key, cache, tile_size_m=20, spacing_m=10, max_attempts=1)

    receipts = sorted((cache / "receipts").glob("*.json"))
    assert len(receipts) == 2
    assert receipts[0].read_bytes() == acquisition._canonical_receipt_bytes(
        json.loads(receipts[0].read_bytes())
    )
    assert receipts[1].read_bytes() == acquisition._canonical_receipt_bytes(
        json.loads(receipts[1].read_bytes())
    )
    eastings = {json.loads(path.read_bytes())["request"]["tile_key"][0] for path in receipts}
    assert eastings == {70, 71}


def test_tampered_tile_object_reacquires_matching_bytes_and_receipt_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    key = (70, 30)
    payload = _valid_tile_bytes(key)
    monkeypatch.setattr(
        acquisition.urllib.request, "urlopen", lambda *_args, **_kwargs: _TileResponse(payload)
    )
    _key, object_path, _url, _digest, _attempts, _failure = acquisition.acquire_tile(
        key, cache, tile_size_m=20, spacing_m=10, max_attempts=1
    )
    assert object_path is not None
    object_path.write_bytes(b"tampered")

    reacquired = acquisition.acquire_tile(key, cache, tile_size_m=20, spacing_m=10, max_attempts=1)
    assert reacquired[4:] == (
        1,
        None,
    )
    request_fingerprint = acquisition._request_fingerprint(
        acquisition._tile_request_payload(
            key, tile_size_m=20, spacing_m=10, endpoint=acquisition.ENDPOINT
        )
    )
    receipt_path = cache / "receipts" / f"{request_fingerprint}.json"
    original_receipt = receipt_path.read_bytes()
    receipt_path.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(
        acquisition.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("a malformed receipt must fail before WCS access"),
    )

    malformed_receipt = acquisition.acquire_tile(
        key, cache, tile_size_m=20, spacing_m=10, max_attempts=1
    )
    assert malformed_receipt[1] is None and malformed_receipt[3] is None
    assert malformed_receipt[4:] == (0, "ValueError: EA tile receipt is invalid JSON")
    assert receipt_path.read_text(encoding="utf-8") == "not-json"

    receipt_with_extra = json.loads(original_receipt)
    receipt_with_extra["unrecognised"] = True
    receipt_path.write_bytes(acquisition._canonical_receipt_bytes(receipt_with_extra))
    unexpected_field = acquisition.acquire_tile(
        key, cache, tile_size_m=20, spacing_m=10, max_attempts=1
    )
    assert unexpected_field[1] is None and unexpected_field[3] is None
    assert unexpected_field[4:] == (
        0,
        "ValueError: EA tile receipt does not match the v1 schema",
    )


def test_incomplete_and_legacy_coordinate_tiffs_are_never_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    key = (70, 30)
    cache.mkdir()
    legacy = cache / "ea-dtm-70-30-10m.tif"
    legacy.write_bytes(_valid_tile_bytes(key))
    (cache / "receipts").mkdir()
    (cache / "receipts" / "incomplete.json.part").write_text("partial", encoding="utf-8")
    payload = _valid_tile_bytes(key)
    calls = 0

    def response(*_args: object, **_kwargs: object) -> _TileResponse:
        nonlocal calls
        calls += 1
        return _TileResponse(payload)

    monkeypatch.setattr(acquisition.urllib.request, "urlopen", response)
    result = acquisition.acquire_tile(key, cache, tile_size_m=20, spacing_m=10, max_attempts=1)

    assert calls == 1 and result[1] is not None
    assert legacy.exists()
    assert not legacy.with_name(legacy.name + ".legacy").exists()
    assert (cache / "receipts" / "incomplete.json.part").exists()


def test_existing_request_receipt_rejects_different_wcs_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    key = (70, 30)
    first = _valid_tile_bytes(key, value=42)
    monkeypatch.setattr(
        acquisition.urllib.request, "urlopen", lambda *_args, **_kwargs: _TileResponse(first)
    )
    _key, object_path, _url, _digest, _attempts, _failure = acquisition.acquire_tile(
        key, cache, tile_size_m=20, spacing_m=10, max_attempts=1
    )
    assert object_path is not None
    object_path.unlink()
    monkeypatch.setattr(
        acquisition.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _TileResponse(_valid_tile_bytes(key, value=99)),
    )

    _key, path, _url, digest, attempts, failure = acquisition.acquire_tile(
        key, cache, tile_size_m=20, spacing_m=10, max_attempts=1
    )

    assert path is None and digest is None and attempts == 1
    assert failure == "ValueError: EA WCS returned different bytes for an existing request receipt"


def test_receipt_publication_never_clobbers_a_conflicting_writer(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    first = {"contract": "test", "raw_sha256": "a" * 64}
    second = {"contract": "test", "raw_sha256": "b" * 64}

    acquisition._publish_receipt(receipt, first)

    with pytest.raises(ValueError, match="publication conflicts"):
        acquisition._publish_receipt(receipt, second)
    assert receipt.read_bytes() == acquisition._canonical_receipt_bytes(first)


def test_wcs_tile_with_wrong_transform_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    key = (70, 30)
    monkeypatch.setattr(
        acquisition.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _TileResponse(_valid_tile_bytes((71, 30))),
    )

    _key, path, _url, digest, attempts, failure = acquisition.acquire_tile(
        key, cache, tile_size_m=20, spacing_m=10, max_attempts=1
    )

    assert path is None and digest is None and attempts == 1
    assert failure is not None and failure.startswith(
        "ValueError: GeoTIFF transform does not match requested WCS tile:"
    )
    assert not (cache / "receipts").exists()


def test_real_ea_nodata_spelling_is_canonicalised_and_wrong_value_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = (70, 30)
    real_ea_spelling = "-3.4028234663852886E38"
    monkeypatch.setattr(
        acquisition.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _TileResponse(
            _valid_tile_bytes(key, nodata_tag=real_ea_spelling)
        ),
    )

    _key, path, _url, _digest, attempts, failure = acquisition.acquire_tile(
        key, tmp_path / "accepted", tile_size_m=20, spacing_m=10, max_attempts=1
    )

    assert path is not None and attempts == 1 and failure is None
    receipt = next((tmp_path / "accepted" / "receipts").glob("*.json"))
    metadata = json.loads(receipt.read_text(encoding="utf-8"))["observed_raster_metadata"]
    assert metadata["nodata"] == acquisition._GEOTIFF_NODATA
    assert metadata["nodata_observed"] == real_ea_spelling

    monkeypatch.setattr(
        acquisition.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _TileResponse(_valid_tile_bytes(key, nodata_tag="-3.3E38")),
    )
    _key, path, _url, digest, attempts, failure = acquisition.acquire_tile(
        key, tmp_path / "rejected", tile_size_m=20, spacing_m=10, max_attempts=1
    )
    assert path is None and digest is None and attempts == 1
    assert failure is not None and "unsupported NoData representation" in failure


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


def _unindexed_survey_choice(point: Point, index: gpd.GeoDataFrame) -> dict[str, object] | None:
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
    tags[34735] = (1, 1, 0, 1, 3072, 0, 1, 27700)
    tags[42113] = "-3.402823466e+38"
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


def test_authority_assignment_uses_the_immutable_millimetre_sample_point() -> None:
    boundaries = gpd.GeoDataFrame(
        [
            {
                "authority": name,
                "authority_id": f"authority-{position}",
                "geometry": (
                    box(0, 0, 1_000, 1)
                    if position == 0
                    else box(2_000 + position, 0, 2_001 + position, 1)
                ),
            }
            for position, name in enumerate(acquisition.WECA_AUTHORITIES)
        ],
        geometry="geometry",
        crs=27700,
    )
    sample = {
        "route_id": "boundary-rounding",
        "sample_index": 0,
        "geometry": Point(1_000.0004, 0.5),
    }

    assigned = acquisition._assigned_samples([sample], boundaries)

    assert assigned[0]["authority_id"] == "authority-0"


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
    assert manifest["explicit_unknown_sample_count"] == 0
    assert outside == {
        "authority": "routing-buffer/outside-authority",
        "status": "available",
        "route_sample_count": 2962,
        "requested_sample_count": 2962,
        "available_sample_count": 2962,
        "nodata_sample_count": 0,
        "explicit_unknown_sample_count": 0,
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


def test_banes_wcs_pixel_without_pinned_survey_is_explicit_unknown_before_immutable_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unattributed WCS value remains a ledger observation, never evidence."""
    routes = tmp_path / "routes.geojson"
    boundaries = tmp_path / "authorities.geojson"
    survey_index = tmp_path / "survey-index.geojson"
    output = tmp_path / "elevation-evidence.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "banes-unattributed-pixel",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile",
                "geometry": LineString([(350050, 150000), (350070, 150000)]),
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
                "geometry": box(350000, 149900, 350100, 150100)
                if position == 0
                else box(400000 + position, 0, 400001 + position, 1),
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
                "geometry": box(350000, 149900, 350065, 150100),
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
        acquisition, "validate_official_weca_survey_index", lambda _path: official_index
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
    evidence = gpd.read_file(output)

    assert manifest["requested_point_count"] == len(ledger) == 3
    assert manifest["evidence_sample_count"] == len(evidence) == 2
    assert manifest["nodata_sample_count"] == 0
    assert manifest["explicit_unknown_sample_count"] == 1
    assert manifest["sample_validation"]["status"] == "partial"
    assert [row["availability"] for row in ledger] == [
        "available",
        "available",
        "explicit-unknown",
    ]
    assert {
        field: ledger[-1][field]
        for field in (
            "availability",
            "elevation_m",
            "survey_feature_id",
            "ed_flown",
            "resolution_m",
            "evidence_row_sha256",
        )
    } == {
        "availability": "explicit-unknown",
        "elevation_m": None,
        "survey_feature_id": None,
        "ed_flown": None,
        "resolution_m": None,
        "evidence_row_sha256": None,
    }
    assert all(
        row["survey_feature_id"] is not None and row["ed_flown"] is not None
        for row in ledger
        if row["availability"] == "available"
    )
    assert sorted(evidence["sample_index"].tolist()) == [0, 1]

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

    assert provenance["coverage_status"] == "partial"
    assert provenance["explicit_unknown_sample_count"] == 1
    assert provenance["sample_ledger_sha256"] == manifest["sample_ledger_sha256"]
    assert len(provenance["evidence_row_sha256s"]) == 2


def test_valid_raster_nodata_is_proven_and_kept_distinct_from_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.geojson"
    boundaries = tmp_path / "authorities.geojson"
    survey_index = tmp_path / "survey-index.geojson"
    output = tmp_path / "elevation-evidence.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "nodata-route",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile",
                "geometry": LineString([(350050, 150000), (350060, 150000)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(routes, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {
                "authority": authority,
                "authority_id": f"authority-{position}",
                "source_query": "synthetic-boundary/v1",
                "geometry": box(350000, 149900, 350100, 150100)
                if position == 0
                else box(400000 + position, 0, 400001 + position, 1),
            }
            for position, authority in enumerate(acquisition.WECA_AUTHORITIES)
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
                "geometry": box(350000, 149900, 350100, 150100),
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
        acquisition, "validate_official_weca_survey_index", lambda _path: official_index
    )
    monkeypatch.setattr(
        "satn.sources.validate_official_weca_survey_index", lambda _path: official_index
    )
    tile = tmp_path / "tile.tif"
    tile.write_bytes(
        _valid_tile_bytes(
            (70, 30),
            tile_size_m=5000,
            value=float(acquisition._GEOTIFF_NODATA),
        )
    )
    monkeypatch.setattr(
        acquisition,
        "acquire_tile",
        lambda key, *_args, **_kwargs: (key, tile, "url", "a" * 64, 1, None),
    )

    manifest = acquisition.write_evidence(
        routes,
        output,
        tmp_path / "cache",
        spacing_m=10,
        authority_boundaries_path=boundaries,
        survey_index_path=survey_index,
        governed_input_fingerprint="c" * 64,
    )
    ledger_path = output.with_name("elevation-evidence.sample-ledger.jsonl")
    ledger = read_sample_ledger(ledger_path)

    assert manifest["availability_outcome"] == "all-nodata"
    assert manifest["sample_validation"]["availability_outcome"] == "all-nodata"
    assert all(row["availability"] == "nodata" for row in ledger)
    assert all(
        row["elevation_m"] is None
        and row["evidence_row_sha256"] is None
        and row["survey_feature_id"] == "survey-1"
        and row["tile_pixel_status"] == "validated-nodata"
        for row in ledger
    )
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
    assert provenance["availability_outcome"] == "all-nodata"

    ledger[0]["survey_feature_id"] = None
    digest = write_sample_ledger(ledger_path, ledger)
    sidecar_path = output.with_suffix(".manifest.json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["sample_ledger_sha256"] = digest
    sidecar_path.write_text(json.dumps(sidecar, sort_keys=True), encoding="utf-8")
    with pytest.raises(ValueError, match="survey selection differs"):
        _ea_elevation_acquisition_provenance(
            NationalElevationConfig(
                provider="local-geojson",
                path=output,
                source_id="ea-lidar-composite-dtm-1m",
                acquisition_contract="ea-lidar-weca-v1",
                licence=acquisition.LICENCE,
                attribution=acquisition.ATTRIBUTION,
            )
        )


def test_wcs_failure_is_explicit_unknown_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.geojson"
    boundaries = tmp_path / "authorities.geojson"
    survey_index = tmp_path / "survey-index.geojson"
    output = tmp_path / "elevation-evidence.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "unknown-route",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile",
                "geometry": LineString([(350050, 150000), (350060, 150000)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(routes, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {
                "authority": authority,
                "authority_id": f"authority-{position}",
                "source_query": "synthetic-boundary/v1",
                "geometry": box(350000, 149900, 350100, 150100)
                if position == 0
                else box(400000 + position, 0, 400001 + position, 1),
            }
            for position, authority in enumerate(acquisition.WECA_AUTHORITIES)
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
                "geometry": box(350000, 149900, 350100, 150100),
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
        acquisition, "validate_official_weca_survey_index", lambda _path: official_index
    )
    monkeypatch.setattr(
        "satn.sources.validate_official_weca_survey_index", lambda _path: official_index
    )
    monkeypatch.setattr(
        acquisition,
        "acquire_tile",
        lambda key, *_args, **_kwargs: (key, None, "url", None, 1, "OSError: unavailable"),
    )

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
    assert manifest["availability_outcome"] == "all-explicit-unknown"
    assert all(row["availability"] == "explicit-unknown" for row in ledger)
    assert all(row["tile_request_fingerprint"] is None for row in ledger)
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
    assert provenance["availability_outcome"] == "all-explicit-unknown"


def test_multi_tile_ledger_binds_each_sample_to_its_own_tile_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.geojson"
    boundaries = tmp_path / "authorities.geojson"
    survey_index = tmp_path / "survey-index.geojson"
    output = tmp_path / "elevation-evidence.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "first-tile",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile",
                "geometry": LineString([(350050, 150000), (350060, 150000)]),
            },
            {
                "feature_id": "second-tile",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile",
                "geometry": LineString([(355050, 150000), (355060, 150000)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(routes, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {
                "authority": authority,
                "authority_id": f"authority-{position}",
                "source_query": "synthetic-boundary/v1",
                "geometry": box(350000, 149900, 355100, 150100)
                if position == 0
                else box(400000 + position, 0, 400001 + position, 1),
            }
            for position, authority in enumerate(acquisition.WECA_AUTHORITIES)
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
                "geometry": box(350000, 149900, 355100, 150100),
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
        acquisition, "validate_official_weca_survey_index", lambda _path: official_index
    )
    tile_digests = {(70, 30): "a" * 64, (71, 30): "b" * 64}
    monkeypatch.setattr(
        acquisition,
        "acquire_tile",
        lambda key, *_args, **_kwargs: (
            key,
            tmp_path / f"{key[0]}-{key[1]}.tif",
            "url",
            tile_digests[key],
            1,
            None,
        ),
    )
    monkeypatch.setattr(acquisition, "load_tile", lambda _path: object())
    monkeypatch.setattr(acquisition, "sample_grid", lambda _grid, _point: 42.0)

    acquisition.write_evidence(
        routes,
        output,
        tmp_path / "cache",
        spacing_m=10,
        authority_boundaries_path=boundaries,
        survey_index_path=survey_index,
        governed_input_fingerprint="c" * 64,
    )
    ledger = read_sample_ledger(output.with_name("elevation-evidence.sample-ledger.jsonl"))
    expected = {
        "first-tile": ((70, 30), "a" * 64),
        "second-tile": ((71, 30), "b" * 64),
    }
    for row in ledger:
        key, digest = expected[row["route_id"]]
        request = acquisition._tile_request_payload(
            key, tile_size_m=5000, spacing_m=10, endpoint=acquisition.ENDPOINT
        )
        assert row["tile_request_fingerprint"] == acquisition._request_fingerprint(request)
        assert row["tile_raw_sha256"] == digest


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
