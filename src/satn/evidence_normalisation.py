"""Canonical observation envelopes produced by governed provider adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from satn.evidence_contracts import (
    EvidencePartitionKey,
    IngestionContract,
    SourceExport,
    canonical_evidence_json,
    evidence_fingerprint,
)

_OBSERVATION_STATES = {
    "available",
    "missing",
    "stale",
    "conflicting",
    "unmatched",
}


def _canonical_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be nonempty canonical text")
    return value


def _canonical_text_set(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    canonical = tuple(_canonical_text(value, name) for value in values)
    if len(canonical) != len(set(canonical)):
        raise ValueError(f"{name} cannot contain duplicates")
    return tuple(sorted(canonical))


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in sorted(value.items())}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _canonical_value(value: Mapping[str, object]) -> Mapping[str, object]:
    canonical_evidence_json(value)
    frozen = _freeze_json(value)
    assert isinstance(frozen, Mapping)
    return frozen


def _canonical_cells(
    cells: tuple[str, ...],
    *,
    source_layer: str,
    partition_scheme: str,
) -> tuple[str, ...]:
    canonical = _canonical_text_set(cells, "coverage cells")
    if not canonical:
        raise ValueError("coverage cells cannot be empty")
    for cell in canonical:
        EvidencePartitionKey(
            source_layer=source_layer,
            partition_scheme=partition_scheme,
            cell=cell,
        )
    return canonical


@dataclass(frozen=True)
class EvidenceObservationDraft:
    """Provider-adapter output awaiting a governed source and contract binding."""

    observation_id: str
    subject_id: str
    claim: str
    value: Mapping[str, object]
    coverage_cells: tuple[str, ...]
    observed_at: str
    limitations: tuple[str, ...] = ()
    state: str = "available"

    def __post_init__(self) -> None:
        for name in ("observation_id", "subject_id", "claim", "observed_at"):
            _canonical_text(getattr(self, name), name)
        if self.state not in _OBSERVATION_STATES:
            raise ValueError(
                "observation state must be available, missing, stale, conflicting, or unmatched"
            )
        object.__setattr__(self, "value", _canonical_value(self.value))
        object.__setattr__(
            self,
            "limitations",
            _canonical_text_set(self.limitations, "limitations"),
        )


@dataclass(frozen=True)
class EvidenceObservation:
    """One claim-specific observation retaining its complete governed source binding."""

    draft: EvidenceObservationDraft
    source_export: SourceExport
    ingestion_contract: IngestionContract
    coverage_cells: tuple[str, ...]
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-evidence-observation/v1")

    def __post_init__(self) -> None:
        if not isinstance(self.draft, EvidenceObservationDraft):
            raise ValueError("evidence observation requires an EvidenceObservationDraft")
        if not isinstance(self.source_export, SourceExport):
            raise ValueError("evidence observation requires a SourceExport")
        if not isinstance(self.ingestion_contract, IngestionContract):
            raise ValueError("evidence observation requires an IngestionContract")
        expected_layer = f"{self.source_export.source_family}/{self.source_export.layer}"
        if expected_layer != self.ingestion_contract.source_layer:
            raise ValueError("source export and ingestion contract source layers differ")
        if self.source_export.declared_crs != self.ingestion_contract.crs_transform["source_crs"]:
            raise ValueError("source export and ingestion contract source CRS differ")
        object.__setattr__(
            self,
            "coverage_cells",
            _canonical_cells(
                self.coverage_cells,
                source_layer=self.ingestion_contract.source_layer,
                partition_scheme=self.ingestion_contract.partition_scheme,
            ),
        )
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("evidence observation fingerprint is stale or collides")
        object.__setattr__(self, "fingerprint", expected)

    @property
    def observation_id(self) -> str:
        return self.draft.observation_id

    @property
    def subject_id(self) -> str:
        return self.draft.subject_id

    @property
    def claim(self) -> str:
        return self.draft.claim

    @property
    def value(self) -> Mapping[str, object]:
        return self.draft.value

    @property
    def observed_at(self) -> str:
        return self.draft.observed_at

    @property
    def limitations(self) -> tuple[str, ...]:
        return self.draft.limitations

    @property
    def state(self) -> str:
        return self.draft.state

    @property
    def source_export_fingerprint(self) -> str:
        return self.source_export.fingerprint

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "observation_id": self.observation_id,
            "subject_id": self.subject_id,
            "claim": self.claim,
            "value": dict(self.value),
            "state": self.state,
            "observed_at": self.observed_at,
            "coverage_cells": list(self.coverage_cells),
            "limitations": list(self.limitations),
            "source_export": self.source_export.canonical_payload(),
            "source_export_fingerprint": self.source_export.fingerprint,
            "ingestion_contract_fingerprint": self.ingestion_contract.fingerprint,
        }


def normalise_source_export(
    *,
    source_export: SourceExport,
    ingestion_contract: IngestionContract,
    drafts: tuple[EvidenceObservationDraft, ...],
) -> tuple[EvidenceObservation, ...]:
    """Bind adapter drafts to one immutable export and normalisation contract."""

    observations = tuple(
        EvidenceObservation(
            draft=draft,
            source_export=source_export,
            ingestion_contract=ingestion_contract,
            coverage_cells=draft.coverage_cells,
        )
        for draft in drafts
    )
    identifiers = [item.observation_id for item in observations]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("one source export cannot repeat an observation_id")
    return tuple(sorted(observations, key=lambda item: item.fingerprint))


@dataclass(frozen=True)
class EvidenceObservationCollection:
    """An immutable, input-order-independent collection that retains competing claims."""

    observations: tuple[EvidenceObservation, ...]
    fingerprint: str = ""

    contract: str = field(init=False, default="satn-evidence-observation-collection/v1")

    def __post_init__(self) -> None:
        if not all(isinstance(item, EvidenceObservation) for item in self.observations):
            raise ValueError("observation collection requires EvidenceObservation records")
        fingerprints = [item.fingerprint for item in self.observations]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("observation collection cannot contain duplicates")
        object.__setattr__(
            self,
            "observations",
            tuple(sorted(self.observations, key=lambda item: item.fingerprint)),
        )
        expected = evidence_fingerprint(self.canonical_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("observation collection fingerprint is stale or collides")
        object.__setattr__(self, "fingerprint", expected)

    def observations_for(
        self,
        *,
        subject_id: str,
        claim: str,
    ) -> tuple[EvidenceObservation, ...]:
        return tuple(
            observation
            for observation in self.observations
            if observation.subject_id == subject_id and observation.claim == claim
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "observations": [item.canonical_payload() for item in self.observations],
        }
