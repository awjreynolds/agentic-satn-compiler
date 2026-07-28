from __future__ import annotations

import pytest
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon

import satn.evidence_contracts as evidence_contracts
from satn.evidence_contracts import (
    BaseUnitParameter,
    EdgeEnrichmentHeader,
    EdgeEnrichmentParameters,
    EvidenceCoverage,
    EvidencePartitionAttestation,
    EvidencePartitionContent,
    EvidencePartitionKey,
    IngestionContract,
    ScenarioConfiguration,
    SourceExport,
    canonical_evidence_geometry,
    canonical_evidence_json,
    evidence_geometry_fingerprint,
)


def test_canonical_evidence_json_sorts_keys_and_rejects_all_floats() -> None:
    assert canonical_evidence_json({"b": [2, 1], "a": "x"}) == '{"a":"x","b":[2,1]}'
    with pytest.raises(ValueError, match="cannot contain JSON floats"):
        canonical_evidence_json({"sample_spacing_mm": 500.0})
    with pytest.raises(ValueError, match="cannot contain JSON floats"):
        canonical_evidence_json({"sample_spacing_mm": float("nan")})


def test_partition_content_rejects_a_digest_collision_between_distinct_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = IngestionContract(
        source_layer="os-open-roads/RoadLink",
        contract_version="satn-open-roads-ingestion/v1",
        accepted_schema={"road_name": "string"},
        stable_feature_key_policy="publisher-roadlink-id/v1",
        selected_attributes=("road_name",),
        normalisation={"trim_road_name": True},
        crs_transform={
            "source_crs": "EPSG:27700",
            "target_crs": "EPSG:27700",
            "axis_order": "always_xy",
        },
        partition_scheme="bng-10km/v1",
        spatial_predicate="intersects",
        implementation_dependency_fingerprint="b" * 64,
    )
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    monkeypatch.setattr(evidence_contracts, "evidence_fingerprint", lambda _: "f" * 64)

    with pytest.raises(ValueError, match="digest collision"):
        EvidencePartitionContent(
            key,
            contract,
            (
                {"logical_key": "roadlink:100", "attributes": {"road_name": "A4"}},
                {"logical_key": "roadlink:101", "attributes": {"road_name": "A36"}},
            ),
        )


def test_source_export_identity_ignores_local_operational_provenance() -> None:
    shared = {
        "source_family": "os-open-roads",
        "dataset": "open-roads",
        "layer": "RoadLink",
        "publisher_release": "2026-04",
        "effective_date": "2026-04-07",
        "licence": "OS-PSGA",
        "format": "GeoPackage",
        "declared_crs": "EPSG:27700",
        "raw_bytes_sha256": "a" * 64,
    }

    first = SourceExport(
        **shared,
        provenance={
            "local_path": "/private/tmp/open-roads.gpkg",
            "retrieved_at": "2026-07-28T08:00:00Z",
            "database_bytes_sha256": "1" * 64,
        },
    )
    rebuilt = SourceExport(
        **shared,
        provenance={
            "local_path": "/Volumes/evidence/open-roads-copy.gpkg",
            "retrieved_at": "2026-07-29T08:00:00Z",
            "database_bytes_sha256": "2" * 64,
        },
    )

    assert first.fingerprint == rebuilt.fingerprint
    assert len(first.fingerprint) == 64


def test_ingestion_contract_has_sorted_unique_schema_and_dependency_identity() -> None:
    shared = {
        "source_layer": "os-open-roads/RoadLink",
        "contract_version": "satn-open-roads-ingestion/v1",
        "accepted_schema": {"road_name": "string", "road_class": "string"},
        "stable_feature_key_policy": "publisher-roadlink-id/v1",
        "normalisation": {"trim_road_name": True},
        "crs_transform": {
            "source_crs": "EPSG:27700",
            "target_crs": "EPSG:27700",
            "axis_order": "always_xy",
        },
        "partition_scheme": "bng-10km/v1",
        "spatial_predicate": "intersects",
        "implementation_dependency_fingerprint": "b" * 64,
    }

    first = IngestionContract(
        **shared,
        selected_attributes=("road_name", "road_class"),
    )
    reordered = IngestionContract(
        **shared,
        selected_attributes=("road_class", "road_name"),
    )

    assert first.selected_attributes == ("road_class", "road_name")
    assert first.fingerprint == reordered.fingerprint
    with pytest.raises(ValueError, match="selected_attributes cannot contain duplicates"):
        IngestionContract(**shared, selected_attributes=("road_name", "road_name"))


def test_evidence_geometry_is_bng_integer_millimetres_not_network_geometry() -> None:
    forward = LineString([(0, 0), (1, 0), (1, 0), (2, 0)])
    reverse = LineString([(2, 0), (1, 0), (0, 0)])

    canonical = canonical_evidence_geometry(forward, "EPSG:27700")

    assert canonical["contract"] == "satn-evidence-geometry-v1"
    assert canonical["crs"] == "EPSG:27700"
    assert canonical["input_coordinate_unit"] == "metres"
    assert canonical["coordinate_unit"] == "millimetres"
    assert canonical["geometry"] == {
        "type": "LineString",
        "coordinates": [[0, 0], [1000, 0], [2000, 0]],
    }
    assert evidence_geometry_fingerprint(forward, "EPSG:27700") == (
        evidence_geometry_fingerprint(reverse, "EPSG:27700")
    )
    with pytest.raises(ValueError, match="EPSG:27700"):
        canonical_evidence_geometry(forward, "EPSG:4326")
    with pytest.raises(ValueError, match="collapses"):
        canonical_evidence_geometry(
            LineString([(0, 0), (0.0004, 0.0004)]),
            "EPSG:27700",
        )
    with pytest.raises(ValueError, match="valid"):
        canonical_evidence_geometry(LineString([(0, 0), (0, 0)]), "EPSG:27700")


def test_evidence_polygon_geometry_ignores_ring_direction_start_and_part_order() -> None:
    polygon = Polygon(
        [(0, 0), (0, 2000), (2000, 2000), (2000, 0), (0, 0)],
        holes=[[(500, 500), (1500, 500), (1500, 1500), (500, 1500), (500, 500)]],
    )
    equivalent = Polygon(
        [(2000, 2000), (0, 2000), (0, 0), (2000, 0), (2000, 2000)],
        holes=[[(500, 500), (500, 1500), (1500, 1500), (1500, 500), (500, 500)]],
    )
    other = Polygon([(3000, 0), (3000, 1000), (4000, 1000), (4000, 0), (3000, 0)])

    canonical = canonical_evidence_geometry(polygon, "EPSG:27700")

    assert canonical["geometry"]["type"] == "Polygon"
    assert evidence_geometry_fingerprint(polygon, "EPSG:27700") == (
        evidence_geometry_fingerprint(equivalent, "EPSG:27700")
    )
    assert evidence_geometry_fingerprint(MultiPolygon([polygon, other]), "EPSG:27700") == (
        evidence_geometry_fingerprint(MultiPolygon([other, polygon]), "EPSG:27700")
    )
    with pytest.raises(ValueError, match="valid"):
        canonical_evidence_geometry(
            Polygon([(0, 0), (2000, 2000), (0, 2000), (2000, 0), (0, 0)]),
            "EPSG:27700",
        )


def test_evidence_geometry_rejects_non_2d_and_post_quantisation_collapse() -> None:
    with pytest.raises(ValueError, match="two-dimensional"):
        canonical_evidence_geometry(Point(0, 0, 1), "EPSG:27700")
    with pytest.raises(ValueError, match="collapses"):
        canonical_evidence_geometry(
            Polygon([(0, 0), (1, 0), (0.0004, 0.0004), (0, 0)]),
            "EPSG:27700",
        )
    with pytest.raises(ValueError, match="valid after millimetre quantization"):
        canonical_evidence_geometry(
            MultiPolygon(
                [
                    Polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]),
                    Polygon(
                        [
                            (1.0004, 0),
                            (1.0004, 1),
                            (2, 1),
                            (2, 0),
                            (1.0004, 0),
                        ]
                    ),
                ]
            ),
            "EPSG:27700",
        )


def test_evidence_geometry_rejects_duplicate_canonical_multiline_members() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        canonical_evidence_geometry(
            MultiLineString([[(0, 0), (1, 0)], [(1, 0), (0, 0)]]),
            "EPSG:27700",
        )


def test_evidence_partition_key_is_a_bng_spatial_address_not_a_council() -> None:
    key = EvidencePartitionKey(
        source_layer="os-open-roads/RoadLink",
        partition_scheme="bng-10km/v1",
        cell="ST56",
    )

    assert key.canonical_payload() == {
        "contract": "satn-evidence-partition-key/v1",
        "source_layer": "os-open-roads/RoadLink",
        "partition_scheme": "bng-10km/v1",
        "cell": "ST56",
    }
    with pytest.raises(ValueError, match="bng-10km/v1"):
        EvidencePartitionKey(
            source_layer="os-open-roads/RoadLink",
            partition_scheme="council/v1",
            cell="Bath-and-North-East-Somerset",
        )


@pytest.mark.parametrize("cell", ("ST56", "HP99"))
def test_evidence_partition_key_accepts_real_bng_10km_cells(cell: str) -> None:
    assert EvidencePartitionKey(
        source_layer="os-open-roads/RoadLink",
        partition_scheme="bng-10km/v1",
        cell=cell,
    ).cell == cell


@pytest.mark.parametrize("cell", ("ZZ99", "HA00", "OS00"))
def test_evidence_partition_key_rejects_out_of_range_bng_10km_cells(cell: str) -> None:
    with pytest.raises(ValueError, match="valid BNG 10km cell"):
        EvidencePartitionKey(
            source_layer="os-open-roads/RoadLink",
            partition_scheme="bng-10km/v1",
            cell=cell,
        )


def test_partition_content_sorts_feature_content_without_fid_or_row_identity() -> None:
    contract = IngestionContract(
        source_layer="os-open-roads/RoadLink",
        contract_version="satn-open-roads-ingestion/v1",
        accepted_schema={"road_name": "string"},
        stable_feature_key_policy="publisher-roadlink-id/v1",
        selected_attributes=("road_name",),
        normalisation={"trim_road_name": True},
        crs_transform={
            "source_crs": "EPSG:27700",
            "target_crs": "EPSG:27700",
            "axis_order": "always_xy",
        },
        partition_scheme="bng-10km/v1",
        spatial_predicate="intersects",
        implementation_dependency_fingerprint="b" * 64,
    )
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    first_feature = {
        "logical_key": "roadlink:100",
        "geometry_fingerprint": "c" * 64,
        "attributes": {"road_name": "A4"},
    }
    second_feature = {
        "logical_key": "roadlink:101",
        "geometry_fingerprint": "d" * 64,
        "attributes": {"road_name": "A4"},
    }

    content = EvidencePartitionContent(key, contract, (second_feature, first_feature))
    reordered = EvidencePartitionContent(key, contract, (first_feature, second_feature))

    assert content.fingerprint == reordered.fingerprint
    assert content.features[0]["logical_key"] == "roadlink:100"
    with pytest.raises(ValueError, match="duplicate"):
        EvidencePartitionContent(key, contract, (first_feature, first_feature))
    with pytest.raises(ValueError, match="FID"):
        EvidencePartitionContent(
            key,
            contract,
            ({"fid": 42, "geometry_fingerprint": "e" * 64, "attributes": {}},),
        )


def test_partition_content_orders_feature_fingerprints_by_full_digest() -> None:
    contract = IngestionContract(
        source_layer="os-open-roads/RoadLink",
        contract_version="satn-open-roads-ingestion/v1",
        accepted_schema={"road_name": "string"},
        stable_feature_key_policy="publisher-roadlink-id/v1",
        selected_attributes=("road_name",),
        normalisation={"trim_road_name": True},
        crs_transform={
            "source_crs": "EPSG:27700",
            "target_crs": "EPSG:27700",
            "axis_order": "always_xy",
        },
        partition_scheme="bng-10km/v1",
        spatial_predicate="intersects",
        implementation_dependency_fingerprint="b" * 64,
    )
    content = EvidencePartitionContent(
        EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56"),
        contract,
        ({"attributes": {"name": "a"}}, {"attributes": {"name": "d"}}),
    )

    assert content.feature_content_fingerprints == tuple(
        sorted(content.feature_content_fingerprints)
    )


def test_partition_attestation_requires_fresh_complete_content_and_export() -> None:
    export = SourceExport(
        source_family="os-open-roads",
        dataset="open-roads",
        layer="RoadLink",
        publisher_release="2026-04",
        effective_date="2026-04-07",
        licence="OS-PSGA",
        format="GeoPackage",
        declared_crs="EPSG:27700",
        raw_bytes_sha256="a" * 64,
    )
    contract = IngestionContract(
        source_layer="os-open-roads/RoadLink",
        contract_version="satn-open-roads-ingestion/v1",
        accepted_schema={"road_name": "string"},
        stable_feature_key_policy="publisher-roadlink-id/v1",
        selected_attributes=("road_name",),
        normalisation={"trim_road_name": True},
        crs_transform={
            "source_crs": "EPSG:27700",
            "target_crs": "EPSG:27700",
            "axis_order": "always_xy",
        },
        partition_scheme="bng-10km/v1",
        spatial_predicate="intersects",
        implementation_dependency_fingerprint="b" * 64,
    )
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    content = EvidencePartitionContent(
        key,
        contract,
        ({"logical_key": "roadlink:100", "attributes": {"road_name": "A4"}},),
    )

    attestation = EvidencePartitionAttestation(content, export)

    assert attestation.canonical_payload()["partition_content_fingerprint"] == content.fingerprint
    assert attestation.canonical_payload()["source_export_fingerprint"] == export.fingerprint
    with pytest.raises(ValueError, match="requires an EvidencePartitionContent"):
        EvidencePartitionAttestation(None, export)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="stale"):
        EvidencePartitionAttestation(content, export, fingerprint="e" * 64)


def test_partition_attestation_binds_exact_source_layer_and_declared_crs() -> None:
    contract = IngestionContract(
        source_layer="os-open-roads/RoadLink",
        contract_version="satn-open-roads-ingestion/v1",
        accepted_schema={"road_name": "string"},
        stable_feature_key_policy="publisher-roadlink-id/v1",
        selected_attributes=("road_name",),
        normalisation={"trim_road_name": True},
        crs_transform={
            "source_crs": "EPSG:27700",
            "target_crs": "EPSG:27700",
            "axis_order": "always_xy",
        },
        partition_scheme="bng-10km/v1",
        spatial_predicate="intersects",
        implementation_dependency_fingerprint="b" * 64,
    )
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    content = EvidencePartitionContent(key, contract, ({"attributes": {}},))
    shared_export = {
        "dataset": "open-roads",
        "layer": "RoadLink",
        "publisher_release": "2026-04",
        "effective_date": "2026-04-07",
        "licence": "OS-PSGA",
        "format": "GeoPackage",
        "raw_bytes_sha256": "a" * 64,
    }

    with pytest.raises(ValueError, match="source_layer"):
        EvidencePartitionAttestation(
            content,
            SourceExport(
                **shared_export,
                source_family="another-source",
                declared_crs="EPSG:27700",
            ),
        )
    with pytest.raises(ValueError, match="source_layer"):
        EvidencePartitionAttestation(
            content,
            SourceExport(
                **(shared_export | {"layer": "OtherLayer"}),
                source_family="os-open-roads",
                declared_crs="EPSG:27700",
            ),
        )
    with pytest.raises(ValueError, match="declared_crs"):
        EvidencePartitionAttestation(
            content,
            SourceExport(
                **shared_export,
                source_family="os-open-roads",
                declared_crs="EPSG:4326",
            ),
        )


def test_evidence_coverage_is_a_sorted_partition_set_with_explicit_state() -> None:
    export = SourceExport(
        source_family="os-open-roads",
        dataset="open-roads",
        layer="RoadLink",
        publisher_release="2026-04",
        effective_date="2026-04-07",
        licence="OS-PSGA",
        format="GeoPackage",
        declared_crs="EPSG:27700",
        raw_bytes_sha256="a" * 64,
    )
    contract = IngestionContract(
        source_layer="os-open-roads/RoadLink",
        contract_version="satn-open-roads-ingestion/v1",
        accepted_schema={"road_name": "string"},
        stable_feature_key_policy="publisher-roadlink-id/v1",
        selected_attributes=("road_name",),
        normalisation={"trim_road_name": True},
        crs_transform={
            "source_crs": "EPSG:27700",
            "target_crs": "EPSG:27700",
            "axis_order": "always_xy",
        },
        partition_scheme="bng-10km/v1",
        spatial_predicate="intersects",
        implementation_dependency_fingerprint="b" * 64,
    )
    present = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    missing = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST57")
    content = EvidencePartitionContent(
        present,
        contract,
        ({"logical_key": "roadlink:100", "attributes": {"road_name": "A4"}},),
    )
    attestation = EvidencePartitionAttestation(content, export)

    coverage = EvidenceCoverage(
        (attestation,),
        requested_partition_keys=(missing, present),
        state="partial",
    )

    assert coverage.attestations == (attestation,)
    assert coverage.missing_partition_keys == (missing,)
    with pytest.raises(ValueError, match="complete"):
        EvidenceCoverage(
            (attestation,),
            requested_partition_keys=(missing, present),
            state="complete",
        )
    with pytest.raises(ValueError, match="duplicate"):
        EvidenceCoverage((attestation, attestation))


def test_scenario_configuration_is_immutable_data_only_identity() -> None:
    shared = {
        "area_definition_fingerprint": "a" * 64,
        "criteria_set_fingerprint": "b" * 64,
        "network_selection_profile_fingerprint": "c" * 64,
    }
    first = ScenarioConfiguration(**shared)
    rebuilt = ScenarioConfiguration(**shared)

    assert first.fingerprint == rebuilt.fingerprint
    for prohibited_field in (
        "accepted_decision_fingerprint",
        "data_choices",
        "store_path",
        "database_bytes",
        "database_path",
        "query_plan",
        "cache_state",
        "rtree_state",
    ):
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            ScenarioConfiguration(**shared, **{prohibited_field: {"nested": "value"}})


def test_edge_enrichment_header_binds_full_dependencies_and_base_unit_parameters() -> None:
    export = SourceExport(
        source_family="os-open-roads",
        dataset="open-roads",
        layer="RoadLink",
        publisher_release="2026-04",
        effective_date="2026-04-07",
        licence="OS-PSGA",
        format="GeoPackage",
        declared_crs="EPSG:27700",
        raw_bytes_sha256="a" * 64,
    )
    contract = IngestionContract(
        source_layer="os-open-roads/RoadLink",
        contract_version="satn-open-roads-ingestion/v1",
        accepted_schema={"road_name": "string"},
        stable_feature_key_policy="publisher-roadlink-id/v1",
        selected_attributes=("road_name",),
        normalisation={"trim_road_name": True},
        crs_transform={
            "source_crs": "EPSG:27700",
            "target_crs": "EPSG:27700",
            "axis_order": "always_xy",
        },
        partition_scheme="bng-10km/v1",
        spatial_predicate="intersects",
        implementation_dependency_fingerprint="b" * 64,
    )
    key = EvidencePartitionKey("os-open-roads/RoadLink", "bng-10km/v1", "ST56")
    content = EvidencePartitionContent(
        key,
        contract,
        ({"logical_key": "roadlink:100", "attributes": {"road_name": "A4"}},),
    )
    attestation = EvidencePartitionAttestation(content, export)
    parameters = EdgeEnrichmentParameters((BaseUnitParameter("sample_spacing", 500, "mm"),))

    header = EdgeEnrichmentHeader(
        stable_edge_id="edge:v1:" + "f" * 64,
        geometry_fingerprint="e" * 64,
        partition_attestations=(attestation,),
        algorithm_id="gradient-profile",
        algorithm_contract="satn-gradient-profile/v1",
        implementation_dependency_fingerprint="d" * 64,
        parameters=parameters,
    )

    assert header.canonical_payload()["parameters_fingerprint"] == parameters.fingerprint
    assert header.canonical_payload()["partition_attestation_fingerprints"] == [
        attestation.fingerprint
    ]
    with pytest.raises(ValueError, match="integer"):
        BaseUnitParameter("sample_spacing", 500.0, "mm")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="duplicate"):
        EdgeEnrichmentHeader(
            stable_edge_id="edge:v1:" + "f" * 64,
            geometry_fingerprint="e" * 64,
            partition_attestations=(attestation, attestation),
            algorithm_id="gradient-profile",
            algorithm_contract="satn-gradient-profile/v1",
            implementation_dependency_fingerprint="d" * 64,
            parameters=parameters,
        )
