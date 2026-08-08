from __future__ import annotations

import json
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from lcwip.publication import ARTIFACTS, _validate_zip
from satn import remote_endpoints, sources
from satn.publisher import _validate_review_map_zip
from satn.remote_endpoints import validate_configured_https_endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.test/api",
        "https://user:secret@example.test/api",
        "https://example.test/api#fragment",
        "https://localhost/api",
        "https://127.0.0.1/api",
        "https://[::1]/api",
        "https://169.254.169.254/latest/meta-data",
    ],
)
def test_configured_remote_endpoint_rejects_unsafe_locations(endpoint: str) -> None:
    with pytest.raises(ValueError):
        validate_configured_https_endpoint(endpoint, field_name="endpoint")


def test_configured_remote_endpoint_accepts_production_service() -> None:
    endpoint = "https://services5.arcgis.com/example/FeatureServer"
    assert validate_configured_https_endpoint(endpoint, field_name="endpoint") == endpoint


def test_configured_https_transport_refuses_remote_redirects() -> None:
    request = urllib.request.Request("https://service.example.test/data")
    handler = remote_endpoints._RejectRedirects()

    with pytest.raises(urllib.error.HTTPError, match="redirect"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://169.254.169.254/latest/meta-data",
        )


def test_bounded_response_read_rejects_chunked_overflow() -> None:
    class Response:
        def __init__(self) -> None:
            self.remaining = b"abcdef"

        def read(self, size: int) -> bytes:
            value, self.remaining = self.remaining[:size], self.remaining[size:]
            return value

    with pytest.raises(ValueError, match="byte budget"):
        sources._read_response_with_limit(Response(), max_bytes=5, source_label="fixture")


def test_arcgis_page_budget_is_checked_before_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import geopandas as gpd
    from shapely.geometry import Polygon

    class Response:
        def __init__(self) -> None:
            self.remaining = b'{"features": [{}], "exceededTransferLimit": true}'

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            value, self.remaining = self.remaining[:size], self.remaining[size:]
            return value

    monkeypatch.setattr(sources, "ARCGIS_MAX_PAGES", 1)
    monkeypatch.setattr(sources, "open_configured_https", lambda *_args, **_kwargs: Response())
    boundary = gpd.GeoDataFrame(geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])], crs=4326)
    with pytest.raises(ValueError, match="page budget"):
        sources._load_arcgis_cycle_routes(
            "https://example.test/FeatureServer", boundary, where="1=1", source_label="fixture"
        )


def test_review_map_zip_requires_exact_member_bytes_and_unique_members(tmp_path: Path) -> None:
    review = tmp_path / "review-map"
    review.mkdir()
    (review / "index.html").write_text("expected")
    archive_path = tmp_path / "review-map.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("review-map/index.html", "expected")
    _validate_review_map_zip(archive_path, review)

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("review-map/index.html", "tampered")
    with pytest.raises(ValueError, match="bytes differ"):
        _validate_review_map_zip(archive_path, review)

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("review-map/index.html", "expected")
        archive.writestr("review-map/index.html", "expected")
    with pytest.raises(ValueError, match="differs"):
        _validate_review_map_zip(archive_path, review)


def test_review_map_zip_accepts_large_exact_static_mirror(tmp_path: Path) -> None:
    review = tmp_path / "review-map"
    review.mkdir()
    large_member = review / "network.geojson"
    with large_member.open("wb") as stream:
        stream.seek(100 * 1024 * 1024)
        stream.write(b"\n")
    archive_path = tmp_path / "review-map.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.write(large_member, "review-map/network.geojson")

    _validate_review_map_zip(archive_path, review)


def test_review_map_zip_rejects_high_ratio_from_metadata(tmp_path: Path) -> None:
    review = tmp_path / "review-map"
    review.mkdir()
    (review / "index.html").write_bytes(b"a" * 100_000)
    archive_path = tmp_path / "review-map.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(review / "index.html", "review-map/index.html")
    with pytest.raises(ValueError, match="compression ratio"):
        _validate_review_map_zip(archive_path, review)


def test_lcwip_zip_rejects_high_ratio_before_decompression(tmp_path: Path) -> None:
    for name in ARTIFACTS:
        if name != "lcwip-release.zip":
            (tmp_path / name).write_bytes(b"a" * 100_000)
    archive_path = tmp_path / "lcwip-release.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ARTIFACTS:
            if name != "lcwip-release.zip":
                archive.write(tmp_path / name, name)
        archive.comment = json.dumps({"release_id": "fixture"}, sort_keys=True).encode()
    with pytest.raises(ValueError, match="compression ratio"):
        _validate_zip(archive_path, SimpleNamespace(watermark={"release_id": "fixture"}))
