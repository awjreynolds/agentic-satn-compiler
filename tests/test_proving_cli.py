"""Public proving/release CLI contract for the Parallel-Reduction corpus."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import satn.proving_cli as proving_cli
from satn.cli import app

PROJECT = Path(__file__).parents[1]
MANIFEST = (
    PROJECT
    / "data/corpus/parallel-reduction/parallel-reduction-reuse-first-composite-vNext.json"
)
EXPECTED = MANIFEST.parent / "expected/parallel-reduction-reuse-first-composite-vNext.expected.json"
DEEP = MANIFEST.parent / "deep"
RUNNER = CliRunner()


def test_light_manifest_is_vnext_and_carries_real_candidate_route_data() -> None:
    import json

    value = json.loads(MANIFEST.read_text())
    assert value["contract"] == "satn-reuse-first-proving-manifest/v1"
    assert value["profile"]["contract"] == "satn-network-selection-profile/vNext"
    required_cases = {
        "existing-cycleway-over-shorter-a-road",
        "upgradeable-prow-over-a-road",
        "bounded-hybrid",
        "unresolved-local-connector",
        "proposed-new-versus-gap",
        "traffic-challenge",
        "evidence-quality",
        "complete-accounting",
        "map-semantics",
        "officer-target-unavailable",
    }
    assert required_cases <= {item["case_id"] for item in value["candidate_sets"]}
    assert all(
        len(candidate["coordinates"]) >= 2
        for item in value["candidate_sets"]
        for candidate in item["candidates"]
    )
    assert "map_semantics" not in value
    assert "evidence_quality" not in value
    assert "officer_decisions" not in value
    assert isinstance(value["traffic_observations"], list)
    assert isinstance(value["officer_inputs"], list)


def test_vnext_compile_uses_production_reviewable_asset_traffic_and_map_seams(
    monkeypatch,
) -> None:
    calls = {"reviewable": 0, "accounting": 0, "traffic": 0, "map": 0}
    original_reviewable = proving_cli.compile_reviewable_network
    original_accounting = proving_cli.build_asset_accounting
    original_traffic = proving_cli.match_dft_traffic
    original_map = proving_cli._reviewable_map_collection

    def reviewable(*args, **kwargs):
        calls["reviewable"] += 1
        return original_reviewable(*args, **kwargs)

    def accounting(*args, **kwargs):
        calls["accounting"] += 1
        return original_accounting(*args, **kwargs)

    def traffic(*args, **kwargs):
        calls["traffic"] += 1
        return original_traffic(*args, **kwargs)

    def map_collection(*args, **kwargs):
        calls["map"] += 1
        return original_map(*args, **kwargs)

    monkeypatch.setattr(proving_cli, "compile_reviewable_network", reviewable)
    monkeypatch.setattr(proving_cli, "build_asset_accounting", accounting)
    monkeypatch.setattr(proving_cli, "match_dft_traffic", traffic)
    monkeypatch.setattr(proving_cli, "_reviewable_map_collection", map_collection)
    manifest = proving_cli._vnext_manifest(MANIFEST)
    actual = proving_cli._compile_vnext_manifest(manifest)

    assert calls["reviewable"] == 1
    assert calls["accounting"] == 1
    assert calls["traffic"] >= 1
    assert calls["map"] == 1
    assert actual["completion"]["selection_performed"] is True


def test_proving_check_runs_one_composite_and_reads_expected_without_writing(
    monkeypatch, tmp_path: Path
) -> None:
    """The light gate is one production-seam compile and a read-only comparison."""

    before = EXPECTED.read_bytes()
    calls = 0
    original = proving_cli._compile_vnext_manifest

    def compile_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(proving_cli, "_compile_vnext_manifest", compile_once)
    result = RUNNER.invoke(app, ["proving", "check"])

    assert result.exit_code == 0, result.stdout
    assert calls == 1
    assert EXPECTED.read_bytes() == before
    assert not list(tmp_path.iterdir())
    assert "passed" in result.stdout.lower()


def test_proving_check_returns_nonzero_on_semantic_drift(monkeypatch) -> None:
    original = proving_cli._compile_vnext_manifest

    def drifted(manifest):
        value = original(manifest)
        value["network_gaps"] = [{"drift": True}]
        return value

    monkeypatch.setattr(proving_cli, "_compile_vnext_manifest", drifted)
    result = RUNNER.invoke(app, ["proving", "check"])

    assert result.exit_code != 0
    assert "drift" in (result.stdout + result.stderr).lower()


def test_proving_regenerate_requires_staging_and_never_overwrites_expected(
    tmp_path: Path,
) -> None:
    before = EXPECTED.read_bytes()
    staging = tmp_path / "staging"

    result = RUNNER.invoke(
        app,
        ["proving", "regenerate", "--staging-dir", str(staging)],
    )

    assert result.exit_code == 0, result.stdout
    assert EXPECTED.read_bytes() == before
    assert (staging / EXPECTED.name).is_file()
    assert (staging / EXPECTED.with_suffix(".svg").name).is_file()
    assert set(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == {
        Path("staging"),
        Path("staging") / EXPECTED.name,
        Path("staging") / EXPECTED.with_suffix(".svg").name,
    }


def test_proving_help_exposes_deep_data_gate() -> None:
    result = RUNNER.invoke(app, ["proving", "check", "--help"], color=False)

    assert result.exit_code == 0
    assert "--deep" in result.stdout


def test_proving_deep_check_compiles_each_independent_vnext_manifest() -> None:
    deep_ids = {path.stem for path in DEEP.glob("*.json")}
    assert len(deep_ids) >= 3

    result = RUNNER.invoke(app, ["proving", "check", "--deep"])

    assert result.exit_code == 0, result.stdout
    assert deep_ids <= set(result.stdout.split())
    assert "composite compile(s)" in result.stdout


def test_light_expected_result_carries_selection_accounting_and_map_semantics() -> None:
    import json

    value = json.loads(EXPECTED.read_text())
    selections = {item["case_id"]: item for item in value["selections"]}
    assert selections["existing-cycleway-over-shorter-a-road"]["selected_route_id"] == (
        "cycleway-existing"
    )
    assert selections["upgradeable-prow-over-a-road"]["selected_route_id"] == (
        "prow-upgrade"
    )
    assert selections["bounded-hybrid"]["selected_route_id"] == "hybrid-reuse"
    assert selections["unresolved-local-connector"]["disposition"] == "network-gap"
    assert selections["proposed-new-versus-gap"]["selected_route_id"] == (
        "proposed-new-link"
    )
    assert any(
        item["asset_id"] == "asset-accounting"
        and item["evidence_state"] == "conflicting"
        and item["candidate_participations"]
        for item in value["assets"]
    )
    assert {
        item["disposition"] for item in value["officer_decisions"]
    } == {"material-divergence", "target-unavailable"}
    map_by_route = {item["route_id"]: item for item in value["map_semantics"]}
    assert map_by_route["cycleway-existing"]["display_state"] == "existing-provision"
    assert map_by_route["map-upgrade"]["display_state"] == "upgrade-required"
    assert any(
        item["feature_type"] == "reviewable-gap-endpoint"
        and item["reason"] == "no-generated-candidates"
        for item in value["map_semantics"]
    )
    traffic = value["traffic_diagnostics"]
    assert {item["traffic_status"] for item in traffic} >= {"unknown", "conflicting"}
    assert any(item.get("freshness_state") == "stale" for item in traffic)
