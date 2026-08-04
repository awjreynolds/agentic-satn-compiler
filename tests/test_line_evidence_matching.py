from __future__ import annotations

from decimal import Decimal

import pytest

from satn.line_evidence_matching import (
    BANES_LINE_MATCH_SENSITIVITY_V1,
    LineAggregationProfile,
    LineEvidenceRecord,
    LineMatchProfile,
    NumericObservation,
    TargetLineRecord,
    aggregate_line_evidence,
    match_line_evidence,
)


def _source(source_id: str, wkt: str) -> LineEvidenceRecord:
    return LineEvidenceRecord(
        source_id=source_id,
        geometry_wkt=wkt,
        geometry_crs="EPSG:27700",
        evidence_fingerprint=(source_id[0] * 64),
    )


def _profile() -> LineMatchProfile:
    return LineMatchProfile(
        profile_id="banes-line-match-trial-v1",
        version=1,
        canonical_crs="EPSG:27700",
        distance_tolerance_m=15,
        bearing_tolerance_degrees=35,
        minimum_shared_length_m=10,
        orientation_policy="insensitive",
        ambiguity_policy="retain-conflict",
    )


def test_matching_retains_exact_parallel_reversed_and_unmatched_evidence() -> None:
    target = TargetLineRecord(
        target_id="target-100m",
        geometry_wkt="LINESTRING (0 0, 100 0)",
        geometry_crs="EPSG:27700",
        evidence_fingerprint="f" * 64,
    )
    sources = (
        _source("a-exact-30m", "LINESTRING (0 0, 30 0)"),
        _source("b-parallel-70m", "LINESTRING (30 5, 100 5)"),
        _source("c-crossing", "LINESTRING (50 -20, 50 20)"),
        _source("d-distant", "LINESTRING (0 50, 30 50)"),
        _source("e-reversed-30m", "LINESTRING (30 0, 0 0)"),
    )

    result = match_line_evidence(sources, (target,), _profile())
    by_source = {record.source_id: record for record in result.records}

    assert result.canonical_crs == "EPSG:27700"
    assert by_source["a-exact-30m"].state == "accepted"
    assert by_source["a-exact-30m"].shared_length_m == 30
    assert by_source["a-exact-30m"].source_coverage_fraction == 1
    assert by_source["a-exact-30m"].target_coverage_fraction == 0.3
    assert by_source["b-parallel-70m"].distance_m == 5
    assert by_source["b-parallel-70m"].shared_length_m == 70
    assert by_source["b-parallel-70m"].target_coverage_fraction == 0.7
    assert by_source["e-reversed-30m"].orientation == "reversed"
    assert by_source["c-crossing"].state == "unmatched"
    assert by_source["c-crossing"].reason == "bearing-outside-tolerance"
    assert by_source["d-distant"].state == "unmatched"
    assert by_source["d-distant"].reason == "distance-outside-tolerance"

    reversed_result = match_line_evidence(tuple(reversed(sources)), (target,), _profile())
    assert reversed_result.records == result.records
    assert reversed_result.result_fingerprint == result.result_fingerprint


def test_extensive_values_are_apportioned_by_source_shared_length() -> None:
    target = TargetLineRecord(
        target_id="target-100m",
        geometry_wkt="LINESTRING (0 0, 100 0)",
        geometry_crs="EPSG:27700",
        evidence_fingerprint="f" * 64,
    )
    matches = match_line_evidence(
        (
            _source("a-exact-30m", "LINESTRING (0 0, 30 0)"),
            _source("b-parallel-70m", "LINESTRING (30 5, 100 5)"),
        ),
        (target,),
        _profile(),
    )
    observations = (
        NumericObservation(
            observation_id="flow-a",
            source_id="a-exact-30m",
            claim="motor-flow",
            value_decimal="10",
            evidence_fingerprint="1" * 64,
        ),
        NumericObservation(
            observation_id="flow-b",
            source_id="b-parallel-70m",
            claim="motor-flow",
            value_decimal="20",
            evidence_fingerprint="2" * 64,
        ),
    )

    result = aggregate_line_evidence(
        matches,
        observations,
        LineAggregationProfile(
            profile_id="motor-flow-extensive-v1",
            version=1,
            law="extensive",
            claim="motor-flow",
        ),
    )

    assert result.targets[0].target_id == "target-100m"
    assert result.canonical_crs == "EPSG:27700"
    assert result.targets[0].state == "available"
    assert result.targets[0].value == Decimal("17")
    assert result.targets[0].observation_ids == ("flow-a", "flow-b")


def test_contradictory_duplicate_observations_remain_conflicts() -> None:
    target = TargetLineRecord(
        target_id="target-100m",
        geometry_wkt="LINESTRING (0 0, 100 0)",
        geometry_crs="EPSG:27700",
        evidence_fingerprint="f" * 64,
    )
    matches = match_line_evidence(
        (_source("a-exact-30m", "LINESTRING (0 0, 30 0)"),),
        (target,),
        _profile(),
    )
    observations = tuple(
        NumericObservation(
            observation_id=observation_id,
            source_id="a-exact-30m",
            claim="motor-flow",
            value_decimal=value,
            evidence_fingerprint=fingerprint,
        )
        for observation_id, value, fingerprint in (
            ("flow-a-10", "10", "3" * 64),
            ("flow-a-12", "12", "4" * 64),
        )
    )

    result = aggregate_line_evidence(
        matches,
        observations,
        LineAggregationProfile(
            profile_id="motor-flow-extensive-v1",
            version=1,
            law="extensive",
            claim="motor-flow",
        ),
    )

    assert result.targets[0].state == "conflicting"
    assert result.targets[0].value is None
    assert result.conflicts[0].observation_ids == ("flow-a-10", "flow-a-12")
    assert result.conflicts[0].value_decimals == ("10", "12")


def test_invalid_source_geometry_is_typed_and_does_not_abort_valid_matches() -> None:
    target = TargetLineRecord(
        target_id="target-100m",
        geometry_wkt="LINESTRING (0 0, 100 0)",
        geometry_crs="EPSG:27700",
        evidence_fingerprint="f" * 64,
    )
    invalid = LineEvidenceRecord(
        source_id="invalid-source",
        geometry_wkt="not-wkt",
        geometry_crs="EPSG:27700",
        evidence_fingerprint="5" * 64,
    )

    result = match_line_evidence(
        (invalid, _source("a-exact-30m", "LINESTRING (0 0, 30 0)")),
        (target,),
        _profile(),
    )

    assert {record.source_id: record.state for record in result.records} == {
        "a-exact-30m": "accepted",
        "invalid-source": "unmatched",
    }
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["invalid-source-geometry"]


def test_only_declared_aggregation_laws_are_available() -> None:
    target = TargetLineRecord(
        target_id="target-100m",
        geometry_wkt="LINESTRING (0 0, 100 0)",
        geometry_crs="EPSG:27700",
        evidence_fingerprint="f" * 64,
    )
    matches = match_line_evidence(
        (
            _source("a-exact-30m", "LINESTRING (0 0, 30 0)"),
            _source("b-parallel-70m", "LINESTRING (30 5, 100 5)"),
        ),
        (target,),
        _profile(),
    )
    observations = (
        NumericObservation(
            observation_id="value-a",
            source_id="a-exact-30m",
            claim="test-value",
            value_decimal="10",
            category="preferred",
            evidence_fingerprint="6" * 64,
        ),
        NumericObservation(
            observation_id="value-b",
            source_id="b-parallel-70m",
            claim="test-value",
            value_decimal="20",
            category="other",
            evidence_fingerprint="7" * 64,
        ),
    )

    expectations = (
        ("intensive", Decimal("17"), False, None),
        ("maximum", Decimal("20"), True, None),
        ("minimum", Decimal("10"), True, None),
        ("categorical-proportion", Decimal("0.3"), False, "preferred"),
    )
    for law, expected, extrema_permitted, category in expectations:
        result = aggregate_line_evidence(
            matches,
            observations,
            LineAggregationProfile(
                profile_id=f"test-{law}-v1",
                version=1,
                law=law,
                claim="test-value",
                category=category,
                extrema_permitted=extrema_permitted,
            ),
        )
        assert result.targets[0].value == expected

    with pytest.raises(ValueError, match="explicitly permitted schema"):
        LineAggregationProfile(
            profile_id="invalid-max-v1",
            version=1,
            law="maximum",
            claim="test-value",
        )


def test_ambiguity_and_sensitivity_profiles_remain_explicit() -> None:
    source = _source("a-exact-30m", "LINESTRING (0 0, 30 0)")
    targets = tuple(
        TargetLineRecord(
            target_id=target_id,
            geometry_wkt="LINESTRING (0 0, 100 0)",
            geometry_crs="EPSG:27700",
            evidence_fingerprint=fingerprint,
        )
        for target_id, fingerprint in (("target-a", "8" * 64), ("target-b", "9" * 64))
    )

    result = match_line_evidence((source,), targets, _profile())

    assert len(result.ambiguous) == 2
    assert result.accepted == ()
    assert {record.target_id for record in result.ambiguous} == {"target-a", "target-b"}
    assert [profile.distance_tolerance_m for profile in BANES_LINE_MATCH_SENSITIVITY_V1] == [
        5,
        15,
        25,
    ]


def test_profile_quantization_cannot_hide_a_behavior_change() -> None:
    base = _profile()
    submillimetre = LineMatchProfile(
        profile_id=base.profile_id,
        version=base.version,
        canonical_crs=base.canonical_crs,
        distance_tolerance_m=15.0004,
        bearing_tolerance_degrees=35.0004,
        minimum_shared_length_m=10.0004,
        orientation_policy=base.orientation_policy,
        ambiguity_policy=base.ambiguity_policy,
    )
    target = TargetLineRecord(
        target_id="target",
        geometry_wkt="LINESTRING (0 0, 100 0)",
        geometry_crs="EPSG:27700",
        evidence_fingerprint="a" * 64,
    )
    source = _source("f-near-boundary", "LINESTRING (0 15.0002, 30 15.0002)")

    assert submillimetre.distance_tolerance_m == 15
    assert submillimetre.fingerprint == base.fingerprint
    assert match_line_evidence((source,), (target,), submillimetre).records == (
        match_line_evidence((source,), (target,), base).records[0],
    )


def test_duplicate_source_identity_is_an_explicit_conflict() -> None:
    target = TargetLineRecord(
        target_id="target",
        geometry_wkt="LINESTRING (0 0, 100 0)",
        geometry_crs="EPSG:27700",
        evidence_fingerprint="a" * 64,
    )
    sources = (
        _source("b-duplicate", "LINESTRING (0 0, 30 0)"),
        LineEvidenceRecord(
            source_id="b-duplicate",
            geometry_wkt="LINESTRING (0 5, 30 5)",
            geometry_crs="EPSG:27700",
            evidence_fingerprint="b" * 64,
        ),
    )

    result = match_line_evidence(sources, (target,), _profile())

    assert len(result.conflicting) == 1
    assert result.conflicting[0].reason == "duplicate-source-id"
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["duplicate-source-id"]
    assert (
        match_line_evidence(tuple(reversed(sources)), (target,), _profile()).result_fingerprint
        == result.result_fingerprint
    )
    changed_geometry = (
        sources[0],
        LineEvidenceRecord(
            source_id="b-duplicate",
            geometry_wkt="LINESTRING (0 6, 30 6)",
            geometry_crs="EPSG:27700",
            evidence_fingerprint="b" * 64,
        ),
    )
    assert (
        match_line_evidence(changed_geometry, (target,), _profile()).result_fingerprint
        != result.result_fingerprint
    )


def test_quantized_measurements_govern_inclusive_boundary_decisions() -> None:
    target = TargetLineRecord(
        target_id="target",
        geometry_wkt="LINESTRING (0 0, 100 0)",
        geometry_crs="EPSG:27700",
        evidence_fingerprint="a" * 64,
    )
    sources = (
        _source("c-distance-rounds-to-boundary", "LINESTRING (0 15.000001, 30 15.000001)"),
        _source("d-length-rounds-to-boundary", "LINESTRING (0 0, 9.999999 0)"),
    )

    result = match_line_evidence(sources, (target,), _profile())

    assert {record.source_id: (record.state, record.reason) for record in result.records} == {
        "c-distance-rounds-to-boundary": ("accepted", "within-profile"),
        "d-length-rounds-to-boundary": ("accepted", "within-profile"),
    }
    assert {record.distance_mm for record in result.records} <= {0, 15_000}
    assert {record.shared_length_mm for record in result.records} == {10_000, 30_000}


def test_duplicate_target_identity_is_order_stable_and_explicit() -> None:
    targets = (
        TargetLineRecord(
            target_id="duplicate-target",
            geometry_wkt="LINESTRING (0 0, 100 0)",
            geometry_crs="EPSG:27700",
            evidence_fingerprint="a" * 64,
        ),
        TargetLineRecord(
            target_id="duplicate-target",
            geometry_wkt="LINESTRING (0 5, 100 5)",
            geometry_crs="EPSG:27700",
            evidence_fingerprint="b" * 64,
        ),
    )
    source = _source("a-exact-30m", "LINESTRING (0 0, 30 0)")

    result = match_line_evidence((source,), targets, _profile())
    reversed_result = match_line_evidence((source,), tuple(reversed(targets)), _profile())

    assert result.records == reversed_result.records
    assert result.result_fingerprint == reversed_result.result_fingerprint
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["duplicate-target-id"]


def test_runtime_policy_values_are_validated() -> None:
    with pytest.raises(ValueError, match="orientation policy"):
        LineMatchProfile(
            profile_id="invalid-orientation-v1",
            version=1,
            canonical_crs="EPSG:27700",
            distance_tolerance_m=15,
            bearing_tolerance_degrees=35,
            minimum_shared_length_m=10,
            orientation_policy="bad",  # type: ignore[arg-type]
            ambiguity_policy="retain-conflict",
        )

    with pytest.raises(ValueError, match="aggregation law"):
        LineAggregationProfile(
            profile_id="invalid-law-v1",
            version=1,
            law="bogus",  # type: ignore[arg-type]
            claim="motor-flow",
        )


def test_aggregation_fingerprint_binds_observation_evidence() -> None:
    target = TargetLineRecord(
        target_id="target",
        geometry_wkt="LINESTRING (0 0, 100 0)",
        geometry_crs="EPSG:27700",
        evidence_fingerprint="a" * 64,
    )
    matches = match_line_evidence(
        (_source("a-exact-30m", "LINESTRING (0 0, 30 0)"),),
        (target,),
        _profile(),
    )
    profile = LineAggregationProfile(
        profile_id="motor-flow-extensive-v1",
        version=1,
        law="extensive",
        claim="motor-flow",
    )

    def aggregate(fingerprint: str):
        return aggregate_line_evidence(
            matches,
            (
                NumericObservation(
                    observation_id="flow-a",
                    source_id="a-exact-30m",
                    claim="motor-flow",
                    value_decimal="10",
                    evidence_fingerprint=fingerprint,
                ),
            ),
            profile,
        )

    first = aggregate("c" * 64)
    second = aggregate("d" * 64)

    assert first.result_fingerprint != second.result_fingerprint
    assert first.targets[0].observation_evidence_fingerprints == ("c" * 64,)


def test_equivalent_duplicate_observations_retain_all_provenance_once() -> None:
    target = TargetLineRecord(
        target_id="target",
        geometry_wkt="LINESTRING (0 0, 100 0)",
        geometry_crs="EPSG:27700",
        evidence_fingerprint="a" * 64,
    )
    matches = match_line_evidence(
        (_source("a-exact-30m", "LINESTRING (0 0, 30 0)"),),
        (target,),
        _profile(),
    )
    observations = tuple(
        NumericObservation(
            observation_id=observation_id,
            source_id="a-exact-30m",
            claim="motor-flow",
            value_decimal="10",
            evidence_fingerprint=fingerprint,
        )
        for observation_id, fingerprint in (
            ("flow-a-primary", "c" * 64),
            ("flow-a-corroborating", "d" * 64),
        )
    )

    result = aggregate_line_evidence(
        matches,
        observations,
        LineAggregationProfile(
            profile_id="motor-flow-extensive-v1",
            version=1,
            law="extensive",
            claim="motor-flow",
        ),
    )

    assert result.targets[0].value == Decimal("3")
    assert result.targets[0].observation_ids == (
        "flow-a-corroborating",
        "flow-a-primary",
    )
    assert result.targets[0].observation_evidence_fingerprints == (
        "d" * 64,
        "c" * 64,
    )


def test_duplicate_observation_identity_is_an_order_stable_conflict() -> None:
    target = TargetLineRecord(
        target_id="target",
        geometry_wkt="LINESTRING (0 0, 100 0)",
        geometry_crs="EPSG:27700",
        evidence_fingerprint="a" * 64,
    )
    matches = match_line_evidence(
        (_source("a-exact-30m", "LINESTRING (0 0, 30 0)"),),
        (target,),
        _profile(),
    )
    observations = tuple(
        NumericObservation(
            observation_id="duplicate-observation",
            source_id="a-exact-30m",
            claim="motor-flow",
            value_decimal="10",
            evidence_fingerprint=fingerprint,
        )
        for fingerprint in ("c" * 64, "d" * 64)
    )
    profile = LineAggregationProfile(
        profile_id="motor-flow-extensive-v1",
        version=1,
        law="extensive",
        claim="motor-flow",
    )

    result = aggregate_line_evidence(matches, observations, profile)
    reversed_result = aggregate_line_evidence(matches, tuple(reversed(observations)), profile)

    assert result.targets[0].state == "conflicting"
    assert result.conflicts[0].reason == "duplicate-observation-id"
    assert result.result_fingerprint == reversed_result.result_fingerprint
