from __future__ import annotations

import json

import pytest
from bath_saltford_fixture import configured_bath_saltford

from satn.filesystem_safety import publication_destination_authority
from satn.parallel_reduction import PreloadedOfficerDecision
from satn.pipeline import compile
from satn.psa_evidence_loaders import GovernedEvidenceLoadError
from satn.publisher import validate_publication
from satn.sources import snapshot


def test_public_compile_publishes_reviewable_network_artifact(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)

    result = compile(
        config,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )

    artifact = result.artifacts["reviewable_network"]
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["contract"] == "satn-reviewable-network/v1"
    assert payload["result_fingerprint"]
    assert result.metadata["reviewable_network"]["contract"] == (
        "satn-reviewable-network/v1"
    )


def test_publication_validation_rejects_tampered_reviewable_metadata(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    authority = publication_destination_authority(workspace_root=tmp_path)
    result = compile(config, publication_authority=authority)
    artifact = result.artifacts["reviewable_network"]
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["metadata"]["network_gap_ids"] = ["invented-gap"]
    artifact.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="metadata is not derived"):
        validate_publication(result.output_dir, config)


def test_publication_validation_rejects_tampered_run_reviewable_metadata(
    tmp_path,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    result = compile(
        config,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )
    run_path = result.output_dir / "run.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["reviewable_network"]["network_gap_ids"] = ["invented-gap"]
    run_path.write_text(json.dumps(run, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="run manifest reviewable-network"):
        validate_publication(result.output_dir, config)


def test_public_compile_records_unavailable_officer_decision_as_governed_input(
    tmp_path,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    authority = publication_destination_authority(workspace_root=tmp_path)
    baseline = compile(config, publication_authority=authority)
    decision = PreloadedOfficerDecision(
        target_id="connection-not-in-current-compilation",
        route_id="route-not-in-current-compilation",
    )

    decided = compile(
        config,
        officer_decisions=(decision,),
        publication_authority=authority,
    )

    assert decided.metadata["compilation_input_fingerprint"] != (
        baseline.metadata["compilation_input_fingerprint"]
    )
    artifact = json.loads(
        decided.artifacts["reviewable_network"].read_text(encoding="utf-8")
    )
    assert artifact["semantic"]["officer_decisions"][0] == {
        "decision_id": artifact["semantic"]["target_unavailable"][0]["decision_id"],
        "target_id": decision.target_id,
        "route_id": decision.route_id,
        "status": "target-unavailable",
        "candidate_set_id": None,
        "candidate_id": None,
    }
    run = json.loads((decided.output_dir / "run.json").read_text(encoding="utf-8"))
    assert run["officer_decision_input"] == [
        {"target_id": decision.target_id, "route_id": decision.route_id}
    ]


def test_public_compile_returns_terminal_result_for_governed_assembly_failure(
    tmp_path,
    monkeypatch,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)

    def fail(*args, **kwargs):
        raise ValueError("candidate preparation fingerprint is stale")

    monkeypatch.setattr("satn.compiler.assemble_prepared_candidate_criteria", fail)

    result = compile(
        config,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )

    assert result.status == "terminated"
    assert result.artifacts == {}
    assert result.metadata["reviewable_network"]["failure_code"] == (
        "mandatory-lineage-invalid"
    )
    assert result.metadata["publication_action"] == (
        "retain-previous-valid-publication"
    )


def test_public_compile_returns_terminal_result_for_governed_evidence_failure(
    tmp_path,
    monkeypatch,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)

    def fail(*args, **kwargs):
        raise GovernedEvidenceLoadError("population source content fingerprint mismatch")

    monkeypatch.setattr("satn.compiler.load_population_reach_evidence", fail)

    result = compile(
        config,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )

    assert result.status == "terminated"
    assert result.artifacts == {}
    assert result.metadata["reviewable_network"]["failure_code"] == (
        "mandatory-evidence-invalid"
    )
