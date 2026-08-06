"""Node-backed contract tests for the pure Review Lens state seam."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[1] / "src" / "satn" / "assets" / "review-lens-state.js"


def _run_node(script: str) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to check the shipped Review Lens state asset")
    result = subprocess.run(
        [node, "-e", script, str(MODULE)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_review_lens_catalog_uses_stable_ids_and_first_duplicate() -> None:
    result = _run_node(
        r"""
const lens = require(process.argv[1]);
const catalog = lens.parseArtifactCatalog({
  network: {type: "FeatureCollection", features: [
    {type: "Feature", id: "route-1", properties: {name: "first"},
      geometry: {type: "LineString", coordinates: [[0, 0], [1, 1]]}},
    {type: "Feature", id: "route-1", properties: {name: "duplicate"},
      geometry: {type: "LineString", coordinates: [[2, 2], [3, 3]]}},
  ]},
  reviewable: {type: "FeatureCollection", features: [
    {type: "Feature", properties: {section_id: "legacy-section"},
      geometry: {type: "LineString", coordinates: [[2, 2], [3, 3]]}},
  ]},
  reviewable_network: {type: "FeatureCollection", features: [
    {type: "Feature", properties: {section_id: "section-1"},
      geometry: {type: "LineString", coordinates: [[0, 0], [1, 1]]}},
  ]},
  places: {type: "FeatureCollection", features: [
    {type: "Feature", properties: {place_id: "place-1"},
      geometry: {type: "Point", coordinates: [0, 0]}},
  ]},
});
const route = catalog.find("network", "route-1");
const section = catalog.find("reviewable", "section-1");
const place = catalog.find("places", "place-1");
if (!route || route.feature.properties.name !== "first") {
  throw new Error("duplicate did not preserve first artifact");
}
if (!section || section.key !== "reviewable:section-1") {
  throw new Error("reviewable artifact lookup failed");
}
if (catalog.find("reviewable", "legacy-section")) {
  throw new Error("canonical reviewable_network source was not preferred");
}
if (!place || place.key !== "places:place-1") throw new Error("place artifact lookup failed");
console.log(JSON.stringify({
  route: route.id, section: section.id, place: place.id, count: catalog.artifacts.length,
}));
""",
    )
    assert result == {"route": "route-1", "section": "section-1", "place": "place-1", "count": 3}


def test_review_lens_reducer_preserves_preview_pin_and_comparison_transitions() -> None:
    result = _run_node(
        r"""
const lens = require(process.argv[1]);
const line = (id) => ({
  key: `network:${id}`, id, sourceId: "network", feature: {geometry: {type: "LineString"}},
});
const point = {
  key: "places:place-1", id: "place-1", sourceId: "places", feature: {geometry: {type: "Point"}},
};
const first = line("route-1");
const second = line("route-2");
let state = lens.createInitialLensState();
state = lens.reduceLens(state, {type: lens.ActionType.PREVIEW_ARTIFACT, artifact: first});
const preview = lens.projectLensView(state);
state = lens.reduceLens(state, {type: lens.ActionType.TOGGLE_PIN_ARTIFACT, artifact: first});
const pinned = lens.projectLensView(state);
state = lens.reduceLens(state, {type: lens.ActionType.PREVIEW_ARTIFACT, artifact: second});
const previewWhilePinned = lens.projectLensView(state);
state = lens.reduceLens(state, {type: lens.ActionType.TOGGLE_PIN_ARTIFACT, artifact: second});
const compared = lens.projectLensView(state);
state = lens.reduceLens(state, {type: lens.ActionType.TOGGLE_PIN_ARTIFACT, artifact: second});
const closed = lens.projectLensView(state);
state = lens.reduceLens(
  lens.createInitialLensState(), {type: lens.ActionType.TOGGLE_PIN_ARTIFACT, artifact: point},
);
const pointPinned = lens.projectLensView(state);
console.log(JSON.stringify({
  preview: {kind: preview.selectionKind, pinned: preview.pinnedArtifact},
  pinned: {
    kind: pinned.selectionKind, comparison: pinned.comparisonKind, id: pinned.pinnedArtifact.id,
  },
  previewWhilePinned: {
    kind: previewWhilePinned.selectionKind,
    pinned: previewWhilePinned.pinnedArtifact.id,
    preview: previewWhilePinned.previewArtifact.id,
  },
  compared: {
    kind: compared.comparisonKind,
    ids: compared.comparisonArtifacts.map((item) => item.id),
  },
  closed: {kind: closed.selectionKind, visible: closed.visible},
  pointPinned: {
    kind: pointPinned.selectionKind,
    comparison: pointPinned.comparisonKind,
    count: pointPinned.comparisonArtifacts.length,
  },
}));
""",
    )
    assert result == {
        "preview": {"kind": "preview", "pinned": None},
        "pinned": {"kind": "pinned", "comparison": "none", "id": "route-1"},
        "previewWhilePinned": {"kind": "pinned", "pinned": "route-1", "preview": "route-2"},
        "compared": {"kind": "segments", "ids": ["route-1", "route-2"]},
        "closed": {"kind": "none", "visible": False},
        "pointPinned": {"kind": "pinned", "comparison": "none", "count": 0},
    }


def test_review_lens_view_projection_exposes_gradient_visibility() -> None:
    result = _run_node(
        r"""
const lens = require(process.argv[1]);
const artifact = {
  key: "network:route-1", id: "route-1", sourceId: "network",
  feature: {geometry: {type: "LineString"}},
};
let state = lens.reduceLens(
  lens.createInitialLensState(), {type: lens.ActionType.TOGGLE_PIN_ARTIFACT, artifact},
);
const pinned = lens.projectLensView(state, {inspectionPathLength: 2});
const noPath = lens.projectLensView(state, {inspectionPathLength: 0});
console.log(JSON.stringify({
  pinned: {label: pinned.label, state: pinned.state, details: pinned.showGradientDetails},
  noPath: {details: noPath.showGradientDetails},
}));
""",
    )
    assert result == {
        "pinned": {"label": "Pinned review", "state": "pinned", "details": True},
        "noPath": {"details": False},
    }
