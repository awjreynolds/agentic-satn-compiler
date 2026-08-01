"""Governed local evidence adapter for the raw parallel-reduction seam."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

import geopandas as gpd
from shapely.geometry import LineString, Point
from shapely.ops import substring

from satn.parallel_reduction_scope import effective_route_scope_ranges
from satn.section_population import (
    SectionPopulationProfile,
    compile_section_population_capture,
    derive_material_population_differences,
)
from satn.topography import build_topography_profiles


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class ParallelEvidenceSummary:
    section_profile: dict[str, object]
    section_profile_fingerprint: str
    material_population_differences: tuple[dict[str, object], ...]
    cumulative_elevation_variation: tuple[dict[str, object], ...]
    missing_evidence: tuple[str, ...]
    section_population_sections: tuple[dict[str, object], ...]


def build_parallel_evidence(
    routes: Sequence[object],
    config: object,
    output_areas: Sequence[object],
    source_fingerprint: str | None,
) -> ParallelEvidenceSummary:
    """Reuse governed section and elevation algorithms; never select a route."""
    profile = SectionPopulationProfile(
        display_section_length_m=config.section_length_m,
        urban_capture_radius_m=config.urban_capture_radius_m,
        rural_capture_radius_m=config.rural_capture_radius_m,
        material_absolute_difference_residents=config.material_population_absolute_residents,
        material_relative_difference_pct=config.material_population_relative_pct,
        material_persistence_m=config.material_population_persistence_m,
    )
    lines = [LineString(route.coordinates) for route in routes]
    alignments = gpd.GeoDataFrame(
        {
            "route_id": [route.route_id for route in routes],
            "alignment_id": [route.route_id for route in routes],
            "candidate_group_id": [":".join(route.endpoints) for route in routes],
            "geometry": lines,
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    sections: tuple[dict[str, object], ...] = ()
    differences: tuple[dict[str, object], ...] = ()
    missing = []
    urban = gpd.GeoDataFrame(
        {
            "geometry": [
                substring(line, scope_range.start_distance_m, scope_range.end_distance_m).buffer(1)
                for line, route in zip(lines, routes, strict=True)
                for scope_range in effective_route_scope_ranges(
                    line.length, route.network_scope, route.network_scope_spans
                )
                if scope_range.network_scope == "urban"
            ]
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    elevations = []
    if not output_areas or source_fingerprint is None:
        missing.append("section-population:governed-output-area-centroids")
    else:
        points = [Point(item.coordinates) for item in output_areas]
        oas = gpd.GeoDataFrame(
            {
                "OA21CD": [item.oa_id for item in output_areas],
                "usual_residents": [item.residents for item in output_areas],
                "population_weighted_centroid": points,
                "geometry": [point.buffer(0.01) for point in points],
            },
            geometry="geometry",
            crs="EPSG:27700",
        )
        inside = [
            point.buffer(0.1)
            for point, item in zip(points, output_areas, strict=True)
            if item.inside_area
        ]
        if inside:
            boundary = gpd.GeoDataFrame({"geometry": inside}, geometry="geometry", crs="EPSG:27700")
            assessment = compile_section_population_capture(
                alignments,
                oas,
                boundary,
                urban_extent=urban,
                source_content_sha256=source_fingerprint,
                profile=profile,
            )
            sections = tuple(item.canonical() for item in assessment.sections)
            differences = tuple(
                item.canonical() for item in derive_material_population_differences(assessment)
            )
        else:
            missing.append("section-population:inside-area-definition")
    for line, route in zip(lines, routes, strict=True):
        if not route.elevation_samples:
            missing.append(f"elevation:{route.route_id}")
        for distance_m, elevation_m in route.elevation_samples:
            elevations.append(
                {
                    "evidence_id": f"{route.route_id}:{distance_m}",
                    "source_id": route.route_id,
                    "elevation_m": elevation_m,
                    "geometry": line.interpolate(distance_m),
                }
            )
    cev_by_route: dict[str, float] = {}
    if elevations:
        evidence = gpd.GeoDataFrame(elevations, geometry="geometry", crs="EPSG:27700")
        profiles, _ = build_topography_profiles(
            [("parallel", "route_id", alignments.copy())], evidence
        )
        for _, row in profiles.iterrows():
            if row.forward_ascent_m is None or row.forward_descent_m is None:
                missing.append(f"elevation:{row.edge_id}")
                continue
            cev_by_route[str(row.edge_id)] = float(row.forward_ascent_m + row.forward_descent_m)
    variation = []
    for left, right in combinations(sorted(cev_by_route), 2):
        left_cev, right_cev = cev_by_route[left], cev_by_route[right]
        larger = max(left_cev, right_cev)
        difference = abs(left_cev - right_cev)
        relative = 0.0 if larger == 0 else difference / larger * 100
        variation.append(
            {
                "route_ids": (left, right),
                "left_cumulative_elevation_variation_m": left_cev,
                "right_cumulative_elevation_variation_m": right_cev,
                "absolute_difference_m": difference,
                "relative_difference_pct_of_larger_cev": relative,
                "material": (
                    difference >= config.material_elevation_variation_m
                    and relative >= config.material_elevation_variation_pct
                ),
            }
        )
    return ParallelEvidenceSummary(
        profile.canonical(),
        _digest(profile.canonical()),
        differences,
        tuple(variation),
        tuple(sorted(set(missing))),
        sections,
    )
