"""Provider-neutral discovery of governed evidence sources by capability and coverage."""

from __future__ import annotations

from dataclasses import dataclass, field

from satn.evidence_contracts import (
    EvidencePartitionKey,
    evidence_fingerprint,
)


def _canonical_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def _canonical_text_set(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    canonical = tuple(_canonical_text(value, name) for value in values)
    if len(canonical) != len(set(canonical)):
        raise ValueError(f"{name} cannot contain duplicates")
    return tuple(sorted(canonical))


def _canonical_cells(values: tuple[str, ...], partition_scheme: str) -> tuple[str, ...]:
    cells = _canonical_text_set(values, "coverage cells")
    if not cells:
        raise ValueError("coverage cells cannot be empty")
    for cell in cells:
        EvidencePartitionKey(
            source_layer="evidence-source-catalogue/coverage",
            partition_scheme=partition_scheme,
            cell=cell,
        )
    return cells


@dataclass(frozen=True)
class AreaEvidenceScope:
    """One resolved compilation area expressed as stable spatial partitions."""

    area_id: str
    cells: tuple[str, ...]
    partition_scheme: str = "bng-10km/v1"
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-area-evidence-scope/v1")

    def __post_init__(self) -> None:
        _canonical_text(self.area_id, "area_id")
        _canonical_text(self.partition_scheme, "partition_scheme")
        object.__setattr__(
            self,
            "cells",
            _canonical_cells(self.cells, self.partition_scheme),
        )
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("area evidence scope fingerprint is stale or collides")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "area_id": self.area_id,
            "partition_scheme": self.partition_scheme,
            "cells": list(self.cells),
        }


@dataclass(frozen=True)
class EvidenceSourceCatalogueEntry:
    """One governed provider capability and its declared spatial applicability."""

    source_id: str
    source_family: str
    capability: str
    source_layer: str
    publisher: str
    effective_date: str
    licence: str
    coverage_cells: tuple[str, ...]
    acquisition_contract: str
    normalisation_contract: str
    permitted_uses: tuple[str, ...]
    authority_rank: int
    partition_scheme: str = "bng-10km/v1"
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-evidence-source-catalogue-entry/v1")

    def __post_init__(self) -> None:
        for name in (
            "source_id",
            "source_family",
            "capability",
            "source_layer",
            "publisher",
            "effective_date",
            "licence",
            "acquisition_contract",
            "normalisation_contract",
            "partition_scheme",
        ):
            _canonical_text(getattr(self, name), name)
        if not isinstance(self.authority_rank, int) or self.authority_rank < 0:
            raise ValueError("authority_rank must be a non-negative integer")
        object.__setattr__(
            self,
            "coverage_cells",
            _canonical_cells(self.coverage_cells, self.partition_scheme),
        )
        permitted_uses = _canonical_text_set(self.permitted_uses, "permitted uses")
        if not permitted_uses:
            raise ValueError("permitted uses cannot be empty")
        object.__setattr__(self, "permitted_uses", permitted_uses)
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("catalogue entry fingerprint is stale or collides")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "source_id": self.source_id,
            "source_family": self.source_family,
            "capability": self.capability,
            "source_layer": self.source_layer,
            "publisher": self.publisher,
            "effective_date": self.effective_date,
            "licence": self.licence,
            "coverage": {
                "partition_scheme": self.partition_scheme,
                "cells": list(self.coverage_cells),
            },
            "acquisition_contract": self.acquisition_contract,
            "normalisation_contract": self.normalisation_contract,
            "permitted_uses": list(self.permitted_uses),
            "authority_rank": self.authority_rank,
        }


@dataclass(frozen=True)
class ResolvedEvidenceSource:
    """The part of one catalogue entry applicable to an area request."""

    entry: EvidenceSourceCatalogueEntry
    matched_cells: tuple[str, ...]
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-resolved-evidence-source/v1")

    def __post_init__(self) -> None:
        if not isinstance(self.entry, EvidenceSourceCatalogueEntry):
            raise ValueError("resolved evidence source requires a catalogue entry")
        matched = _canonical_text_set(self.matched_cells, "matched cells")
        if not matched or not set(matched) <= set(self.entry.coverage_cells):
            raise ValueError("matched cells must be a nonempty subset of source coverage")
        object.__setattr__(self, "matched_cells", matched)
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("resolved evidence source fingerprint is stale or collides")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "entry_fingerprint": self.entry.fingerprint,
            "matched_cells": list(self.matched_cells),
        }


@dataclass(frozen=True)
class SourceEvidenceRequest:
    """A bounded request for catalogue coverage unavailable to this area."""

    area_id: str
    capability: str
    required_use: str
    partition_scheme: str
    missing_cells: tuple[str, ...]
    reason: str = "source-coverage-unavailable"
    request_id: str = ""

    contract: str = field(init=False, default="satn-source-evidence-request/v1")

    def __post_init__(self) -> None:
        for name in ("area_id", "capability", "required_use", "partition_scheme", "reason"):
            _canonical_text(getattr(self, name), name)
        object.__setattr__(
            self,
            "missing_cells",
            _canonical_cells(self.missing_cells, self.partition_scheme),
        )
        identity = evidence_fingerprint(self._identity_payload())
        expected_id = "source-evidence-" + identity[:20]
        if self.request_id and self.request_id != expected_id:
            raise ValueError("source evidence request ID is stale or collides")
        object.__setattr__(self, "request_id", expected_id)

    def _identity_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "area_id": self.area_id,
            "capability": self.capability,
            "required_use": self.required_use,
            "partition_scheme": self.partition_scheme,
            "missing_cells": list(self.missing_cells),
            "reason": self.reason,
        }

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "request_id": self.request_id,
            "area_id": self.area_id,
            "capability": self.capability,
            "required_use": self.required_use,
            "partition_scheme": self.partition_scheme,
            "missing_cells": list(self.missing_cells),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EvidenceSourceResolution:
    """Deterministic present and missing coverage for one requested capability."""

    area: AreaEvidenceScope
    capability: str
    required_use: str
    matches: tuple[ResolvedEvidenceSource, ...]
    missing_cells: tuple[str, ...]
    state: str
    evidence_requests: tuple[SourceEvidenceRequest, ...] = ()
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-evidence-source-resolution/v1")

    def __post_init__(self) -> None:
        if not isinstance(self.area, AreaEvidenceScope):
            raise ValueError("source resolution requires an AreaEvidenceScope")
        _canonical_text(self.capability, "capability")
        _canonical_text(self.required_use, "required_use")
        if not all(isinstance(item, ResolvedEvidenceSource) for item in self.matches):
            raise ValueError("source resolution matches are invalid")
        matches = tuple(
            sorted(
                self.matches,
                key=lambda item: (
                    item.entry.authority_rank,
                    item.entry.source_id,
                    item.fingerprint,
                ),
            )
        )
        object.__setattr__(self, "matches", matches)
        missing_cells = _canonical_text_set(self.missing_cells, "missing cells")
        for cell in missing_cells:
            EvidencePartitionKey(
                source_layer="evidence-source-catalogue/coverage",
                partition_scheme=self.area.partition_scheme,
                cell=cell,
            )
        covered_cells = {cell for match in matches for cell in match.matched_cells}
        if not covered_cells <= set(self.area.cells):
            raise ValueError("source resolution matches must be within the area scope")
        expected_missing = tuple(sorted(set(self.area.cells) - covered_cells))
        if missing_cells != expected_missing:
            raise ValueError("source resolution missing cells do not match coverage")
        object.__setattr__(self, "missing_cells", missing_cells)
        expected_state = (
            "complete" if not expected_missing else ("partial" if matches else "unavailable")
        )
        if self.state != expected_state:
            raise ValueError("source resolution state does not match coverage")
        if not all(isinstance(item, SourceEvidenceRequest) for item in self.evidence_requests):
            raise ValueError("source resolution evidence_requests are invalid")
        object.__setattr__(
            self,
            "evidence_requests",
            tuple(sorted(self.evidence_requests, key=lambda item: item.request_id)),
        )
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("source resolution fingerprint is stale or collides")
        object.__setattr__(self, "fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "area_fingerprint": self.area.fingerprint,
            "capability": self.capability,
            "required_use": self.required_use,
            "matches": [match.canonical_payload() for match in self.matches],
            "missing_cells": list(self.missing_cells),
            "state": self.state,
            "evidence_requests": [
                request.canonical_payload() for request in self.evidence_requests
            ],
        }


class EvidenceSourceCatalogue:
    """Resolve governed providers without making input order authoritative."""

    def __init__(self, entries: tuple[EvidenceSourceCatalogueEntry, ...]) -> None:
        if not all(isinstance(entry, EvidenceSourceCatalogueEntry) for entry in entries):
            raise ValueError("catalogue entries must be EvidenceSourceCatalogueEntry records")
        identities = [(entry.source_id, entry.capability) for entry in entries]
        if len(identities) != len(set(identities)):
            raise ValueError("catalogue source and capability pairs must be unique")
        self.entries = tuple(
            sorted(
                entries,
                key=lambda entry: (entry.authority_rank, entry.source_id, entry.fingerprint),
            )
        )

    def resolve(
        self,
        *,
        area: AreaEvidenceScope,
        capability: str,
        required_use: str,
    ) -> EvidenceSourceResolution:
        _canonical_text(capability, "capability")
        _canonical_text(required_use, "required_use")
        requested_cells = set(area.cells)
        matches = tuple(
            ResolvedEvidenceSource(
                entry=entry,
                matched_cells=tuple(requested_cells & set(entry.coverage_cells)),
            )
            for entry in self.entries
            if entry.capability == capability
            and entry.partition_scheme == area.partition_scheme
            and required_use in entry.permitted_uses
            and requested_cells & set(entry.coverage_cells)
        )
        covered_cells = {cell for match in matches for cell in match.matched_cells}
        missing_cells = tuple(sorted(requested_cells - covered_cells))
        state = "complete" if not missing_cells else ("partial" if matches else "unavailable")
        evidence_requests = (
            (
                SourceEvidenceRequest(
                    area_id=area.area_id,
                    capability=capability,
                    required_use=required_use,
                    partition_scheme=area.partition_scheme,
                    missing_cells=missing_cells,
                ),
            )
            if missing_cells
            else ()
        )
        return EvidenceSourceResolution(
            area=area,
            capability=capability,
            required_use=required_use,
            matches=matches,
            missing_cells=missing_cells,
            state=state,
            evidence_requests=evidence_requests,
        )
