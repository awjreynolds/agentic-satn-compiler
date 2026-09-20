"""Focused tests for publication destination authority and replacement."""

from __future__ import annotations

from pathlib import Path

import pytest

from satn.filesystem_safety import (
    OWNER_MARKER_NAME,
    PublicationRollbackError,
    authorize_replacement,
    commit_replacement,
    publication_destination_authority,
    stage_replacement,
    write_ownership_marker,
)


def _authority(root: Path):  # type: ignore[no-untyped-def]
    root.mkdir()
    return publication_destination_authority(workspace_root=root)


def test_workspace_root_itself_is_never_a_publication_destination(tmp_path: Path) -> None:
    root = tmp_path / "workspace"

    with pytest.raises(ValueError, match="workspace root"):
        authorize_replacement(
            root,
            authority=_authority(root),
            owner_kind="compiled-network:area-a",
            prior_record_name="run.json",
        )


def test_symlink_destination_is_never_a_publication_destination(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    authority = _authority(root)
    actual = root / "actual"
    actual.mkdir()
    destination = root / "published"
    destination.symlink_to(actual, target_is_directory=True)

    with pytest.raises(ValueError, match="non-symlink directory"):
        stage_replacement(
            destination,
            authority=authority,
            owner_kind="compiled-network:area-a",
            prior_record_name="run.json",
        )

    assert not (actual / OWNER_MARKER_NAME).exists()


def test_unowned_existing_destination_is_not_replaced_even_inside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    authority = _authority(root)
    destination = root / "unrelated"
    destination.mkdir()
    sentinel = destination / "sentinel"
    sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="requires compiler ownership"):
        stage_replacement(
            destination,
            authority=authority,
            owner_kind="compiled-network:area-a",
            prior_record_name="run.json",
        )

    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_foreign_area_marker_cannot_authorize_replacement(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    authority = _authority(root)
    destination = root / "published"
    destination.mkdir()
    write_ownership_marker(destination, owner_kind="compiled-network:area-b")

    with pytest.raises(ValueError, match="requires compiler ownership"):
        stage_replacement(
            destination,
            authority=authority,
            owner_kind="compiled-network:area-a",
            prior_record_name="run.json",
        )

    assert (destination / OWNER_MARKER_NAME).is_file()


def test_stages_and_commits_a_new_destination(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    authority = _authority(root)
    destination = root / "published"
    staging = stage_replacement(
        destination,
        authority=authority,
        owner_kind="compiled-network:area-a",
        prior_record_name="run.json",
    )
    try:
        (staging.temporary / "generation").write_text("new", encoding="utf-8")
        write_ownership_marker(staging.temporary, owner_kind="compiled-network:area-a")
        commit_replacement(
            staging,
            authority=authority,
            owner_kind="compiled-network:area-a",
            prior_record_name="run.json",
        )

        assert (destination / "generation").read_text(encoding="utf-8") == "new"
        assert (destination / OWNER_MARKER_NAME).is_file()
    finally:
        staging.cleanup()


def test_failed_install_restores_previous_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import satn.filesystem_safety as filesystem_safety

    root = tmp_path / "workspace"
    authority = _authority(root)
    destination = root / "published"
    destination.mkdir()
    write_ownership_marker(destination, owner_kind="compiled-network:area-a")
    (destination / "generation").write_text("previous", encoding="utf-8")
    staging = stage_replacement(
        destination,
        authority=authority,
        owner_kind="compiled-network:area-a",
        prior_record_name="run.json",
    )
    original_replace = filesystem_safety.os.replace

    def fail_install(source: object, target: object) -> None:
        if Path(source) == staging.temporary and Path(target) == destination:
            raise OSError("simulated install failure")
        original_replace(source, target)

    monkeypatch.setattr(filesystem_safety.os, "replace", fail_install)
    try:
        (staging.temporary / "generation").write_text("new", encoding="utf-8")
        write_ownership_marker(staging.temporary, owner_kind="compiled-network:area-a")
        with pytest.raises(OSError, match="simulated install failure"):
            commit_replacement(
                staging,
                authority=authority,
                owner_kind="compiled-network:area-a",
                prior_record_name="run.json",
            )

        assert (destination / "generation").read_text(encoding="utf-8") == "previous"
        assert not list(root.glob(".published-previous-*"))
    finally:
        staging.cleanup()


def test_failed_rollback_retains_previous_destination_as_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import satn.filesystem_safety as filesystem_safety

    root = tmp_path / "workspace"
    authority = _authority(root)
    destination = root / "published"
    destination.mkdir()
    write_ownership_marker(destination, owner_kind="compiled-network:area-a")
    (destination / "generation").write_text("previous", encoding="utf-8")
    staging = stage_replacement(
        destination,
        authority=authority,
        owner_kind="compiled-network:area-a",
        prior_record_name="run.json",
    )
    original_replace = filesystem_safety.os.replace

    def fail_install_and_rollback(source: object, target: object) -> None:
        source_path = Path(source)
        target_path = Path(target)
        if source_path == staging.temporary and target_path == destination:
            raise OSError("simulated install failure")
        if source_path.name.startswith(".published-previous-") and target_path == destination:
            raise OSError("simulated rollback failure")
        original_replace(source, target)

    monkeypatch.setattr(filesystem_safety.os, "replace", fail_install_and_rollback)
    try:
        (staging.temporary / "generation").write_text("new", encoding="utf-8")
        write_ownership_marker(staging.temporary, owner_kind="compiled-network:area-a")
        with pytest.raises(PublicationRollbackError) as raised:
            commit_replacement(
                staging,
                authority=authority,
                owner_kind="compiled-network:area-a",
                prior_record_name="run.json",
            )

        retained = root / raised.value.retained_previous_name
        assert (retained / "generation").read_text(encoding="utf-8") == "previous"
        assert not destination.exists()
    finally:
        staging.cleanup()
