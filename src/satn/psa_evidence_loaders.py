"""Strict, content-bound loaders for Preferred Strategic Alignment evidence.

The module is an adapter boundary, not a source-discovery layer. Every
configured file is opened beneath one required base directory without
following symlinks, read through the opened descriptor, SHA-256 checked before
parsing, and parsed with a closed JSON schema.

Supported source schemas:

* OA geometry and population-weighted-centroid GeoJSON use exactly ``type``,
  ``crs`` and ``features`` at the root. Each feature has only ``type``,
  ``properties`` and ``geometry``; properties contain only ``OA21CD``.
* usual-resident counts use
  ``{"records": [{"OA21CD": ..., "usual_residents": ...}]}``.
* school registers use ``satn-school-register/v1`` with explicit source,
  authority, current/governed status, effective date and current school rows.
* strategic admissions use
  ``satn-strategic-education-destination-admission/v1`` with explicit
  admission authority, disposition, current site identity/type/status and
  access-point evidence.

Population records are retained as immutable WKB/count tuples. The exposed
GeoDataFrame is always a detached reconstruction, and the loader-owned compile
adapter revalidates the bound frame and source fingerprints immediately before
compilation. Education adapters similarly bind the approved
``EducationAccessSourceSnapshot`` to exact artifact hashes and lineage.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Final, Literal

import geopandas as gpd
from shapely import from_wkb
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from satn.education_access import (
    EducationAccessAssessment,
    EducationAccessSourceSnapshot,
    EducationPhase,
    OptionEducationEvidence,
    School,
    SchoolRegisterEvidence,
    StrategicEducationDestination,
    SupplementaryPCTEvidence,
    assess_education_access,
)
from satn.network_selection import (
    GovernedEvidenceArtifactConfig,
    PopulationReachEvidenceConfig,
    SchoolRegisterEvidenceConfig,
    StrategicEducationDestinationAdmissionConfig,
)
from satn.population_reach import (
    PopulationReachAssessment,
    PopulationReachColumns,
    PopulationReachProfile,
    PopulationReachSource,
    compile_population_reach,
)
from satn.runtime_governance_contract import canonical_sha256


class GovernedEvidenceLoadError(ValueError):
    """A declared governed artifact cannot be safely used as compiler input."""


_OA_ID: Final = re.compile(r"^[EWNS][0-9]{8}$")
_STRICT_ID: Final = re.compile(r"^\S+$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_CRS: Final = frozenset({"EPSG:4326", "EPSG:27700"})
_GEOJSON_ROOT_KEYS: Final = frozenset({"type", "crs", "features"})
_REGISTER_SCHEMA: Final = "satn-school-register/v1"
_ADMISSIONS_SCHEMA: Final = "satn-strategic-education-destination-admission/v1"
_PWC_ASSOCIATION_RULE_VERSION: Final = "satn-oa-pwc-association/v2"
_PWC_ASSOCIATION_RULE: Final = (
    "reject any cross-OA cover; otherwise accept nominal OA cover, or accept "
    "only within the declared metre tolerance when the nominal OA is uniquely nearest"
)
_PWC_NEAREST_TIE_TOLERANCE_M: Final = 0.001
_DESTINATION_TYPES: Final = frozenset({"college", "university", "other-non-school-education"})

type JSONValue = dict[str, object] | list[object] | str | int | float | bool | None


@dataclass(frozen=True)
class GovernedArtifactLineage:
    """Declared provenance and verified byte identity for one opened artifact."""

    source_id: str
    declared_path: Path
    path: Path = field(compare=False)
    release: str
    effective_date: date
    licence: str
    redistribution: Literal["public", "controlled", "aggregate-only"]
    content_sha256: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.source_id, "source_id"),
            (self.release, "release"),
            (self.licence, "licence"),
        ):
            _require_nonblank_text(value, label=label)
        if (
            not isinstance(self.declared_path, Path)
            or self.declared_path.is_absolute()
            or not self.declared_path.parts
            or ".." in self.declared_path.parts
        ):
            raise GovernedEvidenceLoadError(
                "declared artifact identity must be a normalized relative path"
            )
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise GovernedEvidenceLoadError("verified artifact path must be absolute")
        if type(self.effective_date) is not date:
            raise GovernedEvidenceLoadError("effective_date must be an exact date")
        if self.redistribution not in {"public", "controlled", "aggregate-only"}:
            raise GovernedEvidenceLoadError("unsupported redistribution state")
        if (
            not isinstance(self.content_sha256, str)
            or _SHA256.fullmatch(self.content_sha256) is None
        ):
            raise GovernedEvidenceLoadError("content_sha256 must be lowercase SHA-256")

    def canonical(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "declared_path": self.declared_path.as_posix(),
            "release": self.release,
            "effective_date": self.effective_date.isoformat(),
            "licence": self.licence,
            "redistribution": self.redistribution,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class SchoolRegisterGovernanceBinding:
    """Exact authority/currentness declaration retained from the register."""

    source_id: str
    source_name: str
    authority_id: str
    as_of: date
    governed: Literal[True]
    current: Literal[True]
    status: Literal["current"]

    def __post_init__(self) -> None:
        _require_strict_identifier(self.source_id, label="school register source_id")
        _require_nonblank_text(self.source_name, label="school register source_name")
        _require_strict_identifier(
            self.authority_id,
            label="school register authority_id",
        )
        if type(self.as_of) is not date:
            raise GovernedEvidenceLoadError("school register as_of must be an exact date")
        if self.governed is not True or self.current is not True or self.status != "current":
            raise GovernedEvidenceLoadError(
                "school register governance binding must be governed and current"
            )

    def canonical(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "authority_id": self.authority_id,
            "as_of": self.as_of.isoformat(),
            "governed": self.governed,
            "current": self.current,
            "status": self.status,
        }


@dataclass(frozen=True)
class SchoolRegisterRecordBinding:
    """Exact typed school row retained beside the approved School model."""

    school_id: str
    name: str
    phase: EducationPhase
    record_status: Literal["current"]

    def __post_init__(self) -> None:
        _require_strict_identifier(self.school_id, label="school_id")
        _require_nonblank_text(self.name, label="school name")
        if not isinstance(self.phase, EducationPhase):
            raise GovernedEvidenceLoadError("school phase must be an EducationPhase")
        if self.record_status != "current":
            raise GovernedEvidenceLoadError("school record binding must be current")

    def canonical(self) -> dict[str, object]:
        return {
            "school_id": self.school_id,
            "name": self.name,
            "phase": self.phase.value,
            "record_status": self.record_status,
        }


@dataclass(frozen=True)
class StrategicAdmissionAuthorityBinding:
    """Exact governed/current admission authority declaration."""

    authority_id: str
    source_id: str
    governed: Literal[True]
    effective_date: date
    current: Literal[True]
    status: Literal["current"]

    def __post_init__(self) -> None:
        _require_strict_identifier(
            self.authority_id,
            label="strategic admissions authority_id",
        )
        _require_strict_identifier(
            self.source_id,
            label="strategic admissions source_id",
        )
        if type(self.effective_date) is not date:
            raise GovernedEvidenceLoadError(
                "strategic admissions effective_date must be an exact date"
            )
        if self.governed is not True or self.current is not True or self.status != "current":
            raise GovernedEvidenceLoadError(
                "strategic admissions authority must be governed and current"
            )

    def canonical(self) -> dict[str, object]:
        return {
            "authority_id": self.authority_id,
            "source_id": self.source_id,
            "governed": self.governed,
            "effective_date": self.effective_date.isoformat(),
            "current": self.current,
            "status": self.status,
        }


@dataclass(frozen=True)
class StrategicAdmissionRecordBinding:
    """Full typed admission record retained beside the narrowed destination."""

    record_id: str
    record_version: str
    strategic_destination_id: str
    site_id: str
    destination_type: Literal[
        "college",
        "university",
        "other-non-school-education",
    ]
    name: str
    site_status: Literal["current"]
    disposition: Literal["admitted"]
    admitted_on: date
    admission_authority_id: str
    rationale: Literal["configured-strategic-education-destination"]
    review_trigger: Literal["governed-destination-record-changes"]
    access_point_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, label in (
            (self.record_id, "strategic admission record_id"),
            (self.record_version, "strategic admission record_version"),
            (
                self.strategic_destination_id,
                "strategic admission destination ID",
            ),
            (self.site_id, "strategic admission site_id"),
            (
                self.admission_authority_id,
                "strategic admission authority_id",
            ),
        ):
            _require_strict_identifier(value, label=label)
        _require_nonblank_text(self.name, label="strategic admission name")
        if self.site_id != self.strategic_destination_id:
            raise GovernedEvidenceLoadError("strategic admission site identity mismatch")
        if self.destination_type not in _DESTINATION_TYPES:
            raise GovernedEvidenceLoadError("unsupported strategic destination type")
        if self.site_status != "current" or self.disposition != "admitted":
            raise GovernedEvidenceLoadError("strategic admission must be a current admitted site")
        if type(self.admitted_on) is not date:
            raise GovernedEvidenceLoadError("admitted_on must be an exact date")
        if self.rationale != "configured-strategic-education-destination":
            raise GovernedEvidenceLoadError("unsupported strategic admission rationale")
        if self.review_trigger != "governed-destination-record-changes":
            raise GovernedEvidenceLoadError("unsupported strategic admission review trigger")
        if not self.access_point_evidence_ids:
            raise GovernedEvidenceLoadError("strategic admission requires access-point evidence")
        _strict_identifier_list(
            list(self.access_point_evidence_ids),
            label="strategic admission access-point evidence",
        )

    def canonical(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "record_version": self.record_version,
            "strategic_destination_id": self.strategic_destination_id,
            "site_id": self.site_id,
            "destination_type": self.destination_type,
            "name": self.name,
            "site_status": self.site_status,
            "disposition": self.disposition,
            "admitted_on": self.admitted_on.isoformat(),
            "admission_authority_id": self.admission_authority_id,
            "rationale": self.rationale,
            "review_trigger": self.review_trigger,
            "access_point_evidence_ids": list(self.access_point_evidence_ids),
        }


@dataclass(frozen=True)
class _PopulationReachBoundRecord:
    oa_id: str
    usual_residents: int
    geometry_wkb: bytes
    population_weighted_centroid_wkb: bytes

    def canonical(self) -> dict[str, object]:
        return {
            "OA21CD": self.oa_id,
            "usual_residents": self.usual_residents,
            "geometry_wkb": self.geometry_wkb.hex(),
            "population_weighted_centroid_wkb": (self.population_weighted_centroid_wkb.hex()),
        }


@dataclass(frozen=True)
class PopulationReachEvidenceLoad:
    """Immutable OA evidence bound to exact raw and canonical frame identities."""

    source: PopulationReachSource
    columns: PopulationReachColumns
    artifact_lineage: tuple[GovernedArtifactLineage, ...]
    crs: str
    pwc_outside_tolerance_m: float
    frame_content_sha256: str
    _records: tuple[_PopulationReachBoundRecord, ...]

    def __post_init__(self) -> None:
        _verified_population_frame(self)

    @property
    def output_areas(self) -> gpd.GeoDataFrame:
        """Return a detached frame; mutations cannot alter the bound evidence."""

        return _verified_population_frame(self)


@dataclass(frozen=True)
class EducationAccessEvidenceLoad:
    """Approved source snapshot bound to exact register/admission artifacts."""

    source_snapshot: EducationAccessSourceSnapshot
    school_register_lineage: GovernedArtifactLineage
    school_register_governance: SchoolRegisterGovernanceBinding
    school_records: tuple[SchoolRegisterRecordBinding, ...]
    admissions_lineage: GovernedArtifactLineage | None
    strategic_admissions_authority: StrategicAdmissionAuthorityBinding | None
    strategic_admission_records: tuple[StrategicAdmissionRecordBinding, ...]
    as_at: date
    school_register_max_age_days: int
    strategic_admissions_max_age_days: int | None
    governed_source_fingerprint: str

    def __post_init__(self) -> None:
        _verify_education_load(self)


@dataclass(frozen=True)
class GovernedEducationAccessAssessment:
    """Education assessment retaining exact raw-source identity downstream."""

    assessment: EducationAccessAssessment
    source_evidence: EducationAccessEvidenceLoad
    assessment_content_sha256: str
    governed_input_fingerprint: str

    def __post_init__(self) -> None:
        _verify_education_load(self.source_evidence)
        try:
            validated = EducationAccessAssessment.model_validate(
                self.assessment.model_dump(mode="python")
            )
        except Exception as error:
            raise GovernedEvidenceLoadError(
                "governed education assessment fails exact raw reconstruction"
            ) from error
        if validated != self.assessment:
            raise GovernedEvidenceLoadError(
                "governed education assessment changed during revalidation"
            )
        _require_assessment_source_matches_binding(
            validated,
            self.source_evidence,
        )
        content_sha256 = canonical_sha256(validated.model_dump(mode="json"))
        if self.assessment_content_sha256 != content_sha256:
            raise GovernedEvidenceLoadError(
                "governed education assessment content fingerprint mismatch"
            )
        expected = canonical_sha256(
            {
                "schema": "satn-governed-education-assessment-binding/v2",
                "governed_source_fingerprint": (self.source_evidence.governed_source_fingerprint),
                "assessment_content_sha256": content_sha256,
            }
        )
        if self.governed_input_fingerprint != expected:
            raise GovernedEvidenceLoadError(
                "governed education assessment source fingerprint mismatch"
            )

    @property
    def artifact_lineage(self) -> tuple[GovernedArtifactLineage, ...]:
        return _education_lineages(
            self.source_evidence.school_register_lineage,
            self.source_evidence.admissions_lineage,
        )

    @property
    def as_at(self) -> date:
        return self.source_evidence.as_at


@dataclass(frozen=True)
class GovernedPopulationReachAssessment:
    """Population assessment retaining structured artifact provenance."""

    assessment: PopulationReachAssessment
    artifact_lineage: tuple[GovernedArtifactLineage, ...]
    frame_content_sha256: str
    pwc_outside_tolerance_m: float
    governed_input_fingerprint: str

    def __post_init__(self) -> None:
        expected_source = _population_source(
            self.artifact_lineage,
            self.frame_content_sha256,
            self.pwc_outside_tolerance_m,
        )
        if self.assessment.source != expected_source:
            raise GovernedEvidenceLoadError(
                "governed population assessment source binding mismatch"
            )
        expected = canonical_sha256(
            {
                "schema": "satn-governed-population-assessment-binding/v1",
                "assessment": self.assessment.canonical(),
                "artifacts": [item.canonical() for item in self.artifact_lineage],
                "frame_content_sha256": self.frame_content_sha256,
                "pwc_association_rule_version": _PWC_ASSOCIATION_RULE_VERSION,
                "pwc_outside_tolerance_m": self.pwc_outside_tolerance_m,
            }
        )
        if self.governed_input_fingerprint != expected:
            raise GovernedEvidenceLoadError("governed population assessment fingerprint mismatch")


def load_population_reach_evidence(
    evidence: PopulationReachEvidenceConfig | None,
    *,
    base_directory: Path | None = None,
    pwc_outside_tolerance_m: float | None = None,
) -> PopulationReachEvidenceLoad | None:
    """Load the exact three-way governed OA join, or preserve absent config."""

    if evidence is None:
        return None
    base = _required_base_directory(base_directory)
    association_tolerance = _required_nonnegative_finite_distance(
        pwc_outside_tolerance_m,
        label="pwc_outside_tolerance_m",
    )
    geometry_lineage, geometry_payload = _read_artifact(
        evidence.output_area_geometry, base_directory=base
    )
    centroid_lineage, centroid_payload = _read_artifact(
        evidence.population_weighted_centroids, base_directory=base
    )
    counts_lineage, counts_payload = _read_artifact(
        evidence.usual_resident_counts, base_directory=base
    )
    geometry_rows, crs = _load_geojson_rows(
        geometry_payload,
        label="output-area geometry",
        geometry_types={"Polygon", "MultiPolygon"},
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
    _validate_pwc_associations(
        geometry_rows,
        centroid_rows,
        crs=crs,
        outside_tolerance_m=association_tolerance,
    )
    records = tuple(
        _PopulationReachBoundRecord(
            oa_id=oa_id,
            usual_residents=counts[oa_id],
            geometry_wkb=bytes(geometry_rows[oa_id].wkb),
            population_weighted_centroid_wkb=bytes(centroid_rows[oa_id].wkb),
        )
        for oa_id in sorted(geometry_ids)
    )
    frame_fingerprint = _population_frame_fingerprint(
        crs,
        records,
        association_tolerance,
    )
    lineages = (geometry_lineage, centroid_lineage, counts_lineage)
    source = _population_source(
        lineages,
        frame_fingerprint,
        association_tolerance,
    )
    return PopulationReachEvidenceLoad(
        source=source,
        columns=PopulationReachColumns(),
        artifact_lineage=lineages,
        crs=crs,
        pwc_outside_tolerance_m=association_tolerance,
        frame_content_sha256=frame_fingerprint,
        _records=records,
    )


def compile_population_reach_from_evidence(
    evidence: PopulationReachEvidenceLoad,
    route_options: gpd.GeoDataFrame,
    area_definition: gpd.GeoDataFrame,
    *,
    profile: PopulationReachProfile | None = None,
) -> GovernedPopulationReachAssessment:
    """Reverify immutable OA/source binding immediately before compilation."""

    if not isinstance(evidence, PopulationReachEvidenceLoad):
        raise GovernedEvidenceLoadError(
            "population reach compilation requires a bound evidence load"
        )
    frame = _verified_population_frame(evidence)
    assessment = compile_population_reach(
        route_options,
        frame,
        area_definition,
        source=evidence.source,
        profile=profile,
        columns=evidence.columns,
    )
    fingerprint = canonical_sha256(
        {
            "schema": "satn-governed-population-assessment-binding/v1",
            "assessment": assessment.canonical(),
            "artifacts": [item.canonical() for item in evidence.artifact_lineage],
            "frame_content_sha256": evidence.frame_content_sha256,
            "pwc_association_rule_version": _PWC_ASSOCIATION_RULE_VERSION,
            "pwc_outside_tolerance_m": evidence.pwc_outside_tolerance_m,
        }
    )
    return GovernedPopulationReachAssessment(
        assessment=assessment,
        artifact_lineage=evidence.artifact_lineage,
        frame_content_sha256=evidence.frame_content_sha256,
        pwc_outside_tolerance_m=evidence.pwc_outside_tolerance_m,
        governed_input_fingerprint=fingerprint,
    )


def load_education_access_evidence(
    school_register_evidence: SchoolRegisterEvidenceConfig | None,
    strategic_destination_admissions: (StrategicEducationDestinationAdmissionConfig | None) = None,
    *,
    base_directory: Path | None = None,
    as_at: date | None = None,
    school_register_max_age_days: int | None = None,
    strategic_admissions_max_age_days: int | None = None,
) -> EducationAccessEvidenceLoad | None:
    """Load current governed education sources into a raw-identity binding."""

    if school_register_evidence is None:
        if strategic_destination_admissions is not None:
            raise GovernedEvidenceLoadError(
                "strategic destination admissions require current school-register evidence"
            )
        return None
    base = _required_base_directory(base_directory)
    assessment_date = _required_assessment_date(as_at)
    register_max_age = _required_freshness_window(
        school_register_max_age_days,
        label="school_register_max_age_days",
    )
    if strategic_destination_admissions is None:
        if strategic_admissions_max_age_days is not None:
            raise GovernedEvidenceLoadError(
                "strategic admissions freshness requires an admissions artifact"
            )
        admissions_max_age: int | None = None
    else:
        admissions_max_age = _required_freshness_window(
            strategic_admissions_max_age_days,
            label="strategic_admissions_max_age_days",
        )
    register_lineage, register_payload = _read_artifact(
        school_register_evidence.school_register,
        base_directory=base,
    )
    _require_current_artifact(
        register_lineage,
        as_at=assessment_date,
        max_age_days=register_max_age,
        label="school register",
    )
    (
        register,
        schools,
        register_governance,
        school_records,
    ) = _load_school_register(register_payload, register_lineage)
    admissions_lineage: GovernedArtifactLineage | None = None
    admissions_authority: StrategicAdmissionAuthorityBinding | None = None
    admission_records: tuple[StrategicAdmissionRecordBinding, ...] = ()
    destinations: tuple[StrategicEducationDestination, ...] = ()
    if strategic_destination_admissions is not None:
        admissions_lineage, admissions_payload = _read_artifact(
            strategic_destination_admissions.admissions,
            base_directory=base,
        )
        assert admissions_max_age is not None
        _require_current_artifact(
            admissions_lineage,
            as_at=assessment_date,
            max_age_days=admissions_max_age,
            label="strategic admissions",
        )
        (
            destinations,
            admissions_authority,
            admission_records,
        ) = _load_admissions(
            admissions_payload,
            admissions_lineage,
        )
    assessment = assess_education_access(
        register_evidence=register,
        schools=schools,
        strategic_destinations=destinations,
        option_evidence=(),
    )
    lineages = _education_lineages(register_lineage, admissions_lineage)
    fingerprint = _governed_education_fingerprint(
        assessment.source_snapshot,
        lineages,
        school_register_governance=register_governance,
        school_records=school_records,
        strategic_admissions_authority=admissions_authority,
        strategic_admission_records=admission_records,
        as_at=assessment_date,
        school_register_max_age_days=register_max_age,
        strategic_admissions_max_age_days=admissions_max_age,
    )
    return EducationAccessEvidenceLoad(
        source_snapshot=assessment.source_snapshot,
        school_register_lineage=register_lineage,
        school_register_governance=register_governance,
        school_records=school_records,
        admissions_lineage=admissions_lineage,
        strategic_admissions_authority=admissions_authority,
        strategic_admission_records=admission_records,
        as_at=assessment_date,
        school_register_max_age_days=register_max_age,
        strategic_admissions_max_age_days=admissions_max_age,
        governed_source_fingerprint=fingerprint,
    )


def assess_education_access_from_evidence(
    evidence: EducationAccessEvidenceLoad,
    *,
    option_evidence: tuple[OptionEducationEvidence, ...],
    option_ids: tuple[str, ...] = (),
    supplementary_pct_evidence: tuple[SupplementaryPCTEvidence, ...] = (),
) -> GovernedEducationAccessAssessment:
    """Assess options while retaining and reverifying exact raw-source identity."""

    if not isinstance(evidence, EducationAccessEvidenceLoad):
        raise GovernedEvidenceLoadError("education assessment requires a bound evidence load")
    _verify_education_load(evidence)
    source = evidence.source_snapshot
    assessment = assess_education_access(
        register_evidence=source.register_evidence,
        schools=source.schools,
        strategic_destinations=source.strategic_education_destinations,
        option_evidence=option_evidence,
        option_ids=option_ids,
        supplementary_pct_evidence=supplementary_pct_evidence,
    )
    assessment_content_sha256 = canonical_sha256(assessment.model_dump(mode="json"))
    governed_input_fingerprint = canonical_sha256(
        {
            "schema": "satn-governed-education-assessment-binding/v2",
            "governed_source_fingerprint": (evidence.governed_source_fingerprint),
            "assessment_content_sha256": assessment_content_sha256,
        }
    )
    return GovernedEducationAccessAssessment(
        assessment=assessment,
        source_evidence=evidence,
        assessment_content_sha256=assessment_content_sha256,
        governed_input_fingerprint=governed_input_fingerprint,
    )


def _read_artifact(
    artifact: GovernedEvidenceArtifactConfig,
    *,
    base_directory: Path,
) -> tuple[GovernedArtifactLineage, JSONValue]:
    path, declared_path, content = _read_confined_regular_file(
        artifact.path,
        base_directory=base_directory,
    )
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != artifact.content_sha256:
        raise GovernedEvidenceLoadError(
            f"declared governed artifact SHA-256 mismatch for {artifact.source_id!r}"
        )
    try:
        payload = json.loads(
            content.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except GovernedEvidenceLoadError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise GovernedEvidenceLoadError(
            f"declared governed artifact is not strict UTF-8 JSON: {artifact.source_id!r}"
        ) from error
    _require_finite_json(payload, label=artifact.source_id)
    return (
        GovernedArtifactLineage(
            source_id=artifact.source_id,
            declared_path=declared_path,
            path=path,
            release=artifact.release,
            effective_date=artifact.effective_date,
            licence=artifact.licence,
            redistribution=artifact.redistribution,
            content_sha256=actual_sha256,
        ),
        payload,
    )


def _required_base_directory(base_directory: Path | None) -> Path:
    if not isinstance(base_directory, Path):
        raise GovernedEvidenceLoadError(
            "configured governed artifacts require an explicit base_directory"
        )
    if not base_directory.is_absolute():
        raise GovernedEvidenceLoadError("base_directory must be absolute")
    if ".." in base_directory.parts:
        raise GovernedEvidenceLoadError("base_directory must not contain parent traversal")
    return base_directory


def _read_confined_regular_file(
    configured_path: Path,
    *,
    base_directory: Path,
) -> tuple[Path, Path, bytes]:
    """Open one confined file without following any symlink component.

    Directory descriptors plus ``O_NOFOLLOW`` close path-resolution races on
    POSIX. A before/after ``fstat`` also rejects mutation during the read.
    """

    _require_secure_open_capabilities()
    if not isinstance(configured_path, Path):
        raise GovernedEvidenceLoadError("governed artifact path must be a Path")
    if ".." in configured_path.parts:
        raise GovernedEvidenceLoadError(
            "declared governed artifact path must not contain parent traversal"
        )
    base = Path(os.path.abspath(base_directory))
    candidate = (
        Path(os.path.abspath(configured_path))
        if configured_path.is_absolute()
        else base / configured_path
    )
    try:
        relative = candidate.relative_to(base)
    except ValueError as error:
        raise GovernedEvidenceLoadError(
            "declared governed artifact path escapes base_directory"
        ) from error
    if not relative.parts:
        raise GovernedEvidenceLoadError("declared governed artifact path must name a direct file")
    base_fd = _open_absolute_directory_without_symlinks(base)
    directory_fd = base_fd
    try:
        for component in relative.parts[:-1]:
            next_fd = _open_child_directory_without_symlinks(
                directory_fd,
                component,
            )
            if directory_fd != base_fd:
                os.close(directory_fd)
            directory_fd = next_fd
        content = _read_regular_file_descriptor(
            directory_fd,
            relative.parts[-1],
        )
    finally:
        if directory_fd != base_fd:
            os.close(directory_fd)
        os.close(base_fd)
    return candidate, relative, content


def _require_secure_open_capabilities() -> None:
    missing: list[str] = []
    if os.open not in os.supports_dir_fd:
        missing.append("os.open(dir_fd)")
    if os.stat not in os.supports_dir_fd:
        missing.append("os.stat(dir_fd)")
    if os.stat not in os.supports_follow_symlinks:
        missing.append("os.stat(follow_symlinks=False)")
    for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"):
        if not isinstance(getattr(os, name, None), int) or getattr(os, name) == 0:
            missing.append(f"os.{name}")
    if missing:
        raise GovernedEvidenceLoadError(
            "secure governed artifact opening is unavailable on this platform: "
            + ", ".join(missing)
        )


def _open_absolute_directory_without_symlinks(path: Path) -> int:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, directory_flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = _open_child_directory_without_symlinks(
                descriptor,
                component,
            )
            os.close(descriptor)
            descriptor = next_descriptor
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _open_child_directory_without_symlinks(parent_fd: int, component: str) -> int:
    try:
        metadata = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode):
            raise GovernedEvidenceLoadError(
                "governed artifact path must not contain symlink components"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise GovernedEvidenceLoadError(
                "governed artifact parent component must be a directory"
            )
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
        descriptor = os.open(component, flags, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            os.close(descriptor)
            raise GovernedEvidenceLoadError(
                "governed artifact parent component must be a directory"
            )
        return descriptor
    except GovernedEvidenceLoadError:
        raise
    except OSError as error:
        raise GovernedEvidenceLoadError(
            "governed artifact directory cannot be opened without symlinks"
        ) from error


def _read_regular_file_descriptor(parent_fd: int, component: str) -> bytes:
    try:
        metadata = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode):
            raise GovernedEvidenceLoadError("declared governed artifact path must not be a symlink")
        if not stat.S_ISREG(metadata.st_mode):
            raise GovernedEvidenceLoadError(
                "declared governed artifact path must name a regular file"
            )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | getattr(os, "O_BINARY", 0)
        )
        descriptor = os.open(component, flags, dir_fd=parent_fd)
    except GovernedEvidenceLoadError:
        raise
    except OSError as error:
        raise GovernedEvidenceLoadError(
            "declared governed artifact cannot be opened without symlinks"
        ) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise GovernedEvidenceLoadError(
                "declared governed artifact path must name a regular file"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise GovernedEvidenceLoadError(
                "declared governed artifact changed while it was being read"
            )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _reject_json_constant(value: str) -> None:
    raise GovernedEvidenceLoadError(f"non-finite JSON value {value!r} is forbidden")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GovernedEvidenceLoadError(f"strict JSON object contains duplicate member {key!r}")
        result[key] = value
    return result


def _require_exact_keys(
    value: object,
    keys: frozenset[str],
    *,
    label: str,
) -> dict[str, object]:
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
        geometry = _parse_geometry(
            item["geometry"],
            label=f"{label} feature {position}",
        )
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
        value,
        frozenset({"type", "coordinates"}),
        label=f"{label} geometry",
    )
    _require_real_coordinate_tree(
        geometry_data["coordinates"],
        label=f"{label} geometry coordinates",
    )
    try:
        geometry = shape(geometry_data)
    except Exception as error:
        raise GovernedEvidenceLoadError(f"{label} has malformed GeoJSON geometry") from error
    if geometry.is_empty or not geometry.is_valid:
        raise GovernedEvidenceLoadError(f"{label} geometry must be non-empty and valid")
    return geometry


def _require_real_coordinate_tree(value: object, *, label: str) -> None:
    if not isinstance(value, (list, tuple)) or not value:
        raise GovernedEvidenceLoadError(f"{label} must be a non-empty coordinate array")
    if all(not isinstance(item, (list, tuple)) for item in value):
        if len(value) not in {2, 3}:
            raise GovernedEvidenceLoadError(
                f"{label} positions must contain two or three ordinates"
            )
        for coordinate in value:
            if type(coordinate) not in {int, float}:
                raise GovernedEvidenceLoadError(f"{label} must contain real non-boolean numbers")
            try:
                finite = math.isfinite(float(coordinate))
            except (OverflowError, ValueError):
                finite = False
            if not finite:
                raise GovernedEvidenceLoadError(f"{label} must contain finite real numbers")
        return
    if any(not isinstance(item, (list, tuple)) for item in value):
        raise GovernedEvidenceLoadError(f"{label} must have a consistent coordinate nesting level")
    for item in value:
        _require_real_coordinate_tree(item, label=label)


def _load_usual_resident_counts(payload: JSONValue) -> dict[str, int]:
    root = _require_exact_keys(
        payload,
        frozenset({"records"}),
        label="usual-resident counts",
    )
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
        oa_id = _canonical_oa_id(
            row["OA21CD"],
            label="usual-resident counts",
        )
        residents = row["usual_residents"]
        if type(residents) is not int or residents < 0:
            raise GovernedEvidenceLoadError("usual_residents must be a whole non-negative integer")
        if oa_id in result:
            raise GovernedEvidenceLoadError(
                f"usual-resident counts contains duplicate OA21CD {oa_id!r}"
            )
        result[oa_id] = residents
    return result


def _validate_pwc_associations(
    geometries: dict[str, BaseGeometry],
    centroids: dict[str, BaseGeometry],
    *,
    crs: str,
    outside_tolerance_m: float,
) -> None:
    """Apply the versioned bounded OA/PWC association rule."""

    oa_ids = tuple(sorted(geometries))
    try:
        projected_geometries = gpd.GeoSeries(
            [geometries[oa_id] for oa_id in oa_ids],
            index=oa_ids,
            crs=crs,
        ).to_crs(epsg=27700)
        projected_centroids = gpd.GeoSeries(
            [centroids[oa_id] for oa_id in oa_ids],
            index=oa_ids,
            crs=crs,
        ).to_crs(epsg=27700)
    except Exception as error:
        raise GovernedEvidenceLoadError(
            "OA/PWC association evidence could not be projected to EPSG:27700"
        ) from error
    for oa_id in sorted(geometries):
        polygon = geometries[oa_id]
        point = centroids[oa_id]
        covering_other_ids = [
            other_id
            for other_id, other_polygon in geometries.items()
            if other_id != oa_id and other_polygon.covers(point)
        ]
        if covering_other_ids:
            raise GovernedEvidenceLoadError(
                f"population-weighted centroid for {oa_id!r} has ambiguous "
                "cross-OA coverage: "
                f"{', '.join(sorted(covering_other_ids))}"
            )
        if polygon.covers(point):
            continue
        projected_point = projected_centroids.loc[oa_id]
        distances = {
            candidate_id: float(projected_geometries.loc[candidate_id].distance(projected_point))
            for candidate_id in oa_ids
        }
        nominal_distance = distances[oa_id]
        nearest_distance = min(distances.values())
        nearest_ids = {
            candidate_id
            for candidate_id, distance_m in distances.items()
            if abs(distance_m - nearest_distance) <= _PWC_NEAREST_TIE_TOLERANCE_M
        }
        if nominal_distance <= outside_tolerance_m and nearest_ids == {oa_id}:
            continue
        raise GovernedEvidenceLoadError(
            f"population-weighted centroid for {oa_id!r} fails "
            f"{_PWC_ASSOCIATION_RULE_VERSION}: nominal distance "
            f"{nominal_distance:.3f}m, declared tolerance "
            f"{outside_tolerance_m:.3f}m, nearest OA IDs "
            f"{', '.join(sorted(nearest_ids))}"
        )


def _load_school_register(
    payload: JSONValue,
    lineage: GovernedArtifactLineage,
) -> tuple[
    SchoolRegisterEvidence,
    tuple[School, ...],
    SchoolRegisterGovernanceBinding,
    tuple[SchoolRegisterRecordBinding, ...],
]:
    root = _require_exact_keys(
        payload,
        frozenset({"schema", "register", "schools"}),
        label="school register",
    )
    if root["schema"] != _REGISTER_SCHEMA:
        raise GovernedEvidenceLoadError("school register has an unsupported schema")
    register_data = _require_exact_keys(
        root["register"],
        frozenset(
            {
                "source_id",
                "source_name",
                "authority_id",
                "as_of",
                "governed",
                "current",
                "status",
            }
        ),
        label="school register metadata",
    )
    if register_data["source_id"] != lineage.source_id:
        raise GovernedEvidenceLoadError(
            "school register source_id must match declared artifact source_id"
        )
    _require_strict_identifier(
        register_data["authority_id"],
        label="school register authority_id",
    )
    if register_data["as_of"] != lineage.effective_date.isoformat():
        raise GovernedEvidenceLoadError(
            "school register as_of must equal declared artifact effective_date"
        )
    if register_data["governed"] is not True:
        raise GovernedEvidenceLoadError("school register governed authority must be literal true")
    if register_data["current"] is not True or register_data["status"] != "current":
        raise GovernedEvidenceLoadError("school register must explicitly declare current status")
    _require_nonblank_text(
        register_data["source_name"],
        label="school register source_name",
    )
    governance = SchoolRegisterGovernanceBinding(
        source_id=register_data["source_id"],
        source_name=register_data["source_name"],
        authority_id=register_data["authority_id"],
        as_of=_parse_exact_date(
            register_data["as_of"],
            label="school register as_of",
        ),
        governed=register_data["governed"],
        current=register_data["current"],
        status=register_data["status"],
    )
    schools_data = root["schools"]
    if not isinstance(schools_data, list) or not schools_data:
        raise GovernedEvidenceLoadError("school register must contain a non-empty schools array")
    schools: list[School] = []
    bound_records: list[SchoolRegisterRecordBinding] = []
    school_ids: set[str] = set()
    for position, record in enumerate(schools_data):
        row = _require_exact_keys(
            record,
            frozenset({"school_id", "name", "phase", "record_status"}),
            label=f"school register record {position}",
        )
        if row["record_status"] != "current":
            raise GovernedEvidenceLoadError(
                f"school register record {position} must have current status"
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
        bound_records.append(
            SchoolRegisterRecordBinding(
                school_id=school.school_id,
                name=school.name,
                phase=school.phase,
                record_status=row["record_status"],
            )
        )
    try:
        register = SchoolRegisterEvidence(
            evidence_id=lineage.source_id,
            source_name=register_data["source_name"],
            as_of=lineage.effective_date,
            governed=register_data["governed"],
            current=register_data["current"],
        )
    except Exception as error:
        raise GovernedEvidenceLoadError("school register metadata is malformed") from error
    return (
        register,
        tuple(sorted(schools, key=lambda item: item.school_id)),
        governance,
        tuple(sorted(bound_records, key=lambda item: item.school_id)),
    )


def _load_admissions(
    payload: JSONValue,
    lineage: GovernedArtifactLineage,
) -> tuple[
    tuple[StrategicEducationDestination, ...],
    StrategicAdmissionAuthorityBinding,
    tuple[StrategicAdmissionRecordBinding, ...],
]:
    root = _require_exact_keys(
        payload,
        frozenset({"schema", "authority", "admissions"}),
        label="strategic admissions",
    )
    if root["schema"] != _ADMISSIONS_SCHEMA:
        raise GovernedEvidenceLoadError("strategic admissions has an unsupported schema")
    authority = _require_exact_keys(
        root["authority"],
        frozenset(
            {
                "authority_id",
                "source_id",
                "governed",
                "effective_date",
                "current",
                "status",
            }
        ),
        label="strategic admissions authority",
    )
    authority_id = _require_strict_identifier(
        authority["authority_id"],
        label="strategic admissions authority_id",
    )
    if authority["source_id"] != lineage.source_id:
        raise GovernedEvidenceLoadError(
            "strategic admissions source_id must match declared artifact source_id"
        )
    if authority["governed"] is not True:
        raise GovernedEvidenceLoadError(
            "strategic admissions governed authority must be literal true"
        )
    if authority["current"] is not True or authority["status"] != "current":
        raise GovernedEvidenceLoadError(
            "strategic admissions authority must explicitly declare current status"
        )
    if authority["effective_date"] != lineage.effective_date.isoformat():
        raise GovernedEvidenceLoadError(
            "strategic admissions authority effective_date must match artifact"
        )
    authority_binding = StrategicAdmissionAuthorityBinding(
        authority_id=authority_id,
        source_id=authority["source_id"],
        governed=authority["governed"],
        effective_date=_parse_exact_date(
            authority["effective_date"],
            label="strategic admissions authority effective_date",
        ),
        current=authority["current"],
        status=authority["status"],
    )
    records = root["admissions"]
    if not isinstance(records, list):
        raise GovernedEvidenceLoadError("strategic admissions must contain an admissions array")
    destinations: list[StrategicEducationDestination] = []
    bound_records: list[StrategicAdmissionRecordBinding] = []
    record_ids: set[str] = set()
    destination_ids: set[str] = set()
    expected = frozenset(
        {
            "record_id",
            "record_version",
            "strategic_destination_id",
            "site_id",
            "destination_type",
            "name",
            "site_status",
            "disposition",
            "admitted_on",
            "admission_authority_id",
            "rationale",
            "review_trigger",
            "access_point_evidence_ids",
        }
    )
    for position, record in enumerate(records):
        row = _require_exact_keys(
            record,
            expected,
            label=f"strategic admission record {position}",
        )
        if row["site_id"] != row["strategic_destination_id"]:
            raise GovernedEvidenceLoadError(
                f"strategic admission record {position} site identity mismatch"
            )
        if row["destination_type"] not in _DESTINATION_TYPES:
            raise GovernedEvidenceLoadError(
                f"strategic admission record {position} has unsupported destination_type"
            )
        if row["site_status"] != "current":
            raise GovernedEvidenceLoadError(
                f"strategic admission record {position} must be a current site"
            )
        if row["disposition"] != "admitted":
            raise GovernedEvidenceLoadError(
                f"strategic admission record {position} must have admitted disposition"
            )
        if row["admission_authority_id"] != authority_id:
            raise GovernedEvidenceLoadError(
                f"strategic admission record {position} authority mismatch"
            )
        admitted_on = _parse_exact_date(
            row["admitted_on"],
            label=f"strategic admission record {position} admitted_on",
        )
        if admitted_on > lineage.effective_date:
            raise GovernedEvidenceLoadError(
                f"strategic admission record {position} admitted_on is after "
                "the artifact effective_date"
            )
        access_ids = _strict_identifier_list(
            row["access_point_evidence_ids"],
            label=f"strategic admission record {position} access_point_evidence_ids",
        )
        bound_record = StrategicAdmissionRecordBinding(
            record_id=row["record_id"],
            record_version=row["record_version"],
            strategic_destination_id=row["strategic_destination_id"],
            site_id=row["site_id"],
            destination_type=row["destination_type"],
            name=row["name"],
            site_status=row["site_status"],
            disposition=row["disposition"],
            admitted_on=admitted_on,
            admission_authority_id=row["admission_authority_id"],
            rationale=row["rationale"],
            review_trigger=row["review_trigger"],
            access_point_evidence_ids=access_ids,
        )
        try:
            destination = StrategicEducationDestination(
                record_id=row["record_id"],
                record_version=row["record_version"],
                strategic_destination_id=row["strategic_destination_id"],
                name=row["name"],
                source_evidence_id=lineage.source_id,
                admitted_on=admitted_on,
                rationale=row["rationale"],
                admission_evidence_ids=(lineage.source_id,),
                review_trigger=row["review_trigger"],
                access_evidence_ids=access_ids,
                governed=authority["governed"],
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
                "strategic admissions contains duplicate "
                "strategic_destination_id "
                f"{destination.strategic_destination_id!r}"
            )
        record_ids.add(destination.record_id)
        destination_ids.add(destination.strategic_destination_id)
        destinations.append(destination)
        bound_records.append(bound_record)
    return (
        tuple(
            sorted(
                destinations,
                key=lambda item: item.strategic_destination_id,
            )
        ),
        authority_binding,
        tuple(
            sorted(
                bound_records,
                key=lambda item: item.strategic_destination_id,
            )
        ),
    )


def _population_frame_fingerprint(
    crs: str,
    records: tuple[_PopulationReachBoundRecord, ...],
    pwc_outside_tolerance_m: float,
) -> str:
    return canonical_sha256(
        {
            "schema": "satn-population-reach-bound-frame/v1",
            "crs": crs,
            "pwc_association_rule_version": _PWC_ASSOCIATION_RULE_VERSION,
            "pwc_association_rule": _PWC_ASSOCIATION_RULE,
            "pwc_outside_tolerance_m": pwc_outside_tolerance_m,
            "records": [record.canonical() for record in records],
        }
    )


def _population_source(
    lineages: tuple[GovernedArtifactLineage, ...],
    frame_content_sha256: str,
    pwc_outside_tolerance_m: float,
) -> PopulationReachSource:
    content_sha256 = canonical_sha256(
        {
            "schema": "satn-population-reach-governed-binding/v1",
            "artifacts": [item.canonical() for item in lineages],
            "frame_content_sha256": frame_content_sha256,
            "pwc_association_rule_version": _PWC_ASSOCIATION_RULE_VERSION,
            "pwc_outside_tolerance_m": pwc_outside_tolerance_m,
        }
    )
    return PopulationReachSource(
        source_id="population-reach-governed-artifacts",
        release="; ".join(item.release for item in lineages),
        effective_date=max(item.effective_date for item in lineages),
        licence=("multiple governed artifact licences; inspect artifact_lineage"),
        permitted_uses=("population-reach-corridor-comparison",),
        known_limitations=(
            "whole-OA resident counts are not demand or accessibility evidence",
            f"PWC association rule {_PWC_ASSOCIATION_RULE_VERSION}: "
            f"{_PWC_ASSOCIATION_RULE}; outside tolerance "
            f"{pwc_outside_tolerance_m} metres",
        ),
        transformation_lineage=(
            *(f"{item.source_id}:{item.content_sha256}:{item.redistribution}" for item in lineages),
            f"canonical-frame:{frame_content_sha256}",
        ),
        source_uri="governed-artifact://population-reach",
        version="satn-population-reach-evidence-loader/v1",
        content_sha256=content_sha256,
    )


def _verified_population_frame(
    evidence: PopulationReachEvidenceLoad,
) -> gpd.GeoDataFrame:
    if evidence.crs not in _SUPPORTED_CRS:
        raise GovernedEvidenceLoadError("bound population evidence has unsupported CRS")
    if not evidence._records:
        raise GovernedEvidenceLoadError("bound population evidence must contain records")
    association_tolerance = _required_nonnegative_finite_distance(
        evidence.pwc_outside_tolerance_m,
        label="bound pwc_outside_tolerance_m",
    )
    oa_ids: set[str] = set()
    rows: list[dict[str, object]] = []
    geometries: dict[str, BaseGeometry] = {}
    centroids: dict[str, BaseGeometry] = {}
    for record in evidence._records:
        oa_id = _canonical_oa_id(
            record.oa_id,
            label="bound population evidence",
        )
        if oa_id in oa_ids:
            raise GovernedEvidenceLoadError(
                f"bound population evidence contains duplicate OA21CD {oa_id!r}"
            )
        oa_ids.add(oa_id)
        if type(record.usual_residents) is not int or record.usual_residents < 0:
            raise GovernedEvidenceLoadError(
                "bound usual_residents must be whole non-negative integers"
            )
        geometry = _bound_wkb_geometry(
            record.geometry_wkb,
            label=f"bound OA geometry {oa_id}",
            geometry_types={"Polygon", "MultiPolygon"},
        )
        centroid = _bound_wkb_geometry(
            record.population_weighted_centroid_wkb,
            label=f"bound PWC geometry {oa_id}",
            geometry_types={"Point"},
        )
        geometries[oa_id] = geometry
        centroids[oa_id] = centroid
        rows.append(
            {
                "OA21CD": oa_id,
                "usual_residents": record.usual_residents,
                "population_weighted_centroid": centroid,
                "geometry": geometry,
            }
        )
    _validate_pwc_associations(
        geometries,
        centroids,
        crs=evidence.crs,
        outside_tolerance_m=association_tolerance,
    )
    expected_frame = _population_frame_fingerprint(
        evidence.crs,
        evidence._records,
        association_tolerance,
    )
    if evidence.frame_content_sha256 != expected_frame:
        raise GovernedEvidenceLoadError("bound population frame content fingerprint mismatch")
    expected_source = _population_source(
        evidence.artifact_lineage,
        expected_frame,
        association_tolerance,
    )
    if evidence.source != expected_source:
        raise GovernedEvidenceLoadError(
            "bound PopulationReachSource does not match exact frame and lineage"
        )
    if evidence.columns != PopulationReachColumns():
        raise GovernedEvidenceLoadError(
            "bound population evidence requires the approved column contract"
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=evidence.crs)


def _bound_wkb_geometry(
    value: bytes,
    *,
    label: str,
    geometry_types: set[str],
) -> BaseGeometry:
    if not isinstance(value, bytes) or not value:
        raise GovernedEvidenceLoadError(f"{label} must be immutable WKB bytes")
    try:
        geometry = from_wkb(value)
    except Exception as error:
        raise GovernedEvidenceLoadError(f"{label} has malformed WKB") from error
    if (
        not isinstance(geometry, BaseGeometry)
        or geometry.geom_type not in geometry_types
        or geometry.is_empty
        or not geometry.is_valid
    ):
        raise GovernedEvidenceLoadError(f"{label} must be a valid non-empty approved geometry")
    interface = geometry.__geo_interface__
    _require_real_coordinate_tree(
        interface["coordinates"],
        label=f"{label} coordinates",
    )
    return geometry


def _education_lineages(
    school_register: GovernedArtifactLineage,
    admissions: GovernedArtifactLineage | None,
) -> tuple[GovernedArtifactLineage, ...]:
    return (school_register,) if admissions is None else (school_register, admissions)


def _governed_education_fingerprint(
    snapshot: EducationAccessSourceSnapshot,
    lineages: tuple[GovernedArtifactLineage, ...],
    *,
    school_register_governance: SchoolRegisterGovernanceBinding,
    school_records: tuple[SchoolRegisterRecordBinding, ...],
    strategic_admissions_authority: StrategicAdmissionAuthorityBinding | None,
    strategic_admission_records: tuple[StrategicAdmissionRecordBinding, ...],
    as_at: date,
    school_register_max_age_days: int,
    strategic_admissions_max_age_days: int | None,
) -> str:
    return canonical_sha256(
        {
            "schema": "satn-governed-education-source-binding/v2",
            "education_source_snapshot_fingerprint": (snapshot.source_snapshot_fingerprint),
            "as_at": as_at.isoformat(),
            "school_register_max_age_days": school_register_max_age_days,
            "strategic_admissions_max_age_days": (strategic_admissions_max_age_days),
            "artifacts": [item.canonical() for item in lineages],
            "school_register_governance": school_register_governance.canonical(),
            "school_records": [item.canonical() for item in school_records],
            "strategic_admissions_authority": (
                None
                if strategic_admissions_authority is None
                else strategic_admissions_authority.canonical()
            ),
            "strategic_admission_records": [
                item.canonical() for item in strategic_admission_records
            ],
        }
    )


def _verify_education_load(evidence: EducationAccessEvidenceLoad) -> None:
    if type(evidence.as_at) is not date:
        raise GovernedEvidenceLoadError("bound education source requires an exact as_at date")
    if (
        type(evidence.school_register_max_age_days) is not int
        or evidence.school_register_max_age_days < 0
    ):
        raise GovernedEvidenceLoadError(
            "bound education source requires a non-negative freshness window"
        )
    _require_current_artifact(
        evidence.school_register_lineage,
        as_at=evidence.as_at,
        max_age_days=evidence.school_register_max_age_days,
        label="school register",
    )
    if evidence.admissions_lineage is None:
        if evidence.strategic_admissions_max_age_days is not None:
            raise GovernedEvidenceLoadError(
                "admissions freshness policy requires an admissions artifact"
            )
        if (
            evidence.strategic_admissions_authority is not None
            or evidence.strategic_admission_records
        ):
            raise GovernedEvidenceLoadError(
                "typed admissions governance requires an admissions artifact"
            )
    else:
        admissions_max_age = _required_freshness_window(
            evidence.strategic_admissions_max_age_days,
            label="bound strategic_admissions_max_age_days",
        )
        _require_current_artifact(
            evidence.admissions_lineage,
            as_at=evidence.as_at,
            max_age_days=admissions_max_age,
            label="strategic admissions",
        )
    try:
        snapshot = EducationAccessSourceSnapshot.model_validate(
            evidence.source_snapshot.model_dump(mode="python")
        )
    except Exception as error:
        raise GovernedEvidenceLoadError("bound education source snapshot is invalid") from error
    if snapshot != evidence.source_snapshot:
        raise GovernedEvidenceLoadError(
            "bound education source snapshot changed during revalidation"
        )
    if snapshot.option_ids or snapshot.option_evidence:
        raise GovernedEvidenceLoadError(
            "loaded education source binding must not invent option evidence"
        )
    if snapshot.supplementary_pct_evidence:
        raise GovernedEvidenceLoadError(
            "loaded education source binding must not invent PCT evidence"
        )
    if snapshot.register_evidence.evidence_id != evidence.school_register_lineage.source_id:
        raise GovernedEvidenceLoadError(
            "education register evidence does not match raw artifact source"
        )
    governance = evidence.school_register_governance
    if (
        governance.source_id != evidence.school_register_lineage.source_id
        or governance.as_of != evidence.school_register_lineage.effective_date
        or snapshot.register_evidence.source_name != governance.source_name
        or snapshot.register_evidence.as_of != governance.as_of
        or snapshot.register_evidence.governed is not governance.governed
        or snapshot.register_evidence.current is not governance.current
    ):
        raise GovernedEvidenceLoadError(
            "typed school-register governance does not match source snapshot"
        )
    expected_school_rows = tuple(
        (
            school.school_id,
            school.name,
            school.phase,
        )
        for school in snapshot.schools
    )
    bound_school_rows = tuple(
        (record.school_id, record.name, record.phase) for record in evidence.school_records
    )
    if bound_school_rows != expected_school_rows:
        raise GovernedEvidenceLoadError("typed school records do not match source snapshot")
    if evidence.admissions_lineage is None:
        if snapshot.strategic_education_destinations:
            raise GovernedEvidenceLoadError("education destinations require an admissions artifact")
    else:
        authority = evidence.strategic_admissions_authority
        if (
            authority is None
            or authority.source_id != evidence.admissions_lineage.source_id
            or authority.effective_date != evidence.admissions_lineage.effective_date
        ):
            raise GovernedEvidenceLoadError(
                "typed admissions authority does not match raw artifact"
            )
        destinations = snapshot.strategic_education_destinations
        if len(destinations) != len(evidence.strategic_admission_records):
            raise GovernedEvidenceLoadError(
                "typed admission records do not match destination count"
            )
        for destination, record in zip(
            destinations,
            evidence.strategic_admission_records,
            strict=True,
        ):
            if (
                destination.record_id != record.record_id
                or destination.record_version != record.record_version
                or destination.strategic_destination_id != record.strategic_destination_id
                or destination.name != record.name
                or destination.source_evidence_id != evidence.admissions_lineage.source_id
                or destination.admitted_on != record.admitted_on
                or destination.rationale.value != record.rationale
                or destination.review_trigger.value != record.review_trigger
                or destination.access_evidence_ids != record.access_point_evidence_ids
                or record.admission_authority_id != authority.authority_id
            ):
                raise GovernedEvidenceLoadError(
                    "typed admission record does not match approved destination"
                )
    lineages = _education_lineages(
        evidence.school_register_lineage,
        evidence.admissions_lineage,
    )
    expected = _governed_education_fingerprint(
        snapshot,
        lineages,
        school_register_governance=evidence.school_register_governance,
        school_records=evidence.school_records,
        strategic_admissions_authority=(evidence.strategic_admissions_authority),
        strategic_admission_records=evidence.strategic_admission_records,
        as_at=evidence.as_at,
        school_register_max_age_days=evidence.school_register_max_age_days,
        strategic_admissions_max_age_days=(evidence.strategic_admissions_max_age_days),
    )
    if evidence.governed_source_fingerprint != expected:
        raise GovernedEvidenceLoadError("governed education source fingerprint mismatch")


def _require_assessment_source_matches_binding(
    assessment: EducationAccessAssessment,
    source: EducationAccessEvidenceLoad,
) -> None:
    snapshot = assessment.source_snapshot
    bound = source.source_snapshot
    if (
        snapshot.register_evidence != bound.register_evidence
        or snapshot.schools != bound.schools
        or snapshot.strategic_education_destinations != bound.strategic_education_destinations
    ):
        raise GovernedEvidenceLoadError(
            "education assessment governed sources differ from typed binding"
        )


def _required_assessment_date(value: date | None) -> date:
    if type(value) is not date:
        raise GovernedEvidenceLoadError(
            "configured education evidence requires an exact as_at date"
        )
    return value


def _required_freshness_window(value: int | None, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise GovernedEvidenceLoadError(
            f"{label} must be an explicitly declared whole non-negative day count"
        )
    return value


def _required_nonnegative_finite_distance(
    value: float | None,
    *,
    label: str,
) -> float:
    if type(value) not in {int, float}:
        raise GovernedEvidenceLoadError(
            f"{label} must be an explicitly declared finite non-negative metre value"
        )
    distance = float(value)
    if not math.isfinite(distance) or distance < 0:
        raise GovernedEvidenceLoadError(
            f"{label} must be an explicitly declared finite non-negative metre value"
        )
    return 0.0 if distance == 0 else distance


def _require_current_artifact(
    lineage: GovernedArtifactLineage,
    *,
    as_at: date,
    max_age_days: int,
    label: str,
) -> None:
    _require_not_future(
        lineage.effective_date,
        as_at=as_at,
        label=f"{label} effective_date",
    )
    age_days = (as_at - lineage.effective_date).days
    if age_days > max_age_days:
        raise GovernedEvidenceLoadError(
            f"{label} is stale at as_at; maximum age is {max_age_days} days"
        )


def _require_not_future(
    value: date,
    *,
    as_at: date,
    label: str,
) -> None:
    if value > as_at:
        raise GovernedEvidenceLoadError(f"{label} must not be in the future")


def _parse_exact_date(value: object, *, label: str) -> date:
    if not isinstance(value, str):
        raise GovernedEvidenceLoadError(f"{label} must be an ISO-8601 date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise GovernedEvidenceLoadError(f"{label} must be an ISO-8601 date") from error
    if parsed.isoformat() != value:
        raise GovernedEvidenceLoadError(f"{label} must be a canonical ISO-8601 date")
    return parsed


def _strict_identifier_list(
    value: object,
    *,
    label: str,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise GovernedEvidenceLoadError(f"{label} must be a non-empty identifier array")
    identifiers = tuple(_require_strict_identifier(item, label=label) for item in value)
    if len(set(identifiers)) != len(identifiers):
        raise GovernedEvidenceLoadError(f"{label} must not contain duplicates")
    return tuple(sorted(identifiers))


def _require_strict_identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _STRICT_ID.fullmatch(value) is None:
        raise GovernedEvidenceLoadError(f"{label} must be non-blank and contain no whitespace")
    return value


def _require_nonblank_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise GovernedEvidenceLoadError(f"{label} must be non-blank and trimmed")
    return value


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
