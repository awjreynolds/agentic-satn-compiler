"""Focused presentation packaging contracts for the Review Lens state asset."""

from pathlib import Path

import satn.compilation_dependencies as dependencies
import satn.pipeline as pipeline
import satn.publisher as publisher

ASSETS = Path(__file__).parents[1] / "src" / "satn" / "assets"


def test_review_lens_state_is_presentation_only() -> None:
    assert "satn/assets/review-lens-state.js" in dependencies.EXCLUDED_COMPONENTS
    assert "review-lens-state.js" in publisher._PRESENTATION_CORE_ASSETS


def test_review_lens_state_is_copied_and_fingerprinted_by_publication_paths() -> None:
    publisher_source = Path(publisher.__file__).read_text(encoding="utf-8")
    pipeline_source = Path(pipeline.__file__).read_text(encoding="utf-8")
    assert "__REVIEW_LENS_STATE_JS__" in publisher_source
    assert "review-lens-state.js" in publisher_source
    assert "review-lens-state.js" in pipeline_source
    assert (ASSETS / "review-lens-state.js").is_file()


def test_rendered_template_loads_state_before_review_map() -> None:
    presentation_input = {
        "title": "Review",
        "atm_state": "disabled",
        "atm_status": "Unavailable",
        "reference_evidence_html": "",
        "strategic_reference_html": "",
        "reference_enabled": False,
        "strategic_enabled": False,
        "topography": {
            "gentle_max_pct": "3",
            "noticeable_max_pct": "5",
            "steep_max_pct": "8",
            "very_steep_max_pct": "12",
        },
    }
    rendered = publisher._render_presentation_html(
        (ASSETS / "review-map.html").read_text(encoding="utf-8"),
        presentation_input,
        {
            "review-lens-state.js": "review-lens-state.abc.js",
            "review-map.js": "review-map.abc.js",
            "review-map.css": "review-map.abc.css",
        },
    )
    assert "__REVIEW_LENS_STATE_JS__" not in rendered
    assert rendered.index("review-lens-state.abc.js") < rendered.index("review-map.abc.js")
