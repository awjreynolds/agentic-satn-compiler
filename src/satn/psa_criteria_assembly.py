"""Assemble exact selection criteria from governed prepared PSA evidence.

This is a deliberately narrow bridge between Spine Access candidate preparation
and :mod:`satn.scenario_compilation`.  It does not select an alignment, call an
agent, change compiled geometry, or publish anything.  It merely rederives the
typed criterion packets that a later scenario compilation may consume.

All SHA-256 values in this module are deterministic content identities.  They
are used for stale-input detection and replay lineage, never as credentials,
signatures, certificates, or trust roots.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

import geopandas as gpd
from shapely import from_wkb

from satn.alignment_selection import (
    AssessmentKind,
    CandidateCriteria,
    CandidatePopulationOptionBinding,
    CandidateSetGapEvidence,
    CriterionDetail,
    CriterionFinding,
    CriterionState,
    EducationCriterionSummary,
    ExistingAlignmentCriterionSummary,
    GovernedAssessmentBinding,
    GovernedEvidenceSnapshot,
    PopulationCriterionSummary,
    education_option_id_for_candidate,
)
from satn.education_access import OptionEducationEvidence
from satn.population_reach import PopulationReachProfile
from satn.psa_evidence_loaders import (
    EducationAccessEvidenceLoad,
    GovernedEducationAssessmentScope,
    PopulationReachEvidenceLoad,
    assess_education_access_from_evidence,
    compile_population_reach_from_evidence,
)
from satn.scenario_compilation import (
    PreparedCandidateCriteria,
    PreparedCriteriaLineage,
    prepared_network_geometry_source_fingerprint,
    prepared_topography_source_fingerprint,
)
from satn.spine_access_candidate_preparation import (
    PreparedSpineAccessConnection,
    SpineAccessCandidatePreparationResult,
)

_CONTRACT = "satn-governed-criteria-assembly/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OUT_OF_SCOPE_REASON = "out-of-scope-direct-strategic-spine-attachment"


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in sorted(value.items())}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class _BoundAreaDefinition:
    """Detached immutable CRS/geometry input owned by one request."""

    crs: str
    geometries_wkb: tuple[bytes, ...]

    @classmethod
    def from_frame(cls, frame: gpd.GeoDataFrame) -> _BoundAreaDefinition:
        if frame.empty or frame.crs is None:
            raise ValueError("area definition requires non-empty geometry and a CRS")
        try:
            records = tuple(bytes(geometry.wkb) for geometry in frame.geometry)
        except Exception as error:
            raise ValueError("area definition geometry cannot be immutably bound") from error
        return cls(crs=frame.crs.to_wkt(), geometries_wkb=records)

    def __post_init__(self) -> None:
        if not self.crs or not self.geometries_wkb:
            raise ValueError("bound area definition requires CRS and geometry")
        for record in self.geometries_wkb:
            geometry = from_wkb(record)
            if (
                geometry is None
                or geometry.is_empty
                or geometry.geom_type not in {"Polygon", "MultiPolygon"}
            ):
                raise ValueError("area definition requires Polygon or MultiPolygon geometry")

    def frame(self) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {"geometry": [from_wkb(record) for record in self.geometries_wkb]},
            geometry="geometry",
            crs=self.crs,
        )


@dataclass(frozen=True)
class CriteriaAssemblyInput:
    """Only typed governed inputs needed to create inspectable packets.

    ``option_education_evidence`` is deliberately provided by the caller.  The
    compiler may derive an option ID from a candidate, but never infers a school
    entrance, a route observation, a destination assessment, or a safety claim.
    """

    preparation: SpineAccessCandidatePreparationResult | None
    population_evidence: PopulationReachEvidenceLoad | None
    education_evidence: EducationAccessEvidenceLoad | None
    area_definition: gpd.GeoDataFrame | _BoundAreaDefinition | None
    option_education_evidence: Mapping[str, tuple[OptionEducationEvidence, ...]]
    existing_alignment: Mapping[str, ExistingAlignmentCriterionSummary] | None = None
    population_profile: PopulationReachProfile | None = None

    def __post_init__(self) -> None:
        if self.preparation is not None and not isinstance(
            self.preparation, SpineAccessCandidatePreparationResult
        ):
            raise ValueError("criteria assembly requires a preparation result or None")
        if self.population_evidence is not None and not isinstance(
            self.population_evidence, PopulationReachEvidenceLoad
        ):
            raise ValueError("criteria assembly population evidence must be governed")
        if self.education_evidence is not None and not isinstance(
            self.education_evidence, EducationAccessEvidenceLoad
        ):
            raise ValueError("criteria assembly education evidence must be governed")
        if self.area_definition is not None:
            if not isinstance(self.area_definition, gpd.GeoDataFrame):
                raise ValueError("criteria assembly area definition must be a GeoDataFrame")
            object.__setattr__(
                self,
                "area_definition",
                _BoundAreaDefinition.from_frame(self.area_definition),
            )
        if self.population_profile is not None and not isinstance(
            self.population_profile, PopulationReachProfile
        ):
            raise ValueError("criteria assembly population profile must be typed")
        option_evidence = _canonical_option_evidence(self.option_education_evidence)
        object.__setattr__(self, "option_education_evidence", option_evidence)
        existing = _canonical_existing_alignment(self.existing_alignment)
        object.__setattr__(self, "existing_alignment", existing)


@dataclass(frozen=True)
class CriteriaAssemblyResult:
    """Deeply immutable criteria packets and honest non-ready state."""

    contract: str
    status: Literal["disabled", "incomplete", "assembled"]
    preparation_fingerprint: str | None
    packets: tuple[PreparedCandidateCriteria, ...]
    missing_inputs: tuple[str, ...]
    diagnostics: Mapping[str, object]
    result_fingerprint: str

    def __post_init__(self) -> None:
        packets = tuple(
            sorted(
                (
                    PreparedCandidateCriteria(
                        access_connection_id=item.access_connection_id,
                        criteria=item.criteria,
                        preparation_lineage=item.preparation_lineage,
                    )
                    for item in self.packets
                ),
                key=lambda item: item.access_connection_id,
            )
        )
        if len({item.access_connection_id for item in packets}) != len(packets):
            raise ValueError("criteria assembly packets must have unique connections")
        missing = tuple(sorted(set(self.missing_inputs)))
        diagnostics = _freeze(self.diagnostics)
        assert isinstance(diagnostics, Mapping)
        expected = _fingerprint(
            {
                "contract": self.contract,
                "status": self.status,
                "preparation_fingerprint": self.preparation_fingerprint,
                "packet_criteria_fingerprints": [
                    item.criteria.criteria_fingerprint for item in packets
                ],
                "packet_connections": [item.access_connection_id for item in packets],
                "missing_inputs": list(missing),
                "diagnostics": _thaw(diagnostics),
            }
        )
        if self.result_fingerprint != expected:
            raise ValueError("criteria assembly result fingerprint is stale")
        object.__setattr__(self, "packets", packets)
        object.__setattr__(self, "missing_inputs", missing)
        object.__setattr__(self, "diagnostics", diagnostics)

    def metadata(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "status": self.status,
            "preparation_fingerprint": self.preparation_fingerprint,
            "packet_count": len(self.packets),
            "missing_inputs": list(self.missing_inputs),
            "packets": [
                {
                    "access_connection_id": item.access_connection_id,
                    "criteria_fingerprint": item.criteria.criteria_fingerprint,
                    "preparation_lineage": item.preparation_lineage.canonical(),
                }
                for item in self.packets
            ],
            "diagnostics": _thaw(self.diagnostics),
            "result_fingerprint": self.result_fingerprint,
        }


def assemble_prepared_candidate_criteria(
    request: CriteriaAssemblyInput,
) -> CriteriaAssemblyResult:
    """Derive one exact packet for every promotable prepared connection.

    Missing governed source inputs produce an inspectable incomplete result.
    Malformed, mismatched, or stale governed inputs raise instead: treating
    those as merely absent would make a changed input look harmless.
    """

    if not isinstance(request, CriteriaAssemblyInput):
        raise ValueError("criteria assembly requires a typed CriteriaAssemblyInput")
    preparation = request.preparation
    if preparation is None:
        return _result("disabled", None, (), (), {"reason": "no-preparation"})

    _validate_preparation(preparation)
    promotable = _promotable_connections(preparation)
    _validate_mapping_scope(request, promotable)
    if not promotable:
        return _result(
            "incomplete",
            preparation.preparation_fingerprint,
            (),
            tuple(
                sorted(
                    {
                        *preparation.missing_inputs,
                        "eligible-chained-community-connection",
                    }
                )
            ),
            {
                "promotable_connection_ids": [],
                "out_of_scope_connection_ids": _out_of_scope_ids(preparation),
                "reason": "no-promotable-community-connections",
                "selection_performed": False,
                "agent_runtime_invoked": False,
                "network_geometry_mutated": False,
                "publication_performed": False,
            },
        )
    population_profile = _population_profile_for_preparation(
        preparation,
        requested=request.population_profile,
    )
    missing = _missing_inputs(request, preparation)
    if missing:
        return _result(
            "incomplete",
            preparation.preparation_fingerprint,
            (),
            missing,
            {
                "promotable_connection_ids": [item.access_connection_id for item in promotable],
                "out_of_scope_connection_ids": _out_of_scope_ids(preparation),
                "reason": "required-governed-input-missing",
            },
        )

    assert request.population_evidence is not None
    assert request.education_evidence is not None
    assert isinstance(request.area_definition, _BoundAreaDefinition)
    _validate_preparation_evidence_identity(
        preparation,
        request.population_evidence,
        request.education_evidence,
    )
    packets = tuple(
        _assemble_connection(
            preparation,
            item,
            population_evidence=request.population_evidence,
            education_evidence=request.education_evidence,
            area_definition=request.area_definition.frame(),
            option_evidence=request.option_education_evidence.get(
                item.access_connection_id, ()
            ),
            existing_alignment=(request.existing_alignment or {}).get(
                item.access_connection_id
            ),
            population_profile=population_profile,
        )
        for item in promotable
    )
    return _result(
        "assembled",
        preparation.preparation_fingerprint,
        packets,
        (),
        {
            "promotable_connection_ids": [item.access_connection_id for item in promotable],
            "out_of_scope_connection_ids": _out_of_scope_ids(preparation),
            "gap_connection_ids": [
                item.access_connection_id
                for item in packets
                if isinstance(item.criteria, CandidateSetGapEvidence)
            ],
            "selection_performed": False,
            "agent_runtime_invoked": False,
            "network_geometry_mutated": False,
            "publication_performed": False,
        },
    )


def _assemble_connection(
    preparation: SpineAccessCandidatePreparationResult,
    prepared: PreparedSpineAccessConnection,
    *,
    population_evidence: PopulationReachEvidenceLoad,
    education_evidence: EducationAccessEvidenceLoad,
    area_definition: gpd.GeoDataFrame,
    option_evidence: tuple[OptionEducationEvidence, ...],
    existing_alignment: ExistingAlignmentCriterionSummary | None,
    population_profile: PopulationReachProfile | None,
    education_scope: GovernedEducationAssessmentScope | None = None,
) -> PreparedCandidateCriteria:
    candidate_set = prepared.candidate_set
    if existing_alignment is not None:
        _validate_existing_alignment(existing_alignment, prepared)
    if not candidate_set.admitted_candidates:
        criterion = _gap_criterion(
            prepared,
            population_evidence=population_evidence,
            education_evidence=education_evidence,
        )
    else:
        route_options = gpd.GeoDataFrame(
            [
                {
                    population_evidence.columns.option_id: candidate.candidate_id,
                    "geometry": candidate.geometry.as_shapely(),
                }
                for candidate in candidate_set.admitted_candidates
            ],
            geometry="geometry",
            crs="EPSG:27700",
        )
        population = compile_population_reach_from_evidence(
            population_evidence,
            route_options,
            area_definition,
            profile=population_profile,
        )
        expected_option_ids = {
            education_option_id_for_candidate(candidate, candidate_set)
            for candidate in candidate_set.admitted_candidates
        }
        _validate_option_evidence(option_evidence, expected_option_ids, prepared)
        education = assess_education_access_from_evidence(
            education_evidence,
            option_evidence=option_evidence,
            option_ids=tuple(sorted(expected_option_ids)),
            scope=education_scope,
        )
        snapshot = _snapshot(
            prepared,
            population_assessment_id=population.assessment.assessment_id,
            population_assessment_sha256=_fingerprint(population.assessment.canonical()),
            population_source_sha256=population.assessment.source.content_sha256,
            education_assessment_id=education.assessment.assessment_id,
            # EducationCriterionSummary deliberately uses its canonical
            # self-validating assessment ID as its content identity.
            education_assessment_sha256=education.assessment.assessment_id,
            education_source_sha256=(
                education.assessment.source_snapshot.source_content_fingerprint
            ),
            existing_alignment=existing_alignment,
        )
        population_summary = PopulationCriterionSummary.from_assessment(
            population.assessment,
            option_bindings=tuple(
                CandidatePopulationOptionBinding(
                    candidate_id=candidate.candidate_id,
                    option_id=candidate.candidate_id,
                    assessment_geometry_sha256=candidate.geometry.population_geometry_sha256,
                )
                for candidate in candidate_set.admitted_candidates
            ),
            scenario_evidence_snapshot_fingerprint=snapshot.snapshot_fingerprint,
        )
        education_summary = EducationCriterionSummary.from_assessment(
            education.assessment,
            candidate_set=candidate_set,
            scenario_evidence_snapshot_fingerprint=snapshot.snapshot_fingerprint,
        )
        criterion = CandidateCriteria(
            evidence_snapshot=snapshot,
            population=population_summary,
            education=education_summary,
            existing_alignment=existing_alignment,
            directness=_network_findings(
                prepared,
                detail=CriterionDetail.DIRECTNESS_EVIDENCE,
                assessment_id=_binding(snapshot, AssessmentKind.NETWORK_GEOMETRY).assessment_id,
            ),
            gradient=_gradient_findings(
                prepared,
                assessment_id=_binding(snapshot, AssessmentKind.TOPOGRAPHY).assessment_id,
            ),
            uncertainty=_uncertainty_findings(
                prepared,
                assessment_id=_binding(snapshot, AssessmentKind.NETWORK_GEOMETRY).assessment_id,
            ),
        )
    return PreparedCandidateCriteria(
        access_connection_id=prepared.access_connection_id,
        criteria=criterion,
        preparation_lineage=PreparedCriteriaLineage.from_preparation(preparation),
    )


def _gap_criterion(
    prepared: PreparedSpineAccessConnection,
    *,
    population_evidence: PopulationReachEvidenceLoad,
    education_evidence: EducationAccessEvidenceLoad,
) -> CandidateSetGapEvidence:
    candidate_set = prepared.candidate_set
    snapshot = _snapshot(
        prepared,
        population_assessment_id=(
            "population-reach-gap-" + candidate_set.candidate_set_fingerprint[:16]
        ),
        population_assessment_sha256=_fingerprint(
            {
                "contract": _CONTRACT,
                "kind": "population-reach-gap",
                "candidate_set_fingerprint": candidate_set.candidate_set_fingerprint,
                "generation_gap_reason": candidate_set.generation_gap_reason.value,
            }
        ),
        population_source_sha256=population_evidence.source.content_sha256,
        education_assessment_id=(
            "education-access-gap-" + candidate_set.candidate_set_fingerprint[:16]
        ),
        education_assessment_sha256=_fingerprint(
            {
                "contract": _CONTRACT,
                "kind": "education-access-gap",
                "candidate_set_fingerprint": candidate_set.candidate_set_fingerprint,
                "generation_gap_reason": candidate_set.generation_gap_reason.value,
            }
        ),
        education_source_sha256=education_evidence.source_snapshot.source_content_fingerprint,
        existing_alignment=None,
    )
    return CandidateSetGapEvidence(
        candidate_set=candidate_set,
        evidence_snapshot=snapshot,
        rejected_candidate_ids=tuple(
            item.candidate_id
            for item in candidate_set.admissions
            if item.disposition.value == "rejected"
        ),
        unsatisfied_network_place_ids=candidate_set.mandatory_network_place_ids,
        unsatisfied_access_obligation_ids=candidate_set.mandatory_access_obligation_ids,
        unsatisfied_strategic_destination_ids=(
            candidate_set.mandatory_strategic_destination_ids
        ),
        generation_gap_reason=candidate_set.generation_gap_reason,
    )


def _snapshot(
    prepared: PreparedSpineAccessConnection,
    *,
    population_assessment_id: str,
    population_assessment_sha256: str,
    population_source_sha256: str,
    education_assessment_id: str,
    education_assessment_sha256: str,
    education_source_sha256: str,
    existing_alignment: ExistingAlignmentCriterionSummary | None,
) -> GovernedEvidenceSnapshot:
    bindings = [
        GovernedAssessmentBinding(
            kind=AssessmentKind.POPULATION_REACH,
            assessment_id=population_assessment_id,
            assessment_content_sha256=population_assessment_sha256,
            source_content_sha256=population_source_sha256,
            method_version="satn-population-reach/v1",
        ),
        GovernedAssessmentBinding(
            kind=AssessmentKind.EDUCATION_ACCESS,
            assessment_id=education_assessment_id,
            assessment_content_sha256=education_assessment_sha256,
            source_content_sha256=education_source_sha256,
            method_version="satn-education-access-assessment/v2",
        ),
        GovernedAssessmentBinding(
            kind=AssessmentKind.NETWORK_GEOMETRY,
            assessment_id=("prepared-network-geometry-" + prepared.access_connection_id),
            assessment_content_sha256=prepared_network_geometry_source_fingerprint(prepared),
            source_content_sha256=prepared_network_geometry_source_fingerprint(prepared),
            method_version="satn-prepared-network-geometry-source/v1",
        ),
        GovernedAssessmentBinding(
            kind=AssessmentKind.TOPOGRAPHY,
            assessment_id=("prepared-topography-" + prepared.access_connection_id),
            assessment_content_sha256=prepared_topography_source_fingerprint(prepared),
            source_content_sha256=prepared_topography_source_fingerprint(prepared),
            method_version="satn-prepared-topography-source/v1",
        ),
    ]
    if existing_alignment is not None:
        bindings.append(
            GovernedAssessmentBinding(
                kind=AssessmentKind.EXISTING_ALIGNMENT,
                assessment_id=existing_alignment.proof.proof_id,
                assessment_content_sha256=existing_alignment.summary_fingerprint,
                source_content_sha256=existing_alignment.proof.fingerprint,
                method_version="satn-existing-alignment-advantage/v1",
            )
        )
    return GovernedEvidenceSnapshot(
        snapshot_id="criteria-snapshot-" + prepared.access_connection_id,
        assessments=tuple(bindings),
    )


def _network_findings(
    prepared: PreparedSpineAccessConnection,
    *,
    detail: CriterionDetail,
    assessment_id: str,
) -> tuple[CriterionFinding, ...]:
    return tuple(
        CriterionFinding(
            candidate_id=candidate.candidate_id,
            state=CriterionState.SATISFIED,
            detail=detail,
            assessment_id=assessment_id,
            evidence_record_id=_fingerprint(
                {
                    "candidate_id": candidate.candidate_id,
                    "geometry_fingerprint": candidate.geometry_fingerprint,
                    "directness_m": candidate.directness_m,
                    "candidate_evidence_fingerprints": list(candidate.evidence_fingerprints),
                    "detail": detail.value,
                }
            ),
        )
        for candidate in prepared.candidate_set.admitted_candidates
    )


def _gradient_findings(
    prepared: PreparedSpineAccessConnection,
    *,
    assessment_id: str,
) -> tuple[CriterionFinding, ...]:
    return tuple(
        CriterionFinding(
            candidate_id=candidate.candidate_id,
            state=(
                CriterionState.SATISFIED
                if candidate.maximum_gradient_pct is not None
                else CriterionState.UNKNOWN
            ),
            detail=CriterionDetail.GRADIENT_EVIDENCE,
            assessment_id=assessment_id,
            evidence_record_id=_fingerprint(
                {
                    "candidate_id": candidate.candidate_id,
                    "geometry_fingerprint": candidate.geometry_fingerprint,
                    "maximum_gradient_pct": candidate.maximum_gradient_pct,
                }
            ),
        )
        for candidate in prepared.candidate_set.admitted_candidates
    )


def _uncertainty_findings(
    prepared: PreparedSpineAccessConnection,
    *,
    assessment_id: str,
) -> tuple[CriterionFinding, ...]:
    """Preserve grey evidence unless a future governed assessment resolves it."""

    return tuple(
        CriterionFinding(
            candidate_id=candidate.candidate_id,
            state=CriterionState.UNKNOWN,
            detail=CriterionDetail.UNCERTAINTY_EVIDENCE,
            assessment_id=assessment_id,
            evidence_record_id=_fingerprint(
                {
                    "candidate_id": candidate.candidate_id,
                    "geometry_fingerprint": candidate.geometry_fingerprint,
                    "finding": "no-governed-uncertainty-assessment",
                }
            ),
        )
        for candidate in prepared.candidate_set.admitted_candidates
    )


def _binding(
    snapshot: GovernedEvidenceSnapshot,
    kind: AssessmentKind,
) -> GovernedAssessmentBinding:
    binding = snapshot.assessment(kind)
    if binding is None:  # defensive; GovernedEvidenceSnapshot already enforces it
        raise ValueError(f"criteria snapshot is missing {kind.value}")
    return binding


def _validate_option_evidence(
    option_evidence: tuple[OptionEducationEvidence, ...],
    expected_option_ids: set[str],
    prepared: PreparedSpineAccessConnection,
) -> None:
    foreign = sorted({item.option_id for item in option_evidence} - expected_option_ids)
    if foreign:
        raise ValueError(
            "education option evidence is foreign to the prepared candidate set: "
            + ", ".join(foreign)
        )
    if not expected_option_ids:
        raise ValueError("admitted prepared candidate set has no education option IDs")
    # The downstream governed adapter validates every target row, duplicate row,
    # and source record.  This boundary only proves the option belongs here.
    if prepared.access_connection_id.strip() != prepared.access_connection_id:
        raise ValueError("prepared access connection ID is not canonical")


def _validate_existing_alignment(
    summary: ExistingAlignmentCriterionSummary,
    prepared: PreparedSpineAccessConnection,
) -> None:
    """Accept a governed existing-alignment result only for these exact options."""

    candidate_set = prepared.candidate_set
    expected_candidates = {
        candidate.candidate_id for candidate in candidate_set.admitted_candidates
    }
    if set(summary.proof.candidate_ids) != expected_candidates:
        raise ValueError(
            "existing-alignment evidence does not cover exactly the admitted candidates"
        )
    if summary.proof.profile_fingerprint != candidate_set.profile_fingerprint:
        raise ValueError("existing-alignment evidence uses a foreign selection profile")


def _validate_preparation(preparation: SpineAccessCandidatePreparationResult) -> None:
    if preparation.contract != "satn-spine-access-candidate-preparation/v1":
        raise ValueError("unsupported candidate preparation contract")
    if preparation.preparation_fingerprint != _fingerprint(preparation.canonical_payload()):
        raise ValueError("candidate preparation fingerprint is stale")
    roster = tuple(
        sorted(preparation.connection_roster, key=lambda item: item.access_connection_id)
    )
    if not roster or len({item.access_connection_id for item in roster}) != len(roster):
        raise ValueError("candidate preparation requires an exhaustive unique roster")
    prepared_ids = {
        item.access_connection_id
        for item in preparation.prepared_spine_access_connections
    }
    roster_prepared_ids = {
        item.access_connection_id
        for item in roster
        if item.disposition.startswith("prepared-")
    }
    if prepared_ids != roster_prepared_ids:
        raise ValueError("prepared connection sets do not exactly match the roster")
    roster_ids = {item.access_connection_id for item in roster}
    if any(
        issue.access_connection_id not in roster_ids
        for issue in preparation.generation_issues
    ):
        raise ValueError("generation issue names no expected roster connection")
    for item in roster:
        issues = tuple(
            issue
            for issue in preparation.generation_issues
            if issue.access_connection_id == item.access_connection_id
        )
        if item.disposition == "out-of-scope-direct-strategic-spine":
            if (
                item.parent_role != "strategic-spine"
                or not any(issue.reason == _OUT_OF_SCOPE_REASON for issue in issues)
            ):
                raise ValueError("direct-to-spine roster disposition lacks exact evidence")
        elif item.disposition == "unresolved-gap" and not issues:
            raise ValueError("unresolved roster connection requires an explicit issue")
    expected_diagnostics = {
        "expected_connection_roster_count": len(roster),
        "prepared_connection_count": len(roster_prepared_ids),
        "out_of_scope_connection_count": sum(
            item.disposition == "out-of-scope-direct-strategic-spine"
            for item in roster
        ),
        "unresolved_connection_count": sum(
            item.disposition == "unresolved-gap" for item in roster
        ),
    }
    if any(
        preparation.diagnostics.get(key) != value
        for key, value in expected_diagnostics.items()
    ):
        raise ValueError("preparation roster diagnostics are stale")
    if preparation.status == "prepared":
        expected_evidence = _lineage_fingerprints(preparation.evidence_lineage)
        if (
            not expected_evidence
            or tuple(preparation.evidence_fingerprints) != expected_evidence
            or any(_SHA256.fullmatch(item) is None for item in expected_evidence)
        ):
            raise ValueError("prepared evidence fingerprints are empty, foreign or stale")


def _lineage_fingerprints(lineage: Mapping[str, object]) -> tuple[str, ...]:
    fingerprints: set[str] = set()
    population = lineage.get("population")
    education = lineage.get("education")
    if not isinstance(population, Mapping) or not isinstance(education, Mapping):
        return ()
    for key in ("source_content_sha256", "frame_content_sha256"):
        value = population.get(key)
        if isinstance(value, str):
            fingerprints.add(value)
    artifacts = population.get("artifact_lineage")
    if isinstance(artifacts, (list, tuple)):
        for item in artifacts:
            if isinstance(item, Mapping) and isinstance(
                item.get("content_sha256"), str
            ):
                fingerprints.add(item["content_sha256"])
    governed_source = education.get("governed_source_fingerprint")
    if isinstance(governed_source, str):
        fingerprints.add(governed_source)
    for key in ("school_register_lineage", "admissions_lineage"):
        item = education.get(key)
        if isinstance(item, Mapping) and isinstance(item.get("content_sha256"), str):
            fingerprints.add(item["content_sha256"])
    return tuple(sorted(fingerprints))


def _population_profile_for_preparation(
    preparation: SpineAccessCandidatePreparationResult,
    *,
    requested: PopulationReachProfile | None,
) -> PopulationReachProfile:
    profiles = {
        item.candidate_set.profile.fingerprint: item.candidate_set.profile
        for item in preparation.prepared_spine_access_connections
    }
    if len(profiles) != 1:
        raise ValueError("prepared candidate sets require one exact selection profile")
    selection_profile = next(iter(profiles.values()))
    if selection_profile.fingerprint != preparation.profile_fingerprint:
        raise ValueError("prepared selection profile fingerprint is stale")
    derived = PopulationReachProfile(
        corridor_distances_m=(
            float(selection_profile.population.headline_radius_m),
            float(selection_profile.population.sensitivity_radius_m),
        ),
        comparison_tolerance_residents=0,
        comparison_tolerance_percent=(
            selection_profile.population.near_equivalent_tolerance_pct
        ),
    )
    if requested is not None and requested != derived:
        raise ValueError(
            "requested population profile is inconsistent with the prepared "
            "Network Selection Profile"
        )
    return derived


def _promotable_connections(
    preparation: SpineAccessCandidatePreparationResult,
) -> tuple[PreparedSpineAccessConnection, ...]:
    roster_by_id = {item.access_connection_id: item for item in preparation.connection_roster}
    promotable: list[PreparedSpineAccessConnection] = []
    for item in preparation.prepared_spine_access_connections:
        roster = roster_by_id[item.access_connection_id]
        expected_endpoints = tuple(
            sorted((roster.community_id or "", roster.parent_place_id or ""))
        )
        if (
            item.candidate_set.network_role.value != "community-access"
            or roster.parent_role != "spine-access-connection"
            or roster.obligation_kind != "community"
            or not roster.community_id
            or roster.community_id != roster.place_id
            or not roster.parent_place_id
            or roster.community_id == roster.parent_place_id
            or item.obligation_kind != roster.obligation_kind
            or item.parent_role != roster.parent_role
            or item.community_id != roster.community_id
            or item.place_id != roster.place_id
            or item.parent_place_id != roster.parent_place_id
            or item.candidate_set.endpoints != expected_endpoints
            or any(
                candidate.endpoints != expected_endpoints
                or candidate.served_network_place_ids != expected_endpoints
                for candidate in item.candidate_set.candidates
            )
        ):
            raise ValueError("only chained Community Connections can receive criteria")
        promotable.append(item)
    return tuple(sorted(promotable, key=lambda item: item.access_connection_id))


def _out_of_scope_ids(preparation: SpineAccessCandidatePreparationResult) -> list[str]:
    return [
        item.access_connection_id
        for item in preparation.connection_roster
        if item.disposition == "out-of-scope-direct-strategic-spine"
    ]


def _missing_inputs(
    request: CriteriaAssemblyInput,
    preparation: SpineAccessCandidatePreparationResult,
) -> tuple[str, ...]:
    missing = set(preparation.missing_inputs)
    if preparation.status != "prepared":
        missing.add("candidate-preparation-not-ready")
    if request.population_evidence is None:
        missing.add("population-reach-evidence")
    if request.education_evidence is None:
        missing.add("education-access-evidence")
    if request.area_definition is None:
        missing.add("area-definition")
    return tuple(sorted(missing))


def _validate_mapping_scope(
    request: CriteriaAssemblyInput,
    promotable: tuple[PreparedSpineAccessConnection, ...],
) -> None:
    allowed = {item.access_connection_id for item in promotable}
    supplied = set(request.option_education_evidence) | set(request.existing_alignment or {})
    foreign = sorted(supplied - allowed)
    if foreign:
        raise ValueError(
            "criteria evidence names a non-promotable connection: " + ", ".join(foreign)
        )


def _validate_preparation_evidence_identity(
    preparation: SpineAccessCandidatePreparationResult,
    population: PopulationReachEvidenceLoad,
    education: EducationAccessEvidenceLoad,
) -> None:
    expected_population = {
        "source": population.source.canonical(),
        "source_content_sha256": population.source.content_sha256,
        "frame_content_sha256": population.frame_content_sha256,
        "artifact_lineage": [item.canonical() for item in population.artifact_lineage],
    }
    expected_education = {
        "governed_source_fingerprint": education.governed_source_fingerprint,
        "source_snapshot": education.source_snapshot.model_dump(mode="json"),
        "school_register_lineage": education.school_register_lineage.canonical(),
        "admissions_lineage": (
            education.admissions_lineage.canonical()
            if education.admissions_lineage is not None
            else None
        ),
        "as_at": education.as_at.isoformat(),
    }
    if preparation.evidence_lineage.get("population") != expected_population:
        raise ValueError("population evidence does not exactly match preparation lineage")
    if preparation.evidence_lineage.get("education") != expected_education:
        raise ValueError("education evidence does not exactly match preparation lineage")


def _canonical_option_evidence(
    value: Mapping[str, tuple[OptionEducationEvidence, ...]],
) -> Mapping[str, tuple[OptionEducationEvidence, ...]]:
    if not isinstance(value, Mapping):
        raise ValueError("option education evidence must be grouped by connection ID")
    normalized: dict[str, tuple[OptionEducationEvidence, ...]] = {}
    for connection_id, rows in value.items():
        if (
            not isinstance(connection_id, str)
            or not connection_id
            or connection_id.strip() != connection_id
        ):
            raise ValueError("option education evidence connection ID must be canonical")
        if not isinstance(rows, tuple):
            raise ValueError("option education evidence rows must be an immutable tuple")
        normalized[connection_id] = tuple(rows)
    return MappingProxyType(dict(sorted(normalized.items())))


def _canonical_existing_alignment(
    value: Mapping[str, ExistingAlignmentCriterionSummary] | None,
) -> Mapping[str, ExistingAlignmentCriterionSummary] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("existing alignment evidence must be grouped by connection ID")
    normalized: dict[str, ExistingAlignmentCriterionSummary] = {}
    for connection_id, summary in value.items():
        if (
            not isinstance(connection_id, str)
            or not connection_id
            or connection_id.strip() != connection_id
        ):
            raise ValueError("existing alignment connection ID must be canonical")
        normalized[connection_id] = ExistingAlignmentCriterionSummary.model_validate(
            summary.model_dump(mode="python")
        )
    return MappingProxyType(dict(sorted(normalized.items())))


def _result(
    status: Literal["disabled", "incomplete", "assembled"],
    preparation_fingerprint: str | None,
    packets: tuple[PreparedCandidateCriteria, ...],
    missing_inputs: tuple[str, ...],
    diagnostics: Mapping[str, object],
) -> CriteriaAssemblyResult:
    ordered_packets = tuple(sorted(packets, key=lambda item: item.access_connection_id))
    missing = tuple(sorted(set(missing_inputs)))
    frozen_diagnostics = _freeze(diagnostics)
    assert isinstance(frozen_diagnostics, Mapping)
    fingerprint = _fingerprint(
        {
            "contract": _CONTRACT,
            "status": status,
            "preparation_fingerprint": preparation_fingerprint,
            "packet_criteria_fingerprints": [
                item.criteria.criteria_fingerprint for item in ordered_packets
            ],
            "packet_connections": [item.access_connection_id for item in ordered_packets],
            "missing_inputs": list(missing),
            "diagnostics": _thaw(frozen_diagnostics),
        }
    )
    return CriteriaAssemblyResult(
        contract=_CONTRACT,
        status=status,
        preparation_fingerprint=preparation_fingerprint,
        packets=ordered_packets,
        missing_inputs=missing,
        diagnostics=frozen_diagnostics,
        result_fingerprint=fingerprint,
    )
