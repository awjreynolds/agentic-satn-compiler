"""Additive DuckDB sidecar for immutable Local Evidence materialisations."""

from __future__ import annotations

import hashlib
import json
import platform as platform_module
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pyproj import Transformer
from shapely import from_wkb, to_wkb
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

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


class SpatialRuntimeError(RuntimeError):
    """The explicitly provisioned local DuckDB Spatial runtime is unusable."""


class EvidenceStoreSchemaError(RuntimeError):
    """An existing Local Evidence Store cannot be trusted or used."""


Availability = Literal["available", "no-data", "explicit-unknown"]
StoreState = Literal["uninitialised", "ready"]


@dataclass(frozen=True)
class EvidenceFeature:
    """One source-specific feature supplied to the Local Evidence Store seam."""

    logical_key: str
    geometry: BaseGeometry
    attributes: Mapping[str, object]


@dataclass(frozen=True)
class EvidencePartitionInput:
    """A streamed, one-source-layer/one-BNG-cell refresh input."""

    source_export: SourceExport
    ingestion_contract: IngestionContract
    partition_key: EvidencePartitionKey
    availability: Availability
    features: tuple[EvidenceFeature, ...]


@dataclass(frozen=True)
class RefreshResult:
    """Immutable result of one committed Evidence Refresh."""

    coverage: EvidenceCoverage


@dataclass(frozen=True)
class EvidenceStoreStatus:
    """The current immutable Evidence Coverage, if a refresh has completed."""

    state: StoreState
    current_coverage: EvidenceCoverage | None


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
        requested_partition_keys: tuple[EvidencePartitionKey, ...],
        partitions: Iterable[EvidencePartitionInput],
    ) -> RefreshResult:
        """Transactionally materialise streamed source-layer partition attestations."""

        connection = self._open_initialised(read_only=False)
        transaction_started = False
        try:
            connection.execute("BEGIN TRANSACTION")
            transaction_started = True
            self._create_staging_tables(connection)
            attestations: list[EvidencePartitionAttestation] = []
            for partition in partitions:
                attestation, rows = self._normalise_partition(partition)
                self._stage_partition(connection, attestation, rows)
                attestations.append(attestation)
            coverage = EvidenceCoverage(
                tuple(attestations),
                requested_partition_keys=requested_partition_keys,
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
                return EvidenceStoreStatus(state="ready", current_coverage=coverage)
            except EvidenceStoreSchemaError:
                raise
            except Exception as error:
                raise _schema_error(
                    "Local Evidence Store current coverage registry is invalid"
                ) from error
        finally:
            connection.close()

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
                road_name VARCHAR,
                road_number VARCHAR,
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
            self._verify_open_roads_rtree(connection)
        except EvidenceStoreSchemaError:
            raise
        except Exception as error:
            raise _schema_error("Local Evidence Store physical schema is unreadable") from error

    @staticmethod
    def _verify_open_roads_rtree(connection: Any) -> None:
        rows = connection.execute(
            """
            SELECT table_name, expressions, sql
            FROM duckdb_indexes()
            WHERE schema_name = 'main' AND index_name = 'open_roads_roadlink_geometry_rtree'
            """
        ).fetchall()
        if len(rows) != 1:
            raise _schema_error("Local Evidence Store Open Roads RTree is missing")
        table_name, expressions, sql = rows[0]
        normalised_sql = " ".join(str(sql).upper().split())
        if (
            str(table_name) != "open_roads_roadlink"
            or str(expressions) != "[geometry]"
            or " USING RTREE (GEOMETRY)" not in normalised_sql
        ):
            raise _schema_error("Local Evidence Store Open Roads RTree binding is invalid")

    @staticmethod
    def _create_staging_tables(connection: Any) -> None:
        for table in _LAYER_TABLES.values():
            connection.execute(
                f"CREATE OR REPLACE TEMP TABLE stage_{table} AS SELECT * FROM {table} WHERE false"
            )

    def _normalise_partition(
        self, partition: EvidencePartitionInput
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
            canonical_evidence_json(attributes)
            geometry = _geometry_in_bng(feature.geometry, partition.source_export.declared_crs)
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
            if (str(existing[0]), str(existing[1]), str(existing[2])) != (
                payload,
                provenance,
                provenance_fingerprint,
            ):
                raise ValueError("Local Evidence Store Source Export registry collision")
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
            raise ValueError("Local Evidence Store current coverage state is missing")
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
            table = _LAYER_TABLES[content.partition_key.source_layer]
            rows = connection.execute(
                f"""
                SELECT source_export_fingerprint, feature_content_fingerprint, logical_key,
                       geometry_fingerprint, canonical_geometry_json, crs,
                       ST_AsWKB(geometry), road_name, road_number
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
                    attributes.get("road_name"),
                    attributes.get("road_number"),
                )
                actual = (*row[:6], row[7], row[8])
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


_SCHEMA_CONTRACT = "satn-local-evidence-store-physical-schema/v1"
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
        ("road_name", "VARCHAR", "YES"),
        ("road_number", "VARCHAR", "YES"),
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
    "os-open-roads/RoadLink": "open_roads_roadlink",
}

_LAYER_ATTRIBUTES = {
    "os-open-roads/RoadLink": ("road_name", "road_number"),
}


def _geometry_in_bng(geometry: BaseGeometry, source_crs: str) -> BaseGeometry:
    if source_crs == "EPSG:27700":
        return geometry
    transformer = Transformer.from_crs(source_crs, "EPSG:27700", always_xy=True)
    return transform(transformer.transform, geometry)


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
