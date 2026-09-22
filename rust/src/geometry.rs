use geo::{
    BooleanOps, BoundingRect, Buffer, Coord, Euclidean, Length, LineString, MultiLineString,
    MultiPolygon,
};
use proj4rs::Proj;
use proj4rs::adaptors::transform_vertex_2d;
use rstar::{AABB, RTree, RTreeObject};

use crate::error::{Result, SatnError};
use crate::geojson::{Feature, Geometry, canonical_tag_values};

/// The reference currently uses PROJ's first available EPSG:27700 fallback
/// when OSTN15 grids are unavailable. Keep the fallback explicit and stable:
/// this is a seven-parameter Helmert transformation plus the Airy/tmerc
/// projection, not an OSTN15 claim.
pub const GRIDLESS_BNG_PROJECTION_POLICY: &str = "EPSG:4326 to EPSG:27700 using the explicit Airy/tmerc gridless Helmert fallback; OSTN15 is not claimed";

const WGS84: &str = "+proj=longlat +datum=WGS84";
// These are the OSGB36 -> WGS84 +towgs84 parameters from the observed PROJ
// position-vector pipeline. proj4rs applies the inverse when the destination
// datum is OSGB36, matching the observed WGS84 -> BNG fallback direction.
const GRIDLESS_BNG: &str = concat!(
    "+proj=tmerc +lat_0=49 +lon_0=-2 +k=0.9996012717 ",
    "+x_0=400000 +y_0=-100000 +ellps=airy +units=m ",
    "+towgs84=446.448,-125.157,542.060,0.15,0.247,0.842,-20.489"
);

const BUFFER_M: f64 = 20.0;
const OVERLAP_SHARE: f64 = 0.5;
const STRATEGIC_TYPES: [(&str, &str); 3] = [
    ("ncn-route", "current-ncn"),
    ("declassified-ncn-route", "reclassified-ncn"),
    ("greenway-cycleway", "greenway"),
];

#[derive(Debug, Clone, Default)]
pub(crate) struct EdgeEvidence {
    pub ncn: bool,
    pub cycle_alignment_bases: Vec<String>,
}

#[derive(Clone)]
struct Projector {
    source: Proj,
    target: Proj,
}

impl Projector {
    fn new() -> Result<Self> {
        let source = Proj::from_proj_string(WGS84)
            .map_err(|error| SatnError::InvalidInput(format!("WGS84 projection: {error}")))?;
        let target = Proj::from_proj_string(GRIDLESS_BNG)
            .map_err(|error| SatnError::InvalidInput(format!("BNG projection: {error}")))?;
        Ok(Self { source, target })
    }

    fn point(&self, point: [f64; 2]) -> Result<[f64; 2]> {
        let (x, y) = transform_vertex_2d(
            &self.source,
            &self.target,
            (point[0].to_radians(), point[1].to_radians()),
        )
        .map_err(|error| {
            SatnError::InvalidInput(format!("WGS84 to BNG transformation failed: {error}"))
        })?;
        Ok([x, y])
    }

    fn line(&self, line: &[[f64; 2]]) -> Result<LineString<f64>> {
        line.iter()
            .copied()
            .map(|point| self.point(point).map(|[x, y]| Coord { x, y }))
            .collect::<Result<Vec<_>>>()
            .map(LineString::new)
    }
}

/// Transform one WGS84 lon/lat point with the frozen gridless BNG policy.
pub fn project_wgs84_to_bng(point: [f64; 2]) -> Result<[f64; 2]> {
    Projector::new()?.point(point)
}

#[derive(Debug, Clone)]
struct Corridor {
    basis: &'static str,
    buffered: MultiPolygon<f64>,
    envelope: AABB<[f64; 2]>,
}

impl RTreeObject for Corridor {
    type Envelope = AABB<[f64; 2]>;

    fn envelope(&self) -> Self::Envelope {
        self.envelope
    }
}

/// Derive NCN alignment evidence from source context using the reference
/// metric rule: buffer selected context linework by 20m, then mark a network
/// edge when at least half its projected length overlaps the union.
pub(crate) fn enrich_network_edges(
    network_features: &[Feature],
    context_features: &[Feature],
) -> Result<Vec<EdgeEvidence>> {
    let mut evidence = network_features
        .iter()
        .map(|feature| EdgeEvidence {
            ncn: raw_current_ncn(feature),
            cycle_alignment_bases: Vec::new(),
        })
        .collect::<Vec<_>>();
    let projector = Projector::new()?;
    for item in evidence.iter_mut().filter(|item| item.ncn) {
        item.cycle_alignment_bases.push("current-ncn".to_string());
    }

    let mut corridors = Vec::new();
    for feature in context_features {
        let Some(basis) = strategic_basis(feature) else {
            continue;
        };
        for line in line_geometries(feature) {
            let projected = projector.line(&line)?;
            let buffered = projected.buffer(BUFFER_M);
            let Some(rect) = buffered.bounding_rect() else {
                continue;
            };
            corridors.push(Corridor {
                basis,
                buffered,
                envelope: AABB::from_corners(
                    [rect.min().x, rect.min().y],
                    [rect.max().x, rect.max().y],
                ),
            });
        }
    }
    if corridors.is_empty() {
        return Ok(evidence);
    }
    let tree = RTree::bulk_load(corridors);
    for (index, feature) in network_features.iter().enumerate() {
        let Geometry::LineString(line) = &feature.geometry else {
            continue;
        };
        if line.len() < 2 {
            continue;
        }
        let projected = projector.line(line)?;
        let Some(rect) = projected.bounding_rect() else {
            continue;
        };
        let edge_envelope =
            AABB::from_corners([rect.min().x, rect.min().y], [rect.max().x, rect.max().y]);
        let nearby = tree
            .locate_in_envelope_intersecting(edge_envelope)
            .collect::<Vec<_>>();
        if nearby.is_empty() {
            continue;
        }
        let route = MultiLineString(vec![projected]);
        let route_length = Euclidean.length(&route);
        if route_length == 0.0 {
            continue;
        }
        let all_buffers = nearby.iter().map(|corridor| &corridor.buffered);
        let corridor = geo::unary_union(all_buffers);
        let overlap_length = Euclidean.length(&corridor.clip(&route, false));
        if overlap_length / route_length >= OVERLAP_SHARE {
            evidence[index].ncn = true;
        }
        for (feature_type, basis) in STRATEGIC_TYPES {
            let typed_buffers = nearby
                .iter()
                .filter(|corridor| corridor.basis == basis)
                .map(|corridor| &corridor.buffered)
                .collect::<Vec<_>>();
            if typed_buffers.is_empty() {
                continue;
            }
            let typed_corridor = geo::unary_union(typed_buffers);
            let typed_overlap = Euclidean.length(&typed_corridor.clip(&route, false));
            if typed_overlap / route_length >= OVERLAP_SHARE
                && !evidence[index]
                    .cycle_alignment_bases
                    .iter()
                    .any(|existing| existing == basis)
            {
                // Keep the feature-type mapping visible in the implementation;
                // the basis string is the stable value exposed in reports.
                let _ = feature_type;
                evidence[index]
                    .cycle_alignment_bases
                    .push(basis.to_string());
            }
        }
    }
    Ok(evidence)
}

fn raw_current_ncn(feature: &Feature) -> bool {
    canonical_tag_values(&feature.properties, "ncn")
        .iter()
        .any(|value| value.eq_ignore_ascii_case("yes"))
}

fn strategic_basis(feature: &Feature) -> Option<&'static str> {
    canonical_tag_values(&feature.properties, "feature_type")
        .iter()
        .find_map(|feature_type| {
            STRATEGIC_TYPES
                .iter()
                .find(|(name, _)| *name == feature_type)
                .map(|(_, basis)| *basis)
        })
}

fn line_geometries(feature: &Feature) -> Vec<Vec<[f64; 2]>> {
    match &feature.geometry {
        Geometry::LineString(line) => vec![line.clone()],
        Geometry::MultiLineString(lines) => lines.clone(),
        _ => Vec::new(),
    }
}
