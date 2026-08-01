"""Adversarial tests for the publication replacement authority boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from satn.filesystem_safety import (
    OWNER_MARKER_NAME,
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


def test_competing_unowned_destination_created_after_staging_preserves_sentinel(
    tmp_path: Path,
) -> None:
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
        destination.mkdir()
        sentinel = destination / "sentinel"
        sentinel.write_text("preserve", encoding="utf-8")

        with pytest.raises(ValueError, match="destination changed since staging"):
            commit_replacement(
                staging,
                authority=authority,
                owner_kind="compiled-network:area-a",
                prior_record_name="run.json",
            )

        assert sentinel.read_text(encoding="utf-8") == "preserve"
    finally:
        staging.cleanup()


def test_parent_swap_after_staging_aborts_before_external_sentinel_is_touched(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    authority = _authority(root)
    destination = root / "published"
    staging = stage_replacement(
        destination,
        authority=authority,
        owner_kind="compiled-network:area-a",
        prior_record_name="run.json",
    )
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    hostile_destination = attacker / "published"
    hostile_destination.mkdir()
    sentinel = hostile_destination / "sentinel"
    sentinel.write_text("preserve", encoding="utf-8")
    retained = tmp_path / "retained-workspace"
    root.rename(retained)
    root.symlink_to(attacker, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="staging directory changed"):
            commit_replacement(
                staging,
                authority=authority,
                owner_kind="compiled-network:area-a",
                prior_record_name="run.json",
            )

        assert sentinel.read_text(encoding="utf-8") == "preserve"
    finally:
        staging.cleanup()


def test_stale_same_owner_publisher_cannot_replace_a_newer_publication(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    authority = _authority(root)
    destination = root / "published"
    destination.mkdir()
    write_ownership_marker(destination, owner_kind="compiled-network:area-a")
    first = stage_replacement(
        destination,
        authority=authority,
        owner_kind="compiled-network:area-a",
        prior_record_name="run.json",
    )
    second = stage_replacement(
        destination,
        authority=authority,
        owner_kind="compiled-network:area-a",
        prior_record_name="run.json",
    )
    try:
        (first.temporary / "generation").write_text("first", encoding="utf-8")
        write_ownership_marker(first.temporary, owner_kind="compiled-network:area-a")
        commit_replacement(
            first,
            authority=authority,
            owner_kind="compiled-network:area-a",
            prior_record_name="run.json",
        )
        (second.temporary / "generation").write_text("second", encoding="utf-8")
        write_ownership_marker(second.temporary, owner_kind="compiled-network:area-a")

        with pytest.raises(ValueError, match="destination changed since staging"):
            commit_replacement(
                second,
                authority=authority,
                owner_kind="compiled-network:area-a",
                prior_record_name="run.json",
            )

        assert (destination / "generation").read_text(encoding="utf-8") == "first"
    finally:
        first.cleanup()
        second.cleanup()


def test_target_created_between_revalidation_and_rename_is_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import satn.filesystem_safety as filesystem_safety

    root = tmp_path / "workspace"
    authority = _authority(root)
    destination = root / "published"
    staging = stage_replacement(
        destination,
        authority=authority,
        owner_kind="compiled-network:area-a",
        prior_record_name="run.json",
    )
    original_rename = filesystem_safety.os.rename
    inserted = False

    def create_competing_target(
        source: str, target: str, *args: object, **kwargs: object
    ) -> None:
        nonlocal inserted
        if source == destination.name and not inserted:
            inserted = True
            destination.mkdir()
            (destination / "sentinel").write_text("preserve", encoding="utf-8")
        original_rename(source, target, *args, **kwargs)

    monkeypatch.setattr(filesystem_safety.os, "rename", create_competing_target)
    try:
        with pytest.raises(ValueError, match="changed during atomic commit"):
            commit_replacement(
                staging,
                authority=authority,
                owner_kind="compiled-network:area-a",
                prior_record_name="run.json",
            )

        assert (destination / "sentinel").read_text(encoding="utf-8") == "preserve"
    finally:
        staging.cleanup()


def test_owned_target_removed_during_commit_does_not_install_stale_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import satn.filesystem_safety as filesystem_safety

    root = tmp_path / "workspace"
    authority = _authority(root)
    destination = root / "published"
    destination.mkdir()
    write_ownership_marker(destination, owner_kind="compiled-network:area-a")
    staging = stage_replacement(
        destination,
        authority=authority,
        owner_kind="compiled-network:area-a",
        prior_record_name="run.json",
    )
    original_rename = filesystem_safety.os.rename
    removed = tmp_path / "removed-published"

    def remove_target(source: str, target: str, *args: object, **kwargs: object) -> None:
        if source == destination.name:
            original_rename(destination, removed)
        original_rename(source, target, *args, **kwargs)

    monkeypatch.setattr(filesystem_safety.os, "rename", remove_target)
    try:
        with pytest.raises(ValueError, match="changed during atomic commit"):
            commit_replacement(
                staging,
                authority=authority,
                owner_kind="compiled-network:area-a",
                prior_record_name="run.json",
            )

        assert not destination.exists()
        assert (removed / OWNER_MARKER_NAME).is_file()
    finally:
        staging.cleanup()
