"""Adversarial contracts for Preferred Strategic Alignment evidence loaders."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon

from satn.compilation_dependencies import compilation_dependency_manifest
from satn.network_selection import (
    GovernedEvidenceArtifactConfig,
    PopulationReachEvidenceConfig,
    SchoolRegisterEvidenceConfig,
    StrategicEducationDestinationAdmissionConfig,
)
from satn.psa_evidence_loaders import (
    GovernedEvidenceLoadError,
    assess_education_access_from_evidence,
    compile_population_reach_from_evidence,
    load_education_access_evidence,
    load_population_reach_evidence,
)


def write_payload(tmp_path: Path, name: str, payload: object) -> GovernedEvidenceArtifactConfig:
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path = tmp_path / name
    path.write_bytes(content)
    return GovernedEvidenceArtifactConfig(
        source_id=name.removesuffix(".json"),
        path=Path(name),
        release="Governed release 2026-07",
        effective_date=date(2026, 7, 1),
        licence="Open Government Licence v3.0",
        content_sha256=hashlib.sha256(content).hexdigest(),
        redistribution="aggregate-only",
    )


def geojson(oa_id: str, geometry: dict[str, object]) -> dict[str, object]:
    return {
        "type": "FeatureCollection",
        "crs": "EPSG:27700",
        "features": [
            {
                "type": "Feature",
                "properties": {"OA21CD": oa_id},
                "geometry": geometry,
            }
        ],
    }


def population_config(tmp_path: Path) -> PopulationReachEvidenceConfig:
    oa_id = "E00123456"
    geometry = write_payload(
        tmp_path,
        "oa.json",
        geojson(
            oa_id,
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        [400000, 150000],
                        [400100, 150000],
                        [400100, 150100],
                        [400000, 150000],
                    ]
                ],
            },
        ),
    )
    centroids = write_payload(
        tmp_path,
        "pwc.json",
        geojson(oa_id, {"type": "Point", "coordinates": [400050, 150050]}),
    )
    counts = write_payload(
        tmp_path,
        "counts.json",
        {"records": [{"OA21CD": oa_id, "usual_residents": 123}]},
    )
    return PopulationReachEvidenceConfig(
        output_area_geometry=geometry,
        population_weighted_centroids=centroids,
        usual_resident_counts=counts,
    )


def test_loads_exact_complete_oa_evidence_with_artifact_lineage(tmp_path: Path) -> None:
    loaded = load_population_reach_evidence(population_config(tmp_path), base_directory=tmp_path)

    assert loaded is not None
    assert loaded.columns.oa_id == "OA21CD"
    assert loaded.output_areas["OA21CD"].tolist() == ["E00123456"]
    assert loaded.output_areas["usual_residents"].tolist() == [123]
    assert loaded.output_areas.crs.to_epsg() == 27700
    assert [item.source_id for item in loaded.artifact_lineage] == ["oa", "pwc", "counts"]
    assert [item.redistribution for item in loaded.artifact_lineage] == [
        "aggregate-only",
        "aggregate-only",
        "aggregate-only",
    ]
    assert loaded.source.content_sha256
    assert all(
        item.content_sha256 in loaded.source.transformation_lineage[i]
        for i, item in enumerate(loaded.artifact_lineage)
    )


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda config, tmp: (tmp / "counts.json").write_text(
                json.dumps({"records": [{"OA21CD": "E00999999", "usual_residents": 1}]})
            ),
            "SHA-256 mismatch",
        ),
        (
            lambda config, tmp: (tmp / "oa.json").write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "crs": "EPSG:27700",
                        "features": [
                            {
                                "type": "Feature",
                                "properties": {"OA21CD": "E00123456", "unexpected": "no"},
                                "geometry": {"type": "Point", "coordinates": [1, 2]},
                            }
                        ],
                    }
                )
            ),
            "SHA-256 mismatch",
        ),
    ],
)
def test_rejects_changed_bytes_before_parsing(tmp_path: Path, mutate: object, message: str) -> None:
    config = population_config(tmp_path)
    assert callable(mutate)
    mutate(config, tmp_path)

    with pytest.raises(GovernedEvidenceLoadError, match=message):
        load_population_reach_evidence(config, base_directory=tmp_path)


def test_rejects_extra_schema_keys_duplicate_ids_and_incomplete_joins(tmp_path: Path) -> None:
    config = population_config(tmp_path)
    payload = json.loads((tmp_path / "oa.json").read_text())
    payload["features"].append(payload["features"][0])
    bytes_ = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    (tmp_path / "oa.json").write_bytes(bytes_)
    changed = config.model_copy(
        update={
            "output_area_geometry": config.output_area_geometry.model_copy(
                update={"content_sha256": hashlib.sha256(bytes_).hexdigest()}
            )
        }
    )

    with pytest.raises(GovernedEvidenceLoadError, match="duplicate OA21CD"):
        load_population_reach_evidence(changed, base_directory=tmp_path)

    payload["features"] = payload["features"][:1]
    payload["features"][0]["properties"]["extra"] = "not permitted"
    bytes_ = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    (tmp_path / "oa.json").write_bytes(bytes_)
    changed = changed.model_copy(
        update={
            "output_area_geometry": changed.output_area_geometry.model_copy(
                update={"content_sha256": hashlib.sha256(bytes_).hexdigest()}
            )
        }
    )
    with pytest.raises(GovernedEvidenceLoadError, match="must contain exactly"):
        load_population_reach_evidence(changed, base_directory=tmp_path)


def test_rejects_mismatched_canonical_oa_join_ids_after_hash_verification(tmp_path: Path) -> None:
    config = population_config(tmp_path)
    payload = {"records": [{"OA21CD": "E00999999", "usual_residents": 123}]}
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    (tmp_path / "counts.json").write_bytes(content)
    changed = config.model_copy(
        update={
            "usual_resident_counts": config.usual_resident_counts.model_copy(
                update={"content_sha256": hashlib.sha256(content).hexdigest()}
            )
        }
    )

    with pytest.raises(GovernedEvidenceLoadError, match="complete exact join"):
        load_population_reach_evidence(changed, base_directory=tmp_path)


def test_rejects_nonfinite_geometry_and_noncanonical_oa_ids_after_hash_verification(
    tmp_path: Path,
) -> None:
    config = population_config(tmp_path)
    payload = geojson("e00123456", {"type": "Point", "coordinates": [float("nan"), 1]})
    bytes_ = json.dumps(payload, allow_nan=True, sort_keys=True, separators=(",", ":")).encode()
    (tmp_path / "pwc.json").write_bytes(bytes_)
    changed = config.model_copy(
        update={
            "population_weighted_centroids": config.population_weighted_centroids.model_copy(
                update={"content_sha256": hashlib.sha256(bytes_).hexdigest()}
            )
        }
    )

    with pytest.raises(GovernedEvidenceLoadError, match="non-finite JSON"):
        load_population_reach_evidence(changed, base_directory=tmp_path)


def test_rejects_noncanonical_ids_after_hash_verification(
    tmp_path: Path,
) -> None:
    config = population_config(tmp_path)
    payload = geojson("e00123456", {"type": "Point", "coordinates": [400050]})
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    (tmp_path / "pwc.json").write_bytes(content)
    changed = config.model_copy(
        update={
            "population_weighted_centroids": config.population_weighted_centroids.model_copy(
                update={"content_sha256": hashlib.sha256(content).hexdigest()}
            )
        }
    )

    with pytest.raises(GovernedEvidenceLoadError, match="uppercase canonical ONS OA"):
        load_population_reach_evidence(changed, base_directory=tmp_path)


def test_rejects_malformed_geometry_after_hash_verification(tmp_path: Path) -> None:
    config = population_config(tmp_path)
    payload = geojson("E00123456", {"type": "Point", "coordinates": [400050]})
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    (tmp_path / "pwc.json").write_bytes(content)
    changed = config.model_copy(
        update={
            "population_weighted_centroids": config.population_weighted_centroids.model_copy(
                update={"content_sha256": hashlib.sha256(content).hexdigest()}
            )
        }
    )

    with pytest.raises(GovernedEvidenceLoadError, match="two or three ordinates"):
        load_population_reach_evidence(changed, base_directory=tmp_path)


def education_configs(
    tmp_path: Path,
) -> tuple[SchoolRegisterEvidenceConfig, StrategicEducationDestinationAdmissionConfig]:
    register = write_payload(
        tmp_path,
        "school-register.json",
        {
            "schema": "satn-school-register/v1",
            "register": {
                "source_id": "school-register",
                "source_name": "B&NES school register",
                "authority_id": "banes-council",
                "as_of": "2026-07-01",
                "governed": True,
                "current": True,
                "status": "current",
            },
            "schools": [
                {
                    "school_id": "secondary-one",
                    "name": "Secondary One",
                    "phase": "secondary",
                    "record_status": "current",
                },
                {
                    "school_id": "unknown-phase",
                    "name": "Unknown Phase",
                    "phase": "unresolved",
                    "record_status": "current",
                },
            ],
        },
    )
    admissions = write_payload(
        tmp_path,
        "admissions.json",
        {
            "schema": "satn-strategic-education-destination-admission/v1",
            "authority": {
                "authority_id": "banes-council",
                "source_id": "admissions",
                "governed": True,
                "effective_date": "2026-07-01",
            },
            "admissions": [
                {
                    "record_id": "university-record",
                    "record_version": "1",
                    "strategic_destination_id": "university-one",
                    "site_id": "university-one",
                    "destination_type": "university",
                    "name": "University One",
                    "site_status": "current",
                    "disposition": "admitted",
                    "admitted_on": "2026-07-01",
                    "admission_authority_id": "banes-council",
                    "rationale": "configured-strategic-education-destination",
                    "review_trigger": "governed-destination-record-changes",
                    "access_point_evidence_ids": ["university-entrance-record"],
                }
            ],
        },
    )
    return (
        SchoolRegisterEvidenceConfig(school_register=register),
        StrategicEducationDestinationAdmissionConfig(admissions=admissions),
    )


def test_loads_school_and_strategic_destination_snapshot_without_inventing_claims(
    tmp_path: Path,
) -> None:
    school_config, admissions_config = education_configs(tmp_path)
    loaded = load_education_access_evidence(
        school_config,
        admissions_config,
        base_directory=tmp_path,
        as_at=date(2026, 7, 26),
    )

    assert loaded is not None
    snapshot = loaded.source_snapshot
    assert snapshot.register_evidence.evidence_id == "school-register"
    assert [school.phase.value for school in snapshot.schools] == ["secondary", "unresolved"]
    assert [
        item.strategic_destination_id for item in snapshot.strategic_education_destinations
    ] == ["university-one"]
    assert snapshot.option_ids == ()
    assert snapshot.option_evidence == ()
    assert loaded.admissions_lineage is not None
    assert loaded.admissions_lineage.licence == "Open Government Licence v3.0"


def test_rejects_pupil_data_duplicate_school_ids_and_absent_register(tmp_path: Path) -> None:
    school_config, admissions_config = education_configs(tmp_path)
    payload = json.loads((tmp_path / "school-register.json").read_text())
    payload["schools"][0]["pupil_name"] = "prohibited"
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    (tmp_path / "school-register.json").write_bytes(content)
    changed = school_config.model_copy(
        update={
            "school_register": school_config.school_register.model_copy(
                update={"content_sha256": hashlib.sha256(content).hexdigest()}
            )
        }
    )
    with pytest.raises(GovernedEvidenceLoadError, match="must contain exactly"):
        load_education_access_evidence(
            changed,
            base_directory=tmp_path,
            as_at=date(2026, 7, 26),
        )

    assert load_education_access_evidence(None, base_directory=tmp_path) is None
    with pytest.raises(GovernedEvidenceLoadError, match="require current school-register"):
        load_education_access_evidence(
            None,
            admissions_config,
            base_directory=tmp_path,
            as_at=date(2026, 7, 26),
        )


def test_population_frame_mutation_cannot_escape_bound_compile_adapter(
    tmp_path: Path,
) -> None:
    loaded = load_population_reach_evidence(
        population_config(tmp_path),
        base_directory=tmp_path,
    )
    assert loaded is not None
    detached = loaded.output_areas
    detached.loc[:, "usual_residents"] = 999_999
    routes = gpd.GeoDataFrame(
        [{"option_id": "option-a", "geometry": LineString([(399900, 150050), (400200, 150050)])}],
        geometry="geometry",
        crs="EPSG:27700",
    )
    area = gpd.GeoDataFrame(
        [
            {
                "geometry": Polygon(
                    [(399800, 149900), (400300, 149900), (400300, 150200), (399800, 149900)]
                )
            }
        ],
        geometry="geometry",
        crs="EPSG:27700",
    )

    assessment = compile_population_reach_from_evidence(
        loaded,
        routes,
        area,
    )

    assert {record.usual_residents for record in assessment.records} == {123}
    assert loaded.output_areas["usual_residents"].tolist() == [123]
    assert f"canonical-frame:{loaded.frame_content_sha256}" in (
        loaded.source.transformation_lineage
    )


def test_rejects_far_and_swapped_population_weighted_centroids(
    tmp_path: Path,
) -> None:
    config = population_config(tmp_path)
    far = geojson(
        "E00123456",
        {"type": "Point", "coordinates": [500000, 250000]},
    )
    content = json.dumps(far, sort_keys=True, separators=(",", ":")).encode()
    (tmp_path / "pwc.json").write_bytes(content)
    changed = config.model_copy(
        update={
            "population_weighted_centroids": (
                config.population_weighted_centroids.model_copy(
                    update={"content_sha256": hashlib.sha256(content).hexdigest()}
                )
            )
        }
    )
    with pytest.raises(GovernedEvidenceLoadError, match="clearly outside"):
        load_population_reach_evidence(changed, base_directory=tmp_path)

    geometry_payload = {
        "type": "FeatureCollection",
        "crs": "EPSG:27700",
        "features": [
            {
                "type": "Feature",
                "properties": {"OA21CD": "E00123456"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [400000, 150000],
                            [400100, 150000],
                            [400100, 150100],
                            [400000, 150000],
                        ]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {"OA21CD": "E00654321"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [401000, 151000],
                            [401100, 151000],
                            [401100, 151100],
                            [401000, 151000],
                        ]
                    ],
                },
            },
        ],
    }
    centroid_payload = {
        "type": "FeatureCollection",
        "crs": "EPSG:27700",
        "features": [
            {
                "type": "Feature",
                "properties": {"OA21CD": "E00123456"},
                "geometry": {"type": "Point", "coordinates": [401050, 151025]},
            },
            {
                "type": "Feature",
                "properties": {"OA21CD": "E00654321"},
                "geometry": {"type": "Point", "coordinates": [400050, 150025]},
            },
        ],
    }
    count_payload = {
        "records": [
            {"OA21CD": "E00123456", "usual_residents": 123},
            {"OA21CD": "E00654321", "usual_residents": 456},
        ]
    }
    swapped_config = PopulationReachEvidenceConfig(
        output_area_geometry=write_payload(tmp_path, "oa-two.json", geometry_payload),
        population_weighted_centroids=write_payload(
            tmp_path,
            "pwc-two.json",
            centroid_payload,
        ),
        usual_resident_counts=write_payload(
            tmp_path,
            "counts-two.json",
            count_payload,
        ),
    )
    with pytest.raises(GovernedEvidenceLoadError, match="another OA geometry"):
        load_population_reach_evidence(
            swapped_config,
            base_directory=tmp_path,
        )


def test_concave_oa_pwc_inside_envelope_is_explicitly_supported(
    tmp_path: Path,
) -> None:
    oa_id = "E00123456"
    geometry = geojson(
        oa_id,
        {
            "type": "Polygon",
            "coordinates": [
                [
                    [400000, 150000],
                    [400400, 150000],
                    [400400, 150400],
                    [400300, 150400],
                    [400300, 150100],
                    [400100, 150100],
                    [400100, 150400],
                    [400000, 150400],
                    [400000, 150000],
                ]
            ],
        },
    )
    config = PopulationReachEvidenceConfig(
        output_area_geometry=write_payload(tmp_path, "concave-oa.json", geometry),
        population_weighted_centroids=write_payload(
            tmp_path,
            "concave-pwc.json",
            geojson(
                oa_id,
                {"type": "Point", "coordinates": [400200, 150250]},
            ),
        ),
        usual_resident_counts=write_payload(
            tmp_path,
            "concave-counts.json",
            {"records": [{"OA21CD": oa_id, "usual_residents": 5}]},
        ),
    )

    loaded = load_population_reach_evidence(config, base_directory=tmp_path)

    assert loaded is not None
    assert loaded.output_areas["OA21CD"].tolist() == [oa_id]


def test_strict_json_rejects_duplicate_members_and_boolean_coordinates(
    tmp_path: Path,
) -> None:
    config = population_config(tmp_path)
    duplicate = b'{"records":[{"OA21CD":"E00123456","OA21CD":"E00123456","usual_residents":123}]}'
    (tmp_path / "counts.json").write_bytes(duplicate)
    duplicate_config = config.model_copy(
        update={
            "usual_resident_counts": config.usual_resident_counts.model_copy(
                update={"content_sha256": hashlib.sha256(duplicate).hexdigest()}
            )
        }
    )
    with pytest.raises(GovernedEvidenceLoadError, match="duplicate member 'OA21CD'"):
        load_population_reach_evidence(
            duplicate_config,
            base_directory=tmp_path,
        )

    config = population_config(tmp_path)
    boolean_geometry = geojson(
        "E00123456",
        {"type": "Point", "coordinates": [True, 150050]},
    )
    content = json.dumps(
        boolean_geometry,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    (tmp_path / "pwc.json").write_bytes(content)
    boolean_config = config.model_copy(
        update={
            "population_weighted_centroids": (
                config.population_weighted_centroids.model_copy(
                    update={"content_sha256": hashlib.sha256(content).hexdigest()}
                )
            )
        }
    )
    with pytest.raises(GovernedEvidenceLoadError, match="non-boolean"):
        load_population_reach_evidence(
            boolean_config,
            base_directory=tmp_path,
        )


def test_all_configured_paths_require_confined_non_symlink_base(
    tmp_path: Path,
) -> None:
    config = population_config(tmp_path)
    with pytest.raises(GovernedEvidenceLoadError, match="explicit base_directory"):
        load_population_reach_evidence(config)

    leaf_link = tmp_path / "oa-link.json"
    leaf_link.symlink_to(tmp_path / "oa.json")
    leaf_config = config.model_copy(
        update={
            "output_area_geometry": config.output_area_geometry.model_copy(
                update={"path": Path("oa-link.json")}
            )
        }
    )
    with pytest.raises(GovernedEvidenceLoadError, match="must not be a symlink"):
        load_population_reach_evidence(
            leaf_config,
            base_directory=tmp_path,
        )

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    (real_parent / "oa.json").write_bytes((tmp_path / "oa.json").read_bytes())
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(real_parent, target_is_directory=True)
    parent_config = config.model_copy(
        update={
            "output_area_geometry": config.output_area_geometry.model_copy(
                update={"path": Path("parent-link/oa.json")}
            )
        }
    )
    with pytest.raises(GovernedEvidenceLoadError, match="symlink components"):
        load_population_reach_evidence(
            parent_config,
            base_directory=tmp_path,
        )

    real_base = tmp_path / "real-base"
    real_base.mkdir()
    base_config = population_config(real_base)
    base_link = tmp_path / "base-link"
    base_link.symlink_to(real_base, target_is_directory=True)
    with pytest.raises(GovernedEvidenceLoadError, match="symlink components"):
        load_population_reach_evidence(
            base_config,
            base_directory=base_link,
        )


def test_education_binding_changes_with_raw_bytes_and_lineage_metadata(
    tmp_path: Path,
) -> None:
    school_config, admissions_config = education_configs(tmp_path)
    first = load_education_access_evidence(
        school_config,
        admissions_config,
        base_directory=tmp_path,
        as_at=date(2026, 7, 26),
    )
    assert first is not None
    payload = json.loads((tmp_path / "school-register.json").read_text())
    different_bytes = json.dumps(payload, indent=2).encode()
    (tmp_path / "school-register-copy.json").write_bytes(different_bytes)
    byte_changed_config = SchoolRegisterEvidenceConfig(
        school_register=school_config.school_register.model_copy(
            update={
                "path": Path("school-register-copy.json"),
                "content_sha256": hashlib.sha256(different_bytes).hexdigest(),
            }
        )
    )
    byte_changed = load_education_access_evidence(
        byte_changed_config,
        admissions_config,
        base_directory=tmp_path,
        as_at=date(2026, 7, 26),
    )
    metadata_changed = load_education_access_evidence(
        SchoolRegisterEvidenceConfig(
            school_register=school_config.school_register.model_copy(
                update={"release": "Governed release 2026-07 corrected"}
            )
        ),
        admissions_config,
        base_directory=tmp_path,
        as_at=date(2026, 7, 26),
    )
    later_assessment_date = load_education_access_evidence(
        school_config,
        admissions_config,
        base_directory=tmp_path,
        as_at=date(2026, 7, 27),
    )
    assert byte_changed is not None
    assert metadata_changed is not None
    assert later_assessment_date is not None
    assert (
        first.source_snapshot.source_snapshot_fingerprint
        == byte_changed.source_snapshot.source_snapshot_fingerprint
        == metadata_changed.source_snapshot.source_snapshot_fingerprint
    )
    assert first.governed_source_fingerprint != byte_changed.governed_source_fingerprint
    assert first.governed_source_fingerprint != metadata_changed.governed_source_fingerprint
    assert first.governed_source_fingerprint != later_assessment_date.governed_source_fingerprint

    governed_assessment = assess_education_access_from_evidence(
        first,
        option_evidence=(),
    )
    assert governed_assessment.governed_input_fingerprint
    assert governed_assessment.as_at == date(2026, 7, 26)
    assert governed_assessment.artifact_lineage == (
        first.school_register_lineage,
        first.admissions_lineage,
    )


@pytest.mark.parametrize(
    ("field", "invalid", "message"),
    [
        ("governed", False, "literal true"),
        ("current", False, "current status"),
        ("status", "historic", "current status"),
    ],
)
def test_school_register_requires_explicit_current_governed_authority(
    tmp_path: Path,
    field: str,
    invalid: object,
    message: str,
) -> None:
    school_config, _ = education_configs(tmp_path)
    payload = json.loads((tmp_path / "school-register.json").read_text())
    payload["register"][field] = invalid
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    (tmp_path / "school-register.json").write_bytes(content)
    changed = SchoolRegisterEvidenceConfig(
        school_register=school_config.school_register.model_copy(
            update={"content_sha256": hashlib.sha256(content).hexdigest()}
        )
    )

    with pytest.raises(GovernedEvidenceLoadError, match=message):
        load_education_access_evidence(
            changed,
            base_directory=tmp_path,
            as_at=date(2026, 7, 26),
        )


def test_rejects_stale_register_and_future_or_unauthorised_admission(
    tmp_path: Path,
) -> None:
    school_config, admissions_config = education_configs(tmp_path)
    stale_payload = json.loads((tmp_path / "school-register.json").read_text())
    stale_payload["register"]["as_of"] = "2000-01-01"
    stale_content = json.dumps(
        stale_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    (tmp_path / "school-register.json").write_bytes(stale_content)
    stale = SchoolRegisterEvidenceConfig(
        school_register=school_config.school_register.model_copy(
            update={
                "effective_date": date(2000, 1, 1),
                "content_sha256": hashlib.sha256(stale_content).hexdigest(),
            }
        )
    )
    with pytest.raises(GovernedEvidenceLoadError, match="stale"):
        load_education_access_evidence(
            stale,
            base_directory=tmp_path,
            as_at=date(2026, 7, 26),
        )

    school_config, admissions_config = education_configs(tmp_path)
    future_payload = json.loads((tmp_path / "admissions.json").read_text())
    future_payload["admissions"][0]["admitted_on"] = "2026-07-02"
    future_content = json.dumps(
        future_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    (tmp_path / "admissions.json").write_bytes(future_content)
    future = StrategicEducationDestinationAdmissionConfig(
        admissions=admissions_config.admissions.model_copy(
            update={"content_sha256": hashlib.sha256(future_content).hexdigest()}
        )
    )
    with pytest.raises(GovernedEvidenceLoadError, match="after the artifact"):
        load_education_access_evidence(
            school_config,
            future,
            base_directory=tmp_path,
            as_at=date(2026, 7, 26),
        )

    future_payload["admissions"][0]["admitted_on"] = "2026-07-01"
    future_payload["admissions"][0]["admission_authority_id"] = "other-authority"
    unauthorised_content = json.dumps(
        future_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    (tmp_path / "admissions.json").write_bytes(unauthorised_content)
    unauthorised = StrategicEducationDestinationAdmissionConfig(
        admissions=admissions_config.admissions.model_copy(
            update={"content_sha256": hashlib.sha256(unauthorised_content).hexdigest()}
        )
    )
    with pytest.raises(GovernedEvidenceLoadError, match="authority mismatch"):
        load_education_access_evidence(
            school_config,
            unauthorised,
            base_directory=tmp_path,
            as_at=date(2026, 7, 26),
        )


def test_loader_is_an_explicit_compilation_dependency() -> None:
    components = {item["path"]: item for item in compilation_dependency_manifest()["components"]}

    assert components["satn/psa_evidence_loaders.py"]["reason"] == (
        "strict governed Preferred Strategic Alignment evidence loading"
    )
