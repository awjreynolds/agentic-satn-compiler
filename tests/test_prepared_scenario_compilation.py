"""Scenario compilation bridge regressions for PRD #137."""

from __future__ import annotations

import hashlib

import pytest

# Reuse the canonical, governed criterion factories from the selection-core
# contract tests.  This bridge intentionally does not manufacture evidence.
from test_alignment_selection import (
    accepted_envelope,
    candidate,
    candidate_set,
    criteria,
    profile,
)

from satn.alignment_selection import DecisionProcessMode, ScenarioDecisionRecord
from satn.scenario_compilation import (
    PreparedCandidateCriteria,
    PreparedScenarioCompilationInput,
    compile_prepared_scenario,
)
from satn.spine_access_candidate_preparation import (
    PreparedSpineAccessConnection,
    SpineAccessCandidatePreparationResult,
)

AREA = hashlib.sha256(b"prepared-scenario-area").hexdigest()
PREPARATION = hashlib.sha256(b"prepared-scenario-preparation").hexdigest()


def prepared(*, ambiguous: bool = False, review_when: list[str] | None = None):
    candidates = [candidate("prepared-one", role="community-access")]
    if ambiguous:
        candidates.append(candidate("prepared-two", role="community-access"))
    candidate_set_value = candidate_set(
        *candidates,
        selection_profile=profile(review_when=review_when),
    )
    return SpineAccessCandidatePreparationResult(
        contract="satn-spine-access-candidate-preparation/v1",
        profile_fingerprint=candidate_set_value.profile_fingerprint,
        status="prepared",
        prepared_spine_access_connections=(
            PreparedSpineAccessConnection(
                access_connection_id="prepared-community-access",
                candidate_set=candidate_set_value,
                root_spine_id="root-spine",
                strategic_source_id="source",
                strategic_evidence_id="evidence",
                strategic_provenance={},
                candidate_generation_rationales=(),
                candidate_records=(),
            ),
        ),
        generation_issues=(),
        missing_inputs=(),
        evidence_fingerprints=(),
        evidence_lineage={},
        preparation_fingerprint=PREPARATION,
        diagnostics={},
    )


def request(preparation, governed_criteria, *, decision_record=None):
    return PreparedScenarioCompilationInput(
        area_fingerprint=AREA,
        criteria=(
            PreparedCandidateCriteria(
                "prepared-community-access",
                governed_criteria,
            ),
        ),
        decision_record=decision_record,
    )


def test_profile_disabled_is_a_no_op_artifact() -> None:
    result = compile_prepared_scenario(
        None,
        PreparedScenarioCompilationInput(area_fingerprint=AREA),
    )

    assert result.status == "disabled"
    assert result.scenario is None
    assert result.review_orchestration is None
    assert result.reference_satn_created is False
    assert result.can_mutate_authoritative_network is False


def test_missing_exact_criterion_packet_is_explicit_and_non_publishable() -> None:
    result = compile_prepared_scenario(
        prepared(),
        PreparedScenarioCompilationInput(area_fingerprint=AREA),
    )

    assert result.status == "incomplete"
    assert result.scenario is None
    assert result.missing_inputs == (
        "candidate-criteria:prepared-community-access",
    )
    assert result.diagnostics["agent_runtime_constructed"] is False


def test_clear_selection_constructs_no_agent_runtime_or_reference_satn() -> None:
    preparation = prepared(review_when=[])
    exact_criteria = criteria(
        preparation.prepared_spine_access_connections[0].candidate_set
    )

    result = compile_prepared_scenario(preparation, request(preparation, exact_criteria))

    assert result.status == "compiled"
    assert result.scenario is not None
    assert result.review_orchestration is None
    assert result.diagnostics["agent_runtime_constructed"] is False
    assert result.reference_satn_created is False
    assert result.can_mutate_authoritative_network is False
    assert {
        item.candidate_set.network_role.value for item in result.scenario.selections
    } == {"community-access"}


def test_ambiguous_selection_exposes_only_finite_core_request_actions() -> None:
    preparation = prepared(ambiguous=True)
    exact_criteria = criteria(
        preparation.prepared_spine_access_connections[0].candidate_set
    )

    result = compile_prepared_scenario(preparation, request(preparation, exact_criteria))

    assert result.status == "review-required"
    assert result.scenario is not None
    assert result.scenario.publishable is False
    assert result.review_orchestration is not None
    requests = result.review_orchestration.actionable_requests
    assert len(requests) == 1
    assert requests[0].request.options
    assert result.diagnostics["agent_runtime_constructed"] is False


def test_accepted_data_only_ledger_replays_exactly_and_stale_ledger_fails_closed() -> None:
    preparation = prepared(ambiguous=True)
    candidate_set_value = preparation.prepared_spine_access_connections[0].candidate_set
    exact_criteria = criteria(candidate_set_value)
    provisional = compile_prepared_scenario(preparation, request(preparation, exact_criteria))
    assert provisional.scenario is not None
    assert provisional.review_orchestration is not None
    selection = provisional.scenario.selections[0]
    decision_request = provisional.review_orchestration.actionable_requests[0].request
    chosen = next(
        option.option_id
        for option in decision_request.options
        if option.candidate_id is not None
    )
    envelope = accepted_envelope(
        selection,
        decision_request,
        chosen,
        scenario_context_fingerprint=provisional.scenario.scenario_context_fingerprint,
    )
    ledger = ScenarioDecisionRecord(
        mode=DecisionProcessMode.ACCEPTED_LEDGER,
        accepted_envelopes=(envelope,),
    )

    first = compile_prepared_scenario(
        preparation,
        request(preparation, exact_criteria, decision_record=ledger),
    )
    second = compile_prepared_scenario(
        preparation,
        request(preparation, exact_criteria, decision_record=ledger),
    )

    assert first.status == second.status == "compiled"
    assert first.scenario is not None and second.scenario is not None
    assert first.scenario.scenario_fingerprint == second.scenario.scenario_fingerprint
    assert first.result_fingerprint == second.result_fingerprint

    changed_criteria = criteria(
        candidate_set_value,
        counts_500={
            item.candidate_id: index + 100
            for index, item in enumerate(candidate_set_value.admitted_candidates)
        },
    )
    with pytest.raises(
        ValueError,
        match=r"stale|exact compiler-generated menu|clear no-agent selection",
    ):
        compile_prepared_scenario(
            preparation,
            request(preparation, changed_criteria, decision_record=ledger),
        )
