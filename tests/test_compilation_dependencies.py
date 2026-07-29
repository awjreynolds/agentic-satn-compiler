from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import satn.compilation_dependencies as dependencies
from satn.models import AreaDefinition
from satn.pipeline import _compiler_digest

PROJECT = Path(__file__).parents[1]


def copied_compiler_tree(tmp_path: Path) -> Path:
    """Copy an installed-wheel-shaped SATN package, never a regional deployment."""
    root = tmp_path / "wheel-layout"
    shutil.copytree(PROJECT / "src" / "satn", root / "satn")
    return root / "satn"


def test_manifest_is_explicit_complete_and_records_component_digests() -> None:
    manifest = dependencies.compilation_dependency_manifest()

    assert manifest["schema_version"] == "satn-compilation-dependency-manifest/v3"
    assert manifest["dependency_set_version"] == "satn-compiled-network/v3"
    assert manifest["sha256"] == _compiler_digest()
    components = {component["path"] for component in manifest["components"]}
    assert {
        "satn/__init__.py",
        "satn/compiler.py",
        "satn/existing_alignment.py",
        "satn/routing.py",
        "satn/backbone.py",
        "satn/sources.py",
        "satn/streaming_geojson.py",
        "satn/models.py",
        "satn/education_access.py",
        "satn/ea_elevation.py",
        "satn/population_reach.py",
        "satn/alignment_selection.py",
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
    alignment_component = next(
        component
        for component in manifest["components"]
        if component["path"] == "satn/alignment_selection.py"
    )
    assert (
        alignment_component["reason"]
        == "deterministic Preferred Strategic Alignment selection contract"
    )
    assert all(not path.startswith("src/") for path in components)
    runtime_components = {
        component["path"]: component for component in manifest["components"]
    }
    for distribution in ("openai", "httpx"):
        assert runtime_components[f"runtime-distribution/{distribution}"]["version"] == (
            dependencies.metadata.version(distribution)
        )


def test_banes_manifest_records_resolved_configuration_sensitive_dependency_set() -> None:
    config = AreaDefinition.from_yaml(PROJECT / "deployments" / "banes" / "area.yaml")

    manifest = dependencies.compilation_dependency_manifest(config)
    selected = {component["path"] for component in manifest["components"]}
    inactive = {component["path"] for component in manifest["inactive_components"]}

    assert manifest["selection"]["compiler_path"] == "network"
    assert manifest["selection"]["configuration_sensitive"] is True
    assert manifest["selection"]["active_groups"] == [
        "core",
        "elevation-source",
        "osm-source",
    ]
    assert manifest["selection"]["component_paths"] == sorted(selected)
    assert "satn/routing.py" in selected
    assert "satn/ea_elevation.py" in selected
    assert "runtime-distribution/openai" in inactive
    assert "satn/psa_evidence_loaders.py" in inactive
    assert "satn/network_selection.py" in inactive


def test_manifest_self_validation_binds_the_resolved_selection_and_digest() -> None:
    config = AreaDefinition.from_yaml(PROJECT / "deployments" / "banes" / "area.yaml")
    manifest = dependencies.compilation_dependency_manifest(config)

    assert dependencies.validate_compilation_dependency_manifest(manifest) == manifest

    tampered = dict(manifest)
    tampered_selection = dict(manifest["selection"])
    tampered_selection["compiler_path"] = "reference"
    tampered["selection"] = tampered_selection
    with pytest.raises(ValueError, match="digest is stale"):
        dependencies.validate_compilation_dependency_manifest(tampered)


def test_unused_validator_change_does_not_invalidate_banes_but_core_change_does(
    tmp_path: Path,
) -> None:
    root = copied_compiler_tree(tmp_path)
    config = AreaDefinition.from_yaml(PROJECT / "deployments" / "banes" / "area.yaml")
    original = dependencies.compilation_dependency_manifest(config, package_root=root)
    unused_validator = root / "psa_evidence_loaders.py"
    validator_bytes = unused_validator.read_bytes()
    unused_validator.write_bytes(
        validator_bytes + b"\n# inactive dependency regression probe\n"
    )

    unchanged = dependencies.compilation_dependency_manifest(config, package_root=root)
    assert unchanged["sha256"] == original["sha256"]

    unused_validator.write_bytes(validator_bytes)
    routing = root / "routing.py"
    routing.write_bytes(routing.read_bytes() + b"\n# core dependency regression probe\n")
    changed = dependencies.compilation_dependency_manifest(config, package_root=root)
    assert changed["sha256"] != original["sha256"]


def test_active_network_selection_validator_change_invalidates_fixture(
    tmp_path: Path,
) -> None:
    root = copied_compiler_tree(tmp_path)
    config = AreaDefinition.from_yaml(
        PROJECT / "tests" / "fixtures" / "bath-saltford" / "bath-saltford.yaml"
    )
    original = dependencies.compilation_dependency_manifest(config, package_root=root)
    validator = root / "psa_evidence_loaders.py"
    validator.write_bytes(validator.read_bytes() + b"\n# active dependency regression probe\n")

    changed = dependencies.compilation_dependency_manifest(config, package_root=root)

    assert "network-selection" in changed["selection"]["active_groups"]
    assert changed["sha256"] != original["sha256"]


def test_strategic_reference_path_adds_replay_dependency() -> None:
    config = AreaDefinition.from_yaml(
        PROJECT / "tests" / "fixtures" / "bath-saltford" / "bath-saltford.yaml"
    )

    ordinary = dependencies.compilation_dependency_manifest(config)
    strategic = dependencies.compilation_dependency_manifest(
        config,
        compiler_path="strategic-reference",
    )

    ordinary_paths = {component["path"] for component in ordinary["components"]}
    strategic_paths = {component["path"] for component in strategic["components"]}
    assert "satn/strategic_reference_replay.py" not in ordinary_paths
    assert "satn/strategic_reference_replay.py" in strategic_paths
    assert strategic["sha256"] != ordinary["sha256"]


def test_external_direct_runtime_versions_are_selected_only_when_configured() -> None:
    fixture = AreaDefinition.from_yaml(PROJECT / "examples" / "fixture" / "council.yaml")
    external_agent = fixture.compilation.agent.model_copy(
        update={"provider": "openai", "model": "test-model", "response_mode": "direct-runtime"}
    )
    external_config = fixture.model_copy(
        update={
            "compilation": fixture.compilation.model_copy(
                update={"agent": external_agent}
            )
        }
    )

    caller_manifest = dependencies.compilation_dependency_manifest(fixture)
    external_manifest = dependencies.compilation_dependency_manifest(external_config)
    caller_paths = {component["path"] for component in caller_manifest["components"]}
    external_paths = {component["path"] for component in external_manifest["components"]}

    assert "runtime-distribution/openai" not in caller_paths
    assert "runtime-distribution/openai" in external_paths
    assert "direct-agent-runtime" in external_manifest["selection"]["active_groups"]


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


def test_streaming_geojson_validation_changes_compiler_identity(tmp_path: Path) -> None:
    root = copied_compiler_tree(tmp_path)
    original = dependencies.compilation_dependency_manifest(package_root=root)
    parser = root / "streaming_geojson.py"
    parser.write_bytes(parser.read_bytes() + b"\n# dependency-manifest regression probe\n")

    changed = dependencies.compilation_dependency_manifest(package_root=root)

    component = next(
        item
        for item in original["components"]
        if item["path"] == "satn/streaming_geojson.py"
    )
    assert component["reason"] == "strict bounded validation of governed GeoJSON snapshot inputs"
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
