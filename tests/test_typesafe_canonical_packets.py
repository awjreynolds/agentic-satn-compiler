from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from satn.planning_runtime import PlanningRuntime
from satn.typesafe_planning import (
    ChoiceQuestion,
    NoulQuestion,
    ScoreQuestion,
    TypeSafeClient,
    TypeSafeRequest,
)


def _semantic_inputs(
    *, reverse_connections: bool
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    problem: dict[str, object] = {
        "problem_id": "problem-1",
        "input_fingerprint": "problem-fingerprint",
        "brief": {"brief_ref": "owner-brief", "corridor_policy": "retain"},
        "binding": {"snapshot_sha256": "snapshot-1", "policy_fingerprint": "policy-1"},
        "places": [
            {"place_id": "beta", "name": "Beta", "kind": "settlement"},
            {"place_id": "alpha", "name": "Alpha", "kind": "settlement"},
        ],
        "source_corridors": [
            {"corridor_id": "corridor-b", "evidence_refs": ["evidence-b"]},
            {"corridor_id": "corridor-a", "evidence_refs": ["evidence-a"]},
        ],
        "obligations": [
            {"obligation_id": "connection-2", "kind": "place-connection"},
            {"obligation_id": "connection-1", "kind": "place-connection"},
        ],
        "graph_evidence": {
            "directed_edges": [
                {"directed_edge_id": "edge-b", "source_ref": "evidence-b"},
                {"directed_edge_id": "edge-a", "source_ref": "evidence-a"},
            ]
        },
    }
    state: dict[str, object] = {
        "state_id": "state-1",
        "state_fingerprint": "state-fingerprint",
        "connection_intents": [],
        "selected_alignments": [],
        "departures": [],
        "future_interventions": [],
        "planning_gaps": [],
        "unknown_facts": [],
    }
    connection_values = [
        {
            "connection_id": "connection-2",
            "origin_place_id": "beta",
            "destination_place_id": "alpha",
            "corridor_refs": ["corridor-b", "corridor-a"],
            "evidence_refs": ["evidence-b", "evidence-a"],
            "current_or_future": "current",
        },
        {
            "connection_id": "connection-1",
            "origin_place_id": "alpha",
            "destination_place_id": "beta",
            "corridor_refs": ["corridor-a", "corridor-b"],
            "evidence_refs": ["evidence-a", "evidence-b"],
            "current_or_future": "future",
        },
    ]
    if reverse_connections:
        connection_values.reverse()
    context: dict[str, object] = {
        "question_kind": "connection",
        "connections": OrderedDict(
            (str(item["connection_id"]), item) for item in connection_values
        ),
        "candidates": {
            "candidate-1": {
                "candidate_id": "candidate-1",
                "obligation_id": "connection-1",
                "connection_id": "connection-1",
                "place_refs": ["alpha", "beta"],
                "source_corridor_refs": ["corridor-a", "corridor-b"],
                "evidence_refs": ["evidence-a", "evidence-b"],
                "graph_path": {"directed_edge_ids": ["edge-b", "edge-a"]},
            }
        },
        "scope_refs": ["connection-2", "connection-1"],
        "evidence_refs": ["evidence-b", "evidence-a"],
        "permitted_action_kinds": ["select-alignment", "request-evidence"],
    }
    return problem, state, context


def test_classifier_request_bytes_ignore_history_and_unordered_options(tmp_path: Path) -> None:
    problem, state, context = _semantic_inputs(reverse_connections=False)
    _, _, reversed_context = _semantic_inputs(reverse_connections=True)
    questions = {
        "decision": ChoiceQuestion(
            instructions="Choose a connection.",
            criteria={"connection-1": "Alpha to Beta", "connection-2": "Beta to Alpha"},
        )
    }

    first_runtime = PlanningRuntime(tmp_path / "history-a")
    second_runtime = PlanningRuntime(tmp_path / "history-b")
    first_packet = first_runtime._task_packet(problem, state, questions, "live", context, "main")
    second_packet = second_runtime._task_packet(
        problem, state, questions, "live", reversed_context, "main"
    )
    reordered_problem, reordered_state, reordered_context = _semantic_inputs(
        reverse_connections=False
    )
    reordered_candidate = reordered_context["candidates"]["candidate-1"]
    reordered_candidate["place_refs"] = ["beta", "alpha"]
    reordered_candidate["source_corridor_refs"] = ["corridor-b", "corridor-a"]
    reordered_candidate["evidence_refs"] = ["evidence-b", "evidence-a"]
    third_packet = first_runtime._task_packet(
        reordered_problem, reordered_state, questions, "live", reordered_context, "main"
    )

    first_body = TypeSafeRequest(first_packet, questions, "jev-latest").body()
    second_body = TypeSafeRequest(second_packet, questions, "jev-latest").body()
    third_body = TypeSafeRequest(third_packet, questions, "jev-latest").body()

    assert first_body == second_body
    assert first_body == third_body
    assert "history" not in first_packet
    assert [item["connection_id"] for item in first_packet["connection_options"]] == [
        "connection-1",
        "connection-2",
    ]
    assert first_packet["candidates"][0]["graph_path"]["directed_edge_ids"] == [
        "edge-b",
        "edge-a",
    ]
    assert json.dumps(first_packet, sort_keys=True) == json.dumps(second_packet, sort_keys=True)

    first_runtime.store.create_branch("main")
    second_runtime.store.create_branch("main")
    first_request = first_runtime._request(problem, state, questions, "live", context, "main")
    second_request = second_runtime._request(
        problem, state, questions, "live", reversed_context, "main"
    )
    assert first_request["request_id"] == second_request["request_id"]
    assert first_request["task_packet"] == second_request["task_packet"]
    assert (
        first_request["audit_binding"]["history_ref"]
        != second_request["audit_binding"]["history_ref"]
    )


def test_classifier_request_bytes_normalize_unknown_reference_lists() -> None:
    problem, state, context = _semantic_inputs(reverse_connections=False)
    _, reversed_state, _ = _semantic_inputs(reverse_connections=False)
    state["unknown_facts"] = [
        {
            "request_kind": "request-evidence",
            "claim": "coverage",
            "reason": "missing evidence",
            "evidence_refs": ["a", "z"],
        },
        {
            "request_kind": "request-evidence",
            "claim": "coverage",
            "reason": "missing evidence",
            "evidence_refs": ["m"],
        },
    ]
    reversed_state["unknown_facts"] = [
        {
            "request_kind": "request-evidence",
            "claim": "coverage",
            "reason": "missing evidence",
            "evidence_refs": ["z", "a"],
        },
        {
            "request_kind": "request-evidence",
            "claim": "coverage",
            "reason": "missing evidence",
            "evidence_refs": ["m"],
        },
    ]
    questions = {
        "decision": ChoiceQuestion(
            instructions="Choose a connection.",
            criteria={"connection-1": "Alpha to Beta", "connection-2": "Beta to Alpha"},
        )
    }
    first_packet = PlanningRuntime("history-a")._task_packet(
        problem, state, questions, "live", context, "main"
    )
    second_packet = PlanningRuntime("history-b")._task_packet(
        problem, reversed_state, questions, "live", context, "main"
    )

    assert (
        TypeSafeRequest(first_packet, questions, "jev-latest").body()
        == TypeSafeRequest(second_packet, questions, "jev-latest").body()
    )
    assert [item["evidence_refs"] for item in first_packet["unknowns"]] == [["a", "z"], ["m"]]


def test_answered_result_keeps_compact_typed_answers_and_exchange_binding() -> None:
    response = {
        "model": "jev-1.13.0",
        "answers": {
            "route": {
                "type": "choice",
                "choice": "existing",
                "probabilities": {"new": 0.2, "existing": 0.8},
                "confidence": 0.7,
            },
            "usable": {"type": "noul", "noul": 0.9, "confidence": 0.4},
            "evidence": {
                "type": "score",
                "score": 1,
                "legend": {"0": "missing", "1": "partial", "2": "complete"},
                "probabilities": {"2": 0.2, "0": 0.1, "1": 0.7},
                "confidence": 0.6,
            },
        },
        "usage": {"input_tokens": 12, "output_tokens": 8},
    }

    def transport(_request: object) -> tuple[int, str]:
        return 200, json.dumps(response)

    result = TypeSafeClient(
        provider="typesafe-test",
        model="jev-1.13.0",
        endpoint="https://provider.invalid/v1/systemone",
        transport=transport,
    ).judge(
        {"task_id": "task-1"},
        {
            "route": ChoiceQuestion(
                instructions="Choose a route.",
                criteria={"existing": "current", "new": "future"},
            ),
            "usable": NoulQuestion(instructions="Is a route usable?"),
            "evidence": ScoreQuestion(
                instructions="Rate evidence.",
                criteria=["missing", "partial", "complete"],
            ),
        },
    )

    assert result["answers"] == {
        "route": {
            "type": "choice",
            "choice": "existing",
            "probabilities": {"existing": 0.8, "new": 0.2},
            "confidence": 0.7,
        },
        "usable": {"type": "noul", "noul": 0.9},
        "evidence": {
            "type": "score",
            "score": 1,
            "probabilities": {"0": 0.1, "1": 0.7, "2": 0.2},
            "confidence": 0.6,
        },
    }
    binding = result["binding"]
    assert binding["transformation"] == "typesafe-typed-result/v1"
    assert binding["requested_model"] == "jev-1.13.0"
    assert binding["actual_model"] == "jev-1.13.0"
    assert binding["request_fingerprint"] == result["request_receipt"]["body_sha256"]
    assert binding["exchange_ref"] == {
        "request_body_sha256": result["request_receipt"]["body_sha256"],
        "response_body_sha256": result["response_receipt"]["body_sha256"],
    }
