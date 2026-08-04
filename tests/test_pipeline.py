from __future__ import annotations

import json

import geopandas as gpd
import pytest
from bath_saltford_fixture import configured_bath_saltford
from pypdf import PdfReader

from satn.filesystem_safety import publication_destination_authority
from satn.local_evidence_store import LocalEvidenceStore
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
    assert result.metadata["reviewable_network"]["contract"] == ("satn-reviewable-network/v1")


def test_public_compile_carries_one_effective_strategic_fingerprint(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)

    result = compile(
        config,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )

    run = json.loads(result.artifacts["run"].read_text(encoding="utf-8"))
    fingerprint = run["strategic_result_fingerprint"]
    assert len(fingerprint) == 64
    sidecar = json.loads(result.artifacts["strategic_network"].read_text(encoding="utf-8"))
    assert sidecar["strategic_result_fingerprint"] == fingerprint
    network = json.loads(result.artifacts["geojson"].read_text(encoding="utf-8"))
    assert network["strategic_result_fingerprint"] == fingerprint
    metadata = gpd.read_file(result.artifacts["geopackage"], layer="metadata")
    assert set(metadata["strategic_result_fingerprint"]) == {fingerprint}
    data_text = (result.output_dir / "review-map" / "data.js").read_text(encoding="utf-8")
    data = json.loads(data_text.removeprefix("window.SATN_DATA = ").rstrip(";\n"))
    assert data["strategic_result_fingerprint"] == fingerprint
    reviewable = json.loads(
        result.artifacts["reviewable_network_geojson"].read_text(encoding="utf-8")
    )
    assert reviewable["strategic_result_fingerprint"] == fingerprint
    assert all(
        feature["properties"].get("selection_disposition") != "selected-strategic-spine"
        for feature in reviewable["features"]
    )
    pdf = PdfReader(str(result.artifacts["pdf"]))
    assert fingerprint in "".join(page.extract_text() or "" for page in pdf.pages)


def test_publication_validation_rejects_missing_strategic_sidecar(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    result = compile(
        config,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )
    result.artifacts["strategic_network"].unlink()

    with pytest.raises(ValueError, match=r"sidecar|ZIP differs"):
        validate_publication(result.output_dir, config)


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

    assert (
        decided.metadata["compilation_input_fingerprint"]
        != (baseline.metadata["compilation_input_fingerprint"])
    )
    artifact = json.loads(decided.artifacts["reviewable_network"].read_text(encoding="utf-8"))
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
    assert result.metadata["reviewable_network"]["failure_code"] == ("mandatory-lineage-invalid")
    assert result.metadata["publication_action"] == ("retain-previous-valid-publication")


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
    assert result.metadata["reviewable_network"]["failure_code"] == ("mandatory-evidence-invalid")


def test_public_compile_requires_typed_paired_dft_evidence_opt_in(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)

    with pytest.raises(ValueError, match="supplied together"):
        compile(config, evidence_state="a" * 64)
    with pytest.raises(TypeError, match="LocalEvidenceStore"):
        compile(config, evidence_store=object(), evidence_state="a" * 64)


def test_public_compile_returns_typed_terminal_result_for_dft_store_failure(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    authority = publication_destination_authority(workspace_root=tmp_path)
    baseline = compile(config, publication_authority=authority)

    class BrokenStore(LocalEvidenceStore):
        def resolve_coverage(self, *, state_fingerprint: str, verify: bool = True):
            raise ValueError("coverage registry is corrupt")

    store = object.__new__(BrokenStore)
    decision = PreloadedOfficerDecision(
        target_id="missing-target",
        route_id="missing-route",
    )
    result = compile(
        config,
        evidence_store=store,
        evidence_state="a" * 64,
        officer_decisions=(decision,),
        publication_authority=authority,
    )

    assert result.status == "terminated"
    assert result.artifacts == {}
    assert result.metadata["publication_action"] == "retain-previous-valid-publication"
    assert (baseline.output_dir / "run.json").is_file()
    assert result.metadata["compilation_input_fingerprint"]
    assert len(result.metadata["compilation_input_fingerprint"]) == 64
    assert result.metadata["reviewable_network"]["failure_code"] == ("mandatory-evidence-invalid")
    assert result.metadata["reviewable_network"]["officer_decision_ids"]


def test_public_compile_accounts_assets_after_final_reviewable_selection(
    tmp_path, monkeypatch
) -> None:
    import satn.compiler as compiler_module

    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    original = compiler_module.build_asset_accounting
    observed = False

    def account(context, network, compiled):
        nonlocal observed
        observed = True
        assert compiled.reviewable_network is not None
        return original(context, network, compiled)

    monkeypatch.setattr(compiler_module, "build_asset_accounting", account)

    result = compile(
        config,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )

    assert observed is True
    assert result.status in {"complete", "reviewable"}
