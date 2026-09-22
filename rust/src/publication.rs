//! Public projection for selected, provisional, and unresolved planning work.
//!
//! The planning history contains provider attempts and receipts for replay, so
//! it is deliberately not copied into this projection.  This module emits the
//! small public decision manifest plus a GeoJSON/MapLibre view over the admitted
//! source baseline, candidate paths, and operation outcomes.

use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

use serde::Serialize;
use serde_json::{Value, json};

use crate::compiler::{Candidate, CommunityAccess, CompileReport};
use crate::error::{Result, SatnError};
use crate::midend::{MidendRun, TypedOperation};

const MAPLIBRE_JS: &[u8] = include_bytes!("../../src/satn/assets/maplibre-gl.js");
const MAPLIBRE_CSS: &[u8] = include_bytes!("../../src/satn/assets/maplibre-gl.css");
const MAPLIBRE_LICENSE: &[u8] = include_bytes!("../../src/satn/assets/MAPLIBRE-LICENSE.txt");

#[derive(Debug, Clone, Serialize)]
pub struct DecisionMapPublication {
    pub branch: String,
    pub base_id: String,
    pub status: String,
    pub run_status: String,
    pub decision_count: usize,
    pub selected_count: usize,
    pub provisional_count: usize,
    pub unresolved_count: usize,
    pub departure_count: usize,
    pub geojson_file: String,
    pub details_file: String,
    pub html_file: String,
    pub publication_file: String,
}

#[derive(Debug, Clone)]
enum MapGeometry {
    Line(Vec<[f64; 2]>),
    Point([f64; 2]),
}

#[derive(Debug, Clone)]
struct MapFeature {
    kind: String,
    geometry: Option<MapGeometry>,
    properties: Value,
}

#[derive(Debug, Clone, Serialize)]
struct PublicDecision {
    id: String,
    kind: String,
    connection_id: String,
    connection_label: String,
    road_classes: Vec<String>,
    candidate_id: Option<String>,
    decision_class: String,
    provisional: bool,
    reason: String,
    uncertainties: Vec<String>,
    evidence_refs: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct PublicDeparture {
    kind: String,
    decision_id: String,
    source_corridor_id: String,
    graph_edge_id: String,
    alternative_candidate_id: String,
    source_reference: String,
    source_role: String,
    decision_class: String,
    provisional: bool,
    reason: String,
    uncertainties: Vec<String>,
    evidence_refs: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct CompactDecisionMap {
    schema: &'static str,
    disclaimer: &'static str,
    area_id: String,
    deployment_id: String,
    snapshot_id: String,
    attribution: String,
    source_attributions: Vec<String>,
    branch: String,
    base_id: String,
    status: String,
    run_status: String,
    counts: DecisionMapCounts,
    files: DecisionMapFiles,
    decisions: Vec<PublicDecision>,
    departures: Vec<PublicDeparture>,
}

#[derive(Debug, Clone, Serialize)]
struct DecisionMapCounts {
    source_baseline: usize,
    prepared_connections: usize,
    pending_connections: usize,
    connections: usize,
    candidates: usize,
    decisions: usize,
    selected: usize,
    provisional: usize,
    unresolved: usize,
    departures: usize,
    unresolved_facts: usize,
    access_obligations: usize,
    unresolved_access: usize,
    community_access: usize,
    community_gaps: usize,
}

#[derive(Debug, Clone, Serialize)]
struct DecisionMapFiles {
    geojson: &'static str,
    details: &'static str,
    html: &'static str,
    publication: &'static str,
}

#[derive(Debug, Clone, Serialize)]
struct PublicPublicationManifest {
    schema: &'static str,
    publication_kind: &'static str,
    deployment_id: String,
    area_id: String,
    snapshot_id: String,
    attribution: String,
    source_attributions: Vec<String>,
    branch: String,
    base_id: String,
    status: String,
    run_status: String,
    accounting_status: String,
    disclaimer: &'static str,
    counts: DecisionMapCounts,
    files: DecisionMapFiles,
}

/// Publish the public decision projection for a live or replayed run.
///
/// Only `CompileReport` and typed operations are used.  Provider requests,
/// response bodies, and other private history records never enter these files.
pub fn publish_decision_map(
    output_dir: &Path,
    report: &CompileReport,
    run: &MidendRun,
) -> Result<DecisionMapPublication> {
    fs::create_dir_all(output_dir)?;
    write_viewer_assets(output_dir)?;
    let files = decision_map_files();
    let effective_report = if run.community_access.is_empty() {
        report.clone()
    } else {
        report
            .clone()
            .with_community_access(run.community_access.clone())
    };
    let report = &effective_report;

    let mut features = baseline_features(report);
    let mut decisions = Vec::new();
    let mut departures = Vec::new();
    let mut selected_count = 0usize;
    let mut provisional_count = 0usize;
    let mut unresolved_count = 0usize;

    for operation in &run.operations {
        match operation {
            TypedOperation::SelectAlignment {
                id,
                connection_id,
                candidate_id,
                decision_class,
                provisional,
                reason,
                uncertainties,
                ..
            } => {
                selected_count += 1;
                if *provisional {
                    provisional_count += 1;
                }
                let (connection_label, road_classes) = connection_details(report, connection_id);
                let candidate = report
                    .candidates
                    .iter()
                    .find(|candidate| candidate.id == *candidate_id);
                let kind = if *provisional {
                    "provisional-alignment"
                } else {
                    "selected-alignment"
                };
                let public_reason = reason.clone().unwrap_or_else(|| {
                    "Classifier selected an admitted alternative; provision, safety, access and adoption remain unresolved."
                        .to_string()
                });
                let evidence_refs = candidate
                    .map(|candidate| vec![candidate.id.clone(), candidate.connection_id.clone()])
                    .unwrap_or_else(|| vec![connection_id.clone(), candidate_id.clone()]);
                decisions.push(PublicDecision {
                    id: id.clone(),
                    kind: kind.to_string(),
                    connection_id: connection_id.clone(),
                    connection_label: connection_label.clone(),
                    road_classes: road_classes.clone(),
                    candidate_id: Some(candidate_id.clone()),
                    decision_class: decision_class.clone(),
                    provisional: *provisional,
                    reason: public_reason.clone(),
                    uncertainties: uncertainties.clone(),
                    evidence_refs: evidence_refs.clone(),
                });
                if let Some(candidate) = candidate {
                    features.push(decision_feature(
                        kind,
                        candidate,
                        &run.branch,
                        &run.base_id,
                        decision_properties(
                            id,
                            connection_id,
                            &connection_label,
                            &road_classes,
                            Some(candidate_id),
                            decision_class,
                            *provisional,
                            &public_reason,
                            uncertainties,
                            evidence_refs,
                        ),
                    ));
                    if let Some(alternative) = strategic_alternative(report, candidate) {
                        features.push(decision_feature(
                            "candidate-alternative",
                            alternative,
                            &run.branch,
                            &run.base_id,
                            json!({
                                "kind": "candidate-alternative",
                                "candidate_id": alternative.id,
                                "connection_id": alternative.connection_id,
                                "role": alternative.role,
                                "decision_id": id,
                                "decision_class": decision_class,
                                "provisional": provisional,
                                "alternative": true,
                                "reason": "Admitted strategic-spine alternative retained for factual comparison with the selected path.",
                                "evidence_refs": [alternative.id, alternative.connection_id],
                            }),
                        ));
                        departures.extend(source_departures(
                            report,
                            candidate,
                            alternative,
                            id,
                            decision_class,
                            *provisional,
                            uncertainties,
                            &public_reason,
                        ));
                    }
                }
            }
            TypedOperation::Unresolved {
                id,
                connection_id,
                decision_class,
                marker,
                reason,
                uncertainties,
                ..
            } => {
                unresolved_count += 1;
                let (connection_label, road_classes) = connection_details(report, connection_id);
                let candidate = report
                    .candidates
                    .iter()
                    .filter(|candidate| candidate.connection_id == *connection_id)
                    .min_by(|left, right| left.id.cmp(&right.id));
                let evidence_refs = candidate
                    .map(|candidate| vec![candidate.id.clone(), candidate.connection_id.clone()])
                    .unwrap_or_else(|| vec![connection_id.clone()]);
                decisions.push(PublicDecision {
                    id: id.clone(),
                    kind: "unresolved-decision".to_string(),
                    connection_id: connection_id.clone(),
                    connection_label: connection_label.clone(),
                    road_classes: road_classes.clone(),
                    candidate_id: None,
                    decision_class: decision_class.clone(),
                    provisional: false,
                    reason: reason.clone(),
                    uncertainties: uncertainties.clone(),
                    evidence_refs: evidence_refs.clone(),
                });
                features.push(MapFeature {
                    kind: "unresolved-decision".to_string(),
                    geometry: candidate
                        .map(|candidate| MapGeometry::Line(candidate.geometry.clone())),
                    properties: json!({
                        "kind": "unresolved-decision",
                        "decision_id": id,
                        "connection_id": connection_id,
                        "connection_label": connection_label,
                        "road_classes": road_classes,
                        "decision_class": decision_class,
                        "marker": marker,
                        "reason": reason,
                        "uncertainties": uncertainties,
                        "evidence_refs": evidence_refs,
                        "branch": run.branch,
                        "base_id": run.base_id,
                    }),
                });
            }
            TypedOperation::SelectCommunityAccess {
                id,
                community_id,
                candidate_id,
                decision_class,
                provisional,
                reason,
                uncertainties,
                ..
            } => {
                selected_count += 1;
                if *provisional {
                    provisional_count += 1;
                }
                let access = report
                    .community_access
                    .iter()
                    .find(|access| access.community_id == *community_id);
                let public_reason = reason.clone().unwrap_or_else(|| {
                    "Selected rural access path; current provision, safety, access and adoption remain unresolved."
                        .to_string()
                });
                let kind = if *provisional {
                    "provisional-alignment"
                } else {
                    "selected-alignment"
                };
                let evidence_refs = vec![community_id.clone(), candidate_id.clone()];
                decisions.push(PublicDecision {
                    id: id.clone(),
                    kind: "rural-community-access".to_string(),
                    connection_id: format!("rural:{community_id}"),
                    connection_label: access
                        .map(|access| format!("{} → accepted frontier", access.name))
                        .unwrap_or_else(|| community_id.clone()),
                    road_classes: Vec::new(),
                    candidate_id: Some(candidate_id.clone()),
                    decision_class: decision_class.clone(),
                    provisional: *provisional,
                    reason: public_reason.clone(),
                    uncertainties: uncertainties.clone(),
                    evidence_refs: evidence_refs.clone(),
                });
                if let Some(access) = access {
                    features.push(rural_decision_feature(
                        kind,
                        access,
                        id,
                        candidate_id,
                        decision_class,
                        *provisional,
                        &public_reason,
                        uncertainties,
                        evidence_refs,
                        &run.branch,
                        &run.base_id,
                    ));
                }
            }
            TypedOperation::UnresolvedCommunityAccess {
                id,
                community_id,
                candidate_id,
                decision_class,
                marker,
                reason,
                uncertainties,
                ..
            } => {
                unresolved_count += 1;
                let access = report
                    .community_access
                    .iter()
                    .find(|access| access.community_id == *community_id);
                let evidence_refs = vec![community_id.clone(), candidate_id.clone()];
                decisions.push(PublicDecision {
                    id: id.clone(),
                    kind: "unresolved-rural-community-access".to_string(),
                    connection_id: format!("rural:{community_id}"),
                    connection_label: access
                        .map(|access| format!("{} → accepted frontier", access.name))
                        .unwrap_or_else(|| community_id.clone()),
                    road_classes: Vec::new(),
                    candidate_id: Some(candidate_id.clone()),
                    decision_class: decision_class.clone(),
                    provisional: false,
                    reason: reason.clone(),
                    uncertainties: uncertainties.clone(),
                    evidence_refs: evidence_refs.clone(),
                });
                if let Some(access) = access {
                    features.push(rural_decision_feature(
                        "unresolved-decision",
                        access,
                        id,
                        candidate_id,
                        decision_class,
                        false,
                        reason,
                        uncertainties,
                        evidence_refs,
                        &run.branch,
                        &run.base_id,
                    ));
                } else {
                    features.push(MapFeature {
                        kind: "unresolved-decision".to_string(),
                        geometry: None,
                        properties: json!({
                            "kind": "unresolved-decision",
                            "decision_id": id,
                            "connection_id": format!("rural:{community_id}"),
                            "candidate_id": candidate_id,
                            "decision_class": decision_class,
                            "marker": marker,
                            "reason": reason,
                            "uncertainties": uncertainties,
                            "evidence_refs": evidence_refs,
                            "branch": run.branch,
                            "base_id": run.base_id,
                        }),
                    });
                }
            }
        }
    }

    for departure in &departures {
        if let Some(source) = report
            .source_inventory
            .iter()
            .find(|source| source.id == departure.source_corridor_id)
        {
            let geometry = report
                .candidates
                .iter()
                .find(|candidate| candidate.id == departure.alternative_candidate_id)
                .and_then(|candidate| edge_geometry(candidate, &departure.graph_edge_id));
            features.push(MapFeature {
                kind: departure.kind.clone(),
                geometry: geometry.map(MapGeometry::Line),
                properties: json!({
                    "kind": departure.kind,
                    "source_corridor_id": source.id,
                    "source_reference": source.reference,
                    "source_baseline_role": source.baseline_role,
                    "source_kind": source.source_kind,
                    "graph_edge_id": departure.graph_edge_id,
                    "alternative_candidate_id": departure.alternative_candidate_id,
                    "decision_id": departure.decision_id,
                    "decision_class": departure.decision_class,
                    "provisional": departure.provisional,
                    "reason": departure.reason,
                    "uncertainties": departure.uncertainties,
                    "evidence_refs": departure.evidence_refs,
                    "branch": run.branch,
                    "base_id": run.base_id,
                }),
            });
        }
    }

    let decided_connections = run
        .operations
        .iter()
        .filter(|operation| !operation.is_rural())
        .map(operation_connection_id)
        .collect::<BTreeSet<_>>();
    let pending_connections = report
        .connections
        .iter()
        .filter(|connection| !decided_connections.contains(connection.id.as_str()))
        .count();
    let unresolved_access = report
        .access_obligations
        .iter()
        .filter(|obligation| obligation.disposition != "served")
        .count();
    let community_gaps = report
        .community_access
        .iter()
        .filter(|access| access.status == "network-gap")
        .count();
    let accounting_status = if report.accounting.status.is_empty() {
        "reviewable-with-gaps".to_string()
    } else {
        report.accounting.status.clone()
    };
    let counts = DecisionMapCounts {
        source_baseline: report.source_inventory.len(),
        prepared_connections: report.connections.len(),
        pending_connections,
        connections: report.connections.len(),
        candidates: report.candidates.len(),
        decisions: decisions.len(),
        selected: selected_count,
        provisional: provisional_count,
        unresolved: unresolved_count,
        departures: departures.len(),
        unresolved_facts: report.unknown_facts.len(),
        access_obligations: report.access_obligations.len(),
        unresolved_access,
        community_access: report.community_access.len(),
        community_gaps,
    };
    let deployment_id = if report.deployment_id.is_empty() {
        report.area_id.clone()
    } else {
        report.deployment_id.clone()
    };
    let manifest = CompactDecisionMap {
        schema: "satn-rust-decision-map/v1",
        disclaimer: "Experimental SATN POC — not an adopted plan.",
        area_id: report.area_id.clone(),
        deployment_id: deployment_id.clone(),
        snapshot_id: report.snapshot_id.clone(),
        attribution: report.attribution.clone(),
        source_attributions: report.source_attributions.clone(),
        branch: run.branch.clone(),
        base_id: run.base_id.clone(),
        status: accounting_status.clone(),
        run_status: run.status.clone(),
        counts: counts.clone(),
        files: files.clone(),
        decisions: decisions.clone(),
        departures: departures.clone(),
    };
    fs::write(
        output_dir.join("decision-map.json"),
        serde_json::to_string_pretty(&manifest)?,
    )?;
    fs::write(
        output_dir.join("decision-map.geojson"),
        serde_json::to_string_pretty(&geojson(&features, report))?,
    )?;
    fs::write(
        output_dir.join(files.html),
        render_interactive_html(
            report,
            run,
            &features,
            &decisions,
            &departures,
            &counts,
            &accounting_status,
            &files,
        ),
    )?;
    let legacy_html = output_dir.join("decision-map.html");
    if legacy_html.is_file() {
        fs::remove_file(legacy_html)?;
    }
    let publication = PublicPublicationManifest {
        schema: "satn-rust-publication/v1",
        publication_kind: "native-agentic",
        deployment_id,
        area_id: report.area_id.clone(),
        snapshot_id: report.snapshot_id.clone(),
        attribution: report.attribution.clone(),
        source_attributions: report.source_attributions.clone(),
        branch: run.branch.clone(),
        base_id: run.base_id.clone(),
        status: accounting_status.clone(),
        run_status: run.status.clone(),
        accounting_status,
        disclaimer: "Experimental SATN POC — not an adopted plan.",
        counts: counts.clone(),
        files: files.clone(),
    };
    fs::write(
        output_dir.join("publication.json"),
        serde_json::to_string_pretty(&publication)?,
    )?;

    Ok(DecisionMapPublication {
        branch: run.branch.clone(),
        base_id: run.base_id.clone(),
        status: publication.status.clone(),
        run_status: run.status.clone(),
        decision_count: decisions.len(),
        selected_count,
        provisional_count,
        unresolved_count,
        departure_count: departures.len(),
        geojson_file: "decision-map.geojson".to_string(),
        details_file: "decision-map.json".to_string(),
        html_file: "index.html".to_string(),
        publication_file: "publication.json".to_string(),
    })
}

fn write_viewer_assets(output_dir: &Path) -> Result<()> {
    let assets = output_dir.join("assets");
    fs::create_dir_all(&assets)?;
    fs::write(assets.join("maplibre-gl.js"), MAPLIBRE_JS)?;
    fs::write(assets.join("maplibre-gl.css"), MAPLIBRE_CSS)?;
    fs::write(assets.join("MAPLIBRE-LICENSE.txt"), MAPLIBRE_LICENSE)?;
    Ok(())
}

/// Read only the retained mechanical base needed to publish a replayed branch.
/// This is intentionally narrower than exposing private history records.
pub fn load_retained_report(history_root: &Path) -> Result<CompileReport> {
    crate::midend::load_base(history_root)
        .map(|base| base.report)
        .map_err(|error| SatnError::InvalidInput(error.to_string()))
}

fn decision_map_files() -> DecisionMapFiles {
    DecisionMapFiles {
        geojson: "decision-map.geojson",
        details: "decision-map.json",
        html: "index.html",
        publication: "publication.json",
    }
}

fn operation_connection_id(operation: &TypedOperation) -> &str {
    match operation {
        TypedOperation::SelectAlignment { connection_id, .. }
        | TypedOperation::Unresolved { connection_id, .. } => connection_id,
        TypedOperation::SelectCommunityAccess { community_id, .. }
        | TypedOperation::UnresolvedCommunityAccess { community_id, .. } => community_id,
    }
}

fn baseline_features(report: &CompileReport) -> Vec<MapFeature> {
    let mut features = Vec::new();
    for source in &report.source_inventory {
        let baseline_layer = source_layer(&source.baseline_role);
        for (part_index, geometry) in source.geometry.iter().enumerate() {
            features.push(MapFeature {
                kind: "source-baseline".to_string(),
                geometry: Some(MapGeometry::Line(geometry.clone())),
                properties: json!({
                    "kind": "source-baseline",
                    "source_corridor_id": source.id,
                    "source_reference": source.reference,
                    "source_kind": source.source_kind,
                    "source_id": source.source_id,
                    "label": format!("{} ({})", source.reference, source.baseline_role),
                    "source_geometry_part": part_index,
                    "scope": source.scope,
                    "baseline_role": source.baseline_role,
                    "baseline_layer": baseline_layer,
                    "topology_status": source.topology_status,
                    "attachment_status": source.attachment_status,
                    "provision_status": source.provision_status,
                }),
            });
        }
    }
    for place in &report.network_places {
        features.push(MapFeature {
            kind: "network-place".to_string(),
            geometry: Some(MapGeometry::Point(place.geometry)),
            properties: json!({
                "kind": "network-place",
                "place_id": place.id,
                "name": place.name,
                "place_class": place.place_class,
                "source_id": place.source_id,
            }),
        });
    }
    for school in &report.school_context {
        features.push(MapFeature {
            kind: "school-context".to_string(),
            geometry: Some(MapGeometry::Point(school.geometry)),
            properties: json!({
                "kind": "school-context",
                "school_id": school.id,
                "source_id": school.source_id,
                "name": school.name,
                "school_obligation_eligible": school.school_obligation_eligible,
            }),
        });
    }
    for access in &report.community_access {
        let geometry = if access.path_geometry.len() >= 2 {
            Some(MapGeometry::Line(access.path_geometry.clone()))
        } else {
            Some(MapGeometry::Point(access.geometry))
        };
        features.push(MapFeature {
            kind: "community-access".to_string(),
            geometry,
            properties: json!({
                "kind": "community-access",
                "community_id": access.community_id,
                "source_id": access.source_id,
                "name": access.name,
                "status": access.status,
                "decision_class": access.decision_class,
                "is_primary": access.is_primary,
                "attachment_node": access.attachment_node,
                "attachment_distance_m": access.attachment_distance_m,
                "attachment_edge_id": access.attachment_edge_id,
                "attachment_point": access.attachment_point,
                "attachment_fraction": access.attachment_fraction,
                "path_start_fraction": access.path_start_fraction,
                "path_end_fraction": access.path_end_fraction,
                "parent_community_id": access.parent_community_id,
                "parent_community_name": access.parent_community_name,
                "parent_junction_node": access.parent_junction_node,
                "parent_junction_edge_id": access.parent_junction_edge_id,
                "parent_junction_fraction": access.parent_junction_fraction,
                "parent_junction_remaining_m": access.parent_junction_remaining_m,
                "root_spine_id": access.root_spine_id,
                "admission_order": access.admission_order,
                "attachment_depth": access.attachment_depth,
                "new_link_length_m": access.new_link_length_m,
                "full_access_length_m": access.full_access_length_m,
                "joined_spine_id": access.joined_spine_id,
                "joined_spine_reference": access.joined_spine_reference,
                "access_length_m": access.access_length_m,
                "path_edge_count": access.path_edge_ids.len(),
                "onward_destinations": access.onward_destinations,
                "onward_benefits": access.onward_benefits,
                "provision_status": access.provision_status,
                "reason": access.reason,
                "full_access_topography": topography_summary(access.full_access_topography.as_ref()),
                "new_link_topography": topography_summary(access.new_link_topography.as_ref()),
            }),
        });
    }
    for obligation in &report.access_obligations {
        if community_access_represents_obligation(obligation, &report.community_access) {
            continue;
        }
        let Some(point) = obligation.geometry else {
            continue;
        };
        features.push(MapFeature {
            kind: "access-obligation".to_string(),
            geometry: Some(MapGeometry::Point(point)),
            properties: json!({
                "kind": "access-obligation",
                "obligation_id": obligation.id,
                "obligation_kind": obligation.kind,
                "source_id": obligation.source_id,
                "name": obligation.name,
                "disposition": obligation.disposition,
                "reason": obligation.reason,
            }),
        });
    }
    features
}

fn rural_decision_feature(
    kind: &str,
    access: &CommunityAccess,
    decision_id: &str,
    candidate_id: &str,
    decision_class: &str,
    provisional: bool,
    reason: &str,
    uncertainties: &[String],
    evidence_refs: Vec<String>,
    branch: &str,
    base_id: &str,
) -> MapFeature {
    let geometry = if access.path_geometry.len() >= 2 {
        Some(MapGeometry::Line(access.path_geometry.clone()))
    } else {
        Some(MapGeometry::Point(access.geometry))
    };
    MapFeature {
        kind: kind.to_string(),
        geometry,
        properties: json!({
            "kind": kind,
            "decision_id": decision_id,
            "community_id": access.community_id,
            "community_name": access.name,
            "candidate_id": candidate_id,
            "decision_class": decision_class,
            "provisional": provisional,
            "parent_community_id": access.parent_community_id,
            "parent_community_name": access.parent_community_name,
            "root_spine_id": access.root_spine_id,
            "root_spine_reference": access.joined_spine_reference,
            "new_link_length_m": access.new_link_length_m,
            "full_access_length_m": access.full_access_length_m,
            "new_link_topography": topography_summary(access.new_link_topography.as_ref()),
            "full_access_topography": topography_summary(access.full_access_topography.as_ref()),
            "reason": reason,
            "uncertainties": uncertainties,
            "evidence_refs": evidence_refs,
            "branch": branch,
            "base_id": base_id,
        }),
    }
}

fn topography_summary(profile: Option<&crate::topography::RouteTopographyProfile>) -> Value {
    let Some(profile) = profile else {
        return Value::Null;
    };
    json!({
        "availability": profile.availability,
        "reason": profile.reason,
        "coverage": profile.coverage,
        "forward_ascent_m": profile.forward_ascent_m,
        "forward_descent_m": profile.forward_descent_m,
        "reverse_ascent_m": profile.reverse_ascent_m,
        "reverse_descent_m": profile.reverse_descent_m,
        "cumulative_elevation_variation_m": profile.cumulative_elevation_variation_m,
        "sustained_gradient": profile.sustained_gradient,
        "evidence_refs": profile.evidence_refs,
        "source_refs": profile.source_refs,
    })
}

fn community_access_represents_obligation(
    obligation: &crate::compiler::AccessObligation,
    community_access: &[crate::compiler::CommunityAccess],
) -> bool {
    !community_access.is_empty()
        && community_access.iter().any(|access| {
            access.is_primary
                && obligation.id == format!("obligation:community:{}", access.community_id)
        })
}

fn source_layer(baseline_role: &str) -> &'static str {
    match baseline_role {
        "a-road" => "source-strategic-a-road",
        "current-ncn" | "former-ncn" | "declassified-ncn" => "source-strategic-ncn",
        "existing-cycleway" | "greenway-cycleway" => "source-strategic-cycleway",
        _ => "source-context",
    }
}

fn decision_feature(
    kind: &str,
    candidate: &Candidate,
    branch: &str,
    base_id: &str,
    properties: Value,
) -> MapFeature {
    let mut properties = properties;
    if let Some(object) = properties.as_object_mut() {
        object.insert("branch".to_string(), json!(branch));
        object.insert("base_id".to_string(), json!(base_id));
        object.insert("role".to_string(), json!(candidate.role));
        object.insert("length_m".to_string(), json!(candidate.length_m));
    }
    MapFeature {
        kind: kind.to_string(),
        geometry: Some(MapGeometry::Line(candidate.geometry.clone())),
        properties,
    }
}

fn decision_properties(
    decision_id: &str,
    connection_id: &str,
    connection_label: &str,
    road_classes: &[String],
    candidate_id: Option<&str>,
    decision_class: &str,
    provisional: bool,
    reason: &str,
    uncertainties: &[String],
    evidence_refs: Vec<String>,
) -> Value {
    json!({
        "kind": if provisional { "provisional-alignment" } else { "selected-alignment" },
        "decision_id": decision_id,
        "connection_id": connection_id,
        "connection_label": connection_label,
        "road_classes": road_classes,
        "candidate_id": candidate_id,
        "decision_class": decision_class,
        "provisional": provisional,
        "reason": reason,
        "uncertainties": uncertainties,
        "evidence_refs": evidence_refs,
    })
}

fn connection_details(report: &CompileReport, connection_id: &str) -> (String, Vec<String>) {
    report
        .connections
        .iter()
        .find(|connection| connection.id == connection_id)
        .map(|connection| {
            (
                format!(
                    "{} → {}",
                    connection.origin_name, connection.destination_name
                ),
                connection.road_classes.clone(),
            )
        })
        .unwrap_or_else(|| (connection_id.to_string(), Vec::new()))
}

fn strategic_alternative<'a>(
    report: &'a CompileReport,
    selected: &Candidate,
) -> Option<&'a Candidate> {
    report
        .candidates
        .iter()
        .filter(|candidate| {
            candidate.connection_id == selected.connection_id
                && candidate.id != selected.id
                && (candidate.role == "strategic-spine"
                    || candidate
                        .role_aliases
                        .iter()
                        .any(|alias| alias == "strategic-spine"))
        })
        .min_by(|left, right| left.id.cmp(&right.id))
}

fn source_departures(
    report: &CompileReport,
    selected: &Candidate,
    alternative: &Candidate,
    decision_id: &str,
    decision_class: &str,
    provisional: bool,
    uncertainties: &[String],
    decision_reason: &str,
) -> Vec<PublicDeparture> {
    let selected_edges = selected.path_edge_ids.iter().collect::<BTreeSet<_>>();
    let mut departures = Vec::new();
    for source in &report.source_inventory {
        if !is_departure_source_role(&source.baseline_role)
            || source.graph_edge_ids.is_empty()
            || !matches!(
                source.topology_status.as_str(),
                "graph-bound" | "partially-graph-bound"
            )
            || !matches!(source.attachment_status.as_str(), "graph-edge" | "partial")
        {
            continue;
        }
        for edge_id in &alternative.path_edge_ids {
            let Some(alternative_geometry) = edge_geometry(alternative, edge_id) else {
                continue;
            };
            if candidate_has_equivalent_edge(selected, &alternative_geometry) {
                continue;
            }
            if selected_edges.contains(edge_id)
                || !source
                    .graph_edge_ids
                    .iter()
                    .any(|source_edge| source_edge == edge_id)
                || departures.iter().any(|departure: &PublicDeparture| {
                    departure.source_corridor_id == source.id
                        && departure.graph_edge_id == *edge_id
                        && departure.decision_id == decision_id
                })
            {
                continue;
            }
            departures.push(PublicDeparture {
                kind: if source.baseline_role == "a-road" {
                    "a-road-departure".to_string()
                } else {
                    "source-departure".to_string()
                },
                decision_id: decision_id.to_string(),
                source_corridor_id: source.id.clone(),
                graph_edge_id: edge_id.clone(),
                alternative_candidate_id: alternative.id.clone(),
                source_reference: source.reference.clone(),
                source_role: source.baseline_role.clone(),
                decision_class: decision_class.to_string(),
                provisional,
                reason: format!(
                    "Selected alignment leaves this admitted {} corridor section outside the selected path; {}",
                    source.baseline_role,
                    decision_reason
                ),
                uncertainties: uncertainties.to_vec(),
                evidence_refs: vec![
                    source.id.clone(),
                    edge_id.clone(),
                    alternative.id.clone(),
                ],
            });
        }
    }
    departures
}

fn is_departure_source_role(role: &str) -> bool {
    matches!(
        role,
        "a-road"
            | "current-ncn"
            | "former-ncn"
            | "declassified-ncn"
            | "existing-cycleway"
            | "greenway-cycleway"
    )
}

fn edge_geometry(candidate: &Candidate, edge_id: &str) -> Option<Vec<[f64; 2]>> {
    candidate
        .path_edge_ids
        .iter()
        .position(|candidate_edge| candidate_edge == edge_id)
        .and_then(|index| candidate.path_edge_geometries.get(index).cloned())
        .filter(|geometry| geometry.len() >= 2)
}

fn candidate_has_equivalent_edge(candidate: &Candidate, geometry: &[[f64; 2]]) -> bool {
    candidate
        .path_edge_geometries
        .iter()
        .any(|candidate_geometry| geometries_equivalent(candidate_geometry, geometry))
}

fn geometries_equivalent(left: &[[f64; 2]], right: &[[f64; 2]]) -> bool {
    left == right || left.iter().eq(right.iter().rev())
}

fn geojson(features: &[MapFeature], report: &CompileReport) -> Value {
    let mut output = Vec::new();
    if let Some(boundary) = &report.boundary_scope {
        output.push(json!({
            "type": "Feature",
            "properties": {
                "kind": "boundary-scope",
                "boundary_id": boundary.id,
                "name": boundary.name,
            },
            "geometry": {"type": "MultiPolygon", "coordinates": boundary.geometry},
        }));
    }
    output.extend(features.iter().map(|feature| feature_json(feature)));
    json!({"type": "FeatureCollection", "features": output})
}

fn feature_json(feature: &MapFeature) -> Value {
    let geometry = match &feature.geometry {
        Some(MapGeometry::Line(coordinates)) => json!({
            "type": "LineString",
            "coordinates": coordinates,
        }),
        Some(MapGeometry::Point(coordinates)) => json!({
            "type": "Point",
            "coordinates": coordinates,
        }),
        None => Value::Null,
    };
    json!({
        "type": "Feature",
        "properties": feature.properties,
        "geometry": geometry,
    })
}

fn render_interactive_html(
    report: &CompileReport,
    run: &MidendRun,
    _features: &[MapFeature],
    _decisions: &[PublicDecision],
    _departures: &[PublicDeparture],
    counts: &DecisionMapCounts,
    accounting_status: &str,
    files: &DecisionMapFiles,
) -> String {
    let title = html_escape(&report.title);
    let branch = html_escape(&run.branch);
    let base_id = html_escape(&run.base_id);
    let deployment_id = html_escape(if report.deployment_id.is_empty() {
        &report.area_id
    } else {
        &report.deployment_id
    });
    let attribution = html_escape(&report.attribution);
    let source_attributions = html_escape(&report.source_attributions.join("; "));
    let template = include_str!("native_map_template.html");
    template
        .replace("__TITLE__", &title)
        .replace("__DEPLOYMENT__", &deployment_id)
        .replace("__BRANCH__", &branch)
        .replace("__BASE_ID__", &base_id)
        .replace("__ACCOUNTING__", &html_escape(accounting_status))
        .replace("__STATUS__", &html_escape(&run.status))
        .replace("__ATTRIBUTION__", &attribution)
        .replace("__SOURCE_ATTRIBUTIONS__", &source_attributions)
        .replace("__SOURCE_COUNT__", &counts.source_baseline.to_string())
        .replace(
            "__PREPARED_CONNECTIONS__",
            &counts.prepared_connections.to_string(),
        )
        .replace(
            "__PENDING_CONNECTIONS__",
            &counts.pending_connections.to_string(),
        )
        .replace("__CANDIDATE_COUNT__", &counts.candidates.to_string())
        .replace("__DECISION_COUNT__", &counts.decisions.to_string())
        .replace("__UNRESOLVED_FACTS__", &counts.unresolved_facts.to_string())
        .replace(
            "__UNRESOLVED_ACCESS__",
            &counts.unresolved_access.to_string(),
        )
        .replace("__COMMUNITY_ACCESS__", &counts.community_access.to_string())
        .replace("__COMMUNITY_GAPS__", &counts.community_gaps.to_string())
        .replace("__GEOJSON__", files.geojson)
}

fn html_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}
