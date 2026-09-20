from __future__ import annotations

from pathlib import Path

import pytest

import satn.compilation_dependencies as dependencies


def test_manifest_is_an_explicit_revision_record() -> None:
    assert dependencies.compilation_dependency_manifest() == {
        "schema_version": "satn-compilation-dependency-manifest/v4",
        "compiler_cache_revision": dependencies.COMPILER_CACHE_REVISION,
        "compiler_path": "network",
    }


@pytest.mark.parametrize(
    "compiler_path",
    ("network", "reference", "strategic-reference", "ea-recovery"),
)
def test_manifest_preserves_compiler_path(compiler_path: str) -> None:
    manifest = dependencies.compilation_dependency_manifest(compiler_path=compiler_path)

    assert manifest["compiler_path"] == compiler_path


def test_manifest_does_not_scan_or_read_package_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("compiler dependency manifests do not inspect source trees")

    monkeypatch.setattr(Path, "rglob", fail)
    monkeypatch.setattr(Path, "read_bytes", fail)

    assert dependencies.compilation_dependency_manifest() == {
        "schema_version": "satn-compilation-dependency-manifest/v4",
        "compiler_cache_revision": dependencies.COMPILER_CACHE_REVISION,
        "compiler_path": "network",
    }


def test_revision_change_is_cache_identity_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = dependencies.compilation_dependency_manifest()
    monkeypatch.setattr(dependencies, "COMPILER_CACHE_REVISION", "poc-test-change")

    after = dependencies.compilation_dependency_manifest()

    assert after["compiler_cache_revision"] == "poc-test-change"
    assert after != before


def test_validator_accepts_revision_record_and_rejects_legacy_hash() -> None:
    manifest = dependencies.compilation_dependency_manifest()

    assert dependencies.validate_compilation_dependency_manifest(manifest) == manifest

    with pytest.raises(ValueError, match="schema is unsupported"):
        dependencies.validate_compilation_dependency_manifest({**manifest, "sha256": "a" * 64})


@pytest.mark.parametrize("revision", ("", "Poc-v2", "poc v2", "poc:v2"))
def test_validator_rejects_invalid_revision(revision: str) -> None:
    manifest = dependencies.compilation_dependency_manifest()
    manifest["compiler_cache_revision"] = revision

    with pytest.raises(ValueError, match="compiler cache revision is invalid"):
        dependencies.validate_compilation_dependency_manifest(manifest)
