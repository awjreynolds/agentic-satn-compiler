"""Typed orchestration seam for retained compilation.

The ordinary :func:`satn.pipeline.compile` API remains the caller-facing
entrypoint.  This module owns the retained-run policy that is independent from
the network compiler itself: validating rebuild controls and turning one result
into an immutable run report.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from satn.models import AreaConfig, CompilationResult
from satn.retained_artifacts import (
    CompilationRunReport,
    RetainedArtifactStore,
    RunArtifactEvent,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_INCREMENTAL_STAGE_NAMES = frozenset(
    {
        "edge-enrichments",
        "routing-assembly",
        "scenario-selection",
        "presentation",
        "publication",
    }
)
_RETIRED_INPUT_STAGES = {
    "source-export": "satn snapshot",
    "evidence-refresh": "satn evidence refresh",
    "area-extraction": "satn snapshot",
    "canonical-network": "satn snapshot",
}


@dataclass(frozen=True)
class RetainedCompilationIntent:
    """Validated controls for one retained compilation attempt."""

    rebuild_stages: tuple[str, ...] = ()
    full: bool = False
    workers: str | int = "auto"
    explain_reuse: bool = False

    def __post_init__(self) -> None:
        stages = tuple(self.rebuild_stages)
        if any(not isinstance(stage, str) or not stage for stage in stages):
            raise ValueError("retained rebuild stages must be non-empty strings")
        stages = tuple(dict.fromkeys(stages))
        unknown = sorted(set(stages).difference(_INCREMENTAL_STAGE_NAMES))
        if unknown:
            retired = [stage for stage in unknown if stage in _RETIRED_INPUT_STAGES]
            if retired:
                hints = "; ".join(
                    f"{stage}: run `{_RETIRED_INPUT_STAGES[stage]}`" for stage in retired
                )
                raise ValueError(
                    "retained input stages are not compile rebuild targets ("
                    + hints
                    + "); use compile --rebuild-stage only for edge-enrichments, "
                    "routing-assembly, scenario-selection, presentation, or publication"
                )
            raise ValueError("unknown retained compilation stage: " + ", ".join(unknown))
        object.__setattr__(self, "rebuild_stages", stages)
        if not isinstance(self.full, bool):
            raise ValueError("full retained compilation flag is invalid")
        if self.workers != "auto" and (
            not isinstance(self.workers, int) or isinstance(self.workers, bool) or self.workers < 1
        ):
            raise ValueError("workers must be auto or a positive integer")
        if not isinstance(self.explain_reuse, bool):
            raise ValueError("explain_reuse must be a boolean")


@dataclass(frozen=True)
class RetainedCompilationOutcome:
    """Result, report and persisted report path for one retained attempt."""

    result: CompilationResult
    report: CompilationRunReport
    report_path: Path

    @property
    def events(self) -> tuple[RunArtifactEvent, ...]:
        """Run events in the stable retained stage order."""

        return self.report.artifact_events


# Temporary migration seam: ``pipeline.compile`` owns the concrete callback and
# this private alias prevents callers from treating it as a second compiler API.
_CompileFn = Callable[..., CompilationResult]


def compile_retained(
    council: AreaConfig,
    intent: RetainedCompilationIntent,
    *,
    store: RetainedArtifactStore,
    _compile_fn: _CompileFn,
) -> RetainedCompilationOutcome:
    """Execute a compile-shaped callback and retain its run report.

    ``_compile_fn`` is a temporary internal domain-compiler seam used by
    ``pipeline.compile`` during migration.  It receives the council and typed
    intent; callers should use ``pipeline.compile`` rather than this callback.
    Retention policy remains here so callers cannot accidentally bypass stage
    validation or report construction.
    """

    if not isinstance(intent, RetainedCompilationIntent):
        raise TypeError("retained compilation intent is invalid")
    if not isinstance(store, RetainedArtifactStore):
        raise TypeError("retained compilation store is invalid")
    if not callable(_compile_fn):
        raise TypeError("retained compilation callback is invalid")

    started_at = datetime.now(UTC)
    started = time.perf_counter()
    result = _compile_fn(council, intent)
    if not isinstance(result, CompilationResult):
        raise TypeError("retained compilation callback returned an invalid result")
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    events = _run_events(council, result, intent, elapsed_ms=elapsed_ms)
    finished_at = datetime.now(UTC)
    report_id = finished_at.strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
    report = CompilationRunReport(
        run_id=report_id,
        area_definition=str(council.config_path),
        mode="full" if intent.full else "targeted" if intent.rebuild_stages else "incremental",
        result=(
            "failed"
            if result.status in {"decision-required", "terminated"}
            else "complete-with-gaps"
            if result.gaps or result.status == "reviewable"
            else "complete"
        ),
        started_at=started_at.isoformat().replace("+00:00", "Z"),
        finished_at=finished_at.isoformat().replace("+00:00", "Z"),
        workers={"requested": intent.workers, "selected": 1},
        artifact_events=events,
        stitch=None,
        publication={
            "run_id": result.run_id,
            "validation": "passed" if result.status in {"complete", "reviewable"} else "none",
            "replacement": (
                "retained"
                if result.status not in {"complete", "reviewable"}
                else "reused"
                if bool(result.metadata.get("publication_reused"))
                else "atomic"
            ),
        },
        peak_rss_bytes=0,
    )
    report_path = store.write_run_report(report)
    result.metadata = {
        **result.metadata,
        "compilation_run_report": str(report_path),
        **(
            {"reuse_explanation": [event.payload() for event in events]}
            if intent.explain_reuse
            else {}
        ),
    }
    return RetainedCompilationOutcome(result, report, report_path)


def _run_events(
    council: AreaConfig,
    result: CompilationResult,
    intent: RetainedCompilationIntent,
    *,
    elapsed_ms: int,
) -> tuple[RunArtifactEvent, ...]:
    """Construct stable stage events from compiler metadata."""

    publication_reused = bool(result.metadata.get("publication_reused"))
    semantic_reused = bool(result.metadata.get("semantic_compilation_reused"))
    reused = publication_reused or semantic_reused
    published = result.status in {"complete", "reviewable"}
    presentation_republished = bool(result.metadata.get("presentation_republished"))
    retained_publication_context = publication_reused or presentation_republished

    semantic_bundle_disposition = result.metadata.get("semantic_bundle_disposition")
    semantic_bundle_artifact_id = result.metadata.get("semantic_bundle_artifact_id")
    if semantic_bundle_disposition not in {"hit", "build", "unavailable"}:
        semantic_bundle_disposition = None
    if (
        not isinstance(semantic_bundle_artifact_id, str)
        or _SHA256_PATTERN.fullmatch(semantic_bundle_artifact_id) is None
    ):
        semantic_bundle_artifact_id = None

    routing_bundle_disposition = result.metadata.get("routing_bundle_disposition")
    routing_bundle_artifact_id = result.metadata.get("routing_bundle_artifact_id")
    if routing_bundle_disposition not in {"hit", "build"}:
        routing_bundle_disposition = None
    if (
        not isinstance(routing_bundle_artifact_id, str)
        or _SHA256_PATTERN.fullmatch(routing_bundle_artifact_id) is None
    ):
        routing_bundle_artifact_id = None

    edge_enrichment_disposition = result.metadata.get("edge_enrichment_disposition")
    edge_enrichment_artifact_id = result.metadata.get("edge_enrichment_artifact_id")
    if edge_enrichment_disposition not in {"hit", "build"}:
        edge_enrichment_disposition = None
    if (
        not isinstance(edge_enrichment_artifact_id, str)
        or _SHA256_PATTERN.fullmatch(edge_enrichment_artifact_id) is None
    ):
        edge_enrichment_artifact_id = None

    routing_skipped_reason = (
        "publication-reused-routing-skipped"
        if publication_reused
        else "presentation-republish-routing-skipped"
        if presentation_republished
        else "routing-retention-unavailable"
    )
    return (
        RunArtifactEvent(
            kind="edge-enrichments",
            scope=council.area_id,
            disposition=(
                "skipped"
                if retained_publication_context
                else edge_enrichment_disposition
                if edge_enrichment_disposition in {"hit", "build"}
                else "skipped"
            ),
            reason=(
                "publication-reused-edge-enrichment-skipped"
                if publication_reused
                else "presentation-republish-edge-enrichment-skipped"
                if presentation_republished
                else str(result.metadata.get("edge_enrichment_reason"))
                if result.metadata.get("edge_enrichment_reason") is not None
                else "edge-enrichment-retention-unavailable"
            ),
            artifact_id=edge_enrichment_artifact_id,
            elapsed_ms=0,
        ),
        RunArtifactEvent(
            kind="routing-assembly",
            scope=council.area_id,
            disposition=(
                "skipped"
                if retained_publication_context
                else routing_bundle_disposition
                if routing_bundle_disposition in {"hit", "build"}
                else "skipped"
            ),
            reason=(
                routing_skipped_reason
                if retained_publication_context or routing_bundle_disposition is None
                else str(result.metadata.get("routing_bundle_reason"))
            ),
            artifact_id=routing_bundle_artifact_id,
            elapsed_ms=0,
        ),
        RunArtifactEvent(
            kind="semantic-compilation",
            scope=council.area_id,
            disposition=(
                "failed"
                if not published
                else semantic_bundle_disposition
                if semantic_bundle_disposition in {"hit", "build"}
                else "skipped"
                if semantic_bundle_disposition == "unavailable"
                else "hit"
                if reused
                else "build"
            ),
            reason=(
                result.status
                if not published
                else str(result.metadata.get("semantic_bundle_reason"))
                if semantic_bundle_disposition is not None
                else "validated-semantic-publication"
                if reused
                else "compiled-from-governed-inputs"
            ),
            artifact_id=semantic_bundle_artifact_id,
            elapsed_ms=max(0, elapsed_ms),
        ),
        RunArtifactEvent(
            kind="presentation",
            scope=council.area_id,
            disposition=(
                "skipped"
                if not published
                else "build"
                if presentation_republished or not publication_reused
                else "hit"
            ),
            reason=(
                "semantic-compilation-incomplete"
                if not published
                else "forced-stage"
                if presentation_republished
                and set(intent.rebuild_stages).intersection({"presentation", "publication"})
                else "presentation-dependencies-changed"
                if presentation_republished
                else "generated-from-semantic-publication"
                if not publication_reused
                else "validated-current-presentation"
            ),
            artifact_id=None,
            elapsed_ms=int(
                max(0.0, float(result.metadata.get("presentation_elapsed_seconds", 0.0))) * 1000
            ),
        ),
        RunArtifactEvent(
            kind="publication",
            scope=council.area_id,
            disposition="done" if published else "skipped",
            reason=(
                "semantic-compilation-incomplete"
                if not published
                else "validated-existing-publication"
                if publication_reused and not presentation_republished
                else "validated-atomic-replacement"
            ),
            artifact_id=None,
            elapsed_ms=0,
        ),
    )


__all__ = [
    "RetainedCompilationIntent",
    "RetainedCompilationOutcome",
    "compile_retained",
]
