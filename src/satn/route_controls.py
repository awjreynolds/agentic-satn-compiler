"""Deterministic route-control primitives for governed scenario compilation.

This module deliberately contains no officer identity, authentication or
publication authority.  The officer-decision overlay translates an accepted,
attributable decision into one ``RouteControlSet``; routing and replay then
consume only this small immutable contract.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satn.content_identity import content_fingerprint

_SHA256 = r"^[0-9a-f]{64}$"
_BINDING_ID = r"^route-edge-[0-9a-f]{20}$"
_CONTROL_SET_ID = r"^route-controls-[0-9a-f]{20}$"
_GAP_ID = r"^network-gap-[0-9a-f]{20}$"


def _stable_id(prefix: str, payload: object) -> str:
    return f"{prefix}-{content_fingerprint(payload)[:20]}"


def _canonical_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank stable identifier")
    return value.strip()


def _canonical_ids(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    canonical = tuple(sorted({_canonical_text(value, field) for value in values}))
    if len(canonical) != len(values):
        raise ValueError(f"{field} must be unique")
    return canonical


class EdgeBindingMode(StrEnum):
    """Whether one directed edge or an explicit reciprocal pair is controlled."""

    DIRECTIONAL = "directional"
    BIDIRECTIONAL = "bidirectional"


class DirectedEdgeBinding(BaseModel):
    """One exact directed edge bound to current evidence and geometry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256)
    source_edge_id: str
    from_node_id: str
    to_node_id: str
    geometry_fingerprint: str = Field(pattern=_SHA256)
    binding_id: str = Field(default="", pattern=_BINDING_ID)

    @field_validator("source_edge_id", "from_node_id", "to_node_id")
    @classmethod
    def validate_ids(cls, value: str, info: object) -> str:
        return _canonical_text(value, getattr(info, "field_name", "edge identifier"))

    @model_validator(mode="after")
    def bind_identity(self) -> Self:
        if self.from_node_id == self.to_node_id:
            raise ValueError("route edge endpoints must be distinct")
        payload = self.model_dump(mode="json", exclude={"binding_id"})
        expected = _stable_id("route-edge", payload)
        if self.binding_id and self.binding_id != expected:
            raise ValueError("route edge binding identity is stale")
        object.__setattr__(self, "binding_id", expected)
        return self

    @property
    def directed_key(self) -> tuple[str, str]:
        return self.from_node_id, self.to_node_id


class RouteEdgeBinding(BaseModel):
    """A direction-specific or explicitly bidirectional stable edge target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: EdgeBindingMode
    directions: tuple[DirectedEdgeBinding, ...] = Field(min_length=1, max_length=2)
    binding_id: str = Field(default="", pattern=_BINDING_ID)

    @model_validator(mode="after")
    def bind_directions(self) -> Self:
        directions = tuple(
            sorted(
                (
                    DirectedEdgeBinding.model_validate(item.model_dump(mode="python"))
                    for item in self.directions
                ),
                key=lambda item: (
                    item.from_node_id,
                    item.to_node_id,
                    item.source_edge_id,
                    item.binding_id,
                ),
            )
        )
        if len({item.directed_key for item in directions}) != len(directions):
            raise ValueError("route edge binding contains duplicate directed edges")
        if self.mode == EdgeBindingMode.DIRECTIONAL and len(directions) != 1:
            raise ValueError("directional route control requires exactly one directed edge")
        if self.mode == EdgeBindingMode.BIDIRECTIONAL:
            if len(directions) != 2:
                raise ValueError("bidirectional route control requires two directed edges")
            left, right = directions
            if left.directed_key != tuple(reversed(right.directed_key)):
                raise ValueError(
                    "bidirectional route control requires an explicit reciprocal edge pair"
                )
        snapshots = {item.evidence_snapshot_fingerprint for item in directions}
        if len(snapshots) != 1:
            raise ValueError("route edge directions must bind one evidence snapshot")
        object.__setattr__(self, "directions", directions)
        payload = {
            "mode": self.mode,
            "directions": [
                item.model_dump(mode="json") for item in directions
            ],
        }
        expected = _stable_id("route-edge", payload)
        if self.binding_id and self.binding_id != expected:
            raise ValueError("route edge group identity is stale")
        object.__setattr__(self, "binding_id", expected)
        return self

    @property
    def evidence_snapshot_fingerprint(self) -> str:
        return self.directions[0].evidence_snapshot_fingerprint


class RouteControlSet(BaseModel):
    """The minimal translation target for accepted officer route decisions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal["satn-route-controls/v1"] = "satn-route-controls/v1"
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256)
    strategic_spine_exclusions: tuple[RouteEdgeBinding, ...] = ()
    routing_exclusions: tuple[RouteEdgeBinding, ...] = ()
    control_set_id: str = Field(default="", pattern=_CONTROL_SET_ID)
    control_fingerprint: str = Field(default="", pattern=_SHA256)

    @model_validator(mode="after")
    def bind_controls(self) -> Self:
        strategic = self._canonical_bindings(self.strategic_spine_exclusions)
        routing = self._canonical_bindings(self.routing_exclusions)
        all_bindings = (*strategic, *routing)
        if any(
            item.evidence_snapshot_fingerprint != self.evidence_snapshot_fingerprint
            for item in all_bindings
        ):
            raise ValueError("route controls are stale for the evidence snapshot")
        strategic_keys = {
            direction.directed_key
            for binding in strategic
            for direction in binding.directions
        }
        routing_keys = {
            direction.directed_key
            for binding in routing
            for direction in binding.directions
        }
        if strategic_keys & routing_keys:
            raise ValueError(
                "one directed edge cannot have both strategic-only and routing exclusions"
            )
        object.__setattr__(self, "strategic_spine_exclusions", strategic)
        object.__setattr__(self, "routing_exclusions", routing)
        payload = self.model_dump(
            mode="json",
            exclude={"control_set_id", "control_fingerprint"},
        )
        fingerprint = content_fingerprint(payload)
        identifier = _stable_id("route-controls", payload)
        if self.control_set_id and self.control_set_id != identifier:
            raise ValueError("route control set identity is stale")
        if self.control_fingerprint and self.control_fingerprint != fingerprint:
            raise ValueError("route control set fingerprint is stale")
        object.__setattr__(self, "control_set_id", identifier)
        object.__setattr__(self, "control_fingerprint", fingerprint)
        return self

    @staticmethod
    def _canonical_bindings(
        bindings: tuple[RouteEdgeBinding, ...],
    ) -> tuple[RouteEdgeBinding, ...]:
        canonical = tuple(
            sorted(
                (
                    RouteEdgeBinding.model_validate(item.model_dump(mode="python"))
                    for item in bindings
                ),
                key=lambda item: item.binding_id,
            )
        )
        if len({item.binding_id for item in canonical}) != len(canonical):
            raise ValueError("route controls cannot repeat one edge binding")
        direction_keys = [
            direction.directed_key
            for binding in canonical
            for direction in binding.directions
        ]
        if len(set(direction_keys)) != len(direction_keys):
            raise ValueError("route controls cannot repeat one directed edge")
        return canonical

    @property
    def excluded_binding_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                binding.binding_id
                for binding in (
                    *self.strategic_spine_exclusions,
                    *self.routing_exclusions,
                )
            )
        )


class RouteControlNetworkGap(BaseModel):
    """Visible no-geometry result when governed exclusions leave no route."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal["satn-route-control-network-gap/v1"] = (
        "satn-route-control-network-gap/v1"
    )
    from_node_id: str
    to_node_id: str
    route_role: str
    unsatisfied_network_place_ids: tuple[str, ...] = ()
    unsatisfied_access_obligation_ids: tuple[str, ...] = ()
    unsatisfied_strategic_destination_ids: tuple[str, ...] = ()
    evidence_snapshot_fingerprint: str = Field(pattern=_SHA256)
    route_control_fingerprint: str = Field(pattern=_SHA256)
    excluded_edge_binding_ids: tuple[str, ...] = Field(min_length=1)
    reason: Literal["no-route-after-governed-exclusions"] = (
        "no-route-after-governed-exclusions"
    )
    geometry_status: Literal["not-generated"] = "not-generated"
    gap_id: str = Field(default="", pattern=_GAP_ID)
    gap_fingerprint: str = Field(default="", pattern=_SHA256)

    @field_validator("from_node_id", "to_node_id", "route_role")
    @classmethod
    def validate_text(cls, value: str, info: object) -> str:
        return _canonical_text(value, getattr(info, "field_name", "gap identifier"))

    @field_validator(
        "unsatisfied_network_place_ids",
        "unsatisfied_access_obligation_ids",
        "unsatisfied_strategic_destination_ids",
        "excluded_edge_binding_ids",
    )
    @classmethod
    def validate_memberships(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        return _canonical_ids(value, getattr(info, "field_name", "gap identifiers"))

    @model_validator(mode="after")
    def bind_gap(self) -> Self:
        if self.from_node_id == self.to_node_id:
            raise ValueError("a route-control Network Gap requires distinct endpoints")
        payload = self.model_dump(
            mode="json",
            exclude={"gap_id", "gap_fingerprint"},
        )
        fingerprint = content_fingerprint(payload)
        identifier = _stable_id("network-gap", payload)
        if self.gap_id and self.gap_id != identifier:
            raise ValueError("route-control Network Gap identity is stale")
        if self.gap_fingerprint and self.gap_fingerprint != fingerprint:
            raise ValueError("route-control Network Gap fingerprint is stale")
        object.__setattr__(self, "gap_id", identifier)
        object.__setattr__(self, "gap_fingerprint", fingerprint)
        return self
