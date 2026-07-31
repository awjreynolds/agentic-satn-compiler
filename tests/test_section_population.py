from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, box

from satn.section_population import (
    SectionPopulationProfile,
    SectionPopulationValidationError,
    compile_section_population_capture,
    derive_material_population_differences,
)


def _output_areas() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "OA21CD": ["E00000001", "E00000002", "E00000003"],
            "usual_residents": [300, 700, 500],
            "population_weighted_centroid": [
                Point(50, 200),
                Point(250, 700),
                Point(450, 100),
            ],
            "geometry": [
                box(0, 150, 100, 250),
                box(200, 650, 300, 750),
                box(400, 50, 500, 150),
            ],
        },
        geometry="geometry",
        crs="EPSG:27700",
    )


def test_compiles_scope_sensitive_100m_sections_and_cross_boundary_counts() -> None:
    alignments = gpd.GeoDataFrame(
        {
            "candidate_group_id": ["choice-1"],
            "alignment_id": ["candidate-a"],
            "geometry": [LineString([(0, 0), (300, 0)])],
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    area = gpd.GeoDataFrame(
        {"geometry": [box(-100, -100, 180, 800)]},
        geometry="geometry",
        crs="EPSG:27700",
    )
    urban_extent = gpd.GeoDataFrame(
        {"geometry": [box(-100, -100, 150, 800)]},
        geometry="geometry",
        crs="EPSG:27700",
    )

    assessment = compile_section_population_capture(
        alignments,
        _output_areas(),
        area,
        urban_extent=urban_extent,
        source_content_sha256="a" * 64,
    )

    assert [
        (item.start_distance_m, item.end_distance_m, item.network_scope)
        for item in assessment.sections
    ] == [
        (0.0, 100.0, "urban"),
        (100.0, 150.0, "urban"),
        (150.0, 250.0, "rural"),
        (250.0, 300.0, "rural"),
    ]
    assert [item.capture_radius_m for item in assessment.sections] == [
        250.0,
        250.0,
        750.0,
        750.0,
    ]
    assert assessment.sections[0].total_residents == 300
    assert assessment.sections[0].inside_area_residents == 300
    assert assessment.sections[0].outside_area_residents == 0
    assert assessment.sections[-1].total_residents == 1500
    assert assessment.sections[-1].inside_area_residents == 300
    assert assessment.sections[-1].outside_area_residents == 1200
    assert assessment.sections[-1].captured_oa_ids == (
        "E00000001",
        "E00000002",
        "E00000003",
    )


def test_section_identity_is_order_independent_and_profile_is_explicit() -> None:
    alignments = gpd.GeoDataFrame(
        {
            "candidate_group_id": ["choice-1", "choice-1"],
            "alignment_id": ["candidate-a", "candidate-b"],
            "geometry": [
                LineString([(0, 0), (200, 0)]),
                LineString([(0, 100), (200, 100)]),
            ],
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    area = gpd.GeoDataFrame(
        {"geometry": [box(-100, -100, 400, 800)]},
        geometry="geometry",
        crs="EPSG:27700",
    )
    urban = gpd.GeoDataFrame(
        {"geometry": [box(-100, -100, 400, 800)]},
        geometry="geometry",
        crs="EPSG:27700",
    )
    profile = SectionPopulationProfile(
        display_section_length_m=100,
        urban_capture_radius_m=250,
        rural_capture_radius_m=750,
    )

    original = compile_section_population_capture(
        alignments,
        _output_areas(),
        area,
        urban_extent=urban,
        source_content_sha256="b" * 64,
        profile=profile,
    )
    shuffled = compile_section_population_capture(
        alignments.iloc[::-1],
        _output_areas().iloc[::-1],
        area,
        urban_extent=urban,
        source_content_sha256="b" * 64,
        profile=profile,
    )

    assert original.assessment_id == shuffled.assessment_id
    assert original.canonical() == shuffled.canonical()
    assert original.profile.canonical()["urban_capture_radius_m"] == 250.0
    assert original.profile.canonical()["rural_capture_radius_m"] == 750.0


def test_material_population_difference_requires_absolute_relative_and_persistence() -> None:
    alignments = gpd.GeoDataFrame(
        {
            "candidate_group_id": ["choice-1", "choice-1"],
            "alignment_id": ["candidate-a", "candidate-b"],
            "geometry": [
                LineString([(0, 0), (600, 0)]),
                LineString([(0, 1000), (600, 1000)]),
            ],
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    output_areas = gpd.GeoDataFrame(
        {
            "OA21CD": ["E00000001", "E00000002"],
            "usual_residents": [1500, 1000],
            "population_weighted_centroid": [Point(300, 0), Point(300, 1000)],
            "geometry": [box(250, -50, 350, 50), box(250, 950, 350, 1050)],
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    area = gpd.GeoDataFrame(
        {"geometry": [box(-100, -100, 700, 1100)]},
        geometry="geometry",
        crs="EPSG:27700",
    )
    rural = gpd.GeoDataFrame(
        {"geometry": []},
        geometry="geometry",
        crs="EPSG:27700",
    )
    assessment = compile_section_population_capture(
        alignments,
        output_areas,
        area,
        urban_extent=rural,
        source_content_sha256="c" * 64,
    )

    differences = derive_material_population_differences(assessment)

    assert len(differences) == 1
    difference = differences[0]
    assert difference.advantaged_alignment_id == "candidate-a"
    assert difference.compared_alignment_id == "candidate-b"
    assert difference.corridor_length_m == 600.0
    assert difference.minimum_absolute_difference_residents == 500
    assert difference.minimum_relative_difference_pct == 50.0


def test_short_population_spike_is_visible_but_not_material() -> None:
    alignments = gpd.GeoDataFrame(
        {
            "candidate_group_id": ["choice-1", "choice-1"],
            "alignment_id": ["candidate-a", "candidate-b"],
            "geometry": [
                LineString([(0, 0), (400, 0)]),
                LineString([(0, 1000), (400, 1000)]),
            ],
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    output_areas = gpd.GeoDataFrame(
        {
            "OA21CD": ["E00000001"],
            "usual_residents": [1000],
            "population_weighted_centroid": [Point(50, 0)],
            "geometry": [box(0, -50, 100, 50)],
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    area = gpd.GeoDataFrame(
        {"geometry": [box(-100, -100, 500, 1100)]},
        geometry="geometry",
        crs="EPSG:27700",
    )
    assessment = compile_section_population_capture(
        alignments,
        output_areas,
        area,
        urban_extent=gpd.GeoDataFrame(
            {"geometry": []}, geometry="geometry", crs="EPSG:27700"
        ),
        source_content_sha256="d" * 64,
    )

    assert any(
        item.alignment_id == "candidate-a" and item.total_residents == 1000
        for item in assessment.sections
    )
    assert derive_material_population_differences(assessment) == ()


def test_profile_cap_and_input_geometry_contract_are_enforced() -> None:
    with pytest.raises(SectionPopulationValidationError, match="1 km maximum"):
        SectionPopulationProfile(display_section_length_m=1_000.001)

    alignments = gpd.GeoDataFrame(
        {
            "candidate_group_id": ["choice-1"],
            "alignment_id": ["candidate-a"],
            "geometry": [LineString([(0, 0), (100, 0)])],
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    invalid_areas = _output_areas().set_geometry(
        gpd.GeoSeries(
            [Point(50, 200), Point(250, 700), Point(450, 100)],
            crs="EPSG:27700",
        )
    )
    area = gpd.GeoDataFrame(
        {"geometry": [box(-100, -100, 200, 200)]},
        geometry="geometry",
        crs="EPSG:27700",
    )

    with pytest.raises(SectionPopulationValidationError, match="output areas"):
        compile_section_population_capture(
            alignments,
            invalid_areas,
            area,
            urban_extent=gpd.GeoDataFrame(
                {"geometry": []}, geometry="geometry", crs="EPSG:27700"
            ),
            source_content_sha256="e" * 64,
        )
