from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pytest
from playwright.sync_api import sync_playwright
from shapely.geometry import LineString, Point, Polygon

from satn.agents import AgentRole, FakeAgentRuntime
from satn.compiler import compile_network
from satn.models import CouncilConfig, TrafficLight
from satn.publisher import publish

PROJECT = Path(__file__).parents[1]


def test_compile_selects_materially_easier_plausible_alignment() -> None:
    direct = LineString([(0, 0), (1000, 0)])
    easier = LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])
    source = _two_route_source(direct, easier)
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")

    compiled = compile_network(config, source, FakeAgentRuntime())

    connection = _authoritative_connection(compiled)
    assert connection.geometry.equals(easier)
    assert bool(connection["topography_alternative_trigger"])
    assert connection["topography_comparison_status"] == "easier-alternative-selected"
    assert connection["topography_original_role"] == "direct"
    assert connection["topography_selected_role"] == "low-traffic"
    assert "Steep" in connection["topography_comparison_rationale"]
    options = json.loads(connection["alignment_options"])
    selected = next(option for option in options if option["selected"])
    original = next(option for option in options if option["role"] == "direct")
    assert selected["role"] == "low-traffic"
    assert selected["topography"]["worst_direction_ascent_m"] == 0
    assert original["topography"]["trigger_reasons"]


def test_repeated_shorter_climbs_trigger_when_cumulative_ascent_is_material() -> None:
    direct = LineString([(0, 0), (1000, 0)])
    easier = LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])
    repeated_climbs = [(0, 0.0)]
    for climb in range(10):
        repeated_climbs.extend(
            [
                (climb * 80 + 40, 4.0),
                (climb * 80 + 80, 0.0),
            ]
        )
    repeated_climbs.extend([(900, 0.0), (1000, 0.0)])
    source = _two_route_source(direct, easier, direct_heights=repeated_climbs)
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")

    compiled = compile_network(config, source, FakeAgentRuntime())

    connection = _authoritative_connection(compiled)
    assert connection.geometry.equals(easier)
    assert bool(connection["topography_alternative_trigger"])
    assert "Repeated shorter climbs" in connection["topography_comparison_rationale"]
    original = next(
        option
        for option in json.loads(connection["alignment_options"])
        if option["role"] == "direct"
    )
    assert original["topography"]["worst_direction_ascent_m"] == 40


def test_governed_trigger_lengths_are_adjustable() -> None:
    direct = LineString([(0, 0), (1000, 0)])
    easier = LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])
    source = _two_route_source(direct, easier)
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    config.compilation.topography.very_steep_trigger_length_m = 600

    compiled = compile_network(config, source, FakeAgentRuntime())

    connection = _authoritative_connection(compiled)
    assert connection.geometry.equals(direct)
    assert not bool(connection["topography_alternative_trigger"])
    assert connection["topography_comparison_status"] == "not-triggered"


def test_triggered_original_remains_visibly_flagged_without_plausible_alternative() -> None:
    direct = LineString([(0, 0), (1000, 0)])
    easier = LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])
    source = _two_route_source(direct, easier)
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    config.compilation.topography.maximum_alternative_detour_ratio = 1.2

    compiled = compile_network(config, source, FakeAgentRuntime())

    connection = _authoritative_connection(compiled)
    assert connection.geometry.equals(direct)
    assert bool(connection["topography_alternative_trigger"])
    assert connection["topography_comparison_status"] == ("original-retained-no-easier-option")
    assert (
        "original remains selected and visibly flagged"
        in connection["topography_comparison_rationale"]
    )


def test_strategic_spine_is_retained_when_gradient_triggers_comparison() -> None:
    direct = LineString([(0, 0), (1000, 0)])
    easier = LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])
    source = _two_route_source(direct, easier, direct_ref="A1")
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")

    compiled = compile_network(config, source, FakeAgentRuntime())

    connection = _authoritative_connection(compiled)
    assert connection.geometry.equals(direct)
    assert connection["topography_selected_role"] == "strategic-spine"
    assert bool(connection["topography_alternative_trigger"])
    assert connection["topography_comparison_status"] == "strategic-spine-retained"
    assert "does not remove a strategic corridor" in connection["topography_comparison_rationale"]


def test_missing_elevation_keeps_original_and_makes_comparison_explicit() -> None:
    direct = LineString([(0, 0), (1000, 0)])
    easier = LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])
    source = _two_route_source(direct, easier)
    source.pop("elevation_evidence")
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")

    compiled = compile_network(config, source, FakeAgentRuntime())

    connection = _authoritative_connection(compiled)
    assert connection.geometry.equals(direct)
    assert not bool(connection["topography_alternative_trigger"])
    assert connection["topography_comparison_status"] == "evidence-unavailable"
    assert "explicitly unresolved" in connection["topography_comparison_rationale"]


@pytest.mark.parametrize(
    ("challenge_end_m", "challenge_height_m", "label"),
    [(100, 6.0, "Steep"), (50, 5.0, "Very Steep"), (30, 4.5, "Severe")],
)
def test_governed_section_length_thresholds_trigger_at_the_boundary(
    challenge_end_m: int,
    challenge_height_m: float,
    label: str,
) -> None:
    direct = LineString([(0, 0), (1000, 0)])
    easier = LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])
    direct_heights = [
        (0, 0.0),
        (challenge_end_m, challenge_height_m),
        (250, challenge_height_m),
        (500, challenge_height_m),
        (750, challenge_height_m),
        (1000, challenge_height_m),
    ]
    source = _two_route_source(
        direct,
        easier,
        direct_heights=direct_heights,
        alternative_heights=_gentle_alternative_heights(challenge_height_m),
    )
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")

    compiled = compile_network(config, source, FakeAgentRuntime())

    connection = _authoritative_connection(compiled)
    assert connection.geometry.equals(easier)
    assert label in connection["topography_comparison_rationale"]


def test_trigger_free_option_with_more_climbing_is_not_materially_easier() -> None:
    direct = LineString([(0, 0), (1000, 0)])
    alternative = LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])
    direct_heights = [(0, 0.0)]
    for hill in range(5):
        direct_heights.extend(
            [(hill * 100 + 30, 6.0), (hill * 100 + 60, 0.0), (hill * 100 + 100, 0.0)]
        )
    direct_heights.extend([(750, 0.0), (1000, 0.0)])
    alternative_heights = [
        (_alternative_point(distance), 4.9 if distance % 200 == 100 else 0.0)
        for distance in range(100, 1400, 100)
    ]
    source = _two_route_source(
        direct,
        alternative,
        direct_heights=direct_heights,
        alternative_heights=alternative_heights,
    )
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")

    compiled = compile_network(config, source, FakeAgentRuntime())

    connection = _authoritative_connection(compiled)
    assert connection.geometry.equals(direct)
    assert connection["topography_comparison_status"] == ("original-retained-no-easier-option")


def test_unidirectional_option_is_not_a_plausible_topography_alternative() -> None:
    direct = LineString([(0, 0), (1000, 0)])
    alternative = LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])
    source = _two_route_source(direct, alternative)
    source["places"].loc[source["places"]["place_id"] == "west", "place_id"] = "a-west"
    source["places"].loc[source["places"]["place_id"] == "east", "place_id"] = "z-east"
    quiet = source["network"]["osmid"].str.startswith("quiet-")
    for index, row in source["network"][quiet].iterrows():
        start, end = row.geometry.coords[0], row.geometry.coords[-1]
        source["network"].at[index, "u"] = _node_id(start)
        source["network"].at[index, "v"] = _node_id(end)

    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    compiled = compile_network(config, source, FakeAgentRuntime())

    connection = _authoritative_connection(compiled)
    assert connection.geometry.equals(direct)
    assert connection["topography_comparison_status"] == ("original-retained-no-easier-option")
    assert connection["topography_selected_role"] == "low-traffic"


def test_bounded_runtime_cannot_parameterise_authoritative_topography_selection() -> None:
    direct = LineString([(0, 0), (1000, 0)])
    easier = LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])
    source = _two_route_source(direct, easier)
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    runtime = FakeAgentRuntime()

    compiled = compile_network(config, source, runtime)

    connection = _authoritative_connection(compiled)
    assert connection.geometry.equals(easier)
    assert connection["topography_selected_role"] == "low-traffic"
    assert connection["topography_comparison_status"] == "easier-alternative-selected"
    selected = [
        option for option in json.loads(connection["alignment_options"]) if option["selected"]
    ]
    assert [option["role"] for option in selected] == ["low-traffic"]


def test_gate_rejection_publishes_no_authoritative_alignment_selection() -> None:
    direct = LineString([(0, 0), (1000, 0)])
    easier = LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])
    source = _two_route_source(direct, easier)
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    config.compilation.agent.response_mode = "direct-runtime"
    config.compilation.agent.review_statuses = (TrafficLight.GREEN,)
    runtime = FakeAgentRuntime(
        {AgentRole.DECISION: [{"request_id": "$request", "choice_id": "3"} for _ in range(12)]}
    )

    compiled = compile_network(config, source, runtime)

    gap = compiled.gaps.iloc[0]
    assert gap["topography_selected_role"] is None
    assert gap["topography_comparison_status"] == "gate-rejected-selection"
    assert not any(option["selected"] for option in json.loads(gap["alignment_options"]))


def test_physically_impracticable_a_road_is_not_a_topography_substitute() -> None:
    direct = LineString([(0, 0), (1000, 0)])
    alternative = LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])
    source = _two_route_source(direct, alternative)
    quiet = source["network"]["osmid"].str.startswith("quiet-")
    source["network"]["ref"] = source["network"]["ref"].astype(object)
    source["network"].loc[quiet, "highway"] = "primary"
    source["network"].loc[quiet, "ref"] = "A2"
    source["network"].loc[quiet, "satn_alongside"] = "impracticable"
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")

    compiled = compile_network(config, source, FakeAgentRuntime())

    connection = _authoritative_connection(compiled)
    assert connection.geometry.equals(direct)
    assert connection["topography_comparison_status"] == ("original-retained-no-easier-option")
    strategic = next(
        option
        for option in json.loads(connection["alignment_options"])
        if option["role"] == "strategic-spine"
    )
    assert strategic["impracticable_alongside"]
    assert not strategic["selected"]


@pytest.mark.browser
def test_retained_elevation_challenge_has_persistent_map_warning(tmp_path: Path) -> None:
    direct = LineString([(0, 0), (1000, 0)])
    alternative = LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])
    source = _two_route_source(direct, alternative)
    config = CouncilConfig.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    config.compilation.topography.maximum_alternative_detour_ratio = 1.2
    config.publication.output_dir = tmp_path / "published"
    compiled = compile_network(config, source, FakeAgentRuntime())
    artifacts = publish(config, compiled, "run-retained-topography")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
        page.goto(artifacts["review_map"].as_uri())
        page.wait_for_function("document.documentElement.dataset.mapReady === 'true'")

        legend = page.locator("#legend-gaps-warnings")
        assert legend.is_hidden()
        page.get_by_role("button", name="About route warnings").click()
        assert legend.is_visible()
        assert "Dashed amber marks a retained Elevation Challenge." in legend.inner_text()
        warning = page.locator(".connection.retained-topography")
        assert warning.count() == 1
        assert (
            warning.locator(".topography-warning").text_content() == "Elevation challenge retained"
        )
        page.locator("#layer-gaps-warnings").uncheck()
        page.wait_for_function(
            "() => ['crossing-warnings', 'spine-access-topography-warnings'].every(layer => "
            "window.SATN_REVIEW_MAP.getLayoutProperty(layer, 'visibility') === 'none')"
        )
        assert page.evaluate(
            "() => ['crossing-warnings', 'spine-access-topography-warnings'].every(layer => "
            "window.SATN_REVIEW_MAP.getLayoutProperty(layer, 'visibility') === 'none')"
        )
        assert legend.is_hidden()
        browser.close()


def _two_route_source(
    direct: LineString,
    easier: LineString,
    *,
    direct_heights: list[tuple[int, float]] | None = None,
    direct_ref: str | None = None,
    alternative_heights: list[tuple[Point, float]] | None = None,
) -> dict[str, gpd.GeoDataFrame]:
    places = gpd.GeoDataFrame(
        [
            {
                "place_id": "west",
                "name": "West",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(0, 0),
            },
            {
                "place_id": "east",
                "name": "East",
                "kind": "community",
                "place_class": "village",
                "geometry": Point(1000, 0),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    network = gpd.GeoDataFrame(
        [
            {
                "osmid": "direct-main-road",
                "highway": "secondary",
                "ref": direct_ref,
                "geometry": direct,
            },
            {
                "osmid": "quiet-west",
                "highway": "residential",
                "geometry": LineString([(0, 0), (0, 200)]),
            },
            {
                "osmid": "quiet-middle",
                "highway": "residential",
                "geometry": LineString([(0, 200), (1000, 200)]),
            },
            {
                "osmid": "quiet-east",
                "highway": "residential",
                "geometry": LineString([(1000, 200), (1000, 0)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    )
    direct_heights = direct_heights or [
        (0, 0),
        *[(x, 50 - abs(500 - x) / 10) for x in range(100, 1000, 100)],
        (1000, 0),
    ]
    alternative_heights = alternative_heights or [
        (Point(0, 100), 0),
        *[(Point(x, 200), 0) for x in range(0, 1001, 100)],
        (Point(1000, 100), 0),
    ]
    samples = [
        *[(Point(x, 0), height) for x, height in direct_heights],
        *alternative_heights,
    ]
    elevation = gpd.GeoDataFrame(
        [
            {
                "evidence_id": f"terrain-{index}",
                "source_id": "governed-terrain",
                "elevation_m": height,
                "geometry": point,
            }
            for index, (point, height) in enumerate(samples)
        ],
        geometry="geometry",
        crs=27700,
    )
    return {
        "boundary": gpd.GeoDataFrame(
            geometry=[Polygon([(-50, -50), (1050, -50), (1050, 250), (-50, 250)])],
            crs=27700,
        ),
        "places": places,
        "network": network,
        "context": gpd.GeoDataFrame(
            [
                {
                    "evidence_id": "west-spine",
                    "feature_type": "a-road-spine",
                    "name": "A1",
                    "category": "A-road strategic spine",
                    "source_id": "west-spine-source",
                    "feature_count": 1,
                    "network_scope": "rural",
                    "geometry": LineString([(0, 0), (0, 10)]),
                }
            ],
            geometry="geometry",
            crs=27700,
        ),
        "elevation_evidence": elevation,
    }


def _authoritative_connection(compiled: object) -> object:
    return compiled.spine_access_connections[
        compiled.spine_access_connections["place_name"] == "East"
    ].iloc[0]


def _gentle_alternative_heights(end_height_m: float) -> list[tuple[Point, float]]:
    samples: list[tuple[Point, float]] = [(Point(0, 100), end_height_m / 14)]
    samples.extend((Point(x, 200), end_height_m * (200 + x) / 1400) for x in range(0, 1001, 100))
    samples.append((Point(1000, 100), end_height_m * 1300 / 1400))
    return samples


def _alternative_point(distance_m: int) -> Point:
    if distance_m <= 200:
        return Point(0, distance_m)
    if distance_m <= 1200:
        return Point(distance_m - 200, 200)
    return Point(1000, 1400 - distance_m)


def _node_id(coordinate: tuple[float, ...]) -> str:
    return f"xy:{coordinate[0]:.7f}:{coordinate[1]:.7f}"
