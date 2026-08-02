from __future__ import annotations

import hashlib
import json
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
from satn.reviewable_network import (
    compile_reviewable_network,
    reviewable_network_for_optional_context_unavailable,
    terminal_reviewable_network_for_governed_error,
    validate_semantic_payload,
)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _rehash_semantic(payload: dict[str, object]) -> None:
    fingerprint_payload = dict(payload)
    fingerprint_payload.pop("result_fingerprint")
    payload["result_fingerprint"] = _fingerprint(fingerprint_payload)


def _evidence_request(
    *,
    request_id: str,
    kind: str,
    reason: str,
    candidate_set_id: str | None = None,
    target_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": request_id,
        "kind": kind,
        "reason": reason,
        "candidate_set_id": candidate_set_id,
        "target_id": target_id,
        "evidence_ids": [],
    }
    return payload | {"fingerprint": _fingerprint(payload)}


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


def test_reviewable_network_has_one_json_semantic_payload_with_full_compiler_sections() -> None:
    prepared = connection("semantic")
    source = preparation(prepared)
    result = compile_reviewable_network(
        source,
        request((packet(prepared, bound_criteria(prepared), source_preparation=source),)),
    )

    payload = result.semantic_payload
    json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert payload["scenario"]["scenario_fingerprint"] == result.scenario.scenario_fingerprint
    assert payload["compiler_result"]["result_fingerprint"] == (
        result.compiler_result.result_fingerprint
    )
    assert payload["candidate_sets"]
    assert payload["selections"]
    assert payload["criteria"]
    assert payload["candidate_sets"][0]["admissions"]
    assert payload["effective_selections"][0]["candidate"]["candidate_id"] == (
        result.effective_selections[0].candidate_id
    )
    validate_semantic_payload(payload)
    tampered = json.loads(json.dumps(payload))
    tampered["candidate_sets"][0]["candidates"][0]["directness_m"] += 1
    with pytest.raises(ValueError, match=r"Candidate Set|fingerprint"):
        validate_semantic_payload(tampered)


def test_semantic_validation_rejects_fabricated_officer_state_with_fresh_outer_hash() -> None:
    prepared = connection("fabricated-officer")
    source = preparation(prepared)
    result = compile_reviewable_network(
        source,
        request((packet(prepared, bound_criteria(prepared), source_preparation=source),)),
    )
    payload = json.loads(json.dumps(result.semantic_payload))
    payload["officer_decisions"].append(
        {
            "decision_id": "officer-decision-invented",
            "target_id": prepared.candidate_set.connection_id,
            "route_id": result.effective_selections[0].candidate_id,
            "status": "applied",
            "candidate_set_id": prepared.candidate_set.candidate_set_id,
            "candidate_id": result.effective_selections[0].candidate_id,
        }
    )
    _rehash_semantic(payload)

    with pytest.raises(ValueError, match="officer"):
        validate_semantic_payload(payload)


def test_semantic_validation_requires_every_selected_route_to_remain_effective() -> None:
    prepared = connection("missing-effective")
    source = preparation(prepared)
    result = compile_reviewable_network(
        source,
        request((packet(prepared, bound_criteria(prepared), source_preparation=source),)),
    )
    payload = json.loads(json.dumps(result.semantic_payload))
    payload["effective_selections"] = []
    _rehash_semantic(payload)

    with pytest.raises(ValueError, match="effective selection roster is incomplete"):
        validate_semantic_payload(payload)


def test_semantic_validation_binds_unavailable_officer_output_to_exact_input() -> None:
    prepared = connection("foreign-unavailable")
    source = preparation(prepared)
    result = compile_reviewable_network(
        source,
        request((packet(prepared, bound_criteria(prepared), source_preparation=source),)),
    )
    payload = json.loads(json.dumps(result.semantic_payload))
    target_id = "invented-target"
    route_id = "invented-route"
    decision_id = "officer-decision-" + _fingerprint(
        {"target_id": target_id, "route_id": route_id}
    )[:20]
    decision = {
        "decision_id": decision_id,
        "target_id": target_id,
        "route_id": route_id,
        "status": "target-unavailable",
        "candidate_set_id": None,
        "candidate_id": None,
    }
    payload["officer_decisions"].append(decision)
    payload["target_unavailable"].append(decision)
    payload["evidence_requests"].append(
        _evidence_request(
            request_id="officer-target-" + decision_id,
            kind="officer-target",
            reason="officer-decision-target-unavailable",
            target_id=target_id,
        )
    )
    _rehash_semantic(payload)

    with pytest.raises(ValueError, match="governed input"):
        validate_semantic_payload(payload)


def test_semantic_validation_rejects_gap_foreign_to_preparation() -> None:
    prepared = connection("foreign-gap")
    source = preparation(prepared)
    result = compile_reviewable_network(
        source,
        request((packet(prepared, bound_criteria(prepared), source_preparation=source),)),
    )
    payload = json.loads(json.dumps(result.semantic_payload))
    connection_id = "invented-connection"
    endpoints = ["invented-a", "invented-b"]
    reason = "invented-gap"
    gap_id = "network-gap-" + _fingerprint(
        {
            "connection_id": connection_id,
            "endpoints": endpoints,
            "reason": reason,
        }
    )[:20]
    payload["network_gaps"].append(
        {
            "gap_id": gap_id,
            "candidate_set_id": None,
            "connection_id": connection_id,
            "network_role": None,
            "endpoints": endpoints,
            "reason": reason,
            "unsatisfied_network_place_ids": endpoints,
            "unsatisfied_access_obligation_ids": [],
            "unsatisfied_strategic_destination_ids": [],
            "display_state": "unresolved-gap",
        }
    )
    payload["evidence_requests"].append(
        _evidence_request(
            request_id="network-gap-" + gap_id,
            kind="network-gap",
            reason=reason,
            target_id=connection_id,
        )
    )
    _rehash_semantic(payload)

    with pytest.raises(ValueError, match="foreign to preparation"):
        validate_semantic_payload(payload)


def test_semantic_validation_rejects_hash_consistent_malformed_preparation() -> None:
    prepared = connection("forged-preparation")
    source = preparation(prepared)
    result = compile_reviewable_network(
        source,
        request((packet(prepared, bound_criteria(prepared), source_preparation=source),)),
    )
    payload = json.loads(json.dumps(result.semantic_payload))
    payload["preparation"]["connection_roster"].append(
        {
            "access_connection_id": "invented-unresolved",
            "obligation_kind": "community",
            "parent_role": "spine-access-connection",
            "community_id": "invented-a",
            "place_id": "invented-a",
            "parent_place_id": "invented-b",
            "disposition": "unresolved-gap",
            "reason": "invented-gap",
        }
    )
    forged_preparation_fingerprint = _fingerprint(payload["preparation"])
    payload["preparation_fingerprint"] = forged_preparation_fingerprint
    payload["fingerprints"]["preparation"] = forged_preparation_fingerprint
    _rehash_semantic(payload)

    with pytest.raises(ValueError, match="unresolved roster connection"):
        validate_semantic_payload(payload)


def test_semantic_validation_rejects_unknown_preparation_disposition() -> None:
    prepared = connection("forged-disposition")
    source = preparation(prepared)
    result = compile_reviewable_network(
        source,
        request((packet(prepared, bound_criteria(prepared), source_preparation=source),)),
    )
    payload = json.loads(json.dumps(result.semantic_payload))
    payload["preparation"]["connection_roster"].append(
        {
            "access_connection_id": "invented-disposition-row",
            "obligation_kind": "community",
            "parent_role": "spine-access-connection",
            "community_id": "invented-a",
            "place_id": "invented-a",
            "parent_place_id": "invented-b",
            "disposition": "invented-disposition",
            "reason": None,
        }
    )
    payload["preparation"]["diagnostics"]["expected_connection_roster_count"] += 1
    forged_preparation_fingerprint = _fingerprint(payload["preparation"])
    payload["preparation_fingerprint"] = forged_preparation_fingerprint
    payload["fingerprints"]["preparation"] = forged_preparation_fingerprint
    _rehash_semantic(payload)

    with pytest.raises(ValueError, match="disposition"):
        validate_semantic_payload(payload)


def test_semantic_validation_recomputes_optional_request_roster() -> None:
    prepared = connection("invented-request")
    source = preparation(prepared)
    result = compile_reviewable_network(
        source,
        request((packet(prepared, bound_criteria(prepared), source_preparation=source),)),
    )
    payload = json.loads(json.dumps(result.semantic_payload))
    payload["evidence_requests"].append(
        _evidence_request(
            request_id="evidence-request-invented",
            kind="optional-evidence",
            reason="invented-optional-evidence",
            candidate_set_id=prepared.candidate_set.candidate_set_id,
        )
    )
    _rehash_semantic(payload)

    with pytest.raises(ValueError, match="evidence-request roster mismatch"):
        validate_semantic_payload(payload)


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


def test_unresolved_preparation_row_without_endpoints_remains_explicit_gap() -> None:
    roster = PreparedConnectionRosterRecord(
        access_connection_id="missing-endpoints",
        obligation_kind="community",
        parent_role="spine-access-connection",
        community_id=None,
        place_id=None,
        parent_place_id=None,
        disposition="unresolved-gap",
        reason="missing-network-place-endpoints",
    )
    issue = CandidatePreparationIssue(
        access_connection_id="missing-endpoints",
        reason="missing-network-place-endpoints",
        detail="No governed network-place endpoint was available.",
    )
    source = preparation(roster=(roster,), issues=(issue,))

    result = compile_reviewable_network(source, request())

    assert result.status == "complete"
    assert len(result.network_gaps) == 1
    gap = result.network_gaps[0]
    assert gap.connection_id == "missing-endpoints"
    assert gap.endpoints == ()
    assert gap.unsatisfied_network_place_ids == ()
    validate_semantic_payload(result.semantic_payload)


def test_optional_context_unavailable_retains_unresolved_preparation_gap() -> None:
    roster = PreparedConnectionRosterRecord(
        access_connection_id="optional-context-gap",
        obligation_kind="community",
        parent_role="spine-access-connection",
        community_id="community-optional-context",
        place_id=None,
        parent_place_id=None,
        disposition="unresolved-gap",
        reason="missing-optional-context-endpoints",
    )
    issue = CandidatePreparationIssue(
        access_connection_id="optional-context-gap",
        reason="missing-optional-context-endpoints",
        detail="Optional context was unavailable before endpoint derivation.",
    )
    source = preparation(roster=(roster,), issues=(issue,))

    result = reviewable_network_for_optional_context_unavailable(
        source,
        missing_inputs=("area-definition",),
        officer_decisions=(
            PreloadedOfficerDecision(target_id="optional-officer", route_id="route"),
        ),
    )

    assert result.status == "complete"
    assert len(result.network_gaps) == 1
    assert result.network_gaps[0].connection_id == "optional-context-gap"
    assert result.network_gaps[0].endpoints == ()
    assert result.target_unavailable[0].target_id == "optional-officer"
    validate_semantic_payload(result.semantic_payload)


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
    assert isinstance(result.semantic_payload["evidence_requests"][0], dict)
    json.dumps(result.semantic_payload)


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
    assert isinstance(result.semantic_payload["divergences"][0], dict)
    assert isinstance(result.semantic_payload["officer_decisions"][0], dict)
    json.dumps(result.semantic_payload)


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


def test_terminal_result_retains_unresolved_preparation_gap() -> None:
    roster = PreparedConnectionRosterRecord(
        access_connection_id="terminal-unresolved",
        obligation_kind="community",
        parent_role="spine-access-connection",
        community_id=None,
        place_id=None,
        parent_place_id=None,
        disposition="unresolved-gap",
        reason="terminal-missing-endpoints",
    )
    issue = CandidatePreparationIssue(
        access_connection_id="terminal-unresolved",
        reason="terminal-missing-endpoints",
        detail="The governed preparation is terminal before endpoint derivation.",
    )
    source = preparation(roster=(roster,), issues=(issue,))

    result = terminal_reviewable_network_for_governed_error(
        source,
        (),
        error=ValueError("candidate preparation fingerprint is stale"),
    )

    assert result.status == "terminal-failure"
    assert len(result.network_gaps) == 1
    assert result.network_gaps[0].connection_id == "terminal-unresolved"
    validate_semantic_payload(result.semantic_payload)


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
