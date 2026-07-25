"""Community Connection compilation over the governed OSM network."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from dataclasses import dataclass, field
from itertools import combinations, pairwise
from numbers import Number

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely import get_parts
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point
from shapely.ops import nearest_points, split, unary_union

from satn.agents import AgentDecisionResolver, AgentRuntimeSource, CompilationGate
from satn.backbone import GAP_COLUMNS, assemble_backbone_outward
from satn.evidence import (
    PUBLIC_CYCLE_ROUTE_TYPES,
    STRATEGIC_CYCLE_ROUTE_TYPES,
    continuous_linework,
    empty_context,
    govern_network_scope_for_urban_communities,
    mark_ncn_edges,
)
from satn.identifiers import stable_id as _stable_id
from satn.models import (
    AccessPointStatus,
    AccessServiceStatus,
    AgentDecisionLedger,
    AgentFinding,
    AgentRecord,
    AreaConfig,
    DivergenceRecord,
    HumanInterventionRequest,
    NetworkScope,
    TrafficLight,
    UrbanClassificationStatus,
    WithheldDerivedFeatureReference,
)
from satn.routing import RoadGraph
from satn.school_street import assess_school_street_candidates
from satn.settlement import (
    assess_community_urban_eligibility,
    urban_settlement_form_profiles,
)
from satn.topography import (
    GradientThresholds,
    build_topography_profiles,
    empty_elevation_evidence,
)
from satn.urban import derive_urban_structure
from satn.urban_community import assess_urban_community_access, urban_community_gaps
from satn.urban_school import assess_urban_school_access

URBAN_A_ROAD_SOURCE_ALIGNMENT_TOLERANCE_M = 100.0
PUBLIC_ROUTE_TERMINUS_CLOSURE_MAX_M = 100.0


@dataclass
class CompiledNetwork:
    boundary: gpd.GeoDataFrame
    road_context: gpd.GeoDataFrame
    label_places: gpd.GeoDataFrame
    places: gpd.GeoDataFrame
    gaps: gpd.GeoDataFrame
    urban_spines: gpd.GeoDataFrame
    urban_classification_unknowns: gpd.GeoDataFrame
    urban_classification_status: UrbanClassificationStatus
    low_traffic_areas: gpd.GeoDataFrame
    low_traffic_area_portals: gpd.GeoDataFrame
    crossing_warnings: gpd.GeoDataFrame
    strategic_spines: gpd.GeoDataFrame
    access_obligations: gpd.GeoDataFrame
    spine_access_connections: gpd.GeoDataFrame
    spine_access_branches: gpd.GeoDataFrame
    branch_meeting_connections: gpd.GeoDataFrame
    cross_spine_connectors: gpd.GeoDataFrame
    a_road_spines: gpd.GeoDataFrame
    ncn_routes: gpd.GeoDataFrame
    schools: gpd.GeoDataFrame
    school_street_assessments: gpd.GeoDataFrame
    topography_profiles: gpd.GeoDataFrame
    gradient_sections: gpd.GeoDataFrame
    elevation_corroboration: gpd.GeoDataFrame
    elevation_evidence_status: str
    retail_centres: gpd.GeoDataFrame
    healthcare: gpd.GeoDataFrame
    agent_records: list[AgentRecord]
    criteria: dict[str, dict[str, TrafficLight]]
    network_units: list[dict[str, object]]
    atm_reference: gpd.GeoDataFrame | None
    divergence_records: list[DivergenceRecord]
    superseded_hypotheses: int
    human_intervention_requests: list[HumanInterventionRequest]
    compilation_diagnostics: dict[str, object]
    compilation_input_fingerprint: str = ""
    governed_input_fingerprint: str = ""
    snapshot_manifest_sha256: str = ""
    area_definition_sha256: str = ""
    decision_contract: str = "agent-decision-menu/v1"
    # This is the caller-supplied replay ledger, not the decisions produced by
    # direct runtime during this compilation.  Keeping the two separate makes
    # the input fingerprint independently reproducible.
    decision_ledger_input: dict[str, object] = field(default_factory=dict)
    accepted_decisions: list[dict[str, str]] = field(default_factory=list)

    @property
    def connection_count(self) -> int:
        """Number of authoritative Community/School access and branch-meeting edges."""
        return len(self.spine_access_connections) + len(self.branch_meeting_connections)

    @property
    def status(self) -> str:
        has_red = any(
            status == TrafficLight.RED
            for section in self.criteria.values()
            for status in section.values()
        )
        return "complete" if self.gaps.empty and not has_red else "reviewable"


@dataclass(frozen=True)
class _CrossSpineClosureResult:
    """Validated connector traversals plus point-only route-refinement gaps."""

    connectors: gpd.GeoDataFrame
    gaps: gpd.GeoDataFrame


class CrossSpineConnectorTraversalError(ValueError):
    """Expected per-connector evidence or traversal failure.

    These failures are safe to publish as point-only Route Refinement Findings.
    Structural compiler failures intentionally retain their original exception
    types and must stop compilation rather than being mistaken for evidence.
    """


def compile_network(
    config: AreaConfig,
    source: dict[str, gpd.GeoDataFrame],
    runtime: AgentRuntimeSource,
    *,
    governed_input_fingerprint: str = "",
    decision_resolver: AgentDecisionResolver | None = None,
) -> CompiledNetwork:
    places = source["places"].copy().sort_values("place_id").reset_index(drop=True)
    context = source.get("context", empty_context(source["network"].crs)).copy()
    communities = places[places["kind"] == "community"].copy()
    if len(communities) < 2:
        raise ValueError("a network requires at least two Communities")
    communities = assess_community_urban_eligibility(
        communities,
        source["network"],
        context,
        config.source,
    )
    places = gpd.GeoDataFrame(
        pd.concat(
            [communities, places[places["kind"] != "community"]],
            ignore_index=True,
            sort=False,
        ),
        geometry="geometry",
        crs=places.crs,
    ).sort_values("place_id")
    urban_communities = _urban_communities(communities)
    context = govern_network_scope_for_urban_communities(
        context,
        urban_communities,
        urban_scope_buffer_km=config.source.urban_scope_buffer_km,
    )
    gateways = places[places["kind"] == "cross_boundary_gateway"].copy()
    routable_network = mark_ncn_edges(source["network"], context)
    road_graph = RoadGraph(routable_network)
    gate = CompilationGate(
        runtime,
        config.compilation.agent,
        governed_input_fingerprint,
        decision_resolver,
    )
    strategic_spines = _strategic_spines(context)
    rural_communities = _rural_communities(communities)
    rural_schools = _rural_schools(context)
    backbone = assemble_backbone_outward(
        rural_communities,
        rural_schools,
        gateways,
        strategic_spines,
        road_graph,
        gate,
        config.compilation.max_connection_km,
        source.get("elevation_evidence", empty_elevation_evidence(road_graph.crs)),
        config.compilation.topography,
    )
    spine_access_connections = backbone.connections
    access_obligations = backbone.obligations
    access_obligations["network_scope"] = access_obligations["network_scope"].astype(object)
    rural_obligations = access_obligations["obligation_kind"].isin(["community", "school"])
    access_obligations.loc[rural_obligations, "network_scope"] = NetworkScope.RURAL.value
    spine_access_branches = backbone.branches
    branch_meeting_connections = backbone.meeting_connections
    cross_spine_connectors = backbone.cross_spine_connectors
    gaps = backbone.gaps.copy()
    agent_records = list(backbone.agent_records)
    crs = source["network"].crs
    official_road_classification = source.get("official_road_classification")
    urban = derive_urban_structure(
        urban_communities,
        source["network"],
        official_road_classification,
        context,
        source.get("observed_through_traffic"),
        source["boundary"],
    )
    urban_spines = urban.spines
    urban_classification_unknowns = urban.classification_unknowns
    low_traffic_areas = urban.low_traffic_areas
    low_traffic_area_portals = urban.low_traffic_area_portals
    connector_closure = _close_public_route_termini_with_gaps(
        cross_spine_connectors,
        urban_spines,
        strategic_spines,
        context,
        source["boundary"],
    )
    cross_spine_connectors = connector_closure.connectors
    if not connector_closure.gaps.empty:
        gaps = gpd.GeoDataFrame(
            pd.concat([gaps, connector_closure.gaps], ignore_index=True, sort=False),
            columns=GAP_COLUMNS,
            geometry="geometry",
            crs=crs,
        ).sort_values("connection_id")
    _reconcile_withheld_cross_spine_connectors(
        agent_records,
        backbone.cross_spine_connectors,
        cross_spine_connectors,
        connector_closure.gaps,
    )
    urban_community_access = assess_urban_community_access(
        urban_communities,
        source["network"],
        low_traffic_areas,
        low_traffic_area_portals,
        urban_spines,
        road_graph,
        attachment_maximum_m=config.source.urban_scope_buffer_km * 1000.0,
    )
    if not urban_community_access.empty:
        access_obligations = gpd.GeoDataFrame(
            pd.concat(
                [access_obligations, urban_community_access],
                ignore_index=True,
                sort=False,
            ),
            geometry="geometry",
            crs=crs,
        )
        urban_gaps = urban_community_gaps(urban_community_access, crs)
        if not urban_gaps.empty:
            gaps = gpd.GeoDataFrame(
                pd.concat([gaps, urban_gaps], ignore_index=True, sort=False),
                columns=GAP_COLUMNS,
                geometry="geometry",
                crs=crs,
            ).sort_values("connection_id")
    urban_school_access = assess_urban_school_access(
        _urban_schools(context),
        source["network"],
        low_traffic_areas,
        low_traffic_area_portals,
    )
    if not urban_school_access.empty:
        access_obligations = gpd.GeoDataFrame(
            pd.concat(
                [access_obligations, urban_school_access],
                ignore_index=True,
                sort=False,
            ),
            geometry="geometry",
            crs=crs,
        )
        urban_school_gaps = _urban_school_gaps(urban_school_access, crs)
        if not urban_school_gaps.empty:
            agent_records.extend(_review_urban_school_gaps(urban_school_gaps, gate))
            gaps = gpd.GeoDataFrame(
                pd.concat([gaps, urban_school_gaps], ignore_index=True, sort=False),
                columns=GAP_COLUMNS,
                geometry="geometry",
                crs=crs,
            ).sort_values("connection_id")
    school_street_assessments = assess_school_street_candidates(
        _in_scope_schools(context),
        source["network"],
        official_road_classification,
    )
    urban_classification_status = (
        UrbanClassificationStatus.GOVERNED_OFFICIAL
        if official_road_classification is not None
        and not official_road_classification.empty
        and urban_classification_unknowns.empty
        else UrbanClassificationStatus.EXPLICIT_UNKNOWN
    )
    topography_edge_frames = [
        ("strategic-spine", "spine_id", strategic_spines),
        (
            "spine-access-connection",
            "access_connection_id",
            spine_access_connections,
        ),
        (
            "branch-meeting-connection",
            "meeting_connection_id",
            branch_meeting_connections,
        ),
        (
            "cross-spine-connector",
            "cross_spine_connector_id",
            cross_spine_connectors,
        ),
        ("urban-spine", "structure_id", urban_spines),
    ]
    topography_profiles, gradient_sections = build_topography_profiles(
        topography_edge_frames,
        source.get("elevation_evidence", empty_elevation_evidence(crs)),
        thresholds=GradientThresholds(
            gentle=config.compilation.topography.gentle_max_pct,
            noticeable=config.compilation.topography.noticeable_max_pct,
            steep=config.compilation.topography.steep_max_pct,
            very_steep=config.compilation.topography.very_steep_max_pct,
        ),
        maximum_sample_spacing_m=(config.compilation.topography.maximum_sample_spacing_m),
        minimum_sustained_spacing_m=(config.compilation.topography.minimum_sustained_spacing_m),
    )
    crossing_warnings = _backbone_crossing_warnings(
        spine_access_connections, branch_meeting_connections
    )
    community_coverage, community_accounting = _community_coverage(
        communities,
        rural_communities,
        urban_communities,
        access_obligations,
    )
    urban_a_road_coverage, urban_a_road_coverage_status = _urban_a_road_spine_coverage(
        context,
        urban_spines,
    )
    network_units = _backbone_network_units(
        spine_access_branches,
        branch_meeting_connections,
        cross_spine_connectors,
    )
    criteria = {
        "connections": {
            "mandatory_checks": (TrafficLight.GREEN if gaps.empty else TrafficLight.RED),
            "distance_challenges": (
                TrafficLight.AMBER
                if _has_distance_challenge(spine_access_connections, branch_meeting_connections)
                else TrafficLight.GREEN
            ),
        },
        "network": {
            "community_coverage": community_accounting["total"],
            "rural_community_accounting": community_accounting["rural"],
            "urban_community_accounting": community_accounting["urban"],
            "community_accounting": community_accounting["total"],
            "authoritative_model": TrafficLight.GREEN,
            "legacy_pairwise_absent": TrafficLight.GREEN,
            "intervention_coverage": (
                TrafficLight.GREEN
                if _intervention_coverage_complete(
                    spine_access_connections,
                    branch_meeting_connections,
                )
                else TrafficLight.RED
            ),
        },
        "spine_network": {
            "governed_spine_evidence": (
                TrafficLight.GREEN if not strategic_spines.empty else TrafficLight.GREY
            ),
            "first_reachable_access": (
                TrafficLight.GREEN
                if not spine_access_connections.empty
                else TrafficLight.RED
                if not strategic_spines.empty
                else TrafficLight.GREY
            ),
            "all_access_obligations_resolved": (_access_obligation_status(access_obligations)),
            "school_access_state": _access_obligation_status(
                access_obligations[access_obligations["obligation_kind"] == "school"]
            ),
            "branch_provenance": (
                TrafficLight.GREEN
                if _branch_provenance_complete(spine_access_connections)
                else TrafficLight.RED
                if not spine_access_connections.empty
                else TrafficLight.GREY
            ),
            "degree_one_access_valid": (
                TrafficLight.GREEN
                if _degree_one_access_valid(access_obligations, spine_access_connections)
                else TrafficLight.RED
                if not access_obligations.empty
                else TrafficLight.GREY
            ),
            "gateway_coverage": (
                TrafficLight.GREEN
                if backbone.gateway_count == backbone.connected_gateway_count
                else TrafficLight.RED
                if backbone.gateway_count
                else TrafficLight.GREY
            ),
            "cross_spine_traversal": _cross_spine_status(
                spine_access_connections,
                branch_meeting_connections,
            ),
            "parallel_meetings_suppressed": (
                TrafficLight.GREEN
                if _meeting_root_pairs_unique(branch_meeting_connections)
                else TrafficLight.RED
            ),
            "a_road_intervention_assumptions": (
                TrafficLight.GREEN
                if _a_road_assumptions_complete(strategic_spines)
                else TrafficLight.RED
                if "a-road" in set(strategic_spines.get("spine_kind", []))
                else TrafficLight.GREY
            ),
        },
        "urban_network": {
            "official_road_classification": (
                TrafficLight.GREEN
                if urban_classification_status == UrbanClassificationStatus.GOVERNED_OFFICIAL
                else TrafficLight.GREY
            ),
            "official_main_road_spines": (
                TrafficLight.GREEN
                if not urban_spines.empty
                else TrafficLight.GREY
                if urban_classification_status == UrbanClassificationStatus.EXPLICIT_UNKNOWN
                else TrafficLight.RED
            ),
            "urban_a_road_evidence_coverage": urban_a_road_coverage_status,
            "ncn_kept_as_permeability_evidence": TrafficLight.GREEN,
            "candidate_low_traffic_areas": (
                TrafficLight.GREEN if not low_traffic_areas.empty else TrafficLight.GREY
            ),
            "stable_named_area_portals": (
                TrafficLight.GREEN
                if _candidate_area_portals_complete(low_traffic_areas, low_traffic_area_portals)
                else TrafficLight.GREY
                if low_traffic_areas.empty
                else TrafficLight.RED
            ),
            "area_permeability_without_centreline": (
                TrafficLight.GREEN
                if set(low_traffic_areas.get("permeability_representation", []))
                <= {"area-no-internal-centreline"}
                else TrafficLight.RED
            ),
            "urban_school_area_access": _access_obligation_status(urban_school_access),
            "urban_community_area_access": _access_obligation_status(urban_community_access),
        },
        "school_street_candidate_assessments": {
            "all_in_scope_schools_assessed": (
                TrafficLight.GREEN
                if len(school_street_assessments) == len(_in_scope_schools(context))
                else TrafficLight.RED
            ),
            "qualitative_not_probability": (
                TrafficLight.GREEN
                if not school_street_assessments.empty
                and school_street_assessments["qualification"]
                .str.contains("not scheme feasibility or calibrated probability")
                .all()
                else TrafficLight.GREY
                if school_street_assessments.empty
                else TrafficLight.RED
            ),
        },
        "topography": {
            "all_generated_edges_profiled": (
                TrafficLight.GREEN
                if len(topography_profiles)
                == sum(len(frame) for _, _, frame in topography_edge_frames)
                else TrafficLight.RED
            ),
            "elevation_evidence_coverage": (
                TrafficLight.GREEN
                if not topography_profiles.empty
                and (topography_profiles["evidence_status"] == "available").all()
                else TrafficLight.GREY
            ),
            "gradient_sections_published": (
                TrafficLight.GREEN if not gradient_sections.empty else TrafficLight.GREY
            ),
        },
        "atm_comparison": {"compared": TrafficLight.GREY},
    }
    compiled = CompiledNetwork(
        boundary=source["boundary"].copy(),
        road_context=source["network"].copy(),
        label_places=source.get("label_places", places).copy(),
        places=places,
        gaps=gaps,
        urban_spines=urban_spines,
        urban_classification_unknowns=urban_classification_unknowns,
        urban_classification_status=urban_classification_status,
        low_traffic_areas=low_traffic_areas,
        low_traffic_area_portals=low_traffic_area_portals,
        crossing_warnings=crossing_warnings,
        strategic_spines=strategic_spines,
        access_obligations=access_obligations,
        spine_access_connections=spine_access_connections,
        spine_access_branches=spine_access_branches,
        branch_meeting_connections=branch_meeting_connections,
        cross_spine_connectors=cross_spine_connectors,
        a_road_spines=_context_frame(context, "a-road-spine"),
        ncn_routes=context[context["feature_type"].isin(PUBLIC_CYCLE_ROUTE_TYPES)].copy(),
        schools=_context_frame(context, "school"),
        school_street_assessments=school_street_assessments,
        topography_profiles=topography_profiles,
        gradient_sections=gradient_sections,
        elevation_corroboration=source.get(
            "elevation_corroboration",
            gpd.GeoDataFrame(
                columns=[
                    "corroboration_id",
                    "source_id",
                    "osm_elevation",
                    "osm_incline",
                    "evidence_role",
                    "geometry",
                ],
                geometry="geometry",
                crs=crs,
            ),
        ),
        elevation_evidence_status=(
            "governed-national"
            if config.source.national_elevation is not None
            and not source.get("elevation_evidence", empty_elevation_evidence(crs)).empty
            else "explicit-unknown"
        ),
        retail_centres=_context_frame(context, "retail-centre"),
        healthcare=_context_frame(context, "healthcare"),
        agent_records=agent_records,
        criteria=criteria,
        network_units=network_units,
        atm_reference=None,
        divergence_records=[],
        superseded_hypotheses=sum(
            record.decision == "superseded" for record in agent_records
        ),
        human_intervention_requests=_human_intervention_requests(
            agent_records, config.compilation.agent.max_attempts
        ),
        compilation_diagnostics={
            **backbone.compilation_diagnostics,
            "community_coverage": community_coverage,
            "urban_settlement_form_profiles": urban_settlement_form_profiles(communities),
            "urban_a_road_spine_coverage": urban_a_road_coverage,
        },
    )
    # ``compile_network`` is also a supported public entry point.  Its output
    # must therefore carry the same exact decision wire contract as the
    # pipeline path, rather than relying on dataclass defaults which would
    # erase a direct-runtime audit before ``publish`` sees it.
    ledger = gate.decision_resolver.ledger
    compiled.decision_contract = ledger.decision_contract
    compiled.decision_ledger_input = ledger.model_dump(mode="json")
    compiled.accepted_decisions = AgentDecisionLedger.model_validate(
        {
            "decision_contract": ledger.decision_contract,
            "responses": [
                response.model_dump(mode="json")
                for response in gate.decision_resolver.accepted_responses
            ],
        }
    ).model_dump(mode="json")["responses"]
    return compiled


def _urban_school_gaps(
    obligations: gpd.GeoDataFrame,
    crs: object,
) -> gpd.GeoDataFrame:
    """Materialise every unserved urban School obligation as a visible Network Gap."""
    rows: list[dict[str, object]] = []
    for _, obligation in (
        obligations[obligations["service_status"] == AccessServiceStatus.NETWORK_GAP.value]
        .sort_values("obligation_id")
        .iterrows()
    ):
        school_id = str(obligation["school_id"])
        reason = str(obligation["service_rationale"])
        source_ids = json.loads(str(obligation.get("fabric_source_ids") or "[]"))
        access_source = obligation.get("access_point_source_id")
        if access_source is not None and not pd.isna(access_source):
            source_ids.append(str(access_source))
        rows.append(
            {
                "connection_id": _stable_id("urban-school-access-gap", obligation["obligation_id"]),
                "network_role": "school-access-gap",
                "from_place": school_id,
                "to_place": None,
                "from_place_name": obligation.get("name"),
                "to_place_name": None,
                "distance_km": None,
                "classification": "network-gap",
                "intervention_archetype": "urban permeability investigation",
                "geometry_semantics": (
                    "unserved urban School Access Point evidence; no residential "
                    "centreline is fabricated"
                ),
                "status": "gap",
                "selection_reason": reason,
                "agent_outcome": reason,
                "agent_attempt_count": 0,
                "agent_findings": json.dumps(
                    [
                        {
                            "code": str(obligation.get("finding") or "urban-access-gap"),
                            "severity": "blocking",
                            "message": reason,
                            "evidence_ids": sorted(set(source_ids)),
                        }
                    ],
                    sort_keys=True,
                ),
                "school_id": school_id,
                "school_kind": obligation.get("school_kind"),
                "access_point_status": obligation.get("access_point_status"),
                "access_point_source_id": access_source,
                "access_point_rationale": obligation.get("access_point_rationale"),
                "source_ids": json.dumps(sorted(set(source_ids))),
                "cache_status": "not-cacheable",
                "alignment_options": "[]",
                "criterion_endpoints": obligation.get("criterion_access_point"),
                "criterion_continuity": obligation.get("criterion_continuity"),
                "criterion_bidirectional": "grey",
                "criterion_distance": "grey",
                "topography_alternative_trigger": False,
                "topography_comparison_status": "not-evaluated",
                "topography_comparison_rationale": (
                    "Urban service is assessed through area permeability; no routed "
                    "alignment exists for topography comparison."
                ),
                "topography_original_role": None,
                "topography_selected_role": None,
                "geometry": MultiPoint([obligation.geometry]),
            }
        )
    return gpd.GeoDataFrame(rows, columns=GAP_COLUMNS, geometry="geometry", crs=crs)


def _review_urban_school_gaps(
    gaps: gpd.GeoDataFrame,
    gate: CompilationGate,
) -> list[AgentRecord]:
    """Apply the configured review policy to each deterministic urban School gap."""
    records: list[AgentRecord] = []
    criterion_columns = {
        "endpoints": "criterion_endpoints",
        "continuity": "criterion_continuity",
        "bidirectional": "criterion_bidirectional",
        "distance": "criterion_distance",
    }
    for index, row in gaps.sort_values("connection_id").iterrows():
        checks = {
            criterion: TrafficLight(str(row[column]))
            for criterion, column in criterion_columns.items()
        }
        governing_criterion = (
            "endpoints"
            if str(row.get("access_point_status")) == AccessPointStatus.UNRESOLVED.value
            else "continuity"
        )
        governing_status = checks[governing_criterion]
        evidence_ids = tuple(json.loads(str(row.get("source_ids") or "[]")))
        record = gate.evaluate(
            str(row["connection_id"]),
            {
                "from_place": str(row["from_place"]),
                "to_place": str(row.get("to_place") or ""),
                "selection_reason": str(row["selection_reason"]),
                "evidence_ids": evidence_ids,
                "checks_by_role": {
                    "school-access-gap": {
                        criterion: status.value for criterion, status in checks.items()
                    }
                },
            },
            "school-access-gap",
            ["school-access-gap"],
            governing_criterion=governing_criterion,
            governing_status=governing_status,
            deterministic_decision="gap",
        ).record
        record.network_role = str(row["network_role"])
        latest = record.attempts[-1] if record.attempts else None
        reviewed_findings = [
            *(
                finding.model_dump(mode="json")
                for finding in (latest.deterministic_findings if latest else [])
            ),
            *(
                finding.model_dump(mode="json")
                for finding in (latest.critique.findings if latest and latest.critique else [])
            ),
            *(
                finding.model_dump(mode="json")
                for finding in (latest.red_team.findings if latest and latest.red_team else [])
            ),
        ]
        existing_findings = json.loads(str(row.get("agent_findings") or "[]"))
        gaps.at[index, "agent_outcome"] = record.outcome_reason
        gaps.at[index, "agent_attempt_count"] = len(record.attempts)
        gaps.at[index, "agent_findings"] = json.dumps(
            [*existing_findings, *reviewed_findings],
            sort_keys=True,
        )
        records.append(record)
    return records


def _human_intervention_requests(
    records: list[AgentRecord],
    maximum_attempts: int,
) -> list[HumanInterventionRequest]:
    """Escalate only material ambiguity that survives the bounded revision loop."""
    requests: list[HumanInterventionRequest] = []
    for record in records:
        bounded_revision_finished = len(record.attempts) >= maximum_attempts or any(
            marker in record.outcome_reason.lower() for marker in ("no progress", "no-progress")
        )
        if record.decision != "gap" or not bounded_revision_finished or not record.attempts:
            continue
        latest = record.attempts[-1]
        findings = [
            *latest.findings,
            *latest.deterministic_findings,
            *(latest.critique.findings if latest.critique else []),
            *(latest.red_team.findings if latest.red_team else []),
        ]
        blocking = [
            finding
            for finding in findings
            if finding.severity == "blocking" and _is_material_ambiguity(finding)
        ]
        if not blocking:
            continue
        choices = sorted(
            {
                str(attempt.proposal.selected_role)
                for attempt in record.attempts
                if attempt.proposal and attempt.proposal.selected_role
            }
        )
        requests.append(
            HumanInterventionRequest(
                request_id=f"human-intervention-{hashlib.sha256(record.connection_id.encode()).hexdigest()[:12]}",
                connection_id=record.connection_id,
                reason=record.outcome_reason,
                attempted_revisions=[
                    attempt.model_dump(mode="json") for attempt in record.attempts
                ],
                unresolved_findings=[
                    finding.model_dump(mode="json") for finding in blocking
                ],
                missing_evidence=sorted(
                    {
                        str(evidence_id)
                        for finding in blocking
                        for evidence_id in finding.evidence_ids
                    }
                ),
                choices=choices,
                smallest_human_input=(
                    "Provide the missing governed evidence or select one of the listed "
                    "alignment roles; otherwise retain the visible Network Gap."
                ),
            )
        )
    return requests


def _is_material_ambiguity(finding: AgentFinding) -> bool:
    """Distinguish a missing human fact from an ordinary failed route criterion."""
    text = f"{finding.code} {finding.message}".lower()
    return any(
        marker in text
        for marker in ("ambiguous", "ambiguity", "missing-evidence", "missing evidence")
    )


def _candidate_area_portals_complete(
    areas: gpd.GeoDataFrame,
    portals: gpd.GeoDataFrame,
) -> bool:
    if areas.empty:
        return False
    expected = areas.set_index("structure_id")["portal_count"].astype(int).to_dict()
    actual = portals.groupby("area_id").size().to_dict() if not portals.empty else {}
    return expected == actual and all(str(name).strip() for name in portals.get("name", []))


def _crossing_warnings(connections: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    columns = ["warning_id", "connection_a", "connection_b", "status", "message", "geometry"]
    rows: list[dict[str, object]] = []
    for (_, left), (_, right) in combinations(connections.iterrows(), 2):
        if not left.geometry.crosses(right.geometry):
            continue
        intersection = left.geometry.intersection(right.geometry)
        points = list(intersection.geoms) if hasattr(intersection, "geoms") else [intersection]
        for point in points:
            if point.geom_type != "Point":
                continue
            pair = "::".join(sorted((left.connection_id, right.connection_id)))
            digest = hashlib.sha256(f"{pair}:{point.wkt}".encode()).hexdigest()[:10]
            rows.append(
                {
                    "warning_id": f"crossing-{digest}",
                    "connection_a": left.connection_id,
                    "connection_b": right.connection_id,
                    "status": "amber",
                    "message": "Routes cross without a declared shared Junction Node.",
                    "geometry": point,
                }
            )
    return gpd.GeoDataFrame(rows, columns=columns, geometry="geometry", crs=connections.crs)


def _backbone_crossing_warnings(
    access: gpd.GeoDataFrame,
    meetings: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Expose only undeclared geometric crossings in the authoritative linework."""
    columns = ["warning_id", "connection_a", "connection_b", "status", "message", "geometry"]
    rows = [
        {
            "connection_id": str(row["access_connection_id"]),
            "geometry": row.geometry,
        }
        for _, row in access.iterrows()
    ] + [
        {
            "connection_id": str(row["meeting_connection_id"]),
            "geometry": row.geometry,
        }
        for _, row in meetings.iterrows()
    ]
    crs = access.crs or meetings.crs
    if not rows:
        return gpd.GeoDataFrame([], columns=columns, geometry="geometry", crs=crs)
    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)
    return _crossing_warnings(frame)


def _backbone_network_units(
    branches: gpd.GeoDataFrame,
    meetings: gpd.GeoDataFrame,
    connectors: gpd.GeoDataFrame,
) -> list[dict[str, object]]:
    """Describe stable backbone units without reconstructing a peer-to-peer graph."""
    meeting_ids_by_root: dict[str, set[str]] = {}
    for _, meeting in meetings.iterrows():
        meeting_id = str(meeting["meeting_connection_id"])
        for root in (meeting["from_root_spine_id"], meeting["to_root_spine_id"]):
            meeting_ids_by_root.setdefault(str(root), set()).add(meeting_id)
    connector_ids_by_root: dict[str, set[str]] = {}
    for _, connector in connectors.iterrows():
        connector_id = str(connector["cross_spine_connector_id"])
        for root in (connector["from_root_spine_id"], connector["to_root_spine_id"]):
            connector_ids_by_root.setdefault(str(root), set()).add(connector_id)
    return [
        {
            "unit_id": str(row["branch_id"]),
            "root_spine_id": str(row["root_spine_id"]),
            "place_ids": json.loads(str(row["place_ids"])),
            "connection_ids": json.loads(str(row["connection_ids"])),
            "meeting_connection_ids": sorted(
                meeting_ids_by_root.get(str(row["root_spine_id"]), set())
            ),
            "cross_spine_connector_ids": sorted(
                connector_ids_by_root.get(str(row["root_spine_id"]), set())
            ),
        }
        for _, row in branches.sort_values("branch_id").iterrows()
    ]


def _context_frame(context: gpd.GeoDataFrame, feature_type: str) -> gpd.GeoDataFrame:
    return context[context["feature_type"] == feature_type].copy()


def _urban_a_road_spine_coverage(
    context: gpd.GeoDataFrame,
    urban_spines: gpd.GeoDataFrame,
) -> tuple[dict[str, int | float], TrafficLight]:
    """Measure governed urban A-road evidence represented by official A-road spines."""
    feature_type = context.get("feature_type", pd.Series("", index=context.index, dtype=object))
    network_scope = context.get("network_scope", pd.Series("", index=context.index, dtype=object))
    evidence = context[
        feature_type.eq("a-road-spine") & network_scope.eq(NetworkScope.URBAN.value)
    ].copy()
    evidence = evidence[evidence.geometry.notna() & ~evidence.geometry.is_empty]
    if evidence.empty:
        return (
            {
                "source_alignment_tolerance_m": (URBAN_A_ROAD_SOURCE_ALIGNMENT_TOLERANCE_M),
                "evidence_segment_count": 0,
                "total_km": 0.0,
                "unmatched_km": 0.0,
            },
            TrafficLight.GREY,
        )

    source_geometry = evidence.to_crs(27700).geometry.union_all()
    official_a_roads = urban_spines[
        urban_spines.get(
            "official_classification",
            pd.Series("", index=urban_spines.index, dtype=object),
        ).eq("a-road")
    ]
    unmatched_geometry = source_geometry
    if not official_a_roads.empty:
        represented_corridor = (
            official_a_roads.to_crs(27700)
            .geometry.buffer(URBAN_A_ROAD_SOURCE_ALIGNMENT_TOLERANCE_M)
            .union_all()
        )
        unmatched_geometry = source_geometry.difference(represented_corridor)
    unmatched_length_m = float(unmatched_geometry.length)
    diagnostics: dict[str, int | float] = {
        "source_alignment_tolerance_m": URBAN_A_ROAD_SOURCE_ALIGNMENT_TOLERANCE_M,
        "evidence_segment_count": len(evidence),
        "total_km": round(float(source_geometry.length) / 1000.0, 3),
        "unmatched_km": round(unmatched_length_m / 1000.0, 3),
    }
    return (
        diagnostics,
        TrafficLight.GREEN if unmatched_length_m <= 0.01 else TrafficLight.RED,
    )


def _close_public_route_termini(
    cross_spine_connectors: gpd.GeoDataFrame,
    urban_spines: gpd.GeoDataFrame,
    strategic_spines: gpd.GeoDataFrame,
    context: gpd.GeoDataFrame,
    council_boundary: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Return each connector's named-root traversal, with bounded endpoint closures.

    Cross-spine connectors are trees assembled from the meeting connection and
    both Community lineages.  Their public geometry can therefore include
    dangling association/access-prefix spurs.  Treating all public linework as
    one network made those spurs eligible for closure to any nearby primary.
    Keep the traversal local to each connector and bind both ends explicitly to
    the Strategic Spines recorded on that connector instead.
    """
    if cross_spine_connectors.empty:
        return cross_spine_connectors
    crs = cross_spine_connectors.crs
    projected_cross = cross_spine_connectors.to_crs(27700).copy()
    # These layers are already independently anchored.  They must not make an
    # unrelated dangling connector spur eligible for a root closure.
    del urban_spines, context, council_boundary
    roots = _named_strategic_spines(strategic_spines.to_crs(27700))
    for row_index, connector in projected_cross.sort_values(
        "cross_spine_connector_id"
    ).iterrows():
        connector_id = str(connector["cross_spine_connector_id"])
        _validate_cross_spine_connector_linework(connector, connector_id)
        # Validate compiler-generated lineage before entering the expected
        # per-connector traversal boundary.  A malformed provenance/source
        # record is a producer invariant failure, not evidence that an officer
        # can resolve as a route-refinement finding.
        provenance = _connector_provenance(connector, connector_id)
        _cross_spine_connector_source_ids(connector, connector_id, provenance)
        from_root_id, from_root = _connector_named_root(
            connector,
            connector_id,
            "from_root_spine_id",
            roots,
        )
        to_root_id, to_root = _connector_named_root(
            connector,
            connector_id,
            "to_root_spine_id",
            roots,
        )
        graph = _noded_connector_graph(
            connector.geometry,
            connector_id,
            (from_root, to_root),
        )
        from_node, to_node, path = _named_root_path(
            graph,
            connector_id,
            from_root_id,
            from_root,
            to_root_id,
            to_root,
        )
        from_point = Point(from_node)
        to_point = Point(to_node)
        _, from_target = nearest_points(from_point, from_root)
        _, to_target = nearest_points(to_point, to_root)
        from_distance_m = float(from_point.distance(from_target))
        to_distance_m = float(to_point.distance(to_target))
        route = _connector_path_geometry(graph, path, from_target, to_target)
        closures = [
            {"target_id": root_id, "distance_m": round(distance_m, 3)}
            for root_id, distance_m in (
                (from_root_id, from_distance_m),
                (to_root_id, to_distance_m),
            )
            if distance_m > 0.01
        ]
        if closures:
            provenance["terminus_closures"] = closures
        else:
            provenance.pop("terminus_closures", None)
        provenance["named_root_traversal"] = {
            "from_root_spine_id": from_root_id,
            "to_root_spine_id": to_root_id,
            "noded_segment_count": graph.number_of_edges(),
            "selected_segment_count": len(path) - 1,
            "pruned_segment_count": graph.number_of_edges() - (len(path) - 1),
            "from_root_distance_m": round(from_distance_m, 3),
            "to_root_distance_m": round(to_distance_m, 3),
        }
        projected_cross.at[row_index, "geometry"] = route
        projected_cross.at[row_index, "distance_km"] = round(route.length / 1000.0, 3)
        projected_cross.at[row_index, "provenance"] = json.dumps(provenance, sort_keys=True)
        projected_cross.at[row_index, "geometry_semantics"] = (
            f"{connector['geometry_semantics']}; noded named-root traversal between "
            "the connector's recorded Strategic Spines, with unrelated dangling "
            "linework pruned and bounded source-alignment closures only at named "
            "Strategic Spines"
        )
    return projected_cross.to_crs(crs)


def _close_public_route_termini_with_gaps(
    cross_spine_connectors: gpd.GeoDataFrame,
    urban_spines: gpd.GeoDataFrame,
    strategic_spines: gpd.GeoDataFrame,
    context: gpd.GeoDataFrame,
    council_boundary: gpd.GeoDataFrame,
) -> _CrossSpineClosureResult:
    """Keep safe named-root traversals and expose every unsafe one as a Network Gap.

    A single invalid aggregate connector must not suppress a regional deployment.
    The strict traversal function remains the authority for the 100 m closure
    budget; this boundary only converts its data-safety failures into point-only
    Route Refinement Findings, never into fabricated connector linework.
    """
    if cross_spine_connectors.empty:
        return _CrossSpineClosureResult(
            connectors=cross_spine_connectors,
            gaps=_empty_cross_spine_connector_gaps(cross_spine_connectors.crs),
        )
    # Generated geometry is compiler schema, not route evidence.  Validate it
    # before the recoverable per-connector boundary so malformed output cannot
    # be relabelled as an officer-resolvable Route Refinement Finding.
    _validate_named_strategic_spine_linework(strategic_spines)
    for _, connector in cross_spine_connectors.sort_values(
        "cross_spine_connector_id"
    ).iterrows():
        _validate_cross_spine_connector_linework(
            connector,
            str(connector["cross_spine_connector_id"]),
        )
    closed: list[gpd.GeoDataFrame] = []
    gap_rows: list[dict[str, object]] = []
    for row_index, connector in cross_spine_connectors.sort_values(
        "cross_spine_connector_id"
    ).iterrows():
        try:
            closed.append(
                _close_public_route_termini(
                    cross_spine_connectors.loc[[row_index]],
                    urban_spines,
                    strategic_spines,
                    context,
                    council_boundary,
                )
            )
        except CrossSpineConnectorTraversalError as error:
            gap_rows.append(_cross_spine_connector_gap(connector, str(error)))
    connectors = (
        gpd.GeoDataFrame(
            pd.concat(closed, ignore_index=True, sort=False),
            geometry="geometry",
            crs=cross_spine_connectors.crs,
        ).sort_values("cross_spine_connector_id")
        if closed
        else cross_spine_connectors.iloc[0:0].copy()
    )
    gaps = (
        gpd.GeoDataFrame(
            gap_rows,
            columns=GAP_COLUMNS,
            geometry="geometry",
            crs=cross_spine_connectors.crs,
        ).sort_values("connection_id")
        if gap_rows
        else _empty_cross_spine_connector_gaps(cross_spine_connectors.crs)
    )
    return _CrossSpineClosureResult(connectors=connectors, gaps=gaps)


def _empty_cross_spine_connector_gaps(crs: object) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(columns=GAP_COLUMNS, geometry="geometry", crs=crs)


def _cross_spine_connector_gap(
    connector: pd.Series,
    error: str,
) -> dict[str, object]:
    """Represent an unsafe connector as endpoint evidence, not a route line."""
    connector_id = str(connector["cross_spine_connector_id"])
    provenance = _connector_provenance(connector, connector_id)
    source_ids = _cross_spine_connector_source_ids(connector, connector_id, provenance)
    rationale = (
        f"{error}. Aggregate connector omitted; Route Refinement Finding requires "
        "a verified traversable alignment to the recorded named Strategic Spines."
    )
    return {
        "connection_id": _stable_id("cross-spine-connector-gap", connector_id),
        "network_role": "cross-spine-connector-gap",
        "from_place": _connector_text(connector.get("from_root_spine_id")),
        "to_place": _connector_text(connector.get("to_root_spine_id")),
        "from_place_name": _connector_text(connector.get("from_root_spine_name")),
        "to_place_name": _connector_text(connector.get("to_root_spine_name")),
        "distance_km": connector.get("distance_km"),
        "classification": "network-gap",
        "intervention_archetype": "cross-spine route refinement",
        "geometry_semantics": (
            "point-only termini of an unsafe cross-spine connector; aggregate "
            "connector linework is withheld pending route refinement"
        ),
        "status": "gap",
        "selection_reason": rationale,
        "agent_outcome": "route-refinement-required",
        "agent_attempt_count": 0,
        "agent_findings": json.dumps(
            [
                {
                    "code": "cross-spine-named-root-traversal-invalid",
                    "severity": "blocking",
                    "message": rationale,
                    "evidence_ids": source_ids,
                }
            ],
            sort_keys=True,
        ),
        "agent_decision_request_id": None,
        "agent_decision_choice_id": None,
        "agent_decision_action": None,
        "agent_decision_responder_mode": None,
        "school_id": None,
        "school_kind": None,
        "access_point_status": None,
        "access_point_source_id": None,
        "access_point_rationale": None,
        "source_ids": json.dumps(source_ids),
        "cache_status": "not-cacheable",
        "alignment_options": "[]",
        "criterion_endpoints": TrafficLight.RED.value,
        "criterion_continuity": TrafficLight.RED.value,
        "criterion_bidirectional": TrafficLight.GREY.value,
        "criterion_distance": TrafficLight.RED.value,
        "topography_alternative_trigger": False,
        "topography_comparison_status": "not-evaluated",
        "topography_comparison_rationale": (
            "No aggregate connector is published, so topography comparison cannot "
            "be assessed until route refinement supplies a traversable alignment."
        ),
        "topography_original_role": "cross-spine-connector",
        "topography_selected_role": None,
        "geometry": _cross_spine_connector_gap_geometry(connector.geometry),
    }


def _cross_spine_connector_source_ids(
    connector: pd.Series,
    connector_id: str,
    provenance: dict[str, object],
) -> list[str]:
    """Return well-formed generated source lineage, never a best-effort parse."""
    raw_source_ids = connector.get("source_ids")
    if raw_source_ids is None or (isinstance(raw_source_ids, Number) and pd.isna(raw_source_ids)):
        source_ids: list[object] = []
    elif isinstance(raw_source_ids, list):
        source_ids = raw_source_ids
    else:
        try:
            decoded = json.loads(str(raw_source_ids))
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(
                f"cross-spine connector {connector_id} has invalid source_ids lineage"
            ) from error
        if not isinstance(decoded, list):
            raise ValueError(
                f"cross-spine connector {connector_id} source_ids lineage is not a list"
            )
        source_ids = decoded
    provenance_source_ids = provenance.get("source_ids", [])
    if not isinstance(provenance_source_ids, list):
        raise ValueError(
            f"cross-spine connector {connector_id} provenance source_ids lineage is not a list"
        )
    values = [*source_ids, *provenance_source_ids]
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(
            f"cross-spine connector {connector_id} source_ids lineage contains "
            "an invalid identifier"
        )
    return sorted(set(values))


def _connector_text(value: object) -> str | None:
    if value is None or (isinstance(value, Number) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _cross_spine_connector_gap_geometry(geometry: object) -> MultiPoint:
    """Return only observed termini so a gap never depicts an invented route."""
    try:
        linework = continuous_linework(geometry)
    except (AttributeError, TypeError):
        linework = []
    points = {
        tuple(line.coords[0])
        for line in linework
        if len(line.coords) >= 2
    } | {
        tuple(line.coords[-1])
        for line in linework
        if len(line.coords) >= 2
    }
    if points:
        return MultiPoint(sorted(points))
    if geometry is not None and not getattr(geometry, "is_empty", True):
        return MultiPoint([geometry.representative_point()])
    return MultiPoint()


def _reconcile_withheld_cross_spine_connectors(
    records: list[AgentRecord],
    assembled_connectors: gpd.GeoDataFrame,
    published_connectors: gpd.GeoDataFrame,
    connector_gaps: gpd.GeoDataFrame,
) -> None:
    """Keep the agent registry exact while retaining the omitted feature's audit trail.

    A meeting decision can be accepted while its aggregate connector later fails
    the independent named-root traversal check.  ``derived_features`` is the
    published authoritative registry, so leaving that connector there would
    make the publication falsely claim a line that has been deliberately
    withheld.  Preserve the history separately on the originating record.
    """
    assembled_ids = _unique_cross_spine_connector_ids(
        assembled_connectors,
        "assembled",
    )
    published_ids = _unique_cross_spine_connector_ids(
        published_connectors,
        "published",
    )
    if not published_ids <= assembled_ids:
        unexpected = sorted(published_ids - assembled_ids)
        raise ValueError(
            "published cross-spine connector is absent from the assembled registry: "
            f"{unexpected[0]}"
        )
    withheld_ids = assembled_ids - published_ids
    gap_ids_by_connector = _cross_spine_connector_gap_ids(
        connector_gaps,
        withheld_ids,
    )
    accepted_records = _validate_cross_spine_derived_registry(records, assembled_ids)

    for record in accepted_records:
        retained = []
        for reference in record.derived_features:
            if (
                reference.network_role != "cross-spine-connector"
                or reference.feature_id not in withheld_ids
            ):
                retained.append(reference)
                continue
            reason = (
                "Aggregate connector withheld after named-root traversal failed; "
                "the associated point-only Route Refinement Finding is published instead."
            )
            record.withheld_derived_features.append(
                WithheldDerivedFeatureReference(
                    feature_id=reference.feature_id,
                    network_role=reference.network_role,
                    reason=reason,
                    finding_id=gap_ids_by_connector[reference.feature_id],
                )
            )
        record.derived_features = retained
    _validate_withheld_cross_spine_connector_bijection(
        records,
        published_ids,
        withheld_ids,
        gap_ids_by_connector,
    )


def _unique_cross_spine_connector_ids(
    connectors: gpd.GeoDataFrame,
    registry_name: str,
) -> set[str]:
    """Return one nonblank identifier per connector, rejecting producer ambiguity."""
    if connectors.empty:
        return set()
    if "cross_spine_connector_id" not in connectors:
        raise ValueError(f"{registry_name} cross-spine connector registry has no identifier column")
    identifiers = [_connector_text(value) for value in connectors["cross_spine_connector_id"]]
    if any(identifier is None for identifier in identifiers):
        raise ValueError(f"{registry_name} cross-spine connector registry has a blank identifier")
    ids = [str(identifier) for identifier in identifiers]
    duplicates = sorted(identifier for identifier in set(ids) if ids.count(identifier) != 1)
    if duplicates:
        raise ValueError(
            f"{registry_name} cross-spine connector registry has duplicate identifier: "
            f"{duplicates[0]}"
        )
    return set(ids)


def _cross_spine_connector_gap_ids(
    connector_gaps: gpd.GeoDataFrame,
    withheld_ids: set[str],
) -> dict[str, str]:
    """Bind every omitted connector to its one generated finding identifier."""
    if connector_gaps.empty:
        if withheld_ids:
            raise ValueError("withheld cross-spine connector has no Route Refinement Finding")
        return {}
    if "connection_id" not in connector_gaps or "network_role" not in connector_gaps:
        raise ValueError("cross-spine connector gaps omit required identifiers or roles")
    gap_ids = [_connector_text(value) for value in connector_gaps["connection_id"]]
    if any(gap_id is None for gap_id in gap_ids):
        raise ValueError("cross-spine connector gaps contain a blank finding identifier")
    normalized_gap_ids = [str(gap_id) for gap_id in gap_ids]
    duplicates = sorted(
        gap_id for gap_id in set(normalized_gap_ids) if normalized_gap_ids.count(gap_id) != 1
    )
    if duplicates:
        raise ValueError(
            "cross-spine connector gaps duplicate finding identifier: "
            f"{duplicates[0]}"
        )
    if set(connector_gaps["network_role"].astype(str)) != {"cross-spine-connector-gap"}:
        raise ValueError("connector closure emitted a non-cross-spine Route Refinement Finding")
    expected = {
        connector_id: _stable_id("cross-spine-connector-gap", connector_id)
        for connector_id in withheld_ids
    }
    if set(normalized_gap_ids) != set(expected.values()):
        raise ValueError(
            "withheld connector and Route Refinement Finding identifiers are not bijective"
        )
    return expected


def _validate_cross_spine_derived_registry(
    records: list[AgentRecord],
    assembled_ids: set[str],
) -> list[AgentRecord]:
    """Require accepted meeting decisions to identify each connector exactly once."""
    accepted_records = [record for record in records if record.decision == "accept"]
    for record in records:
        if record.decision == "accept":
            continue
        references = [
            *(
                reference
                for reference in record.derived_features
                if reference.network_role == "cross-spine-connector"
            ),
            *(
                reference
                for reference in record.withheld_derived_features
                if reference.network_role == "cross-spine-connector"
            ),
        ]
        if references:
            raise ValueError(
                "non-accepted AgentRecord cannot establish or withhold cross-spine "
                f"connector derived feature: {references[0].feature_id}"
            )
    references = [
        reference
        for record in accepted_records
        for reference in record.derived_features
        if reference.network_role == "cross-spine-connector"
    ]
    ids = [reference.feature_id for reference in references]
    duplicates = sorted(identifier for identifier in set(ids) if ids.count(identifier) != 1)
    if duplicates:
        raise ValueError(
            "cross-spine connector derived feature registry has duplicate identifier: "
            f"{duplicates[0]}"
        )
    if set(ids) != assembled_ids:
        raise ValueError(
            "cross-spine connector derived feature registry differs from the assembled registry"
        )
    return accepted_records


def _validate_withheld_cross_spine_connector_bijection(
    records: list[AgentRecord],
    published_ids: set[str],
    withheld_ids: set[str],
    gap_ids_by_connector: dict[str, str],
) -> None:
    """Prove withheld connector, finding and audit references are one-to-one."""
    retained = [
        reference
        for record in records
        if record.decision == "accept"
        for reference in record.derived_features
        if reference.network_role == "cross-spine-connector"
    ]
    retained_ids = [reference.feature_id for reference in retained]
    if set(retained_ids) != published_ids or len(retained_ids) != len(published_ids):
        raise ValueError("published cross-spine connector registry is not exact after withholding")

    withheld = [
        reference
        for record in records
        if record.decision == "accept"
        for reference in record.withheld_derived_features
        if reference.network_role == "cross-spine-connector"
    ]
    withheld_ids_in_records = [reference.feature_id for reference in withheld]
    duplicate_ids = sorted(
        identifier
        for identifier in set(withheld_ids_in_records)
        if withheld_ids_in_records.count(identifier) != 1
    )
    if duplicate_ids:
        raise ValueError(
            "withheld cross-spine connector registry has duplicate identifier: "
            f"{duplicate_ids[0]}"
        )
    if set(withheld_ids_in_records) != withheld_ids:
        raise ValueError("withheld cross-spine connector registry differs from omitted connectors")
    finding_ids = [reference.finding_id for reference in withheld]
    duplicate_findings = sorted(
        finding_id for finding_id in set(finding_ids) if finding_ids.count(finding_id) != 1
    )
    if duplicate_findings:
        raise ValueError(
            "withheld cross-spine connector registry reuses Route Refinement Finding: "
            f"{duplicate_findings[0]}"
        )
    for reference in withheld:
        if reference.finding_id != gap_ids_by_connector.get(reference.feature_id):
            raise ValueError(
                "withheld cross-spine connector references the wrong Route Refinement Finding: "
                f"{reference.feature_id}"
            )


def _named_strategic_spines(strategic_spines: gpd.GeoDataFrame) -> dict[str, object]:
    _validate_named_strategic_spine_linework(strategic_spines)
    roots: dict[str, object] = {}
    for _, spine in strategic_spines.sort_values("spine_id").iterrows():
        spine_id = str(spine["spine_id"])
        if spine_id in roots:
            raise ValueError(f"Strategic Spine {spine_id!r} is not uniquely identified")
        roots[spine_id] = spine.geometry
    return roots


def _validate_named_strategic_spine_linework(strategic_spines: gpd.GeoDataFrame) -> None:
    """Reject malformed compiler-produced root geometry before traversal."""
    if strategic_spines.empty:
        return
    for _, spine in strategic_spines.sort_values("spine_id").iterrows():
        spine_id = str(spine["spine_id"])
        geometry = spine.geometry
        if not _is_valid_nonempty_linework(geometry):
            raise ValueError(
                f"Strategic Spine {spine_id!r} has invalid geometry; expected a non-empty "
                "valid LineString or MultiLineString"
            )


def _validate_cross_spine_connector_linework(
    connector: pd.Series,
    connector_id: str,
) -> None:
    """Reject malformed aggregate connector geometry before traversal."""
    if not _is_valid_nonempty_linework(connector.geometry):
        raise ValueError(
            f"cross-spine connector {connector_id} has invalid geometry; expected a non-empty "
            "valid LineString or MultiLineString"
        )


def _is_valid_nonempty_linework(geometry: object) -> bool:
    return bool(
        isinstance(geometry, (LineString, MultiLineString))
        and not geometry.is_empty
        and geometry.is_valid
    )


def _connector_named_root(
    connector: pd.Series,
    connector_id: str,
    column: str,
    roots: dict[str, object],
) -> tuple[str, object]:
    value = connector.get(column)
    if value is None or pd.isna(value) or not str(value).strip():
        raise ValueError(
            f"cross-spine connector {connector_id} has no {column}"
        )
    root_id = str(value)
    if root_id not in roots:
        raise ValueError(
            f"cross-spine connector {connector_id} names missing Strategic Spine {root_id}"
        )
    return root_id, roots[root_id]


def _noded_connector_graph(
    geometry: object,
    connector_id: str,
    named_roots: tuple[object, object],
) -> nx.Graph:
    linework = [line for line in continuous_linework(geometry) if line.length > 0.01]
    if not linework:
        raise CrossSpineConnectorTraversalError(
            f"cross-spine connector {connector_id} has no routed linework"
        )
    graph = nx.Graph()
    noded = unary_union(linework)
    segments = sorted(
        (
            segment
            for segment in get_parts(noded)
            if isinstance(segment, LineString) and segment.length > 0.01
        ),
        key=_canonical_connector_segment_signature,
    )
    for raw_segment in segments:
        segment_parts = _split_connector_segment_at_named_roots(raw_segment, named_roots)
        for segment in segment_parts:
            _add_connector_segment(graph, segment)
    if graph.number_of_edges() == 0:
        raise CrossSpineConnectorTraversalError(
            f"cross-spine connector {connector_id} has no usable routed segments"
        )
    return graph


def _split_connector_segment_at_named_roots(
    segment: LineString,
    named_roots: tuple[object, object],
) -> list[LineString]:
    points = [
        point
        for root in named_roots
        for point in _point_parts(segment.intersection(root))
        if point.distance(Point(segment.coords[0])) > 0.01
        and point.distance(Point(segment.coords[-1])) > 0.01
    ]
    if not points:
        return [segment]
    splitter = MultiPoint(sorted(points, key=lambda point: point.wkb_hex))
    return [
        part
        for part in get_parts(split(segment, splitter))
        if isinstance(part, LineString) and part.length > 0.01
    ]


def _point_parts(geometry: object) -> list[Point]:
    if getattr(geometry, "is_empty", False):
        return []
    if isinstance(geometry, Point):
        return [geometry]
    if isinstance(geometry, LineString):
        # Collinear overlaps have line, rather than point, intersections.  The
        # overlap boundaries are the nodes needed to keep root-to-root paths
        # simple and prevent endpoint closures from backtracking over them.
        return [Point(geometry.coords[0]), Point(geometry.coords[-1])]
    if hasattr(geometry, "geoms"):
        return [point for part in geometry.geoms for point in _point_parts(part)]
    return []


def _add_connector_segment(graph: nx.Graph, segment: LineString) -> None:
    signature = _canonical_connector_segment_signature(segment)
    start = signature[0]
    end = signature[-1]
    if start == end:
        return
    existing = graph.get_edge_data(start, end)
    if existing is None or (segment.length, signature) < (
        existing["weight"],
        existing["signature"],
    ):
        graph.add_edge(
            start,
            end,
            geometry=LineString(signature),
            signature=signature,
            weight=segment.length,
        )


def _canonical_connector_segment_signature(
    segment: LineString,
) -> tuple[tuple[float, ...], ...]:
    """Return an undirected segment identity independent of input orientation."""
    coordinates = tuple(tuple(coordinate) for coordinate in segment.coords)
    return min(coordinates, tuple(reversed(coordinates)))


def _deterministic_weighted_path(
    graph: nx.Graph,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    distances: dict[tuple[float, float], float] | None = None,
) -> list[tuple[float, float]]:
    """Return a weighted shortest path with a canonical full-route tie-break."""
    if distances:
        _validate_weighted_path_edges(graph)
    else:
        distances = _weighted_distances(graph, start)
    if end not in distances:
        raise nx.NetworkXNoPath(f"No path between {start!r} and {end!r}")

    # Every edge has a positive length, so the distance-labelled shortest-path
    # graph is acyclic.  Work backwards from the destination to find the
    # portion of it that can reach the destination, then select the lowest
    # next node at each step.  That greedily reconstructs the lexicographically
    # lowest complete node path without copying a growing path into each
    # Dijkstra queue state.  This is also the full-route tie-break previously
    # encoded in ``node_signature``.  ``edge_signature`` only breaks a tie
    # after an identical node path; a simple ``nx.Graph`` has one such edge.
    viable_nodes = {end}
    pending = [end]
    while pending:
        node = pending.pop()
        node_distance = distances[node]
        for neighbour in graph.neighbors(node):
            neighbour_distance = distances.get(neighbour)
            if neighbour_distance is None:
                continue
            if neighbour_distance + float(graph.edges[neighbour, node]["weight"]) != node_distance:
                continue
            if neighbour not in viable_nodes:
                viable_nodes.add(neighbour)
                pending.append(neighbour)

    path = [start]
    node = start
    while node != end:
        node_distance = distances[node]
        next_nodes = [
            neighbour
            for neighbour in graph.neighbors(node)
            if neighbour in viable_nodes
            and node_distance + float(graph.edges[node, neighbour]["weight"])
            == distances.get(neighbour)
        ]
        if not next_nodes:
            raise nx.NetworkXNoPath(f"No path between {start!r} and {end!r}")
        node = min(next_nodes)
        path.append(node)
    return path


def _validate_weighted_path_edges(graph: nx.Graph) -> None:
    """Require the positive, finite weights assumed by path reconstruction."""
    for source, destination, attributes in graph.edges(data=True):
        raw_weight = attributes.get("weight")
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            weight = float("nan")
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError(
                f"connector graph edge {source!r} -> {destination!r} has invalid "
                f"weight {raw_weight!r}; expected a finite, strictly positive number"
            )


def _weighted_distances(
    graph: nx.Graph,
    start: tuple[float, float],
) -> dict[tuple[float, float], float]:
    """Return weighted distances using constant-size Dijkstra queue states."""
    _validate_weighted_path_edges(graph)
    queue: list[tuple[float, tuple[float, float]]] = [(0.0, start)]
    distances = {start: 0.0}
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        for neighbour in sorted(graph.neighbors(node)):
            next_distance = distance + float(graph.edges[node, neighbour]["weight"])
            if next_distance >= distances.get(neighbour, float("inf")):
                continue
            distances[neighbour] = next_distance
            heapq.heappush(queue, (next_distance, neighbour))
    return distances


def _named_root_path(
    graph: nx.Graph,
    connector_id: str,
    from_root_id: str,
    from_root: object,
    to_root_id: str,
    to_root: object,
) -> tuple[tuple[float, float], tuple[float, float], list[tuple[float, float]]]:
    try:
        from_candidates, from_has_exact_candidates = _named_root_candidates(graph, from_root)
    except CrossSpineConnectorTraversalError as error:
        raise CrossSpineConnectorTraversalError(
            f"cross-spine connector {connector_id} named Strategic Spine {from_root_id}: "
            f"{error}"
        ) from error
    try:
        to_candidates, to_has_exact_candidates = _named_root_candidates(graph, to_root)
    except CrossSpineConnectorTraversalError as error:
        raise CrossSpineConnectorTraversalError(
            f"cross-spine connector {connector_id} named Strategic Spine {to_root_id}: "
            f"{error}"
        ) from error
    eligible_from_candidates = [
        (distance, node)
        for distance, node in from_candidates
        if distance <= 0.01 or graph.degree[node] == 1
    ]
    eligible_to_candidates = [
        (distance, node)
        for distance, node in to_candidates
        if distance <= 0.01 or graph.degree[node] == 1
    ]
    candidate: tuple[float, tuple[float, float], tuple[float, float]] | None = None
    distances_by_from_node: dict[tuple[float, float], dict[tuple[float, float], float]] = {}
    for from_distance, from_node in eligible_from_candidates:
        # Reuse each source search for every eligible destination.  Keeping the
        # search direction from the recorded ``from`` root also preserves the
        # original floating-point accumulation order used to rank candidates.
        distances = _weighted_distances(graph, from_node)
        distances_by_from_node[from_node] = distances
        for to_distance, to_node in eligible_to_candidates:
            if from_node == to_node or to_node not in distances:
                continue
            option = (from_distance + distances[to_node] + to_distance, from_node, to_node)
            if candidate is None or option < candidate:
                candidate = option
    if candidate is None:
        if from_has_exact_candidates and to_has_exact_candidates:
            raise CrossSpineConnectorTraversalError(
                f"cross-spine connector {connector_id} has disconnected exact "
                f"named-root intersections between Strategic Spines "
                f"{from_root_id} and {to_root_id}"
            )
        raise CrossSpineConnectorTraversalError(
            f"cross-spine connector {connector_id} has no connected endpoint traversal "
            f"between named Strategic Spines {from_root_id} and {to_root_id}"
        )
    _, from_node, to_node = candidate
    path = _deterministic_weighted_path(
        graph,
        from_node,
        to_node,
        distances=distances_by_from_node.get(from_node),
    )
    return from_node, to_node, path


def _named_root_candidates(
    graph: nx.Graph,
    root: object,
) -> tuple[list[tuple[float, tuple[float, float]]], bool]:
    candidates = sorted(
        (float(Point(node).distance(root)), node)
        for node in graph.nodes
    )
    exact = [candidate for candidate in candidates if candidate[0] <= 0.01]
    if exact:
        # A routed intersection with the named root is authoritative.  Do not
        # substitute a nearby endpoint closure from another component.
        return exact, True
    bounded = [
        candidate
        for candidate in candidates
        if candidate[0] <= PUBLIC_ROUTE_TERMINUS_CLOSURE_MAX_M
    ]
    if bounded:
        return bounded, False
    nearest_distance_m = candidates[0][0]
    raise CrossSpineConnectorTraversalError(
        "named Strategic Spine is beyond bounded source-alignment closure: "
        f"{nearest_distance_m:.1f} m exceeds {PUBLIC_ROUTE_TERMINUS_CLOSURE_MAX_M:.1f} m"
    )


def _connector_path_geometry(
    graph: nx.Graph,
    path: list[tuple[float, float]],
    from_target: Point,
    to_target: Point,
) -> LineString:
    coordinates: list[tuple[float, float]] = [tuple(from_target.coords[0])]
    for index, (start, end) in enumerate(pairwise(path)):
        segment = graph.edges[start, end]["geometry"]
        segment_coordinates = list(segment.coords)
        if tuple(segment_coordinates[0]) != start:
            segment_coordinates.reverse()
        if index == 0 and tuple(segment_coordinates[0]) != coordinates[-1]:
            coordinates.append(tuple(segment_coordinates[0]))
        coordinates.extend(tuple(coordinate) for coordinate in segment_coordinates[1:])
    if tuple(to_target.coords[0]) != coordinates[-1]:
        coordinates.append(tuple(to_target.coords[0]))
    return LineString(coordinates)


def _connector_provenance(connector: pd.Series, connector_id: str) -> dict[str, object]:
    try:
        provenance = json.loads(str(connector["provenance"]))
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"cross-spine connector {connector_id} has invalid provenance") from error
    if not isinstance(provenance, dict):
        raise ValueError(f"cross-spine connector {connector_id} has non-object provenance")
    return provenance


def _rural_communities(
    communities: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    return communities[~communities["urban_circulation_eligible"].astype(bool)].copy()


def _urban_communities(
    communities: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Return every Community admitted to an Urban Circulation Plan."""
    return communities[communities["urban_circulation_eligible"].astype(bool)].copy()


def _community_coverage(
    communities: gpd.GeoDataFrame,
    rural_communities: gpd.GeoDataFrame,
    urban_communities: gpd.GeoDataFrame,
    obligations: gpd.GeoDataFrame,
) -> tuple[dict[str, int], dict[str, TrafficLight]]:
    """Prove identified Communities equal served Communities plus visible gaps."""
    community_obligations = obligations[obligations["obligation_kind"] == "community"].copy()

    def account(
        identified: gpd.GeoDataFrame,
    ) -> tuple[int, int, int, TrafficLight]:
        expected = set(identified["place_id"].astype(str))
        selected = community_obligations[
            community_obligations["community_id"].astype(str).isin(expected)
        ]
        served = int(
            selected["service_status"]
            .isin(
                [
                    AccessServiceStatus.SERVED.value,
                    AccessServiceStatus.SERVED_PROVISIONAL.value,
                ]
            )
            .sum()
        )
        gaps = int((selected["service_status"] == AccessServiceStatus.NETWORK_GAP.value).sum())
        actual = selected["community_id"].astype(str)
        valid = (
            len(expected) == served + gaps
            and not actual.duplicated().any()
            and set(actual) == expected
        )
        return len(expected), served, gaps, TrafficLight.GREEN if valid else TrafficLight.RED

    rural = account(rural_communities)
    urban = account(urban_communities)
    total = account(communities)
    return (
        {
            "identified_rural": rural[0],
            "served_rural": rural[1],
            "gaps_rural": rural[2],
            "identified_urban": urban[0],
            "served_urban": urban[1],
            "gaps_urban": urban[2],
            "identified_total": total[0],
            "served_total": total[1],
            "gaps_total": total[2],
        },
        {"rural": rural[3], "urban": urban[3], "total": total[3]},
    )


def _rural_schools(context: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return _scoped_schools(context, NetworkScope.RURAL)


def _urban_schools(context: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return _scoped_schools(context, NetworkScope.URBAN)


def _scoped_schools(
    context: gpd.GeoDataFrame,
    scope_kind: NetworkScope,
) -> gpd.GeoDataFrame:
    schools = _in_scope_schools(context)
    scope = schools.get("network_scope", pd.Series("unresolved", index=schools.index, dtype=object))
    return schools[scope.eq(scope_kind.value)].copy().sort_values("place_id")


def _in_scope_schools(context: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    schools = context[context["feature_type"] == "school"].copy()
    if schools.empty:
        return gpd.GeoDataFrame(
            columns=[
                "place_id",
                "name",
                "kind",
                "place_class",
                "source_id",
                "evidence_id",
                "school_kind",
                "access_point_status",
                "access_point_source_id",
                "access_point_rationale",
                "geometry",
            ],
            geometry="geometry",
            crs=context.crs,
        )
    eligible = schools.get(
        "school_obligation_eligible",
        schools["category"].eq("school"),
    ).map(_truthy)
    schools = schools[eligible].copy()
    access_status = schools.get(
        "access_point_status",
        pd.Series("unresolved", index=schools.index, dtype=object),
    )
    schools["access_point_status"] = access_status.where(
        access_status.isin([status.value for status in AccessPointStatus]),
        AccessPointStatus.UNRESOLVED.value,
    )
    schools["access_point_source_id"] = schools.get(
        "access_point_source_id", pd.Series(None, index=schools.index, dtype=object)
    )
    default_rationale = (
        "No governed School Access Point evidence is present; the contextual point "
        "is not snapped to a road."
    )
    rationale = schools.get(
        "access_point_rationale",
        pd.Series(default_rationale, index=schools.index, dtype=object),
    )
    schools["access_point_rationale"] = rationale.fillna(default_rationale)
    schools["place_id"] = schools["evidence_id"].astype(str)
    schools["kind"] = "school"
    school_kind = schools.get(
        "school_kind", pd.Series("school-unspecified", index=schools.index, dtype=object)
    )
    schools["school_kind"] = school_kind.fillna("school-unspecified")
    schools["place_class"] = schools["school_kind"]
    return schools.sort_values("place_id")


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, Number):
        return float(value) == 1.0
    if value is None or (not isinstance(value, str) and bool(pd.isna(value))):
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


def _has_distance_challenge(*frames: gpd.GeoDataFrame) -> bool:
    return any(
        "amber" in set(frame.get("criterion_distance", [])) for frame in frames if not frame.empty
    )


def _access_obligation_status(obligations: gpd.GeoDataFrame) -> TrafficLight:
    if obligations.empty:
        return TrafficLight.GREY
    statuses = set(obligations["service_status"])
    if AccessServiceStatus.NETWORK_GAP.value in statuses:
        return TrafficLight.RED
    if AccessServiceStatus.SERVED_PROVISIONAL.value in statuses:
        return TrafficLight.AMBER
    return (
        TrafficLight.GREEN if statuses == {AccessServiceStatus.SERVED.value} else TrafficLight.RED
    )


def _intervention_coverage_complete(*frames: gpd.GeoDataFrame) -> bool:
    populated = [frame for frame in frames if not frame.empty]
    return bool(populated) and all(
        "intervention_archetype" in frame and bool(frame["intervention_archetype"].notna().all())
        for frame in populated
    )


def _branch_provenance_complete(connections: gpd.GeoDataFrame) -> bool:
    required = {
        "root_spine_id",
        "branch_id",
        "parent_role",
        "parent_target_id",
        "source_ids",
    }
    obligation_connections = connections[
        connections["obligation_kind"].isin(["community", "school"])
    ]
    for provenance in obligation_connections.get("provenance", []):
        parsed = json.loads(str(provenance))
        if not required <= set(parsed) or not parsed["source_ids"]:
            return False
    return True


def _degree_one_access_valid(
    obligations: gpd.GeoDataFrame,
    connections: gpd.GeoDataFrame,
) -> bool:
    """Prove that every served rural Community is a leaf with one parent edge."""
    served = obligations[
        (obligations["obligation_kind"] == "community")
        & (obligations["service_status"] == "served")
        & (obligations["network_role"] == "community-access-obligation")
    ]
    community_connections = connections[connections["obligation_kind"] == "community"]
    if community_connections["place_id"].duplicated().any():
        return False
    expected = {
        str(row["place_id"]): str(row["access_connection_id"]) for _, row in served.iterrows()
    }
    actual = {
        str(row["place_id"]): str(row["access_connection_id"])
        for _, row in community_connections.iterrows()
    }
    return actual == expected


def _cross_spine_status(
    connections: gpd.GeoDataFrame,
    meetings: gpd.GeoDataFrame,
) -> TrafficLight:
    roots = sorted(
        connections.loc[connections["obligation_kind"] == "community", "root_spine_id"]
        .dropna()
        .unique()
    )
    if len(roots) < 2:
        return TrafficLight.GREY
    root_graph = nx.Graph()
    root_graph.add_nodes_from(roots)
    root_graph.add_edges_from(
        (
            str(row["from_root_spine_id"]),
            str(row["to_root_spine_id"]),
        )
        for _, row in meetings.iterrows()
    )
    return TrafficLight.GREEN if nx.is_connected(root_graph) else TrafficLight.RED


def _meeting_root_pairs_unique(meetings: gpd.GeoDataFrame) -> bool:
    pairs = [
        tuple(sorted((str(row["from_root_spine_id"]), str(row["to_root_spine_id"]))))
        for _, row in meetings.iterrows()
    ]
    return len(pairs) == len(set(pairs))


def _stable_role_id(prefix: str, *parts: object) -> str:
    value = "::".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:12]}"


def _strategic_spines(context: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Promote rural A-road and governed strategic cycle-route evidence."""
    columns = [
        "spine_id",
        "network_role",
        "spine_kind",
        "name",
        "category",
        "evidence_id",
        "source_id",
        "network_scope",
        "intervention_assumption",
        "design_status",
        "provenance",
        "geometry",
    ]
    candidates = context[
        context["feature_type"].isin({"a-road-spine", *STRATEGIC_CYCLE_ROUTE_TYPES})
    ].copy()
    network_scope = candidates.get(
        "network_scope",
        pd.Series(NetworkScope.UNRESOLVED.value, index=candidates.index, dtype=object),
    ).map(_network_scope)
    candidates = candidates[network_scope.eq(NetworkScope.RURAL)]
    rows: list[dict[str, object]] = []
    for feature_type, spine_kind in (
        ("a-road-spine", "a-road"),
        ("ncn-route", "ncn"),
        ("declassified-ncn-route", "declassified-ncn"),
        ("greenway-cycleway", "greenway"),
    ):
        evidence_frame = candidates[candidates["feature_type"] == feature_type].copy()
        evidence_frame["_corridor_key"] = evidence_frame.apply(
            lambda evidence: (
                str(evidence.get("name") or "").strip()
                or f"unnamed::{evidence.get('category')}::{evidence.get('source_id')}"
            ),
            axis=1,
        )
        for corridor_key, corridor in evidence_frame.groupby("_corridor_key", sort=True):
            evidence_ids = tuple(
                sorted(
                    {
                        str(evidence_id)
                        for evidence_id in corridor.get("evidence_id", [])
                        if str(evidence_id).strip()
                    }
                )
            )
            source_ids = tuple(
                sorted(
                    {
                        str(source_id)
                        for source_id in corridor.get("source_id", [])
                        if str(source_id).strip()
                    }
                )
            )
            evidence_id = _stable_role_id(
                "strategic-spine-evidence", spine_kind, corridor_key, *evidence_ids
            )
            source_id = _stable_role_id(
                "strategic-spine-sources", spine_kind, corridor_key, *source_ids
            )
            name = next(
                (
                    value
                    for value in corridor.get("name", [])
                    if value is not None and str(value).strip()
                ),
                None,
            )
            category = next(
                (
                    value
                    for value in corridor.get("category", [])
                    if value is not None and str(value).strip()
                ),
                None,
            )
            is_a_road = spine_kind == "a-road"
            for geometry in continuous_linework(corridor.geometry.union_all()):
                segment_key = hashlib.sha256(geometry.wkb).hexdigest()[:12]
                rows.append(
                    {
                        "spine_id": _stable_role_id(
                            "strategic-spine",
                            spine_kind,
                            evidence_id,
                            source_id,
                            segment_key,
                        ),
                        "network_role": "strategic-spine",
                        "spine_kind": spine_kind,
                        "name": name,
                        "category": category,
                        "evidence_id": evidence_id,
                        "source_id": source_id,
                        "network_scope": NetworkScope.RURAL.value,
                        "intervention_assumption": (
                            "Major engineering required to provide high-quality protected or "
                            "shared provision"
                            if is_a_road
                            else (
                                "Former National Cycle Network route retained as governed "
                                "strategic cycle-route evidence"
                            )
                            if spine_kind == "declassified-ncn"
                            else (
                                "Greenway cycleway retained as governed strategic cycle-route "
                                "evidence"
                            )
                            if spine_kind == "greenway"
                            else (
                                "Established National Cycle Network route retained as governed "
                                "evidence"
                            )
                        ),
                        "design_status": (
                            "strategic assumption; not a carriageway or final design"
                            if is_a_road
                            else "governed route evidence; not a final design"
                        ),
                        "provenance": json.dumps(
                            {
                                "evidence_id": evidence_id,
                                "evidence_ids": evidence_ids,
                                "source_id": source_id,
                                "source_ids": source_ids,
                                "source_feature_type": feature_type,
                                "network_scope": NetworkScope.RURAL.value,
                            },
                            sort_keys=True,
                        ),
                        "geometry": geometry,
                    }
                )
    return gpd.GeoDataFrame(rows, columns=columns, geometry="geometry", crs=context.crs)


def _network_scope(value: object) -> NetworkScope:
    try:
        return NetworkScope(str(value))
    except ValueError as error:
        raise ValueError(f"invalid governed network_scope: {value!r}") from error


def _a_road_assumptions_complete(strategic_spines: gpd.GeoDataFrame) -> bool:
    a_roads = strategic_spines[strategic_spines["spine_kind"] == "a-road"]
    return not a_roads.empty and bool(
        a_roads["intervention_assumption"].notna().all()
        and a_roads["design_status"].str.contains("not a carriageway").all()
    )
