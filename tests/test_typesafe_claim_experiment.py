from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import ClassVar

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "experiments" / "evaluate_typesafe_claims.py"
SPEC = importlib.util.spec_from_file_location("evaluate_typesafe_claims", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
experiment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(experiment)


def _fixture() -> dict[str, object]:
    return {
        "question": {
            "instructions": "Classify each claim using only the supplied source passage.",
            "criteria": {
                "supports": "The passage supports the claim as stated.",
                "contradicts": "The passage contradicts the claim as stated.",
                "does_not_establish": "The passage does not establish the claim as stated.",
            },
        },
        "sources": [
            {
                "id": "src-alpha",
                "title": "Alpha transport plan",
                "url": "https://example.test/alpha",
                "published_at": "2025-01-01",
                "retrieved_at": "2026-09-20",
                "locator": "p. 4",
                "excerpt": "The route is proposed for investigation in 2025.",
            },
            {
                "id": "src-beta",
                "title": "Beta transport plan",
                "url": "https://example.test/beta",
                "retrieved_at": "2026-09-20",
                "locator": "p. 8",
                "excerpt": "The route opened to cycling in 2024.",
            },
        ],
        "cases": [
            {
                "id": "opaque-a",
                "source_id": "src-alpha",
                "claim": "The route is proposed for investigation.",
                "expected": "supports",
                "rationale": "The source states that it is proposed for investigation.",
            },
            {
                "id": "opaque-b",
                "source_id": "src-alpha",
                "claim": "The route is already open to cycling.",
                "expected": "does_not_establish",
                "rationale": "The passage does not state that the route opened.",
            },
            {
                "id": "opaque-c",
                "source_id": "src-beta",
                "claim": "The route opened to cycling in 2024.",
                "expected": "supports",
                "rationale": "The source states the opening year.",
            },
        ],
    }


class _FakeClient:
    calls: ClassVar[list[tuple[dict[str, object], dict[str, object]]]] = []

    def judge(self, state: dict[str, object], questions: dict[str, object]) -> dict[str, object]:
        self.calls.append((state, questions))
        answers = {
            "opaque-a": {
                "type": "choice",
                "choice": "supports",
                "probabilities": {
                    "supports": 0.9,
                    "contradicts": 0.05,
                    "does_not_establish": 0.05,
                },
                "confidence": 0.9,
            },
            "opaque-b": {
                "type": "choice",
                "choice": "contradicts",
                "probabilities": {
                    "supports": 0.1,
                    "contradicts": 0.7,
                    "does_not_establish": 0.2,
                },
                "confidence": 0.7,
            },
            "opaque-c": {
                "type": "choice",
                "choice": "supports",
                "probabilities": {
                    "supports": 0.8,
                    "contradicts": 0.1,
                    "does_not_establish": 0.1,
                },
                "confidence": 0.8,
            },
        }
        selected = {case_id: answers[case_id] for case_id in questions}
        return {
            "status": "answered",
            "provider": "fake",
            "model": "jev-test",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "answers": selected,
            "request_receipt": {"body_sha256": f"request-{len(self.calls)}"},
            "response_receipt": {"body_sha256": f"response-{len(self.calls)}"},
        }


class _PartialClient:
    calls = 0

    def judge(self, state: dict[str, object], questions: dict[str, object]) -> dict[str, object]:
        del state
        type(self).calls += 1
        first_id = next(iter(questions))
        return {
            "status": "answered",
            "provider": "fake",
            "model": "jev-test",
            "usage": {"input_tokens": 4, "output_tokens": 2},
            "answers": {
                first_id: {
                    "type": "choice",
                    "choice": "supports",
                    "probabilities": {
                        "supports": 0.8,
                        "contradicts": 0.1,
                        "does_not_establish": 0.1,
                    },
                    "confidence": 0.8,
                }
            },
        }


def test_execute_batches_by_source_without_label_or_rationale_leaks(tmp_path: Path) -> None:
    _FakeClient.calls = []
    fixture = _fixture()
    output = tmp_path / "claim-classifier"

    result = experiment.evaluate_fixture(
        fixture,
        output,
        execute=True,
        client_factory=lambda **_kwargs: _FakeClient(),
        model="jev-test",
        endpoint="https://provider.invalid",
    )

    assert len(_FakeClient.calls) == 2
    assert result["metrics"] == {
        "answered_count": 3,
        "correct_count": 2,
        "incorrect_count": 1,
        "missing_count": 0,
        "confusion_matrix": {
            "supports": {"supports": 2, "contradicts": 0, "does_not_establish": 0},
            "contradicts": {"supports": 0, "contradicts": 0, "does_not_establish": 0},
            "does_not_establish": {"supports": 0, "contradicts": 1, "does_not_establish": 0},
        },
    }
    first_state, first_questions = _FakeClient.calls[0]
    assert set(first_state) == {"source", "claims"}
    assert first_state["claims"] == {
        "opaque-a": {"text": "The route is proposed for investigation."},
        "opaque-b": {"text": "The route is already open to cycling."},
    }
    assert set(first_questions) == {"opaque-a", "opaque-b"}
    for question in first_questions.values():
        assert question.criteria == fixture["question"]["criteria"]
        assert "state.claims." in question.instructions
    request_text = json.dumps(
        {
            "state": first_state,
            "questions": {
                question_id: question.as_payload()
                for question_id, question in first_questions.items()
            },
        },
        sort_keys=True,
    )
    assert "The source states that it is proposed" not in request_text
    assert "already open to cycling" in request_text
    assert "opaque-a" in request_text
    assert result["provider_called"] is True
    assert (output / "summary.json").is_file()
    assert len(list((output / "exchanges").glob("*.json"))) == 2
    exchange = json.loads((output / "exchanges" / "src-alpha.json").read_text())
    saved_request = json.loads(exchange["request_receipt"]["body"])
    assert saved_request["state"] == first_state
    assert exchange["source_record"]["url"] == "https://example.test/alpha"
    assert "excerpt" not in exchange["source_record"]
    assert "expected" not in exchange["request_receipt"]["body"]
    assert "rationale" not in exchange["request_receipt"]["body"]
    assert exchange["provider_result"]["answers"]["opaque-a"]["choice"] == "supports"


def test_prepare_is_offline_and_saved_report_preserves_missing_answers(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    fixture_bytes = json.dumps(_fixture(), indent=2, sort_keys=True).encode("utf-8")
    fixture_path.write_bytes(fixture_bytes)
    output = tmp_path / "prepared"

    prepared = experiment.prepare_experiment(fixture_path, output)

    assert prepared["status"] == "prepared"
    assert prepared["provider_called"] is False
    assert prepared["metrics"]["answered_count"] == 0
    assert prepared["metrics"]["missing_count"] == 3
    assert prepared["fixture_sha256"] == experiment._sha256_bytes(fixture_bytes)

    loaded = experiment.read_saved_report(output)
    assert loaded == prepared
    with pytest.raises(FileExistsError):
        experiment.prepare_experiment(fixture_path, output)

    _PartialClient.calls = 0
    executed_output = tmp_path / "partial"
    executed = experiment.evaluate_fixture(
        _fixture(),
        executed_output,
        execute=True,
        client_factory=lambda **_kwargs: _PartialClient(),
        model="jev-test",
        endpoint="https://provider.invalid",
    )
    assert _PartialClient.calls == 2
    assert executed["metrics"]["answered_count"] == 2
    assert executed["metrics"]["correct_count"] == 2
    assert executed["metrics"]["missing_count"] == 1
    assert experiment.read_saved_report(executed_output) == executed
