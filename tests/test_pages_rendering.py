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


def _package_fixture(tmp_path: Path) -> tuple[Path, Path]:
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
    return pages, deployment


def _remove_network_features(deployment: Path, feature_type: str) -> None:
    network_path = deployment / "network.geojson"
    network = json.loads(network_path.read_text(encoding="utf-8"))
    network["features"] = [
        feature
        for feature in network["features"]
        if feature.get("properties", {}).get("feature_type") != feature_type
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


def _replace_review_map_asset(deployment: Path, needle: str, replacement: str) -> None:
    changed = False
    for asset in (deployment / "assets").glob("review-map*.js"):
        text = asset.read_text(encoding="utf-8")
        if needle in text:
            asset.write_text(text.replace(needle, replacement), encoding="utf-8")
            changed = True
    assert changed


def test_rendering_gate_waits_for_paint_and_rejects_permanent_zero() -> None:
    class _DelayedRenderedPage:
        rendered = 0
        wait_expression = ""

        def wait_for_function(self, expression: str) -> None:
            self.wait_expression = expression
            self.rendered = 187

        def evaluate(self, _expression: str) -> int:
            return self.rendered

    delayed_page = _DelayedRenderedPage()
    assert VALIDATOR._wait_for_rendered_strategic_spines(delayed_page) == 187
    assert "queryRenderedFeatures" in delayed_page.wait_expression

    class _PermanentlyZeroRenderedPage:
        wait_expression = ""

        def wait_for_function(self, expression: str) -> None:
            self.wait_expression = expression
            raise VALIDATOR.PlaywrightTimeoutError("paint never arrived")

        def evaluate(self, _expression: str) -> int:
            return 0

    page = _PermanentlyZeroRenderedPage()
    rendered = VALIDATOR._wait_for_rendered_strategic_spines(page)

    assert rendered == 0
    assert "queryRenderedFeatures" in page.wait_expression
    assert VALIDATOR._rendered_strategic_spines_failure("fixture", rendered) == (
        "fixture strategic-spines has no rendered features after fitting its geometry"
    )


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


@pytest.mark.browser
def test_packaged_pages_gate_requires_urban_strategic_sections(
    tmp_path: Path,
) -> None:
    pages, deployment = _package_fixture(tmp_path)
    network_path = deployment / "network.geojson"
    network = json.loads(network_path.read_text(encoding="utf-8"))
    strategic = next(
        feature
        for feature in network["features"]
        if feature.get("properties", {}).get("feature_type") == "strategic-spine"
    )
    urban = json.loads(json.dumps(strategic))
    urban["id"] = "urban-spine-release-gate-fixture"
    urban["properties"]["feature_type"] = "urban-spine"
    network["features"].append(urban)
    network_path.write_text(json.dumps(network), encoding="utf-8")
    data_path = deployment / "data.js"
    data_prefix = "window.SATN_DATA = "
    data_text = data_path.read_text(encoding="utf-8")
    data = json.loads(data_text.removeprefix(data_prefix).rstrip(";\n"))
    data["network"]["features"].append(urban)
    data_path.write_text(data_prefix + json.dumps(data) + ";\n", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="governed urban spines are missing from the Effective Strategic Network",
    ):
        validate_pages_rendering(pages)

    reviewable = data.get("reviewable_network") or data["reviewable"]
    urban_selected = {
        "type": "Feature",
        "id": "urban-main-road-spine-release-gate-fixture",
        "geometry": urban["geometry"],
        "properties": {
            "feature_type": "reviewable-selected-route",
            "network_role": "urban-main-road-spine",
            "selection_disposition": "selected",
            "display_state": "upgrade-required",
            "intervention_state": "upgrade-required",
        },
    }
    reviewable["features"].append(urban_selected)
    data_path.write_text(data_prefix + json.dumps(data) + ";\n", encoding="utf-8")

    assert validate_pages_rendering(pages)[0].rendered_strategic_spines > 0


@pytest.mark.browser
def test_packaged_pages_gate_rejects_hidden_zero_count_network_layer(tmp_path: Path) -> None:
    pages, deployment = _package_fixture(tmp_path)
    _remove_network_features(deployment, "spine-access-connection")
    needle = (
        'id: "spine-access-connections", type: "line", source: "network", '
        'filter: ["==", ["get", "feature_type"], "spine-access-connection"], '
        'layout: { visibility: hasBackboneAndAccessNetwork ? "visible" : "none" }'
    )
    _replace_review_map_asset(
        deployment,
        needle,
        needle.replace(
            'layout: { visibility: hasBackboneAndAccessNetwork ? "visible" : "none" }',
            'layout: { visibility: "none" }',
        ),
    )

    with pytest.raises(ValueError, match="spine-access-connections layer is hidden"):
        validate_pages_rendering(pages)


def test_packaged_pages_gate_checks_required_connection_detail() -> None:
    validator = (PROJECT / "scripts" / "validate_pages_rendering.py").read_text(encoding="utf-8")
    optional_layers = validator.split("for (const layerId of [", maxsplit=1)[1]
    optional_layers = optional_layers.split("]) {", maxsplit=1)[0]
    assert optional_layers.count('"reviewable-') == 4
    assert '"reviewable-required-connections"' in optional_layers


def test_packaged_pages_gate_checks_default_road_and_cycleway_context() -> None:
    validator = (PROJECT / "scripts" / "validate_pages_rendering.py").read_text(encoding="utf-8")
    assert '["layer-urban-spines", "urban-spines"]' in validator
    assert '["layer-mapped-active-travel-assets", "mapped-active-travel-assets"]' in validator
    assert "defaultEvidenceReady" in validator
    assert "has data but no rendered features" in validator
