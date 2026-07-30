from __future__ import annotations

import hashlib
from datetime import date

import geopandas as gpd
import pytest
from pydantic import ValidationError
from shapely.geometry import LineString

from satn.officer_decisions import (
    CleanSATNBaseline,
    ExcludeFromRoutingAction,
    ExcludeFromStrategicSpineAction,
    GovernedBaselineTarget,
    NetworkPublicationKind,
    OfficerDecision,
    OfficerDecisionApplicationError,
    OfficerDecisionLedger,
    OfficerDecisionTarget,
    OfficerDecisionType,
    OfficerTargetKind,
    apply_officer_decision_ledger,
)
from satn.route_controls import (
    EdgeBindingMode,
    RouteControlNetworkGap,
    RouteControlSet,
)
from satn.routing import RoadGraph, choose_alignment
from satn.strategic_reference_replay import _unique_edge_chain


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def parallel_edges() -> gpd.GeoDataFrame:
    rows = [
        {
            "osmid": "a-road-east-1",
            "u": "a",
            "v": "b",
            "length": 100,
            "highway": "primary",
            "ref": "A1",
            "geometry": LineString([(0, 0), (100, 0)]),
        },
        {
            "osmid": "a-road-west-1",
            "u": "b",
            "v": "a",
            "length": 100,
            "highway": "primary",
            "ref": "A1",
            "geometry": LineString([(100, 0), (0, 0)]),
        },
        {
            "osmid": "a-road-east-2",
            "u": "b",
            "v": "c",
            "length": 100,
            "highway": "primary",
            "ref": "A1",
            "geometry": LineString([(100, 0), (200, 0)]),
        },
        {
            "osmid": "a-road-west-2",
            "u": "c",
            "v": "b",
            "length": 100,
            "highway": "primary",
            "ref": "A1",
            "geometry": LineString([(200, 0), (100, 0)]),
        },
        {
            "osmid": "cycleway-east-1",
            "u": "a",
            "v": "d",
            "length": 120,
            "highway": "cycleway",
            "satn_ncn": True,
            "geometry": LineString([(0, 0), (100, 30)]),
        },
        {
            "osmid": "cycleway-west-1",
            "u": "d",
            "v": "a",
            "length": 120,
            "highway": "cycleway",
            "satn_ncn": True,
            "geometry": LineString([(100, 30), (0, 0)]),
        },
        {
            "osmid": "cycleway-east-2",
            "u": "d",
            "v": "c",
            "length": 120,
            "highway": "cycleway",
            "satn_ncn": True,
            "geometry": LineString([(100, 30), (200, 0)]),
        },
        {
            "osmid": "cycleway-west-2",
            "u": "c",
            "v": "d",
            "length": 120,
            "highway": "cycleway",
            "satn_ncn": True,
            "geometry": LineString([(200, 0), (100, 30)]),
        },
    ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=27700)


def controls_for_a_road(
    edges: gpd.GeoDataFrame,
    *,
    strategic_only: bool,
    directional_second_edge: bool = False,
) -> RouteControlSet:
    snapshot = digest("evidence-snapshot")
    baseline = RoadGraph(edges)
    first = baseline.bind_route_edge(
        "a",
        "b",
        evidence_snapshot_fingerprint=snapshot,
        mode=EdgeBindingMode.BIDIRECTIONAL,
    )
    second = baseline.bind_route_edge(
        "b",
        "c",
        evidence_snapshot_fingerprint=snapshot,
        mode=(
            EdgeBindingMode.DIRECTIONAL
            if directional_second_edge
            else EdgeBindingMode.BIDIRECTIONAL
        ),
    )
    field = (
        {"strategic_spine_exclusions": (first, second)}
        if strategic_only
        else {"routing_exclusions": (first, second)}
    )
    return RouteControlSet(
        evidence_snapshot_fingerprint=snapshot,
        **field,
    )


def test_strategic_exclusion_preserves_evidence_and_ordinary_access_routing() -> None:
    edges = parallel_edges()
    controls = controls_for_a_road(edges, strategic_only=True)
    graph = RoadGraph(edges, route_controls=controls)

    ordinary = graph.option("a", "c", "direct")
    strategic = graph.option("a", "c", "direct", strategic_use=True)

    assert ordinary is not None
    assert ordinary.edge_ids == ["a-road-east-1", "a-road-east-2"]
    assert strategic is not None
    assert strategic.edge_ids == ["cycleway-east-1", "cycleway-east-2"]
    assert graph.graph.has_edge("a", "b")
    binding = next(
        item
        for item in controls.strategic_spine_exclusions
        if any(direction.directed_key == ("a", "b") for direction in item.directions)
    )
    assert graph.edge_restrictions("a", "b") == (
        {
            "restriction": "exclude-from-strategic-spine",
            "binding_id": binding.binding_id,
            "route_control_fingerprint": controls.control_fingerprint,
        },
    )


def test_routing_exclusion_applies_to_every_option_attachment_and_replay_path() -> None:
    edges = parallel_edges()
    controls = controls_for_a_road(edges, strategic_only=False)
    graph = RoadGraph(edges, route_controls=controls)
    excluded = {
        direction.source_edge_id
        for binding in controls.routing_exclusions
        for direction in binding.directions
    }

    for role in ("direct", "strategic-spine", "ncn-informed", "low-traffic"):
        option = graph.option("a", "c", role)
        assert option is not None
        assert excluded.isdisjoint(option.edge_ids)
        assert excluded.isdisjoint(option.reverse_edge_ids)

    selected, candidates, _ = choose_alignment(graph, "a", "c", strategic_use=True)
    assert selected is not None
    assert candidates
    assert all(excluded.isdisjoint(item.edge_ids) for item in candidates)

    attachment = graph.best_attachment(
        [("a", 0.0)],
        [("c", 0.0)],
        allow_stationary=False,
    )
    assert attachment is not None
    assert excluded.isdisjoint(attachment.option.edge_ids)

    with pytest.raises(ValueError, match="unique exact graph edge chain"):
        _unique_edge_chain(
            graph,
            "a",
            "c",
            ("a-road-east-1", "a-road-east-2"),
        )


def test_directional_and_bidirectional_bindings_are_stable_and_explicit() -> None:
    edges = parallel_edges()
    snapshot = digest("evidence-snapshot")
    baseline = RoadGraph(edges)
    directional = baseline.bind_route_edge(
        "b",
        "c",
        evidence_snapshot_fingerprint=snapshot,
    )
    bidirectional = baseline.bind_route_edge(
        "a",
        "b",
        evidence_snapshot_fingerprint=snapshot,
        mode=EdgeBindingMode.BIDIRECTIONAL,
    )
    controls = RouteControlSet(
        evidence_snapshot_fingerprint=snapshot,
        strategic_spine_exclusions=(bidirectional,),
        routing_exclusions=(directional,),
    )
    reversed_graph = RoadGraph(edges.iloc[::-1].reset_index(drop=True))
    reversed_controls = RouteControlSet(
        evidence_snapshot_fingerprint=snapshot,
        strategic_spine_exclusions=(
            reversed_graph.bind_route_edge(
                "a",
                "b",
                evidence_snapshot_fingerprint=snapshot,
                mode=EdgeBindingMode.BIDIRECTIONAL,
            ),
        ),
        routing_exclusions=(
            reversed_graph.bind_route_edge(
                "b",
                "c",
                evidence_snapshot_fingerprint=snapshot,
            ),
        ),
    )
    graph = RoadGraph(edges, route_controls=controls)

    assert controls == reversed_controls
    assert controls.strategic_spine_exclusions[0].mode == EdgeBindingMode.BIDIRECTIONAL
    assert controls.routing_exclusions[0].mode == EdgeBindingMode.DIRECTIONAL
    forward = graph.option("a", "c", "direct")
    reverse = graph.option("c", "a", "direct")
    assert forward is not None and "a-road-east-2" not in forward.edge_ids
    assert reverse is not None
    assert reverse.edge_ids == ["a-road-west-2", "a-road-west-1"]


def test_no_route_is_a_visible_network_gap_without_fabricated_geometry() -> None:
    edges = parallel_edges().iloc[:4].copy()
    controls = controls_for_a_road(edges, strategic_only=False)
    graph = RoadGraph(edges, route_controls=controls)

    outcome = graph.governed_option_or_gap(
        "a",
        "c",
        "direct",
        unsatisfied_network_place_ids=("community-c",),
    )

    assert isinstance(outcome, RouteControlNetworkGap)
    assert outcome.unsatisfied_network_place_ids == ("community-c",)
    assert outcome.geometry_status == "not-generated"
    assert "geometry" not in outcome.model_dump(mode="json")
    assert outcome.route_control_fingerprint == controls.control_fingerprint
    assert outcome.excluded_edge_binding_ids == controls.excluded_binding_ids


def test_clean_baseline_is_unchanged_by_an_empty_route_control_set() -> None:
    edges = parallel_edges()
    clean = RoadGraph(edges)
    governed = RoadGraph(
        edges,
        route_controls=RouteControlSet(
            evidence_snapshot_fingerprint=digest("evidence-snapshot"),
        ),
    )

    for role in ("direct", "strategic-spine", "ncn-informed", "low-traffic"):
        clean_option = clean.option("a", "c", role)
        governed_option = governed.option("a", "c", role)
        assert clean_option is not None and governed_option is not None
        assert clean_option.edge_ids == governed_option.edge_ids
        assert clean_option.reverse_edge_ids == governed_option.reverse_edge_ids
        assert clean_option.geometry.equals_exact(governed_option.geometry, 0)


def test_stale_or_geometry_mismatched_edge_binding_fails_closed() -> None:
    edges = parallel_edges()
    controls = controls_for_a_road(edges, strategic_only=False)
    changed = edges.copy()
    changed.loc[changed["osmid"] == "a-road-east-1", "geometry"] = LineString(
        [(0, 0), (100, 1)]
    )

    with pytest.raises(ValueError, match="stale or geometry-mismatched"):
        RoadGraph(changed, route_controls=controls)

    payload = controls.model_dump(mode="json")
    payload["evidence_snapshot_fingerprint"] = digest("different-snapshot")
    payload.pop("control_set_id")
    payload.pop("control_fingerprint")
    with pytest.raises(ValidationError, match="stale for the evidence snapshot"):
        RouteControlSet.model_validate(payload)


def test_one_edge_cannot_mix_strategic_and_routing_exclusion_meanings() -> None:
    edges = parallel_edges()
    snapshot = digest("evidence-snapshot")
    binding = RoadGraph(edges).bind_route_edge(
        "a",
        "b",
        evidence_snapshot_fingerprint=snapshot,
    )

    with pytest.raises(ValidationError, match="cannot have both"):
        RouteControlSet(
            evidence_snapshot_fingerprint=snapshot,
            strategic_spine_exclusions=(binding,),
            routing_exclusions=(binding,),
        )


@pytest.mark.parametrize(
    ("decision_type", "action", "expected_restriction"),
    (
        (
            OfficerDecisionType.EXCLUDE_FROM_ROUTING,
            ExcludeFromRoutingAction(),
            "exclude-from-routing",
        ),
        (
            OfficerDecisionType.EXCLUDE_FROM_STRATEGIC_SPINE,
            ExcludeFromStrategicSpineAction(),
            "exclude-from-strategic-spine",
        ),
    ),
)
def test_officer_edge_decision_translates_to_route_controls_and_scenario_lineage(
    decision_type: OfficerDecisionType,
    action: ExcludeFromRoutingAction | ExcludeFromStrategicSpineAction,
    expected_restriction: str,
) -> None:
    edges = parallel_edges()
    snapshot = digest("officer-evidence-snapshot")
    binding = RoadGraph(edges).bind_route_edge(
        "a",
        "b",
        evidence_snapshot_fingerprint=snapshot,
        mode=EdgeBindingMode.BIDIRECTIONAL,
    )
    clean = CleanSATNBaseline(
        baseline_id="route-control-baseline",
        network_json='{"features":[],"type":"FeatureCollection"}',
        evidence_snapshot_fingerprint=snapshot,
        profile_fingerprint=digest("profile"),
        governed_evidence_ids=("edge-survey",),
        targets=(
            GovernedBaselineTarget(
                target=OfficerDecisionTarget(
                    kind=OfficerTargetKind.ROUTING_EDGE,
                    target_id=binding.binding_id,
                )
            ),
        ),
    )
    decision = OfficerDecision(
        decision_id=f"decision-{decision_type.value}",
        decision_type=decision_type,
        target=OfficerDecisionTarget(
            kind=OfficerTargetKind.ROUTING_EDGE,
            target_id=binding.binding_id,
        ),
        action=action,
        decision_maker="Alex Officer",
        decision_maker_role="Principal Transport Planner",
        organisation="Example Council",
        decision_date=date(2026, 7, 30),
        rationale="The governed evidence supports this scenario restriction.",
        evidence_ids=("edge-survey",),
        source_url="https://example.test/route-decisions/1",
        effective_from=date(2026, 7, 30),
        baseline_fingerprint=clean.baseline_fingerprint,
        evidence_snapshot_fingerprint=snapshot,
        profile_fingerprint=clean.profile_fingerprint,
    )
    ledger = OfficerDecisionLedger(
        baseline_fingerprint=clean.baseline_fingerprint,
        evidence_snapshot_fingerprint=snapshot,
        profile_fingerprint=clean.profile_fingerprint,
        decisions=(decision,),
    )

    scenario = apply_officer_decision_ledger(
        clean,
        ledger,
        effective_on=date(2026, 7, 30),
        route_edge_bindings=(binding,),
    )
    assert scenario.publication_kind == NetworkPublicationKind.OFFICER_INFORMED_SCENARIO
    assert scenario.route_controls is not None
    assert scenario.route_control_fingerprint == scenario.route_controls.control_fingerprint
    assert scenario.route_control_fingerprint in scenario.dependency_fingerprints
    assert binding.binding_id in scenario.route_controls.excluded_binding_ids
    assert decision.decision_id in scenario.baseline_to_scenario_change_summary[0]

    graph = RoadGraph(edges, route_controls=scenario.route_controls)
    assert graph.edge_restrictions("a", "b")[0]["restriction"] == expected_restriction
    ordinary = graph.option("a", "c", "direct")
    strategic = graph.option("a", "c", "direct", strategic_use=True)
    assert ordinary is not None and strategic is not None
    if decision_type == OfficerDecisionType.EXCLUDE_FROM_ROUTING:
        assert ordinary.edge_ids == ["cycleway-east-1", "cycleway-east-2"]
    else:
        assert ordinary.edge_ids == ["a-road-east-1", "a-road-east-2"]
        assert strategic.edge_ids == ["cycleway-east-1", "cycleway-east-2"]

    with pytest.raises(
        OfficerDecisionApplicationError,
        match="no exact current edge binding",
    ):
        apply_officer_decision_ledger(
            clean,
            ledger,
            effective_on=date(2026, 7, 30),
        )
