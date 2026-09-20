from __future__ import annotations

from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from satn.candidate_discovery import (
    CandidateDiscoveryProfile,
    CandidateDiscoveryRequest,
    CandidateDiscoveryResult,
    CandidateSetGapEvidence,
    CorridorObligation,
    discover_candidate_sets,
)
from satn.network_selection import ComparatorDimension
from satn.officer_decisions import (
    OfficerDecision,
    OfficerDecisionLedger,
    OfficerDecisionTarget,
    OfficerDecisionType,
    OfficerTargetKind,
    SelectAlignmentAction,
)
from satn.planning_graph import (
    GraphComponentRecord,
    PlanningEdgeRecord,
    PlanningGraphSnapshot,
    PlanningNodeRecord,
)
from satn.strategic_mesh import StrategicMainNetworkProfile
from satn.strategic_network_planning import (
    EffectiveStrategicSection,
    PlanningAuthority,
    ReferenceRoute,
    StrategicNetworkPlanningRequest,
    StrategicPlanningFallbackProfile,
    _resolved_backbone_component_gap_ids,
    compile_strategic_network,
)


def edge(
    edge_id: str,
    start: str,
    end: str,
    geometry: str,
    *,
    highway: str | None,
    ref: str | None = None,
    bicycle: str | None = None,
    access: str | None = None,
    length_m: float,
) -> PlanningEdgeRecord:
    return PlanningEdgeRecord(
        source_edge_id=edge_id,
        directed_edge_id=edge_id,
        from_node_id=start,
        to_node_id=end,
        geometry_wkt=geometry,
        geometry_fingerprint=(edge_id + "-") * 32,
        length_mm=round(length_m * 1_000),
        highway=highway,
        ref=ref,
        access=access,
        bicycle=bicycle,
        foot=None,
        oneway=False,
        reciprocal_state="reciprocal",
        weak_component_id="main",
        strong_component_id="main",
    )


def fixture_graph() -> PlanningGraphSnapshot:
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
        edge("quiet-aq", "A", "Q", "LINESTRING (0 0, 20 20)", highway="residential", length_m=30),
        edge(
            "quiet-qd", "Q", "D", "LINESTRING (20 20, 100 0)", highway="residential", length_m=100
        ),
    )
    nodes = tuple(PlanningNodeRecord(node, "main", "main") for node in ("A", "B", "D", "Q"))
    return PlanningGraphSnapshot(
        graph_fingerprint="1" * 64,
        edge_records=edges,
        node_records=nodes,
        component_records=(
            GraphComponentRecord(
                "main",
                "weak",
                ("A", "B", "D", "Q"),
                tuple(item.directed_edge_id for item in edges),
                4,
                5,
            ),
        ),
        observation_matches=(),
        diagnostics=(),
        profile_fingerprint="2" * 64,
        source_export_fingerprint="3" * 64,
        route_control_fingerprint=None,
    )


def discovery(graph: PlanningGraphSnapshot, *obligations: CorridorObligation):
    return discover_candidate_sets(
        CandidateDiscoveryRequest(
            graph=graph,
            obligations=obligations,
            evidence_snapshot=type("Snapshot", (), {"fingerprint": "snapshot"})(),
            profile=CandidateDiscoveryProfile(),
        )
    )


def discovery_with_profile(
    graph: PlanningGraphSnapshot,
    selection_profile,
    *obligations: CorridorObligation,
):
    return discover_candidate_sets(
        CandidateDiscoveryRequest(
            graph=graph,
            obligations=obligations,
            evidence_snapshot=type("Snapshot", (), {"fingerprint": "snapshot"})(),
            profile=CandidateDiscoveryProfile(selection_profile=selection_profile),
        )
    )


def request(
    graph: PlanningGraphSnapshot,
    result,
    *,
    officer_decisions=None,
    reference_routes=(),
    fallback_profile=None,
    selection_profile=None,
    compiler_preferred_candidate_ids=(),
    officer_candidate_choices=(),
):
    return StrategicNetworkPlanningRequest(
        area_fingerprint="a" * 64,
        graph=graph,
        discovery=result,
        officer_decisions=officer_decisions,
        reference_routes=reference_routes,
        fallback_profile=fallback_profile or StrategicPlanningFallbackProfile(),
        selection_profile=selection_profile,
        compiler_preferred_candidate_ids=compiler_preferred_candidate_ids,
        officer_candidate_choices=officer_candidate_choices,
    )


def test_rural_mesh_reduces_avoidable_b_road_and_retains_access_support() -> None:
    graph = fixture_graph()
    graph = replace(
        graph,
        edge_records=(
            *graph.edge_records,
            edge(
                "rural-a",
                "R0",
                "R1",
                "LINESTRING (0 0, 3000 0)",
                highway="primary",
                ref="A1",
                length_m=3000,
            ),
            edge(
                "rural-b",
                "R2",
                "R3",
                "LINESTRING (0 500, 3000 500)",
                highway="secondary",
                ref="B1",
                length_m=3000,
            ),
            edge(
                "rural-support",
                "R4",
                "R5",
                "LINESTRING (0 2000, 3000 2000)",
                highway="residential",
                length_m=3000,
            ),
        ),
        graph_fingerprint="4" * 64,
    )
    discovered = discovery(graph, CorridorObligation("corridor-a-d", "A", "D"))
    profile = StrategicMainNetworkProfile()
    required_sections = (
        EffectiveStrategicSection(
            "rural-a",
            "rural-a",
            None,
            "interurban-spine",
            ("rural-a",),
            (),
            "LINESTRING (0 0, 3000 0)",
            PlanningAuthority.COMPILER,
            ("a-road",),
            "a-road",
            "upgrade-required",
            "upgrade-required",
            "rural",
        ),
        EffectiveStrategicSection(
            "rural-b",
            "rural-b",
            None,
            "interurban-spine",
            ("rural-b",),
            (),
            "LINESTRING (0 500, 3000 500)",
            PlanningAuthority.COMPILER,
            ("b-road",),
            "b-road",
            "upgrade-required",
            "upgrade-required",
            "rural",
        ),
        EffectiveStrategicSection(
            "rural-support",
            "rural-support",
            None,
            "community-access",
            ("rural-support",),
            (),
            "LINESTRING (0 2000, 3000 2000)",
            PlanningAuthority.COMPILER,
            ("local-connector",),
            "local-connector",
            "upgrade-required",
            "upgrade-required",
            "rural",
        ),
    )

    result = compile_strategic_network(
        StrategicNetworkPlanningRequest(
            graph=graph,
            discovery=discovered,
            area_fingerprint="a" * 64,
            required_sections=required_sections,
            mesh_profile=profile,
            mesh_profile_fingerprint=profile.fingerprint,
        )
    )

    assert tuple(
        section.section_id
        for section in result.effective_network.sections
        if section.section_id.startswith("rural-")
    ) == ("rural-a", "rural-support")
    assert result.lineage.mesh_profile_fingerprint == profile.fingerprint
    assert not any(
        diagnostic.code == "strategic-mesh-access-support-excluded"
        and diagnostic.subject_id == "rural-support"
        for diagnostic in result.diagnostics
    )
    assert any(
        diagnostic.code == "strategic-mesh-section-omitted" and diagnostic.subject_id == "rural-b"
        for diagnostic in result.diagnostics
    )
    assert not any(gap.network_role == "strategic-main-network" for gap in result.gaps)


def test_materialized_protected_required_self_loop_survives_mesh_assembly() -> None:
    base_graph = fixture_graph()
    graph = replace(
        base_graph,
        edge_records=(
            *base_graph.edge_records,
            edge(
                "required-loop-edge",
                "loop-junction",
                "loop-junction",
                "LINESTRING (200 0, 250 0, 200 0)",
                highway="primary",
                ref="A2",
                length_m=100,
            ),
        ),
        graph_fingerprint="5" * 64,
    )
    required_loop = EffectiveStrategicSection(
        "required-loop",
        "required-loop",
        None,
        "interurban-spine",
        ("required-loop-edge",),
        (),
        "LINESTRING (200 0, 250 0, 200 0)",
        PlanningAuthority.COMPILER,
        ("a-road",),
        "a-road",
        "upgrade-required",
        "upgrade-required",
        "rural",
    )

    result = compile_strategic_network(
        StrategicNetworkPlanningRequest(
            graph=graph,
            discovery=discovery(graph, CorridorObligation("corridor-a-d", "A", "D")),
            area_fingerprint="a" * 64,
            required_sections=(required_loop,),
            backbone_obligation_ids=("required-loop",),
        )
    )

    selected_loop = next(
        section
        for section in result.effective_network.sections
        if section.section_id == "required-loop"
    )
    assert selected_loop.routing_edge_ids == ("required-loop-edge",)
    assert selected_loop.geometry_wkt == "LINESTRING (200 0, 250 0, 200 0)"


def test_materialized_rural_candidate_reduction_keeps_selection_roster_consistent() -> None:
    graph = fixture_graph()
    discovered = discovery(
        graph,
        CorridorObligation("first-rural-corridor", "A", "D"),
        CorridorObligation("second-rural-corridor", "A", "D"),
    )

    result = compile_strategic_network(
        StrategicNetworkPlanningRequest(
            graph=graph,
            discovery=discovered,
            area_fingerprint="a" * 64,
        )
    )

    rural_sections = tuple(
        section
        for section in result.effective_network.sections
        if section.network_role == "interurban-spine"
    )
    assert len(rural_sections) == 1
    assert rural_sections[0].network_scope == "rural"
    assert len(result.selections) == 1
    assert result.selections[0].effective_candidate_id == rural_sections[0].candidate_id
    assert result.selections[0].authority is not PlanningAuthority.GAP
    effective_dispositions = tuple(
        item for item in result.unselected_candidates if item.disposition == "effective"
    )
    mesh_dispositions = tuple(
        item
        for item in result.unselected_candidates
        if item.reason == "omitted by Strategic Main Network mesh"
    )
    assert len(effective_dispositions) == 1
    assert len(mesh_dispositions) == 1


def test_governed_compiler_preference_is_applied_without_reordering_candidates() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("corridor-a-d", "A", "D"))
    road_id = next(
        item.candidate_id for item in discovered.candidate_records if item.edge_ids == ("a-road",)
    )
    candidate_set_id = discovered.candidate_sets[0].candidate_set_id

    result = compile_strategic_network(
        request(
            graph,
            discovered,
            compiler_preferred_candidate_ids=((candidate_set_id, road_id),),
        )
    )

    assert result.selections[0].compiler_candidate_id == road_id
    assert result.effective_network.sections[0].routing_edge_ids == ("a-road",)
    assert result.selections[0].selection_reason == (
        "compiler selection: supplied preference; selection rationale unavailable"
    )
    supplied_alternatives = tuple(
        item for item in result.unselected_candidates if item.disposition == "unselected"
    )
    assert supplied_alternatives
    assert all(
        item.comparison_reason is not None
        and "supplied preference rationale unavailable" in item.comparison_reason
        for item in supplied_alternatives
    )


def test_compiler_selection_exposes_governed_comparison_reason() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("corridor-a-d", "A", "D"))

    cycle_id = next(
        item.candidate_id
        for item in discovered.candidate_records
        if item.edge_ids == ("cycle-ab", "cycle-bd")
    )
    candidate_set_id = discovered.candidate_sets[0].candidate_set_id

    result = compile_strategic_network(request(graph, discovered))

    selection = result.selections[0]
    assert selection.candidate_set_id == candidate_set_id
    assert selection.compiler_candidate_id == cycle_id
    assert selection.effective_candidate_id == cycle_id
    assert selection.selection_reason.startswith("compiler selection: ")
    assert "reuse-class" in selection.selection_reason
    assert "existing-cycle-provision" in selection.selection_reason
    assert "a-road-major-protected-infrastructure" in selection.selection_reason
    assert selection.decision_id is None
    assert selection.decision_maker is None


def test_candidate_discovery_gaps_survive_into_reviewable_network() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("corridor-a-d", "A", "D"))
    repeated_gap = CandidateSetGapEvidence(
        obligation_id="destination-gap",
        endpoints=("D", "hospital"),
        reason="destination access evidence unavailable",
        search_diagnostic_ids=("diagnostic-destination",),
    )
    discovered = replace(
        discovered,
        gaps=(repeated_gap, repeated_gap),
    )

    result = compile_strategic_network(request(graph, discovered))

    assert result.status == "complete-with-gaps"
    assert len([item for item in result.gaps if item.obligation_id == "destination-gap"]) == 1
    assert any(item.obligation_id == "destination-gap" for item in result.evidence_requests)


def test_prepared_optional_interurban_gap_stays_diagnostic_only() -> None:
    graph = fixture_graph()
    discovered = discovery(
        graph,
        CorridorObligation("valid", "A", "D"),
        CorridorObligation("optional-gap", "X", "Y", mandatory=False),
    )
    preparation = SimpleNamespace(
        units=(SimpleNamespace(unit_id="valid", backbone_required=False),),
        issues=(),
    )

    result = compile_strategic_network(
        StrategicNetworkPlanningRequest(
            graph=graph,
            discovery=discovered,
            area_fingerprint="a" * 64,
            corridor_obligations=preparation,
        )
    )

    assert result.status == "complete"
    assert not any(item.obligation_id == "optional-gap" for item in result.gaps)
    assert any(item.obligation_id == "optional-gap" for item in result.evidence_requests)


def test_prepared_backbone_gap_remains_a_published_gap() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("backbone-gap", "X", "Y"))
    preparation = SimpleNamespace(
        units=(
            SimpleNamespace(
                unit_id="backbone-gap",
                backbone_required=True,
                candidate_set=discovered.candidate_sets[0],
            ),
        ),
        issues=(),
    )

    result = compile_strategic_network(
        StrategicNetworkPlanningRequest(
            graph=graph,
            discovery=discovered,
            area_fingerprint="a" * 64,
            corridor_obligations=preparation,
            backbone_obligation_ids=("backbone-gap",),
        )
    )

    assert result.status == "complete-with-gaps"
    assert [item.obligation_id for item in result.gaps] == ["backbone-gap"]


def test_unselected_junction_context_does_not_resolve_one_sided_component_gap() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("a-side", "A", "D"))
    preparation = SimpleNamespace(
        units=(
            SimpleNamespace(
                unit_id="junction-context",
                backbone_component_ids=("component-a", "component-b"),
                routing_start_node_id="A",
                routing_end_node_id="B",
            ),
            SimpleNamespace(
                unit_id="a-side",
                backbone_component_ids=("component-a",),
                routing_start_node_id="A",
                routing_end_node_id="D",
            ),
        ),
        issues=(
            SimpleNamespace(
                reason="a-road-backbone-component-unconnected",
                component_ids=("component-a", "component-b"),
                obligation_id="component-gap",
            ),
        ),
    )
    request = StrategicNetworkPlanningRequest(
        graph=graph,
        discovery=discovered,
        area_fingerprint="a" * 64,
        corridor_obligations=preparation,
    )
    a_side = EffectiveStrategicSection(
        "a-side-section",
        "a-side",
        None,
        "interurban-spine",
        ("a-road",),
        (),
        "LINESTRING (0 0, 100 0)",
        PlanningAuthority.COMPILER,
    )
    context = EffectiveStrategicSection(
        "junction-context-section",
        "junction-context",
        None,
        "interurban-spine",
        ("a-road",),
        (),
        "LINESTRING (0 0, 0 60)",
        PlanningAuthority.COMPILER,
    )

    assert "component-gap" not in _resolved_backbone_component_gap_ids(request, (a_side,))
    assert "component-gap" in _resolved_backbone_component_gap_ids(request, (context,))


def test_selected_obligation_suppresses_its_discovery_failure_gap() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("valid", "A", "D"))
    discovered = replace(
        discovered,
        gaps=(CandidateSetGapEvidence("valid", ("A", "D"), "no-path", ()),),
    )
    preparation = SimpleNamespace(
        units=(SimpleNamespace(unit_id="valid", backbone_required=False),),
        issues=(),
    )

    result = compile_strategic_network(
        StrategicNetworkPlanningRequest(
            graph=graph,
            discovery=discovered,
            area_fingerprint="a" * 64,
            corridor_obligations=preparation,
        )
    )

    assert result.status == "complete"
    assert not result.gaps


def test_unresolved_obligation_is_deduplicated_by_obligation_id() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("valid", "A", "D"))
    discovered = replace(
        discovered,
        gaps=(
            CandidateSetGapEvidence("unresolved", ("X", "Y"), "no-path", ()),
            CandidateSetGapEvidence(
                "unresolved",
                ("X", "Y"),
                "strategies produced no candidate within configured bounds",
                (),
            ),
        ),
    )

    result = compile_strategic_network(
        StrategicNetworkPlanningRequest(
            graph=graph,
            discovery=discovered,
            area_fingerprint="a" * 64,
        )
    )

    assert [item.obligation_id for item in result.gaps].count("unresolved") == 1


def test_cycleway_is_effective_and_a_road_remains_inspectable() -> None:
    graph = fixture_graph()
    result = compile_strategic_network(
        request(graph, discovery(graph, CorridorObligation("corridor-a-d", "A", "D")))
    )
    assert result.status == "complete"
    assert result.effective_network.sections[0].routing_edge_ids == ("cycle-ab", "cycle-bd")
    assert result.effective_network.sections[0].geometry_wkt == "LINESTRING (0 0, 0 60, 100 0)"
    alternatives = tuple(
        item
        for item in result.unselected_candidates
        if item.reason == "admitted alternative retained for review"
    )
    assert alternatives
    compiler_id = result.selections[0].compiler_candidate_id
    assert compiler_id is not None
    assert all(item.comparison_reason is not None for item in alternatives)
    assert all(compiler_id in item.comparison_reason for item in alternatives)
    assert all(item.candidate_id in item.comparison_reason for item in alternatives)


def test_cycleway_replaces_matching_injected_a_road_section() -> None:
    graph = fixture_graph()
    graph = replace(
        graph,
        edge_records=(
            *graph.edge_records,
            edge(
                "independent-a",
                "X",
                "Y",
                "LINESTRING (200 0, 300 0)",
                highway="primary",
                ref="A2",
                length_m=100,
            ),
        ),
        graph_fingerprint="6" * 64,
    )
    discovered = discovery(graph, CorridorObligation("corridor-a-d", "A", "D"))
    required_a = EffectiveStrategicSection(
        "urban-a-road",
        "urban-structure:urban-a-road",
        None,
        "urban-main-road-spine",
        ("a-road",),
        (),
        "LINESTRING (0 0, 100 0)",
        PlanningAuthority.COMPILER,
        ("a-road",),
        "a-road",
        "upgrade-required",
        "upgrade-required",
        "urban",
    )
    independent_a = EffectiveStrategicSection(
        "urban-independent-a",
        "urban-structure:urban-independent-a",
        None,
        "urban-main-road-spine",
        ("independent-a",),
        (),
        "LINESTRING (200 0, 300 0)",
        PlanningAuthority.COMPILER,
        ("a-road",),
        "a-road",
        "upgrade-required",
        "upgrade-required",
        "urban",
    )

    result = compile_strategic_network(
        replace(
            request(graph, discovered),
            required_sections=(required_a, independent_a),
        )
    )

    assert result.status == "complete"
    assert "urban-a-road" not in {
        section.section_id for section in result.effective_network.sections
    }
    assert any(
        section.routing_edge_ids == ("cycle-ab", "cycle-bd")
        for section in result.effective_network.sections
    )
    assert "urban-independent-a" in {
        section.section_id for section in result.effective_network.sections
    }
    road_candidate_id = next(
        item.candidate_id for item in discovered.candidate_records if item.edge_ids == ("a-road",)
    )
    assert any(
        item.candidate_id == road_candidate_id
        and item.disposition == "unselected"
        and item.reason == "admitted alternative retained for review"
        for item in result.unselected_candidates
    )


def test_access_attachment_keeps_exact_parent_corridor_with_cycle_substitute() -> None:
    base_graph = fixture_graph()
    new_edges = (
        *(
            edge_record
            for edge_record in base_graph.edge_records
            if edge_record.directed_edge_id != "a-road"
        ),
        edge(
            "a-road-am",
            "A",
            "M",
            "LINESTRING (0 0, 50 0)",
            highway="primary",
            ref="A1",
            length_m=50,
        ),
        edge(
            "a-road-md",
            "M",
            "D",
            "LINESTRING (50 0, 100 0)",
            highway="primary",
            ref="A1",
            length_m=50,
        ),
        edge(
            "feeder",
            "F",
            "M",
            "LINESTRING (50 -20, 50 0)",
            highway="residential",
            length_m=20,
        ),
    )
    graph = replace(
        base_graph,
        edge_records=new_edges,
        node_records=tuple(
            PlanningNodeRecord(node_id, "main", "main")
            for node_id in ("A", "B", "D", "F", "M", "Q")
        ),
        component_records=(
            GraphComponentRecord(
                "main",
                "weak",
                ("A", "B", "D", "F", "M", "Q"),
                tuple(item.directed_edge_id for item in new_edges),
                6,
                len(new_edges),
            ),
        ),
        graph_fingerprint="6" * 64,
    )
    discovered = discovery(graph, CorridorObligation("corridor-a-d", "A", "D"))
    parent_corridor = EffectiveStrategicSection(
        "urban-a-road",
        "urban-structure:urban-a-road",
        None,
        "urban-main-road-spine",
        ("a-road-am", "a-road-md"),
        (),
        "LINESTRING (0 0, 50 0, 100 0)",
        PlanningAuthority.COMPILER,
        ("a-road",),
        "a-road",
        "upgrade-required",
        "upgrade-required",
        "urban",
    )
    feeder = EffectiveStrategicSection(
        "feeder-access",
        "feeder-obligation",
        None,
        "community-access",
        ("feeder",),
        (),
        "LINESTRING (50 -20, 50 0)",
        PlanningAuthority.COMPILER,
        ("access-support",),
        "access-support",
        "upgrade-required",
        "upgrade-required",
        "urban",
        ("M",),
    )

    result = compile_strategic_network(
        StrategicNetworkPlanningRequest(
            graph=graph,
            discovery=discovered,
            area_fingerprint="a" * 64,
            required_sections=(parent_corridor, feeder),
        )
    )

    assert result.status == "complete"
    selected_main_edges = {
        edge_id
        for section in result.effective_network.sections
        if section.network_role != "community-access"
        for edge_id in section.routing_edge_ids
    }
    assert {"a-road-am", "a-road-md"} <= selected_main_edges


def test_graph_and_candidate_permutations_are_fingerprint_stable() -> None:
    graph = fixture_graph()
    first = compile_strategic_network(
        request(graph, discovery(graph, CorridorObligation("corridor-a-d", "A", "D")))
    )
    reversed_graph = replace(graph, edge_records=tuple(reversed(graph.edge_records)))
    second = compile_strategic_network(
        request(
            reversed_graph, discovery(reversed_graph, CorridorObligation("corridor-a-d", "A", "D"))
        )
    )
    assert first.fingerprint == second.fingerprint
    assert (
        first.effective_network.sections[0].geometry_wkt
        == second.effective_network.sections[0].geometry_wkt
    )


def test_officer_choice_applies_without_expiry_and_divergence_is_retained() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("corridor-a-d", "A", "D"))
    road_id = next(
        item.candidate_id for item in discovered.candidate_records if item.edge_ids == ("a-road",)
    )
    profile_fingerprint = discovered.candidate_sets[0].profile_fingerprint
    decision = OfficerDecision(
        decision_id="decision-road",
        decision_type=OfficerDecisionType.SELECT_ALIGNMENT,
        target=OfficerDecisionTarget(kind=OfficerTargetKind.ALIGNMENT_CANDIDATE, target_id=road_id),
        action=SelectAlignmentAction(),
        decision_maker="officer",
        decision_maker_role="transport",
        organisation="authority",
        decision_date=date(2020, 1, 1),
        rationale="retain the direct strategic corridor",
        evidence_ids=("officer-evidence",),
        source_url="https://example.test/decision",
        effective_from=date(2020, 1, 1),
        effective_until=date(2020, 12, 31),
        baseline_fingerprint="4" * 64,
        evidence_snapshot_fingerprint="5" * 64,
        profile_fingerprint=profile_fingerprint,
    )
    ledger = OfficerDecisionLedger(
        baseline_fingerprint="4" * 64,
        evidence_snapshot_fingerprint="5" * 64,
        profile_fingerprint=profile_fingerprint,
        decisions=(decision,),
    )
    result = compile_strategic_network(request(graph, discovered, officer_decisions=ledger))
    assert result.effective_network.sections[0].routing_edge_ids == ("a-road",)
    assert result.divergences[0].compiler_candidate_id != result.divergences[0].officer_candidate_id
    selection = result.selections[0]
    assert selection.selection_reason == "officer decision: retain the direct strategic corridor"
    assert selection.decision_id == "decision-road"
    assert selection.decision_maker == "officer"


def test_preloaded_officer_choice_keeps_missing_attribution_explicit() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("corridor-a-d", "A", "D"))
    cycle_id = next(
        item.candidate_id
        for item in discovered.candidate_records
        if item.edge_ids == ("cycle-ab", "cycle-bd")
    )

    result = compile_strategic_network(
        request(
            graph,
            discovered,
            officer_candidate_choices=((cycle_id, "decision-preloaded"),),
        )
    )

    selection = result.selections[0]
    assert selection.selection_reason == (
        "officer selection: supplied candidate choice; decision rationale unavailable"
    )
    assert selection.decision_id == "decision-preloaded"
    assert selection.decision_maker is None


def test_unknown_officer_target_is_diagnostic_and_compiler_continues() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("corridor-a-d", "A", "D"))
    profile_fingerprint = discovered.candidate_sets[0].profile_fingerprint
    decision = OfficerDecision(
        decision_id="decision-unknown",
        decision_type=OfficerDecisionType.SELECT_ALIGNMENT,
        target=OfficerDecisionTarget(
            kind=OfficerTargetKind.ALIGNMENT_CANDIDATE, target_id="candidate-missing"
        ),
        action=SelectAlignmentAction(),
        decision_maker="officer",
        decision_maker_role="transport",
        organisation="authority",
        decision_date=date(2020, 1, 1),
        rationale="review requested",
        evidence_ids=("officer-evidence",),
        source_url="https://example.test/decision",
        effective_from=date(2020, 1, 1),
        baseline_fingerprint="4" * 64,
        evidence_snapshot_fingerprint="5" * 64,
        profile_fingerprint=profile_fingerprint,
    )
    ledger = OfficerDecisionLedger(
        baseline_fingerprint="4" * 64,
        evidence_snapshot_fingerprint="5" * 64,
        profile_fingerprint=profile_fingerprint,
        decisions=(decision,),
    )
    result = compile_strategic_network(request(graph, discovered, officer_decisions=ledger))
    assert result.status == "complete-with-gaps"
    assert any(item.code == "unknown-officer-target" for item in result.diagnostics)


def test_reference_fallback_and_endpoint_gap_are_typed() -> None:
    graph = fixture_graph()
    missing = discovery(graph, CorridorObligation("missing", "A", "D"))
    route = ReferenceRoute("reference-missing", "missing", ("a-road",), "reference-source")
    fallback = compile_strategic_network(
        request(
            graph,
            missing,
            reference_routes=(route,),
            fallback_profile=StrategicPlanningFallbackProfile(
                fallback_order=("reference", "gap"),
            ),
        )
    )
    assert fallback.status == "reference-fallback"
    assert fallback.selections[0].authority == "governed-reference-provisional"
    gap = compile_strategic_network(
        request(
            replace(
                graph,
                edge_records=tuple(replace(item, access="no") for item in graph.edge_records),
            ),
            discovery(
                replace(
                    graph,
                    edge_records=tuple(replace(item, access="no") for item in graph.edge_records),
                ),
                CorridorObligation("missing", "A", "D"),
            ),
        )
    )
    assert gap.status == "complete-with-gaps"
    assert gap.gaps[0].obligation_id == "missing"


def test_one_bad_obligation_does_not_suppress_valid_selection() -> None:
    graph = fixture_graph()
    discovered = discovery(
        graph,
        CorridorObligation("valid", "A", "D"),
        CorridorObligation("bad", "X", "Y"),
    )
    result = compile_strategic_network(request(graph, discovered))
    assert result.status == "complete-with-gaps"
    assert any(item.obligation_id == "valid" for item in result.selections)
    assert any(item.obligation_id == "bad" for item in result.gaps)


def test_missing_required_role_is_an_explicit_gap() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("valid", "A", "D"))
    result = compile_strategic_network(
        request(
            graph,
            discovered,
            fallback_profile=StrategicPlanningFallbackProfile(
                required_roles=("strategic-destination-access",),
            ),
        )
    )
    assert result.status == "complete-with-gaps"
    assert any(item.obligation_id == "role:strategic-destination-access" for item in result.gaps)


def test_selection_profile_mismatch_is_rejected_at_request_boundary() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("valid", "A", "D"))
    mismatched = discovered.candidate_sets[0].profile.model_copy(update={"profile_id": "other"})
    with pytest.raises(ValueError, match="selection profile"):
        StrategicNetworkPlanningRequest(
            graph=graph,
            discovery=discovered,
            area_fingerprint="a" * 64,
            selection_profile=mismatched,
        )


def test_reference_is_not_allowed_to_bypass_known_access_prohibition() -> None:
    graph = fixture_graph()
    blocked = replace(
        graph,
        edge_records=tuple(replace(item, access="no") for item in graph.edge_records),
    )
    discovered = discovery(blocked, CorridorObligation("blocked", "A", "D"))
    result = compile_strategic_network(
        request(
            blocked,
            discovered,
            reference_routes=(ReferenceRoute("reference", "blocked", ("a-road",), "source"),),
        )
    )
    assert result.status == "complete-with-gaps"
    assert not any(
        item.authority.value == "governed-reference-provisional" for item in result.selections
    )
    assert any(item.code == "invalid-reference-route" for item in result.diagnostics)


def test_duplicate_reference_obligation_and_unknown_reference_are_retained_as_gaps() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("valid", "A", "D"))
    with pytest.raises(ValueError, match="obligation"):
        request(
            graph,
            discovered,
            reference_routes=(
                ReferenceRoute("reference-a", "valid", ("a-road",), "source"),
                ReferenceRoute("reference-b", "valid", ("cycle-ab", "cycle-bd"), "source"),
            ),
        )
    result = compile_strategic_network(
        request(
            graph,
            discovered,
            reference_routes=(
                ReferenceRoute("foreign", "foreign-obligation", ("a-road",), "source"),
            ),
        )
    )
    assert any(item.code == "foreign-reference-route" for item in result.diagnostics)


def test_gap_is_terminal_when_configured_before_other_fallbacks() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("valid", "A", "D"))
    result = compile_strategic_network(
        request(
            graph,
            discovered,
            fallback_profile=StrategicPlanningFallbackProfile(
                fallback_order=("gap", "compiler", "reference", "officer"),
            ),
        )
    )
    assert result.status == "complete-with-gaps"
    assert not result.effective_network.sections


def test_empty_discovery_and_malformed_admission_rosters_are_typed_gaps() -> None:
    graph = fixture_graph()
    empty = CandidateDiscoveryResult((), (), (), (), (), "e" * 64)
    empty_result = compile_strategic_network(request(graph, empty))
    assert empty_result.status == "complete-with-gaps"
    assert empty_result.gaps and empty_result.evidence_requests

    discovered = discovery(graph, CorridorObligation("valid", "A", "D"))
    malformed_set = discovered.candidate_sets[0].model_copy(update={"admissions": ()})
    malformed = replace(discovered, candidate_sets=(malformed_set,))
    malformed_result = compile_strategic_network(request(graph, malformed))
    assert malformed_result.status == "complete-with-gaps"
    assert any(item.code == "malformed-candidate-set" for item in malformed_result.diagnostics)


def test_candidate_discovery_binding_duplicates_do_not_overwrite() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("valid", "A", "D"))
    duplicate_sets = replace(
        discovered, candidate_sets=(discovered.candidate_sets[0], discovered.candidate_sets[0])
    )
    duplicate_dispositions = replace(
        discovered,
        obligation_dispositions=(
            discovered.obligation_dispositions[0],
            discovered.obligation_dispositions[0],
        ),
    )
    result = compile_strategic_network(request(graph, duplicate_sets))
    assert any(item.code == "duplicate-candidate-set" for item in result.diagnostics)
    result = compile_strategic_network(request(graph, duplicate_dispositions))
    assert any(item.code == "duplicate-obligation-disposition" for item in result.diagnostics)


def test_candidate_geometry_drift_is_a_per_obligation_gap() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("valid", "A", "D"))
    record = next(
        item for item in discovered.candidate_records if item.edge_ids == ("cycle-ab", "cycle-bd")
    )
    record_index = discovered.candidate_records.index(record)
    tampered_record = replace(record, geometry_wkt="LINESTRING (0 0, 0 61, 100 0)")
    tampered_records = list(discovered.candidate_records)
    tampered_records[record_index] = tampered_record
    tampered = replace(discovered, candidate_records=tuple(tampered_records))
    result = compile_strategic_network(request(graph, tampered))
    assert result.status == "complete-with-gaps"
    assert any(item.code == "candidate-geometry-mismatch" for item in result.diagnostics)


def test_custom_route_length_first_profile_controls_compiler_preference() -> None:
    graph = fixture_graph()
    default_profile = CandidateDiscoveryProfile().selection_profile
    assert default_profile is not None
    comparator_order = (
        ComparatorDimension.ROUTE_LENGTH,
        ComparatorDimension.MANDATORY_OBLIGATION_SERVICE,
        ComparatorDimension.REUSE_CLASS,
        ComparatorDimension.INTERVENTION_STATE,
        ComparatorDimension.ROUTE_DETOUR,
        ComparatorDimension.ROUTE_EFFORT,
        ComparatorDimension.TRANSITION_FRAGMENTATION_BURDEN,
        ComparatorDimension.GOVERNED_CONSTRAINTS,
        ComparatorDimension.TRAFFIC_CHALLENGE,
        ComparatorDimension.STABLE_CANDIDATE_ID,
    )
    profile = default_profile.model_copy(update={"comparator_order": comparator_order})
    discovered = discovery_with_profile(graph, profile, CorridorObligation("valid", "A", "D"))
    result = compile_strategic_network(request(graph, discovered, selection_profile=profile))
    assert result.effective_network.sections[0].routing_edge_ids == ("a-road",)
    assert result.selections[0].selection_reason.startswith(
        "compiler selection: route-length ranked candidate "
    )
    assert "(100m) ahead of candidate" in result.selections[0].selection_reason
    assert result.selections[0].selection_reason.endswith("(130m)")


def test_conflicting_active_officer_choices_are_not_overwritten() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("valid", "A", "D"))
    cycle_id = next(
        item.candidate_id
        for item in discovered.candidate_records
        if item.edge_ids == ("cycle-ab", "cycle-bd")
    )
    road_id = next(
        item.candidate_id for item in discovered.candidate_records if item.edge_ids == ("a-road",)
    )
    profile_fingerprint = discovered.candidate_sets[0].profile_fingerprint
    decision = OfficerDecision(
        decision_id="decision-cycle",
        decision_type=OfficerDecisionType.SELECT_ALIGNMENT,
        target=OfficerDecisionTarget(
            kind=OfficerTargetKind.ALIGNMENT_CANDIDATE, target_id=cycle_id
        ),
        action=SelectAlignmentAction(),
        decision_maker="officer",
        decision_maker_role="transport",
        organisation="authority",
        decision_date=date(2020, 1, 1),
        rationale="review requested",
        evidence_ids=("officer-evidence",),
        source_url="https://example.test/decision",
        effective_from=date(2020, 1, 1),
        baseline_fingerprint="4" * 64,
        evidence_snapshot_fingerprint="5" * 64,
        profile_fingerprint=profile_fingerprint,
    )
    conflicting = decision.model_copy(
        update={
            "decision_id": "decision-road",
            "target": OfficerDecisionTarget(
                kind=OfficerTargetKind.ALIGNMENT_CANDIDATE, target_id=road_id
            ),
        }
    )
    ledger = type(
        "Ledger",
        (),
        {"decisions": (decision, conflicting), "ledger_fingerprint": "ledger"},
    )()
    result = compile_strategic_network(request(graph, discovered, officer_decisions=ledger))
    assert result.status == "complete-with-gaps"
    assert any(item.code == "conflicting-officer-choice" for item in result.diagnostics)
    assert any(item.reason == "active officer choices are ambiguous" for item in result.gaps)


def test_duplicate_candidate_records_are_diagnostic_and_non_selectable() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("valid", "A", "D"))
    record = next(
        item for item in discovered.candidate_records if item.edge_ids == ("cycle-ab", "cycle-bd")
    )
    duplicate = replace(
        discovered,
        candidate_records=(
            record,
            record,
            *tuple(
                item
                for item in discovered.candidate_records
                if item.candidate_id != record.candidate_id
            ),
        ),
    )
    result = compile_strategic_network(request(graph, duplicate))
    assert result.status == "complete-with-gaps"
    assert any(item.code == "duplicate-candidate-record" for item in result.diagnostics)
    assert any(item.obligation_id == "valid" for item in result.gaps)


def test_malformed_reverse_chain_is_a_per_obligation_gap() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("valid", "A", "D"))
    record = next(
        item for item in discovered.candidate_records if item.edge_ids == ("cycle-ab", "cycle-bd")
    )
    tampered = replace(
        discovered,
        candidate_records=tuple(
            replace(item, reverse_edge_ids=("cycle-ab", "cycle-bd"))
            if item.candidate_id == record.candidate_id
            else item
            for item in discovered.candidate_records
        ),
    )
    result = compile_strategic_network(request(graph, tampered))
    assert result.status == "complete-with-gaps"
    assert any(item.code == "invalid-selected-candidate" for item in result.diagnostics)


def test_forged_obligation_binding_is_a_per_set_gap_and_other_sets_survive() -> None:
    graph = fixture_graph()
    discovered = discovery(
        graph,
        CorridorObligation("valid", "A", "D"),
        CorridorObligation("other", "A", "Q"),
    )
    valid_set_id = next(
        item.candidate_set_id
        for item in discovered.candidate_sets
        if any(
            record.obligation_id == "valid"
            for record in discovered.candidate_records
            if record.candidate_id in {candidate.candidate_id for candidate in item.candidates}
        )
    )
    forged_dispositions = tuple(
        replace(item, obligation_id="foreign-obligation")
        if item.candidate_set_id == valid_set_id
        else item
        for item in discovered.obligation_dispositions
    )
    forged = replace(discovered, obligation_dispositions=forged_dispositions)
    result = compile_strategic_network(request(graph, forged))
    assert result.status == "complete-with-gaps"
    assert any(
        item.code == "obligation-binding-mismatch" and item.subject_id == valid_set_id
        for item in result.diagnostics
    )
    assert any(item.candidate_set_id == valid_set_id for item in result.gaps)
    assert not any(item.candidate_set_id == valid_set_id for item in result.selections)
    assert any(item.obligation_id == "other" for item in result.selections)


def test_forged_candidate_record_obligation_cannot_be_effective() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("valid", "A", "D"))
    candidate = next(
        item for item in discovered.candidate_records if item.edge_ids == ("cycle-ab", "cycle-bd")
    )
    forged = replace(
        discovered,
        candidate_records=tuple(
            replace(item, obligation_id="foreign-obligation")
            if item.candidate_id == candidate.candidate_id
            else item
            for item in discovered.candidate_records
        ),
    )
    result = compile_strategic_network(request(graph, forged))
    set_id = discovered.candidate_sets[0].candidate_set_id
    assert result.status == "complete-with-gaps"
    assert any(
        item.code == "obligation-binding-mismatch" and item.subject_id == set_id
        for item in result.diagnostics
    )
    assert any(item.candidate_set_id == set_id for item in result.gaps)
    assert not result.selections


def test_missing_or_inconsistent_obligation_disposition_is_a_typed_gap() -> None:
    graph = fixture_graph()
    discovered = discovery(
        graph,
        CorridorObligation("valid", "A", "D"),
        CorridorObligation("other", "A", "Q"),
    )
    valid_set_id = next(
        item.candidate_set_id
        for item in discovered.candidate_sets
        if any(
            record.obligation_id == "valid"
            for record in discovered.candidate_records
            if record.candidate_id in {candidate.candidate_id for candidate in item.candidates}
        )
    )
    missing = replace(
        discovered,
        obligation_dispositions=tuple(
            item
            for item in discovered.obligation_dispositions
            if item.candidate_set_id != valid_set_id
        ),
    )
    missing_result = compile_strategic_network(request(graph, missing))
    assert any(
        item.code == "missing-obligation-disposition" and item.subject_id == valid_set_id
        for item in missing_result.diagnostics
    )
    assert any(item.candidate_set_id == valid_set_id for item in missing_result.gaps)
    assert not any(item.candidate_set_id == valid_set_id for item in missing_result.selections)

    disposition = next(
        item for item in discovered.obligation_dispositions if item.candidate_set_id == valid_set_id
    )
    inconsistent = replace(
        discovered,
        obligation_dispositions=(
            replace(disposition, disposition="gap"),
            *tuple(
                item
                for item in discovered.obligation_dispositions
                if item.candidate_set_id != valid_set_id
            ),
        ),
    )
    inconsistent_result = compile_strategic_network(request(graph, inconsistent))
    assert any(
        item.code == "obligation-disposition-mismatch" and item.subject_id == valid_set_id
        for item in inconsistent_result.diagnostics
    )
    assert any(item.candidate_set_id == valid_set_id for item in inconsistent_result.gaps)


def test_forged_candidate_set_identity_is_quarantined_per_set() -> None:
    graph = fixture_graph()
    discovered = discovery(
        graph,
        CorridorObligation("valid", "A", "D"),
        CorridorObligation("other", "A", "Q"),
    )
    valid_set_id = next(
        item.candidate_set_id
        for item in discovered.candidate_sets
        if any(
            record.obligation_id == "valid"
            for record in discovered.candidate_records
            if record.candidate_id in {candidate.candidate_id for candidate in item.candidates}
        )
    )
    forged_set = next(
        item for item in discovered.candidate_sets if item.candidate_set_id == valid_set_id
    ).model_copy(update={"candidate_set_id": "candidate-set-forged"})
    forged = replace(
        discovered,
        candidate_sets=tuple(
            forged_set if item.candidate_set_id == valid_set_id else item
            for item in discovered.candidate_sets
        ),
    )
    result = compile_strategic_network(request(graph, forged))
    assert any(
        item.code == "invalid-candidate-set" and item.subject_id == "candidate-set-forged"
        for item in result.diagnostics
    )
    assert any(item.candidate_set_id == "candidate-set-forged" for item in result.gaps)
    assert any(item.obligation_id == "other" for item in result.selections)
