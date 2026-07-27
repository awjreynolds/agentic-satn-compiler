"""Build an immutable replay plan for one human-adopted Reference SATN.

This module only binds an exact ``ReferenceSATNSelection`` to the exact
Spine Access candidate preparation from which its Scenario Compilation was
derived.  It does not mutate a compiled network, invoke an agent, or publish.

The later whole-network application boundary must open its actual Area
Definition, snapshot manifest, and governed inputs, derive their content
identities, and validate them before applying or publishing.  This pre-replay
plan cannot accept caller declarations for inputs it has not opened.

SHA-256 values are deterministic content identities for staleness and replay.
They do not authenticate the local operator or any external principal.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satn.alignment_selection import (
    AlignmentCandidateInput,
    ReferenceSATNSelection,
    ResolvedPreferredStrategicAlignment,
)
from satn.spine_access_candidate_preparation import (
    PreparedSpineAccessConnection,
    SpineAccessCandidatePreparationResult,
)

REFERENCE_APPLICATION_CONTRACT = "satn-reference-application-plan/v1"
_PREPARATION_CONTRACT = "satn-spine-access-candidate-preparation/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONNECTION_ID = re.compile(r"^connection-[0-9a-f]{20}$")
_CANDIDATE_SET_ID = re.compile(r"^candidate-set-[0-9a-f]{20}$")
_CANDIDATE_ID = re.compile(r"^candidate-[0-9a-f]{20}$")


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


class ReferenceApplicationCandidateBinding(BaseModel):
    """One exact replacement candidate for one logical Community Connection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    logical_connection_id: str = Field(pattern=_CONNECTION_ID.pattern)
    candidate_set_id: str = Field(pattern=_CANDIDATE_SET_ID.pattern)
    candidate_set_fingerprint: str = Field(pattern=_SHA256.pattern)
    resolution_fingerprint: str = Field(pattern=_SHA256.pattern)
    selected_candidate_id: str = Field(pattern=_CANDIDATE_ID.pattern)
    source_access_connection_id: str = Field(min_length=1)
    route_role: str = Field(min_length=1)
    routing_edge_ids: tuple[str, ...] = Field(min_length=1)
    reverse_routing_edge_ids: tuple[str, ...] = Field(min_length=1)
    geometry_fingerprint: str = Field(pattern=_SHA256.pattern)
    candidate_input_fingerprint: str = Field(pattern=_SHA256.pattern)
    candidate_evidence_fingerprints: tuple[str, ...] = Field(min_length=1)
    prepared_candidate_record_fingerprint: str = Field(pattern=_SHA256.pattern)
    prepared_connection_fingerprint: str = Field(pattern=_SHA256.pattern)
    binding_fingerprint: str = ""

    @field_validator("source_access_connection_id", "route_role")
    @classmethod
    def canonical_text(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("Reference application text fields must be canonical")
        return value

    @field_validator("routing_edge_ids", "reverse_routing_edge_ids")
    @classmethod
    def canonical_route_edges(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or item.strip() != item for item in value):
            raise ValueError("routing edge identifiers must be canonical")
        return value

    @field_validator("candidate_evidence_fingerprints")
    @classmethod
    def canonical_evidence_fingerprints(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if (
            tuple(sorted(value)) != value
            or len(set(value)) != len(value)
            or any(_SHA256.fullmatch(item) is None for item in value)
        ):
            raise ValueError(
                "candidate evidence fingerprints must be unique, sorted SHA-256 values"
            )
        return value

    @model_validator(mode="after")
    def bind_candidate(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"binding_fingerprint"})
        expected = _fingerprint(payload)
        if self.binding_fingerprint and self.binding_fingerprint != expected:
            raise ValueError("Reference application candidate binding is stale")
        object.__setattr__(self, "binding_fingerprint", expected)
        return self


class ReferenceApplicationPlan(BaseModel):
    """Replay-only plan; it has no geometry-mutation or publication authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal["satn-reference-application-plan/v1"] = REFERENCE_APPLICATION_CONTRACT
    reference_selection_fingerprint: str = Field(pattern=_SHA256.pattern)
    reference_decision_fingerprint: str = Field(pattern=_SHA256.pattern)
    preparation_fingerprint: str = Field(pattern=_SHA256.pattern)
    preparation_evidence_fingerprints: tuple[str, ...] = Field(min_length=1)
    scenario_fingerprint: str = Field(pattern=_SHA256.pattern)
    scenario_area_fingerprint: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    selection_run_fingerprint: str = Field(pattern=_SHA256.pattern)
    candidate_bindings: tuple[ReferenceApplicationCandidateBinding, ...] = Field(min_length=1)
    replay_directive: Literal["recompile-whole-network-on-ledger-change"] = (
        "recompile-whole-network-on-ledger-change"
    )
    authoritative_network_geometry_mutated: Literal[False] = False
    publication_created: Literal[False] = False
    plan_fingerprint: str = ""

    @field_validator("preparation_evidence_fingerprints")
    @classmethod
    def canonical_preparation_evidence(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if (
            tuple(sorted(value)) != value
            or len(set(value)) != len(value)
            or any(_SHA256.fullmatch(item) is None for item in value)
        ):
            raise ValueError(
                "preparation evidence fingerprints must be unique, sorted SHA-256 values"
            )
        return value

    @field_validator("candidate_bindings")
    @classmethod
    def canonical_bindings(
        cls,
        value: tuple[ReferenceApplicationCandidateBinding, ...],
    ) -> tuple[ReferenceApplicationCandidateBinding, ...]:
        logical_ids = tuple(item.logical_connection_id for item in value)
        candidate_ids = tuple(item.selected_candidate_id for item in value)
        set_ids = tuple(item.candidate_set_id for item in value)
        if logical_ids != tuple(sorted(logical_ids)):
            raise ValueError("Reference application bindings must be canonically ordered")
        if (
            len(set(logical_ids)) != len(logical_ids)
            or len(set(candidate_ids)) != len(candidate_ids)
            or len(set(set_ids)) != len(set_ids)
        ):
            raise ValueError(
                "Reference application bindings must uniquely consume connections, "
                "Candidate Sets and selected candidates"
            )
        return value

    @model_validator(mode="after")
    def bind_plan(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"plan_fingerprint"})
        expected = _fingerprint(payload)
        if self.plan_fingerprint and self.plan_fingerprint != expected:
            raise ValueError("Reference application plan fingerprint is stale")
        object.__setattr__(self, "plan_fingerprint", expected)
        return self


def build_reference_application_plan(
    reference: ReferenceSATNSelection,
    preparation: SpineAccessCandidatePreparationResult,
) -> ReferenceApplicationPlan:
    """Bind one exact adopted Reference to its exact prepared replacement routes."""

    reference = ReferenceSATNSelection.model_validate(reference.model_dump(mode="python"))
    _validate_reference_outcome(reference)
    prepared_by_set_id = _validate_preparation(reference, preparation)
    bindings = tuple(
        sorted(
            (
                _candidate_binding(
                    resolved,
                    prepared_by_set_id[resolved.candidate_set_id],
                )
                for resolved in reference.scenario.resolved_selections
            ),
            key=lambda item: item.logical_connection_id,
        )
    )
    _validate_preparation_lineage(reference, preparation)
    expected_set_ids = {item.candidate_set_id for item in reference.scenario.candidate_sets}
    consumed_set_ids = {item.candidate_set_id for item in bindings}
    if consumed_set_ids != expected_set_ids:
        missing = sorted(expected_set_ids - consumed_set_ids)
        foreign = sorted(consumed_set_ids - expected_set_ids)
        raise ValueError(
            "Reference application did not consume the exact Scenario Candidate Sets "
            f"(missing={missing}, foreign={foreign})"
        )

    scenario = reference.scenario
    return ReferenceApplicationPlan(
        reference_selection_fingerprint=reference.reference_selection_fingerprint,
        reference_decision_fingerprint=reference.governed_decision.decision_fingerprint,
        preparation_fingerprint=preparation.preparation_fingerprint,
        preparation_evidence_fingerprints=tuple(sorted(preparation.evidence_fingerprints)),
        scenario_fingerprint=scenario.scenario_fingerprint,
        scenario_area_fingerprint=scenario.area_fingerprint,
        profile_fingerprint=scenario.profile_fingerprint,
        evidence_snapshot_fingerprint=(scenario.evidence_snapshot.snapshot_fingerprint),
        selection_run_fingerprint=scenario.decision_record.record_fingerprint,
        candidate_bindings=bindings,
    )


def _validate_reference_outcome(reference: ReferenceSATNSelection) -> None:
    scenario = reference.scenario
    if reference.network_gap_ids or scenario.network_gaps:
        raise ValueError("Reference application cannot replace geometry for a Network Gap")
    if (
        not scenario.publishable
        or scenario.pending_network_gap_candidate_set_ids
        or len(scenario.resolved_selections) != len(scenario.candidate_sets)
    ):
        raise ValueError("Reference application requires an exact resolved, publishable Scenario")
    if any(item.complementary_candidate_ids for item in scenario.resolved_selections):
        raise ValueError(
            "Reference application v1 cannot express a complementary set as one "
            "replacement per logical Community Connection"
        )
    selected = tuple(
        sorted(
            item.selected_candidate_id
            for item in scenario.resolved_selections
            if item.selected_candidate_id is not None
        )
    )
    reference_candidate_ids = tuple(
        sorted(
            {
                *reference.selected_candidate_ids,
                *reference.complementary_candidate_ids,
            }
        )
    )
    if len(selected) != len(scenario.resolved_selections) or selected != reference_candidate_ids:
        raise ValueError(
            "Reference application requires exactly one selected candidate per "
            "logical Community Connection"
        )


def _validate_preparation(
    reference: ReferenceSATNSelection,
    preparation: SpineAccessCandidatePreparationResult,
) -> dict[str, PreparedSpineAccessConnection]:
    if preparation.contract != _PREPARATION_CONTRACT:
        raise ValueError("Reference application preparation contract is unsupported")
    if preparation.status != "prepared" or preparation.missing_inputs:
        raise ValueError("Reference application preparation is incomplete")
    expected_fingerprint = _fingerprint(preparation.canonical_payload())
    if preparation.preparation_fingerprint != expected_fingerprint:
        raise ValueError("Reference application preparation fingerprint is stale")
    evidence_fingerprints = tuple(preparation.evidence_fingerprints)
    expected_evidence_fingerprints = _evidence_lineage_fingerprints(preparation.evidence_lineage)
    if (
        not expected_evidence_fingerprints
        or evidence_fingerprints != expected_evidence_fingerprints
        or any(_SHA256.fullmatch(item) is None for item in evidence_fingerprints)
    ):
        raise ValueError(
            "Reference application preparation fingerprints do not exactly match "
            "the raw evidence lineage"
        )
    roster = tuple(preparation.connection_roster)
    roster_ids = tuple(item.access_connection_id for item in roster)
    if (
        not roster
        or roster_ids != tuple(sorted(roster_ids))
        or len(set(roster_ids)) != len(roster_ids)
    ):
        raise ValueError("Reference application preparation roster is stale")
    if any(item.disposition == "unresolved-gap" for item in roster):
        raise ValueError("Reference application preparation retains an unresolved connection")
    prepared = tuple(preparation.prepared_spine_access_connections)
    access_ids = tuple(item.access_connection_id for item in prepared)
    if (
        not prepared
        or access_ids != tuple(sorted(access_ids))
        or len(set(access_ids)) != len(access_ids)
    ):
        raise ValueError("Reference application requires unique, canonical prepared connections")
    roster_prepared_ids = {
        item.access_connection_id for item in roster if item.disposition.startswith("prepared-")
    }
    if set(access_ids) != roster_prepared_ids:
        raise ValueError("Reference application prepared connections do not match the roster")

    set_ids = tuple(item.candidate_set.candidate_set_id for item in prepared)
    logical_ids = tuple(item.candidate_set.connection_id for item in prepared)
    if len(set(set_ids)) != len(set_ids) or len(set(logical_ids)) != len(logical_ids):
        raise ValueError(
            "Reference application preparation duplicates a Candidate Set or "
            "logical Community Connection"
        )
    for item in prepared:
        record_ids = tuple(record.candidate.candidate_id for record in item.candidate_records)
        if record_ids != tuple(sorted(record_ids)) or len(set(record_ids)) != len(record_ids):
            raise ValueError(
                "Reference application preparation candidate records must be "
                "unique and canonically ordered"
            )
        records_by_candidate_id = {
            record.candidate.candidate_id: record for record in item.candidate_records
        }
        for candidate in item.candidate_set.admitted_candidates:
            record = records_by_candidate_id.get(candidate.candidate_id)
            if (
                record is None
                or record.candidate != candidate
                or record.preparation_disposition != "retained-representative"
                or record.rejection_reason is not None
                or record.retained_candidate_id is not None
            ):
                raise ValueError(
                    "Reference application preparation does not retain one exact "
                    "record for every admitted candidate"
                )
    prepared_by_set_id = {item.candidate_set.candidate_set_id: item for item in prepared}
    scenario = reference.scenario
    scenario_sets = {item.candidate_set_id: item for item in scenario.candidate_sets}
    if set(prepared_by_set_id) != set(scenario_sets):
        raise ValueError(
            "Reference Scenario contains foreign or unconsumed prepared Candidate Sets"
        )
    for candidate_set_id, item in prepared_by_set_id.items():
        if (
            item.candidate_set != scenario_sets[candidate_set_id]
            or item.candidate_set.profile_fingerprint != preparation.profile_fingerprint
        ):
            raise ValueError("Reference Scenario Candidate Set is stale for its exact preparation")
    return prepared_by_set_id


def _validate_preparation_lineage(
    reference: ReferenceSATNSelection,
    preparation: SpineAccessCandidatePreparationResult,
) -> None:
    scenario = reference.scenario
    expected_lineage = tuple(
        sorted(
            {
                preparation.preparation_fingerprint,
                *(selection.criteria.criteria_fingerprint for selection in scenario.selections),
            }
        )
    )
    if (
        scenario.profile_fingerprint != preparation.profile_fingerprint
        or scenario.lineage_fingerprints != expected_lineage
    ):
        raise ValueError(
            "Reference Scenario does not bind the exact preparation, profile and criteria lineage"
        )


def _evidence_lineage_fingerprints(
    lineage: Mapping[str, object],
) -> tuple[str, ...]:
    """Mirror the preparation compiler's exact raw-evidence identity derivation."""

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
            if isinstance(item, Mapping):
                value = item.get("content_sha256")
                if isinstance(value, str):
                    fingerprints.add(value)
    governed_source = education.get("governed_source_fingerprint")
    if isinstance(governed_source, str):
        fingerprints.add(governed_source)
    for key in ("school_register_lineage", "admissions_lineage"):
        item = education.get(key)
        if isinstance(item, Mapping):
            value = item.get("content_sha256")
            if isinstance(value, str):
                fingerprints.add(value)
    return tuple(sorted(fingerprints))


def _candidate_binding(
    resolved: ResolvedPreferredStrategicAlignment,
    prepared: PreparedSpineAccessConnection,
) -> ReferenceApplicationCandidateBinding:
    selected_candidate_id = resolved.selected_candidate_id
    resolution_fingerprint = resolved.resolution_fingerprint
    candidate_set = resolved.compiler_selection.candidate_set
    if not isinstance(selected_candidate_id, str):
        raise ValueError("Reference resolution does not select one replacement candidate")
    candidate = next(
        (
            item
            for item in candidate_set.admitted_candidates
            if item.candidate_id == selected_candidate_id
        ),
        None,
    )
    if candidate is None:
        raise ValueError("Reference resolution selected a foreign candidate")
    records = tuple(
        item
        for item in prepared.candidate_records
        if item.candidate.candidate_id == selected_candidate_id
    )
    if len(records) != 1:
        raise ValueError(
            "Reference application requires exactly one retained prepared record "
            "for its selected candidate"
        )
    record = records[0]
    if (
        record.candidate != candidate
        or record.preparation_disposition != "retained-representative"
        or record.rejection_reason is not None
        or record.retained_candidate_id is not None
    ):
        raise ValueError(
            "Reference selected candidate is rejected, foreign or stale in preparation"
        )
    if not record.routing_edge_ids or not record.reverse_routing_edge_ids:
        raise ValueError(
            "Reference application requires non-empty forward and reverse routing "
            "edge sequences for every selected route"
        )
    return ReferenceApplicationCandidateBinding(
        logical_connection_id=candidate_set.connection_id,
        candidate_set_id=candidate_set.candidate_set_id,
        candidate_set_fingerprint=candidate_set.candidate_set_fingerprint,
        resolution_fingerprint=resolution_fingerprint,
        selected_candidate_id=selected_candidate_id,
        source_access_connection_id=prepared.access_connection_id,
        route_role=record.route_role,
        routing_edge_ids=record.routing_edge_ids,
        reverse_routing_edge_ids=record.reverse_routing_edge_ids,
        geometry_fingerprint=candidate.geometry_fingerprint,
        candidate_input_fingerprint=_candidate_input_fingerprint(candidate),
        candidate_evidence_fingerprints=tuple(sorted(candidate.evidence_fingerprints)),
        prepared_candidate_record_fingerprint=_fingerprint(record.canonical()),
        prepared_connection_fingerprint=_fingerprint(prepared.canonical()),
    )


def _candidate_input_fingerprint(candidate: AlignmentCandidateInput) -> str:
    candidate = AlignmentCandidateInput.model_validate(candidate.model_dump(mode="python"))
    return _fingerprint(candidate.model_dump(mode="json"))
