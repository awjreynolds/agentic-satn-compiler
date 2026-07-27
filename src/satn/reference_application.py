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
from satn.identifiers import stable_id
from satn.models import AreaConfig
from satn.spine_access_candidate_preparation import (
    PreparedSpineAccessConnection,
    SpineAccessCandidatePreparationResult,
)

REFERENCE_APPLICATION_CONTRACT = "satn-reference-application-plan/v1"
VALIDATED_REFERENCE_APPLICATION_CONTRACT = "satn-validated-reference-application/v1"
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
    community_place_id: str = Field(min_length=1)
    parent_place_id: str = Field(min_length=1)
    root_spine_id: str = Field(min_length=1)
    routing_start_node_id: str = Field(min_length=1)
    routing_end_node_id: str = Field(min_length=1)
    route_role: str = Field(min_length=1)
    routing_edge_ids: tuple[str, ...] = Field(min_length=1)
    reverse_routing_edge_ids: tuple[str, ...] = Field(min_length=1)
    geometry_fingerprint: str = Field(pattern=_SHA256.pattern)
    candidate_input_fingerprint: str = Field(pattern=_SHA256.pattern)
    candidate_evidence_fingerprints: tuple[str, ...] = Field(min_length=1)
    prepared_candidate_record_fingerprint: str = Field(pattern=_SHA256.pattern)
    prepared_connection_fingerprint: str = Field(pattern=_SHA256.pattern)
    binding_fingerprint: str = ""

    @field_validator(
        "source_access_connection_id",
        "community_place_id",
        "parent_place_id",
        "root_spine_id",
        "routing_start_node_id",
        "routing_end_node_id",
        "route_role",
    )
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


class ValidatedReferenceApplication(BaseModel):
    """Compiler-owned authority to replay one exact plan against live local inputs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal["satn-validated-reference-application/v1"] = (
        VALIDATED_REFERENCE_APPLICATION_CONTRACT
    )
    plan: ReferenceApplicationPlan
    area_definition_sha256: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    baseline_preparation_fingerprint: str = Field(pattern=_SHA256.pattern)
    baseline_evidence_fingerprints: tuple[str, ...] = Field(min_length=1)
    governed_input_fingerprint: str = Field(pattern=_SHA256.pattern)
    publication_created: Literal[False] = False
    publication_authority: Literal["none"] = "none"
    context_fingerprint: str = ""

    @model_validator(mode="after")
    def bind_context(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"context_fingerprint"})
        expected = _fingerprint(payload)
        if self.context_fingerprint and self.context_fingerprint != expected:
            raise ValueError("Validated Reference application context is stale")
        if (
            self.plan.scenario_area_fingerprint != self.area_definition_sha256
            or self.plan.profile_fingerprint != self.profile_fingerprint
            or self.plan.preparation_fingerprint != self.baseline_preparation_fingerprint
            or self.plan.preparation_evidence_fingerprints
            != self.baseline_evidence_fingerprints
        ):
            raise ValueError("Validated Reference application context lineage is inconsistent")
        object.__setattr__(self, "context_fingerprint", expected)
        return self


def build_validated_reference_application(
    reference: ReferenceSATNSelection,
    source_preparation: SpineAccessCandidatePreparationResult,
    current_baseline_preparation: SpineAccessCandidatePreparationResult,
    config: AreaConfig,
    governed_input_fingerprint: str,
) -> ValidatedReferenceApplication:
    """Validate live local compiler inputs before granting replay authority."""

    if _SHA256.fullmatch(governed_input_fingerprint) is None:
        raise ValueError(
            "Validated Reference application requires a non-empty canonical governed "
            "input SHA-256"
        )
    plan = build_reference_application_plan(reference, source_preparation)
    _validate_preparation(reference, current_baseline_preparation)
    _validate_preparation_lineage(reference, current_baseline_preparation)
    if (
        current_baseline_preparation.contract != source_preparation.contract
        or current_baseline_preparation.status != source_preparation.status
        or current_baseline_preparation.canonical_payload()
        != source_preparation.canonical_payload()
        or current_baseline_preparation.preparation_fingerprint
        != source_preparation.preparation_fingerprint
        or current_baseline_preparation.evidence_fingerprints
        != source_preparation.evidence_fingerprints
        or current_baseline_preparation.profile_fingerprint
        != source_preparation.profile_fingerprint
    ):
        raise ValueError(
            "Validated Reference application current baseline preparation does not "
            "exactly match its source preparation"
        )
    area_sha256 = _actual_area_definition_sha256(config)
    profile_fingerprint = _current_profile_fingerprint(config)
    if plan.scenario_area_fingerprint != area_sha256:
        raise ValueError(
            "Reference Scenario Area fingerprint does not match actual Area Definition bytes"
        )
    if (
        profile_fingerprint != plan.profile_fingerprint
        or profile_fingerprint != current_baseline_preparation.profile_fingerprint
        or profile_fingerprint != source_preparation.profile_fingerprint
    ):
        raise ValueError(
            "Validated Reference application profile does not match current configuration, "
            "plan and preparation"
        )
    return ValidatedReferenceApplication(
        plan=plan,
        area_definition_sha256=area_sha256,
        profile_fingerprint=profile_fingerprint,
        baseline_preparation_fingerprint=current_baseline_preparation.preparation_fingerprint,
        baseline_evidence_fingerprints=tuple(
            current_baseline_preparation.evidence_fingerprints
        ),
        governed_input_fingerprint=governed_input_fingerprint,
    )


def validate_reference_application_for_use(
    context: ValidatedReferenceApplication,
    config: AreaConfig,
    governed_input_fingerprint: str,
) -> ReferenceApplicationPlan:
    """Re-open live inputs so a stale validated context cannot acquire authority."""

    if not isinstance(context, ValidatedReferenceApplication):
        raise TypeError("compiler replay requires a ValidatedReferenceApplication context")
    context = ValidatedReferenceApplication.model_validate(
        context.model_dump(mode="python")
    )
    if _actual_area_definition_sha256(config) != context.area_definition_sha256:
        raise ValueError("Validated Reference application Area Definition changed before use")
    if _current_profile_fingerprint(config) != context.profile_fingerprint:
        raise ValueError("Validated Reference application profile changed before use")
    if (
        _SHA256.fullmatch(governed_input_fingerprint) is None
        or governed_input_fingerprint != context.governed_input_fingerprint
    ):
        raise ValueError("Validated Reference application governed input changed before use")
    return context.plan


def _actual_area_definition_sha256(config: AreaConfig) -> str:
    path = config.config_path
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ValueError("Validated Reference application cannot open Area Definition") from error
    return hashlib.sha256(payload).hexdigest()


def _current_profile_fingerprint(config: AreaConfig) -> str:
    compilation = getattr(config, "compilation", None)
    profile = getattr(compilation, "network_selection", None)
    if profile is None:
        raise ValueError("Validated Reference application requires current network_selection")
    return str(profile.fingerprint)


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
    _validate_replay_endpoints(prepared, candidate_set.endpoints, candidate.endpoints)
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
    replay_facts = _binding_replay_facts(prepared, record.connection_json)
    return ReferenceApplicationCandidateBinding(
        logical_connection_id=candidate_set.connection_id,
        candidate_set_id=candidate_set.candidate_set_id,
        candidate_set_fingerprint=candidate_set.candidate_set_fingerprint,
        resolution_fingerprint=resolution_fingerprint,
        selected_candidate_id=selected_candidate_id,
        source_access_connection_id=prepared.access_connection_id,
        **replay_facts,
        route_role=record.route_role,
        routing_edge_ids=record.routing_edge_ids,
        reverse_routing_edge_ids=record.reverse_routing_edge_ids,
        geometry_fingerprint=candidate.geometry_fingerprint,
        candidate_input_fingerprint=_candidate_input_fingerprint(candidate),
        candidate_evidence_fingerprints=tuple(sorted(candidate.evidence_fingerprints)),
        prepared_candidate_record_fingerprint=_fingerprint(record.canonical()),
        prepared_connection_fingerprint=_fingerprint(prepared.canonical()),
    )


def _binding_replay_facts(
    prepared: PreparedSpineAccessConnection,
    connection_json: str,
) -> dict[str, str]:
    """Derive executable route facts from the exact prepared connection record.

    The data is not a caller-declared replay claim: it is embedded in the
    ``PreparedCandidateRecord`` that is itself bound into preparation and plan
    fingerprints.  Verify every duplicated typed fact before exposing it to a
    later compiler-only replay.
    """

    try:
        payload = json.loads(connection_json)
    except json.JSONDecodeError as error:
        raise ValueError("Reference application prepared connection JSON is invalid") from error
    if not isinstance(payload, dict):
        raise ValueError("Reference application prepared connection JSON must be an object")

    expected = {
        "access_connection_id": prepared.access_connection_id,
        "obligation_kind": "community",
        "community_id": prepared.community_id,
        "place_id": prepared.place_id,
        "parent_place_id": prepared.parent_place_id,
        "root_spine_id": prepared.root_spine_id,
        "parent_role": "spine-access-connection",
    }
    for name, value in expected.items():
        if prepared.obligation_kind != "community" and name == "obligation_kind":
            raise ValueError("Reference application requires a Community preparation")
        if prepared.parent_role != "spine-access-connection" and name == "parent_role":
            raise ValueError("Reference application requires a chained Community parent")
        if payload.get(name) != value:
            raise ValueError(
                "Reference application prepared connection JSON disagrees with typed "
                f"preparation for {name}"
            )

    start = payload.get("community_attachment_node")
    end = payload.get("target_attachment_node")
    if not isinstance(start, str) or not start.strip() or start.strip() != start:
        raise ValueError("Reference application requires a canonical community routing node")
    if not isinstance(end, str) or not end.strip() or end.strip() != end:
        raise ValueError("Reference application requires a canonical parent routing node")
    return {
        "community_place_id": prepared.community_id,
        "parent_place_id": prepared.parent_place_id,
        "root_spine_id": prepared.root_spine_id,
        "routing_start_node_id": start,
        "routing_end_node_id": end,
    }


def _validate_replay_endpoints(
    prepared: PreparedSpineAccessConnection,
    candidate_set_endpoints: tuple[str, str],
    candidate_endpoints: tuple[str, str],
) -> None:
    """Require the selected menu entry to retain the prepared child-parent orientation."""

    expected = tuple(
        sorted(
            (
                _canonical_prepared_endpoint(
                    prepared.community_id,
                    prefix="community-endpoint",
                ),
                _canonical_prepared_endpoint(
                    prepared.parent_place_id,
                    prefix="network-endpoint",
                ),
            )
        )
    )
    if candidate_set_endpoints != expected or candidate_endpoints != expected:
        raise ValueError(
            "Reference application Candidate Set and selected candidate endpoints must "
            "exactly match the typed prepared Community parent orientation"
        )


def _canonical_prepared_endpoint(value: str, *, prefix: str) -> str:
    """Mirror preparation's endpoint derivation without accepting a caller claim."""

    if re.fullmatch(r"^[a-z0-9][a-z0-9._:-]*$", value):
        return value
    return stable_id(prefix, value)


def _candidate_input_fingerprint(candidate: AlignmentCandidateInput) -> str:
    candidate = AlignmentCandidateInput.model_validate(candidate.model_dump(mode="python"))
    return _fingerprint(candidate.model_dump(mode="json"))
