"""Open every packaged SATN deployment and prove its strategic network renders."""

from __future__ import annotations

import argparse
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

if TYPE_CHECKING:
    from collections.abc import Iterator

    from playwright.sync_api import BrowserContext


@dataclass(frozen=True)
class DeploymentRenderResult:
    deployment_id: str
    strategic_spines: int
    access_connections: int
    cross_spine_connectors: int
    rendered_strategic_spines: int


class _PagesHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _serve(root: Path) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_PagesHandler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _deployment_entries(pages: Path) -> list[dict[str, object]]:
    catalogue_path = pages / "catalogue.json"
    catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
    entries = catalogue.get("deployments")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Pages catalogue must declare at least one deployment")
    return entries


def _inspect_deployment(
    context: BrowserContext,
    origin: str,
    entry: dict[str, object],
) -> DeploymentRenderResult:
    deployment_id = entry.get("deployment_id")
    artifacts = entry.get("artifacts")
    if not isinstance(deployment_id, str) or not isinstance(artifacts, dict):
        raise ValueError("Pages catalogue deployment entry is invalid")
    review_map = artifacts.get("review_map")
    if not isinstance(review_map, str):
        raise ValueError(f"Pages catalogue review map is invalid: {deployment_id}")

    page = context.new_page()
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(f"{origin}/{review_map}", wait_until="domcontentloaded")
        try:
            page.wait_for_function(
                "document.documentElement.dataset.mapReady === 'true' && "
                "Boolean(window.SATN_REVIEW_MAP)"
            )
        except PlaywrightTimeoutError as error:
            details = "; ".join(f"browser error: {message}" for message in page_errors)
            suffix = f" ({details})" if details else ""
            raise ValueError(
                f"{deployment_id} map rendering did not become ready{suffix}"
            ) from error
        page.wait_for_function("!window.SATN_REVIEW_MAP.isMoving()")
        inspection = page.evaluate(
            """() => {
              const map = window.SATN_REVIEW_MAP;
              const source = map.getSource("network");
              const features = source?._data?.features || [];
              const expected = [
                ["strategic-spine", "strategic-spines"],
                ["spine-access-connection", "spine-access-connections"],
                ["cross-spine-connector", "cross-spine-connectors"],
              ];
              const failures = [];
              const counts = Object.fromEntries(expected.map(([featureType]) => [
                featureType,
                features.filter((feature) =>
                  feature.properties?.feature_type === featureType
                ).length,
              ]));
              const invalidGeometry = expected.flatMap(([featureType]) =>
                features
                  .filter((feature) => feature.properties?.feature_type === featureType)
                  .filter((feature) => !["LineString", "MultiLineString"].includes(
                    feature.geometry?.type
                  ))
                  .map((feature) => feature.id || featureType)
              );
              if (!document.querySelector("#layer-strategic-network")?.checked) {
                failures.push("Strategic Active Travel Network control is not selected");
              }
              if (counts["strategic-spine"] === 0) {
                failures.push("published network contains no strategic-spine geometry");
              }
              for (const [featureType, layerId] of expected) {
                if (counts[featureType] === 0) continue;
                if (!map.getLayer(layerId)) {
                  failures.push(`${layerId} layer is missing`);
                } else if (map.getLayoutProperty(layerId, "visibility") === "none") {
                  failures.push(`${layerId} layer is hidden`);
                }
              }
              for (const layerId of [
                "reviewable-strategic-network-halo",
                "reviewable-strategic-network-core",
                "reviewable-route-labels",
              ]) {
                if (map.getLayer(layerId) &&
                    map.getLayoutProperty(layerId, "visibility") !== "none") {
                  failures.push(`${layerId} obscures the complete strategic network`);
                }
              }
              if (invalidGeometry.length) {
                failures.push(
                  `strategic network has non-line geometry: ${invalidGeometry.join(", ")}`
                );
              }

              const coordinates = [];
              const collect = (coordinate) => {
                if (typeof coordinate?.[0] === "number") coordinates.push(coordinate);
                else coordinate?.forEach(collect);
              };
              features
                .filter((feature) => feature.properties?.feature_type === "strategic-spine")
                .forEach((feature) => collect(feature.geometry?.coordinates));
              if (coordinates.length) {
                const longitudes = coordinates.map(([longitude]) => longitude);
                const latitudes = coordinates.map(([, latitude]) => latitude);
                map.fitBounds(
                  [[Math.min(...longitudes), Math.min(...latitudes)],
                   [Math.max(...longitudes), Math.max(...latitudes)]],
                  {padding: 40, duration: 0},
                );
              }
              return {counts, failures};
            }"""
        )
        page.wait_for_function("!window.SATN_REVIEW_MAP.isMoving()")
        rendered = page.evaluate(
            "window.SATN_REVIEW_MAP.queryRenderedFeatures({layers: ['strategic-spines']}).length"
        )
        failures = list(inspection["failures"])
        if rendered == 0:
            failures.append("strategic-spines has no rendered features after fitting its geometry")
        failures.extend(f"browser error: {message}" for message in page_errors)
        if failures:
            raise ValueError(f"{deployment_id} map rendering failed: {'; '.join(failures)}")
        counts = inspection["counts"]
        return DeploymentRenderResult(
            deployment_id=deployment_id,
            strategic_spines=counts["strategic-spine"],
            access_connections=counts["spine-access-connection"],
            cross_spine_connectors=counts["cross-spine-connector"],
            rendered_strategic_spines=rendered,
        )
    finally:
        page.close()


def validate_pages_rendering(pages_directory: str | Path) -> tuple[DeploymentRenderResult, ...]:
    pages = Path(pages_directory).resolve()
    if not pages.is_dir():
        raise ValueError(f"expected extracted Pages directory: {pages}")
    entries = _deployment_entries(pages)
    with _serve(pages) as origin, sync_playwright() as playwright:
        executable_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        if executable_path:
            browser = playwright.chromium.launch(headless=True, executable_path=executable_path)
        else:
            browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            context.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
            return tuple(_inspect_deployment(context, origin, entry) for entry in entries)
        finally:
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pages_directory", type=Path)
    args = parser.parse_args()
    for result in validate_pages_rendering(args.pages_directory):
        print(
            f"{result.deployment_id}: {result.strategic_spines} strategic spines, "
            f"{result.access_connections} access connections, "
            f"{result.cross_spine_connectors} cross-spine connectors, "
            f"{result.rendered_strategic_spines} rendered strategic features"
        )


if __name__ == "__main__":
    main()
