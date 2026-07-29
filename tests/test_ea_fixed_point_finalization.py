from __future__ import annotations

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import pytest
import yaml
from shapely.geometry import LineString

import satn.ea_fixed_point_operations as fixed_point_operations
from satn.compilation_dependencies import compilation_dependency_manifest
from satn.ea_elevation import SAMPLE_LEDGER_FILENAME, fixed_point_route_fingerprint
from satn.ea_fixed_point_operations import run_ea_fixed_point_convergence
from satn.models import AreaDefinition, RetainedCoreSourceConfig
from satn.pipeline import compilation_governed_input_fingerprint
from satn.sources import EA_RETAINED_ROUTE_FILENAME, ELEVATION_EVIDENCE_FILENAME


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _terminal_case(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    tmp_path = tmp_path / "project"
    tmp_path.mkdir()
    source = Path(__file__).parents[1] / "deployments" / "weca" / "area.yaml"
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    snapshots = tmp_path / "snapshots"
    parent_id = "parent-snapshot"
    target_id = "target-snapshot"
    evidence = tmp_path / "elevation" / "target.geojson"
    evidence.parent.mkdir()
    evidence.write_text('{"type":"FeatureCollection","features":[]}\n')
    document["source"]["snapshot_dir"] = str(snapshots)
    document["source"]["snapshot_id"] = parent_id
    document["source"]["retained_core_source"] = {
        "snapshot_id": "grandparent-snapshot",
        "manifest_sha256": "9" * 64,
    }
    document["source"]["national_elevation"]["path"] = str(
        tmp_path / "elevation" / "parent.geojson"
    )
    document["publication"]["output_dir"] = str(tmp_path / "compiled")
    config_path = tmp_path / "area.yaml"
    config_path.write_text(yaml.safe_dump(document, sort_keys=False))

    candidate = tmp_path / "compiled" / "network.geojson"
    candidate.parent.mkdir()
    routes = gpd.GeoDataFrame(
        [
            {
                "feature_id": "route-1",
                "feature_type": "strategic-spine",
                "topography_profile_id": "profile-1",
                "geometry": LineString([(350000, 150000), (350010, 150000)]),
            }
        ],
        geometry="geometry",
        crs=27700,
    )
    routes.to_file(candidate, driver="GeoJSON")
    fingerprint = fixed_point_route_fingerprint(routes)
    governed = "8" * 64
    run_manifest = candidate.parent / "run.json"
    run_manifest.write_text(
        json.dumps(
            {
                "governed_input_fingerprint": governed,
                "snapshot_manifest_sha256": "",
            }
        )
    )

    target = snapshots / target_id
    target.mkdir(parents=True)
    (target / "network.geojson").write_bytes(candidate.read_bytes())
    (target / ELEVATION_EVIDENCE_FILENAME).write_bytes(evidence.read_bytes())
    routes.to_file(target / EA_RETAINED_ROUTE_FILENAME, driver="GeoJSON")
    (target / SAMPLE_LEDGER_FILENAME).write_text("{}\n")
    empty_geojson = json.dumps(
        {"type": "FeatureCollection", "features": []},
        sort_keys=True,
    )
    for name in ("ea-authority-boundaries.geojson", "ea-survey-index.geojson"):
        (target / name).write_text(empty_geojson)
    replay_names = {
        "authority_boundaries": "ea-authority-boundaries.geojson",
        "sample_ledger": SAMPLE_LEDGER_FILENAME,
        "sample_routes": EA_RETAINED_ROUTE_FILENAME,
        "survey_index": "ea-survey-index.geojson",
    }
    member_names = [
        "network.geojson",
        ELEVATION_EVIDENCE_FILENAME,
        *replay_names.values(),
    ]
    member_hashes = {name: _sha256(target / name) for name in member_names}
    acquisition = {
        "licence": "Open Government Licence v3.0",
        "authority_boundaries_path": replay_names["authority_boundaries"],
        "sample_ledger_path": replay_names["sample_ledger"],
        "sample_route_path": replay_names["sample_routes"],
        "survey_index_path": replay_names["survey_index"],
        "sample_ledger_sha256": member_hashes[replay_names["sample_ledger"]],
        "sample_route_sha256": member_hashes[replay_names["sample_routes"]],
        "survey_index_sha256": member_hashes[replay_names["survey_index"]],
        "survey_coverage_preflight": {
            "authority_boundaries": {
                "raw_sha256": member_hashes[replay_names["authority_boundaries"]]
            }
        },
    }
    acquisition_path = target / "elevation-evidence.manifest.json"
    acquisition_path.write_text(json.dumps(acquisition, sort_keys=True))
    provenance_hashes = {
        **member_hashes,
        acquisition_path.name: _sha256(acquisition_path),
    }
    snapshot_manifest = target / "snapshot.json"
    snapshot_manifest.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "snapshot_id": target_id,
                "files": member_names,
                "file_sha256": member_hashes,
                "evidence_sources": {
                    "elevation": {
                        "pre_elevation_network_sha256": fingerprint,
                        "acquisition_output_sha256": _sha256(evidence),
                        "content_fingerprint": member_hashes[
                            ELEVATION_EVIDENCE_FILENAME
                        ],
                        "ea_acquisition_manifest_sha256": provenance_hashes[
                            acquisition_path.name
                        ],
                        "replay_inputs": {
                            "schema_version": "ea-fixed-point-replay-inputs/v1",
                            "licence": acquisition["licence"],
                            "files": {
                                role: {
                                    "path": name,
                                    "sha256": member_hashes[name],
                                }
                                for role, name in replay_names.items()
                            },
                        },
                    }
                },
                "provenance_file_sha256": provenance_hashes,
                "retained_core_lineage": {
                    "source_snapshot_id": parent_id,
                    "source_manifest_sha256": "6" * 64,
                },
            },
            sort_keys=True,
        )
    )
    run = json.loads(run_manifest.read_text())
    run["snapshot_manifest_sha256"] = _sha256(snapshot_manifest)
    run_manifest.write_text(json.dumps(run, sort_keys=True))

    record_path = tmp_path / "convergence.json"
    terminal = {
        "schema_version": "ea-fixed-point-convergence/v2",
        "status": "converged",
        "run_token": "test-run",
        "configuration_identity": _sha256(config_path),
        "max_iterations": 3,
        "final_snapshot": {
            "snapshot_id": target_id,
            "manifest_sha256": _sha256(snapshot_manifest),
            "primary_fingerprint": fingerprint,
            "retained_sample_routes": str(target / "sampled.geojson"),
            "elevation_evidence_path": str(evidence),
            "route_inventory": ["route-1"],
            "governed_source_identities": {"network.geojson": "7" * 64},
            "parent_snapshot_id": parent_id,
            "parent_manifest_sha256": "6" * 64,
        },
        "terminal_artifacts": {
            "candidate_network": str(candidate),
            "candidate_network_sha256": _sha256(candidate),
            "run_manifest": str(run_manifest),
            "run_manifest_sha256": _sha256(run_manifest),
        },
        "iterations": [
            {
                "iteration": 1,
                "snapshot_id": target_id,
                "snapshot_manifest_sha256": _sha256(snapshot_manifest),
                "expected_fingerprint": fingerprint,
                "actual_fingerprint": fingerprint,
                "route_inventory": ["route-1"],
                "governed_source_identities": {"network.geojson": "7" * 64},
                "timings_ms": {
                    "acquisition": 0,
                    "snapshot_seal": 0,
                    "snapshot_validation": 0,
                    "urban_access": 0,
                    "topography": 0,
                },
            }
        ],
    }
    record_path.write_text(json.dumps(terminal, sort_keys=True))
    return config_path, record_path, candidate, governed


def _patch_record(record_path: Path, **updates: object) -> None:
    record = json.loads(record_path.read_text())
    record.update(updates)
    record_path.write_text(json.dumps(record, sort_keys=True))


def test_terminal_resume_finalizes_without_compiling_and_replays_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, record_path, _candidate, governed = _terminal_case(tmp_path)
    monkeypatch.setattr(fixed_point_operations, "_PROJECT_ROOT", config_path.parent)
    monkeypatch.setattr(
        "satn.ea_fixed_point_operations.compilation_dependency_manifest",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "satn.ea_fixed_point_operations.compilation_governed_input_fingerprint",
        lambda *_args, **_kwargs: governed,
    )
    monkeypatch.setattr(
        "satn.ea_fixed_point_operations.EAFixedPointProductionOperations",
        lambda *_args, **_kwargs: pytest.fail("terminal resume compiled"),
    )
    replace = fixed_point_operations.atomic_replace_recovery_configuration
    monkeypatch.setattr(
        fixed_point_operations,
        "atomic_replace_recovery_configuration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated crash after proof")
        ),
    )

    with pytest.raises(RuntimeError, match="simulated crash after proof"):
        run_ea_fixed_point_convergence(
            config_path,
            max_iterations=3,
            record_path=record_path,
            resume=True,
        )
    assert record_path.with_name("convergence.closure.json").is_file()
    assert yaml.safe_load(config_path.read_text())["source"]["snapshot_id"] == (
        "parent-snapshot"
    )

    monkeypatch.setattr(
        fixed_point_operations,
        "atomic_replace_recovery_configuration",
        replace,
    )
    result = run_ea_fixed_point_convergence(
        config_path,
        max_iterations=3,
        record_path=record_path,
        resume=True,
    )
    promoted = yaml.safe_load(config_path.read_text())
    closure = json.loads(
        record_path.with_name("convergence.closure.json").read_text()
    )
    assert result.status == "converged"
    assert promoted["source"]["snapshot_id"] == "target-snapshot"
    assert promoted["source"]["retained_core_source"]["snapshot_id"] == (
        "parent-snapshot"
    )
    assert promoted["source"]["national_elevation"]["path"] == (
        "elevation/target.geojson"
    )
    assert closure["fixed_point_closure"]["governed_input_fingerprint"] == governed

    replay = run_ea_fixed_point_convergence(
        config_path,
        max_iterations=3,
        record_path=record_path,
        resume=True,
    )
    assert replay == result


def test_terminal_resume_refuses_tampered_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, record_path, candidate, governed = _terminal_case(tmp_path)
    monkeypatch.setattr(fixed_point_operations, "_PROJECT_ROOT", config_path.parent)
    candidate.write_text("tampered")
    monkeypatch.setattr(
        "satn.ea_fixed_point_operations.compilation_dependency_manifest",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "satn.ea_fixed_point_operations.compilation_governed_input_fingerprint",
        lambda *_args, **_kwargs: governed,
    )

    with pytest.raises(ValueError, match="candidate network SHA-256 differs"):
        run_ea_fixed_point_convergence(
            config_path,
            max_iterations=3,
            record_path=record_path,
            resume=True,
        )


def test_terminal_resume_refuses_tampered_elevation_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, record_path, _candidate, governed = _terminal_case(tmp_path)
    monkeypatch.setattr(fixed_point_operations, "_PROJECT_ROOT", config_path.parent)
    record = json.loads(record_path.read_text())
    Path(record["final_snapshot"]["elevation_evidence_path"]).write_text("tampered")
    monkeypatch.setattr(
        fixed_point_operations,
        "compilation_dependency_manifest",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        fixed_point_operations,
        "compilation_governed_input_fingerprint",
        lambda *_args, **_kwargs: governed,
    )

    with pytest.raises(ValueError, match="elevation evidence identity differs"):
        run_ea_fixed_point_convergence(
            config_path,
            max_iterations=3,
            record_path=record_path,
            resume=True,
        )


@pytest.mark.parametrize(
    "member",
    ["network.geojson", "elevation-evidence.manifest.json"],
)
def test_terminal_resume_revalidates_every_snapshot_member_without_compiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    member: str,
) -> None:
    config_path, record_path, _candidate, governed = _terminal_case(tmp_path)
    monkeypatch.setattr(fixed_point_operations, "_PROJECT_ROOT", config_path.parent)
    record = json.loads(record_path.read_text())
    snapshot_id = record["final_snapshot"]["snapshot_id"]
    (config_path.parent / "snapshots" / snapshot_id / member).write_text("tampered")
    monkeypatch.setattr(
        fixed_point_operations,
        "compilation_dependency_manifest",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        fixed_point_operations,
        "compilation_governed_input_fingerprint",
        lambda *_args, **_kwargs: governed,
    )
    monkeypatch.setattr(
        fixed_point_operations,
        "EAFixedPointProductionOperations",
        lambda *_args, **_kwargs: pytest.fail("terminal resume compiled"),
    )

    with pytest.raises(ValueError, match=r"tampered|hash mismatch|unbound"):
        run_ea_fixed_point_convergence(
            config_path,
            max_iterations=3,
            record_path=record_path,
            resume=True,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "ea-fixed-point-convergence/v1"),
        ("status", "in-progress"),
        ("configuration_identity", "not-a-sha"),
        ("max_iterations", "3"),
    ],
)
def test_terminal_resume_refuses_invalid_terminal_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    config_path, record_path, _candidate, _governed = _terminal_case(tmp_path)
    monkeypatch.setattr(fixed_point_operations, "_PROJECT_ROOT", config_path.parent)
    _patch_record(record_path, **{field: value})

    with pytest.raises(ValueError, match="terminal record is invalid"):
        run_ea_fixed_point_convergence(
            config_path,
            max_iterations=3,
            record_path=record_path,
            resume=True,
        )


@pytest.mark.parametrize("mutation", ["gap", "mismatch"])
def test_terminal_resume_refuses_nonterminal_iteration_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    config_path, record_path, _candidate, _governed = _terminal_case(tmp_path)
    monkeypatch.setattr(fixed_point_operations, "_PROJECT_ROOT", config_path.parent)
    record = json.loads(record_path.read_text())
    if mutation == "gap":
        record["iterations"][0]["iteration"] = 2
    else:
        record["iterations"][0]["actual_fingerprint"] = "5" * 64
    record_path.write_text(json.dumps(record, sort_keys=True))

    with pytest.raises(ValueError, match="terminal record is invalid"):
        run_ea_fixed_point_convergence(
            config_path,
            max_iterations=3,
            record_path=record_path,
            resume=True,
        )


@pytest.mark.parametrize("unsafe", ["config", "record", "closure"])
def test_terminal_resume_refuses_symlink_control_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    config_path, record_path, _candidate, governed = _terminal_case(tmp_path)
    monkeypatch.setattr(fixed_point_operations, "_PROJECT_ROOT", config_path.parent)
    monkeypatch.setattr(
        fixed_point_operations,
        "compilation_dependency_manifest",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        fixed_point_operations,
        "compilation_governed_input_fingerprint",
        lambda *_args, **_kwargs: governed,
    )
    target = {
        "config": config_path,
        "record": record_path,
        "closure": record_path.with_name("convergence.closure.json"),
    }[unsafe]
    real = target.with_name(f"{target.name}.real")
    if target.exists():
        target.rename(real)
    else:
        real.write_text("{}")
    target.symlink_to(real)

    with pytest.raises(ValueError, match="missing or unsafe"):
        run_ea_fixed_point_convergence(
            config_path,
            max_iterations=3,
            record_path=record_path,
            resume=True,
        )


@pytest.mark.parametrize(
    "unsafe",
    [
        "snapshot-id",
        "parent-lineage",
        "snapshot-symlink",
        "candidate",
        "run",
        "evidence",
    ],
)
def test_terminal_resume_refuses_paths_outside_their_governed_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    config_path, record_path, candidate, governed = _terminal_case(tmp_path)
    monkeypatch.setattr(fixed_point_operations, "_PROJECT_ROOT", config_path.parent)
    monkeypatch.setattr(
        fixed_point_operations,
        "compilation_dependency_manifest",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        fixed_point_operations,
        "compilation_governed_input_fingerprint",
        lambda *_args, **_kwargs: governed,
    )
    record = json.loads(record_path.read_text())
    outside = tmp_path / "outside"
    outside.mkdir()
    if unsafe == "snapshot-id":
        record["final_snapshot"]["snapshot_id"] = "../outside"
        record["iterations"][0]["snapshot_id"] = "../outside"
    elif unsafe == "parent-lineage":
        record["final_snapshot"]["parent_snapshot_id"] = "different-parent"
    elif unsafe == "snapshot-symlink":
        target = config_path.parent / "snapshots" / "target-snapshot"
        moved = outside / "target-snapshot"
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=True)
    elif unsafe == "candidate":
        moved = outside / "network.geojson"
        moved.write_bytes(candidate.read_bytes())
        record["terminal_artifacts"]["candidate_network"] = str(moved)
        record["terminal_artifacts"]["candidate_network_sha256"] = _sha256(moved)
    elif unsafe == "run":
        moved = outside / "run.json"
        source = Path(record["terminal_artifacts"]["run_manifest"])
        moved.write_bytes(source.read_bytes())
        record["terminal_artifacts"]["run_manifest"] = str(moved)
        record["terminal_artifacts"]["run_manifest_sha256"] = _sha256(moved)
    else:
        moved = outside / "evidence.geojson"
        moved.write_text("outside")
        record["final_snapshot"]["elevation_evidence_path"] = str(moved)
    record_path.write_text(json.dumps(record, sort_keys=True))

    with pytest.raises(
        ValueError,
        match=r"invalid|differs|outside|exact|safe snapshot basename|candidate sibling",
    ):
        run_ea_fixed_point_convergence(
            config_path,
            max_iterations=3,
            record_path=record_path,
            resume=True,
        )


def test_terminal_finalizer_uses_real_governed_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path, record_path, _candidate, _placeholder = _terminal_case(tmp_path)
    monkeypatch.setattr(fixed_point_operations, "_PROJECT_ROOT", config_path.parent)
    record = json.loads(record_path.read_text())
    snapshot = record["final_snapshot"]
    config = AreaDefinition.from_yaml(config_path)
    config.source.snapshot_id = snapshot["snapshot_id"]
    config.source.retained_core_source = RetainedCoreSourceConfig(
        snapshot_id=snapshot["parent_snapshot_id"],
        manifest_sha256=snapshot["parent_manifest_sha256"],
    )
    assert config.source.national_elevation is not None
    config.source.national_elevation.path = Path(snapshot["elevation_evidence_path"])
    dependencies = compilation_dependency_manifest(config, compiler_path="network")
    governed = compilation_governed_input_fingerprint(
        config, dependency_manifest=dependencies
    )
    run_path = Path(record["terminal_artifacts"]["run_manifest"])
    run = json.loads(run_path.read_text())
    run["governed_input_fingerprint"] = governed
    run_path.write_text(json.dumps(run, sort_keys=True))
    record["terminal_artifacts"]["run_manifest_sha256"] = _sha256(run_path)
    record_path.write_text(json.dumps(record, sort_keys=True))

    result = run_ea_fixed_point_convergence(
        config_path,
        max_iterations=3,
        record_path=record_path,
        resume=True,
    )

    assert result.status == "converged"
