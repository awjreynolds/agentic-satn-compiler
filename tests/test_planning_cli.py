from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import yaml
from bath_saltford_fixture import configured_bath_saltford
from typer.testing import CliRunner

import satn.cli as cli
import satn.planning_cli as planning_cli
from satn.codex_specialist import CodexSpecialistAdapter as RealCodexSpecialistAdapter
from satn.planning_history import HistoryStore
from satn.sources import snapshot


def _write_cli_config(config: object, path: Path) -> Path:
    path.write_text(
        yaml.safe_dump(
            config.model_dump(mode="json", exclude={"config_path"}),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _config(path: Path) -> Path:
    path.write_text(
        """\
council_id: tiny
council_name: Tiny Council
source:
  snapshot_dir: snapshots
publication:
  output_dir: output
  title: Tiny publication
""",
        encoding="utf-8",
    )
    return path


def test_plan_run_uses_explicit_roots_and_branch(tmp_path: Path, monkeypatch) -> None:
    observed: dict[str, object] = {}

    class StubRuntime:
        def __init__(self, root: Path, **kwargs: object) -> None:
            observed["root"] = root
            observed["constructor"] = kwargs

        def run(self, config: object, **kwargs: object) -> SimpleNamespace:
            observed["config"] = config
            observed["run"] = kwargs
            return SimpleNamespace(
                as_dict=lambda: {
                    "status": "reviewable-incomplete",
                    "branch_id": kwargs["branch"],
                    "mode": kwargs["mode"],
                }
            )

    monkeypatch.setattr(planning_cli, "PlanningRuntime", StubRuntime)
    response = CliRunner().invoke(
        cli.app,
        [
            "plan",
            "run",
            str(_config(tmp_path / "area.yaml")),
            "--root",
            str(tmp_path / "history"),
            "--output-root",
            str(tmp_path / "output"),
            "--branch",
            "case-bath-keynsham",
            "--mode",
            "deterministic",
        ],
    )

    assert response.exit_code == 0, response.output
    payload = json.loads(response.stdout)
    assert payload["branch_id"] == "case-bath-keynsham"
    assert observed["root"] == tmp_path / "history"
    assert observed["run"]["output_root"] == tmp_path / "output"
    assert observed["run"]["branch"] == "case-bath-keynsham"
    assert observed["run"]["mode"] == "deterministic"
    assert observed["run"]["progress"] is planning_cli._emit_progress


def test_plan_run_keeps_json_stdout_and_writes_progress_to_stderr(
    tmp_path: Path, monkeypatch
) -> None:
    class StubRuntime:
        def __init__(self, _root: Path, **_kwargs: object) -> None:
            pass

        def run(self, _config: object, **kwargs: object) -> SimpleNamespace:
            progress = kwargs["progress"]
            assert callable(progress)
            progress(
                {
                    "stage": "preparation",
                    "status": "started",
                    "elapsed_seconds": 0.01,
                    "history_head": "head-1",
                }
            )
            progress(
                {
                    "stage": "completed",
                    "status": "reviewable-incomplete",
                    "elapsed_seconds": 0.02,
                    "history_head": "head-1",
                }
            )
            return SimpleNamespace(
                as_dict=lambda: {
                    "status": "reviewable-incomplete",
                    "history_event_id": "head-1",
                }
            )

    monkeypatch.setattr(planning_cli, "PlanningRuntime", StubRuntime)
    response = CliRunner().invoke(
        cli.app,
        [
            "plan",
            "run",
            str(_config(tmp_path / "area.yaml")),
            "--root",
            str(tmp_path / "history"),
            "--output-root",
            str(tmp_path / "output"),
        ],
    )

    assert response.exit_code == 0, response.output
    assert json.loads(response.stdout) == {
        "status": "reviewable-incomplete",
        "history_event_id": "head-1",
    }
    assert "[satn] preparation started" in response.stderr
    assert "[satn] completed reviewable-incomplete" in response.stderr
    assert "head-1" in response.stderr


def test_plan_run_forwards_json_policy_to_runtime(tmp_path: Path, monkeypatch) -> None:
    policy = {"allow_provisional_choices": True, "owner": "planning-review"}
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    observed: dict[str, object] = {}

    class StubRuntime:
        def __init__(self, root: Path, **kwargs: object) -> None:
            observed["root"] = root
            observed["constructor"] = kwargs

        def run(self, _config: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(as_dict=lambda: {"status": "reviewable-incomplete"})

    monkeypatch.setattr(planning_cli, "PlanningRuntime", StubRuntime)
    response = CliRunner().invoke(
        cli.app,
        [
            "plan",
            "run",
            str(_config(tmp_path / "area.yaml")),
            "--root",
            str(tmp_path / "history"),
            "--output-root",
            str(tmp_path / "output"),
            "--policy",
            str(policy_path),
        ],
    )

    assert response.exit_code == 0, response.output
    assert observed["constructor"]["policy"] == policy


def test_plan_run_rejects_non_object_policy(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    policy_path.write_text("[]", encoding="utf-8")

    response = CliRunner().invoke(
        cli.app,
        [
            "plan",
            "run",
            str(_config(tmp_path / "area.yaml")),
            "--root",
            str(tmp_path / "history"),
            "--output-root",
            str(tmp_path / "output"),
            "--policy",
            str(policy_path),
        ],
    )

    assert response.exit_code != 0
    assert "policy must be a JSON object" in response.output


def test_plan_run_rejects_malformed_policy(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.json"
    policy_path.write_text("{", encoding="utf-8")

    response = CliRunner().invoke(
        cli.app,
        [
            "plan",
            "run",
            str(_config(tmp_path / "area.yaml")),
            "--root",
            str(tmp_path / "history"),
            "--output-root",
            str(tmp_path / "output"),
            "--policy",
            str(policy_path),
        ],
    )

    assert response.exit_code != 0
    assert "policy must be a JSON object" in response.output


def test_plan_run_rejects_missing_policy(tmp_path: Path) -> None:
    response = CliRunner().invoke(
        cli.app,
        [
            "plan",
            "run",
            str(_config(tmp_path / "area.yaml")),
            "--root",
            str(tmp_path / "history"),
            "--output-root",
            str(tmp_path / "output"),
            "--policy",
            str(tmp_path / "missing-policy.json"),
        ],
    )

    assert response.exit_code != 0
    assert "policy must be a JSON object" in response.output


def test_plan_run_explicitly_wires_jev_and_codex_specialist(tmp_path: Path, monkeypatch) -> None:
    observed: dict[str, object] = {}

    class StubRuntime:
        def __init__(self, root: Path, **kwargs: object) -> None:
            observed["root"] = root
            observed["constructor"] = kwargs

        def run(self, _config: object, **kwargs: object) -> SimpleNamespace:
            observed["run"] = kwargs
            return SimpleNamespace(as_dict=lambda: {"status": "reviewable-incomplete"})

    monkeypatch.setattr(planning_cli, "PlanningRuntime", StubRuntime)
    response = CliRunner().invoke(
        cli.app,
        [
            "plan",
            "run",
            str(_config(tmp_path / "area.yaml")),
            "--root",
            str(tmp_path / "history"),
            "--output-root",
            str(tmp_path / "output"),
            "--mode",
            "live",
            "--specialist-model",
            "gpt-5.6-luna",
            "--specialist-reasoning-effort",
            "max",
        ],
    )

    assert response.exit_code == 0, response.output
    router = observed["constructor"].get("router")
    assert router is not None
    assert [capability.capability_id for capability in router.capabilities] == [
        "jev",
        "codex-specialist",
    ]
    specialist = router.capabilities[1]
    assert specialist.provider == "codex-exec"
    assert specialist.adapter.model == "gpt-5.6-luna"
    assert specialist.adapter.reasoning_effort == "max"
    assert observed["run"]["mode"] == "live"


def test_public_cli_runs_configured_jev_then_codex_and_replays_offline(
    tmp_path: Path, monkeypatch
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    config_path = _write_cli_config(config, tmp_path / "area.yaml")
    policy = {"allow_provisional_choices": True}
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    calls = {"jev": 0, "codex": 0}
    codex_tasks: list[str] = []
    response_body = json.dumps(
        {
            "proposal": {
                "operation": {
                    "kind": "select-alignment",
                    "payload": {"candidate_id": "placeholder"},
                }
            }
        }
    )

    class FakeJev:
        def judge(self, _state: object, _questions: object) -> dict[str, object]:
            calls["jev"] += 1
            return {
                "status": "answered",
                "provider": "jev",
                "model": "jev-test",
                "answers": {
                    "decision": {"type": "choice", "choice": "__unknown__"},
                },
                "response_receipt": {"body": "jev", "body_sha256": "jev"},
            }

    def fake_codex(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal response_body
        calls["codex"] += 1
        prompt = str(kwargs["input"])
        packet = json.loads(prompt.split("Frozen planning task:\n", 1)[1])
        assert packet["policy"] == policy
        codex_tasks.append(str(packet["task_id"]))
        candidate_id = packet["scope"]["selection_candidate_refs"][0]
        response = {
            "proposal": {
                "operation": {
                    "kind": "select-alignment",
                    "payload": {
                        "candidate_id": candidate_id,
                        "provisional": True,
                        "reason": (
                            "The admitted candidate is supported while one judgment "
                            "remains unresolved."
                        ),
                        "uncertainties": ["Whether continuous access can be confirmed."],
                    },
                }
            }
        }
        response_body = json.dumps(response)
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(response_body, encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"type": "thread.started", "model": "gpt-5.6-luna"}),
            stderr="",
        )

    def adapter_factory(*, model: str, reasoning_effort: str) -> RealCodexSpecialistAdapter:
        return RealCodexSpecialistAdapter(
            model=model,
            reasoning_effort=reasoning_effort,
            runner=fake_codex,
        )

    monkeypatch.setattr(planning_cli, "TypeSafeClient", FakeJev)
    monkeypatch.setattr(planning_cli, "CodexSpecialistAdapter", adapter_factory)
    history = tmp_path / "history"
    run_output = tmp_path / "run"
    response = CliRunner().invoke(
        cli.app,
        [
            "plan",
            "run",
            str(config_path),
            "--root",
            str(history),
            "--output-root",
            str(run_output),
            "--mode",
            "live",
            "--specialist-model",
            "gpt-5.6-luna",
            "--specialist-reasoning-effort",
            "max",
            "--policy",
            str(policy_path),
        ],
    )

    assert response.exit_code == 0, response.output
    payload = json.loads(response.stdout)
    assert calls["jev"] == 1
    assert calls["codex"] == len(codex_tasks) > 0
    assert len(codex_tasks) == len(set(codex_tasks))
    assert payload["provider_status"] == "answered"
    assert payload["provider"] == "codex-exec"
    assert payload["model"] == "gpt-5.6-luna"
    assert payload["output"]["status"] != "invalid"
    provisional = [
        item for item in payload["output"]["selected_alignments"] if item.get("provisional") is True
    ]
    assert provisional
    assert any(item.get("decision_class") == "agent" for item in payload["decision_trace"])
    event = HistoryStore(history).get(payload["history_event_id"])
    assert event["decision_class"] == "agent"
    assert event["outcome"] == "accepted"
    store = HistoryStore(history)
    operation = event.get("operation")
    if not isinstance(operation, dict) and isinstance(event.get("operation_ref"), str):
        operation = store.get(event["operation_ref"])
    assert isinstance(operation, dict)
    agent_candidate_id = operation["payload"]["candidate_id"]
    agent_selection = next(
        item for item in provisional if item["candidate_id"] == agent_candidate_id
    )
    assert agent_selection["reason"].startswith("The admitted candidate")
    assert agent_selection["uncertainties"] == ["Whether continuous access can be confirmed."]
    receipt = store.get(event["receipt_ref"])
    assert receipt["capability_id"] == "codex-specialist"
    assert receipt["provider"] == "codex-exec"
    assert receipt["requested_model"] == "gpt-5.6-luna"
    assert receipt["observed_model"] == "gpt-5.6-luna"
    assert receipt["request"]["prompt"] == receipt["request_receipt"]["body"]
    assert receipt["request"]["task_packet"]["policy"] == policy
    assert receipt["response_receipt"]["body"] == response_body
    published = json.loads(
        (Path(payload["publication"]["publication_dir"]) / "planning-output.json").read_text(
            encoding="utf-8"
        )
    )
    published_selection = next(
        item
        for item in published["selected_alignments"]
        if item["candidate_id"] == agent_candidate_id
    )
    assert published_selection["provisional"] is True
    assert published_selection["reason"] == agent_selection["reason"]
    assert published_selection["uncertainties"] == agent_selection["uncertainties"]

    def provider_must_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("replay dispatched a provider")

    monkeypatch.setattr(planning_cli, "TypeSafeClient", provider_must_not_run)
    monkeypatch.setattr(planning_cli, "CodexSpecialistAdapter", provider_must_not_run)
    replay = CliRunner().invoke(cli.app, ["plan", "replay", str(history)])

    assert replay.exit_code == 0, replay.output
    replay_payload = json.loads(replay.stdout)
    assert replay_payload["state"] == payload["state"]
    replay_selection = next(
        item
        for item in replay_payload["output"]["selected_alignments"]
        if item["candidate_id"] == agent_candidate_id
    )
    assert replay_selection["provisional"] is True
    assert replay_selection["reason"] == agent_selection["reason"]
    assert replay_selection["uncertainties"] == agent_selection["uncertainties"]


def test_public_cli_persists_failed_codex_receipt(tmp_path: Path, monkeypatch) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    config_path = _write_cli_config(config, tmp_path / "area.yaml")

    class FakeJev:
        def judge(self, _state: object, _questions: object) -> dict[str, object]:
            return {
                "status": "answered",
                "answers": {
                    "decision": {"type": "choice", "choice": "__unknown__"},
                },
            }

    def failed_codex(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 17, stdout="", stderr="process failed")

    def adapter_factory(*, model: str, reasoning_effort: str) -> RealCodexSpecialistAdapter:
        return RealCodexSpecialistAdapter(
            model=model,
            reasoning_effort=reasoning_effort,
            runner=failed_codex,
        )

    monkeypatch.setattr(planning_cli, "TypeSafeClient", FakeJev)
    monkeypatch.setattr(planning_cli, "CodexSpecialistAdapter", adapter_factory)
    history = tmp_path / "history"
    response = CliRunner().invoke(
        cli.app,
        [
            "plan",
            "run",
            str(config_path),
            "--root",
            str(history),
            "--output-root",
            str(tmp_path / "run"),
            "--mode",
            "live",
            "--specialist-model",
            "gpt-5.6-luna",
            "--specialist-reasoning-effort",
            "max",
        ],
    )

    assert response.exit_code == 0, response.output
    payload = json.loads(response.stdout)
    assert payload["provider_status"] == "unavailable"
    event = HistoryStore(history).get(payload["history_event_id"])
    assert event["decision_class"] == "agent"
    receipt = HistoryStore(history).get(event["receipt_ref"])
    assert receipt["capability_id"] == "codex-specialist"
    assert receipt["provider"] == "codex-exec"
    assert receipt["requested_model"] == "gpt-5.6-luna"
    assert receipt["observed_model"] is None
    assert receipt["request"]["prompt"] == receipt["request_receipt"]["body"]
    assert receipt["response_receipt"] is None
    assert receipt["failure_class"] == "codex-process-failed"


def test_public_cli_persists_malformed_codex_receipt(tmp_path: Path, monkeypatch) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    config_path = _write_cli_config(config, tmp_path / "area.yaml")

    class FakeJev:
        def judge(self, _state: object, _questions: object) -> dict[str, object]:
            return {
                "status": "answered",
                "answers": {
                    "decision": {"type": "choice", "choice": "__unknown__"},
                },
            }

    def malformed_codex(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text("not json", encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"type": "thread.started", "model": "actual-model"}),
            stderr="",
        )

    def adapter_factory(*, model: str, reasoning_effort: str) -> RealCodexSpecialistAdapter:
        return RealCodexSpecialistAdapter(
            model=model,
            reasoning_effort=reasoning_effort,
            runner=malformed_codex,
        )

    monkeypatch.setattr(planning_cli, "TypeSafeClient", FakeJev)
    monkeypatch.setattr(planning_cli, "CodexSpecialistAdapter", adapter_factory)
    history = tmp_path / "history"
    response = CliRunner().invoke(
        cli.app,
        [
            "plan",
            "run",
            str(config_path),
            "--root",
            str(history),
            "--output-root",
            str(tmp_path / "run"),
            "--mode",
            "live",
            "--specialist-model",
            "gpt-5.6-luna",
            "--specialist-reasoning-effort",
            "max",
        ],
    )

    assert response.exit_code == 0, response.output
    payload = json.loads(response.stdout)
    assert payload["provider_status"] == "invalid-provider-response"
    event = HistoryStore(history).get(payload["history_event_id"])
    assert event["decision_class"] == "agent"
    receipt = HistoryStore(history).get(event["receipt_ref"])
    assert receipt["provider"] == "codex-exec"
    assert receipt["capability_id"] == "codex-specialist"
    assert receipt["requested_model"] == "gpt-5.6-luna"
    assert receipt["observed_model"] == "actual-model"
    assert receipt["failure_class"] == "malformed-response"
    assert receipt["response_receipt"]["body"] == "not json"


def test_plan_run_rejects_partial_codex_configuration(tmp_path: Path) -> None:
    response = CliRunner().invoke(
        cli.app,
        [
            "plan",
            "run",
            str(_config(tmp_path / "area.yaml")),
            "--root",
            str(tmp_path / "history"),
            "--output-root",
            str(tmp_path / "output"),
            "--specialist-model",
            "gpt-5.6-sol",
        ],
    )

    assert response.exit_code != 0
    assert "must be" in response.output
    assert "supplied together" in response.output


def test_plan_verify_and_compare_are_read_only_branch_operations(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    class StubRuntime:
        def __init__(self, root: Path, **_kwargs: object) -> None:
            calls.append(("init", (root,), {}))

        def verify(self, branch: str) -> dict[str, object]:
            calls.append(("verify", (branch,), {}))
            return {"valid": True, "branch_id": branch}

        def compare(self, base: str, branch: str) -> dict[str, object]:
            calls.append(("compare", (base, branch), {}))
            return {"base_branch_id": base, "branch_id": branch}

    monkeypatch.setattr(planning_cli, "PlanningRuntime", StubRuntime)
    runner = CliRunner()
    verify = runner.invoke(
        cli.app,
        ["plan", "verify", str(tmp_path / "history"), "--branch", "main"],
    )
    compare = runner.invoke(
        cli.app,
        [
            "plan",
            "compare",
            str(tmp_path / "history"),
            "--base-branch",
            "main",
            "--branch",
            "alternative",
        ],
    )

    assert verify.exit_code == 0, verify.output
    assert json.loads(verify.stdout) == {"branch_id": "main", "valid": True}
    assert compare.exit_code == 0, compare.output
    assert json.loads(compare.stdout) == {
        "base_branch_id": "main",
        "branch_id": "alternative",
    }
    assert ("verify", ("main",), {}) in calls
    assert ("compare", ("main", "alternative"), {}) in calls
