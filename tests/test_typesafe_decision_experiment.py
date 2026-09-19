from __future__ import annotations

import json

import geopandas as gpd
from scripts.experiments.diagnose_typesafe_planning import (
    build_probe_request,
    build_scoped_packet,
    map_choice_to_catalog,
    prepare_experiment,
)
from shapely.geometry import LineString

from satn.routing import RoadGraph


def _records() -> tuple[dict[str, object], dict[str, object]]:
    packet = {
        "kind": "planning-task-packet",
        "payload": {
            "schema_version": "planning-task-packet/v1",
            "task_id": "task-radstock",
            "question_kind": "alignment",
            "brief": {"brief_ref": "owner-brief", "corridor_policy": {"retain": True}},
            "policy": {"source_designation_is_current_usability": False},
            "input_binding": {"snapshot_id": "snapshot-1"},
            "named_endpoints": [
                {"place_id": "radstock", "name": "Radstock"},
                {"place_id": "midsomer", "name": "Midsomer Norton"},
            ],
            "connection_options": [{"connection_id": "connection-1"}],
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "connection_id": "connection-1",
                    "graph_path": {
                        "directed_edge_ids": ["edge-1"],
                        "source_edge_ids": ["osm-1"],
                        "length_km": 1.2,
                    },
                    "current_or_future": "unknown",
                }
            ],
            "facts": {"obligations": [], "source_corridors": []},
            "source_evidence": {
                "evidence_refs": [],
                "directed_edges": [
                    {
                        "directed_edge_id": "edge-1",
                        "source_edge_id": "osm-1",
                        "from_node_id": "node-a",
                        "to_node_id": "node-b",
                    }
                ],
            },
            "prior_decisions": {},
            "feedback_unknowns": [],
            "unknowns": [],
            "questions": {
                "decision": {
                    "type": "choice",
                    "instructions": "Choose one supplied alignment.",
                    "criteria": {"candidate-1": "The supplied candidate."},
                }
            },
            "scope": {"scope_refs": ["connection-1"]},
        },
    }
    request = {
        "kind": "request",
        "payload": {
            "request_id": "request-1",
            "task_packet": packet["payload"],
            "questions": packet["payload"]["questions"],
        },
    }
    return packet, request


def test_public_packet_and_dependency_probe_preserve_source_identity_and_map_choice() -> None:
    packet, request = _records()
    source_index = {
        "edge-1": {
            "source_binding": {
                "snapshot_id": "snapshot-1",
                "source_file": "network.geojson",
                "source_edge_id": "osm-1",
                "from_node_id": "node-a",
                "to_node_id": "node-b",
                "geometry_fingerprint": "geometry-1",
            },
            "road_facts": {
                "highway": {"status": "value", "value": "primary"},
                "access": {"status": "null", "value": None},
                "surface": {"status": "absent"},
            },
        }
    }

    experiment = build_scoped_packet(
        packet,
        request,
        source_index,
        snapshot_binding={"snapshot_id": "snapshot-1", "network_sha256": "network-hash"},
    )

    candidate = experiment["faithful_packet"]["candidates"][0]
    assert candidate["candidate_id"] == "candidate-1"
    assert candidate["graph_path"]["directed_edge_ids"] == ["edge-1"]
    assert candidate["graph_path"]["source_edge_ids"] == ["osm-1"]
    assert experiment["source_facts"]["directed_edges"][0]["directed_edge_id"] == "edge-1"
    assert experiment["source_facts"]["directed_edges"][0]["road_facts"]["access"] == {
        "status": "null",
        "value": None,
    }
    model_edges = experiment["faithful_packet"]["source_evidence"]["directed_edges"]
    assert model_edges["edge-1"]["road_facts"] == {
        "highway": "primary",
    }
    assert model_edges["edge-1"]["road_facts_null"] == ["access"]
    assert model_edges["edge-1"]["road_facts_absent"] == ["surface"]
    assert experiment["faithful_packet"]["source_evidence"]["snapshot_ref"] == {
        "snapshot_id": "snapshot-1",
        "network_sha256": "network-hash",
    }
    assert "source_binding" not in model_edges["edge-1"]
    assert "source_feature_key" not in model_edges["edge-1"]
    assert "geometry_fingerprint" not in model_edges["edge-1"]
    assert model_edges["edge-1"]["directed_edge_id"] == "edge-1"
    assert model_edges["edge-1"]["source_edge_id"] == "osm-1"
    assert model_edges["edge-1"]["from_node_id"] == "node-a"
    assert model_edges["edge-1"]["to_node_id"] == "node-b"

    faithful_before = build_probe_request(experiment, "faithful-facts")
    framing_before = build_probe_request(experiment, "framing")
    dependency = build_probe_request(experiment, "dependency")
    criteria = dependency["questions"]["decision"]["criteria"]
    assert "current-provision-access" in criteria
    assert "policy-not-specified" in criteria
    assert "candidate-coverage-inadequate" in criteria
    assert "no-identified-factual-blocker" in criteria
    assert "cannot-determine" in criteria

    model_catalog = dependency["state"]["unknown_catalog"]
    assert model_catalog["catalog_ref"]["field"] == "catalog"
    assert (
        model_catalog["catalog_ref"]["fingerprint"] == experiment["fingerprints"]["unknown_catalog"]
    )
    assert model_catalog["source_path_refs"]["alignment-candidate-paths"] == {
        "candidate_refs": ["candidate-1"],
        "connection_refs": ["connection-1"],
        "edge_refs_field": "candidates[*].graph_path.directed_edge_ids",
    }
    assert all("source_edge_refs" not in claim for claim in model_catalog["claims"])
    assert model_catalog["claims"][0]["scope_ref"] == "alignment-scope"
    assert (
        model_catalog["claims"][0]["coverage"]
        == experiment["unknown_catalog"]["claims"][0]["coverage"]
    )
    assert dependency["catalog"] == experiment["unknown_catalog"]
    assert dependency["catalog"]["claims"][0]["source_edge_refs"] == ["edge-1"]
    full_mapping = map_choice_to_catalog(
        {"type": "choice", "choice": "current-provision-access"},
        dependency["catalog"],
    )
    assert full_mapping["status"] == "mapped"
    assert full_mapping["coverage"]["selected_edge_status_counts"] == {
        "value": 0,
        "null": 1,
        "absent": 0,
    }
    assert (
        faithful_before["body_sha256"]
        == build_probe_request(experiment, "faithful-facts")["body_sha256"]
    )
    assert (
        framing_before["body_sha256"] == build_probe_request(experiment, "framing")["body_sha256"]
    )

    for probe in ("faithful-facts", "framing", "dependency"):
        probe_request = build_probe_request(experiment, probe)
        assert probe_request["state"]["questions"] == probe_request["questions"]

    mapped = map_choice_to_catalog(
        {"type": "choice", "choice": "current-provision-access"},
        experiment["unknown_catalog"],
    )
    assert mapped["status"] == "mapped"
    assert mapped["claim_id"] == "current-provision-access"
    assert mapped["scope_refs"] == ["candidate-1", "connection-1"]


def test_prepare_binds_same_source_id_to_the_exact_segment_and_direction(tmp_path) -> None:
    network_path = tmp_path / "network.geojson"
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "same-way",
                "u": "node-a",
                "v": "node-b",
                "highway": "primary",
                "access": "no",
                "geometry": LineString([(-2.5, 51.2), (-2.49, 51.2)]),
            },
            {
                "osmid": "same-way",
                "u": "node-b",
                "v": "node-c",
                "highway": "cycleway",
                "access": "yes",
                "geometry": LineString([(-2.49, 51.2), (-2.48, 51.2)]),
            },
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )
    network.to_file(network_path, driver="GeoJSON")
    graph = RoadGraph(gpd.read_file(network_path))
    first_edge = graph.graph.get_edge_data("node-a", "node-b")["directed_edge_id"]
    second_edge = graph.graph.get_edge_data("node-b", "node-c")["directed_edge_id"]

    packet, request = _records()
    payload = packet["payload"]
    payload["candidates"][0]["graph_path"] = {
        "directed_edge_ids": [first_edge, second_edge],
        "source_edge_ids": ["same-way", "same-way"],
        "length_km": 1.2,
    }
    payload["source_evidence"]["directed_edges"] = [
        {
            "directed_edge_id": first_edge,
            "source_edge_id": "same-way",
            "from_node_id": "node-a",
            "to_node_id": "node-b",
        },
        {
            "directed_edge_id": second_edge,
            "source_edge_id": "same-way",
            "from_node_id": "node-b",
            "to_node_id": "node-c",
        },
    ]
    request["payload"]["task_packet"] = payload

    history_root = tmp_path / "history"
    for record_id, record in (
        ("a" * 64, packet),
        ("b" * 64, request),
    ):
        record_path = history_root / "records" / record_id[:2] / f"{record_id}.json"
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(record), encoding="utf-8")
    snapshot_root = tmp_path / "snapshot" / "snapshot-1"
    snapshot_root.mkdir(parents=True)
    (snapshot_root / "network.geojson").write_bytes(network_path.read_bytes())
    (snapshot_root / "snapshot.json").write_text(
        json.dumps({"snapshot_id": "snapshot-1"}), encoding="utf-8"
    )

    result = prepare_experiment(
        history_root=history_root,
        snapshot_root=snapshot_root,
        output_root=tmp_path / "output",
        probe="faithful-facts",
        packet_record_id="a" * 64,
        request_record_id="b" * 64,
        receipt_ids=(),
    )

    source_edges = {
        item["directed_edge_id"]: item
        for item in result["experiment"]["source_facts"]["directed_edges"]
    }
    assert source_edges[first_edge]["from_node_id"] == "node-a"
    assert source_edges[first_edge]["to_node_id"] == "node-b"
    assert source_edges[first_edge]["road_facts"]["highway"] == "primary"
    assert source_edges[second_edge]["from_node_id"] == "node-b"
    assert source_edges[second_edge]["to_node_id"] == "node-c"
    assert source_edges[second_edge]["road_facts"]["highway"] == "cycleway"
