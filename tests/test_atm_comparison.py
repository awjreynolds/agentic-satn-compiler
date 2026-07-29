from __future__ import annotations

import json
import shutil
from pathlib import Path

import geopandas as gpd
import pyogrio
from shapely.geometry import LineString

from satn import compile
from satn.agents import FakeAgentRuntime
from satn.atm import compare_atm
from satn.compiler import compile_network
from satn.models import AgentDecisionLedger, AgentDecisionResponse, CouncilConfig
from satn.sources import load_snapshot, snapshot

PROJECT = Path(__file__).parents[1]


def prepared(tmp_path: Path) -> tuple[CouncilConfig, Path]:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    atm_path = fixture / "atm.geojson"
    gpd.GeoDataFrame(
        [
            {
                "portal_feature_id": "atm-match",
                "geometry": LineString([(-2.5, 51.4), (-2.482, 51.412), (-2.46, 51.42)]),
            },
            {
                "portal_feature_id": "atm-omission",
                "geometry": LineString([(-2.6, 51.3), (-2.59, 51.31)]),
            },
        ],
        crs=4326,
    ).to_file(atm_path, driver="GeoJSON")
    config.atm.enabled = True
    config.compilation.agent.review_statuses = ()
    config.atm.path = atm_path
    config.atm.mode = "blind"
    snapshot(config)
    return config, atm_path


def test_blind_comparison_is_independent_and_publication_is_lawful(tmp_path: Path) -> None:
    config, _ = prepared(tmp_path)
    config.publication.audience = "public"
    config.atm.redistribution_permitted = False

    result = compile(config)

    assert result.metadata["atm_mode"] == "blind"
    assert result.metadata["atm_geometry_included"] is False
    assert result.metadata["divergence_counts"] == {
        "match": 2,
        "omission": 1,
    }
    network = json.loads(result.artifacts["geojson"].read_text())
    assert "atm-reference" not in {f["properties"]["feature_type"] for f in network["features"]}
    assert "atm_reference" not in set(pyogrio.list_layers(result.artifacts["geopackage"])[:, 0])
    divergence = json.loads(result.artifacts["divergences"].read_text())
    assert {record["status"] for record in divergence["records"]} == {"match", "omission"}
    assert all(
        record["review_required"] is False
        for record in divergence["records"]
        if record["governing_status"] == "green"
    )
    assert all(record["review_required"] is False for record in divergence["records"])
    assert "connections" not in set(pyogrio.list_layers(result.artifacts["geopackage"])[:, 0])


def test_seeded_reference_cannot_replace_the_authoritative_backbone(tmp_path: Path) -> None:
    config, _ = prepared(tmp_path)
    blind = compile(config)
    config.atm.mode = "seeded"

    seeded = compile(config)

    assert "cache" not in blind.metadata
    assert "cache" not in seeded.metadata
    assert blind.run_id != seeded.run_id
    access = gpd.read_file(seeded.artifacts["geopackage"], layer="spine_access_connections")
    assert not access["selection_reason"].str.startswith("ATM-seeded").any()


def test_local_or_permitted_output_can_include_the_atm_overlay(tmp_path: Path) -> None:
    config, atm_path = prepared(tmp_path)
    config.publication.audience = "local"
    atm = gpd.read_file(atm_path)
    atm["fid"] = [101, 102]
    atm.loc[len(atm)] = {
        "portal_feature_id": "atm-null",
        "fid": 103,
        "geometry": None,
    }
    atm = atm.set_crs(4326, allow_override=True)
    atm.to_file(atm_path, driver="GeoJSON")

    local = compile(config)

    assert local.metadata["atm_geometry_included"] is True
    assert "atm_reference" in set(pyogrio.list_layers(local.artifacts["geopackage"])[:, 0])
    published_atm = gpd.read_file(local.artifacts["geopackage"], layer="atm_reference")
    assert list(published_atm["source_fid"]) == [101, 102, 103]
    network = json.loads(local.artifacts["geojson"].read_text())
    assert "atm-reference" in {f["properties"]["feature_type"] for f in network["features"]}


def test_divergence_statuses_cover_deviation_and_addition(tmp_path: Path) -> None:
    config, _ = prepared(tmp_path)
    config.compilation.agent.review_statuses = ("amber",)
    config.compilation.agent.response_mode = "direct-runtime"
    source = load_snapshot(config)
    compiled = compile_network(config, source, FakeAgentRuntime())
    route = compiled.spine_access_connections.to_crs(27700)
    route = route[route.length > 0].iloc[0].geometry
    midpoint = route.interpolate(0.5, normalized=True)
    partial = LineString([route.coords[0], (midpoint.x, midpoint.y)])
    partial_atm = gpd.GeoDataFrame(
        [{"portal_feature_id": "partial", "geometry": partial}], crs=27700
    )

    deviation = compare_atm(compiled, partial_atm, FakeAgentRuntime(), config)
    far_atm = gpd.GeoDataFrame(
        [
            {
                "portal_feature_id": "far",
                "geometry": LineString([(500000, 100000), (501000, 100000)]),
            }
        ],
        crs=27700,
    )
    addition = compare_atm(compiled, far_atm, FakeAgentRuntime(), config)

    assert "deviation" in {record.status for record in deviation}
    assert {record.status for record in addition} == {"addition", "omission"}
    assert all(record.responder_mode == "direct-runtime" for record in [*deviation, *addition])
    assert all(record.selected_choice_id == "1" for record in [*deviation, *addition])
    assert all(record.resolution_attempts == [] for record in [*deviation, *addition])


def test_empty_review_policy_handles_atm_decisions_without_an_agent_runtime(
    tmp_path: Path,
) -> None:
    config, _ = prepared(tmp_path)
    config.compilation.agent.review_statuses = ()
    config.compilation.agent.provider = "provider-that-must-not-be-constructed"

    result = compile(config)
    records = json.loads(result.artifacts["divergences"].read_text())["records"]
    run = json.loads(result.artifacts["run"].read_text())

    assert records
    assert all(record["review_policy"] == [] for record in records)
    assert all(record["review_required"] is False for record in records)
    assert all(record["resolution_attempts"] == [] for record in records)
    assert all("skipped by policy" in record["explanation"] for record in records)
    assert run["agent_review"]["reviewed_decisions"] == 0
    assert run["agent_review"]["skipped_decisions"] == len(result.agent_records) + len(records)


def test_caller_mediated_atm_review_returns_the_same_bounded_request_shape(
    tmp_path: Path,
) -> None:
    config, _ = prepared(tmp_path)
    config.compilation.agent.review_statuses = ("amber",)

    result = compile(config)

    assert result.status == "decision-required"
    assert result.artifacts == {}
    request = result.decision_requests[0]
    assert request.compilation_scope == "atm-comparison"
    assert request.criterion == "atm-geometry-comparison"
    assert request.status == "amber"
    assert request.deterministic_findings
    assert [choice.choice_id for choice in request.choices] == ["1", "terminate"]


def test_public_direct_runtime_applies_the_same_bounded_atm_choice(tmp_path: Path) -> None:
    config, _ = prepared(tmp_path)
    config.compilation.agent.response_mode = "direct-runtime"
    config.compilation.agent.review_statuses = ("amber",)

    result = compile(config)

    assert result.status == "complete"
    direct_records = [
        record
        for record in result.divergence_records
        if record.review_required
    ]
    assert direct_records
    assert all(record.responder_mode == "direct-runtime" for record in direct_records)
    assert all(record.runtime == "fake" for record in direct_records)
    assert all(record.model == "deterministic-choices-v1" for record in direct_records)
    assert all(record.usage == {"requests": 1, "tokens": 1} for record in direct_records)
    assert all(record.selected_choice_id == "1" for record in direct_records)


def test_atm_choices_replay_to_a_complete_published_divergence_audit(
    tmp_path: Path,
) -> None:
    config, _ = prepared(tmp_path)
    config.compilation.agent.review_statuses = ("amber",)
    responses: list[AgentDecisionResponse] = []
    for _ in range(10):
        result = compile(
            config,
            decision_ledger=AgentDecisionLedger(responses=tuple(responses)),
        )
        if result.status == "complete":
            break
        assert result.status == "decision-required"
        request = result.decision_requests[0]
        responses.append(
            AgentDecisionResponse(
                request_id=request.request_id,
                dependency_fingerprint=request.dependency_fingerprint,
                choice_id="1",
            )
        )
    else:
        raise AssertionError("ATM replay did not complete within its bounded decision count")

    caller_records = [
        record for record in result.divergence_records if record.responder_mode == "caller"
    ]
    assert caller_records
    assert all(
        record.mapped_action is not None
        and record.mapped_action.kind == "retain-atm-comparison"
        for record in caller_records
    )
    run = json.loads(result.artifacts["run"].read_text(encoding="utf-8"))
    assert run["accepted_decisions"] == [
        {
            "request_id": record.decision_request.request_id,
            "dependency_fingerprint": record.decision_request.dependency_fingerprint,
            "choice_id": record.selected_choice_id,
        }
        for record in caller_records
        if record.decision_request is not None
    ]
    published = json.loads(result.artifacts["divergences"].read_text(encoding="utf-8"))
    assert sum(record["responder_mode"] == "caller" for record in published["records"]) == len(
        caller_records
    )
