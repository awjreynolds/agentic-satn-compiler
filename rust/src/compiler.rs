use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap, HashMap};
use std::path::Path;
use std::time::Instant;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::config::AreaConfig;
use crate::error::{Result, SatnError};
use crate::geojson::{
    Feature, Geometry, canonical_tag_values, read_feature_collection, string_property,
};
use crate::geometry::enrich_network_edges;
use crate::graph::{EdgeAttachment, FrontierEdgeTarget, FrontierTarget, Graph, GraphEdge, Route};
use crate::output::write_bundle;
use crate::topography::{
    ElevationEvidenceIndex, RouteTopographyProfile, TopographyAvailability, unknown_route_profile,
};

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

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct CommunityAccessBenefit {
    pub destination_name: String,
    pub full_route_length_m: f64,
    pub primary_access_plus_onward_m: Option<f64>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
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
    #[serde(default)]
    pub attachment_edge_id: Option<String>,
    #[serde(default)]
    pub attachment_point: Option<[f64; 2]>,
    #[serde(default)]
    pub attachment_fraction: Option<f64>,
    pub attachment_distance_m: Option<f64>,
    #[serde(default)]
    pub parent_community_id: Option<String>,
    #[serde(default)]
    pub parent_community_name: Option<String>,
    #[serde(default)]
    pub parent_junction_node: Option<String>,
    #[serde(default)]
    pub parent_junction_edge_id: Option<String>,
    #[serde(default)]
    pub parent_junction_fraction: Option<f64>,
    #[serde(default)]
    pub parent_junction_remaining_m: Option<f64>,
    #[serde(default)]
    pub root_spine_id: Option<String>,
    #[serde(default)]
    pub admission_order: Option<usize>,
    #[serde(default)]
    pub attachment_depth: Option<usize>,
    #[serde(default)]
    pub new_link_length_m: Option<f64>,
    #[serde(default)]
    pub full_access_length_m: Option<f64>,
    pub joined_spine_id: Option<String>,
    pub access_length_m: Option<f64>,
    pub path_edge_ids: Vec<String>,
    #[serde(default)]
    pub path_start_fraction: Option<f64>,
    #[serde(default)]
    pub path_end_fraction: Option<f64>,
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
    #[serde(default)]
    pub new_link_topography: Option<RouteTopographyProfile>,
    #[serde(default)]
    pub full_access_topography: Option<RouteTopographyProfile>,
}

/// An admitted city or town that can be used as an explicit onward journey
/// destination for offline comparison.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct JourneyDestination {
    pub id: String,
    pub name: String,
    pub geometry: [f64; 2],
    pub node: String,
}

/// One measured, directed path in a complete journey comparison.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct JourneyPath {
    pub kind: String,
    pub length_m: f64,
    pub new_link_length_m: Option<f64>,
    pub feeder_length_m: Option<f64>,
    pub shared_suffix_length_m: Option<f64>,
    pub onward_length_m: Option<f64>,
    pub path_edge_ids: Vec<String>,
    pub geometry: Vec<[f64; 2]>,
    pub topography: RouteTopographyProfile,
    pub network_status: String,
}

/// An offline comparison between the accepted network, a retained candidate,
/// and the direct source-graph baseline for one admitted destination.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct JourneyComparison {
    pub community_id: String,
    pub community_name: String,
    pub destination: JourneyDestination,
    pub selected: JourneyPath,
    pub retained_alternative: Option<JourneyPath>,
    #[serde(default)]
    pub alternative_error: Option<String>,
    pub direct: JourneyPath,
}

/// The outcome for one community-to-destination batch pair.
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum JourneyPairStatus {
    Success,
    Unsupported,
    Error,
}

/// The scalar terrain and route facts needed in a batch index.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct JourneyBatchPathSummary {
    pub length_m: f64,
    pub new_link_length_m: Option<f64>,
    pub feeder_length_m: Option<f64>,
    pub shared_suffix_length_m: Option<f64>,
    pub onward_length_m: Option<f64>,
    pub network_status: String,
    pub topography: JourneyBatchTopographySummary,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct JourneyBatchTopographySummary {
    pub availability: TopographyAvailability,
    pub forward_ascent_m: Option<f64>,
    pub forward_descent_m: Option<f64>,
    pub cumulative_elevation_variation_m: Option<f64>,
    pub sustained_gradient_pct: Option<f64>,
}

impl JourneyBatchPathSummary {
    fn from_path(path: &JourneyPath) -> Self {
        Self {
            length_m: path.length_m,
            new_link_length_m: path.new_link_length_m,
            feeder_length_m: path.feeder_length_m,
            shared_suffix_length_m: path.shared_suffix_length_m,
            onward_length_m: path.onward_length_m,
            network_status: path.network_status.clone(),
            topography: JourneyBatchTopographySummary {
                availability: path.topography.availability,
                forward_ascent_m: path.topography.forward_ascent_m,
                forward_descent_m: path.topography.forward_descent_m,
                cumulative_elevation_variation_m: path.topography.cumulative_elevation_variation_m,
                sustained_gradient_pct: path
                    .topography
                    .sustained_gradient
                    .as_ref()
                    .map(|gradient| gradient.gradient_pct),
            },
        }
    }
}

/// One compact record in the batch summary. Full route geometry and edge IDs
/// remain in the per-success comparison artifact.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct JourneyBatchPair {
    pub pair_id: String,
    pub origin_id: String,
    pub origin_name: String,
    pub destination_id: String,
    pub destination_name: String,
    pub status: JourneyPairStatus,
    #[serde(default)]
    pub artifact_stem: Option<String>,
    #[serde(default)]
    pub selected: Option<JourneyBatchPathSummary>,
    #[serde(default)]
    pub retained_alternative: Option<JourneyBatchPathSummary>,
    #[serde(default)]
    pub alternative_error: Option<String>,
    #[serde(default)]
    pub direct: Option<JourneyBatchPathSummary>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct JourneyBatchSummary {
    pub origin_count: usize,
    pub destination_count: usize,
    pub pair_count: usize,
    pub success_count: usize,
    pub unsupported_count: usize,
    pub error_count: usize,
    pub pairs: Vec<JourneyBatchPair>,
}

#[derive(Debug, Clone)]
pub struct JourneyBatchSuccess {
    pub pair_id: String,
    pub comparison: JourneyComparison,
}

#[derive(Debug, Clone, Serialize)]
pub struct JourneyBatchProgress {
    pub completed_pair_count: usize,
    pub total_pair_count: usize,
    pub pair_id: String,
    pub origin_name: String,
    pub destination_name: String,
    pub status: JourneyPairStatus,
}

#[derive(Debug, Clone)]
pub struct JourneyBatchEvaluation {
    pub summary: JourneyBatchSummary,
    pub successes: Vec<JourneyBatchSuccess>,
}

impl JourneyComparison {
    /// Return one inspectable GeoJSON line feature per compared path.
    pub fn to_geojson(&self) -> Value {
        let paths = std::iter::once((&self.selected, "selected"))
            .chain(
                self.retained_alternative
                    .as_ref()
                    .map(|path| (path, "retained-alternative")),
            )
            .chain(std::iter::once((&self.direct, "direct")));
        let features = paths
            .map(|(path, label)| {
                json!({
                    "type": "Feature",
                    "properties": {
                        "kind": label,
                        "path_kind": path.kind,
                        "community_id": self.community_id,
                        "community_name": self.community_name,
                        "destination_id": self.destination.id,
                        "destination_name": self.destination.name,
                        "length_m": path.length_m,
                        "new_link_length_m": path.new_link_length_m,
                        "feeder_length_m": path.feeder_length_m,
                        "shared_suffix_length_m": path.shared_suffix_length_m,
                        "onward_length_m": path.onward_length_m,
                        "network_status": path.network_status,
                        "topography": path.topography,
                    },
                    "geometry": {
                        "type": if path.geometry.len() >= 2 { "LineString" } else { "Point" },
                        "coordinates": if path.geometry.len() >= 2 {
                            json!(path.geometry)
                        } else {
                            json!(path.geometry.first().copied().unwrap_or(self.destination.geometry))
                        },
                    },
                })
            })
            .collect::<Vec<_>>();
        json!({"type": "FeatureCollection", "features": features})
    }
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

impl CompileReport {
    pub fn with_community_access(mut self, records: Vec<CommunityAccess>) -> Self {
        self.access_obligations = build_access_obligations(&self.network_places, &records);
        self.accounting = derive_accounting(
            &self.source_inventory,
            &self.network_places,
            &self.access_obligations,
            &self.destination_profile,
        );
        self.community_access = records;
        self
    }
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

#[derive(Debug, Clone)]
struct PendingCommunity {
    place: NetworkPlace,
    attachment: Option<EdgeAttachment>,
}

#[derive(Debug, Clone)]
struct FrontierCandidate {
    community_id: String,
    attachment: EdgeAttachment,
    route: Route,
    target: FrontierTarget,
}

/// One concrete path offered to the rural decision boundary.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuralAccessCandidate {
    pub id: String,
    pub criterion: String,
    pub access: CommunityAccess,
}

/// The next globally nearest rural community and the paths currently available
/// to it. Only accepting one of these candidates extends the frontier.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuralAccessOffer {
    pub community_id: String,
    pub community_name: String,
    pub candidates: Vec<RuralAccessCandidate>,
}

/// Mutable rural frontier used by both ordinary mechanical compilation and a
/// later typed decision boundary. The graph and elevation index are prepared
/// once and borrowed for the lifetime of this planner.
pub struct RuralAccessPlanner<'a> {
    graph: &'a Graph,
    source_inventory: &'a [SourceCorridor],
    elevation: Option<&'a ElevationEvidenceIndex>,
    elevation_file: String,
    target_nodes: HashMap<String, String>,
    target_edges: HashMap<String, String>,
    pending: BTreeMap<String, PendingCommunity>,
    primary_records: BTreeMap<String, CommunityAccess>,
    records: Vec<CommunityAccess>,
    admission_order: usize,
    cached_offer: Option<RuralAccessOffer>,
    offers_exhausted: bool,
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

pub struct PreparedCompilation {
    pub report: CompileReport,
    graph: Graph,
    source_inventory: Vec<SourceCorridor>,
    network_places: Vec<NetworkPlace>,
    destinations: Vec<JourneyDestination>,
    elevation: Option<ElevationEvidenceIndex>,
    elevation_file: String,
}

pub fn prepare_with_progress(
    config_path: &Path,
    options: CompileOptions,
    progress: &mut dyn FnMut(ProgressEvent),
) -> Result<PreparedCompilation> {
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
    let destinations = places
        .iter()
        .filter_map(|place| {
            let node = left_node(
                place,
                &network.graph,
                config.source.urban_scope_buffer_km * 1000.0,
            );
            (!node.is_empty()).then(|| JourneyDestination {
                id: place.id.clone(),
                name: place.name.clone(),
                geometry: place.point,
                node,
            })
        })
        .collect::<Vec<_>>();
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
    let elevation_path = config.elevation_path(config_path, &snapshot_path);
    let elevation_file = elevation_path
        .as_ref()
        .and_then(|path| path.file_name())
        .and_then(|name| name.to_str())
        .unwrap_or("elevation-evidence.geojson")
        .to_string();
    let elevation = elevation_path
        .as_ref()
        .map(ElevationEvidenceIndex::load)
        .transpose()?;
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
        source_inventory: source_inventory.clone(),
        unknown_facts,
        network_places: network_places.clone(),
        school_context,
        community_access: Vec::new(),
        access_obligations: Vec::new(),
        destination_profile,
        accounting: AccountingSummary::default(),
        connections,
        candidates,
        operations,
    };
    let report = report.with_community_access(Vec::new());
    Ok(PreparedCompilation {
        report,
        graph: network.graph,
        source_inventory,
        network_places,
        destinations,
        elevation,
        elevation_file,
    })
}

impl PreparedCompilation {
    pub fn rural_planner(&self) -> RuralAccessPlanner<'_> {
        RuralAccessPlanner::new(
            &self.network_places,
            &self.graph,
            &self.source_inventory,
            self.elevation.as_ref(),
            &self.elevation_file,
        )
    }

    /// Compare one accepted rural path, an already-retained alternative, and
    /// the direct source-graph route to an admitted city or town.  This is an
    /// offline evaluation seam: it does not change planning records or call a
    /// provider.
    pub fn compare_complete_journey(
        &self,
        accepted: &[CommunityAccess],
        selected: &CommunityAccess,
        retained_alternative: Option<&CommunityAccess>,
        destination_name: &str,
    ) -> Result<JourneyComparison> {
        self.compare_complete_journey_inner(
            accepted,
            selected,
            retained_alternative,
            destination_name,
            false,
        )
    }

    fn compare_complete_journey_inner(
        &self,
        accepted: &[CommunityAccess],
        selected: &CommunityAccess,
        retained_alternative: Option<&CommunityAccess>,
        destination_name: &str,
        keep_alternative_error: bool,
    ) -> Result<JourneyComparison> {
        let destination = self.destination(destination_name)?.clone();
        let records = accepted_records(accepted, selected, retained_alternative);
        let selected_to_spine = self.access_to_root(selected, &records)?;
        let selected_root = root_access(selected, &records)?;
        let selected_onward = self.root_onward_route(selected_root, &destination)?;
        let selected_route = combine_routes(&selected_to_spine, &selected_onward);
        let selected_path = self.journey_path(
            "selected",
            selected,
            &selected_route,
            selected_to_spine.length_m,
            "selected-feeder-plus-source-graph-onward",
        );

        let (retained_path, alternative_error) = match retained_alternative {
            Some(alternative) => match self.complete_path(
                "retained-alternative",
                alternative,
                &records,
                &destination,
            ) {
                Ok(path) => (Some(path), None),
                Err(error) if keep_alternative_error => (None, Some(error.to_string())),
                Err(error) => return Err(error),
            },
            None => (None, None),
        };

        let direct_route = self.direct_route(selected, &destination)?;
        let direct_path = self.journey_path(
            "direct",
            selected,
            &direct_route,
            0.0,
            "source-graph-alternative",
        );

        Ok(JourneyComparison {
            community_id: selected.community_id.clone(),
            community_name: selected.name.clone(),
            destination,
            selected: selected_path,
            retained_alternative: retained_path,
            alternative_error,
            direct: direct_path,
        })
    }

    /// Evaluate every retained primary community against every admitted city
    /// or town using this prepared graph and elevation index.  Each pair is
    /// retained in the summary, including unsupported and invalid outcomes;
    /// successful full comparisons are available for separate artifacts.
    pub fn compare_all_journeys(
        &self,
        accepted: &[CommunityAccess],
        retained_alternatives: &[CommunityAccess],
    ) -> JourneyBatchEvaluation {
        self.compare_all_journeys_with_progress(accepted, retained_alternatives, &mut |_| {})
    }

    pub fn compare_all_journeys_with_progress(
        &self,
        accepted: &[CommunityAccess],
        retained_alternatives: &[CommunityAccess],
        progress: &mut dyn FnMut(JourneyBatchProgress),
    ) -> JourneyBatchEvaluation {
        let origins = accepted
            .iter()
            .filter(|access| access.is_primary)
            .collect::<Vec<_>>();
        let total_pair_count = origins.len() * self.destinations.len();
        let mut completed_pair_count = 0;
        let mut pairs = Vec::with_capacity(total_pair_count);
        let mut successes = Vec::new();
        let mut success_count = 0;
        let mut unsupported_count = 0;
        let mut error_count = 0;

        for origin in &origins {
            let retained_alternative = retained_alternatives
                .iter()
                .find(|access| access.community_id == origin.community_id);
            for destination in &self.destinations {
                let pair_id = format!("{}::{}", origin.community_id, destination.id);
                match self.compare_complete_journey_inner(
                    accepted,
                    origin,
                    retained_alternative,
                    &destination.id,
                    true,
                ) {
                    Ok(comparison) => {
                        success_count += 1;
                        pairs.push(JourneyBatchPair {
                            pair_id: pair_id.clone(),
                            origin_id: origin.community_id.clone(),
                            origin_name: origin.name.clone(),
                            destination_id: destination.id.clone(),
                            destination_name: destination.name.clone(),
                            status: JourneyPairStatus::Success,
                            artifact_stem: None,
                            selected: Some(JourneyBatchPathSummary::from_path(
                                &comparison.selected,
                            )),
                            retained_alternative: comparison
                                .retained_alternative
                                .as_ref()
                                .map(JourneyBatchPathSummary::from_path),
                            alternative_error: comparison.alternative_error.clone(),
                            direct: Some(JourneyBatchPathSummary::from_path(&comparison.direct)),
                            error: None,
                        });
                        successes.push(JourneyBatchSuccess {
                            pair_id: pair_id.clone(),
                            comparison,
                        });
                        completed_pair_count += 1;
                        progress(JourneyBatchProgress {
                            completed_pair_count,
                            total_pair_count,
                            pair_id: pair_id.clone(),
                            origin_name: origin.name.clone(),
                            destination_name: destination.name.clone(),
                            status: JourneyPairStatus::Success,
                        });
                    }
                    Err(error) => {
                        let status = batch_error_status(&error);
                        match status {
                            JourneyPairStatus::Unsupported => unsupported_count += 1,
                            JourneyPairStatus::Error => error_count += 1,
                            JourneyPairStatus::Success => {
                                unreachable!("batch error classification cannot return success")
                            }
                        }
                        pairs.push(JourneyBatchPair {
                            pair_id: pair_id.clone(),
                            origin_id: origin.community_id.clone(),
                            origin_name: origin.name.clone(),
                            destination_id: destination.id.clone(),
                            destination_name: destination.name.clone(),
                            status,
                            artifact_stem: None,
                            selected: None,
                            retained_alternative: None,
                            alternative_error: None,
                            direct: None,
                            error: Some(error.to_string()),
                        });
                        completed_pair_count += 1;
                        progress(JourneyBatchProgress {
                            completed_pair_count,
                            total_pair_count,
                            pair_id: pair_id.clone(),
                            origin_name: origin.name.clone(),
                            destination_name: destination.name.clone(),
                            status,
                        });
                    }
                }
            }
        }

        JourneyBatchEvaluation {
            summary: JourneyBatchSummary {
                origin_count: origins.len(),
                destination_count: self.destinations.len(),
                pair_count: pairs.len(),
                success_count,
                unsupported_count,
                error_count,
                pairs,
            },
            successes,
        }
    }

    fn destination(&self, name_or_id: &str) -> Result<&JourneyDestination> {
        let matches = self
            .destinations
            .iter()
            .filter(|destination| destination.id == name_or_id || destination.name == name_or_id)
            .collect::<Vec<_>>();
        match matches.as_slice() {
            [destination] => Ok(destination),
            [] => Err(SatnError::InvalidInput(format!(
                "admitted journey destination {name_or_id:?} was not found"
            ))),
            _ => Err(SatnError::InvalidInput(format!(
                "journey destination {name_or_id:?} is ambiguous"
            ))),
        }
    }

    fn complete_path(
        &self,
        kind: &str,
        access: &CommunityAccess,
        records: &BTreeMap<String, CommunityAccess>,
        destination: &JourneyDestination,
    ) -> Result<JourneyPath> {
        let to_spine = self.access_to_root(access, records)?;
        let root = root_access(access, records)?;
        let onward = self.root_onward_route(root, destination)?;
        let route = combine_routes(&to_spine, &onward);
        Ok(self.journey_path(
            kind,
            access,
            &route,
            to_spine.length_m,
            "retained-alternative",
        ))
    }

    fn access_to_root(
        &self,
        access: &CommunityAccess,
        records: &BTreeMap<String, CommunityAccess>,
    ) -> Result<Route> {
        let mut route = self.access_path_route(access)?;
        if let Some(parent_id) = &access.parent_community_id {
            let parent = records.get(parent_id).ok_or_else(|| {
                SatnError::InvalidInput(format!(
                    "complete journey is missing accepted parent community {parent_id:?}"
                ))
            })?;
            let suffix = self.rootward_suffix(parent, access, records, &mut BTreeSet::new())?;
            route = combine_routes(&route, &suffix);
        }
        Ok(route)
    }

    fn rootward_suffix(
        &self,
        parent: &CommunityAccess,
        child: &CommunityAccess,
        records: &BTreeMap<String, CommunityAccess>,
        visiting: &mut BTreeSet<String>,
    ) -> Result<Route> {
        if !visiting.insert(parent.community_id.clone()) {
            return Err(SatnError::InvalidInput(format!(
                "complete journey parent chain cycles at {}",
                parent.community_id
            )));
        }
        let mut suffix = self.path_suffix_from_junction(parent, child)?;
        if let Some(grandparent_id) = &parent.parent_community_id {
            let grandparent = records.get(grandparent_id).ok_or_else(|| {
                SatnError::InvalidInput(format!(
                    "complete journey is missing accepted parent community {grandparent_id:?}"
                ))
            })?;
            let ancestor_suffix = self.rootward_suffix(grandparent, parent, records, visiting)?;
            suffix = combine_routes(&suffix, &ancestor_suffix);
        }
        visiting.remove(&parent.community_id);
        Ok(suffix)
    }

    fn path_suffix_from_junction(
        &self,
        parent: &CommunityAccess,
        child: &CommunityAccess,
    ) -> Result<Route> {
        let (start_index, start_fraction) = if let Some(edge_id) =
            child.parent_junction_edge_id.as_deref()
        {
            let fraction = child.parent_junction_fraction.ok_or_else(|| {
                SatnError::InvalidInput(format!("parent junction edge {} has no fraction", edge_id))
            })?;
            parent
                .path_edge_ids
                .iter()
                .enumerate()
                .find_map(|(index, parent_edge_id)| {
                    self.graph
                        .normalize_edge_fraction(edge_id, fraction, parent_edge_id)
                        .map(|fraction| (index, fraction))
                })
                .ok_or_else(|| {
                    SatnError::InvalidInput(format!(
                        "parent junction edge {edge_id} is absent from accepted parent {}",
                        parent.community_id
                    ))
                })?
        } else if let Some(node) = child.parent_junction_node.as_deref() {
            let index = parent
                .path_edge_ids
                .iter()
                .enumerate()
                .find_map(|(index, edge_id)| {
                    let edge = self.graph.edge_by_id(edge_id)?;
                    (edge.from == node).then_some(index)
                })
                .or_else(|| {
                    parent
                        .path_edge_ids
                        .iter()
                        .enumerate()
                        .find_map(|(index, edge_id)| {
                            let edge = self.graph.edge_by_id(edge_id)?;
                            (edge.to == node).then_some(index + 1)
                        })
                })
                .unwrap_or(parent.path_edge_ids.len());
            (index, 0.0)
        } else {
            return Err(SatnError::InvalidInput(format!(
                "accepted child {} has no parent junction",
                child.community_id
            )));
        };
        self.access_path_route_from(parent, start_index, start_fraction)
    }

    fn access_path_route(&self, access: &CommunityAccess) -> Result<Route> {
        let start_fraction = access
            .path_start_fraction
            .or_else(|| {
                access
                    .attachment_edge_id
                    .as_deref()
                    .zip(access.attachment_fraction)
                    .zip(access.path_edge_ids.first())
                    .and_then(|((source_edge, fraction), target_edge)| {
                        self.graph
                            .normalize_edge_fraction(source_edge, fraction, target_edge)
                    })
            })
            .unwrap_or_else(|| {
                if access.attachment_node.is_some() {
                    0.0
                } else {
                    access.attachment_fraction.unwrap_or(0.0)
                }
            });
        self.access_path_route_from(access, 0, start_fraction)
    }

    fn access_path_route_from(
        &self,
        access: &CommunityAccess,
        start_index: usize,
        start_fraction: f64,
    ) -> Result<Route> {
        if start_index >= access.path_edge_ids.len() {
            return Ok(self.graph.empty_route());
        }
        let mut route = self.graph.empty_route();
        for (offset, edge_id) in access.path_edge_ids[start_index..].iter().enumerate() {
            let index = start_index + offset;
            let begin = if index == start_index {
                start_fraction
            } else {
                0.0
            };
            let end = if index + 1 == access.path_edge_ids.len() {
                access.path_end_fraction.unwrap_or(1.0)
            } else {
                1.0
            };
            let start_point = self
                .graph
                .edge_point_at_fraction(edge_id, begin)
                .ok_or_else(|| {
                    SatnError::InvalidInput(format!("accepted path edge {edge_id} has no geometry"))
                })?;
            let end_point = self
                .graph
                .edge_point_at_fraction(edge_id, end)
                .ok_or_else(|| {
                    SatnError::InvalidInput(format!("accepted path edge {edge_id} has no geometry"))
                })?;
            let segment = self
                .graph
                .partial_edge_route(edge_id, begin, end, start_point, end_point)
                .ok_or_else(|| {
                    SatnError::InvalidInput(format!(
                        "accepted path edge {edge_id} cannot be traversed in its recorded direction"
                    ))
                })?;
            route = combine_routes(&route, &segment);
        }
        Ok(route)
    }

    fn root_onward_route(
        &self,
        root: &CommunityAccess,
        destination: &JourneyDestination,
    ) -> Result<Route> {
        if let Some(edge_id) = root.path_edge_ids.last() {
            let fraction = root.path_end_fraction.unwrap_or(1.0);
            let onward = if fraction <= 0.0 {
                self.graph
                    .edge_by_id(edge_id)
                    .and_then(|edge| self.graph.cycling_route(&edge.from, &destination.node))
            } else if fraction >= 1.0 {
                self.graph
                    .edge_by_id(edge_id)
                    .and_then(|edge| self.graph.cycling_route(&edge.to, &destination.node))
            } else {
                let point = self.graph.edge_point_at_fraction(edge_id, fraction);
                point.and_then(|point| {
                    let attachment = self.graph.nearest_edge_attachment(point)?;
                    self.graph
                        .normalize_edge_fraction(edge_id, fraction, &attachment.edge_id)?;
                    self.graph
                        .cycling_route_from_attachment(&attachment, &destination.node)
                })
            };
            onward.ok_or_else(|| {
                SatnError::InvalidInput(format!(
                    "no directed onward route from accepted spine for destination {}",
                    destination.name
                ))
            })
        } else if let Some(node) = &root.attachment_node {
            self.graph
                .cycling_route(node, &destination.node)
                .ok_or_else(|| {
                    SatnError::InvalidInput(format!(
                        "no directed onward route from accepted attachment for destination {}",
                        destination.name
                    ))
                })
        } else if let Some(point) = root.attachment_point {
            let attachment = self.graph.nearest_edge_attachment(point).ok_or_else(|| {
                SatnError::InvalidInput(format!(
                    "accepted root {} has no graph attachment",
                    root.community_id
                ))
            })?;
            self.graph
                .cycling_route_from_attachment(&attachment, &destination.node)
                .ok_or_else(|| {
                    SatnError::InvalidInput(format!(
                        "no directed onward route from accepted attachment for destination {}",
                        destination.name
                    ))
                })
        } else {
            Err(SatnError::InvalidInput(format!(
                "accepted root {} has no route endpoint",
                root.community_id
            )))
        }
    }

    fn direct_route(
        &self,
        access: &CommunityAccess,
        destination: &JourneyDestination,
    ) -> Result<Route> {
        let point = access.attachment_point.unwrap_or(access.geometry);
        let attachment = self.graph.nearest_edge_attachment(point).ok_or_else(|| {
            SatnError::InvalidInput(format!(
                "community {} has no graph attachment for direct comparison",
                access.community_id
            ))
        })?;
        self.graph
            .cycling_route_from_attachment(&attachment, &destination.node)
            .ok_or_else(|| {
                SatnError::InvalidInput(format!(
                    "no directed source-graph route from {} to destination {}",
                    access.community_id, destination.name
                ))
            })
    }

    fn journey_path(
        &self,
        kind: &str,
        access: &CommunityAccess,
        route: &Route,
        feeder_length_m: f64,
        network_status: &str,
    ) -> JourneyPath {
        JourneyPath {
            kind: kind.to_string(),
            length_m: route.length_m,
            new_link_length_m: (kind != "direct").then_some(
                access
                    .new_link_length_m
                    .or(access.access_length_m)
                    .unwrap_or_default(),
            ),
            feeder_length_m: (kind != "direct").then_some(feeder_length_m),
            shared_suffix_length_m: (kind != "direct").then_some(
                feeder_length_m
                    - access
                        .new_link_length_m
                        .or(access.access_length_m)
                        .unwrap_or_default(),
            ),
            onward_length_m: Some(route.length_m - feeder_length_m),
            path_edge_ids: route.edge_ids.clone(),
            geometry: route.geometry.clone(),
            topography: self.profile_for_route(
                &route.geometry,
                route.length_m,
                "No governed elevation profile is available for this complete journey.",
            ),
            network_status: network_status.to_string(),
        }
    }

    fn profile_for_route(
        &self,
        geometry: &[[f64; 2]],
        length_m: f64,
        reason: &str,
    ) -> RouteTopographyProfile {
        if let Some(index) = &self.elevation {
            return index
                .enrich_route(geometry)
                .unwrap_or_else(|_| index.unknown_route_profile(length_m, reason));
        }
        unknown_route_profile(length_m, reason, &self.elevation_file)
    }
}

fn accepted_records(
    accepted: &[CommunityAccess],
    selected: &CommunityAccess,
    retained_alternative: Option<&CommunityAccess>,
) -> BTreeMap<String, CommunityAccess> {
    let mut records = accepted
        .iter()
        .filter(|access| access.is_primary)
        .map(|access| (access.community_id.clone(), access.clone()))
        .collect::<BTreeMap<_, _>>();
    records.insert(selected.community_id.clone(), selected.clone());
    if let Some(alternative) = retained_alternative {
        records
            .entry(alternative.community_id.clone())
            .or_insert_with(|| alternative.clone());
    }
    records
}

fn batch_error_status(error: &SatnError) -> JourneyPairStatus {
    let message = error.to_string();
    if message.starts_with("no directed ")
        || message.contains("no graph attachment")
        || message.contains("no route endpoint")
    {
        JourneyPairStatus::Unsupported
    } else {
        JourneyPairStatus::Error
    }
}

fn root_access<'a>(
    access: &'a CommunityAccess,
    records: &'a BTreeMap<String, CommunityAccess>,
) -> Result<&'a CommunityAccess> {
    let mut current = access;
    let mut visiting = BTreeSet::new();
    loop {
        if !visiting.insert(current.community_id.clone()) {
            return Err(SatnError::InvalidInput(format!(
                "complete journey parent chain cycles at {}",
                current.community_id
            )));
        }
        let Some(parent_id) = current.parent_community_id.as_ref() else {
            return Ok(current);
        };
        current = records.get(parent_id).ok_or_else(|| {
            SatnError::InvalidInput(format!(
                "complete journey is missing accepted parent community {parent_id:?}"
            ))
        })?;
    }
}

fn combine_routes(first: &Route, second: &Route) -> Route {
    let mut edge_ids = first.edge_ids.clone();
    edge_ids.extend(second.edge_ids.iter().cloned());
    let mut edge_geometries = first.edge_geometries.clone();
    edge_geometries.extend(second.edge_geometries.iter().cloned());
    let mut edge_lengths_m = first.edge_lengths_m.clone();
    edge_lengths_m.extend(second.edge_lengths_m.iter().copied());
    let mut edge_indices = first.edge_indices.clone();
    edge_indices.extend(second.edge_indices.iter().copied());
    let mut nodes = first.nodes.clone();
    if nodes.is_empty() {
        nodes.extend(second.nodes.iter().cloned());
    } else {
        nodes.extend(second.nodes.iter().skip(1).cloned());
    }
    let mut geometry = first.geometry.clone();
    append_geometry(&mut geometry, second.geometry.clone());
    let mut cycle_alignment_bases = BTreeSet::new();
    cycle_alignment_bases.extend(first.cycle_alignment_bases.iter().cloned());
    cycle_alignment_bases.extend(second.cycle_alignment_bases.iter().cloned());
    Route {
        edge_ids,
        edge_geometries,
        edge_lengths_m,
        length_m: first.length_m + second.length_m,
        search_cost_m: first.search_cost_m + second.search_cost_m,
        a_road_length_m: first.a_road_length_m + second.a_road_length_m,
        ncn_length_m: first.ncn_length_m + second.ncn_length_m,
        cycle_alignment_bases: cycle_alignment_bases.into_iter().collect(),
        geometry,
        edge_indices,
        nodes,
    }
}

pub fn compile_with_progress(
    config_path: &Path,
    output_dir: &Path,
    options: CompileOptions,
    progress: &mut dyn FnMut(ProgressEvent),
) -> Result<CompileReport> {
    let started = Instant::now();
    let prepared = prepare_with_progress(config_path, options, progress)?;
    let rural_community_count = prepared
        .report
        .network_places
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
        prepared.report.source_inventory_count,
        0,
        0,
    );
    let mut planner = prepared.rural_planner();
    while let Some(offer) = planner.offer_next()? {
        let candidate = offer
            .candidates
            .iter()
            .find(|candidate| candidate.criterion == "shortest-new-link")
            .or_else(|| offer.candidates.first())
            .ok_or_else(|| SatnError::InvalidInput("rural offer contained no candidates".into()))?;
        planner.accept(&candidate.id)?;
    }
    let community_access = planner.into_records();
    let source_inventory_count = prepared.report.source_inventory_count;
    emit(
        progress,
        &started,
        "community-access",
        &format!(
            "retained {} primary and alternate community access records",
            community_access.len()
        ),
        prepared.report.source_inventory_count,
        0,
        0,
    );
    let report = prepared.report.with_community_access(community_access);
    emit(
        progress,
        &started,
        "graph",
        &format!(
            "indexed {} directed source edges and admitted {} city/town places",
            prepared.graph.edges.len(),
            report.network_places.len()
        ),
        source_inventory_count,
        report.connection_count,
        report.candidate_count,
    );
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

impl<'a> RuralAccessPlanner<'a> {
    pub(crate) fn new(
        network_places: &[NetworkPlace],
        graph: &'a Graph,
        source_inventory: &'a [SourceCorridor],
        elevation: Option<&'a ElevationEvidenceIndex>,
        elevation_file: &str,
    ) -> Self {
        let (target_nodes, target_edges) = strategic_spine_targets(graph, source_inventory);
        let pending = network_places
            .iter()
            .filter(|place| is_rural_community(place))
            .map(|place| {
                (
                    place.id.clone(),
                    PendingCommunity {
                        place: place.clone(),
                        attachment: graph.nearest_edge_attachment(place.geometry),
                    },
                )
            })
            .collect();
        Self {
            graph,
            source_inventory,
            elevation,
            elevation_file: elevation_file.to_string(),
            target_nodes,
            target_edges,
            pending,
            primary_records: BTreeMap::new(),
            records: Vec::new(),
            admission_order: 1,
            cached_offer: None,
            offers_exhausted: false,
        }
    }

    pub fn offer_next(&mut self) -> Result<Option<RuralAccessOffer>> {
        if let Some(offer) = &self.cached_offer {
            return Ok(Some(offer.clone()));
        }
        if self.offers_exhausted {
            return Ok(None);
        }
        let offer = self.compute_offer();
        if let Some(offer) = &offer {
            self.cached_offer = Some(offer.clone());
        } else {
            self.offers_exhausted = true;
        }
        Ok(offer)
    }

    fn compute_offer(&self) -> Option<RuralAccessOffer> {
        let (frontier_nodes, partial_targets) =
            frontier_targets(&self.target_nodes, &self.primary_records, self.graph);
        let search = self
            .graph
            .frontier_search(&frontier_nodes, &partial_targets);
        let mut best: Option<FrontierCandidate> = None;
        for (community_id, candidate) in &self.pending {
            let Some(attachment) = candidate.attachment.as_ref() else {
                continue;
            };
            let routed = accepted_interval_route(attachment, &self.primary_records, self.graph)
                .or_else(|| {
                    self.graph.route_from_attachment_to_frontier(
                        attachment,
                        &search,
                        &self.target_edges,
                    )
                });
            let Some((route, target)) = routed else {
                continue;
            };
            let replace = best.as_ref().is_none_or(|current| {
                (route.length_m, community_id, &target.key, &route.edge_ids)
                    < (
                        current.route.length_m,
                        &current.community_id,
                        &current.target.key,
                        &current.route.edge_ids,
                    )
            });
            if replace {
                best = Some(FrontierCandidate {
                    community_id: community_id.clone(),
                    attachment: attachment.clone(),
                    route,
                    target,
                });
            }
        }
        let shortest = best?;
        let pending = self.pending.get(&shortest.community_id)?;
        let shortest_reason = rural_primary_reason(&shortest, &self.primary_records);
        let shortest_candidate = self.plan_candidate(
            &shortest,
            &pending.place,
            "shortest-new-link",
            &shortest_reason,
        );
        let mut candidates = vec![shortest_candidate];

        if self.elevation.is_some() && !shortest.route.edge_ids.is_empty() {
            let mut comfort_candidates = Vec::new();
            for excluded_edge_id in &shortest.route.edge_ids {
                let alternate_search = self.graph.frontier_search_excluding(
                    &frontier_nodes,
                    &partial_targets,
                    Some(excluded_edge_id),
                );
                let alt_result = self.graph.route_from_attachment_to_frontier_excluding(
                    &shortest.attachment,
                    &alternate_search,
                    &self.target_edges,
                    Some(excluded_edge_id),
                );
                let Some((route, target)) = alt_result else {
                    continue;
                };
                if route.edge_ids == shortest.route.edge_ids {
                    continue;
                }
                let alternate = FrontierCandidate {
                    community_id: shortest.community_id.clone(),
                    attachment: shortest.attachment.clone(),
                    route,
                    target,
                };
                let planned = self.plan_candidate(
                    &alternate,
                    &pending.place,
                    "least-climbing-detour",
                    "Best generated edge-exclusion detour by measured full-journey elevation variation; the shortest baseline is reported separately.",
                );
                if planned
                    .access
                    .full_access_topography
                    .as_ref()
                    .is_some_and(|profile| {
                        profile.availability == TopographyAvailability::Available
                            && profile.cumulative_elevation_variation_m.is_some()
                    })
                {
                    comfort_candidates.push(planned);
                }
            }
            comfort_candidates.sort_by(|left, right| {
                let left_profile = left.access.full_access_topography.as_ref();
                let right_profile = right.access.full_access_topography.as_ref();
                let left_variation = left_profile
                    .and_then(|profile| profile.cumulative_elevation_variation_m)
                    .unwrap_or(f64::INFINITY);
                let right_variation = right_profile
                    .and_then(|profile| profile.cumulative_elevation_variation_m)
                    .unwrap_or(f64::INFINITY);
                left_variation
                    .partial_cmp(&right_variation)
                    .unwrap_or(Ordering::Equal)
                    .then_with(|| {
                        left.access
                            .full_access_length_m
                            .unwrap_or(f64::INFINITY)
                            .partial_cmp(
                                &right.access.full_access_length_m.unwrap_or(f64::INFINITY),
                            )
                            .unwrap_or(Ordering::Equal)
                    })
                    .then_with(|| left.access.path_edge_ids.cmp(&right.access.path_edge_ids))
            });
            if let Some(best_comfort) = comfort_candidates.into_iter().next() {
                candidates.push(best_comfort);
            }
        }
        Some(RuralAccessOffer {
            community_id: shortest.community_id,
            community_name: pending.place.name.clone(),
            candidates: candidates
                .into_iter()
                .enumerate()
                .map(|(index, mut candidate)| {
                    candidate.id = format!(
                        "rural:{}:{}",
                        candidate.access.community_id,
                        if index == 0 { "shortest" } else { "comfort" }
                    );
                    candidate
                })
                .collect(),
        })
    }

    pub fn accept(&mut self, candidate_id: &str) -> Result<CommunityAccess> {
        let offer = self
            .offer_next()?
            .ok_or_else(|| SatnError::InvalidInput("no rural access offer is available".into()))?;
        let candidate = offer
            .candidates
            .into_iter()
            .find(|candidate| candidate.id == candidate_id)
            .ok_or_else(|| {
                SatnError::InvalidInput(format!(
                    "rural candidate {candidate_id} is not in the current offer"
                ))
            })?;
        let community_id = candidate.access.community_id.clone();
        if self.pending.remove(&community_id).is_none() {
            return Err(SatnError::InvalidInput(format!(
                "rural community {community_id} is no longer pending"
            )));
        }
        let mut access = candidate.access;
        access.admission_order = Some(self.admission_order);
        self.admission_order += 1;
        self.primary_records
            .insert(community_id.clone(), access.clone());
        self.records.push(access.clone());
        self.cached_offer = None;
        self.offers_exhausted = false;
        Ok(access)
    }

    pub fn reject(&mut self, reason: &str) -> Result<()> {
        let offer = self
            .offer_next()?
            .ok_or_else(|| SatnError::InvalidInput("no rural access offer is available".into()))?;
        let pending = self.pending.remove(&offer.community_id).ok_or_else(|| {
            SatnError::InvalidInput(format!(
                "rural community {} is no longer pending",
                offer.community_id
            ))
        })?;
        self.records.push(community_access_unresolved(
            &pending.place,
            pending.attachment.as_ref(),
            reason,
        ));
        self.cached_offer = None;
        self.offers_exhausted = false;
        Ok(())
    }

    pub fn into_records(mut self) -> Vec<CommunityAccess> {
        for pending_community in self.pending.into_values() {
            let reason = if pending_community.attachment.is_some() {
                "No reachable admitted strategic spine or accepted branch exists after explicit bicycle/access restrictions."
            } else {
                "No graph edge is available for the inferred community attachment."
            };
            self.records.push(community_access_gap(
                &pending_community.place,
                pending_community.attachment.as_ref(),
                reason,
            ));
        }
        self.records.sort_by(|left, right| {
            left.community_id
                .cmp(&right.community_id)
                .then_with(|| right.is_primary.cmp(&left.is_primary))
                .then_with(|| left.path_edge_ids.cmp(&right.path_edge_ids))
        });
        self.records
    }

    fn plan_candidate(
        &self,
        candidate: &FrontierCandidate,
        place: &NetworkPlace,
        criterion: &str,
        reason: &str,
    ) -> RuralAccessCandidate {
        let parent_community_id = candidate.target.community_id.clone();
        let parent = parent_community_id
            .as_ref()
            .and_then(|id| self.primary_records.get(id));
        let root_spine_id = candidate.target.spine_id.clone();
        let attachment_depth = parent
            .and_then(|record| record.attachment_depth)
            .map_or(0, |depth| depth + 1);
        let full_access_length_m =
            Some(candidate.route.length_m + candidate.target.remaining_access_length_m);
        let status = if parent_community_id.is_none() && candidate.route.edge_ids.is_empty() {
            "on-spine"
        } else {
            "served"
        };
        let path_start_fraction = candidate.route.edge_ids.first().map(|edge_id| {
            if candidate.attachment.node.is_some() {
                0.0
            } else {
                self.graph
                    .normalize_edge_fraction(
                        &candidate.attachment.edge_id,
                        candidate.attachment.fraction,
                        edge_id,
                    )
                    .unwrap_or(candidate.attachment.fraction)
            }
        });
        let path_end_fraction = candidate.route.edge_ids.last().map(|edge_id| {
            frontier_edge_fraction(&candidate.target.junction_node, edge_id).unwrap_or(1.0)
        });
        let new_link_topography = self.profile_for_route(
            &candidate.route.geometry,
            candidate.route.length_m,
            "No governed elevation profile is available for this new link.",
        );
        let full_access_length_m_value = full_access_length_m.unwrap_or(candidate.route.length_m);
        let full_access_topography = self.profile_for_route(
            &self.full_access_geometry(candidate),
            full_access_length_m_value,
            "No governed elevation profile is available for the complete community-to-spine journey.",
        );
        let access = CommunityAccess {
            community_id: place.id.clone(),
            source_id: place.source_id.clone(),
            name: place.name.clone(),
            geometry: place.geometry,
            status: status.to_string(),
            decision_class: "mechanical".to_string(),
            is_primary: true,
            attachment_node: candidate.attachment.node.clone(),
            attachment_edge_id: Some(candidate.attachment.edge_id.clone()),
            attachment_point: Some(candidate.attachment.point),
            attachment_fraction: Some(candidate.attachment.fraction),
            attachment_distance_m: Some(candidate.attachment.distance_m),
            parent_community_id,
            parent_community_name: candidate
                .target
                .community_id
                .as_ref()
                .and_then(|id| self.primary_records.get(id))
                .map(|record| record.name.clone()),
            parent_junction_node: candidate.target.community_id.as_ref().and_then(|_| {
                (!candidate.target.junction_node.starts_with("edge:"))
                    .then(|| candidate.target.junction_node.clone())
            }),
            parent_junction_edge_id: candidate.target.community_id.as_ref().and_then(|_| {
                candidate
                    .target
                    .junction_node
                    .strip_prefix("edge:")
                    .and_then(|value| value.split('@').next())
                    .map(str::to_string)
            }),
            parent_junction_fraction: candidate.target.community_id.as_ref().and_then(|_| {
                candidate
                    .target
                    .junction_node
                    .split('@')
                    .nth(1)
                    .and_then(|value| value.parse().ok())
            }),
            parent_junction_remaining_m: candidate
                .target
                .community_id
                .as_ref()
                .map(|_| candidate.target.remaining_access_length_m),
            root_spine_id: Some(root_spine_id.clone()),
            admission_order: Some(self.admission_order),
            attachment_depth: Some(attachment_depth),
            new_link_length_m: Some(candidate.route.length_m),
            full_access_length_m,
            joined_spine_id: Some(root_spine_id.clone()),
            access_length_m: Some(candidate.route.length_m),
            path_edge_ids: candidate.route.edge_ids.clone(),
            path_start_fraction,
            path_end_fraction,
            path_geometry: candidate.route.geometry.clone(),
            onward_destinations: Vec::new(),
            onward_benefits: Vec::new(),
            joined_spine_reference: joined_spine_reference(&root_spine_id, self.source_inventory),
            provision_status: "unknown".to_string(),
            reason: reason.to_string(),
            new_link_topography: Some(new_link_topography),
            full_access_topography: Some(full_access_topography),
        };
        RuralAccessCandidate {
            id: String::new(),
            criterion: criterion.to_string(),
            access,
        }
    }

    fn profile_for_route(
        &self,
        route: &[[f64; 2]],
        route_length_m: f64,
        missing_reason: &str,
    ) -> RouteTopographyProfile {
        if let Some(index) = self.elevation {
            return index
                .enrich_route(route)
                .unwrap_or_else(|_| index.unknown_route_profile(route_length_m, missing_reason));
        }
        unknown_route_profile(route_length_m, missing_reason, &self.elevation_file)
    }

    fn full_access_geometry(&self, candidate: &FrontierCandidate) -> Vec<[f64; 2]> {
        let mut geometry = candidate.route.geometry.clone();
        let Some(parent_id) = candidate.target.community_id.as_ref() else {
            return geometry;
        };
        let Some(parent) = self.primary_records.get(parent_id) else {
            return geometry;
        };
        if let Some(suffix) =
            suffix_after_target(parent, &candidate.target, &self.primary_records, self.graph)
        {
            append_geometry(&mut geometry, suffix);
        }
        geometry
    }
}

fn suffix_after_target(
    access: &CommunityAccess,
    target: &FrontierTarget,
    records: &BTreeMap<String, CommunityAccess>,
    graph: &Graph,
) -> Option<Vec<[f64; 2]>> {
    let mut suffix = Vec::new();
    if let Some(value) = target.junction_node.strip_prefix("edge:") {
        let (edge_id, fraction_text) = value.rsplit_once('@')?;
        let fraction = fraction_text.parse::<f64>().ok()?;
        let index = access
            .path_edge_ids
            .iter()
            .position(|candidate| candidate == edge_id)?;
        for (offset, candidate_edge_id) in access.path_edge_ids[index..].iter().enumerate() {
            let edge_index = index + offset;
            let start_fraction = if offset == 0 { fraction } else { 0.0 };
            let end_fraction = if edge_index + 1 == access.path_edge_ids.len() {
                access.path_end_fraction.unwrap_or(1.0)
            } else {
                1.0
            };
            if end_fraction < start_fraction {
                return None;
            }
            let start_point = graph.edge_point_at_fraction(candidate_edge_id, start_fraction)?;
            let end_point = graph.edge_point_at_fraction(candidate_edge_id, end_fraction)?;
            let route = graph.partial_edge_route(
                candidate_edge_id,
                start_fraction,
                end_fraction,
                start_point,
                end_point,
            )?;
            append_geometry(&mut suffix, route.geometry);
        }
    } else {
        let start_index = access
            .path_edge_ids
            .iter()
            .enumerate()
            .find_map(|(index, edge_id)| {
                let edge = graph.edge_by_id(edge_id)?;
                if edge.from == target.junction_node {
                    Some(index)
                } else if edge.to == target.junction_node {
                    Some(index + 1)
                } else {
                    None
                }
            })
            .unwrap_or(access.path_edge_ids.len());
        for (offset, candidate_edge_id) in access.path_edge_ids[start_index..].iter().enumerate() {
            let edge_index = start_index + offset;
            let start_fraction = 0.0;
            let end_fraction = if edge_index + 1 == access.path_edge_ids.len() {
                access.path_end_fraction.unwrap_or(1.0)
            } else {
                1.0
            };
            let start_point = graph.edge_point_at_fraction(candidate_edge_id, start_fraction)?;
            let end_point = graph.edge_point_at_fraction(candidate_edge_id, end_fraction)?;
            let route = graph.partial_edge_route(
                candidate_edge_id,
                start_fraction,
                end_fraction,
                start_point,
                end_point,
            )?;
            append_geometry(&mut suffix, route.geometry);
        }
    }

    if let Some(parent_id) = access.parent_community_id.as_ref() {
        let parent = records.get(parent_id)?;
        let parent_target = FrontierTarget {
            key: format!("community:{}:suffix", parent_id),
            spine_id: access
                .root_spine_id
                .clone()
                .or_else(|| parent.root_spine_id.clone())?,
            community_id: Some(parent_id.clone()),
            junction_node: access.parent_junction_node.clone().or_else(|| {
                access
                    .parent_junction_edge_id
                    .as_ref()
                    .zip(access.parent_junction_fraction)
                    .map(|(edge_id, fraction)| format!("edge:{edge_id}@{fraction:.12}"))
            })?,
            remaining_access_length_m: access.parent_junction_remaining_m.unwrap_or_default(),
        };
        let parent_suffix = suffix_after_target(parent, &parent_target, records, graph)?;
        append_geometry(&mut suffix, parent_suffix);
    }
    Some(suffix)
}

fn append_geometry(target: &mut Vec<[f64; 2]>, source: Vec<[f64; 2]>) {
    for point in source {
        if target.last().copied() != Some(point) {
            target.push(point);
        }
    }
}

fn frontier_targets(
    spine_nodes: &HashMap<String, String>,
    primary_records: &BTreeMap<String, CommunityAccess>,
    graph: &Graph,
) -> (HashMap<String, FrontierTarget>, Vec<FrontierEdgeTarget>) {
    let mut targets = HashMap::new();
    let mut partial_targets = Vec::new();
    for (node, spine_id) in spine_nodes {
        let target = FrontierTarget {
            key: format!("spine:{spine_id}:{node}"),
            spine_id: spine_id.clone(),
            community_id: None,
            junction_node: node.clone(),
            remaining_access_length_m: 0.0,
        };
        insert_frontier_target(&mut targets, node.clone(), target);
    }
    for access in primary_records.values() {
        let Some(root_spine_id) = access
            .root_spine_id
            .as_ref()
            .or(access.joined_spine_id.as_ref())
        else {
            continue;
        };
        if let (Some(edge_id), Some(fraction), Some(point), Some(remaining)) = (
            access.path_edge_ids.first(),
            access.path_start_fraction,
            access.attachment_point,
            access.full_access_length_m.or(access.access_length_m),
        ) {
            if fraction > 0.0 {
                partial_targets.push(FrontierEdgeTarget {
                    edge_id: edge_id.clone(),
                    fraction,
                    point,
                    target: FrontierTarget {
                        key: format!(
                            "community:{}:edge:{}@{fraction:.12}",
                            access.community_id, edge_id
                        ),
                        spine_id: root_spine_id.clone(),
                        community_id: Some(access.community_id.clone()),
                        junction_node: format!("edge:{edge_id}@{fraction:.12}"),
                        remaining_access_length_m: remaining,
                    },
                });
            }
        }
        for (node, remaining_access_length_m) in access_frontier_targets(access, graph) {
            insert_frontier_target(
                &mut targets,
                node.clone(),
                FrontierTarget {
                    key: format!("community:{}:{node}", access.community_id),
                    spine_id: root_spine_id.clone(),
                    community_id: Some(access.community_id.clone()),
                    junction_node: node.clone(),
                    remaining_access_length_m,
                },
            );
        }
    }
    (targets, partial_targets)
}

fn insert_frontier_target(
    targets: &mut HashMap<String, FrontierTarget>,
    node: String,
    target: FrontierTarget,
) {
    if targets
        .get(&node)
        .is_none_or(|current| target.key < current.key)
    {
        targets.insert(node, target);
    }
}

fn frontier_edge_fraction(junction_node: &str, edge_id: &str) -> Option<f64> {
    let value = junction_node.strip_prefix("edge:")?;
    let (junction_edge_id, fraction) = value.rsplit_once('@')?;
    (junction_edge_id == edge_id).then(|| fraction.parse().ok())?
}

fn accepted_interval_route(
    attachment: &EdgeAttachment,
    primary_records: &BTreeMap<String, CommunityAccess>,
    graph: &Graph,
) -> Option<(Route, FrontierTarget)> {
    let mut best: Option<(Route, FrontierTarget)> = None;
    for access in primary_records.values() {
        let Some(root_spine_id) = access
            .root_spine_id
            .as_ref()
            .or(access.joined_spine_id.as_ref())
        else {
            continue;
        };
        let ancestor_length = access
            .full_access_length_m
            .or(access.access_length_m)
            .unwrap_or_default()
            - access
                .new_link_length_m
                .or(access.access_length_m)
                .unwrap_or_default();
        let edge_lengths = access
            .path_edge_ids
            .iter()
            .map(|edge_id| graph.edge_by_id(edge_id).map_or(0.0, |edge| edge.length_m))
            .collect::<Vec<_>>();
        let path_start_fraction = access
            .path_start_fraction
            .or_else(|| {
                access
                    .attachment_edge_id
                    .as_deref()
                    .zip(access.attachment_fraction)
                    .zip(access.path_edge_ids.first())
                    .and_then(|((attachment_edge_id, fraction), edge_id)| {
                        graph.normalize_edge_fraction(attachment_edge_id, fraction, edge_id)
                    })
            })
            .unwrap_or_else(|| {
                if access.attachment_node.is_some() {
                    0.0
                } else {
                    access.attachment_fraction.unwrap_or_default()
                }
            });
        let path_end_fraction = access.path_end_fraction.unwrap_or(1.0);
        let effective_edge_lengths = edge_lengths
            .iter()
            .enumerate()
            .map(|(index, length)| {
                let start = (index == 0).then_some(path_start_fraction).unwrap_or(0.0);
                let end = (index + 1 == edge_lengths.len())
                    .then_some(path_end_fraction)
                    .unwrap_or(1.0);
                (end - start) * length
            })
            .collect::<Vec<_>>();
        for (index, edge_id) in access.path_edge_ids.iter().enumerate() {
            let Some(attachment_fraction) = graph.attachment_fraction_on_edge(attachment, edge_id)
            else {
                continue;
            };
            let start_fraction = if index == 0 && access.attachment_node.is_none() {
                path_start_fraction
            } else {
                0.0
            };
            let end_fraction = if index + 1 == access.path_edge_ids.len() {
                access.path_end_fraction.unwrap_or(1.0)
            } else {
                1.0
            };
            let Some(edge) = graph.edge_by_id(edge_id) else {
                continue;
            };
            if attachment_fraction > end_fraction {
                continue;
            }
            let (route, junction_fraction, remaining) = if attachment_fraction < start_fraction {
                let parent_point = access.attachment_point?;
                let route = graph.partial_edge_route(
                    edge_id,
                    attachment_fraction,
                    start_fraction,
                    attachment.point,
                    parent_point,
                )?;
                (
                    route,
                    start_fraction,
                    access
                        .full_access_length_m
                        .or(access.access_length_m)
                        .unwrap_or_default(),
                )
            } else {
                (
                    graph.empty_route(),
                    attachment_fraction,
                    (end_fraction - attachment_fraction) * edge.length_m
                        + effective_edge_lengths[index + 1..].iter().sum::<f64>()
                        + ancestor_length,
                )
            };
            let target = FrontierTarget {
                key: format!(
                    "community:{}:edge:{}@{:.12}",
                    access.community_id, edge.id, junction_fraction
                ),
                spine_id: root_spine_id.clone(),
                community_id: Some(access.community_id.clone()),
                junction_node: format!("edge:{}@{:.12}", edge.id, junction_fraction),
                remaining_access_length_m: remaining,
            };
            let candidate = (route, target);
            if best
                .as_ref()
                .is_none_or(|(_, current)| candidate.1.key < current.key)
            {
                best = Some(candidate);
            }
        }
    }
    best
}

fn access_frontier_targets(access: &CommunityAccess, graph: &Graph) -> BTreeMap<String, f64> {
    let mut nodes = BTreeMap::new();
    let ancestor_length = access
        .full_access_length_m
        .or(access.access_length_m)
        .unwrap_or_default()
        - access
            .new_link_length_m
            .or(access.access_length_m)
            .unwrap_or_default();
    let link_length = access
        .new_link_length_m
        .or(access.access_length_m)
        .unwrap_or_default();
    if let Some(node) = &access.attachment_node {
        nodes.insert(node.clone(), link_length + ancestor_length);
    }
    let edge_lengths = access
        .path_edge_ids
        .iter()
        .map(|edge_id| graph.edge_by_id(edge_id).map_or(0.0, |edge| edge.length_m))
        .collect::<Vec<_>>();
    let path_start_fraction = access
        .path_start_fraction
        .or_else(|| {
            access
                .attachment_edge_id
                .as_deref()
                .zip(access.attachment_fraction)
                .zip(access.path_edge_ids.first())
                .and_then(|((attachment_edge_id, fraction), edge_id)| {
                    graph.normalize_edge_fraction(attachment_edge_id, fraction, edge_id)
                })
        })
        .unwrap_or_else(|| {
            if access.attachment_node.is_some() {
                0.0
            } else {
                access.attachment_fraction.unwrap_or_default()
            }
        });
    let path_end_fraction = access.path_end_fraction.unwrap_or(1.0);
    let effective_edge_lengths = edge_lengths
        .iter()
        .enumerate()
        .map(|(index, length)| {
            let start = (index == 0).then_some(path_start_fraction).unwrap_or(0.0);
            let end = (index + 1 == edge_lengths.len())
                .then_some(path_end_fraction)
                .unwrap_or(1.0);
            (end - start) * length
        })
        .collect::<Vec<_>>();
    for (index, edge_id) in access.path_edge_ids.iter().enumerate() {
        let Some(edge) = graph.edge_by_id(edge_id) else {
            continue;
        };
        let start_fraction = if index == 0 { path_start_fraction } else { 0.0 };
        let end_fraction = if index + 1 == access.path_edge_ids.len() {
            path_end_fraction
        } else {
            1.0
        };
        if start_fraction == 0.0 {
            let remaining = effective_edge_lengths[index]
                + effective_edge_lengths[index + 1..].iter().sum::<f64>()
                + ancestor_length;
            nodes.entry(edge.from.clone()).or_insert(remaining);
        }
        if end_fraction == 1.0 {
            let remaining =
                effective_edge_lengths[index + 1..].iter().sum::<f64>() + ancestor_length;
            nodes.entry(edge.to.clone()).or_insert(remaining);
        }
    }
    nodes
}

fn community_access_gap(
    place: &NetworkPlace,
    attachment: Option<&EdgeAttachment>,
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
        attachment_node: attachment.and_then(|value| value.node.clone()),
        attachment_edge_id: attachment.map(|value| value.edge_id.clone()),
        attachment_point: attachment.map(|value| value.point),
        attachment_fraction: attachment.map(|value| value.fraction),
        attachment_distance_m: attachment.map(|value| value.distance_m),
        parent_community_id: None,
        parent_community_name: None,
        parent_junction_node: None,
        parent_junction_edge_id: None,
        parent_junction_fraction: None,
        parent_junction_remaining_m: None,
        root_spine_id: None,
        admission_order: None,
        attachment_depth: None,
        new_link_length_m: None,
        full_access_length_m: None,
        joined_spine_id: None,
        access_length_m: None,
        path_edge_ids: Vec::new(),
        path_start_fraction: None,
        path_end_fraction: None,
        path_geometry: Vec::new(),
        onward_destinations: Vec::new(),
        onward_benefits: Vec::new(),
        joined_spine_reference: None,
        provision_status: "unknown".to_string(),
        reason: reason.to_string(),
        new_link_topography: None,
        full_access_topography: None,
    }
}

fn community_access_unresolved(
    place: &NetworkPlace,
    attachment: Option<&EdgeAttachment>,
    reason: &str,
) -> CommunityAccess {
    let mut access = community_access_gap(place, attachment, reason);
    access.status = "unresolved".to_string();
    access
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

fn rural_primary_reason(
    candidate: &FrontierCandidate,
    primary_records: &BTreeMap<String, CommunityAccess>,
) -> String {
    if let Some(parent_id) = candidate.target.community_id.as_ref() {
        let parent_name = primary_records
            .get(parent_id)
            .map(|record| record.name.as_str())
            .unwrap_or(parent_id.as_str());
        return format!(
            "Shortest measured new link reaches the accepted {parent_name} branch junction; its root spine remains reachable through the accepted branch."
        );
    }
    if candidate.route.edge_ids.is_empty() {
        "The inferred community attachment is already on an admitted strategic spine; no access route is generated.".to_string()
    } else {
        "Shortest measured new link reaches the admitted strategic spine frontier.".to_string()
    }
}

fn strategic_spine_targets(
    graph: &Graph,
    source_inventory: &[SourceCorridor],
) -> (HashMap<String, String>, HashMap<String, String>) {
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
    let edge_spines = edge_spines
        .into_iter()
        .map(|(edge_id, spines)| (edge_id, spines.into_iter().collect::<Vec<_>>().join("+")))
        .collect::<HashMap<_, _>>();
    let mut node_spines: HashMap<String, BTreeSet<String>> = HashMap::new();
    for edge in &graph.edges {
        let Some(spines) = edge_spines.get(&edge.id) else {
            continue;
        };
        for spine in spines.split('+') {
            node_spines
                .entry(edge.from.clone())
                .or_default()
                .insert(spine.to_string());
            node_spines
                .entry(edge.to.clone())
                .or_default()
                .insert(spine.to_string());
        }
    }
    let node_spines = node_spines
        .into_iter()
        .map(|(node, spines)| (node, spines.into_iter().collect::<Vec<_>>().join("+")))
        .collect();
    (node_spines, edge_spines)
}

fn build_access_obligations(
    network_places: &[NetworkPlace],
    community_access: &[CommunityAccess],
) -> Vec<AccessObligation> {
    let mut obligations = network_places
        .iter()
        .filter(|place| is_rural_community(place))
        .map(|place| {
            if let Some(access) = community_access
                .iter()
                .find(|access| access.is_primary && access.community_id == place.id)
            {
                let disposition = match access.status.as_str() {
                    "served" | "on-spine" => "served",
                    "network-gap" => "network-gap",
                    _ => "unresolved",
                };
                AccessObligation {
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
                }
            } else {
                let reason =
                    "Rural access decision remains pending; no path has been accepted in this prepared report."
                        .to_string();
                AccessObligation {
                    id: format!("obligation:community:{}", place.id),
                    kind: "community".to_string(),
                    source_id: place.source_id.clone(),
                    name: place.name.clone(),
                    geometry: Some(place.geometry),
                    access_point_status: Some("unresolved".to_string()),
                    access_point_source_id: Some(place.source_id.clone()),
                    access_point_rationale: Some(reason.clone()),
                    disposition: "unresolved".to_string(),
                    reason,
                }
            }
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
