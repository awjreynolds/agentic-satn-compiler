"""Derive quiet, optional map evidence without turning amenities into Network Places."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Literal

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import linemerge

from satn.models import NetworkScope
from satn.osm_active_travel import (
    OSM_ACTIVE_TRAVEL_ASSET_KINDS,
    network_kind,
)
from satn.tags import canonical_tag_values, source_identity
from satn.tags import tag_values as _tag_values

SUBSTANTIAL_CIRCULATION_BOUNDARY_M = 250.0
STRATEGIC_CYCLE_ROUTE_TYPES = {
    "ncn-route",
    "declassified-ncn-route",
    "greenway-cycleway",
}
CYCLE_ALIGNMENT_BASIS_BY_FEATURE_TYPE = {
    "ncn-route": "current-ncn",
    "declassified-ncn-route": "reclassified-ncn",
    "greenway-cycleway": "greenway",
}
PUBLIC_CYCLE_ROUTE_TYPES = {*STRATEGIC_CYCLE_ROUTE_TYPES, "ncn-link"}
OFFICIAL_ROAD_DISAGREEMENT_TOLERANCE_M = 25.0
RoadClassificationDisagreementType = Literal[
    "official-non-a-road",
    "no-overlapping-official-road",
]

CONTEXT_COLUMNS = [
    "evidence_id",
    "feature_type",
    "name",
    "category",
    "source_id",
    "feature_count",
    "network_scope",
    "ncn_evidence_role",
    "asset_kind",
    "current_cycle_asset",
    "school_kind",
    "school_obligation_eligible",
    "access_point_status",
    "access_point_source_id",
    "access_point_rationale",
    "geometry",
]


@dataclass(frozen=True)
class RoadClassificationDisagreement:
    """One inspectable OSM/official hierarchy disagreement."""

    disagreement_type: RoadClassificationDisagreementType
    osm_evidence_id: str
    osm_source_id: str
    official_feature_id: str | None
    official_classification: str | None
    official_source_id: str | None
    official_content_fingerprint: str | None

    def __post_init__(self) -> None:
        official_values = (
            self.official_feature_id,
            self.official_classification,
            self.official_source_id,
            self.official_content_fingerprint,
        )
        if self.disagreement_type == "no-overlapping-official-road":
            if any(value is not None for value in official_values):
                raise ValueError("unmatched OSM road disagreement cannot name an official road")
        elif self.disagreement_type == "official-non-a-road":
            if any(value is None for value in official_values):
                raise ValueError("official road disagreement requires complete official provenance")
        else:
            raise ValueError("unsupported road classification disagreement type")

    def canonical(self) -> dict[str, str | None]:
        """Return the JSON-safe diagnostic record exposed by compilation."""

        return {
            "disagreement_type": self.disagreement_type,
            "osm_evidence_id": self.osm_evidence_id,
            "osm_source_id": self.osm_source_id,
            "official_feature_id": self.official_feature_id,
            "official_classification": self.official_classification,
            "official_source_id": self.official_source_id,
            "official_content_fingerprint": self.official_content_fingerprint,
        }


def empty_context(crs: object = 4326) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(columns=CONTEXT_COLUMNS, geometry="geometry", crs=crs)


def derive_context_layers(
    network: gpd.GeoDataFrame,
    ncn_features: gpd.GeoDataFrame | None = None,
    facilities: gpd.GeoDataFrame | None = None,
    circulation_boundaries: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """Build the map hierarchy from OSM road, route and amenity evidence."""
    frames = [derive_a_road_spines(network), derive_osm_active_travel_assets(network)]
    if ncn_features is not None and not ncn_features.empty:
        frames.append(derive_ncn_routes(ncn_features, network.crs))
    if facilities is not None and not facilities.empty:
        frames.append(derive_facilities(facilities, network, network.crs))
    if circulation_boundaries is not None and not circulation_boundaries.empty:
        frames.append(derive_circulation_boundaries(circulation_boundaries, network.crs))
    populated = [frame for frame in frames if not frame.empty]
    if not populated:
        return empty_context(network.crs)
    return gpd.GeoDataFrame(
        pd.concat(populated, ignore_index=True),
        columns=CONTEXT_COLUMNS,
        geometry="geometry",
        crs=network.crs,
    ).sort_values("evidence_id")


def derive_osm_active_travel_assets(network: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Expose explicitly cycle-capable OSM ways as non-strategic context assets."""
    rows: list[dict[str, object]] = []
    for index, edge in network.iterrows():
        kind = network_kind(edge)
        if kind not in OSM_ACTIVE_TRAVEL_ASSET_KINDS:
            continue
        geometry = edge.geometry
        if not isinstance(geometry, (LineString, MultiLineString)) or geometry.is_empty:
            continue
        source_id = _source_id(edge, index)
        feature_type = {
            "mapped-cycleway": "cycleway",
            "cycle-track": "cycleway",
            "road-cycleway": "road-cycleway",
            "bicycle-priority-road": "bicycle-priority-road",
            "bicycle-route": "bicycle-route",
            "cycle-access-path": "cycle-access-path",
            "shared-use-path": "shared-use-path",
            "public-bridleway": "bridleway",
            "proposed-new-corridor": "proposed-cycleway",
        }[kind]
        rows.append(
            _row(
                feature_type,
                source_id,
                _text(edge.get("name")) or f"Mapped {feature_type}",
                "Mapped OSM active-travel asset",
                source_id,
                geometry,
                asset_kind=kind,
                current_cycle_asset=kind
                in {
                    "mapped-cycleway",
                    "cycle-track",
                    "road-cycleway",
                    "bicycle-priority-road",
                    "bicycle-route",
                    "cycle-access-path",
                    "shared-use-path",
                }
                or (
                    kind == "public-bridleway"
                    and "no" not in {value.lower() for value in _tag_values(edge.get("bicycle"))}
                ),
            )
        )
    return _frame(rows, network.crs)


def govern_a_road_context(
    context: gpd.GeoDataFrame,
    official_classification: gpd.GeoDataFrame | None,
) -> tuple[gpd.GeoDataFrame, list[dict[str, str | None]]]:
    """Replace provisional OSM A-road context with governed official A roads."""
    if official_classification is None or official_classification.empty:
        return context.copy(), []

    feature_type = context.get("feature_type", pd.Series("", index=context.index, dtype=object))
    osm_a_roads = context[feature_type.eq("a-road-spine")].copy()
    other_context = context[~feature_type.eq("a-road-spine")].copy()
    official_a_roads = official_classification[
        official_classification["official_classification"].eq("a-road")
    ].copy()
    rows = [
        {
            "evidence_id": str(feature["official_feature_id"]),
            "feature_type": "a-road-spine",
            "name": str(
                feature.get("official_road_number")
                or feature.get("official_road_name")
                or feature["official_feature_id"]
            ),
            "category": "Governed official A-road strategic spine",
            "source_id": str(feature["source_id"]),
            "feature_count": 1,
            "network_scope": NetworkScope.UNRESOLVED.value,
            "geometry": feature.geometry,
        }
        for _, feature in official_a_roads.iterrows()
        if isinstance(feature.geometry, (LineString, MultiLineString))
        and not feature.geometry.is_empty
    ]
    governed_a_roads = gpd.GeoDataFrame(
        rows,
        columns=CONTEXT_COLUMNS,
        geometry="geometry",
        crs=official_classification.crs,
    )
    if not governed_a_roads.empty:
        governed_a_roads = governed_a_roads.to_crs(context.crs)
    populated = [frame for frame in (other_context, governed_a_roads) if not frame.empty]
    governed_context = (
        gpd.GeoDataFrame(
            pd.concat(populated, ignore_index=True, sort=False),
            columns=CONTEXT_COLUMNS,
            geometry="geometry",
            crs=context.crs,
        ).sort_values("evidence_id")
        if populated
        else empty_context(context.crs)
    )
    disagreements = _road_classification_disagreements(osm_a_roads, official_classification)
    return governed_context, [disagreement.canonical() for disagreement in disagreements]


def _road_classification_disagreements(
    osm_a_roads: gpd.GeoDataFrame,
    official_classification: gpd.GeoDataFrame,
) -> tuple[RoadClassificationDisagreement, ...]:
    if osm_a_roads.empty or official_classification.empty:
        return ()
    osm = osm_a_roads.to_crs(27700)
    official = official_classification.to_crs(27700)
    spatial_index = official.sindex
    disagreements: list[RoadClassificationDisagreement] = []
    grouped = osm.groupby(["evidence_id", "source_id"], sort=True, dropna=False)
    for (evidence_id, source_id), evidence_rows in grouped:
        evidence_geometry = evidence_rows.geometry.union_all()
        search_area = evidence_geometry.buffer(OFFICIAL_ROAD_DISAGREEMENT_TOLERANCE_M)
        candidate_indexes = list(spatial_index.query(search_area, predicate="intersects"))
        if not candidate_indexes:
            disagreements.append(
                RoadClassificationDisagreement(
                    disagreement_type="no-overlapping-official-road",
                    osm_evidence_id=str(evidence_id),
                    osm_source_id=str(source_id),
                    official_feature_id=None,
                    official_classification=None,
                    official_source_id=None,
                    official_content_fingerprint=None,
                )
            )
            continue
        for candidate_index in candidate_indexes:
            candidate = official.iloc[candidate_index]
            if str(candidate["official_classification"]) == "a-road":
                continue
            disagreements.append(
                RoadClassificationDisagreement(
                    disagreement_type="official-non-a-road",
                    osm_evidence_id=str(evidence_id),
                    osm_source_id=str(source_id),
                    official_feature_id=str(candidate["official_feature_id"]),
                    official_classification=str(candidate["official_classification"]),
                    official_source_id=str(candidate["source_id"]),
                    official_content_fingerprint=str(candidate["content_fingerprint"]),
                )
            )
    return tuple(
        sorted(
            disagreements,
            key=lambda item: (
                item.osm_evidence_id,
                item.osm_source_id,
                item.disagreement_type,
                item.official_feature_id or "",
            ),
        )
    )


def govern_network_scope(
    context: gpd.GeoDataFrame,
    place_features: gpd.GeoDataFrame,
    *,
    urban_place_types: list[str],
    urban_scope_buffer_km: float,
) -> gpd.GeoDataFrame:
    """Split strategic line evidence at the configured urban extent and type each part."""
    urban = place_features[
        place_features.get("place", pd.Series("", index=place_features.index, dtype=object)).isin(
            urban_place_types
        )
    ]
    return govern_network_scope_for_urban_communities(
        context,
        urban,
        urban_scope_buffer_km=urban_scope_buffer_km,
    )


def govern_network_scope_for_urban_communities(
    context: gpd.GeoDataFrame,
    urban_communities: gpd.GeoDataFrame,
    *,
    urban_scope_buffer_km: float,
) -> gpd.GeoDataFrame:
    """Apply one already-governed Community eligibility set to context evidence."""
    urban_extent = None
    if not urban_communities.empty:
        urban_extent = (
            urban_communities.to_crs(27700)
            .geometry.buffer(urban_scope_buffer_km * 1000)
            .union_all()
        )

    strategic_types = {"a-road-spine", *PUBLIC_CYCLE_ROUTE_TYPES}
    strategic = context[context["feature_type"].isin(strategic_types)]
    valid_scopes = {scope.value for scope in NetworkScope}
    invalid_scopes = sorted(
        set(strategic.get("network_scope", pd.Series(dtype=object)).dropna().astype(str))
        - valid_scopes
    )
    if invalid_scopes:
        raise ValueError(f"invalid governed network_scope: {', '.join(invalid_scopes)}")
    other = context[~context["feature_type"].isin(strategic_types)].copy()
    school_indexes = other.index[other["feature_type"] == "school"]
    if len(school_indexes):
        projected_schools = other.loc[school_indexes].to_crs(27700)
        other.loc[school_indexes, "network_scope"] = [
            (
                NetworkScope.URBAN.value
                if urban_extent is not None and urban_extent.covers(geometry)
                else NetworkScope.RURAL.value
            )
            for geometry in projected_schools.geometry
        ]
    if strategic.empty:
        return gpd.GeoDataFrame(
            other, columns=CONTEXT_COLUMNS, geometry="geometry", crs=context.crs
        ).sort_values("evidence_id")

    rows: list[dict[str, object]] = []
    for _, evidence in strategic.to_crs(27700).iterrows():
        scoped_parts = (
            [(NetworkScope.RURAL, evidence.geometry)]
            if urban_extent is None
            else [
                (NetworkScope.RURAL, evidence.geometry.difference(urban_extent)),
                (NetworkScope.URBAN, evidence.geometry.intersection(urban_extent)),
            ]
        )
        for scope, scoped_geometry in scoped_parts:
            for geometry in continuous_linework(scoped_geometry):
                row = evidence.to_dict()
                if geometry.equals(evidence.geometry):
                    row["evidence_id"] = evidence["evidence_id"]
                else:
                    identity = hashlib.sha256(geometry.wkb).hexdigest()[:12]
                    row["evidence_id"] = f"{evidence['evidence_id']}-{scope.value}-{identity}"
                row["network_scope"] = scope.value
                row["geometry"] = geometry
                rows.append(row)

    scoped = gpd.GeoDataFrame(rows, columns=CONTEXT_COLUMNS, geometry="geometry", crs=27700)
    if not scoped.empty:
        scoped = scoped.to_crs(context.crs)
    populated = [frame for frame in (other, scoped) if not frame.empty]
    if not populated:
        return empty_context(context.crs)
    return gpd.GeoDataFrame(
        pd.concat(populated, ignore_index=True),
        columns=CONTEXT_COLUMNS,
        geometry="geometry",
        crs=context.crs,
    ).sort_values("evidence_id")


def derive_a_road_spines(network: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    rows: list[dict[str, object]] = []
    for index, edge in network.iterrows():
        refs = _tag_values(edge.get("ref"))
        a_refs = sorted(ref for ref in refs if ref.upper().startswith("A"))
        if not a_refs or not isinstance(edge.geometry, (LineString, MultiLineString)):
            continue
        source_id = _source_id(edge, index)
        rows.append(
            _row(
                "a-road-spine",
                source_id,
                " / ".join(a_refs),
                "A-road strategic spine",
                source_id,
                edge.geometry,
            )
        )
    return _frame(rows, network.crs)


def derive_ncn_routes(features: gpd.GeoDataFrame, target_crs: object) -> gpd.GeoDataFrame:
    rows: list[dict[str, object]] = []
    for index, feature in features.to_crs(target_crs).iterrows():
        geometry = feature.geometry
        if not isinstance(geometry, (LineString, MultiLineString)):
            continue
        network_tags = {value.lower() for value in _tag_values(feature.get("network"))}
        route_type = (_text(feature.get("RouteType")) or "").lower()
        is_greenway = (_text(feature.get("Greenway")) or "").lower() == "yes"
        if route_type == "link":
            feature_type = "ncn-link"
            evidence_role = "connector-link"
            category = "National Cycle Network connector link"
        elif is_greenway:
            feature_type = "greenway-cycleway"
            evidence_role = "greenway-cycleway"
            category = "Greenway cycleway"
        elif "ncn" in network_tags or route_type == "ncn":
            feature_type = "ncn-route"
            evidence_role = "established-route"
            category = "National Cycle Network"
        elif route_type == "reclassified":
            feature_type = "declassified-ncn-route"
            evidence_role = "declassified-route"
            category = "Declassified National Cycle Network route"
        else:
            continue
        source_id = _source_id(feature, index)
        ref = " / ".join(_tag_values(feature.get("ref")) or _tag_values(feature.get("RouteNo")))
        default_name = (
            f"NCN {ref} connector link"
            if evidence_role == "connector-link" and ref
            else "National Cycle Network connector link"
            if evidence_role == "connector-link"
            else f"Declassified NCN {ref}"
            if evidence_role == "declassified-route" and ref
            else "Declassified National Cycle Network route"
            if evidence_role == "declassified-route"
            else f"NCN {ref} Greenway"
            if evidence_role == "greenway-cycleway" and route_type == "ncn" and ref
            else f"Greenway {ref}"
            if evidence_role == "greenway-cycleway" and ref
            else "Greenway cycleway"
            if evidence_role == "greenway-cycleway"
            else f"NCN {ref}"
            if ref
            else "National Cycle Network"
        )
        name = _text(feature.get("name")) or default_name
        rows.append(
            _row(
                feature_type,
                source_id,
                name,
                category,
                source_id,
                geometry,
                ncn_evidence_role=evidence_role,
            )
        )
    return _frame(rows, target_crs)


def derive_circulation_boundaries(
    features: gpd.GeoDataFrame,
    target_crs: object,
) -> gpd.GeoDataFrame:
    """Admit only stable physical edges that can bound urban circulation areas."""
    rows: list[dict[str, object]] = []
    built_up_features: list[tuple[str, object]] = []
    open_land_geometries: list[object] = []
    for index, feature in features.to_crs(target_crs).iterrows():
        waterway = (_text(feature.get("waterway")) or "").lower()
        railway = (_text(feature.get("railway")) or "").lower()
        landuse = (_text(feature.get("landuse")) or "").lower()
        natural = (_text(feature.get("natural")) or "").lower()
        tunnel = (_text(feature.get("tunnel")) or "").lower()
        if landuse in {"residential", "commercial", "industrial", "retail"} and isinstance(
            feature.geometry, (Polygon, MultiPolygon)
        ):
            built_up_features.append((_source_id(feature, index), feature.geometry))
            continue
        if (
            landuse in {"farmland", "meadow", "grass", "forest", "recreation_ground"}
            or natural in {"wood", "heath", "scrub", "grassland"}
        ) and isinstance(feature.geometry, (Polygon, MultiPolygon)):
            open_land_geometries.append(feature.geometry)
            continue
        category = (
            waterway
            if waterway in {"river", "canal"} and tunnel not in {"yes", "culvert"}
            else "railway"
            if railway == "rail" and tunnel not in {"yes", "building_passage"}
            else None
        )
        if category is None:
            continue
        geometry = feature.geometry
        if isinstance(geometry, (Polygon, MultiPolygon)):
            geometry = geometry.boundary
        for position, line in enumerate(continuous_linework(geometry)):
            if not _is_substantial_circulation_boundary(line, target_crs):
                continue
            source_id = _source_id(feature, index)
            part_id = source_id if position == 0 else f"{source_id}-{position + 1}"
            name = _text(feature.get("name")) or f"Unnamed {category} boundary"
            rows.append(
                _row(
                    "circulation-boundary",
                    part_id,
                    name,
                    category,
                    source_id,
                    line,
                )
            )
    if built_up_features and open_land_geometries:
        open_land = gpd.GeoSeries(open_land_geometries, crs=target_crs).union_all()
        for source_id, geometry in sorted(built_up_features, key=lambda value: value[0]):
            verified_edge = geometry.boundary.intersection(open_land)
            for position, line in enumerate(continuous_linework(verified_edge)):
                identity = hashlib.sha256(f"{source_id}:{line.wkb_hex}".encode()).hexdigest()[:12]
                rows.append(
                    _row(
                        "circulation-boundary",
                        f"built-up-edge-{identity}-{position + 1}",
                        "Mapped built-up edge adjoining open land",
                        "built-up-edge",
                        source_id,
                        line,
                    )
                )
    return _frame(rows, target_crs)


def _is_substantial_circulation_boundary(geometry: LineString, crs: object) -> bool:
    projected = gpd.GeoSeries([geometry], crs=crs).to_crs(27700)
    return bool(projected.iloc[0].length >= SUBSTANTIAL_CIRCULATION_BOUNDARY_M)


def derive_facilities(
    features: gpd.GeoDataFrame,
    network: gpd.GeoDataFrame,
    target_crs: object,
) -> gpd.GeoDataFrame:
    source = features.to_crs(target_crs)
    projected_source = source.to_crs(27700)
    access_candidates = _school_access_candidates(source)
    projected_network = network.to_crs(27700)
    network_linework = (
        projected_network.geometry.union_all() if not projected_network.empty else None
    )
    rows: list[dict[str, object]] = []
    retail_points: list[dict[str, object]] = []
    for position, (index, feature) in enumerate(source.iterrows()):
        if feature.geometry is None or feature.geometry.is_empty:
            continue
        amenity = (_text(feature.get("amenity")) or "").lower()
        shop = _text(feature.get("shop"))
        landuse = (_text(feature.get("landuse")) or "").lower()
        source_id = _source_id(feature, index)
        point = feature.geometry.representative_point()
        name = _text(feature.get("name"))
        if amenity in {"school", "college", "university"}:
            access_point, access_status, access_source_id, access_rationale = _school_access_point(
                projected_source.iloc[position],
                access_candidates,
                network_linework,
                target_crs,
                school_source_id=source_id,
            )
            rows.append(
                _row(
                    "school",
                    source_id,
                    name or "Unnamed education site",
                    amenity,
                    source_id,
                    access_point,
                    school_kind=_school_kind(feature, amenity),
                    school_obligation_eligible=amenity == "school",
                    access_point_status=access_status,
                    access_point_source_id=access_source_id,
                    access_point_rationale=access_rationale,
                )
            )
        if amenity in {"doctors", "pharmacy", "clinic", "hospital"}:
            rows.append(
                _row(
                    "healthcare",
                    source_id,
                    name or amenity.title(),
                    amenity,
                    source_id,
                    point,
                )
            )
        if shop or landuse == "retail" or amenity == "marketplace":
            retail_points.append(
                {
                    "source_id": source_id,
                    "name": name,
                    "street": _text(feature.get("addr:street")),
                    "is_centre": landuse == "retail" or amenity == "marketplace",
                    "geometry": point,
                }
            )
    rows.extend(_retail_centres(retail_points, target_crs))
    return _frame(rows, target_crs)


def _school_access_point(
    school: pd.Series,
    access_candidates: gpd.GeoDataFrame,
    network_linework: object,
    target_crs: object,
    *,
    school_source_id: str,
) -> tuple[Point, str, str | None, str]:
    mapped: list[tuple[int, float, str, Point]] = []
    school_geometry = school.geometry
    boundary = school_geometry if isinstance(school_geometry, Point) else school_geometry.boundary
    if not access_candidates.empty:
        positions = access_candidates.sindex.query(boundary.buffer(5), predicate="intersects")
    else:
        positions = []
    for position in positions:
        candidate = access_candidates.iloc[int(position)]
        point = candidate.geometry
        explicitly_associated = str(candidate.get("school_source_id") or "") == school_source_id
        on_site_boundary = (
            not isinstance(school_geometry, Point)
            and school_geometry.buffer(3).covers(point)
            and boundary.distance(point) <= 5
        )
        adjoining_network = network_linework is not None and network_linework.distance(point) <= 20
        if not adjoining_network or not (explicitly_associated or on_site_boundary):
            continue
        distance_m = float(boundary.distance(point))
        entrance = (_text(candidate.get("entrance")) or "").lower()
        priority = 0 if entrance == "main" else 1 if entrance in {"yes", "secondary"} else 2
        mapped.append((priority, distance_m, str(candidate["source_id"]), point))
    if mapped:
        _, _, source_id, point = min(mapped, key=lambda item: (item[0], item[1], item[2]))
        output_point = gpd.GeoSeries([point], crs=27700).to_crs(target_crs).iloc[0]
        return (
            output_point,
            "mapped",
            source_id,
            "Mapped usable School entrance is associated with the site boundary and "
            "adjoining routable linework; preferred over inference.",
        )

    if not isinstance(school_geometry, Point) and network_linework is not None:
        intersections = _geometry_points(boundary.intersection(network_linework))
        if intersections:
            point = sorted(intersections, key=lambda value: value.wkb_hex)[0]
            return (
                gpd.GeoSeries([point], crs=27700).to_crs(target_crs).iloc[0],
                "inferred",
                None,
                "Inferred where routable street/path linework intersects the mapped "
                "school boundary; requires verification.",
            )

    return (
        gpd.GeoSeries([school_geometry.representative_point()], crs=27700)
        .to_crs(target_crs)
        .iloc[0],
        "unresolved",
        None,
        "No mapped usable entrance or defensible boundary/path inference is available; "
        "the representative point is context only and is not snapped to a road.",
    )


def _school_access_candidates(facilities: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Project usable access points once so each School can use the spatial index."""
    rows: list[dict[str, object]] = []
    for index, feature in facilities.iterrows():
        if not _usable_school_access(feature):
            continue
        rows.append(
            {
                "source_id": _source_id(feature, index),
                "school_source_id": _text(feature.get("school_source_id")),
                "entrance": _text(feature.get("entrance")),
                "geometry": feature.geometry.representative_point(),
            }
        )
    if not rows:
        return gpd.GeoDataFrame(
            columns=["source_id", "school_source_id", "entrance", "geometry"],
            geometry="geometry",
            crs=27700,
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=facilities.crs).to_crs(27700)


def _usable_school_access(feature: pd.Series) -> bool:
    entrance = (_text(feature.get("entrance")) or "").lower()
    barrier = (_text(feature.get("barrier")) or "").lower()
    access = (_text(feature.get("access")) or "").lower()
    foot = (_text(feature.get("foot")) or "").lower()
    if access in {"no", "private"} or foot == "no" or entrance in {"no", "emergency"}:
        return False
    return bool(entrance or barrier in {"gate", "lift_gate", "swing_gate"})


def _school_kind(feature: pd.Series, amenity: str) -> str:
    if amenity != "school":
        return amenity
    values = " ".join(
        filter(
            None,
            (
                _text(feature.get("school")),
                _text(feature.get("school:type")),
                _text(feature.get("designation")),
                _text(feature.get("name")),
            ),
        )
    ).lower()
    special_needs = (_text(feature.get("special_needs")) or "").lower()
    if special_needs in {"yes", "only", "designated"} or "special" in values:
        return "special"
    if "all_through" in values or "all-through" in values:
        return "all-through"
    levels = {
        level.strip()
        for value in _tag_values(feature.get("isced:level"))
        for level in value.replace(",", ";").split(";")
        if level.strip()
    }
    if "primary" in values or levels & {"0", "1"}:
        return "primary"
    if "secondary" in values or levels & {"2", "3"}:
        return "secondary"
    return "school-unspecified"


def _geometry_points(geometry: object) -> list[Point]:
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, Point):
        return [geometry]
    if isinstance(geometry, LineString):
        return [Point(geometry.coords[0]), Point(geometry.coords[-1])]
    if hasattr(geometry, "geoms"):
        return [point for part in geometry.geoms for point in _geometry_points(part)]
    return []


def mark_ncn_edges(network: gpd.GeoDataFrame, context: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Annotate routable edges that overlap strategic public cycle-route evidence."""
    result = network.copy()
    raw_current_ncn = [
        any(value.strip().lower() == "yes" for value in _tag_values(raw_value))
        for raw_value in result.get("ncn", pd.Series(index=result.index, dtype=object))
    ]
    ncn = context[context["feature_type"].isin(STRATEGIC_CYCLE_ROUTE_TYPES)]
    cycle_alignment_bases = [()] * len(result)
    if ncn.empty:
        result["satn_ncn"] = raw_current_ncn
        cycle_alignment_bases = [
            ("current-ncn",) if is_current_ncn else () for is_current_ncn in raw_current_ncn
        ]
        result["cycle_alignment_bases"] = cycle_alignment_bases
        return result
    projected = result.to_crs(27700)
    projected_ncn = ncn.to_crs(27700)
    typed_corridors = tuple(
        (
            feature_type,
            CYCLE_ALIGNMENT_BASIS_BY_FEATURE_TYPE[feature_type],
            projected_ncn.loc[projected_ncn["feature_type"].eq(feature_type), "geometry"]
            .buffer(20)
            .union_all(),
        )
        for feature_type in CYCLE_ALIGNMENT_BASIS_BY_FEATURE_TYPE
        if projected_ncn["feature_type"].eq(feature_type).any()
    )
    corridor = projected_ncn.geometry.buffer(20).union_all()
    candidate_positions = sorted(
        int(position) for position in projected.sindex.query(corridor, predicate="intersects")
    )
    marked = [False] * len(result)
    if candidate_positions:
        candidate_geometry = projected.geometry.iloc[candidate_positions]
        candidate_lengths = candidate_geometry.length
        overlap_shares = candidate_geometry.intersection(corridor).length / candidate_lengths
        for position, geometry, length, overlap_share in zip(
            candidate_positions,
            candidate_geometry,
            candidate_lengths,
            overlap_shares,
            strict=True,
        ):
            marked[position] = bool(length and overlap_share >= 0.5) or raw_current_ncn[position]
            if length:
                cycle_alignment_bases[position] = tuple(
                    basis
                    for _feature_type, basis, typed_corridor in typed_corridors
                    if geometry.intersection(typed_corridor).length / length >= 0.5
                )
            if raw_current_ncn[position] and "current-ncn" not in cycle_alignment_bases[position]:
                cycle_alignment_bases[position] = (
                    "current-ncn",
                    *cycle_alignment_bases[position],
                )
    for position, is_current_ncn in enumerate(raw_current_ncn):
        if is_current_ncn and not marked[position]:
            marked[position] = True
            cycle_alignment_bases[position] = (
                "current-ncn",
                *cycle_alignment_bases[position],
            )
    result["satn_ncn"] = marked
    result["cycle_alignment_bases"] = cycle_alignment_bases
    return result


def corridor_overlap_share(
    route: LineString,
    corridor_geometries: object,
    *,
    route_crs: object,
    corridor_crs: object,
    buffer_m: float,
) -> float:
    """Return the directional share of a route inside buffered corridor geometry."""
    projected_route = gpd.GeoSeries([route], crs=route_crs).to_crs(27700).iloc[0]
    if not projected_route.length:
        return 0.0
    projected_corridors = gpd.GeoSeries(
        corridor_geometries,
        crs=corridor_crs,
    ).to_crs(27700)
    corridor = (
        projected_corridors.union_all()
        if buffer_m == 0
        else projected_corridors.buffer(buffer_m).union_all()
    )
    return min(
        1.0,
        float(projected_route.intersection(corridor).length / projected_route.length),
    )


def _retail_centres(points: list[dict[str, object]], crs: object) -> list[dict[str, object]]:
    if not points:
        return []
    frame = gpd.GeoDataFrame(points, geometry="geometry", crs=crs).to_crs(27700)
    graph = nx.Graph()
    graph.add_nodes_from(frame.index)
    spatial_index = frame.sindex
    for index, point in frame.geometry.items():
        neighbours = spatial_index.query(point.buffer(125), predicate="intersects")
        graph.add_edges_from(
            (index, int(neighbour)) for neighbour in neighbours if index != neighbour
        )
    rows: list[dict[str, object]] = []
    for component in nx.connected_components(graph):
        cluster = frame.loc[sorted(component)]
        if len(cluster) < 3 and not bool(cluster["is_centre"].any()):
            continue
        streets = [str(value) for value in cluster["street"] if _text(value)]
        street = Counter(streets).most_common(1)[0][0] if streets else None
        label = (
            f"{street} retail centre" if street else f"Retail centre ({len(cluster)} mapped shops)"
        )
        source_ids = sorted(str(value) for value in cluster["source_id"])
        source_key = ":".join(source_ids)
        rows.append(
            _row(
                "retail-centre",
                source_key,
                label,
                "shop cluster",
                ",".join(source_ids),
                cluster.geometry.union_all().centroid,
                feature_count=len(cluster),
            )
        )
    if not rows:
        return []
    projected = gpd.GeoDataFrame(rows, geometry="geometry", crs=27700).to_crs(crs)
    return projected.to_dict("records")


def _row(
    feature_type: str,
    identity: str,
    name: str,
    category: str,
    source_id: str,
    geometry: object,
    *,
    feature_count: int = 1,
    network_scope: NetworkScope = NetworkScope.UNRESOLVED,
    **attributes: object,
) -> dict[str, object]:
    digest = hashlib.sha256(f"{feature_type}:{identity}".encode()).hexdigest()[:12]
    return {
        "evidence_id": f"{feature_type}-{digest}",
        "feature_type": feature_type,
        "name": name,
        "category": category,
        "source_id": source_id,
        "feature_count": feature_count,
        "network_scope": network_scope.value,
        **attributes,
        "geometry": geometry,
    }


def _frame(rows: list[dict[str, object]], crs: object) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows, columns=CONTEXT_COLUMNS, geometry="geometry", crs=crs)


def continuous_linework(geometry: object) -> list[LineString]:
    """Return deterministic, separately continuous LineStrings from line-like geometry."""
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        merged = linemerge(geometry)
        if isinstance(merged, LineString):
            return [merged]
        return sorted(list(merged.geoms), key=lambda line: line.wkb_hex)
    if hasattr(geometry, "geoms"):
        return sorted(
            [line for part in geometry.geoms for line in continuous_linework(part)],
            key=lambda line: line.wkb_hex,
        )
    return []


def _source_id(row: pd.Series, fallback: object) -> str:
    return str(
        source_identity(
            row,
            ("SegmentID", "GlobalID", "FID", "osmid", "osm_id", "id", "element_type"),
            fallback,
        )
    )


def _text(value: object) -> str | None:
    values = canonical_tag_values(value)
    return ",".join(values) or None
