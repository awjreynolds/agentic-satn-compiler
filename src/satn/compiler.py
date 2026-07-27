"""Community Connection compilation over the governed OSM network."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import combinations
from numbers import Number

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import MultiPoint

from satn.agents import AgentDecisionResolver, AgentRuntimeSource, CompilationGate
from satn.backbone import (
    GAP_COLUMNS,
    _assemble_backbone_outward,
    assemble_backbone_outward,
)
from satn.cross_spine import CrossSpineProgress, resolve_cross_spine_assembly
from satn.evidence import (
    PUBLIC_CYCLE_ROUTE_TYPES,
    STRATEGIC_CYCLE_ROUTE_TYPES,
    continuous_linework,
    empty_context,
    govern_network_scope_for_urban_communities,
    mark_ncn_edges,
)
from satn.heartbeat import StageHeartbeat
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
)
from satn.reference_application import (
    ReferenceApplicationPlan,
    ReferenceSATNPublicationRecord,
)
from satn.routing import RoadGraph
from satn.school_street import assess_school_street_candidates
from satn.settlement import (
    assess_community_urban_eligibility,
    urban_settlement_form_profiles,
)
from satn.spine_access_candidate_preparation import (
    SpineAccessCandidatePreparationResult,
    prepare_spine_access_candidates,
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
    compilation_dependency_manifest: dict[str, object] = field(default_factory=dict)
    decision_contract: str = "agent-decision-menu/v1"
    # This is the caller-supplied replay ledger, not the decisions produced by
    # direct runtime during this compilation.  Keeping the two separate makes
    # the input fingerprint independently reproducible.
    decision_ledger_input: dict[str, object] = field(default_factory=dict)
    accepted_decisions: list[dict[str, str]] = field(default_factory=list)
    # Optional bounded Spine Access preparation. It remains absent, rather
    # than empty, for legacy compilations so their route and publication
    # behaviour stays byte-compatible.
    spine_access_candidate_preparation: SpineAccessCandidatePreparationResult | None = None
    # A Reference record is deliberately absent for ordinary compilations.
    # That keeps their publication contract byte-compatible while giving the
    # dedicated Reference boundary one canonical provenance payload.
    reference_satn_publication: ReferenceSATNPublicationRecord | None = None

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


def compile_network(
    config: AreaConfig,
    source: dict[str, gpd.GeoDataFrame],
    runtime: AgentRuntimeSource,
    *,
    governed_input_fingerprint: str = "",
    decision_resolver: AgentDecisionResolver | None = None,
    heartbeat: StageHeartbeat | None = None,
    cross_spine_progress: CrossSpineProgress | None = None,
) -> CompiledNetwork:
    """Compile the ordinary current-input network with no Reference replay input."""

    return _compile_network(
        config,
        source,
        runtime,
        governed_input_fingerprint=governed_input_fingerprint,
        decision_resolver=decision_resolver,
        heartbeat=heartbeat,
        cross_spine_progress=cross_spine_progress,
    )


def _compile_network_with_reference(
    config: AreaConfig,
    source: dict[str, gpd.GeoDataFrame],
    runtime: AgentRuntimeSource,
    reference_application_plan: ReferenceApplicationPlan,
    *,
    governed_input_fingerprint: str,
    decision_resolver: AgentDecisionResolver,
    heartbeat: StageHeartbeat | None = None,
    cross_spine_progress: CrossSpineProgress | None = None,
) -> CompiledNetwork:
    """Private replay seam reachable only after pipeline-owned baseline validation."""

    return _compile_network(
        config,
        source,
        runtime,
        governed_input_fingerprint=governed_input_fingerprint,
        decision_resolver=decision_resolver,
        heartbeat=heartbeat,
        cross_spine_progress=cross_spine_progress,
        reference_application_plan=reference_application_plan,
    )


def _compile_network(
    config: AreaConfig,
    source: dict[str, gpd.GeoDataFrame],
    runtime: AgentRuntimeSource,
    *,
    governed_input_fingerprint: str = "",
    decision_resolver: AgentDecisionResolver | None = None,
    heartbeat: StageHeartbeat | None = None,
    cross_spine_progress: CrossSpineProgress | None = None,
    reference_application_plan: ReferenceApplicationPlan | None = None,
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
    backbone_arguments = (
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
    backbone = (
        assemble_backbone_outward(*backbone_arguments)
        if reference_application_plan is None
        else _assemble_backbone_outward(
            *backbone_arguments,
            reference_application_plan=reference_application_plan,
        )
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
    if heartbeat is not None:
        heartbeat.set_stage("cross-spine-assembly")
    cross_spine_assembly = resolve_cross_spine_assembly(
        cross_spine_connectors,
        strategic_spines,
        agent_records,
        progress=_cross_spine_progress_observer(heartbeat, cross_spine_progress),
        assembly_diagnostics=backbone.cross_spine_assembly_diagnostics,
    )
    if heartbeat is not None:
        heartbeat.set_stage("network-compilation")
    cross_spine_connectors = cross_spine_assembly.valid_connectors
    agent_records = list(cross_spine_assembly.agent_records)
    if not cross_spine_assembly.route_refinement_findings.empty:
        gaps = gpd.GeoDataFrame(
            pd.concat(
                [gaps, cross_spine_assembly.route_refinement_findings],
                ignore_index=True,
                sort=False,
            ),
            columns=GAP_COLUMNS,
            geometry="geometry",
            crs=crs,
        ).sort_values("connection_id")
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
    spine_access_candidate_preparation = None
    if config.compilation.network_selection is not None:
        # This seam prepares finite Spine Access candidates and strict input
        # bindings. It does not select strategic Community Connections, choose
        # a Preferred Strategic Alignment, or mutate compiled routes.
        spine_access_candidate_preparation = prepare_spine_access_candidates(
            config.compilation.network_selection,
            road_graph=road_graph,
            spine_access_connections=spine_access_connections,
            access_obligations=access_obligations,
            strategic_spines=strategic_spines,
            context=context,
            official_road_classification=official_road_classification,
            source_config=config.source,
            config_directory=config.config_path.parent,
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
        superseded_hypotheses=sum(record.decision == "superseded" for record in agent_records),
        human_intervention_requests=_human_intervention_requests(
            agent_records, config.compilation.agent.max_attempts
        ),
        compilation_diagnostics={
            **backbone.compilation_diagnostics,
            "cross_spine": cross_spine_assembly.diagnostics,
            "community_coverage": community_coverage,
            "urban_settlement_form_profiles": urban_settlement_form_profiles(communities),
            "urban_a_road_spine_coverage": urban_a_road_coverage,
            **(
                {
                    "spine_access_candidate_preparation": (
                        spine_access_candidate_preparation.diagnostics
                    )
                }
                if spine_access_candidate_preparation is not None
                else {}
            ),
        },
        spine_access_candidate_preparation=spine_access_candidate_preparation,
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


def _cross_spine_progress_observer(
    heartbeat: StageHeartbeat | None,
    benchmark_progress: CrossSpineProgress | None,
) -> CrossSpineProgress | None:
    """Return a wall-clock-only progress observer for Cross-Spine traversal.

    The Cross-Spine module emits deterministic completed-work counts.  This
    adapter deliberately adds elapsed time, throughput and an ETA only to the
    operational heartbeat context, never to ``CompiledNetwork`` diagnostics or
    any spatial/provenance artifact.
    """
    if heartbeat is None:
        return benchmark_progress
    started = time.perf_counter()

    def report(assessed: int, total: int, diagnostics: Mapping[str, object]) -> None:
        elapsed = max(time.perf_counter() - started, 0.0)
        throughput = assessed / elapsed if assessed and elapsed else 0.0
        remaining = max(total - assessed, 0)
        estimated_remaining = remaining / throughput if throughput else None
        update_context = getattr(heartbeat, "update_context", None)
        if callable(update_context):
            update_context(
                {
                    "cross_spine_connectors_assessed": assessed,
                    "cross_spine_connectors_total": total,
                    "cross_spine_elapsed_seconds": round(elapsed, 3),
                    "cross_spine_throughput_connectors_per_second": round(throughput, 3),
                    "cross_spine_estimated_remaining_seconds": (
                        round(estimated_remaining, 3) if estimated_remaining is not None else None
                    ),
                    "cross_spine_peak_noded_graph_edges": (
                        diagnostics.get("peak_noded_graph_edges", 0)
                    ),
                }
            )
        if benchmark_progress is not None:
            benchmark_progress(assessed, total, diagnostics)

    return report


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
                unresolved_findings=[finding.model_dump(mode="json") for finding in blocking],
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
