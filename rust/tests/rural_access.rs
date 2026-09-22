use std::fs;

use satn_rs::topography::TopographyAvailability;
use satn_rs::{CompileOptions, compile, prepare_with_progress};
use serde_json::{Value, json};

#[test]
fn prepared_rural_planner_caches_offer_and_accepts_only_offered_path() {
    let root = tempfile_root("satn-rs-rural-planner-seam");
    let snapshot = root.join("snapshot");
    fs::create_dir_all(&snapshot).expect("snapshot directory");
    fs::write(
        root.join("elevation.geojson"),
        serde_json::to_string(&json!({
            "type": "FeatureCollection",
            "features": [
                {"type":"Feature","properties":{"evidence_id":"e1","source_id":"dtm","elevation_m":0.0},"geometry":{"type":"Point","coordinates":[0.0,0.0]}},
                {"type":"Feature","properties":{"evidence_id":"e2","source_id":"dtm","elevation_m":20.0},"geometry":{"type":"Point","coordinates":[0.001,0.0]}},
                {"type":"Feature","properties":{"evidence_id":"e3","source_id":"dtm","elevation_m":0.0},"geometry":{"type":"Point","coordinates":[0.002,0.0]}},
                {"type":"Feature","properties":{"evidence_id":"e4","source_id":"dtm","elevation_m":1.0},"geometry":{"type":"Point","coordinates":[0.0,0.001]}},
                {"type":"Feature","properties":{"evidence_id":"e5","source_id":"dtm","elevation_m":2.0},"geometry":{"type":"Point","coordinates":[0.001,0.0005]}},
                {"type":"Feature","properties":{"evidence_id":"e6","source_id":"dtm","elevation_m":3.0},"geometry":{"type":"Point","coordinates":[0.002,0.0]}}
            ]
        }))
        .expect("elevation fixture"),
    )
    .expect("write elevation fixture");
    fs::write(
        root.join("area.yaml"),
        format!(
            "area_id: fixture\narea_name: Fixture\nsource:\n  snapshot_dir: {}\n  snapshot_id: snapshot\n  community_place_types: [village]\n  national_elevation:\n    path: {}\ncompilation:\n  max_connection_km: 15\n",
            root.display(),
            root.join("elevation.geojson").display()
        ),
    )
    .expect("area config");
    let mut edges = Vec::new();
    add_bidirectional(
        &mut edges,
        "village",
        "short",
        [0.0, 0.0],
        [0.001, 0.0],
        10.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "short",
        "spine",
        [0.001, 0.0],
        [0.002, 0.0],
        10.0,
        "primary",
        Some("A1"),
        None,
    );
    add_bidirectional(
        &mut edges,
        "village",
        "flat",
        [0.0, 0.0],
        [0.0, 0.001],
        15.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "flat",
        "spine",
        [0.0, 0.001],
        [0.002, 0.0],
        15.0,
        "residential",
        None,
        None,
    );
    write_collection(&snapshot.join("network.geojson"), edges);
    write_collection(
        &snapshot.join("places.geojson"),
        vec![place("village", "Village", "village", [0.0, 0.0])],
    );

    let mut progress = Vec::new();
    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |event| progress.push(event.stage.clone()),
    )
    .expect("prepared compilation");
    assert!(prepared.report.accounting.obligation_count > 0);
    assert_eq!(prepared.report.access_obligations.len(), 1);
    let pending_obligation = &prepared.report.access_obligations[0];
    assert_eq!(pending_obligation.id, "obligation:community:village");
    assert_eq!(pending_obligation.disposition, "unresolved");
    assert_eq!(prepared.report.accounting.network_gap_count, 0);
    assert!(prepared.report.accounting.unresolved_count > 0);
    let mut planner = prepared.rural_planner();
    let first = planner
        .offer_next()
        .expect("offer result")
        .expect("rural offer");
    let second = planner
        .offer_next()
        .expect("cached offer result")
        .expect("cached rural offer");
    assert_eq!(
        serde_json::to_value(&first).unwrap(),
        serde_json::to_value(&second).unwrap()
    );
    assert_eq!(first.community_id, "village");
    assert!(
        first
            .candidates
            .iter()
            .any(|candidate| candidate.criterion == "shortest-new-link")
    );
    assert!(
        first
            .candidates
            .iter()
            .any(|candidate| candidate.criterion == "least-climbing-detour")
    );
    let chosen = first
        .candidates
        .iter()
        .find(|candidate| candidate.criterion == "least-climbing-detour")
        .expect("supported flatter candidate");
    let shortest = first
        .candidates
        .iter()
        .find(|candidate| candidate.criterion == "shortest-new-link")
        .expect("shortest candidate");
    assert_eq!(
        shortest
            .access
            .full_access_topography
            .as_ref()
            .expect("shortest terrain profile")
            .availability,
        TopographyAvailability::Available
    );
    assert_eq!(
        shortest
            .access
            .full_access_topography
            .as_ref()
            .and_then(|profile| profile.cumulative_elevation_variation_m),
        Some(20.0)
    );
    assert_eq!(
        chosen
            .access
            .full_access_topography
            .as_ref()
            .and_then(|profile| profile.cumulative_elevation_variation_m),
        Some(4.0)
    );
    let accepted = planner.accept(&chosen.id).expect("accept cached candidate");
    assert_eq!(accepted.path_edge_ids, chosen.access.path_edge_ids);
    assert!(planner.accept("rural:village:shortest").is_err());

    let mut rejected_planner = prepared.rural_planner();
    let rejected_offer = rejected_planner
        .offer_next()
        .expect("rejection offer")
        .expect("rejection candidate");
    rejected_planner
        .reject("terrain evidence needs review")
        .expect("reject offered candidate");
    let rejected_records = rejected_planner.into_records();
    let rejected = rejected_records
        .into_iter()
        .find(|record| record.community_id == rejected_offer.community_id)
        .expect("retained rejected community");
    assert_eq!(rejected.status, "unresolved");
    assert_eq!(rejected.reason, "terrain evidence needs review");
    let rejected_report = prepared
        .report
        .clone()
        .with_community_access(vec![rejected.clone()]);
    let rejected_obligation = rejected_report
        .access_obligations
        .iter()
        .find(|obligation| obligation.id == "obligation:community:village")
        .expect("materialized unresolved community obligation");
    assert_eq!(rejected_obligation.disposition, "unresolved");
    assert_eq!(rejected_report.accounting.network_gap_count, 0);
    assert!(rejected_report.accounting.unresolved_count > 0);
    assert!(progress.iter().any(|stage| stage == "preparation"));
}

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
    assert!(
        alternatives.is_empty(),
        "destination routes are not adopted feeders"
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
fn rural_access_grows_a_shared_frontier_with_unique_child_links() {
    let root = tempfile_root("satn-rs-rural-frontier");
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
        "parent",
        "spine-entry",
        [0.0, 0.01],
        [0.02, 0.0],
        20.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "spine-entry",
        "spine-end",
        [0.02, 0.0],
        [0.03, 0.0],
        10.0,
        "primary",
        Some("A1"),
        None,
    );
    add_bidirectional(
        &mut edges,
        "child",
        "parent",
        [0.0, 0.02],
        [0.0, 0.01],
        5.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "child",
        "alternate-entry",
        [0.0, 0.02],
        [0.04, 0.02],
        50.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "alternate-entry",
        "alternate-spine",
        [0.04, 0.02],
        [0.05, 0.02],
        10.0,
        "primary",
        Some("A2"),
        None,
    );
    write_collection(&snapshot.join("network.geojson"), edges);
    write_collection(
        &snapshot.join("places.geojson"),
        vec![
            place("parent", "Parent", "village", [0.0, 0.01]),
            place("child", "Child", "village", [0.0, 0.02]),
        ],
    );

    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_| {},
    )
    .expect("prepared shared-frontier compilation");
    let mut planner = prepared.rural_planner();
    let first_offer = planner
        .offer_next()
        .expect("first rural offer")
        .expect("parent offer");
    assert_eq!(first_offer.community_id, "parent");
    let parent_candidate = first_offer
        .candidates
        .iter()
        .find(|candidate| candidate.criterion == "shortest-new-link")
        .expect("parent shortest candidate");
    let parent_access = planner
        .accept(&parent_candidate.id)
        .expect("accept parent candidate");
    assert_eq!(parent_access.parent_community_id, None);

    let second_offer = planner
        .offer_next()
        .expect("second rural offer")
        .expect("child offer");
    assert_eq!(second_offer.community_id, "child");
    let child_candidate = second_offer
        .candidates
        .iter()
        .find(|candidate| candidate.criterion == "shortest-new-link")
        .expect("child shortest candidate");
    assert_eq!(
        child_candidate.access.parent_community_id.as_deref(),
        Some("parent")
    );
    assert_eq!(child_candidate.access.new_link_length_m, Some(5.0));
    assert_eq!(child_candidate.access.full_access_length_m, Some(25.0));
    assert_eq!(
        child_candidate
            .access
            .full_access_topography
            .as_ref()
            .expect("unknown child terrain profile")
            .availability,
        TopographyAvailability::Unknown
    );
    assert!(
        child_candidate
            .access
            .full_access_topography
            .as_ref()
            .expect("unknown child terrain profile")
            .cumulative_elevation_variation_m
            .is_none()
    );
    let child_access = planner
        .accept(&child_candidate.id)
        .expect("accept child candidate");
    assert_eq!(
        child_access.path_edge_ids,
        child_candidate.access.path_edge_ids
    );

    let report = compile(
        &root.join("area.yaml"),
        &root.join("output"),
        CompileOptions::default(),
    )
    .expect("frontier fixture compiles");
    let parent = report
        .community_access
        .iter()
        .find(|access| access.community_id == "parent" && access.is_primary)
        .expect("parent access");
    let child = report
        .community_access
        .iter()
        .find(|access| access.community_id == "child" && access.is_primary)
        .expect("child access");

    assert_eq!(parent.status, "served");
    assert_eq!(parent.parent_community_id, None);
    assert_eq!(parent.attachment_depth, Some(0));
    assert_eq!(parent.admission_order, Some(1));
    assert_eq!(parent.new_link_length_m, Some(20.0));
    assert_eq!(parent.full_access_length_m, Some(20.0));
    assert_eq!(child.status, "served");
    assert_eq!(child.parent_community_id.as_deref(), Some("parent"));
    assert_eq!(child.parent_community_name.as_deref(), Some("Parent"));
    assert_eq!(child.parent_junction_node.as_deref(), Some("parent"));
    assert_eq!(child.parent_junction_remaining_m, Some(20.0));
    assert_eq!(child.root_spine_id, parent.root_spine_id);
    assert_eq!(child.attachment_depth, Some(1));
    assert_eq!(child.admission_order, Some(2));
    assert_eq!(child.new_link_length_m, Some(5.0));
    assert_eq!(child.full_access_length_m, Some(25.0));
    assert_eq!(child.path_edge_ids.len(), 1);
    assert!(
        child
            .path_edge_ids
            .iter()
            .all(|edge_id| !parent.path_edge_ids.contains(edge_id)),
        "the child record contains only its incremental feeder"
    );
    let network: Value = serde_json::from_str(
        &fs::read_to_string(root.join("output/network.geojson")).expect("network output"),
    )
    .expect("network GeoJSON");
    let child_feature = network["features"]
        .as_array()
        .expect("features")
        .iter()
        .find(|feature| {
            feature["properties"]["kind"] == "community-access"
                && feature["properties"]["community_id"] == "child"
                && feature["properties"]["is_primary"] == true
        })
        .expect("child public access feature");
    assert_eq!(child_feature["properties"]["new_link_length_m"], 5.0);
    assert_eq!(child_feature["properties"]["full_access_length_m"], 25.0);
    assert_eq!(
        child_feature["geometry"]["coordinates"],
        json!([[0.0, 0.02], [0.0, 0.01]])
    );
}

#[test]
fn complete_journey_comparison_reuses_selected_branch_and_direct_baseline() {
    let root = tempfile_root("satn-rs-complete-journey");
    let snapshot = root.join("snapshot");
    fs::create_dir_all(&snapshot).expect("snapshot directory");
    let elevation = root.join("elevation.geojson");
    fs::write(
        &elevation,
        serde_json::to_string(&json!({
            "type": "FeatureCollection",
            "features": [
                {"type":"Feature","properties":{"evidence_id":"child","source_id":"dtm","elevation_m":0.0},"geometry":{"type":"Point","coordinates":[0.0,0.002]}},
                {"type":"Feature","properties":{"evidence_id":"parent","source_id":"dtm","elevation_m":100.0},"geometry":{"type":"Point","coordinates":[0.0,0.001]}},
                {"type":"Feature","properties":{"evidence_id":"spine-entry","source_id":"dtm","elevation_m":0.0},"geometry":{"type":"Point","coordinates":[0.002,0.0]}},
                {"type":"Feature","properties":{"evidence_id":"spine-end","source_id":"dtm","elevation_m":0.0},"geometry":{"type":"Point","coordinates":[0.003,0.0]}},
                {"type":"Feature","properties":{"evidence_id":"alternate-entry","source_id":"dtm","elevation_m":0.0},"geometry":{"type":"Point","coordinates":[0.004,0.002]}},
                {"type":"Feature","properties":{"evidence_id":"alternate-spine","source_id":"dtm","elevation_m":0.0},"geometry":{"type":"Point","coordinates":[0.005,0.002]}},
                {"type":"Feature","properties":{"evidence_id":"alternate-mid-1","source_id":"dtm","elevation_m":0.0},"geometry":{"type":"Point","coordinates":[0.002,0.002]}},
                {"type":"Feature","properties":{"evidence_id":"alternate-mid-2","source_id":"dtm","elevation_m":0.0},"geometry":{"type":"Point","coordinates":[0.003,0.002]}},
                {"type":"Feature","properties":{"evidence_id":"direct-mid-1","source_id":"dtm","elevation_m":100.0},"geometry":{"type":"Point","coordinates":[0.001,0.0015]}},
                {"type":"Feature","properties":{"evidence_id":"direct-mid-2","source_id":"dtm","elevation_m":0.0},"geometry":{"type":"Point","coordinates":[0.002,0.001]}},
                {"type":"Feature","properties":{"evidence_id":"direct-mid-3","source_id":"dtm","elevation_m":100.0},"geometry":{"type":"Point","coordinates":[0.003,0.0005]}},
                {"type":"Feature","properties":{"evidence_id":"a1-onward","source_id":"dtm","elevation_m":0.0},"geometry":{"type":"Point","coordinates":[0.0035,0.0]}},
                {"type":"Feature","properties":{"evidence_id":"a2-onward","source_id":"dtm","elevation_m":0.0},"geometry":{"type":"Point","coordinates":[0.0045,0.002]}},
                {"type":"Feature","properties":{"evidence_id":"radstock","source_id":"dtm","elevation_m":0.0},"geometry":{"type":"Point","coordinates":[0.004,0.0]}}
            ]
        }))
        .expect("elevation fixture"),
    )
    .expect("write elevation fixture");
    fs::write(
        root.join("area.yaml"),
        format!(
            "area_id: fixture\narea_name: Fixture\nsource:\n  snapshot_dir: {}\n  snapshot_id: snapshot\n  community_place_types: [village]\n  national_elevation:\n    path: {}\ncompilation:\n  max_connection_km: 15\n",
            root.display(),
            elevation.display()
        ),
    )
    .expect("area config");

    let mut edges = Vec::new();
    add_bidirectional(
        &mut edges,
        "parent",
        "spine-entry",
        [0.0, 0.001],
        [0.002, 0.0],
        20.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "spine-entry",
        "spine-end",
        [0.002, 0.0],
        [0.003, 0.0],
        10.0,
        "primary",
        Some("A1"),
        None,
    );
    add_bidirectional(
        &mut edges,
        "child",
        "parent",
        [0.0, 0.002],
        [0.0, 0.001],
        5.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "child",
        "alternate-entry",
        [0.0, 0.002],
        [0.004, 0.002],
        50.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "alternate-entry",
        "alternate-spine",
        [0.004, 0.002],
        [0.005, 0.002],
        10.0,
        "primary",
        Some("A2"),
        None,
    );
    add_bidirectional(
        &mut edges,
        "spine-end",
        "a1-onward",
        [0.003, 0.0],
        [0.0035, 0.0],
        30.0,
        "primary",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "a1-onward",
        "radstock",
        [0.0035, 0.0],
        [0.004, 0.0],
        0.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "alternate-spine",
        "a2-onward",
        [0.005, 0.002],
        [0.0045, 0.002],
        80.0,
        "primary",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "a2-onward",
        "radstock",
        [0.0045, 0.002],
        [0.004, 0.0],
        0.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "child",
        "radstock",
        [0.0, 0.002],
        [0.004, 0.0],
        40.0,
        "residential",
        None,
        None,
    );
    write_collection(&snapshot.join("network.geojson"), edges);
    write_collection(
        &snapshot.join("places.geojson"),
        vec![
            place("parent", "Parent", "village", [0.0, 0.001]),
            place("child", "Child", "village", [0.0, 0.002]),
            place("radstock", "Radstock", "town", [0.004, 0.0]),
        ],
    );

    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_| {},
    )
    .expect("prepared complete-journey fixture");
    let mut planner = prepared.rural_planner();
    let parent_offer = planner
        .offer_next()
        .expect("parent offer result")
        .expect("parent offer");
    let parent_candidate = parent_offer
        .candidates
        .iter()
        .find(|candidate| candidate.criterion == "shortest-new-link")
        .expect("parent shortest candidate");
    planner.accept(&parent_candidate.id).expect("accept parent");
    let child_offer = planner
        .offer_next()
        .expect("child offer result")
        .expect("child offer");
    let shortest = child_offer
        .candidates
        .iter()
        .find(|candidate| candidate.criterion == "shortest-new-link")
        .expect("child shortest candidate");
    let retained_alternative = child_offer
        .candidates
        .iter()
        .find(|candidate| candidate.criterion == "least-climbing-detour")
        .expect("child retained alternative");
    let alternative_access = retained_alternative.access.clone();
    let selected_access = planner.accept(&shortest.id).expect("accept child");
    let accepted = planner.into_records();

    let comparison = prepared
        .compare_complete_journey(
            &accepted,
            &selected_access,
            Some(&alternative_access),
            "Radstock",
        )
        .expect("complete journey comparison");
    assert_eq!(comparison.destination.name, "Radstock");
    assert_eq!(comparison.selected.length_m, 65.0);
    assert_eq!(
        comparison
            .retained_alternative
            .as_ref()
            .expect("retained alternative path")
            .length_m,
        140.0
    );
    assert_eq!(comparison.direct.length_m, 40.0);
    assert_eq!(
        comparison.selected.network_status,
        "selected-feeder-plus-source-graph-onward"
    );
    assert_eq!(comparison.direct.network_status, "source-graph-alternative");
    assert_eq!(
        comparison.to_geojson()["features"]
            .as_array()
            .unwrap()
            .len(),
        3
    );
    assert_eq!(
        comparison.to_geojson()["features"][0]["geometry"]["type"],
        "LineString"
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
    add_bidirectional(
        &mut edges,
        "east",
        "late",
        [0.01, 0.0],
        [0.01, -0.01],
        1_000.0,
        "residential",
        None,
        None,
    );
    add_bidirectional(
        &mut edges,
        "spine",
        "destination",
        [0.02, 0.0],
        [0.03, 0.0],
        50.0,
        "residential",
        None,
        None,
    );
    write_collection(&snapshot.join("network.geojson"), edges);
    write_collection(
        &snapshot.join("places.geojson"),
        vec![
            place(
                "nearer-parent",
                "Nearer Parent",
                "village",
                [0.0075, 0.00048],
            ),
            place(
                "edge-community",
                "Edge Community",
                "village",
                [0.005, 0.00048],
            ),
            place("late", "Late", "village", [0.01, -0.01]),
            place("upstream", "Upstream", "village", [0.0, 0.0]),
            place("destination", "Destination", "town", [0.03, 0.0]),
        ],
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
    let parent = report
        .community_access
        .iter()
        .find(|access| access.community_id == "nearer-parent" && access.is_primary)
        .expect("nearer parent attachment");
    let late = report
        .community_access
        .iter()
        .find(|access| access.community_id == "late" && access.is_primary)
        .expect("late downstream attachment");
    let upstream = report
        .community_access
        .iter()
        .find(|access| access.community_id == "upstream" && access.is_primary)
        .expect("upstream branch attachment");

    assert_eq!(access.status, "served");
    assert_eq!(access.parent_community_id.as_deref(), Some("nearer-parent"));
    assert!(
        access
            .new_link_length_m
            .is_some_and(|length| (length - 250.0).abs() < 1e-9)
    );
    assert!(
        access
            .full_access_length_m
            .is_some_and(|length| (length - 500.0).abs() < 1e-9)
    );
    assert!(
        access
            .access_length_m
            .is_some_and(|length| (length - 250.0).abs() < 1e-9)
    );
    assert!(
        access
            .attachment_distance_m
            .is_some_and(|distance| distance < 56.0)
    );
    assert_eq!(access.attachment_point, Some([0.005, 0.0]));
    assert_eq!(access.path_geometry.first(), Some(&[0.005, 0.0]));
    assert!(
        access
            .path_geometry
            .last()
            .is_some_and(|point| (point[0] - 0.0075).abs() < 1e-12 && point[1] == 0.0)
    );
    assert_ne!(access.path_geometry.first(), Some(&[0.005, 0.00048]));
    assert!(access.attachment_edge_id.is_some());
    assert_eq!(access.attachment_node, None);
    assert!(
        parent
            .new_link_length_m
            .is_some_and(|length| (length - 250.0).abs() < 1e-9)
    );
    assert!(
        parent
            .full_access_length_m
            .is_some_and(|length| (length - 250.0).abs() < 1e-9)
    );
    assert!(
        late.new_link_length_m
            .is_some_and(|length| (length - 1_000.0).abs() < 1e-9)
    );
    assert!(
        late.full_access_length_m
            .is_some_and(|length| (length - 1_000.0).abs() < 1e-9)
    );
    assert_eq!(
        upstream.parent_community_id.as_deref(),
        Some("edge-community")
    );
    assert!(
        upstream
            .new_link_length_m
            .is_some_and(|length| (length - 500.0).abs() < 1e-9)
    );
    assert!(
        upstream
            .full_access_length_m
            .is_some_and(|length| (length - 1_000.0).abs() < 1e-9)
    );

    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_| {},
    )
    .expect("prepared partial journey fixture");
    let comparison = prepared
        .compare_complete_journey(&report.community_access, access, None, "Destination")
        .expect("partial journey comparison");
    assert_eq!(comparison.selected.length_m, 560.0);
    assert_eq!(comparison.direct.length_m, 560.0);
    assert_eq!(comparison.selected.feeder_length_m, Some(500.0));
    assert_eq!(comparison.selected.onward_length_m, Some(60.0));
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
