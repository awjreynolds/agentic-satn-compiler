from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def test_review_map_has_grouped_alignment_evidence_panel_contract() -> None:
    script = (PROJECT / "src/satn/assets/review-map.js").read_text()
    stylesheet = (PROJECT / "src/satn/assets/review-map.css").read_text()

    assert "renderAlignmentComparison" in script
    assert "candidate_set_id" in script
    assert "radar" in script
    assert "officer-compiler-divergence" in script
    assert ".alignment-comparison" in stylesheet
