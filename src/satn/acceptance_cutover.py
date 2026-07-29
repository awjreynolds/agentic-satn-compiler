"""Versioned acceptance evidence and fail-closed coordinator cutover.

The benchmark manifests are evidence records, not switches.  This module
recomputes every gate from their bound measurements and semantic fingerprints
before allowing the Local Evidence Store coordinator to be selected.  The
snapshot-backed coordinator remains available as the independent oracle.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BENCHMARK_MANIFEST_SCHEMA = "satn.acceptance-benchmark/v1"
REQUIRED_CUTOVER_GATES = frozenset(
    {
        "offline-provisioning",
        "spatial-subset",
        "banes-cold",
        "a4017-equivalence",
        "scenario-iteration",
        "weca-cold",
        "publication-validation",
    }
)
REUSED_SCENARIO_STAGES = frozenset(range(1, 7))


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MachineBinding(_ClosedModel):
    """The reference machine conditions for one measured run."""

    machine_id: str = Field(min_length=1)
    operating_system: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    power_mode: str = Field(min_length=1)
    other_material_workloads: bool


class RuntimeBinding(_ClosedModel):
    """Runtime versions that can materially affect a benchmark."""

    python: str = Field(min_length=1)
    duckdb: str = Field(min_length=1)
    spatial_extension: str = Field(min_length=1)


class InputBinding(_ClosedModel):
    """Checksums for every governed input used by a benchmark."""

    area_definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_export_sha256: Mapping[str, str] = Field(min_length=1)
    snapshot_sha256: Mapping[str, str] = Field(min_length=1)


class RunConditions(_ClosedModel):
    """The declared cache/process state for a measurement."""

    mode: Literal["cold", "changed-configuration", "fresh-process", "same-process"]
    process_reopened: bool
    os_page_cache_controlled: bool


class RunOutcome(_ClosedModel):
    """Completion and publication observations; no author-written pass bit exists."""

    completed: bool
    exit_code: int
    atomic_publication: bool
    publication_validated: bool


class Measurements(_ClosedModel):
    """Raw observations from which acceptance is recomputed."""

    wall_seconds: float = Field(ge=0)
    peak_rss_mib: float = Field(gt=0)
    query_samples_seconds: tuple[float, ...] = ()
    stage_seconds: Mapping[str, float] = Field(default_factory=dict)
    reused_stages: tuple[int, ...] = ()


class SemanticBinding(_ClosedModel):
    """Observed semantic hashes and their independent oracle values."""

    observed: Mapping[str, str] = Field(min_length=1)
    oracle: Mapping[str, str] = Field(min_length=1)


class BenchmarkManifestV1(_ClosedModel):
    """One immutable, versioned benchmark run described by ADR-0016."""

    schema_version: Literal["satn.acceptance-benchmark/v1"]
    benchmark_id: str = Field(min_length=1)
    gate: Literal[
        "offline-provisioning",
        "spatial-subset",
        "banes-cold",
        "a4017-equivalence",
        "scenario-iteration",
        "weca-cold",
        "publication-validation",
        "identical-input-reuse",
    ]
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    captured_at: str = Field(min_length=1)
    machine: MachineBinding
    runtime: RuntimeBinding
    inputs: InputBinding
    store_state_fingerprint: str = Field(min_length=1)
    scenario_fingerprint: str | None = None
    decision_fingerprint: str | None = None
    command: tuple[str, ...] = Field(min_length=1)
    conditions: RunConditions
    outcome: RunOutcome
    measurements: Measurements
    result_counts: Mapping[str, int] = Field(default_factory=dict)
    semantics: SemanticBinding


class CoordinatorPath(StrEnum):
    LEGACY_SNAPSHOT = "legacy-snapshot"
    LOCAL_EVIDENCE_STORE = "local-evidence-store"


class CutoverRequest(StrEnum):
    AUTOMATIC = "automatic"
    LEGACY_SNAPSHOT = "legacy-snapshot"
    LOCAL_EVIDENCE_STORE = "local-evidence-store"


class CutoverReport(_ClosedModel):
    """Recomputed result for a complete set of benchmark manifests."""

    accepted: bool
    reasons: tuple[str, ...]
    evaluated_gates: tuple[str, ...]
    identical_input_reuse_accepted: bool


class CoordinatorSelection(_ClosedModel):
    """The selected coordinator path and the evidence behind the selection."""

    path: CoordinatorPath
    report: CutoverReport


class CutoverBlocked(RuntimeError):
    """An explicit store-backed selection was not supported by exact evidence."""

    def __init__(self, report: CutoverReport) -> None:
        self.report = report
        super().__init__("Local Evidence Store cutover blocked: " + "; ".join(report.reasons))


def load_benchmark_manifest(path: Path) -> BenchmarkManifestV1:
    """Load one closed v1 manifest; unknown versions and fields are rejected."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid benchmark manifest JSON: {path}") from error
    return BenchmarkManifestV1.model_validate(payload)


def evaluate_cutover(
    manifests: Iterable[BenchmarkManifestV1],
    *,
    expected_commit: str,
    expected_input_fingerprints: Mapping[str, str],
) -> CutoverReport:
    """Recompute ADR-0016 gates without trusting a stored pass/fail assertion."""

    records = tuple(manifests)
    reasons: list[str] = []
    counts = Counter(record.gate for record in records)
    for gate in sorted(REQUIRED_CUTOVER_GATES):
        count = counts[gate]
        if count == 0:
            reasons.append(f"missing required gate: {gate}")
        elif count > 1:
            reasons.append(f"ambiguous duplicate gate: {gate}")

    for record in records:
        prefix = record.gate
        if record.commit_sha != expected_commit:
            reasons.append(f"{prefix}: commit does not match cutover candidate")
        actual_inputs = _flatten_input_fingerprints(record.inputs)
        for name, expected in sorted(expected_input_fingerprints.items()):
            if actual_inputs.get(name) != expected:
                reasons.append(f"{prefix}: input fingerprint mismatch for {name}")
        reasons.extend(_gate_failures(record))

    reuse_records = [record for record in records if record.gate == "identical-input-reuse"]
    reuse_accepted = len(reuse_records) == 1 and not _common_failures(reuse_records[0])
    return CutoverReport(
        accepted=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        evaluated_gates=tuple(sorted(counts)),
        identical_input_reuse_accepted=reuse_accepted,
    )


def select_coordinator(
    manifests: Iterable[BenchmarkManifestV1],
    *,
    expected_commit: str,
    expected_input_fingerprints: Mapping[str, str],
    request: CutoverRequest = CutoverRequest.AUTOMATIC,
) -> CoordinatorSelection:
    """Select the store coordinator only from a complete, exact gate set.

    Automatic selection falls back to the retained snapshot oracle.  An explicit
    Local Evidence Store request raises instead of silently weakening the gate.
    """

    report = evaluate_cutover(
        manifests,
        expected_commit=expected_commit,
        expected_input_fingerprints=expected_input_fingerprints,
    )
    if request is CutoverRequest.LEGACY_SNAPSHOT:
        return CoordinatorSelection(path=CoordinatorPath.LEGACY_SNAPSHOT, report=report)
    if report.accepted:
        return CoordinatorSelection(path=CoordinatorPath.LOCAL_EVIDENCE_STORE, report=report)
    if request is CutoverRequest.LOCAL_EVIDENCE_STORE:
        raise CutoverBlocked(report)
    return CoordinatorSelection(path=CoordinatorPath.LEGACY_SNAPSHOT, report=report)


def _flatten_input_fingerprints(inputs: InputBinding) -> dict[str, str]:
    values = {"area_definition": inputs.area_definition_sha256}
    values.update(
        {
            f"source_export:{key}": value
            for key, value in inputs.source_export_sha256.items()
        }
    )
    values.update({f"snapshot:{key}": value for key, value in inputs.snapshot_sha256.items()})
    return values


def _common_failures(record: BenchmarkManifestV1) -> list[str]:
    failures: list[str] = []
    if not record.outcome.completed:
        failures.append(f"{record.gate}: run did not complete")
    if record.outcome.exit_code != 0:
        failures.append(f"{record.gate}: exit status was {record.outcome.exit_code}")
    if record.semantics.observed != record.semantics.oracle:
        failures.append(f"{record.gate}: semantic fingerprints do not match the oracle")
    return failures


def _gate_failures(record: BenchmarkManifestV1) -> list[str]:
    failures = _common_failures(record)
    gate = record.gate
    if gate == "spatial-subset":
        samples = record.measurements.query_samples_seconds
        if not samples:
            failures.append("spatial-subset: no query samples")
        elif max(samples) > 2:
            failures.append("spatial-subset: worst query exceeded 2 seconds")
    elif gate == "banes-cold":
        failures.extend(_publication_failures(record))
        if record.conditions.mode != "cold":
            failures.append("banes-cold: run was not cold")
        if record.measurements.wall_seconds > 120:
            failures.append("banes-cold: wall time exceeded 120 seconds")
    elif gate == "scenario-iteration":
        failures.extend(_publication_failures(record))
        if record.conditions.mode != "changed-configuration":
            failures.append("scenario-iteration: run was not changed-configuration")
        if record.measurements.wall_seconds > 60:
            failures.append("scenario-iteration: wall time exceeded 60 seconds")
        if set(record.measurements.reused_stages) != REUSED_SCENARIO_STAGES:
            failures.append("scenario-iteration: stages 1-6 were not exactly reused")
        if record.scenario_fingerprint is None or record.decision_fingerprint is None:
            failures.append("scenario-iteration: scenario/decision fingerprints are absent")
    elif gate == "weca-cold":
        failures.extend(_publication_failures(record))
        if record.conditions.mode != "cold":
            failures.append("weca-cold: run was not cold")
        if record.measurements.wall_seconds > 600:
            failures.append("weca-cold: wall time exceeded 600 seconds")
        if not record.measurements.stage_seconds:
            failures.append("weca-cold: stage timings are absent")
    elif gate == "publication-validation":
        failures.extend(_publication_failures(record))
    return failures


def _publication_failures(record: BenchmarkManifestV1) -> list[str]:
    failures: list[str] = []
    if not record.outcome.atomic_publication:
        failures.append(f"{record.gate}: publication was not atomic")
    if not record.outcome.publication_validated:
        failures.append(f"{record.gate}: publication validation did not pass")
    return failures
