#!/usr/bin/env python3
"""Build fresh EA elevation evidence from a network and an offline tile cache.

This script does not acquire tiles. It samples only the EA DTM tiles already
present in the supplied cache and writes a new evidence package to an output
directory that does not already contain either output file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import LineString, Point

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.acquire_ea_elevation import load_tile, sample_grid  # noqa: E402

SOURCE_ID = "ea-lidar-composite-dtm-1m"
SOURCE_RESOLUTION_M = 1.0
OUTPUT_SAMPLE_SPACING_M = 10.0
TILE_SIZE_M = 5000
VERTICAL_ACCURACY_M = 0.15
VERTICAL_ACCURACY = "+/-15cm RMSE"
VERTICAL_REFERENCE = "ODN"
TRANSFORMATION = "OSTN15"
CRS84 = "urn:ogc:def:crs:OGC:1.3:CRS84"


@dataclass(slots=True)
class Sample:
    geometry_id: str
    sample_index: int
    east: float
    north: float
    longitude: float
    latitude: float
    tile: tuple[int, int]
    elevation_m: float | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tile_key(east: float, north: float) -> tuple[int, int]:
    return math.floor(east / TILE_SIZE_M), math.floor(north / TILE_SIZE_M)


def canonical_geometry_id(
    coordinates: list[tuple[float, float]],
) -> tuple[str, tuple[tuple[float, float], ...]]:
    """Return one stable identity for a LineString and its exact reverse."""
    forward = tuple(coordinates)
    reverse = tuple(reversed(forward))
    canonical = min(forward, reverse)
    encoded = json.dumps(canonical, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20], canonical


def sample_distances(length_m: float) -> list[float]:
    """Use the established 10 m intervals and include the endpoint once."""
    count = math.floor(length_m / OUTPUT_SAMPLE_SPACING_M)
    distances = [index * OUTPUT_SAMPLE_SPACING_M for index in range(count + 1)]
    if not distances or not math.isclose(distances[-1], length_m, abs_tol=1e-9):
        distances.append(length_m)
    else:
        distances[-1] = length_m
    return distances


def read_receipts(cache_dir: Path) -> dict[tuple[int, int], tuple[Path, dict[str, object]]]:
    """Index the supplied cache's receipts by the governed 5 km tile key."""
    receipts: dict[tuple[int, int], tuple[Path, dict[str, object]]] = {}
    for path in sorted((cache_dir / "receipts").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        request = payload.get("request")
        if not isinstance(request, dict) or request.get("tile_size_m") != TILE_SIZE_M:
            continue
        key_value = request.get("tile_key")
        if not isinstance(key_value, list) or len(key_value) != 2:
            continue
        key = (int(key_value[0]), int(key_value[1]))
        if key in receipts:
            raise ValueError(f"duplicate EA tile receipt for {key}")
        receipts[key] = (path, payload)
    return receipts


def tile_object_path(cache_dir: Path, payload: dict[str, object]) -> Path:
    raw_sha256 = payload.get("raw_sha256")
    if not isinstance(raw_sha256, str) or len(raw_sha256) != 64:
        raise ValueError("EA receipt has no usable raw_sha256")
    return cache_dir / "objects" / "sha256" / f"{raw_sha256}.tif"


def source_properties(tile: tuple[int, int]) -> dict[str, object]:
    return {
        "source_id": SOURCE_ID,
        "source_tile": f"{tile[0]},{tile[1]}",
        "source_resolution_m": SOURCE_RESOLUTION_M,
        "output_sample_spacing_m": OUTPUT_SAMPLE_SPACING_M,
        "vertical_accuracy_m": VERTICAL_ACCURACY_M,
    }


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True, help="CRS84 network GeoJSON")
    parser.add_argument("--cache-dir", type=Path, required=True, help="offline EA DTM tile cache")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="new directory for elevation-evidence.geojson and manifest.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    network_path = args.network.expanduser().resolve()
    cache_dir = args.cache_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not network_path.is_file():
        raise FileNotFoundError(f"network GeoJSON does not exist: {network_path}")
    if not cache_dir.is_dir():
        raise FileNotFoundError(f"EA tile cache directory does not exist: {cache_dir}")
    output_path = output_dir / "elevation-evidence.geojson"
    manifest_path = output_dir / "manifest.json"
    existing = [path for path in (output_path, manifest_path) if path.exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite existing output: {existing[0]}")

    started = time.perf_counter()
    source_digest = sha256_file(network_path)
    source = json.loads(network_path.read_text(encoding="utf-8"))
    features = source.get("features", [])
    if source.get("crs", {}).get("properties", {}).get("name") != CRS84:
        raise ValueError("network input is not CRS84 GeoJSON")

    forward = Transformer.from_crs(4326, 27700, always_xy=True)
    reverse = Transformer.from_crs(27700, 4326, always_xy=True)
    samples_by_tile: dict[tuple[int, int], list[Sample]] = {}
    physical: set[tuple[tuple[float, float], ...]] = set()
    directed_line_count = 0
    non_line_count = 0
    reverse_reused_feature_count = 0
    zero_length_count = 0

    for feature in features:
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
            non_line_count += 1
            continue
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            zero_length_count += 1
            continue
        directed_line_count += 1
        original = [(float(coordinate[0]), float(coordinate[1])) for coordinate in coordinates]
        geometry_id, canonical = canonical_geometry_id(original)
        if canonical in physical:
            reverse_reused_feature_count += 1
            continue
        line_bng = LineString([forward.transform(*coordinate) for coordinate in canonical])
        length_m = line_bng.length
        if not math.isfinite(length_m) or length_m <= 0:
            zero_length_count += 1
            continue
        physical.add(canonical)
        for sample_index, distance_m in enumerate(sample_distances(length_m)):
            point_bng = line_bng.interpolate(distance_m)
            longitude, latitude = reverse.transform(point_bng.x, point_bng.y)
            tile = tile_key(point_bng.x, point_bng.y)
            samples_by_tile.setdefault(tile, []).append(
                Sample(
                    geometry_id=geometry_id,
                    sample_index=sample_index,
                    east=point_bng.x,
                    north=point_bng.y,
                    longitude=longitude,
                    latitude=latitude,
                    tile=tile,
                )
            )

    receipts = read_receipts(cache_dir)
    required_tiles = sorted(samples_by_tile)
    missing_tiles = [tile for tile in required_tiles if tile not in receipts]
    tile_manifest: list[dict[str, object]] = []
    nodata_by_tile: dict[tuple[int, int], int] = {}
    available_tile_count = 0
    raster_resolution_m: float | None = None

    for tile in required_tiles:
        tile_samples = samples_by_tile[tile]
        receipt = receipts.get(tile)
        if receipt is None:
            continue
        receipt_path, payload = receipt
        object_path = tile_object_path(cache_dir, payload)
        grid = load_tile(object_path)
        transform = grid[1]
        observed_spacing_x = abs(float(transform[0]))
        observed_spacing_y = abs(float(transform[5]))
        if not math.isclose(observed_spacing_x, observed_spacing_y, rel_tol=0, abs_tol=1e-9):
            raise ValueError(f"EA tile {tile} has non-square raster spacing")
        if raster_resolution_m is None:
            raster_resolution_m = observed_spacing_x
        elif not math.isclose(raster_resolution_m, observed_spacing_x, rel_tol=0, abs_tol=1e-9):
            raise ValueError("EA cache tiles do not share one raster spacing")
        request = payload.get("request", {})
        if not isinstance(request, dict):
            raise ValueError(f"EA receipt {receipt_path.name} has no request payload")
        expected_spacing = int(request["output_spacing_mm"]) / 1000
        if not math.isclose(expected_spacing, observed_spacing_x, rel_tol=0, abs_tol=1e-9):
            raise ValueError(f"EA tile {tile} transform disagrees with receipt output spacing")
        no_data_count = 0
        for sample in tile_samples:
            sample.elevation_m = sample_grid(grid, Point(sample.east, sample.north))
            if sample.elevation_m is None:
                no_data_count += 1
            else:
                sample.elevation_m = round(float(sample.elevation_m), 3)
        nodata_by_tile[tile] = no_data_count
        available_tile_count += 1
        tile_manifest.append(
            {
                "tile_key": list(tile),
                "receipt_file": str(receipt_path.relative_to(cache_dir)),
                "request_fingerprint": payload.get("request_fingerprint"),
                "raw_sha256": payload.get("raw_sha256"),
                "object_file": str(object_path.relative_to(cache_dir)),
                "source_resolution_m": payload.get("source_resolution_m"),
                "output_sample_spacing_m": expected_spacing,
                "vertical_accuracy": payload.get("vertical_accuracy"),
                "observed_raster_dimensions": payload.get("observed_raster_metadata", {}).get(
                    "dimensions"
                ),
                "observed_raster_resolution_m": observed_spacing_x,
            }
        )

    available_samples = 0
    nodata_samples = 0
    missing_tile_samples = 0
    features_out: list[dict[str, object]] = []
    for tile in required_tiles:
        for sample in samples_by_tile[tile]:
            if tile in missing_tiles:
                missing_tile_samples += 1
                continue
            if sample.elevation_m is None:
                nodata_samples += 1
                continue
            available_samples += 1
            properties = {
                "evidence_id": f"ea-dtm-shared-{sample.geometry_id}-{sample.sample_index:04d}",
                **source_properties(tile),
                "elevation_m": sample.elevation_m,
            }
            features_out.append(
                {
                    "type": "Feature",
                    "properties": properties,
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(sample.longitude, 8), round(sample.latitude, 8)],
                    },
                }
            )

    summary = {
        "requested_sample_count": len(features_out) + nodata_samples + missing_tile_samples,
        "available_sample_count": available_samples,
        "nodata_sample_count": nodata_samples,
        "missing_tile_sample_count": missing_tile_samples,
        "required_tile_count": len(required_tiles),
        "available_tile_count": available_tile_count,
        "missing_tile_count": len(missing_tiles),
        "missing_tiles": [list(tile) for tile in missing_tiles],
        "nodata_by_tile": {
            f"{tile[0]},{tile[1]}": count for tile, count in sorted(nodata_by_tile.items()) if count
        },
    }
    artifact = output_dir.name
    evidence = {
        "type": "FeatureCollection",
        "name": artifact,
        "crs": {"type": "name", "properties": {"name": CRS84}},
        "features": features_out,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(evidence, separators=(",", ":")) + "\n", encoding="utf-8")
    manifest = {
        "artifact": artifact,
        "evidence_file": output_path.name,
        "evidence_sha256": sha256_file(output_path),
        "coordinate_reference_system": CRS84,
        "source": {
            "id": SOURCE_ID,
            "dataset_title": "LIDAR Composite Digital Terrain Model (DTM) - 1m",
            "source_resolution_m": SOURCE_RESOLUTION_M,
            "cached_raster_resolution_m": raster_resolution_m,
            "output_sample_spacing_m": OUTPUT_SAMPLE_SPACING_M,
            "vertical_accuracy": VERTICAL_ACCURACY,
            "vertical_accuracy_m": VERTICAL_ACCURACY_M,
            "vertical_reference": VERTICAL_REFERENCE,
            "transformation": TRANSFORMATION,
        },
        "source_input": {
            "path": str(network_path),
            "sha256": source_digest,
            "crs": CRS84,
            "feature_count": len(features),
            "linestring_count": directed_line_count,
            "non_linestring_count": non_line_count,
            "zero_length_or_short_count": zero_length_count,
        },
        "network_geometry": {
            "unique_physical_linestring_count": len(physical),
            "reverse_reused_feature_count": reverse_reused_feature_count,
            "sampling_rule": "canonical physical LineString, 10m intervals plus exact endpoint",
        },
        "cache": {
            "path": str(cache_dir),
            "tile_size_m": TILE_SIZE_M,
            "receipt_count_total": len(receipts),
            "receipts_used": sorted(tile_manifest, key=lambda item: item["tile_key"]),
        },
        "availability_summary": summary,
        "caveat": (
            "Only available samples are emitted. Missing receipt tiles and decoded NoData are "
            "counted separately; no interpolation or fabricated bridge/road samples are added."
        ),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output_path),
                "manifest": str(manifest_path),
                **summary,
                "elapsed_seconds": manifest["elapsed_seconds"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
