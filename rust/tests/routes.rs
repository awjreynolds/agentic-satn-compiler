use std::fs;

use satn_rs::{CompileOptions, compile, project_wgs84_to_bng};
use serde_json::{Value, json};

#[test]
fn gridless_bng_projection_matches_local_reference_fixture() {
    for (point, expected) in [
        ([-2.359, 51.381], [375_111_867, 164_722_399]),
        ([-2.5, 51.5], [365_389_338, 178_014_436]),
        ([-0.0321, 50.8], [538_774_501, 101_894_236]),
    ] {
        let projected = project_wgs84_to_bng(point).expect("projection");
        assert_eq!(round_millimetres(projected[0]), expected[0]);
        assert_eq!(round_millimetres(projected[1]), expected[1]);
    }
}

#[test]
fn raw_ncn_only_yes_establishes_current_ncn_evidence() {
    let root = tempfile_root("satn-rs-raw-ncn");
    let snapshot = root.join("snapshot");
    fs::create_dir_all(&snapshot).expect("snapshot directory");
    fs::write(
        root.join("area.yaml"),
        format!(
            "area_id: fixture\narea_name: Fixture\nsource:\n  snapshot_dir: {}\n  snapshot_id: snapshot\n",
            root.display()
        ),
    )
    .expect("area config");
    let start = [-2.36, 51.38];
    let destination = [-2.35, 51.38];
    let mut edges = Vec::new();
    add_bidirectional_path(
        &mut edges,
        "s",
        "t",
        start,
        destination,
        1_400.0,
        "residential",
        None,
    );
    for edge in &mut edges {
        edge["properties"]["ncn"] = json!("true");
    }
    write_collection(&snapshot.join("network.geojson"), edges);
    write_collection(
        &snapshot.join("places.geojson"),
        vec![
            json!({
                "type":"Feature",
                "properties":{"place_id":"alpha","name":"Alpha","place_class":"town"},
                "geometry":{"type":"Point","coordinates":start},
            }),
            json!({
                "type":"Feature",
                "properties":{"place_id":"beta","name":"Beta","place_class":"town"},
                "geometry":{"type":"Point","coordinates":destination},
            }),
        ],
    );

    let report = compile(
        &root.join("area.yaml"),
        &root.join("output"),
        CompileOptions::default(),
    )
    .expect("raw ncn fixture compiles");
    let direct = report
        .candidates
        .iter()
        .find(|candidate| candidate.role == "direct")
        .expect("direct candidate");
    assert_eq!(direct.ncn_share, 0.0);
    assert!(direct.cycle_alignment_bases.is_empty());
}

#[test]
fn route_roles_produce_distinct_source_supported_alternatives() {
    let root = tempfile_root("satn-rs-route-roles");
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

    let start = [-2.36, 51.38];
    let destination = [-2.35, 51.38];
    let direct = [-2.355, 51.381];
    let strategic = [-2.355, 51.383];
    let ncn = [-2.355, 51.377];
    let quiet = [-2.355, 51.385];
    let mut edges = Vec::new();
    add_bidirectional_path(&mut edges, "s", "d", start, direct, 100.0, "primary", None);
    add_bidirectional_path(
        &mut edges,
        "d",
        "t",
        direct,
        destination,
        100.0,
        "primary",
        None,
    );
    add_bidirectional_path(
        &mut edges,
        "s",
        "a",
        start,
        strategic,
        150.0,
        "primary",
        Some("['A4', 'A36']"),
    );
    add_bidirectional_path(
        &mut edges,
        "a",
        "t",
        strategic,
        destination,
        150.0,
        "primary",
        Some("['A4', 'A36']"),
    );
    add_bidirectional_path(&mut edges, "s", "n", start, ncn, 130.0, "track", None);
    add_bidirectional_path(&mut edges, "n", "t", ncn, destination, 130.0, "track", None);
    add_bidirectional_path(&mut edges, "s", "l", start, quiet, 110.0, "cycleway", None);
    add_bidirectional_path(
        &mut edges,
        "l",
        "t",
        quiet,
        destination,
        110.0,
        "cycleway",
        None,
    );
    write_collection(&snapshot.join("network.geojson"), edges);
    write_collection(
        &snapshot.join("places.geojson"),
        vec![
            json!({
                "type":"Feature",
                "properties":{"place_id":"alpha","name":"Alpha","place_class":"town"},
                "geometry":{"type":"Point","coordinates":start},
            }),
            json!({
                "type":"Feature",
                "properties":{"place_id":"beta","name":"Beta","place_class":"town"},
                "geometry":{"type":"Point","coordinates":destination},
            }),
        ],
    );
    write_collection(
        &snapshot.join("context.geojson"),
        vec![json!({
            "type":"Feature",
            "properties":{"feature_type":"ncn-route","evidence_id":"ncn-fixture"},
            "geometry":{"type":"LineString","coordinates":[start,ncn,destination]},
        })],
    );

    let report = compile(
        &root.join("area.yaml"),
        &root.join("output"),
        CompileOptions::default(),
    )
    .expect("route fixture compiles");

    assert_eq!(report.connection_count, 1);
    assert_eq!(report.candidate_count, 4);
    assert!(
        report
            .candidates
            .iter()
            .all(|candidate| candidate.path_edge_geometries.len() == candidate.path_edge_ids.len())
    );
    let direct = report
        .candidates
        .iter()
        .find(|candidate| candidate.role == "direct")
        .expect("direct role");
    assert_eq!(direct.path_edge_ids.len(), 2);
    assert_eq!(direct.length_m, 200.0);
    assert_eq!(direct.search_cost_m, 200.0);
    let strategic = report
        .candidates
        .iter()
        .find(|candidate| candidate.role == "strategic-spine")
        .expect("strategic role");
    assert_eq!(strategic.length_m, 300.0);
    assert_eq!(strategic.search_cost_m, 105.0);
    assert_eq!(strategic.a_road_share, 1.0);
    let ncn = report
        .candidates
        .iter()
        .find(|candidate| candidate.role == "ncn-informed")
        .expect("ncn role");
    assert_eq!(ncn.length_m, 260.0);
    assert_eq!(ncn.search_cost_m, 104.0);
    assert_eq!(ncn.ncn_share, 1.0);
    assert_eq!(ncn.cycle_alignment_bases, vec!["current-ncn"]);
    let quiet = report
        .candidates
        .iter()
        .find(|candidate| candidate.role == "low-traffic")
        .expect("low-traffic role");
    assert_eq!(quiet.length_m, 220.0);
    assert_eq!(quiet.search_cost_m, 165.0);
    assert_eq!(quiet.provision_status, "unknown");
    assert!(
        report
            .candidates
            .iter()
            .all(|candidate| candidate.role_aliases.is_empty())
    );
    assert!(report.candidates.iter().all(|candidate| {
        candidate
            .path_edge_ids
            .windows(2)
            .all(|pair| pair[0] != pair[1])
    }));
}

fn add_bidirectional_path(
    edges: &mut Vec<Value>,
    from: &str,
    to: &str,
    start: [f64; 2],
    end: [f64; 2],
    length: f64,
    highway: &str,
    reference: Option<&str>,
) {
    for (u, v, coordinates) in [
        (from, to, json!([start, end])),
        (to, from, json!([end, start])),
    ] {
        let mut properties = json!({
            "u":u,
            "v":v,
            "key":0,
            "length":length,
            "highway":highway,
        });
        if let Some(reference) = reference {
            properties["ref"] = json!(reference);
        }
        edges.push(json!({
            "type":"Feature",
            "properties":properties,
            "geometry":{"type":"LineString","coordinates":coordinates},
        }));
    }
}

fn write_collection(path: &std::path::Path, features: Vec<Value>) {
    fs::write(
        path,
        serde_json::to_string(&json!({"type":"FeatureCollection","features":features}))
            .expect("collection json"),
    )
    .expect("collection");
}

fn round_millimetres(value: f64) -> i64 {
    (value * 1_000.0).round() as i64
}

fn tempfile_root(label: &str) -> std::path::PathBuf {
    let root = std::env::temp_dir().join(format!("{label}-{}", std::process::id()));
    if root.exists() {
        fs::remove_dir_all(&root).expect("clean fixture root");
    }
    root
}
