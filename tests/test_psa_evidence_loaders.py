"""Adversarial contracts for Preferred Strategic Alignment evidence loaders."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from satn.compilation_dependencies import compilation_dependency_manifest
from satn.network_selection import (
    GovernedEvidenceArtifactConfig,
    PopulationReachEvidenceConfig,
    SchoolRegisterEvidenceConfig,
    StrategicEducationDestinationAdmissionConfig,
)
from satn.psa_evidence_loaders import (
    GovernedEvidenceLoadError,
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
        release="ONS Census 2021",
        effective_date=date(2021, 3, 21),
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

    with pytest.raises(GovernedEvidenceLoadError, match="strict UTF-8 JSON"):
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

    with pytest.raises(GovernedEvidenceLoadError, match="malformed GeoJSON geometry"):
        load_population_reach_evidence(changed, base_directory=tmp_path)


def education_configs(
    tmp_path: Path,
) -> tuple[SchoolRegisterEvidenceConfig, StrategicEducationDestinationAdmissionConfig]:
    register = write_payload(
        tmp_path,
        "school-register.json",
        {
            "schema": "satn-school-register/v1",
            "register": {"source_name": "B&NES school register", "as_of": "2021-03-21"},
            "schools": [
                {"school_id": "secondary-one", "name": "Secondary One", "phase": "secondary"},
                {"school_id": "unknown-phase", "name": "Unknown Phase", "phase": "unresolved"},
            ],
        },
    )
    admissions = write_payload(
        tmp_path,
        "admissions.json",
        {
            "schema": "satn-strategic-education-destination-admission/v1",
            "admissions": [
                {
                    "record_id": "university-record",
                    "record_version": "1",
                    "strategic_destination_id": "university-one",
                    "name": "University One",
                    "admitted_on": "2021-03-21",
                    "rationale": "configured-strategic-education-destination",
                    "review_trigger": "governed-destination-record-changes",
                    "access_evidence_ids": ["admissions"],
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
        school_config, admissions_config, base_directory=tmp_path
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
        load_education_access_evidence(changed, base_directory=tmp_path)

    assert load_education_access_evidence(None, base_directory=tmp_path) is None
    with pytest.raises(GovernedEvidenceLoadError, match="require current school-register"):
        load_education_access_evidence(None, admissions_config, base_directory=tmp_path)


def test_loader_is_an_explicit_compilation_dependency() -> None:
    components = {item["path"]: item for item in compilation_dependency_manifest()["components"]}

    assert components["satn/psa_evidence_loaders.py"]["reason"] == (
        "strict governed Preferred Strategic Alignment evidence loading"
    )
