"""Changed-configuration Scenario Iteration over validated immutable stages.

This module is the small external seam described by ADR-0012.  It does not own
storage, routing, publication paths, queues or mutable ``current`` state.  A
caller supplies the already materialised stage records, a pure scenario
compiler and the existing whole-publication/atomic-writer adapter.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol

from satn.alignment_selection import ScenarioCompilation
from satn.compilation_dependencies import (
    compiler_cache_revision,
    is_compiler_cache_revision,
    validate_compilation_dependency_manifest,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REUSED_STAGES = tuple(range(1, 7))


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in sorted(value.items())})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _sha256(value: str, name: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


def _dependency_identity(value: str, name: str) -> str:
    if _SHA256.fullmatch(value) is None and not is_compiler_cache_revision(value):
        raise ValueError(f"{name} must be lowercase SHA-256 or compiler cache revision")
    return value


@dataclass(frozen=True)
class ScenarioStageRecord:
    """One validated logical materialisation for an ADR-0012 reusable stage."""

    stage: Literal[1, 2, 3, 4, 5, 6]
    contract: str
    input_fingerprint: str
    output_fingerprint: str
    dependency_manifest_sha256: str
    upstream_output_fingerprints: tuple[str, ...] = ()
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    validated: Literal[True] = True

    def __post_init__(self) -> None:
        if self.stage not in _REUSED_STAGES:
            raise ValueError("reusable stage must be numbered 1 through 6")
        if not self.contract or self.contract.strip() != self.contract:
            raise ValueError("stage contract must be canonical")
        _sha256(self.input_fingerprint, "stage input_fingerprint")
        _sha256(self.output_fingerprint, "stage output_fingerprint")
        _dependency_identity(self.dependency_manifest_sha256, "stage dependency manifest")
        upstream = tuple(
            sorted(
                {
                    _sha256(item, "upstream output fingerprint")
                    for item in self.upstream_output_fingerprints
                }
            )
        )
        if self.stage == 1 and upstream:
            raise ValueError("stage 1 cannot name an upstream materialisation")
        if self.stage > 1 and not upstream:
            raise ValueError("reused stages 2 through 6 require upstream lineage")
        diagnostics = _freeze(self.diagnostics)
        if not isinstance(diagnostics, Mapping):
            raise ValueError("stage diagnostics must be a mapping")
        object.__setattr__(self, "upstream_output_fingerprints", upstream)
        object.__setattr__(self, "diagnostics", diagnostics)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "contract": self.contract,
            "input_fingerprint": self.input_fingerprint,
            "output_fingerprint": self.output_fingerprint,
            "dependency_manifest_sha256": self.dependency_manifest_sha256,
            "upstream_output_fingerprints": list(self.upstream_output_fingerprints),
            "diagnostics": _thaw(self.diagnostics),
            "validated": True,
        }


@dataclass(frozen=True)
class ScenarioIterationState:
    """The exact, closed stage 1-6 state against which iteration may run."""

    stages: tuple[ScenarioStageRecord, ...]
    state_fingerprint: str = ""

    contract: str = field(init=False, default="satn-scenario-iteration-state/v1")

    def __post_init__(self) -> None:
        stages = tuple(sorted(self.stages, key=lambda item: item.stage))
        if tuple(item.stage for item in stages) != _REUSED_STAGES:
            raise ValueError("Scenario Iteration requires exactly validated stages 1 through 6")
        dependency_digests = {item.dependency_manifest_sha256 for item in stages}
        if len(dependency_digests) != 1:
            raise ValueError("reused stages name inconsistent dependency manifests")
        prior_outputs: set[str] = set()
        for record in stages:
            if record.stage > 1 and not (set(record.upstream_output_fingerprints) & prior_outputs):
                raise ValueError(f"stage {record.stage} has no lineage to an earlier stage output")
            prior_outputs.add(record.output_fingerprint)
        object.__setattr__(self, "stages", stages)
        expected = _fingerprint(self.canonical_payload())
        if self.state_fingerprint and self.state_fingerprint != expected:
            raise ValueError("Scenario Iteration state fingerprint is stale")
        object.__setattr__(self, "state_fingerprint", expected)

    @property
    def dependency_manifest_sha256(self) -> str:
        return self.stages[0].dependency_manifest_sha256

    @property
    def evidence_state_fingerprint(self) -> str:
        return self.stages[1].output_fingerprint

    @property
    def assembly_fingerprint(self) -> str:
        return self.stages[-1].output_fingerprint

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "stages": [item.canonical_payload() for item in self.stages],
        }


@dataclass(frozen=True)
class ScenarioConfiguration:
    """Immutable data-only choices that may change at scenario stage 7."""

    area_definition_fingerprint: str
    criteria_set_fingerprint: str
    network_selection_profile_fingerprint: str
    reusable_state_fingerprint: str
    dependency_manifest_sha256: str
    publication_configuration_fingerprint: str
    values: Mapping[str, object] = field(default_factory=dict)
    configuration_fingerprint: str = ""

    contract: str = field(init=False, default="satn-scenario-configuration/v1")

    def __post_init__(self) -> None:
        for name in (
            "area_definition_fingerprint",
            "criteria_set_fingerprint",
            "network_selection_profile_fingerprint",
            "reusable_state_fingerprint",
            "dependency_manifest_sha256",
            "publication_configuration_fingerprint",
        ):
            if name == "dependency_manifest_sha256":
                _dependency_identity(getattr(self, name), name)
            else:
                _sha256(getattr(self, name), name)
        values = _freeze(self.values)
        if not isinstance(values, Mapping):
            raise ValueError("Scenario Configuration values must be a mapping")
        object.__setattr__(self, "values", values)
        expected = _fingerprint(self.canonical_payload())
        if self.configuration_fingerprint and self.configuration_fingerprint != expected:
            raise ValueError("Scenario Configuration fingerprint is stale")
        object.__setattr__(self, "configuration_fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "area_definition_fingerprint": self.area_definition_fingerprint,
            "criteria_set_fingerprint": self.criteria_set_fingerprint,
            "network_selection_profile_fingerprint": (self.network_selection_profile_fingerprint),
            "reusable_state_fingerprint": self.reusable_state_fingerprint,
            "dependency_manifest_sha256": self.dependency_manifest_sha256,
            "publication_configuration_fingerprint": (self.publication_configuration_fingerprint),
            "values": _thaw(self.values),
        }


@dataclass(frozen=True)
class AcceptedDecisionLedger:
    """Accepted decisions bound to one configuration and one reusable state."""

    configuration_fingerprint: str
    evidence_state_fingerprint: str
    assembly_fingerprint: str
    decisions: tuple[Mapping[str, object], ...] = ()
    ledger_fingerprint: str = ""

    contract: str = field(init=False, default="satn-accepted-decision-ledger/v1")

    def __post_init__(self) -> None:
        _sha256(self.configuration_fingerprint, "ledger configuration fingerprint")
        _sha256(self.evidence_state_fingerprint, "ledger evidence state fingerprint")
        _sha256(self.assembly_fingerprint, "ledger assembly fingerprint")
        decisions = tuple(_freeze(item) for item in self.decisions)
        if any(not isinstance(item, Mapping) for item in decisions):
            raise ValueError("accepted decisions must be mappings")
        object.__setattr__(self, "decisions", decisions)
        expected = _fingerprint(self.canonical_payload())
        if self.ledger_fingerprint and self.ledger_fingerprint != expected:
            raise ValueError("accepted-decision ledger fingerprint is stale")
        object.__setattr__(self, "ledger_fingerprint", expected)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "configuration_fingerprint": self.configuration_fingerprint,
            "evidence_state_fingerprint": self.evidence_state_fingerprint,
            "assembly_fingerprint": self.assembly_fingerprint,
            "decisions": [_thaw(item) for item in self.decisions],
        }


@dataclass(frozen=True)
class AtomicPublicationReceipt:
    """Proof returned only after whole validation and atomic replacement."""

    publication_fingerprint: str
    artifact_digests: Mapping[str, str]
    whole_publication_validated: Literal[True]
    atomic_replace_completed: Literal[True]

    contract: str = field(init=False, default="satn-atomic-publication-receipt/v1")

    def __post_init__(self) -> None:
        _sha256(self.publication_fingerprint, "publication_fingerprint")
        if (
            self.whole_publication_validated is not True
            or self.atomic_replace_completed is not True
        ):
            raise ValueError("Scenario Iteration requires whole validation and atomic publication")
        digests = {
            str(name): _sha256(digest, f"artifact digest {name}")
            for name, digest in sorted(self.artifact_digests.items())
            if name and name.strip() == name
        }
        if not digests or len(digests) != len(self.artifact_digests):
            raise ValueError("publication receipt requires canonical artifact digests")
        object.__setattr__(self, "artifact_digests", MappingProxyType(digests))


@dataclass(frozen=True)
class ScenarioStageDiagnostic:
    stage: int
    disposition: Literal["validated-hit", "recomputed"]
    input_fingerprint: str
    output_fingerprint: str
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.stage not in range(1, 9):
            raise ValueError("diagnostic stage must be numbered 1 through 8")
        _sha256(self.input_fingerprint, "diagnostic input fingerprint")
        _sha256(self.output_fingerprint, "diagnostic output fingerprint")
        diagnostics = _freeze(self.diagnostics)
        if not isinstance(diagnostics, Mapping):
            raise ValueError("stage diagnostics must be a mapping")
        object.__setattr__(self, "diagnostics", diagnostics)


@dataclass(frozen=True)
class ScenarioIterationResult:
    scenario: ScenarioCompilation
    dependency_manifest: Mapping[str, object]
    stage_diagnostics: tuple[ScenarioStageDiagnostic, ...]
    publication: AtomicPublicationReceipt
    iteration_fingerprint: str

    contract: str = field(init=False, default="satn-scenario-iteration/v1")

    def __post_init__(self) -> None:
        if tuple(item.stage for item in self.stage_diagnostics) != tuple(range(1, 9)):
            raise ValueError("Scenario Iteration diagnostics must cover stages 1 through 8")
        _sha256(self.iteration_fingerprint, "iteration_fingerprint")
        manifest = _freeze(self.dependency_manifest)
        if not isinstance(manifest, Mapping):
            raise ValueError("dependency manifest must be a mapping")
        object.__setattr__(self, "dependency_manifest", manifest)


class ScenarioCompiler(Protocol):
    def __call__(
        self,
        configuration: ScenarioConfiguration,
        ledger: AcceptedDecisionLedger,
        assembly_fingerprint: str,
        dependency_manifest: Mapping[str, object],
    ) -> ScenarioCompilation: ...


class AtomicPublisher(Protocol):
    def __call__(
        self,
        scenario: ScenarioCompilation,
        dependency_manifest: Mapping[str, object],
    ) -> AtomicPublicationReceipt: ...


def iterate_scenario(
    configuration: ScenarioConfiguration,
    ledger: AcceptedDecisionLedger,
    reusable_state: ScenarioIterationState,
    dependency_manifest: Mapping[str, object],
    *,
    compile_scenario: ScenarioCompiler | Callable[..., ScenarioCompilation],
    publish_atomic: AtomicPublisher | Callable[..., AtomicPublicationReceipt],
) -> ScenarioIterationResult:
    """Recompute stages 7-8 only after stages 1-6 validate as exact hits."""

    manifest = validate_compilation_dependency_manifest(dependency_manifest)
    manifest_identity = compiler_cache_revision(manifest)
    if (
        configuration.reusable_state_fingerprint != reusable_state.state_fingerprint
        or configuration.dependency_manifest_sha256 != manifest_identity
        or reusable_state.dependency_manifest_sha256 != manifest_identity
    ):
        raise ValueError("Scenario Iteration reusable state is stale")
    if (
        ledger.configuration_fingerprint != configuration.configuration_fingerprint
        or ledger.evidence_state_fingerprint != reusable_state.evidence_state_fingerprint
        or ledger.assembly_fingerprint != reusable_state.assembly_fingerprint
    ):
        raise ValueError("accepted-decision ledger is stale for Scenario Iteration")

    diagnostics = [
        ScenarioStageDiagnostic(
            stage=record.stage,
            disposition="validated-hit",
            input_fingerprint=record.input_fingerprint,
            output_fingerprint=record.output_fingerprint,
            diagnostics={
                **dict(record.diagnostics),
                "evidence_refresh_performed": False,
                "routing_recomputed": False,
            },
        )
        for record in reusable_state.stages
    ]
    scenario = compile_scenario(
        configuration,
        ledger,
        reusable_state.assembly_fingerprint,
        manifest,
    )
    if not isinstance(scenario, ScenarioCompilation):
        raise ValueError("Scenario compiler did not return a Scenario Compilation")
    scenario = ScenarioCompilation.model_validate(scenario.model_dump(mode="python"))
    scenario_input = _fingerprint(
        {
            "configuration_fingerprint": configuration.configuration_fingerprint,
            "ledger_fingerprint": ledger.ledger_fingerprint,
            "assembly_fingerprint": reusable_state.assembly_fingerprint,
            "dependency_manifest_sha256": manifest_identity,
        }
    )
    diagnostics.append(
        ScenarioStageDiagnostic(
            stage=7,
            disposition="recomputed",
            input_fingerprint=scenario_input,
            output_fingerprint=scenario.scenario_fingerprint,
            diagnostics={
                "scenario_publishable": scenario.publishable,
                "decision_mode": scenario.decision_record.mode.value,
                "selection_count": len(scenario.selections),
            },
        )
    )
    if not scenario.publishable:
        raise ValueError("Scenario Iteration produced an unpublishable Scenario Compilation")

    receipt = publish_atomic(scenario, manifest)
    if not isinstance(receipt, AtomicPublicationReceipt):
        raise ValueError("atomic publisher did not return a publication receipt")
    publication_input = _fingerprint(
        {
            "scenario_fingerprint": scenario.scenario_fingerprint,
            "publication_configuration_fingerprint": (
                configuration.publication_configuration_fingerprint
            ),
            "dependency_manifest_sha256": manifest_identity,
        }
    )
    diagnostics.append(
        ScenarioStageDiagnostic(
            stage=8,
            disposition="recomputed",
            input_fingerprint=publication_input,
            output_fingerprint=receipt.publication_fingerprint,
            diagnostics={
                "whole_publication_validated": True,
                "atomic_replace_completed": True,
                "artifact_count": len(receipt.artifact_digests),
            },
        )
    )
    iteration_fingerprint = _fingerprint(
        {
            "contract": "satn-scenario-iteration/v1",
            "scenario_fingerprint": scenario.scenario_fingerprint,
            "dependency_manifest_sha256": manifest_identity,
            "stage_outputs": [item.output_fingerprint for item in diagnostics],
            "publication_fingerprint": receipt.publication_fingerprint,
        }
    )
    return ScenarioIterationResult(
        scenario=scenario,
        dependency_manifest=manifest,
        stage_diagnostics=tuple(diagnostics),
        publication=receipt,
        iteration_fingerprint=iteration_fingerprint,
    )
