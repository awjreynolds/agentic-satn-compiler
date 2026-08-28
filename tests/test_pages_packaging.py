from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

import pytest

from satn.constants import DISCLAIMER
from satn.deployment import DEFERRED_GROUPS, build_area_deployment
from satn.filesystem_safety import publication_destination_authority
from satn.models import AgentDecisionLedger, AreaDefinition
from satn.pages_packaging import (
    GITHUB_PAGES_LIMIT_BYTES,
    package_pages,
)
from satn.pipeline import (
    compilation_governed_input_fingerprint,
    compile,
    decision_ledger_input_fingerprint,
    snapshot_manifest_sha256,
)
from satn.sources import snapshot

PROJECT = Path(__file__).parents[1]


def empty_layer_groups() -> dict[str, dict[str, object]]:
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


def write_bundle(root: Path) -> None:
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
    governed = compilation_governed_input_fingerprint(definition)
    input_fingerprint = decision_ledger_input_fingerprint(governed, ledger)
    snapshot_digest = snapshot_manifest_sha256(definition)
    definition_digest = hashlib.sha256(definition_path.read_bytes()).hexdigest()
    evidence_provenance = {
        "source": {"kind": "fixture", "authority_boundary_queries": []},
        "snapshot": {"snapshot_id": "current", "manifest_sha256": snapshot_digest},
        "run": {"run_id": "test-run", "status": "complete"},
        "agent_runtime": {"response_mode": "caller", "provider": "fake", "model": None},
    }
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
        "accepted_decisions": [],
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
    common = {
        "area_id": "test-geography",
        "area_name": "Test area",
        "title": "Test area deployment",
        "scope": {
            "area_id": "test-geography",
            "area_name": "Test area",
            "audience": "public",
        },
        "evidence_provenance": evidence_provenance,
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
        "disclaimer": DISCLAIMER,
        "criteria": {},
        "layer_counts": {},
    }
    (bundle / "data.js").write_text(
        "window.SATN_DATA = " + json.dumps(common) + ";\n", encoding="utf-8"
    )
    (bundle / "publication.json").write_text(
        json.dumps(
            {
                **{
                    key: common[key]
                    for key in common
                    if key
                    not in {
                        "network_url",
                        "layer_manifest_url",
                        "topography_manifest_url",
                        "profile_evidence_index_url",
                    }
                },
                "deployment_id": "test-area",
                "compiler_run": "compiler-run.json",
                "layer_manifest": "layer-manifest.json",
                "topography_manifest": "topography-manifest.json",
                "topography_profile_evidence_index": "topography-profile-evidence.json",
            }
        ),
        encoding="utf-8",
    )


def package_fixture(tmp_path: Path, *, maximum_bytes: int = 1_000_000):
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    write_catalogue(catalogue)
    write_bundle(bundles)
    return package_pages(
        catalogue,
        bundles,
        tmp_path / "pages",
        tmp_path / "satn-pages.zip",
        maximum_bytes=maximum_bytes,
    )


def test_package_pages_copies_catalogue_deployments_and_writes_release_archive(
    tmp_path: Path,
) -> None:
    result = package_fixture(tmp_path)

    assert result.pages_directory.is_dir()
    assert result.release_artifact.is_file()
    assert result.pages_size_bytes < 1_000_000
    assert (result.pages_directory / "index.html").is_file()
    deployment = result.pages_directory / "deployments" / "test-area"
    assert (deployment / "publication.json").is_file()
    assert (deployment / "network-map.pdf").is_file()
    assert not any(
        path.name in {"provenance-lock.json", "catalogue-lock.json"}
        for path in result.pages_directory.rglob("*")
    )
    with zipfile.ZipFile(result.release_artifact) as archive:
        names = set(archive.namelist())
    assert "catalogue.json" in names
    assert "deployments/test-area/publication.json" in names


def test_package_pages_keeps_one_canonical_reviewable_projection(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    write_catalogue(catalogue)
    write_bundle(bundles)
    bundle = bundles / "test-area"
    data_path = bundle / "data.js"
    data = json.loads(
        data_path.read_text(encoding="utf-8")
        .removeprefix("window.SATN_DATA = ")
        .removesuffix(";\n")
    )
    reviewable = {"type": "FeatureCollection", "features": []}
    data["reviewable"] = reviewable
    data["reviewable_network"] = reviewable
    data_path.write_text(
        "window.SATN_DATA = " + json.dumps(data) + ";\n",
        encoding="utf-8",
    )
    (bundle / "strategic-network.json").write_text("{}", encoding="utf-8")

    result = package_pages(
        catalogue,
        bundles,
        tmp_path / "pages",
        tmp_path / "satn-pages.zip",
    )

    packaged = result.pages_directory / "deployments" / "test-area"
    packaged_data = json.loads(
        (packaged / "data.js")
        .read_text(encoding="utf-8")
        .removeprefix("window.SATN_DATA = ")
        .removesuffix(";\n")
    )
    assert packaged_data["reviewable_network"] == reviewable
    assert "reviewable" not in packaged_data
    assert not (packaged / "strategic-network.json").exists()


def test_package_pages_rejects_budget_at_or_above_github_pages_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="1 GB limit"):
        package_fixture(tmp_path, maximum_bytes=GITHUB_PAGES_LIMIT_BYTES)


def test_package_pages_fails_before_publishing_when_budget_is_exceeded(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exceeding configured budget"):
        package_fixture(tmp_path, maximum_bytes=1)
    assert not (tmp_path / "pages").exists()
    assert not (tmp_path / "satn-pages.zip").exists()


def test_package_pages_does_not_replay_compiler_provenance(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    bundles = tmp_path / "bundles"
    write_catalogue(catalogue)
    write_bundle(bundles)
    publication_path = bundles / "test-area" / "publication.json"
    publication = json.loads(publication_path.read_text(encoding="utf-8"))
    publication["area_definition_sha256"] = "stale-area-definition"
    publication["compilation_input_fingerprint"] = "stale-compilation-input"
    publication["evidence_provenance"] = {"snapshot": {"manifest_sha256": "stale"}}
    publication_path.write_text(json.dumps(publication), encoding="utf-8")

    result = package_pages(
        catalogue,
        bundles,
        tmp_path / "pages",
        tmp_path / "satn-pages.zip",
    )

    assert result.release_artifact.is_file()


def test_pages_workflow_extracts_release_and_runs_browser_gate_before_upload() -> None:
    workflow = (PROJECT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    assert "gh release download" in workflow
    assert "unzip -q release/satn-pages.zip -d pages" in workflow
    assert "scripts/validate_pages_rendering.py pages" in workflow
    assert workflow.index("scripts/validate_pages_rendering.py pages") < workflow.index(
        "Upload validated Pages artifact"
    )
    assert "validate_pages_release.py" not in workflow
    assert "provenance-lock.json" not in workflow


def test_real_deployment_shards_use_stable_metadata_without_content_hashes(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    shutil.copytree(PROJECT / "examples" / "fixture", fixture)
    definition = AreaDefinition.from_yaml(fixture / "council.yaml")
    definition.publication.output_dir = tmp_path / "compiled"
    definition.source.snapshot_dir = tmp_path / "snapshots"
    authority = publication_destination_authority(
        workspace_root=fixture,
        approved_external_destination=definition.publication.output_dir,
    )
    snapshot(definition)
    compile(definition, publication_authority=authority)
    deployment = tmp_path / "deployment"
    build_area_deployment(
        definition,
        deployment,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )
    second_deployment = tmp_path / "deployment-second"
    build_area_deployment(
        definition,
        second_deployment,
        publication_authority=publication_destination_authority(workspace_root=tmp_path),
    )
    manifests = [
        deployment / "layer-manifest.json",
        deployment / "topography-manifest.json",
        deployment / "topography-profile-evidence.json",
    ]
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = json.dumps(manifest)
        assert '"sha256"' not in entries

    relative_geojson = sorted(
        path.relative_to(deployment).as_posix() for path in deployment.rglob("*.geojson")
    )
    second_relative_geojson = sorted(
        path.relative_to(second_deployment).as_posix()
        for path in second_deployment.rglob("*.geojson")
    )
    assert relative_geojson == second_relative_geojson
    for relative in relative_geojson:
        if relative.startswith("layers/"):
            assert re.fullmatch(r"layers/[a-z0-9-]+-\d{4}\.geojson", relative)
        elif relative.startswith("topography/"):
            assert re.fullmatch(r"topography/(overview|detail)-\d{4}\.geojson", relative)
        elif relative.startswith("evidence/"):
            assert re.fullmatch(r"evidence/topography-profiles-\d{4}\.geojson", relative)
        else:
            assert relative == "network.geojson"
