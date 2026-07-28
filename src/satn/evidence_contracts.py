"""Immutable, portable identities for Local Evidence logical artifacts.

The contracts in this module deliberately describe evidence, not its local
materialisation.  Store paths, database bytes and retrieval operations therefore
never enter a fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from types import MappingProxyType

from pyproj import CRS
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BNG_10KM_CELL = re.compile(r"^[A-HJ-NP-Z]{2}[0-9]{2}$")


def canonical_evidence_json(value: object) -> str:
    """Return canonical JSON for an evidence identity payload.

    Evidence identities never admit JSON floats: measurements must use an
    explicitly named integer base unit or a normalised decimal string.
    """

    return json.dumps(
        _canonical_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def evidence_fingerprint(value: object) -> str:
    """Return the full SHA-256 for one canonical evidence payload."""

    return hashlib.sha256(canonical_evidence_json(value).encode("utf-8")).hexdigest()


def _canonical_json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        raise ValueError("evidence identity payloads cannot contain JSON floats")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("evidence identity object keys must be strings")
        return {key: _canonical_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    raise ValueError(f"unsupported evidence identity value: {type(value).__name__}")


def _freeze_mapping(value: Mapping[str, object], name: str) -> Mapping[str, object]:
    canonical = _canonical_json_value(value)
    if not isinstance(canonical, dict):
        raise ValueError(f"{name} must be a mapping")
    frozen = _freeze_json_value(canonical)
    assert isinstance(frozen, Mapping)
    return frozen


def _freeze_json_value(value: object) -> object:
    canonical = _canonical_json_value(value)
    if isinstance(canonical, dict):
        return MappingProxyType(
            {key: _freeze_json_value(item) for key, item in sorted(canonical.items())}
        )
    if isinstance(canonical, list):
        return tuple(_freeze_json_value(item) for item in canonical)
    return canonical


def _required_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def _sha256(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase full SHA-256")
    return value


def _crs_identity(value: object, name: str) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"{name} requires an explicit CRS")
    try:
        crs = CRS.from_user_input(value)
    except Exception as error:  # pyproj has several parser-specific error types.
        raise ValueError(f"{name} must be a valid explicit CRS") from error
    authority = crs.to_authority()
    if authority is not None:
        return f"{authority[0]}:{authority[1]}"
    return crs.to_wkt(version="WKT2_2019", pretty=False)


def _sorted_text_set(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    canonical = tuple(_required_text(value, name) for value in values)
    if len(set(canonical)) != len(canonical):
        raise ValueError(f"{name} cannot contain duplicates")
    return tuple(sorted(canonical))


def canonical_evidence_geometry(geometry: object, crs: object) -> dict[str, object]:
    """Return the BNG-millimetre payload for geometry already transformed to EPSG:27700.

    EPSG:27700 coordinates are metres.  The identity quantises those metre
    coordinates once to integer millimetres using declared half-even rounding.
    """

    if _crs_identity(crs, "evidence geometry") != "EPSG:27700":
        raise ValueError("satn-evidence-geometry-v1 requires explicit EPSG:27700")
    if isinstance(geometry, Point):
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError("evidence geometry must be nonempty and valid")
        value: dict[str, object] = {
            "type": "Point",
            "coordinates": _millimetre_coordinate(geometry.coords[0]),
        }
    elif isinstance(geometry, LineString):
        value = {"type": "LineString", "coordinates": _canonical_evidence_line(geometry)}
    elif isinstance(geometry, MultiLineString):
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError("evidence geometry must be nonempty and valid")
        lines = sorted(
            (_canonical_evidence_line(line) for line in geometry.geoms),
            key=canonical_evidence_json,
        )
        value = {"type": "MultiLineString", "coordinates": lines}
    elif isinstance(geometry, Polygon):
        value = _canonical_evidence_polygon(geometry)
    elif isinstance(geometry, MultiPolygon):
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError("evidence geometry must be nonempty and valid")
        polygons = sorted(
            (_canonical_evidence_polygon(polygon) for polygon in geometry.geoms),
            key=canonical_evidence_json,
        )
        value = {"type": "MultiPolygon", "polygons": polygons}
    else:
        raise ValueError(
            "satn-evidence-geometry-v1 supports Point, LineString, MultiLineString, "
            "Polygon and MultiPolygon"
        )
    return {
        "contract": "satn-evidence-geometry-v1",
        "crs": "EPSG:27700",
        "dimensions": 2,
        "input_coordinate_unit": "metres",
        "coordinate_unit": "millimetres",
        "quantization": "nearest-millimetre-half-even",
        "geometry": value,
    }


def evidence_geometry_fingerprint(geometry: object, crs: object) -> str:
    """Return the full fingerprint for canonical evidence geometry."""

    return evidence_fingerprint(canonical_evidence_geometry(geometry, crs))


def _millimetre_coordinate(coordinate: object) -> list[int]:
    try:
        x, y = tuple(coordinate)[:2]  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError("evidence geometry requires two-dimensional coordinates") from error
    result: list[int] = []
    for value in (x, y):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("evidence geometry coordinates must be finite")
        millimetres = (Decimal(str(value)) * 1000).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_EVEN,
        )
        result.append(int(millimetres))
    return result


def _canonical_evidence_line(line: LineString) -> list[list[int]]:
    if line.is_empty or not line.is_valid:
        raise ValueError("evidence geometry must be nonempty and valid")
    coordinates: list[list[int]] = []
    for coordinate in line.coords:
        canonical = _millimetre_coordinate(coordinate)
        if not coordinates or canonical != coordinates[-1]:
            coordinates.append(canonical)
    if len(coordinates) < 2:
        raise ValueError("evidence geometry line collapses after duplicate removal")
    return min(coordinates, list(reversed(coordinates)))


def _canonical_evidence_ring(ring: object) -> list[list[int]]:
    try:
        source_coordinates = ring.coords  # type: ignore[union-attr]
    except AttributeError as error:
        raise ValueError("evidence polygon requires a valid ring") from error
    coordinates: list[list[int]] = []
    for coordinate in source_coordinates:
        canonical = _millimetre_coordinate(coordinate)
        if not coordinates or canonical != coordinates[-1]:
            coordinates.append(canonical)
    if coordinates and coordinates[0] == coordinates[-1]:
        coordinates.pop()
    if len(coordinates) < 3:
        raise ValueError("evidence polygon ring collapses after duplicate removal")
    rotations = [
        orientation[offset:] + orientation[:offset]
        for orientation in (coordinates, list(reversed(coordinates)))
        for offset in range(len(coordinates))
    ]
    canonical_ring = min(rotations)
    return [*canonical_ring, canonical_ring[0]]


def _canonical_evidence_polygon(polygon: Polygon) -> dict[str, object]:
    if polygon.is_empty or not polygon.is_valid:
        raise ValueError("evidence geometry must be nonempty and valid")
    holes = sorted(
        (_canonical_evidence_ring(ring) for ring in polygon.interiors),
        key=canonical_evidence_json,
    )
    return {
        "type": "Polygon",
        "exterior": _canonical_evidence_ring(polygon.exterior),
        "holes": holes,
    }


@dataclass(frozen=True)
class SourceExport:
    """One governed raw export, independent of where or when it was retrieved."""

    source_family: str
    dataset: str
    layer: str
    publisher_release: str
    effective_date: str
    licence: str
    format: str
    declared_crs: str
    raw_bytes_sha256: str
    provenance: Mapping[str, object] = field(default_factory=dict)
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-source-export/v1")

    def __post_init__(self) -> None:
        for name in (
            "source_family",
            "dataset",
            "layer",
            "publisher_release",
            "effective_date",
            "licence",
            "format",
        ):
            _required_text(getattr(self, name), name)
        object.__setattr__(self, "declared_crs", _crs_identity(self.declared_crs, "declared_crs"))
        _sha256(self.raw_bytes_sha256, "raw_bytes_sha256")
        object.__setattr__(self, "provenance", _freeze_mapping(self.provenance, "provenance"))
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("source export fingerprint is stale or collides with its payload")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        """Return the immutable Source Export identity, excluding provenance."""

        return {
            "contract": self.contract,
            "source_family": self.source_family,
            "dataset": self.dataset,
            "layer": self.layer,
            "publisher_release": self.publisher_release,
            "effective_date": self.effective_date,
            "licence": self.licence,
            "format": self.format,
            "declared_crs": self.declared_crs,
            "raw_bytes_sha256": self.raw_bytes_sha256,
        }


@dataclass(frozen=True)
class IngestionContract:
    """The versioned normalisation contract for one governed source layer."""

    source_layer: str
    contract_version: str
    accepted_schema: Mapping[str, object]
    stable_feature_key_policy: str
    selected_attributes: tuple[str, ...]
    normalisation: Mapping[str, object]
    crs_transform: Mapping[str, object]
    partition_scheme: str
    spatial_predicate: str
    implementation_dependency_fingerprint: str
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-ingestion-contract/v1")

    def __post_init__(self) -> None:
        for name in (
            "source_layer",
            "contract_version",
            "stable_feature_key_policy",
            "partition_scheme",
            "spatial_predicate",
        ):
            _required_text(getattr(self, name), name)
        object.__setattr__(
            self,
            "selected_attributes",
            _sorted_text_set(self.selected_attributes, "selected_attributes"),
        )
        object.__setattr__(
            self, "accepted_schema", _freeze_mapping(self.accepted_schema, "accepted_schema")
        )
        object.__setattr__(
            self, "normalisation", _freeze_mapping(self.normalisation, "normalisation")
        )
        transform = _freeze_mapping(self.crs_transform, "crs_transform")
        if set(transform) != {"source_crs", "target_crs", "axis_order"}:
            raise ValueError("crs_transform requires source_crs, target_crs and axis_order exactly")
        if transform["axis_order"] != "always_xy":
            raise ValueError("crs_transform axis_order must be always_xy")
        target_crs = _crs_identity(transform["target_crs"], "crs_transform target_crs")
        if target_crs != "EPSG:27700":
            raise ValueError("crs_transform target_crs must be EPSG:27700")
        object.__setattr__(
            self,
            "crs_transform",
            MappingProxyType(
                {
                    "source_crs": _crs_identity(
                        transform["source_crs"], "crs_transform source_crs"
                    ),
                    "target_crs": target_crs,
                    "axis_order": "always_xy",
                }
            ),
        )
        _sha256(
            self.implementation_dependency_fingerprint,
            "implementation_dependency_fingerprint",
        )
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("ingestion contract fingerprint is stale or collides with its payload")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        """Return the complete ingestion identity, including its code dependency."""

        return {
            "contract": self.contract,
            "source_layer": self.source_layer,
            "contract_version": self.contract_version,
            "accepted_schema": dict(self.accepted_schema),
            "stable_feature_key_policy": self.stable_feature_key_policy,
            "selected_attributes": list(self.selected_attributes),
            "normalisation": dict(self.normalisation),
            "crs_transform": dict(self.crs_transform),
            "partition_scheme": self.partition_scheme,
            "spatial_predicate": self.spatial_predicate,
            "implementation_dependency_fingerprint": self.implementation_dependency_fingerprint,
        }


@dataclass(frozen=True)
class EvidencePartitionKey:
    """The v1 BNG spatial address for one source-layer evidence partition."""

    source_layer: str
    partition_scheme: str
    cell: str
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-evidence-partition-key/v1")

    def __post_init__(self) -> None:
        _required_text(self.source_layer, "source_layer")
        if self.partition_scheme != "bng-10km/v1":
            raise ValueError("evidence partition v1 requires partition_scheme bng-10km/v1")
        if not isinstance(self.cell, str) or _BNG_10KM_CELL.fullmatch(self.cell) is None:
            raise ValueError("evidence partition cell must be an uppercase BNG 10km cell")
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError(
                "evidence partition key fingerprint is stale or collides with its payload"
            )
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        """Return the address identity, never a council or request identifier."""

        return {
            "contract": self.contract,
            "source_layer": self.source_layer,
            "partition_scheme": self.partition_scheme,
            "cell": self.cell,
        }


def _canonical_feature_content(
    feature: Mapping[str, object],
) -> tuple[Mapping[str, object], str, str | None]:
    frozen = _freeze_mapping(feature, "feature content")
    forbidden = {"fid", "row_id", "row_number", "row_index", "row_order"}

    def reject_forbidden(value: object) -> None:
        if isinstance(value, Mapping):
            if forbidden & {key.lower() for key in value}:
                raise ValueError("feature content cannot use FID or row-order identity")
            for item in value.values():
                reject_forbidden(item)
        elif isinstance(value, tuple):
            for item in value:
                reject_forbidden(item)

    reject_forbidden(frozen)
    logical_key = frozen.get("logical_key")
    if logical_key is not None:
        if not isinstance(logical_key, str):
            raise ValueError("feature logical_key must be canonical text")
        logical_key = _required_text(logical_key, "feature logical_key")
    geometry_fingerprint = frozen.get("geometry_fingerprint")
    if geometry_fingerprint is not None:
        if not isinstance(geometry_fingerprint, str):
            raise ValueError("feature geometry_fingerprint must be a full SHA-256")
        _sha256(geometry_fingerprint, "feature geometry_fingerprint")
    fingerprint = evidence_fingerprint(
        {"contract": "satn-evidence-feature-content/v1", "feature": frozen}
    )
    return frozen, fingerprint, logical_key


@dataclass(frozen=True)
class EvidencePartitionContent:
    """Normalised feature content for one partition, independent of source rows."""

    partition_key: EvidencePartitionKey
    ingestion_contract: IngestionContract
    features: tuple[Mapping[str, object], ...]
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-evidence-partition-content/v1")
    feature_content_fingerprints: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.partition_key, EvidencePartitionKey):
            raise ValueError("partition content requires an EvidencePartitionKey")
        if not isinstance(self.ingestion_contract, IngestionContract):
            raise ValueError("partition content requires an IngestionContract")
        if self.partition_key.source_layer != self.ingestion_contract.source_layer:
            raise ValueError("partition key and ingestion contract source_layer differ")
        if self.partition_key.partition_scheme != self.ingestion_contract.partition_scheme:
            raise ValueError("partition key and ingestion contract partition_scheme differ")
        canonical_features: list[tuple[str, Mapping[str, object], str]] = []
        payloads_by_fingerprint: dict[str, str] = {}
        logical_keys: set[str] = set()
        for feature in self.features:
            if not isinstance(feature, Mapping):
                raise ValueError("partition feature content must be a mapping")
            frozen, feature_fingerprint, logical_key = _canonical_feature_content(feature)
            payload = canonical_evidence_json(frozen)
            prior = payloads_by_fingerprint.get(feature_fingerprint)
            if prior is not None:
                if prior != payload:
                    raise ValueError("feature content digest collision")
                raise ValueError("partition feature content cannot contain duplicate values")
            payloads_by_fingerprint[feature_fingerprint] = payload
            if logical_key is not None:
                if logical_key in logical_keys:
                    raise ValueError("partition feature logical_key cannot contain duplicates")
                logical_keys.add(logical_key)
            canonical_features.append((payload, frozen, feature_fingerprint))
        canonical_features.sort(key=lambda item: item[0])
        object.__setattr__(
            self,
            "features",
            tuple(item[1] for item in canonical_features),
        )
        object.__setattr__(
            self,
            "feature_content_fingerprints",
            tuple(item[2] for item in canonical_features),
        )
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("partition content fingerprint is stale or collides with its payload")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        """Return sorted normalised content, never storage row identity."""

        return {
            "contract": self.contract,
            "partition_key": self.partition_key.canonical_payload(),
            "ingestion_contract_fingerprint": self.ingestion_contract.fingerprint,
            "feature_content_fingerprints": list(self.feature_content_fingerprints),
            "feature_count": len(self.feature_content_fingerprints),
        }


@dataclass(frozen=True)
class EvidencePartitionAttestation:
    """Proof that exact partition content came from one governed Source Export."""

    partition_content: EvidencePartitionContent
    source_export: SourceExport
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-evidence-partition-attestation/v1")

    def __post_init__(self) -> None:
        if not isinstance(self.partition_content, EvidencePartitionContent):
            raise ValueError("partition attestation requires an EvidencePartitionContent")
        if not isinstance(self.source_export, SourceExport):
            raise ValueError("partition attestation requires a SourceExport")
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError(
                "partition attestation fingerprint is stale or collides with its payload"
            )
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        """Return the complete source, content and contract provenance tuple."""

        content = self.partition_content
        return {
            "contract": self.contract,
            "partition_key": content.partition_key.canonical_payload(),
            "ingestion_contract_fingerprint": content.ingestion_contract.fingerprint,
            "partition_content_fingerprint": content.fingerprint,
            "source_export_fingerprint": self.source_export.fingerprint,
        }


def _sorted_unique_records(
    records: tuple[object, ...], record_type: type[object], name: str
) -> tuple[object, ...]:
    """Sort a declared record set and reject duplicate or colliding identities."""

    payloads_by_fingerprint: dict[str, str] = {}
    ordered: list[tuple[str, object]] = []
    for record in records:
        if not isinstance(record, record_type):
            raise ValueError(f"{name} must contain {record_type.__name__} records")
        fingerprint = record.fingerprint  # type: ignore[attr-defined]
        payload = canonical_evidence_json(record.canonical_payload())  # type: ignore[attr-defined]
        prior = payloads_by_fingerprint.get(fingerprint)
        if prior is not None:
            if prior != payload:
                raise ValueError(f"{name} digest collision")
            raise ValueError(f"{name} cannot contain duplicate records")
        payloads_by_fingerprint[fingerprint] = payload
        ordered.append((fingerprint, record))
    return tuple(record for _, record in sorted(ordered))


@dataclass(frozen=True)
class EvidenceCoverage:
    """The immutable present/missing state of a requested partition set."""

    attestations: tuple[EvidencePartitionAttestation, ...]
    requested_partition_keys: tuple[EvidencePartitionKey, ...] = ()
    state: str = "complete"
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-evidence-coverage/v1")
    missing_partition_keys: tuple[EvidencePartitionKey, ...] = field(init=False)

    def __post_init__(self) -> None:
        attestations = _sorted_unique_records(
            self.attestations,
            EvidencePartitionAttestation,
            "coverage attestations",
        )
        assert all(isinstance(item, EvidencePartitionAttestation) for item in attestations)
        ordered_attestations = tuple(attestations)
        present_keys = tuple(item.partition_content.partition_key for item in ordered_attestations)
        present_key_fingerprints = [key.fingerprint for key in present_keys]
        if len(set(present_key_fingerprints)) != len(present_key_fingerprints):
            raise ValueError("coverage cannot contain duplicate partition keys")
        supplied_keys = _sorted_unique_records(
            self.requested_partition_keys,
            EvidencePartitionKey,
            "requested partition keys",
        )
        assert all(isinstance(item, EvidencePartitionKey) for item in supplied_keys)
        requested = (
            tuple(supplied_keys)
            if supplied_keys
            else tuple(sorted(present_keys, key=lambda x: x.fingerprint))
        )
        requested_fingerprints = {key.fingerprint for key in requested}
        if not set(present_key_fingerprints) <= requested_fingerprints:
            raise ValueError("coverage attestations must be within requested partition keys")
        missing = tuple(key for key in requested if key.fingerprint not in present_key_fingerprints)
        if self.state not in {"complete", "partial"}:
            raise ValueError("coverage state must be complete or partial")
        if self.state == "complete" and missing:
            raise ValueError("complete coverage cannot have missing partition keys")
        if self.state == "partial" and not missing:
            raise ValueError("partial coverage requires missing partition keys")
        object.__setattr__(self, "attestations", ordered_attestations)
        object.__setattr__(self, "requested_partition_keys", requested)
        object.__setattr__(self, "missing_partition_keys", missing)
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("evidence coverage fingerprint is stale or collides with its payload")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        """Return a deterministic set of exact attestations and coverage state."""

        return {
            "contract": self.contract,
            "attestation_fingerprints": [item.fingerprint for item in self.attestations],
            "requested_partition_key_fingerprints": [
                item.fingerprint for item in self.requested_partition_keys
            ],
            "state": self.state,
        }


@dataclass(frozen=True)
class ScenarioConfiguration:
    """Frozen data-only configuration for a Scenario Compilation input."""

    area_definition_fingerprint: str
    criteria_set_fingerprint: str
    network_selection_profile_fingerprint: str
    data_choices: Mapping[str, object]
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-scenario-configuration/v1")

    def __post_init__(self) -> None:
        for name in (
            "area_definition_fingerprint",
            "criteria_set_fingerprint",
            "network_selection_profile_fingerprint",
        ):
            _sha256(getattr(self, name), name)
        object.__setattr__(self, "data_choices", _freeze_mapping(self.data_choices, "data_choices"))
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError(
                "scenario configuration fingerprint is stale or collides with its payload"
            )
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        """Return data choices only; decisions and Local Evidence Store state are separate."""

        return {
            "contract": self.contract,
            "area_definition_fingerprint": self.area_definition_fingerprint,
            "criteria_set_fingerprint": self.criteria_set_fingerprint,
            "network_selection_profile_fingerprint": self.network_selection_profile_fingerprint,
            "data_choices": dict(self.data_choices),
        }


@dataclass(frozen=True)
class BaseUnitParameter:
    """One integer parameter expressed in an explicitly named base unit."""

    name: str
    value: int
    unit: str

    def __post_init__(self) -> None:
        _required_text(self.name, "parameter name")
        _required_text(self.unit, "parameter unit")
        if type(self.value) is not int:
            raise ValueError("base-unit parameter value must be an integer")

    def canonical_payload(self) -> dict[str, object]:
        """Return the integer/value-unit pair used by enrichment identity."""

        return {"name": self.name, "value": self.value, "unit": self.unit}


@dataclass(frozen=True)
class EdgeEnrichmentParameters:
    """A deterministic set of data-only, base-unit enrichment parameters."""

    parameters: tuple[BaseUnitParameter, ...]
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-edge-enrichment-parameters/v1")

    def __post_init__(self) -> None:
        names: set[str] = set()
        items: list[tuple[str, BaseUnitParameter]] = []
        for parameter in self.parameters:
            if not isinstance(parameter, BaseUnitParameter):
                raise ValueError(
                    "edge enrichment parameters must contain BaseUnitParameter records"
                )
            if parameter.name in names:
                raise ValueError("edge enrichment parameters cannot contain duplicate names")
            names.add(parameter.name)
            items.append((canonical_evidence_json(parameter.canonical_payload()), parameter))
        items.sort(key=lambda item: item[0])
        object.__setattr__(self, "parameters", tuple(item[1] for item in items))
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError(
                "edge enrichment parameters fingerprint is stale or collides with its payload"
            )
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        """Return the versioned parameter-set identity."""

        return {
            "contract": self.contract,
            "parameters": [parameter.canonical_payload() for parameter in self.parameters],
        }


@dataclass(frozen=True)
class EdgeEnrichmentHeader:
    """Common dependency header shared by every reusable Edge Enrichment result."""

    stable_edge_id: str
    geometry_fingerprint: str
    partition_attestations: tuple[EvidencePartitionAttestation, ...]
    algorithm_id: str
    algorithm_contract: str
    implementation_dependency_fingerprint: str
    parameters: EdgeEnrichmentParameters
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-edge-enrichment/v1")

    def __post_init__(self) -> None:
        if (
            not isinstance(self.stable_edge_id, str)
            or re.fullmatch(r"edge:v1:[0-9a-f]{64}", self.stable_edge_id) is None
        ):
            raise ValueError("stable_edge_id must be edge:v1:<full-sha256>")
        _sha256(self.geometry_fingerprint, "geometry_fingerprint")
        _required_text(self.algorithm_id, "algorithm_id")
        _required_text(self.algorithm_contract, "algorithm_contract")
        _sha256(
            self.implementation_dependency_fingerprint,
            "implementation_dependency_fingerprint",
        )
        if not isinstance(self.parameters, EdgeEnrichmentParameters):
            raise ValueError("edge enrichment header requires EdgeEnrichmentParameters")
        attestations = _sorted_unique_records(
            self.partition_attestations,
            EvidencePartitionAttestation,
            "edge enrichment partition attestations",
        )
        if not attestations:
            raise ValueError("edge enrichment header requires partition attestations")
        object.__setattr__(self, "partition_attestations", tuple(attestations))
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError(
                "edge enrichment header fingerprint is stale or collides with its payload"
            )
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        """Return every algorithm, geometry, parameter and evidence dependency."""

        return {
            "contract": self.contract,
            "stable_edge_id": self.stable_edge_id,
            "geometry_fingerprint": self.geometry_fingerprint,
            "partition_attestation_fingerprints": [
                item.fingerprint for item in self.partition_attestations
            ],
            "algorithm": {
                "id": self.algorithm_id,
                "contract": self.algorithm_contract,
                "implementation_dependency_fingerprint": self.implementation_dependency_fingerprint,
            },
            "parameters_fingerprint": self.parameters.fingerprint,
        }
