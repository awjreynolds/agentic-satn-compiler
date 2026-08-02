"""Immutable production seam for a reviewable, intervention-legible network.

The existing prepared-scenario compiler remains the authority for candidate
selection, criteria and route geometry.  This module is a deliberately small
adapter around that result: it exposes effective routes, non-routable gaps,
typed evidence requests and continuing pre-loaded officer decisions without
mutating or re-running the compiler.

Fingerprints in this module are deterministic content identities only.  They
are not signatures, credentials or trust roots.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from satn.alignment_selection import (
    AlignmentCandidateInput,
    CandidateSetGapEvidence,
    NetworkRole,
    PreferredStrategicAlignment,
    ScenarioCompilation,
    SelectionDisposition,
)
from satn.parallel_reduction import PreloadedOfficerDecision
from satn.scenario_compilation import (
    PreparedScenarioCompilationInput,
    PreparedScenarioCompilationResult,
    compile_prepared_scenario,
)
from satn.spine_access_candidate_preparation import SpineAccessCandidatePreparationResult

_CONTRACT = "satn-reviewable-network/v1"


class ReviewableNetworkStatus(StrEnum):
    COMPLETE = "complete"
    TERMINAL_FAILURE = "terminal-failure"


class ReviewableDisplayState(StrEnum):
    EXISTING_PROVISION = "existing-provision"
    UPGRADE_REQUIRED = "upgrade-required"
    PROPOSED_NEW_LINK = "proposed-new-link"
    UNDETERMINED = "undetermined"
    UNRESOLVED_GAP = "unresolved-gap"


class ReviewableEvidenceRequestKind(StrEnum):
    NETWORK_GAP = "network-gap"
    OPTIONAL_EVIDENCE = "optional-evidence"
    SELECTION_REVIEW = "selection-review"
    OFFICER_TARGET = "officer-target"
    MANDATORY_LINEAGE = "mandatory-lineage"


class OfficerDecisionApplicationStatus(StrEnum):
    APPLIED = "applied"
    TARGET_UNAVAILABLE = "target-unavailable"


def _fingerprint(value: object) -> str:
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


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
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


def _json_value(value: object) -> object:
    """Convert our frozen records and compiler models to stable JSON values."""

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


@dataclass(frozen=True)
class ReviewableEvidenceRequest:
    """One bounded request for evidence or human review."""

    request_id: str
    kind: ReviewableEvidenceRequestKind
    reason: str
    candidate_set_id: str | None = None
    target_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    fingerprint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_ids", tuple(sorted(set(self.evidence_ids))))
        expected = _fingerprint(
            {
                "request_id": self.request_id,
                "kind": self.kind.value,
                "reason": self.reason,
                "candidate_set_id": self.candidate_set_id,
                "target_id": self.target_id,
                "evidence_ids": list(self.evidence_ids),
            }
        )
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("reviewable evidence request fingerprint is stale")
        object.__setattr__(self, "fingerprint", expected)


@dataclass(frozen=True)
class ReviewableDiagnostic:
    """A stable diagnostic retained alongside the compiler payload."""

    diagnostic_id: str
    severity: Literal["info", "warning", "error"]
    message: str
    candidate_set_id: str | None = None
    details: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        frozen = _freeze(self.details)
        if not isinstance(frozen, Mapping):
            raise TypeError("diagnostic details must be a mapping")
        object.__setattr__(self, "details", frozen)


@dataclass(frozen=True)
class EffectiveReviewableSelection:
    """The effective primary route while retaining compiler preference."""

    candidate_set_id: str
    connection_id: str
    candidate_id: str
    candidate: AlignmentCandidateInput
    compiler_candidate_id: str
    selection_disposition: SelectionDisposition
    display_state: ReviewableDisplayState
    officer_decision_id: str | None = None

    @property
    def geometry(self) -> object:
        """Return the exact compiler geometry; no wrapper geometry is invented."""

        return self.candidate.geometry

    @property
    def compiler_preferred_candidate_id(self) -> str:
        return self.compiler_candidate_id


@dataclass(frozen=True)
class ReviewableNetworkGap:
    """A non-routable endpoint gap with no geometry field/value."""

    gap_id: str
    candidate_set_id: str | None
    connection_id: str | None
    network_role: NetworkRole | None
    endpoints: tuple[str, ...]
    reason: str
    unsatisfied_network_place_ids: tuple[str, ...] = ()
    unsatisfied_access_obligation_ids: tuple[str, ...] = ()
    unsatisfied_strategic_destination_ids: tuple[str, ...] = ()
    display_state: ReviewableDisplayState = ReviewableDisplayState.UNRESOLVED_GAP

    @property
    def geometry(self) -> None:
        """Gaps are endpoint findings, never route geometry."""

        return None


@dataclass(frozen=True)
class OfficerDecisionRecord:
    """Application status for one continuing, exact-target officer decision."""

    decision_id: str
    target_id: str
    route_id: str
    status: OfficerDecisionApplicationStatus
    candidate_set_id: str | None = None
    candidate_id: str | None = None


@dataclass(frozen=True)
class OfficerCompilerDivergence:
    """A distinct finding where officer and compiler routes differ."""

    candidate_set_id: str
    target_id: str
    officer_candidate_id: str
    compiler_candidate_id: str
    officer_decision_id: str


@dataclass(frozen=True)
class ReviewableNetwork:
    """Deeply immutable result of :func:`compile_reviewable_network`."""

    contract: str
    status: ReviewableNetworkStatus
    preparation_fingerprint: str | None
    profile_fingerprint: str | None
    scenario: ScenarioCompilation | None
    compiler_result: PreparedScenarioCompilationResult | None
    effective_selections: tuple[EffectiveReviewableSelection, ...] = ()
    network_gaps: tuple[ReviewableNetworkGap, ...] = ()
    evidence_requests: tuple[ReviewableEvidenceRequest, ...] = ()
    diagnostics: Mapping[str, object] = MappingProxyType({})
    typed_diagnostics: tuple[ReviewableDiagnostic, ...] = ()
    officer_decisions: tuple[OfficerDecisionRecord, ...] = ()
    divergences: tuple[OfficerCompilerDivergence, ...] = ()
    target_unavailable: tuple[OfficerDecisionRecord, ...] = ()
    failure_code: str | None = None
    result_fingerprint: str = ""

    def __post_init__(self) -> None:
        frozen = _freeze(self.diagnostics)
        if not isinstance(frozen, Mapping):
            raise TypeError("reviewable diagnostics must be a mapping")
        object.__setattr__(self, "diagnostics", frozen)
        expected = _fingerprint(self._fingerprint_payload())
        if self.result_fingerprint and self.result_fingerprint != expected:
            raise ValueError("reviewable network result fingerprint is stale")
        object.__setattr__(self, "result_fingerprint", expected)

    @property
    def fingerprint(self) -> str:
        return self.result_fingerprint

    @property
    def complete(self) -> bool:
        return self.status == ReviewableNetworkStatus.COMPLETE

    @property
    def effective_routes(self) -> tuple[EffectiveReviewableSelection, ...]:
        return self.effective_selections

    @property
    def selected_candidate_ids(self) -> tuple[str, ...]:
        return tuple(item.candidate_id for item in self.effective_selections)

    @property
    def officer_compiler_divergences(self) -> tuple[OfficerCompilerDivergence, ...]:
        return self.divergences

    @property
    def metadata(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "status": self.status.value,
            "preparation_fingerprint": self.preparation_fingerprint,
            "profile_fingerprint": self.profile_fingerprint,
            "scenario_fingerprint": (
                self.scenario.scenario_fingerprint if self.scenario is not None else None
            ),
            "effective_selections": [
                {
                    "candidate_set_id": item.candidate_set_id,
                    "candidate_id": item.candidate_id,
                    "compiler_candidate_id": item.compiler_candidate_id,
                    "display_state": item.display_state.value,
                }
                for item in self.effective_selections
            ],
            "network_gaps": [_json_value(item) for item in self.network_gaps],
            "evidence_requests": [_json_value(item) for item in self.evidence_requests],
            "diagnostics": _thaw(self.diagnostics),
            "failure_code": self.failure_code,
            "result_fingerprint": self.result_fingerprint,
        }

    def _fingerprint_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "status": self.status.value,
            "preparation_fingerprint": self.preparation_fingerprint,
            "profile_fingerprint": self.profile_fingerprint,
            "scenario_fingerprint": (
                self.scenario.scenario_fingerprint if self.scenario is not None else None
            ),
            "compiler_result_fingerprint": (
                self.compiler_result.result_fingerprint
                if self.compiler_result is not None
                else None
            ),
            "effective_selections": [_json_value(item) for item in self.effective_selections],
            "network_gaps": [_json_value(item) for item in self.network_gaps],
            "evidence_requests": [_json_value(item) for item in self.evidence_requests],
            "diagnostics": _thaw(self.diagnostics),
            "typed_diagnostics": [_json_value(item) for item in self.typed_diagnostics],
            "officer_decisions": [_json_value(item) for item in self.officer_decisions],
            "divergences": [_json_value(item) for item in self.divergences],
            "target_unavailable": [_json_value(item) for item in self.target_unavailable],
            "failure_code": self.failure_code,
        }


def _decision_identity(target_id: str, route_id: str) -> str:
    return "officer-decision-" + _fingerprint(
        {"target_id": target_id, "route_id": route_id}
    )[:20]


def _display_state(candidate: AlignmentCandidateInput) -> ReviewableDisplayState:
    value = candidate.intervention_state
    if value is None:
        # Legacy candidates have no intervention assertion.  Keep the route
        # routable without claiming an unsupported asset state.
        return ReviewableDisplayState.UNDETERMINED
    return ReviewableDisplayState(value.value)


def _gap_from_selection(selection: PreferredStrategicAlignment) -> ReviewableNetworkGap:
    candidate_set = selection.candidate_set
    criteria = selection.criteria
    reason = (
        criteria.generation_gap_reason.value
        if isinstance(criteria, CandidateSetGapEvidence)
        else "selection-network-gap"
    )
    gap_id = "network-gap-" + _fingerprint(
        {
            "candidate_set_id": candidate_set.candidate_set_id,
            "reason": reason,
            "endpoints": candidate_set.endpoints,
        }
    )[:20]
    return ReviewableNetworkGap(
        gap_id=gap_id,
        candidate_set_id=candidate_set.candidate_set_id,
        connection_id=candidate_set.connection_id,
        network_role=candidate_set.network_role,
        endpoints=candidate_set.endpoints,
        reason=reason,
        unsatisfied_network_place_ids=candidate_set.mandatory_network_place_ids,
        unsatisfied_access_obligation_ids=candidate_set.mandatory_access_obligation_ids,
        unsatisfied_strategic_destination_ids=candidate_set.mandatory_strategic_destination_ids,
    )


def _gap_from_roster(item: object, reason: str) -> ReviewableNetworkGap | None:
    if not isinstance(item, object):
        return None
    connection_id = getattr(item, "access_connection_id", None)
    endpoints = tuple(
        sorted(
            {
                value
                for value in (
                    getattr(item, "place_id", None),
                    getattr(item, "parent_place_id", None),
                )
                if isinstance(value, str) and value
            }
        )
    )
    if not isinstance(connection_id, str) or not endpoints:
        return None
    row_reason = getattr(item, "reason", None)
    if not isinstance(row_reason, str) or not row_reason:
        row_reason = reason
    gap_id = "network-gap-" + _fingerprint(
        {"connection_id": connection_id, "endpoints": endpoints, "reason": row_reason}
    )[:20]
    return ReviewableNetworkGap(
        gap_id=gap_id,
        candidate_set_id=None,
        connection_id=connection_id,
        network_role=None,
        endpoints=endpoints,
        reason=row_reason,
        unsatisfied_network_place_ids=endpoints,
    )


def _traffic_requests(
    compiler_result: PreparedScenarioCompilationResult,
    scenario: ScenarioCompilation,
) -> tuple[ReviewableEvidenceRequest, ...]:
    requests: list[ReviewableEvidenceRequest] = []
    payload = compiler_result.diagnostics.get("traffic_diagnostics", ())
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        return ()
    candidate_set_by_candidate_id = {
        candidate.candidate_id: candidate_set.candidate_set_id
        for candidate_set in scenario.candidate_sets
        for candidate in candidate_set.candidates
    }
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("traffic_status", "unknown"))
        if status in {"matched", "fresh", "sampled"}:
            continue
        diagnostic_id = str(item.get("diagnostic_id", "traffic-unknown"))
        candidate_id = item.get("candidate_id")
        candidate_set_id = (
            candidate_set_by_candidate_id.get(candidate_id)
            if isinstance(candidate_id, str)
            else None
        )
        evidence_ids = item.get("evidence_ids", ())
        if not isinstance(evidence_ids, (tuple, list)):
            evidence_ids = ()
        requests.append(
            ReviewableEvidenceRequest(
                request_id="evidence-request-" + _fingerprint(item)[:20],
                kind=ReviewableEvidenceRequestKind.OPTIONAL_EVIDENCE,
                reason=diagnostic_id,
                candidate_set_id=candidate_set_id,
                target_id=(str(candidate_id) if isinstance(candidate_id, str) else None),
                evidence_ids=tuple(str(value) for value in evidence_ids),
            )
        )
    return tuple(sorted(requests, key=lambda item: item.request_id))


def _terminal_result(
    preparation: SpineAccessCandidatePreparationResult | None,
    compiler_result: PreparedScenarioCompilationResult | None,
    *,
    code: str,
    detail: str,
) -> ReviewableNetwork:
    diagnostics = {
        "terminal_failure": {
            "code": code,
            "detail": detail,
            "publication_action": "retain-previous-valid-publication",
        }
    }
    return ReviewableNetwork(
        contract=_CONTRACT,
        status=ReviewableNetworkStatus.TERMINAL_FAILURE,
        preparation_fingerprint=(
            preparation.preparation_fingerprint if preparation is not None else None
        ),
        profile_fingerprint=(preparation.profile_fingerprint if preparation is not None else None),
        scenario=None,
        compiler_result=compiler_result,
        diagnostics=diagnostics,
        evidence_requests=(
            ReviewableEvidenceRequest(
                request_id="mandatory-lineage-" + _fingerprint(diagnostics)[:20],
                kind=ReviewableEvidenceRequestKind.MANDATORY_LINEAGE,
                reason=code,
            ),
        ),
        failure_code=code,
    )


_KNOWN_GOVERNED_ERROR_PREFIXES = (
    "unsupported candidate preparation contract",
    "candidate preparation fingerprint is stale",
    "prepared status requires one exhaustive unique connection roster",
    "prepared connection sets do not exactly match the roster",
    "generation issue names no expected roster connection",
    "direct-to-spine roster disposition lacks exact evidence",
    "unresolved roster connection requires an explicit issue",
    "preparation roster diagnostics are stale",
    "prepared evidence fingerprints are empty, foreign or stale",
    "criteria records name unprepared Community Connections:",
    "criteria packet is stale for the exact preparation and raw evidence lineage",
    "criterion evidence snapshot must contain exactly one binding for each required "
    "assessment kind",
    "prepared candidate sets do not share the preparation profile",
    "only provenance-proven chained Community Connections may be promoted",
    "candidate endpoints do not match exact Community provenance",
    "candidate served Network Places do not match exact Community provenance",
    "gap evidence is stale for prepared candidate set",
    "criteria education section is stale for prepared candidate set",
    "population-reach criterion source is foreign or stale",
    "network-geometry criterion source is foreign or stale",
    "topography criterion source is foreign or stale",
    "education-access criterion source is foreign or stale",
    "education-access gap source is foreign or stale",
    "education-access criterion does not extend the prepared source lineage",
    "prepared evidence lineage is malformed",
    "prepared population/education source lineage is malformed",
    "prepared education lineage must retain the pre-candidate source snapshot",
)


def _is_known_governed_error(error: ValueError) -> bool:
    message = str(error)
    return any(message.startswith(prefix) for prefix in _KNOWN_GOVERNED_ERROR_PREFIXES)


def _validate_officer_decisions(
    officer_decisions: Sequence[PreloadedOfficerDecision],
) -> tuple[PreloadedOfficerDecision, ...]:
    if isinstance(officer_decisions, (str, bytes)) or not isinstance(
        officer_decisions, Sequence
    ):
        raise TypeError("officer_decisions must be a sequence of PreloadedOfficerDecision")
    typed = tuple(officer_decisions)
    if any(not isinstance(item, PreloadedOfficerDecision) for item in typed):
        raise TypeError("officer_decisions must contain only PreloadedOfficerDecision")
    by_target: dict[str, str] = {}
    for item in typed:
        previous = by_target.get(item.target_id)
        if previous is not None:
            raise ValueError(
                "duplicate/conflicting officer decisions for target " + item.target_id
            )
        by_target[item.target_id] = item.route_id
    return tuple(
        sorted(typed, key=lambda item: (item.target_id, item.route_id))
    )


def compile_reviewable_network(
    preparation: SpineAccessCandidatePreparationResult | None,
    request: PreparedScenarioCompilationInput,
    officer_decisions: Sequence[PreloadedOfficerDecision] = (),
) -> ReviewableNetwork:
    """Compile a complete immutable Reviewable Network.

    The compiler remains responsible for scenario validity and geometry.  A
    governed lineage failure becomes a terminal result; optional diagnostics,
    unresolved candidate sets and unavailable officer targets remain visible in
    a completed result.
    """

    if not isinstance(request, PreparedScenarioCompilationInput):
        raise TypeError("request must be PreparedScenarioCompilationInput")
    if preparation is not None and not isinstance(
        preparation, SpineAccessCandidatePreparationResult
    ):
        raise TypeError("preparation must be SpineAccessCandidatePreparationResult or None")
    officer_decisions = _validate_officer_decisions(officer_decisions)

    try:
        compiler_result = compile_prepared_scenario(preparation, request)
    except ValueError as error:
        if not _is_known_governed_error(error):
            raise
        return _terminal_result(
            preparation,
            None,
            code="mandatory-lineage-invalid",
            detail=str(error),
        )

    if compiler_result.scenario is None:
        # An entirely out-of-scope preparation has no route obligation.  A
        # prepared roster with endpoint evidence can still be published as a
        # visible endpoint gap rather than silently disappearing.
        if compiler_result.status == "incomplete" and preparation is not None:
            unresolved = any(
                getattr(item, "disposition", None) == "unresolved-gap"
                for item in preparation.connection_roster
            )
            if unresolved or compiler_result.missing_inputs == (
                "eligible-chained-community-connection",
            ):
                gaps = tuple(
                    gap
                    for item in preparation.connection_roster
                    if (
                        getattr(item, "disposition", None) == "unresolved-gap"
                        if unresolved
                        else getattr(item, "disposition", None)
                        != "out-of-scope-direct-strategic-spine"
                    )
                    for gap in (_gap_from_roster(item, "no-eligible-candidate-set"),)
                    if gap is not None
                )
                return ReviewableNetwork(
                    contract=_CONTRACT,
                    status=ReviewableNetworkStatus.COMPLETE,
                    preparation_fingerprint=preparation.preparation_fingerprint,
                    profile_fingerprint=preparation.profile_fingerprint,
                    scenario=None,
                    compiler_result=compiler_result,
                    network_gaps=gaps,
                    diagnostics=compiler_result.diagnostics,
                    evidence_requests=tuple(
                        ReviewableEvidenceRequest(
                            request_id="network-gap-" + gap.gap_id,
                            kind=ReviewableEvidenceRequestKind.NETWORK_GAP,
                            reason=gap.reason,
                            target_id=gap.connection_id,
                        )
                        for gap in gaps
                    ),
                )
        return _terminal_result(
            preparation,
            compiler_result,
            code="mandatory-input-incomplete",
            detail="; ".join(compiler_result.missing_inputs) or "scenario was not compiled",
        )

    scenario = compiler_result.scenario
    by_set_id = {item.candidate_set_id: item for item in scenario.selections}
    effective: list[EffectiveReviewableSelection] = []
    gaps: list[ReviewableNetworkGap] = []
    requests: list[ReviewableEvidenceRequest] = list(
        _traffic_requests(compiler_result, scenario)
    )
    typed_diagnostics: list[ReviewableDiagnostic] = []

    for diagnostic in compiler_result.diagnostics.get("traffic_diagnostics", ()):
        if isinstance(diagnostic, Mapping):
            diagnostic_id = str(diagnostic.get("diagnostic_id", "traffic-unknown"))
            typed_diagnostics.append(
                ReviewableDiagnostic(
                    diagnostic_id=diagnostic_id,
                    severity="warning",
                    message=diagnostic_id,
                    details=diagnostic,
                )
            )

    for selection in scenario.selections:
        if selection.disposition == SelectionDisposition.NETWORK_GAP:
            gap = _gap_from_selection(selection)
            gaps.append(gap)
            requests.append(
                ReviewableEvidenceRequest(
                    request_id="network-gap-" + gap.gap_id,
                    kind=ReviewableEvidenceRequestKind.NETWORK_GAP,
                    reason=gap.reason,
                    candidate_set_id=gap.candidate_set_id,
                    evidence_ids=gap.unsatisfied_access_obligation_ids,
                )
            )
            continue
        selected_id = selection.selected_candidate_id
        if selected_id is None:
            continue
        candidate = next(
            item
            for item in selection.candidate_set.admitted_candidates
            if item.candidate_id == selected_id
        )
        effective.append(
            EffectiveReviewableSelection(
                candidate_set_id=selection.candidate_set_id,
                connection_id=selection.candidate_set.connection_id,
                candidate_id=selected_id,
                candidate=candidate,
                compiler_candidate_id=selected_id,
                selection_disposition=selection.disposition,
                display_state=_display_state(candidate),
            )
        )
        for trigger in selection.ambiguity_triggers:
            requests.append(
                ReviewableEvidenceRequest(
                    request_id="selection-review-"
                    + _fingerprint((selection.candidate_set_id, trigger.value))[:20],
                    kind=ReviewableEvidenceRequestKind.SELECTION_REVIEW,
                    reason=trigger.value,
                    candidate_set_id=selection.candidate_set_id,
            )
            )

    # Prepared selections and unresolved sibling rows are independent
    # obligations.  Retain each unresolved roster row as its own endpoint
    # finding; never turn a valid prepared candidate into a gap.
    if preparation is not None:
        for roster_item in preparation.connection_roster:
            if roster_item.disposition != "unresolved-gap":
                continue
            gap = _gap_from_roster(roster_item, "unresolved-preparation")
            if gap is None:
                continue
            gaps.append(gap)
            requests.append(
                ReviewableEvidenceRequest(
                    request_id="network-gap-" + gap.gap_id,
                    kind=ReviewableEvidenceRequestKind.NETWORK_GAP,
                    reason=gap.reason,
                    target_id=gap.connection_id,
                )
            )

    officer_records: list[OfficerDecisionRecord] = []
    unavailable: list[OfficerDecisionRecord] = []
    divergences: list[OfficerCompilerDivergence] = []
    prepared_by_id = {
        item.access_connection_id: item
        for item in (preparation.prepared_spine_access_connections if preparation else ())
    }
    prepared_by_candidate_set_id = {
        item.candidate_set.candidate_set_id: item
        for item in (preparation.prepared_spine_access_connections if preparation else ())
    }
    for decision in officer_decisions:
        target_id = decision.target_id
        route_id = decision.route_id
        decision_id = _decision_identity(target_id, route_id)
        target_selection = None
        for candidate_set_id, selection in by_set_id.items():
            prepared_item = prepared_by_candidate_set_id.get(candidate_set_id)
            if prepared_item is None:
                prepared_item = prepared_by_id.get(selection.candidate_set.connection_id)
            aliases = {
                candidate_set_id,
                selection.candidate_set.connection_id,
                *( (prepared_item.access_connection_id,) if prepared_item is not None else () ),
            }
            if target_id in aliases:
                target_selection = selection
                break
        if target_selection is None:
            record = OfficerDecisionRecord(
                decision_id=decision_id,
                target_id=target_id,
                route_id=route_id,
                status=OfficerDecisionApplicationStatus.TARGET_UNAVAILABLE,
            )
            officer_records.append(record)
            unavailable.append(record)
            requests.append(
                ReviewableEvidenceRequest(
                    request_id="officer-target-" + decision_id,
                    kind=ReviewableEvidenceRequestKind.OFFICER_TARGET,
                    reason="officer-decision-target-unavailable",
                    target_id=target_id,
                )
            )
            continue
        candidate = next(
            (
                item
                for item in target_selection.candidate_set.admitted_candidates
                if item.candidate_id == route_id
            ),
            None,
        )
        if candidate is None:
            record = OfficerDecisionRecord(
                decision_id=decision_id,
                target_id=target_id,
                route_id=route_id,
                status=OfficerDecisionApplicationStatus.TARGET_UNAVAILABLE,
                candidate_set_id=target_selection.candidate_set_id,
            )
            officer_records.append(record)
            unavailable.append(record)
            requests.append(
                ReviewableEvidenceRequest(
                    request_id="officer-target-" + decision_id,
                    kind=ReviewableEvidenceRequestKind.OFFICER_TARGET,
                    reason="officer-decision-route-unavailable",
                    candidate_set_id=target_selection.candidate_set_id,
                    target_id=target_id,
                )
            )
            continue
        compiler_candidate_id = target_selection.selected_candidate_id
        if compiler_candidate_id is None:
            # A gap cannot be turned into a route by an officer decision whose
            # exact current Candidate Set has no admitted route.
            record = OfficerDecisionRecord(
                decision_id=decision_id,
                target_id=target_id,
                route_id=route_id,
                status=OfficerDecisionApplicationStatus.TARGET_UNAVAILABLE,
                candidate_set_id=target_selection.candidate_set_id,
                candidate_id=candidate.candidate_id,
            )
            officer_records.append(record)
            unavailable.append(record)
            continue
        record = OfficerDecisionRecord(
            decision_id=decision_id,
            target_id=target_id,
            route_id=route_id,
            status=OfficerDecisionApplicationStatus.APPLIED,
            candidate_set_id=target_selection.candidate_set_id,
            candidate_id=candidate.candidate_id,
        )
        officer_records.append(record)
        for index, item in enumerate(effective):
            if item.candidate_set_id != target_selection.candidate_set_id:
                continue
            effective[index] = EffectiveReviewableSelection(
                candidate_set_id=item.candidate_set_id,
                connection_id=item.connection_id,
                candidate_id=candidate.candidate_id,
                candidate=candidate,
                compiler_candidate_id=item.compiler_candidate_id,
                selection_disposition=item.selection_disposition,
                display_state=_display_state(candidate),
                officer_decision_id=decision_id,
            )
            if candidate.candidate_id != item.compiler_candidate_id:
                divergences.append(
                    OfficerCompilerDivergence(
                        candidate_set_id=item.candidate_set_id,
                        target_id=target_id,
                        officer_candidate_id=candidate.candidate_id,
                        compiler_candidate_id=item.compiler_candidate_id,
                        officer_decision_id=decision_id,
                    )
                )
            break

    return ReviewableNetwork(
        contract=_CONTRACT,
        status=ReviewableNetworkStatus.COMPLETE,
        preparation_fingerprint=(preparation.preparation_fingerprint if preparation else None),
        profile_fingerprint=(
            preparation.profile_fingerprint
            if preparation
            else scenario.profile_fingerprint
        ),
        scenario=scenario,
        compiler_result=compiler_result,
        effective_selections=tuple(
            sorted(effective, key=lambda item: item.candidate_set_id)
        ),
        network_gaps=tuple(sorted(gaps, key=lambda item: item.gap_id)),
        evidence_requests=tuple(
            sorted(
                {item.fingerprint: item for item in requests}.values(),
                key=lambda item: item.request_id,
            )
        ),
        diagnostics=compiler_result.diagnostics,
        typed_diagnostics=tuple(sorted(typed_diagnostics, key=lambda item: item.diagnostic_id)),
        officer_decisions=tuple(sorted(officer_records, key=lambda item: item.decision_id)),
        divergences=tuple(sorted(divergences, key=lambda item: item.candidate_set_id)),
        target_unavailable=tuple(sorted(unavailable, key=lambda item: item.decision_id)),
    )


__all__ = [
    "EffectiveReviewableSelection",
    "OfficerCompilerDivergence",
    "OfficerDecisionApplicationStatus",
    "OfficerDecisionRecord",
    "ReviewableDiagnostic",
    "ReviewableDisplayState",
    "ReviewableEvidenceRequest",
    "ReviewableEvidenceRequestKind",
    "ReviewableNetwork",
    "ReviewableNetworkGap",
    "ReviewableNetworkStatus",
    "compile_reviewable_network",
]
