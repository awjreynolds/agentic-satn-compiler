from __future__ import annotations

from shapely.geometry import LineString, box

from satn.evidence_contracts import (
    evidence_geometry_fingerprint,
)
from satn.evidence_materialisations import materialise_area_network
from satn.local_evidence_store import EvidenceQueryResult, EvidenceQueryRow

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _query(
    *,
    geometry: LineString | None = None,
    state: str = SHA_A,
    predicate: str = "intersects",
) -> EvidenceQueryResult:
    geometry = geometry or LineString([(0.0, 0.0), (10.0, 0.0)])
    row = EvidenceQueryRow(
        source_export_fingerprint=SHA_A,
        logical_key="osm-way:42",
        feature_content_fingerprint=SHA_B,
        geometry_fingerprint=evidence_geometry_fingerprint(geometry, "EPSG:27700"),
        geometry=geometry,
        crs="EPSG:27700",
        attributes={"highway": "primary", "from_node": "osm-node:1", "to_node": "osm-node:2"},
        attestation_fingerprints=(SHA_C,),
    )
    manifest = {
        "contract": "satn-evidence-query-manifest/v1",
        "query_contract": "satn-local-evidence-exact-spatial-query/v1",
        "coverage_contract": "satn-evidence-coverage/v1",
        "coverage_state_fingerprint": state,
        "source_layer": "openstreetmap/lines",
        "selector_geometry_fingerprint": SHA_B,
        "selector_crs": "EPSG:27700",
        "predicate": predicate,
        "predicate_operand_order": "feature_geometry predicate selector_geometry",
        "filters": {},
        "projection": ["from_node", "highway", "to_node"],
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
    }
    return EvidenceQueryResult(rows=(row,), manifest=manifest)


def _contract(**updates: object) -> dict[str, object]:
    return {
        "contract": "satn-osm-canonical-network/v1",
        "edge_role_attribute": "highway",
        "endpoint_key_attributes": ["from_node", "to_node"],
        **updates,
    }


def test_materialises_exact_area_and_canonical_edge_lineage() -> None:
    result = materialise_area_network(
        _query(),
        box(-1.0, -1.0, 11.0, 1.0),
        _contract(),
    )

    assert result.extraction.consulted_cells == ("ST56",)
    assert result.extraction.selected_feature_ids == (f"{SHA_A}:osm-way:42",)
    assert result.extraction.rejected_feature_ids == ()
    assert result.extraction.coverage_unknown is False
    assert len(result.logical_edges) == 1
    edge = result.logical_edges[0]
    assert edge.stable_edge_id.startswith("edge:v1:")
    assert edge.endpoint_node_keys == ("osm-node:1", "osm-node:2")
    assert edge.constituent_source_logical_keys == ("osm-way:42",)
    assert edge.partition_attestation_fingerprints == (SHA_C,)
    assert edge.geometry_fingerprint != result.extraction.features[0].source_geometry_fingerprint


def test_boundary_predicate_and_geometry_invalidate_only_declared_records() -> None:
    query = _query()
    area = box(-1.0, -1.0, 5.0, 1.0)

    intersects = materialise_area_network(query, area, _contract())
    within = materialise_area_network(query, area, _contract(predicate="within"))
    moved_area = materialise_area_network(
        query,
        box(-1.0, -1.0, 6.0, 1.0),
        _contract(),
    )
    changed_geometry = materialise_area_network(
        _query(geometry=LineString([(0.0, 0.0), (10.0, 1.0)])),
        box(-1.0, -1.0, 11.0, 2.0),
        _contract(),
    )

    assert intersects.extraction.selected_feature_ids
    assert within.extraction.selected_feature_ids == ()
    assert intersects.extraction.fingerprint != within.extraction.fingerprint
    assert intersects.extraction.fingerprint != moved_area.extraction.fingerprint
    assert (
        intersects.logical_edges[0].stable_edge_id
        == changed_geometry.logical_edges[0].stable_edge_id
    )
    assert (
        intersects.logical_edges[0].geometry_fingerprint
        != changed_geometry.logical_edges[0].geometry_fingerprint
    )


def test_stable_edge_identity_ignores_query_row_order_and_line_direction() -> None:
    forward = materialise_area_network(
        _query(),
        box(-1.0, -1.0, 11.0, 1.0),
        _contract(),
    )
    reverse = materialise_area_network(
        _query(geometry=LineString([(10.0, 0.0), (0.0, 0.0)])),
        box(-1.0, -1.0, 11.0, 1.0),
        _contract(),
    )

    assert forward.logical_edges[0].stable_edge_id == reverse.logical_edges[0].stable_edge_id
    assert (
        forward.logical_edges[0].geometry_fingerprint
        == reverse.logical_edges[0].geometry_fingerprint
    )
