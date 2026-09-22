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
                },
                "geometry": {"type": "LineString", "coordinates": [[-2, 51], [-1.9, 51.1]]},
            },
            {
                "type": "Feature",
                "properties": {
                    "kind": "provisional-alignment",
                    "decision_id": "decision:provisional",
                    "candidate_id": "candidate:provisional",
                },
                "geometry": {"type": "LineString", "coordinates": [[-1.9, 51.1], [-1.8, 51.2]]},
            },
            {
                "type": "Feature",
                "properties": {
                    "kind": "unresolved-decision",
                    "decision_id": "decision:unresolved",
                },
                "geometry": {"type": "LineString", "coordinates": [[-1.8, 51.2], [-1.7, 51.3]]},
            },
            {
                "type": "Feature",
                "properties": {
                    "kind": "a-road-departure",
                    "decision_id": "decision:selected",
                    "alternative_candidate_id": "candidate:strategic",
                },
                "geometry": {"type": "LineString", "coordinates": [[-2, 51], [-1.8, 51.2]]},
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
    source_geometry = (
        ""
        if omit_source_geometry
        else """
    <polyline class="source-baseline source-strategic" data-map-layer="source-strategic"
      points="0,100 100,100" aria-label="A-road source baseline"></polyline>
"""
    )
    departure_geometry = (
        ""
        if omit_departure_geometry
        else """
    <polyline class="a-road-departure" data-native-departure-geometry
      data-map-layer="a-road-departure"
      points="120,0 120,80" aria-label="A-road departure"></polyline>
"""
    )
    html = (
        """<!doctype html>
<html data-native-ready="false"><body data-native-publication="native-agentic"
  data-network-url="decision-map.geojson" data-branch="review-branch" data-base-id="base-1">
<main>
  <p data-native-branch>review-branch</p>
  <svg aria-label="Strategic geometry">
    """
        + source_geometry
        + """
    <polyline class="selected-alignment" data-map-layer="selected-alignment"
      points="0,0 100,100" aria-label="selected-alignment"></polyline>
    <polyline class="provisional-alignment" data-map-layer="provisional-alignment"
      points="100,100 200,200" aria-label="provisional-alignment"></polyline>
    <polyline class="unresolved-decision" data-map-layer="unresolved-decision"
      points="200,200 300,300" aria-label="unresolved-decision"></polyline>
    """
        + departure_geometry
        + """
  </svg>
  <ul>
    <li data-native-source-baseline>A-road source baseline</li>
    <li data-native-decision-kind="selected-alignment">Selected alignment</li>
    <li data-native-decision-kind="provisional-alignment">Provisional alignment</li>
    <li data-native-decision-kind="unresolved-decision">Unresolved decision</li>
    <li data-native-departure>A-road departure</li>
  </ul>
</main>
<script>
fetch("decision-map.geojson").then(response => response.json()).then(network => {
  window.SATN_NATIVE_NETWORK = network;
  document.documentElement.dataset.nativeNetworkLoaded = "true";
  document.documentElement.dataset.nativeReady = "true";
}).catch(() => {
  document.body.textContent = "Native map failed to load its public GeoJSON.";
});
</script>
</body></html>
"""
    )
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

    with pytest.raises(ValueError, match=message):
        VALIDATOR.validate_pages_rendering(result.pages_directory)
