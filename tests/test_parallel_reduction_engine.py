from __future__ import annotations

import pytest

from satn.parallel_reduction import (
    ParallelReductionConfig,
    ParallelReductionRequest,
    ParallelRoute,
    compile_parallel_reduction_scenario,
)


class ExplodingRuntime:
    def __init__(self) -> None:
        self.calls = 0

    def choose(self, request: object) -> str:
        self.calls += 1
        raise RuntimeError("provider unavailable")


def test_raw_parallel_routes_compile_to_one_complete_candidate_set() -> None:
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-test",
            routes=(
                ParallelRoute(
                    route_id="main-road",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 0.0), (1_000.0, 0.0)),
                    network_scope="urban",
                    population=100,
                ),
                ParallelRoute(
                    route_id="quiet-lane",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 400.0), (1_000.0, 400.0)),
                    network_scope="urban",
                    population=100,
                    access_only_quiet_lane=True,
                ),
            ),
        )
    )

    assert len(result.scenario.candidate_sets) == 1
    assert len(result.scenario.selections[0].candidate_set.candidates) == 2
    assert result.scenario.publishable
    assert result.selected_route_ids == ("quiet-lane",)


def test_unresolved_scope_retains_wider_only_relation_and_runtime_failure_falls_back() -> None:
    class InvalidRuntime:
        def choose(self, request: object) -> str:
            return "not-offered"

    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-runtime",
            config=ParallelReductionConfig(runtime_eligible=True),
            routes=(
                ParallelRoute(
                    route_id="population-route",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 0.0), (1_000.0, 0.0)),
                    network_scope="unresolved",
                    population=200,
                ),
                ParallelRoute(
                    route_id="access-route",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 800.0), (1_000.0, 800.0)),
                    network_scope="rural",
                    population=100,
                    access_score=2.0,
                ),
            ),
        ),
        runtime=InvalidRuntime(),
    )

    assert result.artifact.relations[0].scope_sensitive
    assert result.artifact.decisions[0].mode == "fallback"
    assert result.artifact.decisions[0].fallback_trigger == "invalid-runtime-response"
    assert result.scenario.decision_record.mode == "accepted-agent-decision-ledger"
    assert result.scenario.publishable


def test_transitive_parallel_group_is_order_independent_and_preserves_all_members() -> None:
    routes = tuple(
        ParallelRoute(
            route_id=route_id,
            endpoints=("alpha", "beta"),
            coordinates=((0.0, y), (1_000.0, y)),
            network_scope="urban",
        )
        for route_id, y in (("outer-a", 0.0), ("middle", 400.0), ("outer-b", 800.0))
    )
    first = compile_parallel_reduction_scenario(
        ParallelReductionRequest(profile_id="parallel-transitive", routes=routes)
    )
    second = compile_parallel_reduction_scenario(
        ParallelReductionRequest(profile_id="parallel-transitive", routes=tuple(reversed(routes)))
    )

    assert len(first.scenario.candidate_sets[0].candidates) == 3
    assert first.scenario.scenario_fingerprint == second.scenario.scenario_fingerprint


def test_unavailable_officer_target_and_required_missing_bridge_are_retained() -> None:
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-gap",
            routes=(
                ParallelRoute(
                    route_id="left",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 0.0), (1_000.0, 0.0)),
                    network_scope="urban",
                ),
                ParallelRoute(
                    route_id="right",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 300.0), (1_000.0, 300.0)),
                    network_scope="urban",
                ),
            ),
            required_transitions=(("left", "right"),),
            officer_decisions=(({"target_id": "parallel:alpha:beta", "route_id": "gone"}),),
        )
    )

    assert result.artifact.network_gaps[0].intervention_archetype == "bridge"
    assert result.artifact.officer_target_unavailable[0].route_id == "gone"


def test_single_non_parallel_route_still_completes_as_a_publishable_scenario() -> None:
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-pass-through",
            routes=(
                ParallelRoute(
                    route_id="only-route",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 0.0), (1_000.0, 0.0)),
                    network_scope="urban",
                ),
            ),
        )
    )

    assert result.scenario.publishable
    assert result.selected_route_ids == ("only-route",)
    assert result.artifact.retained_route_ids == ("only-route",)
    assert result.artifact.relations == ()


def test_runtime_exception_falls_back_once_and_never_prevents_generation() -> None:
    runtime = ExplodingRuntime()
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-runtime-error",
            config=ParallelReductionConfig(runtime_eligible=True),
            routes=(
                ParallelRoute(
                    route_id="population-route",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 0.0), (1_000.0, 0.0)),
                    network_scope="urban",
                    source_class="verified-existing-asset",
                    population=200,
                ),
                ParallelRoute(
                    route_id="access-route",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 300.0), (1_000.0, 300.0)),
                    network_scope="urban",
                    population=100,
                    access_score=2,
                ),
            ),
        ),
        runtime=runtime,
    )

    assert runtime.calls == 1
    assert result.scenario.publishable
    assert result.artifact.decisions[0].mode == "fallback"
    assert result.artifact.decisions[0].fallback_trigger == "runtime-error"


def test_officer_input_is_applied_without_invoking_runtime() -> None:
    runtime = ExplodingRuntime()
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-officer-first",
            config=ParallelReductionConfig(runtime_eligible=True),
            routes=(
                ParallelRoute(
                    route_id="population-route",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 0.0), (1_000.0, 0.0)),
                    network_scope="urban",
                    source_class="verified-existing-asset",
                    population=200,
                ),
                ParallelRoute(
                    route_id="officer-route",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 300.0), (1_000.0, 300.0)),
                    network_scope="urban",
                    population=100,
                    access_score=2,
                ),
            ),
            officer_decisions=({"target_id": "parallel:alpha:beta", "route_id": "officer-route"},),
        ),
        runtime=runtime,
    )

    assert runtime.calls == 0
    assert result.selected_route_ids == ("officer-route",)
    assert result.artifact.officer_compiler_divergences


def test_section_population_evidence_uses_exact_defaults_and_sustained_boundary() -> None:
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-evidence",
            routes=(
                ParallelRoute(
                    route_id="west",
                    endpoints=("a", "b"),
                    coordinates=((0, 0), (500, 0)),
                    network_scope="urban",
                    population=500,
                ),
                ParallelRoute(
                    route_id="east",
                    endpoints=("a", "b"),
                    coordinates=((0, 400), (500, 400)),
                    network_scope="urban",
                    population=0,
                ),
            ),
        )
    )
    profile = result.artifact.section_population_profile
    assert profile["display_section_length_m"] == 100.0
    assert profile["urban_capture_radius_m"] == 250.0
    assert profile["rural_capture_radius_m"] == 750.0
    assert result.artifact.material_population_differences


def test_missing_elevation_is_explicit_and_never_changes_selection() -> None:
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-missing-elevation",
            routes=(
                ParallelRoute(
                    route_id="west",
                    endpoints=("a", "b"),
                    coordinates=((0, 0), (600, 0)),
                    network_scope="urban",
                    population=1,
                ),
                ParallelRoute(
                    route_id="east",
                    endpoints=("a", "b"),
                    coordinates=((0, 400), (600, 400)),
                    network_scope="urban",
                    population=2,
                ),
            ),
        )
    )
    assert result.artifact.missing_evidence == ("elevation:east", "elevation:west")
    assert result.selected_route_ids == ("east",)


@pytest.mark.parametrize(
    ("left_peak", "right_peak", "material"),
    [(39, 30, False), (50, 40, False), (40, 30, True), (50, 30, True)],
    ids=("absolute-below", "relative-below", "both-at", "both-above"),
)
def test_pairwise_cev_uses_absolute_and_larger_relative_boundaries(
    left_peak: float, right_peak: float, material: bool
) -> None:
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-cev-boundaries",
            routes=(
                ParallelRoute(
                    route_id="left",
                    endpoints=("a", "b"),
                    coordinates=((0, 0), (100, 0), (200, 0)),
                    network_scope="urban",
                    elevation_samples=((0, 0), (100, left_peak), (200, 0)),
                ),
                ParallelRoute(
                    route_id="right",
                    endpoints=("a", "b"),
                    coordinates=((0, 400), (100, 400), (200, 400)),
                    network_scope="urban",
                    elevation_samples=((0, 0), (100, right_peak), (200, 0)),
                ),
            ),
        )
    )
    comparison = result.artifact.cumulative_elevation_variation[0]
    assert comparison["material"] is material
    assert comparison["left_cumulative_elevation_variation_m"] == left_peak * 2
    assert comparison["right_cumulative_elevation_variation_m"] == right_peak * 2
    assert comparison["absolute_difference_m"] == abs(left_peak - right_peak) * 2


def test_rural_capture_profile_is_used_for_a_rural_raw_route() -> None:
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-rural-capture",
            routes=(
                ParallelRoute(
                    route_id="left",
                    endpoints=("a", "b"),
                    coordinates=((0, 0), (600, 0)),
                    network_scope="rural",
                ),
                ParallelRoute(
                    route_id="right",
                    endpoints=("a", "b"),
                    coordinates=((0, 700), (600, 700)),
                    network_scope="rural",
                ),
            ),
        )
    )
    assert result.artifact.section_population_profile["rural_capture_radius_m"] == 750.0
