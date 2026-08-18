from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from satn import compile
from satn.models import CouncilConfig
from satn.sources import snapshot

PROJECT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_pages_rendering", PROJECT / "scripts" / "validate_pages_rendering.py"
)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)
validate_pages_rendering = VALIDATOR.validate_pages_rendering


@pytest.mark.browser
def test_packaged_pages_gate_proves_the_strategic_network_renders(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    result = compile(config)

    pages = tmp_path / "pages"
    deployment = pages / "deployments" / "fixture"
    shutil.copytree(result.artifacts["review_map"].parent, deployment)
    catalogue = {
        "schema_version": "satn-deployment-catalogue/v1",
        "deployments": [
            {
                "deployment_id": "fixture",
                "artifacts": {"review_map": "deployments/fixture/index.html"},
            }
        ],
    }
    (pages / "catalogue.json").write_text(json.dumps(catalogue), encoding="utf-8")

    validated = validate_pages_rendering(pages)

    assert len(validated) == 1
    assert validated[0].deployment_id == "fixture"
    assert validated[0].strategic_spines > 0
    assert validated[0].access_connections > 0
    assert validated[0].rendered_strategic_spines > 0


@pytest.mark.browser
def test_packaged_pages_gate_rejects_a_stale_network_projection(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    result = compile(config)

    pages = tmp_path / "pages"
    deployment = pages / "deployments" / "fixture"
    shutil.copytree(result.artifacts["review_map"].parent, deployment)
    network_path = deployment / "network.geojson"
    network = json.loads(network_path.read_text(encoding="utf-8"))
    network["features"] = [
        feature
        for feature in network["features"]
        if feature.get("properties", {}).get("feature_type") != "strategic-spine"
    ]
    network_path.write_text(json.dumps(network), encoding="utf-8")
    data_path = deployment / "data.js"
    data_text = data_path.read_text(encoding="utf-8")
    data_prefix = "window.SATN_DATA = "
    assert data_text.startswith(data_prefix)
    data = json.loads(data_text.removeprefix(data_prefix).rstrip(";\n"))
    if isinstance(data.get("network"), dict):
        data["network"]["features"] = network["features"]
    data_path.write_text(data_prefix + json.dumps(data) + ";\n", encoding="utf-8")
    catalogue = {
        "schema_version": "satn-deployment-catalogue/v1",
        "deployments": [
            {
                "deployment_id": "fixture",
                "artifacts": {"review_map": "deployments/fixture/index.html"},
            }
        ],
    }
    (pages / "catalogue.json").write_text(json.dumps(catalogue), encoding="utf-8")

    with pytest.raises(ValueError, match="published network contains no strategic-spine geometry"):
        validate_pages_rendering(pages)
