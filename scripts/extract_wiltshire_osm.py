"""Materialise a governed local Wiltshire source from ONS and OSM downloads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import osmium
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union

OSM_DATE = "2026-07-24"
OSM_LICENCE = "Open Data Commons Open Database License (ODbL)"
ONS_SOURCE_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "Counties_and_Unitary_Authorities_December_2025_Boundaries_UK_BGC/"
    "FeatureServer/0"
)
PLACE_TYPES = {
    "city",
    "town",
    "village",
}
ROAD_TYPES = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "cycleway",
    "path",
    "footway",
    "pedestrian",
    "bridleway",
    "track",
}
TAG_KEYS = (
    "highway",
    "name",
    "name:en",
    "ref",
    "bicycle",
    "foot",
    "cycleway",
    "cycleway:left",
    "cycleway:right",
    "cycleway:both",
    "route",
    "network",
    "lcn",
    "rcn",
    "ncn",
    "icn",
    "designation",
    "prow_class",
    "right_of_way",
    "surface",
    "segregated",
    "lanes",
    "lit",
    "incline",
    "oneway",
)


def _feature_collection(features: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": name,
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }


def _tag_value(tags: Any, key: str) -> str | None:
    value = tags.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _location(node: Any) -> tuple[float, float] | None:
    try:
        if not node.location.valid():
            return None
        return (float(node.location.lon), float(node.location.lat))
    except (AttributeError, RuntimeError, ValueError):
        return None


def _retain_way(tags: Any) -> bool:
    highway = (_tag_value(tags, "highway") or "").lower()
    if highway not in ROAD_TYPES:
        return False
    if highway in {"residential", "service"}:
        return any(
            _tag_value(tags, key)
            for key in (
                "cycleway",
                "cycleway:left",
                "cycleway:right",
                "route",
                "lcn",
                "rcn",
                "ncn",
                "icn",
            )
        )
    if highway in {"path", "footway", "pedestrian", "track"}:
        bicycle_values = {
            "yes",
            "designated",
            "permissive",
            "official",
        }
        return any(
            (_tag_value(tags, key) or "").lower() in bicycle_values
            for key in ("bicycle", "cycleway")
        ) or any(_tag_value(tags, key) for key in ("route", "lcn", "rcn", "ncn", "icn"))
    return True


class WiltshireHandler(osmium.SimpleHandler):
    def __init__(self, boundary: Any) -> None:
        super().__init__()
        self.boundary = boundary
        self.places: list[dict[str, Any]] = []
        self.network: list[dict[str, Any]] = []

    def node(self, node: Any) -> None:
        place_type = (_tag_value(node.tags, "place") or "").lower()
        name = _tag_value(node.tags, "name")
        coordinate = _location(node)
        if place_type not in PLACE_TYPES or not name or coordinate is None:
            return
        point = Point(coordinate)
        if not self.boundary.covers(point):
            return
        properties: dict[str, Any] = {
            "place_id": f"osm-node-{node.id}",
            "name": name,
            "kind": "community",
            "osm_place_type": place_type,
            "source_id": f"osm-node-{node.id}",
            "source_family": "OpenStreetMap",
            "dataset": "GEO2day Wiltshire OSM extract",
            "publisher": "OpenStreetMap contributors",
            "effective_date": OSM_DATE,
            "licence": OSM_LICENCE,
        }
        population = _tag_value(node.tags, "population")
        if population and population.isdigit():
            properties["population"] = int(population)
        self.places.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": {"type": "Point", "coordinates": list(coordinate)},
            }
        )

    def way(self, way: Any) -> None:
        if not _retain_way(way.tags):
            return
        coordinates = [_location(node) for node in way.nodes]
        if any(coordinate is None for coordinate in coordinates):
            return
        line = LineString([coordinate for coordinate in coordinates if coordinate is not None])
        clipped = line.intersection(self.boundary)
        geometries = list(clipped.geoms) if clipped.geom_type == "MultiLineString" else [clipped]
        for part_number, geometry in enumerate(geometries, start=1):
            if geometry.geom_type != "LineString" or geometry.is_empty or geometry.length == 0:
                continue
            properties: dict[str, Any] = {
                "source_id": f"osm-way-{way.id}-{part_number}",
                "osm_way_id": int(way.id),
                "source_family": "OpenStreetMap",
                "dataset": "GEO2day Wiltshire OSM extract",
                "publisher": "OpenStreetMap contributors",
                "effective_date": OSM_DATE,
                "licence": OSM_LICENCE,
            }
            for key in TAG_KEYS:
                value = _tag_value(way.tags, key)
                if value is not None:
                    properties[key] = value
            self.network.append(
                {
                    "type": "Feature",
                    "properties": properties,
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [list(pair) for pair in geometry.coords],
                    },
                }
            )


def _read_boundary(path: Path) -> Any:
    payload = json.loads(path.read_text(encoding="utf-8"))
    geometries = [shape(feature["geometry"]) for feature in payload["features"]]
    return unary_union(geometries)


def _boundary_feature(boundary: Any) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "boundary_id": "wiltshire",
            "name": "Wiltshire",
            "source_id": "ons-ctyua-december-2025-wiltshire",
            "source_family": "Office for National Statistics",
            "dataset": "Counties and Unitary Authorities (December 2025) Boundaries UK BGC",
            "publisher": "Office for National Statistics",
            "effective_date": "2025-12-01",
            "licence": "Open Government Licence v3.0",
            "source_url": ONS_SOURCE_URL,
        },
        "geometry": boundary.__geo_interface__,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pbf", type=Path, required=True)
    parser.add_argument("--boundary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    boundary = _read_boundary(args.boundary)
    handler = WiltshireHandler(boundary)
    handler.apply_file(args.pbf, locations=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "boundary.geojson").write_text(
        json.dumps(
            _feature_collection([_boundary_feature(boundary)], "wiltshire-ons-boundary"),
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (args.output / "places.geojson").write_text(
        json.dumps(
            _feature_collection(handler.places, "wiltshire-osm-places"),
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (args.output / "network.geojson").write_text(
        json.dumps(
            _feature_collection(handler.network, "wiltshire-osm-network"),
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    print(f"extracted {len(handler.places)} places and {len(handler.network)} network features")


if __name__ == "__main__":
    main()
