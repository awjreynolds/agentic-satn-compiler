from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from satn.models import CouncilConfig, RetainedCoreSourceConfig, SourceConfig
from satn.sources import ELEVATION_EVIDENCE_FILENAME, snapshot

PROJECT = Path(__file__).parents[1]
WECA_BENCHMARK_SHA256 = "24a03e50ccfe541ff637b9c75f15caa41ac452cc20667f31df5ad274ffbeae6a"


def copied_config(tmp_path: Path) -> CouncilConfig:
    fixture = tmp_path / "fixture"
    shutil.copytree(
        PROJECT / "examples" / "fixture",
        fixture,
        ignore=shutil.ignore_patterns("work", ".satn-cache"),
    )
    return CouncilConfig.from_yaml(fixture / "council.yaml")


def retained_core_source(config: CouncilConfig, source_id: str) -> tuple[Path, str]:
    config.source.snapshot_id = source_id
    source = snapshot(config)
    manifest_sha256 = hashlib.sha256((source / "snapshot.json").read_bytes()).hexdigest()
    return source, manifest_sha256


def configure_lineaged_target(
    config: CouncilConfig,
    *,
    source_id: str,
    manifest_sha256: str,
    target_id: str = "final-elevation",
) -> Path:
    config.source.snapshot_id = target_id
    config.source.retained_core_source = RetainedCoreSourceConfig(
        snapshot_id=source_id,
        manifest_sha256=manifest_sha256,
    )
    return config.source.snapshot_dir / target_id


def reseal_snapshot_member(snapshot_path: Path, filename: str, *, manifest_field: str) -> None:
    """Mutate a target member and update only its target-local manifest hash."""
    member = snapshot_path / filename
    # Trailing whitespace preserves valid GeoJSON while changing the governed
    # byte identity, and is equally sufficient for a provenance-only sidecar.
    member.write_bytes(member.read_bytes() + b"\n")
    manifest_path = snapshot_path / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[manifest_field][filename] = hashlib.sha256(member.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


@pytest.mark.parametrize(
    "snapshot_id",
    [
        ".",
        "..",
        "../escape",
        "nested/target",
        r"nested\target",
        "CON",
        "PrN.txt",
        "AUX.log",
        "nul.json",
        "COM1.geojson",
        "com9.data",
        "LPT1.csv",
        "lpt9.evidence",
        "trailing.",
    ],
)
def test_source_config_rejects_unsafe_snapshot_ids(tmp_path: Path, snapshot_id: str) -> None:
    with pytest.raises(ValueError, match="safe snapshot basename"):
        SourceConfig(snapshot_dir=tmp_path, snapshot_id=snapshot_id)


@pytest.mark.parametrize("snapshot_id", ["COM0", "COM10", "LPT0", "LPT10", "CONSOLE", "NULLED"])
def test_source_config_accepts_portable_near_miss_snapshot_ids(
    tmp_path: Path, snapshot_id: str
) -> None:
    assert SourceConfig(snapshot_dir=tmp_path, snapshot_id=snapshot_id).snapshot_id == snapshot_id


def test_snapshot_rejects_mutated_traversal_before_creating_root_or_acquiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = copied_config(tmp_path)
    root = config.source.snapshot_dir
    config.source.snapshot_id = "../outside"
    acquired = False

    def unexpected_acquisition(*_args: object, **_kwargs: object) -> object:
        nonlocal acquired
        acquired = True
        raise AssertionError("snapshot acquisition must not run")

    monkeypatch.setattr("satn.sources._write_fixture_snapshot", unexpected_acquisition)
    monkeypatch.setattr(
        "satn.sources.tempfile.mkdtemp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("temporary must not run")),
    )

    with pytest.raises(ValueError, match="safe snapshot basename"):
        snapshot(config)

    assert not root.exists()
    assert not (root.parent / "outside").exists()
    assert not acquired


@pytest.mark.parametrize(
    "snapshot_id", ["CON", "nul.json", "COM1.geojson", "LPT9.csv", "trailing."]
)
def test_snapshot_rejects_mutated_windows_unsafe_id_before_creating_root_or_acquiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, snapshot_id: str
) -> None:
    config = copied_config(tmp_path)
    root = config.source.snapshot_dir
    config.source.snapshot_id = snapshot_id
    acquired = False

    def unexpected_acquisition(*_args: object, **_kwargs: object) -> object:
        nonlocal acquired
        acquired = True
        raise AssertionError("snapshot acquisition must not run")

    monkeypatch.setattr("satn.sources._write_fixture_snapshot", unexpected_acquisition)
    monkeypatch.setattr(
        "satn.sources.tempfile.mkdtemp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("temporary must not run")),
    )

    with pytest.raises(ValueError, match="safe snapshot basename"):
        snapshot(config)

    assert not root.exists()
    assert not acquired


def test_snapshot_rejects_symlink_root_before_acquisition_or_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = copied_config(tmp_path)
    external_root = tmp_path / "external-root"
    external_root.mkdir()
    root_link = tmp_path / "snapshot-root-link"
    root_link.symlink_to(external_root, target_is_directory=True)
    config.source.snapshot_dir = root_link
    config.source.snapshot_id = "target"

    monkeypatch.setattr(
        "satn.sources._write_fixture_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("acquisition must not run")),
    )
    monkeypatch.setattr(
        "satn.sources.tempfile.mkdtemp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("temporary must not run")),
    )

    with pytest.raises(ValueError, match="snapshot root is missing or unsafe"):
        snapshot(config)

    assert not (external_root / "target").exists()
    assert not list(external_root.glob(".target-*"))


def test_snapshot_rejects_external_target_symlink_before_acquisition_or_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = copied_config(tmp_path)
    root = config.source.snapshot_dir
    root.mkdir(parents=True)
    external = tmp_path / "external-target"
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_text("do not touch", encoding="utf-8")
    target = root / "target"
    target.symlink_to(external, target_is_directory=True)
    config.source.snapshot_id = "target"

    monkeypatch.setattr(
        "satn.sources._write_fixture_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("acquisition must not run")),
    )
    monkeypatch.setattr(
        "satn.sources.tempfile.mkdtemp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("temporary must not run")),
    )

    with pytest.raises(ValueError, match="snapshot target is missing or unsafe"):
        snapshot(config)

    assert sentinel.read_text(encoding="utf-8") == "do not touch"
    assert sorted(path.name for path in external.iterdir()) == ["sentinel"]
    assert not list(root.glob(".target-*"))


def test_snapshot_rejects_existing_target_file_before_acquisition_or_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = copied_config(tmp_path)
    root = config.source.snapshot_dir
    root.mkdir(parents=True)
    target = root / "target"
    target.write_text("not a snapshot directory", encoding="utf-8")
    config.source.snapshot_id = "target"

    monkeypatch.setattr(
        "satn.sources._write_fixture_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("acquisition must not run")),
    )
    monkeypatch.setattr(
        "satn.sources.tempfile.mkdtemp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("temporary must not run")),
    )

    with pytest.raises(ValueError, match="snapshot target is missing or unsafe"):
        snapshot(config)

    assert target.read_text(encoding="utf-8") == "not a snapshot directory"
    assert not list(root.glob(".target-*"))


def test_weca_final_snapshot_is_distinct_and_benchmark_fixture_is_byte_pinned() -> None:
    benchmark_path = PROJECT / "deployments/weca/area-125-benchmark.yaml"
    benchmark = CouncilConfig.from_yaml(benchmark_path)
    bootstrap = CouncilConfig.from_yaml(PROJECT / "deployments/weca/area-bootstrap.yaml")
    final = CouncilConfig.from_yaml(PROJECT / "deployments/weca/area.yaml")

    assert hashlib.sha256(benchmark_path.read_bytes()).hexdigest() == WECA_BENCHMARK_SHA256
    assert benchmark.source.snapshot_id == bootstrap.source.snapshot_id == "weca-osm-current"
    assert final.source.snapshot_id == "weca-elevation-2026-07-25"
    assert final.source.snapshot_id != bootstrap.source.snapshot_id
    assert final.source.retained_core_source == RetainedCoreSourceConfig(
        snapshot_id="weca-osm-current",
        manifest_sha256="d4d8cbe37c13a6b9ae5d027693d64e89eab2edccf7b69afcdbec519883b1a988",
    )


def test_lineaged_retained_core_seeds_distinct_target_and_is_idempotent(tmp_path: Path) -> None:
    config = copied_config(tmp_path)
    historical, historical_manifest_sha256 = retained_core_source(config, "historical-core")
    historical_manifest_bytes = (historical / "snapshot.json").read_bytes()
    core_names = ("boundary.geojson", "places.geojson", "network.geojson", "context.geojson")
    core_sha256 = {
        name: hashlib.sha256((historical / name).read_bytes()).hexdigest() for name in core_names
    }
    target = configure_lineaged_target(
        config,
        source_id="historical-core",
        manifest_sha256=historical_manifest_sha256,
    )

    result = snapshot(config, retain_core=True)
    final_manifest_bytes = (result / "snapshot.json").read_bytes()
    final_manifest = json.loads(final_manifest_bytes)

    assert result == target
    assert result != historical
    assert (historical / "snapshot.json").read_bytes() == historical_manifest_bytes
    assert {
        name: hashlib.sha256((result / name).read_bytes()).hexdigest() for name in core_names
    } == core_sha256
    assert ELEVATION_EVIDENCE_FILENAME in final_manifest["files"]
    assert final_manifest["retained_core_lineage"] == {
        "source_snapshot_id": "historical-core",
        "source_manifest_sha256": historical_manifest_sha256,
    }

    assert snapshot(config, retain_core=True) == target
    assert (target / "snapshot.json").read_bytes() == final_manifest_bytes


def test_lineaged_target_rejects_self_resealed_retained_network(tmp_path: Path) -> None:
    config = copied_config(tmp_path)
    _historical, historical_manifest_sha256 = retained_core_source(config, "historical-core")
    target = configure_lineaged_target(
        config,
        source_id="historical-core",
        manifest_sha256=historical_manifest_sha256,
    )
    snapshot(config, retain_core=True)

    reseal_snapshot_member(target, "network.geojson", manifest_field="file_sha256")

    with pytest.raises(ValueError, match="changed retained file"):
        snapshot(config, retain_core=True)


def test_lineaged_target_rejects_self_resealed_retained_provenance(tmp_path: Path) -> None:
    config = copied_config(tmp_path)
    historical, _historical_manifest_sha256 = retained_core_source(config, "historical-core")
    provenance_name = "retained-core-provenance.json"
    provenance_path = historical / provenance_name
    provenance_path.write_text('{"source":"historical"}\n', encoding="utf-8")
    historical_manifest_path = historical / "snapshot.json"
    historical_manifest = json.loads(historical_manifest_path.read_text(encoding="utf-8"))
    historical_manifest["provenance_file_sha256"][provenance_name] = hashlib.sha256(
        provenance_path.read_bytes()
    ).hexdigest()
    historical_manifest_path.write_text(
        json.dumps(historical_manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    historical_manifest_sha256 = hashlib.sha256(historical_manifest_path.read_bytes()).hexdigest()
    target = configure_lineaged_target(
        config,
        source_id="historical-core",
        manifest_sha256=historical_manifest_sha256,
    )
    snapshot(config, retain_core=True)

    reseal_snapshot_member(
        target,
        provenance_name,
        manifest_field="provenance_file_sha256",
    )

    with pytest.raises(ValueError, match="changed retained provenance"):
        snapshot(config, retain_core=True)


def test_lineaged_retained_core_rejects_missing_or_mismatched_source_without_target(
    tmp_path: Path,
) -> None:
    config = copied_config(tmp_path)
    missing_target = configure_lineaged_target(
        config,
        source_id="missing-history",
        manifest_sha256="0" * 64,
    )

    with pytest.raises(ValueError, match="source snapshot is missing or unsafe"):
        snapshot(config, retain_core=True)
    assert not missing_target.exists()

    config.source.retained_core_source = None
    historical, historical_manifest_sha256 = retained_core_source(config, "historical-core")
    target = configure_lineaged_target(
        config,
        source_id="historical-core",
        manifest_sha256="0" * 64,
    )
    historical_manifest_bytes = (historical / "snapshot.json").read_bytes()

    with pytest.raises(ValueError, match="source manifest SHA-256 mismatch"):
        snapshot(config, retain_core=True)
    assert not target.exists()
    assert (historical / "snapshot.json").read_bytes() == historical_manifest_bytes
    assert historical_manifest_sha256 != "0" * 64


def test_lineaged_retained_core_rejects_source_symlink_before_copying(tmp_path: Path) -> None:
    config = copied_config(tmp_path)
    historical, historical_manifest_sha256 = retained_core_source(config, "historical-core")
    original = historical.with_name("historical-core-original")
    historical.rename(original)
    historical.symlink_to(original, target_is_directory=True)
    target = configure_lineaged_target(
        config,
        source_id="historical-core",
        manifest_sha256=historical_manifest_sha256,
    )

    with pytest.raises(ValueError, match="source snapshot is missing or unsafe"):
        snapshot(config, retain_core=True)
    assert not target.exists()


def test_lineaged_retained_core_rejects_symlinked_manifest_sibling_before_copying(
    tmp_path: Path,
) -> None:
    config = copied_config(tmp_path)
    historical, historical_manifest_sha256 = retained_core_source(config, "historical-core")
    network = historical / "network.geojson"
    retained_network = historical / "network-original.geojson"
    network.rename(retained_network)
    network.symlink_to(retained_network)
    target = configure_lineaged_target(
        config,
        source_id="historical-core",
        manifest_sha256=historical_manifest_sha256,
    )

    with pytest.raises(ValueError, match="regular non-symlink"):
        snapshot(config, retain_core=True)
    assert not target.exists()


def test_lineaged_retained_core_rejects_source_equal_to_target_even_after_assignment(
    tmp_path: Path,
) -> None:
    config = copied_config(tmp_path)
    config.source.snapshot_id = "same-snapshot"
    config.source.retained_core_source = RetainedCoreSourceConfig(
        snapshot_id="same-snapshot",
        manifest_sha256="0" * 64,
    )

    with pytest.raises(ValueError, match="source snapshot must differ from target snapshot"):
        snapshot(config, retain_core=True)


def test_lineaged_retained_core_rolls_back_missing_target_after_elevation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = copied_config(tmp_path)
    historical, historical_manifest_sha256 = retained_core_source(config, "historical-core")
    historical_manifest_bytes = (historical / "snapshot.json").read_bytes()
    target = configure_lineaged_target(
        config,
        source_id="historical-core",
        manifest_sha256=historical_manifest_sha256,
    )

    def fail_elevation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic elevation acquisition failure")

    monkeypatch.setattr("satn.sources._snapshot_national_elevation", fail_elevation)

    with pytest.raises(RuntimeError, match="synthetic elevation acquisition failure"):
        snapshot(config, retain_core=True)

    assert not target.exists()
    assert not list(target.parent.glob(f".{target.name}-*"))
    assert (historical / "snapshot.json").read_bytes() == historical_manifest_bytes
