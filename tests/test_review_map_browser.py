from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from satn import compile
from satn.models import CouncilConfig
from satn.sources import snapshot

PROJECT = Path(__file__).parents[1]


@pytest.mark.browser
def test_complete_strategic_network_is_the_rendered_default(tmp_path: Path) -> None:
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
              const counts = Object.fromEntries([
                ["strategic-spine", "strategic-spines"],
                ["spine-access-connection", "spine-access-connections"],
                ["cross-spine-connector", "cross-spine-connectors"],
              ].map(([featureType, layerId]) => [layerId, {
                features: network.filter(
                  (feature) => feature.properties?.feature_type === featureType
                ).length,
                visible: map.getLayoutProperty(layerId, "visibility") !== "none",
              }]));
              return {
                counts,
                reviewableCoreVisible: map.getLayer("reviewable-strategic-network-core")
                  ? map.getLayoutProperty(
                      "reviewable-strategic-network-core", "visibility"
                    ) !== "none"
                  : false,
              };
            }"""
        )

        assert state["counts"]["strategic-spines"]["features"] > 0
        assert all(layer["visible"] for layer in state["counts"].values() if layer["features"] > 0)
        assert not state["reviewableCoreVisible"]
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.queryRenderedFeatures({layers: ['strategic-spines']})"
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
                    kind: feature.properties.kind
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

        page.mouse.move(artifact_x, artifact_y)
        assert not page_errors
        assert lens.is_visible()
        hover_text = panel.inner_text()
        assert selection["name"] in hover_text
        assert selection["id"] in hover_text
        assert selection["kind"] in hover_text
        assert "places" in hover_text
        assert "Point" in hover_text
        assert "All contextual properties" in hover_text
        assert panel.locator(".artifact-context").get_attribute("open") is None

        page.mouse.move(map_box["x"] + 2, map_box["y"] + 2)
        assert lens.is_hidden()

        page.mouse.move(artifact_x, artifact_y)
        page.mouse.click(artifact_x, artifact_y)
        page.mouse.move(map_box["x"] + 2, map_box["y"] + 2)
        assert lens.is_visible()

        assert not page_errors
        panel_text = panel.inner_text()
        assert selection["name"] in panel_text
        assert selection["id"] in panel_text
        assert selection["kind"] in panel_text
        assert "places" in panel_text
        assert "Point" in panel_text
        assert "All contextual properties" in panel_text

        page.mouse.move(artifact_x, artifact_y)
        page.mouse.click(artifact_x, artifact_y)
        page.mouse.move(map_box["x"] + 2, map_box["y"] + 2)
        assert lens.is_hidden()

        page.locator("#feature-index summary").click()
        indexed_artifact = page.locator("#connection-list .connection").first
        indexed_artifact.focus()
        assert lens.is_visible()
        assert indexed_artifact.get_attribute("data-feature-id") in panel.inner_text()
        indexed_artifact.blur()
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
        route_preview_text = lens.inner_text()
        assert route_preview["name"] in route_preview_text
        assert route_preview["id"] in route_preview_text
        assert "Line colour" in route_preview_text
        assert "Teal" in route_preview_text
        assert "Map meaning" in route_preview_text
        assert "Selected Strategic Spine" in route_preview_text
        assert "Not available" not in route_preview_text
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
        assert "reference-option-test" in reference_text
        assert "complementary" in reference_text
        assert "Retained as contextual reference evidence." in reference_text
        assert "reference-satn-options" in reference_text
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
        lens = page.get_by_role("dialog", name="Route review lens")

        assert page.locator("#linear-evidence-panel").count() == 0
        assert lens.is_hidden()

        page.mouse.move(first_x, first_y)
        assert lens.is_visible()
        assert lens.get_attribute("data-state") == "preview"
        first_preview = lens.inner_text()

        page.mouse.click(first_x, first_y)
        page.mouse.move(second_x, second_y)
        assert lens.get_attribute("data-state") == "pinned"
        pinned_during_second_hover = lens.inner_text()

        page.mouse.click(empty_x, empty_y)
        assert lens.is_hidden()

        page.mouse.move(second_x, second_y)
        assert lens.is_visible()
        second_preview_after_unlock = lens.inner_text()

        failures = []
        if first["name"] not in first_preview or first["id"] not in first_preview:
            failures.append("hovering the first artifact did not preview it in the lens")
        if (
            first["name"] not in pinned_during_second_hover
            or second["name"] in pinned_during_second_hover
        ):
            failures.append("hovering another artifact replaced the pinned lens")
        if (
            second["name"] not in second_preview_after_unlock
            or second["id"] not in second_preview_after_unlock
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

        control = page.locator("#layer-authority-boundaries")
        information = page.get_by_role("button", name="About authority boundaries")
        popover = page.locator("#legend-authority-boundaries")
        lens = page.get_by_role("dialog", name="Route review lens")
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
        page.locator("#layer-population-display-sections").check()
        assert map_legend.get_attribute("open") is None

        page.locator("#layer-reviewable-gaps").check()
        findings = page.locator("#reviewable-findings")
        if findings.locator(".finding-button").count():
            assert findings.evaluate("element => element.tagName") == "DETAILS"
            assert findings.get_attribute("open") is None
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

        information = page.get_by_role("button", name="About the strategic network")
        information.click()
        popover = page.locator("#legend-strategic-network")
        assert popover.is_visible()
        assert information.get_attribute("aria-expanded") == "true"
        assert page.evaluate("() => window.SATN_REVIEW_MAP.getStyle().layers.at(-1).id") == (
            "strategic-network"
        )

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
