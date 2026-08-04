from __future__ import annotations

from satn.network_diagnostics import (
    DiagnosticNetworkView,
    DiagnosticObligation,
    NetworkDiagnosticProfile,
    analyse_network,
)
from satn.planning_graph import (
    EdgeObservationBinding,
    GraphComponentRecord,
    PlanningEdgeRecord,
    PlanningGraphSnapshot,
    PlanningNodeRecord,
)


def _edge(
    source_edge_id: str,
    directed_edge_id: str,
    start: str,
    end: str,
    wkt: str,
    *,
    access: str | None = "yes",
    reciprocal: str = "reciprocal",
    claims: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> PlanningEdgeRecord:
    return PlanningEdgeRecord(
        source_edge_id=source_edge_id,
        directed_edge_id=directed_edge_id,
        from_node_id=start,
        to_node_id=end,
        geometry_wkt=wkt,
        geometry_fingerprint=(directed_edge_id[0] * 64),
        length_mm=100_000,
        highway="cycleway" if source_edge_id in {"ab", "ac"} else "unclassified",
        ref=None,
        access=access,
        bicycle="yes",
        foot="yes",
        oneway=reciprocal == "one-way",
        reciprocal_state=reciprocal,
        weak_component_id="weak-main" if start not in {"E", "F"} else "weak-island",
        strong_component_id="strong-main" if start not in {"E", "F"} else "strong-island",
        claim_observation_ids=claims,
    )


def _snapshot() -> PlanningGraphSnapshot:
    edges = (
        _edge("ab", "ab-forward", "A", "B", "LINESTRING (0 0, 100 0)"),
        _edge("ab", "ab-reverse", "B", "A", "LINESTRING (100 0, 0 0)"),
        _edge("bd", "bd-forward", "B", "D", "LINESTRING (100 0, 200 100)"),
        _edge("bd", "bd-reverse", "D", "B", "LINESTRING (200 100, 100 0)"),
        _edge("ac", "ac-forward", "A", "C", "LINESTRING (0 0, 100 100)"),
        _edge("ac", "ac-reverse", "C", "A", "LINESTRING (100 100, 0 0)"),
        _edge(
            "cd",
            "cd-forward",
            "C",
            "D",
            "LINESTRING (100 100, 200 100)",
            access="restricted",
            claims=(("crossing", ("crossing-cd",)),),
        ),
        _edge(
            "cd",
            "cd-reverse",
            "D",
            "C",
            "LINESTRING (200 100, 100 100)",
            access="restricted",
            claims=(("crossing", ("crossing-cd",)),),
        ),
        _edge("dg", "dg-forward", "D", "G", "LINESTRING (200 100, 300 100)", reciprocal="one-way"),
        _edge("ef", "ef-forward", "E", "F", "LINESTRING (0 500, 100 500)"),
        _edge("ef", "ef-reverse", "F", "E", "LINESTRING (100 500, 0 500)"),
    )
    nodes = tuple(
        PlanningNodeRecord(
            node_id=node,
            weak_component_id="weak-island" if node in {"E", "F"} else "weak-main",
            strong_component_id="strong-island" if node in {"E", "F"} else "strong-main",
        )
        for node in "ABCDEFG"
    )
    components = (
        GraphComponentRecord(
            "weak-main",
            "weak",
            tuple("ABCDG"),
            tuple(e.directed_edge_id for e in edges if e.from_node_id not in {"E", "F"}),
            5,
            9,
        ),
        GraphComponentRecord("weak-island", "weak", ("E", "F"), ("ef-forward", "ef-reverse"), 2, 2),
    )
    return PlanningGraphSnapshot(
        graph_fingerprint="g" * 64,
        edge_records=edges,
        node_records=nodes,
        component_records=components,
        observation_matches=(
            EdgeObservationBinding(
                observation_id="crossing-cd",
                subject_id="cd",
                claim="crossing",
                state="evidenced",
                directed_edge_ids=("cd-forward", "cd-reverse"),
                source_kind="road",
            ),
        ),
        diagnostics=(),
        profile_fingerprint="p" * 64,
        source_export_fingerprint="s" * 64,
        route_control_fingerprint=None,
    )


def _profile() -> NetworkDiagnosticProfile:
    return NetworkDiagnosticProfile(
        profile_id="diagnostic-trial-v1",
        canonical_crs="EPSG:27700",
        permitted_access_states=("yes", "permitted"),
        crossing_claims=("crossing",),
    )


def test_diagnostics_keep_components_dangles_cuts_reciprocity_and_crossing_assumptions() -> None:
    result = analyse_network(
        _snapshot(),
        DiagnosticNetworkView(),
        (),
        _profile(),
    )

    assert {item.node_id for item in result.degree_dangles if item.kind == "dangle"} == {"G"}
    assert {item.source_edge_id for item in result.bridge_cuts} == {"dg", "ef"}
    assert {item.source_edge_id for item in result.reciprocal_access} == {"dg"}
    assert {item.source_edge_id for item in result.severance} == {"cd", "dg", "ef"}
    assert result.severance[0].crossing_assumption == "evidenced"
    assert sorted(item.node_ids for item in result.components if item.kind == "weak") == [
        ("A", "B", "C", "D", "G"),
        ("E", "F"),
    ]


def test_equal_witness_paths_are_deterministic_and_no_path_is_explicit() -> None:
    obligations = (
        DiagnosticObligation("a-to-d", "A", "D"),
        DiagnosticObligation("a-to-e", "A", "E"),
    )
    first = analyse_network(_snapshot(), DiagnosticNetworkView(), obligations, _profile())
    second = analyse_network(
        _snapshot(),
        DiagnosticNetworkView(),
        tuple(reversed(obligations)),
        _profile(),
    )

    assert first.fingerprint == second.fingerprint
    assert first.witnesses[0].state == "found"
    assert first.witnesses[0].edge_ids == ("ab-forward", "bd-forward")
    assert first.witnesses[1].state == "no-path"
    assert first.witnesses[1].failed_reason == "no-path"
    assert first.directness[1].denominator_m is not None
    assert first.directness[1].numerator_m is None


def test_unknown_access_is_retained_and_reported_never_traversed() -> None:
    snapshot = _snapshot()
    unknown = snapshot.edge_records[0]
    replaced = PlanningEdgeRecord(
        **(unknown.__dict__ | {"access": None, "unknown_claims": ("access",)})
    )
    changed = PlanningGraphSnapshot(
        **(snapshot.__dict__ | {"edge_records": (replaced, *snapshot.edge_records[1:])})
    )

    result = analyse_network(
        changed,
        DiagnosticNetworkView(),
        (DiagnosticObligation("a-to-d", "A", "D"),),
        _profile(),
    )

    assert "ab-forward" in result.reachability[0].unknown_edge_ids
    assert result.witnesses[0].state == "unknown"
    assert result.profile.unknown_policy == "retain-as-unknown-and-report"


def test_selected_network_and_delta_are_projection_only() -> None:
    snapshot = _snapshot()
    selected = DiagnosticNetworkView(
        edge_ids=("ab-forward", "ab-reverse", "bd-forward", "bd-reverse"),
        before_edge_ids=("ab-forward", "ab-reverse"),
        after_edge_ids=("ab-forward", "ab-reverse", "bd-forward", "bd-reverse"),
    )
    result = analyse_network(snapshot, selected, (), _profile())

    assert {item.edge_id for item in result.delta} == {"bd-forward", "bd-reverse"}
    assert result.graph_fingerprint == snapshot.graph_fingerprint
    assert tuple(edge.directed_edge_id for edge in snapshot.edge_records) != ()


def test_unknown_selected_edge_and_missing_place_are_typed_and_non_fatal() -> None:
    selected = DiagnosticNetworkView(edge_ids=("does-not-exist",), place_node_ids=("Z",))
    result = analyse_network(
        _snapshot(),
        selected,
        (),
        _profile(),
    )

    assert ("missing-selected-edge", "does-not-exist") in {
        (item.code, item.subject_id) for item in result.diagnostics
    }
    assert ("missing-place-node", "Z") in {
        (item.code, item.subject_id) for item in result.diagnostics
    }


def test_crossing_state_is_taken_from_governed_observation_matches() -> None:
    snapshot = _snapshot()
    conflicting = PlanningGraphSnapshot(
        **(
            snapshot.__dict__
            | {
                "observation_matches": (
                    EdgeObservationBinding(
                        observation_id="crossing-cd",
                        subject_id="cd",
                        claim="crossing",
                        state="conflicting",
                        directed_edge_ids=("cd-forward", "cd-reverse"),
                        source_kind="road",
                    ),
                )
            }
        )
    )

    result = analyse_network(conflicting, DiagnosticNetworkView(), (), _profile())

    assert result.severance[0].crossing_assumption == "conflicting"


def test_bridge_endpoints_and_reciprocal_state_aggregation_are_input_order_stable() -> None:
    snapshot = _snapshot()
    reversed_snapshot = PlanningGraphSnapshot(
        **(snapshot.__dict__ | {"edge_records": tuple(reversed(snapshot.edge_records))})
    )

    first = analyse_network(snapshot, DiagnosticNetworkView(), (), _profile())
    second = analyse_network(reversed_snapshot, DiagnosticNetworkView(), (), _profile())

    assert first.fingerprint == second.fingerprint
    assert [(item.from_node_id, item.to_node_id) for item in first.bridge_cuts] == [
        ("D", "G"),
        ("E", "F"),
    ]
    assert first.reciprocal_access[0].state == "one-way"

    conflicting_edge = snapshot.edge_records[0]
    conflicting = PlanningGraphSnapshot(
        **(
            snapshot.__dict__
            | {
                "edge_records": (
                    PlanningEdgeRecord(
                        **(conflicting_edge.__dict__ | {"reciprocal_state": "conflicting"})
                    ),
                    *snapshot.edge_records[1:],
                )
            }
        )
    )
    conflict_result = analyse_network(conflicting, DiagnosticNetworkView(), (), _profile())
    assert {item.state for item in conflict_result.reciprocal_access} >= {"conflicting"}


def test_result_identity_binds_obligation_evidence_and_is_permutation_stable() -> None:
    obligations = (
        DiagnosticObligation("a-to-d", "A", "D", ("evidence-1", "evidence-2")),
        DiagnosticObligation("d-to-g", "D", "G", ("evidence-3",)),
    )

    first = analyse_network(_snapshot(), DiagnosticNetworkView(), obligations, _profile())
    permuted = analyse_network(
        _snapshot(), DiagnosticNetworkView(), tuple(reversed(obligations)), _profile()
    )
    changed_evidence = analyse_network(
        _snapshot(),
        DiagnosticNetworkView(),
        (
            DiagnosticObligation("a-to-d", "A", "D", ("evidence-1", "evidence-4")),
            obligations[1],
        ),
        _profile(),
    )

    assert first.fingerprint == permuted.fingerprint
    assert first.fingerprint != changed_evidence.fingerprint


def test_severance_uses_route_impact_not_endpoint_touch() -> None:
    obligations = (
        DiagnosticObligation("a-to-d", "A", "D"),
        DiagnosticObligation("d-to-g", "D", "G"),
    )
    result = analyse_network(_snapshot(), DiagnosticNetworkView(), obligations, _profile())
    by_edge = {item.source_edge_id: item for item in result.severance}

    assert by_edge["cd"].affected_obligation_ids == ()
    assert by_edge["dg"].affected_obligation_ids == ("d-to-g",)
