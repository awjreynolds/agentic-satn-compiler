use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap, HashMap};
use std::path::Path;
use std::time::Instant;

use serde::{Deserialize, Serialize};

use crate::config::AreaConfig;
use crate::error::Result;
use crate::geojson::{
    Feature, Geometry, canonical_tag_values, read_feature_collection, string_property,
};
use crate::geometry::enrich_network_edges;
use crate::graph::{Graph, GraphEdge, Route};
use crate::output::write_bundle;

#[derive(Debug, Clone, Default)]
pub struct CompileOptions {
    pub origin: Option<String>,
    pub destination: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceCorridor {
    pub id: String,
    pub reference: String,
    pub source_kind: String,
    pub source_id: String,
    pub scope: String,
    #[serde(default)]
    pub baseline_role: String,
    pub source_edge_ids: Vec<String>,
    #[serde(default)]
    pub graph_edge_ids: Vec<String>,
    pub geometry: Vec<Vec<[f64; 2]>>,
    pub topology_status: String,
    pub attachment_status: String,
    #[serde(default)]
    pub provision_status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BoundaryScope {
    pub id: String,
    pub name: String,
    pub geometry: Vec<Vec<Vec<[f64; 2]>>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UnknownFact {
    pub id: String,
    pub subject: String,
    pub status: String,
    pub reason: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct NetworkPlace {
    pub id: String,
    pub name: String,
    pub source_id: String,
    pub place_class: String,
    pub geometry: [f64; 2],
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct AccessObligation {
    pub id: String,
    pub kind: String,
    pub source_id: String,
    pub name: String,
    pub geometry: Option<[f64; 2]>,
    pub access_point_status: Option<String>,
    pub access_point_source_id: Option<String>,
    pub access_point_rationale: Option<String>,
    pub disposition: String,
    pub reason: String,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct AccountingSummary {
    pub status: String,
    pub complete: bool,
    pub source_baseline_count: usize,
    pub network_place_count: usize,
    pub obligation_count: usize,
    pub unresolved_count: usize,
    pub network_gap_count: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Connection {
    pub id: String,
    pub origin_place_id: String,
    pub origin_name: String,
    pub destination_place_id: String,
    pub destination_name: String,
    pub origin_node: String,
    pub destination_node: String,
    pub cross_region_edge_ids: Vec<String>,
    pub road_classes: Vec<String>,
    pub preferred_classes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Candidate {
    pub id: String,
    pub connection_id: String,
    pub status: String,
    pub decision_class: String,
    pub role: String,
    pub role_aliases: Vec<String>,
    pub length_m: f64,
    pub search_cost_m: f64,
    pub a_road_share: f64,
    pub ncn_share: f64,
    pub cycle_alignment_bases: Vec<String>,
    pub topology_status: String,
    pub provision_status: String,
    pub path_edge_ids: Vec<String>,
    pub geometry: Vec<[f64; 2]>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Operation {
    pub id: String,
    pub kind: String,
    pub decision_class: String,
    pub candidate_id: String,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompileReport {
    pub area_id: String,
    pub title: String,
    pub snapshot_id: String,
    pub source_inventory_count: usize,
    pub unknown_fact_count: usize,
    pub connection_count: usize,
    pub candidate_count: usize,
    pub operation_count: usize,
    pub boundary_scope: Option<BoundaryScope>,
    pub source_inventory: Vec<SourceCorridor>,
    pub unknown_facts: Vec<UnknownFact>,
    #[serde(default)]
    pub network_places: Vec<NetworkPlace>,
    #[serde(default)]
    pub access_obligations: Vec<AccessObligation>,
    #[serde(default)]
    pub destination_profile: String,
    #[serde(default)]
    pub accounting: AccountingSummary,
    pub connections: Vec<Connection>,
    pub candidates: Vec<Candidate>,
    pub operations: Vec<Operation>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ProgressEvent {
    pub stage: String,
    pub message: String,
    pub elapsed_ms: u128,
    pub source_inventory_count: usize,
    pub connection_count: usize,
    pub candidate_count: usize,
}

#[derive(Debug, Clone)]
struct Place {
    id: String,
    name: String,
    point: [f64; 2],
}

#[derive(Debug, Clone)]
struct LoadedNetwork {
    graph: Graph,
    features: Vec<Feature>,
}

#[derive(Debug, Clone)]
struct PreparedAdjacency {
    left: usize,
    right: usize,
    edge_ids: Vec<String>,
    road_classes: Vec<String>,
    preferred_classes: Vec<String>,
}

#[derive(Debug, Clone)]
struct DistanceEntry {
    distance: f64,
    node: String,
}

const ROUTE_ROLES: [&str; 4] = ["direct", "strategic-spine", "ncn-informed", "low-traffic"];

impl PartialEq for DistanceEntry {
    fn eq(&self, other: &Self) -> bool {
        self.distance == other.distance && self.node == other.node
    }
}

impl Eq for DistanceEntry {}

impl Ord for DistanceEntry {
    fn cmp(&self, other: &Self) -> Ordering {
        other
            .distance
            .partial_cmp(&self.distance)
            .unwrap_or(Ordering::Equal)
            .then_with(|| self.node.cmp(&other.node))
    }
}

impl PartialOrd for DistanceEntry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

pub fn compile(
    config_path: &Path,
    output_dir: &Path,
    options: CompileOptions,
) -> Result<CompileReport> {
    let mut ignored_progress = |_event: ProgressEvent| {};
    compile_with_progress(config_path, output_dir, options, &mut ignored_progress)
}

pub fn compile_with_progress(
    config_path: &Path,
    output_dir: &Path,
    options: CompileOptions,
    progress: &mut dyn FnMut(ProgressEvent),
) -> Result<CompileReport> {
    let started = Instant::now();
    let config = AreaConfig::read(config_path)?;
    emit(
        progress,
        &started,
        "preparation",
        "reading governed area configuration and pinned snapshot",
        0,
        0,
        0,
    );
    let snapshot_path = config.snapshot_path(config_path);
    let network_path = snapshot_path.join("network.geojson");
    let places_path = snapshot_path.join("places.geojson");
    let network_features = read_feature_collection(&network_path)?;
    let context_features = read_optional_features(&snapshot_path.join("context.geojson"))?;
    let edge_evidence = enrich_network_edges(&network_features, &context_features)?;
    let network = LoadedNetwork {
        graph: Graph::from_features(&network_features, &edge_evidence)?,
        features: network_features,
    };
    let places_features = read_feature_collection(&places_path)?;
    let official_features =
        read_optional_features(&snapshot_path.join("official-road-classification.geojson"))?;
    let boundary_features = read_optional_features(&snapshot_path.join("boundary.geojson"))?;
    let boundary_scope = admit_boundary_scope(&boundary_features);
    let graph_geometry_bindings = graph_geometry_bindings(&network.graph);
    let place_labels = read_optional_features(&snapshot_path.join("osm-place-features.geojson"))?;
    let places = if place_labels.is_empty() {
        admit_places(&places_features, boundary_scope.as_ref())
    } else {
        admit_places(&place_labels, boundary_scope.as_ref())
    };
    let network_places = admit_network_places(
        &places_features,
        boundary_scope.as_ref(),
        &config.source.community_place_types,
    );
    let source_inventory = admit_source_inventory(
        &network.features,
        &network.graph,
        &context_features,
        &official_features,
        &graph_geometry_bindings,
    );
    let unknown_facts = source_inventory
        .iter()
        .flat_map(|corridor| {
            let mut facts = vec![UnknownFact {
                id: format!("unknown:{}", corridor.id),
                subject: corridor.id.clone(),
                status: "unknown".to_string(),
                reason: format!(
                    "Pinned {} source geometry does not establish current active-travel provision.",
                    corridor.baseline_role
                ),
            }];
            if corridor.attachment_status != "graph-edge" {
                facts.push(UnknownFact {
                    id: format!("unknown:topology:{}", corridor.id),
                    subject: corridor.id.clone(),
                    status: "unknown".to_string(),
                    reason: "No admitted graph-edge attachment has been established for this source geometry; topology remains unknown."
                        .to_string(),
                });
            }
            facts
        })
        .collect::<Vec<_>>();
    let destination_profile = "unconfigured".to_string();
    let access_obligations = build_access_obligations(&network_places, &context_features);
    let accounting =
        derive_accounting(&source_inventory, &access_obligations, &destination_profile);
    emit(
        progress,
        &started,
        "graph",
        &format!(
            "indexed {} directed source edges and admitted {} city/town places",
            network.graph.edges.len(),
            places.len()
        ),
        source_inventory.len(),
        0,
        0,
    );

    let (connections, candidates) = build_connections_and_candidates(
        &places,
        &network.graph,
        &options,
        progress,
        &started,
        source_inventory.len(),
        config.source.urban_scope_buffer_km * 1000.0,
    )?;
    // Candidates are mechanically generated. Record that compact operation, but do not
    // emit a selection operation until a later judgment boundary actually selects one.
    let operations = candidates
        .iter()
        .map(|candidate| Operation {
            id: format!("operation:{}", candidate.id),
            kind: "generate-candidate".to_string(),
            decision_class: "mechanical".to_string(),
            candidate_id: candidate.id.clone(),
            reason: "Deterministic measured-length route from a prepared graph adjacency."
                .to_string(),
        })
        .collect::<Vec<_>>();
    let report = CompileReport {
        area_id: config
            .area_id
            .clone()
            .unwrap_or_else(|| "unknown-area".to_string()),
        title: config.title(),
        snapshot_id: config.source.snapshot_id.clone(),
        source_inventory_count: source_inventory.len(),
        unknown_fact_count: unknown_facts.len(),
        connection_count: connections.len(),
        candidate_count: candidates.len(),
        operation_count: operations.len(),
        boundary_scope,
        source_inventory,
        unknown_facts,
        network_places,
        access_obligations,
        destination_profile,
        accounting,
        connections,
        candidates,
        operations,
    };
    emit(
        progress,
        &started,
        "publication",
        "writing compact summary, GeoJSON and review map",
        report.source_inventory_count,
        report.connection_count,
        report.candidate_count,
    );
    write_bundle(output_dir, &report)?;
    emit(
        progress,
        &started,
        "completed",
        "mechanical compilation completed",
        report.source_inventory_count,
        report.connection_count,
        report.candidate_count,
    );
    Ok(report)
}

fn read_optional_features(path: &Path) -> Result<Vec<Feature>> {
    if path.exists() {
        read_feature_collection(path)
    } else {
        Ok(Vec::new())
    }
}

fn emit(
    progress: &mut dyn FnMut(ProgressEvent),
    started: &Instant,
    stage: &str,
    message: &str,
    source_inventory_count: usize,
    connection_count: usize,
    candidate_count: usize,
) {
    progress(ProgressEvent {
        stage: stage.to_string(),
        message: message.to_string(),
        elapsed_ms: started.elapsed().as_millis(),
        source_inventory_count,
        connection_count,
        candidate_count,
    });
}

fn admit_places(features: &[Feature], boundary: Option<&BoundaryScope>) -> Vec<Place> {
    let mut places = features
        .iter()
        .filter_map(|feature| {
            let Geometry::Point(point) = feature.geometry else {
                return None;
            };
            let class = string_property(&feature.properties, "place_class")
                .or_else(|| string_property(&feature.properties, "place"))
                .or_else(|| string_property(&feature.properties, "kind"))
                .unwrap_or_default()
                .to_ascii_lowercase();
            // The preparation contract is city/town adjacency. Community eligibility is
            // retained as source evidence, but it does not create a Cartesian roster.
            if class != "city" && class != "town" {
                return None;
            }
            let id = string_property(&feature.properties, "place_id")
                .or_else(|| string_property(&feature.properties, "source_id"))
                .or_else(|| string_property(&feature.properties, "id"))?;
            let name = string_property(&feature.properties, "name").unwrap_or_else(|| id.clone());
            if boundary.is_some_and(|boundary| !boundary_contains(boundary, point)) {
                return None;
            }
            Some(Place { id, name, point })
        })
        .collect::<Vec<_>>();
    places.sort_by(|left, right| left.id.cmp(&right.id));
    places
}

fn admit_source_inventory(
    network_features: &[Feature],
    graph: &Graph,
    context_features: &[Feature],
    official_features: &[Feature],
    graph_geometry_bindings: &HashMap<String, Vec<String>>,
) -> Vec<SourceCorridor> {
    let mut groups: BTreeMap<String, SourceCorridor> = BTreeMap::new();
    for (index, feature) in network_features.iter().enumerate() {
        let references = canonical_tag_values(&feature.properties, "ref");
        let highways = canonical_tag_values(&feature.properties, "highway");
        let mut roles = Vec::new();
        if references.iter().any(|reference| is_a_reference(reference)) {
            roles.push("a-road");
        }
        if highways.iter().any(|highway| is_existing_cycleway(highway)) {
            roles.push("existing-cycleway");
        }
        if highways
            .iter()
            .any(|highway| highway.eq_ignore_ascii_case("bridleway"))
        {
            roles.push("bridleway");
        }
        if roles.is_empty() {
            continue;
        }
        let edge_id = graph
            .edges
            .get(index)
            .map(|edge| edge.id.clone())
            .unwrap_or_else(|| format!("network-feature:{index}"));
        for (line_index, geometry) in line_geometries(feature).into_iter().enumerate() {
            for role in &roles {
                let references_for_role = if *role == "a-road" {
                    references
                        .iter()
                        .filter(|reference| is_a_reference(reference))
                        .cloned()
                        .collect::<Vec<_>>()
                } else if references.is_empty() {
                    vec![role.to_string()]
                } else {
                    vec![references[0].clone()]
                };
                for reference in references_for_role {
                    add_corridor(
                        &mut groups,
                        format!("source:network:{role}:{reference}"),
                        reference,
                        "network",
                        "network.geojson".to_string(),
                        "pinned-network",
                        role,
                        format!("{edge_id}:{line_index}"),
                        geometry.clone(),
                        vec![edge_id.clone()],
                    );
                }
            }
        }
    }
    for (index, feature) in context_features.iter().enumerate() {
        let feature_type = string_property(&feature.properties, "feature_type").unwrap_or_default();
        let category = string_property(&feature.properties, "category").unwrap_or_default();
        let Some(baseline_role) = context_baseline_role(&feature_type, &category) else {
            continue;
        };
        let reference = string_property(&feature.properties, "name")
            .filter(|value| is_a_reference(value))
            .or_else(|| string_property(&feature.properties, "ncn_evidence_role"))
            .unwrap_or_else(|| baseline_role.to_string());
        let source_id = string_property(&feature.properties, "evidence_id")
            .unwrap_or_else(|| format!("context-feature:{index}"));
        let scope = string_property(&feature.properties, "network_scope")
            .unwrap_or_else(|| "unknown-scope".to_string());
        for (line_index, geometry) in line_geometries(feature).into_iter().enumerate() {
            let graph_edge_ids = graph_geometry_bindings
                .get(&geometry_key(&geometry))
                .cloned()
                .unwrap_or_default();
            add_corridor(
                &mut groups,
                format!("source:context:{baseline_role}:{source_id}"),
                reference.clone(),
                "context",
                source_id.clone(),
                &scope,
                baseline_role,
                format!("{source_id}:{index}:{line_index}"),
                geometry,
                graph_edge_ids,
            );
        }
    }
    for (index, feature) in official_features.iter().enumerate() {
        let classification = string_property(&feature.properties, "official_classification")
            .unwrap_or_default()
            .to_ascii_lowercase();
        let number = string_property(&feature.properties, "official_road_number");
        if !classification.contains("a-road") && !number.as_deref().is_some_and(is_a_reference) {
            continue;
        }
        let reference = number.unwrap_or_else(|| "A-road".to_string());
        let source_id = string_property(&feature.properties, "official_feature_id")
            .unwrap_or_else(|| format!("official-feature:{index}"));
        for (line_index, geometry) in line_geometries(feature).into_iter().enumerate() {
            let graph_edge_ids = graph_geometry_bindings
                .get(&geometry_key(&geometry))
                .cloned()
                .unwrap_or_default();
            add_corridor(
                &mut groups,
                format!("source:official:{source_id}"),
                reference.clone(),
                "official",
                source_id.clone(),
                "governed-official",
                "a-road",
                format!("{source_id}:{index}:{line_index}"),
                geometry,
                graph_edge_ids,
            );
        }
    }
    groups.into_values().collect()
}

fn add_corridor(
    groups: &mut BTreeMap<String, SourceCorridor>,
    key: String,
    reference: String,
    source_kind: &str,
    source_id: String,
    scope: &str,
    baseline_role: &str,
    source_edge_id: String,
    geometry: Vec<[f64; 2]>,
    graph_edge_ids: Vec<String>,
) {
    let graph_bound = !graph_edge_ids.is_empty();
    let corridor = groups.entry(key.clone()).or_insert_with(|| SourceCorridor {
        id: key,
        reference,
        source_kind: source_kind.to_string(),
        source_id,
        scope: scope.to_string(),
        baseline_role: baseline_role.to_string(),
        source_edge_ids: Vec::new(),
        graph_edge_ids: Vec::new(),
        geometry: Vec::new(),
        topology_status: if graph_bound {
            "graph-bound".to_string()
        } else {
            "source-only".to_string()
        },
        attachment_status: if graph_bound {
            "graph-edge".to_string()
        } else {
            "unknown".to_string()
        },
        provision_status: "unknown".to_string(),
    });
    let previous_graph_bound = corridor.attachment_status == "graph-edge";
    if previous_graph_bound != graph_bound {
        corridor.topology_status = "partially-graph-bound".to_string();
        corridor.attachment_status = "partial".to_string();
    }
    if !corridor.source_edge_ids.contains(&source_edge_id) {
        corridor.source_edge_ids.push(source_edge_id);
    }
    for graph_edge_id in graph_edge_ids {
        if !corridor.graph_edge_ids.contains(&graph_edge_id) {
            corridor.graph_edge_ids.push(graph_edge_id);
        }
    }
    if !corridor
        .geometry
        .iter()
        .any(|existing| equivalent_geometry(existing, &geometry))
    {
        corridor.geometry.push(geometry);
    }
}

fn equivalent_geometry(left: &[[f64; 2]], right: &[[f64; 2]]) -> bool {
    left == right || left.iter().eq(right.iter().rev())
}

fn geometry_key(line: &[[f64; 2]]) -> String {
    format!("{line:?}")
}

fn graph_geometry_bindings(graph: &Graph) -> HashMap<String, Vec<String>> {
    let mut bindings = HashMap::new();
    for edge in &graph.edges {
        for key in [
            geometry_key(&edge.geometry),
            geometry_key(&edge.geometry.iter().copied().rev().collect::<Vec<_>>()),
        ] {
            let values = bindings.entry(key).or_insert_with(Vec::new);
            if !values.contains(&edge.id) {
                values.push(edge.id.clone());
            }
        }
    }
    bindings
}

fn context_baseline_role(feature_type: &str, category: &str) -> Option<&'static str> {
    let feature_type = feature_type.to_ascii_lowercase();
    let category = category.to_ascii_lowercase();
    if feature_type == "a-road-spine" || category.contains("a-road") {
        Some("a-road")
    } else if feature_type == "ncn-route" {
        Some("current-ncn")
    } else if feature_type == "ncn-link" {
        Some("ncn-link")
    } else if feature_type == "declassified-ncn-route" {
        Some("declassified-ncn")
    } else if feature_type == "greenway-cycleway" || category.contains("greenway") {
        Some("greenway-cycleway")
    } else if feature_type == "cycleway" || category.contains("cycleway") {
        Some("existing-cycleway")
    } else if feature_type == "bridleway" || category.contains("bridleway") {
        Some("bridleway")
    } else if (feature_type.contains("railway") || category.contains("railway"))
        && [feature_type.as_str(), category.as_str()]
            .iter()
            .any(|value| {
                value.contains("former") || value.contains("disused") || value.contains("abandoned")
            })
    {
        Some("former-railway")
    } else if feature_type == "railway" || category.contains("railway") {
        Some("railway")
    } else {
        None
    }
}

fn is_existing_cycleway(highway: &str) -> bool {
    matches!(
        highway,
        "cycleway"
            | "cycle_track"
            | "cycle-track"
            | "greenway"
            | "path-cycleway"
            | "shared_use_path"
    )
}

fn admit_network_places(
    features: &[Feature],
    boundary: Option<&BoundaryScope>,
    allowed_types: &[String],
) -> Vec<NetworkPlace> {
    features
        .iter()
        .filter_map(|feature| {
            let Geometry::Point(point) = feature.geometry else {
                return None;
            };
            let place_class = string_property(&feature.properties, "place_class")
                .or_else(|| string_property(&feature.properties, "place"))
                .unwrap_or_default()
                .to_ascii_lowercase();
            if !allowed_types
                .iter()
                .any(|value| value.eq_ignore_ascii_case(&place_class))
            {
                return None;
            }
            if boundary.is_some_and(|scope| !boundary_contains(scope, point)) {
                return None;
            }
            let id = string_property(&feature.properties, "place_id")
                .or_else(|| string_property(&feature.properties, "source_id"))
                .or_else(|| string_property(&feature.properties, "id"))?;
            let name = string_property(&feature.properties, "name").unwrap_or_else(|| id.clone());
            Some(NetworkPlace {
                source_id: string_property(&feature.properties, "source_id")
                    .unwrap_or_else(|| id.clone()),
                id,
                name,
                place_class,
                geometry: point,
            })
        })
        .collect()
}

fn build_access_obligations(
    network_places: &[NetworkPlace],
    context_features: &[Feature],
) -> Vec<AccessObligation> {
    let mut obligations = network_places
        .iter()
        .map(|place| AccessObligation {
            id: format!("obligation:community:{}", place.id),
            kind: "community".to_string(),
            source_id: place.source_id.clone(),
            name: place.name.clone(),
            geometry: Some(place.geometry),
            access_point_status: None,
            access_point_source_id: None,
            access_point_rationale: None,
            disposition: "unresolved".to_string(),
            reason: "No selected access support is present in the mechanical compilation."
                .to_string(),
        })
        .collect::<Vec<_>>();
    obligations.extend(context_features.iter().filter_map(|feature| {
        if string_property(&feature.properties, "feature_type")?.to_ascii_lowercase() != "school" {
            return None;
        }
        if !string_property(&feature.properties, "school_obligation_eligible")
            .is_some_and(|value| value.eq_ignore_ascii_case("true"))
        {
            return None;
        }
        let id = string_property(&feature.properties, "evidence_id")?;
        let name = string_property(&feature.properties, "name").unwrap_or_else(|| id.clone());
        let point = match feature.geometry {
            Geometry::Point(point) => Some(point),
            _ => None,
        };
        let access_point_status = string_property(&feature.properties, "access_point_status");
        let unresolved_access = access_point_status
            .as_deref()
            .is_none_or(|status| status.eq_ignore_ascii_case("unresolved"));
        Some(AccessObligation {
            id: format!("obligation:school:{id}"),
            kind: "school".to_string(),
            source_id: string_property(&feature.properties, "source_id").unwrap_or(id.clone()),
            name,
            geometry: point,
            access_point_status,
            access_point_source_id: string_property(
                &feature.properties,
                "access_point_source_id",
            ),
            access_point_rationale: string_property(
                &feature.properties,
                "access_point_rationale",
            ),
            disposition: if unresolved_access {
                "network-gap".to_string()
            } else {
                "unresolved".to_string()
            },
            reason: if unresolved_access {
                "School access-point evidence is unresolved; no route or entrance is invented."
                    .to_string()
            } else {
                "Access point is evidenced, but no selected support is present in the mechanical compilation."
                    .to_string()
            },
        })
    }));
    obligations.sort_by(|left, right| left.id.cmp(&right.id));
    obligations
}

fn derive_accounting(
    source_inventory: &[SourceCorridor],
    obligations: &[AccessObligation],
    destination_profile: &str,
) -> AccountingSummary {
    let network_gap_count = obligations
        .iter()
        .filter(|obligation| obligation.disposition == "network-gap")
        .count();
    let unresolved_count = source_inventory
        .iter()
        .filter(|source| {
            source.attachment_status != "graph-edge" || source.provision_status == "unknown"
        })
        .count()
        + obligations
            .iter()
            .filter(|obligation| obligation.disposition != "served")
            .count();
    let complete = destination_profile == "configured"
        && network_gap_count == 0
        && obligations
            .iter()
            .all(|obligation| obligation.disposition == "served")
        && source_inventory
            .iter()
            .all(|source| source.attachment_status == "graph-edge");
    AccountingSummary {
        status: if complete {
            "complete".to_string()
        } else {
            "reviewable-with-gaps".to_string()
        },
        complete,
        source_baseline_count: source_inventory.len(),
        network_place_count: obligations
            .iter()
            .filter(|obligation| obligation.kind == "community")
            .count(),
        obligation_count: obligations.len(),
        unresolved_count,
        network_gap_count,
    }
}

fn admit_boundary_scope(features: &[Feature]) -> Option<BoundaryScope> {
    let feature = features.first()?;
    let geometry = match &feature.geometry {
        Geometry::Polygon(rings) => vec![rings.clone()],
        Geometry::MultiPolygon(polygons) => polygons.clone(),
        _ => return None,
    };
    Some(BoundaryScope {
        id: string_property(&feature.properties, "osm_id")
            .or_else(|| string_property(&feature.properties, "place_id"))
            .unwrap_or_else(|| "boundary".to_string()),
        name: string_property(&feature.properties, "name")
            .unwrap_or_else(|| "governed boundary".to_string()),
        geometry,
    })
}

fn boundary_contains(boundary: &BoundaryScope, point: [f64; 2]) -> bool {
    boundary.geometry.iter().any(|polygon| {
        let Some(outer) = polygon.first() else {
            return false;
        };
        if !point_in_ring(outer, point) {
            return false;
        }
        !polygon
            .iter()
            .skip(1)
            .any(|hole| point_in_ring(hole, point))
    })
}

fn point_in_ring(ring: &[[f64; 2]], point: [f64; 2]) -> bool {
    let mut inside = false;
    for (left, right) in ring
        .iter()
        .zip(ring.iter().cycle().skip(1))
        .take(ring.len())
    {
        let crosses = (left[1] > point[1]) != (right[1] > point[1]);
        if crosses {
            let intersection =
                (right[0] - left[0]) * (point[1] - left[1]) / (right[1] - left[1]) + left[0];
            if point[0] < intersection {
                inside = !inside;
            }
        }
    }
    inside
}

fn line_geometries(feature: &Feature) -> Vec<Vec<[f64; 2]>> {
    match &feature.geometry {
        Geometry::LineString(line) => vec![line.clone()],
        Geometry::MultiLineString(lines) => lines.clone(),
        _ => Vec::new(),
    }
}

fn is_a_reference(value: &str) -> bool {
    value
        .trim()
        .to_ascii_uppercase()
        .split([';', ',', ' '])
        .any(|part| {
            part.starts_with('A')
                && part.len() > 1
                && part[1..]
                    .chars()
                    .all(|character| character.is_ascii_digit())
        })
}

fn build_connections_and_candidates(
    places: &[Place],
    graph: &Graph,
    options: &CompileOptions,
    progress: &mut dyn FnMut(ProgressEvent),
    started: &Instant,
    source_inventory_count: usize,
    attachment_extent_m: f64,
) -> Result<(Vec<Connection>, Vec<Candidate>)> {
    let prepared = prepare_adjacencies(places, graph, attachment_extent_m);
    emit(
        progress,
        started,
        "mechanical",
        &format!(
            "prepared {} graph-supported city/town adjacencies",
            prepared.len()
        ),
        source_inventory_count,
        0,
        0,
    );
    let mut connections = Vec::new();
    let mut candidates = Vec::new();
    for adjacency in prepared {
        let left = &places[adjacency.left];
        let right = &places[adjacency.right];
        if !matches_options(left, right, options) {
            continue;
        }
        let connection_id = format!("prepared-urban-journey:{}:{}", left.id, right.id);
        let left_routing_node = left_node(left, graph, attachment_extent_m);
        let right_routing_node = left_node(right, graph, attachment_extent_m);
        let (origin, destination, direct_route) =
            match graph.route(&left_routing_node, &right_routing_node, "direct") {
                Some(route) => (left, right, Some(route)),
                None => match graph.route(&right_routing_node, &left_routing_node, "direct") {
                    Some(route) => (right, left, Some(route)),
                    None => (left, right, None),
                },
            };
        let origin_node = left_node(origin, graph, attachment_extent_m);
        let destination_node = left_node(destination, graph, attachment_extent_m);
        connections.push(Connection {
            id: connection_id.clone(),
            origin_place_id: origin.id.clone(),
            origin_name: origin.name.clone(),
            destination_place_id: destination.id.clone(),
            destination_name: destination.name.clone(),
            origin_node: origin_node.clone(),
            destination_node: destination_node.clone(),
            cross_region_edge_ids: adjacency.edge_ids,
            road_classes: adjacency.road_classes,
            preferred_classes: adjacency.preferred_classes,
        });
        if let Some(route) = direct_route {
            let mut connection_candidates = Vec::new();
            for role in ROUTE_ROLES {
                let route = if role == "direct" {
                    Some(route.clone())
                } else {
                    graph.route(&origin_node, &destination_node, role)
                };
                let Some(route) = route else {
                    continue;
                };
                if let Some(existing) = connection_candidates
                    .iter_mut()
                    .find(|candidate: &&mut Candidate| candidate.path_edge_ids == route.edge_ids)
                {
                    if !existing.role_aliases.iter().any(|alias| alias == role) {
                        existing.role_aliases.push(role.to_string());
                    }
                    continue;
                }
                let candidate = candidate_from_route(
                    format!("candidate:{connection_id}:{role}"),
                    connection_id.clone(),
                    role,
                    route,
                );
                connection_candidates.push(candidate);
            }
            candidates.extend(connection_candidates);
        }
        emit(
            progress,
            started,
            "mechanical",
            "prepared graph-supported connection and candidate status",
            source_inventory_count,
            connections.len(),
            candidates.len(),
        );
    }
    Ok((connections, candidates))
}

fn matches_options(left: &Place, right: &Place, options: &CompileOptions) -> bool {
    let matches = |value: &str, place: &Place| value == place.id || value == place.name;
    match (&options.origin, &options.destination) {
        (None, None) => true,
        (Some(origin), None) => matches(origin, left) || matches(origin, right),
        (None, Some(destination)) => matches(destination, left) || matches(destination, right),
        (Some(origin), Some(destination)) => {
            (matches(origin, left) && matches(destination, right))
                || (matches(origin, right) && matches(destination, left))
        }
    }
}

fn left_node(place: &Place, graph: &Graph, attachment_extent_m: f64) -> String {
    graph
        .nearest_scoped_node(place.point, attachment_extent_m)
        .unwrap_or_default()
}

fn prepare_adjacencies(
    places: &[Place],
    graph: &Graph,
    attachment_extent_m: f64,
) -> Vec<PreparedAdjacency> {
    let mut undirected: HashMap<String, Vec<(String, f64)>> = HashMap::new();
    for edge in &graph.edges {
        undirected
            .entry(edge.from.clone())
            .or_default()
            .push((edge.to.clone(), edge.length_m));
        undirected
            .entry(edge.to.clone())
            .or_default()
            .push((edge.from.clone(), edge.length_m));
    }
    let mut owners: HashMap<String, (f64, usize)> = HashMap::new();
    for (place_index, place) in places.iter().enumerate() {
        let Some(start) = graph.nearest_scoped_node(place.point, attachment_extent_m) else {
            continue;
        };
        let mut distances = HashMap::new();
        let mut queue = BinaryHeap::new();
        distances.insert(start.clone(), 0.0);
        queue.push(DistanceEntry {
            distance: 0.0,
            node: start,
        });
        while let Some(DistanceEntry { distance, node }) = queue.pop() {
            if distance > *distances.get(&node).unwrap_or(&f64::INFINITY) {
                continue;
            }
            let should_take = owners.get(&node).is_none_or(|(known, known_place)| {
                distance < *known || (distance == *known && place.id < places[*known_place].id)
            });
            if should_take {
                owners.insert(node.clone(), (distance, place_index));
            }
            for (next, length_m) in undirected.get(&node).into_iter().flatten() {
                let next_distance = distance + length_m;
                if next_distance < *distances.get(next).unwrap_or(&f64::INFINITY) {
                    distances.insert(next.clone(), next_distance);
                    queue.push(DistanceEntry {
                        distance: next_distance,
                        node: next.clone(),
                    });
                }
            }
        }
    }
    let mut groups: BTreeMap<(usize, usize), (BTreeSet<String>, BTreeSet<String>)> =
        BTreeMap::new();
    for edge in &graph.edges {
        let Some((_, left_owner)) = owners.get(&edge.from) else {
            continue;
        };
        let Some((_, right_owner)) = owners.get(&edge.to) else {
            continue;
        };
        if left_owner == right_owner {
            continue;
        }
        let pair = if left_owner < right_owner {
            (*left_owner, *right_owner)
        } else {
            (*right_owner, *left_owner)
        };
        let group = groups.entry(pair).or_default();
        group.0.insert(edge.id.clone());
        for class in classify_edge(edge) {
            group.1.insert(class);
        }
    }
    groups
        .into_iter()
        .map(|((left, right), (edge_ids, classes))| {
            let preferred_classes = classes
                .iter()
                .filter(|class| {
                    matches!(
                        class.as_str(),
                        "a-road-reference" | "a-road-highway" | "cycleway" | "ncn"
                    )
                })
                .cloned()
                .collect();
            PreparedAdjacency {
                left,
                right,
                edge_ids: edge_ids.into_iter().collect(),
                road_classes: classes.into_iter().collect(),
                preferred_classes,
            }
        })
        .collect()
}

fn classify_edge(edge: &GraphEdge) -> BTreeSet<String> {
    let mut classes = BTreeSet::new();
    if edge.references.iter().any(|value| is_a_reference(value)) {
        classes.insert("a-road-reference".to_string());
    }
    if edge.highways.iter().any(|highway| {
        matches!(
            highway.as_str(),
            "trunk" | "primary" | "trunk_link" | "primary_link"
        )
    }) {
        classes.insert("a-road-highway".to_string());
    }
    if edge.highways.iter().any(|highway| {
        matches!(
            highway.as_str(),
            "cycleway"
                | "cycle_track"
                | "cycle-track"
                | "greenway"
                | "path-cycleway"
                | "shared_use_path"
        )
    }) {
        classes.insert("cycleway".to_string());
    }
    if edge.references.iter().any(|reference| {
        reference
            .trim()
            .to_ascii_uppercase()
            .split([';', ',', ' '])
            .any(|part| {
                part.starts_with('B')
                    && part.len() > 1
                    && part[1..]
                        .chars()
                        .all(|character| character.is_ascii_digit())
            })
    }) {
        classes.insert("b-road-reference".to_string());
    }
    if classes.is_empty() {
        classes.insert("local".to_string());
    }
    classes
}

fn candidate_from_route(id: String, connection_id: String, role: &str, route: Route) -> Candidate {
    let measured_length = route.length_m;
    Candidate {
        id,
        connection_id,
        status: "mechanical-candidate".to_string(),
        decision_class: "mechanical".to_string(),
        role: role.to_string(),
        role_aliases: Vec::new(),
        length_m: measured_length,
        search_cost_m: route.search_cost_m,
        a_road_share: if measured_length == 0.0 {
            0.0
        } else {
            route.a_road_length_m / measured_length
        },
        ncn_share: if measured_length == 0.0 {
            0.0
        } else {
            route.ncn_length_m / measured_length
        },
        cycle_alignment_bases: route.cycle_alignment_bases,
        topology_status: "graph-supported".to_string(),
        provision_status: "unknown".to_string(),
        path_edge_ids: route.edge_ids,
        geometry: route.geometry,
    }
}
