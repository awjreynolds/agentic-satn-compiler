"""Evaluate typed claim classifications against a frozen source-backed fixture.

Preparation is the default operation.  ``--execute`` explicitly sends one
request per source; the saved summary can then be read without contacting the
provider again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

from satn.typesafe_planning import (
    DEFAULT_MODEL,
    TYPE_SAFE_ENDPOINT,
    ChoiceQuestion,
    TypeSafeClient,
    TypeSafeProvider,
    TypeSafeRequest,
)

SCHEMA_VERSION = "typesafe-claim-experiment/v1"
LABELS = ("supports", "contradicts", "does_not_establish")
_REDACTED_KEYS = {"authorization", "api-key", "cookie", "token"}
ClientFactory = Callable[..., Any]


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "<redacted>"
                if any(name in str(key).casefold() for name in _REDACTED_KEYS)
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return _json_safe(value)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _required_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be non-empty text")
    return value


def _validate_fixture(fixture: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    question = fixture.get("question")
    if not isinstance(question, Mapping):
        raise ValueError("question must be an object")
    _required_text(question.get("instructions"), "question.instructions")
    criteria = question.get("criteria")
    if not isinstance(criteria, Mapping) or set(criteria) != set(LABELS):
        raise ValueError("question.criteria must contain the three classifier labels")
    if any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in criteria.items()
    ):
        raise ValueError("question.criteria keys and values must be text")
    sources = fixture.get("sources")
    cases = fixture.get("cases")
    if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)):
        raise ValueError("sources must be a list")
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)):
        raise ValueError("cases must be a list")

    source_ids: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            raise ValueError(f"sources[{index}] must be an object")
        source_id = _required_text(source.get("id"), f"sources[{index}].id")
        if source_id in source_ids:
            raise ValueError(f"duplicate source id: {source_id}")
        source_ids.add(source_id)
        for field in ("title", "url", "retrieved_at", "locator", "excerpt"):
            _required_text(source.get(field), f"sources[{index}].{field}")
        if "published_at" in source and source["published_at"] is not None:
            _required_text(source["published_at"], f"sources[{index}].published_at")

    case_ids: set[str] = set()
    valid_cases: list[dict[str, object]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise ValueError(f"cases[{index}] must be an object")
        case_id = _required_text(case.get("id"), f"cases[{index}].id")
        if case_id in case_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        case_ids.add(case_id)
        source_id = _required_text(case.get("source_id"), f"cases[{index}].source_id")
        if source_id not in source_ids:
            raise ValueError(f"cases[{index}].source_id is unknown: {source_id}")
        _required_text(case.get("claim"), f"cases[{index}].claim")
        expected = _required_text(case.get("expected"), f"cases[{index}].expected")
        if expected not in LABELS:
            raise ValueError(f"cases[{index}].expected is not a classifier label")
        _required_text(case.get("rationale"), f"cases[{index}].rationale")
        valid_cases.append({str(key): _json_safe(value) for key, value in case.items()})
    return tuple(valid_cases)


def _source_state(
    source: Mapping[str, object], cases: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    state_source = {
        "title": source["title"],
        "published_at": source.get("published_at"),
        "excerpt": source["excerpt"],
    }
    return {
        "source": state_source,
        "claims": {str(case["id"]): {"text": str(case["claim"])} for case in cases},
    }


def _questions(
    instructions: str,
    criteria: Mapping[str, object],
    cases: Sequence[Mapping[str, object]],
) -> dict[str, ChoiceQuestion]:
    return {
        str(case["id"]): ChoiceQuestion(
            instructions=(f"{instructions} The claim text is at state.claims.{case['id']}.text."),
            criteria=dict(criteria),
        )
        for case in cases
    }


def _answer_record(
    case: Mapping[str, object],
    answers: Mapping[str, object],
) -> dict[str, object]:
    case_id = str(case["id"])
    raw = answers.get(case_id)
    if not isinstance(raw, Mapping) or raw.get("type") != "choice":
        return {
            "case_id": case_id,
            "expected": case["expected"],
            "status": "missing",
            "selected": None,
            "probabilities": None,
            "confidence": None,
            "answer": None,
            "correct": None,
        }
    selected = raw.get("choice")
    is_answered = isinstance(selected, str) and selected in LABELS
    return {
        "case_id": case_id,
        "expected": case["expected"],
        "status": "answered" if is_answered else "missing",
        "selected": selected if is_answered else None,
        "probabilities": _json_safe(raw.get("probabilities")),
        "confidence": raw.get("confidence"),
        "answer": _json_safe(dict(raw)),
        "correct": selected == case["expected"] if is_answered else None,
    }


def _empty_confusion() -> dict[str, dict[str, int]]:
    return {expected: {selected: 0 for selected in LABELS} for expected in LABELS}


def _metrics(results: Sequence[Mapping[str, object]]) -> dict[str, object]:
    confusion = _empty_confusion()
    answered = correct = incorrect = missing = 0
    for result in results:
        status = result.get("status")
        if status != "answered":
            missing += 1
            continue
        answered += 1
        expected = str(result["expected"])
        selected = str(result["selected"])
        confusion[expected][selected] += 1
        if result.get("correct") is True:
            correct += 1
        else:
            incorrect += 1
    return {
        "answered_count": answered,
        "correct_count": correct,
        "incorrect_count": incorrect,
        "missing_count": missing,
        "confusion_matrix": confusion,
    }


def _new_output_root(output_root: Path) -> None:
    if output_root.exists():
        raise FileExistsError(f"experiment output already exists: {output_root}")
    output_root.mkdir(parents=True)
    (output_root / "exchanges").mkdir()


def _exchange_artifact(
    source: Mapping[str, object],
    request_body: str,
    provider_result: Mapping[str, object],
    *,
    endpoint: str,
    execute: bool,
) -> dict[str, object]:
    request_body_sha256 = _sha256_bytes(request_body.encode("utf-8"))
    request_receipt = provider_result.get("request_receipt") if execute else None
    if not isinstance(request_receipt, Mapping) or not isinstance(request_receipt.get("body"), str):
        receipt_fields = dict(request_receipt) if isinstance(request_receipt, Mapping) else {}
        request_receipt = {
            **receipt_fields,
            "method": receipt_fields.get("method", "POST"),
            "url": receipt_fields.get("url", endpoint),
            "body": request_body,
            "body_sha256": request_body_sha256,
        }
    exchange_result = {
        key: provider_result[key]
        for key in (
            "status",
            "provider",
            "model",
            "requested_model",
            "usage",
            "answers",
            "binding",
            "response_receipt",
            "violations",
            "failure_class",
        )
        if key in provider_result
    }
    source_record = {str(key): value for key, value in source.items() if str(key) != "excerpt"}
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": source["id"],
        "source_record": _redact(source_record),
        "request_receipt": _redact(request_receipt),
        "provider_result": _redact(exchange_result) if execute else None,
    }


def _evaluate(
    fixture: Mapping[str, object],
    output_root: Path,
    *,
    fixture_bytes: bytes,
    execute: bool,
    model: str,
    endpoint: str,
    client_factory: ClientFactory | None,
) -> dict[str, object]:
    cases = _validate_fixture(fixture)
    _new_output_root(output_root)
    sources = fixture["sources"]
    assert isinstance(sources, Sequence)
    source_map = {str(source["id"]): source for source in sources if isinstance(source, Mapping)}
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for case in cases:
        grouped[str(case["source_id"])].append(case)

    client: Any = None
    if execute:
        provider = TypeSafeProvider(endpoint=endpoint, model=model)
        client = (
            client_factory(provider=provider, model=model, endpoint=endpoint)
            if client_factory is not None
            else TypeSafeClient(provider=provider, require_credentials=True)
        )

    all_case_results: list[dict[str, object]] = []
    source_results: list[dict[str, object]] = []
    for source_id, source_cases in grouped.items():
        source = source_map[source_id]
        state = _source_state(source, source_cases)
        question_definition = fixture["question"]
        assert isinstance(question_definition, Mapping)
        questions = _questions(
            str(question_definition["instructions"]),
            question_definition["criteria"],
            source_cases,
        )
        request = TypeSafeRequest(state, questions, model)
        request.payload()
        request_body = request.body()
        started = perf_counter()
        provider_result: Mapping[str, object] = {}
        if execute:
            provider_result = client.judge(state, questions)
        elapsed = perf_counter() - started
        answers = provider_result.get("answers")
        answer_mapping = answers if isinstance(answers, Mapping) else {}
        case_results = [_answer_record(case, answer_mapping) for case in source_cases]
        all_case_results.extend(case_results)
        exchange_name = f"{source_id}.json"
        exchange_ref = f"exchanges/{exchange_name}"
        exchange = _exchange_artifact(
            source,
            request_body,
            provider_result,
            endpoint=endpoint,
            execute=execute,
        )
        _write_json(output_root / exchange_ref, exchange)
        source_results.append(
            {
                "source_id": source_id,
                "case_ids": [str(case["id"]) for case in source_cases],
                "status": provider_result.get("status", "prepared") if execute else "prepared",
                "provider": provider_result.get("provider") if execute else None,
                "model": provider_result.get("model") if execute else model,
                "usage": _json_safe(provider_result.get("usage")) if execute else None,
                "latency_seconds": elapsed if execute else None,
                "request_body_sha256": exchange["request_receipt"]["body_sha256"],
                "response_body_sha256": (
                    provider_result.get("response_receipt", {}).get("body_sha256")
                    if isinstance(provider_result.get("response_receipt"), Mapping)
                    else None
                ),
                "exchange_ref": exchange_ref,
                "answers": case_results,
            }
        )

    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "executed" if execute else "prepared",
        "provider_called": execute,
        "model": model,
        "endpoint": endpoint,
        "fixture_sha256": _sha256_bytes(fixture_bytes),
        "code_sha256": _sha256_bytes(Path(__file__).read_bytes()),
        "source_count": len(source_results),
        "case_count": len(cases),
        "source_results": source_results,
        "metrics": _metrics(all_case_results),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "fixture_sha256": summary["fixture_sha256"],
        "code_sha256": summary["code_sha256"],
        "fixture_ref": "fixture.json",
        "summary_ref": "summary.json",
        "exchange_refs": [result["exchange_ref"] for result in source_results],
    }
    _write_json(output_root / "summary.json", summary)
    _write_json(output_root / "run-manifest.json", manifest)
    _write_json(output_root / "fixture.json", _redact(fixture))
    return summary


def evaluate_fixture(
    fixture: Mapping[str, object],
    output_root: Path,
    *,
    execute: bool = False,
    model: str = DEFAULT_MODEL,
    endpoint: str = TYPE_SAFE_ENDPOINT,
    client_factory: ClientFactory | None = None,
) -> dict[str, object]:
    """Prepare or execute one frozen fixture and save its machine-readable result."""

    fixture_json = json.dumps(
        fixture,
        ensure_ascii=True,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    return _evaluate(
        fixture,
        Path(output_root),
        fixture_bytes=fixture_json,
        execute=execute,
        model=model,
        endpoint=endpoint,
        client_factory=client_factory,
    )


def prepare_experiment(
    fixture_path: Path,
    output_root: Path,
    *,
    execute: bool = False,
    model: str = DEFAULT_MODEL,
    endpoint: str = TYPE_SAFE_ENDPOINT,
    client_factory: ClientFactory | None = None,
) -> dict[str, object]:
    """Load a fixture by exact bytes, then prepare or explicitly execute it."""

    fixture_bytes = Path(fixture_path).read_bytes()
    fixture = json.loads(fixture_bytes)
    if not isinstance(fixture, Mapping):
        raise ValueError("fixture root must be an object")
    return _evaluate(
        fixture,
        Path(output_root),
        fixture_bytes=fixture_bytes,
        execute=execute,
        model=model,
        endpoint=endpoint,
        client_factory=client_factory,
    )


def read_saved_report(output_root: Path) -> dict[str, object]:
    """Read a saved summary without contacting TypeSafe or recomputing answers."""

    summary = json.loads((Path(output_root) / "summary.json").read_text(encoding="utf-8"))
    if not isinstance(summary, dict):
        raise ValueError("saved summary must be an object")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--endpoint", default=TYPE_SAFE_ENDPOINT)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="send one provider request per source; preparation is otherwise offline",
    )
    args = parser.parse_args(argv)
    summary = prepare_experiment(
        args.fixture,
        args.output_root,
        execute=args.execute,
        model=args.model,
        endpoint=args.endpoint,
    )
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
