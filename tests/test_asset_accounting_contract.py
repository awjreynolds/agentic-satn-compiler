"""Focused evidence-contract tests for reusable asset accounting."""

from __future__ import annotations

from types import SimpleNamespace

import geopandas as gpd
from shapely.geometry import LineString

from satn.asset_accounting import build_asset_accounting
from satn.network_selection import TrafficProfileConfig
from satn.traffic_evidence import TrafficFreshnessState


def _frame(rows: list[dict[str, object]], *, crs: int = 4326) -> gpd.GeoDataFrame:
    if not rows:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


def test_raw_cycleway_is_retained_without_claiming_a_governed_cycle_track() -> None:
    network = _frame(
        [
            {
                "source_id": "osm-cycleway",
                "highway": "cycleway",
                "parser_contract": "osm-network/v1",
                "parser_version": "2026-08-02",
                "geometry": LineString([(-2.5, 51.4), (-2.49, 51.4)]),
            }
        ]
    )

    accounting = build_asset_accounting(_frame([]), network, None)

    assert accounting["asset_count"] == 1
    asset = accounting["records"][0]
    assert asset["asset_kind"] == "mapped-cycleway"
    assert asset["intervention_state"] == "upgrade-required"
    observation = asset["source_provenance"][0]
    assert observation["raw_attributes"] == {"highway": "cycleway"}
    assert observation["parser_contract"] == "osm-network/v1"
    assert observation["parser_version"] == "2026-08-02"
    assert observation["observation_state"] == "provisional"
    assert accounting["excluded_observations"] == []


def test_supported_claim_without_authoritative_lineage_remains_provisional() -> None:
    context = _frame(
        [
            {
                "source_id": "unverified-greenway",
                "feature_type": "greenway-cycleway",
                "evidence_state": "supported",
                "geometry": LineString([(-2.5, 51.4), (-2.49, 51.4)]),
            }
        ]
    )

    accounting = build_asset_accounting(context, _frame([]), None)

    evidence = accounting["records"][0]["source_provenance"][0]
    assert evidence["observation_state"] == "provisional"


def test_aggregate_conflict_keeps_conflicting_observation_visible() -> None:
    geometry = LineString([(-2.5, 51.4), (-2.49, 51.4)])
    authoritative = {
        "source_id": "authority",
        "feature_type": "greenway-cycleway",
        "evidence_state": "supported",
        "claim_type": "cycling-access",
        "source_family": "governed-cycleway",
        "dataset": "cycleway-register",
        "publisher": "Example authority",
        "source_authority_role": "custodian",
        "effective_date": "2026-08-01",
        "licence": "Open Government Licence v3.0",
        "source_export_sha256": "a" * 64,
        "evidence_mode": "observed",
        "coverage_state": "complete",
        "ingestion_contract": "satn-cycleway/v1",
        "geometry": geometry,
    }
    conflicting = dict(authoritative, source_id="conflict", evidence_state="conflicting")

    accounting = build_asset_accounting(
        _frame([authoritative, conflicting]), _frame([]), None
    )

    record = accounting["records"][0]
    assert record["evidence_state"] == "conflicting"
    assert record["conflict_roster"] == ["conflict"]
    assert {item["source_id"] for item in record["source_provenance"]} == {
        "authority",
        "conflict",
    }


def test_asset_identity_and_serialized_geometry_are_source_order_independent() -> None:
    geometry = LineString([(-2.5, 51.4), (-2.49, 51.4)])

    def compile_order(rows: list[dict[str, object]]) -> dict[str, object]:
        return build_asset_accounting(_frame(rows), _frame([]), None)

    first = compile_order(
        [
            {"source_id": "greenway", "feature_type": "greenway-cycleway", "geometry": geometry},
            {"source_id": "ncn", "feature_type": "ncn-route", "geometry": geometry},
        ]
    )
    second = compile_order(
        [
            {"source_id": "ncn", "feature_type": "ncn-route", "geometry": geometry},
            {"source_id": "greenway", "feature_type": "greenway-cycleway", "geometry": geometry},
        ]
    )

    assert first == second


def test_canonicalization_failure_does_not_fallback_to_raw_geometry_hash() -> None:
    network = _frame(
        [
            {
                "source_id": "collapsed-line",
                "highway": "unclassified",
                "geometry": LineString([(0.0, 0.0), (0.0001, 0.0001)]),
            }
        ],
        crs=27700,
    )

    accounting = build_asset_accounting(_frame([], crs=27700), network, None)

    observation = accounting["excluded_observations"][0]
    assert observation["accounting_disposition"] == "excluded-invalid-geometry"
    assert observation["geometry_sha256"] is None
    assert observation["evidence_geometry_fingerprint"] is None


def test_configured_freshness_cannot_be_applied_without_an_as_at_year() -> None:
    profile = TrafficProfileConfig.model_construct(
        profile_id="freshness-bypass",
        version="1",
        thresholds=(),
        high_traffic_challenge_band="high",
        max_observation_age_years=3,
        as_at_year=None,
    )

    try:
        profile.freshness_for(2024, TrafficFreshnessState.FRESH)
    except ValueError as error:
        assert "as_at_year" in str(error)
    else:  # pragma: no cover - assertion keeps the contract explicit
        raise AssertionError("unbound freshness policy was silently applied")


def test_candidate_participation_uses_final_reviewable_selection_dispositions() -> None:
    geometry = LineString([(-2.5, 51.4), (-2.49, 51.4)])
    context = _frame(
        [
            {
                "source_id": "asset-x",
                "feature_type": "greenway-cycleway",
                "geometry": geometry,
            }
        ]
    )

    def candidate(candidate_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            candidate_id=candidate_id,
            topology_state="satisfied",
            governed_evidence_ids=("asset-x",),
            provenance_ids=(),
            network_role="community-access",
        )

    selected = candidate("candidate-selected")
    complementary = candidate("candidate-complementary")
    unselected = candidate("candidate-unselected")
    candidate_set = SimpleNamespace(
        candidate_set_id="candidate-set-1",
        candidate_set_fingerprint="a" * 64,
        network_role="community-access",
        admissions=tuple(
            SimpleNamespace(candidate_id=item.candidate_id, disposition="admitted")
            for item in (selected, complementary, unselected)
        ),
    )
    records = tuple(
        SimpleNamespace(
            candidate=item,
            source_ids=(),
            evidence_ids=(),
            preparation_disposition="retained-representative",
        )
        for item in (selected, complementary, unselected)
    )
    reviewable = SimpleNamespace(
        result_fingerprint="b" * 64,
        effective_selections=(
            SimpleNamespace(
                candidate_set_id="candidate-set-1",
                candidate_id="candidate-selected",
                officer_decision_id="officer-1",
            ),
        ),
        scenario=SimpleNamespace(
            scenario_fingerprint="c" * 64,
            complementary_candidate_ids=("candidate-complementary",),
            selections=(),
        ),
    )
    compiled = SimpleNamespace(
        reviewable_network=reviewable,
        spine_access_candidate_preparation=SimpleNamespace(
            prepared_spine_access_connections=(
                SimpleNamespace(candidate_set=candidate_set, candidate_records=records),
            )
        ),
        strategic_corridor_preparation=None,
    )

    accounting = build_asset_accounting(context, _frame([]), compiled)

    participations = {
        item["candidate_id"]: item
        for item in accounting["records"][0]["candidate_participations"]
    }
    assert participations["candidate-selected"]["selection_disposition"] == "selected"
    assert participations["candidate-selected"]["selection_reason"] == {
        "code": "effective-reviewable-selection",
        "officer_decision_id": "officer-1",
        "reviewable_result_fingerprint": "b" * 64,
    }
    assert (
        participations["candidate-complementary"]["selection_disposition"]
        == "complementary"
    )
    assert participations["candidate-complementary"]["selection_reason"] == {
        "code": "scenario-complementary-selection",
        "scenario_fingerprint": "c" * 64,
    }
    assert (
        participations["candidate-unselected"]["selection_disposition"]
        == "eligible-not-selected"
    )
    assert participations["candidate-unselected"]["selection_reason"]["code"] == (
        "eligible-candidate-not-selected"
    )
