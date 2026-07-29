"""Public compiler-only replay seam tests for a governed Reference SATN."""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from pydantic import BaseModel
from shapely.geometry import LineString, Point, Polygon
from test_alignment_selection import (
    accepted_envelope,
    adopt_reference_satn,
    reference_decision,
)
from test_alignment_selection import (
    profile as scenario_profile,
)
from test_backbone_assembly import parallel_spine_source
from test_prepared_scenario_compilation import request as scenario_request
from test_reference_application import reference_for_area

from satn.agents import (
    AgentRole,
    CompilationGate,
    FakeAgentRuntime,
    RuntimeReply,
)
from satn.alignment_selection import (
    CanonicalLineString,
    DecisionProcessMode,
    ScenarioDecisionRecord,
    education_option_id_for_candidate,
)
from satn.backbone import (
    _assemble_backbone_outward,
    _validated_reference_option,
    assemble_backbone_outward,
)
from satn.compilation_dependencies import compilation_dependency_manifest
from satn.compiler import compile_network
from satn.education_access import (
    ConnectorContinuity,
    EvidenceAvailability,
    EvidenceFactor,
    IndependentTravelEvidence,
    MeasuredDistance,
    RouteObservationKind,
    RouteQualityEvidence,
    SchoolAccessEvidence,
)
from satn.models import (
    AccessPointStatus,
    AgentDecisionLedger,
    CouncilConfig,
    TrafficLight,
)
from satn.network_selection import (
    PopulationReachEvidenceConfig,
    SchoolRegisterEvidenceConfig,
)
from satn.pipeline import (
    compilation_governed_input_fingerprint,
    compile_reference_network,
)
from satn.psa_criteria_assembly import (
    CriteriaAssemblyInput,
    assemble_prepared_candidate_criteria,
)
from satn.psa_evidence_loaders import (
    load_education_access_evidence,
    load_population_reach_evidence,
)
from satn.reference_application import (
    ReferenceApplicationCandidateBinding,
    ReferenceApplicationPlan,
)
from satn.routing import RoadGraph
from satn.scenario_compilation import compile_prepared_scenario
from satn.spine_access_candidate_preparation import SpineAccessCandidatePreparationResult

PROJECT = Path(__file__).parents[1]
_OMITTED = object()


class CountingAgentRuntime(FakeAgentRuntime):
    """Expose how many final-compilation decisions reached the runtime."""

    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    def run(
        self,
        role: AgentRole,
        payload: BaseModel,
        output_type: type[BaseModel],
    ) -> RuntimeReply:
        self.call_count += 1
        return super().run(role, payload, output_type)


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


def _configured_area(tmp_path: Path | None = None) -> CouncilConfig:
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    config.compilation.network_selection = scenario_profile(review_when=[])
    if tmp_path is not None:
        area = tmp_path / "area.yaml"
        area.write_bytes(config.config_path.read_bytes())
        config.config_path = area
        config.source.snapshot_dir = tmp_path / "snapshots"
        manifest = config.source.snapshot_dir / config.source.snapshot_id / "snapshot.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('{"fixture":"current"}', encoding="utf-8")
        geometry = {
            "type": "FeatureCollection",
            "crs": "EPSG:4326",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"OA21CD": "E00000001"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [-0.1, -0.1],
                                [0.2, -0.1],
                                [0.2, 0.1],
                                [-0.1, 0.1],
                                [-0.1, -0.1],
                            ]
                        ],
                    },
                }
            ],
        }
        centroids = {
            "type": "FeatureCollection",
            "crs": "EPSG:4326",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"OA21CD": "E00000001"},
                    "geometry": {"type": "Point", "coordinates": [0.04, 0.0]},
                }
            ],
        }
        counts = {"records": [{"OA21CD": "E00000001", "usual_residents": 100}]}
        artifacts = []
        for name, payload in (
            ("output-area.geojson", geometry),
            ("centroids.geojson", centroids),
            ("counts.json", counts),
        ):
            path = tmp_path / name
            content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            path.write_bytes(content)
            artifacts.append(
                {
                    "source_id": name,
                    "path": path,
                    "release": "fixture-release",
                    "effective_date": "2021-03-21",
                    "licence": "fixture-licence",
                    "content_sha256": hashlib.sha256(content).hexdigest(),
                    "redistribution": "public",
                }
            )
        config.source.population_reach_evidence = PopulationReachEvidenceConfig(
            output_area_geometry=artifacts[0],
            population_weighted_centroids=artifacts[1],
            usual_resident_counts=artifacts[2],
        )
        school_register = {
            "schema": "satn-school-register/v1",
            "register": {
                "source_id": "school-register",
                "source_name": "Current fixture register",
                "authority_id": "fixture-authority",
                "as_of": "2026-07-27",
                "governed": True,
                "current": True,
                "status": "current",
            },
            "schools": [
                {
                    "school_id": "secondary-school",
                    "name": "Secondary School",
                    "phase": "secondary",
                    "record_status": "current",
                }
            ],
        }
        school_path = tmp_path / "schools.json"
        school_content = json.dumps(
            school_register,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        school_path.write_bytes(school_content)
        config.source.school_register_evidence = SchoolRegisterEvidenceConfig(
            school_register={
                "source_id": "school-register",
                "path": school_path,
                "release": "fixture-release",
                "effective_date": "2026-07-27",
                "licence": "fixture-licence",
                "content_sha256": hashlib.sha256(school_content).hexdigest(),
                "redistribution": "public",
            }
        )
        config.source.network_selection_as_at = date(2026, 7, 27)
        config.source.network_selection_school_register_max_age_days = 365
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


def _reference_from_current_preparation(
    config: CouncilConfig,
    preparation: SpineAccessCandidatePreparationResult,
):
    population = load_population_reach_evidence(
        config.source.population_reach_evidence,
        base_directory=config.config_path.parent,
        pwc_outside_tolerance_m=0,
    )
    education = load_education_access_evidence(
        config.source.school_register_evidence,
        base_directory=config.config_path.parent,
        as_at=config.source.network_selection_as_at,
        school_register_max_age_days=(
            config.source.network_selection_school_register_max_age_days
        ),
    )
    assert population is not None and education is not None
    def available(label: str) -> EvidenceFactor:
        return EvidenceFactor(
            availability=EvidenceAvailability.AVAILABLE,
            evidence_ids=(label,),
        )

    independent_travel = IndependentTravelEvidence(
        gradient=available("fixture-gradient"),
        road_class=available("fixture-road-class"),
        speed=available("fixture-speed"),
        crossing=available("fixture-crossing"),
        separation=available("fixture-separation"),
        lighting=available("fixture-lighting"),
        severance=available("fixture-severance"),
        audit=available("fixture-independent-travel-audit"),
    )
    option_evidence = {
        item.access_connection_id: tuple(
            SchoolAccessEvidence(
                option_id=education_option_id_for_candidate(
                    candidate,
                    item.candidate_set,
                ),
                school_id="secondary-school",
                connector_distance=MeasuredDistance(distance_m=120),
                connector_continuity=ConnectorContinuity.CONTINUOUS,
                access_point_status=AccessPointStatus.MAPPED,
                destination_distance=MeasuredDistance(distance_m=900),
                access_evidence_ids=("fixture-school-access",),
                support_evidence_ids=("fixture-school-support",),
                route_quality_evidence=(
                    RouteQualityEvidence(
                        evidence_id="fixture-route-observation",
                        observation=RouteObservationKind.CROSSING_RECORDED,
                    ),
                ),
                independent_travel_evidence=independent_travel,
            )
            for candidate in item.candidate_set.admitted_candidates
        )
        for item in preparation.prepared_spine_access_connections
    }
    criteria = assemble_prepared_candidate_criteria(
        CriteriaAssemblyInput(
            preparation=preparation,
            population_evidence=population,
            education_evidence=education,
            area_definition=gpd.GeoDataFrame(
                {
                    "geometry": [
                        Polygon(
                            [
                                (-0.1, -0.1),
                                (0.2, -0.1),
                                (0.2, 0.1),
                                (-0.1, 0.1),
                            ]
                        )
                    ]
                },
                geometry="geometry",
                crs="EPSG:4326",
            ),
            option_education_evidence=option_evidence,
        )
    )
    assert criteria.status == "assembled"
    compiled = compile_prepared_scenario(
        preparation,
        scenario_request(criteria.packets),
    )
    if compiled.scenario is not None and not compiled.scenario.publishable:
        orchestration = compiled.review_orchestration
        assert orchestration is not None
        envelopes = []
        for state in orchestration.actionable_requests:
            selection = next(
                item
                for item in compiled.scenario.selections
                if item.candidate_set_id == state.request.candidate_set_id
            )
            selected_option = next(
                option
                for option in state.request.options
                if option.candidate_id is not None
            )
            envelopes.append(
                accepted_envelope(
                    selection,
                    state.request,
                    selected_option.option_id,
                    scenario_context_fingerprint=(
                        compiled.scenario.scenario_context_fingerprint
                    ),
                )
            )
        compiled = compile_prepared_scenario(
            preparation,
            scenario_request(
                criteria.packets,
                decision_record=ScenarioDecisionRecord(
                    mode=DecisionProcessMode.ACCEPTED_LEDGER,
                    accepted_envelopes=tuple(envelopes),
                ),
            ),
        )
    assert compiled.scenario is not None and compiled.scenario.publishable
    return adopt_reference_satn(
        compiled.scenario,
        governed_decision=reference_decision(compiled.scenario),
    )


def _assembly(
    *,
    topography: bool = False,
    reject_reference: bool = False,
    crs: str = "EPSG:27700",
):
    return _run_assembly(
        _OMITTED,
        topography=topography,
        reject_reference=reject_reference,
        crs=crs,
    )


def _replay_assembly(
    plan: ReferenceApplicationPlan,
    *,
    topography: bool = False,
    reject_reference: bool = False,
    crs: str = "EPSG:27700",
):
    return _run_assembly(
        plan,
        topography=topography,
        reject_reference=reject_reference,
        crs=crs,
    )


def _run_assembly(
    plan: ReferenceApplicationPlan | object,
    *,
    topography: bool,
    reject_reference: bool,
    crs: str,
):
    config = _configured_area()
    runtime = FakeAgentRuntime()
    if reject_reference:
        config.compilation.agent.response_mode = "direct-runtime"
        config.compilation.agent.review_statuses = (TrafficLight.GREEN,)
        runtime = FakeAgentRuntime(
            {
                AgentRole.DECISION: [
                    {"request_id": "$request", "choice_id": "3"},
                ]
            }
        )
    assembler = assemble_backbone_outward if plan is _OMITTED else _assemble_backbone_outward
    kwargs = {} if plan is _OMITTED else {"reference_application_plan": plan}
    return assembler(
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

    assembly = _replay_assembly(plan)

    child = assembly.connections.set_index("place_id").loc["child"]
    parent = assembly.obligations.set_index("place_id").loc["parent"]
    parent_provenance = json.loads(parent["provenance"])
    child_obligation = assembly.obligations.set_index("place_id").loc["child"]
    child_branch = assembly.branches.set_index("branch_id").loc[child["branch_id"]]
    selected_option = next(
        option
        for option in json.loads(child["alignment_options"])
        if option["selected"] is True
    )
    logical_id = plan.candidate_bindings[0].logical_connection_id
    assert set(assembly.connections["place_id"]) == {"child"}
    assert parent["service_status"] == "served"
    assert pd.isna(parent["access_connection_id"])
    assert parent_provenance["service_kind"] == "backbone-access-association"
    assert parent_provenance["association_kind"] == "colocated-direct-strategic-spine"
    assert child["parent_place_id"] == "parent"
    assert json.loads(child["provenance"])["reference_application"] == {
        "binding_fingerprint": plan.candidate_bindings[0].binding_fingerprint,
        "deterministic_recommended_role": "not-evaluated",
        "deterministic_topography_status": "not-evaluated",
        "logical_connection_id": logical_id,
        "plan_fingerprint": plan.plan_fingerprint,
        "selected_candidate_id": plan.candidate_bindings[0].selected_candidate_id,
        "selected_route_role": "ncn-informed",
        "routing_edge_ids": ["child-parent"],
        "reverse_routing_edge_ids": ["parent-child"],
        "geometry_fingerprint": plan.candidate_bindings[0].geometry_fingerprint,
        "selected_alignment_option": selected_option,
        "published_distance_km": child["distance_km"],
    }
    assert assembly.compilation_diagnostics["reference_application"] == {
        "contract": "satn-reference-application-plan/v1",
        "plan_fingerprint": plan.plan_fingerprint,
        "status": "applied",
        "expected_count": 1,
        "applied_count": 1,
        "logical_connection_ids": [logical_id],
        "source_to_regenerated_access_connection_ids": {
            "old-parent-child-access-id": child["access_connection_id"]
        },
        "selected_candidate_ids": {
            logical_id: plan.candidate_bindings[0].selected_candidate_id
        },
        "binding_fingerprints": {
            logical_id: plan.candidate_bindings[0].binding_fingerprint
        },
        "selected_alignment_options": {logical_id: selected_option},
        "published_distances_km": {logical_id: child["distance_km"]},
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


def test_ordinary_assembly_has_no_reference_replay_state() -> None:
    ordinary = _assembly()

    assert "reference_application" not in ordinary.compilation_diagnostics


def test_compile_network_ordinary_behavior_remains_reference_free() -> None:
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")

    ordinary = compile_network(config, parallel_spine_source(), FakeAgentRuntime())

    assert "reference_application" not in ordinary.compilation_diagnostics


def test_ordinary_public_compiler_signatures_remain_exactly_compatible() -> None:
    assert tuple(inspect.signature(assemble_backbone_outward).parameters) == (
        "communities",
        "schools",
        "gateways",
        "strategic_spines",
        "graph",
        "gate",
        "max_connection_km",
        "elevation_evidence",
        "topography_config",
    )
    assert tuple(inspect.signature(compile_network).parameters) == (
        "config",
        "source",
        "runtime",
        "governed_input_fingerprint",
        "decision_resolver",
        "heartbeat",
        "cross_spine_progress",
    )
    assert tuple(inspect.signature(compile_reference_network).parameters) == (
        "config",
        "runtime",
        "reference",
        "source_preparation",
        "decision_ledger",
        "heartbeat",
    )


def test_raw_reference_plan_has_no_public_compile_or_assembly_authority() -> None:
    raw_plan = _plan(_graph())
    config = _configured_area()

    with pytest.raises(TypeError, match="reference_application_plan"):
        assemble_backbone_outward(
            _communities(),
            _empty(),
            _empty(),
            _spines(),
            _graph(),
            CompilationGate(FakeAgentRuntime(), config.compilation.agent, ""),
            10.0,
            reference_application_plan=raw_plan,  # type: ignore[call-arg]
        )
    with pytest.raises(TypeError, match="reference_application_plan"):
        compile_network(
            config,
            parallel_spine_source(),
            FakeAgentRuntime(),
            governed_input_fingerprint="a" * 64,
            reference_application_plan=raw_plan,  # type: ignore[call-arg]
        )


def test_fresh_baseline_validates_then_replays_through_dedicated_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _configured_area(tmp_path)
    source = parallel_spine_source()
    baseline = compile_network(
        config,
        source,
        FakeAgentRuntime(),
        governed_input_fingerprint="a" * 64,
    )
    baseline_preparation = baseline.spine_access_candidate_preparation
    assert baseline_preparation is not None
    reference = _reference_from_current_preparation(
        config,
        baseline_preparation,
    )
    reference = reference_for_area(reference, config)
    loader_calls = []

    def load_governed_snapshot(area: CouncilConfig):
        loader_calls.append(area)
        return parallel_spine_source()

    monkeypatch.setattr("satn.pipeline.load_snapshot", load_governed_snapshot)

    replayed = compile_reference_network(
        config,
        FakeAgentRuntime(),
        reference,
        baseline_preparation,
    )

    diagnostics = replayed.compilation_diagnostics["reference_application"]
    assert diagnostics["status"] == "applied"
    assert diagnostics["plan_fingerprint"]
    assert diagnostics["applied_count"] == 1
    assert replayed.governed_input_fingerprint
    assert loader_calls == [config]


def test_complete_baseline_ledger_replays_fresh_without_runtime_double_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _configured_area(tmp_path)
    config.compilation.agent.response_mode = "direct-runtime"
    config.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    config.compilation.agent.max_requests = 100
    config.compilation.agent.max_tokens = 10_000
    reference_manifest = compilation_dependency_manifest(
        config,
        compiler_path="reference",
    )
    governed_input = compilation_governed_input_fingerprint(
        config,
        dependency_manifest=reference_manifest,
    )
    source = parallel_spine_source()
    baseline = compile_network(
        config,
        source,
        FakeAgentRuntime(),
        governed_input_fingerprint=governed_input,
    )
    exact_preparation = baseline.spine_access_candidate_preparation
    assert exact_preparation is not None
    ledger = AgentDecisionLedger.model_validate(
        {
            "decision_contract": baseline.decision_contract,
            "responses": baseline.accepted_decisions,
        }
    )
    assert ledger.responses
    reference = reference_for_area(
        _reference_from_current_preparation(config, exact_preparation),
        config,
    )
    final_runtime = CountingAgentRuntime()
    monkeypatch.setattr(
        "satn.pipeline.load_snapshot",
        lambda area: parallel_spine_source(),
    )

    replayed = compile_reference_network(
        config,
        final_runtime,
        reference,
        exact_preparation,
        decision_ledger=ledger,
    )

    assert replayed.accepted_decisions
    assert final_runtime.call_count == 0


def test_dedicated_boundary_rejects_tampered_source_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _configured_area(tmp_path)
    source = parallel_spine_source()
    baseline = compile_network(config, source, FakeAgentRuntime())
    exact_preparation = baseline.spine_access_candidate_preparation
    assert exact_preparation is not None
    reference = _reference_from_current_preparation(
        config,
        exact_preparation,
    )
    reference = reference_for_area(reference, config)
    tampered = replace(
        exact_preparation,
        preparation_fingerprint="0" * 64,
    )
    monkeypatch.setattr(
        "satn.pipeline.load_snapshot",
        lambda area: parallel_spine_source(),
    )

    with pytest.raises(ValueError, match="preparation fingerprint is stale"):
        compile_reference_network(
            config,
            FakeAgentRuntime(),
            reference,
            tampered,
        )


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
        _replay_assembly(plan)


def test_replay_fails_closed_for_a_stationary_adopted_route() -> None:
    graph = _graph()
    plan = _rebuilt_plan(
        _plan(graph),
        routing_start_node_id="parent",
        routing_end_node_id="parent",
        route_role="direct",
        routing_edge_ids=("fabricated-forward-edge",),
        reverse_routing_edge_ids=("fabricated-reverse-edge",),
    )

    with pytest.raises(ValueError, match="route is stationary"):
        _validated_reference_option(
            plan.candidate_bindings[0],
            graph,
            checkpoint="test",
        )


def test_replay_fails_closed_for_a_route_geometry_mismatch() -> None:
    plan = _rebuilt_plan(_plan(_graph()), geometry_fingerprint="0" * 64)

    with pytest.raises(ValueError, match="geometry fingerprint"):
        _replay_assembly(plan)


def test_replay_geometry_verification_uses_preparation_canonical_crs() -> None:
    graph = _graph("EPSG:4326")

    assembly = _replay_assembly(_plan(graph), crs="EPSG:4326")

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
        _replay_assembly(duplicate_plan)


def test_reference_route_remains_selected_despite_other_topography_role() -> None:
    plan = _plan(_graph(), route_role="direct")

    assembly = _replay_assembly(plan, topography=True)

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
    assembly = _replay_assembly(
        _plan(_graph(), route_role=route_role),
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
        _replay_assembly(plan)


def test_replay_does_not_fall_back_after_the_gate_rejects_a_planned_route() -> None:
    with pytest.raises(ValueError, match="gate rejected planned"):
        _replay_assembly(_plan(_graph()), reject_reference=True)
