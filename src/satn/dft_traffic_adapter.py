"""Offline, governed adapter for Department for Transport traffic exports.

The adapter intentionally stops at count-point observations.  It does not
fetch the DfT API and it does not match observations to road geometry.  The
Local Evidence Store may materialise the returned point evidence and callers
can use :func:`observation_from_attributes` as a bounded conversion seam.
"""

from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Final

from pyproj import CRS, Transformer
from shapely.geometry import Point, box

from satn.evidence_contracts import (
    EvidencePartitionKey,
    IngestionContract,
    SourceExport,
    evidence_fingerprint,
    evidence_geometry_fingerprint,
)
from satn.traffic_evidence import (
    TrafficCoverageStatus,
    TrafficFreshnessState,
    TrafficMatchState,
    TrafficObservation,
)

TARGET_CRS: Final = "EPSG:27700"
SOURCE_FAMILY: Final = "dft"
DATASET: Final = "road-traffic-statistics"
PARTITION_SCHEME: Final = "bng-10km/v1"
LAYERS: Final = ("aadf", "aadf-by-direction", "raw-counts")

_LAYER_SOURCE_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "aadf": ("count_point_id", "year", "all_motor_vehicles"),
    "aadf-by-direction": (
        "count_point_id",
        "year",
        "direction_of_travel",
        "all_motor_vehicles",
    ),
    "raw-counts": ("count_point_id", "year", "count_date", "direction_of_travel"),
}
_ATTRIBUTES: Final[tuple[str, ...]] = (
    "count_point_id",
    "observation_year",
    "count_date",
    "direction_of_travel",
    "road_name",
    "road_category",
    "road_type",
    "start_junction_road_name",
    "end_junction_road_name",
    "latitude",
    "longitude",
    "easting",
    "northing",
    "declared_crs",
    "link_length_km",
    "all_motor_vehicles",
    "estimation_method",
    "estimation_method_detailed",
    "freshness_state",
    "match_state",
    "coverage_status",
    "match_proof_json",
    "match_state_fingerprint",
    "row_fingerprint",
    "traffic_observation_json",
    "source_row_json",
)
ATTRIBUTES: Final = _ATTRIBUTES


@dataclass(frozen=True)
class DftTrafficFeature:
    logical_key: str
    geometry: Point
    attributes: Mapping[str, str | None]


@dataclass(frozen=True)
class DftTrafficPartition:
    partition_key: EvidencePartitionKey
    features: tuple[DftTrafficFeature, ...]


def attributes() -> tuple[str, ...]:
    """Return the closed typed store attributes for all DfT layers."""

    return _ATTRIBUTES


def adapter_fingerprint() -> str:
    """Fingerprint adapter code and the versions that affect parsing."""

    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    runtime = {
        name: version(name)
        for name in ("pyproj", "shapely")
    }
    return evidence_fingerprint(
        {
            "contract": "satn-dft-traffic-byte-adapter-implementation/v1",
            "module_sha256": digest,
            "runtime_versions": runtime,
        }
    )


def contract_payload(layer: str, source_crs: str) -> dict[str, object]:
    """Return the exact ingestion contract for one DfT logical layer."""

    if layer not in LAYERS:
        raise ValueError(f"unsupported DfT traffic layer: {layer}")
    CRS.from_user_input(source_crs)
    required = _LAYER_SOURCE_COLUMNS[layer]
    accepted_schema = {name: "string" for name in required}
    accepted_schema.update(
        {
            "easting": "number|null",
            "northing": "number|null",
            "latitude": "number|null",
            "longitude": "number|null",
            "road_name": "string|null",
            "road_category": "string|null",
            "road_type": "string|null",
            "start_junction_road_name": "string|null",
            "end_junction_road_name": "string|null",
        }
    )
    if layer != "raw-counts":
        accepted_schema["all_motor_vehicles"] = "integer"
    return {
        "contract": "satn-ingestion-contract/v1",
        "source_layer": f"{SOURCE_FAMILY}/{layer}",
        "contract_version": "satn-dft-traffic-ingestion/v1",
        "accepted_schema": accepted_schema,
        "stable_feature_key_policy": "source-export-count-point-row-fingerprint/v1",
        "selected_attributes": sorted(_ATTRIBUTES),
        "normalisation": {
            "trim_strings": True,
            "missing_values": None,
            "point_geometry": "declared-crs-to-epsg-27700",
            "aadf_metric": "all_motor_vehicles-only",
            "raw_counts_are_not_aadf": True,
            "road_matching": "none",
        },
        "crs_transform": {
            "source_crs": source_crs,
            "target_crs": TARGET_CRS,
            "axis_order": "always_xy",
        },
        "partition_scheme": PARTITION_SCHEME,
        "spatial_predicate": "intersects",
        "implementation_dependency_fingerprint": adapter_fingerprint(),
    }


def ingestion_contract(layer: str, source_crs: str) -> IngestionContract:
    payload = contract_payload(layer, source_crs)
    payload.pop("contract")
    return IngestionContract(**payload)


def validate_export(source_export: SourceExport, contract: IngestionContract) -> Path:
    """Validate a retained local export without any network operation."""

    if source_export.source_family != SOURCE_FAMILY or source_export.dataset != DATASET:
        raise ValueError("unsupported governed Source Export for DfT traffic")
    if source_export.layer not in LAYERS:
        raise ValueError("DfT Source Export layer must be aadf, aadf-by-direction, or raw-counts")
    if source_export.format not in {"CSV", "csv", "zip+csv", "JSON", "json"}:
        raise ValueError("DfT traffic exports support CSV, zip+csv, and JSON only")
    expected = contract_payload(source_export.layer, source_export.declared_crs)
    if contract.canonical_payload() != expected:
        raise ValueError("unsupported or untrusted DfT traffic Ingestion Contract")
    retained = source_export.provenance.get("retained_path")
    if not isinstance(retained, str) or not retained:
        raise ValueError("DfT Source Export provenance requires retained_path")
    path = Path(retained)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"governed DfT Source Export is not retained at {path}")
    if _sha256_file(path) != source_export.raw_bytes_sha256:
        raise ValueError("governed DfT Source Export checksum does not match retained bytes")
    headers = _headers(path)
    rows = _rows(path)
    _validate_provenance(
        source_export.provenance,
        path=path,
        layer=source_export.layer,
        headers=headers,
        row_count=len(rows),
        contract=contract,
    )
    missing = set(_LAYER_SOURCE_COLUMNS[source_export.layer]) - set(headers)
    if missing:
        raise ValueError(
            "DfT traffic export is missing required columns: "
            + ", ".join(sorted(missing))
        )
    if source_export.layer == "raw-counts" and "all_motor_vehicles" in headers:
        raise ValueError("raw-counts export must not contain AADF all_motor_vehicles")
    if source_export.layer == "aadf" and "direction_of_travel" in headers:
        raise ValueError("plain aadf exports cannot contain directional columns")
    if source_export.layer == "aadf-by-direction" and "direction_of_travel" not in headers:
        raise ValueError("aadf-by-direction export requires direction_of_travel")
    return path


def traffic_claim_signature(observation: Mapping[str, object]) -> tuple[object, ...]:
    """Return the normalized fields used to compare one traffic claim."""

    return tuple(
        observation.get(name)
        for name in (
            "count_point_id",
            "observation_year",
            "direction_of_travel",
            "all_motor_vehicles",
            "road_name",
            "road_category",
            "road_type",
            "start_junction_road_name",
            "end_junction_road_name",
            "latitude",
            "longitude",
            "easting",
            "northing",
            "link_length_km",
            "estimation_method",
            "estimation_method_detailed",
        )
    )


def _validate_provenance(
    provenance: Mapping[str, object],
    *,
    path: Path,
    layer: str,
    headers: Sequence[str],
    row_count: int,
    contract: IngestionContract,
) -> None:
    """Validate the closed DfT acquisition receipt declared beside an export."""

    if not isinstance(provenance, Mapping):
        raise ValueError("DfT Source Export provenance must be a mapping")

    def value(name: str, *nested: tuple[str, str]) -> object:
        if name in provenance:
            return provenance[name]
        for container_name, child_name in nested:
            container = provenance.get(container_name)
            if isinstance(container, Mapping) and child_name in container:
                return container[child_name]
        return None

    endpoint = value(
        "acquisition_url",
        ("acquisition", "url"),
        ("acquisition", "endpoint"),
        ("acquisition", "local_publication"),
    )
    method = value("acquisition_method", ("acquisition", "method"))
    query = value(
        "query_parameters",
        ("acquisition", "query_parameters"),
        ("acquisition", "query"),
    )
    page = value("acquisition_page", ("acquisition", "page"))
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError("DfT provenance requires acquisition URL or local publication")
    if not isinstance(method, str) or not method.strip():
        raise ValueError("DfT provenance requires acquisition method")
    if query is None:
        raise ValueError("DfT provenance requires acquisition query parameters")
    if page is None or not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("DfT provenance requires a positive acquisition page")

    retrieved_at = value("retrieved_at", ("retrieval", "retrieved_at"))
    if not isinstance(retrieved_at, str) or not retrieved_at.endswith("Z"):
        raise ValueError("DfT provenance requires a UTC retrieved_at timestamp")
    try:
        parsed_retrieved_at = datetime.fromisoformat(retrieved_at[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("DfT provenance retrieved_at is not RFC3339 UTC") from error
    if parsed_retrieved_at.tzinfo != UTC:
        raise ValueError("DfT provenance retrieved_at must be UTC")

    content_type = value("content_type", ("retrieval", "content_type"))
    byte_count = value("byte_count", ("retrieval", "byte_count"))
    if not isinstance(content_type, str) or not content_type.strip():
        raise ValueError("DfT provenance requires content_type")
    if (
        not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or byte_count != path.stat().st_size
    ):
        raise ValueError("DfT provenance byte_count does not match retained bytes")
    for name in ("etag", "last_modified"):
        metadata = value(name, ("retrieval", name))
        if metadata is not None and not isinstance(metadata, str):
            raise ValueError(f"DfT provenance {name} must be text or null")

    archive_members = value("archive_members", ("content", "archive_members"))
    csv_header = value("csv_header", ("content", "csv_header"))
    schema_fingerprint = value("schema_fingerprint", ("content", "schema_fingerprint"))
    normalisation_fingerprint = value(
        "normalisation_contract_fingerprint",
        ("content", "normalisation_contract_fingerprint"),
    )
    if (
        not isinstance(archive_members, Sequence)
        or isinstance(archive_members, (str, bytes))
        or any(not isinstance(member, str) or not member for member in archive_members)
    ):
        raise ValueError("DfT provenance archive_members must be a list of paths")
    if (
        not isinstance(csv_header, Sequence)
        or isinstance(csv_header, (str, bytes))
        or tuple(csv_header) != tuple(headers)
    ):
        raise ValueError("DfT provenance csv_header does not match the export schema")
    if not isinstance(schema_fingerprint, str) or len(schema_fingerprint) != 64:
        raise ValueError("DfT provenance requires a schema_fingerprint")
    if any(character not in "0123456789abcdef" for character in schema_fingerprint):
        raise ValueError("DfT provenance schema_fingerprint must be lowercase SHA-256")
    expected_schema_fingerprint = evidence_fingerprint(
        {
            "contract": "satn-dft-traffic-schema/v1",
            "layer": layer,
            "headers": list(headers),
        }
    )
    if schema_fingerprint != expected_schema_fingerprint:
        raise ValueError("DfT provenance schema_fingerprint does not match the export")
    if normalisation_fingerprint != contract.fingerprint:
        raise ValueError("DfT provenance normalisation contract fingerprint does not match")
    if path.suffix.lower() == ".zip" and not archive_members:
        raise ValueError("DfT zip provenance requires archive_members")
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            csv_members = {
                name for name in archive.namelist() if name.lower().endswith(".csv")
            }
        if not csv_members <= set(archive_members):
            raise ValueError("DfT provenance archive_members omits a CSV member")
    methodology_url = value("methodology_url", ("content", "methodology_url"))
    publication_id = value(
        "publication_id",
        ("content", "publication_id"),
        ("content", "release_id"),
    )
    if not isinstance(methodology_url, str) or not methodology_url.strip():
        raise ValueError("DfT provenance requires methodology_url")
    if not isinstance(publication_id, str) or not publication_id.strip():
        raise ValueError("DfT provenance requires publication_id")
    declared_row_count = value("row_count", ("content", "row_count"))
    pagination_bound = value(
        "pagination_bound",
        ("content", "pagination_bound"),
    )
    if declared_row_count != row_count:
        raise ValueError("DfT provenance row_count does not match the export")
    if (
        not isinstance(pagination_bound, int)
        or isinstance(pagination_bound, bool)
        or pagination_bound < 1
    ):
        raise ValueError("DfT provenance requires a positive pagination_bound")


def read_partition(
    source_path: Path,
    source_export: SourceExport,
    contract: IngestionContract,
    partition_key: EvidencePartitionKey,
) -> DftTrafficPartition:
    """Read one BNG partition, retaining point observations without clipping."""

    if partition_key.source_layer != contract.source_layer:
        raise ValueError("requested DfT partition does not match its contract")
    layer = source_export.layer
    if (
        partition_key.partition_scheme != PARTITION_SCHEME
        or contract.source_layer != f"dft/{layer}"
    ):
        raise ValueError("requested DfT partition has an unsupported identity")
    validate_export(source_export, contract)
    transformer = Transformer.from_crs(source_export.declared_crs, TARGET_CRS, always_xy=True)
    cell = box(*_bng_10km_bounds(partition_key.cell))
    features: list[DftTrafficFeature] = []
    seen: set[str] = set()
    for raw in _rows(source_path):
        normalised, point, _observation = _normalise_row(
            raw, layer, source_export, transformer
        )
        if not cell.intersects(point):
            continue
        row_fingerprint = normalised["row_fingerprint"]
        logical_key = (
            f"traffic:{layer}:{normalised['count_point_id']}"
            f":{normalised['observation_year']}"
            f":{normalised.get('direction_of_travel') or '-'}:{row_fingerprint}"
        )
        if logical_key in seen:
            continue
        seen.add(logical_key)
        features.append(DftTrafficFeature(logical_key, point, normalised))
    features.sort(key=lambda item: item.logical_key)
    return DftTrafficPartition(partition_key, tuple(features))


def read_partitions(
    source_path: Path,
    source_export: SourceExport,
    contract: IngestionContract,
    partition_keys: Sequence[EvidencePartitionKey],
) -> tuple[DftTrafficPartition, ...]:
    """Read a deterministic ordered set of BNG partitions."""

    return tuple(
        read_partition(source_path, source_export, contract, key)
        for key in sorted(partition_keys, key=lambda item: item.fingerprint)
    )


def observation_from_attributes(attributes: Mapping[str, object]) -> TrafficObservation:
    """Convert a stored DfT row to its typed observation at a pure seam."""

    payload = attributes.get("traffic_observation_json")
    if isinstance(payload, str):
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("stored DfT traffic observation JSON is invalid") from error
        if not isinstance(value, dict):
            raise ValueError("stored DfT traffic observation JSON must be an object")
        return TrafficObservation.model_validate(value)
    raise ValueError("stored row is not a DfT traffic observation")


def observation_from_query_row(row: object) -> TrafficObservation:
    attrs = getattr(row, "attributes", None)
    if not isinstance(attrs, Mapping):
        raise ValueError("query row does not contain DfT traffic attributes")
    return observation_from_attributes(attrs)


def _normalise_row(
    raw: Mapping[str, object],
    layer: str,
    source_export: SourceExport,
    transformer: Transformer,
) -> tuple[dict[str, str | None], Point, TrafficObservation]:
    row = {str(key).strip(): _clean(value) for key, value in raw.items()}
    count_point_id = _required(row.get("count_point_id"), "count_point_id")
    year = _integer(row.get("year", row.get("observation_year")), "year", minimum=1900)
    if layer == "aadf-by-direction" and not _required(
        row.get("direction_of_travel"), "direction_of_travel"
    ):
        raise ValueError("aadf-by-direction rows require direction_of_travel")
    if layer == "raw-counts" and not _required(
        row.get("direction_of_travel"), "direction_of_travel"
    ):
        raise ValueError("raw-counts rows require direction_of_travel")
    direction = _direction(row.get("direction_of_travel"))
    if layer == "raw-counts" and row.get("all_motor_vehicles") not in (None, ""):
        raise ValueError("raw-counts rows cannot classify all_motor_vehicles")
    easting, northing = _coordinates(row, source_export.declared_crs, transformer)
    point = Point(easting, northing)
    if point.is_empty or not point.is_valid:
        raise ValueError("DfT traffic point geometry is invalid")
    source_row_payload = _canonical_row_payload(row)
    row_fingerprint = evidence_fingerprint(
        {"contract": "satn-dft-traffic-source-row/v1", "row": source_row_payload}
    )
    freshness = _enum_value(
        row.get("freshness_state"), TrafficFreshnessState, TrafficFreshnessState.UNKNOWN
    )
    match = _enum_value(row.get("match_state"), TrafficMatchState, TrafficMatchState.UNKNOWN)
    coverage = _enum_value(
        row.get("coverage_status"), TrafficCoverageStatus, TrafficCoverageStatus.SAMPLED
    )
    observation = TrafficObservation(
        observation_id=evidence_fingerprint(
            {
                "contract": "satn-dft-traffic-observation/v1",
                "source_export_fingerprint": source_export.fingerprint,
                "row_fingerprint": row_fingerprint,
            }
        ),
        source_export_fingerprint=source_export.fingerprint,
        source_layer=layer,
        count_point_id=count_point_id,
        observation_year=year,
        count_date=_date(row.get("count_date")),
        direction_of_travel=direction,
        road_name=_text(row.get("road_name")),
        road_category=_text(row.get("road_category")),
        road_type=_text(row.get("road_type")),
        start_junction_road_name=_text(row.get("start_junction_road_name")),
        end_junction_road_name=_text(row.get("end_junction_road_name")),
        latitude=_float(row.get("latitude")),
        longitude=_float(row.get("longitude")),
        easting=easting,
        northing=northing,
        declared_crs=TARGET_CRS,
        geometry_fingerprint=evidence_geometry_fingerprint(point, TARGET_CRS),
        link_length_km=_float(row.get("link_length_km")),
        all_motor_vehicles=(
            None
            if layer == "raw-counts"
            else _integer(
                row.get("all_motor_vehicles"), "all_motor_vehicles", minimum=0
            )
        ),
        estimation_method=_text(row.get("estimation_method")),
        estimation_method_detailed=_text(row.get("estimation_method_detailed")),
        freshness_state=freshness,
        match_state=match,
        coverage_status=coverage,
        row_fingerprint=row_fingerprint,
        evidence_ids=(source_export.fingerprint, row_fingerprint),
        provenance_ids=(source_export.fingerprint,),
    )
    values: dict[str, str | None] = {
        "count_point_id": count_point_id,
        "observation_year": str(year),
        "count_date": observation.count_date.isoformat() if observation.count_date else None,
        "direction_of_travel": observation.direction_of_travel,
        "road_name": observation.road_name,
        "road_category": observation.road_category,
        "road_type": observation.road_type,
        "start_junction_road_name": observation.start_junction_road_name,
        "end_junction_road_name": observation.end_junction_road_name,
        "latitude": _decimal_text(observation.latitude),
        "longitude": _decimal_text(observation.longitude),
        "easting": _decimal_text(easting),
        "northing": _decimal_text(northing),
        "declared_crs": TARGET_CRS,
        "link_length_km": _decimal_text(observation.link_length_km),
        "all_motor_vehicles": (
            None
            if observation.all_motor_vehicles is None
            else str(observation.all_motor_vehicles)
        ),
        "estimation_method": observation.estimation_method,
        "estimation_method_detailed": observation.estimation_method_detailed,
        "freshness_state": observation.freshness_state.value,
        "match_state": observation.match_state.value,
        "coverage_status": observation.coverage_status.value,
        "match_proof_json": json.dumps(
            observation.match_proof,
            sort_keys=True,
            separators=(",", ":"),
        )
        if observation.match_proof is not None
        else None,
        "match_state_fingerprint": observation.match_state_fingerprint,
        "row_fingerprint": row_fingerprint,
        "traffic_observation_json": json.dumps(
            observation.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ),
        "source_row_json": json.dumps(source_row_payload, sort_keys=True, separators=(",", ":")),
    }
    return values, point, observation


def _rows(path: Path) -> tuple[Mapping[str, object], ...]:
    if path.suffix.lower() in {".json", ".geojson"}:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("data", payload.get("results", payload))
        if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
            raise ValueError("DfT JSON export must contain a list of row objects")
        return tuple(payload)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            members = sorted(name for name in archive.namelist() if name.lower().endswith(".csv"))
            if len(members) != 1:
                raise ValueError("DfT zip+csv export must contain exactly one CSV member")
            with archive.open(members[0]) as stream:
                return tuple(csv.DictReader(line.decode("utf-8-sig") for line in stream))
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return tuple(csv.DictReader(stream))


def _headers(path: Path) -> tuple[str, ...]:
    rows = _rows(path)
    if not rows:
        raise ValueError("DfT traffic export contains no rows")
    return tuple(str(key).strip() for key in rows[0])


def _coordinates(
    row: Mapping[str, object], declared_crs: str, transformer: Transformer
) -> tuple[float, float]:
    east = _float(row.get("easting"))
    north = _float(row.get("northing"))
    if east is not None and north is not None:
        return tuple(float(value) for value in transformer.transform(east, north))
    latitude = _float(row.get("latitude"))
    longitude = _float(row.get("longitude"))
    if latitude is None or longitude is None:
        raise ValueError("DfT traffic rows require easting/northing or latitude/longitude")
    if declared_crs.upper() != "EPSG:4326":
        raise ValueError("latitude/longitude rows require an explicit EPSG:4326 Source Export CRS")
    return tuple(float(value) for value in transformer.transform(longitude, latitude))


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
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean(value: object) -> object:
    if value is None:
        return None
    text = str(value).strip()
    return None if text == "" or text.lower() in {"null", "nan", "na"} else text


def _text(value: object) -> str | None:
    return None if value is None else str(value).strip() or None


def _required(value: object, name: str) -> str:
    text = _text(value)
    if text is None:
        raise ValueError(f"DfT traffic {name} is mandatory")
    return text


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    text = _required(value, name)
    try:
        parsed = int(text)
    except ValueError as error:
        raise ValueError(f"DfT traffic {name} must be an integer") from error
    if minimum is not None and parsed < minimum:
        raise ValueError(f"DfT traffic {name} must be >= {minimum}")
    return parsed


def _float(value: object) -> float | None:
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = float(text)
    except ValueError as error:
        raise ValueError(f"DfT traffic numeric value is invalid: {text}") from error
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        raise ValueError("DfT traffic numeric values must be finite")
    return parsed


def _decimal_text(value: float | None) -> str | None:
    return None if value is None else format(value, ".12g")


def _date(value: object) -> date | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError as error:
        raise ValueError(f"DfT traffic count_date is invalid: {text}") from error


def _direction(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    upper = text.upper()
    if upper not in {"N", "S", "E", "W", "C"}:
        raise ValueError("DfT traffic direction_of_travel must be N, S, E, W, or C")
    return upper


def _enum_value(value: object, enum_type: type, default: object) -> object:
    text = _text(value)
    if text is None:
        return default
    try:
        return enum_type(text)
    except ValueError as error:
        raise ValueError(f"invalid DfT traffic state: {text}") from error


def _canonical_row_payload(row: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): (None if value is None else str(value).strip())
        for key, value in sorted(row.items())
    }
