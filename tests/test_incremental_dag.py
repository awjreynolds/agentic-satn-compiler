from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from satn.incremental_dag import (
    IncrementalDAGResolver,
    IncrementalStage,
    StageCycleError,
    UnknownDependencyError,
)
from satn.retained_artifacts import RetainedArtifactStore


def _stage(
    name: str,
    dependencies: tuple[str, ...] = (),
    *,
    build=None,
) -> IncrementalStage:
    return IncrementalStage(
        name=name,
        contract_version=f"{name}/v1",
        dependencies=dependencies,
        implementation_fingerprint="1" * 64,
        dependency_manifest_fingerprint="2" * 64,
        parameters={"stage": name},
        partition_identities=(name,),
        coverage_identities=("3" * 64,),
        validation_contract=f"{name}-validation/v1",
        diagnostics={"stage": name},
        build=build or (lambda inputs: {"output": name.encode()}),
    )


def test_resolver_builds_a_stable_topological_order_and_binds_dependency_ids(
    tmp_path: Path,
) -> None:
    store = RetainedArtifactStore.in_workspace(tmp_path)
    seen: list[tuple[str, tuple[str, ...]]] = []

    def build(name: str):
        def _build(inputs):
            seen.append((name, tuple(inputs)))
            return {"output": name.encode() + b"-" + b",".join(
                inputs[key].artifact_id.encode() for key in inputs
            )}

        return _build

    stages = (
        _stage("z", ("x",), build=build("z")),
        _stage("x", ("a",), build=build("x")),
        _stage("a", build=build("a")),
        _stage("y", ("a",), build=build("y")),
    )

    result = IncrementalDAGResolver(store).resolve(stages)

    assert tuple(result.artifacts) == ("a", "x", "y", "z")
    assert tuple(event.scope for event in result.events) == ("a", "x", "y", "z")
    assert all(event.disposition == "build" for event in result.events)
    assert all(event.artifact_id for event in result.events)
    assert seen == [("a", ()), ("x", ("a",)), ("y", ("a",)), ("z", ("x",))]

    z_spec = result.artifacts["z"].manifest
    assert z_spec.upstream_artifact_ids == (result.artifacts["x"].artifact_id,)


def test_incremental_resolution_hits_existing_artifacts_and_rebuilds_descendants(
    tmp_path: Path,
) -> None:
    store = RetainedArtifactStore.in_workspace(tmp_path)
    builds: list[str] = []

    def stage(name: str, dependencies: tuple[str, ...] = ()) -> IncrementalStage:
        return _stage(
            name,
            dependencies,
            build=lambda inputs: (builds.append(name) or {"output": name.encode()}),
        )

    stages = (stage("a"), stage("b", ("a",)), stage("c", ("b",)))
    first = IncrementalDAGResolver(store).resolve(stages)
    assert builds == ["a", "b", "c"]

    builds.clear()
    second = IncrementalDAGResolver(store).resolve(stages)

    assert builds == []
    assert [event.disposition for event in second.events] == ["hit", "hit", "hit"]
    assert [event.reason for event in second.events] == [
        "validated-dependency-closure",
        "validated-dependency-closure",
        "validated-dependency-closure",
    ]
    assert second.artifacts == first.artifacts

    # A changed upstream implementation changes its identity and forces only it
    # and its descendants to rebuild.
    builds.clear()
    changed = (
        replace(stage("a"), implementation_fingerprint="4" * 64),
        stage("b", ("a",)),
        stage("c", ("b",)),
    )
    third = IncrementalDAGResolver(store).resolve(changed)
    assert builds == ["a", "b", "c"]
    assert [event.disposition for event in third.events] == ["build", "build", "build"]


def test_full_and_targeted_rebuild_force_stages_without_deleting_history(
    tmp_path: Path,
) -> None:
    store = RetainedArtifactStore.in_workspace(tmp_path)
    builds: list[str] = []

    def stage(name: str, dependencies: tuple[str, ...] = ()) -> IncrementalStage:
        return _stage(
            name,
            dependencies,
            build=lambda inputs: (builds.append(name) or {"output": name.encode()}),
        )

    stages = (stage("a"), stage("b", ("a",)), stage("c", ("b",)))
    initial = IncrementalDAGResolver(store).resolve(stages)
    builds.clear()
    targeted = IncrementalDAGResolver(store, rebuild_stages=("b",)).resolve(stages)
    assert builds == ["b", "c"]
    assert [event.reason for event in targeted.events] == [
        "validated-dependency-closure",
        "forced-stage",
        "forced-stage",
    ]
    assert all(artifact.path.exists() for artifact in initial.artifacts.values())

    builds.clear()
    full = IncrementalDAGResolver(store, full=True).resolve(stages)
    assert builds == ["a", "b", "c"]
    assert [event.reason for event in full.events] == [
        "forced-full",
        "forced-full",
        "forced-full",
    ]
    assert all(artifact.path.exists() for artifact in initial.artifacts.values())


def test_unknown_dependencies_and_cycles_are_rejected_before_building(tmp_path: Path) -> None:
    store = RetainedArtifactStore.in_workspace(tmp_path)
    builds: list[str] = []

    def build(name: str):
        return lambda inputs: (builds.append(name) or {"output": name.encode()})

    with pytest.raises(UnknownDependencyError, match="missing"):
        IncrementalDAGResolver(store).resolve((_stage("a", ("missing",), build=build("a")),))
    assert builds == []

    with pytest.raises(StageCycleError, match=r"a.*b.*a"):
        IncrementalDAGResolver(store).resolve(
            (_stage("a", ("b",), build=build("a")), _stage("b", ("a",), build=build("b")))
        )
    assert builds == []


def test_corrupt_candidate_is_rebuilt_through_store_resolution_api(tmp_path: Path) -> None:
    store = RetainedArtifactStore.in_workspace(tmp_path)
    builds: list[str] = []
    stages = (_stage("a", build=lambda inputs: (builds.append("a") or {"output": b"ok"})),)
    resolver = IncrementalDAGResolver(store)
    first = resolver.resolve(stages)
    (first.artifacts["a"].path / "outputs" / "output").write_bytes(b"corrupt")

    builds.clear()
    second = resolver.resolve(stages)
    assert builds == ["a"]
    assert second.events[0].disposition == "build"
    assert second.events[0].reason == "output-digest-mismatch"
