from __future__ import annotations

import hashlib
import inspect
import shutil
from pathlib import Path

from test_recompilation import prepared_config

import satn.compiler as compiler_module
import satn.pipeline as pipeline
from satn import compile
from satn.compiler import compile_network
from satn.retained_artifacts import RetainedArtifactStore


def _publication_digests(artifacts: dict[str, Path]) -> dict[str, str]:
    nondeterministic_containers = {"geopackage", "pdf", "review_zip"}
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in artifacts.items()
        if name not in {"run", "review_map", *nondeterministic_containers}
    }


def test_downstream_change_reuses_routing_and_matches_forced_cold_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"

    initial = compile(config, artifact_root=artifact_root)
    route_artifact_id = initial.metadata["routing_bundle_artifact_id"]
    assert initial.metadata["routing_bundle_disposition"] == "build"
    shutil.rmtree(config.publication.output_dir)
    config.compilation.criteria_version = "downstream-change"

    original_backbone = compiler_module.assemble_backbone_outward
    original_cross_spine = compiler_module.resolve_cross_spine_assembly

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("validated routing hit must bypass route assembly")

    monkeypatch.setattr(compiler_module, "assemble_backbone_outward", forbidden)
    monkeypatch.setattr(compiler_module, "resolve_cross_spine_assembly", forbidden)
    replayed = compile(config, artifact_root=artifact_root)

    assert replayed.metadata["semantic_bundle_disposition"] == "build"
    assert replayed.metadata["routing_bundle_disposition"] == "hit"
    assert replayed.metadata["routing_bundle_artifact_id"] == route_artifact_id
    replayed_digests = _publication_digests(replayed.artifacts)

    monkeypatch.setattr(compiler_module, "assemble_backbone_outward", original_backbone)
    monkeypatch.setattr(
        compiler_module,
        "resolve_cross_spine_assembly",
        original_cross_spine,
    )
    shutil.rmtree(config.publication.output_dir)
    config.compilation.full = True
    forced_cold = compile(config, artifact_root=artifact_root)

    assert forced_cold.metadata["routing_bundle_disposition"] == "build"
    assert forced_cold.metadata["routing_bundle_artifact_id"] == route_artifact_id
    assert forced_cold.run_id == replayed.run_id
    assert _publication_digests(forced_cold.artifacts) == replayed_digests
    assert (
        forced_cold.metadata["compilation_diagnostics"]
        == replayed.metadata["compilation_diagnostics"]
    )


def test_corrupt_routing_bundle_is_quarantined_and_rebuilt(tmp_path: Path) -> None:
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"
    cold = compile(config, artifact_root=artifact_root)
    route_artifact_id = cold.metadata["routing_bundle_artifact_id"]
    store = RetainedArtifactStore(artifact_root)
    route_artifact = store.resolve(route_artifact_id).artifact
    assert route_artifact is not None
    route_path = route_artifact.path / "outputs" / "routing-assembly-bundle"
    route_path.write_bytes(route_path.read_bytes() + b"corrupt")
    shutil.rmtree(config.publication.output_dir)
    config.compilation.criteria_version = "force-whole-network-miss"

    rebuilt = compile(config, artifact_root=artifact_root)

    assert rebuilt.metadata["routing_bundle_disposition"] == "build"
    assert len(rebuilt.metadata["routing_bundle_artifact_id"]) == 64
    assert any(
        path.name.startswith(route_artifact_id)
        for path in (artifact_root / "quarantine").iterdir()
    )


def test_targeted_routing_rebuild_bypasses_hit_and_retains_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"
    cold = compile(config, artifact_root=artifact_root)
    route_artifact_id = cold.metadata["routing_bundle_artifact_id"]
    shutil.rmtree(config.publication.output_dir)
    calls = 0
    original = compiler_module.assemble_backbone_outward

    def counted(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(compiler_module, "assemble_backbone_outward", counted)
    rebuilt = compile(
        config,
        artifact_root=artifact_root,
        rebuild_stages=("routing-assembly",),
    )

    assert calls == 1
    assert rebuilt.metadata["routing_bundle_disposition"] == "build"
    assert rebuilt.metadata["routing_bundle_artifact_id"] == route_artifact_id


def test_routing_retention_identity_failure_falls_back_to_complete_cold_compile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = prepared_config(tmp_path)

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise ValueError("synthetic routing identity failure")

    monkeypatch.setattr(pipeline, "_routing_input_identity", unavailable)
    result = compile(config, artifact_root=tmp_path / "retained")

    assert result.status == "complete"
    assert result.metadata["routing_bundle_disposition"] == "unavailable"
    assert result.metadata["routing_bundle_reason"] == "routing-identity-unavailable"
    assert result.metadata["semantic_bundle_disposition"] == "build"


def test_public_compile_network_signature_remains_unchanged() -> None:
    assert tuple(inspect.signature(compile_network).parameters) == (
        "config",
        "source",
        "runtime",
        "governed_input_fingerprint",
        "decision_resolver",
        "heartbeat",
        "cross_spine_progress",
    )
