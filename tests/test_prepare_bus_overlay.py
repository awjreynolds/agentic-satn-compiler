from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_bus_overlay.py"
TRANSFER_STOP_SELECTION_PATH = (
    Path(__file__).parents[1] / "data" / "context" / "weca-bus-transfer-stop-selection.json"
)
TRANSFER_STOP_SELECTION = json.loads(TRANSFER_STOP_SELECTION_PATH.read_text())["stops"]


def write_boundary(path: Path, bounds: tuple[float, float, float, float]) -> None:
    west, south, east, north = bounds
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "test authority"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [west, south],
                                    [east, south],
                                    [east, north],
                                    [west, north],
                                    [west, south],
                                ]
                            ],
                        },
                    }
                ],
            }
        )
    )


def write_gtfs(path: Path, *, with_transfer_stops: bool = False) -> None:
    tables = {
        "routes.txt": (
            "route_id,route_short_name,route_type\nbus,10,3\ntram,T1,0\n"
            + ("bus-two,11,3\n" if with_transfer_stops else "")
        ),
        "calendar.txt": (
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            "weekday,0,0,1,0,0,0,0,20260101,20261231\n"
        ),
        "calendar_dates.txt": (
            "service_id,date,exception_type\nweekday,20260923,2\nspecial,20260923,1\n"
        ),
        "trips.txt": (
            "route_id,service_id,trip_id,shape_id,direction_id,trip_headsign\n"
            "bus,special,active-trip,active-shape,0,Radstock\n"
            "bus,special,missing-shape-trip,,0,No shape\n"
            "bus,weekday,inactive-trip,inactive-shape,0,Inactive\n"
            "tram,special,nonbus-trip,tram-shape,0,Non-bus\n"
            + (
                "bus-two,special,active-trip-two,active-shape,1,Bath\n"
                if with_transfer_stops
                else ""
            )
        ),
        "shapes.txt": (
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
            "active-shape,0.5,0.5,2\n"
            "active-shape,0.5,-1,1\n"
            "active-shape,0.5,2,3\n"
            "inactive-shape,0.25,0.25,1\n"
            "inactive-shape,0.75,0.75,2\n"
            "tram-shape,0.1,0.1,1\n"
            "tram-shape,0.9,0.9,2\n"
        ),
        "feed_info.txt": (
            "feed_publisher_name,feed_publisher_url,feed_lang,feed_version\n"
            "Test Transit,https://example.test/en, en,fixture-1\n"
        ),
    }
    if with_transfer_stops:
        stop_rows = []
        stop_time_rows = []
        for index, stop in enumerate(TRANSFER_STOP_SELECTION):
            stop_id = stop["atco_code"]
            longitude = 0.1 + index * 0.1
            latitude = 0.5
            stop_rows.append(
                f"{stop_id},fixture-{index},GTFS {stop['atco_code']},{latitude},{longitude},0,0,,"
            )
            pickup = "1" if index == 0 else "0"
            for trip_id, trip_pickup in (
                ("active-trip", "0"),
                ("active-trip-two", pickup),
            ):
                stop_time_rows.append(
                    f"{trip_id},08:00:00,08:00:00,{stop_id},{index + 1},{trip_pickup},0"
                )
        # A nearby, similarly named served stop is deliberately not selected.
        stop_rows.append("0180BAC39999,fixture-nearby,Victoria Hall,0.5,0.1,0,0,,")
        stop_time_rows.extend(
            [
                "active-trip,08:00:00,08:00:00,0180BAC39999,20,0,0",
                "active-trip-two,08:00:00,08:00:00,0180BAC39999,20,0,0",
            ]
        )
        tables["stops.txt"] = (
            "stop_id,stop_code,stop_name,stop_lat,stop_lon,wheelchair_boarding,location_type,parent_station,platform_code\n"
            + "\n".join(stop_rows)
            + "\n"
        )
        tables["stop_times.txt"] = (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type\n"
            + "\n".join(stop_time_rows)
            + "\n"
        )
    with zipfile.ZipFile(path, "w") as archive:
        for name, contents in tables.items():
            archive.writestr(name, contents)


def write_naptan(path: Path, *, with_transfer_stops: bool = False) -> None:
    namespace = "http://www.naptan.org.uk/"
    root = ET.Element(f"{{{namespace}}}NaPTAN", {"CreationDateTime": "2026-09-23T16:46:37"})
    stop_areas = ET.SubElement(root, f"{{{namespace}}}StopAreas")
    for code, name, kind, status, coordinates in [
        ("010G0001", "Bath Bus Station", "GBCS", "active", (0.25, 0.25)),
        ("010G0002", "High Street stop pair", "GPBS", "active", (0.75, 0.75)),
        ("010G0003", "Retired station", "GBCS", "inactive", (0.5, 0.5)),
        ("010G0004", "No point", "GBCS", "active", None),
        ("010G0005", "Outside authority", "GPBS", "active", (2.0, 2.0)),
        ("010G0006", "Unsupported type", "UNKN", "active", (0.4, 0.4)),
    ]:
        area = ET.SubElement(
            stop_areas,
            f"{{{namespace}}}StopArea",
            {"Status": status, "CreationDateTime": "2024-01-02T03:04:05"},
        )
        ET.SubElement(area, f"{{{namespace}}}StopAreaCode").text = code
        ET.SubElement(area, f"{{{namespace}}}Name").text = name
        ET.SubElement(area, f"{{{namespace}}}StopAreaType").text = kind
        if coordinates:
            location = ET.SubElement(area, f"{{{namespace}}}Location")
            translation = ET.SubElement(location, f"{{{namespace}}}Translation")
            ET.SubElement(translation, f"{{{namespace}}}Longitude").text = str(coordinates[0])
            ET.SubElement(translation, f"{{{namespace}}}Latitude").text = str(coordinates[1])
    if with_transfer_stops:
        for index, stop in enumerate(TRANSFER_STOP_SELECTION):
            point = ET.SubElement(
                root,
                f"{{{namespace}}}StopPoint",
                {"Status": "active", "CreationDateTime": "2026-09-23T16:46:37"},
            )
            ET.SubElement(point, f"{{{namespace}}}AtcoCode").text = stop["atco_code"]
            descriptor = ET.SubElement(point, f"{{{namespace}}}Descriptor")
            name = (
                "Victoria Hall"
                if stop["locality"] == "Radstock"
                else (
                    "Sacred Heart"
                    if stop["atco_code"] in {"0180BAC31168", "0180BAC23485"}
                    else "Post Office"
                )
            )
            ET.SubElement(descriptor, f"{{{namespace}}}CommonName").text = name
            place = ET.SubElement(point, f"{{{namespace}}}Place")
            ET.SubElement(place, f"{{{namespace}}}NptgLocalityRef").text = stop[
                "nptg_locality_code"
            ]
            location = ET.SubElement(place, f"{{{namespace}}}Location")
            translation = ET.SubElement(location, f"{{{namespace}}}Translation")
            ET.SubElement(translation, f"{{{namespace}}}Longitude").text = str(0.1 + index * 0.1)
            ET.SubElement(translation, f"{{{namespace}}}Latitude").text = "0.5"
        nearby = ET.SubElement(root, f"{{{namespace}}}StopPoint", {"Status": "active"})
        ET.SubElement(nearby, f"{{{namespace}}}AtcoCode").text = "0180BAC39999"
        descriptor = ET.SubElement(nearby, f"{{{namespace}}}Descriptor")
        ET.SubElement(descriptor, f"{{{namespace}}}CommonName").text = "Victoria Hall"
        place = ET.SubElement(nearby, f"{{{namespace}}}Place")
        ET.SubElement(place, f"{{{namespace}}}NptgLocalityRef").text = "E0035085"
        location = ET.SubElement(place, f"{{{namespace}}}Location")
        translation = ET.SubElement(location, f"{{{namespace}}}Translation")
        ET.SubElement(translation, f"{{{namespace}}}Longitude").text = "0.1"
        ET.SubElement(translation, f"{{{namespace}}}Latitude").text = "0.5"
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def run_cli(*arguments: str) -> None:
    subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def test_gtfs_cli_applies_service_exceptions_filters_nonbus_and_clips_shapes(
    tmp_path: Path,
) -> None:
    gtfs = tmp_path / "feed.zip"
    boundary = tmp_path / "boundary.geojson"
    output = tmp_path / "overlay.geojson"
    write_gtfs(gtfs)
    write_boundary(boundary, (0, 0, 1, 1))

    run_cli(
        "--gtfs",
        str(gtfs),
        "--service-date",
        "2026-09-23",
        "--boundary",
        str(boundary),
        "--output",
        str(output),
    )

    result = json.loads(output.read_text())
    routes = [
        feature for feature in result["features"] if feature["properties"]["kind"] == "bus-route"
    ]
    assert len(routes) == 1
    route = routes[0]
    assert route["properties"]["shape_id"] == "active-shape"
    assert route["properties"]["service_date"] == "2026-09-23"
    assert route["properties"]["route_ids"] == ["bus"]
    assert route["geometry"]["coordinates"] == [[0.0, 0.5], [0.5, 0.5], [1.0, 0.5]]
    provenance = result["provenance"]
    assert provenance["service_date"] == "2026-09-23"
    assert provenance["sources"][0]["sha256"] == hashlib.sha256(gtfs.read_bytes()).hexdigest()
    assert provenance["sources"][0]["feed_version"] == "fixture-1"
    assert provenance["skipped_services"]["service_ids_with_missing_shape_trips"] == ["special"]
    assert provenance["skipped_services"]["missing_shape_trip_count"] == 1
    assert result["licence"] == "Open Government Licence v3.0"


def test_retained_import_preserves_route_attributes_and_historical_provenance(
    tmp_path: Path,
) -> None:
    retained = tmp_path / "retained.geojson"
    snapshot = tmp_path / "snapshot.json"
    output = tmp_path / "overlay.geojson"
    retained.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "shape-2026-08-12",
                        "properties": {
                            "shape_id": "shape-2026-08-12",
                            "service_date": "2026-08-12",
                            "source_id": "bods-south-west-gtfs",
                            "routes": [{"route_id": "R7", "route_short_name": "7"}],
                            "route_ids": ["R7"],
                            "route_short_names": ["7"],
                            "retained_attribute": "preserved",
                        },
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[0.1, 0.2], [0.3, 0.4]],
                        },
                    }
                ],
            }
        )
    )
    snapshot.write_text(
        json.dumps(
            {
                "snapshot_id": "weca-historical-2026-08-12",
                "service_date": "2026-08-12",
                "sources": [
                    {
                        "id": "bods-south-west-gtfs",
                        "title": "DfT BODS South West GTFS snapshot",
                        "url": "https://example.test/historical-feed.zip",
                        "licence": "Open Government Licence v3.0",
                        "sha256": "a" * 64,
                        "attribution": (
                            "Contains public sector information licensed under the Open Government "
                            "Licence v3.0."
                        ),
                    }
                ],
            }
        )
    )

    run_cli(
        "--retained-geojson",
        str(retained),
        "--snapshot",
        str(snapshot),
        "--output",
        str(output),
    )

    result = json.loads(output.read_text())
    assert len(result["features"]) == 1
    feature = result["features"][0]
    assert feature["properties"]["kind"] == "bus-route"
    assert feature["properties"]["retained_attribute"] == "preserved"
    assert feature["properties"]["service_date"] == "2026-08-12"
    assert result["provenance"]["mode"] == "retained-import"
    assert result["provenance"]["freshness"] == "historical"
    assert result["provenance"]["service_date"] == "2026-08-12"
    assert result["provenance"]["sources"][0]["sha256"] == "a" * 64
    assert result["licence"] == "Open Government Licence v3.0"


def test_naptan_cli_emits_only_active_in_area_stop_groups_with_source_types(
    tmp_path: Path,
) -> None:
    gtfs = tmp_path / "feed.zip"
    boundary = tmp_path / "boundary.geojson"
    naptan = tmp_path / "naptan.xml"
    output = tmp_path / "overlay.geojson"
    write_gtfs(gtfs)
    write_boundary(boundary, (0, 0, 1, 1))
    write_naptan(naptan)

    run_cli(
        "--gtfs",
        str(gtfs),
        "--service-date",
        "2026-09-23",
        "--boundary",
        str(boundary),
        "--naptan-xml",
        str(naptan),
        "--output",
        str(output),
    )

    result = json.loads(output.read_text())
    facilities = [
        feature
        for feature in result["features"]
        if feature["properties"]["kind"] == "bus-interchange"
    ]
    by_code = {feature["properties"]["stop_area_code"]: feature for feature in facilities}
    assert set(by_code) == {"010G0001", "010G0002"}
    assert by_code["010G0001"]["properties"]["facility_type"] == "bus or coach station"
    assert by_code["010G0002"]["properties"]["facility_type"] == "paired on-street bus stops"
    assert all(feature["geometry"]["type"] == "Point" for feature in facilities)
    assert all(
        feature["properties"]["source_creation_date_time"] == "2024-01-02T03:04:05"
        for feature in facilities
    )
    provenance = result["provenance"]
    naptan_source = next(
        source for source in provenance["sources"] if source["id"] == "naptan-west-of-england"
    )
    assert naptan_source["creation_date_time"] == "2026-09-23T16:46:37"
    assert (
        naptan_source["url"]
        == "https://naptan.api.dft.gov.uk/v1/access-nodes?atcoAreaCodes=010,017,018,019&dataFormat=xml"
    )
    assert naptan_source["sha256"] == hashlib.sha256(naptan.read_bytes()).hexdigest()
    assert provenance["naptan_diagnostics"]["inactive_stop_areas"] == 1
    assert provenance["naptan_diagnostics"]["missing_coordinates"] == 1
    assert provenance["naptan_diagnostics"]["outside_boundary"] == 1
    assert provenance["naptan_diagnostics"]["unsupported_type_codes"] == {"UNKN": 1}
    assert "Chew Magna" not in {feature["properties"]["name"] for feature in facilities}


def test_cli_adds_only_selected_stops_with_dated_route_and_boarding_evidence(
    tmp_path: Path,
) -> None:
    gtfs = tmp_path / "feed.zip"
    boundary = tmp_path / "boundary.geojson"
    naptan = tmp_path / "naptan.xml"
    selection = tmp_path / "transfer-stops.json"
    without_selection = tmp_path / "without-selection.geojson"
    output = tmp_path / "overlay.geojson"
    write_gtfs(gtfs, with_transfer_stops=True)
    write_boundary(boundary, (0, 0, 1, 1))
    write_naptan(naptan, with_transfer_stops=True)
    selection.write_text(TRANSFER_STOP_SELECTION_PATH.read_text())

    common_arguments = (
        "--gtfs",
        str(gtfs),
        "--service-date",
        "2026-09-23",
        "--boundary",
        str(boundary),
        "--naptan-xml",
        str(naptan),
    )
    run_cli(*common_arguments, "--output", str(without_selection))
    run_cli(
        *common_arguments,
        "--transfer-stop-selection",
        str(selection),
        "--output",
        str(output),
    )

    baseline = json.loads(without_selection.read_text())
    result = json.loads(output.read_text())
    baseline_routes = [
        feature for feature in baseline["features"] if feature["properties"]["kind"] == "bus-route"
    ]
    features_by_kind = {}
    for feature in result["features"]:
        features_by_kind.setdefault(feature["properties"]["kind"], []).append(feature)
    assert features_by_kind["bus-route"] == baseline_routes
    assert len(features_by_kind["bus-interchange"]) == 2 + len(TRANSFER_STOP_SELECTION)
    transfer_points = [
        feature
        for feature in features_by_kind["bus-interchange"]
        if feature["properties"].get("transfer_candidate") is True
    ]
    by_atco = {feature["properties"]["atco_code"]: feature for feature in transfer_points}
    expected_atco_codes = {
        "0180BAC30834",
        "0180BAC30835",
        "0180BAC31168",
        "0180BAC23485",
        "0180BAC31169",
        "0180BAC31170",
    }
    assert {stop["atco_code"] for stop in TRANSFER_STOP_SELECTION} == expected_atco_codes
    assert set(by_atco) == expected_atco_codes
    assert "0180BAC39999" not in by_atco

    victoria_hall = by_atco["0180BAC30834"]
    properties = victoria_hall["properties"]
    assert properties["kind"] == "bus-interchange"
    assert properties["facility_type"] == "timetable-supported transfer candidate"
    assert properties["source_id"] == "naptan-west-of-england"
    assert properties["service_source_id"] == "bods-south-west-gtfs"
    assert properties["gtfs_stop_id"] == "0180BAC30834"
    assert properties["name"] == "Victoria Hall"
    assert properties["locality"] == "Radstock"
    assert properties["nptg_locality_code"] == "E0035085"
    assert properties["service_date"] == "2026-09-23"
    assert properties["route_ids"] == ["bus", "bus-two"]
    assert properties["route_short_names"] == ["10", "11"]
    assert properties["pickup_type_codes"] == ["0", "1"]
    assert properties["drop_off_type_codes"] == ["0"]
    assert properties["stop_area_refs"] == []
    assert victoria_hall["geometry"] == {
        "type": "Point",
        "coordinates": [0.1, 0.5],
    }
    route_evidence = {entry["route_id"]: entry for entry in properties["route_service_evidence"]}
    assert route_evidence["bus"]["pickup_type_codes"] == ["0"]
    assert route_evidence["bus-two"]["pickup_type_codes"] == ["1"]
