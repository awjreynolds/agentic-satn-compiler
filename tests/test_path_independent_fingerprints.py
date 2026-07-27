"""Governed compilation identity is portable between byte-identical checkouts."""

from __future__ import annotations

import shutil
from pathlib import Path

from satn.models import AgentDecisionLedger, AreaDefinition
from satn.pipeline import (
    area_definition_sha256,
    compilation_governed_input_fingerprint,
    decision_ledger_input_fingerprint,
)

PROJECT = Path(__file__).parents[1]
BANES_AREA = PROJECT / "deployments" / "banes" / "area.yaml"
DEPENDENCY_MANIFEST = {"sha256": "d" * 64, "components": []}


def _banes_checkout(root: Path) -> AreaDefinition:
    area = root / "deployments" / "banes" / "area.yaml"
    area.parent.mkdir(parents=True)
    shutil.copy2(BANES_AREA, area)
    files = {
        root / "data/snapshots/banes-osm-current/snapshot.json": b'{"snapshot":"banes"}',
        root
        / "data/governed/banes-os-open-roads-2026-04-07.geojson": b'{"roads":[]}',
        root
        / "data/local/ea-lidar-dtm-1m-banes-samples.geojson": b'{"elevation":[]}',
        root / "data/local/banes-atm-full.geojson": b'{"atm":[]}',
    }
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return AreaDefinition.from_yaml(area)


def _compilation_input_fingerprint(definition: AreaDefinition) -> str:
    governed = compilation_governed_input_fingerprint(
        definition,
        dependency_manifest=DEPENDENCY_MANIFEST,
    )
    return decision_ledger_input_fingerprint(governed, AgentDecisionLedger())


def test_banes_fingerprints_are_identical_in_byte_identical_checkouts(
    tmp_path: Path,
) -> None:
    first = _banes_checkout(tmp_path / "first-checkout")
    second = _banes_checkout(tmp_path / "nested" / "second-checkout")

    assert area_definition_sha256(first) == area_definition_sha256(second)
    assert _compilation_input_fingerprint(first) == _compilation_input_fingerprint(second)


def test_banes_governed_path_identity_and_content_remain_fingerprinted(
    tmp_path: Path,
) -> None:
    original = _banes_checkout(tmp_path / "original")
    moved = _banes_checkout(tmp_path / "moved")
    moved_path = moved.source.official_road_classification
    assert moved_path is not None
    new_path = moved_path.path.parent / "archive" / moved_path.path.name
    new_path.parent.mkdir()
    moved_path.path.replace(new_path)
    moved.source.official_road_classification = moved_path.model_copy(
        update={"path": new_path}
    )
    changed = _banes_checkout(tmp_path / "changed")
    changed_path = changed.source.official_road_classification
    assert changed_path is not None
    changed_path.path.write_bytes(b'{"roads":["changed"]}')

    original_fingerprint = _compilation_input_fingerprint(original)

    assert _compilation_input_fingerprint(moved) != original_fingerprint
    assert _compilation_input_fingerprint(changed) != original_fingerprint
