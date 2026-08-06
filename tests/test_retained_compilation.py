from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import satn.pipeline as pipeline
from satn.models import CouncilConfig
from satn.retained_artifacts import RetainedArtifactStore
from satn.retained_compilation import RetainedCompilationIntent
from satn.sources import snapshot

PROJECT = Path(__file__).parents[1]


def test_rebuild_intent_is_typed_and_deduplicates_valid_stages() -> None:
    intent = RetainedCompilationIntent(
        rebuild_stages=("presentation", "presentation", "publication"),
        workers=2,
        explain_reuse=True,
    )

    assert intent.rebuild_stages == ("presentation", "publication")
    assert intent.workers == 2
    assert intent.explain_reuse is True


@pytest.mark.parametrize("stage", ["unknown", "source-export"])
def test_rebuild_intent_rejects_invalid_or_retired_stages(stage: str) -> None:
    with pytest.raises(ValueError, match=stage):
        RetainedCompilationIntent(rebuild_stages=(stage,))


def test_pipeline_compile_uses_retained_seam_without_changing_report_contract(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    council = CouncilConfig.from_yaml(fixture / "council.yaml")
    snapshot(council)
    result = pipeline.compile(
        council,
        artifact_root=tmp_path / "retained",
        rebuild_stages=("edge-enrichments",),
    )

    report_path = Path(result.metadata["compilation_run_report"])
    report = RetainedArtifactStore(tmp_path / "retained").read_run_report(report_path.stem)
    assert report.payload()["result"] in {"complete", "complete-with-gaps"}
    assert result.metadata["compilation_run_report"] == str(report_path)
    assert result.run_id == report.payload()["publication"]["run_id"]
    assert report.artifact_events[-1].kind == "publication"
