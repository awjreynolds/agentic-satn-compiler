from __future__ import annotations

from pathlib import Path

import pytest

from satn.planning_history import (
    HistoryCorruptError,
    HistoryMissingError,
    HistoryStaleHeadError,
    HistoryStore,
)


def _reducer(state: object, operation: object) -> object:
    assert isinstance(state, dict)
    assert isinstance(operation, dict)
    return {"value": state["value"] + operation["amount"]}


def test_fork_and_replay_preserve_parent_and_allow_divergent_choice(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history")
    store.create_branch("main")

    chosen = store.commit(
        "main",
        None,
        {
            "event_kind": "decision",
            "input_state": {"value": 0},
            "output_state": {"value": 1},
            "operation": {"amount": 1},
            "outcome": "accepted",
        },
    )
    checkpoint = store.checkpoint(chosen)
    store.fork(checkpoint, "alternative")

    replacement = store.advance(
        "alternative",
        {
            "event_kind": "decision",
            "input_state": {"value": 0},
            "output_state": {"value": 2},
            "operation": {"amount": 2},
            "outcome": "accepted",
        },
    )

    assert store.replay("main", _reducer)["state"] == {"value": 1}
    assert store.replay("alternative", _reducer)["state"] == {"value": 2}
    assert store.head("main").head_event_id == chosen
    assert store.head("alternative").head_event_id == replacement


def test_corrupt_record_is_rejected_and_missing_record_is_distinct(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history")
    record_id = store.put({"value": 3}, kind="state")
    path = store.record_path(record_id)
    corrupted = path.read_text(encoding="utf-8").replace('"value":3', '"value":4')
    path.write_text(corrupted, encoding="utf-8")

    with pytest.raises(HistoryCorruptError):
        store.get(record_id)

    path.unlink()
    with pytest.raises(HistoryMissingError):
        store.get(record_id)


def test_stale_expected_head_does_not_mutate_branch(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history")
    store.create_branch("main")
    first = store.commit("main", None, {"event_kind": "attempt", "outcome": "started"})

    with pytest.raises(HistoryStaleHeadError) as error:
        store.commit("main", None, {"event_kind": "attempt", "outcome": "interrupted"})

    assert error.value.code == "concurrent_head_conflict"
    assert error.value.actual_head == first
    assert store.head("main").head_event_id == first


def test_compare_marks_old_dependent_outputs_stale_after_replacement(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history")
    store.create_branch("main")
    original = store.commit(
        "main",
        None,
        {
            "event_kind": "decision",
            "input_state": {"value": 0},
            "output_state": {"value": 1},
            "operation": {"amount": 1},
        },
    )
    dependent = store.commit(
        "main",
        original,
        {
            "event_kind": "projection",
            "input_state": {"value": 1},
            "output_state": {"value": 10},
            "operation": {"amount": 9},
            "dependency_refs": [original],
        },
    )
    checkpoint = store.checkpoint(original)
    store.fork(checkpoint, "alternative")
    replacement = store.advance(
        "alternative",
        {
            "event_kind": "decision",
            "input_state": {"value": 0},
            "output_state": {"value": 2},
            "operation": {"amount": 2},
        },
    )
    store.commit(
        "alternative",
        replacement,
        {
            "event_kind": "projection",
            "input_state": {"value": 2},
            "output_state": {"value": 20},
            "operation": {"amount": 18},
            "dependency_refs": [replacement],
        },
    )

    comparison = store.compare("main", "alternative")

    assert comparison["replaced_events"][0] == {
        "old_event_id": original,
        "new_event_id": replacement,
    }
    assert dependent in comparison["invalidated_descendants"]
    assert store.verify("main")["valid"] is True


def test_attempts_are_recorded_and_replay_skips_interrupted_dispatch(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history")
    store.create_branch("main")
    started = store.commit(
        "main",
        None,
        {
            "event_kind": "attempt",
            "request": {"prompt": "choose"},
            "outcome": "started",
            "state_transition": False,
        },
    )
    interrupted = store.commit(
        "main",
        started,
        {
            "event_kind": "attempt",
            "outcome": "interrupted",
            "state_transition": False,
        },
    )

    replay = store.replay("main", _reducer)

    assert replay["state"] is None
    assert replay["advanced"] is False
    assert [item["outcome"] for item in replay["diagnostics"]] == ["started", "interrupted"]
    assert store.head("main").head_event_id == interrupted


def test_accepted_model_event_requires_response_receipt(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history")
    store.create_branch("main")

    with pytest.raises(ValueError, match="response receipt"):
        store.commit(
            "main",
            None,
            {"event_kind": "provider", "actor_kind": "model", "outcome": "accepted"},
        )

    event_id = store.commit(
        "main",
        None,
        {
            "event_kind": "provider",
            "actor_kind": "model",
            "input_state": {"value": 0},
            "output_state": {"value": 3},
            "operation": {"amount": 3},
            "receipt": {"provider": "local-test", "response": "ok"},
            "outcome": "accepted",
        },
    )
    assert store.verify(event_id)["valid"] is True
    assert store.replay("main", _reducer)["state"] == {"value": 3}


def test_commit_verify_and_replay_reject_missing_transitive_dependency(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history")
    store.create_branch("main")
    leaf = store.put({"source": "evidence"}, kind="evidence")
    manifest = store.put({"dependency_refs": [leaf]}, kind="manifest")
    event_id = store.commit(
        "main",
        None,
        {
            "event_kind": "projection",
            "dependency_refs": [manifest],
            "state_transition": False,
        },
    )

    store.record_path(leaf).unlink()

    with pytest.raises(HistoryMissingError):
        store.verify("main")
    with pytest.raises(HistoryMissingError):
        store.replay("main", _reducer)
    with pytest.raises(HistoryMissingError):
        store.commit(
            "main",
            event_id,
            {"event_kind": "projection", "dependency_refs": [manifest], "state_transition": False},
        )


def test_commit_binds_input_state_and_history_root_to_current_head(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history")
    store.create_branch("main")
    first = store.commit(
        "main",
        None,
        {
            "event_kind": "decision",
            "input_state": {"value": 0},
            "output_state": {"value": 1},
            "operation": {"amount": 1},
        },
    )

    with pytest.raises(ValueError, match=r"input state|history root"):
        store.commit(
            "main",
            first,
            {
                "event_kind": "decision",
                "input_state": {"value": 100},
                "output_state": {"value": 101},
                "operation": {"amount": 1},
                "input_history_root": None,
            },
        )

    assert store.head("main").head_event_id == first


def test_valid_byte_artifact_is_part_of_record_closure(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history")
    artifact = store.put_artifact(b"payload")
    manifest = store.put({"output_artifact_refs": [artifact]}, kind="manifest")

    assert store.verify(manifest)["valid"] is True


def test_new_events_store_materialized_state_and_operation_by_reference(
    tmp_path: Path,
) -> None:
    store = HistoryStore(tmp_path / "history")
    store.create_branch("main")
    input_state = {"value": 0, "evidence": "x" * 1024}
    output_state = {"value": 1, "evidence": "y" * 1024}
    operation = {"kind": "choose", "payload": {"amount": 1, "evidence": "z" * 1024}}
    request = {"prompt": "choose", "redacted": True}
    receipt = {"status": "answered", "response": {"choice": "candidate-1"}}

    event_id = store.commit(
        "main",
        None,
        {
            "event_kind": "decision",
            "input_state": input_state,
            "output_state": output_state,
            "operation": operation,
            "request": request,
            "receipt": receipt,
        },
    )

    event = store.get(event_id)
    assert "input_state" not in event
    assert "output_state" not in event
    assert "operation" not in event
    assert "request" not in event
    assert "receipt" not in event
    assert store.get(event["input_state_ref"]) == input_state
    assert store.get(event["output_state_ref"]) == output_state
    assert store.get(event["operation_ref"]) == operation
    assert store.get(event["request_ref"]) == request
    assert store.get(event["receipt_ref"]) == receipt


def test_planning_state_storage_shares_problem_facts_without_changing_reads(
    tmp_path: Path,
) -> None:
    shared_geometry = "line-" + ("x" * 4096)
    problem = {
        "schema_version": "planning-problem/v1",
        "problem_id": "planning-problem-test",
        "input_fingerprint": "problem-fingerprint",
        "brief": {"brief_ref": "brief-test", "corridor_policy": "retain"},
        "brief_fingerprint": "brief-fingerprint",
        "source_corridors": [{"corridor_id": "corridor-1", "geometry": shared_geometry}],
        "places": [{"place_id": "place-1", "geometry": shared_geometry}],
        "obligations": [{"obligation_id": "obligation-1", "subject": "corridor-1"}],
        "candidates": [{"candidate_id": "candidate-1", "geometry": shared_geometry}],
    }
    state = {
        "schema_version": "proposal-state/v1",
        "parent_problem_id": problem["problem_id"],
        "problem_fingerprint": problem["input_fingerprint"],
        "brief": problem["brief"],
        "brief_fingerprint": problem["brief_fingerprint"],
        "source_corridors": problem["source_corridors"],
        "places": problem["places"],
        "obligations": problem["obligations"],
        "candidates": problem["candidates"],
        "obligation_dispositions": {"obligation-1": "unresolved"},
        "connection_intents": [],
        "selected_alignments": [],
        "departures": [],
        "planning_gaps": [],
        "unknown_facts": [],
        "future_interventions": [],
        "operations": [],
    }

    legacy_store = HistoryStore(tmp_path / "legacy")
    legacy_ref = legacy_store.put(state, kind="state")
    assert legacy_store.get(legacy_ref) == state

    store = HistoryStore(tmp_path / "compact")
    problem_ref = store.put(problem, kind="planning-problem")
    compact_ref = store.put_state(state, problem_ref=problem_ref)

    assert compact_ref == legacy_ref
    assert store.get(compact_ref) == state
    assert store.get_record(compact_ref) == ("state", state)
    assert (
        store.record_path(compact_ref).stat().st_size
        < legacy_store.record_path(legacy_ref).stat().st_size
    )
    assert store.verify(compact_ref)["valid"] is True

    loaded = store.get(compact_ref)
    assert isinstance(loaded, dict)
    loaded["source_corridors"][0]["geometry"] = "mutated"
    assert store.get(compact_ref) == state

    problem_path = store.record_path(problem_ref)
    problem_path.unlink()
    with pytest.raises(HistoryMissingError):
        store.get(compact_ref)
    with pytest.raises(HistoryMissingError):
        store.verify(compact_ref)

    tampered_store = HistoryStore(tmp_path / "tampered")
    tampered_problem_ref = tampered_store.put(problem, kind="planning-problem")
    tampered_ref = tampered_store.put_state(state, problem_ref=tampered_problem_ref)
    tampered_path = tampered_store.record_path(tampered_problem_ref)
    tampered_path.write_text("{}", encoding="utf-8")
    with pytest.raises(HistoryCorruptError):
        tampered_store.get(tampered_ref)


def test_shared_record_is_loaded_once_per_verification(tmp_path: Path, monkeypatch) -> None:
    store = HistoryStore(tmp_path / "history")
    store.create_branch("main")
    shared = store.put({"source": "shared"}, kind="evidence")
    first = store.commit(
        "main",
        None,
        {
            "event_kind": "attempt",
            "state_transition": False,
            "dependency_refs": [shared],
        },
    )
    store.commit(
        "main",
        first,
        {
            "event_kind": "attempt",
            "state_transition": False,
            "dependency_refs": [shared],
        },
    )

    calls: list[Path] = []
    original = Path.read_bytes
    shared_path = store.record_path(shared)

    def counted(path: Path):
        if path == shared_path:
            calls.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", counted)
    assert store.verify("main")["valid"] is True
    assert calls.count(shared_path) == 1

    calls.clear()
    assert store.replay("main", _reducer)["state"] is None
    assert calls.count(shared_path) == 1


def test_replay_cache_does_not_share_mutable_operation_payloads(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history")
    store.create_branch("main")
    operation = {"amount": 1}
    first = store.commit(
        "main",
        None,
        {
            "event_kind": "decision",
            "input_state": {"value": 0},
            "output_state": {"value": 1},
            "operation": operation,
        },
    )
    store.commit(
        "main",
        first,
        {
            "event_kind": "decision",
            "input_state": {"value": 1},
            "output_state": {"value": 2},
            "operation": operation,
        },
    )

    def mutating_reducer(state: object, current: object) -> object:
        assert isinstance(state, dict)
        assert isinstance(current, dict)
        amount = current["amount"]
        current["amount"] = 99
        return {"value": state["value"] + amount}

    assert store.replay("main", mutating_reducer)["state"] == {"value": 2}
