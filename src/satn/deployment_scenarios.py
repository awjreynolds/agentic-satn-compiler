"""Deployment contracts for clean baselines and named officer scenarios."""

from __future__ import annotations

import json
import re
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from satn.officer_decisions import (
    ActionableHumanInterventionRequest,
    CleanSATNBaseline,
    HumanInterventionRecord,
    HumanInterventionResponse,
    InterventionRequestState,
    NetworkPublicationKind,
    OfficerDecisionLedger,
    OfficerScenarioCompilation,
    apply_officer_decision_ledger,
    import_human_intervention_response,
    parse_canonical_officer_decision_ledger,
    publication_label,
)

_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


class DeploymentClaim(StrEnum):
    DEVELOPMENT_TEST = "development-test"
    PRODUCTION_OFFICER_REVIEW = "production-officer-review"
    FORMAL_ADOPTION = "formal-adoption"


class AuthorityRecordKind(StrEnum):
    DETERMINISTIC_COMPILER = "deterministic-compiler"
    FAKE_AGENT_RUNTIME = "fake-agent-runtime"
    AGENT_RUNTIME = "agent-runtime"
    HUMAN_OFFICER = "human-officer"
    FORMAL_ADOPTION = "formal-adoption"


class CleanBaselineDeployment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["clean-baseline"] = "clean-baseline"
    publication_path: str = Field(default="baseline", pattern=_SLUG.pattern)
    authority_state: Literal[NetworkPublicationKind.GENERATED_BASELINE] = (
        NetworkPublicationKind.GENERATED_BASELINE
    )


class NamedOfficerScenarioDeployment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_SLUG.pattern)
    publication_path: str = Field(pattern=_SLUG.pattern)
    ledger_path: Path
    authority_state: Literal[NetworkPublicationKind.OFFICER_INFORMED_SCENARIO] = (
        NetworkPublicationKind.OFFICER_INFORMED_SCENARIO
    )
    claim: Literal[
        DeploymentClaim.DEVELOPMENT_TEST,
        DeploymentClaim.PRODUCTION_OFFICER_REVIEW,
    ] = DeploymentClaim.DEVELOPMENT_TEST
    route_control_binding: str | None = Field(default=None, pattern=_ID.pattern)


class ReferenceSATNDeployment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["reference-satn"] = "reference-satn"
    publication_path: str = Field(default="reference-satn", pattern=_SLUG.pattern)
    adoption_record_path: Path
    authority_state: Literal[NetworkPublicationKind.REFERENCE_SATN] = (
        NetworkPublicationKind.REFERENCE_SATN
    )
    claim: Literal[DeploymentClaim.FORMAL_ADOPTION] = DeploymentClaim.FORMAL_ADOPTION


class DeploymentScenarioConfiguration(BaseModel):
    """Explicit companion configuration for independently published scenarios."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["satn-deployment-scenarios/v1"] = (
        "satn-deployment-scenarios/v1"
    )
    config_path: Path = Field(exclude=True)
    deployment_id: str = Field(pattern=_SLUG.pattern)
    clean_baseline: CleanBaselineDeployment = Field(default_factory=CleanBaselineDeployment)
    officer_scenarios: tuple[NamedOfficerScenarioDeployment, ...] = ()
    reference_satn: ReferenceSATNDeployment | None = None

    @model_validator(mode="after")
    def validate_publication_paths(self) -> Self:
        scenarios = tuple(sorted(self.officer_scenarios, key=lambda item: item.name))
        names = [item.name for item in scenarios]
        paths = [
            self.clean_baseline.publication_path,
            *(item.publication_path for item in scenarios),
            *(
                (self.reference_satn.publication_path,)
                if self.reference_satn is not None
                else ()
            ),
        ]
        if len(names) != len(set(names)):
            raise ValueError("named officer scenarios must be unique")
        if len(paths) != len(set(paths)):
            raise ValueError("deployment scenario publication paths must be unique")
        object.__setattr__(self, "officer_scenarios", scenarios)
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> Self:
        config_path = Path(path).resolve()
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("deployment scenario configuration must contain a mapping")
        return cls(config_path=config_path, **payload)

    def named_scenario(self, name: str) -> NamedOfficerScenarioDeployment:
        scenario = next(
            (item for item in self.officer_scenarios if item.name == name),
            None,
        )
        if scenario is None:
            raise ValueError(f"unknown named officer scenario: {name}")
        return scenario

    def resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else (self.config_path.parent / path).resolve()


class DeploymentRequestRegister(BaseModel):
    """Canonical inspectable lifecycle state for asynchronous human requests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["satn-deployment-request-register/v1"] = (
        "satn-deployment-request-register/v1"
    )
    records: tuple[HumanInterventionRecord, ...] = ()

    @model_validator(mode="after")
    def canonical_records(self) -> Self:
        records = tuple(sorted(self.records, key=lambda item: item.request.request_id))
        ids = [item.request.request_id for item in records]
        if len(ids) != len(set(ids)):
            raise ValueError("request register contains duplicate request IDs")
        object.__setattr__(self, "records", records)
        return self

    def canonical_json(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


def parse_request_register(value: bytes) -> DeploymentRequestRegister:
    try:
        payload = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("request register is not valid JSON") from error
    register = DeploymentRequestRegister.model_validate(payload)
    if value != register.canonical_json():
        raise ValueError("request register JSON is not canonical")
    return register


class ResponsePacket(BaseModel):
    """Exported request plus its exact finite response boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["satn-human-response-packet/v1"] = (
        "satn-human-response-packet/v1"
    )
    request: ActionableHumanInterventionRequest
    current_state: InterventionRequestState
    offered_option_ids: tuple[str, ...]
    response_contract: Literal["satn-human-intervention-response/v1"] = (
        "satn-human-intervention-response/v1"
    )

    @model_validator(mode="after")
    def bind_offers(self) -> Self:
        expected = tuple(item.option_id for item in self.request.offered_actions)
        if self.offered_option_ids != expected:
            raise ValueError("response packet option IDs differ from the exact request")
        if self.current_state != InterventionRequestState.PENDING:
            raise ValueError("only a pending request can be exported for response")
        return self


def list_actionable_requests(register: DeploymentRequestRegister) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "request_id": record.request.request_id,
            "state": record.state.value,
            "request_fingerprint": record.request.request_fingerprint,
        }
        for record in register.records
    )


def export_response_packet(
    register: DeploymentRequestRegister,
    request_id: str,
) -> ResponsePacket:
    record = next(
        (item for item in register.records if item.request.request_id == request_id),
        None,
    )
    if record is None:
        raise ValueError(f"unknown human intervention request: {request_id}")
    return ResponsePacket(
        request=record.request,
        current_state=record.state,
        offered_option_ids=tuple(
            item.option_id for item in record.request.offered_actions
        ),
    )


def import_response_into_register(
    register: DeploymentRequestRegister,
    response: HumanInterventionResponse,
    ledger: OfficerDecisionLedger,
) -> tuple[DeploymentRequestRegister, OfficerDecisionLedger]:
    record = next(
        (item for item in register.records if item.request.request_id == response.request_id),
        None,
    )
    if record is None:
        raise ValueError("response matches no current request")
    updated_record, updated_ledger = import_human_intervention_response(
        record,
        response,
        ledger,
    )
    return (
        DeploymentRequestRegister(
            records=tuple(
                updated_record if item.request.request_id == response.request_id else item
                for item in register.records
            )
        ),
        updated_ledger,
    )


class DeploymentAuthorityRecord(BaseModel):
    """Machine-readable separation of compiler, agent, officer and adoption authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_state: NetworkPublicationKind
    public_label: str
    authority_record_kind: AuthorityRecordKind
    configured_runtime_kind: AuthorityRecordKind
    configured_runtime_provider: str = Field(min_length=1)
    claim: DeploymentClaim
    officer_ledger_fingerprint: str | None = Field(
        default=None,
        pattern=_SHA256.pattern,
    )
    adoption_record_id: str | None = Field(default=None, pattern=_ID.pattern)

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        if self.public_label != publication_label(self.authority_state):
            raise ValueError("deployment authority public label is misleading")
        if self.authority_state == NetworkPublicationKind.GENERATED_BASELINE:
            if (
                self.authority_record_kind != AuthorityRecordKind.DETERMINISTIC_COMPILER
                or self.officer_ledger_fingerprint is not None
                or self.adoption_record_id is not None
            ):
                raise ValueError("clean baseline authority must remain compiler-only")
        elif self.authority_state == NetworkPublicationKind.OFFICER_INFORMED_SCENARIO:
            if (
                self.authority_record_kind != AuthorityRecordKind.HUMAN_OFFICER
                or self.officer_ledger_fingerprint is None
                or self.adoption_record_id is not None
            ):
                raise ValueError("officer scenario requires only an officer ledger")
        elif (
            self.authority_record_kind != AuthorityRecordKind.FORMAL_ADOPTION
            or self.adoption_record_id is None
        ):
            raise ValueError("Reference SATN authority requires a formal adoption record")
        if self.configured_runtime_provider == "fake" and self.claim != (
            DeploymentClaim.DEVELOPMENT_TEST
        ):
            raise ValueError(
                "fake runtime cannot support a production officer-review or adoption claim"
            )
        if (
            self.configured_runtime_kind == AuthorityRecordKind.FAKE_AGENT_RUNTIME
        ) != (self.configured_runtime_provider == "fake"):
            raise ValueError("configured runtime authority kind is misclassified")
        return self


class ScenarioComparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    baseline_id: str = Field(pattern=_ID.pattern)
    scenario_id: str = Field(pattern=_ID.pattern)
    applied_officer_decision_ids: tuple[str, ...]
    resulting_network_changes: tuple[str, ...]


class DeploymentScenarioPublication(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["satn-deployment-scenario-publication/v1"] = (
        "satn-deployment-scenario-publication/v1"
    )
    deployment_id: str = Field(pattern=_SLUG.pattern)
    scenario_name: str = Field(pattern=_SLUG.pattern)
    publication_path: str = Field(pattern=_SLUG.pattern)
    scenario: OfficerScenarioCompilation
    authority: DeploymentAuthorityRecord
    comparison: ScenarioComparison


class RouteControlScenarioBinding(Protocol):
    """Small #236 integration seam for route-control overlay interpretation."""

    def __call__(
        self,
        baseline: CleanSATNBaseline,
        ledger: OfficerDecisionLedger,
        scenario: OfficerScenarioCompilation,
        binding_id: str,
    ) -> OfficerScenarioCompilation: ...


def _runtime_kind(provider: str) -> AuthorityRecordKind:
    return (
        AuthorityRecordKind.FAKE_AGENT_RUNTIME
        if provider == "fake"
        else AuthorityRecordKind.AGENT_RUNTIME
    )


def compile_clean_baseline_deployment(
    configuration: DeploymentScenarioConfiguration,
    baseline: CleanSATNBaseline,
    *,
    runtime_provider: str,
    effective_on: date,
) -> DeploymentScenarioPublication:
    ledger = OfficerDecisionLedger(
        baseline_fingerprint=baseline.baseline_fingerprint,
        evidence_snapshot_fingerprint=baseline.evidence_snapshot_fingerprint,
        profile_fingerprint=baseline.profile_fingerprint,
    )
    scenario = apply_officer_decision_ledger(
        baseline,
        ledger,
        effective_on=effective_on,
    )
    authority = DeploymentAuthorityRecord(
        authority_state=NetworkPublicationKind.GENERATED_BASELINE,
        public_label=publication_label(NetworkPublicationKind.GENERATED_BASELINE),
        authority_record_kind=AuthorityRecordKind.DETERMINISTIC_COMPILER,
        configured_runtime_kind=_runtime_kind(runtime_provider),
        configured_runtime_provider=runtime_provider,
        claim=DeploymentClaim.DEVELOPMENT_TEST,
    )
    return DeploymentScenarioPublication(
        deployment_id=configuration.deployment_id,
        scenario_name=configuration.clean_baseline.name,
        publication_path=configuration.clean_baseline.publication_path,
        scenario=scenario,
        authority=authority,
        comparison=ScenarioComparison(
            baseline_id=baseline.baseline_id,
            scenario_id=scenario.scenario_id,
            applied_officer_decision_ids=(),
            resulting_network_changes=scenario.baseline_to_scenario_change_summary,
        ),
    )


def compile_named_officer_scenario(
    configuration: DeploymentScenarioConfiguration,
    baseline: CleanSATNBaseline,
    ledger: OfficerDecisionLedger,
    scenario_name: str,
    *,
    runtime_provider: str,
    effective_on: date,
    route_control_binding: RouteControlScenarioBinding | None = None,
) -> DeploymentScenarioPublication:
    deployment = configuration.named_scenario(scenario_name)
    scenario = apply_officer_decision_ledger(
        baseline,
        ledger,
        effective_on=effective_on,
    )
    if not scenario.applied_decision_ids:
        raise ValueError("named officer scenario requires at least one applied decision")
    if deployment.route_control_binding is not None:
        if route_control_binding is None:
            raise ValueError(
                "named scenario requires the configured route-control binding"
            )
        scenario = route_control_binding(
            baseline,
            ledger,
            scenario,
            deployment.route_control_binding,
        )
    authority = DeploymentAuthorityRecord(
        authority_state=NetworkPublicationKind.OFFICER_INFORMED_SCENARIO,
        public_label=publication_label(
            NetworkPublicationKind.OFFICER_INFORMED_SCENARIO
        ),
        authority_record_kind=AuthorityRecordKind.HUMAN_OFFICER,
        configured_runtime_kind=_runtime_kind(runtime_provider),
        configured_runtime_provider=runtime_provider,
        claim=deployment.claim,
        officer_ledger_fingerprint=ledger.ledger_fingerprint,
    )
    return DeploymentScenarioPublication(
        deployment_id=configuration.deployment_id,
        scenario_name=deployment.name,
        publication_path=deployment.publication_path,
        scenario=scenario,
        authority=authority,
        comparison=ScenarioComparison(
            baseline_id=baseline.baseline_id,
            scenario_id=scenario.scenario_id,
            applied_officer_decision_ids=scenario.applied_decision_ids,
            resulting_network_changes=(
                scenario.baseline_to_scenario_change_summary
            ),
        ),
    )


def load_canonical_ledger(path: Path) -> OfficerDecisionLedger:
    return parse_canonical_officer_decision_ledger(path.read_bytes())
