use std::fs;
use std::path::{Path, PathBuf};

use satn_rs::topography::{ElevationEvidenceIndex, TopographyAvailability};
use satn_rs::travel_time::TravelTimeEstimate;
use serde_json::{Value, json};

#[test]
fn route_profile_retains_directional_climbing_and_evidence_metadata() {
    let root = fixture_root("available");
    let evidence = root.join("elevation-evidence.geojson");
    write_points(
        &evidence,
        &[
            ("e-1", "dtm", [-2.5000, 51.5000], 0.0),
            ("e-2", "dtm", [-2.4990, 51.5000], 20.0),
            ("e-3", "dtm", [-2.4980, 51.5000], 15.0),
            ("e-4", "dtm", [-2.4970, 51.5000], 25.0),
        ],
    );

    let index = ElevationEvidenceIndex::load(&evidence).expect("elevation evidence");
    let profile = index
        .enrich_route(&[
            [-2.5000, 51.5000],
            [-2.4990, 51.5000],
            [-2.4980, 51.5000],
            [-2.4970, 51.5000],
        ])
        .expect("route profile");

    assert_eq!(profile.availability, TopographyAvailability::Available);
    assert_eq!(profile.evidence_file, "elevation-evidence.geojson");
    assert_eq!(profile.policy.evidence_tolerance_m, 5.0);
    assert_eq!(profile.policy.maximum_sample_spacing_m, 250.0);
    assert_eq!(profile.policy.minimum_sustained_spacing_m, 10.0);
    assert_eq!(profile.evidence_refs, vec!["e-1", "e-2", "e-4"]);
    assert_eq!(profile.source_refs, vec!["dtm"]);
    assert!(profile.coverage.start_m.expect("coverage start").abs() < 1e-9);
    assert!(
        (profile.coverage.end_m.expect("coverage end") - profile.coverage.route_length_m).abs()
            < 1e-6
    );
    assert_eq!(profile.forward_ascent_m, Some(30.0));
    assert_eq!(profile.forward_descent_m, Some(5.0));
    assert_eq!(profile.reverse_ascent_m, Some(5.0));
    assert_eq!(profile.reverse_descent_m, Some(30.0));
    assert_eq!(profile.cumulative_elevation_variation_m, Some(35.0));
    assert!(profile.sustained_gradient.is_some());
    assert!(
        profile
            .sustained_gradient
            .as_ref()
            .expect("sustained gradient")
            .interval_length_m
            >= 10.0
    );
    assert_eq!(profile.source_resolution_m, Some(1.0));
    assert_eq!(profile.output_sample_spacing_m, Some(10.0));
    assert_eq!(profile.vertical_accuracy_m, Some(0.15));

    let serialized = serde_json::to_value(&profile).expect("serializable profile");
    assert!(serialized.get("samples").is_none());
}

#[test]
fn moving_time_extends_a_bounded_missing_start_without_changing_coverage() {
    let root = fixture_root("moving-time-boundary");
    let evidence = root.join("elevation-evidence.geojson");
    write_points(
        &evidence,
        &[
            ("e-1", "dtm", [-2.49999, 51.5000], 0.0),
            ("e-2", "dtm", [-2.49900, 51.5000], 20.0),
            ("e-3", "dtm", [-2.49800, 51.5000], 15.0),
        ],
    );

    let index = ElevationEvidenceIndex::load(&evidence).expect("elevation evidence");
    let profile = index
        .enrich_route(&[
            [-2.50000, 51.5000],
            [-2.49900, 51.5000],
            [-2.49800, 51.5000],
        ])
        .expect("route profile");

    assert_eq!(profile.availability, TopographyAvailability::Available);
    let coverage_start = profile.coverage.start_m.expect("coverage start");
    assert!(coverage_start > 0.0 && coverage_start < 5.0);
    assert!(
        (profile.coverage.end_m.expect("coverage end") - profile.coverage.route_length_m).abs()
            < 1e-6
    );
    assert!(matches!(
        profile.estimated_moving_time,
        TravelTimeEstimate::Available { .. }
    ));

    let boundary = profile
        .moving_time_boundary_extrapolation
        .expect("bounded endpoint extension should be recorded");
    let start_extension = boundary.start_extension_m.expect("start extension");
    assert!((start_extension - coverage_start).abs() < 1e-9);
    assert!(boundary.start_local_slope.expect("start local slope") > 0.0);
    assert!(boundary.end_extension_m.is_none());
    assert!(boundary.end_local_slope.is_none());
}

#[test]
fn reversing_route_swaps_ascent_and_descent() {
    let root = fixture_root("reverse");
    let evidence = root.join("elevation-evidence.geojson");
    write_points(
        &evidence,
        &[
            ("e-1", "dtm", [-2.5000, 51.5000], 0.0),
            ("e-2", "dtm", [-2.4990, 51.5000], 20.0),
            ("e-3", "dtm", [-2.4980, 51.5000], 15.0),
            ("e-4", "dtm", [-2.4970, 51.5000], 25.0),
        ],
    );

    let index = ElevationEvidenceIndex::load(&evidence).expect("elevation evidence");
    let forward = index
        .enrich_route(&[
            [-2.5000, 51.5000],
            [-2.4990, 51.5000],
            [-2.4980, 51.5000],
            [-2.4970, 51.5000],
        ])
        .expect("forward profile");
    let reverse = index
        .enrich_route(&[
            [-2.4970, 51.5000],
            [-2.4980, 51.5000],
            [-2.4990, 51.5000],
            [-2.5000, 51.5000],
        ])
        .expect("reverse profile");

    assert_eq!(reverse.forward_ascent_m, forward.reverse_ascent_m);
    assert_eq!(reverse.forward_descent_m, forward.reverse_descent_m);
    assert_eq!(reverse.reverse_ascent_m, forward.forward_ascent_m);
    assert_eq!(reverse.reverse_descent_m, forward.forward_descent_m);
    assert_eq!(
        reverse.cumulative_elevation_variation_m,
        forward.cumulative_elevation_variation_m
    );
}

#[test]
fn profile_exposes_measured_climbing_difference_without_an_effort_score() {
    let root = fixture_root("comparison");
    let evidence = root.join("up-elevation-evidence.geojson");
    write_points(
        &evidence,
        &[
            ("e-1", "dtm", [-2.5000, 51.5000], 0.0),
            ("e-2", "dtm", [-2.4990, 51.5000], 20.0),
            ("e-3", "dtm", [-2.4980, 51.5000], 0.0),
        ],
    );
    let up_index = ElevationEvidenceIndex::load(&evidence).expect("up elevation evidence");
    let up = up_index
        .enrich_route(&[[-2.5000, 51.5000], [-2.4990, 51.5000], [-2.4980, 51.5000]])
        .expect("up-and-down profile");
    let flatter_evidence = root.join("flatter-elevation-evidence.geojson");
    write_points(
        &flatter_evidence,
        &[
            ("f-1", "dtm", [-2.5000, 51.5000], 0.0),
            ("f-2", "dtm", [-2.4990, 51.5000], 2.0),
            ("f-3", "dtm", [-2.4980, 51.5000], 0.0),
        ],
    );
    let flatter_index =
        ElevationEvidenceIndex::load(&flatter_evidence).expect("flatter elevation evidence");
    let flatter = flatter_index
        .enrich_route(&[[-2.5000, 51.5000], [-2.4990, 51.5000], [-2.4980, 51.5000]])
        .expect("flatter profile");

    assert_eq!(up.cumulative_elevation_variation_m, Some(40.0));
    assert_eq!(flatter.cumulative_elevation_variation_m, Some(4.0));
    assert!(
        up.cumulative_elevation_variation_m.unwrap()
            > flatter.cumulative_elevation_variation_m.unwrap()
    );
    assert!(
        up.sustained_gradient
            .as_ref()
            .expect("sustained gradient")
            .absolute_gradient_pct
            > 1.0
    );
    let json = serde_json::to_string(&up).expect("profile JSON");
    assert!(!json.contains("effort"));
    assert!(!json.contains("route_weight"));
}

#[test]
fn normalisation_suppresses_only_a_corroborated_local_reversal() {
    let root = fixture_root("normalisation");
    let evidence = root.join("elevation-evidence.geojson");
    write_points(
        &evidence,
        &[
            ("e-1", "dtm", [-2.500000, 51.5000], 0.0),
            ("e-2", "dtm", [-2.499001, 51.5000], 20.0),
            ("e-3", "dtm", [-2.499000, 51.5000], 100.0),
            ("e-4", "dtm", [-2.498000, 51.5000], 0.0),
        ],
    );
    let index = ElevationEvidenceIndex::load(&evidence).expect("elevation evidence");
    let profile = index
        .enrich_route(&[
            [-2.500000, 51.5000],
            [-2.499000, 51.5000],
            [-2.498000, 51.5000],
        ])
        .expect("normalised profile");

    assert_eq!(profile.availability, TopographyAvailability::Available);
    assert_eq!(profile.forward_ascent_m, Some(0.0));
    assert_eq!(profile.forward_descent_m, Some(0.0));
    assert_eq!(profile.cumulative_elevation_variation_m, Some(0.0));
}

#[test]
fn incomplete_or_disconnected_evidence_is_unknown() {
    let root = fixture_root("unknown");
    let evidence = root.join("elevation-evidence.geojson");
    write_points(
        &evidence,
        &[
            ("e-1", "dtm", [-2.5000, 51.5000], 0.0),
            ("e-2", "dtm", [-2.4990, 51.5000], 20.0),
            ("e-3", "dtm", [-2.4900, 51.5000], 10.0),
        ],
    );
    let index = ElevationEvidenceIndex::load(&evidence).expect("elevation evidence");

    let missing_end = index
        .enrich_route(&[[-2.5000, 51.5000], [-2.4980, 51.5000]])
        .expect("missing-end profile");
    assert_eq!(missing_end.availability, TopographyAvailability::Unknown);
    assert!(missing_end.reason.contains("both ends"));
    assert!(missing_end.forward_ascent_m.is_none());

    let interior_gap = index
        .enrich_route(&[[-2.5000, 51.5000], [-2.4900, 51.5000]])
        .expect("gappy profile");
    assert_eq!(interior_gap.availability, TopographyAvailability::Unknown);
    assert!(interior_gap.reason.contains("interior gap"));

    let disconnected = index
        .enrich_route_parts(&[
            vec![[-2.5000, 51.5000], [-2.4990, 51.5000]],
            vec![[-2.4900, 51.5000], [-2.4890, 51.5000]],
        ])
        .expect("disconnected profile");
    assert_eq!(disconnected.availability, TopographyAvailability::Unknown);
    assert!(disconnected.reason.contains("continuous"));
}

fn fixture_root(label: &str) -> PathBuf {
    let root =
        std::env::temp_dir().join(format!("satn-rs-topography-{label}-{}", std::process::id()));
    if root.exists() {
        fs::remove_dir_all(&root).expect("clean fixture root");
    }
    fs::create_dir_all(&root).expect("fixture root");
    root
}

fn write_points(path: &Path, points: &[(&str, &str, [f64; 2], f64)]) {
    let features = points
        .iter()
        .map(|(evidence_id, source_id, point, elevation)| {
            json!({
                "type": "Feature",
                "properties": {
                    "evidence_id": evidence_id,
                    "source_id": source_id,
                    "elevation_m": elevation,
                    "source_resolution_m": 1.0,
                    "output_sample_spacing_m": 10.0,
                    "vertical_accuracy_m": 0.15,
                },
                "geometry": {"type": "Point", "coordinates": point},
            })
        })
        .collect::<Vec<Value>>();
    fs::write(
        path,
        serde_json::to_vec(&json!({"type":"FeatureCollection","features":features}))
            .expect("fixture JSON"),
    )
    .expect("write fixture");
}
