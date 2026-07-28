# ruff: noqa: E501 -- the exact static provenance contract is intentionally readable as JSON.

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from geopandas.testing import assert_geodataframe_equal
from shapely.geometry import LineString, Point, Polygon

import satn.backbone as backbone_module
import satn.compiler as compiler_module
from satn.agents import AgentRole, FakeAgentRuntime
from satn.compiler import CompiledNetwork, compile_network
from satn.heartbeat import StageHeartbeat
from satn.models import CouncilConfig, TrafficLight
from satn.publisher import _write_json_records, publish
from satn.routing import RoadGraph, RouteOption

PROJECT = Path(__file__).parents[1]
PARALLEL_SPINE_PRE_INSTRUMENTATION_SEMANTIC_SNAPSHOT = {
    "access": [
        {
            "obligation_id": "access-obligation-ea07bd160461", "obligation_kind": "community", "place_id": "hinterland", "place_name": "Hinterland", "root": "root:A1", "branch": "branch:A1",
            "parent": {"role": "spine-access-connection", "place_id": "left-near", "branch": "branch:A1", "target": "access:left-near"},
            "attachment_depth": 2, "network_role": "spine-access-connection", "status": "validated",
            "source_ids": ["hinterland-feed", "strategic-spine-evidence-43b76ca0b781", "strategic-spine-sources-7023563c3d64"],
            "provenance": {"access_connection_id": "access:hinterland", "branch_id": "branch:A1", "obligation_kind": "community", "parent_access_connection_id": "access:left-near", "parent_branch_id": "branch:A1", "parent_place_id": "left-near", "parent_role": "spine-access-connection", "parent_target_id": "access:left-near", "parent_target_name": "Left Near", "place_id": "hinterland", "root_evidence_id": "strategic-spine-evidence-43b76ca0b781", "root_source_id": "strategic-spine-sources-7023563c3d64", "root_spine_id": "root:A1", "source_ids": ["hinterland-feed", "strategic-spine-evidence-43b76ca0b781", "strategic-spine-sources-7023563c3d64"]},
        },
        {
            "obligation_id": "access-obligation-c3fd462181b2", "obligation_kind": "community", "place_id": "left-near", "place_name": "Left Near", "root": "root:A1", "branch": "branch:A1",
            "parent": {"role": "strategic-spine", "place_id": None, "branch": None, "target": "root:A1"},
            "attachment_depth": 1, "network_role": "spine-access-connection", "status": "validated",
            "source_ids": ["left-feed", "strategic-spine-evidence-43b76ca0b781", "strategic-spine-sources-7023563c3d64"],
            "provenance": {"access_connection_id": "access:left-near", "branch_id": "branch:A1", "obligation_kind": "community", "parent_access_connection_id": None, "parent_branch_id": None, "parent_place_id": None, "parent_role": "strategic-spine", "parent_target_id": "root:A1", "parent_target_name": "A1", "place_id": "left-near", "root_evidence_id": "strategic-spine-evidence-43b76ca0b781", "root_source_id": "strategic-spine-sources-7023563c3d64", "root_spine_id": "root:A1", "source_ids": ["left-feed", "strategic-spine-evidence-43b76ca0b781", "strategic-spine-sources-7023563c3d64"]},
        },
        {
            "obligation_id": "access-obligation-dbd66a5d950a", "obligation_kind": "community", "place_id": "right-near", "place_name": "Right Near", "root": "root:A2", "branch": "branch:A2",
            "parent": {"role": "strategic-spine", "place_id": None, "branch": None, "target": "root:A2"},
            "attachment_depth": 1, "network_role": "spine-access-connection", "status": "validated",
            "source_ids": ["right-feed", "strategic-spine-evidence-cfdf45c2a36c", "strategic-spine-sources-e03825e2f853"],
            "provenance": {"access_connection_id": "access:right-near", "branch_id": "branch:A2", "obligation_kind": "community", "parent_access_connection_id": None, "parent_branch_id": None, "parent_place_id": None, "parent_role": "strategic-spine", "parent_target_id": "root:A2", "parent_target_name": "A2", "place_id": "right-near", "root_evidence_id": "strategic-spine-evidence-cfdf45c2a36c", "root_source_id": "strategic-spine-sources-e03825e2f853", "root_spine_id": "root:A2", "source_ids": ["right-feed", "strategic-spine-evidence-cfdf45c2a36c", "strategic-spine-sources-e03825e2f853"]},
        },
    ],
    "meetings": [
        {
            "endpoints": [{"place": "hinterland", "place_name": "Hinterland", "branch": "branch:A1", "root": "root:A1"}, {"place": "right-near", "place_name": "Right Near", "branch": "branch:A2", "root": "root:A2"}],
            "network_role": "branch-meeting-connection", "status": "validated", "source_ids": ["middle-feed"],
            "provenance": {"meeting_connection_id": "meeting:A1|A2:hinterland|right-near", "source_ids": ["middle-feed"], "endpoints": [{"branch": "branch:A1", "place": "hinterland", "root": "root:A1"}, {"branch": "branch:A2", "place": "right-near", "root": "root:A2"}]},
        }
    ],
    "connectors": [
        {
            "roots": ["root:A1", "root:A2"], "meeting": "meeting:A1|A2:hinterland|right-near", "branches": ["branch:A1", "branch:A2"],
            "connections": ["access:hinterland", "access:left-near", "access:right-near", "meeting:A1|A2:hinterland|right-near"],
            "communities": ["hinterland", "left-near", "right-near"], "network_role": "cross-spine-connector", "status": "validated",
            "source_ids": ["hinterland-feed", "left-feed", "middle-feed", "right-feed", "strategic-spine-evidence-43b76ca0b781", "strategic-spine-evidence-cfdf45c2a36c", "strategic-spine-sources-7023563c3d64", "strategic-spine-sources-e03825e2f853"],
            "provenance": {"branch_ids": ["branch:A1", "branch:A2"], "community_ids": ["hinterland", "left-near", "right-near"], "connection_ids": ["access:hinterland", "access:left-near", "access:right-near", "meeting:A1|A2:hinterland|right-near"], "cross_spine_connector_id": "connector:A1|A2", "meeting_connection_id": "meeting:A1|A2:hinterland|right-near", "named_root_traversal": {"noded_segment_count": 1, "pruned_segment_count": 0, "selected_segment_count": 1, "root_distances_m": [("root:A1", 0.0), ("root:A2", 0.0)]}, "source_ids": ["hinterland-feed", "left-feed", "middle-feed", "right-feed", "strategic-spine-evidence-43b76ca0b781", "strategic-spine-evidence-cfdf45c2a36c", "strategic-spine-sources-7023563c3d64", "strategic-spine-sources-e03825e2f853"]},
        }
    ],
}


def config() -> CouncilConfig:
    council = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    council.compilation.agent.response_mode = "direct-runtime"
    return council


def frame(rows: list[dict[str, object]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)


def parallel_spine_source(*, reverse: bool = False) -> dict[str, gpd.GeoDataFrame]:
    places = [
        {
            "place_id": "left-near",
            "name": "Left Near",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(0.02, 0),
        },
        {
            "place_id": "hinterland",
            "name": "Hinterland",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(0.04, 0),
        },
        {
            "place_id": "right-near",
            "name": "Right Near",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(0.08, 0),
        },
    ]
    network = [
        {
            "osmid": "left-spine-edge",
            "highway": "primary",
            "ref": "A1",
            "geometry": LineString([(0, 0), (0, 0.01)]),
        },
        {
            "osmid": "left-feed",
            "highway": "unclassified",
            "geometry": LineString([(0, 0), (0.02, 0)]),
        },
        {
            "osmid": "hinterland-feed",
            "highway": "unclassified",
            "geometry": LineString([(0.02, 0), (0.04, 0)]),
        },
        {
            "osmid": "middle-feed",
            "highway": "unclassified",
            "geometry": LineString([(0.04, 0), (0.08, 0)]),
        },
        {
            "osmid": "right-feed",
            "highway": "unclassified",
            "geometry": LineString([(0.08, 0), (0.1, 0)]),
        },
        {
            "osmid": "right-spine-edge",
            "highway": "primary",
            "ref": "A2",
            "geometry": LineString([(0.1, 0), (0.1, 0.01)]),
        },
    ]
    context = [
        {
            "evidence_id": "left-a1",
            "feature_type": "a-road-spine",
            "name": "A1",
            "category": "A-road strategic spine",
            "source_id": "left-spine-edge",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": LineString([(0, 0), (0, 0.01)]),
        },
        {
            "evidence_id": "right-a2",
            "feature_type": "a-road-spine",
            "name": "A2",
            "category": "A-road strategic spine",
            "source_id": "right-spine-edge",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": LineString([(0.1, 0), (0.1, 0.01)]),
        },
    ]
    if reverse:
        places.reverse()
        network.reverse()
        context.reverse()
    return {
        "places": frame(places),
        "network": frame(network),
        "context": frame(context),
        "boundary": gpd.GeoDataFrame(geometry=[], crs=4326),
    }


def three_spine_source() -> dict[str, gpd.GeoDataFrame]:
    source = parallel_spine_source()
    source["places"] = frame(
        [
            *source["places"].to_dict("records"),
            {
                "place_id": "third-near",
                "name": "Third Near",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(0.18, 0),
            },
        ]
    )
    source["network"] = frame(
        [
            *source["network"].to_dict("records"),
            {
                "osmid": "right-to-third",
                "highway": "unclassified",
                "geometry": LineString([(0.1, 0), (0.18, 0)]),
            },
            {
                "osmid": "third-feed",
                "highway": "unclassified",
                "geometry": LineString([(0.18, 0), (0.2, 0)]),
            },
            {
                "osmid": "third-spine-edge",
                "highway": "primary",
                "ref": "A3",
                "geometry": LineString([(0.2, 0), (0.2, 0.01)]),
            },
        ]
    )
    source["context"] = frame(
        [
            *source["context"].to_dict("records"),
            {
                "evidence_id": "third-a3",
                "feature_type": "a-road-spine",
                "name": "A3",
                "category": "A-road strategic spine",
                "source_id": "third-spine-edge",
                "feature_count": 1,
                "network_scope": "rural",
                "geometry": LineString([(0.2, 0), (0.2, 0.01)]),
            },
        ]
    )
    return source


def four_spine_source(*, reverse: bool = False) -> dict[str, gpd.GeoDataFrame]:
    """Four roots create a real cycle-selection case beyond the simple triangle."""
    source = three_spine_source()
    source["places"] = frame(
        [
            *source["places"].to_dict("records"),
            {
                "place_id": "fourth-near",
                "name": "Fourth Near",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(0.28, 0),
            },
        ]
    )
    source["network"] = frame(
        [
            *source["network"].to_dict("records"),
            {
                "osmid": "third-to-fourth",
                "highway": "unclassified",
                "geometry": LineString([(0.2, 0), (0.28, 0)]),
            },
            {
                "osmid": "fourth-feed",
                "highway": "unclassified",
                "geometry": LineString([(0.28, 0), (0.3, 0)]),
            },
            {
                "osmid": "fourth-spine-edge",
                "highway": "primary",
                "ref": "A4",
                "geometry": LineString([(0.3, 0), (0.3, 0.01)]),
            },
        ]
    )
    source["context"] = frame(
        [
            *source["context"].to_dict("records"),
            {
                "evidence_id": "fourth-a4",
                "feature_type": "a-road-spine",
                "name": "A4",
                "category": "A-road strategic spine",
                "source_id": "fourth-spine-edge",
                "feature_count": 1,
                "network_scope": "rural",
                "geometry": LineString([(0.3, 0), (0.3, 0.01)]),
            },
        ]
    )
    if reverse:
        for name in ("places", "network", "context"):
            source[name] = frame(list(reversed(source[name].to_dict("records"))))
    return source


def topology(compiled: object) -> list[tuple[object, ...]]:
    return sorted(
        (
            row.access_connection_id,
            row.place_id,
            row.root_spine_id,
            row.branch_id,
            row.parent_role,
            row.parent_place_id,
            row.parent_access_connection_id,
            row.geometry.wkb_hex,
        )
        for row in compiled.spine_access_connections.itertuples()
    )


def cross_spine_topology(compiled: object) -> list[tuple[object, ...]]:
    return sorted(
        (
            row.meeting_connection_id,
            row.from_place_id,
            row.to_place_id,
            row.from_root_spine_id,
            row.to_root_spine_id,
            row.geometry.wkb_hex,
        )
        for row in compiled.branch_meeting_connections.itertuples()
    )


def backbone_snapshot(compiled: object) -> dict[str, object]:
    """Capture authoritative, externally visible assembly results without timestamps."""
    return {
        "access": topology(compiled),
        "meetings": cross_spine_topology(compiled),
        "connectors": sorted(
            (
                row.cross_spine_connector_id,
                row.meeting_connection_id,
                row.geometry.wkb_hex,
            )
            for row in compiled.cross_spine_connectors.itertuples()
        ),
        "agent_records": [
            record.model_dump(mode="json", exclude={"created_at"})
            for record in compiled.agent_records
        ],
    }


def _fixture_json(value: object) -> object:
    """Decode fixture JSON columns while retaining scalar values unchanged."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _fixture_optional(value: object) -> object:
    return None if value != value else value


def governed_semantic_snapshot(compiled: object) -> dict[str, list[dict[str, object]]]:
    """Capture only the fixture contract that is stable across GEOS runtimes.

    Generated IDs ultimately derive from geometry bytes, so a static golden
    cannot use them as portable identity.  The strict same-runtime comparison
    below still protects every emitted column, ID, provenance value and WKB
    against observer-induced changes.  This fixture golden instead maps those
    IDs to logical fixture identities and pins the source-stable semantics.
    """
    access = compiled.spine_access_connections
    meetings = compiled.branch_meeting_connections
    connectors = compiled.cross_spine_connectors

    provenance_by_access_id = {
        row.access_connection_id: _fixture_json(row.provenance) for row in access.itertuples()
    }
    root_names = {
        row.root_spine_id: provenance_by_access_id[row.access_connection_id]["parent_target_name"]
        for row in access.itertuples()
        if row.parent_role == "strategic-spine"
    }
    logical_ids: dict[object, str] = {}
    for row in access.itertuples():
        root_name = root_names[row.root_spine_id]
        logical_ids[row.root_spine_id] = f"root:{root_name}"
        logical_ids[row.branch_id] = f"branch:{root_name}"
        logical_ids[row.access_connection_id] = f"access:{row.place_id}"
    for row in meetings.itertuples():
        root_names_for_meeting = sorted(
            (root_names[row.from_root_spine_id], root_names[row.to_root_spine_id])
        )
        places_for_meeting = sorted((row.from_place_id, row.to_place_id))
        logical_ids[row.meeting_connection_id] = (
            f"meeting:{'|'.join(root_names_for_meeting)}:{'|'.join(places_for_meeting)}"
        )
    for row in connectors.itertuples():
        root_names_for_connector = sorted(
            (root_names[row.from_root_spine_id], root_names[row.to_root_spine_id])
        )
        logical_ids[row.cross_spine_connector_id] = (
            f"connector:{'|'.join(root_names_for_connector)}"
        )

    def normalize(value: object) -> object:
        value = _fixture_json(value)
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            return sorted((normalize(item) for item in value), key=lambda item: repr(item))
        return logical_ids.get(value, value)

    def normalized_sources(value: object) -> list[object]:
        sources = normalize(value)
        assert isinstance(sources, list)
        return sources

    def normalized_meeting_provenance(row: object) -> dict[str, object]:
        provenance = normalize(row.provenance)
        assert isinstance(provenance, dict)
        endpoints = sorted(
            (
                {
                    "branch": provenance.pop("from_branch_id"),
                    "place": provenance.pop("from_place_id"),
                    "root": provenance.pop("from_root_spine_id"),
                },
                {
                    "branch": provenance.pop("to_branch_id"),
                    "place": provenance.pop("to_place_id"),
                    "root": provenance.pop("to_root_spine_id"),
                },
            ),
            key=lambda endpoint: repr(endpoint),
        )
        provenance["endpoints"] = endpoints
        return provenance

    def normalized_connector_provenance(row: object) -> dict[str, object]:
        provenance = normalize(row.provenance)
        assert isinstance(provenance, dict)
        traversal = provenance["named_root_traversal"]
        assert isinstance(traversal, dict)
        root_distances = sorted(
            (
                (
                    traversal.pop("from_root_spine_id"),
                    traversal.pop("from_root_distance_m"),
                ),
                (
                    traversal.pop("to_root_spine_id"),
                    traversal.pop("to_root_distance_m"),
                ),
            )
        )
        traversal["root_distances_m"] = root_distances
        return provenance

    return {
        "access": sorted(
            [
                {
                    "obligation_id": row.obligation_id,
                    "obligation_kind": row.obligation_kind,
                    "place_id": row.place_id,
                    "place_name": row.place_name,
                    "root": logical_ids[row.root_spine_id],
                    "branch": logical_ids[row.branch_id],
                    "parent": {
                        "role": row.parent_role,
                        "place_id": _fixture_optional(row.parent_place_id),
                        "branch": normalize(_fixture_optional(row.parent_branch_id)),
                        "target": normalize(row.parent_target_id),
                    },
                    "attachment_depth": row.attachment_depth,
                    "network_role": row.network_role,
                    "status": row.status,
                    "source_ids": normalized_sources(row.source_ids),
                    "provenance": normalize(row.provenance),
                }
                for row in access.itertuples()
            ],
            key=lambda item: str(item["place_id"]),
        ),
        "meetings": sorted(
            [
                {
                    "endpoints": sorted(
                        (
                            {
                                "place": row.from_place_id,
                                "place_name": row.from_place_name,
                                "branch": logical_ids[row.from_branch_id],
                                "root": logical_ids[row.from_root_spine_id],
                            },
                            {
                                "place": row.to_place_id,
                                "place_name": row.to_place_name,
                                "branch": logical_ids[row.to_branch_id],
                                "root": logical_ids[row.to_root_spine_id],
                            },
                        ),
                        key=lambda endpoint: repr(endpoint),
                    ),
                    "network_role": row.network_role,
                    "status": row.status,
                    "source_ids": normalized_sources(row.source_ids),
                    "provenance": normalized_meeting_provenance(row),
                }
                for row in meetings.itertuples()
            ],
            key=lambda item: repr(item),
        ),
        "connectors": sorted(
            [
                {
                    "roots": sorted(
                        (logical_ids[row.from_root_spine_id], logical_ids[row.to_root_spine_id])
                    ),
                    "meeting": logical_ids[row.meeting_connection_id],
                    "branches": normalize(row.branch_ids),
                    "connections": normalize(row.connection_ids),
                    "communities": normalize(row.community_ids),
                    "network_role": row.network_role,
                    "status": row.status,
                    "source_ids": normalized_sources(row.source_ids),
                    "provenance": normalized_connector_provenance(row),
                }
                for row in connectors.itertuples()
            ],
            key=lambda item: repr(item),
        ),
    }


def governed_identity_snapshot(compiled: object) -> dict[str, list[dict[str, object]]]:
    """Capture the exact portable identifier and provenance contract."""

    return {
        "strategic_spines": sorted(
            [
                {
                    "spine_id": row.spine_id,
                    "evidence_id": row.evidence_id,
                    "source_id": row.source_id,
                    "provenance": json.loads(row.provenance),
                }
                for row in compiled.strategic_spines.itertuples()
            ],
            key=lambda item: str(item["spine_id"]),
        ),
        "access_connections": sorted(
            [
                {
                    "access_connection_id": row.access_connection_id,
                    "root_spine_id": row.root_spine_id,
                    "branch_id": row.branch_id,
                    "parent_branch_id": _fixture_optional(row.parent_branch_id),
                    "parent_access_connection_id": _fixture_optional(
                        row.parent_access_connection_id
                    ),
                    "provenance": json.loads(row.provenance),
                }
                for row in compiled.spine_access_connections.itertuples()
            ],
            key=lambda item: str(item["access_connection_id"]),
        ),
        "branch_meetings": sorted(
            [
                {
                    "meeting_connection_id": row.meeting_connection_id,
                    "from_place_id": row.from_place_id,
                    "to_place_id": row.to_place_id,
                    "from_branch_id": row.from_branch_id,
                    "to_branch_id": row.to_branch_id,
                    "from_root_spine_id": row.from_root_spine_id,
                    "to_root_spine_id": row.to_root_spine_id,
                    "provenance": json.loads(row.provenance),
                }
                for row in compiled.branch_meeting_connections.itertuples()
            ],
            key=lambda item: str(item["meeting_connection_id"]),
        ),
        "cross_spine_connectors": sorted(
            [
                {
                    "cross_spine_connector_id": row.cross_spine_connector_id,
                    "meeting_connection_id": row.meeting_connection_id,
                    "from_root_spine_id": row.from_root_spine_id,
                    "to_root_spine_id": row.to_root_spine_id,
                    "branch_ids": json.loads(row.branch_ids),
                    "connection_ids": json.loads(row.connection_ids),
                    "provenance": json.loads(row.provenance),
                }
                for row in compiled.cross_spine_connectors.itertuples()
            ],
            key=lambda item: str(item["cross_spine_connector_id"]),
        ),
    }


def test_cross_platform_identifier_and_provenance_fixture_is_exact() -> None:
    expected = json.loads(
        (PROJECT / "tests" / "fixtures" / "cross-platform-identifiers.json").read_text()
    )
    first = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    repeated = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    reordered = compile_network(
        config(),
        parallel_spine_source(reverse=True),
        FakeAgentRuntime(),
    )

    assert governed_identity_snapshot(first) == expected
    assert governed_identity_snapshot(repeated) == expected
    assert governed_identity_snapshot(reordered) == expected


def test_meeting_identity_and_provenance_ignore_candidate_traversal_orientation() -> None:
    root_a = pd.Series(
        {
            "root_spine_id": "root-a",
            "branch_id": "branch-a",
            "place_id": "place-a",
            "place_name": "Place A",
        }
    )
    root_b = pd.Series(
        {
            "root_spine_id": "root-b",
            "branch_id": "branch-b",
            "place_id": "place-b",
            "place_name": "Place B",
        }
    )

    def candidate(
        left: pd.Series,
        right: pd.Series,
        coordinates: list[tuple[float, float]],
        start_node: str,
        end_node: str,
    ) -> object:
        option = RouteOption(
            role="direct",
            geometry=LineString(coordinates),
            length_km=1.0,
            edge_ids=["middle-edge"],
            a_road_share=0.0,
            ncn_share=0.0,
            bidirectional=True,
            reverse_length_km=1.0,
            reverse_edge_ids=["middle-edge"],
            reverse_corridor_share=1.0,
            impracticable_alongside=False,
        )
        return backbone_module._MeetingCandidate(
            rank=(),
            left=left,
            right=right,
            option=option,
            options=(option,),
            topography=None,
            start_node=start_node,
            end_node=end_node,
            excluded_pairs=frozenset(),
        )

    from_b = backbone_module._meeting_row(
        candidate(root_b, root_a, [(1, 0), (0, 0)], "node-b", "node-a"),
        max_connection_km=5.0,
    )
    from_a = backbone_module._meeting_row(
        candidate(root_a, root_b, [(0, 0), (1, 0)], "node-a", "node-b"),
        max_connection_km=5.0,
    )

    for field in (
        "meeting_connection_id",
        "from_place_id",
        "to_place_id",
        "from_branch_id",
        "to_branch_id",
        "from_root_spine_id",
        "to_root_spine_id",
        "from_attachment_node",
        "to_attachment_node",
        "provenance",
    ):
        assert from_b[field] == from_a[field]
    assert from_b["geometry"].wkb_hex == from_a["geometry"].wkb_hex


def test_strategic_spine_identifier_collisions_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        compiler_module,
        "_stable_role_id",
        lambda prefix, *_parts: f"{prefix}-collision",
    )

    with pytest.raises(ValueError, match="canonical identifier collision"):
        compiler_module._strategic_spines(parallel_spine_source()["context"])


def test_strategic_spines_ignore_only_precision_collapsed_union_fragments() -> None:
    context = parallel_spine_source()["context"].copy()
    template = context.loc[context["feature_type"] == "a-road-spine"].iloc[0].copy()
    valid = template.copy()
    valid.update(
        {
            "feature_type": "ncn-route",
            "name": "NCN 24.0",
            "evidence_id": "ncn-24-valid",
            "source_id": "ncn-24",
            "geometry": LineString([(-2.382, 51.32), (-2.38, 51.32)]),
        }
    )
    collapsed = template.copy()
    collapsed.update(
        {
            "feature_type": "ncn-route",
            "name": "NCN 24.0",
            "evidence_id": "ncn-24-union-sliver",
            "source_id": "ncn-24",
            "geometry": LineString(
                [
                    (-2.381811057610917, 51.32019913258663),
                    (-2.381811057609699, 51.32019913258042),
                ]
            ),
        }
    )
    context = gpd.GeoDataFrame(
        pd.concat(
            [
                context,
                gpd.GeoDataFrame([valid, collapsed], geometry="geometry", crs=context.crs),
            ],
            ignore_index=True,
        ),
        geometry="geometry",
        crs=context.crs,
    )

    spines = compiler_module._strategic_spines(context)

    ncn_24 = spines.loc[spines["name"] == "NCN 24.0"]
    assert len(ncn_24) == 1
    assert ncn_24.iloc[0].geometry.equals(valid["geometry"])


def assert_same_runtime_governed_frame_equal(
    actual: gpd.GeoDataFrame,
    expected: gpd.GeoDataFrame,
) -> None:
    """Compare a governed frame's data and its exact emitted geometry.

    GeoPandas' normal geometry assertion is deliberately topological: it
    accepts equivalent reversed or densified LineStrings.  That is useful for
    many spatial tests but is too weak for this observer-isolation regression.
    Both frames are produced in the same Python/GEOS runtime, so their ordered
    WKB must agree exactly as well as their tabular data.
    """
    assert_geodataframe_equal(actual, expected, check_like=False, check_crs=True)
    assert [geometry.wkb_hex for geometry in actual.geometry] == [
        geometry.wkb_hex for geometry in expected.geometry
    ]


def compile_eager_reference(
    monkeypatch: pytest.MonkeyPatch,
    council: CouncilConfig,
    source: dict[str, gpd.GeoDataFrame],
    runtime: FakeAgentRuntime,
) -> object:
    """Compile through the retained private eager schedule for parity tests."""
    original = backbone_module._cross_spine_meetings

    def eager_reference(*args: object, **kwargs: object) -> object:
        return original(*args, lazy_bounds=False, **kwargs)

    monkeypatch.setattr(backbone_module, "_cross_spine_meetings", eager_reference)
    return compile_network(council, source, runtime)


def assert_lazy_matches_eager_governed_contract(lazy: object, eager: object) -> None:
    """Prove optimisation-only differences cannot leak into governed output."""
    for field in dataclasses.fields(CompiledNetwork):
        field_name = field.name
        lazy_value = getattr(lazy, field_name)
        eager_value = getattr(eager, field_name)
        if isinstance(lazy_value, gpd.GeoDataFrame):
            assert isinstance(eager_value, gpd.GeoDataFrame)
            assert_same_runtime_governed_frame_equal(lazy_value, eager_value)
            continue
        if field_name == "agent_records":
            assert [record.model_dump(mode="json", exclude={"created_at"}) for record in lazy_value] == [
                record.model_dump(mode="json", exclude={"created_at"}) for record in eager_value
            ]
            continue
        if field_name == "compilation_diagnostics":
            assert {
                key: value for key, value in lazy_value.items() if key != "cross_spine"
            } == {
                key: value for key, value in eager_value.items() if key != "cross_spine"
            }
            lazy_diagnostics = lazy_value["cross_spine"]
            eager_diagnostics = eager_value["cross_spine"]
            assert isinstance(lazy_diagnostics, dict)
            assert isinstance(eager_diagnostics, dict)
            optimisation_keys = {
                "root_pair_route_searches",
                "root_pair_route_searches_avoided",
                "root_pair_candidate_bounds_enqueued",
                "root_pair_candidate_bounds_skipped_as_connected",
                "root_pair_candidate_bounds_skipped_as_unroutable",
                "root_group_distance_planning_searches",
                "root_group_distance_planning_nodes_settled",
                "root_pair_exact_distance_bounds",
            }
            assert {
                key: value for key, value in lazy_diagnostics.items() if key not in optimisation_keys
            } == {
                key: value for key, value in eager_diagnostics.items() if key not in optimisation_keys
            }
            continue
        assert lazy_value == eager_value


@pytest.mark.parametrize(
    "equivalent_geometry",
    [
        LineString([(2, 0), (0, 0)]),
        LineString([(0, 0), (1, 0), (2, 0)]),
    ],
    ids=["reversed", "densified"],
)
def test_same_runtime_governed_frame_comparison_rejects_topologically_equal_geometry(
    equivalent_geometry: LineString,
) -> None:
    expected = gpd.GeoDataFrame(
        {"connection_id": ["example"], "geometry": [LineString([(0, 0), (2, 0)])]},
        crs="EPSG:4326",
    )
    changed = expected.copy()
    changed.loc[0, "geometry"] = equivalent_geometry

    # Establish the intended contrast with GeoPandas' topology-aware default.
    assert_geodataframe_equal(changed, expected, check_like=False, check_crs=True)
    with pytest.raises(AssertionError):
        assert_same_runtime_governed_frame_equal(changed, expected)


def test_semantic_fixture_golden_normalizes_generated_ids_and_meeting_orientation() -> None:
    """The portable golden rejects semantic drift but not GEOS-derived identities."""
    compiled = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    rewritten = SimpleNamespace(
        spine_access_connections=compiled.spine_access_connections.copy(deep=True),
        branch_meeting_connections=compiled.branch_meeting_connections.copy(deep=True),
        cross_spine_connectors=compiled.cross_spine_connectors.copy(deep=True),
    )
    ids: dict[str, str] = {}
    for index, row in enumerate(compiled.spine_access_connections.itertuples()):
        provenance = json.loads(row.provenance)
        ids[row.access_connection_id] = f"generated-access-{index}"
        ids[row.root_spine_id] = f"generated-root-{index}"
        ids[row.branch_id] = f"generated-branch-{index}"
    for index, row in enumerate(compiled.branch_meeting_connections.itertuples()):
        ids[row.meeting_connection_id] = f"generated-meeting-{index}"
    for index, row in enumerate(compiled.cross_spine_connectors.itertuples()):
        ids[row.cross_spine_connector_id] = f"generated-connector-{index}"

    def replace_generated_ids(value: object) -> object:
        if isinstance(value, dict):
            return {key: replace_generated_ids(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace_generated_ids(item) for item in value]
        return ids.get(value, value)

    def rewrite_cell(value: object) -> object:
        if not isinstance(value, str):
            return ids.get(value, value)
        try:
            return json.dumps(replace_generated_ids(json.loads(value)), sort_keys=True)
        except json.JSONDecodeError:
            return ids.get(value, value)

    for frame in (
        rewritten.spine_access_connections,
        rewritten.branch_meeting_connections,
        rewritten.cross_spine_connectors,
    ):
        for column in frame.columns:
            if column != "geometry":
                frame[column] = frame[column].map(rewrite_cell)

    meetings = rewritten.branch_meeting_connections
    for left, right in (
        ("from_place_id", "to_place_id"),
        ("from_place_name", "to_place_name"),
        ("from_branch_id", "to_branch_id"),
        ("from_root_spine_id", "to_root_spine_id"),
    ):
        meetings[[left, right]] = meetings[[right, left]]
    meeting_provenance = json.loads(meetings.loc[0, "provenance"])
    for left, right in (
        ("from_place_id", "to_place_id"),
        ("from_branch_id", "to_branch_id"),
        ("from_root_spine_id", "to_root_spine_id"),
    ):
        meeting_provenance[left], meeting_provenance[right] = (
            meeting_provenance[right],
            meeting_provenance[left],
        )
    meetings.loc[0, "provenance"] = json.dumps(meeting_provenance, sort_keys=True)

    connectors = rewritten.cross_spine_connectors
    connectors[["from_root_spine_id", "to_root_spine_id"]] = connectors[
        ["to_root_spine_id", "from_root_spine_id"]
    ]
    connector_provenance = json.loads(connectors.loc[0, "provenance"])
    traversal = connector_provenance["named_root_traversal"]
    for left, right in (
        ("from_root_spine_id", "to_root_spine_id"),
        ("from_root_distance_m", "to_root_distance_m"),
    ):
        traversal[left], traversal[right] = traversal[right], traversal[left]
    connectors.loc[0, "provenance"] = json.dumps(connector_provenance, sort_keys=True)

    baseline = governed_semantic_snapshot(compiled)
    assert governed_semantic_snapshot(rewritten) == baseline

    changed_source = SimpleNamespace(
        spine_access_connections=rewritten.spine_access_connections.copy(deep=True),
        branch_meeting_connections=rewritten.branch_meeting_connections.copy(deep=True),
        cross_spine_connectors=rewritten.cross_spine_connectors.copy(deep=True),
    )
    changed_source.spine_access_connections.loc[0, "source_ids"] = json.dumps(["other-feed"])
    assert governed_semantic_snapshot(changed_source) != baseline

    changed_status = SimpleNamespace(
        spine_access_connections=rewritten.spine_access_connections.copy(deep=True),
        branch_meeting_connections=rewritten.branch_meeting_connections.copy(deep=True),
        cross_spine_connectors=rewritten.cross_spine_connectors.copy(deep=True),
    )
    changed_status.spine_access_connections.loc[0, "status"] = "review-required"
    assert governed_semantic_snapshot(changed_status) != baseline

    changed_root_sources = SimpleNamespace(
        spine_access_connections=rewritten.spine_access_connections.copy(deep=True),
        branch_meeting_connections=rewritten.branch_meeting_connections.copy(deep=True),
        cross_spine_connectors=rewritten.cross_spine_connectors.copy(deep=True),
    )
    stable_root_ids = {
        "strategic-spine-evidence-43b76ca0b781": "changed-evidence-A1",
        "strategic-spine-evidence-cfdf45c2a36c": "changed-evidence-A2",
        "strategic-spine-sources-7023563c3d64": "changed-source-A1",
        "strategic-spine-sources-e03825e2f853": "changed-source-A2",
    }

    def rewrite_stable_root_ids(value: object) -> object:
        if isinstance(value, dict):
            return {key: rewrite_stable_root_ids(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite_stable_root_ids(item) for item in value]
        return stable_root_ids.get(value, value)

    for frame in (
        changed_root_sources.spine_access_connections,
        changed_root_sources.branch_meeting_connections,
        changed_root_sources.cross_spine_connectors,
    ):
        for column in frame.columns:
            if column == "geometry":
                continue
            frame[column] = frame[column].map(
                lambda value: json.dumps(rewrite_stable_root_ids(json.loads(value)), sort_keys=True)
                if isinstance(value, str) and value[:1] in "[{"
                else rewrite_stable_root_ids(value)
            )
    assert governed_semantic_snapshot(changed_root_sources) != baseline

    changed_place_relationship = SimpleNamespace(
        spine_access_connections=rewritten.spine_access_connections.copy(deep=True),
        branch_meeting_connections=rewritten.branch_meeting_connections.copy(deep=True),
        cross_spine_connectors=rewritten.cross_spine_connectors.copy(deep=True),
    )
    renamed_place_id = "left-near"
    renamed_place_name = "Left Near Renamed"
    access = changed_place_relationship.spine_access_connections
    access.loc[access["place_id"] == renamed_place_id, "place_name"] = renamed_place_name
    for index, row in access.iterrows():
        provenance = json.loads(row["provenance"])
        if provenance.get("parent_place_id") == renamed_place_id:
            provenance["parent_target_name"] = renamed_place_name
            access.loc[index, "provenance"] = json.dumps(provenance, sort_keys=True)
    meetings = changed_place_relationship.branch_meeting_connections
    meetings.loc[meetings["from_place_id"] == renamed_place_id, "from_place_name"] = renamed_place_name
    meetings.loc[meetings["to_place_id"] == renamed_place_id, "to_place_name"] = renamed_place_name
    assert governed_semantic_snapshot(changed_place_relationship) != baseline


def with_source_costs_below_geometry(
    source: dict[str, gpd.GeoDataFrame],
) -> dict[str, gpd.GeoDataFrame]:
    """Keep an admissible but non-unit cost/geometry ratio on every road edge."""
    network = source["network"].copy()
    network["length"] = 100.0
    return {**source, "network": network}


def test_all_spines_seed_order_independent_growth_and_hinterland_chaining() -> None:
    first = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    reordered = compile_network(config(), parallel_spine_source(reverse=True), FakeAgentRuntime())

    assert topology(first) == topology(reordered)
    assert cross_spine_topology(first) == cross_spine_topology(reordered)
    assert first.compilation_diagnostics["cross_spine"] == reordered.compilation_diagnostics[
        "cross_spine"
    ]
    assert len(first.spine_access_connections) == 3
    assert len(first.access_obligations) == 3
    assert set(first.access_obligations["service_status"]) == {"served"}
    assert set(first.spine_access_connections["root_spine_id"]) == set(
        first.strategic_spines["spine_id"]
    )

    by_place = first.spine_access_connections.set_index("place_id")
    chained = by_place.loc["hinterland"]
    assert chained["parent_role"] == "spine-access-connection"
    assert chained["parent_place_id"] == "left-near"
    assert (
        chained["parent_access_connection_id"] == by_place.loc["left-near", "access_connection_id"]
    )
    assert chained["branch_id"] == by_place.loc["left-near", "branch_id"]
    assert chained["attachment_depth"] == 2

    for row in first.spine_access_connections.itertuples():
        provenance = json.loads(row.provenance)
        assert provenance["root_spine_id"] == row.root_spine_id
        assert provenance["branch_id"] == row.branch_id
        assert provenance["source_ids"]
        assert "cycling-network cost" in row.selection_reason

    assert len(first.spine_access_branches) == 2
    access_ids = set(first.spine_access_connections["access_connection_id"])
    assert access_ids <= {record.connection_id for record in first.agent_records}
    assert first.criteria["spine_network"]["all_access_obligations_resolved"] == "green"
    assert first.criteria["spine_network"]["degree_one_access_valid"] == "green"

    assert len(first.branch_meeting_connections) == 1
    meeting = first.branch_meeting_connections.iloc[0]
    assert {meeting["from_place_id"], meeting["to_place_id"]} == {
        "hinterland",
        "right-near",
    }
    assert {meeting["from_root_spine_id"], meeting["to_root_spine_id"]} == set(
        first.strategic_spines["spine_id"]
    )
    assert meeting["network_role"] == "branch-meeting-connection"
    assert meeting["status"] == "validated"
    assert meeting["intervention_archetype"] == "transverse link between Strategic Spine branches"
    assert "first justified" in meeting["selection_reason"]

    assert len(first.cross_spine_connectors) == 1
    connector = first.cross_spine_connectors.iloc[0]
    assert connector["network_role"] == "cross-spine-connector"
    assert connector["meeting_connection_id"] == meeting["meeting_connection_id"]
    assert {connector["from_root_spine_id"], connector["to_root_spine_id"]} == set(
        first.strategic_spines["spine_id"]
    )
    connector_ids = set(json.loads(connector["connection_ids"]))
    assert meeting["meeting_connection_id"] in connector_ids
    assert set(first.spine_access_connections["access_connection_id"]) <= connector_ids
    assert connector.geometry.covers(meeting.geometry)
    assert first.criteria["spine_network"]["cross_spine_traversal"] == "green"
    assert first.criteria["spine_network"]["parallel_meetings_suppressed"] == "green"


def test_compiler_progress_adapter_updates_real_heartbeat_without_changing_governed_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    council = config()
    council.publication.output_dir = tmp_path / "published"
    # Compile the same fixed fixture without the new instrumentation first.
    # The strict GeoDataFrame comparisons below are deliberately same-runtime:
    # they prove that the observer and heartbeat cannot alter any geometry or
    # connection data without treating GEOS's cross-platform byte encoding as
    # a portable golden.
    control = compiler_module.compile_network(
        council,
        parallel_spine_source(),
        FakeAgentRuntime(),
    )
    heartbeat = StageHeartbeat(
        logging.getLogger("tests.cross-spine-progress"),
        "network-compilation",
        {"area_id": council.area_id},
    )
    ticks = iter((100.0, 101.0, 102.0, 103.0))
    monkeypatch.setattr(
        compiler_module,
        "time",
        SimpleNamespace(perf_counter=lambda: next(ticks)),
    )

    def malicious_benchmark_observer(
        _assessed: int,
        _total: int,
        diagnostics: dict[str, object],
    ) -> None:
        diagnostics["candidate_connectors"] = -1
        typed = diagnostics["typed_refinement_findings"]
        assert isinstance(typed, dict)
        typed["route-refinement-required"] = -1

    compiled = compiler_module.compile_network(
        council,
        parallel_spine_source(),
        FakeAgentRuntime(),
        heartbeat=heartbeat,
        cross_spine_progress=malicious_benchmark_observer,
    )

    # Controlled time makes the compiler -> adapter -> actual StageHeartbeat
    # operation deterministic without persisting a timing baseline.
    assert heartbeat.context_snapshot() == {
        "area_id": council.area_id,
        "cross_spine_connectors_assessed": 1,
        "cross_spine_connectors_total": 1,
        "cross_spine_elapsed_seconds": 3.0,
        "cross_spine_throughput_connectors_per_second": 0.333,
        "cross_spine_estimated_remaining_seconds": 0.0,
        "cross_spine_peak_noded_graph_edges": 1,
    }
    diagnostics = compiled.compilation_diagnostics["cross_spine"]
    assert diagnostics["candidate_connectors"] == 1
    assert diagnostics["typed_refinement_findings"] == {
        "route-refinement-required": 0
    }
    assert (
        governed_semantic_snapshot(control)
        == PARALLEL_SPINE_PRE_INSTRUMENTATION_SEMANTIC_SNAPSHOT
    )
    assert (
        governed_semantic_snapshot(compiled)
        == PARALLEL_SPINE_PRE_INSTRUMENTATION_SEMANTIC_SNAPSHOT
    )
    for frame_name in (
        "spine_access_connections",
        "branch_meeting_connections",
        "cross_spine_connectors",
    ):
        assert_same_runtime_governed_frame_equal(
            getattr(compiled, frame_name),
            getattr(control, frame_name),
        )
    # The run carries only the deterministic diagnostic contract: never the
    # heartbeat's clock, throughput, or ETA fields.
    output = tmp_path / "run-only"
    output.mkdir()
    _write_json_records(output, council, compiled, "run-progress-adapter")
    run = json.loads((output / "run.json").read_text())
    run_diagnostics = run["compilation_diagnostics"]["cross_spine"]
    assert run_diagnostics == diagnostics
    assert not any("second" in key or "throughput" in key for key in run_diagnostics)


def test_reachable_attachment_can_bypass_a_nearer_disconnected_fragment() -> None:
    source = parallel_spine_source()
    source["places"] = frame(
        [
            *source["places"].to_dict("records"),
            {
                "place_id": "near-island",
                "name": "Near Island",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(0.05, 0.001),
            },
        ]
    )
    source["network"] = frame(
        [
            *source["network"].to_dict("records"),
            {
                "osmid": "near-island-fragment",
                "highway": "unclassified",
                "geometry": LineString([(0.05, 0.001), (0.051, 0.001)]),
            },
        ]
    )

    compiled = compile_network(config(), source, FakeAgentRuntime())

    obligation = compiled.access_obligations.set_index("place_id").loc["near-island"]
    assert obligation["service_status"] == "served"
    access = compiled.spine_access_connections.set_index("place_id").loc["near-island"]
    assert access["community_attachment_node"] != "xy:0.0500000:0.0010000"


def test_cross_spine_roles_publish_consistently_to_spatial_and_review_artifacts(
    tmp_path: Path,
) -> None:
    council = config()
    council.publication.output_dir = tmp_path / "output"
    source = parallel_spine_source()
    source["boundary"] = gpd.GeoDataFrame(
        [{"geometry": Polygon([(-0.01, -0.01), (0.11, -0.01), (0.11, 0.02), (-0.01, 0.02)])}],
        geometry="geometry",
        crs=4326,
    )
    compiled = compile_network(council, source, FakeAgentRuntime())

    artifacts = publish(council, compiled, "run-cross-spine")

    layer_names = set(gpd.list_layers(artifacts["geopackage"])["name"])
    assert {"branch_meeting_connections", "cross_spine_connectors"} <= layer_names
    meeting = gpd.read_file(artifacts["geopackage"], layer="branch_meeting_connections")
    connector = gpd.read_file(artifacts["geopackage"], layer="cross_spine_connectors")
    network = json.loads(artifacts["geojson"].read_text())
    feature_by_id = {feature["id"]: feature for feature in network["features"]}
    assert (
        feature_by_id[meeting.iloc[0]["meeting_connection_id"]]["properties"]["network_role"]
        == "branch-meeting-connection"
    )
    assert (
        feature_by_id[connector.iloc[0]["cross_spine_connector_id"]]["properties"][
            "selection_reason"
        ]
        == connector.iloc[0]["selection_reason"]
    )
    connector_id = connector.iloc[0]["cross_spine_connector_id"]
    run = json.loads(artifacts["run"].read_text())
    assert {
        "feature_id": connector_id,
        "network_role": "cross-spine-connector",
    } in run["authoritative_features"]
    agents = json.loads(artifacts["agents"].read_text())
    assert any(
        reference
        == {
            "feature_id": connector_id,
            "network_role": "cross-spine-connector",
        }
        for record in agents["records"]
        for reference in record["derived_features"]
    )
    html = artifacts["review_map"].read_text()
    assert 'id="layer-cross-spine-connectors"' in html
    assert 'id="legend-cross-spine-connectors"' in html


def test_direct_compile_publish_persists_the_canonical_empty_decision_contract(
    tmp_path: Path,
) -> None:
    council = config()
    council.compilation.agent.review_statuses = ()
    council.publication.output_dir = tmp_path / "output"
    source = parallel_spine_source()
    source["boundary"] = gpd.GeoDataFrame(
        [{"geometry": Polygon([(-0.01, -0.01), (0.11, -0.01), (0.11, 0.02), (-0.01, 0.02)])}],
        geometry="geometry",
        crs=4326,
    )
    compiled = compile_network(council, source, FakeAgentRuntime())

    assert compiled.decision_ledger_input == {
        "decision_contract": "agent-decision-menu/v1",
        "responses": [],
    }
    assert compiled.accepted_decisions == []
    artifacts = publish(council, compiled, "run-direct-empty-ledger")
    run = json.loads(artifacts["run"].read_text())
    assert run["decision_ledger_input"] == compiled.decision_ledger_input
    assert run["accepted_decisions"] == []


def test_direct_compile_publish_persists_its_bounded_runtime_audit(tmp_path: Path) -> None:
    council = config()
    council.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    council.compilation.agent.max_requests = 100
    council.compilation.agent.max_tokens = 10_000
    council.compilation.agent.deadline_seconds = 5
    council.publication.output_dir = tmp_path / "output"
    source = parallel_spine_source()
    source["boundary"] = gpd.GeoDataFrame(
        [{"geometry": Polygon([(-0.01, -0.01), (0.11, -0.01), (0.11, 0.02), (-0.01, 0.02)])}],
        geometry="geometry",
        crs=4326,
    )
    compiled = compile_network(council, source, FakeAgentRuntime())

    assert len(compiled.accepted_decisions) > 1
    assert [response["request_id"] for response in compiled.accepted_decisions] == sorted(
        response["request_id"] for response in compiled.accepted_decisions
    )
    artifacts = publish(council, compiled, "run-direct-runtime-audit")
    run = json.loads(artifacts["run"].read_text())
    assert run["decision_ledger_input"] == {
        "decision_contract": "agent-decision-menu/v1",
        "responses": [],
    }
    assert run["accepted_decisions"] == compiled.accepted_decisions


def test_first_meetings_connect_three_roots_without_forming_a_mesh() -> None:
    source = three_spine_source()
    reordered = {name: value.iloc[::-1].reset_index(drop=True) for name, value in source.items()}

    compiled = compile_network(config(), source, FakeAgentRuntime())
    repeated = compile_network(config(), reordered, FakeAgentRuntime())

    roots = set(compiled.spine_access_connections["root_spine_id"])
    assert len(roots) == 3
    assert len(compiled.branch_meeting_connections) == 2
    assert len(compiled.cross_spine_connectors) == 2
    assert cross_spine_topology(compiled) == cross_spine_topology(repeated)
    root_graph = nx.Graph()
    root_graph.add_nodes_from(roots)
    root_graph.add_edges_from(
        compiled.branch_meeting_connections[["from_root_spine_id", "to_root_spine_id"]].itertuples(
            index=False, name=None
        )
    )
    assert nx.is_tree(root_graph)
    diagnostics = compiled.compilation_diagnostics["cross_spine"]
    # All three unordered root pairs were searched; only two were submitted to
    # the meeting agent because the third would form a cycle in the accepted
    # root tree.  Traversal remains a separate later operation.
    assert diagnostics["root_pairs_considered"] == 3
    assert diagnostics["root_pair_candidate_searches"] == 3
    assert diagnostics["meeting_agent_evaluations"] == 2
    assert diagnostics["meeting_agent_evaluation_initial_outcomes"] == {
        "accept": 2,
        "reject": 0,
        "gap": 0,
    }
    assert diagnostics["meeting_agent_evaluation_final_dispositions"] == {
        "accept": 2,
        "reject": 0,
        "gap": 0,
        "superseded": 0,
    }
    assert diagnostics["candidate_connectors"] == 2
    assert diagnostics["connector_traversal_attempts"] == 2


def test_lazy_cross_spine_bounds_preserve_eager_governed_output_and_avoid_route_searches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cycle suppression must happen before unnecessary exact root-pair routing.

    The private eager mode is a reference implementation for this regression.
    It proves that the performance optimisation leaves every same-runtime
    governed frame, exact geometry, provenance-bearing agent record and legacy
    logical diagnostic unchanged while avoiding the discarded cycle's route
    search.
    """
    source = three_spine_source()
    lazy = compile_network(config(), source, FakeAgentRuntime())

    original = backbone_module._cross_spine_meetings

    def eager_reference(*args: object, **kwargs: object) -> object:
        return original(*args, lazy_bounds=False, **kwargs)

    monkeypatch.setattr(backbone_module, "_cross_spine_meetings", eager_reference)
    eager = compile_network(config(), source, FakeAgentRuntime())

    assert backbone_snapshot(lazy) == backbone_snapshot(eager)
    for frame_name in (
        "spine_access_connections",
        "access_obligations",
        "branch_meeting_connections",
        "cross_spine_connectors",
        "gaps",
    ):
        assert_same_runtime_governed_frame_equal(
            getattr(lazy, frame_name),
            getattr(eager, frame_name),
        )

    lazy_diagnostics = lazy.compilation_diagnostics["cross_spine"]
    eager_diagnostics = eager.compilation_diagnostics["cross_spine"]
    for key in (
        "root_pairs_considered",
        "root_pair_candidate_searches",
        "meeting_agent_evaluations",
        "meeting_agent_evaluation_initial_outcomes",
        "meeting_agent_evaluation_final_dispositions",
    ):
        assert lazy_diagnostics[key] == eager_diagnostics[key]
    assert lazy_diagnostics["root_pair_route_searches"] == 2
    assert eager_diagnostics["root_pair_route_searches"] == 3
    assert lazy_diagnostics["root_pair_route_searches_avoided"] == 1
    assert eager_diagnostics["root_pair_route_searches_avoided"] == 0
    assert lazy_diagnostics["root_pair_candidate_bounds_enqueued"] == 3
    assert lazy_diagnostics["root_pair_candidate_bounds_skipped_as_connected"] == 1
    assert lazy_diagnostics["root_pair_candidate_bounds_skipped_as_unroutable"] == 0
    assert lazy_diagnostics["root_group_distance_planning_searches"] == 2
    assert lazy_diagnostics["root_pair_exact_distance_bounds"] == 3


def test_discarded_unmaterializable_cross_spine_path_does_not_change_governed_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eager-only cyclic work cannot leak into legacy graph-quality evidence.

    The injected route cannot be materialised only for the A1--A3 root pair.
    A1 and A3 become connected through accepted A1--A2 and A2--A3 meetings,
    so this direct candidate is redundant.  The eager reference still reaches
    it while the lazy schedule correctly avoids it; both must publish the
    same final Backbone-and-Access diagnostics and authoritative output.
    """
    original_option_from_nodes = RoadGraph._option_from_nodes
    forced_attempts = {"count": 0}

    def reject_redundant_a1_a3_path(
        graph: RoadGraph,
        nodes: list[str],
        role: str,
    ) -> RouteOption | None:
        longitudes = [round(graph.node_points[node].x, 3) for node in nodes]
        if role == "direct" and min(longitudes) <= 0.04 and max(longitudes) >= 0.18:
            forced_attempts["count"] += 1
            return None
        return original_option_from_nodes(graph, nodes, role)

    monkeypatch.setattr(RoadGraph, "_option_from_nodes", reject_redundant_a1_a3_path)
    source = three_spine_source()
    lazy = compile_network(config(), source, FakeAgentRuntime())
    assert forced_attempts["count"] == 0

    eager = compile_eager_reference(monkeypatch, config(), source, FakeAgentRuntime())
    assert forced_attempts["count"] == 1

    assert_lazy_matches_eager_governed_contract(lazy, eager)
    for compiled in (lazy, eager):
        diagnostics = compiled.compilation_diagnostics
        assert diagnostics["unmaterializable_attachment_paths"] == 0
        assert not any(
            finding["finding_id"] == "unmaterializable-osm-attachment-paths"
            for finding in diagnostics["optimization_findings"]
        )
    assert (
        lazy.compilation_diagnostics["cross_spine"]["root_pair_route_searches"]
        < eager.compilation_diagnostics["cross_spine"]["root_pair_route_searches"]
    )


def test_finally_disconnected_unmaterializable_cross_spine_path_remains_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed pair that remains disconnected is governed source-quality evidence."""
    original_option_from_nodes = RoadGraph._option_from_nodes

    def reject_only_a1_a2_path(
        graph: RoadGraph,
        nodes: list[str],
        role: str,
    ) -> RouteOption | None:
        longitudes = [round(graph.node_points[node].x, 3) for node in nodes]
        if role == "direct" and min(longitudes) <= 0.04 and max(longitudes) >= 0.08:
            return None
        return original_option_from_nodes(graph, nodes, role)

    monkeypatch.setattr(RoadGraph, "_option_from_nodes", reject_only_a1_a2_path)
    source = parallel_spine_source()
    lazy = compile_network(config(), source, FakeAgentRuntime())
    eager = compile_eager_reference(monkeypatch, config(), source, FakeAgentRuntime())

    assert_lazy_matches_eager_governed_contract(lazy, eager)
    for compiled in (lazy, eager):
        diagnostics = compiled.compilation_diagnostics
        assert diagnostics["unmaterializable_attachment_paths"] == 1
        assert diagnostics["optimization_findings"][-1]["finding_id"] == (
            "unmaterializable-osm-attachment-paths"
        )
        assert diagnostics["optimization_findings"][-1]["evidence"] == {"path_count": 1}


@pytest.mark.parametrize(
    ("source_factory", "lower_bound_factor"),
    [
        pytest.param(three_spine_source, 0.0, id="zero-bound-fallback"),
        pytest.param(three_spine_source, 0.5, id="discounted-positive-bound"),
        pytest.param(lambda: four_spine_source(reverse=True), 1.0, id="four-root-cycle-row-order"),
    ],
)
def test_lazy_bounds_match_eager_for_fallback_discount_and_order_adversaries(
    monkeypatch: pytest.MonkeyPatch,
    source_factory: object,
    lower_bound_factor: float,
) -> None:
    """Bounds must never alter equal-cost/tie or root-order selection semantics."""
    assert callable(source_factory)
    source = source_factory()
    monkeypatch.setattr(
        RoadGraph,
        "attachment_lower_bound_cost_factor",
        property(lambda _graph: lower_bound_factor),
    )
    # Exercise the conservative projected-bound fallback separately from the
    # default exact numeric root-group schedule.
    monkeypatch.setattr(
        RoadGraph,
        "attachment_group_distance_bounds",
        lambda _graph, _groups: (
            {},
            set(),
            {
                "root_group_distance_planning_searches": 0,
                "root_group_distance_planning_nodes_settled": 0,
            },
        ),
    )
    lazy = compile_network(config(), source, FakeAgentRuntime())
    eager = compile_eager_reference(monkeypatch, config(), source, FakeAgentRuntime())

    assert_lazy_matches_eager_governed_contract(lazy, eager)
    lazy_diagnostics = lazy.compilation_diagnostics["cross_spine"]
    eager_diagnostics = eager.compilation_diagnostics["cross_spine"]
    assert lazy_diagnostics["root_pair_route_searches"] <= eager_diagnostics[
        "root_pair_route_searches"
    ]
    if lower_bound_factor == 0.0:
        # A disabled metric bound is conservative: no pair is silently omitted.
        assert lazy_diagnostics["root_pair_candidate_searches"] == eager_diagnostics[
            "root_pair_candidate_searches"
        ]


def test_lazy_bounds_match_eager_after_rejection_retry_and_supersession(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected pair may retry, then be superseded when a later tree edge connects it."""
    original_evaluate = backbone_module._evaluate_meeting
    decisions = iter(("reject", "accept", "accept"))

    def reject_then_accept(*args: object, **kwargs: object) -> object:
        record = original_evaluate(*args, **kwargs)
        record.decision = next(decisions)
        record.outcome_reason = f"adversarial-{record.decision}"
        return record

    monkeypatch.setattr(backbone_module, "_evaluate_meeting", reject_then_accept)
    source = three_spine_source()
    lazy = compile_network(config(), source, FakeAgentRuntime())
    # Restore the deterministic forced sequence for the independent eager run.
    decisions = iter(("reject", "accept", "accept"))
    eager = compile_eager_reference(monkeypatch, config(), source, FakeAgentRuntime())

    assert_lazy_matches_eager_governed_contract(lazy, eager)
    lazy_records = [
        record for record in lazy.agent_records if record.connection_id.startswith("branch-meeting-")
    ]
    assert [record.decision for record in lazy_records].count("superseded") == 1
    lazy_diagnostics = lazy.compilation_diagnostics["cross_spine"]
    eager_diagnostics = eager.compilation_diagnostics["cross_spine"]
    assert lazy_diagnostics["root_pair_route_searches"] < eager_diagnostics[
        "root_pair_route_searches"
    ]
    assert lazy_diagnostics["root_pair_candidate_bounds_skipped_as_connected"] == 1


def test_missing_attachment_group_has_a_strictly_prior_safe_bound() -> None:
    """A disconnected/missing root attachment is handled as absent work, not a false shortcut."""
    source = three_spine_source()
    graph = RoadGraph(source["network"])
    compiled = compile_network(config(), source, FakeAgentRuntime())
    existing = compiled.spine_access_connections.iloc[0].copy()
    missing = existing.copy()
    missing["root_spine_id"] = "missing-root"
    missing["community_attachment_node"] = "missing-node"
    missing["place_id"] = "missing-place"
    group = gpd.GeoDataFrame([missing], geometry="geometry", crs=compiled.spine_access_connections.crs)

    assert backbone_module._meeting_group_attachment_geometry(group, graph) is None
    assert backbone_module._meeting_candidate_bound_rank(
        "missing-root", "other-root", None, None, graph
    ) == (-1e-9, "missing-root", "other-root", "", "")


def test_lazy_and_eager_schedulers_preserve_output_with_a_missing_attachment_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent attachment gets an eager-safe zero bound in the real scheduler."""
    original_attachment = backbone_module._meeting_group_attachment_geometry

    def missing_one_group(group: gpd.GeoDataFrame, graph: RoadGraph) -> object | None:
        if group["root_spine_id"].iloc[0] == "root:A3":
            return None
        return original_attachment(group, graph)

    monkeypatch.setattr(
        backbone_module,
        "_meeting_group_attachment_geometry",
        missing_one_group,
    )
    source = three_spine_source()
    lazy = compile_network(config(), source, FakeAgentRuntime())
    eager = compile_eager_reference(monkeypatch, config(), source, FakeAgentRuntime())
    assert_lazy_matches_eager_governed_contract(lazy, eager)
    assert lazy.compilation_diagnostics["cross_spine"]["root_pair_route_searches"] <= eager.compilation_diagnostics["cross_spine"]["root_pair_route_searches"]


def test_lazy_scheduler_skips_component_proven_unroutable_root_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An isolated root must retain logical coverage without futile route searches."""
    source = three_spine_source()
    source["places"] = frame(
        [
            *source["places"].to_dict("records"),
            {
                "place_id": "isolated-near",
                "name": "Isolated Near",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(1.02, 0),
            },
        ]
    )
    source["network"] = frame(
        [
            *source["network"].to_dict("records"),
            {
                "osmid": "isolated-feed",
                "highway": "unclassified",
                "geometry": LineString([(1, 0), (1.02, 0)]),
            },
            {
                "osmid": "isolated-spine-edge",
                "highway": "primary",
                "ref": "A4",
                "geometry": LineString([(1, 0), (1, 0.01)]),
            },
        ]
    )
    source["context"] = frame(
        [
            *source["context"].to_dict("records"),
            {
                "evidence_id": "isolated-a4",
                "feature_type": "a-road-spine",
                "name": "A4",
                "category": "A-road strategic spine",
                "source_id": "isolated-spine-edge",
                "feature_count": 1,
                "network_scope": "rural",
                "geometry": LineString([(1, 0), (1, 0.01)]),
            },
        ]
    )

    lazy = compile_network(config(), source, FakeAgentRuntime())
    eager = compile_eager_reference(monkeypatch, config(), source, FakeAgentRuntime())

    assert_lazy_matches_eager_governed_contract(lazy, eager)
    lazy_diagnostics = lazy.compilation_diagnostics["cross_spine"]
    eager_diagnostics = eager.compilation_diagnostics["cross_spine"]
    assert lazy_diagnostics["root_pairs_considered"] == 6
    assert lazy_diagnostics["root_pair_candidate_searches"] == 6
    assert eager_diagnostics["root_pair_route_searches"] == 6
    assert lazy_diagnostics["root_pair_route_searches"] == 2
    assert lazy_diagnostics["root_pair_candidate_bounds_skipped_as_connected"] == 1
    assert lazy_diagnostics["root_pair_candidate_bounds_skipped_as_unroutable"] == 3
    assert lazy_diagnostics["root_pair_route_searches_avoided"] == 4


def test_four_root_source_order_is_equal_in_lazy_and_eager_schedulers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both schedules must be insensitive to the normal/reversed source rows."""
    lazy_normal = compile_network(config(), four_spine_source(reverse=False), FakeAgentRuntime())
    lazy_reversed = compile_network(config(), four_spine_source(reverse=True), FakeAgentRuntime())
    assert backbone_snapshot(lazy_normal) == backbone_snapshot(lazy_reversed)
    with monkeypatch.context() as eager_patch:
        eager_normal = compile_eager_reference(
            eager_patch,
            config(),
            four_spine_source(reverse=False),
            FakeAgentRuntime(),
        )
    with monkeypatch.context() as eager_patch:
        eager_reversed = compile_eager_reference(
            eager_patch,
            config(),
            four_spine_source(reverse=True),
            FakeAgentRuntime(),
        )
    # This is a full governed-contract matrix, not only the historical
    # backbone projection: both schedules must preserve every published field
    # for normal and reversed source ordering.
    assert_lazy_matches_eager_governed_contract(lazy_normal, eager_normal)
    assert_lazy_matches_eager_governed_contract(lazy_reversed, eager_reversed)


def test_rejected_first_meeting_falls_through_to_next_adjacency() -> None:
    runtime = FakeAgentRuntime(
        {
            AgentRole.DECISION: [
                *({"request_id": "$request", "choice_id": "1"} for _ in range(3)),
                {"request_id": "$request", "choice_id": "2"},
                {"request_id": "$request", "choice_id": "1"},
            ]
        }
    )

    council = config()
    council.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    compiled = compile_network(council, parallel_spine_source(), runtime)

    assert len(compiled.branch_meeting_connections) == 1
    meeting_records = [
        record
        for record in compiled.agent_records
        if record.connection_id.startswith("branch-meeting-")
    ]
    assert [record.decision for record in meeting_records] == ["superseded", "accept"]
    assert (
        compiled.branch_meeting_connections.iloc[0]["meeting_connection_id"]
        == meeting_records[-1].connection_id
    )
    diagnostics = compiled.compilation_diagnostics["cross_spine"]
    # Both candidate attempts reached the agent.  The first agent rejection is
    # later superseded only because the second candidate is accepted; it must
    # remain visible as prior agent work, not be misreported as no rejection.
    assert diagnostics["root_pairs_considered"] == 1
    assert diagnostics["root_pair_candidate_searches"] == 2
    assert diagnostics["meeting_agent_evaluations"] == 2
    assert diagnostics["meeting_agent_evaluation_initial_outcomes"] == {
        "accept": 1,
        "reject": 1,
        "gap": 0,
    }
    assert diagnostics["meeting_agent_evaluation_final_dispositions"] == {
        "accept": 1,
        "reject": 0,
        "gap": 0,
        "superseded": 1,
    }
    assert diagnostics["candidate_connectors"] == 1
    assert diagnostics["authoritative_connectors"] == 1
    assert diagnostics["route_refinement_findings"] == 0


def test_rejected_meetings_superseded_when_other_tree_edges_connect_the_roots() -> None:
    runtime = FakeAgentRuntime(
        {
            AgentRole.DECISION: [
                *({"request_id": "$request", "choice_id": "1"} for _ in range(4)),
                *({"request_id": "$request", "choice_id": "2"} for _ in range(2)),
                *({"request_id": "$request", "choice_id": "1"} for _ in range(2)),
            ]
        }
    )

    council = config()
    council.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    compiled = compile_network(council, three_spine_source(), runtime)

    meeting_records = [
        record
        for record in compiled.agent_records
        if record.connection_id.startswith("branch-meeting-")
    ]
    assert [record.decision for record in meeting_records].count("accept") == 2
    assert [record.decision for record in meeting_records].count("superseded") == 2
    assert all(record.decision != "gap" for record in meeting_records)


@pytest.mark.parametrize("row_factory", ["_connection_row", "_meeting_row"])
def test_intervention_coverage_includes_every_backbone_connection_role(
    monkeypatch: pytest.MonkeyPatch,
    row_factory: str,
) -> None:
    original = getattr(backbone_module, row_factory)

    def without_intervention(*args: object, **kwargs: object) -> dict[str, object]:
        row = original(*args, **kwargs)
        row["intervention_archetype"] = None
        return row

    monkeypatch.setattr(backbone_module, row_factory, without_intervention)

    compiled = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())

    assert compiled.criteria["network"]["intervention_coverage"] == "red"


def test_meeting_distance_challenge_uses_unrounded_route_length() -> None:
    compiled = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    grouped = list(compiled.spine_access_connections.groupby("root_spine_id", sort=True))
    left = grouped[0][1].iloc[0]
    right = grouped[1][1].iloc[0]
    candidate = backbone_module._MeetingCandidate(
        rank=(),
        left=left,
        right=right,
        option=RouteOption(
            role="direct",
            geometry=LineString([(0, 0), (0.1, 0)]),
            length_km=15.0004,
            edge_ids=["forward"],
            a_road_share=0.0,
            ncn_share=0.0,
            bidirectional=True,
            reverse_length_km=15.0004,
            reverse_edge_ids=["reverse"],
            reverse_corridor_share=1.0,
            impracticable_alongside=False,
        ),
        start_node="left",
        end_node="right",
    )

    row = backbone_module._meeting_row(candidate, max_connection_km=15.0)

    assert row["distance_km"] == 15.0
    assert row["criterion_distance"] == "amber"


def test_equidistant_attachment_tie_is_stable_when_source_rows_reverse() -> None:
    places = [
        {
            "place_id": "tie",
            "name": "Tie",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(0, 0),
        },
        {
            "place_id": "left-anchor",
            "name": "Left Anchor",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(-0.01, 0),
        },
    ]
    network = [
        {
            "osmid": "left",
            "highway": "primary",
            "ref": "A1",
            "geometry": LineString([(-0.01, 0), (-0.001, 0)]),
        },
        {
            "osmid": "right",
            "highway": "primary",
            "ref": "A2",
            "geometry": LineString([(0.001, 0), (0.01, 0)]),
        },
    ]
    context = [
        {
            "evidence_id": "left-spine",
            "feature_type": "a-road-spine",
            "name": "A1",
            "category": "A-road strategic spine",
            "source_id": "left",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": network[0]["geometry"],
        },
        {
            "evidence_id": "right-spine",
            "feature_type": "a-road-spine",
            "name": "A2",
            "category": "A-road strategic spine",
            "source_id": "right",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": network[1]["geometry"],
        },
    ]

    def compile_rows(reverse: bool) -> object:
        return compile_network(
            config(),
            {
                "places": frame(list(reversed(places)) if reverse else places),
                "network": frame(list(reversed(network)) if reverse else network),
                "context": frame(list(reversed(context)) if reverse else context),
                "boundary": gpd.GeoDataFrame(geometry=[], crs=4326),
            },
            FakeAgentRuntime(),
        )

    assert topology(compile_rows(False)) == topology(compile_rows(True))


def test_unreachable_community_becomes_a_gap_without_fabricated_linework() -> None:
    source = parallel_spine_source()
    source["places"] = frame(
        [
            *source["places"].to_dict("records"),
            {
                "place_id": "island",
                "name": "Island",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(1, 1),
            },
        ]
    )
    source["network"] = frame(
        [
            *source["network"].to_dict("records"),
            {
                "osmid": "island-edge",
                "highway": "unclassified",
                "geometry": LineString([(1, 1), (1.01, 1)]),
            },
        ]
    )

    compiled = compile_network(config(), source, FakeAgentRuntime())

    obligation = compiled.access_obligations.set_index("place_id").loc["island"]
    assert obligation["service_status"] == "network-gap"
    gaps = compiled.gaps[compiled.gaps["network_role"] == "spine-access-gap"]
    assert len(gaps) == 1
    gap = gaps.iloc[0]
    assert gap["from_place"] == "island"
    assert gap.geometry.geom_type == "MultiPoint"
    assert len(gap.geometry.geoms) == 1
    assert gap["criterion_continuity"] == "red"
    assert compiled.criteria["spine_network"]["all_access_obligations_resolved"] == "red"


def test_agent_gate_rejection_cannot_enter_validated_backbone_state() -> None:
    runtime = FakeAgentRuntime(
        {
            AgentRole.DECISION: [
                {"request_id": "$request", "choice_id": "3"} for _ in range(6)
            ]
        }
    )

    council = config()
    council.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    compiled = compile_network(council, parallel_spine_source(), runtime)

    assert compiled.spine_access_connections.empty
    assert set(compiled.access_obligations["service_status"]) == {"network-gap"}
    access_records = [
        record
        for record in compiled.agent_records
        if record.connection_id.startswith("spine-access-")
    ]
    assert len(access_records) == 3
    assert {record.decision for record in access_records} == {"gap"}
    assert {record.selected_choice_id for record in access_records} == {"3"}


def test_meaningful_cross_boundary_gateway_attaches_to_the_assembled_frontier() -> None:
    source = parallel_spine_source()
    source["places"] = frame(
        [
            *source["places"].to_dict("records"),
            {
                "place_id": "gateway-east",
                "name": "Towards East Town",
                "kind": "cross_boundary_gateway",
                "place_class": "road",
                "geometry": Point(0.09, 0),
            },
        ]
    )
    source["network"] = frame(
        [
            *source["network"].to_dict("records"),
            {
                "osmid": "gateway-link",
                "highway": "unclassified",
                "geometry": LineString([(0.08, 0), (0.09, 0)]),
            },
            {
                "osmid": "gateway-to-spine",
                "highway": "unclassified",
                "geometry": LineString([(0.09, 0), (0.1, 0)]),
            },
        ]
    )

    compiled = compile_network(config(), source, FakeAgentRuntime())

    gateway = compiled.spine_access_connections[
        compiled.spine_access_connections["place_id"] == "gateway-east"
    ].iloc[0]
    assert gateway["place_kind"] == "cross_boundary_gateway"
    assert gateway["network_role"] == "gateway-access-connection"
    assert gateway["root_spine_id"] in set(compiled.strategic_spines["spine_id"])
    assert gateway["parent_role"] in {"strategic-spine", "spine-access-connection"}
    assert "gateway-east" not in set(compiled.access_obligations["place_id"])
    branch_place_ids = {
        place_id
        for value in compiled.spine_access_branches["place_ids"]
        for place_id in json.loads(value)
    }
    assert "gateway-east" not in branch_place_ids
    assert compiled.criteria["spine_network"]["gateway_coverage"] == "green"


def test_colocated_gateway_is_already_connected_without_zero_length_linework() -> None:
    source = parallel_spine_source()
    source["places"] = frame(
        [
            *source["places"].to_dict("records"),
            {
                "place_id": "gateway-colocated",
                "name": "Towards Nearby Town",
                "kind": "cross_boundary_gateway",
                "place_class": "road",
                "geometry": Point(0.08, 0),
            },
        ]
    )

    compiled = compile_network(config(), source, FakeAgentRuntime())

    gateway_rows = compiled.spine_access_connections[
        compiled.spine_access_connections["place_id"] == "gateway-colocated"
    ]
    assert gateway_rows.empty
    assert compiled.criteria["spine_network"]["gateway_coverage"] == "green"


def test_growth_uses_lazy_bounds_to_avoid_redundant_searches_at_representative_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    community_count = 24
    places = [
        {
            "place_id": f"community-{index:02d}",
            "name": f"Community {index:02d}",
            "kind": "community",
            "place_class": "village",
            "geometry": Point((index + 1) * 3000, 0),
        }
        for index in range(community_count)
    ]
    network = [
        {
            "osmid": f"edge-{index:02d}",
            "highway": "primary" if index == 0 else "unclassified",
            "ref": "A1" if index == 0 else None,
            "geometry": LineString([(index * 3000, 0), ((index + 1) * 3000, 0)]),
        }
        for index in range(community_count)
    ]
    context = [
        {
            "evidence_id": "scale-spine",
            "feature_type": "a-road-spine",
            "name": "A1",
            "category": "A-road strategic spine",
            "source_id": "edge-00",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": network[0]["geometry"],
        }
    ]
    calls = 0
    original = backbone_module._candidate

    def counted_candidate(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(backbone_module, "_candidate", counted_candidate)
    compiled = compile_network(
        config(),
        {
            "places": gpd.GeoDataFrame(places, geometry="geometry", crs=27700),
            "network": gpd.GeoDataFrame(network, geometry="geometry", crs=27700),
            "context": gpd.GeoDataFrame(context, geometry="geometry", crs=27700),
            "boundary": gpd.GeoDataFrame(geometry=[], crs=27700),
        },
        FakeAgentRuntime(),
    )

    assert len(compiled.spine_access_connections) == community_count
    diagnostics = compiled.compilation_diagnostics
    assert calls == community_count
    assert diagnostics["candidate_evaluations"] == calls
    assert diagnostics["candidate_pairs_enqueued"] == (
        community_count + community_count * (community_count - 1) // 2
    )
    assert diagnostics["candidate_searches_avoided"] == (
        diagnostics["candidate_pairs_enqueued"] - calls
    )
    assert diagnostics["candidate_lower_bound_factor"] == 1.0
    assert diagnostics["candidate_lower_bound_disabled_reason"] is None


def test_lazy_bounds_match_eager_equivalent_assembly_with_discounted_source_costs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = with_source_costs_below_geometry(parallel_spine_source())
    lazy = compile_network(config(), source, FakeAgentRuntime())

    def disable_lower_bound(self: object) -> None:
        self._lower_bound_cost_factor = 0.0
        self._lower_bound_disabled_reason = "forced-by-regression-test"

    monkeypatch.setattr(
        backbone_module.RoadGraph,
        "_set_lower_bound_cost_factor",
        disable_lower_bound,
    )
    eager_equivalent = compile_network(config(), source, FakeAgentRuntime())

    assert 0.0 < lazy.compilation_diagnostics["candidate_lower_bound_factor"] < 1.0
    assert eager_equivalent.compilation_diagnostics["candidate_lower_bound_factor"] == 0.0
    assert backbone_snapshot(lazy) == backbone_snapshot(eager_equivalent)


def test_lazy_bounds_skip_stale_community_work_after_the_community_is_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = parallel_spine_source()
    source["places"] = source["places"].query("place_id != 'hinterland'").copy()
    calls = 0
    original = backbone_module._candidate

    def counted_candidate(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(backbone_module, "_candidate", counted_candidate)
    compiled = compile_network(config(), source, FakeAgentRuntime())

    assert calls == 2
    assert compiled.compilation_diagnostics["candidate_evaluations"] == 2
    assert compiled.compilation_diagnostics["candidate_pairs_enqueued"] == 5
    assert compiled.compilation_diagnostics["candidate_searches_avoided"] == 3


def test_rejected_community_candidate_is_superseded_when_a_later_frontier_is_accepted() -> None:
    runtime = FakeAgentRuntime(
        {
            AgentRole.DECISION: [
                {"request_id": "$request", "choice_id": "2"},
                *({"request_id": "$request", "choice_id": "1"} for _ in range(8)),
            ]
        }
    )
    council = config()
    council.compilation.agent.review_statuses = (TrafficLight.GREEN,)

    compiled = compile_network(council, parallel_spine_source(), runtime)

    access_records = [
        record
        for record in compiled.agent_records
        if record.connection_id.startswith("spine-access-")
    ]
    rejected = access_records[0]
    assert rejected.decision == "superseded"
    assert "different governed frontier attachment" in rejected.outcome_reason
    assert any(
        record.decision == "accept"
        and record.affected_feature_identifiers[1] == rejected.affected_feature_identifiers[1]
        for record in access_records
    )
    assert [record.decision for record in access_records].count("accept") == 3
    assert len(compiled.spine_access_connections) == 3


def test_tied_roots_remain_eager_equivalent_when_inputs_are_reversed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    places = [
        {
            "place_id": "tie",
            "name": "Tie",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(0, 0),
        },
        {
            "place_id": "left-anchor",
            "name": "Left Anchor",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(-0.01, 0),
        },
    ]
    network = [
        {
            "osmid": "left",
            "highway": "primary",
            "ref": "A1",
            "length": 100,
            "geometry": LineString([(-0.01, 0), (-0.001, 0)]),
        },
        {
            "osmid": "right",
            "highway": "primary",
            "ref": "A2",
            "length": 100,
            "geometry": LineString([(0.001, 0), (0.01, 0)]),
        },
    ]
    context = [
        {
            "evidence_id": "left-spine",
            "feature_type": "a-road-spine",
            "name": "A1",
            "category": "A-road strategic spine",
            "source_id": "left",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": network[0]["geometry"],
        },
        {
            "evidence_id": "right-spine",
            "feature_type": "a-road-spine",
            "name": "A2",
            "category": "A-road strategic spine",
            "source_id": "right",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": network[1]["geometry"],
        },
    ]

    def compile_rows(*, reverse: bool, eager_equivalent: bool) -> object:
        if eager_equivalent:
            monkeypatch.setattr(
                backbone_module.RoadGraph,
                "_set_lower_bound_cost_factor",
                lambda graph: setattr(graph, "_lower_bound_cost_factor", 0.0),
            )
        return compile_network(
            config(),
            {
                "places": frame(list(reversed(places)) if reverse else places),
                "network": frame(list(reversed(network)) if reverse else network),
                "context": frame(list(reversed(context)) if reverse else context),
                "boundary": gpd.GeoDataFrame(geometry=[], crs=4326),
            },
            FakeAgentRuntime(),
        )

    lazy = compile_rows(reverse=False, eager_equivalent=False)
    reversed_lazy = compile_rows(reverse=True, eager_equivalent=False)
    eager_equivalent = compile_rows(reverse=True, eager_equivalent=True)

    assert backbone_snapshot(lazy) == backbone_snapshot(reversed_lazy)
    assert backbone_snapshot(lazy) == backbone_snapshot(eager_equivalent)
