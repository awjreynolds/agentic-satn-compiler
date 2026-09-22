use std::fs;

use satn_rs::midend::{MidendRun, PlanningBase, TypedOperation};
use satn_rs::{
    AccessObligation, AccountingSummary, Candidate, CompileReport, Connection, NetworkPlace,
    SourceCorridor, UnknownFact, load_retained_report, publish_decision_map,
};
use serde_json::Value;

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
    assert!(html.contains("Context sources"));
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
    assert!(html.contains("native-feature-details"));
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
