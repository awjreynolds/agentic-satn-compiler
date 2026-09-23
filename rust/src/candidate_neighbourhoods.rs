//! Complete planar faces formed from classified roads and selected by urban extents.

use std::collections::{BTreeMap, BTreeSet};

use geo::{Area, Coord, LineString as GeoLineString, Polygon as GeoPolygon};
use geos::{Geom, Geometry as GeosGeometry};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::compiler::UrbanExtent;
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
    pub geometry: CandidateNeighbourhoodGeometry,
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

/// Polygonize all official A, B, and Classified Unnumbered linework, then
/// retain each complete face intersecting an admitted urban extent.
pub(crate) fn derive_candidate_neighbourhoods(
    official_features: &[Feature],
    urban_extents: &[UrbanExtent],
) -> Result<Vec<CandidateNeighbourhood>> {
    if official_features.is_empty() || urban_extents.is_empty() {
        return Ok(Vec::new());
    }

    let mut polygonizer_lines = Vec::new();
    let mut source_dataset_ids = BTreeSet::new();
    let mut source_effective_dates = BTreeSet::new();
    let mut source_licences = BTreeSet::new();
    let mut source_classifications = BTreeSet::new();
    for feature in official_features {
        let classification = string_property(&feature.properties, "official_classification")
            .unwrap_or_default()
            .to_ascii_lowercase();
        if !INCLUDED_CLASSIFICATIONS.contains(&classification.as_str()) {
            continue;
        }
        let lines = source_line_parts(&feature.geometry);
        if lines.is_empty() {
            continue;
        }
        if let Some(value) = string_property(&feature.properties, "source_id") {
            source_dataset_ids.insert(value);
        }
        if let Some(value) = string_property(&feature.properties, "effective_date") {
            source_effective_dates.insert(value);
        }
        if let Some(value) = string_property(&feature.properties, "licence") {
            source_licences.insert(value);
        }
        source_classifications.insert(classification);
        for line in lines {
            polygonizer_lines.push(geos_line(line)?);
        }
    }
    if polygonizer_lines.is_empty() {
        return Ok(Vec::new());
    }

    // GEOS unary union nodes exact line intersections before polygonization.
    // No precision grid, snapping, clipping, or non-road closure geometry is introduced.
    let linework = GeosGeometry::create_multiline_string(polygonizer_lines).map_err(geos_error)?;
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
            let face = GeosGeometry::new_from_geojson(&geometry.to_string()).map_err(geos_error)?;
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

    let mut extents_by_source_id = BTreeMap::new();
    for extent in urban_extents {
        extents_by_source_id
            .entry(extent.source_id.as_str())
            .or_insert(extent);
    }

    let projector = Projector::new()?;
    let mut candidates = Vec::new();
    for extent in extents_by_source_id.into_values() {
        let extent_geometry = GeosGeometry::new_from_geojson(
            &json!({
                "type": "MultiPolygon",
                "coordinates": extent.geometry,
            })
            .to_string(),
        )
        .map_err(geos_error)?;
        let mut selected_faces = Vec::new();
        for face in &faces {
            if !face
                .geometry
                .intersects(&extent_geometry)
                .map_err(geos_error)?
            {
                continue;
            }
            selected_faces.push(face);
        }

        for (index, face) in selected_faces.into_iter().enumerate() {
            candidates.push(CandidateNeighbourhood {
                id: format!(
                    "candidate-neighbourhood:{}:{:03}",
                    extent.source_id,
                    index + 1
                ),
                urban_extent_source_id: extent.source_id.clone(),
                urban_extent_name: extent.name.clone(),
                area_m2: projected_area_m2(&face.coordinates, &projector)?,
                source_dataset_ids: source_dataset_ids.iter().cloned().collect(),
                source_effective_dates: source_effective_dates.iter().cloned().collect(),
                source_licences: source_licences.iter().cloned().collect(),
                source_classifications: source_classifications.iter().cloned().collect(),
                geometry: CandidateNeighbourhoodGeometry {
                    geometry_type: "Polygon".to_string(),
                    coordinates: face.coordinates.clone(),
                },
            });
        }
    }

    Ok(candidates)
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
