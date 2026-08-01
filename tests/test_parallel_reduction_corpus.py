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
    render_expected_visual,
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
    assert manifest.expected_visual_path == (
        ACCEPTANCE_MANIFEST.parent / "expected/acceptance-composite.svg"
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
        "population-499",
        "population-500",
        "population-501",
        "topography-19",
        "topography-20",
        "topography-21",
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
                evidence_ids=("deep-west-governed-evidence",),
                population=200,
                existing_infrastructure_score=20,
            ),
            ParallelRoute(
                route_id="deep-east",
                endpoints=("deep-a", "deep-b"),
                coordinates=((0.0, float(distance_m)), (1_000.0, float(distance_m))),
                network_scope=scope,
                evidence_ids=("deep-east-governed-evidence",),
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
        assert result.scenario.scenario_fingerprint
        assert result.artifact.relations == ()


@pytest.mark.parametrize("outcome", ["provider-failure", "invalid-response", "timeout"])
@pytest.mark.parallel_reduction_deep
def test_deep_runtime_failure_classes_complete_with_deterministic_fallback(outcome: str) -> None:
    import time

    from satn.parallel_reduction import (
        ParallelReductionConfig,
        ParallelReductionRequest,
        compile_parallel_reduction_scenario,
    )

    class DeepRuntime:
        def choose(self, request: object):
            if outcome == "provider-failure":
                raise RuntimeError("deep-provider-failure")
            if outcome == "timeout":
                time.sleep(0.02)
            return {"route_id": "not-offered"}

    request = _deep_request(distance_m=400, scope="urban", runtime_eligible=True)
    request = ParallelReductionRequest.model_validate(
        {
            **request.model_dump(mode="python"),
            "config": ParallelReductionConfig(
                runtime_eligible=True,
                runtime_deadline_seconds=0.001,
            ),
        }
    )
    result = compile_parallel_reduction_scenario(
        request, DeepRuntime()
    )
    assert result.scenario.scenario_fingerprint
    assert result.artifact.decisions[0].mode == "fallback"
    assert result.artifact.decisions[0].fallback_trigger == {
        "provider-failure": "runtime-error",
        "invalid-response": "invalid-runtime-response",
        "timeout": "runtime-timeout",
    }[outcome]


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


@pytest.mark.parallel_reduction_deep
def test_deep_population_threshold_cases_execute_governed_sustained_evidence() -> None:
    """The 500-resident equality boundary is exercised through governed OA evidence."""

    from satn.parallel_reduction import (
        ParallelOutputAreaCentroid,
        ParallelReductionConfig,
        ParallelReductionRequest,
        compile_parallel_reduction_scenario,
    )

    for residents in (499, 500, 501):
        case = _deep_cases()[f"population-{residents}"]
        config = ParallelReductionConfig(
            runtime_eligible=True,
        )
        base_request = _deep_request(distance_m=400, scope="urban", runtime_eligible=True)
        request_data = base_request.model_dump(mode="python")
        request_data["routes"] = [
            {
                **route.model_dump(mode="python"),
                "existing_infrastructure_score": 0,
            }
            for route in base_request.routes
        ]
        request = ParallelReductionRequest.model_validate(
            {
                **request_data,
                "profile_id": f"parallel-population-{residents}",
                "config": config,
                "output_area_centroids": (
                    ParallelOutputAreaCentroid(
                        oa_id="E00000001",
                        residents=residents,
                        coordinates=(500.0, 0.0),
                        inside_area=True,
                    ),
                    ),
                    "output_area_source_fingerprint": "a" * 64,
                    "output_area_evidence_ids": ("deep-governed-oa-evidence",),
                    "output_area_citation_ids": ("deep-governed-oa-source",),
                }
            )

        result = compile_parallel_reduction_scenario(request)
        assert result.scenario.scenario_fingerprint
        decision = result.artifact.decisions[0]
        expected_runtime_boundary = case["expected"] == "runtime-boundary"
        assert decision.mode == ("fallback" if expected_runtime_boundary else "deterministic")
        assert decision.fallback_trigger == (
            "runtime-unavailable" if expected_runtime_boundary else None
        )


@pytest.mark.parallel_reduction_deep
def test_deep_topography_threshold_cases_use_direction_independent_cev() -> None:
    """The 20-metre equality boundary is exercised through raw elevation evidence."""

    from satn.parallel_reduction import (
        ParallelReductionConfig,
        ParallelReductionRequest,
        compile_parallel_reduction_scenario,
    )

    for cev_difference_m in (19, 20, 21):
        case = _deep_cases()[f"topography-{cev_difference_m}"]
        request = _deep_request(distance_m=400, scope="urban", runtime_eligible=True)
        route_data = []
        for route in request.routes:
            elevation_samples = (
                ((0.0, 0.0), (100.0, cev_difference_m / 2), (200.0, 0.0))
                if route.route_id == "deep-east"
                else ((0.0, 0.0), (100.0, 0.0), (200.0, 0.0))
            )
            route_data.append(
                {
                    **route.model_dump(mode="python"),
                    "coordinates": (
                        (0.0, route.coordinates[0][1]),
                        (200.0, route.coordinates[0][1]),
                    ),
                    "existing_infrastructure_score": 0,
                    "elevation_samples": elevation_samples,
                }
            )
        governed_request = ParallelReductionRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "profile_id": f"parallel-topography-{cev_difference_m}",
                "config": ParallelReductionConfig(runtime_eligible=True),
                "routes": route_data,
            }
        )

        decision = compile_parallel_reduction_scenario(governed_request).artifact.decisions[0]
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

    assert result.scenario.scenario_fingerprint
    assert result.artifact.fingerprint
    assert len(result.selected_route_ids) == len(result.artifact.decisions)
    assert result.artifact.decisions[0].mode == "deterministic"
    assert result.artifact.decisions[0].fallback_trigger is None
    assert (
        result.artifact.decisions[0].selected_route_id
        == result.artifact.decisions[0].compiler_preferred_route_id
    )
    assert case["expected"] == "deterministic"


def test_manifest_rejects_zones_within_rural_candidate_distance(tmp_path: Path) -> None:
    source = json.loads(ACCEPTANCE_MANIFEST.read_text(encoding="ascii"))
    source["zones"][1]["origin_m"] = [1_500, 0]
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(source), encoding="ascii")

    with pytest.raises(ValueError, match="separated beyond rural proximity"):
        load_manifest(invalid)


def test_scripted_runtime_returns_only_configured_choice_or_failure() -> None:
    runtime = ScriptedCorpusRuntime(
        (
            {
                "request_id": "choose",
                "outcome": "select",
                "route_id": "east",
                "decisive_consideration_ids": ["access:east"],
            },
        )
    )

    assert runtime.choose({"request_id": "choose"}) == {
        "route_id": "east",
        "decisive_consideration_ids": ("access:east",),
    }
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

    assert result.scenario.scenario_fingerprint
    assert result.artifact.fingerprint
    assert len(result.selected_route_ids) == len(result.artifact.decisions)
    decisions = {item.target_id: item for item in result.artifact.decisions}
    assert decisions["parallel:agent-a:agent-b"].mode == "agent"
    assert decisions["parallel:agent-a:agent-b"].decisive_consideration_ids == (
        "access:agent-east",
    )
    assert decisions["parallel:fallback-a:fallback-b"].mode == "fallback"
    assert decisions["parallel:officer-a:officer-b"].mode == "officer"
    assert result.artifact.officer_compiler_divergences
    assert any(item.scope_sensitive for item in result.artifact.relations)
    assert result.artifact.crossing_warnings and result.artifact.network_gaps
    assert any(item.route_id.startswith("hybrid:") for item in result.artifact.options)
    assert "quiet-lane" in result.selected_route_ids
    actual = canonical_expected_result(manifest, result)
    assert_matches_expected(actual, load_expected_result(manifest.expected_result_path))
    assert render_expected_visual(actual) == manifest.expected_visual_path.read_text(
        encoding="ascii"
    )
