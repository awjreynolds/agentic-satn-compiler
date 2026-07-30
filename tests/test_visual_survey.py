from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from satn.visual_survey import (
    EvidenceMode,
    FixtureVisualSurveyProvider,
    HumanAcceptanceDecision,
    HumanVisualSurveyAcceptance,
    ImageryAvailability,
    ImageryKind,
    ObservationCategory,
    ObservationConfidence,
    PrivacyTreatment,
    RedistributionPermission,
    ScenarioVisualEvidence,
    VisualImageSource,
    VisualSurveyConfiguration,
    VisualSurveyCoverage,
    VisualSurveyEvidenceRequest,
    VisualSurveyEvidenceRequestKind,
    VisualSurveyObservation,
    VisualSurveyQuestion,
    VisualSurveyQuestionKind,
    VisualSurveyRequest,
    VisualSurveyResponse,
    VisualSurveyTarget,
    VisualSurveyViewpoint,
    build_scenario_visual_evidence,
    commission_visual_survey,
    public_visual_survey_payload,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
PROJECT = Path(__file__).parents[1]


def request() -> VisualSurveyRequest:
    return VisualSurveyRequest(
        request_id="survey-edge-42",
        scenario_compilation_fingerprint=SHA_A,
        targets=(
            VisualSurveyTarget(
                target_id="target-edge-42",
                target_kind="corridor",
                governed_feature_ids=("edge-42", "edge-43"),
                geometry_fingerprint=SHA_B,
            ),
        ),
        questions=(
            VisualSurveyQuestion(
                question_id="question-continuity",
                question_kind=VisualSurveyQuestionKind.INFRASTRUCTURE_CONTINUITY,
                target_ids=("target-edge-42",),
                prompt="Does mapped cycle infrastructure appear to continue here?",
            ),
        ),
    )


def source(
    *,
    imagery_id: str = "fixture-image-1",
    kind: ImageryKind = ImageryKind.STREET_LEVEL,
    availability: ImageryAvailability = ImageryAvailability.AVAILABLE,
    captured_on: date | None = date(2025, 6, 1),
    redistribution: RedistributionPermission = RedistributionPermission.PROHIBITED,
    privacy: PrivacyTreatment = PrivacyTreatment.REDACTED,
) -> VisualImageSource:
    available = availability is ImageryAvailability.AVAILABLE
    return VisualImageSource(
        provider_id="fixture-provider",
        imagery_kind=kind,
        imagery_identifier=imagery_id if available else None,
        source_reference=f"fixture://visual-survey/{imagery_id}",
        captured_on=captured_on,
        retrieved_on=date(2026, 7, 30),
        licence_terms="Fixture licence; no redistribution.",
        viewpoint=VisualSurveyViewpoint(
            easting_mm=451_000_000,
            northing_mm=203_000_000,
            bearing_degrees=90 if kind is ImageryKind.STREET_LEVEL else None,
        ),
        spatial_coverage=VisualSurveyCoverage(
            target_ids=("target-edge-42",),
            geometry_fingerprint=SHA_B,
        ),
        content_sha256=SHA_C if available else None,
        availability=availability,
        redistribution=redistribution,
        privacy_treatment=privacy,
    )


def observation(
    *,
    category: ObservationCategory = ObservationCategory.VISIBLE,
    image_source: VisualImageSource | None = None,
    observation_id: str = "observation-1",
) -> VisualSurveyObservation:
    return VisualSurveyObservation(
        observation_id=observation_id,
        request_fingerprint=request().fingerprint,
        target_id="target-edge-42",
        question_id="question-continuity",
        category=category,
        evidence_mode=EvidenceMode.DESKTOP_IMAGERY_OBSERVATION,
        supporting_sources=(image_source or source(),),
        rationale="The fixture depicts a continuous marked facility through the view.",
        public_summary="A desktop imagery observation was recorded for officer review.",
        visibility_limitations=("Only one viewpoint is available.",),
        confidence=ObservationConfidence.MEDIUM,
        material=True,
    )


def response(*observations: VisualSurveyObservation) -> VisualSurveyResponse:
    return VisualSurveyResponse(
        provider_id="fixture-provider",
        request_fingerprint=request().fingerprint,
        observations=observations,
    )


def acceptance(
    item: VisualSurveyObservation,
    *,
    acceptance_id: str = "acceptance-1",
    decision: HumanAcceptanceDecision = HumanAcceptanceDecision.ACCEPTED,
    rationale: str = "Accepted as bounded desktop evidence, not as a site survey.",
) -> HumanVisualSurveyAcceptance:
    return HumanVisualSurveyAcceptance(
        acceptance_id=acceptance_id,
        observation_fingerprint=item.fingerprint,
        decision=decision,
        accountable_person_id="officer-123",
        accountable_role="Principal Transport Planner",
        organisation="Example Council",
        decided_on=date(2026, 7, 30),
        rationale=rationale,
    )


def test_request_is_bounded_and_fingerprint_is_canonical() -> None:
    first = request()
    reordered = VisualSurveyRequest(
        request_id=first.request_id,
        scenario_compilation_fingerprint=first.scenario_compilation_fingerprint,
        targets=tuple(reversed(first.targets)),
        questions=tuple(reversed(first.questions)),
    )

    assert first.fingerprint == reordered.fingerprint
    assert len(first.fingerprint) == 64
    with pytest.raises(ValidationError, match="unknown target"):
        VisualSurveyRequest(
            request_id="bad-request",
            scenario_compilation_fingerprint=SHA_A,
            targets=first.targets,
            questions=(
                VisualSurveyQuestion(
                    question_id=first.questions[0].question_id,
                    question_kind=first.questions[0].question_kind,
                    target_ids=("not-governed",),
                    prompt=first.questions[0].prompt,
                ),
            ),
        )


def test_fixture_provider_is_explicitly_approved_and_disabled_mode_makes_no_call() -> None:
    item = observation()
    provider = FixtureVisualSurveyProvider(
        provider_id="fixture-provider",
        responses={request().request_id: response(item)},
    )

    assert commission_visual_survey(
        VisualSurveyConfiguration(), request(), provider
    ) is None
    assert provider.call_count == 0

    configured = VisualSurveyConfiguration(
        enabled=True,
        approved_provider_ids=("fixture-provider",),
    )
    result = commission_visual_survey(configured, request(), provider)

    assert result == response(item)
    assert provider.call_count == 1
    with pytest.raises(ValueError, match="not approved"):
        commission_visual_survey(
            VisualSurveyConfiguration(
                enabled=True,
                approved_provider_ids=("different-provider",),
            ),
            request(),
            provider,
        )


def test_clean_banes_and_weca_baselines_have_no_visual_survey_configuration() -> None:
    for relative_path in (
        Path("deployments/banes/area.yaml"),
        Path("deployments/weca/area.yaml"),
    ):
        definition = yaml.safe_load((PROJECT / relative_path).read_text(encoding="utf-8"))
        assert "visual_survey" not in definition.get("compilation", {})


@pytest.mark.parametrize(
    ("kind", "category"),
    (
        (ImageryKind.STREET_LEVEL, ObservationCategory.OBSCURED),
        (ImageryKind.AERIAL, ObservationCategory.AMBIGUOUS),
    ),
)
def test_street_level_and_aerial_observations_retain_source_metadata(
    kind: ImageryKind,
    category: ObservationCategory,
) -> None:
    item = observation(category=category, image_source=source(kind=kind))

    assert item.supporting_sources[0].imagery_kind is kind
    assert item.supporting_sources[0].licence_terms
    assert item.supporting_sources[0].retrieved_on == date(2026, 7, 30)
    assert item.evidence_mode is EvidenceMode.DESKTOP_IMAGERY_OBSERVATION


def test_missing_stale_and_obscured_imagery_cannot_become_positive_findings() -> None:
    unavailable = source(
        availability=ImageryAvailability.UNAVAILABLE,
        captured_on=None,
    )
    missing = observation(
        category=ObservationCategory.IMAGERY_UNAVAILABLE,
        image_source=unavailable,
    )
    stale = observation(
        category=ObservationCategory.IMAGERY_TOO_OLD,
        image_source=source(captured_on=date(2015, 1, 1)),
    )
    obscured = observation(category=ObservationCategory.OBSCURED)

    assert {missing.category, stale.category, obscured.category}.isdisjoint(
        {ObservationCategory.VISIBLE, ObservationCategory.NOT_VISIBLE}
    )
    with pytest.raises(ValidationError, match="positive observation"):
        observation(
            category=ObservationCategory.VISIBLE,
            image_source=unavailable,
        )
    with pytest.raises(ValidationError, match="capture date"):
        observation(
            category=ObservationCategory.VISIBLE,
            image_source=source(captured_on=None),
        )


def test_provider_cannot_claim_site_survey_or_emit_executable_geometry() -> None:
    payload = observation().model_dump(mode="python", exclude={"fingerprint"})
    payload["evidence_mode"] = EvidenceMode.PHYSICAL_SITE_SURVEY
    with pytest.raises(ValidationError, match="cannot produce a physical site survey"):
        VisualSurveyObservation.model_validate(payload)

    payload = observation().model_dump(mode="python", exclude={"fingerprint"})
    payload["executable_geometry"] = {"type": "LineString", "coordinates": []}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VisualSurveyObservation.model_validate(payload)


def test_material_observation_requires_attributable_human_acceptance() -> None:
    item = observation()
    survey_response = response(item)

    with pytest.raises(ValueError, match="human acceptance"):
        build_scenario_visual_evidence(request(), survey_response, ())
    with pytest.raises(ValueError, match="accepted"):
        build_scenario_visual_evidence(
            request(),
            survey_response,
            (acceptance(item, decision=HumanAcceptanceDecision.REJECTED),),
        )

    evidence = build_scenario_visual_evidence(
        request(), survey_response, (acceptance(item),)
    )

    assert isinstance(evidence, ScenarioVisualEvidence)
    assert evidence.findings[0].evidence_mode is EvidenceMode.OFFICER_ACCEPTED_DESKTOP_OBSERVATION
    assert evidence.findings[0].outcome is ObservationCategory.VISIBLE
    assert evidence.acceptance_fingerprints == (acceptance(item).fingerprint,)


def test_conflicting_images_remain_ambiguous_and_request_another_viewpoint() -> None:
    visible = observation()
    not_visible = observation(
        category=ObservationCategory.NOT_VISIBLE,
        observation_id="observation-2",
        image_source=source(imagery_id="fixture-image-2"),
    )

    evidence = build_scenario_visual_evidence(
        request(),
        response(visible, not_visible),
        (
            acceptance(visible),
            acceptance(not_visible, acceptance_id="acceptance-2"),
        ),
    )

    assert evidence.findings[0].outcome is ObservationCategory.AMBIGUOUS
    assert evidence.findings[0].conflicting_observation_fingerprints == tuple(
        sorted((visible.fingerprint, not_visible.fingerprint))
    )
    assert evidence.evidence_requests[0].request_kind is (
        VisualSurveyEvidenceRequestKind.ANOTHER_VIEWPOINT
    )


def test_public_payload_redacts_private_and_licence_restricted_image_references() -> None:
    restricted = response(observation())
    public = response(
        observation(
            image_source=source(
                redistribution=RedistributionPermission.PERMITTED,
                privacy=PrivacyTreatment.NONE_OBSERVED,
            )
        )
    )

    restricted_payload = public_visual_survey_payload(restricted)
    public_payload = public_visual_survey_payload(public)

    restricted_source = restricted_payload["observations"][0]["supporting_sources"][0]
    assert "source_reference" not in restricted_source
    assert "imagery_identifier" not in restricted_source
    assert restricted_source["publication"] == "redacted"
    assert public_payload["observations"][0]["supporting_sources"][0][
        "source_reference"
    ].startswith("fixture://")
    assert "rationale" not in restricted_payload["observations"][0]


def test_changed_imagery_or_acceptance_changes_scenario_evidence_fingerprint() -> None:
    first_observation = observation()
    changed_observation = observation(
        image_source=source(imagery_id="fixture-image-2"),
    )
    first = build_scenario_visual_evidence(
        request(), response(first_observation), (acceptance(first_observation),)
    )
    changed_image = build_scenario_visual_evidence(
        request(),
        response(changed_observation),
        (acceptance(changed_observation),),
    )
    changed_acceptance = build_scenario_visual_evidence(
        request(),
        response(first_observation),
        (
            acceptance(
                first_observation,
                rationale="Accepted after a second officer review.",
            ),
        ),
    )

    assert first.fingerprint != changed_image.fingerprint
    assert first.fingerprint != changed_acceptance.fingerprint


def test_typed_follow_up_request_is_bound_to_the_original_governed_question() -> None:
    follow_up = VisualSurveyEvidenceRequest(
        evidence_request_id="newer-image-edge-42",
        request_kind=VisualSurveyEvidenceRequestKind.NEWER_IMAGERY,
        originating_request_fingerprint=request().fingerprint,
        target_id="target-edge-42",
        question_id="question-continuity",
        reason="Available imagery predates the declared freshness threshold.",
    )

    assert follow_up.fingerprint
