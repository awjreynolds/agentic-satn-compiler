use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap, HashMap};
use std::path::Path;
use std::time::Instant;

use serde::{Deserialize, Serialize};
use serde_json::Value;

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
pub struct SchoolContext {
    pub id: String,
    pub source_id: String,
    pub name: String,
    pub school_obligation_eligible: bool,
    pub geometry: [f64; 2],
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct CommunityAccessBenefit {
    pub destination_name: String,
    pub full_route_length_m: f64,
    pub primary_access_plus_onward_m: Option<f64>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct CommunityAccess {
    pub community_id: String,
    pub source_id: String,
    pub name: String,
    pub geometry: [f64; 2],
    pub status: String,
    #[serde(default)]
    pub decision_class: String,
    pub is_primary: bool,
    pub attachment_node: Option<String>,
    pub attachment_distance_m: Option<f64>,
    pub joined_spine_id: Option<String>,
    pub access_length_m: Option<f64>,
    pub path_edge_ids: Vec<String>,
    #[serde(default)]
    pub path_geometry: Vec<[f64; 2]>,
    #[serde(default)]
    pub onward_destinations: Vec<String>,
    #[serde(default)]
    pub onward_benefits: Vec<CommunityAccessBenefit>,
    #[serde(default)]
    pub joined_spine_reference: Option<String>,
    pub provision_status: String,
    pub reason: String,
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
    /// Geometry for each graph edge in `path_edge_ids`, in the same order.
    ///
    /// This is intentionally compact per-candidate evidence used by the
    /// publication layer to identify actual source sections.  It is not a
    /// copy of the whole graph and old reports remain readable.
    #[serde(default)]
    pub path_edge_geometries: Vec<Vec<[f64; 2]>>,
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
    #[serde(default)]
    pub deployment_id: String,
    #[serde(default)]
    pub attribution: String,
    #[serde(default)]
    pub source_attributions: Vec<String>,
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
    pub school_context: Vec<SchoolContext>,
    #[serde(default)]
    pub community_access: Vec<CommunityAccess>,
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
    let (attribution, source_attributions) = read_snapshot_attributions(&snapshot_path)?;
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
    let school_context = admit_school_context(&context_features);
    let rural_community_count = network_places
        .iter()
        .filter(|place| is_rural_community(place))
        .count();
    emit(
        progress,
        &started,
        "community-access",
        &format!(
            "evaluating shortest source-bound spine access for {rural_community_count} rural communities"
        ),
        source_inventory.len(),
        0,
        0,
    );
    let community_access =
        build_community_access(&network_places, &places, &network.graph, &source_inventory);
    emit(
        progress,
        &started,
        "community-access",
        &format!(
            "retained {} primary and alternate community access records",
            community_access.len()
        ),
        source_inventory.len(),
        0,
        0,
    );
    let access_obligations = build_access_obligations(&network_places, &community_access);
    let accounting = derive_accounting(
        &source_inventory,
        &network_places,
        &access_obligations,
        &destination_profile,
    );
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
        deployment_id: config
            .deployment_id
            .clone()
            .or_else(|| config.area_id.clone())
            .unwrap_or_else(|| "unknown-area".to_string()),
        attribution,
        source_attributions,
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
        school_context,
        community_access,
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

fn read_snapshot_attributions(snapshot_path: &Path) -> Result<(String, Vec<String>)> {
    let metadata_path = snapshot_path.join("snapshot.json");
    if !metadata_path.is_file() {
        return Ok((String::new(), Vec::new()));
    }
    let metadata: Value = serde_json::from_str(&std::fs::read_to_string(metadata_path)?)?;
    let attribution = metadata
        .get("attribution")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let mut source_attributions = Vec::new();
    if let Some(official) = metadata
        .get("evidence_sources")
        .and_then(|sources| sources.get("official_road_classification"))
        .and_then(|source| source.get("attribution"))
        .and_then(Value::as_str)
    {
        source_attributions.push(official.to_string());
    }
    source_attributions.retain(|source| source != &attribution);
    Ok((attribution, source_attributions))
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

fn admit_school_context(features: &[Feature]) -> Vec<SchoolContext> {
    let mut schools = features
        .iter()
        .filter_map(|feature| {
            if string_property(&feature.properties, "feature_type")?.eq_ignore_ascii_case("school")
            {
                // School context is point evidence only; lines or polygons are not admitted as
                // point features and do not become route/access obligations.
                let Geometry::Point(point) = feature.geometry else {
                    return None;
                };
                let id = string_property(&feature.properties, "evidence_id")
                    .or_else(|| string_property(&feature.properties, "source_id"))
                    .or_else(|| string_property(&feature.properties, "id"))?;
                let source_id =
                    string_property(&feature.properties, "source_id").unwrap_or_else(|| id.clone());
                let name =
                    string_property(&feature.properties, "name").unwrap_or_else(|| id.clone());
                let school_obligation_eligible =
                    string_property(&feature.properties, "school_obligation_eligible")
                        .is_some_and(|value| value.eq_ignore_ascii_case("true"));
                Some(SchoolContext {
                    id,
                    source_id,
                    name,
                    school_obligation_eligible,
                    geometry: point,
                })
            } else {
                None
            }
        })
        .collect::<Vec<_>>();
    schools.sort_by(|left, right| left.id.cmp(&right.id));
    schools
}

fn build_community_access(
    network_places: &[NetworkPlace],
    destinations: &[Place],
    graph: &Graph,
    source_inventory: &[SourceCorridor],
) -> Vec<CommunityAccess> {
    let target_nodes = strategic_spine_target_nodes(graph, source_inventory);
    let mut records = Vec::new();
    for place in network_places
        .iter()
        .filter(|place| is_rural_community(place))
    {
        let Some((attachment_node, attachment_distance_m)) =
            graph.nearest_node_with_distance(place.geometry)
        else {
            records.push(community_access_gap(
                place,
                None,
                None,
                "No graph node is available for the inferred community attachment.",
            ));
            continue;
        };
        let Some((primary_route, joined_spine_id)) =
            graph.route_to_targets(&attachment_node, &target_nodes)
        else {
            records.push(community_access_gap(
                place,
                Some(attachment_node),
                Some(attachment_distance_m),
                "No reachable admitted strategic spine exists after explicit bicycle/access restrictions.",
            ));
            continue;
        };
        let entry_node = primary_route
            .nodes
            .last()
            .cloned()
            .unwrap_or_else(|| attachment_node.clone());
        let status = if primary_route.edge_ids.is_empty() {
            "on-spine"
        } else {
            "served"
        };
        records.push(CommunityAccess {
            community_id: place.id.clone(),
            source_id: place.source_id.clone(),
            name: place.name.clone(),
            geometry: place.geometry,
            status: status.to_string(),
            decision_class: "mechanical".to_string(),
            is_primary: true,
            attachment_node: Some(attachment_node.clone()),
            attachment_distance_m: Some(attachment_distance_m),
            joined_spine_id: Some(joined_spine_id.clone()),
            access_length_m: Some(primary_route.length_m),
            path_edge_ids: primary_route.edge_ids.clone(),
            path_geometry: primary_route.geometry.clone(),
            onward_destinations: Vec::new(),
            onward_benefits: Vec::new(),
            joined_spine_reference: joined_spine_reference(
                &joined_spine_id,
                source_inventory,
            ),
            provision_status: "unknown".to_string(),
            reason: if status == "on-spine" {
                "The inferred community attachment is already on an admitted strategic spine; no access route is generated.".to_string()
            } else {
                "Shortest measured-length cycling path reaches the admitted strategic spine.".to_string()
            },
        });

        if primary_route.edge_ids.is_empty() {
            continue;
        }
        let mut alternatives: BTreeMap<
            (String, Vec<String>),
            (Route, Vec<CommunityAccessBenefit>),
        > = BTreeMap::new();
        for destination in destinations {
            if destination.id == place.id {
                continue;
            }
            let Some(destination_node) = graph.nearest_node(destination.point) else {
                continue;
            };
            let Some(full_route) = graph.cycling_route(&attachment_node, &destination_node) else {
                continue;
            };
            let Some((prefix, alternate_spine_id, alternate_entry_node)) =
                first_spine_prefix(graph, &full_route, &target_nodes)
            else {
                continue;
            };
            if alternate_entry_node == entry_node || prefix.edge_ids.is_empty() {
                continue;
            }
            let onward_from_primary = graph
                .cycling_route(&entry_node, &destination_node)
                .map(|route| route.length_m);
            let primary_access_plus_onward =
                onward_from_primary.map(|length| primary_route.length_m + length);
            if primary_access_plus_onward.is_some_and(|length| full_route.length_m >= length) {
                continue;
            }
            let key = (alternate_spine_id, prefix.edge_ids.clone());
            let entry = alternatives
                .entry(key)
                .or_insert_with(|| (prefix.clone(), Vec::new()));
            if !entry
                .1
                .iter()
                .any(|benefit| benefit.destination_name == destination.name)
            {
                entry.1.push(CommunityAccessBenefit {
                    destination_name: destination.name.clone(),
                    full_route_length_m: full_route.length_m,
                    primary_access_plus_onward_m: primary_access_plus_onward,
                });
                entry
                    .1
                    .sort_by(|left, right| left.destination_name.cmp(&right.destination_name));
            }
        }
        for ((joined_spine_id, _), (route, onward_benefits)) in alternatives {
            let onward_destinations = onward_benefits
                .iter()
                .map(|benefit| benefit.destination_name.clone())
                .collect::<Vec<_>>();
            let destination_label = onward_destinations.join(", ");
            records.push(CommunityAccess {
                community_id: place.id.clone(),
                source_id: place.source_id.clone(),
                name: place.name.clone(),
                geometry: place.geometry,
                status: "served".to_string(),
                decision_class: "mechanical".to_string(),
                is_primary: false,
                attachment_node: Some(attachment_node.clone()),
                attachment_distance_m: Some(attachment_distance_m),
                joined_spine_id: Some(joined_spine_id.clone()),
                access_length_m: Some(route.length_m),
                path_edge_ids: route.edge_ids,
                path_geometry: route.geometry,
                onward_destinations,
                onward_benefits,
                joined_spine_reference: joined_spine_reference(
                    &joined_spine_id,
                    source_inventory,
                ),
                provision_status: "unknown".to_string(),
                reason: format!(
                    "A shorter measured route toward {destination_label} reaches a different strategic spine entry."
                ),
            });
        }
    }
    records.sort_by(|left, right| {
        left.community_id
            .cmp(&right.community_id)
            .then_with(|| right.is_primary.cmp(&left.is_primary))
            .then_with(|| left.path_edge_ids.cmp(&right.path_edge_ids))
    });
    records
}

fn community_access_gap(
    place: &NetworkPlace,
    attachment_node: Option<String>,
    attachment_distance_m: Option<f64>,
    reason: &str,
) -> CommunityAccess {
    CommunityAccess {
        community_id: place.id.clone(),
        source_id: place.source_id.clone(),
        name: place.name.clone(),
        geometry: place.geometry,
        status: "network-gap".to_string(),
        decision_class: "mechanical".to_string(),
        is_primary: true,
        attachment_node,
        attachment_distance_m,
        joined_spine_id: None,
        access_length_m: None,
        path_edge_ids: Vec::new(),
        path_geometry: Vec::new(),
        onward_destinations: Vec::new(),
        onward_benefits: Vec::new(),
        joined_spine_reference: None,
        provision_status: "unknown".to_string(),
        reason: reason.to_string(),
    }
}

fn joined_spine_reference(
    joined_spine_id: &str,
    source_inventory: &[SourceCorridor],
) -> Option<String> {
    let references = joined_spine_id
        .split('+')
        .filter_map(|id| source_inventory.iter().find(|source| source.id == id))
        .map(|source| source.reference.clone())
        .collect::<BTreeSet<_>>();
    (!references.is_empty()).then(|| references.into_iter().collect::<Vec<_>>().join(" + "))
}

fn is_rural_community(place: &NetworkPlace) -> bool {
    matches!(place.place_class.as_str(), "village" | "hamlet")
}

fn strategic_spine_target_nodes(
    graph: &Graph,
    source_inventory: &[SourceCorridor],
) -> HashMap<String, String> {
    let mut edge_spines: HashMap<String, BTreeSet<String>> = HashMap::new();
    for source in source_inventory.iter().filter(|source| {
        matches!(
            source.baseline_role.as_str(),
            "a-road"
                | "current-ncn"
                | "declassified-ncn"
                | "existing-cycleway"
                | "greenway-cycleway"
        )
    }) {
        for edge_id in &source.graph_edge_ids {
            edge_spines
                .entry(edge_id.clone())
                .or_default()
                .insert(source.id.clone());
        }
    }
    let mut node_spines: HashMap<String, BTreeSet<String>> = HashMap::new();
    for edge in &graph.edges {
        let Some(spines) = edge_spines.get(&edge.id) else {
            continue;
        };
        node_spines
            .entry(edge.from.clone())
            .or_default()
            .extend(spines.iter().cloned());
        node_spines
            .entry(edge.to.clone())
            .or_default()
            .extend(spines.iter().cloned());
    }
    node_spines
        .into_iter()
        .map(|(node, spines)| (node, spines.into_iter().collect::<Vec<_>>().join("+")))
        .collect()
}

fn first_spine_prefix(
    graph: &Graph,
    route: &Route,
    target_nodes: &HashMap<String, String>,
) -> Option<(Route, String, String)> {
    route
        .nodes
        .iter()
        .enumerate()
        .find_map(|(node_index, node)| {
            let spine_id = target_nodes.get(node)?;
            Some((
                graph.prefix_route(route, node_index),
                spine_id.clone(),
                node.clone(),
            ))
        })
}

fn build_access_obligations(
    network_places: &[NetworkPlace],
    community_access: &[CommunityAccess],
) -> Vec<AccessObligation> {
    let mut obligations = network_places
        .iter()
        .filter(|place| is_rural_community(place))
        .filter_map(|place| {
            let access = community_access
                .iter()
                .find(|access| access.is_primary && access.community_id == place.id)?;
            let disposition = match access.status.as_str() {
                "served" | "on-spine" => "served",
                "network-gap" => "network-gap",
                _ => "unresolved",
            };
            Some(AccessObligation {
                id: format!("obligation:community:{}", access.community_id),
                kind: "community".to_string(),
                source_id: access.source_id.clone(),
                name: access.name.clone(),
                geometry: Some(access.geometry),
                access_point_status: Some(access.status.clone()),
                access_point_source_id: Some(access.source_id.clone()),
                access_point_rationale: Some(access.reason.clone()),
                disposition: disposition.to_string(),
                reason: access.reason.clone(),
            })
        })
        .collect::<Vec<_>>();
    obligations.sort_by(|left, right| left.id.cmp(&right.id));
    obligations
}

fn derive_accounting(
    source_inventory: &[SourceCorridor],
    network_places: &[NetworkPlace],
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
        network_place_count: network_places.len(),
        obligation_count: obligations.len(),
        unresolved_count,
        network_gap_count,
    }
}

fn admit_boundary_scope(features: &[Feature]) -> Option<BoundaryScope> {
    let mut geometry = Vec::new();
    let mut ids = Vec::new();
    let mut names = Vec::new();
    let mut feature_count = 0;
    for feature in features {
        match &feature.geometry {
            Geometry::Polygon(rings) => geometry.push(rings.clone()),
            Geometry::MultiPolygon(polygons) => geometry.extend(polygons.clone()),
            _ => continue,
        }
        feature_count += 1;
        if let Some(id) = string_property(&feature.properties, "boundary_id")
            .or_else(|| string_property(&feature.properties, "osm_id"))
            .or_else(|| string_property(&feature.properties, "place_id"))
        {
            ids.push(id);
        }
        if let Some(name) = string_property(&feature.properties, "name") {
            names.push(name);
        }
    }
    if geometry.is_empty() {
        return None;
    }
    let combined = feature_count > 1;
    Some(BoundaryScope {
        id: if !combined && ids.len() == 1 {
            ids[0].clone()
        } else if combined && ids.len() == feature_count {
            ids.join("+")
        } else if combined {
            "boundary:combined".to_string()
        } else {
            "boundary".to_string()
        },
        name: if !combined && names.len() == 1 {
            names[0].clone()
        } else if combined && names.len() == feature_count {
            names.join(" + ")
        } else if combined {
            "governed boundary (combined)".to_string()
        } else {
            "governed boundary".to_string()
        },
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
        path_edge_geometries: route.edge_geometries,
        geometry: route.geometry,
    }
}
