from __future__ import annotations

import copy
import json
import shutil
import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, sync_playwright

from satn.constants import DISCLAIMER
from satn.deployment import build_area_deployment
from satn.deployment_provenance import generate_lock
from satn.filesystem_safety import publication_destination_authority
from satn.models import CouncilConfig
from satn.pipeline import compile
from satn.sources import snapshot

PROJECT = Path(__file__).parents[1]
BROWSER_TIMEOUT_MS = 10_000


class _DeploymentHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        self.server.request_counts[path] += 1  # type: ignore[attr-defined]
        self.server.request_started[path].append(time.monotonic())  # type: ignore[attr-defined]
        delay = self.server.delays.pop(path, 0)  # type: ignore[attr-defined]
        if delay:
            time.sleep(delay)
        if self.server.failures[path]:  # type: ignore[attr-defined]
            self.server.failures[path] -= 1  # type: ignore[attr-defined]
            self.send_error(503, "planned test failure")
            return
        super().do_GET()

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _serve_deployment(root: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_DeploymentHandler, directory=str(root)))
    server.failures = Counter()  # type: ignore[attr-defined]
    server.delays = {}  # type: ignore[attr-defined]
    server.request_counts = Counter()  # type: ignore[attr-defined]
    server.request_started = defaultdict(list)  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _write_collection(path: Path, features: list[dict[str, object]]) -> dict[str, object]:
    encoded = json.dumps(
        {"type": "FeatureCollection", "features": features}, separators=(",", ":")
    ).encode()
    path.write_bytes(encoded)
    return {
        "path": path.relative_to(path.parents[1]).as_posix(),
        "size_bytes": len(encoded),
        "feature_count": len(features),
        "bbox": None,
    }


def _format_bytes(bytes_: int) -> str:
    if bytes_ < 1024:
        return f"{bytes_} B"
    if bytes_ < 1024**2:
        return f"{bytes_ / 1024:.0f} KB"
    return f"{bytes_ / 1024**2:.1f} MB"


def _split_school_shards(deployment: Path) -> tuple[str, str, str, str, str, str]:
    manifest_path = deployment / "layer-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    group = manifest["groups"]["schools"]
    school_type = group["types"]["school"]
    features = [
        feature
        for entry in school_type["shards"]
        for feature in json.loads((deployment / entry["path"]).read_text())["features"]
    ]
    assert features
    seed = features[0]
    while len(features) < 3:
        duplicate = copy.deepcopy(seed)
        duplicate["id"] = f"browser-school-contextual-shard-{len(features) + 1}"
        duplicate["properties"]["name"] = "Browser test school context"
        features.append(duplicate)
    first, second, third = features[0:1], features[1:2], features[2:3]
    layers = deployment / "layers"
    first_entry = _write_collection(layers / "schools-browser-first.geojson", first)
    second_entry = _write_collection(layers / "schools-browser-second.geojson", second)
    third_entry = _write_collection(layers / "schools-browser-third.geojson", third)
    # The default viewport request should fetch only the local first shard;
    # the deliberate whole-region control must fetch both remote shards.
    second_entry["bbox"] = [10, 10, 11, 11]
    third_entry["bbox"] = [12, 12, 13, 13]
    school_type["shards"] = [first_entry, second_entry, third_entry]
    school_type["feature_count"] = len(features)
    school_type["size_bytes"] = (
        first_entry["size_bytes"] + second_entry["size_bytes"] + third_entry["size_bytes"]
    )
    group["shards"] = [
        entry
        for type_metadata in group["types"].values()
        for entry in type_metadata["shards"]
    ]
    group["feature_count"] = sum(
        type_metadata["feature_count"] for type_metadata in group["types"].values()
    )
    group["size_bytes"] = sum(
        type_metadata["size_bytes"] for type_metadata in group["types"].values()
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return (
        str(first_entry["path"]),
        str(second_entry["path"]),
        str(third_entry["path"]),
        str(first[0]["id"]),
        str(second[0]["id"]),
        str(third[0]["id"]),
    )


def _split_retail_shards(
    deployment: Path, manifest: dict[str, object]
) -> list[str]:
    group = manifest["groups"]["amenities"]
    retail_type = group["types"]["retail-centre"]
    source_entry = retail_type["shards"][0]
    seed = json.loads((deployment / source_entry["path"]).read_text())["features"][0]
    entries = []
    for index in range(3):
        feature = copy.deepcopy(seed)
        feature["id"] = f"browser-retail-context-{index + 1}"
        entry = _write_collection(
            deployment / "layers" / f"amenities-browser-retail-{index + 1}.geojson",
            [feature],
        )
        entry["bbox"] = None
        entries.append(entry)
    retail_type["shards"] = entries
    retail_type["feature_count"] = len(entries)
    retail_type["size_bytes"] = sum(entry["size_bytes"] for entry in entries)
    group["shards"] = [
        entry
        for type_metadata in group["types"].values()
        for entry in type_metadata["shards"]
    ]
    group["feature_count"] = sum(
        type_metadata["feature_count"] for type_metadata in group["types"].values()
    )
    group["size_bytes"] = sum(
        type_metadata["size_bytes"] for type_metadata in group["types"].values()
    )
    return [str(entry["path"]) for entry in entries]


def _add_unavailable_topography(deployment: Path) -> str:
    manifest_path = deployment / "topography-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    source_entry = manifest["overview"][0]
    feature = copy.deepcopy(
        json.loads((deployment / source_entry["path"]).read_text())["features"][0]
    )
    feature["id"] = "browser-unavailable-topography"
    feature["properties"].update(
        {
            "feature_type": "topography-profile",
            "profile_id": "browser-unavailable-topography",
            "evidence_status": "evidence-unavailable",
        }
    )
    entry = _write_collection(
        deployment / "topography" / "browser-unavailable-topography.geojson", [feature]
    )
    manifest["detail"].append(entry)
    manifest["detail_feature_count"] += 1
    manifest["unavailable_profile_count"] += 1
    manifest["detail_size_bytes"] += entry["size_bytes"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return str(feature["id"])


def _fit_map_to_feature(page: Page, feature_id: str) -> None:
    """Bring a feature into the map viewport before asserting rendered output."""
    page.evaluate(
        f"""() => {{
          const map = window.SATN_REVIEW_MAP;
          const feature = map.getSource('topography')._data.features
            .find(candidate => candidate.id === {feature_id!r});
          const coordinates = [];
          const collectCoordinates = (coordinate) => {{
            if (typeof coordinate[0] === 'number') coordinates.push(coordinate);
            else coordinate.forEach(collectCoordinates);
          }};
          collectCoordinates(feature.geometry.coordinates);
          const longitudes = coordinates.map(([longitude]) => longitude);
          const latitudes = coordinates.map(([, latitude]) => latitude);
          map.fitBounds(
            [[Math.min(...longitudes), Math.min(...latitudes)],
             [Math.max(...longitudes), Math.max(...latitudes)]],
            {{padding: 100, maxZoom: 12, duration: 0}},
          );
        }}"""
    )
    page.wait_for_function(
        "window.SATN_REVIEW_MAP.loaded() && !window.SATN_REVIEW_MAP.isMoving()",
        timeout=BROWSER_TIMEOUT_MS,
    )


@pytest.mark.browser
def test_area_deployment_batches_deferred_shards_before_updating_map_source(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns(".satn-cache"),
    )
    definition = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(definition)
    compile(definition)
    run_path = fixture / "work" / "output" / "run.json"
    run = json.loads(run_path.read_text())
    run.setdefault("compilation_diagnostics", {})
    run_path.write_text(json.dumps(run), encoding="utf-8")
    deployment = tmp_path / "deployment"
    deployment_authority = publication_destination_authority(workspace_root=tmp_path)
    build_area_deployment(
        definition,
        deployment,
        bootstrap=True,
        publication_authority=deployment_authority,
    )
    generate_lock(definition, deployment=deployment)
    deployment = build_area_deployment(
        definition,
        deployment,
        publication_authority=deployment_authority,
    )
    template = (PROJECT / "src" / "satn" / "assets" / "review-map.html").read_text()
    replacements = {
        "__TITLE__": definition.publication.title,
        "__DISCLAIMER__": DISCLAIMER,
        "__REVIEW_MAP_CSS__": "review-map.css",
        "__REVIEW_MAP_JS__": "review-map.js",
        "__ATM_STATE__": "disabled",
        "__ATM_STATUS__": "No governed local ATM data loaded.",
        "__GENTLE_MAX_PCT__": "3",
        "__NOTICEABLE_MAX_PCT__": "5",
        "__STEEP_MAX_PCT__": "8",
        "__VERY_STEEP_MAX_PCT__": "12.5",
    }
    for token, replacement in replacements.items():
        template = template.replace(token, replacement)
    (deployment / "index.html").write_text(template, encoding="utf-8")
    (deployment / "assets" / "review-map.js").write_text(
        (PROJECT / "src" / "satn" / "assets" / "review-map.js").read_text(),
        encoding="utf-8",
    )
    manifest_path = deployment / "layer-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    retail_paths = _split_retail_shards(deployment, manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with (
        _serve_deployment(deployment) as (server, origin),
        sync_playwright() as playwright,
        playwright.chromium.launch(headless=True) as browser,
        browser.new_context(viewport={"width": 1280, "height": 900}) as context,
    ):
        context.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page = context.new_page()
        page.goto(f"{origin}/index.html", timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_function(
            "document.documentElement.dataset.mapReady === 'true'",
            timeout=BROWSER_TIMEOUT_MS,
        )
        page.evaluate(
            """() => {
              const source = window.SATN_REVIEW_MAP.getSource('network');
              const setData = source.setData.bind(source);
              window.SATN_NETWORK_SET_DATA_CALLS = 0;
              source.setData = (data) => {
                window.SATN_NETWORK_SET_DATA_CALLS += 1;
                return setData(data);
              };
            }"""
        )
        page.locator("#layer-retail-centres").click(timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_function(
            "document.querySelector('#layer-retail-centres').closest('label')."
            "querySelector('.layer-load-status').textContent.includes('loaded')",
            timeout=BROWSER_TIMEOUT_MS,
        )
        assert page.evaluate("window.SATN_NETWORK_SET_DATA_CALLS") == 1
        assert all(server.request_counts[f"/{path}"] == 1 for path in retail_paths)


@pytest.mark.browser
def test_area_deployment_progressive_loading_recovers_without_losing_data(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns(".satn-cache"),
    )
    definition = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(definition)
    compile(definition)
    run_path = fixture / "work" / "output" / "run.json"
    run = json.loads(run_path.read_text())
    run.setdefault("compilation_diagnostics", {})
    run_path.write_text(json.dumps(run), encoding="utf-8")
    deployment = tmp_path / "deployment"
    deployment_authority = publication_destination_authority(workspace_root=tmp_path)
    build_area_deployment(
        definition,
        deployment,
        bootstrap=True,
        publication_authority=deployment_authority,
    )
    generate_lock(definition, deployment=deployment)
    deployment = build_area_deployment(
        definition,
        deployment,
        publication_authority=deployment_authority,
    )
    template = (PROJECT / "src" / "satn" / "assets" / "review-map.html").read_text()
    replacements = {
        "__TITLE__": definition.publication.title,
        "__DISCLAIMER__": DISCLAIMER,
        "__REVIEW_MAP_CSS__": "review-map.css",
        "__REVIEW_MAP_JS__": "review-map.js",
        "__ATM_STATE__": "disabled",
        "__ATM_STATUS__": "No governed local ATM data loaded.",
        "__GENTLE_MAX_PCT__": "3",
        "__NOTICEABLE_MAX_PCT__": "5",
        "__STEEP_MAX_PCT__": "8",
        "__VERY_STEEP_MAX_PCT__": "12.5",
    }
    for token, replacement in replacements.items():
        template = template.replace(token, replacement)
    (deployment / "index.html").write_text(template, encoding="utf-8")
    (deployment / "assets" / "review-map.js").write_text(
        (PROJECT / "src" / "satn" / "assets" / "review-map.js").read_text(),
        encoding="utf-8",
    )
    (
        first_school_shard,
        second_school_shard,
        third_school_shard,
        first_school_id,
        second_school_id,
        third_school_id,
    ) = _split_school_shards(deployment)
    manifest = json.loads((deployment / "layer-manifest.json").read_text())
    school_street_paths = [
        entry["path"]
        for entry in manifest["groups"]["schools"]["types"]["school-street-assessment"]["shards"]
    ]
    school_size_bytes = manifest["groups"]["schools"]["types"]["school"]["size_bytes"]
    school_shard_sizes = {
        entry["path"]: entry["size_bytes"]
        for entry in manifest["groups"]["schools"]["types"]["school"]["shards"]
    }
    retail_paths = [
        entry["path"]
        for entry in manifest["groups"]["amenities"]["types"]["retail-centre"]["shards"]
    ]
    healthcare_paths = [
        entry["path"]
        for entry in manifest["groups"]["amenities"]["types"]["healthcare"]["shards"]
    ]
    # A stale/older manifest can legitimately lack an optional logical type.
    # Its control must make no sibling request, rather than falling back to the
    # group-wide shard list.
    manifest["groups"]["amenities"]["types"].pop("healthcare")
    (deployment / "layer-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    retail_size_bytes = manifest["groups"]["amenities"]["types"]["retail-centre"]["size_bytes"]
    unavailable_topography_id = _add_unavailable_topography(deployment)

    with (
        _serve_deployment(deployment) as (server, origin),
        sync_playwright() as playwright,
        playwright.chromium.launch(headless=True) as browser,
        browser.new_context(viewport={"width": 1280, "height": 900}) as context,
    ):
        server.failures["/layer-manifest.json"] = 1
        # Let the first-visit core cache settle before showing the planned
        # manifest failure, so its retry state cannot be overwritten by the
        # service-worker progress message.
        server.delays["/layer-manifest.json"] = 2
        server.failures[f"/{third_school_shard}"] = 1
        server.delays[f"/{second_school_shard}"] = 0.2
        server.delays[f"/{third_school_shard}"] = 0.2

        context.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page = context.new_page()
        page.goto(f"{origin}/index.html", timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_function(
            "document.documentElement.dataset.mapReady === 'true'",
            timeout=BROWSER_TIMEOUT_MS,
        )
        assert not any(path.startswith("/topography/") for path in server.request_counts)
        page.wait_for_function(
            "document.querySelector('#deployment-status').textContent."
            "includes('Layer sizes are unavailable')",
            timeout=BROWSER_TIMEOUT_MS,
        )

        page.locator("#layer-urban-spines").click(timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_function(
            "!document.querySelector('#deployment-status').textContent."
            "includes('Layer sizes are unavailable')",
            timeout=BROWSER_TIMEOUT_MS,
        )
        assert "Layer sizes loaded" in page.locator("#deployment-status").inner_text(
            timeout=BROWSER_TIMEOUT_MS
        )
        school_label = page.locator("#layer-schools").locator("xpath=..").inner_text()
        assert _format_bytes(school_size_bytes) in school_label
        retail_label = page.locator("#layer-retail-centres").locator("xpath=..").inner_text()
        assert _format_bytes(retail_size_bytes) in retail_label

        page.locator("#layer-healthcare").click(timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_function(
            "document.querySelector('#layer-healthcare').closest('label')."
            "querySelector('.layer-load-status').textContent."
            "includes('no evidence in this view')",
            timeout=BROWSER_TIMEOUT_MS,
        )
        assert all(server.request_counts[f"/{path}"] == 0 for path in healthcare_paths)
        assert all(server.request_counts[f"/{path}"] == 0 for path in retail_paths)

        page.locator("#layer-schools").click(timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_function(
            "window.SATN_DATA.network.features.some("
            f"feature => feature.id === {first_school_id!r})",
            timeout=BROWSER_TIMEOUT_MS,
        )
        assert server.request_counts[f"/{second_school_shard}"] == 0
        assert server.request_counts[f"/{third_school_shard}"] == 0
        assert all(server.request_counts[f"/{path}"] == 0 for path in school_street_paths)
        assert page.evaluate(
            "() => ['school-access-obligations', 'school-access-connections', "
            "'school-access-gaps'].every(layer => "
            "window.SATN_REVIEW_MAP.getLayoutProperty(layer, 'visibility') === 'visible')"
        )

        page.locator("#load-complete-region").click(timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_function(
            "document.querySelector('#complete-region-status').textContent."
            "includes('could not finish')",
            timeout=BROWSER_TIMEOUT_MS,
        )
        partial_status = page.locator("#complete-region-status").inner_text()
        assert "2 features" in partial_status
        assert _format_bytes(
            school_shard_sizes[first_school_shard] + school_shard_sizes[second_school_shard]
        ) in partial_status
        assert server.request_counts[f"/{second_school_shard}"] == 1
        assert server.request_counts[f"/{third_school_shard}"] == 1
        assert (
            abs(
                server.request_started[f"/{second_school_shard}"][0]
                - server.request_started[f"/{third_school_shard}"][0]
            )
            < 0.15
        )
        page.wait_for_function(
            "window.SATN_DATA.network.features.some("
            f"feature => feature.id === {second_school_id!r})",
            timeout=BROWSER_TIMEOUT_MS,
        )
        assert not page.evaluate(
            f"window.SATN_DATA.network.features.some(feature => feature.id === {third_school_id!r})"
        )
        page.locator("#load-complete-region").click(timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_function(
            "window.SATN_DATA.network.features.some("
            f"feature => feature.id === {third_school_id!r})",
            timeout=BROWSER_TIMEOUT_MS,
        )
        assert server.request_counts[f"/{first_school_shard}"] == 1
        assert server.request_counts[f"/{second_school_shard}"] == 1
        assert server.request_counts[f"/{third_school_shard}"] == 2
        assert all(server.request_counts[f"/{path}"] == 0 for path in school_street_paths)
        assert page.evaluate(
            "() => { const ids = window.SATN_DATA.network.features.map(feature => feature.id); "
            "return ids.length === new Set(ids).size; }"
        )

        # Grouped amenities are independently selected.  A deliberate full
        # region load of retail evidence must not download healthcare shards.
        page.locator("#layer-retail-centres").click(timeout=BROWSER_TIMEOUT_MS)
        page.locator("#load-complete-region").click(timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_function(
            "document.querySelector('#complete-region-status').textContent."
            "includes('Whole-region evidence loaded')",
            timeout=BROWSER_TIMEOUT_MS,
        )
        assert all(server.request_counts[f"/{path}"] == 1 for path in retail_paths)
        assert all(server.request_counts[f"/{path}"] == 0 for path in healthcare_paths)

        page.evaluate("window.SATN_REVIEW_MAP.jumpTo({zoom: 11})")
        page.locator("#layer-gradient-sections").click(timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.getSource('topography')._data.features.some("
            f"feature => feature.id === {unavailable_topography_id!r})",
            timeout=BROWSER_TIMEOUT_MS,
        )
        assert (
            page.evaluate(
                "window.SATN_REVIEW_MAP.getLayoutProperty('topography-unavailable', 'visibility')"
            )
            == "visible"
        )
        _fit_map_to_feature(page, unavailable_topography_id)
        page.wait_for_function(
            f"window.SATN_REVIEW_MAP.queryRenderedFeatures({{layers: ['topography-unavailable']}})"
            f".some(feature => feature.properties.profile_id === {unavailable_topography_id!r})",
            timeout=BROWSER_TIMEOUT_MS,
        )
        assert page.evaluate(
            f"window.SATN_REVIEW_MAP.queryRenderedFeatures({{layers: ['topography-unavailable']}})"
            f".some(feature => feature.id === {unavailable_topography_id!r})"
        )
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.queryRenderedFeatures({layers: ['gradient-sections']})"
            ".length > 0",
            timeout=BROWSER_TIMEOUT_MS,
        )
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.queryRenderedFeatures({layers: ['gradient-sections']})"
            ".every(feature => feature.id === feature.properties.section_id)"
        )
        unavailable_point = page.evaluate(
            f"""() => {{
              const feature = window.SATN_REVIEW_MAP.getSource('topography')._data.features
                .find(candidate => candidate.id === {unavailable_topography_id!r});
              const coordinates = [];
              const collectCoordinates = (coordinate) => {{
                if (typeof coordinate[0] === 'number') coordinates.push(coordinate);
                else coordinate.forEach(collectCoordinates);
              }};
              collectCoordinates(feature.geometry.coordinates);
              const before = coordinates[Math.floor((coordinates.length - 1) / 2)];
              const after = coordinates[Math.ceil((coordinates.length - 1) / 2)];
              const coordinate = [
                (before[0] + after[0]) / 2,
                (before[1] + after[1]) / 2,
              ];
              const point = window.SATN_REVIEW_MAP.project(coordinate);
              return {{x: point.x, y: point.y}};
            }}"""
        )
        map_box = page.locator("#map").bounding_box(timeout=BROWSER_TIMEOUT_MS)
        assert map_box is not None
        page.mouse.click(
            map_box["x"] + unavailable_point["x"],
            map_box["y"] + unavailable_point["y"],
        )
        page.wait_for_function(
            f"document.querySelector('#feature-details').textContent.includes({unavailable_topography_id!r})",
            timeout=BROWSER_TIMEOUT_MS,
        )
        page.evaluate("window.SATN_REVIEW_MAP.jumpTo({zoom: 9})")
        page.wait_for_function(
            "!window.SATN_REVIEW_MAP.getSource('topography')._data.features.some("
            f"feature => feature.id === {unavailable_topography_id!r})",
            timeout=BROWSER_TIMEOUT_MS,
        )
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.queryRenderedFeatures({layers: ['gradient-overview']})"
            ".length > 0",
            timeout=BROWSER_TIMEOUT_MS,
        )
        assert page.evaluate(
            "window.SATN_REVIEW_MAP.queryRenderedFeatures({layers: ['gradient-overview']})"
            ".every(feature => feature.id === feature.properties.profile_id)"
        )
        page.evaluate("window.SATN_REVIEW_MAP.jumpTo({zoom: 11})")
        page.wait_for_function(
            "window.SATN_REVIEW_MAP.getSource('topography')._data.features.some("
            f"feature => feature.id === {unavailable_topography_id!r})",
            timeout=BROWSER_TIMEOUT_MS,
        )

        page.wait_for_function(
            "navigator.serviceWorker.controller !== null", timeout=BROWSER_TIMEOUT_MS
        )
        page.wait_for_function(
            "async () => Boolean(await caches.match('network.geojson'))",
            timeout=BROWSER_TIMEOUT_MS,
        )
        context.set_offline(True)
        page.reload(wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_function(
            "document.documentElement.dataset.mapReady === 'true'",
            timeout=BROWSER_TIMEOUT_MS,
        )
        assert page.locator("#layer-strategic-network").is_checked(timeout=BROWSER_TIMEOUT_MS)
        assert page.locator("#layer-places").is_checked(timeout=BROWSER_TIMEOUT_MS)
        assert not page.locator("#layer-authority-boundaries").is_checked(
            timeout=BROWSER_TIMEOUT_MS
        )
        context.set_offline(False)
