from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from satn.retained_artifacts import (
    ArtifactSpecification,
    CompilationRunReport,
    RetainedArtifactStore,
    RunArtifactEvent,
)


def area_extraction_specification() -> ArtifactSpecification:
    return ArtifactSpecification(
        kind="area-extraction",
        contract_version="area-extraction/v1",
        implementation_fingerprint="1" * 64,
        dependency_manifest_fingerprint="2" * 64,
        parameters={"predicate": "intersects", "working_crs": "EPSG:27700"},
        upstream_artifact_ids=(),
        partition_identities=("ST57NW",),
        coverage_identities=("3" * 64,),
        validation_contract="area-extraction-validation/v1",
        diagnostics={"feature_count": 2, "unknown_count": 0},
    )


def test_retained_artifact_can_be_resolved_from_another_store_instance(
    tmp_path: Path,
) -> None:
    written = RetainedArtifactStore.in_workspace(tmp_path).put(
        area_extraction_specification(),
        outputs={
            "features": b"governed feature bytes\n",
            "coverage-report": b'{"missing":[]}\n',
        },
    )

    resolution = RetainedArtifactStore.in_workspace(tmp_path).resolve(written.artifact_id)

    assert resolution.disposition == "validated-hit"
    assert resolution.reason == "validated-dependency-closure"
    assert resolution.artifact is not None
    assert resolution.artifact.manifest.kind == "area-extraction"
    assert resolution.artifact.read_output("features") == b"governed feature bytes\n"
    assert len(written.artifact_id) == 64


def test_retained_artifact_can_be_resolved_from_its_semantic_specification(
    tmp_path: Path,
) -> None:
    store = RetainedArtifactStore.in_workspace(tmp_path)
    specification = area_extraction_specification()
    written = store.put(specification, outputs={"features": b"features"})

    resolution = RetainedArtifactStore.in_workspace(
        tmp_path
    ).resolve_specification(specification)

    assert resolution.disposition == "validated-hit"
    assert resolution.reason == "validated-dependency-closure"
    assert resolution.artifact is not None
    assert resolution.artifact.artifact_id == written.artifact_id


def test_specification_resolution_fails_closed_for_nondeterministic_outputs(
    tmp_path: Path,
) -> None:
    store = RetainedArtifactStore.in_workspace(tmp_path)
    specification = area_extraction_specification()
    store.put(specification, outputs={"features": b"first"})
    store.put(specification, outputs={"features": b"second"})

    resolution = store.resolve_specification(specification)

    assert resolution.disposition == "miss"
    assert resolution.reason == "nondeterministic-output-candidates"
    assert resolution.artifact is None


def test_corrupt_retained_artifact_is_quarantined_and_becomes_a_miss(
    tmp_path: Path,
) -> None:
    store = RetainedArtifactStore.in_workspace(tmp_path)
    written = store.put(
        area_extraction_specification(),
        outputs={"features": b"governed feature bytes\n"},
    )
    (written.path / "outputs" / "features").write_bytes(b"corrupt bytes\n")

    resolution = store.resolve(written.artifact_id)

    assert resolution.disposition == "miss"
    assert resolution.reason == "output-digest-mismatch"
    assert resolution.artifact is None
    assert resolution.quarantined_path is not None
    assert resolution.quarantined_path.is_dir()
    assert not written.path.exists()


def test_artifact_identity_is_independent_of_workspace_and_input_order(
    tmp_path: Path,
) -> None:
    forward = area_extraction_specification()
    reverse = replace(
        forward,
        partition_identities=("ST67NW", "ST57NW"),
        coverage_identities=("4" * 64, "3" * 64),
    )
    canonical = replace(
        forward,
        partition_identities=("ST57NW", "ST67NW"),
        coverage_identities=("3" * 64, "4" * 64),
    )

    first = RetainedArtifactStore.in_workspace(tmp_path / "first").put(
        reverse,
        outputs={"z-report": b"z", "a-features": b"a"},
    )
    second = RetainedArtifactStore.in_workspace(tmp_path / "second").put(
        canonical,
        outputs={"a-features": b"a", "z-report": b"z"},
    )

    assert first.artifact_id == second.artifact_id
    assert first.manifest.bytes() == second.manifest.bytes()


def test_manifest_preserves_json_arrays_that_look_like_object_pairs(tmp_path: Path) -> None:
    parameters = {"pairs": [["a", 1], ["b", 2]]}

    artifact = RetainedArtifactStore.in_workspace(tmp_path).put(
        replace(area_extraction_specification(), parameters=parameters),
        outputs={"features": b"features"},
    )

    assert artifact.manifest.identity_payload()["parameters"] == parameters


def test_resolve_validates_the_complete_upstream_dependency_closure(
    tmp_path: Path,
) -> None:
    store = RetainedArtifactStore.in_workspace(tmp_path)
    upstream = store.put(
        replace(
            area_extraction_specification(),
            kind="source-export",
            contract_version="source-export/v1",
            validation_contract="source-export-validation/v1",
        ),
        outputs={"source": b"governed source"},
    )
    downstream = store.put(
        replace(
            area_extraction_specification(),
            upstream_artifact_ids=(upstream.artifact_id,),
        ),
        outputs={"features": b"derived features"},
    )
    (upstream.path / "outputs" / "source").write_bytes(b"corrupt source")

    resolution = store.resolve(downstream.artifact_id)

    assert resolution.disposition == "miss"
    assert resolution.reason == "upstream-output-digest-mismatch"
    assert downstream.path.is_dir()
    assert not upstream.path.exists()


def test_compilation_run_report_is_atomic_immutable_and_nonsemantic(
    tmp_path: Path,
) -> None:
    store = RetainedArtifactStore.in_workspace(tmp_path)
    artifact = store.put(
        area_extraction_specification(),
        outputs={"features": b"governed features"},
    )
    report = CompilationRunReport(
        run_id="2026-08-05T211642Z-fixture",
        area_definition="fixture",
        mode="incremental",
        result="complete",
        started_at="2026-08-05T21:16:42Z",
        finished_at="2026-08-05T21:16:44Z",
        workers={"requested": "auto", "selected": 2},
        artifact_events=(
            RunArtifactEvent(
                kind="area-extraction",
                scope="ST57NW",
                disposition="build",
                reason="not-found",
                artifact_id=artifact.artifact_id,
                elapsed_ms=381,
            ),
        ),
        stitch=None,
        publication={"validation": "passed", "replacement": "atomic"},
        peak_rss_bytes=1024,
    )

    path = store.write_run_report(report)
    restored = RetainedArtifactStore.in_workspace(tmp_path).read_run_report(report.run_id)

    assert path.is_file()
    assert restored == report
    assert artifact.artifact_id == store.resolve(artifact.artifact_id).artifact.artifact_id


def test_gc_plan_protects_every_artifact_reachable_from_a_publication(
    tmp_path: Path,
) -> None:
    store = RetainedArtifactStore.in_workspace(tmp_path)
    upstream = store.put(
        replace(area_extraction_specification(), kind="canonical-network"),
        outputs={"network": b"network"},
    )
    publication = store.put(
        replace(
            area_extraction_specification(),
            kind="publication",
            upstream_artifact_ids=(upstream.artifact_id,),
        ),
        outputs={"site": b"site"},
    )
    orphan = store.put(
        replace(area_extraction_specification(), kind="orphan-diagnostic"),
        outputs={"diagnostic": b"diagnostic"},
    )
    store.pin("publication", "fixture", publication.artifact_id)
    now = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)
    old = (now - timedelta(days=30)).timestamp()
    for artifact in (upstream, publication, orphan):
        os.utime(artifact.path, (old, old))

    plan = store.plan_garbage_collection(
        grace_period=timedelta(days=7),
        now=now,
    )

    assert plan.reachable_artifact_ids == tuple(
        sorted((upstream.artifact_id, publication.artifact_id))
    )
    assert tuple(candidate.artifact_id for candidate in plan.candidates) == (
        orphan.artifact_id,
    )
    assert orphan.path.is_dir()

    with pytest.raises(ValueError, match="explicit confirmation"):
        store.collect_garbage(plan, confirm=False)
    collected = store.collect_garbage(plan, confirm=True)

    assert collected.removed_artifact_ids == (orphan.artifact_id,)
    assert not orphan.path.exists()
    assert upstream.path.is_dir()
    assert publication.path.is_dir()


def test_committed_gc_is_recovered_after_interruption(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = RetainedArtifactStore.in_workspace(tmp_path)
    orphan = store.put(
        replace(area_extraction_specification(), kind="orphan-diagnostic"),
        outputs={"diagnostic": b"diagnostic"},
    )
    now = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)
    old = (now - timedelta(days=30)).timestamp()
    os.utime(orphan.path, (old, old))
    plan = store.plan_garbage_collection(
        grace_period=timedelta(days=7),
        now=now,
    )
    original_rmtree = __import__("shutil").rmtree

    def interrupt_staging_cleanup(path: Path, *args: object, **kwargs: object) -> None:
        if "gc-staging" in Path(path).parts:
            raise SystemExit("simulated interruption after durable GC commit")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        "satn.retained_artifacts.shutil.rmtree",
        interrupt_staging_cleanup,
    )
    with pytest.raises(SystemExit, match="simulated interruption"):
        store.collect_garbage(plan, confirm=True)
    monkeypatch.undo()

    reopened = RetainedArtifactStore.in_workspace(tmp_path)

    assert reopened.resolve(orphan.artifact_id).reason == "not-found"
    assert not (reopened.root / "gc-staging").exists()


def test_gc_recovery_rejects_a_staged_artifact_symlink(tmp_path: Path) -> None:
    store_root = tmp_path / ".satn"
    transaction_root = store_root / "gc-staging" / "interrupted"
    transaction_root.mkdir(parents=True)
    artifact_id = "a" * 64
    outside = tmp_path / "outside"
    outside.mkdir()
    (transaction_root / artifact_id).symlink_to(outside, target_is_directory=True)
    (transaction_root / "transaction.json").write_text(
        json.dumps(
            {
                "schema": "satn-artifact-gc-transaction/v1",
                "state": "moving",
                "artifact_ids": [artifact_id],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="staged artifact is invalid"):
        RetainedArtifactStore(store_root)

    assert (transaction_root / artifact_id).is_symlink()
    assert not (
        store_root / "artifacts" / "sha256" / artifact_id[:2] / artifact_id
    ).exists()


def test_committed_gc_recovery_rejects_a_staged_symlink(tmp_path: Path) -> None:
    store_root = tmp_path / ".satn"
    transaction_root = store_root / "gc-staging" / "committed"
    transaction_root.mkdir(parents=True)
    artifact_id = "b" * 64
    outside = tmp_path / "outside"
    outside.mkdir()
    (transaction_root / artifact_id).symlink_to(outside, target_is_directory=True)
    (transaction_root / "transaction.json").write_text(
        json.dumps(
            {
                "schema": "satn-artifact-gc-transaction/v1",
                "state": "committed",
                "artifact_ids": [artifact_id],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="staged artifact is invalid"):
        RetainedArtifactStore(store_root)

    assert (transaction_root / artifact_id).is_symlink()
    assert outside.is_dir()
