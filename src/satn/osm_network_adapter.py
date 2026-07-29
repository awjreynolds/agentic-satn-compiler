"""Closed byte-to-feature adapter for governed OpenStreetMap network XML exports.

The adapter is deliberately independent of the Local Evidence Store.  It
attests the retained raw XML before reading the OGR ``lines`` layer, preserves
each full OSM way geometry, and fans those ways into requested BNG cells without
inventing a routing graph or clipping the source observation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import urllib.parse
import xml.etree.ElementTree as ElementTree
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from importlib.metadata import version
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pandas as pd
import pyogrio
import pyproj
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry

from satn.evidence_contracts import (
    EvidencePartitionKey,
    IngestionContract,
    SourceExport,
    evidence_fingerprint,
)

SOURCE_LAYER: Final = "openstreetmap/lines"
SOURCE_FAMILY: Final = "openstreetmap"
DATASET: Final = "network"
FORMAT: Final = "OSM XML"
DECLARED_CRS: Final = "EPSG:4326"
# SPDX's exact ODbL 1.0 identifier is the governed licence declaration.
LICENCE: Final = "ODbL-1.0"
OSM_LAYER: Final = "lines"
TARGET_CRS: Final = "EPSG:27700"
PARTITION_SCHEME: Final = "bng-10km/v1"
_OSM_CONFIG_PATH: Final = Path(__file__).with_name("assets") / "osm-network-osmconf.ini"
_GDAL_CONFIG_LOCK: Final = threading.RLock()

ATTRIBUTES: Final = (
    "name",
    "highway",
    "ref",
    "oneway",
    "surface",
    "access",
    "bicycle",
    "foot",
    "cycleway",
    "service",
    "tracktype",
    "bridge",
    "tunnel",
    "junction",
    "maxspeed",
    "lanes",
    "width",
    "lit",
    "ele",
    "incline",
)
_PROMOTED_ATTRIBUTES: Final = frozenset({"name", "highway"})
_READ_COLUMNS: Final = ("osm_id", "name", "highway", "other_tags")
_OSM_LINES_SCHEMA: Final = {
    "osm_id": "object",
    "name": "object",
    "highway": "object",
    "waterway": "object",
    "aerialway": "object",
    "barrier": "object",
    "man_made": "object",
    "railway": "object",
    "other_tags": "object",
}
_UTC_RECEIPT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SUPPORTED_BNG_EXTENT = box(0, 0, 700_000, 1_300_000)
ACQUISITION_RECEIPT_CONTRACT: Final = "satn-osm-network-acquisition-receipt/v1"
OSM_ATTRIBUTION: Final = "© OpenStreetMap contributors"
OSM_COPYRIGHT_URL: Final = "https://www.openstreetmap.org/copyright"


@dataclass(frozen=True)
class OsmNetworkFeature:
    """One normalised OSM way, retaining its full transformed geometry."""

    logical_key: str
    geometry: BaseGeometry
    attributes: Mapping[str, str | None]


@dataclass(frozen=True)
class OsmNetworkPartition:
    """One exact BNG-cell projection of the single raw OSM layer scan."""

    partition_key: EvidencePartitionKey
    features: tuple[OsmNetworkFeature, ...]


def governed_overpass_query(
    bounds: Sequence[object], timeout_seconds: int
) -> tuple[str, dict[str, str]]:
    """Return the only supported bounded raw-network acquisition query."""

    area = _canonical_wgs84_bbox(bounds)
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 1_800
    ):
        raise ValueError("OpenStreetMap acquisition timeout_seconds must be 1 to 1800")
    bbox = ",".join(area[key] for key in ("south", "west", "north", "east"))
    query = (
        f"[out:xml][timeout:{timeout_seconds}][bbox:{bbox}];\n"
        'way["highway"];\n'
        "(._;>;);\n"
        "out meta;\n"
    )
    return query, {"type": "bbox", "crs": DECLARED_CRS, **area}


def ingestion_contract() -> IngestionContract:
    """Construct the current closed OSM network ingestion contract."""

    payload = contract_payload()
    payload.pop("contract")
    return IngestionContract(**payload)


def acquisition_receipt_payload(
    source_path: Path,
    *,
    raw_object_path: str,
    endpoint: str,
    query: str,
    area: Mapping[str, object],
    timeout_seconds: int,
    retrieved_at: str,
    response_headers: Mapping[str, str | None],
) -> dict[str, object]:
    """Build a governed receipt after validating the exact received raw bytes."""

    source_path = source_path.resolve()
    raw_sha256 = _sha256_file(source_path)
    expected_object_path = f"objects/sha256/{raw_sha256}.osm"
    if raw_object_path != expected_object_path:
        raise ValueError("OSM raw object path does not match its content-addressed digest")
    canonical_endpoint = _canonical_overpass_endpoint(endpoint)
    canonical_retrieved_at = _canonical_utc_receipt(retrieved_at)
    expected_query, canonical_area = governed_overpass_query(
        tuple(area.get(key) for key in ("south", "west", "north", "east")),
        timeout_seconds,
    )
    if dict(area) != canonical_area or query != expected_query:
        raise ValueError("OSM acquisition query or area differs from the governed bounded request")
    header_keys = {"content_type", "etag", "last_modified", "date"}
    if set(response_headers) != header_keys or any(
        value is not None and (not isinstance(value, str) or not value.strip())
        for value in response_headers.values()
    ):
        raise ValueError("OSM acquisition response headers do not match the closed receipt schema")
    publisher_release = _raw_xml_receipt(source_path)
    source_export = SourceExport(
        source_family=SOURCE_FAMILY,
        dataset=DATASET,
        layer=OSM_LAYER,
        publisher_release=publisher_release,
        effective_date=publisher_release[:10],
        licence=LICENCE,
        format=FORMAT,
        declared_crs=DECLARED_CRS,
        raw_bytes_sha256=raw_sha256,
        provenance={"retained_path": str(source_path)},
    )
    validate_export(source_export, ingestion_contract())
    return {
        "contract": ACQUISITION_RECEIPT_CONTRACT,
        "source_export": source_export.canonical_payload(),
        "acquisition": {
            "endpoint": canonical_endpoint,
            "method": "POST",
            "query_language": "Overpass QL",
            "query": query,
            "area": canonical_area,
            "timeout_seconds": timeout_seconds,
            "retrieved_at": canonical_retrieved_at,
        },
        "publisher": {
            "generator": _raw_xml_generator(source_path),
            "osm_base": publisher_release,
        },
        "http": {key: response_headers[key] for key in sorted(header_keys)},
        "raw_object": {
            "path": raw_object_path,
            "sha256": raw_sha256,
            "byte_count": source_path.stat().st_size,
        },
        "licence": {
            "spdx": LICENCE,
            "attribution": OSM_ATTRIBUTION,
            "url": OSM_COPYRIGHT_URL,
        },
    }


def acquisition_receipt_bytes(payload: Mapping[str, object]) -> bytes:
    """Return canonical retained bytes for an OSM acquisition receipt."""

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return (encoded + "\n").encode()


def load_acquisition_receipt(receipt_path: Path) -> SourceExport:
    """Load and fully attest one retained OSM acquisition without network access."""

    if not receipt_path.is_file() or receipt_path.is_symlink():
        raise ValueError(f"governed OSM acquisition receipt is not retained at {receipt_path}")
    resolved_receipt = receipt_path.resolve()
    raw_receipt = resolved_receipt.read_bytes()
    expected_name = f"{hashlib.sha256(raw_receipt).hexdigest()}.json"
    if resolved_receipt.name != expected_name or resolved_receipt.parent.name != "receipts":
        raise ValueError("governed OSM acquisition receipt is not content-addressed")
    try:
        payload = json.loads(raw_receipt)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("governed OSM acquisition receipt is invalid JSON") from error
    if not isinstance(payload, dict) or raw_receipt != acquisition_receipt_bytes(payload):
        raise ValueError("governed OSM acquisition receipt is not canonical")
    source_payload, acquisition, publisher, raw_object = _validated_acquisition_receipt(payload)
    raw_sha256 = str(raw_object["sha256"])
    object_path = (
        resolved_receipt.parent.parent / "objects" / "sha256" / f"{raw_sha256}.osm"
    )
    if object_path.is_symlink() or not object_path.is_file():
        raise ValueError(f"governed OSM raw object is not retained at {object_path}")
    if object_path.stat().st_size != raw_object["byte_count"]:
        raise ValueError(
            "governed OSM raw object checksum or byte count does not match its receipt"
        )
    provenance = {
        "retained_path": str(object_path.resolve()),
        "acquisition_receipt": str(resolved_receipt),
        "retrieved_at": acquisition["retrieved_at"],
        "acquisition_endpoint": acquisition["endpoint"],
        "acquisition_query": acquisition["query"],
        "acquisition_area": acquisition["area"],
        "publisher_generator": publisher["generator"],
        "http": payload["http"],
        "attribution": payload["licence"]["attribution"],
    }
    source_export = SourceExport(
        **{
            key: value
            for key, value in source_payload.items()
            if key != "contract"
        },
        provenance=provenance,
    )
    validate_export(source_export, ingestion_contract())
    return source_export


def contract_payload() -> dict[str, object]:
    """Return the only accepted v1 OpenStreetMap network XML contract."""

    return {
        "contract": "satn-ingestion-contract/v1",
        "source_layer": SOURCE_LAYER,
        "contract_version": "satn-osm-network-ingestion/v1",
        "accepted_schema": dict(_OSM_LINES_SCHEMA),
        "stable_feature_key_policy": "source-export-scoped-osm-way-id/v1",
        "selected_attributes": sorted(ATTRIBUTES),
        "normalisation": {
            "trim_strings": True,
            "missing_strings": None,
            "preserve_non_missing_source_text": True,
            "other_tags_parser": "closed-ogr-hstore/v1",
            "raw_xml_receipt": {
                "root": {"element": "osm", "version": "0.6"},
                "meta": {"element": "meta", "attribute": "osm_base"},
                "timestamp_format": "UTC-RFC3339-seconds/Z",
                "source_export_binding": {
                    "publisher_release": "exact osm_base",
                    "effective_date": "UTC date of osm_base",
                },
            },
        },
        "crs_transform": {
            "source_crs": DECLARED_CRS,
            "target_crs": TARGET_CRS,
            "axis_order": "always_xy",
        },
        "partition_scheme": PARTITION_SCHEME,
        "spatial_predicate": "intersects",
        "implementation_dependency_fingerprint": adapter_fingerprint(),
    }


def adapter_fingerprint() -> str:
    """Fingerprint adapter semantics and its pinned GIS runtime boundary."""

    return evidence_fingerprint(
        {
            "contract": "satn-osm-network-byte-adapter-implementation/v1",
            "module_sha256": _sha256_file(Path(__file__)),
            "osm_config_sha256": _sha256_file(_OSM_CONFIG_PATH),
            "runtime_versions": {
                distribution: version(distribution)
                for distribution in ("pandas", "pyogrio", "pyproj", "shapely")
            },
            "native_runtime_versions": {
                "gdal": ".".join(str(part) for part in pyogrio.__gdal_version__),
                "proj": pyproj.proj_version_str,
                "geos": shapely.geos_version_string,
            },
        }
    )


def validate_export(source_export: SourceExport, ingestion_contract: IngestionContract) -> Path:
    """Validate retained raw XML, its receipt, and the pinned OGR layer shape."""

    if (
        source_export.source_family != SOURCE_FAMILY
        or source_export.dataset != DATASET
        or source_export.layer != OSM_LAYER
    ):
        raise ValueError("unsupported governed Source Export for OpenStreetMap network ingestion")
    if source_export.format != FORMAT:
        raise ValueError("OpenStreetMap network ingestion accepts OSM XML exports only")
    if source_export.declared_crs != DECLARED_CRS:
        raise ValueError("OpenStreetMap network ingestion requires declared_crs EPSG:4326")
    if source_export.licence != LICENCE:
        raise ValueError("OpenStreetMap network ingestion requires licence ODbL-1.0")
    if ingestion_contract.canonical_payload() != contract_payload():
        raise ValueError("unsupported or untrusted OpenStreetMap network Ingestion Contract")

    source_path = _retained_path(source_export)
    if _sha256_file(source_path) != source_export.raw_bytes_sha256:
        raise ValueError("governed Source Export checksum does not match retained bytes")

    receipt = _raw_xml_receipt(source_path)
    if source_export.publisher_release != receipt:
        raise ValueError("Source Export publisher_release does not match raw XML osm_base")
    if source_export.effective_date != receipt[:10]:
        raise ValueError("Source Export effective_date does not match raw XML osm_base UTC date")

    try:
        with _osm_driver_configuration():
            layers = pyogrio.list_layers(source_path)
            layer_names = {str(layer[0]) for layer in layers}
            if OSM_LAYER not in layer_names:
                raise ValueError(f"governed Source Export does not contain layer {OSM_LAYER}")
            info = pyogrio.read_info(source_path, layer=OSM_LAYER)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("cannot inspect governed OpenStreetMap Source Export") from error

    if info.get("driver") != "OSM":
        raise ValueError("governed Source Export format does not match OSM XML")
    actual_crs = info.get("crs")
    if actual_crs is None or CRS.from_user_input(actual_crs) != CRS.from_epsg(4326):
        raise ValueError("governed Source Export CRS does not match EPSG:4326")
    _validate_ogr_schema(info)
    if str(info.get("geometry_type")) not in {"LineString", "MultiLineString"}:
        raise ValueError("governed OpenStreetMap layer must contain line geometry")
    return source_path


def read_partitions(
    source_path: Path,
    source_export: SourceExport,
    ingestion_contract: IngestionContract,
    partition_keys: Sequence[EvidencePartitionKey],
) -> tuple[OsmNetworkPartition, ...]:
    """Read the raw OSM lines layer once and fan complete ways into requested cells."""

    retained_path = validate_export(source_export, ingestion_contract)
    if source_path.resolve() != retained_path:
        raise ValueError("requested OSM source path does not match its retained Source Export")
    ordered_keys = _requested_partition_keys(partition_keys, ingestion_contract)
    try:
        with _osm_driver_configuration():
            frame = pyogrio.read_dataframe(
                retained_path,
                layer=OSM_LAYER,
                columns=list(_READ_COLUMNS),
            )
    except Exception as error:
        raise ValueError("cannot read governed OpenStreetMap OSM lines layer") from error
    if frame.crs is None or CRS.from_user_input(frame.crs) != CRS.from_epsg(4326):
        raise ValueError("read OpenStreetMap layer CRS does not match EPSG:4326")
    missing_columns = set(_READ_COLUMNS) - set(frame.columns)
    if missing_columns:
        raise ValueError(
            "read OpenStreetMap layer is missing required columns: "
            + ", ".join(sorted(missing_columns))
        )

    transformer = Transformer.from_crs(DECLARED_CRS, TARGET_CRS, always_xy=True)
    all_features = _normalised_features(frame, transformer)
    cells = {key.cell: box(*_bng_10km_bounds(key.cell)) for key in ordered_keys}
    features_by_cell: dict[str, list[OsmNetworkFeature]] = {key.cell: [] for key in ordered_keys}
    for feature in all_features:
        for cell, cell_geometry in cells.items():
            if feature.geometry.intersects(cell_geometry):
                features_by_cell[cell].append(feature)
    return tuple(
        OsmNetworkPartition(
            partition_key=key,
            features=tuple(features_by_cell[key.cell]),
        )
        for key in ordered_keys
    )


def _retained_path(source_export: SourceExport) -> Path:
    retained_path = source_export.provenance.get("retained_path")
    if not isinstance(retained_path, str) or not retained_path:
        raise ValueError("Source Export provenance requires retained_path")
    source_path = Path(retained_path)
    if not source_path.is_absolute():
        raise ValueError("Source Export retained_path must be absolute")
    if not source_path.is_file():
        raise ValueError(f"governed Source Export is not retained at {source_path}")
    return source_path.resolve()


def _canonical_wgs84_bbox(bounds: Sequence[object]) -> dict[str, str]:
    if isinstance(bounds, (str, bytes)) or len(bounds) != 4:
        raise ValueError("OpenStreetMap acquisition bbox requires south, west, north and east")
    names = ("south", "west", "north", "east")
    decimals: list[Decimal] = []
    values: dict[str, str] = {}
    for name, value in zip(names, bounds, strict=True):
        if isinstance(value, bool):
            raise ValueError("OpenStreetMap acquisition bbox coordinates must be finite numbers")
        try:
            coordinate = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise ValueError(
                "OpenStreetMap acquisition bbox coordinates must be finite numbers"
            ) from error
        if not coordinate.is_finite():
            raise ValueError("OpenStreetMap acquisition bbox coordinates must be finite numbers")
        if coordinate == 0:
            coordinate = Decimal(0)
        decimals.append(coordinate)
        text = format(coordinate, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        values[name] = text
    south, west, north, east = decimals
    if not Decimal(-90) <= south < north <= Decimal(90):
        raise ValueError("OpenStreetMap acquisition bbox latitude bounds are invalid")
    if not Decimal(-180) <= west < east <= Decimal(180):
        raise ValueError("OpenStreetMap acquisition bbox longitude bounds are invalid")
    return values


def _canonical_overpass_endpoint(value: object) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("OpenStreetMap acquisition endpoint must be an HTTPS URL")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "OpenStreetMap acquisition endpoint must be an HTTPS URL without credentials, "
            "query or fragment"
        )
    return value


def _validated_acquisition_receipt(
    payload: Mapping[str, object],
) -> tuple[
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
]:
    if set(payload) != {
        "contract",
        "source_export",
        "acquisition",
        "publisher",
        "http",
        "raw_object",
        "licence",
    } or payload.get("contract") != ACQUISITION_RECEIPT_CONTRACT:
        raise ValueError("governed OSM acquisition receipt does not match the v1 schema")
    source_payload = payload["source_export"]
    acquisition = payload["acquisition"]
    publisher = payload["publisher"]
    http = payload["http"]
    raw_object = payload["raw_object"]
    licence = payload["licence"]
    if not all(
        isinstance(value, dict)
        for value in (source_payload, acquisition, publisher, http, raw_object, licence)
    ):
        raise ValueError("governed OSM acquisition receipt does not match the v1 schema")
    assert isinstance(source_payload, dict)
    assert isinstance(acquisition, dict)
    assert isinstance(publisher, dict)
    assert isinstance(http, dict)
    assert isinstance(raw_object, dict)
    assert isinstance(licence, dict)
    if set(source_payload) != {
        "contract",
        "source_family",
        "dataset",
        "layer",
        "publisher_release",
        "effective_date",
        "licence",
        "format",
        "declared_crs",
        "raw_bytes_sha256",
    }:
        raise ValueError("governed OSM acquisition Source Export does not match the v1 schema")
    if (
        source_payload.get("contract") != "satn-source-export/v1"
        or source_payload.get("source_family") != SOURCE_FAMILY
        or source_payload.get("dataset") != DATASET
        or source_payload.get("layer") != OSM_LAYER
        or source_payload.get("licence") != LICENCE
        or source_payload.get("format") != FORMAT
        or source_payload.get("declared_crs") != DECLARED_CRS
    ):
        raise ValueError("governed OSM acquisition Source Export declaration is unsupported")
    if set(acquisition) != {
        "endpoint",
        "method",
        "query_language",
        "query",
        "area",
        "timeout_seconds",
        "retrieved_at",
    }:
        raise ValueError("governed OSM acquisition request does not match the v1 schema")
    if acquisition.get("method") != "POST" or acquisition.get("query_language") != "Overpass QL":
        raise ValueError("governed OSM acquisition request method or language is unsupported")
    endpoint = _canonical_overpass_endpoint(acquisition.get("endpoint"))
    if endpoint != acquisition["endpoint"]:
        raise ValueError("governed OSM acquisition endpoint is not canonical")
    retrieved_at = _canonical_utc_receipt(acquisition.get("retrieved_at"))
    if retrieved_at != acquisition["retrieved_at"]:
        raise ValueError("governed OSM acquisition retrieval time is not canonical")
    area = acquisition.get("area")
    if not isinstance(area, dict) or set(area) != {
        "type",
        "crs",
        "south",
        "west",
        "north",
        "east",
    }:
        raise ValueError("governed OSM acquisition area does not match the bbox schema")
    timeout_seconds = acquisition.get("timeout_seconds")
    query, expected_area = governed_overpass_query(
        tuple(area.get(key) for key in ("south", "west", "north", "east")),
        timeout_seconds,  # type: ignore[arg-type]
    )
    if (
        area != expected_area
        or acquisition.get("query") != query
        or not isinstance(acquisition.get("query"), str)
    ):
        raise ValueError("governed OSM acquisition query or area is not canonical")
    if set(publisher) != {"generator", "osm_base"}:
        raise ValueError("governed OSM publisher receipt does not match the v1 schema")
    generator = publisher.get("generator")
    if generator is not None and (not isinstance(generator, str) or not generator.strip()):
        raise ValueError("governed OSM publisher generator is invalid")
    osm_base = _canonical_utc_receipt(publisher.get("osm_base"))
    if (
        source_payload.get("publisher_release") != osm_base
        or source_payload.get("effective_date") != osm_base[:10]
    ):
        raise ValueError("governed OSM publisher receipt differs from its Source Export")
    if set(http) != {"content_type", "date", "etag", "last_modified"} or any(
        value is not None and (not isinstance(value, str) or not value.strip())
        for value in http.values()
    ):
        raise ValueError("governed OSM HTTP metadata does not match the v1 schema")
    raw_sha256 = source_payload.get("raw_bytes_sha256")
    if (
        not isinstance(raw_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", raw_sha256)
        or set(raw_object) != {"path", "sha256", "byte_count"}
        or raw_object.get("sha256") != raw_sha256
        or raw_object.get("path") != f"objects/sha256/{raw_sha256}.osm"
        or not isinstance(raw_object.get("byte_count"), int)
        or isinstance(raw_object.get("byte_count"), bool)
        or raw_object["byte_count"] <= 0
    ):
        raise ValueError("governed OSM raw object does not match its Source Export")
    if licence != {
        "spdx": LICENCE,
        "attribution": OSM_ATTRIBUTION,
        "url": OSM_COPYRIGHT_URL,
    }:
        raise ValueError("governed OSM acquisition licence or attribution is unsupported")
    return source_payload, acquisition, publisher, raw_object


@contextmanager
def _osm_driver_configuration() -> object:
    """Apply the closed OGR tag mapping only while this adapter is reading."""

    if not _OSM_CONFIG_PATH.is_file():
        raise ValueError("OpenStreetMap OGR configuration is missing")
    with _GDAL_CONFIG_LOCK:
        prior = pyogrio.get_gdal_config_option("OSM_CONFIG_FILE")
        pyogrio.set_gdal_config_options({"OSM_CONFIG_FILE": str(_OSM_CONFIG_PATH)})
        try:
            yield
        finally:
            pyogrio.set_gdal_config_options({"OSM_CONFIG_FILE": prior})


def _raw_xml_receipt(source_path: Path) -> str:
    """Read only root/meta metadata needed to bind a raw XML receipt."""

    root_seen = False
    depth = 0
    receipts: list[str] = []
    try:
        for event, element in ElementTree.iterparse(source_path, events=("start", "end")):
            if event == "start":
                depth += 1
                if depth == 1:
                    root_seen = True
                    if element.tag != "osm" or element.attrib.get("version") != "0.6":
                        raise ValueError('governed OSM XML must have root <osm version="0.6">')
                elif depth == 2:
                    if _is_namespace_qualified_meta(element.tag):
                        raise ValueError("governed OSM XML meta must not be namespace-qualified")
                    if element.tag == "meta":
                        if receipts:
                            raise ValueError(
                                "governed OSM XML requires exactly one <meta osm_base=...Z> receipt"
                            )
                        if set(element.attrib) != {"osm_base"}:
                            raise ValueError(
                                "governed OSM XML meta must contain only the osm_base receipt"
                            )
                        receipts.append(_canonical_utc_receipt(element.attrib["osm_base"]))
                continue
            element.clear()
            depth -= 1
    except ValueError:
        raise
    except (ElementTree.ParseError, UnicodeDecodeError) as error:
        raise ValueError("governed Source Export is not well-formed OSM XML") from error
    if not root_seen:
        raise ValueError("governed Source Export is missing an OSM XML root")
    if len(receipts) != 1:
        raise ValueError("governed OSM XML requires exactly one <meta osm_base=...Z> receipt")
    return receipts[0]


def _raw_xml_generator(source_path: Path) -> str | None:
    try:
        for _event, element in ElementTree.iterparse(source_path, events=("start",)):
            if element.tag != "osm" or element.attrib.get("version") != "0.6":
                raise ValueError('governed OSM XML must have root <osm version="0.6">')
            generator = element.attrib.get("generator")
            if generator is not None and not generator.strip():
                raise ValueError("governed OSM XML generator must be non-empty when present")
            return generator
    except ValueError:
        raise
    except (ElementTree.ParseError, UnicodeDecodeError) as error:
        raise ValueError("governed Source Export is not well-formed OSM XML") from error
    raise ValueError("governed Source Export is missing an OSM XML root")


def _canonical_utc_receipt(value: object) -> str:
    if not isinstance(value, str) or not _UTC_RECEIPT.fullmatch(value):
        raise ValueError("governed OSM XML meta osm_base must be a UTC RFC3339 timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError("governed OSM XML meta osm_base is not a valid UTC timestamp") from error
    return value


def _is_namespace_qualified_meta(tag: object) -> bool:
    return isinstance(tag, str) and tag.startswith("{") and tag.endswith("}meta")


def _validate_ogr_schema(info: Mapping[str, object]) -> None:
    fields = tuple(str(field) for field in info.get("fields", ()))
    dtypes = tuple(str(dtype) for dtype in info.get("dtypes", ()))
    if len(fields) != len(dtypes):
        raise ValueError("governed OpenStreetMap layer has incompatible OGR schema metadata")
    actual_schema = dict(zip(fields, dtypes, strict=True))
    if actual_schema != _OSM_LINES_SCHEMA:
        raise ValueError("governed OpenStreetMap layer does not match the closed OGR schema")


def _requested_partition_keys(
    partition_keys: Sequence[EvidencePartitionKey], ingestion_contract: IngestionContract
) -> tuple[EvidencePartitionKey, ...]:
    keys = tuple(partition_keys)
    if len({key.fingerprint for key in keys}) != len(keys):
        raise ValueError("requested OpenStreetMap partitions must not contain duplicates")
    for key in keys:
        if (
            key.source_layer != ingestion_contract.source_layer
            or key.partition_scheme != ingestion_contract.partition_scheme
        ):
            raise ValueError("requested partition does not match its Ingestion Contract")
    return tuple(sorted(keys, key=lambda key: (key.cell, key.fingerprint)))


def _normalised_features(frame: object, transformer: Transformer) -> tuple[OsmNetworkFeature, ...]:
    features: list[OsmNetworkFeature] = []
    logical_keys: set[str] = set()
    for position in range(len(frame)):  # type: ignore[arg-type]
        row = frame.iloc[position]  # type: ignore[union-attr]
        geometry = frame.geometry.iloc[position]  # type: ignore[union-attr]
        if (
            geometry is None
            or geometry.is_empty
            or not geometry.is_valid
            or geometry.geom_type not in {"LineString", "MultiLineString"}
        ):
            raise ValueError("OpenStreetMap OSM lines layer contains unsupported geometry")
        logical_key = f"osm-way:{_osm_way_id(row['osm_id'])}"
        if logical_key in logical_keys:
            raise ValueError("OpenStreetMap OSM lines layer contains duplicate osm_id values")
        logical_keys.add(logical_key)
        try:
            transformed = transform_geometry(transformer.transform, geometry)
        except Exception as error:
            raise ValueError("OpenStreetMap geometry cannot be transformed to BNG") from error
        _validate_transformed_geometry(transformed)
        features.append(
            OsmNetworkFeature(
                logical_key=logical_key,
                geometry=transformed,
                attributes=MappingProxyType(_normalise_attributes(row)),
            )
        )
    return tuple(sorted(features, key=lambda feature: feature.logical_key))


def _validate_transformed_geometry(geometry: BaseGeometry) -> None:
    if (
        geometry is None
        or geometry.is_empty
        or geometry.geom_type not in {"LineString", "MultiLineString"}
    ):
        raise ValueError("transformed OpenStreetMap geometry is unsupported")
    coordinates = shapely.get_coordinates(geometry)
    if not len(coordinates) or not all(
        math.isfinite(float(ordinate)) for coordinate in coordinates for ordinate in coordinate
    ):
        raise ValueError("transformed OpenStreetMap geometry has non-finite coordinates")
    if not geometry.is_valid:
        raise ValueError("transformed OpenStreetMap geometry is unsupported")
    if not _SUPPORTED_BNG_EXTENT.covers(geometry):
        raise ValueError("transformed OpenStreetMap geometry is outside the supported BNG extent")


def _osm_way_id(value: object) -> str:
    source_id = _normalise_text(value, name="osm_id")
    if source_id is None:
        raise ValueError("OpenStreetMap osm_id must be a non-empty way identifier")
    if (
        not source_id.isascii()
        or not source_id.isdecimal()
        or source_id.startswith("0")
        or int(source_id) <= 0
    ):
        raise ValueError("OpenStreetMap osm_id must be one unambiguous positive way identifier")
    return source_id


def _normalise_attributes(row: object) -> dict[str, str | None]:
    parsed_tags = _parse_other_tags(row["other_tags"])  # type: ignore[index]
    attributes: dict[str, str | None] = {}
    for name in ATTRIBUTES:
        if name in _PROMOTED_ATTRIBUTES:
            promoted = _normalise_text(row[name], name=name)  # type: ignore[index]
            if name in parsed_tags:
                raise ValueError(f"OpenStreetMap tag {name} is ambiguous across OGR fields")
            attributes[name] = promoted
        else:
            attributes[name] = _normalise_text(parsed_tags.get(name), name=name)
    return attributes


def _normalise_text(value: object, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        try:
            if bool(pd.isna(value)):
                return None
        except (TypeError, ValueError):
            pass
        raise ValueError(f"OpenStreetMap {name} must be a string when present")
    normalised = value.strip()
    return normalised or None


def _parse_other_tags(value: object) -> dict[str, str]:
    source = _normalise_text(value, name="other_tags")
    if source is None:
        return {}
    position = 0
    tags: dict[str, str] = {}
    while position < len(source):
        position = _skip_space(source, position)
        key, position = _read_hstore_quoted(source, position)
        position = _skip_space(source, position)
        if not source.startswith("=>", position):
            raise ValueError("OpenStreetMap other_tags is not valid OGR hstore text")
        position = _skip_space(source, position + 2)
        tag_value, position = _read_hstore_quoted(source, position)
        if key in tags:
            raise ValueError("OpenStreetMap other_tags contains duplicate tag keys")
        tags[key] = tag_value
        position = _skip_space(source, position)
        if position == len(source):
            break
        if source[position] != ",":
            raise ValueError("OpenStreetMap other_tags is not valid OGR hstore text")
        position += 1
        if _skip_space(source, position) == len(source):
            raise ValueError("OpenStreetMap other_tags is not valid OGR hstore text")
    return tags


def _read_hstore_quoted(source: str, position: int) -> tuple[str, int]:
    if position >= len(source) or source[position] != '"':
        raise ValueError("OpenStreetMap other_tags is not valid OGR hstore text")
    position += 1
    parts: list[str] = []
    while position < len(source):
        character = source[position]
        if character == '"':
            return "".join(parts), position + 1
        if character == "\\":
            position += 1
            if position >= len(source):
                break
            if source[position] not in {'"', "\\"}:
                raise ValueError("OpenStreetMap other_tags contains an unsupported escape")
            parts.append(source[position])
        else:
            parts.append(character)
        position += 1
    raise ValueError("OpenStreetMap other_tags is not valid OGR hstore text")


def _skip_space(source: str, position: int) -> int:
    while position < len(source) and source[position].isspace():
        position += 1
    return position


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
