"""Compile strategic-corridor criteria and a reviewable Scenario.

This is the bounded bridge between strategic-corridor preparation and the
existing Preferred Strategic Alignment decision model.  It does not replay a
Reference SATN, alter compiler geometry, publish, or grant adoption authority.

The private adapter in this module exists only because the governed criterion
assembler predates strategic alignment units.  No strategic unit is exposed as
an Access Connection in this module's public result.
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
    CandidateCriteria,
    CandidateSetGapEvidence,
    DecisionProcessMode,
    ExistingAlignmentCriterionSummary,
    GovernedEvidenceSnapshot,
    PreferredStrategicAlignment,
    ScenarioCompilation,
    ScenarioCriteriaBinding,
    ScenarioDecisionRecord,
    ScenarioReviewDependency,
    ScenarioReviewOrchestration,
    SelectionDisposition,
    education_option_id_for_candidate,
    orchestrate_scenario_review,
    select_preferred_alignment,
)
from satn.education_access import (
    ConnectorContinuity,
    EducationAccessSourceSnapshot,
    MeasuredDistance,
    StrategicEducationDestinationEvidence,
    governed_education_assessment_fingerprint,
)
from satn.models import AccessPointStatus
from satn.population_reach import PopulationReachProfile
from satn.psa_criteria_assembly import _assemble_connection
from satn.psa_evidence_loaders import (
    EducationAccessEvidenceLoad,
    GovernedEducationAssessmentScope,
    PopulationReachEvidenceLoad,
)
from satn.scenario_compilation import (
    PreparedCriteriaLineage,
    _validate_education_source_extension,
)
from satn.strategic_corridors import (
    PreparedStrategicCorridorUnit,
    StrategicCorridorPreparationResult,
    StrategicCorridorUnitRole,
)

_CONTRACT = "satn-strategic-criteria-scenario/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _thaw(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _lineage_education_source(
    value: object,
) -> EducationAccessSourceSnapshot:
    if not isinstance(value, Mapping):
        raise ValueError(
            "strategic criterion education preparation lineage is malformed"
        )
    try:
        return EducationAccessSourceSnapshot.model_validate(_thaw(value))
    except Exception as error:
        raise ValueError(
            "strategic criterion education preparation lineage is malformed"
        ) from error


@dataclass(frozen=True)
class _BoundAreaDefinition:
    crs: str
    geometries_wkb: tuple[bytes, ...]

    @classmethod
    def from_frame(cls, frame: gpd.GeoDataFrame) -> _BoundAreaDefinition:
        if frame.empty or frame.crs is None:
            raise ValueError("strategic criteria area requires geometry and a CRS")
        return cls(
            crs=frame.crs.to_wkt(),
            geometries_wkb=tuple(bytes(item.wkb) for item in frame.geometry),
        )

    def frame(self) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            {"geometry": [from_wkb(item) for item in self.geometries_wkb]},
            geometry="geometry",
            crs=self.crs,
        )


@dataclass(frozen=True)
class _StrategicAlignmentUnitAdapter:
    """Private alignment-unit shape consumed by the legacy criterion builder."""

    unit_id: str
    prepared_unit: PreparedStrategicCorridorUnit

    @property
    def access_connection_id(self) -> str:
        """Legacy private key; never leaves this adapter boundary."""

        return self.unit_id

    @property
    def candidate_set(self):
        return self.prepared_unit.candidate_set

    def canonical(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "unit_role": self.prepared_unit.unit_role.value,
            "prepared_unit": self.prepared_unit.canonical(),
        }


@dataclass(frozen=True)
class PreparedStrategicUnitCriteria:
    """One role-typed criterion packet bound to one exact strategic unit."""

    unit_id: str
    unit_role: StrategicCorridorUnitRole
    criteria: CandidateCriteria | CandidateSetGapEvidence
    preparation_lineage: PreparedCriteriaLineage

    def __post_init__(self) -> None:
        if not self.unit_id or self.unit_id.strip() != self.unit_id:
            raise ValueError("strategic criterion unit_id must be canonical")
        if isinstance(self.criteria, CandidateCriteria):
            try:
                criteria = CandidateCriteria.model_validate(
                    self.criteria.model_dump(mode="python")
                )
            except Exception as error:
                raise ValueError(
                    "strategic criterion CandidateCriteria is stale"
                ) from error
            object.__setattr__(self, "criteria", criteria)
        else:
            criteria = CandidateSetGapEvidence.model_validate(
                self.criteria.model_dump(mode="python")
            )
            object.__setattr__(self, "criteria", criteria)
        candidate_set = (
            criteria.candidate_set
            if isinstance(criteria, CandidateSetGapEvidence)
            else criteria.education.candidate_set
        )
        expected_role = candidate_set.network_role
        if expected_role is not self.unit_role.network_role:
            raise ValueError("strategic criterion role is stale for its Candidate Set")
        if isinstance(criteria, CandidateCriteria):
            education_lineage = self.preparation_lineage.evidence_lineage.get(
                "education"
            )
            if not isinstance(education_lineage, Mapping):
                raise ValueError(
                    "strategic criterion education preparation lineage is malformed"
                )
            governed_source = education_lineage.get(
                "governed_source_fingerprint"
            )
            prepared_source = _lineage_education_source(
                education_lineage.get("source_snapshot")
            )
            governed_binding = criteria.education.governed_binding
            if (
                not isinstance(governed_source, str)
                or _SHA256.fullmatch(governed_source) is None
                or governed_binding.full_source_governed_fingerprint
                != governed_source
                or governed_binding.governed_input_fingerprint
                != governed_education_assessment_fingerprint(
                    governed_source_fingerprint=governed_source,
                    school_ids=governed_binding.school_ids,
                    strategic_destination_ids=(
                        governed_binding.strategic_destination_ids
                    ),
                    assessment_content_sha256=(
                        governed_binding.assessment_content_sha256
                    ),
                )
            ):
                raise ValueError(
                    "strategic criterion education source is foreign to "
                    "preparation lineage"
                )
            _validate_education_source_extension(
                prepared_source,
                criteria.education.assessment.source_snapshot,
                school_ids=governed_binding.school_ids,
                strategic_destination_ids=(
                    governed_binding.strategic_destination_ids
                ),
                require_full_scope=False,
            )


@dataclass(frozen=True)
class StrategicCriteriaScenarioInput:
    """Governed data-only inputs for criteria and Scenario compilation."""

    preparation: StrategicCorridorPreparationResult | None
    population_evidence: PopulationReachEvidenceLoad | None
    education_evidence: EducationAccessEvidenceLoad | None
    area_definition: gpd.GeoDataFrame | _BoundAreaDefinition | None
    area_fingerprint: str
    existing_alignment: Mapping[str, ExistingAlignmentCriterionSummary] | None = None
    population_profile: PopulationReachProfile | None = None
    decision_record: ScenarioDecisionRecord | None = None
    review_run_instance_id: str = "strategic-corridor-review"
    prior_orchestration: ScenarioReviewOrchestration | None = None

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.area_fingerprint) is None:
            raise ValueError("area_fingerprint must be lowercase SHA-256")
        if isinstance(self.area_definition, gpd.GeoDataFrame):
            object.__setattr__(
                self,
                "area_definition",
                _BoundAreaDefinition.from_frame(self.area_definition),
            )
        elif self.area_definition is not None and not isinstance(
            self.area_definition, _BoundAreaDefinition
        ):
            raise ValueError("area_definition must be a governed GeoDataFrame")
        existing = {
            unit_id: ExistingAlignmentCriterionSummary.model_validate(
                summary.model_dump(mode="python")
            )
            for unit_id, summary in sorted((self.existing_alignment or {}).items())
        }
        object.__setattr__(
            self,
            "existing_alignment",
            MappingProxyType(existing),
        )


@dataclass(frozen=True)
class StrategicCriteriaScenarioResult:
    """Inspect-only result which always requires separate human adoption."""

    status: Literal["disabled", "incomplete", "review-required", "compiled"]
    preparation_fingerprint: str | None
    criteria: tuple[PreparedStrategicUnitCriteria, ...]
    scenario: ScenarioCompilation | None
    review_orchestration: ScenarioReviewOrchestration | None
    missing_inputs: tuple[str, ...]
    diagnostics: Mapping[str, object]
    result_fingerprint: str

    def __post_init__(self) -> None:
        if self.status in {"compiled", "review-required"} and self.scenario is None:
            raise ValueError("completed strategic criteria result requires a Scenario")
        if self.status in {"disabled", "incomplete"} and self.scenario is not None:
            raise ValueError("non-ready strategic criteria result cannot contain a Scenario")
        expected = _result_fingerprint(
            status=self.status,
            preparation_fingerprint=self.preparation_fingerprint,
            criteria=self.criteria,
            scenario=self.scenario,
            review=self.review_orchestration,
            missing_inputs=self.missing_inputs,
            diagnostics=self.diagnostics,
        )
        if self.result_fingerprint != expected:
            raise ValueError("strategic criteria result fingerprint is stale")

    @property
    def human_adoption_required(self) -> bool:
        return True

    @property
    def reference_satn_created(self) -> bool:
        return False

    @property
    def can_mutate_authoritative_network(self) -> bool:
        return False


def compile_strategic_criteria_scenario(
    request: StrategicCriteriaScenarioInput,
) -> StrategicCriteriaScenarioResult:
    """Assemble separate criteria and select both strategic corridor roles."""

    preparation = request.preparation
    if preparation is None:
        return _result("disabled", None, (), None, None, (), {"reason": "disabled"})
    _validate_preparation(preparation)
    missing = set(preparation.missing_inputs)
    if preparation.status != "prepared":
        missing.add("strategic-corridor-preparation-not-ready")
    if request.population_evidence is None:
        missing.add("population-reach-evidence")
    if request.education_evidence is None:
        missing.add("education-access-evidence")
    if request.area_definition is None:
        missing.add("area-definition")
    if missing:
        return _result(
            "incomplete",
            preparation.preparation_fingerprint,
            (),
            None,
            None,
            tuple(sorted(missing)),
            {"reason": "required-governed-input-missing"},
        )

    assert request.population_evidence is not None
    assert request.education_evidence is not None
    assert isinstance(request.area_definition, _BoundAreaDefinition)
    _validate_evidence_identity(
        preparation,
        request.population_evidence,
        request.education_evidence,
    )
    known_unit_ids = {item.unit_id for item in preparation.units}
    foreign_existing = set(request.existing_alignment or ()) - known_unit_ids
    if foreign_existing:
        raise ValueError(
            "existing-alignment evidence names foreign strategic units: "
            + ", ".join(sorted(foreign_existing))
        )

    packets = tuple(
        _assemble_unit(
            preparation,
            item,
            population_evidence=request.population_evidence,
            education_evidence=request.education_evidence,
            area_definition=request.area_definition.frame(),
            existing_alignment=(request.existing_alignment or {}).get(item.unit_id),
            population_profile=request.population_profile,
        )
        for item in preparation.units
    )
    selections = tuple(
        sorted(
            (
                select_preferred_alignment(
                    _candidate_set(packet.criteria).profile,
                    _candidate_set(packet.criteria),
                    packet.criteria,
                )
                for packet in packets
            ),
            key=lambda item: item.candidate_set_id,
        )
    )
    decision = request.decision_record or ScenarioDecisionRecord(
        mode=(
            DecisionProcessMode.NO_AGENT
            if all(
                item.disposition is SelectionDisposition.SELECTED
                for item in selections
            )
            else (
                DecisionProcessMode.PROVISIONAL_REVIEW
                if any(
                    item.disposition is SelectionDisposition.NETWORK_GAP
                    for item in selections
                )
                else DecisionProcessMode.PROFILE_FALLBACK
            )
        )
    )
    scenario = _build_scenario(
        area_fingerprint=request.area_fingerprint,
        preparation=preparation,
        selections=selections,
        decision_record=decision,
    )
    review = (
        orchestrate_scenario_review(
            scenario,
            dependencies=tuple(
                ScenarioReviewDependency(candidate_set_id=item.candidate_set_id)
                for item in scenario.candidate_sets
            ),
            run_instance_id=request.review_run_instance_id,
            prior_orchestration=request.prior_orchestration,
        )
        if not scenario.publishable or request.prior_orchestration is not None
        else None
    )
    return _result(
        "review-required" if not scenario.publishable else "compiled",
        preparation.preparation_fingerprint,
        packets,
        scenario,
        review,
        (),
        {
            "unit_roles": tuple(item.unit_role.value for item in packets),
            "selection_performed": True,
            "agent_runtime_constructed": False,
            "human_adoption_required": True,
            "reference_satn_created": False,
            "authoritative_network_geometry_mutated": False,
            "publication_performed": False,
        },
    )


def _candidate_set(criteria: CandidateCriteria | CandidateSetGapEvidence):
    return (
        criteria.candidate_set
        if isinstance(criteria, CandidateSetGapEvidence)
        else criteria.education.candidate_set
    )


def _assemble_unit(
    preparation: StrategicCorridorPreparationResult,
    unit: PreparedStrategicCorridorUnit,
    *,
    population_evidence: PopulationReachEvidenceLoad,
    education_evidence: EducationAccessEvidenceLoad,
    area_definition: gpd.GeoDataFrame,
    existing_alignment: ExistingAlignmentCriterionSummary | None,
    population_profile: PopulationReachProfile | None,
) -> PreparedStrategicUnitCriteria:
    adapter = _StrategicAlignmentUnitAdapter(
        unit_id=unit.unit_id,
        prepared_unit=unit,
    )
    legacy_packet = _assemble_connection(
        preparation,  # type: ignore[arg-type]
        adapter,  # type: ignore[arg-type]
        population_evidence=population_evidence,
        education_evidence=education_evidence,
        area_definition=area_definition,
        option_evidence=_destination_option_evidence(unit),
        existing_alignment=existing_alignment,
        population_profile=population_profile,
        education_scope=_education_scope(unit),
    )
    return PreparedStrategicUnitCriteria(
        unit_id=unit.unit_id,
        unit_role=unit.unit_role,
        criteria=legacy_packet.criteria,
        preparation_lineage=legacy_packet.preparation_lineage,
    )


def _education_scope(
    unit: PreparedStrategicCorridorUnit,
) -> GovernedEducationAssessmentScope:
    candidate_set = unit.candidate_set
    if (
        candidate_set.mandatory_access_obligation_ids
        or candidate_set.network_role is not unit.unit_role.network_role
    ):
        raise ValueError(
            "strategic unit has unsupported or stale education obligations"
        )
    destination_ids = candidate_set.mandatory_strategic_destination_ids
    if unit.unit_role is StrategicCorridorUnitRole.INTERURBAN_SPINE:
        if destination_ids or unit.strategic_destination_id is not None:
            raise ValueError(
                "interurban unit cannot inherit a strategic destination scope"
            )
    elif (
        len(destination_ids) != 1
        or destination_ids != (unit.strategic_destination_id,)
    ):
        raise ValueError(
            "destination unit scope must match its exact admitted destination"
        )
    return GovernedEducationAssessmentScope(
        school_ids=(),
        strategic_destination_ids=destination_ids,
    )


def _destination_option_evidence(
    unit: PreparedStrategicCorridorUnit,
) -> tuple[StrategicEducationDestinationEvidence, ...]:
    if unit.unit_role is StrategicCorridorUnitRole.INTERURBAN_SPINE:
        return ()
    if unit.strategic_destination_id is None or not unit.access_point_evidence_ids:
        raise ValueError("destination unit lacks exact admitted access evidence")
    record_by_id = {
        item.candidate.candidate_id: item for item in unit.candidate_records
    }
    rows = []
    for candidate in unit.candidate_set.admitted_candidates:
        record = record_by_id.get(candidate.candidate_id)
        if (
            record is None
            or not record.routing_edge_ids
            or not record.reverse_routing_edge_ids
        ):
            raise ValueError("destination candidate lacks exact current graph evidence")
        rows.append(
            StrategicEducationDestinationEvidence(
                option_id=education_option_id_for_candidate(
                    candidate,
                    unit.candidate_set,
                ),
                strategic_destination_id=unit.strategic_destination_id,
                connector_distance=MeasuredDistance(distance_m=0.0),
                connector_continuity=ConnectorContinuity.CONTINUOUS,
                access_point_status=AccessPointStatus.MAPPED,
                destination_distance=MeasuredDistance(
                    distance_m=float(candidate.geometry.as_shapely().length)
                ),
                access_evidence_ids=unit.access_point_evidence_ids,
                support_evidence_ids=tuple(
                    sorted(
                        {
                            *record.evidence_ids,
                            *record.source_ids,
                            *record.routing_edge_ids,
                            *record.reverse_routing_edge_ids,
                        }
                    )
                ),
            )
        )
    return tuple(rows)


def _build_scenario(
    *,
    area_fingerprint: str,
    preparation: StrategicCorridorPreparationResult,
    selections: tuple[PreferredStrategicAlignment, ...],
    decision_record: ScenarioDecisionRecord,
) -> ScenarioCompilation:
    assessments = {
        (binding.kind, binding.assessment_id): binding
        for selection in selections
        for binding in selection.criteria.evidence_snapshot.assessments
    }
    snapshot = GovernedEvidenceSnapshot(
        snapshot_id="strategic-scenario-" + preparation.preparation_fingerprint[:20],
        assessments=tuple(assessments.values()),
    )
    return ScenarioCompilation(
        area_fingerprint=area_fingerprint,
        evidence_snapshot=snapshot,
        profile_fingerprint=preparation.profile_fingerprint,
        decision_record=decision_record,
        candidate_sets=tuple(item.candidate_set for item in selections),
        selections=selections,
        criteria_bindings=tuple(
            ScenarioCriteriaBinding(
                candidate_set_id=item.candidate_set_id,
                criteria_fingerprint=item.criteria_fingerprint,
            )
            for item in selections
        ),
        required_network_role_ids=tuple(
            sorted(
                {item.candidate_set.network_role for item in selections},
                key=str,
            )
        ),
        mandatory_network_place_ids=tuple(
            sorted(
                {
                    identifier
                    for item in selections
                    for identifier in item.candidate_set.mandatory_network_place_ids
                }
            )
        ),
        mandatory_access_obligation_ids=tuple(
            sorted(
                {
                    identifier
                    for item in selections
                    for identifier in item.candidate_set.mandatory_access_obligation_ids
                }
            )
        ),
        mandatory_strategic_destination_ids=tuple(
            sorted(
                {
                    identifier
                    for item in selections
                    for identifier in item.candidate_set.mandatory_strategic_destination_ids
                }
            )
        ),
        lineage_fingerprints=tuple(
            sorted(
                {
                    preparation.preparation_fingerprint,
                    *(item.criteria_fingerprint for item in selections),
                }
            )
        ),
    )


def _validate_preparation(preparation: StrategicCorridorPreparationResult) -> None:
    if preparation.contract != "satn-strategic-corridor-preparation/v1":
        raise ValueError("unsupported strategic corridor preparation contract")
    if preparation.preparation_fingerprint != _fingerprint(
        preparation.canonical_payload()
    ):
        raise ValueError("strategic corridor preparation fingerprint is stale")
    unit_ids = tuple(item.unit_id for item in preparation.units)
    if len(set(unit_ids)) != len(unit_ids):
        raise ValueError("strategic corridor preparation has duplicate units")
    roles = {item.unit_role for item in preparation.units}
    allowed = {
        StrategicCorridorUnitRole.INTERURBAN_SPINE,
        StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS,
    }
    if preparation.status == "prepared" and (
        StrategicCorridorUnitRole.INTERURBAN_SPINE not in roles
        or not roles.issubset(allowed)
    ):
        raise ValueError(
            "prepared strategic corridor requires an interurban unit and only "
            "explicitly admitted supported role-specific units"
        )


def _validate_evidence_identity(
    preparation: StrategicCorridorPreparationResult,
    population: PopulationReachEvidenceLoad,
    education: EducationAccessEvidenceLoad,
) -> None:
    lineage = preparation.evidence_lineage
    population_lineage = lineage.get("population")
    education_lineage = lineage.get("education")
    if not isinstance(population_lineage, Mapping) or not isinstance(
        education_lineage, Mapping
    ):
        raise ValueError("strategic preparation evidence lineage is malformed")
    if (
        population_lineage.get("source_content_sha256")
        != population.source.content_sha256
        or population_lineage.get("frame_content_sha256")
        != population.frame_content_sha256
    ):
        raise ValueError("population evidence is foreign to strategic preparation")
    if (
        education_lineage.get("governed_source_fingerprint")
        != education.governed_source_fingerprint
        or _lineage_education_source(
            education_lineage.get("source_snapshot")
        )
        != education.source_snapshot
        or education_lineage.get("school_register_content_sha256")
        != education.school_register_lineage.content_sha256
        or education_lineage.get("admissions_content_sha256")
        != (
            education.admissions_lineage.content_sha256
            if education.admissions_lineage is not None
            else None
        )
    ):
        raise ValueError("education evidence is foreign to strategic preparation")


def _result(
    status: Literal["disabled", "incomplete", "review-required", "compiled"],
    preparation_fingerprint: str | None,
    criteria: tuple[PreparedStrategicUnitCriteria, ...],
    scenario: ScenarioCompilation | None,
    review: ScenarioReviewOrchestration | None,
    missing_inputs: tuple[str, ...],
    diagnostics: Mapping[str, object],
) -> StrategicCriteriaScenarioResult:
    ordered = tuple(sorted(criteria, key=lambda item: item.unit_id))
    frozen_diagnostics = MappingProxyType(dict(sorted(diagnostics.items())))
    fingerprint = _result_fingerprint(
        status=status,
        preparation_fingerprint=preparation_fingerprint,
        criteria=ordered,
        scenario=scenario,
        review=review,
        missing_inputs=missing_inputs,
        diagnostics=frozen_diagnostics,
    )
    return StrategicCriteriaScenarioResult(
        status=status,
        preparation_fingerprint=preparation_fingerprint,
        criteria=ordered,
        scenario=scenario,
        review_orchestration=review,
        missing_inputs=tuple(sorted(set(missing_inputs))),
        diagnostics=frozen_diagnostics,
        result_fingerprint=fingerprint,
    )


def _result_fingerprint(
    *,
    status: str,
    preparation_fingerprint: str | None,
    criteria: tuple[PreparedStrategicUnitCriteria, ...],
    scenario: ScenarioCompilation | None,
    review: ScenarioReviewOrchestration | None,
    missing_inputs: tuple[str, ...],
    diagnostics: Mapping[str, object],
) -> str:
    payload = {
        "contract": _CONTRACT,
        "status": status,
        "preparation_fingerprint": preparation_fingerprint,
        "criteria": [
            {
                "unit_id": item.unit_id,
                "unit_role": item.unit_role.value,
                "criteria_fingerprint": item.criteria.criteria_fingerprint,
            }
            for item in criteria
        ],
        "scenario_fingerprint": (
            scenario.scenario_fingerprint if scenario is not None else None
        ),
        "review_fingerprint": (
            review.orchestration_fingerprint if review is not None else None
        ),
        "missing_inputs": list(sorted(set(missing_inputs))),
        "diagnostics": dict(diagnostics),
        "human_adoption_required": True,
        "reference_satn_created": False,
    }
    return _fingerprint(payload)
