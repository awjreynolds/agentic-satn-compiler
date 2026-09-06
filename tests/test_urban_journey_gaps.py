from types import SimpleNamespace

from test_strategic_network_planning import discovery, fixture_graph

from satn.candidate_discovery import CorridorObligation
from satn.strategic_network_planning import (
    StrategicNetworkPlanningRequest,
    compile_strategic_network,
)


def test_unroutable_urban_journey_is_a_visible_network_gap() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("urban-gap", "X", "Y"))
    preparation = SimpleNamespace(
        units=(
            SimpleNamespace(
                unit_id="urban-gap",
                urban_journey_id="town-a-to-town-b",
                backbone_required=False,
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
        )
    )
    assert result.status == "complete-with-gaps"
    assert [gap.obligation_id for gap in result.gaps] == ["urban-gap"]
