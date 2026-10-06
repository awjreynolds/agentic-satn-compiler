//! Complete planar faces formed from classified roads and selected by urban extents.

use std::collections::{BTreeMap, BTreeSet};

use geo::{Area, Coord, LineString as GeoLineString, Polygon as GeoPolygon};
use geos::{Geom, Geometry as GeosGeometry};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::error::{Result, SatnError};
use crate::geojson::{Feature, Geometry as SourceGeometry, string_property};
use crate::geometry::Projector;

const INCLUDED_CLASSIFICATIONS: [&str; 3] = ["a-road", "b-road", "classified-unnumbered"];

/// A complete classified-road face that overlaps one admitted urban extent.
///
/// This is an enclosure candidate only. It does not imply connected internal
/// streets, existing low-traffic conditions, safe crossings, or legal access.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct CandidateNeighbourhood {
    pub id: String,
    pub urban_extent_source_id: String,
    pub urban_extent_name: String,
    pub area_m2: f64,
    /// Dataset-level provenance; these values do not identify a face's exact boundary roads.
    pub source_dataset_ids: Vec<String>,
    pub source_effective_dates: Vec<String>,
    pub source_licences: Vec<String>,
    pub source_classifications: Vec<String>,
    #[serde(default)]
    pub classified_road_frontages: Vec<String>,
    #[serde(default)]
    pub urban_edge_closes_boundary: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub urban_extent_source_dataset_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub urban_extent_source_effective_date: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub urban_extent_source_licence: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub urban_extent_source_url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub urban_extent_source_attribution: Option<String>,
    pub geometry: CandidateNeighbourhoodGeometry,
}

/// A candidate-only built-up extent. It is separate from Place urban extents,
/// which remain the rural-routing evidence.
#[derive(Debug, Clone)]
pub(crate) struct CandidateBuiltUpArea {
    pub source_id: String,
    pub name: String,
    pub geometry: Vec<Vec<Vec<[f64; 2]>>>,
    pub source_dataset_id: Option<String>,
    pub source_effective_date: Option<String>,
    pub source_licence: Option<String>,
    pub source_url: Option<String>,
    pub source_attribution: Option<String>,
}

/// GeoJSON Polygon geometry retained with a candidate neighbourhood.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct CandidateNeighbourhoodGeometry {
    #[serde(rename = "type")]
    pub geometry_type: String,
    pub coordinates: Vec<Vec<[f64; 2]>>,
}

struct PolygonFace {
    geometry: GeosGeometry,
    coordinates: Vec<Vec<[f64; 2]>>,
    canonical_key: String,
}

struct ClassifiedRoad {
    geometry: GeosGeometry,
    identity: Option<RoadIdentity>,
}

#[derive(Debug, Clone)]
struct RoadIdentity {
    key: String,
    display: String,
    classification: String,
    road_number: Option<String>,
    road_name: Option<String>,
}

/// Read named ONS BUA features from the candidate-only source input. The
/// source dataset fields are carried on each resulting candidate.
pub(crate) fn candidate_built_up_areas(features: &[Feature]) -> Vec<CandidateBuiltUpArea> {
    features
        .iter()
        .filter_map(|feature| {
            let source_id = non_empty_property(feature, "BUA22CD")?;
            let name = non_empty_property(feature, "BUA22NM")?;
            let geometry = match &feature.geometry {
                SourceGeometry::Polygon(rings) => vec![rings.clone()],
                SourceGeometry::MultiPolygon(polygons) => polygons.clone(),
                SourceGeometry::Point(_)
                | SourceGeometry::LineString(_)
                | SourceGeometry::MultiLineString(_) => return None,
            };
            Some(CandidateBuiltUpArea {
                source_id,
                name,
                geometry,
                source_dataset_id: non_empty_property(feature, "source_id"),
                source_effective_date: non_empty_property(feature, "effective_date"),
                source_licence: non_empty_property(feature, "licence"),
                source_url: non_empty_property(feature, "source_url"),
                source_attribution: non_empty_property(feature, "attribution"),
            })
        })
        .collect()
}

/// Polygonize each sourced built-up edge with official classified roads,
/// retaining contained faces with at least two distinct road frontages.
pub(crate) fn derive_candidate_neighbourhoods(
    official_features: &[Feature],
    built_up_areas: &[CandidateBuiltUpArea],
) -> Result<Vec<CandidateNeighbourhood>> {
    if official_features.is_empty() || built_up_areas.is_empty() {
        return Ok(Vec::new());
    }

    let mut road_linework = Vec::new();
    let mut roads = Vec::new();
    let mut source_dataset_ids = BTreeSet::new();
    let mut source_effective_dates = BTreeSet::new();
    let mut source_licences = BTreeSet::new();
    let mut source_classifications = BTreeSet::new();
    for feature in official_features {
        let classification = string_property(&feature.properties, "official_classification")
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase();
        if !INCLUDED_CLASSIFICATIONS.contains(&classification.as_str()) {
            continue;
        }
        let lines = source_line_parts(&feature.geometry);
        if lines.is_empty() {
            continue;
        }
        if let Some(value) = non_empty_property(feature, "source_id") {
            source_dataset_ids.insert(value);
        }
        if let Some(value) = non_empty_property(feature, "effective_date") {
            source_effective_dates.insert(value);
        }
        if let Some(value) = non_empty_property(feature, "licence") {
            source_licences.insert(value);
        }
        source_classifications.insert(classification.clone());
        let identity = road_identity(feature, &classification);
        for line in lines {
            let geometry = geos_line(line)?;
            road_linework.push(Clone::clone(&geometry));
            roads.push(ClassifiedRoad {
                geometry,
                identity: identity.clone(),
            });
        }
    }
    if road_linework.is_empty() {
        return Ok(Vec::new());
    }

    let mut extents_by_source_id = BTreeMap::new();
    for area in built_up_areas {
        extents_by_source_id
            .entry(area.source_id.as_str())
            .or_insert(area);
    }

    let projector = Projector::new()?;
    let mut candidates = Vec::new();
    for area in extents_by_source_id.into_values() {
        let extent_geometry = GeosGeometry::new_from_geojson(
            &json!({
                "type": "MultiPolygon",
                "coordinates": area.geometry,
            })
            .to_string(),
        )
        .map_err(geos_error)?;
        let extent_boundary = extent_geometry.boundary().map_err(geos_error)?;
        let mut polygonizer_lines = road_linework.iter().map(Clone::clone).collect::<Vec<_>>();
        for polygon in &area.geometry {
            for ring in polygon {
                polygonizer_lines.push(geos_line(ring)?);
            }
        }

        // GEOS nodes exact intersections. The BUA boundary is the only added
        // closure linework; no precision grid, snapping, or clipping is used.
        let linework =
            GeosGeometry::create_multiline_string(polygonizer_lines).map_err(geos_error)?;
        let noded_linework = linework.unary_union().map_err(geos_error)?;
        let polygonized = GeosGeometry::polygonize(&[noded_linework]).map_err(geos_error)?;
        let polygonized_geojson: Value =
            serde_json::from_str(&polygonized.to_geojson().map_err(geos_error)?)?;
        let geometries = polygonized_geojson
            .get("geometries")
            .and_then(Value::as_array)
            .ok_or_else(|| {
                SatnError::InvalidInput(
                    "classified-road polygonizer did not return a geometry collection".to_string(),
                )
            })?;

        let mut faces = geometries
            .iter()
            .filter(|geometry| geometry.get("type").and_then(Value::as_str) == Some("Polygon"))
            .map(|geometry| {
                let face =
                    GeosGeometry::new_from_geojson(&geometry.to_string()).map_err(geos_error)?;
                let coordinates = polygon_coordinates(geometry)?;
                let mut canonical = Clone::clone(&face);
                canonical.normalize().map_err(geos_error)?;
                let canonical_key = canonical.to_geojson().map_err(geos_error)?;
                Ok(PolygonFace {
                    geometry: face,
                    coordinates,
                    canonical_key,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        faces.sort_by(|left, right| left.canonical_key.cmp(&right.canonical_key));

        let mut selected_faces = Vec::new();
        for face in &faces {
            if !face
                .geometry
                .difference(&extent_geometry)
                .map_err(geos_error)?
                .is_empty()
                .map_err(geos_error)?
            {
                continue;
            }
            let frontages = frontage_evidence(face, &roads)?;
            if frontages.len() < 2 {
                continue;
            }
            let face_boundary = face.geometry.boundary().map_err(geos_error)?;
            let urban_edge_closes_boundary = face_boundary
                .intersection(&extent_boundary)
                .map_err(geos_error)?
                .length()
                .map_err(geos_error)?
                > 0.0;
            selected_faces.push((face, frontages, urban_edge_closes_boundary));
        }

        for (index, (face, frontages, urban_edge_closes_boundary)) in
            selected_faces.into_iter().enumerate()
        {
            candidates.push(CandidateNeighbourhood {
                id: format!(
                    "candidate-neighbourhood:{}:{:03}",
                    area.source_id,
                    index + 1
                ),
                urban_extent_source_id: area.source_id.clone(),
                urban_extent_name: area.name.clone(),
                area_m2: projected_area_m2(&face.coordinates, &projector)?,
                source_dataset_ids: source_dataset_ids.iter().cloned().collect(),
                source_effective_dates: source_effective_dates.iter().cloned().collect(),
                source_licences: source_licences.iter().cloned().collect(),
                source_classifications: source_classifications.iter().cloned().collect(),
                classified_road_frontages: frontages,
                urban_edge_closes_boundary,
                urban_extent_source_dataset_id: area.source_dataset_id.clone(),
                urban_extent_source_effective_date: area.source_effective_date.clone(),
                urban_extent_source_licence: area.source_licence.clone(),
                urban_extent_source_url: area.source_url.clone(),
                urban_extent_source_attribution: area.source_attribution.clone(),
                geometry: CandidateNeighbourhoodGeometry {
                    geometry_type: "Polygon".to_string(),
                    coordinates: face.coordinates.clone(),
                },
            });
        }
    }

    Ok(candidates)
}

fn non_empty_property(feature: &Feature, key: &str) -> Option<String> {
    string_property(&feature.properties, key)
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn road_identity(feature: &Feature, classification: &str) -> Option<RoadIdentity> {
    let road_number = non_empty_property(feature, "official_road_number");
    let road_name = non_empty_property(feature, "official_road_name");
    let (key, display) = match classification {
        "a-road" | "b-road" => {
            let number = road_number.as_deref()?;
            let normalized = number
                .split_whitespace()
                .collect::<String>()
                .to_ascii_uppercase();
            (format!("number:{normalized}"), normalized)
        }
        "classified-unnumbered" => {
            let name = road_name.as_deref()?;
            let normalized = name
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ")
                .to_lowercase();
            (format!("name:{normalized}"), name.to_string())
        }
        _ => return None,
    };
    Some(RoadIdentity {
        key,
        display,
        classification: classification.to_string(),
        road_number,
        road_name,
    })
}

fn frontage_evidence(face: &PolygonFace, roads: &[ClassifiedRoad]) -> Result<Vec<String>> {
    let boundary = face.geometry.boundary().map_err(geos_error)?;
    let mut frontages: BTreeMap<String, (RoadIdentity, BTreeSet<String>)> = BTreeMap::new();
    for road in roads {
        let Some(identity) = &road.identity else {
            continue;
        };
        let overlap = boundary
            .intersection(&road.geometry)
            .map_err(geos_error)?
            .length()
            .map_err(geos_error)?;
        if overlap <= 0.0 {
            continue;
        }
        let entry = frontages
            .entry(identity.key.clone())
            .or_insert_with(|| (identity.clone(), BTreeSet::new()));
        if let Some(name) = &identity.road_name {
            entry.1.insert(name.clone());
        }
    }

    Ok(frontages
        .into_values()
        .map(|(identity, names)| {
            let mut evidence = format!("{} {}", identity.classification, identity.display);
            let names = names.into_iter().collect::<Vec<_>>();
            if !names.is_empty() && identity.road_number.is_some() {
                evidence.push_str(&format!(" ({})", names.join(", ")));
            }
            evidence
        })
        .collect())
}

fn source_line_parts(geometry: &SourceGeometry) -> Vec<&[[f64; 2]]> {
    match geometry {
        SourceGeometry::LineString(line) => vec![line],
        SourceGeometry::MultiLineString(lines) => lines.iter().map(Vec::as_slice).collect(),
        SourceGeometry::Point(_) | SourceGeometry::Polygon(_) | SourceGeometry::MultiPolygon(_) => {
            Vec::new()
        }
    }
}

fn geos_line(coordinates: &[[f64; 2]]) -> Result<GeosGeometry> {
    let geometry = json!({
        "type": "LineString",
        "coordinates": coordinates,
    });
    GeosGeometry::new_from_geojson(&geometry.to_string()).map_err(geos_error)
}

fn polygon_coordinates(geometry: &Value) -> Result<Vec<Vec<[f64; 2]>>> {
    geometry
        .get("coordinates")
        .cloned()
        .ok_or_else(|| {
            SatnError::InvalidInput("classified-road polygon has no coordinates".to_string())
        })
        .and_then(|coordinates| serde_json::from_value(coordinates).map_err(SatnError::Json))
}

fn projected_area_m2(rings: &[Vec<[f64; 2]>], projector: &Projector) -> Result<f64> {
    let mut projected_rings = rings
        .iter()
        .map(|ring| {
            ring.iter()
                .copied()
                .map(|coordinate| projector.point(coordinate).map(|[x, y]| Coord { x, y }))
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()?;
    if projected_rings.is_empty() {
        return Ok(0.0);
    }
    let exterior = GeoLineString::new(projected_rings.remove(0));
    let interiors = projected_rings
        .into_iter()
        .map(GeoLineString::new)
        .collect();
    Ok(GeoPolygon::new(exterior, interiors).unsigned_area())
}

fn geos_error(error: geos::Error) -> SatnError {
    SatnError::InvalidInput(format!(
        "classified-road geometry operation failed: {error}"
    ))
}

#[cfg(test)]
mod containment_tests {
    use super::*;

    #[test]
    fn polygonized_face_with_two_distinct_frontages_survives_extent_containment() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/candidate-neighbourhood-containment.json"
        ))
        .expect("actual BUA and polygonized-face fixture");
        let area = CandidateBuiltUpArea {
            source_id: "E63005227".to_string(),
            name: "Bath".to_string(),
            geometry: serde_json::from_value(fixture["extent"]["coordinates"].clone())
                .expect("Bath BUA coordinates"),
            source_dataset_id: None,
            source_effective_date: None,
            source_licence: None,
            source_url: None,
            source_attribution: None,
        };
        let rings: Vec<Vec<[f64; 2]>> =
            serde_json::from_value(fixture["face"]["coordinates"].clone())
                .expect("polygonized face coordinates");
        let boundary = &rings[0];
        let midpoint = boundary.len() / 2;
        let road = |classification: &str, number: &str, coordinates: Vec<[f64; 2]>| Feature {
            properties: BTreeMap::from([
                (
                    "official_classification".to_string(),
                    Value::String(classification.to_string()),
                ),
                (
                    "official_road_number".to_string(),
                    Value::String(number.to_string()),
                ),
            ]),
            geometry: SourceGeometry::LineString(coordinates),
        };
        let outside_ring = vec![
            [-2.51, 51.45],
            [-2.50, 51.45],
            [-2.50, 51.46],
            [-2.51, 51.46],
            [-2.51, 51.45],
        ];
        let mut roads = vec![
            road("a-road", "A3062", boundary[..=midpoint].to_vec()),
            road("b-road", "B3110", boundary[midpoint..].to_vec()),
        ];
        roads.extend([
            road(
                "a-road",
                "A4",
                vec![outside_ring[0], outside_ring[1], outside_ring[2]],
            ),
            road(
                "b-road",
                "B3110",
                vec![outside_ring[2], outside_ring[3], outside_ring[4]],
            ),
        ]);
        let extent_geometry = GeosGeometry::new_from_geojson(&fixture["extent"].to_string())
            .expect("Bath BUA extent geometry");
        let outside_geometry = GeosGeometry::new_from_geojson(
            &json!({
                "type": "Polygon",
                "coordinates": [outside_ring.clone()],
            })
            .to_string(),
        )
        .expect("road-enclosed outside face");
        let outside_face = PolygonFace {
            geometry: Clone::clone(&outside_geometry),
            coordinates: vec![outside_ring],
            canonical_key: String::new(),
        };
        let outside_roads = roads[2..]
            .iter()
            .map(|feature| {
                let classification =
                    string_property(&feature.properties, "official_classification")
                        .expect("outside road classification");
                let line = source_line_parts(&feature.geometry)[0];
                ClassifiedRoad {
                    geometry: geos_line(line).expect("outside road geometry"),
                    identity: road_identity(feature, &classification),
                }
            })
            .collect::<Vec<_>>();
        assert_eq!(
            frontage_evidence(&outside_face, &outside_roads)
                .expect("outside frontage evidence")
                .len(),
            2,
            "the excluded outside face otherwise has two classified-road frontages"
        );
        assert!(
            !outside_geometry
                .difference(&extent_geometry)
                .expect("outside face minus BUA extent")
                .is_empty()
                .expect("outside difference emptiness"),
            "outside fixture face must have geometry beyond the BUA extent"
        );
        let candidates = derive_candidate_neighbourhoods(&roads, &[area])
            .expect("derive Bath candidate neighbourhoods");
        let point = GeosGeometry::new_from_geojson(
            r#"{"type":"Point","coordinates":[-2.3550699,51.3702347]}"#,
        )
        .expect("Lyncombe Vale point");
        let lyncombe_vale = candidates
            .iter()
            .find(|candidate| {
                let geometry = GeosGeometry::new_from_geojson(
                    &json!({
                        "type": "Polygon",
                        "coordinates": candidate.geometry.coordinates,
                    })
                    .to_string(),
                )
                .expect("candidate geometry");
                geometry.covers(&point).expect("point-in-polygon relation")
            })
            .expect("retain candidate covering Lyncombe Vale");
        assert_eq!(lyncombe_vale.classified_road_frontages.len(), 2);

        let outside_point =
            GeosGeometry::new_from_geojson(r#"{"type":"Point","coordinates":[-2.505,51.455]}"#)
                .expect("point outside the Bath extent");
        assert!(!candidates.iter().any(|candidate| {
            let geometry = GeosGeometry::new_from_geojson(
                &json!({
                    "type": "Polygon",
                    "coordinates": candidate.geometry.coordinates,
                })
                .to_string(),
            )
            .expect("candidate geometry");
            geometry
                .covers(&outside_point)
                .expect("outside point-in-polygon relation")
        }));
    }
}
