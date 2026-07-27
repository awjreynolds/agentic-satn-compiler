"""Fail-closed strategic Reference replay tests."""

from __future__ import annotations

from copy import deepcopy

import pytest
from bath_saltford_fixture import configured_bath_saltford
from test_strategic_criteria_scenario import _compiled_inputs
from test_strategic_reference_application import _resolved_reference_inputs

from satn.agents import FakeAgentRuntime
from satn.evidence import mark_ncn_edges
from satn.pipeline import compile_strategic_reference_network
from satn.routing import RoadGraph
from satn.strategic_reference_application import (
    build_strategic_reference_application_plan,
)
from satn.strategic_reference_replay import (
    materialise_replay,
    served_obligations_frame,
    validate_fresh_replay,
)


def _replay_inputs(tmp_path):
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)

    _, source, compiled, _, _ = _compiled_inputs(tmp_path)
    plan = build_strategic_reference_application_plan(reference, preparation)
    graph = RoadGraph(mark_ncn_edges(source["network"], source["context"]))
    return plan, preparation, graph, compiled.strategic_spines, compiled.places


def test_replay_materialises_both_roles_and_serves_endpoints_once(tmp_path) -> None:
    plan, preparation, graph, strategic_spines, places = _replay_inputs(tmp_path)

    validated = validate_fresh_replay(plan, preparation)
    replay = materialise_replay(validated, strategic_spines, places, graph)
    obligations = served_obligations_frame(replay, places)

    assert len(replay.interurban_connections) == 1
    assert len(replay.destination_access_connections) == 1
    assert replay.served_endpoint_place_ids == ("bath-edge", "saltford")
    assert set(obligations["place_id"]) == {"bath-edge", "saltford"}
    assert set(obligations["network_role"]) == {
        "interurban-network-place-obligation"
    }
    assert replay.diagnostics["consumed_binding_count"] == len(plan.bindings)
    assert replay.diagnostics["publication_created"] is False
    assert replay.diagnostics["agent_runtime_invoked"] is False
    destination_binding_ids = set(
        replay.destination_access_connections["binding_id"]
    )
    assert not {
        item.binding_id for item in replay.served_network_place_obligations
    }.intersection(destination_binding_ids)


def test_replay_rejects_current_preparation_drift(tmp_path) -> None:
    plan, preparation, _, _, _ = _replay_inputs(tmp_path)
    drifted = deepcopy(preparation)
    object.__setattr__(drifted, "preparation_fingerprint", "0" * 64)

    with pytest.raises(ValueError, match="incomplete or stale"):
        validate_fresh_replay(plan, drifted)


@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_replay_rejects_missing_graph_edge_identity(tmp_path, direction) -> None:
    plan, preparation, graph, strategic_spines, places = _replay_inputs(tmp_path)
    validated = validate_fresh_replay(plan, preparation)
    binding = plan.bindings[0]
    edge_id = (
        binding.routing_edge_ids[0]
        if direction == "forward"
        else binding.reverse_routing_edge_ids[0]
    )
    edge = next(
        attrs
        for _, _, attrs in graph.graph.edges(data=True)
        if str(attrs["edge_id"]) == edge_id
    )
    edge["edge_id"] = f"drifted-{edge_id}"

    with pytest.raises(ValueError, match="unique exact graph edge chain"):
        materialise_replay(validated, strategic_spines, places, graph)


def test_replay_rejects_graph_geometry_drift(tmp_path) -> None:
    plan, preparation, graph, strategic_spines, places = _replay_inputs(tmp_path)
    validated = validate_fresh_replay(plan, preparation)
    binding = plan.bindings[0]
    edge = next(
        attrs
        for _, _, attrs in graph.graph.edges(data=True)
        if str(attrs["edge_id"]) == binding.routing_edge_ids[0]
    )
    edge["geometry"] = edge["geometry"].parallel_offset(0.0001)

    with pytest.raises(ValueError, match="graph geometry disagrees"):
        materialise_replay(validated, strategic_spines, places, graph)


def test_private_pipeline_recompiles_bath_without_spine_access_duplication(
    tmp_path,
) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)

    plan = build_strategic_reference_application_plan(reference, preparation)
    compiled = compile_strategic_reference_network(
        configured_bath_saltford(tmp_path),
        FakeAgentRuntime(),
        plan,
    )

    assert set(compiled.strategic_interurban_connections["from_network_place_id"]) == {
        "bath-edge"
    }
    assert set(compiled.strategic_interurban_connections["to_network_place_id"]) == {
        "saltford"
    }
    assert set(compiled.strategic_destination_access_connections[
        "strategic_destination_id"
    ]) == {"bath-spa-university"}
    assert not set(compiled.spine_access_connections["place_id"]).intersection(
        {"bath-edge", "saltford"}
    )
    obligations = compiled.access_obligations.set_index("place_id")
    assert obligations.loc["bath-edge", "network_role"] == (
        "interurban-network-place-obligation"
    )
    assert obligations.loc["saltford", "network_role"] == (
        "interurban-network-place-obligation"
    )
    assert compiled.reference_satn_publication is None
    assert compiled.strategic_reference_diagnostics[
        "destination_access_satisfied_community_obligation_count"
    ] == 0
    assert set(compiled.places["place_id"]) == {"bath-edge", "saltford"}
    assert compiled.connection_count == (
        len(compiled.spine_access_connections)
        + len(compiled.branch_meeting_connections)
    )
    assert compiled.strategic_reference_connection_count == 2
    # The synthetic fixture intentionally retains its unrelated school gap;
    # replay must neither hide it nor recreate endpoint community gaps.
    assert set(compiled.gaps["network_role"]) == {"school-access-gap"}
    assert not set(compiled.gaps["from_place"]).intersection(
        {"bath-edge", "saltford"}
    )
    assert compiled.criteria["connections"]["mandatory_checks"].value == "red"


def test_ordinary_compile_retains_empty_replay_outputs(tmp_path) -> None:
    _, _, _, strategic_spines, places = _replay_inputs(tmp_path)
    # `_replay_inputs` obtains these two frames from the ordinary compilation.
    assert not strategic_spines.empty
    assert set(places["place_id"]) == {"bath-edge", "saltford"}
    _, _, ordinary, _, _ = _compiled_inputs(tmp_path)
    assert ordinary.strategic_interurban_connections.empty
    assert ordinary.strategic_destination_access_connections.empty
    assert ordinary.strategic_reference_diagnostics == {}
    assert ordinary.connection_count == (
        len(ordinary.spine_access_connections)
        + len(ordinary.branch_meeting_connections)
    )
    assert ordinary.strategic_reference_connection_count == 0


def test_private_pipeline_rejects_a_stale_plan_before_replay(tmp_path) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)

    plan = build_strategic_reference_application_plan(reference, preparation)
    object.__setattr__(plan, "preparation_fingerprint", "0" * 64)

    with pytest.raises(ValueError, match="identity is stale"):
        compile_strategic_reference_network(
            configured_bath_saltford(tmp_path),
            FakeAgentRuntime(),
            plan,
        )
