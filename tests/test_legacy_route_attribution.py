"""Legacy preparation retains actual routed source facts before graph handoff."""

import geopandas as gpd
from test_route_source_facts import PRECEDENCE, _graph

from satn.network_selection import CandidateSourceClass, NetworkSelectionProfile
from satn.strategic_corridors import StrategicCorridorUnitRole, _candidate_set


def test_preparation_does_not_promote_incidental_cycle_evidence() -> None:
    graph = _graph()
    option = graph.option("r0", "r2", "direct")
    assert option is not None
    option.ncn_share = 0.02
    profile = NetworkSelectionProfile(
        profile_id="route-attribution", candidate_source_precedence=PRECEDENCE
    )
    candidates, _records = _candidate_set(
        profile,
        graph,
        unit_role=StrategicCorridorUnitRole.INTERURBAN_SPINE,
        endpoints=("alpha", "beta"),
        mandatory_network_place_ids=(),
        start_node="r0",
        end_node="r2",
        source_ids=(),
        evidence_ids=(),
        context=gpd.GeoDataFrame(geometry=[], crs=27700),
        strategic_destination_id=None,
        precomputed_options={
            role: option for role in ("direct", "strategic-spine", "ncn-informed", "low-traffic")
        },
    )
    assert candidates.candidates
    for candidate in candidates.candidates:
        assert candidate.source_class is CandidateSourceClass.A_ROAD_CORRIDOR
        assert set(candidate.alignment_bases) == {"current-ncn", "mapped-cycleway", "a-road"}
        assert candidate.primary_alignment_basis == "a-road"
