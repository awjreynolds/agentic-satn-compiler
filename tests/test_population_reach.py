"""Tests for the standalone Population Reach v1 evidence compiler."""

from __future__ import annotations

import json
import math
from dataclasses import replace

import geopandas as gpd
import pytest
from shapely.affinity import translate
from shapely.geometry import LineString, Point, Polygon

import satn.population_reach as population_reach
from satn.population_reach import (
    CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
    CURRENT_DEVELOPMENT_EVIDENCE_MISSING,
    CURRENT_DEVELOPMENT_MATERIAL_OMISSION_INCORPORATED,
    CURRENT_DEVELOPMENT_MATERIAL_OMISSION_OUTSTANDING,
    CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
    ONS_2021_OA_GEOGRAPHY,
    CurrentDevelopmentEvidence,
    PopulationReachColumns,
    PopulationReachProfile,
    PopulationReachSource,
    PopulationReachValidationError,
    compile_population_reach,
)


def _inputs() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    routes = gpd.GeoDataFrame(
        {
            "option_id": ["alpha", "beta", "alpha"],
            "geometry": [
                LineString([(-200, 0), (2000, 0)]),
                LineString([(-200, 900), (2000, 900)]),
                # A second geometry verifies that one option is dissolved before measuring.
                LineString([(2000, 0), (2200, 0)]),
            ],
        },
        crs="EPSG:27700",
    )
    areas = gpd.GeoDataFrame(
        {
            "OA21CD": ["E00000004", "E00000002", "E00000003", "E00000001", "E00000005"],
            "usual_residents": [70, 200, 50, 100, 80],
            "population_weighted_centroid": [
                Point(0, -520),
                Point(1000, 490),
                Point(0, 700),
                Point(0, 100),
                Point(0, 1500),
            ],
            "geometry": [
                _square(0, -520),
                _square(1000, 490),
                _square(0, 700),
                _square(0, 100),
                _square(0, 1500),
            ],
        },
        crs="EPSG:27700",
    )
    area_definition = gpd.GeoDataFrame(
        {"geometry": [Polygon([(-100, -100), (2100, -100), (2100, 600), (-100, 600)])]},
        crs="EPSG:27700",
    )
    return routes, areas, area_definition


def _square(x: float, y: float) -> Polygon:
    return Polygon([(x - 10, y - 10), (x + 10, y - 10), (x + 10, y + 10), (x - 10, y + 10)])


def _at_real_bng_location(
    routes: gpd.GeoDataFrame, areas: gpd.GeoDataFrame, area_definition: gpd.GeoDataFrame
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Move the synthetic fixture into the UK before WGS84 transformation."""

    offset = {"xoff": 400_000, "yoff": 150_000}
    translated_routes = routes.copy()
    translated_routes["geometry"] = routes.geometry.map(
        lambda geometry: translate(geometry, **offset)
    )
    translated_areas = areas.copy()
    translated_areas["geometry"] = areas.geometry.map(
        lambda geometry: translate(geometry, **offset)
    )
    translated_areas["population_weighted_centroid"] = areas[
        "population_weighted_centroid"
    ].map(lambda geometry: translate(geometry, **offset))
    translated_definition = area_definition.copy()
    translated_definition["geometry"] = area_definition.geometry.map(
        lambda geometry: translate(geometry, **offset)
    )
    return translated_routes, translated_areas, translated_definition


def _source() -> PopulationReachSource:
    return PopulationReachSource(
        source_id="ons-census-2021-oa-usual-residents",
        release="Census 2021 OA population-weighted centroids",
        effective_date="2021-03-21",
        licence="Open Government Licence v3.0",
        source_uri="https://www.ons.gov.uk/census",
        version="census-2021-v1",
        content_sha256="a" * 64,
        permitted_uses=("strategic-corridor-analysis",),
        known_limitations=("Whole OA population is assigned to its PWC.",),
        transformation_lineage=("Joined ONS OA boundaries, PWC and usual residents by OA21CD.",),
    )


def test_compiles_canonical_whole_oa_population_reach_with_shared_and_exclusive_counts() -> None:
    routes, areas, area_definition = _inputs()

    assessment = compile_population_reach(routes, areas, area_definition, source=_source())

    assert assessment.assessment_id.startswith("population-reach-v1-")
    assert len(assessment.records) == 20  # 2 options x 2 reporting distances x 5 OAs
    assert [
        (summary.corridor_distance_m, summary.option_id) for summary in assessment.summaries
    ] == [
        (500.0, "alpha"),
        (500.0, "beta"),
        (1000.0, "alpha"),
        (1000.0, "beta"),
    ]

    at_500 = {
        (summary.option_id, summary.corridor_distance_m): summary
        for summary in assessment.summaries
    }
    alpha = at_500[("alpha", 500.0)]
    beta = at_500[("beta", 500.0)]
    assert (alpha.total_residents, alpha.inside_area_residents, alpha.outside_area_residents) == (
        300,
        300,
        0,
    )
    assert alpha.shared_oa_ids == ("E00000002",)
    assert alpha.option_exclusive_oa_ids == ("E00000001",)
    assert (alpha.shared_residents, alpha.option_exclusive_residents) == (200, 100)
    assert (beta.total_residents, beta.inside_area_residents, beta.outside_area_residents) == (
        250,
        200,
        50,
    )
    assert beta.shared_oa_ids == ("E00000002",)
    assert beta.option_exclusive_oa_ids == ("E00000003",)

    near_boundary = next(
        record
        for record in assessment.records
        if (record.option_id, record.corridor_distance_m, record.oa_id)
        == ("alpha", 500.0, "E00000004")
    )
    assert not near_boundary.captured
    assert near_boundary.distance_to_route_m == 520.0
    assert near_boundary.distance_to_corridor_boundary_m == 20.0
    assert near_boundary.borderline


def test_sensitivity_reports_borderline_dominance_and_ordering_flip() -> None:
    routes, areas, area_definition = _inputs()

    assessment = compile_population_reach(
        routes,
        areas,
        area_definition,
        source=_source(),
        profile=PopulationReachProfile(comparison_tolerance_residents=40),
    )

    at_500, at_1000 = assessment.sensitivities
    assert at_500.option_ranking == ("alpha", "beta")
    assert at_500.margin_to_next_residents == 50
    assert not at_500.within_tolerance
    assert not at_500.margin_dominated_by_borderline_oa
    assert at_500.sensitive  # Missing current-development evidence is material sensitivity.
    assert at_500.borderline_oa_ids == ("E00000002", "E00000004")
    assert at_500.individually_decisive_borderline_oa_ids == ()
    assert at_1000.option_ranking == ("beta", "alpha")
    assert at_1000.ordering_flips_from_first_distance
    assert at_1000.sensitive


def test_canonical_assessment_is_independent_of_input_row_order() -> None:
    routes, areas, area_definition = _inputs()

    original = compile_population_reach(routes, areas, area_definition, source=_source())
    shuffled = compile_population_reach(
        routes.sample(frac=1, random_state=3),
        areas.sample(frac=1, random_state=4),
        area_definition,
        source=_source(),
    )

    assert shuffled.assessment_id == original.assessment_id
    assert shuffled.canonical() == original.canonical()


def test_custom_columns_are_supported_with_fixed_v1_reporting_distances() -> None:
    routes, areas, area_definition = _inputs()
    columns = PopulationReachColumns(
        oa_id="oa", usual_residents="residents", population_weighted_centroid="pwc", option_id="id"
    )
    routes = routes.rename(columns={"option_id": "id"})
    areas = areas.rename(
        columns={
            "OA21CD": "oa",
            "usual_residents": "residents",
            "population_weighted_centroid": "pwc",
        }
    )

    assessment = compile_population_reach(
        routes,
        areas,
        area_definition,
        source=_source(),
        columns=columns,
    )

    assert [summary.corridor_distance_m for summary in assessment.summaries] == [
        500.0,
        500.0,
        1000.0,
        1000.0,
    ]


@pytest.mark.parametrize(
    "distances",
    (
        (250,),
        (500,),
        (1000,),
        (1000, 500),
        (500, 1000, 1500),
    ),
)
def test_population_reach_v1_requires_headline_500m_and_sensitivity_1000m(
    distances: tuple[float, ...],
) -> None:
    with pytest.raises(
        PopulationReachValidationError,
        match="headline 500 m and sensitivity 1000 m",
    ):
        PopulationReachProfile(corridor_distances_m=distances)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            {"source_id": "not-2021", "geography": "ons-2011-output-area"},
            "ONS 2021 Output Areas",
        ),
        (
            {"source_id": "not-whole-oa", "population_measure": "modelled-demand"},
            "whole-OA usual-resident",
        ),
        (
            {"source_id": "not-pwc", "centroid_measure": "geometric-centroid"},
            "population-weighted",
        ),
    ],
)
def test_rejects_ungoverned_or_wrong_source_metadata(source: dict[str, str], message: str) -> None:
    with pytest.raises(PopulationReachValidationError, match=message):
        PopulationReachSource(**(_source().canonical() | source))


def test_rejects_missing_schema_columns_and_missing_crs() -> None:
    routes, areas, area_definition = _inputs()

    with pytest.raises(PopulationReachValidationError, match="missing required columns"):
        compile_population_reach(
            routes,
            areas.drop(columns="population_weighted_centroid"),
            area_definition,
            source=_source(),
        )
    without_crs = routes.copy()
    without_crs.crs = None
    with pytest.raises(PopulationReachValidationError, match="must declare a CRS"):
        compile_population_reach(without_crs, areas, area_definition, source=_source())


def test_rejects_invalid_oa_values_and_geometry_schema() -> None:
    routes, areas, area_definition = _inputs()
    areas["usual_residents"] = areas["usual_residents"].astype(float)
    areas.loc[0, "usual_residents"] = 1.5
    with pytest.raises(PopulationReachValidationError, match="whole non-negative"):
        compile_population_reach(routes, areas, area_definition, source=_source())

    invalid_routes = routes.copy()
    invalid_routes.loc[0, "geometry"] = Point(0, 0)
    with pytest.raises(PopulationReachValidationError, match="line geometries"):
        compile_population_reach(invalid_routes, areas, area_definition, source=_source())


@pytest.mark.parametrize(
    ("option_ids", "message"),
    (
        (["alpha", 7, "alpha"], "strict non-blank strings"),
        ([7, "beta", 3], "strict non-blank strings"),
        (["alpha", " \t", "alpha"], "strict non-blank strings"),
        ([" alpha ", "beta", "gamma"], "canonical strings"),
        (["alpha", " alpha ", "beta"], "collide after whitespace canonicalisation"),
    ),
)
def test_rejects_malformed_mixed_and_whitespace_route_option_ids_before_sorting(
    option_ids: list[object],
    message: str,
) -> None:
    routes, areas, area_definition = _inputs()
    routes["option_id"] = option_ids

    with pytest.raises(PopulationReachValidationError, match=message):
        compile_population_reach(
            routes,
            areas,
            area_definition,
            source=_source(),
        )


def test_rejects_invalid_and_degenerate_route_oa_and_area_geometries() -> None:
    routes, areas, area_definition = _inputs()

    zero_length_routes = routes.copy()
    zero_length_routes.at[0, "geometry"] = LineString([(0, 0), (0, 0)])
    with pytest.raises(
        PopulationReachValidationError,
        match="valid positive-length line geometries",
    ):
        compile_population_reach(
            zero_length_routes,
            areas,
            area_definition,
            source=_source(),
        )

    self_intersecting = Polygon([(0, 0), (20, 20), (0, 20), (20, 0), (0, 0)])
    invalid_areas = areas.copy()
    invalid_areas.at[0, "geometry"] = self_intersecting
    with pytest.raises(
        PopulationReachValidationError,
        match="valid positive-area polygon geometries",
    ):
        compile_population_reach(
            routes,
            invalid_areas,
            area_definition,
            source=_source(),
        )

    invalid_definition = area_definition.copy()
    invalid_definition.at[0, "geometry"] = self_intersecting
    with pytest.raises(
        PopulationReachValidationError,
        match="valid positive-area polygon geometries",
    ):
        compile_population_reach(
            routes,
            areas,
            invalid_definition,
            source=_source(),
        )


def test_source_governance_constant_is_explicit() -> None:
    assert _source().geography == ONS_2021_OA_GEOGRAPHY


def test_governance_bindings_claim_limits_and_current_development_warning_are_canonical() -> None:
    routes, areas, area_definition = _inputs()

    assessment = compile_population_reach(routes, areas, area_definition, source=_source())

    assert assessment.source.canonical()["effective_date"] == "2021-03-21"
    assert assessment.source.canonical()["licence"] == "Open Government Licence v3.0"
    assert assessment.source.canonical()["source_uri"] == "https://www.ons.gov.uk/census"
    assert assessment.source.canonical()["version"] == "census-2021-v1"
    assert assessment.source.canonical()["content_sha256"] == "a" * 64
    assert assessment.area_definition_sha256
    assert all(
        option.geometry_sha256 and option.length_m > 0 for option in assessment.option_geometries
    )
    assert assessment.warnings == (
        "No governed current-development evidence was supplied; population reach excludes "
        "current-development residents.",
    )
    assert any("not travel demand" in claim for claim in assessment.prohibited_claims)
    assert all(
        sensitivity.missing_current_development_evidence
        for sensitivity in assessment.sensitivities
    )
    assert all(sensitivity.sensitive for sensitivity in assessment.sensitivities)


def test_reprojects_secondary_population_weighted_centroid_geometry_with_oa_crs() -> None:
    routes, areas, area_definition = _inputs()
    routes, areas, area_definition = _at_real_bng_location(routes, areas, area_definition)
    wgs_routes = routes.to_crs("EPSG:4326")
    wgs_areas = areas.to_crs("EPSG:4326")
    wgs_areas["population_weighted_centroid"] = list(
        gpd.GeoSeries(areas["population_weighted_centroid"], crs=areas.crs).to_crs("EPSG:4326")
    )
    wgs_area_definition = area_definition.to_crs("EPSG:4326")

    bng = compile_population_reach(routes, areas, area_definition, source=_source())
    wgs = compile_population_reach(
        wgs_routes, wgs_areas, wgs_area_definition, source=_source()
    )

    assert [
        (record.option_id, record.corridor_distance_m, record.oa_id, record.captured)
        for record in wgs.records
    ] == [
        (record.option_id, record.corridor_distance_m, record.oa_id, record.captured)
        for record in bng.records
    ]
    assert [summary.canonical() for summary in wgs.summaries] == [
        summary.canonical() for summary in bng.summaries
    ]
    assert wgs.assessment_id == bng.assessment_id
    assert wgs.area_definition_sha256 == bng.area_definition_sha256
    assert wgs.option_geometries == bng.option_geometries
    assert wgs.coordinate_transformation_lineage[1].startswith(
        "Population-weighted centroids are separately transformed"
    )


def test_rejects_trimmed_duplicate_oa_ids_and_assessment_records_are_unique() -> None:
    routes, areas, area_definition = _inputs()
    areas.loc[1, "OA21CD"] = f" {areas.loc[0, 'OA21CD']} "

    with pytest.raises(PopulationReachValidationError, match="canonical OA IDs must be unique"):
        compile_population_reach(routes, areas, area_definition, source=_source())

    _, areas, area_definition = _inputs()
    assessment = compile_population_reach(routes, areas, area_definition, source=_source())
    keys = {
        (record.option_id, record.corridor_distance_m, record.oa_id)
        for record in assessment.records
    }
    assert len(keys) == len(assessment.records)


def test_route_geometry_changes_assessment_identity_without_changing_captured_oas() -> None:
    routes, areas, area_definition = _inputs()
    changed_routes = routes.copy()
    changed_routes.loc[2, "geometry"] = LineString([(2000, 0), (2400, 0)])

    original = compile_population_reach(routes, areas, area_definition, source=_source())
    changed = compile_population_reach(changed_routes, areas, area_definition, source=_source())

    assert [summary.canonical() for summary in changed.summaries] == [
        summary.canonical() for summary in original.summaries
    ]
    assert changed.assessment_id != original.assessment_id
    assert changed.option_geometries != original.option_geometries


def test_percent_near_equivalence_and_one_individually_decisive_borderline_oa() -> None:
    routes = gpd.GeoDataFrame(
        {
            "option_id": ["alpha", "beta"],
            "geometry": [
                LineString([(-100, 0), (500, 0)]),
                LineString([(-100, 1000), (500, 1000)]),
            ],
        },
        crs="EPSG:27700",
    )
    oas = gpd.GeoDataFrame(
        {
            "OA21CD": ["E1", "E2", "E3", "E4"],
            "usual_residents": [1000, 900, 60, 60],
            "population_weighted_centroid": [
                Point(200, 0), Point(200, 1000), Point(0, 490), Point(0, 510)
            ],
            "geometry": [_square(200, 0), _square(200, 1000), _square(0, 490), _square(0, 510)],
        },
        crs="EPSG:27700",
    )
    area_definition = gpd.GeoDataFrame(
        {"geometry": [Polygon([(-200, -200), (600, -200), (600, 1200), (-200, 1200)])]},
        crs="EPSG:27700",
    )

    assessment = compile_population_reach(
        routes,
        oas,
        area_definition,
        source=_source(),
        profile=PopulationReachProfile(comparison_tolerance_percent=10),
    )

    sensitivity = assessment.sensitivities[0]
    assert sensitivity.margin_to_next_residents == 100
    assert sensitivity.within_tolerance  # 100 is within 10% of the 1,060 leading total.
    assert sensitivity.borderline_oa_ids == ("E3", "E4")
    assert not sensitivity.margin_dominated_by_borderline_oa
    assert sensitivity.individually_decisive_borderline_oa_ids == ()

    decisive_oas = oas.drop(index=3).copy()
    decisive_oas.loc[1, "usual_residents"] = 1000
    decisive_oas.loc[2, "usual_residents"] = 100
    decisive = compile_population_reach(
        routes,
        decisive_oas,
        area_definition,
        source=_source(),
        profile=PopulationReachProfile(),
    ).sensitivities[0]
    assert decisive.margin_to_next_residents == 100
    assert decisive.margin_dominated_by_borderline_oa
    assert decisive.individually_decisive_borderline_oa_ids == ("E3",)


def test_zero_resident_borderline_oa_is_never_decisive_when_totals_are_tied() -> None:
    routes = gpd.GeoDataFrame(
        {
            "option_id": ["alpha", "beta"],
            "geometry": [
                LineString([(-100, 0), (500, 0)]),
                LineString([(-100, 1000), (500, 1000)]),
            ],
        },
        crs="EPSG:27700",
    )
    areas = gpd.GeoDataFrame(
        {
            "OA21CD": ["zero-borderline"],
            "usual_residents": [0],
            "population_weighted_centroid": [Point(200, 490)],
            "geometry": [_square(200, 490)],
        },
        crs="EPSG:27700",
    )
    area_definition = gpd.GeoDataFrame(
        {
            "geometry": [
                Polygon([(-200, -100), (600, -100), (600, 1100), (-200, 1100)])
            ]
        },
        crs="EPSG:27700",
    )

    sensitivity = compile_population_reach(
        routes,
        areas,
        area_definition,
        source=_source(),
    ).sensitivities[0]

    assert sensitivity.option_ranking == ("alpha", "beta")
    assert sensitivity.margin_to_next_residents == 0
    assert sensitivity.within_tolerance
    assert sensitivity.borderline_oa_ids == ("zero-borderline",)
    assert not sensitivity.margin_dominated_by_borderline_oa
    assert sensitivity.individually_decisive_borderline_oa_ids == ()


def test_governed_source_requires_content_hash_even_when_a_uri_is_supplied() -> None:
    source = _source().canonical() | {"content_sha256": None}

    with pytest.raises(PopulationReachValidationError, match="requires a content SHA-256"):
        PopulationReachSource(**source)


def test_opaque_current_development_id_cannot_suppress_missing_evidence_sensitivity() -> None:
    routes, areas, area_definition = _inputs()
    opaque = replace(_source(), current_development_evidence_id="opaque-development-record")

    assessment = compile_population_reach(routes, areas, area_definition, source=opaque)

    assert assessment.warnings
    assert all(
        sensitivity.missing_current_development_evidence
        for sensitivity in assessment.sensitivities
    )
    assert all(sensitivity.sensitive for sensitivity in assessment.sensitivities)

    available = replace(
        _source(),
        current_development_evidence=CurrentDevelopmentEvidence(
            source_id="banes-current-development",
            release="Current development schedule",
            effective_date="2026-07-01",
            licence="Open Government Licence v3.0",
            content_sha256="b" * 64,
            availability=CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
            conclusion=CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
        ),
    )
    covered = compile_population_reach(routes, areas, area_definition, source=available)
    assert covered.warnings == ()
    assert not any(
        sensitivity.missing_current_development_evidence for sensitivity in covered.sensitivities
    )

    missing = replace(
        _source(),
        current_development_evidence=CurrentDevelopmentEvidence(
            source_id="banes-current-development",
            release="Current development schedule",
            effective_date="2026-07-01",
            licence="Open Government Licence v3.0",
            content_sha256="c" * 64,
            availability=CURRENT_DEVELOPMENT_EVIDENCE_MISSING,
            conclusion=CURRENT_DEVELOPMENT_MATERIAL_OMISSION_OUTSTANDING,
        ),
    )
    explicit_missing = compile_population_reach(routes, areas, area_definition, source=missing)
    assert explicit_missing.warnings
    assert all(
        sensitivity.missing_current_development_evidence
        for sensitivity in explicit_missing.sensitivities
    )


def test_current_development_availability_cannot_clear_an_outstanding_omission() -> None:
    routes, areas, area_definition = _inputs()
    evidence = CurrentDevelopmentEvidence(
        source_id="banes-current-development",
        release="Current development schedule",
        effective_date="2026-07-01",
        licence="Open Government Licence v3.0",
        content_sha256="b" * 64,
        availability=CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
        conclusion=CURRENT_DEVELOPMENT_MATERIAL_OMISSION_OUTSTANDING,
    )

    outstanding = compile_population_reach(
        routes,
        areas,
        area_definition,
        source=replace(_source(), current_development_evidence=evidence),
    )
    assert outstanding.warnings
    assert all(
        sensitivity.missing_current_development_evidence
        for sensitivity in outstanding.sensitivities
    )

    for conclusion in (
        CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
        CURRENT_DEVELOPMENT_MATERIAL_OMISSION_INCORPORATED,
    ):
        cleared = compile_population_reach(
            routes,
            areas,
            area_definition,
            source=replace(
                _source(),
                current_development_evidence=replace(evidence, conclusion=conclusion),
            ),
        )
        assert cleared.warnings == ()
        assert not any(
            sensitivity.missing_current_development_evidence
            for sensitivity in cleared.sensitivities
        )


@pytest.mark.parametrize(
    "field", ("source_id", "release", "licence", "source_uri", "version")
)
@pytest.mark.parametrize("invalid", (None, 7, " \t"))
def test_rejects_non_string_or_blank_mandatory_population_source_metadata(
    field: str, invalid: object
) -> None:
    with pytest.raises(PopulationReachValidationError, match="must be a non-blank string"):
        PopulationReachSource(**(_source().canonical() | {field: invalid}))


@pytest.mark.parametrize("field", ("source_id", "release", "licence"))
@pytest.mark.parametrize("invalid", (None, 7, " \t"))
def test_rejects_non_string_or_blank_mandatory_current_development_metadata(
    field: str, invalid: object
) -> None:
    evidence = {
        "source_id": "banes-current-development",
        "release": "Current development schedule",
        "effective_date": "2026-07-01",
        "licence": "Open Government Licence v3.0",
        "content_sha256": "b" * 64,
        "availability": CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
        "conclusion": CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
    }
    with pytest.raises(PopulationReachValidationError, match="must be a non-blank string"):
        CurrentDevelopmentEvidence(**(evidence | {field: invalid}))


@pytest.mark.parametrize("invalid", (None, 7, " \t"))
def test_rejects_non_string_or_blank_governed_hashes(invalid: object) -> None:
    with pytest.raises(PopulationReachValidationError, match="SHA-256"):
        PopulationReachSource(**(_source().canonical() | {"content_sha256": invalid}))

    evidence = {
        "source_id": "banes-current-development",
        "release": "Current development schedule",
        "effective_date": "2026-07-01",
        "licence": "Open Government Licence v3.0",
        "content_sha256": invalid,
        "availability": CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
        "conclusion": CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
    }
    with pytest.raises(PopulationReachValidationError, match="SHA-256"):
        CurrentDevelopmentEvidence(**evidence)


@pytest.mark.parametrize("invalid", (None, 7, "", "not-a-conclusion"))
def test_rejects_untyped_or_unknown_current_development_conclusion(invalid: object) -> None:
    evidence = {
        "source_id": "banes-current-development",
        "release": "Current development schedule",
        "effective_date": "2026-07-01",
        "licence": "Open Government Licence v3.0",
        "content_sha256": "b" * 64,
        "availability": CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
        "conclusion": invalid,
    }
    with pytest.raises(PopulationReachValidationError, match="governed omission conclusion"):
        CurrentDevelopmentEvidence(**evidence)


@pytest.mark.parametrize("invalid", (True, False, 0.0, 1.0, float("nan"), float("inf"), None))
def test_rejects_non_integral_population_comparison_tolerance(invalid: object) -> None:
    with pytest.raises(
        PopulationReachValidationError,
        match="comparison tolerance residents must be a non-negative",
    ):
        PopulationReachProfile(comparison_tolerance_residents=invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "invalid",
    (True, None, "10", float("nan"), float("inf"), float("-inf")),
)
@pytest.mark.parametrize(
    "field",
    ("comparison_tolerance_percent", "borderline_distance_tolerance_m"),
)
def test_rejects_non_finite_or_coerced_profile_numeric_values(
    field: str, invalid: object
) -> None:
    with pytest.raises(PopulationReachValidationError, match="must be a finite number"):
        PopulationReachProfile(**{field: invalid})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "distances",
    (
        (500, True),
        (500, None),
        (500, "1000"),
        (500, float("nan")),
        (500, float("inf")),
        [500, 1000],
    ),
)
def test_rejects_non_finite_coerced_or_non_tuple_corridor_values(distances: object) -> None:
    with pytest.raises(PopulationReachValidationError):
        PopulationReachProfile(corridor_distances_m=distances)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "invalid",
    (True, None, "100", 100.0, float("nan"), float("inf"), float("-inf")),
)
def test_rejects_non_integral_or_coerced_usual_resident_values(invalid: object) -> None:
    routes, areas, area_definition = _inputs()
    areas["usual_residents"] = areas["usual_residents"].astype(object)
    areas.at[0, "usual_residents"] = invalid

    with pytest.raises(PopulationReachValidationError, match="whole non-negative"):
        compile_population_reach(routes, areas, area_definition, source=_source())


def test_rejects_non_finite_route_oa_pwc_and_area_coordinates_without_identity_drift() -> None:
    routes, areas, area_definition = _inputs()
    baseline = compile_population_reach(routes, areas, area_definition, source=_source())

    invalid_routes = routes.copy()
    invalid_routes.at[0, "geometry"] = LineString([(0, 0), (float("inf"), 1)])
    with pytest.raises(PopulationReachValidationError, match=r"route options.*finite coordinates"):
        compile_population_reach(invalid_routes, areas, area_definition, source=_source())

    invalid_oas = areas.copy()
    invalid_oas.at[0, "geometry"] = Polygon(
        [(0, 0), (1, 0), (float("nan"), 1), (0, 0)]
    )
    with pytest.raises(PopulationReachValidationError, match=r"output areas.*finite coordinates"):
        compile_population_reach(routes, invalid_oas, area_definition, source=_source())

    invalid_pwc = areas.copy()
    invalid_pwc.at[0, "population_weighted_centroid"] = Point(float("nan"), 0)
    with pytest.raises(
        PopulationReachValidationError,
        match=r"population-weighted centroids.*finite coordinates",
    ):
        compile_population_reach(routes, invalid_pwc, area_definition, source=_source())

    invalid_definition = area_definition.copy()
    invalid_definition.at[0, "geometry"] = Polygon(
        [(0, 0), (1, 0), (float("inf"), 1), (0, 0)]
    )
    with pytest.raises(
        PopulationReachValidationError,
        match=r"area definition.*finite coordinates",
    ):
        compile_population_reach(routes, areas, invalid_definition, source=_source())

    repeated = compile_population_reach(routes, areas, area_definition, source=_source())
    assert repeated.assessment_id == baseline.assessment_id
    assert repeated.canonical() == baseline.canonical()


def test_canonical_json_rejects_non_finite_values() -> None:
    with pytest.raises(PopulationReachValidationError, match="finite JSON values"):
        population_reach._canonical_json({"invalid": float("nan")})


def test_governed_hashes_are_preserved_exactly_without_normalisation() -> None:
    evidence = CurrentDevelopmentEvidence(
        source_id="banes-current-development",
        release="Current development schedule",
        effective_date="2026-07-01",
        licence="Open Government Licence v3.0",
        content_sha256="B" * 64,
        availability=CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
        conclusion=CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
    )
    source = replace(
        _source(),
        content_sha256="A" * 64,
        current_development_evidence=evidence,
    )

    assert source.canonical()["content_sha256"] == "A" * 64
    assert source.canonical()["current_development_evidence"] == evidence.canonical()
    assert evidence.canonical()["content_sha256"] == "B" * 64


def test_repeated_dataframe_indexes_do_not_ambiguously_select_population_centroids() -> None:
    routes, areas, area_definition = _inputs()
    areas.index = [7] * len(areas)

    assessment = compile_population_reach(routes, areas, area_definition, source=_source())
    _, original_areas, _ = _inputs()
    original = compile_population_reach(routes, original_areas, area_definition, source=_source())

    assert assessment.canonical() == original.canonical()


def test_exact_cutoffs_use_literal_distance_after_each_declared_crs_projection() -> None:
    routes = gpd.GeoDataFrame(
        {"option_id": ["alpha"], "geometry": [LineString([(400000, 150000), (401000, 150000)])]},
        crs="EPSG:27700",
    )
    areas = gpd.GeoDataFrame(
        {
            "OA21CD": ["E500", "E1000"],
            "usual_residents": [50, 100],
            "population_weighted_centroid": [Point(400200, 150500), Point(400200, 151000)],
            "geometry": [_square(400200, 150500), _square(400200, 151000)],
        },
        crs="EPSG:27700",
    )
    area_definition = gpd.GeoDataFrame(
        {
            "geometry": [
                Polygon([(399000, 149000), (402000, 149000), (402000, 152000), (399000, 152000)])
            ]
        },
        crs="EPSG:27700",
    )
    profile = PopulationReachProfile()
    wgs_routes = routes.to_crs("EPSG:4326")
    wgs_areas = areas.to_crs("EPSG:4326")
    wgs_areas["population_weighted_centroid"] = list(
        gpd.GeoSeries(areas["population_weighted_centroid"], crs=areas.crs).to_crs("EPSG:4326")
    )
    wgs_area_definition = area_definition.to_crs("EPSG:4326")

    bng = compile_population_reach(
        routes, areas, area_definition, source=_source(), profile=profile
    )
    wgs = compile_population_reach(
        wgs_routes, wgs_areas, wgs_area_definition, source=_source(), profile=profile
    )

    assert wgs.area_definition_sha256 == bng.area_definition_sha256
    assert wgs.option_geometries == bng.option_geometries
    assert wgs.assessment_id != bng.assessment_id
    bng_captured = {
        (record.corridor_distance_m, record.oa_id): record.captured for record in bng.records
    }
    assert bng_captured[(500.0, "E500")]
    assert not bng_captured[(500.0, "E1000")]
    assert bng_captured[(1000.0, "E500")]
    assert bng_captured[(1000.0, "E1000")]
    wgs_records = {
        (record.corridor_distance_m, record.oa_id): record for record in wgs.records
    }
    assert not wgs_records[(500.0, "E500")].captured
    assert wgs_records[(500.0, "E500")].decision_distance_to_route_m > 500.0
    assert not wgs_records[(1000.0, "E1000")].captured
    assert wgs_records[(1000.0, "E1000")].decision_distance_to_route_m > 1000.0


def test_half_centimetre_round_trips_preserve_geometry_identity_not_widened_cutoffs() -> None:
    route_y = 150000.005
    routes = gpd.GeoDataFrame(
        {
            "option_id": ["alpha"],
            "geometry": [
                LineString([(400000.005, route_y), (401000.005, route_y)])
            ],
        },
        crs="EPSG:27700",
    )
    areas = gpd.GeoDataFrame(
        {
            "OA21CD": [
                "E499995",
                "E500000",
                "E500005",
                "E999995",
                "E1000000",
                "E1000005",
            ],
            "usual_residents": [10, 20, 30, 40, 50, 60],
            "population_weighted_centroid": [
                Point(400200.005, route_y + 499.995),
                Point(400300.005, route_y + 500.000),
                Point(400400.005, route_y + 500.005),
                Point(400500.005, route_y + 999.995),
                Point(400600.005, route_y + 1000.000),
                Point(400700.005, route_y + 1000.005),
            ],
            "geometry": [
                _square(400200.005, route_y + 499.995),
                _square(400300.005, route_y + 500.000),
                _square(400400.005, route_y + 500.005),
                _square(400500.005, route_y + 999.995),
                _square(400600.005, route_y + 1000.000),
                _square(400700.005, route_y + 1000.005),
            ],
        },
        crs="EPSG:27700",
    )
    area_definition = gpd.GeoDataFrame(
        {
            "geometry": [
                Polygon(
                    [
                        (399000.005, 149000.005),
                        (402000.005, 149000.005),
                        (402000.005, 152000.005),
                        (399000.005, 152000.005),
                    ]
                )
            ]
        },
        crs="EPSG:27700",
    )

    wgs_routes = routes.to_crs("EPSG:4326")
    wgs_areas = areas.to_crs("EPSG:4326")
    wgs_areas["population_weighted_centroid"] = list(
        gpd.GeoSeries(areas["population_weighted_centroid"], crs=areas.crs).to_crs(
            "EPSG:4326"
        )
    )
    wgs_definition = area_definition.to_crs("EPSG:4326")
    round_trip_routes = wgs_routes.to_crs("EPSG:27700")
    round_trip_areas = wgs_areas.to_crs("EPSG:27700")
    round_trip_areas["population_weighted_centroid"] = list(
        gpd.GeoSeries(
            wgs_areas["population_weighted_centroid"], crs=wgs_areas.crs
        ).to_crs("EPSG:27700")
    )
    round_trip_definition = wgs_definition.to_crs("EPSG:27700")

    bng = compile_population_reach(routes, areas, area_definition, source=_source())
    wgs = compile_population_reach(wgs_routes, wgs_areas, wgs_definition, source=_source())
    round_trip = compile_population_reach(
        round_trip_routes,
        round_trip_areas,
        round_trip_definition,
        source=_source(),
    )

    assert wgs.canonical() == round_trip.canonical()
    assert wgs.assessment_id == round_trip.assessment_id
    assert wgs.assessment_id != bng.assessment_id
    assert wgs.area_definition_sha256 == bng.area_definition_sha256
    assert wgs.option_geometries == bng.option_geometries
    at_500 = {
        record.oa_id: (record.distance_to_route_m, record.captured)
        for record in bng.records
        if record.corridor_distance_m == 500.0
    }
    assert at_500 == {
        "E499995": (499.995, True),
        "E500000": (500.0, True),
        "E500005": (500.005, False),
        "E999995": (999.995, False),
        "E1000000": (1000.0, False),
        "E1000005": (1000.005, False),
    }
    at_1000 = {
        record.oa_id: (record.distance_to_route_m, record.captured)
        for record in bng.records
        if record.corridor_distance_m == 1000.0
    }
    assert at_1000 == {
        "E499995": (499.995, True),
        "E500000": (500.0, True),
        "E500005": (500.005, True),
        "E999995": (999.995, True),
        "E1000000": (1000.0, True),
        "E1000005": (1000.005, False),
    }
    wgs_at_500 = {
        record.oa_id: record.captured
        for record in wgs.records
        if record.corridor_distance_m == 500.0
    }
    assert wgs_at_500 == {
        "E499995": True,
        "E500000": False,
        "E500005": False,
        "E999995": False,
        "E1000000": False,
        "E1000005": False,
    }
    wgs_at_1000 = {
        record.oa_id: record.captured
        for record in wgs.records
        if record.corridor_distance_m == 1000.0
    }
    assert wgs_at_1000 == {
        "E499995": True,
        "E500000": True,
        "E500005": True,
        "E999995": True,
        "E1000000": False,
        "E1000005": False,
    }


@pytest.mark.parametrize("raw_distance_m", (500.000004, 500.00004, 500.0004))
def test_angled_raw_distance_above_500m_is_outside_and_auditable(
    raw_distance_m: float,
) -> None:
    route = LineString([(0, 0), (1000, 1000)])
    perpendicular_offset = raw_distance_m / math.sqrt(2)
    centroid = Point(500 - perpendicular_offset, 500 + perpendicular_offset)
    routes = gpd.GeoDataFrame(
        {"option_id": ["alpha"], "geometry": [route]},
        crs="EPSG:27700",
    )
    areas = gpd.GeoDataFrame(
        {
            "OA21CD": ["angled-outside"],
            "usual_residents": [100],
            "population_weighted_centroid": [centroid],
            "geometry": [_square(centroid.x, centroid.y)],
        },
        crs="EPSG:27700",
    )
    area_definition = gpd.GeoDataFrame(
        {"geometry": [Polygon([(-1000, -1000), (2000, -1000), (2000, 2000), (-1000, 2000)])]},
        crs="EPSG:27700",
    )

    record = next(
        record
        for record in compile_population_reach(
            routes, areas, area_definition, source=_source()
        ).records
        if record.corridor_distance_m == 500.0
    )

    assert record.distance_to_route_m == 500.0
    assert record.decision_distance_to_route_m == pytest.approx(raw_distance_m)
    assert record.decision_distance_to_route_m > record.corridor_distance_m
    assert record.decision_distance_to_corridor_boundary_m == pytest.approx(
        raw_distance_m - 500.0
    )
    assert not record.captured
    serialised = json.loads(population_reach._canonical_json(record.canonical()))
    assert (
        serialised["decision_distance_to_route_m"]
        == record.decision_distance_to_route_m
    )
    assert serialised["decision_distance_to_route_m"] > 500.0


def test_literal_corridor_comparison_never_widens_the_policy_boundary() -> None:
    assert population_reach._within_corridor(500.0, 500.0)
    assert not population_reach._within_corridor(500.000004, 500.0)
    assert not population_reach._within_corridor(1000.000004, 1000.0)


def test_true_half_grid_bng_wgs84_round_trip_has_one_lower_tie_identity() -> None:
    routes = gpd.GeoDataFrame(
        {
            "option_id": ["alpha"],
            "geometry": [
                LineString(
                    [
                        (400000.0025, 150000.0025),
                        (401000.0025, 150000.0025),
                    ]
                )
            ],
        },
        crs="EPSG:27700",
    )
    areas = gpd.GeoDataFrame(
        {
            "OA21CD": ["half-grid"],
            "usual_residents": [100],
            "population_weighted_centroid": [Point(400200.0025, 150100.0025)],
            "geometry": [_square(400200.0025, 150100.0025)],
        },
        crs="EPSG:27700",
    )
    area_definition = gpd.GeoDataFrame(
        {
            "geometry": [
                Polygon(
                    [
                        (399000.0025, 149000.0025),
                        (402000.0025, 149000.0025),
                        (402000.0025, 152000.0025),
                        (399000.0025, 152000.0025),
                    ]
                )
            ]
        },
        crs="EPSG:27700",
    )
    wgs_routes = routes.to_crs("EPSG:4326")
    wgs_areas = areas.to_crs("EPSG:4326")
    wgs_areas["population_weighted_centroid"] = list(
        gpd.GeoSeries(areas["population_weighted_centroid"], crs=areas.crs).to_crs(
            "EPSG:4326"
        )
    )
    wgs_definition = area_definition.to_crs("EPSG:4326")

    bng = compile_population_reach(routes, areas, area_definition, source=_source())
    wgs = compile_population_reach(
        wgs_routes, wgs_areas, wgs_definition, source=_source()
    )

    assert wgs.canonical() == bng.canonical()
    assert wgs.assessment_id == bng.assessment_id
    assert population_reach._canonical_projected_coordinate(400000.0025) == 400000.0
    assert population_reach._canonical_projected_coordinate(400000.0033) == 400000.0
    assert population_reach._canonical_projected_coordinate(400000.0036) == 400000.005
    assert population_reach._canonical_projected_coordinate(150000.0025) == 150000.0
    assert population_reach._canonical_projected_coordinate(-10.0025) == -10.005
    assert population_reach._canonical_projected_coordinate(-10.0017) == -10.005
    assert population_reach._canonical_projected_coordinate(-10.0014) == -10.0


def test_profile_geometry_and_canonical_json_normalise_every_signed_zero() -> None:
    negative = PopulationReachProfile(
        comparison_tolerance_percent=-0.0,
        borderline_distance_tolerance_m=-0.0,
    )
    positive = PopulationReachProfile(
        comparison_tolerance_percent=0.0,
        borderline_distance_tolerance_m=0.0,
    )

    assert negative.canonical() == positive.canonical()
    assert math.copysign(1.0, negative.comparison_tolerance_percent) == 1.0
    assert math.copysign(1.0, negative.borderline_distance_tolerance_m) == 1.0
    assert math.copysign(1.0, population_reach._canonical_measurement(-0.0004)) == 1.0
    assert math.copysign(
        1.0, population_reach._canonical_projected_coordinate(-0.0)
    ) == 1.0
    assert population_reach._canonical_json({"zero": -0.0, "nested": [-0.0]}) == (
        '{"nested":[0.0],"zero":0.0}'
    )
    assert population_reach._geometry_sha256(
        LineString([(-0.0, -0.0), (5.0, -0.0)])
    ) == population_reach._geometry_sha256(
        LineString([(0.0, 0.0), (5.0, 0.0)])
    )


def test_runner_up_exclusive_borderline_oa_cannot_be_reported_as_decisive() -> None:
    routes = gpd.GeoDataFrame(
        {
            "option_id": ["alpha", "beta"],
            "geometry": [LineString([(0, 0), (500, 0)]), LineString([(0, 1000), (500, 1000)])],
        },
        crs="EPSG:27700",
    )
    areas = gpd.GeoDataFrame(
        {
            "OA21CD": ["winner", "runner", "runner-borderline"],
            "usual_residents": [1100, 900, 100],
            "population_weighted_centroid": [Point(200, 0), Point(200, 1000), Point(200, 510)],
            "geometry": [_square(200, 0), _square(200, 1000), _square(200, 510)],
        },
        crs="EPSG:27700",
    )
    area_definition = gpd.GeoDataFrame(
        {"geometry": [Polygon([(-100, -100), (600, -100), (600, 1100), (-100, 1100)])]},
        crs="EPSG:27700",
    )

    sensitivity = compile_population_reach(
        routes,
        areas,
        area_definition,
        source=_source(),
        profile=PopulationReachProfile(),
    ).sensitivities[0]

    assert sensitivity.option_ranking == ("alpha", "beta")
    assert sensitivity.margin_to_next_residents == 100
    assert sensitivity.borderline_oa_ids == ("runner-borderline",)
    assert not sensitivity.margin_dominated_by_borderline_oa
    assert sensitivity.individually_decisive_borderline_oa_ids == ()


def test_winner_exclusive_oa_just_outside_runner_boundary_is_individually_decisive() -> None:
    routes = gpd.GeoDataFrame(
        {
            "option_id": ["winner", "runner"],
            "geometry": [
                LineString([(-100, 0), (500, 0)]),
                LineString([(-100, 700), (500, 700)]),
            ],
        },
        crs="EPSG:27700",
    )
    areas = gpd.GeoDataFrame(
        {
            "OA21CD": ["winner-base", "runner-base", "runner-510"],
            "usual_residents": [1000, 1000, 100],
            "population_weighted_centroid": [
                Point(200, 0),
                Point(200, 700),
                Point(200, 190),
            ],
            "geometry": [_square(200, 0), _square(200, 700), _square(200, 190)],
        },
        crs="EPSG:27700",
    )
    area_definition = gpd.GeoDataFrame(
        {"geometry": [Polygon([(-200, -100), (600, -100), (600, 800), (-200, 800)])]},
        crs="EPSG:27700",
    )

    sensitivity = compile_population_reach(
        routes, areas, area_definition, source=_source()
    ).sensitivities[0]

    assert sensitivity.option_ranking == ("winner", "runner")
    assert sensitivity.margin_to_next_residents == 100
    assert sensitivity.borderline_oa_ids == ("runner-510",)
    assert sensitivity.margin_dominated_by_borderline_oa
    assert sensitivity.individually_decisive_borderline_oa_ids == ("runner-510",)


def test_multiple_runner_side_borderline_oas_are_not_pooled_as_individually_decisive() -> None:
    routes = gpd.GeoDataFrame(
        {
            "option_id": ["winner", "runner"],
            "geometry": [
                LineString([(-100, 0), (500, 0)]),
                LineString([(-100, 700), (500, 700)]),
            ],
        },
        crs="EPSG:27700",
    )
    areas = gpd.GeoDataFrame(
        {
            "OA21CD": ["winner-base", "runner-base", "borderline-a", "borderline-b"],
            "usual_residents": [1000, 1000, 60, 60],
            "population_weighted_centroid": [
                Point(200, 0),
                Point(200, 700),
                Point(100, 190),
                Point(300, 190),
            ],
            "geometry": [
                _square(200, 0),
                _square(200, 700),
                _square(100, 190),
                _square(300, 190),
            ],
        },
        crs="EPSG:27700",
    )
    area_definition = gpd.GeoDataFrame(
        {"geometry": [Polygon([(-200, -100), (600, -100), (600, 800), (-200, 800)])]},
        crs="EPSG:27700",
    )

    sensitivity = compile_population_reach(
        routes, areas, area_definition, source=_source()
    ).sensitivities[0]

    assert sensitivity.margin_to_next_residents == 120
    assert sensitivity.borderline_oa_ids == ("borderline-a", "borderline-b")
    assert not sensitivity.margin_dominated_by_borderline_oa
    assert sensitivity.individually_decisive_borderline_oa_ids == ()
