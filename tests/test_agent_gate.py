from __future__ import annotations

import hashlib
import os

import pytest

from satn.agents import (
    AgentDecisionRequired,
    AgentDecisionResolver,
    AgentRole,
    CompilationGate,
    FakeAgentRuntime,
    PydanticAIRuntime,
)
from satn.models import (
    AgentConfig,
    AgentDecisionLedger,
    AgentDecisionResponse,
    AgentRecord,
    DivergenceRecord,
    TrafficLight,
)


def facts(*, direct: str = "green", low_traffic: str = "green") -> dict[str, object]:
    return {
        "from_place": "a",
        "to_place": "b",
        "evidence_ids": ["edge-1"],
        "checks_by_role": {
            "direct": {
                "endpoints": direct,
                "continuity": direct,
                "bidirectional": direct,
                "distance": "green",
            },
            "low-traffic": {
                "endpoints": low_traffic,
                "continuity": low_traffic,
                "bidirectional": low_traffic,
                "distance": "green",
            },
        },
    }


def gate(runtime: FakeAgentRuntime, *, attempts: int = 3) -> CompilationGate:
    return CompilationGate(
        runtime,
        AgentConfig(
            response_mode="direct-runtime",
            review_statuses=tuple(TrafficLight),
            max_attempts=attempts,
            max_requests=12,
            max_tokens=100,
        ),
    )


def test_agent_review_policy_defaults_to_amber_and_red_and_is_canonical() -> None:
    assert AgentConfig().review_statuses == (TrafficLight.AMBER, TrafficLight.RED)
    assert AgentConfig(review_statuses=["grey", "green", "grey"]).review_statuses == (
        TrafficLight.GREEN,
        TrafficLight.GREY,
    )
    assert AgentConfig(enabled=False).review_statuses == ()
    assert AgentConfig(enabled="false").review_statuses == ()
    with pytest.raises(ValueError, match="enabled: false conflicts"):
        AgentConfig(enabled=False, review_statuses=["amber"])
    config = AgentConfig()
    config.review_statuses = ["grey", "green", "grey"]
    assert config.review_statuses == (TrafficLight.GREEN, TrafficLight.GREY)


def test_decision_uses_the_declared_governing_criterion_without_status_rollup() -> None:
    outcome = CompilationGate(None, AgentConfig(review_statuses=(TrafficLight.AMBER,))).evaluate(
        "connection-a-b",
        facts(direct="green"),
        "direct",
        ["direct"],
        governing_criterion="continuity",
        governing_status=TrafficLight.GREEN,
    )

    assert outcome.record.governing_criterion == "continuity"
    assert outcome.record.governing_status == TrafficLight.GREEN
    assert outcome.record.review_required is False


def test_deterministic_non_invoked_record_has_no_wall_clock_semantic_input() -> None:
    outcome = CompilationGate(None, AgentConfig(review_statuses=(TrafficLight.AMBER,))).evaluate(
        "connection-a-b",
        facts(direct="green"),
        "direct",
        ["direct"],
        governing_criterion="continuity",
        governing_status=TrafficLight.GREEN,
    )

    assert outcome.record.created_at is None


def test_reviewed_record_exposes_only_the_bounded_choice_audit() -> None:
    outcome = gate(FakeAgentRuntime(), attempts=1).evaluate(
        "connection-a-b",
        facts(),
        "direct",
        ["direct"],
        governing_criterion="continuity",
        governing_status=TrafficLight.GREEN,
    )

    assert outcome.record.responder_mode == "direct-runtime"
    assert outcome.record.selected_choice_id == "1"
    assert outcome.record.mapped_action is not None
    assert outcome.record.mapped_action.network_role == "direct"
    assert outcome.record.proposal is None
    assert outcome.record.critique is None
    assert outcome.record.revision is None
    assert outcome.record.attempts == []


def test_review_audit_records_reject_contradictory_execution_state() -> None:
    base_record = {
        "connection_id": "connection-a-b",
        "governing_criterion": "continuity",
        "governing_status": TrafficLight.GREEN,
        "review_policy": (TrafficLight.AMBER,),
        "review_required": False,
        "runtime": "not-invoked",
        "model": "not-invoked",
        "decision": "accept",
    }
    with pytest.raises(ValueError, match="membership in policy"):
        AgentRecord(**{**base_record, "review_required": True})
    with pytest.raises(ValueError, match="skipped review must have no runtime"):
        AgentRecord(**{**base_record, "runtime": "fake"})
    with pytest.raises(ValueError, match="required review must record"):
        AgentRecord(
            **{
                **base_record,
                "governing_status": TrafficLight.AMBER,
                "review_required": True,
                "runtime": "fake",
            }
        )
    with pytest.raises(ValueError, match="required divergence review"):
        DivergenceRecord(
            connection_id="connection-a-b",
            governing_status=TrafficLight.AMBER,
            review_policy=(TrafficLight.AMBER,),
            review_required=True,
            status="deviation",
            overlap_ratio=0.5,
            explanation="Review was incorrectly omitted.",
        )
    with pytest.raises(ValueError, match="skipped divergence review"):
        DivergenceRecord(
            connection_id="connection-a-b",
            governing_status=TrafficLight.GREEN,
            review_policy=(TrafficLight.AMBER,),
            review_required=False,
            status="match",
            overlap_ratio=1,
            explanation="Deterministic match.",
            resolution_attempts=[{"attempt": 1}],
        )


@pytest.mark.parametrize("status", tuple(TrafficLight))
def test_selected_status_returns_a_bounded_caller_menu(status: TrafficLight) -> None:
    config = AgentConfig(
        review_statuses=(status,),
        max_attempts=1,
        max_requests=4,
        max_tokens=100,
    )

    with pytest.raises(AgentDecisionRequired) as raised:
        CompilationGate(None, config).evaluate(
            "connection-a-b",
            facts(direct=status.value),
            "direct",
            ["direct"],
            governing_criterion="continuity",
            governing_status=status,
        )

    assert raised.value.request.status == status
    assert raised.value.request.criterion == "continuity"
    assert [choice.choice_id for choice in raised.value.request.choices] == [
        "1",
        "2",
        "3",
        "terminate",
    ]
    assert raised.value.request.deterministic_findings
    finding = raised.value.request.deterministic_findings[0]
    assert finding.message == f"Deterministic criterion continuity is {status.value}."
    assert finding.evidence_ids == ["edge-1"]


def test_caller_menu_preserves_choice_order_and_predefined_action_mapping() -> None:
    config = AgentConfig(review_statuses=(TrafficLight.AMBER,))

    with pytest.raises(AgentDecisionRequired) as raised:
        CompilationGate(None, config).evaluate(
            "connection-a-b",
            facts(direct="amber"),
            "direct",
            ["direct", "low-traffic", "quietway"],
            governing_criterion="continuity",
            governing_status=TrafficLight.AMBER,
        )

    choices = raised.value.request.choices
    assert [choice.choice_id for choice in choices] == ["1", "2", "3", "terminate"]
    assert [choice.compiler_action.model_dump(exclude_none=True) for choice in choices] == [
        {"kind": "select-network-role", "network_role": "direct"},
        {"kind": "select-network-role", "network_role": "low-traffic"},
        {"kind": "select-network-role", "network_role": "quietway"},
        {"kind": "terminate"},
    ]


def test_caller_choice_cannot_waive_a_mandatory_red_invariant() -> None:
    config = AgentConfig(review_statuses=(TrafficLight.RED,))
    with pytest.raises(AgentDecisionRequired) as first:
        CompilationGate(None, config).evaluate(
            "connection-a-b",
            facts(direct="red"),
            "direct",
            ["direct"],
            governing_criterion="continuity",
            governing_status=TrafficLight.RED,
        )
    request = first.value.request
    governed_fingerprint = hashlib.sha256(config.model_dump_json().encode()).hexdigest()
    resolver = AgentDecisionResolver(
        AgentDecisionLedger(
            responses=(
                AgentDecisionResponse(
                    request_id=request.request_id,
                    dependency_fingerprint=request.dependency_fingerprint,
                    choice_id="1",
                ),
            )
        ),
        governed_fingerprint,
    )

    with pytest.raises(AgentDecisionRequired):
        CompilationGate(
            None,
            config,
            decision_resolver=resolver,
        ).evaluate(
            "connection-a-b",
            facts(direct="red"),
            "direct",
            ["direct"],
            governing_criterion="continuity",
            governing_status=TrafficLight.RED,
        )

    assert resolver.validation == "mandatory-red"
    assert resolver.applied_records == []


@pytest.mark.parametrize(
    ("status", "decision"),
    [
        (TrafficLight.GREEN, "accept"),
        (TrafficLight.AMBER, "accept"),
        (TrafficLight.RED, "gap"),
        (TrafficLight.GREY, "accept"),
    ],
)
def test_unselected_status_is_deterministic_and_never_needs_a_runtime(
    status: TrafficLight,
    decision: str,
) -> None:
    outcome = CompilationGate(None, AgentConfig(review_statuses=())).evaluate(
        "connection-a-b",
        facts(direct=status.value),
        "direct",
        ["direct"],
        governing_criterion="continuity",
        governing_status=status,
    )

    assert outcome.record.decision == decision
    assert outcome.record.governing_status == status
    assert outcome.record.review_policy == ()
    assert outcome.record.review_required is False
    assert outcome.record.runtime == "not-invoked"
    assert outcome.record.usage == {"requests": 0, "tokens": 0}


def test_success_records_one_typed_bounded_runtime_choice() -> None:
    outcome = gate(FakeAgentRuntime()).evaluate(
        "connection-a-b",
        facts(),
        "direct",
        ["direct", "low-traffic"],
        governing_criterion="continuity",
        governing_status=TrafficLight.GREEN,
    )

    assert outcome.record.decision == "accept"
    assert outcome.selected_role == "direct"
    assert outcome.record.usage == {"requests": 1, "tokens": 1}
    assert outcome.record.decision_request is not None
    assert outcome.record.choice_validation == "accepted"


def test_schema_rejection_does_not_retry_or_apply_a_result() -> None:
    runtime = FakeAgentRuntime(
        {AgentRole.DECISION: [{"invalid": "record"}]}
    )

    with pytest.raises(AgentDecisionRequired) as raised:
        gate(runtime).evaluate(
            "connection-a-b",
            facts(),
            "direct",
            ["direct", "low-traffic"],
            governing_criterion="continuity",
            governing_status=TrafficLight.GREEN,
        )

    assert raised.value.validation == "runtime-schema-failure"
    assert raised.value.applied_records == []


def test_direct_runtime_can_select_an_alternative_offered_alignment() -> None:
    runtime = FakeAgentRuntime(
        {
            AgentRole.DECISION: [
                {"request_id": "$request", "choice_id": "2"}
            ]
        }
    )

    outcome = gate(runtime).evaluate(
        "connection-a-b",
        facts(),
        "direct",
        ["direct", "low-traffic"],
        governing_criterion="continuity",
        governing_status=TrafficLight.GREEN,
    )

    assert outcome.record.decision == "accept"
    assert outcome.selected_role == "low-traffic"
    assert outcome.record.selected_choice_id == "2"


def test_unresolved_mandatory_failure_cannot_be_overridden() -> None:
    with pytest.raises(AgentDecisionRequired) as raised:
        gate(FakeAgentRuntime()).evaluate(
            "connection-a-b",
            facts(direct="red", low_traffic="red"),
            "direct",
            ["direct"],
            governing_criterion="continuity",
            governing_status=TrafficLight.RED,
        )

    assert raised.value.validation == "mandatory-red"


def test_reviewed_red_candidate_can_be_repaired_to_a_green_alternative() -> None:
    outcome = CompilationGate(
        FakeAgentRuntime(
            {
                AgentRole.DECISION: [
                    {"request_id": "$request", "choice_id": "2"}
                ]
            }
        ),
        AgentConfig(
            response_mode="direct-runtime",
            review_statuses=(TrafficLight.RED,),
        ),
    ).evaluate(
        "connection-a-b",
        facts(direct="red", low_traffic="green"),
        "direct",
        ["direct", "low-traffic"],
        governing_criterion="continuity",
        governing_status=TrafficLight.RED,
    )

    assert outcome.record.governing_status == TrafficLight.RED
    assert outcome.record.review_required is True
    assert outcome.record.decision == "accept"
    assert outcome.selected_role == "low-traffic"
    assert outcome.record.selected_choice_id == "2"


@pytest.mark.live_agent
def test_live_provider_contract() -> None:
    model = os.getenv("SATN_TEST_AGENT_MODEL")
    if not model:
        pytest.skip("SATN_TEST_AGENT_MODEL is not configured")
    agent_config = AgentConfig(
        provider="pydantic-ai",
        model=model,
        response_mode="direct-runtime",
        max_attempts=1,
        max_requests=4,
        max_tokens=1000,
    )
    outcome = CompilationGate(PydanticAIRuntime(agent_config), agent_config).evaluate(
        "connection-a-b",
        facts(),
        "direct",
        ["direct"],
        governing_criterion="continuity",
        governing_status=TrafficLight.GREEN,
    )
    assert outcome.record.runtime == "pydantic-ai"
    assert outcome.record.responder_mode == "direct-runtime"
