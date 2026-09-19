"""Projection and atomic publication for validated planning output.

The planning engine owns selection and departure decisions.  This module only
copies those decisions into machine-readable records and a WGS84 map
projection.  It never selects an alignment, subtracts geometry, or turns an
unknown into a route.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform

from satn.content_identity import canonical_network_geometry_fingerprint
from satn.planning_contracts import _stable_id


class PublicationValidationError(ValueError):
    """Raised when validated output cannot satisfy the publication contract."""


_VALID_STATUSES = {"validated", "reviewable-incomplete"}
_DEPARTURE_KINDS = {"alternate", "unresolved", "no-loss"}
_ASSET_NAMES = (
    "maplibre-gl.js",
    "maplibre-gl.css",
    "review-lens-state.js",
    "review-map.js",
    "review-map.css",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _json_copy(value: object) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise PublicationValidationError("planning output is not JSON-compatible") from error


def _canonical(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        _canonical(value), ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PublicationValidationError(f"{field} must be non-blank text")
    return value


def _as_list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise PublicationValidationError(f"{field} must be a list")
    return value


def _geometry_from_ref(
    reference: Mapping[str, object], field: str
) -> tuple[dict[str, object], str]:
    geometry = reference.get("geometry")
    crs = reference.get("crs")
    if not isinstance(geometry, Mapping):
        raise PublicationValidationError(f"{field}.geometry is missing")
    if not isinstance(crs, str) or not crs.strip():
        raise PublicationValidationError(f"{field}.crs is missing")
    try:
        source = shape(geometry)
    except (AttributeError, TypeError, ValueError) as error:
        raise PublicationValidationError(f"{field}.geometry is invalid") from error
    if source.is_empty or not source.is_valid:
        raise PublicationValidationError(f"{field}.geometry must be valid and non-empty")
    declared_fingerprint = reference.get("content_fingerprint")
    declared_id = reference.get("geometry_id")
    if _SHA256_RE.fullmatch(str(declared_fingerprint or "")) or str(declared_id or "").startswith(
        "geometry-"
    ):
        try:
            expected_fingerprint = canonical_network_geometry_fingerprint(source, crs)
            expected_id = _stable_id("geometry", expected_fingerprint)
        except (TypeError, ValueError) as error:
            raise PublicationValidationError(
                f"{field} geometry identity cannot be computed"
            ) from error
        if declared_fingerprint != expected_fingerprint or declared_id != expected_id:
            raise PublicationValidationError(
                f"{field} geometry identity does not match coordinates"
            )
    try:
        projector = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        projected = transform(projector.transform, source)
    except (TypeError, ValueError) as error:
        raise PublicationValidationError(f"{field}.crs cannot be projected to WGS84") from error
    if projected.is_empty or not projected.is_valid:
        raise PublicationValidationError(f"{field}.geometry projection is invalid")
    return mapping(projected), str(reference.get("geometry_id") or "")


def _geometry_key(reference: Mapping[str, object], field: str) -> str:
    value = reference.get("geometry_id")
    if not isinstance(value, str) or not value.strip():
        raise PublicationValidationError(f"{field}.geometry_id is missing")
    return value


def _source_refs(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise PublicationValidationError(f"{field} must contain source identifiers")
    return [str(item) for item in value]


def _safe_component(value: str) -> str:
    rendered = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return rendered or "planning-output"


def _output_id(validated: Mapping[str, object]) -> str:
    direct = validated.get("output_id")
    if isinstance(direct, str) and direct.strip():
        return direct
    fingerprint = _required_text(validated.get("output_fingerprint"), "output_fingerprint")
    state_ref = str(
        validated.get("proposal_state_ref") or validated.get("problem_ref") or "planning"
    )
    return f"{state_ref}:{fingerprint[:16]}"


def _validate_input(
    validated_output: Mapping[str, object],
) -> tuple[list[object], dict[str, Mapping[str, object]]]:
    if validated_output.get("schema_version") != "validated-output/v1":
        raise PublicationValidationError("validated output schema is unsupported")
    status = validated_output.get("status")
    if status not in _VALID_STATUSES:
        raise PublicationValidationError(
            "only validated or reviewable-incomplete output may publish"
        )
    _required_text(validated_output.get("output_fingerprint"), "output_fingerprint")
    output_fingerprint = str(validated_output["output_fingerprint"])
    if _SHA256_RE.fullmatch(output_fingerprint):
        identity_payload = dict(validated_output)
        identity_payload.pop("output_fingerprint", None)
        if _fingerprint(identity_payload) != output_fingerprint:
            raise PublicationValidationError("output fingerprint does not match validated output")
    fields = (
        "source_inventory",
        "places",
        "selected_alignments",
        "departures",
        "planning_gaps",
        "unknown_facts",
        "future_interventions",
    )
    for field in fields:
        _as_list(validated_output.get(field), field)
    for field in ("obligations", "unresolved_obligation_refs"):
        if field in validated_output:
            _as_list(validated_output[field], field)
    dispositions = validated_output.get("obligation_dispositions", {})
    if not isinstance(dispositions, Mapping):
        raise PublicationValidationError("obligation_dispositions must be an object")
    if any(
        not isinstance(value, str) or not value.strip()
        for value in _as_list(
            validated_output.get("unresolved_obligation_refs", []), "unresolved_obligation_refs"
        )
    ):
        raise PublicationValidationError("unresolved_obligation_refs must contain identifiers")
    source_inventory = _as_list(validated_output["source_inventory"], "source_inventory")
    sources: dict[str, Mapping[str, object]] = {}
    for index, source in enumerate(source_inventory):
        if not isinstance(source, Mapping):
            raise PublicationValidationError(f"source_inventory[{index}] must be an object")
        corridor_id = _required_text(
            source.get("corridor_id"), f"source_inventory[{index}].corridor_id"
        )
        if corridor_id in sources:
            raise PublicationValidationError(f"source_inventory repeats {corridor_id}")
        reference = source.get("geometry_ref")
        if not isinstance(reference, Mapping):
            raise PublicationValidationError(f"source_inventory[{index}].geometry_ref is missing")
        _geometry_key(reference, f"source_inventory[{index}].geometry_ref")
        _geometry_from_ref(reference, f"source_inventory[{index}].geometry_ref")
        sources[corridor_id] = source
    return source_inventory, sources


def _history_value(history: Mapping[str, object], *names: str) -> object | None:
    for name in names:
        value = history.get(name)
        if value is not None:
            return value
    return None


def _subject_geometry(
    subject_refs: Sequence[str],
    sources: Mapping[str, Mapping[str, object]],
    places: Mapping[str, Mapping[str, object]],
) -> dict[str, object] | None:
    for subject_ref in subject_refs:
        source = sources.get(subject_ref)
        if isinstance(source, Mapping) and isinstance(source.get("geometry_ref"), Mapping):
            geometry, _ = _geometry_from_ref(
                source["geometry_ref"], f"source[{subject_ref}].geometry_ref"
            )
            return geometry
        place = places.get(subject_ref)
        if isinstance(place, Mapping) and isinstance(place.get("geometry_ref"), Mapping):
            geometry, _ = _geometry_from_ref(
                place["geometry_ref"], f"place[{subject_ref}].geometry_ref"
            )
            return geometry
    return None


def project_planning_output(
    validated_output: Mapping[str, object],
    history_metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Project one engine ``validate_proposal`` result without selecting."""

    if not isinstance(validated_output, Mapping):
        raise PublicationValidationError("validated output must be an object")
    source_inventory, sources = _validate_input(validated_output)
    history = {} if history_metadata is None else _json_copy(history_metadata)
    if not isinstance(history, dict):
        raise PublicationValidationError("history metadata must be an object")
    places = _as_list(validated_output["places"], "places")
    place_by_id: dict[str, Mapping[str, object]] = {}
    for index, place in enumerate(places):
        if not isinstance(place, Mapping):
            raise PublicationValidationError(f"places[{index}] must be an object")
        place_id = _required_text(place.get("place_id"), f"places[{index}].place_id")
        if place_id in place_by_id:
            raise PublicationValidationError(f"places repeats {place_id}")
        reference = place.get("geometry_ref")
        if not isinstance(reference, Mapping):
            raise PublicationValidationError(f"places[{index}].geometry_ref is missing")
        _geometry_key(reference, f"places[{index}].geometry_ref")
        _geometry_from_ref(reference, f"places[{index}].geometry_ref")
        place_by_id[place_id] = place

    features: list[dict[str, object]] = []
    source_geometry_ids: dict[str, str] = {}
    source_geometries: dict[str, dict[str, object]] = {}
    for source in source_inventory:
        assert isinstance(source, Mapping)
        corridor_id = str(source["corridor_id"])
        reference = source["geometry_ref"]
        assert isinstance(reference, Mapping)
        geometry, geometry_id = _geometry_from_ref(reference, f"source[{corridor_id}].geometry_ref")
        source_geometry_ids[corridor_id] = geometry_id
        source_geometries[corridor_id] = geometry
        topology = source.get("topology_fact")
        topology_status = topology.get("status") if isinstance(topology, Mapping) else "unassessed"
        classification = str(source.get("classification") or "source-corridor")
        features.append(
            {
                "type": "Feature",
                "id": f"planning-source:{corridor_id}",
                "properties": {
                    "feature_type": "planning-source",
                    "display_role": "source-inventory",
                    "source_corridor_id": corridor_id,
                    "source_section_id": source.get("section_id") or f"{corridor_id}-section",
                    "classification": classification,
                    "mandatory": bool(source.get("mandatory_planning_corridor")),
                    "topology_status": topology_status,
                    "source_refs": _json_copy(source.get("source_refs", [])),
                    "evidence_refs": _json_copy(source.get("evidence_refs", [])),
                    "provision_status": source.get("provision_status"),
                    "map_label": (
                        "A-road source corridor"
                        if classification == "a-road"
                        else "Source corridor"
                    ),
                },
                "geometry": geometry,
            }
        )

    for place_id, place in place_by_id.items():
        reference = place["geometry_ref"]
        assert isinstance(reference, Mapping)
        geometry, _ = _geometry_from_ref(reference, f"place[{place_id}].geometry_ref")
        features.append(
            {
                "type": "Feature",
                "id": f"planning-place:{place_id}",
                "properties": {
                    "feature_type": "planning-place",
                    "place_id": place_id,
                    "name": place.get("name"),
                    "map_label": place.get("name") or place_id,
                },
                "geometry": geometry,
            }
        )

    for index, selection in enumerate(
        _as_list(validated_output["selected_alignments"], "selected_alignments")
    ):
        if not isinstance(selection, Mapping):
            raise PublicationValidationError(f"selected_alignments[{index}] must be an object")
        selected_id = _required_text(
            selection.get("candidate_id") or selection.get("alignment_id"),
            f"selected_alignments[{index}].candidate_id",
        )
        source_refs = _source_refs(
            selection.get("source_corridor_refs", []),
            f"selected_alignments[{index}].source_corridor_refs",
        )
        if any(source_ref not in sources for source_ref in source_refs):
            raise PublicationValidationError(
                f"selected alignment {selected_id} references unknown source"
            )
        reference = selection.get("geometry_ref")
        if not isinstance(reference, Mapping):
            raise PublicationValidationError(
                f"selected_alignments[{index}].geometry_ref is missing"
            )
        geometry, _ = _geometry_from_ref(reference, f"selected_alignments[{index}].geometry_ref")
        cycle = str(
            selection.get("current_or_future")
            or selection.get("provision_status")
            or selection.get("display_state")
            or "unknown"
        ).lower()
        cycle = cycle if cycle in {"current", "future"} else "unknown"
        features.append(
            {
                "type": "Feature",
                "id": f"planning-selected:{selected_id}",
                "properties": {
                    "feature_type": f"planning-selected-{cycle}",
                    "display_role": "selected-alignment",
                    "alignment_id": selected_id,
                    "candidate_id": selected_id,
                    "source_corridor_refs": source_refs,
                    "current_or_future": cycle,
                    "map_label": f"Selected {cycle} alignment",
                },
                "geometry": geometry,
            }
        )

    selected_alignment_ids = {
        str(selection.get("candidate_id") or selection.get("alignment_id"))
        for selection in _as_list(validated_output["selected_alignments"], "selected_alignments")
        if isinstance(selection, Mapping)
        and (selection.get("candidate_id") or selection.get("alignment_id"))
    }
    history_ref = _history_value(history, "history_ref", "history_root", "history_id")
    branch_ref = _history_value(history, "branch_ref", "branch_id")
    for index, departure in enumerate(_as_list(validated_output["departures"], "departures")):
        if not isinstance(departure, Mapping):
            raise PublicationValidationError(f"departures[{index}] must be an object")
        departure_id = _required_text(
            departure.get("departure_id"), f"departures[{index}].departure_id"
        )
        source_refs = _source_refs(
            departure.get("source_corridor_refs", []),
            f"departures[{index}].source_corridor_refs",
        )
        if any(source_ref not in sources for source_ref in source_refs):
            raise PublicationValidationError(f"departure {departure_id} references unknown source")
        extent = departure.get("extent")
        if extent not in {"full", "partial"}:
            raise PublicationValidationError(
                f"departure {departure_id} extent must be full or partial"
            )
        reason = _required_text(departure.get("reason"), f"departures[{index}].reason")
        evidence_refs = _source_refs(
            departure.get("evidence_refs", []), f"departures[{index}].evidence_refs"
        )
        outcome = departure.get("outcome")
        if not isinstance(outcome, Mapping) or outcome.get("kind") not in _DEPARTURE_KINDS:
            raise PublicationValidationError(f"departure {departure_id} outcome is invalid")
        affected_refs = departure.get("affected_geometry_refs")
        if not isinstance(affected_refs, list) or not affected_refs:
            raise PublicationValidationError(f"departure {departure_id} has no affected geometry")
        affected_ids: set[str] = set()
        for affected_index, reference in enumerate(affected_refs):
            if not isinstance(reference, Mapping):
                raise PublicationValidationError(f"departure {departure_id} geometry is invalid")
            affected_id = _geometry_key(
                reference, f"departure {departure_id}.affected_geometry_refs[{affected_index}]"
            )
            _geometry_from_ref(
                reference, f"departure {departure_id}.affected_geometry_refs[{affected_index}]"
            )
            affected_ids.add(affected_id)
        if extent == "full":
            expected_ids = {source_geometry_ids[source_ref] for source_ref in source_refs}
            if not expected_ids.issubset(affected_ids):
                raise PublicationValidationError(
                    f"full departure {departure_id} omits original source geometry"
                )
        a_road = any(
            str(sources[source_ref].get("classification")) == "a-road" for source_ref in source_refs
        )
        outcome_kind = str(outcome["kind"])
        if outcome_kind == "alternate":
            outcome_ref = outcome.get("candidate_id")
            if not isinstance(outcome_ref, str) or outcome_ref not in selected_alignment_ids:
                raise PublicationValidationError(
                    f"alternate departure {departure_id} must reference a selected alignment"
                )
            outcome_label = f"selected alternative {outcome_ref or 'unidentified'}"
        elif outcome_kind == "unresolved":
            outcome_ref = outcome.get("gap_id")
            outcome_label = f"unresolved loss {outcome_ref or 'unidentified'}"
        else:
            outcome_ref = outcome.get("no_loss_ref") or departure_id
            outcome_label = "evidence-backed no loss"
        departure_metadata = {
            key: _json_copy(departure[key])
            for key in (
                "decision_class",
                "decision_origin",
                "history_origin",
                "provider",
                "model",
                "usage",
            )
            if key in departure
        }
        for affected_index, reference in enumerate(affected_refs):
            assert isinstance(reference, Mapping)
            geometry, geometry_id = _geometry_from_ref(
                reference,
                f"departure {departure_id}.affected_geometry_refs[{affected_index}]",
            )
            features.append(
                {
                    "type": "Feature",
                    "id": f"planning-departure:{departure_id}:{geometry_id}",
                    "properties": {
                        "feature_type": "planning-departure",
                        "display_role": "explicit-departure",
                        "departure_id": departure_id,
                        "source_corridor_refs": source_refs,
                        "affected_geometry_ref": geometry_id,
                        "extent": extent,
                        "reason": reason,
                        "evidence_refs": evidence_refs,
                        "decision_ref": departure.get("decision_ref") or departure_id,
                        "history_ref": history_ref,
                        "branch_ref": branch_ref,
                        "outcome": outcome_kind,
                        "outcome_ref": outcome_ref,
                        "outcome_label": outcome_label,
                        "map_label": (
                            f"A-road corridor departure · {str(extent).upper()}"
                            if a_road
                            else f"Source corridor departure · {str(extent).upper()}"
                        ),
                        **departure_metadata,
                    },
                    "geometry": geometry,
                }
            )

    for feature_type, field in (
        ("planning-unknown", "unknown_facts"),
        ("planning-gap", "planning_gaps"),
    ):
        for index, record in enumerate(_as_list(validated_output[field], field)):
            if not isinstance(record, Mapping):
                raise PublicationValidationError(f"{field}[{index}] must be an object")
            raw_refs = record.get("subject_refs")
            if isinstance(raw_refs, list):
                subject_refs = [str(item) for item in raw_refs]
            elif record.get("subject_id") is not None:
                subject_refs = [str(record["subject_id"])]
            elif record.get("gap_id") is not None:
                subject_refs = [str(record["gap_id"])]
            else:
                subject_refs = []
            geometry = _subject_geometry(subject_refs, sources, place_by_id)
            features.append(
                {
                    "type": "Feature",
                    "id": (
                        f"{feature_type}:"
                        f"{record.get('unknown_id') or record.get('gap_id') or index}"
                    ),
                    "properties": {
                        **_json_copy(dict(record)),
                        "feature_type": feature_type,
                        "display_role": "unknown-or-gap",
                        "subject_refs": subject_refs,
                        "map_label": "Unknown fact"
                        if feature_type == "planning-unknown"
                        else "Planning gap",
                    },
                    "geometry": geometry,
                }
            )

    obligations = _as_list(validated_output.get("obligations", []), "obligations")
    obligations_by_id = {
        str(item.get("obligation_id")): item
        for item in obligations
        if isinstance(item, Mapping) and item.get("obligation_id")
    }
    for obligation_id in _as_list(
        validated_output.get("unresolved_obligation_refs", []), "unresolved_obligation_refs"
    ):
        obligation_key = str(obligation_id)
        obligation = obligations_by_id.get(obligation_key, {})
        raw_refs = obligation.get("subject_refs") if isinstance(obligation, Mapping) else None
        if not isinstance(raw_refs, list):
            raw_refs = (
                obligation.get("source_corridor_refs", [])
                if isinstance(obligation, Mapping)
                else []
            )
        subject_refs = [str(item) for item in raw_refs] if isinstance(raw_refs, list) else []
        features.append(
            {
                "type": "Feature",
                "id": f"planning-unresolved-obligation:{obligation_key}",
                "properties": {
                    "feature_type": "planning-unknown",
                    "display_role": "unknown-or-gap",
                    "unknown_kind": "unresolved-obligation",
                    "obligation_id": obligation_key,
                    "status": "unresolved",
                    "subject_refs": subject_refs,
                    "map_label": "Unresolved planning obligation",
                },
                "geometry": _subject_geometry(subject_refs, sources, place_by_id),
            }
        )

    for index, intervention in enumerate(
        _as_list(validated_output["future_interventions"], "future_interventions")
    ):
        if not isinstance(intervention, Mapping):
            raise PublicationValidationError(f"future_interventions[{index}] must be an object")
        target_refs = (
            [str(item) for item in intervention.get("target_refs", [])]
            if isinstance(intervention.get("target_refs"), list)
            else []
        )
        for target_ref in target_refs:
            source_geometry = source_geometries.get(target_ref)
            if source_geometry is None:
                continue
            features.append(
                {
                    "type": "Feature",
                    "id": f"planning-future-intervention:{target_ref}:{index}",
                    "properties": {
                        **_json_copy(dict(intervention)),
                        "feature_type": "planning-future-intervention",
                        "target_ref": target_ref,
                        "map_label": "Future intervention",
                    },
                    "geometry": source_geometry,
                }
            )

    features.sort(key=lambda feature: str(feature.get("id")))
    geojson = {
        "type": "FeatureCollection",
        "name": "SATN planning publication",
        "features": features,
    }
    projected: dict[str, object] = {
        "schema_version": "planning-publication/v1",
        "output_id": _output_id(validated_output),
        "status": validated_output["status"],
        "output_fingerprint": validated_output["output_fingerprint"],
        "state_fingerprint": validated_output.get("state_fingerprint")
        or history.get("state_fingerprint"),
        "proposal_state_ref": validated_output.get("proposal_state_ref"),
        "problem_ref": validated_output.get("problem_ref"),
        "source_inventory": _json_copy(source_inventory),
        "places": _json_copy(places),
        "selected_alignments": _json_copy(validated_output["selected_alignments"]),
        "obligations": _json_copy(validated_output.get("obligations", [])),
        "obligation_dispositions": _json_copy(validated_output.get("obligation_dispositions", {})),
        "unresolved_obligation_refs": _json_copy(
            validated_output.get("unresolved_obligation_refs", [])
        ),
        "departures": _json_copy(validated_output["departures"]),
        "planning_gaps": _json_copy(validated_output["planning_gaps"]),
        "unknown_facts": _json_copy(validated_output["unknown_facts"]),
        "future_interventions": _json_copy(validated_output["future_interventions"]),
        "validation": _json_copy(validated_output.get("validation", {})),
        "history": history,
        "geojson": geojson,
    }
    for metadata_field in (
        "decision_class",
        "decision_origin",
        "history_origin",
        "provider",
        "model",
        "usage",
    ):
        if metadata_field in validated_output:
            projected[metadata_field] = _json_copy(validated_output[metadata_field])
    projected["projection_fingerprint"] = _fingerprint(
        {key: value for key, value in projected.items() if key != "projection_fingerprint"}
    )
    return projected


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _render_planning_html(template: str) -> str:
    replacements = {
        "__TITLE__": "SATN planning publication",
        "__DISCLAIMER__": (
            "This proposed network shows validated planning decisions, affected source "
            "sections, and unresolved evidence for review."
        ),
        "__GENTLE_MAX_PCT__": "5",
        "__NOTICEABLE_MAX_PCT__": "8",
        "__STEEP_MAX_PCT__": "12",
        "__VERY_STEEP_MAX_PCT__": "20",
        "__ATM_STATE__": "disabled",
        "__ATM_STATUS__": "Planning publication does not include ATM comparison evidence.",
        "__REFERENCE_SATN_STATE__": "hidden",
        "__REFERENCE_SATN_EVIDENCE__": "",
        "__REVIEW_LENS_STATE_JS__": "review-lens-state.js",
        "__REVIEW_MAP_CSS__": "review-map.css",
        "__REVIEW_MAP_JS__": "review-map.js",
    }
    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered.replace(
        'href="planning-output.json"', 'href="../planning-output.json"'
    ).replace('href="planning-network.geojson"', 'href="../planning-network.geojson"')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _staged_bundle_valid(staged: Path, projected: Mapping[str, object]) -> None:
    try:
        loaded = json.loads((staged / "planning-output.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublicationValidationError("publication artifact is missing or invalid") from error
    if _canonical(loaded) != _canonical(projected):
        raise PublicationValidationError("staged planning output changed during publication")
    try:
        network = json.loads((staged / "planning-network.geojson").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublicationValidationError("publication artifact is missing or invalid") from error
    if _canonical(network) != _canonical(projected["geojson"]):
        raise PublicationValidationError("staged planning GeoJSON differs from machine output")
    if network.get("type") != "FeatureCollection":
        raise PublicationValidationError("planning GeoJSON is not a feature collection")
    feature_ids = [feature.get("id") for feature in network.get("features", [])]
    if any(not isinstance(feature_id, str) for feature_id in feature_ids) or len(
        feature_ids
    ) != len(set(feature_ids)):
        raise PublicationValidationError("planning GeoJSON feature IDs are not stable and unique")


def _existing_bundle_valid(
    target: Path, projected: Mapping[str, object], manifest: Mapping[str, object]
) -> None:
    """Verify a protected bundle before allowing the current pointer to move."""

    _staged_bundle_valid(target, projected)
    if manifest.get("output_id") != projected.get("output_id"):
        raise PublicationValidationError("publication manifest output identity is stale")
    if manifest.get("output_fingerprint") != projected.get("output_fingerprint"):
        raise PublicationValidationError("publication manifest output fingerprint is stale")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise PublicationValidationError("publication manifest artifact closure is missing")
    actual_paths = {
        str(path.relative_to(target))
        for path in target.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    declared_paths = {str(path) for path in artifacts}
    if actual_paths != declared_paths:
        raise PublicationValidationError("publication artifact closure is incomplete")
    for relative, expected in artifacts.items():
        if not isinstance(expected, str) or not _SHA256_RE.fullmatch(expected):
            raise PublicationValidationError("publication artifact hash is invalid")
        path = target / str(relative)
        if _sha256(path) != expected:
            raise PublicationValidationError("publication artifact hash does not match manifest")


def _write_pointer(root: Path, manifest: Mapping[str, object]) -> Path:
    pointer = {
        "schema_version": "planning-publication-pointer/v1",
        "output_id": manifest["output_id"],
        "output_fingerprint": manifest["output_fingerprint"],
        "publication_path": manifest["publication_path"],
        "manifest_sha256": manifest["manifest_sha256"],
    }
    temporary = root / f".current-{uuid4().hex}.json"
    temporary.write_bytes(_json_bytes(pointer))
    os.replace(temporary, root / "current.json")
    return root / "current.json"


def publish_planning_output(
    validated_output: Mapping[str, object],
    destination: str | Path,
    history_metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Atomically publish a validated planning bundle and current pointer."""

    projected = project_planning_output(validated_output, history_metadata)
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    publications = root / "publications"
    publications.mkdir(parents=True, exist_ok=True)
    output_id = str(projected["output_id"])
    target = publications / _safe_component(output_id)
    projected_bytes = _json_bytes(projected)

    if target.exists():
        existing_path = target / "planning-output.json"
        if not existing_path.exists() or existing_path.read_bytes() != projected_bytes:
            raise PublicationValidationError(
                "protected historical publication cannot be overwritten"
            )
        manifest_path = target / "manifest.json"
        if not manifest_path.exists():
            raise PublicationValidationError("protected historical publication has no manifest")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PublicationValidationError(
                "protected historical publication manifest is invalid"
            ) from error
        if not isinstance(manifest, Mapping):
            raise PublicationValidationError("protected historical publication manifest is invalid")
        _existing_bundle_valid(target, projected, manifest)
        manifest_hash = _sha256(manifest_path)
        manifest = dict(manifest)
        manifest["manifest_sha256"] = manifest_hash
        pointer = _write_pointer(root, manifest)
        return {
            "output_id": output_id,
            "output_fingerprint": projected["output_fingerprint"],
            "publication_dir": str(target),
            "current_pointer": str(pointer),
            "manifest": manifest,
        }

    staging = Path(tempfile.mkdtemp(prefix=".planning-publication-", dir=str(root)))
    try:
        _write_json(staging / "planning-output.json", projected)
        _write_json(staging / "planning-network.geojson", projected["geojson"])
        _write_json(staging / "source-inventory.json", projected["source_inventory"])
        _write_json(staging / "departure-decisions.json", projected["departures"])
        _write_json(
            staging / "unknowns.json",
            {
                "planning_gaps": projected["planning_gaps"],
                "unknown_facts": projected["unknown_facts"],
            },
        )
        _write_json(staging / "history.json", projected["history"])

        review = staging / "review-map"
        assets = review / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        asset_root = Path(__file__).with_name("assets")
        for name in _ASSET_NAMES:
            source = asset_root / name
            if not source.is_file():
                raise PublicationValidationError(f"bundled review asset is missing: {name}")
            shutil.copyfile(source, assets / name)
        template = (asset_root / "review-map.html").read_text(encoding="utf-8")
        (review / "index.html").write_text(_render_planning_html(template), encoding="utf-8")
        javascript_output = json.dumps(
            projected, ensure_ascii=True, allow_nan=False, sort_keys=True
        ).replace("<", "\\u003c")
        data_js = (
            "window.SATN_DATA = {};\nwindow.SATN_PLANNING_OUTPUT = " + javascript_output + ";\n"
        )
        (review / "data.js").write_text(data_js, encoding="utf-8")
        _staged_bundle_valid(staging, projected)

        artifacts: dict[str, str] = {}
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                artifacts[str(path.relative_to(staging))] = _sha256(path)
        manifest: dict[str, object] = {
            "schema_version": "planning-publication-manifest/v1",
            "output_id": output_id,
            "output_fingerprint": projected["output_fingerprint"],
            "state_fingerprint": projected.get("state_fingerprint"),
            "proposal_state_ref": projected.get("proposal_state_ref"),
            "history": projected["history"],
            "artifacts": artifacts,
            "publication_path": str(Path("publications") / _safe_component(output_id)),
        }
        _write_json(staging / "manifest.json", manifest)
        _staged_bundle_valid(staging, projected)

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise PublicationValidationError(
                "protected historical publication appeared during publication"
            )
        os.replace(staging, target)
        manifest_path = target / "manifest.json"
        manifest = dict(json.loads(manifest_path.read_text(encoding="utf-8")))
        manifest["manifest_sha256"] = _sha256(manifest_path)
        pointer = _write_pointer(root, manifest)
        return {
            "output_id": output_id,
            "output_fingerprint": projected["output_fingerprint"],
            "publication_dir": str(target),
            "current_pointer": str(pointer),
            "manifest": manifest,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


__all__ = [
    "PublicationValidationError",
    "project_planning_output",
    "publish_planning_output",
]
