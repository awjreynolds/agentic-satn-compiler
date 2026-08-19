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
from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiLineString, mapping

from satn.constants import DISCLAIMER, SCHEMA_VERSION
from satn.evidence_contracts import evidence_geometry_fingerprint
from satn.osm_active_travel import (
    OSM_ACTIVE_TRAVEL_ASSET_KINDS,
    network_kind,
)
from satn.osm_active_travel import (
    authoritative_cycleway_lineage as _authoritative_cycleway_lineage,
)
from satn.osm_active_travel import (
    authoritative_lineage as _authoritative_lineage,
)
from satn.osm_active_travel import (
    designation as _designation,
)
from satn.tags import (
    canonical_tag_values as _tag_texts,
)
from satn.tags import (
    source_identity,
)
from satn.tags import (
    tag_identity as _tag_identity,
)
from satn.tags import (
    tag_text as _tag_text,
)

_CONTEXT_KINDS = {
    "ncn-route": "current-ncn",
    "ncn-link": "ncn-link",
    "declassified-ncn-route": "reclassified-ncn",
    "greenway-cycleway": "greenway",
    "cycleway": "cycle-track",
    "road-cycleway": "road-cycleway",
    "bicycle-priority-road": "bicycle-priority-road",
    "bicycle-route": "bicycle-route",
    "cycle-access-path": "cycle-access-path",
    "shared-use-path": "shared-use-path",
    "bridleway": "public-bridleway",
    "public-footpath": "public-footpath",
    "public-bridleway": "public-bridleway",
    "restricted-byway": "restricted-byway",
    "byway-open-to-all-traffic": "byway-open-to-all-traffic",
    "prow-class-unknown": "prow-class-unknown",
    "former-railway": "former-railway",
    "local-connector": "local-connector",
    "proposed-new-corridor": "proposed-new-corridor",
    "proposed-cycleway": "proposed-new-corridor",
    "a-road-spine": "a-road",
    "b-road": "b-road",
    "unclassified-road": "unclassified-road",
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
    "name",
    "highway",
    "railway",
    "route_type",
    "bicycle",
    "foot",
    "access",
    "access_default_interpretation",
    "default_access_interpretation",
    "access_default_rule",
    "default_access_rule",
    "designation",
    "prow_class",
    "right_of_way",
    "shared_use",
    "surface",
    "smoothness",
    "segregated",
    "oneway",
    "motor_vehicle",
    "motorcar",
    "horse",
    "lit",
    "width",
    "maxspeed",
    "ref",
    "tags",
    "raw_tags",
    "osm_tags",
    "osmid",
    "osm_id",
    "id",
    "cycleway",
    "bicycle_road",
    "cyclestreet",
    "route",
    "lcn",
    "rcn",
    "ncn",
    "icn",
)

# A complete export lineage is necessary but not sufficient to promote a row
# to an authoritative claim.  The publishing role must be one that is
# governed for the particular claim being asserted.  In particular, a
# community-mapped observation may describe a mapped feature, but it cannot
# establish statutory cycle-track status or a legal/physical connection.

# Classification is deliberately ordered as a governed precedence, rather than
# inheriting the row order of a source export.  ``alignment_bases`` retains all
# defensible classifications while ``asset_kind``/``primary_alignment_basis``
# expose this one stable summary value.
_ASSET_KIND_PRECEDENCE = (
    "current-ncn",
    "ncn-link",
    "reclassified-ncn",
    "greenway",
    "mapped-cycleway",
    "road-cycleway",
    "bicycle-priority-road",
    "bicycle-route",
    "cycle-access-path",
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
_ASSET_KIND_PRECEDENCE_INDEX = {kind: index for index, kind in enumerate(_ASSET_KIND_PRECEDENCE)}


def _json_value(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if hasattr(value, "tolist"):
        with suppress(AttributeError, TypeError, ValueError):
            converted = value.tolist()
            if converted is not value:
                return _json_value(converted)
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
        and set(_tag_texts(item.get("raw_attributes", {}).get("bicycle"))) & {"yes", "designated"}
        for item in provenance
    ):
        return "existing-provision"
    return "upgrade-required"


def _geometry_fingerprint(geometry: object) -> str:
    return hashlib.sha256(geometry.wkb).hexdigest()  # type: ignore[union-attr]


def _canonical_metric_geometry(geometry: object) -> object:
    """Canonicalise BNG line coordinates at evidence-contract millimetres."""
    if isinstance(geometry, LineString):
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError("asset evidence geometry must be nonempty and valid")
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
        if len(deduplicated) < 2:
            raise ValueError("asset evidence line collapses after millimetre canonicalisation")
        ordered = min(deduplicated, list(reversed(deduplicated)))
        return LineString([(x / 1000, y / 1000) for x, y in ordered])
    if isinstance(geometry, MultiLineString):
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError("asset evidence geometry must be nonempty and valid")
        parts = sorted(
            (_canonical_metric_geometry(part) for part in geometry.geoms),
            key=lambda item: item.wkb,
        )
        if not parts:
            raise ValueError("asset evidence MultiLineString has no line members")
        return MultiLineString(parts)
    raise ValueError("asset accounting only supports LineString and MultiLineString geometry")


def _line_geometry(frame: gpd.GeoDataFrame, index: object, row: pd.Series) -> object | None:
    geometry = row.geometry
    if geometry is None or geometry.is_empty or not hasattr(geometry, "wkb"):
        return None
    return geometry


def _context_kind(row: pd.Series) -> str | None:
    feature_type = (_tag_text(row.get("feature_type")) or "").lower().replace("_", "-")
    if feature_type == "cycleway":
        # A cycleway label alone is mapped/provisional evidence.  Reserve the
        # legal-sounding cycle-track basis for a governed authoritative claim.
        return "cycle-track" if _authoritative_cycleway_lineage(row) else "mapped-cycleway"
    if feature_type in _CONTEXT_KINDS:
        return _CONTEXT_KINDS[feature_type]
    if "prow" in feature_type or "right-of-way" in feature_type:
        return "prow-class-unknown"
    return None


def _evidence_row(
    row: pd.Series,
    *,
    kind: str,
    geometry_sha256: str | None,
    evidence_geometry_fingerprint_value: str | None,
) -> dict[str, object]:
    evidence_id = _tag_identity(row.get("evidence_id"))
    source_id = source_identity(
        row,
        ("source_id", "source_feature_id", "osmid", "osm_id", "id"),
    )
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
    # Preserve namespaced OSM tags (for example ``access:conditional``) even
    # when a source adapter has not promoted them to a first-class field.
    raw_attributes.update(
        {
            str(field): _json_value(value)
            for field, value in row.items()
            if ":" in str(field) and field != "geometry" and _json_value(value) is not None
        }
    )
    # Excluded and provisional observations remain reproducible from the raw
    # tag interpretation, not mutable caller lineage fields.  Keep all
    # normalized fields below for inspection, while the observation identity is
    # deliberately tied to the governed classification and raw tag payload.
    source_sha256 = _sha256(
        {
            "kind": kind,
            "geometry_sha256": geometry_sha256,
            "raw_attributes": raw_attributes,
        }
    )
    source_export_sha256 = _text(row.get("source_export_sha256") or row.get("raw_bytes_sha256"))
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
        "parser_contract": _text(row.get("parser_contract")),
        "parser_version": _text(row.get("parser_version")),
        "raw_attributes": raw_attributes,
        "raw_attributes_sha256": _sha256(raw_attributes),
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
        "authority_state": ("authoritative" if _authoritative_lineage(row) else "unknown"),
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
            text for item in value if (text := _text(getattr(item, "value", item))) is not None
        )
    text = _text(getattr(value, "value", value))
    return (text,) if text is not None else ()


def _conflicting_provenance_identities(
    provenance: Iterable[Mapping[str, object]],
) -> set[str]:
    """Detect contradictory rows and return their inspectable identities.

    Distinct sources are allowed to contribute multiple claims to one physical
    asset.  Reusing the same source or evidence identity for materially
    different observations, however, is a provenance conflict and must not be
    allowed to aggregate as ``supported``.
    """

    records = tuple(provenance)
    positive = {"yes", "designated", "permissive", "allowed", "open", "supported"}
    negative = {
        "no",
        "private",
        "forbidden",
        "prohibited",
        "absent",
        "closed",
        "unsupported",
        "not-applicable",
    }

    def claim_family(item: Mapping[str, object]) -> tuple[str, int]:
        raw_claim = (_text(item.get("claim_type")) or _text(item.get("feature_type")) or "").lower()
        claim = raw_claim.replace("_", "-")
        for prefix in ("no-", "not-", "without-", "prohibited-"):
            if claim.startswith(prefix):
                return claim[len(prefix) :], -1
        return claim, 1

    # Reusing an identity for compatible atomic claims is valid.  Only
    # contradictory polarity within the same claim family is a duplicate
    # identity conflict; a surface claim and a cycling-access claim compose.
    grouped: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for item in records:
        family, _polarity = claim_family(item)
        for identity in (item.get("source_id"), item.get("evidence_id")):
            value = _text(identity)
            if value:
                grouped.setdefault((value, family), []).append(item)

    identities: set[str] = set()
    for (identity, _family), items in grouped.items():
        claim_polarities = {claim_family(item)[1] for item in items}
        if len(claim_polarities) > 1:
            identities.add(identity)
            continue
        for field in ("bicycle", "foot", "access", "shared_use"):
            values = {
                value
                for item in items
                if (value := _text(item.get("raw_attributes", {}).get(field))) is not None
            }
            normalized = {value.lower().replace("_", "-") for value in values}
            if normalized & positive and normalized & negative:
                identities.add(identity)
                break
    if identities:
        return identities

    # Distinct source identities can still make opposite raw claims about the
    # same canonical asset.  Keep that check claim-family scoped as well, so
    # independent atomic claims remain composable.
    polarity_fields = (
        "bicycle",
        "foot",
        "access",
        "shared_use",
    )
    for family in {claim_family(item)[0] for item in records}:
        family_records = [item for item in records if claim_family(item)[0] == family]
        for field in polarity_fields:
            values = {
                value.lower().replace("_", "-")
                for item in family_records
                if (value := _text(item.get("raw_attributes", {}).get(field))) is not None
            }
            if values & positive and values & negative:
                return {
                    identity
                    for item in family_records
                    for identity in (
                        _text(item.get("source_id")),
                        _text(item.get("evidence_id")),
                    )
                    if identity
                }
    return set()


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
    compiled: object,
    candidate_set: object,
    candidate: object,
    record: object,
) -> tuple[str, dict[str, object]]:
    """Map preparation facts to the bounded accounting disposition vocabulary."""
    topology = _field(candidate, "topology_state")
    topology_state = _text(getattr(topology, "value", topology))
    if topology_state == "unsatisfied":
        return "topology-unconnected", {
            "code": "candidate-topology-unsatisfied",
            "topology_state": topology_state,
        }
    candidate_id = _text(_field(candidate, "candidate_id"))
    admissions = _field(candidate_set, "admissions", ())
    admission = next(
        (item for item in admissions or () if _text(_field(item, "candidate_id")) == candidate_id),
        None,
    )
    admission_value = _field(admission, "disposition")
    admission_disposition = _text(getattr(admission_value, "value", admission_value))
    preparation_disposition = _text(_field(record, "preparation_disposition")) or ""
    if preparation_disposition == "officer-excluded":
        return "officer-excluded", {
            "code": "governed-officer-exclusion",
            "preparation_disposition": preparation_disposition,
        }
    if preparation_disposition in {
        "rejected-topology-unknown-review-required",
        "incomplete-evidence",
    }:
        return "incomplete-evidence", {
            "code": "candidate-evidence-incomplete",
            "preparation_disposition": preparation_disposition,
        }
    if (
        admission_disposition != "admitted"
        or preparation_disposition
        in {
            "rejected",
            "excluded",
        }
        or preparation_disposition.startswith("rejected-")
    ):
        rationale = _json_value(_field(admission, "rationale"))
        return "ineligible", {
            "code": "candidate-failed-admission-or-preparation",
            "admission_disposition": admission_disposition or None,
            "failed_rule": rationale or preparation_disposition or None,
            "preparation_disposition": preparation_disposition or None,
        }

    reviewable = _field(compiled, "reviewable_network")
    for selection in _field(reviewable, "effective_selections", ()) or ():
        if (
            _text(_field(selection, "candidate_set_id"))
            == _text(_field(candidate_set, "candidate_set_id"))
            and _text(_field(selection, "candidate_id")) == candidate_id
        ):
            reason: dict[str, object] = {
                "code": "effective-reviewable-selection",
                "reviewable_result_fingerprint": _text(_field(reviewable, "result_fingerprint")),
            }
            officer_decision_id = _text(_field(selection, "officer_decision_id"))
            if officer_decision_id:
                reason["officer_decision_id"] = officer_decision_id
            return "selected", reason

    scenario = _field(reviewable, "scenario")
    complementary_ids = {
        _text(value) for value in (_field(scenario, "complementary_candidate_ids", ()) or ())
    }
    if candidate_id in complementary_ids:
        return "complementary", {
            "code": "scenario-complementary-selection",
            "scenario_fingerprint": _text(_field(scenario, "scenario_fingerprint")),
        }

    comparison_reason: object = None
    for selection in _field(scenario, "selections", ()) or ():
        if _text(_field(selection, "candidate_set_id")) != _text(
            _field(candidate_set, "candidate_set_id")
        ):
            continue
        comparison = next(
            (
                item
                for item in (_field(selection, "comparison_dispositions", ()) or ())
                if _text(_field(item, "candidate_id")) == candidate_id
            ),
            None,
        )
        if comparison is not None:
            model_dump = getattr(comparison, "model_dump", None)
            comparison_reason = (
                model_dump(mode="json") if callable(model_dump) else _json_value(comparison)
            )
        break
    return "eligible-not-selected", {
        "code": "eligible-candidate-not-selected",
        "comparison_disposition": comparison_reason,
        "reviewable_result_fingerprint": _text(_field(reviewable, "result_fingerprint")) or None,
    }


_GEOMETRY_BINDING_TOLERANCE_M = 0.001


def _candidate_geometry_shape(candidate: object, record: object) -> object | None:
    geometry = _field(candidate, "geometry")
    if geometry is None:
        geometry = _field(record, "geometry")
    as_shapely = getattr(geometry, "as_shapely", None)
    if callable(as_shapely):
        geometry = as_shapely()
    return geometry


def _geometry_match_distance(candidate_shape: object, asset_shape: object) -> float | None:
    try:
        distance = float(candidate_shape.hausdorff_distance(asset_shape))
        length_delta = abs(float(candidate_shape.length) - float(asset_shape.length))
    except (AttributeError, GEOSException, TypeError, ValueError):
        return None
    if length_delta > _GEOMETRY_BINDING_TOLERANCE_M:
        return None
    return distance


def _resolve_candidate_geometry_bindings(
    compiled: object,
    assets: Mapping[str, Mapping[str, object]],
    identity_counts: Mapping[str, int],
) -> dict[tuple[str, str], frozenset[str]]:
    """Resolve each candidate to at most one asset when identities are reused.

    A nonunique source ID is only a search key.  Geometry disambiguation is
    performed over the complete asset roster so one candidate cannot attach to
    every nearby asset independently.
    """
    bindings: dict[tuple[str, str], frozenset[str]] = {}
    for candidate_set, record in _candidate_records(compiled):
        candidate = _field(record, "candidate")
        candidate_set_id = _text(_field(candidate_set, "candidate_set_id"))
        candidate_id = _text(_field(candidate, "candidate_id"))
        if candidate is None or not candidate_set_id or not candidate_id:
            continue
        key = (candidate_set_id, candidate_id)
        explicit_identities = set(
            _values(_field(candidate, "governed_evidence_ids"))
            + _values(_field(candidate, "provenance_ids"))
            + _values(_field(record, "source_ids"))
            + _values(_field(record, "evidence_ids"))
        )
        matching: list[tuple[str, Mapping[str, object], list[str]]] = []
        direct_assets: set[str] = set()
        for asset_id, asset in assets.items():
            asset_identities = {
                str(value)
                for evidence in asset.get("source_provenance", ())
                for value in (
                    evidence.get("evidence_id"),
                    evidence.get("source_id"),
                )
                if value
            }
            matched = sorted(asset_identities & explicit_identities)
            if not matched:
                continue
            matching.append((asset_id, asset, matched))
            if any(identity_counts.get(identity, 1) == 1 for identity in matched):
                direct_assets.add(asset_id)
        if direct_assets:
            bindings[key] = frozenset(direct_assets)
            continue
        if not matching:
            continue
        candidate_shape = _candidate_geometry_shape(candidate, record)
        if candidate_shape is None:
            bindings[key] = frozenset()
            continue
        exact_assets: list[str] = []
        distances: list[tuple[float, str]] = []
        for asset_id, asset, _matched in matching:
            asset_shape = asset.get("_geometry")
            if asset_shape is None:
                continue
            try:
                if candidate_shape.equals(asset_shape):
                    exact_assets.append(asset_id)
                    continue
            except (AttributeError, GEOSException, TypeError, ValueError):
                pass
            distance = _geometry_match_distance(candidate_shape, asset_shape)
            if distance is not None and distance <= _GEOMETRY_BINDING_TOLERANCE_M:
                distances.append((distance, asset_id))
        if len(exact_assets) == 1:
            bindings[key] = frozenset(exact_assets)
            continue
        if len(exact_assets) > 1:
            bindings[key] = frozenset()
            continue
        distances.sort()
        if not distances:
            bindings[key] = frozenset()
            continue
        if len(distances) == 1 or (
            distances[1][0] - distances[0][0] > _GEOMETRY_BINDING_TOLERANCE_M
        ):
            bindings[key] = frozenset((distances[0][1],))
        else:
            bindings[key] = frozenset()
    return bindings


def _exact_candidate_participations(
    compiled: object,
    asset: Mapping[str, object],
    identity_counts: Mapping[str, int] | None = None,
    resolved_bindings: Mapping[tuple[str, str], frozenset[str]] | None = None,
) -> list[dict[str, object]]:
    """Return only candidate records carrying an explicit source/evidence binding."""
    asset_identities = {
        str(value) for value in asset.get("governed_source_identities", ()) if value
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
        binding_key = (candidate_set_id, candidate_id)
        if (
            resolved_bindings is not None
            and binding_key in resolved_bindings
            and str(asset.get("asset_id")) not in resolved_bindings[binding_key]
        ):
            continue
        unique_identities = [
            identity
            for identity in matched_identities
            if (identity_counts or {}).get(identity, 1) == 1
        ]
        if not unique_identities:
            # A source identifier is not a feature identity when it is reused
            # for several distant assets.  Permit a binding only when the
            # candidate carries an exact governed geometry fingerprint; the
            # conservative default is unbound/ambiguous.
            candidate_geometry = _text(_field(candidate, "geometry_fingerprint")) or _text(
                _field(record, "geometry_fingerprint")
            )
            asset_geometry = _text(asset.get("evidence_geometry_fingerprint"))
            geometry_proof = bool(candidate_geometry and candidate_geometry == asset_geometry)
            if not geometry_proof:
                candidate_shape = _field(candidate, "geometry")
                candidate_shape = (
                    candidate_shape.as_shapely()
                    if callable(getattr(candidate_shape, "as_shapely", None))
                    else candidate_shape
                )
                asset_shape = asset.get("_geometry")
                try:
                    geometry_proof = bool(
                        candidate_shape is not None
                        and asset_shape is not None
                        and candidate_shape.hausdorff_distance(asset_shape) <= 0.001
                        and abs(candidate_shape.length - asset_shape.length) <= 0.001
                    )
                except (AttributeError, GEOSException, TypeError, ValueError):
                    geometry_proof = False
            if not geometry_proof:
                continue
        role = _field(candidate_set, "network_role")
        role_value = _text(getattr(role, "value", role))
        disposition, selection_reason = _candidate_participation_disposition(
            compiled,
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
                "selection_reason": selection_reason,
                "binding_basis": "explicit-governed-source-identity",
                "binding_identities": unique_identities or matched_identities,
                "preparation_disposition": _text(_field(record, "preparation_disposition")),
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
            kind = _context_kind(row) if origin == "context" else network_kind(row)
            if origin == "network" and kind not in OSM_ACTIVE_TRAVEL_ASSET_KINDS:
                kind = None
            geometry = _line_geometry(frame, index, row)
            if geometry is None:
                continue
            try:
                if frame.crs is None:
                    raise ValueError("asset evidence geometry has no CRS")
                identity_geometry = gpd.GeoSeries([geometry], crs=frame.crs).to_crs(27700).iloc[0]
            except (GEOSException, ValueError, TypeError):
                excluded = _evidence_row(
                    row,
                    kind="invalid-crs-geometry",
                    # Do not derive an identity from raw geometry when the
                    # governed CRS/canonicalisation contract has failed.
                    geometry_sha256=None,
                    evidence_geometry_fingerprint_value=None,
                )
                excluded.update(
                    {
                        "accounting_disposition": "excluded-invalid-crs",
                        "observation_state": "unknown",
                        "authority_state": "unknown",
                        "reason": (
                            "source geometry CRS is missing or cannot be transformed to EPSG:27700"
                        ),
                    }
                )
                excluded_observations.append(excluded)
                continue
            try:
                identity_geometry = _canonical_metric_geometry(identity_geometry)
                geometry_sha256 = _geometry_fingerprint(identity_geometry)
                evidence_geometry_identity = evidence_geometry_fingerprint(
                    identity_geometry,
                    27700,
                )
            except (GEOSException, TypeError, ValueError):
                excluded = _evidence_row(
                    row,
                    kind="invalid-canonical-geometry",
                    geometry_sha256=None,
                    evidence_geometry_fingerprint_value=None,
                )
                excluded.update(
                    {
                        "accounting_disposition": "excluded-invalid-geometry",
                        "observation_state": "unknown",
                        "authority_state": "unknown",
                        "reason": (
                            "source geometry cannot be represented by the governed "
                            "canonical evidence-geometry contract"
                        ),
                    }
                )
                excluded_observations.append(excluded)
                continue
            if kind is None:
                highway = (_text(row.get("highway")) or "").lower()
                designation = _designation(row)
                bare_reusable_tag = origin == "network" and (
                    highway in {"cycleway", "footway", "path", "track"}
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

    identity_counts: dict[str, int] = {}
    for asset in assets.values():
        asset_identities: set[str] = set()
        for evidence in asset.get("source_provenance", ()):
            for identity in (evidence.get("evidence_id"), evidence.get("source_id")):
                value = _text(identity)
                if value:
                    asset_identities.add(value)
        for identity in asset_identities:
            identity_counts[identity] = identity_counts.get(identity, 0) + 1
    resolved_bindings = _resolve_candidate_geometry_bindings(
        _compiled,
        assets,
        identity_counts,
    )

    records: list[dict[str, object]] = []
    for _asset_id, asset in assets.items():
        geometry = asset["_geometry"]
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
        conflicting_provenance_ids = _conflicting_provenance_identities(asset["source_provenance"])
        if conflicting_provenance_ids:
            # A duplicated source/evidence identity with divergent content is
            # itself a material contradiction.  Preserve every row, but make
            # the aggregate fail closed even when callers marked both rows
            # ``supported``.
            asset["evidence_state"] = "conflicting"
            asset["evidence_state_reasons"].append(
                {
                    "source_id": None,
                    "evidence_id": None,
                    "state": "conflicting",
                    "reason": "conflicting-provenance-claims",
                }
            )
            asset["_conflicting_provenance_ids"] = sorted(conflicting_provenance_ids)
        asset["intervention_state"] = _derive_intervention_state(
            str(asset["asset_kind"]),
            asset["source_provenance"],
        )
        asset["evidence_state_reasons"] = sorted(
            asset["evidence_state_reasons"],
            key=lambda item: (str(item.get("source_id")), str(item.get("evidence_id"))),
        )
        if asset["evidence_state"] == "conflicting":
            conflict_roster = set(asset.pop("_conflicting_provenance_ids", []))
            conflict_roster.update(
                identity
                for evidence in asset["source_provenance"]
                if evidence.get("observation_state") == "conflicting"
                for identity in (evidence.get("source_id"), evidence.get("evidence_id"))
                if identity
            )
            asset["conflict_roster"] = sorted(conflict_roster)
        asset["governed_source_identities"] = sorted(
            {
                identity
                for evidence in asset["source_provenance"]
                for identity in (evidence.get("evidence_id"), evidence.get("source_id"))
                if identity
            }
        )
        participations = _exact_candidate_participations(
            _compiled,
            asset,
            identity_counts,
            resolved_bindings,
        )
        asset["candidate_participations"] = participations
        if participations:
            asset["participation_state"] = "participating"
            asset["disposition"] = "participating"
            asset["non_participation_reason"] = None
        else:
            asset["participation_state"] = "not-participating"
            asset["disposition"] = "not-participating"
            asset["non_participation_reason"] = "no-governed-candidate-binding"
        asset.pop("_geometry", None)
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
