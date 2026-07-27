"""Private, deterministic replay of an adopted strategic Reference.

This module has no publication, runtime, authentication, signing, or trust-root
authority.  It accepts only a plan that has already bound the exact adopted
preparation and fails closed if current preparation or graph facts drift.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

import geopandas as gpd
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import LineString
from shapely.ops import linemerge, unary_union

from satn.alignment_selection import CanonicalLineString
from satn.identifiers import stable_id
from satn.models import ACCESS_OBLIGATION_COLUMNS, NetworkScope
from satn.routing import RoadGraph
from satn.strategic_corridors import (
    StrategicCorridorPreparationResult,
    StrategicCorridorUnitRole,
)
from satn.strategic_reference_application import (
    StrategicReferenceApplicationPlan,
    StrategicReferenceCandidateBinding,
)

INTERURBAN_COLUMNS = [
    "strategic_connection_id",
    "binding_id",
    "candidate_id",
    "physical_alignment_id",
    "network_role",
    "from_network_place_id",
    "to_network_place_id",
    "routing_start_node_id",
    "routing_end_node_id",
    "routing_edge_ids",
    "reverse_routing_edge_ids",
    "source_ids",
    "evidence_ids",
    "generation_strategies",
    "geometry_fingerprint",
    "geometry",
]

DESTINATION_ACCESS_COLUMNS = [
    "strategic_connection_id",
    "binding_id",
    "candidate_id",
    "physical_alignment_id",
    "network_role",
    "from_network_place_id",
    "strategic_destination_id",
    "routing_start_node_id",
    "routing_end_node_id",
    "routing_edge_ids",
    "reverse_routing_edge_ids",
    "source_ids",
    "evidence_ids",
    "generation_strategies",
    "geometry_fingerprint",
    "geometry",
]


class ServedNetworkPlaceObligation(BaseModel):
    """Typed compiler obligation satisfied by an interurban Reference spine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    obligation_id: str = Field(min_length=1)
    network_place_id: str = Field(min_length=1)
    binding_id: str = Field(min_length=1)
    strategic_connection_id: str = Field(min_length=1)
    physical_alignment_id: str = Field(min_length=1)
    service_status: Literal["served"] = "served"
    network_role: Literal["interurban-network-place-obligation"] = (
        "interurban-network-place-obligation"
    )


@dataclass(frozen=True)
class ValidatedStrategicReferenceReplay:
    """A deeply revalidated plan proven fresh against current preparation."""

    plan: StrategicReferenceApplicationPlan
    current_preparation_fingerprint: str


@dataclass(frozen=True)
class StrategicReferenceReplayMaterialisation:
    """Compiler-ready replay products with no raw plan or publication authority."""

    effective_strategic_spines: gpd.GeoDataFrame
    interurban_connections: gpd.GeoDataFrame
    destination_access_connections: gpd.GeoDataFrame
    served_network_place_obligations: tuple[ServedNetworkPlaceObligation, ...]
    served_endpoint_place_ids: tuple[str, ...]
    consumed_binding_ids: tuple[str, ...]
    diagnostics: dict[str, object]


def validate_fresh_replay(
    plan: StrategicReferenceApplicationPlan,
    current_preparation: StrategicCorridorPreparationResult,
) -> ValidatedStrategicReferenceReplay:
    """Deep-validate the plan and exact canonical current preparation equality."""

    validated_plan = StrategicReferenceApplicationPlan.model_validate(
        plan.model_dump(mode="python")
    )
    canonical = current_preparation.canonical_payload()
    fingerprint = _fingerprint(canonical)
    if (
        current_preparation.contract != "satn-strategic-corridor-preparation/v1"
        or current_preparation.status != "prepared"
        or current_preparation.missing_inputs
        or current_preparation.issues
        or current_preparation.preparation_fingerprint != fingerprint
    ):
        raise ValueError("current strategic preparation is incomplete or stale")
    current_payload = {
        "canonical_payload": canonical,
        "preparation_fingerprint": current_preparation.preparation_fingerprint,
    }
    if (
        validated_plan.source_preparation_json != _canonical_json(current_payload)
        or validated_plan.preparation_fingerprint
        != current_preparation.preparation_fingerprint
    ):
        raise ValueError(
            "current strategic preparation does not exactly match the adopted plan"
        )
    return ValidatedStrategicReferenceReplay(
        plan=validated_plan,
        current_preparation_fingerprint=current_preparation.preparation_fingerprint,
    )


def materialise_replay(
    validated: ValidatedStrategicReferenceReplay,
    strategic_spines: gpd.GeoDataFrame,
    communities: gpd.GeoDataFrame,
    graph: RoadGraph,
) -> StrategicReferenceReplayMaterialisation:
    """Materialise every exact binding once, rejecting all ambiguous graph state."""

    plan = StrategicReferenceApplicationPlan.model_validate(
        validated.plan.model_dump(mode="python")
    )
    if plan.preparation_fingerprint != validated.current_preparation_fingerprint:
        raise ValueError("validated strategic replay identity is stale")
    bindings = plan.bindings
    binding_ids = tuple(binding.binding_fingerprint for binding in bindings)
    if len(set(binding_ids)) != len(binding_ids):
        raise ValueError("strategic replay contains duplicate binding identities")

    place_ids = tuple(str(value) for value in communities.get("place_id", ()))
    if len(set(place_ids)) != len(place_ids):
        raise ValueError("current communities contain duplicate Network Place identities")
    places_by_id = {
        str(row["place_id"]): row for _, row in communities.iterrows()
    }

    geometry_by_physical_id: dict[str, str] = {}
    physical_id_by_geometry: dict[str, str] = {}
    interurban_rows: list[dict[str, object]] = []
    destination_rows: list[dict[str, object]] = []
    obligations: list[ServedNetworkPlaceObligation] = []
    consumed: list[str] = []
    replay_geometries: list[tuple[StrategicReferenceCandidateBinding, LineString]] = []

    for binding in bindings:
        binding_id = binding.binding_fingerprint
        geometry = _materialise_binding_geometry(binding, graph)
        canonical = _canonical_projected_geometry(geometry, graph.crs)
        if (
            canonical != binding.geometry
            or canonical.fingerprint != binding.geometry_fingerprint
            or binding.geometry != binding.registry_geometry
            or binding.geometry_fingerprint != binding.registry_geometry_fingerprint
        ):
            raise ValueError(
                f"strategic replay graph geometry disagrees with {binding.unit_id}"
            )
        prior_geometry = geometry_by_physical_id.setdefault(
            binding.physical_alignment_id,
            binding.geometry_fingerprint,
        )
        prior_physical = physical_id_by_geometry.setdefault(
            binding.geometry_fingerprint,
            binding.physical_alignment_id,
        )
        if (
            prior_geometry != binding.geometry_fingerprint
            or prior_physical != binding.physical_alignment_id
        ):
            raise ValueError("strategic replay physical alignment identities collide")

        common = {
            "strategic_connection_id": stable_id(
                "strategic-reference-connection", binding_id
            ),
            "binding_id": binding_id,
            "candidate_id": binding.selected_candidate_id,
            "physical_alignment_id": binding.physical_alignment_id,
            "network_role": binding.unit_role.value,
            "routing_start_node_id": binding.routing_start_node_id,
            "routing_end_node_id": binding.routing_end_node_id,
            "routing_edge_ids": json.dumps(list(binding.routing_edge_ids)),
            "reverse_routing_edge_ids": json.dumps(
                list(binding.reverse_routing_edge_ids)
            ),
            "source_ids": json.dumps(list(binding.source_ids)),
            "evidence_ids": json.dumps(list(binding.evidence_ids)),
            "generation_strategies": json.dumps(list(binding.generation_strategies)),
            "geometry_fingerprint": binding.geometry_fingerprint,
            "geometry": geometry,
        }
        endpoints = binding.endpoint_binding
        if binding.unit_role is StrategicCorridorUnitRole.INTERURBAN_SPINE:
            if any(place_id not in places_by_id for place_id in endpoints.network_place_ids):
                raise ValueError("strategic replay contains a foreign Network Place endpoint")
            left, right = endpoints.network_place_ids
            interurban_rows.append(
                common
                | {
                    "from_network_place_id": left,
                    "to_network_place_id": right,
                }
            )
            for place_id in endpoints.network_place_ids:
                obligations.append(
                    ServedNetworkPlaceObligation(
                        obligation_id=stable_id(
                            "interurban-network-place-obligation",
                            binding_id,
                            place_id,
                        ),
                        network_place_id=place_id,
                        binding_id=binding_id,
                        strategic_connection_id=str(
                            common["strategic_connection_id"]
                        ),
                        physical_alignment_id=binding.physical_alignment_id,
                    )
                )
            replay_geometries.append((binding, geometry))
        else:
            if (
                len(endpoints.network_place_ids) != 1
                or endpoints.network_place_ids[0] not in places_by_id
                or len(endpoints.strategic_destination_ids) != 1
            ):
                raise ValueError(
                    "strategic destination replay contains foreign endpoint identity"
                )
            destination_rows.append(
                common
                | {
                    "from_network_place_id": endpoints.network_place_ids[0],
                    "strategic_destination_id": (
                        endpoints.strategic_destination_ids[0]
                    ),
                }
            )
        consumed.append(binding_id)

    expected = set(binding_ids)
    actual = set(consumed)
    if len(consumed) != len(bindings) or actual != expected:
        raise ValueError(
            "strategic replay did not consume every binding exactly once "
            f"(missing={sorted(expected - actual)}, foreign={sorted(actual - expected)})"
        )
    obligation_place_ids = tuple(
        sorted(item.network_place_id for item in obligations)
    )
    if len(set(obligation_place_ids)) != len(obligation_place_ids):
        raise ValueError("strategic replay serves one Network Place more than once")

    crs = strategic_spines.crs or communities.crs or graph.crs
    effective = _effective_spines(strategic_spines, replay_geometries, crs)
    interurban = gpd.GeoDataFrame(
        interurban_rows,
        columns=INTERURBAN_COLUMNS,
        geometry="geometry",
        crs=crs,
    ).sort_values("binding_id")
    destination = gpd.GeoDataFrame(
        destination_rows,
        columns=DESTINATION_ACCESS_COLUMNS,
        geometry="geometry",
        crs=crs,
    ).sort_values("binding_id")
    diagnostics = {
        "contract": "satn-strategic-reference-replay/v1",
        "status": "materialised",
        "plan_fingerprint": plan.plan_fingerprint,
        "preparation_fingerprint": validated.current_preparation_fingerprint,
        "expected_binding_count": len(bindings),
        "consumed_binding_count": len(consumed),
        "consumed_binding_ids": sorted(consumed),
        "interurban_connection_count": len(interurban),
        "destination_access_connection_count": len(destination),
        "served_network_place_count": len(obligations),
        "physical_alignment_count": len(geometry_by_physical_id),
        "publication_created": False,
        "agent_runtime_invoked": False,
    }
    return StrategicReferenceReplayMaterialisation(
        effective_strategic_spines=effective,
        interurban_connections=interurban,
        destination_access_connections=destination,
        served_network_place_obligations=tuple(
            sorted(obligations, key=lambda item: item.network_place_id)
        ),
        served_endpoint_place_ids=obligation_place_ids,
        consumed_binding_ids=tuple(sorted(consumed)),
        diagnostics=diagnostics,
    )


def served_obligations_frame(
    materialisation: StrategicReferenceReplayMaterialisation,
    communities: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Convert typed replay obligations to compiler-authored obligation rows."""

    places = {
        str(row["place_id"]): row for _, row in communities.iterrows()
    }
    rows = []
    for item in materialisation.served_network_place_obligations:
        place = places.get(item.network_place_id)
        if place is None:
            raise ValueError("served strategic obligation has no current Network Place")
        provenance = {
            "binding_id": item.binding_id,
            "strategic_connection_id": item.strategic_connection_id,
            "physical_alignment_id": item.physical_alignment_id,
            "service_status": item.service_status,
        }
        rows.append(
            {
                "obligation_id": item.obligation_id,
                "obligation_kind": "community",
                "place_id": item.network_place_id,
                "community_id": item.network_place_id,
                "school_id": None,
                "school_kind": None,
                "name": place.get("name"),
                "network_role": item.network_role,
                "network_scope": NetworkScope.RURAL.value,
                "service_status": item.service_status,
                "service_rationale": (
                    "Network Place is an endpoint of the adopted interurban "
                    "strategic Reference alignment."
                ),
                "access_connection_id": None,
                "root_spine_id": item.physical_alignment_id,
                "branch_id": None,
                "criterion_continuity": "green",
                "geometry_semantics": "network-place-reference-point",
                "provenance": json.dumps(provenance, sort_keys=True),
                "geometry": place.geometry,
            }
        )
    return gpd.GeoDataFrame(
        rows,
        columns=ACCESS_OBLIGATION_COLUMNS,
        geometry="geometry",
        crs=communities.crs,
    )


def empty_interurban_connections() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        columns=INTERURBAN_COLUMNS,
        geometry="geometry",
    )


def empty_destination_access_connections() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        columns=DESTINATION_ACCESS_COLUMNS,
        geometry="geometry",
    )


def _materialise_binding_geometry(
    binding: StrategicReferenceCandidateBinding,
    graph: RoadGraph,
) -> LineString:
    forward_nodes = _unique_edge_chain(
        graph,
        binding.routing_start_node_id,
        binding.routing_end_node_id,
        binding.routing_edge_ids,
    )
    reverse_nodes = _unique_edge_chain(
        graph,
        binding.routing_end_node_id,
        binding.routing_start_node_id,
        binding.reverse_routing_edge_ids,
    )
    if tuple(reversed(reverse_nodes)) != forward_nodes:
        # Reverse routing may use distinct reciprocal edges, but must visit the
        # same ordered physical node chain.
        raise ValueError(f"strategic replay reverse edge chain drifts for {binding.unit_id}")
    lines = [
        graph.graph[left][right]["geometry"]
        for left, right in pairwise(forward_nodes)
    ]
    unioned = unary_union(lines)
    merged = unioned if isinstance(unioned, LineString) else linemerge(unioned)
    if not isinstance(merged, LineString) or merged.is_empty:
        raise ValueError(f"strategic replay graph route is not one LineString: {binding.unit_id}")
    return merged


def _unique_edge_chain(
    graph: RoadGraph,
    start: str,
    end: str,
    edge_ids: tuple[str, ...],
) -> tuple[str, ...]:
    if start not in graph.graph or end not in graph.graph or not edge_ids:
        raise ValueError("strategic replay route endpoint or edge chain is missing")
    solutions: list[tuple[str, ...]] = []

    def walk(node: str, offset: int, nodes: tuple[str, ...]) -> None:
        if len(solutions) > 1:
            return
        if offset == len(edge_ids):
            if node == end:
                solutions.append(nodes)
            return
        expected = edge_ids[offset]
        matches = sorted(
            str(target)
            for _, target, attrs in graph.graph.out_edges(node, data=True)
            if str(attrs.get("edge_id")) == expected
        )
        for target in matches:
            walk(target, offset + 1, (*nodes, target))

    walk(start, 0, (start,))
    if len(solutions) != 1:
        raise ValueError(
            "strategic replay requires one unique exact graph edge chain "
            f"(found={len(solutions)})"
        )
    return solutions[0]


def _effective_spines(
    strategic_spines: gpd.GeoDataFrame,
    replay_geometries: list[tuple[StrategicReferenceCandidateBinding, LineString]],
    crs: object,
) -> gpd.GeoDataFrame:
    result = strategic_spines.copy()
    for column in (
        "physical_alignment_id",
        "logical_membership_ids",
        "replay_binding_ids",
    ):
        if column not in result:
            result[column] = None
    existing_by_geometry: dict[str, tuple[str, object]] = {}
    for index, row in result.iterrows():
        canonical = _canonical_projected_geometry(row.geometry, crs)
        if canonical.fingerprint in existing_by_geometry:
            raise ValueError("effective strategic spine registry duplicates exact geometry")
        existing_by_geometry[canonical.fingerprint] = ("existing", index)

    additions: list[dict[str, object]] = []
    for binding, geometry in replay_geometries:
        fingerprint = binding.geometry_fingerprint
        membership = binding.unit_id
        location = existing_by_geometry.get(fingerprint)
        if location is not None and location[0] == "existing":
            index = location[1]
            prior_physical = result.at[index, "physical_alignment_id"]
            if pd.notna(prior_physical) and str(prior_physical) != binding.physical_alignment_id:
                raise ValueError("effective strategic spine physical identity collides")
            result.at[index, "physical_alignment_id"] = binding.physical_alignment_id
            result.at[index, "logical_membership_ids"] = _append_json_id(
                result.at[index, "logical_membership_ids"],
                membership,
            )
            result.at[index, "replay_binding_ids"] = _append_json_id(
                result.at[index, "replay_binding_ids"],
                binding.binding_fingerprint,
            )
            continue
        if location is not None:
            addition = additions[int(location[1])]
            if addition["physical_alignment_id"] != binding.physical_alignment_id:
                raise ValueError("effective strategic spine physical identity collides")
            addition["logical_membership_ids"] = _append_json_id(
                addition["logical_membership_ids"],
                membership,
            )
            addition["replay_binding_ids"] = _append_json_id(
                addition["replay_binding_ids"],
                binding.binding_fingerprint,
            )
            continue
        additions.append(
            {
                "spine_id": stable_id(
                    "strategic-reference-spine", binding.physical_alignment_id
                ),
                "network_role": "strategic-spine",
                "spine_kind": "selected-interurban-reference",
                "name": f"Adopted interurban alignment {binding.selected_candidate_id}",
                "category": "strategic-reference",
                "evidence_id": stable_id(
                    "strategic-reference-evidence", *binding.evidence_ids
                ),
                "source_id": stable_id(
                    "strategic-reference-sources", *binding.source_ids
                ),
                "network_scope": NetworkScope.RURAL.value,
                "intervention_assumption": (
                    "Adopted strategic alignment; detailed design remains required"
                ),
                "design_status": "adopted strategic Reference; not a final design",
                "provenance": json.dumps(
                    {
                        "binding_id": binding.binding_fingerprint,
                        "candidate_id": binding.selected_candidate_id,
                        "source_ids": list(binding.source_ids),
                        "evidence_ids": list(binding.evidence_ids),
                    },
                    sort_keys=True,
                ),
                "physical_alignment_id": binding.physical_alignment_id,
                "logical_membership_ids": json.dumps([membership]),
                "replay_binding_ids": json.dumps([binding.binding_fingerprint]),
                "geometry": geometry,
            }
        )
        existing_by_geometry[fingerprint] = ("addition", len(additions) - 1)
    if additions:
        result = gpd.GeoDataFrame(
            pd.concat(
                [
                    result,
                    gpd.GeoDataFrame(additions, geometry="geometry", crs=crs),
                ],
                ignore_index=True,
                sort=False,
            ),
            geometry="geometry",
            crs=crs,
        )
    return result.sort_values("spine_id").reset_index(drop=True)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _append_json_id(value: object, identifier: str) -> str:
    existing = (
        json.loads(str(value))
        if value is not None and not pd.isna(value)
        else []
    )
    if not isinstance(existing, list) or any(not isinstance(item, str) for item in existing):
        raise ValueError("effective strategic spine logical memberships are malformed")
    return json.dumps(sorted({*existing, identifier}))


def _canonical_projected_geometry(
    geometry: LineString,
    crs: object,
) -> CanonicalLineString:
    projected = gpd.GeoSeries([geometry], crs=crs).to_crs(27700).iloc[0]
    return CanonicalLineString(
        coordinates=tuple((float(x), float(y)) for x, y in projected.coords)
    )
