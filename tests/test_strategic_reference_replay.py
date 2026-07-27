"""Fail-closed strategic Reference replay tests."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import geopandas as gpd
import pytest
from bath_saltford_fixture import configured_bath_saltford
from test_strategic_criteria_scenario import _compiled_inputs
from test_strategic_reference_application import _resolved_reference_inputs

from satn.agents import FakeAgentRuntime
from satn.alignment_selection import CanonicalLineString
from satn.content_identity import canonical_json, content_fingerprint
from satn.evidence import mark_ncn_edges
from satn.pipeline import compile_strategic_reference_network
from satn.routing import RoadGraph
from satn.strategic_reference_application import (
    build_strategic_reference_application_plan,
)
from satn.strategic_reference_replay import (
    LINEAGE_COLUMNS,
    _aggregate_served_obligations,
    materialise_replay,
    served_obligations_frame,
    validate_fresh_replay,
)


def _projected_fingerprint(geometry, crs) -> str:
    projected = gpd.GeoSeries([geometry], crs=crs).to_crs(27700).iloc[0]
    return CanonicalLineString(
        coordinates=tuple((float(x), float(y)) for x, y in projected.coords)
    ).fingerprint


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
        binding_id
        for item in replay.served_network_place_obligations
        for binding_id in item.binding_ids
    }.intersection(destination_binding_ids)
    for frame in (
        replay.interurban_connections,
        replay.destination_access_connections,
    ):
        assert set(LINEAGE_COLUMNS).issubset(frame.columns)
        assert frame[LINEAGE_COLUMNS].notna().all().all()
        assert set(frame["plan_fingerprint"]) == {plan.plan_fingerprint}
        assert set(frame["area_fingerprint"]) == {plan.area_fingerprint}
    for provenance_json in obligations["provenance"]:
        provenance = json.loads(provenance_json)
        assert provenance["binding_lineages"]
        assert all(
            set(LINEAGE_COLUMNS).issubset(lineage)
            for lineage in provenance["binding_lineages"]
        )
    replay_spines = replay.effective_strategic_spines[
        replay.effective_strategic_spines["replay_binding_ids"].notna()
    ]
    assert not replay_spines.empty
    for provenance_json in replay_spines["provenance"]:
        provenance = json.loads(provenance_json)
        assert all(
            set(LINEAGE_COLUMNS).issubset(lineage)
            for lineage in provenance["binding_lineages"]
        )


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


def test_replay_rejects_reverse_graph_geometry_drift(tmp_path) -> None:
    plan, preparation, graph, strategic_spines, places = _replay_inputs(tmp_path)
    validated = validate_fresh_replay(plan, preparation)
    binding = plan.bindings[0]
    edge = next(
        attrs
        for _, _, attrs in graph.graph.edges(data=True)
        if str(attrs["edge_id"]) == binding.reverse_routing_edge_ids[0]
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
    interurban_binding = next(
        binding
        for binding in plan.bindings
        if binding.unit_role.value == "interurban-spine"
    )
    interurban_unit = next(
        unit
        for unit in preparation.units
        if unit.unit_id == interurban_binding.unit_id
    )
    rejected_fingerprints = {
        record.candidate.geometry_fingerprint
        for record in interurban_unit.candidate_records
        if record.candidate.candidate_id
        != interurban_binding.selected_candidate_id
    }
    effective_fingerprints = {
        _projected_fingerprint(geometry, compiled.strategic_spines.crs)
        for geometry in compiled.strategic_spines.geometry
    }
    assert interurban_binding.geometry_fingerprint in effective_fingerprints
    assert rejected_fingerprints.isdisjoint(effective_fingerprints)
    destination_fingerprints = {
        _projected_fingerprint(
            geometry,
            compiled.strategic_destination_access_connections.crs,
        )
        for geometry in compiled.strategic_destination_access_connections.geometry
    }
    assert destination_fingerprints.isdisjoint(effective_fingerprints)


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


def test_private_pipeline_rejects_a_foreign_area_before_replay(tmp_path) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    plan = build_strategic_reference_application_plan(reference, preparation)
    object.__setattr__(plan, "area_fingerprint", "0" * 64)

    with pytest.raises(ValueError, match="Area identity"):
        compile_strategic_reference_network(
            configured_bath_saltford(tmp_path),
            FakeAgentRuntime(),
            plan,
        )


def test_shared_interurban_hub_has_one_obligation_with_two_memberships() -> None:
    lineage_ab = canonical_json({"unit_id": "a-hub"})
    lineage_bc = canonical_json({"unit_id": "hub-c"})
    obligations = _aggregate_served_obligations(
        {
            "a": [
                {
                    "binding_id": "binding-a-hub",
                    "strategic_connection_id": "connection-a-hub",
                    "physical_alignment_id": "physical-a-hub",
                    "binding_lineage": lineage_ab,
                }
            ],
            "hub": [
                {
                    "binding_id": "binding-a-hub",
                    "strategic_connection_id": "connection-a-hub",
                    "physical_alignment_id": "physical-a-hub",
                    "binding_lineage": lineage_ab,
                },
                {
                    "binding_id": "binding-hub-c",
                    "strategic_connection_id": "connection-hub-c",
                    "physical_alignment_id": "physical-hub-c",
                    "binding_lineage": lineage_bc,
                },
            ],
            "c": [
                {
                    "binding_id": "binding-hub-c",
                    "strategic_connection_id": "connection-hub-c",
                    "physical_alignment_id": "physical-hub-c",
                    "binding_lineage": lineage_bc,
                }
            ],
        }
    )

    assert tuple(item.network_place_id for item in obligations) == ("a", "c", "hub")
    hub = next(item for item in obligations if item.network_place_id == "hub")
    assert hub.binding_ids == ("binding-a-hub", "binding-hub-c")
    assert hub.strategic_connection_ids == (
        "connection-a-hub",
        "connection-hub-c",
    )


def test_shared_content_identity_is_byte_compatible_with_plan_contract(
    tmp_path,
) -> None:
    payload = {"z": [3, 2, 1], "a": {"é": False}}
    expected = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    assert canonical_json(payload) == expected
    assert content_fingerprint(payload) == hashlib.sha256(
        expected.encode("utf-8")
    ).hexdigest()

    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    plan = build_strategic_reference_application_plan(reference, preparation)
    plan_payload = plan.model_dump(mode="json", exclude={"plan_fingerprint"})
    assert plan.plan_fingerprint == hashlib.sha256(
        json.dumps(
            plan_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
