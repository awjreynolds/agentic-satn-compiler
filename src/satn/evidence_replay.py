"""Fail-closed replay gate for exact Local Evidence source queries.

This gate proves only that selected Local Evidence Store queries agree with the
repository's closed byte adapters (or, for EA elevation, verified retained
receipt/object sampling).  It does not compile or publish a network artifact.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import re
import resource
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import duckdb
from shapely.geometry.base import BaseGeometry

from satn import open_roads_adapter, osm_network_adapter
from satn.ea_raster_evidence import (
    ElevationSamplingReadSet,
    ElevationSamplingResult,
    RasterIoEvent,
    resolve_elevation_sampling_read_set,
)
from satn.ea_raster_evidence import sample_elevation as sample_elevation_from_receipts
from satn.evidence_contracts import (
    EvidenceCoverage,
    EvidencePartitionAttestation,
    EvidencePartitionContent,
    EvidencePartitionKey,
    IngestionContract,
    ScenarioConfiguration,
    SourceExport,
    canonical_evidence_json,
    evidence_fingerprint,
    evidence_geometry_fingerprint,
)
from satn.local_evidence_store import (
    EvidenceQueryResult,
    EvidenceStoreStatus,
    LocalEvidenceStore,
    QueryPredicate,
    _bng_cells_intersecting,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VECTOR_LAYERS = frozenset({"os-open-roads/RoadLink", "openstreetmap/lines"})
_GENERATION_CONTRACT = "satn-source-query-replay-generation/v2"
_COMMIT_CONTRACT = "satn-source-query-replay-commit/v2"
_RUN_CONTRACT = "satn-source-query-replay-run/v1"
_DEPENDENCY_CONTRACT = "satn-source-query-replay-dependency/v1"
REPLAY_RUNTIME_DISTRIBUTIONS = (
    "duckdb",
    "geopandas",
    "numpy",
    "pandas",
    "Pillow",
    "pyogrio",
    "pyproj",
    "shapely",
)


def _sha256(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase full SHA-256")
    return value


def _required_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def _frozen_mapping(value: Mapping[str, object], name: str) -> Mapping[str, object]:
    try:
        canonical = json.loads(canonical_evidence_json(dict(value)))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be canonical evidence JSON") from error
    return MappingProxyType(canonical)


@dataclass(frozen=True)
class ProbeObservation:
    """One canonical source-query observation."""

    result_fingerprint: str
    availability_counts: Mapping[str, object]
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-source-query-replay-observation/v1")

    def __post_init__(self) -> None:
        _sha256(self.result_fingerprint, "probe result fingerprint")
        counts = dict(self.availability_counts)
        if set(counts) != {"available", "no-data", "explicit-unknown"}:
            raise ValueError("probe availability counts require the three governed states")
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise ValueError("probe availability counts must be non-negative integers")
        counts = dict(sorted(counts.items()))
        expected = evidence_fingerprint(
            {
                "contract": self.contract,
                "result_fingerprint": self.result_fingerprint,
                "availability_counts": counts,
            }
        )
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("probe observation fingerprint is stale")
        object.__setattr__(self, "availability_counts", MappingProxyType(counts))
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "result_fingerprint": self.result_fingerprint,
            "availability_counts": dict(self.availability_counts),
        }


@dataclass(frozen=True)
class VectorEvidenceBinding:
    """One immutable source export/contract bound to explicit BNG partitions."""

    source_export: SourceExport
    ingestion_contract: IngestionContract
    requested_partition_keys: tuple[EvidencePartitionKey, ...]
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-source-query-vector-binding/v1")

    def __post_init__(self) -> None:
        if not isinstance(self.source_export, SourceExport):
            raise ValueError("vector binding requires a SourceExport")
        if not isinstance(self.ingestion_contract, IngestionContract):
            raise ValueError("vector binding requires an IngestionContract")
        layer = self.ingestion_contract.source_layer
        if layer not in _VECTOR_LAYERS:
            raise ValueError("source-query replay supports governed Open Roads or OSM")
        if f"{self.source_export.source_family}/{self.source_export.layer}" != layer:
            raise ValueError("SourceExport and IngestionContract source layers differ")
        keys = tuple(sorted(self.requested_partition_keys, key=lambda item: item.fingerprint))
        if not keys or len({item.fingerprint for item in keys}) != len(keys):
            raise ValueError("vector binding requires unique requested partition keys")
        if any(
            item.source_layer != layer
            or item.partition_scheme != self.ingestion_contract.partition_scheme
            for item in keys
        ):
            raise ValueError("vector binding partition keys do not match its contract")
        object.__setattr__(self, "requested_partition_keys", keys)
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("vector evidence binding fingerprint is stale")
        object.__setattr__(self, "fingerprint", expected)

    @property
    def source_layer(self) -> str:
        return self.ingestion_contract.source_layer

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "source_export_fingerprint": self.source_export.fingerprint,
            "source_export_raw_bytes_sha256": self.source_export.raw_bytes_sha256,
            "ingestion_contract_fingerprint": self.ingestion_contract.fingerprint,
            "partition_key_fingerprints": [
                item.fingerprint for item in self.requested_partition_keys
            ],
        }


ProbeKind = Literal["vector", "elevation"]


@dataclass(frozen=True)
class EvidenceReplayProbe:
    """One query selector; no caller-supplied oracle is accepted."""

    probe_id: str
    kind: ProbeKind
    source_layer: str
    selector: BaseGeometry
    predicate: QueryPredicate = "intersects"
    filters: Mapping[str, object] = field(default_factory=dict)
    projection: tuple[str, ...] = ()
    elevation_spacing_mm: int = 10_000
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-source-query-replay-probe/v1")

    def __post_init__(self) -> None:
        _required_text(self.probe_id, "probe_id")
        if self.kind not in {"vector", "elevation"}:
            raise ValueError("probe kind must be vector or elevation")
        _required_text(self.source_layer, "probe source_layer")
        if self.kind == "vector" and self.source_layer not in _VECTOR_LAYERS:
            raise ValueError("vector probes support governed Open Roads or OSM only")
        if self.kind == "elevation" and self.source_layer in _VECTOR_LAYERS:
            raise ValueError("elevation probes require an elevation source layer")
        if self.predicate not in {"intersects", "within", "contains"}:
            raise ValueError("unsupported replay probe predicate")
        if type(self.elevation_spacing_mm) is not int or self.elevation_spacing_mm <= 0:
            raise ValueError("elevation_spacing_mm must be a positive integer")
        filters = _frozen_mapping(self.filters, "probe filters")
        projection = tuple(sorted(self.projection))
        if len(set(projection)) != len(projection):
            raise ValueError("probe projection cannot contain duplicates")
        if any(not isinstance(item, str) or not item for item in projection):
            raise ValueError("probe projection fields must be nonempty text")
        if self.kind == "elevation" and (filters or projection):
            raise ValueError("elevation probes do not accept vector filters or projection")
        object.__setattr__(self, "filters", filters)
        object.__setattr__(self, "projection", projection)
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("source-query replay probe fingerprint is stale")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "probe_id": self.probe_id,
            "kind": self.kind,
            "source_layer": self.source_layer,
            "selector_geometry_fingerprint": evidence_geometry_fingerprint(
                self.selector, "EPSG:27700"
            ),
            "predicate": self.predicate,
            "filters": dict(self.filters),
            "projection": list(self.projection),
            "elevation_spacing_mm": self.elevation_spacing_mm,
        }


@dataclass(frozen=True)
class EvidenceReplayRequest:
    """All governed inputs and local paths for one source-query replay."""

    scenario_configuration: ScenarioConfiguration
    vector_bindings: tuple[VectorEvidenceBinding, ...]
    probes: tuple[EvidenceReplayProbe, ...]
    store_path: Path
    runtime_lock_path: Path
    extension_cache: Path
    cache_path: Path
    run_manifest_path: Path
    ea_cache_dir: Path | None = None
    elevation_state_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_configuration, ScenarioConfiguration):
            raise ValueError("replay request requires a ScenarioConfiguration")
        bindings = tuple(sorted(self.vector_bindings, key=lambda item: item.fingerprint))
        partition_owners: dict[tuple[str, str], str] = {}
        for binding in bindings:
            for key in binding.requested_partition_keys:
                identity = (binding.source_layer, key.fingerprint)
                if identity in partition_owners:
                    raise ValueError(
                        "one replay request cannot bind a partition to multiple exports"
                    )
                partition_owners[identity] = binding.fingerprint
        probes = tuple(sorted(self.probes, key=lambda item: item.probe_id))
        if not probes or len({item.probe_id for item in probes}) != len(probes):
            raise ValueError("replay request requires unique probes")
        bound_layers = {item.source_layer for item in bindings}
        if any(
            probe.kind == "vector" and probe.source_layer not in bound_layers for probe in probes
        ):
            raise ValueError("every vector probe requires governed source bindings")
        elevation_probes = any(item.kind == "elevation" for item in probes)
        if elevation_probes and (
            self.ea_cache_dir is None or self.elevation_state_fingerprint is None
        ):
            raise ValueError("elevation probes require an EA cache and pinned raster state")
        if self.elevation_state_fingerprint is not None:
            _sha256(
                self.elevation_state_fingerprint,
                "elevation state fingerprint",
            )
        if self.cache_path == self.run_manifest_path:
            raise ValueError("cache namespace and run commit paths must differ")
        object.__setattr__(self, "vector_bindings", bindings)
        object.__setattr__(self, "probes", probes)


@dataclass(frozen=True)
class EvidenceReplayRun:
    """One completed source-query replay, accepted only from a clean commit."""

    manifest: Mapping[str, object]

    @property
    def evidence_result_fingerprint(self) -> str:
        return str(self.manifest["results"]["evidence_result_fingerprint"])  # type: ignore[index]

    @property
    def accepted(self) -> bool:
        return bool(self.manifest["acceptance"]["accepted"])  # type: ignore[index]


@dataclass(frozen=True)
class _OracleResult:
    observation: ProbeObservation
    dependency: Mapping[str, object]
    adapter_partition_reads: int


@dataclass(frozen=True)
class _CacheEntry:
    dependency: Mapping[str, object]
    observation: ProbeObservation


@dataclass(frozen=True)
class _CommittedCache:
    entries: Mapping[str, _CacheEntry]
    generation_fingerprint: str | None
    commit_identity: Mapping[str, object] | None
    runtime_identity: Mapping[str, object] | None


@dataclass
class _RasterIoCounts:
    receipt_reads: int = 0
    object_reads: int = 0

    def observe(self, event: RasterIoEvent) -> None:
        if event == "receipt-read":
            self.receipt_reads += 1
        else:
            self.object_reads += 1


def run_source_query_replay(request: EvidenceReplayRequest) -> EvidenceReplayRun:
    """Compare exact store queries to immutable-source observations and commit once."""

    started = time.perf_counter_ns()
    git_identity = _git_identity()
    runtime_identity = _runtime_identity()
    committed = _load_committed_cache(request)
    reuse_allowed = (
        git_identity["available"]
        and not git_identity["dirty"]
        and committed.commit_identity is not None
        and dict(committed.commit_identity) == git_identity
        and committed.runtime_identity is not None
        and dict(committed.runtime_identity) == runtime_identity
    )
    prior_cache = committed.entries
    store = LocalEvidenceStore(
        store_path=request.store_path,
        runtime_lock_path=request.runtime_lock_path,
        extension_cache=request.extension_cache,
    )
    store.initialise()
    active_bindings = _bindings_required_by_probes(request.probes, request.vector_bindings)
    (
        refresh_calls,
        refreshed_partitions,
        vector_coverage,
        vector_validation_source_reads,
        vector_validation_source_bytes,
    ) = _prepare_vector_bindings(store, active_bindings)
    coverage_state = None if vector_coverage is None else vector_coverage.fingerprint
    counters = {
        "vector_refresh_calls": refresh_calls,
        "vector_refreshed_partitions": refreshed_partitions,
        "vector_validation_source_reads": vector_validation_source_reads,
        "vector_validation_source_bytes": vector_validation_source_bytes,
        "vector_store_queries": 0,
        "source_adapter_partition_reads": 0,
        "ea_oracle_samples": 0,
        "ea_replay_store_samples": 0,
        "ea_oracle_receipt_reads": 0,
        "ea_oracle_object_reads": 0,
        "ea_replay_receipt_reads": 0,
        "ea_replay_object_reads": 0,
        "ea_cache_validation_receipt_reads": 0,
        "ea_cache_validation_object_reads": 0,
        "derived_hits": 0,
        "derived_misses": 0,
    }
    pending_cache = dict(prior_cache)
    observations: dict[str, ProbeObservation] = {}
    probe_records: list[dict[str, object]] = []
    for probe in request.probes:
        if probe.kind == "vector":
            if vector_coverage is None:
                raise ValueError("vector replay probe has no governed coverage")
            expected_dependency = _vector_dependency_from_coverage(
                probe,
                request.vector_bindings,
                vector_coverage,
            )
        else:
            expected_dependency = _ea_dependency(
                probe,
                _resolve_ea_read_set(
                    request,
                    probe,
                    verify_files=False,
                ),
            )
        cache_key = evidence_fingerprint(expected_dependency)
        existing = prior_cache.get(cache_key) if reuse_allowed else None
        if existing is not None:
            if dict(existing.dependency) != dict(expected_dependency):
                raise ValueError("source-query replay dependency cache is corrupt")
            if probe.kind == "elevation":
                cache_validation_io = _RasterIoCounts()
                verified_dependency = _ea_dependency(
                    probe,
                    _resolve_ea_read_set(
                        request,
                        probe,
                        verify_files=True,
                        io_counts=cache_validation_io,
                    ),
                )
                counters["ea_cache_validation_receipt_reads"] += cache_validation_io.receipt_reads
                counters["ea_cache_validation_object_reads"] += cache_validation_io.object_reads
                if dict(verified_dependency) != dict(expected_dependency):
                    raise ValueError("EA replay cache dependency changed during byte verification")
            observation = existing.observation
            counters["derived_hits"] += 1
            oracle_mode = "validated-derived-observation"
            reused = True
        elif probe.kind == "vector":
            oracle = _direct_vector_oracle(probe, request.vector_bindings)
            counters["source_adapter_partition_reads"] += oracle.adapter_partition_reads
            if dict(oracle.dependency) != dict(expected_dependency):
                raise ValueError("governed byte-adapter dependency differs from store coverage")
            query_result = store.query(
                state_fingerprint=coverage_state,
                source_layer=probe.source_layer,
                selector=probe.selector,
                selector_crs="EPSG:27700",
                predicate=probe.predicate,
                filters=probe.filters,
                projection=probe.projection,
            )
            counters["vector_store_queries"] += 1
            _validate_store_read_set(query_result, oracle.dependency)
            replay_observation = _vector_observation(query_result)
            if replay_observation != oracle.observation:
                raise ValueError(
                    f"source-query replay probe {probe.probe_id!r} differs from "
                    "the governed byte-adapter observation"
                )
            observation = oracle.observation
            oracle_mode = "closed-byte-adapter"
            reused = False
        else:
            assert request.ea_cache_dir is not None
            oracle_io = _RasterIoCounts()
            oracle_sampled = _direct_ea_sample(
                request,
                probe,
                io_counts=oracle_io,
            )
            counters["ea_oracle_samples"] += 1
            counters["ea_oracle_receipt_reads"] += oracle_io.receipt_reads
            counters["ea_oracle_object_reads"] += oracle_io.object_reads
            oracle_observation = _elevation_observation(oracle_sampled)
            oracle = _ea_receipt_oracle(probe, oracle_sampled, oracle_observation)
            if dict(oracle.dependency) != dict(expected_dependency):
                raise ValueError(
                    "EA receipt/object oracle dependency differs from its ledger read set"
                )
            replay_io = _RasterIoCounts()
            replay_sampled = store.sample_elevation(
                cache_dir=request.ea_cache_dir,
                geometry=probe.selector,
                geometry_crs="EPSG:27700",
                spacing_mm=probe.elevation_spacing_mm,
                state_fingerprint=request.elevation_state_fingerprint,
                io_observer=replay_io.observe,
            )
            counters["ea_replay_store_samples"] += 1
            counters["ea_replay_receipt_reads"] += replay_io.receipt_reads
            counters["ea_replay_object_reads"] += replay_io.object_reads
            replay_observation = _elevation_observation(replay_sampled)
            replay_dependency = _ea_dependency(probe, replay_sampled)
            if dict(replay_dependency) != dict(expected_dependency):
                raise ValueError(
                    "EA Local Evidence sample dependency differs from its ledger read set"
                )
            if replay_observation != oracle_observation:
                raise ValueError(
                    f"source-query replay probe {probe.probe_id!r} differs from "
                    "the governed EA receipt/object observation"
                )
            observation = oracle_observation
            oracle_mode = "closed-ea-receipt-object-adapter"
            reused = False
        if not reused:
            counters["derived_misses"] += 1
            pending_cache[cache_key] = _CacheEntry(
                dependency=expected_dependency,
                observation=observation,
            )
        observations[probe.probe_id] = observation
        probe_records.append(
            {
                "probe_id": probe.probe_id,
                "probe_fingerprint": probe.fingerprint,
                "oracle_mode": oracle_mode,
                "oracle_observation_fingerprint": observation.fingerprint,
                "replay_observation_fingerprint": observation.fingerprint,
                "reused": reused,
                "dependency_fingerprint": cache_key,
                "dependency": dict(expected_dependency),
            }
        )

    inventory_fingerprint = evidence_fingerprint(
        {
            "contract": "satn-source-query-probe-inventory/v1",
            "probe_fingerprints": [item.fingerprint for item in request.probes],
        }
    )
    evidence_result_fingerprint = evidence_fingerprint(
        {
            "contract": "satn-source-query-replay-results/v1",
            "observations": {key: item.fingerprint for key, item in sorted(observations.items())},
        }
    )
    scenario_result_fingerprint = evidence_fingerprint(
        {
            "contract": "satn-source-query-scenario-result/v1",
            "scenario_configuration_fingerprint": (request.scenario_configuration.fingerprint),
            "probe_inventory_fingerprint": inventory_fingerprint,
            "evidence_result_fingerprint": evidence_result_fingerprint,
        }
    )
    acceptance_reasons = []
    if not git_identity["available"]:
        acceptance_reasons.append("git-commit-unavailable")
    if git_identity["dirty"]:
        acceptance_reasons.append("git-worktree-dirty")
    accepted = not acceptance_reasons
    generation = _cache_generation(pending_cache)
    generation_fingerprint = str(generation["fingerprint"])
    manifest: dict[str, object] = {
        "contract": _RUN_CONTRACT,
        "scope": "source-query-replay-gate",
        "claims_excluded": [
            "compiler-equivalence",
            "network-artifact-equivalence",
            "publication-equivalence",
            "scenario-cutover-performance",
        ],
        "commit": git_identity,
        "runtime": runtime_identity,
        "source_bindings": [
            item.canonical_payload() | {"fingerprint": item.fingerprint}
            for item in request.vector_bindings
        ],
        "vector_coverage_state_fingerprint": coverage_state,
        "elevation_state_fingerprint": request.elevation_state_fingerprint,
        "scenario_configuration_fingerprint": (request.scenario_configuration.fingerprint),
        "probe_inventory_fingerprint": inventory_fingerprint,
        "probes": probe_records,
        "work": counters,
        "cache": {
            "generation_fingerprint": generation_fingerprint,
            "reused_generation": (generation_fingerprint == committed.generation_fingerprint),
        },
        "measurements": {
            "elapsed_ms": (time.perf_counter_ns() - started) // 1_000_000,
            "peak_rss_bytes": _peak_rss_bytes(),
        },
        "results": {
            "evidence_result_fingerprint": evidence_result_fingerprint,
            "scenario_result_fingerprint": scenario_result_fingerprint,
        },
        "acceptance": {
            "accepted": accepted,
            "reasons": acceptance_reasons,
        },
        "exit": {
            "code": 0 if accepted else 2,
            "status": "passed" if accepted else "not-accepted",
        },
    }
    manifest["fingerprint"] = evidence_fingerprint(manifest)
    _commit_generation(request, generation, manifest)
    return EvidenceReplayRun(manifest=MappingProxyType(manifest))


def _bindings_required_by_probes(
    probes: tuple[EvidenceReplayProbe, ...],
    bindings: tuple[VectorEvidenceBinding, ...],
) -> tuple[VectorEvidenceBinding, ...]:
    required = {
        (probe.source_layer, key.fingerprint)
        for probe in probes
        if probe.kind == "vector"
        for key in (
            EvidencePartitionKey(
                probe.source_layer,
                "bng-10km/v1",
                cell,
            )
            for cell in _bng_cells_intersecting(probe.selector)
        )
    }
    selected: list[VectorEvidenceBinding] = []
    for binding in bindings:
        keys = tuple(
            key
            for key in binding.requested_partition_keys
            if (binding.source_layer, key.fingerprint) in required
        )
        if keys:
            selected.append(
                VectorEvidenceBinding(
                    source_export=binding.source_export,
                    ingestion_contract=binding.ingestion_contract,
                    requested_partition_keys=keys,
                )
            )
    return tuple(selected)


def _prepare_vector_bindings(
    store: LocalEvidenceStore,
    bindings: tuple[VectorEvidenceBinding, ...],
) -> tuple[int, int, EvidenceCoverage | None, int, int]:
    if not bindings:
        return 0, 0, None, 0, 0

    refresh_calls = 0
    refreshed_partitions = 0
    status = store.status(verify=False)
    for binding in bindings:
        present = _matching_partition_fingerprints(status, binding)
        missing = tuple(
            item for item in binding.requested_partition_keys if item.fingerprint not in present
        )
        if missing:
            refreshed = store.refresh(
                source_export=binding.source_export,
                ingestion_contract=binding.ingestion_contract,
                partition_keys=missing,
            )
            refresh_calls += 1
            refreshed_partitions += len(missing)
            status = EvidenceStoreStatus(
                state="ready",
                current_coverage=refreshed.coverage,
            )
    verified = store.status(verify=True)
    validation_source_reads, validation_source_bytes = _vector_validation_work(
        verified.current_coverage
    )
    if verified.current_coverage is None:
        raise ValueError("Local Evidence replay has no completed vector coverage")
    for binding in bindings:
        present = _matching_partition_fingerprints(verified, binding)
        expected = {item.fingerprint for item in binding.requested_partition_keys}
        if not expected <= present:
            raise ValueError("Local Evidence replay vector coverage is incomplete")
    return (
        refresh_calls,
        refreshed_partitions,
        verified.current_coverage,
        validation_source_reads,
        validation_source_bytes,
    )


def _vector_validation_work(coverage: EvidenceCoverage | None) -> tuple[int, int]:
    if coverage is None:
        return 0, 0
    retained_paths = {
        attestation.source_export.fingerprint: Path(
            str(attestation.source_export.provenance["retained_path"])
        )
        for attestation in coverage.attestations
    }
    return len(retained_paths), sum(path.stat().st_size for path in retained_paths.values())


def _matching_partition_fingerprints(
    status: EvidenceStoreStatus, binding: VectorEvidenceBinding
) -> set[str]:
    coverage = status.current_coverage
    if coverage is None:
        return set()
    requested = {item.fingerprint for item in binding.requested_partition_keys}
    result: set[str] = set()
    for attestation in coverage.attestations:
        content = attestation.partition_content
        key_fingerprint = content.partition_key.fingerprint
        if key_fingerprint not in requested:
            continue
        if (
            attestation.source_export.fingerprint != binding.source_export.fingerprint
            or content.ingestion_contract.fingerprint != binding.ingestion_contract.fingerprint
        ):
            raise ValueError("requested replay partition has different governed inputs")
        result.add(key_fingerprint)
    return result


def _vector_dependency_from_coverage(
    probe: EvidenceReplayProbe,
    bindings: tuple[VectorEvidenceBinding, ...],
    coverage: EvidenceCoverage,
) -> Mapping[str, object]:
    required_keys = tuple(
        EvidencePartitionKey(probe.source_layer, "bng-10km/v1", cell)
        for cell in _bng_cells_intersecting(probe.selector)
    )
    owner_by_key = {
        key.fingerprint: binding
        for binding in bindings
        if binding.source_layer == probe.source_layer
        for key in binding.requested_partition_keys
    }
    attestation_by_key = {
        item.partition_content.partition_key.fingerprint: item for item in coverage.attestations
    }
    consulted: list[EvidencePartitionAttestation] = []
    for key in required_keys:
        binding = owner_by_key.get(key.fingerprint)
        attestation = attestation_by_key.get(key.fingerprint)
        if binding is None or attestation is None:
            raise ValueError(f"governed replay coverage does not close selector cell {key.cell}")
        if (
            attestation.source_export.fingerprint != binding.source_export.fingerprint
            or attestation.partition_content.ingestion_contract.fingerprint
            != binding.ingestion_contract.fingerprint
        ):
            raise ValueError("requested replay partition has different governed inputs")
        consulted.append(attestation)
    ordered = tuple(sorted(consulted, key=lambda item: item.fingerprint))
    return MappingProxyType(
        {
            "contract": _DEPENDENCY_CONTRACT,
            "kind": "vector",
            "probe_fingerprint": probe.fingerprint,
            "required_partition_key_fingerprints": sorted(
                item.fingerprint for item in required_keys
            ),
            "consulted_attestation_fingerprints": [item.fingerprint for item in ordered],
            "source_export_fingerprints": sorted(
                {item.source_export.fingerprint for item in ordered}
            ),
            "ingestion_contract_fingerprints": sorted(
                {item.partition_content.ingestion_contract.fingerprint for item in ordered}
            ),
        }
    )


def _direct_vector_oracle(
    probe: EvidenceReplayProbe,
    bindings: tuple[VectorEvidenceBinding, ...],
) -> _OracleResult:
    required_keys = tuple(
        EvidencePartitionKey(probe.source_layer, "bng-10km/v1", cell)
        for cell in _bng_cells_intersecting(probe.selector)
    )
    owner_by_key = {
        key.fingerprint: binding
        for binding in bindings
        if binding.source_layer == probe.source_layer
        for key in binding.requested_partition_keys
    }
    missing = [key.cell for key in required_keys if key.fingerprint not in owner_by_key]
    if missing:
        raise ValueError(
            "governed replay bindings do not cover selector BNG cells: "
            + ", ".join(sorted(missing))
        )
    keys_by_binding: dict[str, list[EvidencePartitionKey]] = {}
    binding_by_fingerprint: dict[str, VectorEvidenceBinding] = {}
    for key in required_keys:
        binding = owner_by_key[key.fingerprint]
        binding_by_fingerprint[binding.fingerprint] = binding
        keys_by_binding.setdefault(binding.fingerprint, []).append(key)

    semantic_rows: dict[tuple[str, str], dict[str, object]] = {}
    attestations: list[EvidencePartitionAttestation] = []
    adapter_reads = 0
    for binding_fingerprint in sorted(keys_by_binding):
        binding = binding_by_fingerprint[binding_fingerprint]
        keys = tuple(keys_by_binding[binding_fingerprint])
        if binding.source_layer == open_roads_adapter.SOURCE_LAYER:
            source_path = open_roads_adapter.validate_export(
                binding.source_export, binding.ingestion_contract
            )
            partitions = tuple(
                open_roads_adapter.read_partition(
                    source_path,
                    binding.source_export,
                    binding.ingestion_contract,
                    key,
                )
                for key in keys
            )
            adapter_reads += len(keys)
        else:
            source_path = osm_network_adapter.validate_export(
                binding.source_export, binding.ingestion_contract
            )
            partitions = osm_network_adapter.read_partitions(
                source_path,
                binding.source_export,
                binding.ingestion_contract,
                keys,
            )
            adapter_reads += 1
        for partition in partitions:
            feature_payloads = tuple(
                {
                    "logical_key": feature.logical_key,
                    "geometry_fingerprint": evidence_geometry_fingerprint(
                        feature.geometry, "EPSG:27700"
                    ),
                    "attributes": dict(feature.attributes),
                }
                for feature in partition.features
            )
            content = EvidencePartitionContent(
                partition_key=partition.partition_key,
                ingestion_contract=binding.ingestion_contract,
                features=feature_payloads,
                availability="available" if feature_payloads else "no-data",
            )
            attestation = EvidencePartitionAttestation(
                partition_content=content,
                source_export=binding.source_export,
            )
            attestations.append(attestation)
            for feature in partition.features:
                if not _matches_probe(feature.geometry, feature.attributes, probe):
                    continue
                row = {
                    "source_export_fingerprint": binding.source_export.fingerprint,
                    "logical_key": feature.logical_key,
                    "geometry_fingerprint": evidence_geometry_fingerprint(
                        feature.geometry, "EPSG:27700"
                    ),
                    "attributes": {field: feature.attributes[field] for field in probe.projection},
                }
                row_key = (binding.source_export.fingerprint, feature.logical_key)
                prior = semantic_rows.setdefault(row_key, row)
                if prior != row:
                    raise ValueError(
                        "one governed source feature has conflicting partition observations"
                    )
    ordered_attestations = tuple(sorted(attestations, key=lambda item: item.fingerprint))
    availability_counts = {
        availability: sum(
            item.partition_content.availability == availability for item in ordered_attestations
        )
        for availability in ("available", "no-data", "explicit-unknown")
    }
    observation = _semantic_vector_observation(
        tuple(semantic_rows[key] for key in sorted(semantic_rows)),
        availability_counts,
    )
    dependency = {
        "contract": _DEPENDENCY_CONTRACT,
        "kind": "vector",
        "probe_fingerprint": probe.fingerprint,
        "required_partition_key_fingerprints": sorted(item.fingerprint for item in required_keys),
        "consulted_attestation_fingerprints": [item.fingerprint for item in ordered_attestations],
        "source_export_fingerprints": sorted(
            {item.source_export.fingerprint for item in ordered_attestations}
        ),
        "ingestion_contract_fingerprints": sorted(
            {item.partition_content.ingestion_contract.fingerprint for item in ordered_attestations}
        ),
    }
    return _OracleResult(
        observation=observation,
        dependency=MappingProxyType(dependency),
        adapter_partition_reads=adapter_reads,
    )


def _matches_probe(
    geometry: BaseGeometry,
    attributes: Mapping[str, object],
    probe: EvidenceReplayProbe,
) -> bool:
    predicate = {
        "intersects": geometry.intersects,
        "within": geometry.within,
        "contains": geometry.contains,
    }[probe.predicate]
    return predicate(probe.selector) and all(
        attributes.get(field) == value for field, value in probe.filters.items()
    )


def _validate_store_read_set(result: EvidenceQueryResult, dependency: Mapping[str, object]) -> None:
    manifest = result.manifest
    expected_required = dependency["required_partition_key_fingerprints"]
    expected_consulted = dependency["consulted_attestation_fingerprints"]
    if sorted(manifest["required_partition_key_fingerprints"]) != expected_required:
        raise ValueError("Local Evidence query required partition read set differs")
    if sorted(manifest["consulted_attestation_fingerprints"]) != expected_consulted:
        raise ValueError("Local Evidence query consulted attestation read set differs")
    allowed_sources = set(dependency["source_export_fingerprints"])
    if any(row.source_export_fingerprint not in allowed_sources for row in result.rows):
        raise ValueError("Local Evidence query returned an unbound Source Export")


def _vector_observation(result: EvidenceQueryResult) -> ProbeObservation:
    rows = tuple(
        {
            "source_export_fingerprint": row.source_export_fingerprint,
            "logical_key": row.logical_key,
            "geometry_fingerprint": row.geometry_fingerprint,
            "attributes": dict(row.attributes),
        }
        for row in result.rows
    )
    counts = result.manifest.get("availability_counts")
    if not isinstance(counts, Mapping):
        raise ValueError("Local Evidence query has no governed availability counts")
    return _semantic_vector_observation(rows, counts)


def _semantic_vector_observation(
    rows: tuple[dict[str, object], ...],
    availability_counts: Mapping[str, object],
) -> ProbeObservation:
    return ProbeObservation(
        result_fingerprint=evidence_fingerprint(
            {
                "contract": "satn-source-query-vector-semantic-result/v1",
                "rows": list(rows),
            }
        ),
        availability_counts=availability_counts,
    )


def _elevation_observation(result: ElevationSamplingResult) -> ProbeObservation:
    counts = {
        availability: sum(item.availability == availability for item in result.observations)
        for availability in ("available", "no-data", "explicit-unknown")
    }
    return ProbeObservation(
        result_fingerprint=evidence_fingerprint(
            {
                "contract": "satn-source-query-ea-semantic-result/v1",
                "geometry_fingerprint": result.geometry_fingerprint,
                "spacing_mm": result.spacing_mm,
                "observations": [item.canonical_payload() for item in result.observations],
            }
        ),
        availability_counts=counts,
    )


def _resolve_ea_read_set(
    request: EvidenceReplayRequest,
    probe: EvidenceReplayProbe,
    *,
    verify_files: bool,
    io_counts: _RasterIoCounts | None = None,
) -> ElevationSamplingReadSet:
    assert request.ea_cache_dir is not None
    connection = duckdb.connect(str(request.store_path), read_only=True)
    try:
        return resolve_elevation_sampling_read_set(
            connection,
            cache_dir=request.ea_cache_dir,
            geometry=probe.selector,
            geometry_crs="EPSG:27700",
            spacing_mm=probe.elevation_spacing_mm,
            state_fingerprint=request.elevation_state_fingerprint,
            verify_files=verify_files,
            io_observer=None if io_counts is None else io_counts.observe,
        )
    finally:
        connection.close()


def _direct_ea_sample(
    request: EvidenceReplayRequest,
    probe: EvidenceReplayProbe,
    *,
    io_counts: _RasterIoCounts,
) -> ElevationSamplingResult:
    """Sample canonical receipt/object bytes without the LocalEvidenceStore method."""

    assert request.ea_cache_dir is not None
    connection = duckdb.connect(str(request.store_path), read_only=True)
    try:
        return sample_elevation_from_receipts(
            connection,
            cache_dir=request.ea_cache_dir,
            geometry=probe.selector,
            geometry_crs="EPSG:27700",
            spacing_mm=probe.elevation_spacing_mm,
            state_fingerprint=request.elevation_state_fingerprint,
            io_observer=io_counts.observe,
        )
    finally:
        connection.close()


def _ea_dependency(
    probe: EvidenceReplayProbe,
    result: ElevationSamplingReadSet | ElevationSamplingResult,
) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "contract": _DEPENDENCY_CONTRACT,
            "kind": "elevation",
            "probe_fingerprint": probe.fingerprint,
            "geometry_fingerprint": result.geometry_fingerprint,
            "spacing_mm": result.spacing_mm,
            "consulted_attestation_fingerprints": list(result.consulted_attestation_fingerprints),
            "tile_receipt_fingerprints": list(result.tile_receipt_fingerprints),
        }
    )


def _ea_receipt_oracle(
    probe: EvidenceReplayProbe,
    result: ElevationSamplingResult,
    observation: ProbeObservation,
) -> _OracleResult:
    return _OracleResult(
        observation=observation,
        dependency=_ea_dependency(probe, result),
        adapter_partition_reads=0,
    )


def _generation_directory(cache_path: Path) -> Path:
    return cache_path.with_name(f".{cache_path.name}.generations")


def _load_committed_cache(
    request: EvidenceReplayRequest,
) -> _CommittedCache:
    if not request.run_manifest_path.exists():
        return _CommittedCache(
            entries=MappingProxyType({}),
            generation_fingerprint=None,
            commit_identity=None,
            runtime_identity=None,
        )
    try:
        commit = json.loads(request.run_manifest_path.read_text(encoding="utf-8"))
        commit_fingerprint = commit.pop("fingerprint")
        if commit.get("contract") != _COMMIT_CONTRACT:
            raise ValueError("unsupported replay commit contract")
        if commit_fingerprint != evidence_fingerprint(commit):
            raise ValueError("replay commit fingerprint mismatch")
        generation_fingerprint = _sha256(commit["generation_fingerprint"], "generation fingerprint")
        manifest = commit["manifest"]
        if not isinstance(manifest, dict):
            raise ValueError("replay commit manifest must be an object")
        manifest_fingerprint = manifest.get("fingerprint")
        unsigned_manifest = dict(manifest)
        unsigned_manifest.pop("fingerprint", None)
        if manifest_fingerprint != evidence_fingerprint(unsigned_manifest):
            raise ValueError("replay manifest fingerprint mismatch")
        manifest_cache = manifest.get("cache")
        if (
            not isinstance(manifest_cache, dict)
            or manifest_cache.get("generation_fingerprint") != generation_fingerprint
        ):
            raise ValueError("replay manifest does not name its cache generation")
        generation_path = (
            _generation_directory(request.cache_path) / f"{generation_fingerprint}.json"
        )
        generation = json.loads(generation_path.read_text(encoding="utf-8"))
        stored_generation_fingerprint = generation.pop("fingerprint")
        if generation.get("contract") != _GENERATION_CONTRACT:
            raise ValueError("unsupported replay generation contract")
        if stored_generation_fingerprint != evidence_fingerprint(generation):
            raise ValueError("replay generation fingerprint mismatch")
        if stored_generation_fingerprint != generation_fingerprint:
            raise ValueError("replay commit points at a different generation")
        raw_entries = generation.get("entries")
        if not isinstance(raw_entries, dict):
            raise ValueError("replay generation entries must be an object")
        entries: dict[str, _CacheEntry] = {}
        for key, payload in raw_entries.items():
            _sha256(key, "cache key")
            if not isinstance(payload, dict):
                raise ValueError("cache entry must be an object")
            dependency = payload["dependency"]
            if not isinstance(dependency, dict):
                raise ValueError("cache dependency must be an object")
            if key != evidence_fingerprint(dependency):
                raise ValueError("cache dependency key is stale")
            observation_payload = payload["observation"]
            if not isinstance(observation_payload, dict):
                raise ValueError("cache observation must be an object")
            observation = ProbeObservation(
                result_fingerprint=observation_payload["result_fingerprint"],
                availability_counts=observation_payload["availability_counts"],
                fingerprint=observation_payload["fingerprint"],
            )
            entries[key] = _CacheEntry(
                dependency=MappingProxyType(dependency),
                observation=observation,
            )
        commit_identity = manifest.get("commit")
        runtime_identity = manifest.get("runtime")
        if not isinstance(commit_identity, dict) or not isinstance(runtime_identity, dict):
            raise ValueError("replay manifest reuse identity is incomplete")
        return _CommittedCache(
            entries=MappingProxyType(entries),
            generation_fingerprint=generation_fingerprint,
            commit_identity=MappingProxyType(commit_identity),
            runtime_identity=MappingProxyType(runtime_identity),
        )
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise ValueError(
            "Source Query Replay committed state is corrupt; refusing to advance it"
        ) from error


def _commit_generation(
    request: EvidenceReplayRequest,
    generation: Mapping[str, object],
    manifest: Mapping[str, object],
) -> None:
    generation_fingerprint = str(generation["fingerprint"])
    manifest_cache = manifest.get("cache")
    if (
        not isinstance(manifest_cache, dict)
        or manifest_cache.get("generation_fingerprint") != generation_fingerprint
    ):
        raise ValueError("replay manifest does not commit its cache generation")
    generation_path = _generation_directory(request.cache_path) / f"{generation_fingerprint}.json"
    _write_immutable_json(generation_path, generation)
    commit: dict[str, object] = {
        "contract": _COMMIT_CONTRACT,
        "generation_fingerprint": generation_fingerprint,
        "manifest": dict(manifest),
    }
    commit["fingerprint"] = evidence_fingerprint(commit)
    _atomic_write_json(request.run_manifest_path, commit)


def _cache_generation(
    entries: Mapping[str, _CacheEntry],
) -> dict[str, object]:
    generation: dict[str, object] = {
        "contract": _GENERATION_CONTRACT,
        "entries": {
            key: {
                "dependency": dict(entry.dependency),
                "observation": entry.observation.canonical_payload()
                | {"fingerprint": entry.observation.fingerprint},
            }
            for key, entry in sorted(entries.items())
        },
    }
    generation["fingerprint"] = evidence_fingerprint(generation)
    return generation


def _write_immutable_json(path: Path, value: Mapping[str, object]) -> None:
    data = _json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("immutable replay generation fingerprint collision")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            if path.read_bytes() != data:
                raise ValueError("immutable replay generation fingerprint collision") from error
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()


def _git_identity() -> dict[str, object]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        _sha256(commit, "git commit")
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        return {"sha": commit, "available": True, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError, ValueError):
        return {"sha": None, "available": False, "dirty": True}


def _runtime_identity() -> dict[str, object]:
    distributions = {}
    for name in REPLAY_RUNTIME_DISTRIBUTIONS:
        try:
            distributions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            distributions[name] = "unavailable"
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable": str(Path(sys.executable).resolve()),
        "distributions": distributions,
    }


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)
