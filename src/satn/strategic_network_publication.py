"""Pure projection of the effective strategic network into review-map GeoJSON.

The selector owns authority.  This module only projects that immutable result and
keeps contextual layers optional.  In particular, a historic Backbone is never
reintroduced as a map feature: only ``effective_network`` sections are strategic
authority.  All coordinates emitted by this adapter are WGS84 GeoJSON.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform as transform_geometry
from shapely.ops import unary_union
from shapely.wkt import loads as load_wkt

from satn.constants import DISCLAIMER


class StrategicPublicationLayer(StrEnum):
    STRATEGIC_MAIN_NETWORK = "Strategic Main Network"
    # Keep the symbolic name for callers that used the pre-separation API;
    # its value now identifies the authoritative main-network projection.
    STRATEGIC_NETWORK = "Strategic Main Network"
    ACCESS_SUPPORT = "Access Support"
    PLACES = "Places"
    CANDIDATES = "Candidates discarded"
    ASSETS = "Existing Assets"
    UPGRADEABLE_ASSETS = "Upgradeable Assets"
    DFT_TRAFFIC = "DfT Traffic"
    DIAGNOSTICS = "Graph Diagnostics"
    DIVERGENCE = "Officer Divergence"


DEFAULT_LAYERS = (
    StrategicPublicationLayer.STRATEGIC_MAIN_NETWORK.value,
    StrategicPublicationLayer.PLACES.value,
)
OPTIONAL_LAYERS = (
    StrategicPublicationLayer.ACCESS_SUPPORT.value,
    StrategicPublicationLayer.CANDIDATES.value,
    StrategicPublicationLayer.ASSETS.value,
    StrategicPublicationLayer.UPGRADEABLE_ASSETS.value,
    StrategicPublicationLayer.DFT_TRAFFIC.value,
    StrategicPublicationLayer.DIAGNOSTICS.value,
    StrategicPublicationLayer.DIVERGENCE.value,
)

_STRATEGIC_MAIN_NETWORK_ROLES = frozenset(
    {
        "interurban-spine",
        "strategic-main-connector",
        "urban-main-road-spine",
    }
)
_ACCESS_SUPPORT_ROLES = frozenset(
    {
        "cross-spine-connector",
        "community-access",
        "school-access",
        "strategic-destination-access",
    }
)

_PUBLICATION_FINDING_KIND = "selected-main-physical-discontinuity"
_PUBLICATION_CONTINUITY_REASON = (
    "Selected Main component is physically separate; representative location only; "
    "no direct connection proposed"
)
_PUBLICATION_CONTINUITY_METHOD = (
    "EPSG:27700 endpoint-to-line contacts within canonical geometry tolerance and "
    "linear overlaps connect; bare interior crossings are reported, not joined"
)


@dataclass(frozen=True)
class StrategicPublicationFinding:
    """A publication-only finding derived from retained selected geometry."""

    gap_id: str
    obligation_id: str
    network_role: str
    reason: str
    representative_coordinates: tuple[float, float]
    component_section_ids: tuple[str, ...]
    component_obligation_ids: tuple[str, ...]
    canonical_geometry_tolerance_m: float
    publication_finding_kind: str = _PUBLICATION_FINDING_KIND
    candidate_set_id: str | None = None
    endpoints: tuple[str, ...] = ()
    mesh_proof_points: tuple[tuple[float, float], ...] = ()


def publication_finding_payload(finding: StrategicPublicationFinding) -> dict[str, object]:
    """Return the stable sidecar/map record for one publication finding."""

    return {
        "gap_id": finding.gap_id,
        "obligation_id": finding.obligation_id,
        "network_role": finding.network_role,
        "endpoints": list(finding.endpoints),
        "reason": finding.reason,
        "candidate_set_id": finding.candidate_set_id,
        "mesh_proof_points": [list(point) for point in finding.mesh_proof_points],
        "representative_point": list(finding.representative_coordinates),
        "representative_point_crs": "EPSG:27700",
        "publication_finding_kind": finding.publication_finding_kind,
        "component_section_ids": list(finding.component_section_ids),
        "component_obligation_ids": list(finding.component_obligation_ids),
        "canonical_geometry_tolerance_m": finding.canonical_geometry_tolerance_m,
        "continuity_method": _PUBLICATION_CONTINUITY_METHOD,
    }


def _canonical_a_road_component_gap(gap: object) -> bool:
    """Return whether a retained gap already represents an official A component."""

    return str(getattr(gap, "obligation_id", "") or "").startswith(
        "a-road-backbone-component-gap-"
    ) or any(
        str(endpoint_id or "").startswith("a-road-backbone-component-endpoint-")
        for endpoint_id in tuple(getattr(gap, "endpoints", ()))
    )


def _geometry_tolerance_m(result: object) -> float | None:
    """Read one canonical geometry tolerance from retained candidate-set profiles."""

    tolerances: set[float] = set()
    for candidate_set in tuple(getattr(result, "candidate_sets", ())):
        profile = getattr(candidate_set, "geometry_equivalence_profile", None)
        tolerance = getattr(profile, "tolerance_m", None)
        if tolerance is None:
            continue
        value = float(tolerance)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("publication geometry tolerance must be finite and positive")
        tolerances.add(value)
    if not tolerances:
        return None
    if len(tolerances) != 1:
        raise ValueError("publication requires one canonical geometry tolerance")
    return tolerances.pop()


def _selected_main_physical_components(
    result: object, tolerance_m: float
) -> tuple[tuple[tuple[str, ...], tuple[str, ...], object], ...]:
    """Partition selected Main sections using the existing physical-contact proof."""

    effective = getattr(result, "effective_network", None)
    records: list[tuple[str, str, object]] = []
    for section in sorted(
        tuple(getattr(effective, "sections", ())),
        key=lambda item: str(item.section_id),
    ):
        if _publication_layer_for_role(getattr(section, "network_role", None)) != (
            StrategicPublicationLayer.STRATEGIC_MAIN_NETWORK.value
        ):
            continue
        geometry = load_wkt(str(getattr(section, "geometry_wkt", "")))
        if geometry.geom_type != "LineString" or geometry.is_empty or len(geometry.coords) < 2:
            raise ValueError("selected Main publication geometry must be a non-empty LineString")
        records.append(
            (
                str(getattr(section, "section_id", "")),
                str(getattr(section, "obligation_id", "")),
                geometry,
            )
        )
    if not records:
        return ()

    lines = [record[2] for record in records]
    tree = STRtree(lines)
    parent = list(range(len(lines)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = root(left), root(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for index, line in enumerate(lines):
        for coordinate in (line.coords[0], line.coords[-1]):
            endpoint = Point(coordinate)
            for candidate in tree.query(endpoint.buffer(tolerance_m)):
                other = int(candidate)
                if other != index and endpoint.distance(lines[other]) <= tolerance_m:
                    union(index, other)
        for candidate in tree.query(line, predicate="intersects"):
            other = int(candidate)
            if other <= index:
                continue
            intersection = line.intersection(lines[other])
            if intersection.length > tolerance_m:
                union(index, other)
            # Bare interior crossings intentionally do not union components.

    groups: dict[int, list[int]] = {}
    for index in range(len(lines)):
        groups.setdefault(root(index), []).append(index)
    components = []
    for members in groups.values():
        section_ids = tuple(sorted(records[index][0] for index in members))
        obligation_ids = tuple(sorted({records[index][1] for index in members}))
        linework = unary_union([lines[index] for index in members])
        components.append(
            (
                section_ids,
                obligation_ids,
                linework,
                len(members),
                sum(lines[index].length for index in members),
            )
        )
    components.sort(key=lambda item: (-item[3], -item[4], item[0]))
    return tuple((item[0], item[1], item[2]) for item in components)


def publication_continuity_findings(result: object) -> tuple[StrategicPublicationFinding, ...]:
    """Find selected Main components that remain physically unrepresented."""

    tolerance_m = _geometry_tolerance_m(result)
    if tolerance_m is None:
        return ()
    components = _selected_main_physical_components(result, tolerance_m)
    if len(components) <= 1:
        return ()

    represented_components: set[int] = set()
    for gap in tuple(getattr(result, "gaps", ())):
        if not _canonical_a_road_component_gap(gap):
            continue
        for coordinates in tuple(getattr(gap, "endpoint_coordinates", ())):
            point = Point(float(coordinates[0]), float(coordinates[1]))
            represented_components.update(
                index
                for index, (_section_ids, _obligation_ids, geometry) in enumerate(components)
                if geometry.distance(point) <= tolerance_m
            )

    findings: list[StrategicPublicationFinding] = []
    # The largest component is the retained Main component.  Only smaller,
    # unrepresented components receive a publication marker.
    for index, (section_ids, obligation_ids, geometry) in enumerate(components[1:], start=1):
        if index in represented_components:
            continue
        identity = _fingerprint(
            {
                "kind": _PUBLICATION_FINDING_KIND,
                "component_section_ids": section_ids,
                "component_obligation_ids": obligation_ids,
            }
        )[:24]
        representative = geometry.representative_point()
        findings.append(
            StrategicPublicationFinding(
                gap_id=f"publication-gap-selected-main-component-{identity}",
                obligation_id=f"publication-selected-main-component-{identity}",
                network_role="strategic-main-network",
                reason=_PUBLICATION_CONTINUITY_REASON,
                representative_coordinates=(float(representative.x), float(representative.y)),
                component_section_ids=section_ids,
                component_obligation_ids=obligation_ids,
                canonical_geometry_tolerance_m=tolerance_m,
            )
        )
    return tuple(findings)


def _publication_layer_for_role(role: object) -> str:
    """Return the closed publication layer for a stored network role.

    Effective sections are expected to carry one of the six governed roles.
    Unknown roles stay in the primary layer so a malformed or newly introduced
    result cannot silently disappear from the complete review roster.
    """

    role_value = _text(role)
    if role_value in _ACCESS_SUPPORT_ROLES:
        return StrategicPublicationLayer.ACCESS_SUPPORT.value
    if role_value in _STRATEGIC_MAIN_NETWORK_ROLES:
        return StrategicPublicationLayer.STRATEGIC_MAIN_NETWORK.value
    return StrategicPublicationLayer.STRATEGIC_MAIN_NETWORK.value


# The colours are deliberately semantic rather than route scores.  ``core`` is
# the route/state colour, ``halo`` exposes the alignment basis, and ``pattern``
# remains legible in monochrome exports.
_INTERVENTION_STYLES: dict[str, dict[str, str]] = {
    "existing-provision": {
        "label": "Existing provision",
        "text": "Existing provision",
        "core": "#197a5b",
        "halo": "#a8e6cf",
        "pattern": "solid",
    },
    "upgrade-required": {
        "label": "Upgrade required",
        "text": "Upgrade required",
        "core": "#c77d00",
        "halo": "#ffe29a",
        "pattern": "dash",
    },
    "proposed-new-link": {
        "label": "Proposed new link",
        "text": "Proposed new link",
        "core": "#c9473b",
        "halo": "#ffb4a8",
        "pattern": "solid",
    },
    "undetermined": {
        "label": "Undetermined",
        "text": "Undetermined",
        "core": "#606a73",
        "halo": "#c9d0d6",
        "pattern": "dash-dot",
    },
    "unresolved-gap": {
        "label": "Unresolved gap",
        "text": "Unresolved gap",
        "core": "#606a73",
        "halo": "#c9d0d6",
        "pattern": "dot",
    },
    "reference-route": {
        "label": "Governed reference (provisional)",
        "text": "Governed reference (provisional)",
        "core": "#6d3bb8",
        "halo": "#ceb7ed",
        "pattern": "long-dash",
    },
    "officer-divergence": {
        "label": "Officer/compiler divergence",
        "text": "Officer/compiler divergence",
        "core": "#006d9c",
        "halo": "#8bd3ea",
        "pattern": "cross-hatch",
    },
}

_BASIS_HALO: dict[str, tuple[str, str]] = {
    "cycleway": ("cycleway", "#2a9d8f"),
    "cycle-track": ("cycleway", "#2a9d8f"),
    "ncn": ("national cycle network", "#3a86ff"),
    "prow": ("public right of way", "#8338ec"),
    "footway": ("public right of way", "#8338ec"),
    "a-road": ("A-road", "#f3722c"),
    "primary": ("A-road", "#f3722c"),
    "b-road": ("B-road", "#f8961e"),
    "secondary": ("B-road", "#f8961e"),
    "unclassified": ("unclassified road", "#90be6d"),
    "residential": ("local road", "#90be6d"),
}


def _json_value(value: object) -> object:
    """Convert common scalar/geometry values into strict JSON values."""

    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_json_value(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_value(value.item())
        except (AttributeError, ValueError, TypeError):
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _canonical(value: object) -> object:
    return _json_value(value)


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _geometry_json(geometry: object, source_crs: str) -> dict[str, object] | None:
    """Convert WKT, Shapely or GeoJSON geometry to WGS84 GeoJSON."""

    if geometry is None:
        return None
    if isinstance(geometry, str):
        try:
            geometry = load_wkt(geometry)
        except (TypeError, ValueError) as error:
            raise ValueError("publication geometry is not valid WKT") from error
    if hasattr(geometry, "as_shapely"):
        geometry = geometry.as_shapely()
    elif isinstance(geometry, Mapping):
        geometry = shape(geometry)
    if not hasattr(geometry, "geom_type"):
        raise ValueError("publication geometry must be WKT, GeoJSON or Shapely geometry")
    if source_crs.upper().replace(" ", "") not in {"EPSG:4326", "CRS84"}:
        transformer = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
        geometry = transform_geometry(transformer.transform, geometry)
    return _json_value(mapping(geometry))  # type: ignore[return-value]


def _line_geometry(value: object, source_crs: str) -> dict[str, object] | None:
    geometry = _geometry_json(value, source_crs)
    if geometry is None:
        return None
    if geometry.get("type") not in {"LineString", "MultiLineString"}:
        raise ValueError("strategic section geometry must be a line")
    return geometry


def _text(value: object, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(getattr(value, "value", value))


def _basis_style(bases: Iterable[object], primary: object | None) -> tuple[str, str, str]:
    values = tuple(_text(item).lower().replace("_", "-") for item in bases if _text(item))
    if primary is not None and _text(primary):
        values = (_text(primary).lower().replace("_", "-"), *values)
    for value in values:
        for key, (label, colour) in _BASIS_HALO.items():
            if key in value:
                return label, colour, value
    return (values[0] if values else "unknown alignment basis", "#9aa0a6", "unknown")


def _style_for(
    *, intervention: object | None, display: object | None, authority: object
) -> dict[str, str]:
    display_value = _text(display)
    intervention_value = _text(intervention)
    if display_value == "reference-route":
        return dict(_INTERVENTION_STYLES["reference-route"])
    if display_value == "unresolved-gap":
        return dict(_INTERVENTION_STYLES["unresolved-gap"])
    if _text(authority) == "governed-reference-provisional":
        return dict(_INTERVENTION_STYLES["reference-route"])
    return dict(_INTERVENTION_STYLES.get(intervention_value, _INTERVENTION_STYLES["undetermined"]))


def _feature(
    *,
    feature_id: str,
    geometry: dict[str, object] | None,
    properties: Mapping[str, object],
) -> dict[str, object]:
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": geometry,
        "properties": _json_value(dict(properties)),
    }


def _empty_collection() -> dict[str, object]:
    return {"type": "FeatureCollection", "features": []}


def _collection_features(
    value: object, *, source_crs: str, layer: str, fingerprint: str
) -> list[dict[str, object]]:
    """Accept a GeoJSON collection, GeoDataFrame, or iterable of feature-like rows."""

    if value is None:
        return []
    if isinstance(value, Mapping) and value.get("type") == "FeatureCollection":
        rows = value.get("features", ())
    elif isinstance(value, Mapping) and value.get("type") == "Feature":
        rows = (value,)
    elif hasattr(value, "iterrows"):
        rows = (
            {
                "type": "Feature",
                "properties": {str(k): _json_value(v) for k, v in row.items() if k != "geometry"},
                "geometry": row.get("geometry"),
            }
            for _, row in value.iterrows()
        )
        source_crs = str(getattr(value, "crs", None) or source_crs)
    else:
        rows = value if isinstance(value, Iterable) and not isinstance(value, (str, bytes)) else ()
    features: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        if isinstance(row, Mapping) and row.get("type") == "Feature":
            properties = dict(row.get("properties") or {})
            geometry = row.get("geometry")
            feature_id = str(
                row.get("id") or properties.get("id") or properties.get("asset_id") or index
            )
        elif isinstance(row, Mapping):
            properties = dict(row)
            geometry = properties.pop("geometry", None)
            feature_id = str(properties.get("id") or properties.get("asset_id") or index)
        else:
            properties = {
                key: getattr(row, key)
                for key in dir(row)
                if not key.startswith("_")
                and isinstance(getattr(row, key), (str, int, float, bool))
            }
            geometry = getattr(row, "geometry", None)
            feature_id = str(getattr(row, "id", index))
        projected = _geometry_json(geometry, source_crs) if geometry is not None else None
        feature_prefix = {
            StrategicPublicationLayer.ASSETS.value: "asset-existing-provision",
            StrategicPublicationLayer.UPGRADEABLE_ASSETS.value: "asset-upgrade-required",
            StrategicPublicationLayer.DFT_TRAFFIC.value: "dft-traffic",
        }.get(layer, layer.lower().replace(" ", "-"))
        contextual_properties = {
            **properties,
            "layer": layer,
            **(
                {"feature_type": "asset-existing-provision"}
                if layer == StrategicPublicationLayer.ASSETS.value
                else {"feature_type": "asset-upgrade-required"}
                if layer == StrategicPublicationLayer.UPGRADEABLE_ASSETS.value
                else {"feature_type": "dft-motor-traffic"}
                if layer == StrategicPublicationLayer.DFT_TRAFFIC.value
                else {"feature_type": "graph-diagnostic"}
                if layer == StrategicPublicationLayer.DIAGNOSTICS.value
                else {}
            ),
            "source_fingerprint": fingerprint,
            "strategic_result_fingerprint": fingerprint,
        }
        if layer in {
            StrategicPublicationLayer.ASSETS.value,
            StrategicPublicationLayer.UPGRADEABLE_ASSETS.value,
        }:
            source_geometry_crs = str(contextual_properties.get("geometry_crs") or source_crs)
            contextual_properties["geometry_crs"] = "EPSG:4326"
            contextual_properties["source_geometry_crs"] = source_geometry_crs
            contextual_properties.setdefault("geometry_semantics", "accounted-governed-asset-line")
        features.append(
            _feature(
                feature_id=f"{feature_prefix}:{feature_id}",
                geometry=projected,
                properties=contextual_properties,
            )
        )
    return sorted(features, key=lambda item: str(item["id"]))


def _record_source_crs(record: Mapping[str, object], fallback: str) -> str:
    explicit = record.get("geometry_crs") or record.get("source_geometry_crs")
    if explicit:
        return str(explicit)
    return fallback


def _accounting_features(
    value: object, *, layer: str, fallback_crs: str, fingerprint: str
) -> list[dict[str, object]]:
    """Project asset-accounting records while respecting each record's CRS."""

    if isinstance(value, Mapping) and value.get("type") not in {"Feature", "FeatureCollection"}:
        value = (value,)
    elif not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return _collection_features(
            value, source_crs=fallback_crs, layer=layer, fingerprint=fingerprint
        )
    features: list[dict[str, object]] = []
    for record in value:
        if not isinstance(record, Mapping):
            continue
        source_crs = _record_source_crs(record, fallback_crs)
        features.extend(
            _collection_features(
                (record,), source_crs=source_crs, layer=layer, fingerprint=fingerprint
            )
        )
    return features


def _candidate_properties(candidate: object) -> dict[str, object]:
    """Expose governed candidate evidence without duplicating its geometry."""

    model_dump = getattr(candidate, "model_dump", None)
    if callable(model_dump):
        try:
            raw = model_dump(mode="json", exclude={"geometry", "traffic_observation"})
        except TypeError:
            raw = model_dump()
    elif isinstance(candidate, Mapping):
        raw = {key: value for key, value in candidate.items() if key != "geometry"}
    else:
        raw = {
            key: value
            for key, value in vars(candidate).items()
            if key not in {"geometry", "traffic_observation"}
        }
    return {
        str(key): _json_value(value) for key, value in raw.items() if _json_value(value) is not None
    }


def _candidate_geometry(candidate: object, source_crs: str) -> dict[str, object] | None:
    geometry = (
        candidate.get("geometry")
        if isinstance(candidate, Mapping)
        else getattr(candidate, "geometry", None)
    )
    return _line_geometry(geometry, source_crs) if geometry is not None else None


def gap_endpoint_identity(endpoint_id: object, occurrence: int) -> tuple[str, bool]:
    """Return a stable endpoint key, preserving explicit missing identities."""

    endpoint_key = str(endpoint_id or "")
    if endpoint_key:
        identity_key = (
            endpoint_key if occurrence == 1 else f"{endpoint_key}-occurrence-{occurrence}"
        )
    else:
        identity_key = f"endpoint-missing-{occurrence}"
    return identity_key, identity_key != endpoint_key


def _traffic_features(
    value: object,
    *,
    candidate_roster: Mapping[str, tuple[object, object]],
    source_crs: str,
    fingerprint: str,
) -> list[dict[str, object]]:
    """Project DfT observations, falling back to the bounded candidate line."""

    if value is None:
        return []
    if isinstance(value, Mapping) and value.get("type") == "FeatureCollection":
        rows = value.get("features", ())
    elif isinstance(value, Mapping) and value.get("type") == "Feature":
        rows = (value,)
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        rows = value
    else:
        rows = (value,)
    features: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        if isinstance(row, Mapping) and row.get("type") == "Feature":
            properties = dict(row.get("properties") or {})
            geometry = row.get("geometry")
        elif isinstance(row, Mapping):
            properties = dict(row)
            geometry = properties.pop("geometry", None)
        else:
            properties = _candidate_properties(row)
            geometry = getattr(row, "geometry", None)
        candidate_id = str(properties.get("candidate_id") or "")
        if geometry is None and properties.get("longitude") is not None:
            geometry = {
                "type": "Point",
                "coordinates": [
                    float(properties["longitude"]),
                    float(properties["latitude"]),
                ],
            }
            geometry_crs = "EPSG:4326"
        elif geometry is None and properties.get("easting") is not None:
            geometry = {
                "type": "Point",
                "coordinates": [
                    float(properties["easting"]),
                    float(properties["northing"]),
                ],
            }
            geometry_crs = str(properties.get("declared_crs") or source_crs)
        else:
            geometry_crs = source_crs
        if geometry is None and candidate_id in candidate_roster:
            geometry = (
                candidate_roster[candidate_id][1].get("geometry")
                if isinstance(candidate_roster[candidate_id][1], Mapping)
                else getattr(candidate_roster[candidate_id][1], "geometry", None)
            )
            geometry_crs = source_crs
            geometry_semantics = "bounded-candidate-route-evidence-no-point"
        else:
            geometry_semantics = "raw-observation-point"
        projected = _geometry_json(geometry, geometry_crs) if geometry is not None else None
        if projected is None:
            continue
        observation_id = str(properties.get("observation_id") or index)
        feature_properties = {
            **properties,
            "layer": StrategicPublicationLayer.DFT_TRAFFIC.value,
            "feature_type": "dft-motor-traffic",
            "candidate_id": candidate_id or None,
            "geometry_semantics": geometry_semantics,
            "source_fingerprint": fingerprint,
            "strategic_result_fingerprint": fingerprint,
        }
        features.append(
            _feature(
                feature_id=f"dft-traffic:{candidate_id or 'observation'}:{observation_id}",
                geometry=projected,
                properties=feature_properties,
            )
        )
    return sorted(features, key=lambda item: str(item["id"]))


def _diagnostic_records(value: object, *, fingerprint: str) -> list[dict[str, object]]:
    """Expose graph diagnostics as stable data records, never null geometries."""

    if value is None:
        return []
    if isinstance(value, Mapping):
        rows = value.get("features", ()) if value.get("type") == "FeatureCollection" else (value,)
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        rows = value
    else:
        rows = (value,)
    records: dict[str, dict[str, object]] = {}
    for row in rows:
        if isinstance(row, Mapping) and row.get("type") == "Feature":
            raw = dict(row.get("properties") or {})
        elif isinstance(row, Mapping):
            raw = dict(row)
        elif hasattr(row, "model_dump"):
            raw = row.model_dump(mode="json")
        else:
            raw = dict(vars(row))
        canonical = {
            str(key): _json_value(item)
            for key, item in raw.items()
            if key not in {"geometry", "layer", "feature_type", "source_fingerprint"}
        }
        diagnostic_id = f"diagnostic-{_fingerprint(canonical)[:20]}"
        records[diagnostic_id] = {
            **canonical,
            "diagnostic_id": diagnostic_id,
            "layer": StrategicPublicationLayer.DIAGNOSTICS.value,
            "strategic_result_fingerprint": fingerprint,
        }
    return [records[key] for key in sorted(records)]


@dataclass(frozen=True)
class StrategicNetworkPublicationProjection:
    """Frozen, JSON-serialisable view used by review maps and artefact writers."""

    feature_collection: dict[str, object]
    reviewable_feature_collection: dict[str, object]
    layers: dict[str, dict[str, object]]
    projection_fingerprint: str
    strategic_result_fingerprint: str
    default_layers: tuple[str, ...]
    optional_layers: tuple[str, ...]
    legend: dict[str, object]

    @property
    def geojson(self) -> dict[str, object]:
        return self.feature_collection

    @property
    def reviewable_geojson(self) -> dict[str, object]:
        return self.reviewable_feature_collection

    @property
    def fingerprint(self) -> str:
        return self.projection_fingerprint


def _legend() -> dict[str, object]:
    entries = {
        key: {
            **value,
            "core_halo_pattern": (
                f"core {value['core']}; halo {value['halo']}; pattern {value['pattern']}"
            ),
        }
        for key, value in sorted(_INTERVENTION_STYLES.items())
    }
    basis = {
        key: {"label": label, "text": label, "halo": colour}
        for key, (label, colour) in sorted(_BASIS_HALO.items())
    }
    return {
        "text": (
            "Core colour shows intervention state; halo shows alignment basis; "
            "pattern remains readable without colour."
        ),
        "core_halo_pattern": (
            "Core = intervention state; halo = alignment basis; pattern = authority/detail."
        ),
        "intervention_state": entries,
        "alignment_basis": basis,
    }


def project_strategic_network(
    result: object,
    *,
    places: object | None = None,
    assets: object | None = None,
    upgradeable_assets: object | None = None,
    traffic: object | None = None,
    diagnostics: object | None = None,
    reviewable_gaps: object | None = None,
    source_crs: str = "EPSG:27700",
    places_crs: str | None = None,
    assets_crs: str | None = None,
    optional_layers: bool = False,
) -> StrategicNetworkPublicationProjection:
    """Project one planning result without selecting, repairing or inventing routes."""

    result_fingerprint = getattr(result, "fingerprint", None)
    effective = getattr(result, "effective_network", None)
    if not isinstance(result_fingerprint, str) or not result_fingerprint:
        raise ValueError("strategic publication requires a fingerprinted planning result")
    if effective is None or not hasattr(effective, "sections"):
        raise ValueError("strategic publication requires an effective strategic network")

    candidate_sets = tuple(getattr(result, "candidate_sets", ()))
    candidate_roster: dict[str, tuple[object, object]] = {}
    for candidate_set in candidate_sets:
        for candidate in tuple(getattr(candidate_set, "candidates", ())):
            candidate_roster[str(getattr(candidate, "candidate_id", ""))] = (
                candidate_set,
                candidate,
            )
    dispositions = {
        str(getattr(item, "candidate_id", "")): item
        for item in getattr(result, "unselected_candidates", ())
    }
    selection_by_candidate = {
        str(getattr(item, "effective_candidate_id", "") or getattr(item, "candidate_id", "")): item
        for item in getattr(result, "selections", ())
    }

    strategic_main_features: list[dict[str, object]] = []
    access_support_features: list[dict[str, object]] = []
    for section in sorted(tuple(effective.sections), key=lambda item: str(item.section_id)):
        publication_layer = _publication_layer_for_role(getattr(section, "network_role", None))
        authority = _text(getattr(section, "authority", None))
        style = _style_for(
            intervention=getattr(section, "intervention_state", None),
            display=getattr(section, "display_state", None),
            authority=authority,
        )
        basis_label, basis_halo, basis_key = _basis_style(
            getattr(section, "alignment_bases", ()),
            getattr(section, "primary_alignment_basis", None),
        )
        section_candidate_id = str(getattr(section, "candidate_id", "") or "")
        candidate_entry = candidate_roster.get(section_candidate_id)
        selection = selection_by_candidate.get(section_candidate_id)
        properties = {
            "layer": StrategicPublicationLayer.STRATEGIC_MAIN_NETWORK.value,
            "feature_type": "reviewable-selected-route",
            "section_id": section.section_id,
            "route_id": section.section_id,
            "obligation_id": section.obligation_id,
            "candidate_id": section.candidate_id,
            "candidate_set_id": None,
            "connection_id": None,
            "selection_disposition": "selected",
            "network_role": section.network_role,
            "authority": authority,
            "alignment_basis": basis_label,
            "alignment_basis_key": basis_key,
            "intervention_state": getattr(section, "intervention_state", None),
            "display_state": getattr(section, "display_state", None),
            "routing_edge_ids": list(getattr(section, "routing_edge_ids", ())),
            "reverse_routing_edge_ids": list(getattr(section, "reverse_routing_edge_ids", ())),
            "core": style["core"],
            "halo": basis_halo,
            "state_halo": style["halo"],
            "pattern": style["pattern"],
            "legend_text": f"{style['text']}; {basis_label}; {style['pattern']} pattern",
            "strategic_result_fingerprint": result_fingerprint,
        }
        if candidate_entry is not None:
            candidate_set, candidate = candidate_entry
            properties.update(_candidate_properties(candidate))
            properties.update(
                {
                    "candidate_id": section_candidate_id,
                    "candidate_set_id": getattr(candidate_set, "candidate_set_id", None),
                    "connection_id": getattr(candidate_set, "connection_id", None),
                    "selection_disposition": getattr(
                        selection, "selection_disposition", "selected"
                    ),
                    "candidate_evidence_fingerprints": list(
                        getattr(candidate, "evidence_fingerprints", ()) or ()
                    ),
                }
            )
        elif selection is not None:
            properties.update(
                {
                    "candidate_set_id": getattr(selection, "candidate_set_id", None),
                    "connection_id": getattr(selection, "connection_id", None),
                    "selection_disposition": getattr(
                        selection, "selection_disposition", "selected"
                    ),
                    "compiler_candidate_id": getattr(selection, "compiler_candidate_id", None),
                }
            )
        properties["layer"] = publication_layer
        feature = _feature(
            feature_id=str(section.section_id),
            geometry=_line_geometry(section.geometry_wkt, source_crs),
            properties=properties,
        )
        if publication_layer == StrategicPublicationLayer.ACCESS_SUPPORT.value:
            access_support_features.append(feature)
        else:
            strategic_main_features.append(feature)

    place_features = _collection_features(
        places,
        source_crs=places_crs or source_crs,
        layer=StrategicPublicationLayer.PLACES.value,
        fingerprint=result_fingerprint,
    )
    layers: dict[str, dict[str, object]] = {
        StrategicPublicationLayer.STRATEGIC_MAIN_NETWORK.value: {
            "type": "FeatureCollection",
            "features": strategic_main_features,
        },
        StrategicPublicationLayer.ACCESS_SUPPORT.value: {
            "type": "FeatureCollection",
            "features": access_support_features,
        },
        StrategicPublicationLayer.PLACES.value: {
            "type": "FeatureCollection",
            "features": place_features,
        },
    }

    if optional_layers:
        # Candidates retain both rejected and admitted alternatives.  Their
        # geometries come only from Candidate Discovery's canonical input.
        candidate_features: list[dict[str, object]] = []
        selected_ids = {
            str(getattr(section, "candidate_id", ""))
            for section in effective.sections
            if getattr(section, "candidate_id", None)
        }
        for candidate_set in sorted(
            candidate_sets, key=lambda item: str(getattr(item, "candidate_set_id", ""))
        ):
            for candidate in sorted(
                tuple(getattr(candidate_set, "candidates", ())),
                key=lambda item: str(getattr(item, "candidate_id", "")),
            ):
                candidate_id = str(candidate.candidate_id)
                if candidate_id in selected_ids:
                    continue
                disposition = dispositions.get(candidate_id)
                admission = next(
                    (
                        item
                        for item in tuple(getattr(candidate_set, "admissions", ()))
                        if str(getattr(item, "candidate_id", "")) == candidate_id
                    ),
                    None,
                )
                candidate_properties = _candidate_properties(candidate)
                candidate_properties.update(
                    {
                        "layer": StrategicPublicationLayer.CANDIDATES.value,
                        "feature_type": "reviewable-unselected-candidate",
                        "candidate_id": candidate_id,
                        "candidate_set_id": getattr(candidate_set, "candidate_set_id", None),
                        "connection_id": getattr(candidate_set, "connection_id", None),
                        "network_role": _text(getattr(candidate_set, "network_role", None)),
                        "disposition": getattr(disposition, "disposition", "unselected"),
                        "reason": getattr(disposition, "reason", "retained alternative"),
                        "admission_disposition": getattr(admission, "disposition", None),
                        "admission_rationale": getattr(admission, "rationale", None),
                        "display_state": "candidate-discarded",
                        "core": "#8c8c8c",
                        "halo": "#d0d0d0",
                        "pattern": "dash",
                        "strategic_result_fingerprint": result_fingerprint,
                    }
                )
                candidate_features.append(
                    _feature(
                        feature_id=f"candidate-{candidate_id}",
                        geometry=_candidate_geometry(candidate, source_crs),
                        properties=candidate_properties,
                    )
                )
        layers[StrategicPublicationLayer.CANDIDATES.value] = {
            "type": "FeatureCollection",
            "features": candidate_features,
        }
        layers[StrategicPublicationLayer.ASSETS.value] = {
            "type": "FeatureCollection",
            "features": _accounting_features(
                assets,
                layer=StrategicPublicationLayer.ASSETS.value,
                fallback_crs=assets_crs or source_crs,
                fingerprint=result_fingerprint,
            ),
        }
        layers[StrategicPublicationLayer.UPGRADEABLE_ASSETS.value] = {
            "type": "FeatureCollection",
            "features": _accounting_features(
                upgradeable_assets,
                layer=StrategicPublicationLayer.UPGRADEABLE_ASSETS.value,
                fallback_crs=assets_crs or source_crs,
                fingerprint=result_fingerprint,
            ),
        }
        if traffic is None:
            derived_traffic: list[dict[str, object]] = []
            for candidate_id, (_candidate_set, candidate) in candidate_roster.items():
                for observation in tuple(getattr(candidate, "traffic_observations", ()) or ()):
                    if hasattr(observation, "model_dump"):
                        properties = observation.model_dump(mode="json")
                    elif isinstance(observation, Mapping):
                        properties = dict(observation)
                    else:
                        properties = dict(vars(observation))
                    properties["candidate_id"] = candidate_id
                    derived_traffic.append(properties)
            traffic = derived_traffic
        layers[StrategicPublicationLayer.DFT_TRAFFIC.value] = {
            "type": "FeatureCollection",
            "features": _traffic_features(
                traffic,
                candidate_roster=candidate_roster,
                source_crs=source_crs,
                fingerprint=result_fingerprint,
            ),
        }
        layers[StrategicPublicationLayer.DIAGNOSTICS.value] = {
            "type": "FeatureCollection",
            "features": [],
            "records": _diagnostic_records(diagnostics, fingerprint=result_fingerprint),
        }
        divergence_features: list[dict[str, object]] = []
        sections_by_obligation = {str(item.obligation_id): item for item in effective.sections}
        for divergence in sorted(
            tuple(getattr(result, "divergences", ())), key=lambda item: str(item.obligation_id)
        ):
            section = sections_by_obligation.get(str(divergence.obligation_id))
            candidate_variants = (
                ("compiler", candidate_roster.get(str(divergence.compiler_candidate_id))),
                ("officer", candidate_roster.get(str(divergence.officer_candidate_id))),
            )
            emitted_variant = False
            for variant, entry in candidate_variants:
                if entry is None:
                    continue
                candidate_set, candidate = entry
                candidate_id = str(getattr(candidate, "candidate_id", ""))
                props = {
                    **_candidate_properties(candidate),
                    "layer": StrategicPublicationLayer.DIVERGENCE.value,
                    "feature_type": "officer-compiler-divergence",
                    "obligation_id": divergence.obligation_id,
                    "network_role": divergence.network_role,
                    "officer_candidate_id": divergence.officer_candidate_id,
                    "compiler_candidate_id": divergence.compiler_candidate_id,
                    "divergence_variant": variant,
                    "candidate_id": candidate_id,
                    "candidate_set_id": getattr(candidate_set, "candidate_set_id", None),
                    "connection_id": getattr(candidate_set, "connection_id", None),
                    "authority": "officer/compiler-divergence",
                    "display_state": "officer-divergence",
                    "core": _INTERVENTION_STYLES["officer-divergence"]["core"],
                    "halo": _INTERVENTION_STYLES["officer-divergence"]["halo"],
                    "pattern": _INTERVENTION_STYLES["officer-divergence"]["pattern"],
                    "legend_text": _INTERVENTION_STYLES["officer-divergence"]["text"],
                    "reason": divergence.reason,
                    "strategic_result_fingerprint": result_fingerprint,
                }
                divergence_features.append(
                    _feature(
                        feature_id=(
                            f"officer-compiler-divergence:{divergence.obligation_id}:"
                            f"{variant}:{candidate_id}"
                        ),
                        geometry=_candidate_geometry(candidate, source_crs),
                        properties=props,
                    )
                )
                emitted_variant = True
            if not emitted_variant:
                divergence_features.append(
                    _feature(
                        feature_id=f"divergence-{divergence.obligation_id}",
                        geometry=(
                            _line_geometry(getattr(section, "geometry_wkt", None), source_crs)
                            if section
                            else None
                        ),
                        properties={
                            "layer": StrategicPublicationLayer.DIVERGENCE.value,
                            "feature_type": "officer-compiler-divergence",
                            "obligation_id": divergence.obligation_id,
                            "network_role": divergence.network_role,
                            "officer_candidate_id": divergence.officer_candidate_id,
                            "compiler_candidate_id": divergence.compiler_candidate_id,
                            "authority": "officer/compiler-divergence",
                            "display_state": "officer-divergence",
                            "core": _INTERVENTION_STYLES["officer-divergence"]["core"],
                            "halo": _INTERVENTION_STYLES["officer-divergence"]["halo"],
                            "pattern": _INTERVENTION_STYLES["officer-divergence"]["pattern"],
                            "legend_text": _INTERVENTION_STYLES["officer-divergence"]["text"],
                            "reason": divergence.reason,
                            "strategic_result_fingerprint": result_fingerprint,
                        },
                    )
                )
        layers[StrategicPublicationLayer.DIVERGENCE.value] = {
            "type": "FeatureCollection",
            "features": divergence_features,
        }
    else:
        for layer in OPTIONAL_LAYERS:
            if layer == StrategicPublicationLayer.ACCESS_SUPPORT.value:
                continue
            layers[layer] = _empty_collection()

    place_points: dict[str, dict[str, object]] = {}
    for place in place_features:
        properties = place.get("properties")
        if not isinstance(properties, Mapping):
            properties = {}
        place_id = properties.get("place_id") or place.get("id")
        geometry = place.get("geometry")
        if place_id is None or not isinstance(geometry, Mapping):
            continue
        try:
            parsed = shape(geometry)
            if parsed.is_empty:
                continue
            point = parsed if parsed.geom_type == "Point" else parsed.representative_point()
            place_points[str(place_id)] = _json_value(mapping(point))  # type: ignore[assignment]
        except (AttributeError, KeyError, TypeError, ValueError):
            continue

    # Gaps are endpoint findings. Emit a governed Point when its endpoint Place
    # is published; mesh coverage findings carry their own proof-point markers.
    gap_features_by_layer: dict[str, list[dict[str, object]]] = {
        StrategicPublicationLayer.STRATEGIC_MAIN_NETWORK.value: [],
        StrategicPublicationLayer.ACCESS_SUPPORT.value: [],
    }
    publication_findings = publication_continuity_findings(result)
    result_gaps = list(getattr(result, "gaps", ()))
    result_gaps.extend(publication_findings)
    known_gap_keys = {
        key
        for item in result_gaps
        for key in (
            str(getattr(item, "gap_id", "")),
            str(getattr(item, "obligation_id", "")),
        )
        if key
    }
    for item in tuple(reviewable_gaps or ()):
        gap_keys = {
            str(getattr(item, "gap_id", "")),
            str(getattr(item, "obligation_id", "")),
        }
        gap_keys.discard("")
        if gap_keys and not (gap_keys & known_gap_keys):
            result_gaps.append(item)
            known_gap_keys.update(gap_keys)
    for gap in sorted(
        tuple(result_gaps),
        key=lambda item: str(getattr(item, "gap_id", getattr(item, "obligation_id", ""))),
    ):
        publication_layer = _publication_layer_for_role(getattr(gap, "network_role", None))
        gap_id = str(getattr(gap, "gap_id", getattr(gap, "obligation_id", "gap")))
        endpoints = tuple(getattr(gap, "endpoints", ()))
        mesh_proof_points = tuple(getattr(gap, "mesh_proof_points", ()))
        representative_coordinates = getattr(gap, "representative_coordinates", None)
        if representative_coordinates is not None:
            identity_key = "representative-point"
            geometry = _geometry_json(Point(representative_coordinates), source_crs)
            properties = {
                "layer": publication_layer,
                "feature_type": "reviewable-gap-endpoint",
                "gap_id": gap_id,
                "obligation_id": getattr(gap, "obligation_id", gap_id),
                "endpoint_id": None,
                "endpoint_identity_key": identity_key,
                "endpoint_position": None,
                "endpoint_identity_fallback": False,
                "network_role": getattr(gap, "network_role", None),
                "endpoints": list(endpoints),
                "candidate_set_id": getattr(gap, "candidate_set_id", None),
                "display_state": "unresolved-gap",
                "missing_endpoint_geometry": False,
                "geometry_semantics": (
                    "selected-main-component-representative-point-marker-only-no-route-geometry"
                ),
                "gap_marker_kind": "selected-main-component-representative",
                "legend_text": "Disconnected selected Main component (representative location)",
                "gap_marker_disclaimer": getattr(gap, "reason", _PUBLICATION_CONTINUITY_REASON),
                "reason": getattr(gap, "reason", _PUBLICATION_CONTINUITY_REASON),
                "publication_finding_kind": getattr(
                    gap, "publication_finding_kind", _PUBLICATION_FINDING_KIND
                ),
                "representative_point": list(representative_coordinates),
                "representative_point_crs": "EPSG:27700",
                "component_section_ids": list(getattr(gap, "component_section_ids", ())),
                "component_obligation_ids": list(getattr(gap, "component_obligation_ids", ())),
                "canonical_geometry_tolerance_m": getattr(
                    gap, "canonical_geometry_tolerance_m", None
                ),
                "continuity_method": _PUBLICATION_CONTINUITY_METHOD,
                "core": _INTERVENTION_STYLES["unresolved-gap"]["core"],
                "halo": _INTERVENTION_STYLES["unresolved-gap"]["halo"],
                "pattern": _INTERVENTION_STYLES["unresolved-gap"]["pattern"],
                "strategic_result_fingerprint": result_fingerprint,
            }
            gap_features_by_layer[publication_layer].append(
                _feature(
                    feature_id=f"reviewable-gap:{gap_id}:{identity_key}",
                    geometry=geometry,
                    properties=properties,
                )
            )
            continue
        for proof_position, coordinates in enumerate(mesh_proof_points, start=1):
            identity_key = f"proof-point-{proof_position}"
            geometry = _geometry_json(Point(coordinates), source_crs)
            gap_features_by_layer[publication_layer].append(
                _feature(
                    feature_id=f"reviewable-gap:{gap_id}:{identity_key}",
                    geometry=geometry,
                    properties={
                        "layer": publication_layer,
                        "feature_type": "reviewable-gap-endpoint",
                        "gap_id": gap_id,
                        "obligation_id": getattr(gap, "obligation_id", gap_id),
                        "endpoint_id": None,
                        "endpoint_identity_key": identity_key,
                        "endpoint_position": None,
                        "endpoint_identity_fallback": False,
                        "network_role": getattr(gap, "network_role", None),
                        "endpoints": list(endpoints),
                        "candidate_set_id": getattr(gap, "candidate_set_id", None),
                        "display_state": "unresolved-gap",
                        "missing_endpoint_geometry": False,
                        "proof_point_position": proof_position,
                        "geometry_semantics": "mesh-proof-point-marker-only-no-route-geometry",
                        "core": _INTERVENTION_STYLES["unresolved-gap"]["core"],
                        "halo": _INTERVENTION_STYLES["unresolved-gap"]["halo"],
                        "pattern": _INTERVENTION_STYLES["unresolved-gap"]["pattern"],
                        "legend_text": _INTERVENTION_STYLES["unresolved-gap"]["text"],
                        "reason": gap.reason,
                        "strategic_result_fingerprint": result_fingerprint,
                    },
                )
            )
        if mesh_proof_points:
            continue
        endpoint_occurrences: dict[str, int] = {}
        for endpoint_position, endpoint_id in enumerate(endpoints, start=1):
            endpoint_key = str(endpoint_id or "")
            endpoint_occurrences[endpoint_key] = endpoint_occurrences.get(endpoint_key, 0) + 1
            occurrence = endpoint_occurrences[endpoint_key]
            identity_key, identity_fallback = gap_endpoint_identity(endpoint_id, occurrence)
            geometry = place_points.get(endpoint_key)
            coordinate_index = endpoint_position - 1
            endpoint_coordinates = tuple(getattr(gap, "endpoint_coordinates", ()))
            if geometry is None and coordinate_index < len(endpoint_coordinates):
                geometry = _geometry_json(Point(endpoint_coordinates[coordinate_index]), source_crs)
            gap_features_by_layer[publication_layer].append(
                _feature(
                    feature_id=f"reviewable-gap:{gap_id}:{identity_key}",
                    geometry=geometry,
                    properties={
                        "layer": publication_layer,
                        "feature_type": "reviewable-gap-endpoint",
                        "gap_id": gap_id,
                        "obligation_id": getattr(gap, "obligation_id", gap_id),
                        "endpoint_id": endpoint_id,
                        "endpoint_identity_key": identity_key,
                        "endpoint_position": endpoint_position,
                        "endpoint_identity_fallback": identity_fallback,
                        "network_role": getattr(gap, "network_role", None),
                        "endpoints": list(endpoints),
                        "candidate_set_id": getattr(gap, "candidate_set_id", None),
                        "display_state": "unresolved-gap",
                        "missing_endpoint_geometry": geometry is None,
                        "geometry_semantics": "endpoint-marker-only-no-route-geometry",
                        "core": _INTERVENTION_STYLES["unresolved-gap"]["core"],
                        "halo": _INTERVENTION_STYLES["unresolved-gap"]["halo"],
                        "pattern": _INTERVENTION_STYLES["unresolved-gap"]["pattern"],
                        "legend_text": _INTERVENTION_STYLES["unresolved-gap"]["text"],
                        "reason": gap.reason,
                        "strategic_result_fingerprint": result_fingerprint,
                    },
                )
            )
    for layer, features in gap_features_by_layer.items():
        layers[layer]["features"] = [*layers[layer]["features"], *features]
    core = {
        "type": "FeatureCollection",
        "name": "SATN effective strategic network map",
        "contract": "satn-reviewable-map/v1",
        "disclaimer": DISCLAIMER,
        "strategic_result_fingerprint": result_fingerprint,
        "publication_finding_count": len(publication_findings),
        "publication_findings": [
            publication_finding_payload(finding) for finding in publication_findings
        ],
        "features": layers[StrategicPublicationLayer.STRATEGIC_MAIN_NETWORK.value]["features"]
        + layers[StrategicPublicationLayer.PLACES.value]["features"],
    }
    reviewable_layer_names = (
        StrategicPublicationLayer.STRATEGIC_MAIN_NETWORK.value,
        StrategicPublicationLayer.ACCESS_SUPPORT.value,
        StrategicPublicationLayer.CANDIDATES.value,
        StrategicPublicationLayer.ASSETS.value,
        StrategicPublicationLayer.UPGRADEABLE_ASSETS.value,
        StrategicPublicationLayer.DFT_TRAFFIC.value,
        StrategicPublicationLayer.DIAGNOSTICS.value,
        StrategicPublicationLayer.DIVERGENCE.value,
    )
    reviewable_features = [
        feature
        for layer_name in reviewable_layer_names
        for feature in layers[layer_name]["features"]
    ]
    reviewable_features.sort(key=lambda item: str(item.get("id")))
    reviewable = {
        "type": "FeatureCollection",
        "name": "SATN effective strategic network map",
        "contract": "satn-reviewable-map/v1",
        "disclaimer": DISCLAIMER,
        "strategic_result_fingerprint": result_fingerprint,
        "default_layers": list(DEFAULT_LAYERS),
        "optional_layers": list(OPTIONAL_LAYERS),
        "legend": _legend(),
        "diagnostics": list(layers[StrategicPublicationLayer.DIAGNOSTICS.value].get("records", ())),
        "publication_finding_count": len(publication_findings),
        "publication_findings": [
            publication_finding_payload(finding) for finding in publication_findings
        ],
        "features": reviewable_features,
    }
    projection_payload = {
        "strategic_result_fingerprint": result_fingerprint,
        "default_layers": DEFAULT_LAYERS,
        "optional_layers": OPTIONAL_LAYERS,
        "layers": layers,
        "feature_collection": core,
        "reviewable_feature_collection": reviewable,
        "publication_finding_count": len(publication_findings),
        "publication_findings": [
            publication_finding_payload(finding) for finding in publication_findings
        ],
        "legend": _legend(),
    }
    return StrategicNetworkPublicationProjection(
        feature_collection=core,
        reviewable_feature_collection=reviewable,
        layers=layers,
        projection_fingerprint=_fingerprint(projection_payload),
        strategic_result_fingerprint=result_fingerprint,
        default_layers=DEFAULT_LAYERS,
        optional_layers=OPTIONAL_LAYERS,
        legend=_legend(),
    )


project_review_map = project_strategic_network
publish_strategic_network_projection = project_strategic_network


__all__ = [
    "DEFAULT_LAYERS",
    "OPTIONAL_LAYERS",
    "StrategicNetworkPublicationProjection",
    "StrategicPublicationFinding",
    "StrategicPublicationLayer",
    "project_review_map",
    "project_strategic_network",
    "publication_continuity_findings",
    "publication_finding_payload",
    "publish_strategic_network_projection",
]
