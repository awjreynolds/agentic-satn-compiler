from __future__ import annotations

import pytest
from shapely.geometry import LineString, MultiLineString, Point, Polygon

from satn.content_identity import (
    CANONICAL_GEOMETRY_VERSION,
    canonical_network_geometry,
    canonical_network_geometry_fingerprint,
    canonical_undirected_pair,
)


def test_canonical_network_line_identity_is_direction_and_dimension_independent() -> None:
    forward = LineString([(-0.0, 1.0000000004, 12), (2.0, 3.0, 14)])
    reverse_2d = LineString([(2.0, 3.0), (0.0, 1.0)])

    assert canonical_network_geometry_fingerprint(
        forward,
        "EPSG:4326",
    ) == canonical_network_geometry_fingerprint(reverse_2d, 4326)
    assert canonical_network_geometry(forward, 4326) == {
        "contract": CANONICAL_GEOMETRY_VERSION,
        "crs": "EPSG:4326",
        "dimensions": 2,
        "decimal_places": 9,
        "geometry": {
            "type": "LineString",
            "coordinates": [[0.0, 1.0], [2.0, 3.0]],
        },
    }


def test_canonical_network_multiline_identity_is_part_order_independent() -> None:
    first = MultiLineString(
        [
            [(5, 5), (4, 4)],
            [(0, 0), (1, 1)],
        ]
    )
    reordered_and_reversed = MultiLineString(
        [
            [(1, 1), (0, 0)],
            [(4, 4), (5, 5)],
        ]
    )

    assert canonical_network_geometry_fingerprint(
        first,
        "EPSG:27700",
    ) == canonical_network_geometry_fingerprint(
        reordered_and_reversed,
        "EPSG:27700",
    )


def test_canonical_network_geometry_rejects_missing_crs_and_unsupported_types() -> None:
    with pytest.raises(ValueError, match="requires a CRS"):
        canonical_network_geometry(Point(0, 0), None)
    with pytest.raises(ValueError, match="supports Point, LineString and MultiLineString"):
        canonical_network_geometry(Polygon([(0, 0), (1, 0), (0, 1)]), 4326)


def test_canonical_undirected_pair_uses_semantic_tuple_order() -> None:
    assert canonical_undirected_pair(
        ("root-b", "branch-a", "place-a"),
        ("root-a", "branch-z", "place-z"),
    ) == (
        ("root-a", "branch-z", "place-z"),
        ("root-b", "branch-a", "place-a"),
    )
