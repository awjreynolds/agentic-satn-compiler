from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "experiments"))

from evaluate_typesafe_planning import (
    _compact_runtime_payload,
    _manifest_history_summary,
)

from satn.planning_runtime import PlanningRuntime
from satn.typesafe_planning import ChoiceQuestion, TypeSafeRequest


def _geometry_ref(geometry_id: str, source_ref: str, geometry: object) -> dict[str, object]:
    return {
        "geometry_id": geometry_id,
        "content_fingerprint": f"fingerprint-{geometry_id}",
        "geometry_kind": "line",
        "crs": "EPSG:4326",
        "source_ref": source_ref,
        "geometry": geometry,
    }


def _classifier_inputs() -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    route_geometry = {
        "type": "LineString",
        "coordinates": [[float(index), float(index) / 2] for index in range(32)],
    }
    endpoint_geometry = {
        "type": "Point",
        "coordinates": [1.0, 2.0],
    }
    route_ref = _geometry_ref("route-geometry", "connection-1", route_geometry)
    origin_ref = _geometry_ref("origin-geometry", "place-origin", endpoint_geometry)
    destination_ref = _geometry_ref("destination-geometry", "place-destination", endpoint_geometry)
    ordered_directed_edges = ["edge-2", "edge-1"]
    ordered_source_edges = ["source-edge-2", "source-edge-1"]
    candidate = {
        "candidate_id": "candidate-1",
        "obligation_id": "connection-1",
        "connection_id": "connection-1",
        "role": "graph-alternative",
        "status": "admitted",
        "current_or_future": "future",
        "uncertainty": {"endpoint_attachment": "approximate"},
        "place_refs": ["place-origin", "place-destination"],
        "source_corridor_refs": ["corridor-1"],
        "provenance": {
            "origin_place_ref": "place-origin",
            "destination_place_ref": "place-destination",
            "source_refs": [{"source_id": "network"}],
            "evidence_refs": ["edge-evidence"],
        },
        "geometry_ref": route_ref,
        "graph_path": {
            "directed_edge_ids": ordered_directed_edges,
            "source_edge_ids": ordered_source_edges,
            "length_km": 12.5,
            "a_road_share": 0.75,
            "ncn_share": 0.25,
            "road_class": "A-road",
            "access_status": "unknown",
        },
        "endpoint_provenance": {
            "origin_place_id": "place-origin",
            "destination_place_id": "place-destination",
            "origin_node_id": "node-origin",
            "destination_node_id": "node-destination",
            "origin_attachment_distance_m": 4.5,
            "destination_attachment_distance_m": 7.5,
            "directed_edge_ids": ordered_directed_edges,
            "source_edge_ids": ordered_source_edges,
            "origin_geometry_ref": origin_ref,
            "destination_geometry_ref": destination_ref,
        },
    }
    edge = {
        "directed_edge_id": "edge-2",
        "source_edge_id": "source-edge-2",
        "from_node_id": "node-origin",
        "to_node_id": "node-middle",
        "road_class": "A-road",
        "access_status": "unknown",
        "infrastructure_refs": ["infrastructure-1"],
        "road_facts": {
            "highway": "primary",
            "name": "A4",
            "lanes": 2,
            "maxspeed": "30 mph",
            "access": None,
            "surface": "asphalt",
        },
        "geometry_ref": _geometry_ref(
            "edge-geometry",
            "source-edge-2",
            {
                "type": "LineString",
                "coordinates": [[1.0, 2.0], [1.5, 2.5]],
            },
        ),
    }
    problem = {
        "problem_id": "problem-1",
        "input_fingerprint": "problem-fingerprint",
        "brief": {"brief_ref": "brief-1"},
        "binding": {"snapshot_id": "snapshot-1"},
        "places": [
            {"place_id": "place-origin", "name": "Origin", "kind": "settlement"},
            {"place_id": "place-destination", "name": "Destination", "kind": "settlement"},
        ],
        "source_corridors": [
            {
                "corridor_id": "corridor-1",
                "evidence_refs": ["corridor-evidence"],
                "geometry_ref": _geometry_ref("corridor-geometry", "corridor-1", route_geometry),
            }
        ],
        "obligations": [{"obligation_id": "connection-1", "kind": "place-connection"}],
        "graph_evidence": {"directed_edges": [edge]},
    }
    state = {
        "state_id": "state-1",
        "state_fingerprint": "state-fingerprint",
        "connection_intents": [],
        "selected_alignments": [],
        "departures": [],
        "future_interventions": [],
        "planning_gaps": [],
        "unknown_facts": [],
    }
    context = {
        "question_kind": "alignment",
        "candidates": {"candidate-1": candidate},
        "scope_refs": ["connection-1"],
        "evidence_refs": ["edge-evidence"],
        "permitted_action_kinds": ["select-alignment", "request-evidence"],
    }
    questions = {
        "decision": ChoiceQuestion(
            instructions="Choose an admitted route or request evidence.",
            criteria={
                "candidate-1": "the supplied graph alternative",
                "__needs_evidence__": "request evidence",
            },
        )
    }
    return problem, state, context, questions, candidate


def test_classifier_packet_omits_geometry_but_keeps_route_facts_and_identities(
    tmp_path: Path,
) -> None:
    problem, state, context, questions, candidate = _classifier_inputs()
    packet = PlanningRuntime(tmp_path / "history")._task_packet(
        problem, state, questions, "live", context, "main"
    )

    projected = packet["candidates"][0]
    assert projected["candidate_id"] == "candidate-1"
    assert projected["uncertainty"] == {"endpoint_attachment": "approximate"}
    assert "geometry_ref" not in projected
    assert projected["evidence_refs"] == ["edge-evidence"]
    assert "provenance" not in projected
    assert projected["graph_path"] == {
        "directed_edge_ids": ["edge-2", "edge-1"],
        "source_edge_ids": ["source-edge-2", "source-edge-1"],
        "length_km": 12.5,
        "a_road_share": 0.75,
        "ncn_share": 0.25,
        "road_class": "A-road",
        "access_status": "unknown",
    }
    assert {item["name"] for item in packet["named_endpoints"]} == {"Origin", "Destination"}
    assert projected["endpoint_provenance"]["origin_place_id"] == "place-origin"
    assert projected["endpoint_provenance"]["destination_place_id"] == "place-destination"
    assert projected["endpoint_provenance"]["origin_node_id"] == "node-origin"
    assert projected["endpoint_provenance"]["destination_node_id"] == "node-destination"
    assert projected["endpoint_provenance"]["origin_attachment_distance_m"] == 4.5
    assert projected["endpoint_provenance"]["destination_attachment_distance_m"] == 7.5
    assert "origin_geometry_ref" not in projected["endpoint_provenance"]
    assert "destination_geometry_ref" not in projected["endpoint_provenance"]
    assert "directed_edge_ids" not in projected["endpoint_provenance"]
    assert "source_edge_ids" not in projected["endpoint_provenance"]

    source_edge = packet["source_evidence"]["directed_edges"][0]
    assert source_edge["directed_edge_id"] == "edge-2"
    assert source_edge["from_node_id"] == "node-origin"
    assert source_edge["to_node_id"] == "node-middle"
    assert source_edge["road_class"] == "A-road"
    assert source_edge["access_status"] == "unknown"
    assert source_edge["infrastructure_refs"] == ["infrastructure-1"]
    assert source_edge["road_facts"] == {
        "highway": "primary",
        "name": "A4",
        "lanes": 2,
        "maxspeed": "30 mph",
        "access": None,
        "surface": "asphalt",
    }
    assert "geometry_ref" not in source_edge

    packet_text = json.dumps(packet, sort_keys=True)
    assert "coordinates" not in packet_text
    assert "content_fingerprint" not in packet_text
    assert "geometry_id" not in packet_text

    expanded_packet = deepcopy(packet)
    expanded_packet["candidates"] = [candidate]
    expanded_packet["source_evidence"]["directed_edges"] = [
        problem["graph_evidence"]["directed_edges"][0]
    ]
    assert len(TypeSafeRequest(packet, questions, "jev-latest").body()) < len(
        TypeSafeRequest(expanded_packet, questions, "jev-latest").body()
    )


class _Store:
    def __init__(self) -> None:
        self.puts: list[tuple[str, object]] = []

    def put(self, value: object, *, kind: str) -> str:
        self.puts.append((kind, value))
        return f"ref-{kind}"


def test_runtime_summary_keeps_output_by_content_reference(tmp_path: Path) -> None:
    store = _Store()
    output = {
        "output_fingerprint": "output-fingerprint",
        "selected_alignments": [],
        "source_inventory": [{"geometry": {"coordinates": [[1, 2], [3, 4]]}}],
    }

    report = _compact_runtime_payload(
        {
            "problem": {"input_fingerprint": "problem-fingerprint"},
            "state": {"state_fingerprint": "state-fingerprint"},
            "output": output,
            "status": "reviewable-incomplete",
        },
        store,
    )

    assert report == {
        "output_fingerprint": "output-fingerprint",
        "output_ref": "ref-planning-output",
        "problem_fingerprint": "problem-fingerprint",
        "problem_ref": "ref-planning-problem",
        "state_fingerprint": "state-fingerprint",
        "state_ref": "ref-state",
        "status": "reviewable-incomplete",
    }
    assert [kind for kind, _ in store.puts] == [
        "planning-problem",
        "state",
        "planning-output",
    ]


def test_manifest_history_summary_references_saved_report(tmp_path: Path) -> None:
    history_path = tmp_path / "cases" / "case-1" / "history.json"
    history_path.parent.mkdir(parents=True)
    history_path.write_text('{"replay":{"large":"state"}}\n', encoding="utf-8")

    summary = _manifest_history_summary(
        tmp_path,
        {
            "case_id": "case-1",
            "history_report": {
                "branch": "main",
                "event_id": "event-1",
                "verify": {"valid": True},
                "replay": {"large": "state"},
            },
        },
    )

    assert summary["artifact"]["path"] == "cases/case-1/history.json"
    assert summary["artifact"]["sha256"]
    assert summary["branch"] == "main"
    assert summary["event_id"] == "event-1"
    assert summary["verify"] == {"valid": True}
    assert "replay" not in summary
