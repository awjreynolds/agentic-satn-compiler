"""Governed, opt-in desktop imagery observation contracts.

The workflow in this module is deliberately separate from baseline compilation.
It can commission only an explicitly approved provider for one bounded request,
and its output is evidence for human review rather than executable network state.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from satn.evidence_contracts import evidence_fingerprint

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256 = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
]
Identifier = Annotated[
    NonBlankText,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]


class ImageryKind(StrEnum):
    STREET_LEVEL = "street-level"
    AERIAL = "aerial-or-satellite"
    MANUALLY_SUPPLIED = "manually-supplied-local"


class ImageryAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class ObservationCategory(StrEnum):
    VISIBLE = "visible"
    NOT_VISIBLE = "not-visible"
    AMBIGUOUS = "ambiguous"
    OBSCURED = "obscured"
    IMAGERY_UNAVAILABLE = "imagery-unavailable"
    IMAGERY_TOO_OLD = "imagery-too-old"


class EvidenceMode(StrEnum):
    DESKTOP_IMAGERY_OBSERVATION = "desktop-imagery-observation"
    OFFICER_ACCEPTED_DESKTOP_OBSERVATION = "officer-accepted-desktop-observation"
    PHYSICAL_SITE_SURVEY = "physical-site-survey"
    UNKNOWN = "unknown"


class ObservationConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class RedistributionPermission(StrEnum):
    PERMITTED = "permitted"
    REDACTED_DERIVATIVE_ONLY = "redacted-derivative-only"
    PROHIBITED = "prohibited"


class PrivacyTreatment(StrEnum):
    NONE_OBSERVED = "none-observed"
    REDACTED = "redacted"
    CONTROLLED = "controlled"


class VisualSurveyQuestionKind(StrEnum):
    INFRASTRUCTURE_CONTINUITY = "infrastructure-continuity"
    CROSSING_VISIBILITY = "crossing-visibility"
    BARRIER_OR_ACCESS = "barrier-or-access"
    ALIGNMENT_VISIBILITY = "alignment-visibility"
    SEVERANCE_VISIBILITY = "severance-visibility"


class VisualSurveyEvidenceRequestKind(StrEnum):
    NEWER_IMAGERY = "newer-imagery"
    ANOTHER_VIEWPOINT = "another-viewpoint"
    PHYSICAL_SITE_SURVEY = "physical-site-survey"


class HumanAcceptanceDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class _FingerprintedModel(_ClosedModel):
    fingerprint: str = ""

    def identity_payload(self) -> dict[str, object]:
        raise NotImplementedError

    def model_post_init(self, __context: object) -> None:
        expected = evidence_fingerprint(self.identity_payload())
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("fingerprint is stale or does not match the governed payload")
        object.__setattr__(self, "fingerprint", expected)


class VisualSurveyTarget(_FingerprintedModel):
    """One exact governed location or corridor; never executable geometry."""

    contract: Literal["satn-visual-survey-target/v1"] = "satn-visual-survey-target/v1"
    target_id: Identifier
    target_kind: Literal["location", "corridor"]
    governed_feature_ids: tuple[Identifier, ...] = Field(min_length=1)
    geometry_fingerprint: Sha256

    @field_validator("governed_feature_ids")
    @classmethod
    def canonical_feature_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("governed feature IDs must be unique")
        return tuple(sorted(value))

    def identity_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "target_id": self.target_id,
            "target_kind": self.target_kind,
            "governed_feature_ids": list(self.governed_feature_ids),
            "geometry_fingerprint": self.geometry_fingerprint,
        }


class VisualSurveyQuestion(_FingerprintedModel):
    contract: Literal["satn-visual-survey-question/v1"] = "satn-visual-survey-question/v1"
    question_id: Identifier
    question_kind: VisualSurveyQuestionKind
    target_ids: tuple[Identifier, ...] = Field(min_length=1)
    prompt: NonBlankText

    @field_validator("target_ids")
    @classmethod
    def canonical_target_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("question target IDs must be unique")
        return tuple(sorted(value))

    def identity_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "question_id": self.question_id,
            "question_kind": self.question_kind.value,
            "target_ids": list(self.target_ids),
            "prompt": self.prompt,
        }


class VisualSurveyRequest(_FingerprintedModel):
    """A compiler-authored, finite survey bound to one exact Scenario Compilation."""

    contract: Literal["satn-visual-survey-request/v1"] = "satn-visual-survey-request/v1"
    request_id: Identifier
    scenario_compilation_fingerprint: Sha256
    targets: tuple[VisualSurveyTarget, ...] = Field(min_length=1)
    questions: tuple[VisualSurveyQuestion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> VisualSurveyRequest:
        target_ids = [target.target_id for target in self.targets]
        question_ids = [question.question_id for question in self.questions]
        if len(set(target_ids)) != len(target_ids):
            raise ValueError("visual survey target IDs must be unique")
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("visual survey question IDs must be unique")
        governed_targets = set(target_ids)
        for question in self.questions:
            unknown = set(question.target_ids) - governed_targets
            if unknown:
                raise ValueError(
                    f"visual survey question references unknown target: {sorted(unknown)[0]}"
                )
        object.__setattr__(
            self, "targets", tuple(sorted(self.targets, key=lambda item: item.target_id))
        )
        object.__setattr__(
            self, "questions", tuple(sorted(self.questions, key=lambda item: item.question_id))
        )
        return self

    def identity_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "request_id": self.request_id,
            "scenario_compilation_fingerprint": self.scenario_compilation_fingerprint,
            "targets": [target.identity_payload() for target in self.targets],
            "questions": [question.identity_payload() for question in self.questions],
        }


class VisualSurveyViewpoint(_ClosedModel):
    """Provider-declared view point using integer BNG millimetres."""

    contract: Literal["satn-visual-survey-viewpoint/v1"] = (
        "satn-visual-survey-viewpoint/v1"
    )
    crs: Literal["EPSG:27700"] = "EPSG:27700"
    coordinate_unit: Literal["millimetres"] = "millimetres"
    easting_mm: int
    northing_mm: int
    bearing_degrees: int | None = Field(default=None, ge=0, le=359)


class VisualSurveyCoverage(_FingerprintedModel):
    contract: Literal["satn-visual-survey-coverage/v1"] = (
        "satn-visual-survey-coverage/v1"
    )
    target_ids: tuple[Identifier, ...] = Field(min_length=1)
    geometry_fingerprint: Sha256

    @field_validator("target_ids")
    @classmethod
    def canonical_target_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("coverage target IDs must be unique")
        return tuple(sorted(value))

    def identity_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "target_ids": list(self.target_ids),
            "geometry_fingerprint": self.geometry_fingerprint,
        }


class VisualImageSource(_FingerprintedModel):
    """Attributable image or provider attempt with licence and privacy state."""

    contract: Literal["satn-visual-image-source/v1"] = "satn-visual-image-source/v1"
    provider_id: Identifier
    imagery_kind: ImageryKind
    imagery_identifier: NonBlankText | None
    source_reference: NonBlankText
    captured_on: date | None
    retrieved_on: date
    licence_terms: NonBlankText
    viewpoint: VisualSurveyViewpoint
    spatial_coverage: VisualSurveyCoverage
    content_sha256: Sha256 | None
    availability: ImageryAvailability
    redistribution: RedistributionPermission
    privacy_treatment: PrivacyTreatment

    @model_validator(mode="after")
    def validate_availability(self) -> VisualImageSource:
        if self.availability is ImageryAvailability.AVAILABLE:
            if self.imagery_identifier is None or self.content_sha256 is None:
                raise ValueError(
                    "available imagery requires its identifier and content fingerprint"
                )
        elif self.imagery_identifier is not None or self.content_sha256 is not None:
            raise ValueError(
                "unavailable imagery cannot claim an imagery identifier or content fingerprint"
            )
        if self.captured_on is not None and self.captured_on > self.retrieved_on:
            raise ValueError("imagery capture date cannot follow retrieval date")
        return self

    def identity_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "provider_id": self.provider_id,
            "imagery_kind": self.imagery_kind.value,
            "imagery_identifier": self.imagery_identifier,
            "source_reference": self.source_reference,
            "captured_on": self.captured_on.isoformat() if self.captured_on else None,
            "retrieved_on": self.retrieved_on.isoformat(),
            "licence_terms": self.licence_terms,
            "viewpoint": self.viewpoint.model_dump(mode="json"),
            "spatial_coverage": self.spatial_coverage.identity_payload(),
            "content_sha256": self.content_sha256,
            "availability": self.availability.value,
            "redistribution": self.redistribution.value,
            "privacy_treatment": self.privacy_treatment.value,
        }


class VisualSurveyEvidenceRequest(_FingerprintedModel):
    contract: Literal["satn-visual-survey-evidence-request/v1"] = (
        "satn-visual-survey-evidence-request/v1"
    )
    evidence_request_id: Identifier
    request_kind: VisualSurveyEvidenceRequestKind
    originating_request_fingerprint: Sha256
    target_id: Identifier
    question_id: Identifier
    reason: NonBlankText

    def identity_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "evidence_request_id": self.evidence_request_id,
            "request_kind": self.request_kind.value,
            "originating_request_fingerprint": self.originating_request_fingerprint,
            "target_id": self.target_id,
            "question_id": self.question_id,
            "reason": self.reason,
        }


class VisualSurveyObservation(_FingerprintedModel):
    """One bounded desktop observation, never a design or legal conclusion."""

    contract: Literal["satn-visual-survey-observation/v1"] = (
        "satn-visual-survey-observation/v1"
    )
    observation_id: Identifier
    request_fingerprint: Sha256
    target_id: Identifier
    question_id: Identifier
    category: ObservationCategory
    evidence_mode: EvidenceMode
    supporting_sources: tuple[VisualImageSource, ...] = Field(min_length=1)
    rationale: NonBlankText
    public_summary: NonBlankText
    visibility_limitations: tuple[NonBlankText, ...] = Field(min_length=1)
    confidence: ObservationConfidence
    material: bool

    @model_validator(mode="after")
    def validate_evidence_boundary(self) -> VisualSurveyObservation:
        if self.evidence_mode is EvidenceMode.PHYSICAL_SITE_SURVEY:
            raise ValueError("a desktop imagery provider cannot produce a physical site survey")
        if self.evidence_mode is EvidenceMode.OFFICER_ACCEPTED_DESKTOP_OBSERVATION:
            raise ValueError("a provider cannot self-declare officer acceptance")
        sources = tuple(
            sorted(self.supporting_sources, key=lambda source: source.fingerprint)
        )
        if len({source.fingerprint for source in sources}) != len(sources):
            raise ValueError("supporting image sources must be unique")
        object.__setattr__(self, "supporting_sources", sources)
        available = [
            source
            for source in sources
            if source.availability is ImageryAvailability.AVAILABLE
        ]
        if self.category in {
            ObservationCategory.VISIBLE,
            ObservationCategory.NOT_VISIBLE,
        }:
            if not available:
                raise ValueError("positive observation requires available imagery")
            if any(source.captured_on is None for source in available):
                raise ValueError("positive observation requires an imagery capture date")
        if self.category is ObservationCategory.IMAGERY_UNAVAILABLE and available:
            raise ValueError("imagery-unavailable cannot cite an available image")
        if self.category is ObservationCategory.IMAGERY_TOO_OLD and not available:
            raise ValueError("imagery-too-old requires an available dated image")
        return self

    @field_validator("visibility_limitations")
    @classmethod
    def canonical_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("visibility limitations must be unique")
        return tuple(sorted(value))

    def identity_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "observation_id": self.observation_id,
            "request_fingerprint": self.request_fingerprint,
            "target_id": self.target_id,
            "question_id": self.question_id,
            "category": self.category.value,
            "evidence_mode": self.evidence_mode.value,
            "supporting_source_fingerprints": [
                source.fingerprint for source in self.supporting_sources
            ],
            "rationale": self.rationale,
            "public_summary": self.public_summary,
            "visibility_limitations": list(self.visibility_limitations),
            "confidence": self.confidence.value,
            "material": self.material,
        }


class VisualSurveyResponse(_FingerprintedModel):
    contract: Literal["satn-visual-survey-response/v1"] = (
        "satn-visual-survey-response/v1"
    )
    provider_id: Identifier
    request_fingerprint: Sha256
    observations: tuple[VisualSurveyObservation, ...]
    evidence_requests: tuple[VisualSurveyEvidenceRequest, ...] = ()

    @model_validator(mode="after")
    def canonical_records(self) -> VisualSurveyResponse:
        observations = tuple(
            sorted(self.observations, key=lambda item: item.observation_id)
        )
        requests = tuple(
            sorted(self.evidence_requests, key=lambda item: item.evidence_request_id)
        )
        if len({item.observation_id for item in observations}) != len(observations):
            raise ValueError("visual survey observation IDs must be unique")
        if len({item.evidence_request_id for item in requests}) != len(requests):
            raise ValueError("visual survey evidence request IDs must be unique")
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "evidence_requests", requests)
        return self

    def identity_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "provider_id": self.provider_id,
            "request_fingerprint": self.request_fingerprint,
            "observation_fingerprints": [
                observation.fingerprint for observation in self.observations
            ],
            "evidence_request_fingerprints": [
                request.fingerprint for request in self.evidence_requests
            ],
        }


class VisualSurveyConfiguration(_ClosedModel):
    """Opt-in provider allow-list. The default cannot invoke any provider."""

    contract: Literal["satn-visual-survey-configuration/v1"] = (
        "satn-visual-survey-configuration/v1"
    )
    enabled: bool = False
    approved_provider_ids: tuple[Identifier, ...] = ()
    maximum_requests: int = Field(default=12, ge=1, le=100)

    @model_validator(mode="after")
    def validate_approval(self) -> VisualSurveyConfiguration:
        providers = tuple(sorted(self.approved_provider_ids))
        if len(set(providers)) != len(providers):
            raise ValueError("approved visual survey provider IDs must be unique")
        if self.enabled and not providers:
            raise ValueError("enabled visual surveys require an approved provider")
        if not self.enabled and providers:
            raise ValueError("disabled visual surveys cannot approve providers")
        object.__setattr__(self, "approved_provider_ids", providers)
        return self


class VisualSurveyProvider(Protocol):
    provider_id: str

    def survey(self, request: VisualSurveyRequest) -> VisualSurveyResponse: ...


class FixtureVisualSurveyProvider:
    """Deterministic test adapter; it never performs network or provider access."""

    def __init__(
        self,
        *,
        provider_id: str,
        responses: Mapping[str, VisualSurveyResponse],
    ) -> None:
        if not provider_id or provider_id.strip() != provider_id:
            raise ValueError("fixture provider ID must be canonical text")
        self.provider_id = provider_id
        self._responses = dict(responses)
        self.call_count = 0

    def survey(self, request: VisualSurveyRequest) -> VisualSurveyResponse:
        self.call_count += 1
        try:
            return self._responses[request.request_id]
        except KeyError as error:
            raise ValueError(
                f"fixture provider has no response for request {request.request_id}"
            ) from error


def commission_visual_survey(
    configuration: VisualSurveyConfiguration,
    request: VisualSurveyRequest,
    provider: VisualSurveyProvider,
) -> VisualSurveyResponse | None:
    """Commission one bounded request, or return before provider construction/use."""

    if not configuration.enabled:
        return None
    if provider.provider_id not in configuration.approved_provider_ids:
        raise ValueError(f"visual survey provider {provider.provider_id} is not approved")
    response = provider.survey(request)
    _validate_response_against_request(request, response, provider.provider_id)
    return response


class HumanVisualSurveyAcceptance(_FingerprintedModel):
    contract: Literal["satn-human-visual-survey-acceptance/v1"] = (
        "satn-human-visual-survey-acceptance/v1"
    )
    acceptance_id: Identifier
    observation_fingerprint: Sha256
    decision: HumanAcceptanceDecision
    accountable_person_id: Identifier
    accountable_role: NonBlankText
    organisation: NonBlankText
    decided_on: date
    rationale: NonBlankText

    def identity_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "acceptance_id": self.acceptance_id,
            "observation_fingerprint": self.observation_fingerprint,
            "decision": self.decision.value,
            "accountable_person_id": self.accountable_person_id,
            "accountable_role": self.accountable_role,
            "organisation": self.organisation,
            "decided_on": self.decided_on.isoformat(),
            "rationale": self.rationale,
        }


class ScenarioVisualFinding(_FingerprintedModel):
    contract: Literal["satn-scenario-visual-finding/v1"] = (
        "satn-scenario-visual-finding/v1"
    )
    target_id: Identifier
    question_id: Identifier
    outcome: ObservationCategory
    evidence_mode: Literal[
        EvidenceMode.OFFICER_ACCEPTED_DESKTOP_OBSERVATION
    ] = EvidenceMode.OFFICER_ACCEPTED_DESKTOP_OBSERVATION
    accepted_observation_fingerprints: tuple[Sha256, ...] = Field(min_length=1)
    acceptance_fingerprints: tuple[Sha256, ...] = Field(min_length=1)
    conflicting_observation_fingerprints: tuple[Sha256, ...] = ()
    limitation: Literal[
        "Desktop imagery is not proof of safety, legal access, dimensions, "
        "condition, capacity, feasibility or design compliance."
    ] = (
        "Desktop imagery is not proof of safety, legal access, dimensions, "
        "condition, capacity, feasibility or design compliance."
    )

    @field_validator(
        "accepted_observation_fingerprints",
        "acceptance_fingerprints",
        "conflicting_observation_fingerprints",
    )
    @classmethod
    def canonical_fingerprints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("scenario visual finding fingerprints must be unique")
        return tuple(sorted(value))

    def identity_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "target_id": self.target_id,
            "question_id": self.question_id,
            "outcome": self.outcome.value,
            "evidence_mode": self.evidence_mode.value,
            "accepted_observation_fingerprints": list(
                self.accepted_observation_fingerprints
            ),
            "acceptance_fingerprints": list(self.acceptance_fingerprints),
            "conflicting_observation_fingerprints": list(
                self.conflicting_observation_fingerprints
            ),
            "limitation": self.limitation,
        }


class ScenarioVisualEvidence(_FingerprintedModel):
    """Immutable criterion evidence; it contains no executable route geometry."""

    contract: Literal["satn-scenario-visual-evidence/v1"] = (
        "satn-scenario-visual-evidence/v1"
    )
    scenario_compilation_fingerprint: Sha256
    request_fingerprint: Sha256
    response_fingerprint: Sha256
    observation_fingerprints: tuple[Sha256, ...]
    acceptance_fingerprints: tuple[Sha256, ...]
    findings: tuple[ScenarioVisualFinding, ...]
    evidence_requests: tuple[VisualSurveyEvidenceRequest, ...] = ()

    def identity_payload(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "scenario_compilation_fingerprint": self.scenario_compilation_fingerprint,
            "request_fingerprint": self.request_fingerprint,
            "response_fingerprint": self.response_fingerprint,
            "observation_fingerprints": list(self.observation_fingerprints),
            "acceptance_fingerprints": list(self.acceptance_fingerprints),
            "finding_fingerprints": [finding.fingerprint for finding in self.findings],
            "evidence_request_fingerprints": [
                request.fingerprint for request in self.evidence_requests
            ],
        }


def build_scenario_visual_evidence(
    request: VisualSurveyRequest,
    response: VisualSurveyResponse,
    acceptances: Sequence[HumanVisualSurveyAcceptance],
) -> ScenarioVisualEvidence:
    """Gate material observations and produce immutable officer-accepted evidence."""

    _validate_response_against_request(request, response, response.provider_id)
    acceptance_by_observation: dict[str, HumanVisualSurveyAcceptance] = {}
    for acceptance in acceptances:
        if acceptance.observation_fingerprint in acceptance_by_observation:
            raise ValueError("each visual observation may have only one human acceptance")
        acceptance_by_observation[acceptance.observation_fingerprint] = acceptance

    accepted: list[tuple[VisualSurveyObservation, HumanVisualSurveyAcceptance]] = []
    observation_fingerprints = {item.fingerprint for item in response.observations}
    if set(acceptance_by_observation) - observation_fingerprints:
        raise ValueError("human acceptance references an observation outside the response")
    for observation in response.observations:
        acceptance = acceptance_by_observation.get(observation.fingerprint)
        if observation.material and acceptance is None:
            raise ValueError("material visual observation requires attributable human acceptance")
        if acceptance is None:
            continue
        if acceptance.decision is not HumanAcceptanceDecision.ACCEPTED:
            raise ValueError("scenario visual evidence requires an accepted human decision")
        accepted.append((observation, acceptance))

    grouped: dict[
        tuple[str, str],
        list[tuple[VisualSurveyObservation, HumanVisualSurveyAcceptance]],
    ] = defaultdict(list)
    for record in accepted:
        grouped[(record[0].target_id, record[0].question_id)].append(record)

    findings: list[ScenarioVisualFinding] = []
    follow_ups = list(response.evidence_requests)
    for (target_id, question_id), records in sorted(grouped.items()):
        categories = {record[0].category for record in records}
        conflict = (
            ObservationCategory.VISIBLE in categories
            and ObservationCategory.NOT_VISIBLE in categories
        )
        outcome = (
            ObservationCategory.AMBIGUOUS
            if conflict or len(categories) > 1
            else next(iter(categories))
        )
        ordered_records = sorted(records, key=lambda record: record[0].observation_id)
        conflicting = (
            tuple(record[0].fingerprint for record in ordered_records) if conflict else ()
        )
        findings.append(
            ScenarioVisualFinding(
                target_id=target_id,
                question_id=question_id,
                outcome=outcome,
                accepted_observation_fingerprints=tuple(
                    record[0].fingerprint for record in ordered_records
                ),
                acceptance_fingerprints=tuple(
                    record[1].fingerprint for record in ordered_records
                ),
                conflicting_observation_fingerprints=conflicting,
            )
        )
        if conflict:
            follow_ups.append(
                VisualSurveyEvidenceRequest(
                    evidence_request_id=f"conflict-{target_id}-{question_id}",
                    request_kind=VisualSurveyEvidenceRequestKind.ANOTHER_VIEWPOINT,
                    originating_request_fingerprint=request.fingerprint,
                    target_id=target_id,
                    question_id=question_id,
                    reason=(
                        "Accepted desktop observations conflict; obtain another "
                        "viewpoint or escalate to a physical site survey."
                    ),
                )
            )

    return ScenarioVisualEvidence(
        scenario_compilation_fingerprint=request.scenario_compilation_fingerprint,
        request_fingerprint=request.fingerprint,
        response_fingerprint=response.fingerprint,
        observation_fingerprints=tuple(
            sorted(observation.fingerprint for observation, _ in accepted)
        ),
        acceptance_fingerprints=tuple(
            sorted(acceptance.fingerprint for _, acceptance in accepted)
        ),
        findings=tuple(sorted(findings, key=lambda item: (item.target_id, item.question_id))),
        evidence_requests=tuple(
            sorted(follow_ups, key=lambda item: item.evidence_request_id)
        ),
    )


def public_visual_survey_payload(response: VisualSurveyResponse) -> dict[str, object]:
    """Return privacy- and licence-safe metadata without controlled imagery bytes."""

    observations: list[dict[str, object]] = []
    for observation in response.observations:
        public_sources: list[dict[str, object]] = []
        for source in observation.supporting_sources:
            publishable = (
                source.redistribution is RedistributionPermission.PERMITTED
                and source.privacy_treatment is PrivacyTreatment.NONE_OBSERVED
            )
            public_source: dict[str, object] = {
                "provider_id": source.provider_id,
                "imagery_kind": source.imagery_kind.value,
                "captured_on": source.captured_on.isoformat()
                if source.captured_on
                else None,
                "retrieved_on": source.retrieved_on.isoformat(),
                "licence_terms": source.licence_terms,
                "availability": source.availability.value,
                "spatial_coverage_fingerprint": source.spatial_coverage.fingerprint,
                "publication": "included" if publishable else "redacted",
            }
            if publishable:
                public_source.update(
                    {
                        "imagery_identifier": source.imagery_identifier,
                        "source_reference": source.source_reference,
                        "content_sha256": source.content_sha256,
                    }
                )
            else:
                public_source["redaction_reason"] = (
                    "provider redistribution or incidental-personal-data controls"
                )
            public_sources.append(public_source)
        observations.append(
            {
                "observation_id": observation.observation_id,
                "fingerprint": observation.fingerprint,
                "target_id": observation.target_id,
                "question_id": observation.question_id,
                "category": observation.category.value,
                "evidence_mode": observation.evidence_mode.value,
                "public_summary": observation.public_summary,
                "visibility_limitations": list(observation.visibility_limitations),
                "confidence": observation.confidence.value,
                "supporting_sources": public_sources,
            }
        )
    return {
        "contract": "satn-public-visual-survey-evidence/v1",
        "provider_id": response.provider_id,
        "request_fingerprint": response.request_fingerprint,
        "response_fingerprint": response.fingerprint,
        "observations": observations,
        "evidence_requests": [
            request.model_dump(mode="json") for request in response.evidence_requests
        ],
    }


def _validate_response_against_request(
    request: VisualSurveyRequest,
    response: VisualSurveyResponse,
    provider_id: str,
) -> None:
    if response.provider_id != provider_id:
        raise ValueError("visual survey response provider does not match commissioned provider")
    if response.request_fingerprint != request.fingerprint:
        raise ValueError("visual survey response is not bound to the exact request")
    targets = {target.target_id: target for target in request.targets}
    questions = {question.question_id: question for question in request.questions}
    for observation in response.observations:
        if observation.request_fingerprint != request.fingerprint:
            raise ValueError("visual survey observation is not bound to the exact request")
        target = targets.get(observation.target_id)
        question = questions.get(observation.question_id)
        if target is None or question is None or target.target_id not in question.target_ids:
            raise ValueError("visual survey observation is outside governed request bounds")
        for source in observation.supporting_sources:
            if source.provider_id != provider_id:
                raise ValueError("visual survey source provider does not match response provider")
            if observation.target_id not in source.spatial_coverage.target_ids:
                raise ValueError("visual survey source coverage does not bind the target")
            if source.spatial_coverage.geometry_fingerprint != target.geometry_fingerprint:
                raise ValueError("visual survey source coverage geometry is stale")
    for evidence_request in response.evidence_requests:
        if (
            evidence_request.originating_request_fingerprint != request.fingerprint
            or evidence_request.target_id not in targets
            or evidence_request.question_id not in questions
        ):
            raise ValueError("visual survey evidence request is outside governed bounds")
