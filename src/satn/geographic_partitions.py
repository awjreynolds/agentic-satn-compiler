"""Deterministic geographic compilation partition contracts.

This module contains the small, serialisable seam used by future partitioned
compilation.  It deliberately does not start workers or stitch results.  Grid
cells and worker outputs are semantic; execution bundles are scheduling hints
and are therefore excluded from partition artifact identities.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from itertools import combinations
from types import MappingProxyType
from typing import Literal

from shapely.geometry import MultiPoint, Point, box
from shapely.geometry.base import BaseGeometry

from satn.evidence_contracts import EvidencePartitionKey, canonical_evidence_geometry

EPSG_27700 = "EPSG:27700"
COMPILATION_PARTITION_SCHEME = "bng-10km/v1"
COMPILATION_PARTITION_CONTRACT = "satn-compilation-partition/v1"
HALO_CONTRACT = "satn-compilation-halo/v1"
PORTAL_CONTRACT = "satn-boundary-portal/v1"
PARTITION_ARTIFACT_CONTRACT = "satn-partition-artifact/v1"
FEATURE_FRAGMENT_CONTRACT = "satn-owned-fragment/v1"
HALO_REFERENCE_CONTRACT = "satn-halo-reference/v1"
CANDIDATE_FRAGMENT_CONTRACT = "satn-candidate-fragment/v1"
DIAGNOSTIC_CONTRACT = "satn-partition-diagnostic/v1"
GAP_CONTRACT = "satn-partition-gap/v1"
HALO_REQUEST_CONTRACT = "satn-halo-request/v1"

PortalKind = Literal["real-node", "boundary-intersection"]

_BNG_500KM_ORIGINS = {
    "S": (0, 0),
    "T": (500_000, 0),
    "N": (0, 500_000),
    "O": (500_000, 500_000),
    "H": (0, 1_000_000),
}
_BNG_100KM_LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"


def canonical_json(value: object) -> str:
    """Return the repository's deterministic compact JSON representation."""

    return json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def content_fingerprint(value: object) -> str:
    """Hash canonical JSON bytes with the full SHA-256 digest."""

    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("partition identities cannot contain non-finite floats")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("partition identity mapping keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, BaseGeometry):
        return canonical_evidence_geometry(value, EPSG_27700)
    if hasattr(value, "canonical_payload"):
        return _json_value(value.canonical_payload())  # type: ignore[no-any-return]
    raise ValueError(f"unsupported partition identity value: {type(value).__name__}")


def _freeze(value: object) -> object:
    """Recursively freeze JSON-like metadata for safe dataclass storage."""

    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("metadata mapping keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in sorted(value.items())})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("metadata cannot contain non-finite floats")
    if isinstance(value, (str, int, bool, float)) or value is None:
        return value
    raise ValueError(f"unsupported metadata value: {type(value).__name__}")


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def _sha256_text(value: str, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{name} must be a lowercase full SHA-256")
    return value


def _quantised_coordinate(point: Point) -> tuple[int, int]:
    payload = canonical_evidence_geometry(point, EPSG_27700)
    coordinates = payload["geometry"]["coordinates"]  # type: ignore[index]
    return int(coordinates[0]), int(coordinates[1])


def _canonical_mm_coordinate(value: object, name: str) -> tuple[int, int]:
    """Validate an already-quantised millimetre coordinate without coercion."""

    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError(f"{name} must contain exactly two integer coordinates")
    if any(not isinstance(item, int) or isinstance(item, bool) for item in value):
        raise ValueError(f"{name} must contain integer millimetre coordinates")
    return int(value[0]), int(value[1])


def _finite_radius(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} radius must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} radius must be a finite number")
    if result < 0:
        raise ValueError(f"{name} radius cannot be negative")
    return result


def _canonical_text_tuple(values: object, name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must contain canonical text values")
    try:
        items = tuple(values)  # type: ignore[arg-type]
    except TypeError as error:
        raise ValueError(f"{name} must contain canonical text values") from error
    return tuple(sorted({_text(item, name) for item in items}))


def _cell_bounds(cell: str) -> tuple[float, float, float, float]:
    """Return the EPSG:27700 metre bounds for a validated 10 km BNG cell."""

    first, second = cell[:2]
    origin_x, origin_y = _BNG_500KM_ORIGINS[first]
    letter_index = _BNG_100KM_LETTERS.index(second)
    easting = origin_x + (letter_index % 5) * 100_000 + int(cell[2]) * 10_000
    northing = origin_y + (4 - letter_index // 5) * 100_000 + int(cell[3]) * 10_000
    return float(easting), float(northing), float(easting + 10_000), float(northing + 10_000)


def _normalise_partition(value: CompilationPartition | str) -> CompilationPartition:
    return value if isinstance(value, CompilationPartition) else CompilationPartition(value)


def _ordered_cells(
    values: Iterable[CompilationPartition | str],
) -> tuple[CompilationPartition, ...]:
    partitions = tuple(_normalise_partition(value) for value in values)
    by_cell = {partition.cell: partition for partition in partitions}
    if len(by_cell) != len(partitions):
        raise ValueError("partition cells cannot be duplicated")
    return tuple(by_cell[cell] for cell in sorted(by_cell))


@dataclass(frozen=True, slots=True)
class CompilationPartition:
    """Stable semantic address for one British National Grid 10 km cell."""

    cell: str
    partition_scheme: str = COMPILATION_PARTITION_SCHEME
    crs: str = EPSG_27700
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default=COMPILATION_PARTITION_CONTRACT)

    def __post_init__(self) -> None:
        _text(self.cell, "cell")
        if self.partition_scheme != COMPILATION_PARTITION_SCHEME:
            raise ValueError("compilation partitions require bng-10km/v1")
        if self.crs != EPSG_27700:
            raise ValueError("compilation partitions require explicit EPSG:27700")
        # Reuse the public evidence grid validator; it is the authority for BNG validity.
        EvidencePartitionKey("compilation", self.partition_scheme, self.cell)
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    @property
    def cell_id(self) -> str:
        return self.cell

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return _cell_bounds(self.cell)

    @property
    def geometry(self) -> BaseGeometry:
        return box(*self.bounds)

    @property
    def polygon(self) -> BaseGeometry:
        return self.geometry

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "partition_scheme": self.partition_scheme,
            "cell": self.cell,
            "crs": self.crs,
        }


@dataclass(frozen=True, slots=True)
class PartitionHalo:
    """Versioned, read-only metric halo around one core partition."""

    partition: CompilationPartition
    radius_m: float
    contract_version: str = "v1"
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default=HALO_CONTRACT)

    def __post_init__(self) -> None:
        if not isinstance(self.partition, CompilationPartition):
            raise ValueError("halo requires a CompilationPartition")
        if self.contract_version != "v1":
            raise ValueError("only halo contract version v1 is supported")
        if not isinstance(self.radius_m, (int, float)) or not math.isfinite(float(self.radius_m)):
            raise ValueError("halo radius must be finite")
        if float(self.radius_m) < 0:
            raise ValueError("halo radius cannot be negative")
        object.__setattr__(self, "radius_m", float(self.radius_m))
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    @property
    def core_cell(self) -> str:
        return self.partition.cell

    @property
    def coverage(self) -> BaseGeometry:
        return self.partition.geometry.buffer(self.radius_m)

    def covers(self, geometry: BaseGeometry) -> bool:
        if not isinstance(geometry, BaseGeometry):
            raise TypeError("halo coverage requires a Shapely geometry")
        return bool(self.coverage.intersects(geometry))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "contract_version": self.contract_version,
            "partition": self.partition.canonical_payload(),
            "radius_m": self.radius_m,
        }


# A short alias keeps call sites readable while retaining the explicit public name.
Halo = PartitionHalo


@dataclass(frozen=True, slots=True)
class PartitionFeature:
    """One whole logical source feature, never a clipped worker fragment."""

    feature_id: str
    geometry: BaseGeometry
    properties: Mapping[str, object] = field(default_factory=dict)
    crs: str = EPSG_27700
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default="satn-partition-feature/v1")

    def __post_init__(self) -> None:
        _text(self.feature_id, "feature_id")
        if not isinstance(self.geometry, BaseGeometry) or self.geometry.is_empty:
            raise ValueError("partition feature geometry must be a nonempty Shapely geometry")
        if self.crs != EPSG_27700:
            raise ValueError("partition feature geometry requires explicit EPSG:27700")
        if not isinstance(self.properties, Mapping):
            raise ValueError("feature properties must be a mapping")
        frozen = _freeze(self.properties)
        assert isinstance(frozen, Mapping)
        object.__setattr__(self, "properties", frozen)
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    @property
    def geometry_fingerprint(self) -> str:
        return content_fingerprint(canonical_evidence_geometry(self.geometry, self.crs))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "feature_id": self.feature_id,
            "crs": self.crs,
            "geometry": canonical_evidence_geometry(self.geometry, self.crs),
            "properties": dict(self.properties),
        }


def _feature_geometry(feature: PartitionFeature | BaseGeometry) -> BaseGeometry:
    return feature.geometry if isinstance(feature, PartitionFeature) else feature


def intersecting_core_cells(
    feature: PartitionFeature | BaseGeometry,
    partitions: Iterable[CompilationPartition | str],
) -> tuple[str, ...]:
    """Return sorted core cells whose exact polygons intersect a feature."""

    geometry = _feature_geometry(feature)
    if not isinstance(geometry, BaseGeometry):
        raise TypeError("feature must contain a Shapely geometry")
    return tuple(
        partition.cell
        for partition in _ordered_cells(partitions)
        if geometry.intersects(partition.geometry)
    )


def deterministic_feature_owner(
    feature: PartitionFeature | BaseGeometry,
    partitions: Iterable[CompilationPartition | str],
) -> str:
    """Return the lexicographically smallest intersecting core cell."""

    cells = intersecting_core_cells(feature, partitions)
    if not cells:
        raise ValueError("feature does not intersect any supplied core partition")
    return cells[0]


feature_owner = deterministic_feature_owner


def _shared_boundary(left: CompilationPartition, right: CompilationPartition) -> BaseGeometry:
    if left.cell == right.cell:
        raise ValueError("a boundary portal requires two distinct cells")
    shared = left.geometry.boundary.intersection(right.geometry.boundary)
    if shared.is_empty or shared.length == 0:
        raise ValueError(f"cells {left.cell} and {right.cell} do not share a boundary")
    return shared


def _canonical_cell_pair(
    left: CompilationPartition | str, right: CompilationPartition | str
) -> tuple[CompilationPartition, CompilationPartition]:
    partitions = _ordered_cells((left, right))
    if len(partitions) != 2:
        raise ValueError("portal requires two distinct cells")
    return partitions


@dataclass(frozen=True, slots=True)
class BoundaryPortal:
    """Explicit governed interface; never a proximity or visual crossing."""

    left_cell: str
    right_cell: str
    kind: PortalKind
    node_id: str | None = None
    node_coordinate: tuple[int, int] | None = None
    intersection_coordinate: tuple[int, int] | None = None
    feature_id: str | None = None
    incident_feature_ids: tuple[str, ...] = ()
    permitted_directions: tuple[str, ...] = ()
    provenance: Mapping[str, object] = field(default_factory=dict)
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default=PORTAL_CONTRACT)

    def __post_init__(self) -> None:
        left, right = _canonical_cell_pair(self.left_cell, self.right_cell)
        object.__setattr__(self, "left_cell", left.cell)
        object.__setattr__(self, "right_cell", right.cell)
        shared = _shared_boundary(left, right)
        if self.node_id is not None:
            _text(self.node_id, "node_id")
        if self.feature_id is not None:
            _text(self.feature_id, "feature_id")
        if self.kind not in ("real-node", "boundary-intersection"):
            raise ValueError("portal kind must be real-node or boundary-intersection")
        if self.kind == "real-node":
            if not self.node_id or self.intersection_coordinate is not None:
                raise ValueError("real-node portals require node_id and no boundary coordinate")
            if self.node_coordinate is None:
                raise ValueError("real-node portals require a canonical node coordinate")
            object.__setattr__(
                self,
                "node_coordinate",
                _canonical_mm_coordinate(self.node_coordinate, "node_coordinate"),
            )
            if not shared.covers(
                Point(self.node_coordinate[0] / 1000, self.node_coordinate[1] / 1000)
            ):
                raise ValueError(
                    "real-node coordinate must lie exactly on the shared cell boundary"
                )
        else:
            if (
                self.node_id is not None
                or self.node_coordinate is not None
                or self.intersection_coordinate is None
            ):
                raise ValueError(
                    "boundary-intersection portals require a coordinate and no node_id"
                )
            object.__setattr__(
                self,
                "intersection_coordinate",
                _canonical_mm_coordinate(self.intersection_coordinate, "intersection_coordinate"),
            )
            if not shared.covers(
                Point(
                    self.intersection_coordinate[0] / 1000,
                    self.intersection_coordinate[1] / 1000,
                )
            ):
                raise ValueError(
                    "boundary intersection coordinate must lie exactly on shared cell boundary"
                )
        object.__setattr__(
            self,
            "incident_feature_ids",
            _canonical_text_tuple(self.incident_feature_ids, "incident_feature_ids"),
        )
        object.__setattr__(
            self,
            "permitted_directions",
            _canonical_text_tuple(self.permitted_directions, "permitted_directions"),
        )
        frozen = _freeze(self.provenance)
        assert isinstance(frozen, Mapping)
        object.__setattr__(self, "provenance", frozen)
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    @classmethod
    def real_node(
        cls,
        left: CompilationPartition | str,
        right: CompilationPartition | str,
        *,
        node_id: str,
        point: Point,
        incident_feature_ids: Iterable[str] = (),
        permitted_directions: Iterable[str] = (),
        provenance: Mapping[str, object] | None = None,
    ) -> BoundaryPortal:
        left_partition, right_partition = _canonical_cell_pair(left, right)
        shared = _shared_boundary(left_partition, right_partition)
        if not isinstance(point, Point) or point.is_empty or not shared.covers(point):
            raise ValueError("real-node portal point must lie exactly on the shared cell boundary")
        return cls(
            left_partition.cell,
            right_partition.cell,
            "real-node",
            node_id=_text(node_id, "node_id"),
            node_coordinate=_quantised_coordinate(point),
            incident_feature_ids=tuple(incident_feature_ids),
            permitted_directions=tuple(permitted_directions),
            provenance=provenance or {},
        )

    @classmethod
    def boundary_intersection(
        cls,
        left: CompilationPartition | str,
        right: CompilationPartition | str,
        *,
        point: Point,
        feature_id: str | None = None,
        incident_feature_ids: Iterable[str] = (),
        permitted_directions: Iterable[str] = (),
        provenance: Mapping[str, object] | None = None,
    ) -> BoundaryPortal:
        left_partition, right_partition = _canonical_cell_pair(left, right)
        shared = _shared_boundary(left_partition, right_partition)
        if not isinstance(point, Point) or point.is_empty or not shared.covers(point):
            raise ValueError("boundary intersection point must lie exactly on shared boundary")
        return cls(
            left_partition.cell,
            right_partition.cell,
            "boundary-intersection",
            intersection_coordinate=_quantised_coordinate(point),
            feature_id=feature_id,
            incident_feature_ids=tuple(incident_feature_ids)
            + ((feature_id,) if feature_id else ()),
            permitted_directions=tuple(permitted_directions),
            provenance=provenance or {},
        )

    @property
    def portal_id(self) -> str:
        return self.fingerprint

    @property
    def portal_type(self) -> PortalKind:
        return self.kind

    @property
    def canonical_boundary_coordinate(self) -> tuple[int, int] | None:
        return self.intersection_coordinate

    @property
    def coordinate(self) -> tuple[int, int]:
        """Return the canonical millimetre coordinate for either portal kind."""

        value = self.node_coordinate if self.kind == "real-node" else self.intersection_coordinate
        assert value is not None
        return value

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "left_cell": self.left_cell,
            "right_cell": self.right_cell,
            "kind": self.kind,
            "node_id": self.node_id,
            "node_coordinate": self.node_coordinate,
            "intersection_coordinate": self.intersection_coordinate,
            "feature_id": self.feature_id,
            "incident_feature_ids": list(self.incident_feature_ids),
            "permitted_directions": list(self.permitted_directions),
            "provenance": dict(self.provenance),
        }


def _intersection_points(geometry: BaseGeometry, boundary: BaseGeometry) -> tuple[Point, ...]:
    intersection = geometry.intersection(boundary)
    if intersection.is_empty:
        return ()
    if isinstance(intersection, Point):
        return (intersection,)
    if isinstance(intersection, MultiPoint):
        return tuple(intersection.geoms)
    # A line lying along a border has no canonical crossing point.  Do not infer
    # one from proximity or a representative point.
    return ()


def boundary_intersection_portals(
    feature: PartitionFeature,
    partitions: Iterable[CompilationPartition | str],
) -> tuple[BoundaryPortal, ...]:
    """Derive exact cell-boundary portals for a line crossing, if any."""

    cells = _ordered_cells(partitions)
    if not isinstance(feature, PartitionFeature):
        raise TypeError("boundary portals require a PartitionFeature")
    portals: dict[str, BoundaryPortal] = {}
    for left, right in combinations(cells, 2):
        try:
            shared = _shared_boundary(left, right)
        except ValueError:
            continue
        for point in _intersection_points(feature.geometry, shared):
            portal = BoundaryPortal.boundary_intersection(
                left,
                right,
                point=point,
                feature_id=feature.feature_id,
                provenance={"feature_fingerprint": feature.fingerprint},
            )
            portals[portal.fingerprint] = portal
    return tuple(portals[key] for key in sorted(portals))


@dataclass(frozen=True, slots=True)
class OwnedFeatureFragment:
    """Authoritative whole-feature fragment emitted by its deterministic owner."""

    feature_id: str
    owner_cell: str
    geometry: BaseGeometry
    content_fingerprint: str
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default=FEATURE_FRAGMENT_CONTRACT)

    def __post_init__(self) -> None:
        _text(self.feature_id, "feature_id")
        CompilationPartition(self.owner_cell)
        if not isinstance(self.geometry, BaseGeometry) or self.geometry.is_empty:
            raise ValueError("owned fragment geometry must be nonempty")
        _sha256_text(self.content_fingerprint, "content_fingerprint")
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "feature_id": self.feature_id,
            "owner_cell": self.owner_cell,
            "geometry": canonical_evidence_geometry(self.geometry, EPSG_27700),
            "content_fingerprint": self.content_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class HaloReference:
    """Read-only reference to a feature owned by another core cell."""

    feature_id: str
    owner_cell: str
    content_fingerprint: str
    source_cell: str
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default=HALO_REFERENCE_CONTRACT)

    def __post_init__(self) -> None:
        _text(self.feature_id, "feature_id")
        CompilationPartition(self.owner_cell)
        CompilationPartition(self.source_cell)
        if self.owner_cell == self.source_cell:
            raise ValueError("a halo reference owner must differ from source core cell")
        _sha256_text(self.content_fingerprint, "content_fingerprint")
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "feature_id": self.feature_id,
            "owner_cell": self.owner_cell,
            "source_cell": self.source_cell,
            "content_fingerprint": self.content_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class CandidateFragment:
    """A deterministic candidate fragment containing references to whole features."""

    candidate_id: str
    feature_ids: tuple[str, ...] = ()
    attributes: Mapping[str, object] = field(default_factory=dict)
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default=CANDIDATE_FRAGMENT_CONTRACT)

    def __post_init__(self) -> None:
        _text(self.candidate_id, "candidate_id")
        ids = tuple(sorted({_text(item, "feature_id") for item in self.feature_ids}))
        object.__setattr__(self, "feature_ids", ids)
        frozen = _freeze(self.attributes)
        assert isinstance(frozen, Mapping)
        object.__setattr__(self, "attributes", frozen)
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "candidate_id": self.candidate_id,
            "feature_ids": list(self.feature_ids),
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class PartitionDiagnostic:
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default=DIAGNOSTIC_CONTRACT)

    def __post_init__(self) -> None:
        _text(self.code, "diagnostic code")
        _text(self.message, "diagnostic message")
        if self.severity not in ("info", "warning", "error"):
            raise ValueError("diagnostic severity must be info, warning, or error")
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class HaloRequest:
    partition_cell: str
    available_radius_m: float
    required_radius_m: float
    reason: str = "operation support exceeds declared halo"
    request_id: str = field(init=False)
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default=HALO_REQUEST_CONTRACT)

    def __post_init__(self) -> None:
        CompilationPartition(self.partition_cell)
        available = _finite_radius(self.available_radius_m, "available")
        required = _finite_radius(self.required_radius_m, "required")
        if required <= available:
            raise ValueError("halo request requires a larger positive radius")
        object.__setattr__(self, "available_radius_m", available)
        object.__setattr__(self, "required_radius_m", required)
        _text(self.reason, "halo request reason")
        payload = self.canonical_payload()
        digest = content_fingerprint(payload)
        object.__setattr__(self, "fingerprint", digest)
        object.__setattr__(self, "request_id", f"halo-request-{digest[:16]}")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "partition_cell": self.partition_cell,
            "available_radius_m": float(self.available_radius_m),
            "required_radius_m": float(self.required_radius_m),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PartitionGap:
    kind: str
    message: str
    missing_cells: tuple[str, ...] = ()
    evidence_request_id: str | None = None
    halo_request: HaloRequest | None = None
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default=GAP_CONTRACT)

    def __post_init__(self) -> None:
        _text(self.kind, "gap kind")
        _text(self.message, "gap message")
        cells = tuple(sorted({_text(item, "missing cell") for item in self.missing_cells}))
        for cell in cells:
            CompilationPartition(cell)
        object.__setattr__(self, "missing_cells", cells)
        if self.evidence_request_id is not None:
            _text(self.evidence_request_id, "evidence_request_id")
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    @classmethod
    def missing_optional_boundary_evidence(
        cls, missing_cells: Iterable[str], boundary_id: str
    ) -> PartitionGap:
        cells = tuple(sorted(set(missing_cells)))
        request = content_fingerprint(
            {
                "kind": "missing-optional-boundary-evidence",
                "missing_cells": cells,
                "boundary_id": boundary_id,
            }
        )
        return cls(
            "missing-optional-boundary-evidence",
            f"Optional boundary evidence is unavailable for {boundary_id}",
            cells,
            evidence_request_id=f"evidence-request-{request[:16]}",
        )

    @classmethod
    def insufficient_halo(
        cls, partition_cell: str, available_radius_m: float, required_radius_m: float
    ) -> PartitionGap:
        request = HaloRequest(partition_cell, available_radius_m, required_radius_m)
        return cls(
            "insufficient-halo",
            (
                f"Halo for {partition_cell} is {available_radius_m:g}m; "
                f"{required_radius_m:g}m is required"
            ),
            missing_cells=(partition_cell,),
            evidence_request_id=request.request_id,
            halo_request=request,
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "kind": self.kind,
            "message": self.message,
            "missing_cells": list(self.missing_cells),
            "evidence_request_id": self.evidence_request_id,
            "halo_request": self.halo_request.canonical_payload() if self.halo_request else None,
        }


@dataclass(frozen=True, slots=True)
class ExecutionBundle:
    """Non-semantic grouping hint for cells scheduled together."""

    label: str
    cells: tuple[CompilationPartition | str, ...]
    worker_count: int = 1
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.label, "execution bundle label")
        cells = _ordered_cells(self.cells)
        if self.worker_count < 1:
            raise ValueError("execution bundle worker_count must be positive")
        object.__setattr__(self, "cells", cells)
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "label": self.label,
            "cells": [cell.cell for cell in self.cells],
            "worker_count": self.worker_count,
        }


@dataclass(frozen=True, slots=True)
class PartitionArtifact:
    """Versioned worker output for one semantic compilation partition."""

    partition: CompilationPartition
    halo: PartitionHalo
    owned_fragments: tuple[OwnedFeatureFragment, ...] = ()
    halo_references: tuple[HaloReference, ...] = ()
    portals: tuple[BoundaryPortal, ...] = ()
    candidate_fragments: tuple[CandidateFragment, ...] = ()
    diagnostics: tuple[PartitionDiagnostic, ...] = ()
    gaps: tuple[PartitionGap, ...] = ()
    # Operational provenance is retained for callers but excluded from identity.
    execution_bundle: ExecutionBundle | None = field(default=None, compare=False, repr=False)
    completion_order: tuple[str, ...] = field(default=(), compare=False, repr=False)
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default=PARTITION_ARTIFACT_CONTRACT)

    def __post_init__(self) -> None:
        if not isinstance(self.partition, CompilationPartition):
            raise ValueError("artifact requires a CompilationPartition")
        if not isinstance(self.halo, PartitionHalo) or self.halo.partition != self.partition:
            raise ValueError("artifact halo must describe the artifact core partition")
        for name in (
            "owned_fragments",
            "halo_references",
            "portals",
            "candidate_fragments",
            "diagnostics",
            "gaps",
        ):
            raw_values = getattr(self, name)
            if not isinstance(raw_values, Iterable):
                raise ValueError(f"{name} must be an iterable collection")
            values = tuple(raw_values)
            object.__setattr__(self, name, values)
        typed_collections = {
            "owned_fragments": OwnedFeatureFragment,
            "halo_references": HaloReference,
            "portals": BoundaryPortal,
            "candidate_fragments": CandidateFragment,
            "diagnostics": PartitionDiagnostic,
            "gaps": PartitionGap,
        }
        for name, expected_type in typed_collections.items():
            if any(not isinstance(value, expected_type) for value in getattr(self, name)):
                raise ValueError(f"{name} must contain {expected_type.__name__} values")
        if self.completion_order:
            object.__setattr__(
                self,
                "completion_order",
                tuple(str(value) for value in self.completion_order),
            )
        owned_ids = [fragment.feature_id for fragment in self.owned_fragments]
        if len(owned_ids) != len(set(owned_ids)):
            raise ValueError("an artifact cannot emit an owned feature twice")
        if any(fragment.owner_cell != self.partition.cell for fragment in self.owned_fragments):
            raise ValueError("owned fragments must belong to the artifact core cell")
        if any(reference.source_cell != self.partition.cell for reference in self.halo_references):
            raise ValueError("halo references must identify this artifact core as source_cell")
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    @classmethod
    def from_features(
        cls,
        partition: CompilationPartition | str,
        halo: PartitionHalo,
        features: Iterable[PartitionFeature],
        core_partitions: Iterable[CompilationPartition | str] | None = None,
        *,
        candidate_fragments: Iterable[CandidateFragment] = (),
        portals: Iterable[BoundaryPortal] = (),
        diagnostics: Iterable[PartitionDiagnostic] = (),
        gaps: Iterable[PartitionGap] = (),
        required_halo_radius_m: float | None = None,
        completion_order: Iterable[str] = (),
        execution_bundle: ExecutionBundle | None = None,
        worker_count: int | None = None,
    ) -> PartitionArtifact:
        """Build an artifact deterministically from whole features.

        ``completion_order`` and ``execution_bundle`` are accepted as operational
        inputs for callers but intentionally do not enter the semantic identity.
        """

        if worker_count is not None and worker_count < 1:
            raise ValueError("worker_count must be positive when supplied")
        core = _normalise_partition(partition)
        if halo.partition != core:
            raise ValueError("halo partition must match artifact partition")
        partitions = _ordered_cells(core_partitions or (core,))
        if core.cell not in {item.cell for item in partitions}:
            raise ValueError("artifact partition must be one of the supplied core partitions")
        ordered_features: dict[str, PartitionFeature] = {}
        for feature in features:
            if not isinstance(feature, PartitionFeature):
                raise TypeError("features must contain PartitionFeature values")
            existing = ordered_features.get(feature.feature_id)
            if existing is not None and existing.fingerprint != feature.fingerprint:
                raise ValueError(f"feature {feature.feature_id} has conflicting content")
            ordered_features[feature.feature_id] = feature

        owned: list[OwnedFeatureFragment] = []
        references: list[HaloReference] = []
        generated_portals: list[BoundaryPortal] = []
        for feature_id in sorted(ordered_features):
            feature = ordered_features[feature_id]
            owner = deterministic_feature_owner(feature, partitions)
            intersects_core = feature.geometry.intersects(core.geometry)
            if owner == core.cell and intersects_core:
                owned.append(
                    OwnedFeatureFragment(
                        feature.feature_id,
                        core.cell,
                        feature.geometry,
                        feature.fingerprint,
                    )
                )
            elif owner != core.cell and halo.covers(feature.geometry):
                references.append(
                    HaloReference(
                        feature.feature_id,
                        owner,
                        feature.fingerprint,
                        core.cell,
                    )
                )
            if intersects_core:
                generated_portals.extend(boundary_intersection_portals(feature, partitions))

        all_gaps = list(gaps)
        if required_halo_radius_m is not None and required_halo_radius_m > halo.radius_m:
            all_gaps.append(
                PartitionGap.insufficient_halo(
                    core.cell,
                    halo.radius_m,
                    float(required_halo_radius_m),
                )
            )
        # Materialise/sort operational values only to make output stable.  The
        # values themselves, not worker completion order, form artifact identity.
        unique_portals = {portal.fingerprint: portal for portal in (*portals, *generated_portals)}
        return cls(
            core,
            halo,
            tuple(sorted(owned, key=lambda value: value.fingerprint)),
            tuple(sorted(references, key=lambda value: value.fingerprint)),
            tuple(unique_portals[key] for key in sorted(unique_portals)),
            tuple(sorted(candidate_fragments, key=lambda value: value.fingerprint)),
            tuple(sorted(diagnostics, key=lambda value: value.fingerprint)),
            tuple(
                sorted(
                    {gap.fingerprint: gap for gap in all_gaps}.values(),
                    key=lambda value: value.fingerprint,
                )
            ),
            execution_bundle=execution_bundle,
            completion_order=tuple(completion_order),
        )

    @property
    def artifact_identity(self) -> str:
        return self.fingerprint

    @property
    def owned_feature_fragments(self) -> tuple[OwnedFeatureFragment, ...]:
        return self.owned_fragments

    @property
    def halo_refs(self) -> tuple[HaloReference, ...]:
        return self.halo_references

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "partition": self.partition.canonical_payload(),
            "halo": self.halo.canonical_payload(),
            "owned_fragments": [value.canonical_payload() for value in self.owned_fragments],
            "halo_references": [value.canonical_payload() for value in self.halo_references],
            "portals": [value.canonical_payload() for value in self.portals],
            "candidate_fragments": [
                value.canonical_payload() for value in self.candidate_fragments
            ],
            "diagnostics": [value.canonical_payload() for value in self.diagnostics],
            "gaps": [value.canonical_payload() for value in self.gaps],
        }


def build_partition_artifact(
    partition: CompilationPartition | str,
    halo: PartitionHalo,
    features: Iterable[PartitionFeature],
    core_partitions: Iterable[CompilationPartition | str] | None = None,
    **kwargs: object,
) -> PartitionArtifact:
    """Functional wrapper around :meth:`PartitionArtifact.from_features`."""

    return PartitionArtifact.from_features(
        partition,
        halo,
        features,
        core_partitions,
        **kwargs,  # type: ignore[arg-type]
    )


# More descriptive aliases make the public seam discoverable without creating
# duplicate schemas.
OwnedFragment = OwnedFeatureFragment
HaloReferenceFragment = HaloReference
Diagnostic = PartitionDiagnostic
FeatureFragment = OwnedFeatureFragment
boundary_portals_for_feature = boundary_intersection_portals


__all__ = [
    "EPSG_27700",
    "BoundaryPortal",
    "CandidateFragment",
    "CompilationPartition",
    "Diagnostic",
    "ExecutionBundle",
    "FeatureFragment",
    "Halo",
    "HaloReference",
    "HaloReferenceFragment",
    "HaloRequest",
    "OwnedFeatureFragment",
    "OwnedFragment",
    "PartitionArtifact",
    "PartitionDiagnostic",
    "PartitionFeature",
    "PartitionGap",
    "PartitionHalo",
    "boundary_intersection_portals",
    "boundary_portals_for_feature",
    "build_partition_artifact",
    "canonical_json",
    "content_fingerprint",
    "deterministic_feature_owner",
    "feature_owner",
    "intersecting_core_cells",
]
