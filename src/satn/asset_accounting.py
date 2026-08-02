"""Deterministic accounting for governed reusable active-travel assets.

The accounting seam deliberately keeps identity, evidence, intervention state,
and candidate participation separate.  It is an inventory, not a route
selection policy: an asset with no candidate participation is retained with an
explicit typed disposition.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from contextlib import suppress
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, MultiLineString, mapping

from satn.constants import DISCLAIMER, SCHEMA_VERSION
from satn.evidence_contracts import evidence_geometry_fingerprint

_CONTEXT_KINDS = {
    "ncn-route": "current-ncn",
    "ncn-link": "ncn-link",
    "declassified-ncn-route": "reclassified-ncn",
    "greenway-cycleway": "greenway",
    "cycleway": "cycle-track",
    "shared-use-path": "shared-use-path",
    "public-footpath": "public-footpath",
    "public-bridleway": "public-bridleway",
    "restricted-byway": "restricted-byway",
    "byway-open-to-all-traffic": "byway-open-to-all-traffic",
    "prow-class-unknown": "prow-class-unknown",
    "former-railway": "former-railway",
    "local-connector": "local-connector",
    "proposed-new-corridor": "proposed-new-corridor",
    "a-road-spine": "a-road",
    "b-road": "b-road",
    "unclassified-road": "unclassified-road",
}
_ROUTABLE_HIGHWAYS = {
    "cycleway",
    "path",
    "track",
    "footway",
    "bridleway",
    "steps",
}
_PROW_DESIGNATIONS = {
    "public_footpath": "public-footpath",
    "public_bridleway": "public-bridleway",
    "bridleway": "public-bridleway",
    "restricted_byway": "restricted-byway",
    "byway_open_to_all_traffic": "byway-open-to-all-traffic",
    "byway": "byway-open-to-all-traffic",
}
_VALID_EVIDENCE_STATES = {
    "supported",
    "provisional",
    "conflicting",
    "stale",
    "missing",
    "coverage_unknown",
    "not_applicable",
    "unknown",
}
_RAW_ATTRIBUTE_FIELDS = (
    "highway",
    "bicycle",
    "foot",
    "access",
    "designation",
    "prow_class",
    "right_of_way",
    "shared_use",
    "surface",
    "ref",
)

# Classification is deliberately ordered as a governed precedence, rather than
# inheriting the row order of a source export.  ``alignment_bases`` retains all
# defensible classifications while ``asset_kind``/``primary_alignment_basis``
# expose this one stable summary value.
_ASSET_KIND_PRECEDENCE = (
    "current-ncn",
    "ncn-link",
    "reclassified-ncn",
    "greenway",
    "cycle-track",
    "shared-use-path",
    "public-footpath",
    "public-bridleway",
    "restricted-byway",
    "byway-open-to-all-traffic",
    "prow-class-unknown",
    "local-connector",
    "former-railway",
    "a-road",
    "b-road",
    "classified-unnumbered-road",
    "unclassified-road",
    "proposed-new-corridor",
)
_ASSET_KIND_PRECEDENCE_INDEX = {
    kind: index for index, kind in enumerate(_ASSET_KIND_PRECEDENCE)
}


def _json_value(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        with suppress(AttributeError, ValueError):
            value = value.item()
    try:
        if not isinstance(value, str) and bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _json_value(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _full_sha256(value: object) -> bool:
    text = _text(value)
    if text is None or len(text) != 64:
        return False
    try:
        int(text, 16)
    except ValueError:
        return False
    return True


def _authoritative_lineage(row: pd.Series) -> bool:
    """Return whether a supported claim carries a complete source lineage."""
    source_export = row.get("source_export_sha256") or row.get("raw_bytes_sha256")
    authority = row.get("publisher") or row.get("source_authority_role")
    return bool(
        _text(row.get("source_family"))
        and _text(row.get("dataset"))
        and _text(authority)
        and (_text(row.get("effective_date")) or _text(row.get("publisher_release")))
        and _text(row.get("licence"))
        and _full_sha256(source_export)
        and _text(row.get("claim_type"))
        and _text(row.get("evidence_mode"))
        and _text(row.get("coverage_state"))
        and (_text(row.get("ingestion_contract")) or _text(row.get("parser_version")))
    )


def _merge_evidence_state(left: str, right: str) -> str:
    priority = {
        "provisional": 0,
        "supported": 1,
        "coverage_unknown": 2,
        "missing": 3,
        "stale": 4,
        "conflicting": 5,
        "unknown": 6,
        "not_applicable": -1,
    }
    if left == right:
        return left
    if "conflicting" in {left, right}:
        return "conflicting"
    return max((left, right), key=lambda value: priority.get(value, 0))


def _kind_sort_key(kind: object) -> tuple[int, str]:
    value = str(kind)
    return (_ASSET_KIND_PRECEDENCE_INDEX.get(value, len(_ASSET_KIND_PRECEDENCE)), value)


def _primary_alignment_basis(bases: Iterable[object]) -> str:
    """Select the stable primary basis without asserting a source preference."""
    values = {str(value) for value in bases if value}
    return min(values, key=_kind_sort_key) if values else "unknown"


def _derive_intervention_state(
    kind: str,
    provenance: list[dict[str, object]],
) -> str:
    if kind == "proposed-new-corridor":
        return "proposed-new-link"
    positive_claims = {"current-cycling-provision", "cycling-access"}
    if kind in {"current-ncn", "ncn-link", "greenway", "cycle-track", "shared-use-path"} and any(
        item.get("observation_state") == "supported"
        and item.get("claim_type") in positive_claims
        and item.get("raw_attributes", {}).get("bicycle") in {"yes", "designated"}
        for item in provenance
    ):
        return "existing-provision"
    return "upgrade-required"


def _geometry_fingerprint(geometry: object) -> str:
    return hashlib.sha256(geometry.wkb).hexdigest()  # type: ignore[union-attr]


def _canonical_metric_geometry(geometry: object) -> object:
    """Canonicalise BNG line coordinates at evidence-contract millimetres."""
    if isinstance(geometry, LineString):
        coordinates = [
            (
                int((Decimal(str(x)) * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)),
                int((Decimal(str(y)) * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)),
            )
            for x, y in geometry.coords
        ]
        deduplicated = [
            item
            for index, item in enumerate(coordinates)
            if index == 0 or item != coordinates[index - 1]
        ]
        ordered = min(deduplicated, list(reversed(deduplicated)))
        return LineString([(x / 1000, y / 1000) for x, y in ordered])
    if isinstance(geometry, MultiLineString):
        parts = sorted(
            (_canonical_metric_geometry(part) for part in geometry.geoms),
            key=lambda item: item.wkb,
        )
        return MultiLineString(parts)
    return geometry


def _line_geometry(frame: gpd.GeoDataFrame, index: object, row: pd.Series) -> object | None:
    geometry = row.geometry
    if geometry is None or geometry.is_empty or not hasattr(geometry, "wkb"):
        return None
    return geometry


def _designation(row: pd.Series) -> str | None:
    for key in ("designation", "prow_class", "right_of_way", "route_type"):
        value = _text(row.get(key))
        if value:
            return value.lower().replace(" ", "_")
    return None


def _context_kind(row: pd.Series) -> str | None:
    feature_type = (_text(row.get("feature_type")) or "").lower().replace("_", "-")
    if feature_type in _CONTEXT_KINDS:
        return _CONTEXT_KINDS[feature_type]
    if "prow" in feature_type or "right-of-way" in feature_type:
        return "prow-class-unknown"
    return None


def _network_kind(row: pd.Series) -> str | None:
    ref = (_text(row.get("ref")) or "").upper()
    if ref.startswith("A") and ref[1:].replace(" ", "").isdigit():
        return "a-road"
    if ref.startswith("B") and ref[1:].replace(" ", "").isdigit():
        return "b-road"
    railway = (_text(row.get("railway")) or "").lower()
    if railway in {"abandoned", "disused", "historic"}:
        return "former-railway"
    designation = _designation(row)
    if designation in _PROW_DESIGNATIONS:
        return _PROW_DESIGNATIONS[designation]
    highway = (_text(row.get("highway")) or "").lower()
    if highway in {"unclassified", "residential", "living_street"}:
        return "unclassified-road"
    if highway in {"primary", "secondary", "tertiary", "trunk"}:
        return "classified-unnumbered-road"
    if highway not in _ROUTABLE_HIGHWAYS:
        return None
    if highway == "cycleway":
        return "cycle-track"
    if highway in {"bridleway"}:
        return "public-bridleway"
    if highway in {"footway", "steps"}:
        bicycle = (_text(row.get("bicycle")) or "").lower()
        shared_use = (_text(row.get("shared_use")) or "").lower()
        if bicycle in {"yes", "designated"} or shared_use in {"yes", "designated"}:
            return "shared-use-path"
        return None
    return None


def _evidence_row(
    row: pd.Series,
    *,
    kind: str,
    geometry_sha256: str,
    evidence_geometry_fingerprint_value: str | None,
) -> dict[str, object]:
    evidence_id = _text(row.get("evidence_id"))
    source_id = _text(row.get("source_id")) or _text(row.get("source_feature_id"))
    attrs = {
        str(key): _json_value(value)
        for key, value in row.items()
        if key != "geometry" and _json_value(value) is not None
    }
    source_sha256 = _sha256({"kind": kind, "geometry_sha256": geometry_sha256, "attributes": attrs})
    requested_state = _text(row.get("evidence_state")) or _text(row.get("observation_state"))
    observed_state = requested_state
    if observed_state not in _VALID_EVIDENCE_STATES:
        observed_state = "provisional"
    if observed_state == "supported" and not _authoritative_lineage(row):
        observed_state = "provisional"
    raw_attributes = {
        field: _json_value(row.get(field))
        for field in _RAW_ATTRIBUTE_FIELDS
        if _json_value(row.get(field)) is not None
    }
    source_export_sha256 = _text(
        row.get("source_export_sha256") or row.get("raw_bytes_sha256")
    )
    return {
        "evidence_id": evidence_id,
        "source_id": source_id,
        "observation_sha256": source_sha256,
        "source_sha256": source_sha256,
        "geometry_sha256": geometry_sha256,
        "evidence_geometry_fingerprint": evidence_geometry_fingerprint_value,
        "feature_type": _text(row.get("feature_type")),
        "claim_type": _text(row.get("claim_type")),
        "ingestion_contract": _text(row.get("ingestion_contract")),
        "parser_version": _text(row.get("parser_version")),
        "raw_attributes": raw_attributes,
        "source_family": _text(row.get("source_family")),
        "dataset": _text(row.get("dataset")),
        "publisher": _text(row.get("publisher")),
        "source_authority_role": _text(row.get("source_authority_role")),
        "publisher_release": _text(row.get("publisher_release")),
        "effective_date": _text(row.get("effective_date")),
        "licence": _text(row.get("licence")),
        "source_uri": _text(row.get("source_uri")),
        "source_export_sha256": source_export_sha256,
        "coverage_state": _text(row.get("coverage_state")),
        "evidence_mode": _text(row.get("evidence_mode")),
        "observation_state": observed_state,
    }


def _field(value: object, name: str, default: object = None) -> object:
    """Read a field from a model or mapping without importing candidate modules."""
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _values(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(
            text
            for item in value
            if (text := _text(getattr(item, "value", item))) is not None
        )
    text = _text(getattr(value, "value", value))
    return (text,) if text is not None else ()


def _candidate_records(compiled: object) -> Iterable[tuple[object, object]]:
    """Yield candidate-set/record pairs from the two compiler preparation seams."""
    for preparation_name, container_name in (
        ("spine_access_candidate_preparation", "prepared_spine_access_connections"),
        ("strategic_corridor_preparation", "units"),
    ):
        preparation = _field(compiled, preparation_name)
        if preparation is None:
            continue
        containers = _field(preparation, container_name, ())
        for container in containers or ():
            candidate_set = _field(container, "candidate_set")
            records = tuple(_field(container, "candidate_records", ()) or ())
            if records:
                for record in records:
                    yield candidate_set, record
                continue
            # Empty records carry no binding facts.  Do not infer participation
            # merely because a Candidate Set exists.


def _candidate_participation_disposition(
    candidate_set: object,
    candidate: object,
    record: object,
) -> str:
    """Map preparation facts to the bounded accounting disposition vocabulary."""
    topology = _field(candidate, "topology_state")
    topology_state = _text(getattr(topology, "value", topology))
    if topology_state == "unsatisfied":
        return "topology-unconnected"
    candidate_id = _text(_field(candidate, "candidate_id"))
    admissions = _field(candidate_set, "admissions", ())
    admission = next(
        (
            item
            for item in admissions or ()
            if _text(_field(item, "candidate_id")) == candidate_id
        ),
        None,
    )
    admission_value = _field(admission, "disposition")
    admission_disposition = _text(getattr(admission_value, "value", admission_value))
    preparation_disposition = _text(_field(record, "preparation_disposition"))
    if admission_disposition != "admitted" or preparation_disposition in {
        "rejected",
        "excluded",
    }:
        return "ineligible"
    # Candidate preparation is explicitly non-selecting.  A future selected
    # disposition can flow through this seam, but is never inferred here.
    return "eligible-not-selected"


def _exact_candidate_participations(
    compiled: object,
    asset: Mapping[str, object],
) -> list[dict[str, object]]:
    """Return only candidate records carrying an explicit source/evidence binding."""
    asset_identities = {
        str(value)
        for value in asset.get("governed_source_identities", ())
        if value
    }
    if not asset_identities:
        return []
    participations: list[dict[str, object]] = []
    for candidate_set, record in _candidate_records(compiled):
        candidate = _field(record, "candidate")
        if candidate is None:
            continue
        explicit_identities = set(
            _values(_field(candidate, "governed_evidence_ids"))
            + _values(_field(candidate, "provenance_ids"))
            + _values(_field(record, "source_ids"))
            + _values(_field(record, "evidence_ids"))
        )
        matched_identities = sorted(asset_identities & explicit_identities)
        if not matched_identities:
            continue
        candidate_set_id = _text(_field(candidate_set, "candidate_set_id"))
        candidate_id = _text(_field(candidate, "candidate_id"))
        if not candidate_set_id or not candidate_id:
            continue
        role = _field(candidate_set, "network_role")
        role_value = _text(getattr(role, "value", role))
        disposition = _candidate_participation_disposition(
            candidate_set,
            candidate,
            record,
        )
        participations.append(
            {
                "candidate_set_id": candidate_set_id,
                "candidate_set_fingerprint": _text(
                    _field(candidate_set, "candidate_set_fingerprint")
                ),
                "candidate_id": candidate_id,
                "candidate_role": role_value,
                "participation_state": "participating",
                "selection_disposition": disposition,
                "binding_basis": "explicit-governed-source-identity",
                "binding_identities": matched_identities,
                "preparation_disposition": _text(
                    _field(record, "preparation_disposition")
                ),
            }
        )
    return sorted(
        participations,
        key=lambda item: (
            str(item["candidate_set_id"]),
            str(item["candidate_id"]),
        ),
    )


def build_asset_accounting(
    context: gpd.GeoDataFrame,
    network: gpd.GeoDataFrame,
    _compiled: object,
) -> dict[str, object]:
    """Build an exhaustive, deterministic asset accounting payload."""

    assets: OrderedDict[str, dict[str, object]] = OrderedDict()
    excluded_observations: list[dict[str, object]] = []
    sources: Iterable[tuple[gpd.GeoDataFrame, str]] = ((context, "context"), (network, "network"))
    for frame, origin in sources:
        if not isinstance(frame, gpd.GeoDataFrame) or frame.empty:
            continue
        for index, row in frame.sort_index().iterrows():
            kind = _context_kind(row) if origin == "context" else _network_kind(row)
            geometry = _line_geometry(frame, index, row)
            if geometry is None:
                continue
            try:
                if frame.crs is None:
                    raise ValueError("asset evidence geometry has no CRS")
                identity_geometry = gpd.GeoSeries([geometry], crs=frame.crs).to_crs(27700).iloc[0]
                identity_geometry = _canonical_metric_geometry(identity_geometry)
            except (ValueError, TypeError):
                excluded = _evidence_row(
                    row,
                    kind="invalid-crs-geometry",
                    geometry_sha256=_geometry_fingerprint(geometry),
                    evidence_geometry_fingerprint_value=None,
                )
                excluded.update(
                    {
                        "accounting_disposition": "excluded-invalid-crs",
                        "reason": (
                            "source geometry CRS is missing or cannot be transformed "
                            "to EPSG:27700"
                        ),
                    }
                )
                excluded_observations.append(excluded)
                continue
            geometry_sha256 = _geometry_fingerprint(identity_geometry)
            evidence_geometry_identity = evidence_geometry_fingerprint(
                identity_geometry,
                27700,
            )
            if kind is None:
                highway = (_text(row.get("highway")) or "").lower()
                designation = _designation(row)
                bare_reusable_tag = origin == "network" and (
                    highway in {"footway", "path", "track"}
                    or designation in {"local_connector", "local-connector"}
                )
                excluded = _evidence_row(
                    row,
                    kind=("unbound-network-feature" if bare_reusable_tag else "unclassified-line"),
                    geometry_sha256=geometry_sha256,
                    evidence_geometry_fingerprint_value=evidence_geometry_identity,
                )
                excluded.update(
                    {
                        "accounting_disposition": (
                            "excluded-unbound" if bare_reusable_tag else "excluded-out-of-scope"
                        ),
                        "reason": (
                            "bare network tag lacks governed legal, cycling or shared-use evidence"
                            if bare_reusable_tag
                            else "source line has no governed reusable alignment classification"
                        ),
                    }
                )
                excluded_observations.append(excluded)
                continue
            evidence = _evidence_row(
                row,
                kind=kind,
                geometry_sha256=geometry_sha256,
                evidence_geometry_fingerprint_value=evidence_geometry_identity,
            )
            asset_id = f"asset-{evidence_geometry_identity}"
            existing = assets.get(asset_id)
            if existing is None:
                assets[asset_id] = {
                    "asset_id": asset_id,
                    "asset_identity_sha256": evidence_geometry_identity,
                    "evidence_geometry_fingerprint": evidence_geometry_identity,
                    "asset_kind": kind,
                    "alignment_bases": [kind],
                    "primary_alignment_basis": kind,
                    "scope_state": "in-scope",
                    "opportunity_state": "reusable-asset",
                    # Classification alone never proves present condition or rights.
                    "intervention_state": _derive_intervention_state(kind, [evidence]),
                    "evidence_state": evidence["observation_state"],
                    "source_provenance": [evidence],
                    "evidence_state_reasons": [
                        {
                            "source_id": evidence.get("source_id"),
                            "evidence_id": evidence.get("evidence_id"),
                            "state": evidence.get("observation_state"),
                        }
                    ],
                    "conflict_roster": (
                        sorted(
                            item
                            for item in (evidence.get("source_id"), evidence.get("evidence_id"))
                            if item
                        )
                        if evidence["observation_state"] == "conflicting"
                        else []
                    ),
                    "candidate_participations": [],
                    "_geometry": identity_geometry,
                    "_crs": 27700,
                }
            else:
                if kind not in existing["alignment_bases"]:
                    existing["alignment_bases"].append(kind)
                existing["source_provenance"].append(evidence)
                existing["evidence_state"] = _merge_evidence_state(
                    str(existing["evidence_state"]),
                    str(evidence["observation_state"]),
                )
                existing["evidence_state_reasons"].append(
                    {
                        "source_id": evidence.get("source_id"),
                        "evidence_id": evidence.get("evidence_id"),
                        "state": evidence.get("observation_state"),
                    }
                )
                if existing["evidence_state"] == "conflicting":
                    existing["conflict_roster"] = sorted(
                        {
                            item
                            for reason in existing["evidence_state_reasons"]
                            for item in (reason.get("source_id"), reason.get("evidence_id"))
                            if item
                        }
                    )

    records: list[dict[str, object]] = []
    for _asset_id, asset in assets.items():
        geometry = asset.pop("_geometry")
        frame_crs = asset.pop("_crs")
        asset["alignment_bases"] = sorted(set(asset["alignment_bases"]))
        asset["asset_kind"] = _primary_alignment_basis(asset["alignment_bases"])
        asset["primary_alignment_basis"] = asset["asset_kind"]
        asset["source_provenance"] = sorted(
            asset["source_provenance"],
            key=lambda item: (
                str(item.get("source_id")),
                str(item.get("evidence_id")),
                str(item.get("source_sha256")),
            ),
        )
        asset["intervention_state"] = _derive_intervention_state(
            str(asset["asset_kind"]),
            asset["source_provenance"],
        )
        asset["evidence_state_reasons"] = sorted(
            asset["evidence_state_reasons"],
            key=lambda item: (str(item.get("source_id")), str(item.get("evidence_id"))),
        )
        asset["governed_source_identities"] = sorted(
            {
                identity
                for evidence in asset["source_provenance"]
                for identity in (evidence.get("evidence_id"), evidence.get("source_id"))
                if identity
            }
        )
        participations = _exact_candidate_participations(_compiled, asset)
        asset["candidate_participations"] = participations
        if participations:
            asset["participation_state"] = "participating"
            asset["disposition"] = "participating"
            asset["non_participation_reason"] = None
        else:
            asset["participation_state"] = "not-participating"
            asset["disposition"] = "not-participating"
            asset["non_participation_reason"] = "no-governed-candidate-binding"
        public_geometry = gpd.GeoSeries([geometry], crs=frame_crs).to_crs(4326).iloc[0]
        asset["geometry"] = mapping(public_geometry)
        records.append(asset)
    records.sort(key=lambda item: str(item["asset_id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": "satn-asset-accounting/v1",
        "disclaimer": DISCLAIMER,
        "asset_count": len(records),
        "records": records,
        "excluded_observations": sorted(
            excluded_observations,
            key=lambda item: (
                str(item.get("source_id")),
                str(item.get("evidence_id")),
                str(item.get("evidence_geometry_fingerprint")),
            ),
        ),
    }


def accounting_geojson(accounting: Mapping[str, object]) -> dict[str, object]:
    """Render asset accounting as a GeoJSON FeatureCollection."""

    features: list[dict[str, object]] = []
    for record in accounting.get("records", []):
        if not isinstance(record, Mapping):
            continue
        # Keep one spatial sibling with properties separated from geometry.
        geometry = record.get("geometry")
        if geometry is None:
            continue
        properties = {key: value for key, value in record.items() if key != "geometry"}
        features.append(
            {
                "type": "Feature",
                "id": record["asset_id"],
                "properties": properties,
                "geometry": geometry,
            }
        )
    return {
        "type": "FeatureCollection",
        "name": "SATN reusable asset accounting",
        "schema_version": accounting.get("schema_version", SCHEMA_VERSION),
        "contract": accounting.get("contract", "satn-asset-accounting/v1"),
        "disclaimer": accounting.get("disclaimer", DISCLAIMER),
        "features": features,
    }


def accounting_geojson_from_records(accounting: Mapping[str, object]) -> dict[str, object]:
    """Compatibility helper retained for callers that supply source geometry."""
    return accounting_geojson(accounting)
