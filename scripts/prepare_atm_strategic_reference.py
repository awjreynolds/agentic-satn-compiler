#!/usr/bin/env python3
"""Fetch and prepare B&NES Strategic ATM lines as WGS84 GeoJSON."""

import argparse
import json
from pathlib import Path
from urllib.request import urlopen

from pyproj import Transformer

SOURCE_URL = (
    "https://bathnes.maps.xmap.cloud/bathnes_public/ows?service=WFS"
    "&version=1.1.0&request=GetFeature&typeName=final_february25"
    "&outputFormat=application/json&SrsName=urn:ogc:def:crs:EPSG::27700"
)
SOURCE_LAYER = "final_february25"
SOURCE_CRS = "EPSG:27700"
TARGET_CRS = "EPSG:4326"
TO_WGS84 = Transformer.from_crs(SOURCE_CRS, TARGET_CRS, always_xy=True)


def _transform_coordinates(coordinates):
    if isinstance(coordinates[0], (int, float)):
        return list(TO_WGS84.transform(*coordinates))
    return [_transform_coordinates(part) for part in coordinates]


def prepare_feature(feature):
    """Return a Strategic feature with transformed geometry and source identity."""
    source = feature.get("properties") or {}
    if source.get("type_2") != "Strategic":
        return None
    geometry = feature["geometry"]
    feature_id = feature.get("id")
    properties = {
        "source_feature_id": feature_id,
        "source_fid": source.get("fid"),
        "source_type_2": source["type_2"],
        "source_layer": SOURCE_LAYER,
        "source_crs": SOURCE_CRS,
    }
    if source.get("name") is not None:
        properties["source_name"] = source["name"]
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {
            "type": geometry["type"],
            "coordinates": _transform_coordinates(geometry["coordinates"]),
        },
        "properties": properties,
    }


def prepare_collection(source):
    return {
        "type": "FeatureCollection",
        "source": {
            "url": SOURCE_URL,
            "layer": SOURCE_LAYER,
            "source_crs": SOURCE_CRS,
            "target_crs": TARGET_CRS,
        },
        "features": [
            prepared
            for feature in source["features"]
            if (prepared := prepare_feature(feature)) is not None
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="saved raw WFS GeoJSON; defaults to live fetch")
    parser.add_argument("--output", type=Path, required=True, help="prepared GeoJSON output path")
    args = parser.parse_args()
    if args.input:
        source = json.loads(args.input.read_text())
    else:
        with urlopen(SOURCE_URL) as response:
            source = json.loads(response.read())
    prepared = prepare_collection(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(prepared, separators=(",", ":")) + "\n")
    print(f"prepared {len(prepared['features'])} Strategic features: {args.output}")


if __name__ == "__main__":
    main()
