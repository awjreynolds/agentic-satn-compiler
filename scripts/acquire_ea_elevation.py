"""Acquire council-generic EA LIDAR DTM samples along published SATN edges."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import pairwise
from pathlib import Path

import geopandas as gpd
import numpy as np
from PIL import Image, ImageFile
from shapely.geometry import Point
from shapely.ops import unary_union

from satn.ea_elevation import (
    CONTRACT_SCHEMA_VERSION,
    DTM_VERTICAL_ACCURACY,
    ELIGIBLE_FEATURE_TYPES,
    WECA_PINNED_ELIGIBLE_ROUTE_BBOX,
    WECA_ROUTING_BUFFER_M,
    WECA_SURVEY_REQUEST_BBOX,
    canonical_polygon_geometry,
    eligible_route_fingerprint,
    eligible_route_samples,
    evidence_row_sha256,
    governed_survey_request_bbox,
    sha256_file,
    validate_official_weca_survey_index,
    write_sample_ledger,
)
from satn.ea_elevation import (
    DTM_ATTRIBUTION as ATTRIBUTION,
)
from satn.ea_elevation import (
    DTM_COVERAGE_ID as COVERAGE_ID,
)
from satn.ea_elevation import (
    DTM_DATASET_ID as DATASET_ID,
)
from satn.ea_elevation import (
    DTM_ENDPOINT as ENDPOINT,
)
from satn.ea_elevation import (
    DTM_LICENCE as LICENCE,
)
from satn.ea_elevation import (
    DTM_TITLE as DATASET_TITLE,
)

SOURCE_ID = "ea-lidar-composite-dtm-1m"
WECA_AUTHORITIES = (
    "Bath and North East Somerset",
    "Bristol",
    "North Somerset",
    "South Gloucestershire",
)
MAX_WCS_ATTEMPTS = 3
MAX_PROGRESS_HEARTBEATS = 20


def route_sample_points(
    path: Path,
    spacing_m: float,
) -> tuple[list[Point], list[str]]:
    """Return tile-deduplicated coordinates from the canonical route sequence."""
    samples, feature_ids = route_samples(path, spacing_m)
    return _acquisition_points(samples), feature_ids


def _acquisition_points(samples: list[dict[str, object]]) -> list[Point]:
    """Deduplicate only WCS coordinate requests, never route observations."""
    points: dict[tuple[float, float], Point] = {}
    for sample in samples:
        point = sample["geometry"]
        if not isinstance(point, Point):
            raise ValueError("canonical EA route sample is not a Point")
        points[(round(point.x, 3), round(point.y, 3))] = point
    return [points[key] for key in sorted(points)]


def route_samples(path: Path, spacing_m: float) -> tuple[list[dict[str, object]], list[str]]:
    """Ordered, source-identifiable samples used for authority accounting.

    Deduplication is intentionally not performed here: an individual route's
    sequence is what proves a transition between authorities.  Acquisition may
    still de-duplicate tile requests and evidence points separately.
    """
    return eligible_route_samples(gpd.read_file(path), spacing_m)


def tile_key(point: Point, tile_size_m: int) -> tuple[int, int]:
    return math.floor(point.x / tile_size_m), math.floor(point.y / tile_size_m)


def build_getcoverage_url(
    east_index: int,
    north_index: int,
    *,
    tile_size_m: int,
    spacing_m: float,
    endpoint: str = ENDPOINT,
) -> str:
    minimum_east = east_index * tile_size_m
    minimum_north = north_index * tile_size_m
    query = urllib.parse.urlencode(
        [
            ("service", "WCS"),
            ("version", "2.0.1"),
            ("request", "GetCoverage"),
            ("coverageId", COVERAGE_ID),
            ("format", "image/tiff"),
            ("subset", f"E({minimum_east},{minimum_east + tile_size_m})"),
            ("subset", f"N({minimum_north},{minimum_north + tile_size_m})"),
            ("scaleFactor", f"{1 / spacing_m:.8f}"),
        ]
    )
    return f"{endpoint}?{query}"


def acquire_tile(
    key: tuple[int, int],
    cache_dir: Path,
    *,
    tile_size_m: int,
    spacing_m: float,
    endpoint: str = ENDPOINT,
    max_attempts: int = MAX_WCS_ATTEMPTS,
) -> tuple[tuple[int, int], Path | None, str, str | None, int, str | None]:
    """Acquire one tile with a bounded failure budget.

    An exhausted WCS request becomes explicit NoData provenance. Its requested
    samples remain in the immutable ledger instead of silently disappearing.
    """
    if not 1 <= max_attempts <= MAX_WCS_ATTEMPTS:
        raise ValueError(f"max_attempts must be between 1 and {MAX_WCS_ATTEMPTS}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"ea-dtm-{key[0]}-{key[1]}-{spacing_m:g}m.tif"
    url = build_getcoverage_url(
        *key,
        tile_size_m=tile_size_m,
        spacing_m=spacing_m,
        endpoint=endpoint,
    )
    failure: str | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            if path.exists():
                # A partial/corrupt cached response must never become durable
                # provenance.  Quarantine it before retrying the official WCS.
                try:
                    load_tile(path)
                except (OSError, ValueError):
                    quarantined = path.with_suffix(path.suffix + ".corrupt")
                    if quarantined.exists():
                        quarantined.unlink()
                    path.replace(quarantined)
            if not path.exists():
                request = urllib.request.Request(url, headers={"User-Agent": "banes-satn/1"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    payload = response.read()
                if not payload.startswith((b"II", b"MM")):
                    raise ValueError("EA WCS did not return a GeoTIFF")
                temporary = path.with_suffix(path.suffix + ".part")
                try:
                    temporary.write_bytes(payload)
                    load_tile(temporary)
                    os.replace(temporary, path)
                finally:
                    if temporary.exists():
                        temporary.unlink()
            load_tile(path)
            return key, path, url, hashlib.sha256(path.read_bytes()).hexdigest(), attempt, None
        except (OSError, ValueError) as error:
            failure = f"{type(error).__name__}: {error}"
            if attempt < max_attempts:
                time.sleep(0.2 * attempt)
    return key, None, url, None, max_attempts, failure


def load_tile(path: Path) -> tuple[np.ndarray, tuple[float, ...]]:
    # Never let Pillow silently synthesise missing raster bytes.  The tile is
    # provenance only after full decode, shape and georeferencing validation.
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    with Image.open(path) as image:
        transform = image.tag_v2.get(34264)
        if not transform or len(transform) != 16:
            raise ValueError(f"GeoTIFF is missing ModelTransformationTag: {path}")
        pixels = np.asarray(image, dtype=float).copy()
    if pixels.ndim != 2 or not pixels.shape[0] or not pixels.shape[1]:
        raise ValueError(f"GeoTIFF has unusable dimensions: {path}")
    if not all(math.isfinite(float(value)) for value in transform):
        raise ValueError(f"GeoTIFF has non-finite ModelTransformationTag: {path}")
    if transform[0] == 0 or transform[5] == 0:
        raise ValueError(f"GeoTIFF has non-invertible ModelTransformationTag: {path}")
    # Force a representative pixel access after decoding, rather than trusting
    # TIFF metadata verification alone.
    _ = float(pixels[0, 0])
    return pixels, tuple(float(value) for value in transform)


def sample_grid(
    grid: tuple[np.ndarray, tuple[float, ...]],
    point: Point,
) -> float | None:
    pixels, transform = grid
    scale_x, scale_y = transform[0], transform[5]
    origin_x, origin_y = transform[3], transform[7]
    column = math.floor((point.x - origin_x) / scale_x)
    row = math.floor((point.y - origin_y) / scale_y)
    column = min(max(column, 0), pixels.shape[1] - 1)
    row = min(max(row, 0), pixels.shape[0] - 1)
    elevation = float(pixels[row, column])
    if not math.isfinite(elevation) or elevation <= -3e38:
        return None
    return elevation


def sample_tile(path: Path, point: Point) -> float | None:
    return sample_grid(load_tile(path), point)


def _normalise_authority(value: object) -> str:
    return " ".join(str(value or "").lower().replace("&", "and").split())


def _authority_column(frame: gpd.GeoDataFrame) -> str:
    for field in ("authority", "authority_name", "name", "name:en"):
        if field in frame.columns:
            return field
    raise ValueError("authority boundaries require authority, authority_name or name")


def _authority_identity(boundaries: gpd.GeoDataFrame, path: Path) -> dict[str, object]:
    """Return a governed, non-name-only authority-boundary identity."""
    if boundaries.crs is None:
        raise ValueError("authority boundaries must declare a CRS")
    name_field = _authority_column(boundaries)
    id_field = next(
        (field for field in ("authority_id", "boundary_id", "id") if field in boundaries), None
    )
    if id_field is None or "source_query" not in boundaries:
        raise ValueError("authority boundaries require stable ID and source_query provenance")
    records = []
    for _, row in boundaries.to_crs(27700).iterrows():
        if row.geometry is None or row.geometry.is_empty:
            continue
        records.append(
            {
                "authority_id": str(row[id_field]),
                "authority": str(row[name_field]),
                "source_query": str(row["source_query"]),
                "geometry": canonical_polygon_geometry(row.geometry),
            }
        )
    if not records:
        raise ValueError("authority boundaries contain no usable polygons")
    encoded = json.dumps(
        sorted(records, key=lambda item: item["authority_id"]),
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "path": path.name,
        "raw_sha256": sha256_file(path),
        "canonical_boundary_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "authority_ids": [
            record["authority_id"]
            for record in sorted(records, key=lambda item: item["authority_id"])
        ],
        "authority_names": [
            record["authority"] for record in sorted(records, key=lambda item: item["authority"])
        ],
        "source_queries": sorted({record["source_query"] for record in records}),
    }


def _authority_geometries(boundaries: gpd.GeoDataFrame) -> dict[str, object]:
    name_field = _authority_column(boundaries)
    authorities = {
        _normalise_authority(row[name_field]): row.geometry
        for _, row in boundaries.to_crs(27700).iterrows()
        if row.geometry is not None and not row.geometry.is_empty
    }
    expected = {_normalise_authority(name): name for name in WECA_AUTHORITIES}
    if missing := [name for key, name in expected.items() if key not in authorities]:
        raise ValueError("WECA authority boundaries are incomplete: " + ", ".join(missing))
    return {key: authorities[key] for key in expected}


def _assigned_samples(
    samples: list[dict[str, object]], boundaries: gpd.GeoDataFrame
) -> list[dict[str, object]]:
    authorities = _authority_geometries(boundaries)
    id_field = next(
        (field for field in ("authority_id", "boundary_id", "id") if field in boundaries), None
    )
    if id_field is None:
        raise ValueError("authority boundaries require a stable authority ID")
    name_field = _authority_column(boundaries)
    authority_ids = {
        _normalise_authority(row[name_field]): str(row[id_field])
        for _, row in boundaries.iterrows()
    }
    assigned = []
    for sample in samples:
        point = sample["geometry"]
        matches = sorted(key for key, geometry in authorities.items() if geometry.covers(point))
        # Borders belong deterministically to one authority; points outside all
        # authorities remain visible in the routing-buffer bucket.
        key = matches[0] if matches else "routing-buffer"
        assigned.append(
            {
                **sample,
                "authority_key": key,
                "authority_id": authority_ids.get(key, "routing-buffer"),
            }
        )
    return assigned


class _SurveyRecord:
    def __init__(
        self, *, geometry: object, feature_id: str, ed_flown: str | None, resolution_m: float
    ) -> None:
        self.geometry = geometry
        self.feature_id = feature_id
        self.ed_flown = ed_flown
        self.resolution_m = resolution_m

    def choice(self) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "ed_flown": self.ed_flown,
            "resolution_m": self.resolution_m,
        }


class _SurveyAttributionIndex:
    """Pre-normalised official survey records with a reusable spatial index."""

    def __init__(self, index: gpd.GeoDataFrame) -> None:
        normalised = index.to_crs(27700)
        records: list[_SurveyRecord] = []
        for position, row in normalised.iterrows():
            feature_id = str(row.get("id") or row.get("polygon_id") or position)
            date = str(row.get("ed_flown") or "")[:10]
            try:
                resolution = float(row.get("resolution"))
            except (TypeError, ValueError):
                resolution = float("inf")
            records.append(
                _SurveyRecord(
                    geometry=row.geometry,
                    feature_id=feature_id,
                    ed_flown=date or None,
                    resolution_m=resolution,
                )
            )
        self._records = tuple(records)
        self._spatial_index = normalised.sindex
        self.candidate_checks = 0

    def choice(self, point: Point) -> dict[str, object] | None:
        """Return the existing deterministic choice without scanning every polygon."""
        positions = self._spatial_index.query(point)
        self.candidate_checks += len(positions)
        matches = [
            self._records[position].choice()
            for position in positions
            if self._records[position].geometry.covers(point)
        ]
        if not matches:
            return None
        # Reversing all sort components would invert feature IDs too; sort by
        # the desired human-readable stable preference directly instead.
        return sorted(
            matches,
            key=lambda row: (
                -(int(str(row["ed_flown"] or "0000-00-00").replace("-", ""))),
                float(row["resolution_m"]),
                str(row["feature_id"]),
            ),
        )[0]


def _survey_choice(point: Point, index: _SurveyAttributionIndex) -> dict[str, object] | None:
    """Choose one official overlapping survey deterministically.

    Newest end-of-flight wins, followed by the finest declared resolution and
    then the immutable official feature ID.  This makes legitimate EA composite
    overlaps auditable rather than treating them as an error.
    """
    return index.choice(point)


def _progress_heartbeat(phase: str, completed: int, total: int) -> None:
    """Print no more than a bounded number of useful long-running progress updates."""
    if total <= 0:
        return
    interval = max(1, math.ceil(total / MAX_PROGRESS_HEARTBEATS))
    if completed == total or completed % interval == 0:
        print(f"[ea-elevation] {phase}: {completed}/{total}", flush=True)


def _survey_choices(
    samples: list[dict[str, object]], index: _SurveyAttributionIndex, *, phase: str
) -> dict[tuple[str, int], dict[str, object] | None]:
    """Attribute every route observation once so ledger and manifest stay aligned."""
    choices: dict[tuple[str, int], dict[str, object] | None] = {}
    for completed, sample in enumerate(samples, start=1):
        choices[_sample_identity(sample)] = _survey_choice(sample["geometry"], index)
        _progress_heartbeat(phase, completed, len(samples))
    return choices


def preflight_weca_coverage(
    route_path: Path,
    authority_boundaries_path: Path,
    survey_index_path: Path,
    *,
    spacing_m: float = 10.0,
) -> dict[str, object]:
    """Prove 10 m route samples have an explicit EA-survey coverage record per authority."""

    routes = gpd.read_file(route_path)
    samples, _feature_ids = eligible_route_samples(routes, spacing_m)
    boundaries = gpd.read_file(authority_boundaries_path)
    boundary_identity = _authority_identity(boundaries, authority_boundaries_path)
    contract = validate_official_weca_survey_index(survey_index_path)
    survey_index = _SurveyAttributionIndex(gpd.read_file(survey_index_path))
    expected = {_normalise_authority(name): name for name in WECA_AUTHORITIES}
    assigned = _assigned_samples(samples, boundaries)
    choices = _survey_choices(assigned, survey_index, phase="checking official survey coverage")
    report: list[dict[str, object]] = []
    for normalised, authority_name in expected.items():
        authority_samples = [row for row in assigned if row["authority_key"] == normalised]
        missing = [
            sample
            for sample in authority_samples
            if choices[_sample_identity(sample)] is None
        ]
        available = len(authority_samples) - len(missing)
        status = (
            "available"
            if authority_samples and not missing
            else "partial"
            if available
            else "unavailable"
        )
        report.append(
            {
                "authority": authority_name,
                "status": status,
                "route_sample_count": len(authority_samples),
                "requested_sample_count": len(authority_samples),
                "available_sample_count": available,
                "nodata_sample_count": len(missing),
                "missing_sample_count": len(missing),
            }
        )
    return {
        "official_survey_index": contract,
        "authority_boundaries": boundary_identity,
        "routing_buffer_sample_count": sum(
            row["authority_key"] == "routing-buffer" for row in assigned
        ),
        "authorities": report,
        "status": (
            "available"
            if report and all(row["status"] == "available" for row in report)
            else "partial"
            if any(row["available_sample_count"] for row in report)
            else "unavailable"
        ),
    }


def validate_weca_route_extent(route_path: Path, *, routing_buffer_m: float) -> None:
    """Bind the pinned WECA WFS subset to exact retained routes and 15 km buffer."""
    if routing_buffer_m != WECA_ROUTING_BUFFER_M:
        raise ValueError(
            f"WECA routing buffer must be exactly {WECA_ROUTING_BUFFER_M:g}m"
        )
    routes = gpd.read_file(route_path)
    samples, _ = eligible_route_samples(routes, spacing_m=10.0)
    actual_extent = (
        min(float(sample["geometry"].x) for sample in samples),
        min(float(sample["geometry"].y) for sample in samples),
        max(float(sample["geometry"].x) for sample in samples),
        max(float(sample["geometry"].y) for sample in samples),
    )
    if any(
        not math.isclose(actual, pinned, abs_tol=0.001)
        for actual, pinned in zip(actual_extent, WECA_PINNED_ELIGIBLE_ROUTE_BBOX, strict=True)
    ):
        raise ValueError(
            "WECA retained eligible-route extent differs from the pinned survey-index contract; "
            "derive and independently validate a new WFS subset"
        )
    if governed_survey_request_bbox(routes, routing_buffer_m=routing_buffer_m) != tuple(
        int(value) for value in WECA_SURVEY_REQUEST_BBOX
    ):
        raise ValueError("WECA survey request does not cover exact routes plus 15km buffer")


def validate_weca_samples(
    samples: list[dict[str, object]],
    sampled: dict[tuple[str, int], float | None],
    authority_boundaries_path: Path,
    *,
    routing_buffer_m: float,
) -> dict[str, object]:
    """Report actual WCS availability; survey-index coverage is only a preflight."""
    boundaries = gpd.read_file(authority_boundaries_path)
    boundary_identity = _authority_identity(boundaries, authority_boundaries_path)
    expected = {_normalise_authority(name): name for name in WECA_AUTHORITIES}
    authorities = _authority_geometries(boundaries)
    assigned = _assigned_samples(samples, boundaries)
    union = unary_union(list(authorities.values()))
    for sample in assigned:
        if (
            sample["authority_key"] == "routing-buffer"
            and sample["geometry"].distance(union) > routing_buffer_m
        ):
            raise ValueError("routing-buffer sample exceeds governed 15km authority limit")
    rows: list[dict[str, object]] = []
    cross_boundary_points = 0
    for normalised, authority in expected.items():
        authority_samples = [row for row in assigned if row["authority_key"] == normalised]
        nodata = sum(sampled[_sample_identity(row)] is None for row in authority_samples)
        available = len(authority_samples) - nodata
        if authority_samples and nodata == 0:
            status = "available"
        elif available:
            status = "partial"
        else:
            status = "unavailable"
        rows.append(
            {
                "authority": authority,
                "status": status,
                "route_sample_count": len(authority_samples),
                "requested_sample_count": len(authority_samples),
                "available_sample_count": available,
                "nodata_sample_count": nodata,
            }
        )
    transitions: list[dict[str, object]] = []
    for route_id in sorted({str(row["route_id"]) for row in assigned}):
        sequence = [row for row in assigned if row["route_id"] == route_id]
        sequence.sort(key=lambda row: int(row["sample_index"]))
        for before, after in pairwise(sequence):
            if before["authority_key"] == after["authority_key"] or "routing-buffer" in {
                before["authority_key"],
                after["authority_key"],
            }:
                continue
            before_available = sampled[_sample_identity(before)] is not None
            after_available = sampled[_sample_identity(after)] is not None
            transitions.append(
                {
                    "route_id": route_id,
                    "before_sample_index": before["sample_index"],
                    "after_sample_index": after["sample_index"],
                    "from_authority": before["authority_key"],
                    "to_authority": after["authority_key"],
                    "status": (
                        "available" if before_available and after_available else "missing-elevation"
                    ),
                }
            )
            if before_available and after_available:
                cross_boundary_points += 1
    buffer_samples = [row for row in assigned if row["authority_key"] == "routing-buffer"]
    buffer_nodata = sum(sampled[_sample_identity(row)] is None for row in buffer_samples)
    rows.append(
        {
            "authority": "routing-buffer/outside-authority",
            "status": "available"
            if buffer_samples and buffer_nodata == 0
            else "partial"
            if len(buffer_samples) - buffer_nodata
            else "unavailable",
            "route_sample_count": len(buffer_samples),
            "requested_sample_count": len(buffer_samples),
            "available_sample_count": len(buffer_samples) - buffer_nodata,
            "nodata_sample_count": buffer_nodata,
        }
    )
    return {
        "routing_buffer_m": routing_buffer_m,
        "authorities": rows,
        "authority_boundaries": boundary_identity,
        "cross_boundary_sample_count": cross_boundary_points,
        "cross_boundary_transitions": transitions,
        "status": (
            "available"
            if all(row["status"] == "available" for row in rows)
            else "partial"
            if any(row["available_sample_count"] for row in rows)
            else "unavailable"
        ),
    }


def _sample_identity(sample: dict[str, object]) -> tuple[str, int]:
    return str(sample["route_id"]), int(sample["sample_index"])


def write_evidence(
    route_path: Path,
    output_path: Path,
    cache_dir: Path,
    *,
    spacing_m: float = 10.0,
    tile_size_m: int = 5000,
    workers: int = 4,
    endpoint: str = ENDPOINT,
    authority_boundaries_path: Path | None = None,
    survey_index_path: Path | None = None,
    require_weca_preflight: bool = False,
    routing_buffer_m: float = 15_000,
    governed_input_fingerprint: str | None = None,
    max_wcs_attempts: int = MAX_WCS_ATTEMPTS,
) -> dict[str, object]:
    if require_weca_preflight and (authority_boundaries_path is None or survey_index_path is None):
        raise ValueError(
            "WECA elevation acquisition requires authority boundaries and an EA survey index"
        )
    if (authority_boundaries_path is None) != (survey_index_path is None):
        raise ValueError("authority boundaries and EA survey index must be provided together")
    if require_weca_preflight and endpoint != ENDPOINT:
        raise ValueError("WECA elevation acquisition requires the governed EA WCS endpoint")
    if require_weca_preflight:
        validate_weca_route_extent(route_path, routing_buffer_m=routing_buffer_m)
    if require_weca_preflight and (
        not isinstance(governed_input_fingerprint, str) or len(governed_input_fingerprint) != 64
    ):
        raise ValueError("WECA elevation acquisition requires the governed input fingerprint")
    preflight = (
        preflight_weca_coverage(
            route_path,
            authority_boundaries_path,
            survey_index_path,
            spacing_m=spacing_m,
        )
        if authority_boundaries_path is not None and survey_index_path is not None
        else None
    )
    # Tile reads may be deduplicated by coordinate, but accounting and evidence
    # are deliberately per (route_id, sample_index): coincident route endpoints
    # are distinct observations in the strategic network.
    ordered_samples, feature_ids = route_samples(route_path, spacing_m)
    points = _acquisition_points(ordered_samples)
    keys = sorted({tile_key(point, tile_size_m) for point in points})
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                acquire_tile,
                key,
                cache_dir,
                tile_size_m=tile_size_m,
                spacing_m=spacing_m,
                endpoint=endpoint,
                max_attempts=max_wcs_attempts,
            )
            for key in keys
        ]
        acquired = []
        for completed, future in enumerate(as_completed(futures), start=1):
            acquired.append(future.result())
            _progress_heartbeat("acquiring EA WCS tiles", completed, len(futures))
    tiles = {
        key: path for key, path, _url, _digest, _attempts, _failure in acquired if path is not None
    }
    grids = {key: load_tile(path) for key, path in tiles.items()}
    rows = []
    elevations_by_coordinate: dict[tuple[float, float], float | None] = {}
    for point in points:
        grid = grids.get(tile_key(point, tile_size_m))
        elevation = sample_grid(grid, point) if grid is not None else None
        elevations_by_coordinate[(round(point.x, 3), round(point.y, 3))] = elevation
    sampled: dict[tuple[str, int], float | None] = {}
    evidence_hashes: dict[tuple[str, int], str] = {}
    for sample in ordered_samples:
        point = sample["geometry"]
        elevation = elevations_by_coordinate[(round(point.x, 3), round(point.y, 3))]
        identity = _sample_identity(sample)
        sampled[identity] = elevation
        if elevation is None:
            continue
        east_mm, north_mm = round(point.x * 1000), round(point.y * 1000)
        row_hash = evidence_row_sha256(
            route_id=identity[0],
            sample_index=identity[1],
            east_mm=east_mm,
            north_mm=north_mm,
            elevation_m=elevation,
        )
        evidence_hashes[identity] = row_hash
        rows.append(
            {
                "evidence_id": f"ea-dtm-{identity[0]}-{identity[1]}",
                "route_id": identity[0],
                "sample_index": identity[1],
                "elevation_m": round(elevation, 3),
                "source_resolution_m": 1.0,
                "output_sample_spacing_m": spacing_m,
                "evidence_row_sha256": row_hash,
                "geometry": point,
            }
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    evidence = gpd.GeoDataFrame(
        rows,
        columns=(
            "evidence_id",
            "route_id",
            "sample_index",
            "elevation_m",
            "source_resolution_m",
            "output_sample_spacing_m",
            "evidence_row_sha256",
            "geometry",
        ),
        geometry="geometry",
        crs=27700,
    ).to_crs(4326)
    evidence.sort_values("evidence_id").to_file(output_path, driver="GeoJSON")
    route_copy = output_path.with_name(f"{output_path.stem}.sampled-routes.geojson")
    if route_copy.resolve() != route_path.resolve():
        shutil.copy2(route_path, route_copy)
    # An acquisition directory may contain multiple outputs.  The sidecar owns
    # an output-specific sibling; snapshotting normalises the internal name.
    ledger_path = output_path.with_name(f"{output_path.stem}.sample-ledger.jsonl")
    ledger_rows: list[dict[str, object]] = []
    survey_choices: dict[tuple[str, int], dict[str, object] | None] | None = None
    if authority_boundaries_path is not None and survey_index_path is not None:
        assigned = _assigned_samples(ordered_samples, gpd.read_file(authority_boundaries_path))
        survey_index = _SurveyAttributionIndex(gpd.read_file(survey_index_path))
        survey_choices = _survey_choices(
            ordered_samples, survey_index, phase="attributing official EA surveys"
        )
        by_route: dict[str, list[dict[str, object]]] = {}
        for sample in assigned:
            by_route.setdefault(str(sample["route_id"]), []).append(sample)
        for _route_id, route_samples_for_ledger in sorted(by_route.items()):
            route_samples_for_ledger.sort(key=lambda item: int(item["sample_index"]))
            for route_position, sample in enumerate(route_samples_for_ledger):
                identity = _sample_identity(sample)
                point = sample["geometry"]
                chosen = survey_choices[identity]
                ledger_rows.append(
                    {
                        "schema_version": "ea-lidar-sample-ledger/v1",
                        "route_id": identity[0],
                        "sample_index": identity[1],
                        "route_position": route_position,
                        "previous_sample_index": (
                            route_samples_for_ledger[route_position - 1]["sample_index"]
                            if route_position
                            else None
                        ),
                        "next_sample_index": (
                            route_samples_for_ledger[route_position + 1]["sample_index"]
                            if route_position + 1 < len(route_samples_for_ledger)
                            else None
                        ),
                        "east_mm": round(point.x * 1000),
                        "north_mm": round(point.y * 1000),
                        "authority_id": sample["authority_id"],
                        "bucket": "authority"
                        if sample["authority_id"] != "routing-buffer"
                        else "routing-buffer",
                        "availability": "available" if sampled[identity] is not None else "nodata",
                        "elevation_m": (
                            round(float(sampled[identity]), 3)
                            if sampled[identity] is not None
                            else None
                        ),
                        "survey_feature_id": chosen["feature_id"] if chosen else None,
                        "ed_flown": chosen["ed_flown"] if chosen else None,
                        "resolution_m": chosen["resolution_m"] if chosen else None,
                        "evidence_row_sha256": evidence_hashes.get(identity),
                    }
                )
        ledger_sha256 = write_sample_ledger(ledger_path, ledger_rows)
    else:
        ledger_sha256 = None
    sample_validation = (
        validate_weca_samples(
            ordered_samples,
            sampled,
            authority_boundaries_path,
            routing_buffer_m=routing_buffer_m,
        )
        if authority_boundaries_path is not None
        else None
    )
    manifest: dict[str, object] = {
        "source_id": SOURCE_ID,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "dataset_title": DATASET_TITLE,
        "coverage_id": COVERAGE_ID,
        "endpoint": endpoint,
        "licence": LICENCE,
        "attribution": ATTRIBUTION,
        "source_resolution_m": 1,
        "output_sample_spacing_m": spacing_m,
        "tile_size_m": tile_size_m,
        "eligible_feature_types": sorted(ELIGIBLE_FEATURE_TYPES),
        "route_feature_count": len(feature_ids),
        "requested_point_count": len(ordered_samples),
        "evidence_sample_count": len(rows),
        "nodata_sample_count": len(ordered_samples) - len(rows),
        "output_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "sample_ledger_path": ledger_path.name if ledger_sha256 else None,
        "sample_ledger_sha256": ledger_sha256,
        "sample_ledger_schema_version": "ea-lidar-sample-ledger/v1" if ledger_sha256 else None,
        "sample_route_path": route_copy.name if ledger_sha256 else None,
        "sample_route_sha256": sha256_file(route_copy) if ledger_sha256 else None,
        "pre_elevation_network_sha256": eligible_route_fingerprint(gpd.read_file(route_path)),
        "acquisition_protocol": "two-pass-fixed-point/v1",
        "survey_coverage_preflight": preflight,
        "sample_validation": sample_validation,
        "effective_survey_date": (
            _effective_survey_date(survey_choices.values(), authority_boundaries_path)
            if authority_boundaries_path is not None and survey_choices is not None
            else None
        ),
        "survey_index_path": (survey_index_path.name if survey_index_path is not None else None),
        "survey_index_sha256": (
            preflight["official_survey_index"]["raw_sha256"] if preflight is not None else None
        ),
        "survey_index_feature_sha256": (
            preflight["official_survey_index"]["canonical_feature_sha256"]
            if preflight is not None
            else None
        ),
        "governed_input_fingerprint": governed_input_fingerprint,
        "authority_boundaries_path": (
            authority_boundaries_path.name if authority_boundaries_path is not None else None
        ),
        "vertical_accuracy": DTM_VERTICAL_ACCURACY,
        "requests": [
            {
                "tile": list(key),
                "url": url,
                "sha256": digest,
                "status": "available" if path is not None else "exhausted-nodata",
                "attempts": attempts,
                "failure": failure,
            }
            for key, path, url, digest, attempts, failure in sorted(acquired)
        ],
    }
    output_path.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _effective_survey_date(
    choices: Iterable[dict[str, object] | None], authority_boundaries_path: Path
) -> str | None:
    """Latest date from the same selected attributions written to the ledger."""
    # Boundaries are loaded here to ensure this computation is only possible with
    # governed authority scope, not caller-supplied dates.
    _authority_identity(gpd.read_file(authority_boundaries_path), authority_boundaries_path)
    # It is the latest deterministically-selected official survey date for all
    # requested points, including explicit NoData observations.
    dates = [
        str(choice["ed_flown"])
        for choice in choices
        if choice is not None and choice["ed_flown"] is not None
    ]
    return max(dates) if dates else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("routes", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--spacing-m", type=float, default=10.0)
    parser.add_argument("--tile-size-m", type=int, default=5000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--authority-boundaries", type=Path)
    parser.add_argument("--survey-index", type=Path)
    parser.add_argument("--weca-preflight", action="store_true")
    parser.add_argument("--routing-buffer-m", type=float, default=15_000)
    parser.add_argument("--governed-input-fingerprint")
    parser.add_argument("--max-wcs-attempts", type=int, default=MAX_WCS_ATTEMPTS)
    args = parser.parse_args()
    manifest = write_evidence(
        args.routes,
        args.output,
        args.cache_dir,
        spacing_m=args.spacing_m,
        tile_size_m=args.tile_size_m,
        workers=args.workers,
        authority_boundaries_path=args.authority_boundaries,
        survey_index_path=args.survey_index,
        require_weca_preflight=args.weca_preflight,
        routing_buffer_m=args.routing_buffer_m,
        governed_input_fingerprint=args.governed_input_fingerprint,
        max_wcs_attempts=args.max_wcs_attempts,
    )
    print(
        f"Wrote {manifest['evidence_sample_count']} governed elevation samples "
        f"from {len(manifest['requests'])} EA WCS tiles."
    )


if __name__ == "__main__":
    main()
