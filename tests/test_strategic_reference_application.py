"""Contract-only Reference adoption and binding for strategic corridor units."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_alignment_selection import (
    criteria,
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
    ScenarioDecisionRecord,
)
from satn.evidence import mark_ncn_edges
from satn.network_selection import CandidateSourceClass
from satn.routing import RoadGraph
from satn.scenario_compilation import PreparedCriteriaLineage
from satn.strategic_corridors import (
    StrategicCorridorUnitRole,
    prepare_strategic_corridors,
)
from satn.strategic_criteria_scenario import (
    PreparedStrategicUnitCriteria,
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


def _resolved_reference_inputs(tmp_path: Path, monkeypatch):
    """Resolve only compiler-offered finite actions for the exact two units."""

    _, source, compiled, population, education = _compiled_inputs(tmp_path)
    preparation = compiled.strategic_corridor_preparation
    assert preparation is not None

    def exact_test_criteria(
        exact_preparation,
        unit,
        **_kwargs,
    ) -> PreparedStrategicUnitCriteria:
        # This isolates the Reference contract from the known production
        # school-option evidence gap. Every candidate retains its exact
        # compiler-authored geometry and Candidate Set; only the criterion
        # evidence packet is a deterministic test adapter.
        packet = criteria(
            unit.candidate_set,
            gradient={
                candidate.candidate_id: "unknown"
                for candidate in unit.candidate_set.admitted_candidates
            },
        )
        return PreparedStrategicUnitCriteria(
            unit_id=unit.unit_id,
            unit_role=unit.unit_role,
            criteria=packet,
            preparation_lineage=PreparedCriteriaLineage.from_preparation(
                exact_preparation
            ),
        )

    monkeypatch.setattr(
        scenario_module,
        "_assemble_unit",
        exact_test_criteria,
    )
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
    assert provisional.review_orchestration is not None
    selections = {
        item.candidate_set_id: item for item in provisional.scenario.selections
    }
    envelopes = []
    for state in provisional.review_orchestration.actionable_requests:
        request = state.request
        selection = selections[request.candidate_set_id]
        if (
            selection.candidate_set.network_role.value
            == StrategicCorridorUnitRole.INTERURBAN_SPINE.value
        ):
            candidate = next(
                item
                for item in selection.candidate_set.admitted_candidates
                if item.source_class
                is CandidateSourceClass.VERIFIED_EXISTING_ASSET
            )
        else:
            candidate = selection.candidate_set.admitted_candidates[0]
            assert candidate.source_class is CandidateSourceClass.A_ROAD_CORRIDOR
        option = next(
            item
            for item in request.options
            if item.action.value == "select-eligible-option"
            and item.candidate_id == candidate.candidate_id
        )
        response = AlignmentDecisionResponse(
            request_id=request.request_id,
            request_fingerprint=request.request_fingerprint,
            option_id=option.option_id,
            evidence_ids=("network-assessment",),
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
            evidence_ids=("network-assessment",),
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
    monkeypatch,
) -> None:
    _, accepted, reference, preparation = _resolved_reference_inputs(
        tmp_path,
        monkeypatch,
    )

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


def test_unresolved_or_missing_campus_scenario_cannot_be_adopted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provisional, _, reference, _ = _resolved_reference_inputs(
        tmp_path,
        monkeypatch,
    )
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
    monkeypatch,
    field: str,
    value: str,
) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(
        tmp_path,
        monkeypatch,
    )
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
    monkeypatch,
    mutation: str,
) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(
        tmp_path,
        monkeypatch,
    )
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
    monkeypatch,
    mutation: str,
) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(
        tmp_path,
        monkeypatch,
    )
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
    monkeypatch,
) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(
        tmp_path,
        monkeypatch,
    )
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
    monkeypatch,
) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(
        tmp_path,
        monkeypatch,
    )
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
    monkeypatch,
) -> None:
    _, _, reference, preparation = _resolved_reference_inputs(
        tmp_path,
        monkeypatch,
    )
    plan = build_strategic_reference_application_plan(reference, preparation)
    left, right = plan.bindings
    shared = right.model_copy(
        update={"physical_alignment_id": left.physical_alignment_id}
    )

    assert StrategicReferenceApplicationPlan.canonical_bindings(
        (left, shared)
    ) == (left, shared)
