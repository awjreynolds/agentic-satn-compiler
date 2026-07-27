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
    geometry_fingerprint: str
    candidate_ids: tuple[str, ...]
    role_memberships: tuple[str, ...]

    def canonical(self) -> dict[str, object]:
        return {
            "physical_alignment_id": self.physical_alignment_id,
            "geometry_fingerprint": self.geometry_fingerprint,
            "candidate_ids": list(self.candidate_ids),
            "role_memberships": list(self.role_memberships),
        }


@dataclass(frozen=True)
class StrategicCorridorCandidateRecord:
    """Exact graph and source facts for one generated candidate."""

    candidate: AlignmentCandidateInput
    physical_alignment_id: str
    route_role: str
    routing_start_node_id: str
    routing_end_node_id: str
    routing_edge_ids: tuple[str, ...]
    reverse_routing_edge_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    generation_rationale: str

    def canonical(self) -> dict[str, object]:
        return {
            "candidate": self.candidate.model_dump(mode="json"),
            "physical_alignment_id": self.physical_alignment_id,
            "route_role": self.route_role,
            "routing_start_node_id": self.routing_start_node_id,
            "routing_end_node_id": self.routing_end_node_id,
            "routing_edge_ids": list(self.routing_edge_ids),
            "reverse_routing_edge_ids": list(self.reverse_routing_edge_ids),
            "source_ids": list(self.source_ids),
            "evidence_ids": list(self.evidence_ids),
            "generation_rationale": self.generation_rationale,
        }


@dataclass(frozen=True)
class PreparedStrategicCorridorUnit:
    """A finite, role-specific Alignment Candidate Set with no selection authority."""

    unit_id: str
    unit_role: StrategicCorridorUnitRole
    candidate_set: AlignmentCandidateSet
    anchor_connection_ids: tuple[str, ...]
    routing_start_node_id: str
    routing_end_node_id: str
    strategic_destination_id: str | None
    site_id: str | None
    access_point_evidence_ids: tuple[str, ...]
    candidate_records: tuple[StrategicCorridorCandidateRecord, ...]

    def canonical(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "unit_role": self.unit_role.value,
            "candidate_set": self.candidate_set.model_dump(mode="json"),
            "anchor_connection_ids": list(self.anchor_connection_ids),
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
    units, issues = _interurban_units(profile, road_graph, spine_access_connections, context)
    destination_units, destination_issues = _destination_units(
        profile,
        road_graph,
        spine_access_connections,
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
    connections: gpd.GeoDataFrame,
    context: gpd.GeoDataFrame,
) -> tuple[tuple[PreparedStrategicCorridorUnit, ...], tuple[StrategicCorridorIssue, ...]]:
    anchors = _direct_spine_anchors(connections)
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
                    "anchors": [left["access_connection_id"], right["access_connection_id"]],
                    "candidate_set": candidate_set.candidate_set_fingerprint,
                },
            )
            units.append(
                PreparedStrategicCorridorUnit(
                    unit_id=unit_id,
                    unit_role=StrategicCorridorUnitRole.INTERURBAN_SPINE,
                    candidate_set=candidate_set,
                    anchor_connection_ids=tuple(
                        sorted((left["access_connection_id"], right["access_connection_id"]))
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
    connections: gpd.GeoDataFrame,
    context: gpd.GeoDataFrame,
    education: EducationAccessEvidenceLoad | None,
) -> tuple[tuple[PreparedStrategicCorridorUnit, ...], tuple[StrategicCorridorIssue, ...]]:
    if education is None or not education.strategic_admission_records:
        return (), ()
    anchors = _direct_spine_anchors(connections)
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
        site_nodes = graph.nodes_on_geometry(site["geometry"], tolerance_m=20)
        if not site_nodes:
            issues.append(
                StrategicCorridorIssue(
                    StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS,
                    "destination-access-geometry-not-attached-to-current-road-graph",
                    (
                        "governed destination access geometry has no exact current "
                        "RoadGraph attachment"
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
        destination_node = site_nodes[0][0]
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
        candidate_set, records = _candidate_set(
            profile,
            graph,
            unit_role=StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS,
            endpoints=(anchor["place_id"], destination_id),
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
                "anchor": anchor["access_connection_id"],
                "site_id": admission.site_id,
                "candidate_set": candidate_set.candidate_set_fingerprint,
            },
        )
        units.append(
            PreparedStrategicCorridorUnit(
                unit_id=unit_id,
                unit_role=StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS,
                candidate_set=candidate_set,
                anchor_connection_ids=(anchor["access_connection_id"],),
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
    start_node: str,
    end_node: str,
    source_ids: tuple[str, ...],
    evidence_ids: tuple[str, ...],
    context: gpd.GeoDataFrame,
    strategic_destination_id: str | None,
) -> tuple[AlignmentCandidateSet, tuple[StrategicCorridorCandidateRecord, ...]]:
    _selected, options, _rationale = choose_alignment(graph, start_node, end_node)
    candidates: list[AlignmentCandidateInput] = []
    raw: list[tuple[AlignmentCandidateInput, RouteOption]] = []
    for option in options:
        geometry = _canonical_geometry(option.geometry, graph.crs)
        if geometry is None:
            continue
        source_class = _source_class(option, graph, context)
        payload = {
            "unit_role": unit_role.value,
            "route_role": option.role,
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
            served_network_place_ids=tuple(sorted(endpoints)),
            served_strategic_destination_ids=(
                (strategic_destination_id,) if strategic_destination_id is not None else ()
            ),
            directness_m=float(option.length_km * 1000),
        )
        candidates.append(candidate)
        raw.append((candidate, option))
    candidate_set = admit_candidate_set(
        profile,
        network_role=unit_role.network_role,
        endpoints=endpoints,
        candidates=tuple(candidates),
        mandatory_network_place_ids=tuple(sorted(endpoints)),
        mandatory_strategic_destination_ids=(
            (strategic_destination_id,) if strategic_destination_id is not None else ()
        ),
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
                    route_role=option.role,
                    routing_start_node_id=start_node,
                    routing_end_node_id=end_node,
                    routing_edge_ids=tuple(option.edge_ids),
                    reverse_routing_edge_ids=tuple(option.reverse_edge_ids),
                    source_ids=tuple(sorted({*source_ids, *option.edge_ids})),
                    evidence_ids=evidence_ids,
                    generation_rationale=(
                        "retained finite candidate" if candidate.candidate_id in admitted
                        else "generated finite candidate retained only in admission provenance"
                    ),
                )
                for candidate, option in raw
            ),
            key=lambda item: item.candidate.candidate_id,
        )
    )
    return candidate_set, records


def _direct_spine_anchors(connections: gpd.GeoDataFrame) -> tuple[dict[str, str], ...]:
    if connections.empty:
        return ()
    required = {
        "access_connection_id",
        "obligation_kind",
        "parent_role",
        "place_id",
        "root_spine_id",
        "community_attachment_node",
    }
    if not required.issubset(connections.columns):
        return ()
    selected = connections[
        connections["obligation_kind"].eq("community")
        & connections["parent_role"].eq("strategic-spine")
    ]
    result: list[dict[str, str]] = []
    for _, row in selected.sort_values("access_connection_id").iterrows():
        values = {
            "access_connection_id": _text(row.get("access_connection_id")),
            "place_id": _text(row.get("place_id")),
            "root_spine_id": _text(row.get("root_spine_id")),
            "routing_node": _text(row.get("community_attachment_node")),
        }
        if not all(values.values()):
            continue
        provenance = _json_object(row.get("provenance"))
        values["source_id"] = _text(provenance.get("root_source_id")) or "unknown-source"
        values["evidence_id"] = _text(provenance.get("root_evidence_id")) or "unknown-evidence"
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
            tuple(sorted(group, key=lambda item: item["access_connection_id"])),
        )
        for root_spine_id, group in sorted(groups.items())
    )


def _governed_destination_site(
    context: gpd.GeoDataFrame,
    admission: object,
) -> dict[str, object] | None:
    required = {"site_id", "access_point_evidence_ids", "source_id", "evidence_id"}
    if context.empty or not required.issubset(context.columns):
        return None
    site_id = str(admission.site_id)
    access_ids = tuple(admission.access_point_evidence_ids)
    rows = context[context["site_id"].eq(site_id)]
    matches: list[dict[str, object]] = []
    for _, row in rows.iterrows():
        geometry = row.geometry
        row_access_ids = _identifier_sequence(row.get("access_point_evidence_ids"))
        if (
            not isinstance(geometry, Point)
            or geometry.is_empty
            or row_access_ids != access_ids
            or _text(row.get("site_status")) != "current"
            or not _text(row.get("source_id"))
            or not _text(row.get("evidence_id"))
        ):
            continue
        matches.append(
            {
                "geometry": geometry,
                "access_ids": row_access_ids,
                "source_id": _text(row.get("source_id")),
                "evidence_id": _text(row.get("evidence_id")),
            }
        )
    return matches[0] if len(matches) == 1 else None


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
                anchor["access_connection_id"],
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
    memberships: dict[str, dict[str, set[str]]] = {}
    for unit in units:
        for record in unit.candidate_records:
            entry = memberships.setdefault(
                record.physical_alignment_id,
                {
                    "geometry": {record.candidate.geometry_fingerprint},
                    "candidates": set(),
                    "roles": set(),
                },
            )
            entry["geometry"].add(record.candidate.geometry_fingerprint)
            entry["candidates"].add(record.candidate.candidate_id)
            entry["roles"].add(unit.unit_role.value)
    output: list[PhysicalAlignment] = []
    for identifier, entry in memberships.items():
        if len(entry["geometry"]) != 1:
            raise ValueError("physical alignment identity must resolve one authoritative geometry")
        output.append(
            PhysicalAlignment(
                physical_alignment_id=identifier,
                geometry_fingerprint=next(iter(entry["geometry"])),
                candidate_ids=tuple(sorted(entry["candidates"])),
                role_memberships=tuple(sorted(entry["roles"])),
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
