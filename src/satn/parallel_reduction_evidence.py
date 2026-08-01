"""Governed local evidence adapter for the raw parallel-reduction seam."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

import geopandas as gpd
from shapely.geometry import LineString, box

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


def build_parallel_evidence(routes: Sequence[object], config: object) -> ParallelEvidenceSummary:
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
            "alignment_id": [route.route_id for route in routes],
            "candidate_group_id": [":".join(route.endpoints) for route in routes],
            "geometry": lines,
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    points = [line.interpolate(0.5, normalized=True) for line in lines]
    oas = gpd.GeoDataFrame(
        {
            "OA21CD": [f"E{i:08d}" for i, _ in enumerate(routes)],
            "usual_residents": [route.population for route in routes],
            "population_weighted_centroid": points,
            "geometry": [point.buffer(1) for point in points],
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    minx, miny, maxx, maxy = alignments.total_bounds
    boundary = gpd.GeoDataFrame(
        {"geometry": [box(minx - 1, miny - 1, maxx + 1, maxy + 1)]},
        geometry="geometry",
        crs="EPSG:27700",
    )
    urban = gpd.GeoDataFrame(
        {
            "geometry": [
                line.buffer(1)
                for line, route in zip(lines, routes, strict=True)
                if route.network_scope == "urban"
            ]
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    assessment = compile_section_population_capture(
        alignments,
        oas,
        boundary,
        urban_extent=urban,
        source_content_sha256=_digest([route.route_id for route in routes]),
        profile=profile,
    )
    differences = tuple(
        item.canonical() for item in derive_material_population_differences(assessment)
    )
    elevations = []
    missing = []
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
    variation = []
    if elevations:
        evidence = gpd.GeoDataFrame(elevations, geometry="geometry", crs="EPSG:27700")
        profiles, _ = build_topography_profiles(
            [("parallel", "route_id", alignments.copy())], evidence
        )
        for _, row in profiles.iterrows():
            if row.forward_ascent_m is None or row.forward_descent_m is None:
                missing.append(f"elevation:{row.edge_id}")
                continue
            cev = float(row.forward_ascent_m + row.forward_descent_m)
            baseline = max(float(row.distance_m), 1.0)
            variation.append(
                {
                    "route_id": row.edge_id,
                    "cumulative_elevation_variation_m": cev,
                    "material": cev >= config.material_elevation_variation_m
                    and cev / baseline * 100 >= config.material_elevation_variation_pct,
                }
            )
    return ParallelEvidenceSummary(
        profile.canonical(),
        _digest(profile.canonical()),
        differences,
        tuple(variation),
        tuple(sorted(set(missing))),
    )
