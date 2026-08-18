from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
from pydantic import BaseModel
from shapely.geometry import LineString, Point, Polygon
from test_backbone_assembly import parallel_spine_source

from satn import compile
from satn.agents import AgentRole, AgentRuntime, RuntimeReply
from satn.constants import DISCLAIMER
from satn.models import CouncilConfig, TrafficLight
from satn.sources import snapshot

PROJECT = Path(__file__).parents[1]


class UnavailableRuntime(AgentRuntime):
    name = "unavailable"
    model = "offline-test"

    def run(
        self,
        role: AgentRole,
        payload: BaseModel,
        output_type: type[BaseModel],
    ) -> RuntimeReply:
        raise RuntimeError("provider unavailable")


def fixture_config(tmp_path: Path) -> Path:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    return fixture / "council.yaml"


def test_public_api_runs_complete_fixture(tmp_path: Path) -> None:
    config_path = fixture_config(tmp_path)
    config = CouncilConfig.from_yaml(config_path)
    snapshot_path = snapshot(config)

    assert snapshot_path.name == "fixture-001"
    manifest = json.loads((snapshot_path / "snapshot.json").read_text())
    assert manifest["source_kind"] == "fixture"
    assert manifest["disclaimer"] == DISCLAIMER

    result = compile(config)

    assert result.status == "complete"
    assert result.connections == 2
    assert result.gaps == 0
    assert result.criteria["connections"]["mandatory_checks"] == "green"
    assert result.agent_records[0].decision == "accept"
    assert all(record.governing_status == "green" for record in result.agent_records)
    assert all(record.review_policy == ("amber", "red") for record in result.agent_records)
    assert all(record.review_required is False for record in result.agent_records)
    assert set(result.artifacts) == {
        "geopackage",
        "geojson",
        "asset_accounting",
        "asset_accounting_geojson",
        "run",
        "agents",
        "divergences",
        "human_intervention_requests",
        "backbone_comparison",
        "review_map",
        "review_zip",
        "pdf",
    }
    assert all(path.exists() for path in result.artifacts.values())
    assert "connections" not in set(pyogrio.list_layers(result.artifacts["geopackage"])[:, 0])
    assert {
        "strategic_spines",
        "access_obligations",
        "spine_access_connections",
        "spine_access_branches",
        "a_road_spines",
        "ncn_routes",
        "schools",
        "retail_centres",
        "healthcare",
    } <= set(pyogrio.list_layers(result.artifacts["geopackage"])[:, 0])
    strategic_spines = gpd.read_file(result.artifacts["geopackage"], layer="strategic_spines")
    assert set(strategic_spines["spine_kind"]) == {"a-road", "ncn"}
    assert set(strategic_spines["network_role"]) == {"strategic-spine"}
    assert "b2" not in set(strategic_spines["source_id"])
    a_road = strategic_spines[strategic_spines["spine_kind"] == "a-road"].iloc[0]
    assert a_road["intervention_assumption"] == (
        "Major engineering required to provide high-quality protected or shared provision"
    )
    assert a_road["design_status"] == "strategic assumption; not a carriageway or final design"
    spine_access = gpd.read_file(result.artifacts["geopackage"], layer="spine_access_connections")
    access_obligations = gpd.read_file(result.artifacts["geopackage"], layer="access_obligations")
    spine_branches = gpd.read_file(result.artifacts["geopackage"], layer="spine_access_branches")
    assert len(spine_access) == 2
    community_access = spine_access[spine_access["obligation_kind"] == "community"]
    school_access = spine_access[spine_access["obligation_kind"] == "school"]
    assert set(community_access["community_id"]) == {"westfield", "eastfield"}
    assert set(spine_access["network_role"]) == {"spine-access-connection"}
    assert school_access.empty
    school_obligation = access_obligations[
        access_obligations["school_id"] == "school-fixture"
    ].iloc[0]
    association = json.loads(school_obligation["provenance"])
    assert school_obligation.geometry.geom_type == "Point"
    assert pd.isna(school_obligation["access_connection_id"])
    assert association["service_kind"] == "backbone-access-association"
    assert association["association_kind"] == "colocated-existing-backbone"
    assert set(spine_access["spine_id"]) <= set(strategic_spines["spine_id"])
    assert spine_access["access_connection_id"].str.startswith("spine-access-").all()
    assert spine_access["community_attachment_node"].notna().all()
    assert (spine_access["community_attachment_distance_m"] >= 0).all()
    assert spine_access["target_attachment_node"].notna().all()
    assert all(json.loads(value) for value in spine_access["source_ids"])
    assert result.metadata["strategic_spines"] == 2
    assert result.metadata["access_obligations"] == 3
    assert result.metadata["school_access_obligations"] == 1
    assert result.metadata["spine_access_connections"] == 2
    assert result.metadata["spine_access_branches"] >= 1
    assert set(spine_access["branch_id"]) == set(spine_branches["branch_id"])
    assert set(spine_branches["network_role"]) == {"spine-access-branch"}
    obligation = gpd.read_file(result.artifacts["geopackage"], layer="access_obligations")
    assert set(obligation["network_role"]) == {
        "community-access-obligation",
        "school-access-obligation",
    }
    assert set(obligation["service_status"]) == {"served"}
    assert set(obligation["access_connection_id"].dropna()) == set(
        spine_access["access_connection_id"]
    )
    school_obligation = obligation[obligation["obligation_kind"] == "school"].iloc[0]
    assert school_obligation["school_id"] == "school-fixture"
    assert school_obligation["access_point_status"] == "mapped"
    assert result.metadata["strategic_spine_records"][0]["spine_id"] in set(
        strategic_spines["spine_id"]
    )
    assert json.loads(result.metadata["strategic_spine_records"][0]["provenance"])["evidence_id"]
    assert result.metadata["spine_access_connection_records"][0]["access_connection_id"] in set(
        spine_access["access_connection_id"]
    )
    assert json.loads(result.metadata["spine_access_connection_records"][0]["source_ids"])
    assert (
        result.metadata["spine_access_connection_records"][0]["community_attachment_node"]
        == spine_access.iloc[0]["community_attachment_node"]
    )
    assert (
        result.metadata["access_obligation_records"][0]["obligation_id"]
        == obligation.iloc[0]["obligation_id"]
    )
    assert (
        json.loads(result.metadata["access_obligation_records"][0]["provenance"])["community_id"]
        == obligation.iloc[0]["community_id"]
    )
    geojson = json.loads(result.artifacts["geojson"].read_text())
    assert geojson["disclaimer"] == DISCLAIMER
    geojson_ids = {feature["id"] for feature in geojson["features"]}
    assert set(strategic_spines["spine_id"]) <= geojson_ids
    assert set(spine_access["access_connection_id"]) <= geojson_ids
    assert set(spine_branches["branch_id"]) <= geojson_ids
    assert set(obligation["obligation_id"]) <= geojson_ids
    assert result.artifacts["pdf"].read_bytes().startswith(b"%PDF")
    html = result.artifacts["review_map"].read_text()
    assert DISCLAIMER in html
    assert 'id="feature-details"' in html
    assert 'id="layer-community-connections"' not in html
    assert 'id="layer-alignment-review"' in html
    assert 'href="https://github.com/awjreynolds/agentic-satn-compiler"' in html
    assert 'id="layer-strategic-network" type="checkbox" checked' in html
    assert "complete compiled Backbone-and-Access Network" in html
    assert "Strategic Spines, Spine Access Connections, Cross-Spine Connectors" in html
    assert 'id="legend-strategic-network"' in html
    assert 'id="legend-alignment-review"' in html
    assert 'id="layer-schools"' in html
    assert 'id="layer-retail-centres"' in html
    assert 'id="layer-healthcare"' in html
    data = (result.artifacts["review_map"].parent / "data.js").read_text()
    assert '"id": "spine-access-' in data
    app = (result.artifacts["review_map"].parent / "assets" / "review-map.js").read_text()
    assert "button.dataset.featureId = feature.id" in app
    assert "Typed agent records" in html
    assert (result.artifacts["review_map"].parent / "agent-records.json").exists()
    assert '"place_name": "Eastfield"' in data
    assert '"place_name": "Westfield"' in data
    assert "[Open SATN Deployments](https://awjreynolds.github.io/agentic-satn-compiler/)" in (
        PROJECT / "README.md"
    ).read_text(encoding="utf-8")


def test_ncn_connector_link_is_published_as_evidence_but_not_promoted_to_spine(
    tmp_path: Path,
) -> None:
    config_path = fixture_config(tmp_path)
    context_path = config_path.parent / "source" / "context.geojson"
    context = gpd.read_file(context_path)
    link = gpd.GeoDataFrame(
        [
            {
                "evidence_id": "ncn-link-fixture",
                "feature_type": "ncn-link",
                "name": "NCN 1 connector link",
                "category": "National Cycle Network connector link",
                "source_id": "ncn-link-1",
                "feature_count": 1,
                "network_scope": "rural",
                "ncn_evidence_role": "connector-link",
                "geometry": LineString([(-2.46, 51.4), (-2.455, 51.4)]),
            }
        ],
        crs=context.crs,
    )
    context = gpd.GeoDataFrame(
        pd.concat([context, link], ignore_index=True, sort=False),
        geometry="geometry",
        crs=context.crs,
    )
    context.to_file(context_path, driver="GeoJSON")
    config = CouncilConfig.from_yaml(config_path)
    snapshot(config)

    result = compile(config)

    ncn_evidence = gpd.read_file(result.artifacts["geopackage"], layer="ncn_routes")
    assert set(ncn_evidence["ncn_evidence_role"].fillna("established-route")) == {
        "established-route",
        "connector-link",
    }
    network = json.loads(result.artifacts["geojson"].read_text(encoding="utf-8"))
    published_ncn_types = {
        feature["properties"]["feature_type"]
        for feature in network["features"]
        if feature["id"] in {"ncn-fixture", "ncn-link-fixture"}
    }
    assert published_ncn_types == {"ncn-route", "ncn-link"}
    strategic_spines = gpd.read_file(result.artifacts["geopackage"], layer="strategic_spines")
    ncn_spines = strategic_spines[strategic_spines["spine_kind"] == "ncn"]
    assert len(ncn_spines) == 1
    assert json.loads(ncn_spines.iloc[0]["provenance"])["source_ids"] == ["ncn1"]
    assert "ncn-link-fixture" not in ncn_spines.iloc[0]["provenance"]


def test_empty_review_policy_compiles_without_constructing_an_agent_runtime(
    tmp_path: Path,
) -> None:
    config = CouncilConfig.from_yaml(fixture_config(tmp_path))
    config.compilation.agent.review_statuses = ()
    config.compilation.agent.response_mode = "direct-runtime"
    config.compilation.agent.provider = "provider-that-must-not-be-constructed"
    snapshot(config)

    result = compile(config)

    assert result.status == "complete"
    assert result.agent_records
    assert all(record.review_required is False for record in result.agent_records)
    assert all(record.runtime == "not-invoked" for record in result.agent_records)
    run = json.loads(result.artifacts["run"].read_text())
    assert run["agent_review"] == {
        "statuses": [],
        "reviewed_decisions": 0,
        "skipped_decisions": len(result.agent_records),
        "decisions_by_status": {
            "green": {"reviewed": 0, "skipped": len(result.agent_records)},
            "amber": {"reviewed": 0, "skipped": 0},
            "red": {"reviewed": 0, "skipped": 0},
            "grey": {"reviewed": 0, "skipped": 0},
        },
    }
    records = json.loads(result.artifacts["agents"].read_text())["records"]
    assert all(record["governing_status"] == "green" for record in records)
    assert all(record["review_policy"] == [] for record in records)
    assert all(record["review_required"] is False for record in records)


def test_runtime_is_not_constructed_until_a_configured_status_occurs(
    tmp_path: Path,
) -> None:
    config = CouncilConfig.from_yaml(fixture_config(tmp_path))
    config.compilation.agent.provider = "provider-that-must-remain-lazy"
    config.compilation.agent.response_mode = "direct-runtime"
    snapshot(config)

    result = compile(config)

    assert result.status == "complete"
    assert all(record.governing_status == "green" for record in result.agent_records)
    assert all(record.review_required is False for record in result.agent_records)
    assert all(record.runtime == "not-invoked" for record in result.agent_records)


def test_public_compiler_applies_bounded_direct_runtime_choices(tmp_path: Path) -> None:
    config = CouncilConfig.from_yaml(fixture_config(tmp_path))
    config.compilation.agent.response_mode = "direct-runtime"
    config.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    snapshot(config)

    result = compile(config)

    assert result.status == "complete"
    direct_records = [record for record in result.agent_records if record.review_required]
    assert direct_records
    assert all(record.responder_mode == "direct-runtime" for record in direct_records)
    assert all(record.selected_choice_id == "1" for record in direct_records)
    assert all(record.usage == {"requests": 1, "tokens": 1} for record in direct_records)
    assert all(record.attempts == [] for record in direct_records)
    run = json.loads(result.artifacts["run"].read_text())
    governance = run["runtime_governance"]
    assert governance["status"] == "non-production"
    assert governance["reason"] == "fake-runtime"
    assert governance["configured_runtime"] == {
        "provider": "fake",
        "model": None,
        "response_mode": "direct-runtime",
    }
    assert governance["observed_runtime"]["responder_modes"] == ["direct-runtime"]
    assert governance["replay_status"] == "fresh"
    assert governance["usage"]["requests"] == len(direct_records)
    assert governance["usage"]["tokens"] == len(direct_records)


def test_public_direct_runtime_failure_returns_non_publishing_decision_required(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = CouncilConfig.from_yaml(fixture_config(tmp_path))
    config.compilation.agent.response_mode = "direct-runtime"
    config.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    snapshot(config)
    monkeypatch.setattr("satn.pipeline.runtime_for", lambda _config: UnavailableRuntime())

    result = compile(config)

    assert result.status == "decision-required"
    assert result.artifacts == {}
    assert result.metadata["decision_response_validation"] == "runtime-unavailable"
    assert result.decision_requests
    governance = result.metadata["runtime_governance"]
    # The fixture adapter is permanently non-production, including when the
    # direct-runtime invocation itself is incomplete.
    assert governance["status"] == "non-production"
    assert governance["reason"] == "fake-runtime"
    assert governance["promotion"] == {
        "allowed": False,
        "reason": "fake-runtime",
    }


def test_public_compilation_routes_a_configured_red_gap_to_review(tmp_path: Path) -> None:
    config = CouncilConfig.from_yaml(fixture_config(tmp_path))
    assert config.source.fixture_dir is not None
    places_path = config.source.fixture_dir / "places.geojson"
    places = gpd.read_file(places_path)
    places.loc[len(places)] = {
        "place_id": "isolated",
        "name": "Isolated",
        "kind": "community",
        "population": 1000,
        "geometry": Point(-2.8, 51.7),
    }
    places = places.set_crs(4326, allow_override=True)
    places.to_file(places_path, driver="GeoJSON")
    config.compilation.agent.review_statuses = (TrafficLight.RED,)
    snapshot(config)

    result = compile(config)
    request = result.decision_requests[0]

    assert result.status == "decision-required"
    assert result.artifacts == {}
    assert request.status == TrafficLight.RED
    assert request.criterion == "availability"
    assert request.compilation_scope == "network-gap"


def test_public_compilation_routes_a_configured_grey_gap_to_review(tmp_path: Path) -> None:
    config = CouncilConfig.from_yaml(fixture_config(tmp_path))
    assert config.source.fixture_dir is not None
    context_path = config.source.fixture_dir / "context.geojson"
    context = gpd.read_file(context_path)
    school = context["feature_type"] == "school"
    assert school.any()
    context.loc[school, "access_point_status"] = "unresolved"
    context.loc[school, "access_point_source_id"] = None
    context.loc[school, "access_point_rationale"] = "No governed entrance evidence."
    context = context.set_crs(4326, allow_override=True)
    context.to_file(context_path, driver="GeoJSON")
    config.compilation.agent.review_statuses = (TrafficLight.GREY,)
    snapshot(config)

    result = compile(config)
    request = result.decision_requests[0]

    assert result.status == "decision-required"
    assert result.artifacts == {}
    assert request.status == TrafficLight.GREY
    assert request.criterion == "availability"
    assert request.compilation_scope == "network-gap"


def test_public_route_compilation_reviews_only_configured_amber_decisions(
    tmp_path: Path,
) -> None:
    config = CouncilConfig.from_yaml(fixture_config(tmp_path))
    assert config.source.fixture_dir is not None
    source = parallel_spine_source()
    source["boundary"] = gpd.GeoDataFrame(
        [
            {
                "boundary_id": "amber-route-fixture",
                "geometry": Polygon([(-0.01, -0.01), (0.11, -0.01), (0.11, 0.02), (-0.01, 0.02)]),
            }
        ],
        geometry="geometry",
        crs=4326,
    )
    for name in ("boundary", "places", "network", "context"):
        source[name].to_file(config.source.fixture_dir / f"{name}.geojson", driver="GeoJSON")
    config.compilation.max_connection_km = 0.01
    config.compilation.agent.review_statuses = (TrafficLight.AMBER,)
    snapshot(config)

    reviewed = compile(config)
    request = reviewed.decision_requests[0]

    assert reviewed.status == "decision-required"
    assert reviewed.artifacts == {}
    assert request.status == TrafficLight.AMBER
    assert request.criterion == "distance"
    assert request.compilation_scope == "branch-meeting"

    config.compilation.agent.review_statuses = ()
    config.compilation.agent.provider = "provider-that-must-not-be-constructed"
    skipped = compile(config)
    skipped_amber = [
        record for record in skipped.agent_records if record.governing_status == TrafficLight.AMBER
    ]

    assert skipped_amber
    assert all(record.review_required is False for record in skipped_amber)
    assert all(record.runtime == "not-invoked" for record in skipped_amber)
    assert all(record.decision == "accept" for record in skipped_amber)
    meetings = gpd.read_file(skipped.artifacts["geopackage"], layer="branch_meeting_connections")
    assert set(meetings["criterion_distance"]) == {"amber"}


def test_external_cli_snapshot_and_compile(tmp_path: Path) -> None:
    config_path = fixture_config(tmp_path)
    snapshot_run = subprocess.run(
        [str(PROJECT / ".venv" / "bin" / "satn"), "snapshot", str(config_path)],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "fixture-001" in snapshot_run.stdout

    compile_run = subprocess.run(
        [str(PROJECT / ".venv" / "bin" / "satn"), "compile", str(config_path)],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "complete: 2 connections, 0 gaps" in compile_run.stdout
    assert (config_path.parent / "work" / "output" / "review-map.zip").exists()


def test_compile_replaces_the_previous_run(tmp_path: Path) -> None:
    config_path = fixture_config(tmp_path)
    config = CouncilConfig.from_yaml(config_path)
    snapshot(config)
    first = compile(config)
    stale = first.output_dir / "stale.txt"
    stale.write_text("must disappear")

    second = compile(config)

    assert first.run_id == second.run_id
    assert not stale.exists()
