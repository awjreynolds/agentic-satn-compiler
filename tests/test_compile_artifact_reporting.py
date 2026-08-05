from __future__ import annotations

from pathlib import Path

import satn.pipeline as pipeline
from satn.models import AreaDefinition, CompilationResult
from satn.retained_artifacts import RetainedArtifactStore


def _area(tmp_path: Path) -> AreaDefinition:
    config = tmp_path / "area.yaml"
    config.write_text(
        """\
council_id: tiny
council_name: Tiny Council
source:
  snapshot_dir: snapshots
publication:
  output_dir: output
  title: Tiny publication
""",
        encoding="utf-8",
    )
    return AreaDefinition.from_yaml(config)


def test_compile_records_incremental_controls_and_publication_outcome(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    council = _area(tmp_path)
    artifact_root = tmp_path / "retained"
    observed: dict[str, object] = {}

    def compile_stub(*_args: object, **kwargs: object) -> CompilationResult:
        observed.update(kwargs)
        return CompilationResult(
            run_id="run-fixture",
            status="complete",
            output_dir=council.publication.output_dir,
            connections=2,
            gaps=0,
            artifacts={},
            criteria={},
            agent_records=[],
            metadata={"network_model": "backbone-outward"},
        )

    monkeypatch.setattr(pipeline, "_compile", compile_stub)

    result = pipeline.compile(
        council,
        artifact_root=artifact_root,
        rebuild_stages=("area-extraction",),
        workers=3,
        explain_reuse=True,
    )

    assert observed["rebuild_stages"] == ("area-extraction",)
    report_path = Path(result.metadata["compilation_run_report"])
    assert report_path.parent == artifact_root / "runs"
    report = RetainedArtifactStore(artifact_root).read_run_report(report_path.stem)
    assert report.mode == "targeted"
    assert report.result == "complete"
    assert report.payload()["workers"] == {"requested": 3, "selected": 1}
    assert [event.kind for event in report.artifact_events] == [
        "routing-assembly",
        "semantic-compilation",
        "presentation",
        "publication",
    ]
    assert [event.disposition for event in report.artifact_events] == [
        "skipped",
        "build",
        "build",
        "done",
    ]
    assert result.metadata["reuse_explanation"] == [
        event.payload() for event in report.artifact_events
    ]


def test_compile_defaults_to_the_caller_workspace_artifact_store(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    council = _area(tmp_path)

    def compile_stub(*_args: object, **_kwargs: object) -> CompilationResult:
        return CompilationResult(
            run_id="run-reused",
            status="complete",
            output_dir=council.publication.output_dir,
            connections=2,
            gaps=0,
            artifacts={},
            criteria={},
            agent_records=[],
            metadata={"publication_reused": True},
        )

    monkeypatch.setattr(pipeline, "_compile", compile_stub)

    result = pipeline.compile(council)

    report_path = Path(result.metadata["compilation_run_report"])
    assert report_path.parent == tmp_path / ".satn" / "runs"
    report = RetainedArtifactStore.in_workspace(tmp_path).read_run_report(
        report_path.stem
    )
    assert report.mode == "incremental"
    assert [event.disposition for event in report.artifact_events] == [
        "skipped",
        "hit",
        "hit",
        "done",
    ]
    assert report.artifact_events[0].reason == "publication-reused-routing-skipped"
    assert report.artifact_events[1].reason == "validated-semantic-publication"
    assert "reuse_explanation" not in result.metadata


def test_nonpublishing_result_reports_failed_and_skipped_stages(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    council = _area(tmp_path)

    def compile_stub(*_args: object, **_kwargs: object) -> CompilationResult:
        return CompilationResult(
            run_id="run-decision-required",
            status="decision-required",
            output_dir=council.publication.output_dir,
            connections=0,
            gaps=1,
            artifacts={},
            criteria={},
            agent_records=[],
        )

    monkeypatch.setattr(pipeline, "_compile", compile_stub)

    result = pipeline.compile(council)
    report_path = Path(result.metadata["compilation_run_report"])
    report = RetainedArtifactStore.in_workspace(tmp_path).read_run_report(
        report_path.stem
    )

    assert report.result == "failed"
    assert [event.disposition for event in report.artifact_events] == [
        "skipped",
        "failed",
        "skipped",
        "skipped",
    ]
    assert report.payload()["publication"] == {
        "run_id": "run-decision-required",
        "validation": "none",
        "replacement": "retained",
    }
