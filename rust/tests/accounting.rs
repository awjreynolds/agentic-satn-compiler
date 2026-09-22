use std::fs;

use satn_rs::{CompileOptions, compile};
use serde_json::{Value, json};

#[test]
fn accounts_strategic_baseline_places_and_school_gaps() {
    let root = tempfile_root("satn-rs-accounting");
    let snapshot = root.join("snapshot");
    fs::create_dir_all(&snapshot).expect("snapshot directory");
    fs::write(
        snapshot.join("snapshot.json"),
        r#"{"attribution":"OSM and NCN attribution","evidence_sources":{"official_road_classification":{"attribution":"Official roads attribution"}}}"#,
    )
    .expect("snapshot metadata");
    fs::write(
        root.join("area.yaml"),
        format!(
            "area_id: fixture\narea_name: Fixture\nsource:\n  snapshot_dir: {}\n  snapshot_id: snapshot\n  community_place_types: [town, village]\ncompilation:\n  max_connection_km: 15\n",
            root.display()
        ),
    )
    .expect("area config");

    write_collection(
        &snapshot.join("network.geojson"),
        vec![
            edge(
                "a",
                "b",
                [0.0, 0.0],
                [1.0, 0.0],
                100.0,
                Some("A1"),
                "primary",
            ),
            edge(
                "b",
                "a",
                [1.0, 0.0],
                [0.0, 0.0],
                100.0,
                Some("A1"),
                "primary",
            ),
            edge("b", "c", [1.0, 0.0], [2.0, 0.0], 100.0, None, "cycleway"),
            edge("c", "b", [2.0, 0.0], [1.0, 0.0], 100.0, None, "cycleway"),
        ],
    );
    write_collection(
        &snapshot.join("places.geojson"),
        vec![
            place("community-a", "Alpha", [0.0, 0.0]),
            place("community-b", "Beta", [2.0, 0.0]),
            place_with_eligibility("community-c", "Gamma", [1.0, 0.0], "village", false),
        ],
    );
    write_collection(
        &snapshot.join("context.geojson"),
        vec![
            context_line("ncn-route", "ncn-route-1", "NCN 1", [0.0, 0.0], [1.0, 0.0]),
            context_line("ncn-link", "ncn-link-1", "NCN link", [0.0, 0.0], [1.0, 0.0]),
            context_line(
                "declassified-ncn-route",
                "former-ncn-1",
                "Former NCN",
                [0.0, 0.0],
                [1.0, 0.0],
            ),
            context_line(
                "greenway-cycleway",
                "greenway-1",
                "Greenway",
                [1.0, 0.0],
                [2.0, 0.0],
            ),
            context_line(
                "circulation-boundary",
                "railway-1",
                "Wessex Main Line",
                [9.0, 9.0],
                [9.1, 9.0],
            ),
            context_line(
                "former-railway",
                "former-railway-1",
                "Former Railway",
                [9.2, 9.0],
                [9.3, 9.0],
            ),
            json!({
                "type":"Feature",
                "properties": {
                    "feature_type":"school",
                    "evidence_id":"school-1",
                    "source_id":"school-source-1",
                    "name":"Alpha School",
                    "school_obligation_eligible":"False",
                    "access_point_status":"unresolved",
                    "access_point_rationale":"Entrance evidence is unresolved."
                },
                "geometry":{"type":"Point","coordinates":[0.5,0.0]}
            }),
        ],
    );

    let report = compile(
        &root.join("area.yaml"),
        &root.join("output"),
        CompileOptions::default(),
    )
    .expect("accounting fixture compiles");

    let roles: std::collections::BTreeSet<_> = report
        .source_inventory
        .iter()
        .map(|source| source.baseline_role.as_str())
        .collect();
    assert!(roles.contains("a-road"));
    assert!(roles.contains("existing-cycleway"));
    assert!(roles.contains("current-ncn"));
    assert!(roles.contains("ncn-link"));
    assert!(roles.contains("declassified-ncn"));
    assert!(roles.contains("greenway-cycleway"));
    assert!(roles.contains("railway"));
    assert!(roles.contains("former-railway"));
    let railway = report
        .source_inventory
        .iter()
        .find(|source| source.source_id == "railway-1")
        .expect("railway context");
    assert_eq!(railway.baseline_role, "railway");
    assert_eq!(railway.provision_status, "unknown");
    assert_eq!(railway.attachment_status, "unknown");
    assert!(railway.graph_edge_ids.is_empty());
    let former_railway = report
        .source_inventory
        .iter()
        .find(|source| source.source_id == "former-railway-1")
        .expect("explicit former railway context");
    assert_eq!(former_railway.baseline_role, "former-railway");

    assert_eq!(report.network_places.len(), 3);
    assert!(
        report
            .network_places
            .iter()
            .any(|place| place.id == "community-c")
    );
    assert_eq!(report.school_context.len(), 1);
    let school = report.school_context.first().expect("school context");
    assert_eq!(school.id, "school-1");
    assert_eq!(school.source_id, "school-source-1");
    assert_eq!(school.name, "Alpha School");
    assert!(!school.school_obligation_eligible);
    assert_eq!(report.access_obligations.len(), 1);
    assert_eq!(
        report
            .access_obligations
            .iter()
            .filter(|obligation| obligation.kind == "school")
            .count(),
        0
    );
    assert!(
        report
            .access_obligations
            .iter()
            .any(|obligation| obligation.id == "obligation:community:community-c")
    );
    assert!(
        !report
            .access_obligations
            .iter()
            .any(|obligation| obligation.id == "obligation:community:community-a")
    );
    assert_eq!(report.destination_profile, "unconfigured");
    assert_eq!(report.attribution, "OSM and NCN attribution");
    assert_eq!(
        report.source_attributions,
        vec!["Official roads attribution".to_string()]
    );
    assert_eq!(report.accounting.status, "reviewable-with-gaps");
    assert!(!report.accounting.complete);
    assert_eq!(report.accounting.network_gap_count, 0);

    let network = fs::read_to_string(root.join("output/network.geojson")).expect("network output");
    assert!(network.contains("source-baseline"));
    assert!(network.contains("source_geometry_part"));
    assert!(!network.contains("source_edge_ids"));
    assert!(network.contains("community-access"));
    assert!(!network.contains("access-obligation"));
    assert!(network.contains("school-context"));
    assert!(network.contains("network-place"));
    let network_json: Value = serde_json::from_str(&network).expect("valid GeoJSON");
    let community_feature = network_json["features"]
        .as_array()
        .expect("features")
        .iter()
        .find(|feature| feature["properties"]["kind"] == "community-access")
        .expect("community access feature");
    assert_eq!(community_feature["properties"]["status"], "on-spine");
    assert_eq!(community_feature["geometry"]["type"], "Point");
    assert!(community_feature["geometry"]["coordinates"].is_array());
    assert!(
        !network_json["features"]
            .as_array()
            .expect("features")
            .iter()
            .any(|feature| feature["properties"]["kind"] == "access-obligation")
    );
    let school_feature = network_json["features"]
        .as_array()
        .expect("features")
        .iter()
        .find(|feature| feature["properties"]["kind"] == "school-context")
        .expect("school context feature");
    assert_eq!(school_feature["properties"]["source_id"], "school-source-1");
    assert_eq!(school_feature["properties"]["name"], "Alpha School");
    assert_eq!(school_feature["geometry"]["type"], "Point");
    let map = fs::read_to_string(root.join("output/index.html")).expect("review map");
    assert!(map.contains("Accounting: reviewable-with-gaps"));
    assert!(map.contains("Network place"));
    assert!(map.contains("Access obligation"));
    assert!(map.contains("School context"));
    assert!(map.contains("class=\"place\""));
    assert!(map.contains("class=\"community-access\""));
}

fn edge(
    from: &str,
    to: &str,
    start: [f64; 2],
    end: [f64; 2],
    length: f64,
    reference: Option<&str>,
    highway: &str,
) -> Value {
    let mut properties = json!({"u":from,"v":to,"key":0,"length":length,"highway":highway});
    if let Some(reference) = reference {
        properties["ref"] = json!(reference);
    }
    json!({"type":"Feature","properties":properties,"geometry":{"type":"LineString","coordinates":[start,end]}})
}

fn place(id: &str, name: &str, point: [f64; 2]) -> Value {
    place_with_eligibility(id, name, point, "town", true)
}

fn place_with_eligibility(
    id: &str,
    name: &str,
    point: [f64; 2],
    place_class: &str,
    eligible: bool,
) -> Value {
    json!({"type":"Feature","properties":{"place_id":id,"source_id":id,"name":name,"place_class":place_class,"urban_circulation_eligible":eligible.to_string()},"geometry":{"type":"Point","coordinates":point}})
}

fn context_line(
    feature_type: &str,
    evidence_id: &str,
    name: &str,
    start: [f64; 2],
    end: [f64; 2],
) -> Value {
    let category = if feature_type == "circulation-boundary" {
        "railway"
    } else {
        ""
    };
    json!({"type":"Feature","properties":{"feature_type":feature_type,"category":category,"evidence_id":evidence_id,"source_id":evidence_id,"name":name},"geometry":{"type":"LineString","coordinates":[start,end]}})
}

fn write_collection(path: &std::path::Path, features: Vec<Value>) {
    fs::write(
        path,
        serde_json::to_string(&json!({"type":"FeatureCollection","features":features}))
            .expect("feature collection"),
    )
    .expect("write feature collection");
}

fn tempfile_root(label: &str) -> std::path::PathBuf {
    let root = std::env::temp_dir().join(format!("{label}-{}", std::process::id()));
    if root.exists() {
        fs::remove_dir_all(&root).expect("clean fixture root");
    }
    root
}
