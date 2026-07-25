"""The pinned Environment Agency DTM contract used by WECA acquisitions.

This module deliberately contains the external contract in one place.  In
particular, a GeoJSON that merely *claims* to describe the EA survey index is
not evidence: the downloaded WFS response must match the known official bytes
and its features are then summarised in a deterministic, reviewable digest.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge

DTM_DATASET_ID = "13787b9a-26a4-4775-8523-806d13af58fc"
DTM_COVERAGE_ID = f"{DTM_DATASET_ID}__Lidar_Composite_Elevation_DTM_1m"
DTM_ENDPOINT = f"https://environment.data.gov.uk/geoservices/datasets/{DTM_DATASET_ID}/wcs"
DTM_TITLE = "LIDAR Composite Digital Terrain Model (DTM) - 1m"
DTM_LICENCE = "Open Government Licence v3.0"
DTM_ATTRIBUTION = (
    "Contains Environment Agency information © Environment Agency and/or database right. "
    "All rights reserved."
)
DTM_VERTICAL_ACCURACY = "+/-15cm RMSE"
SURVEY_WFS_ENDPOINT = "https://environment.data.gov.uk/spatialdata/survey-index-files/wfs"
SURVEY_DATASET_ID = "9f0fa3fc-a860-4729-adc9-47fe53f658d0"
SURVEY_LAYER = f"dataset-{SURVEY_DATASET_ID}:LIDAR_Composite_1m_DTM_2022_extents"
# This is the exact EPSG:27700 envelope of the governed, elevation-eligible
# WECA route sequence used to produce the pinned index.  A 15 km routing buffer
# is applied below.  It is deliberately *not* an authority-envelope shortcut:
# when the retained route semantics change, a new index contract is required.
WECA_PINNED_ELIGIBLE_ROUTE_BBOX = (
    321127.0028859009,
    149191.3835381674,
    382867.65168800263,
    200090.446154786,
)
WECA_ROUTING_BUFFER_M = 15_000.0
# The request uses the exact eligible-route extent plus its governed buffer.
# The returned survey polygons legitimately extend beyond it, so their envelope
# is pinned separately and must never be reused as the request bbox.
WECA_SURVEY_REQUEST_BBOX = (
    306127,
    134191,
    397868,
    215091,
)
WECA_SURVEY_BBOX = (299754.1815, 124750.0, 400250.0, 225250.0)
WECA_SURVEY_INDEX_FEATURE_SHA256 = (
    "fa4b7b78d7adfb865166d7da161261b0134b98a9a909b5cc6fa5203b5d8ccd72"
)
WECA_SURVEY_INDEX_FEATURE_COUNT = 1931
# The WFS response contains a generated timestamp, so its byte hash is recorded
# for each acquisition but is deliberately not its long-lived identity.
WECA_SURVEY_REQUEST = {
    "service": "WFS",
    "version": "2.0.0",
    "request": "GetFeature",
    "typeNames": SURVEY_LAYER,
    "srsName": "EPSG:27700",
    "bbox": ("306127,134191,397868,215091,EPSG:27700"),
}
# Dataset-declared composite period.  It is intentionally distinct from the
# feature-derived dates in any particular WFS subset.
DATASET_DECLARED_SURVEY_START = "2000-06-06"
DATASET_DECLARED_SURVEY_END = "2022-04-02"
CONTRACT_SCHEMA_VERSION = "ea-lidar-composite-dtm-contract/v2"
SURVEY_FIELDS = (
    "id",
    "filename",
    "tilename",
    "polygon_id",
    "resolution",
    "year",
    "od_dtm_fn",
    "sd_flown",
    "ed_flown",
)
ELIGIBLE_FEATURE_TYPES = frozenset(
    {
        "strategic-spine",
        "spine-access-connection",
        "school-access-connection",
        "branch-meeting-connection",
        "urban-spine",
    }
)
SAMPLE_LEDGER_SCHEMA_VERSION = "ea-lidar-sample-ledger/v1"
SAMPLE_LEDGER_FILENAME = "ea-elevation-sample-ledger.jsonl"
EA_ELEVATION_EVIDENCE_FIELDS = (
    "evidence_id",
    "source_id",
    "effective_date",
    "licence",
    "elevation_m",
    "route_id",
    "sample_index",
    "evidence_row_sha256",
    "source_resolution_m",
    "output_sample_spacing_m",
)


def canonical_json_sha256(value: object) -> str:
    """Hash compact canonical JSON used for retained EA metadata only."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def canonical_ea_elevation_evidence_bytes(
    evidence: gpd.GeoDataFrame,
    *,
    source_id: str,
    licence: str,
    effective_date: str,
    source_resolution_m: float,
    output_sample_spacing_m: float,
) -> bytes:
    """Return the sole retained GeoJSON form for governed EA observations.

    This deliberately binds every evidence property that affects provenance or
    interpretation as well as the Point geometry.  Hashes in a mutable
    snapshot manifest are therefore corroborative, not the only witness of
    the retained output.
    """
    if evidence.crs is None:
        raise ValueError("EA Elevation Evidence has no CRS")
    unexpected = set(evidence.columns) - {*EA_ELEVATION_EVIDENCE_FIELDS, "geometry"}
    missing = set(EA_ELEVATION_EVIDENCE_FIELDS) - set(evidence.columns)
    if missing or unexpected:
        details = ", ".join(sorted(missing or unexpected))
        raise ValueError(f"EA Elevation Evidence has non-governed schema fields: {details}")
    if evidence.empty:
        raise ValueError("EA Elevation Evidence must contain retained observations")
    expected_metadata = {
        "source_id": source_id,
        "licence": licence,
        "effective_date": effective_date,
        "source_resolution_m": float(source_resolution_m),
        "output_sample_spacing_m": float(output_sample_spacing_m),
    }
    features: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for _, row in evidence.to_crs(4326).iterrows():
        evidence_id = str(row["evidence_id"])
        if not evidence_id.strip() or evidence_id in seen_ids:
            raise ValueError("EA Elevation Evidence has invalid or duplicate evidence_id")
        seen_ids.add(evidence_id)
        geometry = row.geometry
        if not isinstance(geometry, Point) or geometry.is_empty:
            raise ValueError("EA Elevation Evidence requires non-empty Point observations")
        if not all(math.isfinite(value) for value in (geometry.x, geometry.y)):
            raise ValueError("EA Elevation Evidence has non-finite Point coordinates")
        try:
            sample_index = int(row["sample_index"])
            elevation_m = round(float(row["elevation_m"]), 3)
        except (TypeError, ValueError) as error:
            raise ValueError("EA Elevation Evidence has invalid numeric observations") from error
        if sample_index < 0 or not math.isfinite(elevation_m):
            raise ValueError("EA Elevation Evidence has invalid numeric observations")
        properties = {
            "evidence_id": evidence_id,
            "source_id": str(row["source_id"]),
            "effective_date": str(row["effective_date"]).split(" ", maxsplit=1)[0],
            "licence": str(row["licence"]),
            "elevation_m": elevation_m,
            "route_id": str(row["route_id"]),
            "sample_index": sample_index,
            "evidence_row_sha256": str(row["evidence_row_sha256"]),
            "source_resolution_m": float(row["source_resolution_m"]),
            "output_sample_spacing_m": float(row["output_sample_spacing_m"]),
        }
        if (
            not properties["route_id"].strip()
            or len(properties["evidence_row_sha256"]) != 64
            or any(
                not math.isfinite(properties[field])
                for field in ("source_resolution_m", "output_sample_spacing_m")
            )
        ):
            raise ValueError("EA Elevation Evidence has invalid governed observation fields")
        for field, expected in expected_metadata.items():
            if properties[field] != expected:
                raise ValueError(f"EA Elevation Evidence mismatches governed {field}")
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(geometry.x), float(geometry.y)],
                },
            }
        )
    payload = {
        "type": "FeatureCollection",
        "features": sorted(features, key=lambda item: str(item["properties"]["evidence_id"])),
    }
    return (
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def evidence_row_sha256(
    *, route_id: str, sample_index: int, east_mm: int, north_mm: int, elevation_m: float
) -> str:
    """Stable identity for one retained elevation observation, independent of GeoJSON bytes."""
    return canonical_json_sha256(
        {
            "route_id": route_id,
            "sample_index": sample_index,
            "east_mm": east_mm,
            "north_mm": north_mm,
            "elevation_m": round(float(elevation_m), 3),
        }
    )


def write_sample_ledger(path: Path, rows: list[dict[str, object]]) -> str:
    """Write the ordered immutable sample ledger and return its byte digest."""
    ordered = sorted(rows, key=lambda row: (str(row["route_id"]), int(row["sample_index"])))
    if len({(str(row["route_id"]), int(row["sample_index"])) for row in ordered}) != len(ordered):
        raise ValueError("EA sample ledger has duplicate route/sample identities")
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        for row in ordered
    )
    path.write_text(payload, encoding="utf-8")
    return sha256_file(path)


def read_sample_ledger(path: Path) -> list[dict[str, object]]:
    """Fail closed on malformed or non-canonical retained ledger rows."""
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except json.JSONDecodeError as error:
        raise ValueError("EA sample ledger is invalid JSONL") from error
    if not rows:
        raise ValueError("EA sample ledger is empty")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("EA sample ledger has invalid rows")
    if rows != sorted(
        rows, key=lambda row: (str(row.get("route_id")), int(row.get("sample_index", -1)))
    ):
        raise ValueError("EA sample ledger rows are not in canonical route order")
    identities = [(str(row.get("route_id")), int(row.get("sample_index", -1))) for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("EA sample ledger has duplicate route/sample identities")
    return rows


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def eligible_route_samples(
    routes: gpd.GeoDataFrame, spacing_m: float = 10.0
) -> tuple[list[dict[str, object]], list[str]]:
    """Return the one canonical ordered 10 m sequence for retained EA routes.

    Both acquisition and snapshot validation consume this routine.  The rows are
    deliberately *not* coordinate-deduplicated: route/sample identity proves
    coverage and boundary transitions.  The only safe ordering is stable route
    identifier then its source-line sequence.
    """
    if routes.crs is None:
        raise ValueError("EA sampled routes require a CRS")
    if not math.isfinite(spacing_m) or spacing_m <= 0:
        raise ValueError("EA route sample spacing must be positive")
    samples: list[dict[str, object]] = []
    feature_ids: set[str] = set()
    for position, row in routes.to_crs(27700).iterrows():
        if row.get("feature_type") not in ELIGIBLE_FEATURE_TYPES or pd.isna(
            row.get("topography_profile_id")
        ):
            continue
        route_id = str(row.get("feature_id") or row.get("id") or position)
        geometry = row.geometry
        if isinstance(geometry, MultiLineString):
            geometry = linemerge(geometry)
        lines = list(geometry.geoms) if isinstance(geometry, MultiLineString) else [geometry]
        sample_index = 0
        for line in lines:
            if not isinstance(line, LineString) or line.is_empty:
                continue
            distance = 0.0
            while distance < line.length:
                samples.append(
                    {
                        "route_id": route_id,
                        "sample_index": sample_index,
                        "geometry": line.interpolate(distance),
                    }
                )
                sample_index += 1
                distance += spacing_m
            samples.append(
                {
                    "route_id": route_id,
                    "sample_index": sample_index,
                    "geometry": line.interpolate(line.length),
                }
            )
            sample_index += 1
        feature_ids.add(route_id)
    ordered = sorted(samples, key=lambda item: (str(item["route_id"]), int(item["sample_index"])))
    return ordered, sorted(feature_ids)


def route_sample_extent(
    routes: gpd.GeoDataFrame, *, routing_buffer_m: float
) -> tuple[float, float, float, float]:
    """Return the exact governed eligible-route envelope expanded by its buffer."""
    if routing_buffer_m < 0 or not math.isfinite(routing_buffer_m):
        raise ValueError("EA routing buffer must be a non-negative finite distance")
    samples, _ = eligible_route_samples(routes, spacing_m=10.0)
    if not samples:
        raise ValueError("EA survey request requires eligible retained routes")
    west = min(float(sample["geometry"].x) for sample in samples) - routing_buffer_m
    south = min(float(sample["geometry"].y) for sample in samples) - routing_buffer_m
    east = max(float(sample["geometry"].x) for sample in samples) + routing_buffer_m
    north = max(float(sample["geometry"].y) for sample in samples) + routing_buffer_m
    return (west, south, east, north)


def governed_survey_request_bbox(
    routes: gpd.GeoDataFrame, *, routing_buffer_m: float
) -> tuple[int, int, int, int]:
    """Outward-rounded WFS bbox for the exact retained routes plus buffer."""
    west, south, east, north = route_sample_extent(routes, routing_buffer_m=routing_buffer_m)
    return (math.floor(west), math.floor(south), math.ceil(east), math.ceil(north))


def eligible_route_fingerprint(routes: gpd.GeoDataFrame) -> str:
    """Canonical identity of the exact elevation-eligible published routes.

    It deliberately does not use WKB: GEOS byte representation and line direction
    are implementation details.  Coordinates are EPSG:27700 metres rounded to a
    millimetre; ``-0.0`` is normalised; each line is direction-normalised; multipart
    components are sorted.  Stable source feature IDs retain material identity.
    """
    if routes.crs is None:
        raise ValueError("elevation route fingerprint requires a CRS")
    records: list[dict[str, object]] = []
    for position, row in routes.to_crs(27700).iterrows():
        if row.get("feature_type") not in ELIGIBLE_FEATURE_TYPES:
            continue
        if row.get("topography_profile_id") is None:
            continue
        records.append(
            {
                "feature_id": str(row.get("feature_id") or row.get("id") or position),
                "feature_type": str(row["feature_type"]),
                "topography_profile_id": str(row["topography_profile_id"]),
                "geometry": _canonical_line_geometry(row.geometry),
            }
        )
    encoded = json.dumps(
        sorted(records, key=lambda record: (record["feature_type"], record["feature_id"])),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def canonical_survey_index(index: gpd.GeoDataFrame) -> str:
    """Hash the official feature identities and coverage geometry, not metadata claims."""
    if index.crs is None or index.empty:
        raise ValueError("EA survey index must contain CRS-tagged coverage geometry")
    if not index.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("EA survey index requires Polygon or MultiPolygon coverage geometry")
    records: list[dict[str, Any]] = []
    for position, row in index.to_crs(27700).iterrows():
        properties = {
            field: (None if field not in index else _json_value(row.get(field)))
            for field in SURVEY_FIELDS
        }
        records.append(
            {
                "feature_id": str(row.get("id") or position),
                "properties": properties,
                "geometry": canonical_polygon_geometry(row.geometry),
            }
        )
    encoded = json.dumps(
        sorted(records, key=lambda record: record["feature_id"]),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_survey_index(
    path: Path,
    *,
    expected_feature_sha256: str,
    expected_feature_count: int,
    expected_layer: str,
    expected_bbox: tuple[float, float, float, float],
    expected_resolution_m: float | None = None,
) -> dict[str, object]:
    """Validate a WFS feature identity without treating volatile bytes as identity."""
    index = gpd.read_file(path)
    missing = [field for field in SURVEY_FIELDS[1:] if field not in index.columns]
    if missing:
        raise ValueError("EA survey index is missing official fields: " + ", ".join(missing))
    dates = index["sd_flown"].dropna().map(_date_only)
    ends = index["ed_flown"].dropna().map(_date_only)
    if dates.empty or ends.empty:
        raise ValueError("EA survey index is missing survey flight dates")
    canonical = canonical_survey_index(index)
    if len(index) != expected_feature_count or canonical != expected_feature_sha256:
        raise ValueError("EA survey index does not match the pinned canonical feature identity")
    resolutions = {float(value) for value in index["resolution"].dropna()}
    return {
        "wfs_endpoint": SURVEY_WFS_ENDPOINT,
        "dataset_id": SURVEY_DATASET_ID,
        "layer": expected_layer,
        "bbox_epsg27700": list(expected_bbox),
        "raw_sha256": sha256_file(path),
        "canonical_feature_sha256": canonical,
        "feature_count": len(index),
        "dataset_resolution_m": expected_resolution_m,
        "feature_resolutions_m": sorted(resolutions),
        "feature_survey_start": min(dates),
        "feature_survey_end": max(ends),
        "dataset_declared_survey_start": DATASET_DECLARED_SURVEY_START,
        "dataset_declared_survey_end": DATASET_DECLARED_SURVEY_END,
        "declared_period_matches_feature_extrema": (
            min(dates) == DATASET_DECLARED_SURVEY_START and max(ends) == DATASET_DECLARED_SURVEY_END
        ),
    }


def validate_official_weca_survey_index(path: Path) -> dict[str, object]:
    """Validate a re-fetched official WECA WFS response by feature identity.

    Exact request identity is ``WECA_SURVEY_REQUEST``.  The volatile response-byte
    digest is retained in provenance as ``raw_sha256`` but never used as a release
    identity.
    """
    return validate_survey_index(
        path,
        expected_feature_sha256=WECA_SURVEY_INDEX_FEATURE_SHA256,
        expected_feature_count=WECA_SURVEY_INDEX_FEATURE_COUNT,
        expected_layer=SURVEY_LAYER,
        expected_bbox=WECA_SURVEY_BBOX,
        expected_resolution_m=1,
    )


def _json_value(value: object) -> object:
    if value is None:
        return None
    try:
        if bool(value != value):
            return None
    except TypeError:
        pass
    return str(value)


def _date_only(value: object) -> str:
    return str(value).split(" ", maxsplit=1)[0].split("T", maxsplit=1)[0]


def _canonical_number(value: float) -> float:
    rounded = round(float(value), 3)
    return 0.0 if rounded == 0 else rounded


def _canonical_line_geometry(geometry: object) -> list[list[list[float]]]:
    if geometry is None or geometry.is_empty:
        raise ValueError("elevation route fingerprint requires non-empty line geometry")
    geometries = list(geometry.geoms) if geometry.geom_type == "MultiLineString" else [geometry]
    lines: list[list[list[float]]] = []
    for line in geometries:
        if line.geom_type != "LineString":
            raise ValueError("elevation route fingerprint requires LineString geometry")
        coordinates = [[_canonical_number(x), _canonical_number(y)] for x, y, *_ in line.coords]
        if coordinates[::-1] < coordinates:
            coordinates.reverse()
        lines.append(coordinates)
    return sorted(lines)


def canonical_polygon_geometry(geometry: object) -> list[list[list[list[float]]]]:
    """Return an EPSG:27700, precision-bound canonical Polygon/MultiPolygon.

    This deliberately avoids WKB.  GEOS is free to choose ring starts,
    direction, and multipart order, none of which are coverage semantics.
    """
    if geometry is None or geometry.is_empty:
        raise ValueError("EA survey index requires non-empty coverage geometry")
    polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
    canonical: list[list[list[list[float]]]] = []
    for polygon in polygons:
        if polygon.geom_type != "Polygon":
            raise ValueError("EA survey index requires Polygon or MultiPolygon coverage geometry")
        exterior = _canonical_ring(polygon.exterior.coords)
        interiors = sorted(_canonical_ring(ring.coords) for ring in polygon.interiors)
        canonical.append([exterior, *interiors])
    return sorted(canonical)


def _canonical_ring(coordinates: object) -> list[list[float]]:
    points = [[_canonical_number(x), _canonical_number(y)] for x, y, *_ in coordinates]  # type: ignore[union-attr]
    if len(points) < 4:
        raise ValueError("EA survey index requires closed polygon rings")
    if points[0] == points[-1]:
        points.pop()
    if len(points) < 3:
        raise ValueError("EA survey index requires non-degenerate polygon rings")

    def rotate(values: list[list[float]]) -> list[list[float]]:
        start = min(range(len(values)), key=lambda index: values[index])
        return values[start:] + values[:start]

    forward = rotate(points)
    reverse = rotate(list(reversed(points)))
    chosen = min(forward, reverse)
    return [*chosen, chosen[0]]
