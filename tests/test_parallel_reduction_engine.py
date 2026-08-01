from __future__ import annotations

import time

import pytest

from satn.parallel_reduction import (
    ParallelChoicePoint,
    ParallelReductionArtifact,
    ParallelReductionConfig,
    ParallelReductionRequest,
    ParallelRoute,
    compile_parallel_reduction_scenario,
    discover_parallel_components,
    discover_parallel_relations,
    discover_parallel_sections,
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
            output_area_centroids=(
                {
                    "oa_id": "E00000001",
                    "residents": 500,
                    "coordinates": (250, 0),
                    "inside_area": True,
                },
            ),
            output_area_source_fingerprint="a" * 64,
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
    assert {"elevation:east", "elevation:west"}.issubset(result.artifact.missing_evidence)
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


def test_shared_governed_oa_is_deduplicated_per_section_and_outside_is_retained() -> None:
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-shared-oa",
            routes=(
                ParallelRoute(
                    route_id="left",
                    endpoints=("a", "b"),
                    coordinates=((0, 0), (200, 0)),
                    network_scope="urban",
                ),
                ParallelRoute(
                    route_id="right",
                    endpoints=("a", "b"),
                    coordinates=((0, 400), (200, 400)),
                    network_scope="urban",
                ),
            ),
            output_area_centroids=(
                {
                    "oa_id": "E00000001",
                    "residents": 100,
                    "coordinates": (100, 200),
                    "inside_area": True,
                },
                {
                    "oa_id": "E00000002",
                    "residents": 50,
                    "coordinates": (100, 0),
                    "inside_area": False,
                },
            ),
            output_area_source_fingerprint="b" * 64,
        )
    )
    section = next(
        item
        for item in result.artifact.section_population_sections
        if item["alignment_id"] == "left"
    )
    assert section["captured_oa_ids"] == ["E00000001", "E00000002"]
    assert section["inside_area_residents"] == 100
    assert section["outside_area_residents"] == 50


def test_missing_governed_oa_evidence_is_explicit_not_fabricated() -> None:
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-missing-oa",
            routes=(
                ParallelRoute(
                    route_id="left",
                    endpoints=("a", "b"),
                    coordinates=((0, 0), (200, 0)),
                    network_scope="urban",
                ),
                ParallelRoute(
                    route_id="right",
                    endpoints=("a", "b"),
                    coordinates=((0, 400), (200, 400)),
                    network_scope="urban",
                ),
            ),
        )
    )
    assert result.artifact.section_population_sections == ()
    assert "section-population:governed-output-area-centroids" in result.artifact.missing_evidence


def test_governed_oa_centroids_require_a_unique_roster_and_source_fingerprint() -> None:
    route = ParallelRoute(
        route_id="route",
        endpoints=("a", "b"),
        coordinates=((0, 0), (200, 0)),
        network_scope="urban",
    )
    centroid = {
        "oa_id": "E00000001",
        "residents": 100,
        "coordinates": (100, 0),
        "inside_area": True,
    }
    with pytest.raises(ValueError, match="required together"):
        ParallelReductionRequest(
            profile_id="parallel-oa-source",
            routes=(route,),
            output_area_centroids=(centroid,),
        )
    with pytest.raises(ValueError, match="must be unique"):
        ParallelReductionRequest(
            profile_id="parallel-oa-duplicates",
            routes=(route,),
            output_area_centroids=(centroid, centroid),
            output_area_source_fingerprint="c" * 64,
        )


def test_guidance_findings_are_separate_cited_and_never_a_score_or_veto() -> None:
    request = ParallelReductionRequest(
        profile_id="parallel-guidance",
        routes=(
            ParallelRoute(
                route_id="left",
                endpoints=("a", "b"),
                coordinates=((0, 0), (200, 0)),
                network_scope="urban",
                guidance_considerations=(
                    {
                        "principle_id": "coherence",
                        "state": "contradicted",
                        "citation_ids": ("ltn120-1.4",),
                    },
                ),
            ),
            ParallelRoute(
                route_id="right",
                endpoints=("a", "b"),
                coordinates=((0, 400), (200, 400)),
                network_scope="urban",
            ),
        ),
    )
    result = compile_parallel_reduction_scenario(request)
    assert result.selected_route_ids
    assert result.artifact.guidance_findings[0]["citation_ids"] == ("ltn120-1.4",)
    assert (
        result.artifact.guidance_findings[0]["material_departure_needs"]
        == "evidence-or-intervention"
    )
    assert not hasattr(result.artifact, "guidance_score")


def test_guidance_profile_change_changes_fingerprint_and_missing_is_unassessed() -> None:
    base = ParallelReductionRequest(
        profile_id="parallel-guidance-fingerprint",
        routes=(
            ParallelRoute(
                route_id="left",
                endpoints=("a", "b"),
                coordinates=((0, 0), (200, 0)),
                network_scope="urban",
            ),
            ParallelRoute(
                route_id="right",
                endpoints=("a", "b"),
                coordinates=((0, 400), (200, 400)),
                network_scope="urban",
            ),
        ),
    )
    changed = ParallelReductionRequest.model_validate(
        {
            **base.model_dump(),
            "guidance_profile": {"profile_id": "national-cycle-and-rural-guidance-2027-01"},
        }
    )
    assert base.guidance_profile.profile_fingerprint != changed.guidance_profile.profile_fingerprint
    assert compile_parallel_reduction_scenario(base).artifact.guidance_findings == ()


def test_runtime_receives_only_finite_menu_and_accepts_relevant_evidence() -> None:
    class CapturingRuntime:
        calls: list[dict[str, object]]

        def __init__(self) -> None:
            self.calls = []

        def choose(self, request: object) -> dict[str, object]:
            assert isinstance(request, dict)
            self.calls.append(request)
            menu = request["route_menu"]
            assert isinstance(menu, tuple)
            selected = next(item for item in menu if item["route_id"] == "access-route")
            return {
                "route_id": "access-route",
                "decisive_consideration_ids": (selected["consideration_ids"][0],),
            }

    runtime = CapturingRuntime()
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-runtime-menu",
            config=ParallelReductionConfig(runtime_eligible=True),
            routes=(
                ParallelRoute(
                    route_id="population-route",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 0.0), (1_000.0, 0.0)),
                    network_scope="urban",
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

    assert len(runtime.calls) == 1
    runtime_request = runtime.calls[0]
    assert set(runtime_request) == {
        "request_id",
        "target_id",
        "compiler_preferred_route_id",
        "route_menu",
        "offered_evidence_ids",
        "offered_consideration_ids",
    }
    assert "coordinates" not in str(runtime_request)
    assert "gradient_pct" not in str(runtime_request)
    assert "'population': 200" not in str(runtime_request)
    decision = result.artifact.decisions[0]
    assert decision.mode == "agent"
    assert decision.validation_status == "accepted"
    assert decision.decisive_consideration_ids


def test_runtime_guidance_ids_are_route_bound_and_unoffered_ids_fall_back() -> None:
    class CaptureRuntime:
        def __init__(self, consideration_id: str) -> None:
            self.consideration_id = consideration_id
            self.request: dict[str, object] | None = None

        def choose(self, request: object) -> dict[str, object]:
            assert isinstance(request, dict)
            self.request = request
            return {
                "route_id": "left",
                "decisive_consideration_ids": (self.consideration_id,),
            }

    request = ParallelReductionRequest(
        profile_id="parallel-guidance-runtime",
        config=ParallelReductionConfig(runtime_eligible=True),
        routes=(
            ParallelRoute(
                route_id="left",
                endpoints=("a", "b"),
                coordinates=((0, 0), (200, 0)),
                network_scope="urban",
                population=200,
                guidance_considerations=(
                    {
                        "principle_id": "coherence",
                        "state": "supported",
                        "citation_ids": ("ltn120-1.4",),
                    },
                ),
            ),
            ParallelRoute(
                route_id="right",
                endpoints=("a", "b"),
                coordinates=((0, 400), (200, 400)),
                network_scope="urban",
                population=100,
                access_score=2,
                guidance_considerations=(
                    {
                        "principle_id": "directness",
                        "state": "supported",
                        "citation_ids": ("rural-guide-2",),
                    },
                ),
            ),
        ),
    )
    valid = CaptureRuntime("guidance:left:coherence")
    accepted = compile_parallel_reduction_scenario(request, runtime=valid)
    assert accepted.artifact.decisions[0].mode == "agent"
    assert valid.request is not None
    menu = valid.request["route_menu"]
    assert menu[0]["consideration_ids"][-1] == "guidance:left:coherence"
    assert menu[1]["consideration_ids"][-1] == "guidance:right:directness"
    assert "Cycle Infrastructure Design" not in str(valid.request)
    assert "citation_ids" not in str(valid.request)

    invalid = CaptureRuntime("guidance:left:invented")
    fallback = compile_parallel_reduction_scenario(request, runtime=invalid)
    assert fallback.artifact.decisions[0].mode == "fallback"
    assert fallback.artifact.decisions[0].validation_status == "invalid-runtime-response"


def test_partial_runtime_response_and_timeout_use_the_same_configured_fallback() -> None:
    class PartialRuntime:
        def choose(self, request: object) -> dict[str, object]:
            return {"route_id": "access-route"}

    class SlowRuntime:
        def __init__(self) -> None:
            self.calls = 0

        def choose(self, request: object) -> dict[str, object]:
            self.calls += 1
            time.sleep(0.05)
            return {"route_id": "access-route", "decisive_consideration_ids": ("ignored",)}

    request = ParallelReductionRequest(
        profile_id="parallel-runtime-deadline",
        config=ParallelReductionConfig(runtime_eligible=True, runtime_deadline_seconds=0.001),
        routes=(
            ParallelRoute(
                route_id="population-route",
                endpoints=("alpha", "beta"),
                coordinates=((0.0, 0.0), (1_000.0, 0.0)),
                network_scope="urban",
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
    )

    partial = compile_parallel_reduction_scenario(request, runtime=PartialRuntime())
    slow_runtime = SlowRuntime()
    timed_out = compile_parallel_reduction_scenario(request, runtime=slow_runtime)

    assert partial.scenario.publishable and timed_out.scenario.publishable
    assert partial.selected_route_ids == timed_out.selected_route_ids
    assert partial.artifact.decisions[0].fallback_trigger == "invalid-runtime-response"
    assert timed_out.artifact.decisions[0].fallback_trigger == "runtime-timeout"
    assert slow_runtime.calls == 1


def test_runtime_is_never_called_without_conflicting_material_advantages() -> None:
    runtime = ExplodingRuntime()
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-runtime-lazy",
            config=ParallelReductionConfig(runtime_eligible=True),
            routes=(
                ParallelRoute(
                    route_id="route-a",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 0.0), (1_000.0, 0.0)),
                    network_scope="urban",
                    population=100,
                    access_score=1,
                ),
                ParallelRoute(
                    route_id="route-b",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 300.0), (1_000.0, 300.0)),
                    network_scope="urban",
                    population=100,
                    access_score=1,
                ),
            ),
        ),
        runtime=runtime,
    )

    assert runtime.calls == 0
    assert result.artifact.decisions[0].validation_status == "not-invoked"


def test_runtime_rejects_decisive_considerations_not_relevant_to_selected_route() -> None:
    class IrrelevantEvidenceRuntime:
        def choose(self, request: object) -> dict[str, object]:
            return {
                "route_id": "access-route",
                "decisive_consideration_ids": ("population:population-route",),
            }

    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-runtime-relevance",
            config=ParallelReductionConfig(runtime_eligible=True),
            routes=(
                ParallelRoute(
                    route_id="population-route",
                    endpoints=("alpha", "beta"),
                    coordinates=((0.0, 0.0), (1_000.0, 0.0)),
                    network_scope="urban",
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
        runtime=IrrelevantEvidenceRuntime(),
    )

    assert result.scenario.publishable
    assert result.artifact.decisions[0].mode == "fallback"
    assert result.artifact.decisions[0].validation_status == "invalid-runtime-response"


def test_logical_sections_are_maximal_between_explicit_divergence_and_rejoin_points() -> None:
    route = ParallelRoute(
        route_id="west",
        endpoints=("a", "b"),
        coordinates=((0, 0), (100, 0), (200, 100), (300, 100), (400, 0)),
        network_scope="urban",
    )
    sections = discover_parallel_sections(
        (route,),
        (
            ParallelChoicePoint(
                choice_point_id="diverge",
                coordinates=(100, 0),
                kind="divergence-rejoin",
            ),
            ParallelChoicePoint(
                choice_point_id="rejoin",
                coordinates=(300, 100),
                kind="divergence-rejoin",
            ),
        ),
    )

    assert [(item.start_choice_point_id, item.end_choice_point_id) for item in sections] == [
        ("endpoint:a", "diverge"),
        ("diverge", "rejoin"),
        ("rejoin", "endpoint:b"),
    ]
    assert all(item.logical_endpoints == ("a", "b") for item in sections)


def test_ordinary_vertices_and_display_boundaries_never_create_section_topology() -> None:
    route = ParallelRoute(
        route_id="uncut",
        endpoints=("a", "b"),
        coordinates=((0, 0), (100, 0), (200, 0), (300, 0)),
        network_scope="urban",
        node_ids=("display-cut", "source-edge-cut", "name-cut", "class-cut"),
    )

    sections = discover_parallel_sections((route,))

    assert len(sections) == 1
    assert sections[0].coordinates == route.coordinates


def test_parallel_groups_are_transitive_components_not_all_same_endpoint_routes() -> None:
    routes = (
        ParallelRoute(
            route_id="a-west",
            endpoints=("a", "b"),
            coordinates=((0, 0), (1_000, 0)),
            network_scope="urban",
        ),
        ParallelRoute(
            route_id="a-east",
            endpoints=("a", "b"),
            coordinates=((0, 300), (1_000, 300)),
            network_scope="urban",
        ),
        ParallelRoute(
            route_id="b-west",
            endpoints=("a", "b"),
            coordinates=((0, 3_000), (1_000, 3_000)),
            network_scope="urban",
        ),
        ParallelRoute(
            route_id="b-east",
            endpoints=("a", "b"),
            coordinates=((0, 3_300), (1_000, 3_300)),
            network_scope="urban",
        ),
    )

    components = discover_parallel_components(
        routes, discover_parallel_relations(routes, ParallelReductionConfig())
    )

    assert components == (("a-east", "a-west"), ("b-east", "b-west"))


def test_brief_convergence_is_not_a_parallel_relation() -> None:
    routes = (
        ParallelRoute(
            route_id="straight",
            endpoints=("a", "b"),
            coordinates=((0, 0), (1_000, 0)),
            network_scope="urban",
        ),
        ParallelRoute(
            route_id="briefly-near",
            endpoints=("a", "b"),
            coordinates=((0, 2_000), (450, 2_000), (500, 0), (550, 2_000), (1_000, 2_000)),
            network_scope="urban",
        ),
    )

    assert discover_parallel_relations(routes, ParallelReductionConfig()) == ()


def test_continuous_material_hybrid_exposes_ordered_sections_and_transition() -> None:
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-explicit-hybrid",
            choice_points=(
                ParallelChoicePoint(
                    choice_point_id="switch",
                    coordinates=(500, 0),
                    kind="junction",
                ),
            ),
            routes=(
                ParallelRoute(
                    route_id="access-first",
                    endpoints=("a", "b"),
                    coordinates=((0, 0), (500, 0), (1_000, 0)),
                    network_scope="urban",
                    section_evidence=(
                        {
                            "start_choice_point_id": "endpoint:a",
                            "end_choice_point_id": "switch",
                            "access_score": 10,
                            "evidence_ids": ("access-a",),
                        },
                        {
                            "start_choice_point_id": "switch",
                            "end_choice_point_id": "endpoint:b",
                            "access_score": 0,
                            "evidence_ids": ("access-b",),
                        },
                    ),
                ),
                ParallelRoute(
                    route_id="infrastructure-last",
                    endpoints=("a", "b"),
                    coordinates=((0, 100), (500, 0), (1_000, 100)),
                    network_scope="urban",
                    section_evidence=(
                        {
                            "start_choice_point_id": "endpoint:a",
                            "end_choice_point_id": "switch",
                            "existing_infrastructure_score": 0,
                            "evidence_ids": ("infra-a",),
                        },
                        {
                            "start_choice_point_id": "switch",
                            "end_choice_point_id": "endpoint:b",
                            "existing_infrastructure_score": 10,
                            "evidence_ids": ("infra-b",),
                        },
                    ),
                ),
            ),
        )
    )

    hybrid = next(item for item in result.artifact.options if item.route_id.startswith("hybrid:"))
    assert len(hybrid.ordered_section_ids) == 2
    assert hybrid.transition_choice_point_ids == ("switch",)
    assert hybrid.provenance_ids == ("access-first", "infrastructure-last", "switch")


def test_nonmaterial_or_non_choice_point_hybrids_are_not_generated() -> None:
    no_material = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-no-material-hybrid",
            choice_points=(
                ParallelChoicePoint(
                    choice_point_id="switch",
                    coordinates=(500, 0),
                    kind="junction",
                ),
            ),
            routes=(
                ParallelRoute(
                    route_id="left",
                    endpoints=("a", "b"),
                    coordinates=((0, 0), (500, 0), (1_000, 0)),
                    network_scope="urban",
                    section_evidence=(
                        {"start_choice_point_id": "endpoint:a", "end_choice_point_id": "switch"},
                        {"start_choice_point_id": "switch", "end_choice_point_id": "endpoint:b"},
                    ),
                ),
                ParallelRoute(
                    route_id="right",
                    endpoints=("a", "b"),
                    coordinates=((0, 100), (500, 0), (1_000, 100)),
                    network_scope="urban",
                    section_evidence=(
                        {"start_choice_point_id": "endpoint:a", "end_choice_point_id": "switch"},
                        {"start_choice_point_id": "switch", "end_choice_point_id": "endpoint:b"},
                    ),
                ),
            ),
        )
    )
    no_choice = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-no-choice-hybrid",
            routes=(
                ParallelRoute(
                    route_id="left",
                    endpoints=("a", "b"),
                    coordinates=((0, 0), (500, 0), (1_000, 0)),
                    network_scope="urban",
                ),
                ParallelRoute(
                    route_id="right",
                    endpoints=("a", "b"),
                    coordinates=((0, 100), (500, 0), (1_000, 100)),
                    network_scope="urban",
                ),
            ),
        )
    )

    assert not any(item.route_id.startswith("hybrid:") for item in no_material.artifact.options)
    assert not any(item.route_id.startswith("hybrid:") for item in no_choice.artifact.options)


def test_independent_components_with_same_endpoints_compile_separately() -> None:
    routes = tuple(
        ParallelRoute(
            route_id=route_id,
            endpoints=("a", "b"),
            coordinates=((0, y), (1_000, y)),
            network_scope="urban",
        )
        for route_id, y in (
            ("a-west", 0),
            ("a-east", 300),
            ("b-west", 3_000),
            ("b-east", 3_300),
        )
    )

    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest(profile_id="parallel-independent-components", routes=routes)
    )

    assert len(result.scenario.candidate_sets) == 2
    assert len(result.artifact.decisions) == 2


def test_parallel_artifact_fingerprint_is_content_derived_and_order_stable() -> None:
    routes = (
        ParallelRoute(
            route_id="west",
            endpoints=("a", "b"),
            coordinates=((0, 0), (1_000, 0)),
            network_scope="urban",
        ),
        ParallelRoute(
            route_id="east",
            endpoints=("a", "b"),
            coordinates=((0, 300), (1_000, 300)),
            network_scope="urban",
        ),
    )

    first = compile_parallel_reduction_scenario(
        ParallelReductionRequest(profile_id="parallel-artifact-fingerprint", routes=routes)
    )
    second = compile_parallel_reduction_scenario(
        ParallelReductionRequest(
            profile_id="parallel-artifact-fingerprint", routes=tuple(reversed(routes))
        )
    )

    assert first.artifact.fingerprint
    assert first.artifact.fingerprint == second.artifact.fingerprint
    with pytest.raises(ValueError, match="fingerprint is stale"):
        ParallelReductionArtifact.model_validate(
            first.artifact.model_dump(mode="python") | {"fingerprint": "0" * 64}
        )
