"""Canonical, stdlib-only runtime-governance identity calculations.

This deliberately small contract is shared by the compiler-side packager and
the isolated Pages verifier.  It binds a claimed approval to the runtime
classification and decision ledger *content*, rather than merely to two
copyable declared digest fields.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence


def canonical_sha256(value: object) -> str:
    """Return the SHA-256 of the canonical JSON representation of ``value``."""

    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _canonical_strings(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"runtime governance {field} must be a list of strings")
    if value != sorted(set(value)):
        raise ValueError(f"runtime governance {field} must be sorted and unique")
    return list(value)


def runtime_class_from_manifest(manifest: Mapping[str, object]) -> dict[str, object]:
    """Extract the exact immutable runtime class from public governance evidence."""

    configured = manifest.get("configured_runtime")
    observed = manifest.get("observed_runtime")
    if not isinstance(configured, Mapping) or set(configured) != {
        "provider", "model", "response_mode"
    }:
        raise ValueError("runtime governance configured_runtime is invalid")
    if not isinstance(observed, Mapping) or set(observed) != {
        "providers", "models", "responder_modes"
    }:
        raise ValueError("runtime governance observed_runtime is invalid")
    provider = configured.get("provider")
    model = configured.get("model")
    response_mode = configured.get("response_mode")
    if (
        not isinstance(provider, str)
        or not isinstance(response_mode, str)
        or (model is not None and not isinstance(model, str))
    ):
        raise ValueError("runtime governance configured_runtime has invalid values")
    return {
        "configured": {
            "provider": provider,
            "model": model,
            "response_mode": response_mode,
        },
        "observed": {
            "providers": _canonical_strings(observed.get("providers"), "observed providers"),
            "models": _canonical_strings(observed.get("models"), "observed models"),
            "responder_modes": _canonical_strings(
                observed.get("responder_modes"), "observed responder_modes"
            ),
        },
    }


def recompute_runtime_governance_digests(
    manifest: Mapping[str, object],
    *,
    decision_contract: object,
    decision_ledger_input: Mapping[str, object],
    accepted_decisions: Sequence[Mapping[str, object]],
) -> tuple[str, str]:
    """Recompute the two approval identities from canonical bound content."""

    if not isinstance(decision_contract, str):
        raise ValueError("runtime governance decision contract is invalid")
    return (
        canonical_sha256(runtime_class_from_manifest(manifest)),
        canonical_sha256(
            {
                "decision_contract": decision_contract,
                "decision_ledger_input": dict(decision_ledger_input),
                "accepted_decisions": list(accepted_decisions),
            }
        ),
    )


def assert_declared_runtime_governance_digests(
    manifest: Mapping[str, object],
    *,
    decision_contract: object,
    decision_ledger_input: Mapping[str, object],
    accepted_decisions: Sequence[Mapping[str, object]],
) -> tuple[str, str]:
    """Reject declared approval digests not derived from their bound content."""

    declared_runtime = manifest.get("runtime_class_sha256")
    declared_ledger = manifest.get("decision_ledger_provenance_sha256")
    if not isinstance(declared_runtime, str) or not isinstance(declared_ledger, str):
        raise ValueError("runtime governance declared approval digests are invalid")
    expected_runtime, expected_ledger = recompute_runtime_governance_digests(
        manifest,
        decision_contract=decision_contract,
        decision_ledger_input=decision_ledger_input,
        accepted_decisions=accepted_decisions,
    )
    if declared_runtime != expected_runtime:
        raise ValueError("runtime governance runtime_class_sha256 differs from bound content")
    if declared_ledger != expected_ledger:
        raise ValueError(
            "runtime governance decision_ledger_provenance_sha256 differs from bound content"
        )
    return expected_runtime, expected_ledger
