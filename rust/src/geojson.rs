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

/// Decode source tags the same way as the reference compiler's tag layer.
///
/// GeoJSON snapshots contain scalar values, JSON arrays, and values which have
/// made a round trip through a Python repr (for example, `['A4', 'A36']`).
/// Keep this seam separate from `string_property`: callers which need one
/// display string should retain the existing scalar behaviour, while graph
/// policy must see each canonical tag value independently.
pub(crate) fn canonical_tag_values(properties: &Properties, key: &str) -> Vec<String> {
    properties
        .get(key)
        .map(tag_values)
        .unwrap_or_default()
        .into_iter()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty() && !is_missing_marker(value))
        .collect()
}

fn tag_values(value: &Value) -> Vec<String> {
    match value {
        Value::Null => Vec::new(),
        Value::Array(values) => values.iter().map(value_text).collect(),
        Value::String(text) => {
            parse_stringified_collection(text).unwrap_or_else(|| vec![text.clone()])
        }
        other => vec![value_text(other)],
    }
}

fn value_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Number(number) => number.to_string(),
        Value::Bool(value) => value.to_string(),
        other => other.to_string(),
    }
}

fn is_missing_marker(value: &str) -> bool {
    matches!(value.to_ascii_lowercase().as_str(), "nan" | "none" | "<na>")
}

fn parse_stringified_collection(text: &str) -> Option<Vec<String>> {
    let trimmed = text.trim();
    let (opening, closing) = match trimmed.as_bytes() {
        [b'[', ..] if trimmed.ends_with(']') => ('[', ']'),
        [b'(', ..] if trimmed.ends_with(')') => ('(', ')'),
        [b'{', ..] if trimmed.ends_with('}') => ('{', '}'),
        _ => return None,
    };
    let inner = trimmed[opening.len_utf8()..trimmed.len() - closing.len_utf8()].trim();
    if inner.is_empty() {
        return Some(Vec::new());
    }
    // Python's repr uses single quotes and sets use braces. This parser only
    // needs flat source tags; commas inside quoted tags are retained.
    let mut values = Vec::new();
    let mut current = String::new();
    let mut quote = None;
    for character in inner.chars() {
        match (quote, character) {
            (Some(active), value) if value == active => quote = None,
            (None, '\'' | '"') => quote = Some(character),
            (None, ',') => {
                values.push(clean_collection_item(&current));
                current.clear();
            }
            _ => current.push(character),
        }
    }
    values.push(clean_collection_item(&current));
    values.retain(|value| !value.is_empty());
    if opening == '{' {
        values.sort();
    }
    Some(values)
}

fn clean_collection_item(value: &str) -> String {
    value
        .trim()
        .trim_matches(|character| character == '\'' || character == '"')
        .trim()
        .to_string()
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
