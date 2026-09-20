from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import satn.sources as sources
from satn.models import CouncilConfig
from satn.sources import load_snapshot, snapshot

PROJECT = Path(__file__).parents[1]


def _fixture_config(tmp_path: Path) -> CouncilConfig:
    fixture = tmp_path / "fixture"
    shutil.copytree(PROJECT / "examples" / "fixture", fixture)
    return CouncilConfig.from_yaml(fixture / "council.yaml")


def test_load_snapshot_keeps_geometry_loading_without_retained_byte_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _fixture_config(tmp_path)
    snapshot(config)
    calls: list[Path] = []

    def record_digest(path: Path) -> str:
        calls.append(path)
        raise AssertionError(f"ordinary snapshot load hashed retained bytes: {path}")

    monkeypatch.setattr(sources, "sha256_file", record_digest)

    loaded = load_snapshot(config)

    assert calls == []
    assert not loaded["network"].empty
    assert not loaded["boundary"].empty


def test_load_snapshot_keeps_missing_member_errors_without_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _fixture_config(tmp_path)
    snapshot_path = snapshot(config)
    (snapshot_path / "network.geojson").unlink()

    monkeypatch.setattr(
        sources,
        "sha256_file",
        lambda path: (_ for _ in ()).throw(AssertionError(f"hashed {path}")),
    )

    with pytest.raises(ValueError, match=r"snapshot file.*regular"):
        load_snapshot(config)


def test_load_snapshot_keeps_geojson_geometry_errors_without_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _fixture_config(tmp_path)
    snapshot_path = snapshot(config)
    network_path = snapshot_path / "network.geojson"
    network = json.loads(network_path.read_text(encoding="utf-8"))
    network["features"][0]["geometry"] = None
    network_path.write_text(json.dumps(network), encoding="utf-8")

    monkeypatch.setattr(
        sources,
        "sha256_file",
        lambda path: (_ for _ in ()).throw(AssertionError(f"hashed {path}")),
    )
    monkeypatch.setattr(sources, "STREAMING_GEOJSON_THRESHOLD_BYTES", 1)

    with pytest.raises(ValueError, match=r"malformed GeoJSON feature"):
        load_snapshot(config)
