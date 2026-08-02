from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from satn.dft_traffic_adapter import (
    contract_payload,
    ingestion_contract,
    observation_from_attributes,
    read_partition,
    traffic_claim_signature,
    validate_export,
)
from satn.evidence_contracts import EvidencePartitionKey, SourceExport, evidence_fingerprint


def _provenance(path: Path, layer: str) -> dict[str, object]:
    header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
    return {
        "acquisition_url": "https://roadtraffic.dft.gov.uk/downloads/aadf.csv",
        "acquisition_method": "GET",
        "query_parameters": {"year": "2024"},
        "acquisition_page": 1,
        "retrieved_at": "2026-08-02T10:00:00Z",
        "content_type": "text/csv",
        "byte_count": path.stat().st_size,
        "etag": None,
        "last_modified": None,
        "archive_members": [],
        "csv_header": header,
        "schema_fingerprint": evidence_fingerprint(
            {
                "contract": "satn-dft-traffic-schema/v1",
                "layer": layer,
                "headers": header,
            }
        ),
        "normalisation_contract_fingerprint": "b" * 64,
        "methodology_url": "https://roadtraffic.dft.gov.uk/metadata",
        "publication_id": "dft-2024",
        "row_count": 1,
        "pagination_bound": 1,
        "retained_path": str(path.resolve()),
    }


def _source(path: Path, layer: str, *, declared_crs: str = "EPSG:27700") -> SourceExport:
    provenance = _provenance(path, layer)
    provenance["normalisation_contract_fingerprint"] = ingestion_contract(
        layer, declared_crs
    ).fingerprint
    return SourceExport(
        source_family="dft",
        dataset="road-traffic-statistics",
        layer=layer,
        publisher_release="2025",
        effective_date="2025-01-01",
        licence="OGL-UK-3.0",
        format="CSV",
        declared_crs=declared_crs,
        raw_bytes_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        provenance=provenance,
    )


def test_aadf_rows_become_point_observations_with_stable_provenance(tmp_path: Path) -> None:
    path = tmp_path / "aadf.csv"
    path.write_text(
        "count_point_id,year,all_motor_vehicles,easting,northing,road_name\n"
        "CP1,2024,1234,354000,165000,A4\n",
        encoding="utf-8",
    )
    source = _source(path, "aadf")
    contract = ingestion_contract("aadf", "EPSG:27700")
    key = EvidencePartitionKey("dft/aadf", "bng-10km/v1", "ST56")

    first = read_partition(path, source, contract, key)
    second = read_partition(path, source, contract, key)

    assert first.features[0].logical_key == second.features[0].logical_key
    observation = observation_from_attributes(first.features[0].attributes)
    assert observation.source_layer == "aadf"
    assert observation.all_motor_vehicles == 1234
    assert observation.easting == 354000
    assert observation.northing == 165000
    assert observation.declared_crs == "EPSG:27700"
    assert observation.match_state.value == "unknown"
    assert observation.source_export_fingerprint == source.fingerprint


def test_by_direction_and_raw_contracts_preserve_distinct_semantics(tmp_path: Path) -> None:
    by_direction = tmp_path / "direction.csv"
    by_direction.write_text(
        "count_point_id,year,direction_of_travel,all_motor_vehicles,easting,northing\n"
        "CP1,2024,N,500,354000,165000\n",
        encoding="utf-8",
    )
    source = _source(by_direction, "aadf-by-direction")
    partition = read_partition(
        by_direction,
        source,
        ingestion_contract("aadf-by-direction", "EPSG:27700"),
        EvidencePartitionKey("dft/aadf-by-direction", "bng-10km/v1", "ST56"),
    )
    observation = observation_from_attributes(partition.features[0].attributes)
    assert observation.direction_of_travel == "N"
    assert observation.all_motor_vehicles == 500

    raw = tmp_path / "raw.csv"
    raw.write_text(
        "count_point_id,year,count_date,direction_of_travel,easting,northing,hour\n"
        "CP1,2024,2024-05-01,N,354000,165000,10\n",
        encoding="utf-8",
    )
    raw_source = _source(raw, "raw-counts")
    raw_partition = read_partition(
        raw,
        raw_source,
        ingestion_contract("raw-counts", "EPSG:27700"),
        EvidencePartitionKey("dft/raw-counts", "bng-10km/v1", "ST56"),
    )
    raw_observation = observation_from_attributes(raw_partition.features[0].attributes)
    assert raw_observation.all_motor_vehicles is None
    assert raw_observation.source_layer == "raw-counts"


def test_malformed_mandatory_contract_and_checksum_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "direction.csv"
    path.write_text(
        "count_point_id,year,all_motor_vehicles,easting,northing\n"
        "CP1,2024,500,354000,165000\n",
        encoding="utf-8",
    )
    source = _source(path, "aadf-by-direction")
    with pytest.raises(ValueError, match="direction_of_travel"):
        validate_export(
            source,
            ingestion_contract("aadf-by-direction", "EPSG:27700"),
        )

    bad = SourceExport(
        source_family=source.source_family,
        dataset=source.dataset,
        layer=source.layer,
        publisher_release=source.publisher_release,
        effective_date=source.effective_date,
        licence=source.licence,
        format=source.format,
        declared_crs=source.declared_crs,
        raw_bytes_sha256="0" * 64,
        provenance=source.provenance,
    )
    with pytest.raises(ValueError, match="checksum"):
        validate_export(bad, ingestion_contract("aadf-by-direction", "EPSG:27700"))


def test_plain_aadf_rejects_directional_schema(tmp_path: Path) -> None:
    path = tmp_path / "aadf.csv"
    path.write_text(
        "count_point_id,year,all_motor_vehicles,direction_of_travel,easting,northing\n"
        "CP1,2024,500,N,354000,165000\n",
        encoding="utf-8",
    )
    source = _source(path, "aadf")
    with pytest.raises(ValueError, match="directional"):
        validate_export(source, ingestion_contract("aadf", "EPSG:27700"))


def test_dft_provenance_is_a_closed_acquisition_contract(tmp_path: Path) -> None:
    path = tmp_path / "aadf.csv"
    path.write_text(
        "count_point_id,year,all_motor_vehicles,easting,northing,road_name\n"
        "CP1,2024,1234,354000,165000,A4\n",
        encoding="utf-8",
    )
    source = _source(path, "aadf")
    validate_export(source, ingestion_contract("aadf", "EPSG:27700"))
    incomplete = SourceExport(
        source_family=source.source_family,
        dataset=source.dataset,
        layer=source.layer,
        publisher_release=source.publisher_release,
        effective_date=source.effective_date,
        licence=source.licence,
        format=source.format,
        declared_crs=source.declared_crs,
        raw_bytes_sha256=source.raw_bytes_sha256,
        provenance={"retained_path": str(path.resolve())},
    )
    with pytest.raises(ValueError, match="provenance"):
        validate_export(incomplete, ingestion_contract("aadf", "EPSG:27700"))


def test_contract_declares_point_crs_transform_and_no_road_matching() -> None:
    payload = contract_payload("aadf", "EPSG:4326")
    assert payload["crs_transform"] == {
        "source_crs": "EPSG:4326",
        "target_crs": "EPSG:27700",
        "axis_order": "always_xy",
    }
    assert payload["normalisation"]["road_matching"] == "none"


def test_claim_signature_keeps_normalized_link_identity_fields() -> None:
    first = {
        "count_point_id": "CP1",
        "observation_year": 2024,
        "direction_of_travel": "N",
        "all_motor_vehicles": 500,
        "road_name": "A4",
        "road_category": "PM",
        "road_type": "Major",
        "start_junction_road_name": "Start",
        "end_junction_road_name": "End",
        "easting": 354000,
        "northing": 165000,
        "link_length_km": 1.2,
        "estimation_method": "Counted",
    }
    changed = {**first, "road_type": "Minor"}
    assert traffic_claim_signature(first) != traffic_claim_signature(changed)
