from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_bus_overlay.py"


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


def write_gtfs(path: Path) -> None:
    tables = {
        "routes.txt": ("route_id,route_short_name,route_type\nbus,10,3\ntram,T1,0\n"),
        "calendar.txt": (
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            "weekday,0,0,1,0,0,0,0,20260101,20261231\n"
        ),
        "calendar_dates.txt": (
            "service_id,date,exception_type\nweekday,20260923,2\nspecial,20260923,1\n"
        ),
        "trips.txt": (
            "route_id,service_id,trip_id,shape_id\n"
            "bus,special,active-trip,active-shape\n"
            "bus,special,missing-shape-trip,\n"
            "bus,weekday,inactive-trip,inactive-shape\n"
            "tram,special,nonbus-trip,tram-shape\n"
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
    with zipfile.ZipFile(path, "w") as archive:
        for name, contents in tables.items():
            archive.writestr(name, contents)


def write_naptan(path: Path) -> None:
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
