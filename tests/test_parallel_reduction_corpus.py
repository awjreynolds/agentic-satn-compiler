"""Synthetic-only proving-corpus contract for Parallel-Reduction."""

from __future__ import annotations

import json
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
DEEP_THRESHOLDS = PROJECT / "data/corpus/parallel-reduction/deep-thresholds.json"
RUNNER = CliRunner()


def test_composite_manifest_declares_every_light_acceptance_zone() -> None:
    manifest = load_manifest(ACCEPTANCE_MANIFEST)

    assert manifest.scenario_id == "parallel-reduction-acceptance-composite"
    assert {zone["zone_id"] for zone in manifest.zones} == {
        "convergence-and-divergence",
        "scope-brackets",
        "continuous-hybrid",
        "material-dominance",
        "deterministic-hierarchy",
        "scripted-agent-choice",
        "scripted-runtime-fallback",
        "access-only-quiet-lane",
        "crossing-warning-and-bridge-gap",
        "officer-divergence",
    }
    assert manifest.expected_result_path == (
        ACCEPTANCE_MANIFEST.parent / "expected/acceptance-composite.json"
    )
    assert (
        load_expected_result(manifest.expected_result_path)["contract"] == EXPECTED_RESULT_CONTRACT
    )


@pytest.mark.parallel_reduction_deep
def test_deep_data_declares_exact_boundary_and_completion_cases() -> None:
    deep = json.loads(DEEP_THRESHOLDS.read_text(encoding="ascii"))
    cases = {item["id"]: item for item in deep["cases"]}

    assert set(cases) == {
        "coverage-79",
        "coverage-80",
        "coverage-81",
        "urban-499",
        "urban-500",
        "urban-501",
        "rural-1499",
        "rural-1500",
        "rural-1501",
        "population-99",
        "population-100",
        "population-101",
        "topography-9",
        "topography-10",
        "topography-11",
        "reversed-input",
        "missing-evidence",
        "runtime-timeout",
        "runtime-provider-failure",
        "runtime-invalid-response",
        "repeat-run",
    }

    assert [cases[f"coverage-{value}"]["expected"] for value in (79, 80, 81)] == [
        "reject",
        "admit",
        "admit",
    ]
    assert [cases[f"urban-{value}"]["expected"] for value in (499, 500, 501)] == [
        "admit",
        "admit",
        "reject",
    ]
    assert [cases[f"rural-{value}"]["expected"] for value in (1499, 1500, 1501)] == [
        "admit",
        "admit",
        "reject",
    ]
    assert {
        "reversed-input",
        "missing-evidence",
        "runtime-timeout",
        "runtime-provider-failure",
        "runtime-invalid-response",
        "repeat-run",
    }.issubset(cases)


def _deep_request(*, distance_m: int, scope: str = "urban", runtime_eligible: bool = False):
    from satn.parallel_reduction import (
        ParallelReductionConfig,
        ParallelReductionRequest,
        ParallelRoute,
    )

    return ParallelReductionRequest(
        profile_id="parallel-deep",
        config=ParallelReductionConfig(runtime_eligible=runtime_eligible),
        routes=(
            ParallelRoute(
                route_id="deep-west",
                endpoints=("deep-a", "deep-b"),
                coordinates=((0.0, 0.0), (1_000.0, 0.0)),
                network_scope=scope,
                population=200,
            ),
            ParallelRoute(
                route_id="deep-east",
                endpoints=("deep-a", "deep-b"),
                coordinates=((0.0, float(distance_m)), (1_000.0, float(distance_m))),
                network_scope=scope,
                population=100,
                access_score=20,
            ),
        ),
    )


@pytest.mark.parametrize(
    ("case_id", "distance_m", "scope", "compiles"),
    [
        ("urban-499", 499, "urban", True),
        ("urban-500", 500, "urban", True),
        ("urban-501", 501, "urban", False),
        ("rural-1499", 1499, "rural", True),
        ("rural-1500", 1500, "rural", True),
        ("rural-1501", 1501, "rural", False),
    ],
)
@pytest.mark.parallel_reduction_deep
def test_deep_distance_cases_execute_the_public_compiler_seam(
    case_id: str, distance_m: int, scope: str, compiles: bool
) -> None:
    from satn.parallel_reduction import compile_parallel_reduction_scenario

    if compiles:
        assert compile_parallel_reduction_scenario(
            _deep_request(distance_m=distance_m, scope=scope)
        ).artifact.relations
    else:
        result = compile_parallel_reduction_scenario(
            _deep_request(distance_m=distance_m, scope=scope)
        )
        assert result.scenario.publishable
        assert result.artifact.relations == ()


@pytest.mark.parametrize("outcome", ["provider-failure", "invalid-response", "timeout"])
@pytest.mark.parallel_reduction_deep
def test_deep_runtime_failure_classes_complete_with_deterministic_fallback(outcome: str) -> None:
    from satn.parallel_reduction import compile_parallel_reduction_scenario

    class DeepRuntime:
        def choose(self, request: object):
            if outcome == "provider-failure":
                raise RuntimeError("deep-provider-failure")
            return {"route_id": "not-offered"}

    result = compile_parallel_reduction_scenario(
        _deep_request(distance_m=400, scope="urban", runtime_eligible=True), DeepRuntime()
    )
    assert result.scenario.publishable
    assert result.artifact.decisions[0].mode == "fallback"


@pytest.mark.parallel_reduction_deep
def test_deep_order_and_repeat_cases_have_identical_compiler_identity() -> None:
    from satn.parallel_reduction import (
        ParallelReductionRequest,
        compile_parallel_reduction_scenario,
    )

    request = _deep_request(distance_m=400, scope="urban")
    reversed_request = ParallelReductionRequest.model_validate(
        {**request.model_dump(mode="python"), "routes": list(reversed(request.routes))}
    )
    first = compile_parallel_reduction_scenario(request)
    assert (
        first.scenario.scenario_fingerprint
        == compile_parallel_reduction_scenario(request).scenario.scenario_fingerprint
    )
    assert (
        first.scenario.scenario_fingerprint
        == compile_parallel_reduction_scenario(reversed_request).scenario.scenario_fingerprint
    )


def _deep_cases() -> dict[str, dict[str, object]]:
    return {
        item["id"]: item
        for item in json.loads(DEEP_THRESHOLDS.read_text(encoding="ascii"))["cases"]
    }


@pytest.mark.parallel_reduction_deep
def test_deep_coverage_cases_execute_calibrated_raw_divergence_geometry() -> None:
    """Keep coverage boundaries in the corpus as geometry, not mocked percentages."""

    from satn.parallel_reduction import (
        ParallelReductionConfig,
        ParallelReductionRequest,
        ParallelRoute,
        compile_parallel_reduction_scenario,
    )

    for case_id in ("coverage-79", "coverage-80", "coverage-81"):
        case = _deep_cases()[case_id]
        divergence = float(case["divergence_length_m"])
        request = ParallelReductionRequest(
            profile_id=f"parallel-{case_id}",
            config=ParallelReductionConfig(
                urban_proximity_m=0.01,
                rural_proximity_m=0.01,
            ),
            routes=(
                ParallelRoute(
                    route_id="coverage-left",
                    endpoints=("coverage-a", "coverage-b"),
                    coordinates=((0.0, 0.0), (1_000.0, 0.0), (1_000.0 + divergence, divergence)),
                    network_scope="urban",
                ),
                ParallelRoute(
                    route_id="coverage-right",
                    endpoints=("coverage-a", "coverage-b"),
                    coordinates=((0.0, 0.0), (1_000.0, 0.0), (1_000.0 + divergence, -divergence)),
                    network_scope="urban",
                ),
            ),
        )

        result = compile_parallel_reduction_scenario(request)
        assert bool(result.artifact.relations) is (case["expected"] == "admit")


@pytest.mark.parametrize("field", ["material_population_difference", "material_score_difference"])
@pytest.mark.parallel_reduction_deep
def test_deep_material_threshold_cases_execute_raw_evidence_conflicts(field: str) -> None:
    """Materiality comes from actual competing route evidence and config thresholds."""

    from satn.parallel_reduction import (
        ParallelReductionConfig,
        ParallelReductionRequest,
        compile_parallel_reduction_scenario,
    )

    prefix = "population" if field == "material_population_difference" else "topography"
    for threshold in (9, 10, 11) if prefix == "topography" else (99, 100, 101):
        case = _deep_cases()[f"{prefix}-{threshold}"]
        config = ParallelReductionConfig(
            runtime_eligible=True,
            material_population_difference=0 if prefix == "topography" else case[field],
            material_score_difference=10 if prefix == "population" else case[field],
        )
        base_request = _deep_request(distance_m=400, scope="urban", runtime_eligible=True)
        request_data = base_request.model_dump(mode="python")
        if prefix == "topography":
            request_data["routes"] = [
                {
                    **route.model_dump(mode="python"),
                    "population": 100,
                    "access_score": 10 if route.route_id == "deep-east" else 0,
                }
                for route in base_request.routes
            ]
        request = ParallelReductionRequest.model_validate(
            {**request_data, "profile_id": f"parallel-{prefix}-{threshold}", "config": config}
        )

        result = compile_parallel_reduction_scenario(request)
        assert result.scenario.publishable is True
        decision = result.artifact.decisions[0]
        expected_runtime_boundary = case["expected"] == "runtime-boundary"
        assert decision.mode == ("fallback" if expected_runtime_boundary else "deterministic")
        assert decision.fallback_trigger == (
            "runtime-unavailable" if expected_runtime_boundary else None
        )


@pytest.mark.parallel_reduction_deep
def test_deep_missing_evidence_case_completes_with_raw_absent_evidence() -> None:
    from satn.parallel_reduction import (
        ParallelReductionConfig,
        ParallelReductionRequest,
        ParallelRoute,
        compile_parallel_reduction_scenario,
    )

    case = _deep_cases()["missing-evidence"]
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-missing-evidence",
            config=ParallelReductionConfig(runtime_eligible=True),
            routes=(
                ParallelRoute(
                    route_id="evidence-present",
                    endpoints=("evidence-a", "evidence-b"),
                    coordinates=((0.0, 0.0), (1_000.0, 0.0)),
                    network_scope="urban",
                    evidence_ids=("verified-link",),
                    population=200,
                ),
                ParallelRoute(
                    route_id="evidence-missing",
                    endpoints=("evidence-a", "evidence-b"),
                    coordinates=((0.0, 400.0), (1_000.0, 400.0)),
                    network_scope="urban",
                    population=0,
                    access_score=20,
                ),
            ),
        )
    )

    assert result.scenario.publishable is True
    assert result.artifact.decisions[0].mode == "fallback"
    assert result.artifact.decisions[0].fallback_trigger == "runtime-unavailable"
    assert (
        result.artifact.decisions[0].selected_route_id
        == result.artifact.decisions[0].compiler_preferred_route_id
    )
    assert case["expected"] == "deterministic-fallback"


def test_manifest_rejects_zones_within_rural_candidate_distance(tmp_path: Path) -> None:
    source = ACCEPTANCE_MANIFEST.read_text(encoding="ascii")
    invalid = tmp_path / "invalid.json"
    invalid.write_text(source.replace("[4000,0]", "[1500,0]"), encoding="ascii")

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
