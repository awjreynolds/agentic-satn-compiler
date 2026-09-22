# Rust judgment adapters for the SATN midend

Status: bounded research contract for the Rust midend. It records provider
facts and the minimum accepted semantics for a live focused judgment followed
by an optional configured Codex specialist. It does not call either provider,
inspect credentials, add a router/consent registry, or require the legacy
Python receipt, status, hash, or payload-copy formats. Examples use synthetic
placeholders and contain no source data.

## Minimum behavior

An ordinary live plan run may do the following:

1. Send the admitted focused question to TypeSafe System One as a direct
   Choice request using `jev-latest` (or another caller-supplied TypeSafe model).
2. Accept a Choice only when its selected option and probability map match the
   sent options, numeric values are in range, and the response is otherwise
   valid JSON with the expected answer ID.
3. Treat explicit unresolved options such as `__unknown__`,
   `__needs_evidence__`, or `__none__` as unresolved outcomes. They remain
   available for the existing planning operation mapping; they are not silently
   converted into a selected domain answer.
4. If the focused task remains unresolved under the existing planning policy,
   send that frozen task once to a caller-configured Codex specialist. The
   specialist's JSON proposal is passed through the existing typed operation
   validation. A provisional alignment is accepted only when the caller's
   policy allows it and the proposal includes the required concise `reason` and
   non-empty `uncertainties`.
5. On replay, use the retained result by local ID and do not perform an HTTP
   request or launch Codex.

Live provider use is caller-configured. The Rust adapter does not invent a
confidence threshold, retry policy, timeout, output cap, capability registry,
or consent-discovery framework. One TypeSafe request and one Codex process are
the current per-task calls; this is not a new retry or rate policy. The caller
owns model, reasoning effort, and any future retry/timeout policy.

The existing planning policy source is
[`docs/typesafe-planning.md`](../../docs/typesafe-planning.md) and its focused
task/operation definitions. In particular, the current policy maps explicit
unresolved Choice markers to evidence/gap operations and makes provisional
alignment conditional on `allow_provisional_choices`; the adapter must retain
those meanings without importing the legacy Python wire formats. TypeSafe's
official guidance says confidence thresholds depend on stakes, domain, and
model, so this adapter has no universal cutoff.

## TypeSafe HTTP contract

The official API is a JSON `POST` to
`https://api.typesafe.ai/v1/systemone` with:

```text
Content-Type: application/json
Accept: application/json
Authorization: Bearer <credential>
```

The minimum Choice request is:

```json
{
  "state": {"synthetic": "admitted-state"},
  "model": "jev-latest",
  "questions": {
    "decision": {
      "type": "choice",
      "instructions": "synthetic frozen question",
      "criteria": {
        "__unknown__": "record unresolved",
        "candidate-a": "synthetic admitted option"
      }
    }
  }
}
```

`state` is a string, object, or array; `model` is required; `questions` is a
non-empty object. Choice criteria are a fixed option map, with a documented
maximum of 255 options. The minimum successful response is:

```json
{
  "model": "jev-latest",
  "answers": {
    "decision": {
      "choice": "__unknown__",
      "probabilities": {"__unknown__": 0.8, "candidate-a": 0.2},
      "confidence": 0.8
    }
  },
  "usage": {"input_tokens": 1, "output_tokens": 1}
}
```

The minimum decoder checks the expected answer ID, selected option, exact
probability keys, values in `[0, 1]`, and confidence in `[0, 1]`. It may retain
provider usage when present. A failed request, non-success HTTP response,
malformed JSON, or invalid answer is a failed/invalid judgment; it is not a
fabricated Choice and does not become an unresolved domain answer merely by
error handling.

TypeSafe documents 401, 422, 429, and 529 responses. The Rust adapter should
surface the provider response and failure class to its caller; the exact Rust
enum is an implementation choice and need not match Python status strings.
The current per-task contract has no hidden retry, timeout, or cap.

Credential handling is deliberately narrow. Read the configured
`TYPESAFE_API_KEY` environment value, and retain the current project fallback
`~/.config/typesafe/api-key` only if the parent keeps that convention. Do not
inspect unrelated auth files or discover credential locations. Never print,
serialize, include in errors, or persist the credential or bearer header.

## Codex process contract

The specialist is a single installed `codex` process. The model and reasoning
effort are caller-supplied; the current documented/example configuration is
`gpt-5.6-luna` with `max`. The process arguments are:

```text
codex exec --model <model> -c 'model_reasoning_effort="<effort>"' \
  --sandbox read-only --ephemeral --skip-git-repo-check \
  --output-last-message <path> --json -
```

The `-c` value is one literal argument (`model_reasoning_effort="<effort>"`);
the quotes above are shell notation. Rust should use
`std::process::Command` with literal arguments, piped stdin/stdout/stderr, and
no shell. Write the frozen task to stdin (`-`) and wait for that invocation.

The prompt must say to use only the supplied task, without tools, browsing,
retrieval, files, or added facts, and to return exactly one JSON proposal. It
must describe the supported operation payload fields, not only operation
names. Concise `reason` is allowed where the operation contract supports it;
hidden chain-of-thought is not requested or retained.

Read the final response only from `--output-last-message`. `--json` stdout is
an event stream and is not a final-response fallback. A non-zero process,
missing/blank final-message file, malformed JSON, wrong proposal envelope,
unsupported operation, or invalid payload is a failed/invalid specialist
result. A valid proposal is then checked by the existing typed operation
validator. Keep requested model/effort, observed model when available, process
status, and the final response needed for the result. Do not retain the JSONL
event transcript or hidden reasoning. The adapter intentionally omits
`--output-schema`; the existing typed operation validator is the authority.

Codex saved authentication remains inside the CLI process. The Rust adapter
must not inspect, parse, print, or persist the Codex auth file or
`CODEX_API_KEY`, and must not add another authentication-file lookup.

## Compact retention and Rust foundation

The minimum retained record can store the frozen task once and the final
provider response once, keyed by the local task/decision ID, together with
requested/observed model metadata and the provider/process outcome. A full
response-state hash is optional; it is not required by this contract. Do not
duplicate source payloads merely to imitate a legacy receipt format. Replay
uses the local retained result and skips provider dispatch.

The Rust foundation currently declares `serde`, `serde_json`, `serde_yaml`, and
`clap` in `rust/Cargo.toml`. `serde`/`serde_json` are sufficient for the direct
request, response, and proposal structs; `std::process::Command` is sufficient
for the Codex boundary. An HTTP client can be selected by the parent when the
transport implementation is added. No framework or generic provider registry
is required.

## Primary sources

- [TypeSafe API](https://docs.typesafe.ai/api) — endpoint, authentication, request/response shapes, question limits, and provider errors.
- [TypeSafe Choice](https://docs.typesafe.ai/primitives/choice) — fixed options and full probability/confidence result.
- [TypeSafe confidence](https://docs.typesafe.ai/confidence) — confidence interpretation and domain-specific thresholds.
- [TypeSafe Python SDK](https://docs.typesafe.ai/sdk/python) — `TYPESAFE_API_KEY` environment convention.
- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) — stdin prompts, saved auth, `--ephemeral`, JSONL, and final-message output.
- [Codex CLI reference](https://developers.openai.com/codex/cli/reference/) — `codex exec`, `--model`, `--sandbox`, `--output-last-message`, and `--output-schema`.
- [Codex model and reasoning configuration](https://learn.chatgpt.com/docs/agent-configuration/subagents) — `gpt-5.6-luna` and `model_reasoning_effort` configuration.
- [Rust `std::process::Command`](https://doc.rust-lang.org/std/process/struct.Command.html) — literal argument-vector process control and piped I/O.
