"""Deterministic matching of governed DfT count-point observations.

This module is deliberately an offline, pure boundary.  It never discovers or
fetches traffic evidence; callers provide observations read from a pinned
Local Evidence Store state.  Matching is explicit-count-point/link-first and
uses a configured route buffer only when those identities are unavailable.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from shapely.geometry.base import BaseGeometry

from .traffic_evidence import TrafficMatchState, TrafficObservation

_SHA256 = r"^[0-9a-f]{64}$"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class TrafficMatchPolicy(BaseModel):
    """Versioned matching rule; this is not a traffic safety threshold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    # The caller must declare this tolerance in the governed policy.  The
    # matcher has no hidden geometric threshold of its own.
    route_buffer_m: float = Field(ge=0, strict=True)
    source_layers: tuple[str, ...] = ("aadf", "aadf-by-direction")
    contract: str = "satn-dft-traffic-matching/v1"

    @field_validator("route_buffer_m")
    @classmethod
    def finite_buffer(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("route_buffer_m must be finite")
        return 0.0 if value == 0 else value

    @field_validator("source_layers")
    @classmethod
    def canonical_layers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        allowed = {"aadf", "aadf-by-direction"}
        if not value or any(layer not in allowed for layer in value):
            raise ValueError("source_layers must use supported DfT traffic layers")
        if len(set(value)) != len(value):
            raise ValueError("source_layers cannot contain duplicates")
        return tuple(sorted(value))

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.model_dump(mode="json"))


# Configuration-facing spelling retained as an explicit alias for callers that
# use the longer name from the evidence contract.
TrafficMatchingPolicy = TrafficMatchPolicy


class TrafficMatchResult(BaseModel):
    """One deterministic match decision plus all retained matching evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observations: tuple[TrafficObservation, ...] = ()
    match_state: TrafficMatchState
    match_proof: Mapping[str, object]
    state_fingerprint: str = Field(pattern=_SHA256)
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def canonicalise(self) -> TrafficMatchResult:
        object.__setattr__(
            self,
            "observations",
            tuple(
                sorted(
                    self.observations,
                    key=lambda item: (
                        item.observation_id,
                        item.source_export_fingerprint,
                        item.row_fingerprint,
                    ),
                )
            ),
        )
        object.__setattr__(self, "diagnostics", tuple(sorted(set(self.diagnostics))))
        return self


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.casefold() if text else None


def _link_identity(value: Mapping[str, object] | None) -> tuple[str | None, ...] | None:
    if value is None:
        return None
    return tuple(
        _text(value.get(name))
        for name in (
            "road_name",
            "road_category",
            "start_junction_road_name",
            "end_junction_road_name",
        )
    )


def _observation_link_identity(
    observation: TrafficObservation,
) -> tuple[str | None, ...]:
    return (
        _text(observation.road_name),
        _text(observation.road_category),
        _text(observation.start_junction_road_name),
        _text(observation.end_junction_road_name),
    )


def _compatible_link_identity(
    observation: TrafficObservation,
    target: tuple[str | None, ...] | None,
) -> bool:
    if target is None:
        return False
    observed = _observation_link_identity(observation)
    comparable = [
        (expected, actual)
        for expected, actual in zip(target, observed, strict=True)
        if expected is not None and actual is not None
    ]
    return bool(comparable) and all(expected == actual for expected, actual in comparable)


def _point_distance(observation: TrafficObservation, geometry: BaseGeometry) -> float | None:
    if observation.easting is None or observation.northing is None:
        return None
    try:
        from shapely.geometry import Point

        return float(Point(observation.easting, observation.northing).distance(geometry))
    except (TypeError, ValueError):
        return None


def _state_fingerprint(
    *,
    policy: TrafficMatchPolicy,
    state: TrafficMatchState,
    proof: Mapping[str, object],
    observations: Sequence[TrafficObservation],
    evidence_state_fingerprint: str | None,
) -> str:
    return _fingerprint(
        {
            "contract": "satn-dft-traffic-match-state/v1",
            "policy_fingerprint": policy.fingerprint,
            "evidence_state_fingerprint": evidence_state_fingerprint,
            "coverage_state_fingerprint": evidence_state_fingerprint,
            "match_state": state.value,
            "match_proof": dict(proof),
            "observations": [
                {
                    "observation_id": item.observation_id,
                    "source_export_fingerprint": item.source_export_fingerprint,
                    "row_fingerprint": item.row_fingerprint,
                }
                for item in observations
            ],
        }
    )


def _annotate(
    observation: TrafficObservation,
    *,
    state: TrafficMatchState,
    proof: Mapping[str, object],
    state_fingerprint: str,
) -> TrafficObservation:
    return observation.model_copy(
        update={
            "match_state": state,
            "match_proof": dict(proof),
            "match_state_fingerprint": state_fingerprint,
        }
    )


def match_dft_traffic(
    observations: Sequence[TrafficObservation],
    *,
    policy: TrafficMatchPolicy,
    candidate_geometry: BaseGeometry | None = None,
    count_point_id: str | None = None,
    link_identity: Mapping[str, object] | None = None,
    observation_year: int | None = None,
    source_layers: Sequence[str] | None = None,
    evidence_state_fingerprint: str | None = None,
) -> TrafficMatchResult:
    """Match all supplied observations without dropping ambiguous evidence."""

    if evidence_state_fingerprint is not None and re.fullmatch(
        _SHA256, evidence_state_fingerprint
    ) is None:
        raise ValueError("evidence_state_fingerprint must be a full lowercase SHA-256")

    candidates = tuple(
        item
        for item in observations
        if (observation_year is None or item.observation_year == observation_year)
        and (source_layers is None or item.source_layer in source_layers)
        and item.source_layer in policy.source_layers
    )
    method = "unmatched"
    selected: tuple[TrafficObservation, ...] = ()
    if count_point_id:
        selected = tuple(item for item in candidates if item.count_point_id == count_point_id)
        method = "explicit-count-point"
    elif link_identity is not None:
        target = _link_identity(link_identity)
        selected = tuple(
            item for item in candidates if _compatible_link_identity(item, target)
        )
        method = "link-identity"
    elif candidate_geometry is not None:
        nearby: list[tuple[TrafficObservation, float]] = []
        for item in candidates:
            distance = _point_distance(item, candidate_geometry)
            if distance is not None and distance <= policy.route_buffer_m:
                nearby.append((item, distance))
        selected = tuple(item for item, _distance in nearby)
        method = "route-buffer"

    ordered = tuple(
        sorted(
            selected,
            key=lambda item: (
                item.observation_id,
                item.source_export_fingerprint,
                item.row_fingerprint,
            ),
        )
    )
    # One count-point/year/direction is one claim.  Repeated rows carrying the
    # same claim are retained and harmless; differing claims are ambiguous,
    # while disagreement inside one claim is conflicting.
    grouped: dict[tuple[str, int, str | None], list[TrafficObservation]] = {}
    for item in ordered:
        grouped.setdefault(
            (item.count_point_id, item.observation_year, item.direction_of_travel), []
        ).append(item)

    def substantive_signature(item: TrafficObservation) -> tuple[object, ...]:
        return (
            item.all_motor_vehicles,
            item.road_name,
            item.road_category,
            item.road_type,
            item.start_junction_road_name,
            item.end_junction_road_name,
            item.latitude,
            item.longitude,
            item.easting,
            item.northing,
            item.declared_crs,
            item.link_length_km,
            item.estimation_method,
            item.estimation_method_detailed,
        )

    conflicting_claim = any(
        item.match_state == TrafficMatchState.CONFLICTING
        for rows in grouped.values()
        for item in rows
    ) or any(len({substantive_signature(row) for row in rows}) > 1 for rows in grouped.values())
    if not ordered:
        state = TrafficMatchState.UNKNOWN
        method = "unmatched"
        diagnostics = ("traffic-unknown",)
    elif conflicting_claim:
        state = TrafficMatchState.CONFLICTING
        diagnostics = ("traffic-conflict",)
    elif len(grouped) > 1:
        state = TrafficMatchState.AMBIGUOUS
        diagnostics = ("traffic-ambiguous",)
    else:
        state = TrafficMatchState.MATCHED
        diagnostics = ()
    distances = [
        distance
        for item in ordered
        if candidate_geometry is not None
        for distance in (_point_distance(item, candidate_geometry),)
        if distance is not None
    ]
    proof: dict[str, object] = {
        "contract": "satn-dft-traffic-match-proof/v1",
        "policy_fingerprint": policy.fingerprint,
        "evidence_state_fingerprint": evidence_state_fingerprint,
        "coverage_state_fingerprint": evidence_state_fingerprint,
        "method": method,
        "candidate_count": len(ordered),
        "count_point_ids": sorted({item.count_point_id for item in ordered}),
        "observation_ids": [item.observation_id for item in ordered],
        "source_export_fingerprints": sorted(
            {item.source_export_fingerprint for item in ordered}
        ),
        "row_fingerprints": sorted({item.row_fingerprint for item in ordered}),
        "distance_m": min(distances) if distances else None,
    }
    state_fingerprint = _state_fingerprint(
        policy=policy,
        state=state,
        proof=proof,
        observations=ordered,
        evidence_state_fingerprint=evidence_state_fingerprint,
    )
    annotated = tuple(
        _annotate(
            item,
            state=state,
            proof=proof,
            state_fingerprint=state_fingerprint,
        )
        for item in ordered
    )
    return TrafficMatchResult(
        observations=annotated,
        match_state=state,
        match_proof=proof,
        state_fingerprint=state_fingerprint,
        diagnostics=diagnostics,
    )


# Short spelling for callers that already use the traffic evidence vocabulary.
match_traffic_observations = match_dft_traffic
