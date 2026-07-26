"""Strict loaders for Preferred Strategic Alignment governed evidence.

The loaders intentionally have a small, file-only boundary.  They do not
discover files, retrieve data, infer access, or make safety or demand claims.
Each configured artifact is read exactly once, SHA-256 checked before it is
parsed, and retained as immutable lineage alongside the compiler input.

Supported formats are deliberately closed:

* OA geometry and population-weighted-centroid artifacts are GeoJSON feature
  collections with exactly ``type``, ``crs`` and ``features``. ``crs`` is
  either ``EPSG:4326`` or ``EPSG:27700``; feature properties contain only
  ``OA21CD``.
* usual-resident counts are ``{"records": [{"OA21CD": ..., "usual_residents": ...}]}``.
* school registers are ``{"schema": "satn-school-register/v1", "register":
  {"source_name": ..., "as_of": ...}, "schools": [{"school_id": ...,
  "name": ..., "phase": ...}]}``.
* strategic-destination admissions are ``{"schema":
  "satn-strategic-education-destination-admission/v1", "admissions":
  [{"record_id": ..., "record_version": ..., "strategic_destination_id": ...,
  "name": ..., "admitted_on": ..., "rationale": ...,
  "review_trigger": ..., "access_evidence_ids": [...]}]}``.

The absence of an optional configuration remains meaningful: no artifact is
loaded and no source snapshot is invented.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, Literal

import geopandas as gpd
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from satn.education_access import (
    EducationAccessSourceSnapshot,
    School,
    SchoolRegisterEvidence,
    StrategicEducationDestination,
    assess_education_access,
)
from satn.network_selection import (
    GovernedEvidenceArtifactConfig,
    PopulationReachEvidenceConfig,
    SchoolRegisterEvidenceConfig,
    StrategicEducationDestinationAdmissionConfig,
)
from satn.population_reach import PopulationReachColumns, PopulationReachSource
from satn.runtime_governance_contract import canonical_sha256


class GovernedEvidenceLoadError(ValueError):
    """A declared governed artifact cannot be safely used as compiler input."""


_OA_ID: Final = re.compile(r"^[EWNS][0-9]{8}$")
_SUPPORTED_CRS: Final = frozenset({"EPSG:4326", "EPSG:27700"})
_GEOJSON_ROOT_KEYS: Final = frozenset({"type", "crs", "features"})
_REGISTER_SCHEMA: Final = "satn-school-register/v1"
_ADMISSIONS_SCHEMA: Final = "satn-strategic-education-destination-admission/v1"

type JSONValue = dict[str, object] | list[object] | str | int | float | bool | None


@dataclass(frozen=True)
class GovernedArtifactLineage:
    """The declared identity and verified byte identity of one loaded artifact."""

    source_id: str
    path: Path
    release: str
    effective_date: date
    licence: str
    redistribution: Literal["public", "controlled", "aggregate-only"]
    content_sha256: str


@dataclass(frozen=True)
class PopulationReachEvidenceLoad:
    """OA evidence in the exact ``PopulationReachSource``/column contract."""

    source: PopulationReachSource
    output_areas: gpd.GeoDataFrame
    columns: PopulationReachColumns
    artifact_lineage: tuple[GovernedArtifactLineage, ...]


@dataclass(frozen=True)
class EducationAccessEvidenceLoad:
    """Current school/admission evidence in the education source-snapshot contract."""

    source_snapshot: EducationAccessSourceSnapshot
    school_register_lineage: GovernedArtifactLineage
    admissions_lineage: GovernedArtifactLineage | None


def load_population_reach_evidence(
    evidence: PopulationReachEvidenceConfig | None,
    *,
    base_directory: Path | None = None,
) -> PopulationReachEvidenceLoad | None:
    """Load all three declared OA artifacts, or preserve an absent configuration.

    No partial join is accepted: ONS OA IDs must be canonical, unique, and
    identical across geometry, population-weighted centroids and counts.
    """

    if evidence is None:
        return None
    geometry_lineage, geometry_payload = _read_artifact(
        evidence.output_area_geometry, base_directory=base_directory
    )
    centroid_lineage, centroid_payload = _read_artifact(
        evidence.population_weighted_centroids, base_directory=base_directory
    )
    counts_lineage, counts_payload = _read_artifact(
        evidence.usual_resident_counts, base_directory=base_directory
    )
    geometry_rows, crs = _load_geojson_rows(
        geometry_payload, label="output-area geometry", geometry_types={"Polygon", "MultiPolygon"}
    )
    centroid_rows, centroid_crs = _load_geojson_rows(
        centroid_payload,
        label="population-weighted centroids",
        geometry_types={"Point"},
    )
    if crs != centroid_crs:
        raise GovernedEvidenceLoadError(
            "OA geometry and population-weighted centroids use different CRS"
        )
    counts = _load_usual_resident_counts(counts_payload)
    geometry_ids = set(geometry_rows)
    centroid_ids = set(centroid_rows)
    count_ids = set(counts)
    if geometry_ids != centroid_ids or geometry_ids != count_ids:
        raise GovernedEvidenceLoadError(
            "OA artifacts must have a complete exact join on canonical OA21CD identifiers"
        )
    rows = [
        {
            "OA21CD": oa_id,
            "usual_residents": counts[oa_id],
            "population_weighted_centroid": centroid_rows[oa_id],
            "geometry": geometry_rows[oa_id],
        }
        for oa_id in sorted(geometry_ids)
    ]
    output_areas = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)
    lineages = (geometry_lineage, centroid_lineage, counts_lineage)
    source = _population_source(lineages)
    return PopulationReachEvidenceLoad(
        source=source,
        output_areas=output_areas,
        columns=PopulationReachColumns(),
        artifact_lineage=lineages,
    )


def load_education_access_evidence(
    school_register_evidence: SchoolRegisterEvidenceConfig | None,
    strategic_destination_admissions: StrategicEducationDestinationAdmissionConfig | None = None,
    *,
    base_directory: Path | None = None,
) -> EducationAccessEvidenceLoad | None:
    """Load current register/admissions data into an evidence-only source snapshot.

    The returned snapshot has no option evidence and no implied result about
    safety, suitability, demand, or independent access.  Such claims require
    separately typed input to ``assess_education_access``.
    """

    if school_register_evidence is None:
        if strategic_destination_admissions is not None:
            raise GovernedEvidenceLoadError(
                "strategic destination admissions require current school-register evidence"
            )
        return None
    register_lineage, register_payload = _read_artifact(
        school_register_evidence.school_register, base_directory=base_directory
    )
    register, schools = _load_school_register(register_payload, register_lineage)
    admissions_lineage: GovernedArtifactLineage | None = None
    destinations: tuple[StrategicEducationDestination, ...] = ()
    if strategic_destination_admissions is not None:
        admissions_lineage, admissions_payload = _read_artifact(
            strategic_destination_admissions.admissions, base_directory=base_directory
        )
        destinations = _load_admissions(admissions_payload, admissions_lineage)
    assessment = assess_education_access(
        register_evidence=register,
        schools=schools,
        strategic_destinations=destinations,
        option_evidence=(),
    )
    return EducationAccessEvidenceLoad(
        source_snapshot=assessment.source_snapshot,
        school_register_lineage=register_lineage,
        admissions_lineage=admissions_lineage,
    )


def _read_artifact(
    artifact: GovernedEvidenceArtifactConfig,
    *,
    base_directory: Path | None,
) -> tuple[GovernedArtifactLineage, JSONValue]:
    path = _resolve_declared_artifact_path(artifact.path, base_directory=base_directory)
    try:
        content = path.read_bytes()
    except OSError as error:
        raise GovernedEvidenceLoadError(
            f"declared governed artifact cannot be read: {path}"
        ) from error
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != artifact.content_sha256:
        raise GovernedEvidenceLoadError(
            f"declared governed artifact SHA-256 mismatch for {artifact.source_id!r}"
        )
    try:
        payload = json.loads(content.decode("utf-8"), parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise GovernedEvidenceLoadError(
            f"declared governed artifact is not strict UTF-8 JSON: {artifact.source_id!r}"
        ) from error
    _require_finite_json(payload, label=artifact.source_id)
    return (
        GovernedArtifactLineage(
            source_id=artifact.source_id,
            path=path,
            release=artifact.release,
            effective_date=artifact.effective_date,
            licence=artifact.licence,
            redistribution=artifact.redistribution,
            content_sha256=actual_sha256,
        ),
        payload,
    )


def _resolve_declared_artifact_path(path: Path, *, base_directory: Path | None) -> Path:
    if not isinstance(path, Path):  # defensive: Pydantic normally guarantees this
        raise GovernedEvidenceLoadError("governed artifact path must be a Path")
    if path.is_absolute():
        candidate = path
    elif base_directory is not None:
        candidate = base_directory / path
    else:
        raise GovernedEvidenceLoadError("relative governed artifact path requires base_directory")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise GovernedEvidenceLoadError(
            f"declared governed artifact path does not resolve: {candidate}"
        ) from error
    if candidate.is_symlink():
        raise GovernedEvidenceLoadError("declared governed artifact path must not be a symlink")
    if base_directory is not None:
        root = base_directory.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise GovernedEvidenceLoadError(
                "declared governed artifact path escapes base_directory"
            )
    if not resolved.is_file():
        raise GovernedEvidenceLoadError("declared governed artifact path must name a regular file")
    return resolved


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value {value!r} is forbidden")


def _require_exact_keys(value: object, keys: frozenset[str], *, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise GovernedEvidenceLoadError(f"{label} must contain exactly: {', '.join(sorted(keys))}")
    if any(not isinstance(key, str) for key in value):
        raise GovernedEvidenceLoadError(f"{label} must use string keys")
    return value


def _load_geojson_rows(
    payload: JSONValue,
    *,
    label: str,
    geometry_types: set[str],
) -> tuple[dict[str, BaseGeometry], str]:
    root = _require_exact_keys(payload, _GEOJSON_ROOT_KEYS, label=label)
    if root["type"] != "FeatureCollection":
        raise GovernedEvidenceLoadError(f"{label} must be a GeoJSON FeatureCollection")
    crs = root["crs"]
    if not isinstance(crs, str) or crs not in _SUPPORTED_CRS:
        raise GovernedEvidenceLoadError(f"{label} must declare supported CRS")
    features = root["features"]
    if not isinstance(features, list) or not features:
        raise GovernedEvidenceLoadError(f"{label} must contain a non-empty features array")
    rows: dict[str, BaseGeometry] = {}
    for position, feature in enumerate(features):
        item = _require_exact_keys(
            feature,
            frozenset({"type", "properties", "geometry"}),
            label=f"{label} feature {position}",
        )
        if item["type"] != "Feature":
            raise GovernedEvidenceLoadError(f"{label} feature {position} must have type Feature")
        properties = _require_exact_keys(
            item["properties"],
            frozenset({"OA21CD"}),
            label=f"{label} feature {position} properties",
        )
        oa_id = _canonical_oa_id(properties["OA21CD"], label=label)
        if oa_id in rows:
            raise GovernedEvidenceLoadError(f"{label} contains duplicate OA21CD {oa_id!r}")
        geometry = _parse_geometry(item["geometry"], label=f"{label} feature {position}")
        if geometry.geom_type not in geometry_types:
            raise GovernedEvidenceLoadError(
                f"{label} has unsupported geometry type {geometry.geom_type!r}"
            )
        if geometry.geom_type in {"Polygon", "MultiPolygon"} and geometry.area <= 0:
            raise GovernedEvidenceLoadError(f"{label} polygon geometry must have positive area")
        rows[oa_id] = geometry
    return rows, crs


def _parse_geometry(value: object, *, label: str) -> BaseGeometry:
    geometry_data = _require_exact_keys(
        value, frozenset({"type", "coordinates"}), label=f"{label} geometry"
    )
    try:
        geometry = shape(geometry_data)
    except Exception as error:
        raise GovernedEvidenceLoadError(f"{label} has malformed GeoJSON geometry") from error
    if geometry.is_empty or not geometry.is_valid:
        raise GovernedEvidenceLoadError(f"{label} geometry must be non-empty and valid")
    _require_finite_json(geometry_data, label=label)
    return geometry


def _load_usual_resident_counts(payload: JSONValue) -> dict[str, int]:
    root = _require_exact_keys(payload, frozenset({"records"}), label="usual-resident counts")
    records = root["records"]
    if not isinstance(records, list) or not records:
        raise GovernedEvidenceLoadError(
            "usual-resident counts must contain a non-empty records array"
        )
    result: dict[str, int] = {}
    for position, record in enumerate(records):
        row = _require_exact_keys(
            record,
            frozenset({"OA21CD", "usual_residents"}),
            label=f"usual-resident count record {position}",
        )
        oa_id = _canonical_oa_id(row["OA21CD"], label="usual-resident counts")
        residents = row["usual_residents"]
        if type(residents) is not int or residents < 0:
            raise GovernedEvidenceLoadError("usual_residents must be a whole non-negative integer")
        if oa_id in result:
            raise GovernedEvidenceLoadError(
                f"usual-resident counts contains duplicate OA21CD {oa_id!r}"
            )
        result[oa_id] = residents
    return result


def _load_school_register(
    payload: JSONValue, lineage: GovernedArtifactLineage
) -> tuple[SchoolRegisterEvidence, tuple[School, ...]]:
    root = _require_exact_keys(
        payload,
        frozenset({"schema", "register", "schools"}),
        label="school register",
    )
    if root["schema"] != _REGISTER_SCHEMA:
        raise GovernedEvidenceLoadError("school register has an unsupported schema")
    register_data = _require_exact_keys(
        root["register"], frozenset({"source_name", "as_of"}), label="school register metadata"
    )
    if register_data["as_of"] != lineage.effective_date.isoformat():
        raise GovernedEvidenceLoadError(
            "school register as_of must equal declared artifact effective_date"
        )
    if (
        not isinstance(register_data["source_name"], str)
        or not register_data["source_name"].strip()
    ):
        raise GovernedEvidenceLoadError("school register source_name must be non-blank")
    schools_data = root["schools"]
    if not isinstance(schools_data, list) or not schools_data:
        raise GovernedEvidenceLoadError("school register must contain a non-empty schools array")
    schools: list[School] = []
    school_ids: set[str] = set()
    for position, record in enumerate(schools_data):
        row = _require_exact_keys(
            record,
            frozenset({"school_id", "name", "phase"}),
            label=f"school register record {position}",
        )
        try:
            school = School(
                school_id=row["school_id"],
                name=row["name"],
                phase=row["phase"],
                source_evidence_id=lineage.source_id,
            )
        except Exception as error:
            raise GovernedEvidenceLoadError(
                f"school register record {position} is malformed"
            ) from error
        if school.school_id in school_ids:
            raise GovernedEvidenceLoadError(
                f"school register contains duplicate school_id {school.school_id!r}"
            )
        school_ids.add(school.school_id)
        schools.append(school)
    try:
        register = SchoolRegisterEvidence(
            evidence_id=lineage.source_id,
            source_name=register_data["source_name"],
            as_of=lineage.effective_date,
        )
    except Exception as error:
        raise GovernedEvidenceLoadError("school register metadata is malformed") from error
    return register, tuple(sorted(schools, key=lambda item: item.school_id))


def _load_admissions(
    payload: JSONValue, lineage: GovernedArtifactLineage
) -> tuple[StrategicEducationDestination, ...]:
    root = _require_exact_keys(
        payload, frozenset({"schema", "admissions"}), label="strategic admissions"
    )
    if root["schema"] != _ADMISSIONS_SCHEMA:
        raise GovernedEvidenceLoadError("strategic admissions has an unsupported schema")
    records = root["admissions"]
    if not isinstance(records, list):
        raise GovernedEvidenceLoadError("strategic admissions must contain an admissions array")
    destinations: list[StrategicEducationDestination] = []
    record_ids: set[str] = set()
    destination_ids: set[str] = set()
    expected = frozenset(
        {
            "record_id",
            "record_version",
            "strategic_destination_id",
            "name",
            "admitted_on",
            "rationale",
            "review_trigger",
            "access_evidence_ids",
        }
    )
    for position, record in enumerate(records):
        row = _require_exact_keys(record, expected, label=f"strategic admission record {position}")
        try:
            destination = StrategicEducationDestination(
                record_id=row["record_id"],
                record_version=row["record_version"],
                strategic_destination_id=row["strategic_destination_id"],
                name=row["name"],
                source_evidence_id=lineage.source_id,
                admitted_on=row["admitted_on"],
                rationale=row["rationale"],
                admission_evidence_ids=(lineage.source_id,),
                review_trigger=row["review_trigger"],
                access_evidence_ids=tuple(row["access_evidence_ids"])
                if isinstance(row["access_evidence_ids"], list)
                else (),
            )
        except Exception as error:
            raise GovernedEvidenceLoadError(
                f"strategic admission record {position} is malformed"
            ) from error
        if destination.record_id in record_ids:
            raise GovernedEvidenceLoadError(
                f"strategic admissions contains duplicate record_id {destination.record_id!r}"
            )
        if destination.strategic_destination_id in destination_ids:
            raise GovernedEvidenceLoadError(
                "strategic admissions contains duplicate strategic_destination_id "
                f"{destination.strategic_destination_id!r}"
            )
        record_ids.add(destination.record_id)
        destination_ids.add(destination.strategic_destination_id)
        destinations.append(destination)
    return tuple(sorted(destinations, key=lambda item: item.strategic_destination_id))


def _population_source(lineages: tuple[GovernedArtifactLineage, ...]) -> PopulationReachSource:
    content_sha256 = canonical_sha256(
        [
            {
                "source_id": item.source_id,
                "content_sha256": item.content_sha256,
                "release": item.release,
                "effective_date": item.effective_date.isoformat(),
                "licence": item.licence,
                "redistribution": item.redistribution,
            }
            for item in lineages
        ]
    )
    return PopulationReachSource(
        source_id="population-reach-governed-artifacts",
        release="; ".join(item.release for item in lineages),
        effective_date=max(item.effective_date for item in lineages),
        licence="multiple governed artifact licences; inspect artifact_lineage",
        permitted_uses=("population-reach-corridor-comparison",),
        known_limitations=("whole-OA resident counts are not demand or accessibility evidence",),
        transformation_lineage=tuple(
            f"{item.source_id}:{item.content_sha256}:{item.redistribution}" for item in lineages
        ),
        source_uri="governed-artifact://population-reach",
        version="satn-population-reach-evidence-loader/v1",
        content_sha256=content_sha256,
    )


def _canonical_oa_id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _OA_ID.fullmatch(value) is None:
        raise GovernedEvidenceLoadError(
            f"{label} OA21CD must be an uppercase canonical ONS OA identifier"
        )
    return value


def _require_finite_json(value: object, *, label: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise GovernedEvidenceLoadError(f"{label} must not contain non-finite values")
    if isinstance(value, list):
        for item in value:
            _require_finite_json(item, label=label)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise GovernedEvidenceLoadError(f"{label} must use string JSON object keys")
            _require_finite_json(item, label=label)
