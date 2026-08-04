from __future__ import annotations

from dataclasses import replace

import pytest

from satn.corridor_obligations import (
    CorridorObligationProfile,
    CrossBoundaryGateway,
    DemandMatchState,
    FallbackOrigin,
    GovernedDemandFlow,
    ObligationPlace,
    PairDisposition,
    SeedDisposition,
    StrategicDestination,
    derive_corridor_obligations,
)
from satn.planning_graph import (
    GraphComponentRecord,
    PlanningEdgeRecord,
    PlanningGraphSnapshot,
    PlanningNodeRecord,
)


def empty_graph() -> PlanningGraphSnapshot:
    return PlanningGraphSnapshot(
        graph_fingerprint="1" * 64,
        edge_records=(),
        node_records=(),
        component_records=(),
        observation_matches=(),
        diagnostics=(),
        profile_fingerprint="2" * 64,
        source_export_fingerprint="3" * 64,
        route_control_fingerprint=None,
    )


def edge(edge_id: str, start: str, end: str, length_m: float) -> PlanningEdgeRecord:
    return PlanningEdgeRecord(
        source_edge_id=edge_id,
        directed_edge_id=edge_id,
        from_node_id=start,
        to_node_id=end,
        geometry_wkt=f"LINESTRING ({length_m} 0, {length_m + 1} 0)",
        geometry_fingerprint="a" * 64,
        length_mm=round(length_m * 1_000),
        highway="unclassified",
        ref=None,
        access=None,
        bicycle=None,
        foot=None,
        oneway=False,
        reciprocal_state="reciprocal",
        weak_component_id="main",
        strong_component_id="main",
    )


def fixture_graph() -> PlanningGraphSnapshot:
    edges = (
        edge("ab", "A", "B", 100),
        edge("bc", "B", "C", 100),
        edge("cd", "C", "D", 50),
        edge("ef", "E", "F", 100),
        edge("gh", "G", "H", 100),
        edge("long", "A", "I", 700),
        edge("ij", "I", "J", 700),
    )
    nodes = tuple(
        PlanningNodeRecord(node, "main" if node not in {"G", "H"} else "island", "main")
        for node in ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J")
    )
    components = (
        GraphComponentRecord(
            "main",
            "weak",
            ("A", "B", "C", "D", "E", "F", "I", "J"),
            tuple(item.directed_edge_id for item in edges if item.directed_edge_id != "gh"),
            8,
            6,
        ),
        GraphComponentRecord("island", "weak", ("G", "H"), ("gh",), 2, 1),
    )
    return PlanningGraphSnapshot(
        graph_fingerprint="4" * 64,
        edge_records=edges,
        node_records=nodes,
        component_records=components,
        observation_matches=(),
        diagnostics=(),
        profile_fingerprint="5" * 64,
        source_export_fingerprint="6" * 64,
        route_control_fingerprint=None,
    )


def test_no_demand_uses_declared_place_hierarchy_and_requests_evidence() -> None:
    profile = CorridorObligationProfile(
        profile_id="fixture",
        hierarchy_pairing_rules=(("town", "town"),),
        fallback_order=(FallbackOrigin.PLACE_HIERARCHY, FallbackOrigin.COVERAGE_ONLY),
    )
    result = derive_corridor_obligations(
        places=(
            ObligationPlace("place-a", "node-a", category="town", hierarchy_rank=1),
            ObligationPlace("place-b", "node-b", category="town", hierarchy_rank=1),
        ),
        destinations=(),
        gateways=(),
        demand=(),
        graph=empty_graph(),
        profile=profile,
    )

    assert result.fallback_origin is FallbackOrigin.PLACE_HIERARCHY
    assert result.status == "complete-with-gaps"
    assert result.obligations
    assert {
        (item.origin_endpoint_id, item.destination_endpoint_id) for item in result.obligations
    } == {("place-a", "place-b"), ("place-b", "place-a")}
    assert result.evidence_requests
    assert result.evidence_requests[0].claim == "matched-demand"
    assert result.fingerprint


def test_demand_roster_is_bounded_clustered_and_lossless_about_failures() -> None:
    profile = CorridorObligationProfile(
        profile_id="fixture-demand",
        hierarchy_pairing_rules=(("town", "town"),),
        gateway_pairing_rules=("place-gateway",),
        network_distance_limit_m=500,
        segment_length_m=500,
        threshold_method="absolute",
        threshold_value=10,
        clustering_epsilon_m=75,
        clustering_min_samples=2,
        maximum_path_seeds_per_pair=1,
    )
    places = (
        ObligationPlace("place-a", "A", category="town", hierarchy_rank=1),
        ObligationPlace("place-c", "C", category="town", hierarchy_rank=1),
        ObligationPlace("place-d", "D", category="town", hierarchy_rank=1),
    )
    demand = (
        GovernedDemandFlow("flow-ac", "A", "C", 100, ("flow-evidence-ac",)),
        GovernedDemandFlow("flow-ad", "A", "D", 80, ("flow-evidence-ad",)),
        GovernedDemandFlow("flow-ef", "E", "F", 50, ("flow-evidence-ef",)),
        GovernedDemandFlow("flow-bridge", "C", "D", 1, ("flow-evidence-bridge",)),
        GovernedDemandFlow(
            "flow-unmatched",
            None,
            "C",
            90,
            ("flow-evidence-unmatched",),
            DemandMatchState.UNMATCHED,
        ),
        GovernedDemandFlow("flow-island", "G", "H", 90, ("flow-evidence-island",)),
        GovernedDemandFlow("flow-over-distance", "A", "J", 90, ("flow-evidence-long",)),
        GovernedDemandFlow("flow-failed", "X", "Y", 90, ("flow-evidence-failed",)),
    )
    result = derive_corridor_obligations(
        places=places,
        destinations=(StrategicDestination("hospital", "F"),),
        gateways=(CrossBoundaryGateway("gateway", "E"),),
        demand=demand,
        graph=fixture_graph(),
        profile=profile,
    )

    assert result.status == "complete-with-gaps"
    assert result.fallback_origin is FallbackOrigin.DEMAND_LED
    assert result.obligations
    endpoint_count = len(places) + 1 + 1
    assert len(result.obligations) < endpoint_count * (endpoint_count - 1)
    assert any(item.disposition is SeedDisposition.BELOW_THRESHOLD for item in result.seed_records)
    assert any(item.disposition is SeedDisposition.UNMATCHED for item in result.seed_records)
    assert any(
        item.disposition is SeedDisposition.DISCONNECTED_ISLAND for item in result.seed_records
    )
    assert any(item.disposition is SeedDisposition.OVER_DISTANCE for item in result.seed_records)
    assert any(item.disposition is SeedDisposition.FAILED_PATH for item in result.seed_records)
    assert any(item.disposition is PairDisposition.BELOW_THRESHOLD for item in result.pair_records)
    assert any(item.disposition is PairDisposition.OVER_DISTANCE for item in result.pair_records)
    assert any(item.disposition is PairDisposition.FAILED_PATH for item in result.pair_records)
    assert "flow-unmatched" in {item.flow_id for item in result.unmatched_demand}
    assert len({item.cluster_id for item in result.seed_records if item.cluster_id}) < len(
        {
            item.seed_id
            for item in result.seed_records
            if item.disposition is SeedDisposition.ELIGIBLE
        }
    )


def test_demand_and_profile_permutations_are_fingerprint_stable() -> None:
    profile = CorridorObligationProfile(
        profile_id="fixture-permutation",
        network_distance_limit_m=1_000,
        segment_length_m=500,
        threshold_value=1,
        clustering_epsilon_m=100,
        clustering_min_samples=2,
    )
    demand = (
        GovernedDemandFlow("flow-ac", "A", "C", 100, ("e-ac",)),
        GovernedDemandFlow("flow-ad", "A", "D", 80, ("e-ad",)),
    )
    first = derive_corridor_obligations((), (), (), demand, fixture_graph(), profile)
    second = derive_corridor_obligations(
        (),
        tuple(reversed(())),
        (),
        tuple(reversed(demand)),
        fixture_graph(),
        profile,
    )
    assert first.fingerprint == second.fingerprint
    assert tuple(item.obligation_id for item in first.obligations) == tuple(
        item.obligation_id for item in second.obligations
    )


def test_population_context_is_not_demand() -> None:
    profile = CorridorObligationProfile(
        profile_id="fixture-population",
        hierarchy_pairing_rules=(("town", "town"),),
        threshold_value=1,
    )
    result = derive_corridor_obligations(
        places=(
            ObligationPlace("a", "A", category="town", context_population=100_000),
            ObligationPlace("b", "B", category="town", context_population=100_000),
        ),
        destinations=(),
        gateways=(),
        demand=(),
        graph=fixture_graph(),
        profile=profile,
    )
    assert result.fallback_origin is FallbackOrigin.PLACE_HIERARCHY
    assert not any(item.source_flow_ids for item in result.obligations)


def test_threshold_sensitivity_is_explicit_and_changes_the_roster() -> None:
    demand = (
        GovernedDemandFlow("small", "A", "C", 5, ("small-evidence",)),
        GovernedDemandFlow("large", "A", "D", 50, ("large-evidence",)),
    )
    low = CorridorObligationProfile(
        profile_id="fixture-sensitivity-low",
        threshold_value=1,
        sensitivity_profile_ids=("low-threshold",),
    )
    high = replace(low, threshold_value=10, sensitivity_profile_ids=("high-threshold",))
    first = derive_corridor_obligations((), (), (), demand, fixture_graph(), low)
    second = derive_corridor_obligations((), (), (), demand, fixture_graph(), high)
    assert first.fingerprint != second.fingerprint
    assert first.sensitivity_profile_ids == ("low-threshold",)
    assert second.sensitivity_profile_ids == ("high-threshold",)
    assert any(item.disposition is SeedDisposition.BELOW_THRESHOLD for item in second.seed_records)


def test_fallback_order_is_governed() -> None:
    profile = CorridorObligationProfile(
        profile_id="fixture-fallback-order",
        hierarchy_pairing_rules=(("town", "town"),),
        fallback_order=(FallbackOrigin.COVERAGE_ONLY, FallbackOrigin.PLACE_HIERARCHY),
    )
    result = derive_corridor_obligations(
        (
            ObligationPlace("a", "A", category="town"),
            ObligationPlace("b", "B", category="town"),
            ObligationPlace("c", "C", category="town"),
        ),
        (),
        (),
        (),
        fixture_graph(),
        profile,
    )
    assert result.fallback_origin is FallbackOrigin.COVERAGE_ONLY
    assert all(item.fallback_origin is FallbackOrigin.COVERAGE_ONLY for item in result.obligations)


def test_duplicate_and_ambiguous_endpoint_identities_are_rejected() -> None:
    profile = CorridorObligationProfile(profile_id="fixture-duplicates")
    with pytest.raises(ValueError, match="duplicate place"):
        derive_corridor_obligations(
            (ObligationPlace("a", "A"), ObligationPlace("a", "B")),
            (),
            (),
            (),
            empty_graph(),
            profile,
        )
    with pytest.raises(ValueError, match="duplicate destination"):
        derive_corridor_obligations(
            (),
            (StrategicDestination("d", "A"), StrategicDestination("d", "B")),
            (),
            (),
            empty_graph(),
            profile,
        )
    with pytest.raises(ValueError, match="duplicate gateway"):
        derive_corridor_obligations(
            (),
            (),
            (CrossBoundaryGateway("g", "A"), CrossBoundaryGateway("g", "B")),
            (),
            empty_graph(),
            profile,
        )
    with pytest.raises(ValueError, match="duplicate flow"):
        derive_corridor_obligations(
            (),
            (),
            (),
            (GovernedDemandFlow("f", "A", "C", 2), GovernedDemandFlow("f", "A", "D", 3)),
            fixture_graph(),
            profile,
        )
    with pytest.raises(ValueError, match="ambiguous"):
        derive_corridor_obligations(
            (ObligationPlace("a", "A"),),
            (),
            (CrossBoundaryGateway("g", "A"),),
            (),
            empty_graph(),
            profile,
        )


def test_duplicate_evidence_ids_are_rejected_at_record_boundaries() -> None:
    with pytest.raises(ValueError, match="duplicate evidence"):
        ObligationPlace("place", "A", evidence_ids=("evidence", "evidence"))
    with pytest.raises(ValueError, match="duplicate evidence"):
        StrategicDestination("destination", "A", evidence_ids=("evidence", "evidence"))
    with pytest.raises(ValueError, match="duplicate evidence"):
        CrossBoundaryGateway("gateway", "A", evidence_ids=("evidence", "evidence"))
    with pytest.raises(ValueError, match="duplicate evidence"):
        GovernedDemandFlow("flow", "A", "B", 1, evidence_ids=("evidence", "evidence"))
    with pytest.raises(ValueError, match="gateway pairing rules"):
        CorridorObligationProfile(gateway_pairing_rules=("place-gateway", "place-gateway"))
    with pytest.raises(ValueError, match="hierarchy pairing rules"):
        CorridorObligationProfile(hierarchy_pairing_rules=(("town", "town"), ("town", "town")))
    with pytest.raises(ValueError, match="sensitivity profile IDs"):
        CorridorObligationProfile(sensitivity_profile_ids=("trial", "trial"))


def test_graph_lineage_binds_result_obligation_and_seed_identity() -> None:
    flow = GovernedDemandFlow("f", "A", "C", 20, ("evidence-a",))
    profile = CorridorObligationProfile(
        profile_id="fixture-graph-lineage", clustering_min_samples=1
    )
    first = derive_corridor_obligations((), (), (), (flow,), fixture_graph(), profile)
    changed_graph = replace(
        fixture_graph(),
        graph_fingerprint="7" * 64,
        profile_fingerprint="8" * 64,
        source_export_fingerprint="9" * 64,
        route_control_fingerprint="a" * 64,
    )
    second = derive_corridor_obligations((), (), (), (flow,), changed_graph, profile)
    assert first.graph_fingerprint != second.graph_fingerprint
    assert first.profile_fingerprint == second.profile_fingerprint
    assert first.fingerprint != second.fingerprint
    assert first.obligations[0].obligation_id != second.obligations[0].obligation_id
    assert first.seed_records[0].seed_id != second.seed_records[0].seed_id
    changed_flow = replace(flow, destination_node_id="D", evidence_ids=("evidence-b",))
    changed = derive_corridor_obligations((), (), (), (changed_flow,), fixture_graph(), profile)
    assert first.seed_records[0].seed_id != changed.seed_records[0].seed_id
    assert first.pair_records[0].pair_id != changed.pair_records[0].pair_id


def test_missing_conflicting_unknown_and_unmatched_flows_keep_distinct_dispositions() -> None:
    profile = CorridorObligationProfile(profile_id="fixture-states")
    demand = (
        GovernedDemandFlow("conflict", "A", "C", 20, match_state=DemandMatchState.CONFLICTING),
        GovernedDemandFlow("unknown", "A", "C", 20, match_state=DemandMatchState.UNKNOWN),
        GovernedDemandFlow("missing-endpoint", None, "C", 20),
        GovernedDemandFlow("missing-value", "A", "C", None),
        GovernedDemandFlow("unmatched", "A", "C", 20, match_state=DemandMatchState.UNMATCHED),
    )
    result = derive_corridor_obligations((), (), (), demand, fixture_graph(), profile)
    dispositions = {item.source_flow_id: item.disposition for item in result.seed_records}
    assert dispositions == {
        "conflict": SeedDisposition.CONFLICTING,
        "unknown": SeedDisposition.UNKNOWN_DEMAND,
        "missing-endpoint": SeedDisposition.MISSING_ENDPOINT,
        "missing-value": SeedDisposition.MISSING_VALUE,
        "unmatched": SeedDisposition.UNMATCHED,
    }
    reasons = {item.source_flow_id: item.reason for item in result.seed_records}
    assert len(set(reasons.values())) == len(reasons)


def test_seed_budget_retains_excess_flows_with_explicit_gap() -> None:
    profile = CorridorObligationProfile(
        profile_id="fixture-budget",
        maximum_seed_evaluations=1,
        maximum_path_seeds_per_pair=1,
    )
    demand = tuple(
        GovernedDemandFlow(f"flow-{node}", "A", node, 50) for node in ("C", "D", "F", "J")
    )
    result = derive_corridor_obligations((), (), (), demand, fixture_graph(), profile)
    assert {item.source_flow_id for item in result.seed_records} == {
        item.flow_id for item in demand
    }
    assert any(item.disposition is SeedDisposition.BUDGET_EXCEEDED for item in result.seed_records)
    assert result.evaluated_seed_count <= profile.maximum_seed_evaluations
    assert result.status == "complete-with-gaps"


def test_all_below_threshold_is_demand_gap_without_missing_demand_request() -> None:
    profile = CorridorObligationProfile(profile_id="fixture-all-below", threshold_value=100)
    result = derive_corridor_obligations(
        (),
        (),
        (),
        (GovernedDemandFlow("below", "A", "C", 1, ("below-evidence",)),),
        fixture_graph(),
        profile,
    )
    assert result.fallback_origin is FallbackOrigin.DEMAND_LED
    assert result.status == "complete-with-gaps"
    assert not result.obligations
    assert not result.evidence_requests
    assert result.seed_records[0].disposition is SeedDisposition.BELOW_THRESHOLD


def test_cross_component_flow_is_an_explicit_disconnected_island_gap() -> None:
    profile = CorridorObligationProfile(profile_id="fixture-cross-island")
    result = derive_corridor_obligations(
        places=(ObligationPlace("anchor", "A"),),
        destinations=(),
        gateways=(),
        demand=(GovernedDemandFlow("cross-island", "A", "G", 20),),
        graph=fixture_graph(),
        profile=profile,
    )
    assert result.seed_records[0].disposition is SeedDisposition.DISCONNECTED_ISLAND
    assert result.status == "complete-with-gaps"


def test_border_seeds_are_assigned_once_and_only_medoids_create_obligations() -> None:
    graph = replace(
        fixture_graph(),
        edge_records=(
            *fixture_graph().edge_records,
            edge("cd1", "C", "D1", 50),
            edge("d1d2", "D1", "D2", 50),
            edge("d2d1", "D2", "D1", 50),
            edge("d1d3", "D1", "D3", 50),
            edge("d3d1", "D3", "D1", 50),
            edge("d2d3", "D2", "D3", 100),
            edge("d3d2", "D3", "D2", 100),
        ),
    )
    profile = CorridorObligationProfile(
        profile_id="fixture-border",
        clustering_epsilon_m=60,
        clustering_min_samples=3,
        segment_length_m=1_000,
    )
    demand = (
        GovernedDemandFlow("border-a", "A", "D1", 20),
        GovernedDemandFlow("border-b", "A", "D2", 20),
        GovernedDemandFlow("border-c", "A", "D3", 20),
    )
    result = derive_corridor_obligations((), (), (), demand, graph, profile)
    eligible = [
        item for item in result.seed_records if item.disposition is SeedDisposition.ELIGIBLE
    ]
    assert len({item.seed_id for item in eligible}) == len(eligible)
    assert all(item.cluster_id for item in eligible)
    assert sum(item.medoid for item in eligible) == len(result.obligations)


def test_opposite_od_directions_remain_distinct_and_duplicate_pairs_are_retained() -> None:
    graph = replace(
        fixture_graph(),
        edge_records=(*fixture_graph().edge_records, edge("ca", "C", "A", 100)),
    )
    profile = CorridorObligationProfile(
        profile_id="fixture-direction",
        clustering_epsilon_m=0,
        clustering_min_samples=1,
        segment_length_m=1_000,
        hierarchy_pairing_rules=(("place", "place"),),
    )
    demand = (
        GovernedDemandFlow("forward", "A", "C", 20, ("forward-evidence",)),
        GovernedDemandFlow("reverse", "C", "A", 20, ("reverse-evidence",)),
        GovernedDemandFlow("duplicate-a", "A", "C", 20, ("duplicate-a-evidence",)),
        GovernedDemandFlow("duplicate-b", "A", "C", 20, ("duplicate-b-evidence",)),
    )
    result = derive_corridor_obligations(
        places=(
            ObligationPlace("place-a", "A", category="place"),
            ObligationPlace("place-c", "C", category="place"),
        ),
        destinations=(),
        gateways=(),
        demand=demand,
        graph=graph,
        profile=profile,
    )
    directed = {(item.origin_node_id, item.destination_node_id) for item in result.obligations}
    assert ("A", "C") in directed
    assert ("C", "A") in directed
    assert any(item.disposition is PairDisposition.DUPLICATE for item in result.pair_records)


def test_gateway_pairing_rules_preserve_declared_direction() -> None:
    profile = CorridorObligationProfile(
        profile_id="fixture-gateway-direction",
        gateway_pairing_rules=("gateway-place", "place-gateway"),
        clustering_min_samples=1,
    )
    result = derive_corridor_obligations(
        places=(ObligationPlace("place-a", "A"),),
        destinations=(),
        gateways=(CrossBoundaryGateway("gateway-b", "B"),),
        demand=(GovernedDemandFlow("flow", "C", "D", 20),),
        graph=fixture_graph(),
        profile=profile,
    )
    configured = {
        (item.origin_endpoint_id, item.destination_endpoint_id)
        for item in result.pair_records
        if item.source_seed_ids == ()
    }
    assert {("place-a", "gateway-b"), ("gateway-b", "place-a")} <= configured


def test_same_node_od_is_rejected_as_invalid_input() -> None:
    with pytest.raises(ValueError, match="endpoints must be distinct"):
        GovernedDemandFlow("self", "A", "A", 1)
