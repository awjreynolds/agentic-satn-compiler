"""Compile governed chained Community Connections into a scenario artifact.

Only a compiled Spine Access row that extends one Community from another
already-served Community is eligible for promotion. Direct attachments to a
Strategic Spine remain Spine Access and are explicitly out of scope.

This bridge accepts exact, already-governed criterion records. It never invents
population, education, route-quality, feasibility, safety, cost, topography or
existing-asset evidence. The output is an inspectable Scenario Compilation,
not a Reference SATN, and cannot alter authoritative route geometry or publish.

SHA-256 is used only for deterministic content identity, staleness and lineage.
It is not a signature, credential, certificate or trust root.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from satn.alignment_selection import (
    AssessmentKind,
    CandidateCriteria,
    CandidateSetGapEvidence,
    DecisionProcessMode,
    GovernedEvidenceSnapshot,
    NetworkRole,
    PreferredStrategicAlignment,
    ScenarioCompilation,
    ScenarioCriteriaBinding,
    ScenarioDecisionRecord,
    ScenarioReviewDependency,
    ScenarioReviewOrchestration,
    SelectionDisposition,
    orchestrate_scenario_review,
    select_preferred_alignment,
    traffic_diagnostics_for_candidate,
)
from satn.education_access import EducationAccessSourceSnapshot
from satn.spine_access_candidate_preparation import (
    PreparedSpineAccessConnection,
    SpineAccessCandidatePreparationResult,
)

_CONTRACT = "satn-prepared-scenario-compilation/v3"
_NETWORK_GEOMETRY_BINDING = "satn-prepared-network-geometry-source/v1"
_TOPOGRAPHY_BINDING = "satn-prepared-topography-source/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OUT_OF_SCOPE_REASON = "out-of-scope-direct-strategic-spine-attachment"


def _fingerprint(value: object) -> str:
    """Return deterministic content identity, never an authentication claim."""

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
class PreparedCriteriaLineage:
    """Immutable raw-input identity captured when criterion evidence was built."""

    preparation_fingerprint: str
    evidence_fingerprints: tuple[str, ...]
    evidence_lineage: Mapping[str, object]
    evidence_lineage_fingerprint: str

    @classmethod
    def from_preparation(
        cls,
        preparation: SpineAccessCandidatePreparationResult,
    ) -> PreparedCriteriaLineage:
        lineage = _freeze(preparation.evidence_lineage)
        assert isinstance(lineage, Mapping)
        return cls(
            preparation_fingerprint=preparation.preparation_fingerprint,
            evidence_fingerprints=preparation.evidence_fingerprints,
            evidence_lineage=lineage,
            evidence_lineage_fingerprint=_fingerprint(_thaw(lineage)),
        )

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.preparation_fingerprint) is None:
            raise ValueError("criteria preparation fingerprint must be lowercase SHA-256")
        fingerprints = tuple(sorted(self.evidence_fingerprints))
        if (
            not fingerprints
            or len(set(fingerprints)) != len(fingerprints)
            or any(_SHA256.fullmatch(item) is None for item in fingerprints)
        ):
            raise ValueError(
                "criteria preparation evidence fingerprints must be unique SHA-256"
            )
        lineage = _freeze(self.evidence_lineage)
        if not isinstance(lineage, Mapping) or not lineage:
            raise ValueError("criteria preparation evidence lineage must be nonempty")
        expected = _fingerprint(_thaw(lineage))
        if self.evidence_lineage_fingerprint != expected:
            raise ValueError("criteria preparation evidence lineage fingerprint is stale")
        object.__setattr__(self, "evidence_fingerprints", fingerprints)
        object.__setattr__(self, "evidence_lineage", lineage)

    def canonical(self) -> dict[str, object]:
        return {
            "preparation_fingerprint": self.preparation_fingerprint,
            "evidence_fingerprints": list(self.evidence_fingerprints),
            "evidence_lineage": _thaw(self.evidence_lineage),
            "evidence_lineage_fingerprint": self.evidence_lineage_fingerprint,
        }


@dataclass(frozen=True)
class PreparedCandidateCriteria:
    """Exact criterion/gap plus raw preparation identity captured at derivation."""

    access_connection_id: str
    criteria: CandidateCriteria | CandidateSetGapEvidence
    preparation_lineage: PreparedCriteriaLineage

    def __post_init__(self) -> None:
        if (
            not self.access_connection_id
            or self.access_connection_id.strip() != self.access_connection_id
        ):
            raise ValueError("access_connection_id must be a canonical identifier")
        if isinstance(self.criteria, CandidateSetGapEvidence):
            validated: CandidateCriteria | CandidateSetGapEvidence = (
                CandidateSetGapEvidence.model_validate(
                    self.criteria.model_dump(mode="python")
                )
            )
        elif isinstance(self.criteria, CandidateCriteria):
            validated = CandidateCriteria.model_validate(
                self.criteria.model_dump(mode="python")
            )
        else:
            raise ValueError(
                "criteria must be an exact CandidateCriteria or CandidateSetGapEvidence"
            )
        object.__setattr__(self, "criteria", validated)
        if not isinstance(self.preparation_lineage, PreparedCriteriaLineage):
            raise ValueError(
                "criteria packet requires exact frozen preparation lineage"
            )
        lineage = PreparedCriteriaLineage(**self.preparation_lineage.canonical())
        object.__setattr__(self, "preparation_lineage", lineage)


@dataclass(frozen=True)
class PreparedScenarioCompilationInput:
    """Data-only scenario/replay request without runtime or publication power."""

    area_fingerprint: str
    criteria: tuple[PreparedCandidateCriteria, ...] = ()
    decision_record: ScenarioDecisionRecord | None = None
    review_run_instance_id: str = "prepared-scenario-review"
    prior_orchestration: ScenarioReviewOrchestration | None = None

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.area_fingerprint) is None:
            raise ValueError("area_fingerprint must be lowercase SHA-256")
        if (
            not self.review_run_instance_id
            or self.review_run_instance_id.strip() != self.review_run_instance_id
        ):
            raise ValueError("review_run_instance_id must be canonical")
        criteria = tuple(
            sorted(
                (
                    PreparedCandidateCriteria(
                        access_connection_id=item.access_connection_id,
                        criteria=item.criteria,
                        preparation_lineage=item.preparation_lineage,
                    )
                    for item in self.criteria
                ),
                key=lambda item: item.access_connection_id,
            )
        )
        if len({item.access_connection_id for item in criteria}) != len(criteria):
            raise ValueError("at most one criteria record is allowed per connection")
        object.__setattr__(self, "criteria", criteria)
        if self.decision_record is not None:
            object.__setattr__(
                self,
                "decision_record",
                ScenarioDecisionRecord.model_validate(
                    self.decision_record.model_dump(mode="python")
                ),
            )
        if self.prior_orchestration is not None:
            object.__setattr__(
                self,
                "prior_orchestration",
                ScenarioReviewOrchestration.model_validate(
                    self.prior_orchestration.model_dump(mode="python")
                ),
            )


@dataclass(frozen=True)
class PreparedScenarioCompilationResult:
    """Deeply immutable inspectable result with explicit incomplete states."""

    contract: str
    status: Literal["disabled", "incomplete", "compiled", "review-required"]
    preparation_fingerprint: str | None
    scenario: ScenarioCompilation | None
    review_orchestration: ScenarioReviewOrchestration | None
    missing_inputs: tuple[str, ...]
    diagnostics: Mapping[str, object]
    result_fingerprint: str

    def __post_init__(self) -> None:
        frozen = _freeze(self.diagnostics)
        assert isinstance(frozen, Mapping)
        object.__setattr__(self, "diagnostics", frozen)
        expected = _fingerprint(self._fingerprint_payload())
        if self.result_fingerprint != expected:
            raise ValueError("prepared scenario result fingerprint is stale")

    @property
    def reference_satn_created(self) -> bool:
        return False

    @property
    def can_mutate_authoritative_network(self) -> bool:
        return False

    def _fingerprint_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "status": self.status,
            "preparation_fingerprint": self.preparation_fingerprint,
            "scenario_fingerprint": (
                self.scenario.scenario_fingerprint if self.scenario is not None else None
            ),
            "review_orchestration_fingerprint": (
                self.review_orchestration.orchestration_fingerprint
                if self.review_orchestration is not None
                else None
            ),
            "missing_inputs": list(self.missing_inputs),
            "diagnostics": _thaw(self.diagnostics),
            "reference_satn_created": False,
            "authoritative_network_geometry_mutated": False,
        }

    def metadata(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "status": self.status,
            "preparation_fingerprint": self.preparation_fingerprint,
            "missing_inputs": list(self.missing_inputs),
            "scenario": (
                self.scenario.model_dump(mode="json") if self.scenario is not None else None
            ),
            "review_orchestration": (
                self.review_orchestration.model_dump(mode="json")
                if self.review_orchestration is not None
                else None
            ),
            "reference_satn_created": False,
            "authoritative_network_geometry_mutated": False,
            "diagnostics": _thaw(self.diagnostics),
            "result_fingerprint": self.result_fingerprint,
        }


def prepared_network_geometry_source_fingerprint(
    prepared: PreparedSpineAccessConnection,
) -> str:
    """Bind network criteria to the exact retained connection/candidate record."""

    return _fingerprint(
        {
            "contract": _NETWORK_GEOMETRY_BINDING,
            "prepared_connection": prepared.canonical(),
        }
    )


def prepared_topography_source_fingerprint(
    prepared: PreparedSpineAccessConnection,
) -> str:
    """Bind topography criteria to exact candidate geometry and declared gradient."""

    return _fingerprint(
        {
            "contract": _TOPOGRAPHY_BINDING,
            "candidate_set_fingerprint": prepared.candidate_set.candidate_set_fingerprint,
            "candidates": [
                {
                    "candidate_id": item.candidate_id,
                    "geometry_fingerprint": item.geometry.fingerprint,
                    "maximum_gradient_pct": item.maximum_gradient_pct,
                }
                for item in prepared.candidate_set.candidates
            ],
        }
    )


def compile_prepared_scenario(
    preparation: SpineAccessCandidatePreparationResult | None,
    request: PreparedScenarioCompilationInput,
) -> PreparedScenarioCompilationResult:
    """Compile only exhaustive, evidence-bound chained Community Connections."""

    request = PreparedScenarioCompilationInput(
        area_fingerprint=request.area_fingerprint,
        criteria=request.criteria,
        decision_record=request.decision_record,
        review_run_instance_id=request.review_run_instance_id,
        prior_orchestration=request.prior_orchestration,
    )
    if preparation is None:
        return _result(
            status="disabled",
            preparation=None,
            scenario=None,
            review=None,
            missing=(),
            diagnostics={
                "selection_performed": False,
                "agent_runtime_constructed": False,
                "reason": "network-selection-profile-disabled",
            },
        )

    _validate_preparation_identity(preparation)
    for packet in request.criteria:
        _validate_packet_preparation_lineage(preparation, packet)
        _validate_criterion_snapshot(packet.criteria)
    prepared = tuple(
        sorted(
            preparation.prepared_spine_access_connections,
            key=lambda item: item.access_connection_id,
        )
    )
    unresolved_roster_ids = tuple(
        item.access_connection_id
        for item in preparation.connection_roster
        if item.disposition == "unresolved-gap"
    )
    if preparation.status != "prepared":
        missing = {
            *preparation.missing_inputs,
            *(f"unresolved-preparation:{item}" for item in unresolved_roster_ids),
        }
        if preparation.status != "prepared":
            missing.add("candidate-preparation-incomplete")
        return _result(
            status="incomplete",
            preparation=preparation,
            scenario=None,
            review=None,
            missing=tuple(sorted(missing)),
            diagnostics=_roster_diagnostics(
                preparation,
                reason="prepared-candidate-evidence-or-roster-incomplete",
            ),
        )
    if not prepared:
        missing = (
            tuple(
                f"unresolved-preparation:{item}"
                for item in unresolved_roster_ids
            )
            if unresolved_roster_ids
            else ("eligible-chained-community-connection",)
        )
        return _result(
            status="incomplete",
            preparation=preparation,
            scenario=None,
            review=None,
            missing=missing,
            diagnostics=_roster_diagnostics(
                preparation,
                reason="no-eligible-chained-community-connections",
            ),
        )

    prepared_by_id = {item.access_connection_id: item for item in prepared}
    criteria_by_id = {item.access_connection_id: item.criteria for item in request.criteria}
    foreign = set(criteria_by_id) - set(prepared_by_id)
    if foreign:
        raise ValueError(
            "criteria records name unprepared Community Connections: "
            + ", ".join(sorted(foreign))
        )
    missing = tuple(
        f"candidate-criteria:{item.access_connection_id}"
        for item in prepared
        if item.access_connection_id not in criteria_by_id
    )
    if missing:
        return _result(
            status="incomplete",
            preparation=preparation,
            scenario=None,
            review=None,
            missing=missing,
            diagnostics=_roster_diagnostics(
                preparation,
                reason="governed-criterion-input-missing",
            ),
        )

    _validate_promoted_candidate_sets(preparation)
    (
        population_source,
        education_source,
        education_governed_source,
    ) = _criterion_source_lineage(preparation)
    selections: list[PreferredStrategicAlignment] = []
    traffic_diagnostics: list[dict[str, object]] = []
    for item in prepared:
        criterion = criteria_by_id[item.access_connection_id]
        _validate_exact_candidate_set(item, criterion)
        _validate_criterion_sources(
            item,
            criterion,
            population_source=population_source,
            education_source=education_source,
            education_governed_source=education_governed_source,
        )
        selections.append(
            select_preferred_alignment(
                item.candidate_set.profile,
                item.candidate_set,
                criterion,
            )
        )
        for candidate in item.candidate_set.candidates:
            traffic_diagnostics.extend(
                traffic_diagnostics_for_candidate(candidate, item.candidate_set.profile)
            )

    selections_tuple = tuple(sorted(selections, key=lambda item: item.candidate_set_id))
    decision_record = request.decision_record or ScenarioDecisionRecord(
        mode=(
            DecisionProcessMode.NO_AGENT
            if all(item.disposition == SelectionDisposition.SELECTED for item in selections_tuple)
            else (
                DecisionProcessMode.PROVISIONAL_REVIEW
                if any(
                    item.disposition == SelectionDisposition.NETWORK_GAP
                    for item in selections_tuple
                )
                else DecisionProcessMode.PROFILE_FALLBACK
            )
        )
    )
    scenario = _build_scenario(
        request.area_fingerprint,
        preparation,
        selections_tuple,
        decision_record,
    )
    # An unresolved sibling remains an explicit downstream Network Gap.  It
    # must not hide valid prepared candidate sets, but it does keep the
    # scenario in review-required status until that endpoint lineage is fixed.
    needs_review = not scenario.publishable or bool(unresolved_roster_ids)
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
        if needs_review or request.prior_orchestration is not None
        else None
    )
    traffic_payload = (
        {
            "traffic_diagnostics": tuple(traffic_diagnostics),
            "traffic_profile_fingerprints": tuple(
                sorted(
                    {
                        item.candidate_set.profile.traffic_profile.fingerprint
                        for item in selections_tuple
                        if item.candidate_set.profile.traffic_profile is not None
                    }
                )
            ),
        }
        if traffic_diagnostics
        else {}
    )
    return _result(
        status="review-required" if needs_review else "compiled",
        preparation=preparation,
        scenario=scenario,
        review=review,
        missing=tuple(
            sorted(
                {
                    *preparation.missing_inputs,
                    *(f"unresolved-preparation:{item}" for item in unresolved_roster_ids),
                }
            )
        ),
        diagnostics={
            **_roster_diagnostics(preparation, reason=None),
            "selection_performed": True,
            "agent_runtime_constructed": False,
            "decision_mode": scenario.decision_record.mode.value,
            "selection_count": len(scenario.selections),
            "review_request_count": (
                len(review.actionable_requests) if review is not None else 0
            ),
            "review_round": review.round_number if review is not None else 0,
            "review_converged": review.converged if review is not None else True,
            "reference_satn_created": False,
            "authoritative_network_geometry_mutated": False,
            "replay_directive": "recompile-whole-network-on-ledger-change",
            **traffic_payload,
        },
    )


def _validate_packet_preparation_lineage(
    preparation: SpineAccessCandidatePreparationResult,
    packet: PreparedCandidateCriteria,
) -> None:
    expected = PreparedCriteriaLineage.from_preparation(preparation)
    if packet.preparation_lineage.canonical() != expected.canonical():
        raise ValueError(
            "criteria packet is stale for the exact preparation and raw evidence lineage"
        )


def _validate_preparation_identity(
    preparation: SpineAccessCandidatePreparationResult,
) -> None:
    if preparation.contract != "satn-spine-access-candidate-preparation/v1":
        raise ValueError("unsupported candidate preparation contract")
    expected_fingerprint = _fingerprint(preparation.canonical_payload())
    if preparation.preparation_fingerprint != expected_fingerprint:
        raise ValueError("candidate preparation fingerprint is stale")

    roster = tuple(
        sorted(preparation.connection_roster, key=lambda item: item.access_connection_id)
    )
    roster_ids = tuple(item.access_connection_id for item in roster)
    if len(set(roster_ids)) != len(roster_ids):
        raise ValueError("prepared status requires one exhaustive unique connection roster")
    if not roster and (
        preparation.prepared_spine_access_connections
        or preparation.generation_issues
        or preparation.diagnostics.get("expected_connection_roster_count") != 0
    ):
        raise ValueError(
            "prepared status requires one exhaustive unique connection roster; "
            "empty roster contradicts its governed inputs"
        )
    prepared_ids = {
        item.access_connection_id for item in preparation.prepared_spine_access_connections
    }
    roster_prepared_ids = {
        item.access_connection_id
        for item in roster
        if item.disposition.startswith("prepared-")
    }
    if prepared_ids != roster_prepared_ids:
        raise ValueError("prepared connection sets do not exactly match the roster")
    if any(
        item.access_connection_id not in set(roster_ids)
        for item in preparation.generation_issues
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
            item.disposition == "out-of-scope-direct-strategic-spine" for item in roster
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
        _criterion_source_lineage(preparation)


def _validate_criterion_snapshot(
    criterion: CandidateCriteria | CandidateSetGapEvidence,
) -> None:
    expected = {
        AssessmentKind.POPULATION_REACH: 1,
        AssessmentKind.EDUCATION_ACCESS: 1,
        AssessmentKind.NETWORK_GEOMETRY: 1,
        AssessmentKind.TOPOGRAPHY: 1,
    }
    if isinstance(criterion, CandidateCriteria) and criterion.existing_alignment is not None:
        expected[AssessmentKind.EXISTING_ALIGNMENT] = 1
    actual = {
        kind: sum(
            binding.kind == kind
            for binding in criterion.evidence_snapshot.assessments
        )
        for kind in AssessmentKind
    }
    expected_with_zeros = {
        kind: expected.get(kind, 0)
        for kind in AssessmentKind
    }
    if actual != expected_with_zeros:
        raise ValueError(
            "criterion evidence snapshot must contain exactly one binding for "
            "each required assessment kind and no foreign bindings"
        )


def _validate_promoted_candidate_sets(
    preparation: SpineAccessCandidatePreparationResult,
) -> None:
    profiles = {
        item.candidate_set.profile_fingerprint
        for item in preparation.prepared_spine_access_connections
    }
    if profiles != {preparation.profile_fingerprint}:
        raise ValueError("prepared candidate sets do not share the preparation profile")
    roster_by_id = {
        item.access_connection_id: item for item in preparation.connection_roster
    }
    for item in preparation.prepared_spine_access_connections:
        candidate_set = item.candidate_set
        roster = roster_by_id[item.access_connection_id]
        if (
            candidate_set.network_role != NetworkRole.COMMUNITY_ACCESS
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
        ):
            raise ValueError(
                "only provenance-proven chained Community Connections may be promoted"
            )
        expected_endpoints = tuple(
            sorted((roster.community_id, roster.parent_place_id))
        )
        if candidate_set.endpoints != expected_endpoints:
            raise ValueError("candidate endpoints do not match exact Community provenance")
        for candidate in candidate_set.candidates:
            if (
                candidate.endpoints != expected_endpoints
                or candidate.served_network_place_ids != expected_endpoints
            ):
                raise ValueError(
                    "candidate served Network Places do not match exact Community provenance"
                )


def _validate_exact_candidate_set(
    prepared: PreparedSpineAccessConnection,
    criterion: CandidateCriteria | CandidateSetGapEvidence,
) -> None:
    if isinstance(criterion, CandidateSetGapEvidence):
        if criterion.candidate_set != prepared.candidate_set:
            raise ValueError("gap evidence is stale for prepared candidate set")
        return
    if criterion.education.candidate_set != prepared.candidate_set:
        raise ValueError("criteria education section is stale for prepared candidate set")


def _validate_criterion_sources(
    prepared: PreparedSpineAccessConnection,
    criterion: CandidateCriteria | CandidateSetGapEvidence,
    *,
    population_source: str,
    education_source: EducationAccessSourceSnapshot,
    education_governed_source: str,
) -> None:
    snapshot = criterion.evidence_snapshot
    expected = {
        AssessmentKind.POPULATION_REACH: population_source,
        AssessmentKind.NETWORK_GEOMETRY: (
            prepared_network_geometry_source_fingerprint(prepared)
        ),
        AssessmentKind.TOPOGRAPHY: prepared_topography_source_fingerprint(prepared),
    }
    for kind, source_content_sha256 in expected.items():
        binding = snapshot.assessment(kind)
        if binding is None or binding.source_content_sha256 != source_content_sha256:
            raise ValueError(f"{kind.value} criterion source is foreign or stale")
    education_binding = snapshot.assessment(AssessmentKind.EDUCATION_ACCESS)
    if education_binding is None:
        raise ValueError("education-access criterion source is foreign or stale")
    if isinstance(criterion, CandidateSetGapEvidence):
        if (
            education_binding.source_content_sha256
            != education_source.source_content_fingerprint
        ):
            raise ValueError("education-access gap source is foreign or stale")
        return
    criterion_source = criterion.education.assessment.source_snapshot
    if (
        education_binding.source_content_sha256
        != criterion.education.governed_binding
        .full_source_governed_fingerprint
        or education_binding.source_content_sha256
        != education_governed_source
    ):
        raise ValueError("education-access criterion source is foreign or stale")
    governed_scope = criterion.education.governed_binding
    _validate_education_source_extension(
        education_source,
        criterion_source,
        school_ids=governed_scope.school_ids,
        strategic_destination_ids=(
            governed_scope.strategic_destination_ids
        ),
        require_full_scope=True,
    )


def _criterion_source_lineage(
    preparation: SpineAccessCandidatePreparationResult,
) -> tuple[str, EducationAccessSourceSnapshot, str]:
    population = preparation.evidence_lineage.get("population")
    education = preparation.evidence_lineage.get("education")
    if not isinstance(population, Mapping) or not isinstance(education, Mapping):
        raise ValueError("prepared evidence lineage is malformed")
    population_source = population.get("source_content_sha256")
    education_governed_source = education.get(
        "governed_source_fingerprint"
    )
    source_snapshot = education.get("source_snapshot")
    if (
        not isinstance(population_source, str)
        or _SHA256.fullmatch(population_source) is None
        or not isinstance(education_governed_source, str)
        or _SHA256.fullmatch(education_governed_source) is None
    ):
        raise ValueError("prepared population/education source lineage is malformed")
    if not isinstance(source_snapshot, Mapping):
        raise ValueError("prepared population/education source lineage is malformed")
    try:
        education_source = EducationAccessSourceSnapshot.model_validate(
            dict(source_snapshot)
        )
    except Exception as error:
        raise ValueError(
            "prepared population/education source lineage is malformed"
        ) from error
    if education_source.option_ids or education_source.option_evidence:
        raise ValueError(
            "prepared education lineage must retain the pre-candidate source snapshot"
        )
    return (
        population_source,
        education_source,
        education_governed_source,
    )


def _validate_education_source_extension(
    prepared_source: EducationAccessSourceSnapshot,
    criterion_source: EducationAccessSourceSnapshot,
    *,
    school_ids: tuple[str, ...],
    strategic_destination_ids: tuple[str, ...],
    require_full_scope: bool,
) -> None:
    """Prove the option-specific assessment extends the exact prepared source.

    Education source-content hashes necessarily change when candidate-specific
    option observations are added. The immutable common records are therefore
    compared exactly, while each snapshot's own model validation proves its
    content hash covers the complete deterministic extension.
    """

    criterion_school_ids = tuple(
        item.school_id for item in criterion_source.schools
    )
    criterion_destination_ids = tuple(
        item.strategic_destination_id
        for item in criterion_source.strategic_education_destinations
    )
    prepared_schools = {
        item.school_id: item for item in prepared_source.schools
    }
    prepared_destinations = {
        item.strategic_destination_id: item
        for item in prepared_source.strategic_education_destinations
    }
    expected_full_scope = (
        tuple(prepared_schools),
        tuple(prepared_destinations),
    )
    exact_records = (
        all(
            prepared_schools.get(item.school_id) == item
            for item in criterion_source.schools
        )
        and all(
            prepared_destinations.get(item.strategic_destination_id) == item
            for item in criterion_source.strategic_education_destinations
        )
    )
    if (
        criterion_source.register_evidence
        != prepared_source.register_evidence
        or criterion_source.supplementary_pct_evidence
        != prepared_source.supplementary_pct_evidence
        or criterion_school_ids != school_ids
        or criterion_destination_ids != strategic_destination_ids
        or not exact_records
        or (
            require_full_scope
            and (school_ids, strategic_destination_ids)
            != expected_full_scope
        )
    ):
        raise ValueError(
            "education-access criterion does not extend the prepared source lineage"
        )


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


def _build_scenario(
    area_fingerprint: str,
    preparation: SpineAccessCandidatePreparationResult,
    selections: tuple[PreferredStrategicAlignment, ...],
    decision_record: ScenarioDecisionRecord,
) -> ScenarioCompilation:
    assessment_by_key = {
        (binding.kind, binding.assessment_id): binding
        for selection in selections
        for binding in selection.criteria.evidence_snapshot.assessments
    }
    snapshot = GovernedEvidenceSnapshot(
        snapshot_id=("prepared-scenario-" + preparation.preparation_fingerprint[:20]),
        assessments=tuple(assessment_by_key.values()),
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
        required_network_role_ids=(NetworkRole.COMMUNITY_ACCESS,),
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


def _roster_diagnostics(
    preparation: SpineAccessCandidatePreparationResult,
    *,
    reason: str | None,
) -> dict[str, object]:
    return {
        "selection_performed": False,
        "agent_runtime_constructed": False,
        "reason": reason,
        "expected_connection_roster_count": len(preparation.connection_roster),
        "prepared_connection_count": len(
            preparation.prepared_spine_access_connections
        ),
        "out_of_scope_connections": [
            item.canonical()
            for item in preparation.connection_roster
            if item.disposition == "out-of-scope-direct-strategic-spine"
        ],
        "unresolved_connections": [
            item.canonical()
            for item in preparation.connection_roster
            if item.disposition == "unresolved-gap"
        ],
        "generation_issues": [
            item.canonical() for item in preparation.generation_issues
        ],
    }


def _result(
    *,
    status: Literal["disabled", "incomplete", "compiled", "review-required"],
    preparation: SpineAccessCandidatePreparationResult | None,
    scenario: ScenarioCompilation | None,
    review: ScenarioReviewOrchestration | None,
    missing: tuple[str, ...],
    diagnostics: Mapping[str, object],
) -> PreparedScenarioCompilationResult:
    preparation_fingerprint = (
        preparation.preparation_fingerprint if preparation is not None else None
    )
    frozen = _freeze(diagnostics)
    assert isinstance(frozen, Mapping)
    payload = {
        "contract": _CONTRACT,
        "status": status,
        "preparation_fingerprint": preparation_fingerprint,
        "scenario_fingerprint": (
            scenario.scenario_fingerprint if scenario is not None else None
        ),
        "review_orchestration_fingerprint": (
            review.orchestration_fingerprint if review is not None else None
        ),
        "missing_inputs": list(missing),
        "diagnostics": _thaw(frozen),
        "reference_satn_created": False,
        "authoritative_network_geometry_mutated": False,
    }
    return PreparedScenarioCompilationResult(
        contract=_CONTRACT,
        status=status,
        preparation_fingerprint=preparation_fingerprint,
        scenario=scenario,
        review_orchestration=review,
        missing_inputs=missing,
        diagnostics=frozen,
        result_fingerprint=_fingerprint(payload),
    )
