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
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from satn.alignment_selection import (
    AlignmentCandidateInput,
    AlignmentCandidateSet,
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
            _json_value(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
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
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
        }
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
    preparation: SpineAccessCandidatePreparationResult | None
    scenario: ScenarioCompilation | None
    compiler_result: PreparedScenarioCompilationResult | None
    officer_decision_input: tuple[PreloadedOfficerDecision, ...] = ()
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
        semantic = self.semantic_payload
        return {
            "contract": self.contract,
            "status": self.status.value,
            "preparation_fingerprint": self.preparation_fingerprint,
            "profile_fingerprint": self.profile_fingerprint,
            "scenario_fingerprint": semantic["fingerprints"]["scenario"],
            "effective_selection_ids": [
                item["candidate_id"] for item in semantic["effective_selections"]
            ],
            "network_gap_ids": [item["gap_id"] for item in semantic["network_gaps"]],
            "evidence_request_ids": [
                item["request_id"] for item in semantic["evidence_requests"]
            ],
            "officer_decision_ids": [
                item["decision_id"] for item in semantic["officer_decisions"]
            ],
            "divergence_ids": [
                item["candidate_set_id"] for item in semantic["divergences"]
            ],
            "target_unavailable_ids": [
                item["decision_id"] for item in semantic["target_unavailable"]
            ],
            "failure_code": self.failure_code,
            "result_fingerprint": self.result_fingerprint,
        }

    @property
    def semantic_payload(self) -> dict[str, object]:
        """One complete JSON-compatible semantic artifact payload."""

        payload = self._fingerprint_payload()
        payload["result_fingerprint"] = self.result_fingerprint
        return payload

    def _fingerprint_payload(self) -> dict[str, object]:
        scenario_payload = (
            self.scenario.model_dump(mode="json") if self.scenario is not None else None
        )
        candidate_sets = (
            scenario_payload.get("candidate_sets", []) if scenario_payload else []
        )
        raw_selections = scenario_payload.get("selections", []) if scenario_payload else []
        selections = [
            {
                **item,
                "candidate_set_id": item["candidate_set"]["candidate_set_id"],
            }
            for item in raw_selections
        ]
        selected_by_set = {
            item["candidate_set_id"]: item.get("selected_candidate_id")
            for item in selections
        }
        unselected: list[dict[str, object]] = []
        admissions: list[dict[str, object]] = []
        for candidate_set in candidate_sets:
            candidate_set_id = candidate_set["candidate_set_id"]
            admissions.extend(
                {
                    "candidate_set_id": candidate_set_id,
                    **admission,
                }
                for admission in candidate_set.get("admissions", [])
            )
            selected_id = selected_by_set.get(candidate_set_id)
            for candidate in candidate_set.get("candidates", []):
                if candidate["candidate_id"] != selected_id:
                    unselected.append(
                        {
                            "candidate_set_id": candidate_set_id,
                            "candidate": candidate,
                        }
                    )
        material_displacements = [
            {
                "candidate_set_id": selection["candidate_set_id"],
                "displacements": selection.get("material_displacements", []),
            }
            for selection in selections
            if selection.get("material_displacements")
        ]
        compiler_metadata = None
        if self.compiler_result is not None:
            compiler_metadata = {
                "contract": self.compiler_result.contract,
                "status": self.compiler_result.status,
                "preparation_fingerprint": self.compiler_result.preparation_fingerprint,
                "missing_inputs": list(self.compiler_result.missing_inputs),
                "review_orchestration_fingerprint": (
                    self.compiler_result.review_orchestration.orchestration_fingerprint
                    if self.compiler_result.review_orchestration is not None
                    else None
                ),
                "diagnostics": _thaw(self.compiler_result.diagnostics),
                "result_fingerprint": self.compiler_result.result_fingerprint,
            }
        return {
            "contract": self.contract,
            "status": self.status.value,
            "preparation_fingerprint": self.preparation_fingerprint,
            "profile_fingerprint": self.profile_fingerprint,
            "preparation": (
                _json_value(self.preparation.canonical_payload())
                if self.preparation is not None
                else None
            ),
            "scenario": scenario_payload,
            "compiler_result": compiler_metadata,
            "candidate_sets": candidate_sets,
            "admissions": admissions,
            "selections": selections,
            "criteria": [item.get("criteria") for item in selections],
            "unselected_candidates": unselected,
            "material_displacements": material_displacements,
            "effective_selections": [_json_value(item) for item in self.effective_selections],
            "network_gaps": [_json_value(item) for item in self.network_gaps],
            "evidence_requests": [_json_value(item) for item in self.evidence_requests],
            "diagnostics": _thaw(self.diagnostics),
            "typed_diagnostics": [_json_value(item) for item in self.typed_diagnostics],
            "officer_decision_input": [
                item.model_dump(mode="json") for item in self.officer_decision_input
            ],
            "officer_decisions": [_json_value(item) for item in self.officer_decisions],
            "divergences": [_json_value(item) for item in self.divergences],
            "target_unavailable": [_json_value(item) for item in self.target_unavailable],
            "failure_code": self.failure_code,
            "fingerprints": {
                "preparation": self.preparation_fingerprint,
                "profile": self.profile_fingerprint,
                "scenario": (
                    self.scenario.scenario_fingerprint if self.scenario is not None else None
                ),
                "compiler_result": (
                    self.compiler_result.result_fingerprint
                    if self.compiler_result is not None
                    else None
                ),
            },
        }


def validate_semantic_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Validate and return one canonical JSON semantic payload.

    Publication/reuse validation calls this function on untrusted artifact
    bytes.  The embedded Scenario Compilation is revalidated by its own typed
    contract before the closed candidate/selection rosters are compared.
    """

    if not isinstance(payload, Mapping):
        raise ValueError("reviewable semantic payload must be an object")
    required = {
        "contract",
        "status",
        "preparation",
        "scenario",
        "compiler_result",
        "candidate_sets",
        "admissions",
        "selections",
        "criteria",
        "unselected_candidates",
        "material_displacements",
        "effective_selections",
        "network_gaps",
        "evidence_requests",
        "diagnostics",
        "typed_diagnostics",
        "officer_decision_input",
        "officer_decisions",
        "divergences",
        "target_unavailable",
        "fingerprints",
        "result_fingerprint",
    }
    optional = {
        "preparation_fingerprint",
        "profile_fingerprint",
        "failure_code",
    }
    if set(payload) != required | optional:
        raise ValueError("reviewable semantic payload has an unexpected closed roster")
    if payload["contract"] != _CONTRACT:
        raise ValueError("reviewable semantic contract mismatch")
    fingerprints = payload["fingerprints"]
    if not isinstance(fingerprints, Mapping):
        raise ValueError("reviewable semantic fingerprints are malformed")
    if (
        payload["preparation_fingerprint"] != fingerprints.get("preparation")
        or payload["profile_fingerprint"] != fingerprints.get("profile")
    ):
        raise ValueError("reviewable semantic input fingerprints mismatch")
    preparation_payload = payload["preparation"]
    if preparation_payload is None:
        if payload["preparation_fingerprint"] is not None:
            raise ValueError("reviewable semantic preparation payload is missing")
    else:
        _validate_preparation_semantics(
            preparation_payload,
            expected_fingerprint=payload["preparation_fingerprint"],
            expected_profile_fingerprint=payload["profile_fingerprint"],
        )
    scenario_payload = payload["scenario"]
    scenario = None
    if scenario_payload is not None:
        if not isinstance(scenario_payload, Mapping):
            raise ValueError("reviewable semantic Scenario Compilation is malformed")
        scenario = ScenarioCompilation.model_validate(dict(scenario_payload))
        if scenario.scenario_fingerprint != payload["fingerprints"].get("scenario"):
            raise ValueError("reviewable semantic Scenario fingerprint mismatch")
        if scenario.profile_fingerprint != payload["profile_fingerprint"]:
            raise ValueError("reviewable semantic Scenario profile mismatch")
    compiler_payload = payload["compiler_result"]
    if compiler_payload is None:
        if fingerprints.get("compiler_result") is not None:
            raise ValueError("reviewable semantic compiler-result fingerprint is orphaned")
    else:
        if not isinstance(compiler_payload, Mapping):
            raise ValueError("reviewable semantic compiler result is malformed")
        compiler_fingerprint_payload = {
            "contract": compiler_payload.get("contract"),
            "status": compiler_payload.get("status"),
            "preparation_fingerprint": compiler_payload.get("preparation_fingerprint"),
            "scenario_fingerprint": (
                scenario.scenario_fingerprint if scenario is not None else None
            ),
            "review_orchestration_fingerprint": compiler_payload.get(
                "review_orchestration_fingerprint"
            ),
            "missing_inputs": compiler_payload.get("missing_inputs"),
            "diagnostics": compiler_payload.get("diagnostics"),
            "reference_satn_created": False,
            "authoritative_network_geometry_mutated": False,
        }
        expected_compiler_fingerprint = _fingerprint(compiler_fingerprint_payload)
        if (
            compiler_payload.get("result_fingerprint") != expected_compiler_fingerprint
            or fingerprints.get("compiler_result") != expected_compiler_fingerprint
        ):
            raise ValueError("reviewable semantic compiler-result fingerprint mismatch")
    if not isinstance(payload["candidate_sets"], list) or not isinstance(
        payload["selections"], list
    ):
        raise ValueError("reviewable semantic candidate roster is malformed")
    expected_sets = (
        [item.model_dump(mode="json") for item in scenario.candidate_sets]
        if scenario
        else []
    )
    raw_selections = (
        [item.model_dump(mode="json") for item in scenario.selections]
        if scenario
        else []
    )
    expected_selections = [
        {
            **item,
            "candidate_set_id": item["candidate_set"]["candidate_set_id"],
        }
        for item in raw_selections
    ]
    if payload["candidate_sets"] != expected_sets:
        raise ValueError("reviewable semantic Candidate Set roster mismatch")
    if payload["selections"] != expected_selections:
        raise ValueError("reviewable semantic selection roster mismatch")
    expected_criteria = [item["criteria"] for item in expected_selections]
    if payload["criteria"] != expected_criteria:
        raise ValueError("reviewable semantic criteria roster mismatch")
    expected_admissions = [
        {"candidate_set_id": candidate_set["candidate_set_id"], **admission}
        for candidate_set in expected_sets
        for admission in candidate_set.get("admissions", [])
    ]
    if payload["admissions"] != expected_admissions:
        raise ValueError("reviewable semantic admission roster mismatch")
    expected_unselected = []
    for selection in expected_selections:
        selected_id = selection.get("selected_candidate_id")
        candidate_set = selection["candidate_set"]
        for candidate in candidate_set.get("candidates", []):
            if candidate["candidate_id"] != selected_id:
                expected_unselected.append(
                    {"candidate_set_id": candidate_set["candidate_set_id"], "candidate": candidate}
                )
    if payload["unselected_candidates"] != expected_unselected:
        raise ValueError("reviewable semantic unselected-candidate roster mismatch")
    expected_displacements = [
        {
            "candidate_set_id": selection["candidate_set_id"],
            "displacements": selection.get("material_displacements", []),
        }
        for selection in expected_selections
        if selection.get("material_displacements")
    ]
    if payload["material_displacements"] != expected_displacements:
        raise ValueError("reviewable semantic displacement roster mismatch")
    candidate_by_set = {
        candidate_set["candidate_set_id"]: candidate_set for candidate_set in expected_sets
    }
    effective_set_ids: set[str] = set()
    for effective in payload["effective_selections"]:
        if not isinstance(effective, Mapping):
            raise ValueError("reviewable semantic effective selection is malformed")
        candidate_set = candidate_by_set.get(effective.get("candidate_set_id"))
        if candidate_set is None:
            raise ValueError("reviewable semantic effective selection target is foreign")
        candidate_set_id = str(effective["candidate_set_id"])
        if candidate_set_id in effective_set_ids:
            raise ValueError("reviewable semantic effective selection target is duplicated")
        effective_set_ids.add(candidate_set_id)
        candidate_by_id = {
            item["candidate_id"]: item for item in candidate_set.get("candidates", [])
        }
        candidate_id = effective.get("candidate_id")
        if (
            candidate_id not in candidate_by_id
            or effective.get("candidate") != candidate_by_id[candidate_id]
        ):
            raise ValueError("reviewable semantic effective candidate mismatch")
        compiler_id = effective.get("compiler_candidate_id")
        if compiler_id not in candidate_by_id:
            raise ValueError("reviewable semantic compiler candidate is foreign")
    expected_effective_set_ids = {
        item.candidate_set_id
        for item in (scenario.selections if scenario is not None else ())
        if item.selected_candidate_id is not None
    }
    if effective_set_ids != expected_effective_set_ids:
        raise ValueError("reviewable semantic effective selection roster is incomplete")
    _validate_gap_semantics(payload, scenario, preparation_payload)
    _validate_officer_semantics(payload, candidate_by_set)
    _validate_evidence_request_semantics(payload, scenario)
    if not isinstance(payload["result_fingerprint"], str):
        raise ValueError("reviewable semantic result fingerprint is malformed")
    base = dict(payload)
    base.pop("result_fingerprint")
    if _fingerprint(base) != payload["result_fingerprint"]:
        raise ValueError("reviewable semantic result fingerprint mismatch")
    return dict(payload)


def _validate_preparation_semantics(
    value: object,
    *,
    expected_fingerprint: object,
    expected_profile_fingerprint: object,
) -> None:
    required = {
        "contract",
        "profile_fingerprint",
        "status",
        "prepared_spine_access_connections",
        "connection_roster",
        "generation_issues",
        "missing_inputs",
        "evidence_lineage",
        "evidence_fingerprints",
        "diagnostics",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("reviewable semantic preparation has an unexpected closed roster")
    if (
        value["contract"] != "satn-spine-access-candidate-preparation/v1"
        or value["profile_fingerprint"] != expected_profile_fingerprint
        or not isinstance(expected_fingerprint, str)
        or _fingerprint(value) != expected_fingerprint
    ):
        raise ValueError("reviewable semantic preparation fingerprint mismatch")
    if value["status"] not in {"prepared", "incomplete"}:
        raise ValueError("reviewable semantic preparation status is invalid")
    prepared = value["prepared_spine_access_connections"]
    roster = value["connection_roster"]
    issues = value["generation_issues"]
    diagnostics = value["diagnostics"]
    if not all(isinstance(item, list) for item in (prepared, roster, issues)) or not isinstance(
        diagnostics, Mapping
    ):
        raise ValueError("reviewable semantic preparation rosters are malformed")

    connection_fields = {
        "access_connection_id",
        "candidate_set",
        "root_spine_id",
        "strategic_source_id",
        "strategic_evidence_id",
        "strategic_provenance",
        "obligation_kind",
        "parent_role",
        "community_id",
        "place_id",
        "parent_place_id",
        "candidate_generation_rationales",
        "candidate_records",
    }
    candidate_record_fields = {
        "candidate",
        "candidate_id",
        "route_role",
        "source_class",
        "topology_state",
        "endpoints",
        "served_network_place_ids",
        "served_access_obligation_ids",
        "served_strategic_destination_ids",
        "directness_m",
        "geometry_fingerprint",
        "routing_edge_ids",
        "reverse_routing_edge_ids",
        "generation_rationale",
        "current_asset_share",
        "current_asset_evidence",
        "official_b_road_share",
        "official_b_road_evidence",
        "connection",
        "strategic_spine",
        "preparation_disposition",
        "rejection_reason",
        "retained_candidate_id",
        "review_required",
    }
    prepared_ids: set[str] = set()
    for raw in prepared:
        if not isinstance(raw, Mapping) or set(raw) != connection_fields:
            raise ValueError("reviewable semantic prepared connection is malformed")
        access_connection_id = raw["access_connection_id"]
        if not isinstance(access_connection_id, str) or access_connection_id in prepared_ids:
            raise ValueError("reviewable semantic prepared connection identity is invalid")
        prepared_ids.add(access_connection_id)
        candidate_set = AlignmentCandidateSet.model_validate(raw["candidate_set"])
        if candidate_set.profile_fingerprint != expected_profile_fingerprint:
            raise ValueError("reviewable semantic prepared Candidate Set profile is stale")
        if not isinstance(raw["candidate_records"], list) or not isinstance(
            raw["candidate_generation_rationales"], list
        ):
            raise ValueError("reviewable semantic prepared candidate audit is malformed")
        for record in raw["candidate_records"]:
            if not isinstance(record, Mapping) or set(record) != candidate_record_fields:
                raise ValueError("reviewable semantic prepared candidate record is malformed")
            candidate_payload = record["candidate"]
            if not isinstance(candidate_payload, Mapping):
                raise ValueError("reviewable semantic prepared candidate payload is malformed")
            candidate = AlignmentCandidateInput.model_validate(
                {
                    key: item
                    for key, item in candidate_payload.items()
                    if key
                    not in {
                        "geometry_fingerprint",
                        "geometry_equivalence_fingerprint",
                    }
                }
            )
            if (
                record["candidate_id"] != candidate.candidate_id
                or record["source_class"] != candidate.source_class.value
                or record["topology_state"] != candidate.topology_state.value
                or record["endpoints"] != list(candidate.endpoints)
                or record["served_network_place_ids"]
                != list(candidate.served_network_place_ids)
                or record["served_access_obligation_ids"]
                != list(candidate.served_access_obligation_ids)
                or record["served_strategic_destination_ids"]
                != list(candidate.served_strategic_destination_ids)
                or record["directness_m"] != candidate.directness_m
                or record["geometry_fingerprint"] != candidate.geometry.fingerprint
            ):
                raise ValueError("reviewable semantic prepared candidate audit is stale")

    roster_fields = {
        "access_connection_id",
        "obligation_kind",
        "parent_role",
        "community_id",
        "place_id",
        "parent_place_id",
        "disposition",
        "reason",
    }
    roster_by_id: dict[str, Mapping[str, object]] = {}
    valid_dispositions = {
        "prepared-candidate-set",
        "prepared-candidate-set-gap",
        "unresolved-gap",
        "out-of-scope-direct-strategic-spine",
    }
    for raw in roster:
        if not isinstance(raw, Mapping) or set(raw) != roster_fields:
            raise ValueError("reviewable semantic preparation roster row is malformed")
        connection_id = raw["access_connection_id"]
        if raw["disposition"] not in valid_dispositions:
            raise ValueError("reviewable semantic preparation disposition is invalid")
        if not isinstance(connection_id, str) or connection_id in roster_by_id:
            raise ValueError("reviewable semantic preparation roster identity is invalid")
        roster_by_id[connection_id] = raw
    roster_prepared_ids = {
        connection_id
        for connection_id, item in roster_by_id.items()
        if isinstance(item["disposition"], str)
        and str(item["disposition"]).startswith("prepared-")
    }
    if prepared_ids != roster_prepared_ids:
        raise ValueError("prepared connection sets do not exactly match the roster")

    issue_fields = {
        "access_connection_id",
        "reason",
        "detail",
        "route_role",
        "candidate_id",
        "retained_candidate_id",
        "source_class",
    }
    issue_roster: list[Mapping[str, object]] = []
    for raw in issues:
        if (
            not isinstance(raw, Mapping)
            or set(raw) != issue_fields
            or raw.get("access_connection_id") not in roster_by_id
        ):
            raise ValueError("generation issue names no expected roster connection")
        issue_roster.append(raw)
    for connection_id, raw in roster_by_id.items():
        matching = [
            item for item in issue_roster if item["access_connection_id"] == connection_id
        ]
        if raw["disposition"] == "unresolved-gap" and not matching:
            raise ValueError("unresolved roster connection requires an explicit issue")
        if raw["disposition"] == "out-of-scope-direct-strategic-spine" and (
            raw["parent_role"] != "strategic-spine"
            or not any(
                item["reason"] == "out-of-scope-direct-strategic-spine-attachment"
                for item in matching
            )
        ):
            raise ValueError("direct-to-spine roster disposition lacks exact evidence")

    expected_diagnostics = {
        "expected_connection_roster_count": len(roster_by_id),
        "prepared_connection_count": len(roster_prepared_ids),
        "out_of_scope_connection_count": sum(
            item["disposition"] == "out-of-scope-direct-strategic-spine"
            for item in roster_by_id.values()
        ),
        "unresolved_connection_count": sum(
            item["disposition"] == "unresolved-gap"
            for item in roster_by_id.values()
        ),
    }
    if any(diagnostics.get(key) != number for key, number in expected_diagnostics.items()):
        raise ValueError("preparation roster diagnostics are stale")

    if value["status"] == "prepared":
        expected_evidence = _preparation_lineage_fingerprints(value["evidence_lineage"])
        if (
            not expected_evidence
            or value["evidence_fingerprints"] != expected_evidence
            or any(not _is_sha256(item) for item in expected_evidence)
        ):
            raise ValueError("prepared evidence fingerprints are empty, foreign or stale")


def _preparation_lineage_fingerprints(value: object) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    fingerprints: set[str] = set()
    population = value.get("population")
    education = value.get("education")
    if not isinstance(population, Mapping) or not isinstance(education, Mapping):
        return []
    for key in ("source_content_sha256", "frame_content_sha256"):
        item = population.get(key)
        if isinstance(item, str):
            fingerprints.add(item)
    artifacts = population.get("artifact_lineage")
    if isinstance(artifacts, list):
        for item in artifacts:
            if isinstance(item, Mapping) and isinstance(item.get("content_sha256"), str):
                fingerprints.add(str(item["content_sha256"]))
    governed_source = education.get("governed_source_fingerprint")
    if isinstance(governed_source, str):
        fingerprints.add(governed_source)
    for key in ("school_register_lineage", "admissions_lineage"):
        item = education.get(key)
        if isinstance(item, Mapping) and isinstance(item.get("content_sha256"), str):
            fingerprints.add(str(item["content_sha256"]))
    return sorted(fingerprints)


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def _validate_gap_semantics(
    payload: Mapping[str, object],
    scenario: ScenarioCompilation | None,
    preparation: Mapping[str, object] | None,
) -> None:
    raw_gaps = payload["network_gaps"]
    if not isinstance(raw_gaps, list):
        raise ValueError("reviewable semantic gap roster is malformed")
    gap_fields = {field.name for field in fields(ReviewableNetworkGap)}
    by_id: dict[str, Mapping[str, object]] = {}
    for raw in raw_gaps:
        if not isinstance(raw, Mapping) or set(raw) != gap_fields:
            raise ValueError("reviewable semantic gap is malformed")
        gap_id = raw.get("gap_id")
        endpoints = raw.get("endpoints")
        if (
            not isinstance(gap_id, str)
            or gap_id in by_id
            or not isinstance(endpoints, list)
            or not endpoints
            or any(not isinstance(item, str) or not item for item in endpoints)
            or raw.get("display_state") != ReviewableDisplayState.UNRESOLVED_GAP.value
        ):
            raise ValueError("reviewable semantic gap identity is malformed")
        by_id[gap_id] = raw

    expected_selection_gaps = {
        item.candidate_set_id: _json_value(_gap_from_selection(item))
        for item in (scenario.selections if scenario is not None else ())
        if item.selected_candidate_id is None
    }
    roster_by_id = {
        item["access_connection_id"]: item
        for item in (
            preparation.get("connection_roster", [])
            if isinstance(preparation, Mapping)
            else []
        )
        if isinstance(item, Mapping)
        and isinstance(item.get("access_connection_id"), str)
    }
    seen_selection_gaps: set[str] = set()
    for raw in raw_gaps:
        candidate_set_id = raw.get("candidate_set_id")
        if candidate_set_id is not None:
            expected = expected_selection_gaps.get(candidate_set_id)
            if expected is None or raw != expected:
                raise ValueError("reviewable semantic selection gap mismatch")
            seen_selection_gaps.add(str(candidate_set_id))
            continue
        connection_id = raw.get("connection_id")
        reason = raw.get("reason")
        if not isinstance(connection_id, str) or not isinstance(reason, str):
            raise ValueError("reviewable semantic unresolved gap lacks governed identity")
        roster = roster_by_id.get(connection_id)
        expected_endpoints = (
            sorted(
                {
                    value
                    for value in (roster.get("place_id"), roster.get("parent_place_id"))
                    if isinstance(value, str) and value
                }
            )
            if roster is not None
            else []
        )
        if (
            roster is None
            or raw["endpoints"] != expected_endpoints
            or (
                isinstance(roster.get("reason"), str)
                and roster["reason"]
                and reason != roster["reason"]
            )
        ):
            raise ValueError("reviewable semantic unresolved gap is foreign to preparation")
        expected_id = "network-gap-" + _fingerprint(
            {
                "connection_id": connection_id,
                "endpoints": tuple(raw["endpoints"]),
                "reason": reason,
            }
        )[:20]
        if raw.get("gap_id") != expected_id:
            raise ValueError("reviewable semantic unresolved gap fingerprint mismatch")
    if seen_selection_gaps != set(expected_selection_gaps):
        raise ValueError("reviewable semantic selected-gap roster is incomplete")
    unresolved_roster_ids = {
        connection_id
        for connection_id, item in roster_by_id.items()
        if item.get("disposition") == "unresolved-gap"
    }
    published_unresolved_ids = {
        str(item["connection_id"])
        for item in raw_gaps
        if item.get("candidate_set_id") is None
    }
    if not unresolved_roster_ids.issubset(published_unresolved_ids):
        raise ValueError("reviewable semantic unresolved preparation gap is missing")


def _validate_officer_semantics(
    payload: Mapping[str, object],
    candidate_by_set: Mapping[str, Mapping[str, object]],
) -> None:
    raw_input = payload["officer_decision_input"]
    raw_decisions = payload["officer_decisions"]
    raw_unavailable = payload["target_unavailable"]
    raw_divergences = payload["divergences"]
    raw_effective = payload["effective_selections"]
    if not all(
        isinstance(value, list)
        for value in (
            raw_input,
            raw_decisions,
            raw_unavailable,
            raw_divergences,
            raw_effective,
        )
    ):
        raise ValueError("reviewable semantic officer roster is malformed")

    governed_input: dict[str, str] = {}
    for raw in raw_input:
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"target_id", "route_id"}
            or not isinstance(raw.get("target_id"), str)
            or not isinstance(raw.get("route_id"), str)
            or not raw["target_id"]
            or not raw["route_id"]
            or raw["target_id"] in governed_input
        ):
            raise ValueError("reviewable semantic governed officer input is malformed")
        governed_input[str(raw["target_id"])] = str(raw["route_id"])

    decision_fields = {field.name for field in fields(OfficerDecisionRecord)}
    decisions: dict[str, Mapping[str, object]] = {}
    for raw in raw_decisions:
        if not isinstance(raw, Mapping) or set(raw) != decision_fields:
            raise ValueError("reviewable semantic officer decision is malformed")
        target_id = raw.get("target_id")
        route_id = raw.get("route_id")
        decision_id = raw.get("decision_id")
        status = raw.get("status")
        if (
            not isinstance(target_id, str)
            or not isinstance(route_id, str)
            or decision_id != _decision_identity(target_id, route_id)
            or decision_id in decisions
            or status
            not in {
                OfficerDecisionApplicationStatus.APPLIED.value,
                OfficerDecisionApplicationStatus.TARGET_UNAVAILABLE.value,
            }
        ):
            raise ValueError("reviewable semantic officer decision identity mismatch")
        decisions[str(decision_id)] = raw

    if {
        str(item["target_id"]): str(item["route_id"])
        for item in decisions.values()
    } != governed_input:
        raise ValueError("reviewable semantic officer output is not bound to governed input")

    expected_unavailable = sorted(
        (
            dict(item)
            for item in decisions.values()
            if item["status"]
            == OfficerDecisionApplicationStatus.TARGET_UNAVAILABLE.value
        ),
        key=lambda item: item["decision_id"],
    )
    if raw_unavailable != expected_unavailable:
        raise ValueError("reviewable semantic unavailable officer roster mismatch")

    effective_by_set = {
        item["candidate_set_id"]: item
        for item in raw_effective
        if isinstance(item, Mapping)
    }
    expected_divergences: list[dict[str, object]] = []
    used_applied: set[str] = set()
    for candidate_set_id, effective in effective_by_set.items():
        decision_id = effective.get("officer_decision_id")
        if decision_id is None:
            continue
        decision = decisions.get(str(decision_id))
        if (
            decision is None
            or decision.get("status") != OfficerDecisionApplicationStatus.APPLIED.value
            or decision.get("candidate_set_id") != candidate_set_id
            or decision.get("candidate_id") != effective.get("candidate_id")
            or decision.get("route_id") != effective.get("candidate_id")
        ):
            raise ValueError("reviewable semantic applied officer decision mismatch")
        used_applied.add(str(decision_id))
        if effective.get("candidate_id") != effective.get("compiler_candidate_id"):
            expected_divergences.append(
                {
                    "candidate_set_id": candidate_set_id,
                    "target_id": decision["target_id"],
                    "officer_candidate_id": effective["candidate_id"],
                    "compiler_candidate_id": effective["compiler_candidate_id"],
                    "officer_decision_id": decision_id,
                }
            )

    all_applied = {
        decision_id
        for decision_id, item in decisions.items()
        if item["status"] == OfficerDecisionApplicationStatus.APPLIED.value
    }
    if used_applied != all_applied:
        raise ValueError("reviewable semantic applied officer decision is not effective")
    for decision in decisions.values():
        candidate_set_id = decision.get("candidate_set_id")
        candidate_id = decision.get("candidate_id")
        if candidate_set_id is None:
            if candidate_id is not None:
                raise ValueError("reviewable semantic unavailable officer candidate is orphaned")
            continue
        candidate_set = candidate_by_set.get(str(candidate_set_id))
        if candidate_set is None:
            raise ValueError("reviewable semantic officer Candidate Set is foreign")
        candidate_ids = {
            item["candidate_id"] for item in candidate_set.get("candidates", [])
        }
        if candidate_id is not None and candidate_id not in candidate_ids:
            raise ValueError("reviewable semantic officer candidate is foreign")

    expected_divergences.sort(key=lambda item: str(item["candidate_set_id"]))
    if raw_divergences != expected_divergences:
        raise ValueError("reviewable semantic officer divergence roster mismatch")


def _validate_evidence_request_semantics(
    payload: Mapping[str, object],
    scenario: ScenarioCompilation | None,
) -> None:
    raw_requests = payload["evidence_requests"]
    if not isinstance(raw_requests, list):
        raise ValueError("reviewable semantic evidence-request roster is malformed")
    request_fields = {field.name for field in fields(ReviewableEvidenceRequest)}
    requests: dict[str, Mapping[str, object]] = {}
    for raw in raw_requests:
        if not isinstance(raw, Mapping) or set(raw) != request_fields:
            raise ValueError("reviewable semantic evidence request is malformed")
        try:
            request = ReviewableEvidenceRequest(
                request_id=str(raw["request_id"]),
                kind=ReviewableEvidenceRequestKind(str(raw["kind"])),
                reason=str(raw["reason"]),
                candidate_set_id=raw["candidate_set_id"],
                target_id=raw["target_id"],
                evidence_ids=tuple(raw["evidence_ids"]),
                fingerprint=str(raw["fingerprint"]),
            )
        except (TypeError, ValueError) as error:
            raise ValueError("reviewable semantic evidence request is invalid") from error
        if request.request_id in requests:
            raise ValueError("reviewable semantic evidence request is duplicated")
        requests[request.request_id] = raw

    expected: list[ReviewableEvidenceRequest] = []
    for gap in payload["network_gaps"]:
        expected.append(
            ReviewableEvidenceRequest(
                request_id="network-gap-" + str(gap["gap_id"]),
                kind=ReviewableEvidenceRequestKind.NETWORK_GAP,
                reason=str(gap["reason"]),
                target_id=gap["connection_id"],
            )
        )
    for decision in payload["target_unavailable"]:
        expected.append(
            ReviewableEvidenceRequest(
                request_id="officer-target-" + str(decision["decision_id"]),
                kind=ReviewableEvidenceRequestKind.OFFICER_TARGET,
                reason=(
                    "officer-decision-target-unavailable"
                    if decision["candidate_set_id"] is None
                    else "officer-decision-route-unavailable"
                ),
                candidate_set_id=decision["candidate_set_id"],
                target_id=str(decision["target_id"]),
            )
        )
    if scenario is not None:
        for selection in scenario.selections:
            for trigger in selection.ambiguity_triggers:
                expected.append(
                    ReviewableEvidenceRequest(
                        request_id="selection-review-"
                        + _fingerprint((selection.candidate_set_id, trigger.value))[:20],
                        kind=ReviewableEvidenceRequestKind.SELECTION_REVIEW,
                        reason=trigger.value,
                        candidate_set_id=selection.candidate_set_id,
                    )
                )
        compiler_payload = payload["compiler_result"]
        diagnostics = (
            compiler_payload.get("diagnostics", {})
            if isinstance(compiler_payload, Mapping)
            else {}
        )
        traffic = (
            diagnostics.get("traffic_diagnostics", [])
            if isinstance(diagnostics, Mapping)
            else []
        )
        candidate_set_by_candidate_id = {
            candidate.candidate_id: candidate_set.candidate_set_id
            for candidate_set in scenario.candidate_sets
            for candidate in candidate_set.candidates
        }
        for diagnostic in traffic:
            if not isinstance(diagnostic, Mapping):
                continue
            status = str(diagnostic.get("traffic_status", "unknown"))
            if status in {"matched", "fresh", "sampled"}:
                continue
            diagnostic_id = str(diagnostic.get("diagnostic_id", "traffic-unknown"))
            candidate_id = diagnostic.get("candidate_id")
            evidence_ids = diagnostic.get("evidence_ids", ())
            expected.append(
                ReviewableEvidenceRequest(
                    request_id="evidence-request-" + _fingerprint(diagnostic)[:20],
                    kind=ReviewableEvidenceRequestKind.OPTIONAL_EVIDENCE,
                    reason=diagnostic_id,
                    candidate_set_id=(
                        candidate_set_by_candidate_id.get(candidate_id)
                        if isinstance(candidate_id, str)
                        else None
                    ),
                    target_id=(candidate_id if isinstance(candidate_id, str) else None),
                    evidence_ids=(
                        tuple(str(value) for value in evidence_ids)
                        if isinstance(evidence_ids, (tuple, list))
                        else ()
                    ),
                )
            )
    if payload["status"] == ReviewableNetworkStatus.TERMINAL_FAILURE.value:
        expected.append(
            ReviewableEvidenceRequest(
                request_id="mandatory-lineage-"
                + _fingerprint(payload["diagnostics"])[:20],
                kind=ReviewableEvidenceRequestKind.MANDATORY_LINEAGE,
                reason=str(payload["failure_code"]),
            )
        )
    expected_payload = [
        _json_value(item)
        for item in sorted(
            {item.fingerprint: item for item in expected}.values(),
            key=lambda item: item.request_id,
        )
    ]
    if raw_requests != expected_payload:
        raise ValueError("reviewable semantic evidence-request roster mismatch")


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
    officer_decisions: Sequence[PreloadedOfficerDecision] = (),
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
    unavailable = tuple(
        OfficerDecisionRecord(
            decision_id=_decision_identity(item.target_id, item.route_id),
            target_id=item.target_id,
            route_id=item.route_id,
            status=OfficerDecisionApplicationStatus.TARGET_UNAVAILABLE,
        )
        for item in officer_decisions
    )
    requests = tuple(
        ReviewableEvidenceRequest(
            request_id="officer-target-" + item.decision_id,
            kind=ReviewableEvidenceRequestKind.OFFICER_TARGET,
            reason="officer-decision-target-unavailable",
            target_id=item.target_id,
        )
        for item in unavailable
    )
    return ReviewableNetwork(
        contract=_CONTRACT,
        status=ReviewableNetworkStatus.TERMINAL_FAILURE,
        preparation_fingerprint=(
            preparation.preparation_fingerprint if preparation is not None else None
        ),
        profile_fingerprint=(preparation.profile_fingerprint if preparation is not None else None),
        preparation=preparation,
        scenario=None,
        compiler_result=compiler_result,
        officer_decision_input=tuple(officer_decisions),
        diagnostics=diagnostics,
        evidence_requests=(
            ReviewableEvidenceRequest(
                request_id="mandatory-lineage-" + _fingerprint(diagnostics)[:20],
                kind=ReviewableEvidenceRequestKind.MANDATORY_LINEAGE,
                reason=code,
            ),
            *requests,
        ),
        officer_decisions=unavailable,
        target_unavailable=unavailable,
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


def canonical_officer_decisions(
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


def terminal_reviewable_network_for_governed_error(
    preparation: SpineAccessCandidatePreparationResult | None,
    officer_decisions: Sequence[PreloadedOfficerDecision],
    *,
    error: ValueError,
) -> ReviewableNetwork:
    """Convert only a recognised governed-input failure to a terminal result."""

    if not _is_known_governed_error(error):
        raise error
    return _terminal_result(
        preparation,
        None,
        officer_decisions=canonical_officer_decisions(officer_decisions),
        code="mandatory-lineage-invalid",
        detail=str(error),
    )


def terminal_reviewable_network_for_governed_evidence(
    preparation: SpineAccessCandidatePreparationResult | None,
    officer_decisions: Sequence[PreloadedOfficerDecision],
    *,
    detail: str,
) -> ReviewableNetwork:
    """Represent a typed governed-evidence loader failure without publication."""

    return _terminal_result(
        preparation,
        None,
        officer_decisions=canonical_officer_decisions(officer_decisions),
        code="mandatory-evidence-invalid",
        detail=detail,
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
    officer_decisions = canonical_officer_decisions(officer_decisions)

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
            officer_decisions=officer_decisions,
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
                unavailable = tuple(
                    OfficerDecisionRecord(
                        decision_id=_decision_identity(item.target_id, item.route_id),
                        target_id=item.target_id,
                        route_id=item.route_id,
                        status=OfficerDecisionApplicationStatus.TARGET_UNAVAILABLE,
                    )
                    for item in officer_decisions
                )
                return ReviewableNetwork(
                    contract=_CONTRACT,
                    status=ReviewableNetworkStatus.COMPLETE,
                    preparation_fingerprint=preparation.preparation_fingerprint,
                    profile_fingerprint=preparation.profile_fingerprint,
                    preparation=preparation,
                    scenario=None,
                    compiler_result=compiler_result,
                    officer_decision_input=tuple(officer_decisions),
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
                    )
                    + tuple(
                        ReviewableEvidenceRequest(
                            request_id="officer-target-" + item.decision_id,
                            kind=ReviewableEvidenceRequestKind.OFFICER_TARGET,
                            reason="officer-decision-target-unavailable",
                            target_id=item.target_id,
                        )
                        for item in unavailable
                    ),
                    officer_decisions=unavailable,
                    target_unavailable=unavailable,
                )
        return _terminal_result(
            preparation,
            compiler_result,
            code="mandatory-input-incomplete",
            detail="; ".join(compiler_result.missing_inputs) or "scenario was not compiled",
            officer_decisions=officer_decisions,
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
        preparation=preparation,
        scenario=scenario,
        compiler_result=compiler_result,
        officer_decision_input=tuple(officer_decisions),
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
    "canonical_officer_decisions",
    "compile_reviewable_network",
    "terminal_reviewable_network_for_governed_error",
    "terminal_reviewable_network_for_governed_evidence",
    "validate_semantic_payload",
]
