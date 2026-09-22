use std::fs;

use satn_rs::{CompileOptions, compile};
use serde_json::{Value, json};

#[test]
fn rural_access_uses_measured_graph_paths_and_exposes_gaps_without_routing_schools() {
    let root = tempfile_root("satn-rs-rural-access");
    let snapshot = root.join("snapshot");
    fs::create_dir_all(&snapshot).expect("snapshot directory");
    fs::write(
        root.join("area.yaml"),
        format!(
            "area_id: fixture\narea_name: Fixture\nsource:\n  snapshot_dir: {}\n  snapshot_id: snapshot\n  community_place_types: [village, town]\ncompilation:\n  max_connection_km: 15\n",
            root.display()
        ),
    )
    .expect("area config");

    let mut edges = Vec::new();
    // The physically nearest strategic edge is longer by graph length than the
    // farther cycleway spine, so Euclidean proximity must not choose the result.
    add_bidirectional(
        &mut edges,
        "community",
        "a-entry",
        [0.0, 0.0],
        [0.01, 0.0],
        10.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "a-entry",
        "a-spine",
        [0.01, 0.0],
        [0.02, 0.0],
        50.0,
        "primary",
        Some("A1"),
        None,
    );
    add_bidirectional(
        &mut edges,
        "community",
        "cycle-entry",
        [0.0, 0.0],
        [0.5, 0.0],
        8.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "cycle-entry",
        "cycle-spine",
        [0.5, 0.0],
        [0.6, 0.0],
        50.0,
        "cycleway",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "a-entry",
        "town-one",
        [0.01, 0.0],
        [1.0, 0.0],
        1.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "a-entry",
        "town-two",
        [0.01, 0.0],
        [1.0, 1.0],
        2.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "cycle-entry",
        "town-one",
        [0.5, 0.0],
        [1.0, 0.0],
        100.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "cycle-entry",
        "town-two",
        [0.5, 0.0],
        [1.0, 1.0],
        120.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "isolated",
        "isolated-node",
        [10.0, 0.0],
        [10.1, 0.0],
        4.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "blocked",
        "blocked-node",
        [20.0, 0.0],
        [20.1, 0.0],
        4.0,
        "residential",
        None,
        Some("no"),
    );
    add_bidirectional(
        &mut edges,
        "blocked-node",
        "blocked-spine",
        [20.1, 0.0],
        [20.2, 0.0],
        10.0,
        "primary",
        Some("A2"),
        None,
    );
    add_bidirectional(
        &mut edges,
        "on-spine",
        "on-spine-next",
        [30.0, 0.0],
        [30.1, 0.0],
        10.0,
        "primary",
        Some("A3"),
        None,
    );
    write_collection(&snapshot.join("network.geojson"), edges);
    write_collection(
        &snapshot.join("places.geojson"),
        vec![
            place("community", "Community", "village", [0.0, 0.0]),
            place("isolated", "Isolated", "village", [10.0, 0.0]),
            place("blocked", "Blocked", "village", [20.0, 0.0]),
            place("on-spine", "On Spine", "village", [30.0, 0.0]),
            place("town-one", "Town One", "town", [1.0, 0.0]),
            place("town-two", "Town Two", "town", [1.0, 1.0]),
        ],
    );
    write_collection(
        &snapshot.join("context.geojson"),
        vec![json!({
            "type": "Feature",
            "properties": {"feature_type":"school", "evidence_id":"school-1", "name":"Community School"},
            "geometry": {"type":"Point", "coordinates":[0.0, 0.0]}
        })],
    );

    let report = compile(
        &root.join("area.yaml"),
        &root.join("output"),
        CompileOptions::default(),
    )
    .expect("rural access fixture compiles");

    let primary = report
        .community_access
        .iter()
        .find(|access| access.community_id == "community" && access.is_primary)
        .expect("primary community access");
    assert_eq!(primary.status, "served");
    assert_eq!(
        primary.joined_spine_id.as_deref(),
        Some("source:network:existing-cycleway:existing-cycleway")
    );
    assert_eq!(primary.path_edge_ids.len(), 1);
    assert_eq!(primary.access_length_m, Some(8.0));
    assert!(primary.attachment_distance_m.is_some());

    let alternatives = report
        .community_access
        .iter()
        .filter(|access| access.community_id == "community" && !access.is_primary)
        .collect::<Vec<_>>();
    assert_eq!(
        alternatives.len(),
        1,
        "equivalent prefixes are deduplicated"
    );
    assert_eq!(alternatives[0].path_edge_ids.len(), 1);
    assert_eq!(alternatives[0].access_length_m, Some(10.0));
    assert_eq!(
        alternatives[0].onward_destinations,
        vec!["Town One", "Town Two"]
    );
    assert_eq!(alternatives[0].onward_benefits.len(), 2);
    assert_eq!(
        alternatives[0].onward_benefits[0].destination_name,
        "Town One"
    );
    assert_eq!(alternatives[0].onward_benefits[0].full_route_length_m, 11.0);
    assert_eq!(
        alternatives[0].onward_benefits[0].primary_access_plus_onward_m,
        Some(27.0)
    );

    let on_spine = report
        .community_access
        .iter()
        .find(|access| access.community_id == "on-spine" && access.is_primary)
        .expect("on-spine access");
    assert_eq!(on_spine.status, "on-spine");
    assert_eq!(on_spine.access_length_m, Some(0.0));
    assert!(on_spine.path_edge_ids.is_empty());

    for id in ["isolated", "blocked"] {
        let gap = report
            .community_access
            .iter()
            .find(|access| access.community_id == id && access.is_primary)
            .expect("community gap");
        assert_eq!(gap.status, "network-gap");
        assert!(gap.joined_spine_id.is_none());
        assert!(gap.path_edge_ids.is_empty());
    }
    assert!(
        report
            .community_access
            .iter()
            .all(|access| access.community_id != "school-1")
    );
    assert_eq!(report.access_obligations.len(), 4);
    assert!(report.access_obligations.iter().any(|obligation| {
        obligation.id == "obligation:community:community" && obligation.disposition == "served"
    }));
    assert!(report.access_obligations.iter().any(|obligation| {
        obligation.id == "obligation:community:on-spine" && obligation.disposition == "served"
    }));
    assert_eq!(
        report
            .access_obligations
            .iter()
            .filter(|obligation| obligation.disposition == "network-gap")
            .count(),
        2
    );
    assert_eq!(report.accounting.network_gap_count, 2);

    let network: Value = serde_json::from_str(
        &fs::read_to_string(root.join("output/network.geojson")).expect("network output"),
    )
    .expect("network GeoJSON");
    assert!(
        network["features"]
            .as_array()
            .expect("features")
            .iter()
            .any(|feature| {
                feature["properties"]["kind"] == "community-access"
                    && feature["properties"]["community_id"] == "community"
                    && feature["properties"]["is_primary"] == true
            })
    );
    assert!(
        !network["features"]
            .as_array()
            .expect("features")
            .iter()
            .any(|feature| feature["properties"]["kind"] == "access-obligation")
    );
}

#[test]
fn rural_access_attaches_to_nearest_edge_interior_with_measured_partial_length() {
    let root = tempfile_root("satn-rs-rural-edge-attachment");
    let snapshot = root.join("snapshot");
    fs::create_dir_all(&snapshot).expect("snapshot directory");
    fs::write(
        root.join("area.yaml"),
        format!(
            "area_id: fixture\narea_name: Fixture\nsource:\n  snapshot_dir: {}\n  snapshot_id: snapshot\n  community_place_types: [village, town]\ncompilation:\n  max_connection_km: 15\n",
            root.display()
        ),
    )
    .expect("area config");

    let mut edges = Vec::new();
    add_bidirectional(
        &mut edges,
        "west",
        "east",
        [0.0, 0.0],
        [0.01, 0.0],
        1_000.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "east",
        "spine",
        [0.01, 0.0],
        [0.02, 0.0],
        10.0,
        "primary",
        Some("A4"),
        None,
    );
    add_bidirectional(
        &mut edges,
        "spur",
        "spur-end",
        [0.0055, 0.00048],
        [0.0056, 0.00048],
        4.0,
        "service",
        None,
        None,
    );
    write_collection(&snapshot.join("network.geojson"), edges);
    write_collection(
        &snapshot.join("places.geojson"),
        vec![place(
            "edge-community",
            "Edge Community",
            "village",
            [0.005, 0.00048],
        )],
    );

    let report = compile(
        &root.join("area.yaml"),
        &root.join("output"),
        CompileOptions::default(),
    )
    .expect("edge attachment fixture compiles");
    let access = report
        .community_access
        .iter()
        .find(|access| access.community_id == "edge-community" && access.is_primary)
        .expect("primary edge attachment");

    assert_eq!(access.status, "served");
    assert_eq!(access.access_length_m, Some(500.0));
    assert!(
        access
            .attachment_distance_m
            .is_some_and(|distance| distance < 56.0)
    );
    assert_eq!(access.attachment_point, Some([0.005, 0.0]));
    assert_eq!(access.path_geometry.first(), Some(&[0.005, 0.0]));
    assert_ne!(access.path_geometry.first(), Some(&[0.005, 0.00048]));
    assert!(access.attachment_edge_id.is_some());
    assert_eq!(access.attachment_node, None);
}

fn place(id: &str, name: &str, class: &str, point: [f64; 2]) -> Value {
    json!({
        "type":"Feature",
        "properties":{"place_id":id,"source_id":id,"name":name,"place_class":class},
        "geometry":{"type":"Point","coordinates":point}
    })
}

fn add_bidirectional(
    edges: &mut Vec<Value>,
    from: &str,
    to: &str,
    start: [f64; 2],
    end: [f64; 2],
    length: f64,
    highway: &str,
    reference: Option<&str>,
    bicycle: Option<&str>,
) {
    for (u, v, coordinates) in [
        (from, to, json!([start, end])),
        (to, from, json!([end, start])),
    ] {
        let mut properties = json!({"u":u,"v":v,"key":0,"length":length,"highway":highway});
        if let Some(reference) = reference {
            properties["ref"] = json!(reference);
        }
        if let Some(bicycle) = bicycle {
            properties["bicycle"] = json!(bicycle);
        }
        edges.push(json!({
            "type":"Feature",
            "properties":properties,
            "geometry":{"type":"LineString","coordinates":coordinates}
        }));
    }
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
