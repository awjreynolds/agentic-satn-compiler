//! Public projection for selected, provisional, and unresolved planning work.
//!
//! The planning history contains provider attempts and receipts for replay, so
//! it is deliberately not copied into this projection.  This module emits the
//! small public decision manifest plus a GeoJSON/SVG view over the admitted
//! source baseline, candidate paths, and operation outcomes.

use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

use serde::Serialize;
use serde_json::{Value, json};

use crate::compiler::{Candidate, CompileReport};
use crate::error::{Result, SatnError};
use crate::midend::{MidendRun, TypedOperation};

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
    let files = decision_map_files();

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
        render_html(
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
    for obligation in &report.access_obligations {
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

fn render_html(
    report: &CompileReport,
    run: &MidendRun,
    features: &[MapFeature],
    decisions: &[PublicDecision],
    departures: &[PublicDeparture],
    counts: &DecisionMapCounts,
    accounting_status: &str,
    files: &DecisionMapFiles,
) -> String {
    let mut coordinates = Vec::new();
    if let Some(boundary) = &report.boundary_scope {
        coordinates.extend(boundary.geometry.iter().flatten().flatten().copied());
    }
    for feature in features {
        match &feature.geometry {
            Some(MapGeometry::Line(line)) => coordinates.extend(line.iter().copied()),
            Some(MapGeometry::Point(point)) => coordinates.push(*point),
            None => {}
        }
    }
    let svg = render_svg(report, features, &coordinates);
    let decisions_html = decisions
        .iter()
        .map(|decision| {
            let uncertainty = if decision.uncertainties.is_empty() {
                "No additional unresolved judgment recorded.".to_string()
            } else {
                format!(
                    "Uncertainty: {}",
                    html_escape(&decision.uncertainties.join("; "))
                )
            };
            format!(
                "<li data-native-decision-kind=\"{}\"><strong>{}</strong> <b>{}</b> <code>{}</code>: {} <span>{}</span><small>Road context: {}</small></li>",
                html_escape(&decision.kind),
                html_escape(&decision.kind),
                html_escape(&decision.connection_label),
                html_escape(&decision.connection_id),
                html_escape(&decision.reason),
                uncertainty,
                html_escape(&decision.road_classes.join(", ")),
            )
        })
        .collect::<Vec<_>>()
        .join("");
    let departures_html = departures
        .iter()
        .map(|departure| {
            format!(
                "<li data-native-departure=\"{}\"><strong>{}</strong> <b>{}</b> <code>{}</code>: {} <span>Evidence: {}</span></li>",
                html_escape(&departure.kind),
                html_escape(if departure.kind == "a-road-departure" {
                    "A-road departure"
                } else {
                    "Source corridor departure"
                }),
                html_escape(&departure.source_reference),
                html_escape(&departure.graph_edge_id),
                html_escape(&departure.reason),
                html_escape(&departure.evidence_refs.join(", ")),
            )
        })
        .collect::<Vec<_>>()
        .join("");
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
    format!(
        r##"<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} decision map</title>
<style>
body{{font:16px system-ui,sans-serif;margin:1.5rem;max-width:96rem;color:#17202a}}
svg{{width:100%;height:65vh;min-height:24rem;background:#f6f8fa;border:1px solid #c9d1d9}}
.boundary{{fill:#dbeafe;stroke:#2563eb;stroke-width:1;opacity:.35}}
.source-baseline{{fill:none;stroke:#475569;stroke-width:1.8;opacity:.72}}
.source-strategic-a-road{{stroke:#0f766e;stroke-width:2.5}}
.source-strategic-ncn{{stroke:#2563eb;stroke-width:2.5}}
.source-strategic-cycleway{{stroke:#16a34a;stroke-width:2.5}}
.source-context{{stroke:#94a3b8;stroke-dasharray:4 5;opacity:.45}}
.selected-alignment{{fill:none;stroke:#15803d;stroke-width:4}}
.provisional-alignment{{fill:none;stroke:#d97706;stroke-width:4;stroke-dasharray:8 5}}
.unresolved-decision{{fill:none;stroke:#7c3aed;stroke-width:3;stroke-dasharray:2 5}}
.candidate-alternative{{fill:none;stroke:#64748b;stroke-width:2;stroke-dasharray:7 5;opacity:.85}}
.a-road-departure{{fill:none;stroke:#dc2626;stroke-width:5;stroke-dasharray:11 6}}
.corridor-departure{{fill:none;stroke:#be123c;stroke-width:4;stroke-dasharray:8 5}}
.network-place{{fill:#2563eb;stroke:#fff;stroke-width:1.5}}
.access-obligation{{fill:#dc2626;stroke:#fff;stroke-width:1.5}}
.key{{display:flex;gap:1rem;list-style:none;padding:0;flex-wrap:wrap}}
.key li{{display:flex;align-items:center;gap:.35rem}}
.swatch{{display:inline-block;width:2rem;height:.35rem;vertical-align:middle}}
.source-key{{background:#475569}} .selected-key{{background:#15803d}} .provisional-key{{background:#d97706}} .unresolved-key{{background:#7c3aed}} .departure-key{{background:#dc2626}} .context-key{{background:#94a3b8}}
code{{font-size:.9em}} li{{margin:.35rem 0}} small{{display:block;color:#475569;margin-top:.15rem}}
</style></head>
<body data-native-publication="native-agentic" data-network-url="decision-map.geojson" data-native-deployment="{deployment_id}" data-branch="{branch}" data-base-id="{base_id}"><h1>{title} decision map</h1>
<p data-native-branch>Deployment <code>{deployment_id}</code> · Branch <code>{branch}</code> · base <code>{base_id}</code> · accounting <strong>{accounting_status}</strong> · run status <strong>{status}</strong></p>
<p class="disclaimer">Experimental SATN POC — not an adopted plan.</p>
<p>Source baseline remains visible as the proposed strategic structure. Selected alignments are decision overlays; source provision, safety, access and adoption remain explicit unknowns where evidence is absent.</p>
<p class="source-attribution">Source attribution: {attribution} {source_attributions}</p>
<ul class="key" aria-label="Map legend">
<li><label><input type="checkbox" data-layer-toggle="source-strategic" checked> <span class="swatch source-key"></span>Strategic source baseline (A-roads, NCN, cycleways/greenways)</label></li>
<li><label><input type="checkbox" data-layer-toggle="source-context"> <span class="swatch context-key"></span>Context sources (railway, bridleway)</label></li>
<li><label><input type="checkbox" data-layer-toggle="selected-alignment" checked> <span class="swatch selected-key"></span>Selected alignment</label></li>
<li><label><input type="checkbox" data-layer-toggle="provisional-alignment" checked> <span class="swatch provisional-key"></span>Provisional alignment</label></li>
<li><label><input type="checkbox" data-layer-toggle="unresolved-decision" checked> <span class="swatch unresolved-key"></span>Unresolved decision</label></li>
<li><label><input type="checkbox" data-layer-toggle="a-road-departure" checked> <span class="swatch departure-key"></span>A-road departure</label></li>
</ul>
<p>{source_count} source baseline sections · {prepared_connections} prepared connections · {pending_connections} pending connections · {candidate_count} generated candidates · {decision_count} decisions · {unresolved_facts} unresolved facts · {unresolved_access} unresolved access obligations</p>
{svg}
<h2>Decision details</h2><ul>{decisions_html}</ul>
<h2>Source corridor departures</h2><p>Dashed sections identify only graph-bound strategic source geometry left outside a selected alignment when an admitted alternative provides the comparison. Red marks A-road sections; source-only and unknown-topology rows remain undashed baseline evidence.</p><ul>{departures_html}</ul>
<p id="native-loading-failure" role="alert" hidden>Unable to load public network data.</p>
<p><a href="{details_file}">Compact decision manifest</a> · <a href="{geojson_file}">GeoJSON download</a> · <a href="{publication_file}">Public publication manifest</a></p>
<script>
document.querySelectorAll('[data-layer-toggle]').forEach(function (toggle) {{
  function update() {{ document.querySelectorAll('[data-map-layer="' + toggle.dataset.layerToggle + '"]').forEach(function (node) {{ node.hidden = !toggle.checked; }}); }}
  toggle.addEventListener('change', update); update();
}});
document.documentElement.dataset.nativeReady = 'false';
fetch(document.body.dataset.networkUrl).then(function (response) {{
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return response.json();
}}).then(function (network) {{
  if (!document.querySelector('svg') || !document.querySelector('[data-layer-toggle]')) throw new Error('native map controls are unavailable');
  if (!network || network.type !== 'FeatureCollection' || !Array.isArray(network.features)) throw new Error('public network data is not a FeatureCollection');
  window.SATN_NATIVE_NETWORK = network;
  document.documentElement.dataset.nativeNetworkLoaded = 'true';
  document.documentElement.dataset.nativeReady = 'true';
}}).catch(function (error) {{
  var failure = document.getElementById('native-loading-failure');
  failure.hidden = false;
  failure.textContent = 'Unable to load public network data: ' + error.message;
  document.documentElement.dataset.nativeReady = 'false';
}});
</script>
</body></html>"##,
        title = title,
        deployment_id = deployment_id,
        branch = branch,
        base_id = base_id,
        attribution = attribution,
        source_attributions = source_attributions,
        status = html_escape(&run.status),
        accounting_status = html_escape(accounting_status),
        source_count = counts.source_baseline,
        prepared_connections = counts.prepared_connections,
        pending_connections = counts.pending_connections,
        candidate_count = counts.candidates,
        decision_count = counts.decisions,
        unresolved_facts = counts.unresolved_facts,
        unresolved_access = counts.unresolved_access,
        svg = svg,
        decisions_html = decisions_html,
        departures_html = if departures_html.is_empty() {
            "<li>No graph-bound A-road departure is recorded for this run.</li>".to_string()
        } else {
            departures_html
        },
        details_file = files.details,
        geojson_file = files.geojson,
        publication_file = files.publication,
    )
}

fn render_svg(report: &CompileReport, features: &[MapFeature], coordinates: &[[f64; 2]]) -> String {
    if coordinates.is_empty() {
        return "<svg viewBox=\"0 0 1000 600\" role=\"img\" aria-label=\"Empty planning decision map\"><text x=\"20\" y=\"40\">No geometry admitted</text></svg>".to_string();
    }
    let (min_x, max_x) = min_max(coordinates.iter().map(|point| point[0]));
    let (min_y, max_y) = min_max(coordinates.iter().map(|point| point[1]));
    let width = 1000.0;
    let height = 600.0;
    let pad = 24.0;
    let scale_x = (width - 2.0 * pad) / (max_x - min_x).max(0.000001);
    let scale_y = (height - 2.0 * pad) / (max_y - min_y).max(0.000001);
    let scale = scale_x.min(scale_y);
    let project = |point: [f64; 2]| {
        let x = pad + (point[0] - min_x) * scale;
        let y = height - pad - (point[1] - min_y) * scale;
        format!("{x:.2},{y:.2}")
    };
    let mut elements = Vec::new();
    if let Some(boundary) = &report.boundary_scope {
        for polygon in &boundary.geometry {
            let mut path = String::new();
            for ring in polygon {
                let Some(first) = ring.first().copied() else {
                    continue;
                };
                path.push_str(&format!("M {} ", project(first)));
                for point in ring.iter().skip(1).copied() {
                    path.push_str(&format!("L {} ", project(point)));
                }
                path.push_str("Z ");
            }
            elements.push(format!(
                "<path class=\"boundary\" fill-rule=\"evenodd\" d=\"{}\" aria-label=\"Governed boundary\" />",
                path.trim()
            ));
        }
    }
    for feature in features {
        match &feature.geometry {
            Some(MapGeometry::Line(line)) if line.len() >= 2 => {
                let label = feature
                    .properties
                    .get("label")
                    .and_then(Value::as_str)
                    .unwrap_or(&feature.kind);
                let map_layer = feature_map_layer(feature);
                let hidden = if map_layer == "source-context" {
                    " hidden"
                } else {
                    ""
                };
                let native_geometry = match feature.kind.as_str() {
                    "selected-alignment" | "provisional-alignment" | "unresolved-decision" => {
                        " data-native-strategic-geometry"
                    }
                    "a-road-departure" | "source-departure" => " data-native-departure-geometry",
                    _ => "",
                };
                elements.push(format!(
                    "<polyline class=\"{}\" data-map-layer=\"{}\"{}{} points=\"{}\" aria-label=\"{}\" title=\"{}\" />",
                    html_escape(&feature_css_class(feature)),
                    html_escape(map_layer),
                    hidden,
                    native_geometry,
                    line.iter()
                        .copied()
                        .map(project)
                        .collect::<Vec<_>>()
                        .join(" "),
                    html_escape(label),
                    html_escape(label),
                ));
            }
            Some(MapGeometry::Point(point)) => {
                let projected = project(*point);
                let (x, y) = projected.split_once(',').unwrap_or(("0", "0"));
                let label = feature
                    .properties
                    .get("name")
                    .and_then(Value::as_str)
                    .unwrap_or(&feature.kind);
                elements.push(format!(
                    "<circle class=\"{}\" data-map-layer=\"{}\" cx=\"{}\" cy=\"{}\" r=\"5\" aria-label=\"{}\" title=\"{}\" />",
                    html_escape(&feature_css_class(feature)),
                    html_escape(feature_map_layer(feature)),
                    x,
                    y,
                    html_escape(label),
                    html_escape(label),
                ));
            }
            _ => {}
        }
    }
    format!(
        "<svg viewBox=\"0 0 {width} {height}\" role=\"img\" aria-label=\"Planning decision map\">{}</svg>",
        elements.join("")
    )
}

fn feature_map_layer(feature: &MapFeature) -> &str {
    if feature.kind == "source-baseline" {
        return match feature
            .properties
            .get("baseline_layer")
            .and_then(Value::as_str)
        {
            Some("source-context") => "source-context",
            Some(_) => "source-strategic",
            None => "source-strategic",
        };
    }
    feature.kind.as_str()
}

fn feature_css_class(feature: &MapFeature) -> String {
    if feature.kind == "source-departure" {
        return "corridor-departure".to_string();
    }
    if feature.kind != "source-baseline" {
        return feature.kind.clone();
    }
    let layer = feature
        .properties
        .get("baseline_layer")
        .and_then(Value::as_str)
        .unwrap_or("source-context");
    format!("source-baseline {layer}")
}

fn min_max(values: impl Iterator<Item = f64>) -> (f64, f64) {
    values.fold((f64::INFINITY, f64::NEG_INFINITY), |(min, max), value| {
        (min.min(value), max.max(value))
    })
}

fn html_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}
