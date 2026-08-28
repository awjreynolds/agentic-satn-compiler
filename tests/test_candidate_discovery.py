from __future__ import annotations

from dataclasses import replace

import pytest

from satn.alignment_selection import AlignmentCandidateSet as CanonicalAlignmentCandidateSet
from satn.candidate_discovery import (
    CandidateDiscoveryProfile,
    CandidateDiscoveryRequest,
    CandidateEdgeEvidence,
    CorridorObligation,
    discover_candidate_sets,
)
from satn.network_selection import InterventionState, ReuseFirstCandidateClass
from satn.planning_graph import (
    GraphComponentRecord,
    PlanningEdgeRecord,
    PlanningGraphSnapshot,
    PlanningNodeRecord,
)
from satn.strategic_network_planning import (
    StrategicNetworkPlanningRequest,
    compile_strategic_network,
)


def snapshot(identity: str = "snapshot-fixture") -> object:
    return type("Snapshot", (), {"fingerprint": identity})()


def edge(
    edge_id: str,
    start: str,
    end: str,
    geometry: str,
    *,
    highway: str | None,
    ref: str | None = None,
    bicycle: str | None = None,
    foot: str | None = None,
    access: str | None = None,
    length_m: float,
) -> PlanningEdgeRecord:
    return PlanningEdgeRecord(
        source_edge_id=edge_id,
        directed_edge_id=edge_id,
        from_node_id=start,
        to_node_id=end,
        geometry_wkt=geometry,
        geometry_fingerprint=edge_id * 64,
        length_mm=round(length_m * 1_000),
        highway=highway,
        ref=ref,
        access=access,
        bicycle=bicycle,
        foot=foot,
        oneway=False,
        reciprocal_state="reciprocal",
        weak_component_id="component-main",
        strong_component_id="component-main",
        asset_observation_ids=(),
        road_observation_ids=(),
        claim_observation_ids=(),
        unknown_claims=(),
    )


def fixture_graph() -> PlanningGraphSnapshot:
    # Four connected alternatives from A to D and an intentionally isolated
    # cycle asset E--F.  A direct A-road is shortest; the cycleway is longer.
    edges = (
        edge(
            "a-road", "A", "D", "LINESTRING (0 0, 100 0)", highway="primary", ref="A1", length_m=100
        ),
        edge(
            "cycle-ab",
            "A",
            "B",
            "LINESTRING (0 0, 0 60)",
            highway="cycleway",
            bicycle="designated",
            length_m=60,
        ),
        edge(
            "cycle-bd",
            "B",
            "D",
            "LINESTRING (0 60, 100 0)",
            highway="cycleway",
            bicycle="designated",
            length_m=110,
        ),
        edge(
            "prow-ac",
            "A",
            "C",
            "LINESTRING (0 0, 0 80)",
            highway="footway",
            foot="designated",
            ref="PROW-1",
            length_m=80,
        ),
        edge(
            "prow-cd",
            "C",
            "D",
            "LINESTRING (0 80, 100 0)",
            highway="footway",
            foot="designated",
            ref="PROW-1",
            length_m=100,
        ),
        edge("quiet-aq", "A", "Q", "LINESTRING (0 0, 20 20)", highway="residential", length_m=30),
        edge(
            "quiet-qd", "Q", "D", "LINESTRING (20 20, 100 0)", highway="residential", length_m=100
        ),
        edge(
            "isolated-e",
            "E",
            "F",
            "LINESTRING (500 500, 520 500)",
            highway="cycleway",
            bicycle="designated",
            length_m=20,
        ),
    )
    nodes = tuple(
        PlanningNodeRecord(
            node_id=node, weak_component_id="component-main", strong_component_id="component-main"
        )
        for node in ("A", "B", "C", "D", "Q", "E", "F")
    )
    components = (
        GraphComponentRecord(
            "component-main",
            "weak",
            ("A", "B", "C", "D", "Q"),
            tuple(item.directed_edge_id for item in edges[:-1]),
            5,
            7,
        ),
        GraphComponentRecord("component-island", "weak", ("E", "F"), ("isolated-e",), 2, 1),
    )
    return PlanningGraphSnapshot(
        graph_fingerprint="1" * 64,
        edge_records=edges,
        node_records=nodes,
        component_records=components,
        observation_matches=(),
        diagnostics=(),
        profile_fingerprint="2" * 64,
        source_export_fingerprint="3" * 64,
        route_control_fingerprint=None,
    )


def partial_cycleway_graph() -> PlanningGraphSnapshot:
    """A mixed route has one mapped cycleway section and two continuity links."""

    edges = (
        edge(
            "a-road-ad",
            "A",
            "D",
            "LINESTRING (0 0, 90 0)",
            highway="primary",
            ref="A1",
            length_m=90,
        ),
        edge(
            "cycle-ab",
            "A",
            "B",
            "LINESTRING (0 0, 0 40)",
            highway="cycleway",
            bicycle="designated",
            length_m=40,
        ),
        edge(
            "a-road-bc",
            "B",
            "C",
            "LINESTRING (0 40, 30 40)",
            highway="primary",
            ref="A1",
            length_m=30,
        ),
        edge(
            "local-cd",
            "C",
            "D",
            "LINESTRING (30 40, 90 0)",
            highway="residential",
            length_m=30,
        ),
    )
    nodes = tuple(
        PlanningNodeRecord(
            node_id=node,
            weak_component_id="component-main",
            strong_component_id="component-main",
        )
        for node in ("A", "B", "C", "D")
    )
    return PlanningGraphSnapshot(
        graph_fingerprint="4" * 64,
        edge_records=edges,
        node_records=nodes,
        component_records=(
            GraphComponentRecord(
                "component-main",
                "weak",
                ("A", "B", "C", "D"),
                tuple(item.directed_edge_id for item in edges),
                4,
                4,
            ),
        ),
        observation_matches=(),
        diagnostics=(),
        profile_fingerprint="5" * 64,
        source_export_fingerprint="6" * 64,
        route_control_fingerprint=None,
    )


def request(
    graph: PlanningGraphSnapshot, *, profile: CandidateDiscoveryProfile | None = None
) -> CandidateDiscoveryRequest:
    return CandidateDiscoveryRequest(
        graph=graph,
        obligations=(CorridorObligation("corridor-a-d", "A", "D"),),
        evidence_snapshot=snapshot(),
        profile=profile or CandidateDiscoveryProfile(),
    )


def test_trial_discovery_exposes_all_connected_alternatives_and_facts() -> None:
    result = discover_candidate_sets(request(fixture_graph()))
    assert result.status == "complete"
    records = result.candidate_records
    assert {item.reuse_class for item in records} >= {
        ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION,
        ReuseFirstCandidateClass.UPGRADEABLE_OFF_CARRIAGEWAY,
        ReuseFirstCandidateClass.LOW_TRAFFIC_NON_A_ROAD,
        ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE,
    }
    cycle = next(
        item
        for item in records
        if item.reuse_class == ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION
    )
    assert cycle.intervention_state == InterventionState.EXISTING_PROVISION
    prow = next(
        item
        for item in records
        if item.reuse_class == ReuseFirstCandidateClass.UPGRADEABLE_OFF_CARRIAGEWAY
    )
    assert prow.intervention_state == InterventionState.UPGRADE_REQUIRED
    assert cycle.sections
    assert any(item.code == "disconnected-asset" for item in result.search_diagnostics)
    assert result.evidence_requests


def test_partial_mapped_cycleway_beats_shorter_a_road_and_keeps_section_facts() -> None:
    graph = partial_cycleway_graph()
    result = discover_candidate_sets(
        CandidateDiscoveryRequest(
            graph=graph,
            obligations=(CorridorObligation("partial-corridor", "A", "D"),),
            evidence_snapshot=snapshot("partial-cycleway"),
            profile=CandidateDiscoveryProfile(),
        )
    )

    mixed = next(
        item
        for item in result.candidate_records
        if item.edge_ids == ("cycle-ab", "a-road-bc", "local-cd")
    )
    direct = next(item for item in result.candidate_records if item.edge_ids == ("a-road-ad",))
    admitted = result.candidate_sets[0].admitted_candidates

    assert mixed.reuse_class == ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION
    assert mixed.intervention_state == InterventionState.PROPOSED_NEW_LINK
    assert mixed.existing_provision_m == 40.0
    assert mixed.major_road_m == 30.0
    assert mixed.low_traffic_m == 30.0
    assert tuple(item.primary_alignment_basis for item in mixed.sections) == (
        "cycleway",
        "a-road",
        "quiet-road",
    )
    assert tuple(item.reuse_class for item in mixed.sections) == (
        ReuseFirstCandidateClass.EXISTING_CYCLE_PROVISION,
        ReuseFirstCandidateClass.A_ROAD_MAJOR_PROTECTED_INFRASTRUCTURE,
        ReuseFirstCandidateClass.LOW_TRAFFIC_NON_A_ROAD,
    )
    assert tuple(item.intervention_state for item in mixed.sections) == (
        InterventionState.EXISTING_PROVISION,
        InterventionState.PROPOSED_NEW_LINK,
        InterventionState.UPGRADE_REQUIRED,
    )
    assert mixed.length_m == 100.0
    assert direct.length_m == 90.0
    assert {item.candidate_id for item in admitted} == {mixed.candidate_id, direct.candidate_id}

    selected = compile_strategic_network(
        StrategicNetworkPlanningRequest(
            area_fingerprint="a" * 64,
            graph=graph,
            discovery=result,
        )
    )
    assert selected.status == "complete"
    assert selected.effective_network.sections[0].routing_edge_ids == mixed.edge_ids


def test_route_length_fact_is_bound_to_each_candidate_path() -> None:
    result = discover_candidate_sets(request(fixture_graph()))
    expected_lengths = {
        ("a-road",): 100.0,
        ("cycle-ab", "cycle-bd"): 170.0,
        ("quiet-aq", "quiet-qd"): 130.0,
    }
    for edge_ids, expected in expected_lengths.items():
        record = next(item for item in result.candidate_records if item.edge_ids == edge_ids)
        assert record.length_m == expected
        assert record.directness_m == expected
        assert record.candidate_input is not None
        assert record.candidate_input.directness_m == expected


def test_discovery_is_permutation_stable_and_facts_ignore_generating_strategy() -> None:
    graph = fixture_graph()
    first = discover_candidate_sets(request(graph))
    reversed_graph = replace(graph, edge_records=tuple(reversed(graph.edge_records)))
    second = discover_candidate_sets(request(reversed_graph))
    assert first.fingerprint == second.fingerprint
    assert tuple(item.candidate_id for item in first.candidate_records) == tuple(
        item.candidate_id for item in second.candidate_records
    )
    assert all(item.generating_strategy_ids for item in first.candidate_records)


def test_missing_path_is_typed_and_known_prohibition_is_not_admitted() -> None:
    graph = fixture_graph()
    prohibited = replace(
        graph,
        edge_records=tuple(
            replace(item, access="no") if item.directed_edge_id == "a-road" else item
            for item in graph.edge_records
        ),
    )
    result = discover_candidate_sets(
        CandidateDiscoveryRequest(
            graph=prohibited,
            obligations=(CorridorObligation("unreachable", "X", "Y"),),
            evidence_snapshot=snapshot("snapshot-missing"),
            profile=CandidateDiscoveryProfile(maximum_paths_per_strategy=1),
        )
    )
    assert result.status == "complete-with-gaps"
    assert any(item.code == "no-path" for item in result.search_diagnostics)
    assert any(item.code == "known-access-prohibition" for item in result.search_diagnostics)


def test_search_budget_is_deterministic_and_retains_truncated_candidates() -> None:
    result = discover_candidate_sets(
        request(
            fixture_graph(),
            profile=CandidateDiscoveryProfile(
                maximum_paths_per_strategy=3,
                maximum_generated_candidates=2,
                maximum_node_settlements=3,
            ),
        )
    )
    assert result.candidate_records
    assert any(item.code == "search-truncated" for item in result.search_diagnostics)


def test_generation_budget_is_per_obligation_and_admission_suppression_is_explicit() -> None:
    graph = fixture_graph()
    result = discover_candidate_sets(
        CandidateDiscoveryRequest(
            graph=graph,
            obligations=(
                CorridorObligation("first", "A", "D", role="community-access"),
                CorridorObligation("second", "A", "D", role="school-access", mandatory=False),
            ),
            evidence_snapshot=type("Snapshot", (), {"fingerprint": "evidence-v1"})(),
            profile=CandidateDiscoveryProfile(
                maximum_generated_candidates=3,
                maximum_admitted_options=1,
            ),
        )
    )
    assert {item.obligation_id for item in result.candidate_records} == {"first", "second"}
    assert any(item.code == "admission-limit-suppressed" for item in result.search_diagnostics)
    assert {item.network_role.value for item in result.candidate_sets} == {
        "community-access",
        "school-access",
    }


def test_deviation_budget_retains_first_path_and_records_suppressed_deviations() -> None:
    result = discover_candidate_sets(
        request(
            fixture_graph(),
            profile=CandidateDiscoveryProfile(
                maximum_paths_per_strategy=3,
                maximum_deviations=0,
            ),
        )
    )
    assert result.candidate_records
    assert any(item.code == "search-truncated" for item in result.search_diagnostics)
    assert any(item.code == "deviation-suppressed" for item in result.search_diagnostics)


def test_prohibited_route_is_retained_as_ineligible_candidate_with_lineage() -> None:
    graph = fixture_graph()
    prohibited = replace(
        graph,
        edge_records=tuple(
            replace(
                item,
                access="no" if item.directed_edge_id == "a-road" else item.access,
                access_observation_ids=("access-claim",)
                if item.directed_edge_id == "a-road"
                else item.access_observation_ids,
                claim_observation_ids=(("access", ("claim-access",)),)
                if item.directed_edge_id == "a-road"
                else item.claim_observation_ids,
            )
            for item in graph.edge_records
        ),
    )
    result = discover_candidate_sets(
        CandidateDiscoveryRequest(
            graph=prohibited,
            obligations=(CorridorObligation("prohibited", "A", "D"),),
            evidence_snapshot=type("Snapshot", (), {"snapshot_fingerprint": "snapshot-v2"})(),
            profile=CandidateDiscoveryProfile(maximum_paths_per_strategy=1),
        )
    )
    prohibited_record = next(
        item for item in result.candidate_records if item.edge_ids == ("a-road",)
    )
    assert prohibited_record.known_access_prohibition
    assert "access-claim" in prohibited_record.evidence_ids
    assert "claim-access" in prohibited_record.evidence_ids
    assert prohibited_record.candidate_id not in {
        item.candidate_id for item in result.candidate_sets[0].admitted_candidates
    }
    assert result.evidence_snapshot_fingerprint == "snapshot-v2"
    assert prohibited_record.evidence_snapshot_fingerprint == "snapshot-v2"
    assert prohibited_record.sections[0].evidence_snapshot_fingerprint == "snapshot-v2"


def test_evidence_snapshot_identity_changes_candidate_lineage_and_result() -> None:
    graph = fixture_graph()
    first = discover_candidate_sets(
        CandidateDiscoveryRequest(
            graph=graph,
            obligations=(CorridorObligation("identity", "A", "D"),),
            evidence_snapshot=type("Snapshot", (), {"fingerprint": "snapshot-a"})(),
            profile=CandidateDiscoveryProfile(),
        )
    )
    second = discover_candidate_sets(
        CandidateDiscoveryRequest(
            graph=graph,
            obligations=(CorridorObligation("identity", "A", "D"),),
            evidence_snapshot=type("Snapshot", (), {"fingerprint": "snapshot-b"})(),
            profile=CandidateDiscoveryProfile(),
        )
    )
    assert first.fingerprint != second.fingerprint
    assert first.candidate_records[0].candidate_id != second.candidate_records[0].candidate_id
    assert first.candidate_sets[0].candidate_set_id != second.candidate_sets[0].candidate_set_id


def test_edge_enrichment_populates_claims_and_is_strategy_independent() -> None:
    graph = fixture_graph()
    enrichment = tuple(
        CandidateEdgeEvidence(
            edge_id=edge_record.directed_edge_id,
            absolute_elevation_change_m=2.0,
            traffic_observation_ids=(f"traffic-{edge_record.directed_edge_id}",),
            traffic_state="observed",
            constraint_observation_ids=(f"constraint-{edge_record.directed_edge_id}",),
            constraint_state="clear",
            gradient_band="flat",
            network_scope="strategic",
            evidence_ids=(f"enrichment-{edge_record.directed_edge_id}",),
        )
        for edge_record in graph.edge_records
    )
    first = discover_candidate_sets(
        CandidateDiscoveryRequest(
            graph=graph,
            obligations=(CorridorObligation("enriched", "A", "D"),),
            evidence_snapshot=type("Snapshot", (), {"fingerprint": "snapshot-enriched"})(),
            profile=CandidateDiscoveryProfile(),
            edge_evidence=enrichment,
        )
    )
    reversed_result = discover_candidate_sets(
        CandidateDiscoveryRequest(
            graph=replace(graph, edge_records=tuple(reversed(graph.edge_records))),
            obligations=(CorridorObligation("enriched", "A", "D"),),
            evidence_snapshot=type("Snapshot", (), {"fingerprint": "snapshot-enriched"})(),
            profile=CandidateDiscoveryProfile(),
            edge_evidence=tuple(reversed(enrichment)),
        )
    )
    assert first.fingerprint == reversed_result.fingerprint
    assert first.candidate_records
    candidate = first.candidate_records[0]
    assert candidate.total_absolute_elevation_change_m is not None
    assert candidate.total_absolute_elevation_change_m > 0
    assert candidate.traffic_observation_ids
    assert candidate.constraint_observation_ids
    assert "elevation" not in candidate.unknown_facts
    assert "traffic" not in candidate.unknown_facts
    assert "constraints" not in candidate.unknown_facts
    assert candidate.evidence_ids
    assert first.edge_evidence_fingerprint
    assert candidate.sections[0].gradient_band == "flat"
    assert candidate.sections[0].network_scope == "strategic"
    changed_enrichment = tuple(
        replace(item, traffic_state="conflicting") if item.edge_id == "cycle-ab" else item
        for item in enrichment
    )
    changed = discover_candidate_sets(
        CandidateDiscoveryRequest(
            graph=graph,
            obligations=(CorridorObligation("enriched", "A", "D"),),
            evidence_snapshot=type("Snapshot", (), {"fingerprint": "snapshot-enriched"})(),
            profile=CandidateDiscoveryProfile(),
            edge_evidence=changed_enrichment,
        )
    )
    assert changed.fingerprint != first.fingerprint


def test_edge_enrichment_rejects_duplicate_and_foreign_bindings() -> None:
    graph = fixture_graph()
    duplicate = CandidateEdgeEvidence(edge_id="a-road")
    with pytest.raises(ValueError, match="duplicate"):
        CandidateDiscoveryRequest(
            graph=graph,
            obligations=(CorridorObligation("duplicate", "A", "D"),),
            evidence_snapshot=snapshot("snapshot-duplicate"),
            profile=CandidateDiscoveryProfile(),
            edge_evidence=(duplicate, duplicate),
        )
    with pytest.raises(ValueError, match="foreign"):
        CandidateDiscoveryRequest(
            graph=graph,
            obligations=(CorridorObligation("foreign", "A", "D"),),
            evidence_snapshot=snapshot("snapshot-foreign"),
            profile=CandidateDiscoveryProfile(),
            edge_evidence=(CandidateEdgeEvidence(edge_id="not-in-graph"),),
        )


def test_discovery_uses_canonical_candidate_set_and_candidate_contract() -> None:
    result = discover_candidate_sets(request(fixture_graph()))
    assert all(isinstance(item, CanonicalAlignmentCandidateSet) for item in result.candidate_sets)
    candidate_set = result.candidate_sets[0]
    assert candidate_set.profile.contract == "satn-network-selection-profile/vNext"
    assert candidate_set.profile_fingerprint == candidate_set.profile.fingerprint
    for candidate in candidate_set.candidates:
        admitted_ids = {item.candidate_id for item in candidate_set.admitted_candidates}
        rejected_ids = {
            item.candidate_id
            for item in candidate_set.candidates
            if item.candidate_id
            not in {admission.candidate_id for admission in candidate_set.admissions}
        }
        assert candidate.candidate_id in admitted_ids | rejected_ids
        assert type(candidate).model_validate(candidate.model_dump(mode="python")) == candidate


def test_explicit_selection_profile_owns_order_and_admitted_limit() -> None:
    profile = request(fixture_graph()).profile.selection_profile
    assert profile is not None
    constrained = replace(
        request(fixture_graph()).profile,
        selection_profile=profile.model_copy(update={"maximum_options_per_candidate_set": 1}),
        maximum_admitted_options=1,
    )
    result = discover_candidate_sets(request(fixture_graph(), profile=constrained))
    assert all(len(item.admitted_candidates) <= 1 for item in result.candidate_sets)


def test_legacy_vnext_order_is_effective_and_unknown_candidates_remain_inspectable() -> None:
    base = CandidateDiscoveryProfile(maximum_admitted_options=3)
    assert base.selection_profile is not None
    supplied = base.selection_profile.model_copy(
        update={
            "candidate_class_order": tuple(
                item
                for item in base.selection_profile.candidate_class_order or ()
                if item != ReuseFirstCandidateClass.UNKNOWN_OR_CONFLICTING
            ),
            "intervention_state_order": tuple(
                item
                for item in base.selection_profile.intervention_state_order or ()
                if item != InterventionState.UNDETERMINED
            ),
        }
    )
    profile = CandidateDiscoveryProfile(
        maximum_admitted_options=3,
        selection_profile=supplied,
    )
    assert profile.selection_profile is not None
    assert profile.selection_profile.candidate_class_order == (
        *supplied.candidate_class_order,
        ReuseFirstCandidateClass.UNKNOWN_OR_CONFLICTING,
    )
    assert profile.selection_profile.intervention_state_order == (
        *supplied.intervention_state_order,
        InterventionState.UNDETERMINED,
    )
    assert profile.selection_profile.fingerprint != supplied.fingerprint

    graph = fixture_graph()
    unknown_graph = replace(
        graph,
        edge_records=tuple(
            replace(item, highway="track", ref=None) if item.directed_edge_id == "a-road" else item
            for item in graph.edge_records
        ),
    )
    first = discover_candidate_sets(
        CandidateDiscoveryRequest(
            graph=unknown_graph,
            obligations=(CorridorObligation("legacy-unknown", "A", "D"),),
            evidence_snapshot=snapshot("legacy-unknown-snapshot"),
            profile=profile,
        )
    )
    second = discover_candidate_sets(
        CandidateDiscoveryRequest(
            graph=replace(unknown_graph, edge_records=tuple(reversed(unknown_graph.edge_records))),
            obligations=(CorridorObligation("legacy-unknown", "A", "D"),),
            evidence_snapshot=snapshot("legacy-unknown-snapshot"),
            profile=profile,
        )
    )
    unknown = next(
        item
        for item in first.candidate_records
        if item.reuse_class == ReuseFirstCandidateClass.UNKNOWN_OR_CONFLICTING
    )
    assert unknown.candidate_input is not None
    assert unknown.candidate_input.intervention_state == InterventionState.UNDETERMINED
    assert unknown.candidate_id in {
        admission.candidate_id
        for admission in first.candidate_sets[0].admissions
        if admission.rationale.value == "profile-candidate-limit"
    }
    assert first.fingerprint == second.fingerprint
    assert tuple(item.candidate_id for item in first.candidate_records) == tuple(
        item.candidate_id for item in second.candidate_records
    )
