"""Immutable routing-candidate and Backbone assembly materialisations.

This module is an additive adapter over the current compiler result.  It does
not route, select, assemble, mutate a ``CompiledNetwork`` or grant publication
authority.  Its records preserve the exact emitted identifiers, attributes and
geometry so a later coordinator can reuse a validated stage-6 result only
after equivalence gates have passed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

import geopandas as gpd
import pandas as pd
from shapely.geometry.base import BaseGeometry

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

AssemblyRecordKind = Literal[
    "spine-access-connection",
    "access-obligation",
    "spine-access-branch",
    "branch-meeting-connection",
    "cross-spine-connector",
]


@dataclass(frozen=True)
class RoutingCandidateRecord:
    """One exact selected legacy candidate at the assembly boundary."""

    candidate_id: str
    network_role: str
    materialisation_order: int
    selected_route_role: str | None
    source_ids: tuple[str, ...]
    alignment_options: tuple[Mapping[str, object], ...]
    attributes: Mapping[str, object]
    geometry: BaseGeometry = field(compare=False, repr=False)
    crs: str
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-routing-candidate/v1")

    def __post_init__(self) -> None:
        _required_text(self.candidate_id, "candidate_id")
        _required_text(self.network_role, "network_role")
        _nonnegative_int(self.materialisation_order, "materialisation_order")
        if self.selected_route_role is not None:
            _required_text(self.selected_route_role, "selected_route_role")
        source_ids = _sorted_text_set(self.source_ids, "source_ids")
        options = tuple(
            _freeze_mapping(option, "alignment option") for option in self.alignment_options
        )
        attributes = _freeze_mapping(self.attributes, "candidate attributes")
        _validate_geometry(self.geometry)
        _required_text(self.crs, "crs")
        object.__setattr__(self, "source_ids", source_ids)
        object.__setattr__(self, "alignment_options", options)
        object.__setattr__(self, "attributes", attributes)
        expected = _fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("routing candidate fingerprint is stale")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "candidate_id": self.candidate_id,
            "network_role": self.network_role,
            "materialisation_order": self.materialisation_order,
            "selected_route_role": self.selected_route_role,
            "source_ids": list(self.source_ids),
            "alignment_options": [_thaw(option) for option in self.alignment_options],
            "attributes": _thaw(self.attributes),
            "geometry_wkb_hex": self.geometry.wkb_hex,
            "crs": self.crs,
        }

    def to_row(self) -> dict[str, object]:
        return {**_thaw(self.attributes), "geometry": self.geometry}


@dataclass(frozen=True)
class NetworkAssemblyRecord:
    """One exact typed row in the assembled Backbone-and-Access Network."""

    record_kind: AssemblyRecordKind
    record_id: str
    attributes: Mapping[str, object]
    geometry: BaseGeometry = field(compare=False, repr=False)
    crs: str
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-network-assembly-record/v1")

    def __post_init__(self) -> None:
        if self.record_kind not in _ASSEMBLY_FRAME_SPECS:
            raise ValueError("unsupported network assembly record kind")
        _required_text(self.record_id, "record_id")
        attributes = _freeze_mapping(self.attributes, "assembly attributes")
        _validate_geometry(self.geometry)
        _required_text(self.crs, "crs")
        object.__setattr__(self, "attributes", attributes)
        expected = _fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("network assembly record fingerprint is stale")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "record_kind": self.record_kind,
            "record_id": self.record_id,
            "attributes": _thaw(self.attributes),
            "geometry_wkb_hex": self.geometry.wkb_hex,
            "crs": self.crs,
        }

    def to_row(self) -> dict[str, object]:
        return {**_thaw(self.attributes), "geometry": self.geometry}


@dataclass(frozen=True)
class NetworkGapRecord:
    """One exact visible Network Gap, without fabricated route linework."""

    gap_id: str
    network_role: str
    attributes: Mapping[str, object]
    geometry: BaseGeometry = field(compare=False, repr=False)
    crs: str
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-network-gap-record/v1")

    def __post_init__(self) -> None:
        _required_text(self.gap_id, "gap_id")
        _required_text(self.network_role, "network_role")
        attributes = _freeze_mapping(self.attributes, "gap attributes")
        _validate_geometry(self.geometry)
        _required_text(self.crs, "crs")
        object.__setattr__(self, "attributes", attributes)
        expected = _fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("network gap fingerprint is stale")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "gap_id": self.gap_id,
            "network_role": self.network_role,
            "attributes": _thaw(self.attributes),
            "geometry_wkb_hex": self.geometry.wkb_hex,
            "crs": self.crs,
        }

    def to_row(self) -> dict[str, object]:
        return {**_thaw(self.attributes), "geometry": self.geometry}


@dataclass(frozen=True)
class RoutingAssemblyDiagnostics:
    """Typed deterministic counters plus separately observed run resources."""

    search_count: int
    settled_node_count: int
    edge_relaxation_count: int
    peak_frontier_size: int
    elapsed_seconds: float
    peak_rss_bytes: int
    details: Mapping[str, object]

    contract: str = field(init=False, default="satn-routing-assembly-diagnostics/v1")

    def __post_init__(self) -> None:
        for name in (
            "search_count",
            "settled_node_count",
            "edge_relaxation_count",
            "peak_frontier_size",
            "peak_rss_bytes",
        ):
            _nonnegative_int(getattr(self, name), name)
        if (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or not math.isfinite(float(self.elapsed_seconds))
            or self.elapsed_seconds < 0
        ):
            raise ValueError("elapsed_seconds must be a nonnegative finite number")
        object.__setattr__(
            self,
            "details",
            _freeze_mapping(self.details, "routing assembly diagnostic details"),
        )

    def deterministic_payload(self) -> dict[str, object]:
        """Return identity-bearing counters, excluding run-local resource observations."""
        return {
            "contract": self.contract,
            "search_count": self.search_count,
            "settled_node_count": self.settled_node_count,
            "edge_relaxation_count": self.edge_relaxation_count,
            "peak_frontier_size": self.peak_frontier_size,
            "details": _thaw(self.details),
        }

    def metadata(self) -> dict[str, object]:
        return {
            **self.deterministic_payload(),
            "elapsed_seconds": float(self.elapsed_seconds),
            "peak_rss_bytes": self.peak_rss_bytes,
        }


@dataclass(frozen=True)
class RoutingAssemblyMaterialisation:
    """Reusable stage-6 artifact with candidates and assembly independently bound."""

    routing_input_fingerprint: str
    routing_configuration: Mapping[str, object]
    candidates: tuple[RoutingCandidateRecord, ...]
    assembly_records: tuple[NetworkAssemblyRecord, ...]
    gaps: tuple[NetworkGapRecord, ...]
    diagnostics: RoutingAssemblyDiagnostics
    candidate_fingerprint: str = ""
    assembly_fingerprint: str = ""
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-routing-assembly-materialisation/v1")

    def __post_init__(self) -> None:
        _require_sha256(self.routing_input_fingerprint, "routing_input_fingerprint")
        configuration = _freeze_mapping(
            self.routing_configuration, "routing_configuration"
        )
        candidates = tuple(
            sorted(
                self.candidates,
                key=lambda item: (item.materialisation_order, item.candidate_id),
            )
        )
        if len({item.candidate_id for item in candidates}) != len(candidates):
            raise ValueError("routing materialisation cannot contain duplicate candidate IDs")
        records = tuple(
            sorted(
                self.assembly_records,
                key=lambda item: (item.record_kind, item.record_id),
            )
        )
        if len({(item.record_kind, item.record_id) for item in records}) != len(records):
            raise ValueError("routing materialisation cannot contain duplicate assembly records")
        gaps = tuple(sorted(self.gaps, key=lambda item: item.gap_id))
        if len({item.gap_id for item in gaps}) != len(gaps):
            raise ValueError("routing materialisation cannot contain duplicate Network Gaps")
        assembled_route_ids = {
            item.record_id
            for item in records
            if item.record_kind
            in {"spine-access-connection", "branch-meeting-connection"}
        }
        if {item.candidate_id for item in candidates} != assembled_route_ids:
            raise ValueError(
                "selected routing candidates must exactly match assembled route records"
            )
        object.__setattr__(self, "routing_configuration", configuration)
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "assembly_records", records)
        object.__setattr__(self, "gaps", gaps)
        candidate_fingerprint = _fingerprint(
            {
                "contract": "satn-routing-candidate-set/v1",
                "candidates": [item.fingerprint for item in candidates],
            }
        )
        assembly_fingerprint = _fingerprint(
            {
                "contract": "satn-backbone-access-assembly/v1",
                "records": [item.fingerprint for item in records],
                "gaps": [item.fingerprint for item in gaps],
            }
        )
        if self.candidate_fingerprint and self.candidate_fingerprint != candidate_fingerprint:
            raise ValueError("candidate-set fingerprint is stale")
        if self.assembly_fingerprint and self.assembly_fingerprint != assembly_fingerprint:
            raise ValueError("assembly fingerprint is stale")
        object.__setattr__(self, "candidate_fingerprint", candidate_fingerprint)
        object.__setattr__(self, "assembly_fingerprint", assembly_fingerprint)
        expected = _fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("routing assembly materialisation fingerprint is stale")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "routing_input_fingerprint": self.routing_input_fingerprint,
            "routing_configuration": _thaw(self.routing_configuration),
            "candidate_fingerprint": self.candidate_fingerprint,
            "assembly_fingerprint": self.assembly_fingerprint,
            "diagnostics": self.diagnostics.deterministic_payload(),
        }

    def assembly_frame(self, kind: AssemblyRecordKind) -> gpd.GeoDataFrame:
        """Rehydrate one exact legacy assembly frame for equivalence checks."""
        rows = [item.to_row() for item in self.assembly_records if item.record_kind == kind]
        crs = next(
            (item.crs for item in self.assembly_records if item.record_kind == kind),
            None,
        )
        return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)

    def gap_frame(self) -> gpd.GeoDataFrame:
        """Rehydrate the exact visible Network Gap frame."""
        rows = [item.to_row() for item in self.gaps]
        crs = self.gaps[0].crs if self.gaps else None
        return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


_ASSEMBLY_FRAME_SPECS: dict[AssemblyRecordKind, tuple[str, str]] = {
    "spine-access-connection": (
        "spine_access_connections",
        "access_connection_id",
    ),
    "access-obligation": ("access_obligations", "obligation_id"),
    "spine-access-branch": ("spine_access_branches", "branch_id"),
    "branch-meeting-connection": (
        "branch_meeting_connections",
        "meeting_connection_id",
    ),
    "cross-spine-connector": (
        "cross_spine_connectors",
        "cross_spine_connector_id",
    ),
}


def materialise_compiled_routing_assembly(
    compiled: object,
    *,
    routing_input_fingerprint: str,
    routing_configuration: Mapping[str, object],
    elapsed_seconds: float,
    peak_rss_bytes: int,
) -> RoutingAssemblyMaterialisation:
    """Snapshot the current compiler's exact routing/assembly oracle.

    ``compiled`` is intentionally structural rather than imported from
    ``satn.compiler`` so this sidecar cannot create a compiler dependency or
    execution-path cutover.
    """

    frames: dict[AssemblyRecordKind, gpd.GeoDataFrame] = {}
    records: list[NetworkAssemblyRecord] = []
    for kind, (attribute, identifier) in _ASSEMBLY_FRAME_SPECS.items():
        frame = _required_frame(compiled, attribute)
        frames[kind] = frame
        for row in _ordered_rows(frame, identifier):
            attributes, geometry = _row_payload(row)
            records.append(
                NetworkAssemblyRecord(
                    record_kind=kind,
                    record_id=_required_text(attributes.get(identifier), identifier),
                    attributes=attributes,
                    geometry=geometry,
                    crs=str(frame.crs),
                )
            )
    candidates: list[RoutingCandidateRecord] = []
    candidate_order = 0
    for kind, identifier in (
        ("spine-access-connection", "access_connection_id"),
        ("branch-meeting-connection", "meeting_connection_id"),
    ):
        frame = frames[kind]
        for row in _ordered_rows(frame, identifier):
            attributes, geometry = _row_payload(row)
            candidates.append(
                RoutingCandidateRecord(
                    candidate_id=_required_text(attributes.get(identifier), identifier),
                    network_role=_required_text(
                        attributes.get("network_role"), "network_role"
                    ),
                    materialisation_order=candidate_order,
                    selected_route_role=_optional_text(
                        attributes.get("topography_selected_role")
                    ),
                    source_ids=_json_text_tuple(attributes.get("source_ids"), "source_ids"),
                    alignment_options=_alignment_options(
                        attributes.get("alignment_options")
                    ),
                    attributes=attributes,
                    geometry=geometry,
                    crs=str(frame.crs),
                )
            )
            candidate_order += 1
    gap_frame = _required_frame(compiled, "gaps")
    gaps = [
        NetworkGapRecord(
            gap_id=_required_text(attributes.get("connection_id"), "connection_id"),
            network_role=_required_text(attributes.get("network_role"), "network_role"),
            attributes=attributes,
            geometry=geometry,
            crs=str(gap_frame.crs),
        )
        for row in _ordered_rows(gap_frame, "connection_id")
        for attributes, geometry in [_row_payload(row)]
    ]
    raw_diagnostics = getattr(compiled, "compilation_diagnostics", None)
    if not isinstance(raw_diagnostics, Mapping):
        raise ValueError("compiled routing assembly requires compilation diagnostics")
    details = _normalise_value(raw_diagnostics)
    assert isinstance(details, Mapping)
    diagnostics = RoutingAssemblyDiagnostics(
        search_count=_sum_counters(
            details,
            {
                "candidate_evaluations",
                "root_pair_route_searches",
                "root_group_distance_planning_searches",
                "weighted_shortest_path_searches",
            },
        ),
        settled_node_count=_sum_counters(
            details,
            {
                "root_group_distance_planning_nodes_settled",
                "weighted_shortest_path_nodes_settled",
            },
        ),
        edge_relaxation_count=_sum_counters(
            details, {"weighted_shortest_path_edge_relaxations"}
        ),
        peak_frontier_size=_max_counters(
            details, {"peak_shortest_path_frontier"}
        ),
        elapsed_seconds=elapsed_seconds,
        peak_rss_bytes=peak_rss_bytes,
        details=details,
    )
    return RoutingAssemblyMaterialisation(
        routing_input_fingerprint=routing_input_fingerprint,
        routing_configuration=routing_configuration,
        candidates=tuple(candidates),
        assembly_records=tuple(records),
        gaps=tuple(gaps),
        diagnostics=diagnostics,
    )


def _required_frame(value: object, attribute: str) -> gpd.GeoDataFrame:
    frame = getattr(value, attribute, None)
    if not isinstance(frame, gpd.GeoDataFrame) or frame.crs is None:
        raise ValueError(f"compiled routing assembly requires a CRS-bearing {attribute} frame")
    return frame


def _ordered_rows(frame: gpd.GeoDataFrame, identifier: str) -> Iterable[pd.Series]:
    if identifier not in frame:
        if frame.empty:
            return ()
        raise ValueError(f"routing assembly frame is missing {identifier}")
    return (
        row
        for _, row in frame.sort_values(identifier, kind="stable").iterrows()
    )


def _row_payload(row: pd.Series) -> tuple[dict[str, object], BaseGeometry]:
    geometry = row.geometry
    _validate_geometry(geometry)
    return (
        {
            str(name): _normalise_value(value)
            for name, value in row.items()
            if name != "geometry"
        },
        geometry,
    )


def _alignment_options(value: object) -> tuple[Mapping[str, object], ...]:
    if value is None:
        return ()
    parsed = _json_value(value, "alignment_options")
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise ValueError("alignment_options must be a JSON array of objects")
    return tuple(parsed)


def _json_text_tuple(value: object, name: str) -> tuple[str, ...]:
    parsed = _json_value(value, name)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError(f"{name} must be a JSON array of strings")
    return tuple(parsed)


def _json_value(value: object, name: str) -> object:
    if value is None:
        return []
    if not isinstance(value, str):
        raise ValueError(f"{name} must be canonical JSON text")
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"{name} must be canonical JSON text") from error


def _normalise_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, Mapping):
        return {str(key): _normalise_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise_value(item) for item in value]
    if hasattr(value, "item"):
        return _normalise_value(value.item())
    if pd.isna(value):
        return None
    raise ValueError(f"unsupported routing materialisation value: {type(value).__name__}")


def _freeze_mapping(value: Mapping[str, object], name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return MappingProxyType(
        {
            str(key): _freeze_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    )


def _freeze_value(value: object) -> object:
    value = _normalise_value(value)
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_value(item) for key, item in sorted(value.items())}
        )
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _identity_json_value(value: object) -> object:
    value = _thaw(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("routing identity cannot contain non-finite floats")
        return {"$float64": value.hex()}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _identity_json_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list):
        return [_identity_json_value(item) for item in value]
    raise ValueError(f"unsupported routing identity value: {type(value).__name__}")


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        _identity_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _counter_values(value: object, names: set[str]) -> Iterable[int]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in names and isinstance(item, int) and not isinstance(item, bool) and item >= 0:
                yield item
            yield from _counter_values(item, names)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _counter_values(item, names)


def _sum_counters(value: Mapping[str, object], names: set[str]) -> int:
    return sum(_counter_values(value, names))


def _max_counters(value: Mapping[str, object], names: set[str]) -> int:
    return max(_counter_values(value, names), default=0)


def _validate_geometry(value: object) -> None:
    if (
        not isinstance(value, BaseGeometry)
        or value.is_empty
        or not value.is_valid
    ):
        raise ValueError("routing materialisation requires nonempty valid geometry")


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _required_text(value, "optional text")


def _sorted_text_set(values: Iterable[str], name: str) -> tuple[str, ...]:
    result = tuple(sorted(_required_text(value, name) for value in values))
    if len(set(result)) != len(result):
        raise ValueError(f"{name} cannot contain duplicates")
    return result


def _nonnegative_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _require_sha256(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a full lowercase SHA-256")
    return value
