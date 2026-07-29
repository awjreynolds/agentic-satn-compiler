from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from satn.acceptance_cutover import (
    BENCHMARK_MANIFEST_SCHEMA,
    REQUIRED_CUTOVER_GATES,
    BenchmarkManifestV1,
    CoordinatorPath,
    CutoverBlocked,
    CutoverRequest,
    evaluate_cutover,
    select_coordinator,
)

COMMIT = "a" * 40
INPUT_HASH = "b" * 64
ORACLE_HASH = "c" * 64
INPUTS = {
    "area_definition": INPUT_HASH,
    "source_export:open-roads": INPUT_HASH,
    "snapshot:network": INPUT_HASH,
}


def _manifest(gate: str) -> BenchmarkManifestV1:
    payload = {
        "schema_version": BENCHMARK_MANIFEST_SCHEMA,
        "benchmark_id": f"fixture-{gate}",
        "gate": gate,
        "commit_sha": COMMIT,
        "captured_at": "2026-07-29T12:00:00Z",
        "machine": {
            "machine_id": "reference-mac",
            "operating_system": "macOS 15",
            "architecture": "arm64",
            "power_mode": "automatic",
            "other_material_workloads": False,
        },
        "runtime": {
            "python": "3.12.11",
            "duckdb": "1.4.4",
            "spatial_extension": "1.4.4/osx_arm64",
        },
        "inputs": {
            "area_definition_sha256": INPUT_HASH,
            "source_export_sha256": {"open-roads": INPUT_HASH},
            "snapshot_sha256": {"network": INPUT_HASH},
        },
        "store_state_fingerprint": "fixture-store-v1",
        "scenario_fingerprint": "scenario-v2" if gate == "scenario-iteration" else None,
        "decision_fingerprint": "decision-v2" if gate == "scenario-iteration" else None,
        "command": ["satn", "acceptance", gate],
        "conditions": {
            "mode": "changed-configuration" if gate == "scenario-iteration" else "cold",
            "process_reopened": True,
            "os_page_cache_controlled": False,
        },
        "outcome": {
            "completed": True,
            "exit_code": 0,
            "atomic_publication": gate
            in {"banes-cold", "scenario-iteration", "weca-cold", "publication-validation"},
            "publication_validated": gate
            in {"banes-cold", "scenario-iteration", "weca-cold", "publication-validation"},
        },
        "measurements": {
            "wall_seconds": {
                "spatial-subset": 1.8,
                "banes-cold": 119,
                "scenario-iteration": 59,
                "weca-cold": 599,
            }.get(gate, 1),
            "peak_rss_mib": 100,
            "query_samples_seconds": [1.2, 1.8] if gate == "spatial-subset" else [],
            "stage_seconds": {"compile": 500} if gate == "weca-cold" else {},
            "reused_stages": [1, 2, 3, 4, 5, 6] if gate == "scenario-iteration" else [],
        },
        "result_counts": {"features": 1},
        "semantics": {
            "observed": {"network": ORACLE_HASH},
            "oracle": {"network": ORACLE_HASH},
        },
    }
    return BenchmarkManifestV1.model_validate(payload)


def _complete_manifests() -> tuple[BenchmarkManifestV1, ...]:
    return tuple(_manifest(gate) for gate in sorted(REQUIRED_CUTOVER_GATES))


def _changed(
    manifest: BenchmarkManifestV1,
    section: str,
    key: str,
    value: object,
) -> BenchmarkManifestV1:
    payload = deepcopy(manifest.model_dump(mode="python"))
    payload[section][key] = value
    return BenchmarkManifestV1.model_validate(payload)


def test_complete_exact_manifest_set_selects_store_backed_coordinator() -> None:
    selection = select_coordinator(
        _complete_manifests(),
        expected_commit=COMMIT,
        expected_input_fingerprints=INPUTS,
    )

    assert selection.path is CoordinatorPath.LOCAL_EVIDENCE_STORE
    assert selection.report.accepted is True


def test_missing_or_failed_gate_keeps_legacy_snapshot_oracle() -> None:
    manifests = [
        manifest for manifest in _complete_manifests() if manifest.gate != "weca-cold"
    ]
    banes = next(manifest for manifest in manifests if manifest.gate == "banes-cold")
    manifests[manifests.index(banes)] = _changed(
        banes,
        "semantics",
        "observed",
        {"network": "different"},
    )

    selection = select_coordinator(
        manifests,
        expected_commit=COMMIT,
        expected_input_fingerprints=INPUTS,
    )

    assert selection.path is CoordinatorPath.LEGACY_SNAPSHOT
    assert "missing required gate: weca-cold" in selection.report.reasons
    assert "banes-cold: semantic fingerprints do not match the oracle" in selection.report.reasons


def test_explicit_store_request_fails_instead_of_falling_back() -> None:
    manifests = [
        manifest
        for manifest in _complete_manifests()
        if manifest.gate != "offline-provisioning"
    ]

    with pytest.raises(CutoverBlocked, match="missing required gate: offline-provisioning"):
        select_coordinator(
            manifests,
            expected_commit=COMMIT,
            expected_input_fingerprints=INPUTS,
            request=CutoverRequest.LOCAL_EVIDENCE_STORE,
        )


def test_cutover_recomputes_budgets_reuse_publication_and_bindings() -> None:
    manifests = list(_complete_manifests())
    scenario = next(item for item in manifests if item.gate == "scenario-iteration")
    scenario = _changed(scenario, "measurements", "wall_seconds", 60.01)
    scenario = _changed(scenario, "measurements", "reused_stages", [1, 2, 3, 4, 5])
    scenario_index = next(
        index for index, item in enumerate(manifests) if item.gate == "scenario-iteration"
    )
    manifests[scenario_index] = scenario
    weca = next(item for item in manifests if item.gate == "weca-cold")
    manifests[manifests.index(weca)] = _changed(
        weca,
        "outcome",
        "atomic_publication",
        False,
    )

    report = evaluate_cutover(
        manifests,
        expected_commit="d" * 40,
        expected_input_fingerprints=INPUTS | {"snapshot:foreign": INPUT_HASH},
    )

    assert report.accepted is False
    assert "scenario-iteration: wall time exceeded 60 seconds" in report.reasons
    assert "scenario-iteration: stages 1-6 were not exactly reused" in report.reasons
    assert "weca-cold: publication was not atomic" in report.reasons
    assert "offline-provisioning: commit does not match cutover candidate" in report.reasons
    assert "offline-provisioning: input fingerprint mismatch for snapshot:foreign" in report.reasons


def test_manifest_contract_is_versioned_and_rejects_author_pass_bits() -> None:
    payload = _manifest("offline-provisioning").model_dump(mode="python")
    payload["passed"] = True

    with pytest.raises(ValidationError, match="passed"):
        BenchmarkManifestV1.model_validate(payload)

    payload.pop("passed")
    payload["schema_version"] = "satn.acceptance-benchmark/v2"
    with pytest.raises(ValidationError, match="schema_version"):
        BenchmarkManifestV1.model_validate(payload)


def test_identical_input_reuse_is_reported_but_not_a_cutover_substitute() -> None:
    reuse = _manifest("identical-input-reuse")
    report = evaluate_cutover(
        [reuse],
        expected_commit=COMMIT,
        expected_input_fingerprints=INPUTS,
    )

    assert report.accepted is False
    assert report.identical_input_reuse_accepted is True
    assert "missing required gate: scenario-iteration" in report.reasons
