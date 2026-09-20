from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from bath_saltford_fixture import configured_bath_saltford
from shapely.geometry import shape

from satn.planning_engine import build_planning_problem, initial_proposal
from satn.planning_history import HistoryStaleHeadError, HistoryStore
from satn.planning_routing import (
    CapabilityKind,
    CapabilityRecord,
    DecisionTask,
    StaticCapabilityRouter,
)
from satn.planning_runtime import PlanningRuntime
from satn.sources import snapshot


def _choice_keys(question: object) -> tuple[str, ...]:
    criteria = getattr(question, "criteria", None)
    if isinstance(criteria, dict):
        return tuple(str(key) for key in criteria)
    return ()


def test_run_report_references_problem_and_state_without_inlining_them(tmp_path: Path) -> None:
    runtime = PlanningRuntime(tmp_path / "history")
    runtime.store.create_branch("main")
    problem = {"problem_id": "problem-1", "input_fingerprint": "input-1"}
    state = {"state_id": "state-1", "state_fingerprint": "state-fingerprint-1"}

    result = runtime._result(
        "main",
        "deterministic",
        problem,
        state,
        None,
        {"status": "incomplete"},
        tmp_path / "run",
    )

    payload = json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))
    assert "problem" not in payload
    assert "state" not in payload
    assert isinstance(payload["problem_ref"], str)
    assert isinstance(payload["state_ref"], str)
    assert runtime.store.get(payload["problem_ref"]) == problem
    assert runtime.store.get(payload["state_ref"]) == state
    # The Python result remains the materialized logical API for callers.
    assert result.as_dict()["problem"] == problem
    assert result.as_dict()["state"] == state


def test_typed_choice_changes_state_and_replays_without_provider(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    calls: list[object] = []

    def provider(state: object, questions: object) -> dict[str, object]:
        calls.append(state)
        assert isinstance(questions, dict)
        options = _choice_keys(questions["decision"])
        candidate_id = next(key for key in options if not key.startswith("__"))
        return {
            "status": "answered",
            "provider": "test-provider",
            "model": "test-model",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "decision_class": "agent",
            "answers": {
                "decision": {
                    "type": "choice",
                    "choice": candidate_id,
                    "probabilities": {key: 1.0 if key == candidate_id else 0.0 for key in options},
                    "confidence": 1.0,
                }
            },
            "request": {"state": state, "questions": {"decision": "fixture"}},
            "response": {"selected": candidate_id},
            "request_receipt": {"body_sha256": "request"},
            "response_receipt": {"body_sha256": "response"},
        }

    runtime = PlanningRuntime(tmp_path / "history", provider=provider)
    result = runtime.run(
        config,
        output_root=tmp_path / "run",
        mode="live",
        branch="main",
    )

    assert result.status in {"reviewable-incomplete", "validated"}
    assert calls
    assert result.state["selected_alignments"]
    assert result.history_event_id
    assert result.decision_trace
    assert {item["decision_class"] for item in result.decision_trace} == {"classifier"}
    assert any(item.get("operation_kind") for item in result.decision_trace)
    persisted = json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))
    assert all(
        "response_receipt" not in item and "request_receipt" not in item
        for item in persisted["decision_trace"]
    )
    assert all(isinstance(item.get("receipt_ref"), str) for item in persisted["decision_trace"])
    history_event = HistoryStore(tmp_path / "history").get(result.history_event_id)
    assert history_event["decision_class"] == "classifier"
    assert (tmp_path / "run" / "run.json").is_file()

    replay_calls: list[object] = []

    def should_not_call(*_args: object, **_kwargs: object) -> object:
        replay_calls.append(True)
        raise AssertionError("offline replay dispatched the provider")

    replay_runtime = PlanningRuntime(tmp_path / "history", provider=should_not_call)
    replay = replay_runtime.replay("main")

    assert replay["state"] == result.state
    assert replay["decision_trace"] == list(result.decision_trace)
    assert replay_calls == []
    assert HistoryStore(tmp_path / "history").verify("main")["valid"] is True


def test_fork_replacement_replays_prefix_and_preserves_parent(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)

    def provider(_state: object, questions: object) -> dict[str, object]:
        assert isinstance(questions, dict)
        options = _choice_keys(questions["decision"])
        candidate_id = next(key for key in options if not key.startswith("__"))
        return {
            "status": "answered",
            "provider": "test-provider",
            "model": "test-model",
            "answers": {"decision": {"type": "choice", "choice": candidate_id}},
            "response_receipt": {"body_sha256": "response"},
        }

    root = tmp_path / "history"
    runtime = PlanningRuntime(root, provider=provider)
    result = runtime.run(config, output_root=tmp_path / "run", mode="live")
    store = HistoryStore(root)
    event_id = result.history_event_id
    decision_ids: list[str] = []
    while event_id is not None:
        event = store.get(event_id)
        if isinstance(event, dict) and event.get("event_kind") == "decision":
            decision_ids.append(event_id)
        event_id = event.get("timeline_parent_id") if isinstance(event, dict) else None
    assert decision_ids
    decision_id = decision_ids[-1]
    checkpoint = store.checkpoint(decision_id)
    runtime.fork(checkpoint, "alternative")

    prefix = store.restore(checkpoint)
    prefix_state = prefix["state"]
    assert isinstance(prefix_state, dict)
    candidates = [item for item in result.problem["candidates"] if isinstance(item, dict)]
    original = result.state["selected_alignments"][0]["candidate_id"]
    replacement = next(item for item in candidates if item["candidate_id"] != original)
    alternative = runtime.advance(
        "alternative",
        {
            "kind": "select-alignment",
            "payload": {
                "candidate_id": replacement["candidate_id"],
                "obligation_id": replacement.get("obligation_id"),
            },
        },
    )

    assert (
        alternative.state["selected_alignments"][0]["candidate_id"] == replacement["candidate_id"]
    )
    assert runtime.replay("alternative")["state"] == alternative.state
    assert store.head("main").head_event_id == result.history_event_id
    comparison = runtime.compare("main", "alternative")
    assert comparison["replaced_events"]


def test_unavailable_provider_is_recorded_as_incomplete(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    calls = 0

    def unavailable(_state: object, _questions: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "status": "unavailable",
            "provider": "test-provider",
            "model": "test-model",
            "failure_class": "missing-credential",
            "request": {"redacted": True},
        }

    root = tmp_path / "history"
    result = PlanningRuntime(root, provider=unavailable).run(
        config,
        output_root=tmp_path / "run",
        mode="live",
    )

    assert result.status == "reviewable-incomplete"
    assert result.provider_result["status"] == "unavailable"
    assert result.provider_result["model"] == "test-model"
    assert calls == 1
    assert (tmp_path / "run" / "proposal.json").is_file()
    replay = PlanningRuntime(
        root,
        provider=lambda *_: (_ for _ in ()).throw(AssertionError()),
    ).replay()
    assert replay["state"] == result.state
    assert HistoryStore(root).verify("main")["valid"] is True


@pytest.mark.parametrize("provider_status", ["invalid", "servicefailed"])
def test_provider_failure_output_refreshes_fingerprint_before_publication(
    tmp_path: Path, provider_status: str
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)

    def failed_provider(_packet: object, _questions: object) -> dict[str, object]:
        return {
            "status": provider_status,
            "provider": "test-provider",
            "model": "test-model",
            "failure_class": "test-provider-failure",
            "response_receipt": {"body_sha256": "response"},
        }

    result = PlanningRuntime(tmp_path / "history", provider=failed_provider).run(
        config,
        output_root=tmp_path / "run",
        mode="live",
    )

    assert result.status == "reviewable-incomplete"
    assert result.provider_result["status"] == provider_status
    assert result.output["provider_status"] == provider_status
    assert result.publication is not None
    assert "error" not in result.publication


def test_deterministic_mode_marks_unknown_without_selecting_candidate(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    root = tmp_path / "history"

    result = PlanningRuntime(root).run(
        config,
        output_root=tmp_path / "run",
        mode="deterministic",
    )

    assert result.mode == "deterministic"
    assert result.status == "reviewable-incomplete"
    assert not result.state["selected_alignments"]
    assert result.state["unknown_facts"]
    assert result.decision_trace
    assert {item["decision_class"] for item in result.decision_trace} == {"mechanical"}
    assert (tmp_path / "run" / "run.json").read_text(encoding="utf-8").find(
        '"mode": "deterministic"'
    ) >= 0


def test_requested_connection_expands_once_and_replays_recorded_receipt(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    root = tmp_path / "history"
    result = PlanningRuntime(root).run(
        config,
        output_root=tmp_path / "run",
        mode="deterministic",
        requested_connections=[
            {
                "connection_id": "bath-edge-to-saltford",
                "origin_place_id": "bath-edge",
                "destination_place_id": "saltford",
                "corridor_refs": [problem["source_corridors"][0]["corridor_id"]],
                "current_or_future": "future",
            }
        ],
    )

    assert result.problem["problem_id"] != problem["problem_id"]
    assert any(
        item.get("connection_id") == "bath-edge-to-saltford"
        for item in result.problem["candidates"]
    )
    replay = PlanningRuntime(root).replay("main")
    assert replay["state"] == result.state
    assert replay["problem"] == result.problem

    store = HistoryStore(root)
    event_id = result.history_event_id
    operation_records: list[dict[str, object]] = []
    while isinstance(event_id, str):
        event = store.get(event_id)
        if isinstance(event, dict) and isinstance(event.get("operation_ref"), str):
            operation = store.get(event["operation_ref"])
            if isinstance(operation, dict):
                operation_records.append(operation)
        event_id = event.get("timeline_parent_id") if isinstance(event, dict) else None
    assert operation_records
    assert any(operation.get("kind") == "initialize" for operation in operation_records)
    assert all("state" not in operation for operation in operation_records)
    expansion = [item for item in operation_records if item.get("kind") == "expand-connection"]
    assert expansion
    assert all("problem" not in operation for operation in expansion)
    assert all(isinstance(operation.get("problem_ref"), str) for operation in expansion)


def test_fork_after_connection_expansion_replays_bound_child_problem(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    root = tmp_path / "history"
    result = PlanningRuntime(root).run(
        config,
        output_root=tmp_path / "run",
        mode="deterministic",
        requested_connections=[
            {
                "connection_id": "bath-edge-to-saltford",
                "origin_place_id": "bath-edge",
                "destination_place_id": "saltford",
                "corridor_refs": [problem["source_corridors"][0]["corridor_id"]],
                "current_or_future": "future",
            }
        ],
    )
    runtime = PlanningRuntime(root)
    checkpoint = runtime.store.checkpoint(result.history_event_id)
    runtime.fork(checkpoint, "alternative")

    replay = runtime.replay("alternative")

    assert replay["output"]["status"] != "invalid"
    assert replay["state"]["parent_problem_id"] == replay["problem"]["problem_id"]


def test_supplied_brief_and_policy_are_bound_before_problem_identity(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    default_problem = build_planning_problem(config)
    supplied_brief = {
        "brief_ref": "custom-owner-brief",
        "corridor_policy": default_problem["brief"]["corridor_policy"],
    }
    supplied_policy = {"policy_ref": "owner-policy"}

    result = PlanningRuntime(
        tmp_path / "history",
        brief=supplied_brief,
        policy=supplied_policy,
    ).run(config, output_root=tmp_path / "run")

    assert result.problem["brief"] == supplied_brief
    assert result.problem["policy"] == supplied_policy
    assert result.output["status"] != "invalid"


def test_specialist_proposal_must_select_an_offered_candidate(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    offered = {str(item["candidate_id"]) for item in problem["candidates"][:1]}
    outside = next(
        item for item in problem["candidates"] if str(item["candidate_id"]) not in offered
    )

    def specialist(_task: DecisionTask) -> dict[str, object]:
        return {
            "provider": "configured-specialist",
            "proposal": {
                "operation": {
                    "kind": "select-alignment",
                    "payload": {
                        "candidate_id": outside["candidate_id"],
                        "obligation_id": outside["obligation_id"],
                    },
                }
            },
            "response_receipt": {"body_sha256": "specialist-response"},
        }

    router = StaticCapabilityRouter(
        (
            CapabilityRecord(
                capability_id="configured-specialist",
                kind=CapabilityKind.SPECIALIST,
                operations=("select-alignment",),
                judgment_forms=("structured-proposal",),
                operation_scopes=("select-alignment",),
                provider="test-specialist",
                adapter=specialist,
            ),
        )
    )
    result = PlanningRuntime(tmp_path / "history", router=router).run(
        config,
        output_root=tmp_path / "run",
        mode="live",
    )

    assert result.termination_reason == "invalid-provider-choice"
    assert not result.state["selected_alignments"]
    assert result.provider_result["status"] == "answered"


def test_unresolved_jev_judgment_escalates_to_configured_specialist(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    calls = {"jev": 0, "specialist": 0}

    def jev(_task: DecisionTask) -> dict[str, object]:
        calls["jev"] += 1
        return {
            "status": "answered",
            "answers": {
                "decision": {
                    "type": "choice",
                    "choice": "__unknown__",
                }
            },
            "response_receipt": {"body_sha256": "jev-response"},
        }

    def specialist(_task: DecisionTask) -> dict[str, object]:
        calls["specialist"] += 1
        return {
            "status": "unknown",
            "provider": "configured-specialist",
            "model": "specialist-model",
            "cause": "specialist-evidence-unavailable",
            "response_receipt": {"body_sha256": "specialist-response"},
        }

    router = StaticCapabilityRouter(
        (
            CapabilityRecord(
                capability_id="jev",
                kind=CapabilityKind.JEV,
                judgment_forms=("choice",),
                provider="configured-jev",
                adapter=jev,
            ),
            CapabilityRecord(
                capability_id="specialist",
                kind=CapabilityKind.SPECIALIST,
                judgment_forms=("structured-proposal",),
                provider="configured-specialist",
                adapter=specialist,
            ),
        )
    )
    result = PlanningRuntime(tmp_path / "history", router=router).run(
        config,
        output_root=tmp_path / "run",
        mode="live",
    )

    assert calls == {"jev": 1, "specialist": 1}
    assert result.provider_result["status"] == "unknown"
    assert result.provider_result["provider"] == "configured-specialist"
    assert result.provider_result["model"] == "specialist-model"
    assert result.provider_result["response_receipt"] == {"body_sha256": "specialist-response"}
    assert result.decision_trace[-1]["decision_class"] == "agent"
    assert result.decision_trace[-1]["provider"] == "configured-specialist"
    assert result.decision_trace[-1]["model"] == "specialist-model"
    assert result.decision_trace[-1]["response_receipt"] == {"body_sha256": "specialist-response"}
    history_store = HistoryStore(tmp_path / "history")
    history_event = history_store.get(result.history_event_id)
    receipt = history_store.get(history_event["receipt_ref"])
    assert isinstance(receipt, dict)
    assert receipt["provider"] == "configured-specialist"


def test_requested_connection_options_schedule_each_intent_before_remaining_work(
    tmp_path: Path,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    seen: list[str] = []
    connection_choices = iter(("one", "two"))

    def provider(packet: object, questions: object) -> dict[str, object]:
        assert isinstance(packet, dict)
        assert isinstance(questions, dict)
        question = questions["decision"]
        options = _choice_keys(question)
        question_kind = str(packet["question_kind"])
        seen.append(question_kind)
        if question_kind == "connection":
            choice = next(connection_choices)
        else:
            choice = next(key for key in options if not key.startswith("__"))
        return {
            "status": "answered",
            "provider": "test-provider",
            "model": "test-model",
            "answers": {"decision": {"type": "choice", "choice": choice}},
            "response_receipt": {"body_sha256": f"response-{len(seen)}"},
        }

    result = PlanningRuntime(tmp_path / "history", provider=provider).run(
        config,
        output_root=tmp_path / "run",
        mode="live",
        connection_options=[
            {
                "connection_id": connection_id,
                "origin_place_id": "bath-edge",
                "destination_place_id": "saltford",
                "current_or_future": "future",
            }
            for connection_id in ("one", "two")
        ],
    )

    assert seen[:4] == ["connection", "alignment", "connection", "alignment"]
    assert [item["connection_id"] for item in result.state["connection_intents"]] == [
        "one",
        "two",
    ]


def test_feedback_unknown_is_visible_in_the_next_task_packet(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    seen_unknowns: list[int] = []

    def provider(packet: object, _questions: object) -> dict[str, object]:
        assert isinstance(packet, dict)
        seen_unknowns.append(len(packet["unknowns"]))
        return {
            "status": "answered",
            "provider": "test-provider",
            "model": "test-model",
            "answers": {
                "decision": {
                    "type": "choice",
                    "choice": "__needs_evidence__",
                }
            },
            "response_receipt": {"body_sha256": f"response-{len(seen_unknowns)}"},
        }

    result = PlanningRuntime(tmp_path / "history", provider=provider).run(
        config,
        output_root=tmp_path / "run",
        mode="live",
    )

    assert seen_unknowns[:2] == [0, 1]
    assert result.state["unknown_facts"]


def test_connection_feedback_packet_retains_unscoped_request_and_stops_no_progress(
    tmp_path: Path,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    unrelated_ref = str(problem["candidates"][0]["obligation_id"])
    packets: list[dict[str, object]] = []

    def provider(packet: object, _questions: object) -> dict[str, object]:
        assert isinstance(packet, dict)
        packets.append(packet)
        return {
            "status": "answered",
            "provider": "test-provider",
            "model": "test-model",
            "answers": {
                "decision": {
                    "type": "choice",
                    "choice": "__needs_evidence__",
                }
            },
            "response_receipt": {"body_sha256": f"response-{len(packets)}"},
        }

    result = PlanningRuntime(tmp_path / "history", provider=provider).run(
        config,
        output_root=tmp_path / "run",
        mode="live",
        operations=[
            {
                "kind": "request-evidence",
                "payload": {
                    "target_refs": [unrelated_ref],
                    "claim": "unrelated-scoped-request",
                    "reason": "must stay outside this connection task",
                },
            }
        ],
        connection_options=[
            {
                "connection_id": "focused-connection",
                "origin_place_id": "bath-edge",
                "destination_place_id": "saltford",
                "current_or_future": "future",
            }
        ],
    )

    assert result.termination_reason == "semantic-no-progress"
    assert len(packets) == 2
    prior_requests = [
        item
        for item in packets[1]["feedback_unknowns"]
        if isinstance(item, dict)
        and item.get("request_kind")
        in {
            "request-evidence",
            "request-candidates",
        }
    ]
    assert len(prior_requests) == 1
    assert prior_requests[0]["claim"] == "planning-decision"
    assert prior_requests[0]["reason"] == "provider marked the planning choice unresolved"
    assert any(
        isinstance(item, dict)
        and item.get("request_kind") == "request-evidence"
        and item.get("unknown_id") == prior_requests[0]["unknown_id"]
        for item in packets[1]["unknowns"]
    )
    assert all(
        not isinstance(item, dict) or unrelated_ref not in item.get("subject_refs", [])
        for item in packets[1]["unknowns"]
    )
    assert all(
        not isinstance(item, dict) or item.get("claim") != "routing-graph attachment"
        for item in packets[1]["unknowns"]
    )


def test_unknown_provision_choice_requests_named_evidence_and_replays_without_provider(
    tmp_path: Path,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    packets: list[dict[str, object]] = []

    def provider(packet: object, questions: object) -> dict[str, object]:
        assert isinstance(packet, dict)
        assert isinstance(questions, dict)
        packets.append(packet)
        options = _choice_keys(questions["decision"])
        if len(packets) > 1:
            assert "current-future-provision" in options
            assert any(option.startswith("planning-candidate-") for option in options)
        choice = (
            "current-future-provision"
            if "current-future-provision" in options
            else "bath-edge-to-saltford"
        )
        return {
            "status": "answered",
            "provider": "test-provider",
            "model": "test-model",
            "answers": {
                "decision": {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": {key: 1.0 if key == choice else 0.0 for key in options},
                    "confidence": 1.0,
                }
            },
            "response_receipt": {"body_sha256": f"response-{len(packets)}"},
        }

    root = tmp_path / "history"
    result = PlanningRuntime(root, provider=provider).run(
        config,
        output_root=tmp_path / "run",
        mode="live",
        connection_options=[
            {
                "connection_id": "bath-edge-to-saltford",
                "origin_place_id": "bath-edge",
                "destination_place_id": "saltford",
                "current_or_future": "unknown",
            }
        ],
    )

    assert result.termination_reason == "semantic-no-progress"
    assert len(packets) == 3
    claim = "whether each proposed alignment is current provision or future intervention"
    investigation = (
        "Bind route-section current provision, cycling access and continuity, "
        "or explicit future-intervention evidence; proposal intent does not establish "
        "provision or intervention state."
    )
    feedback = [
        item
        for item in packets[2]["feedback_unknowns"]
        if isinstance(item, dict) and item.get("claim") == claim
    ]
    assert len(feedback) == 1
    assert feedback[0]["reason"] == investigation
    assert feedback[0]["request_kind"] == "request-evidence"
    assert feedback[0]["subject_refs"]

    event_id = result.history_event_id
    store = HistoryStore(root)
    named_request = None
    while event_id is not None:
        event = store.get(event_id)
        if isinstance(event, dict) and event.get("event_kind") == "decision":
            operation = event.get("operation")
            if not isinstance(operation, dict) and isinstance(event.get("operation_ref"), str):
                operation = store.get(event["operation_ref"])
            payload = operation.get("payload") if isinstance(operation, dict) else None
            if isinstance(payload, dict) and payload.get("claim") == claim:
                named_request = payload
                request_ref = event.get("request_ref")
                assert isinstance(request_ref, str)
                request = store.get(request_ref)
                assert "current-future-provision" in request["offered_consideration_refs"]
                break
        event_id = event.get("timeline_parent_id") if isinstance(event, dict) else None
    assert named_request is not None
    assert named_request["reason"] == investigation

    replay = PlanningRuntime(
        root,
        provider=lambda *_: (_ for _ in ()).throw(AssertionError("replay dispatched provider")),
    ).replay()
    assert replay["state"] == result.state
    assert HistoryStore(root).verify("main")["valid"] is True


def test_investigate_bound_request_retains_judgment_for_next_task_and_replay(
    tmp_path: Path,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    packets: list[dict[str, object]] = []

    def provider(packet: object, questions: object) -> dict[str, object]:
        assert isinstance(packet, dict)
        assert isinstance(questions, dict)
        packets.append(packet)
        options = _choice_keys(questions["decision"])
        choice = "supports" if len(packets) == 1 else "__needs_evidence__"
        return {
            "status": "answered",
            "provider": "test-provider",
            "model": "test-model",
            "usage": {"input_tokens": 7, "output_tokens": 3},
            "answers": {
                "decision": {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": {key: 0.8 if key == choice else 0.1 for key in options},
                    "confidence": 0.8,
                }
            },
            "request_receipt": {"body_sha256": f"request-{len(packets)}"},
            "response_receipt": {"body_sha256": f"response-{len(packets)}"},
        }

    root = tmp_path / "history"
    runtime = PlanningRuntime(root, provider=provider)
    problem = build_planning_problem(config)
    candidate = next(item for item in problem["candidates"] if isinstance(item, dict))
    candidate_provision_status = candidate["current_or_future"]
    corridor_id = candidate["source_corridor_refs"][0]
    section_id = next(
        item["section_id"]
        for item in problem["source_corridors"]
        if item["corridor_id"] == corridor_id
    )
    directed_edge_id = candidate["graph_path"]["directed_edge_ids"][0]
    initial = runtime.run(
        config,
        output_root=tmp_path / "initial-run",
        mode="deterministic",
        operations=[
            {
                "kind": "request-evidence",
                "payload": {
                    "request_id": "route-request-1",
                    "target_refs": [candidate["candidate_id"]],
                    "claim": "route-section-context",
                    "reason": "source-backed route investigation",
                },
            }
        ],
    )
    head_before_investigation = HistoryStore(root).head("main").head_event_id

    evidence = {
        "evidence_id": "official-route-context",
        "source": {
            "source_id": "official-route-context",
            "url": "https://example.test/official-route",
            "title": "Official route context",
            "locator": "§1.2",
            "published_at": None,
            "retrieved_at": "2026-09-20",
            "excerpt": "The project aims to improve travel along the named corridor.",
        },
        "scope": {
            "candidate_id": candidate["candidate_id"],
            "source_corridor_refs": [corridor_id],
            "section_refs": [section_id],
            "directed_edge_ids": [directed_edge_id],
        },
    }
    investigated = runtime.investigate_evidence(
        "main",
        "route-request-1",
        evidence,
        output_root=tmp_path / "investigated-run",
    )

    evidence_packet = packets[0]
    assert evidence_packet["question_kind"] == "evidence-relation"
    assert evidence_packet["claim"] == "route-section-context"
    assert "brief" not in evidence_packet
    assert "candidates" not in evidence_packet
    assert evidence_packet["source_evidence"]["source"] == {
        key: value for key, value in evidence["source"].items() if key != "source_id"
    }
    assert "scope" not in evidence_packet["source_evidence"]
    assert "evidence_id" not in evidence_packet["source_evidence"]
    packet_text = json.dumps(evidence_packet, sort_keys=True)
    for local_id in (
        candidate["candidate_id"],
        corridor_id,
        section_id,
        directed_edge_id,
        evidence["evidence_id"],
    ):
        assert local_id not in packet_text

    assert initial.state["unknown_facts"]
    assert investigated.state["unknown_facts"]
    retained = next(
        item
        for item in investigated.state["unknown_facts"]
        if item["unknown_id"] == "route-request-1"
    )
    assert len(retained["evidence_judgments"]) == 1
    judgment = retained["evidence_judgments"][0]
    assert judgment["evidence_id"] == "official-route-context"
    assert judgment["claim"] == "route-section-context"
    assert judgment["relation"] == "supports"
    assert judgment["probabilities"] == {
        "supports": 0.8,
        "contradicts": 0.1,
        "does_not_establish": 0.1,
    }
    assert judgment["confidence"] == 0.8
    assert judgment["source"] == evidence["source"]
    assert judgment["scope"] == evidence["scope"]
    assert candidate["current_or_future"] == candidate_provision_status
    retained_candidate = next(
        item
        for item in investigated.state["candidates"]
        if item["candidate_id"] == candidate["candidate_id"]
    )
    assert retained_candidate["current_or_future"] == candidate_provision_status
    assert any(item.get("decision_class") == "classifier" for item in investigated.decision_trace)

    resumed = runtime.run(
        config,
        output_root=tmp_path / "resumed-run",
        mode="live",
    )
    feedback = next(
        item
        for item in packets[-1].get("feedback_unknowns", [])
        if isinstance(item, dict) and item.get("unknown_id") == "route-request-1"
    )
    assert feedback["evidence_judgments"][0]["claim"] == "route-section-context"
    assert feedback["evidence_judgments"][0]["source"] == evidence["source"]
    assert feedback["evidence_judgments"][0]["scope"] == evidence["scope"]
    resumed_candidate = next(
        item
        for item in resumed.state["candidates"]
        if item["candidate_id"] == candidate["candidate_id"]
    )
    assert resumed_candidate["current_or_future"] == candidate_provision_status

    replay_calls: list[object] = []
    replay = PlanningRuntime(
        root,
        provider=lambda *_args: replay_calls.append(True),
    ).replay("main")
    assert replay["state"] == resumed.state
    assert replay_calls == []

    head_before_invalid = HistoryStore(root).head("main").head_event_id
    packet_count_before_invalid = len(packets)
    foreign = dict(evidence)
    foreign["scope"] = {**evidence["scope"], "directed_edge_ids": ["foreign-edge"]}
    with pytest.raises(ValueError, match="directed edge"):
        runtime.investigate_evidence("main", "route-request-1", foreign)
    assert HistoryStore(root).head("main").head_event_id == head_before_invalid
    assert len(packets) == packet_count_before_invalid
    assert head_before_investigation != head_before_invalid


def test_graph_candidate_scope_uses_admitted_corridor_topology(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    candidate = next(item for item in problem["candidates"] if isinstance(item, dict))
    corridor_id = candidate["source_corridor_refs"][0]
    section_id = next(
        item["section_id"]
        for item in problem["source_corridors"]
        if item["corridor_id"] == corridor_id
    )
    edge_id = candidate["graph_path"]["directed_edge_ids"][0]
    graph_candidate_problem = copy.deepcopy(problem)
    graph_candidate = next(
        item
        for item in graph_candidate_problem["candidates"]
        if item["candidate_id"] == candidate["candidate_id"]
    )
    graph_candidate["source_corridor_refs"] = []

    admitted = PlanningRuntime._validate_evidence_scope(
        graph_candidate_problem,
        {"subject_refs": []},
        {
            "evidence_id": "topology-bound-source",
            "source": {
                "url": "https://example.test/source",
                "title": "Source context",
                "locator": "§1",
                "retrieved_at": "2026-09-20",
                "excerpt": "The source describes the named corridor.",
            },
            "scope": {
                "candidate_id": candidate["candidate_id"],
                "source_corridor_refs": [corridor_id],
                "section_refs": [section_id],
                "directed_edge_ids": [edge_id],
            },
        },
    )

    assert admitted["scope"]["source_corridor_refs"] == [corridor_id]
    assert admitted["scope"]["section_refs"] == [section_id]
    assert admitted["scope"]["directed_edge_ids"] == [edge_id]


def test_investigate_evidence_rejects_touching_only_unbound_candidate_scope(
    tmp_path: Path,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    calls: list[object] = []

    def provider(*_args: object) -> dict[str, object]:
        calls.append(True)
        return {"status": "answered"}

    root = tmp_path / "history"
    runtime = PlanningRuntime(root, provider=provider)
    connection = {
        "connection_id": "bath-saltford-townpair",
        "origin_place_id": "bath-edge",
        "destination_place_id": "saltford",
        "current_or_future": "unknown",
        "corridor_refs": [],
    }
    expanded = runtime.run(
        config,
        output_root=tmp_path / "run",
        mode="deterministic",
        requested_connections=[connection],
    )
    candidate = next(
        item
        for item in expanded.problem["candidates"]
        if item.get("connection_id") == connection["connection_id"] and item.get("role") == "direct"
    )
    edge_id = candidate["graph_path"]["directed_edge_ids"][0]
    corridor = next(
        item
        for item in expanded.problem["source_corridors"]
        if item.get("classification") == "cycleway"
        and edge_id in item["topology_fact"]["directed_edge_ids"]
    )
    graph_edge = next(
        item
        for item in expanded.problem["graph_evidence"]["directed_edges"]
        if item["directed_edge_id"] == edge_id
    )
    assert corridor["geometry_ref"]["crs"] == graph_edge["geometry_ref"]["crs"]
    assert (
        shape(corridor["geometry_ref"]["geometry"])
        .intersection(shape(graph_edge["geometry_ref"]["geometry"]))
        .length
        == 0
    )
    assert candidate["source_corridor_refs"] == []

    request = runtime.advance(
        "main",
        {
            "kind": "request-evidence",
            "payload": {
                "request_id": "touching-only-source-request",
                "target_refs": [candidate["candidate_id"]],
                "claim": "source corridor supports this town-pair candidate",
                "reason": "scope binding test",
            },
        },
    )
    head_before = HistoryStore(root).head("main").head_event_id
    evidence = {
        "evidence_id": "touching-only-source",
        "source": {
            "url": "https://example.test/source",
            "title": "Source corridor",
            "locator": "§1",
            "retrieved_at": "2026-09-20",
            "excerpt": "The source describes the named corridor.",
        },
        "scope": {
            "candidate_id": candidate["candidate_id"],
            "source_corridor_refs": [corridor["corridor_id"]],
            "section_refs": [corridor["section_id"]],
            "directed_edge_ids": [edge_id],
        },
    }

    with pytest.raises(ValueError, match="positive geometry intersection"):
        runtime.investigate_evidence(
            "main",
            "touching-only-source-request",
            evidence,
        )

    assert HistoryStore(root).head("main").head_event_id == head_before
    assert calls == []
    assert request.state["candidates"] == expanded.state["candidates"]


def test_investigate_evidence_accepts_a367_source_geometry_at_graph_precision(
    tmp_path: Path,
) -> None:
    """The pinned A367 segment binds despite source coordinates retaining extra precision."""

    def digest(value: object) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()

    candidate_id = (
        "planning-candidate-c571776bc49b3dc419d23732b4986bd60c540cd76da862fd35f4f036dc09ab4a"
    )
    corridor_id = (
        "planning-corridor-10936e578da7c3ee8d0cf03f79dabfb2879bd17e74db4d40d6cc9ae6c4d0af22"
    )
    section_id = f"{corridor_id}-section"
    edge_id = "203490192#512caf4c2171dd14027a"
    source_ref = {"evidence_id": "a-road-spine-5e0d8bc4d76c", "source_id": "203490192"}
    source_geometry_ref = {
        "crs": "EPSG:4326",
        "geometry": {
            "type": "LineString",
            "coordinates": [
                [-2.454191888811317, 51.29062609520518],
                [-2.45339368881075, 51.29081879520533],
            ],
        },
    }
    graph_edge_geometry_ref = {
        "crs": "EPSG:4326",
        "geometry": {
            "type": "LineString",
            "coordinates": [
                [-2.4533937, 51.2908188],
                [-2.4541919, 51.2906261],
            ],
        },
    }
    corridor = {
        "corridor_id": corridor_id,
        "section_id": section_id,
        "source_refs": [source_ref],
        "name": "A367",
        "geometry_ref": source_geometry_ref,
        "classification": "a-road",
        "scope_status": "network",
        "in_scope": True,
        "current_cycle_asset": None,
        "provision_status": "unknown",
        "mandatory_planning_corridor": False,
        "topology_fact": {
            "status": "attached",
            "node_ids": ["2678501462", "1444761739"],
            "directed_edge_ids": [edge_id],
            "source_edge_ids": ["203490192"],
        },
        "evidence_refs": [source_ref["evidence_id"]],
        "provenance": {
            "source_kind": "network",
            "source_refs": [source_ref],
            "evidence_refs": [source_ref["evidence_id"]],
            "source_hash": "a367-source-hash",
        },
        "departure_disposition": "not-assessed",
        "decision_ref": None,
        "status": "admitted",
    }
    candidate = {
        "candidate_id": candidate_id,
        "obligation_id": corridor_id,
        "source_corridor_refs": [],
        "role": "direct",
        "status": "admitted",
        "current_or_future": "unknown",
        "endpoint_provenance": {
            "start_node_id": "2678501462",
            "end_node_id": "1444761739",
            "source_geometry_ref": source_geometry_ref,
            "source_edge_ids": ["203490192"],
            "directed_edge_ids": [edge_id],
        },
        "graph_path": {
            "source_edge_ids": ["203490192"],
            "directed_edge_ids": [edge_id],
            "length_km": 0.1,
        },
        "geometry_ref": graph_edge_geometry_ref,
        "provenance": {
            "source_refs": [source_ref],
            "evidence_refs": [source_ref["evidence_id"]],
            "source_corridor_ref": corridor_id,
            "source_geometry_ref": source_geometry_ref,
        },
    }
    brief = {"brief_ref": "test/a367-precision", "corridor_policy": {}}
    problem = {
        "schema_version": "planning-problem/v1",
        "status": "admitted-with-unknowns",
        "binding": {"snapshot_id": "synthetic-a367"},
        "brief": brief,
        "brief_fingerprint": digest(brief),
        "places": [],
        "obligations": [],
        "source_corridors": [corridor],
        "candidates": [candidate],
        "graph_evidence": {
            "directed_edges": [
                {
                    "directed_edge_id": edge_id,
                    "from_node_id": "2678501462",
                    "to_node_id": "1444761739",
                    "geometry_ref": graph_edge_geometry_ref,
                }
            ]
        },
        "planning_gaps": [],
        "unknown_facts": [],
        "exclusions": [],
        "source_hashes": ["a367-source-hash"],
    }
    problem["input_fingerprint"] = digest(problem)
    problem["problem_id"] = f"planning-problem-{digest(problem['input_fingerprint'])}"
    state = initial_proposal(problem)

    root = tmp_path / "history"
    store = HistoryStore(root)
    store.create_branch("main")
    problem_ref = store.put(problem, kind="planning-problem")
    state_ref = store.put(state, kind="state")
    envelope_ref = store.put(
        {
            "schema_version": "planning-run/v1",
            "mode": "live",
            "problem_ref": problem_ref,
            "problem_fingerprint": problem["input_fingerprint"],
            "binding": problem["binding"],
            "brief": brief,
            "policy": {},
            "connection_options": [],
        },
        kind="planning-run",
    )
    store.commit(
        "main",
        None,
        {
            "event_kind": "initialization",
            "actor_kind": "code",
            "outcome": "accepted",
            "state_transition": True,
            "output_state": state,
            "operation": {"kind": "initialize", "state_ref": state_ref},
            "problem_ref": problem_ref,
            "run_envelope_ref": envelope_ref,
            "dependency_refs": [problem_ref, envelope_ref],
        },
    )
    calls: list[dict[str, object]] = []

    def provider(packet: object, questions: object) -> dict[str, object]:
        assert isinstance(packet, dict)
        assert isinstance(questions, dict)
        calls.append(packet)
        return {
            "status": "answered",
            "provider": "test-provider",
            "model": "test-model",
            "answers": {
                "decision": {
                    "type": "choice",
                    "choice": "does_not_establish",
                    "probabilities": {
                        "supports": 0.1,
                        "contradicts": 0.1,
                        "does_not_establish": 0.8,
                    },
                    "confidence": 0.8,
                }
            },
            "response_receipt": {"body_sha256": "a367-response"},
        }

    runtime = PlanningRuntime(root, provider=provider)
    runtime.advance(
        "main",
        {
            "kind": "request-evidence",
            "payload": {
                "request_id": "a367-source-request",
                "target_refs": [candidate_id],
                "claim": "A367 source context",
                "reason": "precision binding test",
            },
        },
    )
    head_before = store.head("main").head_event_id
    result = runtime.investigate_evidence(
        "main",
        "a367-source-request",
        {
            "evidence_id": "a367-source-evidence",
            "source": {
                "url": "https://example.test/a367",
                "title": "A367 source",
                "locator": "§1",
                "retrieved_at": "2026-09-20",
                "excerpt": "The source describes the A367 corridor.",
            },
            "scope": {
                "candidate_id": candidate_id,
                "source_corridor_refs": [corridor_id],
                "section_refs": [section_id],
                "directed_edge_ids": [edge_id],
            },
        },
    )

    assert len(calls) == 1
    assert result.state is not None
    assert result.state["unknown_facts"][0]["evidence_judgments"][0]["relation"] == (
        "does_not_establish"
    )
    assert result.problem["source_corridors"][0]["geometry_ref"] == source_geometry_ref
    assert result.problem["graph_evidence"]["directed_edges"][0]["geometry_ref"] == (
        graph_edge_geometry_ref
    )
    assert result.state["candidates"][0]["current_or_future"] == "unknown"
    assert HistoryStore(root).head("main").head_event_id != head_before


def test_advance_rejects_stale_expected_head_without_mutating_branch(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    root = tmp_path / "history"
    result = PlanningRuntime(root).run(
        config,
        output_root=tmp_path / "run",
        mode="deterministic",
    )
    candidate = next(item for item in result.problem["candidates"] if isinstance(item, dict))
    store = HistoryStore(root)
    head = store.head("main").head_event_id

    with pytest.raises(HistoryStaleHeadError):
        PlanningRuntime(root).advance(
            "main",
            {
                "kind": "select-alignment",
                "payload": {
                    "candidate_id": candidate["candidate_id"],
                    "obligation_id": candidate.get("obligation_id"),
                },
            },
            expected_head="0" * 64,
        )

    assert store.head("main").head_event_id == head


def test_live_connection_choice_expands_then_scopes_alignment_choice(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    seen_questions: list[dict[str, object]] = []
    seen_packets: list[dict[str, object]] = []
    choices = iter(("bath-edge-to-saltford", None))

    def provider(packet: object, questions: object) -> dict[str, object]:
        assert isinstance(packet, dict)
        seen_packets.append(packet)
        assert isinstance(questions, dict)
        question = questions["decision"]
        options = _choice_keys(question)
        seen_questions.append({"options": options, "question": question})
        requested = next(choices)
        choice = requested or next(key for key in options if not key.startswith("__"))
        return {
            "status": "answered",
            "provider": "test-provider",
            "model": "test-model",
            "answers": {"decision": {"type": "choice", "choice": choice}},
            "response_receipt": {"body_sha256": f"response-{len(seen_questions)}"},
        }

    result = PlanningRuntime(tmp_path / "history", provider=provider).run(
        config,
        output_root=tmp_path / "run",
        mode="live",
        connection_options=[
            {
                "connection_id": "bath-edge-to-saltford",
                "origin_place_id": "bath-edge",
                "destination_place_id": "saltford",
                "current_or_future": "future",
            }
        ],
    )

    assert result.state["connection_intents"][0]["connection_id"] == "bath-edge-to-saltford"
    assert result.state["selected_alignments"]
    assert len(seen_questions) >= 2
    assert "bath-edge-to-saltford" in seen_questions[0]["options"]
    second_options = seen_questions[1]["options"]
    assert second_options
    assert len(second_options) < len(result.problem["candidates"])
    assert "current-future-provision" not in second_options
    assert all(
        option.startswith("planning-candidate-") or option.startswith("__")
        for option in second_options
    )
    store = HistoryStore(tmp_path / "history")
    request_packets: list[dict[str, object]] = []
    event_id = result.history_event_id
    while event_id is not None:
        event = store.get(event_id)
        if isinstance(event, dict) and event.get("event_kind") == "attempt":
            request = store.get(event["request_ref"])
            if isinstance(request, dict):
                request_packets.append(request)
        event_id = event.get("timeline_parent_id") if isinstance(event, dict) else None
    request_packets.reverse()
    assert request_packets[0]["question_kind"] == "connection"
    assert request_packets[1]["question_kind"] == "alignment"
    assert len(request_packets[1]["candidate_refs"]) < len(result.problem["candidates"])

    assert len(seen_packets) == len(request_packets)
    alignment_packet = seen_packets[1]
    assert alignment_packet["schema_version"] == "planning-task-packet/v1"
    assert "selected_alignments" not in alignment_packet
    assert alignment_packet["input_binding"] == result.problem["binding"]
    assert alignment_packet["named_endpoints"]
    assert alignment_packet["brief"] == result.problem["brief"]
    packet_candidate_ids = {
        str(item["candidate_id"])
        for item in alignment_packet["candidates"]
        if isinstance(item, dict) and item.get("candidate_id")
    }
    offered_candidate_ids = {option for option in second_options if not option.startswith("__")}
    unrelated_candidate_ids = {
        str(item["candidate_id"])
        for item in result.problem["candidates"]
        if isinstance(item, dict)
        and item.get("candidate_id")
        and item.get("connection_id") != "bath-edge-to-saltford"
    }
    assert packet_candidate_ids == offered_candidate_ids
    assert packet_candidate_ids.isdisjoint(unrelated_candidate_ids)
    assert all(
        "graph_path" not in str(value) for value in seen_questions[1]["question"].criteria.values()
    )

    store = HistoryStore(tmp_path / "history")
    for packet, request in zip(seen_packets, request_packets, strict=True):
        assert request["task_packet"] == packet
        packet_ref = request["task_packet_ref"]
        assert isinstance(packet_ref, str)
        assert store.get(packet_ref) == packet


def test_configured_specialist_proposal_is_engine_validated_and_replayed(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    calls: list[object] = []

    def specialist(task: DecisionTask) -> dict[str, object]:
        calls.append(task)
        candidates = task.candidates
        candidate = next(item for item in candidates if isinstance(item, dict))
        return {
            "provider": "configured-specialist",
            "model": "specialist-model",
            "usage": {"input_tokens": 2, "output_tokens": 1},
            "proposal": {
                "operation": {
                    "kind": "select-alignment",
                    "payload": {
                        "candidate_id": candidate["candidate_id"],
                        "obligation_id": candidate.get("obligation_id"),
                    },
                }
            },
            "response_receipt": {"body_sha256": "specialist-response"},
        }

    router = StaticCapabilityRouter(
        (
            CapabilityRecord(
                capability_id="configured-specialist",
                kind=CapabilityKind.SPECIALIST,
                operations=("select-alignment",),
                judgment_forms=("structured-proposal",),
                operation_scopes=("select-alignment",),
                provider="test-specialist",
                adapter=specialist,
            ),
        )
    )
    root = tmp_path / "history"
    result = PlanningRuntime(root, router=router).run(
        config,
        output_root=tmp_path / "run",
        mode="live",
    )

    assert calls
    assert result.provider_result["status"] == "answered"
    assert result.provider_result["model"] == "specialist-model"
    assert result.state["selected_alignments"]
    assert result.decision_trace
    assert {item["decision_class"] for item in result.decision_trace} == {"agent"}
    history_event = HistoryStore(root).get(result.history_event_id)
    assert history_event["decision_class"] == "agent"
    assert HistoryStore(root).verify("main")["valid"] is True
    replay = PlanningRuntime(
        root,
        provider=lambda *_: (_ for _ in ()).throw(AssertionError()),
    ).replay()
    assert replay["state"] == result.state
    assert {item["decision_class"] for item in replay["decision_trace"]} == {"agent"}


def test_unconfigured_specialist_is_explicitly_unavailable(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    router = StaticCapabilityRouter(
        (
            CapabilityRecord(
                capability_id="missing-specialist",
                kind=CapabilityKind.SPECIALIST,
                judgment_forms=("structured-proposal",),
                provider="missing-provider",
                adapter=None,
            ),
        )
    )

    result = PlanningRuntime(tmp_path / "history", router=router).run(
        config,
        output_root=tmp_path / "run",
        mode="live",
    )

    assert result.status == "reviewable-incomplete"
    assert result.provider_result["status"] == "unavailable"
    assert result.provider_result["failure_class"] == "no-capable-provider"
