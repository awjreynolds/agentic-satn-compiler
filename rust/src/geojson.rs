use std::collections::BTreeMap;
use std::path::Path;

use serde_json::Value;

use crate::error::{Result, SatnError};

pub(crate) type Properties = BTreeMap<String, Value>;

#[derive(Debug, Clone)]
pub(crate) enum Geometry {
    Point([f64; 2]),
    LineString(Vec<[f64; 2]>),
    MultiLineString(Vec<Vec<[f64; 2]>>),
    Polygon(Vec<Vec<[f64; 2]>>),
    MultiPolygon(Vec<Vec<Vec<[f64; 2]>>>),
}

#[derive(Debug, Clone)]
pub(crate) struct Feature {
    pub properties: Properties,
    pub geometry: Geometry,
}

pub(crate) fn read_feature_collection(path: &Path) -> Result<Vec<Feature>> {
    let text = std::fs::read_to_string(path)?;
    let document: Value = serde_json::from_str(&text)?;
    let features = document
        .get("features")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            SatnError::InvalidInput(format!(
                "{} is not a GeoJSON FeatureCollection",
                path.display()
            ))
        })?;

    features.iter().map(parse_feature).collect()
}

fn parse_feature(value: &Value) -> Result<Feature> {
    let properties = value
        .get("properties")
        .and_then(Value::as_object)
        .map(|object| {
            object
                .iter()
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect()
        })
        .unwrap_or_default();
    let geometry = value
        .get("geometry")
        .ok_or_else(|| SatnError::InvalidInput("GeoJSON feature has no geometry".to_string()))?;
    let geometry_type = geometry
        .get("type")
        .and_then(Value::as_str)
        .ok_or_else(|| SatnError::InvalidInput("GeoJSON geometry has no type".to_string()))?;
    let coordinates = geometry.get("coordinates").ok_or_else(|| {
        SatnError::InvalidInput("GeoJSON geometry has no coordinates".to_string())
    })?;

    let geometry = match geometry_type {
        "Point" => Geometry::Point(parse_position(coordinates)?),
        "LineString" => Geometry::LineString(parse_line(coordinates, "LineString")?),
        "MultiLineString" => {
            let lines = coordinates.as_array().ok_or_else(|| {
                SatnError::InvalidInput("MultiLineString coordinates must be an array".to_string())
            })?;
            Geometry::MultiLineString(
                lines
                    .iter()
                    .map(|line| parse_line(line, "MultiLineString line"))
                    .collect::<Result<Vec<_>>>()?,
            )
        }
        "Polygon" => Geometry::Polygon(parse_polygon(coordinates, "Polygon")?),
        "MultiPolygon" => {
            let polygons = coordinates.as_array().ok_or_else(|| {
                SatnError::InvalidInput("MultiPolygon coordinates must be an array".to_string())
            })?;
            Geometry::MultiPolygon(
                polygons
                    .iter()
                    .map(|polygon| parse_polygon(polygon, "MultiPolygon polygon"))
                    .collect::<Result<Vec<_>>>()?,
            )
        }
        other => {
            return Err(SatnError::InvalidInput(format!(
                "unsupported GeoJSON geometry type {other}"
            )));
        }
    };

    Ok(Feature {
        properties,
        geometry,
    })
}

fn parse_line(value: &Value, label: &str) -> Result<Vec<[f64; 2]>> {
    let positions = value
        .as_array()
        .ok_or_else(|| SatnError::InvalidInput(format!("{label} coordinates must be an array")))?;
    if positions.len() < 2 {
        return Err(SatnError::InvalidInput(format!(
            "{label} must contain at least two positions"
        )));
    }
    positions
        .iter()
        .map(parse_position)
        .collect::<Result<Vec<_>>>()
}

fn parse_polygon(value: &Value, label: &str) -> Result<Vec<Vec<[f64; 2]>>> {
    let rings = value
        .as_array()
        .ok_or_else(|| SatnError::InvalidInput(format!("{label} coordinates must be an array")))?;
    rings
        .iter()
        .map(|ring| {
            let positions = ring
                .as_array()
                .ok_or_else(|| SatnError::InvalidInput(format!("{label} ring must be an array")))?;
            if positions.len() < 4 {
                return Err(SatnError::InvalidInput(format!(
                    "{label} ring must contain at least four positions"
                )));
            }
            positions
                .iter()
                .map(parse_position)
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()
}

fn parse_position(value: &Value) -> Result<[f64; 2]> {
    let values = value
        .as_array()
        .ok_or_else(|| SatnError::InvalidInput("GeoJSON position must be an array".to_string()))?;
    if values.len() < 2 {
        return Err(SatnError::InvalidInput(
            "GeoJSON position requires longitude and latitude".to_string(),
        ));
    }
    let longitude = values[0]
        .as_f64()
        .ok_or_else(|| SatnError::InvalidInput("longitude must be numeric".to_string()))?;
    let latitude = values[1]
        .as_f64()
        .ok_or_else(|| SatnError::InvalidInput("latitude must be numeric".to_string()))?;
    if !longitude.is_finite() || !latitude.is_finite() {
        return Err(SatnError::InvalidInput(
            "GeoJSON coordinates must be finite".to_string(),
        ));
    }
    Ok([longitude, latitude])
}

pub(crate) fn string_property(properties: &Properties, key: &str) -> Option<String> {
    properties.get(key).and_then(|value| match value {
        Value::String(value) => Some(value.clone()),
        Value::Number(value) => Some(value.to_string()),
        Value::Bool(value) => Some(value.to_string()),
        Value::Array(values) => Some(
            values
                .iter()
                .map(|value| match value {
                    Value::String(value) => value.clone(),
                    other => other.to_string(),
                })
                .collect::<Vec<_>>()
                .join(","),
        ),
        _ => None,
    })
}

pub(crate) fn number_property(properties: &Properties, key: &str) -> Option<f64> {
    properties.get(key).and_then(|value| match value {
        Value::Number(value) => value.as_f64(),
        Value::String(value) => value.parse().ok(),
        _ => None,
    })
}

pub(crate) fn property_text(properties: &Properties, key: &str) -> String {
    string_property(properties, key).unwrap_or_else(|| "unknown".to_string())
}
