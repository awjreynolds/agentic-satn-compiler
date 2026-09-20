"""Publication destination guards and simple staged replacement primitives."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

OWNER_MARKER_NAME = ".satn-publication-owner.json"
OWNER_MARKER_SCHEMA = "satn-publication-destination-authority/v1"


class PublicationRollbackError(RuntimeError):
    """An install and its rollback failed, but the previous publication is retained."""

    def __init__(
        self,
        *,
        destination_name: str,
        retained_previous_name: str,
        install_error: Exception,
        rollback_error: OSError,
    ) -> None:
        self.destination_name = destination_name
        self.retained_previous_name = retained_previous_name
        self.install_error = install_error
        self.rollback_error = rollback_error
        super().__init__(
            "publication install and rollback failed; previous publication retained "
            f"as sibling {retained_previous_name!r} instead of {destination_name!r}"
        )


@dataclass(frozen=True)
class PublicationDestinationAuthority:
    """Caller-owned authority for one publication workspace and optional exception."""

    workspace_root: Path
    approved_external_destination: Path | None = None
    expected_prior_run_fingerprint: str | None = None


@dataclass
class ReplacementStaging:
    """A temporary publication directory awaiting replacement."""

    temporary: Path
    destination_name: str

    def cleanup(self) -> None:
        if self.temporary.is_dir() and not self.temporary.is_symlink():
            shutil.rmtree(self.temporary)


def publication_destination_authority(
    *,
    workspace_root: Path,
    approved_external_destination: Path | None = None,
    expected_prior_run_fingerprint: str | None = None,
) -> PublicationDestinationAuthority:
    """Create an explicit, non-interactive publication capability at the caller seam."""
    return PublicationDestinationAuthority(
        workspace_root=Path(workspace_root),
        approved_external_destination=(
            Path(approved_external_destination)
            if approved_external_destination is not None
            else None
        ),
        expected_prior_run_fingerprint=expected_prior_run_fingerprint,
    )


def default_publication_destination_authority(config_path: Path) -> PublicationDestinationAuthority:
    """Derive a workspace from the file location, never from Area Definition data."""
    config_path = Path(config_path).resolve()
    for candidate in (config_path.parent, *config_path.parents):
        if (candidate / "pyproject.toml").is_file():
            return publication_destination_authority(workspace_root=candidate)
    # Without a repository marker, the definition directory is the narrowest
    # caller-owned workspace.  An untrusted definition must not gain authority
    # over sibling directories merely because both share a temporary parent.
    return publication_destination_authority(workspace_root=config_path.parent)


def validate_replaceable_destination(destination: Path, *, repository_root: Path) -> Path:
    """Legacy narrow guard retained for call sites outside publication authority."""
    destination = Path(destination)
    if destination.is_symlink():
        raise ValueError("publication destination must not be a symlink")
    current = destination.parent
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("publication destination must not have a symlink parent")
        current = current.parent
    resolved = destination.resolve(strict=False)
    prohibited = {Path("/").resolve(), Path.home().resolve(), repository_root.resolve()}
    if resolved in prohibited:
        raise ValueError("publication destination must not be filesystem, home, or repository root")
    if destination.exists() and not destination.is_dir():
        raise ValueError("publication destination must be a directory when it exists")
    return destination


def unique_absent_backup_sibling(destination: Path) -> Path:
    """Reserve a unique backup name, then make it absent for rename."""
    reserved = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-previous-", dir=destination.parent)
    )
    reserved.rmdir()
    return reserved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_path_without_symlinks(path: Path) -> None:
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ValueError(
                "publication destination must not be a symlink or have a symlink parent"
            )
        current = current.parent


def _marker_authorizes(path: Path, owner_kind: str) -> bool:
    marker = path / OWNER_MARKER_NAME
    if marker.is_symlink():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload == {"owner_kind": owner_kind, "schema_version": OWNER_MARKER_SCHEMA}


def _fingerprint_authorizes(path: Path, *, record_name: str, fingerprint: str | None) -> bool:
    if not fingerprint:
        return False
    record_path = path / record_name
    if record_path.is_symlink():
        return False
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(record, dict) and record.get("compilation_input_fingerprint") == fingerprint


def authorize_replacement(
    destination: Path,
    *,
    authority: PublicationDestinationAuthority,
    owner_kind: str,
    prior_record_name: str,
) -> Path:
    """Fail closed unless the caller owns this workspace or exact external target."""
    destination = Path(destination)
    root = Path(authority.workspace_root)
    _validate_path_without_symlinks(root)
    _validate_path_without_symlinks(destination.parent)
    resolved_root = root.resolve(strict=True)
    resolved_destination = destination.resolve(strict=False)
    if resolved_destination in {Path("/").resolve(), Path.home().resolve(), resolved_root}:
        raise ValueError("publication destination must not be filesystem, home, or workspace root")
    approved = authority.approved_external_destination
    external = approved is not None and resolved_destination == Path(approved).resolve(strict=False)
    if not _is_relative_to(resolved_destination, resolved_root) and not external:
        raise ValueError("publication destination is outside the declared publication workspace")
    if destination.is_symlink():
        raise ValueError("publication destination must be a non-symlink directory")
    if destination.exists():
        if not destination.is_dir():
            raise ValueError("publication destination must be a non-symlink directory")
        if not (
            _marker_authorizes(destination, owner_kind)
            or _fingerprint_authorizes(
                destination,
                record_name=prior_record_name,
                fingerprint=authority.expected_prior_run_fingerprint,
            )
        ):
            raise ValueError(
                "publication replacement requires compiler ownership or exact prior-run fingerprint"
            )
    return destination


def stage_replacement(
    destination: Path,
    *,
    authority: PublicationDestinationAuthority,
    owner_kind: str,
    prior_record_name: str,
) -> ReplacementStaging:
    """Authorize a destination and create a temporary sibling for the new output."""
    destination = authorize_replacement(
        destination,
        authority=authority,
        owner_kind=owner_kind,
        prior_record_name=prior_record_name,
    )
    root = Path(authority.workspace_root).resolve(strict=True)
    resolved_destination = destination.resolve(strict=False)
    if _is_relative_to(resolved_destination, root):
        destination.parent.mkdir(parents=True, exist_ok=True)
    elif not destination.parent.is_dir():
        raise ValueError("external publication destination parent must already exist")
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    return ReplacementStaging(temporary=temporary, destination_name=destination.name)


def write_ownership_marker(directory: Path, *, owner_kind: str) -> None:
    (directory / OWNER_MARKER_NAME).write_text(
        json.dumps(
            {"owner_kind": owner_kind, "schema_version": OWNER_MARKER_SCHEMA},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def commit_replacement(
    staging: ReplacementStaging,
    *,
    authority: PublicationDestinationAuthority,
    owner_kind: str,
    prior_record_name: str,
) -> None:
    """Replace the destination while retaining a backup until installation succeeds."""
    del authority, owner_kind, prior_record_name
    destination = staging.temporary.parent / staging.destination_name
    if not staging.temporary.is_dir() or staging.temporary.is_symlink():
        raise ValueError("publication staging directory changed before atomic commit")
    had_previous = destination.exists()
    backup = unique_absent_backup_sibling(destination) if had_previous else None
    try:
        if backup is not None:
            os.replace(destination, backup)
        os.replace(staging.temporary, destination)
    except Exception as install_error:
        if backup is not None and backup.exists():
            if destination.exists():
                rollback_error = FileExistsError(
                    f"publication destination {destination.name!r} is occupied"
                )
                raise PublicationRollbackError(
                    destination_name=destination.name,
                    retained_previous_name=backup.name,
                    install_error=install_error,
                    rollback_error=rollback_error,
                ) from install_error
            try:
                os.replace(backup, destination)
            except OSError as rollback_error:
                raise PublicationRollbackError(
                    destination_name=destination.name,
                    retained_previous_name=backup.name,
                    install_error=install_error,
                    rollback_error=rollback_error,
                ) from rollback_error
        raise
    if backup is not None:
        shutil.rmtree(backup)
