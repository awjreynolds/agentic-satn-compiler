"""Synthetic-only proving-corpus contract for Parallel-Reduction."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from satn.cli import app
from satn.parallel_reduction_corpus import (
    EXPECTED_RESULT_CONTRACT,
    ScriptedCorpusRuntime,
    assert_matches_expected,
    canonical_expected_result,
    load_expected_result,
    load_manifest,
)

PROJECT = Path(__file__).parents[1]
ACCEPTANCE_MANIFEST = PROJECT / "data/corpus/parallel-reduction/acceptance-composite.json"
RUNNER = CliRunner()


def test_composite_manifest_declares_every_light_acceptance_zone() -> None:
    manifest = load_manifest(ACCEPTANCE_MANIFEST)

    assert manifest.scenario_id == "parallel-reduction-acceptance-composite"
    assert {zone["zone_id"] for zone in manifest.zones} == {
        "convergence-and-divergence", "scope-brackets", "continuous-hybrid",
        "material-dominance", "deterministic-hierarchy", "scripted-agent-choice",
        "scripted-runtime-fallback", "access-only-quiet-lane",
        "crossing-warning-and-bridge-gap", "officer-divergence",
    }
    assert manifest.expected_result_path == (
        ACCEPTANCE_MANIFEST.parent / "expected/acceptance-composite.json"
    )
    assert (
        load_expected_result(manifest.expected_result_path)["contract"]
        == EXPECTED_RESULT_CONTRACT
    )


def test_manifest_rejects_zones_within_rural_candidate_distance(tmp_path: Path) -> None:
    source = ACCEPTANCE_MANIFEST.read_text(encoding="ascii")
    invalid = tmp_path / "invalid.json"
    invalid.write_text(source.replace("[4000, 0]", "[1500, 0]"), encoding="ascii")

    with pytest.raises(ValueError, match="separated beyond rural proximity"):
        load_manifest(invalid)


def test_scripted_runtime_returns_only_configured_choice_or_failure() -> None:
    runtime = ScriptedCorpusRuntime(
        ({"request_id": "choose", "outcome": "select", "route_id": "east"},)
    )

    assert runtime.choose({"request_id": "choose"}) == {"route_id": "east"}
    with pytest.raises(RuntimeError, match="response-missing"):
        runtime.choose({"request_id": "other"})


def test_cli_exposes_only_the_explicit_parallel_reduction_regeneration_command() -> None:
    result = RUNNER.invoke(app, ["corpus", "parallel-reduction", "--help"])

    assert result.exit_code == 0
    assert "regenerate" in result.stdout
    assert "compile" not in result.stdout.lower()


def test_canonical_result_excludes_volatile_runtime_fields() -> None:
    manifest = load_manifest(ACCEPTANCE_MANIFEST)
    result = canonical_expected_result(
        manifest,
        {
            "scenario": {
                "candidate_sets": [],
                "selections": [],
                "decision_record": {"mode": "no-agent"},
                "network_gaps": [],
            },
            "artifact": {
                "officer_compiler_divergences": [
                    {"id": "divergence", "usage": {"tokens": 3}, "model": "fake"}
                ]
            },
        },
    )

    assert result["decisions"] == [{"mode": "no-agent"}]
    assert result["material_officer_compiler_divergences"] == [{"id": "divergence"}]
    assert_matches_expected(result, result)


def test_composite_acceptance_compiles_through_the_supported_production_seam() -> None:
    """One complete compilation; never a helper, review-map, or publication test."""

    from satn.parallel_reduction import (
        ParallelReductionRequest,
        compile_parallel_reduction_scenario,
    )

    manifest = load_manifest(ACCEPTANCE_MANIFEST)
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest.model_validate(manifest.request),
        runtime=ScriptedCorpusRuntime(manifest.runtime_responses),
    )

    assert result.scenario.publishable is True
    actual = canonical_expected_result(manifest, result)
    assert_matches_expected(actual, load_expected_result(manifest.expected_result_path))
