from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import satn.compilation_dependencies as dependencies
from satn.pipeline import _compiler_digest

PROJECT = Path(__file__).parents[1]


def copied_compiler_tree(tmp_path: Path) -> Path:
    """Copy an installed-wheel-shaped SATN package, never a regional deployment."""
    root = tmp_path / "wheel-layout"
    shutil.copytree(PROJECT / "src" / "satn", root / "satn")
    return root / "satn"


def test_manifest_is_explicit_complete_and_records_component_digests() -> None:
    manifest = dependencies.compilation_dependency_manifest()

    assert manifest["schema_version"] == "satn-compilation-dependency-manifest/v2"
    assert manifest["dependency_set_version"] == "satn-compiled-network/v2"
    assert manifest["sha256"] == _compiler_digest()
    components = {component["path"] for component in manifest["components"]}
    assert {
        "satn/__init__.py",
        "satn/compiler.py",
        "satn/existing_alignment.py",
        "satn/routing.py",
        "satn/backbone.py",
        "satn/sources.py",
        "satn/models.py",
        "satn/education_access.py",
        "satn/ea_elevation.py",
        "satn/population_reach.py",
        "runtime-distribution/geopandas",
        "runtime-distribution/httpx",
        "runtime-distribution/networkx",
        "runtime-distribution/openai",
        "runtime-distribution/shapely",
    } <= components
    assert all(len(component["sha256"]) == 64 for component in manifest["components"])
    assert "satn/publisher.py" not in components
    assert "satn/pages_packaging.py" not in components
    assert "satn/assets/review-map.js" not in components
    population_component = next(
        component
        for component in manifest["components"]
        if component["path"] == "satn/population_reach.py"
    )
    assert population_component["kind"] == "module"
    assert population_component["reason"] == "governed Population Reach evidence assessment"
    assert all(not path.startswith("src/") for path in components)
    runtime_components = {
        component["path"]: component for component in manifest["components"]
    }
    for distribution in ("openai", "httpx"):
        assert runtime_components[f"runtime-distribution/{distribution}"]["version"] == (
            dependencies.metadata.version(distribution)
        )


def test_network_selection_contract_is_a_controlled_compilation_component(
    tmp_path: Path,
) -> None:
    root = copied_compiler_tree(tmp_path)
    original = dependencies.compilation_dependency_manifest(package_root=root)
    profile = root / "network_selection.py"
    profile.write_bytes(profile.read_bytes() + b"\n# dependency-manifest regression probe\n")

    changed = dependencies.compilation_dependency_manifest(package_root=root)

    assert "satn/network_selection.py" in {
        component["path"] for component in original["components"]
    }
    assert changed["sha256"] != original["sha256"]


def test_existing_alignment_contract_is_a_controlled_compilation_component(
    tmp_path: Path,
) -> None:
    root = copied_compiler_tree(tmp_path)
    original = dependencies.compilation_dependency_manifest(package_root=root)
    module = root / "existing_alignment.py"
    module.write_bytes(module.read_bytes() + b"\n# dependency-manifest regression probe\n")

    changed = dependencies.compilation_dependency_manifest(package_root=root)

    assert "satn/existing_alignment.py" in {
        component["path"] for component in original["components"]
    }
    assert changed["sha256"] != original["sha256"]


def test_compiler_semantic_module_changes_change_the_manifest_digest(tmp_path: Path) -> None:
    root = copied_compiler_tree(tmp_path)
    original = dependencies.compilation_dependency_manifest(package_root=root)

    for relative_path in (
        "compiler.py",
        "routing.py",
        "backbone.py",
        "sources.py",
        "models.py",
        "ea_elevation.py",
        "population_reach.py",
    ):
        path = root / relative_path
        original_bytes = path.read_bytes()
        path.write_bytes(original_bytes + b"\n# dependency-manifest regression probe\n")
        changed = dependencies.compilation_dependency_manifest(package_root=root)
        assert changed["sha256"] != original["sha256"]
        path.write_bytes(original_bytes)


def test_review_map_and_release_packaging_changes_do_not_change_the_digest(tmp_path: Path) -> None:
    root = copied_compiler_tree(tmp_path)
    original = dependencies.compilation_dependency_manifest(package_root=root)

    for relative_path in (
        "assets/review-map.js",
        "publisher.py",
        "pages_packaging.py",
    ):
        path = root / relative_path
        path.write_bytes(path.read_bytes() + b"\n/* dependency-manifest regression probe */\n")
        changed = dependencies.compilation_dependency_manifest(package_root=root)
        assert changed["sha256"] == original["sha256"]


@pytest.mark.parametrize("distribution", ("networkx", "openai", "httpx"))
def test_runtime_distribution_version_change_invalidates_manifest(
    distribution: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copied_compiler_tree(tmp_path)
    original = dependencies.compilation_dependency_manifest(package_root=root)
    recorded_version = dependencies.metadata.version

    monkeypatch.setattr(
        dependencies.metadata,
        "version",
        lambda queried_distribution: (
            "999.0-regression"
            if queried_distribution == distribution
            else recorded_version(queried_distribution)
        ),
    )

    changed = dependencies.compilation_dependency_manifest(package_root=root)

    assert changed["sha256"] != original["sha256"]
    component = next(
        component
        for component in changed["components"]
        if component["path"] == f"runtime-distribution/{distribution}"
    )
    assert component["version"] == "999.0-regression"


def test_non_compiler_tooling_versions_and_project_files_do_not_change_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copied_compiler_tree(tmp_path)
    project_root = root.parent
    (project_root / "pyproject.toml").write_text("[project]\nname = 'changed'\n")
    (project_root / "uv.lock").write_text("version = 1\n")
    original = dependencies.compilation_dependency_manifest(package_root=root)
    recorded_version = dependencies.metadata.version
    queried: list[str] = []

    def versions(distribution: str) -> str:
        queried.append(distribution)
        return "999.0-regression" if distribution in {
            "pytest",
            "ruff",
            "playwright",
            "pypdf",
            "reportlab",
            "hatchling",
        } else recorded_version(distribution)

    monkeypatch.setattr(dependencies.metadata, "version", versions)
    (project_root / "pyproject.toml").write_text("[build-system]\nrequires = ['changed']\n")
    (project_root / "uv.lock").write_text("version = 2\n")

    unchanged = dependencies.compilation_dependency_manifest(package_root=root)

    assert unchanged["sha256"] == original["sha256"]
    assert not {
        "pytest",
        "ruff",
        "playwright",
        "pypdf",
        "reportlab",
        "hatchling",
    } & set(queried)


def test_installed_package_discovery_ignores_cache_but_fails_closed_for_new_file(
    tmp_path: Path,
) -> None:
    root = copied_compiler_tree(tmp_path)
    original = dependencies.compilation_dependency_manifest(package_root=root)
    cache = root / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "compiler.cpython-312.pyc").write_bytes(b"not-source")

    assert (
        dependencies.compilation_dependency_manifest(package_root=root)["sha256"]
        == original["sha256"]
    )

    (root / "unclassified-resource.txt").write_text("must be classified\n")
    with pytest.raises(
        ValueError,
        match=r"unclassified controlled component: satn/unclassified-resource\.txt",
    ):
        dependencies.compilation_dependency_manifest(package_root=root)


def test_text_line_endings_are_canonicalised_before_component_hashing(tmp_path: Path) -> None:
    root = copied_compiler_tree(tmp_path)
    original = dependencies.compilation_dependency_manifest(package_root=root)
    compiler = root / "compiler.py"
    compiler.write_bytes(compiler.read_bytes().replace(b"\n", b"\r\n"))

    assert (
        dependencies.compilation_dependency_manifest(package_root=root)["sha256"]
        == original["sha256"]
    )


@pytest.mark.parametrize("distribution", ("networkx", "openai", "httpx"))
def test_missing_required_runtime_distribution_fails_closed(
    distribution: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copied_compiler_tree(tmp_path)
    recorded_version = dependencies.metadata.version

    def unavailable(queried_distribution: str) -> str:
        if queried_distribution == distribution:
            raise dependencies.metadata.PackageNotFoundError(queried_distribution)
        return recorded_version(queried_distribution)

    monkeypatch.setattr(dependencies.metadata, "version", unavailable)

    with pytest.raises(
        ValueError,
        match=rf"required compiler runtime distribution is unavailable: {distribution}",
    ):
        dependencies.compilation_dependency_manifest(package_root=root)


def test_missing_or_unclassified_components_fail_closed() -> None:
    without_routing = dict(dependencies.COMPILATION_COMPONENTS)
    without_routing.pop("satn/routing.py")
    with pytest.raises(ValueError, match=r"unclassified controlled component: satn/routing.py"):
        dependencies.compilation_dependency_manifest(components=without_routing)

    with_missing_path = dict(dependencies.COMPILATION_COMPONENTS)
    with_missing_path["satn/not-a-module.py"] = ("module", "regression probe")
    with pytest.raises(ValueError, match=r"missing registered component: satn/not-a-module.py"):
        dependencies.compilation_dependency_manifest(components=with_missing_path)
