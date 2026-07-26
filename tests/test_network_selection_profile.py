"""Focused contract tests for the PSA Network Selection Profile."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from satn.models import CouncilConfig
from satn.network_selection import (
    AlignmentAmbiguityPolicy,
    CandidateSourceClass,
    EducationAccessProfileConfig,
    GovernedEvidenceArtifactConfig,
    NetworkSelectionProfile,
    PopulationReachEvidenceConfig,
    PopulationReachProfileConfig,
)


def profile_payload() -> dict[str, object]:
    return {
        "profile_id": "banes-existing-asset-first-v1",
        "candidate_source_precedence": [
            "verified-existing-asset",
            "a-road-corridor",
            "b-road-corridor",
            "other-routable",
        ],
    }


def artifact(name: str) -> GovernedEvidenceArtifactConfig:
    return GovernedEvidenceArtifactConfig(
        source_id=name,
        path=Path(f"governed/{name}.geojson"),
        release="ONS Census 2021",
        effective_date=date(2021, 3, 21),
        licence="Open Government Licence v3.0",
        content_sha256=hashlib.sha256(name.encode("utf-8")).hexdigest(),
        redistribution="aggregate-only",
    )


def test_profile_is_frozen_data_only_and_has_a_canonical_fingerprint() -> None:
    profile = NetworkSelectionProfile.model_validate(profile_payload())
    reordered = NetworkSelectionProfile.model_validate(
        {
            "publication": {"default_show_rejected_options": False},
            "candidate_source_precedence": profile_payload()["candidate_source_precedence"],
            "profile_id": "banes-existing-asset-first-v1",
        }
    )

    assert profile.model_config["frozen"] is True
    assert profile.canonical_json() == reordered.canonical_json()
    assert profile.fingerprint == reordered.fingerprint
    with pytest.raises(ValidationError):
        profile.profile_id = "changed"  # type: ignore[misc]


def test_candidate_precedence_is_finite_ordered_and_never_silently_normalised() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        NetworkSelectionProfile.model_validate(
            profile_payload()
            | {"candidate_source_precedence": ["a-road-corridor", "a-road-corridor"]}
        )

    profile = NetworkSelectionProfile.model_validate(
        profile_payload()
        | {
            "candidate_source_precedence": [
                "other-routable",
                "a-road-corridor",
                "verified-existing-asset",
            ]
        }
    )
    assert profile.candidate_source_precedence == (
        CandidateSourceClass.OTHER_ROUTABLE,
        CandidateSourceClass.A_ROAD_CORRIDOR,
        CandidateSourceClass.VERIFIED_EXISTING_ASSET,
    )
    assert CandidateSourceClass.B_ROAD_CORRIDOR not in profile.candidate_source_precedence

    with pytest.raises(ValidationError, match="must contain"):
        NetworkSelectionProfile.model_validate(
            profile_payload()
            | {"candidate_source_precedence": ["a-road-corridor", "other-routable"]}
        )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("headline_radius_m", 499),
        ("headline_radius_m", "500"),
        ("sensitivity_radius_m", 999),
        ("sensitivity_radius_m", "1000"),
    ],
)
def test_population_v1_radii_are_fixed_strict_constants(field: str, invalid: object) -> None:
    with pytest.raises(ValidationError):
        NetworkSelectionProfile.model_validate(profile_payload() | {"population": {field: invalid}})


def test_population_zero_tolerance_has_one_canonical_identity_and_rejects_scalar_coercion() -> None:
    zero = NetworkSelectionProfile.model_validate(profile_payload())
    negative_zero = NetworkSelectionProfile.model_validate(
        profile_payload() | {"population": {"near_equivalent_tolerance_pct": -0.0}}
    )
    assert negative_zero.population.near_equivalent_tolerance_pct == 0.0
    assert negative_zero.fingerprint == zero.fingerprint

    with pytest.raises(ValidationError):
        NetworkSelectionProfile.model_validate(
            profile_payload() | {"population": {"near_equivalent_tolerance_pct": "5"}}
        )


@pytest.mark.parametrize(
    ("population", "message"),
    [
        ({"headline_radius_m": 1000, "sensitivity_radius_m": 500}, "should be 500"),
        ({"near_equivalent_tolerance_pct": 5}, "trial or adopted"),
    ],
)
def test_population_policy_rejects_invalid_combinations(
    population: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        NetworkSelectionProfile.model_validate(profile_payload() | {"population": population})


def test_non_zero_population_tolerance_requires_an_explicit_local_status() -> None:
    profile = NetworkSelectionProfile.model_validate(
        profile_payload()
        | {
            "population": PopulationReachProfileConfig(
                near_equivalent_tolerance_pct=5,
                tolerance_status="trial",
            ).model_dump(mode="json")
        }
    )
    assert profile.population.near_equivalent_tolerance_pct == 5
    assert profile.population.tolerance_status == "trial"


def test_existing_alignment_rejects_status_only_advantage() -> None:
    with pytest.raises(ValidationError, match="no status-only tolerance"):
        NetworkSelectionProfile.model_validate(
            profile_payload() | {"existing_alignment": {"status_only_tolerance_pct": 1}}
        )


def test_existing_alignment_signed_zero_has_one_full_profile_identity() -> None:
    zero = NetworkSelectionProfile.model_validate(
        profile_payload() | {"existing_alignment": {"status_only_tolerance_pct": 0.0}}
    )
    negative_zero = NetworkSelectionProfile.model_validate(
        profile_payload() | {"existing_alignment": {"status_only_tolerance_pct": -0.0}}
    )

    assert negative_zero.existing_alignment.status_only_tolerance_pct == 0.0
    assert negative_zero.canonical_json() == zero.canonical_json()
    assert negative_zero.fingerprint == zero.fingerprint


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("existing_alignment", "require_reusable_asset_evidence_for_strong_advantage"),
        ("publication", "require_blocking_reviews_resolved"),
    ],
)
@pytest.mark.parametrize("invalid", [False, 0, 1, "false"])
def test_required_governance_switches_are_literal_true(
    section: str, field: str, invalid: object
) -> None:
    with pytest.raises(ValidationError):
        NetworkSelectionProfile.model_validate(profile_payload() | {section: {field: invalid}})


def test_education_v1_requires_the_two_independent_travel_phases_in_canonical_order() -> None:
    profile = EducationAccessProfileConfig.model_validate(
        {"independent_travel_phases": ["all-through-secondary", "secondary"]}
    )
    assert profile.independent_travel_phases == (
        "secondary",
        "all-through-secondary",
    )

    with pytest.raises(ValidationError, match="requires secondary"):
        EducationAccessProfileConfig.model_validate({"independent_travel_phases": ["secondary"]})


def test_ambiguity_review_triggers_are_canonicalised_as_a_set() -> None:
    first = AlignmentAmbiguityPolicy.model_validate(
        {"review_when": ["near-equivalent-options", "material-grey-evidence"]}
    )
    second = AlignmentAmbiguityPolicy.model_validate(
        {"review_when": ["material-grey-evidence", "near-equivalent-options"]}
    )
    assert first.review_when == second.review_when == (
        "material-grey-evidence",
        "near-equivalent-options",
    )


def test_population_evidence_requires_the_three_distinct_ons_artifacts() -> None:
    evidence = PopulationReachEvidenceConfig(
        output_area_geometry=artifact("output-areas"),
        population_weighted_centroids=artifact("pwcs"),
        usual_resident_counts=artifact("usual-residents"),
    )
    assert evidence.profile == "satn-population-reach/v1"

    with pytest.raises(ValidationError, match="three distinct"):
        PopulationReachEvidenceConfig(
            output_area_geometry=artifact("output-areas"),
            population_weighted_centroids=artifact("output-areas"),
            usual_resident_counts=artifact("usual-residents"),
        )

    with pytest.raises(ValidationError, match="distinct content identities"):
        PopulationReachEvidenceConfig(
            output_area_geometry=artifact("output-areas"),
            population_weighted_centroids=artifact("pwcs"),
            usual_resident_counts=artifact("usual-residents").model_copy(
                update={"content_sha256": artifact("output-areas").content_sha256}
            ),
        )


def test_population_evidence_revalidates_distinct_canonical_paths_after_resolution() -> None:
    with pytest.raises(ValidationError, match="three distinct artifacts"):
        CouncilConfig.model_validate(
            {
                "config_path": "/definitions/council.yaml",
                "council_id": "test-council",
                "council_name": "Test Council",
                "source": {
                    "snapshot_dir": "snapshots",
                    "population_reach_evidence": {
                        "output_area_geometry": artifact("same").model_dump(mode="json"),
                        "population_weighted_centroids": artifact("same-parent").model_dump(
                            mode="json"
                        )
                        | {"path": "governed/../governed/same.geojson"},
                        "usual_resident_counts": artifact("usual-residents").model_dump(
                            mode="json"
                        ),
                    },
                },
                "publication": {"output_dir": "output", "title": "Test"},
            }
        )


@pytest.mark.parametrize("profile_id", ["trailing-", "double--hyphen", "Uppercase"])
def test_profile_id_is_strict_kebab_case(profile_id: str) -> None:
    with pytest.raises(ValidationError, match="kebab-case"):
        NetworkSelectionProfile.model_validate(profile_payload() | {"profile_id": profile_id})


def test_profile_and_governed_evidence_parse_through_the_existing_area_config_seam() -> None:
    config = CouncilConfig.model_validate(
        {
            "config_path": "/definitions/council.yaml",
            "council_id": "test-council",
            "council_name": "Test Council",
            "source": {
                "snapshot_dir": "snapshots",
                "population_reach_evidence": {
                    "output_area_geometry": artifact("output-areas").model_dump(mode="json"),
                    "population_weighted_centroids": artifact("pwcs").model_dump(mode="json"),
                    "usual_resident_counts": artifact("usual-residents").model_dump(mode="json"),
                },
                "school_register_evidence": {
                    "school_register": artifact("school-register").model_dump(mode="json")
                },
                "strategic_education_destination_admissions": {
                    "admissions": artifact("education-admissions").model_dump(mode="json")
                },
            },
            "compilation": {"network_selection": profile_payload()},
            "publication": {"output_dir": "output", "title": "Test"},
        }
    )

    assert config.compilation.network_selection is not None
    assert (
        config.compilation.network_selection_fingerprint
        == config.compilation.network_selection.fingerprint
    )
    assert config.source.population_reach_evidence is not None
    assert config.source.population_reach_evidence.output_area_geometry.path == Path(
        "/definitions/governed/output-areas.geojson"
    )
    assert config.source.school_register_evidence is not None
    assert config.source.school_register_evidence.school_register.path == Path(
        "/definitions/governed/school-register.geojson"
    )


def test_absent_profile_and_evidence_fields_are_not_serialized_for_legacy_configs() -> None:
    config = CouncilConfig.model_validate(
        {
            "config_path": "/definitions/council.yaml",
            "council_id": "test-council",
            "council_name": "Test Council",
            "source": {"snapshot_dir": "snapshots"},
            "publication": {"output_dir": "output", "title": "Test"},
        }
    )

    payload = config.model_dump(mode="json")
    assert "network_selection" not in payload["compilation"]
    assert config.compilation.network_selection_fingerprint is None
    assert {
        "population_reach_evidence",
        "school_register_evidence",
        "strategic_education_destination_admissions",
    }.isdisjoint(payload["source"])
