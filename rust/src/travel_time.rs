//! Source-backed moving-time estimates for complete, directed route profiles.
//!
//! The calculation ports the bicycle kinematic model used by the pinned
//! BRouter Trekking profile and its `StdPath` implementation. It deliberately
//! models moving time only: junction, crossing, turn, waiting, and other
//! routing costs are not converted into seconds here.

use serde::{Deserialize, Serialize};

/// The pinned BRouter source used for the named Trekking model.
pub const BROUTER_SOURCE_COMMIT: &str = "4d2639af77ea5ed9c30d3e400764eb6f9e8522da";
pub const BROUTER_TREKKING_PROFILE_NAME: &str = "BRouter Trekking v1.7.10";
pub const BROUTER_TREKKING_PROFILE_URL: &str = "https://raw.githubusercontent.com/abrensch/brouter/4d2639af77ea5ed9c30d3e400764eb6f9e8522da/misc/profiles2/trekking.brf";
pub const BROUTER_STD_PATH_URL: &str = "https://raw.githubusercontent.com/abrensch/brouter/4d2639af77ea5ed9c30d3e400764eb6f9e8522da/brouter-core/src/main/java/btools/router/StdPath.java";
pub const HILL_NEUTRAL_LABEL: &str = "Hill-neutral sensitivity (not an e-bike ETA)";
pub const HILL_NEUTRAL_RATIONALE: &str = "CycleStreets documents an e-bike routing mode that ignores hills; this uses the same BRouter Trekking model and route length with slope set to zero to isolate gradient sensitivity, not to estimate an e-bike ETA.";
pub const HILL_NEUTRAL_SOURCE_URL: &str = "https://www.cyclestreets.net/help/journey/routing/";

const BROUTER_SOLVER_ITERATIONS: u8 = 10;
const BROUTER_SOLVER_INITIAL_SPEED_MPS: f64 = 8.0;
const BROUTER_SOLVER_RESIDUAL_THRESHOLD_W: f64 = 0.1;

/// One ordered elevation observation along a directed route.
///
/// `distance_m` is cumulative distance from the route origin, in metres, and
/// `elevation_m` is metres above the source datum. A complete input starts at
/// zero and ends at the supplied route length; callers must not fill missing
/// terrain with a flat or guessed value.
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq)]
pub struct ElevationSample {
    pub distance_m: f64,
    pub elevation_m: f64,
}

/// Named and serializable assumptions for one moving-time calculation.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct TravelTimeModel {
    pub name: String,
    pub source_commit: String,
    pub profile_source: String,
    pub solver_source: String,
    pub total_mass_kg: f64,
    pub max_speed_kmh: f64,
    /// Effective cubic drag coefficient in W·s³/m³ for `coefficient * v³`.
    pub aero_drag_coefficient_w_s3_per_m3: f64,
    pub rolling_resistance: f64,
    pub biker_power_w: f64,
    pub gravity_mps2: f64,
}

/// A complete result or an explicit unknown when the route profile cannot
/// support an estimate. Unknown results retain the named model and parameters
/// so a viewer can distinguish missing terrain from an absent model.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(tag = "availability", rename_all = "kebab-case")]
pub enum TravelTimeEstimate {
    Available {
        seconds: f64,
        minutes: f64,
        model: TravelTimeModel,
    },
    Unknown {
        reason: String,
        model: TravelTimeModel,
    },
}

/// A flat-equivalent sensitivity result for the same route length and named
/// Trekking model. This is intentionally not an assisted-bike estimate.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct HillNeutralMovingTime {
    pub label: String,
    pub rationale: String,
    pub source_url: String,
    pub seconds: f64,
    pub minutes: f64,
    pub model: TravelTimeModel,
}

impl TravelTimeEstimate {
    fn unknown(model: &TravelTimeModel, reason: impl Into<String>) -> Self {
        Self::Unknown {
            reason: reason.into(),
            model: model.clone(),
        }
    }
}

/// Construct the pinned, unassisted BRouter Trekking model.
pub fn brouter_trekking_v1_7_10() -> TravelTimeModel {
    TravelTimeModel {
        name: BROUTER_TREKKING_PROFILE_NAME.to_string(),
        source_commit: BROUTER_SOURCE_COMMIT.to_string(),
        profile_source: BROUTER_TREKKING_PROFILE_URL.to_string(),
        solver_source: BROUTER_STD_PATH_URL.to_string(),
        total_mass_kg: 90.0,
        max_speed_kmh: 45.0,
        aero_drag_coefficient_w_s3_per_m3: 0.225,
        rolling_resistance: 0.01,
        biker_power_w: 100.0,
        gravity_mps2: 9.81,
    }
}

/// Estimate moving time over a complete ordered elevation profile.
///
/// For each interval, this solves
///
/// `aero_drag_coefficient * v^3 + mass * gravity * (rolling_resistance + slope) * v = power`
///
/// in SI units, caps the resulting speed at the named profile maximum, and
/// adds `distance / speed`. The positive-root Newton method and its numerical
/// constants mirror BRouter `StdPath.solveCubic`; no junction or routing cost
/// is included.
pub fn estimate_moving_time(
    samples: &[ElevationSample],
    route_length_m: f64,
    model: &TravelTimeModel,
) -> TravelTimeEstimate {
    if !model_is_valid(model) {
        return TravelTimeEstimate::unknown(model, "moving-time model parameters are incomplete");
    }
    if !route_length_m.is_finite() || route_length_m <= 0.0 {
        return TravelTimeEstimate::unknown(model, "route length is unavailable");
    }
    if samples.len() < 2 {
        return TravelTimeEstimate::unknown(
            model,
            "at least two ordered elevation samples are required",
        );
    }
    if samples[0].distance_m != 0.0 || samples[samples.len() - 1].distance_m != route_length_m {
        return TravelTimeEstimate::unknown(
            model,
            "ordered elevation samples do not cover both route ends",
        );
    }

    let mut seconds = 0.0;
    for pair in samples.windows(2) {
        let left = pair[0];
        let right = pair[1];
        if !left.distance_m.is_finite()
            || !left.elevation_m.is_finite()
            || !right.distance_m.is_finite()
            || !right.elevation_m.is_finite()
        {
            return TravelTimeEstimate::unknown(
                model,
                "ordered elevation profile contains non-finite data",
            );
        }
        let distance_m = right.distance_m - left.distance_m;
        if distance_m <= 0.0 {
            return TravelTimeEstimate::unknown(
                model,
                "ordered elevation profile is not strictly increasing",
            );
        }
        let slope = (right.elevation_m - left.elevation_m) / distance_m;
        let Some(speed_mps) = speed_for_slope(model, slope) else {
            return TravelTimeEstimate::unknown(
                model,
                "moving-time speed equation has no positive root",
            );
        };
        if !speed_mps.is_finite() || speed_mps <= 0.0 {
            return TravelTimeEstimate::unknown(model, "moving-time speed is not positive");
        }
        seconds += distance_m / speed_mps;
        if !seconds.is_finite() {
            return TravelTimeEstimate::unknown(model, "moving-time result is not finite");
        }
    }

    TravelTimeEstimate::Available {
        seconds,
        minutes: seconds / 60.0,
        model: model.clone(),
    }
}

/// Estimate the same route length with its slope set to zero for every
/// interval. This can remain available when terrain input is missing because
/// it explicitly reports a flat-equivalent sensitivity, not actual terrain
/// travel time or an e-bike ETA.
pub fn estimate_hill_neutral_moving_time(
    route_length_m: f64,
    model: &TravelTimeModel,
) -> Option<HillNeutralMovingTime> {
    if !model_is_valid(model) || !route_length_m.is_finite() || route_length_m <= 0.0 {
        return None;
    }
    let speed_mps = speed_for_slope(model, 0.0)?;
    let seconds = route_length_m / speed_mps;
    if !seconds.is_finite() {
        return None;
    }
    Some(HillNeutralMovingTime {
        label: HILL_NEUTRAL_LABEL.to_string(),
        rationale: HILL_NEUTRAL_RATIONALE.to_string(),
        source_url: HILL_NEUTRAL_SOURCE_URL.to_string(),
        seconds,
        minutes: seconds / 60.0,
        model: model.clone(),
    })
}

fn model_is_valid(model: &TravelTimeModel) -> bool {
    model.total_mass_kg.is_finite()
        && model.total_mass_kg > 0.0
        && model.max_speed_kmh.is_finite()
        && model.max_speed_kmh > 0.0
        && model.aero_drag_coefficient_w_s3_per_m3.is_finite()
        && model.aero_drag_coefficient_w_s3_per_m3 > 0.0
        && model.rolling_resistance.is_finite()
        && model.biker_power_w.is_finite()
        && model.biker_power_w > 0.0
        && model.gravity_mps2.is_finite()
        && model.gravity_mps2 > 0.0
}

fn speed_for_slope(model: &TravelTimeModel, slope: f64) -> Option<f64> {
    let rolling_force =
        model.total_mass_kg * model.gravity_mps2 * (model.rolling_resistance + slope);
    let solved_speed_mps = solve_cubic(
        model.aero_drag_coefficient_w_s3_per_m3,
        rolling_force,
        model.biker_power_w,
        BROUTER_SOLVER_INITIAL_SPEED_MPS,
        BROUTER_SOLVER_ITERATIONS,
        BROUTER_SOLVER_RESIDUAL_THRESHOLD_W,
    )?;
    let speed_mps = solved_speed_mps.min(model.max_speed_kmh / 3.6);
    (speed_mps.is_finite() && speed_mps > 0.0).then_some(speed_mps)
}

/// BRouter `StdPath.solveCubic`: Newton's method with its source-defined
/// initial-value search and ten-iteration budget.
fn solve_cubic(
    a: f64,
    c: f64,
    d: f64,
    initial_speed_mps: f64,
    iterations: u8,
    residual_threshold_w: f64,
) -> Option<f64> {
    let mut speed = initial_speed_mps;
    let mut finding_start_value = true;
    for _ in 0..iterations {
        let residual = (a * speed * speed + c) * speed - d;
        if !residual.is_finite() {
            return None;
        }
        if residual < residual_threshold_w {
            if finding_start_value {
                speed *= 2.0;
                continue;
            }
            break;
        }
        finding_start_value = false;
        let derivative = 3.0 * a * speed * speed + c;
        if !derivative.is_finite() || derivative == 0.0 {
            return None;
        }
        speed -= residual / derivative;
        if !speed.is_finite() {
            return None;
        }
    }
    (speed.is_finite() && speed > 0.0).then_some(speed)
}
