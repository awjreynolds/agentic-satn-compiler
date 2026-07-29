from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon

import satn.compiler as compiler
from satn.agents import FakeAgentRuntime
from satn.backbone import BackboneAssembly
from satn.compiler import compile_network
from satn.models import (
    AgentRecord,
    CouncilConfig,
    PublishedFeatureReference,
    TrafficLight,
)
from satn.publisher import publish, validate_publication
from satn.routing import RoadGraph, choose_alignment
from satn.urban import derive_urban_structure


def test_urban_spines_and_low_traffic_fabrics_share_one_representation() -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "urban-centre",
                "name": "Urban Centre",
                "kind": "community",
                "place_class": "neighbourhood",
                "geometry": Point(0, 0),
            },
            {
                "place_id": "portal-west",
                "name": "West portal",
                "kind": "community_portal",
                "place_class": "neighbourhood",
                "parent_place_id": "urban-centre",
                "geometry": Point(-0.01, 0),
            },
            {
                "place_id": "portal-east",
                "name": "East portal",
                "kind": "community_portal",
                "place_class": "neighbourhood",
                "parent_place_id": "urban-centre",
                "geometry": Point(0.01, 0),
            },
        ],
        crs=4326,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "main",
                "highway": "primary",
                "geometry": LineString([(0, -0.02), (0, 0.02)]),
            },
            {
                "osmid": "minor-west",
                "highway": "residential",
                "geometry": LineString([(-0.015, 0), (-0.005, 0)]),
            },
            {
                "osmid": "minor-west-2",
                "highway": "residential",
                "geometry": LineString([(-0.005, 0), (-0.005, 0.005)]),
            },
            {
                "osmid": "minor-east",
                "highway": "unclassified",
                "geometry": LineString([(0.005, 0), (0.015, 0)]),
            },
            {
                "osmid": "minor-east-2",
                "highway": "residential",
                "geometry": LineString([(0.005, 0), (0.005, 0.005)]),
            },
            {
                "osmid": "misclassified-overlap",
                "highway": "residential",
                "geometry": LineString([(0, -0.005), (0, 0.005)]),
            },
        ],
        crs=4326,
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": f"official-a-{index}",
                "official_classification": "a-road",
                "source_id": "council-highways",
                "effective_date": "2026-01-01",
                "licence": "Open Government Licence v3.0",
                "content_fingerprint": "abc123",
                "geometry": geometry,
            }
            for index, geometry in enumerate(
                (
                    LineString([(0, -0.02), (0, -0.01), (0, 0), (0, 0.01), (0, 0.02)]),
                    LineString([(-0.01, -0.01), (-0.01, 0.01)]),
                    LineString([(0.01, -0.01), (0.01, 0.01)]),
                    LineString([(-0.01, -0.01), (0, -0.01), (0.01, -0.01)]),
                    LineString([(-0.01, 0.01), (0, 0.01), (0.01, 0.01)]),
                ),
                start=1,
            )
        ],
        crs=4326,
    )

    urban = derive_urban_structure(places, network, official)
    spines = urban.spines
    unknowns = urban.classification_unknowns
    areas = urban.low_traffic_areas
    portals = urban.low_traffic_area_portals

    assert len(spines) == 5
    assert set(spines["role"]) == {"urban-main-road-spine"}
    assert set(spines["intervention"]) == {"protected-cycle-infrastructure"}
    assert set(spines["official_classification"]) == {"a-road"}
    assert set(spines["classification_status"]) == {"governed-official"}
    assert unknowns.empty
    assert "Major engineering" in spines.iloc[0]["intervention_assumption"]
    assert areas.empty
    assert portals.empty


def test_open_urban_street_fabric_is_not_promoted_to_candidate_areas() -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "town",
                "name": "Town",
                "kind": "community",
                "place_class": "town",
                "geometry": Point(400000, 200000),
            }
        ],
        crs=27700,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "west-1",
                "highway": "residential",
                "geometry": LineString([(399600, 200000), (400000, 200000)]),
            },
            {
                "osmid": "west-2",
                "highway": "residential",
                "geometry": LineString([(399800, 199800), (399800, 200200)]),
            },
            {
                "osmid": "east-1",
                "highway": "residential",
                "geometry": LineString([(400000, 200000), (400400, 200000)]),
            },
            {
                "osmid": "east-2",
                "highway": "unclassified",
                "geometry": LineString([(400200, 199800), (400200, 200200)]),
            },
        ],
        crs=27700,
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "classified-divider",
                "official_classification": "b-road",
                "source_id": "council-highways",
                "effective_date": "2026-01-01",
                "licence": "Open Government Licence v3.0",
                "content_fingerprint": "divider",
                "geometry": LineString([(400000, 199500), (400000, 200500)]),
            }
        ],
        crs=27700,
    )

    urban = derive_urban_structure(places, network, official)

    assert urban.low_traffic_areas.empty
    assert urban.low_traffic_area_portals.empty


def test_sparse_open_street_fabric_is_not_promoted_to_a_candidate_area() -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "town",
                "name": "Town",
                "kind": "community",
                "place_class": "town",
                "geometry": Point(400000, 200000),
            }
        ],
        crs=27700,
    )
    minor_lines = [
        LineString([(399000, 200000), (400000, 200000), (401000, 200000)]),
        LineString([(400000, 199000), (400000, 200000), (400000, 201000)]),
        LineString([(399800, 199800), (400200, 199800)]),
        LineString([(399800, 200200), (400200, 200200)]),
    ]
    network = gpd.GeoDataFrame(
        [
            {"osmid": f"minor-{index}", "highway": "residential", "geometry": geometry}
            for index, geometry in enumerate(minor_lines)
        ],
        crs=27700,
    )
    boundary = LineString(
        [
            (399000, 199000),
            (401000, 199000),
            (401000, 201000),
            (399000, 201000),
            (399000, 199000),
        ]
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "outer-boundary",
                "official_classification": "classified-unnumbered",
                "source_id": "council-highways",
                "effective_date": "2026-01-01",
                "licence": "Open Government Licence v3.0",
                "content_fingerprint": "outer",
                "geometry": boundary,
            },
            {
                "official_feature_id": "classified-divider",
                "official_classification": "b-road",
                "source_id": "council-highways",
                "effective_date": "2026-01-01",
                "licence": "Open Government Licence v3.0",
                "content_fingerprint": "divider",
                "geometry": LineString([(400000, 199000), (400000, 201000)]),
            },
        ],
        crs=27700,
    )

    urban = derive_urban_structure(places, network, official)
    assert urban.low_traffic_areas.empty
    assert urban.low_traffic_area_portals.empty


def test_geojson_array_tags_preserve_urban_fabric_and_a_road_routing(tmp_path: Path) -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "urban-centre",
                "name": "Urban Centre",
                "kind": "community",
                "place_class": "neighbourhood",
                "geometry": Point(0, 0),
            }
        ],
        crs=27700,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "main",
                "highway": ["primary"],
                "ref": ["A4"],
                "geometry": LineString([(0, -0.02), (0, 0.02)]),
            },
            {
                "osmid": "minor-west",
                "highway": ["residential"],
                "geometry": LineString([(-0.01, 0), (-0.005, 0)]),
            },
            {
                "osmid": "minor-west-2",
                "highway": ["residential"],
                "geometry": LineString([(-0.005, 0), (-0.005, 0.005)]),
            },
            {
                "osmid": "minor-east",
                "highway": ["unclassified"],
                "geometry": LineString([(0.005, 0), (0.01, 0)]),
            },
            {
                "osmid": "minor-east-2",
                "highway": ["residential"],
                "geometry": LineString([(0.005, 0), (0.005, 0.005)]),
            },
        ],
        crs=4326,
    )
    network_path = tmp_path / "network.geojson"
    network.to_file(network_path, driver="GeoJSON")
    reloaded = gpd.read_file(network_path)
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": f"official-a-{index}",
                "official_classification": "a-road",
                "source_id": "council-highways",
                "effective_date": "2026-01-01",
                "licence": "Open Government Licence v3.0",
                "content_fingerprint": "abc123",
                "geometry": geometry,
            }
            for index, geometry in enumerate(
                (
                    LineString([(0, -0.02), (0, 0.02)]),
                    LineString([(-0.01, -0.01), (-0.01, 0.01)]),
                    LineString([(0.01, -0.01), (0.01, 0.01)]),
                    LineString([(-0.01, -0.01), (0.01, -0.01)]),
                    LineString([(-0.01, 0.01), (0.01, 0.01)]),
                ),
                start=1,
            )
        ],
        crs=4326,
    )

    urban = derive_urban_structure(places, reloaded, official)
    graph = RoadGraph(reloaded)
    selected, _, _ = choose_alignment(
        graph,
        graph.nearest_node(Point(0, -0.02))[0],
        graph.nearest_node(Point(0, 0.02))[0],
    )

    assert type(reloaded.iloc[0]["highway"]).__name__ == "ndarray"
    assert urban.low_traffic_areas.empty
    assert selected is not None
    assert selected.role == "strategic-spine"
    assert selected.a_road_share == 1


def test_tuple_and_set_tags_preserve_a_road_routing_semantics() -> None:
    for highway in (("primary",), {"primary"}):
        graph = RoadGraph(
            gpd.GeoDataFrame(
                [
                    {
                        "osmid": "main",
                        "highway": highway,
                        "ref": ("A4",) if isinstance(highway, tuple) else {"A4"},
                        "geometry": LineString([(0, 0), (0.01, 0)]),
                    }
                ],
                crs=4326,
            )
        )

        selected, _, _ = choose_alignment(
            graph,
            graph.nearest_node(Point(0, 0))[0],
            graph.nearest_node(Point(0.01, 0))[0],
        )

        assert selected is not None
        assert selected.role == "strategic-spine"
        assert selected.a_road_share == 1


def test_osm_tags_do_not_override_missing_or_unclassified_official_evidence() -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "urban-centre",
                "name": "Urban Centre",
                "kind": "community",
                "place_class": "town",
                "geometry": Point(0, 0),
            }
        ],
        crs=4326,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "osm-primary",
                "highway": "primary",
                "geometry": LineString([(0, -0.02), (0, 0.02)]),
            }
        ],
        crs=4326,
    )
    unclassified = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "official-u",
                "official_classification": "unclassified",
                "source_id": "council-highways",
                "effective_date": "2026-01-01",
                "licence": "OGL v3.0",
                "content_fingerprint": "abc123",
                "geometry": LineString([(0, -0.02), (0, 0.02)]),
            }
        ],
        crs=4326,
    )

    missing = derive_urban_structure(places, network)
    unclassified_result = derive_urban_structure(places, network, unclassified)

    assert missing.spines.empty
    assert missing.classification_unknowns.empty
    assert unclassified_result.spines.empty
    assert unclassified_result.classification_unknowns.empty

    unknown = unclassified.copy()
    unknown["official_classification"] = "unknown"
    unknowns = derive_urban_structure(places, network, unknown).classification_unknowns
    assert len(unknowns) == 1
    assert unknowns.iloc[0]["classification_status"] == "explicit-unknown"
    assert unknowns.iloc[0]["role"] == "urban-road-classification-unknown"


def test_official_a_b_and_classified_unnumbered_are_stable_urban_spines() -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "urban-centre",
                "name": "Urban Centre",
                "kind": "community",
                "place_class": "town",
                "geometry": Point(0, 0),
            }
        ],
        crs=4326,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "minor",
                "highway": "residential",
                "geometry": LineString([(-0.02, 0), (0.02, 0)]),
            }
        ],
        crs=4326,
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": f"official-{classification}",
                "official_classification": classification,
                "source_id": "council-highways",
                "effective_date": "2026-01-01",
                "licence": "OGL v3.0",
                "content_fingerprint": "abc123",
                "geometry": geometry,
            }
            for classification, geometry in (
                (
                    "a-road",
                    LineString(
                        [
                            (-0.01, -0.01),
                            (0.01, -0.01),
                            (0.01, 0.01),
                            (-0.01, 0.01),
                            (-0.01, -0.01),
                        ]
                    ),
                ),
                ("b-road", LineString([(0.0, -0.01), (0.0, 0.01)])),
                ("classified-unnumbered", LineString([(0.005, -0.01), (0.005, 0.01)])),
            )
        ],
        crs=4326,
    )

    first = derive_urban_structure(places, network, official)
    second = derive_urban_structure(places, network, official)

    assert set(first.spines["official_classification"]) == {
        "a-road",
        "b-road",
        "classified-unnumbered",
    }
    assert list(first.spines["structure_id"]) == list(second.spines["structure_id"])
    assert set(first.spines["source_id"]) == {"council-highways"}
    assert first.classification_unknowns.empty
    assert second.classification_unknowns.empty
    assert list(first.low_traffic_area_portals["portal_id"]) == list(
        second.low_traffic_area_portals["portal_id"]
    )


def test_governed_urban_a_road_evidence_extends_the_urban_spine_extent() -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "urban-centre",
                "name": "Urban Centre",
                "kind": "community",
                "place_class": "town",
                "geometry": Point(0, 0),
            }
        ],
        crs=27700,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "urban-street",
                "highway": "residential",
                "geometry": LineString([(-100, 0), (100, 0)]),
            }
        ],
        crs=27700,
    )
    context = gpd.GeoDataFrame(
        [
            {
                "evidence_id": "urban-a-evidence",
                "feature_type": "a-road-spine",
                "network_scope": "urban",
                "geometry": LineString([(2500, -100), (2500, 100)]),
            },
            {
                "evidence_id": "rural-a-evidence",
                "feature_type": "a-road-spine",
                "network_scope": "rural",
                "geometry": LineString([(3500, -100), (3500, 100)]),
            },
        ],
        crs=27700,
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": feature_id,
                "official_classification": "a-road",
                "source_id": "official-roads",
                "effective_date": "2026-01-01",
                "licence": "OGL v3.0",
                "content_fingerprint": "abc123",
                "geometry": geometry,
            }
            for feature_id, geometry in (
                ("urban-a", LineString([(2530, -100), (2530, 100)])),
                ("rural-a", LineString([(3530, -100), (3530, 100)])),
            )
        ],
        crs=27700,
    )

    urban = derive_urban_structure(places, network, official, context)

    assert list(urban.spines["official_feature_id"]) == ["urban-a"]
    assert urban.spines.iloc[0]["role"] == "urban-main-road-spine"


def test_governed_official_a_roads_define_complete_urban_evidence() -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": place_id,
                "name": name,
                "kind": "community",
                "place_class": "town",
                "geometry": Point(x, 0),
            }
            for place_id, name, x in (
                ("west", "West", 0),
                ("east", "East", 100),
            )
        ],
        crs=27700,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "urban-street",
                "highway": "residential",
                "geometry": LineString([(0, 0), (100, 0)]),
            }
        ],
        crs=27700,
    )
    context = gpd.GeoDataFrame(
        [
            {
                "evidence_id": f"urban-a-{index}",
                "feature_type": "a-road-spine",
                "name": f"A{index}",
                "category": "A-road strategic spine",
                "source_id": f"osm-a-{index}",
                "network_scope": "urban",
                "geometry": geometry,
            }
            for index, geometry in enumerate(
                (
                    LineString([(0, 0), (1000, 0)]),
                    LineString([(0, 500), (1000, 500)]),
                ),
                start=1,
            )
        ],
        crs=27700,
    )

    def official_roads(*, include_second_a_road: bool) -> gpd.GeoDataFrame:
        roads = [
            ("official-a-1", "a-road", LineString([(0, 40), (1000, 40)])),
            ("official-b-extra", "b-road", LineString([(0, 900), (1000, 900)])),
        ]
        if include_second_a_road:
            roads.append(("official-a-2", "a-road", LineString([(0, 540), (1000, 540)])))
        return gpd.GeoDataFrame(
            [
                {
                    "official_feature_id": feature_id,
                    "official_classification": classification,
                    "source_id": "official-roads",
                    "effective_date": "2026-01-01",
                    "licence": "OGL v3.0",
                    "content_fingerprint": "abc123",
                    "geometry": geometry,
                }
                for feature_id, classification, geometry in roads
            ],
            crs=27700,
        )

    config = CouncilConfig.from_yaml(
        Path(__file__).parents[1] / "examples" / "fixture" / "council.yaml"
    )
    source = {
        "places": places,
        "network": network,
        "boundary": gpd.GeoDataFrame(geometry=[], crs=27700),
        "context": context,
    }
    missing_official = compile_network(
        config,
        source,
        FakeAgentRuntime(),
    )
    governed_one = compile_network(
        config,
        source | {"official_road_classification": official_roads(include_second_a_road=False)},
        FakeAgentRuntime(),
    )
    represented = compile_network(
        config,
        source | {"official_road_classification": official_roads(include_second_a_road=True)},
        FakeAgentRuntime(),
    )

    assert missing_official.criteria["urban_network"]["official_main_road_spines"] == "grey"
    assert missing_official.criteria["urban_network"]["urban_a_road_evidence_coverage"] == "red"
    assert missing_official.compilation_diagnostics["urban_a_road_spine_coverage"] == {
        "source_alignment_tolerance_m": 100.0,
        "evidence_segment_count": 2,
        "total_km": 2.0,
        "unmatched_km": 2.0,
    }
    assert list(governed_one.a_road_spines["evidence_id"]) == ["official-a-1"]
    assert governed_one.criteria["urban_network"]["official_main_road_spines"] == "green"
    assert governed_one.criteria["urban_network"]["urban_a_road_evidence_coverage"] == "green"
    assert governed_one.compilation_diagnostics["urban_a_road_spine_coverage"] == {
        "source_alignment_tolerance_m": 100.0,
        "evidence_segment_count": 1,
        "total_km": 1.0,
        "unmatched_km": 0.0,
    }
    assert represented.criteria["urban_network"]["official_main_road_spines"] == "green"
    assert represented.criteria["urban_network"]["urban_a_road_evidence_coverage"] == "green"
    assert represented.compilation_diagnostics["urban_a_road_spine_coverage"] == {
        "source_alignment_tolerance_m": 100.0,
        "evidence_segment_count": 2,
        "total_km": 2.0,
        "unmatched_km": 0.0,
    }
    assert "b-road" not in set(represented.urban_spines["official_classification"])


def test_governed_road_classification_supersedes_conflicting_osm_a_road_context() -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": place_id,
                "name": name,
                "kind": "community",
                "place_class": "town",
                "geometry": Point(x, 0),
            }
            for place_id, name, x in (
                ("west", "West", 0),
                ("east", "East", 100),
            )
        ],
        crs=27700,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "urban-street",
                "highway": "residential",
                "geometry": LineString([(0, 0), (100, 0)]),
            }
        ],
        crs=27700,
    )
    context = gpd.GeoDataFrame(
        [
            {
                "evidence_id": "osm-conflicting-a-road",
                "feature_type": "a-road-spine",
                "name": "A4017",
                "category": "A-road strategic spine",
                "source_id": "osm-way-203032222",
                "network_scope": "urban",
                "geometry": LineString([(0, 500), (1000, 500)]),
            },
            {
                "evidence_id": "osm-unmatched-a-road",
                "feature_type": "a-road-spine",
                "name": "A999",
                "category": "A-road strategic spine",
                "source_id": "osm-way-unmatched",
                "network_scope": "urban",
                "geometry": LineString([(0, 1500), (1000, 1500)]),
            },
        ],
        crs=27700,
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "official-classified-unnumbered",
                "official_classification": "classified-unnumbered",
                "source_id": "os-open-roads-2026-04-07",
                "effective_date": "2026-04-07",
                "licence": "OGL v3.0",
                "content_fingerprint": "official-fingerprint",
                "geometry": LineString([(0, 500), (1000, 500)]),
            },
            {
                "official_feature_id": "official-unclassified",
                "official_classification": "unclassified",
                "source_id": "os-open-roads-2026-04-07",
                "effective_date": "2026-04-07",
                "licence": "OGL v3.0",
                "content_fingerprint": "official-fingerprint",
                "geometry": LineString([(0, 510), (1000, 510)]),
            },
            {
                "official_feature_id": "official-a-road",
                "official_classification": "a-road",
                "source_id": "os-open-roads-2026-04-07",
                "effective_date": "2026-04-07",
                "licence": "OGL v3.0",
                "content_fingerprint": "official-fingerprint",
                "geometry": LineString([(0, 1000), (1000, 1000)]),
            },
        ],
        crs=27700,
    )
    config = CouncilConfig.from_yaml(
        Path(__file__).parents[1] / "examples" / "fixture" / "council.yaml"
    )

    compiled = compile_network(
        config,
        {
            "places": places,
            "network": network,
            "boundary": gpd.GeoDataFrame(geometry=[], crs=27700),
            "context": context,
            "official_road_classification": official,
        },
        FakeAgentRuntime(),
    )

    assert list(compiled.a_road_spines["evidence_id"]) == ["official-a-road"]
    assert list(compiled.a_road_spines["source_id"]) == ["os-open-roads-2026-04-07"]
    assert list(compiled.a_road_spines["network_scope"]) == ["urban"]
    assert compiled.criteria["urban_network"]["urban_a_road_evidence_coverage"] == "green"
    assert compiled.compilation_diagnostics["road_classification_disagreements"] == [
        {
            "disagreement_type": "official-non-a-road",
            "osm_evidence_id": "osm-conflicting-a-road",
            "osm_source_id": "osm-way-203032222",
            "official_feature_id": "official-classified-unnumbered",
            "official_classification": "classified-unnumbered",
            "official_source_id": "os-open-roads-2026-04-07",
            "official_content_fingerprint": "official-fingerprint",
        },
        {
            "disagreement_type": "official-non-a-road",
            "osm_evidence_id": "osm-conflicting-a-road",
            "osm_source_id": "osm-way-203032222",
            "official_feature_id": "official-unclassified",
            "official_classification": "unclassified",
            "official_source_id": "os-open-roads-2026-04-07",
            "official_content_fingerprint": "official-fingerprint",
        },
        {
            "disagreement_type": "no-overlapping-official-road",
            "osm_evidence_id": "osm-unmatched-a-road",
            "osm_source_id": "osm-way-unmatched",
            "official_feature_id": None,
            "official_classification": None,
            "official_source_id": None,
            "official_content_fingerprint": None,
        },
    ]


def test_candidate_areas_use_only_qualifying_boundaries_and_flag_through_traffic() -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "urban-centre",
                "name": "Urban Centre",
                "kind": "community",
                "place_class": "town",
                "geometry": Point(0, 0),
            }
        ],
        crs=27700,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "residential-1",
                "highway": "residential",
                "observed_through_traffic": True,
                "geometry": LineString([(-100, 0), (0, 0)]),
            },
            {
                "osmid": "residential-2",
                "highway": "unclassified",
                "observed_through_traffic": False,
                "geometry": LineString([(0, 0), (100, 0)]),
            },
            {
                "osmid": "residential-3",
                "highway": "residential",
                "observed_through_traffic": False,
                "geometry": LineString([(0, 0), (0, 100)]),
            },
            {
                "osmid": "disconnected-1",
                "highway": "residential",
                "observed_through_traffic": False,
                "geometry": LineString([(-80, 80), (-40, 80)]),
            },
            {
                "osmid": "disconnected-2",
                "highway": "residential",
                "observed_through_traffic": False,
                "geometry": LineString([(-40, 80), (-40, 90)]),
            },
        ],
        crs=27700,
    )
    context = gpd.GeoDataFrame(
        [
            {
                "evidence_id": "built-edge",
                "feature_type": "circulation-boundary",
                "name": "Open-land edge",
                "category": "built-up-edge",
                "source_id": "built-edge-source",
                "geometry": LineString(
                    [
                        (-100, -100),
                        (100, -100),
                        (100, 100),
                        (-100, 100),
                        (-100, -100),
                    ]
                ),
            },
            {
                "evidence_id": "ward-line",
                "feature_type": "circulation-boundary",
                "name": "Ward boundary",
                "category": "administrative-boundary",
                "source_id": "ward-source",
                "geometry": LineString([(-100, -100), (-100, 100)]),
            },
        ],
        crs=27700,
    )

    urban = derive_urban_structure(places, network, context=context)
    areas = urban.low_traffic_areas
    portals = urban.low_traffic_area_portals

    assert len(areas) == 1
    area = areas.iloc[0]
    assert area["status"] == "candidate"
    assert area["intervention_need"] == "observed-through-traffic"
    assert area["permeability_representation"] == "area-no-internal-centreline"
    assert "built-edge" in area["boundary_ids"]
    assert "ward-line" not in area["boundary_ids"]
    assert set(portals["boundary_id"]) == {"built-edge"}
    assert portals["portal_id"].is_unique


def test_candidate_area_requires_a_complete_governed_enclosure() -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "urban-centre",
                "name": "Urban Centre",
                "kind": "community",
                "place_class": "town",
                "geometry": Point(0, 0),
            }
        ],
        crs=27700,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "west-residential",
                "highway": "residential",
                "geometry": LineString([(-250, 0), (0, 0)]),
            },
            {
                "osmid": "east-residential",
                "highway": "residential",
                "geometry": LineString([(0, 0), (250, 0)]),
            },
        ],
        crs=27700,
    )
    context = gpd.GeoDataFrame(
        [
            {
                "evidence_id": "open-boundary",
                "feature_type": "circulation-boundary",
                "name": "Railway",
                "category": "railway",
                "source_id": "railway-source",
                "geometry": LineString([(0, -500), (0, 500)]),
            }
        ],
        crs=27700,
    )

    urban = derive_urban_structure(places, network, context=context)

    assert urban.low_traffic_areas.empty
    assert urban.low_traffic_area_portals.empty


def test_candidate_area_requires_residential_street_fabric() -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "urban-centre",
                "name": "Urban Centre",
                "kind": "community",
                "place_class": "town",
                "geometry": Point(0, 0),
            }
        ],
        crs=27700,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "industrial-service",
                "highway": "service",
                "geometry": LineString([(-500, 0), (0, 0)]),
            },
            {
                "osmid": "industrial-unclassified",
                "highway": "unclassified",
                "geometry": LineString([(0, 0), (500, 0)]),
            },
        ],
        crs=27700,
    )
    context = gpd.GeoDataFrame(
        [
            {
                "evidence_id": "built-edge",
                "feature_type": "circulation-boundary",
                "name": "Built-up edge",
                "category": "built-up-edge",
                "source_id": "built-edge-source",
                "geometry": LineString(
                    [(-500, -500), (500, -500), (500, 500), (-500, 500), (-500, -500)]
                ),
            }
        ],
        crs=27700,
    )

    urban = derive_urban_structure(places, network, context=context)

    assert urban.low_traffic_areas.empty
    assert urban.low_traffic_area_portals.empty


def test_urban_spines_are_pruned_until_every_terminus_reaches_primary_network() -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "urban-centre",
                "name": "Urban Centre",
                "kind": "community",
                "place_class": "town",
                "geometry": Point(0, 0),
            }
        ],
        crs=27700,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "local-street",
                "highway": "residential",
                "geometry": LineString([(-100, 0), (100, 0)]),
            }
        ],
        crs=27700,
    )
    official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": feature_id,
                "official_classification": classification,
                "source_id": "official-roads",
                "effective_date": "2026-01-01",
                "licence": "OGL v3.0",
                "content_fingerprint": "abc123",
                "geometry": geometry,
            }
            for feature_id, classification, geometry in (
                ("primary-a", "a-road", LineString([(-1000, 0), (1000, 0)])),
                ("anchored-b", "b-road", LineString([(0, 0), (0, 1000)])),
                ("orphan-b", "b-road", LineString([(500, 0), (500, 600)])),
            )
        ],
        crs=27700,
    )
    context = gpd.GeoDataFrame(
        [
            {
                "evidence_id": "ncn-primary",
                "feature_type": "ncn-route",
                "name": "NCN",
                "category": "National Cycle Network",
                "source_id": "ncn-source",
                "network_scope": "urban",
                "geometry": LineString([(-1000, 1000), (1000, 1000)]),
            }
        ],
        crs=27700,
    )

    urban = derive_urban_structure(places, network, official, context)

    assert set(urban.spines["official_feature_id"]) == {"primary-a", "anchored-b"}


def test_compile_network_keeps_running_when_backbone_connector_needs_refinement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The compiler promotes an unsafe backbone connector to a visible Network Gap."""
    crs = 27700
    strategic_spines = gpd.GeoDataFrame(
        [
            {
                "spine_id": "far-primary",
                "spine_kind": "ncn",
                "name": "Far primary",
                "geometry": LineString([(-101, -10), (-101, 10)]),
            },
            {
                "spine_id": "right-primary",
                "spine_kind": "ncn",
                "name": "Right primary",
                "geometry": LineString([(100, -10), (100, 10)]),
            },
        ],
        crs=crs,
    )
    invalid_connectors = gpd.GeoDataFrame(
        [
            {
                "cross_spine_connector_id": "needs-refinement",
                "from_root_spine_id": "far-primary",
                "from_root_spine_name": "Far primary",
                "to_root_spine_id": "right-primary",
                "to_root_spine_name": "Right primary",
                "distance_km": 0.1,
                "source_ids": json.dumps(["ncn-connector-evidence"]),
                "provenance": "{}",
                "geometry_semantics": "validated connector",
                "geometry": LineString([(0, 0), (100, 0)]),
            }
        ],
        crs=crs,
    )
    empty_obligations = gpd.GeoDataFrame(
        columns=[
            "network_scope",
            "obligation_kind",
            "service_status",
            "community_id",
            "network_role",
            "place_id",
            "geometry",
        ],
        geometry="geometry",
        crs=crs,
    )
    empty_connections = gpd.GeoDataFrame(
        columns=[
            "access_connection_id",
            "obligation_kind",
            "place_id",
            "root_spine_id",
            "criterion_distance",
            "geometry",
        ],
        geometry="geometry",
        crs=crs,
    )
    empty_branches = gpd.GeoDataFrame(
        columns=["branch_id", "root_spine_id", "place_ids", "connection_ids", "geometry"],
        geometry="geometry",
        crs=crs,
    )
    meetings = gpd.GeoDataFrame(
        [
            {
                "meeting_connection_id": "meeting-withheld-connector",
                "network_role": "branch-meeting-connection",
                "from_place_id": "village-one",
                "from_place_name": "Village one",
                "to_place_id": "village-two",
                "to_place_name": "Village two",
                "from_branch_id": "branch-left",
                "to_branch_id": "branch-right",
                "from_root_spine_id": "far-primary",
                "to_root_spine_id": "right-primary",
                "distance_km": 0.1,
                "status": "validated",
                "agent_outcome": "accepted meeting",
                "agent_attempt_count": 0,
                "agent_findings": "[]",
                "agent_decision_request_id": None,
                "agent_decision_choice_id": None,
                "agent_decision_action": None,
                "agent_decision_responder_mode": None,
                "intervention_archetype": "cross-spine link",
                "selection_reason": "Fixture accepted branch meeting",
                "geometry_semantics": "fixture routed line",
                "from_attachment_node": "0,0",
                "to_attachment_node": "100,0",
                "source_ids": "[]",
                "provenance": "{}",
                "criterion_continuity": "green",
                "criterion_bidirectional": "green",
                "criterion_distance": "green",
                "topography_alternative_trigger": False,
                "topography_comparison_status": "not-triggered",
                "topography_comparison_rationale": "fixture",
                "topography_original_role": "branch-meeting-connection",
                "topography_selected_role": "branch-meeting-connection",
                "alignment_options": "[]",
                "geometry": LineString([(0, 0), (100, 0)]),
            }
        ],
        geometry="geometry",
        crs=crs,
    )
    accepted_record = AgentRecord(
        connection_id="meeting-withheld-connector",
        governing_criterion="continuity",
        governing_status=TrafficLight.GREEN,
        review_policy=(),
        review_required=False,
        network_role="branch-meeting-connection",
        runtime="not-invoked",
        model="not-invoked",
        decision="accept",
        derived_features=[
            PublishedFeatureReference(
                feature_id="needs-refinement",
                network_role="cross-spine-connector",
            )
        ],
    )
    monkeypatch.setattr(
        compiler,
        "assemble_backbone_outward",
        lambda *args, **kwargs: BackboneAssembly(
            connections=empty_connections,
            obligations=empty_obligations,
            branches=empty_branches,
            meeting_connections=meetings,
            cross_spine_connectors=invalid_connectors,
            gaps=gpd.GeoDataFrame(columns=compiler.GAP_COLUMNS, geometry="geometry", crs=crs),
            gateway_count=0,
            connected_gateway_count=0,
            agent_records=[accepted_record],
            compilation_diagnostics={},
        ),
    )
    monkeypatch.setattr(compiler, "_strategic_spines", lambda _: strategic_spines)
    config = CouncilConfig.from_yaml(
        Path(__file__).parents[1] / "examples" / "fixture" / "council.yaml"
    )
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "village-one",
                "name": "Village one",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(0, 0),
            },
            {
                "place_id": "village-two",
                "name": "Village two",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(100, 0),
            },
        ],
        crs=crs,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "test-road",
                "highway": "residential",
                "geometry": LineString([(0, 0), (100, 0)]),
            }
        ],
        crs=crs,
    )

    compiled = compile_network(
        config,
        {
            "places": places,
            "network": network,
            "boundary": gpd.GeoDataFrame(
                [
                    {
                        "boundary_id": "fixture-boundary",
                        "geometry": Polygon([(-200, -200), (200, -200), (200, 200), (-200, 200)]),
                    }
                ],
                crs=crs,
            ),
        },
        FakeAgentRuntime(),
    )

    assert compiled.cross_spine_connectors.empty
    assert compiled.gaps["network_role"].tolist() == ["cross-spine-connector-gap"]
    assert compiled.status == "reviewable"
    record = compiled.agent_records[0]
    assert record.derived_features == []
    assert [reference.feature_id for reference in record.withheld_derived_features] == [
        "needs-refinement"
    ]
    assert record.withheld_derived_features[0].finding_id == compiled.gaps.iloc[0]["connection_id"]

    config.publication.output_dir = tmp_path / "published"
    artifacts = publish(config, compiled, "run-withheld-connector")
    assert artifacts["geojson"].is_file()
    validate_publication(config.publication.output_dir, config)

    records_path = config.publication.output_dir / "agent-records.json"
    original_records = json.loads(records_path.read_text(encoding="utf-8"))
    withheld = original_records["records"][0]["withheld_derived_features"][0]
    for mutation, message in (
        (
            lambda payload: payload["records"][0]["withheld_derived_features"].append(
                deepcopy(withheld)
            ),
            "duplicate connector reference",
        ),
        (
            lambda payload: payload["records"][0]["withheld_derived_features"][0].update(
                {"finding_id": "missing-finding"}
            ),
            "wrong finding",
        ),
        (
            lambda payload: payload["records"][0]["withheld_derived_features"].append(
                {
                    **deepcopy(withheld),
                    "feature_id": "conflicting-connector",
                }
            ),
            "conflicting finding reference",
        ),
        (
            lambda payload: payload["records"][0].update({"decision": "reject"}),
            "non-accepted AgentRecord cannot establish or withhold",
        ),
        (
            lambda payload: payload["records"][0]["derived_features"].extend(
                [
                    {
                        "feature_id": withheld["feature_id"],
                        "network_role": "cross-spine-connector",
                    },
                    {
                        "feature_id": withheld["feature_id"],
                        "network_role": "cross-spine-connector",
                    },
                ]
            ),
            "accepted agent records authoritative feature has duplicate identifier",
        ),
    ):
        tampered = deepcopy(original_records)
        mutation(tampered)
        records_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            validate_publication(config.publication.output_dir, config)
    records_path.write_text(json.dumps(original_records, indent=2), encoding="utf-8")

    public_network_path = config.publication.output_dir / "network.geojson"
    review_network_path = config.publication.output_dir / "review-map" / "network.geojson"
    original_public_network = json.loads(public_network_path.read_text(encoding="utf-8"))
    original_review_network = json.loads(review_network_path.read_text(encoding="utf-8"))
    finding_id = withheld["finding_id"]
    review_finding = next(
        feature for feature in original_review_network["features"] if feature["id"] == finding_id
    )
    for public_mutation, review_mutation, message in (
        (
            lambda payload: next(
                feature for feature in payload["features"] if feature["id"] == finding_id
            ).update({"geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}),
            lambda payload: None,
            "public GeoJSON Route Refinement Finding must have non-empty Point or MultiPoint",
        ),
        (
            lambda payload: None,
            lambda payload: payload.update(
                {
                    "features": [
                        feature for feature in payload["features"] if feature["id"] != finding_id
                    ]
                }
            ),
            "exactly one review-map GeoJSON Route Refinement Finding",
        ),
        (
            lambda payload: None,
            lambda payload: payload["features"].append(deepcopy(review_finding)),
            "exactly one review-map GeoJSON Route Refinement Finding",
        ),
        (
            lambda payload: None,
            lambda payload: next(
                feature for feature in payload["features"] if feature["id"] == finding_id
            ).update({"id": "misnamed-finding"}),
            "exactly one review-map GeoJSON Route Refinement Finding",
        ),
        (
            lambda payload: None,
            lambda payload: next(
                feature for feature in payload["features"] if feature["id"] == finding_id
            ).update({"geometry": {"type": "MultiPoint", "coordinates": [[999, 999]]}}),
            "geometry differs from public GeoJSON",
        ),
    ):
        tampered_public = deepcopy(original_public_network)
        tampered_review = deepcopy(original_review_network)
        public_mutation(tampered_public)
        review_mutation(tampered_review)
        public_network_path.write_text(json.dumps(tampered_public, indent=2), encoding="utf-8")
        review_network_path.write_text(json.dumps(tampered_review, indent=2), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            validate_publication(config.publication.output_dir, config)
    public_network_path.write_text(json.dumps(original_public_network, indent=2), encoding="utf-8")
    review_network_path.write_text(json.dumps(original_review_network, indent=2), encoding="utf-8")


def test_urban_portals_do_not_create_internal_peer_to_peer_routes() -> None:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "west",
                "name": "West",
                "kind": "community",
                "place_class": "neighbourhood",
                "parent_place_id": None,
                "geometry": Point(-0.05, 0),
            },
            {
                "place_id": "west-outer",
                "name": "West outer",
                "kind": "community_portal",
                "place_class": "neighbourhood",
                "parent_place_id": "west",
                "geometry": Point(-0.04, 0),
            },
            {
                "place_id": "west-inner",
                "name": "West inner",
                "kind": "community_portal",
                "place_class": "neighbourhood",
                "parent_place_id": "west",
                "geometry": Point(0, 0),
            },
            {
                "place_id": "east",
                "name": "East",
                "kind": "community",
                "place_class": "neighbourhood",
                "parent_place_id": None,
                "geometry": Point(0.15, 0),
            },
            {
                "place_id": "east-inner",
                "name": "East inner",
                "kind": "community_portal",
                "place_class": "neighbourhood",
                "parent_place_id": "east",
                "geometry": Point(0.1, 0),
            },
            {
                "place_id": "east-outer",
                "name": "East outer",
                "kind": "community_portal",
                "place_class": "neighbourhood",
                "parent_place_id": "east",
                "geometry": Point(0.14, 0),
            },
        ],
        crs=4326,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "urban-link",
                "highway": "residential",
                "geometry": LineString([(0, 0), (0.1, 0)]),
            }
        ],
        crs=4326,
    )
    config = CouncilConfig.from_yaml(
        Path(__file__).parents[1] / "examples" / "fixture" / "council.yaml"
    )

    compiled = compile_network(
        config,
        {"places": places, "network": network, "boundary": gpd.GeoDataFrame()},
        FakeAgentRuntime(),
    )

    assert compiled.spine_access_connections.empty
    assert compiled.urban_spines.empty
    assert compiled.urban_classification_unknowns.empty
    assert compiled.urban_classification_status == "explicit-unknown"
    assert compiled.criteria["urban_network"]["official_road_classification"] == "grey"
    assert compiled.criteria["network"]["legacy_pairwise_absent"] == "green"
