from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from satn.deployment_catalogue import build_deployment_catalogue, load_deployment_catalogue
from satn.models import AreaDefinition

PROJECT = Path(__file__).parents[1]


def test_catalogue_builds_a_small_root_selector_with_stable_area_links(tmp_path: Path) -> None:
    destination = build_deployment_catalogue(
        PROJECT / "deployments" / "catalogue.yaml", tmp_path / "pages"
    )

    publication = json.loads((destination / "catalogue.json").read_text(encoding="utf-8"))
    banes_definition = PROJECT / "deployments" / "banes" / "area.yaml"
    weca_definition = PROJECT / "deployments" / "weca" / "area.yaml"
    wiltshire_definition = PROJECT / "deployments" / "wiltshire" / "area.yaml"
    banes_snapshot_id = AreaDefinition.from_yaml(banes_definition).source.snapshot_id
    weca_snapshot_id = AreaDefinition.from_yaml(weca_definition).source.snapshot_id
    wiltshire_snapshot_id = AreaDefinition.from_yaml(wiltshire_definition).source.snapshot_id
    assert publication["schema_version"] == "satn-deployment-catalogue/v1"
    assert publication["title"] == "Agentic SATN Compiler deployments"
    published = {entry["deployment_id"]: entry for entry in publication["deployments"]}
    assert set(published) == {"banes", "weca", "wiltshire"}
    assert published["banes"] == {
        "deployment_id": "banes",
        "area_id": "bath-and-north-east-somerset",
        "area_name": "Bath and North East Somerset",
        "area_definition": "banes/area.yaml",
        "area_definition_sha256": hashlib.sha256(banes_definition.read_bytes()).hexdigest(),
        "deployment_path": "deployments/banes/",
        "artifacts": {
            "review_map": "deployments/banes/index.html",
            "network_map_pdf": "deployments/banes/network-map.pdf",
        },
        "title": "B&NES Strategic Active Travel Network Review",
        "scope": {
            "area_id": "bath-and-north-east-somerset",
            "area_name": "Bath and North East Somerset",
            "audience": "public",
        },
        "evidence_provenance": {
            "source": {
                "kind": "osm",
                "authority_boundary_queries": [
                    "Bath and North East Somerset, England, United Kingdom"
                ],
            },
            "snapshot": {"snapshot_id": banes_snapshot_id},
            "agent_runtime": {
                "response_mode": "direct-runtime",
                "provider": "fake",
                "model": None,
            },
        },
    }
    assert published["weca"] == {
        "deployment_id": "weca",
        "area_id": "west-of-england",
        "area_name": "West of England Combined Authority area",
        "area_definition": "weca/area.yaml",
        "area_definition_sha256": hashlib.sha256(weca_definition.read_bytes()).hexdigest(),
        "deployment_path": "deployments/weca/",
        "artifacts": {
            "review_map": "deployments/weca/index.html",
            "network_map_pdf": "deployments/weca/network-map.pdf",
        },
        "title": "West of England Strategic Active Travel Network Review",
        "scope": {
            "area_id": "west-of-england",
            "area_name": "West of England Combined Authority area",
            "audience": "public",
        },
        "evidence_provenance": {
            "source": {
                "kind": "osm",
                "authority_boundary_queries": [
                    "Bath and North East Somerset, England, United Kingdom",
                    "Bristol, England, United Kingdom",
                    "North Somerset, England, United Kingdom",
                    "South Gloucestershire, England, United Kingdom",
                ],
            },
            "snapshot": {"snapshot_id": weca_snapshot_id},
            "agent_runtime": {
                "response_mode": "direct-runtime",
                "provider": "fake",
                "model": None,
            },
        },
    }
    assert published["wiltshire"] == {
        "deployment_id": "wiltshire",
        "area_id": "wiltshire",
        "area_name": "Wiltshire Council",
        "area_definition": "wiltshire/area.yaml",
        "area_definition_sha256": hashlib.sha256(wiltshire_definition.read_bytes()).hexdigest(),
        "deployment_path": "deployments/wiltshire/",
        "artifacts": {
            "review_map": "deployments/wiltshire/index.html",
            "network_map_pdf": "deployments/wiltshire/network-map.pdf",
        },
        "title": "Wiltshire Strategic Active Travel Network Review",
        "scope": {
            "area_id": "wiltshire",
            "area_name": "Wiltshire Council",
            "audience": "public",
        },
        "evidence_provenance": {
            "source": {
                "kind": "fixture",
                "authority_boundary_queries": [],
            },
            "snapshot": {"snapshot_id": wiltshire_snapshot_id},
            "agent_runtime": {
                "response_mode": "direct-runtime",
                "provider": "fake",
                "model": None,
            },
        },
    }
    page = (destination / "index.html").read_text(encoding="utf-8")
    assert "Bath and North East Somerset" in page
    assert 'href="deployments/banes/index.html"' in page
    assert 'href="deployments/weca/network-map.pdf"' in page
    assert "Wiltshire Council" in page
    assert 'href="deployments/wiltshire/index.html"' in page
    assert ".zip" not in page
    assert set(path.name for path in destination.iterdir()) == {"catalogue.json", "index.html"}


def test_tracked_area_definitions_are_valid_and_write_only_to_ignored_build() -> None:
    for deployment_id, area_id in (
        ("banes", "bath-and-north-east-somerset"),
        ("weca", "west-of-england"),
        ("wiltshire", "wiltshire"),
    ):
        definition = AreaDefinition.from_yaml(PROJECT / "deployments" / deployment_id / "area.yaml")
        assert definition.area_id == area_id
        assert definition.deployment_slug == deployment_id
        assert definition.publication.output_dir == (PROJECT / "build" / "compiled" / deployment_id)


def test_catalogue_rejects_a_deployment_path_outside_pages_layout(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    catalogue.write_text(
        """schema_version: satn-deployment-catalogue/v1
title: Test deployments
deployments:
  - deployment_id: test
    area_id: test-geography
    area_name: Test area
    area_definition: test/area.yaml
    deployment_path: ../outside/
    artifacts:
      review_map: index.html
      network_map_pdf: network-map.pdf
      review_map_zip: review-map.zip
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="without traversal"):
        load_deployment_catalogue(catalogue)


def test_catalogue_requires_area_definition_identity_to_match_entry(tmp_path: Path) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    definition = tmp_path / "test" / "area.yaml"
    definition.parent.mkdir()
    definition.write_text(
        """area_id: test-geography
area_name: Different area
deployment_id: different-slug
source:
  snapshot_dir: snapshots
publication:
  output_dir: build
  title: Test deployment
""",
        encoding="utf-8",
    )
    catalogue.write_text(
        """schema_version: satn-deployment-catalogue/v1
title: Test deployments
deployments:
  - deployment_id: test-area
    area_id: test-geography
    area_name: Test area
    area_definition: test/area.yaml
    deployment_path: deployments/test-area/
    artifacts:
      review_map: index.html
      network_map_pdf: network-map.pdf
      review_map_zip: review-map.zip
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="deployment_slug must match"):
        load_deployment_catalogue(catalogue)


def test_catalogue_rejects_an_area_id_that_differs_from_its_area_definition(
    tmp_path: Path,
) -> None:
    catalogue = tmp_path / "catalogue.yaml"
    definition = tmp_path / "test" / "area.yaml"
    definition.parent.mkdir()
    definition.write_text(
        """area_id: definition-area
area_name: Test area
deployment_id: test-area
source:
  snapshot_dir: snapshots
publication:
  output_dir: build
  title: Test deployment
""",
        encoding="utf-8",
    )
    catalogue.write_text(
        """schema_version: satn-deployment-catalogue/v1
title: Test deployments
deployments:
  - deployment_id: test-area
    area_id: catalogue-area
    area_name: Test area
    area_definition: test/area.yaml
    deployment_path: deployments/test-area/
    artifacts:
      review_map: index.html
      network_map_pdf: network-map.pdf
      review_map_zip: review-map.zip
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="area_id must match catalogue area_id"):
        load_deployment_catalogue(catalogue)
