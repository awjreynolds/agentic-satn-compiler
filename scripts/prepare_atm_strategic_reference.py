#!/usr/bin/env python3
"""Fetch and prepare B&NES Strategic ATM lines as WGS84 GeoJSON."""

import argparse
import json
from pathlib import Path
from urllib.request import urlopen

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

SOURCE_URL = (
    "https://bathnes.maps.xmap.cloud/bathnes_public/ows?service=WFS"
    "&version=1.1.0&request=GetFeature&typeName=final_february25"
    "&outputFormat=application/json&SrsName=urn:ogc:def:crs:EPSG::27700"
)
SOURCE_LAYER = "final_february25"
SOURCE_CRS = "EPSG:27700"
TARGET_CRS = "EPSG:4326"
TO_WGS84 = Transformer.from_crs(SOURCE_CRS, TARGET_CRS, always_xy=True)
ATTRIBUTION = "B&NES Active Travel Masterplan"


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


def _scope_shape(boundary):
    if boundary.get("type") == "FeatureCollection":
        geometries = [shape(feature["geometry"]) for feature in boundary["features"]]
    elif boundary.get("type") == "Feature":
        geometries = [shape(boundary["geometry"])]
    else:
        geometries = [shape(boundary)]
    return unary_union(geometries)


def _line_parts(geometry):
    if geometry.geom_type == "LineString":
        return [geometry] if geometry.length else []
    if geometry.geom_type in ("MultiLineString", "GeometryCollection"):
        return [part for member in geometry.geoms for part in _line_parts(member)]
    return []


def _graph_edge_id(properties, index):
    value = f"edge:{properties['u']}:{properties['v']}:{properties.get('key', 0)}:{index}"
    osmid = properties.get("osmid")
    if isinstance(osmid, str) and osmid:
        value += f":osm-{osmid}"
    elif isinstance(osmid, list) and osmid:
        value += ":osm-" + ",".join(map(str, osmid))
    elif isinstance(osmid, (int, float)) and osmid:
        value += f":osm-{osmid}"
    return value


def _baseline_edge_ids(report):
    report = report.get("report", report)
    candidate_ids = {
        edge_id
        for candidate in report.get("candidates", [])
        for edge_id in candidate.get("path_edge_ids", [])
    }
    strategic_target_ids = {
        edge_id
        for source in report.get("source_inventory", [])
        if source.get("baseline_role") == "a-road"
        for edge_id in source.get("graph_edge_ids", [])
    }
    return candidate_ids, strategic_target_ids


def _build_strategic_network_ledger(prepared, boundary, baseline_report, network):
    """Return the source-alignment ledger and separate scope diagnostics."""
    scope = _scope_shape(boundary)
    selected_alignments = []
    for feature in prepared["features"]:
        clipped = shape(feature["geometry"]).intersection(scope)
        parts = [list(part.coords) for part in _line_parts(clipped)]
        if parts:
            selected_alignments.append(
                {
                    "source_id": feature["id"],
                    "geometry": parts,
                }
            )

    candidate_ids, strategic_target_ids = _baseline_edge_ids(baseline_report)
    baseline_ids = candidate_ids | strategic_target_ids
    deselected_ids = set()
    crossing_ids = set()
    for index, feature in enumerate(network["features"]):
        geometry = feature.get("geometry")
        properties = feature.get("properties") or {}
        if not geometry or geometry.get("type") != "LineString":
            continue
        edge_id = _graph_edge_id(properties, index)
        if edge_id in baseline_ids:
            graph_geometry = shape(geometry)
            if graph_geometry.intersects(scope):
                deselected_ids.add(edge_id)
                if (
                    graph_geometry.intersection(scope).length
                    and graph_geometry.difference(scope).length
                ):
                    crossing_ids.add(edge_id)

    scope_geometry = mapping(scope)
    evidence = {
        "candidate_path_edge_id_count": len(deselected_ids & candidate_ids),
        "a_road_target_edge_id_count": len(deselected_ids & strategic_target_ids),
        "overlap_edge_id_count": len(deselected_ids & candidate_ids & strategic_target_ids),
        "boundary_crossing_edge_id_count": len(crossing_ids),
    }
    return {
        "decisions": [],
        "strategic_network": {
            "selected_graph_edge_ids": [],
            "deselected_graph_edge_ids": sorted(deselected_ids),
            "selected_alignments": selected_alignments,
            "scope_geometry": scope_geometry,
            "source_refs": [SOURCE_URL],
            "attribution": ATTRIBUTION,
            "rationale": (
                "Official Strategic geometry only. Omission is not an explicit rejection; "
                "unresolved source-to-graph links do not imply absence."
            ),
        },
    }, evidence


def build_strategic_network_ledger(prepared, boundary, baseline_report, network):
    """Build an exact, clipped source-alignment ledger without graph bindings."""
    ledger, _ = _build_strategic_network_ledger(prepared, boundary, baseline_report, network)
    return ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="saved raw WFS GeoJSON; defaults to live fetch")
    parser.add_argument("--output", type=Path, required=True, help="prepared GeoJSON output path")
    parser.add_argument(
        "--ledger-output", type=Path, help="write a scoped Strategic geometry ledger"
    )
    parser.add_argument(
        "--scope-boundary", type=Path, help="B&NES boundary GeoJSON used to clip the ledger"
    )
    parser.add_argument(
        "--baseline-report", type=Path, help="baseline report containing candidate/A-road edge IDs"
    )
    parser.add_argument(
        "--network", type=Path, help="graph snapshot GeoJSON used to scope baseline edge IDs"
    )
    args = parser.parse_args()
    ledger_inputs = (args.scope_boundary, args.baseline_report, args.network)
    if args.ledger_output and not all(ledger_inputs):
        parser.error("--ledger-output requires --scope-boundary, --baseline-report, and --network")
    if any(ledger_inputs) and not args.ledger_output:
        parser.error("--scope-boundary, --baseline-report, and --network require --ledger-output")
    if args.input:
        source = json.loads(args.input.read_text())
    else:
        with urlopen(SOURCE_URL) as response:
            source = json.loads(response.read())
    prepared = prepare_collection(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(prepared, separators=(",", ":")) + "\n")
    print(f"prepared {len(prepared['features'])} Strategic features: {args.output}")
    if args.ledger_output:
        ledger, evidence = _build_strategic_network_ledger(
            prepared,
            json.loads(args.scope_boundary.read_text()),
            json.loads(args.baseline_report.read_text()),
            json.loads(args.network.read_text()),
        )
        args.ledger_output.parent.mkdir(parents=True, exist_ok=True)
        args.ledger_output.write_text(json.dumps(ledger, separators=(",", ":")) + "\n")
        strategic = ledger["strategic_network"]
        print(
            f"wrote ledger: {len(strategic['selected_alignments'])} alignments, "
            f"{len(strategic['deselected_graph_edge_ids'])} scoped deselections "
            f"({evidence['candidate_path_edge_id_count']} candidate-path, "
            f"{evidence['a_road_target_edge_id_count']} A-road-target, "
            f"{evidence['overlap_edge_id_count']} overlapping, "
            f"{evidence['boundary_crossing_edge_id_count']} boundary-crossing): "
            f"{args.ledger_output}"
        )


if __name__ == "__main__":
    main()
