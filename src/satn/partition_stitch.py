"""Canonical, fail-closed stitching of geographic partition artifacts.

The partition worker contracts intentionally stop at :class:`PartitionArtifact`.
This module is the pure global seam between those worker results and later
candidate selection.  It does not schedule work, inspect a database, or snap
geometry.  Inputs are validated in full before any output collections are
assembled, and every output is immutable and content-addressed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from satn.geographic_partitions import (
    EPSG_27700,
    BoundaryPortal,
    CandidateFragment,
    CompilationPartition,
    HaloReference,
    OwnedFeatureFragment,
    PartitionArtifact,
    PartitionGap,
    content_fingerprint,
    deterministic_feature_owner,
)

STITCH_CONTRACT = "satn-deterministic-partition-stitch/v1"
EVIDENCE_REQUEST_CONTRACT = "satn-stitch-evidence-request/v1"
HALO_EXTENSION_CONTRACT = "satn-targeted-halo-extension/v1"

_SHA256_LENGTH = 64


class StitchError(ValueError):
    """Base class for deterministic stitch validation failures."""


class MissingRequiredInputError(StitchError):
    """A required partition or dependency is absent before assembly starts."""


class StitchValidationError(StitchError):
    """A required stitch contract is malformed or cannot be verified."""


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise StitchValidationError(f"{name} must be nonempty canonical text")
    return value


def _sha(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise StitchValidationError(f"{name} must be a lowercase full SHA-256")
    return value


def _freeze(value: object) -> object:
    """Validate and freeze JSON-like provenance without importing pickle."""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StitchValidationError("provenance cannot contain non-finite numbers")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise StitchValidationError("provenance mapping keys must be strings")
        return {key: _freeze(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    raise StitchValidationError(f"unsupported provenance value: {type(value).__name__}")


def _iterable_values(values: object, name: str) -> tuple[object, ...]:
    if values is None or isinstance(values, (str, bytes, bytearray, Mapping)):
        raise StitchValidationError(f"{name} must be a non-string iterable")
    try:
        return tuple(values)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise StitchValidationError(f"{name} must be an iterable") from error


def _ordered_text(values: object, name: str) -> tuple[str, ...]:
    return tuple(sorted({_text(value, name) for value in _iterable_values(values, name)}))


def _partition_cells(values: Iterable[CompilationPartition | str]) -> tuple[str, ...]:
    cells: set[str] = set()
    for value in _iterable_values(values, "partition cells"):
        cell = value.cell if isinstance(value, CompilationPartition) else value
        try:
            partition = CompilationPartition(cell)
        except (TypeError, ValueError) as error:
            raise StitchValidationError(f"invalid partition identifier {cell!r}") from error
        cells.add(partition.cell)
    return tuple(sorted(cells))


@dataclass(frozen=True, slots=True)
class PartitionArtifactInput:
    """Worker artifact plus the governed provenance needed to stitch it."""

    artifact: PartitionArtifact
    source_fingerprint: str
    dependency_ids: tuple[str, ...] = ()
    provenance: Mapping[str, object] = field(default_factory=dict)
    required: bool = True
    crs: str = EPSG_27700
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default="satn-partition-artifact-input/v1")

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, PartitionArtifact):
            raise StitchValidationError("artifact input requires a PartitionArtifact")
        _sha(self.source_fingerprint, "source_fingerprint")
        if self.crs != EPSG_27700:
            raise StitchValidationError("stitch inputs require explicit EPSG:27700")
        if type(self.required) is not bool:
            raise StitchValidationError("required must be a boolean")
        dependencies = _ordered_text(self.dependency_ids, "dependency id")
        for dependency in dependencies:
            _sha(dependency, "dependency id")
        object.__setattr__(self, "dependency_ids", dependencies)
        provenance = _freeze(self.provenance)
        if not isinstance(provenance, dict):
            raise StitchValidationError("provenance must be a mapping")
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "artifact_fingerprint": self.artifact.fingerprint,
            "partition_cell": self.artifact.partition.cell,
            "source_fingerprint": self.source_fingerprint,
            "dependency_ids": list(self.dependency_ids),
            "provenance": self.provenance,
            "required": self.required,
            "crs": self.crs,
        }


@dataclass(frozen=True, slots=True)
class BoundaryObligation:
    """A global obligation whose alternatives may be emitted by many cells."""

    obligation_id: str
    endpoint_ids: tuple[str, str]
    candidate_ids: tuple[str, ...] = ()
    partition_cells: tuple[str, ...] = ()
    required: bool = True
    alternative_groups: tuple[tuple[str, ...], ...] = ()
    permitted_directions: tuple[str, ...] = ()
    provenance: Mapping[str, object] = field(default_factory=dict)
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default="satn-boundary-obligation/v1")

    def __post_init__(self) -> None:
        _text(self.obligation_id, "obligation_id")
        endpoints_values = _iterable_values(self.endpoint_ids, "endpoint_ids")
        if len(endpoints_values) != 2:
            raise StitchValidationError("obligation requires exactly two endpoint identifiers")
        endpoints = tuple(_text(value, "endpoint id") for value in endpoints_values)
        object.__setattr__(self, "endpoint_ids", endpoints)
        candidates = _ordered_text(self.candidate_ids, "candidate id")
        object.__setattr__(self, "candidate_ids", candidates)
        cells = _partition_cells(self.partition_cells)
        object.__setattr__(self, "partition_cells", cells)
        if type(self.required) is not bool:
            raise StitchValidationError("obligation required must be a boolean")
        groups: list[tuple[str, ...]] = []
        for group in _iterable_values(self.alternative_groups, "alternative_groups"):
            values = _ordered_text(group, "alternative candidate id")
            if not set(values).issubset(candidates):
                raise StitchValidationError("alternative candidate is absent from obligation")
            if values and values not in groups:
                groups.append(values)
        object.__setattr__(self, "alternative_groups", tuple(sorted(groups)))
        directions = _ordered_text(self.permitted_directions, "permitted direction")
        object.__setattr__(self, "permitted_directions", directions)
        provenance = _freeze(self.provenance)
        if not isinstance(provenance, dict):
            raise StitchValidationError("obligation provenance must be a mapping")
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "obligation_id": self.obligation_id,
            "endpoint_ids": list(self.endpoint_ids),
            "candidate_ids": list(self.candidate_ids),
            "partition_cells": list(self.partition_cells),
            "required": self.required,
            "alternative_groups": [list(group) for group in self.alternative_groups],
            "permitted_directions": list(self.permitted_directions),
            "provenance": self.provenance,
        }


GlobalObligation = BoundaryObligation


@dataclass(frozen=True, slots=True)
class EvidenceRequest:
    """A deterministic request attached to an explicit stitch gap."""

    kind: str
    subject_ids: tuple[str, ...]
    message: str
    partition_cells: tuple[str, ...] = ()
    request_id: str = field(init=False)
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default=EVIDENCE_REQUEST_CONTRACT)

    def __post_init__(self) -> None:
        _text(self.kind, "evidence request kind")
        ids = _ordered_text(self.subject_ids, "evidence subject id")
        _text(self.message, "evidence request message")
        cells = _partition_cells(self.partition_cells)
        object.__setattr__(self, "subject_ids", ids)
        object.__setattr__(self, "partition_cells", cells)
        digest = content_fingerprint(self.canonical_payload())
        object.__setattr__(self, "fingerprint", digest)
        object.__setattr__(self, "request_id", f"evidence-request-{digest[:16]}")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "kind": self.kind,
            "subject_ids": list(self.subject_ids),
            "message": self.message,
            "partition_cells": list(self.partition_cells),
        }


@dataclass(frozen=True, slots=True)
class GlobalObligationResolution:
    """The canonical global candidate roster and deterministic reference choice."""

    obligation_id: str
    candidate_ids: tuple[str, ...]
    selected_candidate_id: str | None
    missing_candidate_ids: tuple[str, ...] = ()
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default="satn-global-obligation-resolution/v1")

    def __post_init__(self) -> None:
        _text(self.obligation_id, "obligation_id")
        candidates = _ordered_text(self.candidate_ids, "candidate id")
        missing = _ordered_text(self.missing_candidate_ids, "missing candidate id")
        if self.selected_candidate_id is not None:
            _text(self.selected_candidate_id, "selected candidate id")
            if self.selected_candidate_id not in candidates:
                raise StitchValidationError("selected candidate is absent from resolution")
        object.__setattr__(self, "candidate_ids", candidates)
        object.__setattr__(self, "missing_candidate_ids", missing)
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "obligation_id": self.obligation_id,
            "candidate_ids": list(self.candidate_ids),
            "selected_candidate_id": self.selected_candidate_id,
            "missing_candidate_ids": list(self.missing_candidate_ids),
        }


@dataclass(frozen=True, slots=True)
class HaloExtensionArtifact:
    """A targeted, semantic request to extend one partition's halo only."""

    partition_cell: str
    available_radius_m: float
    required_radius_m: float
    request_id: str
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default=HALO_EXTENSION_CONTRACT)

    def __post_init__(self) -> None:
        cells = _partition_cells((self.partition_cell,))
        object.__setattr__(self, "partition_cell", cells[0])
        if (
            isinstance(self.available_radius_m, bool)
            or not isinstance(self.available_radius_m, (int, float))
            or isinstance(self.required_radius_m, bool)
            or not isinstance(self.required_radius_m, (int, float))
            or not math.isfinite(float(self.available_radius_m))
            or not math.isfinite(float(self.required_radius_m))
        ):
            raise StitchValidationError("halo extension radii must be finite")
        if self.available_radius_m < 0 or self.required_radius_m <= self.available_radius_m:
            raise StitchValidationError("halo extension requires a larger positive radius")
        _text(self.request_id, "halo request id")
        object.__setattr__(self, "available_radius_m", float(self.available_radius_m))
        object.__setattr__(self, "required_radius_m", float(self.required_radius_m))
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "partition_cell": self.partition_cell,
            "available_radius_m": self.available_radius_m,
            "required_radius_m": self.required_radius_m,
            "request_id": self.request_id,
        }


@dataclass(frozen=True, slots=True)
class PartitionStitchResult:
    """Immutable canonical output of :func:`deterministic_partition_stitch`."""

    partition_inputs: tuple[PartitionArtifactInput, ...]
    owned_fragments: tuple[OwnedFeatureFragment, ...] = ()
    halo_references: tuple[HaloReference, ...] = ()
    portals: tuple[BoundaryPortal, ...] = ()
    candidate_fragments: tuple[CandidateFragment, ...] = ()
    diagnostics: tuple[object, ...] = ()
    gaps: tuple[PartitionGap, ...] = ()
    evidence_requests: tuple[EvidenceRequest, ...] = ()
    extension_artifacts: tuple[HaloExtensionArtifact, ...] = ()
    obligations: tuple[BoundaryObligation, ...] = ()
    resolutions: tuple[GlobalObligationResolution, ...] = ()
    status: Literal["complete", "complete-with-gaps"] = "complete"
    fingerprint: str = field(init=False)
    contract: str = field(init=False, default=STITCH_CONTRACT)

    def __post_init__(self) -> None:
        input_values = _iterable_values(self.partition_inputs, "partition_inputs")
        if not all(isinstance(value, PartitionArtifactInput) for value in input_values):
            raise StitchValidationError(
                "partition_inputs must contain PartitionArtifactInput values"
            )
        inputs = tuple(
            sorted(input_values, key=lambda value: value.artifact.partition.cell)
        )
        object.__setattr__(self, "partition_inputs", inputs)
        for name in (
            "owned_fragments",
            "halo_references",
            "portals",
            "candidate_fragments",
            "diagnostics",
            "gaps",
            "evidence_requests",
            "extension_artifacts",
            "obligations",
            "resolutions",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        for diagnostic in self.diagnostics:
            if not hasattr(diagnostic, "canonical_payload") or not callable(
                diagnostic.canonical_payload
            ) or not isinstance(getattr(diagnostic, "fingerprint", None), str):
                raise StitchValidationError("unsupported partition diagnostic")
        if self.status not in ("complete", "complete-with-gaps"):
            raise StitchValidationError("invalid stitch status")
        object.__setattr__(self, "fingerprint", content_fingerprint(self.canonical_payload()))

    @property
    def artifact_identity(self) -> str:
        return self.fingerprint

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "partition_inputs": [item.canonical_payload() for item in self.partition_inputs],
            "owned_fragments": [item.canonical_payload() for item in self.owned_fragments],
            "halo_references": [item.canonical_payload() for item in self.halo_references],
            "portals": [item.canonical_payload() for item in self.portals],
            "candidate_fragments": [item.canonical_payload() for item in self.candidate_fragments],
            "diagnostics": [
                item.canonical_payload() if hasattr(item, "canonical_payload") else str(item)
                for item in self.diagnostics
            ],
            "gaps": [item.canonical_payload() for item in self.gaps],
            "evidence_requests": [item.canonical_payload() for item in self.evidence_requests],
            "extension_artifacts": [item.canonical_payload() for item in self.extension_artifacts],
            "obligations": [item.canonical_payload() for item in self.obligations],
            "resolutions": [item.canonical_payload() for item in self.resolutions],
            "status": self.status,
        }


def _as_input(value: PartitionArtifact | PartitionArtifactInput) -> PartitionArtifactInput:
    if isinstance(value, PartitionArtifactInput):
        return value
    if isinstance(value, PartitionArtifact):
        # Bare artifacts remain convenient for local callers; the artifact's immutable
        # identity is used as a conservative provenance token rather than guessing a source.
        return PartitionArtifactInput(
            value,
            value.fingerprint,
            provenance={"artifact_fingerprint": value.fingerprint},
        )
    raise StitchValidationError("stitch inputs must contain PartitionArtifact values")


def _request_gap(
    kind: str,
    message: str,
    *,
    subject_ids: Iterable[str],
    partition_cells: Iterable[str] = (),
) -> tuple[PartitionGap, EvidenceRequest]:
    cells = tuple(partition_cells)
    request = EvidenceRequest(kind, tuple(subject_ids), message, cells)
    return (
        PartitionGap(
            kind,
            message,
            cells,
            evidence_request_id=request.request_id,
        ),
        request,
    )


def _portal_key(portal: BoundaryPortal) -> tuple[object, ...]:
    return (
        portal.kind,
        portal.left_cell,
        portal.right_cell,
        portal.node_id,
        portal.node_coordinate,
        portal.intersection_coordinate,
        portal.feature_id,
    )


def deterministic_partition_stitch(
    inputs: Iterable[PartitionArtifact | PartitionArtifactInput],
    *,
    required_partitions: Iterable[CompilationPartition | str] = (),
    required_dependency_ids: Iterable[str] = (),
    available_dependency_ids: Iterable[str] = (),
    obligations: Iterable[BoundaryObligation] = (),
) -> PartitionStitchResult:
    """Validate, merge and fingerprint partition artifacts in canonical order.

    Validation of required partitions, source/provenance and dependency closure is
    completed before output records are assembled.  Optional conflicts are retained
    as explicit gaps and evidence requests; no geometry is guessed or silently
    dropped.
    """

    normalised = tuple(
        _as_input(value) for value in _iterable_values(inputs, "stitch inputs")
    )
    expected_cells = set(_partition_cells(required_partitions))
    required_dependencies = set(_ordered_text(required_dependency_ids, "required dependency id"))
    available_dependencies = set(_ordered_text(available_dependency_ids, "available dependency id"))
    for dependency in required_dependencies | available_dependencies:
        _sha(dependency, "dependency id")
    # All envelope validation happens here, before any partial result lists exist.
    by_cell: dict[str, PartitionArtifactInput] = {}
    duplicate_inputs: dict[str, list[PartitionArtifactInput]] = {}
    for item in normalised:
        cell = item.artifact.partition.cell
        duplicate_inputs.setdefault(cell, []).append(item)
        existing = by_cell.get(cell)
        if existing is None or item.fingerprint < existing.fingerprint:
            by_cell[cell] = item
    if expected_cells - set(by_cell):
        missing = ", ".join(sorted(expected_cells - set(by_cell)))
        raise MissingRequiredInputError(f"required partition input is missing: {missing}")
    dependency_union = {dependency for item in normalised for dependency in item.dependency_ids}
    if required_dependencies - dependency_union:
        missing = ", ".join(sorted(required_dependencies - dependency_union))
        raise StitchValidationError(f"dependency closure is incomplete: {missing}")
    for item in normalised:
        if required_dependencies - set(item.dependency_ids):
            missing = ", ".join(sorted(required_dependencies - set(item.dependency_ids)))
            raise StitchValidationError(
                f"dependency closure is incomplete for {item.artifact.partition.cell}: {missing}"
            )
    if available_dependencies and not dependency_union.issubset(available_dependencies):
        unknown = ", ".join(sorted(dependency_union - available_dependencies))
        raise StitchValidationError(f"dependency closure contains unavailable ids: {unknown}")

    inputs_by_cell = tuple(by_cell[cell] for cell in sorted(by_cell))
    ordered_inputs = tuple(
        sorted(normalised, key=lambda value: (value.artifact.partition.cell, value.fingerprint))
    )
    cells = tuple(CompilationPartition(cell) for cell in sorted(by_cell))
    cell_ids = tuple(item.cell for item in cells)
    gaps: dict[str, PartitionGap] = {}
    requests: dict[str, EvidenceRequest] = {}
    for cell, items in duplicate_inputs.items():
        if len(items) <= 1 or len({item.fingerprint for item in items}) == 1:
            continue
        kind = (
            "conflicting-partition-input"
            if len({item.artifact.fingerprint for item in items}) == 1
            else "conflicting-partition-artifact"
        )
        gap, request = _request_gap(
            kind,
            f"Partition {cell} has conflicting worker artifact inputs",
            subject_ids=(cell,),
            partition_cells=(cell,),
        )
        gaps[gap.fingerprint] = gap
        requests[request.request_id] = request

    owned_by_id: dict[str, list[OwnedFeatureFragment]] = {}
    reference_groups: dict[tuple[str, str, str], list[HaloReference]] = {}
    portal_groups: dict[tuple[object, ...], list[BoundaryPortal]] = {}
    candidate_groups: dict[str, list[CandidateFragment]] = {}
    diagnostics: dict[str, object] = {}
    expected_owners: dict[str, str] = {}
    for item in ordered_inputs:
        artifact = item.artifact
        for fragment in artifact.owned_fragments:
            if fragment.owner_cell not in cell_ids:
                raise StitchValidationError(
                    f"owned feature {fragment.feature_id} names an unknown owner cell"
                )
            try:
                expected_owner = deterministic_feature_owner(fragment.geometry, cells)
            except (TypeError, ValueError) as error:
                raise StitchValidationError(
                    f"cannot validate ownership for feature {fragment.feature_id}"
                ) from error
            expected_owners[fragment.feature_id] = expected_owner
            owned_by_id.setdefault(fragment.feature_id, []).append(fragment)
        for reference in artifact.halo_references:
            if reference.owner_cell not in cell_ids:
                raise StitchValidationError(
                    f"halo reference {reference.feature_id} names an unknown owner cell"
                )
            reference_groups.setdefault(
                (reference.feature_id, reference.owner_cell, reference.source_cell), []
            ).append(reference)
        for portal in artifact.portals:
            if portal.left_cell not in cell_ids or portal.right_cell not in cell_ids:
                raise StitchValidationError("portal references a partition absent from the stitch")
            if any(not direction for direction in portal.permitted_directions):
                raise StitchValidationError("portal permitted direction cannot be empty")
            incident_ids = set(portal.incident_feature_ids)
            if portal.permitted_directions and not incident_ids:
                raise StitchValidationError(
                    "portal permitted directions require incident feature identifiers"
                )
            if any(
                direction.split(":", 1)[0] not in incident_ids
                for direction in portal.permitted_directions
            ):
                raise StitchValidationError(
                    "portal permitted direction does not identify an incident feature"
                )
            if portal.provenance.get("feature_fingerprint") is not None:
                _sha(portal.provenance["feature_fingerprint"], "portal feature fingerprint")
            portal_groups.setdefault(_portal_key(portal), []).append(portal)
        for candidate in artifact.candidate_fragments:
            candidate_groups.setdefault(candidate.candidate_id, []).append(candidate)
        for diagnostic in artifact.diagnostics:
            if not hasattr(diagnostic, "canonical_payload") or not callable(
                diagnostic.canonical_payload
            ):
                raise StitchValidationError("unsupported partition diagnostic")
            fingerprint = getattr(diagnostic, "fingerprint", None)
            if not isinstance(fingerprint, str):
                raise StitchValidationError("partition diagnostic requires a fingerprint")
            diagnostics[fingerprint] = diagnostic
        for gap in artifact.gaps:
            gaps[gap.fingerprint] = gap

    owned: dict[str, OwnedFeatureFragment] = {}
    for feature_id, values in owned_by_id.items():
        first = min(values, key=lambda value: value.fingerprint)
        if len({value.content_fingerprint for value in values}) > 1:
            gap, request = _request_gap(
                "conflicting-feature",
                f"Feature {feature_id} has conflicting owned content",
                subject_ids=(feature_id,),
                partition_cells=(value.owner_cell for value in values),
            )
            gaps[gap.fingerprint] = gap
            requests[request.request_id] = request
        else:
            expected_owner = expected_owners[feature_id]
            if len(values) == 1 and first.owner_cell != expected_owner:
                raise StitchValidationError(
                    f"feature {feature_id} is owned by {first.owner_cell}; "
                    f"deterministic owner is {expected_owner}"
                )
            # A duplicate may have been produced by a worker that only knew its local
            # core.  Rebind it to the global deterministic owner after content agrees.
            owned[feature_id] = OwnedFeatureFragment(
                feature_id,
                expected_owner,
                first.geometry,
                first.content_fingerprint,
            )

    references_by_id: dict[tuple[str, str, str], HaloReference] = {}
    for key, values in reference_groups.items():
        first = min(values, key=lambda value: value.fingerprint)
        references_by_id[key] = first
        if len({value.content_fingerprint for value in values}) > 1:
            gap, request = _request_gap(
                "conflicting-halo-reference",
                "Halo references disagree on feature content or provenance",
                subject_ids=(key[0],),
                partition_cells=(key[1], key[2]),
            )
            gaps[gap.fingerprint] = gap
            requests[request.request_id] = request

    portals: list[BoundaryPortal] = []
    for key, values in portal_groups.items():
        first = min(values, key=lambda value: value.fingerprint)
        portals.append(first)
        if len({value.fingerprint for value in values}) > 1:
            gap, request = _request_gap(
                "conflicting-portal",
                "Boundary portal records disagree on direction, geometry or provenance",
                subject_ids=(str(key),),
                partition_cells=(value.left_cell for value in values)
                + (value.right_cell for value in values),
            )
            gaps[gap.fingerprint] = gap
            requests[request.request_id] = request

    candidates: list[CandidateFragment] = []
    for candidate_id, values in candidate_groups.items():
        first = min(values, key=lambda value: value.fingerprint)
        candidates.append(first)
        if len({value.fingerprint for value in values}) > 1:
            gap, request = _request_gap(
                "conflicting-candidate",
                f"Candidate {candidate_id} has conflicting worker content",
                subject_ids=(candidate_id,),
            )
            gaps[gap.fingerprint] = gap
            requests[request.request_id] = request

    candidate_ids = {candidate.candidate_id for candidate in candidates}
    obligation_groups: dict[str, list[BoundaryObligation]] = {}
    for obligation in _iterable_values(obligations, "obligations"):
        if not isinstance(obligation, BoundaryObligation):
            raise StitchValidationError("obligations must contain BoundaryObligation values")
        obligation_groups.setdefault(obligation.obligation_id, []).append(obligation)
    canonical_obligations: list[BoundaryObligation] = []
    resolutions: list[GlobalObligationResolution] = []
    for obligation_id, values in sorted(obligation_groups.items()):
        obligation = min(values, key=lambda value: value.fingerprint)
        canonical_obligations.append(obligation)
        if len({value.fingerprint for value in values}) > 1:
            gap, request = _request_gap(
                "conflicting-obligation",
                f"Obligation {obligation_id} has conflicting worker content",
                subject_ids=(obligation_id,),
                partition_cells=obligation.partition_cells,
            )
            gaps[gap.fingerprint] = gap
            requests[request.request_id] = request
        present = tuple(
            candidate for candidate in obligation.candidate_ids if candidate in candidate_ids
        )
        missing = tuple(
            candidate for candidate in obligation.candidate_ids if candidate not in candidate_ids
        )
        if missing:
            gap, request = _request_gap(
                "missing-candidate",
                f"Obligation {obligation_id} has unavailable candidate alternatives",
                subject_ids=(obligation_id, *missing),
                partition_cells=obligation.partition_cells,
            )
            gaps[gap.fingerprint] = gap
            requests[request.request_id] = request
        selected = present[0] if present else None
        resolutions.append(
            GlobalObligationResolution(
                obligation_id,
                present,
                selected,
                missing,
            )
        )

    # A candidate's feature references are checked after the global owned roster is known.
    known_features = set(owned) | {reference.feature_id for reference in references_by_id.values()}
    # Portal incident/feature references identify physical source features only;
    # candidate IDs belong to the higher-level obligation roster and must not
    # make an otherwise missing asset appear authoritative.
    known_identities = known_features
    for portal in portals:
        referenced_ids = set(portal.incident_feature_ids)
        if portal.feature_id is not None:
            referenced_ids.add(portal.feature_id)
        unknown = tuple(sorted(referenced_ids - known_identities))
        if unknown:
            gap, request = _request_gap(
                "unknown-portal-reference",
                "Boundary portal references an unavailable feature or candidate",
                subject_ids=unknown,
                partition_cells=(portal.left_cell, portal.right_cell),
            )
            gaps[gap.fingerprint] = gap
            requests[request.request_id] = request
        provenance_fingerprint = portal.provenance.get("feature_fingerprint")
        if portal.feature_id is not None and provenance_fingerprint is not None:
            feature = owned.get(portal.feature_id)
            if feature is not None and feature.content_fingerprint != provenance_fingerprint:
                gap, request = _request_gap(
                    "conflicting-portal-provenance",
                    "Boundary portal provenance does not match its feature content",
                    subject_ids=(portal.feature_id,),
                    partition_cells=(portal.left_cell, portal.right_cell),
                )
                gaps[gap.fingerprint] = gap
                requests[request.request_id] = request
    for candidate in candidates:
        missing = tuple(
            feature for feature in candidate.feature_ids if feature not in known_features
        )
        if missing:
            gap, request = _request_gap(
                "missing-feature-reference",
                f"Candidate {candidate.candidate_id} references unavailable features",
                subject_ids=(candidate.candidate_id, *missing),
            )
            gaps[gap.fingerprint] = gap
            requests[request.request_id] = request

    # Preserve every existing optional gap and make halo retries partition-local.
    halo_requirements: dict[str, tuple[float, float]] = {}
    for gap in gaps.values():
        request = gap.halo_request
        if request is not None:
            existing = halo_requirements.get(request.partition_cell)
            if existing is None:
                halo_requirements[request.partition_cell] = (
                    request.available_radius_m,
                    request.required_radius_m,
                )
            else:
                halo_requirements[request.partition_cell] = (
                    min(existing[0], request.available_radius_m),
                    max(existing[1], request.required_radius_m),
                )
    extensions: dict[str, HaloExtensionArtifact] = {}
    for partition_cell, (available_radius, required_radius) in halo_requirements.items():
        request_seed = content_fingerprint(
            {
                "partition_cell": partition_cell,
                "available_radius_m": available_radius,
                "required_radius_m": required_radius,
            }
        )
        extension = HaloExtensionArtifact(
            partition_cell,
            available_radius,
            required_radius,
            f"halo-extension-request-{request_seed[:16]}",
        )
        extensions[partition_cell] = extension

    ordered_gaps = tuple(sorted(gaps.values(), key=lambda value: value.fingerprint))
    return PartitionStitchResult(
        inputs_by_cell,
        tuple(owned[key] for key in sorted(owned)),
        tuple(sorted(references_by_id.values(), key=lambda value: value.fingerprint)),
        tuple(sorted(portals, key=lambda value: value.fingerprint)),
        tuple(sorted(candidates, key=lambda value: value.fingerprint)),
        tuple(diagnostics[key] for key in sorted(diagnostics)),
        ordered_gaps,
        tuple(sorted(requests.values(), key=lambda value: value.request_id)),
        tuple(sorted(extensions.values(), key=lambda value: value.fingerprint)),
        tuple(sorted(canonical_obligations, key=lambda value: value.fingerprint)),
        tuple(sorted(resolutions, key=lambda value: value.fingerprint)),
        "complete-with-gaps" if ordered_gaps else "complete",
    )


stitch_partition_artifacts = deterministic_partition_stitch
deterministic_stitch = deterministic_partition_stitch


__all__ = [
    "BoundaryObligation",
    "EvidenceRequest",
    "GlobalObligation",
    "GlobalObligationResolution",
    "HaloExtensionArtifact",
    "MissingRequiredInputError",
    "PartitionArtifactInput",
    "PartitionStitchResult",
    "StitchError",
    "StitchValidationError",
    "deterministic_partition_stitch",
    "deterministic_stitch",
    "stitch_partition_artifacts",
]
