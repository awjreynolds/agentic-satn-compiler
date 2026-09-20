"""Prepare bounded offline TypeSafe probes for one retained planning checkpoint.

The default command reads the archived Radstock--Midsomer Norton request, binds
its ordered directed edges to the pinned network snapshot, and writes one
create-once machine-readable experiment directory.  ``--execute`` is an
explicit later step; preparation never contacts TypeSafe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import geopandas as gpd

from satn.content_identity import canonical_network_geometry_fingerprint
from satn.models import AreaDefinition
from satn.routing import RoadGraph
from satn.tags import source_identity
from satn.typesafe_planning import (
    DEFAULT_MODEL,
    TYPE_SAFE_ENDPOINT,
    TypeSafeClient,
    TypeSafeProvider,
    TypeSafeRequest,
)

SCHEMA_VERSION = "typesafe-decision-experiment/v1"
PACKET_RECORD_ID = "d9c7721ba27e5318743e41523b261a02b8411a112080f53b558d3494074c395d"
REQUEST_RECORD_ID = "5cddaa06daa3cc59c14362511bc842e56f487efe47b1852dab73ec10aaafd9a7"
BASELINE_RECEIPT_IDS = (
    "c8b469738e28eed90d593e43ed42a76df86583323e774835451a4faac3545a9e",
    "5899fa5095ed2bdbd733eb96764615cb58d62e00f03f527df820693f1d40fc8b",
)
PROBES = ("faithful-facts", "framing", "dependency")

_ROAD_FACT_FIELDS = (
    "highway",
    "ref",
    "name",
    "lanes",
    "maxspeed",
    "access",
    "bicycle",
    "cycleway",
    "surface",
    "oneway",
    "service",
    "bridge",
    "tunnel",
    "width",
    "est_width",
    "junction",
)


def _copy_json(value: object) -> object:
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False))


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        item = value.item()  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        item = value
    if item is not value:
        return _json_value(item)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _field_fact(record: Mapping[str, object], field: str) -> object:
    if field not in record:
        return {"status": "absent"}
    value = _json_value(record.get(field))
    if value is None:
        return {"status": "null", "value": None}
    return value


def _fact_counts(records: Iterable[Mapping[str, object]], field: str) -> dict[str, int]:
    counts = {"value": 0, "null": 0, "absent": 0}
    for record in records:
        fact = record.get("road_facts", {}).get(field)
        if isinstance(fact, Mapping):
            status = str(fact.get("status", "absent"))
        elif fact is None:
            status = "null"
        else:
            status = "value"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>" if str(key).casefold() == "authorization" else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return _json_value(value)


def _payload(record: Mapping[str, object]) -> Mapping[str, object]:
    payload = record.get("payload")
    if isinstance(payload, Mapping):
        return payload
    return record


def _record_path(history_root: Path, record_id: str) -> Path:
    return history_root / "records" / record_id[:2] / f"{record_id}.json"


def _read_record(history_root: Path, record_id: str) -> dict[str, object]:
    path = _record_path(history_root, record_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read history record {record_id}: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"history record {record_id} is not an object")
    return value


def _source_id(row: Mapping[str, object], fallback: object) -> str:
    return str(source_identity(row, ("osmid", "source_id", "osm_id", "id", "edge_id"), fallback))


def _node_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.casefold() not in {"nan", "none"} else None


def _authoritative_source_index(
    snapshot_root: Path,
    packet_edges: Sequence[Mapping[str, object]],
    *,
    snapshot_id: str,
    network_sha256: str | None,
) -> dict[str, dict[str, object]]:
    """Bind packet edges to one exact RoadGraph edge and source feature.

    The source-row fallback uses source identity, both endpoint nodes and the
    direction-independent geometry fingerprint.  An OSM way ID alone is never
    sufficient because one way can contain multiple directed segments.
    """

    network_path = snapshot_root / "network.geojson"
    if not network_path.is_file():
        raise ValueError(f"pinned snapshot network is missing: {network_path}")
    network = gpd.read_file(network_path)
    graph = RoadGraph(network)
    rows_by_key: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for index, row in network.iterrows():
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            continue
        source_edge_id = _source_id(row, index)
        from_node = _node_id(row.get("u"))
        to_node = _node_id(row.get("v"))
        if from_node is None or to_node is None:
            continue
        rows_by_key[(source_edge_id, from_node, to_node)].append(
            {
                "row": row,
                "index": index,
                "geometry_fingerprint": canonical_network_geometry_fingerprint(
                    geometry, network.crs
                ),
            }
        )

    result: dict[str, dict[str, object]] = {}
    for packet_edge in packet_edges:
        directed_id = str(packet_edge.get("directed_edge_id", ""))
        source_edge_id = str(packet_edge.get("source_edge_id", ""))
        from_node = str(packet_edge.get("from_node_id", ""))
        to_node = str(packet_edge.get("to_node_id", ""))
        if not directed_id or not source_edge_id or not from_node or not to_node:
            raise ValueError("packet edge is missing an authoritative identity")
        graph_edge = graph.graph.get_edge_data(from_node, to_node)
        if (
            not isinstance(graph_edge, Mapping)
            or str(graph_edge.get("directed_edge_id")) != directed_id
        ):
            raise ValueError(f"packet directed edge is absent or stale: {directed_id}")
        graph_geometry = graph_edge.get("geometry")
        graph_geometry_fingerprint = canonical_network_geometry_fingerprint(
            graph_geometry, graph.crs
        )
        matches = rows_by_key.get((source_edge_id, from_node, to_node), [])
        orientation = "direct"
        if not matches:
            matches = rows_by_key.get((source_edge_id, to_node, from_node), [])
            orientation = "reverse"
        matches = [
            item for item in matches if item["geometry_fingerprint"] == graph_geometry_fingerprint
        ]
        if len(matches) != 1:
            raise ValueError(f"packet edge does not have one source-feature match: {directed_id}")
        row = matches[0]["row"]
        row_mapping = {str(key): value for key, value in row.items()}
        source_properties = {
            str(key): _json_value(value)
            for key, value in row_mapping.items()
            if str(key) != "geometry"
        }
        source_binding = {
            "snapshot_id": snapshot_id,
            "source_file": "network.geojson",
            "source_file_sha256": network_sha256,
            "source_feature_key": {
                "source_edge_id": source_edge_id,
                "u": _json_value(row_mapping.get("u")),
                "v": _json_value(row_mapping.get("v")),
                "key": _json_value(row_mapping.get("key")),
            },
            "directed_edge_id": directed_id,
            "from_node_id": from_node,
            "to_node_id": to_node,
            "geometry_fingerprint": graph_geometry_fingerprint,
            "source_row_orientation": orientation,
        }
        result[directed_id] = {
            **{str(key): _json_value(value) for key, value in packet_edge.items()},
            "source_binding": source_binding,
            "road_facts": {
                field: _field_fact(source_properties, field) for field in _ROAD_FACT_FIELDS
            },
        }
    return result


def _unknown_catalog(
    packet: Mapping[str, object],
    source_facts: Mapping[str, Mapping[str, object]],
    snapshot_binding: Mapping[str, object],
) -> dict[str, object]:
    candidates = [
        item
        for item in packet.get("candidates", [])
        if isinstance(item, Mapping) and item.get("candidate_id")
    ]
    candidate_refs = [str(item["candidate_id"]) for item in candidates]
    connection_refs = sorted(
        {str(item["connection_id"]) for item in candidates if item.get("connection_id")}
    )
    scope_refs = [*candidate_refs, *connection_refs]
    edge_refs = list(source_facts)
    access_counts = _fact_counts(source_facts.values(), "access")
    status_counts: dict[str, int] = defaultdict(int)
    for candidate in candidates:
        status_counts[str(candidate.get("current_or_future", "absent"))] += 1
    elevation = snapshot_binding.get("elevation")
    if not isinstance(elevation, Mapping):
        elevation = {"status": "not-bound"}
    claims = [
        {
            "claim_id": "current-provision-access",
            "kind": "factual",
            "claim": "current provision and access for the proposed alignment",
            "scope_refs": scope_refs,
            "source_edge_refs": edge_refs,
            "coverage": {
                "source_field": "access",
                "selected_edge_status_counts": access_counts,
                "interpretation": (
                    "Selected-edge source coverage only; null or absent values do not establish "
                    "global access absence."
                ),
            },
            "investigation": "Bind claim-specific current-provision and access observations.",
        },
        {
            "claim_id": "route-specific-elevation-profile",
            "kind": "factual",
            "claim": "route-specific elevation or gradient profile for the proposed alignment",
            "scope_refs": scope_refs,
            "source_edge_refs": edge_refs,
            "coverage": _copy_json(dict(elevation)),
            "investigation": "Bind route geometry to elevation sample IDs and a profile.",
        },
        {
            "claim_id": "social-safety-independent-access",
            "kind": "factual",
            "claim": "social safety and independent access for the proposed alignment",
            "scope_refs": scope_refs,
            "source_edge_refs": [],
            "coverage": {
                "status": "not-supplied",
                "source_evidence_refs": [],
            },
            "investigation": (
                "Collect claim-specific social-safety and independent-access evidence."
            ),
        },
        {
            "claim_id": "current-future-provision",
            "kind": "factual",
            "claim": "whether each proposed alignment is current provision or future intervention",
            "scope_refs": scope_refs,
            "source_edge_refs": edge_refs,
            "coverage": {
                "candidate_status_counts": dict(sorted(status_counts.items())),
                "status": "unknown" if status_counts.get("unknown") else "supplied",
                "policy_distinction": "current and future remain distinct",
            },
            "investigation": "Bind explicit current-provision or future-intervention evidence.",
        },
    ]
    outcomes = {
        "policy-not-specified": {
            "kind": "outcome",
            "claim": "owner policy needed for an unresolved trade-off",
            "scope_refs": ["brief", "policy"],
            "coverage": (
                "The supplied brief binds current/future distinction but no additional trade-off."
            ),
            "investigation": "Ask the owner to state the missing policy choice.",
        },
        "candidate-coverage-inadequate": {
            "kind": "outcome",
            "claim": "the supplied candidate set does not cover the required comparison",
            "scope_refs": candidate_refs,
            "coverage": "Four retained candidates are supplied; exhaustiveness is not established.",
            "investigation": "Name and admit the specific missing alternative before comparison.",
        },
        "no-identified-factual-blocker": {
            "kind": "outcome",
            "claim": "no named factual dependency blocks this proposal comparison",
            "scope_refs": scope_refs,
            "coverage": (
                "Outcome supplied for the dependency judgment; it is not a route selection."
            ),
            "investigation": (
                "Continue with the supplied evidence and retain reviewable uncertainty."
            ),
        },
        "cannot-determine": {
            "kind": "outcome",
            "claim": "the dependency cannot be determined from the supplied packet",
            "scope_refs": scope_refs,
            "coverage": "Outcome supplied for an inconclusive dependency judgment.",
            "investigation": "Retain the unresolved proposal and identify the next owner action.",
        },
    }
    return {
        "schema_version": "decision-unknown-catalog/v1",
        "claims": claims,
        "outcomes": outcomes,
    }


def _field_diff(packet: Mapping[str, object], faithful: Mapping[str, object]) -> dict[str, object]:
    del faithful
    return {
        "changed_existing_fields": [],
        "removed_fields": [],
        "added_fields": [
            "source_evidence.snapshot_ref",
            "source_evidence.directed_edges[*].road_facts",
            "source_evidence.directed_edges[*].road_facts_absent",
            "unknown_catalog.claims",
        ],
        "baseline_packet_fields": sorted(str(key) for key in packet),
        "representation_changes": [
            "source_evidence.directed_edges: list -> map keyed by directed_edge_id"
        ],
        "comparison": (
            "Original question, option IDs, candidate IDs, ordered paths and brief retained."
        ),
    }


def _model_source_evidence(
    source_facts: Mapping[str, object],
    snapshot_binding: Mapping[str, object],
) -> dict[str, object]:
    raw_edges = source_facts.get("directed_edges", [])
    if not isinstance(raw_edges, list):
        raise ValueError("source facts have no directed edges")
    compact_edges: dict[str, dict[str, object]] = {}
    for raw_edge in raw_edges:
        if not isinstance(raw_edge, Mapping):
            raise ValueError("source facts contain a malformed directed edge")
        directed_id = str(raw_edge.get("directed_edge_id", ""))
        binding = raw_edge.get("source_binding")
        if not directed_id or not isinstance(binding, Mapping):
            raise ValueError("source facts contain an unbound directed edge")
        source_key = binding.get("source_feature_key")
        if not isinstance(source_key, Mapping):
            source_key = {
                key: binding[key] for key in ("source_edge_id", "u", "v", "key") if key in binding
            }
        road_facts = raw_edge.get("road_facts")
        if not isinstance(road_facts, Mapping):
            raise ValueError(f"source facts have no road facts for {directed_id}")
        supplied: dict[str, object] = {}
        null: list[str] = []
        absent: list[str] = []
        for field, fact in road_facts.items():
            if isinstance(fact, Mapping):
                status = str(fact.get("status", "value"))
                if status == "absent":
                    absent.append(str(field))
                elif status == "null":
                    null.append(str(field))
                else:
                    supplied[str(field)] = fact.get("value")
            elif fact is None:
                null.append(str(field))
            else:
                supplied[str(field)] = _json_value(fact)
        compact: dict[str, object] = {
            "directed_edge_id": directed_id,
            "source_edge_id": raw_edge.get("source_edge_id") or source_key.get("source_edge_id"),
            "from_node_id": raw_edge.get("from_node_id")
            or binding.get("from_node_id")
            or source_key.get("u"),
            "to_node_id": raw_edge.get("to_node_id")
            or binding.get("to_node_id")
            or source_key.get("v"),
            "road_facts": supplied,
            "road_facts_null": sorted(null),
            "road_facts_absent": sorted(absent),
        }
        compact_edges[directed_id] = compact
    snapshot_ref = {
        key: snapshot_binding[key]
        for key in ("snapshot_id", "network_sha256")
        if snapshot_binding.get(key) is not None
    }
    return {
        "snapshot_ref": snapshot_ref,
        "source_file": "network.geojson",
        "evidence_refs": _copy_json(source_facts.get("evidence_refs", [])),
        "directed_edges": compact_edges,
    }


def build_scoped_packet(
    packet_record: Mapping[str, object],
    request_record: Mapping[str, object],
    source_index: Mapping[str, Mapping[str, object]],
    *,
    snapshot_binding: Mapping[str, object],
    baseline_receipts: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Return a source-faithful decision experiment without provider calls."""

    packet = _payload(packet_record)
    request = _payload(request_record)
    if request.get("task_packet") != packet:
        raise ValueError("archived request and packet records disagree")
    raw_edges = packet.get("source_evidence", {}).get("directed_edges", [])
    if not isinstance(raw_edges, list):
        raise ValueError("archived packet source evidence is not a list")
    enriched_edges: list[dict[str, object]] = []
    for raw_edge in raw_edges:
        if not isinstance(raw_edge, Mapping):
            raise ValueError("archived packet contains a malformed source edge")
        directed_id = str(raw_edge.get("directed_edge_id", ""))
        bound = source_index.get(directed_id)
        if bound is None:
            raise ValueError(f"source snapshot has no exact match for {directed_id}")
        enriched_edges.append(
            _copy_json({**dict(raw_edge), **dict(bound)})  # type: ignore[arg-type]
        )
    source_facts = {
        "schema_version": "planning-source-facts/v1",
        "directed_edges": enriched_edges,
        "evidence_refs": _copy_json(packet.get("source_evidence", {}).get("evidence_refs", [])),
    }
    enriched_candidates: list[dict[str, object]] = []
    for candidate in packet.get("candidates", []):
        if not isinstance(candidate, Mapping):
            raise ValueError("archived packet contains a malformed candidate")
        graph_path = candidate.get("graph_path")
        if not isinstance(graph_path, Mapping):
            raise ValueError("candidate has no graph path")
        edge_refs = [str(item) for item in graph_path.get("directed_edge_ids", [])]
        if any(edge_ref not in source_index for edge_ref in edge_refs):
            raise ValueError(f"candidate {candidate.get('candidate_id')} has an unbound edge")
        enriched = dict(_copy_json(dict(candidate)))
        enriched["source_fact_refs"] = edge_refs
        enriched_candidates.append(enriched)
    faithful_packet = dict(_copy_json(dict(packet)))
    faithful_packet["candidates"] = [
        {key: value for key, value in candidate.items() if key != "source_fact_refs"}
        for candidate in enriched_candidates
    ]
    faithful_packet["source_evidence"] = _model_source_evidence(
        source_facts,
        snapshot_binding,
    )
    catalog = _unknown_catalog(packet, source_index, snapshot_binding)
    experiment = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_binding": _copy_json(dict(snapshot_binding)),
        "historical_baseline": {
            "request_record_id": request_record.get("record_digest"),
            "packet_record_id": packet_record.get("record_digest"),
            "request": _redact(dict(request_record)),
            "receipts": [_redact(dict(item)) for item in baseline_receipts],
            "request_id": request.get("request_id"),
            "models": sorted(
                {
                    str(_payload(item).get("model"))
                    for item in baseline_receipts
                    if _payload(item).get("model")
                }
            ),
            "usage": [
                _copy_json(_payload(item).get("usage"))
                for item in baseline_receipts
                if isinstance(_payload(item).get("usage"), Mapping)
            ],
            "latency_seconds": None,
        },
        "original_packet": _copy_json(dict(packet)),
        "faithful_packet": faithful_packet,
        "source_facts": source_facts,
        "unknown_catalog": catalog,
        "field_diff": _field_diff(packet, faithful_packet),
        "fingerprints": {
            "original_packet": _digest(packet),
            "faithful_packet": _digest(faithful_packet),
            "source_facts": _digest(source_facts),
            "unknown_catalog": _digest(catalog),
        },
    }
    return experiment


def _probe_state(experiment: Mapping[str, object]) -> dict[str, object]:
    faithful_packet = experiment.get("faithful_packet")
    if not isinstance(faithful_packet, Mapping):
        raise ValueError("experiment has no faithful packet")
    return dict(_copy_json(dict(faithful_packet)))


def _dependency_model_catalog(
    experiment: Mapping[str, object],
    catalog: Mapping[str, object],
    state: Mapping[str, object],
) -> dict[str, object]:
    candidates = [
        item
        for item in state.get("candidates", [])
        if isinstance(item, Mapping) and item.get("candidate_id")
    ]
    candidate_refs = [str(item["candidate_id"]) for item in candidates]
    connection_refs = sorted(
        {str(item["connection_id"]) for item in candidates if item.get("connection_id")}
    )
    claims = catalog.get("claims", [])
    if not isinstance(claims, list):
        raise ValueError("unknown catalog has no claims")
    scope_values = [
        _copy_json(item.get("scope_refs", []))
        for item in claims
        if isinstance(item, Mapping) and "scope_refs" in item
    ]
    shared_scope = bool(scope_values) and all(value == scope_values[0] for value in scope_values)
    projected_claims: list[dict[str, object]] = []
    for item in claims:
        if not isinstance(item, Mapping):
            raise ValueError("unknown catalog contains a malformed claim")
        projected = dict(_copy_json(dict(item)))
        source_refs = projected.pop("source_edge_refs", None)
        if shared_scope and "scope_refs" in projected:
            projected.pop("scope_refs")
            projected["scope_ref"] = "alignment-scope"
        projected["source_path_ref"] = "alignment-candidate-paths" if source_refs else None
        projected_claims.append(projected)
    return {
        "schema_version": catalog.get("schema_version"),
        "catalog_ref": {
            "field": "catalog",
            "fingerprint": experiment["fingerprints"]["unknown_catalog"],
        },
        "scope_bindings": {
            "alignment-scope": scope_values[0] if shared_scope else [],
        },
        "source_path_refs": {
            "alignment-candidate-paths": {
                "candidate_refs": candidate_refs,
                "connection_refs": connection_refs,
                "edge_refs_field": "candidates[*].graph_path.directed_edge_ids",
            }
        },
        "claims": projected_claims,
        "outcomes": _copy_json(catalog.get("outcomes", {})),
    }


def build_probe_request(
    experiment: Mapping[str, object],
    probe: str,
    *,
    model: str = DEFAULT_MODEL,
) -> dict[str, object]:
    """Build one separately runnable Choice request; never send it."""

    if probe not in PROBES:
        raise ValueError(f"unsupported probe: {probe}")
    state = _probe_state(experiment)
    faithful_packet = experiment["faithful_packet"]
    assert isinstance(faithful_packet, Mapping)
    original_questions = faithful_packet.get("questions")
    if not isinstance(original_questions, Mapping):
        raise ValueError("faithful packet has no original questions")
    original_decision = original_questions.get("decision")
    if not isinstance(original_decision, Mapping):
        raise ValueError("faithful packet has no original decision question")
    question = dict(_copy_json(dict(original_decision)))
    if probe == "framing":
        question["instructions"] = (
            "For review under the supplied owner brief, choose one admitted alignment or an "
            "explicit unresolved outcome. This is a provisional proposal only: it does not "
            "establish current usability, feasibility, safety or adoption. Preserve the supplied "
            "candidate IDs and do not invent a route, preference or policy."
        )
    elif probe == "dependency":
        catalog = experiment.get("unknown_catalog")
        if not isinstance(catalog, Mapping):
            raise ValueError("experiment has no unknown catalog")
        criteria: dict[str, object] = {}
        for collection_name in ("claims", "outcomes"):
            collection = catalog.get(collection_name, [])
            if not isinstance(collection, list):
                continue
            for item in collection:
                if isinstance(item, Mapping) and item.get("claim_id"):
                    criteria[str(item["claim_id"])] = item.get("claim")
                elif isinstance(item, Mapping) and item.get("outcome_id"):
                    criteria[str(item["outcome_id"])] = item.get("claim")
        # Outcomes are keyed in the catalog to keep the answer IDs closed and stable.
        if isinstance(catalog.get("outcomes"), Mapping):
            criteria = {
                **criteria,
                **{
                    str(key): item.get("claim")
                    for key, item in catalog["outcomes"].items()
                    if isinstance(item, Mapping)
                },
            }
        state["unknown_catalog"] = _dependency_model_catalog(experiment, catalog, state)
        question = {
            "type": "choice",
            "instructions": (
                "Which named dependency prevents a meaningful comparison for this proposal? "
                "Choose one supplied claim or explicit outcome. Do not invent facts or citations."
            ),
            "criteria": criteria,
        }
    questions = {"decision": question}
    state["questions"] = _copy_json(questions)
    request = TypeSafeRequest(state, questions, model)
    payload = request.payload()
    return {
        "schema_version": "decision-probe-request/v1",
        "probe": probe,
        "model": model,
        "state": state,
        "questions": questions,
        "request": payload,
        "body": request.body(),
        "body_sha256": hashlib.sha256(request.body().encode("utf-8")).hexdigest(),
        "catalog": _copy_json(experiment.get("unknown_catalog", {})),
    }


def map_choice_to_catalog(
    choice: object,
    catalog: Mapping[str, object],
) -> dict[str, object]:
    """Map a closed Choice answer to its named claim/scope/investigation."""

    selected = choice.get("choice") if isinstance(choice, Mapping) else choice
    selected_id = str(selected) if selected is not None else ""
    entry: Mapping[str, object] | None = None
    claims = catalog.get("claims", [])
    if isinstance(claims, list):
        entry = next(
            (
                item
                for item in claims
                if isinstance(item, Mapping) and str(item.get("claim_id")) == selected_id
            ),
            None,
        )
    outcomes = catalog.get("outcomes")
    if entry is None and isinstance(outcomes, Mapping):
        item = outcomes.get(selected_id)
        if isinstance(item, Mapping):
            entry = item
    if entry is None:
        return {
            "status": "unresolved",
            "choice": selected_id,
            "reason": "choice-not-in-catalog",
        }
    return {
        "status": "mapped",
        "claim_id": selected_id,
        "claim": entry.get("claim"),
        "scope_refs": _copy_json(entry.get("scope_refs", [])),
        "coverage": _copy_json(entry.get("coverage")),
        "investigation": entry.get("investigation"),
    }


def _execute_probe(
    probe_request: Mapping[str, object],
    *,
    model: str,
    endpoint: str,
) -> dict[str, object]:
    state = probe_request.get("state")
    questions = probe_request.get("questions")
    if not isinstance(state, Mapping) or not isinstance(questions, Mapping):
        raise ValueError("probe request is malformed")
    provider = TypeSafeProvider(model=model, endpoint=endpoint)
    client = TypeSafeClient(provider=provider, require_credentials=True)
    started = perf_counter()
    result = client.judge(_copy_json(dict(state)), _copy_json(dict(questions)))
    elapsed = perf_counter() - started
    answer = None
    answers = result.get("answers") if isinstance(result, Mapping) else None
    if isinstance(answers, Mapping):
        answer = answers.get("decision")
    catalog = probe_request.get("catalog")
    mapped = map_choice_to_catalog(answer, catalog) if isinstance(catalog, Mapping) else None
    return {
        "status": "answered" if result.get("status") == "answered" else str(result.get("status")),
        "probe": probe_request.get("probe"),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "requested_model": result.get("requested_model"),
        "usage": _copy_json(result.get("usage")),
        "latency_seconds": elapsed,
        "answer": _copy_json(answer),
        "mapped_catalog_result": mapped,
        "exchange": _redact(result),
    }


def _snapshot_binding(snapshot_root: Path, config_path: Path | None) -> dict[str, object]:
    manifest_path = snapshot_root / "snapshot.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid pinned snapshot manifest: {manifest_path}") from error
    if not isinstance(manifest, Mapping) or not manifest.get("snapshot_id"):
        raise ValueError("pinned snapshot manifest has no snapshot_id")
    elevation_path = snapshot_root / "elevation-evidence.geojson"
    binding: dict[str, object] = {
        "snapshot_id": str(manifest["snapshot_id"]),
        "snapshot_root": str(snapshot_root),
        "snapshot_manifest_sha256": _sha256(manifest_path),
        "network_sha256": _sha256(snapshot_root / "network.geojson"),
        "elevation": {
            "source_file": str(elevation_path),
            "source_file_present": elevation_path.is_file(),
            "status": "source-file-present-unbound"
            if elevation_path.is_file()
            else "source-file-absent",
            "bound_route_profiles": False,
        },
    }
    if config_path is not None:
        binding["config_path"] = str(config_path)
        binding["config_sha256"] = _sha256(config_path)
    return binding


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _artifact_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "run-manifest.json":
            digest = _sha256(path)
            if digest is not None:
                result[str(path.relative_to(root))] = digest
    return result


def _code_bindings() -> dict[str, object]:
    root = Path(__file__).resolve().parents[2]
    paths = (
        Path(__file__).resolve(),
        root / "src/satn/typesafe_planning.py",
        root / "src/satn/routing.py",
        root / "src/satn/content_identity.py",
    )
    return {
        str(path.relative_to(root)) if path != paths[0] else "experiment_script": {
            "path": str(path),
            "sha256": _sha256(path),
        }
        for path in paths
    }


def _report(experiment: Mapping[str, object], probe: Mapping[str, object], executed: bool) -> str:
    packet = experiment["faithful_packet"]
    assert isinstance(packet, Mapping)
    candidates = packet.get("candidates", [])
    edges = experiment["source_facts"]["directed_edges"]
    return "\n".join(
        [
            "# TypeSafe planning decision experiment",
            "",
            f"Probe: `{probe.get('probe')}`",
            f"Execution: `{'provider-called' if executed else 'offline-only'}`",
            f"Candidates retained: `{len(candidates) if isinstance(candidates, list) else 0}`",
            f"Directed source edges bound: `{len(edges) if isinstance(edges, list) else 0}`",
            f"Historical models: `{experiment['historical_baseline'].get('models', [])}`",
            "",
            "The faithful packet retains the archived question, option IDs, candidate IDs, "
            "ordered paths, brief and exact source bindings. Source attributes retain distinct "
            "value, null and absent states. A source file is not treated as a bound route profile.",
            "",
            "This artifact is an evidence-shape experiment. It does not claim route quality, "
            "current usability, safety, feasibility or adoption.",
            "",
        ]
    )


def prepare_experiment(
    *,
    history_root: Path,
    snapshot_root: Path,
    output_root: Path,
    probe: str,
    config_path: Path | None = None,
    packet_record_id: str = PACKET_RECORD_ID,
    request_record_id: str = REQUEST_RECORD_ID,
    receipt_ids: Sequence[str] = BASELINE_RECEIPT_IDS,
    model: str = DEFAULT_MODEL,
    endpoint: str = TYPE_SAFE_ENDPOINT,
    execute: bool = False,
) -> dict[str, object]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite experiment output: {output_root}")
    packet_record = _read_record(history_root, packet_record_id)
    request_record = _read_record(history_root, request_record_id)
    packet = _payload(packet_record)
    packet_edges = packet.get("source_evidence", {}).get("directed_edges", [])
    if not isinstance(packet_edges, list):
        raise ValueError("archived packet has no directed source edges")
    binding = _snapshot_binding(snapshot_root, config_path)
    source_index = _authoritative_source_index(
        snapshot_root,
        [item for item in packet_edges if isinstance(item, Mapping)],
        snapshot_id=str(binding["snapshot_id"]),
        network_sha256=binding.get("network_sha256")
        if isinstance(binding.get("network_sha256"), str)
        else None,
    )
    receipts = [_read_record(history_root, receipt_id) for receipt_id in receipt_ids]
    experiment = build_scoped_packet(
        packet_record,
        request_record,
        source_index,
        snapshot_binding=binding,
        baseline_receipts=receipts,
    )
    probe_request = build_probe_request(experiment, probe, model=model)
    output_root.mkdir(parents=True, exist_ok=False)
    _write_json(
        output_root / "baseline-request.json",
        experiment["historical_baseline"]["request"],
    )
    _write_json(
        output_root / "baseline-receipts.json",
        experiment["historical_baseline"]["receipts"],
    )
    _write_json(output_root / "faithful-packet.json", experiment["faithful_packet"])
    _write_json(output_root / "source-facts.json", experiment["source_facts"])
    _write_json(output_root / "unknown-catalog.json", experiment["unknown_catalog"])
    _write_json(output_root / "field-diff.json", experiment["field_diff"])
    _write_json(output_root / "probe-request.json", probe_request)
    if execute:
        probe_result = _execute_probe(probe_request, model=model, endpoint=endpoint)
    else:
        probe_result = {
            "status": "not-called",
            "probe": probe,
            "model": model,
            "endpoint": endpoint,
            "reason": "offline preparation; pass --execute for an explicitly authorised call",
        }
    _write_json(output_root / "probe-result.json", probe_result)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "executed" if execute else "prepared",
        "created_at": _utc_now(),
        "probe": probe,
        "model": model,
        "endpoint": endpoint,
        "provider_called": execute,
        "snapshot_binding": experiment["snapshot_binding"],
        "code_bindings": _code_bindings(),
        "historical_baseline": {
            "request_record_id": experiment["historical_baseline"]["request_record_id"],
            "packet_record_id": experiment["historical_baseline"]["packet_record_id"],
            "request_id": experiment["historical_baseline"]["request_id"],
            "models": experiment["historical_baseline"]["models"],
            "usage": experiment["historical_baseline"]["usage"],
            "latency_seconds": experiment["historical_baseline"]["latency_seconds"],
            "request_artifact": "baseline-request.json",
            "receipts_artifact": "baseline-receipts.json",
        },
        "fingerprints": experiment["fingerprints"],
        "candidate_count": len(packet.get("candidates", [])),
        "directed_edge_count": len(packet_edges),
        "artifacts": _artifact_hashes(output_root),
    }
    _write_json(output_root / "run-manifest.json", manifest)
    (output_root / "REPORT.md").write_text(
        _report(experiment, probe_request, execute), encoding="utf-8"
    )
    return {
        "output_root": str(output_root),
        "manifest": manifest,
        "experiment": experiment,
        "probe_request": probe_request,
        "probe_result": probe_result,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--snapshot-root", type=Path)
    parser.add_argument("--probe", choices=PROBES, default="faithful-facts")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--endpoint", default=TYPE_SAFE_ENDPOINT)
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    snapshot_root = args.snapshot_root
    config_path = args.config.resolve() if args.config is not None else None
    if snapshot_root is None:
        if config_path is None:
            raise SystemExit("one of --snapshot-root or --config is required")
        config = AreaDefinition.from_yaml(config_path)
        snapshot_root = config.source.snapshot_dir / config.source.snapshot_id
    result = prepare_experiment(
        history_root=args.history_root.resolve(),
        snapshot_root=snapshot_root.resolve(),
        output_root=args.output_root.resolve(),
        probe=args.probe,
        config_path=config_path,
        model=args.model,
        endpoint=args.endpoint,
        execute=args.execute,
    )
    print(result["output_root"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
