"""Strict retained wire for the routable edge-enrichment boundary.

This module stores and validates the exact ``GeoDataFrame`` emitted by
``evidence.mark_ncn_edges``.  The wire is canonical typed JSON/WKB only; it
contains no executable objects or import paths.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Final

import geopandas as gpd

from satn.compilation_dependencies import is_compiler_cache_revision
from satn.compiled_network_bundle import BundleCodecError, decode_geodataframe, encode_geodataframe

EDGE_ENRICHMENT_CONTRACT: Final = "satn-edge-enrichments/v1"
EDGE_ENRICHMENT_OUTPUT_ROLE: Final = "routable-network"
EDGE_ENRICHMENT_VALIDATION_CONTRACT: Final = "satn-edge-enrichments-strict/v1"
EDGE_ENRICHMENT_POLICY: Final[dict[str, object]] = {
    "contract": "satn-mark-ncn-edges-policy/v1",
    "corridor_buffer_m": 20.0,
    "minimum_overlap_share": 0.5,
    "overlap_measure": "projected-line-length-share",
    "context_feature_types": [
        "declassified-ncn-route",
        "greenway-cycleway",
        "ncn-route",
    ],
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise BundleCodecError("edge-enrichment value is not canonical JSON") from error


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def policy_fingerprint() -> str:
    """Return the exact policy contract digest bound into each artifact."""

    return _digest(EDGE_ENRICHMENT_POLICY)


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BundleCodecError(f"{label} must be a full lowercase SHA-256")
    return value


def _require_identity(value: object, label: str, *, allow_revision: bool = False) -> str:
    if not isinstance(value, str) or (
        _SHA256.fullmatch(value) is None
        and not (allow_revision and is_compiler_cache_revision(value))
    ):
        expected = "SHA-256 or compiler revision" if allow_revision else "SHA-256"
        raise BundleCodecError(f"{label} must be a full lowercase {expected}")
    return value


def validate_identities(identities: object) -> dict[str, str]:
    """Validate the closed identity roster used by an enrichment payload."""

    if not isinstance(identities, Mapping):
        raise BundleCodecError("edge-enrichment identities must be an object")
    expected = {
        "snapshot_manifest_sha256",
        "area_identity",
        "network_identity",
        "context_identity",
        "policy_fingerprint",
        "implementation_identity",
        "dependency_identity",
    }
    if set(identities) != expected:
        raise BundleCodecError("edge-enrichment identities have an unexpected roster")
    result = {
        key: _require_identity(
            identities[key],
            key,
            allow_revision=key in {"implementation_identity", "dependency_identity"},
        )
        for key in expected
    }
    if result["policy_fingerprint"] != policy_fingerprint():
        raise BundleCodecError("edge-enrichment policy fingerprint is stale")
    return result


def stable_key_columns(frame: gpd.GeoDataFrame) -> tuple[str, ...] | None:
    """Select the source-edge key used by canonical row ordering."""

    if not isinstance(frame, gpd.GeoDataFrame):
        raise TypeError("edge enrichment output must be a GeoDataFrame")
    if "satn_ncn" not in frame.columns:
        raise BundleCodecError("edge enrichment output must include satn_ncn")
    if str(frame["satn_ncn"].dtype) != "bool":
        raise BundleCodecError("satn_ncn must be a boolean column")
    if frame.geometry.name not in frame.columns:
        raise BundleCodecError("edge enrichment output has no active geometry column")
    for keys in (("source_id", "u", "v", "key"), ("source_id", "u", "v"), ("source_id",)):
        if all(key in frame.columns for key in keys):
            values = frame.loc[:, list(keys)]
            if not values.isna().any(axis=None) and not values.duplicated().any():
                return keys
    return None


def encode_routable_edge_enrichment(
    frame: gpd.GeoDataFrame,
    *,
    identities: Mapping[str, str],
) -> dict[str, object]:
    """Encode one exact marked-network frame in a strict non-executable wire."""

    keys = stable_key_columns(frame)
    validated_identities = validate_identities(identities)
    wire = encode_geodataframe(frame.copy(deep=True), stable_key_columns=keys)
    body: dict[str, object] = {
        "contract": EDGE_ENRICHMENT_CONTRACT,
        "output_roster": [EDGE_ENRICHMENT_OUTPUT_ROLE],
        "identities": validated_identities,
        "policy": EDGE_ENRICHMENT_POLICY,
        "frame": wire,
    }
    return {**body, "content_sha256": _digest(body)}


def decode_routable_edge_enrichment(
    payload: object,
    *,
    identities: Mapping[str, str] | None = None,
) -> gpd.GeoDataFrame:
    """Strictly validate and decode one retained routable-network frame."""

    if not isinstance(payload, Mapping):
        raise BundleCodecError("edge-enrichment payload must be an object")
    expected = {"contract", "output_roster", "identities", "policy", "frame", "content_sha256"}
    if set(payload) != expected:
        raise BundleCodecError("edge-enrichment payload has an unexpected roster")
    content = payload.get("content_sha256")
    if not isinstance(content, str) or _SHA256.fullmatch(content) is None:
        raise BundleCodecError("edge-enrichment content fingerprint is invalid")
    body = {key: payload[key] for key in payload if key != "content_sha256"}
    if _digest(body) != content:
        raise BundleCodecError("edge-enrichment content fingerprint mismatch")
    if payload["contract"] != EDGE_ENRICHMENT_CONTRACT:
        raise BundleCodecError("unsupported edge-enrichment contract")
    if payload["output_roster"] != [EDGE_ENRICHMENT_OUTPUT_ROLE]:
        raise BundleCodecError("edge-enrichment output roster is invalid")
    if payload["policy"] != EDGE_ENRICHMENT_POLICY:
        raise BundleCodecError("edge-enrichment policy contract is invalid")
    actual = validate_identities(payload["identities"])
    if identities is not None and actual != validate_identities(identities):
        raise BundleCodecError("edge-enrichment identities differ from expected inputs")
    frame = decode_geodataframe(payload["frame"])
    stable_key_columns(frame)
    return frame


__all__ = [
    "EDGE_ENRICHMENT_CONTRACT",
    "EDGE_ENRICHMENT_OUTPUT_ROLE",
    "EDGE_ENRICHMENT_POLICY",
    "EDGE_ENRICHMENT_VALIDATION_CONTRACT",
    "decode_routable_edge_enrichment",
    "encode_routable_edge_enrichment",
    "policy_fingerprint",
    "stable_key_columns",
    "validate_identities",
]
