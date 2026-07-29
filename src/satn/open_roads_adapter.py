"""Closed byte-to-feature adapter for governed OS Open Roads RoadLink exports.

The adapter owns the semantics which determine an Open Roads ingestion contract:
the accepted export shape, normalisation, source feature key, coordinate treatment
and exact BNG-cell selection.  The DuckDB store deliberately stays outside this
module, so changing transactional or registry orchestration cannot invalidate a
valid adapter contract.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import pyproj
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from satn.evidence_contracts import (
    EvidencePartitionKey,
    IngestionContract,
    SourceExport,
    evidence_fingerprint,
)
from satn.models import OfficialRoadClassification

SOURCE_LAYER = "os-open-roads/RoadLink"
ATTRIBUTES = (
    "road_classification",
    "road_function",
    "road_classification_number",
    "name_1",
)
SOURCE_SCHEMA = (
    "id",
    "road_classification",
    "road_function",
    "road_classification_number",
    "name_1",
)


def canonical_official_classification(value: object) -> OfficialRoadClassification:
    """Map an Open Roads classification onto the shared source-frame contract."""

    text = (
        value.strip().lower().replace("_", " ").replace("-", " ")
        if isinstance(value, str)
        else ""
    )
    if text in {"a", "a road", "class a"}:
        return OfficialRoadClassification.A_ROAD
    if text in {"b", "b road", "class b"}:
        return OfficialRoadClassification.B_ROAD
    if text in {
        "c",
        "c road",
        "class c",
        "cu",
        "classified unnumbered",
        "classified unnumbered road",
    }:
        return OfficialRoadClassification.CLASSIFIED_UNNUMBERED
    if text in {"unclassified", "u", "unclassified road"}:
        return OfficialRoadClassification.UNCLASSIFIED
    return OfficialRoadClassification.UNKNOWN


@dataclass(frozen=True)
class OpenRoadsFeature:
    """One closed-adapter normalised RoadLink observation."""

    logical_key: str
    geometry: BaseGeometry
    attributes: Mapping[str, object]


@dataclass(frozen=True)
class OpenRoadsPartition:
    """A single exact BNG-cell adapter read, prior to store materialisation."""

    partition_key: EvidencePartitionKey
    features: tuple[OpenRoadsFeature, ...]


def contract_payload(source_crs: str) -> dict[str, object]:
    """Return the only accepted v1 Open Roads RoadLink ingestion contract."""

    return {
        "contract": "satn-ingestion-contract/v1",
        "source_layer": SOURCE_LAYER,
        "contract_version": "satn-open-roads-ingestion/v1",
        "accepted_schema": {
            "id": "string",
            "name_1": "string|null",
            "road_classification": "string",
            "road_classification_number": "string|null",
            "road_function": "string",
        },
        "stable_feature_key_policy": "source-export-roadlink-id/v1",
        "selected_attributes": sorted(ATTRIBUTES),
        "normalisation": {"trim_strings": True},
        "crs_transform": {
            "source_crs": source_crs,
            "target_crs": "EPSG:27700",
            "axis_order": "always_xy",
        },
        "partition_scheme": "bng-10km/v1",
        "spatial_predicate": "intersects",
        "implementation_dependency_fingerprint": adapter_fingerprint(),
    }


def adapter_fingerprint() -> str:
    """Fingerprint only adapter semantics and declared GIS runtime dependencies."""

    return evidence_fingerprint(
        {
            "contract": "satn-open-roads-byte-adapter-implementation/v1",
            "module_sha256": _sha256_file(Path(__file__)),
            "runtime_versions": {
                distribution: version(distribution)
                for distribution in (
                    "geopandas",
                    "pandas",
                    "pyogrio",
                    "pyproj",
                    "shapely",
                )
            },
            "native_runtime_versions": {
                "gdal": ".".join(str(part) for part in pyogrio.__gdal_version__),
                "proj": pyproj.proj_version_str,
                "geos": shapely.geos_version_string,
            },
        }
    )


def validate_export(source_export: SourceExport, ingestion_contract: IngestionContract) -> Path:
    """Validate a retained export and return its exact local path."""

    if (
        source_export.source_family != "os-open-roads"
        or source_export.dataset != "open-roads"
        or source_export.layer != "RoadLink"
    ):
        raise ValueError("unsupported governed Source Export for Open Roads ingestion")
    if source_export.format not in {"GeoPackage", "GeoJSON"}:
        raise ValueError("Open Roads ingestion supports GeoPackage or GeoJSON exports only")
    if ingestion_contract.canonical_payload() != contract_payload(source_export.declared_crs):
        raise ValueError("unsupported or untrusted Open Roads Ingestion Contract")
    retained_path = source_export.provenance.get("retained_path")
    if not isinstance(retained_path, str) or not retained_path:
        raise ValueError("Source Export provenance requires retained_path")
    source_path = Path(retained_path)
    if not source_path.is_absolute():
        raise ValueError("Source Export retained_path must be absolute")
    if not source_path.is_file():
        raise ValueError(f"governed Source Export is not retained at {source_path}")
    if _sha256_file(source_path) != source_export.raw_bytes_sha256:
        raise ValueError("governed Source Export checksum does not match retained bytes")
    try:
        physical_layer = _physical_layer_name(source_path, source_export)
        info = pyogrio.read_info(source_path, layer=physical_layer)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("cannot inspect governed Open Roads Source Export") from error
    actual_crs = info.get("crs")
    if actual_crs is None or CRS.from_user_input(actual_crs) != CRS.from_user_input(
        source_export.declared_crs
    ):
        raise ValueError("governed Source Export CRS does not match its declaration")
    expected_driver = {"GeoPackage": "GPKG", "GeoJSON": "GeoJSON"}[source_export.format]
    if info.get("driver") != expected_driver:
        raise ValueError("governed Source Export format does not match its declaration")
    fields = tuple(str(field) for field in info.get("fields", ()))
    missing = set(SOURCE_SCHEMA) - set(fields)
    if missing:
        raise ValueError(
            "governed Open Roads layer is missing required schema fields: "
            + ", ".join(sorted(missing))
        )
    field_types = dict(zip(fields, (str(dtype) for dtype in info.get("dtypes", ())), strict=True))
    if any(field_types[field] != "object" for field in SOURCE_SCHEMA):
        raise ValueError("governed Open Roads layer has incompatible schema types")
    if str(info.get("geometry_type")) not in {"LineString", "MultiLineString"}:
        raise ValueError("governed Open Roads layer must contain line geometry")
    return source_path


def read_partition(
    source_path: Path,
    source_export: SourceExport,
    ingestion_contract: IngestionContract,
    partition_key: EvidencePartitionKey,
) -> OpenRoadsPartition:
    """Read one exact source-layer/BNG-cell partition without clipping geometry."""

    if (
        partition_key.source_layer != ingestion_contract.source_layer
        or partition_key.partition_scheme != ingestion_contract.partition_scheme
    ):
        raise ValueError("requested partition does not match its Ingestion Contract")
    cell_geometry = box(*_bng_10km_bounds(partition_key.cell))
    source_bounds = _bounds_from_bng(cell_geometry.bounds, source_export.declared_crs)
    try:
        physical_layer = _physical_layer_name(source_path, source_export)
        frame = gpd.read_file(
            source_path,
            layer=physical_layer,
            bbox=source_bounds,
            columns=list(SOURCE_SCHEMA),
        )
    except Exception as error:
        raise ValueError("cannot read governed Open Roads partition") from error
    if frame.crs is None or CRS.from_user_input(frame.crs) != CRS.from_user_input(
        source_export.declared_crs
    ):
        raise ValueError("read Open Roads partition CRS does not match its declaration")
    frame = frame.to_crs("EPSG:27700")
    frame = frame[frame.geometry.intersects(cell_geometry)]
    features: list[OpenRoadsFeature] = []
    logical_keys: set[str] = set()
    for row in frame.itertuples(index=False):
        geometry = row.geometry
        if (
            geometry is None
            or geometry.is_empty
            or geometry.geom_type not in {"LineString", "MultiLineString"}
        ):
            raise ValueError("Open Roads partition contains unsupported geometry")
        source_id = _required_source_text(row.id, "id")
        logical_key = f"roadlink:{source_id}"
        if logical_key in logical_keys:
            raise ValueError("Open Roads partition contains duplicate RoadLink ids")
        logical_keys.add(logical_key)
        features.append(
            OpenRoadsFeature(
                logical_key=logical_key,
                geometry=geometry,
                attributes={
                    name: _normalise_value(
                        getattr(row, name),
                        name=name,
                        required=name in {"road_classification", "road_function"},
                    )
                    for name in ATTRIBUTES
                },
            )
        )
    return OpenRoadsPartition(partition_key=partition_key, features=tuple(features))


def _physical_layer_name(source_path: Path, source_export: SourceExport) -> str:
    layers = tuple(gpd.list_layers(source_path)["name"].astype(str))
    if source_export.format == "GeoJSON":
        if len(layers) != 1:
            raise ValueError("governed GeoJSON Source Export must contain exactly one layer")
        return layers[0]
    if source_export.layer not in layers:
        raise ValueError(f"governed Source Export does not contain layer {source_export.layer}")
    return source_export.layer


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounds_from_bng(
    bounds: tuple[float, float, float, float], target_crs: str
) -> tuple[float, float, float, float]:
    if target_crs == "EPSG:27700":
        return bounds
    transformer = Transformer.from_crs("EPSG:27700", target_crs, always_xy=True)
    return transformer.transform_bounds(*bounds, densify_pts=21)


def _bng_10km_bounds(cell: str) -> tuple[int, int, int, int]:
    first, second, east_digit, north_digit = cell
    first_index = ord(first) - ord("A")
    second_index = ord(second) - ord("A")
    if first_index > 7:
        first_index -= 1
    if second_index > 7:
        second_index -= 1
    easting_100km = ((first_index - 2) % 5) * 5 + second_index % 5
    northing_100km = 19 - (first_index // 5) * 5 - second_index // 5
    easting = easting_100km * 100_000 + int(east_digit) * 10_000
    northing = northing_100km * 100_000 + int(north_digit) * 10_000
    return easting, northing, easting + 10_000, northing + 10_000


def _required_source_text(value: object, name: str) -> str:
    if value is None or pd.isna(value) or not isinstance(value, str) or not value.strip():
        raise ValueError(f"Open Roads {name} must be a non-empty string")
    return value.strip()


def _normalise_value(value: object, *, name: str, required: bool) -> str | None:
    if value is None or pd.isna(value):
        if required:
            raise ValueError(f"Open Roads {name} must be a non-empty string")
        return None
    if not isinstance(value, str):
        raise ValueError(f"Open Roads {name} must be a string")
    normalised = value.strip()
    if not normalised:
        if required:
            raise ValueError(f"Open Roads {name} must be a non-empty string")
        return None
    return normalised
