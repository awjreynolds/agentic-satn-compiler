"""Narrow, immutable projections used to prove Local Evidence equivalence.

This module is additive.  It does not load snapshots, compile networks or
publish artifacts; those existing paths remain the independent oracle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType

import geopandas as gpd
import pandas as pd
from shapely.geometry.base import BaseGeometry

from satn.evidence_contracts import (
    SourceExport,
    evidence_fingerprint,
    evidence_geometry_fingerprint,
)
from satn.local_evidence_store import EvidenceQueryResult
from satn.open_roads_adapter import ATTRIBUTES as OPEN_ROADS_ATTRIBUTES
from satn.open_roads_adapter import SOURCE_LAYER as OPEN_ROADS_SOURCE_LAYER
from satn.open_roads_adapter import canonical_official_classification

OFFICIAL_ROAD_SOURCE_FRAME_COLUMNS = (
    "official_feature_id",
    "official_classification",
    "official_road_number",
    "official_road_name",
    "official_road_function",
    "source_id",
    "effective_date",
    "licence",
    "content_fingerprint",
    "geometry",
)


class EvidenceStoreEquivalenceError(ValueError):
    """A store-backed projection differs from the unchanged source-frame oracle."""


@dataclass(frozen=True)
class OfficialRoadSourceLineage:
    """The governed source identity required by the existing source frame."""

    source_export: SourceExport
    source_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_export, SourceExport):
            raise ValueError("official-road lineage requires a SourceExport")
        if (
            self.source_export.source_family != "os-open-roads"
            or self.source_export.dataset != "open-roads"
            or self.source_export.layer != "RoadLink"
        ):
            raise ValueError("official-road lineage requires an OS Open Roads RoadLink export")
        if (
            not isinstance(self.source_id, str)
            or not self.source_id
            or self.source_id.strip() != self.source_id
        ):
            raise ValueError("official-road lineage source_id must be canonical text")


@dataclass(frozen=True)
class OfficialRoadSourceFrameRow:
    """One immutable row in the existing official-road source-frame contract."""

    official_feature_id: str
    official_classification: str
    official_road_number: str | None
    official_road_name: str | None
    official_road_function: str | None
    source_id: str
    effective_date: str
    licence: str
    content_fingerprint: str
    geometry: BaseGeometry

    def as_record(self) -> dict[str, object]:
        return {
            "official_feature_id": self.official_feature_id,
            "official_classification": self.official_classification,
            "official_road_number": self.official_road_number,
            "official_road_name": self.official_road_name,
            "official_road_function": self.official_road_function,
            "source_id": self.source_id,
            "effective_date": self.effective_date,
            "licence": self.licence,
            "content_fingerprint": self.content_fingerprint,
            "geometry": self.geometry,
        }


@dataclass(frozen=True)
class OfficialRoadSourceFrameProjection:
    """Immutable Local Evidence projection plus its exact query provenance."""

    rows: tuple[OfficialRoadSourceFrameRow, ...]
    query_result_fingerprint: str
    availability_counts: Mapping[str, int]
    semantic_fingerprint: str = ""
    fingerprint: str = ""

    def __post_init__(self) -> None:
        rows = tuple(sorted(self.rows, key=lambda row: row.official_feature_id))
        if len({row.official_feature_id for row in rows}) != len(rows):
            raise ValueError("official-road source-frame feature IDs must be unique")
        counts = dict(self.availability_counts)
        if set(counts) != {"available", "no-data", "explicit-unknown"}:
            raise ValueError("official-road projection requires all availability states")
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise ValueError("official-road projection availability counts must be non-negative")
        semantic_fingerprint = evidence_fingerprint(
            {
                "contract": "satn-official-road-source-frame-semantics/v1",
                "rows": [_projected_row_payload(row) for row in rows],
            }
        )
        if self.semantic_fingerprint and self.semantic_fingerprint != semantic_fingerprint:
            raise ValueError("official-road projection semantic fingerprint is stale")
        fingerprint = evidence_fingerprint(
            {
                "contract": "satn-official-road-source-frame-projection/v1",
                "query_result_fingerprint": self.query_result_fingerprint,
                "availability_counts": dict(sorted(counts.items())),
                "semantic_fingerprint": semantic_fingerprint,
            }
        )
        if self.fingerprint and self.fingerprint != fingerprint:
            raise ValueError("official-road projection fingerprint is stale")
        object.__setattr__(self, "rows", rows)
        object.__setattr__(
            self,
            "availability_counts",
            MappingProxyType(dict(sorted(counts.items()))),
        )
        object.__setattr__(self, "semantic_fingerprint", semantic_fingerprint)
        object.__setattr__(self, "fingerprint", fingerprint)

    def to_geodataframe(self) -> gpd.GeoDataFrame:
        """Return a fresh mutable frame at the legacy compiler boundary."""

        frame = gpd.GeoDataFrame(
            [row.as_record() for row in self.rows],
            columns=list(OFFICIAL_ROAD_SOURCE_FRAME_COLUMNS),
            geometry="geometry",
            crs="EPSG:27700",
        )
        for column in (
            "official_road_number",
            "official_road_name",
            "official_road_function",
        ):
            frame[column] = frame[column].astype(object).where(frame[column].notna(), None)
        return frame


def project_official_road_source_frame(
    result: EvidenceQueryResult,
    lineage: OfficialRoadSourceLineage,
) -> OfficialRoadSourceFrameProjection:
    """Project one exact Open Roads query into the existing source-frame shape."""

    if not isinstance(result, EvidenceQueryResult):
        raise ValueError("official-road projection requires an EvidenceQueryResult")
    if not isinstance(lineage, OfficialRoadSourceLineage):
        raise ValueError("official-road projection requires governed source lineage")
    if result.manifest["source_layer"] != OPEN_ROADS_SOURCE_LAYER:
        raise ValueError("official-road projection requires the Open Roads RoadLink layer")
    if set(result.manifest["projection"]) != set(OPEN_ROADS_ATTRIBUTES):
        raise ValueError("official-road projection requires every governed Open Roads attribute")
    expected_export = lineage.source_export.fingerprint
    if any(row.source_export_fingerprint != expected_export for row in result.rows):
        raise ValueError("official-road query contains a foreign Source Export")
    counts = result.manifest["availability_counts"]
    if not isinstance(counts, Mapping):
        raise ValueError("official-road query has no governed availability counts")
    rows = tuple(
        OfficialRoadSourceFrameRow(
            official_feature_id=_official_feature_id(row.logical_key),
            official_classification=canonical_official_classification(
                row.attributes["road_classification"]
            ).value,
            official_road_number=_optional_text(
                row.attributes["road_classification_number"]
            ),
            official_road_name=_optional_text(row.attributes["name_1"]),
            official_road_function=_optional_text(row.attributes["road_function"]),
            source_id=lineage.source_id,
            effective_date=lineage.source_export.effective_date,
            licence=lineage.source_export.licence,
            content_fingerprint=lineage.source_export.raw_bytes_sha256,
            geometry=row.geometry,
        )
        for row in result.rows
    )
    return OfficialRoadSourceFrameProjection(
        rows=rows,
        query_result_fingerprint=result.fingerprint,
        availability_counts={str(name): int(value) for name, value in counts.items()},
    )


def _official_feature_id(logical_key: str) -> str:
    namespace = "roadlink:"
    if not logical_key.startswith(namespace) or len(logical_key) == len(namespace):
        raise ValueError("official-road query contains a non-canonical logical key")
    return logical_key[len(namespace) :]


def canonical_official_road_source_frame_fingerprint(frame: gpd.GeoDataFrame) -> str:
    """Hash legacy official-road semantics independently of row order and CRS."""

    return evidence_fingerprint(
        {
            "contract": "satn-official-road-source-frame-semantics/v1",
            "rows": _canonical_official_road_frame_rows(frame),
        }
    )


def assert_official_road_source_frame_equivalent(
    oracle: gpd.GeoDataFrame,
    projection: OfficialRoadSourceFrameProjection,
    *,
    expected_availability_counts: Mapping[str, int],
) -> None:
    """Fail when an unchanged snapshot frame and store projection differ."""

    if not isinstance(projection, OfficialRoadSourceFrameProjection):
        raise ValueError("official-road comparison requires a source-frame projection")
    expected_counts = dict(expected_availability_counts)
    if expected_counts != dict(projection.availability_counts):
        raise EvidenceStoreEquivalenceError(
            "official-road availability semantics differ: "
            f"oracle={dict(sorted(expected_counts.items()))} "
            f"store={dict(projection.availability_counts)}"
        )
    oracle_fingerprint = canonical_official_road_source_frame_fingerprint(oracle)
    if oracle_fingerprint != projection.semantic_fingerprint:
        raise EvidenceStoreEquivalenceError(
            "official-road source-frame semantics differ: "
            f"oracle={oracle_fingerprint} store={projection.semantic_fingerprint}"
        )


def _canonical_official_road_frame_rows(
    frame: gpd.GeoDataFrame,
) -> list[dict[str, object]]:
    if not isinstance(frame, gpd.GeoDataFrame) or frame.crs is None:
        raise ValueError("official-road source frame requires a declared CRS")
    required = {
        "official_feature_id",
        "official_classification",
        "source_id",
        "effective_date",
        "licence",
        "content_fingerprint",
        "geometry",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(
            "official-road source frame is missing columns: " + ", ".join(sorted(missing))
        )
    projected = frame.to_crs(27700)
    rows: list[dict[str, object]] = []
    for _, row in projected.iterrows():
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            raise ValueError("official-road source-frame geometry is missing")
        rows.append(
            {
                "official_feature_id": _required_frame_text(
                    row["official_feature_id"], "official_feature_id"
                ),
                "official_classification": _required_frame_text(
                    row["official_classification"], "official_classification"
                ),
                "official_road_number": _optional_frame_text(
                    row.get("official_road_number")
                ),
                "official_road_name": _optional_frame_text(row.get("official_road_name")),
                "official_road_function": _optional_frame_text(
                    row.get("official_road_function")
                ),
                "source_id": _required_frame_text(row["source_id"], "source_id"),
                "effective_date": _canonical_date(row["effective_date"]),
                "licence": _required_frame_text(row["licence"], "licence"),
                "content_fingerprint": _required_frame_text(
                    row["content_fingerprint"], "content_fingerprint"
                ),
                "geometry_fingerprint": evidence_geometry_fingerprint(
                    geometry, "EPSG:27700"
                ),
            }
        )
    rows.sort(key=lambda item: str(item["official_feature_id"]))
    identifiers = [str(item["official_feature_id"]) for item in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("official-road source-frame feature IDs must be unique")
    return rows


def _projected_row_payload(row: OfficialRoadSourceFrameRow) -> dict[str, object]:
    return {
        "official_feature_id": row.official_feature_id,
        "official_classification": row.official_classification,
        "official_road_number": row.official_road_number,
        "official_road_name": row.official_road_name,
        "official_road_function": row.official_road_function,
        "source_id": row.source_id,
        "effective_date": row.effective_date,
        "licence": row.licence,
        "content_fingerprint": row.content_fingerprint,
        "geometry_fingerprint": evidence_geometry_fingerprint(
            row.geometry, "EPSG:27700"
        ),
    }


def _required_frame_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"official-road source-frame {name} must be canonical text")
    return value


def _optional_frame_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    return _required_frame_text(value, "optional attribute")


def _canonical_date(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _required_frame_text(value, "effective_date")
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as error:
        raise ValueError("official-road effective_date must be an ISO date") from error


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError("official-road source attributes must be canonical text or null")
    return value
