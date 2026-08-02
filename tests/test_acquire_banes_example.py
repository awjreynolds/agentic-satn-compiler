from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "acquire_banes_example.py"
SPEC = importlib.util.spec_from_file_location("acquire_banes_example", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
acquire_banes_example = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acquire_banes_example)


def _snapshot_archive(path: Path, *, member_name: str | None = None) -> Path:
    snapshot_id = acquire_banes_example.SNAPSHOT_ID
    content = b'{"type":"FeatureCollection","features":[]}'
    manifest = {
        "schema_version": "2.0",
        "snapshot_id": snapshot_id,
        "source_identifier": "documentation-test",
        "source_kind": "fixture",
        "retrieved_at": "2026-08-02T00:00:00+00:00",
        "attribution": "documentation test",
        "disclaimer": "test only",
        "files": ["boundary.geojson"],
        "file_sha256": {"boundary.geojson": hashlib.sha256(content).hexdigest()},
        "provenance_file_sha256": {},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            member_name or f"{snapshot_id}/boundary.geojson",
            content,
        )
        archive.writestr(f"{snapshot_id}/snapshot.json", json.dumps(manifest))
    return path


def test_extract_archive_validates_and_promotes_atomically(tmp_path: Path) -> None:
    archive = _snapshot_archive(tmp_path / "snapshot.zip")
    destination = tmp_path / "snapshots" / acquire_banes_example.SNAPSHOT_ID

    acquire_banes_example.extract_archive(archive, destination)

    acquire_banes_example.validate_snapshot(destination)
    assert (destination / "boundary.geojson").is_file()


def test_extract_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive = _snapshot_archive(tmp_path / "snapshot.zip", member_name="../escape")
    destination = tmp_path / "snapshots" / acquire_banes_example.SNAPSHOT_ID

    with pytest.raises(ValueError, match="escapes"):
        acquire_banes_example.extract_archive(archive, destination)

    assert not destination.exists()


def test_acquire_rejects_an_unpinned_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _snapshot_archive(tmp_path / "snapshot.zip")
    destination = tmp_path / "snapshots" / acquire_banes_example.SNAPSHOT_ID
    monkeypatch.setattr(acquire_banes_example, "ARCHIVE_SHA256", "0" * 64)

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        acquire_banes_example.acquire_snapshot(
            archive_path=archive,
            destination=destination,
        )

    assert not destination.exists()
