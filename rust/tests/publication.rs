use std::fs;
use std::process::Command;

use satn_rs::midend::{MidendRun, PlanningBase, TypedOperation};
use satn_rs::{
    AccessObligation, AccountingSummary, Candidate, CandidateNeighbourhood,
    CandidateNeighbourhoodGeometry, CommunityAccess, CompileReport, Connection, NetworkPlace,
    SchoolContext, SourceCorridor, UnknownFact, UrbanEntry, add_bus_context, load_retained_report,
    publish_decision_map,
};
use serde_json::{Value, json};

#[test]
fn publishes_compact_decision_map_with_real_departure_sections() {
    let root = tempfile_root("satn-rs-publication");
    fs::create_dir_all(&root).expect("publication root");
    fs::write(root.join("decision-map.html"), "stale duplicate").expect("stale map");
    let report = report_fixture();
    let run = MidendRun {
        branch: "review-branch".to_string(),
        base_id: "base:fixture:snapshot".to_string(),
        status: "unresolved".to_string(),
        task_ids: vec![
            "task:connection:selected".to_string(),
            "task:connection:provisional".to_string(),
            "task:connection:unresolved".to_string(),
        ],
        operations: vec![
            TypedOperation::SelectAlignment {
                id: "decision:selected".to_string(),
                task_id: "task:connection:selected".to_string(),
                attempt_id: "attempt:selected".to_string(),
                connection_id: "connection:selected".to_string(),
                candidate_id: "candidate:selected".to_string(),
                decision_class: "classifier".to_string(),
                provisional: false,
                reason: None,
                uncertainties: Vec::new(),
            },
            TypedOperation::SelectAlignment {
                id: "decision:provisional".to_string(),
                task_id: "task:connection:provisional".to_string(),
                attempt_id: "attempt:provisional".to_string(),
                connection_id: "connection:provisional".to_string(),
                candidate_id: "candidate:provisional".to_string(),
                decision_class: "agent".to_string(),
                provisional: true,
                reason: Some(
                    "Best available alignment while access evidence is unresolved.".to_string(),
                ),
                uncertainties: vec!["access evidence is unresolved".to_string()],
            },
            TypedOperation::Unresolved {
                id: "decision:unresolved".to_string(),
                task_id: "task:connection:unresolved".to_string(),
                attempt_id: "attempt:unresolved".to_string(),
                connection_id: "connection:unresolved".to_string(),
                decision_class: "agent".to_string(),
                marker: Some("__needs_evidence__".to_string()),
                reason: "The supplied evidence does not establish a defensible alignment."
                    .to_string(),
                uncertainties: vec!["provision status is unknown".to_string()],
            },
        ],
        community_access: Vec::new(),
    };

    let publication = publish_decision_map(&root, &report, &run).expect("publish decision map");
    assert_eq!(publication.branch, "review-branch");
    assert_eq!(publication.decision_count, 3);
    assert_eq!(publication.selected_count, 2);
    assert_eq!(publication.provisional_count, 1);
    assert_eq!(publication.unresolved_count, 1);
    assert_eq!(publication.departure_count, 2);

    let geojson: Value = serde_json::from_str(
        &fs::read_to_string(root.join("decision-map.geojson")).expect("GeoJSON output"),
    )
    .expect("valid decision GeoJSON");
    let features = geojson["features"].as_array().expect("features");
    assert!(
        features
            .iter()
            .any(|feature| feature["properties"]["kind"] == "selected-alignment")
    );
    assert!(
        features
            .iter()
            .any(|feature| feature["properties"]["kind"] == "provisional-alignment")
    );
    assert!(
        features
            .iter()
            .any(|feature| feature["properties"]["kind"] == "unresolved-decision")
    );
    let departure = features
        .iter()
        .find(|feature| feature["properties"]["kind"] == "a-road-departure")
        .expect("departure feature");
    assert_eq!(departure["properties"]["decision_class"], "classifier");
    assert_eq!(
        departure["properties"]["alternative_candidate_id"],
        "candidate:strategic"
    );
    assert!(departure["properties"]["evidence_refs"].is_array());
    assert_eq!(departure["geometry"]["type"], "LineString");
    assert!(features.iter().any(|feature| {
        feature["properties"]["kind"] == "source-baseline"
            && feature["properties"]["source_corridor_id"] == "source:unattached"
    }));
    assert!(features.iter().any(|feature| {
        feature["properties"]["kind"] == "source-departure"
            && feature["properties"]["source_corridor_id"] == "source:cycleway"
    }));
    let community_access = features
        .iter()
        .find(|feature| feature["properties"]["kind"] == "community-access")
        .expect("community access feature");
    assert_eq!(community_access["properties"]["status"], "served");
    assert_eq!(
        community_access["properties"]["joined_spine_reference"],
        "A-road"
    );
    assert_eq!(
        community_access["properties"]["decision_class"],
        "mechanical"
    );
    assert_eq!(
        community_access["properties"]["attachment_edge_id"],
        "edge-access"
    );
    assert_eq!(
        community_access["properties"]["attachment_point"],
        serde_json::json!([0.0, 0.0])
    );
    assert_eq!(
        community_access["properties"]["root_spine_id"],
        "source:a-road"
    );
    assert_eq!(community_access["properties"]["new_link_length_m"], 120.0);
    assert_eq!(
        community_access["properties"]["full_access_length_m"],
        120.0
    );
    assert_eq!(community_access["geometry"]["type"], "LineString");
    let school = features
        .iter()
        .find(|feature| feature["properties"]["kind"] == "school-context")
        .expect("school context feature");
    assert_eq!(school["properties"]["source_id"], "school-source-1");
    assert_eq!(school["properties"]["name"], "Alpha School");
    assert_eq!(school["geometry"]["type"], "Point");
    assert!(!features.iter().any(|feature| {
        feature["properties"]["kind"] == "a-road-departure"
            && feature["properties"]["source_corridor_id"] == "source:unattached"
    }));

    let compact = fs::read_to_string(root.join("decision-map.json")).expect("compact output");
    assert!(compact.contains("review-branch"));
    assert!(compact.contains("Experimental SATN POC — not an adopted plan."));
    assert!(!compact.contains("receipt"));
    let html = fs::read_to_string(root.join("index.html")).expect("HTML output");
    assert!(html.contains("review-branch"));
    assert!(html.contains("Provisional alignment"));
    assert!(html.contains("Unresolved decision"));
    assert!(html.contains("Strategic source baseline"));
    assert!(html.contains("Additional source evidence"));
    assert!(!html.contains("source_inventory"));
    assert!(root.join("index.html").is_file());
    let publication: Value = serde_json::from_str(
        &fs::read_to_string(root.join("publication.json")).expect("publication manifest"),
    )
    .expect("valid publication manifest");
    assert_eq!(publication["publication_kind"], "native-agentic");
    assert_eq!(publication["deployment_id"], "fixture");
    assert_eq!(publication["attribution"], "Fixture attribution");
    assert_eq!(
        publication["source_attributions"][0],
        "Fixture official attribution"
    );
    assert_eq!(publication["status"], "reviewable-with-gaps");
    assert_eq!(publication["run_status"], "unresolved");
    assert_eq!(publication["files"]["html"], "index.html");
    assert!(publication["files"].get("planning").is_none());
    assert!(publication["files"].get("decision_map_html").is_none());
    assert_eq!(
        publication["files"]
            .as_object()
            .expect("public files")
            .len(),
        4
    );
    assert!(!root.join("decision-map.html").exists());
    assert!(html.contains("data-native-publication=\"native-agentic\""));
    assert!(html.contains("data-network-url=\"decision-map.geojson\""));
    assert!(html.contains("data-native-deployment=\"fixture\""));
    assert!(html.contains("data-native-branch"));
    assert!(html.contains("native-selected"));
    assert!(html.contains("native-a-road-departure"));
    assert!(html.contains("native-source-departure"));
    assert!(html.contains("data-layer-toggle=\"school-context\""));
    assert!(html.contains("native-school-context"));
    assert!(html.contains("data-layer-toggle=\"community-access\""));
    assert!(!html.contains("data-layer-toggle=\"candidate-neighbourhood\""));
    assert!(html.contains("native-community-access"));
    assert!(html.contains("Parent community"));
    assert!(html.contains("New link distance"));
    assert!(html.contains("Complete access distance"));
    assert!(html.contains("native-feature-details"));
    assert!(html.contains("Strategic source baseline"));
    assert!(html.contains("Provisional alignment"));
    assert!(html.contains("Unresolved decisions"));
    assert!(html.contains("A-road departures"));
    assert!(html.contains("Source departures"));
    assert!(html.contains("Towns and communities"));
    assert!(html.contains("Schools"));
    assert!(html.contains("Community Connections"));
    assert!(html.contains("Access obligations and gaps"));
    assert!(html.contains("Study area boundary"));
    assert!(html.contains("aria-describedby=\"layer-help-2\""));
    assert!(!html.contains("data-layer-toggle=\"selected-alignment\""));
    assert!(html.contains("data-layer-toggle=\"strategic-network\" checked"));
    assert!(html.contains("native-marker-access-obligation"));
    assert!(html.contains("native-highlight-line-casing"));
    assert!(!html.contains("data-native-decision-kind"));
    assert!(!html.contains("data-native-departure=\""));
    assert!(html.contains("Fixture attribution"));
    assert!(html.contains("Fixture official attribution"));
    assert!(html.contains("assets/maplibre-gl.js"));
    assert!(html.contains("assets/maplibre-gl.css"));
    assert!(html.contains("window.SATN_NATIVE_MAP"));
    assert!(html.contains("data-native-reset"));
    assert!(html.contains("nativeNetworkLoaded"));
    assert!(html.contains("nativeReady"));
    assert!(html.contains("data-layer-toggle=\"candidate-alternative\""));
    assert!(html.contains("data-native-clear"));
    assert!(!html.contains("<svg"));
    assert!(!html.contains("summary.json"));
    assert!(root.join("assets/maplibre-gl.js").is_file());
    assert!(root.join("assets/maplibre-gl.css").is_file());
    assert!(root.join("assets/MAPLIBRE-LICENSE.txt").is_file());
}

#[test]
fn adds_sourced_bus_context_to_an_existing_publication_without_rewriting_decisions() {
    let root = tempfile_root("satn-rs-bus-context");
    fs::create_dir_all(&root).expect("publication root");
    let report = report_fixture();
    let run = MidendRun {
        branch: "bus-context-branch".to_string(),
        base_id: "base:bus-context:snapshot".to_string(),
        status: "complete".to_string(),
        task_ids: Vec::new(),
        operations: Vec::new(),
        community_access: Vec::new(),
    };
    publish_decision_map(&root, &report, &run).expect("publish decision map");
    let initial_html = fs::read_to_string(root.join("index.html")).expect("base map HTML");
    let current_loader =
        "<script src=\"bus-context.js\" data-satn-bus-context data-context-url=\"\"></script>";
    assert!(initial_html.contains(current_loader));
    fs::write(
        root.join("index.html"),
        initial_html.replace(current_loader, ""),
    )
    .expect("simulate a previously published map without the bus loader");
    let original_geojson = fs::read(root.join("decision-map.geojson")).expect("base GeoJSON");
    let original_decisions = fs::read(root.join("decision-map.json")).expect("decision manifest");
    let original_publication =
        fs::read(root.join("publication.json")).expect("publication manifest");

    let source_file = root.join("weca-bus-context.geojson");
    fs::write(
        &source_file,
        serde_json::to_vec(&json!({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "bus-route",
                        "route_ids": ["weca:route:1"],
                        "route_short_names": ["1"],
                        "service_date": "2026-09-23",
                        "source_id": "weca-gtfs-2026-09",
                        "shape_id": "shape-1",
                        "source_title": "WECA <script>alert(1)</script>",
                        "provenance": {"licence": "OGL", "provider": "WECA"}
                    },
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": [[[-2.36, 51.38], [-2.35, 51.39]], [[-2.35, 51.39], [-2.34, 51.40]]]
                    }
                },
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "bus-interchange",
                        "name": "Bath Bus Station",
                        "facility_type": "bus station",
                        "source_id": "weca-interchange-register",
                        "source_label": "WECA published interchange facilities"
                    },
                    "geometry": {"type": "Point", "coordinates": [-2.36, 51.38]}
                }
            ]
        }))
        .expect("serialize context"),
    )
    .expect("write source context");

    let added = add_bus_context(&root, &source_file).expect("add bus context");

    assert_eq!(added.route_count, 1);
    assert_eq!(added.interchange_count, 1);
    assert_eq!(added.geojson_file, "bus-context.geojson");
    assert_eq!(
        fs::read(root.join("decision-map.geojson")).expect("base GeoJSON after context"),
        original_geojson,
        "the planner's published network remains byte-for-byte unchanged"
    );
    assert_eq!(
        fs::read(root.join("decision-map.json")).expect("decision manifest after context"),
        original_decisions,
        "decision metadata remains byte-for-byte unchanged"
    );
    assert_eq!(
        fs::read(root.join("publication.json")).expect("publication manifest after context"),
        original_publication,
        "publication metadata remains byte-for-byte unchanged"
    );
    let sidecar: Value = serde_json::from_slice(
        &fs::read(root.join("bus-context.geojson")).expect("bus context sidecar"),
    )
    .expect("valid sidecar GeoJSON");
    assert_eq!(
        sidecar["features"][0]["properties"]["service_date"],
        "2026-09-23"
    );
    assert_eq!(
        sidecar["features"][0]["properties"]["provenance"]["licence"], "OGL",
        "arbitrary provenance survives as data"
    );
    assert_eq!(
        sidecar["features"][1]["properties"]["source_label"],
        "WECA published interchange facilities"
    );
    let html = fs::read_to_string(root.join("index.html")).expect("upgraded map HTML");
    assert!(html.contains("data-satn-bus-context"));
    assert!(html.contains("data-context-url=\"bus-context.geojson\""));
    let viewer = fs::read_to_string(root.join("bus-context.js")).expect("bus context viewer");
    assert!(viewer.contains("service_date"));
    assert!(viewer.contains("textContent"));
    assert!(viewer.contains("Bus route segments"));
    assert!(viewer.contains("Bus facilities and transfer points"));
    assert!(viewer.contains("Facility records:"));
    assert!(viewer.contains("Timetable-supported transfer candidates:"));
    assert!(viewer.contains("native-bus-interchange-star"));
    assert!(viewer.contains("type: 'symbol'"));
    assert!(!viewer.contains("innerHTML"));
}

#[test]
fn publication_retains_typed_urban_entry_terminal_fields() {
    let root = tempfile_root("satn-rs-publication-urban-entry");
    let mut report = report_fixture();
    let access = report
        .community_access
        .first_mut()
        .expect("community access fixture");
    access.status = "urban-entry".to_string();
    access.root_spine_id = None;
    access.joined_spine_id = None;
    access.joined_spine_reference = None;
    access.urban_entry = Some(UrbanEntry {
        destination_id: "1947201".to_string(),
        destination_name: "Bath".to_string(),
        extent_source_id: "5342409".to_string(),
        edge_id: "edge:urban-crossing".to_string(),
        fraction: 0.25,
        point: [0.75, 0.25],
    });
    let run = MidendRun {
        branch: "urban-review".to_string(),
        base_id: "base:fixture:snapshot".to_string(),
        status: "completed".to_string(),
        task_ids: vec!["task:rural:village-1".to_string()],
        operations: vec![TypedOperation::SelectCommunityAccess {
            id: "decision:rural:village-1".to_string(),
            task_id: "task:rural:village-1".to_string(),
            attempt_id: "attempt:rural:village-1".to_string(),
            community_id: "village-1".to_string(),
            candidate_id: "rural:village-1:urban-entry".to_string(),
            decision_class: "mechanical".to_string(),
            provisional: false,
            reason: None,
            uncertainties: Vec::new(),
            parent_community_id: None,
            root_spine_id: None,
            new_link_length_m: Some(120.0),
            full_access_length_m: Some(120.0),
        }],
        community_access: Vec::new(),
    };

    publish_decision_map(&root, &report, &run).expect("publish urban-entry map");
    let geojson: Value = serde_json::from_str(
        &fs::read_to_string(root.join("decision-map.geojson")).expect("GeoJSON output"),
    )
    .expect("valid GeoJSON");
    let features = geojson["features"].as_array().expect("features");
    for kind in ["community-access", "selected-alignment"] {
        let feature = features
            .iter()
            .find(|feature| feature["properties"]["kind"] == kind)
            .unwrap_or_else(|| panic!("{kind} feature"));
        assert_eq!(feature["properties"]["terminal_kind"], "urban-entry");
        assert_eq!(feature["properties"]["terminal_source_id"], "5342409");
        assert_eq!(feature["properties"]["urban_entry"]["kind"], "urban-entry");
        assert_eq!(
            feature["properties"]["urban_entry"]["destination_name"],
            "Bath"
        );
        assert_eq!(
            feature["properties"]["urban_entry"]["extent_source_id"],
            "5342409"
        );
        assert_eq!(
            feature["properties"]["urban_entry"]["crossing_edge_id"],
            "edge:urban-crossing"
        );
        assert_eq!(
            feature["properties"]["urban_entry"]["crossing_fraction"],
            0.25
        );
        assert_eq!(
            feature["properties"]["urban_entry"]["crossing_point"],
            json!([0.75, 0.25])
        );
        assert!(feature["properties"]["root_spine_id"].is_null());
    }
}

#[test]
fn publication_preserves_candidate_neighbourhood_polygon_holes_and_provenance() {
    let root = tempfile_root("satn-rs-publication-candidate-neighbourhood");
    let mut report = report_fixture();
    report.candidate_neighbourhoods = vec![CandidateNeighbourhood {
        id: "candidate-neighbourhood:extent:bath:001".to_string(),
        urban_extent_source_id: "extent:bath".to_string(),
        urban_extent_name: "Bath".to_string(),
        area_m2: 12_345.5,
        source_dataset_ids: vec!["official-classified-roads".to_string()],
        source_effective_dates: vec!["2026-08-01".to_string()],
        source_licences: vec!["Open Government Licence".to_string()],
        source_classifications: vec![
            "a-road".to_string(),
            "b-road".to_string(),
            "classified-unnumbered".to_string(),
        ],
        classified_road_frontages: vec!["a-road A4".to_string(), "b-road B3".to_string()],
        urban_edge_closes_boundary: true,
        urban_extent_source_dataset_id: Some("ons-built-up-areas-2022".to_string()),
        urban_extent_source_effective_date: Some("2022-12".to_string()),
        urban_extent_source_licence: Some("Open Government Licence v3.0".to_string()),
        urban_extent_source_url: Some("https://example.test/bua".to_string()),
        urban_extent_source_attribution: Some("ONS and OS attribution".to_string()),
        geometry: CandidateNeighbourhoodGeometry {
            geometry_type: "Polygon".to_string(),
            coordinates: vec![
                vec![
                    [-2.36, 51.38],
                    [-2.35, 51.38],
                    [-2.35, 51.39],
                    [-2.36, 51.39],
                    [-2.36, 51.38],
                ],
                vec![
                    [-2.358, 51.382],
                    [-2.357, 51.382],
                    [-2.357, 51.383],
                    [-2.358, 51.383],
                    [-2.358, 51.382],
                ],
            ],
        },
    }];
    let run = MidendRun {
        branch: "review-branch".to_string(),
        base_id: "base:fixture:snapshot".to_string(),
        status: "unresolved".to_string(),
        task_ids: Vec::new(),
        operations: Vec::new(),
        community_access: Vec::new(),
    };

    publish_decision_map(&root, &report, &run).expect("publish candidate neighbourhood map");

    let geojson: Value = serde_json::from_str(
        &fs::read_to_string(root.join("decision-map.geojson")).expect("GeoJSON output"),
    )
    .expect("valid decision GeoJSON");
    let feature = geojson["features"]
        .as_array()
        .expect("features")
        .iter()
        .find(|feature| feature["properties"]["kind"] == "candidate-neighbourhood")
        .expect("candidate neighbourhood feature");
    assert_eq!(
        feature["properties"]["candidate_neighbourhood_id"],
        "candidate-neighbourhood:extent:bath:001"
    );
    assert_eq!(feature["geometry"]["type"], "Polygon");
    assert_eq!(
        feature["geometry"]["coordinates"],
        json!([
            [
                [-2.36, 51.38],
                [-2.35, 51.38],
                [-2.35, 51.39],
                [-2.36, 51.39],
                [-2.36, 51.38]
            ],
            [
                [-2.358, 51.382],
                [-2.357, 51.382],
                [-2.357, 51.383],
                [-2.358, 51.383],
                [-2.358, 51.382]
            ]
        ])
    );
    assert_eq!(
        feature["properties"]["urban_extent_source_id"],
        "extent:bath"
    );
    assert_eq!(feature["properties"]["urban_extent_name"], "Bath");
    assert_eq!(feature["properties"]["area_m2"], 12_345.5);
    assert_eq!(
        feature["properties"]["source_dataset_ids"],
        json!(["official-classified-roads"])
    );
    assert_eq!(
        feature["properties"]["source_effective_dates"],
        json!(["2026-08-01"])
    );
    assert_eq!(
        feature["properties"]["source_licences"],
        json!(["Open Government Licence"])
    );
    assert_eq!(
        feature["properties"]["source_classifications"],
        json!(["a-road", "b-road", "classified-unnumbered"])
    );
    assert_eq!(
        feature["properties"]["classified_road_frontages"],
        json!(["a-road A4", "b-road B3"])
    );
    assert_eq!(feature["properties"]["urban_edge_closes_boundary"], true);
    assert_eq!(
        feature["properties"]["urban_extent_source_dataset_id"],
        "ons-built-up-areas-2022"
    );
    assert_eq!(
        feature["properties"]["urban_extent_source_effective_date"],
        "2022-12"
    );
    assert_eq!(
        feature["properties"]["urban_extent_source_licence"],
        "Open Government Licence v3.0"
    );
    assert_eq!(
        feature["properties"]["urban_extent_source_url"],
        "https://example.test/bua"
    );
    assert_eq!(
        feature["properties"]["urban_extent_source_attribution"],
        "ONS and OS attribution"
    );
    assert_eq!(
        feature["properties"]["interpretation"],
        "Candidate enclosure; it does not establish existing low-traffic conditions, safe crossings, or legal access."
    );
    assert!(feature["properties"].get("source_road_ids").is_none());

    let html = fs::read_to_string(root.join("index.html")).expect("HTML output");
    assert!(html.contains("data-layer-toggle=\"candidate-neighbourhood\""));
    assert!(html.contains("layer-help-candidate-neighbourhood"));
    assert!(html.contains("native-candidate-neighbourhood"));
    assert!(html.contains("native-candidate-neighbourhood-boundary"));
    assert!(html.contains("'line-color': '#000000'"));
    assert!(html.contains("'line-width': 1"));
    assert!(html.contains("'line-opacity': 1"));
    assert!(html.contains("'candidate-neighbourhood': ['native-candidate-neighbourhood', 'native-candidate-neighbourhood-boundary']"));
    assert!(html.contains("Classified-road frontages"));
    assert!(html.contains("Built-up edge closes remaining boundary"));
    assert!(html.contains("Built-up source licence"));
    let interactive_layers = html
        .split("const interactiveLayers = [")
        .nth(1)
        .expect("interactive layer list")
        .split("];")
        .next()
        .expect("interactive layer list end");
    assert!(!interactive_layers.contains("native-candidate-neighbourhood-boundary"));
    let boundary_layer_position = html
        .find("id: 'native-candidate-neighbourhood-boundary'")
        .expect("candidate boundary layer");
    assert!(boundary_layer_position < html.find("line('native-selected'").expect("route layer"));
    assert!(html.contains("native-highlight-polygon"));
    assert!(html.contains("Official source datasets"));
    assert!(html.contains("Dataset effective dates"));
    assert!(html.contains("Dataset licences"));
    assert!(html.contains("Admitted source classifications"));
    assert!(html.contains("Measured area"));
}

#[test]
fn publication_uses_accepted_rural_path_with_compact_elevation_evidence() {
    let root = tempfile_root("satn-rs-publication-rural");
    let mut report = report_fixture();
    let mut accepted = report.community_access[0].clone();
    accepted.decision_class = "agent".to_string();
    accepted.path_edge_ids = vec!["edge-flat".to_string()];
    accepted.path_geometry = vec![[0.0, 0.0], [0.0, 1.0]];
    accepted.new_link_length_m = Some(80.0);
    accepted.full_access_length_m = Some(240.0);
    accepted.reason = "A supported provisional rural access choice.".to_string();
    let profile = json!({
        "availability": "available",
        "reason": "12 governed elevation samples cover the route",
        "evidence_file": "elevation-evidence.geojson",
        "policy": {
            "evidence_tolerance_m": 5.0,
            "maximum_sample_spacing_m": 250.0,
            "minimum_sustained_spacing_m": 10.0
        },
        "evidence_refs": ["elevation:1", "elevation:2"],
        "source_refs": ["dtm-fixture"],
        "coverage": {
            "route_length_m": 240.0,
            "start_m": 0.0,
            "end_m": 240.0,
            "maximum_gap_m": 20.0,
            "sample_count": 12
        },
        "forward_ascent_m": 6.0,
        "forward_descent_m": 2.0,
        "reverse_ascent_m": 2.0,
        "reverse_descent_m": 6.0,
        "cumulative_elevation_variation_m": 8.0,
        "sustained_gradient": {
            "gradient_pct": 2.0,
            "absolute_gradient_pct": 2.0,
            "interval_length_m": 30.0,
            "evidence_refs": ["elevation:1", "elevation:2"]
        },
        "estimated_moving_time": {
            "availability": "available",
            "seconds": 37.5,
            "minutes": 0.625,
            "model": {
                "name": "BRouter Trekking v1.7.10",
                "source_commit": "fixture-brouter-commit",
                "profile_source": "fixture-profile-source",
                "solver_source": "fixture-solver-source",
                "total_mass_kg": 90.0,
                "max_speed_kmh": 45.0,
                "aero_drag_coefficient_w_s3_per_m3": 0.225,
                "rolling_resistance": 0.01,
                "biker_power_w": 100.0,
                "gravity_mps2": 9.81
            }
        },
        "moving_time_boundary_extrapolation": {
            "start_extension_m": 3.0,
            "start_local_slope": 0.02,
            "end_extension_m": 4.0,
            "end_local_slope": -0.01
        },
        "hill_neutral_moving_time": {
            "label": "Hill-neutral sensitivity (not an e-bike ETA)",
            "rationale": "Fixture flat-equivalent sensitivity",
            "source_url": "https://example.test/flat-equivalent",
            "seconds": 31.0,
            "minutes": 0.5166666667,
            "model": {
                "name": "BRouter Trekking v1.7.10",
                "source_commit": "fixture-brouter-commit",
                "profile_source": "fixture-profile-source",
                "solver_source": "fixture-solver-source",
                "total_mass_kg": 90.0,
                "max_speed_kmh": 45.0,
                "aero_drag_coefficient_w_s3_per_m3": 0.225,
                "rolling_resistance": 0.01,
                "biker_power_w": 100.0,
                "gravity_mps2": 9.81
            }
        },
        "source_resolution_m": 10.0,
        "output_sample_spacing_m": 10.0,
        "vertical_accuracy_m": 1.0
    });
    accepted.new_link_topography = serde_json::from_value(profile.clone()).expect("new profile");
    accepted.full_access_topography = serde_json::from_value(profile).expect("full profile");
    report.community_access[0].path_geometry = vec![[0.0, 0.0], [0.5, 0.0]];
    let run = MidendRun {
        branch: "rural-review".to_string(),
        base_id: "base:fixture:snapshot".to_string(),
        status: "completed".to_string(),
        task_ids: vec!["task:rural:village-1".to_string()],
        operations: vec![TypedOperation::SelectCommunityAccess {
            id: "decision:rural:village-1".to_string(),
            task_id: "task:rural:village-1".to_string(),
            attempt_id: "attempt:rural:village-1".to_string(),
            community_id: "village-1".to_string(),
            candidate_id: "rural:village-1:comfort".to_string(),
            decision_class: "agent".to_string(),
            provisional: true,
            reason: Some("A supported provisional rural access choice.".to_string()),
            uncertainties: vec!["Provision remains unknown.".to_string()],
            parent_community_id: None,
            root_spine_id: Some("source:a-road".to_string()),
            new_link_length_m: Some(80.0),
            full_access_length_m: Some(240.0),
        }],
        community_access: vec![accepted],
    };

    let publication = publish_decision_map(&root, &report, &run).expect("publish rural map");
    assert_eq!(publication.decision_count, 1);
    assert_eq!(publication.provisional_count, 1);
    let geojson: Value = serde_json::from_str(
        &fs::read_to_string(root.join("decision-map.geojson")).expect("GeoJSON output"),
    )
    .expect("valid GeoJSON");
    let features = geojson["features"].as_array().expect("features");
    let selected = features
        .iter()
        .find(|feature| feature["properties"]["kind"] == "provisional-alignment")
        .expect("provisional rural decision");
    assert_eq!(selected["properties"]["community_id"], "village-1");
    assert_eq!(selected["properties"]["access_status"], "served");
    assert_eq!(selected["properties"]["full_access_length_m"], 240.0);
    assert_eq!(
        selected["properties"]["full_access_topography"]["availability"],
        "available"
    );
    assert_eq!(
        selected["properties"]["full_access_topography"]["cumulative_elevation_variation_m"],
        8.0
    );
    assert_eq!(
        selected["properties"]["full_access_topography"]["estimated_moving_time"]["availability"],
        "available"
    );
    assert_eq!(
        selected["properties"]["full_access_topography"]["estimated_moving_time"]["minutes"],
        0.625
    );
    assert_eq!(
        selected["properties"]["full_access_topography"]["estimated_moving_time"]["model"]["name"],
        "BRouter Trekking v1.7.10"
    );
    assert_eq!(
        selected["properties"]["full_access_topography"]["moving_time_boundary_extrapolation"]["end_extension_m"],
        4.0
    );
    assert_eq!(
        selected["properties"]["full_access_topography"]["hill_neutral_moving_time"]["label"],
        "Hill-neutral sensitivity (not an e-bike ETA)"
    );
    assert_eq!(
        selected["properties"]["full_access_topography"]["hill_neutral_moving_time"]["minutes"],
        0.5166666667
    );
    assert_eq!(
        selected["geometry"]["coordinates"],
        json!([[0.0, 0.0], [0.0, 1.0]])
    );
    let community = features
        .iter()
        .find(|feature| feature["properties"]["kind"] == "community-access")
        .expect("accepted community access");
    assert_eq!(
        community["geometry"]["coordinates"],
        json!([[0.0, 0.0], [0.0, 1.0]])
    );
    let html = fs::read_to_string(root.join("index.html")).expect("map HTML");
    assert!(html.contains("New link elevation"));
    assert!(html.contains("Full journey elevation"));
    let formatter_test = root.join("native-map-formatter-test.js");
    fs::write(
        &formatter_test,
        r#"
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const start = html.indexOf('  const normalizedValue =');
const end = html.indexOf('  const rowsHtml =', start);
if (start < 0 || end < 0) throw new Error('formatter source missing');
const source = html.slice(start, end);
eval(source + `
const available = JSON.stringify({
  availability: 'available',
  coverage: { sample_count: 12 },
  cumulative_elevation_variation_m: 8,
  forward_ascent_m: 6,
  forward_descent_m: 2,
  sustained_gradient: { absolute_gradient_pct: 2 },
  reason: 'governed samples cover the route'
});
const unknown = JSON.stringify({
  availability: 'unknown',
  coverage: { sample_count: 0 },
  cumulative_elevation_variation_m: null,
  forward_ascent_m: null,
  forward_descent_m: null,
  sustained_gradient: null,
  reason: 'elevation evidence is incomplete'
});
const rows = readableRows({
  kind: 'community-access',
  name: 'Example village',
  parent_community_name: 'Parent village',
  joined_spine_reference: 'A-road',
  new_link_length_m: 120,
  full_access_length_m: 240,
  new_link_topography: available,
  full_access_topography: unknown
});
const labels = rows.map(row => row[0]);
const terrain = Object.fromEntries(rows.filter(row => row[0].includes('elevation')));
if (!terrain['New link elevation'].includes('variation 8 m') ||
    !terrain['New link elevation'].includes('sustained grade 2.0%')) process.exit(1);
if (!terrain['Full journey elevation'].includes('unknown') ||
    terrain['Full journey elevation'].includes('variation 0 m') ||
    terrain['Full journey elevation'].includes('ascent 0 m')) process.exit(1);
if (labels.some((label, index) => labels.indexOf(label) !== index)) process.exit(1);
`);
"#,
    )
    .expect("formatter test script");
    let status = Command::new("node")
        .arg(&formatter_test)
        .arg(root.join("index.html"))
        .status()
        .expect("run formatter test");
    assert!(status.success());
}

#[test]
fn identical_physical_path_with_parallel_ids_has_no_departure() {
    let root = tempfile_root("satn-rs-publication-physical-path");
    let mut report = report_fixture();
    let alternative = report
        .candidates
        .iter()
        .find(|candidate| candidate.id == "candidate:strategic")
        .expect("strategic alternative")
        .clone();
    let selected = report
        .candidates
        .iter_mut()
        .find(|candidate| candidate.id == "candidate:selected")
        .expect("selected candidate");
    selected.path_edge_ids = vec!["parallel-first".to_string(), "parallel-second".to_string()];
    selected.path_edge_geometries = alternative.path_edge_geometries.clone();
    selected.geometry = alternative.geometry.clone();
    let run = MidendRun {
        branch: "main".to_string(),
        base_id: "base:fixture:snapshot".to_string(),
        status: "completed".to_string(),
        task_ids: Vec::new(),
        operations: vec![TypedOperation::SelectAlignment {
            id: "selected".to_string(),
            task_id: "task:selected".to_string(),
            attempt_id: "attempt:selected".to_string(),
            connection_id: "connection:selected".to_string(),
            candidate_id: "candidate:selected".to_string(),
            decision_class: "classifier".to_string(),
            provisional: false,
            reason: None,
            uncertainties: Vec::new(),
        }],
        community_access: Vec::new(),
    };

    let publication = publish_decision_map(&root, &report, &run).expect("publish map");
    assert_eq!(publication.departure_count, 0);
}

#[test]
fn retained_base_reader_supports_offline_projection_without_private_history() {
    let root = tempfile_root("satn-rs-retained-base");
    let history = root.join("history");
    fs::create_dir_all(&history).expect("history directory");
    let report = report_fixture();
    let base = PlanningBase::from_report(report.clone());
    fs::write(
        history.join("base.json"),
        serde_json::to_string_pretty(&base).expect("base JSON"),
    )
    .expect("write retained base");

    let loaded = load_retained_report(&history).expect("load retained report");
    assert_eq!(loaded.area_id, report.area_id);
    assert_eq!(loaded.snapshot_id, report.snapshot_id);
    assert_eq!(loaded.candidates.len(), report.candidates.len());
}

#[test]
fn legacy_report_without_school_context_defaults_to_empty() {
    let report = report_fixture();
    let mut legacy = serde_json::to_value(report).expect("serialize report");
    let object = legacy.as_object_mut().expect("report object");
    object.remove("school_context");
    object.remove("community_access");

    let loaded: CompileReport = serde_json::from_value(legacy).expect("read legacy report");
    assert!(loaded.school_context.is_empty());
    assert!(loaded.community_access.is_empty());
}

#[test]
fn publication_manifest_exposes_pending_prepared_connections() {
    let root = tempfile_root("satn-rs-publication-pending");
    let report = report_fixture();
    let run = MidendRun {
        branch: "prefix".to_string(),
        base_id: "base:fixture:snapshot".to_string(),
        status: "replayed".to_string(),
        task_ids: vec!["task:connection:selected".to_string()],
        operations: vec![TypedOperation::SelectAlignment {
            id: "decision:selected".to_string(),
            task_id: "task:connection:selected".to_string(),
            attempt_id: "attempt:selected".to_string(),
            connection_id: "connection:selected".to_string(),
            candidate_id: "candidate:selected".to_string(),
            decision_class: "classifier".to_string(),
            provisional: false,
            reason: None,
            uncertainties: Vec::new(),
        }],
        community_access: Vec::new(),
    };
    publish_decision_map(&root, &report, &run).expect("publish prefix map");
    let publication: Value = serde_json::from_str(
        &fs::read_to_string(root.join("publication.json")).expect("publication manifest"),
    )
    .expect("valid publication manifest");
    assert_eq!(publication["status"], "reviewable-with-gaps");
    assert_eq!(publication["run_status"], "replayed");
    assert_eq!(publication["counts"]["prepared_connections"], 3);
    assert_eq!(publication["counts"]["pending_connections"], 2);
}

fn report_fixture() -> CompileReport {
    CompileReport {
        area_id: "fixture".to_string(),
        deployment_id: "fixture".to_string(),
        attribution: "Fixture attribution".to_string(),
        source_attributions: vec!["Fixture official attribution".to_string()],
        title: "Fixture".to_string(),
        snapshot_id: "snapshot".to_string(),
        source_inventory_count: 2,
        unknown_fact_count: 2,
        connection_count: 3,
        candidate_count: 4,
        operation_count: 4,
        boundary_scope: None,
        source_inventory: vec![
            SourceCorridor {
                id: "source:a-road".to_string(),
                reference: "A-road".to_string(),
                source_kind: "context".to_string(),
                source_id: "a-road-source".to_string(),
                scope: "governed".to_string(),
                baseline_role: "a-road".to_string(),
                source_edge_ids: vec!["a-road-section-1".to_string()],
                graph_edge_ids: vec![
                    "alternative-edge-1".to_string(),
                    "alternative-edge-2".to_string(),
                ],
                geometry: vec![vec![[0.0, 0.0], [0.5, 0.0]], vec![[0.5, 0.0], [1.0, 0.0]]],
                topology_status: "graph-bound".to_string(),
                attachment_status: "graph-edge".to_string(),
                provision_status: "unknown".to_string(),
            },
            SourceCorridor {
                id: "source:cycleway".to_string(),
                reference: "current NCN".to_string(),
                source_kind: "context".to_string(),
                source_id: "ncn-source".to_string(),
                scope: "governed".to_string(),
                baseline_role: "current-ncn".to_string(),
                source_edge_ids: vec!["ncn-section-1".to_string()],
                graph_edge_ids: vec![
                    "alternative-edge-1".to_string(),
                    "alternative-edge-2".to_string(),
                ],
                geometry: vec![vec![[0.0, 0.0], [1.0, 0.0]]],
                topology_status: "graph-bound".to_string(),
                attachment_status: "graph-edge".to_string(),
                provision_status: "unknown".to_string(),
            },
            SourceCorridor {
                id: "source:unattached".to_string(),
                reference: "A-road".to_string(),
                source_kind: "context".to_string(),
                source_id: "unattached".to_string(),
                scope: "unknown".to_string(),
                baseline_role: "a-road".to_string(),
                source_edge_ids: vec!["source-only".to_string()],
                graph_edge_ids: Vec::new(),
                geometry: vec![vec![[2.0, 0.0], [3.0, 0.0]]],
                topology_status: "source-only".to_string(),
                attachment_status: "unknown".to_string(),
                provision_status: "unknown".to_string(),
            },
        ],
        unknown_facts: vec![
            UnknownFact {
                id: "unknown:a-road".to_string(),
                subject: "source:a-road".to_string(),
                status: "unknown".to_string(),
                reason: "Provision is unknown.".to_string(),
            },
            UnknownFact {
                id: "unknown:unattached".to_string(),
                subject: "source:unattached".to_string(),
                status: "unknown".to_string(),
                reason: "Topology is unknown.".to_string(),
            },
        ],
        network_places: vec![NetworkPlace {
            id: "alpha".to_string(),
            name: "Alpha".to_string(),
            source_id: "alpha".to_string(),
            place_class: "town".to_string(),
            geometry: [0.0, 0.0],
        }],
        school_context: vec![SchoolContext {
            id: "school-1".to_string(),
            source_id: "school-source-1".to_string(),
            name: "Alpha School".to_string(),
            school_obligation_eligible: false,
            geometry: [0.5, 0.0],
        }],
        community_access: vec![CommunityAccess {
            community_id: "village-1".to_string(),
            source_id: "village-1".to_string(),
            name: "Village One".to_string(),
            geometry: [0.0, 0.0],
            status: "served".to_string(),
            decision_class: "mechanical".to_string(),
            is_primary: true,
            attachment_node: Some("node-village".to_string()),
            attachment_edge_id: Some("edge-access".to_string()),
            attachment_point: Some([0.0, 0.0]),
            attachment_fraction: Some(0.0),
            attachment_distance_m: Some(12.0),
            parent_community_id: None,
            parent_community_name: None,
            parent_junction_node: None,
            parent_junction_edge_id: None,
            parent_junction_fraction: None,
            parent_junction_remaining_m: None,
            root_spine_id: Some("source:a-road".to_string()),
            admission_order: Some(1),
            attachment_depth: Some(0),
            new_link_length_m: Some(120.0),
            full_access_length_m: Some(120.0),
            joined_spine_id: Some("source:a-road".to_string()),
            urban_entry: None,
            access_length_m: Some(120.0),
            path_edge_ids: vec!["edge-access".to_string()],
            path_start_fraction: Some(0.0),
            path_end_fraction: Some(1.0),
            path_geometry: vec![[0.0, 0.0], [0.5, 0.0]],
            onward_destinations: Vec::new(),
            onward_benefits: Vec::new(),
            joined_spine_reference: Some("A-road".to_string()),
            provision_status: "unknown".to_string(),
            reason: "Shortest measured-length cycling path reaches the admitted strategic spine."
                .to_string(),
            new_link_topography: None,
            full_access_topography: None,
        }],
        access_obligations: vec![AccessObligation {
            id: "obligation:community:alpha".to_string(),
            kind: "community".to_string(),
            source_id: "alpha".to_string(),
            name: "Alpha".to_string(),
            geometry: None,
            access_point_status: None,
            access_point_source_id: None,
            access_point_rationale: None,
            disposition: "unresolved".to_string(),
            reason: "No selected support.".to_string(),
        }],
        destination_profile: "unconfigured".to_string(),
        accounting: AccountingSummary {
            status: "reviewable-with-gaps".to_string(),
            complete: false,
            source_baseline_count: 2,
            network_place_count: 1,
            obligation_count: 1,
            unresolved_count: 3,
            network_gap_count: 0,
        },
        connections: vec![
            connection("connection:selected", "selected"),
            connection("connection:provisional", "provisional"),
            connection("connection:unresolved", "unresolved"),
        ],
        candidates: vec![
            candidate(
                "candidate:selected",
                "connection:selected",
                "direct",
                vec!["selected-edge"],
            ),
            candidate(
                "candidate:strategic",
                "connection:selected",
                "strategic-spine",
                vec!["alternative-edge-1", "alternative-edge-2"],
            ),
            candidate(
                "candidate:provisional",
                "connection:provisional",
                "low-traffic",
                vec!["provisional-edge"],
            ),
            candidate(
                "candidate:unresolved",
                "connection:unresolved",
                "direct",
                vec!["unresolved-edge"],
            ),
        ],
        candidate_neighbourhoods: Vec::new(),
        operations: Vec::new(),
    }
}

fn connection(id: &str, suffix: &str) -> Connection {
    Connection {
        id: id.to_string(),
        origin_place_id: "alpha".to_string(),
        origin_name: "Alpha".to_string(),
        destination_place_id: format!("{suffix}-destination"),
        destination_name: format!("{suffix} destination"),
        origin_node: "origin".to_string(),
        destination_node: format!("{suffix}-node"),
        cross_region_edge_ids: Vec::new(),
        road_classes: vec!["a-road-reference".to_string()],
        preferred_classes: vec!["a-road-reference".to_string()],
    }
}

fn candidate(id: &str, connection_id: &str, role: &str, edge_ids: Vec<&str>) -> Candidate {
    let path_edge_ids = edge_ids.into_iter().map(str::to_string).collect::<Vec<_>>();
    let path_edge_geometries = path_edge_ids
        .iter()
        .enumerate()
        .map(|(index, _)| vec![[index as f64, 0.0], [(index + 1) as f64, 0.0]])
        .collect();
    Candidate {
        id: id.to_string(),
        connection_id: connection_id.to_string(),
        status: "mechanical-candidate".to_string(),
        decision_class: "mechanical".to_string(),
        role: role.to_string(),
        role_aliases: Vec::new(),
        length_m: 100.0,
        search_cost_m: 100.0,
        a_road_share: 1.0,
        ncn_share: 0.0,
        cycle_alignment_bases: Vec::new(),
        topology_status: "graph-supported".to_string(),
        provision_status: "unknown".to_string(),
        path_edge_ids,
        path_edge_geometries,
        geometry: vec![[0.0, 0.0], [1.0, 0.0]],
    }
}

fn tempfile_root(label: &str) -> std::path::PathBuf {
    let root = std::env::temp_dir().join(format!("{label}-{}", std::process::id()));
    if root.exists() {
        fs::remove_dir_all(&root).expect("clean fixture root");
    }
    root
}
