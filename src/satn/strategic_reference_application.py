"""Bind an adopted strategic-corridor Scenario without applying it.

This sibling contract is intentionally separate from the Spine Access
``ReferenceApplicationPlan``.  It proves human adoption and exact replay
bindings for the two strategic-corridor roles, but cannot load inputs, mutate
network geometry, invoke an agent, publish, or authorize any of those actions.

SHA-256 values are reproducible local content identities for lineage and stale
input detection.  They are not credentials, signatures, certificates, trust
roots, or claims about an external principal.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satn.alignment_selection import (
    CandidateSetDisposition,
    CanonicalLineString,
    GovernedReferenceSelectionDecision,
    NetworkRole,
    ReferenceSATNSelection,
    adopt_reference_satn,
)
from satn.content_identity import canonical_json as _canonical_json
from satn.content_identity import content_fingerprint as _fingerprint
from satn.strategic_corridors import (
    StrategicCorridorPreparationResult,
    StrategicCorridorUnitRole,
)
from satn.strategic_criteria_scenario import StrategicCriteriaScenarioResult

STRATEGIC_REFERENCE_APPLICATION_CONTRACT = (
    "satn-strategic-reference-application-plan/v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")


def _canonical_ids(
    value: tuple[str, ...],
    field: str,
    *,
    pattern: re.Pattern[str] = _ID,
) -> tuple[str, ...]:
    if (
        tuple(sorted(value)) != value
        or len(set(value)) != len(value)
        or any(pattern.fullmatch(item) is None for item in value)
    ):
        raise ValueError(f"{field} must contain unique canonical identifiers")
    return value


class StrategicReferenceApplicationDisposition(StrEnum):
    """How one exact adopted strategic route would later enter replay."""

    SELECTED_SUBSTITUTE = "selected-substitute"
    COMPLEMENTARY_REQUIRED = "complementary-required"


class StrategicReferenceEndpointBinding(BaseModel):
    """Exact typed endpoint identity, kept separate from served obligations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_endpoints: tuple[str, str]
    routing_node_ids: tuple[str, str]
    network_place_ids: tuple[str, ...] = ()
    strategic_destination_ids: tuple[str, ...] = ()
    binding_fingerprint: str = ""

    @field_validator(
        "network_place_ids",
        "strategic_destination_ids",
    )
    @classmethod
    def canonical_obligation_ids(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        return _canonical_ids(
            value,
            getattr(info, "field_name", "endpoint binding identifiers"),
        )

    @model_validator(mode="after")
    def bind_endpoint(self) -> Self:
        if (
            len(self.candidate_endpoints) != 2
            or tuple(sorted(set(self.candidate_endpoints)))
            != self.candidate_endpoints
            or len(self.routing_node_ids) != 2
            or any(not item or item.strip() != item for item in self.routing_node_ids)
        ):
            raise ValueError("strategic Reference endpoint binding is not canonical")
        payload = self.model_dump(mode="json", exclude={"binding_fingerprint"})
        expected = _fingerprint(payload)
        if self.binding_fingerprint and self.binding_fingerprint != expected:
            raise ValueError("strategic Reference endpoint binding fingerprint is stale")
        object.__setattr__(self, "binding_fingerprint", expected)
        return self


class StrategicReferenceCandidateBinding(BaseModel):
    """One exact adopted strategic candidate and its immutable source facts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit_id: str = Field(pattern=r"^alignment-unit-[0-9a-f]{20}$")
    unit_role: StrategicCorridorUnitRole
    application_disposition: StrategicReferenceApplicationDisposition
    source_unit_fingerprint: str = Field(pattern=_SHA256.pattern)
    candidate_set_id: str = Field(pattern=r"^candidate-set-[0-9a-f]{20}$")
    candidate_set_fingerprint: str = Field(pattern=_SHA256.pattern)
    resolution_fingerprint: str = Field(pattern=_SHA256.pattern)
    selected_candidate_id: str = Field(pattern=r"^candidate-[0-9a-f]{20}$")
    candidate_input_fingerprint: str = Field(pattern=_SHA256.pattern)
    endpoint_binding: StrategicReferenceEndpointBinding
    mandatory_network_place_ids: tuple[str, ...] = ()
    mandatory_access_obligation_ids: tuple[str, ...] = ()
    mandatory_strategic_destination_ids: tuple[str, ...] = ()
    served_network_place_ids: tuple[str, ...] = ()
    served_access_obligation_ids: tuple[str, ...] = ()
    served_strategic_destination_ids: tuple[str, ...] = ()
    routing_start_node_id: str = Field(min_length=1)
    routing_end_node_id: str = Field(min_length=1)
    routing_edge_ids: tuple[str, ...] = Field(min_length=1)
    reverse_routing_edge_ids: tuple[str, ...] = Field(min_length=1)
    geometry: CanonicalLineString
    geometry_fingerprint: str = Field(pattern=_SHA256.pattern)
    physical_alignment_id: str = Field(
        pattern=r"^physical-alignment-[0-9a-f]{20}$"
    )
    registry_geometry: CanonicalLineString
    registry_geometry_fingerprint: str = Field(pattern=_SHA256.pattern)
    generation_strategies: tuple[str, ...] = Field(min_length=1)
    source_ids: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    candidate_record_fingerprint: str = Field(pattern=_SHA256.pattern)
    binding_fingerprint: str = ""

    @field_validator(
        "mandatory_network_place_ids",
        "mandatory_access_obligation_ids",
        "mandatory_strategic_destination_ids",
        "served_network_place_ids",
        "served_access_obligation_ids",
        "served_strategic_destination_ids",
        "source_ids",
        "evidence_ids",
        "generation_strategies",
    )
    @classmethod
    def canonical_ids(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        return _canonical_ids(
            value,
            getattr(info, "field_name", "strategic Reference identifiers"),
        )

    @field_validator("routing_edge_ids", "reverse_routing_edge_ids")
    @classmethod
    def canonical_edges(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        if any(not item or item.strip() != item for item in value):
            raise ValueError(
                f"{getattr(info, 'field_name', 'route edges')} must be canonical"
            )
        return value

    @model_validator(mode="after")
    def bind_candidate(self) -> Self:
        if (
            self.geometry.fingerprint != self.geometry_fingerprint
            or self.registry_geometry.fingerprint
            != self.registry_geometry_fingerprint
            or self.geometry != self.registry_geometry
        ):
            raise ValueError(
                "strategic Reference candidate and physical registry geometry disagree"
            )
        endpoint = self.endpoint_binding
        if (
            (
                self.routing_start_node_id,
                self.routing_end_node_id,
            )
            != endpoint.routing_node_ids
            or self.unit_role.network_role
            not in {
                NetworkRole.INTERURBAN_SPINE,
                NetworkRole.STRATEGIC_DESTINATION_ACCESS,
            }
        ):
            raise ValueError("strategic Reference route identity is stale")
        if self.unit_role is StrategicCorridorUnitRole.INTERURBAN_SPINE:
            if (
                self.application_disposition
                is not StrategicReferenceApplicationDisposition.SELECTED_SUBSTITUTE
                or len(endpoint.network_place_ids) != 2
                or endpoint.strategic_destination_ids
                or self.mandatory_network_place_ids != endpoint.network_place_ids
                or self.served_network_place_ids != endpoint.network_place_ids
                or self.mandatory_strategic_destination_ids
                or self.served_strategic_destination_ids
            ):
                raise ValueError(
                    "interurban strategic Reference binding must be one selected "
                    "two-place substitute"
                )
        elif (
            self.application_disposition
            is not StrategicReferenceApplicationDisposition.COMPLEMENTARY_REQUIRED
            or len(endpoint.network_place_ids) != 1
            or len(endpoint.strategic_destination_ids) != 1
            or self.mandatory_network_place_ids
            or self.served_network_place_ids
            or self.mandatory_strategic_destination_ids
            != endpoint.strategic_destination_ids
            or self.served_strategic_destination_ids
            != endpoint.strategic_destination_ids
        ):
            raise ValueError(
                "destination strategic Reference binding must be complementary "
                "with the destination as its sole hard obligation"
            )
        payload = self.model_dump(mode="json", exclude={"binding_fingerprint"})
        expected = _fingerprint(payload)
        if self.binding_fingerprint and self.binding_fingerprint != expected:
            raise ValueError("strategic Reference candidate binding fingerprint is stale")
        object.__setattr__(self, "binding_fingerprint", expected)
        return self


class StrategicReferenceApplicationPlan(BaseModel):
    """Replay-only strategic contract with no mutation or publication authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal[
        "satn-strategic-reference-application-plan/v1"
    ] = STRATEGIC_REFERENCE_APPLICATION_CONTRACT
    source_preparation_json: str = Field(min_length=2)
    reference: ReferenceSATNSelection
    preparation_fingerprint: str = Field(pattern=_SHA256.pattern)
    profile_fingerprint: str = Field(pattern=_SHA256.pattern)
    preparation_evidence_fingerprints: tuple[str, ...] = Field(min_length=1)
    scenario_fingerprint: str = Field(pattern=_SHA256.pattern)
    reference_selection_fingerprint: str = Field(pattern=_SHA256.pattern)
    reference_decision_fingerprint: str = Field(pattern=_SHA256.pattern)
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256.pattern)
    area_fingerprint: str = Field(pattern=_SHA256.pattern)
    selection_run_fingerprint: str = Field(pattern=_SHA256.pattern)
    bindings: tuple[StrategicReferenceCandidateBinding, ...] = Field(min_length=2)
    replay_directive: Literal["recompile-whole-network-on-ledger-change"] = (
        "recompile-whole-network-on-ledger-change"
    )
    authoritative_network_geometry_mutated: Literal[False] = False
    publication_created: Literal[False] = False
    plan_fingerprint: str = ""

    @field_validator("preparation_evidence_fingerprints")
    @classmethod
    def canonical_evidence(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _canonical_ids(
            value,
            "preparation_evidence_fingerprints",
            pattern=_SHA256,
        )

    @field_validator("bindings")
    @classmethod
    def canonical_bindings(
        cls,
        value: tuple[StrategicReferenceCandidateBinding, ...],
    ) -> tuple[StrategicReferenceCandidateBinding, ...]:
        if tuple(item.unit_id for item in value) != tuple(
            sorted(item.unit_id for item in value)
        ):
            raise ValueError("strategic Reference bindings must be canonically ordered")
        for field in (
            "unit_id",
            "candidate_set_id",
            "selected_candidate_id",
        ):
            values = tuple(str(getattr(item, field)) for item in value)
            if len(set(values)) != len(values):
                raise ValueError(
                    f"strategic Reference bindings must have unique {field} values"
                )
        return value

    @model_validator(mode="after")
    def bind_plan(self) -> Self:
        reference = ReferenceSATNSelection.model_validate(
            self.reference.model_dump(mode="python")
        )
        source = _canonical_preparation_payload(self.source_preparation_json)
        scenario = reference.scenario
        expected_identities = {
            "preparation_fingerprint": source["preparation_fingerprint"],
            "profile_fingerprint": scenario.profile_fingerprint,
            "scenario_fingerprint": scenario.scenario_fingerprint,
            "reference_selection_fingerprint": (
                reference.reference_selection_fingerprint
            ),
            "reference_decision_fingerprint": (
                reference.governed_decision.decision_fingerprint
            ),
            "evidence_snapshot_fingerprint": (
                scenario.evidence_snapshot.snapshot_fingerprint
            ),
            "area_fingerprint": scenario.area_fingerprint,
            "selection_run_fingerprint": (
                scenario.decision_record.record_fingerprint
            ),
        }
        if any(getattr(self, key) != value for key, value in expected_identities.items()):
            raise ValueError("strategic Reference plan identity is stale")
        canonical = source["canonical_payload"]
        assert isinstance(canonical, dict)
        evidence = canonical.get("evidence_fingerprints")
        if (
            not isinstance(evidence, list)
            or tuple(evidence) != self.preparation_evidence_fingerprints
            or scenario.profile_fingerprint != canonical.get("profile_fingerprint")
        ):
            raise ValueError("strategic Reference plan profile/evidence identity is stale")
        expected_bindings = _bindings_from_canonical_source(reference, source)
        if self.bindings != expected_bindings:
            raise ValueError(
                "strategic Reference bindings do not match exact adopted source lineage"
            )
        object.__setattr__(self, "reference", reference)
        payload = self.model_dump(mode="json", exclude={"plan_fingerprint"})
        expected = _fingerprint(payload)
        if self.plan_fingerprint and self.plan_fingerprint != expected:
            raise ValueError("strategic Reference application plan fingerprint is stale")
        object.__setattr__(self, "plan_fingerprint", expected)
        return self


def adopt_strategic_reference_satn(
    result: StrategicCriteriaScenarioResult,
    *,
    governed_decision: GovernedReferenceSelectionDecision,
) -> ReferenceSATNSelection:
    """Use existing human adoption authority for one exact strategic Scenario."""

    result = replace(result)
    scenario = result.scenario
    if (
        result.status != "compiled"
        or scenario is None
        or not scenario.publishable
        or result.preparation_fingerprint is None
        or result.preparation_fingerprint not in scenario.lineage_fingerprints
        or scenario.network_gaps
        or {item.network_role for item in scenario.candidate_sets}
        != {
            NetworkRole.INTERURBAN_SPINE,
            NetworkRole.STRATEGIC_DESTINATION_ACCESS,
        }
    ):
        raise ValueError(
            "strategic Reference adoption requires one exact fully resolved "
            "interurban-and-destination Scenario"
        )
    return adopt_reference_satn(
        scenario,
        governed_decision=governed_decision,
    )


def build_strategic_reference_application_plan(
    reference: ReferenceSATNSelection,
    preparation: StrategicCorridorPreparationResult,
) -> StrategicReferenceApplicationPlan:
    """Bind an exact adopted strategic Reference to its exact preparation."""

    reference = ReferenceSATNSelection.model_validate(
        reference.model_dump(mode="python")
    )
    source = _source_preparation_payload(preparation)
    canonical = source["canonical_payload"]
    assert isinstance(canonical, dict)
    evidence = canonical["evidence_fingerprints"]
    assert isinstance(evidence, list)
    return StrategicReferenceApplicationPlan(
        source_preparation_json=_canonical_json(source),
        reference=reference,
        preparation_fingerprint=preparation.preparation_fingerprint,
        profile_fingerprint=reference.scenario.profile_fingerprint,
        preparation_evidence_fingerprints=tuple(evidence),
        scenario_fingerprint=reference.scenario.scenario_fingerprint,
        reference_selection_fingerprint=(
            reference.reference_selection_fingerprint
        ),
        reference_decision_fingerprint=(
            reference.governed_decision.decision_fingerprint
        ),
        evidence_snapshot_fingerprint=(
            reference.scenario.evidence_snapshot.snapshot_fingerprint
        ),
        area_fingerprint=reference.scenario.area_fingerprint,
        selection_run_fingerprint=(
            reference.scenario.decision_record.record_fingerprint
        ),
        bindings=_bindings_from_canonical_source(reference, source),
    )


def validate_fresh_strategic_reference_preparation(
    reference: ReferenceSATNSelection,
    source_preparation: StrategicCorridorPreparationResult,
    current_preparation: StrategicCorridorPreparationResult,
) -> StrategicReferenceApplicationPlan:
    """Fail closed unless fresh preparation exactly equals the adopted source."""

    source = _source_preparation_payload(source_preparation)
    current = _source_preparation_payload(current_preparation)
    if source != current:
        raise ValueError(
            "fresh strategic preparation does not exactly match adopted source preparation"
        )
    return build_strategic_reference_application_plan(
        reference,
        current_preparation,
    )


def _source_preparation_payload(
    preparation: StrategicCorridorPreparationResult,
) -> dict[str, object]:
    canonical = preparation.canonical_payload()
    expected = _fingerprint(canonical)
    if preparation.preparation_fingerprint != expected:
        raise ValueError("strategic Reference preparation fingerprint is stale")
    if (
        preparation.contract != "satn-strategic-corridor-preparation/v1"
        or preparation.status != "prepared"
        or preparation.missing_inputs
        or preparation.issues
    ):
        raise ValueError("strategic Reference requires exact prepared corridor evidence")
    return {
        "canonical_payload": canonical,
        "preparation_fingerprint": preparation.preparation_fingerprint,
    }


def _canonical_preparation_payload(value: str) -> dict[str, object]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("strategic Reference source preparation JSON is invalid") from error
    if not isinstance(payload, dict) or _canonical_json(payload) != value:
        raise ValueError("strategic Reference source preparation JSON is not canonical")
    canonical = payload.get("canonical_payload")
    fingerprint = payload.get("preparation_fingerprint")
    if (
        not isinstance(canonical, dict)
        or not isinstance(fingerprint, str)
        or _SHA256.fullmatch(fingerprint) is None
        or _fingerprint(canonical) != fingerprint
        or canonical.get("contract") != "satn-strategic-corridor-preparation/v1"
        or canonical.get("status") != "prepared"
        or canonical.get("missing_inputs") != []
        or canonical.get("issues") != []
    ):
        raise ValueError("strategic Reference source preparation lineage is stale")
    return payload


def _bindings_from_canonical_source(
    reference: ReferenceSATNSelection,
    source: dict[str, object],
) -> tuple[StrategicReferenceCandidateBinding, ...]:
    canonical = source["canonical_payload"]
    assert isinstance(canonical, dict)
    units = canonical.get("units")
    physical = canonical.get("physical_alignments")
    if not isinstance(units, list) or not isinstance(physical, list):
        raise ValueError("strategic Reference preparation units/registry are malformed")
    units_by_set: dict[str, dict[str, object]] = {}
    for unit in units:
        if not isinstance(unit, dict):
            raise ValueError("strategic Reference source unit is malformed")
        candidate_set = unit.get("candidate_set")
        if not isinstance(candidate_set, dict):
            raise ValueError("strategic Reference source Candidate Set is malformed")
        candidate_set_id = candidate_set.get("candidate_set_id")
        if not isinstance(candidate_set_id, str) or candidate_set_id in units_by_set:
            raise ValueError(
                "strategic Reference preparation has duplicate or malformed units"
            )
        units_by_set[candidate_set_id] = unit
    physical_by_id: dict[str, dict[str, object]] = {}
    for item in physical:
        if not isinstance(item, dict):
            raise ValueError("strategic Reference physical registry is malformed")
        identifier = item.get("physical_alignment_id")
        if not isinstance(identifier, str) or identifier in physical_by_id:
            raise ValueError(
                "strategic Reference physical registry contains duplicate identities"
            )
        physical_by_id[identifier] = item

    scenario = reference.scenario
    expected_lineage = {
        source["preparation_fingerprint"],
        *(item.criteria_fingerprint for item in scenario.selections),
    }
    if (
        scenario.profile_fingerprint != canonical.get("profile_fingerprint")
        or set(scenario.lineage_fingerprints) != expected_lineage
        or set(units_by_set)
        != {item.candidate_set_id for item in scenario.candidate_sets}
        or scenario.network_gaps
        or not scenario.publishable
    ):
        raise ValueError(
            "strategic Reference Scenario does not exactly match preparation, "
            "profile and criteria lineage"
        )
    resolution_by_set = {
        item.candidate_set_id: item for item in scenario.resolved_selections
    }
    if set(resolution_by_set) != set(units_by_set):
        raise ValueError(
            "strategic Reference is missing or contains foreign resolved units"
        )
    classification_by_set = {
        item.candidate_set_id: item.disposition
        for item in scenario.candidate_set_classifications
    }
    bindings: list[StrategicReferenceCandidateBinding] = []
    consumed_candidates: set[str] = set()
    for candidate_set_id, unit in sorted(units_by_set.items()):
        resolution = resolution_by_set[candidate_set_id]
        winner_ids = tuple(
            item
            for item in (
                resolution.selected_candidate_id,
                *resolution.complementary_candidate_ids,
            )
            if item is not None
        )
        if len(winner_ids) != 1:
            raise ValueError(
                "strategic Reference v1 requires one exact resolved candidate per unit"
            )
        candidate_id = winner_ids[0]
        if candidate_id in consumed_candidates:
            raise ValueError("strategic Reference candidate is consumed more than once")
        consumed_candidates.add(candidate_id)
        candidate_set = unit["candidate_set"]
        assert isinstance(candidate_set, dict)
        candidates = candidate_set.get("candidates")
        records = unit.get("candidate_records")
        if not isinstance(candidates, list) or not isinstance(records, list):
            raise ValueError("strategic Reference candidate source is malformed")
        candidate = next(
            (
                item
                for item in candidates
                if isinstance(item, dict)
                and item.get("candidate_id") == candidate_id
            ),
            None,
        )
        record = next(
            (
                item
                for item in records
                if isinstance(item, dict)
                and isinstance(item.get("candidate"), dict)
                and item["candidate"].get("candidate_id") == candidate_id
            ),
            None,
        )
        if candidate is None or record is None or record.get("candidate") != candidate:
            raise ValueError(
                "strategic Reference selected candidate lacks one exact source record"
            )
        physical_id = record.get("physical_alignment_id")
        registry = physical_by_id.get(str(physical_id))
        if (
            not isinstance(physical_id, str)
            or registry is None
            or candidate_id not in registry.get("candidate_ids", [])
            or unit.get("unit_role") not in registry.get("role_memberships", [])
        ):
            raise ValueError(
                "strategic Reference candidate physical alignment registry is stale"
            )
        unit_role = StrategicCorridorUnitRole(str(unit["unit_role"]))
        classification = classification_by_set.get(candidate_set_id)
        if (
            unit_role is StrategicCorridorUnitRole.INTERURBAN_SPINE
            and classification is CandidateSetDisposition.SUBSTITUTE_ALTERNATIVES
            and candidate_id in reference.selected_candidate_ids
        ):
            disposition = (
                StrategicReferenceApplicationDisposition.SELECTED_SUBSTITUTE
            )
        elif (
            unit_role
            is StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS
            and classification is CandidateSetDisposition.COMPLEMENTARY_REQUIRED
            and candidate_id in reference.complementary_candidate_ids
        ):
            disposition = (
                StrategicReferenceApplicationDisposition.COMPLEMENTARY_REQUIRED
            )
        else:
            raise ValueError(
                "strategic Reference selected/complementary disposition is stale"
            )
        endpoint = unit.get("endpoint_binding")
        geometry = candidate.get("geometry")
        registry_geometry = registry.get("geometry")
        if (
            not isinstance(endpoint, dict)
            or not isinstance(geometry, dict)
            or not isinstance(registry_geometry, dict)
        ):
            raise ValueError("strategic Reference endpoint/geometry source is malformed")
        candidate_geometry = CanonicalLineString.model_validate(geometry)
        bindings.append(
            StrategicReferenceCandidateBinding(
                unit_id=str(unit["unit_id"]),
                unit_role=unit_role,
                application_disposition=disposition,
                source_unit_fingerprint=_fingerprint(unit),
                candidate_set_id=candidate_set_id,
                candidate_set_fingerprint=str(
                    candidate_set["candidate_set_fingerprint"]
                ),
                resolution_fingerprint=resolution.resolution_fingerprint,
                selected_candidate_id=candidate_id,
                candidate_input_fingerprint=_fingerprint(candidate),
                endpoint_binding=StrategicReferenceEndpointBinding(
                    **endpoint,
                ),
                mandatory_network_place_ids=tuple(
                    candidate_set.get("mandatory_network_place_ids", [])
                ),
                mandatory_access_obligation_ids=tuple(
                    candidate_set.get("mandatory_access_obligation_ids", [])
                ),
                mandatory_strategic_destination_ids=tuple(
                    candidate_set.get(
                        "mandatory_strategic_destination_ids",
                        [],
                    )
                ),
                served_network_place_ids=tuple(
                    candidate.get("served_network_place_ids", [])
                ),
                served_access_obligation_ids=tuple(
                    candidate.get("served_access_obligation_ids", [])
                ),
                served_strategic_destination_ids=tuple(
                    candidate.get("served_strategic_destination_ids", [])
                ),
                routing_start_node_id=str(record["routing_start_node_id"]),
                routing_end_node_id=str(record["routing_end_node_id"]),
                routing_edge_ids=tuple(record["routing_edge_ids"]),
                reverse_routing_edge_ids=tuple(
                    record["reverse_routing_edge_ids"]
                ),
                geometry=candidate_geometry,
                geometry_fingerprint=candidate_geometry.fingerprint,
                physical_alignment_id=physical_id,
                registry_geometry=CanonicalLineString.model_validate(
                    registry_geometry
                ),
                registry_geometry_fingerprint=str(
                    registry["geometry_fingerprint"]
                ),
                generation_strategies=tuple(record["generation_strategies"]),
                source_ids=tuple(record["source_ids"]),
                evidence_ids=tuple(record["evidence_ids"]),
                candidate_record_fingerprint=_fingerprint(record),
            )
        )
    return tuple(sorted(bindings, key=lambda item: item.unit_id))
