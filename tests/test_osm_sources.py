from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon

from satn.evidence import derive_context_layers
from satn.models import (
    CouncilConfig,
    ObservedThroughTrafficConfig,
    OfficialRoadClassificationConfig,
)
from satn.sources import (
    OSMData,
    OSMnxAdapter,
    _features_from_tag_groups,
    _load_ncn_features,
    _load_reclassified_ncn_features,
    derive_network_places,
    snapshot,
)

PROJECT = Path(__file__).parents[1]


def base_config() -> CouncilConfig:
    config = CouncilConfig.from_yaml(PROJECT / "config" / "banes.yaml")
    config.source.national_elevation = None
    return config


def source_frames() -> OSMData:
    boundary_shape = Polygon([(-2.6, 51.3), (-2.4, 51.3), (-2.4, 51.5), (-2.6, 51.5), (-2.6, 51.3)])
    boundary = gpd.GeoDataFrame([{"name": "Test Council", "geometry": boundary_shape}], crs=4326)
    large_village = Polygon(
        [(-2.54, 51.38), (-2.51, 51.38), (-2.51, 51.41), (-2.54, 51.41), (-2.54, 51.38)]
    )
    place_features = gpd.GeoDataFrame(
        [
            {"osmid": 1, "name": "Compact Town", "place": "town", "geometry": Point(-2.47, 51.44)},
            {"osmid": 2, "name": "Large Village", "place": "village", "geometry": large_village},
            {
                "osmid": 3,
                "name": "Named Quarter",
                "place": "neighbourhood",
                "geometry": Point(-2.48, 51.42),
            },
            {"osmid": 4, "name": "Tiny Hamlet", "place": "hamlet", "geometry": Point(-2.56, 51.34)},
            {"osmid": 5, "name": "Outside Town", "place": "town", "geometry": Point(-2.3, 51.4)},
        ],
        crs=4326,
    )
    stations = gpd.GeoDataFrame(
        [
            {
                "osmid": 6,
                "name": "Test Station",
                "railway": "station",
                "geometry": Point(-2.49, 51.43),
            }
        ],
        crs=4326,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": 10,
                "ref": "A1",
                "highway": "primary",
                "geometry": LineString([(-2.56, 51.395), (-2.49, 51.395)]),
            },
            {"osmid": 11, "geometry": LineString([(-2.5, 51.4), (-2.3, 51.4)])},
        ],
        crs=4326,
    )
    circulation_boundaries = gpd.GeoDataFrame(
        [
            {
                "osmid": "river-test",
                "waterway": "river",
                "name": "River Test",
                "geometry": LineString([(-2.475, 51.4), (-2.475, 51.45)]),
            }
        ],
        crs=4326,
    )
    return OSMData(
        boundary,
        place_features,
        stations,
        network,
        circulation_boundaries=circulation_boundaries,
    )


def test_area_definition_accepts_multiple_boundary_queries_and_unions_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = base_config()
    config.source.osm_place_query = None
    config.source.osm_place_queries = ["First Council", "Second Council"]
    config.source.ncn_feature_service_url = None
    config.source.reclassified_ncn_feature_service_url = None
    first = Polygon([(-2.6, 51.3), (-2.5, 51.3), (-2.5, 51.4), (-2.6, 51.4)])
    second = Polygon([(-2.5, 51.3), (-2.4, 51.3), (-2.4, 51.4), (-2.5, 51.4)])
    seen: list[object] = []

    class FakeOSMnx:
        settings = SimpleNamespace(overpass_url="", requests_timeout=0)

        @staticmethod
        def geocode_to_gdf(query: object) -> gpd.GeoDataFrame:
            seen.append(query)
            return gpd.GeoDataFrame(
                [
                    {
                        "osm_type": "relation",
                        "osm_id": 1,
                        "display_name": "First Council, England",
                        "geometry": first,
                    },
                    {
                        "osm_type": "relation",
                        "osm_id": 2,
                        "display_name": "Second Council, England",
                        "geometry": second,
                    },
                ],
                crs=4326,
            )

        @staticmethod
        def features_from_polygon(
            _polygon: object, *, tags: dict[str, object]
        ) -> gpd.GeoDataFrame:
            return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=4326)

        @staticmethod
        def graph_from_polygon(
            _polygon: object,
            *,
            network_type: str,
            simplify: bool,
            retain_all: bool,
        ) -> object:
            return object()

        @staticmethod
        def graph_to_gdfs(_graph: object) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
            nodes = gpd.GeoDataFrame(
                [{"osmid": 1, "geometry": Point(-2.5, 51.35)}], crs=4326
            )
            edges = gpd.GeoDataFrame(
                [
                    {
                        "u": 1,
                        "v": 2,
                        "key": 0,
                        "osmid": 10,
                        "geometry": LineString([(-2.55, 51.35), (-2.45, 51.35)]),
                    }
                ],
                crs=4326,
            )
            return nodes, edges

    monkeypatch.setitem(sys.modules, "osmnx", FakeOSMnx)

    acquired = OSMnxAdapter().acquire(config)

    assert seen == [["First Council", "Second Council"]]
    assert list(acquired.boundary["name"]) == ["First Council", "Second Council"]
    assert list(acquired.boundary["source_query"]) == ["First Council", "Second Council"]
    assert acquired.boundary.geometry.union_all().equals(first.union(second))


def test_derives_places_and_excludes_hamlets() -> None:
    config = base_config()
    data = source_frames()

    places = derive_network_places(
        data.boundary, data.place_features, data.stations, data.network, config
    )

    by_kind = places.groupby("kind").size().to_dict()
    assert by_kind == {
        "community": 3,
        "community_portal": 2,
        "cross_boundary_gateway": 1,
        "station_access": 1,
    }
    assert "Tiny Hamlet" not in set(places["name"])
    compact = places[places["name"] == "Compact Town"]
    assert len(compact) == 1
    portals = places[places["kind"] == "community_portal"]
    assert portals["parent_place_id"].nunique() == 1
    assert set(places.loc[places["kind"] == "station_access", "place_class"]) == {"rail"}
    assert set(places.loc[places["kind"] == "cross_boundary_gateway", "name"]) == {
        "Towards Outside Town"
    }


def test_gateway_name_follows_onward_corridor_not_nearest_centre() -> None:
    config = base_config()
    data = source_frames()
    extra_centres = gpd.GeoDataFrame(
        [
            {
                "osmid": 7,
                "name": "Closer Off Axis",
                "place": "town",
                "geometry": Point(-2.35, 51.53),
            },
            {
                "osmid": 8,
                "name": "Onward Along Road",
                "place": "town",
                "geometry": Point(-2.2, 51.4),
            },
        ],
        crs=4326,
    )
    original = data.place_features[data.place_features["osmid"] != 5]
    data.place_features = gpd.GeoDataFrame(
        list(original.to_dict("records")) + list(extra_centres.to_dict("records")), crs=4326
    )

    places = derive_network_places(
        data.boundary, data.place_features, data.stations, data.network, config
    )

    gateway_names = set(places.loc[places["kind"] == "cross_boundary_gateway", "name"])
    assert gateway_names == {"Towards Onward Along Road"}


def test_gateway_records_unknown_when_no_onward_centre_is_available() -> None:
    config = base_config()
    data = source_frames()
    data.place_features = data.place_features[data.place_features["osmid"] != 5]

    places = derive_network_places(
        data.boundary, data.place_features, data.stations, data.network, config
    )

    gateway_names = set(places.loc[places["kind"] == "cross_boundary_gateway", "name"])
    assert gateway_names == {"Towards Unresolved onward corridor"}


def test_loads_current_ncn_features_from_public_service(monkeypatch: pytest.MonkeyPatch) -> None:
    boundary = source_frames().boundary
    pages = [
        {
            "type": "FeatureCollection",
            "exceededTransferLimit": True,
            "features": [
                {
                    "type": "Feature",
                    "properties": {"RouteType": "NCN", "RouteNo": "24", "SegmentID": 12},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-2.5, 51.4], [-2.45, 51.41]],
                    },
                }
            ],
        },
        {
            "type": "FeatureCollection",
            "exceededTransferLimit": False,
            "features": [
                {
                    "type": "Feature",
                    "properties": {"RouteType": "LINK", "RouteNo": "24", "SegmentID": 13},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-2.45, 51.41], [-2.44, 51.415]],
                    },
                }
            ],
        },
    ]

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(pages.pop(0)).encode()

    seen: list[str] = []

    def fake_urlopen(request: object, timeout: int) -> Response:
        seen.append(request.full_url)  # type: ignore[attr-defined]
        assert timeout == 90
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = _load_ncn_features("https://example.test/FeatureServer", boundary)

    queries = [urllib.parse.parse_qs(urllib.parse.urlparse(url).query) for url in seen]
    assert [query["resultOffset"] for query in queries] == [["0"], ["1"]]
    assert all(
        query["where"] == ["RouteType IN ('NCN','LINK') OR Greenway = 'Yes'"]
        for query in queries
    )
    assert all(query["resultRecordCount"] == ["2000"] for query in queries)
    query = queries[0]
    assert query["geometryType"] == ["esriGeometryEnvelope"]
    assert list(result["RouteType"]) == ["NCN", "LINK"]
    assert list(result["RouteNo"]) == ["24", "24"]
    assert result.crs.to_epsg() == 4326


def test_ncn_pagination_rejects_a_transfer_limit_page_without_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "type": "FeatureCollection",
        "exceededTransferLimit": True,
        "features": [],
    }

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(payload).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Response())

    with pytest.raises(ValueError, match="transfer limit without returning features"):
        _load_ncn_features("https://example.test/FeatureServer", source_frames().boundary)


def test_loads_reclassified_ncn_features_as_declassified_route_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"FID": 7, "Desc_": "Former NCN route"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-2.5, 51.4], [-2.45, 51.41]],
                },
            }
        ],
    }

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(payload).encode()

    seen: list[str] = []

    def fake_urlopen(request: object, timeout: int) -> Response:
        seen.append(request.full_url)  # type: ignore[attr-defined]
        assert timeout == 90
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = _load_reclassified_ncn_features(
        "https://example.test/Reclassified/FeatureServer",
        source_frames().boundary,
    )

    query = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0]).query)
    assert query["where"] == ["1=1"]
    assert list(result["RouteType"]) == ["RECLASSIFIED"]
    context = derive_context_layers(source_frames().network, result)
    assert list(
        context.loc[
            context["feature_type"] == "declassified-ncn-route",
            "feature_type",
        ]
    ) == ["declassified-ncn-route"]


def test_grouped_osm_feature_queries_merge_and_deduplicate_stable_ids() -> None:
    calls: list[dict[str, object]] = []

    class FakeOSMnx:
        @staticmethod
        def features_from_polygon(_polygon: object, *, tags: dict[str, object]) -> gpd.GeoDataFrame:
            calls.append(tags)
            return gpd.GeoDataFrame(
                [
                    {
                        "element_type": "node",
                        "osmid": 1,
                        "name": "Shared feature",
                        "geometry": Point(-2.5, 51.4),
                    },
                    {
                        "element_type": "node",
                        "osmid": len(calls) + 1,
                        "name": f"Feature {len(calls) + 1}",
                        "geometry": Point(-2.5 + len(calls) / 100, 51.4),
                    },
                ],
                crs=4326,
            )

    result = _features_from_tag_groups(
        FakeOSMnx(),
        Polygon([(-2.6, 51.3), (-2.4, 51.3), (-2.4, 51.5), (-2.6, 51.3)]),
        ({"amenity": "school"}, {"shop": True}, {"entrance": True}),
    )

    assert calls == [{"amenity": "school"}, {"shop": True}, {"entrance": True}]
    assert set(result["osmid"]) == {1, 2, 3, 4}


class FakeOSMAdapter:
    def acquire(self, config: CouncilConfig) -> OSMData:
        return source_frames()


def test_osm_snapshot_is_attributable_and_reloadable(tmp_path: Path) -> None:
    config = base_config()
    config.source.snapshot_dir = tmp_path
    config.source.snapshot_id = "offline-osm"

    path = snapshot(config, osm_adapter=FakeOSMAdapter())

    manifest = json.loads((path / "snapshot.json").read_text())
    assert manifest["source_kind"] == "osm"
    assert "OpenStreetMap" in manifest["attribution"]
    assert set(manifest["files"]) >= {
        "boundary.geojson",
        "places.geojson",
        "network.geojson",
        "context.geojson",
    }
    assert set(manifest["file_sha256"]) == set(manifest["files"])
    reloaded = gpd.read_file(path / "places.geojson")
    assert set(reloaded["kind"]) >= {"community", "station_access", "cross_boundary_gateway"}
    context = gpd.read_file(path / "context.geojson")
    assert "rural" in set(context["network_scope"])
    assert set(context.loc[context["feature_type"] == "circulation-boundary", "category"]) == {
        "river"
    }
    assert snapshot(config, osm_adapter=FakeOSMAdapter()) == path


def test_osm_snapshot_persists_settlement_form_eligibility_and_scope(tmp_path: Path) -> None:
    class SettlementFormOSMAdapter:
        def acquire(self, config: CouncilConfig) -> OSMData:
            data = source_frames()
            centre = (-2.525, 51.395)
            branches = [
                LineString([centre, (-2.528, 51.395)]),
                LineString([centre, (-2.522, 51.395)]),
                LineString([centre, (-2.525, 51.392)]),
                LineString([centre, (-2.525, 51.398)]),
            ]
            data.network = gpd.GeoDataFrame(
                [
                    *data.network.to_dict("records"),
                    *[
                        {
                            "osmid": f"village-street-{index}",
                            "highway": "residential",
                            "geometry": geometry,
                        }
                        for index, geometry in enumerate(branches)
                    ],
                ],
                geometry="geometry",
                crs=4326,
            )
            return data

    config = base_config()
    config.source.snapshot_dir = tmp_path
    config.source.snapshot_id = "settlement-form-osm"
    config.source.urban_settlement_form.assessment_radius_km = 0.5
    config.source.urban_settlement_form.minimum_minor_street_length_km = 0.1
    config.source.urban_settlement_form.minimum_junction_count = 1

    path = snapshot(config, osm_adapter=SettlementFormOSMAdapter())

    places = gpd.read_file(path / "places.geojson").set_index("name")
    village = places.loc["Large Village"]
    assert bool(village["urban_circulation_eligible"])
    assert village["urban_eligibility_basis"] == "settlement-form"
    assert village["minor_street_length_km"] >= 0.1
    assert village["junction_count"] >= 1
    context = gpd.read_file(path / "context.geojson")
    a_road = context[context["feature_type"] == "a-road-spine"]
    assert "urban" in set(a_road["network_scope"])


def test_osm_snapshot_governs_official_road_classification(tmp_path: Path) -> None:
    classification_path = tmp_path / "classification.geojson"
    gpd.GeoDataFrame(
        [
            {
                "osmid": "official-a",
                "official_classification": "A road",
                "road_classification_number": "A4",
                "name_1": "Bath Road",
                "road_function": "A Road",
                "geometry": LineString([(-2.5, 51.39), (-2.5, 51.45)]),
            },
            {
                "osmid": "official-c",
                "official_classification": "Classified Unnumbered",
                "geometry": LineString([(-2.48, 51.39), (-2.48, 51.45)]),
            },
            {
                "osmid": "official-u",
                "official_classification": "Unclassified",
                "geometry": LineString([(-2.46, 51.39), (-2.46, 51.45)]),
            },
        ],
        crs=4326,
    ).to_file(classification_path, driver="GeoJSON")
    config = base_config()
    config.source.snapshot_dir = tmp_path / "snapshots"
    config.source.snapshot_id = "classified-osm"
    config.source.official_road_classification = OfficialRoadClassificationConfig(
        path=classification_path,
        source_id="banes-highways-list",
        effective_date="2026-04-01",
        licence="Open Government Licence v3.0",
    )

    path = snapshot(config, osm_adapter=FakeOSMAdapter())

    manifest = json.loads((path / "snapshot.json").read_text())
    governed = manifest["evidence_sources"]["official_road_classification"]
    assert governed["source_id"] == "banes-highways-list"
    assert governed["effective_date"] == "2026-04-01"
    assert governed["licence"] == "Open Government Licence v3.0"
    assert len(governed["content_fingerprint"]) == 64
    assert "official-road-classification.geojson" in manifest["files"]
    snapshotted = gpd.read_file(path / "official-road-classification.geojson")
    assert set(snapshotted["official_classification"]) == {
        "a-road",
        "classified-unnumbered",
        "unclassified",
    }
    assert set(snapshotted["source_id"]) == {"banes-highways-list"}
    assert set(snapshotted["content_fingerprint"]) == {governed["content_fingerprint"]}
    official_a = snapshotted[snapshotted["official_feature_id"] == "official-a"].iloc[0]
    assert official_a["official_road_number"] == "A4"
    assert official_a["official_road_name"] == "Bath Road"
    assert official_a["official_road_function"] == "A Road"


def test_osm_snapshot_governs_observed_through_traffic(tmp_path: Path) -> None:
    evidence_path = tmp_path / "observed-through-traffic.geojson"
    gpd.GeoDataFrame(
        [
            {
                "id": "traffic-study-1",
                "geometry": LineString([(-2.5, 51.4), (-2.48, 51.42)]),
            }
        ],
        crs=4326,
    ).to_file(evidence_path, driver="GeoJSON")
    config = base_config()
    config.source.snapshot_dir = tmp_path / "snapshots"
    config.source.snapshot_id = "observed-traffic-osm"
    config.source.observed_through_traffic = ObservedThroughTrafficConfig(
        path=evidence_path,
        source_id="banes-traffic-study",
        effective_date="2026-03-01",
        licence="Open Government Licence v3.0",
    )

    path = snapshot(config, osm_adapter=FakeOSMAdapter())

    manifest = json.loads((path / "snapshot.json").read_text())
    governed = manifest["evidence_sources"]["observed_through_traffic"]
    assert governed["source_id"] == "banes-traffic-study"
    assert governed["effective_date"] == "2026-03-01"
    assert governed["licence"] == "Open Government Licence v3.0"
    assert len(governed["content_fingerprint"]) == 64
    assert governed["snapshot_file"] == "observed-through-traffic.geojson"
    assert "observed-through-traffic.geojson" in manifest["files"]
    snapshotted = gpd.read_file(path / "observed-through-traffic.geojson")
    assert set(snapshotted["evidence_id"]) == {"traffic-study-1"}
    assert set(snapshotted["source_id"]) == {"banes-traffic-study"}
    assert set(snapshotted["content_fingerprint"]) == {governed["content_fingerprint"]}


@pytest.mark.live_osm
def test_live_banes_snapshot(tmp_path: Path) -> None:
    config = base_config()
    config.source.snapshot_dir = tmp_path
    config.source.snapshot_id = "banes-live-smoke"

    path = snapshot(config, replace=True)

    manifest = json.loads((path / "snapshot.json").read_text())
    assert manifest["council_id"] == "bath-and-north-east-somerset"
    places = gpd.read_file(path / "places.geojson")
    assert not places[places["kind"] == "community"].empty
