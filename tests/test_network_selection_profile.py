"""Focused contract tests for the PSA Network Selection Profile."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from satn.models import AreaDefinition, CouncilConfig
from satn.network_selection import (
    AlignmentAmbiguityPolicy,
    CandidateSourceClass,
    EducationAccessProfileConfig,
    GovernedEvidenceArtifactConfig,
    NetworkSelectionProfile,
    PopulationReachEvidenceConfig,
    PopulationReachProfileConfig,
    SectionPopulationCaptureProfileConfig,
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


def vnext_profile_payload() -> dict[str, object]:
    return {
        "contract": "satn-network-selection-profile/vNext",
        "profile_id": "bath-reuse-first-vnext",
        "version": "2026-08-02",
        "candidate_class_order": [
            "existing-cycle-provision",
            "upgradeable-off-carriageway",
            "low-traffic-non-a-road",
            "a-road-major-protected-infrastructure",
        ],
        "intervention_state_order": [
            "existing-provision",
            "upgrade-required",
            "proposed-new-link",
        ],
        "comparator_order": [
            "mandatory-obligation-service",
            "reuse-class",
            "intervention-state",
            "route-detour",
            "route-effort",
            "transition-fragmentation-burden",
            "governed-constraints",
            "traffic-challenge",
            "stable-candidate-id",
        ],
        "material_difference_rules": [
            {"dimension": "route-detour", "threshold": 0.25, "unit": "ratio"},
            {"dimension": "route-effort", "threshold": 100, "unit": "m"},
        ],
        "displacement_rules": [
            {
                "reason_code": "failed-mandatory-obligation",
                "predicate": "mandatory-obligation-failed",
                "evidence_requirements": ["mandatory-obligation-assessment"],
            },
            {
                "reason_code": "detour-limit-exceeded",
                "predicate": "detour-exceeds-threshold",
                "threshold": 1.5,
                "unit": "ratio",
                "evidence_requirements": ["route-comparison"],
            },
        ],
        "unknown_value_policy": "retain-and-request-evidence",
        "traffic_profile_fingerprint": "a" * 64,
        "deterministic_tie_break": "stable-candidate-id",
        "agent_call_bound": 0,
        "maximum_options_per_candidate_set": 12,
        "maximum_hybrid_candidates_per_set": 2,
        "maximum_transitions_per_candidate": 2,
    }


def test_vnext_reuse_first_profile_is_data_declared_and_fingerprinted() -> None:
    first = NetworkSelectionProfile.model_validate(vnext_profile_payload())
    second = NetworkSelectionProfile.model_validate(
        {
            **vnext_profile_payload(),
            "comparator_order": [
                "mandatory-obligation-service",
                "reuse-class",
                "intervention-state",
                "route-detour",
                "route-effort",
                "transition-fragmentation-burden",
                "governed-constraints",
                "traffic-challenge",
                "stable-candidate-id",
            ],
        }
    )

    assert first.contract == "satn-network-selection-profile/vNext"
    assert first.version == "2026-08-02"
    assert first.candidate_class_order[0] == "existing-cycle-provision"
    assert first.comparator_order[-1] == "stable-candidate-id"
    assert first.fingerprint == second.fingerprint


def test_vnext_profile_parses_through_area_definition_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "area.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "area_id": "bath",
                "area_name": "Bath",
                "source": {"snapshot_dir": "snapshots"},
                "compilation": {"network_selection": vnext_profile_payload()},
                "publication": {"output_dir": "output", "title": "Bath SATN"},
            }
        ),
        encoding="utf-8",
    )

    config = AreaDefinition.from_yaml(path)

    assert isinstance(config, AreaDefinition)
    assert config.compilation.network_selection is not None
    assert config.compilation.network_selection.contract == (
        "satn-network-selection-profile/vNext"
    )


def test_vnext_profile_rejects_explicit_legacy_policy_fields() -> None:
    with pytest.raises(ValidationError, match="legacy"):
        NetworkSelectionProfile.model_validate(
            vnext_profile_payload() | {"population": {"near_equivalent_tolerance_pct": 0}}
        )


def test_vnext_profile_rejects_duplicate_material_difference_dimensions() -> None:
    payload = vnext_profile_payload()
    rules = payload["material_difference_rules"]
    assert isinstance(rules, list)
    with pytest.raises(ValidationError, match="material_difference_rules"):
        NetworkSelectionProfile.model_validate(
            payload | {"material_difference_rules": rules + rules[:1]}
        )


def test_vnext_profile_rejects_duplicate_displacement_reason_codes() -> None:
    payload = vnext_profile_payload()
    rules = payload["displacement_rules"]
    assert isinstance(rules, list)
    with pytest.raises(ValidationError, match="displacement_rules"):
        NetworkSelectionProfile.model_validate(
            payload | {"displacement_rules": rules + rules[:1]}
        )


def test_vnext_profile_requires_strict_generation_bounds() -> None:
    for field in (
        "maximum_options_per_candidate_set",
        "maximum_hybrid_candidates_per_set",
        "maximum_transitions_per_candidate",
    ):
        omitted = vnext_profile_payload()
        omitted.pop(field)
        with pytest.raises(ValidationError, match=field):
            NetworkSelectionProfile.model_validate(omitted)

        with pytest.raises(ValidationError, match=field):
            NetworkSelectionProfile.model_validate(vnext_profile_payload() | {field: "2"})


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


def test_section_population_profile_freezes_local_scope_radii_and_materiality() -> None:
    profile = NetworkSelectionProfile.model_validate(profile_payload())

    assert profile.section_population == SectionPopulationCaptureProfileConfig()
    assert profile.section_population.display_section_length_m == 100
    assert profile.section_population.maximum_display_section_length_m == 1000
    assert profile.section_population.urban_capture_radius_m == 250
    assert profile.section_population.rural_capture_radius_m == 750
    assert profile.section_population.material_absolute_difference_residents == 500
    assert profile.section_population.material_relative_difference_pct == 50
    assert profile.section_population.material_persistence_m == 500

    with pytest.raises(ValidationError):
        NetworkSelectionProfile.model_validate(
            profile_payload()
            | {"section_population": {"display_section_length_m": 1001}}
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
                "network_selection_as_at": "2026-07-26",
                "network_selection_school_register_max_age_days": 365,
                "network_selection_strategic_admissions_max_age_days": 365,
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
