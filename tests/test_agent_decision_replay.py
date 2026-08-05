from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import geopandas as gpd
import pytest
from pydantic import ValidationError

from satn import compile
from satn.deployment import build_area_deployment
from satn.models import (
    AgentDecisionAction,
    AgentDecisionChoice,
    AgentDecisionLedger,
    AgentDecisionResponse,
    CouncilConfig,
    TrafficLight,
)
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
    return config


def response_for(request: object, choice_id: str = "1") -> AgentDecisionResponse:
    return AgentDecisionResponse(
        request_id=request.request_id,
        dependency_fingerprint=request.dependency_fingerprint,
        choice_id=choice_id,
    )


def complete_with_first_choices(
    config: CouncilConfig,
) -> tuple[object, AgentDecisionLedger]:
    responses: list[AgentDecisionResponse] = []
    for _ in range(20):
        ledger = AgentDecisionLedger(responses=tuple(responses))
        result = compile(config, decision_ledger=ledger)
        if result.status == "complete":
            return result, ledger
        assert result.status == "decision-required"
        responses.append(response_for(result.decision_requests[0]))
    raise AssertionError("fixture did not complete within its bounded decision count")


def test_fresh_recompilation_applies_only_the_current_fingerprinted_choice(
    tmp_path: Path,
) -> None:
    config = prepared_config(tmp_path)
    first = compile(config)
    request = first.decision_requests[0]

    replayed = compile(
        config,
        decision_ledger=AgentDecisionLedger(responses=(response_for(request),)),
    )

    assert replayed.status == "decision-required"
    assert replayed.decision_requests[0].request_id != request.request_id
    applied = replayed.agent_records[0]
    assert applied.decision_request == request
    assert applied.selected_choice_id == "1"
    assert applied.mapped_action.kind == "select-network-role"
    assert applied.responder_mode == "caller"
    assert applied.choice_validation == "accepted"
    assert applied.affected_feature_identifiers == request.affected_identifiers
    invalid_record = applied.model_dump(mode="json")
    invalid_record["selected_choice_id"] = "99"
    with pytest.raises(ValidationError, match="must match the offered request action"):
        type(applied).model_validate(invalid_record)
    invalid_activity = applied.model_dump(mode="json")
    invalid_activity["usage"] = {"requests": 1, "tokens": 1}
    with pytest.raises(ValidationError, match="cannot contain direct-runtime activity"):
        type(applied).model_validate(invalid_activity)


@pytest.mark.parametrize(
    ("choice_id", "action_kind"),
    [
        ("1", "select-network-role"),
        ("2", "reject-candidate"),
        ("3", "retain-network-gap"),
    ],
)
def test_each_numbered_choice_maps_independently_through_public_compile(
    tmp_path: Path,
    choice_id: str,
    action_kind: str,
) -> None:
    config = prepared_config(tmp_path)
    request = compile(config).decision_requests[0]

    result = compile(
        config,
        decision_ledger=AgentDecisionLedger(
            responses=(response_for(request, choice_id=choice_id),)
        ),
    )

    applied = result.agent_records[0]
    assert applied.selected_choice_id == choice_id
    assert applied.mapped_action.kind == action_kind
    assert applied.decision_request == request


def test_choice_three_publishes_the_same_gap_audit_to_every_spatial_surface(
    tmp_path: Path,
) -> None:
    config = prepared_config(tmp_path)
    responses: list[AgentDecisionResponse] = []
    first_request = None
    for _ in range(20):
        result = compile(
            config,
            decision_ledger=AgentDecisionLedger(responses=tuple(responses)),
        )
        if result.status != "decision-required":
            break
        request = result.decision_requests[0]
        first_request = first_request or request
        choice_id = "3" if not responses else "1"
        responses.append(response_for(request, choice_id=choice_id))
    else:
        raise AssertionError("fixture did not publish within its bounded decision count")

    assert result.status == "reviewable"
    assert first_request is not None
    geojson = json.loads(result.artifacts["geojson"].read_text(encoding="utf-8"))
    gap = next(
        feature
        for feature in geojson["features"]
        if feature["properties"].get("agent_decision_request_id")
        == first_request.request_id
    )
    assert gap["properties"]["feature_type"] == "gap"
    assert gap["properties"]["agent_decision_choice_id"] == "3"
    geopackage_gaps = gpd.read_file(result.artifacts["geopackage"], layer="gaps")
    row = geopackage_gaps.set_index("agent_decision_request_id").loc[
        first_request.request_id
    ]
    assert row["agent_decision_choice_id"] == "3"
    review = json.loads(
        (result.artifacts["review_map"].parent / "network.geojson").read_text(
            encoding="utf-8"
        )
    )
    review_gap = next(
        feature
        for feature in review["features"]
        if feature["properties"].get("agent_decision_request_id")
        == first_request.request_id
    )
    assert review_gap["properties"]["agent_decision_choice_id"] == "3"


def test_unknown_choice_and_stale_fingerprint_cannot_advance_compilation(
    tmp_path: Path,
) -> None:
    config = prepared_config(tmp_path)
    request = compile(config).decision_requests[0]

    unknown = compile(
        config,
        decision_ledger=AgentDecisionLedger(
            responses=(response_for(request, choice_id="99"),)
        ),
    )
    stale = compile(
        config,
        decision_ledger=AgentDecisionLedger(
            responses=(
                AgentDecisionResponse(
                    request_id=request.request_id,
                    dependency_fingerprint="0" * 64,
                    choice_id="1",
                ),
            )
        ),
    )
    another_request = compile(
        config,
        decision_ledger=AgentDecisionLedger(
            responses=(
                AgentDecisionResponse(
                    request_id="agent-decision-for-another-scope",
                    dependency_fingerprint=request.dependency_fingerprint,
                    choice_id="1",
                ),
            )
        ),
    )

    assert unknown.status == stale.status == "decision-required"
    assert unknown.decision_requests == stale.decision_requests == [request]
    assert unknown.agent_records == stale.agent_records == []
    assert unknown.metadata["decision_response_validation"] == "unknown-choice"
    assert stale.metadata["decision_response_validation"] == "stale-fingerprint"
    assert another_request.decision_requests == [request]
    assert another_request.agent_records == []
    assert another_request.metadata["decision_response_validation"] == "unknown-request"


def test_decision_ledger_rejects_executable_or_free_form_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentDecisionLedger.model_validate(
            {
                "responses": [
                    {
                        "request_id": "request-a",
                        "dependency_fingerprint": "0" * 64,
                        "choice_id": "1",
                        "compiler_action": "delete-everything",
                    }
                ]
            }
        )


def test_typed_actions_and_ledger_order_are_canonical_and_schema_valid() -> None:
    with pytest.raises(ValidationError, match="requires only network_role"):
        AgentDecisionAction(
            kind="select-network-role",
            network_role="direct",
            comparison_status="match",
        )
    with pytest.raises(ValidationError, match="only the reserved terminate"):
        AgentDecisionChoice(
            choice_id="1",
            label="Invalid termination",
            compiler_action=AgentDecisionAction(kind="terminate"),
            expected_consequence="Invalid.",
            mandatory_constraints=("Invalid.",),
        )
    first = AgentDecisionResponse(
        request_id="request-a",
        dependency_fingerprint="a" * 64,
        choice_id="1",
    )
    second = AgentDecisionResponse(
        request_id="request-b",
        dependency_fingerprint="b" * 64,
        choice_id="2",
    )
    assert AgentDecisionLedger(responses=(second, first)).responses == (first, second)


def test_terminate_stops_and_preserves_the_previous_publication(tmp_path: Path) -> None:
    config = prepared_config(tmp_path)
    config.compilation.agent.review_statuses = ()
    published = compile(config)
    run_before = published.artifacts["run"].read_bytes()
    config.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    request = compile(config).decision_requests[0]

    terminated = compile(
        config,
        decision_ledger=AgentDecisionLedger(
            responses=(response_for(request, choice_id="terminate"),)
        ),
    )

    assert terminated.status == "terminated"
    assert terminated.artifacts == {}
    assert terminated.agent_records[-1].selected_choice_id == "terminate"
    assert terminated.agent_records[-1].mapped_action.kind == "terminate"
    assert published.artifacts["run"].read_bytes() == run_before


def test_complete_replay_is_stable_and_published_records_agree(tmp_path: Path) -> None:
    config = prepared_config(tmp_path)
    completed, ledger = complete_with_first_choices(config)
    repeated = compile(config, decision_ledger=ledger)

    assert repeated.status == "complete"
    assert repeated.run_id == completed.run_id
    assert repeated.metadata["publication_reused"] is True
    run = json.loads(completed.artifacts["run"].read_text(encoding="utf-8"))
    records = json.loads(
        completed.artifacts["agents"].read_text(encoding="utf-8")
    )["records"]
    applied = [record for record in records if record["responder_mode"] == "caller"]
    assert applied
    assert run["decision_contract"] == "agent-decision-menu/v1"
    assert run["decision_ledger_input"] == ledger.model_dump(mode="json")
    assert run["accepted_decisions"] == sorted([
        {
            "request_id": record["decision_request"]["request_id"],
            "dependency_fingerprint": record["decision_request"][
                "dependency_fingerprint"
            ],
            "choice_id": record["selected_choice_id"],
        }
        for record in applied
    ], key=lambda response: response["request_id"])
    assert {
        (record["decision_request"]["request_id"], record["selected_choice_id"])
        for record in applied
    } == {(response.request_id, response.choice_id) for response in ledger.responses}
    network = json.loads(completed.artifacts["geojson"].read_text(encoding="utf-8"))
    spatial = {
        str(feature["id"]): feature["properties"] for feature in network["features"]
    }
    for record in applied:
        if record["decision"] != "accept":
            continue
        properties = spatial[record["connection_id"]]
        assert properties["agent_decision_request_id"] == record["decision_request"][
            "request_id"
        ]
        assert properties["agent_decision_choice_id"] == record["selected_choice_id"]
        assert properties["agent_decision_action"] == record["mapped_action"]["kind"]
        assert properties["agent_decision_responder_mode"] == record["responder_mode"]
    geopackage = gpd.read_file(
        completed.artifacts["geopackage"],
        layer="spine_access_connections",
    ).set_index("agent_decision_request_id")
    for record in applied:
        if record["decision"] != "accept" or record["network_role"] != "direct":
            continue
        row = geopackage.loc[record["decision_request"]["request_id"]]
        assert row["agent_decision_choice_id"] == record["selected_choice_id"]
        assert row["agent_decision_action"] == record["mapped_action"]["kind"]
    review_script = (
        completed.artifacts["review_map"].parent / "assets" / "review-map.js"
    ).read_text(encoding="utf-8")
    assert 'addDefinition(list, "Decision request"' in review_script
    assert 'addDefinition(list, "Selected choice"' in review_script

    config.compilation.full = True
    forced = compile(config, decision_ledger=ledger)
    first_run = json.loads(forced.artifacts["run"].read_text(encoding="utf-8"))
    first_run.pop("compilation_metadata")
    first_records = forced.artifacts["agents"].read_bytes()
    first_network = forced.artifacts["geojson"].read_bytes()
    forced_again = compile(config, decision_ledger=ledger)
    assert forced_again.run_id == forced.run_id
    repeated_run = json.loads(
        forced_again.artifacts["run"].read_text(encoding="utf-8")
    )
    repeated_run.pop("compilation_metadata")
    assert repeated_run == first_run
    assert forced_again.artifacts["agents"].read_bytes() == first_records
    assert forced_again.artifacts["geojson"].read_bytes() == first_network


def test_persisted_decision_ledgers_must_be_canonical_at_compile_and_reuse_boundaries(
    tmp_path: Path,
) -> None:
    config = prepared_config(tmp_path)
    completed, ledger = complete_with_first_choices(config)
    assert len(ledger.responses) > 1

    # A caller-owned JSON input has no safe normalisation step: its raw order is
    # part of the signed/fingerprinted wire contract.
    reordered_path = tmp_path / "reordered-decisions.json"
    reordered = ledger.model_dump(mode="json")
    reordered["responses"].reverse()
    reordered_path.write_text(json.dumps(reordered), encoding="utf-8")
    with pytest.raises(ValueError, match="not canonical"):
        compile(config, decision_ledger=reordered_path)

    run_path = completed.artifacts["run"]
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run.pop("decision_ledger_input")
    run_path.write_text(json.dumps(run), encoding="utf-8")
    rebuilt = compile(config, decision_ledger=ledger)
    assert rebuilt.status == "complete"
    assert rebuilt.metadata.get("publication_reused") is not True

    # A reordered persisted audit is equally invalid for reuse; compilation
    # repairs it rather than treating it as the original decision record.
    run = json.loads(rebuilt.artifacts["run"].read_text(encoding="utf-8"))
    run["accepted_decisions"].reverse()
    rebuilt.artifacts["run"].write_text(json.dumps(run), encoding="utf-8")
    repaired = compile(config, decision_ledger=ledger)
    assert repaired.status == "complete"
    assert repaired.metadata.get("publication_reused") is not True

    # A stored ledger can remain canonical while no longer describing the
    # compilation input.  Reuse must recompute its fingerprint, rather than
    # trusting the old run fingerprint that an altered wire record retained.
    run = json.loads(repaired.artifacts["run"].read_text(encoding="utf-8"))
    run["decision_ledger_input"]["responses"][0]["choice_id"] = "2"
    repaired.artifacts["run"].write_text(json.dumps(run), encoding="utf-8")
    rebuilt_again = compile(config, decision_ledger=ledger)
    assert rebuilt_again.status == "complete"
    assert rebuilt_again.metadata.get("publication_reused") is not True


def test_direct_runtime_publishes_a_canonical_multi_decision_audit(tmp_path: Path) -> None:
    config = prepared_config(tmp_path)
    config.compilation.agent.response_mode = "direct-runtime"
    config.compilation.agent.max_requests = 100
    config.compilation.agent.max_tokens = 10_000
    config.compilation.agent.deadline_seconds = 5

    completed = compile(config)
    assert completed.status in {"complete", "reviewable"}
    run = json.loads(completed.artifacts["run"].read_text(encoding="utf-8"))
    accepted = run["accepted_decisions"]
    assert len(accepted) > 1
    assert [response["request_id"] for response in accepted] == sorted(
        response["request_id"] for response in accepted
    )


def test_area_deployment_rejects_a_reordered_caller_ledger_run(tmp_path: Path) -> None:
    config = prepared_config(tmp_path)
    completed, _ledger = complete_with_first_choices(config)
    run_path = completed.artifacts["run"]
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["decision_ledger_input"]["responses"].reverse()
    run_path.write_text(json.dumps(run), encoding="utf-8")

    with pytest.raises(SystemExit, match="decision provenance"):
        build_area_deployment(config, tmp_path / "deployment", bootstrap=True)


def test_cli_accepts_a_json_ledger_and_exits_at_the_next_request(tmp_path: Path) -> None:
    config = prepared_config(tmp_path)
    request = compile(config).decision_requests[0]
    config_path = config.config_path
    yaml_text = config_path.read_text(encoding="utf-8")
    yaml_text = yaml_text.replace("      - amber\n      - red", "      - green")
    config_path.write_text(yaml_text, encoding="utf-8")
    ledger_path = tmp_path / "decisions.json"
    ledger_path.write_text(
        AgentDecisionLedger(
            responses=(response_for(request),)
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            str(PROJECT / ".venv" / "bin" / "satn"),
            "compile",
            str(config_path),
            "--decision-ledger",
            str(ledger_path),
        ],
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
    assert payload["agent_records"][0]["selected_choice_id"] == "1"
    assert payload["decision_requests"][0]["request_id"] != request.request_id
