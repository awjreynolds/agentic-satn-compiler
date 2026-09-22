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
    pub(crate) edge_lengths_m: Vec<f64>,
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
pub(crate) struct EdgeAttachment {
    pub edge_index: usize,
    pub edge_id: String,
    pub point: [f64; 2],
    pub distance_m: f64,
    pub fraction: f64,
    segment_index: usize,
    segment_fraction: f64,
    pub node: Option<String>,
}

#[derive(Debug, Clone)]
struct AttachmentSeed {
    edge_index: usize,
    end_node: String,
    partial_length_m: f64,
    geometry: Vec<[f64; 2]>,
}

#[derive(Debug, Clone)]
pub(crate) struct Graph {
    pub edges: Vec<GraphEdge>,
    edge_indexes: HashMap<String, usize>,
    outgoing: HashMap<String, Vec<usize>>,
    incoming: HashMap<String, Vec<usize>>,
    node_points: HashMap<String, [f64; 2]>,
    reciprocal_components: Vec<BTreeSet<String>>,
}

#[derive(Debug, Clone)]
pub(crate) struct FrontierTarget {
    pub key: String,
    pub spine_id: String,
    pub community_id: Option<String>,
    pub junction_node: String,
    pub remaining_access_length_m: f64,
}

#[derive(Debug, Clone)]
pub(crate) struct FrontierEdgeTarget {
    pub edge_id: String,
    pub fraction: f64,
    pub point: [f64; 2],
    pub target: FrontierTarget,
}

#[derive(Debug, Clone, Default)]
pub(crate) struct FrontierSearch {
    distances: HashMap<String, f64>,
    previous: HashMap<String, usize>,
    targets: HashMap<String, FrontierTarget>,
    frontier_nodes: BTreeSet<String>,
    partial_targets: HashMap<String, FrontierEdgeTarget>,
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
        let mut incoming: HashMap<String, Vec<usize>> = HashMap::new();
        let mut node_points: HashMap<String, [f64; 2]> = HashMap::new();
        for (index, edge) in edges.iter().enumerate() {
            outgoing.entry(edge.from.clone()).or_default().push(index);
            incoming.entry(edge.to.clone()).or_default().push(index);
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
        for indexes in incoming.values_mut() {
            indexes.sort_by(|left, right| edges[*left].id.cmp(&edges[*right].id));
        }
        let reciprocal_components = reciprocal_components(&edges);
        let edge_indexes = edges
            .iter()
            .enumerate()
            .map(|(index, edge)| (edge.id.clone(), index))
            .collect();
        Ok(Self {
            edges,
            edge_indexes,
            outgoing,
            incoming,
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

    pub(crate) fn route_from_attachment_to_targets(
        &self,
        attachment: &EdgeAttachment,
        target_nodes: &HashMap<String, String>,
        target_edges: &HashMap<String, String>,
    ) -> Option<(Route, String)> {
        if let Some(spine_id) = target_edges.get(&attachment.edge_id) {
            return Some((self.empty_route(), spine_id.clone()));
        }
        if let Some(node) = &attachment.node {
            return self.route_to_targets(node, target_nodes);
        }
        let mut best: Option<(Route, String)> = None;
        for seed in self.attachment_seeds(attachment) {
            let Some((route, spine_id)) = self.route_to_targets(&seed.end_node, target_nodes)
            else {
                continue;
            };
            let combined = self.prepend_attachment_seed(attachment, &seed, &route);
            let replace = best.as_ref().is_none_or(|(current, current_spine)| {
                combined.length_m < current.length_m
                    || (combined.length_m == current.length_m
                        && (spine_id.clone(), &combined.edge_ids)
                            < (current_spine.clone(), &current.edge_ids))
            });
            if replace {
                best = Some((combined, spine_id));
            }
        }
        best
    }

    pub(crate) fn frontier_search(
        &self,
        target_nodes: &HashMap<String, FrontierTarget>,
        partial_targets: &[FrontierEdgeTarget],
    ) -> FrontierSearch {
        self.frontier_search_excluding(target_nodes, partial_targets, None)
    }

    pub(crate) fn frontier_search_excluding(
        &self,
        target_nodes: &HashMap<String, FrontierTarget>,
        partial_targets: &[FrontierEdgeTarget],
        excluded_edge_id: Option<&str>,
    ) -> FrontierSearch {
        let mut search = FrontierSearch {
            targets: target_nodes.clone(),
            frontier_nodes: target_nodes.keys().cloned().collect(),
            ..FrontierSearch::default()
        };
        let mut queue = BinaryHeap::new();
        for node in target_nodes.keys() {
            search.distances.insert(node.clone(), 0.0);
            queue.push(QueueEntry {
                distance: 0.0,
                node: node.clone(),
            });
        }
        for partial_target in partial_targets {
            if excluded_edge_id.is_some_and(|edge_id| edge_id == partial_target.edge_id) {
                continue;
            }
            let Some(edge) = self.edge_by_id(&partial_target.edge_id) else {
                continue;
            };
            let node = edge.from.clone();
            let distance = partial_target.fraction * edge.length_m;
            let replace = match search.distances.get(&node) {
                None => true,
                Some(current_distance) if distance < *current_distance => true,
                Some(current_distance) if distance == *current_distance => search
                    .targets
                    .get(&node)
                    .is_none_or(|current| partial_target.target.key < current.key),
                Some(_) => false,
            };
            if !replace {
                continue;
            }
            search.distances.insert(node.clone(), distance);
            search
                .targets
                .insert(node.clone(), partial_target.target.clone());
            search
                .partial_targets
                .insert(node.clone(), partial_target.clone());
            search.frontier_nodes.insert(node.clone());
            queue.push(QueueEntry { distance, node });
        }

        while let Some(QueueEntry { distance, node }) = queue.pop() {
            if distance > *search.distances.get(&node).unwrap_or(&f64::INFINITY) {
                continue;
            }
            let Some(target) = search.targets.get(&node).cloned() else {
                continue;
            };
            for edge_index in self.incoming.get(&node).into_iter().flatten() {
                let edge = &self.edges[*edge_index];
                if excluded_edge_id.is_some_and(|edge_id| edge_id == edge.id) {
                    continue;
                }
                if !edge.cycling_allowed() {
                    continue;
                }
                let next_distance = distance + edge.length_m;
                let replace = match search.distances.get(&edge.from) {
                    None => true,
                    Some(current_distance) if next_distance < *current_distance => true,
                    Some(current_distance) if next_distance == *current_distance => search
                        .targets
                        .get(&edge.from)
                        .is_none_or(|current| target.key < current.key),
                    Some(_) => false,
                };
                if !replace {
                    continue;
                }
                if search.partial_targets.remove(&edge.from).is_some() {
                    search.frontier_nodes.remove(&edge.from);
                }
                search.distances.insert(edge.from.clone(), next_distance);
                search.previous.insert(edge.from.clone(), *edge_index);
                search.targets.insert(edge.from.clone(), target.clone());
                queue.push(QueueEntry {
                    distance: next_distance,
                    node: edge.from.clone(),
                });
            }
        }
        search
    }

    pub(crate) fn route_from_attachment_to_frontier(
        &self,
        attachment: &EdgeAttachment,
        search: &FrontierSearch,
        target_edges: &HashMap<String, String>,
    ) -> Option<(Route, FrontierTarget)> {
        self.route_from_attachment_to_frontier_excluding(attachment, search, target_edges, None)
    }

    pub(crate) fn route_from_attachment_to_frontier_excluding(
        &self,
        attachment: &EdgeAttachment,
        search: &FrontierSearch,
        target_edges: &HashMap<String, String>,
        excluded_edge_id: Option<&str>,
    ) -> Option<(Route, FrontierTarget)> {
        if excluded_edge_id.is_some_and(|edge_id| edge_id == attachment.edge_id) {
            return None;
        }
        if let Some(spine_id) = target_edges.get(&attachment.edge_id) {
            return Some((
                self.empty_route(),
                FrontierTarget {
                    key: format!("spine-edge:{spine_id}:{}", attachment.edge_id),
                    spine_id: spine_id.clone(),
                    community_id: None,
                    junction_node: format!("attachment:{}", attachment.edge_id),
                    remaining_access_length_m: 0.0,
                },
            ));
        }
        if let Some(node) = &attachment.node {
            return self.route_from_frontier_node(node, search);
        }
        let mut best: Option<(Route, FrontierTarget)> = None;
        for seed in self.attachment_seeds(attachment) {
            let Some((route, target)) = self.route_from_frontier_node(&seed.end_node, search)
            else {
                continue;
            };
            let combined = self.prepend_attachment_seed(attachment, &seed, &route);
            let replace = best.as_ref().is_none_or(|(current, current_target)| {
                combined.length_m < current.length_m
                    || (combined.length_m == current.length_m
                        && (target.key.clone(), &combined.edge_ids)
                            < (current_target.key.clone(), &current.edge_ids))
            });
            if replace {
                best = Some((combined, target));
            }
        }
        best
    }

    pub(crate) fn cycling_route_from_attachment(
        &self,
        attachment: &EdgeAttachment,
        end: &str,
    ) -> Option<Route> {
        if let Some(node) = &attachment.node {
            return self.cycling_route(node, end);
        }
        let mut best: Option<Route> = None;
        for seed in self.attachment_seeds(attachment) {
            let Some(route) = self.route_internal(&seed.end_node, end, "direct", true) else {
                continue;
            };
            let combined = self.prepend_attachment_seed(attachment, &seed, &route);
            if best
                .as_ref()
                .is_none_or(|current| combined.length_m < current.length_m)
            {
                best = Some(combined);
            }
        }
        best
    }

    pub(crate) fn prefix_route(&self, route: &Route, edge_count: usize) -> Route {
        let edge_count = edge_count.min(route.edge_indices.len());
        let indices = route.edge_indices[..edge_count].to_vec();
        let lengths = route.edge_lengths_m[..edge_count].to_vec();
        let geometries = route.edge_geometries[..edge_count].to_vec();
        let nodes = route.nodes[..=edge_count].to_vec();
        let search_cost_m = lengths.iter().sum();
        self.route_from_parts(&indices, lengths, geometries, search_cost_m, nodes)
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

    pub(crate) fn nearest_edge_attachment(&self, point: [f64; 2]) -> Option<EdgeAttachment> {
        self.edges
            .iter()
            .enumerate()
            .filter_map(|(edge_index, edge)| {
                let closest = closest_point_on_polyline(point, &edge.geometry)?;
                let node = if closest.segment_index == 0 && closest.segment_fraction == 0.0 {
                    Some(edge.from.clone())
                } else if closest.segment_index + 2 == edge.geometry.len()
                    && closest.segment_fraction == 1.0
                {
                    Some(edge.to.clone())
                } else {
                    None
                };
                Some(EdgeAttachment {
                    edge_index,
                    edge_id: edge.id.clone(),
                    point: closest.point,
                    distance_m: closest.distance_m,
                    fraction: closest.fraction,
                    segment_index: closest.segment_index,
                    segment_fraction: closest.segment_fraction,
                    node,
                })
            })
            .min_by(|left, right| {
                left.distance_m
                    .partial_cmp(&right.distance_m)
                    .unwrap_or(Ordering::Equal)
                    .then_with(|| left.edge_id.cmp(&right.edge_id))
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

    fn route_from_frontier_node(
        &self,
        start: &str,
        search: &FrontierSearch,
    ) -> Option<(Route, FrontierTarget)> {
        let distance = *search.distances.get(start)?;
        let mut edge_indices = Vec::new();
        let mut nodes = vec![start.to_string()];
        let mut cursor = start.to_string();
        while !search.frontier_nodes.contains(&cursor) {
            let edge_index = *search.previous.get(&cursor)?;
            let edge = &self.edges[edge_index];
            if edge.from != cursor {
                return None;
            }
            edge_indices.push(edge_index);
            cursor = edge.to.clone();
            nodes.push(cursor.clone());
        }
        let target = search.targets.get(&cursor)?.clone();
        let partial = search.partial_targets.get(&cursor);
        let route_distance = partial
            .map(|partial| {
                distance
                    - self
                        .edge_by_id(&partial.edge_id)
                        .map_or(0.0, |edge| partial.fraction * edge.length_m)
            })
            .unwrap_or(distance);
        let route = self.route_from_indices(&edge_indices, route_distance, nodes);
        let route = if let Some(partial) = partial {
            let edge = self.edge_by_id(&partial.edge_id)?;
            let start_point = *edge.geometry.first()?;
            let suffix = self.partial_edge_route(
                &partial.edge_id,
                0.0,
                partial.fraction,
                start_point,
                partial.point,
            )?;
            self.append_routes(&route, &suffix)
        } else {
            route
        };
        Some((route, target))
    }

    fn route_from_indices(
        &self,
        edge_indices: &[usize],
        search_cost_m: f64,
        nodes: Vec<String>,
    ) -> Route {
        let edge_geometries = edge_indices
            .iter()
            .map(|index| self.edges[*index].geometry.clone())
            .collect::<Vec<_>>();
        let lengths = edge_indices
            .iter()
            .map(|index| self.edges[*index].length_m)
            .collect::<Vec<_>>();
        self.route_from_parts(edge_indices, lengths, edge_geometries, search_cost_m, nodes)
    }

    fn route_from_parts(
        &self,
        edge_indices: &[usize],
        edge_lengths_m: Vec<f64>,
        edge_geometries: Vec<Vec<[f64; 2]>>,
        search_cost_m: f64,
        nodes: Vec<String>,
    ) -> Route {
        let edge_ids = edge_indices
            .iter()
            .map(|index| self.edges[*index].id.clone())
            .collect::<Vec<_>>();
        let length_m = edge_lengths_m.iter().sum::<f64>();
        let a_road_length_m = edge_indices
            .iter()
            .zip(edge_lengths_m.iter())
            .filter(|(index, _)| {
                self.edges[**index]
                    .references
                    .iter()
                    .any(|value| has_a_road_reference(value))
            })
            .map(|(_, length)| *length)
            .sum::<f64>();
        let ncn_length_m = edge_indices
            .iter()
            .zip(edge_lengths_m.iter())
            .filter(|(index, _)| self.edges[**index].ncn)
            .map(|(_, length)| *length)
            .sum::<f64>();
        let mut cycle_alignment_bases = BTreeSet::new();
        for index in edge_indices {
            cycle_alignment_bases.extend(self.edges[*index].cycle_alignment_bases.iter().cloned());
        }
        let mut geometry = Vec::new();
        for (position, edge_geometry) in edge_geometries.iter().enumerate() {
            if position == 0 {
                geometry.extend(edge_geometry.iter().copied());
            } else {
                geometry.extend(edge_geometry.iter().skip(1).copied());
            }
        }
        Route {
            edge_ids,
            edge_geometries,
            edge_lengths_m,
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

    pub(crate) fn empty_route(&self) -> Route {
        self.route_from_parts(&[], Vec::new(), Vec::new(), 0.0, Vec::new())
    }

    pub(crate) fn edge_by_id(&self, edge_id: &str) -> Option<&GraphEdge> {
        self.edge_indexes
            .get(edge_id)
            .and_then(|index| self.edges.get(*index))
    }

    pub(crate) fn edge_point_at_fraction(&self, edge_id: &str, fraction: f64) -> Option<[f64; 2]> {
        let edge = self.edge_by_id(edge_id)?;
        let first = *edge.geometry.first()?;
        let last = *edge.geometry.last()?;
        if fraction <= 0.0 {
            return Some(first);
        }
        if fraction >= 1.0 {
            return Some(last);
        }
        let total = edge
            .geometry
            .windows(2)
            .map(|pair| haversine_m(pair[0], pair[1]))
            .sum::<f64>();
        if total <= 0.0 {
            return Some(first);
        }
        let target = fraction * total;
        let mut travelled = 0.0;
        for pair in edge.geometry.windows(2) {
            let segment = haversine_m(pair[0], pair[1]);
            if travelled + segment >= target {
                let local = if segment <= 0.0 {
                    0.0
                } else {
                    (target - travelled) / segment
                };
                return Some([
                    pair[0][0] + (pair[1][0] - pair[0][0]) * local,
                    pair[0][1] + (pair[1][1] - pair[0][1]) * local,
                ]);
            }
            travelled += segment;
        }
        Some(last)
    }

    pub(crate) fn attachment_fraction_on_edge(
        &self,
        attachment: &EdgeAttachment,
        edge_id: &str,
    ) -> Option<f64> {
        let attached_edge = self.edges.get(attachment.edge_index)?;
        self.normalize_edge_fraction(&attached_edge.id, attachment.fraction, edge_id)
    }

    pub(crate) fn normalize_edge_fraction(
        &self,
        source_edge_id: &str,
        fraction: f64,
        target_edge_id: &str,
    ) -> Option<f64> {
        let source = self.edge_by_id(source_edge_id)?;
        let target = self.edge_by_id(target_edge_id)?;
        if source.id == target.id || source.geometry == target.geometry {
            Some(fraction)
        } else if source.geometry.iter().eq(target.geometry.iter().rev()) {
            Some(1.0 - fraction)
        } else {
            None
        }
    }

    pub(crate) fn partial_edge_route(
        &self,
        edge_id: &str,
        start_fraction: f64,
        end_fraction: f64,
        start_point: [f64; 2],
        end_point: [f64; 2],
    ) -> Option<Route> {
        if end_fraction < start_fraction {
            return None;
        }
        let edge_index = *self.edge_indexes.get(edge_id)?;
        let edge = &self.edges[edge_index];
        if !edge.cycling_allowed() {
            return None;
        }
        let length_m = (end_fraction - start_fraction) * edge.length_m;
        let geometry = partial_geometry_between(
            &edge.geometry,
            start_fraction,
            end_fraction,
            start_point,
            end_point,
        );
        Some(self.route_from_parts(
            &[edge_index],
            vec![length_m],
            vec![geometry],
            length_m,
            vec![
                format!("attachment:{edge_id}@{start_fraction:.12}"),
                format!("edge:{edge_id}@{end_fraction:.12}"),
            ],
        ))
    }

    fn append_routes(&self, first: &Route, second: &Route) -> Route {
        let mut edge_indices = first.edge_indices.clone();
        edge_indices.extend(second.edge_indices.iter().copied());
        let mut edge_lengths_m = first.edge_lengths_m.clone();
        edge_lengths_m.extend(second.edge_lengths_m.iter().copied());
        let mut edge_geometries = first.edge_geometries.clone();
        edge_geometries.extend(second.edge_geometries.iter().cloned());
        let mut nodes = first.nodes.clone();
        if nodes.is_empty() {
            nodes.extend(second.nodes.iter().cloned());
        } else {
            nodes.extend(second.nodes.iter().skip(1).cloned());
        }
        self.route_from_parts(
            &edge_indices,
            edge_lengths_m,
            edge_geometries,
            first.search_cost_m + second.search_cost_m,
            nodes,
        )
    }

    fn attachment_seeds(&self, attachment: &EdgeAttachment) -> Vec<AttachmentSeed> {
        let edge = &self.edges[attachment.edge_index];
        let mut seeds = Vec::new();
        if edge.cycling_allowed() {
            seeds.push(AttachmentSeed {
                edge_index: attachment.edge_index,
                end_node: edge.to.clone(),
                partial_length_m: (1.0 - attachment.fraction) * edge.length_m,
                geometry: partial_geometry(
                    &edge.geometry,
                    attachment.segment_index,
                    attachment.segment_fraction,
                    attachment.point,
                ),
            });
        }
        for (edge_index, reverse) in self.edges.iter().enumerate().filter(|(_, candidate)| {
            candidate.from == edge.to
                && candidate.to == edge.from
                && candidate.cycling_allowed()
                && equivalent_geometry(&candidate.geometry, &edge.geometry)
        }) {
            let Some(closest) = closest_point_on_polyline(attachment.point, &reverse.geometry)
            else {
                continue;
            };
            let fraction = self
                .normalize_edge_fraction(&edge.id, attachment.fraction, &reverse.id)
                .unwrap_or(closest.fraction);
            seeds.push(AttachmentSeed {
                edge_index,
                end_node: reverse.to.clone(),
                partial_length_m: (1.0 - fraction) * reverse.length_m,
                geometry: partial_geometry(
                    &reverse.geometry,
                    closest.segment_index,
                    closest.segment_fraction,
                    attachment.point,
                ),
            });
        }
        seeds.sort_by(|left, right| {
            left.partial_length_m
                .partial_cmp(&right.partial_length_m)
                .unwrap_or(Ordering::Equal)
                .then_with(|| left.end_node.cmp(&right.end_node))
                .then_with(|| {
                    self.edges[left.edge_index]
                        .id
                        .cmp(&self.edges[right.edge_index].id)
                })
        });
        seeds
    }

    fn prepend_attachment_seed(
        &self,
        attachment: &EdgeAttachment,
        seed: &AttachmentSeed,
        route: &Route,
    ) -> Route {
        let mut edge_indices = vec![seed.edge_index];
        edge_indices.extend(route.edge_indices.iter().copied());
        let mut edge_lengths_m = vec![seed.partial_length_m];
        edge_lengths_m.extend(route.edge_lengths_m.iter().copied());
        let mut edge_geometries = vec![seed.geometry.clone()];
        edge_geometries.extend(route.edge_geometries.iter().cloned());
        let mut nodes = vec![
            format!("attachment:{}", attachment.edge_id),
            seed.end_node.clone(),
        ];
        nodes.extend(route.nodes.iter().skip(1).cloned());
        self.route_from_parts(
            &edge_indices,
            edge_lengths_m,
            edge_geometries,
            seed.partial_length_m + route.search_cost_m,
            nodes,
        )
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

fn equivalent_geometry(left: &[[f64; 2]], right: &[[f64; 2]]) -> bool {
    left == right || left.iter().eq(right.iter().rev())
}

#[derive(Debug, Clone, Copy)]
struct ClosestPoint {
    point: [f64; 2],
    distance_m: f64,
    fraction: f64,
    segment_index: usize,
    segment_fraction: f64,
}

fn closest_point_on_polyline(point: [f64; 2], geometry: &[[f64; 2]]) -> Option<ClosestPoint> {
    let first = *geometry.first()?;
    if geometry.len() == 1 {
        return Some(ClosestPoint {
            point: first,
            distance_m: haversine_m(point, first),
            fraction: 0.0,
            segment_index: 0,
            segment_fraction: 0.0,
        });
    }
    let mut segment_lengths = Vec::with_capacity(geometry.len() - 1);
    let mut total_length_m = 0.0;
    for segment in geometry.windows(2) {
        let length_m = haversine_m(segment[0], segment[1]);
        segment_lengths.push(length_m);
        total_length_m += length_m;
    }
    let mut travelled_m = 0.0;
    let mut best: Option<ClosestPoint> = None;
    for (segment_index, segment) in geometry.windows(2).enumerate() {
        let (segment_point, segment_fraction) = project_to_segment(point, segment[0], segment[1]);
        let distance_m = haversine_m(point, segment_point);
        let fraction = if total_length_m == 0.0 {
            0.0
        } else {
            (travelled_m + segment_lengths[segment_index] * segment_fraction) / total_length_m
        };
        let candidate = ClosestPoint {
            point: segment_point,
            distance_m,
            fraction,
            segment_index,
            segment_fraction,
        };
        if best.is_none_or(|current| {
            candidate.distance_m < current.distance_m
                || (candidate.distance_m == current.distance_m
                    && (candidate.segment_index, candidate.segment_fraction)
                        < (current.segment_index, current.segment_fraction))
        }) {
            best = Some(candidate);
        }
        travelled_m += segment_lengths[segment_index];
    }
    best
}

fn project_to_segment(point: [f64; 2], start: [f64; 2], end: [f64; 2]) -> ([f64; 2], f64) {
    let latitude = ((point[1] + start[1] + end[1]) / 3.0).to_radians();
    let x_scale = 6_371_008.8 * latitude.cos() * std::f64::consts::PI / 180.0;
    let y_scale = 6_371_008.8 * std::f64::consts::PI / 180.0;
    let start_xy = [start[0] * x_scale, start[1] * y_scale];
    let end_xy = [end[0] * x_scale, end[1] * y_scale];
    let point_xy = [point[0] * x_scale, point[1] * y_scale];
    let delta = [end_xy[0] - start_xy[0], end_xy[1] - start_xy[1]];
    let denominator = delta[0] * delta[0] + delta[1] * delta[1];
    let fraction = if denominator == 0.0 {
        0.0
    } else {
        ((point_xy[0] - start_xy[0]) * delta[0] + (point_xy[1] - start_xy[1]) * delta[1])
            / denominator
    }
    .clamp(0.0, 1.0);
    (
        [
            start[0] + (end[0] - start[0]) * fraction,
            start[1] + (end[1] - start[1]) * fraction,
        ],
        fraction,
    )
}

fn partial_geometry(
    geometry: &[[f64; 2]],
    segment_index: usize,
    segment_fraction: f64,
    point: [f64; 2],
) -> Vec<[f64; 2]> {
    let mut partial = vec![point];
    let end_index = (segment_index + 1).min(geometry.len().saturating_sub(1));
    if geometry.len() > 1 {
        partial.push(geometry[end_index]);
        partial.extend(geometry.iter().skip(end_index + 1).copied());
    }
    if partial.len() > 1 && partial[0] == partial[1] && segment_fraction == 0.0 {
        partial.remove(0);
    }
    partial
}

fn partial_geometry_between(
    geometry: &[[f64; 2]],
    start_fraction: f64,
    end_fraction: f64,
    start_point: [f64; 2],
    end_point: [f64; 2],
) -> Vec<[f64; 2]> {
    let mut partial = vec![start_point];
    if geometry.len() > 1 {
        let total_length_m = geometry
            .windows(2)
            .map(|segment| haversine_m(segment[0], segment[1]))
            .sum::<f64>();
        if total_length_m > 0.0 {
            let mut travelled_m = 0.0;
            for segment in geometry.windows(2) {
                travelled_m += haversine_m(segment[0], segment[1]);
                let fraction = travelled_m / total_length_m;
                if fraction > start_fraction && fraction < end_fraction {
                    partial.push(segment[1]);
                }
            }
        }
    }
    if partial.last().copied() != Some(end_point) {
        partial.push(end_point);
    }
    partial
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
