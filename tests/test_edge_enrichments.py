"""Focused lifecycle tests for typed immutable Edge Enrichments."""

from __future__ import annotations

from dataclasses import replace

import duckdb
import pytest
from shapely.geometry import LineString, box

from satn.edge_enrichments import (
    EDGE_ENRICHMENT_EXPECTED_COLUMNS,
    VALUE_SCHEMAS,
    EdgeEnrichmentCollisionError,
    EdgeEnrichmentRequest,
    EdgeEnrichmentResult,
    EdgeEnrichmentStore,
    EducationReachValue,
    ElevationProfileValue,
    ElevationSample,
    OfficialClassificationOverlap,
    OfficialClassificationValue,
    PopulationCaptureLimit,
    PopulationCaptureObservation,
    PopulationCaptureValue,
    create_edge_enrichment_schema,
)
from satn.evidence_contracts import (
    BaseUnitParameter,
    EdgeEnrichmentHeader,
    EdgeEnrichmentParameters,
    EvidencePartitionAttestation,
    EvidencePartitionContent,
    EvidencePartitionKey,
    IngestionContract,
    SourceExport,
    evidence_geometry_fingerprint,
)
from satn.evidence_materialisations import materialise_area_network
from satn.local_evidence_store import EvidenceQueryResult, EvidenceQueryRow

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _edge():
    geometry = LineString([(0.0, 0.0), (10.0, 0.0)])
    row = EvidenceQueryRow(
        source_export_fingerprint=SHA_A,
        logical_key="roadlink:100",
        feature_content_fingerprint=SHA_B,
        geometry_fingerprint=evidence_geometry_fingerprint(geometry, "EPSG:27700"),
        geometry=geometry,
        crs="EPSG:27700",
        attributes={"road_classification": "A Road"},
        attestation_fingerprints=(SHA_C,),
    )
    result = EvidenceQueryResult(
        rows=(row,),
        manifest={
            "contract": "satn-evidence-query-manifest/v1",
            "query_contract": "satn-local-evidence-exact-spatial-query/v1",
            "coverage_contract": "satn-evidence-coverage/v1",
            "coverage_state_fingerprint": SHA_A,
            "source_layer": "os-open-roads/RoadLink",
            "selector_geometry_fingerprint": SHA_B,
            "selector_crs": "EPSG:27700",
            "predicate": "intersects",
            "predicate_operand_order": "feature_geometry predicate selector_geometry",
            "filters": {},
            "projection": ["road_classification"],
            "required_partition_key_fingerprints": [SHA_A],
            "required_bng_10km_cells": ["ST56"],
            "consulted_attestation_fingerprints": [SHA_C],
            "availability_counts": {
                "available": 1,
                "no-data": 0,
                "explicit-unknown": 0,
            },
            "row_count": 1,
            "row_fingerprints": [row.fingerprint],
        },
    )
    return materialise_area_network(
        result,
        box(-1.0, -1.0, 11.0, 1.0),
        {"contract": "satn-open-roads-canonical-network/v1"},
    ).logical_edges[0]


def _attestation() -> EvidencePartitionAttestation:
    export = SourceExport(
        source_family="os-open-roads",
        dataset="open-roads",
        layer="RoadLink",
        publisher_release="2026-04",
        effective_date="2026-04-07",
        licence="OS-PSGA",
        format="GeoPackage",
        declared_crs="EPSG:27700",
        raw_bytes_sha256=SHA_A,
    )
    contract = IngestionContract(
        source_layer="os-open-roads/RoadLink",
        contract_version="satn-open-roads-ingestion/v1",
        accepted_schema={"road_classification": "string"},
        stable_feature_key_policy="publisher-roadlink-id/v1",
        selected_attributes=("road_classification",),
        normalisation={"trim_strings": True},
        crs_transform={
            "source_crs": "EPSG:27700",
            "target_crs": "EPSG:27700",
            "axis_order": "always_xy",
        },
        partition_scheme="bng-10km/v1",
        spatial_predicate="intersects",
        implementation_dependency_fingerprint=SHA_B,
    )
    content = EvidencePartitionContent(
        EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56"),
        contract,
        (
            {
                "logical_key": "roadlink:100",
                "attributes": {"road_classification": "A Road"},
            },
        ),
        availability="available",
    )
    return EvidencePartitionAttestation(content, export)


def _request(kind: str, *, radius: int = 5_000) -> EdgeEnrichmentRequest:
    edge = _edge()
    header = EdgeEnrichmentHeader(
        stable_edge_id=edge.stable_edge_id,
        geometry_fingerprint=edge.geometry_fingerprint,
        partition_attestations=(_attestation(),),
        algorithm_id=kind,
        algorithm_contract=f"satn-{kind}/v1",
        implementation_dependency_fingerprint=SHA_D,
        parameters=EdgeEnrichmentParameters(
            (BaseUnitParameter("corridor_radius", radius, "mm"),)
        ),
    )
    return EdgeEnrichmentRequest(
        edge=edge,
        header=header,
        kind=kind,  # type: ignore[arg-type]
        value_schema_version=VALUE_SCHEMAS[kind],  # type: ignore[index]
    )


def _values():
    return (
        OfficialClassificationValue(
            status="available",
            overlaps=(
                OfficialClassificationOverlap(
                    "roadlink:100",
                    "A Road",
                    "satn-official-road-class/v1",
                    "a-road",
                    10_000,
                    SHA_A,
                    SHA_B,
                ),
            ),
        ),
        PopulationCaptureValue(
            status="available",
            observations=(
                PopulationCaptureObservation(
                    "oa:E0001",
                    "centroid:E0001",
                    125,
                    2_000,
                    "captured",
                    SHA_A,
                    SHA_B,
                ),
            ),
            limits=(
                PopulationCaptureLimit(
                    "current-development-omitted",
                    "No governed current-development population input",
                ),
            ),
        ),
        EducationReachValue(
            status="unknown",
            unknown_reason="not-edge-decomposable",
        ),
        ElevationProfileValue(
            status="available",
            distance_mm=10_000,
            ascent_mm=1_000,
            descent_mm=0,
            samples=(
                ElevationSample(0, 0, 20_000, "lidar:1", "accepted", "covered"),
                ElevationSample(1, 10_000, 21_000, "lidar:2", "accepted", "covered"),
            ),
        ),
    )


def test_typed_tables_cover_all_four_families_and_round_trip_exact_hits() -> None:
    connection = duckdb.connect(":memory:")
    create_edge_enrichment_schema(connection)
    store = EdgeEnrichmentStore(connection)
    requests = tuple(_request(value.kind) for value in _values())
    values = {value.kind: value for value in _values()}
    calls: list[str] = []

    def materialise(request: EdgeEnrichmentRequest) -> EdgeEnrichmentResult:
        calls.append(request.fingerprint)
        return EdgeEnrichmentResult(request=request, value=values[request.kind])

    first = store.resolve(requests, materialise)
    second = store.resolve(tuple(reversed(requests)), materialise)

    assert len(EDGE_ENRICHMENT_EXPECTED_COLUMNS) == 16
    assert calls == list(first.miss_fingerprints)
    assert first.hit_fingerprints == ()
    assert second.miss_fingerprints == ()
    assert second.hit_fingerprints == tuple(sorted(request.fingerprint for request in requests))
    assert second.records == first.records
    assert store.verify(first.records) == first.records


def test_changed_parameter_is_a_miss_and_typed_row_collision_fails_closed() -> None:
    connection = duckdb.connect(":memory:")
    create_edge_enrichment_schema(connection)
    store = EdgeEnrichmentStore(connection)
    original = _request("official-classification-overlap")
    changed = _request("official-classification-overlap", radius=6_000)
    value = _values()[0]

    def materialise(request: EdgeEnrichmentRequest) -> EdgeEnrichmentResult:
        return EdgeEnrichmentResult(request=request, value=value)

    first = store.resolve((original,), materialise)
    second = store.resolve((changed,), materialise)
    assert first.miss_fingerprints == (original.fingerprint,)
    assert second.miss_fingerprints == (changed.fingerprint,)

    connection.execute(
        """
        UPDATE edge_official_classification_overlap
        SET overlap_length_mm = overlap_length_mm + 1
        WHERE enrichment_fingerprint = ?
        """,
        [original.fingerprint],
    )
    with pytest.raises(EdgeEnrichmentCollisionError, match="typed value"):
        store.resolve((original,), materialise)


def test_unknown_and_no_data_are_explicit_and_cannot_hide_typed_rows() -> None:
    assert EducationReachValue(
        status="unknown", unknown_reason="not-edge-decomposable"
    ).canonical_payload()["unknown_reason"] == "not-edge-decomposable"
    assert OfficialClassificationValue(status="no-data").canonical_payload()["status"] == (
        "no-data"
    )
    with pytest.raises(ValueError, match="cannot contain"):
        replace(_values()[0], status="no-data")
