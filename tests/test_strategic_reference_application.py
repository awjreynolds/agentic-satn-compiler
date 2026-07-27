"""Contract-only Reference adoption and binding for strategic corridor units."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_alignment_selection import (
    principal_assertion,
    reference_decision,
)
from test_strategic_criteria_scenario import (
    _area_fingerprint,
    _compiled_inputs,
)

import satn.strategic_criteria_scenario as scenario_module
from satn.alignment_selection import (
    AcceptedDecisionEnvelope,
    AlignmentCritiqueRecord,
    AlignmentDecisionResponse,
    DecisionProcessMode,
    NetworkRole,
    ScenarioDecisionRecord,
)
from satn.evidence import mark_ncn_edges
from satn.routing import RoadGraph
from satn.strategic_corridors import (
    StrategicCorridorUnitRole,
    prepare_strategic_corridors,
)
from satn.strategic_criteria_scenario import (
    StrategicCriteriaScenarioInput,
    compile_strategic_criteria_scenario,
)
from satn.strategic_reference_application import (
    StrategicReferenceApplicationDisposition,
    StrategicReferenceApplicationPlan,
    adopt_strategic_reference_satn,
    build_strategic_reference_application_plan,
    validate_fresh_strategic_reference_preparation,
)


def _interurban_only_preparation(preparation):
    """Construct a governed fixture with no admitted destination unit."""

    units = tuple(
        item
        for item in preparation.units
        if item.unit_role is StrategicCorridorUnitRole.INTERURBAN_SPINE
    )
    candidate_ids = {
        candidate.candidate_id
        for unit in units
        for candidate in unit.candidate_set.candidates
    }
    physical_alignments = tuple(
        item
        for item in preparation.physical_alignments
        if candidate_ids.intersection(item.candidate_ids)
    )
    provisional = replace(
        preparation,
        units=units,
        physical_alignments=physical_alignments,
        preparation_fingerprint="",
    )
    return replace(
        provisional,
        preparation_fingerprint=scenario_module._fingerprint(
            provisional.canonical_payload()
        ),
    )


def _resolved_reference_inputs(tmp_path: Path, *, interurban_only: bool = False):
    """Resolve only compiler-offered actions from exact governed evidence."""

    _, source, compiled, population, education = _compiled_inputs(tmp_path)
    preparation = compiled.strategic_corridor_preparation
    assert preparation is not None
    if interurban_only:
        preparation = _interurban_only_preparation(preparation)
    base_request = StrategicCriteriaScenarioInput(
        preparation=preparation,
        population_evidence=population,
        education_evidence=education,
        area_definition=source["boundary"],
        area_fingerprint=_area_fingerprint(source),
    )
    provisional = compile_strategic_criteria_scenario(base_request)
    assert provisional.status == "review-required"
    assert provisional.scenario is not None
    assert {
        selection.disposition.value
        for selection in provisional.scenario.selections
    } == {"provisional-review"}
    assert provisional.status == "review-required"
    assert provisional.review_orchestration is not None
    assert all(
        any(
            option.action.value == "select-eligible-option"
            for option in state.request.options
        )
        for state in provisional.review_orchestration.actionable_requests
    )
    selections = {
        item.candidate_set_id: item for item in provisional.scenario.selections
    }
    envelopes = []
    for state in provisional.review_orchestration.actionable_requests:
        request = state.request
        selection = selections[request.candidate_set_id]
        option = next(
            item
            for item in request.options
            if item.action.value == "select-eligible-option"
        )
        assert option.candidate_id is not None
        evidence_ids = request.immutable_evidence_ids[:1]
        assert evidence_ids
        response = AlignmentDecisionResponse(
            request_id=request.request_id,
            request_fingerprint=request.request_fingerprint,
            option_id=option.option_id,
            evidence_ids=evidence_ids,
            invocation=principal_assertion(
                request,
                option_id=option.option_id,
            ),
        )
        critique = AlignmentCritiqueRecord(
            request_fingerprint=request.request_fingerprint,
            response_fingerprint=response.response_fingerprint,
            selection_fingerprint=selection.selection_fingerprint,
            scenario_context_fingerprint=(
                provisional.scenario.scenario_context_fingerprint
            ),
            evidence_snapshot_fingerprint=(
                request.evidence_snapshot_fingerprint
            ),
            profile_fingerprint=request.profile_fingerprint,
            finding="accepted",
            resolved=True,
            evidence_ids=evidence_ids,
            invocation=principal_assertion(
                request,
                critic=True,
                response_fingerprint=response.response_fingerprint,
            ),
        )
        envelopes.append(
            AcceptedDecisionEnvelope(
                request=request,
                response=response,
                critique=critique,
            )
        )

    accepted = compile_strategic_criteria_scenario(
        StrategicCriteriaScenarioInput(
            preparation=preparation,
            population_evidence=population,
            education_evidence=education,
            area_definition=source["boundary"],
            area_fingerprint=_area_fingerprint(source),
            decision_record=ScenarioDecisionRecord(
                mode=DecisionProcessMode.ACCEPTED_LEDGER,
                accepted_envelopes=tuple(envelopes),
            ),
        )
    )
    assert accepted.status == "compiled"
    assert accepted.scenario is not None and accepted.scenario.publishable
    reference = adopt_strategic_reference_satn(
        accepted,
        governed_decision=reference_decision(accepted.scenario),
    )
    return provisional, accepted, reference, preparation


def _reseal(payload: dict[str, object], binding_index: int | None = None) -> None:
    payload["plan_fingerprint"] = ""
    if binding_index is not None:
        bindings = payload["bindings"]
        assert isinstance(bindings, list)
        binding = bindings[binding_index]
        assert isinstance(binding, dict)
        binding["binding_fingerprint"] = ""


def test_human_adopts_exact_resolved_strategic_scenario_and_builds_plan(
    tmp_path: Path,
) -> None:
    _, accepted, reference, preparation = _resolved_reference_inputs(tmp_path)

    first = build_strategic_reference_application_plan(reference, preparation)
    second = build_strategic_reference_application_plan(reference, preparation)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert first.authoritative_network_geometry_mutated is False
    assert first.publication_created is False
    assert first.scenario_fingerprint == accepted.scenario.scenario_fingerprint
    assert first.reference_selection_fingerprint == (
        reference.reference_selection_fingerprint
    )
    assert first.reference_decision_fingerprint == (
        reference.governed_decision.decision_fingerprint
    )
    assert first.preparation_fingerprint == preparation.preparation_fingerprint
    assert first.preparation_evidence_fingerprints == (
        preparation.evidence_fingerprints
    )
    assert len(first.bindings) == 2
    by_role = {item.unit_role: item for item in first.bindings}
    interurban = by_role[StrategicCorridorUnitRole.INTERURBAN_SPINE]
    destination = by_role[
        StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS
    ]
    assert (
        interurban.application_disposition
        is StrategicReferenceApplicationDisposition.SELECTED_SUBSTITUTE
    )
    assert interurban.selected_candidate_id in reference.selected_candidate_ids
    assert interurban.endpoint_binding.network_place_ids == (
        "bath-edge",
        "saltford",
    )
    assert interurban.mandatory_network_place_ids == (
        "bath-edge",
        "saltford",
    )
    assert interurban.routing_edge_ids and interurban.reverse_routing_edge_ids
    assert (
        destination.application_disposition
        is StrategicReferenceApplicationDisposition.COMPLEMENTARY_REQUIRED
    )
    assert (
        destination.selected_candidate_id
        in reference.complementary_candidate_ids
    )
    assert destination.endpoint_binding.network_place_ids == ("bath-edge",)
    assert not destination.mandatory_network_place_ids
    assert not destination.served_network_place_ids
    assert destination.mandatory_strategic_destination_ids == (
        "bath-spa-university",
    )
    assert destination.served_strategic_destination_ids == (
        "bath-spa-university",
    )
    assert destination.routing_edge_ids == ("a4-campus-forward",)
    assert destination.reverse_routing_edge_ids == ("a4-campus-reverse",)
    assert destination.geometry == destination.registry_geometry


def test_interurban_only_scenario_is_adopted_without_inventing_destination_access(
    tmp_path: Path,
) -> None:
    _, accepted, reference, preparation = _resolved_reference_inputs(
        tmp_path,
        interurban_only=True,
    )

    assert accepted.scenario is not None
    assert accepted.scenario.required_network_role_ids == (
        NetworkRole.INTERURBAN_SPINE,
    )
    plan = build_strategic_reference_application_plan(reference, preparation)
    assert len(plan.bindings) == 1
    assert plan.bindings[0].unit_role is StrategicCorridorUnitRole.INTERURBAN_SPINE
    assert (
        plan.bindings[0].application_disposition
        is StrategicReferenceApplicationDisposition.SELECTED_SUBSTITUTE
    )


def test_unresolved_or_missing_campus_scenario_cannot_be_adopted(
    tmp_path: Path,
) -> None:
    provisional, _, reference, _ = _resolved_reference_inputs(tmp_path)
    assert provisional.scenario is not None
    with pytest.raises(ValueError, match="fully resolved"):
        adopt_strategic_reference_satn(
            provisional,
            governed_decision=reference_decision(provisional.scenario),
        )

    config, source, compiled, population, education = _compiled_inputs(
        tmp_path / "missing-campus"
    )
    context = source["context"].drop(columns=["access_point_evidence_ids"])
    preparation = prepare_strategic_corridors(
        config.compilation.network_selection,
        road_graph=RoadGraph(
            mark_ncn_edges(source["network"], source["context"])
        ),
        spine_access_connections=compiled.spine_access_connections,
        context=context,
        source_config=config.source,
        config_directory=config.config_path.parent,
    )
    incomplete = scenario_module.compile_strategic_criteria_scenario(
        StrategicCriteriaScenarioInput(
            preparation=preparation,
            population_evidence=population,
            education_evidence=education,
            area_definition=source["boundary"],
            area_fingerprint=_area_fingerprint(source),
        )
    )
    assert incomplete.scenario is None
    with pytest.raises(ValueError, match="fully resolved"):
        adopt_strategic_reference_satn(
            incomplete,
            governed_decision=reference.governed_decision,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("profile_fingerprint", "0" * 64),
        ("scenario_fingerprint", "1" * 64),
        ("reference_selection_fingerprint", "2" * 64),
        ("reference_decision_fingerprint", "3" * 64),
        ("evidence_snapshot_fingerprint", "4" * 64),
        ("area_fingerprint", "5" * 64),
        ("selection_run_fingerprint", "6" * 64),
    ),
)
def test_plan_rejects_stale_global_identity_even_when_caller_reseals(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    payload = build_strategic_reference_application_plan(
        reference,
        preparation,
    ).model_dump(mode="json")
    payload[field] = value
    _reseal(payload)

    with pytest.raises(ValidationError, match="identity is stale"):
        StrategicReferenceApplicationPlan.model_validate(payload)


@pytest.mark.parametrize(
    "mutation",
    (
        "reverse-edge",
        "routing-node",
        "endpoint-binding",
        "destination-association",
        "physical-registry",
        "geometry",
        "disposition",
    ),
)
def test_binding_rejects_adversarial_resealing_against_source_lineage(
    tmp_path: Path,
    mutation: str,
) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    plan = build_strategic_reference_application_plan(reference, preparation)
    payload = plan.model_dump(mode="json")
    bindings = payload["bindings"]
    assert isinstance(bindings, list)
    destination_index = next(
        index
        for index, item in enumerate(bindings)
        if item["unit_role"] == "strategic-destination-access"
    )
    interurban_index = 1 - destination_index
    destination = bindings[destination_index]
    interurban = bindings[interurban_index]
    assert isinstance(destination, dict) and isinstance(interurban, dict)
    if mutation == "reverse-edge":
        destination["reverse_routing_edge_ids"] = ["foreign-reverse"]
    elif mutation == "routing-node":
        destination["routing_start_node_id"] = "foreign-node"
        endpoint = destination["endpoint_binding"]
        assert isinstance(endpoint, dict)
        endpoint["routing_node_ids"][0] = "foreign-node"
        endpoint["binding_fingerprint"] = ""
    elif mutation == "endpoint-binding":
        endpoint = destination["endpoint_binding"]
        assert isinstance(endpoint, dict)
        endpoint["network_place_ids"] = ["foreign-anchor"]
        endpoint["binding_fingerprint"] = ""
    elif mutation == "destination-association":
        endpoint = destination["endpoint_binding"]
        assert isinstance(endpoint, dict)
        endpoint["strategic_destination_ids"] = ["foreign-campus"]
        endpoint["binding_fingerprint"] = ""
        destination["mandatory_strategic_destination_ids"] = [
            "foreign-campus"
        ]
        destination["served_strategic_destination_ids"] = ["foreign-campus"]
    elif mutation in {"physical-registry", "geometry"}:
        destination["geometry"] = interurban["geometry"]
        destination["geometry_fingerprint"] = interurban[
            "geometry_fingerprint"
        ]
        destination["registry_geometry"] = interurban["registry_geometry"]
        destination["registry_geometry_fingerprint"] = interurban[
            "registry_geometry_fingerprint"
        ]
        if mutation == "physical-registry":
            destination["physical_alignment_id"] = interurban[
                "physical_alignment_id"
            ]
    else:
        destination["application_disposition"] = "selected-substitute"
    _reseal(payload, destination_index)

    with pytest.raises(ValidationError):
        StrategicReferenceApplicationPlan.model_validate(payload)


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "foreign"))
def test_plan_requires_every_unit_and_candidate_exactly_once(
    tmp_path: Path,
    mutation: str,
) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    payload = build_strategic_reference_application_plan(
        reference,
        preparation,
    ).model_dump(mode="json")
    bindings = payload["bindings"]
    assert isinstance(bindings, list)
    if mutation == "missing":
        bindings.pop()
    elif mutation == "duplicate":
        bindings[1] = dict(bindings[0])
    else:
        bindings[0]["unit_id"] = "alignment-unit-00000000000000000000"
        bindings[0]["binding_fingerprint"] = ""
    _reseal(payload)

    with pytest.raises(ValidationError):
        StrategicReferenceApplicationPlan.model_validate(payload)


def test_resealed_source_preparation_cannot_escape_reference_lineage(
    tmp_path: Path,
) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    payload = build_strategic_reference_application_plan(
        reference,
        preparation,
    ).model_dump(mode="json")
    source = json.loads(payload["source_preparation_json"])
    source["canonical_payload"]["units"][0]["candidate_records"][0][
        "reverse_routing_edge_ids"
    ] = ["caller-resealed-edge"]
    source["preparation_fingerprint"] = scenario_module._fingerprint(
        source["canonical_payload"]
    )
    payload["source_preparation_json"] = json.dumps(
        source,
        sort_keys=True,
        separators=(",", ":"),
    )
    payload["preparation_fingerprint"] = source["preparation_fingerprint"]
    _reseal(payload)

    with pytest.raises(
        ValidationError,
        match="preparation, profile and criteria lineage",
    ):
        StrategicReferenceApplicationPlan.model_validate(payload)


def test_fresh_preparation_requires_exact_equality(
    tmp_path: Path,
) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    assert (
        validate_fresh_strategic_reference_preparation(
            reference,
            preparation,
            preparation,
        ).preparation_fingerprint
        == preparation.preparation_fingerprint
    )
    with pytest.raises(ValueError, match="fingerprint is stale"):
        validate_fresh_strategic_reference_preparation(
            reference,
            preparation,
            replace(preparation, preparation_fingerprint="0" * 64),
        )


def test_shared_physical_registry_identity_is_allowed_for_distinct_roles(
    tmp_path: Path,
) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(tmp_path)
    plan = build_strategic_reference_application_plan(reference, preparation)
    left, right = plan.bindings
    shared = right.model_copy(
        update={"physical_alignment_id": left.physical_alignment_id}
    )

    assert StrategicReferenceApplicationPlan.canonical_bindings(
        (left, shared)
    ) == (left, shared)
