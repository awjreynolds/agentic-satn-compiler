"""Fail-closed publication record tests for strategic Reference replay."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace

import geopandas as gpd
import pyogrio
import pytest
from bath_saltford_fixture import configured_bath_saltford
from shapely.affinity import translate
from test_publisher import prepared_config
from test_strategic_reference_application import _resolved_reference_inputs

from satn import compile
from satn import compile_strategic_reference as public_compile_strategic_reference
from satn.agents import FakeAgentRuntime
from satn.pipeline import compile_strategic_reference, compile_strategic_reference_network
from satn.publisher import _validate_artifacts, _validated_strategic_reference_publication
from satn.strategic_reference_application import build_strategic_reference_application_plan
from satn.strategic_reference_publication import (
    StrategicReferencePublicationRecord,
    build_strategic_reference_publication_record,
)


def _record_inputs(tmp_path):
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    plan = build_strategic_reference_application_plan(reference, preparation)
    sha = "a" * 64
    return plan, {
        "replay_diagnostics": {
            "plan_fingerprint": plan.plan_fingerprint,
            "publication_created": False,
            "agent_runtime_invoked": False,
        },
        "area_definition_sha256": sha,
        "snapshot_manifest_sha256": sha,
        "compilation_input_fingerprint": sha,
        "governed_input_fingerprint": sha,
        "compilation_dependency_manifest": {"contract": "test"},
        "decision_contract": "agent-decision-menu/v1",
        "decision_ledger_input": {"responses": []},
        "accepted_decisions": [],
    }


def test_strategic_publication_record_round_trips_and_rejects_tampering(tmp_path) -> None:
    assert public_compile_strategic_reference is compile_strategic_reference
    plan, inputs = _record_inputs(tmp_path)
    record = build_strategic_reference_publication_record(plan=plan, **inputs)

    assert (
        StrategicReferencePublicationRecord.from_publication_payload(record.publication_payload())
        == record
    )

    tampered = record.publication_payload()
    tampered["replay_diagnostics"]["plan_fingerprint"] = "b" * 64
    with pytest.raises(ValueError, match="diagnostics are stale"):
        StrategicReferencePublicationRecord.from_publication_payload(tampered)


def test_strategic_publisher_requires_record_and_replay_frames_together(tmp_path) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    compiled = compile_strategic_reference_network(
        configured_bath_saltford(tmp_path),
        FakeAgentRuntime(),
        build_strategic_reference_application_plan(reference, preparation),
    )
    assert compiled.strategic_reference_publication is None
    compiled.strategic_reference_publication = build_strategic_reference_publication_record(
        plan=build_strategic_reference_application_plan(reference, preparation),
        replay_diagnostics=compiled.strategic_reference_diagnostics,
        area_definition_sha256=compiled.area_definition_sha256,
        snapshot_manifest_sha256=compiled.snapshot_manifest_sha256,
        compilation_input_fingerprint=compiled.compilation_input_fingerprint,
        governed_input_fingerprint=compiled.governed_input_fingerprint,
        compilation_dependency_manifest=compiled.compilation_dependency_manifest,
        decision_contract=compiled.decision_contract,
        decision_ledger_input=compiled.decision_ledger_input,
        accepted_decisions=compiled.accepted_decisions,
    )
    assert (
        _validated_strategic_reference_publication(
            compiled.strategic_reference_publication, compiled
        )
        is not None
    )

    missing_record = replace(compiled, strategic_reference_publication=None)
    with pytest.raises(ValueError, match="must appear together"):
        _validated_strategic_reference_publication(None, missing_record)


def test_bath_strategic_reference_publishes_typed_sibling_and_semantic_map(tmp_path) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    config = configured_bath_saltford(tmp_path)
    result = compile_strategic_reference(
        config,
        build_strategic_reference_application_plan(reference, preparation),
    )

    run = json.loads((result.output_dir / "run.json").read_text())
    network = json.loads((result.output_dir / "network.geojson").read_text())
    data = (result.output_dir / "review-map" / "data.js").read_text()
    html = (result.output_dir / "review-map" / "index.html").read_text()
    assert run["strategic_reference"]["record"]["publication_created"] is False
    assert len(run["strategic_reference"]["authoritative_features"]) == 2
    assert "strategic_reference" in data
    assert "strategic-destination-access-connection" in {
        item["properties"]["feature_type"] for item in network["features"]
    }
    assert "Strategic Reference review" in html
    assert "Independent-travel opportunity is not" in html
    assert "<details>" in html
    assert '<ul class="strategic-population-evidence">' in html
    assert '<li data-radius-m="500"><strong>500 m</strong>: 620 residents;' in html
    assert '<li data-radius-m="500"><strong>500 m</strong>: 820 residents;' in html
    assert '<li data-radius-m="1000"><strong>1 km</strong>: 1420 residents;' in html
    assert 'class="strategic-option strategic-option-rejected"' in html
    assert 'class="strategic-option strategic-option-selected"' in html
    assert "not-preferred-after-criteria-hierarchy" in html
    assert "Existing-alignment assessment: unknown" in html
    assert "accepted finite option: select-candidate-" in html
    assert "independent critique: accepted" in html
    assert "strategic-role-label" in html
    assert "not a safety" in html.lower()
    assert (result.output_dir / "review-map" / "assets" / "strategic-reference.css").is_file()
    assert (result.output_dir / "review-map" / "assets" / "strategic-reference.js").is_file()
    script = (result.output_dir / "review-map" / "assets" / "strategic-reference.js").read_text()
    assert "original_feature_type" in script
    assert 'feature_type = "spine-access-connection"' in script
    assert html.index("strategic-reference.js") < html.rindex("review-map.")
    _validate_artifacts(result.output_dir, config)

    run["strategic_reference"]["record"]["record_fingerprint"] = "0" * 64
    (result.output_dir / "run.json").write_text(json.dumps(run))
    with pytest.raises(ValueError, match="fingerprint is stale"):
        _validate_artifacts(result.output_dir, config)


def test_strategic_data_composite_tamper_is_rejected(tmp_path) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    config = configured_bath_saltford(tmp_path)
    result = compile_strategic_reference(
        config, build_strategic_reference_application_plan(reference, preparation)
    )
    data_path = result.output_dir / "review-map" / "data.js"
    data_path.write_text(data_path.read_text().replace('"interurban-spine"', '"foreign-role"', 1))
    with pytest.raises(ValueError, match="differs from run"):
        _validate_artifacts(result.output_dir, config)


def test_strategic_run_and_top_level_geojson_tampering_are_rejected(tmp_path) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    config = configured_bath_saltford(tmp_path)
    result = compile_strategic_reference(
        config, build_strategic_reference_application_plan(reference, preparation)
    )
    pristine = result.output_dir
    for name, mutate in (
        ("run", "run"),
        ("geojson", "geojson"),
        ("spine-geometry", "spine-geometry"),
        ("gpkg", "gpkg"),
    ):
        target = tmp_path / name
        shutil.copytree(pristine, target)
        if mutate == "run":
            path = target / "run.json"
            payload = json.loads(path.read_text())
            payload["strategic_reference"]["replay"]["interurban_connections"]["features"][0][
                "properties"
            ]["candidate_id"] = "tampered"
        elif mutate == "geojson":
            path = target / "network.geojson"
            payload = json.loads(path.read_text())
            feature = next(
                item
                for item in payload["features"]
                if item["properties"].get("feature_type")
                == "strategic-destination-access-connection"
            )
            feature["properties"]["binding_id"] = "tampered"
        elif mutate == "spine-geometry":
            path = target / "network.geojson"
            payload = json.loads(path.read_text())
            feature = next(
                item
                for item in payload["features"]
                if item["properties"].get("feature_type") == "strategic-spine"
                and item["properties"].get("replay_binding_ids")
            )
            feature["geometry"]["coordinates"][0][0] += 0.001
        else:
            path = target / "network.gpkg"
            layer = "strategic_destination_access_connections"
            destinations = gpd.read_file(path, layer=layer)
            destinations.geometry = destinations.geometry.map(
                lambda geometry: translate(geometry, xoff=100)
            )
            pyogrio.write_dataframe(
                destinations,
                path,
                layer=layer,
                driver="GPKG",
                append=False,
            )
            with pytest.raises(ValueError, match="destination geometry differs"):
                _validate_artifacts(target, config)
            continue
        path.write_text(json.dumps(payload))
        with pytest.raises(ValueError):
            _validate_artifacts(target, config)


def test_ordinary_publication_has_no_strategic_sibling_or_assets(tmp_path) -> None:
    config = prepared_config(tmp_path)
    result = compile(config)

    run = json.loads((result.output_dir / "run.json").read_text())
    network = json.loads((result.output_dir / "network.geojson").read_text())
    assets = result.output_dir / "review-map" / "assets"

    assert "strategic_reference" not in run
    assert "strategic-destination-access-connection" not in {
        feature["properties"].get("feature_type") for feature in network["features"]
    }
    assert not (assets / "strategic-reference.css").exists()
    assert not (assets / "strategic-reference.js").exists()
    assert "strategic_destination_access_connections" not in set(
        gpd.list_layers(result.output_dir / "network.gpkg")["name"]
    )
    _validate_artifacts(result.output_dir, config)


def test_atomic_failure_preserves_prior_strategic_publication(tmp_path, monkeypatch) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    config = configured_bath_saltford(tmp_path)
    plan = build_strategic_reference_application_plan(reference, preparation)
    result = compile_strategic_reference(config, plan)
    before = {
        path.relative_to(result.output_dir): path.read_bytes()
        for path in result.output_dir.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(
        "satn.publisher._validate_artifacts",
        lambda *_: (_ for _ in ()).throw(ValueError("forced validation failure")),
    )
    with pytest.raises(ValueError, match="forced validation failure"):
        compile_strategic_reference(config, plan)
    after = {
        path.relative_to(result.output_dir): path.read_bytes()
        for path in result.output_dir.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_strategic_publication_never_enters_agent_runtime(tmp_path, monkeypatch) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    monkeypatch.setattr(
        "satn.pipeline.runtime_for",
        lambda *_: (_ for _ in ()).throw(AssertionError("runtime entered")),
    )
    result = compile_strategic_reference(
        configured_bath_saltford(tmp_path),
        build_strategic_reference_application_plan(reference, preparation),
    )
    assert result.artifacts["run"].is_file()
