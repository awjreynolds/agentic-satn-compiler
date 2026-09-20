from __future__ import annotations

from satn.planning_routing import (
    CapabilityKind,
    CapabilityRecord,
    CapabilityRequirements,
    DecisionTask,
    RoutingConfiguration,
    RoutingPreference,
    RoutingStatus,
    StaticCapabilityRouter,
)


def _requirements(**kwargs) -> CapabilityRequirements:
    values = {
        "operations": ("judge-supplied-options",),
        "judgment_forms": ("choice",),
        "exactness": "typed-judgment-sufficient",
        "permitted_operation_scopes": ("judge-supplied-options",),
        "permitted_authority_scopes": ("compiler-proposal",),
    }
    values.update(kwargs)
    return CapabilityRequirements(**values)


def _task(requirements=None, **kwargs) -> DecisionTask:
    values = {
        "task_id": "task-1",
        "required_capabilities": requirements or _requirements(),
        "allowed_operations": ("judge-supplied-options",),
        "allowed_authority_scopes": ("compiler-proposal",),
    }
    values.update(kwargs)
    return DecisionTask(
        **values,
    )


def _capability(capability_id, kind, *, adapter=None, **kwargs) -> CapabilityRecord:
    values = {
        "capability_id": capability_id,
        "kind": kind,
        "operations": ("judge-supplied-options",),
        "judgment_forms": ("choice",),
        "operation_scopes": ("judge-supplied-options",),
        "authority_scopes": ("compiler-proposal",),
        "provider": None if kind == CapabilityKind.CODE else "configured-provider",
        "adapter": adapter,
    }
    values.update(kwargs)
    return CapabilityRecord(**values)


def test_exact_code_capability_is_selected_without_model_call() -> None:
    calls: list[object] = []

    def code_adapter(task):
        calls.append(task)
        return {"operation": "judge-supplied-options", "selected": "existing"}

    router = StaticCapabilityRouter(
        (_capability("code", CapabilityKind.CODE, adapter=code_adapter),)
    )

    outcome = router.route(_task())

    assert outcome.status is RoutingStatus.CODE_COMPLETED
    assert outcome.capability_id == "code"
    assert outcome.result == {"operation": "judge-supplied-options", "selected": "existing"}
    assert len(calls) == 1


def test_ambiguous_eligible_capabilities_need_explicit_preference() -> None:
    capabilities = (
        _capability(
            "jev-a",
            CapabilityKind.JEV,
            adapter=lambda task: {"status": "answered", "answers": {}},
        ),
        _capability(
            "jev-b",
            CapabilityKind.JEV,
            adapter=lambda task: {"status": "answered", "answers": {}},
        ),
    )
    router = StaticCapabilityRouter(capabilities)

    outcome = router.route(_task())

    assert outcome.status is RoutingStatus.ROUTING_CONFIGURATION_UNRESOLVED
    assert outcome.eligible_capabilities == ("jev-a", "jev-b")

    preferred = StaticCapabilityRouter(
        capabilities,
        RoutingConfiguration(explicit_preferences=(RoutingPreference(capability_id="jev-b"),)),
    ).route(_task())
    assert preferred.status is RoutingStatus.JEV_JUDGMENT
    assert preferred.capability_id == "jev-b"


def test_material_policy_absence_is_owner_policy_unresolved() -> None:
    router = StaticCapabilityRouter((_capability("jev", CapabilityKind.JEV),))

    outcome = router.route(_task(policy_requirement="required-for-this-task", policy=None))

    assert outcome.status is RoutingStatus.OWNER_POLICY_UNRESOLVED
    assert outcome.capability_id is None


def test_no_configured_adapter_is_no_capable_provider_and_unknown_is_distinct() -> None:
    no_provider = StaticCapabilityRouter(
        (_capability("specialist", CapabilityKind.SPECIALIST, adapter=None),)
    ).route(_task(_requirements(needs_decomposition=True)))
    assert no_provider.status is RoutingStatus.NO_CAPABLE_PROVIDER

    unknown = StaticCapabilityRouter(
        (
            _capability(
                "jev",
                CapabilityKind.JEV,
                adapter=lambda task: {"status": "unknown", "cause": "insufficient-evidence"},
            ),
        )
    ).route(_task())
    assert unknown.status is RoutingStatus.UNKNOWN
    assert unknown.capability_id == "jev"


def test_retrieval_items_are_untrusted_until_provenance_admitted() -> None:
    capability = _capability(
        "retrieval",
        CapabilityKind.RETRIEVAL,
        adapter=lambda task: {"items": [{"source_id": "source-1", "text": "claim"}]},
        operations=("retrieve-source",),
        judgment_forms=("none",),
        operation_scopes=("retrieve-source",),
    )
    requirements = _requirements(
        operations=("retrieve-source",),
        judgment_forms=("none",),
        needs_retrieval=True,
        permitted_operation_scopes=("retrieve-source",),
    )

    outcome = StaticCapabilityRouter((capability,)).route(
        _task(requirements, allowed_operations=("retrieve-source",))
    )

    assert outcome.status is RoutingStatus.RETRIEVAL_COMPLETED
    assert outcome.admission == "unadmitted"
    assert outcome.items == ({"source_id": "source-1", "text": "claim"},)


def test_provider_output_cannot_exceed_task_scopes() -> None:
    capability = _capability(
        "jev",
        CapabilityKind.JEV,
        adapter=lambda task: {
            "status": "answered",
            "operation_scopes": ["validated-state-transition"],
        },
    )

    outcome = StaticCapabilityRouter((capability,)).route(_task())

    assert outcome.status is RoutingStatus.INVALID_PROVIDER_RESPONSE
    assert "operation_scopes" in outcome.violations


def test_recorded_route_requires_receipt_and_never_dispatches_adapter() -> None:
    calls: list[object] = []

    def adapter(task):
        calls.append(task)
        return {"status": "answered", "answers": {}}

    capability = _capability("jev", CapabilityKind.JEV, adapter=adapter)
    missing = StaticCapabilityRouter((capability,)).route(_task(replay_mode="recorded"))

    assert missing.status is RoutingStatus.INVALID_TASK
    assert "recorded-receipt-required" in missing.violations
    assert calls == []

    replay = StaticCapabilityRouter((capability,)).route(
        _task(
            replay_mode="recorded",
            recorded_receipt={"status": "unknown", "cause": "saved-result"},
        )
    )
    assert replay.status is RoutingStatus.UNKNOWN
    assert replay.cause == "saved-result"
    assert calls == []


def test_nested_specialist_proposal_scopes_are_checked() -> None:
    capability = _capability(
        "specialist",
        CapabilityKind.SPECIALIST,
        adapter=lambda task: {
            "proposal": {
                "operation": "adopt",
                "authority_scope": "validated-state-transition",
            }
        },
        operations=("adopt",),
        judgment_forms=("structured-proposal",),
        operation_scopes=("compiler-proposal",),
        authority_scopes=("compiler-proposal",),
    )
    requirements = _requirements(
        operations=("adopt",),
        judgment_forms=("structured-proposal",),
        exactness="proposal-then-validate",
        permitted_operation_scopes=("compiler-proposal",),
        permitted_authority_scopes=("compiler-proposal",),
    )

    outcome = StaticCapabilityRouter((capability,)).route(
        _task(
            requirements,
            allowed_operations=("adopt",),
            allowed_authority_scopes=("compiler-proposal",),
        )
    )

    assert outcome.status is RoutingStatus.INVALID_PROVIDER_RESPONSE
    assert "proposal.authority_scope" in outcome.violations
