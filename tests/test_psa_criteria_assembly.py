"""Governed packet-assembly regressions for Preferred Strategic Alignment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date

import geopandas as gpd
import pytest
from shapely.geometry import Polygon
from test_alignment_selection import candidate, profile
from test_psa_evidence_loaders import education_configs, population_config

from satn.alignment_selection import CanonicalLineString, admit_candidate_set
from satn.education_access import SchoolAccessEvidence
from satn.population_reach import PopulationReachProfile
from satn.psa_criteria_assembly import (
    CriteriaAssemblyInput,
    assemble_prepared_candidate_criteria,
)
from satn.psa_evidence_loaders import (
    load_education_access_evidence,
    load_population_reach_evidence,
)
from satn.scenario_compilation import (
    PreparedScenarioCompilationInput,
    compile_prepared_scenario,
)
from satn.spine_access_candidate_preparation import (
    CandidatePreparationIssue,
    PreparedConnectionRosterRecord,
    PreparedSpineAccessConnection,
    SpineAccessCandidatePreparationResult,
)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _evidence(tmp_path):
    population = load_population_reach_evidence(
        population_config(tmp_path), base_directory=tmp_path, pwc_outside_tolerance_m=0
    )
    school_config, admissions_config = education_configs(tmp_path)
    education = load_education_access_evidence(
        school_config,
        admissions_config,
        base_directory=tmp_path,
        as_at=date(2026, 7, 26),
        school_register_max_age_days=30,
        strategic_admissions_max_age_days=30,
    )
    assert population is not None and education is not None
    return population, education


def _connection(
    label: str = "one", *, gap: bool = False, all_rejected: bool = False
) -> PreparedSpineAccessConnection:
    endpoints = (f"community-{label}", f"parent-{label}")
    selection_profile = profile(maximum=5)
    if gap:
        candidates = ()
    else:
        candidates = (
            candidate(
                f"{label}-near",
                role="community-access",
                endpoints=endpoints,
                places=endpoints,
                obligations=("secondary-one",),
                destinations=(() if all_rejected else ("university-one",)),
                geometry=CanonicalLineString(
                    coordinates=((400000.0, 150050.0), (400100.0, 150050.0))
                ),
            ),
            candidate(
                f"{label}-far",
                role="community-access",
                endpoints=endpoints,
                places=endpoints,
                obligations=("secondary-one",),
                destinations=(() if all_rejected else ("university-one",)),
                geometry=CanonicalLineString(
                    coordinates=((400000.0, 151000.0), (400100.0, 151000.0))
                ),
            ),
        )
    admitted = admit_candidate_set(
        selection_profile,
        network_role="community-access",
        endpoints=endpoints,
        candidates=candidates,
        mandatory_network_place_ids=endpoints,
        mandatory_access_obligation_ids=("secondary-one",),
        mandatory_strategic_destination_ids=("university-one",),
    )
    return PreparedSpineAccessConnection(
        access_connection_id=f"access-{label}",
        candidate_set=admitted,
        root_spine_id="spine-a4",
        strategic_source_id="source",
        strategic_evidence_id="evidence",
        strategic_provenance={},
        obligation_kind="community",
        parent_role="spine-access-connection",
        community_id=endpoints[0],
        place_id=endpoints[0],
        parent_place_id=endpoints[1],
        candidate_generation_rationales=(),
        candidate_records=(),
    )


def _preparation(*items, population, education, status: str = "prepared"):
    roster = tuple(
        PreparedConnectionRosterRecord(
            access_connection_id=item.access_connection_id,
            obligation_kind="community",
            parent_role="spine-access-connection",
            community_id=item.community_id,
            place_id=item.place_id,
            parent_place_id=item.parent_place_id,
            disposition=(
                "prepared-candidate-set"
                if item.candidate_set.admitted_candidates
                else "prepared-candidate-set-gap"
            ),
        )
        for item in items
    )
    lineage = {
        "population": {
            "source": population.source.canonical(),
            "source_content_sha256": population.source.content_sha256,
            "frame_content_sha256": population.frame_content_sha256,
            "artifact_lineage": [entry.canonical() for entry in population.artifact_lineage],
        },
        "education": {
            "governed_source_fingerprint": education.governed_source_fingerprint,
            "source_snapshot": education.source_snapshot.model_dump(mode="json"),
            "school_register_lineage": education.school_register_lineage.canonical(),
            "admissions_lineage": education.admissions_lineage.canonical(),
            "as_at": education.as_at.isoformat(),
        },
    }
    unbound = SpineAccessCandidatePreparationResult(
        contract="satn-spine-access-candidate-preparation/v1",
        profile_fingerprint=items[0].candidate_set.profile_fingerprint,
        status=status,
        prepared_spine_access_connections=tuple(items),
        connection_roster=roster,
        generation_issues=(),
        missing_inputs=(() if status == "prepared" else ("population-reach-evidence",)),
        evidence_fingerprints=tuple(
            sorted(
                {
                    population.source.content_sha256,
                    population.frame_content_sha256,
                    *(entry.content_sha256 for entry in population.artifact_lineage),
                    education.governed_source_fingerprint,
                    education.school_register_lineage.content_sha256,
                    education.admissions_lineage.content_sha256,
                }
            )
        ),
        evidence_lineage=lineage,
        preparation_fingerprint="0" * 64,
        diagnostics={
            "expected_connection_roster_count": len(roster),
            "prepared_connection_count": len(roster),
            "out_of_scope_connection_count": 0,
            "unresolved_connection_count": 0,
        },
    )
    return replace(unbound, preparation_fingerprint=_hash(unbound.canonical_payload()))


def _area() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"geometry": [Polygon([(399900, 149900), (400200, 149900), (400200, 150200)])]},
        geometry="geometry",
        crs="EPSG:27700",
    )


def _request(preparation, population, education, **changes):
    values = {
        "preparation": preparation,
        "population_evidence": population,
        "education_evidence": education,
        "area_definition": _area(),
        "option_education_evidence": {},
    }
    values.update(changes)
    return CriteriaAssemblyInput(**values)


def test_assembles_population_comparison_and_honest_missing_education_rows(tmp_path) -> None:
    population, education = _evidence(tmp_path)
    connection = _connection()
    preparation = _preparation(connection, population=population, education=education)
    result = assemble_prepared_candidate_criteria(
        _request(
            preparation,
            population,
            education,
        )
    )

    assert result.status == "assembled"
    packet = result.packets[0].criteria
    assert sorted(item.resident_count for item in packet.population.headline_500m) == [0, 123]
    assert {item.state.value for item in packet.education.completeness} == {"unsatisfied"}
    assert {item.state.value for item in packet.uncertainty} == {"unknown"}
    assert len(packet.evidence_snapshot.assessments) == 4
    assert result.diagnostics["selection_performed"] is False
    scenario = compile_prepared_scenario(
        preparation,
        PreparedScenarioCompilationInput(
            area_fingerprint=_hash({"area": "criteria-assembly-test"}),
            criteria=result.packets,
        ),
    )
    assert scenario.status == "review-required"


def test_empty_candidate_set_is_a_gap_with_exact_four_source_bindings(tmp_path) -> None:
    population, education = _evidence(tmp_path)
    connection = _connection(gap=True)
    result = assemble_prepared_candidate_criteria(
        _request(
            _preparation(connection, population=population, education=education),
            population,
            education,
        )
    )

    gap = result.packets[0].criteria
    assert gap.generation_gap_reason.value == "no-generated-candidates"
    assert {item.kind.value for item in gap.evidence_snapshot.assessments} == {
        "population-reach",
        "education-access",
        "network-geometry",
        "topography",
    }
    assert gap.unsatisfied_strategic_destination_ids == ("university-one",)


def test_all_rejected_candidate_set_retains_its_exact_generation_gap(tmp_path) -> None:
    population, education = _evidence(tmp_path)
    connection = _connection(all_rejected=True)
    result = assemble_prepared_candidate_criteria(
        _request(
            _preparation(connection, population=population, education=education),
            population,
            education,
        )
    )

    gap = result.packets[0].criteria
    assert gap.generation_gap_reason.value == "all-generated-candidates-rejected"
    assert gap.rejected_candidate_ids == tuple(
        item.candidate_id for item in connection.candidate_set.admissions
    )


def test_foreign_education_option_and_incomplete_preparation_are_honest(tmp_path) -> None:
    population, education = _evidence(tmp_path)
    connection = _connection()
    preparation = _preparation(connection, population=population, education=education)
    with pytest.raises(ValueError, match="foreign to the prepared candidate set"):
        assemble_prepared_candidate_criteria(
            _request(
                preparation,
                population,
                education,
                option_education_evidence={
                    connection.access_connection_id: (
                        SchoolAccessEvidence(
                            option_id="foreign-option",
                            school_id="secondary-one",
                            access_point_status="unresolved",
                        ),
                    )
                },
            )
        )
    incomplete = _preparation(
        connection, population=population, education=education, status="incomplete"
    )
    result = assemble_prepared_candidate_criteria(_request(incomplete, population, education))
    assert result.status == "incomplete"
    assert result.packets == ()
    assert "candidate-preparation-not-ready" in result.missing_inputs


def test_rejects_population_profile_not_derived_from_selection_profile(tmp_path) -> None:
    population, education = _evidence(tmp_path)
    connection = _connection()
    preparation = _preparation(connection, population=population, education=education)

    with pytest.raises(ValueError, match="inconsistent with the prepared"):
        assemble_prepared_candidate_criteria(
            _request(
                preparation,
                population,
                education,
                population_profile=PopulationReachProfile(
                    comparison_tolerance_percent=1.0
                ),
            )
        )


def test_rejects_recomputed_foreign_evidence_fingerprint(tmp_path) -> None:
    population, education = _evidence(tmp_path)
    connection = _connection()
    preparation = _preparation(connection, population=population, education=education)
    foreign = replace(
        preparation,
        evidence_fingerprints=tuple(
            sorted((*preparation.evidence_fingerprints, "f" * 64))
        ),
        preparation_fingerprint="0" * 64,
    )
    foreign = replace(
        foreign,
        preparation_fingerprint=_hash(foreign.canonical_payload()),
    )

    with pytest.raises(ValueError, match="empty, foreign or stale"):
        assemble_prepared_candidate_criteria(_request(foreign, population, education))


def test_direct_to_spine_disposition_requires_exact_issue(tmp_path) -> None:
    population, education = _evidence(tmp_path)
    connection = _connection()
    preparation = _preparation(connection, population=population, education=education)
    direct = PreparedConnectionRosterRecord(
        access_connection_id="direct-to-spine",
        obligation_kind="community",
        parent_role="strategic-spine",
        community_id="community-direct",
        place_id="community-direct",
        parent_place_id=None,
        disposition="out-of-scope-direct-strategic-spine",
        reason="out-of-scope-direct-strategic-spine-attachment",
    )
    diagnostics = {
        **preparation.diagnostics,
        "expected_connection_roster_count": 2,
        "out_of_scope_connection_count": 1,
    }
    missing_issue = replace(
        preparation,
        connection_roster=(*preparation.connection_roster, direct),
        diagnostics=diagnostics,
        preparation_fingerprint="0" * 64,
    )
    missing_issue = replace(
        missing_issue,
        preparation_fingerprint=_hash(missing_issue.canonical_payload()),
    )

    with pytest.raises(ValueError, match=r"direct-to-spine.*exact evidence"):
        assemble_prepared_candidate_criteria(
            _request(missing_issue, population, education)
        )


def test_valid_direct_to_spine_only_preparation_is_explicitly_non_ready(tmp_path) -> None:
    population, education = _evidence(tmp_path)
    connection = _connection()
    seed = _preparation(connection, population=population, education=education)
    direct = PreparedConnectionRosterRecord(
        access_connection_id="direct-to-spine",
        obligation_kind="community",
        parent_role="strategic-spine",
        community_id="community-direct",
        place_id="community-direct",
        parent_place_id=None,
        disposition="out-of-scope-direct-strategic-spine",
        reason="out-of-scope-direct-strategic-spine-attachment",
    )
    issue = CandidatePreparationIssue(
        access_connection_id="direct-to-spine",
        reason="out-of-scope-direct-strategic-spine-attachment",
        detail="Direct Strategic Spine attachment is not a two-place alternative.",
    )
    direct_only = replace(
        seed,
        prepared_spine_access_connections=(),
        connection_roster=(direct,),
        generation_issues=(issue,),
        diagnostics={
            "expected_connection_roster_count": 1,
            "prepared_connection_count": 0,
            "out_of_scope_connection_count": 1,
            "unresolved_connection_count": 0,
        },
        preparation_fingerprint="0" * 64,
    )
    direct_only = replace(
        direct_only,
        preparation_fingerprint=_hash(direct_only.canonical_payload()),
    )

    result = assemble_prepared_candidate_criteria(
        _request(direct_only, population, education)
    )
    assert result.status == "incomplete"
    assert result.packets == ()
    assert result.missing_inputs == ("eligible-chained-community-connection",)
    assert result.diagnostics["reason"] == "no-promotable-community-connections"
    assert result.diagnostics["out_of_scope_connection_ids"] == ("direct-to-spine",)
    assert result.diagnostics["selection_performed"] is False
    assert result.diagnostics["publication_performed"] is False


def test_replay_order_and_detached_population_frame_are_deterministic(tmp_path) -> None:
    population, education = _evidence(tmp_path)
    first, second = _connection("first"), _connection("second")
    preparation = _preparation(first, second, population=population, education=education)
    frame = population.output_areas
    frame.loc[:, "usual_residents"] = 999999
    forward = assemble_prepared_candidate_criteria(_request(preparation, population, education))
    reversed_result = assemble_prepared_candidate_criteria(
        _request(
            preparation,
            population,
            education,
            option_education_evidence={"access-second": (), "access-first": ()},
        )
    )
    assert forward.result_fingerprint == reversed_result.result_fingerprint
    assert max(
        item.resident_count
        for item in forward.packets[0].criteria.population.headline_500m
    ) == 123


def test_request_detaches_area_definition_before_caller_mutation(tmp_path) -> None:
    population, education = _evidence(tmp_path)
    connection = _connection()
    preparation = _preparation(connection, population=population, education=education)
    area = _area()
    request = CriteriaAssemblyInput(
        preparation=preparation,
        population_evidence=population,
        education_evidence=education,
        area_definition=area,
        option_education_evidence={},
    )
    before = assemble_prepared_candidate_criteria(request)
    area.loc[0, "geometry"] = Polygon(
        [(100000, 100000), (100100, 100000), (100100, 100100)]
    )
    after = assemble_prepared_candidate_criteria(request)

    assert before.result_fingerprint == after.result_fingerprint
    assert before.packets == after.packets
