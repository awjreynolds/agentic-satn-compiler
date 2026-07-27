"""Stable orchestration API."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd

from satn.agents import (
    AgentCompilationTerminated,
    AgentDecisionRequired,
    AgentDecisionResolver,
    AgentRuntimeProvider,
    AgentRuntimeSource,
    runtime_for,
)
from satn.alignment_selection import ReferenceSATNSelection
from satn.atm import compare_atm, load_atm
from satn.compilation_dependencies import compilation_dependency_manifest
from satn.compiler import (
    CompiledNetwork,
    _compile_network_with_reference,
    _compile_network_with_strategic_reference,
    compile_network,
)
from satn.constants import SCHEMA_VERSION
from satn.content_identity import ordered_geometry_fingerprint
from satn.heartbeat import StageHeartbeat
from satn.models import (
    AgentDecisionLedger,
    AgentDecisionRequest,
    AgentRecord,
    AreaConfig,
    AreaDefinition,
    CompilationResult,
    CouncilConfig,
    DivergenceRecord,
    TrafficLight,
    canonical_decision_ledger_payload,
)
from satn.publisher import (
    publication_artifacts,
    publish,
    validate_publication,
)
from satn.reference_application import (
    _build_reference_application_plan_for_current_baseline,
    build_reference_satn_publication_record,
)
from satn.runtime_governance import incomplete_runtime_governance
from satn.sources import load_snapshot
from satn.spine_access_candidate_preparation import SpineAccessCandidatePreparationResult
from satn.strategic_reference_application import StrategicReferenceApplicationPlan
from satn.strategic_reference_replay import validate_fresh_replay

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _FreshReferenceBaseline:
    council: AreaConfig
    ledger: AgentDecisionLedger
    dependency_manifest: dict[str, object]
    governed_input_fingerprint: str
    source: dict[str, gpd.GeoDataFrame]
    compiled: CompiledNetwork


def _fresh_reference_baseline(
    config: AreaConfig | str | Path,
    decision_ledger: AgentDecisionLedger | str | Path | None,
    heartbeat: StageHeartbeat | None,
    *,
    label: str,
) -> _FreshReferenceBaseline:
    council = (
        config
        if isinstance(config, (AreaDefinition, CouncilConfig))
        else AreaDefinition.from_yaml(config)
    )
    ledger = _load_decision_ledger(decision_ledger)
    dependency_manifest = compilation_dependency_manifest()
    governed_input_fingerprint = compilation_governed_input_fingerprint(
        council,
        dependency_manifest=dependency_manifest,
    )
    source = load_snapshot(council)
    resolver = AgentDecisionResolver(ledger, governed_input_fingerprint)
    baseline = compile_network(
        council,
        _copy_compilation_source(source),
        None,
        governed_input_fingerprint=governed_input_fingerprint,
        decision_resolver=resolver,
        heartbeat=heartbeat,
    )
    unconsumed = {
        response.request_id for response in ledger.responses
    } - resolver.consumed_request_ids
    if unconsumed:
        raise ValueError(
            "decision ledger contains responses that do not belong to the fresh "
            f"{label} baseline: " + ", ".join(sorted(unconsumed))
        )
    return _FreshReferenceBaseline(
        council=council,
        ledger=ledger,
        dependency_manifest=dependency_manifest,
        governed_input_fingerprint=governed_input_fingerprint,
        source=source,
        compiled=baseline,
    )


def _finalize_reference_network(
    compiled: CompiledNetwork,
    baseline: _FreshReferenceBaseline,
    final_resolver: AgentDecisionResolver,
    *,
    label: str,
) -> CompiledNetwork:
    unconsumed = {
        response.request_id for response in baseline.ledger.responses
    } - final_resolver.consumed_request_ids
    if unconsumed:
        raise ValueError(
            "decision ledger contains responses that do not belong to the fresh "
            f"{label} compilation: " + ", ".join(sorted(unconsumed))
        )
    compiled.compilation_input_fingerprint = decision_ledger_input_fingerprint(
        baseline.governed_input_fingerprint,
        baseline.ledger,
    )
    compiled.governed_input_fingerprint = baseline.governed_input_fingerprint
    compiled.snapshot_manifest_sha256 = snapshot_manifest_sha256(baseline.council)
    compiled.area_definition_sha256 = area_definition_sha256(baseline.council)
    compiled.compilation_dependency_manifest = baseline.dependency_manifest
    compiled.decision_contract = baseline.ledger.decision_contract
    compiled.decision_ledger_input = baseline.ledger.model_dump(mode="json")
    compiled.accepted_decisions = AgentDecisionLedger.model_validate(
        {
            "decision_contract": baseline.ledger.decision_contract,
            "responses": [
                response.model_dump(mode="json")
                for response in final_resolver.accepted_responses
            ],
        }
    ).model_dump(mode="json")["responses"]
    return compiled


def compile(
    config: AreaConfig | str | Path,
    *,
    decision_ledger: AgentDecisionLedger | str | Path | None = None,
) -> CompilationResult:
    """Compile into a complete publication or a non-publishing decision request."""
    council = (
        config
        if isinstance(config, (AreaDefinition, CouncilConfig))
        else AreaDefinition.from_yaml(config)
    )
    with StageHeartbeat(
        LOGGER,
        "publication-reuse-check",
        {
            "area_id": council.area_id,
            "snapshot_id": council.source.snapshot_id,
        },
    ) as heartbeat:
        return _compile(council, decision_ledger=decision_ledger, heartbeat=heartbeat)


def compile_reference_network(
    config: AreaConfig | str | Path,
    runtime: AgentRuntimeSource,
    reference: ReferenceSATNSelection,
    source_preparation: SpineAccessCandidatePreparationResult,
    *,
    decision_ledger: AgentDecisionLedger | str | Path | None = None,
    heartbeat: StageHeartbeat | None = None,
) -> CompiledNetwork:
    """Recompile one human-adopted Reference through a fresh current baseline.

    Baseline decisions must already exist in the supplied canonical ledger, so
    validation cannot consume or duplicate direct-runtime responses.  Runtime
    remains available only to the fresh final compilation for genuinely new
    downstream decisions.
    """

    baseline = _fresh_reference_baseline(
        config,
        decision_ledger,
        heartbeat,
        label="Reference",
    )
    current_preparation = baseline.compiled.spine_access_candidate_preparation
    if current_preparation is None:
        raise ValueError(
            "Reference compilation requires current network_selection candidate preparation"
        )
    plan = _build_reference_application_plan_for_current_baseline(
        reference,
        source_preparation,
        current_preparation,
        baseline.council,
    )
    final_resolver = AgentDecisionResolver(
        baseline.ledger,
        baseline.governed_input_fingerprint,
    )
    compiled = _compile_network_with_reference(
        baseline.council,
        _copy_compilation_source(baseline.source),
        runtime,
        plan,
        governed_input_fingerprint=baseline.governed_input_fingerprint,
        decision_resolver=final_resolver,
        heartbeat=heartbeat,
    )
    compiled = _finalize_reference_network(
        compiled,
        baseline,
        final_resolver,
        label="Reference",
    )
    compiled.reference_satn_publication = build_reference_satn_publication_record(
        reference=reference,
        source_preparation=source_preparation,
        baseline_preparation=current_preparation,
        application_plan=plan,
        area_definition_sha256=compiled.area_definition_sha256,
        snapshot_manifest_sha256=compiled.snapshot_manifest_sha256,
        compilation_input_fingerprint=compiled.compilation_input_fingerprint,
        governed_input_fingerprint=compiled.governed_input_fingerprint,
        compilation_dependency_manifest=baseline.dependency_manifest,
        decision_contract=compiled.decision_contract,
        decision_ledger_input=compiled.decision_ledger_input,
        accepted_decisions=compiled.accepted_decisions,
        application_diagnostics=compiled.compilation_diagnostics.get("reference_application", {}),
    )
    return compiled


def compile_strategic_reference_network(
    config: AreaConfig | str | Path,
    runtime: AgentRuntimeSource,
    plan: StrategicReferenceApplicationPlan,
    *,
    decision_ledger: AgentDecisionLedger | str | Path | None = None,
    heartbeat: StageHeartbeat | None = None,
) -> CompiledNetwork:
    """Recompile an adopted strategic Reference without publication authority.

    The ordinary baseline is always rebuilt from the current snapshot.  Exact
    preparation equality is established here, and only the resulting validated
    replay object crosses the private compiler seam.
    """

    baseline = _fresh_reference_baseline(
        config,
        decision_ledger,
        heartbeat,
        label="strategic Reference",
    )
    current_preparation = baseline.compiled.strategic_corridor_preparation
    if current_preparation is None:
        raise ValueError(
            "strategic Reference compilation requires current strategic "
            "corridor preparation"
        )
    current_area_fingerprint = ordered_geometry_fingerprint(
        baseline.source["boundary"].geometry
    )
    if plan.area_fingerprint != current_area_fingerprint:
        raise ValueError(
            "strategic Reference Area identity does not match the fresh current "
            "snapshot boundary"
        )
    validated_replay = validate_fresh_replay(plan, current_preparation)

    final_resolver = AgentDecisionResolver(
        baseline.ledger,
        baseline.governed_input_fingerprint,
    )
    compiled = _compile_network_with_strategic_reference(
        baseline.council,
        _copy_compilation_source(baseline.source),
        runtime,
        validated_replay,
        governed_input_fingerprint=baseline.governed_input_fingerprint,
        decision_resolver=final_resolver,
        heartbeat=heartbeat,
    )
    return _finalize_reference_network(
        compiled,
        baseline,
        final_resolver,
        label="strategic Reference",
    )


def compile_reference(
    config: AreaConfig | str | Path,
    reference: ReferenceSATNSelection,
    source_preparation: SpineAccessCandidatePreparationResult,
    *,
    decision_ledger: AgentDecisionLedger | str | Path | None = None,
) -> CompilationResult:
    """Atomically publish one freshly validated, human-governed Reference SATN.

    The normal compiler entry point remains unchanged.  This boundary always
    performs a fresh baseline/replay validation before reaching the established
    atomic publisher, and never treats a Reference selection as delivery or
    publication authority by itself.
    """

    council = (
        config
        if isinstance(config, (AreaDefinition, CouncilConfig))
        else AreaDefinition.from_yaml(config)
    )
    with StageHeartbeat(
        LOGGER,
        "reference-publication",
        {"area_id": council.area_id, "snapshot_id": council.source.snapshot_id},
    ) as heartbeat:
        runtime = (
            AgentRuntimeProvider(lambda: runtime_for(council.compilation.agent))
            if council.compilation.agent.response_mode == "direct-runtime"
            and council.compilation.agent.review_statuses
            else None
        )
        compiled = compile_reference_network(
            council,
            runtime,
            reference,
            source_preparation,
            decision_ledger=decision_ledger,
            heartbeat=heartbeat,
        )
        record = compiled.reference_satn_publication
        if record is None:  # Defensive: the dedicated boundary must bind provenance.
            raise ValueError("Reference compilation produced no publication provenance")
        run_fingerprint = json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "area_id": council.area_id,
                "snapshot_id": council.source.snapshot_id,
                "reference_publication_fingerprint": record.reference_publication_fingerprint,
                "compilation_input_fingerprint": compiled.compilation_input_fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        run_id = f"reference-{hashlib.sha256(run_fingerprint.encode()).hexdigest()[:12]}"
        heartbeat.set_stage("reference-publication")
        artifacts = publish(council, compiled, run_id)
    return CompilationResult(
        run_id=run_id,
        status=compiled.status,
        output_dir=council.publication.output_dir,
        connections=compiled.connection_count,
        gaps=len(compiled.gaps),
        artifacts=artifacts,
        criteria=compiled.criteria,
        agent_records=compiled.agent_records,
        divergence_records=compiled.divergence_records,
        metadata={
            "network_model": "backbone-outward",
            "compilation_input_fingerprint": compiled.compilation_input_fingerprint,
            "reference_satn": record.revalidated().publication_payload(),
            "compilation_diagnostics": compiled.compilation_diagnostics,
        },
    )


def _copy_compilation_source(
    source: dict[str, gpd.GeoDataFrame],
) -> dict[str, gpd.GeoDataFrame]:
    """Give baseline and final compilation independent current-input frames."""

    return {name: frame.copy(deep=True) for name, frame in source.items()}


def _compile(
    config: AreaConfig,
    *,
    decision_ledger: AgentDecisionLedger | str | Path | None = None,
    heartbeat: StageHeartbeat | None = None,
) -> CompilationResult:
    """Compile a parsed area definition, reporting its current long-running stage."""
    started = time.perf_counter()
    council = config
    ledger = _load_decision_ledger(decision_ledger)
    dependency_manifest = compilation_dependency_manifest()
    governed_input_fingerprint = compilation_governed_input_fingerprint(
        council,
        dependency_manifest=dependency_manifest,
    )
    input_fingerprint = decision_ledger_input_fingerprint(
        governed_input_fingerprint,
        ledger,
    )
    decision_resolver = AgentDecisionResolver(ledger, governed_input_fingerprint)
    LOGGER.info(
        "Compilation started council=%s snapshot=%s schema=%s",
        council.area_id,
        council.source.snapshot_id,
        SCHEMA_VERSION,
    )
    reused = _reuse_validated_publication(
        council,
        governed_input_fingerprint,
        input_fingerprint,
        dependency_manifest,
    )
    if reused is not None:
        return reused
    if heartbeat is not None:
        heartbeat.set_stage("snapshot-load")
    source = load_snapshot(council)
    LOGGER.info(
        "Snapshot loaded places=%d road_edges=%d context_features=%d",
        len(source["places"]),
        len(source["network"]),
        len(source.get("context", [])),
    )
    runtime = (
        AgentRuntimeProvider(lambda: runtime_for(council.compilation.agent))
        if council.compilation.agent.response_mode == "direct-runtime"
        and council.compilation.agent.review_statuses
        else None
    )
    atm_reference = None
    if council.atm.enabled and council.atm.mode == "seeded":
        if heartbeat is not None:
            heartbeat.set_stage("atm-seeded-load-reprojection")
        atm_reference = load_atm(council).to_crs(source["network"].crs)
    if heartbeat is not None:
        heartbeat.set_stage("network-compilation")
    try:
        compiled = compile_network(
            council,
            source,
            runtime,
            governed_input_fingerprint=governed_input_fingerprint,
            decision_resolver=decision_resolver,
            heartbeat=heartbeat,
        )
    except AgentDecisionRequired as required:
        return _decision_required_result(
            council,
            input_fingerprint,
            required.request,
            ledger,
            required.applied_records,
            required.applied_divergence_records,
            required.validation,
        )
    except AgentCompilationTerminated as terminated:
        return _terminated_result(council, input_fingerprint, terminated)
    compiled.compilation_input_fingerprint = input_fingerprint
    compiled.governed_input_fingerprint = governed_input_fingerprint
    compiled.snapshot_manifest_sha256 = snapshot_manifest_sha256(council)
    compiled.area_definition_sha256 = area_definition_sha256(council)
    compiled.compilation_dependency_manifest = dependency_manifest
    LOGGER.info(
        "Network compiled connections=%d gaps=%d status=%s",
        compiled.connection_count,
        len(compiled.gaps),
        compiled.status,
    )
    if council.atm.enabled:
        if council.atm.mode == "blind":
            if heartbeat is not None:
                heartbeat.set_stage("atm-blind-load-reprojection")
            atm_reference = load_atm(council).to_crs(source["network"].crs)
        if heartbeat is not None:
            heartbeat.set_stage("atm-comparison")
        try:
            compiled.divergence_records = compare_atm(
                compiled,
                atm_reference,
                runtime,
                council,
                decision_resolver,
            )
        except AgentDecisionRequired as required:
            return _decision_required_result(
                council,
                input_fingerprint,
                required.request,
                ledger,
                required.applied_records,
                required.applied_divergence_records,
                required.validation,
            )
        except AgentCompilationTerminated as terminated:
            return _terminated_result(council, input_fingerprint, terminated)
        if council.publication.audience == "local" or council.atm.redistribution_permitted:
            compiled.atm_reference = atm_reference
        unresolved = any(not record.resolved for record in compiled.divergence_records)
        compiled.criteria["atm_comparison"] = {
            "comparison_available": TrafficLight.GREEN,
            "unresolved_divergences": (TrafficLight.AMBER if unresolved else TrafficLight.GREEN),
        }
    if heartbeat is not None:
        heartbeat.set_stage("post-compilation-artifact-preparation")
    unconsumed = {
        response.request_id for response in ledger.responses
    } - decision_resolver.consumed_request_ids
    if unconsumed:
        raise ValueError(
            "decision ledger contains responses that do not belong to this compilation: "
            + ", ".join(sorted(unconsumed))
        )
    compiled.decision_contract = ledger.decision_contract
    compiled.decision_ledger_input = ledger.model_dump(mode="json")
    # Execution order is not a durable audit order.  Persist the same canonical
    # response order used by the ledger contract, so downstream equality checks
    # cannot mistake a traversal-order difference for a different decision set.
    compiled.accepted_decisions = AgentDecisionLedger.model_validate(
        {
            "decision_contract": ledger.decision_contract,
            "responses": [
                response.model_dump(mode="json")
                for response in decision_resolver.accepted_responses
            ],
        }
    ).model_dump(mode="json")["responses"]
    if heartbeat is not None:
        heartbeat.set_stage("publication-fingerprint")
    run_fingerprint = json.dumps(
        {
            "council": council.area_id,
            "snapshot": council.source.snapshot_id,
            "schema_version": SCHEMA_VERSION,
            "criteria_version": council.compilation.criteria_version,
            "compilation_input_fingerprint": input_fingerprint,
            "spine_access_candidate_preparation_fingerprint": (
                compiled.spine_access_candidate_preparation.preparation_fingerprint
                if compiled.spine_access_candidate_preparation is not None
                else None
            ),
            "snapshot_manifest": hashlib.sha256(
                (
                    council.source.snapshot_dir / council.source.snapshot_id / "snapshot.json"
                ).read_bytes()
            ).hexdigest(),
            "context": sorted(
                evidence_id
                for frame in (
                    compiled.a_road_spines,
                    compiled.ncn_routes,
                    compiled.schools,
                    compiled.retail_centres,
                    compiled.healthcare,
                )
                for evidence_id in frame.get("evidence_id", [])
            ),
            "school_street_assessments": sorted(
                (
                    row.assessment_id,
                    row.assessment_status,
                    row.rationale,
                    row.evidence,
                    row.geometry.wkb_hex,
                )
                for row in compiled.school_street_assessments.itertuples()
            ),
            "topography_profiles": sorted(
                (
                    row.profile_id,
                    row.edge_id,
                    row.edge_type,
                    row.evidence_status,
                    row.distance_m,
                    row.forward_ascent_m,
                    row.forward_descent_m,
                    row.reverse_ascent_m,
                    row.reverse_descent_m,
                    row.steepest_sustained_gradient_pct,
                    row.steepest_sustained_gradient_rationale,
                    row.gradient_section_ids,
                    row.elevation_evidence_ids,
                    row.geometry.wkb_hex,
                )
                for row in compiled.topography_profiles.itertuples()
            ),
            "gradient_sections": sorted(
                (
                    row.section_id,
                    row.profile_id,
                    row.gradient_band,
                    row.length_m,
                    row.forward_gradient_pct,
                    row.geometry.wkb_hex,
                )
                for row in compiled.gradient_sections.itertuples()
            ),
            "elevation_corroboration": sorted(
                (
                    row.corroboration_id,
                    row.source_id,
                    row.osm_elevation,
                    row.osm_incline,
                    row.evidence_role,
                    row.geometry.wkb_hex,
                )
                for row in compiled.elevation_corroboration.itertuples()
            ),
            "strategic_spines": sorted(compiled.strategic_spines["spine_id"]),
            "urban_classification_status": compiled.urban_classification_status,
            "elevation_evidence_status": compiled.elevation_evidence_status,
            "urban_spines": sorted(
                (
                    row.structure_id,
                    row.official_classification,
                    row.source_id,
                    row.content_fingerprint,
                    row.geometry.wkb_hex,
                )
                for row in compiled.urban_spines.itertuples()
            ),
            "urban_classification_unknowns": sorted(
                (
                    row.structure_id,
                    row.official_feature_id,
                    row.source_id,
                    row.content_fingerprint,
                    row.geometry.wkb_hex,
                )
                for row in compiled.urban_classification_unknowns.itertuples()
            ),
            "candidate_low_traffic_areas": sorted(
                (
                    row.structure_id,
                    row.boundary_ids,
                    row.intervention_need,
                    row.observed_through_traffic_evidence_ids,
                    row.observed_through_traffic_source_ids,
                    row.geometry.wkb_hex,
                )
                for row in compiled.low_traffic_areas.itertuples()
            ),
            "low_traffic_area_portals": sorted(
                (
                    row.portal_id,
                    row.area_id,
                    row.boundary_id,
                    row.geometry.wkb_hex,
                )
                for row in compiled.low_traffic_area_portals.itertuples()
            ),
            "access_obligations": sorted(
                (
                    row.obligation_id,
                    row.service_status,
                    row.access_point_status,
                    row.access_point_source_id,
                    row.access_point_rationale,
                    row.low_traffic_area_id,
                    row.portal_id,
                    row.fabric_source_ids,
                    row.finding,
                    row.geometry.wkb_hex,
                )
                for row in compiled.access_obligations.itertuples()
            ),
            "spine_access_connections": sorted(
                (
                    row.access_connection_id,
                    row.community_id,
                    row.school_id,
                    row.access_point_status,
                    row.spine_id,
                    row.parent_target_id,
                    row.parent_target_name,
                    row.community_attachment_node,
                    row.community_attachment_distance_m,
                    row.spine_attachment_node,
                    row.spine_attachment_distance_m,
                    row.geometry.wkb_hex,
                )
                for row in compiled.spine_access_connections.itertuples()
            ),
            "spine_access_branches": sorted(
                (
                    row.branch_id,
                    row.root_spine_id,
                    row.connection_ids,
                    row.geometry.wkb_hex,
                )
                for row in compiled.spine_access_branches.itertuples()
            ),
            "branch_meeting_connections": sorted(
                (
                    row.meeting_connection_id,
                    row.from_place_id,
                    row.to_place_id,
                    row.from_root_spine_id,
                    row.to_root_spine_id,
                    row.geometry.wkb_hex,
                )
                for row in compiled.branch_meeting_connections.itertuples()
            ),
            "cross_spine_connectors": sorted(
                (
                    row.cross_spine_connector_id,
                    row.meeting_connection_id,
                    row.connection_ids,
                    row.geometry.wkb_hex,
                )
                for row in compiled.cross_spine_connectors.itertuples()
            ),
            "atm_mode": council.atm.mode if council.atm.enabled else "disabled",
        },
        sort_keys=True,
    )
    run_id = f"run-{hashlib.sha256(run_fingerprint.encode()).hexdigest()[:12]}"
    if heartbeat is not None:
        heartbeat.set_stage("publication")
    artifacts = publish(council, compiled, run_id)
    LOGGER.info(
        "Publication validated output=%s elapsed_seconds=%.1f",
        council.publication.output_dir,
        time.perf_counter() - started,
    )
    return CompilationResult(
        run_id=run_id,
        status=compiled.status,
        output_dir=council.publication.output_dir,
        connections=compiled.connection_count,
        gaps=len(compiled.gaps),
        artifacts=artifacts,
        criteria=compiled.criteria,
        agent_records=compiled.agent_records,
        divergence_records=compiled.divergence_records,
        metadata={
            "network_model": "backbone-outward",
            "compilation_input_fingerprint": input_fingerprint,
            "compilation_diagnostics": compiled.compilation_diagnostics,
            "human_intervention_requests": [
                request.model_dump(mode="json") for request in compiled.human_intervention_requests
            ],
            "network_units": compiled.network_units,
            "urban_classification_status": compiled.urban_classification_status,
            "elevation_evidence_status": compiled.elevation_evidence_status,
            **(
                {
                    "spine_access_candidate_preparation": (
                        compiled.spine_access_candidate_preparation.metadata()
                    )
                }
                if compiled.spine_access_candidate_preparation is not None
                else {}
            ),
            "urban_spines": len(compiled.urban_spines),
            "urban_classification_unknowns": len(compiled.urban_classification_unknowns),
            "urban_spine_records": [
                {
                    "structure_id": row.structure_id,
                    "official_classification": row.official_classification,
                    "official_feature_id": row.official_feature_id,
                    "source_id": row.source_id,
                    "effective_date": row.effective_date,
                    "licence": row.licence,
                    "content_fingerprint": row.content_fingerprint,
                    "classification_status": row.classification_status,
                    "intervention_assumption": row.intervention_assumption,
                }
                for row in compiled.urban_spines.itertuples()
            ],
            "urban_classification_unknown_records": [
                {
                    "structure_id": row.structure_id,
                    "official_feature_id": row.official_feature_id,
                    "source_id": row.source_id,
                    "effective_date": row.effective_date,
                    "licence": row.licence,
                    "content_fingerprint": row.content_fingerprint,
                    "classification_status": row.classification_status,
                }
                for row in compiled.urban_classification_unknowns.itertuples()
            ],
            "candidate_low_traffic_areas": len(compiled.low_traffic_areas),
            "low_traffic_area_portals": len(compiled.low_traffic_area_portals),
            "candidate_low_traffic_area_records": [
                {
                    "structure_id": row.structure_id,
                    "name": row.name,
                    "status": row.status,
                    "intervention_need": row.intervention_need,
                    "boundary_ids": row.boundary_ids,
                    "observed_through_traffic_evidence_ids": (
                        row.observed_through_traffic_evidence_ids
                    ),
                    "observed_through_traffic_source_ids": (
                        row.observed_through_traffic_source_ids
                    ),
                    "portal_count": row.portal_count,
                }
                for row in compiled.low_traffic_areas.itertuples()
            ],
            "low_traffic_area_portal_records": [
                {
                    "portal_id": row.portal_id,
                    "area_id": row.area_id,
                    "name": row.name,
                    "boundary_id": row.boundary_id,
                    "boundary_name": row.boundary_name,
                    "boundary_kind": row.boundary_kind,
                }
                for row in compiled.low_traffic_area_portals.itertuples()
            ],
            "strategic_spines": len(compiled.strategic_spines),
            "access_obligations": len(compiled.access_obligations),
            "school_access_obligations": int(
                (compiled.access_obligations["obligation_kind"] == "school").sum()
            ),
            "school_street_assessments": len(compiled.school_street_assessments),
            "school_street_assessment_records": [
                {
                    "assessment_id": row.assessment_id,
                    "school_id": row.school_id,
                    "school_name": row.school_name,
                    "assessment_status": row.assessment_status,
                    "assessment_label": row.assessment_label,
                    "rationale": row.rationale,
                    "qualification": row.qualification,
                    "access_point_status": row.access_point_status,
                    "adjoining_road_classification": (row.adjoining_road_classification),
                    "bus_access": row.bus_access,
                    "essential_access": row.essential_access,
                    "alternative_through_route": row.alternative_through_route,
                    "displacement_risk": row.displacement_risk,
                    "missing_evidence": row.missing_evidence,
                    "evidence": row.evidence,
                    "source_ids": row.source_ids,
                }
                for row in compiled.school_street_assessments.itertuples()
            ],
            "topography_profiles": len(compiled.topography_profiles),
            "gradient_sections": len(compiled.gradient_sections),
            "topography_alternative_comparisons": [
                {
                    "connection_id": row[id_column],
                    "connection_type": connection_type,
                    "triggered": row["topography_alternative_trigger"],
                    "status": row["topography_comparison_status"],
                    "rationale": row["topography_comparison_rationale"],
                    "original_role": row["topography_original_role"],
                    "selected_role": row["topography_selected_role"],
                    "alignment_options": row["alignment_options"],
                }
                for frame, id_column, connection_type in (
                    (
                        compiled.spine_access_connections,
                        "access_connection_id",
                        "spine-access-connection",
                    ),
                    (
                        compiled.branch_meeting_connections,
                        "meeting_connection_id",
                        "branch-meeting-connection",
                    ),
                )
                for _, row in frame.iterrows()
            ],
            "elevation_corroboration_count": len(compiled.elevation_corroboration),
            "topography_profile_records": [
                {
                    "profile_id": row.profile_id,
                    "edge_id": row.edge_id,
                    "edge_type": row.edge_type,
                    "evidence_status": row.evidence_status,
                    "evidence_rationale": row.evidence_rationale,
                    "distance_m": row.distance_m,
                    "forward_ascent_m": row.forward_ascent_m,
                    "forward_descent_m": row.forward_descent_m,
                    "reverse_ascent_m": row.reverse_ascent_m,
                    "reverse_descent_m": row.reverse_descent_m,
                    "steepest_sustained_gradient_pct": (row.steepest_sustained_gradient_pct),
                    "steepest_sustained_gradient_rationale": (
                        row.steepest_sustained_gradient_rationale
                    ),
                    "gradient_section_ids": row.gradient_section_ids,
                    "elevation_evidence_ids": row.elevation_evidence_ids,
                    "elevation_source_ids": row.elevation_source_ids,
                }
                for row in compiled.topography_profiles.itertuples()
            ],
            "spine_access_connections": len(compiled.spine_access_connections),
            "spine_access_branches": len(compiled.spine_access_branches),
            "branch_meeting_connections": len(compiled.branch_meeting_connections),
            "cross_spine_connectors": len(compiled.cross_spine_connectors),
            "strategic_spine_records": [
                {
                    "spine_id": row.spine_id,
                    "evidence_id": row.evidence_id,
                    "source_id": row.source_id,
                    "provenance": row.provenance,
                }
                for row in compiled.strategic_spines.itertuples()
            ],
            "access_obligation_records": [
                {
                    "obligation_id": row.obligation_id,
                    "community_id": row.community_id,
                    "school_id": row.school_id,
                    "school_kind": row.school_kind,
                    "service_status": row.service_status,
                    "service_rationale": row.service_rationale,
                    "access_point_status": row.access_point_status,
                    "access_point_source_id": row.access_point_source_id,
                    "access_point_rationale": row.access_point_rationale,
                    "access_connection_id": row.access_connection_id,
                    "root_spine_id": row.root_spine_id,
                    "branch_id": row.branch_id,
                    "network_scope": row.network_scope,
                    "criterion_continuity": row.criterion_continuity,
                    "low_traffic_area_id": row.low_traffic_area_id,
                    "low_traffic_area_name": row.low_traffic_area_name,
                    "portal_id": row.portal_id,
                    "portal_name": row.portal_name,
                    "urban_spine_id": row.urban_spine_id,
                    "fabric_source_ids": row.fabric_source_ids,
                    "supporting_evidence": row.supporting_evidence,
                    "finding": row.finding,
                    "geometry_semantics": row.geometry_semantics,
                    "provenance": row.provenance,
                }
                for row in compiled.access_obligations.itertuples()
            ],
            "spine_access_connection_records": [
                {
                    "access_connection_id": row.access_connection_id,
                    "network_role": row.network_role,
                    "place_id": row.place_id,
                    "place_kind": row.place_kind,
                    "community_id": row.community_id,
                    "school_id": row.school_id,
                    "school_kind": row.school_kind,
                    "access_point_status": row.access_point_status,
                    "access_point_source_id": row.access_point_source_id,
                    "access_point_rationale": row.access_point_rationale,
                    "spine_id": row.spine_id,
                    "root_spine_id": row.root_spine_id,
                    "branch_id": row.branch_id,
                    "parent_branch_id": row.parent_branch_id,
                    "parent_role": row.parent_role,
                    "parent_target_id": row.parent_target_id,
                    "parent_target_name": row.parent_target_name,
                    "parent_place_id": row.parent_place_id,
                    "parent_access_connection_id": row.parent_access_connection_id,
                    "attachment_depth": row.attachment_depth,
                    "community_attachment_node": row.community_attachment_node,
                    "community_attachment_distance_m": row.community_attachment_distance_m,
                    "community_attachment_point": row.community_attachment_point,
                    "spine_attachment_node": row.spine_attachment_node,
                    "spine_attachment_distance_m": row.spine_attachment_distance_m,
                    "spine_attachment_point": row.spine_attachment_point,
                    "source_ids": row.source_ids,
                    "provenance": row.provenance,
                }
                for row in compiled.spine_access_connections.itertuples()
            ],
            "spine_access_branch_records": [
                {
                    "branch_id": row.branch_id,
                    "root_spine_id": row.root_spine_id,
                    "connection_ids": row.connection_ids,
                    "place_ids": row.place_ids,
                    "provenance": row.provenance,
                }
                for row in compiled.spine_access_branches.itertuples()
            ],
            "branch_meeting_connection_records": [
                {
                    "meeting_connection_id": row.meeting_connection_id,
                    "network_role": row.network_role,
                    "from_place_id": row.from_place_id,
                    "to_place_id": row.to_place_id,
                    "from_root_spine_id": row.from_root_spine_id,
                    "to_root_spine_id": row.to_root_spine_id,
                    "source_ids": row.source_ids,
                    "provenance": row.provenance,
                }
                for row in compiled.branch_meeting_connections.itertuples()
            ],
            "cross_spine_connector_records": [
                {
                    "cross_spine_connector_id": row.cross_spine_connector_id,
                    "meeting_connection_id": row.meeting_connection_id,
                    "branch_ids": row.branch_ids,
                    "connection_ids": row.connection_ids,
                    "source_ids": row.source_ids,
                    "provenance": row.provenance,
                }
                for row in compiled.cross_spine_connectors.itertuples()
            ],
            "superseded_hypotheses": compiled.superseded_hypotheses,
            "atm_mode": council.atm.mode if council.atm.enabled else "disabled",
            "atm_geometry_included": compiled.atm_reference is not None,
            "divergence_counts": dict(
                Counter(record.status for record in compiled.divergence_records)
            ),
        },
    )


def _decision_required_result(
    council: AreaConfig,
    input_fingerprint: str,
    request: AgentDecisionRequest,
    ledger: AgentDecisionLedger,
    agent_records: list[AgentRecord] | None = None,
    divergence_records: list[DivergenceRecord] | None = None,
    validation: str | None = None,
) -> CompilationResult:
    """Return a durable menu without publishing or retaining continuation state."""
    records = [*(agent_records or []), *(divergence_records or [])]
    return CompilationResult(
        run_id=f"decision-{request.dependency_fingerprint[:12]}",
        status="decision-required",
        output_dir=council.publication.output_dir,
        connections=0,
        gaps=0,
        artifacts={},
        criteria={},
        agent_records=agent_records or [],
        divergence_records=divergence_records or [],
        decision_requests=[request],
        metadata={
            "compilation_input_fingerprint": input_fingerprint,
            "decision_response_validation": validation,
            "runtime_governance": incomplete_runtime_governance(
                council.compilation.agent,
                records,
                decision_ledger_input=ledger.model_dump(mode="json"),
                validation=validation,
            ),
        },
    )


def _terminated_result(
    council: AreaConfig,
    input_fingerprint: str,
    terminated: AgentCompilationTerminated,
) -> CompilationResult:
    accepted = [
        *terminated.applied_records,
        *terminated.applied_divergence_records,
    ]
    fingerprint = hashlib.sha256(
        json.dumps(
            [record.model_dump(mode="json") for record in accepted],
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    return CompilationResult(
        run_id=f"terminated-{fingerprint[:12]}",
        status="terminated",
        output_dir=council.publication.output_dir,
        connections=0,
        gaps=0,
        artifacts={},
        criteria={},
        agent_records=terminated.applied_records,
        divergence_records=terminated.applied_divergence_records,
        metadata={
            "compilation_input_fingerprint": input_fingerprint,
            "decision_response_validation": "accepted",
        },
    )


def _load_decision_ledger(
    value: AgentDecisionLedger | str | Path | None,
) -> AgentDecisionLedger:
    if value is None:
        return AgentDecisionLedger()
    if isinstance(value, AgentDecisionLedger):
        return value
    try:
        payload = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("decision ledger file is not valid JSON") from error
    return canonical_decision_ledger_payload(payload)


def decision_ledger_input_fingerprint(
    governed_input_fingerprint: str,
    ledger: AgentDecisionLedger,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "governed_input_fingerprint": governed_input_fingerprint,
                "decision_ledger": ledger.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def compilation_governed_input_fingerprint(
    council: AreaConfig,
    *,
    dependency_manifest: dict[str, object] | None = None,
) -> str:
    """Fingerprint every governed input required for safe whole-publication reuse."""
    config_payload = council.model_dump(mode="json")
    config_payload["compilation"].pop("full", None)
    # The superseded comparison is explanatory, never a correctness input. Its path is
    # governed by configuration, but promoting this run to that path must not invalidate
    # reuse of the authoritative network it just produced.
    network_selection_paths = _network_selection_governed_paths(council)
    governed_paths = [
        council.atm.path,
        (
            council.source.official_road_classification.path
            if council.source.official_road_classification is not None
            else None
        ),
        (
            council.source.observed_through_traffic.path
            if council.source.observed_through_traffic is not None
            else None
        ),
        (
            council.source.national_elevation.path
            if council.source.national_elevation is not None
            else None
        ),
        *network_selection_paths,
    ]
    missing_paths = sorted(
        str(path)
        for path in network_selection_paths
        if not path.is_file()
    )
    if missing_paths:
        raise ValueError(
            "configured governed input file is missing: " + ", ".join(missing_paths)
        )
    manifest = dependency_manifest or compilation_dependency_manifest()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "configuration": config_payload,
        "snapshot_manifest_sha256": snapshot_manifest_sha256(council),
        "governed_file_sha256": {
            str(path): _file_digest(path)
            for path in governed_paths
            if path is not None and path.is_file()
        },
        "compiler_dependency_manifest": manifest,
        # Retain the compact field for release contracts and benchmark evidence.
        "compiler_sha256": manifest["sha256"],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _network_selection_governed_paths(council: AreaConfig) -> tuple[Path, ...]:
    """Return governed alignment-evidence paths when the optional pass is enabled."""
    if council.compilation.network_selection is None:
        return ()
    paths: list[Path] = []
    population = council.source.population_reach_evidence
    if population is not None:
        paths.extend(
            [
                population.output_area_geometry.path,
                population.population_weighted_centroids.path,
                population.usual_resident_counts.path,
            ]
        )
    school_register = council.source.school_register_evidence
    if school_register is not None:
        paths.append(school_register.school_register.path)
    admissions = council.source.strategic_education_destination_admissions
    if admissions is not None:
        paths.append(admissions.admissions.path)
    return tuple(paths)


def snapshot_manifest_sha256(council: AreaConfig) -> str:
    """Return the immutable digest for the snapshot consumed by a compilation."""
    return _file_digest(council.source.snapshot_dir / council.source.snapshot_id / "snapshot.json")


def area_definition_sha256(council: AreaConfig) -> str:
    """Return the exact bytes digest of the Area Definition, never a re-serialisation."""
    return _file_digest(council.config_path)


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compiler_digest() -> str:
    """Return the explicit compilation dependency-set digest."""
    digest = compilation_dependency_manifest()["sha256"]
    if not isinstance(digest, str):  # Defensive: this is a governed reuse boundary.
        raise ValueError("compilation dependency manifest has no SHA-256 digest")
    return digest


def _reuse_validated_publication(
    council: AreaConfig,
    governed_input_fingerprint: str,
    input_fingerprint: str,
    dependency_manifest: dict[str, object],
) -> CompilationResult | None:
    if council.compilation.full:
        LOGGER.info("Validated publication reuse disabled by --full")
        return None
    output = council.publication.output_dir
    run_path = output / "run.json"
    if not run_path.exists():
        return None
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
        # Reuse is a trust boundary: a legacy/stripped or reordered record must
        # be recompiled rather than silently normalised by Pydantic.
        input_ledger = canonical_decision_ledger_payload(run["decision_ledger_input"])
        accepted_ledger = canonical_decision_ledger_payload(
            {
                "decision_contract": run["decision_contract"],
                "responses": run["accepted_decisions"],
            }
        )
        if input_ledger.decision_contract != run["decision_contract"] or (
            accepted_ledger.model_dump(mode="json")["responses"]
            != run["accepted_decisions"]
        ):
            return None
        # The stored wire input is a separate trust boundary.  Recompute its
        # fingerprint before comparing it to the current caller's ledger, so a
        # canonical-looking but altered persisted input cannot borrow the old
        # run fingerprint and be reused.
        persisted_input_fingerprint = decision_ledger_input_fingerprint(
            governed_input_fingerprint,
            input_ledger,
        )
        if run.get("governed_input_fingerprint") != governed_input_fingerprint:
            LOGGER.info("Existing publication governed inputs differ; recompiling")
            return None
        if run.get("compilation_dependency_manifest") != dependency_manifest:
            LOGGER.info("Existing publication compilation dependencies differ; recompiling")
            return None
        if run.get("compilation_input_fingerprint") != persisted_input_fingerprint:
            LOGGER.info("Existing publication persisted decision input differs; recompiling")
            return None
        if persisted_input_fingerprint != input_fingerprint:
            LOGGER.info("Existing publication input fingerprint differs; recompiling")
            return None
        validate_publication(output, council)
        agents_payload = json.loads((output / "agent-records.json").read_text(encoding="utf-8"))
        divergences_payload = json.loads(
            (output / "divergence-records.json").read_text(encoding="utf-8")
        )
        criteria = {
            section: {criterion: TrafficLight(status) for criterion, status in values.items()}
            for section, values in run["criteria"].items()
        }
        LOGGER.info(
            "Validated publication reused run_id=%s output=%s",
            run["run_id"],
            output,
        )
        return CompilationResult(
            run_id=run["run_id"],
            status=run["status"],
            output_dir=output,
            connections=run["connection_count"],
            gaps=run["gap_count"],
            artifacts=publication_artifacts(output),
            criteria=criteria,
            agent_records=[
                AgentRecord.model_validate(record) for record in agents_payload["records"]
            ],
            divergence_records=[
                DivergenceRecord.model_validate(record) for record in divergences_payload["records"]
            ],
            metadata=run | {"publication_reused": True},
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
        LOGGER.warning(
            "Existing publication failed reuse validation; recompiling reason=%s",
            error,
        )
        return None
