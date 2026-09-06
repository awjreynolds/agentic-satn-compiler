from __future__ import annotations

import json
import shutil
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from bath_saltford_fixture import configured_bath_saltford
from playwright.sync_api import sync_playwright
from shapely.geometry import Point

from satn import compile
from satn.agents import FakeAgentRuntime
from satn.compiler import compile_network
from satn.filesystem_safety import publication_destination_authority
from satn.models import CouncilConfig
from satn.publisher import _write_review_map
from satn.sources import load_snapshot, snapshot

PROJECT = Path(__file__).parents[1]


@pytest.mark.browser
def test_strategic_main_network_is_the_rendered_default(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    result = compile(config)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(result.artifacts["review_map"].as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")
        page.wait_for_function("!window.SATN_REVIEW_MAP.isMoving()")

        state = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              const network = map.getSource("network")._data.features;
              return {
                mainFeatures: network.filter(
                  (feature) => feature.properties?.feature_type === "strategic-spine"
                ).length,
                supportFeatures: network.filter(
                  (feature) => feature.properties?.feature_type === "spine-access-connection"
                ).length,
                mainVisible: map.getLayoutProperty(
                  "strategic-spines", "visibility"
                ) !== "none",
                supportVisible: map.getLayoutProperty(
                  "spine-access-connections", "visibility"
                ) !== "none",
              };
            }"""
        )

        assert state["mainFeatures"] > 0
        assert state["supportFeatures"] > 0
        assert state["mainVisible"]
        assert not state["supportVisible"]
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.queryRenderedFeatures("
            "{layers: ['strategic-spines']})"
            ".length > 0"
        )
        browser.close()


@pytest.mark.browser
def test_mobile_map_has_a_visible_compact_legend(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    result = compile(config)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(result.artifacts["review_map"].as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")
        page.locator("#map").scroll_into_view_if_needed()

        legend = page.get_by_role("region", name="Map legend")
        assert legend.is_visible()
        assert legend.get_attribute("open") is None
        page.get_by_text("Map legend", exact=True).click()
        assert legend.get_attribute("open") is not None
        legend_text = legend.inner_text()
        assert all(
            label in legend_text
            for label in (
                "Strategic spine",
                "Access connection",
                "Cross-spine connector",
                "Urban through-road",
                "Candidate low-traffic area",
                "Served community",
                "Place reference",
                "Network gap",
                "Crossing warning",
            )
        )
        map_box = page.locator("#map").bounding_box()
        legend_box = legend.bounding_box()
        assert map_box is not None
        assert legend_box is not None
        assert legend_box["x"] >= map_box["x"]
        assert legend_box["y"] >= map_box["y"]
        assert legend_box["x"] + legend_box["width"] <= map_box["x"] + map_box["width"]
        assert legend_box["width"] <= 300
        browser.close()


@pytest.mark.browser
def test_any_visible_map_artifact_can_pin_its_context(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    result = compile(config)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(result.artifacts["review_map"].as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")
        page.wait_for_function("!window.SATN_REVIEW_MAP.isMoving()")

        selection = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              map.getStyle().layers
                .filter((layer) => layer.source && layer.source !== "places")
                .forEach((layer) => {
                  map.setLayoutProperty(layer.id, "visibility", "none");
                });
              const places = map.getSource("places")._data.features;
              for (const feature of places) {
                const point = map.project(feature.geometry.coordinates);
                const rendered = map.queryRenderedFeatures(point, {layers: ["places"]});
                if (rendered.length) {
                      return {
                        x: point.x,
                        y: point.y,
                        id: feature.properties.place_id,
                        name: feature.properties.name,
                        kind: feature.properties.kind,
                        geometry: feature.geometry
                      };
                }
              }
              return null;
            }"""
        )
        assert selection is not None
        map_box = page.locator("#map").bounding_box()
        assert map_box is not None
        artifact_x = map_box["x"] + selection["x"]
        artifact_y = map_box["y"] + selection["y"]
        lens = page.locator("#review-lens")
        panel = page.locator("#feature-details")
        main_before_hover = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              return {
                source: JSON.stringify(map.getSource("network")._data),
                filter: JSON.stringify(map.getFilter("strategic-spines")),
                paint: JSON.stringify(map.getPaintProperty("strategic-spines", "line-color")),
                visibility: map.getLayoutProperty("strategic-spines", "visibility")
              };
            }"""
        )

        page.mouse.move(artifact_x, artifact_y)
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.getSource('review-lens-highlight')._data.features.length === 1"
        )
        assert not page_errors
        assert lens.is_visible()
        assert (
            page.evaluate(
                """() => {
              const map = window.SATN_REVIEW_MAP;
              return map.getSource("review-lens-highlight")._data.features[0].geometry;
            }"""
            )
            == selection["geometry"]
        )
        assert (
            page.evaluate(
                """() => {
              const map = window.SATN_REVIEW_MAP;
              return {
                source: JSON.stringify(map.getSource("network")._data),
                filter: JSON.stringify(map.getFilter("strategic-spines")),
                paint: JSON.stringify(map.getPaintProperty("strategic-spines", "line-color")),
                visibility: map.getLayoutProperty("strategic-spines", "visibility")
              };
            }"""
            )
            == main_before_hover
        )
        hover_text = panel.inner_text()
        assert selection["name"] in hover_text
        assert "Role: Named community reference" in hover_text
        assert "Purpose: Named place shown for orientation." in hover_text
        assert "Access status" not in hover_text
        assert "Unavailable" not in hover_text
        assert "All contextual properties" not in hover_text
        assert panel.locator(".artifact-context").count() == 0

        page.mouse.move(map_box["x"] + 2, map_box["y"] + 2)
        assert lens.is_hidden()
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.getSource('review-lens-highlight')._data.features.length === 0"
        )

        page.mouse.move(artifact_x, artifact_y)
        page.mouse.click(artifact_x, artifact_y)
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.getSource('review-lens-highlight')._data.features.length === 1"
        )
        page.mouse.move(map_box["x"] + 2, map_box["y"] + 2)
        assert lens.is_visible()

        assert not page_errors
        panel_text = panel.inner_text()
        assert selection["name"] in panel_text
        assert "All contextual properties" in panel_text
        panel.locator(".artifact-context summary").evaluate("(element) => element.click()")
        panel_text = panel.inner_text()
        assert selection["id"] in panel_text
        assert selection["kind"] in panel_text
        assert "places" in panel_text
        assert "Point" in panel_text

        page.get_by_role("button", name="Close route review lens").click()
        assert lens.is_hidden()
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.getSource('review-lens-highlight')._data.features.length === 0"
        )

        page.locator("#feature-index summary").click()
        indexed_artifact = page.locator("#connection-list .connection").first
        indexed_artifact.focus()
        assert lens.is_visible()
        assert panel.locator(".artifact-context").count() == 0
        indexed_artifact.click()
        assert lens.get_attribute("data-state") == "pinned"
        panel.locator(".artifact-context summary").evaluate("(element) => element.click()")
        assert indexed_artifact.get_attribute("data-feature-id") in panel.inner_text()
        page.get_by_role("button", name="Close route review lens").click()
        assert lens.is_hidden()

        route_preview = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              map.setLayoutProperty('places', 'visibility', 'none');
              const feature = map.getSource('network')._data.features.find(
                (candidate) => candidate.geometry?.type === 'LineString' &&
                  candidate.properties.feature_type === 'ncn-route'
              );
              map.addLayer({
                id: 'network-preview-test', type: 'line', source: 'network',
                filter: ['==', ['get', 'feature_type'], 'ncn-route'],
                paint: {'line-color': '#17202a', 'line-width': 14}
              });
              const coordinates = feature.geometry.coordinates;
              const point = map.project(coordinates[Math.floor(coordinates.length / 2)]);
            return {
                x: point.x, y: point.y, id: feature.id,
                geometry: feature.geometry,
                name: feature.properties.name || feature.properties.feature_type
              };
            }"""
        )
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.queryRenderedFeatures("
            "{layers: ['network-preview-test']}).length > 0"
        )
        page.mouse.move(
            map_box["x"] + route_preview["x"],
            map_box["y"] + route_preview["y"],
        )
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.getSource('review-lens-highlight')._data.features.length === 1"
        )
        route_preview_text = lens.inner_text()
        assert route_preview["name"] in route_preview_text
        assert "Role: Strategic Main Network structural route" in route_preview_text
        assert "Purpose: Part of the proposed main network." in route_preview_text
        assert panel.locator(".artifact-context").count() == 0
        assert panel.locator(".artifact-appearance").count() == 0
        assert (
            page.evaluate(
                """() => window.SATN_REVIEW_MAP.getSource(
              "review-lens-highlight"
            )._data.features[0].geometry"""
            )
            == route_preview["geometry"]
        )
        page.mouse.click(
            map_box["x"] + route_preview["x"],
            map_box["y"] + route_preview["y"],
        )
        route_pinned_text = panel.inner_text()
        assert panel.locator(".artifact-context").count() == 1
        assert panel.locator(".artifact-appearance").count() == 1
        lens.locator(".artifact-appearance summary").evaluate("(element) => element.click()")
        lens.locator(".artifact-context summary").evaluate("(element) => element.click()")
        route_pinned_text = lens.inner_text()
        assert route_preview["id"] in route_pinned_text
        assert "Line colour" in route_pinned_text
        assert "Teal" in route_pinned_text
        assert "Map meaning" in route_pinned_text
        assert "Selected Strategic Spine" in route_pinned_text
        assert "Not available" not in route_pinned_text
        page.get_by_role("button", name="Close route review lens").click()
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.getSource('review-lens-highlight')._data.features.length === 0"
        )
        page.mouse.move(map_box["x"] + 2, map_box["y"] + 2)
        assert lens.is_hidden()

        reference_selection = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              map.setLayoutProperty("places", "visibility", "none");
              const center = map.getCenter();
              const feature = {
                type: "Feature",
                id: "reference-option-test",
                properties: {
                  option_id: "reference-option-test",
                  name: "Reference option test",
                  disposition: "complementary",
                  rationale: "Retained as contextual reference evidence."
                },
                geometry: {
                  type: "LineString",
                  coordinates: [
                    [center.lng - 0.001, center.lat],
                    [center.lng + 0.001, center.lat]
                  ]
                }
              };
              const collection = {type: "FeatureCollection", features: [feature]};
              if (map.getSource("reference-satn-options")) {
                map.getSource("reference-satn-options").setData(collection);
                map.setLayoutProperty(
                  "reference-satn-options",
                  "visibility",
                  "visible"
                );
              } else {
                map.addSource("reference-satn-options", {
                  type: "geojson",
                  data: collection
                });
                map.addLayer({
                  id: "reference-satn-options",
                  type: "line",
                  source: "reference-satn-options",
                  paint: {"line-color": "#c0392b", "line-width": 12}
                });
              }
              const point = map.project(center);
              return {x: point.x, y: point.y};
            }"""
        )
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.queryRenderedFeatures("
            "{layers: ['reference-satn-options']}).length > 0"
        )
        reference_x = map_box["x"] + reference_selection["x"]
        reference_y = map_box["y"] + reference_selection["y"]
        page.mouse.move(reference_x, reference_y)
        page.mouse.click(reference_x, reference_y)

        reference_text = panel.inner_text()
        assert "Reference option test" in reference_text
        assert "Complementary" in reference_text
        assert "Retained as contextual reference evidence." in reference_text
        panel.locator(".artifact-context summary").evaluate("(element) => element.click()")
        reference_text = panel.inner_text()
        assert "reference-option-test" in reference_text
        assert "reference-satn-options" in reference_text
        page.get_by_role("button", name="Close route review lens").click()

        served_route = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              const places = map.getSource("places")._data.features;
              const [first, second] = places.slice(0, 2);
              const firstId = first.properties.place_id;
              const secondId = second.properties.place_id;
              const feature = {
                type: "Feature",
                id: "served-route-test",
                properties: {
                  layer: "Strategic Main Network",
                  feature_type: "reviewable-selected-route",
                  section_id: "served-route-test",
                  network_role: "strategic-main-network",
                  display_state: "proposed-new-link",
                  served_network_place_ids: [secondId, firstId],
                  endpoints: [firstId, secondId]
                },
                geometry: {
                  type: "LineString",
                  coordinates: [
                    first.geometry.coordinates,
                    second.geometry.coordinates
                  ]
                }
              };
              const collection = map.getSource("reviewable")._data;
              collection.features.push(feature);
              map.getSource("reviewable").setData(collection);
              map.getStyle().layers
                .filter((layer) => layer.source && layer.id !== "served-route-test")
                .forEach((layer) => map.setLayoutProperty(layer.id, "visibility", "none"));
              map.addLayer({
                id: "served-route-test",
                type: "line",
                source: "reviewable",
                filter: ["==", ["get", "section_id"], "served-route-test"],
                paint: {"line-color": "#17202a", "line-width": 14}
              });
              const midpoint = first.geometry.coordinates.map(
                (value, index) => (value + second.geometry.coordinates[index]) / 2
              );
              const point = map.project(midpoint);
              return {
                x: point.x,
                y: point.y,
                firstName: first.properties.name,
                secondName: second.properties.name
              };
            }"""
        )
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.queryRenderedFeatures("
            "{layers: ['served-route-test']}).length > 0"
        )
        served_x = map_box["x"] + served_route["x"]
        served_y = map_box["y"] + served_route["y"]
        page.mouse.move(served_x, served_y)
        assert panel.get_by_role("heading", name="Strategic route section").is_visible()
        assert panel.locator(".artifact-context").count() == 0
        page.mouse.click(served_x, served_y)
        served_text = panel.inner_text()
        assert "Places served" in served_text
        assert "Endpoints" in served_text
        assert "Selection reason" in served_text
        assert "Not recorded" in served_text
        assert served_route["firstName"] in served_text
        assert served_route["secondName"] in served_text
        served_value = page.evaluate(
            """() => [...document.querySelectorAll("#feature-details dt")]
              .find((item) => item.textContent === "Places served")
              ?.nextElementSibling?.textContent || ''
            """
        )
        assert "," in served_value
        assert " → " not in served_value
        page.get_by_role("button", name="Close route review lens").click()
        browser.close()


@pytest.mark.browser
def test_compact_review_lens_previews_pins_and_closes_map_artifacts(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    result = compile(config)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(result.artifacts["review_map"].as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")
        page.wait_for_function("!window.SATN_REVIEW_MAP.isMoving()")

        selections = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              map.getStyle().layers
                .filter((layer) => layer.source && layer.source !== "places")
                .forEach((layer) => {
                  map.setLayoutProperty(layer.id, "visibility", "none");
                });
              return map.getSource("places")._data.features
                .map((feature) => {
                  const point = map.project(feature.geometry.coordinates);
                  const rendered = map.queryRenderedFeatures(point, {layers: ["places"]});
                  return rendered.length
                    ? {
                        x: point.x,
                        y: point.y,
                        id: feature.properties.place_id,
                        name: feature.properties.name
                      }
                    : null;
                })
                .filter(Boolean)
                .slice(0, 2);
            }"""
        )
        assert len(selections) == 2
        first, second = selections
        map_box = page.locator("#map").bounding_box()
        assert map_box is not None

        def map_point(selection: dict[str, object]) -> tuple[float, float]:
            return (
                map_box["x"] + float(selection["x"]),
                map_box["y"] + float(selection["y"]),
            )

        first_x, first_y = map_point(first)
        second_x, second_y = map_point(second)
        empty_x, empty_y = map_box["x"] + 2, map_box["y"] + 2
        lens = page.get_by_role("region", name="Route review lens")
        panel = page.locator("#feature-details")

        assert page.locator("#linear-evidence-panel").count() == 0
        assert lens.is_hidden()

        page.mouse.move(first_x, first_y)
        assert lens.is_visible()
        assert lens.get_attribute("data-state") == "preview"
        first_preview = lens.inner_text()
        assert panel.locator(".artifact-context").count() == 0
        assert panel.locator(".artifact-appearance").count() == 0

        page.mouse.click(first_x, first_y)
        assert lens.get_attribute("data-state") == "pinned"
        assert panel.locator(".artifact-context").count() == 1
        panel.locator(".artifact-context summary").evaluate("(element) => element.click()")
        assert first["id"] in panel.inner_text()
        page.mouse.move(second_x, second_y)
        assert lens.get_attribute("data-state") == "pinned"
        pinned_during_second_hover = lens.inner_text()

        page.mouse.click(empty_x, empty_y)
        assert lens.is_hidden()

        page.mouse.move(second_x, second_y)
        assert lens.is_visible()
        second_preview_after_unlock = lens.inner_text()
        assert panel.locator(".artifact-context").count() == 0
        assert panel.locator(".artifact-appearance").count() == 0

        failures = []
        if first["name"] not in first_preview:
            failures.append("hovering the first artifact did not preview it in the lens")
        if (
            first["name"] not in pinned_during_second_hover
            or second["name"] in pinned_during_second_hover
        ):
            failures.append("hovering another artifact replaced the pinned lens")
        if (
            second["name"] not in second_preview_after_unlock
            or first["name"] in second_preview_after_unlock
        ):
            failures.append("the lens did not resume preview after being closed")
        assert not failures, "\n".join(failures)

        browser.close()


@pytest.mark.browser
def test_selecting_a_layer_only_changes_map_visibility(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    result = compile(config)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(result.artifacts["review_map"].as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")

        page.get_by_text("Urban and low-traffic context", exact=True).click()
        control = page.locator("#layer-authority-boundaries")
        information = page.get_by_role("button", name="About authority boundaries")
        popover = page.locator("#legend-authority-boundaries")
        lens = page.get_by_role("region", name="Route review lens")
        map_legend = page.get_by_role("region", name="Map legend")

        assert not control.is_checked()
        assert popover.is_hidden()
        assert lens.is_hidden()
        assert map_legend.get_attribute("open") is None

        control.check()

        assert (
            page.evaluate(
                "window.SATN_REVIEW_MAP.getLayoutProperty('authority-boundaries', 'visibility')"
            )
            == "visible"
        )
        assert popover.is_hidden()
        assert lens.is_hidden()
        assert map_legend.get_attribute("open") is None

        information.click()
        assert popover.is_visible()
        assert information.get_attribute("aria-expanded") == "true"

        information.click()
        page.get_by_text("Terrain, warnings and comparison", exact=True).click()
        page.locator("#layer-population-display-sections").check()
        assert map_legend.get_attribute("open") is None

        page.locator("#layer-reviewable-gaps").check()
        findings = page.locator("#reviewable-findings")
        if findings.locator(".finding-button").count():
            assert findings.evaluate("element => element.tagName") == "DETAILS"
            assert findings.get_attribute("open") is None
        browser.close()


@pytest.mark.browser
def test_mesh_gap_marker_has_a_coverage_summary_and_main_layer_role(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    result = compile(config)

    review_map = result.artifacts["review_map"]
    data_path = review_map.parent / "data.js"
    data_prefix = "window.SATN_DATA = "
    data_source = data_path.read_text(encoding="utf-8")
    assert data_source.startswith(data_prefix)
    data = json.loads(data_source[len(data_prefix) :].rstrip(";\n"))
    coordinates = data["places"]["features"][0]["geometry"]["coordinates"]
    mesh_coordinates = [coordinates[0] + 0.01, coordinates[1] + 0.01]
    mesh_marker = {
        "type": "Feature",
        "id": "reviewable-gap:mesh-coverage:proof-point-1",
        "geometry": {"type": "Point", "coordinates": mesh_coordinates},
        "properties": {
            "layer": "Strategic Main Network",
            "feature_type": "reviewable-gap-endpoint",
            "gap_id": "mesh-coverage",
            "obligation_id": "mesh-coverage",
            "endpoint_id": None,
            "network_role": "strategic-main-network",
            "display_state": "unresolved-gap",
            "proof_point_position": 1,
            "geometry_semantics": "mesh-proof-point-marker-only-no-route-geometry",
            "reason": "coverage proof point",
            "missing_endpoint_geometry": False,
        },
    }
    for collection_key in ("reviewable", "reviewable_network"):
        data[collection_key]["features"].append(mesh_marker)
    data_path.write_text(
        data_prefix + json.dumps(data) + ";\n",
        encoding="utf-8",
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(review_map.as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")

        page.wait_for_function("!window.SATN_REVIEW_MAP.isMoving()")
        page.locator("#layer-reviewable-gaps").check()
        page.locator("#reviewable-findings summary").click()
        page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              map.getStyle().layers
                .filter((layer) => layer.source && layer.id !== "reviewable-gaps")
                .forEach((layer) => map.setLayoutProperty(layer.id, "visibility", "none"));
            }"""
        )
        marker_point = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              const feature = map.getSource("reviewable")._data.features.find(
                (item) => item.id === "reviewable-gap:mesh-coverage:proof-point-1"
              );
              const point = map.project(feature.geometry.coordinates);
              return {x: point.x, y: point.y};
            }"""
        )
        page.wait_for_function(
            """(point) => window.SATN_REVIEW_MAP.queryRenderedFeatures(
              point, {layers: ["reviewable-gaps"]}
            ).length > 0""",
            arg=marker_point,
        )
        map_box = page.locator("#map").bounding_box()
        assert map_box is not None
        page.mouse.move(
            map_box["x"] + marker_point["x"],
            map_box["y"] + marker_point["y"],
        )
        page.wait_for_function(
            "document.querySelector('#feature-details').innerText.includes('Network coverage gap')"
        )
        hover_panel_text = page.locator("#feature-details").inner_text()
        assert "Network coverage gap" in hover_panel_text
        assert "Recorded reason: coverage proof point" in hover_panel_text
        assert "Route role" not in hover_panel_text

        finding = page.locator(".finding-button").filter(has_text="mesh-coverage")
        assert finding.count() == 1
        finding_text = finding.inner_text()
        assert "coverage point 1" in finding_text
        assert "coverage marker" in finding_text
        assert "unknown endpoint" not in finding_text

        finding.click()
        panel_text = page.locator("#feature-details").inner_text()
        assert "Network coverage gap" in panel_text
        assert "Recorded reason: coverage proof point" in panel_text
        assert "Route role" not in panel_text
        assert "Strategic Main Network structural route" not in panel_text
        assert "Access Support" not in panel_text
        browser.close()


@pytest.mark.browser
def test_selected_main_component_marker_has_representative_summary(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    result = compile(config)

    review_map = result.artifacts["review_map"]
    data_path = review_map.parent / "data.js"
    data_prefix = "window.SATN_DATA = "
    data_source = data_path.read_text(encoding="utf-8")
    assert data_source.startswith(data_prefix)
    data = json.loads(data_source[len(data_prefix) :].rstrip(";\n"))
    coordinates = data["places"]["features"][0]["geometry"]["coordinates"]
    marker = {
        "type": "Feature",
        "id": "reviewable-gap:selected-main-component:representative-point",
        "geometry": {
            "type": "Point",
            "coordinates": [coordinates[0] + 0.01, coordinates[1] + 0.01],
        },
        "properties": {
            "layer": "Strategic Main Network",
            "feature_type": "reviewable-gap-endpoint",
            "gap_id": "selected-main-component",
            "obligation_id": "selected-main-component",
            "endpoint_id": None,
            "network_role": "strategic-main-network",
            "display_state": "unresolved-gap",
            "geometry_semantics": (
                "selected-main-component-representative-point-marker-only-no-route-geometry"
            ),
            "gap_marker_kind": "selected-main-component-representative",
            "gap_marker_disclaimer": (
                "Selected Main component is physically separate; representative location only; "
                "no direct connection proposed"
            ),
            "reason": (
                "Selected Main component is physically separate; representative location only; "
                "no direct connection proposed"
            ),
            "missing_endpoint_geometry": False,
        },
    }
    for collection_key in ("reviewable", "reviewable_network"):
        data[collection_key]["features"].append(marker)
    data_path.write_text(data_prefix + json.dumps(data) + ";\n", encoding="utf-8")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(review_map.as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")
        page.wait_for_function("!window.SATN_REVIEW_MAP.isMoving()")
        page.locator("#layer-reviewable-gaps").check()
        page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              map.getStyle().layers
                .filter((layer) => layer.source && layer.id !== "reviewable-gaps")
                .forEach((layer) => map.setLayoutProperty(layer.id, "visibility", "none"));
            }"""
        )
        marker_point = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              const feature = map.getSource("reviewable")._data.features.find(
                (item) => item.id === "reviewable-gap:selected-main-component:representative-point"
              );
              const point = map.project(feature.geometry.coordinates);
              return {x: point.x, y: point.y};
            }"""
        )
        page.wait_for_function(
            """(point) => window.SATN_REVIEW_MAP.queryRenderedFeatures(
              point, {layers: ["reviewable-gaps"]}
            ).length > 0""",
            arg=marker_point,
        )
        map_box = page.locator("#map").bounding_box()
        assert map_box is not None
        page.mouse.move(
            map_box["x"] + marker_point["x"],
            map_box["y"] + marker_point["y"],
        )
        page.wait_for_function(
            """document.querySelector('#feature-details').innerText.includes(
              'Disconnected Main component (representative location)')"""
        )
        page.mouse.click(
            map_box["x"] + marker_point["x"],
            map_box["y"] + marker_point["y"],
        )
        page.wait_for_function(
            """document.querySelector('#feature-details').innerText.includes(
              'Disconnected Main component marker')"""
        )
        panel_text = page.locator("#feature-details").inner_text()
        assert "Disconnected Main component (representative location)" in panel_text
        assert "Role: Disconnected Main component marker" in panel_text
        assert "Recorded reason: Selected Main component is physically separate" in panel_text
        assert "Geometry meaning" not in panel_text
        assert "selected-main-component-representative" not in panel_text
        browser.close()


@pytest.mark.browser
def test_two_pinned_segments_show_a_high_level_comparison(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    result = compile(config)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(result.artifacts["review_map"].as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")
        page.wait_for_function("!window.SATN_REVIEW_MAP.isMoving()")

        points = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              map.getStyle().layers
                .filter((layer) => layer.source)
                .forEach((layer) => map.setLayoutProperty(layer.id, 'visibility', 'none'));
              const center = map.getCenter();
              const features = [
                {
                  type: 'Feature', id: 'segment-a',
                  properties: {
                    feature_type: 'reviewable-unselected-candidate',
                    candidate_id: 'segment-a', name: 'Riverside cycleway',
                    resident_count: 820, reusable_asset_share: 0.85,
                    directness_m: 1200, cumulative_elevation_variation_m: 22,
                    maximum_gradient_pct: 3.5
                  },
                  geometry: {type: 'LineString', coordinates: [
                    [center.lng - 0.002, center.lat + 0.001],
                    [center.lng + 0.002, center.lat + 0.001]
                  ]}
                },
                {
                  type: 'Feature', id: 'segment-b',
                  properties: {
                    feature_type: 'reviewable-unselected-candidate',
                    candidate_id: 'segment-b', name: 'Main road option',
                    resident_count: 610, reusable_asset_share: 0.2,
                    directness_m: 900, cumulative_elevation_variation_m: 48,
                  },
                  geometry: {type: 'LineString', coordinates: [
                    [center.lng - 0.002, center.lat - 0.001],
                    [center.lng + 0.002, center.lat - 0.001]
                  ]}
                }
              ];
              map.addSource('comparison-test', {
                type: 'geojson', data: {type: 'FeatureCollection', features}
              });
              map.addLayer({
                id: 'comparison-test', type: 'line', source: 'comparison-test',
                paint: {'line-color': '#2c3e50', 'line-width': 14}
              });
              return features.map((feature) => {
                const midpoint = feature.geometry.coordinates[0].map(
                  (value, index) => (value + feature.geometry.coordinates[1][index]) / 2
                );
                const point = map.project(midpoint);
                return {x: point.x, y: point.y};
              });
            }"""
        )
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.queryRenderedFeatures("
            "{layers: ['comparison-test']}).length === 2"
        )
        map_box = page.locator("#map").bounding_box()
        assert map_box is not None

        first, second = points
        page.mouse.click(map_box["x"] + first["x"], map_box["y"] + first["y"])
        page.mouse.click(map_box["x"] + second["x"], map_box["y"] + second["y"])

        lens = page.locator("#review-lens")
        assert lens.get_attribute("data-state") == "compare"
        assert (
            page.evaluate(
                "window.SATN_REVIEW_MAP.getSource('review-lens-highlight')._data.features.length"
            )
            == 0
        )
        assert lens.get_by_role("heading", name="Compare 2 segments").is_visible()
        assert "Riverside cycleway" in lens.inner_text()
        assert "Main road option" in lens.inner_text()
        assert "Population" in lens.inner_text()
        assert "Reusable alignment" in lens.inner_text()
        assert "Elevation variation" in lens.inner_text()
        assert "Unknown" in lens.inner_text()
        assert lens.locator("svg[aria-label^='Spider comparison']").is_visible()
        assert lens.get_by_role("table", name="Raw segment comparison values").is_visible()

        lens.get_by_role("button", name="Close route review lens").click()
        semantic_points = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              map.setLayoutProperty('comparison-test', 'visibility', 'none');
              const center = map.getCenter();
              const features = [
                {
                  type: 'Feature', id: 'semantic-a',
                  properties: {
                    name: 'Existing greenway section',
                    section_id: 'semantic-a',
                    feature_type: 'reviewable-selected-route',
                    display_state: 'existing-provision',
                    intervention_state: 'existing-provision',
                    primary_alignment_basis: 'greenway',
                    network_role: 'strategic-spine'
                  },
                  geometry: {type: 'LineString', coordinates: [
                    [center.lng - 0.002, center.lat + 0.002],
                    [center.lng + 0.002, center.lat + 0.002]
                  ]}
                },
                {
                  type: 'Feature', id: 'semantic-b',
                  properties: {
                    name: 'PROW upgrade section',
                    section_id: 'semantic-b',
                    feature_type: 'reviewable-selected-route',
                    display_state: 'upgrade-required',
                    intervention_state: 'upgrade-required',
                    primary_alignment_basis: 'public-footpath',
                    network_role: 'strategic-spine'
                  },
                  geometry: {type: 'LineString', coordinates: [
                    [center.lng - 0.002, center.lat - 0.002],
                    [center.lng + 0.002, center.lat - 0.002]
                  ]}
                }
              ];
              map.addSource('semantic-comparison-test', {
                type: 'geojson', data: {type: 'FeatureCollection', features}
              });
              map.addLayer({
                id: 'semantic-comparison-test', type: 'line',
                source: 'semantic-comparison-test',
                paint: {'line-color': '#5e35b1', 'line-width': 14}
              });
              return features.map((feature) => {
                const coordinates = feature.geometry.coordinates;
                const midpoint = coordinates[0].map(
                  (value, index) => (value + coordinates[1][index]) / 2
                );
                const point = map.project(midpoint);
                return {x: point.x, y: point.y};
              });
            }"""
        )
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.queryRenderedFeatures("
            "{layers: ['semantic-comparison-test']}).length === 2"
        )
        first_semantic, second_semantic = semantic_points
        page.mouse.click(
            map_box["x"] + first_semantic["x"],
            map_box["y"] + first_semantic["y"],
        )
        semantic_preview_text = lens.inner_text()
        assert "Existing greenway section" in semantic_preview_text
        assert "Role: Selected network route" in semantic_preview_text
        assert "Purpose: Part of the proposed network." in semantic_preview_text
        assert "Existing provision" in semantic_preview_text
        page.mouse.click(
            map_box["x"] + second_semantic["x"],
            map_box["y"] + second_semantic["y"],
        )

        assert lens.get_attribute("data-state") == "compare"
        semantic_text = lens.inner_text()
        assert "Existing greenway section" in semantic_text
        assert "PROW upgrade section" in semantic_text
        assert "Intervention state" in semantic_text
        assert "existing-provision" in semantic_text
        assert "upgrade-required" in semantic_text
        assert "Alignment Basis" in semantic_text
        assert "A spider chart needs three shared evidence dimensions" in semantic_text
        browser.close()


@pytest.mark.browser
def test_gradient_inspection_path_popovers_and_linear_evidence(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    config = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(config)
    result = compile(config)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.route("https://tiles.mapterhorn.com/**", lambda route: route.abort())
        page.goto(result.artifacts["review_map"].as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")

        assert page.locator("#layer-rail").is_visible()
        assert page.locator("#linear-evidence-panel").count() == 0
        assert page.locator("#linear-evidence-view").is_hidden()
        assert page.locator("#terrain-mode").is_visible()
        assert not page.locator("#terrain-mode").is_checked()
        assert "analytical default" in page.locator("#terrain-status").inner_text()
        assert page.locator("#criteria-controls").count() == 0
        assert page.locator("#criteria-panel").count() == 0

        information = page.get_by_role("button", name="About the strategic main network")
        information.click()
        popover = page.locator("#legend-strategic-network")
        assert popover.is_visible()
        assert information.get_attribute("aria-expanded") == "true"
        layer_order = page.evaluate(
            "() => window.SATN_REVIEW_MAP.getStyle().layers.map((layer) => layer.id)"
        )
        assert layer_order.index("strategic-spines") > layer_order.index("places")

        page.locator("#feature-index summary").click()
        path_candidates = page.evaluate(
            """() => {
              const eligible = new Set([
                "strategic-spine", "spine-access-connection",
                "school-access-connection", "branch-meeting-connection", "urban-spine"
              ]);
              const features = window.SATN_DATA.network.features.filter((feature) =>
                eligible.has(feature.properties.feature_type) &&
                feature.properties.topography_profile_id &&
                feature.geometry?.type === "LineString"
              );
              const key = (coordinate) =>
                `${Number(coordinate[0]).toFixed(5)},${Number(coordinate[1]).toFixed(5)}`;
              for (const first of features) {
                const firstEnd = first.geometry.coordinates.at(-1);
                const second = features.find((candidate) => {
                  if (candidate.id === first.id) return false;
                  const coordinates = candidate.geometry.coordinates;
                  return [coordinates[0], coordinates.at(-1)].some(
                    (endpoint) => key(endpoint) === key(firstEnd)
                  );
                });
                if (!second) continue;
                const secondCoordinates = second.geometry.coordinates;
                const secondFarEnd = key(secondCoordinates[0]) === key(firstEnd)
                  ? secondCoordinates.at(-1) : secondCoordinates[0];
                const disconnected = features.find((candidate) => {
                  if ([first.id, second.id].includes(candidate.id)) return false;
                  const coordinates = candidate.geometry.coordinates;
                  return [coordinates[0], coordinates.at(-1)].every(
                    (endpoint) => key(endpoint) !== key(secondFarEnd)
                  );
                });
                if (disconnected) return [first.id, second.id, disconnected.id];
              }
              return null;
            }"""
        )
        assert path_candidates is not None
        first_id, second_id, disconnected_id = path_candidates
        card = page.locator(f'[data-feature-id="{first_id}"]')
        card.click()
        assert card.get_attribute("aria-pressed") == "true"
        assert page.locator("#gradient-path-start").is_enabled()
        page.locator("#gradient-path-start").click()
        page.locator(f'[data-feature-id="{second_id}"]').click()
        page.locator("#gradient-path-append").click()

        assert "2 edges selected" in page.locator("#gradient-path-status").inner_text()
        gradient_details = page.get_by_role("button", name="Show gradient details")
        assert gradient_details.is_visible()
        assert gradient_details.get_attribute("aria-expanded") == "false"
        assert page.locator("#linear-evidence-view").is_hidden()
        gradient_details.click()
        assert gradient_details.get_attribute("aria-expanded") == "true"
        assert page.locator("#linear-evidence-view").is_visible()
        assert page.locator("#linear-evidence-heading").inner_text() == "Linear Evidence"
        page.locator(f'[data-feature-id="{disconnected_id}"]').click()
        page.locator("#gradient-path-append").click()
        assert "does not share its junction" in page.locator("#gradient-path-status").inner_text()
        assert page.locator(".track-cell.boundary").count() == 2
        assert "shared distance axis" in page.locator("#route-summary").inner_text()
        assert page.locator(".evidence-track").count() == 4
        assert page.locator(".track-label", has_text="Path order").count() == 1
        assert page.locator(".track-label", has_text="Gradient").count() == 2
        assert page.locator(".track-label", has_text="Gradient · 50 m").count() == 1
        assert page.locator(".track-label", has_text="Gradient · 20 m").count() == 1
        assert page.locator(".track-label", has_text="Road type").count() == 1
        assert page.locator(".track-cell.unavailable").count() >= 1
        assert "steepest sustained" in page.locator("#route-summary").inner_text()
        synchronized_cell = page.locator(
            '.track-cell[data-gradient-section-ids]:not([data-gradient-section-ids=""])'
        ).first
        section_id = synchronized_cell.get_attribute("data-gradient-section-ids").split()[0]
        synchronized_cell.focus()
        section_filter = page.evaluate(
            "window.SATN_REVIEW_MAP.getFilter('gradient-section-highlight')"
        )
        assert section_id in str(section_filter)

        page.locator("#gradient-path-reverse").click()
        assert "2 edges selected" in page.locator("#gradient-path-status").inner_text()
        reversed_cell = page.locator(
            '.track-cell[data-gradient-section-ids]:not([data-gradient-section-ids=""])'
        ).first
        reversed_section_id = reversed_cell.get_attribute("data-gradient-section-ids").split()[0]
        reversed_cell.focus()
        reversed_filter = page.evaluate(
            "window.SATN_REVIEW_MAP.getFilter('gradient-section-highlight')"
        )
        assert reversed_section_id in str(reversed_filter)
        page.locator("#terrain-mode").click()
        page.wait_for_function(
            "!document.querySelector('#terrain-mode').checked",
            timeout=10_000,
        )
        assert "restored 2D" in page.locator("#terrain-status").inner_text()
        assert "2 edges selected" in page.locator("#gradient-path-status").inner_text()
        page.locator("#gradient-path-remove").click()
        assert "1 edge selected" in page.locator("#gradient-path-status").inner_text()
        page.locator("#gradient-path-reset").click()
        assert "No path selected" in page.locator("#gradient-path-status").inner_text()
        assert gradient_details.is_hidden()
        assert page.locator("#linear-evidence-view").is_hidden()

        browser.close()


@pytest.mark.browser
def test_route_choices_show_preferred_alternative_and_explicit_attribution(
    tmp_path: Path,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    result = compile(
        config,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(result.artifacts["review_map"].as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")
        page.wait_for_function("!window.SATN_REVIEW_MAP.isMoving()")

        route_points = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              const features = window.SATN_DATA.reviewable_network.features;
              const preferred = features.find((feature) =>
                feature.properties?.feature_type === 'reviewable-selected-route' &&
                feature.properties?.layer === 'Strategic Main Network'
              );
              const alternative = features.find((feature) =>
                feature.properties?.feature_type === 'reviewable-unselected-candidate' &&
                feature.properties?.candidate_set_id === preferred?.properties?.candidate_set_id
              );
              if (!preferred || !alternative) return null;
              const project = (feature, preferInterior = false) => {
                const coordinates = feature.geometry.coordinates;
                const coordinate = preferInterior && coordinates.length > 2
                  ? coordinates[Math.floor(coordinates.length / 2)]
                  : coordinates[0].map((value, index) =>
                    (value + coordinates[coordinates.length - 1][index]) / 2
                  );
                const point = map.project(coordinate);
                return {x: point.x, y: point.y, id: feature.id};
              };
              return {
                candidateSetId: preferred.properties.candidate_set_id,
                preferred: project(preferred, true),
                alternative: project(alternative)
              };
            }"""
        )
        assert route_points is not None

        page.get_by_text("Asset and alignment evidence", exact=True).click()
        page.locator("#layer-unselected-candidates").check()
        route_points = page.evaluate(
            """(candidateSetId) => {
              const map = window.SATN_REVIEW_MAP;
              const features = window.SATN_DATA.reviewable_network.features;
              const preferred = features.find((feature) =>
                feature.properties?.feature_type === 'reviewable-selected-route' &&
                feature.properties?.layer === 'Strategic Main Network' &&
                feature.properties?.candidate_set_id === candidateSetId
              );
              const alternative = features.find((feature) =>
                feature.properties?.feature_type === 'reviewable-unselected-candidate' &&
                feature.properties?.candidate_set_id === candidateSetId
              );
              if (!preferred || !alternative) return null;
              const project = (feature, preferInterior = false) => {
                const coordinates = feature.geometry.coordinates;
                const coordinate = preferInterior && coordinates.length > 2
                  ? coordinates[Math.floor(coordinates.length / 2)]
                  : coordinates[0].map((value, index) =>
                    (value + coordinates[coordinates.length - 1][index]) / 2
                  );
                const point = map.project(coordinate);
                return {x: point.x, y: point.y, id: feature.id};
              };
              return {
                candidateSetId,
                preferredReason: preferred.properties?.selection_reason || null,
                preferredAuthority: preferred.properties?.authority || null,
                alternativeComparisonReason: alternative.properties?.comparison_reason || null,
                preferred: project(preferred, true),
                alternative: project(alternative)
              };
            }""",
            route_points["candidateSetId"],
        )
        assert route_points is not None
        map_box = page.locator("#map").bounding_box()
        assert map_box is not None
        dash_array = page.evaluate(
            "window.SATN_REVIEW_MAP.getPaintProperty("
            "'reviewable-unselected-candidates', 'line-dasharray')"
        )
        assert dash_array and len(dash_array) >= 2
        preferred = route_points["preferred"]
        page.wait_for_function(
            "(point) => window.SATN_REVIEW_MAP.queryRenderedFeatures(point, "
            "{layers: ['reviewable-strategic-main-network']}).length > 0",
            arg=preferred,
        )
        canvas = page.locator(".maplibregl-canvas")
        canvas.hover(position={"x": preferred["x"], "y": preferred["y"]})
        page.wait_for_function("document.querySelector('#feature-details').innerText.length > 0")
        canvas.click(position={"x": preferred["x"], "y": preferred["y"]})
        page.wait_for_function("document.querySelector('#feature-details').innerText.length > 0")
        preferred_text = page.locator("#feature-details").inner_text()
        assert "Preferred route" in preferred_text
        assert "Choice attribution" in preferred_text
        assert route_points["preferredAuthority"] == "compiler"
        assert "Compiler" in preferred_text
        assert "Selection reason" in preferred_text
        preferred_technical = page.locator(".route-choice-technical")
        preferred_technical.locator("summary").click()
        assert (route_points["preferredReason"] or "Not recorded") in (
            preferred_technical.inner_text()
        )
        assert "Officer" not in preferred_text

        page.get_by_role("button", name="Close route review lens").click()
        alternative = route_points["alternative"]
        map_box = page.locator("#map").bounding_box()
        assert map_box is not None
        canvas.hover(position={"x": alternative["x"], "y": alternative["y"]})
        page.wait_for_function("document.querySelector('#feature-details').innerText.length > 0")
        canvas.click(position={"x": alternative["x"], "y": alternative["y"]})
        page.wait_for_function("document.querySelector('#feature-details').innerText.length > 0")
        alternative_text = page.locator("#feature-details").inner_text()
        assert "Considered alternative" in alternative_text
        assert "Comparison reason" in alternative_text
        alternative_technical = page.locator(".route-choice-technical")
        alternative_technical.locator("summary").click()
        assert (route_points["alternativeComparisonReason"] or "Not recorded") in (
            alternative_technical.inner_text()
        )
        assert (
            "Choice attribution" not in page.locator(".route-choice-card.alternative").inner_text()
        )

        browser.close()


@pytest.mark.browser
def test_selecting_route_opens_same_journey_comparison_without_enabling_alternatives(
    tmp_path: Path,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    result = compile(
        config,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(result.artifacts["review_map"].as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")
        page.wait_for_function("!window.SATN_REVIEW_MAP.isMoving()")

        page.get_by_text("Asset and alignment evidence", exact=True).click()
        alternatives = page.locator("#layer-unselected-candidates")
        assert not alternatives.is_checked()
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.getLayoutProperty("
            "'reviewable-unselected-candidates', 'visibility') === 'none'"
        )

        route_points = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              const features = window.SATN_DATA.reviewable_network.features;
              const preferred = features.find((feature) =>
                feature.properties?.feature_type === 'reviewable-selected-route' &&
                feature.properties?.layer === 'Strategic Main Network'
              );
              const alternative = features.find((feature) =>
                feature.properties?.feature_type === 'reviewable-unselected-candidate' &&
                feature.properties?.candidate_set_id === preferred?.properties?.candidate_set_id &&
                feature.properties?.admission_disposition === 'admitted'
              );
              if (!preferred || !alternative) return null;
              const project = (feature, preferInterior = false) => {
                const coordinates = feature.geometry.coordinates;
                const coordinate = preferInterior && coordinates.length > 2
                  ? coordinates[Math.floor(coordinates.length / 2)]
                  : coordinates[0].map((value, index) =>
                    (value + coordinates[coordinates.length - 1][index]) / 2
                  );
                const point = map.project(coordinate);
                return {x: point.x, y: point.y};
              };
              return {
                candidateSetId: preferred.properties.candidate_set_id,
                preferredCandidateId: preferred.properties.candidate_id,
                alternativeCandidateId: alternative.properties.candidate_id,
                preferredBasis: preferred.properties.primary_alignment_basis || null,
                alternativeBasis: alternative.properties.primary_alignment_basis || null,
                preferredReason: preferred.properties.selection_reason || null,
                preferredAuthority: preferred.properties.authority || null,
                alternativeReason: alternative.properties.comparison_reason || null,
                preferred: project(preferred, true),
                alternative: project(alternative)
              };
            }"""
        )
        assert route_points is not None
        canvas = page.locator(".maplibregl-canvas")
        preferred = route_points["preferred"]
        page.wait_for_function(
            "(point) => window.SATN_REVIEW_MAP.queryRenderedFeatures(point, "
            "{layers: ['reviewable-strategic-main-network']}).length > 0",
            arg=preferred,
        )
        canvas.click(position={"x": preferred["x"], "y": preferred["y"]})

        comparison = page.locator(".route-choice-comparison")
        assert comparison.is_visible()
        comparison_text = comparison.inner_text()
        assert "Journey comparison" in comparison_text
        assert "Preferred route" in comparison_text
        assert "Considered alternative" in comparison_text
        assert route_points["candidateSetId"] not in comparison_text
        assert route_points["preferredCandidateId"] not in comparison_text
        assert route_points["alternativeCandidateId"] not in comparison_text
        assert "Current NCN" in comparison_text
        assert "A Road" in comparison_text
        readable_preferred_reason = (
            (route_points["preferredReason"] or "Not recorded")
            .replace(route_points["preferredCandidateId"], "Current NCN")
            .replace(route_points["alternativeCandidateId"], "A Road")
            .replace("candidate-source-precedence", "candidate source precedence")
        )
        readable_alternative_reason = (
            (route_points["alternativeReason"] or "Not recorded")
            .replace(route_points["preferredCandidateId"], "Current NCN")
            .replace(route_points["alternativeCandidateId"], "A Road")
            .replace("candidate-source-precedence", "candidate source precedence")
        )
        assert readable_preferred_reason in comparison_text
        assert readable_alternative_reason in comparison_text
        assert "Choice attribution" in comparison_text
        assert "Compiler" in comparison_text
        ordinary_summary = page.locator(".artifact-why").inner_text()
        assert route_points["preferredCandidateId"] not in ordinary_summary
        assert route_points["alternativeCandidateId"] not in ordinary_summary
        assert route_points["preferredReason"] not in ordinary_summary
        assert page.evaluate(
            """() => {
              const panel = document.querySelector('#feature-details');
              const comparison = panel?.querySelector('.route-choice-comparison');
              const technical = panel?.querySelector('.artifact-context');
              return Boolean(
                comparison && technical &&
                (comparison.compareDocumentPosition(technical) & Node.DOCUMENT_POSITION_FOLLOWING)
              );
            }"""
        )
        assert page.locator(".route-choice-card dt", has_text="Selection reason").count() == 1
        assert page.locator(".route-choice-card dt", has_text="Comparison reason").count() == 1
        technical = page.locator(".route-choice-technical")
        assert technical.is_visible()
        technical.locator("summary").click()
        technical_text = technical.inner_text()
        assert route_points["candidateSetId"] in technical_text
        assert route_points["preferredCandidateId"] in technical_text
        assert route_points["alternativeCandidateId"] in technical_text
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.getLayoutProperty("
            "'reviewable-unselected-candidates', 'visibility') === 'none'"
        )
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.getLayoutProperty("
            "'reviewable-strategic-main-network', 'line-cap') === 'round'"
        )
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.getLayoutProperty("
            "'review-lens-related-alternatives', 'visibility') === 'visible'"
        )
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.getPaintProperty("
            "'review-lens-related-alternatives', 'line-dasharray').length >= 2"
        )
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.getSource("
            "'review-lens-related-alternatives')._data.features.length >= 1"
        )

        alternative = route_points["alternative"]
        page.wait_for_function(
            "(point) => window.SATN_REVIEW_MAP.queryRenderedFeatures(point, "
            "{layers: ['review-lens-related-alternatives']}).length > 0",
            arg=alternative,
        )
        canvas.click(position={"x": alternative["x"], "y": alternative["y"]})
        assert comparison.is_visible()
        comparison_text = comparison.inner_text()
        assert "Preferred route" in comparison_text
        assert "Considered alternative" in comparison_text
        assert route_points["candidateSetId"] not in comparison_text
        assert route_points["preferredCandidateId"] not in comparison_text
        assert route_points["alternativeCandidateId"] not in comparison_text

        page.get_by_role("button", name="Close route review lens").click()
        assert page.locator("#review-lens").is_hidden()
        assert not alternatives.is_checked()
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.getLayoutProperty("
            "'reviewable-unselected-candidates', 'visibility') === 'none'"
        )
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.getLayoutProperty("
            "'review-lens-related-alternatives', 'visibility') === 'none'"
        )
        assert (
            page.evaluate(
                """() => window.SATN_DATA.reviewable_network.features.find((feature) =>
              feature.properties?.feature_type === 'reviewable-selected-route' &&
              feature.properties?.layer === 'Strategic Main Network'
            )?.properties?.selection_disposition"""
            )
            == "selected"
        )

        browser.close()


@pytest.mark.browser
def test_feature_index_keyboard_opens_urban_journey_comparison(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    source = load_snapshot(config)
    labels = source["label_places"].copy()
    labels["kind"] = "town"
    labels["element"] = "node"
    labels["id"] = ["bath", "saltford"]
    labels["name"] = ["Bath", "Saltford"]
    source["label_places"] = labels
    compiled = compile_network(config, source, FakeAgentRuntime())
    (tmp_path / "backbone-comparison.json").write_text("{}")
    review = tmp_path / "review-map"
    review.mkdir()
    _write_review_map(review, config, compiled)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto((review / "index.html").as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")
        page.wait_for_function("!window.SATN_REVIEW_MAP.isMoving()")

        page.locator("#feature-index summary").click()
        first_card = page.locator("#connection-list .connection").first
        assert "Bath → Saltford" in first_card.inner_text()
        first_card.focus()
        first_card.press("Enter")

        assert page.locator("#review-lens").get_attribute("data-state") == "pinned"
        comparison = page.locator(".route-choice-comparison")
        assert comparison.is_visible()
        comparison_text = comparison.inner_text()
        assert "Preferred route" in comparison_text
        assert "Considered alternative" in comparison_text
        assert page.locator("#layer-unselected-candidates").is_checked() is False
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.getLayoutProperty("
            "'review-lens-related-alternatives', 'visibility') === 'visible'"
        )
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.getSource("
            "'review-lens-related-alternatives')._data.features.length > 0"
        )
        browser.close()


@pytest.mark.browser
def test_shared_main_geometry_opens_a_named_journey_comparison(tmp_path: Path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    source = load_snapshot(config)
    labels = source["label_places"].copy()
    labels["kind"] = "town"
    labels["place_id"] = ["bath", "saltford"]
    labels["name"] = ["Bath", "Saltford"]
    railway_mid = gpd.GeoDataFrame(
        [
            {
                "place_id": "railway-mid",
                "name": "Railway Mid",
                "kind": "town",
                "population": 1000,
                "geometry": Point(-2.405, 51.405),
            }
        ],
        geometry="geometry",
        crs=labels.crs,
    )
    source["label_places"] = gpd.GeoDataFrame(
        pd.concat([labels, railway_mid], ignore_index=True),
        geometry="geometry",
        crs=labels.crs,
    )
    compiled = compile_network(config, source, FakeAgentRuntime())
    (tmp_path / "backbone-comparison.json").write_text("{}")
    review = tmp_path / "review-map"
    review.mkdir()
    _write_review_map(review, config, compiled)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto((review / "index.html").as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")
        page.wait_for_function("!window.SATN_REVIEW_MAP.isMoving()")

        shared = page.evaluate(
            """() => {
              const collection = window.SATN_DATA.reviewable_network.strategic_main_display;
              const feature = collection?.features.find((candidate) =>
                candidate.properties?.participating_journey_ids?.length > 1
              );
              if (!feature) return null;
              const coordinates = feature.geometry.type === 'LineString'
                ? feature.geometry.coordinates
                : feature.geometry.coordinates[0];
              const midpoint = coordinates[0].map((value, index) =>
                (value + coordinates[1][index]) / 2
              );
              const point = window.SATN_REVIEW_MAP.project(midpoint);
              return {
                id: feature.id,
                routeIds: feature.properties.participating_journey_ids,
                x: point.x,
                y: point.y
              };
            }"""
        )
        assert shared is not None
        assert len(shared["routeIds"]) > 1
        assert page.evaluate(
            "(id) => window.SATN_REVIEW_MAP.getSource('strategic-main-display')._data.features"
            ".some((feature) => feature.id === id)",
            shared["id"],
        )

        map_box = page.locator("#map").bounding_box()
        assert map_box is not None
        canvas = page.locator(".maplibregl-canvas")
        canvas.hover(position={"x": shared["x"], "y": shared["y"]})
        page.wait_for_function(
            "document.querySelector('#feature-details').innerText.includes('Shared Main section')"
        )
        canvas.click(position={"x": shared["x"], "y": shared["y"]})
        assert page.locator("#review-lens").get_attribute("data-state") == "pinned"
        chooser = page.locator(".shared-journey-list")
        assert chooser.is_visible()
        chooser_text = chooser.inner_text()
        assert "Bath" in chooser_text
        assert "Saltford" in chooser_text

        journey_button = chooser.get_by_role("button", name="Open Bath → Saltford")
        journey_button.hover()
        assert chooser.is_visible()
        journey_button.click()
        comparison = page.locator(".route-choice-comparison")
        assert comparison.is_visible()
        assert "Preferred route" in comparison.inner_text()
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.getLayoutProperty("
            "'reviewable-unselected-candidates', 'visibility') === 'none'"
        )
        browser.close()
