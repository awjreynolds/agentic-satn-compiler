"""Focused deployment scenario and CLI seams for issue #237."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import geopandas as gpd
import pytest
import yaml
from shapely.geometry import LineString
from typer.testing import CliRunner

from satn.cli import app
from satn.deployment_scenarios import (
    AuthorityRecordKind,
    DeploymentAuthorityRecord,
    DeploymentClaim,
    DeploymentRequestRegister,
    DeploymentScenarioConfiguration,
    NamedOfficerScenarioDeployment,
    RoadGraphRouteControlScenarioBinding,
    compile_clean_baseline_deployment,
    compile_named_officer_scenario,
    export_response_packet,
    import_response_into_register,
)
from satn.models import AreaDefinition
from satn.officer_decisions import (
    ActionableHumanInterventionRequest,
    ClassifyCommunityAction,
    CleanSATNBaseline,
    ExcludeFromRoutingAction,
    GovernedBaselineTarget,
    HumanInterventionRecord,
    HumanInterventionResponse,
    HumanInterventionResponseOutcome,
    InterventionRequestState,
    NetworkPublicationKind,
    OfferedOfficerAction,
    OfficerDecision,
    OfficerDecisionLedger,
    OfficerDecisionTarget,
    OfficerDecisionType,
    OfficerTargetKind,
    parse_canonical_officer_decision_ledger,
    publication_label,
)
from satn.route_controls import EdgeBindingMode
from satn.routing import RoadGraph

PROJECT = Path(__file__).parents[1]
TODAY = date(2026, 7, 30)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def clean_baseline() -> CleanSATNBaseline:
    return CleanSATNBaseline(
        baseline_id="baseline-clean-1",
        network_json='{"features":[{"id":"community-1"}],"type":"FeatureCollection"}',
        evidence_snapshot_fingerprint=SHA_A,
        profile_fingerprint=SHA_B,
        governed_evidence_ids=("evidence-1",),
        targets=(
            GovernedBaselineTarget(
                target=OfficerDecisionTarget(
                    kind=OfficerTargetKind.COMMUNITY,
                    target_id="community-1",
                )
            ),
        ),
    )


def empty_ledger(clean: CleanSATNBaseline) -> OfficerDecisionLedger:
    return OfficerDecisionLedger(
        baseline_fingerprint=clean.baseline_fingerprint,
        evidence_snapshot_fingerprint=clean.evidence_snapshot_fingerprint,
        profile_fingerprint=clean.profile_fingerprint,
    )


def officer_ledger(clean: CleanSATNBaseline) -> OfficerDecisionLedger:
    decision = OfficerDecision(
        decision_id="officer-decision-1",
        decision_type=OfficerDecisionType.CLASSIFY_COMMUNITY,
        target=OfficerDecisionTarget(
            kind=OfficerTargetKind.COMMUNITY,
            target_id="community-1",
        ),
        action=ClassifyCommunityAction(classification="rural"),
        decision_maker="Alex Officer",
        decision_maker_role="Principal Transport Planner",
        organisation="Example Council",
        decision_date=TODAY,
        rationale="The governed evidence supports rural treatment.",
        evidence_ids=("evidence-1",),
        source_url="https://example.test/decisions/1",
        effective_from=TODAY,
        baseline_fingerprint=clean.baseline_fingerprint,
        evidence_snapshot_fingerprint=clean.evidence_snapshot_fingerprint,
        profile_fingerprint=clean.profile_fingerprint,
    )
    return OfficerDecisionLedger(
        baseline_fingerprint=clean.baseline_fingerprint,
        evidence_snapshot_fingerprint=clean.evidence_snapshot_fingerprint,
        profile_fingerprint=clean.profile_fingerprint,
        decisions=(decision,),
    )


def named_configuration(
    tmp_path: Path,
    *,
    claim: DeploymentClaim = DeploymentClaim.DEVELOPMENT_TEST,
    route_control_binding: str | None = None,
) -> DeploymentScenarioConfiguration:
    return DeploymentScenarioConfiguration(
        config_path=tmp_path / "scenarios.yaml",
        deployment_id="banes",
        officer_scenarios=(
            NamedOfficerScenarioDeployment(
                name="officer-rural",
                publication_path="officer-rural",
                ledger_path=Path("officer-rural-ledger.json"),
                claim=claim,
                route_control_binding=route_control_binding,
            ),
        ),
    )


def request_register(clean: CleanSATNBaseline) -> DeploymentRequestRegister:
    request = ActionableHumanInterventionRequest(
        request_id="human-intervention-1",
        request_fingerprint=SHA_C,
        baseline_fingerprint=clean.baseline_fingerprint,
        evidence_snapshot_fingerprint=clean.evidence_snapshot_fingerprint,
        profile_fingerprint=clean.profile_fingerprint,
        governed_evidence_ids=("evidence-1",),
        offered_actions=(
            OfferedOfficerAction(
                option_id="classify-rural",
                decision_type=OfficerDecisionType.CLASSIFY_COMMUNITY,
                target=OfficerDecisionTarget(
                    kind=OfficerTargetKind.COMMUNITY,
                    target_id="community-1",
                ),
                action=ClassifyCommunityAction(classification="rural"),
            ),
        ),
    )
    return DeploymentRequestRegister(
        records=(
            HumanInterventionRecord(
                request=request,
                state=InterventionRequestState.PENDING,
            ),
        )
    )


def response(clean: CleanSATNBaseline) -> HumanInterventionResponse:
    return HumanInterventionResponse(
        response_id="human-response-1",
        request_id="human-intervention-1",
        request_fingerprint=SHA_C,
        selected_option_id="classify-rural",
        outcome=HumanInterventionResponseOutcome.ACCEPT,
        baseline_fingerprint=clean.baseline_fingerprint,
        evidence_snapshot_fingerprint=clean.evidence_snapshot_fingerprint,
        profile_fingerprint=clean.profile_fingerprint,
        decision_maker="Alex Officer",
        decision_maker_role="Principal Transport Planner",
        organisation="Example Council",
        response_date=TODAY,
        rationale="Apply the offered rural classification.",
        evidence_ids=("evidence-1",),
        source_url="https://example.test/responses/1",
        effective_from=TODAY,
    )


@pytest.mark.parametrize("deployment_id", ["banes", "weca"])
def test_banes_and_weca_parse_explicit_clean_baselines_and_selection_profiles(
    deployment_id: str,
) -> None:
    area = AreaDefinition.from_yaml(
        PROJECT / "deployments" / deployment_id / "area.yaml"
    )
    scenarios = DeploymentScenarioConfiguration.from_yaml(
        PROJECT / "deployments" / deployment_id / "scenarios.yaml"
    )

    assert area.deployment_slug == scenarios.deployment_id == deployment_id
    assert area.source.urban_place_source_ids == []
    assert area.compilation.network_selection is not None
    assert area.compilation.network_selection.profile_id == (
        f"{deployment_id}-network-selection-v1"
    )
    assert scenarios.clean_baseline.authority_state == (
        NetworkPublicationKind.GENERATED_BASELINE
    )
    assert scenarios.officer_scenarios == ()
    baseline = compile_clean_baseline_deployment(
        scenarios,
        clean_baseline(),
        runtime_provider="fake",
        effective_on=TODAY,
    )
    assert baseline.scenario.applied_decision_ids == ()
    assert baseline.scenario.network_json == clean_baseline().network_json
    assert baseline.authority.authority_record_kind == (
        AuthorityRecordKind.DETERMINISTIC_COMPILER
    )
    assert baseline.authority.configured_runtime_kind == (
        AuthorityRecordKind.FAKE_AGENT_RUNTIME
    )


def test_named_officer_scenario_preserves_baseline_and_lists_every_change(
    tmp_path: Path,
) -> None:
    clean = clean_baseline()
    configuration = named_configuration(tmp_path)
    before = clean.model_dump_json()

    publication = compile_named_officer_scenario(
        configuration,
        clean,
        officer_ledger(clean),
        "officer-rural",
        runtime_provider="fake",
        effective_on=TODAY,
    )

    assert clean.model_dump_json() == before
    assert publication.scenario.network_json == clean.network_json
    assert publication.scenario.scenario_id != clean.baseline_id
    assert publication.comparison.applied_officer_decision_ids == (
        "officer-decision-1",
    )
    assert publication.comparison.resulting_network_changes == (
        publication.scenario.baseline_to_scenario_change_summary
    )
    assert publication.authority.authority_record_kind == AuthorityRecordKind.HUMAN_OFFICER
    assert publication.authority.configured_runtime_kind == (
        AuthorityRecordKind.FAKE_AGENT_RUNTIME
    )


def test_response_packet_import_is_non_waiting_and_uses_the_officer_ledger(
    tmp_path: Path,
) -> None:
    clean = clean_baseline()
    register = request_register(clean)
    packet = export_response_packet(register, "human-intervention-1")

    assert packet.offered_option_ids == ("classify-rural",)
    assert packet.current_state == InterventionRequestState.PENDING

    updated, ledger = import_response_into_register(
        register,
        response(clean),
        empty_ledger(clean),
    )

    assert register.records[0].state == InterventionRequestState.PENDING
    assert updated.records[0].state == InterventionRequestState.ANSWERED
    assert len(ledger.decisions) == 1
    assert ledger.decisions[0].actor_kind == "human-officer"
    assert parse_canonical_officer_decision_ledger(ledger.canonical_json()) == ledger

    register_path = tmp_path / "requests.json"
    packet_path = tmp_path / "response-packet.json"
    ledger_path = tmp_path / "ledger.json"
    register_path.write_bytes(register.canonical_json())
    ledger_path.write_bytes(empty_ledger(clean).canonical_json())
    runner = CliRunner()

    listed = runner.invoke(app, ["scenario", "requests", "list", str(register_path)])
    assert listed.exit_code == 0
    assert "human-intervention-1" in listed.stdout
    exported = runner.invoke(
        app,
        [
            "scenario",
            "requests",
            "export",
            str(register_path),
            "human-intervention-1",
            "--output",
            str(packet_path),
        ],
    )
    assert exported.exit_code == 0
    assert json.loads(packet_path.read_text())["response_contract"] == (
        "satn-human-intervention-response/v1"
    )
    validated = runner.invoke(
        app,
        ["scenario", "ledger", "validate", str(ledger_path)],
    )
    assert validated.exit_code == 0
    assert empty_ledger(clean).ledger_fingerprint in validated.stdout


def test_cli_import_and_scenario_compile_write_separate_artifacts(tmp_path: Path) -> None:
    clean = clean_baseline()
    register = request_register(clean)
    register_path = tmp_path / "requests.json"
    response_path = tmp_path / "response.json"
    empty_path = tmp_path / "empty-ledger.json"
    updated_register_path = tmp_path / "updated-requests.json"
    officer_path = tmp_path / "officer-ledger.json"
    register_path.write_bytes(register.canonical_json())
    response_path.write_text(response(clean).model_dump_json(), encoding="ascii")
    empty_path.write_bytes(empty_ledger(clean).canonical_json())
    runner = CliRunner()

    imported = runner.invoke(
        app,
        [
            "scenario",
            "ledger",
            "import",
            str(register_path),
            str(response_path),
            str(empty_path),
            "--output-register",
            str(updated_register_path),
            "--output-ledger",
            str(officer_path),
        ],
    )
    assert imported.exit_code == 0
    imported_ledger = parse_canonical_officer_decision_ledger(
        officer_path.read_bytes()
    )
    assert len(imported_ledger.decisions) == 1

    scenario_config = tmp_path / "scenarios.yaml"
    scenario_config.write_text(
        yaml.safe_dump(
            {
                "schema_version": "satn-deployment-scenarios/v1",
                "deployment_id": "banes",
                "clean_baseline": {
                    "name": "clean-baseline",
                    "publication_path": "baseline",
                    "authority_state": "generated-baseline",
                },
                "officer_scenarios": [
                    {
                        "name": "officer-rural",
                        "publication_path": "officer-rural",
                        "ledger_path": str(officer_path),
                        "claim": "development-test",
                    }
                ],
                "reference_satn": None,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(clean.model_dump_json(), encoding="ascii")
    baseline_output = tmp_path / "baseline-publication.json"
    officer_output = tmp_path / "officer-publication.json"

    compiled_baseline = runner.invoke(
        app,
        [
            "scenario",
            "compile",
            str(scenario_config),
            str(PROJECT / "deployments" / "banes" / "area.yaml"),
            str(baseline_path),
            "--output",
            str(baseline_output),
            "--effective-on",
            TODAY.isoformat(),
        ],
    )
    assert compiled_baseline.exit_code == 0
    compiled_officer = runner.invoke(
        app,
        [
            "scenario",
            "compile",
            str(scenario_config),
            str(PROJECT / "deployments" / "banes" / "area.yaml"),
            str(baseline_path),
            "--scenario",
            "officer-rural",
            "--output",
            str(officer_output),
            "--effective-on",
            TODAY.isoformat(),
        ],
    )
    assert compiled_officer.exit_code == 0
    assert json.loads(baseline_output.read_text())["authority"]["authority_state"] == (
        "generated-baseline"
    )
    assert json.loads(officer_output.read_text())["authority"]["authority_state"] == (
        "officer-informed-scenario"
    )


def test_fake_runtime_cannot_claim_production_officer_review_or_adoption(
    tmp_path: Path,
) -> None:
    clean = clean_baseline()
    with pytest.raises(ValueError, match="fake runtime"):
        compile_named_officer_scenario(
            named_configuration(
                tmp_path,
                claim=DeploymentClaim.PRODUCTION_OFFICER_REVIEW,
            ),
            clean,
            officer_ledger(clean),
            "officer-rural",
            runtime_provider="fake",
            effective_on=TODAY,
        )
    with pytest.raises(ValueError, match="fake runtime"):
        DeploymentAuthorityRecord(
            authority_state=NetworkPublicationKind.REFERENCE_SATN,
            public_label=publication_label(NetworkPublicationKind.REFERENCE_SATN),
            authority_record_kind=AuthorityRecordKind.FORMAL_ADOPTION,
            configured_runtime_kind=AuthorityRecordKind.FAKE_AGENT_RUNTIME,
            configured_runtime_provider="fake",
            claim=DeploymentClaim.FORMAL_ADOPTION,
            adoption_record_id="decision-committee-1",
        )


def test_route_control_integration_remains_a_small_required_binding(
    tmp_path: Path,
) -> None:
    clean = clean_baseline()
    configuration = named_configuration(
        tmp_path,
        route_control_binding="route-controls-v1",
    )
    with pytest.raises(ValueError, match="route-control binding"):
        compile_named_officer_scenario(
            configuration,
            clean,
            officer_ledger(clean),
            "officer-rural",
            runtime_provider="pydantic-ai",
            effective_on=TODAY,
        )



def test_route_control_binding_compiles_scenario_through_constrained_road_graph(
    tmp_path: Path,
) -> None:
    edges = gpd.GeoDataFrame(
        [
            {
                "osmid": "road-ab",
                "u": "a",
                "v": "b",
                "length": 100,
                "highway": "primary",
                "geometry": LineString([(0, 0), (100, 0)]),
            },
            {
                "osmid": "road-ba",
                "u": "b",
                "v": "a",
                "length": 100,
                "highway": "primary",
                "geometry": LineString([(100, 0), (0, 0)]),
            },
            {
                "osmid": "cycle-ac",
                "u": "a",
                "v": "c",
                "length": 130,
                "highway": "cycleway",
                "geometry": LineString([(0, 0), (50, 30)]),
            },
            {
                "osmid": "cycle-ca",
                "u": "c",
                "v": "a",
                "length": 130,
                "highway": "cycleway",
                "geometry": LineString([(50, 30), (0, 0)]),
            },
            {
                "osmid": "cycle-cb",
                "u": "c",
                "v": "b",
                "length": 130,
                "highway": "cycleway",
                "geometry": LineString([(50, 30), (100, 0)]),
            },
            {
                "osmid": "cycle-bc",
                "u": "b",
                "v": "c",
                "length": 130,
                "highway": "cycleway",
                "geometry": LineString([(100, 0), (50, 30)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    snapshot = hashlib.sha256(b"route-snapshot").hexdigest()
    profile = hashlib.sha256(b"route-profile").hexdigest()
    binding = RoadGraph(edges).bind_route_edge(
        "a",
        "b",
        evidence_snapshot_fingerprint=snapshot,
        mode=EdgeBindingMode.BIDIRECTIONAL,
    )
    clean = CleanSATNBaseline(
        baseline_id="route-baseline",
        network_json='{"features":[],"type":"FeatureCollection"}',
        evidence_snapshot_fingerprint=snapshot,
        profile_fingerprint=profile,
        governed_evidence_ids=("edge-survey",),
        targets=(
            GovernedBaselineTarget(
                target=OfficerDecisionTarget(
                    kind=OfficerTargetKind.ROUTING_EDGE,
                    target_id=binding.binding_id,
                )
            ),
        ),
    )
    decision = OfficerDecision(
        decision_id="officer-route-decision",
        decision_type=OfficerDecisionType.EXCLUDE_FROM_ROUTING,
        target=OfficerDecisionTarget(
            kind=OfficerTargetKind.ROUTING_EDGE,
            target_id=binding.binding_id,
        ),
        action=ExcludeFromRoutingAction(),
        decision_maker="Alex Officer",
        decision_maker_role="Principal Transport Planner",
        organisation="Example Council",
        decision_date=TODAY,
        rationale="Use the governed cycleway in this scenario.",
        evidence_ids=("edge-survey",),
        source_url="https://example.test/route-decisions/1",
        effective_from=TODAY,
        baseline_fingerprint=clean.baseline_fingerprint,
        evidence_snapshot_fingerprint=snapshot,
        profile_fingerprint=profile,
    )
    ledger = OfficerDecisionLedger(
        baseline_fingerprint=clean.baseline_fingerprint,
        evidence_snapshot_fingerprint=snapshot,
        profile_fingerprint=profile,
        decisions=(decision,),
    )
    configuration = named_configuration(
        tmp_path,
        route_control_binding="route-controls-v1",
    )
    route_binding = RoadGraphRouteControlScenarioBinding(
        binding_id="route-controls-v1",
        edges=edges,
        route_edge_bindings=(binding,),
    )

    publication = compile_named_officer_scenario(
        configuration,
        clean,
        ledger,
        "officer-rural",
        runtime_provider="pydantic-ai",
        effective_on=TODAY,
        route_control_binding=route_binding,
    )

    assert publication.scenario.route_controls is not None
    assert publication.scenario.route_control_fingerprint in (
        publication.scenario.dependency_fingerprints
    )
    constrained = route_binding.constrained_graph(publication.scenario)
    option = constrained.option("a", "b", "direct")
    assert option is not None
    assert option.edge_ids == ["cycle-ac", "cycle-cb"]
