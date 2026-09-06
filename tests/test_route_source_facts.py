from __future__ import annotations

import geopandas as gpd
from shapely.geometry import LineString
from test_candidate_discovery import partial_cycleway_graph
from test_effective_strategic_network import _legacy_fixture_preparation

from satn.alignment_selection import AlignmentCandidateInput, admit_candidate_set
from satn.effective_strategic_network import (
    EffectiveStrategicNetworkRequest,
    EffectiveStrategicNetworkStatus,
    compile_effective_strategic_network,
)
from satn.network_selection import CandidateSourceClass
from satn.route_source_facts import derive_route_source_facts
from satn.routing import RoadGraph

PRECEDENCE = (
    CandidateSourceClass.VERIFIED_EXISTING_ASSET,
    CandidateSourceClass.A_ROAD_CORRIDOR,
    CandidateSourceClass.B_ROAD_CORRIDOR,
    CandidateSourceClass.OTHER_ROUTABLE,
)


def _graph() -> RoadGraph:
    return RoadGraph(
        gpd.GeoDataFrame(
            [
                {
                    "osmid": "ncn-edge",
                    "u": "r0",
                    "v": "r1",
                    "oneway": True,
                    "highway": "cycleway",
                    "cycle_alignment_bases": ("current-ncn",),
                    "geometry": LineString([(0, 0), (2, 0)]),
                },
                {
                    "osmid": "a-edge",
                    "u": "r1",
                    "v": "r2",
                    "oneway": True,
                    "highway": "primary",
                    "ref": "A4",
                    "geometry": LineString([(2, 0), (100, 0)]),
                },
                {
                    "osmid": "cycle-edge",
                    "u": "s0",
                    "v": "s1",
                    "oneway": True,
                    "highway": "cycleway",
                    "geometry": LineString([(0, 100), (67, 100)]),
                },
                {
                    "osmid": "cycle-a-edge",
                    "u": "s1",
                    "v": "s2",
                    "oneway": True,
                    "highway": "primary",
                    "ref": "A36",
                    "geometry": LineString([(67, 100), (100, 100)]),
                },
                {
                    "osmid": "plain-cycle-edge",
                    "u": "p0",
                    "v": "p1",
                    "oneway": True,
                    "highway": "cycleway",
                    "geometry": LineString([(0, 200), (100, 200)]),
                },
            ],
            geometry="geometry",
            crs=27700,
        )
    )


def test_route_source_facts_uses_edge_extent_and_retains_minor_bases() -> None:
    graph = _graph()

    ncn_a = graph.option("r0", "r2", "direct")
    cycle_a = graph.option("s0", "s2", "direct")
    plain_cycle = graph.option("p0", "p1", "direct")

    assert ncn_a is not None
    assert cycle_a is not None
    assert plain_cycle is not None

    ncn_a_facts = derive_route_source_facts(ncn_a, graph, PRECEDENCE)
    cycle_a_facts = derive_route_source_facts(cycle_a, graph, PRECEDENCE)
    plain_cycle_facts = derive_route_source_facts(plain_cycle, graph, PRECEDENCE)

    assert ncn_a_facts.generation_source_class is CandidateSourceClass.A_ROAD_CORRIDOR
    assert ncn_a_facts.alignment_bases == ("current-ncn", "mapped-cycleway", "a-road")
    assert ncn_a_facts.primary_alignment_basis == "a-road"
    assert cycle_a_facts.generation_source_class is CandidateSourceClass.VERIFIED_EXISTING_ASSET
    assert cycle_a_facts.alignment_bases == ("mapped-cycleway", "a-road")
    assert cycle_a_facts.primary_alignment_basis == "mapped-cycleway"
    assert plain_cycle_facts.generation_source_class is CandidateSourceClass.VERIFIED_EXISTING_ASSET
    assert plain_cycle_facts.alignment_bases == ("mapped-cycleway",)
    assert plain_cycle_facts.primary_alignment_basis == "mapped-cycleway"
    assert "current-ncn" not in plain_cycle_facts.alignment_bases


def test_route_source_facts_keeps_unresolved_edges_unclassified() -> None:
    facts = derive_route_source_facts(("missing-edge",), _graph(), PRECEDENCE)

    assert facts.complete is False
    assert facts.generation_source_class is None
    assert facts.primary_alignment_basis is None
    assert facts.unresolved_edge_ids == ("missing-edge",)


def test_legacy_effective_compile_preserves_primary_and_mixed_route_state() -> None:
    graph = partial_cycleway_graph()
    preparation = _legacy_fixture_preparation(graph)

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=graph,
            preparation=preparation,
            area_fingerprint="b" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    mixed = next(
        candidate
        for candidate in state.candidate_sets[0].candidates
        if "mapped-cycleway" in candidate.alignment_bases and "a-road" in candidate.alignment_bases
    )
    assert {"mapped-cycleway", "a-road", "local-connector"}.issubset(mixed.alignment_bases)
    assert mixed.primary_alignment_basis == "mapped-cycleway"
    assert mixed.reuse_class.value == "existing-cycle-provision"
    assert mixed.intervention_state.value == "proposed-new-link"


def test_legacy_effective_compile_preserves_explicit_typed_primary_basis() -> None:
    graph = partial_cycleway_graph()
    preparation = _legacy_fixture_preparation(graph)
    unit = preparation.units[0]
    source_set = unit.candidate_set
    selected = next(
        candidate
        for candidate in source_set.candidates
        if "mapped-cycleway" in candidate.alignment_bases and "a-road" in candidate.alignment_bases
    )
    typed = AlignmentCandidateInput.model_validate(
        {
            **selected.model_dump(mode="python", exclude={"candidate_id"}),
            "alignment_bases": tuple(sorted({*selected.alignment_bases, "current-ncn"})),
            "primary_alignment_basis": "current-ncn",
        }
    )
    candidates = tuple(
        typed if candidate.candidate_id == selected.candidate_id else candidate
        for candidate in source_set.candidates
    )
    typed_set = admit_candidate_set(
        source_set.profile,
        network_role=source_set.network_role,
        endpoints=source_set.endpoints,
        candidates=candidates,
        mandatory_network_place_ids=source_set.mandatory_network_place_ids,
        mandatory_access_obligation_ids=source_set.mandatory_access_obligation_ids,
        mandatory_strategic_destination_ids=source_set.mandatory_strategic_destination_ids,
    )
    prepared_records = tuple(
        type(
            "Prepared",
            (),
            {
                "candidate": typed
                if record.candidate.candidate_id == selected.candidate_id
                else record.candidate,
                "routing_edge_ids": record.routing_edge_ids,
                "reverse_routing_edge_ids": record.reverse_routing_edge_ids,
                "evidence_ids": record.evidence_ids,
                "source_ids": record.source_ids,
                "generation_strategies": record.generation_strategies,
            },
        )()
        for record in unit.candidate_records
    )
    typed_unit = type(
        "LegacyUnit",
        (),
        {
            "unit_id": unit.unit_id,
            "unit_role": unit.unit_role,
            "candidate_set": typed_set,
            "candidate_records": prepared_records,
            "routing_start_node_id": unit.routing_start_node_id,
            "routing_end_node_id": unit.routing_end_node_id,
        },
    )()
    typed_preparation = type(
        "LegacyPreparation",
        (),
        {
            "units": (typed_unit,),
            "issues": (),
            "preparation_fingerprint": preparation.preparation_fingerprint,
            "profile_fingerprint": typed_set.profile_fingerprint,
        },
    )()

    state = compile_effective_strategic_network(
        EffectiveStrategicNetworkRequest(
            routable_network=graph,
            preparation=typed_preparation,
            area_fingerprint="b" * 64,
            snapshot_fingerprint=graph.source_export_fingerprint,
        )
    )

    assert state.status is EffectiveStrategicNetworkStatus.EVALUATED
    mixed = next(
        candidate
        for candidate in state.candidate_sets[0].candidates
        if "current-ncn" in candidate.alignment_bases
    )
    assert "local-connector" in mixed.alignment_bases
    assert mixed.primary_alignment_basis == "current-ncn"
