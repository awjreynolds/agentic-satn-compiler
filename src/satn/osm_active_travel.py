"""Shared classification for way-level OSM active-travel evidence."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress

from satn.tags import canonical_tag_values

OSM_ACTIVE_TRAVEL_WAY_TAGS = (
    "bicycle",
    "foot",
    "cycleway",
    "cycleway:left",
    "cycleway:right",
    "cycleway:both",
    "surface",
    "segregated",
    "designation",
    "bicycle_road",
    "cyclestreet",
    "route",
    "network",
    "lcn",
    "rcn",
    "ncn",
    "icn",
    "lit",
    "incline",
    "tracktype",
    "railway",
    "prow_class",
    "right_of_way",
    "shared_use",
)
_CYCLEWAY_BLOCKED_VALUES = frozenset({"", "no", "none", "nan", "<na>", "separate"})
_BICYCLE_ACCESS_VALUES = frozenset({"yes", "designated", "permissive"})
_BICYCLE_PRIORITY_KEYS = frozenset({"bicycle_road", "cyclestreet"})
_BICYCLE_ROUTE_KEYS = frozenset({"route", "lcn", "rcn", "ncn", "icn"})
_ROUTABLE_HIGHWAYS = {
    "cycleway",
    "path",
    "track",
    "footway",
    "pedestrian",
    "bridleway",
    "steps",
}
_ROAD_HIGHWAYS = {
    "living_street",
    "residential",
    "unclassified",
    "service",
    "tertiary",
    "secondary",
    "primary",
    "trunk",
    "motorway",
    "road",
}
_PROW_DESIGNATIONS = {
    "public_footpath": "public-footpath",
    "public_bridleway": "public-bridleway",
    "bridleway": "public-bridleway",
    "restricted_byway": "restricted-byway",
    "byway_open_to_all_traffic": "byway-open-to-all-traffic",
    "byway": "byway-open-to-all-traffic",
}
_AUTHORITY_ROLE_CLAIMS = {
    "custodian": {
        "cycling-access",
        "continuity",
        "current-cycling-provision",
        "physical-connection",
        "route-class",
        "surface-condition",
    },
    "custodian-classification": {
        "cycling-access",
        "continuity",
        "current-cycling-provision",
        "physical-connection",
        "route-class",
        "surface-condition",
    },
    "highway-authority": {
        "cycling-access",
        "continuity",
        "current-cycling-provision",
        "physical-connection",
        "route-class",
        "surface-condition",
    },
    "legal-highway-record": {
        "cycling-access",
        "continuity",
        "current-cycling-provision",
        "physical-connection",
        "route-class",
    },
    "asset-owner-record": {
        "cycling-access",
        "continuity",
        "current-cycling-provision",
        "physical-connection",
        "surface-condition",
    },
    "scheme-delivery-record": {
        "continuity",
        "current-cycling-provision",
        "physical-connection",
        "surface-condition",
    },
    "authoritative-topography": {"topography", "surface-condition"},
}
_CYCLEWAY_STATUS_CLAIMS = {
    "cycling-access",
    "continuity",
    "current-cycling-provision",
    "physical-connection",
    "route-class",
}


def _text(row: Mapping[str, object], key: str) -> str | None:
    values = canonical_tag_values(row.get(key))
    return values[0] if values else None


def _full_sha256(value: object) -> bool:
    values = canonical_tag_values(value)
    if not values or len(values[0]) != 64:
        return False
    with suppress(ValueError):
        int(values[0], 16)
        return True
    return False


def authoritative_lineage(row: Mapping[str, object]) -> bool:
    """Return whether a row carries a complete governed source lineage."""

    source_export = row.get("source_export_sha256") or row.get("raw_bytes_sha256")
    role = (_text(row, "source_authority_role") or "").lower().replace("_", "-")
    claim = (_text(row, "claim_type") or "").lower().replace("_", "-")
    return bool(
        _text(row, "source_family")
        and _text(row, "dataset")
        and _text(row, "publisher")
        and _text(row, "source_authority_role")
        and (_text(row, "effective_date") or _text(row, "publisher_release"))
        and _text(row, "licence")
        and _full_sha256(source_export)
        and claim in _AUTHORITY_ROLE_CLAIMS.get(role, set())
        and _text(row, "evidence_mode")
        and _text(row, "coverage_state")
        and (
            _text(row, "ingestion_contract")
            or _text(row, "parser_contract")
            or _text(row, "parser_version")
        )
    )


def authoritative_cycleway_lineage(row: Mapping[str, object]) -> bool:
    claim = (_text(row, "claim_type") or "").lower().replace("_", "-")
    return authoritative_lineage(row) and claim in _CYCLEWAY_STATUS_CLAIMS


def _row_tag_values(row: Mapping[str, object], keys: set[str]) -> tuple[str, ...]:
    return tuple(value.lower() for key in keys for value in canonical_tag_values(row.get(key)))


def _cycleway_tag_values(row: Mapping[str, object]) -> tuple[str, ...]:
    return _row_tag_values(row, {"cycleway", "cycleway:left", "cycleway:right", "cycleway:both"})


def _has_explicit_cycleway_signal(row: Mapping[str, object]) -> bool:
    return any(value not in _CYCLEWAY_BLOCKED_VALUES for value in _cycleway_tag_values(row))


def _has_road_cycleway_signal(row: Mapping[str, object], highways: set[str]) -> bool:
    return bool(highways & _ROAD_HIGHWAYS) and _has_explicit_cycleway_signal(row)


def _has_bicycle_route_signal(row: Mapping[str, object]) -> bool:
    if "bicycle" in _row_tag_values(row, {"route"}):
        return True
    return any(
        value not in _CYCLEWAY_BLOCKED_VALUES
        for value in _row_tag_values(row, _BICYCLE_ROUTE_KEYS - {"route"})
    )


def _has_bicycle_priority_signal(row: Mapping[str, object]) -> bool:
    return any(
        value in {"yes", "designated", "true", "1"}
        for value in _row_tag_values(row, _BICYCLE_PRIORITY_KEYS)
    )


def _has_bicycle_access(row: Mapping[str, object]) -> bool:
    return bool(set(_row_tag_values(row, {"bicycle", "shared_use"})) & _BICYCLE_ACCESS_VALUES)


def _path_asset_kind(row: Mapping[str, object]) -> str | None:
    bicycle = set(_row_tag_values(row, {"bicycle"}))
    if not bicycle & _BICYCLE_ACCESS_VALUES:
        return None
    foot = set(_row_tag_values(row, {"foot"}))
    shared_use = set(_row_tag_values(row, {"shared_use"}))
    if (bicycle & {"yes", "designated"} and foot & {"yes", "designated"}) or (
        shared_use & {"yes", "designated"}
    ):
        return "shared-use-path"
    return "cycle-access-path"


def _has_proposed_cycleway_signal(row: Mapping[str, object]) -> bool:
    return any(value in {"proposed", "construction"} for value in _cycleway_tag_values(row))


def designation(row: Mapping[str, object]) -> str | None:
    for key in ("designation", "prow_class", "right_of_way", "route_type"):
        value = _text(row, key)
        if value:
            return value.lower().replace(" ", "_")
    return None


def network_kind(row: Mapping[str, object]) -> str | None:
    """Classify one OSM way without promoting it to the strategic network."""

    highways = {value.lower() for value in canonical_tag_values(row.get("highway"))}
    has_cycleway_signal = _has_explicit_cycleway_signal(row)
    has_bicycle_access = _has_bicycle_access(row)
    has_route_signal = _has_bicycle_route_signal(row)
    has_priority_signal = _has_bicycle_priority_signal(row)
    has_road_cycleway_signal = _has_road_cycleway_signal(row, highways)
    has_active_signal = has_cycleway_signal or has_route_signal or has_priority_signal
    if _has_proposed_cycleway_signal(row) or (
        highways & {"proposed", "construction"} and has_active_signal
    ):
        return "proposed-new-corridor"
    if "cycleway" in highways:
        return "cycle-track" if authoritative_cycleway_lineage(row) else "mapped-cycleway"
    if has_road_cycleway_signal:
        return "road-cycleway"
    if has_priority_signal and highways & _ROAD_HIGHWAYS:
        return "bicycle-priority-road"
    if has_route_signal:
        return "bicycle-route"
    if has_cycleway_signal:
        return "mapped-cycleway"
    if highways & {"bridleway"}:
        return "public-bridleway"
    if highways & {"footway", "path", "pedestrian", "steps", "track"} and has_bicycle_access:
        return _path_asset_kind(row)
    refs = tuple(value.upper() for value in canonical_tag_values(row.get("ref")))
    if any(ref.startswith("A") and ref[1:].replace(" ", "").isdigit() for ref in refs):
        return "a-road"
    if any(ref.startswith("B") and ref[1:].replace(" ", "").isdigit() for ref in refs):
        return "b-road"
    railway = {value.lower() for value in canonical_tag_values(row.get("railway"))}
    if railway & {"abandoned", "disused", "historic"}:
        return "former-railway"
    designation_value = designation(row)
    if designation_value in _PROW_DESIGNATIONS:
        return _PROW_DESIGNATIONS[designation_value]
    if highways & {"unclassified", "residential", "living_street"}:
        return "unclassified-road"
    if highways & {"primary", "secondary", "tertiary", "trunk"}:
        return "classified-unnumbered-road"
    if not highways & _ROUTABLE_HIGHWAYS:
        return None
    if highways & {"footway", "path", "pedestrian", "steps", "track"}:
        return None
    return None
