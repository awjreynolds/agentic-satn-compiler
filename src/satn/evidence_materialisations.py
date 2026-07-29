"""Immutable Area Extraction and Canonical Network materialisations.

The records in this module are deliberately independent of a DuckDB file.  They
are the logical rows a Local Evidence Store materialises: paths, row order,
database bytes and query plans never enter their identity.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry

from satn.content_identity import canonical_network_geometry_fingerprint
from satn.evidence_contracts import (
    canonical_evidence_json,
    evidence_fingerprint,
    evidence_geometry_fingerprint,
)
from satn.local_evidence_store import EvidenceQueryResult, EvidenceQueryRow

AreaPredicate = Literal["intersects", "within", "contains"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PREDICATES: dict[AreaPredicate, str] = {
    "intersects": "feature_geometry intersects area_geometry",
    "within": "feature_geometry within area_geometry",
    "contains": "feature_geometry contains area_geometry",
}


@dataclass(frozen=True)
class AreaExtractionFeature:
    """One exact selected source observation and all of its partition lineage."""

    source_layer: str
    source_export_fingerprint: str
    source_logical_key: str
    feature_content_fingerprint: str
    source_geometry_fingerprint: str
    geometry: BaseGeometry = field(compare=False, repr=False)
    attributes: Mapping[str, object]
    partition_attestation_fingerprints: tuple[str, ...]
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-area-extraction-feature/v1")

    def __post_init__(self) -> None:
        _required_text(self.source_layer, "source_layer")
        _required_text(self.source_logical_key, "source_logical_key")
        for name in (
            "source_export_fingerprint",
            "feature_content_fingerprint",
            "source_geometry_fingerprint",
        ):
            _require_sha256(getattr(self, name), name)
        if (
            evidence_geometry_fingerprint(self.geometry, "EPSG:27700")
            != self.source_geometry_fingerprint
        ):
            raise ValueError("area extraction source geometry fingerprint is stale")
        attributes = _freeze_mapping(self.attributes)
        attestations = _sorted_sha256_set(
            self.partition_attestation_fingerprints,
            "partition_attestation_fingerprints",
        )
        if not attestations:
            raise ValueError("area extraction features require partition attestations")
        object.__setattr__(self, "attributes", attributes)
        object.__setattr__(self, "partition_attestation_fingerprints", attestations)
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("area extraction feature fingerprint is stale")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "source_layer": self.source_layer,
            "source_export_fingerprint": self.source_export_fingerprint,
            "source_logical_key": self.source_logical_key,
            "feature_content_fingerprint": self.feature_content_fingerprint,
            "source_geometry_fingerprint": self.source_geometry_fingerprint,
            "attributes": dict(self.attributes),
            "partition_attestation_fingerprints": list(
                self.partition_attestation_fingerprints
            ),
        }


@dataclass(frozen=True)
class AreaExtractionMaterialisation:
    """The exact in-area evidence view and its closed coverage report."""

    area_geometry_fingerprint: str
    predicate: AreaPredicate
    coverage_state_fingerprints: tuple[str, ...]
    consulted_cells: tuple[str, ...]
    consulted_attestation_fingerprints: tuple[str, ...]
    availability_counts: Mapping[str, int]
    selected_feature_ids: tuple[str, ...]
    deduplicated_feature_ids: tuple[str, ...]
    rejected_feature_ids: tuple[str, ...]
    features: tuple[AreaExtractionFeature, ...]
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-area-extraction/v1")
    working_crs: str = field(init=False, default="EPSG:27700")

    def __post_init__(self) -> None:
        _require_sha256(self.area_geometry_fingerprint, "area_geometry_fingerprint")
        if self.predicate not in _PREDICATES:
            raise ValueError("area predicate must be intersects, within, or contains")
        coverage_states = _sorted_sha256_set(
            self.coverage_state_fingerprints, "coverage_state_fingerprints"
        )
        attestations = _sorted_sha256_set(
            self.consulted_attestation_fingerprints,
            "consulted_attestation_fingerprints",
        )
        cells = _sorted_text_set(self.consulted_cells, "consulted_cells")
        counts = {
            name: _nonnegative_int(self.availability_counts.get(name, 0), name)
            for name in ("available", "no-data", "explicit-unknown")
        }
        if set(self.availability_counts) - set(counts):
            raise ValueError("area extraction availability report has unknown states")
        features = tuple(sorted(self.features, key=lambda item: item.fingerprint))
        if len({item.fingerprint for item in features}) != len(features):
            raise ValueError("area extraction cannot contain duplicate features")
        selected = _sorted_text_set(self.selected_feature_ids, "selected_feature_ids")
        deduplicated = _sorted_text_set(
            self.deduplicated_feature_ids, "deduplicated_feature_ids"
        )
        rejected = _sorted_text_set(self.rejected_feature_ids, "rejected_feature_ids")
        if set(selected) & set(rejected):
            raise ValueError("an area feature cannot be both selected and rejected")
        if set(deduplicated) - set(selected):
            raise ValueError("deduplicated feature IDs must also be selected")
        object.__setattr__(self, "coverage_state_fingerprints", coverage_states)
        object.__setattr__(self, "consulted_attestation_fingerprints", attestations)
        object.__setattr__(self, "consulted_cells", cells)
        object.__setattr__(self, "availability_counts", MappingProxyType(counts))
        object.__setattr__(self, "selected_feature_ids", selected)
        object.__setattr__(self, "deduplicated_feature_ids", deduplicated)
        object.__setattr__(self, "rejected_feature_ids", rejected)
        object.__setattr__(self, "features", features)
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("area extraction fingerprint is stale")
        object.__setattr__(self, "fingerprint", expected)

    @property
    def coverage_unknown(self) -> bool:
        return self.availability_counts["explicit-unknown"] > 0

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "area_geometry_fingerprint": self.area_geometry_fingerprint,
            "working_crs": self.working_crs,
            "predicate": self.predicate,
            "predicate_operand_order": _PREDICATES[self.predicate],
            "coverage_state_fingerprints": list(self.coverage_state_fingerprints),
            "consulted_cells": list(self.consulted_cells),
            "consulted_attestation_fingerprints": list(
                self.consulted_attestation_fingerprints
            ),
            "availability_counts": dict(self.availability_counts),
            "selected_feature_ids": list(self.selected_feature_ids),
            "deduplicated_feature_ids": list(self.deduplicated_feature_ids),
            "rejected_feature_ids": list(self.rejected_feature_ids),
            "feature_fingerprints": [item.fingerprint for item in self.features],
        }


@dataclass(frozen=True)
class CanonicalLogicalEdge:
    """One stable logical edge revision with exact source and geometry lineage."""

    stable_edge_id: str
    edge_role: str
    endpoint_node_keys: tuple[str, str]
    constituent_source_logical_keys: tuple[str, ...]
    geometry_fingerprint: str
    geometry: BaseGeometry = field(compare=False, repr=False)
    source_feature_fingerprints: tuple[str, ...]
    partition_attestation_fingerprints: tuple[str, ...]

    contract: str = field(init=False, default="satn-canonical-logical-edge/v1")

    def __post_init__(self) -> None:
        if re.fullmatch(r"edge:v1:[0-9a-f]{64}", self.stable_edge_id) is None:
            raise ValueError("stable_edge_id must be edge:v1:<full-sha256>")
        _required_text(self.edge_role, "edge_role")
        endpoints = tuple(
            _required_text(value, "endpoint_node_key")
            for value in self.endpoint_node_keys
        )
        if len(endpoints) != 2 or endpoints[0] == endpoints[1]:
            raise ValueError("canonical logical edges require two distinct endpoint keys")
        endpoints = tuple(sorted(endpoints))
        source_keys = _canonical_source_key_sequence(
            self.constituent_source_logical_keys
        )
        _require_sha256(self.geometry_fingerprint, "geometry_fingerprint")
        if (
            canonical_network_geometry_fingerprint(self.geometry, "EPSG:27700")
            != self.geometry_fingerprint
        ):
            raise ValueError("canonical logical edge geometry fingerprint is stale")
        feature_fingerprints = _sorted_sha256_set(
            self.source_feature_fingerprints, "source_feature_fingerprints"
        )
        attestations = _sorted_sha256_set(
            self.partition_attestation_fingerprints,
            "partition_attestation_fingerprints",
        )
        expected_edge_id = _stable_edge_id(self.edge_role, endpoints, source_keys)
        if self.stable_edge_id != expected_edge_id:
            raise ValueError("stable edge ID is stale for its source-key lineage")
        object.__setattr__(self, "endpoint_node_keys", endpoints)
        object.__setattr__(self, "constituent_source_logical_keys", source_keys)
        object.__setattr__(self, "source_feature_fingerprints", feature_fingerprints)
        object.__setattr__(self, "partition_attestation_fingerprints", attestations)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "stable_edge_id": self.stable_edge_id,
            "edge_role": self.edge_role,
            "endpoint_node_keys": list(self.endpoint_node_keys),
            "constituent_source_logical_keys": list(
                self.constituent_source_logical_keys
            ),
            "geometry_fingerprint": self.geometry_fingerprint,
            "source_feature_fingerprints": list(self.source_feature_fingerprints),
            "partition_attestation_fingerprints": list(
                self.partition_attestation_fingerprints
            ),
        }


@dataclass(frozen=True)
class AreaNetworkMaterialisation:
    """A reusable Area Extraction and its immutable canonical edge registry."""

    extraction: AreaExtractionMaterialisation
    normalisation_contract: Mapping[str, object]
    logical_edges: tuple[CanonicalLogicalEdge, ...]
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-canonical-network/v1")

    def __post_init__(self) -> None:
        if not isinstance(self.extraction, AreaExtractionMaterialisation):
            raise ValueError("canonical network requires an Area Extraction")
        normalisation = _freeze_mapping(self.normalisation_contract)
        edges = tuple(sorted(self.logical_edges, key=lambda item: item.stable_edge_id))
        if len({edge.stable_edge_id for edge in edges}) != len(edges):
            raise ValueError("canonical network cannot contain duplicate stable edge IDs")
        object.__setattr__(self, "normalisation_contract", normalisation)
        object.__setattr__(self, "logical_edges", edges)
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("canonical network fingerprint is stale")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "area_extraction_fingerprint": self.extraction.fingerprint,
            "normalisation_contract": dict(self.normalisation_contract),
            "logical_edges": [edge.canonical_payload() for edge in self.logical_edges],
        }


def materialise_area_network(
    coverage: EvidenceQueryResult
    | Mapping[str, EvidenceQueryResult]
    | Iterable[EvidenceQueryResult],
    area: BaseGeometry,
    normalisation_contract: Mapping[str, object],
) -> AreaNetworkMaterialisation:
    """Materialise an exact area view and stable canonical network.

    ``coverage`` consists of exact, pinned Local Evidence Store query results.
    This keeps source consultation and deduplication manifests intact and prevents
    the materialisation from silently widening its Evidence Coverage.

    The normalisation contract accepts these closed fields:

    ``contract``
        Required versioned contract name.
    ``predicate``
        ``intersects`` (default), ``within`` or ``contains``.
    ``edge_role`` / ``edge_role_attribute``
        A fixed role or selected source attribute containing it.
    ``endpoint_key_attributes``
        Two selected source attributes containing stable endpoint node keys.
        When omitted, stable endpoint keys are scoped below the source logical
        key; geometry, FID and row order are never substituted.
    """

    if not isinstance(area, BaseGeometry) or area.is_empty or not area.is_valid:
        raise ValueError("area must be a nonempty valid geometry")
    if not isinstance(normalisation_contract, Mapping):
        raise ValueError("normalisation_contract must be a mapping")
    contract = dict(normalisation_contract)
    _required_text(contract.get("contract"), "normalisation contract")
    allowed = {
        "contract",
        "predicate",
        "edge_role",
        "edge_role_attribute",
        "endpoint_key_attributes",
    }
    unknown = set(contract) - allowed
    if unknown:
        raise ValueError(
            "normalisation_contract contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    predicate = contract.get("predicate", "intersects")
    if predicate not in _PREDICATES:
        raise ValueError("normalisation predicate must be intersects, within, or contains")
    predicate = str(predicate)
    query_results = _normalise_query_results(coverage)
    extraction = _materialise_area_extraction(query_results, area, predicate)
    edges = tuple(
        _canonical_edge(feature, contract)
        for feature in extraction.features
        if isinstance(feature.geometry, (LineString, MultiLineString))
    )
    return AreaNetworkMaterialisation(
        extraction=extraction,
        normalisation_contract=contract,
        logical_edges=edges,
    )


def _materialise_area_extraction(
    query_results: tuple[tuple[str, EvidenceQueryResult], ...],
    area: BaseGeometry,
    predicate: str,
) -> AreaExtractionMaterialisation:
    states: set[str] = set()
    cells: set[str] = set()
    attestations: set[str] = set()
    availability = {"available": 0, "no-data": 0, "explicit-unknown": 0}
    selected: list[str] = []
    rejected: list[str] = []
    deduplicated: list[str] = []
    by_source_key: dict[tuple[str, str], AreaExtractionFeature] = {}
    for source_layer, result in query_results:
        manifest = result.manifest
        states.add(str(manifest["coverage_state_fingerprint"]))
        cells.update(str(value) for value in manifest["required_bng_10km_cells"])
        attestations.update(
            str(value) for value in manifest["consulted_attestation_fingerprints"]
        )
        counts = manifest["availability_counts"]
        if not isinstance(counts, Mapping):
            raise ValueError("Evidence Query coverage report is invalid")
        for name in availability:
            availability[name] += _nonnegative_int(counts.get(name, 0), name)
        for row in result.rows:
            observation_id = f"{row.source_export_fingerprint}:{row.logical_key}"
            if not _matches(row.geometry, area, predicate):
                rejected.append(observation_id)
                continue
            selected.append(observation_id)
            feature = _area_feature(source_layer, row)
            key = (source_layer, row.logical_key)
            prior = by_source_key.get(key)
            if prior is None:
                by_source_key[key] = feature
            elif prior.canonical_payload() != feature.canonical_payload():
                raise ValueError(
                    "one stable source logical key resolves to conflicting feature content"
                )
            else:
                deduplicated.append(observation_id)
    return AreaExtractionMaterialisation(
        area_geometry_fingerprint=evidence_geometry_fingerprint(area, "EPSG:27700"),
        predicate=predicate,  # type: ignore[arg-type]
        coverage_state_fingerprints=tuple(states),
        consulted_cells=tuple(cells),
        consulted_attestation_fingerprints=tuple(attestations),
        availability_counts=availability,
        selected_feature_ids=tuple(set(selected)),
        deduplicated_feature_ids=tuple(set(deduplicated)),
        rejected_feature_ids=tuple(set(rejected)),
        features=tuple(by_source_key.values()),
    )


def _area_feature(source_layer: str, row: EvidenceQueryRow) -> AreaExtractionFeature:
    return AreaExtractionFeature(
        source_layer=source_layer,
        source_export_fingerprint=row.source_export_fingerprint,
        source_logical_key=row.logical_key,
        feature_content_fingerprint=row.feature_content_fingerprint,
        source_geometry_fingerprint=row.geometry_fingerprint,
        geometry=row.geometry,
        attributes=row.attributes,
        partition_attestation_fingerprints=row.attestation_fingerprints,
    )


def _canonical_edge(
    feature: AreaExtractionFeature,
    contract: Mapping[str, object],
) -> CanonicalLogicalEdge:
    role_attribute = contract.get("edge_role_attribute")
    if role_attribute is not None:
        role = feature.attributes.get(_required_text(role_attribute, "edge_role_attribute"))
    else:
        role = contract.get("edge_role", "network-edge")
    role = _required_text(role, "edge_role")
    endpoint_attributes = contract.get("endpoint_key_attributes")
    if endpoint_attributes is None:
        endpoints = (
            f"{feature.source_logical_key}:endpoint:0",
            f"{feature.source_logical_key}:endpoint:1",
        )
    else:
        if (
            not isinstance(endpoint_attributes, (tuple, list))
            or len(endpoint_attributes) != 2
        ):
            raise ValueError("endpoint_key_attributes must contain exactly two field names")
        fields = tuple(
            _required_text(value, "endpoint_key_attribute") for value in endpoint_attributes
        )
        endpoints = tuple(
            _required_text(feature.attributes.get(name), f"endpoint attribute {name}")
            for name in fields
        )
    source_keys = (feature.source_logical_key,)
    stable_edge_id = _stable_edge_id(role, tuple(sorted(endpoints)), source_keys)
    return CanonicalLogicalEdge(
        stable_edge_id=stable_edge_id,
        edge_role=role,
        endpoint_node_keys=endpoints,  # type: ignore[arg-type]
        constituent_source_logical_keys=source_keys,
        geometry_fingerprint=canonical_network_geometry_fingerprint(
            feature.geometry, "EPSG:27700"
        ),
        geometry=feature.geometry,
        source_feature_fingerprints=(feature.fingerprint,),
        partition_attestation_fingerprints=feature.partition_attestation_fingerprints,
    )


def _normalise_query_results(
    coverage: EvidenceQueryResult
    | Mapping[str, EvidenceQueryResult]
    | Iterable[EvidenceQueryResult],
) -> tuple[tuple[str, EvidenceQueryResult], ...]:
    if isinstance(coverage, EvidenceQueryResult):
        candidates = ((str(coverage.manifest["source_layer"]), coverage),)
    elif isinstance(coverage, Mapping):
        candidates = tuple((str(layer), result) for layer, result in coverage.items())
    else:
        candidates = tuple(
            (str(result.manifest["source_layer"]), result) for result in coverage
        )
    if not candidates:
        raise ValueError("area materialisation requires at least one Evidence Query result")
    for source_layer, result in candidates:
        _required_text(source_layer, "source_layer")
        if not isinstance(result, EvidenceQueryResult):
            raise ValueError("coverage must contain EvidenceQueryResult records")
        if result.manifest["source_layer"] != source_layer:
            raise ValueError("coverage source-layer key does not match its query manifest")
        if result.manifest["selector_crs"] != "EPSG:27700":
            raise ValueError("area materialisation requires EPSG:27700 query results")
    return tuple(sorted(candidates, key=lambda item: (item[0], item[1].fingerprint)))


def _matches(feature: BaseGeometry, area: BaseGeometry, predicate: str) -> bool:
    if predicate == "intersects":
        return feature.intersects(area)
    if predicate == "within":
        return feature.within(area)
    return feature.contains(area)


def _stable_edge_id(
    edge_role: str,
    endpoint_node_keys: tuple[str, ...],
    source_keys: tuple[str, ...],
) -> str:
    payload = {
        "contract": "satn-stable-logical-edge/v1",
        "edge_role": edge_role,
        "endpoint_node_keys": list(sorted(endpoint_node_keys)),
        "constituent_source_logical_keys": list(
            _canonical_source_key_sequence(source_keys)
        ),
    }
    return "edge:v1:" + evidence_fingerprint(payload)


def _canonical_source_key_sequence(values: tuple[str, ...]) -> tuple[str, ...]:
    keys = tuple(_required_text(value, "constituent source logical key") for value in values)
    if not keys:
        raise ValueError("stable logical edge requires source logical keys")
    reverse = tuple(reversed(keys))
    return min(keys, reverse)


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    canonical = canonical_evidence_json(dict(value))
    # Round-trip through the canonical parser is unnecessary: evidence JSON has
    # already rejected floats and unsupported values, and a shallow immutable
    # copy is sufficient because nested mutation cannot change the retained
    # canonical payload used to derive the fingerprint below.
    import json

    parsed = json.loads(canonical)
    return MappingProxyType(parsed)


def _sorted_sha256_set(values: Iterable[str], name: str) -> tuple[str, ...]:
    result = tuple(sorted(_require_sha256(value, name) for value in values))
    if len(set(result)) != len(result):
        raise ValueError(f"{name} cannot contain duplicates")
    return result


def _sorted_text_set(values: Iterable[str], name: str) -> tuple[str, ...]:
    result = tuple(sorted(_required_text(value, name) for value in values))
    if len(set(result)) != len(result):
        raise ValueError(f"{name} cannot contain duplicates")
    return result


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def _require_sha256(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a full lowercase SHA-256")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value
