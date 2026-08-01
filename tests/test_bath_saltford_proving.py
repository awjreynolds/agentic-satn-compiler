"""Synthetic Bath-Saltford PSA proving boundary for issues #130 and #137.

The fixture deliberately proves what the compiler can and cannot currently do.
It must never be treated as a route design, safety assessment or current B&NES
dataset.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import geopandas as gpd
from bath_saltford_fixture import configured_bath_saltford

from satn.agents import FakeAgentRuntime
from satn.compiler import compile_network
from satn.education_access import (
    AccessPointStatus,
    ConnectorContinuity,
    MeasuredDistance,
    SchoolAccessEvidence,
)
from satn.filesystem_safety import publication_destination_authority
from satn.population_reach import compile_population_reach
from satn.psa_evidence_loaders import (
    assess_education_access_from_evidence,
    load_education_access_evidence,
    load_population_reach_evidence,
)
from satn.publisher import publish
from satn.sources import load_snapshot, snapshot

PROJECT = Path(__file__).parents[1]
RESULT = PROJECT / "docs" / "research" / "bath-saltford-proving-fixture.json"


def _route_options(source: dict[str, gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    network = source["network"]
    by_id = network.set_index("source_id")
    return gpd.GeoDataFrame(
        {
            "option_id": ("a4-corridor", "railway-path", "railway-path"),
            "geometry": (
                by_id.loc["a4-bath-saltford-forward"].geometry,
                by_id.loc["railway-east-forward"].geometry,
                by_id.loc["railway-west-forward"].geometry,
            ),
        },
        geometry="geometry",
        crs=network.crs,
    )


def _access(option_id: str, school_id: str) -> SchoolAccessEvidence:
    return SchoolAccessEvidence(
        option_id=option_id,
        school_id=school_id,
        connector_distance=MeasuredDistance(distance_m=120),
        connector_continuity=ConnectorContinuity.CONTINUOUS,
        access_point_status=AccessPointStatus.MAPPED,
        destination_distance=MeasuredDistance(distance_m=800),
        access_evidence_ids=(f"{option_id}-{school_id}-entrance",),
        support_evidence_ids=(f"{option_id}-{school_id}-continuity",),
    )


def test_bath_saltford_fixture_records_evidence_then_exposes_current_psa_boundary(
    tmp_path: Path,
) -> None:
    """Use snapshot and strict governed loaders before declaring the PSA blocker."""

    started = time.perf_counter()
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    source = load_snapshot(config)

    population = load_population_reach_evidence(
        config.source.population_reach_evidence,
        base_directory=config.config_path.parent,
        pwc_outside_tolerance_m=0,
    )
    education = load_education_access_evidence(
        config.source.school_register_evidence,
        config.source.strategic_education_destination_admissions,
        base_directory=config.config_path.parent,
        as_at=config.source.network_selection_as_at,
        school_register_max_age_days=config.source.network_selection_school_register_max_age_days,
        strategic_admissions_max_age_days=(
            config.source.network_selection_strategic_admissions_max_age_days
        ),
    )
    assert population is not None and education is not None

    reach = compile_population_reach(
        _route_options(source),
        population.output_areas,
        source["boundary"],
        source=population.source,
        columns=population.columns,
    )
    summaries = {(item.option_id, item.corridor_distance_m): item for item in reach.summaries}
    assert summaries[("a4-corridor", 500.0)].total_residents == 820
    assert summaries[("railway-path", 500.0)].total_residents == 620
    assert summaries[("a4-corridor", 500.0)].shared_residents == 420
    assert summaries[("railway-path", 500.0)].option_exclusive_residents == 200
    assert summaries[("railway-path", 1000.0)].total_residents == 1420
    assert reach.sensitivities[1].ordering_flips_from_first_distance
    assert reach.sensitivities[1].sensitive

    assessed = assess_education_access_from_evidence(
        education,
        option_ids=("a4-corridor", "railway-path"),
        option_evidence=(
            _access("a4-corridor", "saltford-secondary"),
            _access("a4-corridor", "bath-edge-primary"),
            _access("railway-path", "saltford-secondary"),
            _access("railway-path", "bath-edge-primary"),
        ),
    ).assessment
    destination_ids = {
        item.strategic_destination_id for item in assessed.strategic_education_destinations
    }
    assert destination_ids == {"bath-spa-university"}
    # The raw education adapter receives no caller-created destination access
    # assertion. Both comparison labels therefore retain an explicit gap until
    # the compiler derives an exact, role-typed current-graph relationship.
    assert {
        (item.option_id, item.strategic_destination_id)
        for item in assessed.network_gaps
        if item.gap_kind == "strategic-education-destination"
    } == {
        ("a4-corridor", "bath-spa-university"),
        ("railway-path", "bath-spa-university"),
    }
    assert all(
        item.status.value == "network-gap"
        for item in assessed.strategic_education_destination_access
    )
    # No independent-travel evidence was supplied.  That remains a bounded
    # evidence caveat, never an implied safety or independent-access finding.
    assert all(
        item.status.value == "evidence-required"
        for item in assessed.independent_travel_opportunities
    )

    compiled = compile_network(config, source, FakeAgentRuntime())
    preparation = compiled.spine_access_candidate_preparation
    assert preparation is not None and preparation.prepared
    assert not preparation.prepared_spine_access_connections
    assert not preparation.connection_roster
    associations = compiled.access_obligations[
        compiled.access_obligations["place_id"].isin({"bath-edge", "saltford"})
    ]
    assert set(associations["service_status"]) == {"served"}
    assert associations["access_connection_id"].isna().all()
    strategic = compiled.strategic_corridor_preparation
    assert strategic is not None and strategic.prepared
    assert {item.unit_role.value for item in strategic.units} == {
        "interurban-spine",
        "strategic-destination-access",
    }
    units = {item.unit_role.value: item for item in strategic.units}
    interurban = units["interurban-spine"]
    destination = units["strategic-destination-access"]
    assert not interurban.anchor_connection_ids
    assert set(interurban.anchor_obligation_ids) == set(associations["obligation_id"])
    artifacts = publish(
        config,
        compiled,
        "backbone-access-association-proof",
        publication_authority=publication_destination_authority(
            workspace_root=tmp_path,
        ),
    )
    published = json.loads(artifacts["geojson"].read_text(encoding="utf-8"))
    published_associations = [
        feature
        for feature in published["features"]
        if feature["properties"].get("feature_type") == "access-obligation"
        and json.loads(feature["properties"].get("provenance", "{}")).get(
            "service_kind"
        )
        == "backbone-access-association"
    ]
    assert {
        feature["properties"]["place_id"] for feature in published_associations
    } == {"bath-edge", "saltford"}
    assert {
        json.loads(feature["properties"]["provenance"])["association_kind"]
        for feature in published_associations
    } == {"colocated-direct-strategic-spine"}
    assert {
        feature["geometry"]["type"] for feature in published_associations
    } == {"Point"}
    railway = next(
        record
        for record in interurban.candidate_records
        if record.candidate.source_class.value == "verified-existing-asset"
    )
    assert {
        railway.routing_edge_ids,
        railway.reverse_routing_edge_ids,
    } == {
        ("railway-west-reverse", "railway-east-reverse"),
        ("railway-east-forward", "railway-west-forward"),
    }
    assert not railway.candidate.served_strategic_destination_ids
    assert destination.candidate_records[0].routing_edge_ids == (
        "a4-campus-forward",
    )
    assert destination.candidate_records[0].reverse_routing_edge_ids == (
        "a4-campus-reverse",
    )
    assert destination.candidate_records[0].candidate.served_strategic_destination_ids == (
        "bath-spa-university",
    )
    assert time.perf_counter() - started > 0

    result = json.loads(RESULT.read_text(encoding="utf-8"))
    assert (
        result["status"]
        == "strategic-reference-production-criteria-and-adoption-proven-application-blocked"
    )
    assert result["benchmark"]["candidate_count"] == 3
    assert (
        result["implementation_gap"]["id"]
        == "strategic-reference-application-replay-and-publication"
    )
    assert "14.1" in result["implementation_gap"]["blocking_acceptance"]
