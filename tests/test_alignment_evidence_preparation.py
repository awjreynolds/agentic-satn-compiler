"""Public compiler seam tests for alignment evidence preparation."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString

import satn.alignment_evidence_preparation as preparation
from satn.alignment_evidence_preparation import prepare_alignment_evidence
from satn.models import CouncilConfig, SourceConfig
from satn.network_selection import NetworkSelectionProfile
from satn.pipeline import compilation_governed_input_fingerprint
from satn.psa_evidence_loaders import GovernedEvidenceLoadError
from satn.routing import RoadGraph, RouteOption


def profile(*, include_b_road: bool = False, maximum_options: int = 5) -> NetworkSelectionProfile:
    precedence = [
        "verified-existing-asset",
        "a-road-corridor",
    ]
    if include_b_road:
        precedence.append("b-road-corridor")
    precedence.append("other-routable")
    return NetworkSelectionProfile.model_validate(
        {
            "profile_id": "public-compiler-integration-v1",
            "candidate_source_precedence": precedence,
            "ambiguity": {"maximum_options_per_candidate_set": maximum_options},
        }
    )


def routing_graph() -> RoadGraph:
    rows = [
        {
            "u": "community-node",
            "v": "target-node",
            "osmid": "direct",
            "length": 1000.0,
            "highway": "residential",
            "ref": None,
            "oneway": False,
            "satn_ncn": False,
            "geometry": LineString([(400000, 170000), (401000, 170000)]),
        },
        {
            "u": "community-node",
            "v": "a-mid",
            "osmid": "a-left",
            "length": 550.0,
            "highway": "primary",
            "ref": "A4",
            "oneway": False,
            "satn_ncn": False,
            "geometry": LineString([(400000, 170000), (400500, 170100)]),
        },
        {
            "u": "a-mid",
            "v": "target-node",
            "osmid": "a-right",
            "length": 550.0,
            "highway": "primary",
            "ref": "A4",
            "oneway": False,
            "satn_ncn": False,
            "geometry": LineString([(400500, 170100), (401000, 170000)]),
        },
        {
            "u": "community-node",
            "v": "ncn-mid",
            "osmid": "ncn-left",
            "length": 600.0,
            "highway": "cycleway",
            "ref": None,
            "oneway": False,
            "satn_ncn": True,
            "geometry": LineString([(400000, 170000), (400500, 169900)]),
        },
        {
            "u": "ncn-mid",
            "v": "target-node",
            "osmid": "ncn-right",
            "length": 600.0,
            "highway": "cycleway",
            "ref": None,
            "oneway": False,
            "satn_ncn": True,
            "geometry": LineString([(400500, 169900), (401000, 170000)]),
        },
    ]
    return RoadGraph(gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:27700"))


def connections() -> gpd.GeoDataFrame:
    rows = [
        {
            "access_connection_id": "access-connection-1",
            "obligation_id": "access-obligation-1",
            "obligation_kind": "community",
            "place_id": "community-1",
            "community_id": "community-1",
            "spine_id": "strategic-spine-1",
            "root_spine_id": "strategic-spine-1",
            "parent_role": "strategic-spine",
            "parent_target_id": "strategic-spine-1",
            "parent_place_id": None,
            "community_attachment_node": "community-node",
            "target_attachment_node": "target-node",
            "spine_attachment_node": "target-node",
            "source_ids": "[\"source-route-1\"]",
            "provenance": "{\"source_ids\":[\"source-route-1\"]}",
            "geometry": LineString([(400000, 170000), (401000, 170000)]),
        },
        {
            "access_connection_id": "school-connection-1",
            "obligation_id": "school-obligation-1",
            "obligation_kind": "school",
            "place_id": "school-1",
            "community_id": None,
            "spine_id": "strategic-spine-1",
            "parent_target_id": "strategic-spine-1",
            "community_attachment_node": "community-node",
            "target_attachment_node": "target-node",
            "spine_attachment_node": "target-node",
            "geometry": LineString([(400000, 170000), (401000, 170000)]),
        },
    ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:27700")


def obligations() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "access_connection_id": "access-connection-1",
                "obligation_id": "access-obligation-1",
                "geometry": LineString([(400000, 170000), (400001, 170000)]),
            }
        ],
        geometry="geometry",
        crs="EPSG:27700",
    )


def spines() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "spine_id": "strategic-spine-1",
                "spine_kind": "a-road",
                "source_id": "strategic-source-exact",
                "evidence_id": "strategic-evidence-exact",
                "provenance": "{\"evidence_ids\":[\"original-evidence-7\"]}",
                "geometry": LineString([(400900, 169900), (401100, 170100)]),
            }
        ],
        geometry="geometry",
        crs="EPSG:27700",
    )


def current_asset_context() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "feature_type": "ncn-route",
                "ncn_evidence_role": "established-route",
                "evidence_id": "current-ncn-evidence",
                "source_id": "official-ncn-source",
                "geometry": LineString(
                    [(400000, 170000), (400500, 169900), (401000, 170000)]
                ),
            },
            {
                "feature_type": "declassified-ncn-route",
                "ncn_evidence_role": "declassified-route",
                "evidence_id": "former-ncn-evidence",
                "source_id": "official-former-ncn-source",
                "geometry": LineString([(400000, 170010), (401000, 170010)]),
            },
        ],
        geometry="geometry",
        crs="EPSG:27700",
    )


def prepare_without_evidence() -> preparation.AlignmentEvidencePreparationResult:
    return prepare_alignment_evidence(
        profile(),
        road_graph=routing_graph(),
        spine_access_connections=connections(),
        access_obligations=obligations(),
        strategic_spines=spines(),
        context=current_asset_context(),
        configuration=None,
        config_directory=Path.cwd(),
        as_at=None,
        school_register_max_age_days=None,
        strategic_admissions_max_age_days=None,
    )


def test_prepares_finite_candidates_per_actual_community_connection_only() -> None:
    result = prepare_without_evidence()

    assert result.status == "incomplete"
    assert result.missing_inputs == (
        "population-reach-evidence",
        "school-register-evidence",
    )
    assert len(result.prepared_connections) == 1
    prepared = result.prepared_connections[0]
    assert prepared.access_connection_id == "access-connection-1"
    assert prepared.candidate_set.maximum_options == 5
    assert len(prepared.candidate_set.candidates) <= 5
    assert result.diagnostics["school_branch_candidates_generated"] == 0
    assert result.diagnostics["selection_performed"] is False
    assert result.diagnostics["agent_runtime_invoked"] is False


def test_maps_current_ncn_a_road_and_other_without_declassified_advantage() -> None:
    prepared = prepare_without_evidence().prepared_connections[0]
    classes = {candidate.source_class.value for candidate in prepared.candidate_set.candidates}

    assert classes == {
        "verified-existing-asset",
        "a-road-corridor",
        "other-routable",
    }
    current = next(
        item
        for item in prepared.candidate_provenance
        if item["source_class"] == "verified-existing-asset"
    )
    assert {
        evidence["evidence_id"] for evidence in current["current_asset_evidence"]
    } == {"current-ncn-evidence"}
    assert prepared.strategic_source_id == "strategic-source-exact"
    assert prepared.strategic_evidence_id == "strategic-evidence-exact"
    assert prepared.strategic_provenance == (
        "{\"evidence_ids\":[\"original-evidence-7\"]}"
    )


def test_routing_deduplication_is_recorded_and_admission_enforces_profile_limit() -> None:
    result = prepare_alignment_evidence(
        profile(maximum_options=2),
        road_graph=routing_graph(),
        spine_access_connections=connections(),
        access_obligations=obligations(),
        strategic_spines=spines(),
        context=current_asset_context(),
        configuration=None,
        config_directory=Path.cwd(),
        as_at=None,
        school_register_max_age_days=None,
        strategic_admissions_max_age_days=None,
    )
    candidate_set = result.prepared_connections[0].candidate_set

    assert sum(item.disposition.value == "admitted" for item in candidate_set.admissions) == 2
    assert any(
        item.rationale.value == "profile-candidate-limit"
        for item in candidate_set.admissions
    )
    assert any(
        item.reason == "duplicate-routing-geometry"
        for item in result.generation_issues
    )


def test_disconnected_multipart_route_is_an_explicit_generation_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = RouteOption(
        role="direct",
        geometry=MultiLineString(
            [
                [(400000, 170000), (400100, 170000)],
                [(400500, 170000), (400600, 170000)],
            ]
        ),
        length_km=0.2,
        edge_ids=["invalid"],
        a_road_share=0.0,
        ncn_share=0.0,
        bidirectional=True,
        reverse_length_km=0.2,
        reverse_edge_ids=["invalid"],
        reverse_corridor_share=1.0,
        impracticable_alongside=False,
    )
    monkeypatch.setattr(
        preparation,
        "choose_alignment",
        lambda _graph, _start, _end: (invalid, [invalid], "fixture route"),
    )

    result = prepare_without_evidence()

    assert result.prepared_connections[0].candidate_set.candidates == ()
    assert result.generation_issues[0].reason == "disconnected-multipart-route"


def artifact(path: Path, *, source_id: str, content: bytes) -> dict[str, object]:
    path.write_bytes(content)
    return {
        "source_id": source_id,
        "path": path,
        "release": "fixture-release",
        "effective_date": "2021-03-21",
        "licence": "fixture-licence",
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "redistribution": "public",
    }


def council_with_population_files(
    tmp_path: Path,
    *,
    enabled: bool = True,
) -> CouncilConfig:
    config_path = tmp_path / "area.yaml"
    config_path.write_text("fixture: true\n", encoding="utf-8")
    snapshot = tmp_path / "snapshots" / "current"
    snapshot.mkdir(parents=True)
    (snapshot / "snapshot.json").write_text("{}", encoding="utf-8")
    population = {
        "output_area_geometry": artifact(
            tmp_path / "oa.geojson",
            source_id="oa-geometry",
            content=b"{\"oa\":1}",
        ),
        "population_weighted_centroids": artifact(
            tmp_path / "pwc.geojson",
            source_id="oa-pwc",
            content=b"{\"pwc\":2}",
        ),
        "usual_resident_counts": artifact(
            tmp_path / "counts.json",
            source_id="oa-counts",
            content=b"{\"counts\":3}",
        ),
    }
    return CouncilConfig(
        config_path=config_path,
        council_id="fixture",
        council_name="Fixture",
        source={
            "snapshot_dir": tmp_path / "snapshots",
            "population_reach_evidence": population,
        },
        compilation={"network_selection": profile()} if enabled else {},
        publication={"output_dir": tmp_path / "publication", "title": "Fixture SATN"},
    )


def compiler_manifest() -> dict[str, object]:
    return {"sha256": "d" * 64, "components": []}


def test_psa_file_bytes_enter_reuse_identity_before_publication_reuse(tmp_path: Path) -> None:
    council = council_with_population_files(tmp_path)
    first = compilation_governed_input_fingerprint(
        council,
        dependency_manifest=compiler_manifest(),
    )
    configured = council.source.population_reach_evidence
    assert configured is not None
    configured.output_area_geometry.path.write_bytes(b"{\"oa\":\"changed\"}")

    changed = compilation_governed_input_fingerprint(
        council,
        dependency_manifest=compiler_manifest(),
    )

    assert changed != first


def test_missing_declared_psa_file_fails_closed_before_reuse(tmp_path: Path) -> None:
    council = council_with_population_files(tmp_path)
    configured = council.source.population_reach_evidence
    assert configured is not None
    configured.output_area_geometry.path.unlink()

    with pytest.raises(ValueError, match="configured governed input file is missing"):
        compilation_governed_input_fingerprint(
            council,
            dependency_manifest=compiler_manifest(),
        )


def test_no_profile_does_not_promote_psa_file_bytes_into_legacy_reuse_identity(
    tmp_path: Path,
) -> None:
    council = council_with_population_files(tmp_path, enabled=False)
    first = compilation_governed_input_fingerprint(
        council,
        dependency_manifest=compiler_manifest(),
    )
    configured = council.source.population_reach_evidence
    assert configured is not None
    configured.output_area_geometry.path.write_bytes(b"{\"oa\":\"changed\"}")

    assert (
        compilation_governed_input_fingerprint(
            council,
            dependency_manifest=compiler_manifest(),
        )
        == first
    )


def test_declared_content_mismatch_fails_closed_instead_of_becoming_incomplete(
    tmp_path: Path,
) -> None:
    council = council_with_population_files(tmp_path)
    configured = council.source.population_reach_evidence
    assert configured is not None
    configured.output_area_geometry.path.write_bytes(b"tampered")

    with pytest.raises(GovernedEvidenceLoadError, match="SHA-256 mismatch"):
        prepare_alignment_evidence(
            profile(),
            road_graph=routing_graph(),
            spine_access_connections=connections(),
            access_obligations=obligations(),
            strategic_spines=spines(),
            context=current_asset_context(),
            configuration={"population_reach_evidence": configured},
            config_directory=tmp_path,
            as_at=None,
            school_register_max_age_days=None,
            strategic_admissions_max_age_days=None,
        )


def school_artifact(tmp_path: Path) -> dict[str, object]:
    return {
        "school_register": artifact(
            tmp_path / "schools.json",
            source_id="schools",
            content=json.dumps({"schema": "fixture"}).encode(),
        )
    }


def test_school_only_freshness_is_valid_and_admissions_freshness_is_conditional(
    tmp_path: Path,
) -> None:
    school_only = SourceConfig(
        snapshot_dir=Path("snapshots"),
        school_register_evidence=school_artifact(tmp_path),
        network_selection_as_at=date(2026, 7, 26),
        network_selection_school_register_max_age_days=365,
    )
    assert school_only.strategic_education_destination_admissions is None

    with pytest.raises(
        ValueError,
        match="strategic-admissions freshness requires an admissions artifact",
    ):
        SourceConfig(
            snapshot_dir=Path("snapshots"),
            network_selection_strategic_admissions_max_age_days=365,
        )


def test_admissions_artifact_requires_its_own_freshness_window(tmp_path: Path) -> None:
    admissions = {
        "admissions": artifact(
            tmp_path / "admissions.json",
            source_id="admissions",
            content=b"{\"schema\":\"fixture\"}",
        )
    }
    with pytest.raises(
        ValueError,
        match="strategic-admissions evidence requires a declared freshness window",
    ):
        SourceConfig(
            snapshot_dir=Path("snapshots"),
            school_register_evidence=school_artifact(tmp_path),
            strategic_education_destination_admissions=admissions,
            network_selection_as_at=date(2026, 7, 26),
            network_selection_school_register_max_age_days=365,
        )
