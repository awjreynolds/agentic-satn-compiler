"""Narrow filesystem guards for destructive publication commit points."""

from __future__ import annotations

import tempfile
from pathlib import Path


def validate_replaceable_destination(destination: Path, *, repository_root: Path) -> Path:
    """Reject symlinked or dangerously broad destinations before replacement."""
    destination = Path(destination)
    if destination.is_symlink():
        raise ValueError("publication destination must not be a symlink")
    current = destination.parent
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("publication destination must not have a symlink parent")
        current = current.parent
    resolved = destination.resolve(strict=False)
    prohibited = {
        Path("/").resolve(),
        Path.home().resolve(),
        repository_root.resolve(),
    }
    if resolved in prohibited:
        raise ValueError("publication destination must not be filesystem, home, or repository root")
    if destination.exists() and not destination.is_dir():
        raise ValueError("publication destination must be a directory when it exists")
    return destination


def unique_absent_backup_sibling(destination: Path) -> Path:
    """Reserve a process-owned unique backup name, then make it absent for rename."""
    reserved = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-previous-", dir=destination.parent)
    )
    reserved.rmdir()
    return reserved
