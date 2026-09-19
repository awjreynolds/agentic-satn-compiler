"""Prepare and optionally execute the three-case B&NES TypeSafe evaluation.

The default mode is preparation only.  It admits the pinned local snapshot,
materialises fresh named-place connection candidates through the planning engine,
and writes immutable input packets and expansion receipts.  ``--mode live`` is
the only mode that constructs a real TypeSafe client or sends a request.

The output directory is intentionally create-once.  A second invocation must use
a new output path so an earlier packet or receipt cannot be overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from pyproj import Transformer
from shapely.geometry import mapping as geometry_mapping
from shapely.geometry import shape
from shapely.ops import transform

from satn.models import AreaConfig, AreaDefinition
from satn.planning_engine import (
    apply_operation,
    build_planning_problem,
    expand_connection,
    initial_proposal,
    replay_expansion,
    validate_proposal,
)
from satn.typesafe_planning import (
    ChoiceQuestion,
    NoulQuestion,
    ScoreQuestion,
    TypeSafeClient,
    TypeSafeProvider,
)

SCHEMA_VERSION = "typesafe-planning-evaluation/v1"
DEFAULT_MODEL = "jev-latest"
DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
CONTRACT_PATHS = (
    Path("/tmp/satn-typesafe-wayfinder/evaluation-resolution.md"),
    Path("/tmp/satn-typesafe-wayfinder/brief-resolution.md"),
    Path("/tmp/satn-typesafe-wayfinder/history-resolution.md"),
    Path("/tmp/satn-typesafe-wayfinder/routing-resolution.md"),
)

# These are current snapshot place identities, deliberately independent of the
# legacy recovery unit/candidate identifiers.  The runner checks their names
# before using them and records the complete source place records in each packet.
CASE_SPECS: tuple[dict[str, str], ...] = (
    {
        "case_id": "bath-keynsham",
        "label": "Bath Spa station to Keynsham community",
        "anchor_note": (
            "The pinned snapshot admits Bath Spa as the Bath anchor; no Bath settlement "
            "place is substituted."
        ),
        "origin_place_id": "station-f6c377446e",
        "origin_name": "Bath Spa",
        "destination_place_id": "community-f44ae41191",
        "destination_name": "Keynsham",
    },
    {
        "case_id": "bath-radstock",
        "label": "Bath Spa station to Radstock community",
        "anchor_note": (
            "The pinned snapshot admits Bath Spa as the Bath anchor; no Bath settlement "
            "place is substituted."
        ),
        "origin_place_id": "station-f6c377446e",
        "origin_name": "Bath Spa",
        "destination_place_id": "community-005f93c6ed",
        "destination_name": "Radstock",
    },
    {
        "case_id": "radstock-midsomer-norton",
        "label": "Radstock community to Midsomer Norton community",
        "anchor_note": "Both endpoints use admitted community places from the pinned snapshot.",
        "origin_place_id": "community-005f93c6ed",
        "origin_name": "Radstock",
        "destination_place_id": "community-51ef7bb1ee",
        "destination_name": "Midsomer Norton",
    },
)


def _json_copy(value: object) -> object:
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False))


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _output_root(path: Path) -> Path:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing evaluation output: {path}")
    path.mkdir(parents=True, exist_ok=False)
    return path


def _place_index(problem: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    places = problem.get("places", [])
    if not isinstance(places, list):
        return {}
    return {
        str(place["place_id"]): place
        for place in places
        if isinstance(place, Mapping) and place.get("place_id")
    }


def _check_case_places(
    problem: Mapping[str, object], spec: Mapping[str, str]
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    places = _place_index(problem)
    selected: list[Mapping[str, object]] = []
    for identity, name_key in (
        ("origin_place_id", "origin_name"),
        ("destination_place_id", "destination_name"),
    ):
        place_id = spec[identity]
        place = places.get(place_id)
        if place is None or place.get("name") != spec[name_key]:
            actual = place.get("name") if place is not None else None
            raise ValueError(
                f"{spec['case_id']} place binding mismatch for {place_id}: "
                f"expected {spec[name_key]!r}, got {actual!r}"
            )
        selected.append(place)
    return selected[0], selected[1]


def _question_payloads(
    spec: Mapping[str, str], candidate_views: Sequence[Mapping[str, object]]
) -> dict[str, dict[str, object]]:
    criteria: dict[str, str] = {}
    for candidate in candidate_views:
        candidate_id = str(candidate["candidate_id"])
        criteria[candidate_id] = (
            f"Admitted {candidate.get('role')} alignment; "
            f"engine path length {candidate.get('graph_path', {}).get('length_km')} km; "
            f"provision status is {candidate.get('current_or_future')}; "
            "ordered graph edges: "
            f"{len(candidate.get('graph_path', {}).get('directed_edge_ids', []))}."
        )
    criteria.update(
        {
            "unknown-insufficient-evidence": (
                "A required fact for choosing among the supplied alignments is missing "
                "or conflicting."
            ),
            "no-currently-acceptable-option": (
                "The supplied evidence does not justify carrying any alignment forward."
            ),
        }
    )
    questions: dict[str, object] = {
        "alignment_choice": ChoiceQuestion(
            instructions=(
                f"For the named-place connection {spec['label']}, choose one admitted alignment "
                "from the supplied candidates. Use an explicit unresolved option when the evidence "
                "does not settle the choice. Do not invent a route, infer present usability from a "
                "designation, or treat the shortest path as an unstated policy preference."
            ),
            criteria=criteria,
        ),
        "current_or_future": ChoiceQuestion(
            instructions=(
                "Classify provision only from the explicit source evidence supplied in the packet. "
                "A graph path proves topology evidence, not present accessibility or feasibility."
            ),
            criteria={
                "current": "The admitted evidence explicitly establishes current provision.",
                "future": "The admitted evidence explicitly identifies a future intervention.",
                "unknown": "The supplied evidence does not establish current or future provision.",
            },
        ),
        "topology_continuity": NoulQuestion(
            instructions=(
                "Is the supplied ordered graph path and endpoint provenance sufficient to state "
                "topological connection between the two named endpoints? This is a topology claim "
                "only and must not be upgraded to current legal or usable access."
            ),
            criteria={
                "true": "The admitted path has ordered edges and bound endpoint nodes.",
                "false": "The supplied path or endpoint evidence is missing or unresolved.",
            },
        ),
        "directness": ScoreQuestion(
            instructions=(
                "Assess directness only as a comparison among the supplied engine candidates. "
                "Do not introduce a distance threshold or an unstated weighting."
            ),
            criteria=[
                "Not assessed from the supplied comparison evidence.",
                "Comparable directness among the supplied candidates.",
                "A material detour is visible in the supplied comparison evidence.",
            ],
        ),
        "elevation_coverage": ChoiceQuestion(
            instructions=(
                "Report whether route-specific gradient or elevation coverage is present in the "
                "packet. The existence of an elevation source file alone does not prove "
                "route coverage."
            ),
            criteria={
                "available": "Route-specific elevation evidence covers the relevant alignment.",
                "partial": "Elevation evidence covers only part of the relevant alignment.",
                "unavailable": "The packet contains no route-specific elevation coverage.",
            },
        ),
        "social_safety": NoulQuestion(
            instructions=(
                "Can the supplied packet support a social-safety or independent-access claim for "
                "the connection? Missing observed safety evidence remains unknown."
            ),
            criteria={
                "true": "The packet contains explicit relevant social-safety evidence.",
                "false": "The packet does not contain enough evidence for that claim.",
            },
        ),
        "decision_disposition": ChoiceQuestion(
            instructions=(
                "Choose whether the supplied evidence supports carrying one admitted alignment "
                "forward or requires human assessment. Human assessment has not been performed by "
                "this evaluation runner."
            ),
            criteria={
                "carry-forward": (
                    "One supplied alignment is supported by the stated evidence and policy."
                ),
                "requires-human-assessment": (
                    "The supplied evidence does not adjudicate the material trade-off."
                ),
                "unresolved": "A required fact or policy binding remains unresolved.",
            },
        ),
    }
    return {
        question_id: question.as_payload()  # type: ignore[union-attr]
        for question_id, question in questions.items()
    }


def _candidate_view(
    candidate: Mapping[str, object],
    *,
    config: AreaConfig,
    source_asset_status: Mapping[str, object],
) -> dict[str, object]:
    path = candidate.get("graph_path")
    endpoint = candidate.get("endpoint_provenance")
    if not isinstance(path, Mapping):
        path = {}
    if not isinstance(endpoint, Mapping):
        endpoint = {}
    return {
        **_json_copy(dict(candidate)),
        "evaluation_evidence": {
            "continuity": {
                "status": "graph-topology-evidence",
                "source": "planning-engine expansion",
                "origin_node_id": endpoint.get("origin_node_id"),
                "destination_node_id": endpoint.get("destination_node_id"),
                "origin_attachment_distance_m": endpoint.get("origin_attachment_distance_m"),
                "destination_attachment_distance_m": endpoint.get(
                    "destination_attachment_distance_m"
                ),
                "directed_edge_count": len(endpoint.get("directed_edge_ids", [])),
                "current_access_claim": "unknown",
            },
            "directness": {
                "status": "comparison-only",
                "length_km": path.get("length_km"),
                "comparison_fields": ["length_km", "a_road_share", "ncn_share"],
            },
            "elevation": {
                "status": "route-profile-unresolved",
                "source_file": source_asset_status.get("elevation_path"),
                "source_file_present": source_asset_status.get("elevation_present"),
                "coverage": "not-profiled-by-planning-engine",
                "binding_gap": (
                    "The admitted candidate graph path has no bound elevation sample IDs or "
                    "profile; source-file presence cannot establish route coverage."
                ),
            },
            "social_safety": {
                "status": "unknown",
                "reason": "No observed traffic or social-safety evidence is in this packet.",
                "atm_reference": source_asset_status.get("atm"),
            },
            "current_or_future": {
                "value": candidate.get("current_or_future"),
                "source_designation_is_not_current_usability": True,
            },
        },
        "human_assessment": {"performed": False, "status": "not-performed"},
        "specialist_capability": {
            "status": "capability-unavailable",
            "capability": "domain-specialist-planning",
        },
        "config_ref": str(config.config_path),
    }


def _source_asset_status(config: AreaConfig) -> dict[str, object]:
    elevation = config.source.national_elevation
    elevation_path = getattr(elevation, "path", None) if elevation is not None else None
    atm_path = config.atm.path
    return {
        "elevation_path": str(elevation_path) if elevation_path is not None else None,
        "elevation_present": bool(elevation_path and Path(elevation_path).is_file()),
        "atm": {
            "enabled": bool(config.atm.enabled),
            "path": str(atm_path) if atm_path is not None else None,
            "present": bool(atm_path and Path(atm_path).is_file()),
            "status": (
                "available-but-not-assessed"
                if atm_path is not None and Path(atm_path).is_file()
                else "optional-absent"
            ),
        },
    }


def _packet_state(
    spec: Mapping[str, str],
    problem: Mapping[str, object],
    state: Mapping[str, object],
    connection: Mapping[str, object],
    places: Sequence[Mapping[str, object]],
    candidate_views: Sequence[Mapping[str, object]],
    source_status: Mapping[str, object],
) -> dict[str, object]:
    return {
        "case": {
            "case_id": spec["case_id"],
            "label": spec["label"],
            "anchor_note": spec["anchor_note"],
            "coverage_scope": "named-place endpoint pair only",
            "recovery_artifact_comparison": "not-comparable",
            "origin_place_id": spec["origin_place_id"],
            "destination_place_id": spec["destination_place_id"],
        },
        "policy": _json_copy(problem.get("brief", {})),
        "problem_binding": {
            "problem_id": problem.get("problem_id"),
            "input_fingerprint": problem.get("input_fingerprint"),
            "brief_fingerprint": problem.get("brief_fingerprint"),
            "binding": _json_copy(problem.get("binding", {})),
            "state_fingerprint": state.get("state_fingerprint"),
        },
        "connection_intent": _json_copy(connection),
        "places": _json_copy(list(places)),
        "candidate_options": _json_copy(list(candidate_views)),
        "evidence_boundary": {
            "source_status": _json_copy(dict(source_status)),
            "unknowns": [
                "route-specific elevation coverage",
                "social safety and independent access",
                "present usability where candidate provision is unknown",
            ],
            "human_assessment_performed": False,
            "specialist_capability": "capability-unavailable",
        },
    }


def _candidate_geojson(
    spec: Mapping[str, str],
    places: Sequence[Mapping[str, object]],
    candidate_views: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    features: list[dict[str, object]] = []
    for place in places:
        reference = place.get("geometry_ref")
        if not isinstance(reference, Mapping):
            continue
        geometry = shape(reference["geometry"])
        if str(reference.get("crs")) != "EPSG:4326":
            geometry = transform(
                Transformer.from_crs(reference.get("crs"), "EPSG:4326", always_xy=True).transform,
                geometry,
            )
        features.append(
            {
                "type": "Feature",
                "id": f"place:{place.get('place_id')}",
                "properties": {
                    "feature_type": "planning-place",
                    "place_id": place.get("place_id"),
                    "name": place.get("name"),
                    "case_id": spec["case_id"],
                },
                "geometry": geometry_mapping(geometry),
            }
        )
    for candidate in candidate_views:
        reference = candidate.get("geometry_ref")
        if not isinstance(reference, Mapping):
            continue
        geometry = shape(reference["geometry"])
        if str(reference.get("crs")) != "EPSG:4326":
            geometry = transform(
                Transformer.from_crs(reference.get("crs"), "EPSG:4326", always_xy=True).transform,
                geometry,
            )
        features.append(
            {
                "type": "Feature",
                "id": f"candidate:{candidate.get('candidate_id')}",
                "properties": {
                    "feature_type": "planning-candidate",
                    "candidate_id": candidate.get("candidate_id"),
                    "role": candidate.get("role"),
                    "length_km": candidate.get("graph_path", {}).get("length_km"),
                    "current_or_future": candidate.get("current_or_future"),
                    "map_label": f"Candidate {candidate.get('role')}",
                },
                "geometry": geometry_mapping(geometry),
            }
        )
    return {"type": "FeatureCollection", "name": spec["label"], "features": features}


def _decision_fixture(
    case: Mapping[str, object],
    problem: Mapping[str, object],
    state: Mapping[str, object],
) -> dict[str, object]:
    candidates = [
        item
        for item in problem.get("candidates", [])
        if isinstance(item, Mapping) and item.get("connection_id") == case["connection_id"]
    ]
    by_role = {
        str(item.get("role")): item
        for item in candidates
        if item.get("role") in {"direct", "ncn-informed"}
    }
    if set(by_role) != {"direct", "ncn-informed"}:
        return {
            "status": "unavailable",
            "reason": "required direct and ncn-informed alternatives were not admitted",
        }
    branches: dict[str, object] = {}
    for branch_name, candidate in by_role.items():
        operation = {
            "kind": "select-alignment",
            "operation_id": f"evaluation-{case['case_id']}-{branch_name}",
            "parent_state_fingerprint": state["state_fingerprint"],
            "payload": {
                "candidate_id": candidate["candidate_id"],
                "obligation_id": case["connection_id"],
            },
        }
        child = apply_operation(problem, state, operation)
        branches[branch_name] = {
            "operation": operation,
            "status": child.get("status"),
            "state_fingerprint": child.get("state_fingerprint"),
            "selected_candidate_id": candidate["candidate_id"],
        }
    replay_operation = branches["direct"]["operation"]
    replayed = apply_operation(problem, state, replay_operation)
    changed_problem = _json_copy(problem)
    if isinstance(changed_problem, dict):
        binding = changed_problem.get("binding")
        if isinstance(binding, dict):
            binding["snapshot_manifest_sha256"] = "evidence-change-fixture"
    invalidated = (
        validate_proposal(changed_problem, state) if isinstance(changed_problem, Mapping) else {}
    )
    return {
        "status": "prepared",
        "parent_state_fingerprint": state.get("state_fingerprint"),
        "parent_preserved": True,
        "branches": branches,
        "recorded_replay": {
            "status": replayed.get("status"),
            "state_fingerprint": replayed.get("state_fingerprint"),
            "matches_direct_branch": replayed.get("state_fingerprint")
            == branches["direct"].get("state_fingerprint"),
        },
        "evidence_change_invalidation": {
            "status": invalidated.get("status"),
            "diagnostics": invalidated.get("validation", {}).get("diagnostics", [])
            if isinstance(invalidated.get("validation"), Mapping)
            else [],
            "old_state_fingerprint": state.get("state_fingerprint"),
        },
        "human_assessment_performed": False,
    }


def _runtime_fork_fixture(
    runtime: object,
    case: Mapping[str, object],
    result: Mapping[str, object],
    runtime_output: Path,
) -> dict[str, object]:
    """Exercise a recorded same-case fork without dispatching another provider call."""

    state = result.get("state")
    problem = result.get("problem")
    history_root = result.get("history_root")
    history_event_id = result.get("history_event_id")
    if (
        not isinstance(state, Mapping)
        or not isinstance(problem, Mapping)
        or not isinstance(history_root, str)
        or not isinstance(history_event_id, str)
    ):
        return {"status": "unavailable", "reason": "runtime result has no replayable state"}
    case_connection_id = case.get("connection_id")
    case_candidates = {
        str(item.get("candidate_id")): item
        for item in problem.get("candidates", [])
        if isinstance(item, Mapping)
        and item.get("candidate_id")
        and item.get("connection_id") == case_connection_id
    }
    selected = [
        item
        for item in state.get("selected_alignments", [])
        if isinstance(item, Mapping)
        and item.get("candidate_id") in case_candidates
        and (
            item.get("obligation_id") is None
            or item.get("obligation_id") == case_connection_id
            or item.get("obligation_id")
            == case_candidates[item["candidate_id"]].get("obligation_id")
        )
    ]
    if not selected:
        return {"status": "unavailable", "reason": "runtime did not select an alignment"}
    selected_candidate = selected[0]
    candidates = [
        item
        for item in problem.get("candidates", [])
        if isinstance(item, Mapping)
        and item.get("connection_id") == case_connection_id
        and item.get("candidate_id") != selected_candidate.get("candidate_id")
    ]
    if not candidates:
        return {"status": "unavailable", "reason": "runtime result has no alternate candidate"}

    from satn.planning_history import HistoryStore

    store = HistoryStore(Path(history_root))
    event_id: str | None = history_event_id
    checkpoint: str | None = None
    while event_id is not None:
        event = store.get(event_id)
        if isinstance(event, Mapping):
            operation = event.get("operation")
            payload = operation.get("payload") if isinstance(operation, Mapping) else None
            if (
                event.get("event_kind") == "decision"
                and isinstance(operation, Mapping)
                and operation.get("kind") == "select-alignment"
                and isinstance(payload, Mapping)
                and payload.get("candidate_id") == selected_candidate.get("candidate_id")
                and (
                    payload.get("obligation_id") is None
                    or payload.get("obligation_id") == selected_candidate.get("obligation_id")
                    or payload.get("obligation_id") == case_connection_id
                )
            ):
                checkpoint = store.checkpoint(event_id)
                break
            parent = event.get("timeline_parent_id")
            event_id = parent if isinstance(parent, str) else None
        else:
            break
    if checkpoint is None:
        return {"status": "unavailable", "reason": "alignment decision checkpoint is absent"}

    branch_id = f"fork-{case['case_id']}"
    runtime.fork(checkpoint, branch_id)  # type: ignore[union-attr]
    alternate = candidates[0]
    advanced = runtime.advance(  # type: ignore[union-attr]
        branch_id,
        {
            "kind": "select-alignment",
            "payload": {
                "candidate_id": alternate["candidate_id"],
                "obligation_id": alternate.get("obligation_id") or case.get("connection_id"),
            },
        },
        output_root=runtime_output / "fork-publication",
    )
    replay = runtime.replay(branch_id)  # type: ignore[union-attr]
    comparison = runtime.compare("main", branch_id)  # type: ignore[union-attr]
    fork_state = advanced.state  # type: ignore[union-attr]
    replacement_verified = isinstance(fork_state, Mapping) and any(
        isinstance(item, Mapping)
        and item.get("candidate_id") == alternate.get("candidate_id")
        and (
            item.get("obligation_id") is None
            or item.get("obligation_id") == case_connection_id
            or item.get("obligation_id") == alternate.get("obligation_id")
        )
        for item in fork_state.get("selected_alignments", [])
    )
    return {
        "status": "prepared" if replacement_verified else "invalid",
        "parent_branch": "main",
        "parent_history_event_id": history_event_id,
        "checkpoint": checkpoint,
        "fork_branch": branch_id,
        "parent_preserved": store.head("main").head_event_id == history_event_id,
        "alternate_candidate_id": alternate.get("candidate_id"),
        "replacement_verified": replacement_verified,
        "fork_result": advanced.as_dict(),  # type: ignore[union-attr]
        "replay": replay,
        "compare": comparison,
    }


def _history_provider_summary(history_root: Path, branch: str) -> dict[str, object]:
    """Count durable provider receipts and aggregate only explicit token usage."""

    from satn.planning_history import HistoryStore

    store = HistoryStore(history_root)
    event_id = store.head(branch).head_event_id
    seen: set[str] = set()
    receipts: list[dict[str, object]] = []
    totals = {"input_tokens": 0, "output_tokens": 0}
    usage_complete = True
    while isinstance(event_id, str) and event_id not in seen:
        seen.add(event_id)
        event = store.get(event_id)
        if not isinstance(event, Mapping):
            break
        receipt = event.get("receipt")
        if not isinstance(receipt, Mapping) and isinstance(event.get("receipt_ref"), str):
            stored = store.get(event["receipt_ref"])
            receipt = stored if isinstance(stored, Mapping) else None
        if isinstance(receipt, Mapping) and (
            receipt.get("provider") is not None
            or receipt.get("usage") is not None
            or receipt.get("status") is not None
        ):
            usage = receipt.get("usage")
            usage_record = dict(usage) if isinstance(usage, Mapping) else None
            if usage_record is None:
                usage_complete = False
            else:
                for name in totals:
                    value = usage_record.get(name)
                    if isinstance(value, int) and not isinstance(value, bool):
                        totals[name] += value
                    else:
                        usage_complete = False
            receipts.append(
                {
                    "event_id": event_id,
                    "event_kind": event.get("event_kind"),
                    "actor_kind": event.get("actor_kind"),
                    "status": receipt.get("status"),
                    "provider": receipt.get("provider"),
                    "model": receipt.get("model"),
                    "usage": usage_record,
                    "decision_class": event.get("decision_class"),
                }
            )
        parent = event.get("timeline_parent_id")
        event_id = parent if isinstance(parent, str) else None
    receipts.reverse()
    return {
        "calls": len(receipts),
        "usage_complete": usage_complete and bool(receipts),
        "usage_total": totals if usage_complete and receipts else None,
        "receipts": receipts,
    }


def _module_bindings(script_path: Path) -> dict[str, object]:
    package_root = script_path.parents[2] / "src" / "satn"
    names = (
        "planning_engine.py",
        "planning_contracts.py",
        "typesafe_planning.py",
        "planning_runtime.py",
        "planning_history.py",
        "planning_routing.py",
        "planning_publication.py",
        "assets/review-map.js",
        "assets/review-map.css",
        "assets/review-map.html",
    )
    bindings: dict[str, object] = {}
    for name in names:
        path = package_root / name
        bindings[name] = {"path": str(path), "sha256": _sha256(path)}
    bindings["evaluation_script.py"] = {
        "path": str(script_path),
        "sha256": _sha256(script_path),
    }
    return bindings


def _input_bindings(config: AreaConfig, script_path: Path) -> dict[str, object]:
    snapshot_path = config.source.snapshot_dir / config.source.snapshot_id
    manifest_path = snapshot_path / "snapshot.json"
    manifest: object = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            manifest = None
    source_paths = {
        "config": Path(config.config_path),
        "snapshot_manifest": manifest_path,
    }
    official = config.source.official_road_classification
    if official is not None:
        source_paths["official_roads"] = Path(official.path)
    elevation = config.source.national_elevation
    if elevation is not None:
        source_paths["elevation"] = Path(elevation.path)
    if config.atm.path is not None:
        source_paths["atm_optional"] = Path(config.atm.path)
    files = {
        name: {"path": str(path), "present": path.is_file(), "sha256": _sha256(path)}
        for name, path in source_paths.items()
    }
    return {
        "config_path": str(config.config_path),
        "config_sha256": _sha256(Path(config.config_path)),
        "snapshot_id": config.source.snapshot_id,
        "snapshot_path": str(snapshot_path),
        "snapshot_manifest_sha256": _sha256(manifest_path),
        "snapshot_manifest": manifest,
        "source_files": files,
        "contracts": {
            str(path): {"present": path.is_file(), "sha256": _sha256(path)}
            for path in CONTRACT_PATHS
        },
        "modules_and_assets": _module_bindings(script_path),
    }


def _prepare_case(
    spec: Mapping[str, str],
    *,
    config: AreaConfig,
    problem: Mapping[str, object],
) -> dict[str, object]:
    origin, destination = _check_case_places(problem, spec)
    state = initial_proposal(problem)
    connection_id = f"evaluation-{spec['case_id']}"
    operation = {
        "kind": "propose-connection",
        "operation_id": f"{connection_id}-intent",
        "parent_state_fingerprint": state["state_fingerprint"],
        "payload": {
            "connection_id": connection_id,
            "origin_place_id": spec["origin_place_id"],
            "destination_place_id": spec["destination_place_id"],
            "current_or_future": "unknown",
        },
    }
    expanded = expand_connection(problem, state, operation, config)
    if expanded.get("status") != "expanded":
        return {
            "case_id": spec["case_id"],
            "label": spec["label"],
            "status": "unresolved",
            "operation": operation,
            "diagnostics": _json_copy(expanded.get("diagnostics", [])),
            "connection_id": connection_id,
        }
    child_problem = expanded.get("problem")
    child_state = expanded.get("state")
    receipt = expanded.get("receipt")
    if not isinstance(child_problem, Mapping) or not isinstance(child_state, Mapping):
        raise ValueError(f"{spec['case_id']} expansion did not return a child problem/state")
    if not isinstance(receipt, Mapping):
        raise ValueError(f"{spec['case_id']} expansion did not return a receipt")
    candidates = [
        item
        for item in child_problem.get("candidates", [])
        if isinstance(item, Mapping) and item.get("connection_id") == connection_id
    ]
    source_status = _source_asset_status(config)
    candidate_views = [
        _candidate_view(item, config=config, source_asset_status=source_status)
        for item in candidates
    ]
    packet_state = _packet_state(
        spec,
        child_problem,
        child_state,
        child_state["connection_intents"][-1],
        (origin, destination),
        candidate_views,
        source_status,
    )
    questions = _question_payloads(spec, candidate_views)
    replayed = replay_expansion(problem, state, receipt)
    replay = {
        "status": replayed.get("status"),
        "problem_matches": replayed.get("problem") == child_problem,
        "state_matches": replayed.get("state") == child_state,
        "provider_calls": 0,
    }
    case: dict[str, object] = {
        "case_id": spec["case_id"],
        "label": spec["label"],
        "status": "prepared",
        "connection_id": connection_id,
        "operation": operation,
        "problem": _json_copy(dict(child_problem)),
        "state": _json_copy(dict(child_state)),
        "receipt": _json_copy(dict(receipt)),
        "packet_state": packet_state,
        "questions": questions,
        "candidate_views": candidate_views,
        "candidate_count": len(candidate_views),
        "candidate_ids": [str(item["candidate_id"]) for item in candidates],
        "replay": replay,
        "decision_fixture": _decision_fixture(
            {"case_id": spec["case_id"], "connection_id": connection_id},
            child_problem,
            child_state,
        ),
        "provider": {
            "status": "not-called",
            "specialist": "capability-unavailable",
            "human_assessment_performed": False,
        },
        "usage": None,
        "latency_seconds": None,
        "publication": None,
    }
    case["candidate_geojson"] = _candidate_geojson(spec, (origin, destination), candidate_views)
    return case


def _write_case(output_root: Path, case: Mapping[str, object]) -> None:
    case_dir = output_root / "cases" / str(case["case_id"])
    case_dir.mkdir(parents=True, exist_ok=False)
    for filename, key in (
        ("child-problem.json", "problem"),
        ("child-state.json", "state"),
        ("expansion-receipt.json", "receipt"),
        ("packet.json", "packet_state"),
        ("questions.json", "questions"),
        ("replay.json", "replay"),
        ("decision-fork-fixture.json", "decision_fixture"),
        ("candidate-options.geojson", "candidate_geojson"),
    ):
        if key in case:
            _write_json(case_dir / filename, case[key])
    _write_json(
        case_dir / "connection-operation.json",
        case.get("operation", {}),
    )
    _write_json(
        case_dir / "case-summary.json",
        {
            key: case.get(key)
            for key in (
                "case_id",
                "label",
                "status",
                "connection_id",
                "candidate_count",
                "candidate_ids",
                "provider",
                "usage",
                "latency_seconds",
                "publication",
            )
        },
    )


def _run_live_case(
    output_root: Path,
    case: dict[str, object],
    *,
    config: AreaConfig,
    spec: Mapping[str, str],
    model: str,
    endpoint: str,
) -> None:
    from satn.planning_runtime import PlanningRuntime

    provider = TypeSafeProvider(model=model, endpoint=endpoint)
    client = TypeSafeClient(provider=provider, require_credentials=True)
    case_dir = output_root / "cases" / str(case["case_id"])
    case_dir.mkdir(parents=True, exist_ok=False)
    history_root = case_dir / "history"
    runtime_output = case_dir / "runtime"
    connection = {
        "connection_id": case["connection_id"],
        "origin_place_id": spec["origin_place_id"],
        "destination_place_id": spec["destination_place_id"],
        "corridor_refs": [],
        "current_or_future": "unknown",
        "evidence_refs": [],
    }
    runtime = PlanningRuntime(history_root, provider=client)
    started = perf_counter()
    result = runtime.run(
        config,
        output_root=runtime_output,
        branch="main",
        mode="live",
        connection_options=[connection],
    )
    elapsed = perf_counter() - started
    result_dict = result.as_dict()
    problem = result.problem
    state = result.state
    candidates = [
        item
        for item in problem.get("candidates", [])
        if isinstance(item, Mapping) and item.get("connection_id") == case["connection_id"]
    ]
    source_status = _source_asset_status(config)
    candidate_views = [
        _candidate_view(item, config=config, source_asset_status=source_status)
        for item in candidates
    ]
    places = _check_case_places(problem, spec)
    case["status"] = result.status
    case["problem"] = _json_copy(problem)
    case["state"] = _json_copy(state) if isinstance(state, Mapping) else None
    case["candidate_views"] = candidate_views
    case["candidate_count"] = len(candidate_views)
    case["candidate_ids"] = [str(item["candidate_id"]) for item in candidates]
    case["runtime_result"] = result_dict
    provider_history = _history_provider_summary(history_root, "main")
    case["provider_history"] = provider_history
    case["history"] = {
        "root": str(history_root),
        "branch": result.branch_id,
        "event_id": result.history_event_id,
        "verify": runtime.verify("main"),
        "replay": runtime.replay("main"),
    }
    case["fork_fixture"] = _runtime_fork_fixture(runtime, case, result_dict, runtime_output)
    if isinstance(state, Mapping):
        case["decision_fixture"] = _decision_fixture(case, problem, state)
    else:
        case["decision_fixture"] = {
            "status": "unavailable",
            "reason": "runtime did not return proposal state",
        }
    case["provider_result"] = result.provider_result
    case["provider"] = {
        "status": result.provider_result.get("status") if result.provider_result else None,
        "provider": result.provider_result.get("provider") if result.provider_result else None,
        "model": result.provider_result.get("model") if result.provider_result else None,
        "failure_class": (
            result.provider_result.get("failure_class") if result.provider_result else None
        ),
        "calls": provider_history["calls"],
        "usage_complete": provider_history["usage_complete"],
        "human_assessment_performed": False,
        "specialist": "capability-unavailable",
    }
    case["latency_seconds"] = elapsed
    case["usage"] = provider_history["usage_total"]
    case["publication"] = result.publication
    case["candidate_geojson"] = _candidate_geojson(spec, places, candidate_views)
    _write_json(case_dir / "runtime-result.json", result_dict)
    _write_json(case_dir / "provider-result.json", result.provider_result)
    _write_json(case_dir / "provider-history.json", provider_history)
    _write_json(case_dir / "history.json", case["history"])
    _write_json(case_dir / "fork-fixture.json", case["fork_fixture"])
    _write_json(case_dir / "decision-fork-fixture.json", case["decision_fixture"])
    _write_json(case_dir / "candidate-options.geojson", case["candidate_geojson"])
    _write_json(
        case_dir / "case-summary.json",
        {
            key: case.get(key)
            for key in (
                "case_id",
                "label",
                "status",
                "connection_id",
                "candidate_count",
                "candidate_ids",
                "provider",
                "usage",
                "provider_history",
                "latency_seconds",
                "publication",
                "history",
                "fork_fixture",
            )
        },
    )


def _artifact_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): digest
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name != "run-manifest.json"
        and (digest := _sha256(path)) is not None
    }


def _report(manifest: Mapping[str, object]) -> str:
    lines = [
        "# B&NES TypeSafe planning evaluation",
        "",
        f"Status: `{manifest.get('status')}`",
        f"Mode: `{manifest.get('mode')}`",
        "",
        "This run binds fresh named-place connection intents to the local planning "
        "engine's admitted candidates. Recovery artifact identifiers are not used as "
        "planning inputs.",
        "Bath cases are explicitly station-to-community anchors because the pinned "
        "snapshot admits Bath Spa station and no Bath settlement place; they are not "
        "whole-city coverage and are not comparable to legacy recovery units.",
        "",
        "| Case | Status | Candidates | Provider | Latency (s) | Usage |",
        "| --- | --- | ---: | --- | ---: | --- |",
    ]
    for case in manifest.get("cases", []):
        if not isinstance(case, Mapping):
            continue
        lines.append(
            f"| {case.get('label')} | {case.get('status')} | {case.get('candidate_count')} | "
            f"{case.get('provider_status')} | {case.get('latency_seconds')} | {case.get('usage')} |"
        )
    lines.extend(
        [
            "",
            "The preparation packet records topology/path provenance and comparison "
            "fields. The EA elevation file is present, but candidate graph paths have no "
            "bound elevation sample IDs or profile, so route coverage remains explicitly "
            "unresolved. Social-safety evidence is also unresolved; the optional ATM "
            "reference is recorded separately. "
            "No human assessment or specialist capability is claimed.",
            "",
            "A live provider result is a typed judgment receipt. Code still owns candidate "
            "admission, operation application, validation, replay and publication.",
            "",
            "The run manifest records hashes of the actual config, snapshot, contract, "
            "module and map asset files. It does not claim a Git revision.",
        ]
    )
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("prepare", "live"), default="prepare")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_root = _output_root(args.output_root.resolve())
    config = AreaDefinition.from_yaml(args.config.resolve())
    started_at = _utc_now()
    started = perf_counter()
    cases: list[dict[str, object]] = []
    problem: Mapping[str, object] = {}
    if args.mode == "prepare":
        problem = build_planning_problem(config)
        for spec in CASE_SPECS:
            case = _prepare_case(spec, config=config, problem=problem)
            cases.append(case)
            _write_case(output_root, case)
    else:
        for spec in CASE_SPECS:
            cases.append(
                {
                    "case_id": spec["case_id"],
                    "label": spec["label"],
                    "connection_id": f"evaluation-{spec['case_id']}",
                    "status": "pending",
                    "provider": {"status": "not-called"},
                    "usage": None,
                    "latency_seconds": None,
                    "publication": None,
                }
            )
        for case in cases:
            spec = next(item for item in CASE_SPECS if item["case_id"] == case["case_id"])
            _run_live_case(
                output_root,
                case,
                config=config,
                spec=spec,
                model=args.model,
                endpoint=args.endpoint,
            )
        first_problem = cases[0].get("problem") if cases else None
        if isinstance(first_problem, Mapping):
            problem = first_problem
    usage_total = {"input_tokens": 0, "output_tokens": 0}
    usage_seen = False
    usage_complete = True
    provider_calls_total = 0
    for case in cases:
        provider_history = case.get("provider_history")
        if isinstance(provider_history, Mapping):
            calls = provider_history.get("calls")
            if isinstance(calls, int) and not isinstance(calls, bool):
                provider_calls_total += calls
            if provider_history.get("usage_complete") is False:
                usage_complete = False
        usage = case.get("usage")
        if isinstance(usage, Mapping):
            usage_seen = True
            for name in usage_total:
                value = usage.get(name)
                if isinstance(value, int) and not isinstance(value, bool):
                    usage_total[name] += value
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "prepared" if args.mode == "prepare" else "executed",
        "mode": args.mode,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "elapsed_seconds": perf_counter() - started,
        "python": platform.python_version(),
        "config": _input_bindings(config, Path(__file__).resolve()),
        "problem": {
            "problem_id": problem.get("problem_id"),
            "input_fingerprint": problem.get("input_fingerprint"),
            "brief_fingerprint": problem.get("brief_fingerprint"),
            "source_corridor_count": len(problem.get("source_corridors", [])),
            "place_count": len(problem.get("places", [])),
            "obligation_count": len(problem.get("obligations", [])),
            "candidate_count": len(problem.get("candidates", [])),
            "planning_gap_count": len(problem.get("planning_gaps", [])),
            "unknown_count": len(problem.get("unknown_facts", [])),
        },
        "cases": [
            {
                "case_id": case.get("case_id"),
                "label": case.get("label"),
                "status": case.get("status"),
                "connection_id": case.get("connection_id"),
                "candidate_count": case.get("candidate_count", 0),
                "candidate_ids": case.get("candidate_ids", []),
                "provider_status": case.get("provider", {}).get("status")
                if isinstance(case.get("provider"), Mapping)
                else None,
                "provider_calls": case.get("provider", {}).get("calls")
                if isinstance(case.get("provider"), Mapping)
                else None,
                "usage": case.get("usage"),
                "latency_seconds": case.get("latency_seconds"),
                "replay": case.get("replay"),
                "history": case.get("history"),
                "fork_fixture": case.get("fork_fixture"),
                "decision_fixture": case.get("decision_fixture"),
                "publication": case.get("publication"),
            }
            for case in cases
        ],
        "provider": {
            "provider": "typesafe",
            "model": args.model,
            "endpoint": args.endpoint,
            "credential_source": "TypeSafeClient environment or local credential path",
            "calls_made": args.mode == "live",
            "specialist": "capability-unavailable",
            "human_assessment_performed": False,
        },
        "provider_calls_total": provider_calls_total,
        "usage_total": usage_total if usage_seen and usage_complete else None,
        "latency_total_seconds": sum(
            float(case["latency_seconds"])
            for case in cases
            if isinstance(case.get("latency_seconds"), (int, float))
        ),
    }
    manifest["artifacts"] = _artifact_hashes(output_root)
    _write_json(output_root / "run-manifest.json", manifest)
    _write_text(output_root / "REPORT.md", _report(manifest))
    print(str(output_root))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
