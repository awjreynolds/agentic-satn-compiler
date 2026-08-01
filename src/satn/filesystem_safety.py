"""Authority and race-safe commit primitives for destructive publication writes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
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
    """A staging directory held beneath an opened, no-follow parent directory."""

    temporary: Path
    parent_fd: int
    destination_name: str
    expected_destination_state: tuple[int, int, str | None, str | None] | None

    def cleanup(self) -> None:
        try:
            try:
                os.stat(self.temporary.name, dir_fd=self.parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                shutil.rmtree(self.temporary.name, dir_fd=self.parent_fd)
        finally:
            os.close(self.parent_fd)


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
    """Reserve a process-owned unique backup name, then make it absent for rename.

    Retained while older non-publication call sites migrate to ``commit_replacement``.
    """
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


def _fingerprint_authorizes(
    path: Path, *, record_name: str, fingerprint: str | None
) -> bool:
    if not fingerprint:
        return False
    record_path = path / record_name
    if record_path.is_symlink():
        return False
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return record.get("compilation_input_fingerprint") == fingerprint


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
    if destination.exists():
        if not destination.is_dir() or destination.is_symlink():
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


def _open_directory_no_follow(path: Path) -> int:
    """Open every path component without following a symlink introduced mid-walk."""
    if not path.is_absolute():
        raise ValueError("publication workspace must be absolute")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def stage_replacement(
    destination: Path,
    *,
    authority: PublicationDestinationAuthority,
    owner_kind: str,
    prior_record_name: str,
) -> ReplacementStaging:
    """Create a temporary sibling through trusted directory descriptors only."""
    destination = authorize_replacement(
        destination,
        authority=authority,
        owner_kind=owner_kind,
        prior_record_name=prior_record_name,
    )
    root = Path(authority.workspace_root).resolve(strict=True)
    resolved_destination = destination.resolve(strict=False)
    if not _is_relative_to(resolved_destination, root):
        # An explicit capability is exact, but its parent must already be a
        # real directory; the compiler never manufactures arbitrary external trees.
        parent_fd = _open_directory_no_follow(destination.parent)
    else:
        parent_fd = _open_directory_no_follow(root)
        relative_parent = resolved_destination.parent.relative_to(root)
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        try:
            for part in relative_parent.parts:
                try:
                    next_fd = os.open(part, flags, dir_fd=parent_fd)
                except FileNotFoundError:
                    os.mkdir(part, dir_fd=parent_fd)
                    next_fd = os.open(part, flags, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
        except Exception:
            os.close(parent_fd)
            raise
    name = f".{destination.name}-{uuid.uuid4().hex}"
    os.mkdir(name, dir_fd=parent_fd)
    return ReplacementStaging(
        # Geospatial writers require an ordinary filesystem path.  The held
        # parent descriptor and inode comparison at commit bind this pathname
        # back to the exact directory created above before any rename occurs.
        temporary=destination.parent / name,
        parent_fd=parent_fd,
        destination_name=destination.name,
        expected_destination_state=_destination_state(
            parent_fd,
            destination.name,
            prior_record_name,
        ),
    )


def write_ownership_marker(directory: Path, *, owner_kind: str) -> None:
    (directory / OWNER_MARKER_NAME).write_text(
        json.dumps(
            {"owner_kind": owner_kind, "schema_version": OWNER_MARKER_SCHEMA},
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )


def _current_destination_authorized(
    staging: ReplacementStaging,
    *,
    authority: PublicationDestinationAuthority,
    owner_kind: str,
    prior_record_name: str,
) -> bool:
    try:
        status = os.stat(staging.destination_name, dir_fd=staging.parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    if not stat.S_ISDIR(status.st_mode):
        return False
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        destination_fd = os.open(
            staging.destination_name,
            flags,
            dir_fd=staging.parent_fd,
        )
    except OSError:
        return False
    try:
        marker = _json_record_at(destination_fd, OWNER_MARKER_NAME)
        if marker == {
            "owner_kind": owner_kind,
            "schema_version": OWNER_MARKER_SCHEMA,
        }:
            return True
        fingerprint = authority.expected_prior_run_fingerprint
        if not fingerprint:
            return False
        record = _json_record_at(destination_fd, prior_record_name)
        return record is not None and record.get("compilation_input_fingerprint") == fingerprint
    finally:
        os.close(destination_fd)


def _regular_file_digest_at(directory_fd: int, name: str) -> str | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    finally:
        os.close(descriptor)


def _destination_state(
    parent_fd: int,
    destination_name: str,
    prior_record_name: str,
) -> tuple[int, int, str | None, str | None] | None:
    try:
        status = os.stat(destination_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(status.st_mode):
        return (status.st_dev, status.st_ino, None, None)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(destination_name, flags, dir_fd=parent_fd)
    except OSError:
        return (status.st_dev, status.st_ino, None, None)
    try:
        return (
            status.st_dev,
            status.st_ino,
            _regular_file_digest_at(descriptor, OWNER_MARKER_NAME),
            _regular_file_digest_at(descriptor, prior_record_name),
        )
    finally:
        os.close(descriptor)


def _json_record_at(directory_fd: int, name: str) -> dict[str, object] | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError:
        return None
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            return None
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as stream:
            payload = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    finally:
        os.close(descriptor)
    return payload if isinstance(payload, dict) else None


def commit_replacement(
    staging: ReplacementStaging,
    *,
    authority: PublicationDestinationAuthority,
    owner_kind: str,
    prior_record_name: str,
) -> None:
    """Revalidate then atomically replace using the opened parent, not a pathname race."""
    try:
        path_status = os.stat(staging.temporary, follow_symlinks=False)
        held_status = os.stat(
            staging.temporary.name,
            dir_fd=staging.parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError as error:
        raise ValueError("publication staging directory changed before atomic commit") from error
    if (
        not stat.S_ISDIR(path_status.st_mode)
        or (path_status.st_dev, path_status.st_ino)
        != (held_status.st_dev, held_status.st_ino)
    ):
        raise ValueError("publication staging directory changed before atomic commit")
    if (
        _destination_state(staging.parent_fd, staging.destination_name, prior_record_name)
        != staging.expected_destination_state
    ):
        raise ValueError("publication destination changed since staging")
    if not _current_destination_authorized(
        staging,
        authority=authority,
        owner_kind=owner_kind,
        prior_record_name=prior_record_name,
    ):
        raise ValueError("publication replacement lost authorization before atomic commit")
    backup_name = f".{staging.destination_name}-previous-{uuid.uuid4().hex}"
    had_previous = False
    try:
        try:
            os.rename(
                staging.destination_name,
                backup_name,
                src_dir_fd=staging.parent_fd,
                dst_dir_fd=staging.parent_fd,
            )
            had_previous = True
            if (
                _destination_state(staging.parent_fd, backup_name, prior_record_name)
                != staging.expected_destination_state
            ):
                os.rename(
                    backup_name,
                    staging.destination_name,
                    src_dir_fd=staging.parent_fd,
                    dst_dir_fd=staging.parent_fd,
                )
                had_previous = False
                raise ValueError("publication destination changed during atomic commit")
        except FileNotFoundError as error:
            if staging.expected_destination_state is not None:
                raise ValueError("publication destination changed during atomic commit") from error
        os.rename(
            staging.temporary.name,
            staging.destination_name,
            src_dir_fd=staging.parent_fd,
            dst_dir_fd=staging.parent_fd,
        )
    except Exception as install_error:
        if had_previous:
            try:
                os.rename(
                    backup_name,
                    staging.destination_name,
                    src_dir_fd=staging.parent_fd,
                    dst_dir_fd=staging.parent_fd,
                )
            except OSError as rollback_error:
                raise PublicationRollbackError(
                    destination_name=staging.destination_name,
                    retained_previous_name=backup_name,
                    install_error=install_error,
                    rollback_error=rollback_error,
                ) from rollback_error
        raise
    if had_previous:
        shutil.rmtree(backup_name, dir_fd=staging.parent_fd)
