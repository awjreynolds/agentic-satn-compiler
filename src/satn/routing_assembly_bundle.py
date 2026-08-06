"""Canonical retained wire state for Backbone and Cross-Spine replay.

This module is deliberately a data-only seam.  It records the result after
``assemble_backbone_outward`` and ``resolve_cross_spine_assembly`` so a later
stage can validate and rehydrate routing state without importing a class by
name from the wire payload.  The caller supplies the expected dataclass type
to the decoder.

The progress callback accepted by Cross-Spine is intentionally not persisted:
it is an operational observer, not compiler state consumed downstream.
Executable callbacks, graph objects and runtime handles are not representable
and are rejected by the underlying canonical codec.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, ClassVar

import geopandas as gpd
import numpy as np
from pydantic import BaseModel

from satn.compiled_network_bundle import (
    BundleCodecError,
    decode_compiled_network_bundle,
    encode_compiled_network_bundle,
)
from satn.models import AgentDecisionResponse, AgentRecord

ROUTING_ASSEMBLY_BUNDLE_CONTRACT = "satn-routing-assembly-bundle/v1"
_COMPILED_BUNDLE_CONTRACT = "satn-compiled-network-bundle/v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")

ROUTING_FRAME_STABLE_KEYS: dict[str, tuple[str, ...]] = {
    "connections": ("access_connection_id",),
    "obligations": ("obligation_id",),
    "branches": ("branch_id",),
    "meeting_connections": ("meeting_connection_id",),
    "cross_spine_connectors": ("cross_spine_connector_id",),
    "gaps": ("connection_id",),
    "valid_cross_spine_connectors": ("cross_spine_connector_id",),
    "route_refinement_findings": ("connection_id",),
}

# These are deliberately documented rather than silently omitted from a
# future direct ``BackboneAssembly``/``CrossSpineAssembly`` adapter.
UNREPRESENTABLE_ROUTING_FIELDS = (
    "CrossSpineProgress callback (executable observer; not downstream state)",
    "RoadGraph object and runtime/cache handles (not emitted by either assembly)",
)


@dataclass(frozen=True)
class RoutingAssemblyBundle:
    """Complete typed replay state at the routing assembly boundary."""

    connections: gpd.GeoDataFrame
    obligations: gpd.GeoDataFrame
    branches: gpd.GeoDataFrame
    meeting_connections: gpd.GeoDataFrame
    cross_spine_connectors: gpd.GeoDataFrame
    gaps: gpd.GeoDataFrame
    gateway_count: int
    connected_gateway_count: int
    agent_records: tuple[AgentRecord, ...]
    accepted_responses: tuple[AgentDecisionResponse, ...]
    compilation_diagnostics: dict[str, object]
    cross_spine_assembly_diagnostics: dict[str, object]
    valid_cross_spine_connectors: gpd.GeoDataFrame
    route_refinement_findings: gpd.GeoDataFrame
    cross_spine_diagnostics: dict[str, object]
    contract: ClassVar[str] = ROUTING_ASSEMBLY_BUNDLE_CONTRACT

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name in ROUTING_FRAME_STABLE_KEYS and not isinstance(value, gpd.GeoDataFrame):
                raise TypeError(f"{item.name} must be a GeoDataFrame")
        if (
            isinstance(self.gateway_count, bool)
            or not isinstance(self.gateway_count, int)
            or self.gateway_count < 0
        ):
            raise ValueError("gateway_count must be a nonnegative integer")
        if (
            isinstance(self.connected_gateway_count, bool)
            or not isinstance(self.connected_gateway_count, int)
            or self.connected_gateway_count < 0
            or self.connected_gateway_count > self.gateway_count
        ):
            raise ValueError("connected_gateway_count must be between zero and gateway_count")
        if not isinstance(self.agent_records, tuple) or not all(
            isinstance(record, AgentRecord) for record in self.agent_records
        ):
            raise TypeError("agent_records must be a tuple of AgentRecord values")
        if not isinstance(self.accepted_responses, tuple) or not all(
            isinstance(response, AgentDecisionResponse)
            for response in self.accepted_responses
        ):
            raise TypeError(
                "accepted_responses must be a tuple of AgentDecisionResponse values"
            )
        responses = tuple(
            sorted(
                (
                    response.model_copy(deep=True)
                    for response in self.accepted_responses
                ),
                key=lambda response: response.request_id,
            )
        )
        if len({response.request_id for response in responses}) != len(responses):
            raise ValueError("accepted decision responses must have unique request IDs")
        records_by_request: dict[str, AgentRecord] = {}
        for record in self.agent_records:
            request = record.decision_request
            if request is None:
                continue
            if request.request_id in records_by_request:
                raise ValueError("reviewed AgentRecords must have unique request IDs")
            records_by_request[request.request_id] = record
        response_ids = {response.request_id for response in responses}
        if response_ids != set(records_by_request):
            raise ValueError("accepted responses and reviewed AgentRecords must match exactly")
        for response in responses:
            record = records_by_request[response.request_id]
            request = record.decision_request
            assert request is not None
            if (
                response.dependency_fingerprint != request.dependency_fingerprint
                or response.choice_id != record.selected_choice_id
            ):
                raise ValueError("accepted response does not match its reviewed AgentRecord")
        object.__setattr__(self, "accepted_responses", responses)
        for name in (
            "compilation_diagnostics",
            "cross_spine_assembly_diagnostics",
            "cross_spine_diagnostics",
        ):
            if not isinstance(getattr(self, name), dict):
                raise TypeError(f"{name} must be a dictionary")
            _reject_nonfinite(getattr(self, name), name)
        for name, keys in ROUTING_FRAME_STABLE_KEYS.items():
            frame = getattr(self, name)
            if frame.geometry.name not in frame.columns:
                raise ValueError(f"{name} must have an active geometry column")
            for key in keys:
                if key not in frame.columns:
                    raise ValueError(f"{name} is missing stable key column {key!r}")

    @classmethod
    def from_assemblies(
        cls,
        backbone: object,
        cross_spine: object,
        accepted_responses: Sequence[AgentDecisionResponse] = (),
    ) -> RoutingAssemblyBundle:
        """Build a bundle from the two assembly result objects structurally.

        Structural access keeps this sidecar out of the compiler import graph;
        it still validates the complete typed state in ``__post_init__``.
        """

        return cls(
            connections=deepcopy(backbone.connections),
            obligations=deepcopy(backbone.obligations),
            branches=deepcopy(backbone.branches),
            meeting_connections=deepcopy(backbone.meeting_connections),
            cross_spine_connectors=deepcopy(backbone.cross_spine_connectors),
            gaps=deepcopy(backbone.gaps),
            gateway_count=backbone.gateway_count,
            connected_gateway_count=backbone.connected_gateway_count,
            agent_records=tuple(
                record.model_copy(deep=True) for record in cross_spine.agent_records
            ),
            accepted_responses=tuple(
                response.model_copy(deep=True) for response in accepted_responses
            ),
            compilation_diagnostics=deepcopy(backbone.compilation_diagnostics),
            cross_spine_assembly_diagnostics=deepcopy(backbone.cross_spine_assembly_diagnostics),
            valid_cross_spine_connectors=deepcopy(cross_spine.valid_connectors),
            route_refinement_findings=deepcopy(cross_spine.route_refinement_findings),
            cross_spine_diagnostics=deepcopy(cross_spine.diagnostics),
        )


def _reject_nonfinite(value: object, path: str) -> None:
    """Reject NaN/Infinity before values reach canonical JSON encoding."""

    if isinstance(value, np.generic):
        _reject_nonfinite(value.item(), path)
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} contains a non-finite float")
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _reject_nonfinite(getattr(value, name), f"{path}.{name}")
    elif is_dataclass(value) and not isinstance(value, type):
        for item in fields(value):
            _reject_nonfinite(getattr(value, item.name), f"{path}.{item.name}")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _reject_nonfinite(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_nonfinite(item, f"{path}[{index}]")


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BundleCodecError("routing assembly bundle is not canonical JSON") from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_keys(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BundleCodecError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise BundleCodecError(f"{label} keys differ")
    return value


def _identities(
    *, area_identity: str, input_identity: str, dependency_identity: str
) -> dict[str, str]:
    values = {"area": area_identity, "input": input_identity, "dependency": dependency_identity}
    if any(
        not isinstance(value, str) or _SHA256.fullmatch(value) is None
        for value in values.values()
    ):
        raise BundleCodecError("routing identities must be full lowercase SHA-256 values")
    return values


def _upstream_ids(upstream_artifact_ids: Sequence[str]) -> list[str]:
    values = list(upstream_artifact_ids)
    if any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in values):
        raise BundleCodecError("upstream artifact IDs must be full lowercase SHA-256 values")
    if len(values) != len(set(values)):
        raise BundleCodecError("upstream artifact IDs must be unique")
    return sorted(values)


def encode_routing_assembly_bundle(
    bundle: RoutingAssemblyBundle,
    *,
    area_identity: str,
    input_identity: str,
    dependency_identity: str,
    upstream_artifact_ids: tuple[str, ...],
    bundle_crs: object | None = None,
) -> dict[str, object]:
    """Encode a canonical non-executable routing assembly envelope."""

    if not isinstance(bundle, RoutingAssemblyBundle):
        raise TypeError("bundle must be a RoutingAssemblyBundle")
    for name in (
        "compilation_diagnostics",
        "cross_spine_assembly_diagnostics",
        "cross_spine_diagnostics",
    ):
        _reject_nonfinite(getattr(bundle, name), name)
    identities = _identities(
        area_identity=area_identity,
        input_identity=input_identity,
        dependency_identity=dependency_identity,
    )
    upstream_ids = _upstream_ids(upstream_artifact_ids)
    payload = encode_compiled_network_bundle(
        bundle,
        area_identity=area_identity,
        input_identity=input_identity,
        dependency_identity=dependency_identity,
        upstream_artifact_ids=tuple(upstream_ids),
        frame_stable_keys=ROUTING_FRAME_STABLE_KEYS,
        bundle_crs=bundle_crs,
    )
    body: dict[str, object] = {
        "contract": ROUTING_ASSEMBLY_BUNDLE_CONTRACT,
        "dataclass": type(bundle).__name__,
        "identities": identities,
        "upstream_artifact_ids": upstream_ids,
        "payload": payload,
    }
    return {**body, "content_sha256": _sha256(body)}


def decode_routing_assembly_bundle(
    payload: object,
    expected_type: type[RoutingAssemblyBundle],
) -> RoutingAssemblyBundle:
    """Strictly decode only the caller-supplied routing bundle type."""

    if expected_type is not RoutingAssemblyBundle:
        raise TypeError("expected_type must be RoutingAssemblyBundle")
    wire = _require_keys(
        payload,
        {
            "contract",
            "dataclass",
            "identities",
            "upstream_artifact_ids",
            "payload",
            "content_sha256",
        },
        "RoutingAssemblyBundle",
    )
    digest = wire["content_sha256"]
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise BundleCodecError("routing bundle content_sha256 must be a full lowercase SHA-256")
    body = {key: value for key, value in wire.items() if key != "content_sha256"}
    if _sha256(body) != digest:
        raise BundleCodecError("routing bundle content fingerprint mismatch")
    if wire["contract"] != ROUTING_ASSEMBLY_BUNDLE_CONTRACT:
        raise BundleCodecError("unsupported routing assembly bundle contract")
    if wire["dataclass"] != expected_type.__name__:
        raise BundleCodecError("routing bundle dataclass does not match expected type")
    identities = _require_keys(wire["identities"], {"area", "input", "dependency"}, "identities")
    _identities(**{f"{name}_identity": identities[name] for name in identities})
    upstream_wire = wire["upstream_artifact_ids"]
    if (
        not isinstance(upstream_wire, list)
        or upstream_wire != sorted(set(upstream_wire))
        or any(
            not isinstance(value, str) or _SHA256.fullmatch(value) is None
            for value in upstream_wire
        )
    ):
        raise BundleCodecError("upstream artifact IDs must be sorted unique SHA-256 values")
    inner = _require_keys(
        wire["payload"],
        {
            "contract",
            "dataclass",
            "identities",
            "upstream_artifact_ids",
            "frame_crs_rule",
            "fields",
            "content_sha256",
        },
        "compiled routing payload",
    )
    if inner["contract"] != _COMPILED_BUNDLE_CONTRACT:
        raise BundleCodecError("routing payload has an unexpected nested contract")
    if inner["identities"] != identities or inner["upstream_artifact_ids"] != upstream_wire:
        raise BundleCodecError("nested routing payload identities differ from envelope")
    decoded = decode_compiled_network_bundle(inner, expected_type)
    if not isinstance(decoded, RoutingAssemblyBundle):
        raise BundleCodecError("nested routing payload did not produce a RoutingAssemblyBundle")
    return decoded


__all__ = [
    "ROUTING_ASSEMBLY_BUNDLE_CONTRACT",
    "ROUTING_FRAME_STABLE_KEYS",
    "UNREPRESENTABLE_ROUTING_FIELDS",
    "RoutingAssemblyBundle",
    "decode_routing_assembly_bundle",
    "encode_routing_assembly_bundle",
]
