from __future__ import annotations

import copy

from bath_saltford_fixture import configured_bath_saltford
from shapely.geometry import LineString

from satn.content_identity import canonical_network_geometry_fingerprint, content_fingerprint
from satn.planning_engine import (
    apply_operation,
    build_planning_problem,
    expand_connection,
    initial_proposal,
    replay_expansion,
    semantic_fingerprint,
    validate_proposal,
)
from satn.sources import snapshot


def test_build_planning_problem_admits_configured_source_families_with_provenance(
    tmp_path,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)

    problem = build_planning_problem(config)
    corridors = problem["source_corridors"]

    assert problem["schema_version"] == "planning-problem/v1"
    assert problem["brief"]["corridor_policy"]["a_roads"] == (
        "mandatory-in-scope-retain-source-geometry"
    )
    assert problem["brief_fingerprint"]
    assert {item["classification"] for item in corridors} >= {
        "a-road",
        "ncn-route",
        "cycleway",
    }
    assert corridors
    for corridor in corridors:
        assert corridor["source_refs"]
        assert corridor["geometry_ref"]["crs"]
        assert corridor["geometry_ref"]["content_fingerprint"]
        assert corridor["provenance"]["source_refs"] == corridor["source_refs"]
        assert corridor["provenance"]["evidence_refs"]


def test_operations_bind_named_places_and_select_a_graph_alignment(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    state = initial_proposal(problem)
    mandatory_corridor = next(
        item for item in problem["source_corridors"] if item["mandatory_planning_corridor"]
    )
    candidate = next(
        item
        for item in problem["candidates"]
        if item["source_corridor_refs"] == [mandatory_corridor["corridor_id"]]
    )

    connected = apply_operation(
        problem,
        state,
        {
            "operation_id": "connect-bath-saltford",
            "kind": "propose-connection",
            "parent_state_fingerprint": state["state_fingerprint"],
            "payload": {
                "origin_place_id": "bath-edge",
                "destination_place_id": "saltford",
                "corridor_refs": [mandatory_corridor["corridor_id"]],
                "current_or_future": "future",
            },
        },
    )
    assert connected["connection_intents"][0]["origin_place_id"] == "bath-edge"
    assert connected["connection_intents"][0]["destination_place_id"] == "saltford"

    selected = apply_operation(
        problem,
        connected,
        {
            "operation_id": "select-a4-alignment",
            "kind": "select-alignment",
            "parent_state_fingerprint": connected["state_fingerprint"],
            "payload": {
                "candidate_id": candidate["candidate_id"],
                "obligation_id": mandatory_corridor["corridor_id"],
            },
        },
    )
    assert selected["selected_alignments"][0]["candidate_id"] == candidate["candidate_id"]
    assert selected["selected_alignments"][0]["graph_path"]["directed_edge_ids"]
    assert selected["selected_alignments"][0]["geometry_ref"]["content_fingerprint"]
    assert selected["selected_alignments"][0]["current_or_future"] == "unknown"

    output = validate_proposal(problem, selected)
    assert output["status"] == "reviewable-incomplete"
    assert output["validation"]["diagnostics"] == []
    assert output["unresolved_obligation_refs"]


def test_partial_and_full_departures_keep_explicit_source_geometry_and_accounting(
    tmp_path,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    state = initial_proposal(problem)
    mandatory_corridor = next(
        item for item in problem["source_corridors"] if item["mandatory_planning_corridor"]
    )
    alternate = next(
        item
        for item in problem["candidates"]
        if item["source_corridor_refs"] == [mandatory_corridor["corridor_id"]]
    )
    selected = apply_operation(
        problem,
        state,
        {
            "kind": "select-alignment",
            "parent_state_fingerprint": state["state_fingerprint"],
            "payload": {
                "candidate_id": alternate["candidate_id"],
                "obligation_id": mandatory_corridor["corridor_id"],
            },
        },
    )
    assert selected["selected_alignments"][0]["candidate_id"] == alternate["candidate_id"]
    partial_coordinates = [[-2.39, 51.37], [-2.39, 51.377]]
    partial_fingerprint = canonical_network_geometry_fingerprint(
        LineString(partial_coordinates), "EPSG:4326"
    )
    explicit_partial_geometry = {
        "geometry_id": f"geometry-{content_fingerprint(partial_fingerprint)}",
        "crs": "EPSG:4326",
        "geometry_kind": "LineString",
        "content_fingerprint": partial_fingerprint,
        "source_ref": mandatory_corridor["source_refs"][0]["source_id"],
        "geometry": {
            "type": "LineString",
            "coordinates": partial_coordinates,
        },
    }
    stale_partial_geometry = copy.deepcopy(explicit_partial_geometry)
    stale_partial_geometry["geometry_id"] = "planning-partial-a4"
    stale_partial_geometry["content_fingerprint"] = "explicit-partial-evidence"
    stale = apply_operation(
        problem,
        selected,
        {
            "kind": "record-departure",
            "parent_state_fingerprint": selected["state_fingerprint"],
            "payload": {
                "source_corridor_refs": [mandatory_corridor["corridor_id"]],
                "extent": "partial",
                "affected_geometry_refs": [stale_partial_geometry],
                "reason": "protected side path unavailable on this section",
                "evidence_refs": mandatory_corridor["evidence_refs"],
                "outcome": {
                    "kind": "alternate",
                    "candidate_id": alternate["candidate_id"],
                },
            },
        },
    )
    assert stale["status"] == "invalid"
    assert stale["diagnostics"][0]["code"] == "departure-geometry"
    partial = apply_operation(
        problem,
        selected,
        {
            "kind": "record-departure",
            "parent_state_fingerprint": selected["state_fingerprint"],
            "payload": {
                "source_corridor_refs": [mandatory_corridor["corridor_id"]],
                "extent": "partial",
                "affected_geometry_refs": [explicit_partial_geometry],
                "reason": "protected side path unavailable on this section",
                "evidence_refs": mandatory_corridor["evidence_refs"],
                "outcome": {
                    "kind": "alternate",
                    "candidate_id": alternate["candidate_id"],
                },
            },
        },
    )
    assert partial["departures"][0]["extent"] == "partial"
    assert partial["departures"][0]["affected_geometry_refs"] == [explicit_partial_geometry]
    assert partial["source_corridors"] == state["source_corridors"]

    full_corridor = next(
        item for item in problem["source_corridors"] if not item["mandatory_planning_corridor"]
    )
    gap_state = apply_operation(
        problem,
        partial,
        {
            "kind": "record-gap",
            "parent_state_fingerprint": partial["state_fingerprint"],
            "payload": {
                "gap_id": "gap-current-route",
                "target_refs": [full_corridor["corridor_id"]],
                "reason": "current route evidence is insufficient",
            },
        },
    )
    full = apply_operation(
        problem,
        gap_state,
        {
            "kind": "record-departure",
            "parent_state_fingerprint": gap_state["state_fingerprint"],
            "payload": {
                "source_corridor_refs": [full_corridor["corridor_id"]],
                "extent": "full",
                "reason": "current route evidence is insufficient",
                "evidence_refs": full_corridor["evidence_refs"],
                "outcome": {"kind": "unresolved", "gap_id": "gap-current-route"},
            },
        },
    )
    assert full["departures"][1]["extent"] == "full"
    assert full["departures"][1]["affected_geometry_refs"] == [full_corridor["geometry_ref"]]
    assert full["source_corridors"] == state["source_corridors"]


def test_alternate_departure_requires_selected_alignment(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    state = initial_proposal(problem)
    mandatory_corridor = next(
        item for item in problem["source_corridors"] if item["mandatory_planning_corridor"]
    )
    candidate = next(
        item
        for item in problem["candidates"]
        if item["source_corridor_refs"] == [mandatory_corridor["corridor_id"]]
    )

    result = apply_operation(
        problem,
        state,
        {
            "kind": "record-departure",
            "parent_state_fingerprint": state["state_fingerprint"],
            "payload": {
                "source_corridor_refs": [mandatory_corridor["corridor_id"]],
                "extent": "full",
                "reason": "the current provision cannot carry the governed connection",
                "evidence_refs": mandatory_corridor["evidence_refs"],
                "outcome": {
                    "kind": "alternate",
                    "candidate_id": candidate["candidate_id"],
                },
            },
        },
    )

    assert result["status"] == "invalid"
    assert result["diagnostics"][0]["code"] == "alternate-selection"

    forged = copy.deepcopy(state)
    forged["departures"] = [
        {
            "departure_id": "forged-alternate-departure",
            "source_corridor_refs": [mandatory_corridor["corridor_id"]],
            "extent": "full",
            "affected_geometry_refs": [mandatory_corridor["geometry_ref"]],
            "reason": "the current provision cannot carry the governed connection",
            "evidence_refs": mandatory_corridor["evidence_refs"],
            "outcome": {"kind": "alternate", "candidate_id": candidate["candidate_id"]},
        }
    ]
    forged["obligation_dispositions"][mandatory_corridor["corridor_id"]] = "departure:alternate"
    forged["state_fingerprint"] = semantic_fingerprint(forged)
    output = validate_proposal(problem, forged)
    assert output["status"] == "invalid"
    assert any(
        item["code"] == "alternate-selection" for item in output["validation"]["diagnostics"]
    )


def test_invalid_or_stale_operations_are_rejected_and_no_progress_is_semantic(
    tmp_path,
) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    state = initial_proposal(problem)

    invalid = apply_operation(
        problem,
        state,
        {
            "kind": "propose-connection",
            "parent_state_fingerprint": state["state_fingerprint"],
            "payload": {
                "origin_place_id": "missing-place",
                "destination_place_id": "saltford",
            },
        },
    )
    assert invalid["status"] == "invalid"
    assert invalid["diagnostics"][0]["code"] == "unknown-place"

    stale = apply_operation(
        problem,
        state,
        {
            "kind": "request-evidence",
            "parent_state_fingerprint": "stale-state",
            "payload": {"target_refs": ["saltford"], "claim": "currentness"},
        },
    )
    assert stale["status"] == "invalid"
    assert stale["diagnostics"][0]["code"] == "stale-state"

    request = {
        "kind": "request-evidence",
        "parent_state_fingerprint": state["state_fingerprint"],
        "payload": {"target_refs": ["saltford"], "claim": "currentness"},
    }
    requested = apply_operation(problem, state, request)
    repeated = apply_operation(
        problem,
        requested,
        {**request, "parent_state_fingerprint": requested["state_fingerprint"]},
    )
    assert repeated["status"] == "no-progress"
    assert repeated["diagnostics"][0]["code"] == "no-progress"

    with_noise = copy.deepcopy(state)
    with_noise["timestamp"] = "2030-01-01T00:00:00Z"
    with_noise["history"] = [{"history_event_id": "event-2"}]
    with_noise["provider_request_id"] = "provider-2"
    assert with_noise["state_fingerprint"] == state["state_fingerprint"]

    initial_output = validate_proposal(problem, state)
    assert initial_output["status"] == "reviewable-incomplete"
    assert any(
        item["code"] == "mandatory-source-unaccounted"
        for item in initial_output["validation"]["diagnostics"]
    )


def test_named_connection_expansion_materialises_graph_alternatives(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    state = initial_proposal(problem)
    operation = {
        "kind": "propose-connection",
        "parent_state_fingerprint": state["state_fingerprint"],
        "payload": {
            "connection_id": "bath-edge-to-saltford",
            "origin_place_id": "bath-edge",
            "destination_place_id": "saltford",
            "current_or_future": "future",
        },
    }

    expanded = expand_connection(problem, state, operation, config)

    assert expanded["status"] == "expanded"
    child_problem = expanded["problem"]
    child_state = expanded["state"]
    assert child_problem["problem_id"] != problem["problem_id"]
    assert child_state["parent_problem_id"] == child_problem["problem_id"]
    connection_candidates = [
        item
        for item in child_problem["candidates"]
        if item.get("connection_id") == "bath-edge-to-saltford"
    ]
    assert connection_candidates
    assert {tuple(item["place_refs"]) for item in connection_candidates} == {
        ("bath-edge", "saltford")
    }
    assert all(item["graph_path"]["directed_edge_ids"] for item in connection_candidates)
    assert all(
        item["endpoint_provenance"]["origin_node_id"]
        and item["endpoint_provenance"]["destination_node_id"]
        for item in connection_candidates
    )
    assert all(
        item["endpoint_provenance"]["origin_attachment_distance_m"] >= 0
        for item in connection_candidates
    )

    replayed = replay_expansion(problem, state, expanded["receipt"])
    assert replayed["status"] == "expanded"
    assert replayed["problem"] == child_problem
    assert replayed["state"] == child_state


def test_full_departure_recomputes_geometry_identity(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    state = initial_proposal(problem)
    corridor = next(
        item for item in problem["source_corridors"] if item["mandatory_planning_corridor"]
    )
    candidate = next(
        item
        for item in problem["candidates"]
        if item["source_corridor_refs"] == [corridor["corridor_id"]]
    )
    selected = apply_operation(
        problem,
        state,
        {
            "kind": "select-alignment",
            "parent_state_fingerprint": state["state_fingerprint"],
            "payload": {
                "candidate_id": candidate["candidate_id"],
                "obligation_id": corridor["corridor_id"],
            },
        },
    )
    forged_geometry = copy.deepcopy(corridor["geometry_ref"])
    forged_geometry["geometry"]["coordinates"][-1][1] = 51.377

    result = apply_operation(
        problem,
        selected,
        {
            "kind": "record-departure",
            "parent_state_fingerprint": selected["state_fingerprint"],
            "payload": {
                "source_corridor_refs": [corridor["corridor_id"]],
                "extent": "full",
                "affected_geometry_refs": [forged_geometry],
                "reason": "shortened geometry retains the original fingerprint",
                "evidence_refs": corridor["evidence_refs"],
                "outcome": {"kind": "alternate", "candidate_id": candidate["candidate_id"]},
            },
        },
    )

    assert result["status"] == "invalid"
    assert result["diagnostics"][0]["code"] == "departure-geometry"


def test_replay_rejects_foreign_expansion_bindings_and_facts(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    state = initial_proposal(problem)
    operation = {
        "kind": "propose-connection",
        "parent_state_fingerprint": state["state_fingerprint"],
        "payload": {
            "connection_id": "bath-edge-to-saltford",
            "origin_place_id": "bath-edge",
            "destination_place_id": "saltford",
            "current_or_future": "future",
        },
    }
    expanded = expand_connection(problem, state, operation, config)
    tampered_receipts = []
    foreign_snapshot = copy.deepcopy(expanded["receipt"])
    foreign_snapshot["snapshot_binding"]["snapshot_manifest_sha256"] = "foreign-manifest"
    tampered_receipts.append(foreign_snapshot)
    foreign_path = copy.deepcopy(expanded["receipt"])
    foreign_candidate = foreign_path["candidates"][0]
    foreign_candidate["graph_path"]["directed_edge_ids"] = ["foreign-edge"]
    foreign_candidate["endpoint_provenance"]["directed_edge_ids"] = ["foreign-edge"]
    tampered_receipts.append(foreign_path)
    forged_geometry = copy.deepcopy(expanded["receipt"])
    forged_geometry["candidates"][0]["geometry_ref"]["content_fingerprint"] = "fabricated-geometry"
    tampered_receipts.append(forged_geometry)

    for receipt in tampered_receipts:
        replayed = replay_expansion(problem, state, receipt)
        assert replayed["status"] == "invalid"


def test_operation_rejects_mutated_parent_brief_with_cached_fingerprint(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    state = initial_proposal(problem)
    mutated_state = copy.deepcopy(state)
    mutated_state["brief"]["corridor_policy"]["a_roads"] = "mutated-policy"

    result = apply_operation(
        problem,
        mutated_state,
        {
            "kind": "request-evidence",
            "parent_state_fingerprint": state["state_fingerprint"],
            "payload": {"target_refs": ["saltford"], "claim": "currentness"},
        },
    )

    assert result["status"] == "invalid"
    assert result["diagnostics"][0]["code"] == "brief-binding"


def test_semantic_fingerprint_deduplicates_request_content_across_request_ids(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    state = initial_proposal(problem)
    first = apply_operation(
        problem,
        state,
        {
            "kind": "request-evidence",
            "parent_state_fingerprint": state["state_fingerprint"],
            "payload": {
                "request_id": "request-a",
                "target_refs": ["saltford"],
                "claim": "currentness",
                "reason": "same claim",
            },
        },
    )
    second = apply_operation(
        problem,
        first,
        {
            "kind": "request-evidence",
            "parent_state_fingerprint": first["state_fingerprint"],
            "payload": {
                "request_id": "request-b",
                "target_refs": ["saltford"],
                "claim": "currentness",
                "reason": "same claim",
            },
        },
    )

    assert second["status"] == "no-progress"
    assert second["state_fingerprint"] == first["state_fingerprint"]


def test_selected_disposition_requires_matching_alignment_proof(tmp_path) -> None:
    config = configured_bath_saltford(tmp_path)
    snapshot(config)
    problem = build_planning_problem(config)
    state = initial_proposal(problem)
    problem["source_corridors"] = [
        {**corridor, "mandatory_planning_corridor": False}
        for corridor in problem["source_corridors"]
    ]
    state["source_corridors"] = copy.deepcopy(problem["source_corridors"])
    problem["planning_gaps"] = []
    problem["unknown_facts"] = []
    state["planning_gaps"] = []
    state["unknown_facts"] = []
    state["obligation_dispositions"] = {
        str(item["obligation_id"]): "selected" for item in problem["obligations"]
    }

    output = validate_proposal(problem, state)

    assert output["status"] == "invalid"
    assert any(
        item["code"] == "obligation-disposition-proof"
        for item in output["validation"]["diagnostics"]
    )
