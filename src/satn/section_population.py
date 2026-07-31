"""Local, scope-sensitive population evidence for strategic route sections.

This module is deliberately separate from :mod:`satn.population_reach`.
Population Reach v1 remains an option-level 500 m/1 km comparison.  Section
Population Capture describes the population beside short pieces of alignment
and must not be presented as end-to-end demand or ridership.
"""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations, pairwise
from numbers import Integral, Real

import geopandas as gpd
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring, unary_union

SECTION_POPULATION_CONTRACT = "satn-section-population-capture/v1"
_BNG = "EPSG:27700"
_SHA256_LENGTH = 64
_DISTANCE_PRECISION = 3


class SectionPopulationValidationError(ValueError):
    """Section-population inputs do not satisfy the governed contract."""


@dataclass(frozen=True)
class SectionPopulationProfile:
    """Frozen policy values for local section capture and materiality."""

    display_section_length_m: float = 100.0
    urban_capture_radius_m: float = 250.0
    rural_capture_radius_m: float = 750.0
    material_absolute_difference_residents: int = 500
    material_relative_difference_pct: float = 50.0
    material_persistence_m: float = 500.0
    maximum_display_section_length_m: float = 1_000.0

    def __post_init__(self) -> None:
        display = _positive_finite(
            self.display_section_length_m, "display section length"
        )
        maximum = _positive_finite(
            self.maximum_display_section_length_m,
            "maximum display section length",
        )
        if display > maximum or maximum > 1_000:
            raise SectionPopulationValidationError(
                "display section length must not exceed the governed 1 km maximum"
            )
        urban = _positive_finite(self.urban_capture_radius_m, "urban capture radius")
        rural = _positive_finite(self.rural_capture_radius_m, "rural capture radius")
        persistence = _positive_finite(
            self.material_persistence_m, "material persistence"
        )
        if (
            type(self.material_absolute_difference_residents) is not int
            or self.material_absolute_difference_residents < 0
        ):
            raise SectionPopulationValidationError(
                "material absolute difference must be a non-negative whole resident count"
            )
        relative = _nonnegative_finite(
            self.material_relative_difference_pct,
            "material relative difference",
        )
        object.__setattr__(self, "display_section_length_m", display)
        object.__setattr__(self, "maximum_display_section_length_m", maximum)
        object.__setattr__(self, "urban_capture_radius_m", urban)
        object.__setattr__(self, "rural_capture_radius_m", rural)
        object.__setattr__(self, "material_persistence_m", persistence)
        object.__setattr__(self, "material_relative_difference_pct", relative)

    def canonical(self) -> dict[str, object]:
        return {
            "display_section_length_m": self.display_section_length_m,
            "maximum_display_section_length_m": self.maximum_display_section_length_m,
            "urban_capture_radius_m": self.urban_capture_radius_m,
            "rural_capture_radius_m": self.rural_capture_radius_m,
            "material_absolute_difference_residents": (
                self.material_absolute_difference_residents
            ),
            "material_relative_difference_pct": self.material_relative_difference_pct,
            "material_persistence_m": self.material_persistence_m,
        }


@dataclass(frozen=True)
class PopulationDisplaySection:
    """One ordered local population observation on a candidate alignment."""

    section_id: str
    candidate_group_id: str
    alignment_id: str
    section_order: int
    start_distance_m: float
    end_distance_m: float
    length_m: float
    alignment_length_m: float
    network_scope: str
    capture_radius_m: float
    total_residents: int
    inside_area_residents: int
    outside_area_residents: int
    captured_oa_ids: tuple[str, ...]
    geometry: LineString

    @property
    def midpoint_fraction(self) -> float:
        return (
            (self.start_distance_m + self.end_distance_m)
            / (2 * self.alignment_length_m)
            if self.alignment_length_m
            else 0.0
        )

    def canonical(self) -> dict[str, object]:
        return {
            "section_id": self.section_id,
            "candidate_group_id": self.candidate_group_id,
            "alignment_id": self.alignment_id,
            "section_order": self.section_order,
            "start_distance_m": self.start_distance_m,
            "end_distance_m": self.end_distance_m,
            "length_m": self.length_m,
            "alignment_length_m": self.alignment_length_m,
            "network_scope": self.network_scope,
            "capture_radius_m": self.capture_radius_m,
            "total_residents": self.total_residents,
            "inside_area_residents": self.inside_area_residents,
            "outside_area_residents": self.outside_area_residents,
            "captured_oa_ids": list(self.captured_oa_ids),
            "geometry_sha256": hashlib.sha256(self.geometry.wkb).hexdigest(),
        }


@dataclass(frozen=True)
class MaterialPopulationDifference:
    """One sustained pairwise local population advantage."""

    candidate_group_id: str
    advantaged_alignment_id: str
    compared_alignment_id: str
    start_fraction: float
    end_fraction: float
    corridor_length_m: float
    minimum_absolute_difference_residents: int
    minimum_relative_difference_pct: float
    supporting_section_ids: tuple[str, ...]

    def canonical(self) -> dict[str, object]:
        return {
            "candidate_group_id": self.candidate_group_id,
            "advantaged_alignment_id": self.advantaged_alignment_id,
            "compared_alignment_id": self.compared_alignment_id,
            "start_fraction": self.start_fraction,
            "end_fraction": self.end_fraction,
            "corridor_length_m": self.corridor_length_m,
            "minimum_absolute_difference_residents": (
                self.minimum_absolute_difference_residents
            ),
            "minimum_relative_difference_pct": self.minimum_relative_difference_pct,
            "supporting_section_ids": list(self.supporting_section_ids),
        }


@dataclass(frozen=True)
class _OutputAreaCentroid:
    """One validated, measured OA centroid held behind the capture seam."""

    oa_id: str
    residents: int
    centroid: Point
    is_inside_area: bool


@dataclass(frozen=True)
class _OutputAreaIndex:
    """Private canonical OA records and their one reusable spatial index."""

    rows: tuple[_OutputAreaCentroid, ...]
    centroids: gpd.GeoSeries
    spatial_index: object


@dataclass(frozen=True)
class SectionPopulationAssessment:
    """Canonical ordered local population evidence for finite candidates."""

    assessment_id: str
    contract: str
    source_content_sha256: str
    area_definition_sha256: str
    profile: SectionPopulationProfile
    sections: tuple[PopulationDisplaySection, ...]

    def canonical(self) -> dict[str, object]:
        return {
            "assessment_id": self.assessment_id,
            "contract": self.contract,
            "source_content_sha256": self.source_content_sha256,
            "area_definition_sha256": self.area_definition_sha256,
            "profile": self.profile.canonical(),
            "sections": [item.canonical() for item in self.sections],
        }

    def to_geodataframe(self) -> gpd.GeoDataFrame:
        """Return publication-ready section geometry in British National Grid."""

        rows = [
            {
                **{
                    key: value
                    for key, value in item.canonical().items()
                    if key not in {"geometry_sha256", "captured_oa_ids"}
                },
                "captured_oa_ids": list(item.captured_oa_ids),
                "geometry": item.geometry,
            }
            for item in self.sections
        ]
        return gpd.GeoDataFrame(rows, geometry="geometry", crs=_BNG)


def compile_section_population_capture(
    alignments: gpd.GeoDataFrame,
    output_areas: gpd.GeoDataFrame,
    area_definition: gpd.GeoDataFrame,
    *,
    urban_extent: gpd.GeoDataFrame,
    source_content_sha256: str,
    profile: SectionPopulationProfile | None = None,
    alignment_id_column: str = "alignment_id",
    candidate_group_column: str = "candidate_group_id",
    oa_id_column: str = "OA21CD",
    residents_column: str = "usual_residents",
    centroid_column: str = "population_weighted_centroid",
) -> SectionPopulationAssessment:
    """Compile local OA-centroid capture along finite candidate alignments."""

    if profile is None:
        profile = SectionPopulationProfile()
    elif not isinstance(profile, SectionPopulationProfile):
        raise SectionPopulationValidationError(
            "section population profile must be a SectionPopulationProfile"
        )
    _validate_sha256(source_content_sha256, "source content")
    for frame, label in (
        (alignments, "alignments"),
        (output_areas, "output areas"),
        (area_definition, "area definition"),
        (urban_extent, "urban extent"),
    ):
        _validate_frame(frame, label, allow_empty=label == "urban extent")
    _require_columns(
        alignments,
        (alignment_id_column, candidate_group_column),
        "alignments",
    )
    _require_columns(
        output_areas,
        (oa_id_column, residents_column, centroid_column),
        "output areas",
    )

    _validate_alignment_geometries(alignments)
    _validate_output_area_geometries(output_areas)
    _validate_polygon_geometries(area_definition, "area definition")
    if not urban_extent.empty:
        _validate_polygon_geometries(urban_extent, "urban extent")

    measured_alignments = _to_bng(alignments, "alignments")
    measured_areas = _to_bng(output_areas, "output areas")
    measured_centroids = _to_bng(
        gpd.GeoDataFrame(
            geometry=gpd.GeoSeries(
                list(output_areas[centroid_column]), crs=output_areas.crs
            ),
            crs=output_areas.crs,
        ),
        "population-weighted centroids",
    ).geometry
    measured_boundary = _polygon_union(
        _to_bng(area_definition, "area definition"), "area definition"
    )
    measured_urban = (
        _polygon_union(_to_bng(urban_extent, "urban extent"), "urban extent")
        if not urban_extent.empty
        else None
    )
    if measured_boundary.is_empty:
        raise SectionPopulationValidationError("area definition must not be empty")

    oa_index = _output_area_index(
        measured_areas,
        measured_centroids,
        measured_boundary,
        oa_id_column=oa_id_column,
        residents_column=residents_column,
    )
    sections: list[PopulationDisplaySection] = []
    seen_alignment_ids: set[str] = set()
    ordered_alignments = sorted(
        measured_alignments.iterrows(),
        key=lambda item: (
            _identifier(item[1][candidate_group_column], "candidate group"),
            _identifier(item[1][alignment_id_column], "alignment"),
        ),
    )
    for _, row in ordered_alignments:
        alignment_id = _identifier(row[alignment_id_column], "alignment")
        group_id = _identifier(row[candidate_group_column], "candidate group")
        if alignment_id in seen_alignment_ids:
            raise SectionPopulationValidationError(
                f"alignment IDs must be unique: {alignment_id!r}"
            )
        seen_alignment_ids.add(alignment_id)
        line = row.geometry
        if not isinstance(line, LineString) or line.is_empty or not line.is_valid:
            raise SectionPopulationValidationError(
                "section population alignments must be valid non-empty LineStrings"
            )
        sections.extend(
            _alignment_sections(
                group_id,
                alignment_id,
                line,
                measured_urban,
                oa_index,
                profile,
            )
        )
    canonical_sections = tuple(
        sorted(
            sections,
            key=lambda item: (
                item.candidate_group_id,
                item.alignment_id,
                item.section_order,
            ),
        )
    )
    area_sha256 = hashlib.sha256(measured_boundary.wkb).hexdigest()
    payload = {
        "contract": SECTION_POPULATION_CONTRACT,
        "source_content_sha256": source_content_sha256,
        "area_definition_sha256": area_sha256,
        "profile": profile.canonical(),
        "sections": [item.canonical() for item in canonical_sections],
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return SectionPopulationAssessment(
        assessment_id=f"section-population-{digest[:16]}",
        contract=SECTION_POPULATION_CONTRACT,
        source_content_sha256=source_content_sha256,
        area_definition_sha256=area_sha256,
        profile=profile,
        sections=canonical_sections,
    )


def derive_material_population_differences(
    assessment: SectionPopulationAssessment,
) -> tuple[MaterialPopulationDifference, ...]:
    """Flag sustained local advantages without selecting an alignment."""

    by_group: dict[str, dict[str, list[PopulationDisplaySection]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for section in assessment.sections:
        by_group[section.candidate_group_id][section.alignment_id].append(section)
    differences: list[MaterialPopulationDifference] = []
    for group_id, by_alignment in sorted(by_group.items()):
        for left_id, right_id in combinations(sorted(by_alignment), 2):
            left = sorted(by_alignment[left_id], key=lambda item: item.section_order)
            right = sorted(by_alignment[right_id], key=lambda item: item.section_order)
            differences.extend(
                _directed_material_runs(
                    group_id,
                    left,
                    right,
                    assessment.profile,
                )
            )
            differences.extend(
                _directed_material_runs(
                    group_id,
                    right,
                    left,
                    assessment.profile,
                )
            )
    return tuple(
        sorted(
            differences,
            key=lambda item: (
                item.candidate_group_id,
                item.advantaged_alignment_id,
                item.compared_alignment_id,
                item.start_fraction,
            ),
        )
    )


def _alignment_sections(
    group_id: str,
    alignment_id: str,
    line: LineString,
    urban_extent: BaseGeometry | None,
    oa_index: _OutputAreaIndex,
    profile: SectionPopulationProfile,
) -> list[PopulationDisplaySection]:
    length = float(line.length)
    if not math.isfinite(length) or length <= 0:
        raise SectionPopulationValidationError("alignment length must be positive and finite")
    ranges = _scope_ranges(line, urban_extent)
    sections: list[PopulationDisplaySection] = []
    order = 0
    for range_start, range_end, scope in ranges:
        start = range_start
        while start < range_end - 1e-9:
            end = min(start + profile.display_section_length_m, range_end)
            geometry = substring(line, start, end)
            if not isinstance(geometry, LineString) or geometry.is_empty:
                raise SectionPopulationValidationError(
                    "display-section substring did not produce a LineString"
                )
            radius = (
                profile.urban_capture_radius_m
                if scope == "urban"
                else profile.rural_capture_radius_m
            )
            captured = tuple(
                sorted(
                    (
                        oa_index.rows[int(index)]
                        for index in oa_index.spatial_index.query(
                            geometry.buffer(radius), sort=True
                        )
                        if float(oa_index.rows[int(index)].centroid.distance(geometry))
                        <= radius
                    ),
                    key=lambda item: item.oa_id,
                )
            )
            captured_ids = tuple(item.oa_id for item in captured)
            inside = sum(
                item.residents for item in captured if item.is_inside_area
            )
            outside = sum(
                item.residents for item in captured if not item.is_inside_area
            )
            start_m = _distance(start)
            end_m = _distance(end)
            identity = {
                "candidate_group_id": group_id,
                "alignment_id": alignment_id,
                "section_order": order,
                "start_distance_m": start_m,
                "end_distance_m": end_m,
                "network_scope": scope,
                "capture_radius_m": radius,
                "geometry_sha256": hashlib.sha256(geometry.wkb).hexdigest(),
            }
            section_id = "population-section-" + hashlib.sha256(
                _canonical_json(identity).encode("utf-8")
            ).hexdigest()[:20]
            sections.append(
                PopulationDisplaySection(
                    section_id=section_id,
                    candidate_group_id=group_id,
                    alignment_id=alignment_id,
                    section_order=order,
                    start_distance_m=start_m,
                    end_distance_m=end_m,
                    length_m=_distance(float(geometry.length)),
                    alignment_length_m=_distance(length),
                    network_scope=scope,
                    capture_radius_m=radius,
                    total_residents=inside + outside,
                    inside_area_residents=inside,
                    outside_area_residents=outside,
                    captured_oa_ids=captured_ids,
                    geometry=geometry,
                )
            )
            order += 1
            start = end
    return sections


def _scope_ranges(
    line: LineString,
    urban_extent: BaseGeometry | None,
) -> tuple[tuple[float, float, str], ...]:
    if urban_extent is None or urban_extent.is_empty:
        return ((0.0, float(line.length), "rural"),)
    intersection = line.intersection(urban_extent.boundary)
    positions = {0.0, float(line.length)}
    for point in _intersection_points(intersection):
        positions.add(float(line.project(point)))
    ordered = sorted(positions)
    ranges: list[tuple[float, float, str]] = []
    for start, end in pairwise(ordered):
        if end - start <= 1e-9:
            continue
        midpoint = line.interpolate((start + end) / 2)
        scope = "urban" if urban_extent.covers(midpoint) else "rural"
        ranges.append((start, end, scope))
    return tuple(ranges)


def _intersection_points(geometry: BaseGeometry) -> tuple[Point, ...]:
    if geometry.is_empty:
        return ()
    if isinstance(geometry, Point):
        return (geometry,)
    if geometry.geom_type == "MultiPoint":
        return tuple(geometry.geoms)
    if geometry.geom_type in {"LineString", "LinearRing"}:
        boundary = geometry.boundary
        return _intersection_points(boundary)
    if hasattr(geometry, "geoms"):
        return tuple(
            point
            for part in geometry.geoms
            for point in _intersection_points(part)
        )
    return ()


def _output_area_index(
    frame: gpd.GeoDataFrame,
    centroids: gpd.GeoSeries,
    area_geometry: BaseGeometry,
    *,
    oa_id_column: str,
    residents_column: str,
) -> _OutputAreaIndex:
    rows: list[_OutputAreaCentroid] = []
    seen: set[str] = set()
    for (_, row), centroid in zip(frame.iterrows(), centroids, strict=True):
        oa_id = _identifier(row[oa_id_column], "OA")
        if oa_id in seen:
            raise SectionPopulationValidationError("canonical OA IDs must be unique")
        seen.add(oa_id)
        residents = row[residents_column]
        if isinstance(residents, bool) or not isinstance(residents, Integral) or residents < 0:
            raise SectionPopulationValidationError(
                "usual residents must be whole non-negative numbers"
            )
        if not isinstance(centroid, Point) or centroid.is_empty or not centroid.is_valid:
            raise SectionPopulationValidationError(
                "population-weighted centroids must be valid non-empty Points"
            )
        rows.append(
            _OutputAreaCentroid(
                oa_id=oa_id,
                residents=int(residents),
                centroid=centroid,
                is_inside_area=bool(area_geometry.covers(centroid)),
            )
        )
    canonical_rows = tuple(sorted(rows, key=lambda item: item.oa_id))
    canonical_centroids = gpd.GeoSeries(
        [item.centroid for item in canonical_rows], crs=_BNG
    )
    return _OutputAreaIndex(
        rows=canonical_rows,
        centroids=canonical_centroids,
        spatial_index=canonical_centroids.sindex,
    )


def _directed_material_runs(
    group_id: str,
    advantaged: list[PopulationDisplaySection],
    compared: list[PopulationDisplaySection],
    profile: SectionPopulationProfile,
) -> list[MaterialPopulationDifference]:
    qualifying: list[
        tuple[PopulationDisplaySection, int, float]
    ] = []
    compared_fractions = [item.midpoint_fraction for item in compared]
    for section in advantaged:
        other = _nearest_section_by_midpoint(
            section.midpoint_fraction,
            compared,
            compared_fractions,
        )
        absolute = section.total_residents - other.total_residents
        relative = (
            math.inf
            if other.total_residents == 0 and section.total_residents > 0
            else 0.0
            if other.total_residents == 0
            else absolute * 100.0 / other.total_residents
        )
        if (
            absolute >= profile.material_absolute_difference_residents
            and relative >= profile.material_relative_difference_pct
        ):
            qualifying.append((section, absolute, relative))
        else:
            qualifying.append((section, -1, -1.0))

    runs: list[list[tuple[PopulationDisplaySection, int, float]]] = []
    current: list[tuple[PopulationDisplaySection, int, float]] = []
    for item in qualifying:
        section, absolute, _relative = item
        contiguous = (
            current
            and math.isclose(
                current[-1][0].end_distance_m,
                section.start_distance_m,
                abs_tol=0.001,
            )
        )
        if absolute >= 0 and (not current or contiguous):
            current.append(item)
            continue
        if current:
            runs.append(current)
        current = [item] if absolute >= 0 else []
    if current:
        runs.append(current)

    results: list[MaterialPopulationDifference] = []
    for run in runs:
        corridor_length = sum(item[0].length_m for item in run)
        if corridor_length + 1e-9 < profile.material_persistence_m:
            continue
        first, last = run[0][0], run[-1][0]
        finite_relatives = [item[2] for item in run if math.isfinite(item[2])]
        minimum_relative = (
            min(finite_relatives) if finite_relatives else float("inf")
        )
        results.append(
            MaterialPopulationDifference(
                candidate_group_id=group_id,
                advantaged_alignment_id=first.alignment_id,
                compared_alignment_id=compared[0].alignment_id,
                start_fraction=_fraction(first.start_distance_m / first.alignment_length_m),
                end_fraction=_fraction(last.end_distance_m / last.alignment_length_m),
                corridor_length_m=_distance(corridor_length),
                minimum_absolute_difference_residents=min(item[1] for item in run),
                minimum_relative_difference_pct=(
                    minimum_relative
                    if math.isfinite(minimum_relative)
                    else 1_000_000.0
                ),
                supporting_section_ids=tuple(item[0].section_id for item in run),
            )
        )
    return results


def _nearest_section_by_midpoint(
    target_fraction: float,
    compared: list[PopulationDisplaySection],
    compared_fractions: list[float],
) -> PopulationDisplaySection:
    """Return the current nearest section with the historic order tie-break."""

    insertion = bisect_left(compared_fractions, target_fraction)
    exact_end = bisect_right(compared_fractions, target_fraction)
    if insertion != exact_end:
        candidates = compared[insertion:exact_end]
    else:
        candidates = []
        if insertion:
            left_fraction = compared_fractions[insertion - 1]
            left_start = bisect_left(compared_fractions, left_fraction)
            candidates.extend(compared[left_start:insertion])
        if insertion < len(compared):
            right_fraction = compared_fractions[insertion]
            right_end = bisect_right(compared_fractions, right_fraction)
            candidates.extend(compared[insertion:right_end])
    return min(
        candidates,
        key=lambda item: (
            abs(item.midpoint_fraction - target_fraction),
            item.section_order,
        ),
    )


def _validate_frame(
    frame: gpd.GeoDataFrame,
    label: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(frame, gpd.GeoDataFrame):
        raise SectionPopulationValidationError(f"{label} must be a GeoDataFrame")
    if frame.crs is None:
        raise SectionPopulationValidationError(f"{label} must declare a CRS")
    if frame.empty and not allow_empty:
        raise SectionPopulationValidationError(f"{label} must not be empty")


def _validate_alignment_geometries(frame: gpd.GeoDataFrame) -> None:
    if any(
        not isinstance(geometry, LineString)
        or geometry.is_empty
        or not geometry.is_valid
        or not math.isfinite(float(geometry.length))
        or geometry.length <= 0
        for geometry in frame.geometry
    ):
        raise SectionPopulationValidationError(
            "section population alignments must be valid non-empty LineStrings"
        )


def _validate_output_area_geometries(frame: gpd.GeoDataFrame) -> None:
    _validate_polygon_geometries(frame, "output areas")


def _validate_polygon_geometries(frame: gpd.GeoDataFrame, label: str) -> None:
    if any(
        geometry is None
        or geometry.geom_type not in {"Polygon", "MultiPolygon"}
        or geometry.is_empty
        or not geometry.is_valid
        or not math.isfinite(float(geometry.area))
        or geometry.area <= 0
        for geometry in frame.geometry
    ):
        raise SectionPopulationValidationError(
            f"{label} must contain valid non-empty polygon geometries"
        )


def _to_bng(frame: gpd.GeoDataFrame, label: str) -> gpd.GeoDataFrame:
    try:
        return frame.to_crs(_BNG)
    except Exception as error:
        raise SectionPopulationValidationError(
            f"{label} could not be projected to EPSG:27700"
        ) from error


def _polygon_union(frame: gpd.GeoDataFrame, label: str) -> BaseGeometry:
    geometry = unary_union(list(frame.geometry))
    if geometry.is_empty or not geometry.is_valid:
        raise SectionPopulationValidationError(
            f"{label} could not be combined into a valid measurement area"
        )
    return geometry


def _require_columns(
    frame: gpd.GeoDataFrame,
    columns: tuple[str, ...],
    label: str,
) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise SectionPopulationValidationError(
            f"{label} missing required columns: {', '.join(missing)}"
        )


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise SectionPopulationValidationError(
            f"{label} identifier must be a non-blank canonical string"
        )
    return value


def _validate_sha256(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SectionPopulationValidationError(f"{label} SHA-256 is invalid")


def _positive_finite(value: Real, label: str) -> float:
    result = _nonnegative_finite(value, label)
    if result <= 0:
        raise SectionPopulationValidationError(f"{label} must be positive")
    return result


def _nonnegative_finite(value: Real, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise SectionPopulationValidationError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise SectionPopulationValidationError(
            f"{label} must be a finite non-negative number"
        )
    return result


def _distance(value: float) -> float:
    return round(float(value), _DISTANCE_PRECISION)


def _fraction(value: float) -> float:
    return round(float(value), 9)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
