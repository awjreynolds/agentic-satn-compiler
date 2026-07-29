"""Typed, immutable Edge Enrichment records and DuckDB lifecycle.

This module is an internal part of :class:`satn.local_evidence_store.LocalEvidenceStore`.
It deliberately accepts already-governed, typed algorithm results: storage decides
hit/miss/collision and transactionality, while the four closed algorithms remain
independently testable.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from satn.evidence_contracts import (
    EdgeEnrichmentHeader,
    canonical_evidence_json,
    evidence_fingerprint,
)
from satn.evidence_materialisations import CanonicalLogicalEdge

EnrichmentKind = Literal[
    "official-classification-overlap",
    "elevation-profile",
    "population-capture",
    "education-reach-observation",
]
ValueStatus = Literal["available", "no-data", "unknown"]
CaptureDecision = Literal["captured", "borderline", "outside"]
AccessPointStatus = Literal["observed", "not-observed", "unknown"]

VALUE_SCHEMAS: Mapping[EnrichmentKind, str] = MappingProxyType(
    {
        "official-classification-overlap": "satn-edge-official-classification/v1",
        "elevation-profile": "satn-edge-elevation-profile/v1",
        "population-capture": "satn-edge-population-capture/v1",
        "education-reach-observation": "satn-edge-education-reach-observation/v1",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class EdgeEnrichmentCollisionError(RuntimeError):
    """An immutable enrichment key is bound to different identity or value bytes."""


@dataclass(frozen=True)
class OfficialClassificationOverlap:
    source_feature_logical_key: str
    publisher_raw_classification: str
    normalisation_contract_version: str
    normalised_official_class: str
    overlap_length_mm: int
    source_export_fingerprint: str
    source_feature_fingerprint: str

    def canonical_payload(self) -> dict[str, object]:
        return _record_payload(self)


@dataclass(frozen=True)
class OfficialClassificationValue:
    status: ValueStatus
    overlaps: tuple[OfficialClassificationOverlap, ...] = ()
    unknown_reason: str | None = None

    kind: EnrichmentKind = field(init=False, default="official-classification-overlap")

    def __post_init__(self) -> None:
        _validate_status(self.status, self.unknown_reason, self.overlaps)
        _validate_unique(
            self.overlaps,
            lambda item: (
                item.source_export_fingerprint,
                item.source_feature_logical_key,
                item.normalised_official_class,
            ),
            "official classification overlaps",
        )
        for item in self.overlaps:
            _required(item.source_feature_logical_key, "source feature logical key")
            _required(item.publisher_raw_classification, "publisher classification")
            _required(item.normalisation_contract_version, "normalisation contract")
            _required(item.normalised_official_class, "normalised official class")
            _nonnegative(item.overlap_length_mm, "overlap length")
            _sha(item.source_export_fingerprint, "source export fingerprint")
            _sha(item.source_feature_fingerprint, "source feature fingerprint")
        object.__setattr__(
            self,
            "overlaps",
            tuple(
                sorted(
                    self.overlaps,
                    key=lambda item: canonical_evidence_json(item.canonical_payload()),
                )
            ),
        )

    def canonical_payload(self) -> dict[str, object]:
        return _value_payload(self.status, self.unknown_reason, self.overlaps)


@dataclass(frozen=True)
class PopulationCaptureObservation:
    oa_logical_key: str
    centroid_source_feature_key: str
    whole_oa_residents: int
    minimum_edge_distance_mm: int
    decision: CaptureDecision
    source_export_fingerprint: str
    source_feature_fingerprint: str

    def canonical_payload(self) -> dict[str, object]:
        return _record_payload(self)


@dataclass(frozen=True)
class PopulationCaptureLimit:
    limit_code: str
    detail: str

    def canonical_payload(self) -> dict[str, object]:
        return _record_payload(self)


@dataclass(frozen=True)
class PopulationCaptureValue:
    status: ValueStatus
    observations: tuple[PopulationCaptureObservation, ...] = ()
    limits: tuple[PopulationCaptureLimit, ...] = ()
    unknown_reason: str | None = None

    kind: EnrichmentKind = field(init=False, default="population-capture")

    def __post_init__(self) -> None:
        _validate_status(self.status, self.unknown_reason, self.observations)
        _validate_unique(
            self.observations,
            lambda item: item.oa_logical_key,
            "population OA observations",
        )
        _validate_unique(self.limits, lambda item: item.limit_code, "population limits")
        for item in self.observations:
            _required(item.oa_logical_key, "OA logical key")
            _required(item.centroid_source_feature_key, "centroid source feature key")
            _nonnegative(item.whole_oa_residents, "whole OA residents")
            _nonnegative(item.minimum_edge_distance_mm, "minimum edge distance")
            if item.decision not in {"captured", "borderline", "outside"}:
                raise ValueError("population capture decision is invalid")
            _sha(item.source_export_fingerprint, "source export fingerprint")
            _sha(item.source_feature_fingerprint, "source feature fingerprint")
        for item in self.limits:
            _required(item.limit_code, "population limit code")
            _required(item.detail, "population limit detail")
        object.__setattr__(
            self,
            "observations",
            tuple(sorted(self.observations, key=lambda item: item.oa_logical_key)),
        )
        object.__setattr__(
            self,
            "limits",
            tuple(sorted(self.limits, key=lambda item: item.limit_code)),
        )

    def canonical_payload(self) -> dict[str, object]:
        payload = _value_payload(self.status, self.unknown_reason, self.observations)
        payload["limits"] = [item.canonical_payload() for item in self.limits]
        return payload


@dataclass(frozen=True)
class EducationReachObservation:
    target_kind: str
    target_id: str
    phase: str | None
    access_point_status: AccessPointStatus
    edge_to_access_distance_mm: int | None
    source_feature_fingerprint: str | None

    def canonical_payload(self) -> dict[str, object]:
        return _record_payload(self)


@dataclass(frozen=True)
class EducationReachEvidence:
    target_id: str
    evidence_id: str

    def canonical_payload(self) -> dict[str, object]:
        return _record_payload(self)


@dataclass(frozen=True)
class EducationReachValue:
    status: ValueStatus
    observations: tuple[EducationReachObservation, ...] = ()
    evidence: tuple[EducationReachEvidence, ...] = ()
    unknown_reason: str | None = None

    kind: EnrichmentKind = field(init=False, default="education-reach-observation")

    def __post_init__(self) -> None:
        _validate_status(self.status, self.unknown_reason, self.observations)
        if self.status == "unknown" and self.unknown_reason not in {
            "not-edge-decomposable",
            "missing-source",
            "missing-coverage",
            "unusable-geometry",
        }:
            raise ValueError("education Unknown requires a controlled reason")
        _validate_unique(
            self.observations,
            lambda item: (item.target_kind, item.target_id),
            "education reach observations",
        )
        _validate_unique(
            self.evidence,
            lambda item: (item.target_id, item.evidence_id),
            "education evidence",
        )
        target_ids = {item.target_id for item in self.observations}
        for item in self.observations:
            _required(item.target_kind, "education target kind")
            _required(item.target_id, "education target ID")
            if item.access_point_status not in {"observed", "not-observed", "unknown"}:
                raise ValueError("education access-point status is invalid")
            if item.edge_to_access_distance_mm is not None:
                _nonnegative(item.edge_to_access_distance_mm, "edge-to-access distance")
            if item.source_feature_fingerprint is not None:
                _sha(item.source_feature_fingerprint, "source feature fingerprint")
        for item in self.evidence:
            if item.target_id not in target_ids:
                raise ValueError("education evidence must reference an observation target")
            _required(item.evidence_id, "education evidence ID")
        object.__setattr__(
            self,
            "observations",
            tuple(
                sorted(
                    self.observations,
                    key=lambda item: (item.target_kind, item.target_id),
                )
            ),
        )
        object.__setattr__(
            self,
            "evidence",
            tuple(sorted(self.evidence, key=lambda item: (item.target_id, item.evidence_id))),
        )

    def canonical_payload(self) -> dict[str, object]:
        payload = _value_payload(self.status, self.unknown_reason, self.observations)
        payload["evidence"] = [item.canonical_payload() for item in self.evidence]
        return payload


@dataclass(frozen=True)
class ElevationSample:
    ordinal: int
    distance_mm: int
    elevation_mm: int
    source_evidence_key: str
    quality_code: str
    coverage_code: str

    def canonical_payload(self) -> dict[str, object]:
        return _record_payload(self)


@dataclass(frozen=True)
class GradientSection:
    ordinal: int
    start_distance_mm: int
    end_distance_mm: int
    gradient_microratio: int
    sustained: bool

    def canonical_payload(self) -> dict[str, object]:
        return _record_payload(self)


@dataclass(frozen=True)
class ElevationProfileValue:
    status: ValueStatus
    distance_mm: int | None = None
    ascent_mm: int | None = None
    descent_mm: int | None = None
    sustained_gradient_microratio: int | None = None
    sustained_gradient_rationale: str | None = None
    samples: tuple[ElevationSample, ...] = ()
    sections: tuple[GradientSection, ...] = ()
    unknown_reason: str | None = None

    kind: EnrichmentKind = field(init=False, default="elevation-profile")

    def __post_init__(self) -> None:
        _validate_status(self.status, self.unknown_reason, self.samples)
        if self.status == "available":
            for name in ("distance_mm", "ascent_mm", "descent_mm"):
                value = getattr(self, name)
                if value is None:
                    raise ValueError(f"available elevation profile requires {name}")
                _nonnegative(value, name)
            if len(self.samples) < 2:
                raise ValueError("available elevation profile requires at least two samples")
        elif any(
            value is not None
            for value in (
                self.distance_mm,
                self.ascent_mm,
                self.descent_mm,
                self.sustained_gradient_microratio,
                self.sustained_gradient_rationale,
            )
        ):
            raise ValueError("unavailable elevation profile cannot contain summary values")
        _validate_ordinals(self.samples, "elevation samples")
        _validate_ordinals(self.sections, "gradient sections")
        last_distance = -1
        for item in self.samples:
            _nonnegative(item.distance_mm, "sample distance")
            _required(item.source_evidence_key, "sample source evidence key")
            _required(item.quality_code, "sample quality code")
            _required(item.coverage_code, "sample coverage code")
            if item.distance_mm < last_distance:
                raise ValueError("elevation samples must be in canonical direction")
            last_distance = item.distance_mm
        for item in self.sections:
            _nonnegative(item.start_distance_mm, "section start distance")
            if item.end_distance_mm <= item.start_distance_mm:
                raise ValueError("gradient section must have positive length")
        object.__setattr__(
            self,
            "samples",
            tuple(sorted(self.samples, key=lambda item: item.ordinal)),
        )
        object.__setattr__(
            self,
            "sections",
            tuple(sorted(self.sections, key=lambda item: item.ordinal)),
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "unknown_reason": self.unknown_reason,
            "distance_mm": self.distance_mm,
            "ascent_mm": self.ascent_mm,
            "descent_mm": self.descent_mm,
            "sustained_gradient_microratio": self.sustained_gradient_microratio,
            "sustained_gradient_rationale": self.sustained_gradient_rationale,
            "samples": [item.canonical_payload() for item in self.samples],
            "sections": [item.canonical_payload() for item in self.sections],
        }


type EdgeEnrichmentValue = (
    OfficialClassificationValue
    | PopulationCaptureValue
    | EducationReachValue
    | ElevationProfileValue
)


@dataclass(frozen=True)
class EdgeEnrichmentRequest:
    edge: CanonicalLogicalEdge
    header: EdgeEnrichmentHeader
    kind: EnrichmentKind
    value_schema_version: str
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.edge, CanonicalLogicalEdge):
            raise ValueError("edge enrichment request requires a canonical logical edge")
        if not isinstance(self.header, EdgeEnrichmentHeader):
            raise ValueError("edge enrichment request requires an Edge Enrichment header")
        if self.kind not in VALUE_SCHEMAS:
            raise ValueError("unsupported Edge Enrichment kind")
        if self.value_schema_version != VALUE_SCHEMAS[self.kind]:
            raise ValueError("Edge Enrichment value schema does not match its kind")
        if (
            self.header.stable_edge_id != self.edge.stable_edge_id
            or self.header.geometry_fingerprint != self.edge.geometry_fingerprint
        ):
            raise ValueError("Edge Enrichment header does not match its canonical edge revision")
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("Edge Enrichment fingerprint is stale or collides with its payload")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        header = self.header
        return {
            "contract": "satn-edge-enrichment/v1",
            "kind": self.kind,
            "value_schema": self.value_schema_version,
            "stable_edge_id": header.stable_edge_id,
            "geometry_fingerprint": header.geometry_fingerprint,
            "partition_attestation_fingerprints": [
                item.fingerprint for item in header.partition_attestations
            ],
            "algorithm": {
                "id": header.algorithm_id,
                "contract": header.algorithm_contract,
                "implementation_dependency_fingerprint": (
                    header.implementation_dependency_fingerprint
                ),
            },
            "parameters_fingerprint": header.parameters.fingerprint,
        }


@dataclass(frozen=True)
class EdgeEnrichmentDiagnostic:
    code: str
    phase: str
    count_value: int | None = None
    decimal_value: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        _required(self.code, "diagnostic code")
        _required(self.phase, "diagnostic phase")
        if self.count_value is not None:
            _nonnegative(self.count_value, "diagnostic count")

    def canonical_payload(self) -> dict[str, object]:
        return _record_payload(self)


@dataclass(frozen=True)
class EdgeEnrichmentResult:
    request: EdgeEnrichmentRequest
    value: EdgeEnrichmentValue
    diagnostics: tuple[EdgeEnrichmentDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.value.kind != self.request.kind:
            raise ValueError("typed Edge Enrichment value does not match request kind")
        object.__setattr__(
            self,
            "diagnostics",
            tuple(
                sorted(
                    self.diagnostics,
                    key=lambda item: canonical_evidence_json(item.canonical_payload()),
                )
            ),
        )

    @property
    def fingerprint(self) -> str:
        return self.request.fingerprint

    @property
    def outcome(self) -> ValueStatus:
        return self.value.status

    @property
    def value_fingerprint(self) -> str:
        return evidence_fingerprint(
            {
                "contract": self.request.value_schema_version,
                "value": self.value.canonical_payload(),
            }
        )


@dataclass(frozen=True)
class EdgeEnrichmentResolution:
    records: tuple[EdgeEnrichmentResult, ...]
    hit_fingerprints: tuple[str, ...]
    miss_fingerprints: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioEnrichmentCitation:
    scenario_fingerprint: str
    enrichment_fingerprint: str
    kind: EnrichmentKind
    value_schema_version: str
    stable_edge_id: str
    geometry_fingerprint: str
    consumption_role: str

    def __post_init__(self) -> None:
        _sha(self.scenario_fingerprint, "scenario fingerprint")
        _sha(self.enrichment_fingerprint, "enrichment fingerprint")
        _sha(self.geometry_fingerprint, "geometry fingerprint")
        _required(self.stable_edge_id, "stable edge ID")
        _required(self.consumption_role, "consumption role")
        if self.value_schema_version != VALUE_SCHEMAS.get(self.kind):
            raise ValueError("citation value schema does not match its kind")


def create_edge_enrichment_schema(connection: Any) -> None:
    """Create the closed typed tables owned by the internal enrichment module."""

    connection.execute(_SCHEMA_SQL)


class EdgeEnrichmentStore:
    """Internal resolve/verify batching over an existing DuckDB transaction."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def resolve(
        self,
        requests: Sequence[EdgeEnrichmentRequest],
        materialise: Callable[[EdgeEnrichmentRequest], EdgeEnrichmentResult],
    ) -> EdgeEnrichmentResolution:
        ordered = _normalise_requests(requests)
        hits: list[str] = []
        misses: list[str] = []
        records: list[EdgeEnrichmentResult] = []
        for request in ordered:
            core = self._connection.execute(
                """
                SELECT canonical_identity_payload, value_fingerprint
                FROM edge_enrichment WHERE enrichment_fingerprint = ?
                """,
                [request.fingerprint],
            ).fetchone()
            if core is not None:
                record = self._load_exact(request)
                hits.append(request.fingerprint)
                records.append(record)
                continue
            record = materialise(request)
            if not isinstance(record, EdgeEnrichmentResult) or record.request != request:
                raise ValueError("Edge Enrichment materialiser returned a different request")
            self._insert(record)
            misses.append(request.fingerprint)
            records.append(record)
        return EdgeEnrichmentResolution(
            records=tuple(records),
            hit_fingerprints=tuple(hits),
            miss_fingerprints=tuple(misses),
        )

    def verify(self, records: Sequence[EdgeEnrichmentResult]) -> tuple[EdgeEnrichmentResult, ...]:
        return tuple(self._load_exact(record.request, expected=record) for record in records)

    def verify_citations(
        self, citations: Sequence[ScenarioEnrichmentCitation]
    ) -> tuple[ScenarioEnrichmentCitation, ...]:
        ordered = tuple(
            sorted(
                citations,
                key=lambda item: (
                    item.scenario_fingerprint,
                    item.enrichment_fingerprint,
                    item.consumption_role,
                ),
            )
        )
        if len(
            {
                (item.scenario_fingerprint, item.enrichment_fingerprint, item.consumption_role)
                for item in ordered
            }
        ) != len(ordered):
            raise ValueError("scenario enrichment citations must be unique")
        for citation in ordered:
            row = self._connection.execute(
                """
                SELECT kind, value_schema_version, stable_edge_id, geometry_fingerprint,
                       outcome, value_fingerprint
                FROM edge_enrichment WHERE enrichment_fingerprint = ?
                """,
                [citation.enrichment_fingerprint],
            ).fetchone()
            expected = (
                citation.kind,
                citation.value_schema_version,
                citation.stable_edge_id,
                citation.geometry_fingerprint,
            )
            if row is None or tuple(row[:4]) != expected:
                raise EdgeEnrichmentCollisionError(
                    "scenario citation does not resolve to the exact Edge Enrichment"
                )
            value = self._load_value_for(
                citation.kind,
                citation.enrichment_fingerprint,
                str(row[4]),
            )
            value_fingerprint = evidence_fingerprint(
                {
                    "contract": citation.value_schema_version,
                    "value": value.canonical_payload(),
                }
            )
            if value_fingerprint != str(row[5]):
                raise EdgeEnrichmentCollisionError(
                    "scenario citation resolves to changed typed enrichment rows"
                )
            self._connection.execute(
                """
                INSERT INTO scenario_enrichment_citation VALUES (?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                [
                    citation.scenario_fingerprint,
                    citation.enrichment_fingerprint,
                    citation.consumption_role,
                    citation.stable_edge_id,
                    citation.geometry_fingerprint,
                ],
            )
        return ordered

    def _insert(self, record: EdgeEnrichmentResult) -> None:
        request = record.request
        identity_bytes = canonical_evidence_json(request.canonical_payload()).encode()
        parameter_bytes = canonical_evidence_json(
            request.header.parameters.canonical_payload()
        ).encode()
        existing_parameter = self._connection.execute(
            """
            SELECT canonical_payload FROM edge_enrichment_parameter_set
            WHERE parameters_fingerprint = ?
            """,
            [request.header.parameters.fingerprint],
        ).fetchone()
        if existing_parameter is not None and bytes(existing_parameter[0]) != parameter_bytes:
            raise EdgeEnrichmentCollisionError("Edge Enrichment parameter fingerprint collision")
        self._connection.execute(
            """
            INSERT INTO edge_enrichment_parameter_set VALUES (?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                request.header.parameters.fingerprint,
                request.header.parameters.contract,
                parameter_bytes,
            ],
        )
        self._connection.execute(
            """
            INSERT INTO edge_enrichment VALUES (
                ?, 'satn-edge-enrichment/v1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp
            )
            """,
            [
                request.fingerprint,
                request.kind,
                request.value_schema_version,
                request.edge.stable_edge_id,
                request.edge.geometry_fingerprint,
                request.header.algorithm_contract,
                request.header.implementation_dependency_fingerprint,
                request.header.parameters.fingerprint,
                record.outcome,
                record.value_fingerprint,
                identity_bytes,
            ],
        )
        for attestation in request.header.partition_attestations:
            self._connection.execute(
                "INSERT INTO edge_enrichment_partition VALUES (?, ?)",
                [request.fingerprint, attestation.fingerprint],
            )
        self._insert_value(record)
        for diagnostic in record.diagnostics:
            self._connection.execute(
                "INSERT INTO edge_enrichment_diagnostic VALUES (?, ?, ?, ?, ?, ?)",
                [
                    request.fingerprint,
                    diagnostic.code,
                    diagnostic.phase,
                    diagnostic.count_value,
                    diagnostic.decimal_value,
                    diagnostic.detail,
                ],
            )

    def _load_exact(
        self,
        request: EdgeEnrichmentRequest,
        *,
        expected: EdgeEnrichmentResult | None = None,
    ) -> EdgeEnrichmentResult:
        row = self._connection.execute(
            """
            SELECT contract_version, kind, value_schema_version, stable_edge_id,
                   geometry_fingerprint, algorithm_contract,
                   algorithm_dependency_fingerprint, parameters_fingerprint,
                   outcome, value_fingerprint, canonical_identity_payload
            FROM edge_enrichment WHERE enrichment_fingerprint = ?
            """,
            [request.fingerprint],
        ).fetchone()
        if row is None:
            raise LookupError(f"Edge Enrichment {request.fingerprint} is not found")
        identity_bytes = canonical_evidence_json(request.canonical_payload()).encode()
        expected_core = (
            "satn-edge-enrichment/v1",
            request.kind,
            request.value_schema_version,
            request.edge.stable_edge_id,
            request.edge.geometry_fingerprint,
            request.header.algorithm_contract,
            request.header.implementation_dependency_fingerprint,
            request.header.parameters.fingerprint,
        )
        if tuple(row[:8]) != expected_core or bytes(row[10]) != identity_bytes:
            raise EdgeEnrichmentCollisionError(
                "Edge Enrichment fingerprint is bound to a different identity"
            )
        attestations = tuple(
            str(item[0])
            for item in self._connection.execute(
                """
                SELECT partition_attestation_fingerprint
                FROM edge_enrichment_partition
                WHERE enrichment_fingerprint = ?
                ORDER BY partition_attestation_fingerprint
                """,
                [request.fingerprint],
            ).fetchall()
        )
        expected_attestations = tuple(
            item.fingerprint for item in request.header.partition_attestations
        )
        if attestations != expected_attestations:
            raise EdgeEnrichmentCollisionError(
                "Edge Enrichment partition attestation set is incomplete or changed"
            )
        parameter = self._connection.execute(
            """
            SELECT contract_version, canonical_payload
            FROM edge_enrichment_parameter_set WHERE parameters_fingerprint = ?
            """,
            [request.header.parameters.fingerprint],
        ).fetchone()
        expected_parameter = (
            request.header.parameters.contract,
            canonical_evidence_json(request.header.parameters.canonical_payload()).encode(),
        )
        if parameter is None or (str(parameter[0]), bytes(parameter[1])) != expected_parameter:
            raise EdgeEnrichmentCollisionError(
                "Edge Enrichment parameter payload is incomplete or changed"
            )
        value = self._load_value_for(request.kind, request.fingerprint, str(row[8]))
        diagnostics = self._load_diagnostics(request.fingerprint)
        result = EdgeEnrichmentResult(request=request, value=value, diagnostics=diagnostics)
        if result.value_fingerprint != str(row[9]) or (
            expected is not None and result != expected
        ):
            raise EdgeEnrichmentCollisionError(
                "Edge Enrichment typed value is incomplete or changed"
            )
        return result

    def _insert_value(self, record: EdgeEnrichmentResult) -> None:
        fingerprint = record.fingerprint
        value = record.value
        if isinstance(value, OfficialClassificationValue):
            self._connection.execute(
                "INSERT INTO edge_official_classification_state VALUES (?, ?, ?)",
                [fingerprint, value.status, value.unknown_reason],
            )
            for ordinal, item in enumerate(value.overlaps):
                self._connection.execute(
                    "INSERT INTO edge_official_classification_overlap "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [fingerprint, ordinal, *tuple(item.canonical_payload().values())],
                )
        elif isinstance(value, PopulationCaptureValue):
            self._connection.execute(
                "INSERT INTO edge_population_capture_state VALUES (?, ?, ?)",
                [fingerprint, value.status, value.unknown_reason],
            )
            for item in value.observations:
                self._connection.execute(
                    "INSERT INTO edge_population_capture VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [fingerprint, *tuple(item.canonical_payload().values())],
                )
            for item in value.limits:
                self._connection.execute(
                    "INSERT INTO edge_population_capture_limit VALUES (?, ?, ?)",
                    [fingerprint, item.limit_code, item.detail],
                )
        elif isinstance(value, EducationReachValue):
            self._connection.execute(
                "INSERT INTO edge_education_reach_state VALUES (?, ?, ?)",
                [fingerprint, value.status, value.unknown_reason],
            )
            for item in value.observations:
                self._connection.execute(
                    "INSERT INTO edge_education_reach_observation VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [fingerprint, *tuple(item.canonical_payload().values())],
                )
            for item in value.evidence:
                self._connection.execute(
                    "INSERT INTO edge_education_reach_evidence VALUES (?, ?, ?)",
                    [fingerprint, item.target_id, item.evidence_id],
                )
        elif isinstance(value, ElevationProfileValue):
            self._connection.execute(
                "INSERT INTO edge_elevation_profile VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    fingerprint,
                    value.status,
                    value.unknown_reason,
                    value.distance_mm,
                    value.ascent_mm,
                    value.descent_mm,
                    value.sustained_gradient_microratio,
                    value.sustained_gradient_rationale,
                ],
            )
            for item in value.sections:
                self._connection.execute(
                    "INSERT INTO edge_gradient_section VALUES (?, ?, ?, ?, ?, ?)",
                    [fingerprint, *tuple(item.canonical_payload().values())],
                )
            for item in value.samples:
                self._connection.execute(
                    "INSERT INTO edge_elevation_sample VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [fingerprint, *tuple(item.canonical_payload().values())],
                )
        else:
            raise TypeError("unsupported typed Edge Enrichment value")

    def _load_value_for(
        self,
        kind: EnrichmentKind,
        fingerprint: str,
        outcome: str,
    ) -> EdgeEnrichmentValue:
        if kind == "official-classification-overlap":
            state = self._one_state("edge_official_classification_state", fingerprint)
            rows = self._connection.execute(
                """
                SELECT source_feature_logical_key, publisher_raw_classification,
                       normalisation_contract_version, normalised_official_class,
                       overlap_length_mm, source_export_fingerprint,
                       source_feature_fingerprint
                FROM edge_official_classification_overlap
                WHERE enrichment_fingerprint = ? ORDER BY ordinal
                """,
                [fingerprint],
            ).fetchall()
            value: EdgeEnrichmentValue = OfficialClassificationValue(
                status=state[0],
                unknown_reason=state[1],
                overlaps=tuple(OfficialClassificationOverlap(*row) for row in rows),
            )
        elif kind == "population-capture":
            state = self._one_state("edge_population_capture_state", fingerprint)
            observations = self._connection.execute(
                """
                SELECT oa_logical_key, centroid_source_feature_key, whole_oa_residents,
                       minimum_edge_distance_mm, decision, source_export_fingerprint,
                       source_feature_fingerprint
                FROM edge_population_capture WHERE enrichment_fingerprint = ?
                ORDER BY oa_logical_key
                """,
                [fingerprint],
            ).fetchall()
            limits = self._connection.execute(
                """
                SELECT limit_code, detail FROM edge_population_capture_limit
                WHERE enrichment_fingerprint = ? ORDER BY limit_code
                """,
                [fingerprint],
            ).fetchall()
            value = PopulationCaptureValue(
                status=state[0],
                unknown_reason=state[1],
                observations=tuple(PopulationCaptureObservation(*row) for row in observations),
                limits=tuple(PopulationCaptureLimit(*row) for row in limits),
            )
        elif kind == "education-reach-observation":
            state = self._one_state("edge_education_reach_state", fingerprint)
            observations = self._connection.execute(
                """
                SELECT target_kind, target_id, phase, access_point_status,
                       edge_to_access_distance_mm, source_feature_fingerprint
                FROM edge_education_reach_observation
                WHERE enrichment_fingerprint = ? ORDER BY target_kind, target_id
                """,
                [fingerprint],
            ).fetchall()
            evidence = self._connection.execute(
                """
                SELECT target_id, evidence_id FROM edge_education_reach_evidence
                WHERE enrichment_fingerprint = ? ORDER BY target_id, evidence_id
                """,
                [fingerprint],
            ).fetchall()
            value = EducationReachValue(
                status=state[0],
                unknown_reason=state[1],
                observations=tuple(EducationReachObservation(*row) for row in observations),
                evidence=tuple(EducationReachEvidence(*row) for row in evidence),
            )
        else:
            row = self._connection.execute(
                """
                SELECT value_status, unknown_reason, distance_mm, ascent_mm, descent_mm,
                       sustained_gradient_microratio, sustained_gradient_rationale
                FROM edge_elevation_profile WHERE enrichment_fingerprint = ?
                """,
                [fingerprint],
            ).fetchone()
            if row is None:
                raise EdgeEnrichmentCollisionError("Edge Enrichment typed state is missing")
            samples = self._connection.execute(
                """
                SELECT ordinal, distance_mm, elevation_mm, source_evidence_key,
                       quality_code, coverage_code
                FROM edge_elevation_sample WHERE enrichment_fingerprint = ?
                ORDER BY ordinal
                """,
                [fingerprint],
            ).fetchall()
            sections = self._connection.execute(
                """
                SELECT ordinal, start_distance_mm, end_distance_mm,
                       gradient_microratio, sustained
                FROM edge_gradient_section WHERE enrichment_fingerprint = ?
                ORDER BY ordinal
                """,
                [fingerprint],
            ).fetchall()
            value = ElevationProfileValue(
                status=row[0],
                unknown_reason=row[1],
                distance_mm=row[2],
                ascent_mm=row[3],
                descent_mm=row[4],
                sustained_gradient_microratio=row[5],
                sustained_gradient_rationale=row[6],
                samples=tuple(ElevationSample(*item) for item in samples),
                sections=tuple(GradientSection(*item) for item in sections),
            )
        if value.status != outcome:
            raise EdgeEnrichmentCollisionError("Edge Enrichment outcome and typed state differ")
        return value

    def _one_state(self, table: str, fingerprint: str) -> tuple[str, str | None]:
        row = self._connection.execute(
            f"SELECT value_status, unknown_reason FROM {table} "
            "WHERE enrichment_fingerprint = ?",
            [fingerprint],
        ).fetchone()
        if row is None:
            raise EdgeEnrichmentCollisionError("Edge Enrichment typed state is missing")
        return str(row[0]), None if row[1] is None else str(row[1])

    def _load_diagnostics(
        self, fingerprint: str
    ) -> tuple[EdgeEnrichmentDiagnostic, ...]:
        rows = self._connection.execute(
            """
            SELECT diagnostic_code, phase, count_value,
                   CAST(decimal_value AS VARCHAR), detail
            FROM edge_enrichment_diagnostic WHERE enrichment_fingerprint = ?
            ORDER BY diagnostic_code, phase, count_value, decimal_value, detail
            """,
            [fingerprint],
        ).fetchall()
        return tuple(EdgeEnrichmentDiagnostic(*row) for row in rows)


def _normalise_requests(
    requests: Sequence[EdgeEnrichmentRequest],
) -> tuple[EdgeEnrichmentRequest, ...]:
    by_fingerprint: dict[str, EdgeEnrichmentRequest] = {}
    for request in requests:
        if not isinstance(request, EdgeEnrichmentRequest):
            raise ValueError("resolve requires EdgeEnrichmentRequest records")
        existing = by_fingerprint.setdefault(request.fingerprint, request)
        if canonical_evidence_json(existing.canonical_payload()) != canonical_evidence_json(
            request.canonical_payload()
        ):
            raise EdgeEnrichmentCollisionError("request batch contains a fingerprint collision")
    return tuple(by_fingerprint[key] for key in sorted(by_fingerprint))


def _record_payload(record: object) -> dict[str, object]:
    return {
        name: value
        for name, value in record.__dict__.items()
        if name not in {"kind", "fingerprint"}
    }


def _value_payload(
    status: ValueStatus,
    unknown_reason: str | None,
    observations: Sequence[object],
) -> dict[str, object]:
    return {
        "status": status,
        "unknown_reason": unknown_reason,
        "observations": [
            item.canonical_payload()  # type: ignore[attr-defined]
            for item in observations
        ],
    }


def _validate_status(
    status: ValueStatus, unknown_reason: str | None, records: Sequence[object]
) -> None:
    if status not in {"available", "no-data", "unknown"}:
        raise ValueError("Edge Enrichment status must be available, no-data, or unknown")
    if status == "available" and not records:
        raise ValueError("available Edge Enrichment requires typed observations")
    if status != "available" and records:
        raise ValueError("unavailable Edge Enrichment cannot contain typed observations")
    if status == "unknown":
        _required(unknown_reason, "Unknown reason")
    elif unknown_reason is not None:
        raise ValueError("only an Unknown Edge Enrichment has an Unknown reason")


def _validate_unique(
    records: Sequence[object],
    key: Callable[[Any], object],
    label: str,
) -> None:
    values = [key(item) for item in records]
    if len(set(values)) != len(values):
        raise ValueError(f"{label} cannot contain duplicates")


def _validate_ordinals(records: Sequence[object], label: str) -> None:
    ordinals = sorted(item.ordinal for item in records)  # type: ignore[attr-defined]
    if ordinals != list(range(len(records))):
        raise ValueError(f"{label} require contiguous zero-based ordinals")


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty")
    return value


def _sha(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a full lowercase SHA-256")
    return value


def _nonnegative(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS edge_enrichment (
    enrichment_fingerprint VARCHAR PRIMARY KEY,
    contract_version VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    value_schema_version VARCHAR NOT NULL,
    stable_edge_id VARCHAR NOT NULL,
    geometry_fingerprint VARCHAR NOT NULL,
    algorithm_contract VARCHAR NOT NULL,
    algorithm_dependency_fingerprint VARCHAR NOT NULL,
    parameters_fingerprint VARCHAR NOT NULL,
    outcome VARCHAR NOT NULL,
    value_fingerprint VARCHAR NOT NULL,
    canonical_identity_payload BLOB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS edge_enrichment_partition (
    enrichment_fingerprint VARCHAR NOT NULL,
    partition_attestation_fingerprint VARCHAR NOT NULL,
    PRIMARY KEY (enrichment_fingerprint, partition_attestation_fingerprint)
);
CREATE TABLE IF NOT EXISTS edge_enrichment_parameter_set (
    parameters_fingerprint VARCHAR PRIMARY KEY,
    contract_version VARCHAR NOT NULL,
    canonical_payload BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS edge_enrichment_diagnostic (
    enrichment_fingerprint VARCHAR NOT NULL,
    diagnostic_code VARCHAR NOT NULL,
    phase VARCHAR NOT NULL,
    count_value BIGINT,
    decimal_value DECIMAL(20,6),
    detail VARCHAR
);
CREATE TABLE IF NOT EXISTS scenario_enrichment_citation (
    scenario_fingerprint VARCHAR NOT NULL,
    enrichment_fingerprint VARCHAR NOT NULL,
    consumption_role VARCHAR NOT NULL,
    stable_edge_id VARCHAR NOT NULL,
    geometry_fingerprint VARCHAR NOT NULL,
    PRIMARY KEY (scenario_fingerprint, enrichment_fingerprint, consumption_role)
);
CREATE TABLE IF NOT EXISTS edge_official_classification_state (
    enrichment_fingerprint VARCHAR PRIMARY KEY,
    value_status VARCHAR NOT NULL,
    unknown_reason VARCHAR
);
CREATE TABLE IF NOT EXISTS edge_official_classification_overlap (
    enrichment_fingerprint VARCHAR NOT NULL,
    ordinal BIGINT NOT NULL,
    source_feature_logical_key VARCHAR NOT NULL,
    publisher_raw_classification VARCHAR NOT NULL,
    normalisation_contract_version VARCHAR NOT NULL,
    normalised_official_class VARCHAR NOT NULL,
    overlap_length_mm BIGINT NOT NULL,
    source_export_fingerprint VARCHAR NOT NULL,
    source_feature_fingerprint VARCHAR NOT NULL,
    PRIMARY KEY (enrichment_fingerprint, ordinal)
);
CREATE TABLE IF NOT EXISTS edge_population_capture_state (
    enrichment_fingerprint VARCHAR PRIMARY KEY,
    value_status VARCHAR NOT NULL,
    unknown_reason VARCHAR
);
CREATE TABLE IF NOT EXISTS edge_population_capture (
    enrichment_fingerprint VARCHAR NOT NULL,
    oa_logical_key VARCHAR NOT NULL,
    centroid_source_feature_key VARCHAR NOT NULL,
    whole_oa_residents BIGINT NOT NULL,
    minimum_edge_distance_mm BIGINT NOT NULL,
    decision VARCHAR NOT NULL,
    source_export_fingerprint VARCHAR NOT NULL,
    source_feature_fingerprint VARCHAR NOT NULL,
    PRIMARY KEY (enrichment_fingerprint, oa_logical_key)
);
CREATE TABLE IF NOT EXISTS edge_population_capture_limit (
    enrichment_fingerprint VARCHAR NOT NULL,
    limit_code VARCHAR NOT NULL,
    detail VARCHAR NOT NULL,
    PRIMARY KEY (enrichment_fingerprint, limit_code)
);
CREATE TABLE IF NOT EXISTS edge_education_reach_state (
    enrichment_fingerprint VARCHAR PRIMARY KEY,
    value_status VARCHAR NOT NULL,
    unknown_reason VARCHAR
);
CREATE TABLE IF NOT EXISTS edge_education_reach_observation (
    enrichment_fingerprint VARCHAR NOT NULL,
    target_kind VARCHAR NOT NULL,
    target_id VARCHAR NOT NULL,
    phase VARCHAR,
    access_point_status VARCHAR NOT NULL,
    edge_to_access_distance_mm BIGINT,
    source_feature_fingerprint VARCHAR,
    PRIMARY KEY (enrichment_fingerprint, target_kind, target_id)
);
CREATE TABLE IF NOT EXISTS edge_education_reach_evidence (
    enrichment_fingerprint VARCHAR NOT NULL,
    target_id VARCHAR NOT NULL,
    evidence_id VARCHAR NOT NULL,
    PRIMARY KEY (enrichment_fingerprint, target_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS edge_elevation_profile (
    enrichment_fingerprint VARCHAR PRIMARY KEY,
    value_status VARCHAR NOT NULL,
    unknown_reason VARCHAR,
    distance_mm BIGINT,
    ascent_mm BIGINT,
    descent_mm BIGINT,
    sustained_gradient_microratio BIGINT,
    sustained_gradient_rationale VARCHAR
);
CREATE TABLE IF NOT EXISTS edge_gradient_section (
    enrichment_fingerprint VARCHAR NOT NULL,
    ordinal BIGINT NOT NULL,
    start_distance_mm BIGINT NOT NULL,
    end_distance_mm BIGINT NOT NULL,
    gradient_microratio BIGINT NOT NULL,
    sustained BOOLEAN NOT NULL,
    PRIMARY KEY (enrichment_fingerprint, ordinal)
);
CREATE TABLE IF NOT EXISTS edge_elevation_sample (
    enrichment_fingerprint VARCHAR NOT NULL,
    ordinal BIGINT NOT NULL,
    distance_mm BIGINT NOT NULL,
    elevation_mm BIGINT NOT NULL,
    source_evidence_key VARCHAR NOT NULL,
    quality_code VARCHAR NOT NULL,
    coverage_code VARCHAR NOT NULL,
    PRIMARY KEY (enrichment_fingerprint, ordinal)
);
"""

EDGE_ENRICHMENT_EXPECTED_COLUMNS = {
    "edge_enrichment": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("contract_version", "VARCHAR", "NO"),
        ("kind", "VARCHAR", "NO"),
        ("value_schema_version", "VARCHAR", "NO"),
        ("stable_edge_id", "VARCHAR", "NO"),
        ("geometry_fingerprint", "VARCHAR", "NO"),
        ("algorithm_contract", "VARCHAR", "NO"),
        ("algorithm_dependency_fingerprint", "VARCHAR", "NO"),
        ("parameters_fingerprint", "VARCHAR", "NO"),
        ("outcome", "VARCHAR", "NO"),
        ("value_fingerprint", "VARCHAR", "NO"),
        ("canonical_identity_payload", "BLOB", "NO"),
        ("created_at", "TIMESTAMP WITH TIME ZONE", "NO"),
    ),
    "edge_enrichment_partition": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("partition_attestation_fingerprint", "VARCHAR", "NO"),
    ),
    "edge_enrichment_parameter_set": (
        ("parameters_fingerprint", "VARCHAR", "NO"),
        ("contract_version", "VARCHAR", "NO"),
        ("canonical_payload", "BLOB", "NO"),
    ),
    "edge_enrichment_diagnostic": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("diagnostic_code", "VARCHAR", "NO"),
        ("phase", "VARCHAR", "NO"),
        ("count_value", "BIGINT", "YES"),
        ("decimal_value", "DECIMAL(20,6)", "YES"),
        ("detail", "VARCHAR", "YES"),
    ),
    "scenario_enrichment_citation": (
        ("scenario_fingerprint", "VARCHAR", "NO"),
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("consumption_role", "VARCHAR", "NO"),
        ("stable_edge_id", "VARCHAR", "NO"),
        ("geometry_fingerprint", "VARCHAR", "NO"),
    ),
    "edge_official_classification_state": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("value_status", "VARCHAR", "NO"),
        ("unknown_reason", "VARCHAR", "YES"),
    ),
    "edge_official_classification_overlap": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("ordinal", "BIGINT", "NO"),
        ("source_feature_logical_key", "VARCHAR", "NO"),
        ("publisher_raw_classification", "VARCHAR", "NO"),
        ("normalisation_contract_version", "VARCHAR", "NO"),
        ("normalised_official_class", "VARCHAR", "NO"),
        ("overlap_length_mm", "BIGINT", "NO"),
        ("source_export_fingerprint", "VARCHAR", "NO"),
        ("source_feature_fingerprint", "VARCHAR", "NO"),
    ),
    "edge_population_capture_state": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("value_status", "VARCHAR", "NO"),
        ("unknown_reason", "VARCHAR", "YES"),
    ),
    "edge_population_capture": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("oa_logical_key", "VARCHAR", "NO"),
        ("centroid_source_feature_key", "VARCHAR", "NO"),
        ("whole_oa_residents", "BIGINT", "NO"),
        ("minimum_edge_distance_mm", "BIGINT", "NO"),
        ("decision", "VARCHAR", "NO"),
        ("source_export_fingerprint", "VARCHAR", "NO"),
        ("source_feature_fingerprint", "VARCHAR", "NO"),
    ),
    "edge_population_capture_limit": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("limit_code", "VARCHAR", "NO"),
        ("detail", "VARCHAR", "NO"),
    ),
    "edge_education_reach_state": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("value_status", "VARCHAR", "NO"),
        ("unknown_reason", "VARCHAR", "YES"),
    ),
    "edge_education_reach_observation": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("target_kind", "VARCHAR", "NO"),
        ("target_id", "VARCHAR", "NO"),
        ("phase", "VARCHAR", "YES"),
        ("access_point_status", "VARCHAR", "NO"),
        ("edge_to_access_distance_mm", "BIGINT", "YES"),
        ("source_feature_fingerprint", "VARCHAR", "YES"),
    ),
    "edge_education_reach_evidence": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("target_id", "VARCHAR", "NO"),
        ("evidence_id", "VARCHAR", "NO"),
    ),
    "edge_elevation_profile": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("value_status", "VARCHAR", "NO"),
        ("unknown_reason", "VARCHAR", "YES"),
        ("distance_mm", "BIGINT", "YES"),
        ("ascent_mm", "BIGINT", "YES"),
        ("descent_mm", "BIGINT", "YES"),
        ("sustained_gradient_microratio", "BIGINT", "YES"),
        ("sustained_gradient_rationale", "VARCHAR", "YES"),
    ),
    "edge_gradient_section": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("ordinal", "BIGINT", "NO"),
        ("start_distance_mm", "BIGINT", "NO"),
        ("end_distance_mm", "BIGINT", "NO"),
        ("gradient_microratio", "BIGINT", "NO"),
        ("sustained", "BOOLEAN", "NO"),
    ),
    "edge_elevation_sample": (
        ("enrichment_fingerprint", "VARCHAR", "NO"),
        ("ordinal", "BIGINT", "NO"),
        ("distance_mm", "BIGINT", "NO"),
        ("elevation_mm", "BIGINT", "NO"),
        ("source_evidence_key", "VARCHAR", "NO"),
        ("quality_code", "VARCHAR", "NO"),
        ("coverage_code", "VARCHAR", "NO"),
    ),
}
