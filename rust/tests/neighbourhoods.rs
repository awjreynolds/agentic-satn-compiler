use std::fs;

use satn_rs::{CompileOptions, CompileReport, prepare_with_progress};
use serde_json::{Value, json};

#[test]
fn preparation_does_not_use_place_extents_as_candidate_built_up_evidence() {
    let root = tempfile_root("satn-rs-candidate-neighbourhood");
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
    write_collection(&snapshot.join("network.geojson"), Vec::new());
    write_collection(&snapshot.join("places.geojson"), Vec::new());
    write_collection(
        &snapshot.join("osm-place-features.geojson"),
        vec![
            json!({
                "type":"Feature",
                "properties":{"id":"bath-relation","name":"Bath","place":"city","boundary":"place","wikidata":"Q-fixture"},
                "geometry":{"type":"Polygon","coordinates":[[
                    [-2.3595,51.3825],[-2.3585,51.3825],[-2.3585,51.3835],[-2.3595,51.3835],[-2.3595,51.3825]
                ]]}
            }),
            json!({
                "type":"Feature",
                "properties":{"id":"bath-node","name":"Bath","place":"city","wikidata":"Q-fixture"},
                "geometry":{"type":"Point","coordinates":[-2.359,51.383]}
            }),
            json!({
                "type":"Feature",
                "properties":{"id":"gapped-place","name":"Gapped Place","place":"city","boundary":"place","wikidata":"Q-gap"},
                "geometry":{"type":"Polygon","coordinates":[[
                    [-2.34,51.38],[-2.33,51.38],[-2.33,51.39],[-2.34,51.39],[-2.34,51.38]
                ]]}
            }),
            json!({
                "type":"Feature",
                "properties":{"id":"gapped-place-node","name":"Gapped Place","place":"city","wikidata":"Q-gap"},
                "geometry":{"type":"Point","coordinates":[-2.335,51.385]}
            }),
        ],
    );
    write_collection(
        &snapshot.join("official-road-classification.geojson"),
        vec![
            official_road("west-a", "a-road", vec![[-2.36, 51.38], [-2.36, 51.39]]),
            official_road("south-b", "b-road", vec![[-2.36, 51.38], [-2.35, 51.38]]),
            official_road("east-a", "a-road", vec![[-2.35, 51.38], [-2.35, 51.39]]),
            official_road(
                "north-unclassified",
                "classified-unnumbered",
                vec![[-2.36, 51.39], [-2.35, 51.39]],
            ),
            official_road(
                "diagonal-sw-ne",
                "b-road",
                vec![[-2.36, 51.38], [-2.35, 51.39]],
            ),
            official_road(
                "diagonal-nw-se",
                "classified-unnumbered",
                vec![[-2.36, 51.39], [-2.35, 51.38]],
            ),
            official_road(
                "excluded-local-road",
                "unclassified",
                vec![[-2.36, 51.385], [-2.35, 51.385]],
            ),
            official_road("gap-west", "a-road", vec![[-2.34, 51.38], [-2.34, 51.39]]),
            official_road("gap-south", "b-road", vec![[-2.34, 51.38], [-2.33, 51.38]]),
            official_road(
                "gap-east",
                "classified-unnumbered",
                vec![[-2.33, 51.38], [-2.33, 51.39]],
            ),
            // Polygonization may node real intersections, but this small open gap stays open.
            official_road(
                "gap-north-west",
                "a-road",
                vec![[-2.34, 51.39], [-2.3350005, 51.39]],
            ),
            official_road(
                "gap-north-east",
                "b-road",
                vec![[-2.3349995, 51.39], [-2.33, 51.39]],
            ),
        ],
    );

    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_event| {},
    )
    .expect("fixture prepares");
    let report = serde_json::to_value(&prepared.report).expect("serialized report");
    let mut legacy_report = report.clone();
    legacy_report
        .as_object_mut()
        .expect("report object")
        .remove("candidate_neighbourhoods");
    let legacy_report: CompileReport =
        serde_json::from_value(legacy_report).expect("older report without candidate field");
    assert!(legacy_report.candidate_neighbourhoods.is_empty());
    assert!(prepared.report.candidate_neighbourhoods.is_empty());
}

#[test]
fn preparation_limits_candidates_to_built_up_areas_and_distinct_road_frontages() {
    let root = tempfile_root("satn-rs-candidate-built-up-frontages");
    let snapshot = root.join("snapshot");
    fs::create_dir_all(&snapshot).expect("snapshot directory");
    fs::write(
        root.join("area.yaml"),
        format!(
            "area_id: fixture\narea_name: Fixture\nsource:\n  snapshot_dir: {}\n  snapshot_id: snapshot\n  candidate_built_up_areas: candidate-built-up-areas.geojson\n",
            root.display()
        ),
    )
    .expect("area config");
    write_collection(&snapshot.join("network.geojson"), Vec::new());
    write_collection(&snapshot.join("places.geojson"), Vec::new());
    write_collection(&snapshot.join("osm-place-features.geojson"), Vec::new());
    write_collection(
        &root.join("candidate-built-up-areas.geojson"),
        vec![
            built_up_area("same-road", "Same Road", -2.38),
            built_up_area("distinct-roads", "Distinct Roads", -2.36),
            built_up_area("point-only", "Point Only", -2.34),
            built_up_area("named-unnumbered", "Named Unnumbered", -2.32),
        ],
    );
    write_collection(
        &snapshot.join("official-road-classification.geojson"),
        vec![
            // Two separately identified features and changing names remain one A-road.
            identified_official_road(
                "a4-west",
                "a-road",
                Some("A4"),
                Some("West Road"),
                vec![[-2.38, 51.38], [-2.38, 51.39]],
            ),
            identified_official_road(
                "a4-south",
                "a-road",
                Some("A4"),
                Some("High Street"),
                vec![[-2.38, 51.38], [-2.37, 51.38]],
            ),
            identified_official_road(
                "a36-west",
                "a-road",
                Some("A36"),
                Some("West Road"),
                vec![[-2.36, 51.38], [-2.36, 51.39]],
            ),
            identified_official_road(
                "b3110-south",
                "b-road",
                Some("B3110"),
                Some("High Street"),
                vec![[-2.36, 51.38], [-2.35, 51.38]],
            ),
            identified_official_road(
                "a33-west",
                "a-road",
                Some("A33"),
                Some("West Road"),
                vec![[-2.34, 51.38], [-2.34, 51.39]],
            ),
            // This B-road meets the built-up boundary at one point only.
            identified_official_road(
                "b4420-point-contact",
                "b-road",
                Some("B4420"),
                Some("Point Road"),
                vec![[-2.33, 51.39], [-2.335, 51.385]],
            ),
            identified_official_road(
                "a2-west",
                "a-road",
                Some("A2"),
                Some("West Road"),
                vec![[-2.32, 51.38], [-2.32, 51.39]],
            ),
            identified_official_road(
                "old-road-south",
                "classified-unnumbered",
                None,
                Some("Old Road"),
                vec![[-2.32, 51.38], [-2.31, 51.38]],
            ),
        ],
    );

    let prepared = prepare_with_progress(
        &root.join("area.yaml"),
        CompileOptions::default(),
        &mut |_event| {},
    )
    .expect("fixture prepares");

    let candidates = &prepared.report.candidate_neighbourhoods;
    assert_eq!(
        candidates
            .iter()
            .map(|candidate| candidate.urban_extent_source_id.as_str())
            .collect::<Vec<_>>(),
        vec!["distinct-roads", "named-unnumbered"]
    );
    let distinct_roads = &candidates[0];
    assert_eq!(distinct_roads.urban_extent_name, "Distinct Roads");
    assert!(distinct_roads.urban_edge_closes_boundary);
    assert_eq!(
        distinct_roads.classified_road_frontages,
        vec!["a-road A36 (West Road)", "b-road B3110 (High Street)"]
    );
    assert_eq!(
        distinct_roads.urban_extent_source_dataset_id.as_deref(),
        Some("ons-built-up-areas-2022")
    );
    assert_eq!(
        distinct_roads.urban_extent_source_effective_date.as_deref(),
        Some("2022-12")
    );
    assert_eq!(
        distinct_roads.urban_extent_source_licence.as_deref(),
        Some("Open Government Licence v3.0")
    );
    assert!(
        distinct_roads
            .geometry
            .coordinates
            .iter()
            .flatten()
            .all(|[longitude, latitude]| {
                *longitude >= -2.36
                    && *longitude <= -2.35
                    && *latitude >= 51.38
                    && *latitude <= 51.39
            })
    );
    assert_eq!(
        candidates[1].classified_road_frontages,
        vec!["classified-unnumbered Old Road", "a-road A2 (West Road)"]
    );
}

fn built_up_area(source_id: &str, name: &str, west: f64) -> Value {
    let east = west + 0.01;
    json!({
        "type":"Feature",
        "properties":{
            "BUA22CD":source_id,
            "BUA22NM":name,
            "source_id":"ons-built-up-areas-2022",
            "effective_date":"2022-12",
            "licence":"Open Government Licence v3.0",
            "source_url":"https://example.test/bua",
            "attribution":"ONS and OS test attribution"
        },
        "geometry":{"type":"Polygon","coordinates":[[
            [west,51.38],[east,51.38],[east,51.39],[west,51.39],[west,51.38]
        ]]}
    })
}

fn identified_official_road(
    id: &str,
    classification: &str,
    number: Option<&str>,
    name: Option<&str>,
    coordinates: Vec<[f64; 2]>,
) -> Value {
    json!({
        "type":"Feature",
        "properties":{
            "official_feature_id":id,
            "official_classification":classification,
            "official_road_number":number,
            "official_road_name":name,
            "source_id":"os-open-roads-fixture",
            "effective_date":"2026-01-01",
            "licence":"Open Government Licence v3.0"
        },
        "geometry":{"type":"LineString","coordinates":coordinates}
    })
}

fn official_road(id: &str, classification: &str, coordinates: Vec<[f64; 2]>) -> Value {
    json!({
        "type":"Feature",
        "properties":{
            "official_feature_id":id,
            "official_classification":classification,
            "official_road_number":null,
            "source_id":"os-open-roads-fixture",
            "effective_date":"2026-01-01",
            "licence":"Open Government Licence v3.0"
        },
        "geometry":{"type":"LineString","coordinates":coordinates}
    })
}

fn write_collection(path: &std::path::Path, features: Vec<Value>) {
    fs::write(
        path,
        serde_json::to_string(&json!({"type":"FeatureCollection","features":features}))
            .expect("collection JSON"),
    )
    .expect("collection");
}

fn tempfile_root(label: &str) -> std::path::PathBuf {
    let root = std::env::temp_dir().join(format!("{label}-{}", std::process::id()));
    if root.exists() {
        fs::remove_dir_all(&root).expect("clean fixture root");
    }
    root
}
