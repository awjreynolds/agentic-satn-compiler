"""Private, deterministic replay of an adopted strategic Reference.

This module has no publication, runtime, authentication, signing, or trust-root
authority.  It accepts only a plan that has already bound the exact adopted
preparation and fails closed if current preparation or graph facts drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal, Self

import geopandas as gpd
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely.geometry import LineString
from shapely.ops import linemerge, unary_union

from satn.alignment_selection import CanonicalLineString
from satn.content_identity import canonical_json as _canonical_json
from satn.content_identity import content_fingerprint as _fingerprint
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

LINEAGE_COLUMNS = [
    "unit_id",
    "source_unit_fingerprint",
    "candidate_set_id",
    "candidate_set_fingerprint",
    "resolution_fingerprint",
    "candidate_record_fingerprint",
    "plan_fingerprint",
    "preparation_fingerprint",
    "profile_fingerprint",
    "scenario_fingerprint",
    "reference_selection_fingerprint",
    "reference_decision_fingerprint",
    "area_fingerprint",
    "evidence_snapshot_fingerprint",
    "selection_run_fingerprint",
]

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
    *LINEAGE_COLUMNS,
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
    *LINEAGE_COLUMNS,
    "geometry",
]


class ServedNetworkPlaceObligation(BaseModel):
    """Typed compiler obligation satisfied by an interurban Reference spine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    obligation_id: str = Field(min_length=1)
    network_place_id: str = Field(min_length=1)
    binding_ids: tuple[str, ...] = Field(min_length=1)
    strategic_connection_ids: tuple[str, ...] = Field(min_length=1)
    physical_alignment_ids: tuple[str, ...] = Field(min_length=1)
    binding_lineages: tuple[str, ...] = Field(min_length=1)
    service_status: Literal["served"] = "served"
    network_role: Literal["interurban-network-place-obligation"] = (
        "interurban-network-place-obligation"
    )

    @model_validator(mode="after")
    def canonical_memberships(self) -> Self:
        for field in (
            "binding_ids",
            "strategic_connection_ids",
            "physical_alignment_ids",
            "binding_lineages",
        ):
            values = getattr(self, field)
            if values != tuple(sorted(set(values))):
                raise ValueError(
                    f"served Network Place {field} must be canonical"
                )
        if not (
            len(self.binding_ids)
            == len(self.strategic_connection_ids)
            == len(self.binding_lineages)
        ):
            raise ValueError(
                "served Network Place logical memberships are incomplete"
            )
        return self


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
    interurban_candidate_geometries = _interurban_candidate_geometries(plan)
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
    obligation_memberships: dict[str, list[dict[str, str]]] = {}
    consumed: list[str] = []
    replay_geometries: list[tuple[StrategicReferenceCandidateBinding, LineString]] = []

    for binding in bindings:
        binding_id = binding.binding_fingerprint
        geometry, reverse_geometry = _materialise_binding_geometry(binding, graph)
        canonical = _canonical_projected_geometry(geometry, graph.crs)
        canonical_reverse = _canonical_projected_geometry(reverse_geometry, graph.crs)
        if (
            canonical != binding.geometry
            or canonical_reverse != binding.geometry
            or canonical.fingerprint != binding.geometry_fingerprint
            or canonical_reverse.fingerprint != binding.geometry_fingerprint
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

        lineage = _binding_lineage(plan, binding)
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
            **lineage,
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
                obligation_memberships.setdefault(place_id, []).append(
                    {
                        "binding_id": binding_id,
                        "strategic_connection_id": str(
                            common["strategic_connection_id"]
                        ),
                        "physical_alignment_id": binding.physical_alignment_id,
                        "binding_lineage": _canonical_json(lineage),
                    }
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
    obligations = _aggregate_served_obligations(obligation_memberships)
    obligation_place_ids = tuple(item.network_place_id for item in obligations)

    crs = strategic_spines.crs or communities.crs or graph.crs
    effective = _effective_spines(
        strategic_spines,
        replay_geometries,
        interurban_candidate_geometries,
        plan,
        crs,
    )
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
        served_network_place_obligations=obligations,
        served_endpoint_place_ids=obligation_place_ids,
        consumed_binding_ids=tuple(sorted(consumed)),
        diagnostics=diagnostics,
    )


def _aggregate_served_obligations(
    obligation_memberships: dict[str, list[dict[str, str]]],
) -> tuple[ServedNetworkPlaceObligation, ...]:
    obligations = []
    for place_id, memberships in sorted(obligation_memberships.items()):
        binding_ids = tuple(sorted(item["binding_id"] for item in memberships))
        connection_ids = tuple(
            sorted(item["strategic_connection_id"] for item in memberships)
        )
        lineages = tuple(sorted(item["binding_lineage"] for item in memberships))
        if (
            len(set(binding_ids)) != len(binding_ids)
            or len(set(connection_ids)) != len(connection_ids)
            or len(set(lineages)) != len(lineages)
        ):
            raise ValueError(
                "strategic replay duplicates one logical membership at a shared hub"
            )
        obligations.append(
            ServedNetworkPlaceObligation(
                obligation_id=stable_id(
                    "interurban-network-place-obligation",
                    place_id,
                ),
                network_place_id=place_id,
                binding_ids=binding_ids,
                strategic_connection_ids=connection_ids,
                physical_alignment_ids=tuple(
                    sorted(
                        {
                            item["physical_alignment_id"]
                            for item in memberships
                        }
                    )
                ),
                binding_lineages=lineages,
            )
        )
    return tuple(obligations)


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
            "binding_ids": list(item.binding_ids),
            "strategic_connection_ids": list(item.strategic_connection_ids),
            "physical_alignment_ids": list(item.physical_alignment_ids),
            "binding_lineages": [
                json.loads(value) for value in item.binding_lineages
            ],
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
                "root_spine_id": None,
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
) -> tuple[LineString, LineString]:
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
    forward = _chain_geometry(graph, forward_nodes, binding.unit_id)
    reverse = _chain_geometry(graph, reverse_nodes, binding.unit_id)
    return forward, LineString(list(reverse.coords)[::-1])


def _chain_geometry(
    graph: RoadGraph,
    nodes: tuple[str, ...],
    unit_id: str,
) -> LineString:
    lines = [graph.graph[left][right]["geometry"] for left, right in pairwise(nodes)]
    unioned = unary_union(lines)
    merged = unioned if isinstance(unioned, LineString) else linemerge(unioned)
    if not isinstance(merged, LineString) or merged.is_empty:
        raise ValueError(f"strategic replay graph route is not one LineString: {unit_id}")
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
    interurban_candidate_geometries: tuple[CanonicalLineString, ...],
    plan: StrategicReferenceApplicationPlan,
    crs: object,
) -> gpd.GeoDataFrame:
    result = strategic_spines.copy()
    retained = []
    for _, row in result.iterrows():
        projected = _canonical_projected_geometry(row.geometry, crs)
        retained.append(
            not any(
                CanonicalLineString(
                    coordinates=projected.coordinates,
                    equivalence_profile=candidate.equivalence_profile,
                ).materially_equivalent(candidate)
                for candidate in interurban_candidate_geometries
            )
        )
    result = result.loc[retained].copy()
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
            provenance = json.loads(str(addition["provenance"]))
            provenance["binding_lineages"] = sorted(
                [
                    *provenance["binding_lineages"],
                    _binding_lineage(plan, binding),
                ],
                key=lambda item: str(item["unit_id"]),
            )
            addition["provenance"] = _canonical_json(provenance)
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
                        "binding_lineages": [
                            _binding_lineage(plan, binding)
                        ],
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


def _binding_lineage(
    plan: StrategicReferenceApplicationPlan,
    binding: StrategicReferenceCandidateBinding,
) -> dict[str, object]:
    return {
        "binding_id": binding.binding_fingerprint,
        "unit_id": binding.unit_id,
        "source_unit_fingerprint": binding.source_unit_fingerprint,
        "candidate_set_id": binding.candidate_set_id,
        "candidate_set_fingerprint": binding.candidate_set_fingerprint,
        "resolution_fingerprint": binding.resolution_fingerprint,
        "candidate_record_fingerprint": binding.candidate_record_fingerprint,
        "selected_candidate_id": binding.selected_candidate_id,
        "physical_alignment_id": binding.physical_alignment_id,
        "geometry_fingerprint": binding.geometry_fingerprint,
        "source_ids": list(binding.source_ids),
        "evidence_ids": list(binding.evidence_ids),
        "generation_strategies": list(binding.generation_strategies),
        "plan_fingerprint": plan.plan_fingerprint,
        "preparation_fingerprint": plan.preparation_fingerprint,
        "profile_fingerprint": plan.profile_fingerprint,
        "scenario_fingerprint": plan.scenario_fingerprint,
        "reference_selection_fingerprint": (
            plan.reference_selection_fingerprint
        ),
        "reference_decision_fingerprint": plan.reference_decision_fingerprint,
        "area_fingerprint": plan.area_fingerprint,
        "evidence_snapshot_fingerprint": plan.evidence_snapshot_fingerprint,
        "selection_run_fingerprint": plan.selection_run_fingerprint,
    }


def _interurban_candidate_geometries(
    plan: StrategicReferenceApplicationPlan,
) -> tuple[CanonicalLineString, ...]:
    source = json.loads(plan.source_preparation_json)
    canonical = source.get("canonical_payload")
    if not isinstance(canonical, dict):
        raise ValueError("strategic replay canonical preparation is malformed")
    units = canonical.get("units")
    registry = canonical.get("physical_alignments")
    if not isinstance(units, list) or not isinstance(registry, list):
        raise ValueError("strategic replay substitution registry is malformed")
    geometry_by_physical_id: dict[str, CanonicalLineString] = {}
    for item in registry:
        if not isinstance(item, dict):
            raise ValueError("strategic replay physical alignment is malformed")
        identifier = item.get("physical_alignment_id")
        geometry = item.get("geometry")
        if (
            not isinstance(identifier, str)
            or identifier in geometry_by_physical_id
            or not isinstance(geometry, dict)
        ):
            raise ValueError("strategic replay physical alignment identity collides")
        geometry_by_physical_id[identifier] = CanonicalLineString.model_validate(
            geometry
        )
    expected_units = {
        binding.unit_id
        for binding in plan.bindings
        if binding.unit_role is StrategicCorridorUnitRole.INTERURBAN_SPINE
    }
    seen_units: set[str] = set()
    geometries: dict[str, CanonicalLineString] = {}
    for unit in units:
        if (
            not isinstance(unit, dict)
            or unit.get("unit_role")
            != StrategicCorridorUnitRole.INTERURBAN_SPINE.value
        ):
            continue
        unit_id = unit.get("unit_id")
        if not isinstance(unit_id, str) or unit_id not in expected_units:
            raise ValueError("strategic replay contains a foreign interurban unit")
        if unit_id in seen_units:
            raise ValueError("strategic replay duplicates an interurban unit")
        seen_units.add(unit_id)
        records = unit.get("candidate_records")
        if not isinstance(records, list) or not records:
            raise ValueError("strategic replay interurban unit has no candidates")
        for record in records:
            physical_id = (
                record.get("physical_alignment_id")
                if isinstance(record, dict)
                else None
            )
            geometry = geometry_by_physical_id.get(str(physical_id))
            if not isinstance(physical_id, str) or geometry is None:
                raise ValueError(
                    "strategic replay interurban candidate lacks registry geometry"
                )
            geometries[geometry.fingerprint] = geometry
    if seen_units != expected_units:
        raise ValueError("strategic replay omits an interurban substitution unit")
    return tuple(geometries[key] for key in sorted(geometries))


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
