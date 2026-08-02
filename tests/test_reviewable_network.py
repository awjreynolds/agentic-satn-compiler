from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from test_prepared_scenario_compilation import (
    CandidatePreparationIssue,
    PreparedConnectionRosterRecord,
    bound_criteria,
    connection,
    gap_evidence,
    high_traffic_on_carriageway_candidate,
    packet,
    preparation,
    profile,
    request,
    reuse_connection,
    reuse_first_profile,
)

from satn import reviewable_network
from satn.parallel_reduction import PreloadedOfficerDecision
from satn.reviewable_network import compile_reviewable_network


def test_valid_compilation_is_a_complete_reviewable_network() -> None:
    prepared = connection("reviewable")
    source = preparation(prepared)

    result = compile_reviewable_network(
        source,
        request((packet(prepared, bound_criteria(prepared), source_preparation=source),)),
    )

    assert result.status == "complete"
    assert result.scenario is not None
    assert result.effective_selections[0].candidate_id == (
        result.scenario.selections[0].selected_candidate_id
    )
    assert result.network_gaps == ()


def test_unresolved_preparation_row_is_a_known_endpoint_gap_not_terminal_failure() -> None:
    roster = PreparedConnectionRosterRecord(
        access_connection_id="missing-parent",
        obligation_kind="community",
        parent_role="spine-access-connection",
        community_id="community-known",
        place_id="community-known",
        parent_place_id=None,
        disposition="unresolved-gap",
        reason="missing-parent-network-place-endpoint",
    )
    issue = CandidatePreparationIssue(
        access_connection_id="missing-parent",
        reason="missing-parent-network-place-endpoint",
        detail="Parent Community Network Place is missing.",
    )
    source = preparation(roster=(roster,), issues=(issue,))

    result = compile_reviewable_network(source, request())

    assert result.status == "complete"
    assert result.scenario is None
    assert len(result.network_gaps) == 1
    assert result.network_gaps[0].endpoints == ("community-known",)
    assert "missing-parent" in result.network_gaps[0].reason
    assert any(item.kind == "network-gap" for item in result.evidence_requests)


def test_valid_prepared_subset_survives_an_unresolved_sibling_row() -> None:
    prepared = connection("mixed")
    unresolved = PreparedConnectionRosterRecord(
        access_connection_id="missing-sibling",
        obligation_kind="community",
        parent_role="spine-access-connection",
        community_id="community-sibling",
        place_id="community-sibling",
        parent_place_id=None,
        disposition="unresolved-gap",
        reason="missing-sibling-parent-endpoint",
    )
    issue = CandidatePreparationIssue(
        access_connection_id="missing-sibling",
        reason="missing-sibling-parent-endpoint",
        detail="Sibling parent endpoint is missing.",
    )
    prepared_roster = preparation(prepared).connection_roster[0]
    source = preparation(
        prepared,
        roster=(prepared_roster, unresolved),
        issues=(issue,),
    )
    compilation_request = request(
        (packet(prepared, bound_criteria(prepared), source_preparation=source),)
    )

    result = compile_reviewable_network(source, compilation_request)

    assert result.status == "complete"
    assert result.scenario is not None
    assert result.effective_selections[0].candidate_id == (
        result.scenario.selections[0].selected_candidate_id
    )
    assert len(result.network_gaps) == 1
    assert result.network_gaps[0].connection_id == "missing-sibling"
    assert result.network_gaps[0].endpoints == ("community-sibling",)
    assert sum(item.kind == "network-gap" for item in result.evidence_requests) == 1
    assert result.compiler_result is not None
    assert result.compiler_result.missing_inputs == ("unresolved-preparation:missing-sibling",)


def test_no_candidate_preparation_is_an_endpoint_gap_without_geometry() -> None:
    prepared = connection("gap", gap="no-options")
    source = preparation(prepared)

    result = compile_reviewable_network(
        source,
        request((packet(prepared, gap_evidence(prepared), source_preparation=source),)),
    )

    assert result.status == "complete"
    assert result.effective_selections == ()
    assert len(result.network_gaps) == 1
    gap = result.network_gaps[0]
    assert gap.endpoints == ("community-gap", "parent-community-gap")
    assert gap.geometry is None
    assert gap.display_state == "unresolved-gap"
    assert any(item.kind == "network-gap" for item in result.evidence_requests)


def test_unknown_optional_traffic_is_a_diagnostic_and_does_not_stop_completion() -> None:
    traffic_profile = {
        "profile_id": "reviewable-traffic",
        "version": "2026-08-02",
        "metric": "all_motor_vehicles",
        "thresholds": [
            {"id": "low", "upper_vehicles_per_day": 1_000},
            {"id": "high", "upper_vehicles_per_day": None},
        ],
        "high_traffic_challenge_band": "high",
        "max_observation_age_years": 3,
        "as_at_year": 2026,
        "stale_value_policy": "retain-and-diagnose",
        "missing_policy": "explicit-unknown",
    }
    profile = reuse_first_profile(traffic_profile=traffic_profile)
    traffic = high_traffic_on_carriageway_candidate(include_observation=False)
    prepared = reuse_connection(traffic, selection_profile=profile)
    source = preparation(prepared)

    result = compile_reviewable_network(
        source,
        request((packet(prepared, bound_criteria(prepared), source_preparation=source),)),
    )

    assert result.status == "complete"
    assert result.effective_selections
    assert any(item.diagnostic_id == "traffic-unknown" for item in result.typed_diagnostics)
    traffic_request = next(
        item for item in result.evidence_requests if item.kind == "optional-evidence"
    )
    assert traffic_request.candidate_set_id == prepared.candidate_set.candidate_set_id


def test_officer_decision_applies_exact_route_and_records_divergence() -> None:
    prepared = connection("officer", ambiguous=True)
    source = preparation(prepared)
    compiler = compile_reviewable_network(
        source,
        request((packet(prepared, bound_criteria(prepared), source_preparation=source),)),
    )
    assert compiler.scenario is not None
    selection = compiler.scenario.selections[0]
    alternatives = [
        item.candidate_id for item in selection.candidate_set.admitted_candidates
    ]
    officer_route = next(item for item in alternatives if item != selection.selected_candidate_id)

    result = compile_reviewable_network(
        source,
        request((packet(prepared, bound_criteria(prepared), source_preparation=source),)),
        officer_decisions=(
            PreloadedOfficerDecision(
                target_id=selection.candidate_set.connection_id,
                route_id=officer_route,
            ),
        ),
    )

    assert result.effective_selections[0].candidate_id == officer_route
    assert result.effective_selections[0].compiler_candidate_id != officer_route
    assert len(result.divergences) == 1
    assert result.officer_decisions[0].status == "applied"


def test_officer_target_is_unavailable_without_remapping_or_expiry() -> None:
    prepared = connection("officer-unavailable")
    source = preparation(prepared)
    packet_value = packet(prepared, bound_criteria(prepared), source_preparation=source)

    result = compile_reviewable_network(
        source,
        request((packet_value,)),
        officer_decisions=(
            PreloadedOfficerDecision(
                target_id="connection-never-seen",
                route_id="route-never-seen",
            ),
        ),
    )

    assert result.effective_selections[0].candidate_id == (
        result.scenario.selections[0].selected_candidate_id
    )
    assert result.target_unavailable[0].status == "target-unavailable"
    assert result.target_unavailable[0].route_id == "route-never-seen"
    assert any(item.kind == "officer-target" for item in result.evidence_requests)


def test_profile_change_does_not_expire_exact_officer_target() -> None:
    baseline = connection("officer-profile")
    baseline_source = preparation(baseline)
    baseline_result = compile_reviewable_network(
        baseline_source,
        request((packet(baseline, bound_criteria(baseline), source_preparation=baseline_source),)),
    )
    assert baseline_result.scenario is not None
    target = baseline.candidate_set.connection_id
    route = baseline.candidate_set.admitted_candidates[0].candidate_id

    changed_profile = connection("officer-profile", selection_profile=profile(review_when=[]))
    changed_source = preparation(changed_profile)
    changed_result = compile_reviewable_network(
        changed_source,
        request(
            (
                packet(
                    changed_profile,
                    bound_criteria(changed_profile),
                    source_preparation=changed_source,
                ),
            )
        ),
        officer_decisions=(PreloadedOfficerDecision(target_id=target, route_id=route),),
    )

    assert changed_result.effective_selections[0].candidate_id == route
    assert changed_result.officer_decisions[0].status == "applied"


def test_malformed_mandatory_lineage_is_terminal_and_repeat_identity_is_stable() -> None:
    prepared = connection("malformed")
    source = preparation(prepared)
    malformed = replace(source, preparation_fingerprint=hashlib.sha256(b"wrong").hexdigest())
    compilation_request = request(
        (packet(prepared, bound_criteria(prepared), source_preparation=source),)
    )

    first = compile_reviewable_network(malformed, compilation_request)
    second = compile_reviewable_network(malformed, compilation_request)

    assert first.status == "terminal-failure"
    assert first.failure_code == "mandatory-lineage-invalid"
    assert first.scenario is None
    assert first.network_gaps == ()
    assert first.result_fingerprint == second.result_fingerprint


def test_legacy_route_without_intervention_state_is_undetermined() -> None:
    prepared = connection("legacy-display")
    source = preparation(prepared)
    result = compile_reviewable_network(
        source,
        request((packet(prepared, bound_criteria(prepared), source_preparation=source),)),
    )

    assert result.effective_selections[0].display_state == "undetermined"


def test_officer_decisions_must_be_typed_and_conflicts_are_not_last_wins() -> None:
    prepared = connection("officer-typed", ambiguous=True)
    source = preparation(prepared)
    compilation_request = request(
        (packet(prepared, bound_criteria(prepared), source_preparation=source),)
    )

    with pytest.raises(TypeError, match="PreloadedOfficerDecision"):
        compile_reviewable_network(
            source,
            compilation_request,
            officer_decisions=({"target_id": "foreign", "route_id": "foreign"},),
        )

    selection = compile_reviewable_network(source, compilation_request).scenario.selections[0]
    routes = [item.candidate_id for item in selection.candidate_set.admitted_candidates]
    with pytest.raises(ValueError, match="duplicate/conflicting"):
        compile_reviewable_network(
            source,
            compilation_request,
            officer_decisions=tuple(
                PreloadedOfficerDecision(
                    target_id=selection.candidate_set.connection_id,
                    route_id=route,
                )
                for route in routes
            ),
        )


def test_unclassified_value_error_is_not_converted_to_terminal_failure(monkeypatch) -> None:
    prepared = connection("unexpected-error")
    source = preparation(prepared)
    compilation_request = request(
        (packet(prepared, bound_criteria(prepared), source_preparation=source),)
    )

    def fail(*args, **kwargs):
        raise ValueError("unexpected internal failure")

    monkeypatch.setattr(reviewable_network, "compile_prepared_scenario", fail)
    with pytest.raises(ValueError, match="unexpected internal failure"):
        compile_reviewable_network(source, compilation_request)
