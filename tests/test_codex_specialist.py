from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

from satn.codex_specialist import CodexSpecialistAdapter
from satn.planning_routing import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityRequirements,
    DecisionTask,
    StaticCapabilityRouter,
)


def _task() -> DecisionTask:
    return DecisionTask(
        task_id="planning-task-1",
        required_capabilities=CapabilityRequirements(
            judgment_forms=("structured-proposal",),
            exactness="proposal-then-validate",
        ),
        input_state={
            "question_kind": "alignment",
            "scope": {"candidate_refs": ["candidate-1"]},
            "candidates": [{"candidate_id": "candidate-1"}],
        },
        allowed_operations=("select-alignment",),
    )


def test_codex_adapter_guides_owner_authorized_provisional_selection() -> None:
    calls: list[dict[str, object]] = []
    response = {
        "proposal": {
            "operation": {
                "kind": "select-alignment",
                "payload": {
                    "candidate_id": "candidate-1",
                    "provisional": True,
                    "reason": (
                        "The admitted candidate is supported while one judgment remains unresolved."
                    ),
                    "uncertainties": ["Whether continuous access can be confirmed."],
                },
            }
        }
    }

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(json.dumps(response), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    task = replace(
        _task(),
        input_state={
            **_task().input_state,
            "policy": {"allow_provisional_choices": True},
        },
    )
    result = CodexSpecialistAdapter(
        model="gpt-5.6-luna",
        reasoning_effort="max",
        runner=runner,
    ).propose(task)

    assert result["status"] == "answered"
    assert result["proposal"]["operation"]["payload"]["provisional"] is True
    prompt = " ".join(str(calls[0]["input"]).split())
    assert '"allow_provisional_choices": true' in prompt
    assert "only when the frozen task policy explicitly permits it" in prompt
    assert "do not invent observations, access, provision, or adoption" in prompt


def test_codex_adapter_sends_frozen_task_and_retains_structured_response(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "proposal": {
                        "operation": {
                            "kind": "select-alignment",
                            "payload": {
                                "candidate_id": "candidate-1",
                                "obligation_id": "obligation-1",
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"type": "thread.started", "model": "observed-model"}),
            stderr="",
        )

    result = CodexSpecialistAdapter(
        model="requested-model",
        reasoning_effort="medium",
        runner=runner,
    ).propose(_task())

    assert result["status"] == "answered"
    assert result["requested_model"] == "requested-model"
    assert result["observed_model"] == "observed-model"
    assert result["proposal"]["operation"]["kind"] == "select-alignment"
    assert result["response_receipt"]["body"] == json.dumps(
        {
            "proposal": {
                "operation": {
                    "kind": "select-alignment",
                    "payload": {
                        "candidate_id": "candidate-1",
                        "obligation_id": "obligation-1",
                    },
                }
            }
        }
    )
    command = calls[0]["command"]
    assert command[0:2] == ["codex", "exec"]
    assert "--model" in command and "requested-model" in command
    assert 'model_reasoning_effort="medium"' in command
    assert "--sandbox" in command and "read-only" in command
    assert "--ephemeral" in command
    assert "--skip-git-repo-check" in command
    assert "--output-schema" not in command
    assert calls[0]["input"] == result["request_receipt"]["body"]
    assert '"candidate-1"' in calls[0]["input"]
    assert "concise `reason`" in calls[0]["input"]
    assert "candidate_id" in calls[0]["input"]


def test_codex_adapter_maps_process_failure_and_malformed_response() -> None:
    def process_failure(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="failed")

    unavailable = CodexSpecialistAdapter(
        model="requested-model",
        reasoning_effort="low",
        runner=process_failure,
    ).propose(_task())
    assert unavailable["status"] == "unavailable"
    assert unavailable["failure_class"] == "codex-process-failed"

    def malformed(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        Path(command[command.index("--output-last-message") + 1]).write_text(
            "not json", encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    invalid = CodexSpecialistAdapter(
        model="requested-model",
        reasoning_effort="low",
        runner=malformed,
    ).propose(_task())
    assert invalid["status"] == "invalid"
    assert invalid["violations"] == ["response:invalid-json"]


def test_codex_adapter_does_not_retain_jsonl_events_as_final_response() -> None:
    def missing_final(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"type": "thread.started", "model": "observed-model"}),
            stderr="",
        )

    result = CodexSpecialistAdapter(
        model="requested-model",
        reasoning_effort="low",
        runner=missing_final,
    ).propose(_task())

    assert result["status"] == "invalid"
    assert result["violations"] == ["response:missing-final-message"]
    assert result["response_receipt"] is None


def test_recorded_router_result_does_not_dispatch_codex_again() -> None:
    calls: list[object] = []

    def specialist(task: DecisionTask) -> dict[str, object]:
        calls.append(task)
        return {
            "status": "answered",
            "proposal": {
                "operation": {
                    "kind": "select-alignment",
                    "payload": {"candidate_id": "candidate-1"},
                }
            },
        }

    capability = CapabilityRecord(
        capability_id="codex-specialist",
        kind=CapabilityKind.SPECIALIST,
        judgment_forms=("structured-proposal",),
        provider="codex-exec",
        adapter=specialist,
    )
    router = StaticCapabilityRouter((capability,))
    fresh = router.route(_task())
    assert fresh.status.value == "specialist-proposal"
    assert len(calls) == 1

    recorded = router.route(replace(_task(), replay_mode="recorded", recorded_receipt=fresh.result))
    assert recorded.status.value == "specialist-proposal"
    assert len(calls) == 1
