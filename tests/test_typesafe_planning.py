from __future__ import annotations

import json

from satn.typesafe_planning import (
    ChoiceQuestion,
    NoulQuestion,
    ScoreQuestion,
    TypeSafeClient,
    TypeSafeProvider,
)


def _response_payload() -> dict[str, object]:
    return {
        "model": "jev-1.13.0",
        "answers": {
            "route": {
                "type": "choice",
                "choice": "existing",
                "probabilities": {"existing": 0.8, "new": 0.2},
                "confidence": 0.7,
            },
            "usable": {"type": "noul", "noul": 0.9},
            "evidence": {
                "type": "score",
                "score": 1.25,
                "legend": {"0": "missing", "1": "partial", "2": "complete"},
                "probabilities": {"0": 0.1, "1": 0.55, "2": 0.35},
                "confidence": 0.6,
            },
        },
        "usage": {"input_tokens": 120, "output_tokens": 35},
    }


def _questions() -> dict[str, object]:
    return {
        "route": ChoiceQuestion(
            instructions="Which supplied route fits the policy?",
            criteria={"existing": "currently usable", "new": "requires construction"},
        ),
        "usable": NoulQuestion(instructions="Is at least one supplied route currently usable?"),
        "evidence": ScoreQuestion(
            instructions="How complete is the evidence?",
            criteria=["missing", "partial", "complete"],
        ),
    }


def test_judge_sends_documented_payload_and_returns_redacted_receipts(monkeypatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret-must-not-escape")
    seen: list[object] = []

    def transport(request):
        seen.append(request)
        return 200, json.dumps(_response_payload()).encode("utf-8")

    client = TypeSafeClient(
        provider="typesafe-test",
        model="jev-1.13.0",
        endpoint="https://provider.invalid/v1/systemone",
        transport=transport,
    )
    result = client.judge({"connection": "Birch to Cedar"}, _questions())

    assert result["status"] == "answered"
    assert result["provider"] == "typesafe-test"
    assert result["model"] == "jev-1.13.0"
    assert result["usage"] == {"input_tokens": 120, "output_tokens": 35}
    assert result["answers"]["usable"]["noul"] == 0.9
    assert "confidence" not in result["answers"]["usable"]
    assert len(seen) == 1
    request = seen[0]
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {
        "state": {"connection": "Birch to Cedar"},
        "model": "jev-1.13.0",
        "questions": {
            "route": {
                "type": "choice",
                "instructions": "Which supplied route fits the policy?",
                "criteria": {"existing": "currently usable", "new": "requires construction"},
            },
            "usable": {
                "type": "noul",
                "instructions": "Is at least one supplied route currently usable?",
            },
            "evidence": {
                "type": "score",
                "instructions": "How complete is the evidence?",
                "criteria": ["missing", "partial", "complete"],
            },
        },
    }
    assert "secret-must-not-escape" not in json.dumps(result, sort_keys=True)
    assert result["request_receipt"]["body"] == request.data.decode("utf-8")
    assert result["response_receipt"]["status_code"] == 200
    assert result["response_receipt"]["request_id"] is None


def test_observed_rounded_choice_distribution_remains_a_typed_answer() -> None:
    response = {
        "model": "jev-1.13.0",
        "answers": {
            "decision": {
                "type": "choice",
                "choice": "__needs_evidence__",
                "confidence": 0.39,
                "probabilities": {
                    "__needs_evidence__": 0.54,
                    "__none__": 0.04,
                    "__unknown__": 0.14,
                    "evaluation-bath-radstock": 0.27,
                },
            }
        },
        "usage": {"input_tokens": 1753, "output_tokens": 66},
    }
    questions = {
        "decision": ChoiceQuestion(
            instructions="Choose one admitted outcome.",
            criteria={
                "__needs_evidence__": "request evidence",
                "__none__": "select no supplied option",
                "__unknown__": "record an unknown",
                "evaluation-bath-radstock": "the named-place connection",
            },
        )
    }

    result = TypeSafeClient(
        provider="typesafe-recorded",
        model="jev-latest",
        endpoint="https://provider.invalid/v1/systemone",
        transport=lambda _request: (200, json.dumps(response)),
    ).judge({"task": "bath-radstock"}, questions)

    assert result["status"] == "answered"
    assert result["model"] == "jev-1.13.0"
    assert result["usage"] == {"input_tokens": 1753, "output_tokens": 66}
    assert result["answers"]["decision"] == response["answers"]["decision"]


def test_choice_answer_must_use_an_offered_key_and_valid_probabilities() -> None:
    def transport(request):
        del request
        payload = _response_payload()
        payload["answers"]["route"]["choice"] = "invented"
        payload["answers"]["route"]["probabilities"] = {"existing": 1.1, "new": -0.1}
        return 200, json.dumps(payload).encode("utf-8")

    result = TypeSafeClient(transport=transport).judge({}, _questions())

    assert result["status"] == "invalid"
    assert "answers.route.choice" in result["violations"]
    assert "answers.route.probabilities.existing" in result["violations"]
    assert "answers.route.probabilities.new" in result["violations"]


def test_invalid_score_fields_are_reported_without_coercing_noul() -> None:
    def transport(request):
        del request
        payload = _response_payload()
        payload["answers"]["usable"] = {"type": "noul", "noul": 0.4, "confidence": 0.4}
        payload["answers"]["evidence"]["score"] = 4.0
        payload["answers"]["evidence"]["probabilities"] = {"0": 0.2, "1": 0.2, "2": 0.2}
        return 200, json.dumps(payload).encode("utf-8")

    result = TypeSafeClient(transport=transport).judge({}, _questions())

    assert result["status"] == "invalid"
    assert "answers.evidence.score" in result["violations"]
    assert "answers.evidence.probabilities" in result["violations"]


def test_missing_credentials_are_unavailable_without_calling_transport(monkeypatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    called = False

    def transport(request):
        nonlocal called
        called = True
        del request
        return 200, json.dumps(_response_payload()).encode("utf-8")

    result = TypeSafeClient(
        provider=TypeSafeProvider(credential_path=None),
        transport=transport,
        require_credentials=True,
    ).judge({}, _questions())

    assert result["status"] == "unavailable"
    assert result["failure_class"] == "missing-credential"
    assert called is False


def test_http_failure_is_servicefailed_and_keeps_response_receipt() -> None:
    def transport(request):
        del request
        return 503, b'{"error":"temporarily unavailable"}'

    result = TypeSafeClient(transport=transport, require_credentials=False).judge({}, _questions())

    assert result["status"] == "servicefailed"
    assert result["failure_class"] == "http-503"
    assert result["response_receipt"]["body"] == '{"error":"temporarily unavailable"}'


def test_response_receipt_redacts_credential_from_service_body(monkeypatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "response-secret")

    def transport(request):
        del request
        return 500, b'{"error":"response-secret"}'

    result = TypeSafeClient(transport=transport).judge({}, _questions())

    assert result["status"] == "servicefailed"
    assert "response-secret" not in json.dumps(result, sort_keys=True)
    assert result["response_receipt"]["body"] == '{"error":"<redacted>"}'


def test_choice_selected_key_cannot_have_zero_probability() -> None:
    def transport(request):
        del request
        payload = _response_payload()
        payload["answers"]["route"]["probabilities"] = {"existing": 0.0, "new": 1.0}
        return 200, json.dumps(payload).encode("utf-8")

    result = TypeSafeClient(transport=transport).judge({}, _questions())

    assert result["status"] == "invalid"
    assert "answers.route.choice:probability" in result["violations"]


def test_score_legend_must_match_requested_levels() -> None:
    def transport(request):
        del request
        payload = _response_payload()
        payload["answers"]["evidence"]["legend"] = {"0": "only level"}
        payload["answers"]["evidence"]["score"] = 0.0
        payload["answers"]["evidence"]["probabilities"] = {"0": 1.0}
        return 200, json.dumps(payload).encode("utf-8")

    result = TypeSafeClient(transport=transport).judge({}, _questions())

    assert result["status"] == "invalid"
    assert "answers.evidence.legend" in result["violations"]


def test_request_rejects_non_documented_state_instruction_and_criterion_types() -> None:
    called = False

    def transport(request):
        nonlocal called
        called = True
        del request
        return 200, json.dumps(_response_payload()).encode("utf-8")

    client = TypeSafeClient(transport=transport)
    result = client.judge(
        42,
        {
            "route": ChoiceQuestion(
                instructions=42,
                criteria={"existing": {"nested": "value"}},
            )
        },
    )

    assert result["status"] == "invalid"
    assert "state:documented-type" in result["violations"]
    assert "questions.route.instructions:documented-type" in result["violations"]
    assert "questions.route.criteria.existing:criterion-text-or-null" in result["violations"]
    assert called is False


def test_response_receipt_redacts_secret_from_headers_and_request_id(monkeypatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "header-secret")

    def transport(request):
        del request
        return (
            503,
            b'{"error":"temporarily unavailable"}',
            {"x-request-id": "req-header-secret", "x-provider": "header-secret"},
        )

    result = TypeSafeClient(transport=transport).judge({}, _questions())
    receipt = result["response_receipt"]

    assert result["status"] == "servicefailed"
    assert "header-secret" not in json.dumps(receipt, sort_keys=True)
    assert receipt["headers"] == {"x-request-id": "req-<redacted>", "x-provider": "<redacted>"}
    assert receipt["request_id"] == "req-<redacted>"
