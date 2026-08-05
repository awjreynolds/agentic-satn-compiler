"""Stable orchestration API."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np

import satn.compilation_dependencies as compilation_dependencies
import satn.compiled_network_bundle as compiled_network_bundle_codec
from satn.agents import (
    AgentCompilationTerminated,
    AgentDecisionRequired,
    AgentDecisionResolver,
    AgentRuntimeProvider,
    AgentRuntimeSource,
    runtime_for,
)
from satn.alignment_selection import ReferenceSATNSelection
from satn.atm import compare_atm, load_atm
from satn.compilation_dependencies import CompilerPath, compilation_dependency_manifest
from satn.compiler import (
    CompiledNetwork,
    _compile_network_with_reference,
    _compile_network_with_strategic_reference,
    _routing_assembly_capture,
    _routing_assembly_replay,
    compile_network,
    governed_input_binding,
)
from satn.constants import SCHEMA_VERSION
from satn.content_identity import ordered_geometry_fingerprint
from satn.ea_snapshot_recovery import load_legacy_ea_recovery_snapshot
from satn.filesystem_safety import (
    PublicationDestinationAuthority,
    default_publication_destination_authority,
)
from satn.heartbeat import StageHeartbeat
from satn.local_evidence_store import LocalEvidenceStore
from satn.models import (
    AgentDecisionLedger,
    AgentDecisionRequest,
    AgentRecord,
    AreaConfig,
    AreaDefinition,
    CompilationResult,
    CouncilConfig,
    DivergenceRecord,
    TrafficLight,
    canonical_decision_ledger_payload,
)
from satn.parallel_reduction import PreloadedOfficerDecision
from satn.psa_evidence_loaders import GovernedEvidenceLoadError
from satn.publisher import (
    presentation_dependency_manifest,
    publication_artifacts,
    publish,
    republish_presentation,
    retain_ea_recovery_candidate,
    validate_presentation_retention,
    validate_publication,
)
from satn.reference_application import (
    _build_reference_application_plan_for_current_baseline,
    build_reference_satn_publication_record,
)
from satn.retained_artifacts import (
    ArtifactSpecification,
    CompilationRunReport,
    RetainedArtifact,
    RetainedArtifactStore,
    RunArtifactEvent,
)
from satn.reviewable_network import (
    ReviewableNetwork,
    canonical_officer_decisions,
    terminal_reviewable_network_for_governed_evidence,
)
from satn.routing_assembly_bundle import (
    RoutingAssemblyBundle,
    decode_routing_assembly_bundle,
    encode_routing_assembly_bundle,
)
from satn.runtime_governance import incomplete_runtime_governance
from satn.sources import load_snapshot
from satn.spine_access_candidate_preparation import SpineAccessCandidatePreparationResult
from satn.strategic_reference_application import StrategicReferenceApplicationPlan
from satn.strategic_reference_publication import (
    build_strategic_reference_publication_record,
)
from satn.strategic_reference_replay import validate_fresh_replay

LOGGER = logging.getLogger(__name__)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_INCREMENTAL_STAGE_NAMES = frozenset(
    {
        "source-export",
        "evidence-refresh",
        "area-extraction",
        "canonical-network",
        "edge-enrichments",
        "routing-assembly",
        "scenario-selection",
        "presentation",
        "publication",
    }
)
_ROUTING_BYPASS_STAGES = frozenset(
    {
        "source-export",
        "evidence-refresh",
        "area-extraction",
        "canonical-network",
        "edge-enrichments",
        "routing-assembly",
    }
)


def _compilation_metadata(started: float) -> dict[str, object]:
    """Return wall-clock completion and monotonic compiler elapsed time."""

    return {
        "completed_at_utc": datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "duration_seconds": max(0.0, time.perf_counter() - started),
    }


def _validate_evidence_binding(
    evidence_store: LocalEvidenceStore | None,
    evidence_state: str | None,
) -> None:
    """Require the Python opt-in to name one typed store and exact state."""

    if (evidence_store is None) != (evidence_state is None):
        raise ValueError("evidence_store and evidence_state must be supplied together")
    if evidence_store is None:
        return
    if not isinstance(evidence_store, LocalEvidenceStore):
        raise TypeError("evidence_store must be a LocalEvidenceStore")
    if evidence_state is None or _SHA256_PATTERN.fullmatch(evidence_state) is None:
        raise ValueError("evidence_state must be a full lowercase SHA-256")


@dataclass(frozen=True)
class _FreshReferenceBaseline:
    council: AreaConfig
    ledger: AgentDecisionLedger
    dependency_manifest: dict[str, object]
    governed_input_fingerprint: str
    source: dict[str, gpd.GeoDataFrame]
    compiled: CompiledNetwork


def _fresh_reference_baseline(
    config: AreaConfig | str | Path,
    decision_ledger: AgentDecisionLedger | str | Path | None,
    heartbeat: StageHeartbeat | None,
    *,
    compiler_path: CompilerPath,
    label: str,
) -> _FreshReferenceBaseline:
    council = (
        config
        if isinstance(config, (AreaDefinition, CouncilConfig))
        else AreaDefinition.from_yaml(config)
    )
    ledger = _load_decision_ledger(decision_ledger)
    dependency_manifest = compilation_dependency_manifest(
        council,
        compiler_path=compiler_path,
    )
    governed_input_fingerprint = compilation_governed_input_fingerprint(
        council,
        dependency_manifest=dependency_manifest,
    )
    source = load_snapshot(council)
    resolver = AgentDecisionResolver(ledger, governed_input_fingerprint)
    baseline = compile_network(
        council,
        _copy_compilation_source(source),
        None,
        governed_input_fingerprint=governed_input_fingerprint,
        decision_resolver=resolver,
        heartbeat=heartbeat,
    )
    unconsumed = {
        response.request_id for response in ledger.responses
    } - resolver.consumed_request_ids
    if unconsumed:
        raise ValueError(
            "decision ledger contains responses that do not belong to the fresh "
            f"{label} baseline: " + ", ".join(sorted(unconsumed))
        )
    return _FreshReferenceBaseline(
        council=council,
        ledger=ledger,
        dependency_manifest=dependency_manifest,
        governed_input_fingerprint=governed_input_fingerprint,
        source=source,
        compiled=baseline,
    )


def _finalize_reference_network(
    compiled: CompiledNetwork,
    baseline: _FreshReferenceBaseline,
    final_resolver: AgentDecisionResolver,
    *,
    label: str,
) -> CompiledNetwork:
    unconsumed = {
        response.request_id for response in baseline.ledger.responses
    } - final_resolver.consumed_request_ids
    if unconsumed:
        raise ValueError(
            "decision ledger contains responses that do not belong to the fresh "
            f"{label} compilation: " + ", ".join(sorted(unconsumed))
        )
    compiled.compilation_input_fingerprint = decision_ledger_input_fingerprint(
        baseline.governed_input_fingerprint,
        baseline.ledger,
    )
    compiled.governed_input_fingerprint = baseline.governed_input_fingerprint
    compiled.snapshot_manifest_sha256 = snapshot_manifest_sha256(baseline.council)
    compiled.area_definition_sha256 = area_definition_sha256(baseline.council)
    compiled.compilation_dependency_manifest = baseline.dependency_manifest
    compiled.decision_contract = baseline.ledger.decision_contract
    compiled.decision_ledger_input = baseline.ledger.model_dump(mode="json")
    compiled.accepted_decisions = AgentDecisionLedger.model_validate(
        {
            "decision_contract": baseline.ledger.decision_contract,
            "responses": [
                response.model_dump(mode="json")
                for response in final_resolver.accepted_responses
            ],
        }
    ).model_dump(mode="json")["responses"]
    return compiled


def compile(
    config: AreaConfig | str | Path,
    *,
    decision_ledger: AgentDecisionLedger | str | Path | None = None,
    officer_decisions: tuple[PreloadedOfficerDecision, ...] = (),
    publication_authority: PublicationDestinationAuthority | None = None,
    evidence_store: LocalEvidenceStore | None = None,
    evidence_state: str | None = None,
    rebuild_stages: tuple[str, ...] = (),
    artifact_root: Path | None = None,
    workers: str | int = "auto",
    explain_reuse: bool = False,
) -> CompilationResult:
    """Compile into a complete publication or a non-publishing decision request."""
    council = (
        config
        if isinstance(config, (AreaDefinition, CouncilConfig))
        else AreaDefinition.from_yaml(config)
    )
    rebuild_stages = tuple(dict.fromkeys(rebuild_stages))
    unknown_stages = sorted(set(rebuild_stages).difference(_INCREMENTAL_STAGE_NAMES))
    if unknown_stages:
        raise ValueError("unknown retained compilation stage: " + ", ".join(unknown_stages))
    if workers != "auto" and (
        not isinstance(workers, int) or isinstance(workers, bool) or workers < 1
    ):
        raise ValueError("workers must be auto or a positive integer")
    authority = publication_authority or default_publication_destination_authority(
        council.config_path
    )
    store = (
        RetainedArtifactStore(artifact_root)
        if artifact_root is not None
        else RetainedArtifactStore.in_workspace(authority.workspace_root)
    )
    report_started_at = datetime.now(UTC)
    report_started = time.perf_counter()
    try:
        _validate_evidence_binding(evidence_store, evidence_state)
    except GovernedEvidenceLoadError as error:
        reviewable = terminal_reviewable_network_for_governed_evidence(
            None,
            (),
            detail=str(error),
        )
        result = _reviewable_terminal_result(council, "", reviewable)
        return _record_compilation_run(
            store,
            council,
            result,
            report_started_at=report_started_at,
            elapsed_ms=int((time.perf_counter() - report_started) * 1000),
            rebuild_stages=rebuild_stages,
            workers=workers,
            explain_reuse=explain_reuse,
        )
    with StageHeartbeat(
        LOGGER,
        "publication-reuse-check",
        {
            "area_id": council.area_id,
            "snapshot_id": council.source.snapshot_id,
        },
    ) as heartbeat:
        result = _compile(
            council,
            decision_ledger=decision_ledger,
            officer_decisions=officer_decisions,
            publication_authority=publication_authority,
            evidence_store=evidence_store,
            evidence_state_fingerprint=evidence_state,
            heartbeat=heartbeat,
            rebuild_stages=rebuild_stages,
            artifact_store=store,
        )
    return _record_compilation_run(
        store,
        council,
        result,
        report_started_at=report_started_at,
        elapsed_ms=int((time.perf_counter() - report_started) * 1000),
        rebuild_stages=rebuild_stages,
        workers=workers,
        explain_reuse=explain_reuse,
    )


def _record_compilation_run(
    store: RetainedArtifactStore,
    council: AreaConfig,
    result: CompilationResult,
    *,
    report_started_at: datetime,
    elapsed_ms: int,
    rebuild_stages: tuple[str, ...],
    workers: str | int,
    explain_reuse: bool,
) -> CompilationResult:
    publication_reused = bool(result.metadata.get("publication_reused"))
    semantic_reused = bool(result.metadata.get("semantic_compilation_reused"))
    reused = publication_reused or semantic_reused
    published = result.status in {"complete", "reviewable"}
    presentation_republished = bool(result.metadata.get("presentation_republished"))
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
    routing_skipped_reason = (
        "publication-reused-routing-skipped"
        if publication_reused
        else "routing-retention-unavailable"
    )
    events = (
        RunArtifactEvent(
            kind="routing-assembly",
            scope=council.area_id,
            disposition=(
                "skipped"
                if publication_reused
                else routing_bundle_disposition
                if routing_bundle_disposition in {"hit", "build"}
                else "skipped"
            ),
            reason=(
                routing_skipped_reason
                if publication_reused or routing_bundle_disposition is None
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
                if presentation_republished and "presentation" in rebuild_stages
                else "presentation-dependencies-changed"
                if presentation_republished
                else "generated-from-semantic-publication"
                if not publication_reused
                else "validated-current-presentation"
            ),
            artifact_id=None,
            elapsed_ms=int(
                max(
                    0.0,
                    float(result.metadata.get("presentation_elapsed_seconds", 0.0)),
                )
                * 1000
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
    finished_at = datetime.now(UTC)
    report_id = finished_at.strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
    report = CompilationRunReport(
        run_id=report_id,
        area_definition=str(council.config_path),
        mode=(
            "full"
            if council.compilation.full
            else "targeted"
            if rebuild_stages
            else "incremental"
        ),
        result=(
            "failed"
            if result.status in {"decision-required", "terminated"}
            else "complete-with-gaps"
            if result.gaps or result.status == "reviewable"
            else "complete"
        ),
        started_at=report_started_at.isoformat().replace("+00:00", "Z"),
        finished_at=finished_at.isoformat().replace("+00:00", "Z"),
        workers={"requested": workers, "selected": 1},
        artifact_events=events,
        stitch=None,
        publication={
            "run_id": result.run_id,
            "validation": "passed" if published else "none",
            "replacement": (
                "retained"
                if not published
                else "reused"
                if publication_reused
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
            if explain_reuse
            else {}
        ),
    }
    return result


def compile_ea_recovery_candidate(config: AreaConfig | str | Path) -> Path:
    """Compile the pinned invalid v10 only far enough to retain its replacement candidate."""

    council = (
        config
        if isinstance(config, (AreaDefinition, CouncilConfig))
        else AreaDefinition.from_yaml(config)
    )
    council.compilation.full = True
    with StageHeartbeat(
        LOGGER,
        "ea-recovery-candidate",
        {
            "area_id": council.area_id,
            "snapshot_id": council.source.snapshot_id,
        },
    ) as heartbeat:
        result = _compile(
            council,
            heartbeat=heartbeat,
            compiler_path="ea-recovery",
        )
    candidate = result.artifacts.get("candidate")
    if candidate is None:
        raise ValueError("EA recovery compilation retained no governed mismatch candidate")
    return candidate


def compile_reference_network(
    config: AreaConfig | str | Path,
    runtime: AgentRuntimeSource,
    reference: ReferenceSATNSelection,
    source_preparation: SpineAccessCandidatePreparationResult,
    *,
    decision_ledger: AgentDecisionLedger | str | Path | None = None,
    heartbeat: StageHeartbeat | None = None,
) -> CompiledNetwork:
    """Recompile one human-adopted Reference through a fresh current baseline.

    Baseline decisions must already exist in the supplied canonical ledger, so
    validation cannot consume or duplicate direct-runtime responses.  Runtime
    remains available only to the fresh final compilation for genuinely new
    downstream decisions.
    """

    baseline = _fresh_reference_baseline(
        config,
        decision_ledger,
        heartbeat,
        compiler_path="reference",
        label="Reference",
    )
    current_preparation = baseline.compiled.spine_access_candidate_preparation
    if current_preparation is None:
        raise ValueError(
            "Reference compilation requires current network_selection candidate preparation"
        )
    plan = _build_reference_application_plan_for_current_baseline(
        reference,
        source_preparation,
        current_preparation,
        baseline.council,
    )
    final_resolver = AgentDecisionResolver(
        baseline.ledger,
        baseline.governed_input_fingerprint,
    )
    compiled = _compile_network_with_reference(
        baseline.council,
        _copy_compilation_source(baseline.source),
        runtime,
        plan,
        governed_input_fingerprint=baseline.governed_input_fingerprint,
        decision_resolver=final_resolver,
        heartbeat=heartbeat,
    )
    compiled = _finalize_reference_network(
        compiled,
        baseline,
        final_resolver,
        label="Reference",
    )
    compiled.reference_satn_publication = build_reference_satn_publication_record(
        reference=reference,
        source_preparation=source_preparation,
        baseline_preparation=current_preparation,
        application_plan=plan,
        area_definition_sha256=compiled.area_definition_sha256,
        snapshot_manifest_sha256=compiled.snapshot_manifest_sha256,
        compilation_input_fingerprint=compiled.compilation_input_fingerprint,
        governed_input_fingerprint=compiled.governed_input_fingerprint,
        compilation_dependency_manifest=baseline.dependency_manifest,
        decision_contract=compiled.decision_contract,
        decision_ledger_input=compiled.decision_ledger_input,
        accepted_decisions=compiled.accepted_decisions,
        application_diagnostics=compiled.compilation_diagnostics.get("reference_application", {}),
    )
    return compiled


def compile_strategic_reference_network(
    config: AreaConfig | str | Path,
    runtime: AgentRuntimeSource,
    plan: StrategicReferenceApplicationPlan,
    *,
    decision_ledger: AgentDecisionLedger | str | Path | None = None,
    heartbeat: StageHeartbeat | None = None,
) -> CompiledNetwork:
    """Recompile an adopted strategic Reference without publication authority.

    The ordinary baseline is always rebuilt from the current snapshot.  Exact
    preparation equality is established here, and only the resulting validated
    replay object crosses the private compiler seam.
    """

    baseline = _fresh_reference_baseline(
        config,
        decision_ledger,
        heartbeat,
        compiler_path="strategic-reference",
        label="strategic Reference",
    )
    current_preparation = baseline.compiled.strategic_corridor_preparation
    if current_preparation is None:
        raise ValueError(
            "strategic Reference compilation requires current strategic "
            "corridor preparation"
        )
    current_area_fingerprint = ordered_geometry_fingerprint(
        baseline.source["boundary"].geometry
    )
    if plan.area_fingerprint != current_area_fingerprint:
        raise ValueError(
            "strategic Reference Area identity does not match the fresh current "
            "snapshot boundary"
        )
    validated_replay = validate_fresh_replay(plan, current_preparation)

    final_resolver = AgentDecisionResolver(
        baseline.ledger,
        baseline.governed_input_fingerprint,
    )
    compiled = _compile_network_with_strategic_reference(
        baseline.council,
        _copy_compilation_source(baseline.source),
        runtime,
        validated_replay,
        governed_input_fingerprint=baseline.governed_input_fingerprint,
        decision_resolver=final_resolver,
        heartbeat=heartbeat,
    )
    return _finalize_reference_network(
        compiled,
        baseline,
        final_resolver,
        label="strategic Reference",
    )


def compile_strategic_reference(
    config: AreaConfig | str | Path,
    plan: StrategicReferenceApplicationPlan,
    *,
    decision_ledger: AgentDecisionLedger | str | Path | None = None,
    publication_authority: PublicationDestinationAuthority | None = None,
) -> CompilationResult:
    """Freshly compile then atomically publish a strategic Reference replay.

    This public boundary intentionally accepts a plan only as replay input. It
    derives a fresh private compilation first; the existing publisher remains
    the only authority that can replace a publication directory.
    """

    council = (
        config
        if isinstance(config, (AreaDefinition, CouncilConfig))
        else AreaDefinition.from_yaml(config)
    )
    started = time.perf_counter()
    with StageHeartbeat(
        LOGGER,
        "strategic-reference-publication",
        {"area_id": council.area_id, "snapshot_id": council.source.snapshot_id},
    ) as heartbeat:
        # A strategic publication is a governed replay of accepted decisions,
        # not an invitation for a publisher to obtain or choose agent output.
        compiled = compile_strategic_reference_network(
            council,
            None,
            plan,
            decision_ledger=decision_ledger,
            heartbeat=heartbeat,
        )
        # Only the public publication boundary makes the sibling record; the
        # private replay entry point remains inspect-only and record-free.
        compiled.strategic_reference_publication = build_strategic_reference_publication_record(
            plan=plan,
            replay_diagnostics=compiled.strategic_reference_diagnostics,
            area_definition_sha256=compiled.area_definition_sha256,
            snapshot_manifest_sha256=compiled.snapshot_manifest_sha256,
            compilation_input_fingerprint=compiled.compilation_input_fingerprint,
            governed_input_fingerprint=compiled.governed_input_fingerprint,
            compilation_dependency_manifest=compiled.compilation_dependency_manifest,
            decision_contract=compiled.decision_contract,
            decision_ledger_input=compiled.decision_ledger_input,
            accepted_decisions=compiled.accepted_decisions,
        )
        record = compiled.strategic_reference_publication
        if record is None:
            raise ValueError("strategic Reference compilation produced no publication provenance")
        run_fingerprint = json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "area_id": council.area_id,
                "snapshot_id": council.source.snapshot_id,
                "strategic_reference_publication_fingerprint": record.record_fingerprint,
                "compilation_input_fingerprint": compiled.compilation_input_fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        run_id = "strategic-reference-" + hashlib.sha256(run_fingerprint.encode()).hexdigest()[:12]
        heartbeat.set_stage("strategic-reference-publication")
        compilation_metadata = _compilation_metadata(started)
        artifacts = publish(
            council,
            compiled,
            run_id,
            publication_authority=publication_authority,
            compilation_metadata=compilation_metadata,
        )
    return CompilationResult(
        run_id=run_id,
        status=compiled.status,
        output_dir=council.publication.output_dir,
        connections=compiled.connection_count,
        gaps=len(compiled.gaps),
        artifacts=artifacts,
        criteria=compiled.criteria,
        agent_records=compiled.agent_records,
        divergence_records=compiled.divergence_records,
        metadata={
            "network_model": "backbone-outward",
            "compilation_input_fingerprint": compiled.compilation_input_fingerprint,
            "strategic_reference": record.publication_payload(),
            "compilation_diagnostics": compiled.compilation_diagnostics,
            "compilation_metadata": compilation_metadata,
        },
    )


def compile_reference(
    config: AreaConfig | str | Path,
    reference: ReferenceSATNSelection,
    source_preparation: SpineAccessCandidatePreparationResult,
    *,
    decision_ledger: AgentDecisionLedger | str | Path | None = None,
    publication_authority: PublicationDestinationAuthority | None = None,
) -> CompilationResult:
    """Atomically publish one freshly validated, human-governed Reference SATN.

    The normal compiler entry point remains unchanged.  This boundary always
    performs a fresh baseline/replay validation before reaching the established
    atomic publisher, and never treats a Reference selection as delivery or
    publication authority by itself.
    """

    council = (
        config
        if isinstance(config, (AreaDefinition, CouncilConfig))
        else AreaDefinition.from_yaml(config)
    )
    started = time.perf_counter()
    with StageHeartbeat(
        LOGGER,
        "reference-publication",
        {"area_id": council.area_id, "snapshot_id": council.source.snapshot_id},
    ) as heartbeat:
        runtime = (
            AgentRuntimeProvider(lambda: runtime_for(council.compilation.agent))
            if council.compilation.agent.response_mode == "direct-runtime"
            and council.compilation.agent.review_statuses
            else None
        )
        compiled = compile_reference_network(
            council,
            runtime,
            reference,
            source_preparation,
            decision_ledger=decision_ledger,
            heartbeat=heartbeat,
        )
        record = compiled.reference_satn_publication
        if record is None:  # Defensive: the dedicated boundary must bind provenance.
            raise ValueError("Reference compilation produced no publication provenance")
        run_fingerprint = json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "area_id": council.area_id,
                "snapshot_id": council.source.snapshot_id,
                "reference_publication_fingerprint": record.reference_publication_fingerprint,
                "compilation_input_fingerprint": compiled.compilation_input_fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        run_id = f"reference-{hashlib.sha256(run_fingerprint.encode()).hexdigest()[:12]}"
        heartbeat.set_stage("reference-publication")
        compilation_metadata = _compilation_metadata(started)
        artifacts = publish(
            council,
            compiled,
            run_id,
            publication_authority=publication_authority,
            compilation_metadata=compilation_metadata,
        )
    return CompilationResult(
        run_id=run_id,
        status=compiled.status,
        output_dir=council.publication.output_dir,
        connections=compiled.connection_count,
        gaps=len(compiled.gaps),
        artifacts=artifacts,
        criteria=compiled.criteria,
        agent_records=compiled.agent_records,
        divergence_records=compiled.divergence_records,
        metadata={
            "network_model": "backbone-outward",
            "compilation_input_fingerprint": compiled.compilation_input_fingerprint,
            "reference_satn": record.revalidated().publication_payload(),
            "compilation_diagnostics": compiled.compilation_diagnostics,
            "compilation_metadata": compilation_metadata,
        },
    )


def _copy_compilation_source(
    source: dict[str, gpd.GeoDataFrame],
) -> dict[str, gpd.GeoDataFrame]:
    """Give baseline and final compilation independent current-input frames."""

    return {name: frame.copy(deep=True) for name, frame in source.items()}


def _compiled_network_bundle_implementation_fingerprint() -> str:
    """Bind reuse to both compiler semantics and the exact installed wire codec."""

    codec_path = Path(compiled_network_bundle_codec.__file__ or "")
    if not codec_path.is_file():
        raise ValueError("compiled network bundle codec source is unavailable")
    return hashlib.sha256(
        json.dumps(
            {
                "compiler": _compiler_digest(),
                "codec": hashlib.sha256(codec_path.read_bytes()).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _compiled_network_bundle_specification(
    council: AreaConfig,
    *,
    governed_input_fingerprint: str,
    input_fingerprint: str,
    dependency_manifest: dict[str, object],
    evidence_state_fingerprint: str | None,
    upstream_artifact_ids: tuple[str, ...] = (),
    routing_input_identity: str | None = None,
) -> ArtifactSpecification:
    dependency_identity = dependency_manifest.get("sha256")
    if (
        not isinstance(dependency_identity, str)
        or _SHA256_PATTERN.fullmatch(dependency_identity) is None
    ):
        raise ValueError("compilation dependency manifest has no SHA-256 digest")
    snapshot_identity = snapshot_manifest_sha256(council)
    coverage = (snapshot_identity,) + (
        (evidence_state_fingerprint,) if evidence_state_fingerprint is not None else ()
    )
    parameters: dict[str, object] = {
        "area_identity": area_definition_sha256(council),
        "input_identity": input_fingerprint,
        "governed_input_fingerprint": governed_input_fingerprint,
        "dependency_identity": dependency_identity,
        "snapshot_identity": snapshot_identity,
    }
    if routing_input_identity is not None:
        parameters["routing_input_identity"] = routing_input_identity
    return ArtifactSpecification(
        kind="compiled-network-bundle",
        contract_version="satn-compiled-network-bundle/v1",
        implementation_fingerprint=_compiled_network_bundle_implementation_fingerprint(),
        dependency_manifest_fingerprint=dependency_identity,
        parameters=parameters,
        upstream_artifact_ids=upstream_artifact_ids,
        partition_identities=(council.area_id,),
        coverage_identities=coverage,
        validation_contract="satn-compiled-network-bundle-strict/v1",
        diagnostics={"compiler_path": "network"},
    )


def _compiled_network_bundle_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _routing_frame_payload(
    name: str,
    frame: gpd.GeoDataFrame,
) -> dict[str, object]:
    """Return the canonical, input-order-independent source-frame wire."""

    candidates = {
        "boundary": (("boundary_id",),),
        "places": (("place_id",),),
        "network": (
            ("source_id", "u", "v", "key"),
            ("source_id", "u", "v"),
            ("source_id",),
        ),
        "context": (("evidence_id",),),
        "official_road_classification": (("official_feature_id",),),
        "elevation_evidence": (("evidence_id",),),
    }.get(name, ())
    stable_keys = None
    for keys in candidates:
        if all(key in frame.columns for key in keys):
            values = frame.loc[:, list(keys)]
            if not values.isna().any(axis=None) and not values.duplicated().any():
                stable_keys = keys
                break
    identity_frame = frame.copy(deep=True)
    for column in identity_frame.columns:
        if str(identity_frame[column].dtype) != "geometry":
            identity_frame[column] = identity_frame[column].map(
                _normalise_routing_identity_value
            )
    return compiled_network_bundle_codec.encode_geodataframe(
        identity_frame,
        stable_key_columns=stable_keys,
    )


def _normalise_routing_identity_value(value: Any) -> Any:
    """Make array-valued source cells explicit in the canonical identity wire."""

    if isinstance(value, np.ndarray):
        return {
            "__satn_numpy_array__": {
                "dtype": value.dtype.str,
                "shape": list(value.shape),
                "values": _normalise_routing_identity_value(value.tolist()),
            }
        }
    if isinstance(value, tuple):
        return tuple(_normalise_routing_identity_value(item) for item in value)
    if isinstance(value, list):
        return [_normalise_routing_identity_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _normalise_routing_identity_value(item)
            for key, item in value.items()
        }
    return value


def _routing_source_identities(
    council: AreaConfig,
    source: dict[str, gpd.GeoDataFrame],
) -> tuple[str, str]:
    """Bind routing reuse to the exact canonical source content it consumes."""

    names = (
        "boundary",
        "places",
        "network",
        "context",
        "official_road_classification",
        "elevation_evidence",
    )
    frames = {
        name: (
            _routing_frame_payload(name, source[name])["content_sha256"]
            if name in source
            else None
        )
        for name in names
    }
    source_payload = {
        "contract": "satn-routing-source-input/v1",
        "frames": frames,
    }
    source_identity = hashlib.sha256(
        json.dumps(
            source_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    area_payload = {
        "contract": "satn-routing-area-input/v1",
        "area_id": council.area_id,
        "boundary": frames["boundary"],
    }
    area_identity = hashlib.sha256(
        json.dumps(
            area_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return area_identity, source_identity


def _routing_snapshot_area_identity(council: AreaConfig) -> str:
    """Derive the routing boundary identity without loading the full snapshot."""

    snapshot_root = council.source.snapshot_dir / council.source.snapshot_id
    manifest_path = snapshot_root / "snapshot.json"
    boundary_path = snapshot_root / "boundary.geojson"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or boundary_path.is_symlink()
        or not boundary_path.is_file()
    ):
        raise ValueError("routing boundary snapshot input is unavailable")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("routing snapshot manifest is invalid") from error
    file_hashes = manifest.get("file_sha256") if isinstance(manifest, dict) else None
    if not isinstance(file_hashes, dict) or file_hashes.get("boundary.geojson") != hashlib.sha256(
        boundary_path.read_bytes()
    ).hexdigest():
        raise ValueError("routing boundary snapshot digest differs from manifest")
    boundary = gpd.read_file(boundary_path)
    boundary_identity = _routing_frame_payload("boundary", boundary)["content_sha256"]
    if not isinstance(boundary_identity, str):
        raise ValueError("routing boundary identity is invalid")
    return hashlib.sha256(
        json.dumps(
            {
                "contract": "satn-routing-area-input/v1",
                "area_id": council.area_id,
                "boundary": boundary_identity,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _routing_lineage_identity(
    council: AreaConfig,
    *,
    ledger: AgentDecisionLedger,
    dependency_manifest: dict[str, object],
) -> str:
    """Bind active route replay to source release and route configuration."""

    dependency_identity = dependency_manifest.get("sha256")
    if not isinstance(dependency_identity, str) or not _SHA256_PATTERN.fullmatch(
        dependency_identity
    ):
        raise ValueError("compilation dependency manifest has no SHA-256 digest")
    payload = {
        "contract": "satn-routing-lineage/v1",
        "area_definition": area_definition_sha256(council),
        "snapshot_identity": snapshot_manifest_sha256(council),
        "source_semantics": {
            "urban_place_types": list(council.source.urban_place_types),
            "urban_place_source_ids": list(council.source.urban_place_source_ids),
            "urban_settlement_form": council.source.urban_settlement_form.model_dump(
                mode="json"
            ),
            "urban_scope_buffer_km": council.source.urban_scope_buffer_km,
        },
        "routing": {
            "max_connection_km": council.compilation.max_connection_km,
            "topography": council.compilation.topography.model_dump(mode="json"),
            "agent": council.compilation.agent.model_dump(mode="json"),
            "decision_ledger": ledger.model_dump(mode="json"),
        },
        "dependency_identity": dependency_identity,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _routing_input_identity_from_lineage(
    *,
    area_identity: str,
    lineage_identity: str,
) -> str:
    """Derive replay identity from governed area and route lineage only."""

    return hashlib.sha256(
        json.dumps(
            {
                "contract": "satn-routing-input/v2",
                "area_identity": area_identity,
                "lineage_identity": lineage_identity,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _routing_input_identity(
    council: AreaConfig,
    source: dict[str, gpd.GeoDataFrame],
    *,
    ledger: AgentDecisionLedger,
    dependency_manifest: dict[str, object],
) -> tuple[str, str]:
    """Return stage-scoped area/input identities for routing assembly reuse."""

    dependency_identity = dependency_manifest.get("sha256")
    if (
        not isinstance(dependency_identity, str)
        or _SHA256_PATTERN.fullmatch(dependency_identity) is None
    ):
        raise ValueError("compilation dependency manifest has no SHA-256 digest")
    area_identity, _ = _routing_source_identities(council, source)
    lineage_identity = _routing_lineage_identity(
        council,
        ledger=ledger,
        dependency_manifest=dependency_manifest,
    )
    return area_identity, _routing_input_identity_from_lineage(
        area_identity=area_identity,
        lineage_identity=lineage_identity,
    )


def _routing_bundle_implementation_fingerprint() -> str:
    """Bind route reuse to both canonical codecs and compiler semantics."""

    route_codec_path = compilation_dependencies._package_root() / "routing_assembly_bundle.py"
    compiled_codec_path = Path(compiled_network_bundle_codec.__file__ or "")
    if not route_codec_path.is_file() or not compiled_codec_path.is_file():
        raise ValueError("routing bundle codec source is unavailable")
    return hashlib.sha256(
        json.dumps(
            {
                "compiler": _compiler_digest(),
                "compiled_codec": hashlib.sha256(
                    compiled_codec_path.read_bytes()
                ).hexdigest(),
                "routing_codec": hashlib.sha256(route_codec_path.read_bytes()).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _routing_bundle_specification(
    council: AreaConfig,
    source: dict[str, gpd.GeoDataFrame],
    *,
    ledger: AgentDecisionLedger,
    dependency_manifest: dict[str, object],
) -> tuple[ArtifactSpecification, str]:
    dependency_identity = dependency_manifest.get("sha256")
    if not isinstance(dependency_identity, str):
        raise ValueError("compilation dependency manifest has no SHA-256 digest")
    area_identity, input_identity = _routing_input_identity(
        council,
        source,
        ledger=ledger,
        dependency_manifest=dependency_manifest,
    )
    snapshot_identity = snapshot_manifest_sha256(council)
    return (
        ArtifactSpecification(
            kind="routing-assembly-bundle",
            contract_version="satn-routing-assembly-bundle/v1",
            implementation_fingerprint=_routing_bundle_implementation_fingerprint(),
            dependency_manifest_fingerprint=dependency_identity,
            parameters={
                "area_identity": area_identity,
                "input_identity": input_identity,
                "lineage_identity": _routing_lineage_identity(
                    council,
                    ledger=ledger,
                    dependency_manifest=dependency_manifest,
                ),
                "dependency_identity": dependency_identity,
                "snapshot_identity": snapshot_identity,
            },
            upstream_artifact_ids=(),
            partition_identities=(council.area_id,),
            coverage_identities=(snapshot_identity,),
            validation_contract="satn-routing-assembly-bundle-strict/v1",
            diagnostics={"compiler_path": "network", "stage": "routing-assembly"},
        ),
        input_identity,
    )


def _decode_retained_routing_bundle(
    store: RetainedArtifactStore,
    artifact: RetainedArtifact,
) -> RoutingAssemblyBundle | None:
    try:
        if tuple(item.role for item in artifact.manifest.outputs) != (
            "routing-assembly-bundle",
        ):
            raise ValueError("routing artifact output roster is invalid")
        payload = json.loads(artifact.read_output("routing-assembly-bundle"))
        parameters = artifact.manifest.identity_payload().get("parameters")
        if not isinstance(parameters, dict):
            raise ValueError("routing artifact parameters are invalid")
        expected_identities = {
            "area": parameters.get("area_identity"),
            "input": parameters.get("input_identity"),
            "dependency": parameters.get("dependency_identity"),
        }
        if payload.get("identities") != expected_identities:
            raise ValueError("routing bundle identities differ from manifest")
        if payload.get("upstream_artifact_ids") != list(
            artifact.manifest.upstream_artifact_ids
        ):
            raise ValueError("routing bundle upstream identities differ from manifest")
        decoded = decode_routing_assembly_bundle(payload, RoutingAssemblyBundle)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        LOGGER.warning(
            "Retained routing assembly failed semantic validation; rebuilding reason=%s",
            error,
        )
        store.reject_semantic_artifact(
            artifact.artifact_id, reason="routing-contract-invalid"
        )
        return None
    return decoded


def _artifact_manifest_matches_specification(
    manifest: object,
    specification: ArtifactSpecification,
    *,
    upstream_artifact_ids: tuple[str, ...] | None = None,
) -> bool:
    """Compare every semantic manifest field, including exact upstream IDs."""

    expected_upstream = (
        specification.upstream_artifact_ids
        if upstream_artifact_ids is None
        else upstream_artifact_ids
    )
    return (
        getattr(manifest, "kind", None) == specification.kind
        and getattr(manifest, "contract_version", None) == specification.contract_version
        and getattr(manifest, "status", None) == specification.status
        and getattr(manifest, "implementation_fingerprint", None)
        == specification.implementation_fingerprint
        and getattr(manifest, "dependency_manifest_fingerprint", None)
        == specification.dependency_manifest_fingerprint
        and getattr(manifest, "parameters", None) == specification.parameters
        and getattr(manifest, "upstream_artifact_ids", None) == expected_upstream
        and getattr(manifest, "partition_identities", None) == specification.partition_identities
        and getattr(manifest, "coverage_identities", None) == specification.coverage_identities
        and getattr(manifest, "validation_contract", None) == specification.validation_contract
        and getattr(manifest, "diagnostics", None) == specification.diagnostics
    )


def _prepare_active_semantic_reuse(
    council: AreaConfig,
    *,
    base_specification: ArtifactSpecification,
    input_fingerprint: str,
    governed_input_fingerprint: str,
    evidence_state_fingerprint: str | None,
    ledger: AgentDecisionLedger,
    dependency_manifest: dict[str, object],
    artifact_store: RetainedArtifactStore,
) -> tuple[
    CompiledNetwork | None,
    RetainedArtifact | None,
    RetainedArtifact | None,
    RoutingAssemblyBundle | None,
    str | None,
    str,
]:
    """Resolve a semantic artifact through one exact active-lineage pin.

    The semantic manifest is the authority for its route dependency.  We
    validate that target, its complete closure, the one route upstream and the
    route wire before accepting the source-free hit.  A miss is intentionally
    fail-closed and leaves normal source/routing resolution to the caller.
    """

    pin_resolution = artifact_store.resolve_active_lineage(input_fingerprint)
    if pin_resolution.artifact is None:
        return None, None, None, None, None, pin_resolution.reason
    semantic_artifact = pin_resolution.artifact
    manifest = semantic_artifact.manifest
    upstream = manifest.upstream_artifact_ids
    if (
        manifest.kind != "compiled-network-bundle"
        or manifest.contract_version != base_specification.contract_version
        or len(upstream) != 1
    ):
        return None, None, None, None, None, "active-lineage-semantic-input-mismatch"
    route_id = upstream[0]
    route_resolution = artifact_store.resolve(route_id)
    if route_resolution.artifact is None:
        return (
            None,
            None,
            None,
            None,
            None,
            f"active-lineage-route-{route_resolution.reason}",
        )
    route_artifact = route_resolution.artifact
    route_manifest = route_artifact.manifest
    if (
        route_manifest.kind != "routing-assembly-bundle"
        or route_manifest.contract_version != "satn-routing-assembly-bundle/v1"
        or route_manifest.implementation_fingerprint
        != _routing_bundle_implementation_fingerprint()
        or route_manifest.dependency_manifest_fingerprint
        != base_specification.dependency_manifest_fingerprint
        or route_manifest.validation_contract
        != "satn-routing-assembly-bundle-strict/v1"
        or route_manifest.coverage_identities
        != (snapshot_manifest_sha256(council),)
        or route_manifest.upstream_artifact_ids
    ):
        return None, None, None, None, None, "active-lineage-route-invalid"
    if route_manifest.identity_payload().get("diagnostics") != {
        "compiler_path": "network",
        "stage": "routing-assembly",
    }:
        return None, None, None, None, None, "active-lineage-route-invalid"
    route_specification = ArtifactSpecification(
        kind=route_manifest.kind,
        contract_version=route_manifest.contract_version,
        implementation_fingerprint=route_manifest.implementation_fingerprint,
        dependency_manifest_fingerprint=route_manifest.dependency_manifest_fingerprint,
        parameters=route_manifest.identity_payload()["parameters"],
        upstream_artifact_ids=route_manifest.upstream_artifact_ids,
        partition_identities=route_manifest.partition_identities,
        coverage_identities=route_manifest.coverage_identities,
        validation_contract=route_manifest.validation_contract,
        diagnostics=route_manifest.identity_payload()["diagnostics"],
        status=route_manifest.status,
    )
    route_exact = artifact_store.resolve_specification(route_specification)
    if route_exact.artifact is None:
        return None, None, None, None, None, route_exact.reason
    if route_exact.artifact.artifact_id != route_artifact.artifact_id:
        return None, None, None, None, None, "nondeterministic-routing-candidates"
    route_parameters = route_manifest.identity_payload().get("parameters")
    try:
        expected_route_area_identity = _routing_snapshot_area_identity(council)
        expected_route_lineage_identity = _routing_lineage_identity(
            council,
            ledger=ledger,
            dependency_manifest=dependency_manifest,
        )
    except (OSError, TypeError, ValueError) as error:
        LOGGER.info("Active routing lineage validation unavailable: %s", error)
        return None, None, None, None, None, "active-lineage-route-input-unavailable"
    semantic_parameters = {
        "area_identity": expected_route_area_identity,
        "lineage_identity": expected_route_lineage_identity,
        "dependency_identity": base_specification.dependency_manifest_fingerprint,
        "snapshot_identity": snapshot_manifest_sha256(council),
    }
    if not isinstance(route_parameters, dict):
        return None, None, None, None, None, "active-lineage-route-parameters-invalid"
    for key in (
        "area_identity",
        "lineage_identity",
        "dependency_identity",
        "snapshot_identity",
    ):
        if route_parameters.get(key) != semantic_parameters.get(key):
            return None, None, None, None, None, "active-lineage-route-input-mismatch"
    expected_route_input_identity = _routing_input_identity_from_lineage(
        area_identity=expected_route_area_identity,
        lineage_identity=expected_route_lineage_identity,
    )
    if route_parameters.get("input_identity") != expected_route_input_identity:
        return None, None, None, None, None, "active-lineage-route-input-mismatch"
    routing_bundle = _decode_retained_routing_bundle(artifact_store, route_artifact)
    if routing_bundle is None:
        return None, None, None, None, None, "active-lineage-route-contract-invalid"
    route_input_fingerprint = route_parameters.get("input_identity")
    if not isinstance(route_input_fingerprint, str) or not _SHA256_PATTERN.fullmatch(
        route_input_fingerprint
    ):
        return None, None, None, None, None, "active-lineage-route-input-invalid"
    semantic_specification = _compiled_network_bundle_specification(
        council,
        governed_input_fingerprint=governed_input_fingerprint,
        input_fingerprint=input_fingerprint,
        dependency_manifest=dependency_manifest,
        evidence_state_fingerprint=evidence_state_fingerprint,
        upstream_artifact_ids=(route_id,),
        routing_input_identity=route_input_fingerprint,
    )
    if not _artifact_manifest_matches_specification(manifest, semantic_specification):
        return None, None, None, None, None, "active-lineage-semantic-input-mismatch"
    exact = artifact_store.resolve_specification(semantic_specification)
    if exact.artifact is None:
        return None, None, None, None, None, exact.reason
    if exact.artifact.artifact_id != semantic_artifact.artifact_id:
        return None, None, None, None, None, "nondeterministic-output-candidates"
    compiled = _decode_retained_compiled_network(artifact_store, semantic_artifact)
    if compiled is None:
        return None, None, None, None, None, "semantic-contract-invalid"
    return (
        compiled,
        semantic_artifact,
        route_artifact,
        routing_bundle,
        route_input_fingerprint,
        "validated-active-lineage",
    )


def _prepare_routing_reuse(
    council: AreaConfig,
    source: dict[str, gpd.GeoDataFrame],
    *,
    ledger: AgentDecisionLedger,
    dependency_manifest: dict[str, object],
    governed_input_fingerprint: str,
    artifact_store: RetainedArtifactStore | None,
    rebuild_stages: tuple[str, ...],
) -> tuple[
    str,
    ArtifactSpecification | None,
    RoutingAssemblyBundle | None,
    RetainedArtifact | None,
    str,
    str,
]:
    """Resolve a safe route hit; identity/retention failure remains a cold compile."""

    try:
        _, routing_input_fingerprint = _routing_input_identity(
            council,
            source,
            ledger=ledger,
            dependency_manifest=dependency_manifest,
        )
        if artifact_store is None:
            return (
                routing_input_fingerprint,
                None,
                None,
                None,
                "unavailable",
                "retained-store-unavailable",
            )
        specification, specified_input = _routing_bundle_specification(
            council,
            source,
            ledger=ledger,
            dependency_manifest=dependency_manifest,
        )
        if specified_input != routing_input_fingerprint:
            raise ValueError("routing specification input identity is inconsistent")
        routing_forced = council.compilation.full or bool(
            set(rebuild_stages).intersection(_ROUTING_BYPASS_STAGES)
        )
        if routing_forced:
            return (
                routing_input_fingerprint,
                specification,
                None,
                None,
                "unavailable",
                "forced-stage",
            )
        resolution = artifact_store.resolve_specification(specification)
        if resolution.artifact is None:
            return (
                routing_input_fingerprint,
                specification,
                None,
                None,
                "unavailable",
                resolution.reason,
            )
        bundle = _decode_retained_routing_bundle(artifact_store, resolution.artifact)
        if bundle is None:
            return (
                routing_input_fingerprint,
                specification,
                None,
                None,
                "unavailable",
                "routing-contract-invalid",
            )
        LOGGER.info(
            "Retained routing assembly reused artifact=%s",
            resolution.artifact.artifact_id,
        )
        return (
            routing_input_fingerprint,
            specification,
            bundle,
            resolution.artifact,
            "hit",
            "validated-routing-assembly",
        )
    except (OSError, TypeError, ValueError) as error:
        LOGGER.warning("Routing assembly reuse unavailable; compiling cold: %s", error)
        return (
            governed_input_fingerprint,
            None,
            None,
            None,
            "unavailable",
            "routing-identity-unavailable",
        )


def _decode_retained_compiled_network(
    store: RetainedArtifactStore,
    artifact: RetainedArtifact,
) -> CompiledNetwork | None:
    try:
        if tuple(item.role for item in artifact.manifest.outputs) != (
            "compiled-network-bundle",
        ):
            raise ValueError("compiled network artifact output roster is invalid")
        payload = json.loads(artifact.read_output("compiled-network-bundle"))
        manifest_payload = artifact.manifest.identity_payload()
        parameters = manifest_payload.get("parameters")
        if not isinstance(parameters, dict):
            raise ValueError("compiled network artifact parameters are invalid")
        expected_identities = {
            "area": parameters.get("area_identity"),
            "input": parameters.get("input_identity"),
            "dependency": parameters.get("dependency_identity"),
        }
        if payload.get("identities") != expected_identities:
            raise ValueError("compiled network bundle identities differ from manifest")
        if payload.get("upstream_artifact_ids") != list(
            artifact.manifest.upstream_artifact_ids
        ):
            raise ValueError("compiled network bundle upstream identities differ from manifest")
        decoded = compiled_network_bundle_codec.decode_compiled_network_bundle(
            payload, CompiledNetwork
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        LOGGER.warning(
            "Retained compiled network bundle failed semantic validation; rebuilding reason=%s",
            error,
        )
        store.reject_semantic_artifact(
            artifact.artifact_id, reason="semantic-contract-invalid"
        )
        return None
    if not isinstance(decoded, CompiledNetwork):
        store.reject_semantic_artifact(
            artifact.artifact_id, reason="semantic-contract-invalid"
        )
        return None
    return decoded


def _compile(
    config: AreaConfig,
    *,
    decision_ledger: AgentDecisionLedger | str | Path | None = None,
    officer_decisions: tuple[PreloadedOfficerDecision, ...] = (),
    publication_authority: PublicationDestinationAuthority | None = None,
    heartbeat: StageHeartbeat | None = None,
    compiler_path: CompilerPath = "network",
    evidence_store: LocalEvidenceStore | None = None,
    evidence_state_fingerprint: str | None = None,
    rebuild_stages: tuple[str, ...] = (),
    artifact_store: RetainedArtifactStore | None = None,
) -> CompilationResult:
    """Compile a parsed area definition, reporting its current long-running stage."""
    if compiler_path not in {"network", "ea-recovery"}:
        raise ValueError(f"unsupported orchestration compiler path: {compiler_path}")
    recovery_candidate = compiler_path == "ea-recovery"
    started = time.perf_counter()
    council = config
    officer_decisions = canonical_officer_decisions(officer_decisions)
    ledger = _load_decision_ledger(decision_ledger)
    dependency_manifest = compilation_dependency_manifest(
        council,
        compiler_path=compiler_path,
    )
    governed_input_fingerprint = compilation_governed_input_fingerprint(
        council,
        dependency_manifest=dependency_manifest,
        evidence_state_fingerprint=evidence_state_fingerprint,
        traffic_match_policy_fingerprint=(
            council.compilation.network_selection.traffic_match_policy_fingerprint
            if council.compilation.network_selection is not None
            else None
        ),
    )
    input_fingerprint = decision_ledger_input_fingerprint(
        governed_input_fingerprint,
        ledger,
        officer_decisions=officer_decisions,
    )
    if evidence_store is not None:
        try:
            coverage = evidence_store.resolve_coverage(
                state_fingerprint=evidence_state_fingerprint or ""
            )
            if coverage.fingerprint != evidence_state_fingerprint:
                raise ValueError(
                    "resolved coverage state fingerprint does not match evidence_state"
                )
        except Exception as error:
            reviewable = terminal_reviewable_network_for_governed_evidence(
                None,
                officer_decisions,
                detail=(
                    "DfT Local Evidence Store coverage state could not be verified: "
                    + str(error)
                ),
            )
            return _reviewable_terminal_result(council, input_fingerprint, reviewable)
    decision_resolver = AgentDecisionResolver(ledger, governed_input_fingerprint)
    LOGGER.info(
        "Compilation started council=%s snapshot=%s schema=%s",
        council.area_id,
        council.source.snapshot_id,
        SCHEMA_VERSION,
    )
    presentation_only_rebuild = set(rebuild_stages) == {"presentation"}
    semantic_rebuild = bool(rebuild_stages) and not presentation_only_rebuild
    reused = (
        None
        if recovery_candidate or semantic_rebuild
        else _reuse_validated_publication(
            council,
            governed_input_fingerprint,
            input_fingerprint,
            dependency_manifest,
            officer_decisions=officer_decisions,
            publication_authority=publication_authority,
            force_presentation_republish=presentation_only_rebuild,
        )
    )
    if semantic_rebuild:
        LOGGER.info(
            "Validated publication reuse disabled by targeted retained-stage rebuild stages=%s",
            ",".join(rebuild_stages),
        )
    if reused is not None:
        return reused
    bundle_specification = None
    semantic_bundle_artifact = None
    semantic_bundle_disposition = "unavailable"
    semantic_bundle_reason = "retained-store-unavailable"
    routing_specification = None
    routing_bundle: RoutingAssemblyBundle | None = None
    routing_bundle_artifact = None
    routing_bundle_disposition = "unavailable"
    routing_bundle_reason = "retained-store-unavailable"
    routing_input_fingerprint = None
    captured_routing: list[RoutingAssemblyBundle] | None = None
    compiled: CompiledNetwork | None = None
    if artifact_store is not None and not recovery_candidate:
        bundle_specification = _compiled_network_bundle_specification(
            council,
            governed_input_fingerprint=governed_input_fingerprint,
            input_fingerprint=input_fingerprint,
            dependency_manifest=dependency_manifest,
            evidence_state_fingerprint=evidence_state_fingerprint,
        )
    if (
        artifact_store is not None
        and bundle_specification is not None
        and not council.compilation.full
        and not semantic_rebuild
    ):
        try:
            active_semantic_resolution = _prepare_active_semantic_reuse(
                council,
                base_specification=bundle_specification,
                input_fingerprint=input_fingerprint,
                governed_input_fingerprint=governed_input_fingerprint,
                evidence_state_fingerprint=evidence_state_fingerprint,
                ledger=ledger,
                dependency_manifest=dependency_manifest,
                artifact_store=artifact_store,
            )
        except (OSError, TypeError, ValueError) as error:
            LOGGER.warning(
                "Active semantic reuse unavailable; compiling governed inputs: %s",
                error,
            )
            active_semantic_resolution = (
                None,
                None,
                None,
                None,
                None,
                "active-lineage-unavailable",
            )
        (
            compiled,
            semantic_bundle_artifact,
            routing_bundle_artifact,
            routing_bundle,
            routing_input_fingerprint,
            semantic_bundle_reason,
        ) = active_semantic_resolution
        if compiled is not None:
            semantic_bundle_disposition = "hit"
            routing_bundle_disposition = "hit"
            routing_bundle_reason = "validated-routing-assembly-upstream"
            LOGGER.info(
                "Retained compiled network reused through active lineage artifact=%s",
                semantic_bundle_artifact.artifact_id
                if semantic_bundle_artifact is not None
                else "unknown",
            )
    source = None
    if compiled is None:
        if heartbeat is not None:
            heartbeat.set_stage("snapshot-load")
        source = (
            load_legacy_ea_recovery_snapshot(council)
            if recovery_candidate
            else load_snapshot(council)
        )
        LOGGER.info(
            "Snapshot loaded places=%d road_edges=%d context_features=%d",
            len(source["places"]),
            len(source["network"]),
            len(source.get("context", [])),
        )
        if not recovery_candidate:
            (
                routing_input_fingerprint,
                routing_specification,
                routing_bundle,
                routing_bundle_artifact,
                routing_bundle_disposition,
                routing_bundle_reason,
            ) = _prepare_routing_reuse(
                council,
                source,
                ledger=ledger,
                dependency_manifest=dependency_manifest,
                governed_input_fingerprint=governed_input_fingerprint,
                artifact_store=artifact_store,
                rebuild_stages=rebuild_stages,
            )
        # A semantic artifact is a descendant of exactly one validated route
        # artifact.  Form its exact specification only after routing has hit or
        # been retained; a missing route must never produce a falsely rootless
        # semantic artifact.
        if (
            compiled is None
            and artifact_store is not None
            and bundle_specification is not None
            and routing_bundle_artifact is not None
        ):
            assert routing_input_fingerprint is not None
            bundle_specification = _compiled_network_bundle_specification(
                council,
                governed_input_fingerprint=governed_input_fingerprint,
                input_fingerprint=input_fingerprint,
                dependency_manifest=dependency_manifest,
                evidence_state_fingerprint=evidence_state_fingerprint,
                upstream_artifact_ids=(routing_bundle_artifact.artifact_id,),
                routing_input_identity=routing_input_fingerprint,
            )
            if not council.compilation.full and not semantic_rebuild:
                bundle_resolution = artifact_store.resolve_specification(
                    bundle_specification
                )
                semantic_bundle_reason = bundle_resolution.reason
                if bundle_resolution.artifact is not None:
                    compiled = _decode_retained_compiled_network(
                        artifact_store, bundle_resolution.artifact
                    )
                    if compiled is not None:
                        semantic_bundle_artifact = bundle_resolution.artifact
                        semantic_bundle_disposition = "hit"
                        semantic_bundle_reason = (
                            "validated-routing-dependent-compiled-network"
                        )
                        try:
                            artifact_store.pin(
                                "active-lineage",
                                input_fingerprint,
                                semantic_bundle_artifact.artifact_id,
                            )
                        except (OSError, TypeError, ValueError) as error:
                            LOGGER.warning("Active semantic lineage pin failed: %s", error)
                        LOGGER.info(
                            "Retained routing-dependent compiled network reused artifact=%s",
                            semantic_bundle_artifact.artifact_id,
                        )
    runtime = (
        AgentRuntimeProvider(lambda: runtime_for(council.compilation.agent))
        if compiled is None
        and council.compilation.agent.response_mode == "direct-runtime"
        and council.compilation.agent.review_statuses
        else None
    )
    atm_reference = None
    if compiled is None and council.atm.enabled and council.atm.mode == "seeded":
        assert source is not None
        if heartbeat is not None:
            heartbeat.set_stage("atm-seeded-load-reprojection")
        atm_reference = load_atm(council).to_crs(source["network"].crs)
    if compiled is None:
        assert source is not None
        if heartbeat is not None:
            heartbeat.set_stage("network-compilation")
        try:
            with governed_input_binding(
                officer_decisions=officer_decisions,
                evidence_store=evidence_store,
                evidence_state_fingerprint=evidence_state_fingerprint,
                routing_input_fingerprint=routing_input_fingerprint,
            ):
                if routing_bundle is not None:
                    with _routing_assembly_replay(routing_bundle):
                        compiled = compile_network(
                            council,
                            source,
                            runtime,
                            governed_input_fingerprint=governed_input_fingerprint,
                            decision_resolver=decision_resolver,
                            heartbeat=heartbeat,
                        )
                else:
                    with _routing_assembly_capture() as captured_routing:
                        compiled = compile_network(
                            council,
                            source,
                            runtime,
                            governed_input_fingerprint=governed_input_fingerprint,
                            decision_resolver=decision_resolver,
                            heartbeat=heartbeat,
                        )
        except AgentDecisionRequired as required:
            return _decision_required_result(
                council,
                input_fingerprint,
                required.request,
                ledger,
                required.applied_records,
                required.applied_divergence_records,
                required.validation,
            )
        except AgentCompilationTerminated as terminated:
            return _terminated_result(council, input_fingerprint, terminated)
        except GovernedEvidenceLoadError as error:
            reviewable = terminal_reviewable_network_for_governed_evidence(
                None,
                officer_decisions,
                detail=str(error),
            )
            return _reviewable_terminal_result(council, input_fingerprint, reviewable)
        if (
            routing_bundle_disposition != "hit"
            and artifact_store is not None
            and routing_specification is not None
            and captured_routing is not None
            and len(captured_routing) == 1
        ):
            try:
                routing_area_identity, _ = _routing_source_identities(council, source)
                dependency_identity = dependency_manifest.get("sha256")
                if not isinstance(dependency_identity, str):
                    raise ValueError("routing dependency identity is unavailable")
                if routing_input_fingerprint is None:
                    raise ValueError("routing input identity is unavailable")
                routing_payload = encode_routing_assembly_bundle(
                    captured_routing[0],
                    area_identity=routing_area_identity,
                    input_identity=routing_input_fingerprint,
                    dependency_identity=dependency_identity,
                    upstream_artifact_ids=routing_specification.upstream_artifact_ids,
                    bundle_crs=source["network"].crs,
                )
                routing_bundle_artifact = artifact_store.put(
                    routing_specification,
                    outputs={
                        "routing-assembly-bundle": _compiled_network_bundle_bytes(
                            routing_payload
                        )
                    },
                )
                routing_bundle = _decode_retained_routing_bundle(
                    artifact_store,
                    routing_bundle_artifact,
                )
                if routing_bundle is None:
                    raise ValueError("new routing assembly bundle failed validation")
                if bundle_specification is not None:
                    if routing_input_fingerprint is None:
                        raise ValueError("routing input identity is unavailable")
                    bundle_specification = _compiled_network_bundle_specification(
                        council,
                        governed_input_fingerprint=governed_input_fingerprint,
                        input_fingerprint=input_fingerprint,
                        dependency_manifest=dependency_manifest,
                        evidence_state_fingerprint=evidence_state_fingerprint,
                        upstream_artifact_ids=(routing_bundle_artifact.artifact_id,),
                        routing_input_identity=routing_input_fingerprint,
                    )
                routing_bundle_disposition = "build"
                routing_bundle_reason = "compiled-from-governed-routing-inputs"
            except (OSError, TypeError, ValueError) as error:
                routing_bundle_artifact = None
                routing_bundle_disposition = "unavailable"
                routing_bundle_reason = "retention-failed"
                LOGGER.warning("Routing assembly retention failed: %s", error)
    assert compiled is not None
    reviewable = compiled.reviewable_network
    if reviewable is not None and reviewable.status.value == "terminal-failure":
        return _reviewable_terminal_result(council, input_fingerprint, reviewable)
    if semantic_bundle_disposition != "hit":
        compiled.compilation_input_fingerprint = input_fingerprint
        compiled.governed_input_fingerprint = governed_input_fingerprint
        compiled.snapshot_manifest_sha256 = snapshot_manifest_sha256(council)
        compiled.area_definition_sha256 = area_definition_sha256(council)
        compiled.compilation_dependency_manifest = dependency_manifest
        LOGGER.info(
            "Network compiled connections=%d gaps=%d status=%s",
            compiled.connection_count,
            len(compiled.gaps),
            compiled.status,
        )
    if semantic_bundle_disposition != "hit" and council.atm.enabled:
        assert source is not None
        if council.atm.mode == "blind":
            if heartbeat is not None:
                heartbeat.set_stage("atm-blind-load-reprojection")
            atm_reference = load_atm(council).to_crs(source["network"].crs)
        if heartbeat is not None:
            heartbeat.set_stage("atm-comparison")
        try:
            compiled.divergence_records = compare_atm(
                compiled,
                atm_reference,
                runtime,
                council,
                decision_resolver,
            )
        except AgentDecisionRequired as required:
            return _decision_required_result(
                council,
                input_fingerprint,
                required.request,
                ledger,
                required.applied_records,
                required.applied_divergence_records,
                required.validation,
            )
        except AgentCompilationTerminated as terminated:
            return _terminated_result(council, input_fingerprint, terminated)
        if council.publication.audience == "local" or council.atm.redistribution_permitted:
            compiled.atm_reference = atm_reference
        unresolved = any(not record.resolved for record in compiled.divergence_records)
        compiled.criteria["atm_comparison"] = {
            "comparison_available": TrafficLight.GREEN,
            "unresolved_divergences": (TrafficLight.AMBER if unresolved else TrafficLight.GREEN),
        }
    if semantic_bundle_disposition != "hit":
        if heartbeat is not None:
            heartbeat.set_stage("post-compilation-artifact-preparation")
        unconsumed = {
            response.request_id for response in ledger.responses
        } - decision_resolver.consumed_request_ids
        if unconsumed:
            raise ValueError(
                "decision ledger contains responses that do not belong to this compilation: "
                + ", ".join(sorted(unconsumed))
            )
        compiled.decision_contract = ledger.decision_contract
        compiled.decision_ledger_input = ledger.model_dump(mode="json")
        # Execution order is not a durable audit order.  Persist the same canonical
        # response order used by the ledger contract, so downstream equality checks
        # cannot mistake a traversal-order difference for a different decision set.
        compiled.accepted_decisions = AgentDecisionLedger.model_validate(
            {
                "decision_contract": ledger.decision_contract,
                "responses": [
                    response.model_dump(mode="json")
                    for response in decision_resolver.accepted_responses
                ],
            }
        ).model_dump(mode="json")["responses"]
        if (
            artifact_store is not None
            and bundle_specification is not None
            and routing_bundle_artifact is not None
        ):
            assert source is not None
            try:
                bundle_payload = compiled_network_bundle_codec.encode_compiled_network_bundle(
                    compiled,
                    area_identity=area_definition_sha256(council),
                    input_identity=input_fingerprint,
                    dependency_identity=str(dependency_manifest["sha256"]),
                    upstream_artifact_ids=bundle_specification.upstream_artifact_ids,
                    bundle_crs=source["network"].crs,
                )
                semantic_bundle_artifact = artifact_store.put(
                    bundle_specification,
                    outputs={
                        "compiled-network-bundle": _compiled_network_bundle_bytes(
                            bundle_payload
                        )
                    },
                )
                semantic_resolution = artifact_store.resolve_specification(
                    bundle_specification
                )
                if semantic_resolution.artifact is None:
                    semantic_bundle_artifact = None
                    semantic_bundle_disposition = "unavailable"
                    semantic_bundle_reason = semantic_resolution.reason
                    LOGGER.warning(
                        "Compiled network bundle retention is not uniquely resolvable: %s",
                        semantic_resolution.reason,
                    )
                    raise ValueError("semantic-retention-not-unique")
                if (
                    semantic_resolution.artifact.artifact_id
                    != semantic_bundle_artifact.artifact_id
                ):
                    semantic_bundle_artifact = None
                    semantic_bundle_disposition = "unavailable"
                    semantic_bundle_reason = "semantic-retention-target-mismatch"
                    raise ValueError("semantic-retention-target-mismatch")
                canonical_compiled = _decode_retained_compiled_network(
                    artifact_store, semantic_bundle_artifact
                )
                if canonical_compiled is None:
                    raise ValueError(
                        "newly retained compiled network bundle failed validation"
                    )
                # Cold and retained paths publish the same canonical, validated
                # materialisation so byte-level publication equivalence is testable.
                compiled = canonical_compiled
                reviewable = compiled.reviewable_network
                semantic_bundle_disposition = "build"
                semantic_bundle_reason = "compiled-from-governed-inputs"
                try:
                    artifact_store.pin(
                        "active-lineage",
                        input_fingerprint,
                        semantic_bundle_artifact.artifact_id,
                    )
                except (OSError, TypeError, ValueError) as error:
                    # The artifact remains valid and dependency-complete; only
                    # the source-free active lookup is unavailable next time.
                    LOGGER.warning("Active semantic lineage pin failed: %s", error)
            except (OSError, TypeError, ValueError) as error:
                # Retention is an optimisation. A valid governed compilation must
                # still reach the existing atomic publisher when retention fails.
                if semantic_bundle_disposition != "unavailable":
                    semantic_bundle_disposition = "unavailable"
                    semantic_bundle_reason = "retention-failed"
                LOGGER.warning("Compiled network bundle retention failed: %s", error)
        elif artifact_store is not None and bundle_specification is not None:
            semantic_bundle_disposition = "unavailable"
            semantic_bundle_reason = "routing-retention-unavailable"
    if heartbeat is not None:
        heartbeat.set_stage("publication-fingerprint")
    run_fingerprint = json.dumps(
        {
            "council": council.area_id,
            "snapshot": council.source.snapshot_id,
            "schema_version": SCHEMA_VERSION,
            "criteria_version": council.compilation.criteria_version,
            "compilation_input_fingerprint": input_fingerprint,
            "dft_traffic_evidence_state_fingerprint": evidence_state_fingerprint,
            "dft_traffic_match_policy_fingerprint": (
                compiled.traffic_match_policy_fingerprint
            ),
            "spine_access_candidate_preparation_fingerprint": (
                compiled.spine_access_candidate_preparation.preparation_fingerprint
                if compiled.spine_access_candidate_preparation is not None
                else None
            ),
            "snapshot_manifest": hashlib.sha256(
                (
                    council.source.snapshot_dir / council.source.snapshot_id / "snapshot.json"
                ).read_bytes()
            ).hexdigest(),
            "context": sorted(
                evidence_id
                for frame in (
                    compiled.a_road_spines,
                    compiled.ncn_routes,
                    compiled.schools,
                    compiled.retail_centres,
                    compiled.healthcare,
                )
                for evidence_id in frame.get("evidence_id", [])
            ),
            "asset_accounting_fingerprint": hashlib.sha256(
                json.dumps(
                    compiled.asset_accounting,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "school_street_assessments": sorted(
                (
                    row.assessment_id,
                    row.assessment_status,
                    row.rationale,
                    row.evidence,
                    row.geometry.wkb_hex,
                )
                for row in compiled.school_street_assessments.itertuples()
            ),
            "topography_profiles": sorted(
                (
                    row.profile_id,
                    row.edge_id,
                    row.edge_type,
                    row.evidence_status,
                    row.distance_m,
                    row.forward_ascent_m,
                    row.forward_descent_m,
                    row.reverse_ascent_m,
                    row.reverse_descent_m,
                    row.steepest_sustained_gradient_pct,
                    row.steepest_sustained_gradient_rationale,
                    row.gradient_section_ids,
                    row.elevation_evidence_ids,
                    row.geometry.wkb_hex,
                )
                for row in compiled.topography_profiles.itertuples()
            ),
            "gradient_sections": sorted(
                (
                    row.section_id,
                    row.profile_id,
                    row.gradient_band,
                    row.length_m,
                    row.forward_gradient_pct,
                    row.geometry.wkb_hex,
                )
                for row in compiled.gradient_sections.itertuples()
            ),
            "elevation_corroboration": sorted(
                (
                    row.corroboration_id,
                    row.source_id,
                    row.osm_elevation,
                    row.osm_incline,
                    row.evidence_role,
                    row.geometry.wkb_hex,
                )
                for row in compiled.elevation_corroboration.itertuples()
            ),
            "strategic_spines": sorted(compiled.strategic_spines["spine_id"]),
            "urban_classification_status": compiled.urban_classification_status,
            "elevation_evidence_status": compiled.elevation_evidence_status,
            "urban_spines": sorted(
                (
                    row.structure_id,
                    row.official_classification,
                    row.source_id,
                    row.content_fingerprint,
                    row.geometry.wkb_hex,
                )
                for row in compiled.urban_spines.itertuples()
            ),
            "urban_classification_unknowns": sorted(
                (
                    row.structure_id,
                    row.official_feature_id,
                    row.source_id,
                    row.content_fingerprint,
                    row.geometry.wkb_hex,
                )
                for row in compiled.urban_classification_unknowns.itertuples()
            ),
            "candidate_low_traffic_areas": sorted(
                (
                    row.structure_id,
                    row.boundary_ids,
                    row.intervention_need,
                    row.observed_through_traffic_evidence_ids,
                    row.observed_through_traffic_source_ids,
                    row.geometry.wkb_hex,
                )
                for row in compiled.low_traffic_areas.itertuples()
            ),
            "low_traffic_area_portals": sorted(
                (
                    row.portal_id,
                    row.area_id,
                    row.boundary_id,
                    row.geometry.wkb_hex,
                )
                for row in compiled.low_traffic_area_portals.itertuples()
            ),
            "access_obligations": sorted(
                (
                    row.obligation_id,
                    row.service_status,
                    row.access_point_status,
                    row.access_point_source_id,
                    row.access_point_rationale,
                    row.low_traffic_area_id,
                    row.portal_id,
                    row.fabric_source_ids,
                    row.finding,
                    row.geometry.wkb_hex,
                )
                for row in compiled.access_obligations.itertuples()
            ),
            "spine_access_connections": sorted(
                (
                    row.access_connection_id,
                    row.community_id,
                    row.school_id,
                    row.access_point_status,
                    row.spine_id,
                    row.parent_target_id,
                    row.parent_target_name,
                    row.community_attachment_node,
                    row.community_attachment_distance_m,
                    row.spine_attachment_node,
                    row.spine_attachment_distance_m,
                    row.geometry.wkb_hex,
                )
                for row in compiled.spine_access_connections.itertuples()
            ),
            "spine_access_branches": sorted(
                (
                    row.branch_id,
                    row.root_spine_id,
                    row.connection_ids,
                    row.geometry.wkb_hex,
                )
                for row in compiled.spine_access_branches.itertuples()
            ),
            "branch_meeting_connections": sorted(
                (
                    row.meeting_connection_id,
                    row.from_place_id,
                    row.to_place_id,
                    row.from_root_spine_id,
                    row.to_root_spine_id,
                    row.geometry.wkb_hex,
                )
                for row in compiled.branch_meeting_connections.itertuples()
            ),
            "cross_spine_connectors": sorted(
                (
                    row.cross_spine_connector_id,
                    row.meeting_connection_id,
                    row.connection_ids,
                    row.geometry.wkb_hex,
                )
                for row in compiled.cross_spine_connectors.itertuples()
            ),
            "atm_mode": council.atm.mode if council.atm.enabled else "disabled",
        },
        sort_keys=True,
    )
    run_id = f"run-{hashlib.sha256(run_fingerprint.encode()).hexdigest()[:12]}"
    compilation_metadata = _compilation_metadata(started)
    if heartbeat is not None:
        heartbeat.set_stage("publication")
    artifacts = (
        retain_ea_recovery_candidate(council, compiled, run_id)
        if recovery_candidate
        else publish(
            council,
            compiled,
            run_id,
            publication_authority=publication_authority,
            compilation_metadata=compilation_metadata,
        )
    )
    if not recovery_candidate:
        LOGGER.info(
            "Publication validated output=%s elapsed_seconds=%.1f",
            council.publication.output_dir,
            time.perf_counter() - started,
        )
    else:
        LOGGER.info(
            "Non-publishing compilation artifact validated artifacts=%s elapsed_seconds=%.1f",
            sorted(artifacts),
            time.perf_counter() - started,
        )
    public_status = compiled.status
    if reviewable is not None and (
        reviewable.status.value != "complete" or reviewable.network_gaps
    ):
        public_status = "reviewable"
    return CompilationResult(
        run_id=run_id,
        status=public_status,
        output_dir=council.publication.output_dir,
        connections=compiled.connection_count,
        gaps=len(compiled.gaps) + (len(reviewable.network_gaps) if reviewable else 0),
        artifacts=artifacts,
        criteria=compiled.criteria,
        agent_records=compiled.agent_records,
        divergence_records=compiled.divergence_records,
        metadata={
            "network_model": "backbone-outward",
            "compilation_input_fingerprint": input_fingerprint,
            "compilation_metadata": compilation_metadata,
            "semantic_compilation_reused": semantic_bundle_disposition == "hit",
            "semantic_bundle_disposition": semantic_bundle_disposition,
            "semantic_bundle_reason": semantic_bundle_reason,
            **(
                {"semantic_bundle_artifact_id": semantic_bundle_artifact.artifact_id}
                if semantic_bundle_artifact is not None
                else {}
            ),
            "routing_input_fingerprint": routing_input_fingerprint,
            "routing_bundle_disposition": routing_bundle_disposition,
            "routing_bundle_reason": routing_bundle_reason,
            **(
                {"routing_bundle_artifact_id": routing_bundle_artifact.artifact_id}
                if routing_bundle_artifact is not None
                else {}
            ),
            "dft_traffic_evidence_state_fingerprint": evidence_state_fingerprint,
            "dft_traffic_match_policy_fingerprint": (
                compiled.traffic_match_policy_fingerprint
            ),
            "compilation_diagnostics": compiled.compilation_diagnostics,
            **(
                {"reviewable_network": reviewable.metadata}
                if reviewable is not None
                else {}
            ),
            "human_intervention_requests": [
                request.model_dump(mode="json") for request in compiled.human_intervention_requests
            ],
            "network_units": compiled.network_units,
            "urban_classification_status": compiled.urban_classification_status,
            "elevation_evidence_status": compiled.elevation_evidence_status,
            **(
                {
                    "spine_access_candidate_preparation": (
                        compiled.spine_access_candidate_preparation.metadata()
                    )
                }
                if compiled.spine_access_candidate_preparation is not None
                else {}
            ),
            "urban_spines": len(compiled.urban_spines),
            "urban_classification_unknowns": len(compiled.urban_classification_unknowns),
            "urban_spine_records": [
                {
                    "structure_id": row.structure_id,
                    "official_classification": row.official_classification,
                    "official_feature_id": row.official_feature_id,
                    "source_id": row.source_id,
                    "effective_date": row.effective_date,
                    "licence": row.licence,
                    "content_fingerprint": row.content_fingerprint,
                    "classification_status": row.classification_status,
                    "intervention_assumption": row.intervention_assumption,
                }
                for row in compiled.urban_spines.itertuples()
            ],
            "urban_classification_unknown_records": [
                {
                    "structure_id": row.structure_id,
                    "official_feature_id": row.official_feature_id,
                    "source_id": row.source_id,
                    "effective_date": row.effective_date,
                    "licence": row.licence,
                    "content_fingerprint": row.content_fingerprint,
                    "classification_status": row.classification_status,
                }
                for row in compiled.urban_classification_unknowns.itertuples()
            ],
            "candidate_low_traffic_areas": len(compiled.low_traffic_areas),
            "low_traffic_area_portals": len(compiled.low_traffic_area_portals),
            "candidate_low_traffic_area_records": [
                {
                    "structure_id": row.structure_id,
                    "name": row.name,
                    "status": row.status,
                    "intervention_need": row.intervention_need,
                    "boundary_ids": row.boundary_ids,
                    "observed_through_traffic_evidence_ids": (
                        row.observed_through_traffic_evidence_ids
                    ),
                    "observed_through_traffic_source_ids": (
                        row.observed_through_traffic_source_ids
                    ),
                    "portal_count": row.portal_count,
                }
                for row in compiled.low_traffic_areas.itertuples()
            ],
            "low_traffic_area_portal_records": [
                {
                    "portal_id": row.portal_id,
                    "area_id": row.area_id,
                    "name": row.name,
                    "boundary_id": row.boundary_id,
                    "boundary_name": row.boundary_name,
                    "boundary_kind": row.boundary_kind,
                }
                for row in compiled.low_traffic_area_portals.itertuples()
            ],
            "strategic_spines": len(compiled.strategic_spines),
            "access_obligations": len(compiled.access_obligations),
            "school_access_obligations": int(
                (compiled.access_obligations["obligation_kind"] == "school").sum()
            ),
            "school_street_assessments": len(compiled.school_street_assessments),
            "school_street_assessment_records": [
                {
                    "assessment_id": row.assessment_id,
                    "school_id": row.school_id,
                    "school_name": row.school_name,
                    "assessment_status": row.assessment_status,
                    "assessment_label": row.assessment_label,
                    "rationale": row.rationale,
                    "qualification": row.qualification,
                    "access_point_status": row.access_point_status,
                    "adjoining_road_classification": (row.adjoining_road_classification),
                    "bus_access": row.bus_access,
                    "essential_access": row.essential_access,
                    "alternative_through_route": row.alternative_through_route,
                    "displacement_risk": row.displacement_risk,
                    "missing_evidence": row.missing_evidence,
                    "evidence": row.evidence,
                    "source_ids": row.source_ids,
                }
                for row in compiled.school_street_assessments.itertuples()
            ],
            "topography_profiles": len(compiled.topography_profiles),
            "gradient_sections": len(compiled.gradient_sections),
            "topography_alternative_comparisons": [
                {
                    "connection_id": row[id_column],
                    "connection_type": connection_type,
                    "triggered": row["topography_alternative_trigger"],
                    "status": row["topography_comparison_status"],
                    "rationale": row["topography_comparison_rationale"],
                    "original_role": row["topography_original_role"],
                    "selected_role": row["topography_selected_role"],
                    "alignment_options": row["alignment_options"],
                }
                for frame, id_column, connection_type in (
                    (
                        compiled.spine_access_connections,
                        "access_connection_id",
                        "spine-access-connection",
                    ),
                    (
                        compiled.branch_meeting_connections,
                        "meeting_connection_id",
                        "branch-meeting-connection",
                    ),
                )
                for _, row in frame.iterrows()
            ],
            "elevation_corroboration_count": len(compiled.elevation_corroboration),
            "topography_profile_records": [
                {
                    "profile_id": row.profile_id,
                    "edge_id": row.edge_id,
                    "edge_type": row.edge_type,
                    "evidence_status": row.evidence_status,
                    "evidence_rationale": row.evidence_rationale,
                    "distance_m": row.distance_m,
                    "forward_ascent_m": row.forward_ascent_m,
                    "forward_descent_m": row.forward_descent_m,
                    "reverse_ascent_m": row.reverse_ascent_m,
                    "reverse_descent_m": row.reverse_descent_m,
                    "steepest_sustained_gradient_pct": (row.steepest_sustained_gradient_pct),
                    "steepest_sustained_gradient_rationale": (
                        row.steepest_sustained_gradient_rationale
                    ),
                    "gradient_section_ids": row.gradient_section_ids,
                    "elevation_evidence_ids": row.elevation_evidence_ids,
                    "elevation_source_ids": row.elevation_source_ids,
                }
                for row in compiled.topography_profiles.itertuples()
            ],
            "spine_access_connections": len(compiled.spine_access_connections),
            "spine_access_branches": len(compiled.spine_access_branches),
            "branch_meeting_connections": len(compiled.branch_meeting_connections),
            "cross_spine_connectors": len(compiled.cross_spine_connectors),
            "strategic_spine_records": [
                {
                    "spine_id": row.spine_id,
                    "evidence_id": row.evidence_id,
                    "source_id": row.source_id,
                    "provenance": row.provenance,
                }
                for row in compiled.strategic_spines.itertuples()
            ],
            "access_obligation_records": [
                {
                    "obligation_id": row.obligation_id,
                    "community_id": row.community_id,
                    "school_id": row.school_id,
                    "school_kind": row.school_kind,
                    "service_status": row.service_status,
                    "service_rationale": row.service_rationale,
                    "access_point_status": row.access_point_status,
                    "access_point_source_id": row.access_point_source_id,
                    "access_point_rationale": row.access_point_rationale,
                    "access_connection_id": row.access_connection_id,
                    "root_spine_id": row.root_spine_id,
                    "branch_id": row.branch_id,
                    "network_scope": row.network_scope,
                    "criterion_continuity": row.criterion_continuity,
                    "low_traffic_area_id": row.low_traffic_area_id,
                    "low_traffic_area_name": row.low_traffic_area_name,
                    "portal_id": row.portal_id,
                    "portal_name": row.portal_name,
                    "urban_spine_id": row.urban_spine_id,
                    "fabric_source_ids": row.fabric_source_ids,
                    "supporting_evidence": row.supporting_evidence,
                    "finding": row.finding,
                    "geometry_semantics": row.geometry_semantics,
                    "provenance": row.provenance,
                }
                for row in compiled.access_obligations.itertuples()
            ],
            "spine_access_connection_records": [
                {
                    "access_connection_id": row.access_connection_id,
                    "network_role": row.network_role,
                    "place_id": row.place_id,
                    "place_kind": row.place_kind,
                    "community_id": row.community_id,
                    "school_id": row.school_id,
                    "school_kind": row.school_kind,
                    "access_point_status": row.access_point_status,
                    "access_point_source_id": row.access_point_source_id,
                    "access_point_rationale": row.access_point_rationale,
                    "spine_id": row.spine_id,
                    "root_spine_id": row.root_spine_id,
                    "branch_id": row.branch_id,
                    "parent_branch_id": row.parent_branch_id,
                    "parent_role": row.parent_role,
                    "parent_target_id": row.parent_target_id,
                    "parent_target_name": row.parent_target_name,
                    "parent_place_id": row.parent_place_id,
                    "parent_access_connection_id": row.parent_access_connection_id,
                    "attachment_depth": row.attachment_depth,
                    "community_attachment_node": row.community_attachment_node,
                    "community_attachment_distance_m": row.community_attachment_distance_m,
                    "community_attachment_point": row.community_attachment_point,
                    "spine_attachment_node": row.spine_attachment_node,
                    "spine_attachment_distance_m": row.spine_attachment_distance_m,
                    "spine_attachment_point": row.spine_attachment_point,
                    "source_ids": row.source_ids,
                    "provenance": row.provenance,
                }
                for row in compiled.spine_access_connections.itertuples()
            ],
            "spine_access_branch_records": [
                {
                    "branch_id": row.branch_id,
                    "root_spine_id": row.root_spine_id,
                    "connection_ids": row.connection_ids,
                    "place_ids": row.place_ids,
                    "provenance": row.provenance,
                }
                for row in compiled.spine_access_branches.itertuples()
            ],
            "branch_meeting_connection_records": [
                {
                    "meeting_connection_id": row.meeting_connection_id,
                    "network_role": row.network_role,
                    "from_place_id": row.from_place_id,
                    "to_place_id": row.to_place_id,
                    "from_root_spine_id": row.from_root_spine_id,
                    "to_root_spine_id": row.to_root_spine_id,
                    "source_ids": row.source_ids,
                    "provenance": row.provenance,
                }
                for row in compiled.branch_meeting_connections.itertuples()
            ],
            "cross_spine_connector_records": [
                {
                    "cross_spine_connector_id": row.cross_spine_connector_id,
                    "meeting_connection_id": row.meeting_connection_id,
                    "branch_ids": row.branch_ids,
                    "connection_ids": row.connection_ids,
                    "source_ids": row.source_ids,
                    "provenance": row.provenance,
                }
                for row in compiled.cross_spine_connectors.itertuples()
            ],
            "superseded_hypotheses": compiled.superseded_hypotheses,
            "atm_mode": council.atm.mode if council.atm.enabled else "disabled",
            "atm_geometry_included": compiled.atm_reference is not None,
            "divergence_counts": dict(
                Counter(record.status for record in compiled.divergence_records)
            ),
        },
    )


def _decision_required_result(
    council: AreaConfig,
    input_fingerprint: str,
    request: AgentDecisionRequest,
    ledger: AgentDecisionLedger,
    agent_records: list[AgentRecord] | None = None,
    divergence_records: list[DivergenceRecord] | None = None,
    validation: str | None = None,
) -> CompilationResult:
    """Return a durable menu without publishing or retaining continuation state."""
    records = [*(agent_records or []), *(divergence_records or [])]
    return CompilationResult(
        run_id=f"decision-{request.dependency_fingerprint[:12]}",
        status="decision-required",
        output_dir=council.publication.output_dir,
        connections=0,
        gaps=0,
        artifacts={},
        criteria={},
        agent_records=agent_records or [],
        divergence_records=divergence_records or [],
        decision_requests=[request],
        metadata={
            "compilation_input_fingerprint": input_fingerprint,
            "decision_response_validation": validation,
            "runtime_governance": incomplete_runtime_governance(
                council.compilation.agent,
                records,
                decision_ledger_input=ledger.model_dump(mode="json"),
                validation=validation,
            ),
        },
    )


def _terminated_result(
    council: AreaConfig,
    input_fingerprint: str,
    terminated: AgentCompilationTerminated,
) -> CompilationResult:
    accepted = [
        *terminated.applied_records,
        *terminated.applied_divergence_records,
    ]
    fingerprint = hashlib.sha256(
        json.dumps(
            [record.model_dump(mode="json") for record in accepted],
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    return CompilationResult(
        run_id=f"terminated-{fingerprint[:12]}",
        status="terminated",
        output_dir=council.publication.output_dir,
        connections=0,
        gaps=0,
        artifacts={},
        criteria={},
        agent_records=terminated.applied_records,
        divergence_records=terminated.applied_divergence_records,
        metadata={
            "compilation_input_fingerprint": input_fingerprint,
            "decision_response_validation": "accepted",
        },
    )


def _reviewable_terminal_result(
    council: AreaConfig,
    input_fingerprint: str,
    reviewable: ReviewableNetwork,
) -> CompilationResult:
    """Stop before publication when governed reviewable input is terminal."""

    return CompilationResult(
        run_id=f"terminated-reviewable-{reviewable.result_fingerprint[:12]}",
        status="terminated",
        output_dir=council.publication.output_dir,
        connections=0,
        gaps=0,
        artifacts={},
        criteria={},
        agent_records=[],
        metadata={
            "compilation_input_fingerprint": input_fingerprint,
            "reviewable_network": reviewable.metadata,
            "publication_action": "retain-previous-valid-publication",
        },
    )


def _load_decision_ledger(
    value: AgentDecisionLedger | str | Path | None,
) -> AgentDecisionLedger:
    if value is None:
        return AgentDecisionLedger()
    if isinstance(value, AgentDecisionLedger):
        return value
    try:
        payload = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("decision ledger file is not valid JSON") from error
    return canonical_decision_ledger_payload(payload)


def decision_ledger_input_fingerprint(
    governed_input_fingerprint: str,
    ledger: AgentDecisionLedger,
    *,
    officer_decisions: tuple[PreloadedOfficerDecision, ...] = (),
) -> str:
    payload: dict[str, object] = {
        "governed_input_fingerprint": governed_input_fingerprint,
        "decision_ledger": ledger.model_dump(mode="json"),
    }
    if officer_decisions:
        payload["officer_decisions"] = [
            item.model_dump(mode="json") for item in officer_decisions
        ]
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def compilation_governed_input_fingerprint(
    council: AreaConfig,
    *,
    dependency_manifest: dict[str, object] | None = None,
    evidence_state_fingerprint: str | None = None,
    traffic_match_policy_fingerprint: str | None = None,
) -> str:
    """Fingerprint every governed input required for safe whole-publication reuse."""
    config_payload = _canonical_configuration_payload(council)
    config_payload["compilation"].pop("full", None)
    # The superseded comparison is explanatory, never a correctness input. Its path is
    # governed by configuration, but promoting this run to that path must not invalidate
    # reuse of the authoritative network it just produced.
    network_selection_paths = _network_selection_governed_paths(council)
    governed_paths = [
        council.atm.path,
        (
            council.source.official_road_classification.path
            if council.source.official_road_classification is not None
            else None
        ),
        (
            council.source.observed_through_traffic.path
            if council.source.observed_through_traffic is not None
            else None
        ),
        (
            council.source.national_elevation.path
            if council.source.national_elevation is not None
            else None
        ),
        *network_selection_paths,
    ]
    missing_paths = sorted(
        str(path)
        for path in network_selection_paths
        if not path.is_file()
    )
    if missing_paths:
        raise ValueError(
            "configured governed input file is missing: " + ", ".join(missing_paths)
        )
    manifest = dependency_manifest or compilation_dependency_manifest(council)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "configuration": config_payload,
        "snapshot_manifest_sha256": snapshot_manifest_sha256(council),
        "governed_file_sha256": {
            _canonical_path_identity(path, council.config_path.parent): _file_digest(path)
            for path in governed_paths
            if path is not None and path.is_file()
        },
        "compiler_dependency_manifest": manifest,
        # Retain the compact field for release contracts and benchmark evidence.
        "compiler_sha256": manifest["sha256"],
        "dft_traffic_evidence_state_fingerprint": evidence_state_fingerprint,
        "dft_traffic_match_policy_fingerprint": traffic_match_policy_fingerprint,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _canonical_configuration_payload(council: AreaConfig) -> dict[str, object]:
    """Serialize configured paths by their identity relative to the Area Definition."""
    python_payload = council.model_dump(mode="python")
    json_payload = council.model_dump(mode="json")
    canonical = _canonical_configuration_value(
        python_payload,
        json_payload,
        council.config_path.parent,
    )
    if not isinstance(canonical, dict):  # Defensive: AreaConfig always serializes a mapping.
        raise TypeError("Area Definition configuration payload must be a mapping")
    return canonical


def _canonical_configuration_value(
    value: object,
    serialized: object,
    base_directory: Path,
) -> object:
    if isinstance(value, Path):
        return _canonical_path_identity(value, base_directory)
    if isinstance(value, dict) and isinstance(serialized, dict):
        return {
            key: _canonical_configuration_value(
                item,
                serialized[key],
                base_directory,
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)) and isinstance(serialized, list):
        return [
            _canonical_configuration_value(item, serialized[index], base_directory)
            for index, item in enumerate(value)
        ]
    return serialized


def _canonical_path_identity(path: Path, base_directory: Path) -> str:
    """Return one POSIX path identity independent of the checkout's absolute root."""
    relative = Path(os.path.relpath(path.resolve(), base_directory.resolve()))
    return relative.as_posix()


def _network_selection_governed_paths(council: AreaConfig) -> tuple[Path, ...]:
    """Return governed alignment-evidence paths when the optional pass is enabled."""
    if council.compilation.network_selection is None:
        return ()
    paths: list[Path] = []
    population = council.source.population_reach_evidence
    if population is not None:
        paths.extend(
            [
                population.output_area_geometry.path,
                population.population_weighted_centroids.path,
                population.usual_resident_counts.path,
            ]
        )
    school_register = council.source.school_register_evidence
    if school_register is not None:
        paths.append(school_register.school_register.path)
    admissions = council.source.strategic_education_destination_admissions
    if admissions is not None:
        paths.append(admissions.admissions.path)
    return tuple(paths)


def snapshot_manifest_sha256(council: AreaConfig) -> str:
    """Return the immutable digest for the snapshot consumed by a compilation."""
    return _file_digest(council.source.snapshot_dir / council.source.snapshot_id / "snapshot.json")


def area_definition_sha256(council: AreaConfig) -> str:
    """Return the exact bytes digest of the Area Definition, never a re-serialisation."""
    return _file_digest(council.config_path)


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compiler_digest() -> str:
    """Return the explicit compilation dependency-set digest."""
    digest = compilation_dependency_manifest()["sha256"]
    if not isinstance(digest, str):  # Defensive: this is a governed reuse boundary.
        raise ValueError("compilation dependency manifest has no SHA-256 digest")
    return digest


def _review_map_assets_are_current(output: Path) -> bool:
    """Return whether a reusable publication carries this installed UI shell."""
    source_assets = compilation_dependencies._package_root() / "assets"
    published_review = output / "review-map"
    published_assets = published_review / "assets"
    try:
        html = (published_review / "index.html").read_text(encoding="utf-8")
        for name in (
            "maplibre-gl.js",
            "maplibre-gl.css",
            "MAPLIBRE-LICENSE.txt",
            "review-map.js",
            "review-map.css",
        ):
            content = (source_assets / name).read_bytes()
            if (published_assets / name).read_bytes() != content:
                return False
            if name.startswith("review-map."):
                path = Path(name)
                digest = hashlib.sha256(content).hexdigest()[:12]
                fingerprinted = f"{path.stem}.{digest}{path.suffix}"
                if (published_assets / fingerprinted).read_bytes() != content:
                    return False
                if f"assets/{fingerprinted}" not in html:
                    return False
    except OSError:
        return False
    return True


def _reuse_validated_publication(
    council: AreaConfig,
    governed_input_fingerprint: str,
    input_fingerprint: str,
    dependency_manifest: dict[str, object],
    *,
    officer_decisions: tuple[PreloadedOfficerDecision, ...] = (),
    publication_authority: PublicationDestinationAuthority | None = None,
    force_presentation_republish: bool = False,
) -> CompilationResult | None:
    if council.compilation.full:
        LOGGER.info("Validated publication reuse disabled by --full")
        return None
    output = council.publication.output_dir
    run_path = output / "run.json"
    if not run_path.exists():
        return None
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
        # Reuse is a trust boundary: a legacy/stripped or reordered record must
        # be recompiled rather than silently normalised by Pydantic.
        input_ledger = canonical_decision_ledger_payload(run["decision_ledger_input"])
        accepted_ledger = canonical_decision_ledger_payload(
            {
                "decision_contract": run["decision_contract"],
                "responses": run["accepted_decisions"],
            }
        )
        if input_ledger.decision_contract != run["decision_contract"] or (
            accepted_ledger.model_dump(mode="json")["responses"]
            != run["accepted_decisions"]
        ):
            return None
        # The stored wire input is a separate trust boundary.  Recompute its
        # fingerprint before comparing it to the current caller's ledger, so a
        # canonical-looking but altered persisted input cannot borrow the old
        # run fingerprint and be reused.
        persisted_payload = run.get("officer_decision_input", [])
        if not isinstance(persisted_payload, list):
            return None
        persisted_officer_decisions = canonical_officer_decisions(
            tuple(
                PreloadedOfficerDecision.model_validate(item)
                for item in persisted_payload
            )
        )
        persisted_input_fingerprint = decision_ledger_input_fingerprint(
            governed_input_fingerprint,
            input_ledger,
            officer_decisions=persisted_officer_decisions,
        )
        if run.get("governed_input_fingerprint") != governed_input_fingerprint:
            LOGGER.info("Existing publication governed inputs differ; recompiling")
            return None
        if run.get("compilation_dependency_manifest") != dependency_manifest:
            LOGGER.info("Existing publication compilation dependencies differ; recompiling")
            return None
        if run.get("compilation_input_fingerprint") != persisted_input_fingerprint:
            LOGGER.info("Existing publication persisted decision input differs; recompiling")
            return None
        if persisted_input_fingerprint != input_fingerprint:
            LOGGER.info("Existing publication input fingerprint differs; recompiling")
            return None
        validate_publication(output, council)
        presentation_republished = False
        presentation_elapsed: float | None = None
        try:
            presentation_input, presentation_manifest = validate_presentation_retention(run)
        except ValueError as error:
            LOGGER.info(
                "Existing publication presentation retention is invalid; "
                "recompiling reason=%s",
                error,
            )
            return None
        strategic = presentation_input["strategic_enabled"]
        current_presentation_manifest = presentation_dependency_manifest(strategic=strategic)
        if (
            force_presentation_republish
            or presentation_manifest != current_presentation_manifest
            or run.get("presentation_dependency_fingerprint")
            != current_presentation_manifest.get("sha256")
            or not _review_map_assets_are_current(output)
        ):
            LOGGER.info("Existing publication presentation dependencies differ; republishing")
            presentation_started = time.perf_counter()
            try:
                republish_presentation(
                    council,
                    publication_dependency_manifest=current_presentation_manifest,
                    publication_authority=publication_authority,
                )
            except Exception as error:
                # The republisher stages beside the live directory.  Surface a
                # failed candidate rather than falling through to a fresh
                # semantic compile that could replace the valid old publication.
                raise RuntimeError(
                    "presentation-only publication failed; previous publication retained"
                ) from error
            presentation_republished = True
            presentation_elapsed = max(0.0, time.perf_counter() - presentation_started)
            run = json.loads(run_path.read_text(encoding="utf-8"))
        agents_payload = json.loads((output / "agent-records.json").read_text(encoding="utf-8"))
        divergences_payload = json.loads(
            (output / "divergence-records.json").read_text(encoding="utf-8")
        )
        criteria = {
            section: {criterion: TrafficLight(status) for criterion, status in values.items()}
            for section, values in run["criteria"].items()
        }
        LOGGER.info(
            "Validated publication reused run_id=%s output=%s",
            run["run_id"],
            output,
        )
        metadata = dict(run)
        # These are invocation-scoped result flags, never durable publication
        # state.  Strip markers from older publications before ordinary reuse.
        metadata.pop("presentation_republished", None)
        metadata.pop("presentation_elapsed_seconds", None)
        if presentation_republished:
            metadata.update(
                {
                    "semantic_compilation_reused": True,
                    "presentation_republished": True,
                    "presentation_elapsed_seconds": presentation_elapsed,
                }
            )
        return CompilationResult(
            run_id=run["run_id"],
            status=run["status"],
            output_dir=output,
            connections=run["connection_count"],
            gaps=run["gap_count"],
            artifacts=publication_artifacts(output),
            criteria=criteria,
            agent_records=[
                AgentRecord.model_validate(record) for record in agents_payload["records"]
            ],
            divergence_records=[
                DivergenceRecord.model_validate(record) for record in divergences_payload["records"]
            ],
            metadata=(metadata | {"publication_reused": True})
            if not presentation_republished
            else metadata,
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
        LOGGER.warning(
            "Existing publication failed reuse validation; recompiling reason=%s",
            error,
        )
        return None
