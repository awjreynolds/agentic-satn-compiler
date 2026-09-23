use std::fs;

use satn_rs::{CompileOptions, CompileReport, prepare_with_progress};
use serde_json::{Value, json};

#[test]
fn preparation_selects_a_complete_noded_face_with_dataset_provenance_and_measured_area() {
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
    let neighbourhoods = report["candidate_neighbourhoods"]
        .as_array()
        .expect("candidate neighbourhoods in prepared report");
    assert_eq!(neighbourhoods.len(), 1);

    let neighbourhood = &neighbourhoods[0];
    assert_eq!(neighbourhood["urban_extent_source_id"], "bath-relation");
    assert_eq!(
        neighbourhood["source_dataset_ids"],
        json!(["os-open-roads-fixture"])
    );
    assert_eq!(
        neighbourhood["source_effective_dates"],
        json!(["2026-01-01"])
    );
    assert_eq!(
        neighbourhood["source_licences"],
        json!(["Open Government Licence v3.0"])
    );
    assert_eq!(
        neighbourhood["source_classifications"],
        json!(["a-road", "b-road", "classified-unnumbered"])
    );
    assert_eq!(neighbourhood["geometry"]["type"], "Polygon");
    let coordinates = neighbourhood["geometry"]["coordinates"]
        .as_array()
        .expect("candidate polygon coordinates");
    assert_eq!(coordinates.len(), 1);
    assert!(coordinates[0].as_array().expect("outer ring").len() >= 4);
    let area_m2 = neighbourhood["area_m2"].as_f64().expect("measured area");
    assert!(
        (100_000.0..300_000.0).contains(&area_m2),
        "selected triangular face should retain its measured area, got {area_m2} m²"
    );
    let ring = coordinates[0].as_array().expect("outer ring");
    assert!(ring.iter().any(|point| point == &json!([-2.36, 51.38])));
    assert!(ring.iter().any(|point| point == &json!([-2.36, 51.39])));
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
