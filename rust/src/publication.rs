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
use crate::officer::{OfficerOutcomeStatus, OfficerScenario, displaced_baseline_edges};

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

#[derive(Debug, Clone, Serialize)]
pub struct BusContextPublication {
    pub route_count: usize,
    pub interchange_count: usize,
    pub geojson_file: String,
}

#[derive(Debug, Clone)]
enum MapGeometry {
    Line(Vec<[f64; 2]>),
    Point([f64; 2]),
    Polygon(Vec<Vec<[f64; 2]>>),
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
    #[serde(skip_serializing_if = "Option::is_none")]
    officer_scenario: Option<PublicOfficerScenario>,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    officer_scenario: Option<PublicOfficerScenario>,
}

#[derive(Debug, Clone, Serialize)]
struct PublicOfficerScenario {
    label: &'static str,
    authority: String,
    base_id: String,
    baseline_branch: String,
    community_access_regenerated: bool,
    agreement_count: usize,
    divergence_count: usize,
    unavailable_count: usize,
    outcomes: Vec<PublicOfficerOutcome>,
}

#[derive(Debug, Clone, Serialize)]
struct PublicOfficerOutcome {
    decision_id: String,
    connection_id: String,
    baseline_candidate_id: Option<String>,
    officer_candidate_id: Option<String>,
    effective_candidate_id: Option<String>,
    status: &'static str,
    source_refs: Vec<String>,
    attribution: String,
    rationale: String,
    baseline_decision_id: Option<String>,
    baseline_decision_class: Option<String>,
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
    publish_decision_map_inner(output_dir, report, run, None)
}

/// Publish the effective network together with its separate illustrative officer scenario.
pub fn publish_officer_scenario_map(
    output_dir: &Path,
    report: &CompileReport,
    effective_run: &MidendRun,
    scenario: &OfficerScenario,
) -> Result<DecisionMapPublication> {
    if scenario.base_id != effective_run.base_id {
        return Err(SatnError::InvalidInput(
            "officer scenario base_id must match the effective run".to_string(),
        ));
    }
    publish_decision_map_inner(output_dir, report, effective_run, Some(scenario))
}

fn publish_decision_map_inner(
    output_dir: &Path,
    report: &CompileReport,
    run: &MidendRun,
    officer_scenario: Option<&OfficerScenario>,
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

    let public_officer_scenario = officer_scenario.map(public_officer_scenario);
    if let Some(scenario) = officer_scenario {
        add_officer_scenario_features(report, scenario, run, &mut features);
        if !scenario.community_access_regenerated {
            for feature in features.iter_mut().filter(|feature| {
                feature.kind == "community-access"
                    || feature.properties.get("community_id").is_some()
            }) {
                if let Some(properties) = feature.properties.as_object_mut() {
                    properties.insert("scenario_baseline_context".to_string(), json!(true));
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
        officer_scenario: public_officer_scenario.clone(),
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
            public_officer_scenario.as_ref(),
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
        officer_scenario: public_officer_scenario,
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

/// Add a separately sourced bus context sidecar to an existing native map.
///
/// This updates only the viewer and the context sidecar; the planner's GeoJSON
/// and decision/publication manifests are left untouched.
pub fn add_bus_context(output_dir: &Path, context_path: &Path) -> Result<BusContextPublication> {
    let context_bytes = fs::read(context_path)?;
    let context: Value = serde_json::from_slice(&context_bytes)?;
    let (route_count, interchange_count) = validate_bus_context(&context)?;

    let publication: Value =
        serde_json::from_slice(&fs::read(output_dir.join("publication.json"))?)?;
    if publication["schema"] != "satn-rust-publication/v1"
        || publication["publication_kind"] != "native-agentic"
    {
        return Err(SatnError::InvalidInput(
            "bus context requires an existing native-agentic publication".to_string(),
        ));
    }
    let network: Value =
        serde_json::from_slice(&fs::read(output_dir.join("decision-map.geojson"))?)?;
    feature_collection(&network, "published decision map")?;
    let html_path = output_dir.join("index.html");
    let html = fs::read_to_string(&html_path)?;
    if !html.contains("data-native-publication=\"native-agentic\"") {
        return Err(SatnError::InvalidInput(
            "existing publication HTML is not a native-agentic map".to_string(),
        ));
    }
    let html = enable_bus_context_loader(&html)?;

    fs::write(output_dir.join("bus-context.geojson"), context_bytes)?;
    fs::write(
        output_dir.join("bus-context.js"),
        include_str!("native_bus_context.js"),
    )?;
    fs::write(html_path, html)?;

    Ok(BusContextPublication {
        route_count,
        interchange_count,
        geojson_file: "bus-context.geojson".to_string(),
    })
}

fn validate_bus_context(context: &Value) -> Result<(usize, usize)> {
    let features = feature_collection(context, "bus context")?;
    let mut route_count = 0;
    let mut interchange_count = 0;
    for (index, feature) in features.iter().enumerate() {
        if feature.get("type").and_then(Value::as_str) != Some("Feature") {
            return Err(SatnError::InvalidInput(format!(
                "bus context item {index} must be a GeoJSON Feature"
            )));
        }
        let properties = feature
            .get("properties")
            .and_then(Value::as_object)
            .ok_or_else(|| {
                SatnError::InvalidInput(format!(
                    "bus context feature {index} has no properties object"
                ))
            })?;
        let geometry = feature
            .get("geometry")
            .and_then(Value::as_object)
            .ok_or_else(|| {
                SatnError::InvalidInput(format!(
                    "bus context feature {index} has no geometry object"
                ))
            })?;
        match properties.get("kind").and_then(Value::as_str) {
            Some("bus-route") => {
                validate_bus_route_properties(properties, index)?;
                if !matches!(
                    geometry.get("type").and_then(Value::as_str),
                    Some("LineString" | "MultiLineString")
                ) {
                    return Err(SatnError::InvalidInput(format!(
                        "bus route feature {index} geometry must be LineString or MultiLineString"
                    )));
                }
                route_count += 1;
            }
            Some("bus-interchange") => {
                validate_interchange_properties(properties, index)?;
                if geometry.get("type").and_then(Value::as_str) != Some("Point") {
                    return Err(SatnError::InvalidInput(format!(
                        "bus interchange feature {index} geometry must be Point"
                    )));
                }
                interchange_count += 1;
            }
            _ => {
                return Err(SatnError::InvalidInput(format!(
                    "bus context feature {index} kind must be bus-route or bus-interchange"
                )));
            }
        }
    }
    Ok((route_count, interchange_count))
}

fn validate_bus_route_properties(
    properties: &serde_json::Map<String, Value>,
    index: usize,
) -> Result<()> {
    for key in ["route_ids", "route_short_names"] {
        if !properties.contains_key(key) {
            return Err(SatnError::InvalidInput(format!(
                "bus route feature {index} is missing {key}"
            )));
        }
    }
    for key in ["service_date", "source_id", "shape_id"] {
        if !properties
            .get(key)
            .and_then(Value::as_str)
            .is_some_and(|value| !value.trim().is_empty())
        {
            return Err(SatnError::InvalidInput(format!(
                "bus route feature {index} is missing text {key}"
            )));
        }
    }
    if !has_nonempty_value(&properties["route_ids"]) {
        return Err(SatnError::InvalidInput(format!(
            "bus route feature {index} has no route identifier"
        )));
    }
    Ok(())
}

fn validate_interchange_properties(
    properties: &serde_json::Map<String, Value>,
    index: usize,
) -> Result<()> {
    let name = properties
        .get("name")
        .or_else(|| properties.get("facility_name"));
    if !name
        .and_then(Value::as_str)
        .is_some_and(|value| !value.trim().is_empty())
    {
        return Err(SatnError::InvalidInput(format!(
            "bus interchange feature {index} is missing facility name"
        )));
    }
    let facility_type = properties
        .get("facility_type")
        .or_else(|| properties.get("interchange_type"))
        .or_else(|| properties.get("type"));
    if !facility_type
        .and_then(Value::as_str)
        .is_some_and(|value| !value.trim().is_empty())
    {
        return Err(SatnError::InvalidInput(format!(
            "bus interchange feature {index} is missing facility type"
        )));
    }
    let has_source_label = [
        "source_id",
        "source_label",
        "source_name",
        "source",
        "dataset",
    ]
    .iter()
    .any(|key| {
        properties
            .get(*key)
            .and_then(Value::as_str)
            .is_some_and(|value| !value.trim().is_empty())
    });
    if !has_source_label {
        return Err(SatnError::InvalidInput(format!(
            "bus interchange feature {index} has no source label"
        )));
    }
    Ok(())
}

fn has_nonempty_value(value: &Value) -> bool {
    match value {
        Value::String(text) => !text.trim().is_empty(),
        Value::Array(values) => values.iter().any(has_nonempty_value),
        Value::Null => false,
        _ => true,
    }
}

fn feature_collection<'a>(value: &'a Value, label: &str) -> Result<&'a Vec<Value>> {
    if value.get("type").and_then(Value::as_str) != Some("FeatureCollection") {
        return Err(SatnError::InvalidInput(format!(
            "{label} must be a GeoJSON FeatureCollection"
        )));
    }
    value
        .get("features")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            SatnError::InvalidInput(format!("{label} FeatureCollection has no features array"))
        })
}

fn enable_bus_context_loader(html: &str) -> Result<String> {
    const MARKER: &str = "data-satn-bus-context";
    const ACTIVE_URL: &str = "data-context-url=\"bus-context.geojson\"";
    const SCRIPT: &str = "<script src=\"bus-context.js\" data-satn-bus-context data-context-url=\"bus-context.geojson\"></script>";
    if html.contains(MARKER) {
        if html.contains(ACTIVE_URL) {
            return Ok(html.to_string());
        }
        let updated = html.replace(
            "data-context-url=\"\"",
            "data-context-url=\"bus-context.geojson\"",
        );
        if updated == html {
            return Err(SatnError::InvalidInput(
                "existing bus context loader has no context URL marker".to_string(),
            ));
        }
        return Ok(updated);
    }
    let closing_body = html.rfind("</body>").ok_or_else(|| {
        SatnError::InvalidInput("existing publication HTML has no body close tag".to_string())
    })?;
    let mut updated = String::with_capacity(html.len() + SCRIPT.len() + 1);
    updated.push_str(&html[..closing_body]);
    updated.push_str(SCRIPT);
    updated.push_str(&html[closing_body..]);
    Ok(updated)
}

fn write_viewer_assets(output_dir: &Path) -> Result<()> {
    let assets = output_dir.join("assets");
    fs::create_dir_all(&assets)?;
    fs::write(assets.join("maplibre-gl.js"), MAPLIBRE_JS)?;
    fs::write(assets.join("maplibre-gl.css"), MAPLIBRE_CSS)?;
    fs::write(assets.join("MAPLIBRE-LICENSE.txt"), MAPLIBRE_LICENSE)?;
    fs::write(
        output_dir.join("bus-context.js"),
        include_str!("native_bus_context.js"),
    )?;
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

fn public_officer_scenario(scenario: &OfficerScenario) -> PublicOfficerScenario {
    let outcomes = scenario
        .outcomes
        .iter()
        .map(|outcome| PublicOfficerOutcome {
            decision_id: outcome.decision_id.clone(),
            connection_id: outcome.connection_id.clone(),
            baseline_candidate_id: outcome.baseline_candidate_id.clone(),
            officer_candidate_id: outcome.officer_candidate_id.clone(),
            effective_candidate_id: outcome.effective_candidate_id.clone(),
            status: officer_status_label(outcome.status),
            source_refs: outcome.source_refs.clone(),
            attribution: outcome.attribution.clone(),
            rationale: outcome.rationale.clone(),
            baseline_decision_id: outcome.baseline_decision_id.clone(),
            baseline_decision_class: outcome.baseline_decision_class.clone(),
        })
        .collect::<Vec<_>>();
    PublicOfficerScenario {
        label: "Illustrative officer-led scenario",
        authority: scenario.authority.clone(),
        base_id: scenario.base_id.clone(),
        baseline_branch: scenario.baseline_branch.clone(),
        community_access_regenerated: scenario.community_access_regenerated,
        agreement_count: scenario
            .outcomes
            .iter()
            .filter(|outcome| outcome.status == OfficerOutcomeStatus::Agreement)
            .count(),
        divergence_count: scenario
            .outcomes
            .iter()
            .filter(|outcome| outcome.status == OfficerOutcomeStatus::Divergence)
            .count(),
        unavailable_count: scenario
            .outcomes
            .iter()
            .filter(|outcome| outcome.status == OfficerOutcomeStatus::Unavailable)
            .count(),
        outcomes,
    }
}

fn officer_status_label(status: OfficerOutcomeStatus) -> &'static str {
    match status {
        OfficerOutcomeStatus::Agreement => "agreement",
        OfficerOutcomeStatus::Divergence => "divergence",
        OfficerOutcomeStatus::Unavailable => "unavailable",
    }
}

fn add_officer_scenario_features(
    report: &CompileReport,
    scenario: &OfficerScenario,
    effective_run: &MidendRun,
    features: &mut Vec<MapFeature>,
) {
    for outcome in &scenario.outcomes {
        let status = officer_status_label(outcome.status);
        if let Some(feature) = features.iter_mut().find(|feature| {
            matches!(
                feature.kind.as_str(),
                "selected-alignment" | "provisional-alignment" | "unresolved-decision"
            ) && feature.properties["connection_id"] == outcome.connection_id
                && (outcome.effective_candidate_id.is_none()
                    || feature.properties["candidate_id"]
                        == outcome
                            .effective_candidate_id
                            .as_deref()
                            .unwrap_or_default()
                    || feature.properties["kind"] == "unresolved-decision")
        }) {
            let officer_selected = outcome.officer_candidate_id.is_some()
                && outcome.officer_candidate_id == outcome.effective_candidate_id;
            if let Some(properties) = feature.properties.as_object_mut() {
                properties.insert("scenario_status".to_string(), json!(status));
                properties.insert(
                    "scenario_authority".to_string(),
                    json!(if outcome.status == OfficerOutcomeStatus::Unavailable {
                        "Officer decision unavailable"
                    } else {
                        "Illustrative officer-led scenario"
                    }),
                );
                properties.insert(
                    "baseline_candidate_id".to_string(),
                    json!(outcome.baseline_candidate_id),
                );
                properties.insert(
                    "officer_candidate_id".to_string(),
                    json!(outcome.officer_candidate_id),
                );
                properties.insert(
                    "effective_candidate_id".to_string(),
                    json!(outcome.effective_candidate_id),
                );
                properties.insert(
                    "baseline_decision_id".to_string(),
                    json!(outcome.baseline_decision_id),
                );
                properties.insert(
                    "baseline_decision_class".to_string(),
                    json!(outcome.baseline_decision_class),
                );
                properties.insert(
                    "officer_source_refs".to_string(),
                    json!(outcome.source_refs),
                );
                properties.insert(
                    "officer_attribution".to_string(),
                    json!(outcome.attribution),
                );
                properties.insert("officer_rationale".to_string(), json!(outcome.rationale));
                properties.insert("officer_selected".to_string(), json!(officer_selected));
            }
        }
    }

    for displaced in displaced_baseline_edges(report, scenario, effective_run) {
        let outcome = displaced.outcome;
        let candidate = displaced.baseline_candidate;
        let (connection_label, road_classes) = connection_details(report, &outcome.connection_id);
        features.push(MapFeature {
            kind: "officer-baseline-unused".to_string(),
            geometry: Some(MapGeometry::Line(displaced.geometry.to_vec())),
            properties: json!({
                "kind": "officer-baseline-unused",
                "label": "Unused original baseline edge",
                "decision_id": outcome.baseline_decision_id,
                "connection_id": outcome.connection_id,
                "connection_label": connection_label,
                "road_classes": road_classes,
                "candidate_id": candidate.id,
                "baseline_candidate_id": candidate.id,
                "baseline_edge_id": displaced.edge_id,
                "officer_candidate_id": outcome.officer_candidate_id,
                "effective_candidate_id": outcome.effective_candidate_id,
                "decision_class": outcome.baseline_decision_class,
                "baseline_decision_id": outcome.baseline_decision_id,
                "baseline_decision_class": outcome.baseline_decision_class,
                "scenario_status": officer_status_label(outcome.status),
                "scenario_authority": "Original baseline decision",
                "officer_source_refs": outcome.source_refs,
                "officer_attribution": outcome.attribution,
                "officer_rationale": outcome.rationale,
                "reason": "This original baseline edge is displaced and is no longer used by any effective selected or provisional strategic candidate.",
                "evidence_refs": [candidate.id, displaced.edge_id, outcome.source_refs],
                "branch": scenario.baseline_branch,
                "base_id": scenario.base_id,
            }),
        });
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
    for candidate in &report.candidate_neighbourhoods {
        features.push(MapFeature {
            kind: "candidate-neighbourhood".to_string(),
            geometry: Some(MapGeometry::Polygon(
                candidate.geometry.coordinates.clone(),
            )),
            properties: json!({
                "kind": "candidate-neighbourhood",
                "candidate_neighbourhood_id": candidate.id,
                "label": format!("{} candidate enclosure", candidate.urban_extent_name),
                "urban_extent_source_id": candidate.urban_extent_source_id,
                "urban_extent_name": candidate.urban_extent_name,
                "area_m2": candidate.area_m2,
                "source_dataset_ids": candidate.source_dataset_ids,
                "source_effective_dates": candidate.source_effective_dates,
                "source_licences": candidate.source_licences,
                "source_classifications": candidate.source_classifications,
                "classified_road_frontages": candidate.classified_road_frontages,
                "urban_edge_closes_boundary": candidate.urban_edge_closes_boundary,
                "urban_extent_source_dataset_id": candidate.urban_extent_source_dataset_id,
                "urban_extent_source_effective_date": candidate.urban_extent_source_effective_date,
                "urban_extent_source_licence": candidate.urban_extent_source_licence,
                "urban_extent_source_url": candidate.urban_extent_source_url,
                "urban_extent_source_attribution": candidate.urban_extent_source_attribution,
                "interpretation": "Candidate enclosure; it does not establish existing low-traffic conditions, safe crossings, or legal access.",
            }),
        });
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
                "terminal_kind": terminal_kind(access),
                "terminal_source_id": terminal_source_id(access),
                "urban_entry": urban_entry_properties(access),
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
            "access_status": access.status,
            "root_spine_id": access.root_spine_id,
            "root_spine_reference": access.joined_spine_reference,
            "terminal_kind": terminal_kind(access),
            "terminal_source_id": terminal_source_id(access),
            "urban_entry": urban_entry_properties(access),
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

fn terminal_kind(access: &CommunityAccess) -> Option<&'static str> {
    if access.urban_entry.is_some() {
        Some("urban-entry")
    } else if access.root_spine_id.is_some() || access.joined_spine_id.is_some() {
        Some("strategic-spine")
    } else {
        None
    }
}

fn terminal_source_id(access: &CommunityAccess) -> Option<&str> {
    access
        .urban_entry
        .as_ref()
        .map(|entry| entry.extent_source_id.as_str())
        .or(access.root_spine_id.as_deref())
        .or(access.joined_spine_id.as_deref())
}

fn urban_entry_properties(access: &CommunityAccess) -> Value {
    let Some(entry) = access.urban_entry.as_ref() else {
        return Value::Null;
    };
    json!({
        "kind": "urban-entry",
        "destination_id": entry.destination_id,
        "destination_name": entry.destination_name,
        "extent_source_id": entry.extent_source_id,
        "crossing_edge_id": entry.edge_id,
        "crossing_fraction": entry.fraction,
        "crossing_point": entry.point,
    })
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
        "estimated_moving_time": profile.estimated_moving_time,
        "moving_time_boundary_extrapolation": profile.moving_time_boundary_extrapolation,
        "hill_neutral_moving_time": profile.hill_neutral_moving_time,
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
        Some(MapGeometry::Polygon(rings)) => json!({
            "type": "Polygon",
            "coordinates": rings,
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
    officer_scenario: Option<&PublicOfficerScenario>,
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
    let (community_access_label, community_access_help, network_description) =
        match officer_scenario {
            Some(scenario) if scenario.community_access_regenerated => (
                "Community access (recomputed)",
                "Community access paths were recomputed against the effective strategic choices in this scenario.",
                "The strategic active travel network shows effective scenario selections. Any baseline choice retained for an unavailable officer decision is identified as not officer-approved. A-road reference corridors remain in the source baseline. Community access paths were recomputed against the effective strategic choices.",
            ),
            Some(_) => (
                "Baseline community access (not re-evaluated)",
                "Retained mechanical baseline context. These paths were not re-evaluated against the illustrative officer-led choices and do not establish connection to the effective scenario network.",
                "The strategic active travel network shows effective scenario selections. Any baseline choice retained for an unavailable officer decision is identified as not officer-approved. A-road reference corridors remain in the source baseline. Community access is retained baseline context and was not re-evaluated against officer choices.",
            ),
            None => (
                "Community Connections",
                "Community Connections show recorded access from a community to the strategic network. A cross marks a missing connection where no connected path is evidenced.",
                "The Strategic active travel network shows selected and provisional route lines. Community Connections, source baseline and other evidence layers are optional overlays. Provision, safety, access and adoption remain explicit unknowns where evidence is absent.",
            ),
        };
    let candidate_neighbourhood_layer_control = if report.candidate_neighbourhoods.is_empty() {
        String::new()
    } else {
        "<li class=\"layer-control-row\"><label><input type=\"checkbox\" data-layer-toggle=\"candidate-neighbourhood\"> <span class=\"swatch candidate-key\"></span>Candidate neighbourhoods <span data-layer-count></span></label><details class=\"layer-help\" name=\"native-layer-help\"><summary aria-label=\"About candidate neighbourhoods\" aria-describedby=\"layer-help-candidate-neighbourhood\">ⓘ</summary></details><span id=\"layer-help-candidate-neighbourhood\" class=\"layer-help-popup\" role=\"tooltip\">Candidate neighbourhoods are generated planning areas based on available evidence; they do not confirm a low-traffic area.</span></li>".to_string()
    };
    let (strategic_network_legend, strategic_network_help) = if officer_scenario.is_some() {
        (
            "<span class=\"swatch strategic-network-key\" aria-hidden=\"true\"></span>Strategic network <span class=\"swatch officer-selected-key\" aria-hidden=\"true\"></span>Officer-selected route <span class=\"swatch officer-baseline-unused-key\" aria-hidden=\"true\"></span>Unused original baseline edge",
            "Red shows the strategic network and A-road references; orange shows officer-selected alignments; dark grey shows original baseline edges unused by every effective selected or provisional strategic candidate.",
        )
    } else {
        (
            "<span class=\"swatch strategic-network-key\" aria-hidden=\"true\"></span>Strategic active travel network",
            "The proposed strategic network includes chosen and provisional non-community route lines plus A-road reference sections. Provisional routes remain available in the separate review layer.",
        )
    };
    let officer_scenario_attribute = if officer_scenario.is_some() {
        " data-native-officer-scenario=\"illustrative\""
    } else {
        ""
    };
    let officer_scenario_banner = officer_scenario
        .map(officer_scenario_banner)
        .unwrap_or_default();
    let officer_findings = officer_scenario.map(officer_findings).unwrap_or_default();
    let template = include_str!("native_map_template.html");
    template
        .replace(
            "__CANDIDATE_NEIGHBOURHOOD_LAYER_CONTROL__",
            &candidate_neighbourhood_layer_control,
        )
        .replace("__STRATEGIC_NETWORK_LEGEND__", strategic_network_legend)
        .replace("__STRATEGIC_NETWORK_HELP__", strategic_network_help)
        .replace("__OFFICER_SCENARIO_ATTRIBUTE__", officer_scenario_attribute)
        .replace("__OFFICER_SCENARIO_BANNER__", &officer_scenario_banner)
        .replace("__OFFICER_FINDINGS__", &officer_findings)
        .replace("__NETWORK_DESCRIPTION__", network_description)
        .replace("__COMMUNITY_ACCESS_LABEL__", community_access_label)
        .replace("__COMMUNITY_ACCESS_HELP_LABEL__", community_access_label)
        .replace("__COMMUNITY_ACCESS_HELP__", community_access_help)
        .replace("__TITLE__", &title)
        .replace("__DEPLOYMENT__", &deployment_id)
        .replace("__BRANCH__", &branch)
        .replace("__BASE_ID__", &base_id)
        .replace("__ACCOUNTING__", &html_escape(accounting_status))
        .replace("__STATUS__", &html_escape(&run.status))
        .replace("__ATTRIBUTION__", &attribution)
        .replace("__SOURCE_ATTRIBUTIONS__", &source_attributions)
        .replace("__BUS_CONTEXT_URL__", "")
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

fn officer_scenario_banner(scenario: &PublicOfficerScenario) -> String {
    format!(
        "<section class=\"officer-scenario-banner\" aria-label=\"Illustrative officer scenario notice\"><strong>Illustrative officer-led scenario</strong><p>Scenario authority: {}</p><p>This is an illustrative scenario, not an adopted plan or an actual named-officer-issued decision.</p><p>The decision class describes the mechanical or classifier process; scenario authority is shown separately.</p></section>",
        html_escape(&scenario.authority)
    )
}

fn officer_findings(scenario: &PublicOfficerScenario) -> String {
    let unavailable = scenario
        .outcomes
        .iter()
        .filter(|outcome| outcome.status == "unavailable")
        .collect::<Vec<_>>();
    if unavailable.is_empty() {
        return String::new();
    }
    let findings = unavailable
        .iter()
        .map(|outcome| {
            let source_refs = outcome.source_refs.join("; ");
            format!(
                "<li><strong>Officer decision unavailable</strong> for {}. The baseline operation remains as context and is not officer-approved. Attribution: {}. Rationale: {}. Source references: {}.</li>",
                html_escape(&outcome.connection_id),
                html_escape(&outcome.attribution),
                html_escape(&outcome.rationale),
                html_escape(&source_refs),
            )
        })
        .collect::<String>();
    format!(
        "<section class=\"officer-findings\" aria-label=\"Unavailable officer decisions\"><h2>Unavailable officer decisions</h2><ul>{findings}</ul></section>"
    )
}

fn html_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}
