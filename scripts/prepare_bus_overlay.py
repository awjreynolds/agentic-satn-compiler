#!/usr/bin/env python3
"""Prepare a dated, boundary-clipped GeoJSON overlay from local bus evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import zipfile
from collections.abc import Iterator
from datetime import date
from io import TextIOWrapper
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from shapely.geometry import LineString, MultiLineString, Point, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

OGL_LICENCE = "Open Government Licence v3.0"
BODS_SOURCE_ID = "bods-south-west-gtfs"
BODS_SOURCE_URL = "https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/south_west/"
BODS_ATTRIBUTION = (
    "Contains public sector information licensed under the Open Government Licence v3.0."
)
NAPTAN_SOURCE_ID = "naptan-west-of-england"
NAPTAN_SOURCE_URL = (
    "https://naptan.api.dft.gov.uk/v1/access-nodes?atcoAreaCodes=010,017,018,019&dataFormat=xml"
)
NAPTAN_ATTRIBUTION = (
    "Contains National Public Transport Access Node data from the Department for Transport."
)
STOP_AREA_TYPES = {
    "GBCS": "bus or coach station",
    "GPBS": "paired on-street bus stops",
}


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _iter_csv(archive: zipfile.ZipFile, member: str) -> Iterator[dict[str, str]]:
    try:
        raw = archive.open(member)
    except KeyError as exc:
        raise ValueError(f"GTFS ZIP is missing required file {member}") from exc
    with TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


def _read_csv(archive: zipfile.ZipFile, member: str) -> list[dict[str, str]]:
    return list(_iter_csv(archive, member))


def _active_service_ids(archive: zipfile.ZipFile, service_date: str) -> set[str]:
    """Return service IDs active on a date using calendar plus date exceptions."""
    target = date.fromisoformat(service_date)
    target_text = target.strftime("%Y%m%d")
    weekday = target.strftime("%A").lower()
    active: set[str] = set()
    for row in _read_csv(archive, "calendar.txt"):
        if not (row["start_date"] <= target_text <= row["end_date"]):
            continue
        if row.get(weekday) == "1":
            active.add(row["service_id"])
    if "calendar_dates.txt" in archive.namelist():
        for row in _iter_csv(archive, "calendar_dates.txt"):
            if row.get("date") != target_text:
                continue
            if row.get("exception_type") == "1":
                active.add(row["service_id"])
            elif row.get("exception_type") == "2":
                active.discard(row["service_id"])
    return active


def _load_gtfs_shapes(path: Path, service_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load active fixed-route bus shapes and report active bus trips without usable shapes."""
    with zipfile.ZipFile(path) as archive:
        active_services = _active_service_ids(archive, service_date)
        routes = {row["route_id"]: row for row in _read_csv(archive, "routes.txt")}
        trip_services_by_shape: dict[str, set[str]] = {}
        route_records_by_shape: dict[str, set[tuple[str, str]]] = {}
        trips_by_shape: dict[str, list[dict[str, str]]] = {}
        skipped_trips: list[dict[str, str]] = []
        service_ids_with_bus_trips: set[str] = set()
        service_ids_with_usable_shapes: set[str] = set()
        active_bus_trip_count = 0
        for trip in _iter_csv(archive, "trips.txt"):
            route = routes.get(trip.get("route_id", ""), {})
            if trip.get("service_id") not in active_services or route.get("route_type") != "3":
                continue
            active_bus_trip_count += 1
            service_id = trip.get("service_id", "")
            route_id = trip.get("route_id", "")
            shape_id = trip.get("shape_id", "")
            service_ids_with_bus_trips.add(service_id)
            if not shape_id:
                skipped_trips.append(
                    {
                        "service_id": service_id,
                        "route_id": route_id,
                        "trip_id": trip.get("trip_id", ""),
                        "reason": "trip has no shape_id",
                    }
                )
                continue
            trip_services_by_shape.setdefault(shape_id, set()).add(service_id)
            trips_by_shape.setdefault(shape_id, []).append(trip)
            route_records_by_shape.setdefault(shape_id, set()).add(
                (route_id, route.get("route_short_name", ""))
            )

        active_shape_ids = set(trip_services_by_shape)
        points: dict[str, list[tuple[int, float, float]]] = {}
        for row in _iter_csv(archive, "shapes.txt"):
            shape_id = row.get("shape_id", "")
            if shape_id not in active_shape_ids:
                continue
            points.setdefault(shape_id, []).append(
                (
                    int(row["shape_pt_sequence"]),
                    float(row["shape_pt_lon"]),
                    float(row["shape_pt_lat"]),
                )
            )

        shapes: list[dict[str, Any]] = []
        shapes_without_usable_points: set[str] = set()
        for shape_id in sorted(active_shape_ids):
            ordered = sorted(points.get(shape_id, []))
            if len(ordered) < 2:
                shapes_without_usable_points.add(shape_id)
                for trip in trips_by_shape[shape_id]:
                    skipped_trips.append(
                        {
                            "service_id": trip.get("service_id", ""),
                            "route_id": trip.get("route_id", ""),
                            "trip_id": trip.get("trip_id", ""),
                            "shape_id": shape_id,
                            "reason": "shape has fewer than two shape points",
                        }
                    )
                continue
            service_ids_with_usable_shapes.update(trip_services_by_shape[shape_id])
            route_records = sorted(route_records_by_shape[shape_id])
            shapes.append(
                {
                    "shape_id": shape_id,
                    "coordinates": [(lon, lat) for _, lon, lat in ordered],
                    "routes": [
                        {"route_id": route_id, "route_short_name": route_short_name}
                        for route_id, route_short_name in route_records
                    ],
                    "service_ids": sorted(trip_services_by_shape[shape_id]),
                }
            )

        feed_version: str | None = None
        feed_start_date: str | None = None
        feed_end_date: str | None = None
        if "feed_info.txt" in archive.namelist():
            feed_info = _read_csv(archive, "feed_info.txt")
            if feed_info:
                feed_version = feed_info[0].get("feed_version") or None
                feed_start_date = feed_info[0].get("feed_start_date") or None
                feed_end_date = feed_info[0].get("feed_end_date") or None

    skipped_trips.sort(
        key=lambda row: (
            row["service_id"],
            row["route_id"],
            row["trip_id"],
            row.get("shape_id", ""),
        )
    )
    return shapes, {
        "active_service_count": len(active_services),
        "active_bus_trip_count": active_bus_trip_count,
        "active_shape_count": len(shapes),
        "feed_version": feed_version,
        "feed_start_date": feed_start_date,
        "feed_end_date": feed_end_date,
        "skipped_services": {
            "missing_shape_trip_count": len(skipped_trips),
            "service_ids_with_missing_shape_trips": sorted(
                {trip["service_id"] for trip in skipped_trips}
            ),
            "service_ids_without_usable_shapes": sorted(
                service_ids_with_bus_trips - service_ids_with_usable_shapes
            ),
            "missing_shape_trip_details": skipped_trips,
            "shape_ids_with_fewer_than_two_points": sorted(shapes_without_usable_points),
        },
    }


def _load_boundary(path: Path) -> BaseGeometry:
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read boundary GeoJSON {path}: {exc}") from exc

    if source.get("type") == "FeatureCollection":
        geometries = [
            shape(feature["geometry"])
            for feature in source.get("features", [])
            if feature.get("geometry")
        ]
    elif source.get("type") == "Feature":
        geometries = [shape(source["geometry"])] if source.get("geometry") else []
    else:
        geometries = [shape(source)]
    if not geometries:
        raise ValueError("boundary GeoJSON contains no polygon geometry")
    if any(geometry.geom_type not in {"Polygon", "MultiPolygon"} for geometry in geometries):
        raise ValueError("boundary GeoJSON must contain only Polygon or MultiPolygon geometry")
    boundary = unary_union(geometries)
    if boundary.is_empty or not boundary.is_valid:
        raise ValueError("boundary geometry must be valid and non-empty")
    return boundary


def _line_fragments(geometry: BaseGeometry) -> list[LineString]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, LineString):
        return [geometry] if geometry.length > 0 else []
    if isinstance(geometry, MultiLineString) or geometry.geom_type == "GeometryCollection":
        return [fragment for child in geometry.geoms for fragment in _line_fragments(child)]
    return []


def _clip_shapes(
    shapes: list[dict[str, Any]], boundary: BaseGeometry, service_date: str
) -> tuple[list[dict[str, Any]], list[str]]:
    features: list[dict[str, Any]] = []
    no_overlap_shape_ids: list[str] = []
    inner_boundary = boundary.buffer(-1e-9)
    for route_shape in shapes:
        source = LineString(route_shape["coordinates"])
        fragments = _line_fragments(source.intersection(boundary))
        if any(not fragment.covered_by(boundary) for fragment in fragments):
            fragments = _line_fragments(source.intersection(inner_boundary))
        fragments.sort(key=lambda fragment: tuple(tuple(point) for point in fragment.coords))
        if not fragments:
            no_overlap_shape_ids.append(route_shape["shape_id"])
            continue
        for index, fragment in enumerate(fragments, start=1):
            feature_id = route_shape["shape_id"]
            if len(fragments) > 1:
                feature_id = f"{feature_id}--fragment-{index:03d}"
            routes = [
                {
                    "route_id": str(route.get("route_id", "")),
                    "route_short_name": str(route.get("route_short_name", "")),
                }
                for route in route_shape.get("routes", [])
            ]
            routes.sort(key=lambda route: (route["route_short_name"], route["route_id"]))
            features.append(
                {
                    "type": "Feature",
                    "id": feature_id,
                    "properties": {
                        "kind": "bus-route",
                        "shape_id": route_shape["shape_id"],
                        "service_date": service_date,
                        "source_id": BODS_SOURCE_ID,
                        "routes": routes,
                        "route_ids": sorted(
                            {route["route_id"] for route in routes if route["route_id"]}
                        ),
                        "route_short_names": sorted(
                            {
                                route["route_short_name"]
                                for route in routes
                                if route["route_short_name"]
                            }
                        ),
                    },
                    "geometry": mapping(fragment),
                }
            )
    features.sort(key=lambda feature: feature["id"])
    return features, sorted(no_overlap_shape_ids)


def _text_child(element: ET.Element, name: str) -> str | None:
    for child in element:
        if child.tag.rsplit("}", maxsplit=1)[-1] == name:
            return (child.text or "").strip() or None
    return None


def _path_text(element: ET.Element, *names: str) -> str | None:
    current = element
    for name in names:
        child = next(
            (item for item in current if item.tag.rsplit("}", maxsplit=1)[-1] == name),
            None,
        )
        if child is None:
            return None
        current = child
    return (current.text or "").strip() or None


def _load_naptan_facilities(
    path: Path, boundary: BaseGeometry
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read source-identified, active GBCS/GPBS StopAreas with usable in-area points."""
    features: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "stop_areas_seen": 0,
        "inactive_stop_areas": 0,
        "unsupported_type_codes": {},
        "missing_coordinates": 0,
        "invalid_coordinates": 0,
        "missing_identity": 0,
        "outside_boundary": 0,
        "included_stop_areas": 0,
    }
    creation_date_time: str | None = None
    try:
        events = ET.iterparse(path, events=("start", "end"))
        for event, element in events:
            local_name = element.tag.rsplit("}", maxsplit=1)[-1]
            if event == "start" and local_name == "NaPTAN" and creation_date_time is None:
                creation_date_time = element.attrib.get("CreationDateTime")
                continue
            if event != "end" or local_name != "StopArea":
                continue
            diagnostics["stop_areas_seen"] += 1
            if element.attrib.get("Status", "").lower() != "active":
                diagnostics["inactive_stop_areas"] += 1
                element.clear()
                continue
            type_code = _text_child(element, "StopAreaType") or ""
            facility_type = STOP_AREA_TYPES.get(type_code)
            if facility_type is None:
                counts = diagnostics["unsupported_type_codes"]
                counts[type_code or "(missing)"] = counts.get(type_code or "(missing)", 0) + 1
                element.clear()
                continue
            code = _text_child(element, "StopAreaCode")
            name = _text_child(element, "Name")
            if not code or not name:
                diagnostics["missing_identity"] += 1
                element.clear()
                continue
            longitude_text = _path_text(element, "Location", "Translation", "Longitude")
            latitude_text = _path_text(element, "Location", "Translation", "Latitude")
            if longitude_text is None or latitude_text is None:
                diagnostics["missing_coordinates"] += 1
                element.clear()
                continue
            try:
                longitude = float(longitude_text)
                latitude = float(latitude_text)
            except ValueError:
                diagnostics["invalid_coordinates"] += 1
                element.clear()
                continue
            if (
                not math.isfinite(longitude)
                or not math.isfinite(latitude)
                or not -180 <= longitude <= 180
                or not -90 <= latitude <= 90
            ):
                diagnostics["invalid_coordinates"] += 1
                element.clear()
                continue
            point = Point(longitude, latitude)
            if not boundary.covers(point):
                diagnostics["outside_boundary"] += 1
                element.clear()
                continue
            source_creation_date_time = element.attrib.get("CreationDateTime")
            features.append(
                {
                    "type": "Feature",
                    "id": f"stop-area-{code}",
                    "properties": {
                        "kind": "bus-interchange",
                        "source_id": NAPTAN_SOURCE_ID,
                        "stop_area_code": code,
                        "name": name,
                        "stop_area_type": type_code,
                        "facility_type": facility_type,
                        "source_status": "active",
                        "source_creation_date_time": source_creation_date_time,
                    },
                    "geometry": mapping(point),
                }
            )
            diagnostics["included_stop_areas"] += 1
            element.clear()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"cannot read NaPTAN XML {path}: {exc}") from exc

    features.sort(key=lambda feature: feature["id"])
    diagnostics["unsupported_type_codes"] = dict(
        sorted(diagnostics["unsupported_type_codes"].items())
    )
    return features, {
        "creation_date_time": creation_date_time,
        "diagnostics": diagnostics,
    }


def _source_record(
    *,
    source_id: str,
    title: str,
    url: str,
    path: Path,
    attribution: str,
    **metadata: Any,
) -> dict[str, Any]:
    return {
        "id": source_id,
        "title": title,
        "url": url,
        "licence": OGL_LICENCE,
        "attribution": attribution,
        "byte_size": path.stat().st_size,
        "sha256": _digest(path),
        **metadata,
    }


def _fresh_overlay(
    gtfs_path: Path,
    service_date: str,
    boundary_path: Path,
    naptan_path: Path | None,
) -> dict[str, Any]:
    date.fromisoformat(service_date)
    boundary = _load_boundary(boundary_path)
    shapes, gtfs_metadata = _load_gtfs_shapes(gtfs_path, service_date)
    route_features, no_overlap_shape_ids = _clip_shapes(shapes, boundary, service_date)
    gtfs_source = _source_record(
        source_id=BODS_SOURCE_ID,
        title="Department for Transport Bus Open Data Service South West GTFS snapshot",
        url=BODS_SOURCE_URL,
        path=gtfs_path,
        attribution=BODS_ATTRIBUTION,
        service_date=service_date,
        feed_version=gtfs_metadata["feed_version"],
        feed_start_date=gtfs_metadata["feed_start_date"],
        feed_end_date=gtfs_metadata["feed_end_date"],
        route_type=3,
    )
    sources = [gtfs_source]
    provenance: dict[str, Any] = {
        "mode": "gtfs",
        "freshness": "dated-feed",
        "service_date": service_date,
        "boundary_sha256": _digest(boundary_path),
        "sources": sources,
        "skipped_services": gtfs_metadata["skipped_services"],
        "active_service_count": gtfs_metadata["active_service_count"],
        "active_bus_trip_count": gtfs_metadata["active_bus_trip_count"],
        "active_shape_count": gtfs_metadata["active_shape_count"],
        "shape_ids_without_boundary_overlap": no_overlap_shape_ids,
    }
    all_features = route_features
    if naptan_path is not None:
        facility_features, naptan_metadata = _load_naptan_facilities(naptan_path, boundary)
        naptan_source = _source_record(
            source_id=NAPTAN_SOURCE_ID,
            title="Department for Transport National Public Transport Access Nodes (NaPTAN)",
            url=NAPTAN_SOURCE_URL,
            path=naptan_path,
            attribution=NAPTAN_ATTRIBUTION,
            creation_date_time=naptan_metadata["creation_date_time"],
        )
        sources.append(naptan_source)
        provenance["naptan_diagnostics"] = naptan_metadata["diagnostics"]
        all_features = route_features + facility_features
    all_features.sort(key=lambda feature: (feature.get("id", ""), feature["properties"]["kind"]))
    return {
        "type": "FeatureCollection",
        "features": all_features,
        "provenance": provenance,
        "licence": OGL_LICENCE,
        "attribution": sorted({source["attribution"] for source in sources}),
    }


def _retained_overlay(geojson_path: Path, snapshot_path: Path) -> dict[str, Any]:
    try:
        collection = json.loads(geojson_path.read_text(encoding="utf-8"))
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read retained GeoJSON or snapshot provenance: {exc}") from exc
    if collection.get("type") != "FeatureCollection" or not isinstance(
        collection.get("features"), list
    ):
        raise ValueError("retained bus GeoJSON must be a FeatureCollection")
    service_date = snapshot.get("service_date")
    if not service_date:
        raise ValueError("snapshot provenance is missing service_date")
    date.fromisoformat(service_date)
    source = next(
        (item for item in snapshot.get("sources", []) if item.get("id") == BODS_SOURCE_ID),
        None,
    )
    if source is None:
        raise ValueError(f"snapshot provenance is missing source {BODS_SOURCE_ID}")
    features: list[dict[str, Any]] = []
    for feature in collection["features"]:
        if feature.get("type") != "Feature" or not isinstance(feature.get("properties"), dict):
            raise ValueError("retained GeoJSON contains an invalid route Feature")
        properties = dict(feature["properties"])
        properties["kind"] = "bus-route"
        features.append({**feature, "properties": properties})
    features.sort(key=lambda feature: str(feature.get("id", "")))
    licence = source.get("licence") or source.get("license")
    if not licence:
        raise ValueError("retained BODS source provenance is missing its licence")
    attribution = source.get("attribution")
    if not attribution:
        raise ValueError("retained BODS source provenance is missing its attribution")
    return {
        "type": "FeatureCollection",
        "features": features,
        "provenance": {
            "mode": "retained-import",
            "freshness": "historical",
            "snapshot_id": snapshot.get("snapshot_id"),
            "captured_at": snapshot.get("captured_at"),
            "service_date": service_date,
            "retained_feature_count": len(features),
            "sources": [source],
        },
        "licence": licence,
        "attribution": attribution,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--gtfs",
        type=Path,
        help="local DfT BODS South West GTFS ZIP for a fresh dated overlay",
    )
    mode.add_argument(
        "--retained-geojson",
        type=Path,
        help="previously retained bus-routes GeoJSON; requires its snapshot provenance",
    )
    parser.add_argument("--service-date", help="ISO service date required with --gtfs")
    parser.add_argument(
        "--boundary", type=Path, help="governed boundary GeoJSON required with --gtfs"
    )
    parser.add_argument(
        "--naptan-xml", type=Path, help="optional local NaPTAN XML to add stop-area points"
    )
    parser.add_argument(
        "--snapshot", type=Path, help="snapshot.json required with --retained-geojson"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        if args.gtfs is not None:
            if args.service_date is None or args.boundary is None:
                parser.error("--gtfs requires both --service-date and --boundary")
            if args.snapshot is not None:
                parser.error("--snapshot is only used with --retained-geojson")
            if not args.gtfs.is_file():
                parser.error(f"GTFS ZIP does not exist: {args.gtfs}")
            if not args.boundary.is_file():
                parser.error(f"boundary GeoJSON does not exist: {args.boundary}")
            if args.naptan_xml is not None and not args.naptan_xml.is_file():
                parser.error(f"NaPTAN XML does not exist: {args.naptan_xml}")
            result = _fresh_overlay(args.gtfs, args.service_date, args.boundary, args.naptan_xml)
        else:
            if (
                args.service_date is not None
                or args.boundary is not None
                or args.naptan_xml is not None
            ):
                parser.error(
                    "--service-date, --boundary and --naptan-xml are only used with --gtfs"
                )
            if args.snapshot is None:
                parser.error("--retained-geojson requires --snapshot")
            if not args.retained_geojson.is_file():
                parser.error(f"retained bus GeoJSON does not exist: {args.retained_geojson}")
            if not args.snapshot.is_file():
                parser.error(f"snapshot provenance does not exist: {args.snapshot}")
            result = _retained_overlay(args.retained_geojson, args.snapshot)
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        parser.error(str(exc))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(result, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    args.output.write_text(serialized + "\n", encoding="utf-8")
    print(
        f"Wrote {len(result['features'])} bus overlay features to {args.output}; "
        f"mode={result['provenance']['mode']}"
    )


if __name__ == "__main__":
    main()
