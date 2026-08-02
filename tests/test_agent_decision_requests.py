from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from satn import compile
from satn.compiler import _GOVERNED_INPUT_BINDING, governed_input_binding
from satn.models import CouncilConfig, TrafficLight
from satn.sources import snapshot

PROJECT = Path(__file__).parents[1]


def prepared_config(tmp_path: Path) -> CouncilConfig:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    config.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    config.compilation.agent.provider = "provider-that-must-not-be-constructed"
    return config


def test_public_compile_returns_a_stable_bounded_decision_menu_without_publication(
    tmp_path: Path,
) -> None:
    config = prepared_config(tmp_path)

    first = compile(config)
    second = compile(config)

    assert first.status == "decision-required"
    assert first.artifacts == {}
    assert not first.output_dir.exists()
    assert first.decision_requests == second.decision_requests
    assert len(first.decision_requests) == 1
    request = first.decision_requests[0]
    assert request.request_id.startswith("agent-decision-")
    assert len(request.dependency_fingerprint) == 64
    assert request.decision_contract == "agent-decision-menu/v1"
    assert request.compilation_scope == "spine-access"
    assert request.affected_identifiers
    assert request.criterion == "continuity"
    assert request.status == TrafficLight.GREEN
    assert request.question == (
        "Which predefined compiler action should be applied for the continuity criterion?"
    )
    assert request.governed_evidence_references
    assert [choice.choice_id for choice in request.choices] == [
        "1",
        "2",
        "3",
        "terminate",
    ]
    assert all(choice.label for choice in request.choices)
    assert all(choice.compiler_action for choice in request.choices)
    assert all(choice.expected_consequence for choice in request.choices)
    assert all(choice.mandatory_constraints for choice in request.choices)
    terminate = request.choices[-1]
    assert terminate.compiler_action.kind == "terminate"
    assert terminate.expected_consequence == (
        "Stop this run, preserve the previous valid publication, and require a fresh compilation."
    )


def test_cli_prints_the_machine_readable_menu_and_exits_without_input(
    tmp_path: Path,
) -> None:
    config = prepared_config(tmp_path)
    config_path = config.config_path
    yaml_text = config_path.read_text(encoding="utf-8")
    yaml_text = yaml_text.replace("      - amber\n      - red", "      - green")
    yaml_text = yaml_text.replace(
        "provider: fake", "provider: provider-that-must-not-be-constructed"
    )
    config_path.write_text(yaml_text, encoding="utf-8")

    completed = subprocess.run(
        [str(PROJECT / ".venv" / "bin" / "satn"), "compile", str(config_path)],
        cwd=PROJECT,
        env={**os.environ, "PYTHONPATH": str(PROJECT / "src")},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["status"] == "decision-required"
    assert payload["decision_requests"][0]["choices"][0]["choice_id"] == "1"
    assert payload["decision_requests"][0]["choices"][-1]["choice_id"] == "terminate"


def test_decision_required_preserves_the_previous_valid_publication(
    tmp_path: Path,
) -> None:
    config = prepared_config(tmp_path)
    config.compilation.agent.review_statuses = ()
    published = compile(config)
    previous_run = published.artifacts["run"].read_bytes()

    config.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    required = compile(config)

    assert required.status == "decision-required"
    assert published.artifacts["run"].read_bytes() == previous_run


def test_governed_evidence_change_invalidates_only_the_dependency_fingerprint(
    tmp_path: Path,
) -> None:
    config = prepared_config(tmp_path)
    first = compile(config).decision_requests[0]
    assert config.source.fixture_dir is not None
    network_path = config.source.fixture_dir / "network.geojson"
    network = json.loads(network_path.read_text(encoding="utf-8"))
    network["features"][0]["properties"]["name"] = "Changed governed evidence"
    network_path.write_text(json.dumps(network), encoding="utf-8")
    snapshot(config, replace=True)

    changed = compile(config).decision_requests[0]

    assert changed.request_id == first.request_id
    assert changed.dependency_fingerprint != first.dependency_fingerprint


def test_unexpected_compiler_failures_remain_normal_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = prepared_config(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("unexpected compiler defect")

    monkeypatch.setattr("satn.pipeline.compile_network", fail)

    with pytest.raises(RuntimeError, match="unexpected compiler defect"):
        compile(config)


def test_governed_input_binding_is_nested_and_exception_safe() -> None:
    assert _GOVERNED_INPUT_BINDING.get() is None

    with governed_input_binding():
        outer = _GOVERNED_INPUT_BINDING.get()
        assert outer is not None
        with pytest.raises(RuntimeError, match="scope probe"), governed_input_binding():
            assert _GOVERNED_INPUT_BINDING.get() is not outer
            raise RuntimeError("scope probe")
        assert _GOVERNED_INPUT_BINDING.get() is outer

    assert _GOVERNED_INPUT_BINDING.get() is None
