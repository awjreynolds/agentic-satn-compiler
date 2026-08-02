"""Read-only proving and explicit reference-generation commands.

The proving gate deliberately sits beside the legacy ``corpus`` alias.  ``check``
loads a checked-in data manifest, invokes the production Parallel-Reduction seam
once, and compares its semantic result with a checked-in expectation.  It never
rewrites an expectation.  ``regenerate`` is a developer-only escape hatch and
requires an explicit staging directory so a candidate reference cannot silently
replace the checked-in one.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import geopandas as gpd
import typer
from shapely.geometry import LineString, Point, box

from satn.alignment_selection import (
    AlignmentCandidateInput,
    AssessmentKind,
    CandidateCriteria,
    CandidateGenerationGapReason,
    CandidatePopulationOptionBinding,
    CandidateSetGapEvidence,
    CanonicalLineString,
    CriterionDetail,
    CriterionFinding,
    DecisionProcessMode,
    EducationCriterionSummary,
    GovernedAssessmentBinding,
    GovernedEducationCriterionBinding,
    GovernedEvidenceSnapshot,
    PopulationCriterionSummary,
    ScenarioDecisionRecord,
    admit_candidate_set,
    education_option_id_for_candidate,
)
from satn.asset_accounting import build_asset_accounting
from satn.dft_traffic_matching import TrafficMatchPolicy, match_dft_traffic
from satn.education_access import (
    SchoolRegisterEvidence,
    assess_education_access,
    governed_education_assessment_fingerprint,
)
from satn.evidence_contracts import canonical_evidence_json
from satn.network_selection import NetworkSelectionProfile
from satn.parallel_reduction import (
    ParallelReductionRequest,
    PreloadedOfficerDecision,
    compile_parallel_reduction_scenario,
)
from satn.parallel_reduction_corpus import (
    ScriptedCorpusRuntime,
    assert_matches_expected,
    canonical_expected_result,
    load_expected_result,
    load_manifest,
    write_expected_result,
    write_expected_visual,
)
from satn.population_reach import (
    CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
    CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
    CurrentDevelopmentEvidence,
    PopulationReachProfile,
    PopulationReachSource,
    compile_population_reach,
)
from satn.publisher import _reviewable_map_collection
from satn.reviewable_network import compile_reviewable_network
from satn.scenario_compilation import (
    PreparedCandidateCriteria,
    PreparedCriteriaLineage,
    PreparedScenarioCompilationInput,
    prepared_network_geometry_source_fingerprint,
    prepared_topography_source_fingerprint,
)
from satn.spine_access_candidate_preparation import (
    PreparedCandidateRecord,
    PreparedConnectionRosterRecord,
    PreparedSpineAccessConnection,
    SpineAccessCandidatePreparationResult,
)
from satn.traffic_evidence import TrafficExposure, TrafficObservation

proving_app = typer.Typer(
    no_args_is_help=True,
    help="Run deterministic proving gates and stage reviewed reference artifacts.",
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CORPUS_ROOT = _PROJECT_ROOT / "data/corpus/parallel-reduction"
DEFAULT_MANIFEST = _CORPUS_ROOT / "parallel-reduction-reuse-first-composite-vNext.json"
DEEP_ROOT = _CORPUS_ROOT / "deep"
VNEXT_MANIFEST_CONTRACT = "satn-reuse-first-proving-manifest/v1"
VNEXT_EXPECTED_CONTRACT = "satn-reuse-first-proving-expected/v1"


def _digest(value: object) -> str:
    return hashlib.sha256(
        canonical_evidence_json(_normalise_vnext(value)).encode("ascii")
    ).hexdigest()


def _production_fingerprint(value: object) -> str:
    """Match the JSON identity algorithm used by production dataclasses."""
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _normalise_vnext(value: object) -> object:
    if isinstance(value, float):
        return format(Decimal(str(value)).normalize(), "f")
    if isinstance(value, Mapping):
        return {str(key): _normalise_vnext(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise_vnext(item) for item in value]
    return value


def _vnext_manifest(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read vNext proving manifest: {path}") from error
    if not isinstance(value, dict) or value.get("contract") != VNEXT_MANIFEST_CONTRACT:
        raise ValueError(f"unsupported vNext proving manifest contract: {path}")
    required = {
        "contract",
        "scenario_id",
        "expected_result",
        "profile",
        "area_id",
        "candidate_sets",
        "assets",
        "traffic_observations",
        "officer_inputs",
    }
    if set(value) != required:
        raise ValueError("vNext proving manifest has an unsupported or missing field")
    scenario_id = value.get("scenario_id")
    expected_result = value.get("expected_result")
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        raise ValueError("vNext scenario_id must be non-empty text")
    if not isinstance(expected_result, str):
        raise ValueError("vNext expected_result must be text")
    expected_path = (path.parent / expected_result).resolve()
    if expected_path.parent != (path.parent / "expected").resolve():
        raise ValueError("vNext expected_result must be a direct artifact in expected/")
    profile = value.get("profile")
    if not isinstance(profile, dict) or profile.get("contract") != (
        "satn-network-selection-profile/vNext"
    ):
        raise ValueError("vNext proving manifest requires a vNext selection profile")
    candidate_sets = value.get("candidate_sets")
    if not isinstance(candidate_sets, list) or not candidate_sets:
        raise ValueError("vNext proving candidate_sets must be non-empty")
    case_ids = [item.get("case_id") for item in candidate_sets if isinstance(item, dict)]
    if len(case_ids) != len(candidate_sets) or any(
        not isinstance(item, str) or not item for item in case_ids
    ):
        raise ValueError("vNext candidate-set case ids must be non-empty text")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("vNext candidate-set case ids must be unique")
    for item in candidate_sets:
        if not isinstance(item, dict):
            raise ValueError("vNext candidate sets must be objects")
        endpoints = item.get("endpoints")
        candidates = item.get("candidates")
        if (
            not isinstance(endpoints, list)
            or len(endpoints) != 2
            or any(not isinstance(endpoint, str) for endpoint in endpoints)
            or not isinstance(candidates, list)
        ):
            raise ValueError("vNext candidate set endpoints/candidates are malformed")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError("vNext candidates must be objects")
            coordinates = candidate.get("coordinates")
            if (
                not isinstance(coordinates, list)
                or len(coordinates) < 2
                or any(
                    not isinstance(point, list)
                    or len(point) != 2
                    or any(not isinstance(value, (int, float)) for value in point)
                    for point in coordinates
                )
            ):
                raise ValueError("vNext candidates require metric route coordinates")
    for field in ("assets", "traffic_observations", "officer_inputs"):
        if not isinstance(value.get(field), list):
            raise ValueError(f"vNext {field} must be a list of governed inputs")
    for item in value["assets"]:
        if not isinstance(item, dict):
            raise ValueError("vNext assets must be objects")
        coordinates = item.get("coordinates")
        if (
            not isinstance(coordinates, list)
            or len(coordinates) < 2
            or any(
                not isinstance(point, list)
                or len(point) != 2
                or any(not isinstance(number, (int, float)) for number in point)
                for point in coordinates
            )
        ):
            raise ValueError("vNext assets require metric line coordinates")
    for item in value["traffic_observations"]:
        if not isinstance(item, dict) or not isinstance(item.get("route_id"), str):
            raise ValueError("vNext traffic observations require a route_id")
        if not isinstance(item.get("observations", []), list):
            raise ValueError("vNext traffic observations require an observations list")
    for item in value["officer_inputs"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("case_id"), str)
            or not isinstance(item.get("target_route_id"), str)
        ):
            raise ValueError("vNext officer inputs require case and target route IDs")
    return {**value, "path": path.resolve(), "expected_result_path": expected_path}


def _vnext_population_source() -> PopulationReachSource:
    development = CurrentDevelopmentEvidence(
        source_id="proving-development-register",
        release="synthetic-2026-08",
        effective_date=date(2026, 8, 2),
        licence="Open Government Licence v3.0",
        content_sha256=_digest("proving-development"),
        availability=CURRENT_DEVELOPMENT_EVIDENCE_AVAILABLE,
        conclusion=CURRENT_DEVELOPMENT_NO_MATERIAL_OMISSION,
    )
    return PopulationReachSource(
        source_id="proving-population-register",
        release="synthetic-population-v1",
        effective_date="2026-08-02",
        licence="Open Government Licence v3.0",
        permitted_uses=("strategic-corridor-analysis",),
        known_limitations=("Synthetic proving population only.",),
        transformation_lineage=("Synthetic OA centroids are declared in the manifest.",),
        source_uri="fixture://reuse-first-proving/population",
        version="synthetic-v1",
        content_sha256=_digest("proving-population-source"),
        current_development_evidence=development,
        current_development_evidence_id="proving-development-register",
    )


def _legacy_vnext_criteria(
    candidate_set,
    candidate_specs: Mapping[str, Mapping[str, object]],
    prepared=None,
    education_source_sha: str | None = None,
) -> CandidateCriteria:
    items = candidate_set.admitted_candidates
    option_ids = {
        item.candidate_id: f"population-option-{index}" for index, item in enumerate(items, start=1)
    }
    routes = gpd.GeoDataFrame(
        {
            "option_id": [option_ids[item.candidate_id] for item in items],
            "geometry": [item.geometry.as_shapely() for item in items],
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    oa_ids: list[str] = []
    residents: list[int] = []
    centroids: list[Point] = []
    polygons = []
    sequence = 0
    for item in items:
        spec = candidate_specs[item.candidate_id]
        midpoint = item.geometry.as_shapely().interpolate(0.5, normalized=True)
        headline = int(spec.get("population_500", 100))
        sensitivity = max(int(spec.get("population_1000", headline)) - headline, 0)
        for count, distance in ((headline, 100.0), (sensitivity, 750.0)):
            if count == 0:
                continue
            sequence += 1
            point = Point(midpoint.x, midpoint.y + distance)
            oa_ids.append(f"E{sequence:08d}")
            residents.append(count)
            centroids.append(point)
            polygons.append(point.buffer(10.0))
    output_areas = gpd.GeoDataFrame(
        {
            "OA21CD": oa_ids,
            "usual_residents": residents,
            "population_weighted_centroid": centroids,
            "geometry": polygons,
        },
        geometry="geometry",
        crs="EPSG:27700",
    )
    minx, miny, maxx, maxy = routes.total_bounds
    area = gpd.GeoDataFrame(
        {"geometry": [box(minx - 2_000, miny - 2_000, maxx + 2_000, maxy + 2_000)]},
        geometry="geometry",
        crs="EPSG:27700",
    )
    population = compile_population_reach(
        routes,
        output_areas,
        area,
        source=_vnext_population_source(),
        profile=PopulationReachProfile(comparison_tolerance_residents=0),
    )
    bindings = tuple(
        CandidatePopulationOptionBinding(
            candidate_id=item.candidate_id,
            option_id=option_ids[item.candidate_id],
            assessment_geometry_sha256=item.geometry.population_geometry_sha256,
        )
        for item in items
    )
    population_summary = PopulationCriterionSummary.from_assessment(
        population,
        option_bindings=bindings,
        scenario_evidence_snapshot_fingerprint="0" * 64,
    )

    education_assessment = assess_education_access(
        register_evidence=SchoolRegisterEvidence(
            evidence_id="proving-school-register",
            source_name="Synthetic proving register",
            as_of=date(2026, 8, 2),
        ),
        schools=(),
        option_evidence=(),
        option_ids=tuple(education_option_id_for_candidate(item, candidate_set) for item in items),
    )
    education_content_sha = _digest(education_assessment.model_dump(mode="json"))
    education_source_sha = education_source_sha or _digest("proving-education-source")
    education_binding = GovernedEducationCriterionBinding(
        school_ids=(),
        strategic_destination_ids=(),
        full_source_governed_fingerprint=education_source_sha,
        governed_input_fingerprint=governed_education_assessment_fingerprint(
            governed_source_fingerprint=education_source_sha,
            school_ids=(),
            strategic_destination_ids=(),
            assessment_content_sha256=education_content_sha,
        ),
        assessment_content_sha256=education_content_sha,
    )
    snapshot = GovernedEvidenceSnapshot(
        snapshot_id=f"proving-evidence-{candidate_set.candidate_set_id}",
        assessments=(
            GovernedAssessmentBinding(
                kind=AssessmentKind.POPULATION_REACH,
                assessment_id=population_summary.assessment_id,
                assessment_content_sha256=population_summary.assessment_content_sha256,
                source_content_sha256=population.source.content_sha256,
                method_version="population-reach/v1",
            ),
            GovernedAssessmentBinding(
                kind=AssessmentKind.EDUCATION_ACCESS,
                assessment_id=education_assessment.assessment_id,
                assessment_content_sha256=education_content_sha,
                source_content_sha256=education_source_sha,
                method_version="satn-governed-full-education-assessment-binding/v3",
            ),
            GovernedAssessmentBinding(
                kind=AssessmentKind.NETWORK_GEOMETRY,
                assessment_id=f"proving-network-{candidate_set.candidate_set_id}",
                assessment_content_sha256=_digest(candidate_set.candidate_set_fingerprint),
                source_content_sha256=(
                    prepared_network_geometry_source_fingerprint(prepared)
                    if prepared is not None
                    else _digest("proving-network-source")
                ),
                method_version="network/v1",
            ),
            GovernedAssessmentBinding(
                kind=AssessmentKind.TOPOGRAPHY,
                assessment_id=f"proving-topography-{candidate_set.candidate_set_id}",
                assessment_content_sha256=_digest("proving-topography"),
                source_content_sha256=(
                    prepared_topography_source_fingerprint(prepared)
                    if prepared is not None
                    else _digest("proving-topography-source")
                ),
                method_version="topography/v1",
            ),
        ),
    )
    population_summary = population_summary.model_copy(
        update={"scenario_evidence_snapshot_fingerprint": snapshot.snapshot_fingerprint}
    )
    education_summary = EducationCriterionSummary.from_assessment(
        education_assessment,
        candidate_set=candidate_set,
        scenario_evidence_snapshot_fingerprint=snapshot.snapshot_fingerprint,
        governed_binding=education_binding,
    )
    directness = []
    gradient = []
    uncertainty = []
    for item in items:
        spec = candidate_specs[item.candidate_id]
        state = "satisfied" if str(item.topology_state) == "satisfied" else "unknown"
        directness.append(
            CriterionFinding(
                candidate_id=item.candidate_id,
                state=state,
                detail=CriterionDetail.DIRECTNESS_EVIDENCE,
                assessment_id=f"proving-network-{candidate_set.candidate_set_id}",
                evidence_record_id=f"directness-{item.candidate_id}",
            )
        )
        gradient.append(
            CriterionFinding(
                candidate_id=item.candidate_id,
                state=("unknown" if item.maximum_gradient_pct is None else state),
                detail=CriterionDetail.GRADIENT_EVIDENCE,
                assessment_id=f"proving-topography-{candidate_set.candidate_set_id}",
                evidence_record_id=f"gradient-{item.candidate_id}",
            )
        )
        uncertainty.append(
            CriterionFinding(
                candidate_id=item.candidate_id,
                state=state,
                detail=CriterionDetail.UNCERTAINTY_EVIDENCE,
                assessment_id=f"proving-network-{candidate_set.candidate_set_id}",
                evidence_record_id=f"uncertainty-{item.candidate_id}",
            )
        )
    return CandidateCriteria(
        evidence_snapshot=snapshot,
        population=population_summary,
        education=education_summary,
        directness=tuple(sorted(directness, key=lambda item: item.candidate_id)),
        gradient=tuple(sorted(gradient, key=lambda item: item.candidate_id)),
        uncertainty=tuple(sorted(uncertainty, key=lambda item: item.candidate_id)),
    )


def _traffic_policy_for_manifest(manifest: Mapping[str, object]) -> TrafficMatchPolicy:
    raw = manifest.get("traffic_policy")
    if raw is None and isinstance(manifest.get("profile"), Mapping):
        raw = manifest["profile"].get("traffic_match_policy")
    if isinstance(raw, Mapping):
        return TrafficMatchPolicy.model_validate(raw)
    return TrafficMatchPolicy(
        policy_id="proving-dft-route-buffer",
        version="synthetic-2026-08",
        route_buffer_m=250.0,
    )


def _traffic_rows_for_route(
    manifest: Mapping[str, object],
    route_id: str,
    geometry: CanonicalLineString,
) -> tuple[TrafficObservation, ...]:
    row = next(
        (
            item
            for item in manifest.get("traffic_observations", ())
            if isinstance(item, Mapping) and str(item.get("route_id")) == route_id
        ),
        None,
    )
    observations: list[TrafficObservation] = []
    for index, raw in enumerate(row.get("observations", ()) if isinstance(row, Mapping) else ()):
        if not isinstance(raw, Mapping):
            continue
        payload = dict(raw)
        payload.setdefault("observation_id", f"{route_id}-traffic-{index + 1}")
        payload.setdefault("source_export_fingerprint", _digest(payload))
        payload.setdefault("source_layer", "aadf")
        payload.setdefault("count_point_id", f"count-point-{route_id}")
        payload.setdefault("observation_year", 2025)
        payload.setdefault("row_fingerprint", _digest({"route_id": route_id, **payload}))
        observations.append(TrafficObservation.model_validate(payload))
    result = match_dft_traffic(
        tuple(observations),
        policy=_traffic_policy_for_manifest(manifest),
        candidate_geometry=geometry.as_shapely(),
    )
    return result.observations


def _vnext_candidate(
    manifest: Mapping[str, object],
    set_spec: Mapping[str, object],
    raw: Mapping[str, object],
) -> tuple[AlignmentCandidateInput, str]:
    route_id = str(raw["route_id"])
    geometry = CanonicalLineString(
        coordinates=tuple((float(point[0]), float(point[1])) for point in raw["coordinates"])
    )
    endpoints = tuple(str(item) for item in set_spec["endpoints"])
    mandatory_places = tuple(
        str(item) for item in set_spec.get("mandatory_network_place_ids", endpoints)
    )
    mandatory_access = tuple(
        str(item) for item in set_spec.get("mandatory_access_obligation_ids", ())
    )
    mandatory_destinations = tuple(
        str(item) for item in set_spec.get("mandatory_strategic_destination_ids", ())
    )
    payload: dict[str, object] = {
        "network_role": str(set_spec.get("network_role", "community-access")),
        "endpoints": endpoints,
        "source_class": str(raw.get("source_class", "other-routable")),
        "geometry": geometry,
        "evidence_fingerprints": tuple(
            str(item) for item in raw.get("evidence_fingerprints", (_digest(f"route:{route_id}"),))
        ),
        "provenance_ids": tuple(
            str(item) for item in raw.get("provenance_ids", (f"source-{route_id}",))
        ),
        "topology_state": str(raw.get("topology_state", "satisfied")),
        "served_network_place_ids": tuple(
            str(item) for item in raw.get("served_network_place_ids", mandatory_places)
        ),
        "served_access_obligation_ids": tuple(
            str(item) for item in raw.get("served_access_obligation_ids", mandatory_access)
        ),
        "served_strategic_destination_ids": tuple(
            str(item)
            for item in raw.get("served_strategic_destination_ids", mandatory_destinations)
        ),
        "directness_m": float(raw.get("directness_m", geometry.as_shapely().length)),
        "reuse_class": str(raw["reuse_class"]),
        "intervention_state": str(raw["intervention_state"]),
        "alignment_bases": tuple(str(item) for item in raw["alignment_bases"]),
        "primary_alignment_basis": str(raw["primary_alignment_basis"]),
        "total_absolute_elevation_change_m": None
        if raw.get("elevation_change_m") is None
        else float(raw["elevation_change_m"]),
        "transition_count": int(raw.get("transition_count", 0)),
        "fragmentation_count": int(raw.get("fragmentation_count", 0)),
        "governed_evidence_ids": tuple(
            str(item) for item in raw.get("governed_evidence_ids", (f"evidence-{route_id}",))
        ),
        "maximum_gradient_pct": None
        if raw.get("maximum_gradient_pct") is None
        else float(raw["maximum_gradient_pct"]),
    }
    if any(
        str(item.get("route_id")) == route_id
        for item in manifest.get("traffic_observations", ())
        if isinstance(item, Mapping)
    ):
        payload["traffic_observations"] = _traffic_rows_for_route(manifest, route_id, geometry)
        payload["traffic_exposure"] = TrafficExposure.ON_CARRIAGEWAY
    else:
        # Exercise the production matcher for an explicit missing-traffic row.
        if manifest.get("traffic_observations"):
            _traffic_rows_for_route(manifest, route_id, geometry)
    return AlignmentCandidateInput(**payload), route_id


def _vnext_prepared_record(
    candidate: AlignmentCandidateInput, route_id: str
) -> PreparedCandidateRecord:
    evidence = json.dumps(
        {"route_id": route_id, "candidate_id": candidate.candidate_id}, sort_keys=True
    )
    return PreparedCandidateRecord(
        candidate=candidate,
        route_role=str(candidate.network_role),
        routing_edge_ids=(),
        reverse_routing_edge_ids=(),
        generation_rationale="retained raw governed route input",
        current_asset_share=1.0
        if candidate.reuse_class.value == "existing-cycle-provision"
        else 0.0,
        current_asset_evidence_json=evidence,
        official_b_road_share=0.0,
        official_b_road_evidence_json="{}",
        connection_json=json.dumps({"connection_id": route_id}),
        strategic_spine_json="{}",
    )


def _vnext_gap_criteria(
    candidate_set,
    case_id: str,
    prepared=None,
    education_source_sha: str | None = None,
) -> CandidateSetGapEvidence:
    snapshot = GovernedEvidenceSnapshot(
        snapshot_id=f"gap-{case_id}",
        assessments=tuple(
            GovernedAssessmentBinding(
                kind=kind,
                assessment_id=f"gap-{kind.value}-{case_id}",
                assessment_content_sha256=_digest((case_id, kind.value, "gap")),
                source_content_sha256=(
                    _digest("proving-population-source")
                    if kind is AssessmentKind.POPULATION_REACH
                    else education_source_sha
                    if kind is AssessmentKind.EDUCATION_ACCESS and education_source_sha
                    else prepared_network_geometry_source_fingerprint(prepared)
                    if kind is AssessmentKind.NETWORK_GEOMETRY and prepared is not None
                    else prepared_topography_source_fingerprint(prepared)
                    if kind is AssessmentKind.TOPOGRAPHY and prepared is not None
                    else _digest((kind.value, "source"))
                ),
                method_version="proving-gap-input/v1",
            )
            for kind in (
                AssessmentKind.POPULATION_REACH,
                AssessmentKind.EDUCATION_ACCESS,
                AssessmentKind.NETWORK_GEOMETRY,
                AssessmentKind.TOPOGRAPHY,
            )
        ),
    )
    return CandidateSetGapEvidence(
        candidate_set=candidate_set,
        evidence_snapshot=snapshot,
        rejected_candidate_ids=tuple(item.candidate_id for item in candidate_set.admissions),
        unsatisfied_network_place_ids=candidate_set.mandatory_network_place_ids,
        unsatisfied_access_obligation_ids=candidate_set.mandatory_access_obligation_ids,
        unsatisfied_strategic_destination_ids=candidate_set.mandatory_strategic_destination_ids,
        generation_gap_reason=(
            candidate_set.generation_gap_reason
            or CandidateGenerationGapReason.NO_GENERATED_CANDIDATES
        ),
    )


def _vnext_asset_frames(
    manifest: Mapping[str, object],
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    rows = []
    for raw in manifest.get("assets", ()):
        if not isinstance(raw, Mapping):
            continue
        row = {str(key): value for key, value in raw.items() if key != "coordinates"}
        identities = tuple(str(item) for item in raw.get("governed_evidence_ids", ()))
        row.setdefault("evidence_id", identities[0] if identities else row.get("asset_id"))
        row.setdefault("source_id", row.get("asset_id"))
        row.setdefault("source_family", "synthetic-governed-assets")
        row.setdefault("dataset", "parallel-reduction-assets")
        row.setdefault("publisher", "SATN proving corpus")
        row.setdefault("source_authority_role", "governed synthetic fixture")
        row.setdefault("effective_date", "2026-08-02")
        row.setdefault("licence", "Open Government Licence v3.0")
        row.setdefault("source_export_sha256", _digest(raw))
        row.setdefault("evidence_mode", "declared-governed-input")
        row.setdefault("coverage_state", "complete")
        row.setdefault("ingestion_contract", "satn-proving-asset-input/v1")
        row["geometry"] = LineString(raw["coordinates"])
        rows.append(row)
    context = gpd.GeoDataFrame(
        rows if rows else {"geometry": []},
        geometry="geometry",
        crs="EPSG:27700",
    )
    network = gpd.GeoDataFrame(
        {"geometry": []}, geometry="geometry", crs="EPSG:27700"
    )
    return context, network


def _compile_vnext_manifest(manifest: Mapping[str, object]) -> dict[str, object]:
    """Compile raw vNext inputs through reviewable selection and publication seams."""
    profile = NetworkSelectionProfile.model_validate(manifest["profile"])
    prepared: list[PreparedSpineAccessConnection] = []
    case_by_connection: dict[str, str] = {}
    route_by_candidate: dict[str, str] = {}
    candidate_specs: dict[str, Mapping[str, object]] = {}
    for set_spec in manifest["candidate_sets"]:
        assert isinstance(set_spec, Mapping)
        case_id = str(set_spec["case_id"])
        connection_id = f"connection-{case_id}"
        endpoints = tuple(str(item) for item in set_spec["endpoints"])
        pairs = [_vnext_candidate(manifest, set_spec, raw) for raw in set_spec["candidates"]]
        candidates = tuple(item[0] for item in pairs)
        for candidate, route_id in pairs:
            route_by_candidate[candidate.candidate_id] = route_id
            raw = next(raw for raw in set_spec["candidates"] if str(raw["route_id"]) == route_id)
            candidate_specs[candidate.candidate_id] = raw
        candidate_set = admit_candidate_set(
            profile,
            network_role=str(set_spec.get("network_role", "community-access")),
            endpoints=endpoints,
            candidates=candidates,
            mandatory_network_place_ids=tuple(
                str(item) for item in set_spec.get("mandatory_network_place_ids", endpoints)
            ),
            mandatory_access_obligation_ids=tuple(
                str(item) for item in set_spec.get("mandatory_access_obligation_ids", ())
            ),
            mandatory_strategic_destination_ids=tuple(
                str(item) for item in set_spec.get("mandatory_strategic_destination_ids", ())
            ),
        )
        prepared.append(
            PreparedSpineAccessConnection(
                access_connection_id=connection_id,
                candidate_set=candidate_set,
                root_spine_id=f"root-{case_id}",
                strategic_source_id=f"source-{case_id}",
                strategic_evidence_id=f"evidence-{case_id}",
                strategic_provenance={"raw_case_id": case_id},
                obligation_kind="community",
                parent_role="spine-access-connection",
                community_id=endpoints[0],
                place_id=endpoints[0],
                parent_place_id=endpoints[1],
                candidate_generation_rationales=tuple(
                    {"candidate_id": candidate.candidate_id, "route_id": route_id}
                    for candidate, route_id in pairs
                ),
                candidate_records=tuple(
                    _vnext_prepared_record(candidate, route_id) for candidate, route_id in pairs
                ),
            )
        )
        case_by_connection[connection_id] = case_id
    roster = tuple(
        PreparedConnectionRosterRecord(
            access_connection_id=item.access_connection_id,
            obligation_kind="community",
            parent_role="spine-access-connection",
            community_id=item.community_id,
            place_id=item.place_id,
            parent_place_id=item.parent_place_id,
            disposition="prepared-community-connection",
        )
        for item in prepared
    )
    # The preparation lineage is derived from the same governed source records
    # used by the criterion adapters, but contains no selected-route outcome.
    education_source = assess_education_access(
        register_evidence=SchoolRegisterEvidence(
            evidence_id="proving-school-register",
            source_name="Synthetic proving register",
            as_of=date(2026, 8, 2),
        ),
        schools=(),
        option_evidence=(),
        option_ids=(),
    ).source_snapshot.model_dump(mode="json")
    lineage = {
        "population": {
            "source_content_sha256": _digest("proving-population-source"),
            "frame_content_sha256": _digest(manifest["candidate_sets"]),
            "artifact_lineage": [],
        },
        "education": {
            "governed_source_fingerprint": education_source["source_content_fingerprint"],
            "school_register_lineage": {"content_sha256": _digest("proving-school-register")},
            "source_snapshot": education_source,
        },
    }
    evidence_fingerprints = tuple(
        sorted(
            {
                lineage["population"]["source_content_sha256"],
                lineage["population"]["frame_content_sha256"],
                lineage["education"]["governed_source_fingerprint"],
                lineage["education"]["school_register_lineage"]["content_sha256"],
            }
        )
    )
    diagnostics = {
        "expected_connection_roster_count": len(roster),
        "prepared_connection_count": len(roster),
        "out_of_scope_connection_count": 0,
        "unresolved_connection_count": 0,
    }
    preparation = SpineAccessCandidatePreparationResult(
        contract="satn-spine-access-candidate-preparation/v1",
        profile_fingerprint=profile.fingerprint,
        status="prepared",
        prepared_spine_access_connections=tuple(prepared),
        connection_roster=roster,
        generation_issues=(),
        missing_inputs=(),
        evidence_fingerprints=evidence_fingerprints,
        evidence_lineage=lineage,
        preparation_fingerprint="0" * 64,
        diagnostics=diagnostics,
    )
    preparation = replace(
        preparation,
        preparation_fingerprint=_production_fingerprint(preparation.canonical_payload()),
    )
    packets = []
    for item in prepared:
        criteria = (
            _legacy_vnext_criteria(
                item.candidate_set,
                candidate_specs,
                item,
                str(education_source["source_content_fingerprint"]),
            )
            if item.candidate_set.admitted_candidates
            else _vnext_gap_criteria(
                item.candidate_set,
                case_by_connection[item.access_connection_id],
                item,
                str(education_source["source_content_fingerprint"]),
            )
        )
        packets.append(
            PreparedCandidateCriteria(
                item.access_connection_id,
                criteria,
                PreparedCriteriaLineage.from_preparation(preparation),
            )
        )
    target_by_case = {
        str(item["case_id"]): item
        for item in manifest.get("officer_inputs", ())
        if isinstance(item, Mapping)
    }
    officer_inputs = []
    for case_id, raw in target_by_case.items():
        connection_id = next(
            (item for item, case in case_by_connection.items() if case == case_id), connection_id
        )
        target_route = str(raw["target_route_id"])
        target_candidate = next(
            (
                candidate_id
                for candidate_id, route_id in route_by_candidate.items()
                if route_id == target_route
            ),
            target_route,
        )
        officer_inputs.append(
            PreloadedOfficerDecision(target_id=connection_id, route_id=target_candidate)
        )
    reviewable = compile_reviewable_network(
        preparation,
        PreparedScenarioCompilationInput(
            area_fingerprint=_digest(manifest["area_id"]),
            criteria=tuple(packets),
            decision_record=ScenarioDecisionRecord(
                mode=(
                    DecisionProcessMode.PROVISIONAL_REVIEW
                    if any(not item.candidate_set.admitted_candidates for item in prepared)
                    else DecisionProcessMode.NO_AGENT
                )
            ),
            review_run_instance_id=f"proving-{str(manifest['scenario_id']).lower()}",
        ),
        officer_inputs,
    )
    case_by_set_id = {
        item.candidate_set.candidate_set_id: case_by_connection[item.access_connection_id]
        for item in prepared
    }
    context, network = _vnext_asset_frames(manifest)
    compiled = type("ProvingCompiled", (), {})()
    compiled.reviewable_network = reviewable
    compiled.spine_access_candidate_preparation = preparation
    compiled.asset_accounting = build_asset_accounting(context, network, compiled)
    points = []
    place_ids = []
    for index, item in enumerate(manifest["candidate_sets"]):
        for endpoint_index, endpoint in enumerate(item["endpoints"]):
            place_ids.append(str(endpoint))
            points.append(Point(index * 1000.0, endpoint_index * 100.0))
    compiled.places = gpd.GeoDataFrame(
        {"place_id": place_ids, "geometry": points}, geometry="geometry", crs="EPSG:27700"
    )
    map_collection = _reviewable_map_collection(compiled)
    selections = []
    scenario_selections = reviewable.scenario.selections if reviewable.scenario else ()
    for selection in scenario_selections:
        selections.append(
            {
                "case_id": case_by_set_id.get(selection.candidate_set_id, ""),
                "candidate_set_id": selection.candidate_set_id,
                "disposition": selection.disposition.value,
                "selected_candidate_id": selection.selected_candidate_id,
                "selected_route_id": route_by_candidate.get(selection.selected_candidate_id)
                if selection.selected_candidate_id
                else None,
                "retained_candidate_ids": sorted(
                    [*selection.admitted_loser_ids, *selection.complementary_candidate_ids]
                ),
                "retained_route_ids": sorted(
                    route_by_candidate.get(item)
                    for item in [
                        *selection.admitted_loser_ids,
                        *selection.complementary_candidate_ids,
                    ]
                    if route_by_candidate.get(item)
                ),
                "material_displacements": [
                    item.model_dump(mode="json") for item in selection.material_displacements
                ],
            }
        )
    map_output = []
    for feature in map_collection.get("features", []):
        props = feature.get("properties", {})
        if props.get("feature_type") not in {
            "reviewable-selected-route",
            "reviewable-unselected-candidate",
            "officer-compiler-divergence",
            "reviewable-gap-endpoint",
        }:
            continue
        candidate_id = props.get("candidate_id")
        map_output.append(
            {
                "route_id": route_by_candidate.get(candidate_id, props.get("route_id")),
                "candidate_id": candidate_id,
                "selected": props.get("feature_type") == "reviewable-selected-route",
                "display_state": props.get("display_state"),
                "primary_alignment_basis": props.get("primary_alignment_basis"),
                "feature_type": props.get("feature_type"),
                **(
                    {
                        "gap_id": props.get("gap_id"),
                        "endpoint_id": props.get("endpoint_id"),
                        "reason": props.get("reason"),
                        "missing_endpoint_geometry": props.get("missing_endpoint_geometry"),
                        "geometry_semantics": props.get("geometry_semantics"),
                    }
                    if props.get("feature_type") == "reviewable-gap-endpoint"
                    else {}
                ),
            }
        )
    officer_output = []
    for record in reviewable.officer_decisions:
        raw = target_by_case.get(case_by_connection.get(record.target_id, ""), {})
        compiler = next(
            (item for item in selections if item["candidate_set_id"] == record.candidate_set_id),
            None,
        )
        officer_output.append(
            {
                "case_id": raw.get("case_id", record.target_id),
                "target_route_id": raw.get("target_route_id", record.route_id),
                "compiler_selected_route_id": compiler.get("selected_route_id")
                if compiler
                else None,
                "disposition": "target-unavailable"
                if record.status.value == "target-unavailable"
                else "material-divergence"
                if compiler and compiler.get("selected_route_id") != raw.get("target_route_id")
                else "aligned",
            }
        )
    asset_aliases = {
        str(evidence_id): str(raw.get("asset_id"))
        for raw in manifest.get("assets", ())
        if isinstance(raw, Mapping)
        for evidence_id in raw.get("governed_evidence_ids", ())
    }
    assets = []
    for record in compiled.asset_accounting.get("records", []):
        item = {key: value for key, value in record.items() if key != "geometry"}
        item["accounting_asset_id"] = item["asset_id"]
        provenance = item.get("source_provenance", ())
        for evidence in provenance:
            if isinstance(evidence, Mapping) and evidence.get("evidence_id") in asset_aliases:
                item["asset_id"] = asset_aliases[evidence["evidence_id"]]
                break
        assets.append(item)
    return _normalise_vnext(
        {
            "contract": VNEXT_EXPECTED_CONTRACT,
            "scenario_id": manifest["scenario_id"],
            "profile_fingerprint": profile.fingerprint,
            "scenario_fingerprint": reviewable.scenario.scenario_fingerprint
            if reviewable.scenario
            else None,
            "candidate_sets": [
                {
                    "case_id": case_by_set_id.get(item.candidate_set_id, ""),
                    "candidate_set_id": item.candidate_set_id,
                    "candidate_ids": [candidate.candidate_id for candidate in item.candidates],
                    "admitted_candidate_ids": [
                        candidate.candidate_id for candidate in item.admitted_candidates
                    ],
                }
                for item in (reviewable.scenario.candidate_sets if reviewable.scenario else ())
            ],
            "selections": sorted(selections, key=lambda item: item["candidate_set_id"]),
            "network_gaps": [
                {
                    "gap_id": gap.gap_id,
                    "candidate_set_id": gap.candidate_set_id,
                    "endpoints": list(gap.endpoints),
                    "reason": gap.reason,
                }
                for gap in reviewable.network_gaps
            ],
            "assets": sorted(assets, key=lambda item: str(item.get("asset_id"))),
            "map_semantics": sorted(map_output, key=lambda item: str(item.get("route_id"))),
        "officer_decisions": sorted(officer_output, key=lambda item: item["case_id"]),
        "traffic_diagnostics": [
            dict(item)
            for item in reviewable.diagnostics.get("traffic_diagnostics", ())
            if isinstance(item, Mapping)
        ],
        "evidence_quality": [
                {"evidence_id": item.get("asset_id"), "state": item.get("evidence_state")}
                for item in assets
            ],
            "completion": {
                "reviewable_status": reviewable.status.value,
                "selection_performed": bool(reviewable.scenario),
                "asset_accounting_contract": compiled.asset_accounting.get("contract"),
                "map_contract": map_collection.get("contract"),
            },
        }
    )


def _manifest_paths(manifest: Path, deep: bool) -> tuple[Path, ...]:
    """Return the light manifest and, for a deep gate, independent vNext cases."""

    paths = [manifest]
    if deep:
        paths.extend(sorted(DEEP_ROOT.glob("*.json")))
    return tuple(path.resolve() for path in paths)


def _compile_manifest(path: Path, *, load_expected: bool = True):
    raw = json.loads(path.read_text(encoding="ascii"))
    if isinstance(raw, dict) and raw.get("contract") == VNEXT_MANIFEST_CONTRACT:
        manifest = _vnext_manifest(path)
        actual = _compile_vnext_manifest(manifest)
        expected_path = manifest["expected_result_path"]
        expected = (
            _load_vnext_expected_result(expected_path)
            if load_expected and expected_path.is_file()
            else {}
        )
        return manifest, actual, expected
    manifest = load_manifest(path)
    result = compile_parallel_reduction_scenario(
        ParallelReductionRequest.model_validate(manifest.request),
        runtime=ScriptedCorpusRuntime(manifest.runtime_responses),
    )
    actual = canonical_expected_result(manifest, result)
    expected = load_expected_result(manifest.expected_result_path)
    return manifest, actual, expected


def _validate_deep_data() -> None:
    """Validate every independent deep manifest without compiling combinations."""

    paths = sorted(DEEP_ROOT.glob("*.json"))
    if not paths:
        raise ValueError("deep proving manifest directory is empty")
    for path in paths:
        _vnext_manifest(path)


def _manifest_scenario_id(manifest: object) -> str:
    return str(manifest["scenario_id"]) if isinstance(manifest, dict) else manifest.scenario_id


def _manifest_expected_result_path(manifest: object) -> Path:
    return (
        manifest["expected_result_path"]
        if isinstance(manifest, dict)
        else manifest.expected_result_path
    )


def _manifest_expected_visual_path(manifest: object) -> Path:
    expected = _manifest_expected_result_path(manifest)
    return expected.with_suffix(".svg")


def _load_vnext_expected_result(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read vNext expected result: {path}") from error
    if not isinstance(value, dict) or value.get("contract") != VNEXT_EXPECTED_CONTRACT:
        raise ValueError("unsupported vNext proving expected-result contract")
    canonical_evidence_json(value)
    return value


def _run_check(manifest: Path, *, deep: bool) -> int:
    if deep:
        _validate_deep_data()
    checked = 0
    for path in _manifest_paths(manifest, deep):
        loaded, actual, expected = _compile_manifest(path)
        try:
            assert_matches_expected(actual, expected)
        except AssertionError as error:
            typer.echo(f"proving check drift: {_manifest_scenario_id(loaded)}: {error}", err=True)
            return 1
        checked += 1
        typer.echo(f"passed: {_manifest_scenario_id(loaded)}")
    typer.echo(f"proving check passed ({checked} composite compile(s))")
    return 0


def _assert_safe_staging_directory(staging_dir: Path, expected_paths: tuple[Path, ...]) -> Path:
    staging = staging_dir.expanduser().resolve()
    for expected in expected_paths:
        expected = expected.resolve()
        if staging == expected.parent or expected.parent in staging.parents:
            raise typer.BadParameter(
                "--staging-dir must be outside the checked-in expected/ directory",
                param_hint="--staging-dir",
            )
    staging.mkdir(parents=True, exist_ok=True)
    return staging


def _write_staged_reference(
    staging: Path, manifest, actual: dict[str, object]
) -> tuple[Path, Path]:
    expected_source = _manifest_expected_result_path(manifest)
    visual_source = _manifest_expected_visual_path(manifest)
    expected_path = staging / expected_source.name
    visual_path = staging / visual_source.name
    if expected_path.resolve() == expected_source.resolve():
        raise typer.BadParameter("staged reference resolves to the checked-in expected result")
    write_expected_result(expected_path, actual)
    write_expected_visual(visual_path, actual)
    return expected_path, visual_path


@proving_app.command("check")
def check(
    manifest: Annotated[
        Path,
        typer.Option(
            "--manifest",
            help="Data-only composite Scenario Manifest to compile.",
        ),
    ] = DEFAULT_MANIFEST,
    deep: bool = typer.Option(
        False,
        "--deep",
        help="Also run independently checked-in deep scenario manifests and data cases.",
    ),
) -> None:
    """Compile once and compare the semantic checked-in expectation read-only."""

    try:
        code = _run_check(manifest, deep=deep)
    except (AssertionError, OSError, ValueError, TypeError, RuntimeError) as error:
        typer.echo(f"proving check failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    if code:
        raise typer.Exit(code=code)


@proving_app.command("regenerate")
def regenerate(
    staging_dir: Annotated[
        Path,
        typer.Option(
            ...,
            "--staging-dir",
            help="Explicit caller-owned directory for candidate references.",
        ),
    ],
    manifest: Annotated[
        Path,
        typer.Option(
            "--manifest",
            help="Data-only composite Scenario Manifest to compile.",
        ),
    ] = DEFAULT_MANIFEST,
    deep: bool = typer.Option(
        False,
        "--deep",
        help="Also stage independently checked-in deep scenario references.",
    ),
) -> None:
    """Generate candidate references in staging only; never update checked-in files."""

    paths = _manifest_paths(manifest, deep)
    if deep:
        _validate_deep_data()
    expected_paths = tuple(
        _manifest_expected_result_path(
            _vnext_manifest(path)
            if json.loads(path.read_text(encoding="ascii")).get("contract")
            == VNEXT_MANIFEST_CONTRACT
            else load_manifest(path)
        )
        for path in paths
    )
    staging = _assert_safe_staging_directory(staging_dir, expected_paths)
    for path in paths:
        loaded, actual, _ = _compile_manifest(path, load_expected=False)
        expected_path, visual_path = _write_staged_reference(staging, loaded, actual)
        typer.echo(f"staged: {expected_path}")
        typer.echo(f"staged: {visual_path}")


@proving_app.command("promote")
def promote(
    staging_dir: Annotated[
        Path,
        typer.Option(..., "--staging-dir", help="Reviewed staging directory."),
    ],
    reviewer: Annotated[
        str,
        typer.Option(..., "--reviewer", help="Named human reviewer approving the diff."),
    ],
    rationale: Annotated[
        str,
        typer.Option(..., "--rationale", help="Short rationale for accepting the reference."),
    ],
    manifest: Annotated[
        Path,
        typer.Option("--manifest"),
    ] = DEFAULT_MANIFEST,
    approve: bool = typer.Option(
        False,
        "--approve",
        help="Explicitly authorise replacement of the checked-in expected artifacts.",
    ),
    deep: bool = typer.Option(
        False,
        "--deep",
        help="Also promote reviewed references for the independent deep manifests.",
    ),
) -> None:
    """Promote a reviewed staged pair through an explicit, atomic boundary."""

    if not approve:
        raise typer.BadParameter("pass --approve after reviewing the semantic diff")
    if not reviewer.strip() or not rationale.strip():
        raise typer.BadParameter("--reviewer and --rationale must be non-empty")
    staging = staging_dir.expanduser().resolve()
    paths = _manifest_paths(manifest, deep)
    if deep:
        _validate_deep_data()
    expected_paths = tuple(
        _manifest_expected_result_path(
            _vnext_manifest(path)
            if json.loads(path.read_text(encoding="ascii")).get("contract")
            == VNEXT_MANIFEST_CONTRACT
            else load_manifest(path)
        )
        for path in paths
    )
    staging = _assert_safe_staging_directory(staging, expected_paths)
    for path in paths:
        raw = json.loads(path.read_text(encoding="ascii"))
        loaded = (
            _vnext_manifest(path)
            if raw.get("contract") == VNEXT_MANIFEST_CONTRACT
            else load_manifest(path)
        )
        expected_source = _manifest_expected_result_path(loaded)
        visual_source = _manifest_expected_visual_path(loaded)
        staged_expected = staging / expected_source.name
        staged_visual = staging / visual_source.name
        if not staged_expected.is_file() or not staged_visual.is_file():
            raise typer.BadParameter(
                "staging directory does not contain both reference artifacts"
            )
        candidate = (
            _load_vnext_expected_result(staged_expected)
            if isinstance(loaded, dict)
            else load_expected_result(staged_expected)
        )
        expected = expected_source.resolve()
        visual = visual_source.resolve()
        expected.parent.mkdir(parents=True, exist_ok=True)
        for source, target in ((staged_expected, expected), (staged_visual, visual)):
            temporary = target.with_name(f".{target.name}.promote-{os.getpid()}")
            temporary.write_bytes(source.read_bytes())
            os.replace(temporary, target)
        typer.echo(
            f"promoted {_manifest_scenario_id(loaded)} by {reviewer.strip()}: "
            f"{rationale.strip()} ({len(candidate)} semantic fields)"
        )
