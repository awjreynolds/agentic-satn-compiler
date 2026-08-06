"""Contract checks for the browser adapter around the pure Review Lens seam."""

from pathlib import Path

ASSETS = Path(__file__).parents[1] / "src" / "satn" / "assets"


def test_review_map_adapter_uses_pure_catalog_and_reducer() -> None:
    script = (ASSETS / "review-map.js").read_text(encoding="utf-8")
    assert "window.SATN_REVIEW_LENS_STATE" in script
    assert "parseArtifactCatalog(data)" in script
    assert "reduceLens" in script
    assert "ActionType.TOGGLE_PIN_ARTIFACT" in script
    assert "inspectionPath" in script
    assert "populationSectionIds" in script
    assert "renderSegmentComparison" in script
    assert "showArtifactDetails" in script
    assert "function canCompareArtifact" not in script


def test_review_map_loads_lens_state_before_browser_adapter() -> None:
    html = (ASSETS / "review-map.html").read_text(encoding="utf-8")
    assert "assets/__REVIEW_LENS_STATE_JS__" in html
    assert html.index("assets/__REVIEW_LENS_STATE_JS__") < html.index("assets/__REVIEW_MAP_JS__")


def test_preview_and_clear_paths_dispatch_pure_transitions() -> None:
    script = (ASSETS / "review-map.js").read_text(encoding="utf-8")
    details_start = script.index("  function showArtifactDetails(artifact) {")
    details_end = script.index("\n  function renderReviewableDetails", details_start)
    details_source = script[details_start:details_end]
    clear_start = script.index("  function clearTransient() {")
    clear_end = script.index("\n  function toggleArtifactPin", clear_start)
    clear_source = script[clear_start:clear_end]
    assert "PREVIEW_ARTIFACT" in details_source
    assert "!state.pinnedArtifact" in details_source
    assert "PREVIEW_ARTIFACT" in clear_source
    assert "artifact: null" in clear_source
