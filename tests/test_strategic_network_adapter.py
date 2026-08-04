from __future__ import annotations

from types import SimpleNamespace

from test_strategic_network_planning import discovery, fixture_graph

from satn.candidate_discovery import CorridorObligation
from satn.strategic_network_adapter import discovery_from_preparation


def test_malformed_candidate_route_becomes_governed_gap_without_aborting() -> None:
    graph = fixture_graph()
    discovered = discovery(graph, CorridorObligation("corridor-a-d", "A", "D"))
    malformed_record = next(
        item for item in discovered.candidate_records if len(item.edge_ids) == 2
    )
    valid_record = next(item for item in discovered.candidate_records if len(item.edge_ids) == 1)

    malformed_prepared_record = SimpleNamespace(
        candidate=malformed_record.candidate_input,
        routing_edge_ids=tuple(reversed(malformed_record.edge_ids)),
        reverse_routing_edge_ids=malformed_record.reverse_edge_ids,
        evidence_ids=(),
        source_ids=(),
        generation_strategies=("fixture",),
    )
    valid_prepared_record = SimpleNamespace(
        candidate=valid_record.candidate_input,
        routing_edge_ids=valid_record.edge_ids,
        reverse_routing_edge_ids=valid_record.reverse_edge_ids,
        evidence_ids=(),
        source_ids=(),
        generation_strategies=("fixture",),
    )
    unit = SimpleNamespace(
        unit_id="unit-a-d",
        unit_role=SimpleNamespace(value="interurban-spine"),
        candidate_set=discovered.candidate_sets[0],
        candidate_records=(malformed_prepared_record, valid_prepared_record),
    )
    preparation = SimpleNamespace(
        units=(unit,),
        issues=(),
        preparation_fingerprint="p" * 64,
        profile_fingerprint="f" * 64,
    )

    result = discovery_from_preparation(preparation, graph)

    assert result.status == "complete-with-gaps"
    assert [item.candidate_id for item in result.candidate_records] == [valid_record.candidate_id]
    assert [item.candidate_id for item in result.candidate_sets[0].candidates] == [
        valid_record.candidate_id
    ]
    assert len(result.gaps) == 1
    gap = result.gaps[0]
    assert gap.obligation_id == "unit-a-d"
    assert "contiguous" in gap.reason
    assert result.search_diagnostics[0].candidate_id == malformed_record.candidate_id
    assert result.search_diagnostics[0].code == "malformed-prepared-route"
    assert result.evidence_requests[0].candidate_id == malformed_record.candidate_id

    repeated = discovery_from_preparation(preparation, graph)
    assert repeated.fingerprint == result.fingerprint
    assert repeated.gaps == result.gaps
    assert repeated.evidence_requests == result.evidence_requests
