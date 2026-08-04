from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from satn.planning_graph import (
    PlanningGraphProfile,
    PlanningGraphRequest,
    SourceExportFrame,
    build_planning_graph,
)
from satn.route_controls import RouteControlSet

FIXTURE = Path(__file__).parent / "fixtures" / "planning-graph-fixture.json"


def _frame(*, reverse: bool = False) -> SourceExportFrame:
    payload = json.loads(FIXTURE.read_text())
    rows = payload["rows"]
    if reverse:
        rows = list(reversed(rows))
    frame = gpd.GeoDataFrame(
        [
            {
                **{key: value for key, value in row.items() if key != "geometry"},
                "geometry": LineString(row["geometry"]),
            }
            for row in rows
        ],
        geometry="geometry",
        crs=payload["crs"],
    )
    return SourceExportFrame(frame=frame, source_export_fingerprint="a" * 64)


def _snapshot(*, reverse: bool = False):
    return build_planning_graph(
        PlanningGraphRequest(
            routable_edges=_frame(reverse=reverse),
            asset_observations=(),
            road_observations=(),
            route_controls=None,
            profile=PlanningGraphProfile(canonical_crs="EPSG:27700"),
        )
    )


def test_parallel_edges_components_reciprocal_and_isolated_component_are_retained() -> None:
    snapshot = _snapshot()

    assert len(snapshot.edge_records) == 8
    assert {edge.source_edge_id for edge in snapshot.edge_records} == {
        "ab-a-road",
        "ab-cycleway",
        "bc-forward",
        "bc-reverse",
        "cd-oneway",
        "ef-cycleway",
    }
    assert len({edge.directed_edge_id for edge in snapshot.edge_records}) == 8

    by_source = {edge.source_edge_id: edge for edge in snapshot.edge_records}
    assert by_source["ab-a-road"].reciprocal_state == "reciprocal"
    assert by_source["ab-cycleway"].reciprocal_state == "reciprocal"
    assert by_source["cd-oneway"].reciprocal_state == "one-way"
    assert by_source["ef-cycleway"].reciprocal_state == "reciprocal"

    weak = {
        record.node_ids: record
        for record in snapshot.component_records
        if record.kind == "weak"
    }
    assert sorted(record.node_ids for record in weak.values()) == [
        ("A", "B", "C", "D"),
        ("E", "F"),
    ]
    assert all(record.node_ids for record in snapshot.component_records)


def test_source_row_permutation_does_not_change_fingerprint_or_identities() -> None:
    first = _snapshot()
    second = _snapshot(reverse=True)

    assert first.graph_fingerprint == second.graph_fingerprint
    assert [edge.directed_edge_id for edge in first.edge_records] == [
        edge.directed_edge_id for edge in second.edge_records
    ]


def test_claim_specific_conflicting_observations_are_bound_independently() -> None:
    from satn.evidence_normalisation import EvidenceObservationDraft

    observations = (
        EvidenceObservationDraft(
            observation_id="access-yes",
            subject_id="ab-a-road",
            claim="access",
            value={"value": "permitted"},
            coverage_cells=("ST56",),
            observed_at="2026-08-04",
        ),
        EvidenceObservationDraft(
            observation_id="access-no",
            subject_id="ab-a-road",
            claim="access",
            value={"value": "restricted"},
            coverage_cells=("ST56",),
            observed_at="2026-08-04",
            state="conflicting",
        ),
    )
    # EvidenceObservation construction is covered by its own contract; this
    # test uses the lightweight source-frame observation adapter accepted by
    # the planning seam.
    snapshot = build_planning_graph(
        PlanningGraphRequest(
            routable_edges=_frame(),
            asset_observations=observations,
            road_observations=(),
            route_controls=None,
            profile=PlanningGraphProfile(canonical_crs="EPSG:27700"),
        )
    )

    bound = [item for item in snapshot.observation_matches if item.subject_id == "ab-a-road"]
    assert {item.observation_id for item in bound} == {"access-yes", "access-no"}
    assert {item.claim for item in bound} == {"access"}
    assert len(snapshot.edge_records[0].access_observation_ids) == 2


def test_legacy_single_edge_path_parity_is_available_from_snapshot_records() -> None:
    snapshot = _snapshot()
    ab = [edge for edge in snapshot.edge_records if edge.source_edge_id == "ab-a-road"]
    assert {(edge.from_node_id, edge.to_node_id) for edge in ab} == {("A", "B"), ("B", "A")}


def test_conflicting_duplicate_direction_is_diagnosed_deterministically() -> None:
    source = _frame().frame
    rows = [row.to_dict() for _, row in source.iterrows()]
    rows.append(
        {
            **rows[0],
            "geometry": LineString(((0, 0), (100, 20))),
        }
    )
    rows.append({**rows[0], "highway": "secondary"})

    def compile_rows(values: list[dict[str, object]]):
        return build_planning_graph(
            PlanningGraphRequest(
                routable_edges=SourceExportFrame(
                    frame=gpd.GeoDataFrame(
                        values,
                        geometry="geometry",
                        crs="EPSG:27700",
                    ),
                    source_export_fingerprint="b" * 64,
                ),
                asset_observations=(),
                road_observations=(),
                route_controls=None,
                profile=PlanningGraphProfile(canonical_crs="EPSG:27700"),
            )
        )

    forward = compile_rows(rows)
    reverse = compile_rows(list(reversed(rows)))

    assert len(
        [
            edge
            for edge in forward.edge_records
            if edge.source_edge_id == "ab-a-road"
            and edge.from_node_id == "A"
            and edge.to_node_id == "B"
        ]
    ) == 1
    assert [diagnostic.code for diagnostic in forward.diagnostics] == [
        "conflicting-directed-edge"
    ]
    assert forward.graph_fingerprint == reverse.graph_fingerprint


def test_invalid_node_identity_is_retained_as_a_typed_diagnostic() -> None:
    frame = _frame().frame.copy()
    frame.loc[0, "u"] = None

    snapshot = build_planning_graph(
        PlanningGraphRequest(
            routable_edges=SourceExportFrame(
                frame=frame,
                source_export_fingerprint="c" * 64,
            ),
            asset_observations=(),
            road_observations=(),
            route_controls=None,
            profile=PlanningGraphProfile(canonical_crs="EPSG:27700"),
        )
    )

    assert "None" not in {node.node_id for node in snapshot.node_records}
    assert ("invalid-source-edge", "ab-a-road") in {
        (diagnostic.code, diagnostic.subject_id) for diagnostic in snapshot.diagnostics
    }

    nullable = _frame().frame.copy()
    nullable["u"] = nullable["u"].astype(object)
    nullable.loc[0, "u"] = pd.NA
    nullable_snapshot = build_planning_graph(
        PlanningGraphRequest(
            routable_edges=SourceExportFrame(
                frame=nullable,
                source_export_fingerprint="3" * 64,
            ),
            asset_observations=(),
            road_observations=(),
            route_controls=None,
            profile=PlanningGraphProfile(canonical_crs="EPSG:27700"),
        )
    )
    assert "<NA>" not in {node.node_id for node in nullable_snapshot.node_records}


def test_invalid_and_unmatched_observations_do_not_abort_the_snapshot() -> None:
    from satn.evidence_normalisation import EvidenceObservationDraft

    unmatched = EvidenceObservationDraft(
        observation_id="missing-edge-access",
        subject_id="not-a-planning-edge",
        claim="access",
        value={"value": "permitted"},
        coverage_cells=("ST56",),
        observed_at="2026-08-04",
    )
    snapshot = build_planning_graph(
        PlanningGraphRequest(
            routable_edges=_frame(),
            asset_observations=(unmatched, object()),
            road_observations=(),
            route_controls=None,
            profile=PlanningGraphProfile(canonical_crs="EPSG:27700"),
        )
    )

    assert snapshot.observation_matches[0].observation_id == "missing-edge-access"
    assert snapshot.observation_matches[0].directed_edge_ids == ()
    assert {diagnostic.code for diagnostic in snapshot.diagnostics} == {
        "invalid-observation",
        "unmatched-observation",
    }


def test_route_controls_and_canonical_crs_are_bound_to_snapshot_identity() -> None:
    baseline = _snapshot()
    controls = RouteControlSet(evidence_snapshot_fingerprint="d" * 64)
    controlled = build_planning_graph(
        PlanningGraphRequest(
            routable_edges=_frame(),
            asset_observations=(),
            road_observations=(),
            route_controls=controls,
            profile=PlanningGraphProfile(canonical_crs="EPSG:27700"),
        )
    )

    assert controlled.route_control_fingerprint == controls.control_fingerprint
    assert controlled.graph_fingerprint != baseline.graph_fingerprint
    wrong_crs = build_planning_graph(
        PlanningGraphRequest(
            routable_edges=SourceExportFrame(
                frame=_frame().frame.to_crs("EPSG:3857"),
                source_export_fingerprint="2" * 64,
            ),
            asset_observations=(),
            road_observations=(),
            route_controls=controls,
            profile=PlanningGraphProfile(canonical_crs="EPSG:27700"),
        )
    )
    assert wrong_crs.route_control_fingerprint == controls.control_fingerprint
    assert wrong_crs.diagnostics[0].code == "source-crs-mismatch"
    with pytest.raises(ValueError, match="canonical CRS must be EPSG:27700"):
        PlanningGraphProfile(canonical_crs="EPSG:3857")


def test_diagnostic_order_is_stable_for_repeated_invalid_subjects() -> None:
    rows = [row.to_dict() for _, row in _frame().frame.iloc[:1].iterrows()]
    rows[0]["u"] = None
    rows.append({**rows[0], "u": "A", "v": None})

    def compile_rows(values: list[dict[str, object]]):
        return build_planning_graph(
            PlanningGraphRequest(
                routable_edges=SourceExportFrame(
                    frame=gpd.GeoDataFrame(
                        values,
                        geometry="geometry",
                        crs="EPSG:27700",
                    ),
                    source_export_fingerprint="e" * 64,
                ),
                asset_observations=(),
                road_observations=(),
                route_controls=None,
                profile=PlanningGraphProfile(canonical_crs="EPSG:27700"),
            )
        )

    assert compile_rows(rows).graph_fingerprint == compile_rows(
        list(reversed(rows))
    ).graph_fingerprint


def test_invalid_optional_oneway_claim_is_unknown_without_dropping_edge() -> None:
    frame = _frame().frame.iloc[:1].copy()
    frame["oneway"] = frame["oneway"].astype(object)
    frame.loc[0, "oneway"] = "maybe"

    snapshot = build_planning_graph(
        PlanningGraphRequest(
            routable_edges=SourceExportFrame(
                frame=frame,
                source_export_fingerprint="f" * 64,
            ),
            asset_observations=(),
            road_observations=(),
            route_controls=None,
            profile=PlanningGraphProfile(canonical_crs="EPSG:27700"),
        )
    )

    assert len(snapshot.edge_records) == 1
    assert snapshot.edge_records[0].oneway is None
    assert snapshot.edge_records[0].reciprocal_state == "unknown"
    assert [diagnostic.code for diagnostic in snapshot.diagnostics] == [
        "unknown-edge-claim"
    ]


def test_raw_access_claims_remain_separate_on_each_edge_record() -> None:
    frame = _frame().frame.iloc[:1].copy()
    frame["access"] = "permissive"
    frame["bicycle"] = "yes"
    frame["foot"] = "yes"

    snapshot = build_planning_graph(
        PlanningGraphRequest(
            routable_edges=SourceExportFrame(
                frame=frame,
                source_export_fingerprint="1" * 64,
            ),
            asset_observations=(),
            road_observations=(),
            route_controls=None,
            profile=PlanningGraphProfile(canonical_crs="EPSG:27700"),
        )
    )

    edge = snapshot.edge_records[0]
    assert edge.access == "permissive"
    assert edge.bicycle == "yes"
    assert edge.foot == "yes"
    assert edge.claim_observation_ids == ()
