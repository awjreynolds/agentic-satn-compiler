"""Build source-only baseline maps from governed area snapshots.

This path deliberately reads only the area boundary, official road
classification, OSM network, and retained route context.  It does not run the
compiler, RoadGraph, route selection, enrichment, or mesh planning.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString, mapping
from shapely.ops import unary_union

from satn.models import AreaDefinition
from satn.osm_active_travel import network_kind
from satn.tags import canonical_tag_values

PROJECT = Path(__file__).parents[1]
DEFAULT_AREAS = (
    PROJECT / "deployments" / "weca" / "area.yaml",
    PROJECT / "deployments" / "wiltshire" / "area.yaml",
    PROJECT / "deployments" / "wmca" / "area.yaml",
)
DEFAULT_DESTINATION = PROJECT / "build" / "source-baseline-pages"
SOURCE_BASELINE_SCHEMA = "satn-source-baseline-publication/v1"
CATALOGUE_SCHEMA = "satn-source-baseline-catalogue/v1"
MAP_ASSETS = ("maplibre-gl.js", "maplibre-gl.css", "MAPLIBRE-LICENSE.txt")
CURRENT_NCN_TYPES = frozenset({"ncn-route"})
FORMER_NCN_TYPES = frozenset({"declassified-ncn-route"})
GREENWAY_TYPES = frozenset({"greenway-cycleway"})
EXISTING_CYCLEWAY_KINDS = frozenset(
    {
        "mapped-cycleway",
        "road-cycleway",
        "bicycle-priority-road",
        "bicycle-route",
        "cycle-access-path",
        "cycle-track",
        "shared-use-path",
    }
)
BASELINE_CATEGORIES = (
    "a-road",
    "cycleway",
    "current-ncn",
    "former-ncn",
    "bridleway",
    "abandoned-railway",
)
BRIDLEWAY_DESIGNATIONS = frozenset({"bridleway", "public_bridleway"})
ABANDONED_RAILWAY_VALUES = frozenset({"abandoned", "disused", "dismantled", "razed"})
BRIDLEWAY_CONTEXT_TYPES = frozenset({"bridleway", "public-bridleway"})
FORMER_RAILWAY_CONTEXT_TYPES = frozenset(
    {"former-railway", "abandoned-railway", "disused-railway", "dismantled-railway"}
)


def _text(value: object) -> str | None:
    values = canonical_tag_values(value)
    return values[0] if values else None


def _values(value: object) -> tuple[str, ...]:
    return tuple(sorted(set(canonical_tag_values(value))))


def _normalised_values(value: object) -> set[str]:
    return {
        item.casefold().replace("-", "_").replace(" ", "_") for item in canonical_tag_values(value)
    }


def _date_text(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return _text(value)


def _line_parts(geometry: object) -> list[LineString]:
    if geometry is None or getattr(geometry, "is_empty", True):
        return []
    if isinstance(geometry, LineString):
        return [geometry] if len(geometry.coords) >= 2 else []
    if isinstance(geometry, MultiLineString):
        return [line for line in geometry.geoms if len(line.coords) >= 2]
    if hasattr(geometry, "geoms"):
        return [line for part in geometry.geoms for line in _line_parts(part)]
    return []


def _canonical_coordinates(geometry: LineString) -> tuple[tuple[float, float], ...]:
    coordinates = tuple((float(x), float(y)) for x, y, *_ in geometry.coords)
    reverse = tuple(reversed(coordinates))
    return min(coordinates, reverse)


def _truthy(value: object) -> bool:
    return any(item.casefold() in {"yes", "true", "1", "designated"} for item in _values(value))


def _snapshot_paths(definition: AreaDefinition) -> tuple[Path, dict[str, object]]:
    snapshot = definition.source.snapshot_dir / definition.source.snapshot_id
    if not snapshot.is_dir():
        raise ValueError(f"snapshot directory is missing: {snapshot}")
    manifest_path = snapshot / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("snapshot_id") != definition.source.snapshot_id
    ):
        raise ValueError(f"snapshot manifest does not match area definition: {snapshot}")
    return snapshot, manifest


def _read_frame(path: Path, *, columns: Sequence[str] = ()) -> gpd.GeoDataFrame:
    if not path.exists():
        return gpd.GeoDataFrame(columns=[*columns, "geometry"], geometry="geometry", crs=4326)
    frame = gpd.read_file(path)
    if frame.crs is None:
        raise ValueError(f"source frame has no CRS: {path}")
    return frame


def _source_metadata(manifest: Mapping[str, object]) -> dict[str, object]:
    evidence_sources = manifest.get("evidence_sources")
    return {
        "attribution": _text(manifest.get("attribution")) or "Source attribution unavailable",
        "source_kind": _text(manifest.get("source_kind")),
        "source_identifier": _text(manifest.get("source_identifier")),
        "evidence_sources": evidence_sources if isinstance(evidence_sources, dict) else {},
    }


def _row_identity(row: Mapping[str, object], fallback: object) -> str:
    for key in (
        "source_id",
        "source_feature_id",
        "official_feature_id",
        "osmid",
        "osm_way_id",
        "id",
    ):
        value = _text(row.get(key))
        if value:
            return value
    return str(fallback)


def _base_properties(
    *,
    category: str,
    classification: str,
    source_kind: str,
    row: Mapping[str, object],
    fallback: object,
    metadata: Mapping[str, object],
) -> dict[str, object]:
    source_id = _row_identity(row, fallback)
    source_feature_id = _text(row.get("official_feature_id")) or source_id
    name = _text(row.get("name")) or _text(row.get("official_road_name"))
    ref = _text(row.get("ref")) or _text(row.get("official_road_number"))
    dataset = _text(row.get("dataset"))
    publisher = _text(row.get("publisher"))
    effective_date = _date_text(row.get("effective_date"))
    licence = _text(row.get("licence"))
    designation = _text(row.get("designation")) or _text(row.get("prow_class"))
    railway = _text(row.get("railway"))
    return {
        "category": category,
        "classification": classification,
        "source_kind": source_kind,
        "source_id": source_id,
        "source_feature_id": source_feature_id,
        "name": name,
        "ref": ref,
        "dataset": dataset,
        "publisher": publisher,
        "effective_date": effective_date,
        "licence": licence,
        "designation": designation,
        "railway": railway,
        "source_attribution": metadata.get("attribution"),
    }


def _append_feature(
    records: dict[tuple[str, tuple[tuple[float, float], ...]], dict[str, object]],
    *,
    category: str,
    classification: str,
    source_kind: str,
    row: Mapping[str, object],
    fallback: object,
    geometry: object,
    boundary: object,
    metadata: Mapping[str, object],
) -> None:
    clipped = geometry.intersection(boundary)
    for line in _line_parts(clipped):
        coordinates = _canonical_coordinates(line)
        key = (category, coordinates)
        properties = _base_properties(
            category=category,
            classification=classification,
            source_kind=source_kind,
            row=row,
            fallback=fallback,
            metadata=metadata,
        )
        record = records.setdefault(
            key,
            {
                "category": category,
                "classification": classification,
                "source_kind": source_kind,
                "coordinates": coordinates,
                "source_ids": set(),
                "source_feature_ids": set(),
                "names": set(),
                "refs": set(),
                "datasets": set(),
                "publishers": set(),
                "effective_dates": set(),
                "licences": set(),
                "designations": set(),
                "railways": set(),
                "attributions": set(),
            },
        )
        for record_key, value_key in (
            ("source_ids", "source_id"),
            ("source_feature_ids", "source_feature_id"),
            ("names", "name"),
            ("refs", "ref"),
            ("datasets", "dataset"),
            ("publishers", "publisher"),
            ("effective_dates", "effective_date"),
            ("licences", "licence"),
            ("designations", "designation"),
            ("railways", "railway"),
            ("attributions", "source_attribution"),
        ):
            value = properties.get(value_key)
            if value:
                record[record_key].add(str(value))


def _network_features(
    network: gpd.GeoDataFrame,
    boundary: object,
    metadata: Mapping[str, object],
    records: dict[tuple[str, tuple[tuple[float, float], ...]], dict[str, object]],
    *,
    source_kind: str = "osm-network",
    include_existing_categories: bool = True,
) -> None:
    for index, row in network.iterrows():
        if row.geometry is None or row.geometry.is_empty:
            continue
        highways = _normalised_values(row.get("highway"))
        designations = _normalised_values(row.get("designation"))
        railway_values = _normalised_values(row.get("railway"))
        is_bridleway = bool("bridleway" in highways or designations & BRIDLEWAY_DESIGNATIONS)
        is_former_railway = bool(railway_values & ABANDONED_RAILWAY_VALUES)
        bridleway_classification = (
            "public-bridleway" if "public_bridleway" in designations else "bridleway"
        )
        if is_bridleway:
            _append_feature(
                records,
                category="bridleway",
                classification=bridleway_classification,
                source_kind=source_kind,
                row=row,
                fallback=index,
                geometry=row.geometry,
                boundary=boundary,
                metadata=metadata,
            )
        if is_former_railway:
            _append_feature(
                records,
                category="abandoned-railway",
                classification="abandoned-railway",
                source_kind=source_kind,
                row=row,
                fallback=index,
                geometry=row.geometry,
                boundary=boundary,
                metadata=metadata,
            )
        values = _normalised_values(row.get("ncn"))
        if include_existing_categories and values & {"yes", "true", "1", "designated"}:
            _append_feature(
                records,
                category="current-ncn",
                classification="current-ncn",
                source_kind=source_kind,
                row=row,
                fallback=index,
                geometry=row.geometry,
                boundary=boundary,
                metadata=metadata,
            )
            continue
        if not include_existing_categories:
            continue
        kind = network_kind(row)
        if kind not in EXISTING_CYCLEWAY_KINDS:
            continue
        _append_feature(
            records,
            category="cycleway",
            classification=kind or "cycleway",
            source_kind=source_kind,
            row=row,
            fallback=index,
            geometry=row.geometry,
            boundary=boundary,
            metadata=metadata,
        )


def _official_features(
    official: gpd.GeoDataFrame,
    boundary: object,
    metadata: Mapping[str, object],
    records: dict[tuple[str, tuple[tuple[float, float], ...]], dict[str, object]],
) -> None:
    official_sources = metadata.get("evidence_sources")
    source_metadata = (
        official_sources.get("official_road_classification", {})
        if isinstance(official_sources, dict)
        else {}
    )
    for index, row in official.iterrows():
        classification = (_text(row.get("official_classification")) or "").casefold()
        if classification != "a-road" or row.geometry is None or row.geometry.is_empty:
            continue
        row_values = dict(row)
        for key in ("licence", "effective_date"):
            if not _text(row_values.get(key)):
                row_values[key] = source_metadata.get(key)
        _append_feature(
            records,
            category="a-road",
            classification="a-road",
            source_kind="official-road-classification",
            row=row_values,
            fallback=index,
            geometry=row.geometry,
            boundary=boundary,
            metadata={
                **metadata,
                "attribution": source_metadata.get("attribution") or metadata.get("attribution"),
            },
        )


def _context_features(
    context: gpd.GeoDataFrame,
    boundary: object,
    metadata: Mapping[str, object],
    records: dict[tuple[str, tuple[tuple[float, float], ...]], dict[str, object]],
) -> None:
    for index, row in context.iterrows():
        feature_type = (
            (_text(row.get("feature_type")) or "").casefold().replace("_", "-").replace(" ", "-")
        )
        if feature_type in CURRENT_NCN_TYPES:
            category, classification = "current-ncn", "current-ncn"
        elif feature_type in FORMER_NCN_TYPES:
            category, classification = "former-ncn", "former-ncn"
        elif feature_type in GREENWAY_TYPES:
            category, classification = "cycleway", "greenway"
        elif feature_type in BRIDLEWAY_CONTEXT_TYPES:
            category = "bridleway"
            classification = (
                "public-bridleway" if feature_type == "public-bridleway" else "bridleway"
            )
        elif feature_type in FORMER_RAILWAY_CONTEXT_TYPES or (
            _normalised_values(row.get("railway")) & ABANDONED_RAILWAY_VALUES
        ):
            category, classification = "abandoned-railway", "abandoned-railway"
        else:
            continue
        if row.geometry is None or row.geometry.is_empty:
            continue
        _append_feature(
            records,
            category=category,
            classification=classification,
            source_kind="route-context",
            row=row,
            fallback=index,
            geometry=row.geometry,
            boundary=boundary,
            metadata=metadata,
        )


def _property_values(record: Mapping[str, object]) -> dict[str, object]:
    def values(name: str) -> list[str]:
        return sorted(str(value) for value in record[name] if value)

    source_ids = values("source_ids")
    source_feature_ids = values("source_feature_ids")
    names = values("names")
    refs = values("refs")
    return {
        "category": record["category"],
        "classification": record["classification"],
        "source_kind": record["source_kind"],
        "source_id": source_ids[0] if source_ids else None,
        "source_ids": source_ids,
        "source_feature_id": source_feature_ids[0] if source_feature_ids else None,
        "source_feature_ids": source_feature_ids,
        "name": names[0] if names else None,
        "names": names,
        "ref": refs[0] if refs else None,
        "refs": refs,
        "dataset": ", ".join(values("datasets")) or None,
        "publisher": ", ".join(values("publishers")) or None,
        "effective_date": ", ".join(values("effective_dates")) or None,
        "licence": ", ".join(values("licences")) or None,
        "designation": ", ".join(values("designations")) or None,
        "railway": ", ".join(values("railways")) or None,
        "source_attribution": ", ".join(values("attributions")) or None,
    }


def _feature_collection(
    records: Iterable[Mapping[str, object]],
    *,
    input_crs: object,
    output_crs: object,
) -> tuple[dict[str, object], dict[str, int], list[float] | None]:
    features: list[dict[str, object]] = []
    counts = {category: 0 for category in BASELINE_CATEGORIES}
    all_geometry = []
    for index, record in enumerate(
        sorted(records, key=lambda item: (str(item["category"]), item["coordinates"]))
    ):
        geometry = LineString(record["coordinates"])
        projected = gpd.GeoSeries([geometry], crs=input_crs).to_crs(output_crs).iloc[0]
        properties = _property_values(record)
        feature_id = f"baseline-{index + 1}"
        features.append(
            {
                "type": "Feature",
                "id": feature_id,
                "geometry": mapping(projected),
                "properties": properties,
            }
        )
        counts[str(record["category"])] += 1
        all_geometry.append(projected)
    bbox = None
    if all_geometry:
        bounds = unary_union(all_geometry).bounds
        bbox = [float(value) for value in bounds]
    return {"type": "FeatureCollection", "features": features}, counts, bbox


def _safe_json(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _build_area(area_path: Path, destination: Path) -> dict[str, object]:
    definition = AreaDefinition.from_yaml(area_path)
    snapshot, manifest = _snapshot_paths(definition)
    boundary = _read_frame(snapshot / "boundary.geojson")
    network = _read_frame(snapshot / "network.geojson")
    context = _read_frame(snapshot / "context.geojson")
    source_corridors = _read_frame(area_path.parent / "source-corridors.geojson")
    official_path = snapshot / "official-road-classification.geojson"
    if not official_path.exists() and definition.source.official_road_classification is not None:
        official_path = definition.source.official_road_classification.path
    official = _read_frame(official_path)
    if boundary.empty:
        raise ValueError(f"snapshot boundary is empty: {snapshot}")

    working_crs = boundary.crs
    if working_crs is None:
        raise ValueError(f"snapshot boundary has no CRS: {snapshot}")
    boundary_geometry = boundary.geometry.union_all()
    metadata = _source_metadata(manifest)
    records: dict[tuple[str, tuple[tuple[float, float], ...]], dict[str, object]] = {}
    _official_features(official.to_crs(working_crs), boundary_geometry, metadata, records)
    _network_features(network.to_crs(working_crs), boundary_geometry, metadata, records)
    if not source_corridors.empty:
        _network_features(
            source_corridors.to_crs(working_crs),
            boundary_geometry,
            metadata,
            records,
            source_kind="source-corridors",
            include_existing_categories=False,
        )
    _context_features(context.to_crs(working_crs), boundary_geometry, metadata, records)
    network_payload, counts, bbox = _feature_collection(
        records.values(), input_crs=working_crs, output_crs=4326
    )

    deployment_id = definition.deployment_slug
    area_destination = destination / "deployments" / deployment_id
    area_destination.mkdir(parents=True, exist_ok=True)
    (area_destination / "network.geojson").write_text(
        json.dumps(_safe_json(network_payload), separators=(",", ":")), encoding="utf-8"
    )
    snapshot_date = _text(manifest.get("retrieved_at")) or _text(manifest.get("snapshot_id"))
    publication = {
        "schema_version": SOURCE_BASELINE_SCHEMA,
        "publication_kind": "source-baseline",
        "deployment_id": deployment_id,
        "area_id": definition.area_id,
        "area_name": definition.area_name,
        "snapshot_id": definition.source.snapshot_id,
        "date": snapshot_date,
        "counts": counts,
        "bbox": bbox,
        "attribution": metadata["attribution"],
        "source": metadata,
        "disclaimer": (
            "This map publishes source geometry and classifications only. It has not been "
            "assessed for quality, safety, feasibility, funding, or chosen priority."
        ),
    }
    (area_destination / "publication.json").write_text(
        json.dumps(_safe_json(publication), indent=2) + "\n", encoding="utf-8"
    )
    template = (PROJECT / "src" / "satn" / "assets" / "source-baseline.html").read_text(
        encoding="utf-8"
    )
    title = html.escape(f"{definition.area_name} source baseline")
    template = template.replace("__BASELINE_TITLE__", title)
    (area_destination / "index.html").write_text(template, encoding="utf-8")
    assets = area_destination / "assets"
    assets.mkdir(exist_ok=True)
    for filename in MAP_ASSETS:
        shutil.copyfile(PROJECT / "src" / "satn" / "assets" / filename, assets / filename)
    shutil.copyfile(
        PROJECT / "src" / "satn" / "assets" / "source-baseline-service-worker.js",
        area_destination / "service-worker.js",
    )
    return {
        "deployment_id": deployment_id,
        "area_id": definition.area_id,
        "area_name": definition.area_name,
        "publication_kind": "source-baseline",
        "snapshot_id": definition.source.snapshot_id,
        "date": snapshot_date,
        "counts": counts,
        "artifacts": {"review_map": f"deployments/{deployment_id}/index.html"},
    }


def build_source_baseline_maps(
    area_definitions: Iterable[str | Path] = DEFAULT_AREAS,
    destination: str | Path = DEFAULT_DESTINATION,
) -> Path:
    """Build the source-only catalogue and one map directory per area."""

    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    entries = [_build_area(Path(path).resolve(), output) for path in area_definitions]
    catalogue = {
        "schema_version": CATALOGUE_SCHEMA,
        "publication_kind": "source-baseline",
        "deployments": entries,
    }
    (output / "catalogue.json").write_text(
        json.dumps(_safe_json(catalogue), indent=2) + "\n", encoding="utf-8"
    )
    links = "\n".join(
        '<li><a href="{path}">{name}</a></li>'.format(
            path=html.escape(str(entry["artifacts"]["review_map"]), quote=True),
            name=html.escape(str(entry["area_name"])),
        )
        for entry in entries
    )
    (output / "index.html").write_text(
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Source baseline maps</title></head><body><main>"
        "<h1>Source baseline maps</h1>"
        "<p>Source geometry and classifications only; no quality or priority assessment.</p>"
        f"<ul>{links}</ul></main></body></html>\n",
        encoding="utf-8",
    )
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("area_definitions", nargs="*", type=Path, default=list(DEFAULT_AREAS))
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    output = build_source_baseline_maps(args.area_definitions, args.destination)
    print(output)


if __name__ == "__main__":
    main()
