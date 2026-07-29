from __future__ import annotations

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon, shape
from test_backbone_assembly import (
    config as backbone_config,
)
from test_backbone_assembly import (
    frame as backbone_frame,
)
from test_backbone_assembly import (
    parallel_spine_source,
)

from satn import open_roads_adapter
from satn.agents import FakeAgentRuntime
from satn.compiler import compile_network
from satn.evidence_contracts import (
    EvidencePartitionKey,
    IngestionContract,
    SourceExport,
    evidence_geometry_fingerprint,
)
from satn.evidence_store_equivalence import (
    EvidenceStoreEquivalenceError,
    OfficialRoadSourceLineage,
    assert_official_road_source_frame_equivalent,
    canonical_official_road_source_frame_fingerprint,
    project_official_road_source_frame,
)
from satn.local_evidence_store import EvidenceQueryResult, EvidenceQueryRow
from satn.publisher import publish

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def _published_network_semantics(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    projected_geometry = gpd.GeoSeries(
        [shape(feature["geometry"]) for feature in payload["features"]],
        crs=4326,
    ).to_crs(27700)
    features = [
        {
            "id": feature["id"],
            "properties": feature["properties"],
            "geometry_fingerprint": evidence_geometry_fingerprint(
                geometry,
                "EPSG:27700",
            ),
        }
        for feature, geometry in zip(
            payload.pop("features"),
            projected_geometry,
            strict=True,
        )
    ]
    features.sort(key=lambda feature: str(feature["id"]))
    canonical = json.dumps(
        {
            "header": payload,
            "features": features,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_export() -> SourceExport:
    return SourceExport(
        source_family="os-open-roads",
        dataset="open-roads",
        layer="RoadLink",
        publisher_release="2026-04-07",
        effective_date="2026-04-07",
        licence="Open Government Licence v3.0",
        format="GeoJSON",
        declared_crs="EPSG:4326",
        raw_bytes_sha256=SHA_A,
        provenance={"retained_path": "/governed/open-roads.geojson"},
    )


def _query_result(
    rows: tuple[tuple[str, LineString, dict[str, object]], ...],
    *,
    availability_counts: dict[str, int] | None = None,
) -> EvidenceQueryResult:
    export = _source_export()
    query_rows = tuple(
        sorted(
            (
                EvidenceQueryRow(
                    source_export_fingerprint=export.fingerprint,
                    logical_key=(
                        logical_key
                        if logical_key.startswith("roadlink:")
                        else f"roadlink:{logical_key}"
                    ),
                    feature_content_fingerprint=hashlib.sha256(
                        logical_key.encode()
                    ).hexdigest(),
                    geometry_fingerprint=evidence_geometry_fingerprint(
                        geometry, "EPSG:27700"
                    ),
                    geometry=geometry,
                    crs="EPSG:27700",
                    attributes=attributes,
                    attestation_fingerprints=(SHA_B,),
                )
                for logical_key, geometry, attributes in rows
            ),
            key=lambda row: (row.source_export_fingerprint, row.logical_key),
        )
    )
    counts = availability_counts or {
        "available": 1,
        "no-data": 0,
        "explicit-unknown": 0,
    }
    return EvidenceQueryResult(
        rows=query_rows,
        manifest={
            "contract": "satn-evidence-query-manifest/v1",
            "query_contract": "satn-evidence-query/v1",
            "coverage_contract": "satn-evidence-coverage/v1",
            "coverage_state_fingerprint": SHA_C,
            "source_layer": "os-open-roads/RoadLink",
            "selector_geometry_fingerprint": SHA_D,
            "selector_crs": "EPSG:27700",
            "predicate": "intersects",
            "predicate_operand_order": "feature-predicate-selector",
            "filters": {},
            "projection": [
                "name_1",
                "road_classification",
                "road_classification_number",
                "road_function",
            ],
            "required_partition_key_fingerprints": [SHA_E],
            "required_bng_10km_cells": ["ST67"],
            "consulted_attestation_fingerprints": [SHA_B],
            "availability_counts": counts,
            "row_count": len(query_rows),
            "row_fingerprints": [row.fingerprint for row in query_rows],
        },
    )


def test_store_rows_project_to_the_existing_official_road_source_frame_contract() -> None:
    result = _query_result(
        (
            (
                "roadlink:road-a",
                LineString([(350_000, 170_000), (351_000, 170_000)]),
                {
                    "road_classification": "A Road",
                    "road_function": "A Road",
                    "road_classification_number": "A4017",
                    "name_1": "Overndale Road",
                },
            ),
            (
                "roadlink:road-unknown",
                LineString([(350_000, 171_000), (351_000, 171_000)]),
                {
                    "road_classification": "Not Classified",
                    "road_function": "Local Road",
                    "road_classification_number": None,
                    "name_1": None,
                },
            ),
        )
    )
    projection = project_official_road_source_frame(
        result,
        OfficialRoadSourceLineage(
            source_export=_source_export(),
            source_id="os-open-roads-2026-04-07",
        ),
    )

    frame = projection.to_geodataframe()

    assert isinstance(frame, gpd.GeoDataFrame)
    assert str(frame.crs) == "EPSG:27700"
    assert frame.columns.tolist() == [
        "official_feature_id",
        "official_classification",
        "official_road_number",
        "official_road_name",
        "official_road_function",
        "source_id",
        "effective_date",
        "licence",
        "content_fingerprint",
        "geometry",
    ]
    assert frame.drop(columns="geometry").to_dict("records") == [
        {
            "official_feature_id": "road-a",
            "official_classification": "a-road",
            "official_road_number": "A4017",
            "official_road_name": "Overndale Road",
            "official_road_function": "A Road",
            "source_id": "os-open-roads-2026-04-07",
            "effective_date": "2026-04-07",
            "licence": "Open Government Licence v3.0",
            "content_fingerprint": SHA_A,
        },
        {
            "official_feature_id": "road-unknown",
            "official_classification": "unknown",
            "official_road_number": None,
            "official_road_name": None,
            "official_road_function": "Local Road",
            "source_id": "os-open-roads-2026-04-07",
            "effective_date": "2026-04-07",
            "licence": "Open Government Licence v3.0",
            "content_fingerprint": SHA_A,
        },
    ]
    assert projection.availability_counts == {
        "available": 1,
        "explicit-unknown": 0,
        "no-data": 0,
    }
    assert projection.query_result_fingerprint == result.fingerprint


def test_source_frame_hash_is_crs_order_and_null_representation_independent() -> None:
    oracle_geometry = {
        "road-a": LineString([(-2.5100, 51.4900), (-2.5090, 51.4905)]),
        "road-b": LineString([(-2.5080, 51.4910), (-2.5070, 51.4915)]),
    }
    projected_geometry = dict(
        zip(
            oracle_geometry,
            gpd.GeoSeries(
                list(oracle_geometry.values()),
                index=list(oracle_geometry),
                crs=4326,
            ).to_crs(27700),
            strict=True,
        )
    )
    result = _query_result(
        (
            (
                "road-b",
                projected_geometry["road-b"],
                {
                    "road_classification": "Classified Unnumbered",
                    "road_function": "Local Road",
                    "road_classification_number": None,
                    "name_1": "Overndale Road",
                },
            ),
            (
                "road-a",
                projected_geometry["road-a"],
                {
                    "road_classification": "A Road",
                    "road_function": "A Road",
                    "road_classification_number": "A4017",
                    "name_1": "Soundwell Road",
                },
            ),
        ),
        availability_counts={
            "available": 1,
            "no-data": 1,
            "explicit-unknown": 1,
        },
    )
    projection = project_official_road_source_frame(
        result,
        OfficialRoadSourceLineage(_source_export(), "os-open-roads-2026-04-07"),
    )
    oracle = projection.to_geodataframe().drop(columns="geometry")
    oracle = gpd.GeoDataFrame(
        oracle,
        geometry=[
            oracle_geometry[feature_id] for feature_id in oracle["official_feature_id"]
        ],
        crs=4326,
    ).iloc[::-1]
    oracle["effective_date"] = pd.to_datetime(oracle["effective_date"])

    assert (
        canonical_official_road_source_frame_fingerprint(oracle)
        == projection.semantic_fingerprint
    )
    expected_counts = {
        "available": 1,
        "explicit-unknown": 1,
        "no-data": 1,
    }
    assert_official_road_source_frame_equivalent(
        oracle,
        projection,
        expected_availability_counts=expected_counts,
    )
    assert projection.availability_counts == {
        "available": 1,
        "explicit-unknown": 1,
        "no-data": 1,
    }

    changed = oracle.copy()
    changed.loc[changed["official_feature_id"] == "road-b", "official_classification"] = "a-road"
    with pytest.raises(
        EvidenceStoreEquivalenceError,
        match="official-road source-frame semantics differ",
    ):
        assert_official_road_source_frame_equivalent(
            changed,
            projection,
            expected_availability_counts=expected_counts,
        )


def test_projection_identity_includes_explicit_unknown_and_nodata_counts() -> None:
    rows = (
        (
            "road-a",
            LineString([(350_000, 170_000), (351_000, 170_000)]),
            {
                "road_classification": "A Road",
                "road_function": "A Road",
                "road_classification_number": "A4017",
                "name_1": "Soundwell Road",
            },
        ),
    )
    available = project_official_road_source_frame(
        _query_result(rows),
        OfficialRoadSourceLineage(_source_export(), "os-open-roads-2026-04-07"),
    )
    incomplete = project_official_road_source_frame(
        _query_result(
            rows,
            availability_counts={
                "available": 0,
                "no-data": 1,
                "explicit-unknown": 1,
            },
        ),
        OfficialRoadSourceLineage(_source_export(), "os-open-roads-2026-04-07"),
    )

    assert available.semantic_fingerprint == incomplete.semantic_fingerprint
    assert available.fingerprint != incomplete.fingerprint


def test_source_frame_equivalence_rejects_availability_status_mismatch() -> None:
    projection = project_official_road_source_frame(
        _query_result(
            (
                (
                    "road-a",
                    LineString([(350_000, 170_000), (351_000, 170_000)]),
                    {
                        "road_classification": "A Road",
                        "road_function": "A Road",
                        "road_classification_number": "A4017",
                        "name_1": "Soundwell Road",
                    },
                ),
            ),
            availability_counts={
                "available": 1,
                "no-data": 1,
                "explicit-unknown": 0,
            },
        ),
        OfficialRoadSourceLineage(_source_export(), "os-open-roads-2026-04-07"),
    )

    with pytest.raises(
        EvidenceStoreEquivalenceError,
        match="official-road availability semantics differ",
    ):
        assert_official_road_source_frame_equivalent(
            projection.to_geodataframe(),
            projection,
            expected_availability_counts={
                "available": 0,
                "no-data": 1,
                "explicit-unknown": 1,
            },
        )


def _backbone_official_frames() -> tuple[gpd.GeoDataFrame, object]:
    oracle_geometry = [
        LineString([(0, 0), (0, 0.01)]),
        LineString([(0.1, 0), (0.1, 0.01)]),
    ]
    projected_geometry = gpd.GeoSeries(
        oracle_geometry,
        crs=4326,
    ).to_crs(27700)
    oracle = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "official-a1",
                "official_classification": "a-road",
                "official_road_number": "A1",
                "official_road_name": "A1",
                "official_road_function": "A Road",
                "source_id": "os-open-roads-2026-04-07",
                "effective_date": "2026-04-07",
                "licence": "Open Government Licence v3.0",
                "content_fingerprint": SHA_A,
                "geometry": oracle_geometry[0],
            },
            {
                "official_feature_id": "official-a2",
                "official_classification": "a-road",
                "official_road_number": "A2",
                "official_road_name": "A2",
                "official_road_function": "A Road",
                "source_id": "os-open-roads-2026-04-07",
                "effective_date": "2026-04-07",
                "licence": "Open Government Licence v3.0",
                "content_fingerprint": SHA_A,
                "geometry": oracle_geometry[1],
            },
        ],
        crs=4326,
    )
    result = _query_result(
        (
            (
                "official-a1",
                projected_geometry.iloc[0],
                {
                    "road_classification": "A Road",
                    "road_function": "A Road",
                    "road_classification_number": "A1",
                    "name_1": "A1",
                },
            ),
            (
                "official-a2",
                projected_geometry.iloc[1],
                {
                    "road_classification": "A Road",
                    "road_function": "A Road",
                    "road_classification_number": "A2",
                    "name_1": "A2",
                },
            ),
        )
    )
    return (
        oracle,
        project_official_road_source_frame(
            result,
            OfficialRoadSourceLineage(
                _source_export(),
                "os-open-roads-2026-04-07",
            ),
        ),
    )


def _source_with_gap(*, reverse: bool) -> dict[str, gpd.GeoDataFrame]:
    source = parallel_spine_source(reverse=reverse)
    place_rows = [
        *source["places"].to_dict("records"),
        {
            "place_id": "island",
            "name": "Island",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(0.12, 0.1),
        },
    ]
    network_rows = [
        *source["network"].to_dict("records"),
        {
            "osmid": "island-edge",
            "highway": "unclassified",
            "geometry": LineString([(0.12, 0.1), (0.13, 0.1)]),
        },
    ]
    source["places"] = backbone_frame(list(reversed(place_rows)) if reverse else place_rows)
    source["network"] = backbone_frame(
        list(reversed(network_rows)) if reverse else network_rows
    )
    return source


def test_store_projection_preserves_routes_gaps_and_diagnostics() -> None:
    oracle_official, projection = _backbone_official_frames()
    oracle_source = _source_with_gap(reverse=False) | {
        "official_road_classification": oracle_official
    }
    store_source = _source_with_gap(reverse=True) | {
        "official_road_classification": projection.to_geodataframe().iloc[::-1]
    }

    oracle = compile_network(backbone_config(), oracle_source, FakeAgentRuntime())
    store_backed = compile_network(backbone_config(), store_source, FakeAgentRuntime())

    assert len(store_backed.gaps) == 1
    assert store_backed.gaps.iloc[0]["from_place"] == "island"
    assert len(store_backed.spine_access_connections) == 3
    assert set(store_backed.spine_access_connections["access_connection_id"]) == set(
        oracle.spine_access_connections["access_connection_id"]
    )
    assert store_backed.compilation_diagnostics == oracle.compilation_diagnostics
    assert set(store_backed.access_obligations["obligation_id"]) == set(
        oracle.access_obligations["obligation_id"]
    )


def test_independent_oracle_and_projection_publish_identical_network(
    tmp_path: Path,
) -> None:
    oracle_official, projection = _backbone_official_frames()
    oracle_source = parallel_spine_source()
    store_source = parallel_spine_source(reverse=True)
    for source in (oracle_source, store_source):
        source["boundary"] = gpd.GeoDataFrame(
            [
                {
                    "geometry": Polygon(
                        [
                            (-0.01, -0.01),
                            (0.11, -0.01),
                            (0.11, 0.02),
                            (-0.01, 0.02),
                        ]
                    )
                }
            ],
            crs=4326,
        )
    oracle_config = backbone_config()
    oracle_config.publication.output_dir = tmp_path / "oracle"
    store_config = backbone_config()
    store_config.publication.output_dir = tmp_path / "store"
    oracle = compile_network(
        oracle_config,
        oracle_source | {"official_road_classification": oracle_official},
        FakeAgentRuntime(),
    )
    store_backed = compile_network(
        store_config,
        store_source
        | {
            "official_road_classification": (
                projection.to_geodataframe().iloc[::-1]
            )
        },
        FakeAgentRuntime(),
    )

    oracle_artifacts = publish(oracle_config, oracle, "equivalence")
    store_artifacts = publish(store_config, store_backed, "equivalence")
    assert _published_network_semantics(
        oracle_artifacts["geojson"]
    ) == _published_network_semantics(
        store_artifacts["geojson"]
    )


def test_a4017_overndale_authoritative_precedence_matches_snapshot_oracle() -> None:
    geometries = (
        LineString([(0, 500), (1000, 500)]),
        LineString([(0, 1000), (1000, 1000)]),
    )
    projection = project_official_road_source_frame(
        _query_result(
            (
                (
                    "official-overndale",
                    geometries[0],
                    {
                        "road_classification": "Classified Unnumbered",
                        "road_function": "Local Road",
                        "road_classification_number": None,
                        "name_1": "Overndale Road",
                    },
                ),
                (
                    "official-a-road",
                    geometries[1],
                    {
                        "road_classification": "A Road",
                        "road_function": "A Road",
                        "road_classification_number": "A999",
                        "name_1": "A Road",
                    },
                ),
            )
        ),
        OfficialRoadSourceLineage(_source_export(), "os-open-roads-2026-04-07"),
    )
    oracle_official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": "official-overndale",
                "official_classification": "classified-unnumbered",
                "official_road_number": None,
                "official_road_name": "Overndale Road",
                "official_road_function": "Local Road",
                "source_id": "os-open-roads-2026-04-07",
                "effective_date": "2026-04-07",
                "licence": "Open Government Licence v3.0",
                "content_fingerprint": SHA_A,
                "geometry": geometries[0],
            },
            {
                "official_feature_id": "official-a-road",
                "official_classification": "a-road",
                "official_road_number": "A999",
                "official_road_name": "A Road",
                "official_road_function": "A Road",
                "source_id": "os-open-roads-2026-04-07",
                "effective_date": "2026-04-07",
                "licence": "Open Government Licence v3.0",
                "content_fingerprint": SHA_A,
                "geometry": geometries[1],
            },
        ],
        crs=27700,
    )
    source = {
        "places": gpd.GeoDataFrame(
            [
                {
                    "place_id": "west",
                    "name": "West",
                    "kind": "community",
                    "place_class": "town",
                    "geometry": Point(0, 0),
                },
                {
                    "place_id": "east",
                    "name": "East",
                    "kind": "community",
                    "place_class": "town",
                    "geometry": Point(100, 0),
                },
            ],
            crs=27700,
        ),
        "network": gpd.GeoDataFrame(
            [
                {
                    "osmid": "urban-street",
                    "highway": "residential",
                    "geometry": LineString([(0, 0), (100, 0)]),
                }
            ],
            crs=27700,
        ),
        "boundary": gpd.GeoDataFrame(geometry=[], crs=27700),
        "context": gpd.GeoDataFrame(
            [
                {
                    "evidence_id": "osm-conflicting-a4017",
                    "feature_type": "a-road-spine",
                    "name": "A4017",
                    "category": "A-road strategic spine",
                    "source_id": "osm-way-overndale",
                    "network_scope": "urban",
                    "geometry": geometries[0],
                }
            ],
            crs=27700,
        ),
    }

    oracle = compile_network(
        backbone_config(),
        source | {"official_road_classification": oracle_official},
        FakeAgentRuntime(),
    )
    store_backed = compile_network(
        backbone_config(),
        source | {"official_road_classification": projection.to_geodataframe()},
        FakeAgentRuntime(),
    )

    assert list(store_backed.a_road_spines["evidence_id"]) == list(
        oracle.a_road_spines["evidence_id"]
    )
    assert store_backed.compilation_diagnostics == oracle.compilation_diagnostics
    assert set(store_backed.access_obligations["obligation_id"]) == set(
        oracle.access_obligations["obligation_id"]
    )
    assert list(store_backed.a_road_spines["evidence_id"]) == ["official-a-road"]
    assert store_backed.compilation_diagnostics["road_classification_disagreements"] == [
        {
            "disagreement_type": "official-non-a-road",
            "osm_evidence_id": "osm-conflicting-a4017",
            "osm_source_id": "osm-way-overndale",
            "official_feature_id": "official-overndale",
            "official_classification": "classified-unnumbered",
            "official_source_id": "os-open-roads-2026-04-07",
            "official_content_fingerprint": SHA_A,
        }
    ]


def test_equidistant_route_tie_matches_when_store_rows_and_source_rows_reverse() -> None:
    places = [
        {
            "place_id": "tie",
            "name": "Tie",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(0, 0),
        },
        {
            "place_id": "left-anchor",
            "name": "Left Anchor",
            "kind": "community",
            "place_class": "village",
            "geometry": Point(-0.01, 0),
        },
    ]
    network = [
        {
            "osmid": "left",
            "highway": "primary",
            "ref": "A1",
            "geometry": LineString([(-0.01, 0), (-0.001, 0)]),
        },
        {
            "osmid": "right",
            "highway": "primary",
            "ref": "A2",
            "geometry": LineString([(0.001, 0), (0.01, 0)]),
        },
    ]
    context = [
        {
            "evidence_id": "left-spine",
            "feature_type": "a-road-spine",
            "name": "A1",
            "category": "A-road strategic spine",
            "source_id": "left",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": network[0]["geometry"],
        },
        {
            "evidence_id": "right-spine",
            "feature_type": "a-road-spine",
            "name": "A2",
            "category": "A-road strategic spine",
            "source_id": "right",
            "feature_count": 1,
            "network_scope": "rural",
            "geometry": network[1]["geometry"],
        },
    ]
    official_geometry = gpd.GeoSeries(
        [network[0]["geometry"], network[1]["geometry"]],
        crs=4326,
    ).to_crs(27700)
    projection = project_official_road_source_frame(
        _query_result(
            tuple(
                (
                    f"official-{ref.lower()}",
                    geometry,
                    {
                        "road_classification": "A Road",
                        "road_function": "A Road",
                        "road_classification_number": ref,
                        "name_1": ref,
                    },
                )
                for ref, geometry in (
                    ("A1", official_geometry.iloc[0]),
                    ("A2", official_geometry.iloc[1]),
                )
            )
        ),
        OfficialRoadSourceLineage(_source_export(), "os-open-roads-2026-04-07"),
    )
    oracle_official = gpd.GeoDataFrame(
        [
            {
                "official_feature_id": f"official-{ref.lower()}",
                "official_classification": "a-road",
                "official_road_number": ref,
                "official_road_name": ref,
                "official_road_function": "A Road",
                "source_id": "os-open-roads-2026-04-07",
                "effective_date": "2026-04-07",
                "licence": "Open Government Licence v3.0",
                "content_fingerprint": SHA_A,
                "geometry": geometry,
            }
            for ref, geometry in (
                ("A1", network[0]["geometry"]),
                ("A2", network[1]["geometry"]),
            )
        ],
        crs=4326,
    )

    def compile_rows(reverse: bool) -> object:
        order = reversed if reverse else lambda values: values
        return compile_network(
            backbone_config(),
            {
                "places": backbone_frame(list(order(places))),
                "network": backbone_frame(list(order(network))),
                "context": backbone_frame(list(order(context))),
                "boundary": gpd.GeoDataFrame(geometry=[], crs=4326),
                "official_road_classification": (
                    projection.to_geodataframe().iloc[::-1]
                    if reverse
                    else oracle_official
                ),
            },
            FakeAgentRuntime(),
        )

    oracle = compile_rows(False)
    store_backed = compile_rows(True)

    assert set(store_backed.spine_access_connections["access_connection_id"]) == set(
        oracle.spine_access_connections["access_connection_id"]
    )
    assert set(store_backed.spine_access_branches["branch_id"]) == set(
        oracle.spine_access_branches["branch_id"]
    )
    assert store_backed.compilation_diagnostics == oracle.compilation_diagnostics


def test_governed_single_layer_geojson_keeps_semantic_roadlink_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "weca-os-open-roads-2026-04-07.geojson"
    gpd.GeoDataFrame(
        [
            {
                "id": "official-overndale",
                "road_classification": "Classified Unnumbered",
                "road_function": "Local Road",
                "road_classification_number": None,
                "name_1": "Overndale Road",
                    "geometry": LineString(
                        [(355_000, 175_000), (356_000, 175_000)]
                    ),
                }
            ],
            crs=27700,
    ).to_file(path, driver="GeoJSON", index=False)
    export = SourceExport(
        source_family="os-open-roads",
        dataset="open-roads",
        layer="RoadLink",
        publisher_release="2026-04-07",
        effective_date="2026-04-07",
        licence="Open Government Licence v3.0",
        format="GeoJSON",
        declared_crs="EPSG:27700",
        raw_bytes_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        provenance={"retained_path": str(path.resolve())},
    )
    contract_payload = open_roads_adapter.contract_payload("EPSG:27700")
    contract_payload.pop("contract")
    contract = IngestionContract(**contract_payload)

    validated = open_roads_adapter.validate_export(
        export,
        contract,
    )
    partition = open_roads_adapter.read_partition(
        validated,
        export,
        contract,
        EvidencePartitionKey(
            "os-open-roads/RoadLink",
            "bng-10km/v1",
            "ST57",
        ),
    )

    assert validated == path.resolve()
    assert partition.features[0].logical_key == "roadlink:official-overndale"
    assert partition.features[0].attributes == {
        "road_classification": "Classified Unnumbered",
        "road_function": "Local Road",
        "road_classification_number": None,
        "name_1": "Overndale Road",
    }
