"""Pure projection of the effective strategic network into review-map GeoJSON.

The selector owns authority.  This module only projects that immutable result and
keeps contextual layers optional.  In particular, a historic Backbone is never
reintroduced as a map feature: only ``effective_network`` sections are strategic
authority.  All coordinates emitted by this adapter are WGS84 GeoJSON.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform as transform_geometry
from shapely.wkt import loads as load_wkt

from satn.constants import DISCLAIMER


class StrategicPublicationLayer(StrEnum):
    STRATEGIC_NETWORK = "Strategic Network"
    PLACES = "Places"
    CANDIDATES = "Candidates discarded"
    ASSETS = "Existing Assets"
    UPGRADEABLE_ASSETS = "Upgradeable Assets"
    DFT_TRAFFIC = "DfT Traffic"
    DIAGNOSTICS = "Graph Diagnostics"
    DIVERGENCE = "Officer Divergence"


DEFAULT_LAYERS = (
    StrategicPublicationLayer.STRATEGIC_NETWORK.value,
    StrategicPublicationLayer.PLACES.value,
)
OPTIONAL_LAYERS = (
    StrategicPublicationLayer.CANDIDATES.value,
    StrategicPublicationLayer.ASSETS.value,
    StrategicPublicationLayer.UPGRADEABLE_ASSETS.value,
    StrategicPublicationLayer.DFT_TRAFFIC.value,
    StrategicPublicationLayer.DIAGNOSTICS.value,
    StrategicPublicationLayer.DIVERGENCE.value,
)


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
        features.append(
            _feature(
                feature_id=f"{layer.lower().replace(' ', '-')}-{feature_id}",
                geometry=projected,
                properties={
                    **properties,
                    "layer": layer,
                    **(
                        {"feature_type": "asset-existing-provision"}
                        if layer == StrategicPublicationLayer.ASSETS.value
                        else {"feature_type": "asset-upgrade-required"}
                        if layer == StrategicPublicationLayer.UPGRADEABLE_ASSETS.value
                        else {"feature_type": "dft-motor-traffic"}
                        if layer == StrategicPublicationLayer.DFT_TRAFFIC.value
                        else {}
                    ),
                    "source_fingerprint": fingerprint,
                    "strategic_result_fingerprint": fingerprint,
                },
            )
        )
    return sorted(features, key=lambda item: str(item["id"]))


@dataclass(frozen=True)
class StrategicNetworkPublicationProjection:
    """Frozen, JSON-serialisable view used by review maps and artefact writers."""

    feature_collection: dict[str, object]
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
    source_crs: str = "EPSG:27700",
    places_crs: str | None = None,
    optional_layers: bool = False,
) -> StrategicNetworkPublicationProjection:
    """Project one planning result without selecting, repairing or inventing routes."""

    result_fingerprint = getattr(result, "fingerprint", None)
    effective = getattr(result, "effective_network", None)
    if not isinstance(result_fingerprint, str) or not result_fingerprint:
        raise ValueError("strategic publication requires a fingerprinted planning result")
    if effective is None or not hasattr(effective, "sections"):
        raise ValueError("strategic publication requires an effective strategic network")

    strategic_features: list[dict[str, object]] = []
    for section in sorted(tuple(effective.sections), key=lambda item: str(item.section_id)):
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
        properties = {
            "layer": StrategicPublicationLayer.STRATEGIC_NETWORK.value,
            "feature_type": "reviewable-selected-route",
            "section_id": section.section_id,
            "route_id": section.section_id,
            "obligation_id": section.obligation_id,
            "candidate_id": section.candidate_id,
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
        strategic_features.append(
            _feature(
                feature_id=str(section.section_id),
                geometry=_line_geometry(section.geometry_wkt, source_crs),
                properties=properties,
            )
        )

    place_features = _collection_features(
        places,
        source_crs=places_crs or source_crs,
        layer=StrategicPublicationLayer.PLACES.value,
        fingerprint=result_fingerprint,
    )
    layers: dict[str, dict[str, object]] = {
        StrategicPublicationLayer.STRATEGIC_NETWORK.value: {
            "type": "FeatureCollection",
            "features": strategic_features,
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
        selected_ids = {str(getattr(section, "candidate_id", "")) for section in effective.sections}
        candidate_sets = tuple(getattr(result, "candidate_sets", ()))
        dispositions = {
            str(item.candidate_id): item for item in getattr(result, "unselected_candidates", ())
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
                candidate_features.append(
                    _feature(
                        feature_id=f"candidate-{candidate_id}",
                        geometry=_line_geometry(getattr(candidate, "geometry", None), source_crs),
                        properties={
                            "layer": StrategicPublicationLayer.CANDIDATES.value,
                            "feature_type": "reviewable-unselected-candidate",
                            "candidate_id": candidate_id,
                            "candidate_set_id": getattr(candidate_set, "candidate_set_id", None),
                            "network_role": _text(getattr(candidate_set, "network_role", None)),
                            "disposition": getattr(disposition, "disposition", "unselected"),
                            "reason": getattr(disposition, "reason", "retained alternative"),
                            "display_state": "candidate-discarded",
                            "core": "#8c8c8c",
                            "halo": "#d0d0d0",
                            "pattern": "dash",
                            "strategic_result_fingerprint": result_fingerprint,
                        },
                    )
                )
        layers[StrategicPublicationLayer.CANDIDATES.value] = {
            "type": "FeatureCollection",
            "features": candidate_features,
        }
        layers[StrategicPublicationLayer.ASSETS.value] = {
            "type": "FeatureCollection",
            "features": _collection_features(
                assets,
                source_crs=source_crs,
                layer=StrategicPublicationLayer.ASSETS.value,
                fingerprint=result_fingerprint,
            ),
        }
        layers[StrategicPublicationLayer.UPGRADEABLE_ASSETS.value] = {
            "type": "FeatureCollection",
            "features": _collection_features(
                upgradeable_assets,
                source_crs=source_crs,
                layer=StrategicPublicationLayer.UPGRADEABLE_ASSETS.value,
                fingerprint=result_fingerprint,
            ),
        }
        layers[StrategicPublicationLayer.DFT_TRAFFIC.value] = {
            "type": "FeatureCollection",
            "features": _collection_features(
                traffic,
                source_crs=source_crs,
                layer=StrategicPublicationLayer.DFT_TRAFFIC.value,
                fingerprint=result_fingerprint,
            ),
        }
        layers[StrategicPublicationLayer.DIAGNOSTICS.value] = {
            "type": "FeatureCollection",
            "features": _collection_features(
                diagnostics,
                source_crs=source_crs,
                layer=StrategicPublicationLayer.DIAGNOSTICS.value,
                fingerprint=result_fingerprint,
            ),
        }
        divergence_features: list[dict[str, object]] = []
        sections_by_obligation = {str(item.obligation_id): item for item in effective.sections}
        for divergence in sorted(
            tuple(getattr(result, "divergences", ())), key=lambda item: str(item.obligation_id)
        ):
            section = sections_by_obligation.get(str(divergence.obligation_id))
            divergence_features.append(
                _feature(
                    feature_id=f"divergence-{divergence.obligation_id}",
                    geometry=_line_geometry(getattr(section, "geometry_wkt", None), source_crs)
                    if section
                    else None,
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
            layers[layer] = _empty_collection()

    # Gaps are deliberately non-line features.  Endpoint identities are facts;
    # coordinates would be invented unless a separately governed place layer is
    # supplied, so map consumers render these as point/null review markers.
    gap_features = [
        _feature(
            feature_id=f"gap-{gap.obligation_id}",
            geometry=None,
            properties={
                "layer": StrategicPublicationLayer.STRATEGIC_NETWORK.value,
                "feature_type": "reviewable-gap-endpoint",
                "gap_id": gap.obligation_id,
                "obligation_id": gap.obligation_id,
                "network_role": gap.network_role,
                "endpoints": list(gap.endpoints),
                "display_state": "unresolved-gap",
                "missing_endpoint_geometry": True,
                "core": _INTERVENTION_STYLES["unresolved-gap"]["core"],
                "halo": _INTERVENTION_STYLES["unresolved-gap"]["halo"],
                "pattern": _INTERVENTION_STYLES["unresolved-gap"]["pattern"],
                "legend_text": _INTERVENTION_STYLES["unresolved-gap"]["text"],
                "reason": gap.reason,
                "strategic_result_fingerprint": result_fingerprint,
            },
        )
        for gap in sorted(
            tuple(getattr(result, "gaps", ())), key=lambda item: str(item.obligation_id)
        )
    ]
    layers[StrategicPublicationLayer.STRATEGIC_NETWORK.value]["features"] = [
        *layers[StrategicPublicationLayer.STRATEGIC_NETWORK.value]["features"],
        *gap_features,
    ]
    core = {
        "type": "FeatureCollection",
        "name": "SATN effective strategic network map",
        "contract": "satn-reviewable-map/v1",
        "disclaimer": DISCLAIMER,
        "strategic_result_fingerprint": result_fingerprint,
        "features": layers[StrategicPublicationLayer.STRATEGIC_NETWORK.value]["features"]
        + layers[StrategicPublicationLayer.PLACES.value]["features"],
    }
    projection_payload = {
        "strategic_result_fingerprint": result_fingerprint,
        "default_layers": DEFAULT_LAYERS,
        "optional_layers": OPTIONAL_LAYERS,
        "layers": layers,
        "legend": _legend(),
    }
    return StrategicNetworkPublicationProjection(
        feature_collection=core,
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
    "StrategicPublicationLayer",
    "project_review_map",
    "project_strategic_network",
    "publish_strategic_network_projection",
]
