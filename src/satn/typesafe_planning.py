"""Small, receipt-first HTTP boundary for TypeSafe System One judgments.

The module intentionally keeps the provider boundary independent of the rest of
the compiler.  It sends one JSON state and question map, validates the typed
answers returned by the documented HTTP API, and returns a JSON-compatible
receipt.  It never turns an unavailable service into an answer.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
type QuestionInput = ChoiceQuestion | NoulQuestion | ScoreQuestion | Mapping[str, Any]
type TransportResult = (
    HTTPResponse | tuple[int, bytes | str] | tuple[int, bytes | str, Mapping[str, str]]
)
type Transport = Callable[[urllib.request.Request], TransportResult]

TYPE_SAFE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_CREDENTIAL_ENV = "TYPESAFE_API_KEY"
DEFAULT_CREDENTIAL_PATH = "~/.config/typesafe/api-key"
_RESULT_TRANSFORMATION = "typesafe-typed-result/v1"


class TypeSafeStatus(StrEnum):
    """JSON statuses returned by :meth:`TypeSafeClient.judge`."""

    ANSWERED = "answered"
    UNAVAILABLE = "unavailable"
    SERVICEFAILED = "servicefailed"
    INVALID = "invalid"


class TypeSafeValidationError(ValueError):
    """Raised internally when a request cannot satisfy the HTTP contract."""

    def __init__(self, violations: Sequence[str]):
        self.violations = tuple(violations)
        super().__init__("; ".join(self.violations))


def _json_copy(value: object, path: str) -> JSONValue:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise TypeSafeValidationError((f"{path}:not-json",)) from error
    return copied


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeSafeValidationError((f"{path}:required-text",))
    return value


def _documented_value(value: object) -> bool:
    """Return whether a value has the documented JSON input shape."""

    return isinstance(value, (str, Mapping, list))


def _criterion_value(value: object) -> bool:
    return value is None or isinstance(value, str)


def _question_payload(question: QuestionInput, path: str) -> dict[str, JSONValue]:
    if hasattr(question, "as_payload"):
        question = question.as_payload()  # type: ignore[union-attr]
    if not isinstance(question, Mapping):
        raise TypeSafeValidationError((f"{path}:question-object",))
    raw = _json_copy(dict(question), path)
    if not isinstance(raw, dict):  # pragma: no cover - _json_copy preserves dict input.
        raise TypeSafeValidationError((f"{path}:question-object",))
    question_type = raw.get("type")
    instructions = raw.get("instructions")
    if question_type not in {"choice", "noul", "score"}:
        raise TypeSafeValidationError((f"{path}.type:unsupported",))
    violations: list[str] = []
    if not _documented_value(instructions):
        violations.append(f"{path}.instructions:documented-type")
    criteria = raw.get("criteria")
    if question_type == "choice":
        if not isinstance(criteria, dict) or not criteria:
            violations.append(f"{path}.criteria:non-empty-object")
        elif len(criteria) > 255:
            violations.append(f"{path}.criteria:too-many-options")
        else:
            for key, value in criteria.items():
                if not isinstance(key, str) or not key:
                    violations.append(f"{path}.criteria:invalid-key")
                if not _criterion_value(value):
                    violations.append(f"{path}.criteria.{key}:criterion-text-or-null")
    elif question_type == "noul":
        if criteria is not None and (
            not isinstance(criteria, dict) or any(key not in {"true", "false"} for key in criteria)
        ):
            violations.append(f"{path}.criteria:invalid-noul-criteria")
        elif isinstance(criteria, dict):
            for key, value in criteria.items():
                if not _criterion_value(value):
                    violations.append(f"{path}.criteria.{key}:criterion-text-or-null")
    elif not isinstance(criteria, list) or len(criteria) < 2:
        violations.append(f"{path}.criteria:at-least-two-levels")
    elif any(not _criterion_value(value) for value in criteria):
        violations.append(f"{path}.criteria:criterion-text-or-null")
    if violations:
        raise TypeSafeValidationError(violations)
    return raw


@dataclass(frozen=True, slots=True)
class ChoiceQuestion:
    """A TypeSafe Choice over a caller-owned finite option map."""

    instructions: JSONValue
    criteria: Mapping[str, JSONValue | None]
    type: Literal["choice"] = "choice"

    def as_payload(self) -> dict[str, JSONValue]:
        return {
            "type": self.type,
            "instructions": _json_copy(self.instructions, "instructions"),
            "criteria": _json_copy(dict(self.criteria), "criteria"),
        }


@dataclass(frozen=True, slots=True)
class NoulQuestion:
    """A TypeSafe Noul whose wire value is the probability of yes."""

    instructions: JSONValue
    criteria: Mapping[str, JSONValue] | None = None
    type: Literal["noul"] = "noul"

    def as_payload(self) -> dict[str, JSONValue]:
        payload: dict[str, JSONValue] = {
            "type": self.type,
            "instructions": _json_copy(self.instructions, "instructions"),
        }
        if self.criteria is not None:
            payload["criteria"] = _json_copy(dict(self.criteria), "criteria")
        return payload


@dataclass(frozen=True, slots=True)
class ScoreQuestion:
    """A TypeSafe Score over ordered level descriptions."""

    instructions: JSONValue
    criteria: Sequence[JSONValue]
    type: Literal["score"] = "score"

    def as_payload(self) -> dict[str, JSONValue]:
        return {
            "type": self.type,
            "instructions": _json_copy(self.instructions, "instructions"),
            "criteria": _json_copy(list(self.criteria), "criteria"),
        }


# These short names mirror the documented primitive names without importing a
# provider SDK or making the compiler depend on one.
Choice = ChoiceQuestion
Noul = NoulQuestion
Score = ScoreQuestion


@dataclass(frozen=True, slots=True)
class TypeSafeRequest:
    """Validated request envelope sent to the documented System One endpoint."""

    state: JSONValue
    questions: Mapping[str, QuestionInput]
    model: str = DEFAULT_MODEL

    def payload(self) -> dict[str, JSONValue]:
        model = _text(self.model, "model")
        state = _json_copy(self.state, "state")
        if not isinstance(self.questions, Mapping) or not self.questions:
            raise TypeSafeValidationError(("questions:non-empty-object",))
        questions: dict[str, JSONValue] = {}
        violations: list[str] = []
        if not _documented_value(state):
            violations.append("state:documented-type")
        for question_id, question in self.questions.items():
            if not isinstance(question_id, str) or not question_id.strip():
                violations.append("questions:<id>:required-text")
                continue
            try:
                questions[question_id] = _question_payload(question, f"questions.{question_id}")
            except TypeSafeValidationError as error:
                violations.extend(error.violations)
        if violations:
            raise TypeSafeValidationError(violations)
        return {"state": state, "model": model, "questions": questions}

    def body(self) -> str:
        return json.dumps(
            self.payload(),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class TypeSafeProvider:
    """Explicit provider configuration; no product endpoint is guessed."""

    provider: str = "typesafe"
    endpoint: str = TYPE_SAFE_ENDPOINT
    model: str = DEFAULT_MODEL
    credential_env: str = DEFAULT_CREDENTIAL_ENV
    credential_path: str | Path | None = DEFAULT_CREDENTIAL_PATH

    def __post_init__(self) -> None:
        _text(self.provider, "provider")
        _text(self.endpoint, "endpoint")
        _text(self.model, "model")


ProviderConfig = TypeSafeProvider


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    """Minimal response shape accepted from a deterministic test transport."""

    status_code: int
    body: bytes | str
    headers: Mapping[str, str] = ()


def _normalise_response(response: TransportResult | object) -> HTTPResponse:
    if isinstance(response, HTTPResponse):
        return response
    if isinstance(response, tuple) and len(response) in {2, 3}:
        status, body = response[0], response[1]
        headers = response[2] if len(response) == 3 else {}
        if isinstance(status, bool) or not isinstance(status, int):
            raise TypeError("transport status must be an integer")
        if not isinstance(body, (bytes, str)):
            raise TypeError("transport body must be bytes or text")
        if not isinstance(headers, Mapping):
            raise TypeError("transport headers must be a mapping")
        return HTTPResponse(status, body, {str(k): str(v) for k, v in headers.items()})
    raise TypeError("transport must return HTTPResponse or (status, body[, headers])")


def _response_bytes(body: bytes | str) -> bytes:
    return body if isinstance(body, bytes) else body.encode("utf-8")


def _safe_headers(
    headers: Mapping[str, object],
    secret: str | None = None,
) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in headers.items():
        lowered = str(key).lower()
        if any(
            sensitive_name in lowered
            for sensitive_name in ("authorization", "api-key", "cookie", "token")
        ):
            continue
        rendered = str(value)
        if secret:
            rendered = rendered.replace(secret, "<redacted>")
        safe[str(key)] = rendered
    return safe


def _header(headers: Mapping[str, object], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def _receipt_body(
    body: bytes | str,
    secret: str | None = None,
) -> tuple[str, str, bool]:
    raw = _response_bytes(body)
    digest = hashlib.sha256(raw).hexdigest()
    original_text = raw.decode("utf-8", errors="replace")
    text = original_text
    if secret:
        text = text.replace(secret, "<redacted>")
    return text, digest, text != original_text


def _base_result(
    status: str,
    *,
    provider: str,
    model: str,
    request: dict[str, JSONValue] | None,
    request_receipt: dict[str, JSONValue] | None,
    response: dict[str, JSONValue] | None = None,
    response_receipt: dict[str, JSONValue] | None = None,
    violations: Sequence[str] = (),
    failure_class: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": status,
        "provider": provider,
        "model": model,
        "usage": None,
        "answers": {},
        "request": request,
        "response": response,
        "request_receipt": request_receipt,
        "response_receipt": response_receipt,
    }
    if violations:
        result["violations"] = list(violations)
    if failure_class is not None:
        result["failure_class"] = failure_class
    return result


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _compact_answer(question: Mapping[str, JSONValue], answer: object) -> dict[str, JSONValue]:
    if not isinstance(answer, Mapping):  # pragma: no cover - validated before projection.
        return {}
    answer_type = question.get("type")
    if answer_type == "choice":
        probabilities = answer.get("probabilities")
        compact_probabilities = (
            {
                str(key): value
                for key, value in sorted(probabilities.items(), key=lambda item: str(item[0]))
            }
            if isinstance(probabilities, Mapping)
            else {}
        )
        return {
            "type": "choice",
            "choice": answer.get("choice"),
            "probabilities": compact_probabilities,
            "confidence": answer.get("confidence"),
        }
    if answer_type == "score":
        probabilities = answer.get("probabilities")
        compact_probabilities = (
            {
                str(key): value
                for key, value in sorted(probabilities.items(), key=lambda item: str(item[0]))
            }
            if isinstance(probabilities, Mapping)
            else {}
        )
        return {
            "type": "score",
            "score": answer.get("score"),
            "probabilities": compact_probabilities,
            "confidence": answer.get("confidence"),
        }
    return {"type": "noul", "noul": answer.get("noul")}


def _compact_answers(
    request: Mapping[str, JSONValue], answers: Mapping[str, object]
) -> dict[str, JSONValue]:
    questions = request.get("questions")
    if not isinstance(questions, Mapping):  # pragma: no cover - validated before projection.
        return {}
    return {
        str(question_id): _compact_answer(question, answers[question_id])
        for question_id, question in questions.items()
        if isinstance(question, Mapping) and question_id in answers
    }


def _answer_binding(
    request: Mapping[str, JSONValue],
    response: Mapping[str, JSONValue],
    request_receipt: Mapping[str, JSONValue],
    response_receipt: Mapping[str, JSONValue],
) -> dict[str, JSONValue]:
    request_hash = request_receipt.get("body_sha256")
    response_hash = response_receipt.get("body_sha256")
    return {
        "transformation": _RESULT_TRANSFORMATION,
        "request_fingerprint": request_hash,
        "question_fingerprint": _canonical_hash(request.get("questions", {})),
        "requested_model": request.get("model"),
        "actual_model": response.get("model"),
        "exchange_ref": {
            "request_body_sha256": request_hash,
            "response_body_sha256": response_hash,
        },
    }


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _probability_map(
    answer_path: str,
    value: object,
    expected: set[str],
    violations: list[str],
) -> None:
    if not isinstance(value, Mapping):
        violations.append(f"{answer_path}:object")
        return
    actual = {str(key) for key in value}
    if actual != expected:
        violations.append(answer_path)
    total = 0.0
    for key, probability in value.items():
        item_path = f"{answer_path}.{key}"
        if not isinstance(key, str) or not _number(probability) or not 0 <= probability <= 1:
            violations.append(item_path)
        elif isinstance(probability, (int, float)):
            total += float(probability)
    if actual == expected and not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-9):
        violations.append(answer_path)


def _validate_answer(
    question_id: str,
    question: Mapping[str, JSONValue],
    answer: object,
    violations: list[str],
) -> None:
    path = f"answers.{question_id}"
    if not isinstance(answer, Mapping):
        violations.append(f"{path}:object")
        return
    expected_type = question.get("type")
    if answer.get("type") != expected_type:
        violations.append(f"{path}.type")
        return
    if expected_type == "choice":
        criteria = question.get("criteria")
        offered = set(criteria) if isinstance(criteria, Mapping) else set()
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in offered:
            violations.append(f"{path}.choice")
        probabilities = answer.get("probabilities")
        _probability_map(f"{path}.probabilities", probabilities, offered, violations)
        if (
            isinstance(choice, str)
            and isinstance(probabilities, Mapping)
            and choice in probabilities
            and _number(probabilities[choice])
            and probabilities[choice] <= 0
        ):
            violations.append(f"{path}.choice:probability")
        confidence = answer.get("confidence")
        if not _number(confidence) or not 0 <= confidence <= 1:
            violations.append(f"{path}.confidence")
    elif expected_type == "noul":
        noul = answer.get("noul")
        if not _number(noul) or not 0 <= noul <= 1:
            violations.append(f"{path}.noul")
    elif expected_type == "score":
        criteria = question.get("criteria")
        expected_levels = criteria if isinstance(criteria, list) else []
        legend = answer.get("legend")
        if not isinstance(legend, Mapping) or not legend:
            violations.append(f"{path}.legend")
            legend_keys: set[str] = set()
        else:
            legend_keys = {str(key) for key in legend}
            expected_keys = {str(index) for index in range(len(expected_levels))}
            if legend_keys != expected_keys or any(
                legend.get(str(index)) != level for index, level in enumerate(expected_levels)
            ):
                violations.append(f"{path}.legend")
        score = answer.get("score")
        if (
            not _number(score)
            or not isinstance(legend, Mapping)
            or not legend
            or not (0 <= score <= len(expected_levels) - 1)
        ):
            violations.append(f"{path}.score")
        _probability_map(
            f"{path}.probabilities",
            answer.get("probabilities"),
            legend_keys,
            violations,
        )
        confidence = answer.get("confidence")
        if not _number(confidence) or not 0 <= confidence <= 1:
            violations.append(f"{path}.confidence")


def _validate_response(
    request: Mapping[str, JSONValue],
    payload: object,
) -> list[str]:
    violations: list[str] = []
    if not isinstance(payload, Mapping):
        return ["response:object"]
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        violations.append("response.model")
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        violations.append("response.answers")
        answers = {}
    questions = request.get("questions")
    if not isinstance(questions, Mapping):
        violations.append("request.questions")
        questions = {}
    expected_ids = set(questions)
    actual_ids = set(answers)
    for question_id in sorted(expected_ids - actual_ids):
        violations.append(f"answers.{question_id}:missing")
    for question_id in sorted(actual_ids - expected_ids):
        violations.append(f"answers.{question_id}:unexpected")
    for question_id in sorted(expected_ids & actual_ids):
        question = questions[question_id]
        if isinstance(question, Mapping):
            _validate_answer(question_id, question, answers[question_id], violations)
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        violations.append("response.usage")
    else:
        for name in ("input_tokens", "output_tokens"):
            token_count = usage.get(name)
            if isinstance(token_count, bool) or not isinstance(token_count, int) or token_count < 0:
                violations.append(f"usage.{name}")
    return violations


class TypeSafeClient:
    """Call TypeSafe's HTTP API with a configurable, process-local credential."""

    def __init__(
        self,
        *,
        provider: str | TypeSafeProvider = "typesafe",
        model: str | None = None,
        endpoint: str | None = None,
        transport: Transport | None = None,
        require_credentials: bool | None = None,
        timeout: float | None = None,
    ) -> None:
        if isinstance(provider, TypeSafeProvider):
            if model is not None or endpoint is not None:
                provider = TypeSafeProvider(
                    provider=provider.provider,
                    endpoint=endpoint or provider.endpoint,
                    model=model or provider.model,
                    credential_env=provider.credential_env,
                    credential_path=provider.credential_path,
                )
            self.provider = provider
        else:
            self.provider = TypeSafeProvider(
                provider=provider,
                endpoint=endpoint or TYPE_SAFE_ENDPOINT,
                model=model or DEFAULT_MODEL,
            )
        self.transport = transport
        self.require_credentials = (
            transport is None if require_credentials is None else require_credentials
        )
        self.timeout = timeout

    def _credential(self) -> str | None:
        env_name = self.provider.credential_env
        if env_name:
            environment_value = os.environ.get(env_name)
            if environment_value and environment_value.strip():
                return environment_value.strip()
        path = self.provider.credential_path
        if path is None:
            return None
        try:
            file_value = Path(path).expanduser().read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return None
        return file_value or None

    def _request_receipt(self, body: str, headers: Mapping[str, str]) -> dict[str, JSONValue]:
        raw = body.encode("utf-8")
        return {
            "method": "POST",
            "url": self.provider.endpoint,
            "headers": _safe_headers(headers),
            "body": body,
            "body_sha256": hashlib.sha256(raw).hexdigest(),
        }

    def _send(self, request: urllib.request.Request) -> HTTPResponse:
        if self.transport is not None:
            return _normalise_response(self.transport(request))
        try:
            if self.timeout is None:
                response_context = urllib.request.urlopen(request)
            else:
                response_context = urllib.request.urlopen(request, timeout=self.timeout)
            with response_context as response:
                return HTTPResponse(
                    int(response.status),
                    response.read(),
                    {str(k): str(v) for k, v in response.headers.items()},
                )
        except urllib.error.HTTPError as error:
            return HTTPResponse(
                int(error.code),
                error.read(),
                {str(k): str(v) for k, v in error.headers.items()} if error.headers else {},
            )

    def judge(
        self,
        state: JSONValue,
        questions: Mapping[str, QuestionInput],
    ) -> dict[str, object]:
        """Return one JSON-compatible typed judgment receipt."""

        try:
            request = TypeSafeRequest(state, questions, self.provider.model)
            request_payload = request.payload()
            request_body = request.body()
        except TypeSafeValidationError as error:
            return _base_result(
                TypeSafeStatus.INVALID,
                provider=self.provider.provider,
                model=self.provider.model,
                request=None,
                request_receipt=None,
                violations=error.violations,
            )

        credential = self._credential()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        request_receipt = self._request_receipt(request_body, headers)
        if self.require_credentials and not credential:
            return _base_result(
                TypeSafeStatus.UNAVAILABLE,
                provider=self.provider.provider,
                model=self.provider.model,
                request=request_payload,
                request_receipt=request_receipt,
                failure_class="missing-credential",
            )

        http_request = urllib.request.Request(
            self.provider.endpoint,
            data=request_body.encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            response = self._send(http_request)
        except (urllib.error.URLError, TimeoutError, OSError):
            return _base_result(
                TypeSafeStatus.UNAVAILABLE,
                provider=self.provider.provider,
                model=self.provider.model,
                request=request_payload,
                request_receipt=request_receipt,
                failure_class="transport-unavailable",
            )
        except (TypeError, ValueError):
            return _base_result(
                TypeSafeStatus.SERVICEFAILED,
                provider=self.provider.provider,
                model=self.provider.model,
                request=request_payload,
                request_receipt=request_receipt,
                failure_class="transport-invalid",
            )

        response_body, response_hash, response_body_redacted = _receipt_body(
            response.body, credential
        )
        response_receipt: dict[str, JSONValue] = {
            "status_code": response.status_code,
            "headers": _safe_headers(response.headers, credential),
            "body": response_body,
            "body_sha256": response_hash,
            "body_redacted": response_body_redacted,
            "request_id": _header(response.headers, "x-request-id")
            or _header(response.headers, "request-id"),
        }
        if response_receipt["request_id"] is not None and credential:
            response_receipt["request_id"] = str(response_receipt["request_id"]).replace(
                credential, "<redacted>"
            )
        try:
            response_payload = json.loads(response_body)
        except json.JSONDecodeError:
            response_payload = None
        if not 200 <= response.status_code < 300:
            return _base_result(
                TypeSafeStatus.SERVICEFAILED,
                provider=self.provider.provider,
                model=self.provider.model,
                request=request_payload,
                request_receipt=request_receipt,
                response=response_payload if isinstance(response_payload, dict) else None,
                response_receipt=response_receipt,
                failure_class=f"http-{response.status_code}",
            )
        if response_payload is None:
            return _base_result(
                TypeSafeStatus.INVALID,
                provider=self.provider.provider,
                model=self.provider.model,
                request=request_payload,
                request_receipt=request_receipt,
                response=None,
                response_receipt=response_receipt,
                violations=("response:invalid-json",),
            )
        violations = _validate_response(request_payload, response_payload)
        if violations:
            return _base_result(
                TypeSafeStatus.INVALID,
                provider=self.provider.provider,
                model=self.provider.model,
                request=request_payload,
                request_receipt=request_receipt,
                response=response_payload if isinstance(response_payload, dict) else None,
                response_receipt=response_receipt,
                violations=violations,
            )
        assert isinstance(response_payload, Mapping)  # validated above
        usage = response_payload["usage"]
        answers = response_payload["answers"]
        assert isinstance(answers, Mapping)  # validated above
        return {
            "status": TypeSafeStatus.ANSWERED,
            "provider": self.provider.provider,
            "model": response_payload["model"],
            "requested_model": self.provider.model,
            "usage": usage,
            "answers": _compact_answers(request_payload, answers),
            "binding": _answer_binding(
                request_payload,
                response_payload,
                request_receipt,
                response_receipt,
            ),
            "request": request_payload,
            "response": response_payload,
            "request_receipt": request_receipt,
            "response_receipt": response_receipt,
        }


__all__ = [
    "DEFAULT_MODEL",
    "TYPE_SAFE_ENDPOINT",
    "Choice",
    "ChoiceQuestion",
    "HTTPResponse",
    "Noul",
    "NoulQuestion",
    "ProviderConfig",
    "Score",
    "ScoreQuestion",
    "TypeSafeClient",
    "TypeSafeProvider",
    "TypeSafeRequest",
    "TypeSafeStatus",
    "TypeSafeValidationError",
]
