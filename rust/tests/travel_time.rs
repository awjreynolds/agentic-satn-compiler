#[path = "../src/travel_time.rs"]
mod travel_time;

use travel_time::{
    ElevationSample, TravelTimeEstimate, brouter_trekking_v1_7_10,
    estimate_hill_neutral_moving_time, estimate_moving_time,
};

fn estimate(elevations: &[(f64, f64)], route_length_m: f64) -> TravelTimeEstimate {
    let samples = elevations
        .iter()
        .map(|(distance_m, elevation_m)| ElevationSample {
            distance_m: *distance_m,
            elevation_m: *elevation_m,
        })
        .collect::<Vec<_>>();
    estimate_moving_time(&samples, route_length_m, &brouter_trekking_v1_7_10())
}

#[test]
fn flat_route_matches_the_named_brouter_profile() {
    let TravelTimeEstimate::Available { seconds, model, .. } =
        estimate(&[(0.0, 0.0), (1_000.0, 0.0)], 1_000.0)
    else {
        panic!("flat complete profile should be estimable");
    };

    assert!((seconds - 168.0036).abs() < 0.01);
    assert_eq!(model.name, "BRouter Trekking v1.7.10");
    assert_eq!(model.total_mass_kg, 90.0);
    assert_eq!(model.biker_power_w, 100.0);
}

#[test]
fn uphill_is_slower_and_downhill_is_faster() {
    let TravelTimeEstimate::Available {
        seconds: flat_seconds,
        ..
    } = estimate(&[(0.0, 0.0), (1_000.0, 0.0)], 1_000.0)
    else {
        panic!("flat complete profile should be estimable");
    };
    let TravelTimeEstimate::Available {
        seconds: uphill_seconds,
        ..
    } = estimate(&[(0.0, 0.0), (1_000.0, 100.0)], 1_000.0)
    else {
        panic!("uphill complete profile should be estimable");
    };
    let TravelTimeEstimate::Available {
        seconds: downhill_seconds,
        ..
    } = estimate(&[(0.0, 100.0), (1_000.0, 0.0)], 1_000.0)
    else {
        panic!("downhill complete profile should be estimable");
    };

    assert!(uphill_seconds > flat_seconds);
    assert!(downhill_seconds < flat_seconds);
}

#[test]
fn route_direction_changes_the_estimate() {
    let TravelTimeEstimate::Available {
        seconds: forward_seconds,
        ..
    } = estimate(&[(0.0, 0.0), (500.0, 100.0), (1_000.0, 50.0)], 1_000.0)
    else {
        panic!("forward complete profile should be estimable");
    };
    let TravelTimeEstimate::Available {
        seconds: reverse_seconds,
        ..
    } = estimate(&[(0.0, 50.0), (500.0, 100.0), (1_000.0, 0.0)], 1_000.0)
    else {
        panic!("reverse complete profile should be estimable");
    };

    assert!((forward_seconds - reverse_seconds).abs() > 0.1);
}

#[test]
fn missing_or_incomplete_ordered_profile_is_unknown() {
    assert!(matches!(
        estimate(&[], 1_000.0),
        TravelTimeEstimate::Unknown { .. }
    ));
    assert!(matches!(
        estimate(&[(0.0, 0.0), (500.0, 10.0), (400.0, 20.0)], 1_000.0),
        TravelTimeEstimate::Unknown { .. }
    ));
    assert!(matches!(
        estimate(&[(10.0, 0.0), (1_000.0, 0.0)], 1_000.0),
        TravelTimeEstimate::Unknown { .. }
    ));
}

#[test]
fn hill_neutral_is_flat_equivalent_and_exposes_sensitivity_label() {
    let model = brouter_trekking_v1_7_10();
    let TravelTimeEstimate::Available {
        seconds: flat_seconds,
        ..
    } = estimate(&[(0.0, 0.0), (1_000.0, 0.0)], 1_000.0)
    else {
        panic!("flat complete profile should be estimable");
    };
    let hill_neutral = estimate_hill_neutral_moving_time(1_000.0, &model)
        .expect("positive route length should have a flat-equivalent estimate");
    let TravelTimeEstimate::Available {
        seconds: hill_seconds,
        ..
    } = estimate(&[(0.0, 0.0), (1_000.0, 100.0)], 1_000.0)
    else {
        panic!("uphill complete profile should be estimable");
    };

    assert!((hill_neutral.seconds - flat_seconds).abs() < 0.01);
    assert!(hill_seconds > hill_neutral.seconds);
    assert_eq!(
        hill_neutral.label,
        "Hill-neutral sensitivity (not an e-bike ETA)"
    );
    assert!(hill_neutral.rationale.contains("slope set to zero"));
}
