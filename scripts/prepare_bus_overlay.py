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
RAIL_NAPTAN_SOURCE_ID = "naptan-national-rail"
RAIL_NAPTAN_SOURCE_URL = (
    "https://naptan.api.dft.gov.uk/v1/access-nodes?atcoAreaCodes=910&dataFormat=xml"
)
STOP_AREA_TYPES = {
    "GBCS": "bus or coach station",
    "GPBS": "paired on-street bus stops",
    "GRLS": "train station",
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
    path: Path,
    boundary: BaseGeometry,
    transfer_stop_selection: dict[str, Any] | None = None,
    *,
    source_id: str = NAPTAN_SOURCE_ID,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]]]:
    """Read active bus facilities and rail stations with usable in-area points."""
    features: list[dict[str, Any]] = []
    selected_stops = {
        stop["atco_code"]: stop for stop in (transfer_stop_selection or {}).get("stops", [])
    }
    transfer_stop_points: dict[str, dict[str, Any]] = {}
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
            if event != "end":
                continue
            if local_name == "StopPoint":
                code = _text_child(element, "AtcoCode")
                if code in selected_stops:
                    if element.attrib.get("Status", "").lower() != "active":
                        raise ValueError(f"selected NaPTAN StopPoint {code} is not active")
                    name = _path_text(element, "Descriptor", "CommonName")
                    locality_code = _path_text(element, "Place", "NptgLocalityRef")
                    selection = selected_stops[code]
                    if locality_code != selection["nptg_locality_code"]:
                        raise ValueError(
                            f"selected NaPTAN StopPoint {code} has locality "
                            f"{locality_code!r}, expected {selection['nptg_locality_code']!r}"
                        )
                    if not name:
                        raise ValueError(f"selected NaPTAN StopPoint {code} has no CommonName")
                    longitude_text = _path_text(
                        element, "Place", "Location", "Translation", "Longitude"
                    )
                    latitude_text = _path_text(
                        element, "Place", "Location", "Translation", "Latitude"
                    )
                    if longitude_text is None or latitude_text is None:
                        raise ValueError(f"selected NaPTAN StopPoint {code} has no coordinates")
                    try:
                        longitude = float(longitude_text)
                        latitude = float(latitude_text)
                    except ValueError as exc:
                        raise ValueError(
                            f"selected NaPTAN StopPoint {code} has invalid coordinates"
                        ) from exc
                    if (
                        not math.isfinite(longitude)
                        or not math.isfinite(latitude)
                        or not -180 <= longitude <= 180
                        or not -90 <= latitude <= 90
                    ):
                        raise ValueError(
                            f"selected NaPTAN StopPoint {code} has invalid coordinates"
                        )
                    if not boundary.covers(Point(longitude, latitude)):
                        raise ValueError(
                            f"selected NaPTAN StopPoint {code} is outside the boundary"
                        )
                    stop_area_refs = sorted(
                        {
                            (child.text or "").strip()
                            for child in element.iter()
                            if child.tag.rsplit("}", maxsplit=1)[-1] == "StopAreaRef"
                            and (child.text or "").strip()
                        }
                    )
                    transfer_stop_points[code] = {
                        "name": name,
                        "longitude": longitude,
                        "latitude": latitude,
                        "source_creation_date_time": element.attrib.get("CreationDateTime"),
                        "stop_area_refs": stop_area_refs,
                    }
                element.clear()
                continue
            if local_name != "StopArea":
                continue
            diagnostics["stop_areas_seen"] += 1
            if element.attrib.get("Status", "").lower() != "active":
                diagnostics["inactive_stop_areas"] += 1
                element.clear()
                continue
            type_code = _text_child(element, "StopAreaType") or ""
            facility_type = STOP_AREA_TYPES.get(type_code)
            if source_id == RAIL_NAPTAN_SOURCE_ID and type_code != "GRLS":
                facility_type = None
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
                        "kind": "rail-station" if type_code == "GRLS" else "bus-interchange",
                        "source_id": source_id,
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

    missing_transfer_stops = sorted(set(selected_stops) - set(transfer_stop_points))
    if missing_transfer_stops:
        raise ValueError(
            "transfer-stop selection is missing active NaPTAN StopPoints: "
            + ", ".join(missing_transfer_stops)
        )

    features.sort(key=lambda feature: feature["id"])
    diagnostics["unsupported_type_codes"] = dict(
        sorted(diagnostics["unsupported_type_codes"].items())
    )
    return (
        features,
        {"creation_date_time": creation_date_time, "diagnostics": diagnostics},
        transfer_stop_points,
    )


def _load_transfer_stop_selection(path: Path) -> dict[str, Any]:
    """Read an explicit set of focal stops and their selected locality labels."""
    try:
        selection = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read transfer-stop selection {path}: {exc}") from exc
    if not isinstance(selection, dict):
        raise ValueError("transfer-stop selection must be a JSON object")
    selection_id = selection.get("selection_id")
    selection_basis = selection.get("selection_basis")
    stops = selection.get("stops")
    if not isinstance(selection_id, str) or not selection_id.strip():
        raise ValueError("transfer-stop selection is missing selection_id")
    if not isinstance(selection_basis, str) or not selection_basis.strip():
        raise ValueError("transfer-stop selection is missing selection_basis")
    if not isinstance(stops, list) or not stops:
        raise ValueError("transfer-stop selection must contain a non-empty stops list")
    locality_source = selection.get("locality_source")
    if locality_source is not None and (
        not isinstance(locality_source, dict)
        or not isinstance(locality_source.get("title"), str)
        or not isinstance(locality_source.get("url"), str)
        or not isinstance(locality_source.get("retrieved_on"), str)
    ):
        raise ValueError(
            "transfer-stop selection locality_source requires title, url, and retrieved_on"
        )

    records: list[dict[str, str]] = []
    seen_atco_codes: set[str] = set()
    for index, stop in enumerate(stops):
        if not isinstance(stop, dict):
            raise ValueError(f"transfer-stop selection item {index} must be an object")
        record = {
            key: stop.get(key, "").strip()
            for key in ("atco_code", "locality", "nptg_locality_code")
            if isinstance(stop.get(key), str)
        }
        if set(record) != {"atco_code", "locality", "nptg_locality_code"} or any(
            not value for value in record.values()
        ):
            raise ValueError(
                f"transfer-stop selection item {index} requires atco_code, locality, "
                "and nptg_locality_code"
            )
        if record["atco_code"] in seen_atco_codes:
            raise ValueError(f"transfer-stop selection repeats ATCO code {record['atco_code']}")
        seen_atco_codes.add(record["atco_code"])
        records.append(record)
    result = {
        "selection_id": selection_id.strip(),
        "selection_basis": selection_basis.strip(),
        "stops": records,
    }
    if locality_source is not None:
        result["locality_source"] = locality_source
    return result


def _load_transfer_candidates(
    gtfs_path: Path,
    service_date: str,
    selection: dict[str, Any],
    naptan_stops: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Match explicit NaPTAN points to active GTFS stop-time route evidence."""
    selected_stops = {stop["atco_code"]: stop for stop in selection["stops"]}
    with zipfile.ZipFile(gtfs_path) as archive:
        active_services = _active_service_ids(archive, service_date)
        routes = {
            row["route_id"]: row
            for row in _iter_csv(archive, "routes.txt")
            if row.get("route_type") == "3"
        }
        active_trips = {
            row["trip_id"]: row
            for row in _iter_csv(archive, "trips.txt")
            if row.get("service_id") in active_services and row.get("route_id") in routes
        }
        gtfs_stops = {
            row["stop_id"]: row
            for row in _iter_csv(archive, "stops.txt")
            if row.get("stop_id") in selected_stops
        }
        missing_gtfs_stops = sorted(set(selected_stops) - set(gtfs_stops))
        if missing_gtfs_stops:
            raise ValueError(
                "transfer-stop selection ATCO codes are missing from GTFS stop_id values: "
                + ", ".join(missing_gtfs_stops)
            )

        patterns_by_stop: dict[str, dict[str, dict[tuple[str, ...], set[str]]]] = {
            stop_id: {} for stop_id in selected_stops
        }
        for row in _iter_csv(archive, "stop_times.txt"):
            stop_id = row.get("stop_id", "")
            trip = active_trips.get(row.get("trip_id", ""))
            if stop_id not in selected_stops or trip is None:
                continue
            route_id = trip["route_id"]
            pattern = (
                trip.get("service_id", ""),
                trip.get("direction_id", ""),
                trip.get("trip_headsign", ""),
                row.get("pickup_type") or "0",
                row.get("drop_off_type") or "0",
            )
            trip_ids = patterns_by_stop[stop_id].setdefault(route_id, {}).setdefault(pattern, set())
            trip_ids.add(trip["trip_id"])

    features: list[dict[str, Any]] = []
    stops_without_multiple_routes: list[dict[str, Any]] = []
    for atco_code, selection_record in sorted(selected_stops.items()):
        naptan_stop = naptan_stops[atco_code]
        route_evidence: list[dict[str, Any]] = []
        active_service_ids: set[str] = set()
        pickup_type_codes: set[str] = set()
        drop_off_type_codes: set[str] = set()
        served_trip_ids: set[str] = set()
        for route_id, patterns in sorted(
            patterns_by_stop[atco_code].items(),
            key=lambda item: (
                routes[item[0]].get("route_short_name", ""),
                item[0],
            ),
        ):
            route = routes[route_id]
            route_service_ids: set[str] = set()
            route_trip_ids: set[str] = set()
            route_pickup_types: set[str] = set()
            route_drop_off_types: set[str] = set()
            service_patterns: list[dict[str, Any]] = []
            for pattern, trip_ids in sorted(patterns.items()):
                service_id, direction_id, headsign, pickup_type, drop_off_type = pattern
                route_service_ids.add(service_id)
                route_trip_ids.update(trip_ids)
                route_pickup_types.add(pickup_type)
                route_drop_off_types.add(drop_off_type)
                service_patterns.append(
                    {
                        "service_id": service_id,
                        "direction_id": direction_id,
                        "trip_headsign": headsign,
                        "scheduled_trip_count": len(trip_ids),
                        "pickup_type_code": pickup_type,
                        "drop_off_type_code": drop_off_type,
                    }
                )
            active_service_ids.update(route_service_ids)
            served_trip_ids.update(route_trip_ids)
            pickup_type_codes.update(route_pickup_types)
            drop_off_type_codes.update(route_drop_off_types)
            route_evidence.append(
                {
                    "route_id": route_id,
                    "route_short_name": route.get("route_short_name", ""),
                    "active_service_ids": sorted(route_service_ids),
                    "scheduled_trip_count": len(route_trip_ids),
                    "pickup_type_codes": sorted(route_pickup_types),
                    "drop_off_type_codes": sorted(route_drop_off_types),
                    "service_patterns": service_patterns,
                }
            )

        if len(route_evidence) < 2:
            stops_without_multiple_routes.append(
                {
                    "atco_code": atco_code,
                    "route_ids": [record["route_id"] for record in route_evidence],
                }
            )
            continue

        gtfs_stop = gtfs_stops[atco_code]
        route_ids = [record["route_id"] for record in route_evidence]
        route_short_names = sorted(
            {record["route_short_name"] for record in route_evidence if record["route_short_name"]}
        )
        properties = {
            "kind": "bus-interchange",
            "facility_type": "timetable-supported transfer candidate",
            "transfer_candidate": True,
            "transfer_candidate_basis": (
                "Multiple distinct active scheduled bus routes serve this selected stop."
            ),
            "selection_id": selection["selection_id"],
            "selection_basis": selection["selection_basis"],
            "source_id": NAPTAN_SOURCE_ID,
            "source_title": (
                "Department for Transport National Public Transport Access Nodes (NaPTAN)"
            ),
            "service_source_id": BODS_SOURCE_ID,
            "service_source_title": (
                "Department for Transport Bus Open Data Service South West GTFS"
            ),
            "atco_code": atco_code,
            "gtfs_stop_id": gtfs_stop["stop_id"],
            "gtfs_stop_name": gtfs_stop.get("stop_name", ""),
            "name": naptan_stop["name"],
            "locality": selection_record["locality"],
            "nptg_locality_code": selection_record["nptg_locality_code"],
            "source_status": "active",
            "source_creation_date_time": naptan_stop["source_creation_date_time"],
            "stop_area_refs": naptan_stop["stop_area_refs"],
            "service_date": service_date,
            "route_ids": route_ids,
            "route_short_names": route_short_names,
            "active_route_count": len(route_ids),
            "active_service_ids": sorted(active_service_ids),
            "scheduled_trip_count": len(served_trip_ids),
            "pickup_type_codes": sorted(pickup_type_codes),
            "drop_off_type_codes": sorted(drop_off_type_codes),
            "route_service_evidence": route_evidence,
        }
        features.append(
            {
                "type": "Feature",
                "id": f"transfer-stop-{atco_code}",
                "properties": properties,
                "geometry": mapping(Point(naptan_stop["longitude"], naptan_stop["latitude"])),
            }
        )
    return features, {
        "selected_stop_count": len(selected_stops),
        "included_transfer_candidate_count": len(features),
        "stops_without_multiple_active_routes": stops_without_multiple_routes,
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
    transfer_stop_selection_path: Path | None,
    rail_naptan_path: Path | None = None,
) -> dict[str, Any]:
    date.fromisoformat(service_date)
    transfer_stop_selection = (
        _load_transfer_stop_selection(transfer_stop_selection_path)
        if transfer_stop_selection_path is not None
        else None
    )
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
        facility_features, naptan_metadata, naptan_stops = _load_naptan_facilities(
            naptan_path, boundary, transfer_stop_selection
        )
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
        if transfer_stop_selection is not None:
            transfer_features, transfer_diagnostics = _load_transfer_candidates(
                gtfs_path, service_date, transfer_stop_selection, naptan_stops
            )
            all_features.extend(transfer_features)
            provenance["transfer_candidate_diagnostics"] = transfer_diagnostics
            provenance["transfer_stop_selection"] = {
                "selection_id": transfer_stop_selection["selection_id"],
                "selection_basis": transfer_stop_selection["selection_basis"],
                "sha256": _digest(transfer_stop_selection_path),
            }
            if "locality_source" in transfer_stop_selection:
                provenance["transfer_stop_selection"]["locality_source"] = transfer_stop_selection[
                    "locality_source"
                ]
    if rail_naptan_path is not None:
        rail_features, rail_metadata, _ = _load_naptan_facilities(
            rail_naptan_path, boundary, source_id=RAIL_NAPTAN_SOURCE_ID
        )
        all_features.extend(rail_features)
        sources.append(
            _source_record(
                source_id=RAIL_NAPTAN_SOURCE_ID,
                title="Department for Transport NaPTAN national rail stations",
                url=RAIL_NAPTAN_SOURCE_URL,
                path=rail_naptan_path,
                attribution=NAPTAN_ATTRIBUTION,
                creation_date_time=rail_metadata["creation_date_time"],
            )
        )
        provenance["rail_naptan_diagnostics"] = rail_metadata["diagnostics"]
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
        "--rail-naptan-xml", type=Path, help="optional national rail NaPTAN XML (area 910)"
    )
    parser.add_argument(
        "--transfer-stop-selection",
        type=Path,
        help="optional JSON selection of exact NaPTAN StopPoints for dated GTFS transfer evidence",
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
            if args.rail_naptan_xml is not None and not args.rail_naptan_xml.is_file():
                parser.error(f"rail NaPTAN XML does not exist: {args.rail_naptan_xml}")
            if args.transfer_stop_selection is not None:
                if args.naptan_xml is None:
                    parser.error("--transfer-stop-selection requires --naptan-xml")
                if not args.transfer_stop_selection.is_file():
                    parser.error(
                        f"transfer-stop selection does not exist: {args.transfer_stop_selection}"
                    )
            result = _fresh_overlay(
                args.gtfs,
                args.service_date,
                args.boundary,
                args.naptan_xml,
                args.transfer_stop_selection,
                args.rail_naptan_xml,
            )
        else:
            if (
                args.service_date is not None
                or args.boundary is not None
                or args.naptan_xml is not None
                or args.rail_naptan_xml is not None
                or args.transfer_stop_selection is not None
            ):
                parser.error(
                    "--service-date, --boundary, --naptan-xml, --rail-naptan-xml "
                    "and --transfer-stop-selection "
                    "are only used with --gtfs"
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
