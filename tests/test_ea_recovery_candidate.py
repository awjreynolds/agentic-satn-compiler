from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import satn.ea_snapshot_recovery as recovery_module
import satn.pipeline as pipeline_module
import satn.publisher as publisher_module
import satn.sources as sources_module
from satn.publisher import EAFixedPointMismatchError


def _recovery_config(snapshot_dir: Path, snapshot_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        area_id="west-of-england",
        source=SimpleNamespace(
            snapshot_dir=snapshot_dir,
            snapshot_id=snapshot_id,
        ),
        publication=SimpleNamespace(output_dir=snapshot_dir / "published"),
        compilation=SimpleNamespace(full=False),
    )


def test_recovery_parent_loader_is_exactly_identity_and_normalization_gated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot_id = "legacy-v10"
    snapshot = tmp_path / snapshot_id
    snapshot.mkdir()
    manifest_path = snapshot / "snapshot.json"
    manifest_path.write_text(
        json.dumps({"snapshot_id": snapshot_id}),
        encoding="utf-8",
    )
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    config = _recovery_config(tmp_path, snapshot_id)
    validation: dict[str, object] = {}
    marker = {"network": object()}

    monkeypatch.setattr(
        recovery_module,
        "LEGACY_NAN_PARENT_SNAPSHOT_ID",
        snapshot_id,
    )
    monkeypatch.setattr(
        recovery_module,
        "LEGACY_NAN_PARENT_MANIFEST_SHA256",
        manifest_sha256,
    )
    monkeypatch.setattr(recovery_module, "LEGACY_NAN_EXPECTED_COUNT", 7)

    def validate(path: Path, **kwargs: object) -> None:
        validation.update({"path": path, **kwargs})
        kwargs["normalization_report"]["access_point_source_id"] = 7  # type: ignore[index]

    monkeypatch.setattr(recovery_module, "_validate_snapshot", validate)
    monkeypatch.setattr(
        recovery_module,
        "_read_snapshot_frames",
        lambda path: marker if path == snapshot else pytest.fail("wrong snapshot"),
    )

    assert recovery_module.load_legacy_ea_recovery_snapshot(config) is marker
    assert validation == {
        "path": snapshot,
        "defer_ea_route_nondegeneracy": True,
        "legacy_nan_property_key": "access_point_source_id",
        "expected_legacy_nan_count": 7,
        "normalization_report": {"access_point_source_id": 7},
    }


def test_generic_snapshot_loader_remains_strict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _recovery_config(tmp_path, "ordinary")
    snapshot = tmp_path / "ordinary"
    calls: list[tuple[object, ...]] = []
    marker = {"network": object()}

    monkeypatch.setattr(
        sources_module,
        "_validate_snapshot",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(sources_module, "_read_snapshot_frames", lambda path: marker)

    assert sources_module.load_snapshot(config) is marker
    assert calls == [((snapshot,), {})]


@pytest.mark.parametrize("difference", ["snapshot-id", "manifest-sha256"])
def test_recovery_parent_loader_refuses_any_other_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    difference: str,
) -> None:
    snapshot_id = "legacy-v10"
    snapshot = tmp_path / snapshot_id
    snapshot.mkdir()
    manifest_path = snapshot / "snapshot.json"
    manifest_path.write_text(
        json.dumps({"snapshot_id": snapshot_id}),
        encoding="utf-8",
    )
    actual_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    config = _recovery_config(tmp_path, snapshot_id)
    monkeypatch.setattr(
        recovery_module,
        "LEGACY_NAN_PARENT_SNAPSHOT_ID",
        "different-v10" if difference == "snapshot-id" else snapshot_id,
    )
    monkeypatch.setattr(
        recovery_module,
        "LEGACY_NAN_PARENT_MANIFEST_SHA256",
        "0" * 64 if difference == "manifest-sha256" else actual_sha256,
    )
    monkeypatch.setattr(
        recovery_module,
        "_validate_snapshot",
        lambda *_args, **_kwargs: pytest.fail("invalid parent reached recovery validation"),
    )

    with pytest.raises(ValueError, match="exact pinned WECA v10"):
        recovery_module.load_legacy_ea_recovery_snapshot(config)


def test_candidate_only_writer_retains_mismatch_without_publishing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _recovery_config(tmp_path, "legacy-v10")
    retained = tmp_path / "candidate"
    calls: list[str] = []

    monkeypatch.setattr(
        publisher_module,
        "_write_geojson",
        lambda path, _compiled: path.write_text("candidate", encoding="utf-8"),
    )
    monkeypatch.setattr(
        publisher_module,
        "_validate_ea_elevation_fixed_point",
        lambda *_args: (_ for _ in ()).throw(
            EAFixedPointMismatchError(expected="a" * 64, actual="b" * 64)
        ),
    )

    def retain(*_args: object, **kwargs: object) -> Path:
        calls.append("retain")
        assert kwargs["expected"] == "a" * 64
        assert kwargs["actual"] == "b" * 64
        return retained

    monkeypatch.setattr(
        publisher_module,
        "_retain_ea_fixed_point_candidate",
        retain,
    )
    monkeypatch.setattr(
        publisher_module,
        "publish",
        lambda *_args, **_kwargs: pytest.fail("recovery candidate attempted publication"),
    )

    assert publisher_module.retain_ea_recovery_candidate(
        config,
        SimpleNamespace(governed_input_fingerprint="c" * 64),
        "run-recovery",
    ) == {"candidate": retained}
    assert calls == ["retain"]
    assert not config.publication.output_dir.exists()


def test_candidate_only_writer_refuses_even_if_legacy_parent_matches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _recovery_config(tmp_path, "legacy-v10")
    monkeypatch.setattr(
        publisher_module,
        "_write_geojson",
        lambda path, _compiled: path.write_text("candidate", encoding="utf-8"),
    )
    monkeypatch.setattr(
        publisher_module,
        "_validate_ea_elevation_fixed_point",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        publisher_module,
        "_retain_ea_fixed_point_candidate",
        lambda *_args, **_kwargs: pytest.fail("matching parent retained a candidate"),
    )

    with pytest.raises(ValueError, match="refusing invalid-parent publication"):
        publisher_module.retain_ea_recovery_candidate(
            config,
            object(),
            "run-recovery",
        )
    assert not config.publication.output_dir.exists()


def test_recovery_entrypoint_injects_only_recovery_loader_and_candidate_writer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _recovery_config(tmp_path, "legacy-v10")
    config_path = tmp_path / "area.yaml"
    candidate = tmp_path / "candidate"
    calls: list[dict[str, object]] = []

    def compile_internal(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append({"args": args, **kwargs})
        return SimpleNamespace(artifacts={"candidate": candidate})

    monkeypatch.setattr(pipeline_module, "_compile", compile_internal)
    monkeypatch.setattr(
        pipeline_module.AreaDefinition,
        "from_yaml",
        lambda path: config if path == config_path else pytest.fail("wrong config"),
    )

    assert pipeline_module.compile_ea_recovery_candidate(config_path) == candidate
    assert config.compilation.full is True
    assert len(calls) == 1
    assert calls[0]["args"] == (config,)
    assert calls[0]["heartbeat"] is not None
    assert calls[0]["source_loader"] is recovery_module.load_legacy_ea_recovery_snapshot
    assert calls[0]["publisher"] is publisher_module.retain_ea_recovery_candidate
