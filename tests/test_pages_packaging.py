from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from satn.constants import DISCLAIMER
from satn.deployment import DEFERRED_GROUPS, build_area_deployment
from satn.deployment_catalogue import generate_catalogue_lock
from satn.deployment_provenance import generate_lock
from satn.models import AgentDecisionLedger, AreaDefinition
from satn.pages_packaging import (
    DEFAULT_MAXIMUM_BYTES,
    GITHUB_PAGES_LIMIT_BYTES,
    package_pages,
)
from satn.pages_packaging import (
    _validate_review_map_zip as validate_packaged_review_map_zip,
)
from satn.pipeline import (
    compilation_governed_input_fingerprint,
    compile,
    decision_ledger_input_fingerprint,
    snapshot_manifest_sha256,
)
from satn.sources import snapshot

PROJECT = Path(__file__).parents[1]
_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_pages_release", PROJECT / "scripts" / "validate_pages_release.py"
)
assert _VALIDATOR_SPEC and _VALIDATOR_SPEC.loader
_VALIDATOR = importlib.util.module_from_spec(_VALIDATOR_SPEC)
sys.modules[_VALIDATOR_SPEC.name] = _VALIDATOR
_VALIDATOR_SPEC.loader.exec_module(_VALIDATOR)
validate_pages_release = _VALIDATOR.validate_pages_release


def test_standalone_validator_uses_the_packager_size_budget() -> None:
    assert _VALIDATOR.DEFAULT_MAXIMUM_BYTES == DEFAULT_MAXIMUM_BYTES


def write_catalogue(path: Path) -> None:
    definition = path.parent / "test-area" / "area.yaml"
    definition.parent.mkdir(parents=True, exist_ok=True)
    definition.write_text(
        """area_id: test-geography
area_name: Test area
deployment_id: test-area
source:
  snapshot_dir: ../snapshots
publication:
  output_dir: build
  title: Test area deployment
""",
        encoding="utf-8",
    )
    path.write_text(
        """schema_version: satn-deployment-catalogue/v1
title: Test deployments
deployments:
  - deployment_id: test-area
    area_id: test-geography
    area_name: Test area
    area_definition: test-area/area.yaml
    deployment_path: deployments/test-area/
    artifacts:
      review_map: index.html
      network_map_pdf: network-map.pdf
      review_map_zip: review-map.zip
""",
        encoding="utf-8",
    )
    generate_catalogue_lock(path)


def empty_layer_groups() -> dict[str, dict[str, object]]:
    """Return the exact progressive contract, including empty feature types."""
    return {
        group: {
            "feature_types": sorted(feature_types),
            "feature_count": 0,
            "size_bytes": 0,
            "shards": [],
            "types": {
                feature_type: {"feature_count": 0, "size_bytes": 0, "shards": []}
                for feature_type in sorted(feature_types)
            },
        }
        for group, feature_types in sorted(DEFERRED_GROUPS.items())
    }


def write_bundle(root: Path, *, accepted_decisions: list[dict[str, str]] | None = None) -> None:
    bundle = root / "test-area"
    (bundle / "assets").mkdir(parents=True, exist_ok=True)
    (bundle / "index.html").write_text("<h1>Test area</h1>", encoding="utf-8")
    (bundle / "network-map.pdf").write_bytes(b"%PDF-test")
    (bundle / "network.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8"
    )
    (bundle / "assets" / "map.js").write_text("window.map = true;", encoding="utf-8")
    snapshot = root.parent / "snapshots" / "current" / "snapshot.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text('{"snapshot_id":"current"}', encoding="utf-8")
    definition_path = root.parent / "test-area" / "area.yaml"
    definition = AreaDefinition.from_yaml(definition_path)
    ledger = AgentDecisionLedger()
    accepted_decisions = accepted_decisions or []
    governed = compilation_governed_input_fingerprint(definition)
    input_fingerprint = decision_ledger_input_fingerprint(governed, ledger)
    snapshot_digest = snapshot_manifest_sha256(definition)
    definition_digest = hashlib.sha256(definition_path.read_bytes()).hexdigest()
    compiler_run = {
        "run_id": "test-run",
        "status": "complete",
        "disclaimer": DISCLAIMER,
        "compilation_input_fingerprint": input_fingerprint,
        "governed_input_fingerprint": governed,
        "snapshot_manifest_sha256": snapshot_digest,
        "area_definition_sha256": definition_digest,
        "decision_contract": ledger.decision_contract,
        "decision_ledger_input": ledger.model_dump(mode="json"),
        "accepted_decisions": accepted_decisions,
    }
    (bundle / "compiler-run.json").write_text(json.dumps(compiler_run), encoding="utf-8")
    (bundle / "layer-manifest.json").write_text(
        json.dumps({"groups": empty_layer_groups()}), encoding="utf-8"
    )
    (bundle / "topography-manifest.json").write_text(
        json.dumps(
            {
                "overview": [],
                "detail": [],
                "overview_feature_count": 0,
                "detail_feature_count": 0,
                "overview_size_bytes": 0,
                "detail_size_bytes": 0,
            }
        ),
        encoding="utf-8",
    )
    (bundle / "topography-profile-evidence.json").write_text(
        json.dumps({"profile_count": 0, "chunks": []}), encoding="utf-8"
    )
    (bundle / "data.js").write_text(
        "window.SATN_DATA = "
        + json.dumps(
            {
                "area_id": "test-geography",
                "area_name": "Test area",
                "title": "Test area deployment",
                "scope": {
                    "area_id": "test-geography",
                    "area_name": "Test area",
                    "audience": "public",
                },
                "evidence_provenance": {
                    "source": {"kind": "fixture", "authority_boundary_queries": []},
                    "snapshot": {"snapshot_id": "current", "manifest_sha256": snapshot_digest},
                    "run": {"run_id": "test-run", "status": "complete"},
                    "agent_runtime": {"response_mode": "caller", "provider": "fake", "model": None},
                },
                "run_id": "test-run",
                "status": "complete",
                "area_definition_sha256": definition_digest,
                "compilation_input_fingerprint": input_fingerprint,
                "network_url": "network.geojson",
                "layer_manifest_url": "layer-manifest.json",
                "topography_manifest_url": "topography-manifest.json",
                "profile_evidence_index_url": "topography-profile-evidence.json",
                "connection_count": 0,
                "gap_count": 0,
                "provenance_lock": {
                    "schema_version": "satn-deployment-provenance-lock/v2",
                    "deployment_id": "test-area",
                    "run_id": "test-run",
                    "status": "complete",
                    "area_definition_sha256": definition_digest,
                    "snapshot_manifest_sha256": snapshot_digest,
                    "compilation_input_fingerprint": input_fingerprint,
                },
                "disclaimer": DISCLAIMER,
                "criteria": {},
                "layer_counts": {},
            }
        )
        + ";\n",
        encoding="utf-8",
    )
    (bundle / "publication.json").write_text(
        json.dumps(
            {
                "deployment_id": "test-area",
                "area_id": "test-geography",
                "area_name": "Test area",
                "title": "Test area deployment",
                "scope": {
                    "area_id": "test-geography",
                    "area_name": "Test area",
                    "audience": "public",
                },
                "evidence_provenance": {
                    "source": {"kind": "fixture", "authority_boundary_queries": []},
                    "snapshot": {
                        "snapshot_id": "current",
                        "manifest_sha256": snapshot_digest,
                    },
                    "run": {"run_id": "test-run", "status": "complete"},
                    "agent_runtime": {
                        "response_mode": "caller",
                        "provider": "fake",
                        "model": None,
                    },
                },
                "run_id": "test-run",
                "status": "complete",
                "area_definition_sha256": definition_digest,
                "compilation_input_fingerprint": input_fingerprint,
                "connection_count": 0,
                "gap_count": 0,
                "compiler_run": "compiler-run.json",
                "layer_manifest": "layer-manifest.json",
                "topography_manifest": "topography-manifest.json",
                "topography_profile_evidence_index": "topography-profile-evidence.json",
                "criteria": {},
                "layer_counts": {},
                "disclaimer": DISCLAIMER,
            }
        ),
        encoding="utf-8",
    )

    def artifact(path: Path) -> dict[str, object]:
        return {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
        }

    lock = {
        "schema_version": "satn-deployment-provenance-lock/v2",
        "deployment_id": "test-area",
        "area_id": "test-geography",
        "area_name": "Test area",
        "title": "Test area deployment",
        "scope": {"area_id": "test-geography", "area_name": "Test area", "audience": "public"},
        "snapshot_id": "current",
        "run_id": "test-run",
        "status": "complete",
        "area_definition_sha256": definition_digest,
        "snapshot_manifest_sha256": snapshot_digest,
        "governed_input_fingerprint": governed,
        "decision_ledger_input_sha256": hashlib.sha256(
            json.dumps(
                ledger.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        "decision_contract_sha256": hashlib.sha256(
            json.dumps(ledger.decision_contract, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "accepted_decisions_sha256": hashlib.sha256(
            json.dumps(accepted_decisions, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "compilation_input_fingerprint": input_fingerprint,
        "connection_count": 0,
        "gap_count": 0,
        "layer_counts": {},
        "criteria": {},
        "cyclic_runtime_files": ["provenance-lock.json", "review-map.zip"],
        "artifacts": {
            item.relative_to(bundle).as_posix(): artifact(item)
            for item in sorted(bundle.rglob("*"))
            if item.is_file() and item.name != "provenance-lock.json"
        },
    }
    # The lock is tracked beside its Area Definition and copied into the bundle.
    (definition_path.parent / "provenance-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    (bundle / "provenance-lock.json").write_text(json.dumps(lock), encoding="utf-8")


def write_release_from_tree(root: Path, release: Path) -> None:
    with zipfile.ZipFile(release, "w") as archive:
        for item in sorted(root.rglob("*")):
            if item.is_file():
                archive.write(item, item.relative_to(root).as_posix())


def test_production_promotion_gate_denies_non_production_runtime(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    write_catalogue(catalogue)
    deployments = tmp_path / "deployments"
    write_bundle(deployments)

    with pytest.raises(ValueError, match="production promotion denied"):
        package_pages(
            catalogue,
            deployments,
            tmp_path / "pages",
            tmp_path / "release.zip",
            promote_production=True,
        )


def _forge_self_consistent_production_governance(bundle: Path) -> None:
    """Forge every mutable release artefact a malicious packager can control."""

    governance = {
        "schema_version": "satn-runtime-governance/v1",
        "status": "production-approved",
        "reason": "approved-immutable-runtime-class-and-ledger-provenance",
        "promotion": {
            "allowed": True,
            "reason": "approved-immutable-runtime-class-and-ledger-provenance",
        },
        "runtime_class_sha256": "a" * 64,
        "decision_ledger_provenance_sha256": "b" * 64,
    }
    compiler_run_path = bundle / "compiler-run.json"
    compiler_run = json.loads(compiler_run_path.read_text(encoding="utf-8"))
    compiler_run["runtime_governance"] = governance
    compiler_run_path.write_text(json.dumps(compiler_run), encoding="utf-8")

    publication_path = bundle / "publication.json"
    publication = json.loads(publication_path.read_text(encoding="utf-8"))
    publication["runtime_governance"] = governance
    publication_path.write_text(json.dumps(publication), encoding="utf-8")

    data_path = bundle / "data.js"
    prefix = "window.SATN_DATA = "
    data = json.loads(
        data_path.read_text(encoding="utf-8").removeprefix(prefix).removesuffix(";\n")
    )
    data["runtime_governance"] = governance
    data_path.write_text(prefix + json.dumps(data) + ";\n", encoding="utf-8")

    lock_path = bundle / "provenance-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["runtime_governance_sha256"] = hashlib.sha256(
        json.dumps(governance, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    lock["artifacts"] = {
        item.relative_to(bundle).as_posix(): {
            "sha256": hashlib.sha256(item.read_bytes()).hexdigest(),
            "size_bytes": item.stat().st_size,
        }
        for item in sorted(bundle.rglob("*"))
        if item.is_file() and item.name != "provenance-lock.json"
    }
    serialized_lock = json.dumps(lock)
    lock_path.write_text(serialized_lock, encoding="utf-8")
    (bundle.parent.parent / "test-area" / "provenance-lock.json").write_text(
        serialized_lock, encoding="utf-8"
    )


def _set_urban_criteria(bundle: Path, urban: dict[str, str]) -> None:
    criteria = {"urban_network": urban}

    compiler_run_path = bundle / "compiler-run.json"
    compiler_run = json.loads(compiler_run_path.read_text(encoding="utf-8"))
    compiler_run["criteria"] = criteria
    compiler_run_path.write_text(json.dumps(compiler_run), encoding="utf-8")

    publication_path = bundle / "publication.json"
    publication = json.loads(publication_path.read_text(encoding="utf-8"))
    publication["criteria"] = criteria
    publication_path.write_text(json.dumps(publication), encoding="utf-8")

    data_path = bundle / "data.js"
    prefix = "window.SATN_DATA = "
    data = json.loads(
        data_path.read_text(encoding="utf-8").removeprefix(prefix).removesuffix(";\n")
    )
    data["criteria"] = criteria
    data_path.write_text(prefix + json.dumps(data) + ";\n", encoding="utf-8")

    lock_path = bundle / "provenance-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["criteria"] = criteria
    lock["artifacts"] = {
        item.relative_to(bundle).as_posix(): {
            "sha256": hashlib.sha256(item.read_bytes()).hexdigest(),
            "size_bytes": item.stat().st_size,
        }
        for item in sorted(bundle.rglob("*"))
        if item.is_file() and item.name != "provenance-lock.json"
    }
    serialized_lock = json.dumps(lock)
    lock_path.write_text(serialized_lock, encoding="utf-8")
    (bundle.parent.parent / "test-area" / "provenance-lock.json").write_text(
        serialized_lock, encoding="utf-8"
    )


def test_required_urban_evidence_blocks_only_canonical_production_release(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    write_catalogue(catalogue)
    deployments = tmp_path / "deployments"
    write_bundle(deployments)
    bundle = deployments / "test-area"
    _forge_self_consistent_production_governance(bundle)
    _set_urban_criteria(
        bundle,
        {
            "official_road_classification": "grey",
            "official_main_road_spines": "grey",
            "urban_a_road_evidence_coverage": "red",
        },
    )
    blocking = (
        "production promotion denied: incomplete required urban evidence: "
        "official_main_road_spines=grey, urban_a_road_evidence_coverage=red"
    )

    with pytest.raises(ValueError, match=blocking):
        package_pages(
            catalogue,
            deployments,
            tmp_path / "production-pages",
            tmp_path / "production-release.zip",
            promote_production=True,
        )

    package_pages(
        catalogue,
        deployments,
        tmp_path / "review-pages",
        tmp_path / "review-release.zip",
    )
    with pytest.raises(ValueError, match=blocking):
        validate_pages_release(
            tmp_path / "review-release.zip",
            tmp_path / "rejected-production-pages",
            catalogue,
        )
    validate_pages_release(
        tmp_path / "review-release.zip",
        tmp_path / "validated-review-pages",
        catalogue,
        allow_non_production=True,
    )


def test_production_package_rejects_forged_self_consistent_runtime_and_lock(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    write_catalogue(catalogue)
    deployments = tmp_path / "deployments"
    write_bundle(deployments)
    _forge_self_consistent_production_governance(deployments / "test-area")

    with pytest.raises(ValueError, match="production promotion denied"):
        package_pages(
            catalogue,
            deployments,
            tmp_path / "pages",
            tmp_path / "release.zip",
            promote_production=True,
        )


def test_production_release_validator_cannot_be_bypassed_by_local_packaging(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    write_catalogue(catalogue)
    deployments = tmp_path / "deployments"
    write_bundle(deployments)
    # Local packaging intentionally does not require production approval: it is
    # useful for reviewable/fake deployments.  Forge the entire package and
    # both mutable locks to prove that the *deployed* validator is still the
    # independent deny-by-default production gate.
    _forge_self_consistent_production_governance(deployments / "test-area")
    package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")

    with pytest.raises(ValueError, match="production promotion denied"):
        validate_pages_release(
            tmp_path / "release.zip",
            tmp_path / "validated-pages",
            catalogue,
        )


def test_published_pages_workflow_requires_the_independent_production_gate() -> None:
    """Canonical Pages publication never bypasses the production gate."""

    workflow = yaml.load(
        (PROJECT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    triggers = workflow["on"]
    assert triggers["release"]["types"] == ["released"]
    preview_input = triggers["workflow_dispatch"]["inputs"]["allow_non_production"]
    assert preview_input["required"] == "false"
    assert preview_input["default"] == "false"

    validation_step = next(
        step
        for step in workflow["jobs"]["validate-release"]["steps"]
        if step.get("name") == "Safely extract and validate the Pages release"
    )
    assert validation_step["env"]["ALLOW_NON_PRODUCTION"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.allow_non_production }}"
    )
    validation_script = validation_step["run"]
    assert 'if [[ "$ALLOW_NON_PRODUCTION" == "true" ]]; then' in validation_script
    assert "validator_args+=(--allow-non-production)" in validation_script
    validation_command = 'python scripts/validate_pages_release.py "${validator_args[@]}"'
    assert validation_command in validation_script


def write_layer_shard(
    bundle: Path, feature_type: str, coordinates: tuple[int, int]
) -> tuple[dict[str, object], bytes]:
    features = [
        {
            "type": "Feature",
            "id": f"test-{feature_type}",
            "properties": {"feature_type": feature_type},
            "geometry": {"type": "Point", "coordinates": list(coordinates)},
        }
    ]
    encoded = json.dumps(
        {"type": "FeatureCollection", "features": features}, separators=(",", ":")
    ).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    shard = bundle / "layers" / f"amenities-{feature_type}-{digest[:16]}.geojson"
    shard.parent.mkdir(exist_ok=True)
    shard.write_bytes(encoded)
    entry = {
        "path": f"layers/{shard.name}",
        "sha256": digest,
        "size_bytes": len(encoded),
        "feature_count": 1,
        "bbox": [
            float(coordinates[0]),
            float(coordinates[1]),
            float(coordinates[0]),
            float(coordinates[1]),
        ],
    }
    return entry, encoded


def add_layer_shard(bundle: Path) -> Path:
    """Add one declared shard for general shard-integrity tests."""
    entry, encoded = write_layer_shard(bundle, "retail-centre", (1, 2))
    groups = empty_layer_groups()
    amenities = groups["amenities"]
    amenities["feature_count"] = 1
    amenities["size_bytes"] = len(encoded)
    amenities["shards"] = [entry]
    types = amenities["types"]
    assert isinstance(types, dict)
    retail = types["retail-centre"]
    assert isinstance(retail, dict)
    retail["feature_count"] = 1
    retail["size_bytes"] = len(encoded)
    retail["shards"] = [entry]
    (bundle / "layer-manifest.json").write_text(
        json.dumps({"groups": groups}), encoding="utf-8"
    )
    return bundle / str(entry["path"])


def add_layer_shards(bundle: Path) -> None:
    healthcare, healthcare_bytes = write_layer_shard(bundle, "healthcare", (1, 2))
    retail, retail_bytes = write_layer_shard(bundle, "retail-centre", (3, 4))
    groups = empty_layer_groups()
    amenities = groups["amenities"]
    amenities["feature_count"] = 2
    amenities["size_bytes"] = len(healthcare_bytes) + len(retail_bytes)
    amenities["shards"] = [healthcare, retail]
    types = amenities["types"]
    assert isinstance(types, dict)
    for feature_type, entry, encoded in (
        ("healthcare", healthcare, healthcare_bytes),
        ("retail-centre", retail, retail_bytes),
    ):
        metadata = types[feature_type]
        assert isinstance(metadata, dict)
        metadata["feature_count"] = 1
        metadata["size_bytes"] = len(encoded)
        metadata["shards"] = [entry]
    (bundle / "layer-manifest.json").write_text(
        json.dumps({"groups": groups}),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("tamper", "match"),
    [
        (
            lambda manifest: manifest["groups"].pop("urban"),
            "groups must exactly match canonical deferred groups",
        ),
        (
            lambda manifest: manifest["groups"].__setitem__(
                "shops", manifest["groups"].pop("amenities")
            ),
            "groups must exactly match canonical deferred groups",
        ),
        (
            lambda manifest: (
                manifest["groups"]["amenities"]["types"].__setitem__(
                    "local-retail", manifest["groups"]["amenities"]["types"].pop("retail-centre")
                ),
                manifest["groups"]["amenities"].__setitem__(
                    "feature_types", ["healthcare", "local-retail"]
                ),
            ),
            "types must exactly match canonical feature types",
        ),
        (
            lambda manifest: manifest["groups"]["amenities"]["shards"].reverse(),
            "typed shards must exactly match group shards in order",
        ),
        (
            lambda manifest: manifest["groups"]["amenities"]["types"]["retail-centre"].__setitem__(
                "feature_count", 2
            ),
            "type retail-centre aggregate counts do not match shards",
        ),
        (
            lambda manifest: manifest["groups"]["amenities"]["types"]["retail-centre"].__setitem__(
                "size_bytes", 1
            ),
            "type retail-centre aggregate counts do not match shards",
        ),
        (
            lambda manifest: (
                manifest["groups"]["amenities"].__setitem__("feature_count", 3),
                manifest["groups"]["amenities"].__setitem__("size_bytes", 1),
            ),
            "layer group amenities aggregate counts do not match shards",
        ),
    ],
)
def test_progressive_manifest_tampering_is_rejected_by_packager_and_isolated_validator(
    tmp_path: Path, tamper: object, match: str
) -> None:
    """The typed metadata is a signed loading boundary, not UI-only decoration."""
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    write_catalogue(catalogue)
    write_bundle(deployments)
    add_layer_shards(deployments / "test-area")
    manifest_path = deployments / "test-area" / "layer-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert callable(tamper)
    tamper(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")

    # The isolated validator must make the identical decision for a forged archive.
    # Copy a valid release tree, then add a typed layer manifest solely to forge
    # it. The independent check runs before the unrelated provenance lock detects
    # that the release was altered.
    shutil.rmtree(deployments / "test-area" / "layers", ignore_errors=True)
    write_bundle(deployments)
    package_pages(catalogue, deployments, tmp_path / "valid-pages", tmp_path / "valid.zip")
    package_root = tmp_path / "forged-pages"
    shutil.copytree(tmp_path / "valid-pages", package_root)
    forged_manifest = package_root / "deployments" / "test-area" / "layer-manifest.json"
    add_layer_shards(package_root / "deployments" / "test-area")
    forged = json.loads(forged_manifest.read_text(encoding="utf-8"))
    tamper(forged)
    forged_manifest.write_text(json.dumps(forged), encoding="utf-8")
    release = tmp_path / "forged.zip"
    write_release_from_tree(package_root, release)
    with pytest.raises(ValueError, match=match):
        validate_pages_release(
            release,
            tmp_path / "validated-pages",
            catalogue,
            allow_non_production=True,
        )


def test_real_fixture_bootstrap_lock_rebuild_package_and_isolated_validation(
    tmp_path: Path,
) -> None:
    """Exercise the release path with a compiler-produced core-subset deployment."""
    fixture = tmp_path / "fixture"
    shutil.copytree(PROJECT / "examples" / "fixture", fixture)
    council_path = fixture / "council.yaml"
    council_path.write_text(
        council_path.read_text(encoding="utf-8")
        .replace("council_id: tiny-council", "area_id: tiny-council")
        .replace("council_name: Tiny Council", "area_name: Tiny Council")
        + "\ndeployment_id: tiny-council\n",
        encoding="utf-8",
    )
    definition = AreaDefinition.from_yaml(fixture / "council.yaml")
    snapshot(definition)
    compile(definition)

    bundles = tmp_path / "bundles"
    deployment = bundles / definition.deployment_slug
    build_area_deployment(definition, deployment, bootstrap=True)
    generate_lock(definition, deployment=deployment)
    build_area_deployment(definition, deployment)

    catalogue = fixture / "catalogue.yaml"
    catalogue.write_text(
        f"""schema_version: satn-deployment-catalogue/v1
title: Fixture deployments
deployments:
  - deployment_id: {definition.deployment_slug}
    area_id: {definition.area_id}
    area_name: {definition.area_name}
    area_definition: council.yaml
    deployment_path: deployments/{definition.deployment_slug}/
    artifacts:
      review_map: index.html
      network_map_pdf: network-map.pdf
      review_map_zip: review-map.zip
""",
        encoding="utf-8",
    )
    generate_catalogue_lock(catalogue)
    isolated = tmp_path / "isolated-checkout"
    isolated_fixture = isolated / "fixture"
    isolated_bundles = isolated / "bundles"
    shutil.copytree(fixture, isolated_fixture)
    shutil.copytree(bundles, isolated_bundles)
    isolated_catalogue = isolated_fixture / "catalogue.yaml"
    release = isolated / "satn-pages.zip"
    package_pages(
        isolated_catalogue,
        isolated_bundles,
        isolated / "pages",
        release,
    )
    validated = validate_pages_release(
        release,
        isolated / "validated",
        isolated_catalogue,
        allow_non_production=True,
    )
    assert (
        validated.pages_directory / f"deployments/{definition.deployment_slug}/review-map.zip"
    ).is_file()


def test_package_pages_generates_stable_links_deployment_zip_and_release_archive(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    destination = tmp_path / "pages"
    release = tmp_path / "satn-pages.zip"
    write_catalogue(catalogue)
    write_bundle(deployments)

    result = package_pages(catalogue, deployments, destination, release, maximum_bytes=1_000_000)

    assert result.pages_directory == destination.resolve()
    assert result.release_artifact == release.resolve()
    assert result.pages_size_bytes < 1_000_000
    publication = json.loads((destination / "catalogue.json").read_text(encoding="utf-8"))
    assert publication["deployments"][0]["artifacts"] == {
        "review_map": "deployments/test-area/index.html",
        "network_map_pdf": "deployments/test-area/network-map.pdf",
        "review_map_zip": "deployments/test-area/review-map.zip",
    }
    assert publication["deployments"][0]["area_id"] == "test-geography"
    assert publication["deployments"][0]["title"] == "Test area deployment"
    assert publication["deployments"][0]["scope"] == {
        "area_id": "test-geography",
        "area_name": "Test area",
        "audience": "public",
    }
    assert publication["deployments"][0]["evidence_provenance"] == {
        "source": {"kind": "fixture", "authority_boundary_queries": []},
        "snapshot": {"snapshot_id": "current"},
        "agent_runtime": {"response_mode": "caller", "provider": "fake", "model": None},
    }
    area_definition_sha256 = hashlib.sha256(
        (catalogue.parent / "test-area" / "area.yaml").read_bytes()
    ).hexdigest()
    assert publication["deployments"][0]["area_definition_sha256"] == area_definition_sha256
    assert (
        json.loads((destination / "deployments" / "test-area" / "publication.json").read_text())[
            "area_definition_sha256"
        ]
        == area_definition_sha256
    )
    assert (destination / "deployments" / "test-area" / "network-map.pdf").exists()
    with zipfile.ZipFile(destination / "deployments" / "test-area" / "review-map.zip") as archive:
        assert set(archive.namelist()) == {
            "review-map/assets/map.js",
            "review-map/compiler-run.json",
            "review-map/data.js",
            "review-map/index.html",
            "review-map/layer-manifest.json",
            "review-map/network-map.pdf",
            "review-map/network.geojson",
            "review-map/publication.json",
            "review-map/provenance-lock.json",
            "review-map/topography-manifest.json",
            "review-map/topography-profile-evidence.json",
        }
    with zipfile.ZipFile(release) as archive:
        assert "index.html" in archive.namelist()
        assert "catalogue.json" in archive.namelist()
        assert "deployments/test-area/review-map.zip" in archive.namelist()


@pytest.mark.parametrize(
    ("relative_path", "coordinates"),
    (
        ("network.geojson", [0.0, 91.0]),
        ("network.geojson", [181.0, 0.0]),
        ("map-artifacts/invalid.geojson", [0.0, float("inf")]),
        ("map-artifacts/invalid.geojson", [float("nan"), 0.0]),
    ),
)
def test_package_pages_rejects_non_wgs84_map_artifact_coordinates(
    tmp_path: Path,
    relative_path: str,
    coordinates: list[float],
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    write_catalogue(catalogue)
    write_bundle(deployments)
    artifact = deployments / "test-area" / relative_path
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "invalid-coordinate",
                        "properties": {},
                        "geometry": {"type": "Point", "coordinates": coordinates},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    lock_path = deployments / "test-area" / "provenance-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["artifacts"][relative_path] = {
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "size_bytes": artifact.stat().st_size,
    }
    lock_text = json.dumps(lock)
    lock_path.write_text(lock_text, encoding="utf-8")
    (tmp_path / "test-area" / "provenance-lock.json").write_text(
        lock_text,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="finite WGS84 longitude/latitude"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")


def test_validate_pages_release_independently_checks_extracted_content(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    release = tmp_path / "satn-pages.zip"
    write_catalogue(catalogue)
    write_bundle(deployments)
    package_pages(catalogue, deployments, tmp_path / "packaged-pages", release)

    result = validate_pages_release(
        release, tmp_path / "validated-pages", catalogue, allow_non_production=True
    )

    assert result.pages_size_bytes < 900_000_000
    assert (result.pages_directory / "catalogue.json").is_file()
    assert (result.pages_directory / "deployments" / "test-area" / "publication.json").is_file()


def test_validate_pages_release_rejects_mismatched_deployment_content(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    packaged_pages = tmp_path / "packaged-pages"
    release = tmp_path / "satn-pages.zip"
    write_catalogue(catalogue)
    write_bundle(deployments)
    package_pages(catalogue, deployments, packaged_pages, tmp_path / "original-release.zip")
    (packaged_pages / "deployments" / "test-area" / "publication.json").write_text(
        json.dumps({"deployment_id": "wrong", "area_id": "test-geography"}),
        encoding="utf-8",
    )
    write_release_from_tree(packaged_pages, release)

    with pytest.raises(ValueError, match="publication identity does not match"):
        validate_pages_release(release, tmp_path / "validated-pages", catalogue)


def test_validate_pages_release_rejects_mismatched_publication_area_id(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    packaged_pages = tmp_path / "packaged-pages"
    release = tmp_path / "satn-pages.zip"
    write_catalogue(catalogue)
    write_bundle(deployments)
    package_pages(catalogue, deployments, packaged_pages, tmp_path / "original-release.zip")
    (packaged_pages / "deployments" / "test-area" / "publication.json").write_text(
        json.dumps({"deployment_id": "test-area", "area_id": "other-area"}),
        encoding="utf-8",
    )
    write_release_from_tree(packaged_pages, release)

    with pytest.raises(ValueError, match="publication identity does not match"):
        validate_pages_release(release, tmp_path / "validated-pages", catalogue)


def test_validate_pages_release_binds_archive_catalogue_to_tracked_identities(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    packaged_pages = tmp_path / "packaged-pages"
    release = tmp_path / "satn-pages.zip"
    write_catalogue(catalogue)
    write_bundle(deployments)
    package_pages(catalogue, deployments, packaged_pages, tmp_path / "original-release.zip")
    archived_catalogue = json.loads((packaged_pages / "catalogue.json").read_text())
    archived_catalogue["deployments"][0]["area_name"] = "Untracked area name"
    (packaged_pages / "catalogue.json").write_text(json.dumps(archived_catalogue), encoding="utf-8")
    write_release_from_tree(packaged_pages, release)

    with pytest.raises(ValueError, match="does not exactly match"):
        validate_pages_release(release, tmp_path / "validated-pages", catalogue)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("root-index", "root file does not match"),
        ("extra-root", "undeclared or missing files"),
        ("rogue-deployment", "undeclared or missing files"),
        ("missing-file", "artifacts are invalid"),
    ],
)
def test_validate_pages_release_requires_the_exact_tag_locked_global_file_set(
    tmp_path: Path, change: str, message: str
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    pages = tmp_path / "pages"
    release = tmp_path / "satn-pages.zip"
    write_catalogue(catalogue)
    write_bundle(deployments)
    package_pages(catalogue, deployments, pages, tmp_path / "original-release.zip")
    if change == "root-index":
        (pages / "index.html").write_text("forged root", encoding="utf-8")
    elif change == "extra-root":
        (pages / "unlocked-root.html").write_text("forged root", encoding="utf-8")
    elif change == "rogue-deployment":
        rogue = pages / "deployments" / "rogue"
        rogue.mkdir()
        (rogue / "index.html").write_text("forged deployment", encoding="utf-8")
    else:
        (pages / "deployments" / "test-area" / "network.geojson").unlink()
    write_release_from_tree(pages, release)

    with pytest.raises(ValueError, match=message):
        validate_pages_release(
            release,
            tmp_path / "validated-pages",
            catalogue,
            allow_non_production=True,
        )


def test_review_map_zip_rejects_high_compression_members_before_decompression(
    tmp_path: Path,
) -> None:
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    payload = b"0" * 200_000
    (deployment / "index.html").write_bytes(payload)
    with zipfile.ZipFile(
        deployment / "review-map.zip", "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr("review-map/index.html", payload)

    with pytest.raises(ValueError, match="high-compression"):
        validate_packaged_review_map_zip(deployment)
    with pytest.raises(ValueError, match="high-compression"):
        _VALIDATOR._validate_review_map_zip(deployment)


def test_validate_pages_release_runs_in_an_isolated_stdlib_subprocess(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    release = tmp_path / "satn-pages.zip"
    write_catalogue(catalogue)
    write_bundle(deployments)
    package_pages(catalogue, deployments, tmp_path / "packaged-pages", release)

    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(PROJECT / "scripts" / "validate_pages_release.py"),
            str(release),
            str(tmp_path / "validated-pages"),
            "--catalogue",
            str(catalogue),
            "--allow-non-production",
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "validated-pages" / "catalogue.json").is_file()


def test_validate_pages_release_rejects_stale_area_definition_in_isolated_subprocess(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    release = tmp_path / "satn-pages.zip"
    write_catalogue(catalogue)
    write_bundle(deployments)
    package_pages(catalogue, deployments, tmp_path / "packaged-pages", release)
    definition = tmp_path / "test-area" / "area.yaml"
    definition.write_text(
        definition.read_text(encoding="utf-8").replace(
            "title: Test area deployment", "title: Changed area deployment title"
        ),
        encoding="utf-8",
    )
    archived_catalogue_path = tmp_path / "packaged-pages" / "catalogue.json"
    archived_catalogue = json.loads(archived_catalogue_path.read_text(encoding="utf-8"))
    archived_catalogue["deployments"][0]["area_definition_sha256"] = hashlib.sha256(
        definition.read_bytes()
    ).hexdigest()
    archived_catalogue_path.write_text(json.dumps(archived_catalogue), encoding="utf-8")
    write_release_from_tree(tmp_path / "packaged-pages", release)

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(PROJECT / "scripts" / "validate_pages_release.py"),
            str(release),
            str(tmp_path / "validated-pages"),
            "--catalogue",
            str(catalogue),
            "--allow-non-production",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "does not exactly match" in completed.stderr


def test_package_pages_rejects_missing_or_mismatched_deployment_publication(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    write_catalogue(catalogue)
    write_bundle(deployments)
    publication = deployments / "test-area" / "publication.json"
    publication.unlink()

    with pytest.raises(ValueError, match=r"missing publication\.json"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")

    publication.write_text(
        json.dumps({"deployment_id": "wrong", "area_id": "test-geography"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="identity does not match catalogue deployment_id"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")


def test_package_pages_rejects_unbound_publication_scope_and_provenance(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    write_catalogue(catalogue)
    write_bundle(deployments)
    publication_path = deployments / "test-area" / "publication.json"
    publication = json.loads(publication_path.read_text(encoding="utf-8"))
    publication["scope"]["audience"] = "local"
    publication_path.write_text(json.dumps(publication), encoding="utf-8")

    with pytest.raises(ValueError, match="scope does not match"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")

    publication["scope"]["audience"] = "public"
    publication["evidence_provenance"]["source"]["authority_boundary_queries"] = ["invented"]
    publication_path.write_text(json.dumps(publication), encoding="utf-8")
    with pytest.raises(ValueError, match="evidence_provenance does not match"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")


def test_package_pages_rejects_stale_area_definition_with_stable_identity(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    write_catalogue(catalogue)
    write_bundle(deployments)
    definition = tmp_path / "test-area" / "area.yaml"
    definition.write_text(
        definition.read_text(encoding="utf-8").replace(
            "title: Test area deployment", "title: Changed area deployment title"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="area_definition_sha256 does not match"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")


def test_package_pages_rejects_stale_snapshot_and_tampered_dynamic_provenance(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    write_catalogue(catalogue)
    write_bundle(deployments)
    snapshot = tmp_path / "snapshots" / "current" / "snapshot.json"
    snapshot.write_text('{"snapshot_id":"changed"}', encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot digest does not match"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")


def test_package_pages_binds_input_ledger_separately_from_runtime_decisions(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    write_catalogue(catalogue)
    # Direct runtime has no caller replay input but does produce an audit output.
    accepted = [
        {
            "request_id": "runtime-choice",
            "dependency_fingerprint": "a" * 64,
            "choice_id": "1",
        }
    ]
    write_bundle(deployments, accepted_decisions=accepted)
    package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")

    write_bundle(deployments, accepted_decisions=accepted)
    run_path = deployments / "test-area" / "compiler-run.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["decision_ledger_input"]["responses"] = accepted
    run_path.write_text(json.dumps(run), encoding="utf-8")
    with pytest.raises(ValueError, match="decision_ledger_input"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")

    write_bundle(deployments, accepted_decisions=accepted)
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["accepted_decisions"][0]["choice_id"] = "2"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    with pytest.raises(ValueError, match="accepted_decisions"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")

    write_bundle(deployments)
    publication_path = deployments / "test-area" / "publication.json"
    publication = json.loads(publication_path.read_text(encoding="utf-8"))
    publication["compilation_input_fingerprint"] = "0" * 64
    publication_path.write_text(json.dumps(publication), encoding="utf-8")
    with pytest.raises(ValueError, match="compiler run compilation_input_fingerprint"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")


def test_package_pages_rejects_reordered_runtime_decision_audits(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    write_catalogue(catalogue)
    accepted = [
        {"request_id": "a", "dependency_fingerprint": "a" * 64, "choice_id": "1"},
        {"request_id": "b", "dependency_fingerprint": "b" * 64, "choice_id": "1"},
    ]
    write_bundle(deployments, accepted_decisions=accepted)
    run_path = deployments / "test-area" / "compiler-run.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["accepted_decisions"].reverse()
    run_path.write_text(json.dumps(run), encoding="utf-8")

    with pytest.raises(ValueError, match="decision provenance"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")


def test_isolated_validator_rejects_non_ascii_numeric_decision_choice() -> None:
    with pytest.raises(ValueError, match="choice_id is invalid"):
        _VALIDATOR._canonical_decision_ledger(
            {
                "decision_contract": "agent-decision-menu/v1",
                "responses": [
                    {
                        "request_id": "request-a",
                        "dependency_fingerprint": "a" * 64,
                        "choice_id": "\u0661",
                    }
                ],
            },
            "compiler run accepted_decisions",
        )


def test_package_pages_rejects_tampered_disclaimer_and_content_addressed_shard(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    write_catalogue(catalogue)
    write_bundle(deployments)
    publication_path = deployments / "test-area" / "publication.json"
    publication = json.loads(publication_path.read_text(encoding="utf-8"))
    publication["disclaimer"] = "tampered"
    publication_path.write_text(json.dumps(publication), encoding="utf-8")
    with pytest.raises(ValueError, match="disclaimer"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")

    write_bundle(deployments)
    shard = add_layer_shard(deployments / "test-area")
    shard.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="content hash does not match"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")


def test_isolated_release_validator_rejects_tampered_run_and_shard(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    packaged_pages = tmp_path / "packaged-pages"
    release = tmp_path / "satn-pages.zip"
    write_catalogue(catalogue)
    write_bundle(deployments)
    package_pages(catalogue, deployments, packaged_pages, tmp_path / "original-release.zip")
    compiler_run = packaged_pages / "deployments" / "test-area" / "compiler-run.json"
    run = json.loads(compiler_run.read_text(encoding="utf-8"))
    run["run_id"] = "tampered-run"
    compiler_run.write_text(json.dumps(run), encoding="utf-8")
    write_release_from_tree(packaged_pages, release)
    with pytest.raises(ValueError, match="compiler run run_id"):
        validate_pages_release(
            release,
            tmp_path / "validated-pages",
            catalogue,
            allow_non_production=True,
        )

    shutil.rmtree(tmp_path / "validated-pages", ignore_errors=True)
    package_pages(catalogue, deployments, packaged_pages, tmp_path / "original-release.zip")
    shard = add_layer_shard(packaged_pages / "deployments" / "test-area")
    shard.write_bytes(b"tampered")
    write_release_from_tree(packaged_pages, release)
    with pytest.raises(ValueError, match="content hash does not match"):
        validate_pages_release(
            release,
            tmp_path / "validated-pages",
            catalogue,
            allow_non_production=True,
        )


def test_package_pages_rejects_file_and_directory_symlinks_before_copying(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    write_catalogue(catalogue)
    write_bundle(deployments)
    bundle = deployments / "test-area"
    (bundle / "file-link").symlink_to(bundle / "index.html")

    with pytest.raises(ValueError, match="must not contain symlinks"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")

    (bundle / "file-link").unlink()
    (bundle / "directory-link").symlink_to(bundle / "assets", target_is_directory=True)
    with pytest.raises(ValueError, match="must not contain symlinks"):
        package_pages(catalogue, deployments, tmp_path / "pages", tmp_path / "release.zip")


def test_package_pages_rejects_symlinked_input_roots_before_resolving(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    write_catalogue(catalogue)
    write_bundle(deployments)
    deployments_link = tmp_path / "deployments-link"
    deployments_link.symlink_to(deployments, target_is_directory=True)

    with pytest.raises(ValueError, match="deployments_root must not be a symlink"):
        package_pages(catalogue, deployments_link, tmp_path / "pages", tmp_path / "release.zip")

    release_target = tmp_path / "release-target.zip"
    release_target.write_bytes(b"keep")
    release_link = tmp_path / "release-link.zip"
    release_link.symlink_to(release_target)
    with pytest.raises(ValueError, match="release_artifact must not be a symlink"):
        package_pages(catalogue, deployments, tmp_path / "pages", release_link)
    assert release_target.read_bytes() == b"keep"


def test_validate_pages_release_rejects_traversal_symlinks_and_oversized_payload(
    tmp_path: Path,
) -> None:
    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../outside", "no")
    with pytest.raises(ValueError, match="unsafe path"):
        validate_pages_release(traversal, tmp_path / "traversal-pages", tmp_path / "catalogue.yaml")

    symlink = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(symlink, "w") as archive:
        archive.writestr(info, "target")
    with pytest.raises(ValueError, match="contains symlink"):
        validate_pages_release(symlink, tmp_path / "symlink-pages", tmp_path / "catalogue.yaml")

    oversized = tmp_path / "oversized.zip"
    with zipfile.ZipFile(oversized, "w") as archive:
        archive.writestr("large", "xx")
    with pytest.raises(ValueError, match="extracted size exceeds"):
        validate_pages_release(
            oversized,
            tmp_path / "oversized-pages",
            tmp_path / "catalogue.yaml",
            maximum_bytes=1,
        )


def test_validate_pages_release_rejects_symlinked_archive_before_resolving(tmp_path: Path) -> None:
    release = tmp_path / "release.zip"
    with zipfile.ZipFile(release, "w") as archive:
        archive.writestr("index.html", "test")
    link = tmp_path / "release-link.zip"
    link.symlink_to(release)

    with pytest.raises(ValueError, match="release archive must not be a symlink"):
        validate_pages_release(link, tmp_path / "validated-pages", tmp_path / "catalogue.yaml")


def test_validate_pages_release_rejects_symlinked_destination_before_resolving(
    tmp_path: Path,
) -> None:
    destination_target = tmp_path / "destination-target"
    destination = tmp_path / "validated-pages"
    destination.symlink_to(destination_target, target_is_directory=True)

    with pytest.raises(ValueError, match="destination must not be a symlink"):
        validate_pages_release(tmp_path / "missing.zip", destination, tmp_path / "catalogue.yaml")


def test_package_pages_rejects_symlinked_destination_before_removal(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    destination_target = tmp_path / "destination-target"
    destination = tmp_path / "pages"
    write_catalogue(catalogue)
    write_bundle(deployments)
    destination.symlink_to(destination_target, target_is_directory=True)

    with pytest.raises(ValueError, match="Pages destination must not be a symlink"):
        package_pages(catalogue, deployments, destination, tmp_path / "release.zip")


def test_package_pages_rejects_budget_at_or_above_pages_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="below the GitHub Pages"):
        package_pages(
            tmp_path / "catalogue.yaml",
            tmp_path / "deployments",
            tmp_path / "pages",
            tmp_path / "satn-pages.zip",
            maximum_bytes=GITHUB_PAGES_LIMIT_BYTES,
        )


def test_package_pages_fails_before_publishing_when_the_budget_is_exceeded(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    deployments = tmp_path / "deployments"
    write_catalogue(catalogue)
    write_bundle(deployments)

    with pytest.raises(ValueError, match="exceeding configured budget"):
        package_pages(
            catalogue,
            deployments,
            tmp_path / "pages",
            tmp_path / "satn-pages.zip",
            maximum_bytes=1,
        )
