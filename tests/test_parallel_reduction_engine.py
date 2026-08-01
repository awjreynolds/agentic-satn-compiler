from __future__ import annotations

from satn.parallel_reduction import (
    ParallelReductionRequest,
    ParallelRoute,
    compile_parallel_reduction_scenario,
)


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
