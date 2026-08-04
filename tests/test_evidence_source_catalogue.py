from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from satn.evidence_contracts import IngestionContract, SourceExport
from satn.evidence_normalisation import (
    EvidenceObservationCollection,
    EvidenceObservationDraft,
    normalise_source_export,
)
from satn.evidence_source_catalogue import (
    AreaEvidenceScope,
    EvidenceSourceCatalogue,
    EvidenceSourceCatalogueEntry,
    EvidenceSourceResolution,
)
from satn.scenario_compilation import (
    PreparedScenarioCompilationInput,
    compile_prepared_scenario,
)


def source_entry(
    source_id: str,
    *,
    capability: str = "demand-flow",
    source_layer: str,
    coverage_cells: tuple[str, ...],
) -> EvidenceSourceCatalogueEntry:
    return EvidenceSourceCatalogueEntry(
        source_id=source_id,
        source_family=source_id,
        capability=capability,
        source_layer=source_layer,
        publisher=f"{source_id} synthetic publisher",
        effective_date="2026-01-31",
        licence="Open Government Licence v3.0",
        coverage_cells=coverage_cells,
        acquisition_contract=f"{source_id}-acquisition/v1",
        normalisation_contract=f"{source_id}-normalisation/v1",
        permitted_uses=("network-planning",),
        authority_rank=100,
    )


def test_catalogue_resolves_cross_border_sources_by_capability_and_coverage() -> None:
    england = source_entry(
        "england-demand",
        source_layer="england-demand/flows",
        coverage_cells=("ST56",),
    )
    scotland = source_entry(
        "scotland-demand",
        source_layer="scotland-demand/flows",
        coverage_cells=("ST57",),
    )
    irrelevant = source_entry(
        "traffic-counts",
        capability="motor-traffic",
        source_layer="traffic-counts/aadf",
        coverage_cells=("ST56", "ST57"),
    )
    area = AreaEvidenceScope(
        area_id="synthetic-cross-border",
        partition_scheme="bng-10km/v1",
        cells=("ST57", "ST56"),
    )

    first = EvidenceSourceCatalogue((scotland, irrelevant, england)).resolve(
        area=area,
        capability="demand-flow",
        required_use="network-planning",
    )
    reordered = EvidenceSourceCatalogue((england, scotland, irrelevant)).resolve(
        area=area,
        capability="demand-flow",
        required_use="network-planning",
    )

    assert first.state == "complete"
    assert tuple(match.entry.source_id for match in first.matches) == (
        "england-demand",
        "scotland-demand",
    )
    assert tuple(match.matched_cells for match in first.matches) == (("ST56",), ("ST57",))
    assert first.missing_cells == ()
    assert first.evidence_requests == ()
    assert first.fingerprint == reordered.fingerprint
    assert first.canonical_payload() == reordered.canonical_payload()


def source_export(source_family: str, *, content: str) -> SourceExport:
    return SourceExport(
        source_family=source_family,
        dataset=f"{source_family}-dataset",
        layer="flows",
        publisher_release="2026.1",
        effective_date="2026-01-31",
        licence="Open Government Licence v3.0",
        format="GeoJSON",
        declared_crs="EPSG:27700",
        raw_bytes_sha256=content * 64,
        provenance={"retrieved_at": "2026-02-01T00:00:00Z"},
    )


def ingestion_contract(source_family: str, *, implementation: str) -> IngestionContract:
    return IngestionContract(
        source_layer=f"{source_family}/flows",
        contract_version=f"{source_family}-normalisation/v1",
        accepted_schema={"daily_trips": "integer"},
        stable_feature_key_policy="publisher-flow-id/v1",
        selected_attributes=("daily_trips",),
        normalisation={"daily_trips": "integer-trips-per-day"},
        crs_transform={
            "source_crs": "EPSG:27700",
            "target_crs": "EPSG:27700",
            "axis_order": "always_xy",
        },
        partition_scheme="bng-10km/v1",
        spatial_predicate="intersects",
        implementation_dependency_fingerprint=implementation * 64,
    )


def test_normalisation_retains_provenance_and_conflicting_provider_claims() -> None:
    england_export = source_export("england-demand", content="a")
    scotland_export = source_export("scotland-demand", content="b")
    england = normalise_source_export(
        source_export=england_export,
        ingestion_contract=ingestion_contract("england-demand", implementation="c"),
        drafts=(
            EvidenceObservationDraft(
                observation_id="england-flow-1",
                subject_id="cross-border-corridor-1",
                claim="daily-demand-flow",
                value={"trips_per_day": 120},
                coverage_cells=("ST56",),
                observed_at="2026-01-31",
                limitations=("modelled estimate",),
            ),
        ),
    )
    scotland = normalise_source_export(
        source_export=scotland_export,
        ingestion_contract=ingestion_contract("scotland-demand", implementation="d"),
        drafts=(
            EvidenceObservationDraft(
                observation_id="scotland-flow-1",
                subject_id="cross-border-corridor-1",
                claim="daily-demand-flow",
                value={"trips_per_day": 145},
                coverage_cells=("ST57",),
                observed_at="2026-01-31",
                limitations=("modelled estimate",),
            ),
        ),
    )

    collection = EvidenceObservationCollection((*scotland, *england))
    reordered = EvidenceObservationCollection((*england, *scotland))
    claims = collection.observations_for(
        subject_id="cross-border-corridor-1",
        claim="daily-demand-flow",
    )

    assert len(claims) == 2
    assert {claim.value["trips_per_day"] for claim in claims} == {120, 145}
    assert {claim.source_export_fingerprint for claim in claims} == {
        england_export.fingerprint,
        scotland_export.fingerprint,
    }
    assert all(claim.limitations == ("modelled estimate",) for claim in claims)
    assert collection.fingerprint == reordered.fingerprint
    assert collection.canonical_payload() == reordered.canonical_payload()


def test_missing_source_coverage_returns_an_evidence_request_without_failing_resolution() -> None:
    area = AreaEvidenceScope(
        area_id="synthetic-cross-border",
        partition_scheme="bng-10km/v1",
        cells=("ST56", "ST57"),
    )
    england = source_entry(
        "england-demand",
        source_layer="england-demand/flows",
        coverage_cells=("ST56",),
    )

    partial = EvidenceSourceCatalogue((england,)).resolve(
        area=area,
        capability="demand-flow",
        required_use="network-planning",
    )
    unavailable = EvidenceSourceCatalogue(()).resolve(
        area=area,
        capability="motor-traffic",
        required_use="network-planning",
    )

    assert partial.state == "partial"
    assert partial.missing_cells == ("ST57",)
    assert len(partial.evidence_requests) == 1
    assert partial.evidence_requests[0].canonical_payload() == {
        "contract": "satn-source-evidence-request/v1",
        "request_id": partial.evidence_requests[0].request_id,
        "area_id": "synthetic-cross-border",
        "capability": "demand-flow",
        "required_use": "network-planning",
        "partition_scheme": "bng-10km/v1",
        "missing_cells": ["ST57"],
        "reason": "source-coverage-unavailable",
    }
    assert unavailable.state == "unavailable"
    assert unavailable.missing_cells == ("ST56", "ST57")
    assert len(unavailable.evidence_requests) == 1


def test_partial_source_coverage_is_diagnostic_and_never_blocks_compilation() -> None:
    area = AreaEvidenceScope(
        area_id="synthetic-cross-border",
        cells=("ST56", "ST57"),
    )
    resolution = EvidenceSourceCatalogue(
        (
            source_entry(
                "england-demand",
                source_layer="england-demand/flows",
                coverage_cells=("ST56",),
            ),
        )
    ).resolve(
        area=area,
        capability="demand-flow",
        required_use="network-planning",
    )

    result = compile_prepared_scenario(
        None,
        PreparedScenarioCompilationInput(
            area_fingerprint="f" * 64,
            evidence_source_resolutions=(resolution,),
        ),
    )

    assert result.status == "disabled"
    assert result.missing_inputs == ()
    assert result.diagnostics["evidence_source_resolutions"][0]["state"] == "partial"
    assert len(result.diagnostics["evidence_requests"]) == 1
    assert result.diagnostics["evidence_requests"][0]["request_id"] == (
        resolution.evidence_requests[0].request_id
    )
    assert result.diagnostics["evidence_requests"][0]["missing_cells"] == ("ST57",)


def test_resolution_rejects_a_false_complete_state_for_missing_coverage() -> None:
    area = AreaEvidenceScope(area_id="synthetic-area", cells=("ST56",))

    with pytest.raises(ValueError, match="state does not match coverage"):
        EvidenceSourceResolution(
            area=area,
            capability="demand-flow",
            required_use="network-planning",
            matches=(),
            missing_cells=("ST56",),
            state="complete",
        )


def test_normalised_observation_collection_is_immutable() -> None:
    observations = normalise_source_export(
        source_export=source_export("england-demand", content="a"),
        ingestion_contract=ingestion_contract("england-demand", implementation="c"),
        drafts=(
            EvidenceObservationDraft(
                observation_id="england-flow-1",
                subject_id="corridor-1",
                claim="daily-demand-flow",
                value={"trips_per_day": 120},
                coverage_cells=("ST56",),
                observed_at="2026-01-31",
            ),
        ),
    )
    collection = EvidenceObservationCollection(observations)

    with pytest.raises(FrozenInstanceError):
        collection.observations = ()
    with pytest.raises(FrozenInstanceError):
        collection.fingerprint = "f" * 64
