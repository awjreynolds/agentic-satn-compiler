from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from satn.ea_fixed_point_convergence import (
    EAFixedPointAcquisition,
    EAFixedPointCompilation,
    EAFixedPointSnapshot,
    EAFixedPointSnapshotCreation,
    converge_ea_fixed_point,
)

FP_A = "a" * 64
FP_B = "b" * 64
FP_C = "c" * 64
GOVERNED_SOURCES = (("network.geojson", "d" * 64),)


@dataclass
class FakeConvergenceOperations:
    actual_fingerprints: list[str]
    next_route_inventories: list[tuple[str, ...]]
    compile_index: int = 0
    acquisition_supplements: list[tuple[str, ...]] = field(default_factory=list)
    crash_snapshot_once: bool = False
    snapshot_attempts: int = 0
    restored_snapshots: list[str] = field(default_factory=list)

    def restore(self, snapshot: EAFixedPointSnapshot) -> None:
        self.restored_snapshots.append(snapshot.snapshot_id)

    def compile(self, snapshot: EAFixedPointSnapshot) -> EAFixedPointCompilation:
        actual = self.actual_fingerprints[self.compile_index]
        self.compile_index += 1
        return EAFixedPointCompilation(
            expected_fingerprint=snapshot.primary_fingerprint,
            actual_fingerprint=actual,
            candidate_network=Path(f"candidate-{self.compile_index}.geojson"),
            urban_access_ms=self.compile_index * 10,
            topography_ms=self.compile_index * 20,
        )

    def acquire(
        self,
        snapshot: EAFixedPointSnapshot,
        compilation: EAFixedPointCompilation,
    ) -> EAFixedPointAcquisition:
        self.acquisition_supplements.append(snapshot.route_inventory)
        return EAFixedPointAcquisition(
            primary_fingerprint=compilation.actual_fingerprint,
            route_inventory=self.next_route_inventories.pop(0),
            evidence_path=Path(f"elevation-{self.compile_index}.geojson"),
        )

    def snapshot(
        self,
        previous: EAFixedPointSnapshot,
        acquisition: EAFixedPointAcquisition,
        iteration: int,
    ) -> EAFixedPointSnapshotCreation:
        self.snapshot_attempts += 1
        if self.crash_snapshot_once:
            self.crash_snapshot_once = False
            raise RuntimeError("simulated crash after acquisition")
        return EAFixedPointSnapshotCreation(
            snapshot=EAFixedPointSnapshot(
                snapshot_id=f"snapshot-{iteration}",
                manifest_sha256=str(iteration) * 64,
                primary_fingerprint=acquisition.primary_fingerprint,
                retained_sample_routes=Path(
                    f"snapshot-{iteration}/sample-routes.geojson"
                ),
                route_inventory=acquisition.route_inventory,
                governed_source_identities=GOVERNED_SOURCES,
                parent_snapshot_id=previous.snapshot_id,
                parent_manifest_sha256=previous.manifest_sha256,
            ),
            snapshot_seal_ms=30,
            snapshot_validation_ms=40,
        )


def test_two_cycle_convergence_accumulates_prior_routes_and_records_phase_timings(
    tmp_path: Path,
) -> None:
    initial = EAFixedPointSnapshot(
        snapshot_id="snapshot-0",
        manifest_sha256="0" * 64,
        primary_fingerprint=FP_A,
        retained_sample_routes=Path("snapshot-0/sample-routes.geojson"),
        route_inventory=("route-a",),
        governed_source_identities=GOVERNED_SOURCES,
    )
    operations = FakeConvergenceOperations(
        actual_fingerprints=[FP_B, FP_B],
        next_route_inventories=[("route-a", "route-b")],
    )

    result = converge_ea_fixed_point(
        initial,
        operations=operations,
        max_iterations=2,
        record_path=tmp_path / "convergence.json",
    )

    assert result.status == "converged"
    assert [
        (iteration.expected_fingerprint, iteration.actual_fingerprint)
        for iteration in result.iterations
    ] == [(FP_A, FP_B), (FP_B, FP_B)]
    assert operations.acquisition_supplements == [("route-a",)]
    assert result.final_snapshot.snapshot_id == "snapshot-1"
    assert result.final_snapshot.route_inventory == ("route-a", "route-b")
    assert result.iterations[0].timings.acquisition_ms >= 0
    assert result.iterations[0].timings.snapshot_seal_ms == 30
    assert result.iterations[0].timings.snapshot_validation_ms == 40
    assert result.iterations[0].timings.urban_access_ms == 10
    assert result.iterations[0].timings.topography_ms == 20
    assert result.iterations[1].timings.urban_access_ms == 20
    assert result.iterations[1].timings.topography_ms == 40
    assert result.iterations[0].governed_source_identities == GOVERNED_SOURCES
    assert (tmp_path / "convergence.json").is_file()


def test_three_set_convergence_retains_every_prior_sampled_alternative(
    tmp_path: Path,
) -> None:
    initial = EAFixedPointSnapshot(
        snapshot_id="snapshot-0",
        manifest_sha256="0" * 64,
        primary_fingerprint=FP_A,
        retained_sample_routes=Path("snapshot-0/sample-routes.geojson"),
        route_inventory=("route-a",),
        governed_source_identities=GOVERNED_SOURCES,
    )
    operations = FakeConvergenceOperations(
        actual_fingerprints=[FP_B, FP_C, FP_C],
        next_route_inventories=[
            ("route-a", "route-b"),
            ("route-a", "route-b", "route-c"),
        ],
    )

    result = converge_ea_fixed_point(
        initial,
        operations=operations,
        max_iterations=3,
        record_path=tmp_path / "convergence.json",
    )

    assert result.status == "converged"
    assert operations.acquisition_supplements == [
        ("route-a",),
        ("route-a", "route-b"),
    ]
    assert [
        (iteration.expected_fingerprint, iteration.actual_fingerprint)
        for iteration in result.iterations
    ] == [(FP_A, FP_B), (FP_B, FP_C), (FP_C, FP_C)]
    assert result.final_snapshot.route_inventory == (
        "route-a",
        "route-b",
        "route-c",
    )
    assert result.final_snapshot.parent_snapshot_id == "snapshot-1"
    assert result.final_snapshot.parent_manifest_sha256 == "1" * 64


def test_bounded_non_convergence_records_history_without_starting_extra_work(
    tmp_path: Path,
) -> None:
    record_path = tmp_path / "non-convergence.json"
    initial = EAFixedPointSnapshot(
        snapshot_id="snapshot-0",
        manifest_sha256="0" * 64,
        primary_fingerprint=FP_A,
        retained_sample_routes=Path("snapshot-0/sample-routes.geojson"),
        route_inventory=("route-a",),
        governed_source_identities=GOVERNED_SOURCES,
    )
    operations = FakeConvergenceOperations(
        actual_fingerprints=[FP_B, FP_C],
        next_route_inventories=[("route-a", "route-b")],
    )

    result = converge_ea_fixed_point(
        initial,
        operations=operations,
        max_iterations=2,
        record_path=record_path,
    )

    assert result.status == "non-converged"
    assert operations.compile_index == 2
    assert operations.acquisition_supplements == [("route-a",)]
    assert [
        (iteration.expected_fingerprint, iteration.actual_fingerprint)
        for iteration in result.iterations
    ] == [(FP_A, FP_B), (FP_B, FP_C)]
    record = record_path.read_text(encoding="utf-8")
    assert '"status": "non-converged"' in record
    assert f'"expected_fingerprint": "{FP_A}"' in record
    assert f'"actual_fingerprint": "{FP_C}"' in record


def test_existing_record_is_refused_before_any_expensive_work(tmp_path: Path) -> None:
    record_path = tmp_path / "existing.json"
    record_path.write_text('{"owned_by": "another run"}\n', encoding="utf-8")
    initial = EAFixedPointSnapshot(
        snapshot_id="snapshot-0",
        manifest_sha256="0" * 64,
        primary_fingerprint=FP_A,
        retained_sample_routes=Path("snapshot-0/sample-routes.geojson"),
        route_inventory=("route-a",),
        governed_source_identities=GOVERNED_SOURCES,
    )
    operations = FakeConvergenceOperations(
        actual_fingerprints=[FP_A],
        next_route_inventories=[],
    )

    with pytest.raises(ValueError, match="record already exists"):
        converge_ea_fixed_point(
            initial,
            operations=operations,
            max_iterations=1,
            record_path=record_path,
        )

    assert operations.compile_index == 0
    assert record_path.read_text(encoding="utf-8") == '{"owned_by": "another run"}\n'


def test_restart_resumes_after_completed_acquisition_without_repeating_it(
    tmp_path: Path,
) -> None:
    record_path = tmp_path / "resume.json"
    initial = EAFixedPointSnapshot(
        snapshot_id="snapshot-0",
        manifest_sha256="0" * 64,
        primary_fingerprint=FP_A,
        retained_sample_routes=Path("snapshot-0/sample-routes.geojson"),
        route_inventory=("route-a",),
        governed_source_identities=GOVERNED_SOURCES,
    )
    operations = FakeConvergenceOperations(
        actual_fingerprints=[FP_B, FP_B],
        next_route_inventories=[("route-a", "route-b")],
        crash_snapshot_once=True,
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        converge_ea_fixed_point(
            initial,
            operations=operations,
            max_iterations=2,
            record_path=record_path,
            run_token="test-run",
        )

    checkpoint = record_path.read_text(encoding="utf-8")
    assert '"status": "in-progress"' in checkpoint
    assert '"phase": "acquisition-complete"' in checkpoint
    assert f'"actual_fingerprint": "{FP_B}"' in checkpoint
    assert '"route-b"' in checkpoint

    result = converge_ea_fixed_point(
        None,
        operations=operations,
        max_iterations=2,
        record_path=record_path,
        run_token="test-run",
        resume=True,
    )

    assert result.status == "converged"
    assert operations.compile_index == 2
    assert operations.acquisition_supplements == [("route-a",)]
    assert operations.snapshot_attempts == 2
    assert operations.restored_snapshots == ["snapshot-0"]


def test_restart_refuses_a_different_configuration_identity(tmp_path: Path) -> None:
    record_path = tmp_path / "resume.json"
    initial = EAFixedPointSnapshot(
        snapshot_id="snapshot-0",
        manifest_sha256="0" * 64,
        primary_fingerprint=FP_A,
        retained_sample_routes=Path("snapshot-0/sample-routes.geojson"),
        route_inventory=("route-a",),
        governed_source_identities=GOVERNED_SOURCES,
    )
    operations = FakeConvergenceOperations(
        actual_fingerprints=[FP_B],
        next_route_inventories=[("route-a", "route-b")],
        crash_snapshot_once=True,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        converge_ea_fixed_point(
            initial,
            operations=operations,
            max_iterations=2,
            record_path=record_path,
            run_token="test-run",
            configuration_identity="1" * 64,
        )

    with pytest.raises(ValueError, match="record identity"):
        converge_ea_fixed_point(
            None,
            operations=operations,
            max_iterations=2,
            record_path=record_path,
            run_token="test-run",
            resume=True,
            configuration_identity="2" * 64,
        )

    assert operations.restored_snapshots == []
