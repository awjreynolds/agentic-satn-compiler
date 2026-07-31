"""Atomic, cited and versioned LCWIP adoption-candidate publication."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from datetime import date
from enum import StrEnum
from html import escape
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

import geopandas as gpd
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pypdf import PdfReader
from reportlab.graphics.shapes import Drawing, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from shapely.geometry import mapping, shape

from lcwip.conformance import evaluate_conformance
from lcwip.models import (
    ConformanceResult,
    GuidanceProfile,
    LifecycleState,
    RequirementAssessment,
    _canonical_resource_identity,
)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    ),
]
Sha256 = Annotated[
    str,
    StringConstraints(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]


class PublicationContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )
    schema_version: Literal["1.0"] = "1.0"


class ArtifactKind(StrEnum):
    EVIDENCE = "evidence"
    DEMAND = "demand"
    WALKING = "walking"
    INTERVENTIONS = "interventions"
    PRIORITISATION = "prioritisation"
    GOVERNANCE = "governance"
    NETWORK_GEOJSON = "network-geojson"
    NETWORK_GEOPACKAGE = "network-geopackage"
    AGENT_AUDIT = "agent-audit"
    DECISION_AUDIT = "decision-audit"
    TECHNICAL_REVIEW = "technical-review"


class SourceArtifact(PublicationContract):
    artifact_id: Identifier
    kind: ArtifactKind
    path: Path
    sha256: Sha256
    coverage_status: Text
    quality_status: Text
    limitations: tuple[Text, ...] = Field(min_length=1)
    public: bool
    contains_personal_data: bool

    @model_validator(mode="after")
    def privacy_boundary(self) -> Self:
        if self.public and self.contains_personal_data:
            raise ValueError("public source artifact cannot contain personal data")
        return self


class SourceDescriptor(PublicationContract):
    artifact_id: Identifier
    kind: ArtifactKind
    sha256: Sha256
    coverage_status: Text
    quality_status: Text
    limitations: tuple[Text, ...]
    public: bool
    contains_personal_data: Literal[False]


class SourceRecord(PublicationContract):
    record_id: Identifier
    artifact_id: Identifier
    record_fingerprint: Sha256
    public_summary: Text


class CitationRecord(PublicationContract):
    citation_id: Identifier
    source_artifact_id: Identifier
    source_record_id: Identifier
    label: Text
    uri: Text
    sha256: Sha256

    @field_validator("uri")
    @classmethod
    def public_https_uri(cls, value: str) -> str:
        return _validate_public_uri(value)


class ClaimAuthorityKind(StrEnum):
    ADOPTION = "adoption"
    FEASIBILITY = "feasibility"
    FUNDING = "funding"


class ClaimAuthority(PublicationContract):
    kind: ClaimAuthorityKind
    authority_identifier: Identifier
    authority_name: Text
    decided_on: date
    evidence_uri: Text
    evidence_sha256: Sha256
    release_fingerprint: Sha256

    @field_validator("evidence_uri")
    @classmethod
    def public_https_uri(cls, value: str) -> str:
        return _validate_public_uri(value)


class ClaimPolarity(StrEnum):
    ASSERTION = "assertion"
    LIMITATION = "limitation"
    PLACEHOLDER = "placeholder"


AUTHORITY_TERMS = {
    ClaimAuthorityKind.ADOPTION: re.compile(r"\badopted\b", re.IGNORECASE),
    ClaimAuthorityKind.FEASIBILITY: re.compile(
        r"\b(?:feasible|delivery-ready)\b",
        re.IGNORECASE,
    ),
    ClaimAuthorityKind.FUNDING: re.compile(
        r"\b(?:funded|funding committed|fully financed)\b",
        re.IGNORECASE,
    ),
}


class NarrativeClaim(PublicationContract):
    claim_id: Identifier
    text: Text
    category: Literal[
        "evidence",
        "analysis",
        "proposal",
        "uncertainty",
        "consultation-change",
        "decision",
        "method",
        "policy",
    ]
    polarity: ClaimPolarity
    material: bool
    citation_ids: tuple[Identifier, ...]
    authority: ClaimAuthority | None = None

    @model_validator(mode="after")
    def cited_and_authorised(self) -> Self:
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("claim citations must be unique")
        if self.polarity is ClaimPolarity.PLACEHOLDER:
            if not self.text.startswith("[Evidence required:") or self.citation_ids:
                raise ValueError(
                    "placeholder must be an explicit uncited Evidence required marker"
                )
            if self.authority is not None:
                raise ValueError("placeholder cannot claim supporting authority")
            return self
        if self.material and not self.citation_ids:
            raise ValueError("material claim requires at least one governed citation")
        positive_authority_kinds = {
            kind for kind, pattern in AUTHORITY_TERMS.items() if pattern.search(self.text)
        }
        if self.polarity is ClaimPolarity.LIMITATION:
            if not re.search(
                r"\b(?:not|unresolved|unknown|gap|limitation|candidate)\b",
                self.text,
                re.IGNORECASE,
            ):
                raise ValueError("limitation claim must state its bounded status")
            positive_authority_kinds = set()
        if positive_authority_kinds:
            if self.authority is None or self.authority.kind not in positive_authority_kinds:
                raise ValueError(
                    "authority claim requires exact typed supporting authority"
                )
        elif self.authority is not None and self.polarity is not ClaimPolarity.ASSERTION:
            raise ValueError("only an asserted authority claim may carry authority")
        return self


class ReportSectionKind(StrEnum):
    EXECUTIVE_SUMMARY = "executive-summary"
    STATUS_CONFORMANCE = "status-conformance"
    NETWORK_PLANS = "network-plans"
    INTERVENTION_PROGRAMME = "intervention-programme"
    METHODS = "methods"
    ENGAGEMENT = "engagement"
    EQUALITY = "equality"
    POLICY = "policy"
    APPENDICES = "appendices"


class ReportSection(PublicationContract):
    kind: ReportSectionKind
    title: Text
    introduction: Text
    introduction_citation_ids: tuple[Identifier, ...] = Field(min_length=1)
    claims: tuple[NarrativeClaim, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_content(self) -> Self:
        if len(self.introduction_citation_ids) != len(
            set(self.introduction_citation_ids)
        ):
            raise ValueError("section introduction citations must be unique")
        if len({item.claim_id for item in self.claims}) != len(self.claims):
            raise ValueError("section claim IDs must be unique")
        if any(pattern.search(self.introduction) for pattern in AUTHORITY_TERMS.values()):
            raise ValueError(
                "section introduction cannot make an untyped authority claim"
            )
        return self


Metric = float | int | str | bool


class NetworkFeatureRecord(PublicationContract):
    feature_id: Identifier
    source_artifact_id: Identifier
    source_record_id: Identifier
    status: Text
    metrics: dict[Identifier, Metric] = Field(min_length=1)
    geometry_sha256: Sha256
    bbox: tuple[float, float, float, float]

    @model_validator(mode="after")
    def valid_bbox(self) -> Self:
        if self.bbox[0] > self.bbox[2] or self.bbox[1] > self.bbox[3]:
            raise ValueError("network feature bounding box is invalid")
        return self


class ProgrammeScheduleRecord(PublicationContract):
    intervention_id: Identifier
    source_record_id: Identifier
    phase: Text
    status: Text
    lower_cost: float = Field(ge=0)
    upper_cost: float = Field(ge=0)
    dependencies: tuple[Identifier, ...]
    risks: tuple[Text, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def cost_range(self) -> Self:
        if self.upper_cost < self.lower_cost:
            raise ValueError("programme upper cost must not be below lower cost")
        if self.intervention_id in self.dependencies:
            raise ValueError("programme intervention cannot depend on itself")
        return self


class ConsultationChange(PublicationContract):
    change_id: Identifier
    source_record_id: Identifier
    representation_ids: tuple[Identifier, ...] = Field(min_length=1)
    before: Text
    after: Text
    human_decision_record_id: Identifier
    citation_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def actual_change(self) -> Self:
        if self.before == self.after:
            raise ValueError("consultation change must record distinct before and after")
        return self


class PrivacySafeAuditEntry(PublicationContract):
    entry_id: Identifier
    kind: Literal[
        "agent-decision",
        "human-decision",
        "amendment",
        "external-decision",
    ]
    occurred_on: date
    actor: Text
    summary: Text
    source_record_id: Identifier
    citation_ids: tuple[Identifier, ...] = Field(min_length=1)
    contains_personal_data: Literal[False] = False


class PublicationAdoptionAnnotation(PublicationContract):
    decision_identifier: Identifier
    authority_name: Text
    decision_date: date
    decision_uri: Text
    verifier_name: Text
    verification_evidence_uri: Text
    verification_date: date
    release_fingerprint: Sha256

    @field_validator("decision_uri", "verification_evidence_uri")
    @classmethod
    def public_https_uri(cls, value: str) -> str:
        return _validate_public_uri(value)

    @model_validator(mode="after")
    def independently_verified(self) -> Self:
        if self.verification_date < self.decision_date:
            raise ValueError("adoption verification cannot predate the decision")
        if _canonical_resource_identity(
            self.decision_uri
        ) == _canonical_resource_identity(self.verification_evidence_uri):
            raise ValueError("adoption verification evidence must be distinct")
        if self.verifier_name.casefold() == self.authority_name.casefold():
            raise ValueError("adoption verifier must be distinct from authority")
        return self


class PublicationConfig(PublicationContract):
    output_dir: Path
    release_id: Identifier
    release_version: Text
    plan_area: Text
    publication_date: date
    lifecycle_state: LifecycleState
    evidence_fingerprint: Sha256
    configuration_fingerprint: Sha256
    guidance_profile: GuidanceProfile
    requirement_assessments: tuple[RequirementAssessment, ...]
    sources: tuple[SourceArtifact, ...] = Field(min_length=3)
    source_records: tuple[SourceRecord, ...] = Field(min_length=1)
    citations: tuple[CitationRecord, ...] = Field(min_length=1)
    sections: tuple[ReportSection, ...] = Field(min_length=1)
    network_features: tuple[NetworkFeatureRecord, ...] = Field(min_length=1)
    programme: tuple[ProgrammeScheduleRecord, ...] = Field(min_length=1)
    consultation_changes: tuple[ConsultationChange, ...]
    unresolved_representation_record_ids: tuple[Identifier, ...]
    unresolved_equality_finding_record_ids: tuple[Identifier, ...]
    audit_entries: tuple[PrivacySafeAuditEntry, ...] = Field(min_length=1)
    adoption_annotation: PublicationAdoptionAnnotation | None = None
    previous_release_dir: Path | None = None


class ArtifactWatermark(PublicationContract):
    plan_area: Text
    release_id: Identifier
    release_version: Text
    lifecycle_state: LifecycleState
    evidence_fingerprint: Sha256
    configuration_fingerprint: Sha256
    release_fingerprint: Sha256
    publication_date: date


class PublicationBlocker(PublicationContract):
    blocker_id: Identifier
    category: Literal[
        "mandatory-conformance",
        "representation",
        "equality",
    ]
    message: Text
    source_record_id: Identifier | None


class SpatialChange(PublicationContract):
    feature_id: Identifier
    change: Literal["added", "removed", "geometry-changed", "attributes-changed"]
    previous_geometry_sha256: Sha256 | None
    current_geometry_sha256: Sha256 | None
    previous_bbox: tuple[float, float, float, float] | None
    current_bbox: tuple[float, float, float, float] | None


class ReleaseDiff(PublicationContract):
    previous_release_id: Identifier | None
    current_release_id: Identifier
    changed_categories: tuple[
        Literal[
            "evidence",
            "method",
            "geometry",
            "programme",
            "narrative",
            "decision",
        ],
        ...,
    ]
    spatial_changes: tuple[SpatialChange, ...]
    category_fingerprints: dict[str, dict[str, Sha256 | None]]


class ReleaseHistoryEntry(PublicationContract):
    release_id: Identifier
    release_version: Text
    publication_date: date
    lifecycle_state: LifecycleState
    release_fingerprint: Sha256
    publication_fingerprint: Sha256


class ReleaseHistory(PublicationContract):
    watermark: ArtifactWatermark
    releases: tuple[ReleaseHistoryEntry, ...] = Field(min_length=1)


class PublicationArtifact(PublicationContract):
    path: Text
    sha256: Sha256
    size_bytes: int = Field(ge=1)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("publication artifact path must remain inside release")
        return value


ARTIFACTS = (
    "conformance-manifest.json",
    "decision-agent-audit.json",
    "executive-summary.html",
    "lcwip-report.pdf",
    "network-plan.geojson",
    "network-plan.gpkg",
    "programme-schedule.json",
    "release-diff.json",
    "release-history.json",
    "report-data.json",
    "source-coverage-quality.json",
    "lcwip-release.zip",
)
LCWIP_ZIP_MAX_MEMBER_BYTES = 100 * 1024 * 1024
LCWIP_ZIP_MAX_TOTAL_BYTES = 500 * 1024 * 1024
LCWIP_ZIP_MAX_COMPRESSION_RATIO = 200


class PublicationManifest(PublicationContract):
    release_id: Identifier
    release_version: Text
    plan_area: Text
    publication_date: date
    lifecycle_state: LifecycleState
    evidence_fingerprint: Sha256
    configuration_fingerprint: Sha256
    release_fingerprint: Sha256
    publication_fingerprint: Sha256
    watermark: ArtifactWatermark
    conformance: ConformanceResult
    unresolved_mandatory_requirement_ids: tuple[Identifier, ...]
    blockers: tuple[PublicationBlocker, ...]
    sources: tuple[SourceDescriptor, ...]
    source_records: tuple[SourceRecord, ...]
    citations: tuple[CitationRecord, ...]
    sections: tuple[ReportSection, ...]
    network_features: tuple[NetworkFeatureRecord, ...]
    programme: tuple[ProgrammeScheduleRecord, ...]
    consultation_changes: tuple[ConsultationChange, ...]
    unresolved_representation_record_ids: tuple[Identifier, ...]
    unresolved_equality_finding_record_ids: tuple[Identifier, ...]
    audit_entries: tuple[PrivacySafeAuditEntry, ...]
    adoption_annotation: PublicationAdoptionAnnotation | None
    release_diff: ReleaseDiff
    release_history: ReleaseHistory
    artifacts: tuple[PublicationArtifact, ...]
    manifest_fingerprint: Sha256

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        paths = tuple(item.path for item in self.artifacts)
        if len(paths) != len(set(paths)) or set(paths) != set(ARTIFACTS):
            raise ValueError("publication artifact set is incomplete")
        if self.watermark != _watermark(self):
            raise ValueError("publication watermark does not match")
        if self.release_fingerprint != _release_fingerprint(self):
            raise ValueError("LCWIP release fingerprint does not match")
        if self.publication_fingerprint != _publication_fingerprint(self):
            raise ValueError("LCWIP publication fingerprint does not match")
        expected = _fingerprint(
            self.model_dump(mode="json", exclude={"manifest_fingerprint"})
        )
        if self.manifest_fingerprint != expected:
            raise ValueError("publication manifest fingerprint does not match")
        _validate_manifest_content(self)
        return self


def lcwip_release_fingerprint(config: PublicationConfig) -> str:
    """Fingerprint substantive content before an external adoption annotation."""
    validated = PublicationConfig.model_validate(config.model_dump())
    return _release_fingerprint(validated)


def build_lcwip_publication(config: PublicationConfig) -> Path:
    """Build and atomically archive one immutable LCWIP publication release."""
    config = PublicationConfig.model_validate(config.model_dump())
    conformance = evaluate_conformance(
        config.guidance_profile,
        config.requirement_assessments,
    )
    _validate_config(config, conformance)
    release_fingerprint = _release_fingerprint(config)
    _validate_adoption(config, release_fingerprint)
    watermark = _watermark(config, release_fingerprint=release_fingerprint)
    sources = _source_descriptors(config.sources)
    blockers = _blockers(
        conformance,
        config.unresolved_representation_record_ids,
        config.unresolved_equality_finding_record_ids,
    )
    previous = _previous_manifest(config)
    release_diff = _release_diff(config, previous)
    publication_fingerprint = _publication_fingerprint(
        config,
        release_fingerprint=release_fingerprint,
    )
    history = _release_history(
        config,
        watermark,
        release_fingerprint,
        publication_fingerprint,
    )
    destination = config.output_dir / config.release_id
    if destination.exists():
        existing = validate_lcwip_publication(destination)
        if existing.publication_fingerprint != publication_fingerprint:
            raise ValueError("LCWIP publication release is immutable and content changed")
        return destination
    config.output_dir.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{config.release_id}-", dir=config.output_dir)
    )
    try:
        _write_publication_files(
            temporary,
            config,
            watermark,
            conformance,
            blockers,
            sources,
            release_diff,
            history,
        )
        artifacts = tuple(
            PublicationArtifact(
                path=filename,
                sha256=_file_hash(temporary / filename),
                size_bytes=(temporary / filename).stat().st_size,
            )
            for filename in ARTIFACTS
        )
        manifest_payload = {
            "schema_version": "1.0",
            "release_id": config.release_id,
            "release_version": config.release_version,
            "plan_area": config.plan_area,
            "publication_date": config.publication_date,
            "lifecycle_state": config.lifecycle_state,
            "evidence_fingerprint": config.evidence_fingerprint,
            "configuration_fingerprint": config.configuration_fingerprint,
            "release_fingerprint": release_fingerprint,
            "publication_fingerprint": publication_fingerprint,
            "watermark": watermark,
            "conformance": conformance,
            "unresolved_mandatory_requirement_ids": (
                conformance.unresolved_mandatory_requirement_ids
            ),
            "blockers": blockers,
            "sources": sources,
            "source_records": config.source_records,
            "citations": config.citations,
            "sections": config.sections,
            "network_features": config.network_features,
            "programme": config.programme,
            "consultation_changes": config.consultation_changes,
            "unresolved_representation_record_ids": (
                config.unresolved_representation_record_ids
            ),
            "unresolved_equality_finding_record_ids": (
                config.unresolved_equality_finding_record_ids
            ),
            "audit_entries": config.audit_entries,
            "adoption_annotation": config.adoption_annotation,
            "release_diff": release_diff,
            "release_history": history,
            "artifacts": artifacts,
        }
        manifest = PublicationManifest(
            **manifest_payload,
            manifest_fingerprint=_fingerprint(manifest_payload),
        )
        _write_json(temporary / "publication-manifest.json", manifest)
        validate_lcwip_publication(temporary)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return destination


def validate_lcwip_publication(path: Path) -> PublicationManifest:
    """Validate every artifact and cross-artifact contract in a release."""
    path = Path(path)
    try:
        manifest = PublicationManifest.model_validate_json(
            (path / "publication-manifest.json").read_text()
        )
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid LCWIP publication: {error}") from error
    expected_files = {"publication-manifest.json", *ARTIFACTS}
    actual_files = {
        item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_file()
    }
    if actual_files != expected_files:
        raise ValueError("invalid LCWIP publication: file set mismatch")
    for artifact in manifest.artifacts:
        artifact_path = path / artifact.path
        if (
            not artifact_path.is_file()
            or _file_hash(artifact_path) != artifact.sha256
            or artifact_path.stat().st_size != artifact.size_bytes
        ):
            raise ValueError(
                f"invalid LCWIP publication: {artifact.path} content hash mismatch"
            )
    _validate_json_artifacts(path, manifest)
    _validate_geojson(path / "network-plan.geojson", manifest)
    _validate_geopackage(path / "network-plan.gpkg", manifest)
    _validate_html(path / "executive-summary.html", manifest)
    _validate_pdf(path / "lcwip-report.pdf", manifest)
    _validate_zip(path / "lcwip-release.zip", manifest)
    return manifest


def _validate_config(
    config: PublicationConfig,
    conformance: ConformanceResult,
) -> None:
    source_ids = _unique_ids(config.sources, "artifact_id", "source artifact")
    for required in (ArtifactKind.NETWORK_GEOJSON, ArtifactKind.NETWORK_GEOPACKAGE):
        _source_of_kind(config.sources, required)
    if not any(item.kind is ArtifactKind.EVIDENCE for item in config.sources):
        raise ValueError("publication requires source artifact kind evidence")
    for source in config.sources:
        if not source.path.is_file():
            raise ValueError(f"source artifact is missing: {source.artifact_id}")
        if _file_hash(source.path) != source.sha256:
            raise ValueError(f"source artifact hash mismatch: {source.artifact_id}")
        if not source.public or source.contains_personal_data:
            raise ValueError("publication sources must be privacy-safe public artifacts")
    record_ids = _unique_ids(config.source_records, "record_id", "source record")
    for record in config.source_records:
        if record.artifact_id not in source_ids:
            raise ValueError("source record artifact must resolve")
    citation_ids = _unique_ids(config.citations, "citation_id", "citation")
    records = {item.record_id: item for item in config.source_records}
    for citation in config.citations:
        record = records.get(citation.source_record_id)
        if (
            record is None
            or citation.source_artifact_id != record.artifact_id
            or citation.sha256 != record.record_fingerprint
        ):
            raise ValueError("citation source record and fingerprint must resolve")
    if {section.kind for section in config.sections} != set(ReportSectionKind):
        raise ValueError("LCWIP report must contain every required section")
    if len(config.sections) != len(ReportSectionKind):
        raise ValueError("LCWIP report section kinds must be unique")
    claim_ids: set[str] = set()
    for section in config.sections:
        if not set(section.introduction_citation_ids).issubset(citation_ids):
            raise ValueError("section introduction citation must resolve")
        for claim in section.claims:
            if claim.claim_id in claim_ids:
                raise ValueError("report claim IDs must be unique")
            claim_ids.add(claim.claim_id)
            if not set(claim.citation_ids).issubset(citation_ids):
                raise ValueError("claim citation must resolve")
            if claim.authority is not None and not any(
                citation.sha256 == claim.authority.evidence_sha256
                and _canonical_resource_identity(citation.uri)
                == _canonical_resource_identity(claim.authority.evidence_uri)
                for citation in config.citations
                if citation.citation_id in claim.citation_ids
            ):
                raise ValueError(
                    "claim authority evidence must resolve to a claim citation"
                )
            if (
                AUTHORITY_TERMS[ClaimAuthorityKind.ADOPTION].search(claim.text)
                and claim.polarity is ClaimPolarity.ASSERTION
                and config.lifecycle_state is not LifecycleState.ADOPTED
            ):
                raise ValueError("adopted claim requires adopted lifecycle state")
    _unique_ids(config.network_features, "feature_id", "network feature")
    network_geojson = _source_of_kind(config.sources, ArtifactKind.NETWORK_GEOJSON)
    network_geopackage = _source_of_kind(
        config.sources,
        ArtifactKind.NETWORK_GEOPACKAGE,
    )
    for feature in config.network_features:
        if (
            feature.source_artifact_id != network_geojson.artifact_id
            or feature.source_record_id not in record_ids
        ):
            raise ValueError("network feature provenance must resolve")
    _validate_source_geojson(
        network_geojson.path,
        config.network_features,
    )
    _validate_source_geopackage(
        network_geopackage.path,
        config.network_features,
    )
    _unique_ids(config.programme, "intervention_id", "programme intervention")
    for entry in config.programme:
        if entry.source_record_id not in record_ids:
            raise ValueError("programme source record must resolve")
        if not set(entry.dependencies).issubset(
            {item.intervention_id for item in config.programme}
        ):
            raise ValueError("programme dependencies must resolve")
    _unique_ids(config.consultation_changes, "change_id", "consultation change")
    for change in config.consultation_changes:
        if change.source_record_id not in record_ids:
            raise ValueError("consultation change source record must resolve")
        if not set(change.citation_ids).issubset(citation_ids):
            raise ValueError("consultation change citations must resolve")
    if not set(config.unresolved_representation_record_ids).issubset(record_ids):
        raise ValueError("unresolved representation record must resolve")
    if not set(config.unresolved_equality_finding_record_ids).issubset(record_ids):
        raise ValueError("unresolved equality finding record must resolve")
    _unique_ids(config.audit_entries, "entry_id", "audit entry")
    for entry in config.audit_entries:
        if entry.source_record_id not in record_ids:
            raise ValueError("audit source record must resolve")
        if not set(entry.citation_ids).issubset(citation_ids):
            raise ValueError("audit citations must resolve")
    if conformance.profile.fingerprint != config.guidance_profile.fingerprint:
        raise ValueError("conformance Guidance Profile must resolve")


def _validate_adoption(config: PublicationConfig, release_fingerprint: str) -> None:
    if config.lifecycle_state is LifecycleState.ADOPTED:
        if config.adoption_annotation is None:
            raise ValueError("adopted publication requires a typed adoption annotation")
        if config.adoption_annotation.release_fingerprint != release_fingerprint:
            raise ValueError("adoption annotation release fingerprint mismatch")
    elif config.adoption_annotation is not None:
        raise ValueError("adoption annotation is valid only for adopted lifecycle")
    for section in config.sections:
        for claim in section.claims:
            authority = claim.authority
            if authority is None:
                continue
            if authority.release_fingerprint != release_fingerprint:
                raise ValueError(
                    "claim authority must be bound to the exact release fingerprint"
                )
            if authority.kind is not ClaimAuthorityKind.ADOPTION:
                continue
            annotation = config.adoption_annotation
            if (
                annotation is None
                or authority.authority_identifier != annotation.decision_identifier
                or authority.authority_name != annotation.authority_name
                or authority.decided_on != annotation.decision_date
                or _canonical_resource_identity(authority.evidence_uri)
                != _canonical_resource_identity(annotation.decision_uri)
                or authority.release_fingerprint != release_fingerprint
            ):
                raise ValueError(
                    "adoption claim authority must match the release adoption annotation"
                )


def _validate_manifest_content(manifest: PublicationManifest) -> None:
    """Reject internally inconsistent manifests even when hashes are recomputed."""
    source_ids = _unique_ids(manifest.sources, "artifact_id", "source artifact")
    for required in (ArtifactKind.NETWORK_GEOJSON, ArtifactKind.NETWORK_GEOPACKAGE):
        _source_of_kind(manifest.sources, required)
    if not any(item.kind is ArtifactKind.EVIDENCE for item in manifest.sources):
        raise ValueError("publication requires source artifact kind evidence")
    record_ids = _unique_ids(manifest.source_records, "record_id", "source record")
    for record in manifest.source_records:
        if record.artifact_id not in source_ids:
            raise ValueError("source record artifact must resolve")
    citation_ids = _unique_ids(manifest.citations, "citation_id", "citation")
    records = {item.record_id: item for item in manifest.source_records}
    for citation in manifest.citations:
        record = records.get(citation.source_record_id)
        if (
            record is None
            or citation.source_artifact_id != record.artifact_id
            or citation.sha256 != record.record_fingerprint
        ):
            raise ValueError("citation source record and fingerprint must resolve")
    if {section.kind for section in manifest.sections} != set(ReportSectionKind):
        raise ValueError("LCWIP report must contain every required section")
    if len(manifest.sections) != len(ReportSectionKind):
        raise ValueError("LCWIP report section kinds must be unique")
    claim_ids: set[str] = set()
    for section in manifest.sections:
        if not set(section.introduction_citation_ids).issubset(citation_ids):
            raise ValueError("section introduction citation must resolve")
        for claim in section.claims:
            if claim.claim_id in claim_ids:
                raise ValueError("report claim IDs must be unique")
            claim_ids.add(claim.claim_id)
            if not set(claim.citation_ids).issubset(citation_ids):
                raise ValueError("claim citation must resolve")
            if claim.authority is not None and not any(
                citation.sha256 == claim.authority.evidence_sha256
                and _canonical_resource_identity(citation.uri)
                == _canonical_resource_identity(claim.authority.evidence_uri)
                for citation in manifest.citations
                if citation.citation_id in claim.citation_ids
            ):
                raise ValueError(
                    "claim authority evidence must resolve to a claim citation"
                )
            if (
                AUTHORITY_TERMS[ClaimAuthorityKind.ADOPTION].search(claim.text)
                and claim.polarity is ClaimPolarity.ASSERTION
                and manifest.lifecycle_state is not LifecycleState.ADOPTED
            ):
                raise ValueError("adopted claim requires adopted lifecycle state")
    _unique_ids(manifest.network_features, "feature_id", "network feature")
    geojson_source = _source_of_kind(
        manifest.sources,
        ArtifactKind.NETWORK_GEOJSON,
    )
    for feature in manifest.network_features:
        if (
            feature.source_artifact_id != geojson_source.artifact_id
            or feature.source_record_id not in record_ids
        ):
            raise ValueError("network feature provenance must resolve")
    intervention_ids = _unique_ids(
        manifest.programme,
        "intervention_id",
        "programme intervention",
    )
    for entry in manifest.programme:
        if entry.source_record_id not in record_ids:
            raise ValueError("programme source record must resolve")
        if not set(entry.dependencies).issubset(intervention_ids):
            raise ValueError("programme dependencies must resolve")
    _unique_ids(manifest.consultation_changes, "change_id", "consultation change")
    for change in manifest.consultation_changes:
        if (
            change.source_record_id not in record_ids
            or not set(change.citation_ids).issubset(citation_ids)
        ):
            raise ValueError("consultation change provenance must resolve")
    if not set(manifest.unresolved_representation_record_ids).issubset(record_ids):
        raise ValueError("unresolved representation record must resolve")
    if not set(manifest.unresolved_equality_finding_record_ids).issubset(record_ids):
        raise ValueError("unresolved equality finding record must resolve")
    _unique_ids(manifest.audit_entries, "entry_id", "audit entry")
    for entry in manifest.audit_entries:
        if (
            entry.source_record_id not in record_ids
            or not set(entry.citation_ids).issubset(citation_ids)
        ):
            raise ValueError("audit provenance must resolve")
    if (
        manifest.unresolved_mandatory_requirement_ids
        != manifest.conformance.unresolved_mandatory_requirement_ids
    ):
        raise ValueError("manifest mandatory gaps differ from conformance")
    if manifest.blockers != _blockers(
        manifest.conformance,
        manifest.unresolved_representation_record_ids,
        manifest.unresolved_equality_finding_record_ids,
    ):
        raise ValueError("publication blockers differ from governed unresolved records")
    if manifest.release_diff.current_release_id != manifest.release_id:
        raise ValueError("release diff current release does not match")
    if manifest.release_history.watermark != manifest.watermark:
        raise ValueError("release history watermark does not match")
    current_history = tuple(
        entry
        for entry in manifest.release_history.releases
        if entry.release_id == manifest.release_id
    )
    if len(current_history) != 1 or current_history[0] != ReleaseHistoryEntry(
        release_id=manifest.release_id,
        release_version=manifest.release_version,
        publication_date=manifest.publication_date,
        lifecycle_state=manifest.lifecycle_state,
        release_fingerprint=manifest.release_fingerprint,
        publication_fingerprint=manifest.publication_fingerprint,
    ):
        raise ValueError("release history current entry does not match publication")
    if manifest.lifecycle_state is LifecycleState.ADOPTED:
        annotation = manifest.adoption_annotation
        if annotation is None or annotation.release_fingerprint != manifest.release_fingerprint:
            raise ValueError("adopted publication requires an exact adoption annotation")
    elif manifest.adoption_annotation is not None:
        raise ValueError("adoption annotation is valid only for adopted lifecycle")
    for section in manifest.sections:
        for claim in section.claims:
            authority = claim.authority
            if authority is None:
                continue
            if authority.release_fingerprint != manifest.release_fingerprint:
                raise ValueError(
                    "claim authority must be bound to the exact release fingerprint"
                )
            if authority.kind is not ClaimAuthorityKind.ADOPTION:
                continue
            annotation = manifest.adoption_annotation
            if (
                annotation is None
                or authority.authority_identifier != annotation.decision_identifier
                or authority.authority_name != annotation.authority_name
                or authority.decided_on != annotation.decision_date
                or _canonical_resource_identity(authority.evidence_uri)
                != _canonical_resource_identity(annotation.decision_uri)
                or authority.release_fingerprint != manifest.release_fingerprint
            ):
                raise ValueError(
                    "adoption claim authority must match the release adoption annotation"
                )


def _source_of_kind(
    sources: tuple[SourceArtifact, ...] | tuple[SourceDescriptor, ...],
    kind: ArtifactKind,
) -> SourceArtifact | SourceDescriptor:
    matches = tuple(item for item in sources if item.kind is kind)
    if len(matches) != 1:
        raise ValueError(
            f"publication requires exactly one source artifact kind {kind.value}"
        )
    return matches[0]


def _write_publication_files(
    destination: Path,
    config: PublicationConfig,
    watermark: ArtifactWatermark,
    conformance: ConformanceResult,
    blockers: tuple[PublicationBlocker, ...],
    sources: tuple[SourceDescriptor, ...],
    release_diff: ReleaseDiff,
    history: ReleaseHistory,
) -> None:
    report_data = _report_payload(config, watermark)
    _write_json(destination / "report-data.json", report_data)
    _write_json(
        destination / "conformance-manifest.json",
        {
            "watermark": watermark,
            "profile": config.guidance_profile,
            "profile_fingerprint": config.guidance_profile.fingerprint,
            "requirements": conformance.requirements,
            "unresolved_mandatory_requirement_ids": (
                conformance.unresolved_mandatory_requirement_ids
            ),
            "blockers": blockers,
            "conformance_fingerprint": conformance.conformance_fingerprint,
        },
    )
    _write_json(
        destination / "source-coverage-quality.json",
        {
            "watermark": watermark,
            "sources": sources,
            "source_records": config.source_records,
        },
    )
    _write_json(
        destination / "decision-agent-audit.json",
        {
            "watermark": watermark,
            "entries": config.audit_entries,
            "consultation_changes": config.consultation_changes,
            "adoption_annotation": config.adoption_annotation,
        },
    )
    _write_json(
        destination / "programme-schedule.json",
        {
            "watermark": watermark,
            "programme": config.programme,
        },
    )
    _write_json(
        destination / "release-diff.json",
        {"watermark": watermark, **release_diff.model_dump(mode="json")},
    )
    _write_json(destination / "release-history.json", history)
    network_geojson = _source_of_kind(config.sources, ArtifactKind.NETWORK_GEOJSON)
    network_geopackage = _source_of_kind(
        config.sources,
        ArtifactKind.NETWORK_GEOPACKAGE,
    )
    _write_watermarked_geojson(
        network_geojson.path,
        destination / "network-plan.geojson",
        watermark,
    )
    _write_watermarked_geopackage(
        network_geopackage.path,
        destination / "network-plan.gpkg",
        watermark,
    )
    _write_html(
        destination / "executive-summary.html",
        config,
        watermark,
        conformance,
        blockers,
    )
    _write_pdf(
        destination / "lcwip-report.pdf",
        config,
        watermark,
        conformance,
        blockers,
    )
    _write_zip(destination / "lcwip-release.zip", destination, watermark)


def _report_payload(
    config: PublicationConfig,
    watermark: ArtifactWatermark,
) -> dict[str, Any]:
    return {
        "watermark": watermark,
        "sections": config.sections,
        "citations": config.citations,
        "network_features": config.network_features,
        "programme": config.programme,
        "consultation_changes": config.consultation_changes,
        "unresolved_representation_record_ids": (
            config.unresolved_representation_record_ids
        ),
        "unresolved_equality_finding_record_ids": (
            config.unresolved_equality_finding_record_ids
        ),
    }


def _write_html(
    path: Path,
    config: PublicationConfig,
    watermark: ArtifactWatermark,
    conformance: ConformanceResult,
    blockers: tuple[PublicationBlocker, ...],
) -> None:
    status = _status_banner(config.lifecycle_state)
    citation_numbers = {
        item.citation_id: index
        for index, item in enumerate(config.citations, start=1)
    }
    nav = "".join(
        f'<li><a href="#{section.kind.value}">{escape(section.title)}</a></li>'
        for section in config.sections
    )
    gap_items = "".join(
        f"<li><strong>{escape(item.blocker_id)}</strong>: {escape(item.message)}</li>"
        for item in blockers
    )
    section_html = []
    for section in config.sections:
        intro_citations = _html_citation_links(
            section.introduction_citation_ids,
            citation_numbers,
        )
        claims = "".join(
            (
                f'<article class="claim {claim.polarity.value}">'
                f"<p>{escape(claim.text)} "
                f"{_html_citation_links(claim.citation_ids, citation_numbers)}</p>"
                f'<p class="claim-meta">{escape(claim.category)} - '
                f"{escape(claim.polarity.value)}</p></article>"
            )
            for claim in section.claims
        )
        section_html.append(
            f'<section id="{section.kind.value}" '
            f'aria-labelledby="{section.kind.value}-heading">'
            f'<h2 id="{section.kind.value}-heading">{escape(section.title)}</h2>'
            f"<p>{escape(section.introduction)} {intro_citations}</p>{claims}</section>"
        )
    network_rows = "".join(
        f"<tr><th scope=\"row\">{escape(item.feature_id)}</th>"
        f"<td>{escape(item.status)}</td>"
        f"<td>{escape(json.dumps(item.metrics, sort_keys=True))}</td></tr>"
        for item in config.network_features
    )
    programme_rows = "".join(
        f"<tr><th scope=\"row\">{escape(item.intervention_id)}</th>"
        f"<td>{escape(item.phase)}</td><td>{escape(item.status)}</td>"
        f"<td>GBP {item.lower_cost:,.0f} - {item.upper_cost:,.0f}</td></tr>"
        for item in config.programme
    )
    citations = "".join(
        f'<li id="citation-{index}"><a href="{escape(item.uri)}">'
        f"[{index}] {escape(item.label)}</a> "
        f"<code>{escape(item.sha256[:12])}</code></li>"
        for index, item in enumerate(config.citations, start=1)
    )
    network_svg = _network_svg(config)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="lcwip-release-id" content="{escape(config.release_id)}">
  <meta name="lcwip-release-fingerprint" content="{watermark.release_fingerprint}">
  <title>{escape(config.plan_area)} LCWIP - {escape(status)}</title>
  <style>
    :root {{ color-scheme: light; --ink:#17212b; --blue:#005a8d; --pale:#eaf4f8;
      --amber:#ffd166; --red:#a61b1b; }}
    * {{ box-sizing:border-box; }} body {{ margin:0; color:var(--ink);
      font:18px/1.55 system-ui,sans-serif; background:#fff; }}
    a {{ color:#004f7c; }} a:focus-visible {{ outline:4px solid #ffbf47; outline-offset:3px; }}
    .skip {{ position:absolute; left:-9999px; }} .skip:focus {{ left:1rem; top:1rem;
      z-index:2; padding:.75rem; background:#fff; }}
    header, main, footer {{ max-width:76rem; margin:auto; padding:1.5rem; }}
    .status {{ padding:1rem; border:3px solid var(--blue); background:var(--pale);
      font-weight:800; letter-spacing:.04em; }}
    .gaps {{ border-left:.6rem solid var(--red); padding:1rem; background:#fff0f0; }}
    nav ul {{ display:flex; flex-wrap:wrap; gap:.5rem 1.25rem; padding-left:1.25rem; }}
    section {{ margin:2.5rem 0; }} .claim {{ border-left:.3rem solid var(--blue);
      padding:.25rem 1rem; margin:1rem 0; }} .placeholder {{ border-color:var(--amber); }}
    .claim-meta {{ font-size:.85em; font-weight:700; }}
    table {{ border-collapse:collapse; width:100%; margin:1rem 0 2rem; }}
    caption {{ text-align:left; font-weight:800; margin-bottom:.5rem; }}
    th, td {{ border:1px solid #50606e; padding:.6rem; text-align:left; vertical-align:top; }}
    th {{ background:var(--pale); }} code {{ overflow-wrap:anywhere; }}
    .network-map {{ margin:1.5rem 0 2rem; }} .network-map svg {{ width:100%;
      height:auto; max-height:34rem; border:1px solid #50606e; background:#f7fbfd; }}
    .network-map figcaption {{ font-weight:700; margin-top:.4rem; }}
    footer {{ border-top:2px solid var(--blue); font-size:.85em; }}
    @media print {{ nav,.skip {{ display:none; }} a {{ color:inherit; text-decoration:none; }}
      section {{ break-inside:avoid; }} }}
  </style>
</head>
<body>
<a class="skip" href="#main-content">Skip to main content</a>
<header>
  <p class="status">{escape(status)}</p>
  <h1>{escape(config.plan_area)} Local Cycling and Walking Infrastructure Plan</h1>
  <p>Release {escape(config.release_id)} - version {escape(config.release_version)} -
  {config.publication_date.isoformat()}</p>
  <p>Evidence {config.evidence_fingerprint[:12]} - Configuration
  {config.configuration_fingerprint[:12]} - Release {watermark.release_fingerprint[:12]}</p>
  <nav aria-label="Report sections"><h2>Contents</h2><ul>{nav}</ul></nav>
</header>
<main id="main-content">
  <aside class="gaps" role="alert"><h2>Mandatory conformance gaps and blockers</h2>
  <ul>{gap_items or "<li>None recorded.</li>"}</ul></aside>
  {''.join(section_html)}
  <section aria-labelledby="network-map-heading">
    <h2 id="network-map-heading">Indicative network plan map</h2>
    <figure class="network-map">{network_svg}
      <figcaption>Indicative network geometry; use the following schedule for a
      complete non-map alternative.</figcaption>
    </figure>
  </section>
  <section aria-labelledby="non-map-network">
    <h2 id="non-map-network">Non-map network schedule</h2>
    <table><caption>Network features represented without relying on the map</caption>
    <thead><tr><th scope="col">Feature ID</th><th scope="col">Status</th>
    <th scope="col">Metrics</th></tr></thead><tbody>{network_rows}</tbody></table>
  </section>
  <section aria-labelledby="programme-table">
    <h2 id="programme-table">Programme schedule table</h2>
    <table><caption>Intervention programme</caption><thead><tr>
    <th scope="col">Intervention</th><th scope="col">Phase</th>
    <th scope="col">Status</th><th scope="col">Outline cost range</th>
    </tr></thead><tbody>{programme_rows}</tbody></table>
  </section>
  <section aria-labelledby="references"><h2 id="references">References</h2>
    <ol>{citations}</ol></section>
</main>
<footer><p>{escape(status)} - {escape(config.release_id)} -
release fingerprint {watermark.release_fingerprint}</p></footer>
</body>
</html>
"""
    path.write_text(html)


def _network_svg(config: PublicationConfig) -> str:
    paths = _network_map_paths(config, width=800, height=440, padding=44)
    polylines = "".join(
        (
            f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" '
            'fill="none" stroke="#005a8d" stroke-width="8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            f"<title>{escape(feature_id)}</title></polyline>"
        )
        for feature_id, points in paths
    )
    return (
        '<svg viewBox="0 0 800 440" role="img" '
        'aria-labelledby="network-svg-title network-svg-description">'
        '<title id="network-svg-title">Indicative LCWIP network plan</title>'
        '<desc id="network-svg-description">Network geometry from the governed '
        "GeoJSON source. Feature details follow in an accessible table.</desc>"
        '<rect width="800" height="440" fill="#f7fbfd"/>'
        f"{polylines}</svg>"
    )


def _network_map_paths(
    config: PublicationConfig,
    *,
    width: float,
    height: float,
    padding: float,
    invert_y: bool = True,
) -> tuple[tuple[str, tuple[tuple[float, float], ...]], ...]:
    source = _source_of_kind(config.sources, ArtifactKind.NETWORK_GEOJSON)
    if not isinstance(source, SourceArtifact):
        raise TypeError("network map requires a source artifact")
    payload = json.loads(source.path.read_text())
    raw_paths: list[tuple[str, tuple[tuple[float, float], ...]]] = []
    for feature in payload["features"]:
        geometry = shape(feature["geometry"])
        for index, coordinates in enumerate(_line_coordinates(geometry)):
            path_id = str(feature["id"])
            if index:
                path_id = f"{path_id}-{index + 1}"
            raw_paths.append((path_id, coordinates))
    if not raw_paths:
        raise ValueError("network map has no drawable geometry")
    xs = [point[0] for _, points in raw_paths for point in points]
    ys = [point[1] for _, points in raw_paths for point in points]
    x_span = max(max(xs) - min(xs), 1e-12)
    y_span = max(max(ys) - min(ys), 1e-12)
    scale = min((width - 2 * padding) / x_span, (height - 2 * padding) / y_span)
    drawn_width = x_span * scale
    drawn_height = y_span * scale
    x_offset = (width - drawn_width) / 2
    y_offset = (height - drawn_height) / 2
    return tuple(
        (
            feature_id,
            tuple(
                (
                    x_offset + (x - min(xs)) * scale,
                    (
                        height - (y_offset + (y - min(ys)) * scale)
                        if invert_y
                        else y_offset + (y - min(ys)) * scale
                    ),
                )
                for x, y in points
            ),
        )
        for feature_id, points in raw_paths
    )


def _line_coordinates(geometry: Any) -> tuple[tuple[tuple[float, float], ...], ...]:
    if geometry.geom_type in {"LineString", "LinearRing"}:
        return (tuple((float(x), float(y)) for x, y, *_ in geometry.coords),)
    if geometry.geom_type == "Polygon":
        return (
            tuple((float(x), float(y)) for x, y, *_ in geometry.exterior.coords),
        )
    if hasattr(geometry, "geoms"):
        return tuple(
            coordinates
            for part in geometry.geoms
            for coordinates in _line_coordinates(part)
        )
    if geometry.geom_type == "Point":
        x, y = geometry.coords[0][:2]
        return (((float(x), float(y)), (float(x) + 1e-12, float(y))),)
    return ()


def _network_pdf_drawing(config: PublicationConfig) -> Drawing:
    width = 170 * mm
    height = 70 * mm
    drawing = Drawing(width, height)
    drawing.add(
        Rect(
            0,
            0,
            width,
            height,
            fillColor=colors.HexColor("#F7FBFD"),
            strokeColor=colors.HexColor("#50606E"),
            strokeWidth=0.5,
        )
    )
    for feature_id, points in _network_map_paths(
        config,
        width=width,
        height=height,
        padding=9 * mm,
        invert_y=False,
    ):
        flattened = [coordinate for point in points for coordinate in point]
        drawing.add(
            PolyLine(
                flattened,
                strokeColor=colors.HexColor("#005A8D"),
                strokeWidth=3,
            )
        )
        drawing.add(
            String(
                points[0][0] + 2,
                points[0][1] + 4,
                feature_id,
                fontName="Helvetica",
                fontSize=7,
                fillColor=colors.HexColor("#17212B"),
            )
        )
    return drawing


def _write_pdf(
    path: Path,
    config: PublicationConfig,
    watermark: ArtifactWatermark,
    conformance: ConformanceResult,
    blockers: tuple[PublicationBlocker, ...],
) -> None:
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=29,
            textColor=colors.HexColor("#123B52"),
            alignment=TA_CENTER,
            spaceAfter=8 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Status",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=16,
            textColor=colors.HexColor("#7A1515"),
            backColor=colors.HexColor("#FFF0F0"),
            borderColor=colors.HexColor("#A61B1B"),
            borderWidth=1,
            borderPadding=8,
            alignment=TA_CENTER,
            spaceAfter=7 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Claim",
            parent=styles["BodyText"],
            fontSize=9.5,
            leading=14,
            leftIndent=6 * mm,
            borderColor=colors.HexColor("#005A8D"),
            borderWidth=0,
            borderPadding=4,
            spaceAfter=3 * mm,
        )
    )
    document = BaseDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=25 * mm,
        bottomMargin=22 * mm,
        title=f"{config.plan_area} LCWIP {config.release_id}",
        author=config.plan_area,
        subject=(
            f"{_status_banner(config.lifecycle_state)}; release fingerprint "
            f"{watermark.release_fingerprint}"
        ),
    )
    citation_numbers = {
        item.citation_id: index
        for index, item in enumerate(config.citations, start=1)
    }
    story: list[Any] = [
        Spacer(1, 15 * mm),
        Paragraph(
            _pdf_escape(
                f"{config.plan_area} Local Cycling and Walking Infrastructure Plan"
            ),
            styles["ReportTitle"],
        ),
        Paragraph(_pdf_escape(_status_banner(config.lifecycle_state)), styles["Status"]),
        Paragraph(
            _pdf_escape(
                f"Release {config.release_id} | Version {config.release_version} | "
                f"{config.publication_date.isoformat()}"
            ),
            styles["BodyText"],
        ),
        Spacer(1, 5 * mm),
        Paragraph(
            _pdf_escape(
                "Evidence fingerprint: "
                f"{config.evidence_fingerprint}\nConfiguration fingerprint: "
                f"{config.configuration_fingerprint}\nRelease fingerprint: "
                f"{watermark.release_fingerprint}"
            ).replace("\n", "<br/>"),
            styles["Code"],
        ),
        PageBreak(),
        Paragraph("MANDATORY CONFORMANCE GAPS", styles["Heading1"]),
    ]
    if blockers:
        gap_data = [["ID", "Category", "Status / limitation"]]
        gap_data.extend(
            [[item.blocker_id, item.category, item.message] for item in blockers]
        )
        story.append(_pdf_table(gap_data, (32 * mm, 38 * mm, 100 * mm)))
    else:
        story.append(Paragraph("No mandatory blockers recorded.", styles["BodyText"]))
    story.append(Spacer(1, 5 * mm))
    for section in config.sections:
        section_start = [
            Paragraph(_pdf_escape(section.title), styles["Heading1"]),
            Paragraph(
                _pdf_escape(section.introduction)
                + " "
                + _pdf_citations(
                    section.introduction_citation_ids,
                    citation_numbers,
                ),
                styles["BodyText"],
            ),
            Paragraph(
                _pdf_escape(section.claims[0].text)
                + " "
                + _pdf_citations(
                    section.claims[0].citation_ids,
                    citation_numbers,
                ),
                styles["Claim"],
            ),
        ]
        story.append(KeepTogether(section_start))
        for claim in section.claims[1:]:
            story.append(
                Paragraph(
                    _pdf_escape(claim.text)
                    + " "
                    + _pdf_citations(claim.citation_ids, citation_numbers),
                    styles["Claim"],
                )
            )
        if section.kind is ReportSectionKind.NETWORK_PLANS:
            story.extend(
                [
                    Paragraph(
                        "Indicative network plan map",
                        styles["Heading2"],
                    ),
                    _network_pdf_drawing(config),
                    Paragraph(
                        "Network geometry from the governed GeoJSON source. "
                        "The following table is the complete non-map alternative.",
                        styles["BodyText"],
                    ),
                    Spacer(1, 2 * mm),
                ]
            )
            story.append(
                _pdf_table(
                    [
                        ["Feature ID", "Status", "Metrics"],
                        *[
                            [
                                item.feature_id,
                                item.status,
                                json.dumps(item.metrics, sort_keys=True),
                            ]
                            for item in config.network_features
                        ],
                    ],
                    (47 * mm, 32 * mm, 91 * mm),
                )
            )
        if section.kind is ReportSectionKind.INTERVENTION_PROGRAMME:
            story.append(
                _pdf_table(
                    [
                        ["Intervention", "Phase", "Status", "Cost range (GBP)"],
                        *[
                            [
                                item.intervention_id,
                                item.phase,
                                item.status,
                                f"{item.lower_cost:,.0f} - {item.upper_cost:,.0f}",
                            ]
                            for item in config.programme
                        ],
                    ],
                    (48 * mm, 25 * mm, 36 * mm, 61 * mm),
                )
            )
        if section.kind is ReportSectionKind.ENGAGEMENT:
            story.append(
                Paragraph(
                    "<b>Unresolved representations:</b> "
                    + _pdf_escape(
                        ", ".join(config.unresolved_representation_record_ids)
                        or "None"
                    ),
                    styles["BodyText"],
                )
            )
        if section.kind is ReportSectionKind.EQUALITY:
            story.append(
                Paragraph(
                    "<b>Unresolved equality findings:</b> "
                    + _pdf_escape(
                        ", ".join(config.unresolved_equality_finding_record_ids)
                        or "None"
                    ),
                    styles["BodyText"],
                )
            )
        story.append(Spacer(1, 5 * mm))
    story.extend([PageBreak(), Paragraph("References", styles["Heading1"])])
    for index, citation in enumerate(config.citations, start=1):
        story.append(
            Paragraph(
                _pdf_escape(
                    f"[{index}] {citation.label} | {citation.uri} | "
                    f"SHA-256 {citation.sha256}"
                ),
                styles["BodyText"],
            )
        )

    def page_watermark(canvas: Any, page_number: int) -> None:
        canvas.saveState()
        canvas.resetTransforms()
        canvas.setTitle(f"{config.plan_area} LCWIP {config.release_id}")
        canvas.setSubject(
            f"{_status_banner(config.lifecycle_state)} "
            f"{watermark.release_fingerprint}"
        )
        width, height = A4
        canvas.setFillColor(colors.HexColor("#123B52"))
        canvas.rect(0, height - 17 * mm, width, 14 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(
            18 * mm,
            height - 12 * mm,
            _pdf_plain(
                f"{config.plan_area} | {_status_banner(config.lifecycle_state)}"
            ),
        )
        canvas.setFillColor(colors.HexColor("#273746"))
        canvas.setFont("Helvetica", 7)
        canvas.drawString(
            18 * mm,
            10 * mm,
            _pdf_plain(
                f"{config.release_id} | {config.publication_date.isoformat()} | "
                f"{watermark.release_fingerprint[:20]}"
            ),
        )
        canvas.drawRightString(
            width - 18 * mm,
            10 * mm,
            f"Page {page_number}",
        )
        canvas.restoreState()

    frame = Frame(
        document.leftMargin,
        document.bottomMargin,
        document.width,
        document.height,
        id="lcwip-report-frame",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    def on_page(canvas: Any, active_document: Any) -> None:
        page_watermark(canvas, active_document.page)

    document.addPageTemplates(
        [
            PageTemplate(
                id="lcwip-report-pages",
                frames=(frame,),
                onPage=on_page,
            )
        ]
    )
    document.build(story)


def _pdf_table(data: list[list[Any]], widths: tuple[float, ...]) -> Table:
    wrapped = [
        [
            Paragraph(_pdf_escape(str(cell)), getSampleStyleSheet()["BodyText"])
            for cell in row
        ]
        for row in data
    ]
    table = Table(wrapped, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDECF2")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#123B52")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#50606E")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _write_watermarked_geojson(
    source: Path,
    destination: Path,
    watermark: ArtifactWatermark,
) -> None:
    payload = json.loads(source.read_text())
    payload["lcwip_watermark"] = _json(watermark)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_watermarked_geopackage(
    source: Path,
    destination: Path,
    watermark: ArtifactWatermark,
) -> None:
    shutil.copy2(source, destination)
    values = {
        "plan_area": watermark.plan_area,
        "release_id": watermark.release_id,
        "release_version": watermark.release_version,
        "lifecycle_state": watermark.lifecycle_state.value,
        "evidence_fingerprint": watermark.evidence_fingerprint,
        "configuration_fingerprint": watermark.configuration_fingerprint,
        "release_fingerprint": watermark.release_fingerprint,
        "publication_date": watermark.publication_date.isoformat(),
    }
    with sqlite3.connect(destination) as database:
        database.execute(
            "create table lcwip_publication_metadata "
            "(metadata_key text primary key, metadata_value text not null)"
        )
        database.executemany(
            "insert into lcwip_publication_metadata values (?, ?)",
            tuple(sorted(values.items())),
        )
        database.commit()


def _write_zip(
    path: Path,
    source_dir: Path,
    watermark: ArtifactWatermark,
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in ARTIFACTS:
            if filename == "lcwip-release.zip":
                continue
            archive.write(source_dir / filename, filename)
        archive.comment = json.dumps(_json(watermark), sort_keys=True).encode()


def _validate_json_artifacts(path: Path, manifest: PublicationManifest) -> None:
    expected = {
        "report-data.json": _report_payload_from_manifest(manifest),
        "conformance-manifest.json": {
            "watermark": manifest.watermark,
            "profile": manifest.conformance.profile,
            "profile_fingerprint": manifest.conformance.profile_fingerprint,
            "requirements": manifest.conformance.requirements,
            "unresolved_mandatory_requirement_ids": (
                manifest.unresolved_mandatory_requirement_ids
            ),
            "blockers": manifest.blockers,
            "conformance_fingerprint": manifest.conformance.conformance_fingerprint,
        },
        "source-coverage-quality.json": {
            "watermark": manifest.watermark,
            "sources": manifest.sources,
            "source_records": manifest.source_records,
        },
        "decision-agent-audit.json": {
            "watermark": manifest.watermark,
            "entries": manifest.audit_entries,
            "consultation_changes": manifest.consultation_changes,
            "adoption_annotation": manifest.adoption_annotation,
        },
        "programme-schedule.json": {
            "watermark": manifest.watermark,
            "programme": manifest.programme,
        },
        "release-diff.json": {
            "watermark": manifest.watermark,
            **manifest.release_diff.model_dump(mode="json"),
        },
        "release-history.json": manifest.release_history,
    }
    for filename, contents in expected.items():
        if json.loads((path / filename).read_text()) != _json(contents):
            raise ValueError(f"invalid LCWIP publication: {filename} mismatch")


def _report_payload_from_manifest(manifest: PublicationManifest) -> dict[str, Any]:
    return {
        "watermark": manifest.watermark,
        "sections": manifest.sections,
        "citations": manifest.citations,
        "network_features": manifest.network_features,
        "programme": manifest.programme,
        "consultation_changes": manifest.consultation_changes,
        "unresolved_representation_record_ids": (
            manifest.unresolved_representation_record_ids
        ),
        "unresolved_equality_finding_record_ids": (
            manifest.unresolved_equality_finding_record_ids
        ),
    }


def _validate_source_geojson(
    path: Path,
    expected_features: tuple[NetworkFeatureRecord, ...],
) -> None:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("network GeoJSON is unreadable") from error
    if payload.get("type") != "FeatureCollection" or not isinstance(
        payload.get("features"), list
    ):
        raise ValueError("network GeoJSON must be a FeatureCollection")
    actual = {}
    for feature in payload["features"]:
        if (
            not isinstance(feature, dict)
            or feature.get("type") != "Feature"
            or not isinstance(feature.get("id"), str)
            or not isinstance(feature.get("properties"), dict)
            or not isinstance(feature.get("geometry"), dict)
        ):
            raise ValueError("network GeoJSON feature schema is invalid")
        feature_id = feature["id"]
        if feature_id in actual:
            raise ValueError("network GeoJSON feature IDs must be unique")
        try:
            geometry = shape(feature["geometry"])
        except (TypeError, ValueError) as error:
            raise ValueError("network GeoJSON geometry is invalid") from error
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError("network GeoJSON geometry is empty or invalid")
        actual[feature_id] = (feature, geometry)
    expected = {item.feature_id: item for item in expected_features}
    if set(actual) != set(expected):
        raise ValueError("network GeoJSON feature IDs differ from report records")
    for feature_id, record in expected.items():
        feature, geometry = actual[feature_id]
        properties = feature["properties"]
        if properties.get("status") != record.status:
            raise ValueError(f"network feature {feature_id} status differs")
        if properties.get("metrics") != record.metrics:
            raise ValueError(f"network feature {feature_id} metrics differ")
        if _geometry_fingerprint(geometry) != record.geometry_sha256:
            raise ValueError(f"network feature {feature_id} geometry differs")
        if tuple(float(value) for value in geometry.bounds) != record.bbox:
            raise ValueError(f"network feature {feature_id} bounding box differs")


def _validate_source_geopackage(
    path: Path,
    expected_features: tuple[NetworkFeatureRecord, ...],
) -> None:
    try:
        with sqlite3.connect(path) as database:
            layers = database.execute(
                "select table_name from gpkg_contents where data_type = 'features'"
            ).fetchall()
    except sqlite3.DatabaseError as error:
        raise ValueError("network GeoPackage schema is invalid") from error
    if not layers:
        raise ValueError("network GeoPackage has no feature layer")
    frame = gpd.read_file(path, layer=layers[0][0])
    if not {"feature_id", "status", "length_m", "geometry"}.issubset(frame.columns):
        raise ValueError("network GeoPackage feature schema is incomplete")
    expected = {item.feature_id: item for item in expected_features}
    if set(frame["feature_id"].astype(str)) != set(expected):
        raise ValueError("network GeoPackage feature IDs differ")
    if frame.crs is None:
        raise ValueError("network GeoPackage feature layer requires a CRS")
    frame = frame.to_crs("EPSG:4326")
    for row in frame.itertuples():
        record = expected[str(row.feature_id)]
        if row.geometry is None or row.geometry.is_empty or not row.geometry.is_valid:
            raise ValueError("network GeoPackage geometry is empty or invalid")
        if _geometry_fingerprint(row.geometry) != record.geometry_sha256:
            raise ValueError("network GeoPackage geometry differs")
        if tuple(float(value) for value in row.geometry.bounds) != record.bbox:
            raise ValueError("network GeoPackage bounding box differs")
        if str(row.status) != record.status:
            raise ValueError("network GeoPackage status differs")
        if "length_m" in record.metrics and float(row.length_m) != float(
            record.metrics["length_m"]
        ):
            raise ValueError("network GeoPackage metrics differ")


def _validate_geojson(path: Path, manifest: PublicationManifest) -> None:
    payload = json.loads(path.read_text())
    if payload.get("lcwip_watermark") != _json(manifest.watermark):
        raise ValueError("invalid LCWIP publication: GeoJSON watermark mismatch")
    _validate_source_geojson(path, manifest.network_features)


def _validate_geopackage(path: Path, manifest: PublicationManifest) -> None:
    _validate_source_geopackage(path, manifest.network_features)
    with sqlite3.connect(path) as database:
        metadata = dict(
            database.execute(
                "select metadata_key, metadata_value "
                "from lcwip_publication_metadata"
            ).fetchall()
        )
    expected = {
        "plan_area": manifest.watermark.plan_area,
        "release_id": manifest.watermark.release_id,
        "release_version": manifest.watermark.release_version,
        "lifecycle_state": manifest.watermark.lifecycle_state.value,
        "evidence_fingerprint": manifest.watermark.evidence_fingerprint,
        "configuration_fingerprint": manifest.watermark.configuration_fingerprint,
        "release_fingerprint": manifest.watermark.release_fingerprint,
        "publication_date": manifest.watermark.publication_date.isoformat(),
    }
    if metadata != expected:
        raise ValueError("invalid LCWIP publication: GeoPackage watermark mismatch")


def _validate_html(path: Path, manifest: PublicationManifest) -> None:
    html = path.read_text()
    required = (
        '<html lang="en">',
        'href="#main-content"',
        "<nav",
        "<table",
        '<svg viewBox="0 0 800 440" role="img"',
        'aria-labelledby="network-svg-title network-svg-description"',
        "Indicative network plan map",
        "Non-map network schedule",
        manifest.release_id,
        manifest.release_fingerprint,
        _status_banner(manifest.lifecycle_state),
    )
    if any(value not in html for value in required):
        raise ValueError("invalid LCWIP publication: accessible HTML contract mismatch")
    for requirement_id in manifest.unresolved_mandatory_requirement_ids:
        if requirement_id not in html:
            raise ValueError("invalid LCWIP publication: mandatory gap missing from HTML")


def _validate_pdf(path: Path, manifest: PublicationManifest) -> None:
    try:
        reader = PdfReader(path)
    except Exception as error:
        raise ValueError("invalid LCWIP publication: PDF is unreadable") from error
    if not reader.pages:
        raise ValueError("invalid LCWIP publication: PDF has no pages")
    texts = tuple(page.extract_text() or "" for page in reader.pages)
    page_watermark = (
        manifest.release_id,
        _status_banner(manifest.lifecycle_state),
    )
    if any(any(value not in text for value in page_watermark) for text in texts):
        raise ValueError("invalid LCWIP publication: PDF page watermark missing")
    complete = "\n".join(texts)
    required = {
        "MANDATORY CONFORMANCE GAPS",
        "Indicative network plan map",
        "References",
        *(
            section.kind.value.replace("-", " ").title()
            for section in manifest.sections
        ),
        *manifest.unresolved_mandatory_requirement_ids,
    }
    if any(value not in complete for value in required):
        raise ValueError("invalid LCWIP publication: PDF report content is incomplete")
    subject = str((reader.metadata or {}).get("/Subject", ""))
    if manifest.release_fingerprint not in subject:
        raise ValueError("invalid LCWIP publication: PDF metadata watermark mismatch")


def _validate_zip(path: Path, manifest: PublicationManifest) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            expected = set(ARTIFACTS) - {"lcwip-release.zip"}
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or set(names) != expected:
                raise ValueError("ZIP artifact set differs")
            if json.loads(archive.comment) != _json(manifest.watermark):
                raise ValueError("ZIP watermark differs")
            total_size = 0
            for info in infos:
                pure = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                if (
                    info.is_dir()
                    or pure.is_absolute()
                    or ".." in pure.parts
                    or not pure.parts
                    or (mode and stat.S_IFMT(mode) not in {0, stat.S_IFREG})
                    or info.file_size > LCWIP_ZIP_MAX_MEMBER_BYTES
                ):
                    raise ValueError("ZIP contains an unsafe member")
                total_size += info.file_size
                if total_size > LCWIP_ZIP_MAX_TOTAL_BYTES:
                    raise ValueError("ZIP exceeds uncompressed size budget")
                if info.file_size and (
                    info.compress_size == 0
                    or info.file_size / info.compress_size > LCWIP_ZIP_MAX_COMPRESSION_RATIO
                ):
                    raise ValueError("ZIP exceeds compression ratio budget")
            for info in infos:
                plain_path = path.parent / info.filename
                with archive.open(info) as zipped, plain_path.open("rb") as plain:
                    while True:
                        zipped_chunk = zipped.read(64 * 1024)
                        plain_chunk = plain.read(64 * 1024)
                        if zipped_chunk != plain_chunk:
                            raise ValueError("ZIP member differs from publication artifact")
                        if not zipped_chunk:
                            break
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as error:
        raise ValueError("invalid LCWIP publication: ZIP is invalid") from error


def _source_descriptors(
    sources: tuple[SourceArtifact, ...],
) -> tuple[SourceDescriptor, ...]:
    return tuple(
        SourceDescriptor(
            artifact_id=item.artifact_id,
            kind=item.kind,
            sha256=item.sha256,
            coverage_status=item.coverage_status,
            quality_status=item.quality_status,
            limitations=item.limitations,
            public=item.public,
            contains_personal_data=False,
        )
        for item in sorted(sources, key=lambda source: source.artifact_id)
    )


def _blockers(
    conformance: ConformanceResult,
    representations: tuple[str, ...],
    equality_findings: tuple[str, ...],
) -> tuple[PublicationBlocker, ...]:
    blockers = [
        PublicationBlocker(
            blocker_id=f"conformance-{requirement_id}",
            category="mandatory-conformance",
            message=f"Mandatory requirement {requirement_id} is unresolved.",
            source_record_id=None,
        )
        for requirement_id in conformance.unresolved_mandatory_requirement_ids
    ]
    blockers.extend(
        PublicationBlocker(
            blocker_id=f"representation-{record_id}",
            category="representation",
            message=f"Representation record {record_id} remains unresolved.",
            source_record_id=record_id,
        )
        for record_id in representations
    )
    blockers.extend(
        PublicationBlocker(
            blocker_id=f"equality-{record_id}",
            category="equality",
            message=f"Equality finding record {record_id} remains unresolved.",
            source_record_id=record_id,
        )
        for record_id in equality_findings
    )
    return tuple(blockers)


def _previous_manifest(config: PublicationConfig) -> PublicationManifest | None:
    if config.previous_release_dir is None:
        return None
    previous = Path(config.previous_release_dir)
    if previous == config.output_dir / config.release_id:
        raise ValueError("previous release must differ from current release")
    return validate_lcwip_publication(previous)


def _release_diff(
    config: PublicationConfig,
    previous: PublicationManifest | None,
) -> ReleaseDiff:
    current_categories = _category_values(config)
    if previous is None:
        return ReleaseDiff(
            previous_release_id=None,
            current_release_id=config.release_id,
            changed_categories=(),
            spatial_changes=(),
            category_fingerprints={
                category: {
                    "previous": None,
                    "current": _fingerprint(value),
                }
                for category, value in current_categories.items()
            },
        )
    previous_categories = _category_values(previous)
    changed = tuple(
        category
        for category in (
            "evidence",
            "method",
            "geometry",
            "programme",
            "narrative",
            "decision",
        )
        if _fingerprint(previous_categories[category])
        != _fingerprint(current_categories[category])
    )
    previous_features = {
        item.feature_id: item for item in previous.network_features
    }
    current_features = {item.feature_id: item for item in config.network_features}
    spatial = []
    for feature_id in sorted(set(previous_features) | set(current_features)):
        before = previous_features.get(feature_id)
        after = current_features.get(feature_id)
        if before is None:
            change = "added"
        elif after is None:
            change = "removed"
        elif before.geometry_sha256 != after.geometry_sha256:
            change = "geometry-changed"
        elif before.status != after.status or before.metrics != after.metrics:
            change = "attributes-changed"
        else:
            continue
        spatial.append(
            SpatialChange(
                feature_id=feature_id,
                change=change,
                previous_geometry_sha256=(
                    before.geometry_sha256 if before is not None else None
                ),
                current_geometry_sha256=(
                    after.geometry_sha256 if after is not None else None
                ),
                previous_bbox=before.bbox if before is not None else None,
                current_bbox=after.bbox if after is not None else None,
            )
        )
    return ReleaseDiff(
        previous_release_id=previous.release_id,
        current_release_id=config.release_id,
        changed_categories=changed,
        spatial_changes=tuple(spatial),
        category_fingerprints={
            category: {
                "previous": _fingerprint(previous_categories[category]),
                "current": _fingerprint(current_categories[category]),
            }
            for category in current_categories
        },
    )


def _category_values(record: Any) -> dict[str, Any]:
    return {
        "evidence": {
            "evidence_fingerprint": record.evidence_fingerprint,
            "sources": (
                _source_descriptors(record.sources)
                if isinstance(record, PublicationConfig)
                else record.sources
            ),
            "source_records": record.source_records,
        },
        "method": {
            "configuration_fingerprint": record.configuration_fingerprint,
            "profile": (
                record.guidance_profile
                if isinstance(record, PublicationConfig)
                else record.conformance.profile
            ),
        },
        "geometry": record.network_features,
        "programme": record.programme,
        "narrative": {
            "sections": record.sections,
            "citations": record.citations,
        },
        "decision": {
            "consultation_changes": record.consultation_changes,
            "unresolved_representation_record_ids": (
                record.unresolved_representation_record_ids
            ),
            "unresolved_equality_finding_record_ids": (
                record.unresolved_equality_finding_record_ids
            ),
            "audit_entries": record.audit_entries,
            "adoption_annotation": record.adoption_annotation,
        },
    }


def _release_history(
    config: PublicationConfig,
    watermark: ArtifactWatermark,
    release_fingerprint: str,
    publication_fingerprint: str,
) -> ReleaseHistory:
    entries = []
    if config.output_dir.exists():
        for child in sorted(config.output_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            manifest_path = child / "publication-manifest.json"
            if manifest_path.is_file():
                manifest = validate_lcwip_publication(child)
                entries.append(
                    ReleaseHistoryEntry(
                        release_id=manifest.release_id,
                        release_version=manifest.release_version,
                        publication_date=manifest.publication_date,
                        lifecycle_state=manifest.lifecycle_state,
                        release_fingerprint=manifest.release_fingerprint,
                        publication_fingerprint=manifest.publication_fingerprint,
                    )
                )
    entries.append(
        ReleaseHistoryEntry(
            release_id=config.release_id,
            release_version=config.release_version,
            publication_date=config.publication_date,
            lifecycle_state=config.lifecycle_state,
            release_fingerprint=release_fingerprint,
            publication_fingerprint=publication_fingerprint,
        )
    )
    unique = {entry.release_id: entry for entry in entries}
    return ReleaseHistory(
        watermark=watermark,
        releases=tuple(
            sorted(
                unique.values(),
                key=lambda item: (item.publication_date, item.release_id),
            )
        ),
    )


def _release_payload(record: Any) -> dict[str, Any]:
    if isinstance(record, PublicationConfig):
        conformance = evaluate_conformance(
            record.guidance_profile,
            record.requirement_assessments,
        )
        sources = _source_descriptors(record.sources)
    else:
        conformance = record.conformance
        sources = record.sources
    return {
        "plan_area": record.plan_area,
        "evidence_fingerprint": record.evidence_fingerprint,
        "configuration_fingerprint": record.configuration_fingerprint,
        "guidance_profile": conformance.profile,
        "requirement_assessments": conformance.requirements,
        "sources": sources,
        "source_records": record.source_records,
        "citations": record.citations,
        "sections": _release_sections(record.sections),
        "network_features": record.network_features,
        "programme": record.programme,
        "consultation_changes": record.consultation_changes,
        "unresolved_representation_record_ids": (
            record.unresolved_representation_record_ids
        ),
        "unresolved_equality_finding_record_ids": (
            record.unresolved_equality_finding_record_ids
        ),
        "audit_entries": record.audit_entries,
    }


def _release_sections(
    sections: tuple[ReportSection, ...],
) -> tuple[dict[str, Any], ...]:
    """Exclude the self-referential binding field from the release hash."""
    payloads = []
    for section in sections:
        payload = section.model_dump(mode="json")
        for claim in payload["claims"]:
            if claim["authority"] is not None:
                claim["authority"]["release_fingerprint"] = None
        payloads.append(payload)
    return tuple(payloads)


def _release_fingerprint(record: Any) -> str:
    return _fingerprint(_release_payload(record))


def _publication_fingerprint(
    record: Any,
    *,
    release_fingerprint: str | None = None,
) -> str:
    return _fingerprint(
        {
            "release_id": record.release_id,
            "release_version": record.release_version,
            "publication_date": record.publication_date,
            "lifecycle_state": record.lifecycle_state,
            "release_fingerprint": release_fingerprint
            or record.release_fingerprint,
            "adoption_annotation": record.adoption_annotation,
        }
    )


def _watermark(
    record: Any,
    *,
    release_fingerprint: str | None = None,
) -> ArtifactWatermark:
    return ArtifactWatermark(
        plan_area=record.plan_area,
        release_id=record.release_id,
        release_version=record.release_version,
        lifecycle_state=record.lifecycle_state,
        evidence_fingerprint=record.evidence_fingerprint,
        configuration_fingerprint=record.configuration_fingerprint,
        release_fingerprint=release_fingerprint or record.release_fingerprint,
        publication_date=record.publication_date,
    )


def _status_banner(state: LifecycleState) -> str:
    if state is LifecycleState.ADOPTED:
        return "ADOPTED - EXTERNAL DECISION RECORDED"
    if state is LifecycleState.ADOPTION_CANDIDATE:
        return "ADOPTION CANDIDATE - NOT ADOPTED"
    return state.value.replace("_", " ").upper()


def _html_citation_links(
    citation_ids: tuple[str, ...],
    numbers: dict[str, int],
) -> str:
    return " ".join(
        f'<a href="#citation-{numbers[item]}" aria-label="Citation {numbers[item]}">'
        f"[{numbers[item]}]</a>"
        for item in citation_ids
    )


def _pdf_citations(
    citation_ids: tuple[str, ...],
    numbers: dict[str, int],
) -> str:
    return " ".join(f"[{numbers[item]}]" for item in citation_ids)


def _pdf_plain(value: str) -> str:
    return (
        value.replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )


def _pdf_escape(value: str) -> str:
    return escape(_pdf_plain(value))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_json(value), indent=2, sort_keys=True) + "\n")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unique_ids(records: tuple[Any, ...], field: str, label: str) -> set[str]:
    values = tuple(getattr(item, field) for item in records)
    if len(values) != len(set(values)):
        raise ValueError(f"{label} IDs must be unique")
    return set(values)


def _json(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, StrEnum):
        return value.value
    return value


def _validate_public_uri(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("public publication URI must use https")
    return value


def _geometry_fingerprint(geometry: Any) -> str:
    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        if isinstance(value, (int, float)):
            return float(value)
        return value

    return _fingerprint(normalize(mapping(geometry)))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(_json(value), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
