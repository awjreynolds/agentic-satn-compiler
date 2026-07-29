"""Verified local catalogue and offline sampling for governed EA DTM tiles.

Raster payloads remain in the filesystem content cache.  DuckDB stores only
canonical receipt identities and immutable BNG 10 km coverage attestations.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageFile
from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge

from satn.ea_elevation import (
    CONTRACT_SCHEMA_VERSION,
    DATASET_DECLARED_SURVEY_END,
    DATASET_DECLARED_SURVEY_START,
    DTM_ATTRIBUTION,
    DTM_COVERAGE_ID,
    DTM_DATASET_ID,
    DTM_ENDPOINT,
    DTM_LICENCE,
    DTM_TITLE,
    DTM_VERTICAL_ACCURACY,
)
from satn.evidence_contracts import (
    canonical_evidence_json,
    evidence_fingerprint,
)

EA_TILE_RECEIPT_CONTRACT = "satn-ea-dtm-tile-receipt/v1"
EA_BNG_ATTESTATION_CONTRACT = "satn-ea-dtm-bng10km-raster-attestation/v1"
EA_COVERAGE_CONTRACT = "satn-ea-dtm-raster-coverage/v1"
EA_SAMPLING_CONTRACT = "satn-ea-dtm-elevation-sampling/v1"
EA_SOURCE_ID = "ea-lidar-composite-dtm-1m"
EA_WCS_VERSION = "2.0.1"
EA_TILE_SIZE_M = 5_000
EA_RESOLUTION_M = 1
EA_RESOLUTION_MM = 1_000
EA_NODATA = "-3.402823466e+38"
EA_NODATA_VALUE = float(np.finfo(np.float32).min)
EA_NODATA_POLICY = "non-finite-or-<=-3e38/v1"
EA_VERTICAL_REFERENCE = "ODN"
EA_TRANSFORMATION = "OSTN15"
EA_DATASET_DECLARATION_PROVENANCE = "governed-dataset-contract-declaration"
EA_GEOTIFF_EPSG = 27700

RasterAvailability = Literal["available", "no-data", "explicit-unknown"]
RasterCompleteness = Literal["complete", "partial"]
RasterIoEvent = Literal["receipt-read", "object-read"]
RasterIoObserver = Callable[[RasterIoEvent], None]


@dataclass(frozen=True)
class EATileReceipt:
    """One canonical receipt/object pair, with no local path in its identity."""

    receipt_payload: Mapping[str, object]
    request_fingerprint: str
    raw_sha256: str
    byte_count: int
    tile_key: tuple[int, int]
    bounds_m: tuple[int, int, int, int]
    fingerprint: str

    def __post_init__(self) -> None:
        payload = _plain_mapping(self.receipt_payload)
        encoded = _canonical_json_bytes(payload)
        expected = hashlib.sha256(encoded).hexdigest()
        if self.fingerprint != expected:
            raise ValueError("EA tile receipt fingerprint is stale")
        if payload.get("request_fingerprint") != self.request_fingerprint:
            raise ValueError("EA tile receipt request fingerprint is stale")
        if payload.get("raw_sha256") != self.raw_sha256:
            raise ValueError("EA tile receipt object fingerprint is stale")
        if payload.get("byte_count") != self.byte_count:
            raise ValueError("EA tile receipt byte count is stale")
        object.__setattr__(self, "receipt_payload", _freeze_mapping(payload))

    def canonical_payload(self) -> dict[str, object]:
        """Return the exact acquisition receipt stored in the registry."""

        return _plain_mapping(self.receipt_payload)


@dataclass(frozen=True)
class EABng10kmRasterAttestation:
    """Available EA 5 km children for one exact BNG 10 km partition."""

    bng_10km_cell: str
    completeness: RasterCompleteness
    tile_receipts: tuple[EATileReceipt, ...]
    fingerprint: str = ""

    def __post_init__(self) -> None:
        _bng_cell_indices(self.bng_10km_cell)
        receipts = tuple(sorted(self.tile_receipts, key=lambda item: item.fingerprint))
        if len({item.fingerprint for item in receipts}) != len(receipts):
            raise ValueError("EA BNG attestation tile receipts must be unique")
        expected_keys = set(_tile_keys_for_bng_cell(self.bng_10km_cell))
        actual_keys = {item.tile_key for item in receipts}
        if not actual_keys <= expected_keys:
            raise ValueError("EA tile receipt is outside its attested BNG 10 km cell")
        expected_completeness = "complete" if actual_keys == expected_keys else "partial"
        if self.completeness != expected_completeness:
            raise ValueError("EA BNG attestation completeness is stale")
        expected = evidence_fingerprint(self.canonical_payload(tile_receipts=receipts))
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("EA BNG attestation fingerprint is stale")
        object.__setattr__(self, "tile_receipts", receipts)
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(
        self, *, tile_receipts: tuple[EATileReceipt, ...] | None = None
    ) -> dict[str, object]:
        receipts = self.tile_receipts if tile_receipts is None else tile_receipts
        return {
            "contract": EA_BNG_ATTESTATION_CONTRACT,
            "bng_10km_cell": self.bng_10km_cell,
            "completeness": self.completeness,
            "tile_receipts": [
                {
                    "receipt_fingerprint": item.fingerprint,
                    "request_fingerprint": item.request_fingerprint,
                    "raw_sha256": item.raw_sha256,
                }
                for item in receipts
            ],
        }


@dataclass(frozen=True)
class EAElevationCoverage:
    """One immutable set of requested BNG 10 km raster attestations."""

    attestations: tuple[EABng10kmRasterAttestation, ...]
    requested_bng_10km_cells: tuple[str, ...]
    fingerprint: str = ""

    def __post_init__(self) -> None:
        attestations = tuple(sorted(self.attestations, key=lambda item: item.bng_10km_cell))
        cells = tuple(sorted(self.requested_bng_10km_cells))
        if not cells or len(set(cells)) != len(cells):
            raise ValueError("EA raster coverage requires unique requested BNG cells")
        for cell in cells:
            _bng_cell_indices(cell)
        if tuple(item.bng_10km_cell for item in attestations) != cells:
            raise ValueError("EA raster coverage attestations do not close requested cells")
        expected = evidence_fingerprint(
            self.canonical_payload(attestations=attestations, cells=cells)
        )
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("EA raster coverage fingerprint is stale")
        object.__setattr__(self, "attestations", attestations)
        object.__setattr__(self, "requested_bng_10km_cells", cells)
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(
        self,
        *,
        attestations: tuple[EABng10kmRasterAttestation, ...] | None = None,
        cells: tuple[str, ...] | None = None,
    ) -> dict[str, object]:
        resolved_attestations = self.attestations if attestations is None else attestations
        resolved_cells = self.requested_bng_10km_cells if cells is None else cells
        return {
            "contract": EA_COVERAGE_CONTRACT,
            "state": "complete",
            "attestation_fingerprints": [item.fingerprint for item in resolved_attestations],
            "requested_bng_10km_cells": list(resolved_cells),
        }


@dataclass(frozen=True)
class ElevationObservation:
    """One deterministic integer-millimetre sampling observation."""

    sample_index: int
    distance_mm: int
    east_mm: int
    north_mm: int
    availability: RasterAvailability
    elevation_mm: int | None
    tile_receipt_fingerprint: str | None

    def canonical_payload(self) -> dict[str, object]:
        return {
            "sample_index": self.sample_index,
            "distance_mm": self.distance_mm,
            "east_mm": self.east_mm,
            "north_mm": self.north_mm,
            "availability": self.availability,
            "elevation_mm": self.elevation_mm,
            "tile_receipt_fingerprint": self.tile_receipt_fingerprint,
        }


@dataclass(frozen=True)
class ElevationSamplingResult:
    """Replayable offline point/line sampling result."""

    coverage_state_fingerprint: str
    geometry_fingerprint: str
    spacing_mm: int
    consulted_attestation_fingerprints: tuple[str, ...]
    tile_receipt_fingerprints: tuple[str, ...]
    observations: tuple[ElevationObservation, ...]
    fingerprint: str = ""

    def __post_init__(self) -> None:
        attestations = tuple(sorted(set(self.consulted_attestation_fingerprints)))
        receipts = tuple(sorted(set(self.tile_receipt_fingerprints)))
        observations = tuple(sorted(self.observations, key=lambda item: item.sample_index))
        if [item.sample_index for item in observations] != list(range(len(observations))):
            raise ValueError("EA elevation samples require contiguous sample indices")
        expected = evidence_fingerprint(
            self.canonical_payload(
                attestations=attestations,
                receipts=receipts,
                observations=observations,
            )
        )
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("EA elevation sampling fingerprint is stale")
        object.__setattr__(self, "consulted_attestation_fingerprints", attestations)
        object.__setattr__(self, "tile_receipt_fingerprints", receipts)
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(
        self,
        *,
        attestations: tuple[str, ...] | None = None,
        receipts: tuple[str, ...] | None = None,
        observations: tuple[ElevationObservation, ...] | None = None,
    ) -> dict[str, object]:
        return {
            "contract": EA_SAMPLING_CONTRACT,
            "coverage_state_fingerprint": self.coverage_state_fingerprint,
            "geometry_fingerprint": self.geometry_fingerprint,
            "geometry_crs": "EPSG:27700",
            "spacing_mm": self.spacing_mm,
            "source_resolution_mm": EA_RESOLUTION_MM,
            "vertical_reference": _dataset_contract_declaration(EA_VERTICAL_REFERENCE),
            "transformation": _dataset_contract_declaration(EA_TRANSFORMATION),
            "consulted_attestation_fingerprints": list(
                self.consulted_attestation_fingerprints if attestations is None else attestations
            ),
            "tile_receipt_fingerprints": list(
                self.tile_receipt_fingerprints if receipts is None else receipts
            ),
            "observations": [
                item.canonical_payload()
                for item in (self.observations if observations is None else observations)
            ],
        }


@dataclass(frozen=True)
class ElevationSamplingReadSet:
    """Exact immutable ledger objects required by one elevation sample."""

    coverage_state_fingerprint: str
    geometry_fingerprint: str
    spacing_mm: int
    consulted_attestation_fingerprints: tuple[str, ...]
    tile_receipt_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, value in (
            ("coverage state", self.coverage_state_fingerprint),
            ("geometry", self.geometry_fingerprint),
        ):
            if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"EA elevation {name} fingerprint must be a SHA-256")
        if not isinstance(self.spacing_mm, int) or self.spacing_mm <= 0:
            raise ValueError("EA elevation sample spacing must be positive")
        attestations = tuple(sorted(set(self.consulted_attestation_fingerprints)))
        receipts = tuple(sorted(set(self.tile_receipt_fingerprints)))
        if any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in attestations):
            raise ValueError("EA elevation read-set attestations must be SHA-256 values")
        if any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in receipts):
            raise ValueError("EA elevation read-set receipts must be SHA-256 values")
        object.__setattr__(self, "consulted_attestation_fingerprints", attestations)
        object.__setattr__(self, "tile_receipt_fingerprints", receipts)


EA_RASTER_EXPECTED_COLUMNS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "current_ea_raster_coverage_state": (
        ("singleton", "BOOLEAN", "NO"),
        ("coverage_fingerprint", "VARCHAR", "NO"),
    ),
    "ea_raster_bng10km_attestation_registry": (
        ("fingerprint", "VARCHAR", "NO"),
        ("bng_10km_cell", "VARCHAR", "NO"),
        ("completeness", "VARCHAR", "NO"),
        ("canonical_payload_json", "VARCHAR", "NO"),
    ),
    "ea_raster_bng10km_attestation_receipt": (
        ("attestation_fingerprint", "VARCHAR", "NO"),
        ("receipt_fingerprint", "VARCHAR", "NO"),
        ("position", "BIGINT", "NO"),
    ),
    "ea_raster_coverage_state_attestation": (
        ("coverage_fingerprint", "VARCHAR", "NO"),
        ("attestation_fingerprint", "VARCHAR", "NO"),
        ("position", "BIGINT", "NO"),
    ),
    "ea_raster_coverage_state_registry": (
        ("fingerprint", "VARCHAR", "NO"),
        ("canonical_payload_json", "VARCHAR", "NO"),
    ),
    "ea_raster_coverage_state_requested_partition": (
        ("coverage_fingerprint", "VARCHAR", "NO"),
        ("bng_10km_cell", "VARCHAR", "NO"),
        ("position", "BIGINT", "NO"),
    ),
    "ea_raster_receipt_registry": (
        ("fingerprint", "VARCHAR", "NO"),
        ("request_fingerprint", "VARCHAR", "NO"),
        ("raw_sha256", "VARCHAR", "NO"),
        ("byte_count", "BIGINT", "NO"),
        ("tile_east_index", "BIGINT", "NO"),
        ("tile_north_index", "BIGINT", "NO"),
        ("minimum_east_m", "BIGINT", "NO"),
        ("minimum_north_m", "BIGINT", "NO"),
        ("maximum_east_m", "BIGINT", "NO"),
        ("maximum_north_m", "BIGINT", "NO"),
        ("canonical_payload_json", "VARCHAR", "NO"),
    ),
}

EA_RASTER_REQUIRED_CONSTRAINTS = frozenset(
    {
        (
            "ea_raster_receipt_registry",
            "PRIMARY KEY",
            ("fingerprint",),
            None,
            (),
            None,
        ),
        (
            "ea_raster_bng10km_attestation_registry",
            "PRIMARY KEY",
            ("fingerprint",),
            None,
            (),
            None,
        ),
        (
            "ea_raster_bng10km_attestation_registry",
            "CHECK",
            ("completeness",),
            None,
            (),
            "(completeness IN ('complete', 'partial'))",
        ),
        (
            "ea_raster_bng10km_attestation_receipt",
            "PRIMARY KEY",
            ("attestation_fingerprint", "receipt_fingerprint"),
            None,
            (),
            None,
        ),
        (
            "ea_raster_bng10km_attestation_receipt",
            "UNIQUE",
            ("attestation_fingerprint", "position"),
            None,
            (),
            None,
        ),
        (
            "ea_raster_bng10km_attestation_receipt",
            "CHECK",
            ("position",),
            None,
            (),
            '("position" >= 0)',
        ),
        (
            "ea_raster_bng10km_attestation_receipt",
            "FOREIGN KEY",
            ("attestation_fingerprint",),
            "ea_raster_bng10km_attestation_registry",
            ("fingerprint",),
            None,
        ),
        (
            "ea_raster_bng10km_attestation_receipt",
            "FOREIGN KEY",
            ("receipt_fingerprint",),
            "ea_raster_receipt_registry",
            ("fingerprint",),
            None,
        ),
        (
            "ea_raster_coverage_state_registry",
            "PRIMARY KEY",
            ("fingerprint",),
            None,
            (),
            None,
        ),
        (
            "ea_raster_coverage_state_attestation",
            "PRIMARY KEY",
            ("coverage_fingerprint", "attestation_fingerprint"),
            None,
            (),
            None,
        ),
        (
            "ea_raster_coverage_state_attestation",
            "UNIQUE",
            ("coverage_fingerprint", "position"),
            None,
            (),
            None,
        ),
        (
            "ea_raster_coverage_state_attestation",
            "CHECK",
            ("position",),
            None,
            (),
            '("position" >= 0)',
        ),
        (
            "ea_raster_coverage_state_attestation",
            "FOREIGN KEY",
            ("coverage_fingerprint",),
            "ea_raster_coverage_state_registry",
            ("fingerprint",),
            None,
        ),
        (
            "ea_raster_coverage_state_attestation",
            "FOREIGN KEY",
            ("attestation_fingerprint",),
            "ea_raster_bng10km_attestation_registry",
            ("fingerprint",),
            None,
        ),
        (
            "ea_raster_coverage_state_requested_partition",
            "PRIMARY KEY",
            ("coverage_fingerprint", "bng_10km_cell"),
            None,
            (),
            None,
        ),
        (
            "ea_raster_coverage_state_requested_partition",
            "UNIQUE",
            ("coverage_fingerprint", "position"),
            None,
            (),
            None,
        ),
        (
            "ea_raster_coverage_state_requested_partition",
            "CHECK",
            ("position",),
            None,
            (),
            '("position" >= 0)',
        ),
        (
            "ea_raster_coverage_state_requested_partition",
            "FOREIGN KEY",
            ("coverage_fingerprint",),
            "ea_raster_coverage_state_registry",
            ("fingerprint",),
            None,
        ),
        (
            "current_ea_raster_coverage_state",
            "PRIMARY KEY",
            ("singleton",),
            None,
            (),
            None,
        ),
        (
            "current_ea_raster_coverage_state",
            "CHECK",
            ("singleton",),
            None,
            (),
            "singleton",
        ),
        (
            "current_ea_raster_coverage_state",
            "FOREIGN KEY",
            ("coverage_fingerprint",),
            "ea_raster_coverage_state_registry",
            ("fingerprint",),
            None,
        ),
    }
)


def create_ea_raster_schema(connection: Any) -> None:
    """Create metadata-only EA raster catalogue tables."""

    connection.execute(
        """
        CREATE TABLE ea_raster_receipt_registry (
            fingerprint VARCHAR,
            request_fingerprint VARCHAR NOT NULL,
            raw_sha256 VARCHAR NOT NULL,
            byte_count BIGINT NOT NULL,
            tile_east_index BIGINT NOT NULL,
            tile_north_index BIGINT NOT NULL,
            minimum_east_m BIGINT NOT NULL,
            minimum_north_m BIGINT NOT NULL,
            maximum_east_m BIGINT NOT NULL,
            maximum_north_m BIGINT NOT NULL,
            canonical_payload_json VARCHAR NOT NULL,
            CONSTRAINT ea_raster_receipt_pk PRIMARY KEY (fingerprint)
        );
        CREATE TABLE ea_raster_bng10km_attestation_registry (
            fingerprint VARCHAR,
            bng_10km_cell VARCHAR NOT NULL,
            completeness VARCHAR NOT NULL,
            canonical_payload_json VARCHAR NOT NULL,
            CONSTRAINT ea_raster_attestation_pk PRIMARY KEY (fingerprint),
            CONSTRAINT ea_raster_attestation_completeness
                CHECK (completeness IN ('complete', 'partial'))
        );
        CREATE TABLE ea_raster_bng10km_attestation_receipt (
            attestation_fingerprint VARCHAR NOT NULL,
            receipt_fingerprint VARCHAR NOT NULL,
            position BIGINT NOT NULL,
            CONSTRAINT ea_raster_attestation_receipt_pk
                PRIMARY KEY (attestation_fingerprint, receipt_fingerprint),
            CONSTRAINT ea_raster_attestation_receipt_position
                UNIQUE (attestation_fingerprint, position),
            CONSTRAINT ea_raster_attestation_receipt_position_nonnegative
                CHECK (position >= 0),
            CONSTRAINT ea_raster_attestation_receipt_attestation_fk
                FOREIGN KEY (attestation_fingerprint)
                REFERENCES ea_raster_bng10km_attestation_registry (fingerprint),
            CONSTRAINT ea_raster_attestation_receipt_receipt_fk
                FOREIGN KEY (receipt_fingerprint)
                REFERENCES ea_raster_receipt_registry (fingerprint)
        );
        CREATE TABLE ea_raster_coverage_state_registry (
            fingerprint VARCHAR,
            canonical_payload_json VARCHAR NOT NULL,
            CONSTRAINT ea_raster_coverage_pk PRIMARY KEY (fingerprint)
        );
        CREATE TABLE ea_raster_coverage_state_attestation (
            coverage_fingerprint VARCHAR NOT NULL,
            attestation_fingerprint VARCHAR NOT NULL,
            position BIGINT NOT NULL,
            CONSTRAINT ea_raster_coverage_attestation_pk
                PRIMARY KEY (coverage_fingerprint, attestation_fingerprint),
            CONSTRAINT ea_raster_coverage_attestation_position
                UNIQUE (coverage_fingerprint, position),
            CONSTRAINT ea_raster_coverage_attestation_position_nonnegative
                CHECK (position >= 0),
            CONSTRAINT ea_raster_coverage_attestation_coverage_fk
                FOREIGN KEY (coverage_fingerprint)
                REFERENCES ea_raster_coverage_state_registry (fingerprint),
            CONSTRAINT ea_raster_coverage_attestation_attestation_fk
                FOREIGN KEY (attestation_fingerprint)
                REFERENCES ea_raster_bng10km_attestation_registry (fingerprint)
        );
        CREATE TABLE ea_raster_coverage_state_requested_partition (
            coverage_fingerprint VARCHAR NOT NULL,
            bng_10km_cell VARCHAR NOT NULL,
            position BIGINT NOT NULL,
            CONSTRAINT ea_raster_coverage_partition_pk
                PRIMARY KEY (coverage_fingerprint, bng_10km_cell),
            CONSTRAINT ea_raster_coverage_partition_position
                UNIQUE (coverage_fingerprint, position),
            CONSTRAINT ea_raster_coverage_partition_position_nonnegative
                CHECK (position >= 0),
            CONSTRAINT ea_raster_coverage_partition_coverage_fk
                FOREIGN KEY (coverage_fingerprint)
                REFERENCES ea_raster_coverage_state_registry (fingerprint)
        );
        CREATE TABLE current_ea_raster_coverage_state (
            singleton BOOLEAN,
            coverage_fingerprint VARCHAR NOT NULL,
            CONSTRAINT current_ea_raster_coverage_pk PRIMARY KEY (singleton),
            CONSTRAINT current_ea_raster_coverage_singleton CHECK (singleton),
            CONSTRAINT current_ea_raster_coverage_fk
                FOREIGN KEY (coverage_fingerprint)
                REFERENCES ea_raster_coverage_state_registry (fingerprint)
        );
        """
    )


def refresh_ea_elevation_cache(
    connection: Any,
    *,
    cache_dir: Path,
    requested_bng_10km_cells: tuple[str, ...],
) -> EAElevationCoverage:
    """Register verified receipt/object pairs for disconnected BNG cells."""

    requested = tuple(sorted(requested_bng_10km_cells))
    if not requested or len(set(requested)) != len(requested):
        raise ValueError("EA elevation refresh requires unique requested BNG 10 km cells")
    for cell in requested:
        _bng_cell_indices(cell)

    current = _current_coverage(connection)
    by_cell = {} if current is None else {item.bng_10km_cell: item for item in current.attestations}
    all_cells = set(requested)
    if current is not None:
        verify_ea_coverage(
            connection,
            current,
            cache_dir=cache_dir,
            verify_file_cells=frozenset(requested),
        )
        all_cells.update(current.requested_bng_10km_cells)

    requested_tile_keys = {key for cell in requested for key in _tile_keys_for_bng_cell(cell)}
    verified_current_receipts = {
        receipt.tile_key: receipt.fingerprint
        for cell in requested
        for receipt in (() if cell not in by_cell else by_cell[cell].tile_receipts)
    }
    eligible = _verified_cache_receipts(
        cache_dir,
        requested_tile_keys=frozenset(requested_tile_keys),
        verified_receipt_fingerprints_by_tile=verified_current_receipts,
    )
    by_tile_key = {item.tile_key: item for item in eligible}
    for cell in requested:
        receipts = tuple(
            by_tile_key[key] for key in _tile_keys_for_bng_cell(cell) if key in by_tile_key
        )
        by_cell[cell] = EABng10kmRasterAttestation(
            bng_10km_cell=cell,
            completeness="complete" if len(receipts) == 4 else "partial",
            tile_receipts=receipts,
        )

    coverage = EAElevationCoverage(
        attestations=tuple(by_cell[cell] for cell in sorted(all_cells)),
        requested_bng_10km_cells=tuple(sorted(all_cells)),
    )
    _insert_coverage(connection, coverage)
    connection.execute("DELETE FROM current_ea_raster_coverage_state")
    connection.execute(
        """
        INSERT INTO current_ea_raster_coverage_state (singleton, coverage_fingerprint)
        VALUES (true, ?)
        """,
        [coverage.fingerprint],
    )
    return coverage


def resolve_elevation_sampling_read_set(
    connection: Any,
    *,
    cache_dir: Path,
    geometry: BaseGeometry,
    geometry_crs: str,
    spacing_mm: int,
    state_fingerprint: str | None,
    verify_files: bool,
    io_observer: RasterIoObserver | None = None,
) -> ElevationSamplingReadSet:
    """Resolve one sample's exact canonical ledger dependency without decoding."""

    if geometry_crs != "EPSG:27700":
        raise ValueError("EA elevation sampling requires EPSG:27700 geometry")
    if not isinstance(spacing_mm, int) or spacing_mm <= 0:
        raise ValueError("EA elevation sample spacing must be positive integer millimetres")
    _canonical_geometry, geometry_fingerprint, sample_points = _sampling_points(
        geometry,
        spacing_mm=spacing_mm,
    )
    coverage = (
        _current_coverage(connection)
        if state_fingerprint is None
        else _coverage_by_fingerprint(connection, state_fingerprint)
    )
    if coverage is None:
        raise ValueError("Local Evidence Store has no EA raster coverage state")
    verify_ea_coverage(
        connection,
        coverage,
        cache_dir=cache_dir,
        verify_file_cells=frozenset(),
    )
    by_cell = {item.bng_10km_cell: item for item in coverage.attestations}
    sample_cells = {
        _bng_cell_for_mm(east_mm, north_mm)
        for _distance_mm, east_mm, north_mm in sample_points
    }
    missing_cells = sample_cells - set(by_cell)
    if missing_cells:
        raise ValueError(
            "pinned EA raster coverage does not cover sampling BNG cells: "
            + ", ".join(sorted(missing_cells))
        )
    consulted = tuple(by_cell[cell] for cell in sorted(sample_cells))
    receipt_by_tile = {
        receipt.tile_key: receipt
        for attestation in consulted
        for receipt in attestation.tile_receipts
    }
    used_receipts = {
        receipt.fingerprint: receipt
        for _distance_mm, east_mm, north_mm in sample_points
        if (receipt := receipt_by_tile.get(_tile_key_for_mm(east_mm, north_mm)))
        is not None
    }
    if verify_files:
        for receipt_fingerprint in sorted(used_receipts):
            receipt = used_receipts[receipt_fingerprint]
            _verify_receipt_files(
                cache_dir,
                receipt,
                decode=False,
                io_observer=io_observer,
            )
    return ElevationSamplingReadSet(
        coverage_state_fingerprint=coverage.fingerprint,
        geometry_fingerprint=geometry_fingerprint,
        spacing_mm=spacing_mm,
        consulted_attestation_fingerprints=tuple(
            item.fingerprint for item in consulted
        ),
        tile_receipt_fingerprints=tuple(used_receipts),
    )


def sample_elevation(
    connection: Any,
    *,
    cache_dir: Path,
    geometry: BaseGeometry,
    geometry_crs: str,
    spacing_mm: int,
    state_fingerprint: str | None,
    io_observer: RasterIoObserver | None = None,
) -> ElevationSamplingResult:
    """Sample a point or line offline, decoding at most one tile at a time."""

    if geometry_crs != "EPSG:27700":
        raise ValueError("EA elevation sampling requires EPSG:27700 geometry")
    if not isinstance(spacing_mm, int) or spacing_mm <= 0:
        raise ValueError("EA elevation sample spacing must be positive integer millimetres")
    _canonical_geometry, geometry_fingerprint, sample_points = _sampling_points(
        geometry, spacing_mm=spacing_mm
    )
    coverage = (
        _current_coverage(connection)
        if state_fingerprint is None
        else _coverage_by_fingerprint(connection, state_fingerprint)
    )
    if coverage is None:
        raise ValueError("Local Evidence Store has no EA raster coverage state")

    by_cell = {item.bng_10km_cell: item for item in coverage.attestations}
    sample_cells = {
        _bng_cell_for_mm(east_mm, north_mm) for _distance_mm, east_mm, north_mm in sample_points
    }
    missing_cells = sample_cells - set(by_cell)
    if missing_cells:
        raise ValueError(
            "pinned EA raster coverage does not cover sampling BNG cells: "
            + ", ".join(sorted(missing_cells))
        )
    verify_ea_coverage(
        connection,
        coverage,
        cache_dir=cache_dir,
        verify_file_cells=frozenset(sample_cells),
        io_observer=io_observer,
    )
    by_tile: dict[tuple[int, int], tuple[EABng10kmRasterAttestation, EATileReceipt]] = {}
    for attestation in coverage.attestations:
        for receipt in attestation.tile_receipts:
            by_tile[receipt.tile_key] = (attestation, receipt)

    statuses: list[ElevationObservation | None] = [None] * len(sample_points)
    work: dict[str, list[tuple[int, int, int, int]]] = {}
    consulted: set[str] = set()
    used_receipts: dict[str, EATileReceipt] = {}
    for index, (distance_mm, east_mm, north_mm) in enumerate(sample_points):
        cell = _bng_cell_for_mm(east_mm, north_mm)
        attestation = by_cell.get(cell)
        if attestation is not None:
            consulted.add(attestation.fingerprint)
        tile_key = _tile_key_for_mm(east_mm, north_mm)
        pair = by_tile.get(tile_key)
        if pair is None:
            statuses[index] = ElevationObservation(
                sample_index=index,
                distance_mm=distance_mm,
                east_mm=east_mm,
                north_mm=north_mm,
                availability="explicit-unknown",
                elevation_mm=None,
                tile_receipt_fingerprint=None,
            )
            continue
        tile_attestation, receipt = pair
        consulted.add(tile_attestation.fingerprint)
        used_receipts[receipt.fingerprint] = receipt
        work.setdefault(receipt.fingerprint, []).append((index, distance_mm, east_mm, north_mm))

    for receipt_fingerprint in sorted(work):
        receipt = used_receipts[receipt_fingerprint]
        object_path = _object_path(cache_dir, receipt.raw_sha256)
        pixels, transform = (
            _load_verified_tile(object_path, receipt)
            if io_observer is None
            else _load_verified_tile(
                object_path,
                receipt,
                io_observer=io_observer,
            )
        )
        try:
            for index, distance_mm, east_mm, north_mm in work[receipt_fingerprint]:
                elevation = _sample_pixels(pixels, transform, east_mm=east_mm, north_mm=north_mm)
                statuses[index] = ElevationObservation(
                    sample_index=index,
                    distance_mm=distance_mm,
                    east_mm=east_mm,
                    north_mm=north_mm,
                    availability="no-data" if elevation is None else "available",
                    elevation_mm=(None if elevation is None else round(float(elevation) * 1000)),
                    tile_receipt_fingerprint=receipt.fingerprint,
                )
        finally:
            del pixels

    observations = tuple(item for item in statuses if item is not None)
    if len(observations) != len(sample_points):
        raise AssertionError("EA elevation sampling left unresolved observations")
    return ElevationSamplingResult(
        coverage_state_fingerprint=coverage.fingerprint,
        geometry_fingerprint=geometry_fingerprint,
        spacing_mm=spacing_mm,
        consulted_attestation_fingerprints=tuple(consulted),
        tile_receipt_fingerprints=tuple(used_receipts),
        observations=observations,
    )


def verify_ea_coverage(
    connection: Any,
    coverage: EAElevationCoverage,
    *,
    cache_dir: Path,
    verify_file_cells: frozenset[str] | None = None,
    io_observer: RasterIoObserver | None = None,
) -> None:
    """Verify immutable registry closure and every retained receipt/object pair."""

    row = connection.execute(
        """
        SELECT canonical_payload_json FROM ea_raster_coverage_state_registry
        WHERE fingerprint = ?
        """,
        [coverage.fingerprint],
    ).fetchone()
    if row != (canonical_evidence_json(coverage.canonical_payload()),):
        raise ValueError("EA raster coverage registry payload is invalid")
    attestation_rows = connection.execute(
        """
        SELECT attestation_fingerprint, position
        FROM ea_raster_coverage_state_attestation
        WHERE coverage_fingerprint = ? ORDER BY position
        """,
        [coverage.fingerprint],
    ).fetchall()
    if attestation_rows != [
        (item.fingerprint, position) for position, item in enumerate(coverage.attestations)
    ]:
        raise ValueError("EA raster coverage attestation closure is invalid")
    cell_rows = connection.execute(
        """
        SELECT bng_10km_cell, position
        FROM ea_raster_coverage_state_requested_partition
        WHERE coverage_fingerprint = ? ORDER BY position
        """,
        [coverage.fingerprint],
    ).fetchall()
    if cell_rows != [
        (cell, position) for position, cell in enumerate(coverage.requested_bng_10km_cells)
    ]:
        raise ValueError("EA raster requested partition closure is invalid")
    for attestation in coverage.attestations:
        _verify_attestation(
            connection,
            attestation,
            cache_dir=cache_dir,
            verify_files=(
                verify_file_cells is None or attestation.bng_10km_cell in verify_file_cells
            ),
            io_observer=io_observer,
        )


def resolve_verified_ea_coverage(
    connection: Any,
    *,
    cache_dir: Path,
    state_fingerprint: str | None = None,
) -> EAElevationCoverage:
    """Resolve and verify one current or historical EA raster state and its objects."""

    coverage = (
        _current_coverage(connection)
        if state_fingerprint is None
        else _coverage_by_fingerprint(connection, state_fingerprint)
    )
    if coverage is None:
        raise ValueError("Local Evidence Store has no EA raster coverage state")
    verify_ea_coverage(connection, coverage, cache_dir=cache_dir)
    return coverage


def _insert_coverage(connection: Any, coverage: EAElevationCoverage) -> None:
    for attestation in coverage.attestations:
        for receipt in attestation.tile_receipts:
            _insert_receipt(connection, receipt)
        payload = canonical_evidence_json(attestation.canonical_payload())
        existing = connection.execute(
            """
            SELECT canonical_payload_json
            FROM ea_raster_bng10km_attestation_registry WHERE fingerprint = ?
            """,
            [attestation.fingerprint],
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO ea_raster_bng10km_attestation_registry
                (fingerprint, bng_10km_cell, completeness, canonical_payload_json)
                VALUES (?, ?, ?, ?)
                """,
                [
                    attestation.fingerprint,
                    attestation.bng_10km_cell,
                    attestation.completeness,
                    payload,
                ],
            )
            for position, receipt in enumerate(attestation.tile_receipts):
                connection.execute(
                    """
                    INSERT INTO ea_raster_bng10km_attestation_receipt
                    (attestation_fingerprint, receipt_fingerprint, position)
                    VALUES (?, ?, ?)
                    """,
                    [attestation.fingerprint, receipt.fingerprint, position],
                )
        elif existing != (payload,):
            raise ValueError("EA raster attestation fingerprint collision")

    payload = canonical_evidence_json(coverage.canonical_payload())
    existing = connection.execute(
        """
        SELECT canonical_payload_json FROM ea_raster_coverage_state_registry
        WHERE fingerprint = ?
        """,
        [coverage.fingerprint],
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO ea_raster_coverage_state_registry
            (fingerprint, canonical_payload_json) VALUES (?, ?)
            """,
            [coverage.fingerprint, payload],
        )
        for position, attestation in enumerate(coverage.attestations):
            connection.execute(
                """
                INSERT INTO ea_raster_coverage_state_attestation
                (coverage_fingerprint, attestation_fingerprint, position)
                VALUES (?, ?, ?)
                """,
                [coverage.fingerprint, attestation.fingerprint, position],
            )
        for position, cell in enumerate(coverage.requested_bng_10km_cells):
            connection.execute(
                """
                INSERT INTO ea_raster_coverage_state_requested_partition
                (coverage_fingerprint, bng_10km_cell, position)
                VALUES (?, ?, ?)
                """,
                [coverage.fingerprint, cell, position],
            )
    elif existing != (payload,):
        raise ValueError("EA raster coverage fingerprint collision")


def _insert_receipt(connection: Any, receipt: EATileReceipt) -> None:
    payload = _canonical_json_bytes(receipt.canonical_payload()).decode().rstrip("\n")
    existing = connection.execute(
        """
        SELECT canonical_payload_json FROM ea_raster_receipt_registry
        WHERE fingerprint = ?
        """,
        [receipt.fingerprint],
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO ea_raster_receipt_registry
            (fingerprint, request_fingerprint, raw_sha256, byte_count,
             tile_east_index, tile_north_index,
             minimum_east_m, minimum_north_m, maximum_east_m, maximum_north_m,
             canonical_payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                receipt.fingerprint,
                receipt.request_fingerprint,
                receipt.raw_sha256,
                receipt.byte_count,
                *receipt.tile_key,
                *receipt.bounds_m,
                payload,
            ],
        )
    elif existing != (payload,):
        raise ValueError("EA tile receipt fingerprint collision")


def _verify_attestation(
    connection: Any,
    attestation: EABng10kmRasterAttestation,
    *,
    cache_dir: Path,
    verify_files: bool,
    io_observer: RasterIoObserver | None = None,
) -> None:
    row = connection.execute(
        """
        SELECT bng_10km_cell, completeness, canonical_payload_json
        FROM ea_raster_bng10km_attestation_registry WHERE fingerprint = ?
        """,
        [attestation.fingerprint],
    ).fetchone()
    expected = (
        attestation.bng_10km_cell,
        attestation.completeness,
        canonical_evidence_json(attestation.canonical_payload()),
    )
    if row != expected:
        raise ValueError("EA raster BNG attestation registry is invalid")
    receipt_rows = connection.execute(
        """
        SELECT receipt_fingerprint, position
        FROM ea_raster_bng10km_attestation_receipt
        WHERE attestation_fingerprint = ? ORDER BY position
        """,
        [attestation.fingerprint],
    ).fetchall()
    if receipt_rows != [
        (item.fingerprint, position) for position, item in enumerate(attestation.tile_receipts)
    ]:
        raise ValueError("EA raster BNG attestation receipt closure is invalid")
    for receipt in attestation.tile_receipts:
        _verify_receipt_registry(connection, receipt)
        if verify_files:
            _verify_receipt_files(
                cache_dir,
                receipt,
                decode=False,
                io_observer=io_observer,
            )


def _verify_receipt_registry(connection: Any, receipt: EATileReceipt) -> None:
    row = connection.execute(
        """
        SELECT request_fingerprint, raw_sha256, byte_count,
               tile_east_index, tile_north_index,
               minimum_east_m, minimum_north_m, maximum_east_m, maximum_north_m,
               canonical_payload_json
        FROM ea_raster_receipt_registry WHERE fingerprint = ?
        """,
        [receipt.fingerprint],
    ).fetchone()
    expected = (
        receipt.request_fingerprint,
        receipt.raw_sha256,
        receipt.byte_count,
        *receipt.tile_key,
        *receipt.bounds_m,
        _canonical_json_bytes(receipt.canonical_payload()).decode().rstrip("\n"),
    )
    if row != expected:
        raise ValueError("EA tile receipt registry is invalid")


def _current_coverage(connection: Any) -> EAElevationCoverage | None:
    row = connection.execute(
        """
        SELECT coverage_fingerprint FROM current_ea_raster_coverage_state
        WHERE singleton = true
        """
    ).fetchone()
    return None if row is None else _coverage_by_fingerprint(connection, str(row[0]))


def _coverage_by_fingerprint(connection: Any, fingerprint: str) -> EAElevationCoverage:
    if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise ValueError("EA raster coverage fingerprint must be a full SHA-256")
    row = connection.execute(
        """
        SELECT canonical_payload_json FROM ea_raster_coverage_state_registry
        WHERE fingerprint = ?
        """,
        [fingerprint],
    ).fetchone()
    if row is None:
        raise ValueError(f"EA raster coverage state {fingerprint} is not found")
    payload = _json_object(str(row[0]), "EA raster coverage")
    attestation_rows = connection.execute(
        """
        SELECT attestation_fingerprint
        FROM ea_raster_coverage_state_attestation
        WHERE coverage_fingerprint = ? ORDER BY position
        """,
        [fingerprint],
    ).fetchall()
    cell_rows = connection.execute(
        """
        SELECT bng_10km_cell
        FROM ea_raster_coverage_state_requested_partition
        WHERE coverage_fingerprint = ? ORDER BY position
        """,
        [fingerprint],
    ).fetchall()
    coverage = EAElevationCoverage(
        attestations=tuple(
            _attestation_by_fingerprint(connection, str(item[0])) for item in attestation_rows
        ),
        requested_bng_10km_cells=tuple(str(item[0]) for item in cell_rows),
        fingerprint=fingerprint,
    )
    if canonical_evidence_json(coverage.canonical_payload()) != canonical_evidence_json(payload):
        raise ValueError("EA raster coverage payload is invalid")
    return coverage


def _attestation_by_fingerprint(connection: Any, fingerprint: str) -> EABng10kmRasterAttestation:
    row = connection.execute(
        """
        SELECT bng_10km_cell, completeness, canonical_payload_json
        FROM ea_raster_bng10km_attestation_registry WHERE fingerprint = ?
        """,
        [fingerprint],
    ).fetchone()
    if row is None:
        raise ValueError("EA raster BNG attestation is missing")
    receipt_rows = connection.execute(
        """
        SELECT receipt_fingerprint FROM ea_raster_bng10km_attestation_receipt
        WHERE attestation_fingerprint = ? ORDER BY position
        """,
        [fingerprint],
    ).fetchall()
    attestation = EABng10kmRasterAttestation(
        bng_10km_cell=str(row[0]),
        completeness=str(row[1]),
        tile_receipts=tuple(
            _receipt_by_fingerprint(connection, str(item[0])) for item in receipt_rows
        ),
        fingerprint=fingerprint,
    )
    if str(row[2]) != canonical_evidence_json(attestation.canonical_payload()):
        raise ValueError("EA raster BNG attestation payload is invalid")
    return attestation


def _receipt_by_fingerprint(connection: Any, fingerprint: str) -> EATileReceipt:
    row = connection.execute(
        """
        SELECT request_fingerprint, raw_sha256, byte_count,
               tile_east_index, tile_north_index,
               minimum_east_m, minimum_north_m, maximum_east_m, maximum_north_m,
               canonical_payload_json
        FROM ea_raster_receipt_registry WHERE fingerprint = ?
        """,
        [fingerprint],
    ).fetchone()
    if row is None:
        raise ValueError("EA raster tile receipt is missing")
    payload = _json_object(str(row[9]), "EA tile receipt")
    return EATileReceipt(
        receipt_payload=payload,
        request_fingerprint=str(row[0]),
        raw_sha256=str(row[1]),
        byte_count=int(row[2]),
        tile_key=(int(row[3]), int(row[4])),
        bounds_m=(int(row[5]), int(row[6]), int(row[7]), int(row[8])),
        fingerprint=fingerprint,
    )


def _verified_cache_receipts(
    cache_dir: Path,
    *,
    requested_tile_keys: frozenset[tuple[int, int]],
    verified_receipt_fingerprints_by_tile: Mapping[tuple[int, int], str] | None = None,
) -> tuple[EATileReceipt, ...]:
    receipts_dir = cache_dir / "receipts"
    if not receipts_dir.exists():
        return ()
    if not receipts_dir.is_dir():
        raise ValueError("EA tile receipt cache path is not a directory")
    verified = (
        {}
        if verified_receipt_fingerprints_by_tile is None
        else dict(verified_receipt_fingerprints_by_tile)
    )
    receipts: list[EATileReceipt] = []
    for tile_key in sorted(requested_tile_keys):
        request_fingerprint = hashlib.sha256(
            _canonical_json_bytes(_governed_tile_request_payload(tile_key))
        ).hexdigest()
        path = receipts_dir / f"{request_fingerprint}.json"
        if not path.exists():
            continue
        if not path.is_file():
            raise ValueError("EA tile receipt path is not a regular file")
        receipt = _read_receipt(path)
        if receipt.tile_key != tile_key:
            raise ValueError("EA tile receipt request resolves to a different tile")
        if verified.get(tile_key) != receipt.fingerprint:
            _verify_receipt_files(cache_dir, receipt, decode=True)
        receipts.append(receipt)
    return tuple(receipts)


def _governed_tile_request_payload(tile_key: tuple[int, int]) -> dict[str, object]:
    minimum_east = tile_key[0] * EA_TILE_SIZE_M
    minimum_north = tile_key[1] * EA_TILE_SIZE_M
    return {
        "contract": EA_TILE_RECEIPT_CONTRACT,
        "source_id": EA_SOURCE_ID,
        "dataset_id": DTM_DATASET_ID,
        "dataset_title": DTM_TITLE,
        "coverage_id": DTM_COVERAGE_ID,
        "endpoint": DTM_ENDPOINT,
        "licence": DTM_LICENCE,
        "attribution": DTM_ATTRIBUTION,
        "acquisition_contract_version": CONTRACT_SCHEMA_VERSION,
        "publisher_release": None,
        "effective_date": None,
        "dataset_declared_survey_period": {
            "start": DATASET_DECLARED_SURVEY_START,
            "end": DATASET_DECLARED_SURVEY_END,
        },
        "request": {
            "service": "WCS",
            "version": EA_WCS_VERSION,
            "operation": "GetCoverage",
            "format": "image/tiff",
            "crs": "EPSG:27700",
            "tile_key": list(tile_key),
            "bounds_m": [
                minimum_east,
                minimum_north,
                minimum_east + EA_TILE_SIZE_M,
                minimum_north + EA_TILE_SIZE_M,
            ],
            "tile_size_m": EA_TILE_SIZE_M,
            "output_spacing_mm": EA_RESOLUTION_MM,
            "scale_factor": f"{1000 / EA_RESOLUTION_MM:.8f}",
        },
        "vertical_reference": EA_VERTICAL_REFERENCE,
        "transformation": EA_TRANSFORMATION,
        "source_resolution_m": EA_RESOLUTION_M,
        "vertical_accuracy": DTM_VERTICAL_ACCURACY,
        "nodata_policy": EA_NODATA_POLICY,
    }


def _read_receipt(path: Path) -> EATileReceipt:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"EA tile receipt is invalid JSON: {path}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"EA tile receipt is not an object: {path}")
    if raw != _canonical_json_bytes(payload):
        raise ValueError(f"EA tile receipt is not canonical: {path}")
    _validate_receipt_payload(payload)
    request_payload = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "request_fingerprint",
            "raw_sha256",
            "byte_count",
            "observed_raster_metadata",
        }
    }
    request_fingerprint = hashlib.sha256(_canonical_json_bytes(request_payload)).hexdigest()
    if payload["request_fingerprint"] != request_fingerprint:
        raise ValueError("EA tile receipt request fingerprint is invalid")
    if path.stem != request_fingerprint:
        raise ValueError("EA tile receipt filename does not match its request fingerprint")
    request = payload["request"]
    assert isinstance(request, dict)
    tile_key = tuple(int(item) for item in request["tile_key"])
    bounds_m = tuple(int(item) for item in request["bounds_m"])
    return EATileReceipt(
        receipt_payload=payload,
        request_fingerprint=request_fingerprint,
        raw_sha256=str(payload["raw_sha256"]),
        byte_count=int(payload["byte_count"]),
        tile_key=(tile_key[0], tile_key[1]),
        bounds_m=(bounds_m[0], bounds_m[1], bounds_m[2], bounds_m[3]),
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def _validate_receipt_payload(payload: dict[str, object]) -> None:
    expected_fields = {
        "contract",
        "source_id",
        "dataset_id",
        "dataset_title",
        "coverage_id",
        "endpoint",
        "licence",
        "attribution",
        "acquisition_contract_version",
        "publisher_release",
        "effective_date",
        "dataset_declared_survey_period",
        "request",
        "vertical_reference",
        "transformation",
        "source_resolution_m",
        "vertical_accuracy",
        "nodata_policy",
        "request_fingerprint",
        "raw_sha256",
        "byte_count",
        "observed_raster_metadata",
    }
    if set(payload) != expected_fields:
        raise ValueError("EA tile receipt does not match the v1 schema")
    exact = {
        "contract": EA_TILE_RECEIPT_CONTRACT,
        "source_id": EA_SOURCE_ID,
        "dataset_id": DTM_DATASET_ID,
        "dataset_title": DTM_TITLE,
        "coverage_id": DTM_COVERAGE_ID,
        "endpoint": DTM_ENDPOINT,
        "licence": DTM_LICENCE,
        "attribution": DTM_ATTRIBUTION,
        "acquisition_contract_version": CONTRACT_SCHEMA_VERSION,
        "publisher_release": None,
        "effective_date": None,
        "dataset_declared_survey_period": {
            "start": DATASET_DECLARED_SURVEY_START,
            "end": DATASET_DECLARED_SURVEY_END,
        },
        "vertical_reference": EA_VERTICAL_REFERENCE,
        "transformation": EA_TRANSFORMATION,
        "source_resolution_m": EA_RESOLUTION_M,
        "vertical_accuracy": DTM_VERTICAL_ACCURACY,
        "nodata_policy": EA_NODATA_POLICY,
    }
    if any(payload.get(key) != value for key, value in exact.items()):
        raise ValueError("EA tile receipt provenance differs from the governed contract")
    if re.fullmatch(r"[0-9a-f]{64}", str(payload["raw_sha256"])) is None:
        raise ValueError("EA tile receipt raw SHA-256 is invalid")
    if not isinstance(payload["byte_count"], int) or int(payload["byte_count"]) <= 0:
        raise ValueError("EA tile receipt byte count is invalid")
    request = payload["request"]
    if not isinstance(request, dict) or set(request) != {
        "service",
        "version",
        "operation",
        "format",
        "crs",
        "tile_key",
        "bounds_m",
        "tile_size_m",
        "output_spacing_mm",
        "scale_factor",
    }:
        raise ValueError("EA tile receipt request schema is invalid")
    if {key: request[key] for key in ("service", "version", "operation", "format", "crs")} != {
        "service": "WCS",
        "version": EA_WCS_VERSION,
        "operation": "GetCoverage",
        "format": "image/tiff",
        "crs": "EPSG:27700",
    }:
        raise ValueError("EA tile receipt WCS request is not governed")
    tile_key = request["tile_key"]
    bounds = request["bounds_m"]
    if (
        not isinstance(tile_key, list)
        or len(tile_key) != 2
        or any(not isinstance(item, int) for item in tile_key)
        or not isinstance(bounds, list)
        or len(bounds) != 4
        or any(not isinstance(item, int) for item in bounds)
    ):
        raise ValueError("EA tile receipt tile coordinates are invalid")
    tile_size = request["tile_size_m"]
    spacing = request["output_spacing_mm"]
    if not isinstance(tile_size, int) or tile_size <= 0:
        raise ValueError("EA tile receipt tile size is invalid")
    if not isinstance(spacing, int) or spacing <= 0:
        raise ValueError("EA tile receipt output spacing is invalid")
    expected_bounds = [
        tile_key[0] * tile_size,
        tile_key[1] * tile_size,
        (tile_key[0] + 1) * tile_size,
        (tile_key[1] + 1) * tile_size,
    ]
    if bounds != expected_bounds:
        raise ValueError("EA tile receipt bounds do not match its tile key")
    if request["scale_factor"] != f"{1000 / spacing:.8f}":
        raise ValueError("EA tile receipt scale factor is invalid")
    observed = payload["observed_raster_metadata"]
    if not isinstance(observed, dict) or set(observed) != {
        "crs",
        "dimensions",
        "model_transformation",
        "nodata",
        "nodata_observed",
    }:
        raise ValueError("EA tile receipt observed raster metadata is invalid")


def _verify_receipt_files(
    cache_dir: Path,
    receipt: EATileReceipt,
    *,
    decode: bool,
    io_observer: RasterIoObserver | None = None,
) -> None:
    receipt_path = cache_dir / "receipts" / f"{receipt.request_fingerprint}.json"
    if not receipt_path.is_file():
        raise ValueError("EA tile receipt file is missing")
    if io_observer is not None:
        io_observer("receipt-read")
    if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != receipt.fingerprint:
        raise ValueError("EA tile receipt bytes changed")
    object_path = _object_path(cache_dir, receipt.raw_sha256)
    if not object_path.is_file():
        raise ValueError("EA tile object is missing")
    if object_path.stat().st_size != receipt.byte_count:
        raise ValueError("EA tile object byte count differs from its receipt")
    if io_observer is not None:
        io_observer("object-read")
    if _sha256_file(object_path) != receipt.raw_sha256:
        raise ValueError("EA tile object digest differs from its receipt")
    if io_observer is not None:
        io_observer("object-read")
    actual = _observed_raster_metadata(object_path, receipt, decode=decode)
    if actual != _plain_mapping(receipt.receipt_payload["observed_raster_metadata"]):
        raise ValueError("EA tile receipt raster metadata differs from its object")


def _observed_raster_metadata(
    path: Path, receipt: EATileReceipt, *, decode: bool
) -> dict[str, object]:
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    with Image.open(path) as image:
        transform = image.tag_v2.get(34264)
        geokeys = image.tag_v2.get(34735)
        nodata_observed = image.tag_v2.get(42113)
        dimensions = image.size
        if decode:
            pixels = np.asarray(image)
            if pixels.ndim != 2 or pixels.shape != (dimensions[1], dimensions[0]):
                raise ValueError("EA GeoTIFF has unusable raster dimensions")
            _ = float(pixels[0, 0])
    if not transform or len(transform) != 16:
        raise ValueError("EA GeoTIFF is missing ModelTransformationTag")
    if not geokeys or len(geokeys) < 4:
        raise ValueError("EA GeoTIFF is missing GeoKeyDirectoryTag")
    transform_values = tuple(float(item) for item in transform)
    entries = {
        tuple(int(item) for item in geokeys[4 + offset : 8 + offset])
        for offset in range(0, 4 * int(geokeys[3]), 4)
    }
    if (3072, 0, 1, EA_GEOTIFF_EPSG) not in entries:
        raise ValueError("EA GeoTIFF CRS is not EPSG:27700")
    expected_dimensions = (EA_TILE_SIZE_M, EA_TILE_SIZE_M)
    if tuple(int(item) for item in dimensions) != expected_dimensions:
        raise ValueError("EA GeoTIFF dimensions are not an exact 5 km 1 m tile")
    expected_transform = _expected_transform(receipt.bounds_m)
    if transform_values != expected_transform:
        raise ValueError("EA GeoTIFF transform does not match its exact 5 km bounds")
    observed_text = None if nodata_observed is None else str(nodata_observed).strip("\x00")
    try:
        observed_value = float(observed_text) if observed_text is not None else None
    except ValueError as error:
        raise ValueError("EA GeoTIFF NoData is not numeric") from error
    if (
        observed_value is None
        or not math.isfinite(observed_value)
        or float(np.float32(observed_value)) != EA_NODATA_VALUE
    ):
        raise ValueError("EA GeoTIFF NoData differs from the governed value")
    return {
        "crs": "EPSG:27700",
        "dimensions": [EA_TILE_SIZE_M, EA_TILE_SIZE_M],
        "model_transformation": list(transform_values),
        "nodata": EA_NODATA,
        "nodata_observed": observed_text,
    }


def _load_verified_tile(
    path: Path,
    receipt: EATileReceipt,
    *,
    io_observer: RasterIoObserver | None = None,
) -> tuple[np.ndarray, tuple[float, ...]]:
    _verify_receipt_files(
        path.parents[2],
        receipt,
        decode=False,
        io_observer=io_observer,
    )
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    if io_observer is not None:
        io_observer("object-read")
    with Image.open(path) as image:
        pixels = np.asarray(image).copy()
    if pixels.shape != (EA_TILE_SIZE_M, EA_TILE_SIZE_M):
        raise ValueError("EA GeoTIFF decoded dimensions are invalid")
    return pixels, _expected_transform(receipt.bounds_m)


def _sample_pixels(
    pixels: np.ndarray,
    transform: tuple[float, ...],
    *,
    east_mm: int,
    north_mm: int,
) -> float | None:
    east = east_mm / 1000
    north = north_mm / 1000
    column = math.floor((east - transform[3]) / transform[0])
    row = math.floor((north - transform[7]) / transform[5])
    # Match the governed acquisition sampler exactly at raster boundaries.
    # The coordinate-addressed tile is selected first; a point on its southern
    # edge therefore reads the nearest bottom-row pixel rather than the tile to
    # the south.
    column = min(max(column, 0), pixels.shape[1] - 1)
    row = min(max(row, 0), pixels.shape[0] - 1)
    elevation = float(pixels[row, column])
    if not math.isfinite(elevation) or elevation <= -3e38:
        return None
    return elevation


def _sampling_points(
    geometry: BaseGeometry, *, spacing_mm: int
) -> tuple[dict[str, object], str, tuple[tuple[int, int, int], ...]]:
    if geometry.is_empty:
        raise ValueError("EA elevation sampling geometry is empty")
    if isinstance(geometry, Point):
        coordinates = ((round(geometry.x * 1000), round(geometry.y * 1000)),)
        canonical = {"type": "Point", "coordinates_mm": list(coordinates[0])}
        points = ((0, *coordinates[0]),)
    else:
        line = _normalised_line(geometry)
        coordinate_mm = tuple(
            (round(east * 1000), round(north * 1000)) for east, north in line.coords
        )
        if len(set(coordinate_mm)) < 2:
            raise ValueError("EA elevation sampling line collapses at millimetre precision")
        reverse = tuple(reversed(coordinate_mm))
        if reverse < coordinate_mm:
            coordinate_mm = reverse
        canonical = {
            "type": "LineString",
            "coordinates_mm": [list(item) for item in coordinate_mm],
        }
        metric = LineString([(east / 1000, north / 1000) for east, north in coordinate_mm])
        length_mm = round(metric.length * 1000)
        distances = list(range(0, length_mm + 1, spacing_mm))
        if distances[-1] != length_mm:
            distances.append(length_mm)
        points_list: list[tuple[int, int, int]] = []
        for distance_mm in distances:
            point = metric.interpolate(
                metric.length if distance_mm == length_mm else distance_mm / 1000
            )
            points_list.append((distance_mm, round(point.x * 1000), round(point.y * 1000)))
        points = tuple(points_list)
    if not all(isinstance(value, int) for item in points for value in item):
        raise AssertionError("EA elevation canonical samples are not integer millimetres")
    geometry_fingerprint = evidence_fingerprint(
        {
            "contract": "satn-ea-dtm-sampling-geometry/v1",
            "crs": "EPSG:27700",
            "geometry": canonical,
        }
    )
    return canonical, geometry_fingerprint, points


def _normalised_line(geometry: BaseGeometry) -> LineString:
    if isinstance(geometry, LineString):
        line = geometry
    elif isinstance(geometry, MultiLineString):
        merged = linemerge(geometry)
        if not isinstance(merged, LineString):
            raise ValueError("EA elevation MultiLineString must form one connected line")
        line = merged
    else:
        raise ValueError("EA elevation sampling supports Point or connected line geometry")
    if not line.is_simple or line.length <= 0:
        raise ValueError("EA elevation sampling requires one simple non-zero line")
    if any(not math.isfinite(value) for coordinate in line.coords for value in coordinate):
        raise ValueError("EA elevation sampling geometry has non-finite coordinates")
    return line


def _object_path(cache_dir: Path, raw_sha256: str) -> Path:
    return cache_dir / "objects" / "sha256" / f"{raw_sha256}.tif"


def _dataset_contract_declaration(value: str) -> dict[str, object]:
    """Identify dataset-contract claims that are not observed GeoTIFF metadata."""

    return {
        "value": value,
        "provenance_kind": EA_DATASET_DECLARATION_PROVENANCE,
        "observed_in_geotiff": False,
    }


def _expected_transform(bounds_m: tuple[int, int, int, int]) -> tuple[float, ...]:
    return (
        1.0,
        0.0,
        0.0,
        float(bounds_m[0]),
        0.0,
        -1.0,
        0.0,
        float(bounds_m[3]),
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    )


def _tile_keys_for_bng_cell(cell: str) -> tuple[tuple[int, int], ...]:
    easting_10km, northing_10km = _bng_cell_indices(cell)
    return tuple(
        sorted(
            (easting_10km * 2 + east_offset, northing_10km * 2 + north_offset)
            for east_offset in (0, 1)
            for north_offset in (0, 1)
        )
    )


def _bng_cell_for_mm(east_mm: int, north_mm: int) -> str:
    easting_index = east_mm // 10_000_000
    northing_index = north_mm // 10_000_000
    if not 0 <= easting_index <= 69 or not 0 <= northing_index <= 129:
        raise ValueError("EA elevation sample is outside the supported BNG extent")
    return _bng_10km_cell(easting_index, northing_index)


def _tile_key_for_mm(east_mm: int, north_mm: int) -> tuple[int, int]:
    tile_mm = EA_TILE_SIZE_M * 1000
    return (
        east_mm // tile_mm,
        north_mm // tile_mm,
    )


def _bng_cell_indices(cell: str) -> tuple[int, int]:
    if re.fullmatch(r"[A-HJ-Z]{2}[0-9]{2}", cell) is None:
        raise ValueError(f"invalid BNG 10 km cell: {cell}")
    for easting_index in range(70):
        for northing_index in range(130):
            if _bng_10km_cell(easting_index, northing_index) == cell:
                return easting_index, northing_index
    raise ValueError(f"BNG 10 km cell is outside the supported grid: {cell}")


def _bng_10km_cell(easting_index: int, northing_index: int) -> str:
    easting_100km, east_digit = divmod(easting_index, 10)
    northing_100km, north_digit = divmod(northing_index, 10)
    first_index = (19 - northing_100km) - (19 - northing_100km) % 5 + (easting_100km + 10) // 5
    second_index = ((19 - northing_100km) * 5) % 25 + easting_100km % 5
    if first_index > 7:
        first_index += 1
    if second_index > 7:
        second_index += 1
    return f"{chr(first_index + ord('A'))}{chr(second_index + ord('A'))}{east_digit}{north_digit}"


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _plain_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("EA evidence payload is not an object")

    def plain(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): plain(child) for key, child in item.items()}
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            return [plain(child) for child in item]
        return item

    result = plain(value)
    assert isinstance(result, dict)
    return result


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    def freeze(item: object) -> object:
        if isinstance(item, Mapping):
            return MappingProxyType(
                {str(key): freeze(child) for key, child in sorted(item.items())}
            )
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            return tuple(freeze(child) for child in item)
        return item

    frozen = freeze(value)
    assert isinstance(frozen, Mapping)
    return frozen


def _json_object(raw: str, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} registry payload is invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} registry payload is not an object")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
