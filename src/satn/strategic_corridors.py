"""Prepare finite strategic-corridor units from governed compiler inputs.

This is deliberately a sibling of ``spine_access_candidate_preparation``.
Direct Community-to-Strategic-Spine attachments remain Spine Access work and
are not promoted.  Instead, their exact current graph anchors are the bounded
input for an interurban comparison unit.  A separately admitted Strategic
Education Destination may create a destination-access unit only when its
current governed site/access-point geometry can be joined to the current Road
Graph.  No names, free-form caller observations, safety, feasibility, cost,
deliverability, or independent-travel conclusions are inferred here.

SHA-256 values are reproducible local content identities for stale-input and
lineage checks.  They are neither credentials nor trust claims.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point

from satn.alignment_selection import (
    AlignmentCandidateInput,
    AlignmentCandidateSet,
    CanonicalLineString,
    CriterionState,
    NetworkRole,
    admit_candidate_set,
)
from satn.models import SourceConfig
from satn.network_selection import CandidateSourceClass, NetworkSelectionProfile
from satn.psa_evidence_loaders import (
    EducationAccessEvidenceLoad,
    load_education_access_evidence,
    load_population_reach_evidence,
)
from satn.routing import RoadGraph, RouteOption, choose_alignment

STRATEGIC_CORRIDOR_PREPARATION_CONTRACT = "satn-strategic-corridor-preparation/v1"
STRATEGIC_DESTINATION_GRAPH_BINDING_CONTRACT = (
    "satn-strategic-destination-graph-binding/v1"
)
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _stable_id(prefix: str, value: object) -> str:
    return f"{prefix}-{_fingerprint(value)[:20]}"


class StrategicCorridorUnitRole(StrEnum):
    """The two separately governed logical roles in this narrow seam."""

    INTERURBAN_SPINE = "interurban-spine"
    STRATEGIC_DESTINATION_ACCESS = "strategic-destination-access"

    @property
    def network_role(self) -> NetworkRole:
        return (
            NetworkRole.INTERURBAN_SPINE
            if self is StrategicCorridorUnitRole.INTERURBAN_SPINE
            else NetworkRole.STRATEGIC_DESTINATION_ACCESS
        )


@dataclass(frozen=True)
class StrategicCorridorIssue:
    """One explicit, non-inferred evidence gap in strategic preparation."""

    unit_role: StrategicCorridorUnitRole
    reason: str
    detail: str
    strategic_destination_id: str | None = None
    site_id: str | None = None

    def canonical(self) -> dict[str, object]:
        return {
            "unit_role": self.unit_role.value,
            "reason": self.reason,
            "detail": self.detail,
            "strategic_destination_id": self.strategic_destination_id,
            "site_id": self.site_id,
        }


@dataclass(frozen=True)
class PhysicalAlignment:
    """One authoritative geometry shared by one or more logical candidate roles."""

    physical_alignment_id: str
    geometry: CanonicalLineString
    geometry_fingerprint: str
    candidate_ids: tuple[str, ...]
    role_memberships: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.geometry_fingerprint != self.geometry.fingerprint:
            raise ValueError("physical alignment geometry fingerprint is stale")

    def canonical(self) -> dict[str, object]:
        return {
            "physical_alignment_id": self.physical_alignment_id,
            "geometry": self.geometry.model_dump(mode="json"),
            "geometry_fingerprint": self.geometry_fingerprint,
            "candidate_ids": list(self.candidate_ids),
            "role_memberships": list(self.role_memberships),
        }


@dataclass(frozen=True)
class StrategicCorridorEndpointBinding:
    """Typed endpoint identity, separate from candidate-set obligations.

    ``AlignmentCandidateSet`` requires two mechanical endpoints.  A Strategic
    Education Destination is not a Network Place, so a stable surrogate is
    used only for those mechanics.  For a destination-access unit the anchor
    Network Place remains routing and provenance identity in this binding; its
    only hard candidate-set obligation is the admitted destination.
    """

    candidate_endpoints: tuple[str, str]
    routing_node_ids: tuple[str, str]
    network_place_ids: tuple[str, ...]
    strategic_destination_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            len(self.candidate_endpoints) != 2
            or self.candidate_endpoints != tuple(sorted(set(self.candidate_endpoints)))
            or len(self.routing_node_ids) != 2
            or any(not item or item.strip() != item for item in self.routing_node_ids)
            or self.network_place_ids
            != tuple(sorted(set(self.network_place_ids)))
            or self.strategic_destination_ids
            != tuple(sorted(set(self.strategic_destination_ids)))
        ):
            raise ValueError("strategic corridor endpoint binding is not canonical")

    def canonical(self) -> dict[str, object]:
        return {
            "candidate_endpoints": list(self.candidate_endpoints),
            "routing_node_ids": list(self.routing_node_ids),
            "network_place_ids": list(self.network_place_ids),
            "strategic_destination_ids": list(self.strategic_destination_ids),
        }


@dataclass(frozen=True)
class StrategicCorridorCandidateRecord:
    """Exact graph and source facts for one generated candidate."""

    candidate: AlignmentCandidateInput
    physical_alignment_id: str
    routing_start_node_id: str
    routing_end_node_id: str
    routing_edge_ids: tuple[str, ...]
    reverse_routing_edge_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    generation_strategies: tuple[str, ...]
    generation_rationale: str

    def canonical(self) -> dict[str, object]:
        return {
            "candidate": self.candidate.model_dump(mode="json"),
            "physical_alignment_id": self.physical_alignment_id,
            "routing_start_node_id": self.routing_start_node_id,
            "routing_end_node_id": self.routing_end_node_id,
            "routing_edge_ids": list(self.routing_edge_ids),
            "reverse_routing_edge_ids": list(self.reverse_routing_edge_ids),
            "source_ids": list(self.source_ids),
            "evidence_ids": list(self.evidence_ids),
            "generation_strategies": list(self.generation_strategies),
            "generation_rationale": self.generation_rationale,
        }


@dataclass(frozen=True)
class PreparedStrategicCorridorUnit:
    """A finite, role-specific Alignment Candidate Set with no selection authority."""

    unit_id: str
    unit_role: StrategicCorridorUnitRole
    candidate_set: AlignmentCandidateSet
    endpoint_binding: StrategicCorridorEndpointBinding
    anchor_connection_ids: tuple[str, ...]
    anchor_obligation_ids: tuple[str, ...]
    routing_start_node_id: str
    routing_end_node_id: str
    strategic_destination_id: str | None
    site_id: str | None
    access_point_evidence_ids: tuple[str, ...]
    candidate_records: tuple[StrategicCorridorCandidateRecord, ...]

    def __post_init__(self) -> None:
        binding = self.endpoint_binding
        anchor_count = len(self.anchor_connection_ids) + len(
            self.anchor_obligation_ids
        )
        if (
            self.candidate_set.endpoints != binding.candidate_endpoints
            or self.candidate_set.mandatory_strategic_destination_ids
            != binding.strategic_destination_ids
            or (
                self.routing_start_node_id,
                self.routing_end_node_id,
            )
            != binding.routing_node_ids
            or self.anchor_connection_ids
            != tuple(sorted(set(self.anchor_connection_ids)))
            or self.anchor_obligation_ids
            != tuple(sorted(set(self.anchor_obligation_ids)))
        ):
            raise ValueError("strategic corridor unit endpoint binding is stale")
        if self.unit_role is StrategicCorridorUnitRole.INTERURBAN_SPINE:
            if (
                anchor_count != 2
                or len(binding.network_place_ids) != 2
                or binding.strategic_destination_ids
                or self.strategic_destination_id is not None
                or self.candidate_set.mandatory_network_place_ids
                != binding.network_place_ids
                or any(
                    candidate.served_network_place_ids != binding.network_place_ids
                    or candidate.served_strategic_destination_ids
                    for candidate in self.candidate_set.candidates
                )
            ):
                raise ValueError("interurban unit requires exactly two Network Places")
        elif (
            anchor_count != 1
            or len(binding.network_place_ids) != 1
            or len(binding.strategic_destination_ids) != 1
            or (self.strategic_destination_id,)
            != binding.strategic_destination_ids
            or self.strategic_destination_id in binding.candidate_endpoints
            or self.candidate_set.mandatory_network_place_ids
            or any(
                candidate.served_network_place_ids
                or candidate.served_strategic_destination_ids
                != binding.strategic_destination_ids
                for candidate in self.candidate_set.candidates
            )
        ):
            raise ValueError(
                "destination unit requires anchor identity and one typed destination obligation"
            )

    def canonical(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "unit_role": self.unit_role.value,
            "candidate_set": self.candidate_set.model_dump(mode="json"),
            "endpoint_binding": self.endpoint_binding.canonical(),
            "anchor_connection_ids": list(self.anchor_connection_ids),
            "anchor_obligation_ids": list(self.anchor_obligation_ids),
            "routing_start_node_id": self.routing_start_node_id,
            "routing_end_node_id": self.routing_end_node_id,
            "strategic_destination_id": self.strategic_destination_id,
            "site_id": self.site_id,
            "access_point_evidence_ids": list(self.access_point_evidence_ids),
            "candidate_records": [item.canonical() for item in self.candidate_records],
        }


@dataclass(frozen=True)
class StrategicCorridorPreparationResult:
    """Immutable non-mutating output for the strategic-corridor sibling seam."""

    contract: str
    profile_fingerprint: str
    status: str
    units: tuple[PreparedStrategicCorridorUnit, ...]
    physical_alignments: tuple[PhysicalAlignment, ...]
    issues: tuple[StrategicCorridorIssue, ...]
    missing_inputs: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    evidence_lineage: dict[str, object]
    preparation_fingerprint: str

    @property
    def prepared(self) -> bool:
        return self.status == "prepared"

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "profile_fingerprint": self.profile_fingerprint,
            "status": self.status,
            "units": [item.canonical() for item in self.units],
            "physical_alignments": [item.canonical() for item in self.physical_alignments],
            "issues": [item.canonical() for item in self.issues],
            "missing_inputs": list(self.missing_inputs),
            "evidence_fingerprints": list(self.evidence_fingerprints),
            "evidence_lineage": self.evidence_lineage,
        }

    def metadata(self) -> dict[str, object]:
        return {
            **self.canonical_payload(),
            "preparation_fingerprint": self.preparation_fingerprint,
            "unit_count": len(self.units),
            "candidate_count": sum(len(item.candidate_set.candidates) for item in self.units),
            "selection_performed": False,
            "network_geometry_mutated": False,
            "publication_performed": False,
        }


@dataclass(frozen=True)
class NetworkSelectionPreparationResult:
    """Private aggregate view retaining the truth of both preparation seams.

    It intentionally names neither corridor unit as an ``access_connection``:
    that existing identifier belongs only to Spine Access preparation.
    """

    spine_access_preparation: object | None
    strategic_corridor_preparation: StrategicCorridorPreparationResult | None

    @property
    def alignment_units(self) -> tuple[object, ...]:
        access = getattr(
            self.spine_access_preparation, "prepared_spine_access_connections", ()
        )
        strategic = (
            self.strategic_corridor_preparation.units
            if self.strategic_corridor_preparation is not None
            else ()
        )
        return (*access, *strategic)

    def metadata(self) -> dict[str, object]:
        return {
            "contract": "satn-network-selection-preparation-view/v1",
            "spine_access_preparation_fingerprint": getattr(
                self.spine_access_preparation, "preparation_fingerprint", None
            ),
            "strategic_corridor_preparation_fingerprint": (
                self.strategic_corridor_preparation.preparation_fingerprint
                if self.strategic_corridor_preparation is not None
                else None
            ),
            "alignment_unit_count": len(self.alignment_units),
        }


def prepare_strategic_corridors(
    profile: NetworkSelectionProfile,
    *,
    road_graph: RoadGraph,
    spine_access_connections: gpd.GeoDataFrame,
    access_obligations: gpd.GeoDataFrame | None = None,
    context: gpd.GeoDataFrame,
    source_config: SourceConfig,
    config_directory: Path,
) -> StrategicCorridorPreparationResult:
    """Generate only finite, current, evidence-bound strategic units.

    The compiler owns all geometry and education-destination association.  In
    particular, a destination name in context is not enough: site identity,
    exact access evidence identifiers, current status, geometry and an exact
    RoadGraph attachment are all required before a destination route exists.
    """

    profile = NetworkSelectionProfile.model_validate(profile.model_dump(mode="json"))
    missing: list[str] = []
    population = source_config.population_reach_evidence
    if population is None:
        missing.append("population-reach-evidence")
        population_load = None
    else:
        population_load = load_population_reach_evidence(
            population,
            base_directory=config_directory,
            pwc_outside_tolerance_m=0,
        )
    schools = source_config.school_register_evidence
    if schools is None:
        missing.append("school-register-evidence")
        education = None
    else:
        education = load_education_access_evidence(
            schools,
            source_config.strategic_education_destination_admissions,
            base_directory=config_directory,
            as_at=source_config.network_selection_as_at,
            school_register_max_age_days=(
                source_config.network_selection_school_register_max_age_days
            ),
            strategic_admissions_max_age_days=(
                source_config.network_selection_strategic_admissions_max_age_days
            ),
        )
    anchors = _direct_spine_anchors(
        spine_access_connections,
        access_obligations=access_obligations,
    )
    units, issues = _interurban_units(profile, road_graph, anchors, context)
    destination_units, destination_issues = _destination_units(
        profile,
        road_graph,
        anchors,
        context,
        education,
    )
    units = tuple(sorted((*units, *destination_units), key=lambda item: item.unit_id))
    issues = tuple(sorted((*issues, *destination_issues), key=_issue_key))
    lineage = _evidence_lineage(population_load, education)
    fingerprints = tuple(sorted(_lineage_fingerprints(lineage)))
    # A missing/mismatched admitted destination is a hard preparation blocker:
    # the interurban unit stays inspectable, but no later selection can pretend
    # that the admitted destination was served.
    status = (
        "prepared"
        if not missing
        and not any(
            item.unit_role is StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS
            for item in issues
        )
        else "incomplete"
    )
    provisional = {
        "contract": STRATEGIC_CORRIDOR_PREPARATION_CONTRACT,
        "profile_fingerprint": profile.fingerprint,
        "status": status,
        "units": [item.canonical() for item in units],
        "physical_alignments": [
            item.canonical() for item in _physical_alignments(units)
        ],
        "issues": [item.canonical() for item in issues],
        "missing_inputs": sorted(set(missing)),
        "evidence_fingerprints": list(fingerprints),
        "evidence_lineage": lineage,
    }
    return StrategicCorridorPreparationResult(
        contract=STRATEGIC_CORRIDOR_PREPARATION_CONTRACT,
        profile_fingerprint=profile.fingerprint,
        status=status,
        units=units,
        physical_alignments=_physical_alignments(units),
        issues=issues,
        missing_inputs=tuple(sorted(set(missing))),
        evidence_fingerprints=fingerprints,
        evidence_lineage=lineage,
        preparation_fingerprint=_fingerprint(provisional),
    )


def _interurban_units(
    profile: NetworkSelectionProfile,
    graph: RoadGraph,
    anchors: tuple[dict[str, str], ...],
    context: gpd.GeoDataFrame,
) -> tuple[tuple[PreparedStrategicCorridorUnit, ...], tuple[StrategicCorridorIssue, ...]]:
    units: list[PreparedStrategicCorridorUnit] = []
    issues: list[StrategicCorridorIssue] = []
    for root_spine_id, grouped in _group_anchors(anchors):
        if len(grouped) < 2:
            continue
        for left, right in combinations(grouped, 2):
            start = left["routing_node"]
            end = right["routing_node"]
            if start == end:
                issues.append(
                    StrategicCorridorIssue(
                        StrategicCorridorUnitRole.INTERURBAN_SPINE,
                        "identical-direct-spine-anchor-nodes",
                        "direct-spine community anchors resolve to one RoadGraph node",
                    )
                )
                continue
            candidate_set, records = _candidate_set(
                profile,
                graph,
                unit_role=StrategicCorridorUnitRole.INTERURBAN_SPINE,
                endpoints=(left["place_id"], right["place_id"]),
                mandatory_network_place_ids=(left["place_id"], right["place_id"]),
                start_node=start,
                end_node=end,
                source_ids=tuple(sorted({left["source_id"], right["source_id"]})),
                evidence_ids=tuple(sorted({left["evidence_id"], right["evidence_id"]})),
                context=context,
                strategic_destination_id=None,
            )
            unit_id = _stable_id(
                "alignment-unit",
                {
                    "role": StrategicCorridorUnitRole.INTERURBAN_SPINE.value,
                    "root_spine_id": root_spine_id,
                    "anchors": [left["anchor_id"], right["anchor_id"]],
                    "candidate_set": candidate_set.candidate_set_fingerprint,
                },
            )
            units.append(
                PreparedStrategicCorridorUnit(
                    unit_id=unit_id,
                    unit_role=StrategicCorridorUnitRole.INTERURBAN_SPINE,
                    candidate_set=candidate_set,
                    endpoint_binding=StrategicCorridorEndpointBinding(
                        candidate_endpoints=candidate_set.endpoints,
                        routing_node_ids=(start, end),
                        network_place_ids=tuple(
                            sorted((left["place_id"], right["place_id"]))
                        ),
                        strategic_destination_ids=(),
                    ),
                    anchor_connection_ids=tuple(
                        sorted(
                            item["access_connection_id"]
                            for item in (left, right)
                            if item["access_connection_id"]
                        )
                    ),
                    anchor_obligation_ids=tuple(
                        sorted(
                            item["access_obligation_id"]
                            for item in (left, right)
                            if item["access_obligation_id"]
                        )
                    ),
                    routing_start_node_id=start,
                    routing_end_node_id=end,
                    strategic_destination_id=None,
                    site_id=None,
                    access_point_evidence_ids=(),
                    candidate_records=records,
                )
            )
    return tuple(units), tuple(issues)


def _destination_units(
    profile: NetworkSelectionProfile,
    graph: RoadGraph,
    anchors: tuple[dict[str, str], ...],
    context: gpd.GeoDataFrame,
    education: EducationAccessEvidenceLoad | None,
) -> tuple[tuple[PreparedStrategicCorridorUnit, ...], tuple[StrategicCorridorIssue, ...]]:
    if education is None or not education.strategic_admission_records:
        return (), ()
    units: list[PreparedStrategicCorridorUnit] = []
    issues: list[StrategicCorridorIssue] = []
    for admission in education.strategic_admission_records:
        destination_id = admission.strategic_destination_id
        site = _governed_destination_site(context, admission)
        if site is None:
            issues.append(
                StrategicCorridorIssue(
                    StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS,
                    "destination-site-or-access-geometry-missing-or-mismatched",
                    (
                        "admitted destination has no matching current governed "
                        "site/access-point geometry"
                    ),
                    strategic_destination_id=destination_id,
                    site_id=admission.site_id,
                )
            )
            continue
        destination_node = _current_graph_destination_node(graph, site)
        if destination_node is None:
            issues.append(
                StrategicCorridorIssue(
                    StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS,
                    "destination-access-geometry-not-exactly-bound-to-current-road-graph",
                    (
                        "governed destination access geometry, node, incident edges "
                        "or content identity do not exactly match the current RoadGraph"
                    ),
                    strategic_destination_id=destination_id,
                    site_id=admission.site_id,
                )
            )
            continue
        if not anchors:
            issues.append(
                StrategicCorridorIssue(
                    StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS,
                    "no-direct-spine-community-anchor-for-destination-access",
                    "no compiler-emitted direct-spine community anchor can form destination access",
                    strategic_destination_id=destination_id,
                    site_id=admission.site_id,
                )
            )
            continue
        anchor = _nearest_anchor(graph, site["geometry"], anchors)
        if anchor is None or anchor["routing_node"] == destination_node:
            issues.append(
                StrategicCorridorIssue(
                    StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS,
                    "destination-access-has-no-distinct-current-road-graph-route",
                    (
                        "governed destination access does not resolve to a distinct "
                        "current graph route"
                    ),
                    strategic_destination_id=destination_id,
                    site_id=admission.site_id,
                )
            )
            continue
        destination_endpoint = _stable_id(
            "destination-endpoint",
            {
                "strategic_destination_id": destination_id,
                "site_id": admission.site_id,
                "graph_node_id": destination_node,
                "access_point_evidence_ids": list(site["access_ids"]),
            },
        )
        candidate_set, records = _candidate_set(
            profile,
            graph,
            unit_role=StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS,
            endpoints=(anchor["place_id"], destination_endpoint),
            mandatory_network_place_ids=(),
            start_node=anchor["routing_node"],
            end_node=destination_node,
            source_ids=tuple(sorted({anchor["source_id"], site["source_id"]})),
            evidence_ids=tuple(sorted({anchor["evidence_id"], *site["access_ids"]})),
            context=context,
            strategic_destination_id=destination_id,
        )
        unit_id = _stable_id(
            "alignment-unit",
            {
                "role": StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS.value,
                "anchor": anchor["anchor_id"],
                "site_id": admission.site_id,
                "candidate_set": candidate_set.candidate_set_fingerprint,
            },
        )
        units.append(
            PreparedStrategicCorridorUnit(
                unit_id=unit_id,
                unit_role=StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS,
                candidate_set=candidate_set,
                endpoint_binding=StrategicCorridorEndpointBinding(
                    candidate_endpoints=candidate_set.endpoints,
                    routing_node_ids=(anchor["routing_node"], destination_node),
                    network_place_ids=(anchor["place_id"],),
                    strategic_destination_ids=(destination_id,),
                ),
                anchor_connection_ids=tuple(
                    [anchor["access_connection_id"]]
                    if anchor["access_connection_id"]
                    else []
                ),
                anchor_obligation_ids=tuple(
                    [anchor["access_obligation_id"]]
                    if anchor["access_obligation_id"]
                    else []
                ),
                routing_start_node_id=anchor["routing_node"],
                routing_end_node_id=destination_node,
                strategic_destination_id=destination_id,
                site_id=admission.site_id,
                access_point_evidence_ids=site["access_ids"],
                candidate_records=records,
            )
        )
    return tuple(units), tuple(issues)


def _candidate_set(
    profile: NetworkSelectionProfile,
    graph: RoadGraph,
    *,
    unit_role: StrategicCorridorUnitRole,
    endpoints: tuple[str, str],
    mandatory_network_place_ids: tuple[str, ...],
    start_node: str,
    end_node: str,
    source_ids: tuple[str, ...],
    evidence_ids: tuple[str, ...],
    context: gpd.GeoDataFrame,
    strategic_destination_id: str | None,
) -> tuple[AlignmentCandidateSet, tuple[StrategicCorridorCandidateRecord, ...]]:
    _selected, options, _rationale = choose_alignment(graph, start_node, end_node)
    strategic_destination_ids = (
        (strategic_destination_id,) if strategic_destination_id is not None else ()
    )
    generated: dict[tuple[object, ...], dict[str, object]] = {}
    for option in options:
        geometry = _canonical_geometry(option.geometry, graph.crs)
        if geometry is None:
            continue
        source_class = _source_class(option, graph, context)
        key = (
            unit_role.value,
            tuple(sorted(endpoints)),
            tuple(sorted(mandatory_network_place_ids)),
            strategic_destination_ids,
            tuple(option.edge_ids),
            tuple(option.reverse_edge_ids),
            geometry.fingerprint,
            source_class.value,
        )
        entry = generated.setdefault(
            key,
            {
                "option": option,
                "geometry": geometry,
                "source_class": source_class,
                "strategies": set(),
            },
        )
        strategies = entry["strategies"]
        assert isinstance(strategies, set)
        strategies.add(str(option.role))

    candidates: list[AlignmentCandidateInput] = []
    raw: list[
        tuple[
            AlignmentCandidateInput,
            RouteOption,
            tuple[str, ...],
        ]
    ] = []
    for key in sorted(generated, key=repr):
        entry = generated[key]
        option = entry["option"]
        geometry = entry["geometry"]
        source_class = entry["source_class"]
        strategies = tuple(sorted(entry["strategies"]))
        assert isinstance(option, RouteOption)
        assert isinstance(geometry, CanonicalLineString)
        assert isinstance(source_class, CandidateSourceClass)
        payload = {
            "unit_role": unit_role.value,
            "generation_strategies": list(strategies),
            "start": start_node,
            "end": end_node,
            "edge_ids": option.edge_ids,
            "reverse_edge_ids": option.reverse_edge_ids,
            "source_ids": source_ids,
            "evidence_ids": evidence_ids,
            "geometry_fingerprint": geometry.fingerprint,
        }
        candidate = AlignmentCandidateInput(
            network_role=unit_role.network_role,
            endpoints=endpoints,
            source_class=source_class,
            geometry=geometry,
            evidence_fingerprints=(_fingerprint(payload),),
            provenance_ids=tuple(sorted({*source_ids, *evidence_ids, *option.edge_ids})),
            topology_state=(
                CriterionState.SATISFIED
                if option.bidirectional
                else CriterionState.UNSATISFIED
            ),
            served_network_place_ids=tuple(sorted(mandatory_network_place_ids)),
            served_strategic_destination_ids=strategic_destination_ids,
            directness_m=float(option.length_km * 1000),
        )
        candidates.append(candidate)
        raw.append((candidate, option, strategies))
    candidate_set = admit_candidate_set(
        profile,
        network_role=unit_role.network_role,
        endpoints=endpoints,
        candidates=tuple(candidates),
        mandatory_network_place_ids=tuple(sorted(mandatory_network_place_ids)),
        mandatory_strategic_destination_ids=strategic_destination_ids,
    )
    admitted = {item.candidate_id for item in candidate_set.admitted_candidates}
    records = tuple(
        sorted(
            (
                StrategicCorridorCandidateRecord(
                    candidate=candidate,
                    physical_alignment_id=_stable_id(
                        "physical-alignment", candidate.geometry_fingerprint
                    ),
                    routing_start_node_id=start_node,
                    routing_end_node_id=end_node,
                    routing_edge_ids=tuple(option.edge_ids),
                    reverse_routing_edge_ids=tuple(option.reverse_edge_ids),
                    source_ids=tuple(sorted({*source_ids, *option.edge_ids})),
                    evidence_ids=evidence_ids,
                    generation_strategies=strategies,
                    generation_rationale=(
                        "retained exact physical route generated by "
                        + ", ".join(strategies)
                        if candidate.candidate_id in admitted
                        else (
                            "exact physical route generated by "
                            + ", ".join(strategies)
                            + " retained only in admission provenance"
                        )
                    ),
                )
                for candidate, option, strategies in raw
            ),
            key=lambda item: item.candidate.candidate_id,
        )
    )
    return candidate_set, records


def _direct_spine_anchors(
    connections: gpd.GeoDataFrame,
    *,
    access_obligations: gpd.GeoDataFrame | None = None,
) -> tuple[dict[str, str], ...]:
    """Return routed and explicit non-route direct-spine anchor evidence."""

    required = {
        "access_connection_id",
        "obligation_kind",
        "parent_role",
        "place_id",
        "root_spine_id",
        "community_attachment_node",
    }
    result: list[dict[str, str]] = []
    if not connections.empty and required.issubset(connections.columns):
        selected = connections[
            connections["obligation_kind"].eq("community")
            & connections["parent_role"].eq("strategic-spine")
        ]
        for _, row in selected.sort_values("access_connection_id").iterrows():
            provenance = _json_object(row.get("provenance"))
            access_connection_id = _text(row.get("access_connection_id"))
            values = {
                "anchor_id": access_connection_id,
                "access_connection_id": access_connection_id,
                "access_obligation_id": "",
                "place_id": _text(row.get("place_id")),
                "root_spine_id": _text(row.get("root_spine_id")),
                "routing_node": _text(row.get("community_attachment_node")),
                "source_id": (
                    _text(provenance.get("root_source_id")) or "unknown-source"
                ),
                "evidence_id": (
                    _text(provenance.get("root_evidence_id")) or "unknown-evidence"
                ),
            }
            if all(
                values[key]
                for key in (
                    "anchor_id",
                    "place_id",
                    "root_spine_id",
                    "routing_node",
                )
            ):
                result.append(values)
    if access_obligations is None or access_obligations.empty:
        return tuple(result)
    association_required = {
        "obligation_id",
        "obligation_kind",
        "place_id",
        "service_status",
        "access_connection_id",
        "root_spine_id",
        "provenance",
    }
    if not association_required.issubset(access_obligations.columns):
        return tuple(result)
    associations = access_obligations[
        access_obligations["obligation_kind"].eq("community")
        & access_obligations["service_status"].eq("served")
    ]
    for _, row in associations.sort_values("obligation_id").iterrows():
        provenance = _json_object(row.get("provenance"))
        root_spine_id = _text(row.get("root_spine_id"))
        if (
            _text(row.get("access_connection_id"))
            or provenance.get("service_kind") != "backbone-access-association"
            or provenance.get("association_kind")
            != "colocated-direct-strategic-spine"
            or provenance.get("parent_role") != "strategic-spine"
            or _text(provenance.get("root_spine_id")) != root_spine_id
        ):
            continue
        obligation_id = _text(row.get("obligation_id"))
        values = {
            "anchor_id": obligation_id,
            "access_connection_id": "",
            "access_obligation_id": obligation_id,
            "place_id": _text(row.get("place_id")),
            "root_spine_id": root_spine_id,
            "routing_node": _text(provenance.get("routing_node_id")),
            "source_id": _text(provenance.get("root_source_id")),
            "evidence_id": _text(provenance.get("root_evidence_id")),
        }
        if all(
            values[key]
            for key in (
                "anchor_id",
                "access_obligation_id",
                "place_id",
                "root_spine_id",
                "routing_node",
                "source_id",
                "evidence_id",
            )
        ) and _text(provenance.get("parent_target_id")):
            result.append(values)
    return tuple(result)


def _group_anchors(
    anchors: Iterable[dict[str, str]],
) -> tuple[tuple[str, tuple[dict[str, str], ...]], ...]:
    groups: dict[str, list[dict[str, str]]] = {}
    for anchor in anchors:
        groups.setdefault(anchor["root_spine_id"], []).append(anchor)
    return tuple(
        (
            root_spine_id,
            tuple(sorted(group, key=lambda item: item["anchor_id"])),
        )
        for root_spine_id, group in sorted(groups.items())
    )


def _governed_destination_site(
    context: gpd.GeoDataFrame,
    admission: object,
) -> dict[str, object] | None:
    required = {
        "site_id",
        "access_point_evidence_ids",
        "source_id",
        "evidence_id",
        "admission_record_id",
        "admission_record_version",
        "access_point_graph_node_id",
        "access_point_graph_edge_ids",
        "access_point_graph_binding_sha256",
    }
    if context.empty or not required.issubset(context.columns):
        return None
    site_id = str(admission.site_id)
    access_ids = tuple(admission.access_point_evidence_ids)
    rows = context[context["site_id"].eq(site_id)]
    matches: list[dict[str, object]] = []
    for _, row in rows.iterrows():
        geometry = row.geometry
        row_access_ids = _identifier_sequence(row.get("access_point_evidence_ids"))
        graph_edge_ids = _identifier_sequence(row.get("access_point_graph_edge_ids"))
        graph_node_id = _text(row.get("access_point_graph_node_id"))
        graph_binding_sha256 = _text(row.get("access_point_graph_binding_sha256"))
        if (
            not isinstance(geometry, Point)
            or geometry.is_empty
            or row_access_ids != access_ids
            or _text(row.get("admission_record_id")) != admission.record_id
            or _text(row.get("admission_record_version")) != admission.record_version
            or _text(row.get("site_status")) != "current"
            or not _text(row.get("source_id"))
            or not _text(row.get("evidence_id"))
            or not graph_node_id
            or not graph_edge_ids
            or re.fullmatch(r"[0-9a-f]{64}", graph_binding_sha256) is None
        ):
            continue
        matches.append(
            {
                "geometry": geometry,
                "access_ids": row_access_ids,
                "source_id": _text(row.get("source_id")),
                "evidence_id": _text(row.get("evidence_id")),
                "site_id": site_id,
                "admission_record_id": _text(row.get("admission_record_id")),
                "admission_record_version": _text(
                    row.get("admission_record_version")
                ),
                "graph_node_id": graph_node_id,
                "graph_edge_ids": graph_edge_ids,
                "graph_binding_sha256": graph_binding_sha256,
            }
        )
    return matches[0] if len(matches) == 1 else None


def _current_graph_destination_node(
    graph: RoadGraph,
    site: dict[str, object],
) -> str | None:
    """Resolve only an exact, content-bound current graph access point."""

    geometry = site.get("geometry")
    node_id = site.get("graph_node_id")
    expected_edge_ids = site.get("graph_edge_ids")
    if (
        not isinstance(geometry, Point)
        or not isinstance(node_id, str)
        or not isinstance(expected_edge_ids, tuple)
    ):
        return None
    node_point = graph.node_points.get(node_id)
    if node_point is None or not node_point.equals_exact(geometry, tolerance=0.0):
        return None
    actual_edge_ids = tuple(
        sorted(
            {
                str(edge.get("edge_id"))
                for left, right, edge in graph.graph.edges(data=True)
                if node_id in {str(left), str(right)} and _text(edge.get("edge_id"))
            }
        )
    )
    if actual_edge_ids != expected_edge_ids:
        return None
    payload = {
        "contract": STRATEGIC_DESTINATION_GRAPH_BINDING_CONTRACT,
        "site_id": site["site_id"],
        "admission_record_id": site["admission_record_id"],
        "admission_record_version": site["admission_record_version"],
        "access_point_evidence_ids": list(site["access_ids"]),
        "source_id": site["source_id"],
        "evidence_id": site["evidence_id"],
        "graph_node_id": node_id,
        "graph_edge_ids": list(actual_edge_ids),
        "geometry_wkb": geometry.wkb_hex,
    }
    return node_id if site.get("graph_binding_sha256") == _fingerprint(payload) else None


def _nearest_anchor(
    graph: RoadGraph,
    geometry: Point,
    anchors: tuple[dict[str, str], ...],
) -> dict[str, str] | None:
    target = gpd.GeoSeries([geometry], crs=graph.crs).to_crs(27700).iloc[0]
    distances: list[tuple[float, str, dict[str, str]]] = []
    for anchor in anchors:
        point = graph.node_points.get(anchor["routing_node"])
        if point is None:
            continue
        projected = gpd.GeoSeries([point], crs=graph.crs).to_crs(27700).iloc[0]
        distances.append(
            (
                float(projected.distance(target)),
                anchor["anchor_id"],
                anchor,
            )
        )
    return min(distances, default=(0.0, "", None), key=lambda item: item[:2])[2]


def _canonical_geometry(geometry: object, crs: object) -> CanonicalLineString | None:
    if not isinstance(geometry, LineString) or geometry.is_empty:
        return None
    projected = gpd.GeoSeries([geometry], crs=crs).to_crs(27700).iloc[0]
    if not isinstance(projected, LineString) or len(projected.coords) < 2:
        return None
    try:
        return CanonicalLineString(
            coordinates=tuple((float(x), float(y)) for x, y in projected.coords)
        )
    except ValueError:
        return None


def _source_class(
    option: RouteOption,
    graph: RoadGraph,
    context: gpd.GeoDataFrame,
) -> CandidateSourceClass:
    if option.ncn_share > 0:
        return CandidateSourceClass.VERIFIED_EXISTING_ASSET
    edge_ids = set(option.edge_ids)
    refs = {
        ref
        for _, _, edge in graph.graph.edges(data=True)
        if str(edge.get("edge_id")) in edge_ids
        for ref in edge.get("ref", ())
    }
    if any(re.fullmatch(r"A\s*\d+[A-Z]?", str(ref), re.IGNORECASE) for ref in refs):
        return CandidateSourceClass.A_ROAD_CORRIDOR
    if option.a_road_share > 0:
        return CandidateSourceClass.A_ROAD_CORRIDOR
    return CandidateSourceClass.OTHER_ROUTABLE


def _physical_alignments(
    units: tuple[PreparedStrategicCorridorUnit, ...],
) -> tuple[PhysicalAlignment, ...]:
    memberships: dict[str, dict[str, object]] = {}
    for unit in units:
        for record in unit.candidate_records:
            entry = memberships.setdefault(
                record.physical_alignment_id,
                {
                    "geometry": record.candidate.geometry,
                    "geometry_fingerprints": {
                        record.candidate.geometry_fingerprint
                    },
                    "candidates": set(),
                    "roles": set(),
                },
            )
            fingerprints = entry["geometry_fingerprints"]
            candidates = entry["candidates"]
            roles = entry["roles"]
            assert isinstance(fingerprints, set)
            assert isinstance(candidates, set)
            assert isinstance(roles, set)
            fingerprints.add(record.candidate.geometry_fingerprint)
            candidates.add(record.candidate.candidate_id)
            roles.add(unit.unit_role.value)
    output: list[PhysicalAlignment] = []
    for identifier, entry in memberships.items():
        fingerprints = entry["geometry_fingerprints"]
        candidates = entry["candidates"]
        roles = entry["roles"]
        geometry = entry["geometry"]
        assert isinstance(fingerprints, set)
        assert isinstance(candidates, set)
        assert isinstance(roles, set)
        assert isinstance(geometry, CanonicalLineString)
        if len(fingerprints) != 1:
            raise ValueError("physical alignment identity must resolve one authoritative geometry")
        output.append(
            PhysicalAlignment(
                physical_alignment_id=identifier,
                geometry=geometry,
                geometry_fingerprint=next(iter(fingerprints)),
                candidate_ids=tuple(sorted(candidates)),
                role_memberships=tuple(sorted(roles)),
            )
        )
    return tuple(sorted(output, key=lambda item: item.physical_alignment_id))


def _evidence_lineage(
    population: object,
    education: EducationAccessEvidenceLoad | None,
) -> dict[str, object]:
    return {
        "population": (
            {
                "source_content_sha256": population.source.content_sha256,
                "frame_content_sha256": population.frame_content_sha256,
            }
            if population is not None
            else None
        ),
        "education": (
            {
                "governed_source_fingerprint": education.governed_source_fingerprint,
                "source_snapshot": education.source_snapshot.model_dump(
                    mode="json"
                ),
                "school_register_content_sha256": education.school_register_lineage.content_sha256,
                "admissions_content_sha256": (
                    education.admissions_lineage.content_sha256
                    if education.admissions_lineage is not None
                    else None
                ),
            }
            if education is not None
            else None
        ),
    }


def _lineage_fingerprints(lineage: dict[str, object]) -> set[str]:
    result: set[str] = set()
    for value in lineage.values():
        if not isinstance(value, dict):
            continue
        for fingerprint in value.values():
            if isinstance(fingerprint, str) and re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                result.add(fingerprint)
    return result


def _identifier_sequence(value: object) -> tuple[str, ...]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ()
    if not isinstance(value, (list, tuple)):
        return ()
    values = tuple(str(item) for item in value)
    if (
        not values
        or values != tuple(sorted(set(values)))
        or any(_ID.fullmatch(item) is None for item in values)
    ):
        return ()
    return values


def _text(value: object) -> str:
    return value if isinstance(value, str) and value.strip() == value else ""


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _issue_key(issue: StrategicCorridorIssue) -> tuple[str, str, str, str, str]:
    return (
        issue.unit_role.value,
        issue.strategic_destination_id or "",
        issue.site_id or "",
        issue.reason,
        issue.detail,
    )
