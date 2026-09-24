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
    include_transfer_candidate: bool = False,
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
                    "role": "strategic-spine",
                    "decision_id": "decision:selected",
                    "candidate_id": "candidate:selected",
                    "reason": "Selected public evidence alignment",
                    "uncertainties": [],
                    "evidence_refs": ["candidate:selected"],
                },
                "geometry": {"type": "LineString", "coordinates": [[-2, 51], [-1.9, 51.1]]},
            },
            {
                "type": "Feature",
                "properties": {
                    "kind": "provisional-alignment",
                    "role": "strategic-spine",
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
                    "community_access": 0,
                    "community_gaps": 0,
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
    shutil.copy2(PROJECT / "rust" / "src" / "native_bus_context.js", bundle / "bus-context.js")
    template = (PROJECT / "rust" / "src" / "native_map_template.html").read_text(encoding="utf-8")
    html = template
    for placeholder, value in {
        "__BUS_CONTEXT_URL__": "bus-context.geojson" if include_transfer_candidate else "",
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
    if include_transfer_candidate:
        (bundle / "bus-context.geojson").write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "service_date": "2026-09-23",
                    "licence": "Fixture OGL",
                    "attribution": "Fixture transit data",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {
                                "kind": "bus-route",
                                "route_ids": ["fixture-route-91"],
                                "route_short_names": ["X91"],
                                "service_date": "2026-09-23",
                                "source_id": "fixture-gtfs-route",
                            },
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[-1.68, 51.15], [-1.67, 51.16]],
                            },
                        },
                        {
                            "type": "Feature",
                            "properties": {
                                "kind": "bus-interchange",
                                "name": "Fixture Bus Station",
                                "facility_type": "bus station",
                                "source_id": "fixture-stop-area",
                            },
                            "geometry": {"type": "Point", "coordinates": [-1.66, 51.17]},
                        },
                        {
                            "type": "Feature",
                            "properties": {
                                "kind": "bus-interchange",
                                "facility_type": "timetable-supported transfer candidate",
                                "transfer_candidate": True,
                                "transfer_candidate_basis": (
                                    "Multiple distinct active scheduled bus routes serve "
                                    "this selected stop."
                                ),
                                "source_id": "fixture-naptan-stop",
                                "source_title": "Fixture NaPTAN source",
                                "service_source_id": "fixture-gtfs",
                                "atco_code": "0180BAC30834",
                                "name": "Victoria Hall",
                                "locality": "Radstock",
                                "service_date": "2026-09-23",
                                "route_ids": ["fixture-route-101", "fixture-route-172"],
                                "route_short_names": ["101", "172"],
                                "pickup_type_codes": ["0", "1"],
                                "drop_off_type_codes": ["0"],
                            },
                            "geometry": {"type": "Point", "coordinates": [-1.64, 51.18]},
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
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
                    "community_access": 0,
                    "community_gaps": 0,
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


def _make_on_spine_decision_point(bundle: Path, *, include_access: bool) -> None:
    network_path = bundle / "decision-map.geojson"
    network = json.loads(network_path.read_text(encoding="utf-8"))
    selected = next(
        feature
        for feature in network["features"]
        if feature["properties"].get("kind") == "selected-alignment"
    )
    point = [-1.95, 51.05]
    selected["geometry"] = {"type": "Point", "coordinates": point}
    selected["properties"].update(
        {
            "community_id": "community:on-spine",
            "root_spine_id": "source:spine",
            "new_link_length_m": -0.0,
            "full_access_length_m": 0.0,
        }
    )
    if include_access:
        selected["properties"]["access_status"] = "on-spine"
        network["features"].append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "community-access",
                    "community_id": "community:on-spine",
                    "status": "on-spine",
                    "root_spine_id": "source:spine",
                    "joined_spine_id": "source:spine",
                    "new_link_length_m": -0.0,
                    "full_access_length_m": 0.0,
                },
                "geometry": {"type": "Point", "coordinates": point},
            }
        )
    network_path.write_text(json.dumps(network), encoding="utf-8")
    for filename in ("decision-map.json", "publication.json"):
        path = bundle / filename
        document = json.loads(path.read_text(encoding="utf-8"))
        document["counts"]["community_access"] = int(include_access)
        path.write_text(json.dumps(document), encoding="utf-8")


def _add_serialized_urban_entry_features(bundle: Path) -> None:
    network_path = bundle / "decision-map.geojson"
    network = json.loads(network_path.read_text(encoding="utf-8"))
    urban_entry = {
        "kind": "urban-entry",
        "destination_id": "place:bath",
        "destination_name": "Bath",
        "extent_source_id": "osm:relation:5342409",
        "crossing_edge_id": "edge:urban-entry",
        "crossing_fraction": 0.5,
        "crossing_point": [-1.75, 51.2],
    }
    topography = {
        "availability": "available",
        "forward_ascent_m": 120.0,
        "forward_descent_m": 80.0,
        "cumulative_elevation_variation_m": 200.0,
        "estimated_moving_time": {
            "availability": "available",
            "minutes": 14.5,
            "seconds": 870.0,
            "model": "BRouter Trekking v1.7.10",
        },
        "hill_neutral_moving_time": {
            "label": "Hill-neutral sensitivity (not an e-bike ETA)",
            "minutes": 11.2,
            "seconds": 672.0,
            "model": "BRouter Trekking v1.7.10",
        },
    }
    selected = next(
        feature
        for feature in network["features"]
        if feature["properties"].get("kind") == "selected-alignment"
    )
    selected["properties"].update(
        {
            "community_id": "community:urban",
            "community_name": "Englishcombe",
            "terminal_kind": "urban-entry",
            "terminal_source_id": "osm:relation:5342409",
            "urban_entry": json.dumps(urban_entry),
            "full_access_topography": json.dumps(topography),
        }
    )
    network["features"].append(
        {
            "type": "Feature",
            "properties": {
                "kind": "community-access",
                "community_id": "community:urban",
                "name": "Englishcombe",
                "status": "served",
                "decision_class": "mechanical",
                "is_primary": True,
                "terminal_kind": "urban-entry",
                "terminal_source_id": "osm:relation:5342409",
                "urban_entry": json.dumps(urban_entry),
                "full_access_topography": json.dumps(topography),
                "new_link_length_m": 793.964,
                "full_access_length_m": 793.964,
                "provision_status": "unknown",
            },
            "geometry": {"type": "Point", "coordinates": [-1.75, 51.2]},
        }
    )
    network_path.write_text(json.dumps(network), encoding="utf-8")
    for filename in ("publication.json", "decision-map.json"):
        path = bundle / filename
        document = json.loads(path.read_text(encoding="utf-8"))
        document["counts"]["community_access"] = 1
        path.write_text(json.dumps(document), encoding="utf-8")


def test_package_pages_accepts_a_valid_zero_length_on_spine_decision_point(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    _write_native_catalogue(catalogue)
    _write_native_bundle(bundles)
    _make_on_spine_decision_point(bundles / "native-area", include_access=True)

    result = package_pages(
        catalogue,
        bundles,
        tmp_path / "pages",
        tmp_path / "satn-pages.zip",
    )

    network = json.loads(
        (result.pages_directory / "deployments" / "native-area" / "decision-map.geojson").read_text(
            encoding="utf-8"
        )
    )
    point = next(
        feature
        for feature in network["features"]
        if feature["properties"].get("community_id") == "community:on-spine"
        and feature["properties"].get("kind") == "selected-alignment"
    )
    assert point["geometry"] == {"type": "Point", "coordinates": [-1.95, 51.05]}


def test_package_pages_rejects_an_unproven_zero_length_decision_point(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    _write_native_catalogue(catalogue)
    _write_native_bundle(bundles)
    _make_on_spine_decision_point(bundles / "native-area", include_access=False)

    with pytest.raises(ValueError, match="on-spine decision point"):
        package_pages(
            catalogue,
            bundles,
            tmp_path / "pages",
            tmp_path / "satn-pages.zip",
        )


@pytest.mark.browser
def test_native_rendering_gate_accepts_a_valid_on_spine_decision_point(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    _write_native_catalogue(catalogue)
    _write_native_bundle(bundles)
    _make_on_spine_decision_point(bundles / "native-area", include_access=True)
    result = package_pages(
        catalogue,
        bundles,
        tmp_path / "pages",
        tmp_path / "satn-pages.zip",
    )

    validated = VALIDATOR.validate_pages_rendering(result.pages_directory)

    assert validated[0].strategic_spines == 2
    assert validated[0].rendered_strategic_spines == 3


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


def test_package_pages_accepts_legacy_native_decision_counts(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    _write_native_catalogue(catalogue)
    _write_native_bundle(bundles)
    bundle = bundles / "native-area"
    for filename in ("publication.json", "decision-map.json"):
        path = bundle / filename
        document = json.loads(path.read_text(encoding="utf-8"))
        document["counts"].pop("community_access")
        document["counts"].pop("community_gaps")
        path.write_text(json.dumps(document), encoding="utf-8")

    result = package_pages(
        catalogue,
        bundles,
        tmp_path / "pages",
        tmp_path / "satn-pages.zip",
    )

    assert (result.pages_directory / "deployments" / "native-area" / "publication.json").is_file()


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
    unrelated = server_root / "other-region"
    unrelated.mkdir()
    (unrelated / "index.html").write_text(
        """<!doctype html><html><body data-load-count="0"><script>
const count = Number(sessionStorage.getItem("other-region-loads") || "0") + 1;
sessionStorage.setItem("other-region-loads", String(count));
document.body.dataset.loadCount = String(count);
</script></body></html>
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
            unrelated_page = context.new_page()
            unrelated_page.goto(
                f"http://127.0.0.1:{server.server_port}/other-region/",
                wait_until="domcontentloaded",
            )
            unrelated_page.wait_for_function("document.body.dataset.loadCount === '1'")
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
            page.wait_for_function("document.documentElement.dataset.nativeReady === 'true'")
            assert unrelated_page.evaluate("document.body.dataset.loadCount") == "1"
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
def test_native_rendering_gate_checks_readable_transfer_schedule_evidence(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    _write_native_catalogue(catalogue)
    _write_native_bundle(bundles, include_transfer_candidate=True)
    result = package_pages(
        catalogue,
        bundles,
        tmp_path / "pages",
        tmp_path / "satn-pages.zip",
    )
    published_bundle = result.pages_directory / "deployments" / "native-area"
    shutil.copy2(bundles / "native-area" / "bus-context.geojson", published_bundle)
    shutil.copy2(bundles / "native-area" / "bus-context.js", published_bundle)

    validated = VALIDATOR.validate_pages_rendering(result.pages_directory)

    assert len(validated) == 1
    assert validated[0].deployment_id == "native-area"


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
                layout = page.evaluate(
                    """() => {
                      const panel = document.querySelector('.native-panel');
                      const summary = document.querySelector('#native-feature-details');
                      const layers = document.querySelector('.native-panel fieldset');
                      return {
                        shellBottom: Math.round(
                          document.querySelector('.native-shell').getBoundingClientRect().bottom
                        ),
                        viewportHeight: window.innerHeight,
                        documentHeight: document.documentElement.scrollHeight,
                        panelTabIndex: panel.getAttribute('tabindex'),
                        panelOverflowY: getComputedStyle(panel).overflowY,
                        summaryBeforeLayers: Boolean(
                          summary.compareDocumentPosition(layers) &
                          Node.DOCUMENT_POSITION_FOLLOWING
                        )
                      };
                    }"""
                )
                assert layout["summaryBeforeLayers"]
                if viewport["width"] >= 720:
                    assert layout["shellBottom"] == round(layout["viewportHeight"])
                    assert layout["documentHeight"] == layout["viewportHeight"]
                    assert layout["panelTabIndex"] == "0"
                    assert layout["panelOverflowY"] == "auto"
                assert not page.locator(
                    "input[data-layer-toggle='candidate-alternative']"
                ).is_visible()
                default_layers = page.evaluate(
                    """() => {
                      const map = window.SATN_NATIVE_MAP;
                      const toggles = [...document.querySelectorAll('[data-layer-toggle]')];
                      const features = map.queryRenderedFeatures({
                        layers: ['native-strategic-network']
                      });
                      return {
                        checked: toggles.filter(toggle => toggle.checked)
                          .map(toggle => toggle.dataset.layerToggle),
                        visibility: map.getLayoutProperty(
                          'native-strategic-network', 'visibility'
                        ),
                        color: map.getPaintProperty('native-strategic-network', 'line-color'),
                        features: features.map(feature => ({
                          kind: feature.properties.kind,
                          geometry: feature.geometry.type,
                          hasCommunity: Object.hasOwn(feature.properties, 'community_id')
                        }))
                      };
                    }"""
                )
                assert default_layers["checked"] == ["strategic-network"]
                assert default_layers["visibility"] == "visible"
                assert default_layers["color"] == "#d71920"
                assert default_layers["features"]
                assert all(
                    feature["kind"] in {"selected-alignment", "provisional-alignment"}
                    and feature["geometry"] == "LineString"
                    and not feature["hasCommunity"]
                    for feature in default_layers["features"]
                )

                page.locator("input[data-layer-toggle='selected-alignment']").check()
                page.locator("input[data-layer-toggle='unresolved-decision']").check()

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
                      return {x: screen.x + rect.left, y: screen.y + rect.top,
                        mapX: screen.x, mapY: screen.y};
                    }"""
                )
                assert point is not None
                summary_text = page.locator("#native-feature-details").inner_text()
                assert "Hover a map feature" in summary_text
                assert "Click to pin" in summary_text
                page.mouse.click(point["x"], point["y"])
                page.wait_for_selector(".maplibregl-popup")
                assert (
                    "selected-alignment" in page.locator(".maplibregl-popup-content").inner_text()
                )
                assert "strategic-spine" in page.locator(".maplibregl-popup-content").inner_text()
                assert page.locator(".maplibregl-popup").count() == 1
                page.locator(".maplibregl-popup-content details summary").click()
                popup_text = page.locator(".maplibregl-popup-content").inner_text()
                assert "candidate:selected" in popup_text
                assert "[]" not in popup_text
                assert "[" not in popup_text
                unresolved_point = page.evaluate(
                    """() => {
                      const map = window.SATN_NATIVE_MAP;
                      const feature = map.queryRenderedFeatures({layers: ['native-unresolved']})[0];
                      if (!feature) return null;
                      const rect = map.getContainer().getBoundingClientRect();
                      const lines = feature.geometry.type === 'MultiLineString'
                        ? feature.geometry.coordinates
                        : [feature.geometry.coordinates];
                      const candidates = [];
                      lines.forEach(line => line.forEach((coordinate, index) => {
                        candidates.push(coordinate);
                        if (index + 1 < line.length) {
                          candidates.push([
                            (coordinate[0] + line[index + 1][0]) / 2,
                            (coordinate[1] + line[index + 1][1]) / 2
                          ]);
                        }
                      }));
                      for (const coordinate of candidates) {
                        const screen = map.project(coordinate);
                        const hits = map.queryRenderedFeatures(screen, {
                          layers: ['native-unresolved']
                        });
                        if (!hits.some(hit =>
                          hit.properties.decision_id === feature.properties.decision_id
                        )) continue;
                        const x = screen.x + rect.left;
                        const y = screen.y + rect.top;
                        if (document.elementFromPoint(x, y) !== map.getCanvas()) continue;
                        return {x, y, mapX: screen.x, mapY: screen.y};
                      }
                      return null;
                    }"""
                )
                assert unresolved_point is not None
                page.mouse.move(unresolved_point["x"], unresolved_point["y"])
                assert (
                    "selected-alignment" in page.locator(".maplibregl-popup-content").inner_text()
                )
                assert page.locator("#native-feature-details").inner_text() == summary_text
                page.mouse.click(unresolved_point["x"], unresolved_point["y"])
                page.wait_for_function(
                    "() => document.querySelector('.maplibregl-popup-content')?.innerText "
                    ".includes('unresolved-decision')"
                )
                assert page.locator(".maplibregl-popup").count() == 1
                page.mouse.move(point["x"], point["y"])
                assert (
                    "unresolved-decision" in page.locator(".maplibregl-popup-content").inner_text()
                )
                assert page.locator("#native-feature-details").inner_text() == summary_text
                blank_point = [8, 8]
                assert page.evaluate(
                    "point => window.SATN_NATIVE_MAP.queryRenderedFeatures(point).length === 0",
                    blank_point,
                )
                map_box = page.locator("#native-map").bounding_box()
                assert map_box and map_box["width"] > 0 and map_box["height"] > 0
                page.mouse.click(map_box["x"] + blank_point[0], map_box["y"] + blank_point[1])
                page.wait_for_function("() => !document.querySelector('.maplibregl-popup')")
                page.wait_for_function(
                    "point => window.SATN_NATIVE_MAP.queryRenderedFeatures(point, "
                    "{layers: ['native-highlight-line']}).length === 0",
                    arg=[unresolved_point["mapX"], unresolved_point["mapY"]],
                )
                assert page.locator("#native-feature-details").inner_text() == summary_text
                page.mouse.move(point["x"], point["y"])
                page.wait_for_function(
                    "() => document.querySelector('.maplibregl-popup-content')?.innerText "
                    ".includes('selected-alignment')"
                )
                assert page.locator("#native-feature-details").inner_text() == summary_text
                page.mouse.click(unresolved_point["x"], unresolved_point["y"])
                page.wait_for_function(
                    "() => document.querySelector('.maplibregl-popup-content')?.innerText "
                    ".includes('unresolved-decision')"
                )
                page.locator("[data-native-clear]").click()
                page.wait_for_function("() => !document.querySelector('.maplibregl-popup')")
                page.locator("#native-map").scroll_into_view_if_needed()
                map_box = page.locator("#native-map").bounding_box()
                assert map_box
                page.mouse.move(map_box["x"] + point["mapX"], map_box["y"] + point["mapY"])
                page.wait_for_function(
                    "() => document.querySelector('.maplibregl-popup-content')?.innerText "
                    ".includes('selected-alignment')"
                )
                assert page.locator("#native-feature-details").inner_text() == summary_text
                page.locator("[data-native-clear]").click()
                page.wait_for_function("() => !document.querySelector('.maplibregl-popup')")
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
def test_native_map_labels_serialized_urban_entry_without_spine_claim(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    _write_native_catalogue(catalogue)
    _write_native_bundle(bundles)
    _add_serialized_urban_entry_features(bundles / "native-area")
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
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"{origin}/deployments/native-area/index.html", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => document.documentElement.dataset.nativeReady === 'true' && "
                "window.SATN_NATIVE_MAP?.isStyleLoaded()"
            )
            assert "Community Connections" in page.locator(".native-panel").inner_text()
            assert not page.evaluate(
                """() => window.SATN_NATIVE_MAP.queryRenderedFeatures({
                  layers: ['native-strategic-network']
                }).some(feature => feature.properties.decision_id === 'decision:selected')"""
            )
            page.locator("input[data-layer-toggle='selected-alignment']").check()
            page.locator("input[data-layer-toggle='community-access']").check()
            page.wait_for_function(
                "() => window.SATN_NATIVE_MAP.queryRenderedFeatures({layers: "
                "['native-community-access-point']}).length > 0"
            )

            def click_feature(layer: str) -> None:
                page.wait_for_function(
                    """layer => {
                      const map = window.SATN_NATIVE_MAP;
                    return map && !map.isMoving() &&
                        map.queryRenderedFeatures({layers: [layer]}).length > 0;
                    }""",
                    arg=layer,
                )
                point = page.evaluate(
                    """layer => {
                      const map = window.SATN_NATIVE_MAP;
                      const feature = map.queryRenderedFeatures({layers: [layer]})[0];
                      if (!feature) return null;
                      const coordinates = feature.geometry.type === 'Point'
                        ? feature.geometry.coordinates
                        : feature.geometry.coordinates.length === 2
                          ? [
                              (feature.geometry.coordinates[0][0] +
                                feature.geometry.coordinates[1][0]) / 2,
                              (feature.geometry.coordinates[0][1] +
                                feature.geometry.coordinates[1][1]) / 2,
                            ]
                          : feature.geometry.coordinates[0];
                      const screen = map.project(coordinates);
                      if (!map.queryRenderedFeatures(
                        [screen.x, screen.y], {layers: [layer]}
                      ).length) return null;
                      const rect = map.getContainer().getBoundingClientRect();
                      return {x: screen.x + rect.left, y: screen.y + rect.top};
                    }""",
                    layer,
                )
                assert point is not None
                page.mouse.click(point["x"], point["y"])
                page.wait_for_selector(".maplibregl-popup")

            summary_text = page.locator("#native-feature-details").inner_text()
            click_feature("native-community-access-point")
            community_popup = page.locator(".maplibregl-popup-content").inner_text()
            assert "Urban entry to Bath" in community_popup
            assert "Urban extent source" in community_popup
            assert "osm:relation:5342409" in community_popup
            assert "estimated moving time 14.5 min" in community_popup
            assert "Hill-neutral sensitivity (not an e-bike ETA): 11.2 min" in community_popup
            assert "Primary spine access" not in community_popup
            assert page.locator("#native-feature-details").inner_text() == summary_text

            page.locator(".maplibregl-popup-close-button").click()
            page.locator("[data-native-clear]").click()
            click_feature("native-selected")
            selected_popup = page.locator(".maplibregl-popup-content").inner_text()
            assert "Urban entry to Bath" in selected_popup
            assert "Primary spine access" not in selected_popup
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
