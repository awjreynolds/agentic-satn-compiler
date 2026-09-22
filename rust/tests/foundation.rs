use std::fs;

use satn_rs::{CompileOptions, compile_with_progress};

#[test]
fn compiles_fixture_area_to_small_review_bundle() {
    let root = tempfile_root("satn-rs-foundation");
    let snapshot = root.join("snapshot");
    fs::create_dir_all(&snapshot).expect("snapshot directory");
    fs::write(
        root.join("area.yaml"),
        format!(
            "area_id: fixture\narea_name: Fixture\ndeployment_id: fixture\nsource:\n  snapshot_dir: {}\n  snapshot_id: snapshot\n  network_type: bike\n  urban_place_types: [town]\ncompilation:\n  max_connection_km: 15\n",
            root.display()
        ),
    )
    .expect("area config");
    fs::write(
        snapshot.join("network.geojson"),
        r#"{"type":"FeatureCollection","features":[
          {"type":"Feature","properties":{"u":"a","v":"b","key":0,"length":20000.0,"ref":"A1"},"geometry":{"type":"LineString","coordinates":[[0.0,0.0],[1.0,0.0]]}},
          {"type":"Feature","properties":{"u":"b","v":"a","key":0,"length":20000.0,"ref":"A1"},"geometry":{"type":"LineString","coordinates":[[1.0,0.0],[0.0,0.0]]}}
        ]}"#,
    )
    .expect("network");
    fs::write(
        snapshot.join("places.geojson"),
        r#"{"type":"FeatureCollection","features":[
          {"type":"Feature","properties":{"place_id":"alpha","name":"Alpha","place_class":"town","urban_circulation_eligible":"True"},"geometry":{"type":"Point","coordinates":[0.0,0.0]}},
          {"type":"Feature","properties":{"place_id":"beta","name":"Beta","place_class":"town","urban_circulation_eligible":"True"},"geometry":{"type":"Point","coordinates":[0.9,0.0]}}
        ]}"#,
    )
    .expect("places");

    fs::write(
        snapshot.join("context.geojson"),
        r#"{"type":"FeatureCollection","features":[
          {"type":"Feature","properties":{"feature_type":"a-road-spine","evidence_id":"context-a1","name":"A1","network_scope":"urban"},"geometry":{"type":"MultiLineString","coordinates":[[[0.0,0.0],[1.0,0.0]],[[0.0,0.02],[1.0,0.02]]] }},
          {"type":"Feature","properties":{"feature_type":"a-road-spine","evidence_id":"context-unattached","name":"A1","network_scope":"urban"},"geometry":{"type":"LineString","coordinates":[[2.0,2.0],[2.1,2.0]]}}
        ]}"#,
    )
    .expect("context");
    fs::write(
        snapshot.join("official-road-classification.geojson"),
        r#"{"type":"FeatureCollection","features":[
          {"type":"Feature","properties":{"official_classification":"a-road","official_road_number":"A1","official_feature_id":"official-a1"},"geometry":{"type":"LineString","coordinates":[[0.0,0.0],[1.0,0.0]]}}
        ]}"#,
    )
    .expect("official road classification");
    fs::write(
        snapshot.join("boundary.geojson"),
        r#"{"type":"FeatureCollection","features":[
          {"type":"Feature","properties":{"osm_id":"fixture-boundary","name":"Fixture"},"geometry":{"type":"MultiPolygon","coordinates":[[[[0.0,-0.1],[1.0,-0.1],[1.0,0.1],[0.0,0.1],[0.0,-0.1]],[[0.2,0.02],[0.3,0.02],[0.3,0.08],[0.2,0.08],[0.2,0.02]]],[[[2.0,-0.1],[2.2,-0.1],[2.2,0.1],[2.0,0.1],[2.0,-0.1]]]]}}
        ]}"#,
    )
    .expect("boundary");

    let output = root.join("output");
    let mut stages = Vec::new();
    let report = compile_with_progress(
        &root.join("area.yaml"),
        &output,
        CompileOptions::default(),
        &mut |event| stages.push(event.stage),
    )
    .expect("fixture compiles");

    // Multipart and source-only A-road rows must remain individually represented.
    assert_eq!(report.source_inventory_count, 4);
    assert_eq!(report.connection_count, 1);
    assert_eq!(report.candidate_count, 1);
    assert_eq!(report.candidates[0].path_edge_ids.len(), 1);
    assert_eq!(
        report.candidates[0].path_edge_geometries.len(),
        report.candidates[0].path_edge_ids.len()
    );
    assert!(report.candidates[0].length_m > 15_000.0);
    assert_eq!(report.unknown_fact_count, 6);
    let context = report
        .source_inventory
        .iter()
        .find(|corridor| corridor.source_id == "context-a1")
        .expect("multipart context source");
    assert_eq!(context.geometry.len(), 2);
    assert_eq!(context.source_edge_ids.len(), 2);
    assert_eq!(context.attachment_status, "partial");
    let unattached = report
        .source_inventory
        .iter()
        .find(|corridor| corridor.source_id == "context-unattached")
        .expect("source-only context source");
    assert_eq!(unattached.topology_status, "source-only");
    assert_eq!(unattached.attachment_status, "unknown");
    assert!(report.unknown_facts.iter().any(|fact| {
        fact.subject == unattached.id && fact.reason.contains("topology remains unknown")
    }));
    assert!(stages.iter().any(|stage| stage == "preparation"));
    assert!(stages.iter().any(|stage| stage == "mechanical"));
    assert!(stages.iter().any(|stage| stage == "publication"));
    assert!(stages.iter().any(|stage| stage == "completed"));
    assert!(report.boundary_scope.is_some());
    assert!(output.join("summary.json").is_file());
    assert!(output.join("network.geojson").is_file());
    assert!(output.join("index.html").is_file());
    let summary = fs::read_to_string(output.join("summary.json")).expect("summary");
    assert!(summary.contains("mechanical-candidate"));
    assert!(summary.contains("generate-candidate"));
    assert!(summary.contains("topology_status"));
    let network = fs::read_to_string(output.join("network.geojson")).expect("network output");
    assert!(network.contains("MultiPolygon"));
    assert!(network.contains("0.2"));
    let map = fs::read_to_string(output.join("index.html")).expect("map");
    assert!(map.contains("<svg"));
    assert!(map.contains("class=\"candidate\""));
    assert!(map.contains("class=\"boundary\""));
    assert!(map.contains("fill-rule=\"evenodd\""));
    assert!(!summary.contains("select-alignment"));
}

#[test]
fn compiles_all_authority_boundary_features_and_places() {
    let root = tempfile_root("satn-rs-multi-authority-boundary");
    let snapshot = root.join("snapshot");
    fs::create_dir_all(&snapshot).expect("snapshot directory");
    fs::write(
        root.join("area.yaml"),
        format!(
            "area_id: multi-authority\narea_name: Multi Authority\ndeployment_id: fixture\nsource:\n  snapshot_dir: {}\n  snapshot_id: snapshot\n  network_type: bike\n  urban_place_types: [town]\ncompilation:\n  max_connection_km: 15\n",
            root.display()
        ),
    )
    .expect("area config");
    fs::write(
        snapshot.join("network.geojson"),
        r#"{"type":"FeatureCollection","features":[
          {"type":"Feature","properties":{"u":"a","v":"b","key":0,"length":3000.0,"ref":"A1"},"geometry":{"type":"LineString","coordinates":[[0.0,0.0],[3.0,0.0]]}},
          {"type":"Feature","properties":{"u":"b","v":"a","key":0,"length":3000.0,"ref":"A1"},"geometry":{"type":"LineString","coordinates":[[3.0,0.0],[0.0,0.0]]}}
        ]}"#,
    )
    .expect("network");
    fs::write(
        snapshot.join("places.geojson"),
        r#"{"type":"FeatureCollection","features":[
          {"type":"Feature","properties":{"place_id":"alpha","name":"Alpha","place_class":"town"},"geometry":{"type":"Point","coordinates":[0.0,0.0]}},
          {"type":"Feature","properties":{"place_id":"beta","name":"Beta","place_class":"town"},"geometry":{"type":"Point","coordinates":[3.0,0.0]}}
        ]}"#,
    )
    .expect("places");
    fs::write(
        snapshot.join("boundary.geojson"),
        r#"{"type":"FeatureCollection","features":[
          {"type":"Feature","properties":{"boundary_id":"authority-a","name":"Authority A"},"geometry":{"type":"Polygon","coordinates":[[[ -1.0,-1.0],[1.0,-1.0],[1.0,1.0],[-1.0,1.0],[-1.0,-1.0]],[[ -0.5,-0.5],[0.0,-0.5],[0.0,0.0],[-0.5,0.0],[-0.5,-0.5]]]}},
          {"type":"Feature","properties":{"boundary_id":"authority-b","name":"Authority B"},"geometry":{"type":"MultiPolygon","coordinates":[[[[2.0,-1.0],[4.0,-1.0],[4.0,1.0],[2.0,1.0],[2.0,-1.0]],[[2.2,-0.2],[2.5,-0.2],[2.5,0.2],[2.2,0.2],[2.2,-0.2]]],[[[5.0,-1.0],[6.0,-1.0],[6.0,1.0],[5.0,1.0],[5.0,-1.0]]]]}}
        ]}"#,
    )
    .expect("boundary");

    let report = compile_with_progress(
        &root.join("area.yaml"),
        &root.join("output"),
        CompileOptions::default(),
        &mut |_event| {},
    )
    .expect("fixture compiles");

    let boundary = report.boundary_scope.expect("combined boundary");
    assert_eq!(boundary.id, "authority-a+authority-b");
    assert_eq!(boundary.name, "Authority A + Authority B");
    assert_eq!(boundary.geometry.len(), 3);
    assert_eq!(boundary.geometry[0].len(), 2);
    assert_eq!(boundary.geometry[1].len(), 2);
    assert_eq!(boundary.geometry[2].len(), 1);
    assert_eq!(
        report
            .network_places
            .iter()
            .map(|place| place.id.as_str())
            .collect::<Vec<_>>(),
        vec!["alpha", "beta"]
    );
}

fn tempfile_root(label: &str) -> std::path::PathBuf {
    let root = std::env::temp_dir().join(format!("{label}-{}", std::process::id()));
    if root.exists() {
        fs::remove_dir_all(&root).expect("clean fixture root");
    }
    root
}
