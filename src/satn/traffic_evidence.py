"""Governed DfT traffic and protected-space evidence annotations.

Traffic evidence is optional enrichment for alignment candidates.  These records
retain source and row identities so a profile-derived challenge can be inspected
without turning missing evidence into a safety or eligibility gate.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")


def _canonical_ids(value: tuple[str, ...], field: str) -> tuple[str, ...]:
    if any(_ID.fullmatch(item) is None for item in value):
        raise ValueError(f"{field} must contain canonical identifiers")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} cannot contain duplicates")
    return tuple(sorted(value))


class TrafficFreshnessState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class TrafficMatchState(StrEnum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"
    UNMATCHED = "unmatched"
    UNKNOWN = "unknown"


class TrafficCoverageStatus(StrEnum):
    SAMPLED = "sampled"
    NOT_SAMPLED = "not_sampled"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class TrafficExposure(StrEnum):
    """Candidate placement fact used independently from Alignment Basis."""

    ON_CARRIAGEWAY = "on-carriageway"
    OFF_CARRIAGEWAY = "off-carriageway"
    UNKNOWN = "unknown"


class ProtectedSpaceState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    MISSING = "missing"
    STALE = "stale"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class TrafficObservation(BaseModel):
    """One normalized AADF observation with immutable source lineage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: str = Field(min_length=1)
    source_export_fingerprint: str = Field(pattern=_SHA256.pattern)
    source_layer: Literal["aadf", "aadf-by-direction", "raw-counts"]
    count_point_id: str = Field(min_length=1)
    observation_year: int = Field(ge=1900, strict=True)
    count_date: date | None = None
    direction_of_travel: Literal[
        "N",
        "S",
        "E",
        "W",
        "C",
        "north",
        "south",
        "east",
        "west",
        "combined",
    ] | None = None
    road_name: str | None = None
    road_category: str | None = None
    road_type: str | None = None
    start_junction_road_name: str | None = None
    end_junction_road_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    easting: float | None = None
    northing: float | None = None
    declared_crs: str | None = None
    geometry_fingerprint: str | None = Field(default=None, pattern=_SHA256.pattern)
    link_length_km: float | None = Field(default=None, ge=0, strict=True)
    all_motor_vehicles: int | None = Field(default=None, ge=0, strict=True)
    estimation_method: str | None = None
    estimation_method_detailed: str | None = None
    freshness_state: TrafficFreshnessState = TrafficFreshnessState.UNKNOWN
    match_state: TrafficMatchState = TrafficMatchState.UNKNOWN
    coverage_status: TrafficCoverageStatus = TrafficCoverageStatus.UNKNOWN
    match_proof: dict[str, object] | None = None
    match_state_fingerprint: str | None = Field(default=None, pattern=_SHA256.pattern)
    row_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_ids: tuple[str, ...] = ()
    provenance_ids: tuple[str, ...] = ()

    @field_validator("evidence_ids", "provenance_ids")
    @classmethod
    def validate_ids(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        return _canonical_ids(value, getattr(info, "field_name", "ids"))

    @model_validator(mode="after")
    def validate_layer_metric(self) -> Self:
        if self.source_layer == "raw-counts" and self.all_motor_vehicles is not None:
            raise ValueError("raw-counts observations cannot classify all_motor_vehicles")
        if self.source_layer == "aadf-by-direction" and self.direction_of_travel is None:
            raise ValueError("aadf-by-direction observations require direction")
        if self.direction_of_travel in {"C", "north", "south", "east", "west"}:
            canonical = {
                "C": "combined",
                "north": "N",
                "south": "S",
                "east": "E",
                "west": "W",
            }[self.direction_of_travel]
            object.__setattr__(self, "direction_of_travel", canonical)
        return self


class ProtectedSpaceEvidence(BaseModel):
    """Claim-specific protected-space result and its evidence/provenance IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: ProtectedSpaceState
    evidence_ids: tuple[str, ...] = ()
    provenance_ids: tuple[str, ...] = ()

    @field_validator("evidence_ids", "provenance_ids")
    @classmethod
    def validate_ids(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        return _canonical_ids(value, getattr(info, "field_name", "ids"))


class TrafficChallengeDiagnostic(BaseModel):
    """Structured, non-veto traffic/protected-space selection diagnostic."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    diagnostic_id: str = "traffic-high-on-carriageway-without-protected-space"
    candidate_id: str = Field(min_length=1)
    traffic_observation_id: str = Field(min_length=1)
    observation_year: int = Field(ge=1900, strict=True)
    traffic_band: str = Field(min_length=1)
    traffic_profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    source_export_fingerprint: str = Field(pattern=_SHA256.pattern)
    row_fingerprint: str = Field(pattern=_SHA256.pattern)
    freshness_state: TrafficFreshnessState
    estimation_method: str | None = None
    protected_space_state: ProtectedSpaceState
    evidence_ids: tuple[str, ...] = ()
    provenance_ids: tuple[str, ...] = ()

    @field_validator("evidence_ids", "provenance_ids")
    @classmethod
    def validate_ids(cls, value: tuple[str, ...], info: object) -> tuple[str, ...]:
        return _canonical_ids(value, getattr(info, "field_name", "ids"))

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()
