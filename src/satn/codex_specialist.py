"""One-shot Codex process adapter for scoped planning proposals.

The adapter is deliberately a small process boundary.  It sends the frozen
planning task to ``codex exec`` through stdin, asks for a strict JSON proposal,
and leaves operation scope and domain validation to the existing planning
runtime.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from satn.planning_routing import DecisionTask

_OPERATION_KINDS = (
    "propose-connection",
    "revise-connection",
    "select-alignment",
    "record-departure",
    "propose-intervention",
    "request-evidence",
    "request-candidates",
    "record-gap",
)

_PROMPT_PREFIX = """You are a SATN planning specialist.

Use only the frozen planning task below as domain evidence. Do not call tools,
browse, retrieve sources, inspect files, or add facts. Return exactly one JSON
object with this shape: {"proposal":{"operation":{"kind":"...","payload":{...}}}}.
Choose only an operation and identifiers offered by the task. Do not include
prose outside that JSON object or hidden chain of thought. A concise `reason`
payload field is allowed where the operation contract below uses it; keep that
reason limited to the decision basis in the frozen task.

Use these existing operation payload contracts. Include only fields needed for
the selected operation and copy every identifier from the frozen task:
- `select-alignment`: `candidate_id`; optional `obligation_id`.
- `propose-connection` or `revise-connection`: `origin_place_id`,
  `destination_place_id`, `corridor_refs`, and `current_or_future` (`current`,
  `future`, or `unknown`); optional `connection_id`, `status`, `reason`.
- `record-departure`: `source_corridor_refs`, `extent` (`full` or `partial`),
  `affected_geometry_refs` when needed, `outcome`, `evidence_refs`, and a
  concise `reason`; `outcome.kind` is `alternate`, `unresolved`, or `no-loss`,
  with an admitted `candidate_id` or `gap_id` when that outcome needs one.
- `propose-intervention`: `target_refs`, `proposal`, and optional admitted
  `evidence_refs`.
- `request-evidence` or `request-candidates`: `target_refs`, optional
  `request_id`, `claim`, and `reason`; use `evidence_judgment` only when the
  frozen task supplies all of its source and scope facts.
- `record-gap`: `target_refs`, optional `gap_id` and `reason`.
The runtime performs the final typed scope and payload validation. If the task
does not support a safe operation, return the permitted unresolved operation
with identifiers and a concise reason rather than inventing facts.

Frozen planning task:
"""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-blank")
    return value


def _json_copy(value: object) -> object:
    return json.loads(
        json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    )


def _observed_model(stdout: object) -> str | None:
    """Read an actual model only from Codex JSON event envelopes."""

    if not isinstance(stdout, str):
        return None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, Mapping):
            continue
        event_type = event.get("type")
        if event_type not in {
            "thread.started",
            "turn.started",
            "turn.completed",
            "response.completed",
        }:
            continue
        model = event.get("model")
        if isinstance(model, str) and model.strip():
            return model
    return None


def _response_receipt(
    response: str,
    returncode: int | None,
    observed_model: str | None,
) -> dict[str, object]:
    return {
        "body": response,
        "body_sha256": _digest(response),
        "process_returncode": returncode,
        "observed_model": observed_model,
    }


@dataclass(frozen=True, slots=True)
class CodexSpecialistAdapter:
    """Run one configured Codex reasoning task as a specialist proposal."""

    model: str
    reasoning_effort: str
    executable: str | Sequence[str] = "codex"
    runner: Callable[..., object] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _text(self.model, "model")
        _text(self.reasoning_effort, "reasoning_effort")
        if isinstance(self.executable, str):
            _text(self.executable, "executable")
        elif not self.executable or any(
            not isinstance(item, str) or not item for item in self.executable
        ):
            raise ValueError("executable must be a non-empty command")

    def _command(self, output_path: Path) -> list[str]:
        executable = (
            [self.executable] if isinstance(self.executable, str) else list(self.executable)
        )
        return [
            *executable,
            "exec",
            "--model",
            self.model,
            "-c",
            f'model_reasoning_effort="{self.reasoning_effort}"',
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "--output-last-message",
            str(output_path),
            "--json",
            "-",
        ]

    def _result(
        self,
        status: str,
        request: Mapping[str, object] | None,
        *,
        failure_class: str | None = None,
        violations: Sequence[str] = (),
        response: str | None = None,
        returncode: int | None = None,
        observed_model: str | None = None,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "status": status,
            "provider": "codex-exec",
            "model": observed_model,
            "requested_model": self.model,
            "requested_reasoning_effort": self.reasoning_effort,
            "observed_model": observed_model,
            "usage": None,
            "request": dict(request) if request is not None else None,
            "request_receipt": None,
            "response": None,
            "response_receipt": None,
        }
        if request is not None:
            prompt = request.get("prompt")
            result["request_receipt"] = {
                "body": prompt,
                "body_sha256": _digest(str(prompt or "")),
            }
        if violations:
            result["violations"] = list(violations)
        if failure_class is not None:
            result["failure_class"] = failure_class
        if response is not None:
            result["response_receipt"] = _response_receipt(response, returncode, observed_model)
        return result

    def _invalid(
        self,
        request: Mapping[str, object],
        *,
        violation: str,
        response: str | None = None,
        returncode: int | None = None,
        observed_model: str | None = None,
    ) -> dict[str, object]:
        return self._result(
            "invalid",
            request,
            failure_class="malformed-response",
            violations=(violation,),
            response=response,
            returncode=returncode,
            observed_model=observed_model,
        )

    def _unavailable(
        self,
        request: Mapping[str, object],
        *,
        failure_class: str,
        response: str | None = None,
        returncode: int | None = None,
        observed_model: str | None = None,
    ) -> dict[str, object]:
        return self._result(
            "unavailable",
            request,
            failure_class=failure_class,
            response=response,
            returncode=returncode,
            observed_model=observed_model,
        )

    def _invalid_task(self, violation: str) -> dict[str, object]:
        return self._result(
            "invalid",
            None,
            failure_class="invalid-task",
            violations=(violation,),
        )

    def propose(self, task: DecisionTask) -> dict[str, object]:
        """Return a typed proposal receipt, or an explicit unavailable/invalid result."""

        if not isinstance(task, DecisionTask):
            return self._invalid_task("task:invalid-type")
        packet = task.input_state if isinstance(task.input_state, Mapping) else task.state
        if not isinstance(packet, Mapping):
            return self._invalid_task("task:input-state-object")
        try:
            frozen_packet = _json_copy(packet)
            prompt = _PROMPT_PREFIX + json.dumps(
                frozen_packet,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            return self._invalid_task(f"task:not-json:{error}")
        request: dict[str, object] = {
            "task_id": task.task_id,
            "requested_model": self.model,
            "requested_reasoning_effort": self.reasoning_effort,
            "task_packet": frozen_packet,
            "prompt": prompt,
        }
        runner = self.runner or subprocess.run
        try:
            with tempfile.TemporaryDirectory(prefix="satn-codex-") as temporary:
                temporary_root = Path(temporary)
                output_path = temporary_root / "response.json"
                command = self._command(output_path)
                completed = runner(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                returncode = getattr(completed, "returncode", None)
                stdout = getattr(completed, "stdout", "")
                observed_model = _observed_model(stdout)
                try:
                    response = output_path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    response = None
                if returncode != 0:
                    return self._unavailable(
                        request,
                        failure_class="codex-process-failed",
                        response=response,
                        returncode=returncode if isinstance(returncode, int) else None,
                        observed_model=observed_model,
                    )
                if response is None or not response.strip():
                    return self._invalid(
                        request,
                        violation="response:missing-final-message",
                        returncode=returncode if isinstance(returncode, int) else None,
                        observed_model=observed_model,
                    )

                def reject(violation: str) -> dict[str, object]:
                    return self._invalid(
                        request,
                        violation=violation,
                        response=response,
                        returncode=returncode if isinstance(returncode, int) else None,
                        observed_model=observed_model,
                    )

                try:
                    parsed = json.loads(response)
                except json.JSONDecodeError:
                    return reject("response:invalid-json")
                if not isinstance(parsed, Mapping):
                    return reject("response:object-required")
                if set(parsed) != {"proposal"}:
                    return reject("response:unexpected-fields")
                proposal = parsed.get("proposal")
                if not isinstance(proposal, Mapping):
                    return reject("response.proposal:object-required")
                if set(proposal) != {"operation"}:
                    return reject("response.proposal:unexpected-fields")
                operation = proposal.get("operation")
                if not isinstance(operation, Mapping):
                    return reject("response.proposal.operation:object-required")
                if set(operation) != {"kind", "payload"}:
                    return reject("response.proposal.operation:unexpected-fields")
                if (
                    not isinstance(operation.get("kind"), str)
                    or operation["kind"] not in _OPERATION_KINDS
                ):
                    return reject("response.proposal.operation.kind:unsupported")
                if not isinstance(operation.get("payload"), Mapping):
                    return reject("response.proposal.operation.payload:object-required")
                return {
                    "status": "answered",
                    "provider": "codex-exec",
                    "model": observed_model,
                    "requested_model": self.model,
                    "requested_reasoning_effort": self.reasoning_effort,
                    "observed_model": observed_model,
                    "usage": None,
                    "proposal": _json_copy(dict(proposal)),
                    "request": request,
                    "response": _json_copy(dict(parsed)),
                    "request_receipt": {
                        "body": prompt,
                        "body_sha256": _digest(prompt),
                    },
                    "response_receipt": _response_receipt(
                        response,
                        returncode if isinstance(returncode, int) else None,
                        observed_model,
                    ),
                }
        except Exception:
            return self._unavailable(request, failure_class="codex-process-unavailable")


CodexExecSpecialistAdapter = CodexSpecialistAdapter

__all__ = ["CodexExecSpecialistAdapter", "CodexSpecialistAdapter"]
