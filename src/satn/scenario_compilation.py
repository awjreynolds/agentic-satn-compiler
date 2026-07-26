"""Compile an inspectable Preferred Strategic Alignment scenario artifact.

This is a deliberately narrow bridge between prepared Spine Access candidate
sets and the alignment-selection domain.  It accepts only already-governed,
exact ``CandidateCriteria`` records.  It never invents population, education,
route-quality, feasibility, safety, cost, or existing-asset evidence.

The result is a Scenario Compilation for review and deterministic replay.  It
is *not* a Reference SATN, does not change any authoritative route geometry,
and grants no publication authority.  A later, explicitly governed adoption
and publication step remains responsible for those actions.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from satn.alignment_selection import (
    CandidateCriteria,
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
)
from satn.spine_access_candidate_preparation import (
    SpineAccessCandidatePreparationResult,
)

_CONTRACT = "satn-prepared-scenario-compilation/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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


@dataclass(frozen=True)
class PreparedCandidateCriteria:
    """One exact governed criteria packet for one prepared candidate set.

    Criteria construction belongs to governed evidence adapters.  Keeping this
    small bridge dependent on their exact output means an omitted section is a
    visible incomplete result, never an optimistic default.
    """

    access_connection_id: str
    criteria: CandidateCriteria

    def __post_init__(self) -> None:
        if (
            not self.access_connection_id
            or self.access_connection_id.strip() != self.access_connection_id
        ):
            raise ValueError("access_connection_id must be a non-blank canonical identifier")
        if not isinstance(self.criteria, CandidateCriteria):
            raise ValueError("criteria must be an exact CandidateCriteria record")
        object.__setattr__(
            self,
            "criteria",
            CandidateCriteria.model_validate(self.criteria.model_dump(mode="python")),
        )


@dataclass(frozen=True)
class PreparedScenarioCompilationInput:
    """Data-only scenario request; it cannot carry runtime or publication power."""

    area_fingerprint: str
    criteria: tuple[PreparedCandidateCriteria, ...] = ()
    decision_record: ScenarioDecisionRecord | None = None
    review_run_instance_id: str = "prepared-scenario-review"

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.area_fingerprint) is None:
            raise ValueError("area_fingerprint must be a lowercase SHA-256 content identity")
        if (
            not self.review_run_instance_id
            or self.review_run_instance_id.strip() != self.review_run_instance_id
        ):
            raise ValueError("review_run_instance_id must be a non-blank canonical identifier")
        criteria = tuple(sorted(self.criteria, key=lambda item: item.access_connection_id))
        if len({item.access_connection_id for item in criteria}) != len(criteria):
            raise ValueError("at most one criteria record is allowed per prepared connection")
        object.__setattr__(self, "criteria", criteria)
        if self.decision_record is not None:
            object.__setattr__(
                self,
                "decision_record",
                ScenarioDecisionRecord.model_validate(
                    self.decision_record.model_dump(mode="python")
                ),
            )


@dataclass(frozen=True)
class PreparedScenarioCompilationResult:
    """Inspectable optional output with explicit incomplete/disabled states."""

    contract: str
    status: Literal["disabled", "incomplete", "compiled", "review-required"]
    preparation_fingerprint: str | None
    scenario: ScenarioCompilation | None
    review_orchestration: ScenarioReviewOrchestration | None
    missing_inputs: tuple[str, ...]
    diagnostics: dict[str, object]
    result_fingerprint: str

    @property
    def reference_satn_created(self) -> bool:
        """Scenario output is never Reference SATN authority in this slice."""

        return False

    @property
    def can_mutate_authoritative_network(self) -> bool:
        """Route mutation is explicitly deferred to a later governed step."""

        return False

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
            "diagnostics": self.diagnostics,
            "result_fingerprint": self.result_fingerprint,
        }


def compile_prepared_scenario(
    preparation: SpineAccessCandidatePreparationResult | None,
    request: PreparedScenarioCompilationInput,
) -> PreparedScenarioCompilationResult:
    """Turn complete prepared sets and exact criteria into a reviewable scenario.

    Missing preparation or criterion packets yields an honest non-publishable
    result.  A malformed, foreign, or stale criterion/ledger instead fails
    closed by raising through the exact alignment-selection validation models.
    """

    request = PreparedScenarioCompilationInput(
        area_fingerprint=request.area_fingerprint,
        criteria=request.criteria,
        decision_record=request.decision_record,
        review_run_instance_id=request.review_run_instance_id,
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

    prepared = tuple(
        sorted(
            preparation.prepared_spine_access_connections,
            key=lambda item: item.access_connection_id,
        )
    )
    if preparation.status != "prepared":
        return _result(
            status="incomplete",
            preparation=preparation,
            scenario=None,
            review=None,
            missing=tuple(
                sorted({"candidate-preparation-incomplete", *preparation.missing_inputs})
            ),
            diagnostics={
                "selection_performed": False,
                "agent_runtime_constructed": False,
                "reason": "prepared-candidate-evidence-incomplete",
                "prepared_connection_count": len(prepared),
            },
        )
    if not prepared:
        return _result(
            status="incomplete",
            preparation=preparation,
            scenario=None,
            review=None,
            missing=("prepared-community-access-candidate-set",),
            diagnostics={
                "selection_performed": False,
                "agent_runtime_constructed": False,
                "reason": "no-community-access-candidate-sets",
            },
        )

    prepared_by_id = {item.access_connection_id: item for item in prepared}
    criteria_by_id = {item.access_connection_id: item.criteria for item in request.criteria}
    foreign = set(criteria_by_id) - set(prepared_by_id)
    if foreign:
        raise ValueError(
            "criteria records name unprepared Spine Access connections: "
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
            diagnostics={
                "selection_performed": False,
                "agent_runtime_constructed": False,
                "reason": "governed-criterion-input-missing",
                "prepared_connection_count": len(prepared),
            },
        )

    _validate_prepared_candidate_sets(preparation)
    selections: list[PreferredStrategicAlignment] = []
    for item in prepared:
        criteria = criteria_by_id[item.access_connection_id]
        if criteria.education.candidate_set != item.candidate_set:
            raise ValueError(
                "criteria education section is stale for prepared candidate set "
                + item.access_connection_id
            )
        selections.append(
            select_preferred_alignment(
                item.candidate_set.profile,
                item.candidate_set,
                criteria,
            )
        )
    selections_tuple = tuple(sorted(selections, key=lambda item: item.candidate_set_id))
    decision_record = request.decision_record or ScenarioDecisionRecord(
        mode=(
            DecisionProcessMode.NO_AGENT
            if all(item.disposition == SelectionDisposition.SELECTED for item in selections_tuple)
            else DecisionProcessMode.PROFILE_FALLBACK
        )
    )
    scenario = _build_scenario(
        request.area_fingerprint,
        preparation,
        selections_tuple,
        decision_record,
    )
    # The core retains a provisional compiler selection even after an accepted
    # data-only ledger resolves it.  Its ``publishable`` value is therefore the
    # authoritative indication of whether a review frontier remains; this
    # bridge still withholds Reference SATN/publication authority either way.
    needs_review = not scenario.publishable
    review = (
        orchestrate_scenario_review(
            scenario,
            dependencies=tuple(
                ScenarioReviewDependency(candidate_set_id=item.candidate_set_id)
                for item in scenario.candidate_sets
            ),
            run_instance_id=request.review_run_instance_id,
        )
        if needs_review
        else None
    )
    return _result(
        status="review-required" if needs_review else "compiled",
        preparation=preparation,
        scenario=scenario,
        review=review,
        missing=(),
        diagnostics={
            "selection_performed": True,
            "agent_runtime_constructed": False,
            "decision_mode": scenario.decision_record.mode.value,
            "selection_count": len(scenario.selections),
            "review_request_count": (
                len(review.actionable_requests) if review is not None else 0
            ),
            "reference_satn_created": False,
            "authoritative_network_geometry_mutated": False,
            "replay_directive": "recompile-whole-network-on-ledger-change",
        },
    )


def _validate_prepared_candidate_sets(
    preparation: SpineAccessCandidatePreparationResult,
) -> None:
    """Reject any attempt to relabel Spine Access as an interurban spine."""

    if any(
        item.candidate_set.network_role != NetworkRole.COMMUNITY_ACCESS
        for item in preparation.prepared_spine_access_connections
    ):
        raise ValueError(
            "prepared Spine Access candidates must retain NetworkRole.COMMUNITY_ACCESS"
        )
    profiles = {
        item.candidate_set.profile_fingerprint
        for item in preparation.prepared_spine_access_connections
    }
    if profiles != {preparation.profile_fingerprint}:
        raise ValueError("prepared candidate sets do not share the preparation profile")


def _build_scenario(
    area_fingerprint: str,
    preparation: SpineAccessCandidatePreparationResult,
    selections: tuple[PreferredStrategicAlignment, ...],
    decision_record: ScenarioDecisionRecord,
) -> ScenarioCompilation:
    """Build only the core's immutable Scenario Compilation model."""

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


def _result(
    *,
    status: Literal["disabled", "incomplete", "compiled", "review-required"],
    preparation: SpineAccessCandidatePreparationResult | None,
    scenario: ScenarioCompilation | None,
    review: ScenarioReviewOrchestration | None,
    missing: tuple[str, ...],
    diagnostics: dict[str, object],
) -> PreparedScenarioCompilationResult:
    payload = {
        "contract": _CONTRACT,
        "status": status,
        "preparation_fingerprint": (
            preparation.preparation_fingerprint if preparation is not None else None
        ),
        "scenario_fingerprint": scenario.scenario_fingerprint if scenario is not None else None,
        "review_orchestration_fingerprint": (
            review.orchestration_fingerprint if review is not None else None
        ),
        "missing_inputs": missing,
        "diagnostics": diagnostics,
        "reference_satn_created": False,
        "authoritative_network_geometry_mutated": False,
    }
    return PreparedScenarioCompilationResult(
        contract=_CONTRACT,
        status=status,
        preparation_fingerprint=(
            preparation.preparation_fingerprint if preparation is not None else None
        ),
        scenario=scenario,
        review_orchestration=review,
        missing_inputs=missing,
        diagnostics=diagnostics,
        result_fingerprint=_fingerprint(payload),
    )
