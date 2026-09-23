//! Native route-level elevation enrichment.
//!
//! The binding and normalisation rules in this module are intentionally the
//! same governed rules used by `src/satn/topography.py`: a 5 m evidence
//! tolerance, a 250 m maximum interior gap, and a 10 m sustained window.
//! Those values are policy from the reference implementation and CONTEXT.md;
//! they are not routing thresholds.

use std::collections::BTreeMap;
use std::path::Path;

use rstar::{AABB, RTree, RTreeObject};
use serde::{Deserialize, Serialize};

use crate::error::Result;
use crate::geojson::{Geometry, number_property, read_feature_collection, string_property};
use crate::geometry::Projector;
use crate::travel_time::{
    ElevationSample, HillNeutralMovingTime, TravelTimeEstimate, brouter_trekking_v1_7_10,
    estimate_hill_neutral_moving_time, estimate_moving_time,
};

const EVIDENCE_TOLERANCE_M: f64 = 5.0;
const MAXIMUM_SAMPLE_SPACING_M: f64 = 250.0;
const MINIMUM_SUSTAINED_SPACING_M: f64 = 10.0;

/// Whether a route has a complete elevation profile or an explicit evidence gap.
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum TopographyAvailability {
    Available,
    Unknown,
}

/// Coverage of the ordered route geometry by usable elevation samples.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RouteCoverage {
    pub route_length_m: f64,
    pub start_m: Option<f64>,
    pub end_m: Option<f64>,
    pub maximum_gap_m: Option<f64>,
    pub sample_count: usize,
}

/// Compact record of a bounded moving-time endpoint extension.
///
/// The extension uses the nearest observed interval's local rise/run slope;
/// it does not alter the governed coverage or elevation aggregates above.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct MovingTimeBoundaryExtrapolation {
    pub start_extension_m: Option<f64>,
    pub start_local_slope: Option<f64>,
    pub end_extension_m: Option<f64>,
    pub end_local_slope: Option<f64>,
}

/// The governed values used to decide whether the route profile is complete.
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
pub struct TopographyPolicy {
    pub evidence_tolerance_m: f64,
    pub maximum_sample_spacing_m: f64,
    pub minimum_sustained_spacing_m: f64,
}

fn governed_policy() -> TopographyPolicy {
    TopographyPolicy {
        evidence_tolerance_m: EVIDENCE_TOLERANCE_M,
        maximum_sample_spacing_m: MAXIMUM_SAMPLE_SPACING_M,
        minimum_sustained_spacing_m: MINIMUM_SUSTAINED_SPACING_M,
    }
}

/// The strongest supported sustained gradient interval in route direction.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct SustainedGradient {
    pub gradient_pct: f64,
    pub absolute_gradient_pct: f64,
    pub interval_length_m: f64,
    /// The small set of samples supporting the displayed strongest interval.
    pub evidence_refs: Vec<String>,
}

/// Compact, serializable elevation facts for one ordered route.
///
/// Raw samples are deliberately retained only in the index. This profile
/// carries endpoint and strongest-interval evidence references, plus source
/// identifiers, coverage and directional aggregates for route comparison.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RouteTopographyProfile {
    pub availability: TopographyAvailability,
    pub reason: String,
    pub evidence_file: String,
    pub policy: TopographyPolicy,
    pub evidence_refs: Vec<String>,
    pub source_refs: Vec<String>,
    pub coverage: RouteCoverage,
    pub forward_ascent_m: Option<f64>,
    pub forward_descent_m: Option<f64>,
    pub reverse_ascent_m: Option<f64>,
    pub reverse_descent_m: Option<f64>,
    pub cumulative_elevation_variation_m: Option<f64>,
    pub sustained_gradient: Option<SustainedGradient>,
    pub source_resolution_m: Option<f64>,
    pub output_sample_spacing_m: Option<f64>,
    pub vertical_accuracy_m: Option<f64>,
    /// Moving-time estimate computed from the same ordered samples. The
    /// result retains its named model even when terrain is incomplete.
    #[serde(default = "unknown_travel_time_default")]
    pub estimated_moving_time: TravelTimeEstimate,
    /// Any bounded endpoint extension used only for moving-time integration.
    /// The original `coverage` remains the measured evidence coverage.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub moving_time_boundary_extrapolation: Option<MovingTimeBoundaryExtrapolation>,
    /// Flat-equivalent route-length sensitivity that remains available when
    /// terrain input is missing. It is explicitly not an e-bike ETA.
    #[serde(default)]
    pub hill_neutral_moving_time: Option<HillNeutralMovingTime>,
}

fn unknown_travel_time_default() -> TravelTimeEstimate {
    TravelTimeEstimate::Unknown {
        reason: "moving-time estimate was not published".to_string(),
        model: brouter_trekking_v1_7_10(),
    }
}

#[derive(Debug, Clone)]
struct IndexedSample {
    point: [f64; 2],
    elevation_m: f64,
    evidence_ref: String,
    source_ref: String,
    source_resolution_m: Option<f64>,
    output_sample_spacing_m: Option<f64>,
    vertical_accuracy_m: Option<f64>,
}

impl RTreeObject for IndexedSample {
    type Envelope = AABB<[f64; 2]>;

    fn envelope(&self) -> Self::Envelope {
        AABB::from_point(self.point)
    }
}

/// One loaded, projected elevation evidence index that can enrich many routes.
///
/// Construct this once for a pinned snapshot and reuse it for every ordered
/// feeder or candidate route in that compilation.
#[derive(Clone)]
pub struct ElevationEvidenceIndex {
    samples: RTree<IndexedSample>,
    evidence_file: String,
    projector: Projector,
}

impl ElevationEvidenceIndex {
    /// Load and project the point evidence from a pinned GeoJSON file once.
    pub fn load(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let features = read_feature_collection(path)?;
        let projector = Projector::new()?;
        let mut samples = Vec::new();
        for (index, feature) in features.iter().enumerate() {
            let Geometry::Point(point) = feature.geometry else {
                continue;
            };
            let Some(elevation_m) = number_property(&feature.properties, "elevation_m") else {
                continue;
            };
            if !elevation_m.is_finite() {
                continue;
            }
            let [x, y] = projector.point(point)?;
            let evidence_ref = string_property(&feature.properties, "evidence_id")
                .filter(|value| !value.trim().is_empty())
                .unwrap_or_else(|| format!("elevation-sample:{index}"));
            let source_ref = string_property(&feature.properties, "source_id")
                .unwrap_or_default()
                .trim()
                .to_string();
            samples.push(IndexedSample {
                point: [x, y],
                elevation_m,
                evidence_ref,
                source_ref,
                source_resolution_m: number_property(&feature.properties, "source_resolution_m"),
                output_sample_spacing_m: number_property(
                    &feature.properties,
                    "output_sample_spacing_m",
                ),
                vertical_accuracy_m: number_property(&feature.properties, "vertical_accuracy_m"),
            });
        }
        Ok(Self {
            samples: RTree::bulk_load(samples),
            evidence_file: path
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("elevation-evidence.geojson")
                .to_string(),
            projector,
        })
    }

    /// Enrich one ordered, continuous WGS84 route geometry.
    pub fn enrich_route(&self, route: &[[f64; 2]]) -> Result<RouteTopographyProfile> {
        let projected = self.projector.line(route)?;
        let projected = projected
            .0
            .iter()
            .map(|point| [point.x, point.y])
            .collect::<Vec<_>>();
        self.enrich_projected_route(&projected)
    }

    pub(crate) fn unknown_route_profile(
        &self,
        route_length_m: f64,
        reason: &str,
    ) -> RouteTopographyProfile {
        unknown_profile(route_length_m, reason, &self.evidence_file)
    }

    /// Enrich multipart route input, preserving the explicit unknown state for
    /// disconnected geometry rather than joining pieces with invented linework.
    pub fn enrich_route_parts(&self, route: &[Vec<[f64; 2]>]) -> Result<RouteTopographyProfile> {
        if route.len() != 1 {
            return Ok(unknown_profile(
                0.0,
                "route geometry is not one continuous usable line",
                &self.evidence_file,
            ));
        }
        self.enrich_route(route.first().map(Vec::as_slice).unwrap_or(&[]))
    }

    fn enrich_projected_route(&self, route: &[[f64; 2]]) -> Result<RouteTopographyProfile> {
        let route_length_m = polyline_length(route);
        let empty_coverage = RouteCoverage {
            route_length_m,
            start_m: None,
            end_m: None,
            maximum_gap_m: None,
            sample_count: 0,
        };
        if route.len() < 2 || route_length_m <= 0.0 {
            return Ok(unknown_profile_from_coverage(
                empty_coverage,
                "route geometry is not one continuous usable line",
                &self.evidence_file,
            ));
        }

        let Some(envelope) = route_envelope(route) else {
            return Ok(unknown_profile_from_coverage(
                empty_coverage,
                "route geometry is not one continuous usable line",
                &self.evidence_file,
            ));
        };
        let mut by_distance: BTreeMap<i64, BoundSample> = BTreeMap::new();
        for sample in self.samples.locate_in_envelope_intersecting(envelope) {
            let Some((distance_m, distance_to_route_m)) =
                closest_route_position(route, sample.point)
            else {
                continue;
            };
            if distance_to_route_m > EVIDENCE_TOLERANCE_M {
                continue;
            }
            // The Python reference groups equal projected distances at six
            // decimal places before normalisation. Preserve that policy while
            // making ties deterministic for the native index.
            let key = (distance_m * 1_000_000.0).round() as i64;
            let candidate = BoundSample::from_indexed(distance_m, sample);
            match by_distance.get_mut(&key) {
                Some(existing) if candidate.evidence_ref < existing.evidence_ref => {
                    *existing = candidate;
                }
                Some(_) => {}
                None => {
                    by_distance.insert(key, candidate);
                }
            }
        }
        let samples = by_distance.into_values().collect::<Vec<_>>();
        let coverage = coverage(route_length_m, &samples);
        if samples.len() < 2 {
            return Ok(with_refs(
                unknown_profile_from_coverage(
                    coverage,
                    "at least two usable Elevation Evidence samples are required",
                    &self.evidence_file,
                ),
                &samples,
            ));
        }
        if samples[0].distance_m > EVIDENCE_TOLERANCE_M
            || route_length_m - samples[samples.len() - 1].distance_m > EVIDENCE_TOLERANCE_M
        {
            return Ok(with_refs(
                unknown_profile_from_coverage(
                    coverage,
                    "Elevation Evidence does not cover both ends of the route",
                    &self.evidence_file,
                ),
                &samples,
            ));
        }
        if samples
            .windows(2)
            .any(|pair| pair[1].distance_m - pair[0].distance_m > MAXIMUM_SAMPLE_SPACING_M)
        {
            return Ok(with_refs(
                unknown_profile_from_coverage(
                    coverage,
                    "Elevation Evidence contains a disallowed interior gap",
                    &self.evidence_file,
                ),
                &samples,
            ));
        }

        let observed = normalise_observed_samples(&samples);
        let travel_time_model = brouter_trekking_v1_7_10();
        let (estimated_moving_time, moving_time_boundary_extrapolation) =
            match extend_moving_time_boundaries(&observed, route_length_m) {
                Ok((travel_time_samples, boundary_extrapolation)) => (
                    estimate_moving_time(&travel_time_samples, route_length_m, &travel_time_model),
                    boundary_extrapolation,
                ),
                Err(reason) => (
                    TravelTimeEstimate::Unknown {
                        reason: reason.to_string(),
                        model: travel_time_model.clone(),
                    },
                    None,
                ),
            };
        let hill_neutral_moving_time =
            estimate_hill_neutral_moving_time(route_length_m, &travel_time_model);
        let mut forward_ascent_m = 0.0;
        let mut forward_descent_m = 0.0;
        for pair in observed.windows(2) {
            let change = pair[1].elevation_m - pair[0].elevation_m;
            forward_ascent_m += change.max(0.0);
            forward_descent_m += (-change).max(0.0);
        }
        let sustained = sustained_samples(&observed)
            .windows(2)
            .filter_map(|pair| {
                let interval_length_m = pair[1].distance_m - pair[0].distance_m;
                if interval_length_m < MINIMUM_SUSTAINED_SPACING_M {
                    return None;
                }
                let gradient_pct =
                    (pair[1].elevation_m - pair[0].elevation_m) / interval_length_m * 100.0;
                Some(SustainedGradient {
                    gradient_pct,
                    absolute_gradient_pct: gradient_pct.abs(),
                    interval_length_m,
                    evidence_refs: compact_refs_for_pair(&pair[0], &pair[1]),
                })
            })
            .fold(None::<SustainedGradient>, |best, candidate| match best {
                Some(current)
                    if current.absolute_gradient_pct >= candidate.absolute_gradient_pct =>
                {
                    Some(current)
                }
                _ => Some(candidate),
            });
        let (evidence_refs, source_refs) = profile_references(&samples, sustained.as_ref());
        Ok(RouteTopographyProfile {
            availability: TopographyAvailability::Available,
            reason: format!(
                "{} governed Elevation Evidence samples cover the route",
                samples.len()
            ),
            evidence_file: self.evidence_file.clone(),
            policy: governed_policy(),
            evidence_refs,
            source_refs,
            coverage,
            forward_ascent_m: Some(forward_ascent_m),
            forward_descent_m: Some(forward_descent_m),
            reverse_ascent_m: Some(forward_descent_m),
            reverse_descent_m: Some(forward_ascent_m),
            cumulative_elevation_variation_m: Some(forward_ascent_m + forward_descent_m),
            sustained_gradient: sustained,
            source_resolution_m: metadata(&samples, |sample| sample.source_resolution_m),
            output_sample_spacing_m: metadata(&samples, |sample| sample.output_sample_spacing_m),
            vertical_accuracy_m: metadata(&samples, |sample| sample.vertical_accuracy_m),
            estimated_moving_time,
            moving_time_boundary_extrapolation,
            hill_neutral_moving_time,
        })
    }
}

pub(crate) fn unknown_route_profile(
    route_length_m: f64,
    reason: &str,
    evidence_file: &str,
) -> RouteTopographyProfile {
    unknown_profile(route_length_m, reason, evidence_file)
}

#[derive(Debug, Clone)]
struct BoundSample {
    distance_m: f64,
    elevation_m: f64,
    evidence_ref: String,
    source_ref: String,
    source_resolution_m: Option<f64>,
    output_sample_spacing_m: Option<f64>,
    vertical_accuracy_m: Option<f64>,
}

impl BoundSample {
    fn from_indexed(distance_m: f64, sample: &IndexedSample) -> Self {
        Self {
            distance_m,
            elevation_m: sample.elevation_m,
            evidence_ref: sample.evidence_ref.clone(),
            source_ref: sample.source_ref.clone(),
            source_resolution_m: sample.source_resolution_m,
            output_sample_spacing_m: sample.output_sample_spacing_m,
            vertical_accuracy_m: sample.vertical_accuracy_m,
        }
    }
}

fn unknown_profile(
    route_length_m: f64,
    reason: &str,
    evidence_file: &str,
) -> RouteTopographyProfile {
    unknown_profile_from_coverage(
        RouteCoverage {
            route_length_m,
            start_m: None,
            end_m: None,
            maximum_gap_m: None,
            sample_count: 0,
        },
        reason,
        evidence_file,
    )
}

fn unknown_profile_from_coverage(
    coverage: RouteCoverage,
    reason: &str,
    evidence_file: &str,
) -> RouteTopographyProfile {
    let hill_neutral_moving_time =
        estimate_hill_neutral_moving_time(coverage.route_length_m, &brouter_trekking_v1_7_10());
    RouteTopographyProfile {
        availability: TopographyAvailability::Unknown,
        reason: reason.to_string(),
        evidence_file: evidence_file.to_string(),
        policy: governed_policy(),
        evidence_refs: Vec::new(),
        source_refs: Vec::new(),
        coverage,
        forward_ascent_m: None,
        forward_descent_m: None,
        reverse_ascent_m: None,
        reverse_descent_m: None,
        cumulative_elevation_variation_m: None,
        sustained_gradient: None,
        source_resolution_m: None,
        output_sample_spacing_m: None,
        vertical_accuracy_m: None,
        estimated_moving_time: TravelTimeEstimate::Unknown {
            reason: reason.to_string(),
            model: brouter_trekking_v1_7_10(),
        },
        moving_time_boundary_extrapolation: None,
        hill_neutral_moving_time,
    }
}

fn with_refs(
    mut profile: RouteTopographyProfile,
    samples: &[BoundSample],
) -> RouteTopographyProfile {
    let (evidence_refs, source_refs) = profile_references(samples, None);
    profile.evidence_refs = evidence_refs;
    profile.source_refs = source_refs;
    profile
}

fn extend_moving_time_boundaries(
    observed: &[BoundSample],
    route_length_m: f64,
) -> std::result::Result<
    (
        Vec<ElevationSample>,
        Option<MovingTimeBoundaryExtrapolation>,
    ),
    &'static str,
> {
    if observed.len() < 2 {
        return Err("moving-time profile has too few observed samples");
    }
    let first = observed.first().expect("checked sample count");
    let second = &observed[1];
    let penultimate = &observed[observed.len() - 2];
    let last = observed.last().expect("checked sample count");
    if !route_length_m.is_finite()
        || !first.distance_m.is_finite()
        || !last.distance_m.is_finite()
        || !first.elevation_m.is_finite()
        || !second.elevation_m.is_finite()
        || !penultimate.elevation_m.is_finite()
        || !last.elevation_m.is_finite()
    {
        return Err("moving-time profile contains non-finite boundary data");
    }
    let start_extension_m = first.distance_m;
    let end_extension_m = route_length_m - last.distance_m;
    if start_extension_m < 0.0 || end_extension_m < 0.0 {
        return Err("moving-time profile extends beyond a route boundary");
    }
    if start_extension_m > EVIDENCE_TOLERANCE_M || end_extension_m > EVIDENCE_TOLERANCE_M {
        return Err("moving-time profile boundary gap exceeds evidence tolerance");
    }

    let start_local_slope = if start_extension_m > 0.0 {
        local_slope(first, second)?
    } else {
        0.0
    };
    let end_local_slope = if end_extension_m > 0.0 {
        local_slope(penultimate, last)?
    } else {
        0.0
    };
    let start_elevation_m = first.elevation_m - start_local_slope * start_extension_m;
    let end_elevation_m = last.elevation_m + end_local_slope * end_extension_m;
    if !start_elevation_m.is_finite() || !end_elevation_m.is_finite() {
        return Err("moving-time boundary extrapolation is not finite");
    }

    let mut samples = Vec::with_capacity(observed.len() + 2);
    if start_extension_m > 0.0 {
        samples.push(ElevationSample {
            distance_m: 0.0,
            elevation_m: start_elevation_m,
        });
    }
    samples.extend(observed.iter().map(|sample| ElevationSample {
        distance_m: sample.distance_m,
        elevation_m: sample.elevation_m,
    }));
    if end_extension_m > 0.0 {
        samples.push(ElevationSample {
            distance_m: route_length_m,
            elevation_m: end_elevation_m,
        });
    }

    let boundary_extrapolation = (start_extension_m > 0.0 || end_extension_m > 0.0).then(|| {
        MovingTimeBoundaryExtrapolation {
            start_extension_m: (start_extension_m > 0.0).then_some(start_extension_m),
            start_local_slope: (start_extension_m > 0.0).then_some(start_local_slope),
            end_extension_m: (end_extension_m > 0.0).then_some(end_extension_m),
            end_local_slope: (end_extension_m > 0.0).then_some(end_local_slope),
        }
    });
    Ok((samples, boundary_extrapolation))
}

fn local_slope(left: &BoundSample, right: &BoundSample) -> std::result::Result<f64, &'static str> {
    let distance_m = right.distance_m - left.distance_m;
    if !distance_m.is_finite() || distance_m <= 0.0 {
        return Err("moving-time profile boundary interval is not increasing");
    }
    let slope = (right.elevation_m - left.elevation_m) / distance_m;
    slope
        .is_finite()
        .then_some(slope)
        .ok_or("moving-time profile boundary slope is not finite")
}

fn route_envelope(route: &[[f64; 2]]) -> Option<AABB<[f64; 2]>> {
    let first = *route.first()?;
    let (mut min_x, mut max_x) = (first[0], first[0]);
    let (mut min_y, mut max_y) = (first[1], first[1]);
    for [x, y] in route.iter().copied() {
        min_x = min_x.min(x);
        max_x = max_x.max(x);
        min_y = min_y.min(y);
        max_y = max_y.max(y);
    }
    Some(AABB::from_corners(
        [min_x - EVIDENCE_TOLERANCE_M, min_y - EVIDENCE_TOLERANCE_M],
        [max_x + EVIDENCE_TOLERANCE_M, max_y + EVIDENCE_TOLERANCE_M],
    ))
}

fn polyline_length(route: &[[f64; 2]]) -> f64 {
    route
        .windows(2)
        .map(|pair| {
            let dx = pair[1][0] - pair[0][0];
            let dy = pair[1][1] - pair[0][1];
            dx.hypot(dy)
        })
        .sum()
}

fn closest_route_position(route: &[[f64; 2]], point: [f64; 2]) -> Option<(f64, f64)> {
    let mut distance_along = 0.0;
    let mut closest = None;
    for pair in route.windows(2) {
        let start = pair[0];
        let end = pair[1];
        let dx = end[0] - start[0];
        let dy = end[1] - start[1];
        let segment_length_squared = dx * dx + dy * dy;
        let segment_length = segment_length_squared.sqrt();
        if segment_length == 0.0 {
            continue;
        }
        let fraction =
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / segment_length_squared;
        let fraction = fraction.clamp(0.0, 1.0);
        let projected = [start[0] + dx * fraction, start[1] + dy * fraction];
        let distance_to_route = (point[0] - projected[0]).hypot(point[1] - projected[1]);
        let candidate = (
            distance_along + segment_length * fraction,
            distance_to_route,
        );
        if closest.map_or(true, |(_, distance)| candidate.1 < distance) {
            closest = Some(candidate);
        }
        distance_along += segment_length;
    }
    closest
}

fn coverage(route_length_m: f64, samples: &[BoundSample]) -> RouteCoverage {
    let start_m = samples.first().map(|sample| sample.distance_m);
    let end_m = samples.last().map(|sample| sample.distance_m);
    let maximum_gap_m = if samples.is_empty() {
        None
    } else {
        let start_gap = start_m.unwrap_or(0.0);
        let interior_gap = samples
            .windows(2)
            .map(|pair| pair[1].distance_m - pair[0].distance_m)
            .max_by(|left, right| left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal))
            .unwrap_or(0.0);
        let end_gap = route_length_m - end_m.unwrap_or(route_length_m);
        Some(start_gap.max(interior_gap).max(end_gap))
    };
    RouteCoverage {
        route_length_m,
        start_m,
        end_m,
        maximum_gap_m,
        sample_count: samples.len(),
    }
}

fn profile_references(
    samples: &[BoundSample],
    sustained: Option<&SustainedGradient>,
) -> (Vec<String>, Vec<String>) {
    let mut evidence = samples
        .first()
        .into_iter()
        .chain(samples.last())
        .flat_map(|sample| sample.evidence_ref.split('+'))
        .map(str::to_string)
        .collect::<Vec<_>>();
    if let Some(sustained) = sustained {
        evidence.extend(sustained.evidence_refs.iter().cloned());
    }
    evidence.sort();
    evidence.dedup();
    let mut sources = samples
        .iter()
        .filter(|sample| !sample.source_ref.is_empty())
        .flat_map(|sample| sample.source_ref.split('+'))
        .map(str::to_string)
        .collect::<Vec<_>>();
    sources.sort();
    sources.dedup();
    (evidence, sources)
}

fn compact_refs_for_pair(left: &BoundSample, right: &BoundSample) -> Vec<String> {
    let mut refs = left
        .evidence_ref
        .split('+')
        .chain(right.evidence_ref.split('+'))
        .map(str::to_string)
        .collect::<Vec<_>>();
    refs.sort();
    refs.dedup();
    refs
}

fn metadata(samples: &[BoundSample], value: impl Fn(&BoundSample) -> Option<f64>) -> Option<f64> {
    let values = samples.iter().map(value).collect::<Option<Vec<_>>>()?;
    if values.iter().any(|value| !value.is_finite()) {
        return None;
    }
    values
        .into_iter()
        .max_by(|left, right| left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal))
}

fn normalise_observed_samples(samples: &[BoundSample]) -> Vec<BoundSample> {
    let cluster_tolerance_m = (MINIMUM_SUSTAINED_SPACING_M / 20.0).min(0.5);
    let mut collapsed: Vec<(BoundSample, usize)> = Vec::new();
    let mut cluster: Vec<BoundSample> = Vec::new();
    let mut cluster_start = samples[0].distance_m;
    for sample in samples {
        if !cluster.is_empty() && sample.distance_m - cluster_start > cluster_tolerance_m {
            let cluster_len = cluster.len();
            collapsed.push((median_sample(&cluster), cluster_len));
            cluster.clear();
            cluster_start = sample.distance_m;
        }
        cluster.push(sample.clone());
    }
    if !cluster.is_empty() {
        let cluster_len = cluster.len();
        collapsed.push((median_sample(&cluster), cluster_len));
    }
    if collapsed.len() < 2 {
        return vec![samples[0].clone(), samples[samples.len() - 1].clone()];
    }
    let mut filtered = collapsed
        .iter()
        .map(|(sample, _)| sample.clone())
        .collect::<Vec<_>>();
    for index in 1..collapsed.len() - 1 {
        let left = &collapsed[index - 1].0;
        let current = &collapsed[index].0;
        let right = &collapsed[index + 1].0;
        let span = right.distance_m - left.distance_m;
        if collapsed[index].1 < 2 || span <= 0.0 {
            continue;
        }
        let left_change = current.elevation_m - left.elevation_m;
        let right_change = right.elevation_m - current.elevation_m;
        if left_change == 0.0 || right_change == 0.0 || left_change * right_change >= 0.0 {
            continue;
        }
        let fraction = (current.distance_m - left.distance_m) / span;
        filtered[index].elevation_m =
            left.elevation_m + (right.elevation_m - left.elevation_m) * fraction;
    }
    filtered
}

fn median_sample(samples: &[BoundSample]) -> BoundSample {
    let mut distances = samples
        .iter()
        .map(|sample| sample.distance_m)
        .collect::<Vec<_>>();
    distances.sort_by(|left, right| left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal));
    let mut elevations = samples
        .iter()
        .map(|sample| sample.elevation_m)
        .collect::<Vec<_>>();
    elevations.sort_by(|left, right| left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal));
    let middle = samples.len() / 2;
    let distance_m = if samples.len() % 2 == 1 {
        distances[middle]
    } else {
        (distances[middle - 1] + distances[middle]) / 2.0
    };
    let elevation_m = if samples.len() % 2 == 1 {
        elevations[middle]
    } else {
        (elevations[middle - 1] + elevations[middle]) / 2.0
    };
    let mut evidence_refs = samples
        .iter()
        .flat_map(|sample| sample.evidence_ref.split('+'))
        .map(str::to_string)
        .collect::<Vec<_>>();
    evidence_refs.sort();
    evidence_refs.dedup();
    let mut source_refs = samples
        .iter()
        .filter(|sample| !sample.source_ref.is_empty())
        .flat_map(|sample| sample.source_ref.split('+'))
        .map(str::to_string)
        .collect::<Vec<_>>();
    source_refs.sort();
    source_refs.dedup();
    BoundSample {
        distance_m,
        elevation_m,
        evidence_ref: evidence_refs.join("+"),
        source_ref: source_refs.join("+"),
        source_resolution_m: None,
        output_sample_spacing_m: None,
        vertical_accuracy_m: None,
    }
}

fn sustained_samples(samples: &[BoundSample]) -> Vec<BoundSample> {
    if samples[samples.len() - 1].distance_m - samples[0].distance_m <= MINIMUM_SUSTAINED_SPACING_M
    {
        return vec![samples[0].clone(), samples[samples.len() - 1].clone()];
    }
    let mut sustained = vec![samples[0].clone()];
    for sample in samples.iter().skip(1).take(samples.len().saturating_sub(2)) {
        if sample.distance_m - sustained.last().unwrap().distance_m >= MINIMUM_SUSTAINED_SPACING_M {
            sustained.push(sample.clone());
        }
    }
    let last = samples.last().unwrap();
    if last.distance_m - sustained.last().unwrap().distance_m < MINIMUM_SUSTAINED_SPACING_M
        && sustained.len() > 1
    {
        *sustained.last_mut().unwrap() = last.clone();
    } else {
        sustained.push(last.clone());
    }
    sustained
}
