"""Deterministic local content identities without authentication semantics."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable

from pyproj import CRS
from shapely.geometry import LineString, MultiLineString, Point

CANONICAL_GEOMETRY_VERSION = "satn-network-geometry-v1"
CANONICAL_GEOMETRY_DECIMALS = 9


def canonical_json(value: object) -> str:
    """Return the established byte-compatible canonical JSON representation."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def content_fingerprint(value: object) -> str:
    """Return a local SHA-256 content identity for canonical JSON bytes."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_network_geometry(
    geometry: object,
    crs: object,
) -> dict[str, object]:
    """Return the portable identity representation for governed network geometry.

    Network identities are two-dimensional, quantised to nine decimal places,
    direction-independent for lines, and part-order-independent for multipart
    lines. CRS identity is explicit. Negative zero is represented as positive
    zero. Unsupported geometry types fail closed instead of acquiring an
    accidental representation.
    """

    if crs is None:
        raise ValueError("Canonical network geometry requires a CRS")
    normalized_crs = CRS.from_user_input(crs)
    authority = normalized_crs.to_authority()
    crs_identity = (
        f"{authority[0]}:{authority[1]}"
        if authority is not None
        else normalized_crs.to_wkt(version="WKT2_2019", pretty=False)
    )
    if isinstance(geometry, Point):
        if geometry.is_empty:
            raise ValueError("Canonical network geometry cannot be empty")
        value: dict[str, object] = {
            "type": "Point",
            "coordinates": _canonical_coordinate(geometry.coords[0]),
        }
    elif isinstance(geometry, LineString):
        value = {
            "type": "LineString",
            "coordinates": _canonical_line_coordinates(geometry),
        }
    elif isinstance(geometry, MultiLineString):
        if geometry.is_empty:
            raise ValueError("Canonical network geometry cannot be empty")
        parts = sorted(
            (_canonical_line_coordinates(part) for part in geometry.geoms),
            key=canonical_json,
        )
        value = {"type": "MultiLineString", "coordinates": parts}
    else:
        raise ValueError(
            "Canonical network geometry supports Point, LineString and MultiLineString"
        )
    return {
        "contract": CANONICAL_GEOMETRY_VERSION,
        "crs": crs_identity,
        "dimensions": 2,
        "decimal_places": CANONICAL_GEOMETRY_DECIMALS,
        "geometry": value,
    }


def canonical_network_geometry_fingerprint(geometry: object, crs: object) -> str:
    """Hash governed network geometry through the portable representation."""

    return content_fingerprint(canonical_network_geometry(geometry, crs))


def canonical_undirected_pair(
    left: Iterable[object],
    right: Iterable[object],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return a deterministic orientation for a semantic undirected pair."""

    pair = (tuple(str(item) for item in left), tuple(str(item) for item in right))
    ordered = sorted(pair)
    return ordered[0], ordered[1]


def _canonical_coordinate(coordinate: Iterable[object]) -> list[float]:
    values = tuple(coordinate)
    if len(values) < 2:
        raise ValueError("Canonical network geometry requires two-dimensional coordinates")
    result: list[float] = []
    for value in values[:2]:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Canonical network geometry coordinates must be finite")
        rounded = round(number, CANONICAL_GEOMETRY_DECIMALS)
        result.append(0.0 if rounded == 0 else rounded)
    return result


def _canonical_line_coordinates(line: LineString) -> list[list[float]]:
    if line.is_empty:
        raise ValueError("Canonical network geometry cannot be empty")
    coordinates: list[list[float]] = []
    for coordinate in line.coords:
        canonical = _canonical_coordinate(coordinate)
        if not coordinates or canonical != coordinates[-1]:
            coordinates.append(canonical)
    if len(coordinates) < 2:
        raise ValueError("Canonical network line collapses at identity precision")
    reversed_coordinates = list(reversed(coordinates))
    return min(coordinates, reversed_coordinates)


def ordered_geometry_fingerprint(geometries: Iterable[object]) -> str:
    """Match the Scenario Area identity: ordered current geometry WKB bytes."""

    try:
        payload = b"".join(bytes(item.wkb) for item in geometries)
    except (AttributeError, TypeError) as error:
        raise ValueError("Area identity requires ordered current geometry WKB") from error
    return hashlib.sha256(payload).hexdigest()
