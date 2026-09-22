use std::cmp::Ordering;
use std::collections::{BTreeSet, BinaryHeap, HashMap};

use crate::error::{Result, SatnError};
use crate::geojson::{
    Feature, Geometry, canonical_tag_values, number_property, property_text, string_property,
};
use crate::geometry::EdgeEvidence;

#[derive(Debug, Clone)]
pub(crate) struct GraphEdge {
    pub id: String,
    pub from: String,
    pub to: String,
    pub length_m: f64,
    pub highway: Option<String>,
    pub reference: Option<String>,
    pub highways: Vec<String>,
    pub references: Vec<String>,
    pub bicycle: Option<String>,
    pub access: Option<String>,
    pub ncn: bool,
    pub cycle_alignment_bases: Vec<String>,
    pub geometry: Vec<[f64; 2]>,
}

impl GraphEdge {
    pub(crate) fn cycling_allowed(&self) -> bool {
        if let Some(bicycle) = &self.bicycle {
            if explicitly_denied(bicycle) {
                return false;
            }
            if explicitly_permitted(bicycle) {
                return true;
            }
        }
        !self.access.as_deref().is_some_and(explicitly_denied)
    }
}

#[derive(Debug, Clone)]
pub(crate) struct Route {
    pub edge_ids: Vec<String>,
    pub edge_geometries: Vec<Vec<[f64; 2]>>,
    pub length_m: f64,
    pub search_cost_m: f64,
    pub a_road_length_m: f64,
    pub ncn_length_m: f64,
    pub cycle_alignment_bases: Vec<String>,
    pub geometry: Vec<[f64; 2]>,
    pub(crate) edge_indices: Vec<usize>,
    pub(crate) nodes: Vec<String>,
}

#[derive(Debug, Clone)]
pub(crate) struct Graph {
    pub edges: Vec<GraphEdge>,
    outgoing: HashMap<String, Vec<usize>>,
    node_points: HashMap<String, [f64; 2]>,
    reciprocal_components: Vec<BTreeSet<String>>,
}

#[derive(Debug, Clone)]
struct QueueEntry {
    distance: f64,
    node: String,
}

impl PartialEq for QueueEntry {
    fn eq(&self, other: &Self) -> bool {
        self.distance == other.distance && self.node == other.node
    }
}

impl Eq for QueueEntry {}

impl Ord for QueueEntry {
    fn cmp(&self, other: &Self) -> Ordering {
        other
            .distance
            .partial_cmp(&self.distance)
            .unwrap_or(Ordering::Equal)
            .then_with(|| self.node.cmp(&other.node))
    }
}

impl PartialOrd for QueueEntry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Graph {
    pub(crate) fn from_features(features: &[Feature], evidence: &[EdgeEvidence]) -> Result<Self> {
        let mut edges = Vec::with_capacity(features.len());
        for (index, feature) in features.iter().enumerate() {
            let Geometry::LineString(geometry) = &feature.geometry else {
                continue;
            };
            let from = property_text(&feature.properties, "u");
            let to = property_text(&feature.properties, "v");
            if from == "unknown" || to == "unknown" {
                return Err(SatnError::InvalidInput(format!(
                    "network feature {index} is missing u/v node identifiers"
                )));
            }
            let length_m = number_property(&feature.properties, "length").ok_or_else(|| {
                SatnError::InvalidInput(format!(
                    "network feature {index} is missing numeric length"
                ))
            })?;
            if !length_m.is_finite() || length_m < 0.0 {
                return Err(SatnError::InvalidInput(format!(
                    "network feature {index} has invalid length"
                )));
            }
            let key = property_text(&feature.properties, "key");
            let osmid = string_property(&feature.properties, "osmid").unwrap_or_default();
            let id = format!("edge:{from}:{to}:{key}:{index}");
            let highways = canonical_tag_values(&feature.properties, "highway");
            let references = canonical_tag_values(&feature.properties, "ref");
            let edge_evidence = evidence.get(index).cloned().unwrap_or_default();
            edges.push(GraphEdge {
                id: if osmid.is_empty() {
                    id
                } else {
                    format!("{id}:osm-{osmid}")
                },
                from,
                to,
                length_m,
                highway: highways.first().cloned(),
                reference: references.first().cloned(),
                highways,
                references,
                bicycle: string_property(&feature.properties, "bicycle"),
                access: string_property(&feature.properties, "access"),
                ncn: edge_evidence.ncn,
                cycle_alignment_bases: edge_evidence.cycle_alignment_bases,
                geometry: geometry.clone(),
            });
        }

        let mut outgoing: HashMap<String, Vec<usize>> = HashMap::new();
        let mut node_points: HashMap<String, [f64; 2]> = HashMap::new();
        for (index, edge) in edges.iter().enumerate() {
            outgoing.entry(edge.from.clone()).or_default().push(index);
            if let Some(point) = edge.geometry.first() {
                node_points.entry(edge.from.clone()).or_insert(*point);
            }
            if let Some(point) = edge.geometry.last() {
                node_points.entry(edge.to.clone()).or_insert(*point);
            }
        }
        for indexes in outgoing.values_mut() {
            indexes.sort_by(|left, right| edges[*left].id.cmp(&edges[*right].id));
        }
        let reciprocal_components = reciprocal_components(&edges);
        Ok(Self {
            edges,
            outgoing,
            node_points,
            reciprocal_components,
        })
    }

    pub(crate) fn route(&self, start: &str, end: &str, role: &str) -> Option<Route> {
        self.route_internal(start, end, role, false)
    }

    pub(crate) fn cycling_route(&self, start: &str, end: &str) -> Option<Route> {
        self.route_internal(start, end, "direct", true)
    }

    fn route_internal(
        &self,
        start: &str,
        end: &str,
        role: &str,
        respect_access: bool,
    ) -> Option<Route> {
        let mut distances: HashMap<String, f64> = HashMap::new();
        let mut previous: HashMap<String, (String, usize)> = HashMap::new();
        let mut queue = BinaryHeap::new();
        distances.insert(start.to_string(), 0.0);
        queue.push(QueueEntry {
            distance: 0.0,
            node: start.to_string(),
        });

        while let Some(QueueEntry { distance, node }) = queue.pop() {
            if node == end {
                break;
            }
            if distance > *distances.get(node.as_str()).unwrap_or(&f64::INFINITY) {
                continue;
            }
            for edge_index in self.outgoing.get(&node).into_iter().flatten() {
                let edge = &self.edges[*edge_index];
                if respect_access && !edge.cycling_allowed() {
                    continue;
                }
                let next_distance = distance + route_weight(edge, role);
                if next_distance < *distances.get(&edge.to).unwrap_or(&f64::INFINITY) {
                    distances.insert(edge.to.clone(), next_distance);
                    previous.insert(edge.to.clone(), (node.clone(), *edge_index));
                    queue.push(QueueEntry {
                        distance: next_distance,
                        node: edge.to.clone(),
                    });
                }
            }
        }

        self.route_from_search(start, end, &distances, &previous)
    }

    /// Return the shortest measured-length route from `start` to the first node
    /// belonging to an admitted strategic spine.  The search stops at the
    /// node, so the returned geometry never draws a synthetic or spine-edge
    /// segment as part of the access connection.
    pub(crate) fn route_to_targets(
        &self,
        start: &str,
        target_nodes: &HashMap<String, String>,
    ) -> Option<(Route, String)> {
        let mut distances: HashMap<String, f64> = HashMap::new();
        let mut previous: HashMap<String, (String, usize)> = HashMap::new();
        let mut queue = BinaryHeap::new();
        distances.insert(start.to_string(), 0.0);
        queue.push(QueueEntry {
            distance: 0.0,
            node: start.to_string(),
        });

        while let Some(QueueEntry { distance, node }) = queue.pop() {
            if distance > *distances.get(&node).unwrap_or(&f64::INFINITY) {
                continue;
            }
            if let Some(spine_id) = target_nodes.get(&node) {
                let route = self.route_from_search(start, &node, &distances, &previous)?;
                return Some((route, spine_id.clone()));
            }
            for edge_index in self.outgoing.get(&node).into_iter().flatten() {
                let edge = &self.edges[*edge_index];
                if !edge.cycling_allowed() {
                    continue;
                }
                let next_distance = distance + edge.length_m;
                if next_distance < *distances.get(&edge.to).unwrap_or(&f64::INFINITY) {
                    distances.insert(edge.to.clone(), next_distance);
                    previous.insert(edge.to.clone(), (node.clone(), *edge_index));
                    queue.push(QueueEntry {
                        distance: next_distance,
                        node: edge.to.clone(),
                    });
                }
            }
        }
        None
    }

    pub(crate) fn prefix_route(&self, route: &Route, edge_count: usize) -> Route {
        let edge_count = edge_count.min(route.edge_indices.len());
        let indices = route.edge_indices[..edge_count].to_vec();
        let nodes = route.nodes[..=edge_count].to_vec();
        let search_cost_m = indices
            .iter()
            .map(|index| self.edges[*index].length_m)
            .sum();
        self.route_from_indices(&indices, search_cost_m, nodes)
    }

    pub(crate) fn nearest_node_with_distance(&self, point: [f64; 2]) -> Option<(String, f64)> {
        self.node_points
            .iter()
            .map(|(id, node_point)| (id.clone(), haversine_m(*node_point, point)))
            .min_by(|(left_id, left_distance), (right_id, right_distance)| {
                left_distance
                    .partial_cmp(right_distance)
                    .unwrap_or(Ordering::Equal)
                    .then_with(|| left_id.cmp(right_id))
            })
    }

    fn route_from_search(
        &self,
        start: &str,
        end: &str,
        distances: &HashMap<String, f64>,
        previous: &HashMap<String, (String, usize)>,
    ) -> Option<Route> {
        if !distances.contains_key(end) {
            return None;
        }
        let mut edge_indices = Vec::new();
        let mut cursor = end.to_string();
        let mut nodes = vec![cursor.clone()];
        while cursor != start {
            let (previous_node, edge_index) = previous.get(cursor.as_str())?.clone();
            edge_indices.push(edge_index);
            cursor = previous_node;
            nodes.push(cursor.clone());
        }
        edge_indices.reverse();
        nodes.reverse();
        Some(self.route_from_indices(&edge_indices, *distances.get(end).unwrap_or(&0.0), nodes))
    }

    fn route_from_indices(
        &self,
        edge_indices: &[usize],
        search_cost_m: f64,
        nodes: Vec<String>,
    ) -> Route {
        let edge_ids = edge_indices
            .iter()
            .map(|index| self.edges[*index].id.clone())
            .collect::<Vec<_>>();
        let edge_geometries = edge_indices
            .iter()
            .map(|index| self.edges[*index].geometry.clone())
            .collect::<Vec<_>>();
        let length_m = edge_indices
            .iter()
            .map(|index| self.edges[*index].length_m)
            .sum::<f64>();
        let a_road_length_m = edge_indices
            .iter()
            .filter(|index| {
                self.edges[**index]
                    .references
                    .iter()
                    .any(|value| has_a_road_reference(value))
            })
            .map(|index| self.edges[*index].length_m)
            .sum::<f64>();
        let ncn_length_m = edge_indices
            .iter()
            .filter(|index| self.edges[**index].ncn)
            .map(|index| self.edges[*index].length_m)
            .sum::<f64>();
        let mut cycle_alignment_bases = BTreeSet::new();
        for index in edge_indices {
            cycle_alignment_bases.extend(self.edges[*index].cycle_alignment_bases.iter().cloned());
        }
        let mut geometry = Vec::new();
        for (position, edge_index) in edge_indices.iter().enumerate() {
            let edge_geometry = &self.edges[*edge_index].geometry;
            if position == 0 {
                geometry.extend(edge_geometry.iter().copied());
            } else {
                geometry.extend(edge_geometry.iter().skip(1).copied());
            }
        }
        Route {
            edge_ids,
            edge_geometries,
            length_m,
            search_cost_m,
            a_road_length_m,
            ncn_length_m,
            cycle_alignment_bases: cycle_alignment_bases.into_iter().collect(),
            geometry,
            edge_indices: edge_indices.to_vec(),
            nodes,
        }
    }

    pub(crate) fn nearest_node(&self, point: [f64; 2]) -> Option<String> {
        self.node_points
            .iter()
            .min_by(|(left_id, left_point), (right_id, right_point)| {
                squared_distance(**left_point, point)
                    .partial_cmp(&squared_distance(**right_point, point))
                    .unwrap_or(Ordering::Equal)
                    .then_with(|| left_id.cmp(right_id))
            })
            .map(|(id, _)| id.clone())
    }

    pub(crate) fn nearest_scoped_node(
        &self,
        point: [f64; 2],
        max_distance_m: f64,
    ) -> Option<String> {
        let dominant = self.reciprocal_components.first();
        if let Some(dominant) = dominant {
            if let Some(node) = self.nearest_from_nodes(point, max_distance_m, dominant) {
                return Some(node);
            }
        }
        self.reciprocal_components
            .iter()
            .flatten()
            .min_by(|left, right| {
                haversine_m(*self.node_points.get(*left).unwrap(), point)
                    .partial_cmp(&haversine_m(*self.node_points.get(*right).unwrap(), point))
                    .unwrap_or(Ordering::Equal)
                    .then_with(|| left.cmp(right))
            })
            .cloned()
            .or_else(|| self.nearest_node(point))
    }

    fn nearest_from_nodes(
        &self,
        point: [f64; 2],
        max_distance_m: f64,
        nodes: &BTreeSet<String>,
    ) -> Option<String> {
        nodes
            .iter()
            .filter_map(|node| {
                let node_point = *self.node_points.get(node)?;
                let distance = haversine_m(node_point, point);
                (distance <= max_distance_m).then_some((node, distance))
            })
            .min_by(|(left_id, left_distance), (right_id, right_distance)| {
                left_distance
                    .partial_cmp(right_distance)
                    .unwrap_or(Ordering::Equal)
                    .then_with(|| left_id.cmp(right_id))
            })
            .map(|(node, _)| node.clone())
    }
}

fn squared_distance(left: [f64; 2], right: [f64; 2]) -> f64 {
    let longitude = left[0] - right[0];
    let latitude = left[1] - right[1];
    longitude * longitude + latitude * latitude
}

fn route_weight(edge: &GraphEdge, role: &str) -> f64 {
    let length = edge.length_m;
    match role {
        "strategic-spine" => {
            length
                * if edge
                    .references
                    .iter()
                    .any(|value| has_a_road_reference(value))
                {
                    0.35
                } else {
                    1.6
                }
        }
        "ncn-informed" => length * if edge.ncn { 0.4 } else { 1.3 },
        "low-traffic" => {
            length
                * if edge.highways.iter().any(|value| is_low_traffic(value)) {
                    0.75
                } else {
                    4.0
                }
        }
        _ => length,
    }
}

fn has_a_road_reference(value: &str) -> bool {
    value.trim().to_ascii_uppercase().starts_with('A')
}

fn explicitly_denied(value: &str) -> bool {
    matches!(value.trim().to_ascii_lowercase().as_str(), "no" | "private")
}

fn explicitly_permitted(value: &str) -> bool {
    matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "yes" | "designated" | "permissive" | "destination"
    )
}

fn is_low_traffic(value: &str) -> bool {
    matches!(
        value,
        "living_street"
            | "residential"
            | "unclassified"
            | "service"
            | "track"
            | "path"
            | "cycleway"
    )
}

fn haversine_m(left: [f64; 2], right: [f64; 2]) -> f64 {
    let latitude_1 = left[1].to_radians();
    let latitude_2 = right[1].to_radians();
    let delta_latitude = latitude_2 - latitude_1;
    let delta_longitude = (right[0] - left[0]).to_radians();
    let a = (delta_latitude / 2.0).sin().powi(2)
        + latitude_1.cos() * latitude_2.cos() * (delta_longitude / 2.0).sin().powi(2);
    6_371_008.8 * 2.0 * a.sqrt().asin()
}

fn reciprocal_components(edges: &[GraphEdge]) -> Vec<BTreeSet<String>> {
    let directed = edges
        .iter()
        .map(|edge| (edge.from.clone(), edge.to.clone()))
        .collect::<BTreeSet<_>>();
    let mut adjacency: HashMap<String, BTreeSet<String>> = HashMap::new();
    for (from, to) in &directed {
        adjacency.entry(from.clone()).or_default();
        adjacency.entry(to.clone()).or_default();
        if directed.contains(&(to.clone(), from.clone())) {
            adjacency
                .entry(from.clone())
                .or_default()
                .insert(to.clone());
            adjacency
                .entry(to.clone())
                .or_default()
                .insert(from.clone());
        }
    }
    let mut remaining = adjacency.keys().cloned().collect::<BTreeSet<_>>();
    let mut components = Vec::new();
    while let Some(start) = remaining.pop_first() {
        let mut component = BTreeSet::from([start.clone()]);
        let mut stack = vec![start];
        while let Some(node) = stack.pop() {
            for neighbor in adjacency.get(&node).into_iter().flatten() {
                if remaining.remove(neighbor) {
                    component.insert(neighbor.clone());
                    stack.push(neighbor.clone());
                }
            }
        }
        components.push(component);
    }
    components.sort_by(|left, right| right.len().cmp(&left.len()).then_with(|| left.cmp(right)));
    components
}
