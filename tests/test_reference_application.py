"""Reference SATN application-plan contract regressions for PRD #137."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError
from test_alignment_selection import (
    AcceptedDecisionEnvelope,
    AlignmentCritiqueRecord,
    AlignmentDecisionResponse,
    ScenarioDecisionRecord,
    accepted_envelope,
    adopt_reference_satn,
    build_alignment_decision_request,
    candidate,
    candidate_set,
    criteria,
    principal_assertion,
    profile,
    reference_decision,
    scenario,
    select_preferred_alignment,
)
from test_prepared_scenario_compilation import (
    bound_criteria,
    connection,
    packet,
    preparation,
    request,
)

from satn.reference_application import (
    ReferenceApplicationPlan,
    build_reference_application_plan,
)
from satn.scenario_compilation import compile_prepared_scenario
from satn.spine_access_candidate_preparation import PreparedCandidateRecord


def retained_prepared_connection(label: str = "one"):
    item = connection(
        label,
        selection_profile=profile(review_when=[]),
    )
    selected = item.candidate_set.admitted_candidates[0]
    record = PreparedCandidateRecord(
        candidate=selected,
        route_role="ncn-informed",
        routing_edge_ids=("edge-forward-1", "edge-forward-2"),
        reverse_routing_edge_ids=("edge-reverse-2", "edge-reverse-1"),
        generation_rationale="Finite current-asset-informed route.",
        current_asset_share=0.75,
        current_asset_evidence_json="[]",
        official_b_road_share=0.0,
        official_b_road_evidence_json="[]",
        connection_json=(
            '{"access_connection_id":"'
            + item.access_connection_id
            + '","community_attachment_node":"node-a",'
            '"target_attachment_node":"node-b"}'
        ),
        strategic_spine_json="{}",
        preparation_disposition="retained-representative",
    )
    return replace(item, candidate_records=(record,))


def adopted_reference(item=None):
    item = item or retained_prepared_connection()
    prepared = preparation(item)
    result = compile_prepared_scenario(
        prepared,
        request(
            (
                packet(
                    item,
                    bound_criteria(item),
                    source_preparation=prepared,
                ),
            )
        ),
    )
    assert result.scenario is not None and result.scenario.publishable
    reference = adopt_reference_satn(
        result.scenario,
        governed_decision=reference_decision(result.scenario),
    )
    return reference, prepared


def build(reference, prepared):
    return build_reference_application_plan(reference, prepared)


def readopt_with_lineage(reference, lineage_fingerprints):
    payload = reference.scenario.model_dump(mode="python")
    payload["lineage_fingerprints"] = tuple(sorted(lineage_fingerprints))
    payload["scenario_id"] = ""
    payload["scenario_fingerprint"] = ""
    rebuilt = type(reference.scenario).model_validate(payload)
    return adopt_reference_satn(
        rebuilt,
        governed_decision=reference_decision(rebuilt),
    )


def test_builds_exact_immutable_deterministic_replay_plan() -> None:
    reference, prepared = adopted_reference()

    first = build(reference, prepared)
    second = build(reference, prepared)

    assert first == second
    assert first.plan_fingerprint == second.plan_fingerprint
    assert first.reference_selection_fingerprint == (reference.reference_selection_fingerprint)
    assert first.preparation_fingerprint == prepared.preparation_fingerprint
    assert first.scenario_fingerprint == reference.scenario.scenario_fingerprint
    assert first.profile_fingerprint == reference.scenario.profile_fingerprint
    assert first.evidence_snapshot_fingerprint == (
        reference.scenario.evidence_snapshot.snapshot_fingerprint
    )
    assert first.selection_run_fingerprint == (
        reference.scenario.decision_record.record_fingerprint
    )
    assert first.authoritative_network_geometry_mutated is False
    assert first.publication_created is False
    assert len(first.candidate_bindings) == 1
    binding = first.candidate_bindings[0]
    source = prepared.prepared_spine_access_connections[0]
    selected = source.candidate_set.admitted_candidates[0]
    assert binding.logical_connection_id == source.candidate_set.connection_id
    assert binding.source_access_connection_id == source.access_connection_id
    assert binding.selected_candidate_id == selected.candidate_id
    assert binding.route_role == "ncn-informed"
    assert binding.routing_edge_ids == ("edge-forward-1", "edge-forward-2")
    assert binding.reverse_routing_edge_ids == (
        "edge-reverse-2",
        "edge-reverse-1",
    )
    assert binding.geometry_fingerprint == selected.geometry_fingerprint
    assert binding.candidate_evidence_fingerprints == (selected.evidence_fingerprints)
    assert binding.selected_candidate_id in reference.complementary_candidate_ids
    with pytest.raises(ValidationError, match="frozen"):
        first.publication_created = True


def test_plan_round_trip_rejects_changed_binding_or_derived_identity() -> None:
    reference, prepared = adopted_reference()
    plan = build(reference, prepared)

    payload = plan.model_dump(mode="json")
    payload["candidate_bindings"][0]["route_role"] = "other-routable"
    with pytest.raises(ValidationError, match="binding is stale"):
        ReferenceApplicationPlan.model_validate(payload)

    payload = plan.model_dump(mode="json")
    payload["scenario_area_fingerprint"] = "0" * 64
    with pytest.raises(ValidationError, match="plan fingerprint is stale"):
        ReferenceApplicationPlan.model_validate(payload)

    payload = plan.model_dump(mode="json")
    payload["candidate_bindings"][0]["reverse_routing_edge_ids"] = ["tampered-reverse-edge"]
    with pytest.raises(ValidationError, match="binding is stale"):
        ReferenceApplicationPlan.model_validate(payload)


def test_builder_cannot_accept_or_emit_unverified_external_identity_claims() -> None:
    reference, prepared = adopted_reference()

    with pytest.raises(TypeError, match="unexpected keyword argument"):
        build_reference_application_plan(
            reference,
            prepared,
            area_definition_sha256="0" * 64,
            snapshot_manifest_sha256="1" * 64,
            governed_input_fingerprint="2" * 64,
        )

    payload = build(reference, prepared).model_dump(mode="json")
    assert "area_definition_sha256" not in payload
    assert "snapshot_manifest_sha256" not in payload
    assert "governed_input_fingerprint" not in payload


def test_rejects_stale_foreign_and_different_preparation_lineage() -> None:
    reference, prepared = adopted_reference()
    stale = replace(prepared, preparation_fingerprint="0" * 64)
    with pytest.raises(ValueError, match="fingerprint is stale"):
        build(reference, stale)

    foreign = retained_prepared_connection("foreign")
    foreign_preparation = preparation(foreign)
    with pytest.raises(ValueError, match="foreign or unconsumed"):
        build(reference, foreign_preparation)

    source = prepared.prepared_spine_access_connections[0]
    changed_record = replace(
        source.candidate_records[0],
        reverse_routing_edge_ids=("different-reverse-edge",),
    )
    different_preparation = preparation(replace(source, candidate_records=(changed_record,)))
    with pytest.raises(ValueError, match="exact preparation, profile and criteria"):
        build(reference, different_preparation)


@pytest.mark.parametrize("mutation", ("missing-criteria", "extra-foreign"))
def test_rejects_freshly_readopted_scenario_with_inexact_lineage(mutation) -> None:
    reference, prepared = adopted_reference()
    criteria_fingerprints = {
        selection.criteria.criteria_fingerprint for selection in reference.scenario.selections
    }
    lineage = {prepared.preparation_fingerprint, *criteria_fingerprints}
    if mutation == "missing-criteria":
        lineage.remove(next(iter(criteria_fingerprints)))
    else:
        lineage.add("f" * 64)
    forged_reference = readopt_with_lineage(reference, lineage)

    with pytest.raises(ValueError, match="exact preparation, profile and criteria"):
        build(forged_reference, prepared)


@pytest.mark.parametrize("mutation", ("missing", "extra"))
def test_rejects_refingerprinted_preparation_with_forged_evidence_fingerprints(
    mutation,
) -> None:
    reference, prepared = adopted_reference()
    source = prepared.prepared_spine_access_connections[0]
    forged_evidence = set(prepared.evidence_fingerprints)
    if mutation == "missing":
        forged_evidence.remove(next(iter(forged_evidence)))
    else:
        forged_evidence.add("f" * 64)
    forged_preparation = preparation(
        source,
        fingerprints=tuple(sorted(forged_evidence)),
    )
    exact_forged_lineage = {
        forged_preparation.preparation_fingerprint,
        *(selection.criteria.criteria_fingerprint for selection in reference.scenario.selections),
    }
    forged_reference = readopt_with_lineage(reference, exact_forged_lineage)

    with pytest.raises(ValueError, match="raw evidence lineage"):
        build(forged_reference, forged_preparation)


@pytest.mark.parametrize(
    "changed_edges",
    (
        {"routing_edge_ids": ()},
        {"reverse_routing_edge_ids": ()},
    ),
)
def test_rejects_selected_route_without_both_edge_sequences(changed_edges) -> None:
    reference, prepared = adopted_reference()
    source = prepared.prepared_spine_access_connections[0]
    changed_record = replace(source.candidate_records[0], **changed_edges)
    incomplete_preparation = preparation(replace(source, candidate_records=(changed_record,)))

    with pytest.raises(ValueError, match="non-empty forward and reverse"):
        build(reference, incomplete_preparation)


def test_rejects_missing_duplicate_and_rejected_candidate_records() -> None:
    reference, prepared = adopted_reference()
    source = prepared.prepared_spine_access_connections[0]

    missing = replace(source, candidate_records=())
    with pytest.raises(ValueError, match="one exact record"):
        build(reference, preparation(missing))

    duplicate = replace(
        source,
        candidate_records=(source.candidate_records[0],) * 2,
    )
    with pytest.raises(ValueError, match="unique and canonically ordered"):
        build(reference, preparation(duplicate))

    rejected_record = replace(
        source.candidate_records[0],
        preparation_disposition="rejected-topology-unsatisfied",
        rejection_reason="topology-unsatisfied",
    )
    rejected = replace(source, candidate_records=(rejected_record,))
    with pytest.raises(ValueError, match="one exact record"):
        build(reference, preparation(rejected))


def test_rejects_duplicate_logical_connection_and_unresolved_roster() -> None:
    reference, prepared = adopted_reference()
    source = prepared.prepared_spine_access_connections[0]
    duplicate = replace(
        source,
        access_connection_id=source.access_connection_id + "-duplicate",
    )
    duplicated_preparation = preparation(source, duplicate)
    with pytest.raises(ValueError, match="duplicates a Candidate Set"):
        build(reference, duplicated_preparation)

    roster = list(prepared.connection_roster)
    roster[0] = replace(
        roster[0],
        disposition="unresolved-gap",
        reason="parent-lineage-unresolved",
    )
    unresolved = preparation(
        source,
        roster=tuple(roster),
    )
    with pytest.raises(ValueError, match="unresolved connection"):
        build(reference, unresolved)


def complementary_reference():
    left = candidate("complementary-left", role="unresolved-strategic-alignment")
    right = candidate("complementary-right", role="unresolved-strategic-alignment")
    admitted = candidate_set(left, right)
    provisional = select_preferred_alignment(profile(), admitted, criteria(admitted))
    base = scenario((provisional,), mode="profile-fallback-awaiting-review")
    decision_request = build_alignment_decision_request(
        provisional,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    option = next(
        item for item in decision_request.options if item.action == "retain-complementary-set"
    )
    response = AlignmentDecisionResponse(
        request_id=decision_request.request_id,
        request_fingerprint=decision_request.request_fingerprint,
        option_id=option.option_id,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            decision_request,
            option_id=option.option_id,
        ),
    )
    critique = AlignmentCritiqueRecord(
        request_fingerprint=decision_request.request_fingerprint,
        response_fingerprint=response.response_fingerprint,
        selection_fingerprint=provisional.selection_fingerprint,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
        evidence_snapshot_fingerprint=decision_request.evidence_snapshot_fingerprint,
        profile_fingerprint=decision_request.profile_fingerprint,
        finding="accepted",
        resolved=True,
        evidence_ids=("network-assessment",),
        invocation=principal_assertion(
            decision_request,
            critic=True,
            response_fingerprint=response.response_fingerprint,
        ),
    )
    accepted = AcceptedDecisionEnvelope(
        request=decision_request,
        response=response,
        critique=critique,
    )
    compiled = scenario(
        (provisional,),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            accepted_envelopes=(accepted,),
        ),
    )
    return adopt_reference_satn(
        compiled,
        governed_decision=reference_decision(compiled),
    )


def test_v1_stops_for_complementary_set_instead_of_inventing_replacement() -> None:
    _, prepared = adopted_reference()
    reference = complementary_reference()

    with pytest.raises(ValueError, match="cannot express a complementary set"):
        build(reference, prepared)


def test_rejects_reference_with_resolved_network_gap() -> None:
    invalid = candidate(
        "gap",
        role="interurban-spine",
        topology="unsatisfied",
        places=("bath", "saltford"),
        obligations=("secondary-school",),
    )
    admitted = candidate_set(
        invalid,
        places=("bath", "saltford"),
        obligations=("secondary-school",),
    )
    gap_selection = select_preferred_alignment(
        profile(),
        admitted,
        criteria(admitted),
    )
    base = scenario(
        (gap_selection,),
        mode="provisional-review-awaiting-decision",
    )
    decision_request = build_alignment_decision_request(
        gap_selection,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    expose = next(item for item in decision_request.options if item.action == "expose-network-gap")
    accepted = accepted_envelope(
        gap_selection,
        decision_request,
        expose.option_id,
        scenario_context_fingerprint=base.scenario_context_fingerprint,
    )
    compiled = scenario(
        (gap_selection,),
        decision_record=ScenarioDecisionRecord(
            mode="accepted-agent-decision-ledger",
            accepted_envelopes=(accepted,),
        ),
    )
    reference = adopt_reference_satn(
        compiled,
        governed_decision=reference_decision(compiled),
    )
    _, prepared = adopted_reference()

    with pytest.raises(ValueError, match="Network Gap"):
        build(reference, prepared)
