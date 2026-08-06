from __future__ import annotations

import copy
from dataclasses import dataclass, replace

import numpy as np
import pytest
from geopandas.testing import assert_geodataframe_equal
from pydantic import BaseModel
from test_backbone_assembly import config, parallel_spine_source

from satn.agents import FakeAgentRuntime
from satn.backbone import BackboneAssembly
from satn.compiled_network_bundle import BundleCodecError
from satn.compiler import compile_network
from satn.cross_spine import CrossSpineAssembly
from satn.models import (
    AgentDecisionAction,
    AgentDecisionChoice,
    AgentDecisionRequest,
    AgentDecisionResponse,
    AgentRecord,
    TrafficLight,
)
from satn.routing_assembly_bundle import (
    RoutingAssemblyBundle,
    decode_routing_assembly_bundle,
    encode_routing_assembly_bundle,
)

AREA = "a" * 64
INPUT = "b" * 64
DEPENDENCY = "c" * 64
UPSTREAM = ("d" * 64, "e" * 64)


class _NestedDiagnosticModel(BaseModel):
    value: float


@dataclass(frozen=True)
class _NestedDiagnosticDataclass:
    value: float


def _assemblies_from_compiled(
    compiled: object,
) -> tuple[BackboneAssembly, CrossSpineAssembly]:
    empty_findings = compiled.gaps.iloc[0:0].copy()
    backbone = BackboneAssembly(
        connections=compiled.spine_access_connections,
        obligations=compiled.access_obligations,
        branches=compiled.spine_access_branches,
        meeting_connections=compiled.branch_meeting_connections,
        cross_spine_connectors=compiled.cross_spine_connectors,
        gaps=compiled.gaps,
        gateway_count=0,
        connected_gateway_count=0,
        agent_records=list(compiled.agent_records),
        compilation_diagnostics=compiled.compilation_diagnostics,
        cross_spine_assembly_diagnostics=compiled.compilation_diagnostics["cross_spine"],
    )
    cross_spine = CrossSpineAssembly(
        valid_connectors=compiled.cross_spine_connectors,
        route_refinement_findings=empty_findings,
        agent_records=tuple(compiled.agent_records),
        diagnostics=compiled.compilation_diagnostics["cross_spine"],
    )
    return backbone, cross_spine


def _bundle_from_compiled(compiled: object) -> RoutingAssemblyBundle:
    backbone, cross_spine = _assemblies_from_compiled(compiled)
    return RoutingAssemblyBundle.from_assemblies(backbone, cross_spine)


def _reviewed_pair() -> tuple[AgentRecord, AgentDecisionResponse]:
    action = AgentDecisionAction(kind="reject-candidate")
    choice = AgentDecisionChoice(
        choice_id="2",
        label="Reject",
        compiler_action=action,
        expected_consequence="The compiler evaluates the next candidate.",
        mandatory_constraints=("Use only the offered choices.",),
    )
    request = AgentDecisionRequest(
        request_id="request-1",
        dependency_fingerprint="f" * 64,
        compilation_scope="routing",
        affected_identifiers=("connection-1",),
        criterion="continuity",
        question="Choose the route.",
        status=TrafficLight.AMBER,
        governed_evidence_references=(),
        deterministic_findings=(),
        choices=(choice,),
    )
    record = AgentRecord(
        connection_id="connection-1",
        governing_criterion="continuity",
        governing_status=TrafficLight.AMBER,
        review_policy=(TrafficLight.AMBER,),
        review_required=True,
        runtime="caller",
        model="",
        decision="reject",
        decision_request=request,
        selected_choice_id="2",
        mapped_action=action,
        responder_mode="caller",
        choice_validation="accepted",
        affected_feature_identifiers=("connection-1",),
    )
    return record, AgentDecisionResponse(
        request_id="request-1",
        dependency_fingerprint="f" * 64,
        choice_id="2",
    )


def _encode(bundle: RoutingAssemblyBundle) -> dict[str, object]:
    return encode_routing_assembly_bundle(
        bundle,
        area_identity=AREA,
        input_identity=INPUT,
        dependency_identity=DEPENDENCY,
        upstream_artifact_ids=UPSTREAM,
    )


def test_synthesised_routing_result_round_trips_as_typed_state() -> None:
    compiled = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    bundle = _bundle_from_compiled(compiled)

    decoded = decode_routing_assembly_bundle(_encode(bundle), RoutingAssemblyBundle)

    assert isinstance(decoded, RoutingAssemblyBundle)
    for name in (
        "connections",
        "obligations",
        "branches",
        "meeting_connections",
        "cross_spine_connectors",
        "gaps",
        "valid_cross_spine_connectors",
        "route_refinement_findings",
    ):
        stable_key = {
            "connections": "access_connection_id",
            "obligations": "obligation_id",
            "branches": "branch_id",
            "meeting_connections": "meeting_connection_id",
            "cross_spine_connectors": "cross_spine_connector_id",
            "gaps": "connection_id",
            "valid_cross_spine_connectors": "cross_spine_connector_id",
            "route_refinement_findings": "connection_id",
        }[name]
        assert_geodataframe_equal(
            getattr(decoded, name),
            getattr(bundle, name).sort_values(stable_key, kind="stable").reset_index(drop=True),
        )
    assert decoded.gateway_count == bundle.gateway_count
    assert decoded.connected_gateway_count == bundle.connected_gateway_count
    assert decoded.agent_records == bundle.agent_records
    assert decoded.accepted_responses == bundle.accepted_responses
    assert decoded.cross_spine_diagnostics == bundle.cross_spine_diagnostics


def test_frame_order_does_not_change_bundle_identity() -> None:
    compiled = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    bundle = _bundle_from_compiled(compiled)
    shuffled = replace(bundle, connections=bundle.connections.iloc[::-1].copy())

    assert _encode(bundle) == _encode(shuffled)


def test_from_assemblies_takes_a_deep_snapshot() -> None:
    compiled = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    backbone, cross_spine = _assemblies_from_compiled(compiled)
    bundle = RoutingAssemblyBundle.from_assemblies(backbone, cross_spine)

    backbone.connections.attrs["mutated"] = True
    backbone.compilation_diagnostics["mutated"] = {"nested": True}
    if backbone.agent_records:
        backbone.agent_records[0].attempts.append({"mutated": True})

    assert "mutated" not in bundle.connections.attrs
    assert "mutated" not in bundle.compilation_diagnostics
    if bundle.agent_records:
        assert all(attempt != {"mutated": True} for attempt in bundle.agent_records[0].attempts)


def test_accepted_decisions_round_trip_and_mismatch_is_rejected() -> None:
    compiled = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    backbone, cross_spine = _assemblies_from_compiled(compiled)
    record, response = _reviewed_pair()
    bundle = replace(
        RoutingAssemblyBundle.from_assemblies(backbone, cross_spine),
        agent_records=(record,),
        accepted_responses=(response,),
    )

    decoded = decode_routing_assembly_bundle(_encode(bundle), RoutingAssemblyBundle)

    assert decoded.accepted_responses == (response,)
    with pytest.raises(ValueError, match="match exactly"):
        replace(bundle, accepted_responses=())
    with pytest.raises(ValueError, match="does not match"):
        replace(
            bundle,
            accepted_responses=(
                response.model_copy(update={"choice_id": "terminate"}),
            ),
        )


def test_nonfinite_diagnostics_are_rejected_at_construction_and_encode() -> None:
    compiled = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    bundle = _bundle_from_compiled(compiled)
    with pytest.raises(ValueError, match="non-finite"):
        replace(bundle, cross_spine_diagnostics={"nested": [np.float32(float("nan"))]})
    with pytest.raises(ValueError, match="non-finite"):
        replace(
            bundle,
            cross_spine_diagnostics={"nested": _NestedDiagnosticModel(value=float("nan"))},
        )
    with pytest.raises(ValueError, match="non-finite"):
        replace(
            bundle,
            cross_spine_diagnostics={
                "nested": _NestedDiagnosticDataclass(value=float("inf"))
            },
        )

    bundle.compilation_diagnostics["late"] = {"infinity": float("inf")}
    with pytest.raises(ValueError, match="non-finite"):
        _encode(bundle)


def test_corruption_and_forged_nested_contract_are_rejected() -> None:
    compiled = compile_network(config(), parallel_spine_source(), FakeAgentRuntime())
    payload = _encode(_bundle_from_compiled(compiled))

    corrupted = copy.deepcopy(payload)
    corrupted["payload"]["fields"]["gateway_count"]["payload"] = {  # type: ignore[index]
        "type": "string",
        "value": "forged",
    }
    with pytest.raises(BundleCodecError):
        decode_routing_assembly_bundle(corrupted, RoutingAssemblyBundle)

    forged = copy.deepcopy(payload)
    forged["payload"]["fields"]["agent_records"]["payload"]["items"][0]["model"] = (
        "NotAgentRecord"
    )  # type: ignore[index]
    with pytest.raises(BundleCodecError):
        decode_routing_assembly_bundle(forged, RoutingAssemblyBundle)
