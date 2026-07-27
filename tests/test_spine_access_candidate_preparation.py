"""Public compiler seam tests for bounded Spine Access candidate preparation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import date
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString

import satn.spine_access_candidate_preparation as preparation
from satn.models import CouncilConfig, SourceConfig
from satn.network_selection import NetworkSelectionProfile
from satn.pipeline import compilation_governed_input_fingerprint
from satn.psa_evidence_loaders import GovernedEvidenceLoadError
from satn.routing import RoadGraph, RouteOption
from satn.spine_access_candidate_preparation import prepare_spine_access_candidates


def profile(*, include_b_road: bool = False, maximum_options: int = 5) -> NetworkSelectionProfile:
    precedence = [
        "verified-existing-asset",
        "a-road-corridor",
    ]
    if include_b_road:
        precedence.append("b-road-corridor")
    precedence.append("other-routable")
    return NetworkSelectionProfile.model_validate(
        {
            "profile_id": "public-compiler-integration-v1",
            "candidate_source_precedence": precedence,
            "ambiguity": {"maximum_options_per_candidate_set": maximum_options},
        }
    )


def empty_source_config() -> SourceConfig:
    return SourceConfig(snapshot_dir=Path("snapshots"))


def routing_graph(*, include_b_road: bool = False) -> RoadGraph:
    rows = [
        {
            "u": "community-node",
            "v": "target-node",
            "osmid": "direct",
            "length": 1000.0,
            "highway": "residential",
            "ref": None,
            "oneway": False,
            "satn_ncn": False,
            "geometry": LineString([(400000, 170000), (401000, 170000)]),
        },
        {
            "u": "community-node",
            "v": "a-mid",
            "osmid": "a-left",
            "length": 550.0,
            "highway": "primary",
            "ref": "A4",
            "oneway": False,
            "satn_ncn": False,
            "geometry": LineString([(400000, 170000), (400500, 170100)]),
        },
        {
            "u": "a-mid",
            "v": "target-node",
            "osmid": "a-right",
            "length": 550.0,
            "highway": "primary",
            "ref": "A4",
            "oneway": False,
            "satn_ncn": False,
            "geometry": LineString([(400500, 170100), (401000, 170000)]),
        },
        {
            "u": "community-node",
            "v": "ncn-mid",
            "osmid": "ncn-left",
            "length": 600.0,
            "highway": "cycleway",
            "ref": None,
            "oneway": False,
            "satn_ncn": True,
            "geometry": LineString([(400000, 170000), (400500, 169900)]),
        },
        {
            "u": "ncn-mid",
            "v": "target-node",
            "osmid": "ncn-right",
            "length": 600.0,
            "highway": "cycleway",
            "ref": None,
            "oneway": False,
            "satn_ncn": True,
            "geometry": LineString([(400500, 169900), (401000, 170000)]),
        },
    ]
    if include_b_road:
        rows.extend(
            [
                {
                    "u": "community-node",
                    "v": "b-mid",
                    "osmid": "b-left",
                    "length": 575.0,
                    "highway": "secondary",
                    "ref": "B3116",
                    "oneway": False,
                    "satn_ncn": False,
                    "geometry": LineString(
                        [(400000, 170000), (400500, 170200)]
                    ),
                },
                {
                    "u": "b-mid",
                    "v": "target-node",
                    "osmid": "b-right",
                    "length": 575.0,
                    "highway": "secondary",
                    "ref": "B3116",
                    "oneway": False,
                    "satn_ncn": False,
                    "geometry": LineString(
                        [(400500, 170200), (401000, 170000)]
                    ),
                },
            ]
        )
    rows.extend(
        {
            **row,
            "u": row["v"],
            "v": row["u"],
            "osmid": f"{row['osmid']}-reverse",
            "geometry": LineString(list(reversed(row["geometry"].coords))),
        }
        for row in list(rows)
    )
    return RoadGraph(gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:27700"))


def connections() -> gpd.GeoDataFrame:
    rows = [
        {
            "access_connection_id": "access-connection-1",
            "obligation_id": "access-obligation-1",
            "obligation_kind": "community",
            "place_id": "community-1",
            "community_id": "community-1",
            "spine_id": "strategic-spine-1",
            "root_spine_id": "strategic-spine-1",
            "parent_role": "spine-access-connection",
            "parent_target_id": "parent-access-connection-2",
            "parent_place_id": "parent-community-2",
            "community_attachment_node": "community-node",
            "target_attachment_node": "target-node",
            "spine_attachment_node": "target-node",
            "source_ids": "[\"source-route-1\"]",
            "provenance": "{\"source_ids\":[\"source-route-1\"]}",
            "geometry": LineString([(400000, 170000), (401000, 170000)]),
        },
        {
            "access_connection_id": "school-connection-1",
            "obligation_id": "school-obligation-1",
            "obligation_kind": "school",
            "place_id": "school-1",
            "community_id": None,
            "spine_id": "strategic-spine-1",
            "parent_target_id": "strategic-spine-1",
            "community_attachment_node": "community-node",
            "target_attachment_node": "target-node",
            "spine_attachment_node": "target-node",
            "geometry": LineString([(400000, 170000), (401000, 170000)]),
        },
    ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:27700")


def obligations() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "access_connection_id": "access-connection-1",
                "obligation_id": "access-obligation-1",
                "geometry": LineString([(400000, 170000), (400001, 170000)]),
            }
        ],
        geometry="geometry",
        crs="EPSG:27700",
    )


def spines() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "spine_id": "strategic-spine-1",
                "spine_kind": "a-road",
                "source_id": "strategic-source-exact",
                "evidence_id": "strategic-evidence-exact",
                "provenance": "{\"evidence_ids\":[\"original-evidence-7\"]}",
                "geometry": LineString([(400900, 169900), (401100, 170100)]),
            }
        ],
        geometry="geometry",
        crs="EPSG:27700",
    )


def current_asset_context() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "feature_type": "ncn-route",
                "ncn_evidence_role": "established-route",
                "evidence_id": "current-ncn-evidence",
                "source_id": "official-ncn-source",
                "geometry": LineString(
                    [(400000, 170000), (400500, 169900), (401000, 170000)]
                ),
            },
            {
                "feature_type": "declassified-ncn-route",
                "ncn_evidence_role": "declassified-route",
                "evidence_id": "former-ncn-evidence",
                "source_id": "official-former-ncn-source",
                "geometry": LineString([(400000, 170010), (401000, 170010)]),
            },
        ],
        geometry="geometry",
        crs="EPSG:27700",
    )


def official_b_roads() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "official-b3116",
                "official_classification": "b-road",
                "source_id": "official-highway-list",
                "effective_date": "2026-04-01",
                "licence": "Open Government Licence v3.0",
                "content_fingerprint": "b" * 64,
                "geometry": LineString(
                    [(400000, 170000), (400500, 170200), (401000, 170000)]
                ),
            }
        ],
        geometry="geometry",
        crs="EPSG:27700",
    )


def prepare_without_evidence() -> preparation.SpineAccessCandidatePreparationResult:
    return prepare_spine_access_candidates(
        profile(),
        road_graph=routing_graph(),
        spine_access_connections=connections(),
        access_obligations=obligations(),
        strategic_spines=spines(),
        context=current_asset_context(),
        official_road_classification=None,
        source_config=empty_source_config(),
        config_directory=Path.cwd(),
    )


def test_prepares_finite_candidates_per_actual_community_connection_only() -> None:
    result = prepare_without_evidence()

    assert result.status == "incomplete"
    assert result.missing_inputs == (
        "population-reach-evidence",
        "school-register-evidence",
    )
    assert len(result.prepared_spine_access_connections) == 1
    prepared = result.prepared_spine_access_connections[0]
    assert prepared.access_connection_id == "access-connection-1"
    assert prepared.candidate_set.maximum_options == 5
    assert len(prepared.candidate_set.candidates) <= 5
    assert result.diagnostics["school_branch_candidates_generated"] == 0
    assert result.diagnostics["selection_performed"] is False
    assert result.diagnostics["agent_runtime_invoked"] is False
    assert result.diagnostics["spine_access_connection_count"] == 1
    assert "community_connection_count" not in result.diagnostics
    assert result.contract == "satn-spine-access-candidate-preparation/v1"
    assert result.metadata()["contract"] == result.contract


def test_maps_current_ncn_a_road_and_other_without_declassified_advantage() -> None:
    prepared = prepare_without_evidence().prepared_spine_access_connections[0]
    classes = {candidate.source_class.value for candidate in prepared.candidate_set.candidates}

    assert classes == {
        "verified-existing-asset",
        "a-road-corridor",
        "other-routable",
    }
    current = next(
        item.canonical()
        for item in prepared.candidate_records
        if item.candidate.source_class.value == "verified-existing-asset"
    )
    assert {
        evidence["evidence_id"] for evidence in current["current_asset_evidence"]
    } == {"current-ncn-evidence"}
    assert prepared.strategic_source_id == "strategic-source-exact"
    assert prepared.strategic_evidence_id == "strategic-evidence-exact"
    assert prepared.strategic_provenance == (
        "{\"evidence_ids\":[\"original-evidence-7\"]}"
    )
    result_scope = prepare_without_evidence().diagnostics["scope"]
    assert result_scope == "spine-access-candidate-preparation"
    assert all(
        "selected" not in item["rationale"].lower()
        and "preferred" not in item["rationale"].lower()
        for item in prepared.candidate_generation_rationales
    )


def test_candidate_records_are_complete_immutable_pre_admission_evidence() -> None:
    prepared = prepare_without_evidence().prepared_spine_access_connections[0]
    rejected = next(
        item
        for item in prepared.candidate_records
        if item.preparation_disposition.startswith("rejected-")
    )
    canonical = rejected.canonical()

    assert canonical["candidate"]["geometry"]["coordinates"]
    assert canonical["geometry_fingerprint"] == rejected.candidate.geometry.fingerprint
    assert canonical["source_class"]
    assert canonical["topology_state"]
    assert canonical["endpoints"]
    assert canonical["served_network_place_ids"]
    assert canonical["served_access_obligation_ids"]
    assert "served_strategic_destination_ids" in canonical
    assert canonical["directness_m"] >= 0
    assert canonical["rejection_reason"]
    assert canonical["retained_candidate_id"]
    assert canonical["connection"]["access_connection_id"] == "access-connection-1"
    with pytest.raises(FrozenInstanceError):
        rejected.preparation_disposition = "mutated"  # type: ignore[misc]


def test_routing_deduplication_is_recorded_and_admission_enforces_profile_limit() -> None:
    result = prepare_spine_access_candidates(
        profile(maximum_options=2),
        road_graph=routing_graph(),
        spine_access_connections=connections(),
        access_obligations=obligations(),
        strategic_spines=spines(),
        context=current_asset_context(),
        official_road_classification=None,
        source_config=empty_source_config(),
        config_directory=Path.cwd(),
    )
    candidate_set = result.prepared_spine_access_connections[0].candidate_set

    assert sum(item.disposition.value == "admitted" for item in candidate_set.admissions) == 2
    assert any(
        item.rationale.value == "profile-candidate-limit"
        for item in candidate_set.admissions
    )
    assert any(
        item.reason == "exact-equivalent-routing-geometry"
        and item.candidate_id is not None
        and item.retained_candidate_id is not None
        for item in result.generation_issues
    )


def test_material_representative_prefers_current_asset_over_earlier_direct_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry = LineString([(400000, 170000), (401000, 170000)])
    direct = RouteOption(
        role="direct",
        geometry=geometry,
        length_km=1.0,
        edge_ids=["direct"],
        a_road_share=0.0,
        ncn_share=0.0,
        bidirectional=True,
        reverse_length_km=1.0,
        reverse_edge_ids=["direct"],
        reverse_corridor_share=1.0,
        impracticable_alongside=False,
    )
    ncn = RouteOption(
        role="ncn-informed",
        geometry=geometry,
        length_km=1.0,
        edge_ids=["ncn-left", "ncn-right"],
        a_road_share=0.0,
        ncn_share=1.0,
        bidirectional=True,
        reverse_length_km=1.0,
        reverse_edge_ids=["ncn-right", "ncn-left"],
        reverse_corridor_share=1.0,
        impracticable_alongside=False,
    )
    monkeypatch.setattr(
        preparation,
        "choose_alignment",
        lambda _graph, _start, _end: (direct, [direct, ncn], "direct-first fixture"),
    )
    current = current_asset_context().iloc[[0]].copy()
    current.geometry = [geometry]

    result = prepare_spine_access_candidates(
        profile(),
        road_graph=routing_graph(),
        spine_access_connections=connections(),
        access_obligations=obligations(),
        strategic_spines=spines(),
        context=current,
        official_road_classification=None,
        source_config=empty_source_config(),
        config_directory=Path.cwd(),
    )
    prepared = result.prepared_spine_access_connections[0]

    assert [item.source_class.value for item in prepared.candidate_set.candidates] == [
        "verified-existing-asset"
    ]
    rejected = next(
        item
        for item in prepared.candidate_records
        if item.preparation_disposition.startswith("rejected-")
    )
    retained = next(
        item
        for item in prepared.candidate_records
        if item.preparation_disposition == "retained-representative"
    )
    assert rejected.candidate.source_class.value == "other-routable"
    assert retained.candidate.source_class.value == "verified-existing-asset"
    assert rejected.retained_candidate_id == retained.candidate.candidate_id
    assert retained.routing_edge_ids == ("ncn-left", "ncn-right")
    assert retained.reverse_routing_edge_ids == ("ncn-right", "ncn-left")
    assert retained.canonical()["reverse_routing_edge_ids"] == [
        "ncn-right",
        "ncn-left",
    ]


def test_material_clustering_prevents_profile_limit_dangling_duplicate_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ncn = RouteOption(
        role="ncn-informed",
        geometry=LineString(
            [(400000, 170000), (400500, 169900), (401000, 170000)]
        ),
        length_km=1.2,
        edge_ids=["ncn-left", "ncn-right"],
        a_road_share=0.0,
        ncn_share=1.0,
        bidirectional=True,
        reverse_length_km=1.2,
        reverse_edge_ids=["ncn-right", "ncn-left"],
        reverse_corridor_share=1.0,
        impracticable_alongside=False,
    )
    a_road = RouteOption(
        role="strategic-spine",
        geometry=LineString(
            [(400000, 170000), (400500, 170100), (401000, 170000)]
        ),
        length_km=1.1,
        edge_ids=["a-left", "a-right"],
        a_road_share=1.0,
        ncn_share=0.0,
        bidirectional=True,
        reverse_length_km=1.1,
        reverse_edge_ids=["a-right", "a-left"],
        reverse_corridor_share=1.0,
        impracticable_alongside=False,
    )
    shifted_other = RouteOption(
        role="direct",
        geometry=LineString(
            [(400000, 170000.01), (400500, 170100.01), (401000, 170000.01)]
        ),
        length_km=1.1,
        edge_ids=["direct"],
        a_road_share=0.0,
        ncn_share=0.0,
        bidirectional=True,
        reverse_length_km=1.1,
        reverse_edge_ids=["direct"],
        reverse_corridor_share=1.0,
        impracticable_alongside=False,
    )
    monkeypatch.setattr(
        preparation,
        "choose_alignment",
        lambda _graph, _start, _end: (
            ncn,
            [ncn, a_road, shifted_other],
            "material cluster fixture",
        ),
    )

    result = prepare_spine_access_candidates(
        profile(maximum_options=1),
        road_graph=routing_graph(),
        spine_access_connections=connections(),
        access_obligations=obligations(),
        strategic_spines=spines(),
        context=current_asset_context(),
        official_road_classification=None,
        source_config=empty_source_config(),
        config_directory=Path.cwd(),
    )
    candidate_set = result.prepared_spine_access_connections[0].candidate_set

    assert len(candidate_set.candidates) == 2
    assert sum(item.disposition.value == "admitted" for item in candidate_set.admissions) == 1
    issue = next(
        item
        for item in result.generation_issues
        if item.reason == "materially-equivalent-routing-geometry"
    )
    retained = next(
        item
        for item in result.prepared_spine_access_connections[0].candidate_records
        if item.candidate.candidate_id == issue.retained_candidate_id
    )
    assert retained.candidate.source_class.value == "a-road-corridor"


def test_topology_unsatisfied_ncn_cannot_suppress_valid_direct_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry = LineString([(400000, 170000), (401000, 170000)])
    direct = RouteOption(
        role="direct",
        geometry=geometry,
        length_km=1.0,
        edge_ids=["direct"],
        a_road_share=0.0,
        ncn_share=0.0,
        bidirectional=True,
        reverse_length_km=1.0,
        reverse_edge_ids=["direct"],
        reverse_corridor_share=1.0,
        impracticable_alongside=False,
    )
    invalid_ncn = RouteOption(
        role="ncn-informed",
        geometry=geometry,
        length_km=1.0,
        edge_ids=["ncn"],
        a_road_share=0.0,
        ncn_share=1.0,
        bidirectional=False,
        reverse_length_km=None,
        reverse_edge_ids=[],
        reverse_corridor_share=0.0,
        impracticable_alongside=False,
    )
    monkeypatch.setattr(
        preparation,
        "choose_alignment",
        lambda _graph, _start, _end: (
            invalid_ncn,
            [invalid_ncn, direct],
            "legacy selection text must not escape",
        ),
    )
    current = current_asset_context().iloc[[0]].copy()
    current.geometry = [geometry]

    result = prepare_spine_access_candidates(
        profile(),
        road_graph=routing_graph(),
        spine_access_connections=connections(),
        access_obligations=obligations(),
        strategic_spines=spines(),
        context=current,
        official_road_classification=None,
        source_config=empty_source_config(),
        config_directory=Path.cwd(),
    )
    prepared = result.prepared_spine_access_connections[0]

    assert [item.source_class.value for item in prepared.candidate_set.candidates] == [
        "other-routable"
    ]
    invalid_record = next(
        item
        for item in prepared.candidate_records
        if item.route_role == "ncn-informed"
    )
    assert invalid_record.preparation_disposition == "rejected-topology-unsatisfied"
    assert invalid_record.rejection_reason == "topology-unsatisfied"
    assert invalid_record.retained_candidate_id is None
    assert any(
        issue.reason == "topology-unsatisfied"
        and issue.candidate_id == invalid_record.candidate.candidate_id
        for issue in result.generation_issues
    )


def test_unknown_topology_never_beats_satisfied_material_candidate_by_precedence() -> None:
    prepared = prepare_without_evidence().prepared_spine_access_connections[0]
    satisfied_record = next(
        item
        for item in prepared.candidate_records
        if item.route_role == "direct"
        and item.candidate.topology_state == preparation.CriterionState.SATISFIED
    )
    unknown_payload = satisfied_record.candidate.model_dump(
        mode="json",
        exclude={"candidate_id"},
    )
    unknown_payload["source_class"] = "verified-existing-asset"
    unknown_payload["topology_state"] = "unknown"
    unknown_candidate = preparation.AlignmentCandidateInput.model_validate(
        unknown_payload
    )
    unknown_record = replace(
        satisfied_record,
        candidate=unknown_candidate,
        route_role="ncn-informed",
        review_required=True,
    )

    admitted, records, issues = preparation._material_representatives(
        profile(),
        access_connection_id="access-connection-1",
        generated=[
            preparation._GeneratedCandidate(
                candidate=unknown_candidate,
                route_role="ncn-informed",
                evidence_quality=1.0,
                record=unknown_record,
            ),
            preparation._GeneratedCandidate(
                candidate=satisfied_record.candidate,
                route_role="direct",
                evidence_quality=0.0,
                record=satisfied_record,
            ),
        ],
    )

    assert admitted == (satisfied_record.candidate,)
    rejected_unknown = next(
        item for item in records if item.candidate.candidate_id == unknown_candidate.candidate_id
    )
    assert rejected_unknown.review_required is True
    assert rejected_unknown.retained_candidate_id == satisfied_record.candidate.candidate_id
    assert any(
        issue.reason == "topology-unknown-review-required"
        and issue.candidate_id == unknown_candidate.candidate_id
        for issue in issues
    )


def test_direct_spine_attachment_is_explicitly_out_of_scope_not_unresolved() -> None:
    frame = connections()
    mask = frame["obligation_kind"].eq("community")
    frame.loc[mask, "parent_role"] = "strategic-spine"
    frame.loc[mask, "parent_target_id"] = "strategic-spine-1"
    frame.loc[mask, "parent_place_id"] = None

    result = prepare_spine_access_candidates(
        profile(),
        road_graph=routing_graph(),
        spine_access_connections=frame,
        access_obligations=obligations(),
        strategic_spines=spines(),
        context=current_asset_context(),
        official_road_classification=None,
        source_config=empty_source_config(),
        config_directory=Path.cwd(),
    )

    assert result.prepared_spine_access_connections == ()
    assert len(result.connection_roster) == 1
    roster = result.connection_roster[0]
    assert roster.disposition == "out-of-scope-direct-strategic-spine"
    assert roster.reason == "out-of-scope-direct-strategic-spine-attachment"
    assert any(
        issue.reason == "out-of-scope-direct-strategic-spine-attachment"
        for issue in result.generation_issues
    )
    assert result.diagnostics["out_of_scope_connection_count"] == 1
    assert result.diagnostics["unresolved_connection_count"] == 0


@pytest.mark.parametrize("parent_place_id", [None, float("nan"), ""])
def test_missing_parent_place_is_an_explicit_unresolved_roster_gap(
    parent_place_id: object,
) -> None:
    frame = connections()
    frame["parent_place_id"] = frame["parent_place_id"].astype(object)
    frame.loc[frame["obligation_kind"].eq("community"), "parent_place_id"] = (
        parent_place_id
    )

    result = prepare_spine_access_candidates(
        profile(),
        road_graph=routing_graph(),
        spine_access_connections=frame,
        access_obligations=obligations(),
        strategic_spines=spines(),
        context=current_asset_context(),
        official_road_classification=None,
        source_config=empty_source_config(),
        config_directory=Path.cwd(),
    )
    assert result.prepared_spine_access_connections == ()
    assert result.connection_roster[0].disposition == "unresolved-gap"
    assert result.connection_roster[0].reason == "missing-parent-network-place-endpoint"
    assert any(
        issue.reason == "missing-parent-network-place-endpoint"
        for issue in result.generation_issues
    )


def test_missing_parent_identifiers_never_promote_an_attachment_node() -> None:
    frame = connections()
    mask = frame["obligation_kind"].eq("community")
    frame.loc[mask, ["parent_place_id", "parent_target_id"]] = None

    result = prepare_spine_access_candidates(
        profile(),
        road_graph=routing_graph(),
        spine_access_connections=frame,
        access_obligations=obligations(),
        strategic_spines=spines(),
        context=current_asset_context(),
        official_road_classification=None,
        source_config=empty_source_config(),
        config_directory=Path.cwd(),
    )

    assert result.prepared_spine_access_connections == ()
    assert any(
        issue.reason == "missing-parent-network-place-endpoint"
        for issue in result.generation_issues
    )


def test_enabled_b_road_candidate_requires_and_retains_official_evidence() -> None:
    result = prepare_spine_access_candidates(
        profile(include_b_road=True),
        road_graph=routing_graph(include_b_road=True),
        spine_access_connections=connections(),
        access_obligations=obligations(),
        strategic_spines=spines(),
        context=current_asset_context(),
        official_road_classification=official_b_roads(),
        source_config=empty_source_config(),
        config_directory=Path.cwd(),
    )
    prepared = result.prepared_spine_access_connections[0]
    b_candidate = next(
        item.canonical()
        for item in prepared.candidate_records
        if item.candidate.source_class.value == "b-road-corridor"
    )

    assert b_candidate["route_role"] == "b-road-corridor"
    assert b_candidate["official_b_road_share"] > 0
    assert {
        item["official_feature_id"]
        for item in b_candidate["official_b_road_evidence"]
    } == {"official-b3116"}


def test_unverified_b_road_is_a_complete_immutable_rejected_record() -> None:
    result = prepare_spine_access_candidates(
        profile(include_b_road=True),
        road_graph=routing_graph(include_b_road=True),
        spine_access_connections=connections(),
        access_obligations=obligations(),
        strategic_spines=spines(),
        context=current_asset_context(),
        official_road_classification=None,
        source_config=empty_source_config(),
        config_directory=Path.cwd(),
    )
    prepared = result.prepared_spine_access_connections[0]
    rejected = next(
        item for item in prepared.candidate_records if item.route_role == "b-road-corridor"
    )
    canonical = rejected.canonical()

    assert rejected.preparation_disposition == "rejected-b-road-evidence-unverified"
    assert rejected.rejection_reason == "b-road-evidence-unverified"
    assert rejected.candidate.candidate_id not in {
        item.candidate_id for item in prepared.candidate_set.candidates
    }
    assert canonical["candidate"]["geometry"]["coordinates"]
    assert canonical["geometry_fingerprint"]
    assert canonical["source_class"] == "other-routable"
    assert canonical["topology_state"] == "satisfied"
    assert canonical["endpoints"] == ["community-1", "parent-community-2"]
    assert canonical["served_network_place_ids"] == canonical["endpoints"]
    assert canonical["served_access_obligation_ids"] == ["access-obligation-1"]
    assert canonical["served_strategic_destination_ids"] == []
    assert canonical["directness_m"] > 0
    assert canonical["generation_rationale"]
    assert canonical["connection"]["access_connection_id"] == "access-connection-1"
    issue = next(
        item
        for item in result.generation_issues
        if item.reason == "b-road-evidence-unverified"
    )
    assert issue.candidate_id == rejected.candidate.candidate_id
    assert issue.source_class == "other-routable"


def test_disabled_b_road_profile_generates_no_b_candidate() -> None:
    result = prepare_spine_access_candidates(
        profile(),
        road_graph=routing_graph(include_b_road=True),
        spine_access_connections=connections(),
        access_obligations=obligations(),
        strategic_spines=spines(),
        context=current_asset_context(),
        official_road_classification=official_b_roads(),
        source_config=empty_source_config(),
        config_directory=Path.cwd(),
    )

    assert "b-road-corridor" not in {
        item.candidate.source_class.value
        for item in result.prepared_spine_access_connections[0].candidate_records
    }


def test_disconnected_multipart_route_is_an_explicit_generation_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = RouteOption(
        role="direct",
        geometry=MultiLineString(
            [
                [(400000, 170000), (400100, 170000)],
                [(400500, 170000), (400600, 170000)],
            ]
        ),
        length_km=0.2,
        edge_ids=["invalid"],
        a_road_share=0.0,
        ncn_share=0.0,
        bidirectional=True,
        reverse_length_km=0.2,
        reverse_edge_ids=["invalid"],
        reverse_corridor_share=1.0,
        impracticable_alongside=False,
    )
    monkeypatch.setattr(
        preparation,
        "choose_alignment",
        lambda _graph, _start, _end: (invalid, [invalid], "fixture route"),
    )

    result = prepare_without_evidence()

    assert (
        result.prepared_spine_access_connections[0].candidate_set.candidates == ()
    )
    assert result.generation_issues[0].reason == "disconnected-multipart-route"


def artifact(path: Path, *, source_id: str, content: bytes) -> dict[str, object]:
    path.write_bytes(content)
    return {
        "source_id": source_id,
        "path": path,
        "release": "fixture-release",
        "effective_date": "2021-03-21",
        "licence": "fixture-licence",
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "redistribution": "public",
    }


def council_with_population_files(
    tmp_path: Path,
    *,
    enabled: bool = True,
) -> CouncilConfig:
    config_path = tmp_path / "area.yaml"
    config_path.write_text("fixture: true\n", encoding="utf-8")
    snapshot = tmp_path / "snapshots" / "current"
    snapshot.mkdir(parents=True)
    (snapshot / "snapshot.json").write_text("{}", encoding="utf-8")
    population = {
        "output_area_geometry": artifact(
            tmp_path / "oa.geojson",
            source_id="oa-geometry",
            content=b"{\"oa\":1}",
        ),
        "population_weighted_centroids": artifact(
            tmp_path / "pwc.geojson",
            source_id="oa-pwc",
            content=b"{\"pwc\":2}",
        ),
        "usual_resident_counts": artifact(
            tmp_path / "counts.json",
            source_id="oa-counts",
            content=b"{\"counts\":3}",
        ),
    }
    return CouncilConfig(
        config_path=config_path,
        council_id="fixture",
        council_name="Fixture",
        source={
            "snapshot_dir": tmp_path / "snapshots",
            "population_reach_evidence": population,
        },
        compilation={"network_selection": profile()} if enabled else {},
        publication={"output_dir": tmp_path / "publication", "title": "Fixture SATN"},
    )


def compiler_manifest() -> dict[str, object]:
    return {"sha256": "d" * 64, "components": []}


def test_psa_file_bytes_enter_reuse_identity_before_publication_reuse(tmp_path: Path) -> None:
    council = council_with_population_files(tmp_path)
    first = compilation_governed_input_fingerprint(
        council,
        dependency_manifest=compiler_manifest(),
    )
    configured = council.source.population_reach_evidence
    assert configured is not None
    configured.output_area_geometry.path.write_bytes(b"{\"oa\":\"changed\"}")

    changed = compilation_governed_input_fingerprint(
        council,
        dependency_manifest=compiler_manifest(),
    )

    assert changed != first


def test_missing_declared_psa_file_fails_closed_before_reuse(tmp_path: Path) -> None:
    council = council_with_population_files(tmp_path)
    configured = council.source.population_reach_evidence
    assert configured is not None
    configured.output_area_geometry.path.unlink()

    with pytest.raises(ValueError, match="configured governed input file is missing"):
        compilation_governed_input_fingerprint(
            council,
            dependency_manifest=compiler_manifest(),
        )


def test_no_profile_does_not_promote_psa_file_bytes_into_legacy_reuse_identity(
    tmp_path: Path,
) -> None:
    council = council_with_population_files(tmp_path, enabled=False)
    first = compilation_governed_input_fingerprint(
        council,
        dependency_manifest=compiler_manifest(),
    )
    configured = council.source.population_reach_evidence
    assert configured is not None
    configured.output_area_geometry.path.write_bytes(b"{\"oa\":\"changed\"}")

    assert (
        compilation_governed_input_fingerprint(
            council,
            dependency_manifest=compiler_manifest(),
        )
        == first
    )


def test_declared_content_mismatch_fails_closed_instead_of_becoming_incomplete(
    tmp_path: Path,
) -> None:
    council = council_with_population_files(tmp_path)
    configured = council.source.population_reach_evidence
    assert configured is not None
    configured.output_area_geometry.path.write_bytes(b"tampered")

    with pytest.raises(GovernedEvidenceLoadError, match="SHA-256 mismatch"):
        prepare_spine_access_candidates(
            profile(),
            road_graph=routing_graph(),
            spine_access_connections=connections(),
            access_obligations=obligations(),
            strategic_spines=spines(),
            context=current_asset_context(),
            official_road_classification=None,
            source_config=council.source,
            config_directory=tmp_path,
        )


def school_artifact(tmp_path: Path) -> dict[str, object]:
    return {
        "school_register": artifact(
            tmp_path / "schools.json",
            source_id="schools",
            content=json.dumps({"schema": "fixture"}).encode(),
        )
    }


def test_school_only_freshness_is_valid_and_admissions_freshness_is_conditional(
    tmp_path: Path,
) -> None:
    school_only = SourceConfig(
        snapshot_dir=Path("snapshots"),
        school_register_evidence=school_artifact(tmp_path),
        network_selection_as_at=date(2026, 7, 26),
        network_selection_school_register_max_age_days=365,
    )
    assert school_only.strategic_education_destination_admissions is None

    with pytest.raises(
        ValueError,
        match="strategic-admissions freshness requires an admissions artifact",
    ):
        SourceConfig(
            snapshot_dir=Path("snapshots"),
            network_selection_strategic_admissions_max_age_days=365,
        )


def test_admissions_artifact_requires_its_own_freshness_window(tmp_path: Path) -> None:
    admissions = {
        "admissions": artifact(
            tmp_path / "admissions.json",
            source_id="admissions",
            content=b"{\"schema\":\"fixture\"}",
        )
    }
    with pytest.raises(
        ValueError,
        match="strategic-admissions evidence requires a declared freshness window",
    ):
        SourceConfig(
            snapshot_dir=Path("snapshots"),
            school_register_evidence=school_artifact(tmp_path),
            strategic_education_destination_admissions=admissions,
            network_selection_as_at=date(2026, 7, 26),
            network_selection_school_register_max_age_days=365,
        )
