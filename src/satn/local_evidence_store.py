"""Additive DuckDB sidecar for immutable Local Evidence materialisations."""

from __future__ import annotations

import hashlib
import json
import math
import platform as platform_module
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from shapely import from_geojson, from_wkb, to_wkb
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from satn.evidence_contracts import (
    EvidenceCoverage,
    EvidencePartitionAttestation,
    EvidencePartitionContent,
    EvidencePartitionKey,
    IngestionContract,
    SourceExport,
    canonical_evidence_geometry,
    canonical_evidence_json,
    evidence_fingerprint,
    evidence_geometry_fingerprint,
)
from satn.open_roads_adapter import (
    ATTRIBUTES as _OPEN_ROADS_ATTRIBUTES,
)
from satn.open_roads_adapter import (
    SOURCE_LAYER as _OPEN_ROADS_SOURCE_LAYER,
)
from satn.open_roads_adapter import (
    adapter_fingerprint as _adapter_fingerprint,
)
from satn.open_roads_adapter import (
    contract_payload as _open_roads_contract_payload,
)
from satn.open_roads_adapter import (
    read_partition as _read_open_roads_adapter_partition,
)
from satn.open_roads_adapter import (
    validate_export as _validate_open_roads_export,
)
from satn.osm_network_adapter import (
    ATTRIBUTES as _OSM_NETWORK_ATTRIBUTES,
)
from satn.osm_network_adapter import (
    SOURCE_LAYER as _OSM_NETWORK_SOURCE_LAYER,
)
from satn.osm_network_adapter import (
    contract_payload as _osm_network_contract_payload,
)
from satn.osm_network_adapter import (
    read_partitions as _read_osm_network_adapter_partitions,
)
from satn.osm_network_adapter import (
    validate_export as _validate_osm_network_export,
)


class SpatialRuntimeError(RuntimeError):
    """The explicitly provisioned local DuckDB Spatial runtime is unusable."""


class EvidenceStoreSchemaError(RuntimeError):
    """An existing Local Evidence Store cannot be trusted or used."""


Availability = Literal["available", "no-data", "explicit-unknown"]
StoreState = Literal["uninitialised", "ready"]


@dataclass(frozen=True)
class _EvidenceFeature:
    """One byte-grounded feature accepted by a closed source adapter."""

    logical_key: str
    geometry: BaseGeometry
    attributes: Mapping[str, object]


@dataclass(frozen=True)
class _EvidencePartitionInput:
    """One prevalidated source-layer/BNG-cell input for the transaction seam."""

    source_export: SourceExport
    ingestion_contract: IngestionContract
    partition_key: EvidencePartitionKey
    availability: Availability
    features: tuple[_EvidenceFeature, ...]


@dataclass(frozen=True)
class RefreshResult:
    """Immutable result of one committed Evidence Refresh."""

    coverage: EvidenceCoverage


@dataclass(frozen=True)
class EvidenceStoreStatus:
    """The current immutable Evidence Coverage, if a refresh has completed."""

    state: StoreState
    current_coverage: EvidenceCoverage | None


QueryPredicate = Literal["intersects", "within", "contains"]


@dataclass(frozen=True)
class EvidenceQueryRow:
    """One exact, deduplicated row from a pinned Evidence Coverage state."""

    source_export_fingerprint: str
    logical_key: str
    feature_content_fingerprint: str
    geometry_fingerprint: str
    geometry: BaseGeometry
    crs: str
    attributes: Mapping[str, object]
    attestation_fingerprints: tuple[str, ...]
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if self.crs != "EPSG:27700":
            raise ValueError("Evidence query rows must use EPSG:27700")
        if not isinstance(self.logical_key, str) or not self.logical_key:
            raise ValueError("Evidence query row logical_key must be non-empty")
        for name, value in (
            ("source_export_fingerprint", self.source_export_fingerprint),
            ("feature_content_fingerprint", self.feature_content_fingerprint),
            ("geometry_fingerprint", self.geometry_fingerprint),
        ):
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"Evidence query row {name} must be a full SHA-256")
        if evidence_geometry_fingerprint(self.geometry, self.crs) != self.geometry_fingerprint:
            raise ValueError("Evidence query row geometry does not match its fingerprint")
        attributes = dict(self.attributes)
        canonical_evidence_json(attributes)
        attestations = tuple(sorted(self.attestation_fingerprints))
        if not attestations:
            raise ValueError("Evidence query rows require an attestation")
        if len(set(attestations)) != len(attestations):
            raise ValueError("Evidence query row attestation fingerprints must be unique")
        if any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in attestations):
            raise ValueError("Evidence query row attestations must be full SHA-256 values")
        payload = self.canonical_payload(
            attributes=attributes, attestation_fingerprints=attestations
        )
        expected = evidence_fingerprint(payload)
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("Evidence query row fingerprint is stale")
        object.__setattr__(self, "attributes", _freeze_mapping(attributes))
        object.__setattr__(self, "attestation_fingerprints", attestations)
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(
        self,
        *,
        attributes: Mapping[str, object] | None = None,
        attestation_fingerprints: tuple[str, ...] | None = None,
    ) -> dict[str, object]:
        """Return the response identity, including the exact spatial provenance."""

        return {
            "contract": "satn-evidence-query-row/v1",
            "source_export_fingerprint": self.source_export_fingerprint,
            "logical_key": self.logical_key,
            "feature_content_fingerprint": self.feature_content_fingerprint,
            "geometry_fingerprint": self.geometry_fingerprint,
            "crs": self.crs,
            "attributes": dict(self.attributes if attributes is None else attributes),
            "attestation_fingerprints": list(
                self.attestation_fingerprints
                if attestation_fingerprints is None
                else attestation_fingerprints
            ),
        }


@dataclass(frozen=True)
class EvidenceQueryResult:
    """Immutable response and replay manifest for one exact current or historical query."""

    rows: tuple[EvidenceQueryRow, ...]
    manifest: Mapping[str, object]
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if any(not isinstance(row, EvidenceQueryRow) for row in self.rows):
            raise ValueError("Evidence query results require EvidenceQueryRow values")
        rows = tuple(
            sorted(self.rows, key=lambda row: (row.source_export_fingerprint, row.logical_key))
        )
        if len({(row.source_export_fingerprint, row.logical_key) for row in rows}) != len(rows):
            raise ValueError("Evidence query result rows must be deduplicated")
        manifest = dict(self.manifest)
        manifest.pop("result_fingerprint", None)
        required_manifest_fields = {
            "contract",
            "query_contract",
            "coverage_contract",
            "coverage_state_fingerprint",
            "source_layer",
            "selector_geometry_fingerprint",
            "selector_crs",
            "predicate",
            "predicate_operand_order",
            "filters",
            "projection",
            "required_partition_key_fingerprints",
            "required_bng_10km_cells",
            "consulted_attestation_fingerprints",
            "availability_counts",
            "row_count",
            "row_fingerprints",
        }
        if not required_manifest_fields <= set(manifest):
            raise ValueError("Evidence query result manifest is incomplete")
        if manifest["row_count"] != len(rows) or manifest["row_fingerprints"] != [
            row.fingerprint for row in rows
        ]:
            raise ValueError("Evidence query result manifest rows do not match its result")
        canonical_evidence_json(manifest)
        expected = evidence_fingerprint(
            {
                "contract": "satn-evidence-query-result/v1",
                "manifest": manifest,
                "row_fingerprints": [row.fingerprint for row in rows],
            }
        )
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("Evidence query result fingerprint is stale")
        manifest["result_fingerprint"] = expected
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "manifest", _freeze_mapping(manifest))
        object.__setattr__(self, "fingerprint", expected)


@dataclass(frozen=True)
class _NormalisedQueryFields:
    """Closed, deterministic attribute selection for a typed evidence layer."""

    filters: tuple[tuple[str, object], ...]
    projection: tuple[str, ...]


@dataclass(frozen=True)
class SpatialRuntimeLock:
    """Pinned Python-wheel and Spatial-extension identity for one platform."""

    duckdb_version: str
    spatial_version: str
    platform: str
    extension_relative_path: str
    extension_sha256: str

    def canonical_payload(self) -> dict[str, str]:
        """Return the exact offline runtime identity recorded in each store."""

        return {
            "contract": "satn-duckdb-spatial-runtime/v1",
            "duckdb_version": self.duckdb_version,
            "spatial_version": self.spatial_version,
            "platform": self.platform,
            "extension_relative_path": self.extension_relative_path,
            "extension_sha256": self.extension_sha256,
        }

    @property
    def fingerprint(self) -> str:
        """Return the full fingerprint of the exact runtime-lock payload."""

        return evidence_fingerprint(self.canonical_payload())

    @classmethod
    def from_json(cls, path: Path) -> SpatialRuntimeLock:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SpatialRuntimeError(f"cannot read Spatial runtime lock: {path}") from error
        if (
            not isinstance(payload, dict)
            or payload.get("contract") != "satn-duckdb-spatial-runtime/v1"
        ):
            raise SpatialRuntimeError("Spatial runtime lock has an unsupported contract")
        names = (
            "duckdb_version",
            "spatial_version",
            "platform",
            "extension_relative_path",
            "extension_sha256",
        )
        values = {name: payload.get(name) for name in names}
        if not all(isinstance(value, str) and value for value in values.values()):
            raise SpatialRuntimeError("Spatial runtime lock is incomplete")
        checksum = str(values["extension_sha256"])
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise SpatialRuntimeError("Spatial runtime lock has an invalid extension checksum")
        relative_path = Path(str(values["extension_relative_path"]))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise SpatialRuntimeError("Spatial runtime lock has an unsafe extension path")
        return cls(
            duckdb_version=str(values["duckdb_version"]),
            spatial_version=str(values["spatial_version"]),
            platform=str(values["platform"]),
            extension_relative_path=relative_path.as_posix(),
            extension_sha256=checksum,
        )


def provision_spatial_runtime(
    *,
    runtime_lock_path: Path,
    extension_archive: Path,
    extension_cache: Path,
) -> Path:
    """Explicitly provision one checked Spatial binary into the declared cache.

    This is the only provisioning operation.  It copies a caller-supplied local
    archive after checksum and platform validation; it never contacts DuckDB's
    extension repository or invokes ``INSTALL``.
    """

    runtime_lock = SpatialRuntimeLock.from_json(runtime_lock_path)
    actual_platform = _runtime_platform()
    if runtime_lock.platform != actual_platform:
        raise SpatialRuntimeError(
            f"Spatial runtime lock targets {runtime_lock.platform}, not {actual_platform}"
        )
    if not extension_archive.is_file():
        raise SpatialRuntimeError(
            f"pinned Spatial extension archive is missing: {extension_archive}"
        )
    if _sha256_file(extension_archive) != runtime_lock.extension_sha256:
        raise SpatialRuntimeError("pinned Spatial extension archive checksum does not match")
    destination = extension_cache / runtime_lock.extension_relative_path
    if destination.exists():
        if _sha256_file(destination) != runtime_lock.extension_sha256:
            raise SpatialRuntimeError("cached Spatial extension checksum does not match")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    shutil.copyfile(extension_archive, temporary)
    if _sha256_file(temporary) != runtime_lock.extension_sha256:
        temporary.unlink(missing_ok=True)
        raise SpatialRuntimeError("provisioned Spatial extension checksum does not match")
    temporary.replace(destination)
    return destination


class LocalEvidenceStore:
    """The sole deep physical seam for Local Evidence materialisations.

    Provisioning stays outside this object.  Normal operations only verify and
    load the exact local binary pinned by the runtime lock; they never use a
    global DuckDB extension directory, automatic extension loading or INSTALL.
    """

    def __init__(
        self,
        *,
        store_path: Path,
        runtime_lock_path: Path,
        extension_cache: Path,
    ) -> None:
        self._store_path = store_path
        self._runtime_lock_path = runtime_lock_path
        self._extension_cache = extension_cache

    def initialise(self) -> None:
        """Create a new exact store, or validate an exact existing store."""

        self._verify_runtime()
        if self._store_path.exists():
            connection = self._open_initialised(read_only=True)
            connection.close()
            return
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect(read_only=False)
        transaction_started = False
        try:
            connection.execute("BEGIN TRANSACTION")
            transaction_started = True
            self._create_schema(connection)
            self._record_store_metadata(connection)
            connection.execute("COMMIT")
            transaction_started = False
        except Exception:
            if transaction_started:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def refresh(
        self,
        *,
        source_export: SourceExport,
        ingestion_contract: IngestionContract,
        partition_keys: tuple[EvidencePartitionKey, ...],
    ) -> RefreshResult:
        """Union exact governed BNG partitions into the current immutable coverage."""

        if not partition_keys:
            raise ValueError("refresh requires at least one Evidence Partition Key")
        if len({key.fingerprint for key in partition_keys}) != len(partition_keys):
            raise ValueError("refresh Evidence Partition Keys must be unique")
        connection = self._open_initialised(read_only=False)
        transaction_started = False
        try:
            connection.execute("BEGIN TRANSACTION")
            transaction_started = True
            source_path = self._validated_source_path(source_export, ingestion_contract)
            current_coverage = self._current_coverage(connection)
            if current_coverage is None:
                attestations: list[EvidencePartitionAttestation] = []
                requested_keys: dict[str, EvidencePartitionKey] = {}
            else:
                self._verify_coverage(connection, current_coverage)
                if any(
                    item.source_export.fingerprint == source_export.fingerprint
                    for item in current_coverage.attestations
                ):
                    self._insert_source_export(connection, source_export)
                    current_coverage = self._current_coverage(connection)
                    assert current_coverage is not None
                    self._verify_coverage(connection, current_coverage)
                self._verify_retained_source_bytes(current_coverage)
                attestations = list(current_coverage.attestations)
                requested_keys = {
                    key.fingerprint: key for key in current_coverage.requested_partition_keys
                }
            current_by_key = {
                item.partition_content.partition_key.fingerprint: item for item in attestations
            }
            observations = self._observations_by_source_export(attestations)
            missing_keys: list[EvidencePartitionKey] = []
            for partition_key in partition_keys:
                requested_keys[partition_key.fingerprint] = partition_key
                existing = current_by_key.get(partition_key.fingerprint)
                if existing is not None:
                    content = existing.partition_content
                    if (
                        existing.source_export.fingerprint != source_export.fingerprint
                        or content.ingestion_contract.fingerprint != ingestion_contract.fingerprint
                    ):
                        raise ValueError(
                            "requested partition already has a different Source Export or "
                            "Ingestion Contract; source replacement is not enabled"
                        )
                    continue
                missing_keys.append(partition_key)
            if not missing_keys:
                connection.execute("COMMIT")
                transaction_started = False
                assert current_coverage is not None
                return RefreshResult(coverage=current_coverage)
            self._create_staging_tables(connection)
            for partition in self._read_missing_partitions(
                source_path,
                source_export,
                ingestion_contract,
                tuple(missing_keys),
            ):
                attestation, rows = self._normalise_partition(partition)
                self._add_observations(observations, attestation)
                self._stage_partition(connection, attestation, rows)
                attestations.append(attestation)
            if _sha256_file(source_path) != source_export.raw_bytes_sha256:
                raise ValueError("governed Source Export changed while it was being read")
            coverage = EvidenceCoverage(
                tuple(attestations),
                requested_partition_keys=tuple(requested_keys.values()),
                state="complete",
            )
            self._commit_staged_refresh(connection, coverage)
            connection.execute("COMMIT")
            transaction_started = False
            return RefreshResult(coverage=coverage)
        except Exception:
            if transaction_started:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def resolve_coverage(
        self,
        *,
        state_fingerprint: str,
        verify: bool = True,
    ) -> EvidenceCoverage:
        """Resolve exactly one explicitly named immutable historical coverage state."""

        if (
            not isinstance(state_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", state_fingerprint) is None
        ):
            raise ValueError("coverage state fingerprint must be a full lowercase SHA-256")
        if not self._store_path.is_file():
            raise ValueError("Local Evidence Store has no coverage state")
        connection = self._open_initialised(read_only=True)
        try:
            try:
                coverage = self._coverage_by_fingerprint(connection, state_fingerprint)
                if verify:
                    self._verify_coverage(connection, coverage)
                    self._verify_retained_source_bytes(coverage)
                return coverage
            except EvidenceStoreSchemaError:
                raise
            except LookupError as error:
                raise ValueError(
                    f"Local Evidence Store coverage state {state_fingerprint} is not found"
                ) from error
            except Exception as error:
                raise _schema_error(
                    "Local Evidence Store requested coverage registry is invalid"
                ) from error
        finally:
            connection.close()

    def query(
        self,
        *,
        state_fingerprint: str | None = None,
        source_layer: str,
        selector: BaseGeometry | None = None,
        bbox: tuple[object, object, object, object] | None = None,
        selector_crs: str = "EPSG:27700",
        predicate: QueryPredicate = "intersects",
        filters: Mapping[str, object] | None = None,
        projection: tuple[str, ...] = (),
    ) -> EvidenceQueryResult:
        """Read an exact subset from current or explicitly pinned historical coverage.

        Predicates always use ``feature_geometry PREDICATE selector_geometry``:
        ``within`` therefore means that the returned road feature lies within the
        selector, while ``contains`` means that the feature contains it.
        """

        if state_fingerprint is not None:
            _validate_query_state_fingerprint(state_fingerprint)
        selector = _query_selector_input(selector=selector, bbox=bbox)
        selector_geometry, selector_fingerprint = _canonical_query_selector(
            selector, selector_crs
        )
        table = _LAYER_TABLES.get(source_layer)
        if table is None:
            raise ValueError(f"unsupported Local Evidence query source layer: {source_layer}")
        predicate_sql = _QUERY_PREDICATES.get(predicate)
        if predicate_sql is None:
            raise ValueError("query predicate must be intersects, within, or contains")
        fields = _normalise_query_fields(
            source_layer=source_layer,
            filters={} if filters is None else filters,
            projection=projection,
        )
        if not self._store_path.is_file():
            raise ValueError("Local Evidence Store has no coverage state")
        connection = self._open_initialised(read_only=True)
        transaction_started = False
        try:
            connection.execute("BEGIN TRANSACTION")
            transaction_started = True
            if state_fingerprint is None:
                coverage = self._current_coverage(connection)
                if coverage is None:
                    raise ValueError("Local Evidence Store has no current coverage state")
                resolved_state_fingerprint = coverage.fingerprint
            else:
                coverage = self._coverage_by_fingerprint(connection, state_fingerprint)
                resolved_state_fingerprint = state_fingerprint
            self._verify_coverage(connection, coverage)
            self._verify_retained_source_bytes(coverage)
            required_keys, consulted = _query_attestations_for_selector(
                coverage, source_layer, selector_geometry
            )
            _validate_query_contracts(consulted, fields)
            availability_counts = {
                availability: sum(
                    item.partition_content.availability == availability for item in consulted
                )
                for availability in ("available", "no-data", "explicit-unknown")
            }
            attestation_fingerprints = tuple(item.fingerprint for item in consulted)
            if not attestation_fingerprints:
                result = EvidenceQueryResult(
                    rows=(),
                    manifest=_query_manifest(
                        state_fingerprint=resolved_state_fingerprint,
                        source_layer=source_layer,
                        selector_fingerprint=selector_fingerprint,
                        predicate=predicate,
                        filters=fields.filters,
                        projection=fields.projection,
                        required_partition_keys=required_keys,
                        consulted_attestations=(),
                        availability_counts=availability_counts,
                        rows=(),
                    ),
                )
                connection.execute("COMMIT")
                transaction_started = False
                return result
            sql, parameters = _query_sql(
                table=table,
                attestation_fingerprints=attestation_fingerprints,
                selector_wkb=to_wkb(selector_geometry),
                selector_envelope_wkb=to_wkb(box(*selector_geometry.bounds)),
                predicate_sql=predicate_sql,
                filters=fields.filters,
            )
            raw_rows = connection.execute(sql, parameters).fetchall()
            deduplicated: dict[tuple[str, str], EvidenceQueryRow] = {}
            full_attributes = _LAYER_ATTRIBUTES[source_layer]
            for raw in raw_rows:
                (
                    attestation_fingerprint,
                    source_export_fingerprint,
                    feature_content_fingerprint,
                    logical_key,
                    geometry_fingerprint,
                    crs,
                    geometry_wkb,
                    *attribute_values,
                ) = raw
                all_attributes = dict(zip(full_attributes, attribute_values, strict=True))
                result_attributes = {
                    field: all_attributes[field] for field in fields.projection
                }
                row = EvidenceQueryRow(
                    source_export_fingerprint=str(source_export_fingerprint),
                    logical_key=str(logical_key),
                    feature_content_fingerprint=str(feature_content_fingerprint),
                    geometry_fingerprint=str(geometry_fingerprint),
                    geometry=from_wkb(bytes(geometry_wkb)),
                    crs=str(crs),
                    attributes=result_attributes,
                    attestation_fingerprints=(
                        str(attestation_fingerprint),
                    ),
                )
                key = (row.source_export_fingerprint, row.logical_key)
                existing = deduplicated.get(key)
                if existing is None:
                    deduplicated[key] = row
                elif _query_row_content(existing) != _query_row_content(row):
                    raise _schema_error("Local Evidence Store duplicate query rows conflict")
                else:
                    deduplicated[key] = EvidenceQueryRow(
                        source_export_fingerprint=existing.source_export_fingerprint,
                        logical_key=existing.logical_key,
                        feature_content_fingerprint=existing.feature_content_fingerprint,
                        geometry_fingerprint=existing.geometry_fingerprint,
                        geometry=existing.geometry,
                        crs=existing.crs,
                        attributes=existing.attributes,
                        attestation_fingerprints=(
                            *existing.attestation_fingerprints,
                            *row.attestation_fingerprints,
                        ),
                    )
            rows = tuple(deduplicated.values())
            result = EvidenceQueryResult(
                rows=rows,
                manifest=_query_manifest(
                    state_fingerprint=resolved_state_fingerprint,
                    source_layer=source_layer,
                    selector_fingerprint=selector_fingerprint,
                    predicate=predicate,
                    filters=fields.filters,
                    projection=fields.projection,
                    required_partition_keys=required_keys,
                    consulted_attestations=attestation_fingerprints,
                    availability_counts=availability_counts,
                    rows=rows,
                ),
            )
            connection.execute("COMMIT")
            transaction_started = False
            return result
        except EvidenceStoreSchemaError:
            if transaction_started:
                connection.execute("ROLLBACK")
            raise
        except LookupError as error:
            if transaction_started:
                connection.execute("ROLLBACK")
            raise ValueError(
                f"Local Evidence Store coverage state {state_fingerprint} is not found"
            ) from error
        except Exception:
            if transaction_started:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def status(self, *, verify: bool = False) -> EvidenceStoreStatus:
        """Return the current immutable coverage and optionally revalidate it."""

        if not self._store_path.is_file():
            return EvidenceStoreStatus(state="uninitialised", current_coverage=None)
        connection = self._open_initialised(read_only=True)
        try:
            try:
                pointer = connection.execute(
                    "SELECT coverage_fingerprint FROM current_coverage_state WHERE singleton = true"
                ).fetchone()
                if pointer is None:
                    return EvidenceStoreStatus(state="ready", current_coverage=None)
                coverage = self._coverage_by_fingerprint(connection, str(pointer[0]))
                if verify:
                    self._verify_coverage(connection, coverage)
                    self._verify_retained_source_bytes(coverage)
                return EvidenceStoreStatus(state="ready", current_coverage=coverage)
            except EvidenceStoreSchemaError:
                raise
            except Exception as error:
                raise _schema_error(
                    "Local Evidence Store current coverage registry is invalid"
                ) from error
        finally:
            connection.close()

    def _current_coverage(self, connection: Any) -> EvidenceCoverage | None:
        pointer = connection.execute(
            "SELECT coverage_fingerprint FROM current_coverage_state WHERE singleton = true"
        ).fetchone()
        if pointer is None:
            return None
        return self._coverage_by_fingerprint(connection, str(pointer[0]))

    @staticmethod
    def _observations_by_source_export(
        attestations: list[EvidencePartitionAttestation],
    ) -> dict[tuple[str, str], str]:
        observations: dict[tuple[str, str], str] = {}
        for attestation in attestations:
            LocalEvidenceStore._add_observations(observations, attestation)
        return observations

    @staticmethod
    def _add_observations(
        observations: dict[tuple[str, str], str],
        attestation: EvidencePartitionAttestation,
    ) -> None:
        source_export_fingerprint = attestation.source_export.fingerprint
        for feature, feature_fingerprint in zip(
            attestation.partition_content.features,
            attestation.partition_content.feature_content_fingerprints,
            strict=True,
        ):
            key = (source_export_fingerprint, str(feature["logical_key"]))
            existing = observations.setdefault(key, feature_fingerprint)
            if existing != feature_fingerprint:
                raise ValueError(
                    "one source-export RoadLink id has conflicting feature content"
                )

    @staticmethod
    def _create_schema(connection: Any) -> None:
        connection.execute(
            """
            CREATE TABLE local_evidence_store_metadata (
                singleton BOOLEAN PRIMARY KEY CHECK (singleton),
                schema_contract VARCHAR NOT NULL,
                runtime_lock_fingerprint VARCHAR NOT NULL
            );
            CREATE TABLE spatial_runtime_registry (
                fingerprint VARCHAR PRIMARY KEY,
                canonical_payload_json VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_export_registry (
                fingerprint VARCHAR PRIMARY KEY,
                canonical_payload_json VARCHAR NOT NULL,
                provenance_json VARCHAR NOT NULL,
                provenance_fingerprint VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ingestion_contract_registry (
                fingerprint VARCHAR PRIMARY KEY,
                canonical_payload_json VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS partition_key_registry (
                fingerprint VARCHAR PRIMARY KEY,
                canonical_payload_json VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS partition_content_registry (
                fingerprint VARCHAR PRIMARY KEY,
                partition_key_fingerprint VARCHAR NOT NULL,
                ingestion_contract_fingerprint VARCHAR NOT NULL,
                availability VARCHAR NOT NULL,
                canonical_payload_json VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS partition_feature_registry (
                partition_content_fingerprint VARCHAR NOT NULL,
                feature_content_fingerprint VARCHAR NOT NULL,
                canonical_feature_json VARCHAR NOT NULL,
                position BIGINT NOT NULL,
                PRIMARY KEY (partition_content_fingerprint, feature_content_fingerprint)
            );
            CREATE TABLE IF NOT EXISTS partition_attestation_registry (
                fingerprint VARCHAR PRIMARY KEY,
                partition_content_fingerprint VARCHAR NOT NULL,
                source_export_fingerprint VARCHAR NOT NULL,
                canonical_payload_json VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS coverage_state_registry (
                fingerprint VARCHAR PRIMARY KEY,
                coverage_state VARCHAR NOT NULL,
                canonical_payload_json VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS coverage_state_attestation (
                coverage_fingerprint VARCHAR NOT NULL,
                attestation_fingerprint VARCHAR NOT NULL,
                position BIGINT NOT NULL,
                PRIMARY KEY (coverage_fingerprint, attestation_fingerprint)
            );
            CREATE TABLE IF NOT EXISTS coverage_state_requested_partition (
                coverage_fingerprint VARCHAR NOT NULL,
                partition_key_fingerprint VARCHAR NOT NULL,
                position BIGINT NOT NULL,
                PRIMARY KEY (coverage_fingerprint, partition_key_fingerprint)
            );
            CREATE TABLE IF NOT EXISTS current_coverage_state (
                singleton BOOLEAN PRIMARY KEY CHECK (singleton),
                coverage_fingerprint VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS open_roads_roadlink (
                attestation_fingerprint VARCHAR NOT NULL,
                source_export_fingerprint VARCHAR NOT NULL,
                feature_content_fingerprint VARCHAR NOT NULL,
                logical_key VARCHAR NOT NULL,
                geometry_fingerprint VARCHAR NOT NULL,
                canonical_geometry_json VARCHAR NOT NULL,
                crs VARCHAR NOT NULL CHECK (crs = 'EPSG:27700'),
                geometry GEOMETRY NOT NULL,
                road_classification VARCHAR NOT NULL,
                road_function VARCHAR NOT NULL,
                road_classification_number VARCHAR,
                name_1 VARCHAR,
                PRIMARY KEY (attestation_fingerprint, feature_content_fingerprint)
            );
            CREATE TABLE IF NOT EXISTS openstreetmap_network_lines (
                attestation_fingerprint VARCHAR NOT NULL,
                source_export_fingerprint VARCHAR NOT NULL,
                feature_content_fingerprint VARCHAR NOT NULL,
                logical_key VARCHAR NOT NULL,
                geometry_fingerprint VARCHAR NOT NULL,
                canonical_geometry_json VARCHAR NOT NULL,
                crs VARCHAR NOT NULL CHECK (crs = 'EPSG:27700'),
                geometry GEOMETRY NOT NULL,
                name VARCHAR,
                highway VARCHAR,
                ref VARCHAR,
                oneway VARCHAR,
                surface VARCHAR,
                access VARCHAR,
                bicycle VARCHAR,
                foot VARCHAR,
                cycleway VARCHAR,
                service VARCHAR,
                tracktype VARCHAR,
                bridge VARCHAR,
                tunnel VARCHAR,
                junction VARCHAR,
                maxspeed VARCHAR,
                lanes VARCHAR,
                width VARCHAR,
                lit VARCHAR,
                ele VARCHAR,
                incline VARCHAR,
                PRIMARY KEY (attestation_fingerprint, feature_content_fingerprint)
            );
            """
        )
        for table in _LAYER_TABLES.values():
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_geometry_rtree "
                f"ON {table} USING RTREE (geometry)"
            )

    def _record_store_metadata(self, connection: Any) -> None:
        runtime_lock = self._runtime_lock()
        connection.execute(
            """
            INSERT INTO spatial_runtime_registry (fingerprint, canonical_payload_json)
            VALUES (?, ?)
            """,
            [
                runtime_lock.fingerprint,
                canonical_evidence_json(runtime_lock.canonical_payload()),
            ],
        )
        connection.execute(
            """
            INSERT INTO local_evidence_store_metadata
            (singleton, schema_contract, runtime_lock_fingerprint)
            VALUES (true, ?, ?)
            """,
            [_SCHEMA_CONTRACT, runtime_lock.fingerprint],
        )

    def _open_initialised(self, *, read_only: bool) -> Any:
        if not self._store_path.is_file():
            raise _schema_error("Local Evidence Store is uninitialised")
        try:
            connection = self._connect(read_only=read_only)
        except (EvidenceStoreSchemaError, SpatialRuntimeError):
            raise
        except Exception as error:
            raise _schema_error("Local Evidence Store database cannot be opened") from error
        try:
            self._validate_store_schema(connection)
        except Exception:
            connection.close()
            raise
        return connection

    def _validate_store_schema(self, connection: Any) -> None:
        try:
            column_rows = connection.execute(
                """
                SELECT table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'main'
                ORDER BY table_name, ordinal_position
                """
            ).fetchall()
            actual_columns: dict[str, list[tuple[str, str, str]]] = {}
            for table, column, data_type, nullable in column_rows:
                actual_columns.setdefault(str(table), []).append(
                    (str(column), str(data_type), str(nullable))
                )
            if {
                table: tuple(columns) for table, columns in actual_columns.items()
            } != _EXPECTED_COLUMNS:
                raise _schema_error("Local Evidence Store physical schema is incomplete or changed")

            runtime_lock = self._runtime_lock()
            metadata_rows = connection.execute(
                """
                SELECT schema_contract, runtime_lock_fingerprint
                FROM local_evidence_store_metadata
                WHERE singleton = true
                """
            ).fetchall()
            expected_metadata = [(_SCHEMA_CONTRACT, runtime_lock.fingerprint)]
            if metadata_rows != expected_metadata:
                raise _schema_error(
                    "Local Evidence Store schema contract or runtime lock is incompatible"
                )
            runtime_rows = connection.execute(
                """
                SELECT fingerprint, canonical_payload_json
                FROM spatial_runtime_registry
                ORDER BY fingerprint
                """
            ).fetchall()
            expected_runtime = [
                (
                    runtime_lock.fingerprint,
                    canonical_evidence_json(runtime_lock.canonical_payload()),
                )
            ]
            if runtime_rows != expected_runtime:
                raise _schema_error("Local Evidence Store runtime registry is invalid")
            self._verify_layer_rtrees(connection)
        except EvidenceStoreSchemaError:
            raise
        except Exception as error:
            raise _schema_error("Local Evidence Store physical schema is unreadable") from error

    @staticmethod
    def _verify_layer_rtrees(connection: Any) -> None:
        for table in _LAYER_TABLES.values():
            rows = connection.execute(
                """
                SELECT table_name, expressions, sql
                FROM duckdb_indexes()
                WHERE schema_name = 'main' AND index_name = ?
                """,
                [f"{table}_geometry_rtree"],
            ).fetchall()
            if len(rows) != 1:
                raise _schema_error(f"Local Evidence Store {table} RTree is missing")
            table_name, expressions, sql = rows[0]
            normalised_sql = " ".join(str(sql).upper().split())
            if (
                str(table_name) != table
                or str(expressions) != "[geometry]"
                or " USING RTREE (GEOMETRY)" not in normalised_sql
            ):
                raise _schema_error(f"Local Evidence Store {table} RTree binding is invalid")

    @staticmethod
    def _create_staging_tables(connection: Any) -> None:
        for table in _LAYER_TABLES.values():
            connection.execute(
                f"CREATE OR REPLACE TEMP TABLE stage_{table} AS SELECT * FROM {table} WHERE false"
            )

    def _normalise_partition(
        self, partition: _EvidencePartitionInput
    ) -> tuple[EvidencePartitionAttestation, tuple[dict[str, object], ...]]:
        if partition.partition_key.source_layer != partition.ingestion_contract.source_layer:
            raise ValueError("partition input key and ingestion contract source_layer differ")
        table = _LAYER_TABLES.get(partition.partition_key.source_layer)
        if table is None:
            raise ValueError(
                f"unsupported Local Evidence source layer: {partition.partition_key.source_layer}"
            )
        supported_attributes = _LAYER_ATTRIBUTES[partition.partition_key.source_layer]
        if not set(partition.ingestion_contract.selected_attributes) <= set(supported_attributes):
            raise ValueError(
                "ingestion contract selects attributes unsupported by its typed source table"
            )
        normalised: dict[str, dict[str, object]] = {}
        features: list[Mapping[str, object]] = []
        for feature in partition.features:
            attributes = dict(feature.attributes)
            if set(attributes) != set(partition.ingestion_contract.selected_attributes):
                raise ValueError(
                    "feature attributes must match the declared ingestion contract exactly"
                )
            for name, value in attributes.items():
                _, nullable = _LAYER_FIELD_TYPES[partition.partition_key.source_layer][name]
                if value is None:
                    if not nullable:
                        raise ValueError(f"feature attribute {name} must not be null")
                elif not isinstance(value, str):
                    raise ValueError(f"feature attribute {name} must be a string or null")
            canonical_evidence_json(attributes)
            geometry = feature.geometry
            canonical_geometry = canonical_evidence_geometry(geometry, "EPSG:27700")
            geometry_fingerprint = evidence_geometry_fingerprint(geometry, "EPSG:27700")
            feature_payload = {
                "logical_key": feature.logical_key,
                "geometry_fingerprint": geometry_fingerprint,
                "attributes": attributes,
            }
            feature_fingerprint = evidence_fingerprint(
                {"contract": "satn-evidence-feature-content/v1", "feature": feature_payload}
            )
            if feature_fingerprint in normalised:
                raise ValueError("partition input has duplicate canonical feature content")
            normalised[feature_fingerprint] = {
                "table": table,
                "feature_content_fingerprint": feature_fingerprint,
                "logical_key": feature.logical_key,
                "geometry_fingerprint": geometry_fingerprint,
                "canonical_geometry_json": canonical_evidence_json(canonical_geometry),
                "geometry_wkb": to_wkb(geometry),
                "attributes": attributes,
            }
            features.append(feature_payload)
        content = EvidencePartitionContent(
            partition_key=partition.partition_key,
            ingestion_contract=partition.ingestion_contract,
            features=tuple(features),
            availability=partition.availability,
        )
        attestation = EvidencePartitionAttestation(content, partition.source_export)
        ordered_rows = tuple(
            normalised[fingerprint] for fingerprint in content.feature_content_fingerprints
        )
        return attestation, ordered_rows

    @staticmethod
    def _validated_source_path(
        source_export: SourceExport,
        ingestion_contract: IngestionContract,
    ) -> Path:
        if ingestion_contract.source_layer == _OPEN_ROADS_SOURCE_LAYER:
            return _validate_open_roads_export(source_export, ingestion_contract)
        if ingestion_contract.source_layer == _OSM_NETWORK_SOURCE_LAYER:
            return _validate_osm_network_export(source_export, ingestion_contract)
        raise ValueError(
            f"unsupported Local Evidence source layer: {ingestion_contract.source_layer}"
        )

    def _read_missing_partitions(
        self,
        source_path: Path,
        source_export: SourceExport,
        ingestion_contract: IngestionContract,
        missing_keys: tuple[EvidencePartitionKey, ...],
    ) -> tuple[_EvidencePartitionInput, ...]:
        if ingestion_contract.source_layer == _OPEN_ROADS_SOURCE_LAYER:
            return tuple(
                self._read_open_roads_partition(
                    source_path,
                    source_export,
                    ingestion_contract,
                    partition_key,
                )
                for partition_key in missing_keys
            )
        if ingestion_contract.source_layer == _OSM_NETWORK_SOURCE_LAYER:
            return self._read_osm_network_partitions(
                source_path,
                source_export,
                ingestion_contract,
                missing_keys,
            )
        raise ValueError(
            f"unsupported Local Evidence source layer: {ingestion_contract.source_layer}"
        )

    @staticmethod
    def _read_open_roads_partition(
        source_path: Path,
        source_export: SourceExport,
        ingestion_contract: IngestionContract,
        partition_key: EvidencePartitionKey,
    ) -> _EvidencePartitionInput:
        source_partition = _read_open_roads_adapter_partition(
            source_path, source_export, ingestion_contract, partition_key
        )
        return _EvidencePartitionInput(
            source_export=source_export,
            ingestion_contract=ingestion_contract,
            partition_key=source_partition.partition_key,
            availability="available" if source_partition.features else "no-data",
            features=tuple(
                _EvidenceFeature(
                    logical_key=feature.logical_key,
                    geometry=feature.geometry,
                    attributes=feature.attributes,
                )
                for feature in source_partition.features
            ),
        )

    @staticmethod
    def _read_osm_network_partitions(
        source_path: Path,
        source_export: SourceExport,
        ingestion_contract: IngestionContract,
        partition_keys: tuple[EvidencePartitionKey, ...],
    ) -> tuple[_EvidencePartitionInput, ...]:
        source_partitions = _read_osm_network_adapter_partitions(
            source_path,
            source_export,
            ingestion_contract,
            partition_keys,
        )
        by_key = {
            partition.partition_key.fingerprint: partition for partition in source_partitions
        }
        expected_keys = {key.fingerprint for key in partition_keys}
        if len(by_key) != len(source_partitions) or set(by_key) != expected_keys:
            raise ValueError(
                "OpenStreetMap adapter did not return exactly the requested partitions"
            )
        return tuple(
            _EvidencePartitionInput(
                source_export=source_export,
                ingestion_contract=ingestion_contract,
                partition_key=by_key[partition_key.fingerprint].partition_key,
                availability=(
                    "available"
                    if by_key[partition_key.fingerprint].features
                    else "no-data"
                ),
                features=tuple(
                    _EvidenceFeature(
                        logical_key=feature.logical_key,
                        geometry=feature.geometry,
                        attributes=feature.attributes,
                    )
                    for feature in by_key[partition_key.fingerprint].features
                ),
            )
            for partition_key in partition_keys
        )

    @staticmethod
    def _verify_retained_source_bytes(coverage: EvidenceCoverage) -> None:
        checked: set[str] = set()
        for attestation in coverage.attestations:
            source_export = attestation.source_export
            if source_export.fingerprint in checked:
                continue
            checked.add(source_export.fingerprint)
            retained_path = source_export.provenance.get("retained_path")
            if not isinstance(retained_path, str) or not retained_path:
                raise _schema_error("Source Export retained_path provenance is missing")
            path = Path(retained_path)
            if not path.is_file() or _sha256_file(path) != source_export.raw_bytes_sha256:
                raise _schema_error("retained Source Export bytes do not match their attestation")

    def _stage_partition(
        self,
        connection: Any,
        attestation: EvidencePartitionAttestation,
        rows: tuple[dict[str, object], ...],
    ) -> None:
        content = attestation.partition_content
        self._insert_source_export(connection, attestation.source_export)
        self._insert_immutable(
            connection,
            "ingestion_contract_registry",
            content.ingestion_contract.fingerprint,
            canonical_evidence_json(content.ingestion_contract.canonical_payload()),
        )
        self._insert_immutable(
            connection,
            "partition_key_registry",
            content.partition_key.fingerprint,
            canonical_evidence_json(content.partition_key.canonical_payload()),
        )
        self._insert_partition_content(connection, content)
        self._insert_attestation(connection, attestation)
        for position, (feature, fingerprint) in enumerate(
            zip(content.features, content.feature_content_fingerprints, strict=True)
        ):
            self._insert_partition_feature(
                connection, content.fingerprint, fingerprint, feature, position
            )
        for row in rows:
            self._insert_staged_typed_row(connection, attestation, row)

    @staticmethod
    def _insert_immutable(connection: Any, table: str, fingerprint: str, payload: str) -> None:
        existing = connection.execute(
            f"SELECT canonical_payload_json FROM {table} WHERE fingerprint = ?", [fingerprint]
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload:
                raise ValueError(f"Local Evidence Store fingerprint collision in {table}")
            return
        connection.execute(
            f"INSERT INTO {table} (fingerprint, canonical_payload_json) VALUES (?, ?)",
            [fingerprint, payload],
        )

    @staticmethod
    def _insert_source_export(connection: Any, source_export: SourceExport) -> None:
        payload = canonical_evidence_json(source_export.canonical_payload())
        provenance = canonical_evidence_json(source_export.provenance)
        provenance_fingerprint = _provenance_fingerprint(source_export.provenance)
        existing = connection.execute(
            """
            SELECT canonical_payload_json, provenance_json, provenance_fingerprint
            FROM source_export_registry
            WHERE fingerprint = ?
            """,
            [source_export.fingerprint],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload:
                raise ValueError("Local Evidence Store Source Export registry collision")
            if (str(existing[1]), str(existing[2])) != (
                provenance,
                provenance_fingerprint,
            ):
                connection.execute(
                    """
                    UPDATE source_export_registry
                    SET provenance_json = ?, provenance_fingerprint = ?
                    WHERE fingerprint = ?
                    """,
                    [provenance, provenance_fingerprint, source_export.fingerprint],
                )
            return
        connection.execute(
            """
            INSERT INTO source_export_registry
            (fingerprint, canonical_payload_json, provenance_json, provenance_fingerprint)
            VALUES (?, ?, ?, ?)
            """,
            [source_export.fingerprint, payload, provenance, provenance_fingerprint],
        )

    @staticmethod
    def _insert_partition_content(connection: Any, content: EvidencePartitionContent) -> None:
        payload = canonical_evidence_json(content.canonical_payload())
        existing = connection.execute(
            "SELECT canonical_payload_json FROM partition_content_registry WHERE fingerprint = ?",
            [content.fingerprint],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload:
                raise ValueError("Local Evidence Store partition content fingerprint collision")
            return
        connection.execute(
            """
            INSERT INTO partition_content_registry
            (fingerprint, partition_key_fingerprint, ingestion_contract_fingerprint, availability,
             canonical_payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                content.fingerprint,
                content.partition_key.fingerprint,
                content.ingestion_contract.fingerprint,
                content.availability,
                payload,
            ],
        )

    @staticmethod
    def _insert_attestation(connection: Any, attestation: EvidencePartitionAttestation) -> None:
        payload = canonical_evidence_json(attestation.canonical_payload())
        existing = connection.execute(
            "SELECT canonical_payload_json FROM partition_attestation_registry "
            "WHERE fingerprint = ?",
            [attestation.fingerprint],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload:
                raise ValueError("Local Evidence Store partition attestation fingerprint collision")
            return
        connection.execute(
            """
            INSERT INTO partition_attestation_registry
            (fingerprint, partition_content_fingerprint, source_export_fingerprint,
             canonical_payload_json)
            VALUES (?, ?, ?, ?)
            """,
            [
                attestation.fingerprint,
                attestation.partition_content.fingerprint,
                attestation.source_export.fingerprint,
                payload,
            ],
        )

    @staticmethod
    def _insert_partition_feature(
        connection: Any,
        content_fingerprint: str,
        feature_fingerprint: str,
        feature: Mapping[str, object],
        position: int,
    ) -> None:
        payload = canonical_evidence_json(feature)
        existing = connection.execute(
            """
            SELECT canonical_feature_json FROM partition_feature_registry
            WHERE partition_content_fingerprint = ? AND feature_content_fingerprint = ?
            """,
            [content_fingerprint, feature_fingerprint],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload:
                raise ValueError("Local Evidence Store feature content fingerprint collision")
            return
        connection.execute(
            """
            INSERT INTO partition_feature_registry
            (partition_content_fingerprint, feature_content_fingerprint, canonical_feature_json,
             position)
            VALUES (?, ?, ?, ?)
            """,
            [content_fingerprint, feature_fingerprint, payload, position],
        )

    @staticmethod
    def _insert_staged_typed_row(
        connection: Any,
        attestation: EvidencePartitionAttestation,
        row: Mapping[str, object],
    ) -> None:
        table = str(row["table"])
        attributes = row["attributes"]
        assert isinstance(attributes, Mapping)
        columns = _LAYER_ATTRIBUTES[attestation.partition_content.partition_key.source_layer]
        values = [attributes.get(column) for column in columns]
        column_names = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        connection.execute(
            f"""
            INSERT INTO stage_{table}
            (attestation_fingerprint, source_export_fingerprint, feature_content_fingerprint,
             logical_key, geometry_fingerprint, canonical_geometry_json, crs, geometry,
             {column_names})
            VALUES (?, ?, ?, ?, ?, ?, ?, ST_GeomFromWKB(?), {placeholders})
            """,
            [
                attestation.fingerprint,
                attestation.source_export.fingerprint,
                row["feature_content_fingerprint"],
                row["logical_key"],
                row["geometry_fingerprint"],
                row["canonical_geometry_json"],
                "EPSG:27700",
                row["geometry_wkb"],
                *values,
            ],
        )

    def _commit_staged_refresh(self, connection: Any, coverage: EvidenceCoverage) -> None:
        for table in _LAYER_TABLES.values():
            connection.execute(
                f"""
                INSERT INTO {table}
                SELECT staged.* FROM stage_{table} AS staged
                WHERE NOT EXISTS (
                    SELECT 1 FROM {table} AS existing
                    WHERE existing.attestation_fingerprint = staged.attestation_fingerprint
                    AND existing.feature_content_fingerprint = staged.feature_content_fingerprint
                )
                """
            )
        self._insert_coverage_state(connection, coverage)
        connection.execute("DELETE FROM current_coverage_state")
        connection.execute(
            "INSERT INTO current_coverage_state (singleton, coverage_fingerprint) VALUES (true, ?)",
            [coverage.fingerprint],
        )

    def _insert_coverage_state(self, connection: Any, coverage: EvidenceCoverage) -> None:
        for key in coverage.requested_partition_keys:
            self._insert_immutable(
                connection,
                "partition_key_registry",
                key.fingerprint,
                canonical_evidence_json(key.canonical_payload()),
            )
        payload = canonical_evidence_json(coverage.canonical_payload())
        existing = connection.execute(
            "SELECT canonical_payload_json FROM coverage_state_registry WHERE fingerprint = ?",
            [coverage.fingerprint],
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO coverage_state_registry
                (fingerprint, coverage_state, canonical_payload_json)
                VALUES (?, ?, ?)
                """,
                [coverage.fingerprint, coverage.state, payload],
            )
            for position, attestation in enumerate(coverage.attestations):
                connection.execute(
                    """
                    INSERT INTO coverage_state_attestation
                    (coverage_fingerprint, attestation_fingerprint, position) VALUES (?, ?, ?)
                    """,
                    [coverage.fingerprint, attestation.fingerprint, position],
                )
            for position, key in enumerate(coverage.requested_partition_keys):
                connection.execute(
                    """
                    INSERT INTO coverage_state_requested_partition
                    (coverage_fingerprint, partition_key_fingerprint, position) VALUES (?, ?, ?)
                    """,
                    [coverage.fingerprint, key.fingerprint, position],
                )
        elif str(existing[0]) != payload:
            raise ValueError("Local Evidence Store coverage state fingerprint collision")

    def _coverage_by_fingerprint(self, connection: Any, fingerprint: str) -> EvidenceCoverage:
        state = connection.execute(
            "SELECT coverage_state FROM coverage_state_registry WHERE fingerprint = ?",
            [fingerprint],
        ).fetchone()
        if state is None:
            raise LookupError("Local Evidence Store requested coverage state is missing")
        attestation_rows = connection.execute(
            """
            SELECT attestation_fingerprint FROM coverage_state_attestation
            WHERE coverage_fingerprint = ? ORDER BY position
            """,
            [fingerprint],
        ).fetchall()
        key_rows = connection.execute(
            """
            SELECT partition_key_fingerprint FROM coverage_state_requested_partition
            WHERE coverage_fingerprint = ? ORDER BY position
            """,
            [fingerprint],
        ).fetchall()
        coverage = EvidenceCoverage(
            tuple(
                self._attestation_by_fingerprint(connection, str(row[0]))
                for row in attestation_rows
            ),
            requested_partition_keys=tuple(
                self._partition_key_by_fingerprint(connection, str(row[0])) for row in key_rows
            ),
            state=str(state[0]),
            fingerprint=fingerprint,
        )
        return coverage

    def _attestation_by_fingerprint(
        self, connection: Any, fingerprint: str
    ) -> EvidencePartitionAttestation:
        row = connection.execute(
            """
            SELECT partition_content_fingerprint, source_export_fingerprint
            FROM partition_attestation_registry WHERE fingerprint = ?
            """,
            [fingerprint],
        ).fetchone()
        if row is None:
            raise ValueError("Local Evidence Store partition attestation is missing")
        attestation = EvidencePartitionAttestation(
            self._partition_content_by_fingerprint(connection, str(row[0])),
            self._source_export_by_fingerprint(connection, str(row[1])),
            fingerprint=fingerprint,
        )
        return attestation

    def _partition_content_by_fingerprint(
        self, connection: Any, fingerprint: str
    ) -> EvidencePartitionContent:
        row = connection.execute(
            """
            SELECT partition_key_fingerprint, ingestion_contract_fingerprint, availability
            FROM partition_content_registry WHERE fingerprint = ?
            """,
            [fingerprint],
        ).fetchone()
        if row is None:
            raise ValueError("Local Evidence Store partition content is missing")
        feature_rows = connection.execute(
            """
            SELECT canonical_feature_json FROM partition_feature_registry
            WHERE partition_content_fingerprint = ? ORDER BY position
            """,
            [fingerprint],
        ).fetchall()
        return EvidencePartitionContent(
            self._partition_key_by_fingerprint(connection, str(row[0])),
            self._ingestion_contract_by_fingerprint(connection, str(row[1])),
            tuple(json.loads(str(feature[0])) for feature in feature_rows),
            availability=str(row[2]),
            fingerprint=fingerprint,
        )

    @staticmethod
    def _source_export_by_fingerprint(connection: Any, fingerprint: str) -> SourceExport:
        row = connection.execute(
            """
            SELECT canonical_payload_json, provenance_json, provenance_fingerprint
            FROM source_export_registry
            WHERE fingerprint = ?
            """,
            [fingerprint],
        ).fetchone()
        if row is None:
            raise ValueError("Local Evidence Store Source Export registry record is missing")
        payload = _json_object(str(row[0]), "Source Export canonical payload")
        provenance = _json_object(str(row[1]), "Source Export provenance")
        if str(row[2]) != _provenance_fingerprint(provenance):
            raise ValueError("Local Evidence Store Source Export provenance fingerprint is invalid")
        return SourceExport(
            **_without_contract(payload),
            provenance=provenance,
            fingerprint=fingerprint,
        )

    @staticmethod
    def _ingestion_contract_by_fingerprint(connection: Any, fingerprint: str) -> IngestionContract:
        payload = _registry_payload(connection, "ingestion_contract_registry", fingerprint)
        return IngestionContract(**_without_contract(payload), fingerprint=fingerprint)

    @staticmethod
    def _partition_key_by_fingerprint(connection: Any, fingerprint: str) -> EvidencePartitionKey:
        payload = _registry_payload(connection, "partition_key_registry", fingerprint)
        return EvidencePartitionKey(**_without_contract(payload), fingerprint=fingerprint)

    def _verify_coverage(self, connection: Any, coverage: EvidenceCoverage) -> None:
        self._verify_registry_closure(connection, coverage)
        for attestation in coverage.attestations:
            content = attestation.partition_content
            if content.ingestion_contract.canonical_payload() != _expected_contract_payload(
                content.partition_key.source_layer,
                attestation.source_export.declared_crs,
            ):
                raise _schema_error("stored Ingestion Contract is not supported by this adapter")
            table = _LAYER_TABLES[content.partition_key.source_layer]
            typed_columns = _LAYER_ATTRIBUTES[content.partition_key.source_layer]
            selected_columns = ", ".join(typed_columns)
            rows = connection.execute(
                f"""
                SELECT source_export_fingerprint, feature_content_fingerprint, logical_key,
                       geometry_fingerprint, canonical_geometry_json, crs,
                       ST_AsWKB(geometry), {selected_columns}
                FROM {table}
                WHERE attestation_fingerprint = ?
                ORDER BY feature_content_fingerprint
                """,
                [attestation.fingerprint],
            ).fetchall()
            if len(rows) != len(content.features):
                raise _schema_error(
                    "Local Evidence Store typed partition rows do not match their attestation"
                )
            for row, feature, feature_fingerprint in zip(
                rows,
                content.features,
                content.feature_content_fingerprints,
                strict=True,
            ):
                attributes = feature.get("attributes")
                if not isinstance(attributes, Mapping):
                    raise _schema_error("Local Evidence Store feature attributes are invalid")
                geometry = from_wkb(bytes(row[6]))
                canonical_geometry_json = canonical_evidence_json(
                    canonical_evidence_geometry(geometry, "EPSG:27700")
                )
                geometry_fingerprint = evidence_geometry_fingerprint(geometry, "EPSG:27700")
                expected = (
                    attestation.source_export.fingerprint,
                    feature_fingerprint,
                    feature.get("logical_key"),
                    geometry_fingerprint,
                    canonical_geometry_json,
                    "EPSG:27700",
                    *(attributes.get(name) for name in typed_columns),
                )
                actual = (*row[:6], *row[7:])
                if (
                    actual != expected
                    or feature.get("geometry_fingerprint") != geometry_fingerprint
                ):
                    raise _schema_error(
                        "Local Evidence Store typed values or physical geometry are invalid"
                    )

    def _verify_registry_closure(self, connection: Any, coverage: EvidenceCoverage) -> None:
        self._assert_payload(
            connection,
            "coverage_state_registry",
            coverage.fingerprint,
            coverage.canonical_payload(),
        )
        expected_attestations = [
            (item.fingerprint, position) for position, item in enumerate(coverage.attestations)
        ]
        actual_attestations = connection.execute(
            """
            SELECT attestation_fingerprint, position
            FROM coverage_state_attestation
            WHERE coverage_fingerprint = ?
            ORDER BY position
            """,
            [coverage.fingerprint],
        ).fetchall()
        if actual_attestations != expected_attestations:
            raise _schema_error("Local Evidence Store coverage attestation closure is invalid")
        expected_keys = [
            (item.fingerprint, position)
            for position, item in enumerate(coverage.requested_partition_keys)
        ]
        actual_keys = connection.execute(
            """
            SELECT partition_key_fingerprint, position
            FROM coverage_state_requested_partition
            WHERE coverage_fingerprint = ?
            ORDER BY position
            """,
            [coverage.fingerprint],
        ).fetchall()
        if actual_keys != expected_keys:
            raise _schema_error("Local Evidence Store requested partition closure is invalid")
        for key in coverage.requested_partition_keys:
            self._assert_payload(
                connection,
                "partition_key_registry",
                key.fingerprint,
                key.canonical_payload(),
            )
        for attestation in coverage.attestations:
            content = attestation.partition_content
            source_export = attestation.source_export
            self._assert_source_export(connection, source_export)
            self._assert_payload(
                connection,
                "ingestion_contract_registry",
                content.ingestion_contract.fingerprint,
                content.ingestion_contract.canonical_payload(),
            )
            self._assert_payload(
                connection,
                "partition_key_registry",
                content.partition_key.fingerprint,
                content.partition_key.canonical_payload(),
            )
            self._assert_payload(
                connection,
                "partition_content_registry",
                content.fingerprint,
                content.canonical_payload(),
            )
            self._assert_payload(
                connection,
                "partition_attestation_registry",
                attestation.fingerprint,
                attestation.canonical_payload(),
            )
            feature_rows = connection.execute(
                """
                SELECT feature_content_fingerprint, canonical_feature_json, position
                FROM partition_feature_registry
                WHERE partition_content_fingerprint = ?
                ORDER BY position
                """,
                [content.fingerprint],
            ).fetchall()
            expected_features = [
                (
                    fingerprint,
                    canonical_evidence_json(feature),
                    position,
                )
                for position, (fingerprint, feature) in enumerate(
                    zip(
                        content.feature_content_fingerprints,
                        content.features,
                        strict=True,
                    )
                )
            ]
            if feature_rows != expected_features:
                raise _schema_error("Local Evidence Store feature registry closure is invalid")

    @staticmethod
    def _assert_payload(
        connection: Any,
        table: str,
        fingerprint: str,
        payload: Mapping[str, object],
    ) -> None:
        row = connection.execute(
            f"SELECT canonical_payload_json FROM {table} WHERE fingerprint = ?",
            [fingerprint],
        ).fetchone()
        if row is None or str(row[0]) != canonical_evidence_json(payload):
            raise _schema_error(f"Local Evidence Store canonical payload is invalid in {table}")

    @staticmethod
    def _assert_source_export(connection: Any, source_export: SourceExport) -> None:
        row = connection.execute(
            """
            SELECT canonical_payload_json, provenance_json, provenance_fingerprint
            FROM source_export_registry
            WHERE fingerprint = ?
            """,
            [source_export.fingerprint],
        ).fetchone()
        expected = (
            canonical_evidence_json(source_export.canonical_payload()),
            canonical_evidence_json(source_export.provenance),
            _provenance_fingerprint(source_export.provenance),
        )
        if row is None or (str(row[0]), str(row[1]), str(row[2])) != expected:
            raise _schema_error("Local Evidence Store Source Export registry is invalid")

    def _verify_runtime(self) -> None:
        runtime_lock, extension_path, duckdb = self._validated_runtime()
        connection = duckdb.connect(":memory:")
        try:
            self._load_spatial(connection, extension_path, runtime_lock)
        finally:
            connection.close()

    def _connect(self, *, read_only: bool) -> Any:
        runtime_lock, extension_path, duckdb = self._validated_runtime()
        connection = duckdb.connect(str(self._store_path), read_only=read_only)
        try:
            self._load_spatial(connection, extension_path, runtime_lock)
        except Exception:
            connection.close()
            raise
        return connection

    def _validated_runtime(self) -> tuple[SpatialRuntimeLock, Path, Any]:
        runtime_lock = self._runtime_lock()
        extension_path = self._extension_path(runtime_lock)
        duckdb = _duckdb_module()
        if duckdb.__version__ != runtime_lock.duckdb_version:
            raise SpatialRuntimeError(
                f"DuckDB {runtime_lock.duckdb_version} is required; found {duckdb.__version__}"
            )
        return runtime_lock, extension_path, duckdb

    def _runtime_lock(self) -> SpatialRuntimeLock:
        runtime_lock = SpatialRuntimeLock.from_json(self._runtime_lock_path)
        actual_platform = _runtime_platform()
        if runtime_lock.platform != actual_platform:
            raise SpatialRuntimeError(
                f"Spatial runtime lock targets {runtime_lock.platform}, not {actual_platform}"
            )
        return runtime_lock

    def _extension_path(self, runtime_lock: SpatialRuntimeLock) -> Path:
        extension_path = self._extension_cache / runtime_lock.extension_relative_path
        if not extension_path.is_file():
            raise SpatialRuntimeError(
                "provision the pinned Spatial extension before opening a Local Evidence Store"
            )
        if _sha256_file(extension_path) != runtime_lock.extension_sha256:
            raise SpatialRuntimeError(
                "provision the pinned Spatial extension again: cached checksum does not match"
            )
        return extension_path

    @staticmethod
    def _load_spatial(
        connection: Any,
        extension_path: Path,
        runtime_lock: SpatialRuntimeLock,
    ) -> None:
        connection.execute("SET autoinstall_known_extensions = false")
        connection.execute("SET autoload_known_extensions = false")
        connection.execute(f"LOAD '{_sql_literal(str(extension_path))}'")
        row = connection.execute(
            "SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'spatial'"
        ).fetchone()
        if row is None or row[0] != runtime_lock.spatial_version:
            loaded = None if row is None else row[0]
            raise SpatialRuntimeError(
                f"Spatial {runtime_lock.spatial_version} is required; loaded {loaded}"
            )


def _duckdb_module() -> Any:
    try:
        import duckdb
    except ImportError as error:
        raise SpatialRuntimeError("DuckDB 1.4.4 is not installed") from error
    return duckdb


def _runtime_platform() -> str:
    system = platform_module.system().lower()
    if system == "darwin":
        system = "osx"
    return f"{system}_{platform_module.machine().lower()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _open_roads_adapter_fingerprint() -> str:
    """Compatibility helper for building the closed Open Roads contract in callers."""

    return _adapter_fingerprint()


def _expected_contract_payload(source_layer: str, declared_crs: str) -> dict[str, object]:
    if source_layer == _OPEN_ROADS_SOURCE_LAYER:
        return _open_roads_contract_payload(declared_crs)
    if source_layer == _OSM_NETWORK_SOURCE_LAYER:
        return _osm_network_contract_payload()
    raise _schema_error(f"unsupported Local Evidence source layer: {source_layer}")


_SCHEMA_CONTRACT = "satn-local-evidence-store-physical-schema/v3"
_REBUILD_GUIDANCE = (
    "rebuild the Local Evidence Store from governed source inputs at a new store path"
)

_EXPECTED_COLUMNS = {
    "coverage_state_attestation": (
        ("coverage_fingerprint", "VARCHAR", "NO"),
        ("attestation_fingerprint", "VARCHAR", "NO"),
        ("position", "BIGINT", "NO"),
    ),
    "coverage_state_registry": (
        ("fingerprint", "VARCHAR", "NO"),
        ("coverage_state", "VARCHAR", "NO"),
        ("canonical_payload_json", "VARCHAR", "NO"),
    ),
    "coverage_state_requested_partition": (
        ("coverage_fingerprint", "VARCHAR", "NO"),
        ("partition_key_fingerprint", "VARCHAR", "NO"),
        ("position", "BIGINT", "NO"),
    ),
    "current_coverage_state": (
        ("singleton", "BOOLEAN", "NO"),
        ("coverage_fingerprint", "VARCHAR", "NO"),
    ),
    "ingestion_contract_registry": (
        ("fingerprint", "VARCHAR", "NO"),
        ("canonical_payload_json", "VARCHAR", "NO"),
    ),
    "local_evidence_store_metadata": (
        ("singleton", "BOOLEAN", "NO"),
        ("schema_contract", "VARCHAR", "NO"),
        ("runtime_lock_fingerprint", "VARCHAR", "NO"),
    ),
    "open_roads_roadlink": (
        ("attestation_fingerprint", "VARCHAR", "NO"),
        ("source_export_fingerprint", "VARCHAR", "NO"),
        ("feature_content_fingerprint", "VARCHAR", "NO"),
        ("logical_key", "VARCHAR", "NO"),
        ("geometry_fingerprint", "VARCHAR", "NO"),
        ("canonical_geometry_json", "VARCHAR", "NO"),
        ("crs", "VARCHAR", "NO"),
        ("geometry", "GEOMETRY", "NO"),
        ("road_classification", "VARCHAR", "NO"),
        ("road_function", "VARCHAR", "NO"),
        ("road_classification_number", "VARCHAR", "YES"),
        ("name_1", "VARCHAR", "YES"),
    ),
    "openstreetmap_network_lines": (
        ("attestation_fingerprint", "VARCHAR", "NO"),
        ("source_export_fingerprint", "VARCHAR", "NO"),
        ("feature_content_fingerprint", "VARCHAR", "NO"),
        ("logical_key", "VARCHAR", "NO"),
        ("geometry_fingerprint", "VARCHAR", "NO"),
        ("canonical_geometry_json", "VARCHAR", "NO"),
        ("crs", "VARCHAR", "NO"),
        ("geometry", "GEOMETRY", "NO"),
        ("name", "VARCHAR", "YES"),
        ("highway", "VARCHAR", "YES"),
        ("ref", "VARCHAR", "YES"),
        ("oneway", "VARCHAR", "YES"),
        ("surface", "VARCHAR", "YES"),
        ("access", "VARCHAR", "YES"),
        ("bicycle", "VARCHAR", "YES"),
        ("foot", "VARCHAR", "YES"),
        ("cycleway", "VARCHAR", "YES"),
        ("service", "VARCHAR", "YES"),
        ("tracktype", "VARCHAR", "YES"),
        ("bridge", "VARCHAR", "YES"),
        ("tunnel", "VARCHAR", "YES"),
        ("junction", "VARCHAR", "YES"),
        ("maxspeed", "VARCHAR", "YES"),
        ("lanes", "VARCHAR", "YES"),
        ("width", "VARCHAR", "YES"),
        ("lit", "VARCHAR", "YES"),
        ("ele", "VARCHAR", "YES"),
        ("incline", "VARCHAR", "YES"),
    ),
    "partition_attestation_registry": (
        ("fingerprint", "VARCHAR", "NO"),
        ("partition_content_fingerprint", "VARCHAR", "NO"),
        ("source_export_fingerprint", "VARCHAR", "NO"),
        ("canonical_payload_json", "VARCHAR", "NO"),
    ),
    "partition_content_registry": (
        ("fingerprint", "VARCHAR", "NO"),
        ("partition_key_fingerprint", "VARCHAR", "NO"),
        ("ingestion_contract_fingerprint", "VARCHAR", "NO"),
        ("availability", "VARCHAR", "NO"),
        ("canonical_payload_json", "VARCHAR", "NO"),
    ),
    "partition_feature_registry": (
        ("partition_content_fingerprint", "VARCHAR", "NO"),
        ("feature_content_fingerprint", "VARCHAR", "NO"),
        ("canonical_feature_json", "VARCHAR", "NO"),
        ("position", "BIGINT", "NO"),
    ),
    "partition_key_registry": (
        ("fingerprint", "VARCHAR", "NO"),
        ("canonical_payload_json", "VARCHAR", "NO"),
    ),
    "source_export_registry": (
        ("fingerprint", "VARCHAR", "NO"),
        ("canonical_payload_json", "VARCHAR", "NO"),
        ("provenance_json", "VARCHAR", "NO"),
        ("provenance_fingerprint", "VARCHAR", "NO"),
    ),
    "spatial_runtime_registry": (
        ("fingerprint", "VARCHAR", "NO"),
        ("canonical_payload_json", "VARCHAR", "NO"),
    ),
}

_LAYER_TABLES = {
    _OPEN_ROADS_SOURCE_LAYER: "open_roads_roadlink",
    _OSM_NETWORK_SOURCE_LAYER: "openstreetmap_network_lines",
}

_LAYER_ATTRIBUTES = {
    _OPEN_ROADS_SOURCE_LAYER: _OPEN_ROADS_ATTRIBUTES,
    _OSM_NETWORK_SOURCE_LAYER: _OSM_NETWORK_ATTRIBUTES,
}

_LAYER_FIELD_TYPES = {
    _OPEN_ROADS_SOURCE_LAYER: {
        "road_classification": ("string", False),
        "road_function": ("string", False),
        "road_classification_number": ("string|null", True),
        "name_1": ("string|null", True),
    },
    _OSM_NETWORK_SOURCE_LAYER: {
        name: ("string|null", True) for name in _OSM_NETWORK_ATTRIBUTES
    },
}

_QUERY_PREDICATES = {
    "intersects": "ST_Intersects",
    "within": "ST_Within",
    "contains": "ST_Contains",
}


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    frozen = _freeze_query_value(dict(value))
    assert isinstance(frozen, Mapping)
    return frozen


def _freeze_query_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_query_value(item) for key, item in sorted(value.items())}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_query_value(item) for item in value)
    return value


def _validate_query_state_fingerprint(state_fingerprint: str) -> None:
    if (
        not isinstance(state_fingerprint, str)
        or re.fullmatch(r"[0-9a-f]{64}", state_fingerprint) is None
    ):
        raise ValueError("coverage state fingerprint must be a full lowercase SHA-256")


def _query_selector_input(
    *,
    selector: BaseGeometry | None,
    bbox: tuple[object, object, object, object] | None,
) -> BaseGeometry:
    if (selector is None) == (bbox is None):
        raise ValueError("evidence query requires exactly one selector geometry or BNG bbox")
    if selector is not None:
        return selector
    if not isinstance(bbox, tuple) or len(bbox) != 4:
        raise ValueError("evidence query BNG bbox must contain exactly four numbers")
    coordinates: list[float] = []
    for value in bbox:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("evidence query BNG bbox values must be numbers")
        coordinate = float(value)
        if not math.isfinite(coordinate):
            raise ValueError("evidence query BNG bbox values must be finite")
        coordinates.append(coordinate)
    min_x, min_y, max_x, max_y = coordinates
    if min_x >= max_x or min_y >= max_y:
        raise ValueError("evidence query BNG bbox must have increasing bounds")
    return box(min_x, min_y, max_x, max_y)


def _canonical_query_selector(
    selector: BaseGeometry, selector_crs: str
) -> tuple[BaseGeometry, str]:
    if not isinstance(selector, BaseGeometry):
        raise ValueError("evidence query selector must be a Shapely geometry")
    canonical = canonical_evidence_geometry(selector, selector_crs)
    geometry_payload = canonical["geometry"]
    assert isinstance(geometry_payload, Mapping)
    geometry_type = str(geometry_payload["type"])

    def metres(coordinates: object) -> object:
        if (
            isinstance(coordinates, (list, tuple))
            and len(coordinates) == 2
            and all(type(value) is int for value in coordinates)
        ):
            return [value / 1000 for value in coordinates]
        if isinstance(coordinates, (list, tuple)):
            return [metres(value) for value in coordinates]
        raise ValueError("canonical query selector coordinates are invalid")

    if geometry_type in {"Point", "LineString", "MultiLineString"}:
        geojson_geometry = {
            "type": geometry_type,
            "coordinates": metres(geometry_payload["coordinates"]),
        }
    elif geometry_type == "Polygon":
        geojson_geometry = {
            "type": geometry_type,
            "coordinates": [
                metres(geometry_payload["exterior"]),
                *(metres(ring) for ring in geometry_payload["holes"]),
            ],
        }
    elif geometry_type == "MultiPolygon":
        geojson_geometry = {
            "type": geometry_type,
            "coordinates": [
                [
                    metres(polygon["exterior"]),
                    *(metres(ring) for ring in polygon["holes"]),
                ]
                for polygon in geometry_payload["polygons"]
            ],
        }
    else:  # pragma: no cover - canonical_evidence_geometry owns this closed set.
        raise ValueError(f"unsupported evidence query selector geometry: {geometry_type}")
    canonical_selector = from_geojson(
        json.dumps(geojson_geometry, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )
    return canonical_selector, evidence_fingerprint(canonical)


def _normalise_query_fields(
    *,
    source_layer: str,
    filters: Mapping[str, object],
    projection: tuple[str, ...],
) -> _NormalisedQueryFields:
    field_types = _LAYER_FIELD_TYPES.get(source_layer)
    if field_types is None:
        raise ValueError(f"unsupported Local Evidence query source layer: {source_layer}")
    if not isinstance(filters, Mapping):
        raise ValueError("evidence query filters must be a mapping")
    if not isinstance(projection, tuple):
        raise ValueError("evidence query projection must be a tuple")
    if any(not isinstance(field, str) for field in filters):
        raise ValueError("evidence query filter field names must be strings")
    if any(not isinstance(field, str) for field in projection):
        raise ValueError("evidence query projection field names must be strings")
    if len(set(projection)) != len(projection):
        raise ValueError("evidence query projection cannot contain duplicates")
    unknown = (set(filters) | set(projection)) - set(field_types)
    if unknown:
        raise ValueError(
            "evidence query uses unsupported fields: " + ", ".join(sorted(unknown))
        )
    normalised_filters: list[tuple[str, object]] = []
    for field, value in sorted(filters.items()):
        _, nullable = field_types[field]
        if value is None:
            if not nullable:
                raise ValueError(f"evidence query field {field} is not nullable")
        elif not isinstance(value, str):
            raise ValueError(f"evidence query field {field} requires a string or null")
        normalised_filters.append((field, value))
    return _NormalisedQueryFields(
        filters=tuple(normalised_filters),
        projection=tuple(sorted(projection)),
    )


def _query_attestations_for_selector(
    coverage: EvidenceCoverage,
    source_layer: str,
    selector: BaseGeometry,
) -> tuple[
    tuple[EvidencePartitionKey, ...],
    tuple[EvidencePartitionAttestation, ...],
]:
    required_cells = _bng_cells_intersecting(selector)
    required_keys = tuple(
        sorted(
            (
                EvidencePartitionKey(source_layer, "bng-10km/v1", cell)
                for cell in required_cells
            ),
            key=lambda key: key.fingerprint,
        )
    )
    by_key = {
        attestation.partition_content.partition_key.fingerprint: attestation
        for attestation in coverage.attestations
    }
    missing = tuple(key.cell for key in required_keys if key.fingerprint not in by_key)
    if missing:
        raise ValueError(
            "pinned Evidence Coverage does not cover selector BNG cells: "
            + ", ".join(sorted(missing))
        )
    return required_keys, tuple(
        sorted(
            (by_key[key.fingerprint] for key in required_keys),
            key=lambda item: item.fingerprint,
        )
    )


def _bng_cells_intersecting(selector: BaseGeometry) -> tuple[str, ...]:
    min_x, min_y, max_x, max_y = selector.bounds
    bounds = (min_x, min_y, max_x, max_y)
    if not all(math.isfinite(value) for value in bounds):
        raise ValueError("evidence query selector bounds must be finite")
    grid_mm = 10_000_000
    bounds_mm = tuple(round(value * 1000) for value in bounds)
    min_easting = bounds_mm[0] // grid_mm - (bounds_mm[0] % grid_mm == 0)
    min_northing = bounds_mm[1] // grid_mm - (bounds_mm[1] % grid_mm == 0)
    max_easting = bounds_mm[2] // grid_mm
    max_northing = bounds_mm[3] // grid_mm
    cells: list[str] = []
    for easting_index in range(max(0, min_easting), min(69, max_easting) + 1):
        for northing_index in range(max(0, min_northing), min(129, max_northing) + 1):
            cell_bounds = (
                easting_index * 10_000,
                northing_index * 10_000,
                (easting_index + 1) * 10_000,
                (northing_index + 1) * 10_000,
            )
            if box(*cell_bounds).intersects(selector):
                cells.append(_bng_10km_cell(easting_index, northing_index))
    if not cells:
        raise ValueError("evidence query selector is outside the supported BNG extent")
    return tuple(sorted(cells))


def _bng_10km_cell(easting_index: int, northing_index: int) -> str:
    easting_100km, east_digit = divmod(easting_index, 10)
    northing_100km, north_digit = divmod(northing_index, 10)
    first_index = (
        (19 - northing_100km)
        - (19 - northing_100km) % 5
        + (easting_100km + 10) // 5
    )
    second_index = ((19 - northing_100km) * 5) % 25 + easting_100km % 5
    if first_index > 7:
        first_index += 1
    if second_index > 7:
        second_index += 1
    return (
        f"{chr(first_index + ord('A'))}{chr(second_index + ord('A'))}"
        f"{east_digit}{north_digit}"
    )


def _validate_query_contracts(
    attestations: tuple[EvidencePartitionAttestation, ...],
    fields: _NormalisedQueryFields,
) -> None:
    used_fields = {field for field, _ in fields.filters} | set(fields.projection)
    for attestation in attestations:
        contract = attestation.partition_content.ingestion_contract
        if not used_fields <= set(contract.selected_attributes):
            raise _schema_error(
                "query fields are not selected by every consulted Ingestion Contract"
            )


def _query_sql(
    *,
    table: str,
    attestation_fingerprints: tuple[str, ...],
    selector_wkb: bytes,
    selector_envelope_wkb: bytes,
    predicate_sql: str,
    filters: tuple[tuple[str, object], ...],
) -> tuple[str, list[object]]:
    if table not in _LAYER_TABLES.values():
        raise ValueError("unsupported Local Evidence query table")
    if predicate_sql not in _QUERY_PREDICATES.values():
        raise ValueError("unsupported Local Evidence query predicate")
    source_layer = next(layer for layer, candidate in _LAYER_TABLES.items() if candidate == table)
    attributes = _LAYER_ATTRIBUTES[source_layer]
    placeholders = ", ".join("?" for _ in attestation_fingerprints)
    clauses = [f"{predicate_sql}(feature.geometry, ST_GeomFromWKB(?))"]
    parameters: list[object] = [
        *attestation_fingerprints,
        selector_envelope_wkb,
        selector_wkb,
    ]
    for field, value in filters:
        if field not in attributes:
            raise ValueError(f"unsupported Local Evidence query field: {field}")
        if value is None:
            clauses.append(f"feature.{field} IS NULL")
        else:
            clauses.append(f"feature.{field} = ?")
            parameters.append(value)
    selected_attributes = ", ".join(f"feature.{field}" for field in attributes)
    sql = f"""
        SELECT feature.attestation_fingerprint,
               feature.source_export_fingerprint,
               feature.feature_content_fingerprint,
               feature.logical_key,
               feature.geometry_fingerprint,
               feature.crs,
               ST_AsWKB(feature.geometry),
               {selected_attributes}
        FROM (
            SELECT *
            FROM {table} AS indexed_feature
            WHERE indexed_feature.attestation_fingerprint IN ({placeholders})
              AND ST_Intersects(indexed_feature.geometry, ST_GeomFromWKB(?))
        ) AS feature
        WHERE {" AND ".join(clauses)}
        ORDER BY feature.source_export_fingerprint,
                 feature.logical_key,
                 feature.attestation_fingerprint
    """
    return sql, parameters


def _query_row_content(row: EvidenceQueryRow) -> tuple[object, ...]:
    return (
        row.feature_content_fingerprint,
        row.geometry_fingerprint,
        to_wkb(row.geometry),
        row.crs,
        canonical_evidence_json(row.attributes),
    )


def _query_manifest(
    *,
    state_fingerprint: str,
    source_layer: str,
    selector_fingerprint: str,
    predicate: QueryPredicate,
    filters: tuple[tuple[str, object], ...],
    projection: tuple[str, ...],
    required_partition_keys: tuple[EvidencePartitionKey, ...],
    consulted_attestations: tuple[str, ...],
    availability_counts: Mapping[str, int],
    rows: tuple[EvidenceQueryRow, ...],
) -> dict[str, object]:
    ordered_rows = tuple(
        sorted(rows, key=lambda row: (row.source_export_fingerprint, row.logical_key))
    )
    return {
        "contract": "satn-evidence-query-manifest/v1",
        "query_contract": "satn-local-evidence-exact-spatial-query/v1",
        "coverage_contract": "satn-evidence-coverage/v1",
        "coverage_state_fingerprint": state_fingerprint,
        "source_layer": source_layer,
        "selector_geometry_fingerprint": selector_fingerprint,
        "selector_crs": "EPSG:27700",
        "predicate": predicate,
        "predicate_operand_order": "feature_geometry predicate selector_geometry",
        "filters": {field: value for field, value in filters},
        "projection": list(projection),
        "required_partition_key_fingerprints": [
            key.fingerprint for key in required_partition_keys
        ],
        "required_bng_10km_cells": sorted(key.cell for key in required_partition_keys),
        "consulted_attestation_fingerprints": sorted(consulted_attestations),
        "availability_counts": {
            name: int(availability_counts.get(name, 0))
            for name in ("available", "no-data", "explicit-unknown")
        },
        "row_count": len(ordered_rows),
        "row_fingerprints": [row.fingerprint for row in ordered_rows],
    }


def _registry_payload(connection: Any, table: str, fingerprint: str) -> dict[str, object]:
    row = connection.execute(
        f"SELECT canonical_payload_json FROM {table} WHERE fingerprint = ?", [fingerprint]
    ).fetchone()
    if row is None:
        raise ValueError(f"Local Evidence Store registry record is missing from {table}")
    return _json_object(str(row[0]), f"registry record in {table}")


def _without_contract(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "contract"}


def _json_object(payload_json: str, name: str) -> dict[str, object]:
    payload = json.loads(payload_json)
    if not isinstance(payload, dict):
        raise ValueError(f"Local Evidence Store {name} is invalid")
    return payload


def _provenance_fingerprint(provenance: Mapping[str, object]) -> str:
    return evidence_fingerprint(
        {
            "contract": "satn-source-export-provenance/v1",
            "provenance": provenance,
        }
    )


def _schema_error(detail: str) -> EvidenceStoreSchemaError:
    return EvidenceStoreSchemaError(f"{detail}; {_REBUILD_GUIDANCE}")
