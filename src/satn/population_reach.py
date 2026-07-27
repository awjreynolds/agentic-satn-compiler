"""Deterministic population reach evidence for strategic alignment options.

This module measures *geometric corridor coverage*, not access, demand or a
walking-time catchment.  It deliberately works from governed ONS 2021 Output
Area (OA) inputs: each OA contributes its complete usual-resident count once
when its population-weighted centroid falls inside a straight-line route
corridor.  It has no dependency on SATN routing, selection or profile code so
that those stages can supply their own governed inputs.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import date
from numbers import Integral, Real
from typing import Any, Literal

import geopandas as gpd
from pyproj import CRS
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union
from shapely.wkb import dumps as wkb_dumps

BRITISH_NATIONAL_GRID_EPSG = 27700
ONS_2021_OA_GEOGRAPHY = "ons-2021-output-area"
WHOLE_OA_USUAL_RESIDENTS = "whole-oa-usual-residents"
POPULATION_WEIGHTED_CENTROID = "population-weighted-centroid"
PROHIBITED_CLAIMS = (
    "This geometric corridor measure is not a walking or cycling access catchment.",
    "This geometric corridor measure is not travel demand, mode-share, or route-use modelling.",
    "This evidence does not establish scheme feasibility, safety, cost, or delivery priority.",
)
CURRENT_DEVELOPMENT_ABSENT_WARNING = (
    "No governed current-development evidence was supplied; population reach excludes "
    "current-development residents."
)
CURRENT_DEVELOPMENT_OUTSTANDING_WARNING = (
    "Governed current-development evidence records a material omission that remains "
    "outstanding; population reach excludes those residents."
)
CANONICAL_PROJECTED_DECIMALS = 3
CANONICAL_PROJECTED_GRID_M = 0.005
CANONICAL_PROJECTED_TIE_BAND_M = 0.001
CANONICAL_PROJECTED_TOLERANCE_M = (
    CANONICAL_PROJECTED_GRID_M / 2 + CANONICAL_PROJECTED_TIE_BAND_M
)
POLICY_COMPARISON_EPSILON_M = 1e-5
POPULATION_REACH_V1_CORRIDOR_DISTANCES_M = (500.0, 1000.0)
CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE = "available"
CURRENT_DEVELOPMENT_EVIDENCE_MISSING = "missing"
CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION = "no-material-omission"
CURRENT_DEVELOPMENT_MATERIAL_OMISSION_INCORPORATED = "material-omission-incorporated"
CURRENT_DEVELOPMENT_MATERIAL_OMISSION_OUTSTANDING = "material-omission-outstanding"
CURRENT_DEVELOPMENT_CLEAR_CONCLUSIONS = frozenset(
    {
        CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
        CURRENT_DEVELOPMENT_MATERIAL_OMISSION_INCORPORATED,
    }
)


class PopulationReachValidationError(ValueError):
    """Raised where supplied reach evidence cannot be safely interpreted."""


@dataclass(frozen=True)
class CurrentDevelopmentEvidence:
    """Governed current-development conclusion for current-development residents.

    Population reach must not claim that development is accounted for merely
    because an opaque identifier was supplied.  A missing statement is itself
    governed evidence: it names the source examined and pins the returned
    record or absence declaration by content hash.  Availability alone is not
    a conclusion: only an evidence-backed finding of no material omission, or
    a material omission that has been incorporated, can clear the sensitivity.
    """

    source_id: str
    release: str
    effective_date: str | date
    licence: str
    content_sha256: str
    availability: Literal["available", "missing"]
    conclusion: Literal[
        "no-material-omission",
        "material-omission-incorporated",
        "material-omission-outstanding",
    ]

    def __post_init__(self) -> None:
        for field in ("source_id", "release", "licence"):
            _require_non_blank_string(getattr(self, field), field)
        effective_date = _parse_iso_date(
            self.effective_date, "current development effective date"
        )
        if not _is_sha256(self.content_sha256):
            raise PopulationReachValidationError(
                "current development content SHA-256 must be a 64-character hexadecimal digest"
            )
        if self.availability not in {
            CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
            CURRENT_DEVELOPMENT_EVIDENCE_MISSING,
        }:
            raise PopulationReachValidationError(
                "current development evidence availability must be available or missing"
            )
        if self.conclusion not in {
            CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
            CURRENT_DEVELOPMENT_MATERIAL_OMISSION_INCORPORATED,
            CURRENT_DEVELOPMENT_MATERIAL_OMISSION_OUTSTANDING,
        }:
            raise PopulationReachValidationError(
                "current development conclusion must be a governed omission conclusion"
            )
        if (
            self.availability == CURRENT_DEVELOPMENT_EVIDENCE_MISSING
            and self.conclusion != CURRENT_DEVELOPMENT_MATERIAL_OMISSION_OUTSTANDING
        ):
            raise PopulationReachValidationError(
                "missing current development evidence must have an outstanding material omission"
            )
        object.__setattr__(self, "effective_date", effective_date.isoformat())

    def canonical(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "release": self.release,
            "effective_date": self.effective_date,
            "licence": self.licence,
            "content_sha256": self.content_sha256,
            "availability": self.availability,
            "conclusion": self.conclusion,
        }


@dataclass(frozen=True)
class PopulationReachProfile:
    """Data-only values controlling geometric corridor measurement.

    The defaults express the two v1 reporting distances.  Distances are
    straight-line metres from the supplied route geometry; they must not be
    presented as walking, cycling, access or demand measures.
    """

    corridor_distances_m: tuple[float, ...] = POPULATION_REACH_V1_CORRIDOR_DISTANCES_M
    comparison_tolerance_residents: int = 0
    comparison_tolerance_percent: float = 0.0
    borderline_distance_tolerance_m: float = 25.0

    def __post_init__(self) -> None:
        if not isinstance(self.corridor_distances_m, tuple):
            raise PopulationReachValidationError(
                "Population Reach v1 corridor distances must be the tuple (500, 1000) metres"
            )
        distances = tuple(
            _require_finite_real(value, "corridor distance")
            for value in self.corridor_distances_m
        )
        if distances != POPULATION_REACH_V1_CORRIDOR_DISTANCES_M:
            raise PopulationReachValidationError(
                "Population Reach v1 requires headline 500 m and sensitivity 1000 m corridors"
            )
        if (
            type(self.comparison_tolerance_residents) is not int
            or self.comparison_tolerance_residents < 0
        ):
            raise PopulationReachValidationError(
                "comparison tolerance residents must be a non-negative whole integer"
            )
        comparison_tolerance_percent = _require_finite_real(
            self.comparison_tolerance_percent, "comparison tolerance percent"
        )
        if (
            comparison_tolerance_percent < 0
            or comparison_tolerance_percent > 100
        ):
            raise PopulationReachValidationError(
                "comparison tolerance percent must be between 0 and 100"
            )
        borderline_distance_tolerance_m = _require_finite_real(
            self.borderline_distance_tolerance_m, "borderline distance tolerance"
        )
        if borderline_distance_tolerance_m < 0:
            raise PopulationReachValidationError(
                "borderline distance tolerance must be a finite non-negative metre value"
            )
        object.__setattr__(self, "corridor_distances_m", distances)
        object.__setattr__(self, "comparison_tolerance_percent", comparison_tolerance_percent)
        object.__setattr__(
            self, "borderline_distance_tolerance_m", borderline_distance_tolerance_m
        )

    def canonical(self) -> dict[str, object]:
        return {
            "corridor_distances_m": list(self.corridor_distances_m),
            "comparison_tolerance_residents": self.comparison_tolerance_residents,
            "comparison_tolerance_percent": self.comparison_tolerance_percent,
            "borderline_distance_tolerance_m": self.borderline_distance_tolerance_m,
        }


@dataclass(frozen=True)
class PopulationReachSource:
    """Governance assertions required before OA data can be analysed."""

    source_id: str
    release: str
    effective_date: str | date
    licence: str
    permitted_uses: tuple[str, ...]
    known_limitations: tuple[str, ...]
    transformation_lineage: tuple[str, ...]
    source_uri: str
    version: str
    content_sha256: str
    current_development_evidence: CurrentDevelopmentEvidence | None = None
    current_development_evidence_id: str | None = None
    geography: str = ONS_2021_OA_GEOGRAPHY
    population_measure: str = WHOLE_OA_USUAL_RESIDENTS
    centroid_measure: str = POPULATION_WEIGHTED_CENTROID

    def __post_init__(self) -> None:
        for field in ("source_id", "release", "licence"):
            _require_non_blank_string(getattr(self, field), field)
        effective_date = _parse_iso_date(self.effective_date, "effective date")
        if not self.content_sha256:
            raise PopulationReachValidationError(
                "governed source requires a content SHA-256"
            )
        _require_non_blank_string(self.source_uri, "source URI")
        _require_non_blank_string(self.version, "version")
        if not _is_sha256(self.content_sha256):
            raise PopulationReachValidationError(
                "content SHA-256 must be a 64-character hexadecimal digest"
            )
        for field in ("permitted_uses", "known_limitations", "transformation_lineage"):
            values = getattr(self, field)
            if (
                not isinstance(values, (tuple, list))
                or not values
                or any(not isinstance(value, str) or not value.strip() for value in values)
            ):
                raise PopulationReachValidationError(f"{field.replace('_', ' ')} must be non-empty")
            object.__setattr__(self, field, tuple(values))
        if (
            self.current_development_evidence_id is not None
        ):
            _require_non_blank_string(
                self.current_development_evidence_id, "current development evidence ID"
            )
        if (
            self.current_development_evidence is not None
            and not isinstance(self.current_development_evidence, CurrentDevelopmentEvidence)
        ):
            raise PopulationReachValidationError(
                "current development evidence must be a governed availability statement"
            )
        object.__setattr__(self, "effective_date", effective_date.isoformat())
        if self.geography != ONS_2021_OA_GEOGRAPHY:
            raise PopulationReachValidationError(
                "population reach requires governed ONS 2021 Output Areas"
            )
        if self.population_measure != WHOLE_OA_USUAL_RESIDENTS:
            raise PopulationReachValidationError(
                "population reach requires whole-OA usual-resident counts"
            )
        if self.centroid_measure != POPULATION_WEIGHTED_CENTROID:
            raise PopulationReachValidationError(
                "population reach requires population-weighted centroids"
            )

    def canonical(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "release": self.release,
            "effective_date": self.effective_date,
            "licence": self.licence,
            "source_uri": self.source_uri,
            "version": self.version,
            "content_sha256": self.content_sha256,
            "permitted_uses": list(self.permitted_uses),
            "known_limitations": list(self.known_limitations),
            "transformation_lineage": list(self.transformation_lineage),
            "current_development_evidence_id": self.current_development_evidence_id,
            "current_development_evidence": (
                None
                if self.current_development_evidence is None
                else self.current_development_evidence.canonical()
            ),
            "geography": self.geography,
            "population_measure": self.population_measure,
            "centroid_measure": self.centroid_measure,
        }


@dataclass(frozen=True)
class PopulationReachColumns:
    """Column mapping for a governed OA GeoDataFrame and route options."""

    oa_id: str = "OA21CD"
    usual_residents: str = "usual_residents"
    population_weighted_centroid: str = "population_weighted_centroid"
    option_id: str = "option_id"

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not value.strip():
                raise PopulationReachValidationError(f"{name} column name must not be blank")


@dataclass(frozen=True)
class PopulationReachRecord:
    """One OA evaluated once for one option and corridor distance."""

    option_id: str
    corridor_distance_m: float
    oa_id: str
    usual_residents: int
    inside_area_definition: bool
    captured: bool
    distance_to_route_m: float
    decision_distance_to_route_m: float
    distance_to_corridor_boundary_m: float
    decision_distance_to_corridor_boundary_m: float
    borderline: bool

    def canonical(self) -> dict[str, object]:
        return {
            "option_id": self.option_id,
            "corridor_distance_m": self.corridor_distance_m,
            "oa_id": self.oa_id,
            "usual_residents": self.usual_residents,
            "inside_area_definition": self.inside_area_definition,
            "captured": self.captured,
            "distance_to_route_m": self.distance_to_route_m,
            "decision_distance_to_route_m": self.decision_distance_to_route_m,
            "distance_to_corridor_boundary_m": self.distance_to_corridor_boundary_m,
            "decision_distance_to_corridor_boundary_m": (
                self.decision_distance_to_corridor_boundary_m
            ),
            "borderline": self.borderline,
        }


@dataclass(frozen=True)
class PopulationReachSummary:
    """Captured residents for an option at one reporting distance."""

    option_id: str
    corridor_distance_m: float
    total_residents: int
    inside_area_residents: int
    outside_area_residents: int
    shared_residents: int
    option_exclusive_residents: int
    captured_oa_ids: tuple[str, ...]
    shared_oa_ids: tuple[str, ...]
    option_exclusive_oa_ids: tuple[str, ...]

    def canonical(self) -> dict[str, object]:
        return {
            "option_id": self.option_id,
            "corridor_distance_m": self.corridor_distance_m,
            "total_residents": self.total_residents,
            "inside_area_residents": self.inside_area_residents,
            "outside_area_residents": self.outside_area_residents,
            "shared_residents": self.shared_residents,
            "option_exclusive_residents": self.option_exclusive_residents,
            "captured_oa_ids": list(self.captured_oa_ids),
            "shared_oa_ids": list(self.shared_oa_ids),
            "option_exclusive_oa_ids": list(self.option_exclusive_oa_ids),
        }


@dataclass(frozen=True)
class PopulationReachSensitivity:
    """Deterministic warnings about close or scale-dependent rankings."""

    corridor_distance_m: float
    option_ranking: tuple[str, ...]
    margin_to_next_residents: int | None
    within_tolerance: bool
    margin_dominated_by_borderline_oa: bool
    ordering_flips_from_first_distance: bool
    sensitive: bool
    missing_current_development_evidence: bool
    borderline_oa_ids: tuple[str, ...]
    individually_decisive_borderline_oa_ids: tuple[str, ...]

    def canonical(self) -> dict[str, object]:
        return {
            "corridor_distance_m": self.corridor_distance_m,
            "option_ranking": list(self.option_ranking),
            "margin_to_next_residents": self.margin_to_next_residents,
            "within_tolerance": self.within_tolerance,
            "margin_dominated_by_borderline_oa": self.margin_dominated_by_borderline_oa,
            "ordering_flips_from_first_distance": self.ordering_flips_from_first_distance,
            "sensitive": self.sensitive,
            "missing_current_development_evidence": self.missing_current_development_evidence,
            "borderline_oa_ids": list(self.borderline_oa_ids),
            "individually_decisive_borderline_oa_ids": list(
                self.individually_decisive_borderline_oa_ids
            ),
        }


@dataclass(frozen=True)
class PopulationReachAssessment:
    """Canonical, reproducible population-reach evidence for route options."""

    assessment_id: str
    source: PopulationReachSource
    profile: PopulationReachProfile
    area_definition_sha256: str
    option_geometries: tuple[PopulationReachOptionGeometry, ...]
    coordinate_transformation_lineage: tuple[str, ...]
    warnings: tuple[str, ...]
    prohibited_claims: tuple[str, ...]
    records: tuple[PopulationReachRecord, ...]
    summaries: tuple[PopulationReachSummary, ...]
    sensitivities: tuple[PopulationReachSensitivity, ...]

    def canonical(self) -> dict[str, object]:
        return {
            "assessment_id": self.assessment_id,
            "source": self.source.canonical(),
            "profile": self.profile.canonical(),
            "area_definition_sha256": self.area_definition_sha256,
            "option_geometries": [option.canonical() for option in self.option_geometries],
            "coordinate_transformation_lineage": list(self.coordinate_transformation_lineage),
            "warnings": list(self.warnings),
            "prohibited_claims": list(self.prohibited_claims),
            "records": [record.canonical() for record in self.records],
            "summaries": [summary.canonical() for summary in self.summaries],
            "sensitivities": [sensitivity.canonical() for sensitivity in self.sensitivities],
        }


@dataclass(frozen=True)
class PopulationReachOptionGeometry:
    """Canonical identity and measured length for a dissolved route option."""

    option_id: str
    geometry_sha256: str
    length_m: float

    def canonical(self) -> dict[str, object]:
        return {
            "option_id": self.option_id,
            "geometry_sha256": self.geometry_sha256,
            "length_m": self.length_m,
        }


def compile_population_reach(
    route_options: gpd.GeoDataFrame,
    output_areas: gpd.GeoDataFrame,
    area_definition: gpd.GeoDataFrame,
    *,
    source: PopulationReachSource,
    profile: PopulationReachProfile | None = None,
    columns: PopulationReachColumns | None = None,
) -> PopulationReachAssessment:
    """Compile geometric population reach for a finite set of route options.

    `route_options`, `output_areas` and `area_definition` need explicit CRSs.
    They are projected to British National Grid for metre measurements. Each
    route-option geometry may have several rows; rows with the same option ID
    form one corridor before OA centroids are measured.
    """

    profile = profile or PopulationReachProfile()
    columns = columns or PopulationReachColumns()
    _validate_route_options(route_options, columns)
    _validate_output_areas(output_areas, columns)
    _validate_area_definition(area_definition)

    measurement_routes = _to_bng(route_options, "route options")
    measurement_oas = _to_bng(output_areas, "output areas")
    measurement_pwc = _project_population_weighted_centroids(output_areas, columns)
    measurement_area = _to_bng(area_definition, "area definition")
    measurement_area_geometry = unary_union(list(measurement_area.geometry))
    if measurement_area_geometry.is_empty:
        raise PopulationReachValidationError("area definition must not be empty")

    identity_routes = _canonicalize_frame_geometry(measurement_routes)
    identity_area_geometry = _canonicalize_geometry(measurement_area_geometry)
    measurement_options = _dissolved_options(measurement_routes, columns.option_id)
    identity_options = _dissolved_options(identity_routes, columns.option_id)
    if tuple(option_id for option_id, _ in measurement_options) != tuple(
        option_id for option_id, _ in identity_options
    ):
        raise PopulationReachValidationError(
            "measurement and identity route options must have matching IDs"
        )
    option_geometries = tuple(
        PopulationReachOptionGeometry(
            option_id=option_id,
            geometry_sha256=_geometry_sha256(geometry),
            length_m=_canonical_measurement(float(geometry.length)),
        )
        for option_id, geometry in identity_options
    )
    oa_rows = _canonical_oa_rows(measurement_oas, measurement_pwc, columns)
    records = _measure_records(
        measurement_options,
        oa_rows,
        measurement_area_geometry,
        profile,
    )
    _assert_unique_record_keys(records)
    summaries = _summarise_records(records)
    missing_current_development_evidence = _missing_current_development_evidence(source)
    sensitivities = _derive_sensitivities(
        records,
        summaries,
        profile,
        missing_current_development_evidence=missing_current_development_evidence,
    )
    warnings = _current_development_warnings(source)
    coordinate_transformation_lineage = _coordinate_transformation_lineage()
    canonical_without_id = {
        "source": source.canonical(),
        "profile": profile.canonical(),
        "area_definition_sha256": _geometry_sha256(identity_area_geometry),
        "option_geometries": [option.canonical() for option in option_geometries],
        "coordinate_transformation_lineage": list(coordinate_transformation_lineage),
        "warnings": list(warnings),
        "prohibited_claims": list(PROHIBITED_CLAIMS),
        "records": [record.canonical() for record in records],
        "summaries": [summary.canonical() for summary in summaries],
        "sensitivities": [sensitivity.canonical() for sensitivity in sensitivities],
    }
    digest = hashlib.sha256(_canonical_json(canonical_without_id).encode("utf-8")).hexdigest()
    return PopulationReachAssessment(
        assessment_id=f"population-reach-v1-{digest[:16]}",
        source=source,
        profile=profile,
        area_definition_sha256=_geometry_sha256(identity_area_geometry),
        option_geometries=option_geometries,
        coordinate_transformation_lineage=coordinate_transformation_lineage,
        warnings=warnings,
        prohibited_claims=PROHIBITED_CLAIMS,
        records=tuple(records),
        summaries=tuple(summaries),
        sensitivities=tuple(sensitivities),
    )


def _validate_route_options(frame: gpd.GeoDataFrame, columns: PopulationReachColumns) -> None:
    _validate_geodataframe(frame, "route options")
    _require_columns(frame, (columns.option_id,), "route options")
    declared_option_ids = list(frame[columns.option_id])
    option_ids = [
        _canonical_route_option_id(value)
        for value in declared_option_ids
    ]
    declarations_by_canonical_id: dict[str, set[str]] = defaultdict(set)
    for declared, canonical in zip(
        declared_option_ids,
        option_ids,
        strict=True,
    ):
        declarations_by_canonical_id[canonical].add(declared)
    if any(
        len(declarations) > 1
        for declarations in declarations_by_canonical_id.values()
    ):
        raise PopulationReachValidationError(
            "route option IDs must not collide after whitespace canonicalisation"
        )
    if any(
        value != option_id
        for value, option_id in zip(
            declared_option_ids,
            option_ids,
            strict=True,
        )
    ):
        raise PopulationReachValidationError(
            "route option IDs must be canonical strings without surrounding whitespace"
        )
    if any(
        not _is_non_empty_geometry_type(geometry, {"LineString", "MultiLineString"})
        for geometry in frame.geometry
    ):
        raise PopulationReachValidationError("route options must contain non-empty line geometries")
    _require_finite_geometry_coordinates(frame.geometry, "route options")
    if any(not _is_usable_line(geometry) for geometry in frame.geometry):
        raise PopulationReachValidationError(
            "route options must contain valid positive-length line geometries"
        )


def _validate_output_areas(frame: gpd.GeoDataFrame, columns: PopulationReachColumns) -> None:
    _validate_geodataframe(frame, "output areas")
    _require_columns(
        frame,
        (columns.oa_id, columns.usual_residents, columns.population_weighted_centroid),
        "output areas",
    )
    if frame[columns.oa_id].isna().any() or any(
        not _canonical_oa_id(value) for value in frame[columns.oa_id]
    ):
        raise PopulationReachValidationError("OA IDs must be present and non-blank")
    canonical_ids = [_canonical_oa_id(value) for value in frame[columns.oa_id]]
    if len(set(canonical_ids)) != len(canonical_ids):
        raise PopulationReachValidationError("canonical OA IDs must be unique")
    if any(
        not _is_non_empty_geometry_type(geometry, {"Polygon", "MultiPolygon"})
        for geometry in frame.geometry
    ):
        raise PopulationReachValidationError(
            "output areas must contain non-empty polygon geometries"
        )
    _require_finite_geometry_coordinates(frame.geometry, "output areas")
    if any(not _is_usable_polygon(geometry) for geometry in frame.geometry):
        raise PopulationReachValidationError(
            "output areas must contain valid positive-area polygon geometries"
        )
    for value in frame[columns.usual_residents]:
        if not _is_integral_non_negative(value):
            raise PopulationReachValidationError(
                "usual residents must be whole non-negative numbers"
            )
    for value in frame[columns.population_weighted_centroid]:
        if not _is_non_empty_geometry_type(value, {"Point"}):
            raise PopulationReachValidationError(
                "population-weighted centroids must contain non-empty point geometries"
            )
    _require_finite_geometry_coordinates(
        frame[columns.population_weighted_centroid], "population-weighted centroids"
    )
    if any(
        not _is_usable_point(value)
        for value in frame[columns.population_weighted_centroid]
    ):
        raise PopulationReachValidationError(
            "population-weighted centroids must contain valid point geometries"
        )


def _validate_area_definition(frame: gpd.GeoDataFrame) -> None:
    _validate_geodataframe(frame, "area definition")
    if any(
        not _is_non_empty_geometry_type(geometry, {"Polygon", "MultiPolygon"})
        for geometry in frame.geometry
    ):
        raise PopulationReachValidationError(
            "area definition must contain non-empty polygon geometries"
        )
    _require_finite_geometry_coordinates(frame.geometry, "area definition")
    if any(not _is_usable_polygon(geometry) for geometry in frame.geometry):
        raise PopulationReachValidationError(
            "area definition must contain valid positive-area polygon geometries"
        )


def _validate_geodataframe(frame: gpd.GeoDataFrame, label: str) -> None:
    if not isinstance(frame, gpd.GeoDataFrame):
        raise PopulationReachValidationError(f"{label} must be a GeoDataFrame")
    if frame.empty:
        raise PopulationReachValidationError(f"{label} must not be empty")
    if frame.crs is None:
        raise PopulationReachValidationError(f"{label} must declare a CRS")
    try:
        CRS.from_user_input(frame.crs)
    except Exception as error:  # pragma: no cover - geopandas normally rejects this first
        raise PopulationReachValidationError(f"{label} has an invalid CRS") from error


def _require_columns(frame: gpd.GeoDataFrame, columns: Iterable[str], label: str) -> None:
    missing = sorted(set(columns).difference(frame.columns))
    if missing:
        raise PopulationReachValidationError(
            f"{label} missing required columns: {', '.join(missing)}"
        )


def _to_bng(frame: gpd.GeoDataFrame, label: str) -> gpd.GeoDataFrame:
    try:
        projected = frame.to_crs(epsg=BRITISH_NATIONAL_GRID_EPSG)
    except Exception as error:
        raise PopulationReachValidationError(
            f"{label} could not be projected to EPSG:27700"
        ) from error
    _require_finite_geometry_coordinates(projected.geometry, f"projected {label}")
    return projected


def _canonicalize_frame_geometry(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Round projected geometry before topology or metre policy comparisons."""
    canonical = frame.copy()
    canonical.geometry = canonical.geometry.map(_canonicalize_geometry)
    return canonical


def _project_population_weighted_centroids(
    frame: gpd.GeoDataFrame, columns: PopulationReachColumns
) -> gpd.GeoSeries:
    """Project the non-active PWC geometry column with the OA frame's declared CRS.

    GeoPandas only reprojects the active geometry column. Treating the PWC
    column as already in BNG would silently compare degrees to metres for
    WGS84 OA inputs, so this independently constructs a GeoSeries using the
    governed OA CRS and fails closed when it cannot be transformed.
    """

    try:
        centroids = gpd.GeoSeries(
            frame[columns.population_weighted_centroid], index=frame.index, crs=frame.crs
        )
        projected = centroids.to_crs(epsg=BRITISH_NATIONAL_GRID_EPSG)
    except Exception as error:
        raise PopulationReachValidationError(
            "population-weighted centroids could not be projected from the output-area CRS "
            "to EPSG:27700"
        ) from error
    _require_finite_geometry_coordinates(projected, "projected population-weighted centroids")
    return projected


def _dissolved_options(
    frame: gpd.GeoDataFrame, option_id_column: str
) -> tuple[tuple[str, BaseGeometry], ...]:
    grouped: dict[str, list[BaseGeometry]] = defaultdict(list)
    for _, row in frame.iterrows():
        grouped[_canonical_route_option_id(row[option_id_column])].append(row.geometry)
    return tuple(
        (option_id, unary_union(geometries)) for option_id, geometries in sorted(grouped.items())
    )


def _canonical_oa_rows(
    frame: gpd.GeoDataFrame, centroids: gpd.GeoSeries, columns: PopulationReachColumns
) -> tuple[tuple[str, int, BaseGeometry, BaseGeometry], ...]:
    rows: list[tuple[str, int, BaseGeometry, BaseGeometry]] = []
    for (_, row), centroid in zip(frame.iterrows(), centroids, strict=True):
        rows.append(
            (
                _canonical_oa_id(row[columns.oa_id]),
                int(row[columns.usual_residents]),
                row.geometry,
                centroid,
            )
        )
    canonical_rows = tuple(sorted(rows, key=lambda row: row[0]))
    if len({row[0] for row in canonical_rows}) != len(canonical_rows):
        raise PopulationReachValidationError("canonical OA IDs must be unique")
    return canonical_rows


def _measure_records(
    options: tuple[tuple[str, BaseGeometry], ...],
    oa_rows: tuple[tuple[str, int, BaseGeometry, BaseGeometry], ...],
    area_geometry: BaseGeometry,
    profile: PopulationReachProfile,
) -> list[PopulationReachRecord]:
    records: list[PopulationReachRecord] = []
    for option_id, geometry in options:
        for corridor_distance_m in profile.corridor_distances_m:
            for oa_id, residents, _oa_geometry, centroid in oa_rows:
                raw_distance_to_route_m = _require_finite_real(
                    float(centroid.distance(geometry)), "raw route distance"
                )
                canonical_corridor_distance_m = _canonical_measurement(corridor_distance_m)
                raw_boundary_distance_m = _require_finite_real(
                    abs(raw_distance_to_route_m - corridor_distance_m),
                    "raw corridor boundary distance",
                )
                captured = _within_corridor(raw_distance_to_route_m, corridor_distance_m)
                records.append(
                    PopulationReachRecord(
                        option_id=option_id,
                        corridor_distance_m=canonical_corridor_distance_m,
                        oa_id=oa_id,
                        usual_residents=residents,
                        inside_area_definition=bool(area_geometry.covers(centroid)),
                        captured=captured,
                        distance_to_route_m=_canonical_measurement(raw_distance_to_route_m),
                        decision_distance_to_route_m=_canonical_decision_measurement(
                            raw_distance_to_route_m,
                            corridor_distance_m,
                        ),
                        distance_to_corridor_boundary_m=_canonical_measurement(
                            raw_boundary_distance_m
                        ),
                        decision_distance_to_corridor_boundary_m=(
                            _canonical_decision_measurement(
                                raw_boundary_distance_m,
                                profile.borderline_distance_tolerance_m,
                            )
                        ),
                        borderline=_within_corridor(
                            raw_boundary_distance_m,
                            profile.borderline_distance_tolerance_m,
                        ),
                    )
                )
    return sorted(
        records, key=lambda record: (record.option_id, record.corridor_distance_m, record.oa_id)
    )


def _assert_unique_record_keys(records: Iterable[PopulationReachRecord]) -> None:
    materialised = tuple(records)
    keys = {
        (record.option_id, record.corridor_distance_m, record.oa_id) for record in materialised
    }
    if len(keys) != len(materialised):
        raise PopulationReachValidationError(
            "population reach must contain one record for each option, distance and OA"
        )


def _summarise_records(records: list[PopulationReachRecord]) -> list[PopulationReachSummary]:
    by_distance: dict[float, dict[str, list[PopulationReachRecord]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for record in records:
        by_distance[record.corridor_distance_m].setdefault(record.option_id, [])
        if record.captured:
            by_distance[record.corridor_distance_m][record.option_id].append(record)
    summaries: list[PopulationReachSummary] = []
    for distance_m, by_option in sorted(by_distance.items()):
        oa_option_count: dict[str, int] = defaultdict(int)
        for captured in by_option.values():
            for oa_id in {record.oa_id for record in captured}:
                oa_option_count[oa_id] += 1
        for option_id, captured in sorted(by_option.items()):
            by_id = {record.oa_id: record for record in captured}
            shared_ids = tuple(sorted(oa_id for oa_id in by_id if oa_option_count[oa_id] > 1))
            exclusive_ids = tuple(sorted(oa_id for oa_id in by_id if oa_option_count[oa_id] == 1))
            captured_ids = tuple(sorted(by_id))
            summaries.append(
                PopulationReachSummary(
                    option_id=option_id,
                    corridor_distance_m=distance_m,
                    total_residents=sum(record.usual_residents for record in by_id.values()),
                    inside_area_residents=sum(
                        record.usual_residents
                        for record in by_id.values()
                        if record.inside_area_definition
                    ),
                    outside_area_residents=sum(
                        record.usual_residents
                        for record in by_id.values()
                        if not record.inside_area_definition
                    ),
                    shared_residents=sum(by_id[oa_id].usual_residents for oa_id in shared_ids),
                    option_exclusive_residents=sum(
                        by_id[oa_id].usual_residents for oa_id in exclusive_ids
                    ),
                    captured_oa_ids=captured_ids,
                    shared_oa_ids=shared_ids,
                    option_exclusive_oa_ids=exclusive_ids,
                )
            )
    return sorted(summaries, key=lambda summary: (summary.corridor_distance_m, summary.option_id))


def _derive_sensitivities(
    records: list[PopulationReachRecord],
    summaries: list[PopulationReachSummary],
    profile: PopulationReachProfile,
    *,
    missing_current_development_evidence: bool,
) -> list[PopulationReachSensitivity]:
    summaries_by_distance: dict[float, list[PopulationReachSummary]] = defaultdict(list)
    for summary in summaries:
        summaries_by_distance[summary.corridor_distance_m].append(summary)
    records_by_distance: dict[float, list[PopulationReachRecord]] = defaultdict(list)
    for record in records:
        records_by_distance[record.corridor_distance_m].append(record)
    first_ranking: tuple[str, ...] | None = None
    sensitivities: list[PopulationReachSensitivity] = []
    for distance_m in profile.corridor_distances_m:
        ranked = tuple(
            summary.option_id
            for summary in sorted(
                summaries_by_distance[distance_m],
                key=lambda summary: (-summary.total_residents, summary.option_id),
            )
        )
        if first_ranking is None:
            first_ranking = ranked
        ranked_summaries = sorted(
            summaries_by_distance[distance_m],
            key=lambda summary: (-summary.total_residents, summary.option_id),
        )
        margin = (
            ranked_summaries[0].total_residents - ranked_summaries[1].total_residents
            if len(ranked_summaries) > 1
            else None
        )
        relevant_ids = set(ranked[:2])
        by_option_and_oa = {
            (record.option_id, record.oa_id): record
            for record in records_by_distance[distance_m]
            if record.option_id in relevant_ids
        }
        borderline_ids = tuple(
            sorted({record.oa_id for record in by_option_and_oa.values() if record.borderline})
        )
        percent_tolerance = (
            0.0
            if not ranked_summaries
            else ranked_summaries[0].total_residents * profile.comparison_tolerance_percent / 100
        )
        near_equivalence_tolerance = max(
            profile.comparison_tolerance_residents, percent_tolerance
        )
        decisive_ids: tuple[str, ...] = ()
        if margin is not None and len(ranked) > 1:
            winner, runner_up = ranked[:2]
            decisive_ids = tuple(
                sorted(
                    oa_id
                    for oa_id in {record.oa_id for record in by_option_and_oa.values()}
                    if _is_individually_decisive_borderline_oa(
                        by_option_and_oa[(winner, oa_id)],
                        by_option_and_oa[(runner_up, oa_id)],
                        margin,
                        near_equivalence_tolerance,
                    )
                )
            )
        within_tolerance = margin is not None and margin <= near_equivalence_tolerance
        borderline_dominates = bool(decisive_ids)
        ordering_flips = ranked != first_ranking
        sensitivities.append(
            PopulationReachSensitivity(
                corridor_distance_m=distance_m,
                option_ranking=ranked,
                margin_to_next_residents=margin,
                within_tolerance=within_tolerance,
                margin_dominated_by_borderline_oa=borderline_dominates,
                ordering_flips_from_first_distance=ordering_flips,
                sensitive=(
                    within_tolerance
                    or borderline_dominates
                    or ordering_flips
                    or missing_current_development_evidence
                ),
                missing_current_development_evidence=missing_current_development_evidence,
                borderline_oa_ids=borderline_ids,
                individually_decisive_borderline_oa_ids=decisive_ids,
            )
        )
    return sensitivities


def _is_individually_decisive_borderline_oa(
    winner_record: PopulationReachRecord,
    runner_up_record: PopulationReachRecord,
    margin: int,
    near_equivalence_tolerance: float,
) -> bool:
    """Whether one near-corridor OA, on its own, could alter the leading pair.

    An OA captured by both options has no effect on their margin even when it
    is borderline. A winner-only OA can reduce the leading margin in either
    direction: removal where it lies near the winner's inner boundary, or
    addition where it lies just outside the runner-up's boundary. A runner-up
    exclusive OA remains excluded because removing it can only strengthen the
    winner. Each OA is tested alone; residents are never pooled across several
    merely borderline OAs.
    """

    if not winner_record.captured or runner_up_record.captured:
        return False
    required_shift = (
        margin
        if margin <= near_equivalence_tolerance
        else margin - near_equivalence_tolerance
    )
    return (
        (winner_record.borderline or runner_up_record.borderline)
        and winner_record.usual_residents > 0
        and winner_record.usual_residents >= required_shift
    )


def _canonical_route_option_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PopulationReachValidationError(
            "route option IDs must be strict non-blank strings"
        )
    return value.strip()


def _canonical_oa_id(value: object) -> str:
    return str(value).strip()


def _require_non_blank_string(value: object, field: str) -> str:
    """Reject coercion so governed metadata retains its exact declared value."""

    if not isinstance(value, str) or not value.strip():
        raise PopulationReachValidationError(f"{field} must be a non-blank string")
    return value


def _parse_iso_date(value: object, field: str) -> date:
    if type(value) is date:
        return value
    if not isinstance(value, str):
        raise PopulationReachValidationError(f"{field} must be an ISO-8601 date")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise PopulationReachValidationError(f"{field} must be an ISO-8601 date") from error


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _coordinate_transformation_lineage() -> tuple[str, ...]:
    return (
        "Route options and area definition are transformed from their declared CRS "
        "to EPSG:27700; unrounded finite projected geometry determines policy membership.",
        "Population-weighted centroids are separately transformed from the declared "
        "output-area CRS to EPSG:27700.",
        "A separate five-millimetre BNG identity grid stabilises geometry hashes and "
        "never determines 500 m or 1000 m membership.",
    )


def _geometry_sha256(geometry: BaseGeometry) -> str:
    """Digest a normalised BNG geometry on the canonical five-millimetre grid.

    The grid has a 2.5 mm canonical projection tolerance. It makes equivalent
    WGS84 and BNG inputs canonical after reprojection while retaining
    sub-centimetre distinctions at the exact 500 m and 1000 m policy
    boundaries.
    """

    rounded = _canonicalize_geometry(geometry)
    normalized = rounded.normalize()
    if normalized is None:  # Shapely 1.x mutates in place; Shapely 2 returns a geometry.
        normalized = rounded
    return hashlib.sha256(wkb_dumps(normalized, hex=False)).hexdigest()


def _round_coordinates_to_millimetre(
    x: float | Iterable[float], y: float | Iterable[float], z: object = None
) -> tuple[object, ...]:
    rounded_x = _round_coordinate(x)
    rounded_y = _round_coordinate(y)
    if z is None:
        return rounded_x, rounded_y
    return rounded_x, rounded_y, _round_coordinate(z)


def _round_coordinate(value: float | Iterable[float]) -> float | tuple[float, ...]:
    if isinstance(value, (str, bytes)):
        return _canonical_projected_coordinate(float(value))
    try:
        return tuple(_canonical_projected_coordinate(float(component)) for component in value)
    except TypeError:
        return _canonical_projected_coordinate(float(value))


def _is_non_empty_geometry_type(
    geometry: Any, geometry_types: set[str]
) -> bool:
    return bool(
        isinstance(geometry, BaseGeometry)
        and not geometry.is_empty
        and geometry.geom_type in geometry_types
    )


def _is_usable_line(geometry: Any) -> bool:
    return bool(
        _is_non_empty_geometry_type(geometry, {"LineString", "MultiLineString"})
        and geometry.is_valid
        and math.isfinite(float(geometry.length))
        and geometry.length > 0
    )


def _is_usable_polygon(geometry: Any) -> bool:
    return bool(
        _is_non_empty_geometry_type(geometry, {"Polygon", "MultiPolygon"})
        and geometry.is_valid
        and math.isfinite(float(geometry.area))
        and geometry.area > 0
    )


def _is_usable_point(geometry: Any) -> bool:
    return bool(
        _is_non_empty_geometry_type(geometry, {"Point"})
        and geometry.is_valid
    )


def _is_integral_non_negative(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, Integral) and value >= 0


def _require_finite_real(value: object, field: str) -> float:
    """Return a finite real without accepting bools or string coercion."""

    if isinstance(value, bool) or not isinstance(value, Real):
        raise PopulationReachValidationError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise PopulationReachValidationError(f"{field} must be a finite number")
    return _normalise_mathematical_zero(number)


def _normalise_mathematical_zero(value: float) -> float:
    return 0.0 if value == 0 else value


def _iter_geometry_coordinates(geometry: BaseGeometry) -> Iterator[tuple[float, ...]]:
    if geometry.geom_type == "Polygon":
        yield from geometry.exterior.coords
        for interior in geometry.interiors:
            yield from interior.coords
        return
    if hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _iter_geometry_coordinates(part)
        return
    yield from geometry.coords


def _geometry_coordinates_are_finite(geometry: BaseGeometry) -> bool:
    try:
        coordinates = tuple(_iter_geometry_coordinates(geometry))
    except (AttributeError, NotImplementedError, TypeError, ValueError):
        return False
    return bool(coordinates) and all(
        all(isinstance(component, Real) and math.isfinite(float(component)) for component in point)
        for point in coordinates
    )


def _require_finite_geometry_coordinates(
    geometries: Iterable[BaseGeometry], label: str
) -> None:
    if any(not _geometry_coordinates_are_finite(geometry) for geometry in geometries):
        raise PopulationReachValidationError(f"{label} must contain only finite coordinates")


def _canonicalize_geometry(geometry: BaseGeometry) -> BaseGeometry:
    """Snap projected geometry once to the governed five-millimetre identity grid."""

    if not _geometry_coordinates_are_finite(geometry):
        raise PopulationReachValidationError("geometry must contain only finite coordinates")
    canonical = transform(_round_coordinates_to_millimetre, geometry)
    if not _geometry_coordinates_are_finite(canonical):
        raise PopulationReachValidationError(
            "canonical geometry must contain only finite coordinates"
        )
    return canonical


def _canonical_projected_coordinate(value: float) -> float:
    """Snap BNG identity coordinates with a projection-stable lower tie rule."""

    number = _require_finite_real(value, "projected coordinate")
    lower_grid_step = math.floor(number / CANONICAL_PROJECTED_GRID_M)
    lower = lower_grid_step * CANONICAL_PROJECTED_GRID_M
    midpoint = lower + CANONICAL_PROJECTED_GRID_M / 2
    grid_step = (
        lower_grid_step
        if number <= midpoint + CANONICAL_PROJECTED_TIE_BAND_M
        else lower_grid_step + 1
    )
    canonical = _normalise_mathematical_zero(
        round(
            grid_step * CANONICAL_PROJECTED_GRID_M,
            CANONICAL_PROJECTED_DECIMALS,
        )
    )
    if abs(canonical - number) > (
        CANONICAL_PROJECTED_TOLERANCE_M + POLICY_COMPARISON_EPSILON_M
    ):
        raise PopulationReachValidationError(
            "projected coordinate exceeded the canonical projection tolerance"
        )
    return canonical


def _canonical_measurement(value: float) -> float:
    """Return deterministic BNG metre values used for identity and policy tests."""
    number = _require_finite_real(value, "measurement")
    return _normalise_mathematical_zero(round(number, CANONICAL_PROJECTED_DECIMALS))


def _canonical_decision_measurement(value: float, boundary_m: float) -> float:
    """Publish the least precision that exactly explains a literal boundary decision."""

    number = _require_finite_real(value, "decision measurement")
    boundary = _require_finite_real(boundary_m, "decision boundary")
    published = _canonical_measurement(number)
    raw_decision = _within_corridor(number, boundary)
    if (
        _within_corridor(published, boundary) == raw_decision
        and not (number != 0 and published == 0)
    ):
        return published
    for decimal_places in range(CANONICAL_PROJECTED_DECIMALS + 1, 16):
        candidate = _normalise_mathematical_zero(round(number, decimal_places))
        if (
            _within_corridor(candidate, boundary) == raw_decision
            and not (number != 0 and candidate == 0)
        ):
            return candidate
    return _normalise_mathematical_zero(number)


def _within_corridor(distance_m: float, radius_m: float) -> bool:
    """Apply the literal finite policy boundary without widening the corridor."""

    distance = _require_finite_real(distance_m, "distance")
    radius = _require_finite_real(radius_m, "radius")
    return distance <= radius


def _missing_current_development_evidence(source: PopulationReachSource) -> bool:
    """Only a hash-bound governed conclusion may clear development sensitivity."""
    evidence = source.current_development_evidence
    return evidence is None or evidence.conclusion not in CURRENT_DEVELOPMENT_CLEAR_CONCLUSIONS


def _current_development_warnings(source: PopulationReachSource) -> tuple[str, ...]:
    if not _missing_current_development_evidence(source):
        return ()
    if source.current_development_evidence is None:
        return (CURRENT_DEVELOPMENT_ABSENT_WARNING,)
    return (CURRENT_DEVELOPMENT_OUTSTANDING_WARNING,)


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            _normalise_canonical_numeric_zeros(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise PopulationReachValidationError(
            "canonical population reach evidence must contain only finite JSON values"
        ) from error


def _normalise_canonical_numeric_zeros(value: object) -> object:
    if isinstance(value, float):
        return _normalise_mathematical_zero(value)
    if isinstance(value, dict):
        return {
            key: _normalise_canonical_numeric_zeros(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_normalise_canonical_numeric_zeros(item) for item in value]
    return value
