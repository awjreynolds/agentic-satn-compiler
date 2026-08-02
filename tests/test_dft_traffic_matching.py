from __future__ import annotations

import hashlib

import pytest
from shapely.geometry import Point

from satn.dft_traffic_matching import TrafficMatchPolicy, match_dft_traffic
from satn.traffic_evidence import TrafficMatchState, TrafficObservation


def _observation(
    observation_id: str,
    count_point_id: str,
    *,
    value: int = 100,
    easting: float | None = 0,
    northing: float | None = 0,
    direction: str | None = None,
) -> TrafficObservation:
    return TrafficObservation(
        observation_id=observation_id,
        source_export_fingerprint="a" * 64,
        source_layer="aadf",
        count_point_id=count_point_id,
        observation_year=2024,
        direction_of_travel=direction,
        easting=easting,
        northing=northing,
        declared_crs="EPSG:27700" if easting is not None else None,
        all_motor_vehicles=value,
        row_fingerprint=hashlib.sha256(observation_id.encode()).hexdigest(),
    )


def _policy() -> TrafficMatchPolicy:
    return TrafficMatchPolicy(policy_id="test-traffic", version="1", route_buffer_m=20)


def test_distinct_nearby_count_points_are_ambiguous_and_retained() -> None:
    observations = (
        _observation("row-1", "CP1", easting=0),
        _observation("row-2", "CP2", easting=10),
    )

    result = match_dft_traffic(
        observations,
        policy=_policy(),
        candidate_geometry=Point(0, 0),
        evidence_state_fingerprint="b" * 64,
    )

    assert result.match_state is TrafficMatchState.AMBIGUOUS
    assert {item.count_point_id for item in result.observations} == {"CP1", "CP2"}
    assert result.match_proof["evidence_state_fingerprint"] == "b" * 64
    assert all(item.match_state is TrafficMatchState.AMBIGUOUS for item in result.observations)


def test_same_claim_disagreement_is_conflicting() -> None:
    result = match_dft_traffic(
        (
            _observation("row-1", "CP1", value=100),
            _observation("row-2", "CP1", value=200),
        ),
        policy=_policy(),
        count_point_id="CP1",
    )

    assert result.match_state is TrafficMatchState.CONFLICTING
    assert len(result.observations) == 2
    assert all(item.match_state is TrafficMatchState.CONFLICTING for item in result.observations)


def test_identical_duplicate_rows_for_one_claim_are_matched() -> None:
    result = match_dft_traffic(
        (
            _observation("row-1", "CP1"),
            _observation("row-2", "CP1"),
        ),
        policy=_policy(),
        count_point_id="CP1",
    )

    assert result.match_state is TrafficMatchState.MATCHED
    assert len(result.observations) == 2


def test_missing_point_geometry_is_unknown_and_raw_counts_are_not_allowed() -> None:
    result = match_dft_traffic(
        (_observation("row-1", "CP1", easting=None, northing=None),),
        policy=_policy(),
        candidate_geometry=Point(0, 0),
    )
    assert result.match_state is TrafficMatchState.UNKNOWN
    assert result.observations == ()

    with pytest.raises(ValueError, match="source_layers"):
        TrafficMatchPolicy(
            policy_id="raw", version="1", route_buffer_m=20, source_layers=("raw-counts",)
        )


def test_coverage_state_is_bound_into_match_state_fingerprint() -> None:
    observation = (_observation("row-1", "CP1"),)
    first = match_dft_traffic(
        observation,
        policy=_policy(),
        count_point_id="CP1",
        evidence_state_fingerprint="b" * 64,
    )
    second = match_dft_traffic(
        observation,
        policy=_policy(),
        count_point_id="CP1",
        evidence_state_fingerprint="c" * 64,
    )
    assert first.state_fingerprint != second.state_fingerprint
