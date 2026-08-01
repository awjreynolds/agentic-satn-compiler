"""Evidence-bounded strategic physical-site access obligations.

This seam deliberately establishes only whether a physical strategic site has
an evidenced connection to *any* strategic network edge.  It does not claim a
surveyed entrance, detailed route, legal access, or scheme feasibility.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from satn.identifiers import stable_id
from satn.models import AccessPointStatus
from satn.runtime_governance_contract import canonical_sha256

_IDENTIFIER = re.compile(r"^\S+$")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("identifiers must be non-blank and contain no whitespace")
    return value


def _canonical_identifiers(values: tuple[str, ...]) -> tuple[str, ...]:
    canonical = tuple(sorted(set(values)))
    if len(canonical) != len(values):
        raise ValueError("identifier values must not contain duplicates")
    for value in canonical:
        _identifier(value)
    return canonical


class DestinationSiteKind(StrEnum):
    FURTHER_EDUCATION = "further-education"
    HIGHER_EDUCATION = "higher-education"
    ACUTE_HOSPITAL = "acute-hospital"
    GENERAL_HOSPITAL = "general-hospital"
    MAJOR_COMMUNITY_HOSPITAL = "major-community-hospital"


class EvidenceSource(StrEnum):
    OFFICIAL = "official-register"
    OSM = "openstreetmap-provisional"


class AccessEvidenceKind(StrEnum):
    ROUTED_WALKING_PATH = "routed-walking-path"
    PROVIDER_WALKING_PATH = "provider-walking-path"
    OSM_FOOTWAY = "osm-footway"
    OSM_SHARED_PATH = "osm-shared-path"
    OSM_PUBLIC_RIGHT_OF_WAY = "osm-public-right-of-way"


class AccessServiceStatus(StrEnum):
    SERVED = "served"
    SERVED_PROVISIONAL = "served-provisional"
    NETWORK_GAP = "network-gap"


class DestinationSite(_FrozenModel):
    """One source record for a physical site, never a provider-level obligation."""

    physical_site_id: str
    name: str = Field(min_length=1)
    kind: DestinationSiteKind
    source: EvidenceSource
    source_record_id: str
    provider_id: str | None = None

    _identifiers = field_validator("physical_site_id", "source_record_id")(_identifier)
    _provider = field_validator("provider_id")(
        lambda value: _identifier(value) if value is not None else value
    )

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("name must be non-blank and trimmed")
        return value


class AccessEvidence(_FrozenModel):
    """A source-backed, strategic-level observed site-to-edge connection."""

    physical_site_id: str
    entrance_id: str
    strategic_network_edge_id: str
    kind: AccessEvidenceKind
    evidence_id: str
    source: EvidenceSource = EvidenceSource.OFFICIAL
    access_point_status: AccessPointStatus = AccessPointStatus.INFERRED

    _identifiers = field_validator(
        "physical_site_id", "entrance_id", "strategic_network_edge_id", "evidence_id"
    )(_identifier)


class StrategicDestinationAccessConfig(_FrozenModel):
    """Versioned policy input; defaults enact the one-entrance POC obligation."""

    required_evidenced_entrances: int = Field(default=1, ge=1)
    source_fallback_hierarchy: tuple[EvidenceSource, ...] = (
        EvidenceSource.OFFICIAL,
        EvidenceSource.OSM,
    )
    method_version: str = "strategic-physical-site-access/v1"

    _method = field_validator("method_version")(_identifier)

    @field_validator("source_fallback_hierarchy")
    @classmethod
    def _hierarchy(cls, values: tuple[EvidenceSource, ...]) -> tuple[EvidenceSource, ...]:
        if set(values) != set(EvidenceSource) or len(values) != len(EvidenceSource):
            raise ValueError("source fallback hierarchy must contain each source exactly once")
        return values

    @property
    def fingerprint(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class StrategicDestinationAccessRequest(_FrozenModel):
    sites: tuple[DestinationSite, ...]
    access_evidence: tuple[AccessEvidence, ...] = ()
    config: StrategicDestinationAccessConfig = Field(
        default_factory=StrategicDestinationAccessConfig
    )

    @field_validator("sites")
    @classmethod
    def _sites(cls, values: tuple[DestinationSite, ...]) -> tuple[DestinationSite, ...]:
        records = tuple(sorted(values, key=lambda item: (item.physical_site_id, item.source.value)))
        pairs = tuple((item.physical_site_id, item.source) for item in records)
        if len(pairs) != len(set(pairs)):
            raise ValueError("sites must not duplicate a physical-site/source record")
        return records

    @field_validator("access_evidence")
    @classmethod
    def _evidence(cls, values: tuple[AccessEvidence, ...]) -> tuple[AccessEvidence, ...]:
        records = tuple(
            sorted(
                values,
                key=lambda item: (
                    item.physical_site_id,
                    item.entrance_id,
                    item.strategic_network_edge_id,
                    item.evidence_id,
                ),
            )
        )
        ids = tuple(item.evidence_id for item in records)
        if len(ids) != len(set(ids)):
            raise ValueError("access evidence must not duplicate evidence IDs")
        return records

    @model_validator(mode="after")
    def _known_evidence_sites(self) -> Self:
        known_site_ids = {item.physical_site_id for item in self.sites}
        unknown_site_ids = sorted(
            {item.physical_site_id for item in self.access_evidence} - known_site_ids
        )
        if unknown_site_ids:
            raise ValueError("access evidence must refer to a known physical site")
        return self


class StrategicDestinationAccessObligation(_FrozenModel):
    obligation_id: str
    physical_site_id: str
    name: str
    kind: DestinationSiteKind
    source: EvidenceSource
    source_record_id: str
    service_status: AccessServiceStatus
    access_point_status: AccessPointStatus
    entrance_ids: tuple[str, ...]
    strategic_network_edge_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    is_nonblocking: bool
    limitation: str = "strategic-access-evidence-not-detailed-route-or-survey"

    _identifiers = field_validator("obligation_id", "physical_site_id", "source_record_id")(
        _identifier
    )
    _many_identifiers = field_validator(
        "entrance_ids", "strategic_network_edge_ids", "evidence_ids"
    )(_canonical_identifiers)

    @model_validator(mode="after")
    def _semantics(self) -> Self:
        if self.obligation_id != stable_id("strategic-destination-access", self.physical_site_id):
            raise ValueError("obligation_id must be derived from physical_site_id")
        if self.service_status is AccessServiceStatus.NETWORK_GAP:
            if self.entrance_ids or self.strategic_network_edge_ids or self.evidence_ids:
                raise ValueError("access gaps cannot claim entrance evidence")
            if not self.is_nonblocking:
                raise ValueError("missing access evidence must remain nonblocking")
            if self.access_point_status is not AccessPointStatus.UNRESOLVED:
                raise ValueError("access gaps must retain an unresolved access point")
        elif not (self.entrance_ids and self.strategic_network_edge_ids and self.evidence_ids):
            raise ValueError("served sites require entrance, edge, and evidence")
        elif self.access_point_status is AccessPointStatus.UNRESOLVED:
            raise ValueError("served sites require an evidenced access point")
        return self


class StrategicDestinationAccessCompilation(_FrozenModel):
    config_fingerprint: str
    source_fingerprint: str
    obligations: tuple[StrategicDestinationAccessObligation, ...]


def compile_strategic_destination_access(
    request: StrategicDestinationAccessRequest,
) -> StrategicDestinationAccessCompilation:
    """Compile physical-site access without routing or excluding unknown entrances."""

    selected_sites = _select_source_records(request.sites, request.config)
    evidence_by_site: dict[str, list[AccessEvidence]] = {}
    for evidence in request.access_evidence:
        evidence_by_site.setdefault(evidence.physical_site_id, []).append(evidence)

    obligations = tuple(
        _compile_obligation(
            site,
            tuple(evidence_by_site.get(site.physical_site_id, ())),
            request.config,
        )
        for site in selected_sites
    )
    source_fingerprint = canonical_sha256(
        {
            "sites": [site.model_dump(mode="json") for site in request.sites],
            "access_evidence": [item.model_dump(mode="json") for item in request.access_evidence],
        }
    )
    return StrategicDestinationAccessCompilation(
        config_fingerprint=request.config.fingerprint,
        source_fingerprint=source_fingerprint,
        obligations=obligations,
    )


def _select_source_records(
    sites: tuple[DestinationSite, ...], config: StrategicDestinationAccessConfig
) -> tuple[DestinationSite, ...]:
    by_site: dict[str, list[DestinationSite]] = {}
    for site in sites:
        by_site.setdefault(site.physical_site_id, []).append(site)
    rank = {source: position for position, source in enumerate(config.source_fallback_hierarchy)}
    return tuple(
        sorted(
            (min(records, key=lambda item: rank[item.source]) for records in by_site.values()),
            key=lambda item: item.physical_site_id,
        )
    )


def _compile_obligation(
    site: DestinationSite,
    evidence: tuple[AccessEvidence, ...],
    config: StrategicDestinationAccessConfig,
) -> StrategicDestinationAccessObligation:
    entrances = tuple(sorted({item.entrance_id for item in evidence}))
    sufficient = len(entrances) >= config.required_evidenced_entrances
    all_entrances_mapped = sufficient and all(
        any(
            item.access_point_status is AccessPointStatus.MAPPED
            for item in evidence
            if item.entrance_id == entrance_id
        )
        for entrance_id in entrances
    )
    access_point_status = (
        AccessPointStatus.MAPPED
        if all_entrances_mapped
        else AccessPointStatus.INFERRED
        if sufficient
        else AccessPointStatus.UNRESOLVED
    )
    status = (
        AccessServiceStatus.SERVED
        if sufficient
        and site.source is EvidenceSource.OFFICIAL
        and access_point_status is AccessPointStatus.MAPPED
        else AccessServiceStatus.SERVED_PROVISIONAL
        if sufficient
        else AccessServiceStatus.NETWORK_GAP
    )
    return StrategicDestinationAccessObligation(
        obligation_id=stable_id("strategic-destination-access", site.physical_site_id),
        physical_site_id=site.physical_site_id,
        name=site.name,
        kind=site.kind,
        source=site.source,
        source_record_id=site.source_record_id,
        service_status=status,
        access_point_status=access_point_status,
        entrance_ids=entrances if sufficient else (),
        strategic_network_edge_ids=(
            tuple(sorted({item.strategic_network_edge_id for item in evidence}))
            if sufficient
            else ()
        ),
        evidence_ids=tuple(sorted({item.evidence_id for item in evidence})) if sufficient else (),
        is_nonblocking=status is AccessServiceStatus.NETWORK_GAP,
    )
