from pathlib import Path

from shapely.geometry import LineString

from satn.section_population import (
    CapturedOutputAreaPopulation,
    PopulationDisplaySection,
    aggregate_section_population_selection,
)

PROJECT = Path(__file__).resolve().parents[1]


def test_review_map_has_grouped_alignment_evidence_panel_contract() -> None:
    script = (PROJECT / "src/satn/assets/review-map.js").read_text()
    stylesheet = (PROJECT / "src/satn/assets/review-map.css").read_text()

    assert "renderAlignmentComparison" in script
    assert "candidate_set_id" in script
    assert "radar" in script
    assert "officer-compiler-divergence" in script
    assert ".alignment-comparison" in stylesheet
    assert 'createElementNS("http://www.w3.org/2000/svg", name)' in script
    assert 'svgElement("polygon"' in script
    assert "commonAlignmentAxes" in script
    assert "togglePopulationSectionSelection" in script
    assert "captured_output_areas" in script


def test_spider_chart_keeps_unknown_values_out_of_normalisation() -> None:
    script = (PROJECT / "src/satn/assets/review-map.js").read_text()

    assert 'if (raw === null || raw === undefined || raw === "") return null;' in script
    assert "].filter(([, metric]) => metric !== null);" in script
    assert 'if (!comparison) {\n          cell.textContent = "Unknown";' in script
    assert '"Route length", "Elevation variation", "Maximum gradient"' in script
    assert "return minimum / metric" in script
    assert "return maximum > 0 ? metric / maximum : 1" in script


def test_selected_population_sections_deduplicate_shared_output_areas() -> None:
    shared = CapturedOutputAreaPopulation("oa-shared", 100, True)

    def section(section_id: str, extra: CapturedOutputAreaPopulation):
        records = (shared, extra)
        return PopulationDisplaySection(
            section_id=section_id,
            candidate_group_id="group",
            alignment_id="route",
            section_order=0,
            start_distance_m=0,
            end_distance_m=100,
            length_m=100,
            alignment_length_m=200,
            network_scope="urban",
            capture_radius_m=250,
            total_residents=sum(item.residents for item in records),
            inside_area_residents=sum(
                item.residents for item in records if item.is_inside_area
            ),
            outside_area_residents=sum(
                item.residents for item in records if not item.is_inside_area
            ),
            captured_oa_ids=tuple(item.oa_id for item in records),
            captured_output_areas=records,
            geometry=LineString([(0, 0), (100, 0)]),
        )

    summary = aggregate_section_population_selection(
        (
            section("one", CapturedOutputAreaPopulation("oa-inside", 200, True)),
            section("two", CapturedOutputAreaPopulation("oa-outside", 300, False)),
        )
    )

    assert summary.total_residents == 600
    assert summary.inside_area_residents == 300
    assert summary.outside_area_residents == 300
    assert summary.captured_oa_ids == ("oa-inside", "oa-outside", "oa-shared")
