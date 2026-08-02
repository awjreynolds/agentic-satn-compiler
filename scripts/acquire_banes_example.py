#!/usr/bin/env python3
"""Acquire and verify the governed B&NES documentation snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from satn.sources import _validate_snapshot

SNAPSHOT_ID = "banes-osm-open-roads-v1-2026-07-29"
ARCHIVE_NAME = f"{SNAPSHOT_ID}.zip"
ARCHIVE_SHA256 = "53f2f2e8e23a24afb6ab44536f0745cc7cecd3d8366761b28cbf6e00be6fafd3"
ARCHIVE_URL = (
    "https://github.com/awjreynolds/agentic-satn-compiler/releases/download/"
    "asatnc-governed-inputs-banes-2026-07-29/"
    f"{ARCHIVE_NAME}"
)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of one regular file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_snapshot(path: Path) -> None:
    """Apply the compiler's normal snapshot checks and bind the expected identity."""
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"B&NES snapshot target is missing or unsafe: {path}")
    _validate_snapshot(path)
    manifest = json.loads((path / "snapshot.json").read_text(encoding="utf-8"))
    if manifest.get("snapshot_id") != SNAPSHOT_ID:
        raise ValueError("B&NES snapshot identity does not match the pinned release")


def _safe_member_path(member: zipfile.ZipInfo) -> PurePosixPath:
    path = PurePosixPath(member.filename)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"archive member escapes the snapshot directory: {member.filename}")
    if not path.parts or path.parts[0] != SNAPSHOT_ID:
        raise ValueError(f"archive member is outside {SNAPSHOT_ID}: {member.filename}")
    mode = member.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise ValueError(f"archive contains a symbolic link: {member.filename}")
    return path


def extract_archive(archive_path: Path, destination: Path) -> None:
    """Safely and atomically extract the one pinned snapshot directory."""
    if destination.exists() or destination.is_symlink():
        validate_snapshot(destination)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix=f".{SNAPSHOT_ID}-", dir=destination.parent)
    )
    staged = temporary_root / SNAPSHOT_ID
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if not members:
                raise ValueError("B&NES snapshot archive is empty")
            for member in members:
                path = _safe_member_path(member)
                relative = Path(*path.parts[1:])
                target = staged / relative
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
        validate_snapshot(staged)
        os.replace(staged, destination)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def acquire_snapshot(*, archive_path: Path | None, destination: Path) -> Path:
    """Acquire a missing snapshot or verify an existing immutable target."""
    if destination.exists() or destination.is_symlink():
        validate_snapshot(destination)
        return destination

    with tempfile.TemporaryDirectory(prefix="satn-banes-download-") as temporary:
        downloaded = Path(temporary) / ARCHIVE_NAME
        source = archive_path
        if source is None:
            print(f"downloading {ARCHIVE_URL}")
            urllib.request.urlretrieve(ARCHIVE_URL, downloaded)
            source = downloaded
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"B&NES snapshot archive is missing or unsafe: {source}")
        actual_sha256 = sha256_file(source)
        if actual_sha256 != ARCHIVE_SHA256:
            raise ValueError(
                "B&NES snapshot archive SHA-256 mismatch: "
                f"expected {ARCHIVE_SHA256}, found {actual_sha256}"
            )
        extract_archive(source, destination)
    validate_snapshot(destination)
    return destination


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        help="Use a local copy of the pinned ZIP instead of downloading it.",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=project_root / "data" / "snapshots" / SNAPSHOT_ID,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    destination = acquire_snapshot(
        archive_path=args.archive.resolve() if args.archive else None,
        destination=args.destination.resolve(),
    )
    try:
        display_path = destination.relative_to(project_root)
    except ValueError:
        display_path = destination
    print(f"verified B&NES snapshot: {display_path}")


if __name__ == "__main__":
    main()
