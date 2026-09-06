from __future__ import annotations

from dataclasses import replace

import pytest
from test_prepared_scenario_compilation import (
    bound_criteria,
    connection,
    packet,
    preparation,
    request,
)

from satn.compilation_dependencies import compilation_dependency_manifest
from satn.scenario_compilation import compile_prepared_scenario
from satn.scenario_iteration import (
    AcceptedDecisionLedger,
    AtomicPublicationReceipt,
    ScenarioConfiguration,
    ScenarioIterationState,
    ScenarioStageRecord,
    iterate_scenario,
)


def _inputs():
    manifest = compilation_dependency_manifest()
    digest = manifest["compiler_cache_revision"]
    stages = []
    prior = None
    for stage in range(1, 7):
        output = f"{stage}" * 64
        stages.append(
            ScenarioStageRecord(
                stage=stage,
                contract=f"satn-stage-{stage}/v1",
                input_fingerprint="a" * 64,
                output_fingerprint=output,
                dependency_manifest_sha256=digest,
                upstream_output_fingerprints=(() if prior is None else (prior,)),
            )
        )
        prior = output
    state = ScenarioIterationState(tuple(stages))
    configuration = ScenarioConfiguration(
        area_definition_fingerprint="7" * 64,
        criteria_set_fingerprint="8" * 64,
        network_selection_profile_fingerprint="9" * 64,
        reusable_state_fingerprint=state.state_fingerprint,
        dependency_manifest_sha256=digest,
        publication_configuration_fingerprint="b" * 64,
        values={"criteria": {"minimum": 1}},
    )
    ledger = AcceptedDecisionLedger(
        configuration_fingerprint=configuration.configuration_fingerprint,
        evidence_state_fingerprint=state.evidence_state_fingerprint,
        assembly_fingerprint=state.assembly_fingerprint,
        decisions=({"decision_id": "accepted-1"},),
    )
    return manifest, state, configuration, ledger


def _scenario():
    prepared = connection()
    source = preparation(prepared)
    compiled = compile_prepared_scenario(
        source,
        request(
            (
                packet(
                    prepared,
                    bound_criteria(prepared),
                    source_preparation=source,
                ),
            )
        ),
    )
    assert compiled.scenario is not None
    assert compiled.scenario.publishable
    return compiled.scenario


def test_iteration_reuses_one_through_six_then_compiles_and_publishes() -> None:
    manifest, state, configuration, ledger = _inputs()
    scenario = _scenario()
    calls = []

    def compile_stage(*args):
        calls.append(("compile", args[2]))
        return scenario

    def publish_stage(compiled, dependency_manifest):
        calls.append(("publish", compiled.scenario_fingerprint))
        assert dependency_manifest["compiler_cache_revision"] == manifest["compiler_cache_revision"]
        return AtomicPublicationReceipt(
            publication_fingerprint="c" * 64,
            artifact_digests={"run.json": "d" * 64},
            whole_publication_validated=True,
            atomic_replace_completed=True,
        )

    result = iterate_scenario(
        configuration,
        ledger,
        state,
        manifest,
        compile_scenario=compile_stage,
        publish_atomic=publish_stage,
    )

    assert [item.disposition for item in result.stage_diagnostics] == [
        *(["validated-hit"] * 6),
        "recomputed",
        "recomputed",
    ]
    assert all(
        item.diagnostics["evidence_refresh_performed"] is False
        and item.diagnostics["routing_recomputed"] is False
        for item in result.stage_diagnostics[:6]
    )
    assert calls == [
        ("compile", state.assembly_fingerprint),
        ("publish", scenario.scenario_fingerprint),
    ]


def test_stale_state_and_ledger_fail_before_compilation() -> None:
    manifest, state, configuration, ledger = _inputs()
    called = False

    def never(*args):
        nonlocal called
        called = True
        raise AssertionError

    with pytest.raises(ValueError, match="reusable state is stale"):
        iterate_scenario(
            replace(
                configuration,
                reusable_state_fingerprint="f" * 64,
                configuration_fingerprint="",
            ),
            ledger,
            state,
            manifest,
            compile_scenario=never,
            publish_atomic=never,
        )
    with pytest.raises(ValueError, match="ledger is stale"):
        iterate_scenario(
            configuration,
            replace(ledger, assembly_fingerprint="e" * 64, ledger_fingerprint=""),
            state,
            manifest,
            compile_scenario=never,
            publish_atomic=never,
        )
    assert called is False
