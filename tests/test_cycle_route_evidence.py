from __future__ import annotations

import geopandas as gpd
from shapely.geometry import LineString

from satn.evidence import mark_ncn_edges
from satn.routable_edge_enrichment import (
    decode_routable_edge_enrichment,
    encode_routable_edge_enrichment,
    policy_fingerprint,
)
from satn.routing import RoadGraph, choose_alignment


def _network() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "osmid": "current-edge",
                "u": "current-u",
                "v": "current-v",
                "highway": "cycleway",
                "geometry": LineString([(0, 0), (100, 0)]),
            },
            {
                "osmid": "former-edge",
                "u": "former-u",
                "v": "former-v",
                "highway": "cycleway",
                "geometry": LineString([(0, 100), (100, 100)]),
            },
            {
                "osmid": "greenway-edge",
                "u": "greenway-u",
                "v": "greenway-v",
                "highway": "cycleway",
                "geometry": LineString([(0, 200), (100, 200)]),
            },
            {
                "osmid": "plain-cycleway-edge",
                "u": "plain-u",
                "v": "plain-v",
                "highway": "cycleway",
                "geometry": LineString([(0, 300), (100, 300)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )


def _context() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "feature_type": "ncn-route",
                "geometry": LineString([(0, 0), (100, 0)]),
            },
            {
                "feature_type": "declassified-ncn-route",
                "geometry": LineString([(0, 100), (100, 100)]),
            },
            {
                "feature_type": "greenway-cycleway",
                "geometry": LineString([(0, 200), (100, 200)]),
            },
            {
                "feature_type": "ncn-link",
                "geometry": LineString([(0, 300), (100, 300)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )


def test_typed_cycle_route_evidence_survives_marking_and_road_graph() -> None:
    marked = mark_ncn_edges(_network(), _context())

    assert marked["satn_ncn"].tolist() == [True, True, True, False]
    assert marked["cycle_alignment_bases"].tolist() == [
        ("current-ncn",),
        ("reclassified-ncn",),
        ("greenway",),
        (),
    ]

    graph = RoadGraph(marked)
    edges = {attrs["edge_id"]: attrs for _left, _right, attrs in graph.graph.edges(data=True)}

    assert edges["current-edge"]["ncn"] is True
    assert edges["current-edge"]["cycle_alignment_bases"] == ("current-ncn",)
    assert edges["former-edge"]["cycle_alignment_bases"] == ("reclassified-ncn",)
    assert edges["greenway-edge"]["cycle_alignment_bases"] == ("greenway",)
    assert edges["plain-cycleway-edge"]["ncn"] is False
    assert edges["plain-cycleway-edge"]["cycle_alignment_bases"] == ()


def test_raw_osm_ncn_membership_survives_without_typed_context() -> None:
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "osm-way-current-ncn",
                "ncn": "yes",
                "highway": "cycleway",
                "u": "ncn-u",
                "v": "ncn-v",
                "geometry": LineString([(0, 0), (100, 0)]),
            },
            {
                "osmid": "osm-way-ncn-link",
                "ncn": "link",
                "highway": "path",
                "u": "link-u",
                "v": "link-v",
                "geometry": LineString([(0, 100), (100, 100)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )

    marked = mark_ncn_edges(network, _context().iloc[0:0].copy())

    assert marked["satn_ncn"].tolist() == [True, False]
    assert marked["cycle_alignment_bases"].tolist() == [("current-ncn",), ()]

    graph = RoadGraph(marked)
    edges = {attrs["edge_id"]: attrs for _left, _right, attrs in graph.graph.edges(data=True)}

    assert edges["osm-way-current-ncn"]["ncn"] is True
    assert edges["osm-way-current-ncn"]["cycle_alignment_bases"] == ("current-ncn",)
    assert edges["osm-way-ncn-link"]["ncn"] is False
    assert edges["osm-way-ncn-link"]["cycle_alignment_bases"] == ()

    selected, _options, _reason = choose_alignment(graph, "ncn-u", "ncn-v")
    assert selected is not None
    assert selected.role == "ncn-informed"
    assert selected.ncn_share == 1


def test_typed_cycle_route_evidence_survives_retained_edge_wire() -> None:
    marked = mark_ncn_edges(_network(), _context())
    marked["source_id"] = marked["osmid"]
    identities = {
        "snapshot_manifest_sha256": "a" * 64,
        "area_identity": "b" * 64,
        "network_identity": "c" * 64,
        "context_identity": "d" * 64,
        "policy_fingerprint": policy_fingerprint(),
        "implementation_identity": "e" * 64,
        "dependency_identity": "f" * 64,
    }

    payload = encode_routable_edge_enrichment(marked, identities=identities)
    restored = decode_routable_edge_enrichment(payload, identities=identities)

    assert restored["cycle_alignment_bases"].tolist() == [
        ("current-ncn",),
        ("reclassified-ncn",),
        ("greenway",),
        (),
    ]
    assert restored["satn_ncn"].tolist() == [True, True, True, False]
