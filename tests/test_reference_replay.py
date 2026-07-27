"""Public compiler-only replay seam tests for a governed Reference SATN."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point
from test_alignment_selection import profile as scenario_profile
from test_backbone_assembly import parallel_spine_source
from test_reference_application import adopted_reference, reference_for_area

from satn.agents import AgentRole, CompilationGate, FakeAgentRuntime
from satn.alignment_selection import CanonicalLineString
from satn.backbone import assemble_backbone_outward
from satn.compiler import compile_network
from satn.models import CouncilConfig, TrafficLight
from satn.reference_application import (
    ReferenceApplicationCandidateBinding,
    ReferenceApplicationPlan,
    ValidatedReferenceApplication,
    build_validated_reference_application,
)
from satn.routing import RoadGraph

PROJECT = Path(__file__).parents[1]
_OMITTED = object()


def _graph(crs: str = "EPSG:27700") -> RoadGraph:
    network = gpd.GeoDataFrame(
            [
                {
                    "u": "spine",
                    "v": "parent",
                    "osmid": "spine-parent",
                    "length": 100.0,
                    "highway": "residential",
                    "ref": None,
                    "oneway": False,
                    "geometry": LineString([(400000, 170000), (400100, 170000)]),
                },
                {
                    "u": "parent",
                    "v": "child",
                    "osmid": "parent-child",
                    "length": 100.0,
                    "highway": "cycleway",
                    "ref": None,
                    "oneway": False,
                    "satn_ncn": True,
                    "geometry": LineString([(400100, 170000), (400200, 170000)]),
                },
                {
                    "u": "parent",
                    "v": "spine",
                    "osmid": "parent-spine",
                    "length": 100.0,
                    "highway": "residential",
                    "ref": None,
                    "oneway": False,
                    "geometry": LineString([(400100, 170000), (400000, 170000)]),
                },
                {
                    "u": "child",
                    "v": "parent",
                    "osmid": "child-parent",
                    "length": 100.0,
                    "highway": "cycleway",
                    "ref": None,
                    "oneway": False,
                    "satn_ncn": True,
                    "geometry": LineString([(400200, 170000), (400100, 170000)]),
                },
            ],
            geometry="geometry",
            crs="EPSG:27700",
        )
    return RoadGraph(network if crs == "EPSG:27700" else network.to_crs(crs))


def _communities(crs: str = "EPSG:27700") -> gpd.GeoDataFrame:
    communities = gpd.GeoDataFrame(
        [
            {
                "place_id": "parent",
                "name": "Parent",
                "kind": "community",
                "geometry": Point(400100, 170000),
            },
            {
                "place_id": "child",
                "name": "Child",
                "kind": "community",
                "geometry": Point(400200, 170000),
            },
        ],
        geometry="geometry",
        crs="EPSG:27700",
    )
    return communities if crs == "EPSG:27700" else communities.to_crs(crs)


def _spines(crs: str = "EPSG:27700") -> gpd.GeoDataFrame:
    spines = gpd.GeoDataFrame(
        [
            {
                "spine_id": "spine-1",
                "name": "Spine",
                "spine_kind": "a-road",
                "evidence_id": "spine-evidence",
                "source_id": "spine-source",
                "geometry": LineString([(400000, 170000), (400100, 170000)]),
            }
        ],
        geometry="geometry",
        crs="EPSG:27700",
    )
    return spines if crs == "EPSG:27700" else spines.to_crs(crs)


def _empty() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"place_id": []}, geometry=[], crs="EPSG:27700")


def _configured_area() -> CouncilConfig:
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    config.compilation.network_selection = scenario_profile(review_when=[])
    return config


def _plan(graph: RoadGraph, *, route_role: str = "ncn-informed") -> ReferenceApplicationPlan:
    option = graph.option("child", "parent", route_role)
    assert option is not None
    canonical_geometry = gpd.GeoSeries([option.geometry], crs=graph.crs).to_crs(27700).iloc[0]
    geometry_fingerprint = CanonicalLineString(
        coordinates=tuple((float(x), float(y)) for x, y in canonical_geometry.coords)
    ).fingerprint
    binding = ReferenceApplicationCandidateBinding(
        logical_connection_id="connection-00000000000000000000",
        candidate_set_id="candidate-set-00000000000000000000",
        candidate_set_fingerprint="a" * 64,
        resolution_fingerprint="b" * 64,
        selected_candidate_id="candidate-00000000000000000000",
        source_access_connection_id="old-parent-child-access-id",
        community_place_id="child",
        parent_place_id="parent",
        root_spine_id="spine-1",
        routing_start_node_id="child",
        routing_end_node_id="parent",
        route_role=route_role,
        routing_edge_ids=tuple(option.edge_ids),
        reverse_routing_edge_ids=tuple(option.reverse_edge_ids),
        geometry_fingerprint=geometry_fingerprint,
        candidate_input_fingerprint="c" * 64,
        candidate_evidence_fingerprints=("d" * 64,),
        prepared_candidate_record_fingerprint="e" * 64,
        prepared_connection_fingerprint="f" * 64,
    )
    config = _configured_area()
    area_sha256 = hashlib.sha256(config.config_path.read_bytes()).hexdigest()
    profile_fingerprint = config.compilation.network_selection_fingerprint
    assert profile_fingerprint is not None
    return ReferenceApplicationPlan(
        reference_selection_fingerprint="1" * 64,
        reference_decision_fingerprint="2" * 64,
        preparation_fingerprint="3" * 64,
        preparation_evidence_fingerprints=("4" * 64,),
        scenario_fingerprint="5" * 64,
        scenario_area_fingerprint=area_sha256,
        profile_fingerprint=profile_fingerprint,
        evidence_snapshot_fingerprint="8" * 64,
        selection_run_fingerprint="9" * 64,
        candidate_bindings=(binding,),
    )


def _context(plan: ReferenceApplicationPlan) -> ValidatedReferenceApplication:
    return ValidatedReferenceApplication(
        plan=plan,
        area_definition_sha256=plan.scenario_area_fingerprint,
        profile_fingerprint=plan.profile_fingerprint,
        baseline_preparation_fingerprint=plan.preparation_fingerprint,
        baseline_evidence_fingerprints=plan.preparation_evidence_fingerprints,
        governed_input_fingerprint="a" * 64,
    )


def _assembly(
    context: ValidatedReferenceApplication | ReferenceApplicationPlan | None | object = _OMITTED,
    *,
    topography: bool = False,
    reject_reference: bool = False,
    crs: str = "EPSG:27700",
):
    config = _configured_area()
    runtime = FakeAgentRuntime()
    if reject_reference:
        config.compilation.agent.response_mode = "direct-runtime"
        config.compilation.agent.review_statuses = (TrafficLight.GREEN,)
        runtime = FakeAgentRuntime(
            {
                AgentRole.DECISION: [
                    {"request_id": "$request", "choice_id": "1"},
                    {"request_id": "$request", "choice_id": "3"},
                ]
            }
        )
    kwargs = (
        {}
        if context is _OMITTED
        else {
            "validated_reference_application": context,
            "area_config": config,
            "governed_input_fingerprint": "a" * 64,
        }
    )
    return assemble_backbone_outward(
        _communities(crs),
        _empty().to_crs(crs) if crs != "EPSG:27700" else _empty(),
        _empty().to_crs(crs) if crs != "EPSG:27700" else _empty(),
        _spines(crs),
        _graph(crs),
        CompilationGate(runtime, config.compilation.agent, ""),
        max_connection_km=10.0,
        elevation_evidence=_empty() if topography else None,
        topography_config=config.compilation.topography if topography else None,
        **kwargs,
    )


def test_replay_applies_exact_route_only_after_its_parent_frontier_exists() -> None:
    graph = _graph()
    plan = _plan(graph)

    assembly = _assembly(_context(plan))

    child = assembly.connections.set_index("place_id").loc["child"]
    child_obligation = assembly.obligations.set_index("place_id").loc["child"]
    child_branch = assembly.branches.set_index("branch_id").loc[child["branch_id"]]
    assert child["parent_place_id"] == "parent"
    assert json.loads(child["provenance"])["reference_application"] == {
        "binding_fingerprint": plan.candidate_bindings[0].binding_fingerprint,
        "deterministic_recommended_role": "not-evaluated",
        "deterministic_topography_status": "not-evaluated",
        "logical_connection_id": plan.candidate_bindings[0].logical_connection_id,
        "plan_fingerprint": plan.plan_fingerprint,
        "selected_candidate_id": plan.candidate_bindings[0].selected_candidate_id,
        "selected_route_role": "ncn-informed",
    }
    assert assembly.compilation_diagnostics["reference_application"] == {
        "contract": "satn-reference-application-plan/v1",
        "plan_fingerprint": plan.plan_fingerprint,
        "status": "applied",
        "expected_count": 1,
        "applied_count": 1,
        "logical_connection_ids": [plan.candidate_bindings[0].logical_connection_id],
        "source_to_regenerated_access_connection_ids": {
            "old-parent-child-access-id": child["access_connection_id"]
        },
        "selected_candidate_ids": {
            plan.candidate_bindings[0].logical_connection_id: (
                plan.candidate_bindings[0].selected_candidate_id
            )
        },
        "binding_fingerprints": {
            plan.candidate_bindings[0].logical_connection_id: (
                plan.candidate_bindings[0].binding_fingerprint
            )
        },
        "application_stage": "compiler-only",
        "publication_created": False,
        "publication_authority": "none",
    }
    assert child_obligation["access_connection_id"] == child["access_connection_id"]
    assert child_obligation["branch_id"] == child["branch_id"]
    assert child["access_connection_id"] in json.loads(child_branch["connection_ids"])
    authoritative_lineage = json.dumps(
        {
            "connections": assembly.connections.drop(columns="geometry").to_dict("records"),
            "obligations": assembly.obligations.drop(columns="geometry").to_dict("records"),
            "branches": assembly.branches.drop(columns="geometry").to_dict("records"),
        },
        sort_keys=True,
        default=str,
    )
    assert "old-parent-child-access-id" not in authoritative_lineage


def test_omitted_plan_and_explicit_none_preserve_legacy_assembly() -> None:
    omitted = _assembly()
    explicit = _assembly(None)

    assert omitted.connections.equals(explicit.connections)
    assert omitted.compilation_diagnostics == explicit.compilation_diagnostics
    assert "reference_application" not in omitted.compilation_diagnostics


def test_compile_network_omitted_plan_and_explicit_none_are_equivalent() -> None:
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")

    omitted = compile_network(config, parallel_spine_source(), FakeAgentRuntime())
    explicit = compile_network(
        config,
        parallel_spine_source(),
        FakeAgentRuntime(),
        validated_reference_application=None,
    )

    assert omitted.spine_access_connections.equals(explicit.spine_access_connections)
    assert omitted.access_obligations.equals(explicit.access_obligations)
    assert omitted.spine_access_branches.equals(explicit.spine_access_branches)
    assert omitted.compilation_diagnostics == explicit.compilation_diagnostics
    assert "reference_application" not in omitted.compilation_diagnostics


def test_raw_reference_plan_has_no_public_compile_or_assembly_authority() -> None:
    raw_plan = _plan(_graph())

    with pytest.raises(TypeError, match="ValidatedReferenceApplication"):
        _assembly(raw_plan)
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    with pytest.raises(TypeError, match="ValidatedReferenceApplication"):
        compile_network(
            config,
            parallel_spine_source(),
            FakeAgentRuntime(),
            governed_input_fingerprint="a" * 64,
            validated_reference_application=raw_plan,  # type: ignore[arg-type]
        )


def test_exact_prepared_record_builds_context_and_replays_through_compile_network() -> None:
    config = _configured_area()
    source = parallel_spine_source()
    baseline = compile_network(
        config,
        source,
        FakeAgentRuntime(),
        governed_input_fingerprint="a" * 64,
    )
    baseline_preparation = baseline.spine_access_candidate_preparation
    assert baseline_preparation is not None
    item = baseline_preparation.prepared_spine_access_connections[0]
    reference, exact_preparation = adopted_reference(item)
    reference = reference_for_area(reference, config)
    context = build_validated_reference_application(
        reference,
        exact_preparation,
        exact_preparation,
        config,
        "a" * 64,
    )

    replayed = compile_network(
        config,
        parallel_spine_source(),
        FakeAgentRuntime(),
        governed_input_fingerprint="a" * 64,
        validated_reference_application=context,
    )

    diagnostics = replayed.compilation_diagnostics["reference_application"]
    assert diagnostics["status"] == "applied"
    assert diagnostics["plan_fingerprint"] == context.plan.plan_fingerprint
    assert diagnostics["applied_count"] == 1


def _rebuilt_plan(
    plan: ReferenceApplicationPlan,
    **binding_updates: object,
) -> ReferenceApplicationPlan:
    binding_payload = plan.candidate_bindings[0].model_dump(mode="python")
    binding_payload.update(binding_updates)
    binding_payload["binding_fingerprint"] = ""
    binding = ReferenceApplicationCandidateBinding.model_validate(binding_payload)
    plan_payload = plan.model_dump(mode="python")
    plan_payload["candidate_bindings"] = (binding,)
    plan_payload["plan_fingerprint"] = ""
    return ReferenceApplicationPlan.model_validate(plan_payload)


def test_replay_fails_closed_for_a_route_edge_mismatch() -> None:
    plan = _rebuilt_plan(_plan(_graph()), routing_edge_ids=("drifted-edge",))

    with pytest.raises(ValueError, match="forward route edges"):
        _assembly(_context(plan))


def test_replay_fails_closed_for_a_route_geometry_mismatch() -> None:
    plan = _rebuilt_plan(_plan(_graph()), geometry_fingerprint="0" * 64)

    with pytest.raises(ValueError, match="geometry fingerprint"):
        _assembly(_context(plan))


def test_replay_geometry_verification_uses_preparation_canonical_crs() -> None:
    graph = _graph("EPSG:4326")

    assembly = _assembly(_context(_plan(graph)), crs="EPSG:4326")

    assert assembly.connections.set_index("place_id").loc["child", "status"] == "validated"


def test_replay_rejects_duplicate_child_bindings_before_routing() -> None:
    plan = _plan(_graph())
    duplicate_payload = plan.candidate_bindings[0].model_dump(mode="python")
    duplicate_payload.update(
        {
            "logical_connection_id": "connection-11111111111111111111",
            "candidate_set_id": "candidate-set-11111111111111111111",
            "selected_candidate_id": "candidate-11111111111111111111",
            "binding_fingerprint": "",
        }
    )
    duplicate = ReferenceApplicationCandidateBinding.model_validate(duplicate_payload)
    plan_payload = plan.model_dump(mode="python")
    plan_payload["candidate_bindings"] = (*plan.candidate_bindings, duplicate)
    plan_payload["plan_fingerprint"] = ""
    duplicate_plan = ReferenceApplicationPlan.model_validate(plan_payload)

    with pytest.raises(ValueError, match="duplicate Community"):
        _assembly(_context(duplicate_plan))


def test_reference_route_remains_selected_despite_other_topography_role() -> None:
    plan = _plan(_graph(), route_role="direct")

    assembly = _assembly(_context(plan), topography=True)

    child = assembly.connections.set_index("place_id").loc["child"]
    assert child["topography_comparison_status"] == "evidence-unavailable"
    assert child["topography_selected_role"] == "direct"
    assert "recommended ncn-informed" in child["selection_reason"]
    assert "governed Reference candidate" in child["topography_comparison_rationale"]
    options = json.loads(child["alignment_options"])
    assert [item["role"] for item in options if item["selected"]] == ["direct"]
    assert next(item for item in options if item["role"] == "direct")[
        "reference_selection_scope"
    ] == "within-deterministic-comparison"


@pytest.mark.parametrize("route_role", ("b-road-corridor", "other-routable-corridor"))
def test_reference_only_role_is_selected_outside_deterministic_topography(
    route_role: str,
) -> None:
    assembly = _assembly(
        _context(_plan(_graph(), route_role=route_role)),
        topography=True,
    )

    child = assembly.connections.set_index("place_id").loc["child"]
    options = json.loads(child["alignment_options"])
    selected = [item for item in options if item["selected"]]
    assert [item["role"] for item in selected] == [route_role]
    assert selected[0]["reference_selection_scope"] == "outside-deterministic-comparison"
    assert selected[0]["topography"] is None
    assert child["topography_comparison_status"] == "evidence-unavailable"


def test_replay_fails_closed_when_a_planned_parent_cannot_become_a_frontier() -> None:
    plan = _rebuilt_plan(_plan(_graph()), parent_place_id="child")

    with pytest.raises(ValueError, match="unconsumed"):
        _assembly(_context(plan))


def test_replay_does_not_fall_back_after_the_gate_rejects_a_planned_route() -> None:
    with pytest.raises(ValueError, match="gate rejected planned"):
        _assembly(_context(_plan(_graph())), reject_reference=True)
