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
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations, pairwise
from pathlib import Path
from time import perf_counter

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point
from shapely.ops import substring

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
from satn.routing import RoadGraph, RouteOption, _coordinate_id, _present, _truthy, choose_alignment
from satn.section_population import (
    MaterialPopulationDifference,
    SectionPopulationAssessment,
    SectionPopulationProfile,
    compile_section_population_capture,
    derive_material_population_differences,
)
from satn.spine_access_candidate_preparation import SpineAccessCandidatePreparationResult
from satn.urban import URBAN_SPINE_TERMINUS_TOLERANCE_M

STRATEGIC_CORRIDOR_PREPARATION_CONTRACT = "satn-strategic-corridor-preparation/v1"
STRATEGIC_DESTINATION_GRAPH_BINDING_CONTRACT = "satn-strategic-destination-graph-binding/v1"
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_ROUTING_ROLES = ("direct", "strategic-spine", "ncn-informed", "low-traffic")


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


def _provenance_id(value: str) -> str:
    """Retain canonical source IDs and bind compound external IDs deterministically."""
    return value if _ID.fullmatch(value) is not None else _stable_id("source-reference", value)


class StrategicCorridorUnitRole(StrEnum):
    """The two separately governed logical roles in this narrow seam."""

    INTERURBAN_SPINE = "interurban-spine"
    A_ROAD_BACKBONE = "a-road-backbone"
    STRATEGIC_DESTINATION_ACCESS = "strategic-destination-access"

    @property
    def network_role(self) -> NetworkRole:
        return (
            NetworkRole.INTERURBAN_SPINE
            if self
            in {
                StrategicCorridorUnitRole.INTERURBAN_SPINE,
                StrategicCorridorUnitRole.A_ROAD_BACKBONE,
            }
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
    obligation_id: str | None = None
    endpoints: tuple[str, str] = ("", "")
    network_role: str | None = None
    endpoint_coordinates: tuple[tuple[float, float], ...] = ()
    component_ids: tuple[str, ...] = ()

    def canonical(self) -> dict[str, object]:
        return {
            "unit_role": self.unit_role.value,
            "reason": self.reason,
            "detail": self.detail,
            "strategic_destination_id": self.strategic_destination_id,
            "site_id": self.site_id,
            "obligation_id": self.obligation_id,
            "endpoints": list(self.endpoints),
            "network_role": self.network_role,
            "endpoint_coordinates": [list(item) for item in self.endpoint_coordinates],
            "component_ids": list(self.component_ids),
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
            or self.network_place_ids != tuple(sorted(set(self.network_place_ids)))
            or self.strategic_destination_ids != tuple(sorted(set(self.strategic_destination_ids)))
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
    network_scope: str = "rural"
    backbone_required: bool = False
    endpoint_coordinates: tuple[tuple[float, float], ...] = ()
    backbone_component_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        binding = self.endpoint_binding
        anchor_count = len(self.anchor_connection_ids) + len(self.anchor_obligation_ids)
        if (
            self.candidate_set.endpoints != binding.candidate_endpoints
            or self.candidate_set.mandatory_strategic_destination_ids
            != binding.strategic_destination_ids
            or (
                self.routing_start_node_id,
                self.routing_end_node_id,
            )
            != binding.routing_node_ids
            or self.anchor_connection_ids != tuple(sorted(set(self.anchor_connection_ids)))
            or self.anchor_obligation_ids != tuple(sorted(set(self.anchor_obligation_ids)))
        ):
            raise ValueError("strategic corridor unit endpoint binding is stale")
        if self.network_scope not in {"urban", "rural"}:
            raise ValueError("strategic corridor unit network scope is invalid")
        if self.endpoint_coordinates and (
            len(self.endpoint_coordinates) != 2
            or any(len(point) != 2 for point in self.endpoint_coordinates)
        ):
            raise ValueError("strategic corridor unit endpoint coordinates are invalid")
        if self.backbone_component_ids != tuple(sorted(set(self.backbone_component_ids))):
            raise ValueError("strategic corridor unit backbone component IDs are not canonical")
        if self.backbone_required:
            if (
                self.unit_role is not StrategicCorridorUnitRole.A_ROAD_BACKBONE
                or self.anchor_connection_ids
                or self.anchor_obligation_ids
                or binding.network_place_ids
                or binding.strategic_destination_ids
                or self.candidate_set.mandatory_network_place_ids
                or self.candidate_set.mandatory_strategic_destination_ids
                or any(
                    candidate.served_network_place_ids or candidate.served_strategic_destination_ids
                    for candidate in self.candidate_set.candidates
                )
            ):
                raise ValueError("A-road backbone unit must use mechanical endpoints only")
        elif self.unit_role is StrategicCorridorUnitRole.INTERURBAN_SPINE:
            if (
                anchor_count != 2
                or len(binding.network_place_ids) != 2
                or binding.strategic_destination_ids
                or self.strategic_destination_id is not None
                or self.candidate_set.mandatory_network_place_ids != binding.network_place_ids
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
            or (self.strategic_destination_id,) != binding.strategic_destination_ids
            or self.strategic_destination_id in binding.candidate_endpoints
            or self.candidate_set.mandatory_network_place_ids
            or any(
                candidate.served_network_place_ids
                or candidate.served_strategic_destination_ids != binding.strategic_destination_ids
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
            "network_scope": self.network_scope,
            "backbone_required": self.backbone_required,
            "endpoint_coordinates": [list(item) for item in self.endpoint_coordinates],
            "backbone_component_ids": list(self.backbone_component_ids),
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
    section_population: SectionPopulationAssessment | None
    material_population_differences: tuple[MaterialPopulationDifference, ...]
    preparation_fingerprint: str
    phase_diagnostics: dict[str, object]

    @property
    def prepared(self) -> bool:
        return self.status == "prepared"

    def canonical_payload(self) -> dict[str, object]:
        payload = {
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
        if self.section_population is not None:
            payload["section_population"] = self.section_population.canonical()
            payload["material_population_differences"] = [
                item.canonical() for item in self.material_population_differences
            ]
        return payload

    def metadata(self) -> dict[str, object]:
        return {
            **self.canonical_payload(),
            "preparation_fingerprint": self.preparation_fingerprint,
            "unit_count": len(self.units),
            "candidate_count": sum(len(item.candidate_set.candidates) for item in self.units),
            "population_display_section_count": (
                len(self.section_population.sections) if self.section_population is not None else 0
            ),
            "phase_diagnostics": self.phase_diagnostics,
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

    spine_access_preparation: SpineAccessCandidatePreparationResult | None
    strategic_corridor_preparation: StrategicCorridorPreparationResult | None

    @property
    def alignment_units(self) -> tuple[object, ...]:
        access = getattr(self.spine_access_preparation, "prepared_spine_access_connections", ())
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
    area_definition: gpd.GeoDataFrame | None = None,
    urban_extent: gpd.GeoDataFrame | None = None,
    official_road_classification: gpd.GeoDataFrame | None = None,
    urban_spines: gpd.GeoDataFrame | None = None,
) -> StrategicCorridorPreparationResult:
    """Generate only finite, current, evidence-bound strategic units.

    The compiler owns all geometry and education-destination association.  In
    particular, a destination name in context is not enough: site identity,
    exact access evidence identifiers, current status, geometry and an exact
    RoadGraph attachment are all required before a destination route exists.
    """

    started_at = perf_counter()
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
    route_pairs = set(_strategic_route_pairs(road_graph, anchors, context, education))
    # Backbone pairs use the same targeted route-option seam as ordinary
    # strategic pairs. Unbound official junctions are intentionally omitted
    # here and remain explicit preparation issues below.
    for chain in _official_a_road_chains(official_road_classification):
        start_node = _bound_backbone_node(road_graph, chain["start_point"])
        end_node = _bound_backbone_node(road_graph, chain["end_point"])
        if start_node is not None and end_node is not None and start_node != end_node:
            route_pairs.add((start_node, end_node))
    route_options, route_searches = road_graph.route_options_for_pairs(
        tuple(sorted(route_pairs)),
        roles=_ROUTING_ROLES,
        strategic_use=True,
    )
    backbone_units, backbone_issues = _a_road_backbone_units(
        profile,
        road_graph,
        official_road_classification,
        urban_spines,
        context,
        route_options,
    )
    units, issues = _interurban_units(
        profile,
        road_graph,
        anchors,
        context,
        route_options,
    )
    destination_units, destination_issues = _destination_units(
        profile,
        road_graph,
        anchors,
        context,
        education,
        route_options,
    )
    units = tuple(
        sorted((*backbone_units, *units, *destination_units), key=lambda item: item.unit_id)
    )
    issues = tuple(sorted((*backbone_issues, *issues, *destination_issues), key=_issue_key))
    section_population = None
    if (
        population_load is not None
        and area_definition is not None
        and urban_extent is not None
        and units
    ):
        population_alignments = _population_alignment_frame(units)
        if not population_alignments.empty:
            section_population = compile_section_population_capture(
                population_alignments,
                population_load.output_areas,
                area_definition,
                urban_extent=urban_extent,
                source_content_sha256=population_load.frame_content_sha256,
                profile=SectionPopulationProfile(
                    **profile.section_population.model_dump(
                        mode="python",
                        exclude={"profile"},
                    )
                ),
            )
    material_population_differences = (
        derive_material_population_differences(section_population)
        if section_population is not None
        else ()
    )
    physical_alignments = _physical_alignments(units)
    phase_diagnostics = {
        "anchors": len(anchors),
        "pairs": len(route_pairs),
        "a_road_backbone_units": len(backbone_units),
        "a_road_backbone_issues": len(backbone_issues),
        "route_searches": route_searches,
        "unique_alignments": len(physical_alignments),
        "sections": len(section_population.sections) if section_population is not None else 0,
        "elapsed_seconds": perf_counter() - started_at,
    }
    lineage = _evidence_lineage(population_load, education)
    fingerprints = tuple(sorted(_lineage_fingerprints(lineage)))
    # A missing/mismatched admitted destination is a hard preparation blocker:
    # the interurban unit stays inspectable, but no later selection can pretend
    # that the admitted destination was served.
    status = (
        "prepared"
        if not missing
        and not any(
            item.unit_role
            in {
                StrategicCorridorUnitRole.STRATEGIC_DESTINATION_ACCESS,
                StrategicCorridorUnitRole.A_ROAD_BACKBONE,
            }
            for item in issues
        )
        else "incomplete"
    )
    provisional = {
        "contract": STRATEGIC_CORRIDOR_PREPARATION_CONTRACT,
        "profile_fingerprint": profile.fingerprint,
        "status": status,
        "units": [item.canonical() for item in units],
        "physical_alignments": [item.canonical() for item in physical_alignments],
        "issues": [item.canonical() for item in issues],
        "missing_inputs": sorted(set(missing)),
        "evidence_fingerprints": list(fingerprints),
        "evidence_lineage": lineage,
        **(
            {
                "section_population": section_population.canonical(),
                "material_population_differences": [
                    item.canonical() for item in material_population_differences
                ],
            }
            if section_population is not None
            else {}
        ),
    }
    return StrategicCorridorPreparationResult(
        contract=STRATEGIC_CORRIDOR_PREPARATION_CONTRACT,
        profile_fingerprint=profile.fingerprint,
        status=status,
        units=units,
        physical_alignments=physical_alignments,
        issues=issues,
        missing_inputs=tuple(sorted(set(missing))),
        evidence_fingerprints=fingerprints,
        evidence_lineage=lineage,
        section_population=section_population,
        material_population_differences=material_population_differences,
        preparation_fingerprint=_fingerprint(provisional),
        phase_diagnostics=phase_diagnostics,
    )


def _population_alignment_frame(
    units: tuple[PreparedStrategicCorridorUnit, ...],
) -> gpd.GeoDataFrame:
    """Expose every finite candidate once through the section-capture seam."""

    rows = [
        {
            "candidate_group_id": unit.unit_id,
            "alignment_id": candidate.candidate_id,
            "geometry": candidate.geometry.as_shapely(),
        }
        for unit in units
        for candidate in unit.candidate_set.candidates
    ]
    # AlignmentCandidateInput canonical geometry is always EPSG:27700,
    # independently of the source RoadGraph CRS.
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=27700)


def _strategic_route_pairs(
    graph: RoadGraph,
    anchors: tuple[dict[str, str], ...],
    context: gpd.GeoDataFrame,
    education: EducationAccessEvidenceLoad | None,
) -> tuple[tuple[str, str], ...]:
    """Collect every already-governed strategic route pair before routing.

    This repeats only the fail-closed admission checks used by destination
    preparation.  It creates no unit and therefore cannot make an invalid
    destination route observable; it simply lets RoadGraph batch valid starts.
    """

    pairs = {
        (left["routing_node"], right["routing_node"])
        for _root_spine_id, grouped in _group_anchors(anchors)
        for left, right in combinations(grouped, 2)
        if left["routing_node"] != right["routing_node"]
    }
    if education is not None:
        for admission in education.strategic_admission_records:
            site = _governed_destination_site(context, admission)
            if site is None:
                continue
            destination_node = _current_graph_destination_node(graph, site)
            if destination_node is None:
                continue
            anchor = _nearest_anchor(graph, site["geometry"], anchors)
            if anchor is not None and anchor["routing_node"] != destination_node:
                pairs.add((anchor["routing_node"], destination_node))
    return tuple(sorted(pairs))


def _official_a_road_chains(
    official_road_classification: gpd.GeoDataFrame | None,
) -> tuple[dict[str, object], ...]:
    """Collapse governed A-road links into structural junction-to-junction chains.

    The official RoadLink endpoints are the only topology used here.  A node is
    structural when it is a terminal/junction or when the official road number
    changes.  A pure degree-two loop has no natural cut, so every retained link
    is kept as a structural connection rather than silently reducing the loop.
    A non-motorway RoadLink is also retained when its exact endpoints join two
    different retained A-road components.  Those links are junction context;
    they do not become A-road evidence or acquire an A-road classification.
    """

    if official_road_classification is None or official_road_classification.empty:
        return ()
    required = {"official_feature_id", "official_classification", "geometry"}
    if not required.issubset(official_road_classification.columns):
        return ()
    frame = official_road_classification
    if frame.crs is None:
        return ()
    projected = frame.to_crs(27700)
    all_links: list[dict[str, object]] = []
    for index, row in projected.sort_values("official_feature_id").iterrows():
        geometry = row.geometry
        if not isinstance(geometry, LineString) or geometry.is_empty or len(geometry.coords) < 2:
            continue
        coordinates = tuple((float(x), float(y)) for x, y in geometry.coords)
        start = _coordinate_id(coordinates[0])
        end = _coordinate_id(coordinates[-1])
        if start == end:
            continue
        official_id = (
            str(row.get("official_feature_id"))
            if _present(row.get("official_feature_id"))
            else str(index)
        )
        classification = str(row.get("official_classification") or "unknown").strip().lower()
        road_number = (
            str(row.get("official_road_number")).strip()
            if _present(row.get("official_road_number"))
            else (official_id if classification == "a-road" else None)
        )
        source_id = str(row.get("source_id")) if _present(row.get("source_id")) else official_id
        evidence_id = (
            str(row.get("content_fingerprint"))
            if _present(row.get("content_fingerprint"))
            else official_id
        )
        all_links.append(
            {
                "link_id": official_id,
                "start": start,
                "end": end,
                "start_point": Point(coordinates[0]),
                "end_point": Point(coordinates[-1]),
                "geometry": geometry,
                "road_number": road_number,
                "official_classification": classification,
                "official_road_function": str(row.get("official_road_function") or "").strip(),
                "source_id": source_id,
                "evidence_id": evidence_id,
            }
        )
    links = [item for item in all_links if item["official_classification"] == "a-road"]
    if not links:
        return ()
    incident: dict[str, list[int]] = defaultdict(list)
    for index, link in enumerate(links):
        incident[str(link["start"])].append(index)
        incident[str(link["end"])].append(index)
    structural = {
        node_id
        for node_id, edge_ids in incident.items()
        if len(edge_ids) != 2
        or len({str(links[edge_id]["road_number"]) for edge_id in edge_ids}) != 1
    }
    # A closed degree-two road has no authoritative terminus.  Retain each
    # supplied link as its own connection so the loop cannot become a tree,
    # even when another disconnected A-road component has a junction.
    component_nodes: list[set[str]] = []
    remaining_nodes = set(incident)
    while remaining_nodes:
        component = {remaining_nodes.pop()}
        pending = list(component)
        while pending:
            node_id = pending.pop()
            neighbours = {
                str(
                    links[edge_id]["end"]
                    if str(links[edge_id]["start"]) == node_id
                    else links[edge_id]["start"]
                )
                for edge_id in incident[node_id]
            }
            new_nodes = neighbours - component
            component.update(new_nodes)
            remaining_nodes.difference_update(new_nodes)
            pending.extend(new_nodes)
        component_nodes.append(component)
    for component in component_nodes:
        if not structural.intersection(component):
            structural.update(component)

    component_ids_by_node: dict[str, str] = {}
    for component in component_nodes:
        link_ids = tuple(
            sorted(
                str(link["link_id"])
                for link in links
                if str(link["start"]) in component or str(link["end"]) in component
            )
        )
        component_id = _stable_id("a-road-backbone-component", link_ids)
        for node_id in component:
            component_ids_by_node[node_id] = component_id

    chains: list[dict[str, object]] = []
    visited: set[int] = set()
    for start_node in sorted(structural):
        for first_edge_id in sorted(incident[start_node]):
            if first_edge_id in visited:
                continue
            current_node = start_node
            edge_id = first_edge_id
            edge_indexes: list[int] = []
            oriented_geometries: list[LineString] = []
            while True:
                link = links[edge_id]
                visited.add(edge_id)
                edge_indexes.append(edge_id)
                geometry = link["geometry"]
                if str(link["start"]) == current_node:
                    oriented_geometries.append(geometry)
                else:
                    oriented_geometries.append(LineString(list(geometry.coords)[::-1]))
                next_node = (
                    str(link["end"]) if str(link["start"]) == current_node else str(link["start"])
                )
                if next_node in structural:
                    end_node = next_node
                    break
                next_edges = [
                    candidate
                    for candidate in incident[next_node]
                    if candidate != edge_id
                    and str(links[candidate]["road_number"]) == str(link["road_number"])
                ]
                if len(next_edges) != 1:
                    end_node = next_node
                    break
                current_node = next_node
                edge_id = next_edges[0]
            chain_links = [links[index] for index in edge_indexes]
            chains.append(
                {
                    "chain_id": _stable_id(
                        "a-road-backbone-chain",
                        (start_node, end_node, tuple(item["link_id"] for item in chain_links)),
                    ),
                    "start_node": start_node,
                    "end_node": end_node,
                    "start_point": next(
                        item["start_point"] if item["start"] == start_node else item["end_point"]
                        for item in chain_links
                        if item["start"] == start_node or item["end"] == start_node
                    ),
                    "end_point": next(
                        item["end_point"] if item["end"] == end_node else item["start_point"]
                        for item in reversed(chain_links)
                        if item["start"] == end_node or item["end"] == end_node
                    ),
                    "source_ids": tuple(sorted({str(item["source_id"]) for item in chain_links})),
                    "evidence_ids": tuple(
                        sorted({str(item["evidence_id"]) for item in chain_links})
                    ),
                    "geometries": tuple(oriented_geometries),
                    "road_number": str(chain_links[0]["road_number"]),
                    "official_classification": "a-road",
                    "official_road_function": str(chain_links[0]["official_road_function"]),
                    "component_ids": (component_ids_by_node[start_node],),
                    "component_endpoint_points": {
                        component_ids_by_node[start_node]: (
                            next(
                                item["start_point"]
                                if item["start"] == start_node
                                else item["end_point"]
                                for item in chain_links
                                if item["start"] == start_node or item["end"] == start_node
                            ),
                            next(
                                item["end_point"]
                                if item["end"] == end_node
                                else item["start_point"]
                                for item in reversed(chain_links)
                                if item["start"] == end_node or item["end"] == end_node
                            ),
                        )
                    },
                }
            )

    # Retain exact non-motorway junction links that bridge separate official
    # A-road components.  The endpoint identity is exact RoadLink topology;
    # no proximity join or inferred route is introduced here.
    context_chains: list[dict[str, object]] = []
    for link in all_links:
        if link["official_classification"] == "a-road":
            continue
        if str(link["official_road_function"]).casefold() == "motorway":
            continue
        start = str(link["start"])
        end = str(link["end"])
        start_component = component_ids_by_node.get(start)
        end_component = component_ids_by_node.get(end)
        if start_component is None or end_component is None or start_component == end_component:
            continue
        context_chains.append(
            {
                "chain_id": _stable_id(
                    "a-road-backbone-context",
                    (str(link["link_id"]), start, end),
                ),
                "start_node": start,
                "end_node": end,
                "start_point": link["start_point"],
                "end_point": link["end_point"],
                "source_ids": (str(link["source_id"]),),
                "evidence_ids": (str(link["evidence_id"]),),
                "geometries": (link["geometry"],),
                "road_number": link["road_number"],
                "official_classification": str(link["official_classification"]),
                "official_road_function": str(link["official_road_function"]),
                "component_ids": tuple(sorted((start_component, end_component))),
                "component_endpoint_points": {
                    start_component: (link["start_point"],),
                    end_component: (link["end_point"],),
                },
            }
        )
    return tuple(sorted((*chains, *context_chains), key=lambda item: str(item["chain_id"])))


def _backbone_chain_path(
    chain: Mapping[str, object],
    start_node: str,
    end_node: str,
) -> tuple[tuple[str, ...], tuple[LineString, ...]]:
    coordinates: list[tuple[float, float]] = []
    for geometry in chain["geometries"]:
        points = [(float(x), float(y)) for x, y in geometry.coords]
        coordinates.extend(points[1:] if coordinates and coordinates[-1] == points[0] else points)
    if len(coordinates) < 2:
        return (), ()
    if len(coordinates) == 2:
        geometry = LineString(coordinates)
        midpoint = geometry.interpolate(geometry.length / 2.0)
        coordinates.insert(1, (float(midpoint.x), float(midpoint.y)))
    chain_id = str(chain["chain_id"])
    internal_nodes = tuple(
        f"a-road-backbone-node:{chain_id}:{index}" for index in range(1, len(coordinates) - 1)
    )
    nodes = (start_node, *internal_nodes, end_node)
    segments = tuple(
        LineString([coordinates[index], coordinates[index + 1]])
        for index in range(len(coordinates) - 1)
    )
    return nodes, segments


def strategic_routable_network_with_a_road_backbone(
    routable_network: gpd.GeoDataFrame,
    official_road_classification: gpd.GeoDataFrame | None,
) -> gpd.GeoDataFrame:
    """Overlay governed A-road chains onto the current routable topology.

    Existing source rows remain untouched.  Split attachment rows expose
    official chain endpoints on source-edge interiors, and segmented exact
    official chain rows supply the governed A-road fallback used by the
    strategic candidate seam.  Chains whose endpoints cannot attach to a
    current source edge remain as standalone proposal geometry; only the
    optional source-edge attachment rows require current routable evidence.
    """

    chains = _official_a_road_chains(official_road_classification)
    if not chains or routable_network.empty:
        return routable_network
    if routable_network.crs is None:
        raise ValueError("A-road backbone overlay requires routable network CRS")

    projected = routable_network.to_crs(27700)
    source_endpoint_nodes: dict[str, str] = {}
    for _index, row in projected.iterrows():
        geometry = row.geometry
        if not isinstance(geometry, LineString) or len(geometry.coords) < 2:
            continue
        start = (
            str(row.get("u"))
            if _present(row.get("u"))
            else _coordinate_id(tuple(geometry.coords[0]))
        )
        end = (
            str(row.get("v"))
            if _present(row.get("v"))
            else _coordinate_id(tuple(geometry.coords[-1]))
        )
        source_endpoint_nodes.setdefault(_coordinate_id(geometry.coords[0]), start)
        source_endpoint_nodes.setdefault(_coordinate_id(geometry.coords[-1]), end)

    endpoint_points: dict[str, Point] = {}
    for chain in chains:
        for point in (chain["start_point"], chain["end_point"]):
            endpoint_points.setdefault(_coordinate_id(tuple(point.coords[0])), point)

    source_index = projected.geometry.sindex
    endpoint_nodes: dict[str, str | None] = {}
    source_interiors: dict[object, list[tuple[float, str, Point]]] = defaultdict(list)
    for point_id, point in sorted(endpoint_points.items()):
        if point_id in source_endpoint_nodes:
            endpoint_nodes[point_id] = source_endpoint_nodes[point_id]
            continue
        matches: list[tuple[float, object, float]] = []
        for position in source_index.query(
            point.buffer(URBAN_SPINE_TERMINUS_TOLERANCE_M), predicate="intersects"
        ):
            source_position = int(position)
            geometry = projected.geometry.iloc[source_position]
            if not isinstance(geometry, LineString):
                continue
            distance = float(geometry.distance(point))
            if distance > URBAN_SPINE_TERMINUS_TOLERANCE_M:
                continue
            distance_along = float(geometry.project(point))
            if distance_along <= 0.0 or distance_along >= float(geometry.length):
                continue
            matches.append((distance, source_position, distance_along))
        if not matches:
            # The official frame is also proposal evidence. Keep an exact
            # coordinate node when no current OSM attachment exists so the
            # governed chain remains available as a visible fallback.
            endpoint_nodes[point_id] = point_id
            continue
        _distance, source_position, distance_along = min(
            matches, key=lambda item: (item[0], str(item[1]))
        )
        endpoint_nodes[point_id] = point_id
        source_interiors[source_position].append((distance_along, point_id, point))

    attachment_rows: list[dict[str, object]] = []
    backbone_rows: list[dict[str, object]] = []
    for position, (index, row) in enumerate(projected.iterrows()):
        interior = source_interiors.get(position, ())
        geometry = row.geometry
        if not interior or not isinstance(geometry, LineString):
            continue
        source_edge_id = str(row.get("source_id") or row.get("osmid") or index)
        start_node = (
            str(row.get("u"))
            if _present(row.get("u"))
            else _coordinate_id(tuple(geometry.coords[0]))
        )
        end_node = (
            str(row.get("v"))
            if _present(row.get("v"))
            else _coordinate_id(tuple(geometry.coords[-1]))
        )
        boundaries = (
            [(0.0, start_node)]
            + [(distance, point_id) for distance, point_id, _point in sorted(interior)]
            + [(float(geometry.length), end_node)]
        )
        for segment_index, ((start, from_node), (end, to_node)) in enumerate(pairwise(boundaries)):
            segment = substring(geometry, start, end)
            if not isinstance(segment, LineString) or segment.is_empty:
                continue
            segment_row = row.to_dict()
            segment_row["u"] = from_node
            segment_row["v"] = to_node
            segment_row["geometry"] = (
                gpd.GeoSeries([segment], crs=27700).to_crs(routable_network.crs).iloc[0]
            )
            segment_row["source_id"] = (
                f"a-road-attachment:{source_edge_id}:{index!s}:{segment_index}"
            )
            segment_row["osmid"] = None
            segment_row["edge_id"] = None
            attachment_rows.append(segment_row)
            if not _truthy(row.get("oneway")):
                reverse = dict(segment_row)
                reverse["u"], reverse["v"] = to_node, from_node
                reverse["geometry"] = LineString(list(segment_row["geometry"].coords)[::-1])
                reverse["source_id"] = f"{segment_row['source_id']}:reverse"
                attachment_rows.append(reverse)

    for chain in chains:
        start_id = _coordinate_id(tuple(chain["start_point"].coords[0]))
        end_id = _coordinate_id(tuple(chain["end_point"].coords[0]))
        start_node = endpoint_nodes.get(start_id)
        end_node = endpoint_nodes.get(end_id)
        if start_node is None or end_node is None or start_node == end_node:
            continue
        path_nodes, segments = _backbone_chain_path(chain, start_node, end_node)
        if not path_nodes or not segments:
            continue
        for reverse in (False, True):
            directed_nodes = tuple(reversed(path_nodes)) if reverse else path_nodes
            directed_segments = (
                tuple(LineString(list(segment.coords)[::-1]) for segment in reversed(segments))
                if reverse
                else segments
            )
            for segment_index, (from_node, to_node, segment) in enumerate(
                zip(directed_nodes[:-1], directed_nodes[1:], directed_segments, strict=True)
            ):
                classification = str(chain.get("official_classification") or "unknown")
                if classification == "a-road":
                    highway = "primary"
                    ref = chain.get("road_number")
                elif classification == "b-road":
                    highway = "secondary"
                    ref = chain.get("road_number")
                elif classification == "classified-unnumbered":
                    highway = "tertiary"
                    ref = None
                else:
                    highway = "unclassified"
                    ref = None
                row = {
                    "source_id": (
                        f"a-road-backbone:{chain['chain_id']}:{segment_index}"
                        f"{':reverse' if reverse else ''}"
                    ),
                    "u": from_node,
                    "v": to_node,
                    "highway": highway,
                    "ref": ref,
                    "official_classification": classification,
                    "official_road_function": str(chain.get("official_road_function") or ""),
                    "oneway": False,
                    "geometry": gpd.GeoSeries([segment], crs=27700)
                    .to_crs(routable_network.crs)
                    .iloc[0],
                }
                backbone_rows.append(row)
    overlay_rows = [*backbone_rows, *attachment_rows]
    if not overlay_rows:
        return routable_network
    overlay = gpd.GeoDataFrame(overlay_rows, geometry="geometry", crs=routable_network.crs)
    return gpd.GeoDataFrame(
        pd.concat([routable_network, overlay], ignore_index=True, sort=False),
        geometry="geometry",
        crs=routable_network.crs,
    )


def _bound_backbone_node(graph: RoadGraph, point: Point) -> str | None:
    """Bind one official junction to an existing governed routing node."""

    exact_node_id = _coordinate_id(tuple(point.coords[0]))
    if graph.graph.has_node(exact_node_id):
        return exact_node_id
    graph_point = gpd.GeoSeries([point], crs=27700).to_crs(graph.crs).iloc[0]
    matches = graph.nodes_on_geometry(graph_point)
    return matches[0][0] if matches else None


def _backbone_path_nodes(
    graph: RoadGraph,
    option: RouteOption,
    edge_nodes_by_id: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[str, ...]:
    edge_ids = tuple(option.directed_edge_ids or option.edge_ids)
    by_edge_id = edge_nodes_by_id or {
        str(data.get("directed_edge_id")): (str(left), str(right))
        for left, right, data in graph.graph.edges(data=True)
    }
    pairs = tuple(by_edge_id[item] for item in edge_ids if item in by_edge_id)
    if not pairs:
        return ()
    return (pairs[0][0], *(pair[1] for pair in pairs))


def _a_road_backbone_units(
    profile: NetworkSelectionProfile,
    graph: RoadGraph,
    official_road_classification: gpd.GeoDataFrame | None,
    urban_spines: gpd.GeoDataFrame | None,
    context: gpd.GeoDataFrame,
    route_options: Mapping[tuple[str, str], Mapping[str, RouteOption | None]],
) -> tuple[tuple[PreparedStrategicCorridorUnit, ...], tuple[StrategicCorridorIssue, ...]]:
    """Prepare one finite Candidate Set for each governed A-road chain."""

    chains = _official_a_road_chains(official_road_classification)
    if not chains:
        return (), ()
    urban_geometry = None
    if urban_spines is not None and not urban_spines.empty:
        urban_a_roads = urban_spines[
            urban_spines.get(
                "official_classification",
                pd.Series("", index=urban_spines.index, dtype=object),
            ).eq("a-road")
        ]
        if not urban_a_roads.empty:
            urban_geometry = urban_a_roads.to_crs(27700).geometry.union_all()
    units: list[PreparedStrategicCorridorUnit] = []
    issues: list[StrategicCorridorIssue] = []
    bound_nodes = {
        str(chain["chain_id"]): (
            _bound_backbone_node(graph, chain["start_point"]),
            _bound_backbone_node(graph, chain["end_point"]),
        )
        for chain in chains
    }
    edge_nodes_by_id = {
        str(data.get("directed_edge_id")): (str(left), str(right))
        for left, right, data in graph.graph.edges(data=True)
    }

    # The exact official A-road graph can contain clipped or otherwise
    # disconnected components.  Context links only merge components when the
    # supplied junction ends are actually bound in this RoadGraph; otherwise
    # the existing per-connection issue remains the honest blocker.  Keep one
    # located required issue for each component group still outside the main
    # group, rather than silently treating every finite chain as continuity.
    component_ids = sorted(
        {
            str(component_id)
            for chain in chains
            for component_id in tuple(chain.get("component_ids", ()))
        }
    )
    parent = {component_id: component_id for component_id in component_ids}

    def find(component_id: str) -> str:
        root = component_id
        while parent[root] != root:
            root = parent[root]
        while parent[component_id] != component_id:
            next_id = parent[component_id]
            parent[component_id] = root
            component_id = next_id
        return root

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        parent[max(left_root, right_root)] = min(left_root, right_root)

    component_sizes: dict[str, int] = defaultdict(int)
    component_points: dict[str, list[Point]] = defaultdict(list)
    for chain in chains:
        chain_components = tuple(str(item) for item in chain.get("component_ids", ()))
        if str(chain.get("official_classification")) == "a-road":
            for component_id in chain_components:
                component_sizes[component_id] += len(tuple(chain.get("source_ids", ())))
        endpoint_points = chain.get("component_endpoint_points", {})
        if isinstance(endpoint_points, Mapping):
            for component_id, points in endpoint_points.items():
                component_points[str(component_id)].extend(
                    point for point in tuple(points) if isinstance(point, Point)
                )
        if len(chain_components) == 2:
            chain_start, chain_end = bound_nodes[str(chain["chain_id"])]
            if chain_start is not None and chain_end is not None and chain_start != chain_end:
                union(*chain_components)

    groups: dict[str, set[str]] = defaultdict(set)
    for component_id in component_ids:
        groups[find(component_id)].add(component_id)
    if groups:
        main_root = min(
            groups,
            key=lambda root: (
                -sum(component_sizes.get(component_id, 0) for component_id in groups[root]),
                root,
            ),
        )
        main_components = groups[main_root]
        main_point = min(
            (
                point
                for component_id in main_components
                for point in component_points.get(component_id, ())
            ),
            key=lambda point: (float(point.x), float(point.y)),
            default=None,
        )
        if main_point is not None:
            for root, members in sorted(groups.items()):
                if root == main_root:
                    continue
                component_point = min(
                    (
                        point
                        for component_id in members
                        for point in component_points.get(component_id, ())
                    ),
                    key=lambda point: (float(point.x), float(point.y)),
                    default=None,
                )
                if component_point is None:
                    continue
                issue_id = _stable_id(
                    "a-road-backbone-component-gap",
                    (tuple(sorted(members)), tuple(sorted(main_components))),
                )
                issues.append(
                    StrategicCorridorIssue(
                        StrategicCorridorUnitRole.A_ROAD_BACKBONE,
                        "a-road-backbone-component-unconnected",
                        "official A-road backbone component remains disconnected from "
                        "the retained main component; continuity is not inferred",
                        obligation_id=issue_id,
                        endpoints=(
                            _stable_id(
                                "a-road-backbone-component-endpoint",
                                (tuple(sorted(members)), "component"),
                            ),
                            _stable_id(
                                "a-road-backbone-component-endpoint",
                                (tuple(sorted(main_components)), "main"),
                            ),
                        ),
                        network_role=NetworkRole.INTERURBAN_SPINE.value,
                        endpoint_coordinates=(
                            (float(component_point.x), float(component_point.y)),
                            (float(main_point.x), float(main_point.y)),
                        ),
                        component_ids=tuple(sorted((*members, *main_components))),
                    )
                )
    for chain in chains:
        chain_id = str(chain["chain_id"])
        start_node, end_node = bound_nodes[chain_id]
        endpoints = (
            _stable_id("a-road-backbone-endpoint", (chain_id, "start")),
            _stable_id("a-road-backbone-endpoint", (chain_id, "end")),
        )
        if start_node is None or end_node is None or start_node == end_node:
            classification = str(chain.get("official_classification") or "unknown")
            connection_label = (
                "official A-road structural connection"
                if classification == "a-road"
                else f"official {classification} junction context connection"
            )
            issues.append(
                StrategicCorridorIssue(
                    StrategicCorridorUnitRole.A_ROAD_BACKBONE,
                    "a-road-backbone-endpoint-unbound",
                    f"{connection_label} cannot bind both ends to the current RoadGraph",
                    obligation_id=chain_id,
                    endpoints=endpoints,
                    network_role=NetworkRole.INTERURBAN_SPINE.value,
                    endpoint_coordinates=(
                        tuple(float(value) for value in chain["start_point"].coords[0]),
                        tuple(float(value) for value in chain["end_point"].coords[0]),
                    ),
                )
            )
            continue
        scope = (
            "urban"
            if urban_geometry is not None
            and any(geometry.intersects(urban_geometry) for geometry in chain["geometries"])
            else "rural"
        )
        forbidden_nodes = {
            node_id
            for other_chain_id, nodes in bound_nodes.items()
            if other_chain_id != chain_id
            for node_id in nodes
            if node_id is not None
        }
        pair = (start_node, end_node)
        exact_option = None
        exact_path_nodes, _exact_segments = _backbone_chain_path(chain, start_node, end_node)
        strategic_graph = graph._graph_for_role("strategic-spine", strategic_use=True)
        if exact_path_nodes and all(
            strategic_graph.has_edge(left, right) for left, right in pairwise(exact_path_nodes)
        ):
            exact_option = graph._option_from_nodes(
                list(exact_path_nodes),
                "strategic-spine",
                strategic_use=True,
            )
        candidate_set, records = _candidate_set(
            profile,
            graph,
            unit_role=StrategicCorridorUnitRole.A_ROAD_BACKBONE,
            endpoints=endpoints,
            mandatory_network_place_ids=(),
            start_node=start_node,
            end_node=end_node,
            source_ids=tuple(chain["source_ids"]),
            evidence_ids=tuple(chain["evidence_ids"]),
            context=context,
            strategic_destination_id=None,
            precomputed_options=route_options.get(pair),
            exact_backbone_option=exact_option,
            forbidden_interior_nodes=forbidden_nodes,
            edge_nodes_by_id=edge_nodes_by_id,
        )
        units.append(
            PreparedStrategicCorridorUnit(
                unit_id=chain_id,
                unit_role=StrategicCorridorUnitRole.A_ROAD_BACKBONE,
                candidate_set=candidate_set,
                endpoint_binding=StrategicCorridorEndpointBinding(
                    candidate_endpoints=candidate_set.endpoints,
                    routing_node_ids=(start_node, end_node),
                    network_place_ids=(),
                    strategic_destination_ids=(),
                ),
                anchor_connection_ids=(),
                anchor_obligation_ids=(),
                routing_start_node_id=start_node,
                routing_end_node_id=end_node,
                strategic_destination_id=None,
                site_id=None,
                access_point_evidence_ids=(),
                candidate_records=records,
                network_scope=scope,
                backbone_required=True,
                endpoint_coordinates=(
                    tuple(float(value) for value in chain["start_point"].coords[0]),
                    tuple(float(value) for value in chain["end_point"].coords[0]),
                ),
                backbone_component_ids=tuple(
                    sorted(str(item) for item in chain.get("component_ids", ()))
                ),
            )
        )
    return tuple(sorted(units, key=lambda item: item.unit_id)), tuple(
        sorted(issues, key=lambda item: item.obligation_id or item.detail)
    )


def _interurban_units(
    profile: NetworkSelectionProfile,
    graph: RoadGraph,
    anchors: tuple[dict[str, str], ...],
    context: gpd.GeoDataFrame,
    route_options: Mapping[tuple[str, str], Mapping[str, RouteOption | None]],
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
                precomputed_options=route_options.get((start, end)),
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
                        network_place_ids=tuple(sorted((left["place_id"], right["place_id"]))),
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
    route_options: Mapping[tuple[str, str], Mapping[str, RouteOption | None]],
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
            precomputed_options=route_options.get((anchor["routing_node"], destination_node)),
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
                    [anchor["access_connection_id"]] if anchor["access_connection_id"] else []
                ),
                anchor_obligation_ids=tuple(
                    [anchor["access_obligation_id"]] if anchor["access_obligation_id"] else []
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
    precomputed_options: Mapping[str, RouteOption | None] | None,
    exact_backbone_option: RouteOption | None = None,
    forbidden_interior_nodes: set[str] | frozenset[str] = frozenset(),
    edge_nodes_by_id: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[AlignmentCandidateSet, tuple[StrategicCorridorCandidateRecord, ...]]:
    _selected, options, _rationale = choose_alignment(
        graph,
        start_node,
        end_node,
        strategic_use=True,
        precomputed_options=precomputed_options,
    )
    strategic_destination_ids = (
        (strategic_destination_id,) if strategic_destination_id is not None else ()
    )
    generated: dict[tuple[object, ...], dict[str, object]] = {}
    options = [
        *([exact_backbone_option] if exact_backbone_option is not None else []),
        *options,
    ]
    exact_backbone_geometry = (
        _canonical_geometry(exact_backbone_option.geometry, graph.crs)
        if exact_backbone_option is not None
        else None
    )
    for option in options:
        path_nodes = _backbone_path_nodes(graph, option, edge_nodes_by_id)
        if forbidden_interior_nodes and any(
            node_id in forbidden_interior_nodes for node_id in path_nodes[1:-1]
        ):
            continue
        geometry = _canonical_geometry(option.geometry, graph.crs)
        if geometry is None:
            continue
        if exact_backbone_option is not None and (
            exact_backbone_geometry is None
            or not _geometry_endpoints_match(geometry, exact_backbone_geometry)
        ):
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
            provenance_ids=tuple(
                sorted(
                    {
                        _provenance_id(item)
                        for item in (*source_ids, *evidence_ids, *option.edge_ids)
                    }
                )
            ),
            topology_state=(
                CriterionState.SATISFIED if option.bidirectional else CriterionState.UNSATISFIED
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
                    routing_edge_ids=tuple(option.directed_edge_ids or option.edge_ids),
                    reverse_routing_edge_ids=tuple(
                        option.reverse_directed_edge_ids or option.reverse_edge_ids
                    ),
                    source_ids=tuple(sorted({*source_ids, *option.edge_ids})),
                    evidence_ids=evidence_ids,
                    generation_strategies=strategies,
                    generation_rationale=(
                        "retained exact physical route generated by " + ", ".join(strategies)
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
                "source_id": (_text(provenance.get("root_source_id")) or "unknown-source"),
                "evidence_id": (_text(provenance.get("root_evidence_id")) or "unknown-evidence"),
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
            or provenance.get("association_kind") != "colocated-direct-strategic-spine"
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
                "admission_record_version": _text(row.get("admission_record_version")),
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
    actual_edge_ids = graph.edge_ids_for_node(node_id)
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
        point = graph.projected_node(anchor["routing_node"])
        if point is None:
            continue
        distances.append(
            (
                float(point.distance(target)),
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


def _geometry_endpoints_match(
    candidate: CanonicalLineString,
    required: CanonicalLineString,
) -> bool:
    """Require a candidate's physical endpoints to meet the exact fallback ends."""

    candidate_line = candidate.as_shapely()
    required_line = required.as_shapely()
    candidate_ends = (Point(candidate_line.coords[0]), Point(candidate_line.coords[-1]))
    required_ends = (Point(required_line.coords[0]), Point(required_line.coords[-1]))
    tolerance = required.equivalence_profile.tolerance_m
    return all(
        left.distance(right) <= tolerance
        for left, right in zip(candidate_ends, required_ends, strict=True)
    ) or all(
        left.distance(right) <= tolerance
        for left, right in zip(candidate_ends, reversed(required_ends), strict=True)
    )


def _source_class(
    option: RouteOption,
    graph: RoadGraph,
    context: gpd.GeoDataFrame,
) -> CandidateSourceClass:
    if option.ncn_share > 0:
        return CandidateSourceClass.VERIFIED_EXISTING_ASSET
    edge_ids = set(option.edge_ids)
    refs = set(graph.references_for_edge_ids(edge_ids))
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
                    "geometry_fingerprints": {record.candidate.geometry_fingerprint},
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
                "source_snapshot": education.source_snapshot.model_dump(mode="json"),
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
