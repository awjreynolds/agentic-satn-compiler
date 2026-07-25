from __future__ import annotations

import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest

from satn.models import AgentConfig
from satn.runtime_governance import (
    ApprovedRuntimeClass,
    assert_promotable_runtime_governance,
    classify_runtime_governance,
    incomplete_runtime_governance,
    validate_runtime_governance,
)


def _record(
    *,
    responder_mode: str = "direct-runtime",
    runtime: str = "approved-fixture-adapter",
    model: str = "approved-fixture-model",
    requests: int = 1,
    tokens: int = 7,
) -> SimpleNamespace:
    return SimpleNamespace(
        review_required=True,
        responder_mode=responder_mode,
        runtime=runtime,
        model=model,
        usage={"requests": requests, "tokens": tokens},
    )


def _ledger(*, replay: bool = False) -> dict[str, object]:
    response = {
        "request_id": "agent-decision-test",
        "dependency_fingerprint": hashlib.sha256(b"test").hexdigest(),
        "choice_id": "1",
    }
    return {
        "decision_contract": "agent-decision-menu/v1",
        "responses": [response] if replay else [],
    }


def test_fake_runtime_is_permanently_non_production_and_declares_runtime_metadata() -> None:
    manifest = classify_runtime_governance(
        AgentConfig(provider="fake", response_mode="direct-runtime"),
        [_record(runtime="fake", model="deterministic-choices-v1")],
        decision_ledger_input=_ledger(),
        accepted_decisions=[],
    )

    assert manifest["status"] == "non-production"
    assert manifest["reason"] == "fake-runtime"
    assert manifest["promotion"] == {"allowed": False, "reason": "fake-runtime"}
    assert manifest["configured_runtime"] == {
        "provider": "fake",
        "model": None,
        "response_mode": "direct-runtime",
    }
    assert manifest["observed_runtime"] == {
        "providers": ["fake"],
        "models": ["deterministic-choices-v1"],
        "responder_modes": ["direct-runtime"],
    }
    assert manifest["replay_status"] == "fresh"
    assert manifest["usage"] == {
        "available": True,
        "records": 1,
        "requests": 1,
        "tokens": 7,
        "by_responder_mode": {
            "direct-runtime": {"records": 1, "requests": 1, "tokens": 7}
        },
    }


def test_caller_replay_cannot_upgrade_an_approved_direct_runtime_origin() -> None:
    config = AgentConfig(
        provider="approved-fixture-provider",
        model="approved-fixture-model",
        response_mode="direct-runtime",
    )
    direct = classify_runtime_governance(
        config,
        [_record()],
        decision_ledger_input=_ledger(),
        accepted_decisions=[],
    )
    approval = ApprovedRuntimeClass(
        runtime_class_sha256=str(direct["runtime_class_sha256"]),
        ledger_provenance_sha256=str(direct["decision_ledger_provenance_sha256"]),
    )
    approved_direct = classify_runtime_governance(
        config,
        [_record()],
        decision_ledger_input=_ledger(),
        accepted_decisions=[],
        approved_runtime_classes=(approval,),
    )
    assert approved_direct["status"] == "production-approved"
    assert approved_direct["promotion"]["allowed"] is True

    replay_ledger = _ledger(replay=True)
    replay = classify_runtime_governance(
        config,
        [
            _record(
                responder_mode="caller",
                runtime="caller",
                model="decision-ledger",
                requests=0,
                tokens=0,
            )
        ],
        decision_ledger_input=replay_ledger,
        accepted_decisions=list(replay_ledger["responses"]),
        approved_runtime_classes=(approval,),
    )
    assert replay["status"] == "non-production"
    assert replay["reason"] == "replay-origin-provenance-unavailable"
    assert replay["replay_status"] == "replay"


def test_missing_credentials_or_incomplete_decisions_are_reviewable_without_fallback() -> None:
    manifest = incomplete_runtime_governance(
        AgentConfig(
            provider="pydantic-ai",
            model="openai:gpt-test",
            response_mode="direct-runtime",
        ),
        [],
        decision_ledger_input=_ledger(),
        validation="runtime-unavailable",
    )

    assert manifest["status"] == "reviewable"
    assert manifest["reason"] == "incomplete-decisions:runtime-unavailable"
    assert manifest["promotion"]["allowed"] is False
    assert manifest["usage"] == {
        "available": False,
        "records": 0,
        "requests": 0,
        "tokens": 0,
        "by_responder_mode": {},
    }


def test_fake_runtime_remains_non_production_when_decisions_are_incomplete() -> None:
    manifest = incomplete_runtime_governance(
        AgentConfig(provider="fake", response_mode="direct-runtime"),
        [_record(runtime="fake", model="deterministic-choices-v1")],
        decision_ledger_input=_ledger(),
        validation="runtime-unavailable",
    )

    assert manifest["status"] == "non-production"
    assert manifest["reason"] == "fake-runtime"
    assert manifest["promotion"] == {"allowed": False, "reason": "fake-runtime"}


def test_misclassification_and_tampering_are_rejected() -> None:
    config = AgentConfig(
        provider="approved-fixture-provider",
        model="approved-fixture-model",
        response_mode="direct-runtime",
    )
    record = _record()
    manifest = classify_runtime_governance(
        config,
        [record],
        decision_ledger_input=_ledger(),
        accepted_decisions=[],
    )
    tampered = {**manifest, "status": "production-approved"}
    with pytest.raises(ValueError, match="differs from decision provenance"):
        validate_runtime_governance(
            tampered,
            config,
            [record],
            decision_ledger_input=_ledger(),
            accepted_decisions=[],
        )

    record.usage = {"requests": 1, "tokens": 8}
    with pytest.raises(ValueError, match="differs from decision provenance"):
        validate_runtime_governance(
            manifest,
            config,
            [record],
            decision_ledger_input=_ledger(),
            accepted_decisions=[],
        )


def test_promotion_gate_denies_without_an_approved_immutable_class() -> None:
    manifest = classify_runtime_governance(
        AgentConfig(
            provider="approved-fixture-provider",
            model="approved-fixture-model",
            response_mode="direct-runtime",
        ),
        [_record()],
        decision_ledger_input=_ledger(),
        accepted_decisions=[],
    )

    with pytest.raises(ValueError, match="production promotion denied"):
        assert_promotable_runtime_governance(
            manifest,
            decision_contract="agent-decision-menu/v1",
            decision_ledger_input=_ledger(),
            accepted_decisions=[],
        )


def test_promotion_gate_rejects_a_forged_self_consistent_manifest() -> None:
    """A manifest cannot promote itself by declaring its own approval."""

    manifest = classify_runtime_governance(
        AgentConfig(
            provider="approved-fixture-provider",
            model="approved-fixture-model",
            response_mode="direct-runtime",
        ),
        [_record()],
        decision_ledger_input=_ledger(),
        accepted_decisions=[],
    )
    forged = {
        **manifest,
        "status": "production-approved",
        "reason": "approved-immutable-runtime-class-and-ledger-provenance",
        "promotion": {
            "allowed": True,
            "reason": "approved-immutable-runtime-class-and-ledger-provenance",
        },
    }

    with pytest.raises(ValueError, match="production promotion denied"):
        assert_promotable_runtime_governance(
            forged,
            decision_contract="agent-decision-menu/v1",
            decision_ledger_input=_ledger(),
            accepted_decisions=[],
        )


def test_promotion_gate_recomputes_approved_digests_from_bound_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reviewed pair cannot be pasted onto altered runtime or ledger evidence."""

    import satn.runtime_governance as governance

    config = AgentConfig(
        provider="approved-fixture-provider",
        model="approved-fixture-model",
        response_mode="direct-runtime",
    )
    ledger = _ledger()
    accepted = [
        {
            "request_id": "agent-decision-accepted",
            "dependency_fingerprint": hashlib.sha256(b"accepted").hexdigest(),
            "choice_id": "1",
        }
    ]
    unsigned = classify_runtime_governance(
        config,
        [_record()],
        decision_ledger_input=ledger,
        accepted_decisions=accepted,
    )
    approved = ApprovedRuntimeClass(
        runtime_class_sha256=str(unsigned["runtime_class_sha256"]),
        ledger_provenance_sha256=str(unsigned["decision_ledger_provenance_sha256"]),
    )
    manifest = classify_runtime_governance(
        config,
        [_record()],
        decision_ledger_input=ledger,
        accepted_decisions=accepted,
        approved_runtime_classes=(approved,),
    )
    monkeypatch.setattr(governance, "APPROVED_RUNTIME_CLASSES", frozenset({approved}))

    assert_promotable_runtime_governance(
        manifest,
        decision_contract="agent-decision-menu/v1",
        decision_ledger_input=ledger,
        accepted_decisions=accepted,
    )

    altered_runtime = deepcopy(manifest)
    altered_runtime["configured_runtime"]["provider"] = "forged-provider"  # type: ignore[index]
    with pytest.raises(ValueError, match="runtime_class_sha256 differs from bound content"):
        assert_promotable_runtime_governance(
            altered_runtime,
            decision_contract="agent-decision-menu/v1",
            decision_ledger_input=ledger,
            accepted_decisions=accepted,
        )

    altered_ledger = deepcopy(accepted)
    altered_ledger[0]["choice_id"] = "2"
    with pytest.raises(
        ValueError, match="decision_ledger_provenance_sha256 differs from bound content"
    ):
        assert_promotable_runtime_governance(
            manifest,
            decision_contract="agent-decision-menu/v1",
            decision_ledger_input=ledger,
            accepted_decisions=altered_ledger,
        )
