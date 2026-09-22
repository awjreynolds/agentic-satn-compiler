from __future__ import annotations

import http.server
import importlib.util
import json
import os
import shutil
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from satn.pages_packaging import package_pages

PROJECT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_pages_rendering", PROJECT / "scripts" / "validate_pages_rendering.py"
)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


def _write_native_catalogue(path: Path) -> None:
    definition = path.parent / "native-area" / "area.yaml"
    definition.parent.mkdir(parents=True, exist_ok=True)
    definition.write_text(
        """area_id: native-geography
area_name: Native area
deployment_id: native-area
source:
  snapshot_dir: ../snapshots
publication:
  output_dir: build
  title: Native area deployment
""",
        encoding="utf-8",
    )
    path.write_text(
        """schema_version: satn-deployment-catalogue/v1
title: Native deployments
deployments:
  - deployment_id: native-area
    publication_kind: native-agentic
    area_id: native-geography
    area_name: Native area
    area_definition: native-area/area.yaml
    deployment_path: deployments/native-area/
    artifacts:
      review_map: index.html
      network_geojson: decision-map.geojson
""",
        encoding="utf-8",
    )


def _write_native_bundle(
    root: Path,
    *,
    omit_source_geometry: bool = False,
    omit_departure_geometry: bool = False,
) -> None:
    bundle = root / "native-area"
    bundle.mkdir(parents=True, exist_ok=True)
    network = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "kind": "source-baseline",
                    "baseline_role": "a-road",
                    "source_id": "source:a-road",
                },
                "geometry": {"type": "LineString", "coordinates": [[-2, 51], [-1.9, 51.1]]},
            },
            {
                "type": "Feature",
                "properties": {
                    "kind": "selected-alignment",
                    "decision_id": "decision:selected",
                    "candidate_id": "candidate:selected",
                    "reason": "Selected public evidence alignment",
                    "uncertainties": ["Provision remains unknown"],
                    "evidence_refs": ["candidate:selected"],
                },
                "geometry": {"type": "LineString", "coordinates": [[-2, 51], [-1.9, 51.1]]},
            },
            {
                "type": "Feature",
                "properties": {
                    "kind": "provisional-alignment",
                    "decision_id": "decision:provisional",
                    "candidate_id": "candidate:provisional",
                    "reason": "Provisional public evidence alignment",
                    "uncertainties": ["Access remains unknown"],
                    "evidence_refs": ["candidate:provisional"],
                },
                "geometry": {"type": "LineString", "coordinates": [[-1.9, 51.1], [-1.8, 51.2]]},
            },
            {
                "type": "Feature",
                "properties": {
                    "kind": "unresolved-decision",
                    "decision_id": "decision:unresolved",
                    "reason": "Specialist review remains unresolved",
                    "uncertainties": ["No admitted choice"],
                    "evidence_refs": ["decision:unresolved"],
                },
                "geometry": {"type": "LineString", "coordinates": [[-1.8, 51.2], [-1.7, 51.3]]},
            },
            {
                "type": "Feature",
                "properties": {
                    "kind": "a-road-departure",
                    "decision_id": "decision:selected",
                    "alternative_candidate_id": "candidate:strategic",
                    "reason": "Selected alignment departs the A-road baseline",
                    "evidence_refs": ["candidate:strategic"],
                },
                "geometry": {"type": "LineString", "coordinates": [[-1.7, 51.3], [-1.6, 51.35]]},
            },
        ],
    }
    (bundle / "decision-map.geojson").write_text(json.dumps(network), encoding="utf-8")
    (bundle / "decision-map.json").write_text(
        json.dumps(
            {
                "schema": "satn-rust-decision-map/v1",
                "area_id": "native-geography",
                "snapshot_id": "snapshot-1",
                "branch": "review-branch",
                "base_id": "base-1",
                "status": "reviewable-with-gaps",
                "run_status": "complete",
                "counts": {
                    "source_baseline": 1,
                    "prepared_connections": 3,
                    "pending_connections": 0,
                    "connections": 3,
                    "candidates": 3,
                    "decisions": 3,
                    "selected": 2,
                    "provisional": 1,
                    "unresolved": 1,
                    "departures": 1,
                    "unresolved_facts": 0,
                    "access_obligations": 0,
                    "unresolved_access": 0,
                },
                "files": {
                    "geojson": "decision-map.geojson",
                    "details": "decision-map.json",
                    "html": "index.html",
                    "publication": "publication.json",
                },
            }
        ),
        encoding="utf-8",
    )
    assets = bundle / "assets"
    assets.mkdir()
    for name in ("maplibre-gl.js", "maplibre-gl.css", "MAPLIBRE-LICENSE.txt"):
        shutil.copy2(PROJECT / "src" / "satn" / "assets" / name, assets / name)
    template = (PROJECT / "rust" / "src" / "native_map_template.html").read_text(encoding="utf-8")
    html = template
    for placeholder, value in {
        "__TITLE__": "Native area deployment",
        "__DEPLOYMENT__": "native-area",
        "__BRANCH__": "review-branch",
        "__BASE_ID__": "base-1",
        "__ACCOUNTING__": "reviewable-with-gaps",
        "__STATUS__": "complete",
        "__ATTRIBUTION__": "Fixture attribution",
        "__SOURCE_ATTRIBUTIONS__": "Fixture official attribution",
        "__SOURCE_COUNT__": "1",
        "__PREPARED_CONNECTIONS__": "3",
        "__PENDING_CONNECTIONS__": "0",
        "__CANDIDATE_COUNT__": "3",
        "__DECISION_COUNT__": "3",
        "__UNRESOLVED_FACTS__": "0",
        "__UNRESOLVED_ACCESS__": "0",
    }.items():
        html = html.replace(placeholder, value)
    (bundle / "index.html").write_text(html, encoding="utf-8")
    (bundle / "publication.json").write_text(
        json.dumps(
            {
                "schema": "satn-rust-publication/v1",
                "publication_kind": "native-agentic",
                "deployment_id": "native-area",
                "area_id": "native-geography",
                "snapshot_id": "snapshot-1",
                "branch": "review-branch",
                "base_id": "base-1",
                "status": "reviewable-with-gaps",
                "run_status": "complete",
                "accounting_status": "reviewable-with-gaps",
                "disclaimer": "Experimental SATN POC — not an adopted plan.",
                "counts": {
                    "source_baseline": 1,
                    "prepared_connections": 3,
                    "pending_connections": 0,
                    "connections": 3,
                    "candidates": 3,
                    "decisions": 3,
                    "selected": 2,
                    "provisional": 1,
                    "unresolved": 1,
                    "departures": 1,
                    "unresolved_facts": 0,
                    "access_obligations": 0,
                    "unresolved_access": 0,
                },
                "files": {
                    "geojson": "decision-map.geojson",
                    "details": "decision-map.json",
                    "html": "index.html",
                    "publication": "publication.json",
                },
            }
        ),
        encoding="utf-8",
    )
    (bundle / "history").mkdir()
    (bundle / "history" / "events.jsonl").write_text(
        '{"private":true}\n',
        encoding="utf-8",
    )
    (bundle / "planning.json").write_text(
        '{"private":true}\n',
        encoding="utf-8",
    )


def test_package_pages_accepts_the_explicit_native_agentic_publication(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    _write_native_catalogue(catalogue)
    _write_native_bundle(bundles)

    result = package_pages(
        catalogue,
        bundles,
        tmp_path / "pages",
        tmp_path / "satn-pages.zip",
    )

    deployment = result.pages_directory / "deployments" / "native-area"
    assert (deployment / "index.html").is_file()
    assert (deployment / "decision-map.geojson").is_file()
    assert (deployment / "decision-map.json").is_file()
    assert (deployment / "publication.json").is_file()
    assert not (deployment / "decision-map.html").exists()
    assert not (deployment / "history" / "events.jsonl").exists()
    assert not (deployment / "planning.json").exists()
    with zipfile.ZipFile(result.release_artifact) as archive:
        members = set(archive.namelist())
    assert "pages/deployments/native-area/history/events.jsonl" not in members
    assert "pages/deployments/native-area/planning.json" not in members
    public_catalogue = json.loads(
        (result.pages_directory / "catalogue.json").read_text(encoding="utf-8")
    )
    entry = public_catalogue["deployments"][0]
    assert entry["publication_kind"] == "native-agentic"
    assert entry["artifacts"]["network_geojson"].endswith("decision-map.geojson")


@pytest.mark.browser
def test_native_package_takes_over_a_legacy_cache_first_worker(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    _write_native_catalogue(catalogue)
    _write_native_bundle(bundles)
    result = package_pages(
        catalogue,
        bundles,
        tmp_path / "pages",
        tmp_path / "satn-pages.zip",
    )

    native_deployment = result.pages_directory / "deployments" / "native-area"
    assert (native_deployment / "service-worker.js").is_file()

    server_root = tmp_path / "server"
    shutil.copytree(result.pages_directory, server_root)
    deployment = server_root / "deployments" / "native-area"
    (deployment / "index.html").write_text(
        """<!doctype html>
<html><body><p id="legacy-map">Old Python map</p><script>
navigator.serviceWorker.register("service-worker.js");
</script></body></html>
""",
        encoding="utf-8",
    )
    (deployment / "service-worker.js").write_text(
        """const CACHE = "satn-native-area-run-old";
self.addEventListener("install", event => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE).then(cache =>
    cache.addAll(["./", "index.html"])
  ));
});
self.addEventListener("activate", event => {
  event.waitUntil(self.clients.claim());
});
self.addEventListener("fetch", event => {
  if (event.request.method === "GET") {
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
  }
});
""",
        encoding="utf-8",
    )

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(server_root), **kwargs)

        def do_GET(self) -> None:
            if self.path.endswith("/service-worker.js"):
                body = Path(self.translate_path(self.path)).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        with VALIDATOR.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=executable)
            context = browser.new_context()
            page = context.new_page()
            url = f"http://127.0.0.1:{server.server_port}/deployments/native-area/"
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_function(
                "document.getElementById('legacy-map')?.textContent === 'Old Python map'"
            )
            page.wait_for_function("navigator.serviceWorker.controller !== null")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function(
                "async () => (await caches.keys()).includes('satn-native-area-run-old')"
            )
            page.evaluate(
                "caches.open('unrelated-cache').then(cache => "
                "cache.put('/unrelated', new Response('keep')))"
            )

            shutil.copytree(native_deployment, deployment, dirs_exist_ok=True)
            page.evaluate(
                "window.__satnControllerChanged = false; "
                "navigator.serviceWorker.addEventListener('controllerchange', () => "
                "window.__satnControllerChanged = true)"
            )
            page.evaluate(
                "void navigator.serviceWorker.getRegistration().then(registration => "
                "registration.update())"
            )
            page.wait_for_function("window.__satnControllerChanged === true")
            page.wait_for_function(
                "async () => !(await caches.keys()).includes('satn-native-area-run-old')"
            )
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("document.documentElement.dataset.nativeReady === 'true'")
            assert page.locator('[data-native-publication="native-agentic"]').count() == 1
            assert "unrelated-cache" in page.evaluate("caches.keys()")
            browser.close()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.browser
def test_native_rendering_gate_uses_the_loaded_map_and_public_decision_sections(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    _write_native_catalogue(catalogue)
    _write_native_bundle(bundles)
    result = package_pages(
        catalogue,
        bundles,
        tmp_path / "pages",
        tmp_path / "satn-pages.zip",
    )

    validated = VALIDATOR.validate_pages_rendering(result.pages_directory)

    assert len(validated) == 1
    assert validated[0].deployment_id == "native-area"
    assert validated[0].strategic_spines == 2
    assert validated[0].cross_spine_connectors == 1
    assert validated[0].rendered_strategic_spines == 3


@pytest.mark.browser
def test_native_map_supports_public_feature_inspection_reset_and_layer_toggle(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    _write_native_catalogue(catalogue)
    _write_native_bundle(bundles)
    result = package_pages(
        catalogue,
        bundles,
        tmp_path / "pages",
        tmp_path / "satn-pages.zip",
    )

    with (
        VALIDATOR._serve(result.pages_directory) as origin,
        VALIDATOR.sync_playwright() as playwright,
    ):
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, executable_path=executable)
        try:
            for viewport in ({"width": 1280, "height": 900}, {"width": 390, "height": 844}):
                context = browser.new_context(viewport=viewport)
                page = context.new_page()
                page.goto(
                    f"{origin}/deployments/native-area/index.html", wait_until="domcontentloaded"
                )
                page.wait_for_function(
                    "() => document.documentElement.dataset.nativeReady === 'true' && "
                    "window.SATN_NATIVE_MAP?.isStyleLoaded()"
                )
                assert not page.locator(
                    "input[data-layer-toggle='candidate-alternative']"
                ).is_visible()

                initial = page.evaluate(
                    "() => ({center: window.SATN_NATIVE_MAP.getCenter().toArray(), "
                    "zoom: window.SATN_NATIVE_MAP.getZoom()})"
                )
                page.evaluate(
                    "() => window.SATN_NATIVE_MAP.jumpTo({center: [-1.7, 51.3], zoom: 12})"
                )
                moved = page.evaluate(
                    "() => ({center: window.SATN_NATIVE_MAP.getCenter().toArray(), "
                    "zoom: window.SATN_NATIVE_MAP.getZoom()})"
                )
                assert moved != initial
                page.locator("[data-native-reset]").click()
                page.wait_for_function("() => window.SATN_NATIVE_MAP.getZoom() !== 12")
                page.wait_for_function(
                    "() => window.SATN_NATIVE_MAP.queryRenderedFeatures({layers: "
                    "['native-selected']}).length > 0"
                )
                assert page.locator(".decision-list, .departure-list").count() == 0
                assert page.locator("[data-native-decision-kind]").count() == 0

                point = page.evaluate(
                    """() => {
                      const map = window.SATN_NATIVE_MAP;
                      const feature = map.queryRenderedFeatures({layers: ['native-selected']})[0];
                      if (!feature) return null;
                      const coordinates = feature.geometry.coordinates;
                      const coordinate = coordinates[0];
                      const screen = map.project(coordinate);
                      const rect = map.getContainer().getBoundingClientRect();
                      return {x: screen.x + rect.left, y: screen.y + rect.top};
                    }"""
                )
                assert point is not None
                page.mouse.click(point["x"], point["y"])
                page.wait_for_selector(".maplibregl-popup")
                assert "selected-alignment" in page.locator("#native-feature-details").inner_text()
                assert page.locator(".maplibregl-popup").count() == 1
                map_box = page.locator("#native-map").bounding_box()
                assert map_box and map_box["width"] > 0 and map_box["height"] > 0
                page.mouse.move(
                    map_box["x"] + map_box["width"] - 8, map_box["y"] + map_box["height"] - 8
                )
                assert "selected-alignment" in page.locator("#native-feature-details").inner_text()
                if viewport["width"] < 720:
                    panel_box = page.locator(".native-panel").bounding_box()
                    map_wrap_box = page.locator(".native-map-wrap").bounding_box()
                    assert panel_box and map_wrap_box and map_wrap_box["y"] < panel_box["y"]

                page.locator("input[data-layer-toggle='selected-alignment']").uncheck()
                page.wait_for_function(
                    "() => window.SATN_NATIVE_MAP.getLayoutProperty('native-selected', "
                    "'visibility') === 'none'"
                )
                page.wait_for_function(
                    "() => window.SATN_NATIVE_MAP.queryRenderedFeatures({layers: "
                    "['native-selected']}).length === 0"
                )
                assert (
                    page.evaluate(
                        "() => window.SATN_NATIVE_MAP.queryRenderedFeatures({layers: "
                        "['native-selected']}).length"
                    )
                    == 0
                )
                page.close()
                context.close()
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.parametrize(
    ("omit_source_geometry", "omit_departure_geometry", "message"),
    [
        (True, False, "source baseline geometry"),
        (False, True, "departure geometry"),
    ],
)
def test_native_rendering_gate_rejects_text_only_source_or_departure_sections(
    tmp_path: Path,
    omit_source_geometry: bool,
    omit_departure_geometry: bool,
    message: str,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    _write_native_catalogue(catalogue)
    _write_native_bundle(
        bundles,
        omit_source_geometry=omit_source_geometry,
        omit_departure_geometry=omit_departure_geometry,
    )
    result = package_pages(
        catalogue,
        bundles,
        tmp_path / "pages",
        tmp_path / "satn-pages.zip",
    )
    network_path = result.pages_directory / "deployments" / "native-area" / "decision-map.geojson"
    network = json.loads(network_path.read_text(encoding="utf-8"))
    if omit_source_geometry:
        network["features"] = [
            feature
            for feature in network["features"]
            if feature["properties"].get("kind") != "source-baseline"
        ]
    if omit_departure_geometry:
        network["features"] = [
            feature
            for feature in network["features"]
            if feature["properties"].get("kind") != "a-road-departure"
        ]
    network_path.write_text(json.dumps(network), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        VALIDATOR.validate_pages_rendering(result.pages_directory)
