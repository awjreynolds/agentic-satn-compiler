from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, TiffImagePlugin
from pyproj import Transformer

SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_native_elevation.py"
CRS84 = "urn:ogc:def:crs:OGC:1.3:CRS84"


def _write_network(path: Path) -> tuple[int, int]:
    coordinates = [[-2.36, 51.38], [-2.3595, 51.38]]
    forward = Transformer.from_crs(4326, 27700, always_xy=True)
    east_north = [forward.transform(*coordinate) for coordinate in coordinates]
    tile = (int(east_north[0][0] // 5000), int(east_north[0][1] // 5000))
    assert all((int(east // 5000), int(north // 5000)) == tile for east, north in east_north)
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": CRS84}},
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"id": "fixture-road"},
                        "geometry": {"type": "LineString", "coordinates": coordinates},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return tile


def _write_tile(path: Path, tile: tuple[int, int]) -> str:
    width = height = 500
    minimum_east, minimum_north = tile[0] * 5000, tile[1] * 5000
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[34264] = (
        10.0,
        0.0,
        0.0,
        float(minimum_east),
        0.0,
        -10.0,
        0.0,
        float(minimum_north + 5000),
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
    Image.new("F", (width, height), 42.123456).save(path, format="TIFF", tiffinfo=tags)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cli_builds_fresh_evidence_from_explicit_paths_and_preserves_outputs(
    tmp_path: Path,
) -> None:
    network = tmp_path / "snapshot" / "network.geojson"
    network.parent.mkdir()
    tile = _write_network(network)
    cache = tmp_path / "ea-cache"
    object_dir = cache / "objects" / "sha256"
    receipt_dir = cache / "receipts"
    object_dir.mkdir(parents=True)
    receipt_dir.mkdir()
    raw_tile = object_dir / "fixture.tif"
    raw_sha256 = _write_tile(raw_tile, tile)
    raw_tile.rename(object_dir / f"{raw_sha256}.tif")
    (receipt_dir / "fixture.json").write_text(
        json.dumps(
            {
                "request_fingerprint": "a" * 64,
                "raw_sha256": raw_sha256,
                "source_resolution_m": 1,
                "vertical_accuracy": "+/-15cm RMSE",
                "observed_raster_metadata": {"dimensions": [500, 500]},
                "request": {
                    "tile_key": list(tile),
                    "tile_size_m": 5000,
                    "output_spacing_mm": 10000,
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "fresh" / "shared-feeder-elevation-fixture-v1"
    command = [
        sys.executable,
        str(SCRIPT),
        "--network",
        str(network),
        "--cache-dir",
        str(cache),
        "--output-dir",
        str(output),
    ]

    result = subprocess.run(command, check=True, capture_output=True, text=True)

    evidence_path = output / "elevation-evidence.geojson"
    manifest_path = output / "manifest.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert evidence["name"] == "shared-feeder-elevation-fixture-v1"
    assert evidence["features"]
    assert {feature["properties"]["elevation_m"] for feature in evidence["features"]} == {42.123}
    observed_spacing = {
        feature["properties"]["output_sample_spacing_m"] for feature in evidence["features"]
    }
    assert observed_spacing == {10.0}
    assert manifest["availability_summary"]["available_sample_count"] == len(evidence["features"])
    assert manifest["network_geometry"]["sampling_rule"] == (
        "canonical physical LineString, 10m intervals plus exact endpoint"
    )
    assert json.loads(result.stdout)["missing_tile_count"] == 0
    evidence_before = evidence_path.read_bytes()
    manifest_before = manifest_path.read_bytes()

    duplicate = subprocess.run(command, capture_output=True, text=True)

    assert duplicate.returncode != 0
    assert "refusing to overwrite existing output" in duplicate.stderr
    assert evidence_path.read_bytes() == evidence_before
    assert manifest_path.read_bytes() == manifest_before
