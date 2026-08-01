from __future__ import annotations

import pytest

from satn.strategic_destination_access import (
    AccessEvidence,
    AccessEvidenceKind,
    DestinationSite,
    DestinationSiteKind,
    EvidenceSource,
    StrategicDestinationAccessConfig,
    StrategicDestinationAccessRequest,
    compile_strategic_destination_access,
)


def test_one_evidenced_entrance_to_any_strategic_edge_serves_a_physical_fe_site() -> None:
    result = compile_strategic_destination_access(
        StrategicDestinationAccessRequest(
            strategic_network_edge_ids=("spine-a-17",),
            sites=(
                DestinationSite(
                    physical_site_id="city-college-campus",
                    name="City College Campus",
                    kind=DestinationSiteKind.FURTHER_EDUCATION,
                    source=EvidenceSource.OFFICIAL,
                    source_record_id="get-city-college",
                ),
            ),
            access_evidence=(
                AccessEvidence(
                    physical_site_id="city-college-campus",
                    entrance_id="south-gate",
                    strategic_network_edge_id="spine-a-17",
                    kind=AccessEvidenceKind.ROUTED_WALKING_PATH,
                    evidence_id="directions-city-college-south-gate",
                ),
            ),
        )
    )

    obligation = result.obligations[0]
    assert obligation.physical_site_id == "city-college-campus"
    assert obligation.service_status == "served-provisional"
    assert obligation.access_point_status == "inferred"
    assert obligation.entrance_ids == ("south-gate",)
    assert obligation.strategic_network_edge_ids == ("spine-a-17",)
    assert obligation.is_nonblocking is False


def test_official_physical_site_record_takes_precedence_over_osm_fallback() -> None:
    result = compile_strategic_destination_access(
        StrategicDestinationAccessRequest(
            strategic_network_edge_ids=("spine-west",),
            sites=(
                DestinationSite(
                    physical_site_id="ruh",
                    name="Royal United Hospital",
                    kind=DestinationSiteKind.ACUTE_HOSPITAL,
                    source=EvidenceSource.OSM,
                    source_record_id="osm-way-101",
                ),
                DestinationSite(
                    physical_site_id="ruh",
                    name="Royal United Hospital NHS Foundation Trust",
                    kind=DestinationSiteKind.ACUTE_HOSPITAL,
                    source=EvidenceSource.OFFICIAL,
                    source_record_id="nhs-acute-17",
                ),
            ),
            access_evidence=(
                AccessEvidence(
                    physical_site_id="ruh",
                    entrance_id="main-gate",
                    strategic_network_edge_id="spine-west",
                    kind=AccessEvidenceKind.OSM_FOOTWAY,
                    evidence_id="osm-footway-501",
                    source=EvidenceSource.OSM,
                ),
            ),
        )
    )

    obligation = result.obligations[0]
    assert obligation.source is EvidenceSource.OFFICIAL
    assert obligation.source_record_id == "nhs-acute-17"
    assert obligation.service_status == "served-provisional"


def test_missing_access_point_evidence_keeps_site_as_nonblocking_explicit_gap() -> None:
    result = compile_strategic_destination_access(
        StrategicDestinationAccessRequest(
            strategic_network_edge_ids=("east-spine",),
            sites=(
                DestinationSite(
                    physical_site_id="university-east-campus",
                    name="University East Campus",
                    kind=DestinationSiteKind.HIGHER_EDUCATION,
                    source=EvidenceSource.OFFICIAL,
                    source_record_id="hesa-east-campus",
                ),
            )
        )
    )

    obligation = result.obligations[0]
    assert obligation.service_status == "network-gap"
    assert obligation.is_nonblocking is True
    assert obligation.entrance_ids == ()
    assert obligation.limitation == "strategic-access-evidence-not-detailed-route-or-survey"


def test_provider_identity_never_collapses_distinct_physical_sites() -> None:
    result = compile_strategic_destination_access(
        StrategicDestinationAccessRequest(
            strategic_network_edge_ids=("east-spine",),
            sites=(
                DestinationSite(
                    physical_site_id="trust-hospital-east",
                    name="Trust Hospital East",
                    kind=DestinationSiteKind.GENERAL_HOSPITAL,
                    source=EvidenceSource.OFFICIAL,
                    source_record_id="nhs-east",
                    provider_id="one-trust",
                ),
                DestinationSite(
                    physical_site_id="trust-hospital-west",
                    name="Trust Hospital West",
                    kind=DestinationSiteKind.GENERAL_HOSPITAL,
                    source=EvidenceSource.OFFICIAL,
                    source_record_id="nhs-west",
                    provider_id="one-trust",
                ),
            ),
            access_evidence=(
                AccessEvidence(
                    physical_site_id="trust-hospital-east",
                    entrance_id="east-gate",
                    strategic_network_edge_id="east-spine",
                    kind=AccessEvidenceKind.PROVIDER_WALKING_PATH,
                    evidence_id="trust-walking-east",
                ),
            ),
        )
    )

    assert [item.physical_site_id for item in result.obligations] == [
        "trust-hospital-east",
        "trust-hospital-west",
    ]
    assert [item.service_status for item in result.obligations] == [
        "served-provisional",
        "network-gap",
    ]


def test_configurable_entrance_threshold_counts_unique_entrances_not_paths() -> None:
    site = DestinationSite(
        physical_site_id="large-secondary-campus",
        name="Large Secondary Campus",
        kind=DestinationSiteKind.FURTHER_EDUCATION,
        source=EvidenceSource.OFFICIAL,
        source_record_id="get-large-campus",
    )
    evidence = (
        AccessEvidence(
            physical_site_id=site.physical_site_id,
            entrance_id="north-gate",
            strategic_network_edge_id="north-spine",
            kind=AccessEvidenceKind.ROUTED_WALKING_PATH,
            evidence_id="walking-north",
        ),
        AccessEvidence(
            physical_site_id=site.physical_site_id,
            entrance_id="north-gate",
            strategic_network_edge_id="north-spine",
            kind=AccessEvidenceKind.OSM_FOOTWAY,
            evidence_id="osm-north",
            source=EvidenceSource.OSM,
        ),
    )
    config = StrategicDestinationAccessConfig(required_evidenced_entrances=2)

    result = compile_strategic_destination_access(
        StrategicDestinationAccessRequest(
            sites=(site,),
            strategic_network_edge_ids=("north-spine",),
            access_evidence=evidence,
            config=config,
        )
    )

    assert result.obligations[0].service_status == "network-gap"
    assert result.config_fingerprint == config.fingerprint


def test_configured_fallback_hierarchy_and_its_fingerprint_are_effective() -> None:
    official = DestinationSite(
        physical_site_id="community-hospital",
        name="Official Community Hospital",
        kind=DestinationSiteKind.MAJOR_COMMUNITY_HOSPITAL,
        source=EvidenceSource.OFFICIAL,
        source_record_id="nhs-community-1",
    )
    osm = official.model_copy(
        update={
            "name": "OSM Community Hospital",
            "source": EvidenceSource.OSM,
            "source_record_id": "osm-node-1",
        }
    )
    prefer_osm = StrategicDestinationAccessConfig(
        source_fallback_hierarchy=(EvidenceSource.OSM, EvidenceSource.OFFICIAL)
    )

    result = compile_strategic_destination_access(
        StrategicDestinationAccessRequest(sites=(official, osm), config=prefer_osm)
    )

    assert result.obligations[0].source_record_id == "osm-node-1"
    assert result.config_fingerprint == prefer_osm.fingerprint


def test_access_evidence_for_an_unknown_physical_site_is_rejected() -> None:
    with pytest.raises(ValueError, match="known physical site"):
        StrategicDestinationAccessRequest(
            sites=(),
            access_evidence=(
                AccessEvidence(
                    physical_site_id="invented-site",
                    entrance_id="invented-gate",
                    strategic_network_edge_id="edge-a",
                    kind=AccessEvidenceKind.ROUTED_WALKING_PATH,
                    evidence_id="invented-evidence",
                ),
            ),
        )


def test_access_evidence_must_terminate_at_a_current_strategic_network_edge() -> None:
    site = DestinationSite(
        physical_site_id="college-campus",
        name="College Campus",
        kind=DestinationSiteKind.FURTHER_EDUCATION,
        source=EvidenceSource.OFFICIAL,
        source_record_id="college-register",
    )
    with pytest.raises(ValueError, match="current strategic network edge"):
        StrategicDestinationAccessRequest(
            sites=(site,),
            strategic_network_edge_ids=("selected-edge",),
            access_evidence=(
                AccessEvidence(
                    physical_site_id=site.physical_site_id,
                    entrance_id="gate",
                    strategic_network_edge_id="invented-edge",
                    kind=AccessEvidenceKind.ROUTED_WALKING_PATH,
                    evidence_id="route-evidence",
                ),
            ),
        )
