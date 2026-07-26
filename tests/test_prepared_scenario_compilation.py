"""Scenario compilation bridge regressions for PRD #137."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import MappingProxyType
from typing import Literal

import pytest
from test_alignment_selection import (
    accepted_envelope,
    candidate,
    candidate_set,
    compile_education,
    criteria,
    profile,
)

from satn.alignment_selection import (
    AssessmentKind,
    CandidateCriteria,
    CandidateGenerationGapReason,
    CandidateSetGapEvidence,
    DecisionProcessMode,
    GovernedAssessmentBinding,
    GovernedEvidenceSnapshot,
    RuntimeDecisionAttempt,
    RuntimeInvocationRecord,
    ScenarioDecisionRecord,
    admit_candidate_set,
    review_frontier_fingerprint,
)
from satn.education_access import assess_education_access
from satn.scenario_compilation import (
    PreparedCandidateCriteria,
    PreparedScenarioCompilationInput,
    compile_prepared_scenario,
    prepared_network_geometry_source_fingerprint,
    prepared_topography_source_fingerprint,
)
from satn.spine_access_candidate_preparation import (
    CandidatePreparationIssue,
    PreparedConnectionRosterRecord,
    PreparedSpineAccessConnection,
    SpineAccessCandidatePreparationResult,
)

AREA = hashlib.sha256(b"prepared-scenario-area").hexdigest()
POPULATION_SOURCE = hashlib.sha256(b"population-source").hexdigest()
POPULATION_FRAME = hashlib.sha256(b"prepared-population-frame").hexdigest()
POPULATION_ARTIFACT = hashlib.sha256(b"prepared-population-artifact").hexdigest()
EDUCATION_GOVERNED = hashlib.sha256(b"prepared-education-governed").hexdigest()
EDUCATION_REGISTER = hashlib.sha256(b"prepared-education-register").hexdigest()


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def education_base_source(
    prepared: PreparedSpineAccessConnection,
) -> dict[str, object]:
    assessed = compile_education(prepared.candidate_set)
    source = assessed.source_snapshot
    base = assess_education_access(
        register_evidence=source.register_evidence,
        schools=source.schools,
        strategic_destinations=source.strategic_education_destinations,
        option_evidence=(),
    )
    return base.source_snapshot.model_dump(mode="json")


def governed_lineage(
    prepared: PreparedSpineAccessConnection,
) -> dict[str, object]:
    return {
        "population": {
            "source_content_sha256": POPULATION_SOURCE,
            "frame_content_sha256": POPULATION_FRAME,
            "artifact_lineage": [{"content_sha256": POPULATION_ARTIFACT}],
        },
        "education": {
            "governed_source_fingerprint": EDUCATION_GOVERNED,
            "source_snapshot": education_base_source(prepared),
            "school_register_lineage": {"content_sha256": EDUCATION_REGISTER},
            "admissions_lineage": None,
        },
    }


def evidence_fingerprints() -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                POPULATION_SOURCE,
                POPULATION_FRAME,
                POPULATION_ARTIFACT,
                EDUCATION_GOVERNED,
                EDUCATION_REGISTER,
            }
        )
    )


def connection(
    label: str = "one",
    *,
    ambiguous: bool = False,
    selection_profile=None,
    gap: Literal["no-options", "all-rejected"] | None = None,
) -> PreparedSpineAccessConnection:
    endpoints = (f"community-{label}", f"parent-community-{label}")
    candidates = []
    if gap != "no-options":
        candidates.append(
            candidate(
                f"prepared-{label}-one",
                role="community-access",
                endpoints=endpoints,
                places=endpoints,
            )
        )
    if ambiguous:
        candidates.append(
            candidate(
                f"prepared-{label}-two",
                role="community-access",
                endpoints=endpoints,
                places=endpoints,
            )
        )
    if gap == "no-options":
        candidate_set_value = admit_candidate_set(
            selection_profile or profile(),
            network_role="community-access",
            endpoints=endpoints,
            candidates=(),
            mandatory_network_place_ids=endpoints,
            mandatory_access_obligation_ids=("secondary-school",),
        )
    elif gap == "all-rejected":
        candidate_set_value = admit_candidate_set(
            selection_profile or profile(),
            network_role="community-access",
            endpoints=endpoints,
            candidates=tuple(candidates),
            mandatory_network_place_ids=endpoints,
            mandatory_access_obligation_ids=("secondary-school",),
            mandatory_strategic_destination_ids=("university-campus",),
        )
    else:
        candidate_set_value = candidate_set(
            *candidates,
            selection_profile=selection_profile or profile(),
            places=endpoints,
        )
    return PreparedSpineAccessConnection(
        access_connection_id=f"prepared-community-access-{label}",
        candidate_set=candidate_set_value,
        root_spine_id="root-spine",
        strategic_source_id="source",
        strategic_evidence_id="evidence",
        strategic_provenance={},
        obligation_kind="community",
        parent_role="spine-access-connection",
        community_id=endpoints[0],
        place_id=endpoints[0],
        parent_place_id=endpoints[1],
        candidate_generation_rationales=(),
        candidate_records=(),
    )


def roster_for(
    item: PreparedSpineAccessConnection,
) -> PreparedConnectionRosterRecord:
    return PreparedConnectionRosterRecord(
        access_connection_id=item.access_connection_id,
        obligation_kind=item.obligation_kind,
        parent_role=item.parent_role,
        community_id=item.community_id,
        place_id=item.place_id,
        parent_place_id=item.parent_place_id,
        disposition=(
            "prepared-candidate-set"
            if item.candidate_set.admitted_candidates
            else "prepared-candidate-set-gap"
        ),
    )


def preparation(
    *items: PreparedSpineAccessConnection,
    roster: tuple[PreparedConnectionRosterRecord, ...] | None = None,
    issues: tuple[CandidatePreparationIssue, ...] = (),
    status: str = "prepared",
    fingerprints: tuple[str, ...] | None = None,
) -> SpineAccessCandidatePreparationResult:
    lineage_item = items[0] if items else connection("lineage-source")
    roster = roster if roster is not None else tuple(roster_for(item) for item in items)
    diagnostics = {
        "expected_connection_roster_count": len(roster),
        "prepared_connection_count": sum(
            item.disposition.startswith("prepared-") for item in roster
        ),
        "out_of_scope_connection_count": sum(
            item.disposition == "out-of-scope-direct-strategic-spine"
            for item in roster
        ),
        "unresolved_connection_count": sum(
            item.disposition == "unresolved-gap" for item in roster
        ),
    }
    unbound = SpineAccessCandidatePreparationResult(
        contract="satn-spine-access-candidate-preparation/v1",
        profile_fingerprint=(items[0].candidate_set.profile_fingerprint if items else "a" * 64),
        status=status,
        prepared_spine_access_connections=tuple(items),
        connection_roster=roster,
        generation_issues=issues,
        missing_inputs=(),
        evidence_fingerprints=(
            evidence_fingerprints() if fingerprints is None else fingerprints
        ),
        evidence_lineage=governed_lineage(lineage_item),
        preparation_fingerprint="0" * 64,
        diagnostics=diagnostics,
    )
    return replace(
        unbound,
        preparation_fingerprint=canonical_hash(unbound.canonical_payload()),
    )


def bound_snapshot(
    prepared: PreparedSpineAccessConnection,
    snapshot: GovernedEvidenceSnapshot,
    *,
    use_base_education_source: bool = False,
) -> GovernedEvidenceSnapshot:
    education_source = (
        education_base_source(prepared)["source_content_fingerprint"]
        if use_base_education_source
        else snapshot.assessment(AssessmentKind.EDUCATION_ACCESS).source_content_sha256
    )
    expected = {
        AssessmentKind.POPULATION_REACH: POPULATION_SOURCE,
        AssessmentKind.EDUCATION_ACCESS: education_source,
        AssessmentKind.NETWORK_GEOMETRY: (
            prepared_network_geometry_source_fingerprint(prepared)
        ),
        AssessmentKind.TOPOGRAPHY: prepared_topography_source_fingerprint(prepared),
    }
    assessment_ids = (
        {
            AssessmentKind.NETWORK_GEOMETRY: (
                f"network-{prepared.access_connection_id}"
            ),
            AssessmentKind.TOPOGRAPHY: f"topography-{prepared.access_connection_id}",
        }
        if prepared.access_connection_id != "prepared-community-access-one"
        else {}
    )
    return GovernedEvidenceSnapshot(
        snapshot_id=f"bound-{prepared.access_connection_id}",
        assessments=tuple(
            GovernedAssessmentBinding(
                kind=item.kind,
                assessment_id=assessment_ids.get(item.kind, item.assessment_id),
                assessment_content_sha256=item.assessment_content_sha256,
                source_content_sha256=expected[item.kind],
                method_version=item.method_version,
            )
            for item in snapshot.assessments
        ),
    )


def bound_criteria(
    prepared: PreparedSpineAccessConnection,
    base: CandidateCriteria | None = None,
    **changes,
) -> CandidateCriteria:
    base = base or criteria(prepared.candidate_set, **changes)
    snapshot = bound_snapshot(prepared, base.evidence_snapshot)
    network_assessment = snapshot.assessment(AssessmentKind.NETWORK_GEOMETRY)
    topography_assessment = snapshot.assessment(AssessmentKind.TOPOGRAPHY)
    assert network_assessment is not None and topography_assessment is not None
    return CandidateCriteria(
        evidence_snapshot=snapshot,
        population=base.population.model_copy(
            update={
                "scenario_evidence_snapshot_fingerprint": snapshot.snapshot_fingerprint
            }
        ),
        education=base.education.model_copy(
            update={
                "scenario_evidence_snapshot_fingerprint": snapshot.snapshot_fingerprint
            }
        ),
        existing_alignment=base.existing_alignment,
        directness=tuple(
            item.model_copy(
                update={"assessment_id": network_assessment.assessment_id}
            )
            for item in base.directness
        ),
        gradient=tuple(
            item.model_copy(
                update={"assessment_id": topography_assessment.assessment_id}
            )
            for item in base.gradient
        ),
        uncertainty=tuple(
            item.model_copy(
                update={"assessment_id": network_assessment.assessment_id}
            )
            for item in base.uncertainty
        ),
    )


def gap_evidence(prepared: PreparedSpineAccessConnection) -> CandidateSetGapEvidence:
    seed = connection("seed")
    snapshot = bound_snapshot(
        prepared,
        criteria(seed.candidate_set).evidence_snapshot,
        use_base_education_source=True,
    )
    candidate_set_value = prepared.candidate_set
    return CandidateSetGapEvidence(
        candidate_set=candidate_set_value,
        evidence_snapshot=snapshot,
        rejected_candidate_ids=tuple(
            item.candidate_id
            for item in candidate_set_value.admissions
            if item.disposition.value == "rejected"
        ),
        unsatisfied_network_place_ids=candidate_set_value.mandatory_network_place_ids,
        unsatisfied_access_obligation_ids=(
            candidate_set_value.mandatory_access_obligation_ids
        ),
        unsatisfied_strategic_destination_ids=(
            candidate_set_value.mandatory_strategic_destination_ids
        ),
        generation_gap_reason=candidate_set_value.generation_gap_reason,
    )


def request(
    packets: tuple[PreparedCandidateCriteria, ...] = (),
    *,
    decision_record=None,
    run_id: str = "prepared-scenario-review",
    prior=None,
) -> PreparedScenarioCompilationInput:
    return PreparedScenarioCompilationInput(
        area_fingerprint=AREA,
        criteria=packets,
        decision_record=decision_record,
        review_run_instance_id=run_id,
        prior_orchestration=prior,
    )


def packet(
    prepared: PreparedSpineAccessConnection,
    criterion: CandidateCriteria | CandidateSetGapEvidence,
) -> PreparedCandidateCriteria:
    return PreparedCandidateCriteria(prepared.access_connection_id, criterion)


def test_profile_disabled_is_a_no_op_artifact() -> None:
    result = compile_prepared_scenario(None, request())

    assert result.status == "disabled"
    assert result.scenario is None
    assert result.reference_satn_created is False
    assert result.can_mutate_authoritative_network is False


def test_direct_spine_attachment_is_preserved_but_never_promoted() -> None:
    direct = PreparedConnectionRosterRecord(
        access_connection_id="direct-to-spine",
        obligation_kind="community",
        parent_role="strategic-spine",
        community_id="community-direct",
        place_id="community-direct",
        parent_place_id=None,
        disposition="out-of-scope-direct-strategic-spine",
        reason="out-of-scope-direct-strategic-spine-attachment",
    )
    issue = CandidatePreparationIssue(
        access_connection_id="direct-to-spine",
        reason="out-of-scope-direct-strategic-spine-attachment",
        detail="Direct-to-spine is not a strategic Community Connection.",
    )
    result = compile_prepared_scenario(
        preparation(roster=(direct,), issues=(issue,)),
        request(),
    )

    assert result.status == "incomplete"
    assert result.scenario is None
    assert result.missing_inputs == ("eligible-chained-community-connection",)
    assert result.diagnostics["out_of_scope_connections"]


def test_missing_roster_and_empty_evidence_fingerprints_fail_closed() -> None:
    item = connection()
    with pytest.raises(ValueError, match="exhaustive unique connection roster"):
        compile_prepared_scenario(
            preparation(item, roster=()),
            request((packet(item, bound_criteria(item)),)),
        )
    with pytest.raises(ValueError, match="empty, foreign or stale"):
        compile_prepared_scenario(
            preparation(item, fingerprints=()),
            request((packet(item, bound_criteria(item)),)),
        )


def test_missing_parent_roster_gap_is_explicitly_incomplete() -> None:
    missing = PreparedConnectionRosterRecord(
        access_connection_id="missing-parent",
        obligation_kind="community",
        parent_role="spine-access-connection",
        community_id="community-one",
        place_id="community-one",
        parent_place_id=None,
        disposition="unresolved-gap",
        reason="missing-parent-network-place-endpoint",
    )
    issue = CandidatePreparationIssue(
        access_connection_id="missing-parent",
        reason="missing-parent-network-place-endpoint",
        detail="Parent Community Network Place is missing.",
    )
    result = compile_prepared_scenario(
        preparation(roster=(missing,), issues=(issue,)),
        request(),
    )

    assert result.status == "incomplete"
    assert result.missing_inputs == ("unresolved-preparation:missing-parent",)
    assert result.diagnostics["unresolved_connections"]


@pytest.mark.parametrize(
    ("gap", "expected"),
    [
        ("no-options", CandidateGenerationGapReason.NO_GENERATED_CANDIDATES),
        ("all-rejected", CandidateGenerationGapReason.ALL_GENERATED_CANDIDATES_REJECTED),
    ],
)
def test_gap_only_candidate_sets_remain_review_required(
    gap: Literal["no-options", "all-rejected"],
    expected: CandidateGenerationGapReason,
) -> None:
    item = connection(gap=gap)
    assert item.candidate_set.generation_gap_reason == expected
    result = compile_prepared_scenario(
        preparation(item),
        request((packet(item, gap_evidence(item)),)),
    )

    assert result.status == "review-required"
    assert result.scenario is not None and result.scenario.publishable is False
    assert result.review_orchestration is not None
    actions = {
        option.action.value
        for option in result.review_orchestration.actionable_requests[0].request.options
    }
    assert actions == {"expose-network-gap", "terminate"}


def test_clear_and_multi_set_compilations_use_exact_source_bindings() -> None:
    first = connection("one", selection_profile=profile(review_when=[]))
    second = connection("two", selection_profile=first.candidate_set.profile)
    result = compile_prepared_scenario(
        preparation(first, second),
        request(
            (
                packet(first, bound_criteria(first)),
                packet(second, bound_criteria(second)),
            )
        ),
    )

    assert result.status == "compiled"
    assert result.scenario is not None
    assert len(result.scenario.selections) == 2
    assert result.review_orchestration is None
    assert result.diagnostics["agent_runtime_constructed"] is False


def test_forged_criterion_source_hash_fails_closed() -> None:
    item = connection()
    exact = bound_criteria(item)
    bindings = tuple(
        entry.model_copy(update={"source_content_sha256": "0" * 64})
        if entry.kind == AssessmentKind.NETWORK_GEOMETRY
        else entry
        for entry in exact.evidence_snapshot.assessments
    )
    forged_snapshot = GovernedEvidenceSnapshot(
        snapshot_id="forged-network-source",
        assessments=bindings,
    )
    forged = CandidateCriteria(
        evidence_snapshot=forged_snapshot,
        population=exact.population.model_copy(
            update={
                "scenario_evidence_snapshot_fingerprint": (
                    forged_snapshot.snapshot_fingerprint
                )
            }
        ),
        education=exact.education.model_copy(
            update={
                "scenario_evidence_snapshot_fingerprint": (
                    forged_snapshot.snapshot_fingerprint
                )
            }
        ),
        directness=exact.directness,
        gradient=exact.gradient,
        uncertainty=exact.uncertainty,
    )

    with pytest.raises(ValueError, match="network-geometry criterion source"):
        compile_prepared_scenario(
            preparation(item),
            request((packet(item, forged),)),
        )


def test_ambiguous_ledger_replays_exactly_and_stale_ledger_fails_closed() -> None:
    item = connection(ambiguous=True)
    exact = bound_criteria(item)
    prepared = preparation(item)
    provisional = compile_prepared_scenario(
        prepared,
        request((packet(item, exact),)),
    )
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
        prepared,
        request((packet(item, exact),), decision_record=ledger),
    )
    second = compile_prepared_scenario(
        prepared,
        request((packet(item, exact),), decision_record=ledger),
    )
    assert first.status == second.status == "compiled"
    assert first.scenario is not None and second.scenario is not None
    assert first.scenario.scenario_fingerprint == second.scenario.scenario_fingerprint
    assert first.result_fingerprint == second.result_fingerprint

    changed = bound_criteria(
        item,
        counts_500={
            candidate.candidate_id: index + 100
            for index, candidate in enumerate(item.candidate_set.admitted_candidates)
        },
    )
    with pytest.raises(
        ValueError,
        match=r"stale|exact compiler-generated menu|clear no-agent selection",
    ):
        compile_prepared_scenario(
            prepared,
            request((packet(item, changed),), decision_record=ledger),
        )


def test_prior_orchestration_advances_and_preserves_round_history() -> None:
    item = connection(
        ambiguous=True,
        selection_profile=profile(maximum_review_rounds=2),
    )
    exact = bound_criteria(item)
    prepared = preparation(item)
    first = compile_prepared_scenario(
        prepared,
        request((packet(item, exact),), run_id="scenario-round-one"),
    )
    assert first.scenario is not None and first.review_orchestration is not None
    selection = first.scenario.selections[0]
    decision_request = first.review_orchestration.actionable_requests[0].request
    fallback = next(
        option
        for option in decision_request.options
        if option.action.value == "accept-profile-fallback"
    )
    envelope = accepted_envelope(
        selection,
        decision_request,
        fallback.option_id,
        scenario_context_fingerprint=first.scenario.scenario_context_fingerprint,
    )
    ledger = ScenarioDecisionRecord(
        mode=DecisionProcessMode.ACCEPTED_LEDGER,
        accepted_envelopes=(envelope,),
    )
    second = compile_prepared_scenario(
        prepared,
        request(
            (packet(item, exact),),
            decision_record=ledger,
            run_id="scenario-round-two",
            prior=first.review_orchestration,
        ),
    )

    assert second.status == "compiled"
    assert second.review_orchestration is not None
    assert second.review_orchestration.round_number == 2
    assert len(second.review_orchestration.round_history) == 1
    assert second.review_orchestration.converged is True


def test_prior_timeout_advances_to_maximum_round_intervention() -> None:
    item = connection(
        ambiguous=True,
        selection_profile=profile(maximum_review_rounds=1),
    )
    exact = bound_criteria(item)
    prepared = preparation(item)
    first = compile_prepared_scenario(
        prepared,
        request((packet(item, exact),), run_id="scenario-timeout-one"),
    )
    assert first.scenario is not None and first.review_orchestration is not None
    decision_request = first.review_orchestration.actionable_requests[0].request
    attempt = RuntimeDecisionAttempt(
        request=decision_request,
        outcome="provider-timeout",
        provider_failure_code="adapter-timeout",
        invocation_record=RuntimeInvocationRecord(
            invocation_id=f"timeout-{first.review_orchestration.review_run.run_id[-12:]}",
            review_run_id=first.review_orchestration.review_run.run_id,
            run_instance_id=first.review_orchestration.review_run.run_instance_id,
            run_scope_fingerprint=(
                first.review_orchestration.review_run.run_scope_fingerprint
            ),
            run_config_fingerprint=(
                first.review_orchestration.review_run.run_config_fingerprint
            ),
            attempt_number=1,
            maximum_attempts=first.review_orchestration.review_run.maximum_attempts,
            deadline_seconds=first.review_orchestration.review_run.deadline_seconds,
            frontier_fingerprint=review_frontier_fingerprint(
                first.review_orchestration
            ),
            request_fingerprint=decision_request.request_fingerprint,
            outcome="provider-timeout",
            failure_code="adapter-timeout",
            started_at_ms=1000,
            completed_at_ms=2000,
        ),
    )
    ledger = ScenarioDecisionRecord(
        mode=DecisionProcessMode.ACCEPTED_LEDGER,
        runtime_attempts=(attempt,),
    )
    second = compile_prepared_scenario(
        prepared,
        request(
            (packet(item, exact),),
            decision_record=ledger,
            run_id="scenario-timeout-two",
            prior=first.review_orchestration,
        ),
    )

    assert second.status == "review-required"
    assert second.review_orchestration is not None
    assert second.review_orchestration.round_number == 2
    assert len(second.review_orchestration.round_history) == 1
    assert second.review_orchestration.human_intervention_request is not None
    assert (
        second.review_orchestration.human_intervention_request.reason
        == "maximum-review-rounds-exhausted"
    )
    assert second.review_orchestration.scenario.decision_record.runtime_attempts == (
        attempt,
    )


def test_result_diagnostics_are_deeply_immutable_and_metadata_is_defensive() -> None:
    item = connection(selection_profile=profile(review_when=[]))
    result = compile_prepared_scenario(
        preparation(item),
        request((packet(item, bound_criteria(item)),)),
    )
    original = result.result_fingerprint

    assert isinstance(result.diagnostics, MappingProxyType)
    with pytest.raises(TypeError):
        result.diagnostics["reason"] = "mutated"  # type: ignore[index]
    metadata = result.metadata()
    diagnostics = metadata["diagnostics"]
    assert isinstance(diagnostics, dict)
    diagnostics["reason"] = "changed"
    assert result.result_fingerprint == original
    assert result.metadata()["diagnostics"]["reason"] is None
