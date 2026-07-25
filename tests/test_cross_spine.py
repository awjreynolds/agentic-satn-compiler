"""Cross-Spine behaviour specified at the assembly module interface."""

from __future__ import annotations

import json

import geopandas as gpd
import networkx as nx
import pytest
from shapely.geometry import LineString, MultiLineString

import satn.cross_spine as cross_spine
from satn.cross_spine import CROSS_SPINE_DIAGNOSTICS_SCHEMA_VERSION, resolve_cross_spine_assembly
from satn.identifiers import stable_id
from satn.models import AgentRecord, PublishedFeatureReference, TrafficLight


def _connector(
    connector_id: str,
    geometry: LineString | MultiLineString,
    *,
    from_root: str = "left-primary",
    to_root: str = "right-primary",
) -> dict[str, object]:
    return {
        "cross_spine_connector_id": connector_id,
        "from_root_spine_id": from_root,
        "from_root_spine_name": from_root,
        "to_root_spine_id": to_root,
        "to_root_spine_name": to_root,
        "distance_km": 0.1,
        "source_ids": json.dumps(["ncn-evidence"]),
        "provenance": json.dumps({"source_ids": ["connector-evidence"]}),
        "geometry_semantics": "assembled connector",
        "geometry": geometry,
    }


def _roots(
    left: LineString | None = None,
    right: LineString | None = None,
) -> gpd.GeoDataFrame:
    left = LineString([(0, -10), (0, 10)]) if left is None else left
    right = LineString([(100, -10), (100, 10)]) if right is None else right
    return gpd.GeoDataFrame(
        [
            {"spine_id": "left-primary", "geometry": left},
            {"spine_id": "right-primary", "geometry": right},
        ],
        crs=27700,
    )


def _accepted_record(connector_id: str) -> AgentRecord:
    return AgentRecord(
        connection_id=f"meeting-{connector_id}",
        governing_criterion="continuity",
        governing_status=TrafficLight.GREEN,
        review_policy=(),
        review_required=False,
        network_role="branch-meeting-connection",
        runtime="not-invoked",
        model="not-invoked",
        decision="accept",
        derived_features=[
            PublishedFeatureReference(
                feature_id=connector_id,
                network_role="cross-spine-connector",
            )
        ],
    )


def test_assembly_selects_a_deterministic_named_root_path_without_mutating_lineage() -> None:
    """Input ordering/orientation cannot change an authoritative connector or its input audit."""
    routes: list[tuple[str, list[tuple[float, float]]]] = []
    diagnostics: list[dict[str, object]] = []
    segments = [
        [(0, 0), (50, 50)],
        [(50, 50), (100, 0)],
        [(0, 0), (50, -50)],
        [(50, -50), (100, 0)],
    ]
    for lines in (segments, list(reversed([list(reversed(line)) for line in segments]))):
        connectors = gpd.GeoDataFrame([_connector("diamond", MultiLineString(lines))], crs=27700)
        record = _accepted_record("diamond")

        result = resolve_cross_spine_assembly(connectors, _roots(), [record])
        connector = result.valid_connectors.iloc[0]

        routes.append((connector.geometry.wkb_hex, list(connector.geometry.coords)))
        diagnostics.append(result.diagnostics)
        assert connectors.iloc[0]["provenance"] == json.dumps(
            {"source_ids": ["connector-evidence"]}
        )
        assert [reference.feature_id for reference in record.derived_features] == ["diamond"]
        assert result.agent_records[0] is not record
    assert routes == [(routes[0][0], [(0.0, 0.0), (50.0, -50.0), (100.0, 0.0)])] * 2
    assert diagnostics == [diagnostics[0]] * 2
    assert diagnostics[0] == {
        "schema_version": CROSS_SPINE_DIAGNOSTICS_SCHEMA_VERSION,
        "root_pairs_considered": 0,
        "root_pair_candidate_searches": 0,
        "meeting_agent_evaluations": 0,
        "meeting_agent_evaluation_initial_outcomes": {
            "accept": 0,
            "reject": 0,
            "gap": 0,
        },
        "meeting_agent_evaluation_final_dispositions": {
            "accept": 0,
            "reject": 0,
            "gap": 0,
            "superseded": 0,
        },
        "candidate_connectors": 1,
        "authoritative_connectors": 1,
        "route_refinement_findings": 0,
        "typed_refinement_findings": {"route-refinement-required": 0},
        "noded_graphs_built": 1,
        "noded_graph_nodes_total": 2,
        "noded_graph_edges_total": 1,
        "peak_noded_graph_nodes": 2,
        "peak_noded_graph_edges": 1,
        "root_candidate_nodes_examined": 4,
        "eligible_root_endpoint_candidates": 2,
        "endpoint_pairs_considered": 1,
        "weighted_shortest_path_searches": 1,
        "weighted_shortest_path_nodes_settled": 2,
        "weighted_shortest_path_edge_relaxations": 2,
        "peak_shortest_path_frontier": 1,
        "deterministic_path_nodes_selected": 2,
        "connector_traversal_attempts": 1,
    }


def test_progress_observer_receives_a_deep_defensive_diagnostics_snapshot() -> None:
    connectors = gpd.GeoDataFrame(
        [_connector("defensive", LineString([(0, 0), (100, 0)]))], crs=27700
    )

    def malicious_observer(_assessed: int, _total: int, diagnostics: dict[str, object]) -> None:
        diagnostics["candidate_connectors"] = -1
        typed = diagnostics["typed_refinement_findings"]
        assert isinstance(typed, dict)
        typed["route-refinement-required"] = -1

    result = resolve_cross_spine_assembly(
        connectors,
        _roots(),
        progress=malicious_observer,
    )

    assert result.diagnostics["candidate_connectors"] == 1
    assert result.diagnostics["typed_refinement_findings"] == {
        "route-refinement-required": 0
    }


def test_assembly_rejects_missing_named_root_as_a_producer_invariant() -> None:
    connectors = gpd.GeoDataFrame(
        [_connector("missing-root", LineString([(0, 0), (100, 0)]), to_root="missing")],
        crs=27700,
    )

    with pytest.raises(ValueError, match=r"missing-root.*missing Strategic Spine missing"):
        resolve_cross_spine_assembly(connectors, _roots())


def test_assembly_withholds_disconnected_exact_intersections_as_one_point_only_finding() -> None:
    connectors = gpd.GeoDataFrame(
        [
            _connector(
                "disconnected",
                MultiLineString([[(0, 0), (20, 0)], [(80, 0), (100, 0)]]),
            )
        ],
        crs=27700,
    )

    result = resolve_cross_spine_assembly(connectors, _roots())

    assert result.valid_connectors.empty
    finding = result.route_refinement_findings.iloc[0]
    assert finding["network_role"] == "cross-spine-connector-gap"
    assert finding.geometry.geom_type == "MultiPoint"
    assert "disconnected exact named-root intersections" in finding["selection_reason"]
    assert result.diagnostics["candidate_connectors"] == 1
    assert result.diagnostics["authoritative_connectors"] == 0
    assert result.diagnostics["route_refinement_findings"] == 1
    assert result.diagnostics["typed_refinement_findings"] == {
        "route-refinement-required": 1
    }


def test_assembly_enforces_named_root_closure_budget_and_reconciles_agent_audit() -> None:
    """A one-millimetre excess becomes a finding; the accepted audit is bijective."""
    connectors = gpd.GeoDataFrame(
        [_connector("needs-refinement", LineString([(0, 0), (100, 0)]))], crs=27700
    )
    roots = _roots(
        LineString([(-100.001, -10), (-100.001, 10)]),
        LineString([(100, -10), (100, 10)]),
    )
    record = _accepted_record("needs-refinement")

    result = resolve_cross_spine_assembly(connectors, roots, [record])

    assert result.valid_connectors.empty
    assert result.route_refinement_findings["connection_id"].tolist() == [
        stable_id("cross-spine-connector-gap", "needs-refinement")
    ]
    reconciled = result.agent_records[0]
    assert reconciled.derived_features == []
    assert reconciled.withheld_derived_features[0].feature_id == "needs-refinement"
    assert (
        reconciled.withheld_derived_features[0].finding_id
        == result.route_refinement_findings.iloc[0]["connection_id"]
    )
    assert [reference.feature_id for reference in record.derived_features] == ["needs-refinement"]


def test_assembly_rejects_a_cross_spine_reference_from_a_nonaccepted_record() -> None:
    connectors = gpd.GeoDataFrame(
        [_connector("nonaccepted", LineString([(0, 0), (100, 0)]))], crs=27700
    )
    record = _accepted_record("nonaccepted")
    record.decision = "reject"

    with pytest.raises(ValueError, match="non-accepted AgentRecord cannot establish or withhold"):
        resolve_cross_spine_assembly(connectors, _roots(), [record])


@pytest.mark.parametrize("weight", [0.0, -1.0, float("nan"), float("inf")])
def test_assembly_rejects_invalid_traversal_weights_at_its_public_boundary(
    monkeypatch: pytest.MonkeyPatch, weight: float
) -> None:
    """Malformed graph weights fail before traversal can loop or fabricate a route."""
    graph = nx.Graph()
    start, end = (0.0, 0.0), (100.0, 0.0)
    graph.add_edge(
        start,
        end,
        weight=weight,
        geometry=LineString([start, end]),
        signature=(start, end),
    )
    monkeypatch.setattr(cross_spine, "_noded_graph", lambda *_args: graph)
    connectors = gpd.GeoDataFrame(
        [_connector("invalid-weight", LineString([start, end]))], crs=27700
    )

    with pytest.raises(ValueError, match=r"finite, strictly positive"):
        resolve_cross_spine_assembly(connectors, _roots())


def test_assembly_prunes_an_unrelated_nearby_spur_from_the_named_root_route() -> None:
    """Only each connector's recorded roots may determine its published path."""
    connectors = gpd.GeoDataFrame(
        [
            _connector(
                "pruned-spur",
                MultiLineString(
                    [
                        [(0, 0), (100, 0)],
                        [(50, 0), (50, 115.9)],
                    ]
                ),
            )
        ],
        crs=27700,
    )
    roots = gpd.GeoDataFrame(
        [
            {"spine_id": "left-primary", "geometry": LineString([(0, -10), (0, 10)])},
            {"spine_id": "right-primary", "geometry": LineString([(100, -10), (100, 10)])},
            {
                "spine_id": "unrelated-primary",
                "geometry": LineString([(165.9, 105.9), (165.9, 125.9)]),
            },
        ],
        crs=27700,
    )

    connector = resolve_cross_spine_assembly(connectors, roots).valid_connectors.iloc[0]
    provenance = json.loads(connector["provenance"])

    assert (50.0, 115.9) not in connector.geometry.coords
    assert connector.geometry.distance(roots.geometry.iloc[2]) > 100.0
    assert connector["distance_km"] == 0.1
    assert provenance["named_root_traversal"]["pruned_segment_count"] == 1
