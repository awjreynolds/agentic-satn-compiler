from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from test_recompilation import prepared_config

import satn.pipeline as pipeline
from satn import compile
from satn.retained_artifacts import ArtifactSpecification, RetainedArtifactStore


def _publication_digests(artifacts: dict[str, Path]) -> dict[str, str]:
    # SQLite/GDAL, PDF and ZIP containers carry producer metadata. Compare the
    # canonical publication payloads rather than container bytes.
    nondeterministic_containers = {"geopackage", "pdf", "review_zip"}
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in artifacts.items()
        if name not in {"run", "review_map", *nondeterministic_containers}
    }


def test_removed_publication_is_rehydrated_from_complete_semantic_bundle(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"

    cold = compile(config, artifact_root=artifact_root, explain_reuse=True)
    artifact_id = cold.metadata["semantic_bundle_artifact_id"]
    cold_digests = _publication_digests(cold.artifacts)
    assert cold.metadata["semantic_bundle_disposition"] == "build"
    assert len(artifact_id) == 64

    shutil.rmtree(config.publication.output_dir)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("semantic bundle hit must bypass governed compilation")

    monkeypatch.setattr(pipeline, "load_snapshot", forbidden)
    monkeypatch.setattr(pipeline, "compile_network", forbidden)

    rehydrated = compile(config, artifact_root=artifact_root, explain_reuse=True)

    assert rehydrated.run_id == cold.run_id
    assert rehydrated.metadata["semantic_compilation_reused"] is True
    assert rehydrated.metadata["semantic_bundle_disposition"] == "hit"
    assert rehydrated.metadata["semantic_bundle_artifact_id"] == artifact_id
    assert _publication_digests(rehydrated.artifacts) == cold_digests
    report = RetainedArtifactStore(artifact_root).read_run_report(
        Path(rehydrated.metadata["compilation_run_report"]).stem
    )
    semantic_event = report.artifact_events[0]
    assert semantic_event.disposition == "hit"
    assert semantic_event.artifact_id == artifact_id
    assert report.artifact_events[1].disposition == "build"
    assert report.payload()["publication"]["replacement"] == "atomic"
    assert json.loads(rehydrated.artifacts["run"].read_text())["run_id"] == cold.run_id


def test_corrupt_bundle_is_quarantined_and_rebuilt_from_governed_inputs(
    tmp_path: Path,
) -> None:
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"
    cold = compile(config, artifact_root=artifact_root)
    artifact_id = cold.metadata["semantic_bundle_artifact_id"]
    store = RetainedArtifactStore(artifact_root)
    resolved = store.resolve(artifact_id)
    assert resolved.artifact is not None
    bundle_path = resolved.artifact.path / "outputs" / "compiled-network-bundle"
    bundle_path.write_bytes(bundle_path.read_bytes() + b"corrupt")
    shutil.rmtree(config.publication.output_dir)

    rebuilt = compile(config, artifact_root=artifact_root)

    assert rebuilt.metadata["semantic_bundle_disposition"] == "build"
    assert len(rebuilt.metadata["semantic_bundle_artifact_id"]) == 64
    quarantine = artifact_root / "quarantine"
    assert any(path.name.startswith(artifact_id) for path in quarantine.iterdir())


def test_full_compile_bypasses_retained_semantic_hit(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"
    compile(config, artifact_root=artifact_root)
    shutil.rmtree(config.publication.output_dir)
    config.compilation.full = True
    original = pipeline.compile_network
    calls = 0

    def counted(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline, "compile_network", counted)
    rebuilt = compile(config, artifact_root=artifact_root)

    assert calls == 1
    assert rebuilt.metadata["semantic_compilation_reused"] is False
    assert rebuilt.metadata["semantic_bundle_disposition"] == "unavailable"


def test_storage_valid_bundle_with_wrong_bound_identity_is_quarantined(
    tmp_path: Path,
) -> None:
    config = prepared_config(tmp_path)
    artifact_root = tmp_path / "retained"
    cold = compile(config, artifact_root=artifact_root)
    store = RetainedArtifactStore(artifact_root)
    original = store.resolve(cold.metadata["semantic_bundle_artifact_id"]).artifact
    assert original is not None
    manifest = original.manifest
    manifest_payload = manifest.identity_payload()
    specification = ArtifactSpecification(
        kind=manifest.kind,
        contract_version=manifest.contract_version,
        implementation_fingerprint=manifest.implementation_fingerprint,
        dependency_manifest_fingerprint=manifest.dependency_manifest_fingerprint,
        parameters=manifest_payload["parameters"],
        upstream_artifact_ids=manifest.upstream_artifact_ids,
        partition_identities=manifest.partition_identities,
        coverage_identities=manifest.coverage_identities,
        validation_contract=manifest.validation_contract,
        diagnostics=manifest_payload["diagnostics"],
        status=manifest.status,
    )
    payload = json.loads(original.read_output("compiled-network-bundle"))
    payload["identities"]["area"] = "b" * 64
    body = {key: value for key, value in payload.items() if key != "content_sha256"}
    payload["content_sha256"] = hashlib.sha256(
        json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    store.reject_semantic_artifact(original.artifact_id, reason="test-replacement")
    forged = store.put(
        specification,
        outputs={
            "compiled-network-bundle": (
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
            )
        },
    )
    shutil.rmtree(config.publication.output_dir)

    rebuilt = compile(config, artifact_root=artifact_root)

    assert rebuilt.metadata["semantic_bundle_disposition"] == "build"
    quarantine = artifact_root / "quarantine"
    assert any(
        path.name.startswith(forged.artifact_id + "-semantic-contract-invalid")
        for path in quarantine.iterdir()
    )
