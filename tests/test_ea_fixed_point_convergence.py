from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from satn.ea_elevation import (
    FIXED_POINT_PRIMARY_FIELD,
    eligible_route_fingerprint,
    eligible_route_samples,
    fixed_point_route_fingerprint,
)
from satn.ea_fixed_point_convergence import (
    EAFixedPointAcquisition,
    EAFixedPointCompilation,
    EAFixedPointSnapshot,
)
from satn.ea_fixed_point_operations import EAFixedPointProductionOperations
from satn.models import AreaDefinition
from satn.publisher import (
    WECA_PINNED_ELIGIBLE_ROUTE_BBOX,
    WECA_SURVEY_REQUEST_BBOX,
    _ea_fixed_point_candidate_path,
    _ea_fixed_point_next_step,
)

SCRIPT = Path(__file__).parents[1] / "scripts" / "acquire_ea_elevation.py"
SPEC = importlib.util.spec_from_file_location("acquire_ea_elevation_convergence", SCRIPT)
assert SPEC and SPEC.loader
acquisition = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acquisition)


def _production_acquisition_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    configured_evidence_name: str = "elevation.geojson",
) -> tuple[
    EAFixedPointProductionOperations,
    EAFixedPointSnapshot,
    EAFixedPointCompilation,
    Path,
    Path,
    gpd.GeoDataFrame,
]:
    config = AreaDefinition.from_yaml(
        Path(__file__).parents[1] / "deployments" / "weca" / "area.yaml"
    )
    config.publication.output_dir = tmp_path / "compiled" / "weca"
    config.source.snapshot_dir = tmp_path / "snapshots"
    assert config.source.national_elevation is not None
    configured_evidence = tmp_path / "elevation" / configured_evidence_name
    config.source.national_elevation.path = configured_evidence
    configured_evidence.parent.mkdir(parents=True)
    configured_evidence.write_text("sealed-governed-evidence", encoding="utf-8")

    candidate = _ea_fixed_point_candidate_path(config) / "network.geojson"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(b"candidate-network")
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    authority = replay_dir / "ea-authority-boundaries.geojson"
    survey = replay_dir / "ea-survey-index.geojson"
    supplemental = replay_dir / "ea-elevation-sampled-routes.geojson"
    for path in (authority, survey, supplemental):
        path.write_text(path.name, encoding="utf-8")
    monkeypatch.setattr(
        "satn.ea_fixed_point_operations._validated_ea_snapshot_replay_inputs",
        lambda _snapshot: {
            "authority_boundaries": authority,
            "survey_index": survey,
            "sample_routes": supplemental,
        },
    )
    monkeypatch.setattr("satn.ea_fixed_point_operations._PROJECT_ROOT", tmp_path)

    routes = gpd.GeoDataFrame(
        [
            {
                "feature_id": "primary-spine",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile-primary",
                "geometry": LineString([(350000, 150000), (350010, 150000)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    actual_fingerprint = eligible_route_fingerprint(routes)
    snapshot = EAFixedPointSnapshot(
        snapshot_id=config.source.snapshot_id,
        manifest_sha256="a" * 64,
        primary_fingerprint="b" * 64,
        retained_sample_routes=supplemental,
        route_inventory=("route-a",),
        governed_source_identities=(("network.geojson", "c" * 64),),
    )
    compilation = EAFixedPointCompilation(
        expected_fingerprint=snapshot.primary_fingerprint,
        actual_fingerprint=actual_fingerprint,
        candidate_network=candidate,
        urban_access_ms=0,
        topography_ms=0,
        acquisition_command=(
            "uv",
            "run",
            "python",
            "scripts/acquire_ea_elevation.py",
            str(candidate),
            str(configured_evidence),
            "--cache-dir",
            str(configured_evidence.parent / "ea-dtm-cache"),
            "--spacing-m",
            "10",
            "--authority-boundaries",
            str(authority),
            "--survey-index",
            str(survey),
            "--weca-preflight",
            "--routing-buffer-m",
            "15000",
            "--governed-input-fingerprint",
            "e" * 64,
            "--supplemental-routes",
            str(supplemental),
        ),
    )
    return (
        EAFixedPointProductionOperations(config, run_token="test"),
        snapshot,
        compilation,
        configured_evidence,
        supplemental,
        routes,
    )


def _write_completed_acquisition_output(
    evidence_path: Path,
    routes: gpd.GeoDataFrame,
    *,
    governed_input_fingerprint: str,
) -> None:
    sampled_routes = evidence_path.with_name(f"{evidence_path.stem}.sampled-routes.geojson")
    ledger = evidence_path.with_name(f"{evidence_path.stem}.sample-ledger.jsonl")
    manifest = evidence_path.with_suffix(".manifest.json")
    evidence_path.write_text('{"type":"FeatureCollection","features":[]}\n', encoding="utf-8")
    routes.to_file(sampled_routes, driver="GeoJSON")
    ledger.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "acquisition_protocol": "two-pass-fixed-point/v1",
                "governed_input_fingerprint": governed_input_fingerprint,
                "output_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                "pre_elevation_network_sha256": eligible_route_fingerprint(routes),
                "sample_ledger_path": ledger.name,
                "sample_ledger_sha256": hashlib.sha256(ledger.read_bytes()).hexdigest(),
                "sample_route_path": sampled_routes.name,
                "sample_route_sha256": hashlib.sha256(sampled_routes.read_bytes()).hexdigest(),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_acquisition_refuses_an_eligible_route_collapsed_to_one_point(
    tmp_path: Path,
) -> None:
    route_path = tmp_path / "candidate.geojson"
    output_path = tmp_path / "elevation.geojson"
    feature_id = "spine-access-07d8d07fe59d"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": feature_id,
                "feature_type": "spine-access-connection",
                "topography_profile_id": "topography-profile-dc8152b42c505885",
                "geometry": LineString(
                    [
                        (369092.3832793793, 169040.53825675382),
                        (369092.3832793793, 169040.53825675382),
                    ]
                ),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(route_path, driver="GeoJSON")

    with pytest.raises(
        ValueError,
        match=(
            rf"{feature_id}.*collapses at identity precision.*"
            "regenerate the candidate network"
        ),
    ):
        acquisition.write_evidence(
            route_path,
            output_path,
            tmp_path / "cache",
            workers=1,
        )

    assert not output_path.with_name("elevation.sampled-routes.geojson").exists()


def test_current_weca_startup_reports_the_invalid_retained_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = AreaDefinition.from_yaml(
        Path(__file__).parents[1] / "deployments" / "weca" / "area.yaml"
    )
    feature_id = "spine-access-07d8d07fe59d"
    sampled_routes = tmp_path / "ea-elevation-sampled-routes.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": feature_id,
                "feature_type": "spine-access-connection",
                "topography_profile_id": "topography-profile-dc8152b42c505885",
                "geometry": LineString(
                    [
                        (369092.3832793793, 169040.53825675382),
                        (369092.3832793793, 169040.53825675382),
                    ]
                ),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(sampled_routes, driver="GeoJSON")
    snapshot_dir = tmp_path / config.source.snapshot_id
    snapshot_dir.mkdir()
    (snapshot_dir / "snapshot.json").write_text(
        json.dumps(
            {
                "evidence_sources": {"elevation": {"pre_elevation_network_sha256": "a" * 64}},
                "provenance_file_sha256": {"network.geojson": "b" * 64},
            }
        ),
        encoding="utf-8",
    )
    config.source.snapshot_dir = tmp_path
    monkeypatch.setattr(
        "satn.ea_fixed_point_operations._validated_ea_snapshot_replay_inputs",
        lambda _snapshot_dir: {"sample_routes": sampled_routes},
    )

    with pytest.raises(
        ValueError,
        match=rf"{feature_id}.*collapses at identity precision",
    ):
        EAFixedPointProductionOperations(config, run_token="test").initial_snapshot()


def test_malformed_acquisition_command_is_refused_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AreaDefinition.from_yaml(
        Path(__file__).parents[1] / "deployments" / "weca" / "area.yaml"
    )
    operations = EAFixedPointProductionOperations(config, run_token="test")
    snapshot = EAFixedPointSnapshot(
        snapshot_id=config.source.snapshot_id,
        manifest_sha256="a" * 64,
        primary_fingerprint="b" * 64,
        retained_sample_routes=Path("sample-routes.geojson"),
        route_inventory=("route-a",),
        governed_source_identities=(("network.geojson", "c" * 64),),
    )
    compilation = EAFixedPointCompilation(
        expected_fingerprint="b" * 64,
        actual_fingerprint="d" * 64,
        candidate_network=Path("candidate.geojson"),
        urban_access_ms=0,
        topography_ms=0,
        acquisition_command=("/usr/bin/false",),
    )
    executed = False

    def unexpected_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal executed
        executed = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("satn.ea_fixed_point_operations.subprocess.run", unexpected_run)

    with pytest.raises(ValueError, match="command is malformed"):
        operations.acquire(snapshot, compilation)

    assert not executed


def test_acquisition_path_escape_is_refused_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = AreaDefinition.from_yaml(
        Path(__file__).parents[1] / "deployments" / "weca" / "area.yaml"
    )
    operations = EAFixedPointProductionOperations(config, run_token="test")
    snapshot = EAFixedPointSnapshot(
        snapshot_id=config.source.snapshot_id,
        manifest_sha256="a" * 64,
        primary_fingerprint="b" * 64,
        retained_sample_routes=Path("sample-routes.geojson"),
        route_inventory=("route-a",),
        governed_source_identities=(("network.geojson", "c" * 64),),
    )
    outside_candidate = tmp_path / "candidate.geojson"
    outside_evidence = tmp_path / "elevation.geojson"
    compilation = EAFixedPointCompilation(
        expected_fingerprint="b" * 64,
        actual_fingerprint="d" * 64,
        candidate_network=outside_candidate,
        urban_access_ms=0,
        topography_ms=0,
        acquisition_command=(
            "uv",
            "run",
            "python",
            "scripts/acquire_ea_elevation.py",
            str(outside_candidate),
            str(outside_evidence),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--spacing-m",
            "10",
            "--authority-boundaries",
            str(tmp_path / "authority-boundaries.geojson"),
            "--survey-index",
            str(tmp_path / "survey-index.geojson"),
            "--weca-preflight",
            "--routing-buffer-m",
            "15000",
            "--governed-input-fingerprint",
            "e" * 64,
            "--supplemental-routes",
            str(tmp_path / "sample-routes.geojson"),
        ),
    )
    executed = False

    def unexpected_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal executed
        executed = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("satn.ea_fixed_point_operations.subprocess.run", unexpected_run)

    with pytest.raises(ValueError, match="path escapes"):
        operations.acquire(snapshot, compilation)

    assert not executed


def test_acquisition_writes_a_hash_bound_sibling_without_touching_governed_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        operations,
        snapshot,
        compilation,
        configured_evidence,
        supplemental,
        routes,
    ) = _production_acquisition_case(tmp_path, monkeypatch)
    commands: list[tuple[str, ...]] = []

    def run(
        command: tuple[str, ...],
        **_kwargs: object,
    ) -> SimpleNamespace:
        commands.append(command)
        _write_completed_acquisition_output(
            Path(command[5]),
            routes,
            governed_input_fingerprint=command[18],
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("satn.ea_fixed_point_operations.subprocess.run", run)

    result = operations.acquire(snapshot, compilation)

    expected = configured_evidence.with_name(
        "elevation.fixed-point-"
        "8cd099f39e067bdee532480bc0e117e7a34e3c29b072a7c936f10fb01f7826ff"
        ".geojson"
    )
    assert commands[0][5] == str(expected)
    assert commands[0][7] == str(configured_evidence.parent / "ea-dtm-cache")
    assert commands[0][20] == str(supplemental)
    assert configured_evidence.read_text(encoding="utf-8") == "sealed-governed-evidence"
    assert result.evidence_path == expected


def test_acquisition_normalizes_prior_fixed_point_suffixes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_stem = "ea-lidar-dtm-1m-weca-recovery-v14"
    configured_name = f"{base_stem}.fixed-point-{'a' * 64}.fixed-point-{'b' * 64}.geojson"
    (
        operations,
        snapshot,
        compilation,
        configured_evidence,
        _supplemental,
        routes,
    ) = _production_acquisition_case(
        tmp_path,
        monkeypatch,
        configured_evidence_name=configured_name,
    )

    commands: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        _write_completed_acquisition_output(
            Path(command[5]),
            routes,
            governed_input_fingerprint=command[18],
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("satn.ea_fixed_point_operations.subprocess.run", run)

    result = operations.acquire(snapshot, compilation)

    expected = configured_evidence.with_name(
        f"{base_stem}.fixed-point-"
        "8cd099f39e067bdee532480bc0e117e7a34e3c29b072a7c936f10fb01f7826ff"
        ".geojson"
    )
    assert commands[0][5] == str(expected)
    assert result.evidence_path == expected
    assert expected.stem.count(".fixed-point-") == 1
    assert max(
        len(path.name.encode())
        for path in (
            expected,
            expected.with_suffix(".manifest.json"),
            expected.with_name(f"{expected.stem}.sample-ledger.jsonl"),
            expected.with_name(f"{expected.stem}.sampled-routes.geojson"),
        )
    ) <= os.pathconf(expected.parent, "PC_NAME_MAX")


def test_acquisition_bounds_a_near_component_limit_evidence_stem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component_limit = os.pathconf(tmp_path, "PC_NAME_MAX")
    base_stem = "e" * (component_limit - len(".geojson") - 1)
    (
        operations,
        snapshot,
        compilation,
        configured_evidence,
        _supplemental,
        routes,
    ) = _production_acquisition_case(
        tmp_path,
        monkeypatch,
        configured_evidence_name=f"{base_stem}.geojson",
    )

    commands: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        _write_completed_acquisition_output(
            Path(command[5]),
            routes,
            governed_input_fingerprint=command[18],
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("satn.ea_fixed_point_operations.subprocess.run", run)

    result = operations.acquire(snapshot, compilation)

    identity = "8cd099f39e067bdee532480bc0e117e7a34e3c29b072a7c936f10fb01f7826ff"
    expected_tail = f".fixed-point-{identity}"
    family_tail_bytes = max(
        len(f"{expected_tail}{suffix}".encode())
        for suffix in (
            ".geojson",
            ".manifest.json",
            ".sample-ledger.jsonl",
            ".sampled-routes.geojson",
        )
    )
    available_stem_bytes = component_limit - family_tail_bytes
    expected_stem = base_stem.encode()[:available_stem_bytes].decode()
    expected = configured_evidence.with_name(f"{expected_stem}{expected_tail}.geojson")
    assert commands[0][5] == str(expected)
    assert result.evidence_path == expected
    assert all(
        len(path.name.encode()) <= component_limit
        for path in (
            expected,
            expected.with_suffix(".manifest.json"),
            expected.with_name(f"{expected.stem}.sample-ledger.jsonl"),
            expected.with_name(f"{expected.stem}.sampled-routes.geojson"),
        )
    )


def test_acquisition_identity_separates_distinct_governed_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        operations,
        snapshot,
        compilation,
        _configured_evidence,
        _supplemental,
        routes,
    ) = _production_acquisition_case(tmp_path, monkeypatch)
    commands: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        _write_completed_acquisition_output(
            Path(command[5]),
            routes,
            governed_input_fingerprint=command[18],
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("satn.ea_fixed_point_operations.subprocess.run", run)
    operations.acquire(snapshot, compilation)
    changed_command = (
        *compilation.acquisition_command[:18],
        "f" * 64,
        *compilation.acquisition_command[19:],
    )
    changed = EAFixedPointCompilation(
        expected_fingerprint=compilation.expected_fingerprint,
        actual_fingerprint=compilation.actual_fingerprint,
        candidate_network=compilation.candidate_network,
        urban_access_ms=0,
        topography_ms=0,
        acquisition_command=changed_command,
    )

    operations.acquire(snapshot, changed)

    assert len(commands) == 2
    assert commands[0][5] != commands[1][5]
    assert Path(commands[0][5]).stem.endswith(
        "8cd099f39e067bdee532480bc0e117e7a34e3c29b072a7c936f10fb01f7826ff"
    )
    assert Path(commands[1][5]).stem.endswith(
        "841939112684e7d908cf327f77f665fda8520fc45db1d258c2917edf7f3d54d7"
    )
    for command in commands:
        output = Path(command[5])
        family = (
            output.name,
            output.with_suffix(".manifest.json").name,
            output.with_name(f"{output.stem}.sample-ledger.jsonl").name,
            output.with_name(f"{output.stem}.sampled-routes.geojson").name,
        )
        assert max(len(name.encode()) for name in family) <= 255


def test_sequential_acquisitions_derive_from_one_stable_evidence_basename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        operations,
        snapshot,
        compilation,
        configured_evidence,
        _supplemental,
        routes,
    ) = _production_acquisition_case(tmp_path, monkeypatch)
    commands: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        _write_completed_acquisition_output(
            Path(command[5]),
            routes,
            governed_input_fingerprint=command[18],
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("satn.ea_fixed_point_operations.subprocess.run", run)
    first = operations.acquire(snapshot, compilation)
    next_snapshot = EAFixedPointSnapshot(
        snapshot_id=f"{snapshot.snapshot_id}-fp-test-01",
        manifest_sha256="d" * 64,
        primary_fingerprint=first.primary_fingerprint,
        retained_sample_routes=first.evidence_path.with_name(
            f"{first.evidence_path.stem}.sampled-routes.geojson"
        ),
        route_inventory=first.route_inventory,
        governed_source_identities=(("network.geojson", "c" * 64),),
        parent_snapshot_id=snapshot.snapshot_id,
        parent_manifest_sha256=snapshot.manifest_sha256,
        elevation_evidence_path=first.evidence_path,
    )
    monkeypatch.setattr(
        "satn.ea_fixed_point_operations.create_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "satn.ea_fixed_point_operations._snapshot_state",
        lambda _config, *, expected_parent: next_snapshot,
    )
    sealed = operations.snapshot(snapshot, first, 1).snapshot
    second_command = list(compilation.acquisition_command)
    second_command[5] = str(first.evidence_path)
    second_command[18] = "f" * 64
    second_compilation = EAFixedPointCompilation(
        expected_fingerprint=sealed.primary_fingerprint,
        actual_fingerprint=compilation.actual_fingerprint,
        candidate_network=compilation.candidate_network,
        urban_access_ms=0,
        topography_ms=0,
        acquisition_command=tuple(second_command),
    )

    second = operations.acquire(sealed, second_compilation)

    assert first.evidence_path != second.evidence_path
    assert first.evidence_path.parent == second.evidence_path.parent
    assert all(
        path.name.startswith(f"{configured_evidence.stem}.fixed-point-")
        and path.stem.count(".fixed-point-") == 1
        for path in (first.evidence_path, second.evidence_path)
    )
    for output in (first.evidence_path, second.evidence_path):
        family = (
            output,
            output.with_suffix(".manifest.json"),
            output.with_name(f"{output.stem}.sample-ledger.jsonl"),
            output.with_name(f"{output.stem}.sampled-routes.geojson"),
        )
        assert max(len(path.name.encode()) for path in family) <= 255
    assert len(commands) == 2


def test_acquisition_refuses_a_partial_immutable_output_family_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        operations,
        snapshot,
        compilation,
        configured_evidence,
        _supplemental,
        _routes,
    ) = _production_acquisition_case(tmp_path, monkeypatch)
    expected = configured_evidence.with_name(
        "elevation.fixed-point-"
        "8cd099f39e067bdee532480bc0e117e7a34e3c29b072a7c936f10fb01f7826ff"
        ".geojson"
    )
    expected.write_text("interrupted acquisition", encoding="utf-8")
    executed = False

    def unexpected_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal executed
        executed = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        "satn.ea_fixed_point_operations.subprocess.run",
        unexpected_run,
    )

    with pytest.raises(ValueError, match="output family is incomplete"):
        operations.acquire(snapshot, compilation)

    assert not executed
    assert expected.read_text(encoding="utf-8") == "interrupted acquisition"


def test_acquisition_resume_reuses_a_complete_immutable_output_family(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        operations,
        snapshot,
        compilation,
        configured_evidence,
        _supplemental,
        routes,
    ) = _production_acquisition_case(tmp_path, monkeypatch)
    expected = configured_evidence.with_name(
        "elevation.fixed-point-"
        "8cd099f39e067bdee532480bc0e117e7a34e3c29b072a7c936f10fb01f7826ff"
        ".geojson"
    )
    _write_completed_acquisition_output(
        expected,
        routes,
        governed_input_fingerprint="e" * 64,
    )
    executed = False

    def unexpected_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal executed
        executed = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        "satn.ea_fixed_point_operations.subprocess.run",
        unexpected_run,
    )

    result = operations.acquire(snapshot, compilation)

    assert not executed
    assert result.primary_fingerprint == compilation.actual_fingerprint
    assert result.evidence_path == expected


def test_acquisition_resume_refuses_output_with_a_different_governed_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        operations,
        snapshot,
        compilation,
        configured_evidence,
        _supplemental,
        routes,
    ) = _production_acquisition_case(tmp_path, monkeypatch)
    expected = configured_evidence.with_name(
        "elevation.fixed-point-"
        "8cd099f39e067bdee532480bc0e117e7a34e3c29b072a7c936f10fb01f7826ff"
        ".geojson"
    )
    _write_completed_acquisition_output(
        expected,
        routes,
        governed_input_fingerprint="f" * 64,
    )
    executed = False

    def unexpected_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal executed
        executed = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        "satn.ea_fixed_point_operations.subprocess.run",
        unexpected_run,
    )

    with pytest.raises(ValueError, match="output identity is invalid"):
        operations.acquire(snapshot, compilation)

    assert not executed


def test_snapshot_retry_validates_an_already_sealed_target_without_overwriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AreaDefinition.from_yaml(
        Path(__file__).parents[1] / "deployments" / "weca" / "area.yaml"
    )
    config.source.snapshot_dir = tmp_path
    operations = EAFixedPointProductionOperations(config, run_token="test")
    previous = EAFixedPointSnapshot(
        snapshot_id=config.source.snapshot_id,
        manifest_sha256="a" * 64,
        primary_fingerprint="b" * 64,
        retained_sample_routes=Path("sample-routes.geojson"),
        route_inventory=("route-a",),
        governed_source_identities=(("network.geojson", "c" * 64),),
    )
    acquisition = EAFixedPointAcquisition(
        primary_fingerprint="d" * 64,
        route_inventory=("route-a", "route-b"),
        evidence_path=tmp_path / "elevation.geojson",
    )
    target_id = f"{config.source.snapshot_id}-fp-test-01"
    (tmp_path / target_id).mkdir()
    expected = EAFixedPointSnapshot(
        snapshot_id=target_id,
        manifest_sha256="e" * 64,
        primary_fingerprint=acquisition.primary_fingerprint,
        retained_sample_routes=Path("retained-routes.geojson"),
        route_inventory=acquisition.route_inventory,
        governed_source_identities=(("network.geojson", "f" * 64),),
        parent_snapshot_id=previous.snapshot_id,
        parent_manifest_sha256=previous.manifest_sha256,
    )
    sealed = False

    def unexpected_seal(*_args: object, **_kwargs: object) -> None:
        nonlocal sealed
        sealed = True

    monkeypatch.setattr(
        "satn.ea_fixed_point_operations.create_snapshot",
        unexpected_seal,
    )
    monkeypatch.setattr(
        "satn.ea_fixed_point_operations._snapshot_state",
        lambda _config, *, expected_parent: expected,
    )

    creation = operations.snapshot(previous, acquisition, 1)

    assert not sealed
    assert creation.snapshot == expected
    assert creation.snapshot_seal_ms == 0


def test_retained_supplemental_routes_contain_only_elevation_eligible_features(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary.geojson"
    supplemental = tmp_path / "supplemental.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "primary-spine",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile-primary",
                "geometry": LineString([(350000, 150000), (350010, 150000)]),
            },
            {
                "feature_id": "irrelevant-gradient",
                "feature_type": "gradient-section",
                "topography_profile_id": "profile-primary",
                "geometry": LineString([(350000, 150000), (350005, 150000)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(primary, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "supplemental-access",
                "feature_type": "spine-access-connection",
                "topography_profile_id": "profile-supplemental",
                "geometry": LineString([(350010, 150000), (350010, 150010)]),
            },
            {
                "feature_id": "irrelevant-area",
                "feature_type": "low-traffic-area",
                "topography_profile_id": "profile-area",
                "geometry": LineString([(350020, 150000), (350020, 150010)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(supplemental, driver="GeoJSON")

    retained = acquisition._combined_sample_routes(primary, [supplemental])

    assert retained["feature_type"].tolist() == [
        "strategic-spine",
        "spine-access-connection",
    ]
    assert retained[FIXED_POINT_PRIMARY_FIELD].tolist() == [True, False]
    assert fixed_point_route_fingerprint(retained) == eligible_route_fingerprint(
        gpd.read_file(primary)
    )
    samples, _feature_ids = eligible_route_samples(retained, spacing_m=10)
    assert {(sample["geometry"].x, sample["geometry"].y) for sample in samples} == {
        (350000, 150000),
        (350010, 150000),
        (350010, 150010),
    }


def test_acquisition_retains_only_normalized_elevation_routes_without_supplements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.geojson"
    output = tmp_path / "elevation.geojson"
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "primary-spine",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile-primary",
                "geometry": LineString([(350000, 150000), (350010, 150000)]),
            },
            {
                "feature_id": "irrelevant-gradient",
                "feature_type": "gradient-section",
                "topography_profile_id": "profile-primary",
                "geometry": LineString([(350000, 150000), (350005, 150000)]),
            },
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(routes, driver="GeoJSON")
    monkeypatch.setattr(
        acquisition,
        "acquire_tile",
        lambda key, *_args, **_kwargs: (
            key,
            tmp_path / "synthetic.tif",
            "synthetic-url",
            "a" * 64,
            1,
            None,
        ),
    )
    monkeypatch.setattr(acquisition, "load_tile", lambda _path: object())
    monkeypatch.setattr(acquisition, "sample_grid", lambda _grid, _point: 42.0)

    manifest = acquisition.write_evidence(
        routes,
        output,
        tmp_path / "cache",
        spacing_m=10,
        workers=1,
    )

    retained_path = output.with_name("elevation.sampled-routes.geojson")
    retained = gpd.read_file(retained_path)
    assert retained["feature_type"].tolist() == ["strategic-spine"]
    assert manifest["pre_elevation_network_sha256"] == eligible_route_fingerprint(
        gpd.read_file(routes)
    )
    assert fixed_point_route_fingerprint(retained) == manifest["pre_elevation_network_sha256"]


def test_acquisition_writes_strict_sample_routes_for_inferred_school_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = tmp_path / "routes.geojson"
    output = tmp_path / "elevation.geojson"
    routes.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {
                    "type": "name",
                    "properties": {"name": "urn:ogc:def:crs:EPSG::27700"},
                },
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "feature_id": "inferred-school-access",
                            "feature_type": "spine-access-connection",
                            "topography_profile_id": "profile-inferred-school",
                            "access_point_source_id": None,
                            "provenance": (
                                '{"access_point_source_id": NaN, "access_point_status": "inferred"}'
                            ),
                        },
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[350000, 150000], [350010, 150000]],
                        },
                    }
                ],
            },
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        acquisition,
        "acquire_tile",
        lambda key, *_args, **_kwargs: (
            key,
            tmp_path / "synthetic.tif",
            "synthetic-url",
            "a" * 64,
            1,
            None,
        ),
    )
    monkeypatch.setattr(acquisition, "load_tile", lambda _path: object())
    monkeypatch.setattr(acquisition, "sample_grid", lambda _grid, _point: 42.0)

    acquisition.write_evidence(
        routes,
        output,
        tmp_path / "cache",
        spacing_m=10,
        workers=1,
    )

    retained_path = output.with_name("elevation.sampled-routes.geojson")

    def reject_nonstandard_constant(value: str) -> object:
        raise ValueError(f"nonstandard JSON constant {value!r}")

    retained = json.loads(
        retained_path.read_text(encoding="utf-8"),
        parse_constant=reject_nonstandard_constant,
    )
    provenance = retained["features"][0]["properties"]["provenance"]
    if isinstance(provenance, str):
        provenance = json.loads(
            provenance,
            parse_constant=reject_nonstandard_constant,
        )
    assert provenance["access_point_source_id"] is None
    assert provenance["access_point_status"] == "inferred"


def test_supplemental_routes_support_three_set_convergence(tmp_path: Path) -> None:
    route_paths = {name: tmp_path / f"{name}.geojson" for name in ("first", "second", "third")}
    accumulated_path = tmp_path / "accumulated.geojson"
    for offset, (name, path) in enumerate(route_paths.items()):
        northing = 150000 + offset * 10
        gpd.GeoDataFrame(
            [
                {
                    "feature_id": name,
                    "feature_type": "strategic-spine",
                    "topography_profile_id": f"profile-{name}",
                    "geometry": LineString([(350000, northing), (350010, northing)]),
                }
            ],
            geometry="geometry",
            crs=27700,
        ).to_file(path, driver="GeoJSON")

    def selected_route(sampled_routes: gpd.GeoDataFrame) -> str:
        covered = {
            round(float(geometry.centroid.y)) for geometry in sampled_routes.to_crs(27700).geometry
        }
        return "second" if 150010 not in covered else "third"

    accumulated = acquisition._combined_sample_routes(route_paths["first"], [])
    expected_actual = []
    for iteration, primary_name in enumerate(("first", "second", "third"), start=1):
        primary = route_paths[primary_name]
        if iteration > 1:
            accumulated.to_file(accumulated_path, driver="GeoJSON")
            accumulated = acquisition._combined_sample_routes(primary, [accumulated_path])
        actual_name = selected_route(accumulated)
        expected_actual.append((primary_name, actual_name))
        if actual_name == primary_name:
            break

    assert expected_actual == [
        ("first", "second"),
        ("second", "third"),
        ("third", "third"),
    ]
    assert accumulated[FIXED_POINT_PRIMARY_FIELD].tolist() == [True, False, False]
    assert fixed_point_route_fingerprint(accumulated) == eligible_route_fingerprint(
        gpd.read_file(route_paths["third"])
    )


def test_fixed_point_next_step_replays_validated_accumulated_sample_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    authority_boundaries = snapshot / "ea-authority-boundaries.geojson"
    survey_index = snapshot / "ea-survey-index.geojson"
    accumulated_routes = snapshot / "ea-elevation-sampled-routes.geojson"
    candidate = tmp_path / "candidate.geojson"
    validation = tmp_path / "validation.geojson"
    elevation = tmp_path / "elevation.geojson"
    config = SimpleNamespace(
        source=SimpleNamespace(
            national_elevation=SimpleNamespace(path=elevation),
            snapshot_dir=tmp_path,
            snapshot_id=snapshot.name,
        )
    )
    west, south, east, north = WECA_PINNED_ELIGIBLE_ROUTE_BBOX
    routes = gpd.GeoDataFrame(
        [{"geometry": LineString([(west, south), (east, north)])}],
        geometry="geometry",
        crs=27700,
    )
    monkeypatch.setattr("satn.publisher.gpd.read_file", lambda _path: routes)
    monkeypatch.setattr(
        "satn.publisher.eligible_route_samples",
        lambda _routes, spacing_m: (
            [
                {"geometry": Point(west, south)},
                {"geometry": Point(east, north)},
            ],
            ["candidate"],
        ),
    )
    monkeypatch.setattr(
        "satn.publisher.governed_survey_request_bbox",
        lambda _routes, routing_buffer_m: tuple(int(value) for value in WECA_SURVEY_REQUEST_BBOX),
    )
    monkeypatch.setattr(
        "satn.publisher._validated_ea_snapshot_replay_inputs",
        lambda _snapshot: {
            "authority_boundaries": authority_boundaries,
            "survey_index": survey_index,
            "sample_routes": accumulated_routes,
        },
    )

    result = _ea_fixed_point_next_step(
        config,
        candidate,
        validation_network=validation,
        governed_input_fingerprint="a" * 64,
    )
    command = shlex.split(result["next_step_command"])

    assert command[command.index("--supplemental-routes") + 1] == str(accumulated_routes)


def test_fixed_point_next_step_validates_primary_and_supplemental_route_union(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    authority_boundaries = snapshot / "ea-authority-boundaries.geojson"
    survey_index = snapshot / "ea-survey-index.geojson"
    accumulated_routes = snapshot / "ea-elevation-sampled-routes.geojson"
    candidate = tmp_path / "candidate.geojson"
    elevation = tmp_path / "elevation.geojson"
    west, south, east, north = WECA_PINNED_ELIGIBLE_ROUTE_BBOX
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "new-primary",
                "feature_type": "strategic-spine",
                "topography_profile_id": "new-primary-profile",
                "geometry": LineString(
                    [(west + 10_000, south + 10_000), (east - 10_000, north - 10_000)]
                ),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(candidate, driver="GeoJSON")
    gpd.GeoDataFrame(
        [
            {
                "feature_id": "retained-supplemental",
                "feature_type": "strategic-spine",
                "topography_profile_id": "retained-profile",
                "geometry": LineString([(west, south), (east, north)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    ).to_file(accumulated_routes, driver="GeoJSON")
    monkeypatch.setattr(
        "satn.publisher._validated_ea_snapshot_replay_inputs",
        lambda _snapshot: {
            "authority_boundaries": authority_boundaries,
            "survey_index": survey_index,
            "sample_routes": accumulated_routes,
        },
    )
    config = SimpleNamespace(
        source=SimpleNamespace(
            national_elevation=SimpleNamespace(path=elevation),
            snapshot_dir=tmp_path,
            snapshot_id=snapshot.name,
        )
    )

    result = _ea_fixed_point_next_step(
        config,
        candidate,
        validation_network=candidate,
        governed_input_fingerprint="a" * 64,
    )

    assert result["next_step_status"] == "ea-acquisition-ready"
