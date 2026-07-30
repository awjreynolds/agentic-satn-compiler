"""Governed human decisions applied as overlays to a clean SATN baseline.

This module deliberately does not mutate compiler evidence, Area Definitions or
baseline network bytes.  It validates an attributable human ledger and produces
an immutable Scenario Compilation record whose overlay can be interpreted by
domain-specific compilers in later tickets.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("ascii")).hexdigest()


def _canonical_ids(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    if any(not _ID.fullmatch(value) for value in values):
        raise ValueError(f"{field} must contain canonical identifiers")
    if len(values) != len(set(values)):
        raise ValueError(f"{field} cannot contain duplicates")
    return tuple(sorted(values))


def _validate_source_url(value: str) -> str:
    if value != value.strip():
        raise ValueError("source_url must not contain surrounding whitespace")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source_url must be an absolute HTTP(S) URL")
    return value


class OfficerTargetKind(StrEnum):
    COMMUNITY = "community"
    STRATEGIC_SPINE = "strategic-spine"
    ALIGNMENT_CANDIDATE = "alignment-candidate"
    ROUTING_EDGE = "routing-edge"
    NETWORK_GAP = "network-gap"


class OfficerDecisionStatus(StrEnum):
    ACTIVE = "active"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class OfficerDecisionType(StrEnum):
    CLASSIFY_COMMUNITY = "classify-community"
    RETAIN_NETWORK_GAP = "retain-network-gap"
    SELECT_ALIGNMENT = "select-alignment"
    SET_TARGET_ELIGIBILITY = "set-target-eligibility"


class OfficerDecisionTarget(BaseModel):
    """Geometry-free binding to one compiler-governed target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: OfficerTargetKind
    target_id: str = Field(pattern=_ID.pattern)

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind.value, self.target_id)


class ClassifyCommunityAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["classify-community"] = "classify-community"
    classification: Literal["urban", "rural"]


class RetainNetworkGapAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["retain-network-gap"] = "retain-network-gap"


class SelectAlignmentAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["select-alignment"] = "select-alignment"


class SetTargetEligibilityAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["set-target-eligibility"] = "set-target-eligibility"
    eligibility: Literal["include", "exclude"]


OfficerDecisionAction = Annotated[
    ClassifyCommunityAction
    | RetainNetworkGapAction
    | SelectAlignmentAction
    | SetTargetEligibilityAction,
    Field(discriminator="kind"),
]


_ACTION_TARGETS: dict[str, frozenset[OfficerTargetKind]] = {
    "classify-community": frozenset({OfficerTargetKind.COMMUNITY}),
    "retain-network-gap": frozenset({OfficerTargetKind.NETWORK_GAP}),
    "select-alignment": frozenset({OfficerTargetKind.ALIGNMENT_CANDIDATE}),
    "set-target-eligibility": frozenset(OfficerTargetKind),
}


class OfficerDecision(BaseModel):
    """Frozen, attributable human decision bound to exact governed lineage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal["satn-officer-decision/v1"] = "satn-officer-decision/v1"
    actor_kind: Literal["human-officer"] = "human-officer"
    decision_id: str = Field(pattern=_ID.pattern)
    decision_type: OfficerDecisionType
    target: OfficerDecisionTarget
    action: OfficerDecisionAction
    decision_maker: str = Field(min_length=1)
    decision_maker_role: str = Field(min_length=1)
    organisation: str = Field(min_length=1)
    decision_date: date
    rationale: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    source_url: str
    effective_from: date
    effective_until: date | None = None
    status: OfficerDecisionStatus = OfficerDecisionStatus.ACTIVE
    baseline_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    decision_fingerprint: str = ""

    @field_validator(
        "decision_maker",
        "decision_maker_role",
        "organisation",
        "rationale",
    )
    @classmethod
    def canonical_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("officer decision text must not contain surrounding whitespace")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def canonical_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(value, "evidence_ids")

    @field_validator("source_url")
    @classmethod
    def canonical_source_url(cls, value: str) -> str:
        return _validate_source_url(value)

    @model_validator(mode="after")
    def bind_decision(self) -> Self:
        if self.decision_date > self.effective_from:
            raise ValueError("effective period cannot begin before the decision date")
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("effective period cannot end before it begins")
        if self.action.kind != self.decision_type.value:
            raise ValueError("decision type must match its typed action")
        if self.target.kind not in _ACTION_TARGETS[self.action.kind]:
            raise ValueError("decision action is incompatible with its target kind")
        payload = self.model_dump(mode="json", exclude={"decision_fingerprint"})
        expected = _fingerprint(payload)
        if self.decision_fingerprint and self.decision_fingerprint != expected:
            raise ValueError("officer decision fingerprint is stale")
        object.__setattr__(self, "decision_fingerprint", expected)
        return self


class OfficerDecisionLedger(BaseModel):
    """Canonical immutable human-decision ledger for one clean baseline."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal["satn-officer-decision-ledger/v1"] = (
        "satn-officer-decision-ledger/v1"
    )
    baseline_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    decisions: tuple[OfficerDecision, ...] = ()
    ledger_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_ledger(self) -> Self:
        decisions = tuple(sorted(self.decisions, key=lambda item: item.decision_id))
        if len({item.decision_id for item in decisions}) != len(decisions):
            raise ValueError("officer decision IDs must be unique")
        for decision in decisions:
            if (
                decision.baseline_fingerprint != self.baseline_fingerprint
                or decision.evidence_snapshot_fingerprint
                != self.evidence_snapshot_fingerprint
                or decision.profile_fingerprint != self.profile_fingerprint
            ):
                raise ValueError("officer decision is stale for its ledger lineage")
        active_by_target: dict[tuple[str, str], OfficerDecision] = {}
        active_keys: set[tuple[str, tuple[str, str]]] = set()
        for decision in decisions:
            if decision.status != OfficerDecisionStatus.ACTIVE:
                continue
            duplicate_key = (decision.decision_type.value, decision.target.key)
            if duplicate_key in active_keys:
                raise ValueError("duplicate active officer decision")
            active_keys.add(duplicate_key)
            existing = active_by_target.get(decision.target.key)
            if existing is not None and existing.action != decision.action:
                raise ValueError("conflicting active officer decisions")
            active_by_target[decision.target.key] = decision
        object.__setattr__(self, "decisions", decisions)
        payload = self.model_dump(mode="json", exclude={"ledger_fingerprint"})
        expected = _fingerprint(payload)
        if self.ledger_fingerprint and self.ledger_fingerprint != expected:
            raise ValueError("officer decision ledger fingerprint is stale")
        object.__setattr__(self, "ledger_fingerprint", expected)
        return self

    def canonical_json(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json")).encode("ascii")


def parse_canonical_officer_decision_ledger(value: bytes) -> OfficerDecisionLedger:
    """Import a persisted ledger only when its bytes are already canonical."""

    try:
        payload = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("officer decision ledger is not valid JSON") from error
    ledger = OfficerDecisionLedger.model_validate(payload)
    if value != ledger.canonical_json():
        raise ValueError("officer decision ledger JSON is not canonical")
    return ledger


class GovernedBaselineTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target: OfficerDecisionTarget


class CleanSATNBaseline(BaseModel):
    """Immutable baseline identity plus exact canonical network bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal["satn-clean-baseline/v1"] = "satn-clean-baseline/v1"
    baseline_id: str = Field(pattern=_ID.pattern)
    network_json: str = Field(min_length=2)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    governed_evidence_ids: tuple[str, ...] = Field(min_length=1)
    targets: tuple[GovernedBaselineTarget, ...] = ()
    network_sha256: str = ""
    baseline_fingerprint: str = ""

    @field_validator("governed_evidence_ids")
    @classmethod
    def canonical_governed_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(value, "governed_evidence_ids")

    @model_validator(mode="after")
    def bind_baseline(self) -> Self:
        try:
            parsed_network = json.loads(self.network_json)
        except json.JSONDecodeError as error:
            raise ValueError("baseline network_json is not valid JSON") from error
        if self.network_json != _canonical_json(parsed_network):
            raise ValueError("baseline network_json must use canonical JSON")
        targets = tuple(
            sorted(self.targets, key=lambda item: item.target.key)
        )
        target_keys = [item.target.key for item in targets]
        if len(target_keys) != len(set(target_keys)):
            raise ValueError("baseline targets must be unique")
        object.__setattr__(self, "targets", targets)
        network_sha256 = hashlib.sha256(self.network_json.encode("ascii")).hexdigest()
        if self.network_sha256 and self.network_sha256 != network_sha256:
            raise ValueError("baseline network fingerprint is stale")
        object.__setattr__(self, "network_sha256", network_sha256)
        payload = self.model_dump(
            mode="json",
            exclude={"network_json", "baseline_fingerprint"},
        )
        expected = _fingerprint(payload)
        if self.baseline_fingerprint and self.baseline_fingerprint != expected:
            raise ValueError("clean baseline fingerprint is stale")
        object.__setattr__(self, "baseline_fingerprint", expected)
        return self

    @property
    def network_bytes(self) -> bytes:
        return self.network_json.encode("ascii")


class OfferedOfficerAction(BaseModel):
    """One compiler-offered action for an actionable intervention request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    option_id: str = Field(pattern=_ID.pattern)
    decision_type: OfficerDecisionType
    target: OfficerDecisionTarget
    action: OfficerDecisionAction

    @model_validator(mode="after")
    def validate_offer(self) -> Self:
        if self.decision_type.value != self.action.kind:
            raise ValueError("offered decision type must match its action")
        if self.target.kind not in _ACTION_TARGETS[self.action.kind]:
            raise ValueError("offered action is incompatible with its target")
        return self


class InterventionRequestState(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ActionableHumanInterventionRequest(BaseModel):
    """Exact request lineage and the finite human actions the compiler offered."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal["satn-actionable-human-intervention/v1"] = (
        "satn-actionable-human-intervention/v1"
    )
    request_id: str = Field(pattern=_ID.pattern)
    request_fingerprint: str = Field(pattern=_SHA256.pattern)
    baseline_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    governed_evidence_ids: tuple[str, ...] = Field(min_length=1)
    offered_actions: tuple[OfferedOfficerAction, ...] = Field(min_length=1)

    @field_validator("governed_evidence_ids")
    @classmethod
    def canonical_request_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(value, "governed_evidence_ids")

    @model_validator(mode="after")
    def canonical_offers(self) -> Self:
        offers = tuple(sorted(self.offered_actions, key=lambda item: item.option_id))
        if len({item.option_id for item in offers}) != len(offers):
            raise ValueError("human intervention option IDs must be unique")
        object.__setattr__(self, "offered_actions", offers)
        return self


class HumanInterventionResponseOutcome(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class HumanInterventionResponse(BaseModel):
    """Typed human response selecting one exact compiler-offered action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal["satn-human-intervention-response/v1"] = (
        "satn-human-intervention-response/v1"
    )
    actor_kind: Literal["human-officer"] = "human-officer"
    response_id: str = Field(pattern=_ID.pattern)
    request_id: str = Field(pattern=_ID.pattern)
    request_fingerprint: str = Field(pattern=_SHA256.pattern)
    selected_option_id: str = Field(pattern=_ID.pattern)
    outcome: HumanInterventionResponseOutcome
    baseline_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    decision_maker: str = Field(min_length=1)
    decision_maker_role: str = Field(min_length=1)
    organisation: str = Field(min_length=1)
    response_date: date
    rationale: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    source_url: str
    effective_from: date
    effective_until: date | None = None
    response_fingerprint: str = ""

    @field_validator(
        "decision_maker",
        "decision_maker_role",
        "organisation",
        "rationale",
    )
    @classmethod
    def canonical_response_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("intervention response text must be canonical")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def canonical_response_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_ids(value, "evidence_ids")

    @field_validator("source_url")
    @classmethod
    def canonical_response_url(cls, value: str) -> str:
        return _validate_source_url(value)

    @model_validator(mode="after")
    def bind_response(self) -> Self:
        if self.response_date > self.effective_from:
            raise ValueError("response effective period cannot predate the response")
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("response effective period cannot end before it begins")
        payload = self.model_dump(mode="json", exclude={"response_fingerprint"})
        expected = _fingerprint(payload)
        if self.response_fingerprint and self.response_fingerprint != expected:
            raise ValueError("human intervention response fingerprint is stale")
        object.__setattr__(self, "response_fingerprint", expected)
        return self


class HumanInterventionRecord(BaseModel):
    """Inspectable request lifecycle; records are replaced, never mutated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: ActionableHumanInterventionRequest
    state: InterventionRequestState
    response: HumanInterventionResponse | None = None
    officer_decision_id: str | None = Field(default=None, pattern=_ID.pattern)
    superseded_by_request_id: str | None = Field(default=None, pattern=_ID.pattern)

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        answered = self.state == InterventionRequestState.ANSWERED
        rejected = self.state == InterventionRequestState.REJECTED
        superseded = self.state == InterventionRequestState.SUPERSEDED
        if answered != (
            self.response is not None
            and self.response.outcome == HumanInterventionResponseOutcome.ACCEPT
            and self.officer_decision_id is not None
        ):
            raise ValueError("answered request requires one accepted response and decision")
        if rejected != (
            self.response is not None
            and self.response.outcome == HumanInterventionResponseOutcome.REJECT
        ):
            raise ValueError("rejected request requires one rejected response")
        if superseded != (self.superseded_by_request_id is not None):
            raise ValueError("superseded request requires its replacement request ID")
        if self.state == InterventionRequestState.PENDING and any(
            (self.response, self.officer_decision_id, self.superseded_by_request_id)
        ):
            raise ValueError("pending request cannot contain a response or disposition")
        if rejected and self.officer_decision_id is not None:
            raise ValueError("rejected response cannot create an officer decision")
        return self


class NetworkPublicationKind(StrEnum):
    GENERATED_BASELINE = "generated-baseline"
    OFFICER_INFORMED_SCENARIO = "officer-informed-scenario"
    REFERENCE_SATN = "reference-satn"


_PUBLICATION_LABELS = {
    NetworkPublicationKind.GENERATED_BASELINE: "Generated clean baseline",
    NetworkPublicationKind.OFFICER_INFORMED_SCENARIO: "Officer-informed scenario",
    NetworkPublicationKind.REFERENCE_SATN: "Formally adopted Reference SATN",
}


class OfficerScenarioCompilation(BaseModel):
    """Immutable result of validating an Officer Decision Ledger."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal["satn-officer-scenario-compilation/v1"] = (
        "satn-officer-scenario-compilation/v1"
    )
    publication_kind: NetworkPublicationKind
    publication_label: str
    baseline_id: str = Field(pattern=_ID.pattern)
    baseline_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    network_json: str
    network_sha256: str = Field(pattern=_SHA256.pattern)
    ledger_fingerprint: str = Field(pattern=_SHA256.pattern)
    applied_decision_ids: tuple[str, ...] = ()
    rejected_decision_ids: tuple[str, ...] = ()
    superseded_decision_ids: tuple[str, ...] = ()
    stale_decision_ids: tuple[str, ...] = ()
    baseline_to_scenario_change_summary: tuple[str, ...]
    scenario_id: str
    scenario_fingerprint: str


class OfficerDecisionApplicationError(ValueError):
    """Fail-closed rejection of a ledger that cannot create a scenario."""

    def __init__(self, reason: str, decision_ids: tuple[str, ...] = ()) -> None:
        self.reason = reason
        self.decision_ids = decision_ids
        super().__init__(reason)


def _decision_summary(decision: OfficerDecision) -> str:
    target = f"{decision.target.kind.value} {decision.target.target_id}"
    if isinstance(decision.action, ClassifyCommunityAction):
        action = f"classified as {decision.action.classification}"
    elif isinstance(decision.action, RetainNetworkGapAction):
        action = "retained as a visible Network Gap"
    elif isinstance(decision.action, SelectAlignmentAction):
        action = "selected as the Alignment Candidate"
    else:
        action = f"marked {decision.action.eligibility}"
    return f"{target}: {action} (decision {decision.decision_id})."


def apply_officer_decision_ledger(
    baseline: CleanSATNBaseline,
    ledger: OfficerDecisionLedger,
    *,
    effective_on: date,
) -> OfficerScenarioCompilation:
    """Validate a ledger and create a separate scenario without baseline mutation."""

    if (
        ledger.baseline_fingerprint != baseline.baseline_fingerprint
        or ledger.evidence_snapshot_fingerprint != baseline.evidence_snapshot_fingerprint
        or ledger.profile_fingerprint != baseline.profile_fingerprint
    ):
        raise OfficerDecisionApplicationError(
            "officer decision ledger is stale for the clean baseline",
            tuple(item.decision_id for item in ledger.decisions),
        )
    known_targets = {item.target.key for item in baseline.targets}
    known_evidence = set(baseline.governed_evidence_ids)
    unknown_targets = tuple(
        item.decision_id for item in ledger.decisions if item.target.key not in known_targets
    )
    if unknown_targets:
        raise OfficerDecisionApplicationError(
            "officer decision references an unknown baseline target",
            unknown_targets,
        )
    unknown_evidence = tuple(
        item.decision_id
        for item in ledger.decisions
        if not set(item.evidence_ids).issubset(known_evidence)
    )
    if unknown_evidence:
        raise OfficerDecisionApplicationError(
            "officer decision references evidence outside the governed snapshot",
            unknown_evidence,
        )
    applied = tuple(
        item
        for item in ledger.decisions
        if item.status == OfficerDecisionStatus.ACTIVE
        and item.effective_from <= effective_on
        and (item.effective_until is None or effective_on <= item.effective_until)
    )
    rejected_ids = tuple(
        item.decision_id
        for item in ledger.decisions
        if item.status == OfficerDecisionStatus.REJECTED
        or (
            item.status == OfficerDecisionStatus.ACTIVE
            and not (
                item.effective_from <= effective_on
                and (item.effective_until is None or effective_on <= item.effective_until)
            )
        )
    )
    superseded_ids = tuple(
        item.decision_id
        for item in ledger.decisions
        if item.status == OfficerDecisionStatus.SUPERSEDED
    )
    applied_ids = tuple(item.decision_id for item in applied)
    if not applied_ids:
        kind = NetworkPublicationKind.GENERATED_BASELINE
        scenario_id = baseline.baseline_id
        scenario_fingerprint = baseline.baseline_fingerprint
        summary = ("No officer decisions applied; network bytes equal the clean baseline.",)
    else:
        kind = NetworkPublicationKind.OFFICER_INFORMED_SCENARIO
        scenario_payload = {
            "baseline_fingerprint": baseline.baseline_fingerprint,
            "ledger_fingerprint": ledger.ledger_fingerprint,
            "effective_on": effective_on.isoformat(),
            "applied_decision_ids": applied_ids,
            "rejected_decision_ids": rejected_ids,
            "superseded_decision_ids": superseded_ids,
        }
        scenario_fingerprint = _fingerprint(scenario_payload)
        scenario_id = f"officer-scenario-{scenario_fingerprint[:20]}"
        summary = tuple(_decision_summary(item) for item in applied)
    return OfficerScenarioCompilation(
        publication_kind=kind,
        publication_label=_PUBLICATION_LABELS[kind],
        baseline_id=baseline.baseline_id,
        baseline_fingerprint=baseline.baseline_fingerprint,
        evidence_snapshot_fingerprint=baseline.evidence_snapshot_fingerprint,
        profile_fingerprint=baseline.profile_fingerprint,
        network_json=baseline.network_json,
        network_sha256=baseline.network_sha256,
        ledger_fingerprint=ledger.ledger_fingerprint,
        applied_decision_ids=applied_ids,
        rejected_decision_ids=rejected_ids,
        superseded_decision_ids=superseded_ids,
        stale_decision_ids=(),
        baseline_to_scenario_change_summary=summary,
        scenario_id=scenario_id,
        scenario_fingerprint=scenario_fingerprint,
    )


def import_human_intervention_response(
    record: HumanInterventionRecord,
    response: HumanInterventionResponse,
    ledger: OfficerDecisionLedger,
) -> tuple[HumanInterventionRecord, OfficerDecisionLedger]:
    """Translate an exact accepted response into the sole human mutation ledger."""

    request = record.request
    if record.state != InterventionRequestState.PENDING:
        raise ValueError("only a current pending intervention request may be answered")
    if (
        response.request_id != request.request_id
        or response.request_fingerprint != request.request_fingerprint
        or response.baseline_fingerprint != request.baseline_fingerprint
        or response.evidence_snapshot_fingerprint
        != request.evidence_snapshot_fingerprint
        or response.profile_fingerprint != request.profile_fingerprint
    ):
        raise ValueError("human intervention response is stale or answers another request")
    if (
        ledger.baseline_fingerprint != request.baseline_fingerprint
        or ledger.evidence_snapshot_fingerprint != request.evidence_snapshot_fingerprint
        or ledger.profile_fingerprint != request.profile_fingerprint
    ):
        raise ValueError("human intervention request is stale for the officer ledger")
    if not set(response.evidence_ids).issubset(request.governed_evidence_ids):
        raise ValueError("human intervention response cites ungoverned evidence")
    offered = next(
        (
            item
            for item in request.offered_actions
            if item.option_id == response.selected_option_id
        ),
        None,
    )
    if offered is None:
        raise ValueError("human intervention response selects an unoffered action")
    if response.outcome == HumanInterventionResponseOutcome.REJECT:
        return (
            HumanInterventionRecord(
                request=request,
                state=InterventionRequestState.REJECTED,
                response=response,
            ),
            ledger,
        )
    decision_id = f"officer-decision-{response.response_fingerprint[:20]}"
    decision = OfficerDecision(
        decision_id=decision_id,
        decision_type=offered.decision_type,
        target=offered.target,
        action=offered.action,
        decision_maker=response.decision_maker,
        decision_maker_role=response.decision_maker_role,
        organisation=response.organisation,
        decision_date=response.response_date,
        rationale=response.rationale,
        evidence_ids=response.evidence_ids,
        source_url=response.source_url,
        effective_from=response.effective_from,
        effective_until=response.effective_until,
        baseline_fingerprint=response.baseline_fingerprint,
        evidence_snapshot_fingerprint=response.evidence_snapshot_fingerprint,
        profile_fingerprint=response.profile_fingerprint,
    )
    updated_ledger = OfficerDecisionLedger(
        baseline_fingerprint=ledger.baseline_fingerprint,
        evidence_snapshot_fingerprint=ledger.evidence_snapshot_fingerprint,
        profile_fingerprint=ledger.profile_fingerprint,
        decisions=(*ledger.decisions, decision),
    )
    return (
        HumanInterventionRecord(
            request=request,
            state=InterventionRequestState.ANSWERED,
            response=response,
            officer_decision_id=decision_id,
        ),
        updated_ledger,
    )


def publication_label(kind: NetworkPublicationKind) -> str:
    """Return the required unambiguous public label for a network authority level."""

    return _PUBLICATION_LABELS[kind]
