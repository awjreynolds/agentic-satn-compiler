"""Deterministic incremental resolution of retained artifact stages.

The resolver deliberately knows nothing about compiler domains.  A stage declares
the semantic fields needed to form an :class:`ArtifactSpecification` and a small
builder that materialises its byte outputs.  Reuse is delegated to
``RetainedArtifactStore.resolve_specification``; this keeps corruption and
candidate ambiguity in one store API rather than teaching each caller how to
inspect the filesystem.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from time import monotonic_ns
from types import MappingProxyType

from satn.retained_artifacts import (
    ArtifactResolution,
    ArtifactSpecification,
    RetainedArtifact,
    RetainedArtifactStore,
    RunArtifactEvent,
)

type ArtifactBuilder = Callable[
    [Mapping[str, RetainedArtifact]], Mapping[str, bytes]
]


class DAGValidationError(ValueError):
    """Base class for invalid stage graphs."""


class UnknownDependencyError(DAGValidationError):
    """A stage names a dependency that is not in the graph."""


class UnknownStageError(DAGValidationError):
    """A requested targeted rebuild names a stage that is not in the graph."""


class StageCycleError(DAGValidationError):
    """The declared stage graph is cyclic."""


@dataclass(frozen=True)
class IncrementalStage:
    """Semantic contract and builder for one retained DAG stage.

    ``workers`` and execution scheduling intentionally have no field here.  A
    caller may choose any worker profile when invoking a builder, but worker
    allocation cannot become part of the resulting artifact identity.
    """

    name: str
    contract_version: str
    dependencies: tuple[str, ...]
    implementation_fingerprint: str
    dependency_manifest_fingerprint: str
    parameters: object
    partition_identities: tuple[str, ...]
    coverage_identities: tuple[str, ...]
    validation_contract: str
    diagnostics: object
    build: ArtifactBuilder
    status: str = "complete"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("incremental stage name is invalid")
        if not isinstance(self.contract_version, str) or not self.contract_version:
            raise ValueError("incremental stage contract version is invalid")
        if not isinstance(self.dependencies, tuple):
            object.__setattr__(self, "dependencies", tuple(self.dependencies))
        if any(not isinstance(item, str) or not item for item in self.dependencies):
            raise ValueError("incremental stage dependencies are invalid")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("incremental stage dependencies must be unique")
        object.__setattr__(self, "dependencies", tuple(sorted(self.dependencies)))
        if not callable(self.build):
            raise ValueError("incremental stage builder is invalid")


@dataclass(frozen=True)
class IncrementalResolution:
    """Ordered retained artifacts and run-report-compatible stage events."""

    artifacts: Mapping[str, RetainedArtifact]
    events: tuple[RunArtifactEvent, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifacts",
            MappingProxyType(dict(self.artifacts)),
        )
        object.__setattr__(self, "events", tuple(self.events))

    @property
    def ordered_artifacts(self) -> tuple[tuple[str, RetainedArtifact], ...]:
        """Artifacts in the resolver's stable topological order."""

        return tuple(self.artifacts.items())

    @property
    def artifact_events(self) -> tuple[RunArtifactEvent, ...]:
        """Alias suitable for embedding directly in a run report."""

        return self.events


class IncrementalDAGResolver:
    """Resolve and materialise a generic retained artifact DAG."""

    def __init__(
        self,
        store: RetainedArtifactStore,
        *,
        full: bool = False,
        rebuild_stages: Iterable[str] = (),
    ) -> None:
        if not isinstance(store, RetainedArtifactStore) and (
            not callable(getattr(store, "put", None))
            or not callable(getattr(store, "resolve_specification", None))
        ):
            # A small structural seam makes the resolver straightforward to use
            # with a test double while retaining the concrete production type in
            # the public annotation.
            raise TypeError("incremental DAG store is invalid")
        if not isinstance(full, bool):
            raise ValueError("full rebuild flag is invalid")
        requested = tuple(rebuild_stages)
        if any(not isinstance(name, str) or not name for name in requested):
            raise ValueError("rebuild stage names are invalid")
        if len(set(requested)) != len(requested):
            raise ValueError("rebuild stage names must be unique")
        self.store = store
        self.full = full
        self.rebuild_stages = requested

    def resolve(self, stages: Iterable[IncrementalStage]) -> IncrementalResolution:
        """Resolve ``stages`` in stable topological order.

        Validation happens entirely before the first store lookup or builder
        invocation.  A miss invalidates its descendants for this invocation;
        targeted and full rebuilds use the same descendant rule while retaining
        every historical artifact on disk.
        """

        stage_by_name = self._validate_graph(tuple(stages))
        order = self._topological_order(stage_by_name)
        forced = self._forced_stages(stage_by_name)

        artifacts: dict[str, RetainedArtifact] = {}
        events: list[RunArtifactEvent] = []
        invalidated: dict[str, str] = {}

        for name in order:
            stage = stage_by_name[name]
            dependencies = {
                dependency: artifacts[dependency] for dependency in stage.dependencies
            }
            specification = self._specification(stage, dependencies)
            started = monotonic_ns()

            reason: str
            disposition: str
            artifact: RetainedArtifact | None = None
            if self.full:
                reason = "forced-full"
            elif name in forced:
                reason = "forced-stage"
            elif name in invalidated:
                reason = invalidated[name]
            else:
                resolution = self._resolve_specification(specification)
                if resolution.artifact is not None and resolution.disposition != "miss":
                    artifact = resolution.artifact
                    disposition = "hit"
                    reason = resolution.reason
                else:
                    reason = resolution.reason

            if artifact is None:
                outputs = stage.build(dependencies)
                if not isinstance(outputs, Mapping):
                    raise TypeError(f"stage {name!r} builder must return a mapping")
                for role, content in outputs.items():
                    if not isinstance(role, str) or not isinstance(content, bytes):
                        raise TypeError(
                            f"stage {name!r} builder outputs must map strings to bytes"
                        )
                artifact = self.store.put(specification, outputs=outputs)
                disposition = "build"
                # Every materialised miss invalidates all descendants.  Their
                # specifications are still formed with exact IDs, so a forced
                # descendant that happens to be byte-identical remains safely
                # content-addressed without replacing historical directories.
                invalidation_reason = (
                    reason if reason.startswith("upstream-") else f"upstream-{reason}"
                )
                for descendant in self._descendants(name, stage_by_name):
                    invalidated.setdefault(descendant, invalidation_reason)

            artifacts[name] = artifact
            elapsed_ms = max(0, (monotonic_ns() - started) // 1_000_000)
            events.append(
                RunArtifactEvent(
                    kind=stage.name,
                    scope=stage.name,
                    disposition=disposition,
                    reason=reason,
                    artifact_id=artifact.artifact_id,
                    elapsed_ms=elapsed_ms,
                )
            )

        return IncrementalResolution(artifacts=artifacts, events=tuple(events))

    @staticmethod
    def _specification(
        stage: IncrementalStage,
        dependencies: Mapping[str, RetainedArtifact],
    ) -> ArtifactSpecification:
        dependency_ids = tuple(dependencies[name].artifact_id for name in stage.dependencies)
        return ArtifactSpecification(
            kind=stage.name,
            contract_version=stage.contract_version,
            implementation_fingerprint=stage.implementation_fingerprint,
            dependency_manifest_fingerprint=stage.dependency_manifest_fingerprint,
            parameters=stage.parameters,
            upstream_artifact_ids=dependency_ids,
            partition_identities=stage.partition_identities,
            coverage_identities=stage.coverage_identities,
            validation_contract=stage.validation_contract,
            diagnostics=stage.diagnostics,
            status=stage.status,
        )

    def _resolve_specification(self, specification: ArtifactSpecification) -> ArtifactResolution:
        resolver = getattr(self.store, "resolve_specification", None)
        if not callable(resolver):
            raise TypeError("retained artifact store lacks resolve_specification")
        result = resolver(specification)
        if not isinstance(result, ArtifactResolution):
            raise TypeError("resolve_specification returned an invalid resolution")
        return result

    @staticmethod
    def _validate_graph(stages: tuple[IncrementalStage, ...]) -> dict[str, IncrementalStage]:
        stage_by_name: dict[str, IncrementalStage] = {}
        for stage in stages:
            if not isinstance(stage, IncrementalStage):
                raise DAGValidationError("incremental DAG stages are invalid")
            if stage.name in stage_by_name:
                raise DAGValidationError(f"duplicate incremental stage: {stage.name}")
            stage_by_name[stage.name] = stage
        for stage in stages:
            for dependency in stage.dependencies:
                if dependency not in stage_by_name:
                    raise UnknownDependencyError(
                        f"stage {stage.name!r} depends on unknown stage {dependency!r}"
                    )
        return stage_by_name

    @staticmethod
    def _topological_order(
        stage_by_name: Mapping[str, IncrementalStage],
    ) -> tuple[str, ...]:
        remaining = {name: set(stage.dependencies) for name, stage in stage_by_name.items()}
        order: list[str] = []
        while remaining:
            ready = sorted(name for name, dependencies in remaining.items() if not dependencies)
            if not ready:
                cycle = IncrementalDAGResolver._find_cycle(stage_by_name)
                raise StageCycleError(f"incremental stage dependency cycle: {' -> '.join(cycle)}")
            for name in ready:
                order.append(name)
                remaining.pop(name)
            ready_set = set(ready)
            for dependencies in remaining.values():
                dependencies.difference_update(ready_set)
        return tuple(order)

    @staticmethod
    def _find_cycle(stage_by_name: Mapping[str, IncrementalStage]) -> tuple[str, ...]:
        visiting: set[str] = set()
        visited: set[str] = set()
        stack: list[str] = []

        def visit(name: str) -> tuple[str, ...] | None:
            if name in visiting:
                return tuple([*stack[stack.index(name) :], name])
            if name in visited:
                return None
            visiting.add(name)
            stack.append(name)
            for dependency in sorted(stage_by_name[name].dependencies):
                found = visit(dependency)
                if found is not None:
                    return found
            stack.pop()
            visiting.remove(name)
            visited.add(name)
            return None

        for name in sorted(stage_by_name):
            found = visit(name)
            if found is not None:
                return found
        return ("<unknown>",)

    def _forced_stages(self, stage_by_name: Mapping[str, IncrementalStage]) -> set[str]:
        unknown = sorted(set(self.rebuild_stages) - set(stage_by_name))
        if unknown:
            raise UnknownStageError(f"unknown rebuild stage: {unknown[0]}")
        forced: set[str] = set(self.rebuild_stages)
        for name in self.rebuild_stages:
            forced.update(self._descendants(name, stage_by_name))
        return forced

    @staticmethod
    def _descendants(name: str, stage_by_name: Mapping[str, IncrementalStage]) -> set[str]:
        descendants: set[str] = set()
        pending = [name]
        while pending:
            parent = pending.pop()
            for child, stage in stage_by_name.items():
                if parent in stage.dependencies and child not in descendants:
                    descendants.add(child)
                    pending.append(child)
        return descendants


__all__ = [
    "ArtifactBuilder",
    "DAGValidationError",
    "IncrementalDAGResolver",
    "IncrementalResolution",
    "IncrementalStage",
    "StageCycleError",
    "UnknownDependencyError",
    "UnknownStageError",
]
